"""The published API surface, as a privacy assertion rather than a style rule.

Two DoD items live here: no path template carries a consent name (§9.2), and
phase 1 exposes no medical endpoint at all. The second is checked by pinning
the complete set of paths — an endpoint added by accident fails the test rather
than passing unnoticed.
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

PHASE_1_PATHS = {
    "/health",
    "/v1/auth/telegram",
    "/v1/sessions",
    "/v1/sessions/revoke-others",
    "/v1/consents",
    "/v1/consents/revoke",
    "/v1/account/delete",
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


@pytest.mark.parametrize("kind", [kind.value for kind in ConsentKind])
def test_no_path_template_carries_a_consent_name(
    schema: dict[str, Any],
    kind: str,
) -> None:
    """A path reaches the proxy request line; the active set is an inference."""
    assert all(kind not in path for path in schema["paths"])


def test_the_published_surface_is_exactly_phase_one(schema: dict[str, Any]) -> None:
    assert set(schema["paths"]) == PHASE_1_PATHS


def test_there_are_no_medical_endpoints(schema: dict[str, Any]) -> None:
    for path in schema["paths"]:
        assert not path.startswith("/v1/sync")
        assert "entry" not in path
        assert "cycle" not in path
        assert "vault" not in path
        assert "reminders" not in path


def _url_parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """Path and query parameters only: a header never reaches the request line."""
    return [
        parameter
        for parameter in operation.get("parameters", [])
        if parameter["in"] in {"path", "query"}
    ]


def test_nothing_travels_in_a_path_or_query_parameter(
    schema: dict[str, Any],
) -> None:
    """§2 and §11 keep medical values and identifiers out of the URL."""
    for path, operations in schema["paths"].items():
        assert "{" not in path, path
        for operation in operations.values():
            assert _url_parameters(operation) == [], path


def test_revocation_takes_the_kind_in_the_body(schema: dict[str, Any]) -> None:
    operation = schema["paths"]["/v1/consents/revoke"]["post"]

    assert "requestBody" in operation
    assert _url_parameters(operation) == []
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    assert body["$ref"].endswith("RevokeRequest")
