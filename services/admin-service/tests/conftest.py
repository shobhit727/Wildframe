"""Shared fixtures for the admin-service test suite.

``db_session`` gives each test its own freshly built schema. The admin models
use no Postgres-only column types, so a per-test SQLite file is enough and
avoids a container; modules that need a real PostgreSQL define their own
module-level ``db_session``, which shadows this one.
"""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db_session(tmp_path) -> AsyncSession:
    """An AsyncSession over a freshly built, empty schema for one test."""
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
