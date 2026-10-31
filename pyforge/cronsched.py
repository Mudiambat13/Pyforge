"""cronsched — Expressions cron (5 champs) et ordonnanceur de tâches.

    CronExpr("*/15 8-18 * * mon-fri").next_after(datetime.now())

    sched = Scheduler()
    sched.every(30, ma_fonction)
    sched.cron("0 9 * * 1", rapport_hebdo)
    sched.start()        # thread d'arrière-plan   (ou run_pending() dans votre boucle)
"""
from __future__ import annotations

import datetime as dt
import threading
from typing import Any, Callable

_MONTHS = {n: i for i, n in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_DAYS = {n: i for i, n in enumerate("sun mon tue wed thu fri sat".split())}


def _parse_field(spec: str, lo: int, hi: int, names: dict[str, int] | None = None) -> set[int]:
    def conv(tok: str) -> int:
        tok = tok.lower()
        if names and tok in names:
            return names[tok]
        if not tok.isdigit():
            raise ValueError(f"Valeur cron invalide : {tok!r}")
        return int(tok)

    values: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise ValueError(f"Pas invalide : {step_s!r}")
            step = int(step_s)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = conv(a), conv(b)
        else:
            start = conv(part)
            end = hi if step > 1 else start
        if start < lo or end > hi or start > end:
            raise ValueError(f"Hors limites [{lo}-{hi}] : {part!r}")
        values.update(range(start, end + 1, step))
    return values


class CronExpr:
    """minute heure jour-du-mois mois jour-de-semaine (0-7, 0 et 7 = dimanche).
    Supporte `*`, listes `a,b`, plages `a-b`, pas `*/n` et noms (jan, mon…).
    Si jour-du-mois ET jour-de-semaine sont restreints, l'un OU l'autre suffit
    (comportement cron standard)."""

    def __init__(self, expr: str):
        fields = expr.split()
        if len(fields) != 5:
            raise ValueError("Une expression cron comporte 5 champs")
        self.expr = expr
        self.minutes = _parse_field(fields[0], 0, 59)
        self.hours = _parse_field(fields[1], 0, 23)
        self.days = _parse_field(fields[2], 1, 31)
        self.months = _parse_field(fields[3], 1, 12, _MONTHS)
        self.weekdays = {d % 7 for d in _parse_field(fields[4], 0, 7, _DAYS)}
        self._dom_star = fields[2].startswith("*")
        self._dow_star = fields[4].startswith("*")

    def _day_ok(self, d: dt.datetime) -> bool:
        dom_ok = d.day in self.days
        dow_ok = (d.weekday() + 1) % 7 in self.weekdays
        if self._dom_star and self._dow_star:
            return True
        if self._dom_star:
            return dow_ok
        if self._dow_star:
            return dom_ok
        return dom_ok or dow_ok

    def matches(self, when: dt.datetime) -> bool:
        return (when.minute in self.minutes and when.hour in self.hours
                and when.month in self.months and self._day_ok(when))

    def next_after(self, after: dt.datetime) -> dt.datetime:
        """Prochaine occurrence strictement postérieure à `after`."""
        t = after.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
        limit = after + dt.timedelta(days=366 * 5)
        while t < limit:
            if t.month not in self.months:
                t = (t.replace(day=1, hour=0, minute=0) + dt.timedelta(days=32)).replace(day=1)
            elif not self._day_ok(t):
                t = t.replace(hour=0, minute=0) + dt.timedelta(days=1)
            elif t.hour not in self.hours:
                t = t.replace(minute=0) + dt.timedelta(hours=1)
            elif t.minute not in self.minutes:
                t += dt.timedelta(minutes=1)
            else:
                return t
        raise ValueError(f"Aucune occurrence pour {self.expr!r}")


class Job:
    def __init__(self, name: str, func: Callable[[], Any], interval: float | None,
                 cron: CronExpr | None, now: dt.datetime):
        self.name, self.func, self.interval, self.cron = name, func, interval, cron
        self.runs = 0
        self.last_error: Exception | None = None
        self.last_result: Any = None
        self.next_run = self._compute_next(now)

    def _compute_next(self, now: dt.datetime) -> dt.datetime:
        if self.cron:
            return self.cron.next_after(now)
        return now + dt.timedelta(seconds=self.interval or 0)

    def __repr__(self) -> str:
        return f"Job({self.name!r}, next={self.next_run:%Y-%m-%d %H:%M:%S}, runs={self.runs})"


class Scheduler:
    """`clock` est injectable : les tests et démos peuvent simuler le temps."""

    def __init__(self, clock: Callable[[], dt.datetime] = dt.datetime.now):
        self._clock = clock
        self.jobs: list[Job] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _add(self, func, name, interval, cron):
        def register(fn):
            job = Job(name or fn.__name__, fn, interval, cron, self._clock())
            with self._lock:
                self.jobs.append(job)
            return fn
        return register(func) if func is not None else register

    def every(self, seconds: float, func: Callable | None = None, name: str | None = None):
        """Exécute `func` toutes les `seconds` secondes. Utilisable en décorateur."""
        return self._add(func, name, seconds, None)

    def cron(self, expr: str, func: Callable | None = None, name: str | None = None):
        """Exécute `func` selon l'expression cron. Utilisable en décorateur."""
        return self._add(func, name, None, CronExpr(expr))

    def run_pending(self, now: dt.datetime | None = None) -> list[str]:
        """Exécute les tâches échues, retourne leurs noms."""
        now = now or self._clock()
        with self._lock:
            due = [j for j in self.jobs if j.next_run <= now]
        ran = []
        for job in due:
            try:
                job.last_result = job.func()
                job.last_error = None
            except Exception as exc:  # noqa: BLE001
                job.last_error = exc
            job.runs += 1
            job.next_run = job._compute_next(now)
            ran.append(job.name)
        return ran

    def start(self, tick: float = 1.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(tick):
                self.run_pending()

        self._thread = threading.Thread(target=loop, daemon=True, name="cronsched")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
