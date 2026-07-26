"""Request logging and the in-memory per-IP limiter of §11.

Two properties are load-bearing: the log carries the route template rather than
the requested URL, and the limiter never lets an address reach disk.
"""

from __future__ import annotations

import asyncio
import io
import json

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.middleware import (
    UNMATCHED_ROUTE,
    QueryParameterAllowlistMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
)
from app.infra.logging import configure_logging

CLIENT_IP = "203.0.113.7"
LIMITED_PATH = "/v1/auth/telegram"


class FakeClock:
    def __init__(self) -> None:
        self.seconds = 1_000.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def buffer() -> io.StringIO:
    stream = io.StringIO()
    configure_logging(stream=stream)
    return stream


@pytest.fixture
def app(clock: FakeClock) -> FastAPI:
    application = FastAPI()
    application.add_middleware(
        RateLimitMiddleware,
        limits={("POST", LIMITED_PATH): 10},
        window_seconds=60,
        secret=bytes(range(32)),
        monotonic=clock,
    )
    application.add_middleware(RequestContextMiddleware, monotonic=clock)

    @application.post(LIMITED_PATH)
    def authenticate() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/v1/consents")
    def consents() -> dict[str, list[str]]:
        return {"consents": []}

    return application


class Caller:
    """Synchronous facade over the ASGI transport, fixed to one address."""

    def __init__(self, app: FastAPI, address: str = CLIENT_IP) -> None:
        self._app = app
        self._address = address

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        async def _send() -> httpx.Response:
            transport = httpx.ASGITransport(
                app=self._app,
                client=(self._address, 51_000),
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as session:
                return await session.request(method, path, params=params)

        return asyncio.run(_send())

    def get(self, path: str, params: dict[str, str] | None = None) -> httpx.Response:
        return self.request("GET", path, params)

    def post(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        return self.request("POST", path, params)


@pytest.fixture
def client(app: FastAPI) -> Caller:
    return Caller(app)


def _records(buffer: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line]


def test_request_log_carries_the_route_template_not_the_url(
    client: Caller,
    buffer: io.StringIO,
) -> None:
    response = client.get("/v1/consents", params={"note": "migraine"})

    assert response.status_code == 200
    (record,) = _records(buffer)
    assert record["route_template"] == "/v1/consents"
    assert record["method"] == "GET"
    assert record["status_code"] == 200
    assert isinstance(record["duration_ms"], int)
    assert record["request_id"]
    assert set(record) <= {
        "event",
        "level",
        "timestamp",
        "request_id",
        "route_template",
        "method",
        "status_code",
        "duration_ms",
    }
    raw = buffer.getvalue()
    assert "migraine" not in raw
    assert CLIENT_IP not in raw


def test_unmatched_routes_never_log_the_requested_path(
    client: Caller,
    buffer: io.StringIO,
) -> None:
    client.get("/v1/entry/2026-03-14")

    (record,) = _records(buffer)
    assert record["route_template"] == "unmatched"
    assert "2026-03-14" not in buffer.getvalue()


def test_the_eleventh_authentication_attempt_is_throttled(
    client: Caller,
) -> None:
    for _ in range(10):
        assert client.post(LIMITED_PATH).status_code == 200

    throttled = client.post(LIMITED_PATH)

    assert throttled.status_code == 429
    assert throttled.json() == {"error": "rate_limited"}
    assert int(throttled.headers["Retry-After"]) > 0


def test_the_window_reopens(client: Caller, clock: FakeClock) -> None:
    for _ in range(10):
        client.post(LIMITED_PATH)
    assert client.post(LIMITED_PATH).status_code == 429

    clock.advance(61)

    assert client.post(LIMITED_PATH).status_code == 200


def test_an_unreadable_query_never_spends_the_authentication_window(
    app: FastAPI,
) -> None:
    """The mechanism: with the query check above the limiter, a refusal is free.

    A request carrying a parameter this API does not read is not an
    authentication attempt, so refusing it must not cost anybody a slot of the
    §11 per-IP window.

    This builds its own stack, so it proves the mechanism and **not** that
    `create_app` registers them in this order — Starlette runs middleware in
    reverse order of registration, and swapping two lines there leaves this test
    green. The registration itself is asserted against the real application in
    `tests/integration/test_fuzz_regressions.py::test_an_unreadable_query_never_
    spends_the_authentication_window`.
    """
    app.add_middleware(QueryParameterAllowlistMiddleware)
    client = Caller(app, CLIENT_IP)

    for _ in range(30):
        assert client.request("POST", LIMITED_PATH, {"note": "head"}).status_code == 422

    assert client.post(LIMITED_PATH).status_code == 200


def test_an_unsupported_method_on_the_limited_path_is_not_an_attempt(
    client: Caller,
) -> None:
    """§11 limits authentication attempts, and a `PATCH` is not one.

    The router answers 405 without reaching the database, so throttling it costs
    the caller a 429 in place of the truthful refusal — and made the
    `unsupported_method` check of the schemathesis job fail on a real
    disagreement between the limiter and the router.
    """
    for _ in range(20):
        assert client.request("PATCH", LIMITED_PATH).status_code == 405

    assert client.post(LIMITED_PATH).status_code == 200


def test_throttling_is_scoped_to_the_configured_route(client: Caller) -> None:
    for _ in range(10):
        client.post(LIMITED_PATH)
    assert client.post(LIMITED_PATH).status_code == 429

    assert client.get("/v1/consents").status_code == 200


def test_a_throttled_request_still_keeps_the_address_off_disk(
    client: Caller,
    buffer: io.StringIO,
) -> None:
    for _ in range(11):
        client.post(LIMITED_PATH)

    raw = buffer.getvalue()
    assert CLIENT_IP not in raw
    throttled = _records(buffer)[-1]
    assert throttled["status_code"] == 429
    assert throttled["route_template"] == LIMITED_PATH
    assert throttled["error_code"] == "rate_limited"


def test_separate_addresses_have_separate_budgets(app: FastAPI) -> None:
    exhausted = Caller(app)
    for _ in range(11):
        exhausted.post(LIMITED_PATH)

    assert Caller(app, address="198.51.100.4").post(LIMITED_PATH).status_code == 200


def test_a_refused_query_leaves_neither_the_path_nor_the_name_in_the_log(
    buffer: io.StringIO,
    clock: FakeClock,
) -> None:
    """Новий шлях відмови — і новий шлях, яким §11 могли б порушити.

    Параметр і сам шлях запиту — це надіслані значення. Middleware відмовляє до
    маршрутизації, тож `route_template` лишається `unmatched`: назвати шаблон він
    не може, а назвати запитаний шлях §11 забороняє.

    Стек збирається тут у **тому самому порядку**, що в `create_app`: логер
    найзовнішній, перевірка query під ним. Порядок має значення саме для цього
    твердження — якби перевірка стояла вище за логер, відмова не потрапила б у лог
    узагалі, і «в лозі немає назви параметра» трималося б на відсутності рядка.
    """
    application = FastAPI()
    application.add_middleware(QueryParameterAllowlistMiddleware)
    application.add_middleware(RequestContextMiddleware, monotonic=clock)

    @application.get("/v1/consents")
    def consents() -> dict[str, list[str]]:  # pragma: no cover - refused earlier
        return {"consents": []}

    client = Caller(application)

    refused = client.get("/v1/entries/2026-01-15", {"note": "migraine"})

    assert refused.status_code == 422
    (record,) = _records(buffer)
    assert record["route_template"] == UNMATCHED_ROUTE
    assert record["error_code"] == "unknown_query_parameter"
    raw = buffer.getvalue()
    assert "note" not in raw
    assert "migraine" not in raw
    assert "2026-01-15" not in raw
