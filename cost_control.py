"""In-memory TTL caching and request throttling for quota-sensitive Google Maps endpoints.

Directions, Distance Matrix, and Place Details are billed at a much higher rate
than basic Geocoding, so their client functions are wrapped with
@cached_and_throttled. Cheap endpoints call the API directly.
"""

import functools
import json
import logging
import threading
import time

logger = logging.getLogger(__name__)

_MISS = object()


def _make_key(func_name: str, args: tuple, kwargs: dict) -> str:
    # default=str covers enums, Pydantic models, and anything else non-JSON-native
    return json.dumps([func_name, args, kwargs], sort_keys=True, default=str)


class TTLCache:
    def __init__(self, maxsize: int = 256, ttl_seconds: float = 600.0):
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return _MISS
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                del self._entries[key]
                return _MISS
            return value

    def set(self, key: str, value: object) -> None:
        with self._lock:
            now = time.monotonic()
            expired = [k for k, (exp, _) in self._entries.items() if now >= exp]
            for k in expired:
                del self._entries[k]
            while len(self._entries) >= self.maxsize:
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = (now + self.ttl_seconds, value)


class Throttle:
    """Blocks so that consecutive calls are at least min_interval_seconds apart."""

    def __init__(self, min_interval_seconds: float):
        self.min_interval_seconds = min_interval_seconds
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._last_call + self.min_interval_seconds - now
            if delay > 0:
                logger.debug("Throttling API call for %.2fs", delay)
                time.sleep(delay)
                now = time.monotonic()
            self._last_call = now


def cached_and_throttled(*, ttl_seconds: float = 600.0, maxsize: int = 256,
                         min_interval_seconds: float = 0.5):
    """Serve repeated identical calls from an in-memory TTL cache and rate-limit
    the calls that do reach the Google API."""
    def decorator(func):
        cache = TTLCache(maxsize=maxsize, ttl_seconds=ttl_seconds)
        throttle = Throttle(min_interval_seconds)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = _make_key(func.__name__, args, kwargs)
            hit = cache.get(key)
            if hit is not _MISS:
                logger.debug("Cache hit for %s", func.__name__)
                return hit
            throttle.wait()
            result = func(*args, **kwargs)
            cache.set(key, result)
            return result

        return wrapper
    return decorator
