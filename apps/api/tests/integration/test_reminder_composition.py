"""The seams the unit suites cannot see: §11's double check and the entry points.

Two things were asserted by nothing before this module. §11 requires the consent
to be checked on the way in **and** again inside the write transaction, and
either half could be deleted with the whole suite green, because both answer the
same 403. And no test ever ran the composition an entry point performs, so
`TelegramBotApi` and `ReminderWorker` — an adapter tested against a mock
transport and a worker tested against a fake port — had never met.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text

from app.api.v1.deps import Services
from app.domain.identity import ConsentKind, ConsentRequired
from app.domain.reminders import BUTTON_LABEL, MESSAGE_TEXT
from app.main import AppDependencies
from app.services.consent import ConsentService
from app.services.ports import UnitOfWork
from app.worker_main import build_maintenance, build_reminders
from app.workers.config import ReminderWorkerSettings

from conftest import Caller, Database, FrozenClock, REPO_ROOT, sign_init_data

REMINDERS_SHA = hashlib.sha256(
    (REPO_ROOT / "consent-copy" / "uk" / "telegram_reminders" / "0.9.md").read_bytes()
).hexdigest()
KYIV = "Europe/Kyiv"
SETTINGS = "/v1/reminders/settings"
FIRE_AT = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
BODY = {"enabled": True, "time": "20:00", "timezone": KYIV}


class CountingConsents:
    """A `ConsentService` that can refuse on a chosen call of `require_active`.

    Everything else is delegated, so the request runs exactly as it would
    otherwise. Refusing on the first call kills the entry check; refusing on the
    second kills the in-transaction barrier — and until this existed, deleting
    either one left the suite green.
    """

    def __init__(self, inner: ConsentService, *, refuse_on: int | None = None) -> None:
        self._inner = inner
        self._refuse_on = refuse_on
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def require_active(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        kind: ConsentKind,
    ) -> None:
        self.calls += 1
        if self.calls == self._refuse_on:
            raise ConsentRequired()
        self._inner.require_active(unit, account_id=account_id, kind=kind)


def _grant(caller: Caller) -> None:
    response = caller.post(
        "/v1/auth/telegram",
        {
            "init_data": sign_init_data(),
            "grant": {
                "kind": "telegram_reminders",
                "text_version": "telegram_reminders@0.9",
                "text_sha256": REMINDERS_SHA,
                "settings": {"time": "20:00", "timezone": KYIV},
            },
        },
    )
    assert response.status_code == 200, response.text
    caller.token = str(response.json()["session_token"])


def _with_consents(api: FastAPI, spy: CountingConsents) -> None:
    services: Services = api.state.services
    api.state.services = dataclasses.replace(services, consents=spy)  # type: ignore[arg-type]


@pytest.fixture
def spy(api: FastAPI) -> CountingConsents:
    services: Services = api.state.services
    return CountingConsents(services.consents)


# ------------------------------------------------------- §11's double check


@pytest.mark.parametrize("path_call", ["get", "put"])
def test_the_consent_is_checked_twice_on_every_request(
    api: FastAPI,
    caller: Caller,
    engine: Engine,
    spy: CountingConsents,
    path_call: str,
) -> None:
    del engine
    _grant(caller)
    _with_consents(api, spy)
    spy.calls = 0

    response = (
        caller.put(SETTINGS, BODY) if path_call == "put" else caller.get(SETTINGS)
    )

    assert response.status_code == 200, response.text
    assert spy.calls == 2


@pytest.mark.parametrize("refuse_on", [1, 2])
@pytest.mark.parametrize("path_call", ["get", "put"])
def test_either_half_of_the_check_alone_refuses_the_request(
    api: FastAPI,
    caller: Caller,
    engine: Engine,
    refuse_on: int,
    path_call: str,
) -> None:
    """§11: the entry check and the in-transaction barrier are both load-bearing.

    `refuse_on=2` is the case that matters. It stands in for a consent revoked
    between the dependency and the write — the race the barrier exists for, and
    the one a test on a plain consent-less session can never distinguish.
    """
    _grant(caller)
    services: Services = api.state.services
    _with_consents(api, CountingConsents(services.consents, refuse_on=refuse_on))

    response = (
        caller.put(SETTINGS, BODY) if path_call == "put" else caller.get(SETTINGS)
    )

    assert response.status_code == 403
    assert response.json() == {"error": "consent_required"}
    if path_call == "put":
        with engine.connect() as connection:
            stored = connection.execute(
                text("SELECT local_time FROM reminders.reminder_schedule")
            ).scalar_one()
        assert stored.strftime("%H:%M") == "20:00"


# ----------------------------------------------------------- the entry points


def test_the_maintenance_tick_is_composed_and_runnable(
    dependencies: AppDependencies,
    engine: Engine,
) -> None:
    """The composition `neuro-maintenance` performs, executed once for real.

    Constructor wiring is where an entry point breaks — a swapped positional, a
    renamed keyword — and until this ran, `maintenance()` was covered by a test
    that only asked whether the name was callable.
    """
    del engine

    build_maintenance(dependencies)()


def test_the_adapter_and_the_worker_meet_under_the_real_composition(
    caller: Caller,
    engine: Engine,
    identity_database: Database,
    clock: FrozenClock,
) -> None:
    """`neuro-reminders` end to end: real worker, real adapter, mock socket.

    Everywhere else the adapter is tested against `httpx.MockTransport` and the
    worker against a fake port, so the one thing neither covers is the two of
    them wired together — which is exactly what the entry point does.
    """
    _grant(caller)
    clock.set(FIRE_AT)
    with psycopg.connect(identity_database.api_url, autocommit=True) as connection:
        connection.execute(
            "UPDATE reminders.reminder_schedule SET next_fire_at = %s", (FIRE_AT,)
        )

    seen: list[tuple[str, dict[str, Any]]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 909}})

    settings = ReminderWorkerSettings.from_env(
        {
            "REMINDER_WORKER_DATABASE_URL": identity_database.worker_url,
            "WEBAPP_URL": "https://diary.example.invalid",
            "BOT_TOKEN": "1234567890:synthetic-generated-in-this-run",
        }
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        worker = build_reminders(settings, client, clock)
        result = worker.run_once()

    assert result.sent == 1
    method, body = seen[0]
    assert method == "sendMessage"
    assert body["text"] == MESSAGE_TEXT
    assert body["reply_markup"]["inline_keyboard"] == [
        [{"text": BUTTON_LABEL, "web_app": {"url": "https://diary.example.invalid"}}]
    ]
    with engine.connect() as connection:
        stored = connection.execute(
            text("SELECT status, telegram_message_id FROM reminders.reminder_delivery")
        ).one()
    assert (stored.status, stored.telegram_message_id) == ("sent", 909)
