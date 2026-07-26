"""Reminder settings routes (§10).

Two operations on one resource, and nothing else. There is no collection, no
delivery log and no «send a test message» — the last one looks harmless and is
not: a message outside the schedule is a message the dedupe key of §10 does not
cover, and the only way to test delivery without one would be to expose whether
it worked.

The consent is checked twice, as §11 requires: `RemindersConsentDep` before the
handler, and again inside the transaction that writes. Both refusals answer
`consent_required` with 403.
"""

from __future__ import annotations

from datetime import time
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.api.v1.deps import RemindersConsentDep, Services, ServicesDep
from app.domain.identity import ConsentKind
from app.schemas.reminders import ReminderSettingsOutput, ReminderSettingsUpdate
from app.services.reminder import ReminderSettingsView

router = APIRouter(prefix="/v1", tags=["reminders"])


def _resource(view: ReminderSettingsView) -> ReminderSettingsOutput:
    return ReminderSettingsOutput(
        enabled=view.enabled,
        # `%H:%M`, never `isoformat()`: the column is a `time` and isoformat
        # would start emitting seconds the day somebody stores one.
        time=view.local_time.strftime("%H:%M"),
        timezone=view.timezone_name,
        quiet_blocked=view.quiet_blocked,
        bot_blocked=view.bot_blocked,
    )


def _rate_limited(retry_after_seconds: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"error": "rate_limited"},
        headers={"Retry-After": str(retry_after_seconds)},
    )


def _spend_budget(
    request: Request,
    services: Services,
    account_id: UUID,
) -> JSONResponse | None:
    """§11: one request of the per-account window, committed on its own."""
    now = services.reminders.now()
    with services.unit_of_work() as unit:
        verdict = services.reminders.consume_budget(
            unit,
            account_id=account_id,
            now=now,
        )
        unit.commit()
    if verdict.allowed:
        return None
    request.state.error_code = "rate_limited"
    return _rate_limited(verdict.retry_after_seconds)


@router.get("/reminders/settings", response_model=ReminderSettingsOutput)
def read_settings(
    request: Request,
    services: ServicesDep,
    session: RemindersConsentDep,
) -> Response:
    request.state.account_id = session.account_id
    refusal = _spend_budget(request, services, session.account_id)
    if refusal is not None:
        return refusal

    with services.unit_of_work() as unit:
        services.consents.require_active(
            unit,
            account_id=session.account_id,
            kind=ConsentKind.TELEGRAM_REMINDERS,
        )
        view = services.reminders.read(unit, account_id=session.account_id)
    return JSONResponse(
        status_code=200,
        content=_resource(view).model_dump(),
    )


@router.put("/reminders/settings", response_model=ReminderSettingsOutput)
def write_settings(
    request: Request,
    payload: ReminderSettingsUpdate,
    services: ServicesDep,
    session: RemindersConsentDep,
) -> Response:
    request.state.account_id = session.account_id
    refusal = _spend_budget(request, services, session.account_id)
    if refusal is not None:
        return refusal

    hour, minute = payload.time.split(":")
    now = services.reminders.now()
    with services.unit_of_work() as unit:
        # The second half of §11's double check, and the only one that is a
        # barrier: it reads the consent rows inside the transaction that is
        # about to write the schedule.
        services.consents.require_active(
            unit,
            account_id=session.account_id,
            kind=ConsentKind.TELEGRAM_REMINDERS,
        )
        view = services.reminders.update(
            unit,
            account_id=session.account_id,
            enabled=payload.enabled,
            local_time=time(int(hour), int(minute)),
            timezone_name=payload.timezone,
            now=now,
        )
        unit.commit()
    return JSONResponse(
        status_code=200,
        content=_resource(view).model_dump(),
    )
