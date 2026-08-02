"""Pytest configuration and fixtures."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "postgresql+asyncpg://postgres:password@db:5432/uploads_db"

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def db():
    """Database session fixture."""
    async with async_session() as session:
        yield session


@pytest.fixture(scope="session")
def event_loop():
    """Event loop fixture."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
