"""微信房间邀请票据。

票据无需落库，可由多位好友在有效期内使用；签名同时绑定房间码，
不能被篡改后用于其他房间。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


@dataclass(frozen=True)
class RoomInviteConfig:
    secret: str
    ttl_seconds: int = 15 * 60

    @classmethod
    def from_env(cls) -> 'RoomInviteConfig':
        load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=False)
        try:
            ttl = int(os.getenv('ROOM_INVITE_TTL_SECONDS', '900'))
        except ValueError:
            ttl = -1
        # 默认与微信访问令牌共享随机密钥，通过消息域隔离避免两类签名互用。
        secret = os.getenv('ROOM_INVITE_SECRET', '').strip() \
            or os.getenv('WECHAT_TOKEN_SECRET', '').strip()
        return cls(secret=secret, ttl_seconds=ttl)

    @property
    def configured(self) -> bool:
        return (
            len(self.secret.encode('utf-8')) >= 32
            and isinstance(self.ttl_seconds, int)
            and math.isfinite(self.ttl_seconds)
            and 60 <= self.ttl_seconds <= 24 * 60 * 60
        )


@dataclass(frozen=True)
class RoomInviteClaims:
    room_id: str
    expires_at: int


class RoomInviteError(Exception):
    def __init__(self, code: str, status_code: int):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class RoomInviteService:
    def __init__(self, config: RoomInviteConfig,
                 clock: Callable[[], float] = time.time):
        self.config = config
        self._clock = clock

    def _ensure_configured(self) -> None:
        if not self.config.configured:
            raise RoomInviteError('ROOM_INVITE_NOT_CONFIGURED', 503)

    def _signature(self, signing_input: str) -> str:
        digest = hmac.new(
            self.config.secret.encode('utf-8'),
            f'room-invite:{signing_input}'.encode('ascii'),
            hashlib.sha256,
        ).digest()
        return _b64encode(digest)

    def issue(self, room_id: str, issuer_player_id: str) -> tuple[str, int]:
        self._ensure_configured()
        now = int(self._clock())
        expires_at = now + self.config.ttl_seconds
        payload = _b64encode(json.dumps({
            'v': 1,
            'room': room_id,
            'iat': now,
            'exp': expires_at,
            'nonce': secrets.token_urlsafe(12),
            # 只保留不可逆摘要便于日志追踪，不把玩家身份放进分享参数。
            'by': hashlib.sha256(issuer_player_id.encode('utf-8')).hexdigest()[:16],
        }, separators=(',', ':'), sort_keys=True).encode('utf-8'))
        signing_input = f'ri1.{payload}'
        return f'{signing_input}.{self._signature(signing_input)}', expires_at

    def verify(self, ticket: str, expected_room_id: str) -> RoomInviteClaims:
        self._ensure_configured()
        if not ticket or len(ticket) > 1024:
            raise RoomInviteError('ROOM_INVITE_INVALID', 400)
        try:
            version, payload_encoded, signature = ticket.split('.')
            if version != 'ri1':
                raise ValueError('invalid version')
            signing_input = f'{version}.{payload_encoded}'
            if not hmac.compare_digest(signature, self._signature(signing_input)):
                raise ValueError('invalid signature')
            payload = json.loads(_b64decode(payload_encoded))
            if not isinstance(payload, dict) or payload.get('v') != 1:
                raise ValueError('invalid payload')
            room_id = payload.get('room')
            issued_at = payload.get('iat')
            expires_at = payload.get('exp')
            if room_id != expected_room_id:
                raise ValueError('room mismatch')
            if not isinstance(issued_at, int) or not isinstance(expires_at, int):
                raise ValueError('invalid timestamps')
            now = int(self._clock())
            if expires_at <= now:
                raise RoomInviteError('ROOM_INVITE_EXPIRED', 410)
            if issued_at > now + 60 \
                    or expires_at - issued_at > self.config.ttl_seconds:
                raise ValueError('invalid lifetime')
            return RoomInviteClaims(room_id=room_id, expires_at=expires_at)
        except RoomInviteError:
            raise
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
            raise RoomInviteError('ROOM_INVITE_INVALID', 400) from exc


_service: RoomInviteService | None = None


def get_room_invite_service() -> RoomInviteService:
    global _service
    if _service is None:
        _service = RoomInviteService(RoomInviteConfig.from_env())
    return _service
