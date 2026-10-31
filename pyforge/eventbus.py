"""eventbus — Bus d'événements publish/subscribe avec jokers (wildcards).

    bus = EventBus()

    @bus.subscribe("order.*")
    def on_order(topic, payload): ...

    bus.publish("order.created", {"id": 1})
"""
from __future__ import annotations

import fnmatch
import threading
from typing import Any, Callable

Handler = Callable[[str, Any], None]


class _Sub:
    __slots__ = ("handler", "once")

    def __init__(self, handler: Handler, once: bool):
        self.handler = handler
        self.once = once


class EventBus:
    """Bus synchrone, thread-safe. Les motifs utilisent la syntaxe fnmatch
    (`*`, `?`, `[abc]`) : `user.*` reçoit `user.created`, `*` reçoit tout."""

    def __init__(self, keep_history: int = 100):
        self._subs: dict[str, list[_Sub]] = {}
        self._lock = threading.RLock()
        self._history: list[tuple[str, Any]] = []
        self._keep = keep_history
        self.errors: list[tuple[str, Handler, Exception]] = []

    # -- abonnement -------------------------------------------------------
    def subscribe(self, pattern: str, handler: Handler | None = None, once: bool = False):
        """Abonne `handler` à `pattern`. Utilisable comme décorateur.
        Retourne une fonction de désabonnement quand `handler` est fourni."""
        if handler is None:
            def decorator(fn: Handler) -> Handler:
                self.subscribe(pattern, fn, once=once)
                return fn
            return decorator
        with self._lock:
            self._subs.setdefault(pattern, []).append(_Sub(handler, once))
        return lambda: self.unsubscribe(pattern, handler)

    def once(self, pattern: str, handler: Handler | None = None):
        """Comme subscribe, mais le handler n'est appelé qu'une seule fois."""
        return self.subscribe(pattern, handler, once=True)

    def unsubscribe(self, pattern: str, handler: Handler) -> None:
        with self._lock:
            subs = [s for s in self._subs.get(pattern, []) if s.handler is not handler]
            if subs:
                self._subs[pattern] = subs
            else:
                self._subs.pop(pattern, None)

    # -- publication ------------------------------------------------------
    def publish(self, topic: str, payload: Any = None) -> int:
        """Publie un événement. Retourne le nombre de handlers appelés.
        Une exception dans un handler n'interrompt pas les autres : elle est
        consignée dans `bus.errors`."""
        with self._lock:
            self._history.append((topic, payload))
            del self._history[: max(0, len(self._history) - self._keep)]
            matched = [
                (pattern, sub)
                for pattern, subs in self._subs.items()
                if fnmatch.fnmatchcase(topic, pattern)
                for sub in subs
            ]
            for pattern, sub in matched:
                if sub.once:
                    self._subs[pattern].remove(sub)
                    if not self._subs[pattern]:
                        del self._subs[pattern]
        for _, sub in matched:
            try:
                sub.handler(topic, payload)
            except Exception as exc:  # noqa: BLE001
                self.errors.append((topic, sub.handler, exc))
        return len(matched)

    def history(self, pattern: str = "*") -> list[tuple[str, Any]]:
        """Derniers événements publiés correspondant au motif."""
        with self._lock:
            return [(t, p) for t, p in self._history if fnmatch.fnmatchcase(t, pattern)]

    def listeners(self, topic: str) -> int:
        with self._lock:
            return sum(len(s) for p, s in self._subs.items() if fnmatch.fnmatchcase(topic, p))
