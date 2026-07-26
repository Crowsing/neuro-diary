"""HTTP dependency declarations.

This module never imports `app.infra`: everything it names is a protocol from
`app.services.ports`, and the implementations arrive through `app.state`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request

from app.domain.identity import AuthInvalid, ConsentKind
from app.domain.records import SessionRecord
from app.services.auth import AuthService
from app.services.consent import ConsentService
from app.services.erasure import ErasureService
from app.services.ports import ConsentCopyPort, UnitOfWorkFactory
from app.services.reminder import ReminderService
from app.services.sync import SyncService

BEARER_PREFIX = "Bearer "


@dataclass(frozen=True, slots=True)
class Services:
    auth: AuthService
    consents: ConsentService
    erasure: ErasureService
    consent_copy: ConsentCopyPort
    unit_of_work: UnitOfWorkFactory
    sync: SyncService
    reminders: ReminderService
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


def require_consent(
    kind: ConsentKind,
) -> Callable[[Request, Services, str], SessionRecord]:
    """§11: the entry check for an endpoint that carries medical data.

    It resolves the session and refuses with `consent_required` before the
    handler runs. §11 asks for the check **twice**, and this is only the first
    half: it commits nothing and holds no lock, so a revocation that commits a
    microsecond later would slip past it. The second half lives inside the write
    transaction, where it reads the rows that write is about to touch — which is
    the same shape §9.1 gives the sync path.

    The session is returned rather than discarded so the handler does not
    resolve it a second time; it re-checks the consent, not the token.
    """

    def dependency(
        request: Request,
        services: ServicesDep,
        token: BearerDep,
    ) -> SessionRecord:
        with services.unit_of_work() as unit:
            session = services.auth.resolve_session(unit, token)
            if session is None:
                raise AuthInvalid()
            # Set before the consent check, so a 403 is still attributable in
            # the log to a pseudonymous account rather than to nobody.
            request.state.account_id = session.account_id
            services.consents.require_active(
                unit,
                account_id=session.account_id,
                kind=kind,
            )
        return session

    return dependency


#: The only consent this phase gates on. Declared once so a second endpoint
#: cannot quietly gate on a different one.
RemindersConsentDep = Annotated[
    SessionRecord,
    Depends(require_consent(ConsentKind.TELEGRAM_REMINDERS)),
]
