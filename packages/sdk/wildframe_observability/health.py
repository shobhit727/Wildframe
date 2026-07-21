"""Standardized health check response builder.

All Wildframe services return the same health response structure::

    {
        "status": "healthy" | "degraded" | "unhealthy",
        "service": "billing",
        "version": "2.0.0",
        "timestamp": "2026-06-30T12:00:00+00:00",
        "checks": {
            "database": "ok" | "unavailable",
            "redis": "ok" | "unavailable",
        }
    }
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def create_health_response(
    service_name: str,
    version: str,
    db_ok: bool = True,
    redis_ok: Optional[bool] = None,
) -> dict:
    """Build a standardized health check response.

    Status logic:
      - "healthy": all checks pass
      - "degraded": non-critical check fails (e.g. Redis)
      - "unhealthy": critical check fails (database)
    """
    checks = {"database": "ok" if db_ok else "unavailable"}
    if redis_ok is not None:
        checks["redis"] = "ok" if redis_ok else "unavailable"

    if not db_ok:
        status = "unhealthy"
    elif redis_ok is False:
        status = "degraded"
    else:
        status = "healthy"

    return {
        "status": status,
        "service": service_name,
        "version": version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
