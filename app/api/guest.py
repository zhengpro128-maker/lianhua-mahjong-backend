import os

from fastapi import APIRouter, Request, Response

from app.auth.guest import COOKIE_NAME, MAX_AGE, issue_guest_token, verify_guest_token

router = APIRouter(prefix='/api/guest', tags=['guest'])


def _secure_cookie(request: Request) -> bool:
    configured = os.getenv('GUEST_COOKIE_SECURE')
    if configured is not None:
        return configured.strip().lower() in {'1', 'true', 'yes', 'on'}
    return request.url.scheme == 'https'


@router.post('/session')
def ensure_guest_session(request: Request, response: Response) -> dict:
    uid = verify_guest_token(request.cookies.get(COOKIE_NAME))
    if uid is None:
        token, uid = issue_guest_token()
        response.set_cookie(
            COOKIE_NAME, token, max_age=MAX_AGE, httponly=True,
            secure=_secure_cookie(request), samesite='lax', path='/',
        )
    return {'authenticated': True, 'account': {'id': uid, 'displayName': '游客'}}
