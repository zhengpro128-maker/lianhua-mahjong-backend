"""微信座位的 WebSocket 握手必须同时绑定 Bearer 身份。"""

from types import SimpleNamespace

import pytest

from app.game.room import room_registry
from app.ws import game_ws as ws_module


class FakeWebSocket:
    def __init__(self, rejoin_code: str, authorization: str = ''):
        self.query_params = {'rejoin_code': rejoin_code}
        self.headers = {'authorization': authorization} if authorization else {}
        self.messages: list[dict] = []
        self.accepted = False
        self.closed = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, message):
        self.messages.append(message)

    async def close(self):
        self.closed = True


@pytest.fixture()
def wechat_room(fresh_rooms):
    room = room_registry.create('WXSOCK', capacity=4)
    _, _, state = room.join_or_rejoin('微信玩家', player_id='wechat-owner')
    return room, state.rejoin_code


@pytest.mark.asyncio
async def test_wechat_seat_rejects_socket_without_bearer(wechat_room):
    _, rejoin_code = wechat_room
    socket = FakeWebSocket(rejoin_code)

    await ws_module.game_ws(socket, 'WXSOCK')

    assert socket.accepted is True
    assert socket.closed is True
    assert socket.messages == [{'kind': 'rejoin_err', 'code': 'AUTH_REQUIRED'}]


@pytest.mark.asyncio
async def test_wechat_seat_rejects_different_bearer_identity(
        wechat_room, monkeypatch):
    _, rejoin_code = wechat_room
    service = SimpleNamespace(verify_access_token=lambda _: SimpleNamespace(
        player_id='wechat-someone-else'))
    monkeypatch.setattr(ws_module, 'get_wechat_auth_service', lambda: service)
    socket = FakeWebSocket(rejoin_code, 'Bearer valid-but-different-user')

    await ws_module.game_ws(socket, 'WXSOCK')

    assert socket.closed is True
    assert socket.messages == [{'kind': 'rejoin_err', 'code': 'AUTH_REQUIRED'}]
