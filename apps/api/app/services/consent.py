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
from app.domain.events import ConsentRevoked
from app.domain.identity import (
    ConfirmRequired,
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
    # §9.7: HMAC(k_index,'cycle'), named by the client when it grants
    # `cycle_sync`. Only phase 3 deletes by it; phase 2 stores it and gates
    # pushes on it.
    record_key_cycle: bytes | None = None


@dataclass(frozen=True, slots=True)
class RevocationOutcome:
    revoked: list[ConsentKind]
    account_erased: bool
    erasure_reference: UUID | None = None


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
        """Validate and record one consent inside the caller's transaction.

        The account row is locked first, exactly as revocation does: without it
        two concurrent grants both read an empty active set and the second one
        reaches the partial unique index as an integrity error instead of a
        clean 409.
        """
        unit.accounts.lock(account_id)
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

        # §9.7: pull повідомляє про зміну згод нейтральним прапорцем, і його
        # джерело — лічильник у рядку сейфа, а не годинник. Без інкременту тут
        # пристрій B ніколи не дізнався б про відкликання й отримував би вічний
        # 409 без жодної підказки.
        unit.vault.ensure_counters(account_id)
        unit.vault.bump_consent_epoch(account_id)
        unit.consents.grant(
            account_id,
            kind=request.kind,
            text_version=text.text_version,
            text_sha256=text.sha256,
            text_locale=text.locale,
            record_key_cycle=(
                request.record_key_cycle
                if request.kind is ConsentKind.CYCLE_SYNC
                else None
            ),
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
        last_acked_revision: int = 0,
        acknowledge_incomplete: bool = False,
    ) -> RevocationOutcome:
        """Revoke one consent, cascading and erasing where §4.3 requires it.

        Everything here happens in the caller's single transaction, and that is
        a requirement rather than a convenience: §9.8 makes the account row lock
        the barrier against an in-flight push, §4.3 puts the counter reset
        beside `revoked_at`, and §9.7 puts the hard DELETE there too. Any of it
        deferred to a follow-up job would leave a window in which the consent is
        gone and the data is not.
        """
        # The same first lock as the push transaction of §9.1, step 1. This is
        # the barrier of §9.8: a push either committed before it (and its rows
        # are visible to the DELETE below), or it starts afterwards and fails
        # the in-transaction consent check.
        unit.accounts.lock(account_id)
        active = unit.consents.active_kinds(account_id)
        if kind not in active:
            raise ConsentPrecondition()

        # Cycle data lives in the same vault, so withdrawing the vault
        # withdraws it too — in the same transaction, never as a follow-up.
        kinds = [kind]
        if kind is ConsentKind.HEALTH_SYNC and ConsentKind.CYCLE_SYNC in active:
            kinds.append(ConsentKind.CYCLE_SYNC)

        if kind is ConsentKind.HEALTH_SYNC and not acknowledge_incomplete:
            self._assert_device_has_everything(
                unit,
                account_id=account_id,
                last_acked_revision=last_acked_revision,
            )

        revoked = unit.consents.revoke(
            account_id,
            kinds=kinds,
            reason=reason,
            now=now,
        )
        if revoked:
            # Той самий нейтральний сигнал, що й при grant: пристрій дізнається
            # «щось зі згодами змінилося» і мусить перечитати їх ДО будь-якого
            # pruning (§9.4), не дізнаючись із pull назви жодної згоди.
            unit.vault.ensure_counters(account_id)
            unit.vault.bump_consent_epoch(account_id)
        for revoked_kind in revoked:
            event = ConsentRevoked(account_id=account_id, kind=revoked_kind)
            unit.outbox.publish(
                event_type=event.event_type,
                payload=event.to_payload(),
                now=now,
            )

        remaining = unit.consents.active_kinds(account_id)
        if not remaining and reason is RevokeReason.USER:
            # §4.3: the account exists only alongside a consent, and the user
            # asked for this. A full erasure covers the vault too, so the
            # partial erasure below would be redundant work on rows that are
            # about to be cascaded away.
            reference = self._erasure.erase(unit, account_id=account_id, now=now)
            return RevocationOutcome(
                revoked=revoked,
                account_erased=True,
                erasure_reference=reference,
            )

        # One erasure code per revocation, never two, and the partial erasures
        # sit below the full one for the same reason `sync_off` already did: a
        # full erasure deletes these rows itself, so journalling the partial
        # code first would record two facts about one deletion.
        #
        # The branches are exclusive because they can be: `revoke` takes a
        # single kind and the only cascade is `health_sync → cycle_sync`, so
        # reminders and the vault are never lost in the same call. That is why
        # one reference still describes the whole outcome.
        partial_reference: UUID | None = None
        if ConsentKind.TELEGRAM_REMINDERS in revoked:
            partial_reference = self._erasure.erase_reminders(
                unit,
                account_id=account_id,
                now=now,
            )
        elif ConsentKind.HEALTH_SYNC in revoked:
            partial_reference = self._erasure.erase_vault(
                unit,
                account_id=account_id,
                now=now,
            )
        elif ConsentKind.CYCLE_SYNC in revoked:
            self._delete_named_cycle_records(unit, account_id=account_id)

        # A consent lost for any reason other than the user's decision gets the
        # 30-day window of §4.3; the sweeper owns that deadline.
        return RevocationOutcome(
            revoked=revoked,
            account_erased=False,
            erasure_reference=partial_reference,
        )

    def _assert_device_has_everything(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
        last_acked_revision: int,
    ) -> None:
        """§8: refuse once if the server holds data this device never saw.

        The compensating control that stands in for the step-up Art. 7(3)
        forbids here. The server looks only at revisions and never at content —
        it could not look at content even if it wanted to.

        The predicate asks whether a *row* above that revision exists, not
        whether the counter moved past it. §8 states it as
        `max(last_acked_revision) < current_revision`, and taken literally that
        fires after the hard DELETE of §9.7: revoking `cycle_sync` spends a
        revision without leaving anything behind, so the very ordinary sequence
        "turn cycle sync off, then turn sync off" would always answer «there is
        data here you have not seen» about data that does not exist. A control
        that fires every time is a control the user learns to click through —
        which is the same reason §8 rejects the per-session alternative.
        """
        if unit.vault.has_records_above(account_id, revision=last_acked_revision):
            raise ConfirmRequired()

    def _delete_named_cycle_records(
        self,
        unit: UnitOfWork,
        *,
        account_id: UUID,
    ) -> None:
        """§9.7: hard DELETE by the key the client named, no tombstone.

        The revision is incremented only when a row actually went away. A bump
        on nothing would tell every other device to come back for a change that
        does not exist, and on a vault whose records are all older than the new
        counter it would push `current_revision` past the last real revision for
        no reason.

        A missing `record_key_cycle` is a defined outcome, not a silent one: the
        consent is revoked and nothing is deleted, because the server cannot
        tell which ciphertext is the cycle until the client names it. §9.7 says
        so, and the honest thing is to leave it visible rather than to guess.
        """
        named = unit.consents.named_cycle_key(account_id)
        if named is None:
            return
        unit.vault.ensure_counters(account_id)
        counters = unit.vault.lock_counters(account_id)
        removed = unit.vault.delete_named_key(account_id, record_key=named)
        if removed:
            unit.vault.set_current_revision(
                account_id,
                value=counters.current_revision + 1,
            )

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
