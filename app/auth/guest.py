"""Production-safe anonymous browser sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

COOKIE_NAME = 'mahjong_guest_session'
MAX_AGE = 60 * 60 * 24 * 180


def _secret() -> bytes:
    value = os.getenv('GUEST_SESSION_SECRET', '').strip()
    if len(value) < 32:
        raise RuntimeError('GUEST_SESSION_SECRET must contain at least 32 characters')
    return value.encode()


def issue_guest_token() -> tuple[str, str]:
    uid = secrets.token_urlsafe(18)
    payload = json.dumps({'uid': uid, 'exp': int(time.time()) + MAX_AGE}, separators=(',', ':')).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b'=')
    signature = hmac.new(_secret(), encoded, hashlib.sha256).digest()
    return f'{encoded.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b"=").decode()}', uid


def verify_guest_token(token: str | None) -> str | None:
    if not token or '.' not in token:
        return None
    encoded, supplied = token.split('.', 1)
    try:
        expected = hmac.new(_secret(), encoded.encode(), hashlib.sha256).digest()
        signature = base64.urlsafe_b64decode(supplied + '=' * (-len(supplied) % 4))
        if not hmac.compare_digest(expected, signature):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        uid = payload.get('uid')
        return uid if isinstance(uid, str) and uid and int(payload.get('exp', 0)) > time.time() else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
