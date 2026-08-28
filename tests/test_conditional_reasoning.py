from app.llm.conditional_reasoning import (
    ConditionalReasoningCoordinator,
    DEFAULT_CONDITIONAL_REASONING,
)


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
    assert coordinator.admit(request(), 5000)
    assert coordinator.admit(request(), 5000)
    assert not coordinator.admit(request(), 5000)
    assert not coordinator.admit(request(1), 4999)
