"""武汉晃晃 authoritative rule profile."""

from dataclasses import dataclass
from collections import Counter
from functools import lru_cache

from app.core.tiles import shuffle
from app.models.game import GamePlayer, Meld, TileType
from app.rules.base import ClaimCapabilities
from app.rules.fans import FanContext, FanEvaluation

TILES = [f'{s}{n}' for s in 'mps' for n in range(1, 10)] + ['red', 'green', 'white']
USABLE = tuple(t for t in TILES if t != 'red')


def joker_for(tile: TileType) -> TileType:
    if tile[0] in 'mps':
        return f'{tile[0]}{int(tile[1]) % 9 + 1}'
    return 'white' if tile == 'green' else 'green'


def _meldable(values: tuple[int, ...], wild: int, left: int) -> bool:
    @lru_cache(maxsize=None)
    def visit(counts: tuple[int, ...], jokers: int, groups: int) -> bool:
        try: i = next(i for i, amount in enumerate(counts) if amount)
        except StopIteration: return jokers == groups * 3
        if not groups: return False
        amount = min(3, counts[i]); need = 3 - amount
        if need <= jokers:
            next_counts = list(counts); next_counts[i] -= amount
            if visit(tuple(next_counts), jokers - need, groups - 1): return True
        tile = USABLE[i]
        if tile[0] in 'mps':
            rank = int(tile[1])
            for start in range(max(1, rank - 2), min(7, rank) + 1):
                next_counts = list(counts); missing = 0
                for value in (f'{tile[0]}{start + x}' for x in range(3)):
                    pos = USABLE.index(value)
                    if next_counts[pos]: next_counts[pos] -= 1
                    else: missing += 1
                if missing <= jokers and visit(tuple(next_counts), jokers - missing, groups - 1): return True
        return False
    return visit(values, wild, left)


@dataclass
class WuhanRoundState:
    flip_tile: TileType | None = None
    joker_tiles: list[TileType] | None = None
    wall_break_index: int = 0
    @property
    def jokers(self): return self.joker_tiles or []


class WuhanHuanghuangRuleSet:
    code = 'wuhan-huanghuang'
    base_score = 1
    horse_count = 0
    supports_chi = True
    supports_flowers = True

    def __init__(self): self.round_state = WuhanRoundState()
    def create_wall(self): return [tile for tile in TILES for _ in range(4)]
    def begin_round(self, *, dealer, dice, second_dice=None, random=None, **_):
        wall = shuffle(self.create_wall(), random)
        flip = wall.pop()
        joker = joker_for(flip)
        self.round_state = WuhanRoundState(flip, [joker], 0)
        return {'wall': wall, 'flipTile': flip, 'jokers': [joker], 'flipStack': 59,
                'openingStack': 0, 'wallBreakIndex': 0, 'flipSeat': dealer}
    # 红中不再摸到即自动花杠；玩家选择杠红中、或选择打出癞子时，
    # 由 GameManager 以武汉专属单张杠处理。
    def is_flower_tile(self, tile): return False
    def is_joker_tile(self, tile): return tile in self.round_state.jokers
    def is_pure_one_suit(self, hand: list[TileType], melds: list[Meld]) -> bool:
        """清一色须同时检查手牌及已吃、碰、杠的结构副露；红中/癞子单张杠不参与。"""
        joker = self.round_state.jokers[0] if self.round_state.jokers else None
        tiles = [tile for tile in hand if tile != joker]
        tiles.extend(
            tile for meld in melds if meld.type != 'flower'
            for tile in meld.tiles if tile != joker
        )
        suits = {tile[0] for tile in tiles if tile and tile[0] in 'mps'}
        return len(suits) == 1 and all(tile[0] in 'mps' for tile in tiles)
    def seven_pairs_factor(self, hand: list[TileType], melds: list[Meld]) -> int:
        """七对/龙七对/双龙七对的基础倍数：1/2/4；七对不叠加门前清。"""
        if any(meld.type != 'flower' for meld in melds) or 'red' in hand:
            return 0
        joker = self.round_state.jokers[0] if self.round_state.jokers else None
        wild = hand.count(joker) if joker else 0
        counts = Counter(tile for tile in hand if tile != joker)
        singles = sum(amount % 2 for amount in counts.values())
        remaining_wild = wild - singles
        pairs = sum(amount // 2 for amount in counts.values()) + singles + (remaining_wild // 2 if remaining_wild >= 0 and remaining_wild % 2 == 0 else -99)
        if pairs != 7:
            return 0
        quads = sum(amount == 4 for amount in counts.values())
        return 4 if quads > 1 else 2 if quads else 1
    def is_claimable_tile(self, tile): return tile != 'red'
    def should_auto_win_on_flowers(self, count): return False
    def resolve_win_tile(self, winner: GamePlayer, options):
        return options.get('winTile') or winner.hand[winner.drawnTileIndex if winner.drawnTileIndex >= 0 else -1]
    def is_winning_hand(self, tiles, exposed_meld_count=0, ordinary_jokers=None):
        if 'red' in tiles or len(tiles) != (4 - exposed_meld_count) * 3 + 2: return False
        joker = self.round_state.jokers[0] if self.round_state.jokers else None
        ordinary = (ordinary_jokers or []).count(joker)
        wild = max(0, tiles.count(joker) - ordinary) if joker else 0
        naturals = [t for t in tiles if t != joker] + ([joker] * ordinary if joker else [])
        counts = Counter(naturals); values = tuple(counts[t] for t in USABLE)
        if wild >= 2 and _meldable(values, wild - 2, 4 - exposed_meld_count): return True
        for i, amount in enumerate(values):
            use = min(2, amount); need = 2 - use
            if need <= wild:
                next_values = list(values); next_values[i] -= use
                if _meldable(tuple(next_values), wild - need, 4 - exposed_meld_count): return True
        return False
    def waiting_tiles(self, tiles, exposed_meld_count=0):
        return [t for t in USABLE if self.is_winning_hand([*tiles, t], exposed_meld_count)]
    def matching_count(self, tiles, tile): return tiles.count(tile)
    def concealed_kongs(self, tiles): return [t for t in set(tiles) if t != 'red' and tiles.count(t) == 4]
    def can_added_kong(self, hand, melds: list[Meld], tile): return tile in hand and any(m.type == 'peng' and m.tile == tile for m in melds)
    def can_rob_kong(self, tiles, tile, exposed_meld_count=0):
        ordinary = [tile] if tile in self.round_state.jokers else []
        return self.is_winning_hand([*tiles, tile], exposed_meld_count, ordinary)
    def claim_capabilities(self, hand, tile):
        n = hand.count(tile); return ClaimCapabilities(can_peng=n >= 2, can_gang=n >= 3)
    def chi_options(self, hand, tile):
        if tile[0] not in 'mps': return []
        rank = int(tile[1]); out = []
        for start in range(max(1, rank - 2), min(7, rank) + 1):
            seq = [f'{tile[0]}{start + x}' for x in range(3)]; need = list(seq); need.remove(tile)
            if all(hand.count(t) >= need.count(t) for t in set(need)): out.append({'kind': 'sequence', 'tiles': seq})
        return out
    def draw_horses(self, wall, amount=None, seat=0): return {'horses': [], 'hits': 0}
    def evaluate_fans(self, context: FanContext): return FanEvaluation((), 1, 0, 1, 1)
    def score_hand(self, context): return {'multiplier': 1, 'totalMultiplier': 1, 'horsePoints': 0, 'points': 1, 'details': []}
