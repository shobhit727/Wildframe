"""Tests for Billing Service."""

from httpx import ASGITransport, AsyncClient


async def test_health_check():
    """Test health check endpoint."""
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] in ("healthy", "degraded")
