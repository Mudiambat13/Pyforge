"""microweb — Micro-framework web WSGI : routage, middlewares, JSON, client de test.

    app = App()

    @app.get("/hello/<name>")
    def hello(name):
        return {"message": f"Bonjour {name}"}

    @app.post("/items")
    def create(request):
        return request.json, 201

    app.run(port=8000)                         # serveur de développement
    app.test_client().get("/hello/Alice")      # tests sans réseau
"""
from __future__ import annotations

import inspect
import io
import json
import re
import sys
import traceback
from http import HTTPStatus
from typing import Any, Callable
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server


class HTTPError(Exception):
    def __init__(self, status: int = 500, message: str | None = None):
        self.status = status
        self.message = message or HTTPStatus(status).phrase
        super().__init__(f"{status} {self.message}")


class Request:
    def __init__(self, method: str, path: str, query: dict[str, str],
                 headers: dict[str, str], body: bytes):
        self.method, self.path, self.query = method, path, query
        self.headers, self.body = headers, body
        self.params: dict[str, Any] = {}
        self.context: dict[str, Any] = {}  # espace libre pour les middlewares
        self._json: Any = None

    @property
    def json(self) -> Any:
        if self._json is None:
            try:
                self._json = json.loads(self.body or b"null")
            except ValueError:
                raise HTTPError(400, "JSON invalide") from None
        return self._json


class Response:
    def __init__(self, body: bytes | str = b"", status: int = 200,
                 headers: dict[str, str] | None = None,
                 content_type: str = "text/plain; charset=utf-8"):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = {"Content-Type": content_type, **(headers or {})}

    @classmethod
    def json(cls, data: Any, status: int = 200, headers: dict[str, str] | None = None) -> "Response":
        return cls(json.dumps(data, ensure_ascii=False, default=str), status, headers,
                   "application/json; charset=utf-8")


_CONVERTERS = {
    "str": (r"[^/]+", str),
    "int": (r"-?\d+", int),
    "float": (r"-?\d+(?:\.\d+)?", float),
    "path": (r".+", str),
}
_PARAM_RE = re.compile(r"<(?:(\w+):)?(\w+)>")


def _compile(rule: str) -> tuple[re.Pattern, dict[str, Callable]]:
    casts: dict[str, Callable] = {}
    pos, regex = 0, ""
    for m in _PARAM_RE.finditer(rule):
        conv, name = m.group(1) or "str", m.group(2)
        if conv not in _CONVERTERS:
            raise ValueError(f"Convertisseur inconnu : {conv!r}")
        pattern, cast = _CONVERTERS[conv]
        regex += re.escape(rule[pos:m.start()]) + f"(?P<{name}>{pattern})"
        casts[name] = cast
        pos = m.end()
    regex += re.escape(rule[pos:])
    return re.compile(f"^{regex}$"), casts


