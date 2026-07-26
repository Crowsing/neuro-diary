"""Every published operation has a *named* status regarding §11.

This is the DoD item «кожен ендпоінт має названий статус щодо ліміту: або
застосунковий ліміт із тестом, або записане рішення „ліміту немає, бо…"». Before
Phase 5 the answer required reading every router and cross-checking it against
every call of `rate_windows.consume`, which is not a property a test can hold.

The table below is that record. It is not, on its own, a check: a table can
claim a limit that was deleted. So it is asserted in three directions at once —

* against the app's real surface, so an endpoint added without a decision fails;
* against `app.api.v1.deps.DECLARED_BUDGETS`, which is written by the dependency
  factories themselves, so a bucket removed from the wiring fails even while the
  table still names it;
* against the reverse: an operation recorded as deliberately unlimited must
  declare **no** bucket, so a limit added without recording the reason fails too.

The refusal behaviour of each bucket — that the limit+1st request answers 429
with a `Retry-After`, and that the window rolls — lives in
`tests/integration/test_sync_rate_limits.py` and
`tests/integration/test_endpoint_limits.py`. This module holds the map, not the
territory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.api.v1 import deps
from app.domain.identity import ConsentKind
from app.domain.rate_limits import BUDGETS, RateBucket
from app.infra.clock import SystemClock
from app.infra.config import Settings
from app.infra.consent_copy import FileConsentCopyRegistry
from app.infra.telegram.initdata import InitDataValidator
from app.main import AUTH_ATTEMPTS_PER_MINUTE, CONSENT_COPY_ROOT
from app.main import AppDependencies, create_app
from app.services.erasure_journal import DatabaseErasureJournal


@dataclass(frozen=True)
class Policy:
    """What one operation charges, or why it charges nothing."""

    #: Buckets charged by an entry dependency. Cross-checked against the wiring.
    at_the_door: frozenset[RateBucket] = frozenset()
    #: Buckets charged inside the handler because their cost is not one per
    #: request. Named here, proven by the integration test named beside them.
    in_the_handler: frozenset[RateBucket] = frozenset()
    #: Non-empty exactly when both sets above are empty.
    unlimited_because: str = ""
    #: The consent §11 gates this operation on at the door, if any.
    consent: ConsentKind | None = None

    def __post_init__(self) -> None:
        charged = bool(self.at_the_door or self.in_the_handler)
        assert charged != bool(self.unlimited_because), self


PER_IP_ONLY = (
    "§11 puts the auth attempt limit at the reverse proxy, per address, in"
    " process memory. There is no account yet to key a per-account window on:"
    " this is the endpoint that mints the session."
)

#: The decision of the sixteenth deviation, operation by operation.
POLICY: dict[tuple[str, str], Policy] = {
    ("/health", "get"): Policy(
        unlimited_because=(
            "No session, no account, no row read or written. A liveness probe"
            " that needed a per-account window would need an account, and the"
            " endpoint exists precisely for callers that have none."
        )
    ),
    ("/v1/auth/telegram", "post"): Policy(unlimited_because=PER_IP_ONLY),
    ("/v1/sessions", "get"): Policy(at_the_door=frozenset({RateBucket.ACCOUNT_OPS})),
    ("/v1/sessions/revoke-others", "post"): Policy(
        at_the_door=frozenset({RateBucket.ACCOUNT_OPS})
    ),
    ("/v1/consents", "get"): Policy(
        unlimited_because=(
            "§9.4 forbids pruning without a fresh consent answer, so an"
            " exhausted budget must never block the answer that unblocks"
            " pruning. Held from the other side by"
            " test_consents_do_not_share_the_sync_bucket."
        )
    ),
    ("/v1/consents", "post"): Policy(at_the_door=frozenset({RateBucket.ACCOUNT_OPS})),
    ("/v1/consents/revoke", "post"): Policy(
        unlimited_because=(
            "Art. 7(3): withdrawing must be as easy as granting, and «as easy»"
            " cannot mean «behind a budget the grant already spent». §8 refuses"
            " a step-up here for the same reason."
        )
    ),
    ("/v1/account/delete", "post"): Policy(
        unlimited_because=(
            "Art. 17 with Art. 12(3): terminal on success, step-up on failure."
            " A window could only delay an erasure request, and the first"
            " successful call leaves no account to charge."
        )
    ),
    ("/v1/account/vault-reset", "post"): Policy(
        at_the_door=frozenset({RateBucket.SYNC}),
        consent=ConsentKind.HEALTH_SYNC,
    ),
    ("/v1/sync/push", "post"): Policy(
        at_the_door=frozenset({RateBucket.SYNC}),
        # Proven by test_push_volume_over_five_mebibytes_a_minute_is_rate_limited.
        in_the_handler=frozenset({RateBucket.PUSH_BYTES}),
        consent=ConsentKind.HEALTH_SYNC,
    ),
    ("/v1/sync/pull", "get"): Policy(
        at_the_door=frozenset({RateBucket.SYNC}),
        consent=ConsentKind.HEALTH_SYNC,
    ),
    ("/v1/sync/key", "get"): Policy(
        at_the_door=frozenset({RateBucket.KEY_READ}),
        consent=ConsentKind.HEALTH_SYNC,
    ),
    ("/v1/sync/key", "post"): Policy(
        at_the_door=frozenset({RateBucket.SYNC}),
        consent=ConsentKind.HEALTH_SYNC,
    ),
    ("/v1/reminders/settings", "get"): Policy(
        at_the_door=frozenset({RateBucket.REMINDERS_SETTINGS}),
        consent=ConsentKind.TELEGRAM_REMINDERS,
    ),
    ("/v1/reminders/settings", "put"): Policy(
        at_the_door=frozenset({RateBucket.REMINDERS_SETTINGS}),
        consent=ConsentKind.TELEGRAM_REMINDERS,
    ),
}


def _app() -> FastAPI:
    settings = Settings.from_env(
        {
            "API_DATABASE_URL": "postgresql://api_rw@localhost:5432/diary",
            "TELEGRAM_BOT_ID": "1234567890",
            "WEBAPP_URL": "https://example.invalid",
            "APP_ENV": "development",
        }
    )
    consent_copy = FileConsentCopyRegistry(CONSENT_COPY_ROOT)

    def _no_database() -> object:
        raise AssertionError("describing the API must not need a database")

    return create_app(
        AppDependencies(
            settings=settings,
            unit_of_work=_no_database,  # type: ignore[arg-type]
            clock=SystemClock(),
            init_data=InitDataValidator(
                bot_id=settings.telegram_bot_id,
                public_key=settings.telegram_ed25519_public_key,
            ),
            consent_copy=consent_copy,
            erasure_journal=DatabaseErasureJournal(
                _no_database,  # type: ignore[arg-type]
                consent_copy,
            ),
        )
    )


@pytest.fixture(scope="module")
def application() -> FastAPI:
    return _app()


def _operations(application: FastAPI) -> dict[tuple[str, str], APIRoute]:
    """Walk the route tree, not the top level.

    `include_router` wraps each router in an `_IncludedRouter` node in this
    FastAPI version, so a flat loop over `application.routes` finds the four
    documentation routes and none of the API — and every assertion in this module
    would then be vacuously true, which is the failure mode worth naming.
    `test_the_schema_names_no_operation_the_policy_has_not_seen` is the guard
    against that: it compares this walk against the OpenAPI document, so a walk
    that finds nothing fails rather than passes.
    """
    found: dict[tuple[str, str], APIRoute] = {}
    pending: list[Any] = list(application.routes)
    while pending:
        route = pending.pop()
        pending.extend(getattr(route, "routes", ()))
        included = getattr(route, "original_router", None)
        if included is not None:
            pending.extend(included.routes)
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            if method in {"HEAD", "OPTIONS"}:  # pragma: no cover - none declared
                continue
            found[(route.path, method.lower())] = route
    return found


def _declared(route: APIRoute, registry: dict[Any, Any]) -> set[Any]:
    """What this route's dependency tree actually declares, per registry."""
    found: set[Any] = set()
    pending = list(route.dependant.dependencies)
    while pending:
        dependant = pending.pop()
        declared = registry.get(dependant.call)
        if declared is not None:
            found.add(declared)
        pending.extend(dependant.dependencies)
    return found


