"""百度 TTS、文本/音色缓存与 single-flight 测试（不访问外网）。"""

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import httpx
import pytest

from app.tts.baidu import BaiduTtsClient, TtsProviderError
from app.tts.cache import TtsDiskCache
from app.tts.config import TtsConfig, TtsVoiceProfile, load_tts_config
from app.tts.service import TtsService, normalize_tts_text, tts_cache_key
from app.local_tts.service import LocalTtsGatewayService


def config(tmp_path: Path) -> TtsConfig:
    return TtsConfig(
        enabled=True,
        api_key='api-key',
        secret_key='secret-key',
        cuid='test-cuid',
        timeout_s=2.0,
        concurrency=2,
        cache_dir=tmp_path / 'tts-cache',
        cache_max_mb=16,
        cache_ttl_days=30,
        negative_ttl_s=30.0,
        voices={
            '激进': TtsVoiceProfile(3, 7, 6, 7),
            '稳健': TtsVoiceProfile(0, 5, 5, 6),
            '话痨': TtsVoiceProfile(4, 6, 6, 7),
            '高冷': TtsVoiceProfile(1, 4, 4, 6),
        },
    )


class FakeBaiduClient:
    def __init__(self, audio: bytes = b'ID3-fake-mp3', error: Exception | None = None):
        self.audio = audio
        self.error = error
        self.calls = 0

    async def synthesize(self, text, voice):
        self.calls += 1
        await asyncio.sleep(0.01)
        if self.error:
            raise self.error
        return self.audio

    async def close(self):
        pass


def test_credential_file_is_read_without_environment(monkeypatch, tmp_path):
    credential = tmp_path / '百度api-key.txt'
    credential.write_text('API Key：abc123\nSecret Key：secret456\n', encoding='utf-8')
    monkeypatch.delenv('BAIDU_TTS_API_KEY', raising=False)
    monkeypatch.delenv('BAIDU_TTS_SECRET_KEY', raising=False)
    monkeypatch.setenv('TTS_ENABLED', 'auto')
    cfg = load_tts_config(credential)
    assert cfg.available is True
    assert cfg.api_key == 'abc123'
    assert cfg.secret_key == 'secret456'
    assert 'abc123' not in repr(cfg)


def test_invalid_optional_environment_values_fall_back(monkeypatch, tmp_path):
    monkeypatch.setenv('TTS_TIMEOUT_S', 'invalid')
    monkeypatch.setenv('TTS_CONCURRENCY', 'invalid')
    monkeypatch.setenv('TTS_CACHE_MAX_MB', 'invalid')
    monkeypatch.setenv('TTS_CACHE_TTL_DAYS', 'invalid')
    monkeypatch.setenv('TTS_NEGATIVE_TTL_S', 'invalid')
    cfg = load_tts_config(tmp_path / 'missing.txt')
    assert (cfg.timeout_s, cfg.concurrency) == (5.0, 2)
    assert (cfg.cache_max_mb, cfg.cache_ttl_days, cfg.negative_ttl_s) == (256, 30, 30.0)


def test_provider_voice_overrides_style_per_field(monkeypatch, tmp_path):
    prefixes = (
        'BAIDU_TTS_VOICE_PROVIDER_', 'BAIDU_TTS_SPEED_PROVIDER_',
        'BAIDU_TTS_PITCH_PROVIDER_', 'BAIDU_TTS_VOLUME_PROVIDER_',
    )
    for env_name in list(os.environ):
        if env_name.startswith(prefixes):
            monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv('BAIDU_TTS_VOICE_PROVIDER_DEEPSEEK', '4196')
    monkeypatch.setenv('BAIDU_TTS_SPEED_PROVIDER_DEEPSEEK', '7')
    monkeypatch.setenv('BAIDU_TTS_PITCH_PROVIDER_DEEPSEEK', '6')
    monkeypatch.setenv('BAIDU_TTS_VOLUME_PROVIDER_DEEPSEEK', '7')
    monkeypatch.setenv('BAIDU_TTS_VOICE_PROVIDER_RELAY_GPT', '4195')
    monkeypatch.setenv('BAIDU_TTS_SPEED_PROVIDER_RELAY_GPT', 'invalid')

    cfg = load_tts_config(tmp_path / 'missing.txt')
    assert cfg.voice_for('高冷', 'deepseek') == TtsVoiceProfile(4196, 7, 6, 7)
    talkative = cfg.voices['话痨']
    relay = cfg.voice_for('话痨', 'relay-gpt')
    assert relay == TtsVoiceProfile(
        4195, talkative.speed, talkative.pitch, talkative.volume,
        talkative.emotion,
    )
    assert cfg.voice_for('稳健', 'unknown') == cfg.voices['稳健']


