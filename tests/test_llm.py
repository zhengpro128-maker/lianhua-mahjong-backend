"""LLM 后端接入单元测试 —— 文档 §13（无网络：mock 供应商调用）。

覆盖：规范/解析、候选与特征、合法性校验、LLMPlayer 三路径（成功/非法/异常回退、
胡短路、skipDraw 约束）、房间装配（llmEnabled/能力探测）、局况元数据。
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.game.llm_player import LLMPlayer
from app.game.player import ClaimContext, TurnContext
from app.llm.candidates import build_request
from app.llm.client import extract_json_object, parse_llm_output
from app.llm.validation import validate_action
from app.rules.registry import get_rule_set
from app.settlement import settlement_service


# ── 基础上下文工厂 ───────────────────────────────────────────

def run(coro):
    """在独立事件循环中执行协程（不依赖 pytest-asyncio 的全局循环状态）。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def turn_ctx(hand=None, melds=None, exposed_melds=0, skip_draw=False, **overrides):
    data = dict(
        hand=hand or ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'm7', 'm8', 'm9',
                      'p1', 'p2', 'p3', 'p4', 'white'],
        melds=melds or [],
        exposedMelds=exposed_melds,
        kongBloom=False,
        skipDraw=skip_draw,
        afterKong=False,
        visibleTiles=[],
        publicTiles=[],
        wallCount=60,
        scores=[1000, 1000, 1000, 1000],
        peers=[{'discards': [], 'melds': []} for _ in range(4)],
        seatWind='东', roundWind='东', dealerIndex=0, roundIndex=1,
        requestId='turn-0-1', stateVersion='1:drawing:60:0:0:13',
        **overrides,
    )
    return TurnContext(**data)


def claim_ctx(hand=None, tile='m3', can_peng=True, can_gang=False, chi_options=None,
              can_hu=False, **overrides):
    data = dict(
        hand=hand or ['m3', 'm3', 'm5', 'm6', 'm7', 'm8', 'p2', 'p3', 'p4',
                      's1', 's2', 's3', 'east'],
        canGang=can_gang,
        canHu=can_hu,
        canPeng=can_peng,
        chiOptions=chi_options or [],
        tile=tile,
        from_=1,
        exposedMelds=0,
        visibleTiles=[],
        publicTiles=[],
        wallCount=60,
        scores=[1000, 1000, 1000, 1000],
        peers=[{'discards': [], 'melds': []} for _ in range(4)],
        seatWind='东', roundWind='东', dealerIndex=0, roundIndex=1,
        requestId='claim-1-2', stateVersion='1:checking:60:0:0:13',
        **overrides,
    )
    return ClaimContext(**data)


# ── 解析器 ───────────────────────────────────────────────────

class TestParsing:
    def test_balanced_brace_scanner_skips_strings(self):
        text = '好的 {"choice":"A1","message":"大{括}号"} 完毕'
        assert extract_json_object(text) == '{"choice":"A1","message":"大{括}号"}'

    def test_parse_valid_and_whitelist(self):
        assert parse_llm_output('{"choice":"A1","message":"哈哈哈"}', ['A1', 'A2']) == ('A1', '哈哈哈')
        with pytest.raises(Exception):
            parse_llm_output('{"choice":"A9"}', ['A1', 'A2'])


# ── 候选枚举 ─────────────────────────────────────────────────

class TestCandidates:
    def test_turn_discard_dedupe(self):
        ctx = turn_ctx(hand=['m3', 'm3', 'm5'])
        built = build_request(ctx, get_rule_set(), 'r1', 'v1', 'turn')
        labels = [c['label'] for c in built['request']['candidates'] if c['action']['kind'] == 'discard']
        assert labels == ['出3万', '出5万']
        assert built['engineSuggestion'] is not None

    def test_skip_draw_only_discard(self):
        ctx = turn_ctx(hand=['m3', 'm3', 'm3', 'm3'], melds=[], exposed_melds=0, skip_draw=True)
        built = build_request(ctx, get_rule_set(), 'r1', 'v1', 'turn')
        assert all(c['action']['kind'] == 'discard' for c in built['request']['candidates'])

    def test_claim_candidates_pass_gang_peng(self):
        ctx = claim_ctx(tile='m3', can_peng=True, can_gang=True)
        built = build_request(ctx, get_rule_set(), 'r1', 'v1', 'claim')
        assert [c['id'] for c in built['request']['candidates']] == ['Z', 'G', 'P']

    def test_lotus_wind_kong(self):
        rules = get_rule_set('lotus-legacy')
        rules.round_state.joker_tiles = []
        ctx = turn_ctx(hand=['east', 'south', 'west', 'north', 'm3', 'm3', 'm5'],
                       exposed_melds=0)
        built = build_request(ctx, rules, 'r1', 'v1', 'turn')
        kinds = [c['action']['kind'] for c in built['request']['candidates']]
        assert 'wind-kong' in kinds

    def test_claim_chi_options(self):
        rules = get_rule_set('lotus-legacy')
        rules.round_state.joker_tiles = []
        ctx = claim_ctx(hand=['m4', 'm5', 'east'], tile='m3', can_peng=False,
                        chi_options=[{'tiles': ['m3', 'm4', 'm5'], 'kind': 'sequence'}])
        built = build_request(ctx, rules, 'r1', 'v1', 'claim')
        ids = [c['id'] for c in built['request']['candidates']]
        assert 'C1' in ids


