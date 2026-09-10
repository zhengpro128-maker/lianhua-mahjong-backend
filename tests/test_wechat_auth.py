import httpx
import pytest
from fastapi import Depends, FastAPI

from app.api import deps
from app.api import wechat_auth as wechat_auth_api
from app.auth.wechat import (
    WechatAuthConfig,
    WechatAuthError,
    WechatAuthService,
)


def config(**overrides) -> WechatAuthConfig:
    values = {
        'app_id': 'wx-app-id',
        'app_secret': 'wx-app-secret',
        'token_secret': 't' * 32,
        'code2session_url': 'https://api.weixin.qq.com/sns/jscode2session',
        'token_ttl_seconds': 3600,
        'request_timeout_seconds': 5,
        'login_rate_limit_per_minute': 30,
    }
    values.update(overrides)
    return WechatAuthConfig(**values)


@pytest.mark.asyncio
async def test_exchange_code_and_verify_token_without_exposing_openid_or_session_key():
    captured = {}

    def handler(request: httpx.Request):
        captured['query'] = dict(request.url.params)
        return httpx.Response(200, json={
            'openid': 'raw-open-id',
            'session_key': 'must-not-leave-server',
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = WechatAuthService(config(), client=client, clock=lambda: 1000)
        identity = await service.exchange_code('one-time-code')

    assert captured['query'] == {
        'appid': 'wx-app-id',
        'secret': 'wx-app-secret',
        'js_code': 'one-time-code',
        'grant_type': 'authorization_code',
    }
    assert identity.player_id.startswith('wechat-')
    assert 'raw-open-id' not in identity.access_token
    assert 'must-not-leave-server' not in identity.access_token
    assert service.verify_access_token(identity.access_token) == identity


@pytest.mark.asyncio
async def test_exchange_code_uses_configured_cloud_hosting_endpoint():
    captured = {}

    def handler(request: httpx.Request):
        captured['url'] = str(request.url).split('?')[0]
        return httpx.Response(200, json={'openid': 'openid'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = WechatAuthService(config(
            code2session_url='http://api.weixin.qq.com/sns/jscode2session',
        ), client=client)
        await service.exchange_code('one-time-code')

    assert captured['url'] == 'http://api.weixin.qq.com/sns/jscode2session'


def test_access_token_rejects_tampering_expiry_and_wrong_audience():
    now = [1000]
    service = WechatAuthService(config(), clock=lambda: now[0])
    token, _ = service.issue_access_token('a' * 32)
    assert service.verify_access_token(token) is not None
    assert service.verify_access_token(token + 'x') is None

    now[0] = 4600
    assert service.verify_access_token(token) is None
    other_app = WechatAuthService(config(app_id='another-app'), clock=lambda: 1000)
    assert other_app.verify_access_token(token) is None


@pytest.mark.asyncio
async def test_exchange_code_bypasses_environment_proxy(monkeypatch):
    created = {}
    original_client = httpx.AsyncClient

    def handler(_: httpx.Request):
        return httpx.Response(200, json={'openid': 'openid'})

    def create_client(*args, **kwargs):
        created.update(kwargs)
        return original_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr('app.auth.wechat.httpx.AsyncClient', create_client)
    await WechatAuthService(config()).exchange_code('one-time-code')

    assert created['trust_env'] is False


@pytest.mark.asyncio
async def test_code2session_errors_are_mapped_to_stable_codes():
    async def exchange(payload):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
        ) as client:
            return await WechatAuthService(config(), client=client).exchange_code('bad-code')

    with pytest.raises(WechatAuthError) as exc:
        await exchange({'errcode': 40029, 'errmsg': 'invalid code'})
    assert exc.value.code == 'WECHAT_CODE_INVALID'
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_login_exchange_is_rate_limited_before_calling_wechat():
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: (
            calls.append(request) or httpx.Response(200, json={'openid': 'openid'})
        )),
    ) as client:
        service = WechatAuthService(
            config(login_rate_limit_per_minute=1), client=client, clock=lambda: 1000,
        )
        await service.exchange_code('first', client_key='127.0.0.1')
        with pytest.raises(WechatAuthError) as exc:
            await service.exchange_code('second', client_key='127.0.0.1')

    assert exc.value.code == 'WECHAT_AUTH_RATE_LIMITED'
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_login_endpoint_returns_no_store_bearer_session(monkeypatch):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            'openid': 'openid', 'session_key': 'secret-session-key',
        })),
    ) as wechat_client:
        service = WechatAuthService(config(), client=wechat_client, clock=lambda: 1000)
        monkeypatch.setattr(wechat_auth_api, 'get_wechat_auth_service', lambda: service)
        app = FastAPI()
        app.include_router(wechat_auth_api.router)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test',
        ) as client:
            response = await client.post('/api/auth/wechat', json={'code': 'wx-code'})

    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['playerId'].startswith('wechat-')
    assert response.json()['expiresAt'] == 4600
    assert 'openid' not in response.text
    assert 'session-key' not in response.text


@pytest.mark.asyncio
async def test_unified_dependency_accepts_wechat_bearer(monkeypatch):
    service = WechatAuthService(config(), clock=lambda: 1000)
    token, _ = service.issue_access_token('b' * 32)
    monkeypatch.setattr(deps, 'get_wechat_auth_service', lambda: service)

    app = FastAPI()

    @app.get('/protected')
    async def protected(user=Depends(deps.require_wakudemo_login)):
        return {'playerId': user.player_id, 'provider': user.provider}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://test',
    ) as client:
        response = await client.get('/protected', headers={
            'Authorization': f'Bearer {token}',
        })
        rejected = await client.get('/protected', headers={
            'Authorization': 'Bearer invalid',
        })

    assert response.json() == {'playerId': f'wechat-{"b" * 32}', 'provider': 'wechat'}
    assert rejected.status_code == 401
    assert rejected.json()['detail']['code'] == 'AUTH_REQUIRED'
