from app.llm.speech_policy import LlmSpeechPolicy, compact_speech_text


def test_global_and_style_cooldowns_are_deterministic():
    current = [10.0]
    policy = LlmSpeechPolicy(lambda: current[0])
    assert policy.admit(1, '话痨') is True
    current[0] += 3.9
    assert policy.admit(2, '话痨') is False
    current[0] += 0.1
    assert policy.admit(2, '话痨') is True
    current[0] += 8.0
    assert policy.admit(1, '高冷') is False
    assert policy.admit(1, '高冷', 'important') is True


def test_compact_speech_keeps_first_short_sentence():
    assert compact_speech_text('  先稳住这一手。后面不用念。 ') == '先稳住这一手。'
    assert len(compact_speech_text('这是一句明显超过十六个汉字的超长牌桌吐槽文本')) == 16
