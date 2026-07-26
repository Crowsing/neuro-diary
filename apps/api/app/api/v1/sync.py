"""Vault synchronization routes (§9.2).

Thin by construction: parse, call the service, serialize. The two responses
that carry data on a refusal — `conflict_keys` and `current_wrap_version` — are
built here as `JSONResponse`, because a domain error cannot hold a field and
must not learn how (§11).
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from app.api.v1.deps import BearerDep, ServicesDep
from app.api.v1.mapping import (
    decode_envelope,
    key_output,
    pull_payload,
    record_writes,
    require_session,
)
from app.domain.identity import ProtectedOperation
from app.domain.vault import KeyWriteMode, RateBucket
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


def _rate_limited(retry_after_seconds: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"error": "rate_limited"},
        headers={"Retry-After": str(retry_after_seconds)},
    )


@router.post("/sync/push", response_model=PushAccepted)
def push(
    request: Request,
    payload: PushRequest,
    services: ServicesDep,
    token: BearerDep,
) -> Response:
    now = services.sync.now()
    writes = record_writes(payload)
    volume = sum(0 if write.payload is None else len(write.payload) for write in writes)

    # The budget is consumed in its own transaction, before the work: a 409 or
    # a 410 must not refund it, or a looping client would push for free exactly
    # when it is misbehaving.
    with services.unit_of_work() as unit:
        session = require_session(services, unit, token)
        request.state.account_id = session.account_id
        refusal: int | None = None
        for bucket, cost in ((RateBucket.SYNC, 1), (RateBucket.PUSH_BYTES, volume)):
            verdict = services.sync.consume_budget(
                unit,
                account_id=session.account_id,
                bucket=bucket,
                cost=cost,
                now=now,
            )
            if not verdict.allowed and refusal is None:
                refusal = verdict.retry_after_seconds
        unit.commit()
    if refusal is not None:
        request.state.error_code = "rate_limited"
        return _rate_limited(refusal)

    with services.unit_of_work() as unit:
        session = require_session(services, unit, token)
        request.state.account_id = session.account_id
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
    token: BearerDep,
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=500),
    consent_epoch: int = Query(default=0, ge=0),
) -> Response:
    now = services.sync.now()
    with services.unit_of_work() as unit:
        session = require_session(services, unit, token)
        request.state.account_id = session.account_id
        verdict = services.sync.consume_budget(
            unit,
            account_id=session.account_id,
            bucket=RateBucket.SYNC,
            cost=1,
            now=now,
        )
        unit.commit()
    if not verdict.allowed:
        request.state.error_code = "rate_limited"
        return _rate_limited(verdict.retry_after_seconds)

    with services.unit_of_work() as unit:
        session = require_session(services, unit, token)
        request.state.account_id = session.account_id
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
    token: BearerDep,
) -> Response:
    now = services.sync.now()
    with services.unit_of_work() as unit:
        session = require_session(services, unit, token)
        request.state.account_id = session.account_id
        # §11: this endpoint hands out the envelope, the salt and the KDF
        # parameters, so a stolen session must not turn into an offline
        # dictionary attack at leisure.
        verdict = services.sync.consume_budget(
            unit,
            account_id=session.account_id,
            bucket=RateBucket.KEY_READ,
            cost=1,
            now=now,
        )
        unit.commit()
    if not verdict.allowed:
        request.state.error_code = "rate_limited"
        return _rate_limited(verdict.retry_after_seconds)

    with services.unit_of_work() as unit:
        session = require_session(services, unit, token)
        request.state.account_id = session.account_id
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
    token: BearerDep,
) -> Response:
    now = services.sync.now()
    mode = KeyWriteMode(payload.mode)
    wrapped_dek = decode_envelope(payload.wrapped_dek)

    with services.unit_of_work() as unit:
        session = require_session(services, unit, token)
        request.state.account_id = session.account_id
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