def _declared_buckets(route: APIRoute) -> set[RateBucket]:
    """Which buckets this route's dependency tree actually charges."""
    return _declared(route, deps.DECLARED_BUDGETS)


def test_every_published_operation_has_a_recorded_decision(
    application: FastAPI,
) -> None:
    assert set(_operations(application)) == set(POLICY)


@pytest.mark.parametrize("operation", sorted(POLICY))
def test_the_wiring_charges_exactly_what_the_record_claims(
    application: FastAPI,
    operation: tuple[str, str],
) -> None:
    """Both directions at once.

    A bucket removed from the wiring fails here while the table still names it,
    and a bucket added to the wiring fails while the table calls the operation
    unlimited.
    """
    route = _operations(application)[operation]

    assert _declared_buckets(route) == set(POLICY[operation].at_the_door), operation


@pytest.mark.parametrize("operation", sorted(POLICY))
def test_the_wiring_gates_on_exactly_the_consent_the_record_claims(
    application: FastAPI,
    operation: tuple[str, str],
) -> None:
    """§11's entry check, asserted structurally because it cannot always be seen.

    Where the handler re-checks the same consent inside its transaction — which
    §11 also requires — removing the dependency changes no status code, so a
    behavioural test cannot tell the two designs apart. This registry can: it is
    written by `require_consent` itself, so the entry disappears with the
    dependency.

    Two operations *are* observable, because their handler would answer
    something else first, and those two are checked in
    `tests/integration/test_medical_consent_gate.py`.
    """
    route = _operations(application)[operation]
    expected = POLICY[operation].consent

    declared = _declared(route, deps.DECLARED_CONSENTS)
    assert declared == (set() if expected is None else {expected}), operation


