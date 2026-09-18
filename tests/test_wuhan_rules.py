from app.models.game import GamePlayer, Meld
from app.game.manager import GameManager
from app.rules.registry import get_rule_set
from app.rules.wuhan import (
    evaluate_wuhan_win, is_wuhan_hard_win, joker_for, wuhan_discarder_multiplier,
    wuhan_kong_kinds, wuhan_kong_multiplier, wuhan_raw_win_points,
    wuhan_settlement_kong_kinds,
)


def player(seat, hand=None, melds=None):
    return GamePlayer(name=str(seat), avatar='', score=1000, seat=seat,
                      hand=hand or [], discards=[], melds=melds or [],
                      redCount=0, drawnTileIndex=-1)


def test_wuhan_registry_and_wall():
    rules = get_rule_set('wuhan-huanghuang')
    assert len(rules.create_wall()) == 120
    assert set(rules.create_wall()) == {
        *(f'{s}{n}' for s in 'mps' for n in range(1, 10)), 'red', 'green', 'white'
    }
    opening = rules.begin_round(dealer=0, dice=[3, 4], random=lambda: 0.37)
    # 翻牌只是指示牌，单机和联机都不应从 120 张物理牌墙中移除它。
    assert len(opening['wall']) == 120
    assert opening['flipTile'] == opening['wall'][11]


def test_wuhan_joker_mapping():
    assert joker_for('m9') == 'm1'
    assert joker_for('green') == 'white'
    assert joker_for('white') == 'green'
    assert joker_for('red') == 'green'


def test_wuhan_standard_win_and_single_joker_hard_win():
    rules = get_rule_set('wuhan-huanghuang')
    rules.round_state.joker_tiles = ['p3']
    hand = ['m1', 'm2', 'm3', 'p1', 'p2', 'p3', 's4', 's5', 's6', 's7', 's8', 's9', 'green', 'green']
    assert rules.is_winning_hand(hand)
    assert is_wuhan_hard_win(rules, hand, 0, 'p3')
    assert not is_wuhan_hard_win(rules, [*hand[:-2], 'p3', 'p3'], 0, 'p3')


def test_wuhan_chi_and_regular_kong_exclude_red_and_joker():
    rules = get_rule_set('wuhan-huanghuang')
    rules.round_state.joker_tiles = ['s8']
    assert rules.chi_options(['m1', 'm2'], 'm3')
    assert rules.chi_options(['green', 'white'], 'red') == []
    assert rules.concealed_kongs(['red'] * 4 + ['s8'] * 4 + ['m1'] * 4) == ['m1']


def test_wuhan_pure_one_suit_and_peng_peng_include_exposed_melds():
    rules = get_rule_set('wuhan-huanghuang')
    rules.round_state.joker_tiles = ['white']
    hand = ['m1', 'm1', 'm1', 'm2', 'm2', 'm2', 'm3', 'm3', 'm3', 'm4', 'm4']
    chi = Meld(type='chi', tile='m5', tiles=['m5', 'm6', 'm7'])
    assert rules.is_pure_one_suit(hand, [chi])
    assert not rules.is_pure_one_suit(hand, [Meld(type='chi', tile='p5', tiles=['p5', 'p6', 'p7'])])

    triplets = ['m1', 'm1', 'm1', 'm2', 'm2', 'm2', 'p3', 'p3', 'p3', 'green', 'green']
    assert '碰碰胡' not in evaluate_wuhan_win(
        rules, triplets, exposed=1, exposed_melds=[Meld(type='chi', tile='s5', tiles=['s5', 's6', 's7'])], joker='white')
    assert '碰碰胡' in evaluate_wuhan_win(
        rules, triplets, exposed=1, exposed_melds=[Meld(type='peng', tile='s5', tiles=['s5'] * 3)], joker='white')


def test_wuhan_kongs_are_owned_and_special_kongs_need_a_receipt():
    players = [
        player(0, melds=[
            Meld(type='flower', tile='red', tiles=['red'], specialKong='red'),
            Meld(type='flower', tile='white', tiles=['white']),
        ]),
        player(1, melds=[Meld(type='gang', tile='m1', tiles=['m1'] * 4)]),
        player(2, melds=[Meld(type='angang', tile='p2', tiles=['p2'] * 4)]),
        player(3, melds=[Meld(type='flower', tile='white', tiles=['white'], specialKong='joker')]),
    ]
    assert wuhan_kong_kinds(players[0].melds, 'white') == ['red']
    assert wuhan_kong_kinds(players[3].melds, 'white') == ['joker']
    assert wuhan_settlement_kong_kinds(players, 0, 'white', False) == ['red']
    assert wuhan_kong_multiplier(wuhan_kong_kinds(players[2].melds, 'white')) == 4


def test_wuhan_scoring_matches_frontend_rules():
    assert wuhan_raw_win_points(['杠上开花'], True, False, ['red'], True) == 10
    assert wuhan_raw_win_points(['杠上开花'], True, False, ['joker'], True) == 20
    assert wuhan_raw_win_points(['杠上开花'], True, True, ['joker'], True) == 40
    assert wuhan_raw_win_points(['屁胡', '门前清'], True, False, []) == 6
    assert wuhan_discarder_multiplier(['碰碰胡'], True) == 1.2
    assert wuhan_discarder_multiplier(['屁胡'], True) == 2


def test_wuhan_settlement_uses_winner_and_each_payer_kongs_separately():
    rules = get_rule_set('wuhan-huanghuang')
    rules.round_state.joker_tiles = ['white']
    manager = GameManager(rule_set=rules)
    winner = player(0, ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'p1', 'p2', 'p3', 's1', 's2', 's3', 'green'], [
        Meld(type='flower', tile='red', tiles=['red'], specialKong='red'),
    ])
    discarder = player(1, melds=[])
    discarder.discards = ['green']
    manager.players = [
        winner,
        discarder,
        player(2, melds=[Meld(type='flower', tile='red', tiles=['red'], specialKong='red')]),
        player(3, melds=[Meld(type='flower', tile='red', tiles=['red'], specialKong='red')]),
    ]
    manager._table_context.players = manager.players
    manager.finalize_win(0, {'sourceFrom': 1, 'winTile': 'green', 'selfDraw': False})

    assert manager.result['payerPayments'] == [0, 8, 8, 8]
    assert manager.result['discarderMultiplier'] == 2
    assert manager.result['payerKongDetails'][2] == [{'label': '红中杠', 'multiplier': 2}]
    assert [p.score for p in manager.players] == [1024, 992, 992, 992]


def test_two_jokers_can_win_only_through_a_complete_kong_bloom_shape():
    rules = get_rule_set('wuhan-huanghuang')
    rules.round_state.joker_tiles = ['s8']
    manager = GameManager(rule_set=rules)
    hand = ['m1', 'm2', 'm3', 'p3', 'p3', 'p5', 'p6', 'p7', 'p9', 's8', 's8']
    manager.players = [player(0, hand, [
        Meld(type='angang', tile='m9', tiles=['m9'] * 4),
        Meld(type='flower', tile='s8', tiles=['s8'], specialKong='joker'),
    ]), player(1), player(2), player(3)]
    manager._table_context.players = manager.players

    assert manager._can_wuhan_win(0, hand, self_draw=True, kong_bloom=True)
    assert not manager._can_wuhan_win(0, hand, self_draw=True, kong_bloom=False)
