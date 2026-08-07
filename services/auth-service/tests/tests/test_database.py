"""Regression tests for the Auth Service database layer.

Covers the fix for ``DatabaseManager.health_check``: the previous
implementation passed a ``lambda`` to ``conn.execute`` (``conn.execute(lambda:
"SELECT 1")``) which is not a valid SQLAlchemy statement and would always
raise, making the health check fail and preventing the app from starting.
"""

import pytest
from app.core.database import DatabaseManager
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
async def tmp_engine(tmp_path):
    """Create a throwaway async SQLite engine for the duration of a test."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
    )
    yield engine
    await engine.dispose()


@pytest.mark.unit
async def test_health_check_returns_true_against_working_engine():
    """``health_check`` must succeed when the database is reachable.

    The old buggy body was ``await conn.execute(lambda: "SELECT 1")`` which
    raises at execution time, so the check returned ``False`` even against a
    perfectly healthy database. After the fix it issues a real ``text()``
    statement over a transaction and returns ``True``.
    """
    # Point the singleton engine at a fresh in-memory database.
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
    """Guard against the lambda regression at the source-text level.

    Read the source of ``health_check`` and assert it executes a compiled SQL
    text statement rather than calling a Python lambda. This catches a
    re-introduction of the bug without needing a live database connection.
    """
    import inspect

    source = inspect.getsource(DatabaseManager.health_check)
    assert "conn.execute(text(" in source, (
        "health_check must execute a sqlalchemy.text() statement; the lambda "
        "form ``conn.execute(lambda: ...)`` is invalid and always raises."
    )
    assert "lambda:" not in source, "health_check must not pass a lambda to conn.execute."
