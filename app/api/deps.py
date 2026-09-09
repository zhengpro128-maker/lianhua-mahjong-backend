"""联机接口统一登录鉴权依赖。

支持 WakuDemo Cookie 与微信小游戏 Bearer token；身份一律由服务端推导，
不再信任客户端提交的 playerId / guestId。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException, Request

from app.auth.wakudemo import get_wakudemo_oauth_service
from app.auth.wechat import get_wechat_auth_service

AUTH_REQUIRED = 'AUTH_REQUIRED'


@dataclass(frozen=True)
class AuthenticatedUser:
    """从可信登录态推导出的联机玩家身份。"""

    uid: str
    display_name: Optional[str]
    avatar_url: Optional[str]
    provider: str = 'wakudemo'

    @property
    def player_id(self) -> str:
        return f'{self.provider}-{self.uid}'


async def require_wakudemo_login(request: Request) -> AuthenticatedUser:
    """兼容旧名称的统一联机鉴权：无有效登录态 → 401 AUTH_REQUIRED。

    本地开发（WAKUDEMO_LOGIN_BYPASS=true）且无真实会话时，按客户端提交的
    playerId 推导测试身份（非法/缺失时用 dev-bypass），跳过 OAuth 流程。
    """
    authorization = request.headers.get('authorization', '')
    scheme, _, bearer = authorization.partition(' ')
    if scheme.lower() == 'bearer' and bearer:
        identity = get_wechat_auth_service().verify_access_token(bearer.strip())
        if identity is not None:
            return AuthenticatedUser(
                uid=identity.uid,
                display_name=None,
                avatar_url=None,
                provider='wechat',
            )
        raise HTTPException(status_code=401, detail={'code': AUTH_REQUIRED})

    service = get_wakudemo_oauth_service()
    session = service.get_session(request.cookies.get(service.config.cookie_name))
    if session is not None:
        account: dict[str, Any] = session.account or {}
        uid = account.get('id')
        if not isinstance(uid, str) or not uid:
            raise HTTPException(status_code=401, detail={'code': AUTH_REQUIRED})
        display_name = account.get('displayName')
        avatar_url = account.get('avatarUrl')
        return AuthenticatedUser(
            uid=uid,
            display_name=display_name if isinstance(display_name, str) else None,
            avatar_url=avatar_url if isinstance(avatar_url, str) else None,
        )
    if service.config.login_bypass:
        return AuthenticatedUser(
            uid=await _bypass_uid(request),
            display_name='本地开发账号',
            avatar_url=None,
        )
    raise HTTPException(status_code=401, detail={'code': AUTH_REQUIRED})


async def _bypass_uid(request: Request) -> str:
    """开发旁路身份：优先客户端 playerId（保持多开/多身份测试体验），否则固定值。"""
    try:
        payload = await request.json()
    except Exception:
        return 'dev-bypass'
    if isinstance(payload, dict):
        candidate = payload.get('playerId')
        if isinstance(candidate, str) and candidate.strip() \
                and len(candidate) <= 64 \
                and all(ord(ch) < 128 for ch in candidate):
            return candidate.strip()
    return 'dev-bypass'
