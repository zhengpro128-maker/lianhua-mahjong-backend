"""WakuDemo OAuth 2.0 + PKCE 集成测试（外部接口全部 Mock）。"""

import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from app.api import auth as auth_api
from app.auth import wakudemo as oauth_module
from app.auth.wakudemo import (
    WakuDemoOAuthConfig,
    WakuDemoOAuthError,
    WakuDemoOAuthService,
    create_s256_challenge,
)


def config() -> WakuDemoOAuthConfig:
    return WakuDemoOAuthConfig(
        client_id='client-under-review',
        base_url='https://waku.example',
        redirect_uri='https://www.bestguo.top:58000/api/login/callback',
        frontend_url='http://game.example/',
        cookie_secure=False,
        cookie_samesite='lax',
    )


def test_begin_builds_s256_authorization_request():
    service = WakuDemoOAuthService(config())
    location, session_id = service.begin()
    query = parse_qs(urlsplit(location).query)

    assert urlsplit(location).path == '/oauth/authorize'
    assert query['client_id'] == ['client-under-review']
    assert query['redirect_uri'] == ['https://www.bestguo.top:58000/api/login/callback']
    assert query['response_type'] == ['code']
    assert query['scope'] == ['account.read']
    assert query['code_challenge_method'] == ['S256']
    assert re.fullmatch(r'[A-Za-z0-9_-]{43,128}', query['code_challenge'][0])

    pending = service.consume_pending(session_id, query['state'][0], 'one-time-code')
    assert query['code_challenge'][0] == create_s256_challenge(pending.code_verifier)


def test_state_mismatch_and_reused_code_are_rejected():
    service = WakuDemoOAuthService(config())
    location, session_id = service.begin()
    state = parse_qs(urlsplit(location).query)['state'][0]

    with pytest.raises(WakuDemoOAuthError, match='state_mismatch'):
        service.consume_pending(session_id, 'wrong-state', 'code-a')

    location, session_id = service.begin()
    state = parse_qs(urlsplit(location).query)['state'][0]
    service.consume_pending(session_id, state, 'code-a')
    with pytest.raises(WakuDemoOAuthError, match='authorization_code_already_used'):
        service.consume_pending(session_id, state, 'code-a')


def test_expired_authorization_and_token_session_are_removed(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(oauth_module.time, 'time', lambda: now[0])
    cfg = config()
    service = WakuDemoOAuthService(cfg)

    location, browser_session_id = service.begin()
    state = parse_qs(urlsplit(location).query)['state'][0]
    now[0] += cfg.pending_ttl_seconds + 1
    with pytest.raises(WakuDemoOAuthError, match='authorization_request_expired'):
        service.consume_pending(browser_session_id, state, 'late-code')

    session_id = service.create_session('server-token', 1, {'id': 'account-1'})
    assert service.get_session(session_id) is not None
    now[0] += 2
    assert service.get_session(session_id) is None


@pytest.mark.asyncio
async def test_token_exchange_is_form_encoded_and_account_uses_bearer():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request):
        requests.append(request)
        if request.url.path.endswith('/token'):
            return httpx.Response(200, json={'access_token': 'server-only-token', 'expires_in': 900})
        return httpx.Response(200, json={'id': 42, 'nickname': '莲花玩家'})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WakuDemoOAuthService(config(), client)
    try:
        token, expires_in, account = await service.exchange_code('auth-code', 'pkce-verifier')
    finally:
        await client.aclose()

    form = parse_qs(requests[0].content.decode())
    assert requests[0].headers['content-type'].startswith('application/x-www-form-urlencoded')
    assert form == {
        'grant_type': ['authorization_code'],
        'code': ['auth-code'],
        'client_id': ['client-under-review'],
        'redirect_uri': ['https://www.bestguo.top:58000/api/login/callback'],
        'code_verifier': ['pkce-verifier'],
    }
    assert requests[1].headers['authorization'] == 'Bearer server-only-token'
    assert (token, expires_in, account['id']) == ('server-only-token', 900, 42)


@pytest.mark.asyncio
async def test_network_timeout_and_pkce_failure_are_mapped():
    async def timeout_handler(request: httpx.Request):
        raise httpx.ReadTimeout('slow', request=request)

    timeout_client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
    service = WakuDemoOAuthService(config(), timeout_client)
    with pytest.raises(WakuDemoOAuthError, match='token_endpoint_timeout'):
        await service.exchange_code('code', 'verifier')
    await timeout_client.aclose()

    reject_client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(400, json={'error': 'invalid_grant'})))
    service = WakuDemoOAuthService(config(), reject_client)
    with pytest.raises(WakuDemoOAuthError, match='pkce_or_authorization_code_rejected'):
        await service.exchange_code('code', 'bad-verifier')
    await reject_client.aclose()


@pytest.mark.asyncio
async def test_complete_callback_keeps_token_server_side(monkeypatch):
    async def provider(request: httpx.Request):
        if request.url.path.endswith('/token'):
            return httpx.Response(200, json={'access_token': 'never-send-to-browser', 'expires_in': 600})
        return httpx.Response(200, json={'uid': 'waku-7', 'display_name': '阿莲'})

    provider_client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    service = WakuDemoOAuthService(config(), provider_client)
    monkeypatch.setattr(auth_api, 'get_wakudemo_oauth_service', lambda: service)
    app = FastAPI()
    app.include_router(auth_api.router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url='http://backend.test',
        follow_redirects=False,
    ) as client:
        start = await client.get('/api/login/wakudemo')
        query = parse_qs(urlsplit(start.headers['location']).query)
        pre_auth_cookie = client.cookies.get(config().cookie_name)

        callback = await client.get('/api/login/callback', params={
            'code': 'single-use',
            'state': query['state'][0],
        })
        assert callback.status_code == 302
        assert 'wakudemo_login=success' in callback.headers['location']
        assert client.cookies.get(config().cookie_name) != pre_auth_cookie

        session = await client.get('/api/login/session')
        assert session.json() == {
            'authenticated': True,
            'account': {'id': 'waku-7', 'displayName': '阿莲', 'avatarUrl': None},
        }
        assert 'never-send-to-browser' not in session.text

        repeated = await client.get('/api/login/callback', params={
            'code': 'single-use',
            'state': query['state'][0],
        })
        assert 'authorization_code_already_used' in repeated.headers['location']

        logout = await client.post('/api/login/logout')
        assert logout.json() == {'authenticated': False}
        assert (await client.get('/api/login/session')).json() == {'authenticated': False}

    await provider_client.aclose()


@pytest.mark.asyncio
async def test_cancelled_authorization_is_reported(monkeypatch):
    service = WakuDemoOAuthService(config())
    monkeypatch.setattr(auth_api, 'get_wakudemo_oauth_service', lambda: service)
    app = FastAPI()
    app.include_router(auth_api.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://backend.test',
        follow_redirects=False,
    ) as client:
        start = await client.get('/api/login/wakudemo')
        state = parse_qs(urlsplit(start.headers['location']).query)['state'][0]
        response = await client.get('/api/login/callback', params={
            'error': 'access_denied', 'state': state,
        })
        assert 'wakudemo_error=authorization_cancelled' in response.headers['location']
