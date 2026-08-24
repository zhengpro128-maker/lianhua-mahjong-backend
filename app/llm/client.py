"""OpenAI 兼容客户端 —— §7.2/§7.3/§10（异步 httpx，共享实例，总预算）。

仅语义错误（JSON 解析失败 / choice 不在白名单）允许一次反馈重试；
超时 / 网络 / HTTP 错误直接抛错（由调用方回退启发式）。
"""

import asyncio
import json
import re
import time
from typing import Optional

import httpx
from loguru import logger

from app.llm.config import LlmServerConfig, llm_semaphore
from app.llm.reasoning import resolve_reasoning_policy


class LlmClientError(Exception):
    KIND_HTTP = 'http'
    KIND_TIMEOUT = 'timeout'
    KIND_NETWORK = 'network'
    KIND_PARSE = 'parse'
    KIND_CONFIG = 'config'
    KIND_REASONING = 'reasoning'

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# 共享 client：进程级单例，应用退出时显式关闭（§9.6）。
_shared: Optional[httpx.AsyncClient] = None


def get_llm_client() -> httpx.AsyncClient:
    global _shared
    if _shared is None:
        _shared = httpx.AsyncClient(timeout=httpx.Timeout(60.0), limits=httpx.Limits(max_connections=8))
    return _shared


async def close_llm_client() -> None:
    global _shared
    if _shared is not None:
        await _shared.aclose()
        _shared = None


def _is_anthropic(base_url: str) -> bool:
    return re.match(r'^https://api\.anthropic\.com', base_url.strip(), re.I) is not None


def _normalize_endpoint(base_url: str) -> Optional[str]:
    """规范化：拒绝 userinfo / 非 https（localhost 除外）；只能追加一次 /chat/completions。"""
    value = base_url.strip()
    if not value:
        return None
    if re.match(r'^[a-z][a-z0-9+.-]*://[^/@]*@', value, re.I):
        return None
    m = re.match(r'^(https?)://([^/]+)(/.*)?$', value)
    if not m:
        return None
    host = m.group(2).rsplit(':', 1)[0]  # 去掉端口再比对 localhost 白名单
    if host not in ('localhost', '127.0.0.1') and m.group(1) != 'https':
        return None
    trimmed = value.rstrip('/')
    return trimmed if trimmed.endswith('/chat/completions') else trimmed + '/chat/completions'


def extract_json_object(text: str) -> Optional[str]:
    """平衡括号扫描：必须跳过字符串字面量内的 { } 与转义（§7.2）。"""
    start = text.find('{')
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escaped:
                escaped = False
                continue
            if ch == '\\':
                escaped = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _clean_message(text: str) -> str:
    import unicodedata
    cleaned = ''.join(ch for ch in text if not (unicodedata.category(ch).startswith('C')))
    return cleaned[:30].strip()


def parse_llm_output(raw: str, candidate_ids: list[str]) -> tuple[str, str]:
    """解析 LLM 回复 → (choice, message)；任意失败抛 parse 错误（触发一次语义重试）。"""
    text = (raw or '').strip()
    if not text:
        raise LlmClientError(LlmClientError.KIND_PARSE, '回复为空')
    text = re.sub(r'^```[a-zA-Z]*\s*', '', text)
    text = re.sub(r'```\s*$', '', text)
    obj = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            obj = parsed
    except ValueError:
        extracted = extract_json_object(text)
        if extracted is not None:
            try:
                parsed = json.loads(extracted)
                if isinstance(parsed, dict):
                    obj = parsed
            except ValueError:
                obj = None
    if not obj:
        raise LlmClientError(LlmClientError.KIND_PARSE, '未找到有效 JSON 对象')
    choice = obj.get('choice')
    if not isinstance(choice, str) or not choice:
        raise LlmClientError(LlmClientError.KIND_PARSE, 'choice 缺失或非字符串')
    if choice not in candidate_ids:
        raise LlmClientError(LlmClientError.KIND_PARSE, f'choice "{choice}" 不在合法候选列表')
    message = _clean_message(obj['message']) if isinstance(obj.get('message'), str) else ''
    return choice, message


