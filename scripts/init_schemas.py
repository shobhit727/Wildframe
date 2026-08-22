"""Create database tables for every service (fresh-stack bootstrap).

The repo intentionally has no migration framework at runtime (AGENTS.md);
schemas are the SQLAlchemy models' responsibility. This script imports each
service's models in an isolated subprocess (every service owns a top-level
`app` package, so imports cannot share a process) and runs
``Base.metadata.create_all`` against its database on the shared Postgres.

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
import asyncio, sys
from sqlalchemy.ext.asyncio import create_async_engine

try:
    from app.models import Base
except ImportError:
    # Some services keep Base in a submodule (e.g. app.models.admin).
    from app.models.admin import Base  # type: ignore[assignment]

async def main(url: str) -> int:
    eng = create_async_engine(url)
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
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
