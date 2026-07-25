"""Translation between wire schemas and service values.

Kept out of the routers so they stay parse → service → serialize.
"""

from __future__ import annotations

import base64
import binascii
from datetime import time
from typing import Any

from app.api.v1.deps import Services
from app.domain.identity import AuthInvalid, ConsentKind
from app.domain.records import ConsentRecord, RecordWrite, SessionRecord
from app.domain.vault import MAX_RECORD_BYTES, PayloadTooLarge
from app.services.consent import GrantRequest, ReminderSettings
from app.services.ports import UnitOfWork
from app.services.sync import KeyView, PullPage
from app.schemas.identity import ConsentOutput, GrantInput
from app.schemas.sync import PushRequest


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
        record_key_cycle=(
            None
            if payload.record_key_cycle is None
            else bytes.fromhex(payload.record_key_cycle)
        ),
    )


def decode_envelope(value: str) -> bytes:
    """Decode opaque bytes, refusing anything that is not base64 at all.

    Without `validate=True` a corrupted body would decode to garbage and reach
    the database, where a CHECK constraint would answer 500 instead of 413.
    """
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise PayloadTooLarge() from error


def record_writes(payload: PushRequest) -> list[RecordWrite]:
    writes: list[RecordWrite] = []
    for change in payload.changes:
        body = (
            None if change.payload_b64 is None else decode_envelope(change.payload_b64)
        )
        if body is not None and len(body) > MAX_RECORD_BYTES:
            raise PayloadTooLarge()
        writes.append(
            RecordWrite(
                record_key=bytes.fromhex(change.record_key),
                payload=body,
                tombstone=change.tombstone,
                client_ts_ms=change.client_ts_ms,
            )
        )
    return writes


def pull_payload(page: PullPage) -> dict[str, Any]:
    return {
        "records": [
            {
                "record_key": record.record_key.hex(),
                "payload_b64": (
                    None
                    if record.payload is None
                    else base64.b64encode(record.payload).decode()
                ),
                "tombstone": record.deleted,
                "revision": record.revision,
                "client_ts_ms": record.client_ts_ms,
            }
            for record in page.records
        ],
        "next_since": page.next_since,
        "current_revision": page.current_revision,
        "reset": page.reset,
        "consent_state_changed": page.consent_state_changed,
    }


def key_output(view: KeyView) -> dict[str, Any]:
    stored = view.stored
    return {
        "wrapped_dek": base64.b64encode(stored.wrapped_dek).decode(),
        "kdf": stored.kdf,
        "kdf_params": stored.kdf_params,
        "key_version": stored.key_version,
        "wrap_version": stored.wrap_version,
        "wrapped_dek_prev": (
            None
            if stored.wrapped_dek_prev is None
            else base64.b64encode(stored.wrapped_dek_prev).decode()
        ),
    }


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
