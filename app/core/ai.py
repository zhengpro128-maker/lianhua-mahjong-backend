"""AI 决策层 —— 从 src/game/ai.ts 翻译

纯决策：只负责「看状态 → 给出动作命令」，不修改任何游戏状态、
不触发表现副作用。动作的「执行」由 actions.py / GameManager 完成。

优先级链（与原 playAI 一致）：自摸胡 → 补杠 → 暗杠 → 弃牌。
"""

import random as _random
import re
from typing import Optional

from app.core.actions import remove_matches
from app.models.game import GamePlayer, Meld, TileType
from app.rules.base import GameRuleSet
from app.rules.lianhua import get_default_rule_set

# ─── 决策类型（对应 TS 端联合类型，用 dict 表示）────────────────

TurnDecision = dict
ClaimDecision = str
RobKongDecision = str

_SUITED_RE = re.compile(r'^([mps])([1-9])$')


def decide_turn(view: dict, rule_set: Optional[GameRuleSet] = None) -> dict:
    """决策当前 AI 回合的动作。

    view: {hand, melds, exposedMelds, kongBloom}（见 make_turn_view）
    优先级：自摸胡 → 补杠 → 暗杠 → 弃牌。杠前评估是否破坏听牌。
    """
    rules = rule_set or get_default_rule_set()
    if rules.is_winning_hand(view['hand'], view['exposedMelds']):
        return {'kind': 'win'}

    if rules.code == 'lotus-legacy':
        # 莲花麻将：杠决策评估（破坏听牌/被抢杠风险）+ 听口质量弃牌（对齐前端 lotusAi）。
        from app.core.lotus_ai import decide_turn as lotus_decide_turn
        return lotus_decide_turn(view, view.get('jokers', []), rules)

    meld_index = -1
    for i, meld in enumerate(view['melds']):
        if meld.type == 'peng' and rules.can_added_kong(view['hand'], view['melds'], meld.tile):
            meld_index = i
            break
    # 补杠：已听牌时放弃（避免破坏手牌结构 + 被抢杠风险）
    if meld_index >= 0 and not _is_tenpai(view['hand'], view['exposedMelds'], rules):
        return {'kind': 'added-kong', 'meldIndex': meld_index}

    kongs = rules.concealed_kongs(view['hand'])
    # 暗杠：已听牌时放弃（拆散成形手牌得不偿失）
    if kongs and not _is_tenpai(view['hand'], view['exposedMelds'], rules):
        return {'kind': 'concealed-kong', 'tile': kongs[0]}

    if getattr(rules, 'wind_kong', lambda _hand: False)(view['hand']) \
            and not _is_tenpai(view['hand'], view['exposedMelds'], rules):
        return {'kind': 'wind-kong'}

    return {'kind': 'discard', 'handIndex': choose_discard_index(
        view['hand'], rule_set=rules, exposed_melds=view.get('exposedMelds', 0))}


def _can_be_tenpai(after_length: int, exposed_melds: int) -> bool:
    """打出/当前手牌长度是否可能听牌（= 3n+1，补 1 张即胡）。"""
    needed_melds = 4 - exposed_melds
    return after_length == needed_melds * 3 + 1


def _is_tenpai(hand: list[TileType], exposed_melds: int, rules: GameRuleSet) -> bool:
    """当前手牌是否已听牌（打出 1 张后为 3n+1 听牌态且听口非空）。散手直接返回 False。"""
    if not _can_be_tenpai(len(hand) - 1, exposed_melds):
        return False
    for index in range(len(hand)):
        after = hand[:index] + hand[index + 1:]
        if rules.waiting_tiles(after, exposed_melds):
            return True
    return False


def _discard_quality(hand: list[TileType], index: int, exposed_melds: int,
                     rules: GameRuleSet) -> tuple[bool, int, int]:
    """打出某张后的听口质量：(是否听牌, 听口数, 质量分)。散手跳过重计算。"""
    after = hand[:index] + hand[index + 1:]
    if not _can_be_tenpai(len(after), exposed_melds):
        return (False, 0, -1)
    waits = rules.waiting_tiles(after, exposed_melds)
    if not waits:
        return (False, 0, -1)
    return (True, len(waits), len(waits) * 10)


