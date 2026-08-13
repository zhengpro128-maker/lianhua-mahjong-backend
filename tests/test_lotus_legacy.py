from app.core.lotus_rules import (
    chi_options,
    is_seven_pairs,
    is_thirteen_lan,
    is_thirteen_orphans,
)
from app.core.lotus_wall import build_lotus_wall, compute_jokers, next_in_sequence
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


def test_lotus_chi_and_settlement():
    assert chi_options(['m4', 'm6'], 'm5') == [
        {'tile': 'm5', 'tiles': ['m4', 'm5', 'm6']}
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


def test_lotus_robbed_kong_win_scores_with_the_robbed_tile():
    rules = LotusLegacyRuleSet()
    rules.round_state.joker_tiles = []
    manager = GameManager(
        controllers=[AIPlayer() for _ in range(4)],
        rule_set=rules,
    )
    manager._reset_players()
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