def test_text_normalization_and_voice_are_part_of_cache_key(tmp_path):
    cfg = config(tmp_path)
    text = normalize_tts_text('  AI  打出癞子  ')
    assert text == '人工智能 打出赖子'
    steady, _ = tts_cache_key(text, '稳健', cfg.voices['稳健'])
    steady_again, _ = tts_cache_key(text, '稳健', cfg.voices['稳健'])
    cold, _ = tts_cache_key(text, '高冷', cfg.voices['高冷'])
    assert steady == steady_again
    assert steady != cold


@pytest.mark.asyncio
async def test_local_tts_gateway_has_independent_cache_and_voice_allowlist(
        monkeypatch, tmp_path):
    monkeypatch.setenv('LOCAL_TTS_CACHE_DIR', str(tmp_path / 'local-cache'))
    monkeypatch.setenv('LOCAL_TTS_ALLOWED_VOICES', 'custom_voice')
    gateway = LocalTtsGatewayService()
    try:
        assert gateway.cache.root == (tmp_path / 'local-cache').resolve()
        assert {'default', 'custom_voice'} <= gateway.allowed_voice_keys
        assert gateway.normalize_voice_key('CUSTOM-VOICE') == 'custom_voice'
        assert gateway.normalize_voice_key('../bad') is None
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_baidu_client_caches_token_and_accepts_audio_response(tmp_path):
    calls = []

    async def handler(request: httpx.Request):
        calls.append(str(request.url))
        if 'oauth' in str(request.url):
            return httpx.Response(200, json={'access_token': 'token', 'expires_in': 3600})
        return httpx.Response(200, content=b'ID3-audio', headers={'Content-Type': 'audio/mp3'})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BaiduTtsClient(config(tmp_path), http)
    assert await client.synthesize('稳住。', TtsVoiceProfile(0)) == b'ID3-audio'
    assert await client.synthesize('再来。', TtsVoiceProfile(0)) == b'ID3-audio'
    assert sum('oauth' in url for url in calls) == 1
    await http.aclose()


@pytest.mark.asyncio
async def test_baidu_json_error_is_not_treated_as_audio(tmp_path):
    async def handler(request: httpx.Request):
        if 'oauth' in str(request.url):
            return httpx.Response(200, json={'access_token': 'token', 'expires_in': 3600})
        return httpx.Response(200, json={'err_no': 500, 'err_msg': 'bad voice'})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = BaiduTtsClient(config(tmp_path), http)
    with pytest.raises(TtsProviderError):
        await client.synthesize('稳住。', TtsVoiceProfile(0))
    await http.aclose()


@pytest.mark.asyncio
async def test_disk_cache_round_trip_and_path_validation(tmp_path):
    cache = TtsDiskCache(tmp_path / 'cache', 16 * 1024 * 1024, 30)
    key = 'a' * 64
    written = await cache.put(
        key, b'ID3-cache', provider='baidu', voice_id='0', style='稳健', text_hash='b' * 64)
    assert written.cached is False and written.path.is_file()
    loaded = await cache.get(key)
    assert loaded is not None and loaded.cached is True
    assert loaded.path.read_bytes() == b'ID3-cache'
    assert cache.path_for_key('../secret') is None


@pytest.mark.asyncio
async def test_singleflight_and_persistent_cache_call_provider_once(tmp_path):
    fake = FakeBaiduClient()
    service = TtsService(config(tmp_path), client=fake)
    results = await asyncio.gather(*[
        service.ensure_audio('这一手稳住。', '稳健') for _ in range(10)
    ])
    assert fake.calls == 1
    assert all(item is not None for item in results)
    # 其余请求要么等待同一生成任务，要么在线程调度较慢时直接命中刚写入的缓存。
    assert service.stats['singleflightWaits'] + service.stats['hits'] == 9
    cached = await service.ensure_audio('这一手稳住。', '稳健')
    assert cached is not None and cached.cached is True
    assert fake.calls == 1
    await service.close()


@pytest.mark.asyncio
async def test_provider_failure_uses_negative_cache(tmp_path):
    fake = FakeBaiduClient(error=TtsProviderError('provider', 'bad'))
    service = TtsService(config(tmp_path), client=fake)
    assert await service.ensure_audio('失败。', '稳健') is None
    assert await service.ensure_audio('失败。', '稳健') is None
    assert fake.calls == 1
    assert service.stats['negativeHits'] == 1
    await service.close()


@pytest.mark.asyncio
async def test_cleanup_removes_expired_metadata_and_file(tmp_path):
    cache = TtsDiskCache(tmp_path / 'cache', 16 * 1024 * 1024, 1)
    key = 'c' * 64
    item = await cache.put(
        key, b'ID3-old', provider='baidu', voice_id='0', style='稳健', text_hash='d' * 64)
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with cache._connect() as conn:
        conn.execute('UPDATE tts_cache SET last_accessed_at=? WHERE cache_key=?', (old, key))
    result = await cache.cleanup()
    assert result['removed'] == 1
    assert not item.path.exists()
