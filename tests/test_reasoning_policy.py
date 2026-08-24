import pytest

from app.llm.reasoning import infer_provider_type, resolve_reasoning_policy


@pytest.mark.parametrize(('provider_type', 'model', 'expected'), [
    ('deepseek', 'deepseek-v4-flash', {'thinking': {'type': 'disabled'}}),
    ('qwen', 'qwen3.7-plus', {'enable_thinking': False}),
    ('kimi', 'kimi-k2.6', {'thinking': {'type': 'disabled'}, 'temperature': 0.6, 'top_p': 0.95}),
    ('doubao', 'doubao-1.5-thinking-pro', {'thinking': {'type': 'disabled'}}),
    ('openai', 'gpt-5.6', {'reasoning_effort': 'none'}),
    ('glm', 'glm-4.7-flash', {'thinking': {'type': 'disabled'}}),
])
def test_switchable_models_force_non_reasoning(provider_type, model, expected):
    result = resolve_reasoning_policy(provider_type, 'https://proxy.example.com/v1', model)
    assert result.mode == 'explicit-off'
    assert result.request_body == expected
    assert result.usable


@pytest.mark.parametrize(('provider_type', 'model'), [
    ('deepseek', 'deepseek-reasoner'), ('qwen', 'qwq-plus'),
    ('kimi', 'kimi-k2-thinking'), ('minimax', 'MiniMax-M2.7'),
    ('openai', 'o3-mini'), ('glm', 'glm-4.1v-thinking-flash'),
])
def test_reasoning_only_models_are_rejected(provider_type, model):
    result = resolve_reasoning_policy(provider_type, 'https://proxy.example.com/v1', model)
    assert result.mode == 'reasoning-only'
    assert not result.usable


def test_legacy_provider_type_inference():
    assert infer_provider_type('https://dashscope.aliyuncs.com/compatible-mode/v1', 'qwen3.7-plus') == 'qwen'
    assert infer_provider_type('https://proxy.local/v1', 'kimi-k2.6') == 'kimi'
    assert infer_provider_type('https://api.example.com/v1', 'mystery-model') == 'custom'
    assert resolve_reasoning_policy('qwen', 'https://proxy.local/v1', 'qwen-plus').mode == 'unknown'
