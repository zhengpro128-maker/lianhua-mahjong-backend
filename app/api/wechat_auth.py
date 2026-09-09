"""微信小游戏登录 API。"""

from pydantic import BaseModel, Field
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth.wechat import WechatAuthError, get_wechat_auth_service

router = APIRouter(prefix='/api/auth', tags=['auth'])


class WechatLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=256)


@router.post('/wechat')
async def login_wechat(body: WechatLoginRequest, request: Request):
    try:
        identity = await get_wechat_auth_service().exchange_code(
            body.code,
            request.client.host if request.client else None,
        )
    except WechatAuthError as exc:
        return JSONResponse(
            {'detail': {'code': exc.code}},
            status_code=exc.status_code,
            headers={'Cache-Control': 'no-store'},
        )
    return JSONResponse({
        'playerId': identity.player_id,
        'accessToken': identity.access_token,
        'expiresAt': identity.expires_at,
    }, headers={'Cache-Control': 'no-store'})