async def _call_once(cfg: LlmServerConfig, system: str, user: str,
                     budget_s: float, max_tokens: int = 64,
                     strict_length: bool = True, attempt_no: int = 1) -> str:
    endpoint = _normalize_endpoint(cfg.base_url)
    if endpoint is None:
        raise LlmClientError(LlmClientError.KIND_PARSE, 'baseUrl 非法（userinfo 或协议不支持）')
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {cfg.api_key}',
    }
    if _is_anthropic(cfg.base_url):
        headers['anthropic-dangerous-direct-browser-access'] = 'true'
    payload = {
        'model': cfg.model,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ],
        'temperature': 0.4,
        'max_tokens': max_tokens,
        'top_p': 1,
        'stream': False,
        'n': 1,
    }
    reasoning_policy = resolve_reasoning_policy(
        getattr(cfg, 'provider_type', ''), cfg.base_url, cfg.model,
        getattr(cfg, 'provider_id', ''))
    if not reasoning_policy.usable:
        raise LlmClientError(LlmClientError.KIND_CONFIG, reasoning_policy.message)
    payload.update(reasoning_policy.request_body)
    if reasoning_policy.provider_type == 'qwen':
        payload['response_format'] = {'type': 'json_object'}
    client = get_llm_client()
    request_started = time.monotonic()
    try:
        async with llm_semaphore():
            response = await client.post(
                endpoint, headers=headers, json=payload, timeout=budget_s)
    except httpx.TimeoutException as exc:
        elapsed_ms = round((time.monotonic() - request_started) * 1000, 1)
        logger.bind(
            llm_provider=cfg.provider_id or 'default', llm_model=cfg.model,
            llm_attempt=attempt_no,
            llm_elapsed_ms=elapsed_ms,
            llm_error='timeout',
        ).warning(
            f'LLM 请求失败 provider={cfg.provider_id or "default"} model={cfg.model} '
            f'attempt={attempt_no} elapsed={elapsed_ms}ms kind=timeout')
        raise LlmClientError(LlmClientError.KIND_TIMEOUT, '请求超时') from exc
    except httpx.HTTPError as exc:
        elapsed_ms = round((time.monotonic() - request_started) * 1000, 1)
        logger.bind(
            llm_provider=cfg.provider_id or 'default', llm_model=cfg.model,
            llm_attempt=attempt_no,
            llm_elapsed_ms=elapsed_ms,
            llm_error='network',
        ).warning(
            f'LLM 请求失败 provider={cfg.provider_id or "default"} model={cfg.model} '
            f'attempt={attempt_no} elapsed={elapsed_ms}ms kind=network')
        raise LlmClientError(LlmClientError.KIND_NETWORK, f'网络错误: {exc}') from exc
    if response.status_code != 200:
        elapsed_ms = round((time.monotonic() - request_started) * 1000, 1)
        logger.bind(
            llm_provider=cfg.provider_id or 'default', llm_model=cfg.model,
            llm_attempt=attempt_no,
            llm_elapsed_ms=elapsed_ms,
            llm_http_status=response.status_code, llm_error='http',
        ).warning(
            f'LLM 请求失败 provider={cfg.provider_id or "default"} model={cfg.model} '
            f'attempt={attempt_no} elapsed={elapsed_ms}ms '
            f'kind=http status={response.status_code}')
        detail = response.text[:200]
        raise LlmClientError(LlmClientError.KIND_HTTP, f'HTTP {response.status_code}: {detail}')
    try:
        body = response.json()
    except ValueError as exc:
        raise LlmClientError(LlmClientError.KIND_PARSE, 'API 响应非 JSON') from exc
    choices = body.get('choices') or []
    if not choices:
        raise LlmClientError(LlmClientError.KIND_PARSE, 'API 响应格式无效或无内容')
    first = choices[0]
    message_obj = first.get('message') or {}
    message = message_obj.get('content')
    if not isinstance(message, str) or not message:
        raise LlmClientError(LlmClientError.KIND_PARSE, 'API 响应格式无效或无内容')
    if first.get('finish_reason') == 'length' and strict_length:
        raise LlmClientError(LlmClientError.KIND_PARSE, 'finish_reason=length（输出被截断）')
    usage = body.get('usage') if isinstance(body.get('usage'), dict) else {}
    details = usage.get('completion_tokens_details') \
        if isinstance(usage.get('completion_tokens_details'), dict) else {}
    reasoning_content = message_obj.get('reasoning_content')
    reasoning_tokens = details.get('reasoning_tokens')
    leaked_reasoning = isinstance(reasoning_content, str) and bool(reasoning_content.strip())
    leaked_reasoning = leaked_reasoning or isinstance(reasoning_tokens, (int, float)) and reasoning_tokens > 0
    leaked_reasoning = leaked_reasoning or isinstance(body.get('reasoning'), (str, list)) and bool(body.get('reasoning'))
    if leaked_reasoning:
        raise LlmClientError(
            LlmClientError.KIND_REASONING,
            '供应商仍返回思考内容，非思考模式验证失败')
    elapsed_ms = round((time.monotonic() - request_started) * 1000, 1)
    logger.bind(
        llm_provider=cfg.provider_id or 'default', llm_model=cfg.model,
        llm_attempt=attempt_no,
        llm_elapsed_ms=elapsed_ms,
        llm_finish_reason=first.get('finish_reason'),
        llm_completion_tokens=usage.get('completion_tokens'),
        llm_reasoning_tokens=details.get('reasoning_tokens'),
    ).info(
        f'LLM 请求完成 provider={cfg.provider_id or "default"} model={cfg.model} '
        f'attempt={attempt_no} elapsed={elapsed_ms}ms '
        f'finish={first.get("finish_reason")} '
        f'completionTokens={usage.get("completion_tokens")} '
        f'reasoningTokens={details.get("reasoning_tokens")}')
    return message


