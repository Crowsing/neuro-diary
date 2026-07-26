from __future__ import annotations

import inspect
from datetime import timedelta

import pytest

from app.api.v1.errors import STATUS_BY_ERROR
from app.domain import identity, reminders, vault
from app.domain.identity import DomainError


def _domain_errors() -> list[type[DomainError]]:
    """Every declared domain error, from every module that declares one.

    `reminders` joined the list in phase 4. Leaving it out would have let two
    new codes skip the uniqueness, ASCII and no-constructor-argument checks
    below — and the uniqueness one had already earned its keep by the time this
    line was written.
    """
    found: list[type[DomainError]] = []
    for module in (identity, reminders, vault):
        for _, member in inspect.getmembers(module, inspect.isclass):
            if issubclass(member, DomainError) and member is not DomainError:
                found.append(member)
    return sorted(set(found), key=lambda error: error.__name__)


def test_the_journal_ttl_is_never_shorter_than_the_tombstone_ttl() -> None:
    # §6.4: if the client guard fired later than the server compactor, the 410
    # path would be dead code and the boundary tests meaningless.
    assert vault.JOURNAL_TTL >= vault.TOMBSTONE_TTL
    assert vault.TOMBSTONE_TTL == vault.HORIZON + vault.COMPACTION_SLACK
    assert vault.HORIZON == timedelta(days=180)


def test_the_limits_match_the_plan() -> None:
    assert vault.MAX_RECORD_BYTES == 65_536
    assert vault.MAX_RECORDS_PER_PUSH == 200
    assert vault.MAX_PUSH_BYTES == 1_048_576
    assert vault.PULL_PAGE_LIMIT == 500
    # The three §11 budget numbers that used to be asserted here moved to
    # `tests/unit/test_rate_limits_domain.py` together with the constants, and
    # what replaced them is stronger: every bucket's limit *and* window, plus the
    # bucket set itself.
    assert vault.PREV_ENVELOPE_TTL == timedelta(days=7)
    assert vault.PREV_ENVELOPE_MIN_INTERVAL == timedelta(hours=24)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (vault.VaultForbidden, 403),
        (identity.ConsentRequired, 403),
        (reminders.NoSchedule, 404),
        (vault.VaultReset, 409),
        (vault.VaultGone, 410),
        (vault.PayloadTooLarge, 413),
    ],
)
def test_every_vault_error_maps_to_its_status(
    error: type[DomainError],
    status: int,
) -> None:
    assert STATUS_BY_ERROR[error] == status


def test_every_domain_error_has_a_status() -> None:
    # TransientConflict never reaches the wire: the router retries and then
    # gives up with the generic handler.
    unmapped = {
        error.__name__ for error in _domain_errors() if error not in STATUS_BY_ERROR
    }
    assert unmapped == {"TransientConflict"}


def test_error_codes_are_stable_ascii_and_unique() -> None:
    codes = [error.code for error in _domain_errors()]
    assert len(codes) == len(set(codes))
    for code in codes:
        assert code.isascii()
        assert code == code.lower()
        assert " " not in code


def test_no_domain_error_accepts_a_constructor_argument() -> None:
    # A field on the exception is the shortest path back to echoing an input
    # value into an error response, which §11 forbids.
    for error in _domain_errors():
        signature = inspect.signature(error.__init__)
        assert list(signature.parameters) == ["self"], error.__name__


def test_the_key_write_modes_are_rewrap_and_rekey() -> None:
    assert {mode.value for mode in vault.KeyWriteMode} == {"rewrap", "rekey"}
