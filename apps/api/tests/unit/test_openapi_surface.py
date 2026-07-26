"""The published API surface, as a privacy assertion rather than a style rule.

Two DoD items live here: no path template carries a consent name (§9.2), and
the URL carries nothing about the diary. The set of paths is pinned, so an
endpoint added by accident fails the test rather than passing unnoticed.

Phase 2 widens this surface for the first time. What is deliberately allowed
now, and why:

* `/v1/sync/*` and `/v1/account/vault-reset` are opaque: they name a mechanism,
  never a domain, a date, or a consent;
* `GET /v1/sync/pull` takes the first query parameters in this API. Only
  integers pass, from a closed allowlist — §11 already names `revision` as a
  loggable field, so a cursor in the request line reveals nothing the log does
  not already hold. Making pull a POST would have kept the old assertion
  verbatim at the cost of a larger deviation from §9.2.

Phase 4 widens it once more, and one earlier assertion had to go with it.
`test_no_path_names_a_domain_a_date_or_a_record` used to forbid the substring
`reminders` outright, which §10 contradicts by naming `GET/PUT
/v1/reminders/settings` in as many words. The property that assertion was
protecting is not lost: a path may not carry a **consent name**, and
`telegram_reminders` is the consent — `reminders` is the mechanism, exactly as
`sync` is. `test_no_path_template_carries_a_consent_name` still holds it, and
the ban is replaced by something stronger rather than weaker:
`test_the_reminder_surface_is_only_the_settings_resource` pins the whole
`/v1/reminders` subtree to the single resource §10 allows, so a delivery list —
the thing §10 actually forbids — fails the suite whoever adds it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

from app.domain.identity import ConsentKind
from app.infra.clock import SystemClock
from app.infra.config import Settings
from app.infra.consent_copy import FileConsentCopyRegistry
from app.infra.telegram.initdata import InitDataValidator
from app.main import CONSENT_COPY_ROOT, AppDependencies, create_app
from app.services.erasure_journal import DatabaseErasureJournal

PUBLISHED_PATHS = {
    "/health",
    "/v1/auth/telegram",
    "/v1/sessions",
    "/v1/sessions/revoke-others",
    "/v1/consents",
    "/v1/consents/revoke",
    "/v1/account/delete",
    "/v1/account/vault-reset",
    "/v1/sync/push",
    "/v1/sync/pull",
    "/v1/sync/key",
    # Phase 4, §10. The only reminder path there will ever be: a resource, not
    # a collection, and nothing below it.
    "/v1/reminders/settings",
}

#: Єдині параметри, яким дозволено потрапити в request line, і лише цілі.
ALLOWED_QUERY_PARAMETERS = {"since", "limit", "consent_epoch"}


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


@pytest.mark.parametrize("kind", [kind.value for kind in ConsentKind])
def test_no_path_template_carries_a_consent_name(
    schema: dict[str, Any],
    kind: str,
) -> None:
    """A path reaches the proxy request line; the active set is an inference."""
    assert all(kind not in path for path in schema["paths"])


def test_the_published_surface_is_exactly_what_is_declared(
    schema: dict[str, Any],
) -> None:
    assert set(schema["paths"]) == PUBLISHED_PATHS


def test_no_path_names_a_domain_a_date_or_a_record(schema: dict[str, Any]) -> None:
    """A mechanism may be named; what the mechanism carries may not."""
    for path in schema["paths"]:
        assert "entry" not in path
        assert "cycle" not in path
        assert "record" not in path
        # §10 forbids the delivery history from existing in the API. A path
        # that names one is the first way it would come back.
        assert "delivery" not in path
        assert "deliveries" not in path


def test_the_reminder_surface_is_only_the_settings_resource(
    schema: dict[str, Any],
) -> None:
    """§10: aggregate flags on one resource, never a list of occurrences."""
    reminder_paths = {
        path for path in schema["paths"] if path.startswith("/v1/reminders")
    }

    assert reminder_paths == {"/v1/reminders/settings"}
    assert set(schema["paths"]["/v1/reminders/settings"]) == {"get", "put"}


def test_the_settings_resource_returns_no_history(schema: dict[str, Any]) -> None:
    """The response shape has room for two booleans and nothing countable.

    A test on the path alone would pass a `history` field added to the body,
    which is the same disclosure by another route.
    """
    component = schema["components"]["schemas"]["ReminderSettingsOutput"]

    assert set(component["properties"]) == {
        "enabled",
        "time",
        "timezone",
        "quiet_blocked",
        "bot_blocked",
    }
    for name, field in component["properties"].items():
        if name.endswith("_blocked"):
            assert field["type"] == "boolean", name


def _url_parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """Path and query parameters only: a header never reaches the request line."""
    return [
        parameter
        for parameter in operation.get("parameters", [])
        if parameter["in"] in {"path", "query"}
    ]


def test_nothing_but_an_integer_cursor_travels_in_the_url(
    schema: dict[str, Any],
) -> None:
    """§2 and §11 keep medical values and identifiers out of the URL."""
    for path, operations in schema["paths"].items():
        assert "{" not in path, path
        for operation in operations.values():
            for parameter in _url_parameters(operation):
                assert parameter["in"] == "query", path
                assert parameter["name"] in ALLOWED_QUERY_PARAMETERS, path
                assert parameter["schema"]["type"] == "integer", path


def test_only_pull_takes_query_parameters(schema: dict[str, Any]) -> None:
    with_parameters = {
        path
        for path, operations in schema["paths"].items()
        if any(_url_parameters(operation) for operation in operations.values())
    }
    assert with_parameters == {"/v1/sync/pull"}


def test_revocation_takes_the_kind_in_the_body(schema: dict[str, Any]) -> None:
    operation = schema["paths"]["/v1/consents/revoke"]["post"]

    assert "requestBody" in operation
    assert _url_parameters(operation) == []
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    assert body["$ref"].endswith("RevokeRequest")
