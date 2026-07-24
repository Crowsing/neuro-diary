"""Translation between wire schemas and service values.

Kept out of the routers so they stay parse → service → serialize.
"""

from __future__ import annotations

from datetime import time

from app.api.v1.deps import Services
from app.domain.identity import AuthInvalid, ConsentKind
from app.domain.records import ConsentRecord, SessionRecord
from app.services.consent import GrantRequest, ReminderSettings
from app.services.ports import UnitOfWork
from app.schemas.identity import ConsentOutput, GrantInput


def to_grant_request(payload: GrantInput | None) -> GrantRequest | None:
    if payload is None:
        return None
    settings = None
    if payload.settings is not None:
        hour, minute = payload.settings.time.split(":")
        settings = ReminderSettings(
            local_time=time(int(hour), int(minute)),
            timezone_name=payload.settings.timezone,
        )
    return GrantRequest(
        kind=ConsentKind(payload.kind),
        text_version=payload.text_version,
        text_sha256=bytes.fromhex(payload.text_sha256),
        settings=settings,
    )


def consent_outputs(records: list[ConsentRecord]) -> list[ConsentOutput]:
    return [
        ConsentOutput(
            kind=record.kind.value,
            granted_at=record.granted_at,
            text_version=record.text_version,
        )
        for record in records
    ]


def require_session(
    services: Services,
    unit: UnitOfWork,
    token: str,
) -> SessionRecord:
    session = services.auth.resolve_session(unit, token)
    if session is None:
        raise AuthInvalid()
    return session
