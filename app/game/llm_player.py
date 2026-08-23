"""LLM 玩家控制器 —— §2/§3/§8（联机空座补位）。

LLMPlayer(AIPlayer)：覆盖 request_turn / request_claim；
- 胡由引擎短路（v1 不放给 LLM）：turn 判 is_winning_hand，claim 判 ctx.canHu；
- skipDraw 只允许出牌（候选枚举不含杠/胡）；
- LLM 决定 → validate_action（§8.2，逐类型）→ 失败/超时/网络/HTTP → super().request_*（启发式兜底）；
- request_rob_kong 继承 AIPlayer（能抢必抢）。
"""

import asyncio
from typing import Callable, Optional

from app.game.player import AIPlayer, ClaimContext, TurnContext
from app.llm.candidates import build_request
from app.llm.client import request_llm_decision
from app.llm.config import LlmServerConfig, load_llm_config
from app.llm.prompt import build_prompt
from app.llm.validation import validate_action
from app.rules.base import GameRuleSet


IMPORTANT_SPEECH_ACTIONS = {
    'gang', 'peng', 'chi', 'added-kong', 'concealed-kong', 'wind-kong',
}

class LLMPlayer(AIPlayer):
    """LLM 驱动的 AI 玩家（联机空座补位用；超时/断线代打仍用 AIPlayer）。"""

    def __init__(self, delays: Optional[dict] = None, random=None,
                 rule_set: Optional[GameRuleSet] = None,
                 config: Optional[LlmServerConfig] = None,
                 stats: Optional[dict] = None,
                 seat: int = -1,
                 provider_id: str = '',
                 on_message: Optional[Callable[[int, str, str], None]] = None):
        super().__init__(delays=delays, random=random, rule_set=rule_set)
        self.config = config or load_llm_config()
        self.stats = stats if stats is not None else {
            'requests': 0, 'successes': 0, 'fallbacks': 0, 'messages': 0, 'invalid': 0,
        }
        self.requests = 0  # 本座位自建以来请求数（房间预算近似：每座位独立计数）
        self.seat = seat
        self.provider_id = provider_id
        self.on_message = on_message
        self.message_history: list[str] = []

    async def request_turn(self, ctx: TurnContext) -> dict:
        ms = self.delays['after_kong'] if ctx.afterKong else self.delays['turn']
        if ms:
            await asyncio.sleep(ms / 1000)
        rules = self.rules
        # v1 胡短路：引擎判定，不放给 LLM（§3）
        if not ctx.skipDraw and rules.is_winning_hand(ctx.hand, ctx.exposedMelds):
            return {'kind': 'win'}
        built = build_request(ctx, rules, getattr(ctx, 'requestId', '') or '',
                              getattr(ctx, 'stateVersion', '') or '', 'turn')
        action = await self._decide(built, ctx)
        if action is None:
            return await super().request_turn(ctx)
        return action

    async def request_claim(self, ctx: ClaimContext) -> dict:
        ms = self.delays['claim']
        if ms:
            await asyncio.sleep(ms / 1000)
        # 点炮胡引擎短路（§3）：canHu → win（manager 依据 canHu 执行胡）
        if ctx.canHu:
            return {'kind': 'win'}
        chi_options = list(getattr(ctx, 'chiOptions', None) or getattr(ctx, 'chi_options', None) or [])
        if not ctx.canGang and not ctx.canPeng and not chi_options:
            return {'kind': 'pass'}
        built = build_request(ctx, self.rules, getattr(ctx, 'requestId', '') or '',
                              getattr(ctx, 'stateVersion', '') or '', 'claim')
        action = await self._decide(built, ctx)
        if action is None:
            return await super().request_claim(ctx)
        return action

    async def _decide(self, built: Optional[dict], ctx) -> Optional[dict]:
        """LLM 决定 → 候选动作；失败/非法 → None（调用方回退启发式）。"""
        if built is None or len(built['request']['candidates']) <= 1:
            return built['fallbackAction'] if built else None
        # 房间预算：超出后直接回退启发式（不阻塞游戏循环）
        if self.config.max_requests_per_room > 0 \
                and self.requests >= self.config.max_requests_per_room:
            return None
        self.requests += 1
        request = built['request']
        ids = [candidate['id'] for candidate in request['candidates']]
        system, user = build_prompt(self.config.style, request)
        self.stats['requests'] += 1
        try:
            choice, message = await request_llm_decision(self.config, system, user, ids)
        except Exception:
            self.stats['fallbacks'] += 1
            return None
        candidate = next((item for item in request['candidates'] if item['id'] == choice), None)
        if candidate is None:
            self.stats['fallbacks'] += 1
            return None
        # §8.2 自校验：对照请求时刻的 ctx 复核（引擎执行层还会再复核一次）
        if not validate_action(ctx, candidate['action'], self.rules):
            self.stats['invalid'] += 1
            self.stats['fallbacks'] += 1
            return None
        self.stats['successes'] += 1
        if message:
            self.stats['messages'] += 1
            self.message_history.append(message)
            if self.on_message is not None:
                try:
                    action_kind = candidate['action']['kind']
                    priority = 'important' if action_kind in IMPORTANT_SPEECH_ACTIONS else 'normal'
                    self.on_message(self.seat, message, priority)
                except Exception:
                    # 吐槽属于表现副作用；广播失败不能影响动作执行和对局推进。
                    pass
        return self._map_action(candidate['action'])

    def _map_action(self, action: dict) -> dict:
        kind = action['kind']
        if kind == 'win':
            return {'kind': 'win'}
        if kind == 'added-kong':
            return {'kind': 'added-kong', 'meldIndex': action['meldIndex']}
        if kind == 'concealed-kong':
            return {'kind': 'concealed-kong', 'tile': action['tile']}
        if kind == 'wind-kong':
            return {'kind': 'wind-kong'}
        if kind == 'discard':
            return {'kind': 'discard', 'handIndex': action['handIndex']}
        if kind == 'gang':
            return {'kind': 'gang'}
        if kind == 'peng':
            # §4.3：两步决策，不带 discardIndex
            return {'kind': 'peng'}
        if kind == 'chi':
            return {'kind': 'chi', 'optionIndex': action['optionIndex']}
        return {'kind': 'pass'}
