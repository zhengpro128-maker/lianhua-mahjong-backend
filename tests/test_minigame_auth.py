import httpx
import pytest
from fastapi import FastAPI, Depends
from app.api import minigame
from app.api.deps import require_wakudemo_login


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv('WECHAT_SESSION_SECRET', 'test-secret-' * 4)
    monkeypatch.setenv('WECHAT_APP_ID', 'test-app')
    monkeypatch.setenv('WECHAT_APP_SECRET', 'never-return-this')


def test_signed_session_rejects_tampering_and_expiry(auth_env, monkeypatch):
    account = {'id': 'openid-1', 'displayName': '玩家', 'avatarUrl': ''}
    token = minigame.issue(account)
    assert minigame.verify(token)['id'] == 'openid-1'
    assert minigame.verify(token + 'x') is None
    monkeypatch.setattr(minigame.time, 'time', lambda: 99999999999)
    assert minigame.verify(token) is None


@pytest.mark.asyncio
async def test_code_exchange_and_bearer_identity(auth_env, monkeypatch):
    real_client = httpx.AsyncClient
    captured = {}
    class WechatClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, params):
            captured.update(params)
            return httpx.Response(200, json={'openid': 'verified-openid', 'session_key': 'private-key'}, request=httpx.Request('GET', url))
    app = FastAPI()
    app.include_router(minigame.router)
    @app.get('/identity')
    async def who(user=Depends(require_wakudemo_login)):
        return {'playerId': user.player_id, 'avatar': user.avatar_url}
    monkeypatch.setattr(minigame.httpx, 'AsyncClient', WechatClient)
    async with real_client(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/minigame/login', json={'code': 'one-time-code', 'profile': {'nickname': '小明', 'avatarUrl': 'https://example.com/a.png'}})
        assert response.status_code == 200
        data = response.json()
        assert captured['js_code'] == 'one-time-code'
        assert 'private-key' not in response.text and 'never-return-this' not in response.text
        identity = await client.get('/identity', headers={'Authorization': 'Bearer ' + data['sessionToken']})
        assert identity.json() == {'playerId': 'wechat-verified-openid', 'avatar': 'https://example.com/a.png'}
        denied = await client.get('/identity', headers={'Authorization': 'Bearer forged'})
        assert denied.status_code == 401


@pytest.mark.asyncio
async def test_unconfigured_login_fails_closed(monkeypatch):
    monkeypatch.delenv('WECHAT_SESSION_SECRET', raising=False)
    app = FastAPI(); app.include_router(minigame.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        result = await client.post('/api/minigame/login', json={'code': 'code'})
        assert result.status_code == 503


@pytest.mark.asyncio
async def test_four_wechat_players_complete_wuhan_match(server, fresh_rooms, auth_env):
    import asyncio
    import json
    import websockets.asyncio.client
    from app.game.room import room_registry

    tokens = [minigame.issue({'id': f'wx-{i}', 'displayName': f'微信{i}', 'avatarUrl': f'https://example.com/{i}.png'}) for i in range(4)]
    sockets = []
    async with httpx.AsyncClient(base_url=server['http']) as client:
        created = await client.post('/api/rooms', headers={'Authorization': 'Bearer ' + tokens[0]},
                                    json={'mode': 'east', 'capacity': 4, 'rulesetId': 'wuhan-huanghuang'})
        assert created.status_code == 200, created.text
        room_id = created.json()['roomId']
        room = room_registry.get(room_id)
        room.pace = None
        try:
            for i in range(4):
                joined = await client.post(f'/api/rooms/{room_id}/join', headers={'Authorization': 'Bearer ' + tokens[i]}, json={'nickname': f'微信{i}'})
                assert joined.status_code == 200, joined.text
                seat = joined.json()
                assert room.seats[seat['seat']].avatar == f'https://example.com/{i}.png'
                assert room.seats[seat['seat']].player_id == f'wechat-wx-{i}'
                ready = await client.post(f'/api/rooms/{room_id}/ready', json={'seat': seat['seat'], 'rejoinCode': seat['rejoinCode'], 'ready': True})
                assert ready.status_code == 200
                socket = await websockets.asyncio.client.connect(f"{server['ws']}/ws/room/{room_id}?rejoin_code={seat['rejoinCode']}")
                sockets.append(socket)
                hello = json.loads(await socket.recv())
                assert hello['kind'] == 'rejoin_ok'
            started = await client.post(f'/api/rooms/{room_id}/start')
            assert started.status_code == 200, started.text
            async def play(socket):
                while True:
                    message = json.loads(await asyncio.wait_for(socket.recv(), 15))
                    kind = message.get('kind')
                    if kind == 'turn_request':
                        await socket.send(json.dumps({'type': 'discard', 'handIndex': 0}))
                    elif kind in ('claim_request', 'rob_kong_request'):
                        await socket.send(json.dumps({'type': 'pass'}))
                    elif kind == 'hand_result':
                        await socket.send(json.dumps({'type': 'continue', 'presentationKey': message['result']['presentationKey']}))
                    elif kind == 'match_finished':
                        return message['finalScores']
            results = await asyncio.wait_for(asyncio.gather(*(play(s) for s in sockets)), 60)
            assert all(sum(p['score'] for p in result) == 0 for result in results)
            assert room.status == 'finished'
        finally:
            for socket in sockets:
                await socket.close()


def test_jade_client_early_continue_is_not_lost():
    from types import SimpleNamespace
    from app.game.room import RoomSession
    room = RoomSession('JADE-CONTINUE', mode='east', capacity=4)
    room.manager = SimpleNamespace(phase='settled', result={'winnerIndex': 0}, round=1, honba=0)
    key = room._settlement_presentation_key()
    room._confirm_continue(0, 'stale-key')
    assert not room._early_continue_confirmed
    room._confirm_continue(0, key)
    assert room._early_continue_confirmed == {key: {0}}
