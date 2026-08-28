"""动作一致台词与前端 decisionSpeech.test.ts 对照。"""

from app.llm.decision_speech import (
    DECISION_SPEECH_LINES, decision_speech, resolve_decision_speech,
)


def test_all_action_style_lines_are_short():
    for styles in DECISION_SPEECH_LINES.values():
        for variants in styles.values():
            assert variants
            assert all(len(line) <= 16 for line in variants)


def test_discard_lines_never_say_keep_and_rotate_stably():
    for variants in DECISION_SPEECH_LINES['discard'].values():
        assert all(not any(term in line for term in ('留着', '保留', '不打'))
                   for line in variants)
    action = {'kind': 'discard', 'handIndex': 0}
    assert decision_speech(action, '稳健', 0) == '这张先走。'
    assert decision_speech(action, '稳健', 3) == '这张先走。'


def test_steady_style_never_uses_reduplicated_steady_wording():
    for styles in DECISION_SPEECH_LINES.values():
        assert all('稳稳' not in line for line in styles['稳健'])


def test_bluff_and_steady_reduplication_are_kept_but_backstage_terms_fallback():
    action = {'kind': 'discard', 'handIndex': 0}
    assert resolve_decision_speech('这张留着。', action, '稳健') == '这张留着。'
    assert resolve_decision_speech('稳稳出牌。', action, '稳健') == '稳稳出牌。'
    assert resolve_decision_speech('按候选A1来。', action, '话痨') == '先把这张放出去。'


def test_strategy_bluff_is_allowed_but_public_dealer_identity_must_be_true():
    action = {'kind': 'discard', 'handIndex': 0}
    assert resolve_decision_speech('这张留着。', action, '激进', facts={'isDealer': False}) == '这张留着。'
    assert resolve_decision_speech('我就是庄家！', action, '激进', facts={'isDealer': False}) == '这张不要了。'
    assert resolve_decision_speech('庄家就是我！', action, '激进', facts={'isDealer': True}) == '庄家就是我！'
    assert resolve_decision_speech('我不是庄家。', action, '稳健', facts={'isDealer': True}) == '这张先走。'


def test_public_action_commitment_must_match_final_choice():
    discard = {'kind': 'discard', 'handIndex': 0}
    assert resolve_decision_speech('这牌我吃定了！', discard, '激进') == '这张不要了。'
    assert resolve_decision_speech('这牌我吃定了！', {'kind': 'chi', 'optionIndex': 0}, '激进') == '这牌我吃定了！'


def test_other_players_public_actions_and_current_discard_must_be_true():
    discard = {'kind': 'discard', 'handIndex': 0}
    facts = {
        'publicMeldTypes': {'上家': [], '对家': [], '下家': ['peng']},
        'currentDiscard': {'from': '上家', 'tile': '7万'},
    }
    assert resolve_decision_speech(
        '下家杠了，我稳一手。', discard, '稳健', facts=facts) == '这张先走。'
    assert resolve_decision_speech(
        '下家碰了，我稳一手。', discard, '稳健', facts=facts) == '下家碰了，我稳一手。'
    assert resolve_decision_speech(
        '下家打出7万。', discard, '稳健', facts=facts) == '这张先走。'
    assert resolve_decision_speech(
        '上家打出7万，这张留着。', discard, '稳健', facts=facts) == '上家打出7万，这张留着。'
