"""TTS 服务：规范化、文本+音色缓存、single-flight、限流与负缓存。"""

import asyncio
from dataclasses import dataclass
import hashlib
import json
import re
import time
import unicodedata
from typing import Optional

from loguru import logger

from app.tts.baidu import BaiduTtsClient, TtsProviderError
from app.tts.cache import CachedAudio, TtsDiskCache
from app.tts.config import TtsConfig, TtsVoiceProfile, load_tts_config


PROVIDER_VERSION = 1
CLEANUP_INTERVAL_S = 6 * 60 * 60


@dataclass(frozen=True)
class TtsAudio:
    cache_key: str
    path: str
    cached: bool
    size_bytes: int

    @property
    def audio_url(self) -> str:
        return f'/api/tts/audio/{self.cache_key}.mp3'


def normalize_tts_text(text: str) -> str:
    value = unicodedata.normalize('NFKC', text or '')
    value = re.sub(r'\s+', ' ', value).strip()
    replacements = {
        'AI': '人工智能',
        '癞子': '赖子',
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return value[:30]


def tts_cache_key(text: str, style: str, voice: TtsVoiceProfile) -> tuple[str, str]:
    text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
    payload = {
        'provider': 'baidu',
        'providerVersion': PROVIDER_VERSION,
        'text': text,
        'voiceId': voice.voice_id,
        'style': style,
        'speed': voice.speed,
        'pitch': voice.pitch,
        'volume': voice.volume,
        'emotion': voice.emotion,
        'format': 'mp3',
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest(), text_hash


class TtsService:
    def __init__(self, config: Optional[TtsConfig] = None,
                 client: Optional[BaiduTtsClient] = None,
                 cache: Optional[TtsDiskCache] = None):
        self.config = config or load_tts_config()
        self.client = client or BaiduTtsClient(self.config)
        self.cache = cache or TtsDiskCache(
            self.config.cache_dir,
            self.config.cache_max_mb * 1024 * 1024,
            self.config.cache_ttl_days,
        )
        self._semaphore = asyncio.Semaphore(self.config.concurrency)
        self._lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Task[Optional[TtsAudio]]] = {}
        self._negative: dict[str, float] = {}
        self._last_cleanup = 0.0
        self.stats = {
            'requests': 0, 'hits': 0, 'misses': 0, 'generated': 0,
            'errors': 0, 'singleflightWaits': 0, 'negativeHits': 0,
        }

    @property
    def available(self) -> bool:
        return self.config.available

    async def close(self) -> None:
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.close()

    def stats_snapshot(self) -> dict[str, int]:
        return dict(self.stats)

    async def ensure_audio(self, text: str, style: str) -> Optional[TtsAudio]:
        if not self.available:
            return None
        normalized = normalize_tts_text(text)
        if not normalized:
            return None
        voice = self.config.voices.get(style) or self.config.voices['稳健']
        cache_key, text_hash = tts_cache_key(normalized, style, voice)
        self.stats['requests'] += 1
        cached = await self.cache.get(cache_key)
        if cached is not None:
            self.stats['hits'] += 1
            return self._result(cached)
        now = time.monotonic()
        if self._negative.get(cache_key, 0) > now:
            self.stats['negativeHits'] += 1
            return None
        creator = False
        async with self._lock:
            task = self._inflight.get(cache_key)
            if task is None:
                creator = True
                self.stats['misses'] += 1
                task = asyncio.create_task(
                    self._generate(cache_key, text_hash, normalized, style, voice))
                self._inflight[cache_key] = task
            else:
                self.stats['singleflightWaits'] += 1
        try:
            return await asyncio.shield(task)
        finally:
            if creator:
                async with self._lock:
                    if self._inflight.get(cache_key) is task:
                        self._inflight.pop(cache_key, None)

    async def _generate(self, cache_key: str, text_hash: str, text: str,
                        style: str, voice: TtsVoiceProfile) -> Optional[TtsAudio]:
        try:
            async with self._semaphore:
                audio = await self.client.synthesize(text, voice)
            cached = await self.cache.put(
                cache_key, audio, provider='baidu', voice_id=str(voice.voice_id),
                style=style, text_hash=text_hash)
            self.stats['generated'] += 1
            await self._maybe_cleanup()
            return self._result(cached)
        except (TtsProviderError, OSError, ValueError) as exc:
            self.stats['errors'] += 1
            self._negative[cache_key] = time.monotonic() + self.config.negative_ttl_s
            logger.bind(tts_error=type(exc).__name__).warning(
                f'TTS 合成失败 kind={getattr(exc, "kind", "cache")}')
            return None

    async def _maybe_cleanup(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < CLEANUP_INTERVAL_S:
            return
        self._last_cleanup = now
        result = await self.cache.cleanup()
        if result['removed']:
            logger.bind(tts_cache_cleanup=result).info('TTS 缓存清理完成')

    @staticmethod
    def _result(cached: CachedAudio) -> TtsAudio:
        return TtsAudio(
            cache_key=cached.cache_key,
            path=str(cached.path),
            cached=cached.cached,
            size_bytes=cached.size_bytes,
        )


_service: Optional[TtsService] = None


def get_tts_service() -> TtsService:
    global _service
    if _service is None:
        _service = TtsService()
    return _service


def reset_tts_service_for_tests() -> None:
    global _service
    _service = None
