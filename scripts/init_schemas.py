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

# The per-service half lives in schema_bootstrap.py so that compose-smoke.sh can
# run the identical code inside a service container (see that file's docstring for
# why it cannot run on the CI runner). Read as text and passed to `python -c`, so
# it stays a standalone program with no dependency on its own location.
with open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema_bootstrap.py"),
    encoding="utf-8",
) as _fh:
    RUNNER = _fh.read()


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
