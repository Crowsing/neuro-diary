import asyncio
import logging

import httpx
import pytest
from pydantic import BaseModel

from app.main import app, create_app


def test_health() -> None:
    async def _request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get("/health")

    response = asyncio.run(_request())
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_validation_errors_do_not_echo_input() -> None:
    class Probe(BaseModel):
        count: int

    probe_app = create_app()

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
        create_app()
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
