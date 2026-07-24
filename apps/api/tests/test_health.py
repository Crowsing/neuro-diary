import asyncio

import httpx

from app.main import app


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
