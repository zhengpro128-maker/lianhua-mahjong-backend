"""单机 TTS 网关服务：独立缓存、音色白名单与百度合成实例。"""

from dataclasses import replace
import os
from pathlib import Path
from typing import Optional

from app.tts.service import TtsAudio, TtsService
from app.tts.config import BACKEND_ROOT, load_tts_config


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.environ.get(name, str(default)))))
    except ValueError:
        return default


class LocalTtsGatewayService:
    """与联机 RoomSession 完全解耦的单机语音服务。"""

    def __init__(self):
        base = load_tts_config()
        enabled_raw = os.environ.get('LOCAL_TTS_ENABLED', 'auto').strip().lower()
        enabled = base.available if enabled_raw == 'auto' else enabled_raw == 'true'
        cache_dir = Path(os.environ.get(
            'LOCAL_TTS_CACHE_DIR', str(BACKEND_ROOT / 'data' / 'local-tts-cache'))).resolve()
        config = replace(
            base,
            enabled=enabled,
            cache_dir=cache_dir,
            cache_max_mb=_bounded_int('LOCAL_TTS_CACHE_MAX_MB', 128, 16, 2048),
            cache_ttl_days=_bounded_int('LOCAL_TTS_CACHE_TTL_DAYS', 30, 1, 365),
        )
        self.tts = TtsService(config)
        configured = {
            item.strip().lower().replace('-', '_')
            for item in os.environ.get('LOCAL_TTS_ALLOWED_VOICES', '').split(',')
            if item.strip()
        }
        self.allowed_voice_keys = frozenset({'default', *config.provider_voices, *configured})
        self.rate_limit_per_minute = _bounded_int(
            'LOCAL_TTS_RATE_LIMIT_PER_MINUTE', 60, 1, 600)

    @property
    def available(self) -> bool:
        return self.tts.available

    @property
    def cache(self):
        return self.tts.cache

    def normalize_voice_key(self, voice_key: str) -> Optional[str]:
        normalized = voice_key.strip().lower().replace('-', '_')
        return normalized if normalized in self.allowed_voice_keys else None

    async def ensure_audio(self, text: str, voice_key: str, style: str) -> Optional[TtsAudio]:
        normalized = self.normalize_voice_key(voice_key)
        if normalized is None:
            return None
        provider_id = '' if normalized == 'default' else normalized
        return await self.tts.ensure_audio(text, style, provider_id)

    async def close(self) -> None:
        await self.tts.close()


_service: Optional[LocalTtsGatewayService] = None


def get_local_tts_service() -> LocalTtsGatewayService:
    global _service
    if _service is None:
        _service = LocalTtsGatewayService()
    return _service


def reset_local_tts_service_for_tests() -> None:
    global _service
    _service = None
