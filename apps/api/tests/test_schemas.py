import pytest
from pydantic import ValidationError

from app.schemas import (
    AppData,
    DoneEntry,
    DraftEntry,
    ExportState,
    LegacyAppDataInput,
    LegacyExportStateInput,
)


def v4_data() -> dict:
    return {
        "schemaVersion": 4,
        "entries": {
            "2026-01-15": {
                "status": "done",
                "wb": 6,
                "sym": {
                    "fatigue": {
                        "int": 3,
                        "side": None,
                        "impact": "Помітно",
                    }
                },
                "absent": ["numb"],
                "ctx": {
                    "stress": None,
                    "sleepQ": 2,
                    "sleepH": None,
                    "activity": False,
                    "actType": "",
                    "heat": None,
                    "extras": [],
                },
                "note": "Нотатка",
                "flare": None,
                "noSymptoms": False,
                "filledLater": False,
            },
            "2026-01-14": {
                "status": "draft",
                "d": {
                    "wb": None,
                    "wbSkip": False,
                    "sel": [],
                    "sym": {},
                    "absent": ["fatigue"],
                    "groupId": "g1",
                    "ctx": {
                        "stress": None,
                        "sleepQ": None,
                        "sleepH": None,
                        "activity": None,
                        "actType": "",
                        "heat": None,
                        "extras": [],
                    },
                    "note": "",
                    "flare": None,
                    "confirmed": False,
                    "noSymptoms": False,
                    "step": 2,
                },
            },
        },
        "cycleStarts": [],
        "active": ["fatigue"],
        "archived": ["numb"],
        "custom": [
            {
                "id": "custom-1",
                "name": "Власний",
                "type": "bool",
                "side": ["Ліва", "Права"],
                "extra": {
                    "label": "Де",
                    "opts": ["Рука"],
                    "multi": True,
                },
                "cat": "Власні",
            }
        ],
        "groups": [{"id": "g1", "name": "Група", "archived": False}],
        "symptomGroupIds": {"fatigue": ["g1"], "numb": ["g1"]},
        "cycleOn": False,
        "lock": False,
    }


def test_v4_entries_are_discriminated_and_round_trip_aliases() -> None:
    data = AppData.model_validate(v4_data())

    assert isinstance(data.entries["2026-01-15"], DoneEntry)
    assert isinstance(data.entries["2026-01-14"], DraftEntry)
    assert data.entries["2026-01-15"].ctx.activity is False
    assert data.entries["2026-01-15"].ctx.heat is None

    dumped = data.model_dump(by_alias=True, exclude_none=True)
    assert dumped["schemaVersion"] == 4
    assert dumped["entries"]["2026-01-15"]["sym"]["fatigue"]["int"] == 3
    assert dumped["entries"]["2026-01-15"]["absent"] == ["numb"]
    assert dumped["entries"]["2026-01-14"]["d"]["absent"] == ["fatigue"]
    assert dumped["custom"][0]["side"] == ["Ліва", "Права"]
    assert dumped["custom"][0]["extra"]["multi"] is True


def test_export_state_includes_report_group_settings_and_identity() -> None:
    state = ExportState.model_validate(
        {
            "view": "app",
            "report": {
                "step": 3,
                "period": 30,
                "groupIds": ["g1"],
                "syms": ["fatigue", "numb"],
                "includeGroupNames": True,
                "ctx": True,
                "cycle": False,
                "notes": True,
                "flares": True,
                "name": "Олена",
                "dob": "1990-02-03",
            },
            "data": v4_data(),
        }
    )

    dumped = state.model_dump(by_alias=True, exclude_none=True)
    assert dumped["report"]["groupIds"] == ["g1"]
    assert dumped["report"]["includeGroupNames"] is True
    assert dumped["report"]["name"] == "Олена"
    assert dumped["data"]["groups"][0]["name"] == "Група"


