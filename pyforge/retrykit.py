"""retrykit — Résilience : retry avec backoff exponentiel et circuit breaker.

    @retry(attempts=4, delay=0.2, backoff=2, jitter=0.1, exceptions=(ConnectionError,))
    def appel(): ...

    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
    breaker.call(appel)
"""
from __future__ import annotations

import functools
import random
import threading
import time
from typing import Any, Callable


def retry(attempts: int = 3, delay: float = 0.1, backoff: float = 2.0,
          max_delay: float = 30.0, jitter: float = 0.0,
          exceptions: tuple[type[BaseException], ...] = (Exception,),
          on_retry: Callable[[int, BaseException, float], None] | None = None,
          sleep: Callable[[float], None] = time.sleep):
    """Réessaie une fonction qui lève l'une des `exceptions`.

    L'attente vaut `delay * backoff**n` (plafonnée à `max_delay`), augmentée
    d'un bruit aléatoire de `jitter` (0.1 = jusqu'à +10 %) pour éviter que
    tous les clients réessaient au même instant. Après la dernière tentative,
    l'exception d'origine est propagée."""
    if attempts < 1:
        raise ValueError("attempts doit être >= 1")

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            wait = delay
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    if attempt == attempts:
                        raise
                    real_wait = wait * (1 + random.uniform(0, jitter)) if jitter else wait
                    if on_retry:
                        on_retry(attempt, exc, real_wait)
                    sleep(real_wait)
                    wait = min(wait * backoff, max_delay)
        return wrapper
    return decorator


class CircuitOpenError(RuntimeError):
    """Levée quand le circuit est ouvert : l'appel n'est même pas tenté."""


class CircuitBreaker:
    """États : closed (normal) → open (échecs répétés, appels refusés)
    → half_open (après `recovery_timeout`, un appel d'essai est autorisé)
    → closed si l'essai réussit, open sinon."""

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 30.0,
                 exceptions: tuple[type[BaseException], ...] = (Exception,),
                 clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.exceptions = exceptions
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.RLock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return self.CLOSED
            if self._clock() - self._opened_at >= self.recovery_timeout:
                return self.HALF_OPEN
            return self.OPEN

    def call(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        if self.state == self.OPEN:
            raise CircuitOpenError(f"circuit ouvert (échecs consécutifs : {self._failures})")
        try:
            result = fn(*args, **kwargs)
        except self.exceptions:
            self._record_failure()
            raise
        self._record_success()
        return result

    def __call__(self, fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return self.call(fn, *args, **kwargs)
        return wrapper

    def _record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._opened_at is not None or self._failures >= self.failure_threshold:
                self._opened_at = self._clock()

    def _record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def reset(self) -> None:
        self._record_success()
