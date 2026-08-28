"""供应商/模型思考能力矩阵：实时麻将只允许可验证的非思考请求。"""

import re
from dataclasses import dataclass, field

PROVIDER_TYPES = (
    'deepseek', 'qwen', 'kimi', 'doubao', 'minimax', 'openai', 'glm', 'claude', 'custom',
)


@dataclass(frozen=True)
class ReasoningPolicy:
    provider_type: str
    mode: str
    message: str
    request_body: dict = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.mode in ('explicit-off', 'explicit-on', 'naturally-off')


def infer_provider_type(base_url: str, model: str, provider_id: str = '') -> str:
    source = f'{base_url} {model} {provider_id}'.lower()
    if 'deepseek' in source:
        return 'deepseek'
    if re.search(r'dashscope|\.maas\.aliyuncs|qwen|qwq', source):
        return 'qwen'
    if re.search(r'moonshot|kimi', source):
        return 'kimi'
    if re.search(r'volces|volcengine|doubao', source):
        return 'doubao'
    if 'minimax' in source:
        return 'minimax'
    if re.search(r'api\.openai\.com|\bgpt-|\bo[134](?:[.-]|\s|$)', source):
        return 'openai'
    if re.search(r'bigmodel|\bglm-', source):
        return 'glm'
    if re.search(r'anthropic|\bclaude', source):
        return 'claude'
    return 'custom'


def _policy(provider_type: str, mode: str, message: str,
            request_body: dict | None = None) -> ReasoningPolicy:
    return ReasoningPolicy(provider_type, mode, message, request_body or {})


def resolve_reasoning_policy(provider_type: str, base_url: str, model: str,
                             provider_id: str = '', reasoning: bool = False) -> ReasoningPolicy:
    kind = provider_type if provider_type in PROVIDER_TYPES else \
        infer_provider_type(base_url, model, provider_id)
    name = (model or '').strip().lower()

    if kind == 'deepseek':
        if re.search(r'reasoner|(^|[-_.])r1(?:[-_.]|$)', name):
            return _policy(kind, 'reasoning-only', 'DeepSeek Reasoner/R1 无法保证关闭思考')
        return _policy(kind, 'explicit-on', '已开启 DeepSeek 条件思考', {
            'thinking': {'type': 'enabled'}, 'reasoning_effort': 'medium',
        }) if reasoning else _policy(
            kind, 'explicit-off', '已强制关闭 DeepSeek 思考模式',
            {'thinking': {'type': 'disabled'}})
    if kind == 'qwen':
        if re.match(r'^(?:qwq|.*thinking)', name):
            return _policy(kind, 'reasoning-only', '该千问型号属于推理专用模型')
        if re.match(r'^qwen-?3\.(?:5|6|7|8)(?:[.-]|$)', name):
            return _policy(kind, 'explicit-on', '已开启千问条件思考', {
                'enable_thinking': True,
            }) if reasoning else _policy(
                kind, 'explicit-off', '已强制关闭千问思考模式',
                {'enable_thinking': False})
        return _policy(kind, 'unknown', '无法确认该千问型号是否支持非思考模式')
    if kind == 'kimi':
        if 'thinking' in name:
            return _policy(kind, 'reasoning-only', 'Kimi Thinking 型号无法关闭思考')
        if re.match(r'^kimi-k2[.-](?:5|6)(?:[.-]|$)', name):
            return _policy(kind, 'explicit-off', '已强制关闭 Kimi 思考模式', {
                'thinking': {'type': 'disabled'}, 'temperature': 0.6, 'top_p': 0.95,
            })
        if re.match(r'^(?:kimi-k2|moonshot-v1)', name):
            return _policy(kind, 'naturally-off', '该 Kimi 型号本身不输出思考链')
        return _policy(kind, 'unknown', '无法确认该 Kimi 型号是否支持非思考模式')
    if kind == 'doubao':
        if 'thinking' in name:
            return _policy(kind, 'explicit-off', '已强制关闭豆包思考模式',
                           {'thinking': {'type': 'disabled'}})
        if re.match(r'^doubao', name):
            return _policy(kind, 'naturally-off', '该豆包型号按非思考模型调用')
        return _policy(kind, 'unknown', '无法确认该豆包接入点是否支持非思考模式')
    if kind == 'minimax':
        if re.match(r'^minimax-m(?:1|2)(?:[.-]|$)', name):
            return _policy(kind, 'reasoning-only', 'MiniMax M1/M2 系列没有可靠关闭开关')
        if re.match(r'^minimax-(?:text|01)', name):
            return _policy(kind, 'naturally-off', '该 MiniMax 型号本身不输出思考链')
        return _policy(kind, 'unknown', '无法确认该 MiniMax 型号是否支持非思考模式')
    if kind == 'openai':
        if re.match(r'^o(?:1|3|4)(?:[.-]|$)', name):
            return _policy(kind, 'reasoning-only', 'OpenAI o 系列属于推理模型')
        if re.match(r'^gpt-5(?:[.-]|$)', name):
            return _policy(kind, 'explicit-on', '已开启 GPT 条件思考', {
                'reasoning_effort': 'medium',
            }) if reasoning else _policy(
                kind, 'explicit-off', '已将 GPT 推理强度设为 none',
                {'reasoning_effort': 'none'})
        if re.match(r'^(?:gpt-4|gpt-3\.5)', name):
            return _policy(kind, 'naturally-off', '该 GPT 型号本身不是推理模型')
        return _policy(kind, 'unknown', '无法确认该 OpenAI 型号是否能关闭推理')
    if kind == 'glm':
        if 'thinking' in name:
            return _policy(kind, 'reasoning-only', '显式 Thinking 型号不用于实时麻将决策')
        if re.match(r'^glm-(?:4\.(?:5|6|7)|5)(?:[.-]|$)', name):
            return _policy(kind, 'explicit-off', '已强制关闭 GLM 思考模式',
                           {'thinking': {'type': 'disabled'}})
        if re.match(r'^glm-4(?:[.-]|$)', name):
            return _policy(kind, 'naturally-off', '该 GLM 型号本身不是思考模型')
        return _policy(kind, 'unknown', '无法确认该 GLM 型号是否支持非思考模式')
    if kind == 'claude':
        return _policy(kind, 'naturally-off', 'Claude 扩展思考未显式开启')
    return _policy(kind, 'unknown', '自定义协议无法验证非思考模式')
