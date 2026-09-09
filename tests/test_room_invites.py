"""微信房间分享票据与受邀加入集成测试。"""

import httpx
import pytest
from fastapi import FastAPI

from app.api import rooms as rooms_api
from app.auth.room_invite import (
    RoomInviteConfig,
    RoomInviteError,
    RoomInviteService,
)


SECRET = 'room-invite-test-secret-that-is-long-enough'


def test_room_invite_ticket_is_room_bound_and_expires():
    clock = {'now': 1_000.0}
    service = RoomInviteService(
        RoomInviteConfig(secret=SECRET, ttl_seconds=120),
        clock=lambda: clock['now'],
    )
    ticket, expires_at = service.issue('ABC123', 'wechat-owner')
    assert expires_at == 1120
    assert service.verify(ticket, 'ABC123').room_id == 'ABC123'

    with pytest.raises(RoomInviteError) as mismatch:
        service.verify(ticket, 'XYZ789')
    assert mismatch.value.code == 'ROOM_INVITE_INVALID'

    parts = ticket.split('.')
    tampered = f'{parts[0]}.{parts[1]}.{parts[2][:-1]}x'
    with pytest.raises(RoomInviteError) as invalid:
        service.verify(tampered, 'ABC123')
    assert invalid.value.code == 'ROOM_INVITE_INVALID'

    clock['now'] = 1120.0
    with pytest.raises(RoomInviteError) as expired:
        service.verify(ticket, 'ABC123')
    assert expired.value.code == 'ROOM_INVITE_EXPIRED'
    assert expired.value.status_code == 410


@pytest.fixture()
def invite_app(monkeypatch):
    clock = {'now': 2_000.0}
    service = RoomInviteService(
        RoomInviteConfig(secret=SECRET, ttl_seconds=300),
        clock=lambda: clock['now'],
    )
    monkeypatch.setattr(rooms_api, 'get_room_invite_service', lambda: service)
    app = FastAPI()
    app.include_router(rooms_api.router)
    return app, clock


def _auth(uid: str) -> dict[str, str]:
    return {'cookie': f'lgm_wakudemo_session={uid}'}


@pytest.mark.asyncio
async def test_room_member_can_share_and_multiple_friends_can_join(
        invite_app, fresh_rooms):
    app, _ = invite_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url='http://test') as http:
        created = await http.post('/api/rooms', json={'capacity': 4},
                                  headers=_auth('owner'))
        room_id = created.json()['roomId']
        owner = await http.post(f'/api/rooms/{room_id}/join',
                                json={'nickname': '房主'}, headers=_auth('owner'))
        assert owner.status_code == 200

        shared = await http.post(f'/api/rooms/{room_id}/invites',
                                 headers=_auth('owner'))
        assert shared.status_code == 200
        assert shared.json()['roomId'] == room_id
        assert shared.json()['expiresAt'] == 2300
        ticket = shared.json()['inviteTicket']

        first = await http.post(f'/api/rooms/{room_id}/join-by-invite', json={
            'nickname': '好友甲', 'inviteTicket': ticket,
        }, headers=_auth('friend-a'))
        second = await http.post(f'/api/rooms/{room_id}/join-by-invite', json={
            'nickname': '好友乙', 'inviteTicket': ticket,
        }, headers=_auth('friend-b'))
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()['playerId'] == 'wakudemo-friend-a'
        assert first.json()['mode'] == 'east'
        assert first.json()['rulesetId'] == 'lotus-classic'

        # 重复打开卡片幂等恢复原座位，不再占一个座位。
        again = await http.post(f'/api/rooms/{room_id}/join-by-invite', json={
            'nickname': '随意新昵称', 'inviteTicket': ticket,
        }, headers=_auth('friend-a'))
        assert again.status_code == 200
        assert again.json()['seat'] == first.json()['seat']
        assert again.json()['rejoinCode'] == first.json()['rejoinCode']
        assert again.json()['rejoin'] is True


@pytest.mark.asyncio
async def test_non_member_cannot_create_invite(invite_app, fresh_rooms):
    app, _ = invite_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url='http://test') as http:
        created = await http.post('/api/rooms', json={}, headers=_auth('owner'))
        room_id = created.json()['roomId']
        await http.post(f'/api/rooms/{room_id}/join', json={'nickname': '房主'},
                        headers=_auth('owner'))
        denied = await http.post(f'/api/rooms/{room_id}/invites',
                                 headers=_auth('outsider'))
        assert denied.status_code == 403
        assert denied.json()['detail']['code'] == 'NOT_ROOM_MEMBER'


@pytest.mark.asyncio
async def test_expired_or_wrong_room_invite_is_rejected(invite_app, fresh_rooms):
    app, clock = invite_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url='http://test') as http:
        created = await http.post('/api/rooms', json={}, headers=_auth('owner'))
        room_id = created.json()['roomId']
        await http.post(f'/api/rooms/{room_id}/join', json={'nickname': '房主'},
                        headers=_auth('owner'))
        shared = await http.post(f'/api/rooms/{room_id}/invites',
                                 headers=_auth('owner'))
        ticket = shared.json()['inviteTicket']

        wrong = await http.post('/api/rooms/ABC123/join-by-invite', json={
            'nickname': '好友', 'inviteTicket': ticket,
        }, headers=_auth('friend'))
        assert wrong.status_code == 400
        assert wrong.json()['detail']['code'] == 'ROOM_INVITE_INVALID'

        clock['now'] = 2300.0
        expired = await http.post(f'/api/rooms/{room_id}/join-by-invite', json={
            'nickname': '好友', 'inviteTicket': ticket,
        }, headers=_auth('friend'))
        assert expired.status_code == 410
        assert expired.json()['detail']['code'] == 'ROOM_INVITE_EXPIRED'
