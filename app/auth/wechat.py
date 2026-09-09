"""微信小游戏登录：code2Session 交换与短期 Bearer 登录态。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx
from dotenv import load_dotenv
from loguru import logger


@dataclass(frozen=True)
class WechatAuthConfig:
    app_id: str
    app_secret: str
    token_secret: str
    token_ttl_seconds: int = 7 * 24 * 60 * 60
    request_timeout_seconds: float = 10.0
    login_rate_limit_per_minute: int = 30

    @classmethod
    def from_env(cls) -> 'WechatAuthConfig':
        load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=False)
        try:
            token_ttl = int(os.getenv('WECHAT_TOKEN_TTL_SECONDS', '604800'))
        except ValueError:
            token_ttl = -1
        try:
            timeout = float(os.getenv('WECHAT_AUTH_TIMEOUT_SECONDS', '10'))
        except ValueError:
            timeout = -1.0
        try:
            rate_limit = int(os.getenv('WECHAT_LOGIN_RATE_LIMIT_PER_MINUTE', '30'))
        except ValueError:
            rate_limit = -1
        return cls(
            app_id=os.getenv('WECHAT_APP_ID', '').strip(),
            app_secret=os.getenv('WECHAT_APP_SECRET', '').strip(),
            token_secret=os.getenv('WECHAT_TOKEN_SECRET', '').strip(),
            token_ttl_seconds=token_ttl,
            request_timeout_seconds=timeout,
            login_rate_limit_per_minute=rate_limit,
        )

    @property
    def configured(self) -> bool:
        return (
            bool(self.app_id)
            and bool(self.app_secret)
            and len(self.token_secret.encode('utf-8')) >= 32
            and 300 <= self.token_ttl_seconds <= 30 * 24 * 60 * 60
            and math.isfinite(self.request_timeout_seconds)
            and 0 < self.request_timeout_seconds <= 60
            and self.login_rate_limit_per_minute >= 0
        )


@dataclass(frozen=True)
class WechatIdentity:
    uid: str
    access_token: str
    expires_at: int

    @property
    def player_id(self) -> str:
        return f'wechat-{self.uid}'


class WechatAuthError(Exception):
    def __init__(self, code: str, status_code: int = 400):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64decode(value: str) -> bytes:
    padding = '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


class WechatAuthService:
    """不保存 session_key；只签发服务自身可验证的短期访问令牌。"""

    code2session_url = 'https://api.weixin.qq.com/sns/jscode2session'

    def __init__(self, config: WechatAuthConfig,
                 client: httpx.AsyncClient | None = None,
                 clock: Callable[[], float] = time.time):
        self.config = config
        self._client = client
        self._clock = clock
        self._login_attempts: dict[str, deque[float]] = {}
        self._lock = threading.RLock()

    def ensure_configured(self) -> None:
        if not self.config.configured:
            raise WechatAuthError('WECHAT_AUTH_NOT_CONFIGURED', 503)

    def _stable_uid(self, openid: str) -> str:
        source = f'{self.config.app_id}:{openid}'.encode('utf-8')
        return hashlib.sha256(source).hexdigest()[:32]

    def _check_login_rate_limit(self, client_key: str | None) -> None:
        limit = self.config.login_rate_limit_per_minute
        if not client_key or limit == 0:
            return
        now = self._clock()
        cutoff = now - 60
        with self._lock:
            attempts = self._login_attempts.setdefault(client_key, deque())
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= limit:
                raise WechatAuthError('WECHAT_AUTH_RATE_LIMITED', 429)
            attempts.append(now)

    def _signature(self, signing_input: str) -> str:
        digest = hmac.new(
            self.config.token_secret.encode('utf-8'),
            signing_input.encode('ascii'),
            hashlib.sha256,
        ).digest()
        return _b64encode(digest)

    def issue_access_token(self, uid: str) -> tuple[str, int]:
        now = int(self._clock())
        expires_at = now + self.config.token_ttl_seconds
        payload = _b64encode(json.dumps({
            'v': 1,
            'iss': 'lianhua-guangma',
            'aud': self.config.app_id,
            'sub': uid,
            'iat': now,
            'exp': expires_at,
        }, separators=(',', ':'), sort_keys=True).encode('utf-8'))
        signing_input = f'v1.{payload}'
        return f'{signing_input}.{self._signature(signing_input)}', expires_at

    def verify_access_token(self, token: str) -> WechatIdentity | None:
        if not self.config.configured or len(token) > 2048:
            return None
        try:
            version, payload_encoded, signature = token.split('.')
            if version != 'v1':
                return None
            signing_input = f'{version}.{payload_encoded}'
            if not hmac.compare_digest(signature, self._signature(signing_input)):
                return None
            payload = json.loads(_b64decode(payload_encoded))
            now = int(self._clock())
            if not isinstance(payload, dict) or payload.get('v') != 1:
                return None
            if payload.get('iss') != 'lianhua-guangma' \
                    or payload.get('aud') != self.config.app_id:
                return None
            uid = payload.get('sub')
            issued_at = payload.get('iat')
            expires_at = payload.get('exp')
            if not isinstance(uid, str) or len(uid) != 32:
                return None
            if not isinstance(issued_at, int) or not isinstance(expires_at, int):
                return None
            if issued_at > now + 60 or expires_at <= now \
                    or expires_at - issued_at > self.config.token_ttl_seconds:
                return None
            return WechatIdentity(uid=uid, access_token=token, expires_at=expires_at)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    async def exchange_code(self, code: str,
                            client_key: str | None = None) -> WechatIdentity:
        self.ensure_configured()
        normalized_code = code.strip()
        if not normalized_code or len(normalized_code) > 256:
            raise WechatAuthError('WECHAT_CODE_INVALID', 400)
        self._check_login_rate_limit(client_key)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.config.request_timeout_seconds)
        try:
            try:
                response = await client.get(self.code2session_url, params={
                    'appid': self.config.app_id,
                    'secret': self.config.app_secret,
                    'js_code': normalized_code,
                    'grant_type': 'authorization_code',
                }, follow_redirects=False)
            except httpx.TimeoutException as exc:
                raise WechatAuthError('WECHAT_AUTH_TIMEOUT', 504) from exc
            except httpx.HTTPError as exc:
                raise WechatAuthError('WECHAT_AUTH_UNAVAILABLE', 502) from exc
            if response.status_code != 200:
                logger.warning('微信 code2Session HTTP 错误: status={}', response.status_code)
                raise WechatAuthError('WECHAT_AUTH_UNAVAILABLE', 502)
            try:
                payload: Any = response.json()
            except ValueError as exc:
                raise WechatAuthError('WECHAT_AUTH_INVALID_RESPONSE', 502) from exc
            if not isinstance(payload, dict):
                raise WechatAuthError('WECHAT_AUTH_INVALID_RESPONSE', 502)
            error_code = payload.get('errcode')
            if error_code not in (None, 0):
                logger.warning('微信 code2Session 拒绝请求: errcode={}', error_code)
                if error_code in {40029, 40163}:
                    raise WechatAuthError('WECHAT_CODE_INVALID', 401)
                if error_code == 45011:
                    raise WechatAuthError('WECHAT_AUTH_RATE_LIMITED', 429)
                raise WechatAuthError('WECHAT_AUTH_REJECTED', 502)
            openid = payload.get('openid')
            if not isinstance(openid, str) or not openid or len(openid) > 128:
                raise WechatAuthError('WECHAT_AUTH_INVALID_RESPONSE', 502)
            uid = self._stable_uid(openid)
            token, expires_at = self.issue_access_token(uid)
            return WechatIdentity(uid=uid, access_token=token, expires_at=expires_at)
        finally:
            if owns_client:
                await client.aclose()


_service: WechatAuthService | None = None


def get_wechat_auth_service() -> WechatAuthService:
    global _service
    if _service is None:
        _service = WechatAuthService(WechatAuthConfig.from_env())
    return _service
