import asyncio
from contextlib import AsyncExitStack
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

    room = room_registry.create('SOCIAL', capacity=4)
    codes = [room.join_or_rejoin(f'玩家{i}')[2].rejoin_code for i in range(4)]
    # Keep the real room and authenticated transport, with no turn loop needed.
    room.status = 'playing'

    async def receive_kind(socket, kind):
        async with asyncio.timeout(5):
            while True:
                event = json.loads(await socket.recv())
                if event.get('kind') == kind:
                    return event

    async with AsyncExitStack() as stack:
        sockets = [await stack.enter_async_context(websockets.connect(
            f"{server['ws']}/ws/room/SOCIAL?rejoin_code={code}")) for code in codes]
        for seat, socket in enumerate(sockets):
            assert (await receive_kind(socket, 'rejoin_ok'))['seat'] == seat
            await receive_kind(socket, 'state_snapshot')
        # Only the table's occupied targets are needed for social validation;
        # game-turn timing must not interfere with this real transport test.
        room.manager = SimpleNamespace(players=[object()] * 4)
        payloads = [
            {'category': 'prop', 'value': 'tomato', 'targetSeat': 1},
            {'category': 'prop', 'value': 'coffee', 'targetSeat': 2},
            {'category': 'prop', 'value': 'hammer', 'targetSeat': 3},
            {'category': 'text', 'value': '你好，同桌！'},
            {'category': 'phrase', 'value': 'hello'},
            {'category': 'emoji', 'value': 'smile'},
        ]
        event_ids = set()
        for index, payload in enumerate(payloads):
            if index == 4:
                # Preserve the real two-second per-sender limit; the first
                # four events use different senders and need no wait.
                await asyncio.sleep(2.01)
            sender = index % 4
            await sockets[sender].send(json.dumps({
                **payload, 'type': 'room_social', 'seat': (sender + 1) % 4,
                'id': 'forged',
            }))
            events = await asyncio.gather(*[
                receive_kind(socket, 'room_social') for socket in sockets])
            echo = events[0]
            assert all(event == echo for event in events)
            assert echo == {**payload, 'kind': 'room_social',
                            'seat': sender, 'id': echo['id']}
            assert echo['id'] != 'forged' and echo['id'] not in event_ids
            event_ids.add(echo['id'])
            if index == 0:
                await sockets[sender].send(json.dumps({
                    'type': 'room_social', 'category': 'emoji', 'value': 'smile'}))
                assert (await receive_kind(sockets[sender], 'error'))['code'] == 'SOCIAL_RATE_LIMIT'


@pytest.mark.parametrize('props', [
    pytest.param(('tomato',) * 3, id='three-tomatoes'),
    pytest.param(('coffee',) * 3, id='three-coffees'),
    pytest.param(('hammer',) * 3, id='three-hammers'),
    pytest.param(('tomato', 'coffee', 'hammer'), id='mixed-props'),
])
async def test_concurrent_props_to_one_target_over_real_websockets(server, fresh_rooms, props):
    import json
    import websockets
    from app.game.room import room_registry

    room = room_registry.create('SOCIAL', capacity=4)
    codes = [room.join_or_rejoin(f'玩家{i}')[2].rejoin_code for i in range(4)]
    room.status = 'playing'

    async def receive_kind(socket, kind):
        async with asyncio.timeout(5):
            while True:
                event = json.loads(await socket.recv())
                if event.get('kind') == kind:
                    return event

    async def receive_social_batch(socket):
        return [await receive_kind(socket, 'room_social') for _ in props]

    async with AsyncExitStack() as stack:
        sockets = [await stack.enter_async_context(websockets.connect(
            f"{server['ws']}/ws/room/SOCIAL?rejoin_code={code}")) for code in codes]
        for seat, socket in enumerate(sockets):
            assert (await receive_kind(socket, 'rejoin_ok'))['seat'] == seat
            await receive_kind(socket, 'state_snapshot')
        room.manager = SimpleNamespace(players=[object()] * 4)

        payloads = [
            {'type': 'room_social', 'category': 'prop', 'value': prop,
             'targetSeat': 3, 'seat': 3, 'id': 'forged'}
            for prop in props
        ]
        # Three authenticated players send concurrently to the fourth player.
        await asyncio.gather(*[
            sockets[sender].send(json.dumps(payload))
            for sender, payload in enumerate(payloads)
        ])
        batches = await asyncio.gather(*[
            receive_social_batch(socket) for socket in sockets
        ])
        events = batches[0]
        assert all(batch == events for batch in batches)
        event_ids = {event['id'] for event in events}
        assert len(event_ids) == 3 and 'forged' not in event_ids
        assert {event['seat'] for event in events} == {0, 1, 2}
        for event in events:
            assert event == {
                'kind': 'room_social', 'category': 'prop',
                'value': props[event['seat']], 'targetSeat': 3,
                'seat': event['seat'], 'id': event['id'],
            }

        # Each sender has its own cooldown, even when the target is shared.
        await asyncio.gather(*[
            sockets[sender].send(json.dumps(payload))
            for sender, payload in enumerate(payloads)
        ])
        errors = await asyncio.gather(*[
            receive_kind(sockets[sender], 'error') for sender in range(3)
        ])
        assert all(error['code'] == 'SOCIAL_RATE_LIMIT' for error in errors)

        # Receiving three props must not consume the target's send allowance.
        await sockets[3].send(json.dumps({
            'type': 'room_social', 'category': 'prop', 'value': 'tomato', 'targetSeat': 0,
        }))
        reply_events = await asyncio.gather(*[
            receive_kind(socket, 'room_social') for socket in sockets
        ])
        reply = reply_events[0]
        assert all(event == reply for event in reply_events)
        assert reply == {
            'kind': 'room_social', 'category': 'prop', 'value': 'tomato',
            'targetSeat': 0, 'seat': 3, 'id': reply['id'],
        }
        assert reply['id'] not in event_ids