# ── 合法性校验（§8.2）──────────────────────────────────────

class TestValidation:
    def test_discard_out_of_range(self):
        ctx = turn_ctx(hand=['m1', 'm2'])
        rules = get_rule_set()
        assert validate_action(ctx, {'kind': 'discard', 'handIndex': 0}, rules)
        assert not validate_action(ctx, {'kind': 'discard', 'handIndex': 2}, rules)

    def test_win_rejected(self):
        ctx = turn_ctx()
        assert not validate_action(ctx, {'kind': 'win'}, get_rule_set())

    def test_chi_out_of_range(self):
        ctx = claim_ctx(chi_options=[{'tiles': ['m3', 'm4', 'm5'], 'kind': 'sequence'}])
        rules = get_rule_set('lotus-legacy')
        rules.round_state.joker_tiles = []
        assert validate_action(ctx, {'kind': 'chi', 'optionIndex': 0}, rules)
        assert not validate_action(ctx, {'kind': 'chi', 'optionIndex': 1}, rules)


# ── LLMPlayer（mock 供应商）──────────────────────────────────

class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status
        self._payload = None

    def json(self):
        if self._payload is None:
            self._payload = {'choices': [{'message': {'content': self.text},
                                          'finish_reason': 'stop'}]}
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def post(self, url, headers=None, json=None, timeout=None):
        self.calls += 1
        item = self._responses.pop(0) if self._responses else self._responses[-1]
        if callable(item):
            return item()
        return item


def make_llm_player(monkeypatch, responses, **cfg_overrides):
    cfg = dict(
        enabled=True, base_url='https://api.deepseek.com/v1', api_key='sk-x',
        model='deepseek-v4-flash', style='稳健', timeout_s=8.0,
        pool_timeout_s=1.0, concurrency=2, max_requests_per_room=0,
    )
    cfg.update(cfg_overrides)
    config = SimpleNamespace(**cfg)
    fake = FakeClient(responses)
    # LLMPlayer 直接使用 request_llm_decision —— mock 掉它（透传到假客户端）
    async def fake_decision(cfg_, system, user, candidate_ids):
        item = fake._responses.pop(0) if fake._responses else fake._responses[-1]
        if callable(item):
            item = item()
        if isinstance(item, Exception):
            raise item
        return parse_llm_output(item, candidate_ids)
    monkeypatch.setattr('app.game.llm_player.request_llm_decision', fake_decision)
    player = LLMPlayer(delays={'turn': 0, 'after_kong': 0, 'claim': 0}, config=config)
    return player, fake


