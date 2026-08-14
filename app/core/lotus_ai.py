"""莲花麻将（lotus-legacy）AI 决策层 —— 从 src/game/variants/lotus/lotusAi.ts 翻译。

与 core/ai.py 的差异：
- 副露决策（decide_claim）按「动作后听牌质量」与现状比较，不提升则 pass，
  而非无脑 gang>peng>chi；
- 弃牌启发式（choose_discard_index）按听口数/剩余可见张/特殊牌型分/安全度打分，
  而非只按同牌数+靠张+癞子罚分。

纯决策：只看状态给动作命令，不改任何状态、不触发表现副作用。
"""

import random as _random
import re
from collections import Counter
from functools import cmp_to_key
from typing import Callable, Optional

from app.core.actions import remove_matches
from app.core.lotus_rules import matching_count, waiting_tiles
from app.models.game import TileType

_SUITED_RE = re.compile(r'^([mps])([1-9])$')


def _wildcard_set(jokers: list[TileType]) -> set[TileType]:
    """癞子集合 = 精牌 + 白板（白板可替补精牌及白板本身）。"""
    return set(jokers) | {'white'}


# ── 听牌质量打分 ─────────────────────────────────────────────

def _remaining_count(tile: TileType, visible_tiles: list[TileType]) -> int:
    """剩余可见张 = 4 - 可见牌中该牌张数（他人暗手不计）。"""
    return max(0, 4 - matching_count(visible_tiles, tile))


def _hand_quality_attack_score(waits: list[TileType], effective_remaining: int,
                               special_score: int) -> int:
    return (80 if len(waits) > 0 else 0) + len(waits) * 10 \
        + effective_remaining * 2 + special_score * 3


def _public_safety_score(tile: TileType, public_tiles: list[TileType],
                         upper_last_discard: Optional[TileType]) -> int:
    """按公开可见的该牌数量评估安全度；上家刚打的牌优先跟打。"""
    public_count = matching_count(public_tiles, tile)
    score = 24 if public_count >= 3 else 12 if public_count >= 2 else 4 if public_count >= 1 else 0
    if upper_last_discard == tile:
        score += 12
    match = _SUITED_RE.match(tile)
    if match and match.group(2) in ('1', '7'):
        middle = f'{match.group(1)}4'
        if middle in public_tiles:
            score += 5
    return score


def _special_pattern_score(hand: list[TileType], exposed_melds: int,
                           jokers: list[TileType]) -> int:
    """门清时对十三烂/七对潜力的加分；副露后不再追求特殊牌型。"""
    if exposed_melds > 0:
        return -20
    effective_jokers = _wildcard_set(jokers)
    lan_defects = _shi_san_lan_defects(hand, effective_jokers)
    lan_score = (4 - lan_defects) * 4 if lan_defects <= 3 else 0
    return lan_score + _pair_potential(hand, effective_jokers) * 2


def _shi_san_lan_defects(hand: list[TileType], jokers: set[TileType]) -> int:
    """十三烂的缺陷数：重复牌 + 同花色相邻点数差 <3 的次数（癞子已剔除）。

    注意：必须用数牌正则精确匹配（`^[mps][1-9]$`），不能用 startswith('s')——
    'south' 也以 's' 开头，会误入数牌列表。
    """
    natural = [tile for tile in hand if tile not in jokers]
    defects = len(natural) - len(set(natural))
    for suit in ('m', 'p', 's'):
        ranks: list[int] = []
        for tile in natural:
            match = _SUITED_RE.match(tile)
            if match and match.group(1) == suit:
                ranks.append(int(match.group(2)))
        ranks.sort()
        for index in range(1, len(ranks)):
            if ranks[index] - ranks[index - 1] < 3:
                defects += 1
    return defects


def _pair_potential(hand: list[TileType], jokers: set[TileType]) -> int:
    """成对潜力：已有对子数 + 癞子可补足的单张数。"""
    counts: Counter[TileType] = Counter()
    joker_count = 0
    for tile in hand:
        if tile in jokers:
            joker_count += 1
        else:
            counts[tile] += 1
    pairs = 0
    singles = 0
    for count in counts.values():
        pairs += count // 2
        singles += count % 2
    return pairs + min(singles, joker_count)


