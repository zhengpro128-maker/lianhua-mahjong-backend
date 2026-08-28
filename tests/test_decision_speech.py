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


def test_bluff_is_kept_but_steady_reduplication_and_backstage_terms_fallback():
    action = {'kind': 'discard', 'handIndex': 0}
    assert resolve_decision_speech('这张留着。', action, '稳健') == '这张留着。'
    assert resolve_decision_speech('稳稳出牌。', action, '稳健') == '这张先走。'
    assert resolve_decision_speech('按候选A1来。', action, '话痨') == '先把这张放出去。'
