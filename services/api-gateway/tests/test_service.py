"""Tests for Api Gateway Service."""

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_health_check():
    """Test health check endpoint."""
    import app.main as main
    from app.main import app
    from fastapi.testclient import TestClient

    app.dependency_overrides.clear()
    with TestClient(app, base_url="http://localhost") as client:
        # Stub rate limiter and redis
        main.rate_limiter = MagicMock()
        main.rate_limiter.check_rate_limit = AsyncMock(return_value=True)
        redis_stub = MagicMock()
        redis_stub.ping = AsyncMock(return_value=True)
        app.state.redis_client = redis_stub
        main._shared_client = MagicMock()

        response = client.get("/health")
        assert response.status_code == 200
        # #628: /health returns status-only
        assert response.json() == {"status": "ok"}
