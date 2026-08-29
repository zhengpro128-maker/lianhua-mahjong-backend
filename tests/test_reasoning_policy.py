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


@pytest.mark.parametrize(('provider_type', 'model'), [
    ('deepseek', 'deepseek-reasoner'), ('qwen', 'qwq-plus'),
    ('kimi', 'kimi-k2-thinking'), ('minimax', 'MiniMax-M2.7'),
    ('openai', 'o3-mini'), ('glm', 'glm-4.1v-thinking-flash'),
])
def test_reasoning_only_models_are_identified_without_request_precheck(provider_type, model):
    result = resolve_reasoning_policy(provider_type, 'https://proxy.example.com/v1', model)
    assert result.mode == 'reasoning-only'


def test_glm_5_3_flash_custom_proxy_disables_quick_and_enables_medium_reasoning():
    result = resolve_reasoning_policy(
        'custom', 'https://api.orcarouter.ai/v1', 'z-ai/glm-5.3-flash')
    assert result.provider_type == 'glm'
    assert result.mode == 'explicit-off'
    assert result.request_body == {'reasoning_effort': 'none'}
    assert resolve_reasoning_policy(
        'custom', 'https://api.orcarouter.ai/v1', 'z-ai/glm-5.3-flash',
        reasoning=True).request_body == {
            'reasoning_effort': 'medium'}


def test_full_glm_5_3_remains_always_on():
    quick = resolve_reasoning_policy(
        'glm', 'https://api.orcarouter.ai/v1', 'z-ai/glm-5.3')
    deep = resolve_reasoning_policy(
        'glm', 'https://api.orcarouter.ai/v1', 'z-ai/glm-5.3', reasoning=True)
    assert quick.mode == 'always-on'
    assert quick.request_body == {
        'thinking': {'type': 'enabled'}, 'reasoning_effort': 'low'}
    assert deep.request_body == {
        'thinking': {'type': 'enabled'}, 'reasoning_effort': 'medium'}


def test_kimi_k3_qualified_model_uses_fixed_sampling_parameters():
    result = resolve_reasoning_policy(
        'kimi', 'https://api.orcarouter.ai/v1', 'kimi/kimi-k3')
    assert result.provider_type == 'kimi'
    assert result.mode == 'always-on'
    assert result.request_body == {
        'temperature': 1.0, 'top_p': 0.95, 'reasoning_effort': 'low'}
    assert resolve_reasoning_policy(
        'kimi', 'https://api.orcarouter.ai/v1', 'kimi/kimi-k3',
        reasoning=True).request_body == {
            'temperature': 1.0, 'top_p': 0.95, 'reasoning_effort': 'high'}


@pytest.mark.parametrize('model', ['kimi/kimi-k2.5', 'kimi/kimi-k2.6'])
def test_kimi_k2_switchable_qualified_models_keep_non_reasoning_parameters(model):
    result = resolve_reasoning_policy(
        'kimi', 'https://api.orcarouter.ai/v1', model)
    assert result.provider_type == 'kimi'
    assert result.mode == 'explicit-off'
    assert result.accept_reasoning_response
    assert result.request_body == {
        'thinking': {'type': 'disabled'}, 'temperature': 0.6, 'top_p': 0.95}
    enabled = resolve_reasoning_policy(
        'kimi', 'https://api.orcarouter.ai/v1', model, reasoning=True)
    assert enabled.mode == 'explicit-on'
    assert enabled.request_body == {
        'thinking': {'type': 'enabled'}, 'temperature': 1.0, 'top_p': 0.95}


def test_claude_sonnet_5_disables_default_thinking_then_enables_medium_adaptive():
    quick = resolve_reasoning_policy(
        'custom', 'https://api.orcarouter.ai/v1', 'anthropic/claude-sonnet-5')
    deep = resolve_reasoning_policy(
        'custom', 'https://api.orcarouter.ai/v1', 'anthropic/claude-sonnet-5',
        reasoning=True)
    assert quick.request_body == {'thinking': {'type': 'disabled'}}
    assert deep.request_body == {
        'thinking': {'type': 'adaptive', 'display': 'summarized'},
        'output_config': {'effort': 'medium'},
    }


def test_kimi_k2_base_alias_remains_naturally_non_reasoning():
    result = resolve_reasoning_policy(
        'kimi', 'https://proxy.example.com/v1', 'kimi/kimi-k2')
    assert result.mode == 'naturally-off'
    assert result.request_body == {}


@pytest.mark.parametrize(('provider_type', 'model', 'expected'), [
    ('deepseek', 'deepseek-v4-flash', {
        'thinking': {'type': 'enabled'}, 'reasoning_effort': 'medium'}),
    ('qwen', 'qwen3.8-flash', {'enable_thinking': True}),
    ('openai', 'gpt-5.6-sol', {'reasoning_effort': 'medium'}),
])
def test_conditional_reasoning_explicitly_enables_supported_models(
        provider_type, model, expected):
    result = resolve_reasoning_policy(
        provider_type, 'https://proxy.example.com/v1', model, reasoning=True)
    assert result.mode == 'explicit-on'
    assert result.request_body == expected


def test_legacy_provider_type_inference():
    assert infer_provider_type('https://dashscope.aliyuncs.com/compatible-mode/v1', 'qwen3.7-plus') == 'qwen'
    assert infer_provider_type('https://proxy.local/v1', 'kimi-k2.6') == 'kimi'
    assert infer_provider_type('https://api.example.com/v1', 'mystery-model') == 'custom'
    assert resolve_reasoning_policy('qwen', 'https://proxy.local/v1', 'qwen-plus').mode == 'unknown'
