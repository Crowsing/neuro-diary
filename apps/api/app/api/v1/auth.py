"""Authentication and session routes (§8)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.v1.deps import AccountOpsDep, ServicesDep
from app.api.v1.mapping import consent_outputs, to_grant_request
from app.schemas.identity import (
    AuthRequest,
    AuthResponse,
    RevokedSessionsOutput,
    SessionListOutput,
    SessionOutput,
)

router = APIRouter(prefix="/v1", tags=["auth"])


@router.post("/auth/telegram", response_model=AuthResponse)
def authenticate(payload: AuthRequest, services: ServicesDep) -> AuthResponse:
    result = services.auth.authenticate(
        payload.init_data,
        grant=to_grant_request(payload.grant),
    )
    return AuthResponse(
        session_token=result.token,
        expires_at=result.session.expires_at,
        consents=consent_outputs(result.consents),
    )


@router.get("/sessions", response_model=SessionListOutput)
def list_sessions(
    request: Request,
    services: ServicesDep,
    session: AccountOpsDep,
) -> SessionListOutput:
    with services.unit_of_work() as unit:
        summaries = services.auth.list_sessions(unit, session)
        unit.commit()
    return SessionListOutput(
        sessions=[
            SessionOutput(
                id=summary.id,
                created_at=summary.created_at,
                last_used_at=summary.last_used_at,
                expires_at=summary.expires_at,
                current=summary.current,
            )
            for summary in summaries
        ]
    )


@router.post("/sessions/revoke-others", response_model=RevokedSessionsOutput)
def revoke_other_sessions(
    request: Request,
    services: ServicesDep,
    session: AccountOpsDep,
) -> RevokedSessionsOutput:
    with services.unit_of_work() as unit:
        revoked = services.auth.revoke_other_sessions(unit, session)
        unit.commit()
    return RevokedSessionsOutput(revoked=revoked)
