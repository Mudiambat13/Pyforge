import datetime as dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyforge import cachekit, cronsched, eventbus, microweb, miniorm, pipeline, retrykit, schema, textlab, vecsearch


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class TestEventBus(unittest.TestCase):
    def test_wildcards_once_and_errors(self):
        bus, seen = eventbus.EventBus(), []
        bus.subscribe("user.*", lambda t, p: seen.append((t, p)))
        bus.once("user.created", lambda t, p: seen.append(("once", p)))
        bus.subscribe("user.created", lambda t, p: 1 / 0)
        self.assertEqual(bus.publish("user.created", 1), 3)
        self.assertEqual(bus.publish("user.created", 2), 2)
        bus.publish("order.created", 3)
        self.assertEqual(seen, [("user.created", 1), ("once", 1), ("user.created", 2)])
        self.assertEqual(len(bus.errors), 2)
        self.assertEqual(len(bus.history("user.*")), 2)

    def test_unsubscribe(self):
        bus, seen = eventbus.EventBus(), []
        off = bus.subscribe("a", lambda t, p: seen.append(p))
        bus.publish("a", 1)
        off()
        bus.publish("a", 2)
        self.assertEqual(seen, [1])


class TestCache(unittest.TestCase):
    def test_lru_and_ttl(self):
        clock = FakeClock()
        c = cachekit.TTLCache(maxsize=2, ttl=10, clock=clock)
        c.set("a", 1); c.set("b", 2); c.get("a"); c.set("c", 3)
        self.assertIsNone(c.get("b"))          # b évincé (LRU)
        self.assertEqual(c.get("a"), 1)
        clock.t = 11
        self.assertIsNone(c.get("a"))          # expiré
        self.assertEqual(c.stats()["evictions"], 1)

    def test_decorator(self):
        calls = []

        @cachekit.cached(maxsize=8)
        def square(x):
            calls.append(x)
            return x * x

        self.assertEqual([square(3), square(3), square(4)], [9, 9, 16])
        self.assertEqual(calls, [3, 4])
        self.assertEqual(square.cache_stats()["hits"], 1)


class TestRetry(unittest.TestCase):
    def test_retry_succeeds_with_backoff(self):
        waits, state = [], {"n": 0}

        @retrykit.retry(attempts=4, delay=1, backoff=2, sleep=waits.append)
        def flaky():
            state["n"] += 1
            if state["n"] < 3:
                raise ConnectionError
            return "ok"

        self.assertEqual(flaky(), "ok")
        self.assertEqual(waits, [1, 2])

    def test_retry_gives_up(self):
        @retrykit.retry(attempts=2, delay=0, sleep=lambda s: None)
        def bad():
            raise ValueError("x")
        with self.assertRaises(ValueError):
            bad()

    def test_circuit_breaker_cycle(self):
        clock = FakeClock()
        cb = retrykit.CircuitBreaker(2, 10, clock=clock)

        def fail():
            raise OSError

        for _ in range(2):
            with self.assertRaises(OSError):
                cb.call(fail)
        self.assertEqual(cb.state, "open")
        with self.assertRaises(retrykit.CircuitOpenError):
            cb.call(fail)
        clock.t = 11
        self.assertEqual(cb.state, "half_open")
        self.assertEqual(cb.call(lambda: 42), 42)
        self.assertEqual(cb.state, "closed")


class TestORM(unittest.TestCase):
    def setUp(self):
        class Item(miniorm.Model):
            name = miniorm.TextField(null=False)
            qty = miniorm.IntegerField(default=0)
            price = miniorm.RealField(default=0.0)
            tags = miniorm.JSONField()
            ok = miniorm.BooleanField(default=True)
            created = miniorm.DateTimeField(default=dt.datetime.now)

        self.Item = Item
        self.db = miniorm.Database()
        self.db.create_tables(Item)

    def test_crud_and_queries(self):
        I = self.Item
        a = I.create(name="a", qty=5, price=2.5, tags=["x", "é"])
        I.create(name="b", qty=1, price=10)
        I.create(name="c", qty=9, price=1)
        self.assertEqual(a.id, 1)
        self.assertEqual(I.get(id=1).tags, ["x", "é"])
        self.assertIsInstance(I.get(id=1).created, dt.datetime)
        self.assertIs(I.get(id=1).ok, True)
        self.assertEqual([i.name for i in I.select().where(qty__gte=5).order_by("-qty")], ["c", "a"])
        self.assertEqual(I.select().where(name__in=["a", "b"]).count(), 2)
        self.assertEqual(I.select().aggregate("SUM", "qty"), 15)
        self.assertEqual(I.select().group_aggregate("name", "SUM", "price")[0], ("b", 10.0))
        a.qty = 50
        a.save()
        self.assertEqual(I.get(id=1).qty, 50)
        a.delete()
        self.assertEqual(I.select().count(), 2)

    def test_transaction_rollback_and_safety(self):
        I = self.Item
        with self.assertRaises(RuntimeError):
            with self.db.transaction():
                I.create(name="tmp")
                raise RuntimeError
        self.assertEqual(I.select().count(), 0)
        with self.assertRaises(ValueError):
            I.select().where(**{"name; DROP TABLE items": 1})
        with self.assertRaises(TypeError):
            I(inconnu=1)


