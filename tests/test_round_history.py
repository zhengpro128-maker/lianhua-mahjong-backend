"""当前房间整场战绩：公开边界、净输赢、身份冻结与真实 WS 重连。"""

import asyncio
import concurrent.futures
import json
from unittest.mock import AsyncMock

import pytest
import websockets

from app.game.manager import GameManager
from app.game.room import RoomSession, WSEvents, build_snapshot, room_registry


def install_manager(room):
    room.manager = GameManager(
        controllers=room._controllers(), player_seeds=room._seeds(),
        events=WSEvents(room), initial_score=0,
    )
    room.manager._reset_players()
    room.manager.phase = 'playing'
    room.status = 'playing'
    room.manager.events.round_start(True, 1, 0, 0, [1, 1])
    return room.manager


def history_room(mode='east'):
    room = RoomSession('HISTORY', mode=mode, capacity=4)
    for seat in range(4):
        _, _, state = room.join_or_rejoin(f'真实玩家{seat}')
        state.avatar = f'https://example.com/avatar/{seat}.png'
    install_manager(room)
    return room


def settle(room, scores, *, draw=False):
    mgr = room.manager
    scores_before_win = [player.score for player in mgr.players]
    for player, score in zip(mgr.players, scores):
        player.score = score
    base = {
        'draw': draw, 'winner': '荒庄' if draw else mgr.players[1].name,
        'details': [] if draw else [{'label': '底分·七对', 'points': 4},
                                   {'label': '杠番·暗杠', 'multiplier': 4}],
        'totalWon': 0 if draw else 12,
    }
    if not draw:
        base.update({'winnerIndex': 1, 'winType': 'self-draw'})
    mgr.result = mgr.make_round_result(base, scores_before_win)
    mgr.phase = 'settled'
    room._settlement_snapshot_released = False


def publish(room):
    room._settlement_snapshot_released = True
    room.broadcast_snapshot()


def test_empty_lobby_has_round_history_for_old_clients_to_ignore():
    snapshot = build_snapshot(RoomSession('EMPTY'), 0)
    assert snapshot['phase'] == 'lobby'
    assert snapshot['roundHistory'] == []


def test_history_freezes_absolute_seats_true_identity_details_and_whole_round_net():
    room = history_room()
    # A mid-hand transfer precedes winning settlement, as with following dealer.
    for player, score in zip(room.manager.players, [-3, 1, 1, 1]):
        player.score = score
    # Manager seeds may be stale; room identity at settlement is authoritative.
    room.manager.players[1].name = '旧昵称'
    room.manager.players[1].avatar = ''
    settle(room, [-7, 13, -3, -3])
    original = json.loads(json.dumps(room.manager.result))
    assert [change['delta'] for change in original['scoreChanges']] == [-4, 12, -4, -4]
    publish(room)
    record = build_snapshot(room, 3)['roundHistory'][0]
    assert record['round'] == 1 and record['dealer'] == 0 and record['honba'] == 0
    assert record['winnerIndex'] == 1 and record['winner'] == '真实玩家1'
    assert record['details'] == original['details']
    assert record['totalWon'] == 12
    assert [change['delta'] for change in record['scoreChanges']] == [-7, 13, -3, -3]
    assert [change['playerIndex'] for change in record['scoreChanges']] == [0, 1, 2, 3]
    assert record['scoreChanges'][1]['avatar'] == 'https://example.com/avatar/1.png'
    assert record['scoreChanges'][1]['playerKind'] == 'human'
    assert room.manager.result == original, '战绩记录不得修改原结算或计分'

    room.seats[1].nickname = '改名后'
    room.seats[1].avatar = 'https://example.com/new.png'
    room.manager.result['details'][0]['label'] = '被修改'
    record['scoreChanges'][1]['name'] = '客户端修改'
    frozen = build_snapshot(room, 0)['roundHistory'][0]
    assert frozen['scoreChanges'][1]['name'] == '真实玩家1'
    assert frozen['scoreChanges'][1]['avatar'] == 'https://example.com/avatar/1.png'
    assert frozen['details'][0]['label'] == '底分·七对'


