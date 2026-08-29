from app.llm.conditional_reasoning import (
    ConditionalReasoningCoordinator,
    DEFAULT_CONDITIONAL_REASONING,
    evaluate_reasoning_triggers,
)


def test_all_supported_reasoning_providers_share_40_second_budget():
    assert DEFAULT_CONDITIONAL_REASONING.deadline_ms == 40_000
    assert DEFAULT_CONDITIONAL_REASONING.min_remaining_budget_ms == 45_000
    assert DEFAULT_CONDITIONAL_REASONING.max_per_seat_per_round == 2
    assert DEFAULT_CONDITIONAL_REASONING.max_soft_per_seat_per_round == 1
    assert DEFAULT_CONDITIONAL_REASONING.max_per_match == 24
    assert DEFAULT_CONDITIONAL_REASONING.trigger.early_opponent_threat == 90


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
            {'id': 'A1', 'action': {'kind': 'discard'},
             'features': {**features, 'efficiency': '优'}},
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


def test_soft_close_candidates_use_one_slot_and_reserve_one_for_strong_trigger():
    coordinator = ConditionalReasoningCoordinator(
        DEFAULT_CONDITIONAL_REASONING, random_fn=lambda: 1)
    assert coordinator.admit(request(), 1, 45_000)
    assert not coordinator.admit(request(), 1, 45_000)
    assert coordinator.admit(request(), 2, 45_000)
    strong = request()
    strong['candidates'][0]['features']['scoreDelta'] = 800
    assert coordinator.admit(strong, 1, 45_000)
    assert not coordinator.admit(strong, 1, 45_000)
    assert not coordinator.admit(request(1), 1, 44_999)


def test_opening_and_early_round_ignore_soft_triggers_but_keep_strong_triggers():
    opening = request()
    opening['state']['turnOrigin'] = 'opening'
    opening['state']['earlyRound'] = True
    coordinator = ConditionalReasoningCoordinator(
        DEFAULT_CONDITIONAL_REASONING, random_fn=lambda: 0)
    assert not coordinator.admit(opening, 1, 45_000)

    opening['candidates'][0]['features']['scoreDelta'] = 800
    assert coordinator.admit(opening, 1, 45_000)

    early = request()
    early['state']['turnOrigin'] = 'draw'
    early['state']['earlyRound'] = True
    reasons = evaluate_reasoning_triggers(early, random_fn=lambda: 0)
    assert 'close-candidates' not in reasons
    assert 'audit' not in reasons


def test_identical_zero_gap_candidates_are_not_a_hard_choice():
    tied = request()
    tied['candidates'][0]['features'] = dict(tied['candidates'][1]['features'])
    assert evaluate_reasoning_triggers(tied, random_fn=lambda: 1) == set()


def test_early_round_allows_distinct_ready_choices_and_ready_break_risk():
    ready = request()
    ready['state']['earlyRound'] = True
    ready['candidates'][0]['features'].update({
        'ready': True, 'waits': [{'tile': '3万', 'remaining': 2}],
        'effectiveRemaining': 2,
    })
    assert evaluate_reasoning_triggers(ready, random_fn=lambda: 1) == set()

    ready['candidates'][1]['features'].update({
        'ready': True, 'waits': [{'tile': '6筒', 'remaining': 3}],
        'effectiveRemaining': 3,
    })
    assert 'ready-choice' in evaluate_reasoning_triggers(ready, random_fn=lambda: 1)

    risky = request()
    risky['state']['earlyRound'] = True
    risky['candidates'][0]['features']['risks'] = ['碰/杠可能破坏听牌']
    assert 'ready-choice' in evaluate_reasoning_triggers(risky, random_fn=lambda: 1)


def test_early_opponent_threat_requires_three_strong_exposed_melds():
    value = request()
    value['state']['earlyRound'] = True
    meld = {'type': 'peng', 'tile': '2万', 'tiles': ['2万', '2万', '2万']}
    value['state']['snapshots']['upper'] = {
        'discards': ['东风'], 'melds': [meld, meld],
    }
    assert 'opponent-threat' not in evaluate_reasoning_triggers(value, random_fn=lambda: 1)
    value['state']['snapshots']['upper']['melds'].append(meld)
    assert 'opponent-threat' in evaluate_reasoning_triggers(value, random_fn=lambda: 1)


def test_match_budget_is_shared_and_capped_at_24():
    coordinator = ConditionalReasoningCoordinator(
        DEFAULT_CONDITIONAL_REASONING, random_fn=lambda: 1)
    for round_index in range(4):
        for seat in (1, 2, 3):
            strong = request(round_index)
            strong['candidates'][0]['features']['scoreDelta'] = 800
            assert coordinator.admit(strong, seat, 45_000)
            assert coordinator.admit(strong, seat, 45_000)
    assert not coordinator.admit(request(4), 1, 45_000)