def test_the_five_medical_operations_are_the_ones_gated_on_health_sync() -> None:
    """§9.2 lists them; a sixth would mean a new endpoint carrying the vault."""
    gated = {
        operation
        for operation, policy in POLICY.items()
        if policy.consent is ConsentKind.HEALTH_SYNC
    }

    assert gated == {
        ("/v1/sync/push", "post"),
        ("/v1/sync/pull", "get"),
        ("/v1/sync/key", "get"),
        ("/v1/sync/key", "post"),
        ("/v1/account/vault-reset", "post"),
    }


def test_no_operation_gates_on_the_cycle_consent() -> None:
    """§9.7 puts that filter in the client; the server enforces it per record.

    A dependency on `cycle_sync` would mean an endpoint that exists only for the
    cycle — and the whole point of opaque record keys is that no such endpoint
    can exist.
    """
    assert ConsentKind.CYCLE_SYNC not in {policy.consent for policy in POLICY.values()}


def test_every_unlimited_operation_states_why(application: FastAPI) -> None:
    """A blank reason is the silent hole this whole module exists to prevent."""
    del application
    for operation, policy in POLICY.items():
        if policy.at_the_door or policy.in_the_handler:
            continue
        assert len(policy.unlimited_because) > 40, operation


def test_the_per_ip_window_covers_the_auth_endpoint_and_nothing_else(
    application: FastAPI,
) -> None:
    """§11 keeps the address-keyed counter for auth alone, in process memory."""
    limits: dict[tuple[str, str], int] = {}
    for middleware in application.user_middleware:
        configured = middleware.kwargs.get("limits")
        if configured is not None:
            limits = dict(configured)

    # Keyed by method too: a `PATCH` to this path is a 405, not an attempt.
    assert limits == {("POST", "/v1/auth/telegram"): AUTH_ATTEMPTS_PER_MINUTE}


def test_every_declared_bucket_is_a_migrated_bucket() -> None:
    """A bucket without a `ck_rate_window_bucket` value is an unlimited endpoint.

    The CHECK would refuse the insert, the request would answer 500, and the
    limit would exist in the wiring and nowhere else. The migration test asserts
    the other half: that the constraint admits exactly these five.
    """
    assert set(deps.DECLARED_BUDGETS.values()) <= set(BUDGETS)
    assert set(BUDGETS) == set(RateBucket)


def test_no_bucket_name_leaks_a_consent_or_a_domain() -> None:
    """A bucket name lands in `diary.rate_window` and in nothing else, but §2
    holds anywhere a name is stored, and `telegram_reminders` is a consent."""
    for bucket in RateBucket:
        assert "telegram" not in bucket.value
        assert "cycle" not in bucket.value
        assert "health" not in bucket.value


def test_the_recorded_handler_buckets_are_the_ones_no_door_can_charge(
    application: FastAPI,
) -> None:
    """Exactly one bucket has a cost that is not one per request.

    If a second one appeared, «charged at the door» would stop being a complete
    account of the limits and this module would start lying by omission.
    """
    del application
    in_handlers = {
        bucket for policy in POLICY.values() for bucket in policy.in_the_handler
    }

    assert in_handlers == {RateBucket.PUSH_BYTES}


def test_the_schema_names_no_operation_the_policy_has_not_seen(
    application: FastAPI,
) -> None:
    """The OpenAPI document and the route table must not disagree.

    `test_openapi_surface.py` pins the published paths; this pins the
    method-level operations, which is the granularity a limit lives at.
    """
    schema: dict[str, Any] = application.openapi()
    documented = {
        (path, method)
        for path, operations in schema["paths"].items()
        for method in operations
    }

    assert documented == set(POLICY)
