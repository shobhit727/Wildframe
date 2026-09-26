import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture(autouse=True)
def _mock_auth_introspection():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {}
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_resp)
    with patch("httpx.AsyncClient", return_value=mock_client):
        with patch("app.api.routes.httpx.AsyncClient", return_value=mock_client):
            yield

# Migrated fixtures from app/tests
"""Pytest configuration and fixtures with testcontainers for integration tests."""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

# Start PostgreSQL container for integration tests
postgres = PostgresContainer("postgres:15-alpine")
postgres.start()

# Override DATABASE_URL for tests
DATABASE_URL = postgres.get_connection_url().replace("psycopg2", "asyncpg")

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session")
def event_loop():
    """Event loop fixture."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db():
    """Database session fixture with transaction rollback."""
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def db_with_commit():
    """Database session fixture that commits (for setup)."""
    async with async_session() as session:
        yield session
        await session.commit()


# Cleanup
def pytest_sessionfinish(session, exitstatus):
    postgres.stop()
