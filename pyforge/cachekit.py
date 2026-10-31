"""cachekit — Cache LRU avec expiration (TTL), thread-safe, avec statistiques.

    @cached(maxsize=256, ttl=60)
    def lent(x): ...

    lent.cache_stats()  # {'hits': .., 'misses': .., 'hit_rate': ..}
"""
from __future__ import annotations

import functools
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

_MISSING = object()


class TTLCache:
    """Cache LRU borné, avec TTL global ou par entrée.
    `clock` est injectable (pratique pour tester sans `sleep`)."""

    def __init__(self, maxsize: int = 128, ttl: float | None = None,
                 clock: Callable[[], float] = time.monotonic):
        if maxsize < 1:
            raise ValueError("maxsize doit être >= 1")
        self.maxsize, self.ttl, self._clock = maxsize, ttl, clock
        self._data: OrderedDict[Any, tuple[Any, float | None]] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = self.misses = self.evictions = 0

    def _expired(self, expires: float | None) -> bool:
        return expires is not None and self._clock() >= expires

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            item = self._data.get(key, _MISSING)
            if item is _MISSING:
                self.misses += 1
                return default
            value, expires = item
            if self._expired(expires):
                del self._data[key]
                self.misses += 1
                return default
            self._data.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: Any, value: Any, ttl: float | None = None) -> None:
        ttl = self.ttl if ttl is None else ttl
        expires = self._clock() + ttl if ttl is not None else None
        with self._lock:
            self._data[key] = (value, expires)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)
                self.evictions += 1

    def delete(self, key: Any) -> bool:
        with self._lock:
            return self._data.pop(key, _MISSING) is not _MISSING

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.hits = self.misses = self.evictions = 0

    def purge_expired(self) -> int:
        """Supprime les entrées expirées, retourne leur nombre."""
        with self._lock:
            dead = [k for k, (_, e) in self._data.items() if self._expired(e)]
            for k in dead:
                del self._data[k]
            return len(dead)

    def __contains__(self, key: Any) -> bool:
        with self._lock:
            item = self._data.get(key, _MISSING)
            return item is not _MISSING and not self._expired(item[1])

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        return {
            "size": len(self), "maxsize": self.maxsize, "hits": self.hits,
            "misses": self.misses, "evictions": self.evictions,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }


def cached(maxsize: int = 128, ttl: float | None = None,
           clock: Callable[[], float] = time.monotonic):
    """Décorateur de mémoïsation avec LRU + TTL. Arguments hashables requis.
    Les exceptions ne sont jamais mises en cache."""
    def decorator(fn: Callable) -> Callable:
        cache = TTLCache(maxsize, ttl, clock)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            value = cache.get(key, _MISSING)
            if value is _MISSING:
                value = fn(*args, **kwargs)
                cache.set(key, value)
            return value

        wrapper.cache = cache
        wrapper.cache_clear = cache.clear
        wrapper.cache_stats = cache.stats
        return wrapper
    return decorator
