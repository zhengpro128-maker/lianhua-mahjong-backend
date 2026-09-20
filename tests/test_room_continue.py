import asyncio

import pytest

from app.game.room import RoomSession


@pytest.mark.asyncio
async def test_next_round_waits_for_every_connected_human_and_excludes_ai_seats():
    room = RoomSession('CONTINUE-GATE', mode='east', capacity=4)
    first, _, first_state = room.join_or_rejoin('甲')
    second, _, second_state = room.join_or_rejoin('乙')
    first_state.controller.set_connected(True)
    second_state.controller.set_connected(True)

    waiting = asyncio.create_task(room._wait_for_continue())
    await asyncio.sleep(0)

    # 空座由 AI 补位，不能让它们阻塞确认；两个在线真人都尚未确认时也绝不能自动推进。
    assert room._continue is not None
    assert room._continue['confirmed'] == set()
    assert not waiting.done()

    assert room._confirm_continue(first) == (True, '')
    await asyncio.sleep(0)
    assert not waiting.done()

    assert room._confirm_continue(second) == (True, '')
    await waiting

