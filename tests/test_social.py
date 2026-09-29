import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.game.room import RoomSession


def playing_room():
    room = RoomSession('SOCIAL')
    for i in range(2):
        room.join_or_rejoin(f'玩家{i}')
        room.conn.register(i, asyncio.Queue(), Mock())
    room.status = 'playing'
    room.manager = SimpleNamespace(players=[object()] * 4)
    return room


@pytest.mark.parametrize('payload', [
    {'category': 'text', 'value': '你好😀'},
    {'category': 'phrase', 'value': 'hello'},
    {'category': 'emoji', 'value': 'smile'},
    *[{'category': 'prop', 'value': prop, 'targetSeat': 3} for prop in ('tomato', 'coffee', 'hammer')],
])
def test_broadcast_to_every_connected_seat_with_authoritative_sender(payload):
    room = playing_room()
    other_room = playing_room()
    assert room.handle_client_message(0, {**payload, 'type': 'room_social', 'seat': 3, 'id': 'forged'}) == (True, '')
    first = room.conn._queues[0].get_nowait()
    assert first == room.conn._queues[1].get_nowait()
    assert first['seat'] == 0 and first['id'] != 'forged'
    assert first['kind'] == 'room_social'
    assert other_room.conn._queues[0].empty()


@pytest.mark.parametrize('payload', [
    {'category': 'text', 'value': ''}, {'category': 'text', 'value': ' '},
    {'category': 'text', 'value': '字' * 61}, {'category': 'text', 'value': 'a\x00b'},
    {'category': 'text', 'value': []}, {'category': 'phrase', 'value': 'unknown'},
    {'category': 'emoji', 'value': 'unknown'}, {'category': 'other', 'value': 'hello'},
    *[{'category': 'prop', 'value': 'tomato', 'targetSeat': target} for target in (0, -1, 4, True, '1', None)],
])
def test_invalid_messages_are_not_broadcast(payload):
    room = playing_room()
    assert room.handle_client_message(0, {'type': 'room_social', **payload}) == (False, 'SOCIAL_INVALID')
    assert room.conn._queues[1].empty()


def test_cooldown_survives_reconnect_and_does_not_block_other_seats(monkeypatch):
    room = playing_room()
    payload = {'type': 'room_social', 'category': 'phrase', 'value': 'hello'}
    monkeypatch.setattr('app.game.social.time.monotonic', lambda: 100)
    assert room.handle_client_message(0, payload)[0]
    room.conn.unregister(0)
    room.conn.register(0, asyncio.Queue(), Mock())
    assert room.handle_client_message(0, payload) == (False, 'SOCIAL_RATE_LIMIT')
    assert room.handle_client_message(1, payload)[0]
    monkeypatch.setattr('app.game.social.time.monotonic', lambda: 102)
    assert room.handle_client_message(0, payload)[0]


def test_only_seated_connected_players_in_active_room_can_send():
    room = playing_room()
    payload = {'type': 'room_social', 'category': 'text', 'value': 'hello'}
    assert room.handle_client_message(2, payload) == (False, 'SOCIAL_NOT_SEATED')
    room.status = 'closed'
    assert room.handle_client_message(0, payload) == (False, 'SOCIAL_UNAVAILABLE')
    room.status = 'playing'
    room.conn.unregister(0)
    assert room.handle_client_message(0, payload) == (False, 'SOCIAL_UNAVAILABLE')


async def test_social_over_real_websockets(server, fresh_rooms):
    import json
    import websockets
    from app.game.room import room_registry

    room = room_registry.create('SOCIAL', capacity=2)
    codes = [room.join_or_rejoin(f'玩家{i}')[2].rejoin_code for i in range(2)]
    # Keep the real room and authenticated transport, with no turn loop needed.
    room.status = 'playing'

    async def receive_kind(socket, kind):
        async with asyncio.timeout(5):
            while True:
                event = json.loads(await socket.recv())
                if event.get('kind') == kind:
                    return event

    async with websockets.connect(f"{server['ws']}/ws/room/SOCIAL?rejoin_code={codes[0]}") as first, \
            websockets.connect(f"{server['ws']}/ws/room/SOCIAL?rejoin_code={codes[1]}") as second:
        await receive_kind(first, 'rejoin_ok')
        await receive_kind(second, 'rejoin_ok')
        await first.send(json.dumps({'type': 'room_social', 'category': 'text', 'value': '你好，同桌！', 'seat': 3}))
        echo = await receive_kind(first, 'room_social')
        assert echo == await receive_kind(second, 'room_social')
        assert echo['seat'] == 0 and echo['value'] == '你好，同桌！'
        await first.send(json.dumps({'type': 'room_social', 'category': 'emoji', 'value': 'smile'}))
        assert (await receive_kind(first, 'error'))['code'] == 'SOCIAL_RATE_LIMIT'
