"""
OAuth security surface (#223).

The auth-service exposes NO OAuth endpoints today (verified: no routes,
settings, schemas, or database tables across services/, the gateway, and
the frontend). All five audit findings for #223 are therefore vacuous. This
file pins the absence so that any future OAuth implementation is a
deliberate change reviewed against the audit's requirements:

  1. state must be unpredictable, session-bound, and single-use;
  2. redirect URIs must use exact allowlists (no parser-widening);
  3. authorization codes must be single-use;
  4. account linking must never attach an OAuth identity to the wrong
     local account;
  5. provider token claims must be validated against the expected issuer
     and client ID.
"""

import re

import pytest

OAUTH_IDENTIFIERS = re.compile(
    r"\boauth\b|authorization.?code|redirect_uri|client_id|client_secret",
    re.IGNORECASE,
)


def _runtime_sources():
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    for svc in ("auth-service", "api-gateway"):
        base = repo / "services" / svc
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "tests" in path.parts or "migrations" in path.parts:
                continue
            yield path


def test_no_oauth_endpoints_exposed() -> None:
    from app.main import app

    # /docs/oauth2-redirect is the Swagger UI's own demo redirect, not an
    # OAuth provider endpoint.
    oauth_routes = [
        route.path
        for route in app.routes
        if hasattr(route, "path")
        and "oauth" in route.path.lower()
        and route.path != "/docs/oauth2-redirect"
    ]
    assert not oauth_routes, f"OAuth endpoints exposed: {oauth_routes}"


def test_no_oauth_configuration_in_settings() -> None:
    from app.core.settings import settings

    oauth_settings = [name for name in dir(settings) if "oauth" in name.lower()]
    assert not oauth_settings, f"OAuth settings present: {oauth_settings}"


def test_no_oauth_schemas_or_models() -> None:
    import app.schemas as schemas_mod
    from app.models import Base

    schema_hits = [name for name in dir(schemas_mod) if "oauth" in name.lower()]
    assert not schema_hits, f"OAuth schemas present: {schema_hits}"

    oauth_tables = [t for t in Base.metadata.tables if "oauth" in t.lower()]
    assert not oauth_tables, f"OAuth tables present: {oauth_tables}"


def test_no_oauth_identifiers_in_runtime_code() -> None:
    """No OAuth plumbing may be added without deliberate review.

    If this test fails, the new code must implement the five #223 audit
    requirements (single-use state, exact redirect allowlist, single-use
    authorization codes, safe account linking, issuer+client_id claim
    validation) and extend this file with the corresponding tests.
    """
    hits = []
    for path in _runtime_sources():
        if OAUTH_IDENTIFIERS.search(path.read_text(errors="ignore")):
            hits.append(str(path.relative_to(path.parents[3])))
    assert not hits, f"OAuth references in runtime code: {hits}"
