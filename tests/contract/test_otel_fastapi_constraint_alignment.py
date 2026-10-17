"""Contract guard for the OpenTelemetry FastAPI dependency alignment (#977)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# These manifests are the dependency edges identified by #977.
MANIFESTS = [
    REPO / "pyproject.toml",
    REPO / "packages" / "sdk" / "wildframe_observability" / "pyproject.toml",
    REPO / "services" / "admin-service" / "pyproject.toml",
    REPO / "services" / "auth-service" / "pyproject.toml",
    REPO / "services" / "user-service" / "pyproject.toml",
]

# Per-service locks called out by the issue plus the root lock and SDK lock.
LOCKS = [
    REPO / "poetry.lock",
    REPO / "packages" / "sdk" / "wildframe_observability" / "poetry.lock",
    REPO / "services" / "admin-service" / "poetry.lock",
    REPO / "services" / "auth-service" / "poetry.lock",
    REPO / "services" / "user-service" / "poetry.lock",
    REPO / "services" / "billing-service" / "poetry.lock",
    REPO / "services" / "streaming-service" / "poetry.lock",
]

# One version across every manifest and every lock. The value is pinned rather
# than merely "all equal", because the defect this gate exists for (#977) was
# three *disjoint* carets: equality alone would have passed a repo where every
# manifest agreed on a version that does not work.
#
# It is ^0.64b0 and not ^0.49b0 for a functional reason, recorded in #978:
# 0.49b0 resolves a span's route by reading `starlette_route.path` while
# iterating `app.routes`, and FastAPI 0.137+ nests include_router() routes under
# `_IncludedRouter`, which has no `.path`. Every included route raised
# AttributeError and 500'd. `^0.49b0` admits no version that can work, because
# the caret caps it below 0.50. 0.64b0 flattens the router tree via
# iter_route_contexts()/_flatten_routes instead.
#
# packages/sdk/wildframe_observability/tests/test_included_router_tracing.py
# is the behavioural test for that; this one is the bookkeeping check, and it
# exists because a version bump with green tests and an unfixed 500 is exactly
# what #978 was.
EXPECTED_CONSTRAINT = "^0.64b0"
EXPECTED_LOCK_VERSION = "0.64b0"


def _declared_constraint(path: Path) -> str | None:
    """Read the Poetry dependency table and return the instrumentation constraint."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    poetry = data.get("tool", {}).get("poetry", {})
    dependencies = poetry.get("dependencies", {})
    value = dependencies.get("opentelemetry-instrumentation-fastapi")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("version")
    return None


def _locked_version(path: Path) -> str | None:
    """Read the lock entry for opentelemetry-instrumentation-fastapi."""
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r'(?ms)^\[\[package\]\]\s*name = "opentelemetry-instrumentation-fastapi"\s*'
        r'version = "([^"]+)"',
        text,
    )
    return match.group(1) if match else None


def test_all_issue_manifests_use_one_otel_fastapi_constraint() -> None:
    """Prevent the three-way disjoint constraint drift from returning."""
    observed = {
        path.relative_to(REPO).as_posix(): _declared_constraint(path)
        for path in MANIFESTS
    }
    assert observed == {path: EXPECTED_CONSTRAINT for path in observed}, observed


def test_affected_locks_resolve_the_same_otel_fastapi_version() -> None:
    """Keep the lock snapshots on the resolved 0.49 prerelease line."""
    observed = {
        path.relative_to(REPO).as_posix(): _locked_version(path)
        for path in LOCKS
    }
    assert observed == {path: EXPECTED_LOCK_VERSION for path in observed}, observed
