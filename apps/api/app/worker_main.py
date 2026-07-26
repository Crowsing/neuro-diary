"""Entry points for the background processes (§4.3, §6.4, §10).

Until this phase not one worker had a way to be started. That was survivable
while every deadline was soft — a compaction or a thirty-day sweep can slip an
hour and nobody is worse off — but reminders have an external, visible deadline:
the message either arrives at eight in the evening or it does not. So the
scheduler arrives here, and it arrives for all of them at once, because a
system where three of four workers are started by hand and one by a service
file is *less* predictable than either arrangement on its own.

Two long-running processes and one command:

* `neuro-maintenance` drives the three `api_rw` workers — the outbox
  dispatcher, the housekeeper and the vault compactor — in one loop. They share
  a role, a database and a tolerance for being a few minutes late, so a second
  process would buy nothing and cost another connection pool;
* `neuro-reminders` drives delivery under `reminder_worker`, on the period §10
  gives the sweeper. It is separate because its role and its secret are
  different, and that separation is the whole of §5.3;
* `neuro-restore-reconcile` is a command, not a loop. §6.4 makes reconciliation
  a decision a person takes during an incident, with the runbook open; a
  scheduler for it would be a scheduler for irreversible deletions.

**The loop is deliberately dumb.** One `run_once()` per tick, no backoff, no
concurrency and no retry policy: every worker already treats one account per
transaction and returns counters rather than raising. A driver that added
recovery logic here would be a second place where the isolation rules of §10
live.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.domain.erasure_journal import JournalLine
from app.domain.reminders import SWEEPER_PERIOD
from app.infra.clock import SystemClock
from app.infra.config import ConfigurationError, Settings
from app.infra.db.engine import (
    SqlReminderUnitOfWorkFactory,
    SqlUnitOfWorkFactory,
    build_engine,
)
from app.infra.logging import ensure_configured, get_logger
from app.infra.telegram.bot_api import TelegramBotApi
from app.main import CONSENT_COPY_ROOT, AppDependencies, build_dependencies
from app.infra.consent_copy import FileConsentCopyRegistry
from app.services.consent import ConsentService
from app.services.erasure import ErasureService
from app.services.erasure_journal import DatabaseErasureJournal
from app.services.ports import Clock
from app.workers.config import ReminderWorkerSettings
from app.workers.erasure_worker import Housekeeper
from app.workers.outbox_dispatcher import OutboxDispatcher
from app.workers.reminder_worker import ReminderWorker
from app.workers.restore_reconciler import RestoreReconciler
from app.workers.vault_compactor import VaultCompactor

#: §4.3 puts a fifteen-minute ceiling on the erasure safety net, so the
#: dispatcher has to run several times inside it rather than once at the edge.
MAINTENANCE_PERIOD = timedelta(minutes=5)


def drive(
    name: str,
    step: Callable[[], object],
    *,
    period: timedelta,
    stop: threading.Event,
) -> None:
    """Call `step` every `period` until asked to stop.

    A failing step is logged and the loop continues. The alternative — letting
    the exception end the process — turns one unreachable row into an outage
    that lasts until somebody notices, and the workers already isolate failures
    per account beneath this.

    `name` becomes part of the event name rather than a field beside it. §11
    allowlists `event` and would drop a `worker=` field silently, leaving a log
    line that says a tick failed without saying which loop — worse than either
    naming it properly or not logging at all.
    """
    while not stop.is_set():
        started = datetime.now(UTC)
        try:
            step()
        except Exception:  # noqa: BLE001 - a tick must not end the process
            # No traceback: a database error carries the failing statement and
            # its bound parameters, which is every identifier this system has.
            get_logger().error(f"{name}_tick_failed")
        elapsed = datetime.now(UTC) - started
        stop.wait(max((period - elapsed).total_seconds(), 0.0))


def _stopper() -> threading.Event:
    """SIGTERM and SIGINT end the current tick rather than the current write."""
    stop = threading.Event()

    def handle(signum: int, frame: object) -> None:
        del signum, frame
        stop.set()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    return stop


def build_maintenance(dependencies: AppDependencies) -> Callable[[], None]:
    """Compose the three `api_rw` workers into one tick.

    Separate from `maintenance()` so a test can build the real objects with real
    constructor arguments. Wiring is where entry points go wrong — a swapped
    positional, a renamed keyword — and an entry point tested only for being
    callable is an entry point tested for nothing.
    """
    erasure = ErasureService(dependencies.erasure_journal)
    consents = ConsentService(
        dependencies.consent_copy,
        erasure,
        allow_unfrozen_copy=dependencies.settings.is_development,
    )
    dispatcher = OutboxDispatcher(
        dependencies.unit_of_work, erasure, consents, dependencies.clock
    )
    housekeeper = Housekeeper(dependencies.unit_of_work, erasure, dependencies.clock)
    compactor = VaultCompactor(dependencies.unit_of_work, dependencies.clock)

    def tick() -> None:
        dispatcher.run_once()
        housekeeper.run_once()
        compactor.run_once()

    return tick


def build_reminders(
    settings: ReminderWorkerSettings,
    client: httpx.Client,
    clock: Clock | None = None,
) -> ReminderWorker:
    """Compose the delivery worker around the real Bot API adapter.

    The client is a parameter so the caller owns its lifetime — and so a test
    can hand in `httpx.MockTransport` and exercise the one seam that otherwise
    exists nowhere: `TelegramBotApi` and `ReminderWorker` meeting. The clock is
    a parameter for the same reason `build_maintenance` gets one on its
    dependencies: a composition that could only be exercised at the current wall
    time would be a composition tested at whatever o'clock CI happened to run.
    """
    resolved = clock if clock is not None else SystemClock()
    return ReminderWorker(
        SqlReminderUnitOfWorkFactory(build_engine(settings.database_url)),
        TelegramBotApi(
            token=settings.bot_token,
            webapp_url=settings.webapp_url,
            client=client,
            clock=resolved.now,
        ),
        resolved,
    )


def maintenance(env: Mapping[str, str] | None = None) -> None:
    """The three `api_rw` workers, in one loop."""
    ensure_configured()
    dependencies = build_dependencies(env if env is not None else os.environ)
    get_logger().info("maintenance_started")
    drive(
        "maintenance",
        build_maintenance(dependencies),
        period=MAINTENANCE_PERIOD,
        stop=_stopper(),
    )


def reminders(env: Mapping[str, str] | None = None) -> None:
    """Delivery, under `reminder_worker` and with `BOT_TOKEN`.

    The period is the sweeper's, which §10 caps at five minutes: a `pending`
    row has to be settled well before it could be mistaken for a live attempt,
    and the same tick is what notices a schedule coming due.
    """
    ensure_configured()
    settings = ReminderWorkerSettings.from_env(env if env is not None else os.environ)
    # The client is closed on the way out, so SIGTERM releases its sockets
    # rather than leaving the kernel to notice the process is gone.
    with httpx.Client() as client:
        worker = build_reminders(settings, client)
        get_logger().info("reminders_started")
        drive("reminders", worker.run_once, period=SWEEPER_PERIOD, stop=_stopper())


def restore_reconcile(argv: list[str] | None = None) -> int:
    """Replay an erasure journal against a restored database (§6.4, step 4).

    A command rather than a loop, and one that refuses to guess: it takes the
    journal export and the restore point explicitly, because both are decisions
    the controller makes with the runbook open. Re-running is safe but not free
    — `sync_off` and `security_reset` advance counters on every pass — and the
    runbook says when to stop.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2:
        sys.stderr.write(
            "usage: neuro-restore-reconcile <journal.jsonl> <restore-point>\n"
            "see docs/restore-runbook.md — this deletes data\n"
        )
        return 2

    ensure_configured()
    settings = Settings.from_env(os.environ)
    if settings.erasure_journal_key is None or settings.service_start_at is None:
        raise ConfigurationError(
            "ERASURE_JOURNAL_KEY and SERVICE_START_AT are required to"
            " reconcile a restore (§6.4)"
        )
    unit_of_work = SqlUnitOfWorkFactory(build_engine(settings.api_database_url))
    reconciler = RestoreReconciler(
        unit_of_work,
        # The row journal, not the external one: the reconciliation *writes*
        # journal entries of its own, and §6.5's transport does not exist yet
        # (`app.main._object_store` refuses on purpose). The runbook already
        # names that as the open blocker; it must not be papered over here.
        ErasureService(
            DatabaseErasureJournal(
                unit_of_work,
                FileConsentCopyRegistry(CONSENT_COPY_ROOT),
            )
        ),
        SystemClock(),
        erasure_key=settings.erasure_journal_key,
        service_start_at=settings.service_start_at,
    )

    lines = [
        JournalLine.from_json(json.dumps(json.loads(raw)).encode())
        for raw in Path(arguments[0]).read_text("utf-8").splitlines()
        if raw.strip()
    ]
    outcome = reconciler.reconcile(
        lines,
        taken_at=datetime.fromisoformat(arguments[1].replace("Z", "+00:00")),
    )
    sys.stdout.write(f"{outcome}\n")
    return 0
