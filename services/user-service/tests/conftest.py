import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db_session(tmp_path) -> AsyncSession:
    """An AsyncSession over a freshly built schema, isolated per test.

    The user-service models use no Postgres-only column types, so a per-test
    SQLite file gives each test a clean catalog without a container. Modules
    that need their own backing store (or a real PostgreSQL) still override
    this with a module-level fixture of the same name.
    """
    from app.models import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/db_session.db", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
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
