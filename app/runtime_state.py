"""Process-local lifecycle state used by health checks and room admission."""

from __future__ import annotations

import threading


class RuntimeState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # ASGI embedding and tests may not run lifespan. Admission stays open
        # until the process explicitly begins its shutdown sequence.
        self._ready = True
        self._lifespan_active = False

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready if self._lifespan_active else True

    def mark_ready(self) -> None:
        with self._lock:
            self._lifespan_active = True
            self._ready = True

    def begin_shutdown(self) -> None:
        with self._lock:
            self._ready = False

    def complete_shutdown(self) -> None:
        """Allow the ASGI app to be started again in the same interpreter."""
        with self._lock:
            self._lifespan_active = False


runtime_state = RuntimeState()
