"""动作一致台词与前端 decisionSpeech.test.ts 对照。"""

from app.llm.decision_speech import DECISION_SPEECH_LINES, decision_speech


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