class App:
    def __init__(self):
        self._routes: list[tuple[re.Pattern, dict, set[str], Callable]] = []
        self._middleware: list[Callable] = []
        self._error_handlers: dict[int, Callable] = {}

    # -- déclaration
    def route(self, rule: str, methods: tuple[str, ...] | list[str] = ("GET",)):
        pattern, casts = _compile(rule)

        def decorator(fn: Callable) -> Callable:
            self._routes.append((pattern, casts, {m.upper() for m in methods}, fn))
            return fn
        return decorator

    def get(self, rule: str):
        return self.route(rule, ("GET",))

    def post(self, rule: str):
        return self.route(rule, ("POST",))

    def put(self, rule: str):
        return self.route(rule, ("PUT",))

    def delete(self, rule: str):
        return self.route(rule, ("DELETE",))

    def use(self, middleware: Callable[[Request, Callable[[Request], Response]], Response]):
        """Middleware : `fn(request, call_next) -> Response`. Utilisable en décorateur."""
        self._middleware.append(middleware)
        return middleware

    def errorhandler(self, status: int):
        def decorator(fn: Callable[[HTTPError], Any]) -> Callable:
            self._error_handlers[status] = fn
            return fn
        return decorator

    # -- exécution
    @staticmethod
    def _to_response(result: Any) -> Response:
        status = None
        if isinstance(result, tuple):
            result, status = result
        if isinstance(result, Response):
            resp = result
        elif result is None:
            resp = Response(b"", 204)
        elif isinstance(result, (dict, list)):
            resp = Response.json(result)
        elif isinstance(result, bytes):
            resp = Response(result, content_type="application/octet-stream")
        else:
            text = str(result)
            ctype = "text/html; charset=utf-8" if text.lstrip().startswith("<") else "text/plain; charset=utf-8"
            resp = Response(text, content_type=ctype)
        if status is not None:
            resp.status = status
        return resp

    def _route(self, request: Request) -> Response:
        allowed: set[str] = set()
        for pattern, casts, methods, fn in self._routes:
            m = pattern.match(request.path)
            if not m:
                continue
            if request.method not in methods:
                allowed |= methods
                continue
            request.params = {k: casts[k](v) for k, v in m.groupdict().items()}
            kwargs = dict(request.params)
            if "request" in inspect.signature(fn).parameters:
                kwargs["request"] = request
            return self._to_response(fn(**kwargs))
        if allowed:
            err = HTTPError(405)
            resp = self._error_response(err)
            resp.headers["Allow"] = ", ".join(sorted(allowed))
            return resp
        raise HTTPError(404, "Route introuvable")

    def _error_response(self, err: HTTPError) -> Response:
        handler = self._error_handlers.get(err.status)
        if handler:
            resp = self._to_response(handler(err))
            resp.status = err.status
            return resp
        return Response.json({"error": err.message}, err.status)

    def _safe(self, fn: Callable[[], Response]) -> Response:
        try:
            return fn()
        except HTTPError as err:
            return self._error_response(err)
        except Exception:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            return self._error_response(HTTPError(500, "Erreur interne du serveur"))

    def dispatch(self, request: Request) -> Response:
        # Les erreurs de route sont converties en réponses AVANT les middlewares,
        # qui voient ainsi aussi les 404/422/500 (utile pour logs et métriques).
        handler = lambda req: self._safe(lambda: self._route(req))  # noqa: E731
        for mw in reversed(self._middleware):
            handler = (lambda mw_, nxt: lambda req: mw_(req, nxt))(mw, handler)
        return self._safe(lambda: handler(request))

    def __call__(self, environ: dict, start_response: Callable):
        length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(length) if length else b""
        headers = {k[5:].replace("_", "-").title(): v for k, v in environ.items() if k.startswith("HTTP_")}
        if environ.get("CONTENT_TYPE"):
            headers["Content-Type"] = environ["CONTENT_TYPE"]
        query = {k: v[0] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
        request = Request(environ["REQUEST_METHOD"].upper(), environ.get("PATH_INFO", "/"), query, headers, body)
        resp = self.dispatch(request)
        status = f"{resp.status} {HTTPStatus(resp.status).phrase}"
        resp.headers["Content-Length"] = str(len(resp.body))
        start_response(status, list(resp.headers.items()))
        return [resp.body]

    def run(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        with make_server(host, port, self) as server:
            print(f"microweb — http://{host}:{port}  (Ctrl+C pour arrêter)")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass

    def test_client(self) -> "TestClient":
        return TestClient(self)


class TestResponse:
    __test__ = False

    def __init__(self, status: int, headers: dict[str, str], body: bytes):
        self.status, self.headers, self.body = status, headers, body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.body)


class TestClient:
    """Appelle l'application WSGI directement, sans ouvrir de port."""
    __test__ = False

    def __init__(self, app: App):
        self.app = app

    def request(self, method: str, path: str, json_data: Any = None,
                headers: dict[str, str] | None = None) -> TestResponse:
        path, _, qs = path.partition("?")
        body = json.dumps(json_data).encode() if json_data is not None else b""
        environ = {
            "REQUEST_METHOD": method.upper(), "PATH_INFO": path, "QUERY_STRING": qs,
            "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(body), "SERVER_NAME": "testserver", "SERVER_PORT": "80",
            "wsgi.url_scheme": "http",
        }
        for k, v in (headers or {}).items():
            environ["HTTP_" + k.upper().replace("-", "_")] = v
        captured: dict[str, Any] = {}

        def start_response(status: str, hdrs: list[tuple[str, str]]):
            captured["status"], captured["headers"] = int(status.split()[0]), dict(hdrs)

        chunks = self.app(environ, start_response)
        return TestResponse(captured["status"], captured["headers"], b"".join(chunks))

    def get(self, path: str, **kw: Any) -> TestResponse:
        return self.request("GET", path, **kw)

    def post(self, path: str, json_data: Any = None, **kw: Any) -> TestResponse:
        return self.request("POST", path, json_data, **kw)

    def put(self, path: str, json_data: Any = None, **kw: Any) -> TestResponse:
        return self.request("PUT", path, json_data, **kw)

    def delete(self, path: str, **kw: Any) -> TestResponse:
        return self.request("DELETE", path, **kw)
