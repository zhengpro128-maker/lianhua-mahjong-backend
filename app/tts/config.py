"""TTS 配置：环境变量优先，本地凭据文件仅作开发机回退。"""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Optional


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CREDENTIAL_FILE = BACKEND_ROOT / 'docs' / '百度api-key.txt'
TTS_STYLES = ('激进', '稳健', '话痨', '高冷')


@dataclass(frozen=True)
class TtsVoiceProfile:
    voice_id: int
    speed: int = 5
    pitch: int = 5
    volume: int = 6
    emotion: str = ''


@dataclass(frozen=True)
class TtsVoiceOverride:
    """按 LLM providerId 覆盖音色参数；未配置字段继续继承策略配置。"""
    voice_id: Optional[int] = None
    speed: Optional[int] = None
    pitch: Optional[int] = None
    volume: Optional[int] = None


@dataclass(frozen=True, repr=False)
class TtsConfig:
    enabled: bool
    api_key: str
    secret_key: str
    cuid: str
    timeout_s: float
    concurrency: int
    cache_dir: Path
    cache_max_mb: int
    cache_ttl_days: int
    negative_ttl_s: float
    voices: dict[str, TtsVoiceProfile]
    provider_voices: dict[str, TtsVoiceOverride] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.api_key and self.secret_key)

    def voice_for(self, style: str, provider_id: str = '') -> TtsVoiceProfile:
        """provider 专属字段优先，缺失字段回退当前策略，再回退稳健策略。"""
        base = self.voices.get(style) or self.voices['稳健']
        provider_key = provider_id.strip().lower().replace('-', '_')
        override = self.provider_voices.get(provider_key)
        if override is None:
            return base
        return TtsVoiceProfile(
            voice_id=base.voice_id if override.voice_id is None else override.voice_id,
            speed=base.speed if override.speed is None else override.speed,
            pitch=base.pitch if override.pitch is None else override.pitch,
            volume=base.volume if override.volume is None else override.volume,
            emotion=base.emotion,
        )


