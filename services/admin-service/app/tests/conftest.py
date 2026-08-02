"""Pytest configuration and fixtures with testcontainers for integration tests."""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from testcontainers.postgres import PostgresContainer

# Start PostgreSQL container for integration tests
postgres = PostgresContainer("postgres:15-alpine")
postgres.start()

# Override DATABASE_URL for tests
DATABASE_URL = postgres.get_connection_url().replace("psycopg2", "asyncpg")

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


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
