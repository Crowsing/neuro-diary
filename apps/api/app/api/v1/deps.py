"""HTTP dependency declarations.

This module never imports `app.infra`: everything it names is a protocol from
`app.services.ports`, and the implementations arrive through `app.state`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request

from app.domain.identity import AuthInvalid
from app.services.auth import AuthService
from app.services.consent import ConsentService
from app.services.erasure import ErasureService
from app.services.ports import ConsentCopyPort, UnitOfWorkFactory
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
