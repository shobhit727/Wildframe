import os
from contextlib import ExitStack

import pytest
import pytest_asyncio
import wildframe_auth
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from wildframe_auth.verifier import clear_jwks_cache

from tests._test_jwks import JWKS


@pytest.fixture(autouse=True)
def _published_jwks(monkeypatch):
    """Serve the in-memory JWKS instead of reaching for auth-service over HTTP.

    ``app/api/routes/__init__.py`` verifies access tokens against
    auth-service's published JWKS. Patching only the outbound fetch leaves the
    real SDK verifier in the path, so signature verification is still exercised
    for real -- but no test here waits out the 5s fetch timeout against a
    host that does not exist in CI. The keypair is generated at import time in
    ``tests/_test_jwks.py``; no private key is committed.
    """

    async def fetch(url):
        return JWKS

    monkeypatch.setattr(wildframe_auth.verifier, "fetch_jwks", fetch)
    clear_jwks_cache()
    yield
    clear_jwks_cache()


@pytest.fixture(scope="session")
def postgres_url():
    """TEST_DATABASE_URL, the documented local test instance, else a container.

    The content models use ``JSONB``, so SQLite cannot host them and a real
    PostgreSQL is required.
    """
    with ExitStack() as stack:
        url = os.environ.get("TEST_DATABASE_URL")
        if url:
            yield url
            return
        from testcontainers.postgres import PostgresContainer  # lazy: safe to collect

        url = stack.enter_context(PostgresContainer("postgres:15")).get_connection_url()
        yield url


def _truncate_sql() -> str:
    """Empty every table in one statement (cheaper than per-test create/drop)."""
    from app.models import Base

    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    return f"TRUNCATE {tables} RESTART IDENTITY CASCADE"


@pytest_asyncio.fixture
async def db_session(postgres_url: str):
    """A real PostgreSQL session with the full content schema, emptied per test.

    The schema is created once and isolation comes from TRUNCATE, so the
    unique constraints the catalog enforces (for example ``ix_genre_slug``)
    cannot leak between tests.
    """
    from app.models import Base

    engine = create_async_engine(
        make_url(postgres_url).set(drivername="postgresql+asyncpg"), echo=False
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await session.execute(text(_truncate_sql()))
            await session.commit()
            yield session
    finally:
        await engine.dispose()


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
