"""HTTP dependency declarations.

This module never imports `app.infra`: everything it names is a protocol from
`app.services.ports`, and the implementations arrive through `app.state`.

Phase 5 makes this the door every authenticated operation goes through, and the
order inside that door is the whole point: the session is resolved, the §11
window is charged **and committed**, and only then is the consent read. Charging
after the consent check is what let a token holder without a consent collect
unlimited cheap 403s — the hole Phase 4's review named and handed here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request

from app.api.v1.errors import RateLimitRefusal
from app.domain.identity import AuthInvalid, ConsentKind
from app.domain.rate_limits import RateBucket
from app.domain.records import SessionRecord
from app.services.auth import AuthService
from app.services.consent import ConsentService
from app.services.erasure import ErasureService
from app.services.ports import ConsentCopyPort, UnitOfWorkFactory
from app.services.rate_limits import RateLimitService
from app.services.reminder import ReminderService
from app.services.sync import SyncService

BEARER_PREFIX = "Bearer "

Dependency = Callable[[Request, "Services", str], SessionRecord]

#: Which bucket each dependency below charges, keyed by the closure itself.
#:
#: Written by the two factories and read by
#: `tests/unit/test_rate_limit_surface.py`, which answers «what does this
#: operation charge» from the wiring rather than from a table maintained twice. A
#: table alone would keep passing after somebody removed the dependency; this
#: registry cannot, because the entry disappears with the closure.
DECLARED_BUDGETS: dict[Dependency, RateBucket] = {}

#: Which consent each dependency below gates on, keyed the same way.
#:
#: Read by `tests/unit/test_rate_limit_surface.py`. The behavioural half of §11's
#: entry check cannot be observed everywhere — where the handler re-checks the
#: same consent inside its transaction, both designs answer 403 — so the wiring
#: itself is asserted for every medical operation, and the two endpoints whose
#: handler would answer something *else* first are asserted behaviourally.
DECLARED_CONSENTS: dict[Dependency, ConsentKind] = {}


@dataclass(frozen=True, slots=True)
class Services:
    auth: AuthService
    consents: ConsentService
    erasure: ErasureService
    consent_copy: ConsentCopyPort
    unit_of_work: UnitOfWorkFactory
    sync: SyncService
    reminders: ReminderService
    rate_limits: RateLimitService
    app_env: str


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def bearer_token(authorization: Annotated[str | None, Header()] = None) -> str:
    """Bearer only. §8 keeps the token out of the URL, so no query fallback."""
    if not authorization or not authorization.startswith(BEARER_PREFIX):
        raise AuthInvalid()
    token = authorization[len(BEARER_PREFIX) :].strip()
    if not token:
        raise AuthInvalid()
    return token


ServicesDep = Annotated[Services, Depends(get_services)]
BearerDep = Annotated[str, Depends(bearer_token)]


def _admit(
    request: Request,
    services: Services,
    token: str,
    *,
    kind: ConsentKind | None,
    budget: RateBucket | None,
) -> SessionRecord:
    """Resolve the session, charge the window, then read the consent.

    The commit sits between the charge and the consent read on purpose. A
    refusal that refunded its own window would let a client spend nothing
    exactly while it misbehaves — which is true of a 409 loop on push, and was
    true of a 403 loop on the reminder settings until this function existed.
    """
    with services.unit_of_work() as unit:
        session = services.auth.resolve_session(unit, token)
        if session is None:
            raise AuthInvalid()
        # Set before anything can refuse, so a 403 or a 429 is still
        # attributable in the log to a pseudonymous account rather than nobody.
        request.state.account_id = session.account_id
        verdict = (
            None
            if budget is None
            else services.rate_limits.consume(
                unit,
                account_id=session.account_id,
                bucket=budget,
                now=services.rate_limits.now(),
            )
        )
        unit.commit()
        if verdict is not None and not verdict.allowed:
            raise RateLimitRefusal(verdict.retry_after_seconds)
        if kind is not None:
            services.consents.require_active(
                unit,
                account_id=session.account_id,
                kind=kind,
            )
    return session


def require_consent(
    kind: ConsentKind,
    *,
    budget: RateBucket | None = None,
) -> Dependency:
    """§11: the entry check for an endpoint that carries medical data.

    It resolves the session and refuses with `consent_required` before the
    handler runs. §11 asks for the check **twice**, and this is only the first
    half: it commits nothing about the consent and holds no lock, so a
    revocation that commits a microsecond later would slip past it. The second
    half lives inside the write transaction, where it reads the rows that write
    is about to touch — which is the same shape §9.1 gives the sync path.

    The session is returned rather than discarded so the handler does not
    resolve it a second time; it re-checks the consent, not the token.

    `budget` is the endpoint's own §11 window, charged once per request before
    the consent is read. The keyword is optional so the form §11 names —
    `require_consent(kind)` — remains exactly what it says.
    """

    def dependency(
        request: Request,
        services: ServicesDep,
        token: BearerDep,
    ) -> SessionRecord:
        return _admit(request, services, token, kind=kind, budget=budget)

    DECLARED_CONSENTS[dependency] = kind
    if budget is not None:
        DECLARED_BUDGETS[dependency] = budget
    return dependency


def require_budget(bucket: RateBucket) -> Dependency:
    """A §11 window on an endpoint that gates on no consent.

    The endpoints §11 does not name write or read rows under a Bearer token, and
    a session alone is not a budget. Two of them deliberately get no window at
    all, and the reasons are recorded in `tests/unit/test_rate_limit_surface.py`
    beside the ones that do.
    """

    def dependency(
        request: Request,
        services: ServicesDep,
        token: BearerDep,
    ) -> SessionRecord:
        return _admit(request, services, token, kind=None, budget=bucket)

    DECLARED_BUDGETS[dependency] = bucket
    return dependency


#: §11 on the reminder settings resource: the consent, and the settings window.
RemindersConsentDep = Annotated[
    SessionRecord,
    Depends(
        require_consent(
            ConsentKind.TELEGRAM_REMINDERS,
            budget=RateBucket.REMINDERS_SETTINGS,
        )
    ),
]

#: §11 on the sync path. Phase 5 adds it: before this, `health_sync` was checked
#: only inside the transaction — one of the two halves §11 requires — and two of
#: the five medical endpoints (`/v1/sync/key` and `/v1/account/vault-reset`) were
#: not checking it at all.
SyncConsentDep = Annotated[
    SessionRecord,
    Depends(require_consent(ConsentKind.HEALTH_SYNC, budget=RateBucket.SYNC)),
]

#: The same consent, charged to the window §11 gives the envelope read: it hands
#: out the wrapped key, the salt and the KDF parameters.
KeyReadConsentDep = Annotated[
    SessionRecord,
    Depends(require_consent(ConsentKind.HEALTH_SYNC, budget=RateBucket.KEY_READ)),
]

#: Session and account operations §11 does not name.
AccountOpsDep = Annotated[
    SessionRecord,
    Depends(require_budget(RateBucket.ACCOUNT_OPS)),
]
