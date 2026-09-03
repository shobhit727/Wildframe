"""Auth httpx integration - privacy routes DB."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app


@pytest.mark.asyncio
async def test_health_privacy_openapi():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/openapi.json")
        assert resp.status_code == 200
        assert "privacy" in resp.text


@pytest.mark.asyncio
async def test_privacy_openapi_has_notices():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert any("privacy" in p for p in data["paths"])
