from app.rules.registry import get_rule_set
from app.rules.wuhan import joker_for
from app.models.game import Meld


def test_wuhan_registry_and_wall():
    rules = get_rule_set('wuhan-huanghuang')
    assert len(rules.create_wall()) == 120
    assert set(rules.create_wall()) == {
        *(f'{s}{n}' for s in 'mps' for n in range(1, 10)), 'red', 'green', 'white'
    }


def test_wuhan_joker_mapping():
    assert joker_for('m9') == 'm1'
    assert joker_for('green') == 'white'
    assert joker_for('white') == 'green'
    assert joker_for('red') == 'green'


def test_wuhan_standard_win_with_joker():
    rules = get_rule_set('wuhan-huanghuang')
    rules.round_state.joker_tiles = ['white']
    hand = ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'p1', 'p2', 'p3',
            's1', 's2', 'white', 'green', 'green']
    assert rules.is_winning_hand(hand)


def test_wuhan_chi_only_number_tiles():
    rules = get_rule_set('wuhan-huanghuang')
    assert rules.chi_options(['m1', 'm2'], 'm3')
    assert rules.chi_options(['green', 'white'], 'red') == []


def test_wuhan_red_is_not_an_automatic_flower_draw():
    rules = get_rule_set('wuhan-huanghuang')
    # 红中和翻出的癞子都应留到玩家选择杠/出牌时处理。
    assert rules.is_flower_tile('red') is False
    rules.round_state.joker_tiles = ['m2']
    assert rules.is_flower_tile('m2') is False


def test_wuhan_pure_one_suit_includes_exposed_melds():
    rules = get_rule_set('wuhan-huanghuang')
    rules.round_state.joker_tiles = ['white']
    hand = ['m1', 'm1', 'm1', 'm2', 'm2', 'm2', 'm3', 'm3', 'm3', 'm4', 'm4']
    assert rules.is_pure_one_suit(hand, [Meld(type='chi', tile='m5', tiles=['m5', 'm6', 'm7'])])
    assert not rules.is_pure_one_suit(hand, [Meld(type='chi', tile='p5', tiles=['p5', 'p6', 'p7'])])
