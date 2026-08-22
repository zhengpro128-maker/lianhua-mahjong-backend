"""服务端 LLM 配置 —— 环境变量（§9.2）。LLM_ENABLED 默认关，测试/冒烟脚本零影响。"""

import asyncio
import os
from typing import Optional

ENABLED_KEY = 'LLM_ENABLED'
BASE_URL_KEY = 'LLM_API_BASE'
API_KEY_KEY = 'LLM_API_KEY'
MODEL_KEY = 'LLM_MODEL'
TIMEOUT_S_KEY = 'LLM_TIMEOUT_S'
POOL_TIMEOUT_S_KEY = 'LLM_POOL_TIMEOUT_S'
STYLE_KEY = 'LLM_STYLE'
CONCURRENCY_KEY = 'LLM_CONCURRENCY'
MAX_PER_ROOM_KEY = 'LLM_MAX_REQUESTS_PER_ROOM'


class LlmServerConfig:
    def __init__(self,
                 enabled: bool = False,
                 base_url: str = '',
                 api_key: str = '',
                 model: str = '',
                 style: str = '稳健',
                 timeout_s: float = 8.0,
                 pool_timeout_s: float = 1.0,
                 concurrency: int = 4,
                 max_requests_per_room: int = 0,
                 ):
        self.enabled = enabled
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.style = style if style in ('激进', '稳健', '话痨', '高冷') else '稳健'
        self.timeout_s = timeout_s
        self.pool_timeout_s = pool_timeout_s
        self.concurrency = concurrency
        self.max_requests_per_room = max_requests_per_room


def load_llm_config() -> LlmServerConfig:
    """从环境变量读取（每次调用重新读，部署后可热改；值很小无性能顾虑）。"""
    return LlmServerConfig(
        enabled=os.environ.get(ENABLED_KEY, 'false').strip().lower() == 'true',
        base_url=os.environ.get(BASE_URL_KEY, '').strip(),
        api_key=os.environ.get(API_KEY_KEY, '').strip(),
        model=os.environ.get(MODEL_KEY, '').strip(),
        style=os.environ.get(STYLE_KEY, '稳健').strip(),
        timeout_s=float(os.environ.get(TIMEOUT_S_KEY, '8')),
        pool_timeout_s=float(os.environ.get(POOL_TIMEOUT_S_KEY, '1')),
        concurrency=max(1, int(os.environ.get(CONCURRENCY_KEY, '4'))),
        max_requests_per_room=max(0, int(os.environ.get(MAX_PER_ROOM_KEY, '0'))),
    )


def llm_server_available(cfg: Optional[LlmServerConfig] = None) -> bool:
    """服务端 LLM 能力：启用且 Base URL / Key / 模型齐全（§9.3 llmAvailable）。"""
    cfg = cfg or load_llm_config()
    return bool(cfg.enabled and cfg.base_url and cfg.api_key and cfg.model)


# ── 每座位 LLM 配置（联机空位自带配置；key 仅会话内存，不落库/日志/响应）──────

_STYLES = ('激进', '稳健', '话痨', '高冷')


def seat_config_from(entry: dict) -> Optional[LlmServerConfig]:
    """从座位配置条目（{baseUrl, apiKey, model, style, timeoutMs}）构建单座配置。

    字段非法 → None（调用方回退启发式 AI）；style 非法归一为稳健；
    timeoutMs（毫秒）折算为 timeout_s 并限制在 0.5..120s。
    """
    base_url = (entry.get('baseUrl') or '').strip()
    api_key = (entry.get('apiKey') or '').strip()
    model = (entry.get('model') or '').strip()
    style = (entry.get('style') or '稳健').strip()
    timeout_ms = entry.get('timeoutMs') or None
    timeout_s = 8.0
    if timeout_ms:
        try:
            timeout_s = float(timeout_ms) / 1000.0
        except (TypeError, ValueError):
            timeout_s = 8.0
    if not (base_url and api_key and model):
        return None
    return LlmServerConfig(
        enabled=True,
        base_url=base_url,
        api_key=api_key,
        model=model,
        style=style if style in _STYLES else '稳健',
        timeout_s=max(0.5, min(timeout_s, 120.0)),
    )


def valid_seat_entry(entry: dict) -> bool:
    """座位配置条目是否完整合法（baseUrl 可规范化 & key/model 非空）——用于开局前校验。"""
    from app.llm.client import _normalize_endpoint  # 延迟导入避免循环依赖
    base_url = (entry.get('baseUrl') or '').strip()
    return bool(base_url and entry.get('apiKey') and entry.get('model')
                and _normalize_endpoint(base_url) is not None)


# 并发信号量（单进程语义，§9.6）；懒创建共享实例。
_semaphore: Optional[asyncio.Semaphore] = None


def llm_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(load_llm_config().concurrency)
    return _semaphore
