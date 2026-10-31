"""Projet 2 — API de monitoring résiliente.

Modules combinés : microweb + retrykit + cachekit + cronsched + eventbus + schema

Scénario : une API expose des taux de change fournis par un service amont
instable. On protège l'appel avec retry + circuit breaker, on met le résultat
en cache, on planifie des tâches de supervision et on compte les événements.
Toute la démo tourne sans réseau grâce au client de test et à une horloge
simulée. Ajoutez --serve pour lancer un vrai serveur HTTP.

    python examples/api_monitor.py
    python examples/api_monitor.py --serve      # http://127.0.0.1:8000/health
"""
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyforge.cachekit import cached
from pyforge.cronsched import Scheduler
from pyforge.eventbus import EventBus
from pyforge.microweb import App, HTTPError
from pyforge.retrykit import CircuitBreaker, CircuitOpenError, retry
from pyforge.schema import Integer, Schema, String, ValidationError

# ---------------------------------------------------------------- horloge simulée
class Clock:
    def __init__(self):
        self.now = dt.datetime(2026, 10, 27, 8, 0, 0)

    def advance(self, **kw):
        self.now += dt.timedelta(**kw)

    def seconds(self) -> float:                 # pour cache + breaker
        return self.now.timestamp()


clock = Clock()

# ---------------------------------------------------------------- service amont instable
class Upstream:
    RATES = {"USD": 1.0, "EUR": 0.92, "CDF": 2800.0}

    def __init__(self):
        self.down = False
        self.calls = 0

    def fetch(self, currency: str) -> float:
        self.calls += 1
        if self.down:
            raise ConnectionError("service amont indisponible")
        return self.RATES[currency]


upstream = Upstream()
bus = EventBus()
breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30, clock=clock.seconds,
                         exceptions=(ConnectionError,))


@cached(ttl=30, clock=clock.seconds)
@retry(attempts=3, delay=0.5, backoff=2, exceptions=(ConnectionError,), sleep=lambda s: None,
       on_retry=lambda n, exc, wait: bus.publish("upstream.retry", {"attempt": n, "wait": wait}))
def get_rate(currency: str) -> float:
    try:
        return breaker.call(upstream.fetch, currency)
    except ConnectionError:
        bus.publish("upstream.failure", currency)
        raise


# ---------------------------------------------------------------- API
app = App()
CHECK_SCHEMA = Schema({"url": String(pattern=r"https?://\S+"), "timeout": Integer(min=1, max=30, default=5)})


@app.get("/health")
def health():
    return {"status": "ok", "breaker": breaker.state, "cache": get_rate.cache_stats()}


@app.get("/rates/<currency>")
def rates(currency: str):
    currency = currency.upper()
    if currency not in Upstream.RATES:
        raise HTTPError(404, f"Devise inconnue : {currency}")
    try:
        return {"currency": currency, "per_usd": get_rate(currency)}
    except CircuitOpenError:
        raise HTTPError(503, "Service amont isolé (circuit ouvert)")
    except ConnectionError:
        raise HTTPError(503, "Service amont indisponible")


@app.post("/check")
def check(request):
    try:
        data = CHECK_SCHEMA.validate(request.json)
    except ValidationError as exc:
        return {"errors": exc.errors}, 422
    bus.publish("check.requested", data)
    return {"queued": data}, 202


@app.get("/metrics")
def metrics():
    return dict(counters)


@app.use
def access_log(request, call_next):
    resp = call_next(request)
    bus.publish("http.request", {"path": request.path, "status": resp.status})
    return resp


# ---------------------------------------------------------------- supervision
counters: Counter = Counter()
bus.subscribe("*", lambda topic, p: counters.update([topic]))
bus.subscribe("http.request", lambda t, p: counters.update([f"status.{p['status']}"]))

scheduler = Scheduler(clock=lambda: clock.now)
scheduler.every(60, lambda: bus.publish("job.ping", breaker.state), name="ping")
scheduler.cron("*/5 * * * *", lambda: bus.publish("job.report", dict(counters)), name="rapport-5min")


def show(label: str, resp) -> None:
    print(f"  {label:<34} → {resp.status} {resp.json() if resp.body else ''}")


def main() -> None:
    client = app.test_client()
    print("1) Fonctionnement normal")
    show("GET /rates/eur", client.get("/rates/eur"))
    show("GET /rates/eur (cache)", client.get("/rates/eur"))
    show("GET /rates/xyz", client.get("/rates/xyz"))

    print("\n2) Le service amont tombe (retry x3 puis circuit ouvert)")
    upstream.down = True
    clock.advance(seconds=31)                                   # expire le cache
    show("GET /rates/eur", client.get("/rates/eur"))
    show("GET /rates/cdf (circuit ouvert)", client.get("/rates/cdf"))
    print(f"     appels amont réels : {upstream.calls} — état du circuit : {breaker.state}")

    print("\n3) Reprise : après 30 s le circuit se rétablit")
    upstream.down = False
    clock.advance(seconds=30)
    show("GET /rates/cdf (half-open → ok)", client.get("/rates/cdf"))
    print(f"     état du circuit : {breaker.state}")

    print("\n4) Validation d'entrée")
    show("POST /check valide", client.post("/check", {"url": "https://exemple.cd"}))
    show("POST /check invalide", client.post("/check", {"url": "ftp://x", "timeout": 99}))

    print("\n5) Tâches planifiées (10 minutes simulées)")
    ran = Counter()
    for _ in range(10):
        clock.advance(minutes=1)
        ran.update(scheduler.run_pending())
    print("     exécutions :", dict(ran))

    print("\n6) Métriques collectées par le bus")
    for k, v in sorted(client.get("/metrics").json().items()):
        print(f"     {k:<20} {v}")


if __name__ == "__main__":
    if "--serve" in sys.argv:
        scheduler.every(60, lambda: None, name="noop")
        clock.now = dt.datetime.now()
        app.run()
    else:
        main()