def test_unreleased_result_cannot_leak_through_history_even_to_early_anime_client():
    room = history_room()
    settle(room, [-4, 12, -4, -4])
    room.broadcast_snapshot()
    assert build_snapshot(room, 0)['result'] is None
    assert build_snapshot(room, 0)['roundHistory'] == []
    room._presentation_audio_modes[1] = 'anime-fixed-tts-v1'
    assert build_snapshot(room, 1)['result']['winnerIndex'] == 1
    assert build_snapshot(room, 1)['roundHistory'] == []
    publish(room)
    assert len(build_snapshot(room, 0)['roundHistory']) == 1
    room.manager.honba = 1
    room.manager.events.round_start(False, 1, 0, 1, [1, 1])
    settle(room, [-8, 24, -8, -8])
    room.broadcast_snapshot()
    assert build_snapshot(room, 0)['result'] is None
    assert len(build_snapshot(room, 0)['roundHistory']) == 1
    assert len(build_snapshot(room, 1)['roundHistory']) == 1


@pytest.mark.parametrize('scores', [[0, 0, 0, 0], [-3, 1, 1, 1]])
def test_draw_history_keeps_every_players_net(scores):
    room = history_room()
    for player, score in zip(room.manager.players, scores):
        player.score = score
    room.manager.end_draw()
    assert all(change['delta'] == 0 for change in room.manager.result['scoreChanges'])
    publish(room)
    record = build_snapshot(room, 0)['roundHistory'][0]
    assert record['draw'] is True and record.get('winnerIndex') is None
    assert record['winner'] == '荒庄'
    assert [change['delta'] for change in record['scoreChanges']] == scores


def test_repeated_snapshots_do_not_duplicate_completed_round():
    room = history_room()
    settle(room, [-4, 12, -4, -4])
    publish(room)
    first_id = room._round_history[0]['id']
    for _ in range(5):
        publish(room)
        assert build_snapshot(room, 2)['roundHistory'][0]['id'] == first_id
    assert len(room._round_history) == 1


async def test_next_round_preserves_history_and_captures_a_new_score_baseline(monkeypatch):
    room = history_room()
    settle(room, [-4, 12, -4, -4])
    publish(room)
    monkeypatch.setattr(room.manager, 'begin_turn', AsyncMock())
    await room.manager.next_round()
    assert len(build_snapshot(room, 2)['roundHistory']) == 1
    assert room._round_start_scores == {0: -4, 1: 12, 2: -4, 3: -4}
    settle(room, [2, 10, -6, -6])
    publish(room)
    records = build_snapshot(room, 2)['roundHistory']
    assert len(records) == 2 and records[0]['id'] != records[1]['id']
    assert [change['delta'] for change in records[1]['scoreChanges']] == [6, -2, -2, -2]


def test_dealer_continuation_is_a_distinct_record_even_with_same_round_label():
    room = history_room()
    settle(room, [-4, 12, -4, -4])
    publish(room)
    mgr = room.manager
    mgr.honba = 1
    mgr.events.round_start(False, mgr.round, mgr.dealer, mgr.honba, [1, 1])
    settle(room, [-8, 24, -8, -8])
    publish(room)
    records = build_snapshot(room, 0)['roundHistory']
    assert len(records) == 2
    assert records[0]['roundLabel'] == records[1]['roundLabel']
    assert [record['honba'] for record in records] == [0, 1]


async def test_last_hand_remains_in_finished_snapshot():
    room = history_room()
    mgr = room.manager
    mgr.round = 4
    mgr.dealer = 3
    mgr.events.round_start(False, mgr.round, mgr.dealer, mgr.honba, [1, 1])
    settle(room, [-4, 12, -4, -4])
    publish(room)
    await mgr.next_round()
    room.status = 'finished'
    snapshot = build_snapshot(room, 2)
    assert snapshot['matchFinished'] is True and snapshot['phase'] == 'finished'
    assert len(snapshot['roundHistory']) == 1 and snapshot['roundHistory'][0]['round'] == 4


async def test_starting_another_match_in_same_room_clears_history(monkeypatch):
    room = history_room()
    settle(room, [-4, 12, -4, -4])
    publish(room)
    old_id = room._round_history[0]['id']
    room.status = 'finished'
    for state in room.seats:
        state.ready = True
    monkeypatch.setattr(room, '_drive', AsyncMock())
    await room.start()
    await room.game_task
    assert build_snapshot(room, 1)['roundHistory'] == []
    room.manager._reset_players()
    room.manager.events.round_start(True, 1, 0, 0, [1, 1])
    settle(room, [-4, 12, -4, -4])
    publish(room)
    assert room._round_history[0]['id'] != old_id