def _discard_heuristic(hand: list[TileType], discarded: TileType,
                       jokers: list[TileType], early_round: bool) -> int:
    """弃牌启发式（质量打分里的 tie-break 分量）。"""
    same = matching_count(hand, discarded) - 1
    match = _SUITED_RE.match(discarded)
    neighbors = 0
    edge_penalty = 0
    if match:
        rank = int(match.group(2))
        suit = match.group(1)
        neighbors += 1 if f'{suit}{rank - 1}' in hand else 0
        neighbors += 1 if f'{suit}{rank + 1}' in hand else 0
        edge_penalty = 0 if rank in (1, 9) else 1
    honor_penalty = 0 if match else (12 if early_round else 3)
    joker_penalty = 100 if discarded in _wildcard_set(jokers) else 0
    return same * 4 + neighbors * 2 + edge_penalty + honor_penalty + joker_penalty


def _discard_quality(after_discard: list[TileType], discarded: TileType,
                     exposed_melds: int, jokers: list[TileType],
                     visible_tiles: list[TileType], early_round: bool,
                     public_tiles: list[TileType],
                     upper_last_discard: Optional[TileType]) -> dict:
    waits = waiting_tiles(after_discard, exposed_melds, jokers)
    effective_remaining = sum(_remaining_count(tile, visible_tiles) for tile in waits)
    special_score = _special_pattern_score(after_discard, exposed_melds, jokers)
    safety_score = _public_safety_score(discarded, public_tiles, upper_last_discard)
    attack_score = _hand_quality_attack_score(waits, effective_remaining, special_score)
    return {
        'ready': len(waits) > 0,
        'waits': waits,
        'effectiveRemaining': effective_remaining,
        'specialScore': special_score,
        'heuristic': _discard_heuristic(after_discard, discarded, jokers, early_round),
        'safetyScore': safety_score,
        'netScore': attack_score + safety_score * 2,
    }


def _current_hand_quality(hand: list[TileType], exposed_melds: int,
                          jokers: list[TileType],
                          visible_tiles: Optional[list[TileType]] = None) -> dict:
    if visible_tiles is None:
        visible_tiles = hand
    waits = waiting_tiles(hand, exposed_melds, jokers)
    special_score = _special_pattern_score(hand, exposed_melds, jokers)
    effective_remaining = sum(_remaining_count(tile, visible_tiles) for tile in waits)
    attack_score = _hand_quality_attack_score(waits, effective_remaining, special_score)
    return {
        'ready': len(waits) > 0,
        'waits': waits,
        'effectiveRemaining': effective_remaining,
        'specialScore': special_score,
        'heuristic': 0,
        'safetyScore': 0,
        'netScore': attack_score,
    }


def _compare_quality(a: dict, b: dict) -> int:
    if a['ready'] != b['ready']:
        return 1 if a['ready'] else -1
    if a['netScore'] != b['netScore']:
        return a['netScore'] - b['netScore']
    if a['effectiveRemaining'] != b['effectiveRemaining']:
        return a['effectiveRemaining'] - b['effectiveRemaining']
    if len(a['waits']) != len(b['waits']):
        return len(a['waits']) - len(b['waits'])
    if a['specialScore'] != b['specialScore']:
        return a['specialScore'] - b['specialScore']
    if a['safetyScore'] != b['safetyScore']:
        return a['safetyScore'] - b['safetyScore']
    return b['heuristic'] - a['heuristic']


def _best_discard_after_claim(hand: list[TileType], exposed_melds: int,
                              jokers: list[TileType],
                              visible_tiles: Optional[list[TileType]] = None,
                              early_round: bool = False,
                              public_tiles: Optional[list[TileType]] = None,
                              upper_last_discard: Optional[TileType] = None):
    if not hand:
        return None
    if visible_tiles is None:
        visible_tiles = hand
    if public_tiles is None:
        public_tiles = []
    joker_set = _wildcard_set(jokers)
    has_natural = any(tile not in joker_set for tile in hand)
    candidates = []
    for index, tile in enumerate(hand):
        if has_natural and tile in joker_set:
            continue
        after_discard = hand[:index] + hand[index + 1:]
        candidates.append({
            'index': index,
            'tile': tile,
            'quality': _discard_quality(after_discard, tile, exposed_melds, jokers,
                                        visible_tiles, early_round, public_tiles,
                                        upper_last_discard),
        })
    candidates.sort(key=cmp_to_key(
        lambda a, b: _compare_quality(b['quality'], a['quality']) or (a['index'] - b['index'])))
    return candidates[0] if candidates else None


