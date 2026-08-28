from app.llm.conditional_reasoning import (
    ConditionalReasoningCoordinator,
DEFAULT_CONDITIONAL_REASONING,
)


def test_all_supported_reasoning_providers_share_40_second_budget():
    assert DEFAULT_CONDITIONAL_REASONING.deadline_ms == 40_000
    assert DEFAULT_CONDITIONAL_REASONING.min_remaining_budget_ms == 45_000


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


def test_close_candidates_obey_round_and_time_budgets():
    coordinator = ConditionalReasoningCoordinator(
        DEFAULT_CONDITIONAL_REASONING, random_fn=lambda: 1)
    assert coordinator.admit(request(), 45_000)
    assert coordinator.admit(request(), 45_000)
    assert not coordinator.admit(request(), 45_000)
    assert not coordinator.admit(request(1), 44_999)
