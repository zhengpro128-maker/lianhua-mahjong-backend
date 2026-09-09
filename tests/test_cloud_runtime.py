"""Cloud-hosting lifecycle and production database configuration."""

import httpx
import pytest

from app.runtime_state import runtime_state
from app.storage.db import postgres_dsn


@pytest.mark.asyncio
async def test_health_and_readiness_are_separate(server):
    async with httpx.AsyncClient(base_url=server['http'], trust_env=False) as http:
        assert (await http.get('/api/health')).json() == {'status': 'ok'}
        assert (await http.get('/api/ready')).json() == {'status': 'ready'}


@pytest.mark.asyncio
async def test_draining_instance_rejects_new_rooms(server, fresh_rooms):
    runtime_state.begin_shutdown()
    try:
        async with httpx.AsyncClient(base_url=server['http'], trust_env=False) as http:
            ready = await http.get('/api/ready')
            assert ready.status_code == 503
            assert ready.json() == {'status': 'draining'}
            created = await http.post('/api/rooms', json={
                'mode': 'east', 'capacity': 4,
            })
            assert created.status_code == 503
            assert created.json()['detail']['code'] == 'SERVICE_DRAINING'
    finally:
        runtime_state.mark_ready()


def test_database_url_takes_precedence(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://cloud/example')
    monkeypatch.setenv('PG_PASSWORD', 'ignored')
    assert postgres_dsn() == 'postgresql://cloud/example'


def test_split_postgres_config_supports_tls(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('PG_USER', 'cloud user')
    monkeypatch.setenv('PG_PASSWORD', 'secret/value')
    monkeypatch.setenv('PG_HOST', 'db.internal')
    monkeypatch.setenv('PG_PORT', '5432')
    monkeypatch.setenv('PG_DATABASE', 'mahjong')
    monkeypatch.setenv('PG_SSLMODE', 'verify-full')
    assert postgres_dsn() == (
        'postgresql://cloud%20user:secret%2Fvalue@db.internal:5432/mahjong'
        '?sslmode=verify-full'
    )


def test_production_can_forbid_sqlite_fallback(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('PG_PASSWORD', raising=False)
    monkeypatch.setenv('REQUIRE_POSTGRES', 'true')
    with pytest.raises(RuntimeError, match='DATABASE_URL/PG_PASSWORD is missing'):
        postgres_dsn()
