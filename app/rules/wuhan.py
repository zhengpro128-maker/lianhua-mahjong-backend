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
MIN_WIN_POINTS = 9
BIG_DISCARD_KINDS = frozenset({'七对', '龙七对', '双龙七对', '清一色', '碰碰胡'})


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


def _seven_pairs(tiles: list[TileType], joker: TileType | None,
                 ordinary_jokers: list[TileType] | None = None) -> bool:
    if len(tiles) != 14 or 'red' in tiles:
        return False
    wild, natural = _split_jokers(tiles, joker, ordinary_jokers)
    values = Counter(natural).values()
    singles = sum(amount % 2 for amount in values)
    remaining = wild - singles
    return (remaining >= 0 and remaining % 2 == 0
            and sum(amount // 2 for amount in values) + singles + remaining // 2 == 7)


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
        # 与单机 roundFlow 一致：翻牌只是指示牌，仍留在物理牌墙中，
        # 后续可照常从牌头或牌尾被摸走，不能凭空少一张牌。
        indicator_index = ((dice[0] + dice[1] - 2) * 2 + 1) % len(wall)
        flip = wall[indicator_index]
        joker = joker_for(flip)
        self.round_state = WuhanRoundState(flip, [joker], 0)
        return {'wall': wall, 'flipTile': flip, 'jokers': [joker], 'flipStack': indicator_index // 2,
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
        return (self.is_standard_winning_hand(tiles, exposed_meld_count, ordinary_jokers)
                or (exposed_meld_count == 0 and _seven_pairs(
                    tiles, self.round_state.jokers[0] if self.round_state.jokers else None,
                    ordinary_jokers)))
    def is_standard_winning_hand(self, tiles, exposed_meld_count=0, ordinary_jokers=None):
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
    def concealed_kongs(self, tiles):
        joker = self.round_state.jokers[0] if self.round_state.jokers else None
        return [t for t in set(tiles) if t != 'red' and t != joker and tiles.count(t) == 4]
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
            if all(hand.count(t) >= need.count(t) for t in set(need)):
                # 吃牌选项是跨规则集的公共协议；manager / perform_chi 需要用 tile
                # 从上家牌河移除被吃的弃牌。缺少该字段会在联机真人吃牌时触发 KeyError。
                out.append({'tile': tile, 'kind': 'sequence', 'tiles': seq})
        return out
    def draw_horses(self, wall, amount=None, seat=0): return {'horses': [], 'hits': 0}
    def evaluate_fans(self, context: FanContext): return FanEvaluation((), 1, 0, 1, 1)
    def score_hand(self, context): return {'multiplier': 1, 'totalMultiplier': 1, 'horsePoints': 0, 'points': 1, 'details': []}


def wuhan_kong_label(kind: str) -> str:
    return {
        'red': '红中杠', 'discard': '明杠', 'added': '补杠',
        'concealed': '暗杠', 'joker': '癞子杠',
    }[kind]


def wuhan_kong_kinds(melds: list[Meld], joker: TileType | None) -> list[str]:
    """返回一名玩家自己的杠番，和前端 ruleProfile 保持一致。"""
    kinds: list[str] = []
    for meld in melds:
        if meld.type == 'flower' and meld.specialKong == 'red' and meld.tile == 'red':
            kinds.append('red')
        elif meld.type == 'flower' and meld.specialKong == 'joker' and meld.tile == joker:
            kinds.append('joker')
        elif meld.type == 'angang':
            kinds.append('joker' if meld.tile == joker else 'concealed')
        elif meld.type == 'gang':
            kinds.append('added' if meld.added else 'discard')
    return kinds


def wuhan_kong_multiplier(kinds: list[str], kong_bloom: bool = False) -> float:
    factor = 1
    for kind in kinds:
        factor *= 4 if kind in ('concealed', 'joker') else 2
    return factor / 2 if kong_bloom else factor


def wuhan_settlement_kong_kinds(players: list[GamePlayer], winner_index: int,
                                 joker: TileType | None, kong_bloom: bool) -> list[str]:
    """胡家仅计自己的杠；杠开时的扣一番在 multiplier 中统一处理。"""
    return wuhan_kong_kinds(players[winner_index].melds, joker)


def _split_jokers(tiles: list[TileType], joker: TileType | None,
                  ordinary_jokers: list[TileType] | None = None) -> tuple[int, list[TileType]]:
    if not joker:
        return 0, list(tiles)
    ordinary = (ordinary_jokers or []).count(joker)
    all_jokers = tiles.count(joker)
    return max(0, all_jokers - ordinary), [t for t in tiles if t != joker] + [joker] * min(all_jokers, ordinary)


def is_wuhan_hard_win(rules: WuhanHuanghuangRuleSet, tiles: list[TileType],
                       exposed_meld_count: int, joker: TileType | None) -> bool:
    """单张癞子须按自身牌面仍能成胡；两张及以上癞子一律软胡。"""
    joker_count = tiles.count(joker) if joker else 0
    if joker_count > 1:
        return False
    # ordinary_jokers 会保留这张牌的真实面值，而不是把它当作万能牌。
    return rules.is_winning_hand(
        tiles, exposed_meld_count, [joker] if joker_count == 1 and joker else [])


def _triplets_only(values: tuple[int, ...], wild: int, left: int) -> bool:
    @lru_cache(maxsize=None)
    def visit(counts: tuple[int, ...], jokers: int, groups: int) -> bool:
        try:
            index = next(i for i, amount in enumerate(counts) if amount)
        except StopIteration:
            return jokers == groups * 3
        if not groups:
            return False
        used = min(3, counts[index])
        if 3 - used > jokers:
            return False
        next_counts = list(counts)
        next_counts[index] -= used
        return visit(tuple(next_counts), jokers - (3 - used), groups - 1)
    return visit(values, wild, left)


def evaluate_wuhan_win(rules: WuhanHuanghuangRuleSet, tiles: list[TileType], *,
                       exposed: int, exposed_melds: list[Meld], joker: TileType | None,
                       ordinary_jokers: list[TileType] | None = None,
                       men_qian_qing: bool = False, self_draw: bool = False) -> list[str]:
    """前后端共用的武汉基础牌型：屁胡、碰碰胡、清一色、门前清、七对。"""
    if 'red' in tiles:
        return []
    wild, natural = _split_jokers(tiles, joker, ordinary_jokers)
    kinds: list[str] = []
    standard = rules.is_standard_winning_hand(tiles, exposed, ordinary_jokers)
    if standard and wild <= 1:
        kinds.append('屁胡')
    if standard:
        all_natural = list(natural)
        for meld in exposed_melds:
            all_natural.extend(tile for tile in meld.tiles if tile not in (joker, 'red'))
        suits = {tile[0] for tile in all_natural if tile[0] in 'mps'}
        honors = any(tile in ('green', 'white') for tile in all_natural)
        if len(suits) == 1 and not honors:
            kinds.append('清一色')
        if not any(meld.type == 'chi' for meld in exposed_melds):
            counts = Counter(natural)
            values = tuple(counts[t] for t in USABLE)
            for index, amount in enumerate(values):
                used = min(2, amount)
                need = 2 - used
                if need <= wild:
                    remainder = list(values)
                    remainder[index] -= used
                    if _triplets_only(tuple(remainder), wild - need, 4 - exposed):
                        kinds.append('碰碰胡')
                        break
    if not exposed and _seven_pairs(tiles, joker, ordinary_jokers):
        values = list(Counter(natural).values())
        quads = sum(amount == 4 for amount in values)
        kinds.append('双龙七对' if quads > 1 else '龙七对' if quads else '七对')
    if not any(kind in ('七对', '龙七对', '双龙七对') for kind in kinds) and self_draw and men_qian_qing and kinds:
        kinds.append('门前清')
    return kinds


def with_wuhan_win_scenes(kinds: list[str], *, exposed: int, discard_win: bool,
                          kong_bloom: bool, robbed_kong: bool) -> list[str]:
    result = list(kinds)
    if exposed == 4 and discard_win:
        result.append('全求人')
    if kong_bloom:
        result.append('杠上开花')
    if robbed_kong:
        result.append('抢杠胡')
    return result


def wuhan_pattern_points(kind: str) -> int:
    return {'双龙七对': 40, '龙七对': 20, '门前清': 6}.get(kind, 10)


def wuhan_gets_self_draw_bonus(kinds: list[str]) -> bool:
    return '杠上开花' not in kinds and any(kind not in ('屁胡', '门前清') for kind in kinds)


def wuhan_discarder_multiplier(kinds: list[str], discard_win: bool) -> float:
    return 1.2 if discard_win and any(kind in BIG_DISCARD_KINDS for kind in kinds) else 2


def wuhan_raw_win_points(kinds: list[str], self_draw: bool, hard: bool, kongs: list[str],
                         kong_bloom: bool = False) -> float:
    big = [kind for kind in kinds if kind != '屁胡']
    has_other_big = any(kind != '门前清' for kind in big)
    if big:
        base = 1
        for kind in big:
            base *= 2 if kind == '门前清' and has_other_big else wuhan_pattern_points(kind)
    else:
        base = 3 if '屁胡' in kinds and self_draw else 1 if '屁胡' in kinds else 0
    return base * (2 if hard else 1) * (1.5 if self_draw and wuhan_gets_self_draw_bonus(kinds) else 1) * wuhan_kong_multiplier(kongs, kong_bloom)


def cap_wuhan_payment(points: float) -> float:
    return min(50, max(0, points))


def wuhan_meets_minimum(kinds: list[str], self_draw: bool, hard: bool,
                         kongs: list[str], discard_win: bool,
                         kong_bloom: bool = False) -> bool:
    payment = wuhan_raw_win_points(kinds, self_draw, hard, kongs, kong_bloom)
    total = payment * 3 if self_draw else payment * (2 + wuhan_discarder_multiplier(kinds, discard_win))
    return total >= MIN_WIN_POINTS
