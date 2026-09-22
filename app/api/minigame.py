"""WeChat Mini Game login. App secret and session_key never leave the server."""
import base64
import hashlib
import hmac
import json
import os
import time

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix='/api/minigame', tags=['minigame'])


def secret():
    value = os.getenv('WECHAT_SESSION_SECRET', '')
    if len(value) < 32:
        raise HTTPException(503, detail={'code': 'WECHAT_NOT_CONFIGURED'})
    return value.encode()


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def issue(account):
    payload = encode(json.dumps({**account, 'exp': int(time.time()) + 86400 * 7}).encode())
    return payload + '.' + encode(hmac.new(secret(), payload.encode(), hashlib.sha256).digest())


def verify(token):
    if not token or len(token) > 8192:
        return None
    try:
        payload, signature = token.split('.')
        expected = encode(hmac.new(secret(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        if data['exp'] > time.time() and isinstance(data['id'], str) and data['id']:
            return data
    except (ValueError, KeyError, TypeError, HTTPException):
        pass
    return None


class Profile(BaseModel):
    nickname: str = Field(default='微信玩家', min_length=1, max_length=20)
    avatarUrl: str = Field(default='', max_length=2048)


class Login(BaseModel):
    code: str = Field(min_length=1, max_length=256)
    profile: Profile = Field(default_factory=Profile)


@router.post('/login')
async def login(body: Login):
    appid, appsecret = os.getenv('WECHAT_APP_ID'), os.getenv('WECHAT_APP_SECRET')
    secret()
    if not appid or not appsecret:
        raise HTTPException(503, detail={'code': 'WECHAT_NOT_CONFIGURED'})
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get('https://api.weixin.qq.com/sns/jscode2session', params={
                'appid': appid, 'secret': appsecret, 'js_code': body.code, 'grant_type': 'authorization_code'})
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, detail={'code': 'WECHAT_UNAVAILABLE'}) from None
    if data.get('errcode') or not isinstance(data.get('openid'), str) or not data['openid']:
        raise HTTPException(401, detail={'code': 'WECHAT_LOGIN_FAILED'})
    avatar = body.profile.avatarUrl
    account = {'id': data['openid'], 'displayName': body.profile.nickname,
               'avatarUrl': avatar if avatar.startswith('https://') else ''}
    return {'sessionToken': issue(account), 'account': account}
