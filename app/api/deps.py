"""联机接口统一登录鉴权依赖。

WakuDemo 登录后，联机身份一律由服务端从登录会话推导（uid），不再信任客户端
提交的 playerId / guestId。身份键统一为 ``wakudemo-<uid>``，避免与历史匿名
guestId 冲突；封禁、防占房、战绩与头像都绑定该键。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException, Request

from app.auth.wakudemo import get_wakudemo_oauth_service

AUTH_REQUIRED = 'AUTH_REQUIRED'


@dataclass(frozen=True)
class AuthenticatedUser:
    """从 WakuDemo 会话推导出的联机玩家身份。"""

    uid: str
    display_name: Optional[str]
    avatar_url: Optional[str]

    @property
    def player_id(self) -> str:
        return f'wakudemo-{self.uid}'


def require_wakudemo_login(request: Request) -> AuthenticatedUser:
    """联机接口鉴权：无有效登录会话 → 401 AUTH_REQUIRED。"""
    service = get_wakudemo_oauth_service()
    session = service.get_session(request.cookies.get(service.config.cookie_name))
    if session is None:
        raise HTTPException(status_code=401, detail={'code': AUTH_REQUIRED})
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
