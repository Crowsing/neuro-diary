"""Granting and revoking consent (§4.3, §9.2).

One contract covers all three kinds. `settings` is required if and only if the
kind is `telegram_reminders`, which is also what gives the schedule provision of
§4.4 its `local_time` and `tz`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from uuid import UUID

from app.domain.consent_copy import TextVersion
from app.domain.identity import (
    ConsentAlreadyActive,
    ConsentCopyNotFrozen,
    ConsentKind,
    ConsentPrecondition,
    ConsentTextMismatch,
    QuietHoursViolation,
    RevokeReason,
)
from app.domain.records import ConsentRecord
from app.domain.reminders import in_quiet_hours, next_fire_at
from app.services.erasure import ErasureService
from app.services.ports import ConsentCopyPort, UnitOfWork

DEFAULT_LOCALE = "uk"


@dataclass(frozen=True, slots=True)
class ReminderSettings:
    local_time: time
    timezone_name: str


@dataclass(frozen=True, slots=True)
class GrantRequest:
    kind: ConsentKind
    text_version: str
    text_sha256: bytes
    settings: ReminderSettings | None = None


@dataclass(frozen=True, slots=True)
class RevocationOutcome:
    revoked: list[ConsentKind]
    account_erased: bool


class ConsentService:
    def __init__(
        self,
        consent_copy: ConsentCopyPort,
        erasure: ErasureService,
        *,
        allow_unfrozen_copy: bool,
    ) -> None:
        self._consent_copy = consent_copy
        self._erasure = erasure
        self._allow_unfrozen_copy = allow_unfrozen_copy

    def list_active(self, unit: UnitOfWork, account_id: UUID) -> list[ConsentRecord]:
        return unit.consents.active(account_id)

    def grant(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        telegram_user_id: int,
        request: GrantRequest,
        now: datetime,
    ) -> None:
        """Validate and record one consent inside the caller's transaction."""
        text = self._consent_copy.grant_text(request.kind, locale=DEFAULT_LOCALE)
        version = TextVersion.parse(text.text_version)

        # Fail closed while the controller is unnamed: a consent recorded
        # against draft copy would be evidence of a text nobody approved.
        if not version.is_frozen and not self._allow_unfrozen_copy:
            raise ConsentCopyNotFrozen()

        if request.text_version != text.text_version:
            raise ConsentTextMismatch()
        if request.text_sha256 != text.sha256:
            raise ConsentTextMismatch()

        active = unit.consents.active_kinds(account_id)
        if request.kind in active:
            raise ConsentAlreadyActive()
        if (
            request.kind is ConsentKind.CYCLE_SYNC
            and ConsentKind.HEALTH_SYNC not in active
        ):
            raise ConsentPrecondition()

        if request.kind is ConsentKind.TELEGRAM_REMINDERS:
            self._provision_schedule(
                unit,
                account_id=account_id,
                telegram_user_id=telegram_user_id,
                settings=_require_settings(request),
                now=now,
            )

        unit.consents.grant(
            account_id,
            kind=request.kind,
            text_version=text.text_version,
            text_sha256=text.sha256,
            text_locale=text.locale,
            now=now,
        )

    def revoke(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        kind: ConsentKind,
        now: datetime,
        reason: RevokeReason = RevokeReason.USER,
    ) -> RevocationOutcome:
        """Revoke one consent, cascading and erasing where §4.3 requires it."""
        unit.accounts.lock(account_id)
        active = unit.consents.active_kinds(account_id)
        if kind not in active:
            raise ConsentPrecondition()

        # Cycle data lives in the same vault, so withdrawing the vault
        # withdraws it too — in the same transaction, never as a follow-up.
        kinds = [kind]
        if kind is ConsentKind.HEALTH_SYNC and ConsentKind.CYCLE_SYNC in active:
            kinds.append(ConsentKind.CYCLE_SYNC)

        revoked = unit.consents.revoke(
            account_id,
            kinds=kinds,
            reason=reason,
            now=now,
        )
        if ConsentKind.TELEGRAM_REMINDERS in revoked:
            unit.schedules.delete(account_id)

        remaining = unit.consents.active_kinds(account_id)
        if remaining or reason is not RevokeReason.USER:
            # A consent lost for any other reason gets the 30-day window of
            # §4.3; the sweeper owns that deadline.
            return RevocationOutcome(revoked=revoked, account_erased=False)

        self._erasure.erase(unit, account_id=account_id, now=now)
        return RevocationOutcome(revoked=revoked, account_erased=True)

    def _provision_schedule(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        telegram_user_id: int,
        settings: ReminderSettings,
        now: datetime,
    ) -> None:
        if in_quiet_hours(settings.local_time):
            raise QuietHoursViolation()
        fire_at = next_fire_at(settings.timezone_name, settings.local_time, now)
        unit.schedules.provision(
            account_id,
            # A Mini App runs in the private chat, where chat id equals user id.
            telegram_chat_id=telegram_user_id,
            timezone_name=settings.timezone_name,
            local_time=settings.local_time,
            next_fire_at=fire_at,
            now=now,
        )


def _require_settings(request: GrantRequest) -> ReminderSettings:
    if request.settings is None:  # pragma: no cover - schema rejects this first
        raise QuietHoursViolation()
    return request.settings