def test_legacy_reminder_fields_are_accepted_only_by_input_adapters() -> None:
    legacy_data = {**v4_data(), "remOn": True, "remTime": "07:30"}
    legacy_export = {
        "view": "app",
        "obRemOn": True,
        "obTime": "08:15",
        "data": legacy_data,
    }

    with pytest.raises(ValidationError):
        AppData.model_validate(legacy_data)
    with pytest.raises(ValidationError):
        ExportState.model_validate(legacy_export)

    data = LegacyAppDataInput.model_validate(legacy_data)
    state = LegacyExportStateInput.model_validate(legacy_export)
    dumped_data = data.model_dump(by_alias=True, exclude_none=True)
    dumped_state = state.model_dump(by_alias=True, exclude_none=True)

    assert dumped_data["entries"]["2026-01-15"]["note"] == "Нотатка"
    assert dumped_data["active"] == ["fatigue"]
    assert dumped_state["data"]["entries"]["2026-01-15"]["note"] == "Нотатка"
    assert dumped_state["data"]["groups"][0]["name"] == "Група"
    assert {"remOn", "remTime"}.isdisjoint(dumped_data)
    assert {"obRemOn", "obTime"}.isdisjoint(dumped_state)
    assert {"remOn", "remTime"}.isdisjoint(dumped_state["data"])


def test_current_output_schema_has_no_reminder_fields() -> None:
    app_data_fields = AppData.model_json_schema()["properties"]
    export_fields = ExportState.model_json_schema()["properties"]

    assert {"remOn", "remTime"}.isdisjoint(app_data_fields)
    assert {"obRemOn", "obTime"}.isdisjoint(export_fields)


def test_export_state_rejects_removed_states_subroute() -> None:
    with pytest.raises(ValidationError):
        ExportState.model_validate({"sub": "states"})


def test_contract_rejects_wrong_schema_or_web_type_mismatches() -> None:
    wrong_version = {**v4_data(), "schemaVersion": 3}
    with pytest.raises(ValidationError):
        AppData.model_validate(wrong_version)

    wrong_activity = v4_data()
    wrong_activity["entries"]["2026-01-15"]["ctx"]["activity"] = 1
    with pytest.raises(ValidationError):
        AppData.model_validate(wrong_activity)


# ------------------------------------------------- Phase 5: restated constants
#
# The import-linter contract «Schemas are standalone» keeps `app.schemas` free of
# `app.domain`, so three numbers and two patterns exist twice by design. Two
# copies that agree today is exactly the failure this file exists to prevent:
# the way they stop agreeing is silent, and the fuzzer of §12 would report it as
# a server defect rather than as a drifted constant.


def test_the_counter_ceiling_is_spelled_the_same_in_both_places() -> None:
    from app.domain import vault
    from app.schemas import identity as identity_schemas
    from app.schemas import sync as sync_schemas

    assert sync_schemas.MAX_COUNTER == vault.MAX_COUNTER
    assert identity_schemas.MAX_COUNTER == vault.MAX_COUNTER
    # And it is the JSON-safe one, not the `bigint` one: FastAPI types `maximum`
    # as a float, so 2**63 - 1 leaves the document one larger than the code
    # accepts.
    assert vault.MAX_COUNTER == 2**53 - 1


def test_the_base64_ceilings_follow_the_byte_limits_they_encode() -> None:
    from app.domain import vault
    from app.schemas import sync as sync_schemas

    assert sync_schemas.MAX_PAYLOAD_B64_CHARS == 4 * ((vault.MAX_RECORD_BYTES + 2) // 3)
    assert sync_schemas.MAX_ENVELOPE_B64_CHARS == 4 * (
        (vault.MAX_ENVELOPE_BYTES + 2) // 3
    )


def test_the_payload_ceiling_leaves_the_413_branch_reachable() -> None:
    """§9.5 answers 413 for an oversized record, and it must keep answering it.

    base64 pads to four, so 64 KiB and 64 KiB + 1 encode to the same number of
    characters: a payload the service refuses passes the schema, and the refusal
    stays a 413 rather than becoming a 422.
    """
    import base64

    from app.domain import vault
    from app.schemas import sync as sync_schemas

    oversized = base64.b64encode(b"x" * (vault.MAX_RECORD_BYTES + 1)).decode()

    assert len(oversized) <= sync_schemas.MAX_PAYLOAD_B64_CHARS


def test_the_time_pattern_is_spelled_the_same_in_both_schema_modules() -> None:
    from app.schemas import identity as identity_schemas
    from app.schemas import reminders as reminder_schemas

    assert reminder_schemas.TIME_PATTERN == identity_schemas.TIME_PATTERN


def test_the_hex_pattern_is_spelled_the_same_in_both_schema_modules() -> None:
    from app.schemas import identity as identity_schemas
    from app.schemas import sync as sync_schemas

    assert sync_schemas.RECORD_KEY_PATTERN == identity_schemas.HEX32_PATTERN
