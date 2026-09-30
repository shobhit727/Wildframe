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

Adding a column to a table that *already holds rows* invents a value for
every one of those rows. A column that was deliberately removed leaves no
trace in the database, so nothing in the live schema distinguishes "the table
is stale" from "a person dropped this column on purpose" -- the two look
identical. The pass therefore refuses to add to a populated table unless the
operator names the column with ``--allow-add``. Adding to an *empty* table
fabricates nothing, so it needs no flag and stays automatic.

Usage:
  python scripts/init_schemas.py
  python scripts/init_schemas.py --allow-add content.price_usd
"""

import argparse
import json
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
import asyncio, importlib, json, pkgutil, sys
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


def reconcile_columns(sync_conn, bases, allow_add=()):
    """Add model columns missing from tables that already exist.

    create_all is checkfirst, so a table created before a model gained a
    column keeps the old shape forever. Returns the list of "table.column"
    names added. Additive only: nothing is dropped, retyped or rewritten.

    ``allow_add`` is the set of "table.column" the operator has authorised for
    ADD COLUMN against a table that already holds rows. See the populated-table
    gate below for why that authorisation is per-invocation and not stored.
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
                # Everything above refuses, so nothing above has guessed. This
                # is the guess, and it is the last one.
                #
                # ADD COLUMN on a table that already has rows manufactures a
                # value for every one of those rows: NULL with no default, or
                # the server default when there is one. On an empty table that
                # fabricates nothing, so the add is right by definition. On a
                # populated table the pass cannot tell "this table predates the
                # model" from "somebody deliberately removed this column" --
                # a dropped column leaves no trace in information_schema, so
                # there is nothing here to inspect. It has to choose, and on
                # the case where choosing wrong throws away a human's decision
                # it chooses wrong.
                #
                # So it asks instead. The acknowledgement is a command-line
                # flag, deliberately not a committed allowlist: the decision
                # belongs to one database, not to the repository. A committed
                # `("content", "price_usd") -> dropped` would stop a freshly
                # created *production* database from ever receiving a column
                # the model and the app both require, because this same script
                # is the documented way to create it. Naming the column once
                # is enough, because this pass repairs drift and repaired
                # drift is no longer drift.
                if row_count > 0 and f"{table.name}.{column.name}" not in allow_add:
                    fills = "every one of them will be given NULL"
                    default = column.server_default
                    if default is not None:
                        # str(), not repr(): repr of a TextClause is an object
                        # address, which tells the operator nothing.
                        arg = getattr(default, "arg", default)
                        fills = (
                            "every one of them will be given the server "
                            f"default {arg}"
                        )
                    raise SystemExit(
                        f"refusing to add {table.name}.{column.name}: the table "
                        f"holds {row_count} rows, and adding the column means "
                        f"{fills}. A dropped column leaves no trace in the "
                        "database, so this cannot tell a stale table from a "
                        "column somebody removed on purpose, and guessing wrong "
                        "either resurrects that drop or overwrites real values "
                        "with a default.\n"
                        "If the model is right and the table is stale, re-run "
                        f"naming it:\n"
                        f"  python scripts/init_schemas.py "
                        f"--allow-add {table.name}.{column.name}\n"
                        "If the column was dropped on purpose, remove it from "
                        "the model instead -- this pass is additive and will "
                        "not re-add it. To apply it by hand now:\n"
                        f"  ALTER TABLE {quote(table.name)} ADD COLUMN {ddl};"
                    )
                sync_conn.execute(
                    text(f"ALTER TABLE {quote(table.name)} ADD COLUMN {ddl}")
                )
                added.append(f"{table.name}.{column.name}")
    return added


async def main(url: str, allow_add=()) -> int:
    eng = create_async_engine(url)
    try:
        async with eng.begin() as conn:
            bases = collect_bases()
            for base in bases:
                await conn.run_sync(base.metadata.create_all)
            added = await conn.run_sync(reconcile_columns, bases, allow_add)
        for name in added:
            print(f"  + added column {name}")
        return 0
    finally:
        await eng.dispose()

_ALLOW = json.loads(sys.argv[2]) if len(sys.argv) > 2 else []
sys.exit(asyncio.run(main(sys.argv[1], _ALLOW)))
'''


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="init_schemas.py",
        description=(
            "Create every service's tables from its SQLAlchemy models, then add "
            "the model columns existing tables lack. Additive only: nothing is "
            "dropped, retyped or rewritten."
        ),
        epilog=(
            "A column added to a table that already holds rows invents a value "
            "for every existing row, and a deliberately dropped column leaves no "
            "trace in the database that would distinguish it from a stale table. "
            "So a populated table is only reconciled when you name the column "
            "with --allow-add. An empty table is reconciled automatically, because "
            "no existing row can be invented for."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--allow-add",
        action="append",
        default=[],
        metavar="TABLE.COLUMN",
        help=(
            "authorise ADD COLUMN on a populated table for this column. "
            "Repeatable, or comma-separated. Only use it once you have decided "
            "the table is stale rather than the column being deliberately absent."
        ),
    )
    args = ap.parse_args()
    allow_add: set[str] = set()
    for item in args.allow_add:
        allow_add.update(p.strip() for p in item.split(",") if p.strip())

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
            [
                sys.executable,
                "-c",
                RUNNER,
                f"{PG}/{db}",
                json.dumps(sorted(allow_add)),
            ],
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