def _feedback_retry(user: str, error: str, legal_ids: list[str]) -> str:
    return user + '\n\n【上次你选错了】\n' + error + '\n' \
        + f'合法候选编号（只能从中选择）：{"、".join(legal_ids)}\n请重新输出一个合法编号。\n'


async def request_llm_decision(cfg: LlmServerConfig, system: str, user: str,
                               candidate_ids: list[str]) -> tuple[str, str]:
    """一次决策请求：总预算 cfg.timeout_s（含并发排队+一次语义重试）。"""
    started = time.monotonic()
    error_for_retry: Optional[str] = None

    async def attempt(messages_http_user: str) -> tuple[str, str]:
        left = cfg.timeout_s - (time.monotonic() - started)
        if left <= 0:
            raise LlmClientError(LlmClientError.KIND_TIMEOUT, '总预算耗尽')
        raw = await _call_once(
            cfg, system, messages_http_user, budget_s=left,
            attempt_no=2 if error_for_retry is not None else 1)
        try:
            return parse_llm_output(raw, candidate_ids)
        except LlmClientError as exc:
            if exc.kind == LlmClientError.KIND_PARSE and error_for_retry is None:
                pass  # 交给外层做一次语义重试
            raise

    try:
        return await attempt(user)
    except LlmClientError as exc:
        if exc.kind != LlmClientError.KIND_PARSE or error_for_retry is not None:
            raise
        error_for_retry = exc.args[0] if exc.args else '解析失败'
        logger.bind(
            llm_provider=cfg.provider_id or 'default', llm_model=cfg.model,
            llm_retry_reason=error_for_retry,
        ).info(
            f'LLM 语义重试 provider={cfg.provider_id or "default"} '
            f'model={cfg.model} reason={error_for_retry}')
        return await attempt(_feedback_retry(user, error_for_retry, candidate_ids))