def _best_discard_quality(hand: list[TileType], exposed_melds: int,
                          rules: GameRuleSet) -> tuple[bool, int, int]:
    """当前手牌的最佳弃牌听口质量。"""
    best: tuple[bool, int, int] = (False, 0, -1)
    for index in range(len(hand)):
        quality = _discard_quality(hand, index, exposed_melds, rules)
        if _better_quality(quality, best):
            best = quality
    return best


def _current_tenpai(hand: list[TileType], exposed_melds: int,
                    rules: GameRuleSet) -> tuple[bool, int, int]:
    """当前手牌（未打出）的听口：直接 waiting_tiles，手牌为 3n+1 听牌态时才计算。"""
    if not _can_be_tenpai(len(hand), exposed_melds):
        return (False, 0, -1)
    waits = rules.waiting_tiles(hand, exposed_melds)
    if not waits:
        return (False, 0, -1)
    return (True, len(waits), len(waits) * 10)


def _better_quality(a: tuple[bool, int, int], b: tuple[bool, int, int]) -> bool:
    if a[0] != b[0]:
        return a[0]
    if not a[0]:
        return False
    return a[2] > b[2]


def decide_claim(view: dict, rule_set: Optional[GameRuleSet] = None) -> str:
    """面对弃牌：能杠必杠；碰需评估碰后听口是否优于现状，不提升则 pass。"""
    if view['canGang']:
        return 'gang'
    rules = rule_set or get_default_rule_set()
    if not view.get('tile'):
        return 'peng'
    if rules.matching_count(view['hand'], view['tile']) < 2:
        return 'pass'
    # 现状：当前手牌（未碰）的听口（13 张 exposed=0 / 10 张 exposed=1 为听牌态）
    baseline = _current_tenpai(view['hand'], view.get('exposedMelds', 0), rules)
    after_peng = remove_matches(list(view['hand']), view['tile'], 2)
    if not after_peng:
        return 'pass'
    after_quality = _best_discard_quality(after_peng, view.get('exposedMelds', 0) + 1, rules)
    return 'peng' if _better_quality(after_quality, baseline) else 'pass'


def decide_rob_kong(_view: dict) -> str:
    """面对加杠：当前 AI 能抢必抢；未来可按听牌风险权衡后返回 'pass'。"""
    return 'win'


def choose_discard_index(
    hand: list[TileType],
    random=None,
    rule_set: Optional[GameRuleSet] = None,
    exposed_melds: int = 0,
) -> int:
    """弃牌启发式：优先打掉「孤张」——同牌少、无相邻靠张的牌；
    白板（癞子）加罚分保手。有 exposed_melds 时叠加听口质量：
    打出后听口越多越后打，已听牌优先保留。random 注入以便测试确定化。

    分数 = 同牌数×4 + 相邻靠张数×2 + 白板罚分10 + 随机抖动 - 听口加分，取最小者。
    """
    rules = rule_set or get_default_rule_set()
    _rand = random if random is not None else _random.random
    scored = []
    for index, tile in enumerate(hand):
        same = rules.matching_count(hand, tile) - 1
        neighbors = 0
        match = _SUITED_RE.match(tile)
        if match:
            suit, rank_s = match.groups()
            number = int(rank_s)
            if f'{suit}{number - 1}' in hand:
                neighbors += 1
            if f'{suit}{number + 1}' in hand:
                neighbors += 1
        penalty = 10 if rules.is_joker_tile(tile) else 0
        base = same * 4 + neighbors * 2 + penalty + _rand()
        ready, wait_count, listen_score = _discard_quality(hand, index, exposed_melds, rules)
        listen_bonus = 1000 + listen_score if ready else 0
        scored.append((base - listen_bonus, index))
    # 稳定排序：同分保持原手牌顺序（与 TS 端 Array.sort 一致）
    scored.sort(key=lambda pair: pair[0])
    return scored[0][1] if scored else 0


def make_turn_view(player: GamePlayer, exposed_melds: int, kong_bloom: bool) -> dict:
    """构造 AI 回合决策快照，只暴露决策需要的只读信息。"""
    return {
        'hand': player.hand,
        'melds': player.melds,
        'exposedMelds': exposed_melds,
        'kongBloom': kong_bloom,
    }
