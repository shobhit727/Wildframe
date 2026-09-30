"""Regression for #980: the schema bootstrap must repair a stale table.

``Base.metadata.create_all`` is checkfirst, so it creates a missing table but
never adds a column to a table that already exists. A database created before
a model gained a column therefore keeps the old shape forever, and the service
starts raising ``UndefinedColumnError`` on the columns it selects --
content-service did exactly this with ``content.price_usd``, which made every
content listing and the trending feed return 500 while ``/health`` and
``/genres`` stayed green.

These tests drive the real ``scripts/init_schemas.py`` runner -- the same
subprocess the documented ``python scripts/init_schemas.py`` invokes -- against
a real PostgreSQL whose ``content`` table is deliberately rolled back to the
pre-``price_usd`` shape. They assert the runner restores the column, that the
exact query the failing endpoint issues then succeeds, and that re-running the
bootstrap changes nothing.

The ``database_url`` fixture is module-local (the same pattern
``test_content_extra.py`` already uses) so these tests do not depend on
conftest's session-scoped ``postgres_url``.
"""

import importlib.util
import json
import os
import socket
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from sqlalchemy import insert, inspect as sa_inspect
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.models import Base, Content

# scripts/init_schemas.py lives at the repo root; service dirs sit two levels
# below it (services/<service>/tests -> parents[3]).
REPO = Path(__file__).resolve().parents[3]
BOOTSTRAP = REPO / "scripts" / "init_schemas.py"
SERVICE_DIR = Path(__file__).resolve().parents[1]

LOCAL_TEST_DATABASE_URL = "postgresql+asyncpg://postgres:test@127.0.0.1:55432/test_db"

pytestmark = pytest.mark.db


def _local_instance_reachable(url: str) -> bool:
    """Cheap TCP probe so the suite also works without TEST_DATABASE_URL set."""
    parsed = urlparse(url)
    if not parsed.hostname:
        return False
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((parsed.hostname, parsed.port or 5432)) == 0


@pytest.fixture(scope="module")
def database_url():
    """TEST_DATABASE_URL, the documented local test instance, else a container."""
    with ExitStack() as stack:
        url = os.environ.get("TEST_DATABASE_URL")
        if url:
            yield url
            return
        if _local_instance_reachable(LOCAL_TEST_DATABASE_URL):
            yield LOCAL_TEST_DATABASE_URL
            return
        from testcontainers.postgres import PostgresContainer  # lazy import

        yield stack.enter_context(PostgresContainer("postgres:15")).get_connection_url()


@pytest_asyncio.fixture
async def rolled_back_engine(database_url):
    """A real ``content`` table missing ``price_usd``, as a stale database has.

    ``price_usd`` was added to the model in 5a74e1eb; dropping it here
    reproduces precisely the live database that shipped with #980.
    """
    engine = create_async_engine(make_url(database_url).set(drivername="postgresql+asyncpg"))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE content DROP COLUMN IF EXISTS price_usd"))
        yield engine
    finally:
        # Leave the shared test database in the shape the models declare.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()


async def _content_columns(engine) -> set:
    async with engine.connect() as conn:
        return {
            c["name"] for c in (await conn.run_sync(lambda c: sa_inspect(c).get_columns("content")))
        }


def _run_bootstrap(database_url: str, allow_add: str = "") -> subprocess.CompletedProcess:
    """Run the documented bootstrap for content-service against ``database_url``."""
    spec = importlib.util.spec_from_file_location("init_schemas", BOOTSTRAP)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    env = dict(os.environ)
    env["PYTHONPATH"] = "."
    async_url = make_url(database_url).set(drivername="postgresql+asyncpg")
    # The script refuses to add a column to a populated table unless the operator
    # authorises it, because a dropped column leaves no trace in information_schema
    # and the pass cannot tell a stale table from a deliberate removal. The runner
    # takes that authorisation as JSON in argv[2] - this is the same call the
    # parent makes with `json.dumps(sorted(allow_add))`, not a --allow-add flag,
    # because `--allow-add` is the CLI spelling and the runner speaks the internal
    # protocol. These tests drive the repair path, so they must authorise the
    # column the way the script does.
    argv = [
        sys.executable,
        "-c",
        runner.RUNNER,
        async_url.render_as_string(hide_password=False),
        json.dumps([allow_add] if allow_add else []),
    ]
    return subprocess.run(
        argv,
        cwd=str(SERVICE_DIR),
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
async def test_stale_column_is_repaired_by_the_schema_bootstrap(rolled_back_engine, database_url):
    """A model column the live table lacks must be added, not left missing."""
    # Sanity: the fixture really did roll the table back, so a pass below
    # cannot be an artefact of the column never having been dropped.
    assert "price_usd" not in await _content_columns(rolled_back_engine)

    # The bug as reported: the model's select list names a column the schema
    # does not have, so the content query cannot run at all.
    with pytest.raises(Exception) as excinfo:
        async with rolled_back_engine.connect() as conn:
            await conn.execute(select(Content).limit(1))
    assert "price_usd" in str(excinfo.value).lower(), excinfo.value

    proc = _run_bootstrap(database_url, allow_add="content.price_usd")
    assert proc.returncode == 0, proc.stderr
    assert "content.price_usd" in proc.stdout, proc.stdout

    assert "price_usd" in await _content_columns(rolled_back_engine)

    # The exact query the failing endpoint issues now runs against the
    # repaired table.
    async with rolled_back_engine.connect() as conn:
        await conn.execute(select(Content).limit(1))


@pytest.mark.asyncio
async def test_schema_bootstrap_is_idempotent(rolled_back_engine, database_url):
    """Re-running the bootstrap must be safe and change nothing the second time."""
    first = _run_bootstrap(database_url, allow_add="content.price_usd")
    assert first.returncode == 0, first.stderr
    assert "content.price_usd" in first.stdout, first.stdout

    before = await _content_columns(rolled_back_engine)
    second = _run_bootstrap(database_url, allow_add="content.price_usd")
    assert second.returncode == 0, second.stderr
    assert "content.price_usd" not in second.stdout, second.stdout
    assert await _content_columns(rolled_back_engine) == before