class TestTextLab(unittest.TestCase):
    def test_tokenize_and_language(self):
        self.assertEqual(textlab.tokenize("Le café de l'Université de Kolwezi", stopwords=textlab.STOPWORDS), ["café", "université", "kolwezi"])
        self.assertEqual(textlab.detect_language("Je suis dans la maison avec les enfants"), "fr")
        self.assertEqual(textlab.detect_language("This is the house of my friend"), "en")

    def test_tfidf_keywords_similarity(self):
        docs = ["le chat dort sur le canapé", "le chien joue dans le jardin", "python est un langage de programmation"]
        t = textlab.TfIdf().fit(docs)
        self.assertEqual(t.keywords("le chat chat dort", 1)[0][0], "chat")
        best = t.most_similar("programmer en python", docs, 1)[0][0]
        self.assertEqual(best, 2)

    def test_summarize(self):
        text = "Python est un langage. Python sert à tout. Il pleut. Python est populaire partout."
        self.assertLessEqual(len(textlab.split_sentences(textlab.summarize(text, 2))), 2)


class TestCron(unittest.TestCase):
    def test_parse_and_next(self):
        c = cronsched.CronExpr("*/15 9-17 * * mon-fri")
        mon = dt.datetime(2026, 10, 26, 8, 0)  # lundi
        self.assertEqual(c.next_after(mon), dt.datetime(2026, 10, 26, 9, 0))
        fri_eve = dt.datetime(2026, 10, 30, 17, 50)
        self.assertEqual(c.next_after(fri_eve), dt.datetime(2026, 11, 2, 9, 0))
        self.assertTrue(c.matches(dt.datetime(2026, 10, 27, 12, 30)))
        self.assertFalse(c.matches(dt.datetime(2026, 10, 31, 12, 30)))  # samedi

    def test_invalid(self):
        for bad in ("* * *", "61 * * * *", "a * * * *", "*/0 * * * *"):
            with self.assertRaises(ValueError):
                cronsched.CronExpr(bad)

    def test_scheduler(self):
        now = {"t": dt.datetime(2026, 1, 1, 0, 0, 0)}
        s = cronsched.Scheduler(clock=lambda: now["t"])
        out = []
        s.every(30, lambda: out.append("e"), name="e")
        s.cron("*/5 * * * *", lambda: out.append("c"), name="c")
        s.every(10, lambda: 1 / 0, name="boom")
        now["t"] += dt.timedelta(seconds=30)
        self.assertEqual(sorted(s.run_pending()), ["boom", "e"])
        now["t"] += dt.timedelta(minutes=5)
        self.assertIn("c", s.run_pending())
        self.assertIsInstance(s.jobs[2].last_error, ZeroDivisionError)


class TestPipeline(unittest.TestCase):
    def test_chain(self):
        P = pipeline.Pipeline
        self.assertEqual(P.from_iterable(range(10)).filter(lambda x: x % 2).map(lambda x: x * x).take(3).to_list(), [1, 9, 25])
        self.assertEqual(P.from_iterable(range(7)).batch(3).to_list(), [[0, 1, 2], [3, 4, 5], [6]])
        self.assertEqual(P.from_iterable([1, 1, 2, 3, 3]).distinct().to_list(), [1, 2, 3])
        self.assertEqual(P.from_iterable("aab").group_by(lambda c: c, len), {"a": 2, "b": 1})
        self.assertEqual(P.from_iterable([3, 1, 2]).sorted().to_list(), [1, 2, 3])

    def test_skip_errors_and_io(self):
        p = pipeline.Pipeline.from_iterable(["1", "x", "3"]).map(int, skip_errors=True)
        self.assertEqual(p.to_list(), [1, 3])
        self.assertEqual(len(p.errors), 1)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "o.csv")
            rows = [{"a": 1, "b": "é"}, {"a": 2, "b": "z"}]
            self.assertEqual(pipeline.Pipeline.from_iterable(rows).to_csv(path), 2)
            back = pipeline.Pipeline.from_csv(path).to_list()
            self.assertEqual(back[0], {"a": "1", "b": "é"})
            jl = os.path.join(d, "o.jsonl")
            pipeline.Pipeline.from_iterable(rows).to_jsonl(jl)
            self.assertEqual(pipeline.Pipeline.from_jsonl(jl).to_list(), rows)
        s = pipeline.Pipeline.from_iterable([1, 2, 3, 4]).stats()
        self.assertEqual((s["mean"], s["median"], s["max"]), (2.5, 2.5, 4))


