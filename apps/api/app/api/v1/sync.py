"""Vault synchronization routes (§9.2).

Thin by construction: parse, call the service, serialize. The two responses
that carry data on a refusal — `conflict_keys` and `current_wrap_version` — are
built here as `JSONResponse`, because a domain error cannot hold a field and
must not learn how (§11).

§11 wants the consent checked twice on every endpoint carrying medical data.
Phase 5 adds the first half here — `SyncConsentDep` and `KeyReadConsentDep` —
and the §11 window moves into it with them. Before that the sync path had only
the in-transaction half, and the envelope endpoints had neither: an account
holding `telegram_reminders` alone could write a `wrapped_dek` and reset a
vault it had never consented to keeping.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from app.api.v1.deps import KeyReadConsentDep, ServicesDep, SyncConsentDep
from app.api.v1.errors import RateLimitRefusal
from app.api.v1.mapping import (
    decode_envelope,
    key_output,
    pull_payload,
    record_writes,
)
from app.domain.identity import ProtectedOperation
from app.domain.rate_limits import RateBucket
from app.domain.vault import KeyWriteMode
from app.schemas.sync import (
    KeyOutput,
    KeyWriteAccepted,
    KeyWriteRequest,
    PullResponse,
    PushAccepted,
    PushRequest,
)
from app.services.sync import KeyWriteApplied, PushApplied

router = APIRouter(prefix="/v1", tags=["sync"])


@router.post("/sync/push", response_model=PushAccepted)
def push(
    request: Request,
    payload: PushRequest,
    services: ServicesDep,
    session: SyncConsentDep,
) -> Response:
    now = services.sync.now()
    writes = record_writes(payload)
    volume = sum(0 if write.payload is None else len(write.payload) for write in writes)

    # The request budget was charged at the door. The volume budget cannot be:
    # it is the only §11 window whose cost is not one per request, and nothing
    # outside the handler knows how many bytes arrived. Its own transaction
    # commits before the work, for the same reason as the other windows — a 409
    # or a 410 must not refund it.
    with services.unit_of_work() as unit:
        verdict = services.rate_limits.consume(
            unit,
            account_id=session.account_id,
            bucket=RateBucket.PUSH_BYTES,
            cost=volume,
            now=now,
        )
        unit.commit()
    if not verdict.allowed:
        raise RateLimitRefusal(verdict.retry_after_seconds)

    with services.unit_of_work() as unit:
        outcome = services.sync.push(
            unit,
            account_id=session.account_id,
            base_revision=payload.base_revision,
            changes=writes,
            now=now,
        )
        if isinstance(outcome, PushApplied):
            unit.commit()
            request.state.record_count = len(writes)
            request.state.revision = outcome.new_revision
            return JSONResponse(
                status_code=200,
                content={"new_revision": outcome.new_revision},
            )
        # Nothing is committed on a conflict: §9.1 promises zero rows written.
        request.state.error_code = "conflict"
        return JSONResponse(
            status_code=409,
            content={
                "reason": "conflict",
                "conflict_keys": [key.hex() for key in outcome.conflict_keys],
            },
        )


@router.get("/sync/pull", response_model=PullResponse)
def pull(
    request: Request,
    services: ServicesDep,
    session: SyncConsentDep,
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=500),
    consent_epoch: int = Query(default=0, ge=0),
) -> Response:
    with services.unit_of_work() as unit:
        page = services.sync.pull(
            unit,
            account_id=session.account_id,
            since=since,
            limit=limit,
            consent_epoch_seen=consent_epoch,
        )
        unit.commit()
    request.state.record_count = len(page.records)
    request.state.revision = page.current_revision
    return JSONResponse(status_code=200, content=pull_payload(page))


@router.get("/sync/key", response_model=KeyOutput)
def read_key(
    request: Request,
    services: ServicesDep,
    session: KeyReadConsentDep,
) -> Response:
    now = services.sync.now()
    with services.unit_of_work() as unit:
        view = services.sync.read_key(
            unit,
            account_id=session.account_id,
            stepped_up=services.auth.is_stepped_up(session),
            now=now,
        )
        unit.commit()
    if view is None:
        request.state.error_code = "no_vault_key"
        return JSONResponse(status_code=404, content={"error": "no_vault_key"})
    return JSONResponse(status_code=200, content=key_output(view))


@router.post("/sync/key", response_model=KeyWriteAccepted)
def write_key(
    request: Request,
    payload: KeyWriteRequest,
    services: ServicesDep,
    session: SyncConsentDep,
) -> Response:
    now = services.sync.now()
    mode = KeyWriteMode(payload.mode)
    wrapped_dek = decode_envelope(payload.wrapped_dek)

    with services.unit_of_work() as unit:
        # §7 and §8: step-up on every write, both modes. The server is
        # zero-knowledge and cannot tell a valid envelope from arbitrary bytes,
        # so a mistaken overwrite is unrecoverable by construction.
        services.auth.require_step_up(
            session,
            ProtectedOperation.REKEY
            if mode is KeyWriteMode.REKEY
            else ProtectedOperation.SYNC_KEY_WRITE,
        )
        outcome = services.sync.write_key(
            unit,
            account_id=session.account_id,
            mode=mode,
            expected_wrap_version=payload.expected_wrap_version,
            wrapped_dek=wrapped_dek,
            kdf=payload.kdf,
            kdf_params=dict(payload.kdf_params),
            now=now,
        )
        if isinstance(outcome, KeyWriteApplied):
            unit.commit()
            if outcome.erasure_reference is not None:
                services.erasure.confirm(outcome.erasure_reference, now=now)
            return JSONResponse(
                status_code=200,
                content={
                    "key_version": outcome.key_version,
                    "wrap_version": outcome.wrap_version,
                },
            )
        request.state.error_code = "wrap_conflict"
        return JSONResponse(
            status_code=409,
            content={"current_wrap_version": outcome.current_wrap_version},
        )
