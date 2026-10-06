import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

# Migrated fixtures from app/tests
"""Pytest configuration and fixtures with testcontainers for integration tests."""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from app.models import Base
from sqlalchemy.orm import sessionmaker
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

# Start PostgreSQL container for integration tests
postgres = PostgresContainer("postgres:15-alpine")
postgres.start()

# Override DATABASE_URL for tests
DATABASE_URL = postgres.get_connection_url().replace("psycopg2", "asyncpg")

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-overload]


@pytest.fixture(scope="session")
def event_loop():
    """Event loop fixture."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db():
    """Create a clean database schema and session for each integration test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with async_session() as session:
            yield session
            await session.rollback()
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_with_commit():
    """Database session fixture that commits (for setup)."""
    async with async_session() as session:
        yield session
        await session.commit()


# Cleanup
def pytest_sessionfinish(session, exitstatus):
    postgres.stop()


@pytest_asyncio.fixture
async def db_session(db):
    """Compatibility alias for integration tests using the db_session name."""
    yield db
