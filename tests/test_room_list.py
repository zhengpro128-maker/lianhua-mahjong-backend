"""大厅可加入房间列表与联机起分规则。"""

import httpx
import pytest

from app.game.manager import GameManager
from app.game.room import RoomSession, room_registry


@pytest.mark.asyncio
async def test_room_list_only_returns_lobby_rooms_with_human_capacity(server, fresh_rooms):
    open_room = room_registry.create('OPEN01', mode='east', capacity=2)
    open_room.join_or_rejoin('甲', player_id='open-player')

    full_room = room_registry.create('FULL01', mode='hanchan', capacity=2)
    full_room.join_or_rejoin('乙', player_id='full-player-1')
    full_room.join_or_rejoin('丙', player_id='full-player-2')

    playing_room = room_registry.create('PLAY01', mode='east', capacity=4)
    playing_room.status = 'playing'

    async with httpx.AsyncClient(base_url=server['http']) as http:
        response = await http.get('/api/rooms')

    assert response.status_code == 200
    assert response.json() == {
        'rooms': [{
            'roomId': 'OPEN01', 'mode': 'east', 'rulesetId': 'lotus-classic',
            'capacity': 2, 'occupied': 1,
        }],
    }


def test_online_room_seeds_all_players_at_zero(fresh_rooms):
    room = RoomSession('ZERO00', mode='east', capacity=2)
    room.join_or_rejoin('甲', player_id='zero-player')

    assert [seed['score'] for seed in room._seeds()] == [0, 0, 0, 0]


def test_online_room_start_keeps_zero_score_for_legacy_rules(fresh_rooms):
    """联机还要覆盖 GameManager 对旧规则的单机起分兼容分支。"""
    room = RoomSession('ZERO01', mode='east', capacity=2, ruleset_id='lotus-legacy')
    room.join_or_rejoin('甲', player_id='legacy-zero-player')
    manager = GameManager(player_seeds=room._seeds(), rule_set=room.rules, initial_score=0)
    manager._reset_players()

    assert [player.score for player in manager.players] == [0, 0, 0, 0]
