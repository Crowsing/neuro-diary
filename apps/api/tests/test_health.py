"""Health route and the sanitizers that guard every response.

`create_app` now takes its dependencies explicitly, so these tests build a
minimal set rather than reading the environment.
"""

import asyncio
import logging

import httpx
import pytest
from pydantic import BaseModel

from app.infra.config import Settings
from app.infra.consent_copy import FileConsentCopyRegistry
from app.main import CONSENT_COPY_ROOT, AppDependencies, create_app
from app.infra.clock import SystemClock
from app.infra.telegram.initdata import InitDataValidator
from app.services.erasure_journal import DatabaseErasureJournal


def _dependencies() -> AppDependencies:
    settings = Settings.from_env(
        {
            "API_DATABASE_URL": "postgresql://api_rw@localhost:5432/diary",
            "TELEGRAM_BOT_ID": "1234567890",
            "WEBAPP_URL": "https://example.invalid",
            "APP_ENV": "development",
        }
    )
    consent_copy = FileConsentCopyRegistry(CONSENT_COPY_ROOT)

    # No engine is built: these tests never reach a repository.
    def _no_database() -> object:
        raise AssertionError("the health route must not touch the database")

    return AppDependencies(
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


def _app() -> object:
    return create_app(_dependencies())


def test_health() -> None:
    async def _request() -> httpx.Response:
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get("/health")

    response = asyncio.run(_request())
    assert response.status_code == 200
    # `app_env` присутнє свідомо: стенд, який приймає незаморожені тексти
    # згод, мусить сказати про це видимо (блокер 1 промпту Фази 2), і джерелом
    # цього факту є сервер, а не прапорець збірки клієнта.
    assert response.json() == {"status": "ok", "app_env": "development"}


def test_validation_errors_do_not_echo_input() -> None:
    class Probe(BaseModel):
        count: int

    probe_app = create_app(_dependencies())

    @probe_app.post("/_validation-probe")
    def validate_probe(probe: Probe) -> dict[str, int]:
        return {"count": probe.count}

    async def _request() -> httpx.Response:
        transport = httpx.ASGITransport(app=probe_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            return await client.post(
                "/_validation-probe",
                json={"count": "PRIVATE_SENTINEL"},
            )

    response = asyncio.run(_request())
    assert response.status_code == 422
    assert response.json() == {"detail": "Request validation failed"}
    assert "PRIVATE_SENTINEL" not in response.text


def test_raw_uvicorn_access_log_is_disabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "PRIVATE_QUERY_SENTINEL"
    access_logger = logging.getLogger("uvicorn.access")
    was_disabled = access_logger.disabled
    access_logger.disabled = False

    try:
        create_app(_dependencies())
        assert access_logger.disabled
        access_logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1",
            "GET",
            f"/health?note={sentinel}",
            "1.1",
            200,
        )
        assert sentinel not in caplog.text
    finally:
        access_logger.disabled = was_disabled
