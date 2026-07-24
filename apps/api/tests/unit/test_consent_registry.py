"""CI checks over the shared consent copy registry.

The registry is the single source of the text a user is shown and of the hash
stored in `consent.text_sha256`. A drift between the two would make the stored
proof of consent describe a text nobody ever saw.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
REGISTRY_ROOT = REPO_ROOT / "consent-copy"
REGISTRY_FILE = REGISTRY_ROOT / "registry.json"

KINDS = {"health_sync", "telegram_reminders", "cycle_sync"}
TEXT_VERSION = re.compile(r"^(health_sync|telegram_reminders|cycle_sync)@\d+\.\d+$")
CONTROLLER_PLACEHOLDER = "[ім'я контролера]"

# §6.5: without these two statements the only declared transfer mechanism for
# Telegram (Art. 49(1)(a)) is not legally established.
TELEGRAM_TRANSFER_DISCLOSURES = (
    "для його країни немає рішення Єврокомісії про достатній захист",
    "не можемо укласти з ним договір",
)


def _registry() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    return payload


def _entries() -> list[dict[str, Any]]:
    registry = _registry()
    grants: list[dict[str, Any]] = registry["grants"]
    revocations: list[dict[str, Any]] = registry["revocations"]
    return [*grants, *revocations]


def _major(entry: dict[str, Any]) -> int:
    version: str = entry["text_version"]
    return int(version.split("@", 1)[1].split(".", 1)[0])


def test_registry_declares_exactly_the_three_gate_d_consents() -> None:
    registry = _registry()

    assert {entry["kind"] for entry in registry["grants"]} == KINDS
    assert {entry["kind"] for entry in registry["revocations"]} == KINDS
    assert len(registry["grants"]) == len(registry["revocations"]) == 3
    assert registry["locales"] == ["uk"]


def test_every_recorded_hash_matches_the_text_that_is_shown() -> None:
    for entry in _entries():
        text_file = REGISTRY_ROOT / entry["file"]
        digest = hashlib.sha256(text_file.read_bytes()).hexdigest()
        assert digest == entry["sha256"], entry["file"]


def test_no_registry_file_contains_an_unfilled_token() -> None:
    for entry in _entries():
        text = (REGISTRY_ROOT / entry["file"]).read_text(encoding="utf-8")
        assert "{{" not in text, entry["file"]


def test_text_versions_use_the_kind_major_minor_format() -> None:
    for entry in _entries():
        assert TEXT_VERSION.match(entry["text_version"]), entry["text_version"]
        assert entry["text_version"].startswith(f"{entry['kind']}@")
        assert entry["locale"] == "uk"


def test_frozen_copy_can_never_carry_the_controller_placeholder() -> None:
    for entry in _entries():
        if _major(entry) == 0:
            continue
        text = (REGISTRY_ROOT / entry["file"]).read_text(encoding="utf-8")
        assert CONTROLLER_PLACEHOLDER not in text, entry["file"]


def test_draft_copy_still_leaves_the_controller_unnamed() -> None:
    """The two texts that name a controller must keep the placeholder at 0.x.

    Without this the copy could be frozen by accident: a version bump would be
    the only visible change, while the text silently started naming nobody.
    """
    naming_a_controller = {"uk/health_sync/0.9.md", "uk/telegram_reminders/0.9.md"}
    unnamed = {
        entry["file"]
        for entry in _entries()
        if _major(entry) == 0
        and CONTROLLER_PLACEHOLDER
        in (REGISTRY_ROOT / entry["file"]).read_text(encoding="utf-8")
    }

    assert unnamed == naming_a_controller


def test_frozen_flag_tracks_the_major_version() -> None:
    for entry in _entries():
        assert entry["frozen"] is (_major(entry) >= 1), entry["text_version"]


@pytest.mark.parametrize("disclosure", TELEGRAM_TRANSFER_DISCLOSURES)
def test_telegram_consent_states_the_transfer_has_no_adequacy_decision(
    disclosure: str,
) -> None:
    entry = next(
        item for item in _registry()["grants"] if item["kind"] == "telegram_reminders"
    )
    text = (REGISTRY_ROOT / entry["file"]).read_text(encoding="utf-8")

    assert disclosure in text


def test_text_files_are_byte_stable_across_platforms() -> None:
    for entry in _entries():
        raw = (REGISTRY_ROOT / entry["file"]).read_bytes()
        assert raw, entry["file"]
        assert not raw.startswith(b"\xef\xbb\xbf"), entry["file"]
        assert b"\r" not in raw, entry["file"]
        assert raw.endswith(b"\n"), entry["file"]
