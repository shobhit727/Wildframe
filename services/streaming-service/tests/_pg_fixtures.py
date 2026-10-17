"""Real-PostgreSQL fixtures shared by the streaming gap test modules.

These live outside ``conftest.py`` so the gap tests remain self-contained. The
streaming models use ``ARRAY``/``JSONB`` and ``PlaybackSessionRepository``
issues ``pg_advisory_xact_lock``, so SQLite cannot host them and the real
database is required.

The engine is pooled and the event loop is session-scoped so that a single
container and a single connection pool serve every test: with a per-test loop
a pooled asyncpg connection would outlive the loop that created it.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def truncate_sql() -> str:
    """Empty every table in one statement (cheaper than per-test create/drop DDL)."""
    from app.models import Base

    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    return f"TRUNCATE {tables} RESTART IDENTITY CASCADE"


@pytest.fixture(scope="session")
def event_loop():
    """One event loop for the whole session so the pooled engine stays valid."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """One disposable PostgreSQL for the whole session (~25s to start)."""
    from contextlib import ExitStack

    with ExitStack() as stack:
        from testcontainers.postgres import (
            PostgresContainer,
        )  # lazy: keeps collection safe when the dep is absent

        url = os.environ.get("TEST_DATABASE_URL")
        if not url:
            url = stack.enter_context(PostgresContainer("postgres:15")).get_connection_url()
        yield url


@pytest_asyncio.fixture(scope="session")
async def engine(postgres_url: str, schema: str):
    """A pooled engine whose schema is already built and emptied."""
    engine = create_async_engine(
        make_url(postgres_url).set(drivername="postgresql+asyncpg"),
        echo=False,
        pool_size=5,
        max_overflow=5,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def schema(postgres_url: str) -> Iterator[str]:
    """Build the schema once; per-test isolation comes from TRUNCATE instead."""
    from app.models import Base

    async def _create() -> None:
        tmp = create_async_engine(make_url(postgres_url).set(drivername="postgresql+asyncpg"))
        try:
            async with tmp.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await tmp.dispose()

    async def _drop() -> None:
        tmp = create_async_engine(make_url(postgres_url).set(drivername="postgresql+asyncpg"))
        try:
            async with tmp.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        finally:
            await tmp.dispose()

    asyncio.run(_create())
    yield "ready"
    asyncio.run(_drop())


@pytest_asyncio.fixture
async def db_session(engine, schema: str) -> AsyncIterator[AsyncSession]:
    """An AsyncSession over an emptied schema for the duration of one test."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await session.execute(text(truncate_sql()))
        await session.commit()
        try:
            yield session
        finally:
            await session.rollback()


@pytest_asyncio.fixture
async def broken_transaction(db_session: AsyncSession) -> AsyncSession:
    """A session whose transaction Postgres has already aborted.

    Statements issued afterwards fail for the same reason a real write failure
    would, so the service layer's ``except Exception -> rollback -> raise``
    contract can be exercised without faking a repository.
    """
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        await db_session.execute(text("SELECT * FROM table_that_does_not_exist"))
    return db_session
