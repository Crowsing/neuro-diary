"""Consent routes (§9.2).

The kind travels in the request body on every route. A path template such as
`/v1/consents/cycle_sync` would put it in the reverse proxy's request line, and
the set of active consents is a health inference (§13.12).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.deps import BearerDep, ServicesDep
from app.api.v1.mapping import consent_outputs, require_session, to_grant_request
from app.domain.identity import ConsentKind
from app.schemas.identity import (
    ConsentListOutput,
    GrantInput,
    RevokeRequest,
    RevokeResponse,
)

router = APIRouter(prefix="/v1", tags=["consents"])


@router.get("/consents", response_model=ConsentListOutput)
def list_consents(services: ServicesDep, token: BearerDep) -> ConsentListOutput:
    with services.unit_of_work() as unit:
        session = require_session(services, unit, token)
        records = services.consents.list_active(unit, session.account_id)
        unit.commit()
    return ConsentListOutput(consents=consent_outputs(records))


@router.post("/consents", response_model=ConsentListOutput, status_code=201)
def grant_consent(
    payload: GrantInput,
    services: ServicesDep,
    token: BearerDep,
) -> ConsentListOutput:
    request = to_grant_request(payload)
    assert request is not None
    with services.unit_of_work() as unit:
        session = require_session(services, unit, token)
        telegram_user_id = unit.identities.telegram_user_id_for(session.account_id)
        assert telegram_user_id is not None
        services.consents.grant(
            unit,
            account_id=session.account_id,
            telegram_user_id=telegram_user_id,
            request=request,
            now=services.auth.now(),
        )
        records = services.consents.list_active(unit, session.account_id)
        unit.commit()
    return ConsentListOutput(consents=consent_outputs(records))


@router.post("/consents/revoke", response_model=RevokeResponse)
def revoke_consent(
    payload: RevokeRequest,
    services: ServicesDep,
    token: BearerDep,
) -> RevokeResponse:
    with services.unit_of_work() as unit:
        session = require_session(services, unit, token)
        # Art. 7(3): no step-up here, whatever the age of the session.
        outcome = services.consents.revoke(
            unit,
            account_id=session.account_id,
            kind=ConsentKind(payload.kind),
            now=services.auth.now(),
        )
        unit.commit()
    return RevokeResponse(account_erased=outcome.account_erased)