# ── 副露决策 ────────────────────────────────────────────────

def _claim_action_priority(action: dict) -> int:
    return 0 if action['kind'] == 'peng' else 1


def _remove_claimed_meld_tiles(hand: list[TileType], meld: dict, tile: TileType):
    """吃牌落地：从手牌移除除弃牌外的两张搭子；缺牌返回 None。"""
    remaining = list(hand)
    for meld_tile in meld['tiles']:
        if meld_tile == tile:
            continue
        try:
            index = remaining.index(meld_tile)
        except ValueError:
            return None
        remaining.pop(index)
    return remaining


def decide_claim(view: dict) -> dict:
    """面对弃牌：能杠必杠；碰/吃按动作后听牌质量与现状比较，不提升则 pass。"""
    if view.get('canGang'):
        return {'kind': 'gang'}

    baseline = _current_hand_quality(
        view['hand'], view['exposedMelds'], view['jokers'], view.get('visibleTiles'))
    candidates = []

    if view.get('canPeng') and matching_count(view['hand'], view['tile']) >= 2:
        after_peng = remove_matches(list(view['hand']), view['tile'], 2)
        discard = _best_discard_after_claim(
            after_peng, view['exposedMelds'] + 1, view['jokers'],
            view.get('visibleTiles'), view.get('earlyRound', False),
            view.get('publicTiles'), view.get('upperLastDiscard'))
        if discard:
            candidates.append({
                'action': {'kind': 'peng', 'discardIndex': discard['index']},
                'quality': discard['quality'],
            })

    for meld in view.get('chiOptions', []):
        after_chi = _remove_claimed_meld_tiles(view['hand'], meld, view['tile'])
        if not after_chi:
            continue
        discard = _best_discard_after_claim(
            after_chi, view['exposedMelds'] + 1, view['jokers'],
            view.get('visibleTiles'), view.get('earlyRound', False),
            view.get('publicTiles'), view.get('upperLastDiscard'))
        if discard:
            candidates.append({
                'action': {'kind': 'chi', 'meld': meld},
                'quality': discard['quality'],
            })

    improving = [c for c in candidates if _compare_quality(c['quality'], baseline) > 0]
    if not improving:
        return {'kind': 'pass'}
    improving.sort(key=cmp_to_key(
        lambda a, b: _compare_quality(b['quality'], a['quality'])
        or (_claim_action_priority(a['action']) - _claim_action_priority(b['action']))))
    return improving[0]['action']


# ── 弃牌决策 ────────────────────────────────────────────────

def choose_discard_index(hand: list[TileType], jokers: list[TileType],
                         random: Optional[Callable[[], float]] = None,
                         options: Optional[dict] = None) -> int:
    """弃牌启发式：优先打孤张/字牌；精牌默认保留。带质量打分时按听口质量选牌。"""
    if random is None:
        random = _random.random
    options = options or {}
    joker_set = _wildcard_set(jokers)
    has_natural = any(tile not in joker_set for tile in hand)

    candidates = []
    for index, tile in enumerate(hand):
        if has_natural and tile in joker_set:
            continue
        same = matching_count(hand, tile) - 1
        match = _SUITED_RE.match(tile)
        neighbors = 0
        if match:
            rank = int(match.group(2))
            suit = match.group(1)
            neighbors += 1 if f'{suit}{rank - 1}' in hand else 0
            neighbors += 1 if f'{suit}{rank + 1}' in hand else 0
        honor = 0 if match else 6
        score = same * 4 + neighbors * 2 + honor + random()
        quality = None
        if options.get('exposedMelds') is not None:
            after_discard = hand[:index] + hand[index + 1:]
            quality = _discard_quality(
                after_discard, tile, options['exposedMelds'], jokers,
                options.get('visibleTiles') or hand,
                options.get('earlyRound', False),
                options.get('publicTiles') or [],
                options.get('upperLastDiscard'))
        candidates.append({'index': index, 'score': score, 'quality': quality})

    def cmp(a: dict, b: dict) -> int:
        if a['quality'] is not None and b['quality'] is not None:
            return _compare_quality(b['quality'], a['quality']) or (a['score'] - b['score'])
        return a['score'] - b['score']

    candidates.sort(key=cmp_to_key(cmp))
    return candidates[0]['index'] if candidates else 0
