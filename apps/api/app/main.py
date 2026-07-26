"""FastAPI composition root.

Dependencies are passed in explicitly so tests can supply a frozen clock and a
deterministic initData key without reaching into module state. `app_factory`
builds them from the environment for a real process.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.account import router as account_router
from app.api.v1.auth import router as auth_router
from app.api.v1.consents import router as consents_router
from app.api.v1.deps import Services
from app.api.v1.errors import (
    domain_error_handler,
    request_validation_error_handler,
    unhandled_error_handler,
)
from app.api.v1.health import router as health_router
from app.api.v1.sync import router as sync_router
from app.api.v1.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.domain.identity import DomainError
from app.infra.clock import SystemClock
from app.infra.config import ConfigurationError, Settings
from app.infra.consent_copy import FileConsentCopyRegistry
from app.infra.db.engine import SqlUnitOfWorkFactory, build_engine
from app.infra.erasure_journal.journal import ObjectStoreErasureJournal
from app.infra.erasure_journal.store import ObjectStorePort
from app.services.erasure_journal import DatabaseErasureJournal, TeeErasureJournal
from app.infra.logging import ensure_configured, get_logger
from app.infra.telegram.initdata import InitDataValidator
from app.services.auth import AuthService
from app.services.consent import ConsentService
from app.services.erasure import ErasureService
from app.services.sync import SyncService
from app.services.ports import (
    Clock,
    ConsentCopyPort,
    ErasureJournalPort,
    InitDataValidatorPort,
    UnitOfWorkFactory,
)

CONSENT_COPY_ROOT = Path(__file__).resolve().parents[3] / "consent-copy"
AUTH_ATTEMPTS_PER_MINUTE = 10
RATE_LIMIT_WINDOW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class AppDependencies:
    settings: Settings
    unit_of_work: UnitOfWorkFactory
    clock: Clock
    init_data: InitDataValidatorPort
    consent_copy: ConsentCopyPort
    erasure_journal: ErasureJournalPort


def create_app(dependencies: AppDependencies) -> FastAPI:
    # Uvicorn's default access formatter includes the raw query string.
    logging.getLogger("uvicorn.access").disabled = True
    ensure_configured()

    settings = dependencies.settings
    erasure = ErasureService(dependencies.erasure_journal)
    consents = ConsentService(
        dependencies.consent_copy,
        erasure,
        # Draft copy is accepted only while developing; production fails closed
        # until the controller is named and the text is frozen at 1.0.
        allow_unfrozen_copy=settings.is_development,
    )
    auth = AuthService(
        dependencies.unit_of_work,
        dependencies.init_data,
        consents,
        dependencies.clock,
    )
    sync = SyncService(dependencies.clock, erasure)

    application = FastAPI(title="Neuro Diary API")
    application.state.services = Services(
        auth=auth,
        consents=consents,
        erasure=erasure,
        consent_copy=dependencies.consent_copy,
        unit_of_work=dependencies.unit_of_work,
        sync=sync,
        app_env=settings.app_env,
    )

    application.add_exception_handler(
        RequestValidationError,
        request_validation_error_handler,
    )
    application.add_exception_handler(DomainError, domain_error_handler)
    # Registered last so nothing reaches uvicorn.error with a SQL statement and
    # its bound parameters attached.
    application.add_exception_handler(Exception, unhandled_error_handler)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.webapp_url],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    application.add_middleware(
        RateLimitMiddleware,
        limits={"/v1/auth/telegram": AUTH_ATTEMPTS_PER_MINUTE},
        window_seconds=RATE_LIMIT_WINDOW_SECONDS,
        secret=settings.log_account_ref_key,
    )
    application.add_middleware(
        RequestContextMiddleware,
        secret=settings.log_account_ref_key,
    )

    _disclose_copy_state(dependencies, settings)
    _disclose_journal_state(dependencies)

    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(consents_router)
    application.include_router(account_router)
    application.include_router(sync_router)
    return application


def _disclose_copy_state(
    dependencies: AppDependencies,
    settings: Settings,
) -> None:
    """Say at startup whether this process accepts draft consent copy.

    Only a count is logged: `text_version` is `<kind>@<major>.<minor>`, and §11
    keeps the consent name out of the log.
    """
    unfrozen = dependencies.consent_copy.unfrozen_versions()
    if not unfrozen:
        return
    event = (
        "consent_copy_unfrozen_accepted"
        if settings.is_development
        else "consent_copy_unfrozen_refused"
    )
    get_logger().warning(event, record_count=len(unfrozen))


def _disclose_journal_state(dependencies: AppDependencies) -> None:
    """Say at startup which erasure journal this process is actually writing to.

    The same shape as the consent-copy disclosure above, and for the same
    reason: a weakened guarantee has to be visible in the log of the running
    process, not only in the configuration somebody set months ago.

    It reads the wired dependency rather than the settings flag on purpose. The
    flag says what the environment asked for; only the object says what this
    process will do, and a log line that reports the wrong one is exactly the
    kind of claim this codebase keeps failing to keep.
    """
    event = (
        "erasure_journal_external"
        if isinstance(dependencies.erasure_journal, TeeErasureJournal)
        else "erasure_journal_database_only"
    )
    get_logger().warning(event)


def _object_store(settings: Settings) -> ObjectStorePort:
    """The one piece of §6.5 this repository does not have.

    Everything above it is written and tested: the line format, the chain, the
    signed head, the adapter, and the composition below. What is missing is a
    client that speaks to a real bucket with another provider in another
    country — and no bucket was available to verify a signer against. Shipping
    an unverified one would be a claim the code cannot demonstrate, and quietly
    selecting `InMemoryObjectStore` would be worse than either: a journal that
    does not survive a restart, let alone the incident it exists for.

    So this refuses loudly, and the refusal is the honest state of the
    deployment rather than a bug.
    """
    del settings
    raise ConfigurationError(
        "ERASURE_JOURNAL_ENABLED is set, but no object store transport is"
        " implemented yet — see docs/restore-runbook.md"
    )


def _erasure_journal(
    settings: Settings,
    unit_of_work: UnitOfWorkFactory,
    consent_copy: ConsentCopyPort,
) -> ErasureJournalPort:
    """The external journal in front, the row behind it (§6.4, §6.5)."""
    database = DatabaseErasureJournal(unit_of_work, consent_copy)
    if not settings.erasure_journal_enabled:
        return database
    if (
        settings.erasure_journal_key is None
        or settings.erasure_journal_head_key is None
    ):
        # `Settings.from_env` already refuses this; repeating it here keeps the
        # composition safe against a hand-built `Settings` in a future caller.
        raise ConfigurationError("the erasure journal is enabled without its keys")
    external = ObjectStoreErasureJournal(
        _object_store(settings),
        erasure_key=settings.erasure_journal_key,
        head_key=settings.erasure_journal_head_key,
        prefix=settings.erasure_journal_prefix,
    )
    return TeeErasureJournal(external, database)


def build_dependencies(env: Mapping[str, str] | None = None) -> AppDependencies:
    settings = Settings.from_env(env if env is not None else os.environ)
    unit_of_work = SqlUnitOfWorkFactory(build_engine(settings.api_database_url))
    consent_copy = FileConsentCopyRegistry(CONSENT_COPY_ROOT)
    return AppDependencies(
        settings=settings,
        unit_of_work=unit_of_work,
        clock=SystemClock(),
        init_data=InitDataValidator(
            bot_id=settings.telegram_bot_id,
            public_key=settings.telegram_ed25519_public_key,
            min_auth_date=settings.min_auth_date,
            hmac_fallback_enabled=settings.auth_hmac_fallback_enabled,
            hmac_secret=settings.webapp_hmac_secret,
        ),
        consent_copy=consent_copy,
        erasure_journal=_erasure_journal(settings, unit_of_work, consent_copy),
    )


def app_factory() -> FastAPI:
    """Entry point for `uvicorn app.main:app_factory --factory`."""
    return create_app(build_dependencies())
