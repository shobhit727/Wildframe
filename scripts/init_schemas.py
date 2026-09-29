"""Create database tables for every service (fresh-stack bootstrap).

The repo intentionally has no migration framework at runtime (AGENTS.md);
schemas are the SQLAlchemy models' responsibility. This script imports each
service's models in an isolated subprocess (every service owns a top-level
`app` package, so imports cannot share a process) and runs
``Base.metadata.create_all`` against its database on the shared Postgres.

``create_all`` is checkfirst: it creates *missing tables* and never touches a
table that already exists. So when a model gains a column, every database
created before that commit keeps the old shape and the service starts raising
``UndefinedColumnError`` on the columns it selects -- content-service did
exactly this with ``content.price_usd`` (#980), which made the whole browse
page 500 while ``/health`` and ``/genres`` stayed green. The second pass,
:func:`reconcile_columns`, closes that gap: it ``ADD COLUMN``s whatever the
model declares and the live table lacks. It only ever adds columns, so it is
safe to re-run and never rewrites or drops existing data.

Usage:  python scripts/init_schemas.py
"""

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = "localhost"
PG = "postgresql+asyncpg://wildframe:wildframe_dev_password@localhost:5432"

# service dir -> database name
SERVICES = {
    "auth-service": "auth_db",
    "user-service": "users_db",
    "admin-service": "admin_db",
    "content-service": "content_db",
    "streaming-service": "streaming_db",
    "search-service": "search_db",
    "recommendation-service": "recommendation_db",
    "billing-service": "billing_db",
    "analytics-service": "analytics_db",
    "notification-service": "notification_db",
    "media-pipeline": "media_db",
    "creators-service": "creators_db",
    "moderation-service": "moderation_db",
    "uploads-service": "uploads_db",
}

RUNNER = r'''
import asyncio, importlib, pkgutil, sys
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateColumn

try:
    from app.models import Base
except ImportError:
    # Some services keep Base in a submodule (e.g. app.models.admin).
    from app.models.admin import Base  # type: ignore[assignment]

import app.models as models_pkg


def collect_bases():
    """Every declarative Base the service's models package defines.

    Most services keep one Base, but the compliance modules of the bigger
    services (content, admin, billing, analytics, moderation, streaming) each
    own a separate Base, so their tables live in separate metadata and
    create_all on the primary Base alone silently skips them (#980).
    """
    bases, seen = [], set()

    def _add(base):
        if base is not None and id(base.metadata) not in seen:
            seen.add(id(base.metadata))
            bases.append(base)

    _add(Base)
    for info in pkgutil.iter_modules(getattr(models_pkg, "__path__", []) or []):
        module = importlib.import_module(f"{models_pkg.__name__}.{info.name}")
        _add(getattr(module, "Base", None))
    return bases


def reconcile_columns(sync_conn, bases):
    """Add model columns missing from tables that already exist.

    create_all is checkfirst, so a table created before a model gained a
    column keeps the old shape forever. Returns the list of "table.column"
    names added. Additive only: nothing is dropped, retyped or rewritten.
    """
    inspector = sa_inspect(sync_conn)
    live_tables = set(inspector.get_table_names())
    quote = sync_conn.dialect.identifier_preparer.quote
    added = []
    for base in bases:
        for table in base.metadata.sorted_tables:
            if table.name not in live_tables:
                continue  # create_all owns missing tables
            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                ddl = CreateColumn(column).compile(dialect=sync_conn.dialect)
                sync_conn.execute(
                    text(f"ALTER TABLE {quote(table.name)} ADD COLUMN {ddl}")
                )
                added.append(f"{table.name}.{column.name}")
    return added


async def main(url: str) -> int:
    eng = create_async_engine(url)
    try:
        async with eng.begin() as conn:
            bases = collect_bases()
            for base in bases:
                await conn.run_sync(base.metadata.create_all)
            added = await conn.run_sync(reconcile_columns, bases)
        for name in added:
            print(f"  + added column {name}")
        return 0
    finally:
        await eng.dispose()

sys.exit(asyncio.run(main(sys.argv[1])))
'''


def main() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = "."  # per-service cwd; keeps `app` unambiguous
    failures = []
    for svc, db in SERVICES.items():
        svc_dir = os.path.join(REPO, "services", svc)
        if not os.path.isdir(svc_dir):
            print(f"  ! {svc}: directory missing")
            failures.append(svc)
            continue
        proc = subprocess.run(
            [sys.executable, "-c", RUNNER, f"{PG}/{db}"],
            cwd=svc_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            print(f"  ok  {svc:24s} -> {db}")
            # Echo what the reconcile pass had to repair, so a stale schema is
            # visible in the bootstrap output instead of silently fixed.
            for line in (proc.stdout or "").splitlines():
                if line.strip():
                    print(f"       {line.strip()}")
        else:
            tail = (proc.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
            print(f"  !   {svc:24s} -> {db}: {tail[0][:140]}")
            failures.append(svc)
    if failures:
        print(f"\nFAILED for: {', '.join(failures)}")
        sys.exit(1)
    print("\nAll schemas created.")


if __name__ == "__main__":
    main()
