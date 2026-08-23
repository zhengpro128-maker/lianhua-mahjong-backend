"""LLM 牌桌发言准入：性格冷却、关键台词优先与短句压缩。"""

from __future__ import annotations

import time
from typing import Callable

GLOBAL_NORMAL_COOLDOWN_S = 4.0
STYLE_NORMAL_COOLDOWN_S = {
    '话痨': 6.0,
    '激进': 8.0,
    '稳健': 12.0,
    '高冷': 16.0,
}
MAX_SPEECH_CHARS = 16


class LlmSpeechPolicy:
    def __init__(self, now: Callable[[], float] = time.monotonic):
        self._now = now
        self.reset()

    def reset(self) -> None:
        self._last_global = float('-inf')
        self._last_seat: dict[int, float] = {}

    def admit(self, seat: int, style: str, priority: str = 'normal') -> bool:
        current = self._now()
        if priority == 'important':
            self._last_global = current
            self._last_seat[seat] = current
            return True
        seat_cooldown = STYLE_NORMAL_COOLDOWN_S.get(style, STYLE_NORMAL_COOLDOWN_S['稳健'])
        if current - self._last_global < GLOBAL_NORMAL_COOLDOWN_S:
            return False
        if current - self._last_seat.get(seat, float('-inf')) < seat_cooldown:
            return False
        self._last_global = current
        self._last_seat[seat] = current
        return True


def compact_speech_text(text: str) -> str:
    normalized = ' '.join((text or '').strip().split())
    if not normalized:
        return ''
    endings = [normalized.find(mark) for mark in ('。', '！', '？', '!', '?')]
    endings = [index for index in endings if index >= 0]
    if endings:
        normalized = normalized[:min(endings) + 1]
    return normalized[:MAX_SPEECH_CHARS].strip()
