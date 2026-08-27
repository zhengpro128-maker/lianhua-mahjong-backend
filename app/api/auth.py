"""WakuDemo OAuth 登录入口、回调和服务端会话 API。"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from loguru import logger

from app.auth.wakudemo import WakuDemoOAuthError, get_wakudemo_oauth_service

router = APIRouter(prefix='/api/login', tags=['auth'])


def _redirect_url(base: str, result: str, error: str | None = None) -> str:
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query['wakudemo_login'] = result
    if error:
        query['wakudemo_error'] = error
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _set_session_cookie(response: RedirectResponse | JSONResponse, session_id: str,
                        max_age: int) -> None:
    config = get_wakudemo_oauth_service().config
    response.set_cookie(
        key=config.cookie_name,
        value=session_id,
        max_age=max_age,
        path='/',
        secure=config.cookie_secure,
        httponly=True,
        samesite=config.cookie_samesite,  # type: ignore[arg-type]
    )


def _delete_session_cookie(response: RedirectResponse | JSONResponse) -> None:
    config = get_wakudemo_oauth_service().config
    response.delete_cookie(
        key=config.cookie_name,
        path='/',
        secure=config.cookie_secure,
        httponly=True,
        samesite=config.cookie_samesite,  # type: ignore[arg-type]
    )


def _account_summary(account: dict[str, Any]) -> dict[str, Any]:
    """兼容审核期尚未最终确认的账户字段，不向前端暴露 token。"""
    account_id = next((account.get(key) for key in ('id', 'account_id', 'user_id', 'uid')
                       if account.get(key) is not None), None)
    display_name = next((account.get(key) for key in (
        'display_name', 'nickname', 'username', 'name')
        if isinstance(account.get(key), str) and account.get(key)), None)
    avatar_url = next((account.get(key) for key in ('avatar_url', 'avatar')
                       if isinstance(account.get(key), str) and account.get(key)), None)
    return {
        'id': str(account_id) if account_id is not None else None,
        'displayName': display_name,
        'avatarUrl': avatar_url,
    }


@router.get('/wakudemo')
async def begin_wakudemo_login(request: Request):
    service = get_wakudemo_oauth_service()
    try:
        authorize_url, browser_session_id = service.begin(
            request.cookies.get(service.config.cookie_name))
    except WakuDemoOAuthError as exc:
        raise HTTPException(exc.status_code, detail={'code': exc.code}) from exc
    response = RedirectResponse(authorize_url, status_code=302)
    _set_session_cookie(response, browser_session_id, service.config.pending_ttl_seconds)
    return response


@router.get('/callback')
async def wakudemo_login_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    service = get_wakudemo_oauth_service()
    browser_session_id = request.cookies.get(service.config.cookie_name)
    try:
        pending = service.consume_pending(browser_session_id, state, code)
        if error:
            error_code = 'authorization_cancelled' if error == 'access_denied' else 'authorization_failed'
            raise WakuDemoOAuthError(error_code)
        if not code:
            raise WakuDemoOAuthError('missing_authorization_code')
        access_token, expires_in, account = await service.exchange_code(
            code, pending.code_verifier)
        # 授权成功后轮换随机会话 ID，防止登录前会话固定。
        service.delete_session(browser_session_id)
        authenticated_session_id = service.create_session(access_token, expires_in, account)
    except WakuDemoOAuthError as exc:
        service.delete_session(browser_session_id)
        logger.warning('WakuDemo OAuth 登录失败: {}', exc.code)
        response = RedirectResponse(
            _redirect_url(service.config.frontend_url, 'error', exc.code),
            status_code=302,
        )
        _delete_session_cookie(response)
        return response

    response = RedirectResponse(
        _redirect_url(service.config.frontend_url, 'success'), status_code=302)
    _set_session_cookie(response, authenticated_session_id, expires_in)
    return response


@router.get('/session')
async def get_login_session(request: Request) -> dict[str, Any]:
    service = get_wakudemo_oauth_service()
    session = service.get_session(request.cookies.get(service.config.cookie_name))
    if session is None:
        return {'authenticated': False}
    return {'authenticated': True, 'account': _account_summary(session.account)}


@router.post('/logout')
async def logout(request: Request):
    service = get_wakudemo_oauth_service()
    service.delete_session(request.cookies.get(service.config.cookie_name))
    response = JSONResponse({'authenticated': False})
    _delete_session_cookie(response)
    return response
