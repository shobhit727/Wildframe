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
    quote = sync_conn.dialect.identifier_preparer.quote
    live_tables = set(inspector.get_table_names())
    added = []
    # A table declared in more than one metadata set is a model bug, not drift.
    # Reconciling it grafts two different shapes into one physical table and
    # reports success, so refuse. This is how billing-service's two conflicting
    # `payout_ledger` definitions were being silently merged.
    # Dedupe by identity. collect_bases() can hand us the same Base more than
    # once, and counting occurrences flagged every table in admin-service as a
    # collision - a false positive that blocked 5 services. Only genuinely
    # distinct metadata sets are a collision.
    declared_in = {}
    for base in bases:
        for table in base.metadata.sorted_tables:
            declared_in.setdefault(table.name, []).append(id(base))
    collisions = {n: g for n, g in declared_in.items() if len(set(g)) > 1}
    if collisions:
        names = ", ".join(sorted(collisions))
        raise SystemExit(
            f"refusing to reconcile: {names} declared in more than one metadata "
            "set. Two models sharing a table name is a model bug; reconcile would "
            "merge their columns into one physical table. Fix the models first."
        )

    for base in bases:
        for table in base.metadata.sorted_tables:
            if table.name not in live_tables:
                continue  # create_all owns missing tables
            present = {c["name"] for c in inspector.get_columns(table.name)}
            row_count = None
            for column in table.columns:
                if column.name in present:
                    continue
                if row_count is None:
                    row_count = sync_conn.execute(
                        text(f"SELECT count(*) FROM {quote(table.name)}")
                    ).scalar() or 0
                # Preflight rather than discover by crashing. A NOT NULL column
                # with no *server* default cannot be added to a populated table,
                # and letting it throw rolled back create_all for the whole
                # service and printed a SQLAlchemy documentation URL instead of
                # the column and the DDL.
                if not column.nullable and column.server_default is None and row_count > 0:
                    raise SystemExit(
                        f"cannot add {table.name}.{column.name}: NOT NULL with no "
                        f"server default, and the table holds {row_count} rows. "
                        "Backfill it manually:\n"
                        f"  ALTER TABLE {quote(table.name)} ADD COLUMN "
                        f"{CreateColumn(column).compile(dialect=sync_conn.dialect)} "
                        "NOT NULL DEFAULT <value>;\n"
                        f"  ALTER TABLE {quote(table.name)} ALTER COLUMN "
                        f"{quote(column.name)} DROP DEFAULT;"
                    )
                # CreateColumn emits the column definition only: no index, no
                # foreign key, no unique constraint. A column declared
                # unique=True or index=True would land unenforced, which for an
                # idempotency key is a financial invariant lost behind a green
                # run. Refuse rather than add it bare.
                kinds = []
                if column.unique:
                    kinds.append("unique")
                if column.index:
                    kinds.append("index")
                if column.foreign_keys:
                    kinds.append("foreign key")
                if kinds:
                    raise SystemExit(
                        f"cannot safely add {table.name}.{column.name}: it carries "
                        f"{', '.join(kinds)}, and this pass only emits the column "
                        "definition. Adding it bare would leave the constraint "
                        "unenforced. Add it by hand, or extend the pass."
                    )
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
            # Surface the exception line and the failing statement, not the last
            # line. For a SQLAlchemy IntegrityError the last line is always
            # "(Background on this error at: ...)", so an operator following the
            # troubleshooting table was handed a documentation URL instead of the
            # column that actually failed.
            _err = (proc.stderr or "").strip().splitlines()
            _keep = [
                ln for ln in _err
                if "sqlalchemy.exc." in ln or ln.strip().startswith("[SQL:") or "Error" in ln
            ]
            tail = (_keep or _err)[-3:] or ["unknown error"]
            print(f"  !   {svc:24s} -> {db}: {tail[0][:140]}")
            failures.append(svc)
    if failures:
        print(f"\nFAILED for: {', '.join(failures)}")
        sys.exit(1)
    print("\nAll schemas created.")


if __name__ == "__main__":
    main()
