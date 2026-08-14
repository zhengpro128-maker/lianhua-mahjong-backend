from app.core.lotus_rules import (
    chi_options,
    evaluate_pattern,
    is_seven_pairs,
    is_thirteen_lan,
    is_thirteen_orphans,
)
from app.core.lotus_wall import build_lotus_wall, compute_jokers, next_in_sequence
from app.models.game import Meld
from app.rules.lotus_legacy import LotusLegacyRuleSet
from app.settlement import settlement_service
from app.game.manager import GameManager
from app.game.player import AIPlayer


def test_lotus_wall_removes_flip_stack_and_computes_jokers():
    result = build_lotus_wall(
        dealer=0, dice=(2, 3), second_dice=(4, 4), random=lambda: 0,
    )
    assert len(result['wall']) == 134
    assert result['jokers'] == compute_jokers(result['flipTile'])
    assert next_in_sequence('m9') == 'm1'
    assert next_in_sequence('north') == 'east'


def test_lotus_special_patterns():
    seven_pairs = [
        'm1', 'm1', 'm2', 'm2', 'm3', 'm3', 'm4', 'm4',
        'p1', 'p1', 'p2', 'p2', 's1', 's1',
    ]
    orphans = [
        'm1', 'm9', 'p1', 'p9', 's1', 's9',
        'east', 'south', 'west', 'north', 'red', 'green', 'white', 'white',
    ]
    lan = ['m1', 'm4', 'm7', 'p2', 'p5', 'p8', 's3', 's6', 's9',
           'east', 'south', 'red', 'green', 'white', 'north']
    assert is_seven_pairs(seven_pairs, [])
    assert is_thirteen_orphans(orphans)
    assert is_thirteen_lan(lan[:14], [])


def test_lotus_qi_xing_allows_joker_to_substitute_missing_honor():
    # 物理缺 white；精牌 m2 替补成 white → 仍成立七星十三烂（七字允许精牌替补）。
    hand = ['east', 'south', 'west', 'north', 'red', 'green',
            'm1', 'm4', 'm7', 'p2', 'p5', 'p8', 's1', 'm2']
    result = evaluate_pattern(hand, 0, ['m2', 'm3'])
    assert result == {'pattern': 'qiXing', 'fan': 4, 'label': '七星十三烂'}


def test_lotus_qi_xing_requires_usable_joker_for_missing_honor():
    # 物理缺 white，且无精可替补 → 只算十三烂（2 番）。
    hand = ['east', 'south', 'west', 'north', 'red', 'green',
            'm1', 'm4', 'm7', 'p2', 'p5', 'p8', 's1', 's4']
    result = evaluate_pattern(hand, 0, [])
    assert result == {'pattern': 'shiSanLan', 'fan': 2, 'label': '十三烂'}


def test_lotus_chi_and_settlement():
    assert chi_options(['m4', 'm6'], 'm5') == [
        {'tile': 'm5', 'tiles': ['m4', 'm5', 'm6'], 'kind': 'sequence'}
    ]
    result = settlement_service.calculate_lotus_win(
        player_count=4, winner_index=1, base_fan=1,
        winner_is_dealer=False, self_draw_style=False, dealer_index=0,
    )
    assert result.total_won == 400
    assert {item['playerIndex']: item['amount'] for item in result.deltas} == {
        1: 400, 0: -200, 2: -100, 3: -100,
    }


def test_lotus_ruleset_score_has_base_and_display_fan():
    rules = LotusLegacyRuleSet()
    rules.round_state.joker_tiles = []
    hand = ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'm7', 'm8', 'm9',
            'p1', 'p2', 'p3', 's1', 's1']
    result = rules.score_legacy_hand(hand, 0, dealer=False, self_draw=False)
    assert result['baseFan'] == 1
    assert result['settlement']['total'] == 400


def test_lotus_discard_win_uses_the_physical_discard_tile():
    rules = LotusLegacyRuleSet()
    rules.round_state.joker_tiles = []
    manager = GameManager(
        controllers=[AIPlayer() for _ in range(4)],
        rule_set=rules,
    )
    manager._reset_players()
    manager.phase = 'checking'
    manager.players[0].discards = ['m3']
    manager.players[1].hand = [
        'm1', 'm2', 'm4', 'm5', 'm6', 'm7', 'm8', 'm9',
        'p1', 'p2', 'p3', 's1', 's1',
    ]
    manager.end_game(1, {'winTile': 'm3', 'sourceFrom': 0})
    assert manager.phase == 'settled'
    assert manager.players[0].discards == []
    assert manager.result['winnerIndex'] == 1
    # 点炮胡：赢家手牌保持 13 张，和牌由 winTile 单独携带（不再追加进手牌）。
    assert len(manager.players[1].hand) == 13
    assert 'm3' not in manager.players[1].hand


def test_lotus_robbed_kong_win_conserves_the_robbed_tile():
    rules = LotusLegacyRuleSet()
    rules.round_state.joker_tiles = []
    manager = GameManager(
        controllers=[AIPlayer() for _ in range(4)],
        rule_set=rules,
    )
    manager._reset_players()
    # 补杠副露（pending gang）供抢杠还原
    manager.players[0].melds = [
        Meld(type='gang', tile='m3', tiles=['m3', 'm3', 'm3', 'm3'],
             added=True, pending=True),
    ]
    manager.players[1].hand = [
        'm1', 'm2', 'm4', 'm5', 'm6', 'm7', 'm8', 'm9',
        'p1', 'p2', 'p3', 's1', 's1',
    ]

    manager.end_game(1, {
        'winTile': 'm3',
        'robbedKong': True,
        'robbedKongPlayerIndex': 0,
    })

    assert manager.phase == 'settled'
    assert manager.result['winnerIndex'] == 1
    # 杠副露还原为碰（3 张），被抢的杠牌进入抢杠者手牌，牌数守恒（14 张）。
    assert manager.players[0].melds[0].type == 'peng'
    assert manager.players[0].melds[0].tiles == ['m3', 'm3', 'm3']
    assert len(manager.players[1].hand) == 14
    assert manager.players[1].hand.count('m3') == 1


def test_lotus_waiting_tiles_reports_joker_face_as_wait():
    rules = LotusLegacyRuleSet()
    rules.round_state.joker_tiles = ['red']
    hand = ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'p1', 'p2', 'p3', 's1', 's2', 's3', 'east']
    waits = rules.waiting_tiles(hand, 0)
    assert 'east' in waits
    # 精牌面本身是听口：补入后作为癞子与 east 成对。
    assert 'red' in waits
