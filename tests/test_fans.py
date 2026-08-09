"""注册式番型引擎与当前广麻计分兼容性。"""

import pytest

from app.core.rules import score_hand
from app.rules.fans import FanContext, FanEngine, PredicateFan
from app.rules.lianhua import get_default_rule_set


def test_fan_engine_keeps_registration_order_and_sums_components():
    engine = FanEngine([
        PredicateFan('base', '基础', lambda _: True, multiplier=2),
        PredicateFan('extra', '附加', lambda _: True, points=lambda _: 30,
                     equivalent_multiplier=lambda _: 3),
    ], base_score=10)

    result = engine.evaluate(FanContext())

    assert [hit.code for hit in result.hits] == ['base', 'extra']
    assert result.multiplier == 2
    assert result.additive_points == 30
    assert result.total_multiplier == 5
    assert result.points == 50


def test_fan_engine_rejects_duplicate_codes():
    with pytest.raises(ValueError, match='duplicate fan codes: same'):
        FanEngine([
            PredicateFan('same', 'A', lambda _: True),
            PredicateFan('same', 'B', lambda _: True),
        ], base_score=100)


def test_fan_engine_applies_one_way_override():
    engine = FanEngine([
        PredicateFan('small', '小番', lambda _: True, multiplier=2),
        PredicateFan('large', '大番', lambda _: True, multiplier=4,
                     suppresses=frozenset({'small'})),
    ], base_score=100)

    result = engine.evaluate(FanContext())

    assert [hit.code for hit in result.hits] == ['large']
    assert result.multiplier == 4
    assert result.points == 400


def test_lianhua_rule_set_preserves_full_legacy_score_shape():
    context = FanContext(
        dealer=True,
        no_joker=True,
        four_red=True,
        kong_bloom=True,
        horse_hits=2,
        robbed_kong=True,
    )
    expected = {
        'multiplier': 32,
        'totalMultiplier': 34,
        'horsePoints': 200,
        'points': 3400,
        'details': [
            {'label': '抢杠胡', 'multiplier': 1},
            {'label': '庄家', 'multiplier': 2},
            {'label': '无癞子', 'multiplier': 2},
            {'label': '四红中', 'multiplier': 4},
            {'label': '杠上开花', 'multiplier': 2},
            {'label': '中马 2 张', 'points': 200},
        ],
    }

    assert get_default_rule_set().score_hand(context) == expected
    assert score_hand(
        dealer=True, no_joker=True, four_red=True, kong_bloom=True,
        horse_hits=2, robbed_kong=True,
    ) == expected
