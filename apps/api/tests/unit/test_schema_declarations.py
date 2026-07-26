"""The OpenAPI document says what the server does — asserted, not hoped.

Every assertion here answers a finding of the schemathesis run of §12. A fuzzer
with a random seed is not a regression test, so each finding gets one:

* nine statuses were reachable and two were declared;
* the 422 body was declared as Pydantic's per-field array while §11 requires one
  constant string;
* three operations can answer 422 from the **domain** (`quiet_hours_violation`,
  `unknown_timezone`), which is a different body on the same status;
* the record-key pattern and the counter bounds lived in `field_validator`s,
  which do not reach the document — so the schema promised `""` was acceptable.

And one assertion that answers no finding but forbids one: **500 is declared
nowhere.** A server error is a defect; the moment it becomes an expected status,
`not_a_server_error` stops meaning anything.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

from app.domain.vault import MAX_COUNTER, MAX_ENVELOPE_BYTES, MAX_RECORD_BYTES
from app.infra.clock import SystemClock
from app.infra.config import Settings
from app.infra.consent_copy import FileConsentCopyRegistry
from app.infra.telegram.initdata import InitDataValidator
from app.main import CONSENT_COPY_ROOT, AppDependencies, create_app
from app.schemas.sync import MAX_ENVELOPE_B64_CHARS, MAX_PAYLOAD_B64_CHARS
from app.services.erasure_journal import DatabaseErasureJournal

#: Each operation and the statuses its code can actually produce.
#:
#: The list is the record of a decision, so it is exhaustive rather than
#: illustrative: an operation that starts answering a tenth status has to be
#: added here, and one that stops answering a declared one has to be removed.
DECLARED: dict[tuple[str, str], set[int]] = {
    ("/health", "get"): {200},
    ("/v1/auth/telegram", "post"): {200, 400, 401, 403, 409, 422, 429, 503},
    ("/v1/sessions", "get"): {200, 401, 422, 429},
    ("/v1/sessions/revoke-others", "post"): {200, 401, 422, 429},
    ("/v1/consents", "get"): {200, 401, 422},
    ("/v1/consents", "post"): {201, 400, 401, 409, 422, 429, 503},
    ("/v1/consents/revoke", "post"): {200, 400, 401, 409, 422},
    ("/v1/account/delete", "post"): {200, 401, 403, 422},
    ("/v1/account/vault-reset", "post"): {200, 401, 403, 422, 429},
    ("/v1/sync/push", "post"): {200, 400, 401, 403, 409, 410, 413, 422, 429},
    ("/v1/sync/pull", "get"): {200, 401, 403, 410, 422, 429},
    ("/v1/sync/key", "get"): {200, 401, 403, 404, 422, 429},
    ("/v1/sync/key", "post"): {200, 400, 401, 403, 409, 413, 422, 429},
    ("/v1/reminders/settings", "get"): {200, 401, 403, 404, 422, 429},
    ("/v1/reminders/settings", "put"): {200, 400, 401, 403, 404, 422, 429},
}

#: The three that can refuse a *well-formed* value from the domain, so their 422
#: carries either shape. §10 keeps the tz database and the quiet hours in the
#: domain rather than in the schema, which is why this list is not empty.
DOMAIN_422 = {
    ("/v1/auth/telegram", "post"),
    ("/v1/consents", "post"),
    ("/v1/reminders/settings", "put"),
}


def _app() -> FastAPI:
    settings = Settings.from_env(
        {
            "API_DATABASE_URL": "postgresql://api_rw@localhost:5432/diary",
            "TELEGRAM_BOT_ID": "1234567890",
            "WEBAPP_URL": "https://example.invalid",
            "APP_ENV": "development",
        }
    )
    consent_copy = FileConsentCopyRegistry(CONSENT_COPY_ROOT)

    def _no_database() -> object:
        raise AssertionError("describing the API must not need a database")

    return create_app(
        AppDependencies(
            settings=settings,
            unit_of_work=_no_database,  # type: ignore[arg-type]
            clock=SystemClock(),
            init_data=InitDataValidator(
                bot_id=settings.telegram_bot_id,
                public_key=settings.telegram_ed25519_public_key,
            ),
            consent_copy=consent_copy,
            erasure_journal=DatabaseErasureJournal(
                _no_database,  # type: ignore[arg-type]
                consent_copy,
            ),
        )
    )


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    document: dict[str, Any] = _app().openapi()
    return document


def _operation(schema: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    operation: dict[str, Any] = schema["paths"][path][method]
    return operation


@pytest.mark.parametrize("operation", sorted(DECLARED))
def test_each_operation_declares_exactly_the_statuses_it_can_answer(
    schema: dict[str, Any],
    operation: tuple[str, str],
) -> None:
    path, method = operation
    declared = {int(code) for code in _operation(schema, path, method)["responses"]}

    assert declared == DECLARED[operation], operation


def test_no_operation_declares_a_server_error(schema: dict[str, Any]) -> None:
    """A 500 is a defect, and a declared defect is one nobody has to fix.

    The fuzzer's `not_a_server_error` check is the reason this matters: declaring
    500 would make it pass on the very failure it exists to report.
    """
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for code in operation["responses"]:
                assert not str(code).startswith("5") or code == "503", (path, method)


def test_the_only_five_hundred_class_status_anywhere_is_the_frozen_copy_guard(
    schema: dict[str, Any],
) -> None:
    """503 is the Phase 1 blocker refusing to create a consent, not a fault."""
    with_503 = {
        (path, method)
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        if "503" in operation["responses"]
    }

    assert with_503 == {("/v1/auth/telegram", "post"), ("/v1/consents", "post")}


def _body_schema(operation: dict[str, Any], code: str) -> dict[str, Any]:
    content = operation["responses"][code]["content"]["application/json"]
    result: dict[str, Any] = content["schema"]
    return result


@pytest.mark.parametrize("operation", sorted(DECLARED))
def test_the_validation_refusal_is_declared_as_one_string(
    schema: dict[str, Any],
    operation: tuple[str, str],
) -> None:
    """§11's sanitized 422, not Pydantic's array of per-field errors.

    FastAPI documents `HTTPValidationError` with `detail` an **array** carrying
    `input` — the submitted value, which for this API is medical content. The
    handler has always answered one constant string; only the document was
    wrong, and no client reads the document, so nothing but the fuzzer noticed.
    """
    path, method = operation
    if 422 not in DECLARED[operation]:
        pytest.skip("no parameters and no body")
    declared = _body_schema(_operation(schema, path, method), "422")

    if operation in DOMAIN_422:
        names = {
            reference["$ref"].rsplit("/", 1)[-1] for reference in declared["anyOf"]
        }
        assert names == {"ValidationRefusal", "ErrorResponse"}, operation
    else:
        assert declared["$ref"].endswith("ValidationRefusal"), operation


def test_the_component_for_a_validation_refusal_holds_one_string(
    schema: dict[str, Any],
) -> None:
    component = schema["components"]["schemas"]["ValidationRefusal"]

    assert component["required"] == ["detail"]
    assert component["properties"]["detail"]["type"] == "string"
    assert component["additionalProperties"] is False


def test_the_component_for_a_coded_refusal_holds_one_ascii_code(
    schema: dict[str, Any],
) -> None:
    component = schema["components"]["schemas"]["ErrorResponse"]

    assert component["required"] == ["error"]
    assert set(component["properties"]) == {"error"}
    assert component["additionalProperties"] is False


def test_the_record_key_pattern_reaches_the_document(schema: dict[str, Any]) -> None:
    """It lived in a `field_validator`, which OpenAPI never sees.

    So the schema promised that `record_key: ""` was acceptable, the fuzzer
    generated it, and the server answered 422 — a real disagreement between the
    contract and the code, with the contract at fault.
    """
    change = schema["components"]["schemas"]["ChangeInput"]

    assert change["properties"]["record_key"]["pattern"] == "^[0-9a-f]{64}$"


def test_the_counter_bounds_reach_the_document(schema: dict[str, Any]) -> None:
    """A cursor of 10**22 reached PostgreSQL and the request answered 500."""
    push = schema["components"]["schemas"]["PushRequest"]
    change = schema["components"]["schemas"]["ChangeInput"]
    pull = _operation(schema, "/v1/sync/pull", "get")

    assert push["properties"]["base_revision"]["maximum"] == MAX_COUNTER
    assert change["properties"]["client_ts_ms"]["maximum"] == MAX_COUNTER
    for parameter in pull["parameters"]:
        if parameter["name"] in {"since", "consent_epoch"}:
            assert parameter["schema"]["maximum"] == MAX_COUNTER, parameter["name"]


def test_the_payload_bound_is_the_base64_of_the_record_limit(
    schema: dict[str, Any],
) -> None:
    """And it leaves the 413 branch of §9.5 reachable.

    base64 pads to four, so 64 KiB and 64 KiB + 1..2 encode to the same length:
    a payload the service refuses with `payload_too_large` still passes the
    schema, which is what keeps that refusal alive rather than replacing it
    with a 422.
    """
    change = schema["components"]["schemas"]["ChangeInput"]
    field = change["properties"]["payload_b64"]
    declared = next(
        option["maxLength"] for option in field["anyOf"] if "maxLength" in option
    )

    assert declared == MAX_PAYLOAD_B64_CHARS
    assert MAX_PAYLOAD_B64_CHARS == 4 * ((MAX_RECORD_BYTES + 2) // 3)


def test_the_envelope_bound_is_declared_at_all(schema: dict[str, Any]) -> None:
    """§7: the envelope is 32 bytes wrapped. «Unbounded» is not a size.

    `ck_vault_key_wrapped_dek_nonempty` refuses an empty envelope and says
    nothing about a large one, so before Phase 5 a stepped-up caller could store
    an arbitrarily large blob under a key the server cannot even read.
    """
    request = schema["components"]["schemas"]["KeyWriteRequest"]

    assert request["properties"]["wrapped_dek"]["maxLength"] == MAX_ENVELOPE_B64_CHARS
    assert MAX_ENVELOPE_B64_CHARS == 4 * ((MAX_ENVELOPE_BYTES + 2) // 3)


def test_the_time_pattern_reaches_the_document(schema: dict[str, Any]) -> None:
    update = schema["components"]["schemas"]["ReminderSettingsUpdate"]
    grant = schema["components"]["schemas"]["ReminderSettingsInput"]

    assert update["properties"]["time"]["pattern"] == r"^([01]\d|2[0-3]):[0-5]\d$"
    assert grant["properties"]["time"]["pattern"] == r"^([01]\d|2[0-3]):[0-5]\d$"


def test_the_consent_digest_pattern_reaches_the_document(
    schema: dict[str, Any],
) -> None:
    grant = schema["components"]["schemas"]["GrantInput"]

    assert grant["properties"]["text_sha256"]["pattern"] == "^[0-9a-f]{64}$"
