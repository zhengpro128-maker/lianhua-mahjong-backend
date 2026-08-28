from app.llm.conditional_reasoning import (
    ConditionalReasoningCoordinator,
DEFAULT_CONDITIONAL_REASONING,
)


def test_all_supported_reasoning_providers_share_40_second_budget():
    assert DEFAULT_CONDITIONAL_REASONING.deadline_ms == 40_000
    assert DEFAULT_CONDITIONAL_REASONING.min_remaining_budget_ms == 45_000
    assert DEFAULT_CONDITIONAL_REASONING.max_per_seat_per_round == 2
    assert DEFAULT_CONDITIONAL_REASONING.max_per_match == 24


def request(round_index=0):
    features = {
        'shanten': 1, 'ukeire': 4, 'effectiveTiles': [], 'ready': False,
        'waits': 'n/a', 'effectiveRemaining': 'n/a', 'specialPattern': 'none',
        'safety': '中', 'efficiency': '中', 'risks': [],
    }
    empty = {'discards': [], 'melds': []}
    return {
        'ruleCode': 'lotus-legacy',
        'candidates': [
            {'id': 'A1', 'action': {'kind': 'discard'}, 'features': features},
            {'id': 'A2', 'action': {'kind': 'discard'}, 'features': features},
        ],
        'state': {
            'roundIndex': round_index, 'wallCount': 40,
            'scores': [1000, 2000, 3000, 4000],
            'snapshots': {
                'self': empty, 'upper': empty, 'opposite': empty, 'lower': empty,
            },
        },
    }


def test_close_candidates_have_independent_per_seat_round_budgets():
    coordinator = ConditionalReasoningCoordinator(
        DEFAULT_CONDITIONAL_REASONING, random_fn=lambda: 1)
    assert coordinator.admit(request(), 1, 45_000)
    assert coordinator.admit(request(), 1, 45_000)
    assert not coordinator.admit(request(), 1, 45_000)
    assert coordinator.admit(request(), 2, 45_000)
    assert not coordinator.admit(request(1), 1, 44_999)


def test_opening_ignores_close_candidates_and_audit_but_keeps_strong_triggers():
    opening = request()
    opening['state']['turnOrigin'] = 'opening'
    coordinator = ConditionalReasoningCoordinator(
        DEFAULT_CONDITIONAL_REASONING, random_fn=lambda: 0)
    assert not coordinator.admit(opening, 1, 45_000)

    opening['candidates'][0]['features']['scoreDelta'] = 800
    assert coordinator.admit(opening, 1, 45_000)


def test_match_budget_is_shared_and_capped_at_24():
    coordinator = ConditionalReasoningCoordinator(
        DEFAULT_CONDITIONAL_REASONING, random_fn=lambda: 1)
    for round_index in range(4):
        for seat in (1, 2, 3):
            assert coordinator.admit(request(round_index), seat, 45_000)
            assert coordinator.admit(request(round_index), seat, 45_000)
    assert not coordinator.admit(request(4), 1, 45_000)
