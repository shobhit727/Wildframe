"""Regression tests for the User Service database + schema import layer.

These cover two confirmed bugs:

1. ``DatabaseManager.health_check`` executed ``conn.execute(lambda: "SELECT
   1")``, which is not a valid SQLAlchemy statement and always raised, so the
   check always returned ``False`` and the app refused to start.
2. ``app/main.py`` imports ``HealthCheckResponse`` from ``app.schemas``, which
   never defined it, raising ``ImportError`` at import time.
"""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import DatabaseManager


@pytest.fixture
async def user_tmp_engine():
    """Provide a fresh async SQLite engine for the test."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
    )
    yield engine
    await engine.dispose()


@pytest.mark.unit
async def test_health_check_returns_true_against_working_engine():
    """``health_check`` must return ``True`` when the database is healthy."""
    DatabaseManager._engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
    )
    DatabaseManager._session_factory = None

    try:
        assert await DatabaseManager.health_check() is True
    finally:
        await DatabaseManager.close()


@pytest.mark.unit
def test_health_check_uses_text_statement_not_lambda():
    """Guard against the lambda regression in the source text."""
    import inspect

    source = inspect.getsource(DatabaseManager.health_check)
    assert "conn.execute(text(" in source, (
        "health_check must execute a sqlalchemy.text() statement; the lambda "
        "form is invalid and always raises."
    )
    assert "lambda:" not in source


@pytest.mark.unit
def test_health_check_response_importable_from_schemas():
    """``app.schemas`` must export ``HealthCheckResponse`` (used by main.py)."""
    from app import schemas

    assert hasattr(schemas, "HealthCheckResponse"), (
        "app.schemas must export HealthCheckResponse; app/main.py imports it "
        "at module load and would otherwise raise ImportError."
    )

    response = schemas.HealthCheckResponse(
        status="healthy",
        service="user-service",
        version="1.0.0",
        timestamp="2026-01-01T00:00:00Z",
    )
    assert response.status == "healthy"
    assert response.service == "user-service"


@pytest.mark.unit
def test_error_response_importable_from_schemas():
    """``app.schemas`` must continue to export ``ErrorResponse``."""
    from app import schemas

    assert hasattr(schemas, "ErrorResponse")
    error = schemas.ErrorResponse(
        error="VALIDATION_ERROR",
        message="Request validation failed",
        details={"errors": []},
    )
    assert error.error == "VALIDATION_ERROR"
    assert error.details == {"errors": []}
