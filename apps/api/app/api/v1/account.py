"""Account routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.v1.deps import BearerDep, ServicesDep
from app.api.v1.mapping import require_session
from app.domain.identity import ProtectedOperation
from app.schemas.identity import AccountDeletedResponse
from app.schemas.sync import VaultResetResponse

router = APIRouter(prefix="/v1", tags=["account"])


@router.post("/account/delete", response_model=AccountDeletedResponse)
def delete_account(
    request: Request,
    services: ServicesDep,
    token: BearerDep,
) -> AccountDeletedResponse:
    """Art. 17, not Art. 7(3): identity is confirmed before erasing (§8)."""
    now = services.auth.now()
    with services.unit_of_work() as unit:
        session = require_session(services, unit, token)
        request.state.account_id = session.account_id
        services.auth.require_step_up(session, ProtectedOperation.ACCOUNT_DELETE)
        unit.accounts.lock(session.account_id)
        reference = services.erasure.erase(
            unit,
            account_id=session.account_id,
            now=now,
        )
        unit.commit()
    services.erasure.confirm(reference, now=now)
    return AccountDeletedResponse(status="erased")


@router.post("/account/vault-reset", response_model=VaultResetResponse)
def reset_vault(
    request: Request,
    services: ServicesDep,
    token: BearerDep,
) -> VaultResetResponse:
    """Drop the server copy and move all three counters past it (§9.4).

    `vault_key` is left alone on purpose, and it stays that way now that
    crypto-erasure exists: a vault-reset is the first half of a re-key (§7), and
    the new envelope arrives right after through `POST /v1/sync/key`. Deleting
    the envelope here would break the very flow this endpoint serves. Erasing it
    belongs to revocation, where no new envelope follows.
    """
    with services.unit_of_work() as unit:
        session = require_session(services, unit, token)
        request.state.account_id = session.account_id
        services.auth.require_step_up(session, ProtectedOperation.VAULT_RESET)
        revision = services.sync.vault_reset(unit, account_id=session.account_id)
        unit.commit()
    request.state.revision = revision
    return VaultResetResponse(new_revision=revision)
