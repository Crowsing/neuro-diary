"""Contract fixtures shared with `apps/web` (§12, DoD of phase 2).

Both sides read the same files from the repository root, so a divergence in
what they accept fails here rather than in production.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.contract import AppData, DoneEntry, LegacyAppDataInput

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "contract"


def _load(name: str) -> dict[str, object]:
    payload: dict[str, object] = json.loads((FIXTURES / name).read_text("utf-8"))
    return payload


def test_the_fixture_directory_is_shared_and_present() -> None:
    assert FIXTURES.is_dir()
    assert (FIXTURES / "README.md").exists()


def test_a_valid_done_entry_is_accepted() -> None:
    entry = DoneEntry.model_validate(_load("done-entry.valid.json"))
    assert entry.absent == ["nausea"]
    assert "fatigue" in entry.sym


def test_an_entry_that_is_both_present_and_absent_is_refused() -> None:
    """§13.13: `sym ∩ absent = ∅`.

    The web side repairs such an entry on read; the active contract refuses
    it. Both behaviours are deliberate, and this fixture is what keeps them
    from drifting into agreeing on nonsense.
    """
    with pytest.raises(ValidationError):
        DoneEntry.model_validate(_load("done-entry.sym-absent-overlap.json"))


def test_the_minimal_app_data_is_accepted() -> None:
    data = AppData.model_validate(_load("app-data.v4.min.json"))
    assert data.schemaVersion == 4
    assert data.entries == {}


def test_the_legacy_boundary_refuses_the_overlap_too() -> None:
    """The ACL discards retired fields; it does not launder invalid ones."""
    overlapping = {
        **_load("app-data.v4.min.json"),
        "entries": {"2026-01-15": _load("done-entry.sym-absent-overlap.json")},
        "remOn": True,
    }
    with pytest.raises(ValidationError):
        LegacyAppDataInput.model_validate(overlapping)


def test_the_cycle_merge_fixture_states_the_expected_convergence() -> None:
    """The web side runs this case; api only pins its shape so it cannot rot."""
    fixture = _load("merge/cycle-reconvergence.json")
    for side in ("deviceA", "deviceB", "expected"):
        body = fixture[side]
        assert isinstance(body, dict)
        assert body["kind"] == "cycle"
        assert isinstance(body["starts"], list)
        assert isinstance(body["journal"], list)