def test_explicit_return_to_lobby_resets_history():
    room = history_room()
    settle(room, [-4, 12, -4, -4])
    publish(room)
    room.manager.return_to_lobby()
    assert build_snapshot(room, 1)['roundHistory'] == []


@pytest.mark.parametrize('mode,total', [('east', 4), ('rounds4', 4), ('rounds8', 8), ('rounds16', 16)])
async def test_real_game_driver_keeps_every_hand_through_match_finished(mode, total):
    room = history_room(mode)
    room.status = 'lobby'
    for state in room.seats:
        state.ready = True
    await room.start()
    await asyncio.wait_for(room.game_task, timeout=30)
    snapshot = build_snapshot(room, 3)
    records = snapshot['roundHistory']
    assert snapshot['phase'] == 'finished' and snapshot['matchFinished'] is True
    assert {record['round'] for record in records} == set(range(1, total + 1))
    if mode != 'east':
        assert len(records) == total
    assert len({record['id'] for record in records}) == len(records)
    assert [change['score'] for change in records[-1]['scoreChanges']] == [
        player.score for player in room.manager.players
    ]
    assert all(sum(change['delta'] for change in record['scoreChanges']) == 0 for record in records)
    assert [sum(record['scoreChanges'][seat]['delta'] for record in records) for seat in range(4)] == [
        player.score for player in room.manager.players
    ]


async def test_real_websocket_rejoin_recovers_missed_rounds_and_absolute_identities(server, fresh_rooms):
    room = room_registry.create('HISTORY-WS', capacity=4)
    for seat in range(4):
        _, _, state = room.join_or_rejoin(f'真实玩家{seat}')
        state.avatar = f'https://example.com/avatar/{seat}.png'
    install_manager(room)

    async def receive_kind(socket, kind):
        async with asyncio.timeout(5):
            while True:
                message = json.loads(await socket.recv())
                if message.get('kind') == kind:
                    return message

    def url(seat):
        return f"{server['ws']}/ws/room/HISTORY-WS?rejoin_code={room.seats[seat].rejoin_code}"

    async with websockets.connect(url(0)) as first:
        await receive_kind(first, 'rejoin_ok')
        assert (await receive_kind(first, 'state_snapshot'))['roundHistory'] == []
        async with websockets.connect(url(2)) as missing:
            await receive_kind(missing, 'rejoin_ok')
            assert (await receive_kind(missing, 'state_snapshot'))['roundHistory'] == []
        async with asyncio.timeout(5):
            while room.conn.is_connected(2):
                await asyncio.sleep(0.01)

        # Execute publication on uvicorn's loop, matching real game-driver ownership.
        def complete_two_hands():
            settle(room, [-4, 12, -4, -4])
            publish(room)
            mgr = room.manager
            mgr.honba = 1
            mgr.events.round_start(False, mgr.round, mgr.dealer, mgr.honba, [1, 1])
            settle(room, [-8, 24, -8, -8], draw=True)
            publish(room)
            mgr.phase = 'playing'
            mgr.result = None
            room.broadcast_snapshot()

        completed = concurrent.futures.Future()

        def run_on_server():
            try:
                complete_two_hands()
                completed.set_result(None)
            except Exception as exc:
                completed.set_exception(exc)

        room.conn._tasks[0].get_loop().call_soon_threadsafe(run_on_server)
        await asyncio.wrap_future(completed)
        live = await receive_kind(first, 'state_snapshot')
        assert len(live['roundHistory']) == 1
        live = await receive_kind(first, 'state_snapshot')
        assert len(live['roundHistory']) == 2
        async with websockets.connect(url(2)) as resumed:
            assert (await receive_kind(resumed, 'rejoin_ok'))['seat'] == 2
            recovered = await receive_kind(resumed, 'state_snapshot')
            assert recovered['phase'] == 'playing' and recovered['result'] is None
            assert recovered['roundHistory'] == live['roundHistory']
            assert len(recovered['roundHistory']) == 2
            assert recovered['roundHistory'][0]['winnerIndex'] == 1
            assert [change['playerIndex'] for change in recovered['roundHistory'][0]['scoreChanges']] == [0, 1, 2, 3]
            assert recovered['roundHistory'][1]['draw'] is True
            assert recovered['roundHistory'][0]['scoreChanges'][1]['name'] == '真实玩家1'
            assert recovered['roundHistory'][0]['scoreChanges'][1]['avatar'] == 'https://example.com/avatar/1.png'
