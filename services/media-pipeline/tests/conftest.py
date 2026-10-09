"""Shared real-PostgreSQL fixtures for the media-pipeline test suite.

The pipeline models use ``ARRAY``/``JSONB`` and the job repository takes row
locks, so SQLite cannot host them and the real database is required.

Scope split is deliberate:

* ``postgres_url`` is session-scoped and yields a *URL string only* — no
  connection and therefore no event-loop coupling, so one container serves the
  whole run.
* ``db_session`` is function-scoped: the engine is created and disposed inside
  a single test's event loop. That keeps it safe to mix with the rest of the
  suite, whose tests each run on their own function-scoped loop.

Per-test isolation comes from ``TRUNCATE ... RESTART IDENTITY CASCADE`` rather
than repeated create/drop DDL.
"""

import os
from collections.abc import Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """One disposable PostgreSQL for the whole session.

    ``TEST_DATABASE_URL`` wins when set (the documented local test instance);
    otherwise a container is started lazily so collection stays safe when the
    optional dependency is absent.
    """
    from contextlib import ExitStack

    with ExitStack() as stack:
        url = os.environ.get("TEST_DATABASE_URL")
        if not url:
            from testcontainers.postgres import (  # lazy: keeps collection safe
                PostgresContainer,
            )

            url = stack.enter_context(PostgresContainer("postgres:15")).get_connection_url()
        yield url


def truncate_sql() -> str:
    """Empty every table in one statement (cheaper than per-test create/drop)."""
    from app.models import Base

    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    return f"TRUNCATE {tables} RESTART IDENTITY CASCADE"


@pytest_asyncio.fixture
async def db_session(postgres_url: str) -> AsyncSession:
    """An AsyncSession over an emptied schema for the duration of one test."""
    from app.models import Base

    engine = create_async_engine(
        make_url(postgres_url).set(drivername="postgresql+asyncpg"),
        echo=False,
        future=True,
        pool_pre_ping=True,
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await session.execute(text(truncate_sql()))
            await session.commit()
            yield session
    finally:
        await engine.dispose()
