"""Shared test fixtures and configuration."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.models import Base, User
from app.repositories import (
    LoginAuditRepository,
    RefreshTokenRepository,
    UserRepository,
)
from app.security import PasswordManager, TokenManager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def test_engine(tmp_path):
    """Create a fresh per-test database engine using a temp-file SQLite DB."""
    db_path = tmp_path / "test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        echo=False,
        connect_args={"timeout": 15},
    )

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest.fixture
async def test_session_factory(test_engine):
    """Create test session factory."""
    return async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


@pytest.fixture
async def test_session(test_session_factory):
    """Create test database session."""
    async with test_session_factory() as session:
        yield session
        # Rollback after test
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
        id=uuid4(),
        email="test@example.com",
        password_hash=password_manager.hash_password("SecurePass123!"),
        first_name="Test",
        last_name="User",
        email_verified=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    test_session.add(user)
    await test_session.commit()
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