def _clamp_int(value: str | int, minimum: int, maximum: int, default: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _clamp_float(value: str | float, minimum: float, maximum: float,
                 default: float) -> float:
    try:
        return max(minimum, min(maximum, float(value)))
    except (TypeError, ValueError):
        return default


def _int_at_least(value: str | int, minimum: int, default: int) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def _optional_clamped_int(value: Optional[str], minimum: int,
                          maximum: int) -> Optional[int]:
    if value is None or not value.strip():
        return None
    try:
        return max(minimum, min(maximum, int(value)))
    except ValueError:
        return None


def _read_credential_file(path: Path) -> tuple[str, str]:
    """读取两行 API Key/Secret Key；永不记录文件内容。"""
    try:
        entries = {}
        for raw in path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line:
                continue
            separator = next((item for item in (':', '：', '=') if item in line), None)
            if separator is None:
                continue
            label, value = line.split(separator, 1)
            entries[label.strip().lower().replace(' ', '_')] = value.strip()
        return entries.get('api_key', ''), entries.get('secret_key', '')
    except OSError:
        return '', ''


def _voice(style: str, defaults: tuple[int, int, int, int]) -> TtsVoiceProfile:
    prefix = {
        '激进': 'AGGRESSIVE', '稳健': 'STEADY',
        '话痨': 'TALKATIVE', '高冷': 'COLD',
    }[style]
    voice_id, speed, pitch, volume = defaults
    emotion = os.environ.get(f'BAIDU_TTS_EMOTION_{prefix}', '').strip()
    if emotion not in ('', 'neutral', 'happy', 'down', 'angry', 'surprise', 'fear'):
        emotion = ''
    return TtsVoiceProfile(
        voice_id=_clamp_int(os.environ.get(f'BAIDU_TTS_VOICE_{prefix}', voice_id), 0, 99999, voice_id),
        speed=_clamp_int(os.environ.get(f'BAIDU_TTS_SPEED_{prefix}', speed), 0, 15, speed),
        pitch=_clamp_int(os.environ.get(f'BAIDU_TTS_PITCH_{prefix}', pitch), 0, 15, pitch),
        volume=_clamp_int(os.environ.get(f'BAIDU_TTS_VOLUME_{prefix}', volume), 0, 15, volume),
        emotion=emotion,
    )


def _provider_voices() -> dict[str, TtsVoiceOverride]:
    """扫描 BAIDU_TTS_<FIELD>_PROVIDER_<ID> 动态提供商配置。"""
    prefixes = {
        'BAIDU_TTS_VOICE_PROVIDER_': ('voice_id', 0, 99999),
        'BAIDU_TTS_SPEED_PROVIDER_': ('speed', 0, 15),
        'BAIDU_TTS_PITCH_PROVIDER_': ('pitch', 0, 15),
        'BAIDU_TTS_VOLUME_PROVIDER_': ('volume', 0, 15),
    }
    entries: dict[str, dict[str, int]] = {}
    for env_name, raw_value in os.environ.items():
        for prefix, (field_name, minimum, maximum) in prefixes.items():
            if not env_name.startswith(prefix):
                continue
            provider_id = env_name[len(prefix):].strip().lower()
            if not provider_id or not provider_id.replace('_', '').isalnum():
                break
            value = _optional_clamped_int(raw_value, minimum, maximum)
            if value is not None:
                entries.setdefault(provider_id, {})[field_name] = value
            break
    return {
        provider_id: TtsVoiceOverride(**values)
        for provider_id, values in entries.items()
    }


def load_tts_config(credential_file: Optional[Path] = None) -> TtsConfig:
    file_path = credential_file or Path(os.environ.get(
        'BAIDU_TTS_CREDENTIAL_FILE', str(DEFAULT_CREDENTIAL_FILE)))
    file_api_key, file_secret_key = _read_credential_file(file_path)
    api_key = os.environ.get('BAIDU_TTS_API_KEY', '').strip() or file_api_key
    secret_key = os.environ.get('BAIDU_TTS_SECRET_KEY', '').strip() or file_secret_key
    enabled_raw = os.environ.get('TTS_ENABLED', 'auto').strip().lower()
    enabled = bool(api_key and secret_key) if enabled_raw == 'auto' else enabled_raw == 'true'
    cache_dir = Path(os.environ.get(
        'TTS_CACHE_DIR', str(BACKEND_ROOT / 'data' / 'tts-cache'))).resolve()
    voices = {
        # 默认均使用基础音库，部署时可在试听后用环境变量覆盖。
        '激进': _voice('激进', (3, 7, 6, 7)),
        '稳健': _voice('稳健', (0, 5, 5, 6)),
        '话痨': _voice('话痨', (4, 6, 6, 7)),
        '高冷': _voice('高冷', (1, 4, 4, 6)),
    }
    return TtsConfig(
        enabled=enabled,
        api_key=api_key,
        secret_key=secret_key,
        cuid=os.environ.get('BAIDU_TTS_CUID', 'lianhua-mahjong-server')[:60],
        timeout_s=_clamp_float(os.environ.get('TTS_TIMEOUT_S', '5'), 1.0, 20.0, 5.0),
        concurrency=_clamp_int(os.environ.get('TTS_CONCURRENCY', '2'), 1, 3, 2),
        cache_dir=cache_dir,
        cache_max_mb=_int_at_least(os.environ.get('TTS_CACHE_MAX_MB', '256'), 16, 256),
        cache_ttl_days=_int_at_least(os.environ.get('TTS_CACHE_TTL_DAYS', '30'), 1, 30),
        negative_ttl_s=_clamp_float(
            os.environ.get('TTS_NEGATIVE_TTL_S', '30'), 1.0, 300.0, 30.0),
        voices=voices,
        provider_voices=_provider_voices(),
    )
