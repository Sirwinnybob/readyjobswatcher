import threading
from typing import Callable, Optional


class DebouncedTimer:
    """Single-key cancel-replace debounce timer."""

    def __init__(self, name: str = "DebouncedTimer"):
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._name = name

    def schedule(self, delay: float, callback: Callable, *args, **kwargs) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(delay, callback, args=args, kwargs=kwargs)
            self._timer.name = self._name
            self._timer.daemon = True
            self._timer.start()

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class DebouncedTimerMap:
    """Multi-key cancel-replace debounce timer map.

    Each key gets its own independent timer. Scheduling a key cancels any
    existing timer for that key before starting a new one.
    """

    def __init__(self, name_prefix: str = "DebouncedTimerMap"):
        self._timers: dict = {}
        self._lock = threading.Lock()
        self._name_prefix = name_prefix

    def schedule(self, key: str, delay: float, callback: Callable, *, auto_remove: bool = True) -> None:
        def _wrapper():
            try:
                callback()
            finally:
                if auto_remove:
                    with self._lock:
                        self._timers.pop(key, None)

        with self._lock:
            existing = self._timers.get(key)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(delay, _wrapper)
            timer.name = f"{self._name_prefix}-{key}"
            timer.daemon = True
            self._timers[key] = timer
            timer.start()

    def cancel(self, key: str) -> None:
        with self._lock:
            existing = self._timers.pop(key, None)
            if existing is not None:
                existing.cancel()

    def cancel_all(self) -> None:
        with self._lock:
            for timer in self._timers.values():
                try:
                    timer.cancel()
                except Exception:
                    pass
            self._timers.clear()
