"""Shared test fixtures and configuration."""

import sys
from pathlib import Path
import asyncio

import pytest
from unittest.mock import AsyncMock, patch

service_root = Path(__file__).parents[1]
sdk_root = service_root.parent.parent / "packages" / "sdk"
for p in (str(service_root), str(sdk_root), str(sdk_root / "wildframe_compliance")):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.models import Base, User
from app.repositories import (
    LoginAuditRepository,
    RefreshTokenRepository,
    UserRepository,
)
from app.security import PasswordManager, TokenManager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture(autouse=True)
def _no_redis_rate_limit():
    """Run auth tests deterministically without the Redis throttle (#54).

    The one endpoint test that asserts throttling re-patches allow() itself.
    """
    # Skip patching if the module doesn't exist (e.g., during model tests)
    try:
        with patch("app.api.routes.auth.allow", new=AsyncMock(return_value=True)):
            yield
    except (ImportError, AttributeError):
        yield


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def test_engine(tmp_path):
    """Create a fresh per-test database engine using a temp-file SQLite DB."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def test_session_factory(test_engine):
    """Create test session factory."""
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def test_session(test_session_factory):
    """Create test database session."""
    async with test_session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def db_session(test_session):
    """Alias for test_session for backward compatibility."""
    yield test_session


@pytest.fixture
def password_manager():
    """Create password manager instance."""
    return PasswordManager()


@pytest.fixture
def token_manager():
    """Create token manager instance."""
    return TokenManager()


@pytest.fixture
async def test_user(test_session, password_manager):
    """Create test user."""
    user = User(
        email="test@example.com",
        password_hash=password_manager.hash_password("testpass123"),
    )
    test_session.add(user)
    await test_session.flush()
    await test_session.refresh(user)
    return user


@pytest.fixture
async def user_repository(test_session):
    """Create user repository."""
    return UserRepository(test_session)


@pytest.fixture
async def token_repository(test_session):
    """Create token repository."""
    return RefreshTokenRepository(test_session)


@pytest.fixture
async def audit_repository(test_session):
    """Create audit repository."""
    return LoginAuditRepository(test_session)
