"""WakuDemo OAuth 2.0 Authorization Code + PKCE 服务端实现。"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


@dataclass(frozen=True)
class WakuDemoOAuthConfig:
    client_id: str
    base_url: str
    redirect_uri: str
    frontend_url: str
    scope: str = 'account.read'
    request_timeout_seconds: float = 10.0
    pending_ttl_seconds: int = 600
    fallback_token_ttl_seconds: int = 3600
    cookie_name: str = 'lgm_wakudemo_session'
    cookie_secure: bool = True
    cookie_samesite: str = 'none'

    @classmethod
    def from_env(cls) -> 'WakuDemoOAuthConfig':
        return cls(
            client_id=os.getenv('WAKUDEMO_CLIENT_ID', '').strip(),
            base_url=os.getenv('WAKUDEMO_BASE_URL', '').strip().rstrip('/'),
            redirect_uri=os.getenv(
                'WAKUDEMO_REDIRECT_URI',
                'https://www.bestguo.top:58000/api/login/callback',
            ).strip(),
            frontend_url=os.getenv(
                'WAKUDEMO_FRONTEND_URL',
                'https://lianhuaguangdongmahjong.guoguo-labs.online/',
            ).strip(),
            scope=os.getenv('WAKUDEMO_SCOPE', 'account.read').strip(),
            request_timeout_seconds=float(os.getenv('WAKUDEMO_TIMEOUT_SECONDS', '10')),
            pending_ttl_seconds=int(os.getenv('WAKUDEMO_PENDING_TTL_SECONDS', '600')),
            fallback_token_ttl_seconds=int(os.getenv('WAKUDEMO_TOKEN_TTL_SECONDS', '3600')),
            cookie_name=os.getenv('WAKUDEMO_COOKIE_NAME', 'lgm_wakudemo_session').strip(),
            cookie_secure=_env_bool('WAKUDEMO_COOKIE_SECURE', True),
            cookie_samesite=os.getenv('WAKUDEMO_COOKIE_SAMESITE', 'none').strip().lower(),
        )

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.base_url and self.redirect_uri)

    @property
    def authorize_url(self) -> str:
        return f'{self.base_url}/oauth/authorize'

    @property
    def token_url(self) -> str:
        return f'{self.base_url}/api/v1/integrations/wakudemo/auth/token'

    @property
    def account_url(self) -> str:
        return f'{self.base_url}/api/v1/integrations/wakudemo/auth/account'


@dataclass
class PendingAuthorization:
    state: str
    code_verifier: str
    expires_at: float


@dataclass
class AuthenticatedSession:
    access_token: str
    account: dict[str, Any]
    expires_at: float


class WakuDemoOAuthError(Exception):
    def __init__(self, code: str, status_code: int = 400):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def generate_code_verifier() -> str:
    """生成 RFC 7636 允许字符集内、长度 43-128 的高熵 verifier。"""
    return secrets.token_urlsafe(64)[:96]


def create_s256_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


class WakuDemoOAuthService:
    """服务端 OAuth 会话仓库。

    access token 只存在服务端内存，浏览器 Cookie 仅保存不可推导的随机 session id。
    多 worker 部署时应将三个字典替换为共享 Redis 存储，接口语义无需变化。
    """

    def __init__(self, config: WakuDemoOAuthConfig, client: httpx.AsyncClient | None = None):
        if config.cookie_samesite not in {'lax', 'strict', 'none'}:
            raise ValueError('WAKUDEMO_COOKIE_SAMESITE 必须是 lax、strict 或 none')
        if config.cookie_samesite == 'none' and not config.cookie_secure:
            raise ValueError('SameSite=None 的 Cookie 必须同时启用 Secure')
        self.config = config
        self._client = client
        self._pending: dict[str, PendingAuthorization] = {}
        self._sessions: dict[str, AuthenticatedSession] = {}
        self._used_codes: dict[str, float] = {}
        self._lock = threading.RLock()

    def ensure_configured(self) -> None:
        if not self.config.configured:
            raise WakuDemoOAuthError('oauth_not_configured', 503)

    def _cleanup(self, now: float) -> None:
        self._pending = {key: item for key, item in self._pending.items() if item.expires_at > now}
        self._sessions = {key: item for key, item in self._sessions.items() if item.expires_at > now}
        self._used_codes = {key: expiry for key, expiry in self._used_codes.items() if expiry > now}

    def begin(self, old_session_id: str | None = None) -> tuple[str, str]:
        self.ensure_configured()
        now = time.time()
        browser_session_id = old_session_id or secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        verifier = generate_code_verifier()
        with self._lock:
            self._cleanup(now)
            self._pending[browser_session_id] = PendingAuthorization(
                state=state,
                code_verifier=verifier,
                expires_at=now + self.config.pending_ttl_seconds,
            )
        query = urlencode({
            'client_id': self.config.client_id,
            'redirect_uri': self.config.redirect_uri,
            'response_type': 'code',
            'scope': self.config.scope,
            'state': state,
            'code_challenge': create_s256_challenge(verifier),
            'code_challenge_method': 'S256',
        })
        return f'{self.config.authorize_url}?{query}', browser_session_id

    def consume_pending(self, browser_session_id: str | None, state: str | None,
                        code: str | None) -> PendingAuthorization:
        now = time.time()
        code_hash = hashlib.sha256((code or '').encode()).hexdigest()
        with self._lock:
            self._cleanup(now)
            if code and code_hash in self._used_codes:
                raise WakuDemoOAuthError('authorization_code_already_used')
            pending = self._pending.pop(browser_session_id or '', None)
            if pending is None:
                raise WakuDemoOAuthError('authorization_request_expired')
            if not state or not secrets.compare_digest(pending.state, state):
                raise WakuDemoOAuthError('state_mismatch')
            if code:
                self._used_codes[code_hash] = now + self.config.pending_ttl_seconds
            return pending

    async def exchange_code(self, code: str, verifier: str) -> tuple[str, int, dict[str, Any]]:
        timeout = httpx.Timeout(self.config.request_timeout_seconds)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=timeout, trust_env=False)
        try:
            try:
                token_response = await client.post(self.config.token_url, data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'client_id': self.config.client_id,
                    'redirect_uri': self.config.redirect_uri,
                    'code_verifier': verifier,
                }, timeout=timeout)
            except httpx.TimeoutException as exc:
                raise WakuDemoOAuthError('token_endpoint_timeout', 504) from exc
            except httpx.HTTPError as exc:
                raise WakuDemoOAuthError('token_endpoint_unavailable', 502) from exc

            if token_response.status_code >= 400:
                raise WakuDemoOAuthError('pkce_or_authorization_code_rejected', 401)
            try:
                token_body = token_response.json()
            except ValueError as exc:
                raise WakuDemoOAuthError('invalid_token_response', 502) from exc
            access_token = token_body.get('access_token') if isinstance(token_body, dict) else None
            if not isinstance(access_token, str) or not access_token:
                raise WakuDemoOAuthError('missing_access_token', 502)
            try:
                expires_in = max(1, int(token_body.get(
                    'expires_in', self.config.fallback_token_ttl_seconds)))
            except (TypeError, ValueError):
                expires_in = self.config.fallback_token_ttl_seconds

            try:
                account_response = await client.get(
                    self.config.account_url,
                    headers={'Authorization': f'Bearer {access_token}'},
                    timeout=timeout,
                )
            except httpx.TimeoutException as exc:
                raise WakuDemoOAuthError('account_endpoint_timeout', 504) from exc
            except httpx.HTTPError as exc:
                raise WakuDemoOAuthError('account_endpoint_unavailable', 502) from exc
            if account_response.status_code in {401, 403}:
                raise WakuDemoOAuthError('access_token_rejected', 401)
            if account_response.status_code >= 400:
                raise WakuDemoOAuthError('account_endpoint_error', 502)
            try:
                account = account_response.json()
            except ValueError as exc:
                raise WakuDemoOAuthError('invalid_account_response', 502) from exc
            if not isinstance(account, dict):
                raise WakuDemoOAuthError('invalid_account_response', 502)
            return access_token, expires_in, account
        finally:
            if owns_client:
                await client.aclose()

    def create_session(self, access_token: str, expires_in: int,
                       account: dict[str, Any]) -> str:
        now = time.time()
        session_id = secrets.token_urlsafe(32)
        with self._lock:
            self._cleanup(now)
            self._sessions[session_id] = AuthenticatedSession(
                access_token=access_token,
                account=account,
                expires_at=now + expires_in,
            )
        return session_id

    def get_session(self, session_id: str | None) -> AuthenticatedSession | None:
        now = time.time()
        with self._lock:
            self._cleanup(now)
            return self._sessions.get(session_id or '')

    def delete_session(self, session_id: str | None) -> None:
        with self._lock:
            self._pending.pop(session_id or '', None)
            self._sessions.pop(session_id or '', None)


_service: WakuDemoOAuthService | None = None


def get_wakudemo_oauth_service() -> WakuDemoOAuthService:
    global _service
    if _service is None:
        _service = WakuDemoOAuthService(WakuDemoOAuthConfig.from_env())
    return _service