class TestSchema(unittest.TestCase):
    def setUp(self):
        self.s = schema.Schema({
            "name": schema.String(min_length=2),
            "email": schema.Email(),
            "age": schema.Integer(min=0, max=130, coerce=True),
            "tags": schema.List(schema.String(), required=False, default=list),
            "address": schema.Object({"city": schema.String(), "zip": schema.String(required=False)}, required=False),
        })

    def test_valid_and_coercion(self):
        out = self.s.validate({"name": " Al ", "email": "A@B.COM", "age": "42", "address": {"city": "Kolwezi"}})
        self.assertEqual(out["name"], "Al")
        self.assertEqual(out["email"], "a@b.com")
        self.assertEqual(out["age"], 42)
        self.assertEqual(out["tags"], [])

    def test_all_errors_reported(self):
        errs = self.s.errors({"name": "A", "email": "nope", "age": "x", "tags": ["ok", 3],
                              "address": {}, "extra": 1})
        self.assertEqual(set(errs), {"name", "email", "age", "tags[1]", "address.city", "extra"})
        with self.assertRaises(schema.ValidationError):
            self.s.validate({})
        self.assertFalse(self.s.is_valid({}))

    def test_choices_and_bool(self):
        s = schema.Schema({"role": schema.String(choices=["admin", "user"]), "on": schema.Boolean(coerce=True)})
        self.assertEqual(s.validate({"role": "user", "on": "oui"}), {"role": "user", "on": True})
        self.assertIn("role", s.errors({"role": "root", "on": True}))


class TestVecSearch(unittest.TestCase):
    def test_search_typos_and_persistence(self):
        ts = vecsearch.TextSearch()
        ts.add("1", "recette de poulet à la moambe", {"t": "cuisine"})
        ts.add("2", "déboguer une erreur python en production", {"t": "dev"})
        ts.add("3", "budget mensuel et épargne", {"t": "finance"})
        self.assertEqual(ts.search("recete poulet moambé", 1)[0].id, "1")
        self.assertEqual(ts.search("erreur pyhton", 1)[0].id, "2")
        self.assertEqual([h.id for h in ts.search("erreur", 3, where=lambda m: m["t"] == "finance")], ["3"])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "i.json")
            ts.index.save(p)
            loaded = vecsearch.VectorIndex.load(p)
            self.assertEqual(len(loaded), 3)
            self.assertEqual(loaded.search(ts.embedder("budget épargne"), 1)[0].id, "3")
        self.assertTrue(ts.index.remove("3"))
        self.assertEqual(len(ts), 2)

    def test_kmeans(self):
        pts = [[0, 0], [0.1, 0], [0, 0.1], [5, 5], [5.1, 5], [5, 5.1]]
        labels, _ = vecsearch.kmeans(pts, 2)
        self.assertEqual(len(set(labels[:3])), 1)
        self.assertEqual(len(set(labels[3:])), 1)
        self.assertNotEqual(labels[0], labels[3])


class TestMicroWeb(unittest.TestCase):
    def setUp(self):
        app = microweb.App()

        @app.get("/hello/<name>")
        def hello(name):
            return {"hello": name}

        @app.get("/n/<int:n>")
        def num(n):
            return {"double": n * 2}

        @app.post("/echo")
        def echo(request):
            return request.json, 201

        @app.get("/boom")
        def boom():
            raise RuntimeError

        @app.get("/teapot")
        def teapot():
            raise microweb.HTTPError(418, "théière")

        @app.use
        def tag(request, call_next):
            resp = call_next(request)
            resp.headers["X-Test"] = "1"
            return resp

        self.c = app.test_client()

    def test_routes(self):
        self.assertEqual(self.c.get("/hello/Alice").json(), {"hello": "Alice"})
        self.assertEqual(self.c.get("/n/21").json(), {"double": 42})
        r = self.c.post("/echo", {"a": 1})
        self.assertEqual((r.status, r.json()), (201, {"a": 1}))
        self.assertEqual(r.headers["X-Test"], "1")

    def test_errors(self):
        self.assertEqual(self.c.get("/nope").status, 404)
        self.assertEqual(self.c.get("/n/abc").status, 404)
        r = self.c.get("/echo")
        self.assertEqual(r.status, 405)
        self.assertIn("POST", r.headers["Allow"])
        self.assertEqual(self.c.get("/teapot").json(), {"error": "théière"})
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.c.get("/boom").status, 500)


if __name__ == "__main__":
    unittest.main()