class TestLLMPlayer:
    def test_turn_win_short_circuit_no_llm_call(self, monkeypatch):
        player, fake = make_llm_player(monkeypatch, [])
        # 白板癞子 + 4 面子 + 一对 → 胡
        ctx = turn_ctx(hand=['m1', 'm1', 'm1', 'm2', 'm2', 'm2', 'm3', 'm3', 'm3',
                             's1', 's1', 's1', 's2', 's2'])
        action = run(player.request_turn(ctx))
        assert action['kind'] == 'win'
        assert fake.calls == 0

    def test_turn_legal_choice_executes(self, monkeypatch):
        player, fake = make_llm_player(
            monkeypatch, ['{"choice":"A1","message":"稳一手。"}'])
        ctx = turn_ctx(hand=['m3', 'm3', 'm5', 'm6', 'p1', 'p2', 'p3', 's1', 's2', 's3',
                             'east', 'west', 'white'])
        action = run(player.request_turn(ctx))
        assert action['kind'] == 'discard'
        assert action['handIndex'] >= 0
        assert player.stats['successes'] == 1
        assert player.stats['messages'] == 1

    def test_turn_illegal_choice_falls_back(self, monkeypatch):
        # 返回白名单外的 choice → 解析失败 → 回退启发式（kind=discard 或杠）
        player, fake = make_llm_player(monkeypatch, [])
        ctx = turn_ctx(hand=['m3', 'm5', 'm6'])
        action = run(player.request_turn(ctx))
        assert action['kind'] == 'discard'
        assert player.stats['fallbacks'] >= 1

    def test_network_error_falls_back(self, monkeypatch):
        player, fake = make_llm_player(monkeypatch, [Exception('boom')])
        ctx = turn_ctx(hand=['m3', 'm5', 'm6'])
        action = run(player.request_turn(ctx))
        assert action['kind'] == 'discard'
        assert player.stats['fallbacks'] >= 1

    def test_claim_hu_short_circuit(self, monkeypatch):
        player, fake = make_llm_player(monkeypatch, [])
        ctx = claim_ctx(tile='m1', can_peng=True, can_hu=True)
        action = run(player.request_claim(ctx))
        assert action['kind'] == 'win'
        assert fake.calls == 0

    def test_claim_chi_mapping(self, monkeypatch):
        player, fake = make_llm_player(
            monkeypatch, ['{"choice":"C1","message":"吃！"}'])
        rules = get_rule_set('lotus-legacy')
        player.set_rule_set(rules)
        rules.round_state.joker_tiles = []
        ctx = claim_ctx(hand=['m4', 'm5', 'east'], tile='m3', can_peng=False,
                        chi_options=[{'tiles': ['m3', 'm4', 'm5'], 'kind': 'sequence'}])
        action = run(player.request_claim(ctx))
        assert action == {'kind': 'chi', 'optionIndex': 0}


# ── 房间装配（§9.3）───────────────────────────────────────────

class TestRoomAssembly:
    def test_llm_enabled_uses_llm_player(self, monkeypatch):
        from app.game.room import RoomSession
        monkeypatch.setattr('app.game.room.llm_server_available', lambda: True)
        room = RoomSession('LLMROOM', mode='east', capacity=2, llm_enabled=True)
        assert room.llm_available is True
        assert room.effective_llm_enabled is True
        # 四人桌固定 4 座；无真人占座时全部为 AI 补位
        controllers = room._controllers()
        assert sum(1 for c in controllers if isinstance(c, LLMPlayer)) == 4

    def test_llm_disabled_keeps_heuristic(self, monkeypatch):
        from app.game.player import AIPlayer
        from app.game.room import RoomSession
        monkeypatch.setattr('app.game.room.llm_server_available', lambda: True)
        room = RoomSession('HEURISTIC', mode='east', capacity=2, llm_enabled=False)
        assert room.effective_llm_enabled is False
        controllers = room._controllers()
        assert sum(1 for c in controllers if isinstance(c, AIPlayer)) == 4

    def test_server_unavailable_silently_degrades(self, monkeypatch):
        from app.game.player import AIPlayer
        from app.game.room import RoomSession
        monkeypatch.setattr('app.game.room.llm_server_available', lambda: False)
        room = RoomSession('OFF', mode='east', capacity=2, llm_enabled=True)
        assert room.llm_available is False
        assert room.effective_llm_enabled is False
        controllers = room._controllers()
        assert sum(1 for c in controllers if isinstance(c, AIPlayer)) == 4


# ── 局况元数据（§6.2/§6.4）───────────────────────────────────

class TestMeta:
    def test_llm_meta_fields(self):
        from app.game.manager import GameManager
        manager = GameManager()
        manager.players = [SimpleNamespace(score=1000, discards=[], melds=[],
                                           hand=['m1']) for _ in range(4)]
        manager.dealer = 1
        manager.round = 2
        manager.phase = 'drawing'
        manager._head_drawn = 4
        manager.wall = ['m2']
        meta = manager._llm_meta(2, 'turn')
        assert meta['seatWind'] == '南'   # 庄家 1 座 → 座位 2 为南
        assert meta['roundWind'] == '东'
        assert meta['dealerIndex'] == 1
        assert meta['roundIndex'] == 2
        assert meta['requestId'].startswith('turn-2-')
        assert 'drawing' in meta['stateVersion']
        assert meta['scores'] == [1000, 1000, 1000, 1000]
        assert len(meta['peers']) == 4
