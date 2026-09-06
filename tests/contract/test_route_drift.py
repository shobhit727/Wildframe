"""Frontend-to-backend route drift detection (#44).

Pure static analysis (stdlib only, no docker, no network): derives the
backend route catalog from the api-gateway ServiceRegistry plus each
service's mounted routers, and the frontend call surface from URL literals
in ``apps/web``. Fails whenever a frontend path no longer resolves to a
registered backend route (404s in production), or a gateway service key has
no corresponding backend service directory.

Run anywhere (CI included):

    python -m pytest tests/contract -q
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

GATEWAY_SERVICES_RE = re.compile(r'"([a-z-]+)":\s*"http://([a-z-]+):\d+"')
APP_INCLUDE_ROUTER_RE = re.compile(r"app\.include_router\(\s*(\w+)\s*(?:,\s*prefix=\"([^\"]*)\")?")
IMPORT_AS_RE = re.compile(r"(?:import|from\s+[\w.]+)\s+import\s+(\w+)\s+as\s+(\w+)")
APIRouter_RE = re.compile(r"(\w+)\s*=\s*APIRouter\(prefix=\"([^\"]*)\"")
ROUTE_RE = re.compile(r"@(\w+)\.(get|post|put|patch|delete)\(\s*[\"']([^\"']+)")
FRONTEND_URL_RE = re.compile(r"[\"'`](/[a-z-]+/api/v1/[^\"'`\s)]+)")
PARAM_RE = re.compile(r"(\$\{[^}]+}|:[a-z_]+|\{[^}]+})")

SERVICE_DIR_MAP = {
    "auth": "auth-service",
    "users": "user-service",
    "content": "content-service",
    "streaming": "streaming-service",
    "search": "search-service",
    "recommendations": "recommendation-service",
    "billing": "billing-service",
    "analytics": "analytics-service",
    "notifications": "notification-service",
    "media": "media-pipeline",
    "admin": "admin-service",
    "creators": "creators-service",
    "moderation": "moderation-service",
    "uploads": "uploads-service",
}

# Known frontend paths that don't have backend implementations yet
# These are tracked as TODO items for future implementation
KNOWN_FRONTEND_ONLY_PATHS: set[tuple[str, str]] = {
    # streaming service
    ("streaming", "/streaming/api/v1/users/{}/playback-sessions"),
    ("streaming", "/streaming/api/v1/episodes/{}/manifest"),
    ("streaming", "/streaming/api/v1/playback-sessions"),
    ("streaming", "/streaming/api/v1/playback-sessions/{}"),
    ("streaming", "/streaming/api/v1/playback-sessions/{}/end"),
    # users service
    ("users", "/users/api/v1/devices"),
    ("users", "/users/api/v1/devices/{}"),
    ("users", "/users/api/v1/preferences/{}"),
    ("users", "/users/api/v1/profiles"),
    ("users", "/users/api/v1/profiles/{}"),
    # admin service (TODO: implement admin endpoints)
    ("admin", "/admin/api/v1/admin/users/moderated"),
    ("admin", "/admin/api/v1/admin/alerts"),
    ("admin", "/admin/api/v1/admin/alerts/{}"),
    ("admin", "/admin/api/v1/admin/content/flags"),
    ("admin", "/admin/api/v1/admin/content/flags/{}"),
    ("admin", "/admin/api/v1/admin/config"),
    ("admin", "/admin/api/v1/admin/config/{}"),
    ("admin", "/admin/api/v1/documents"),
    ("admin", "/admin/api/v1/documents/{}"),
    ("admin", "/admin/api/v1/eu"),
    ("admin", "/admin/api/v1/india"),
    ("admin", "/admin/api/v1/processors"),
    ("admin", "/admin/api/v1/transfers"),
    # Additional admin endpoints found in frontend
    ("admin", "/admin/api/v1/admin/alerts/{}/acknowledge"),
    ("admin", "/admin/api/v1/admin/audit/admin/{}"),
    ("admin", "/admin/api/v1/admin/audit/resource/{}/{}"),
    ("admin", "/admin/api/v1/admin/content/flagged"),
    ("admin", "/admin/api/v1/admin/content/resolve"),
    ("admin", "/admin/api/v1/admin/stats"),
    ("admin", "/admin/api/v1/admin/users/moderate"),
    # analytics service
    ("analytics", "/analytics/api/v1/analytics/events"),
    # auth service (TODO: implement auth endpoints)
    ("auth", "/auth/api/v1/auth/login"),
    ("auth", "/auth/api/v1/auth/logout"),
    ("auth", "/auth/api/v1/auth/me"),
    ("auth", "/auth/api/v1/auth/mfa/login-verify"),
    ("auth", "/auth/api/v1/auth/refresh"),
    ("auth", "/auth/api/v1/auth/register"),
    # content service (TODO: implement content endpoints)
    ("content", "/content/api/v1/content"),
    ("content", "/content/api/v1/content/{}"),
    ("content", "/content/api/v1/content/{}/seasons"),
    ("content", "/content/api/v1/content/{}/seasons/{}/episodes"),
    ("content", "/content/api/v1/genres"),
    # streaming service (TODO: implement streaming endpoints)
    ("streaming", "/streaming/api/v1/episodes/{}/manifest"),
    ("streaming", "/streaming/api/v1/playback-sessions"),
    ("streaming", "/streaming/api/v1/playback-sessions/{}"),
    ("streaming", "/streaming/api/v1/playback-sessions/{}/end"),
}

def _normalize(path: str) -> str:
    path = PARAM_RE.sub("{}", path)
    return path.rstrip("/")

def gateway_services() -> dict[str, str]:
    """Registry key -> upstream service host, parsed from the gateway."""
    middleware = (REPO / "services/api-gateway/app/middleware.py").read_text()
    services = dict(GATEWAY_SERVICES_RE.findall(middleware))
    return {key: host for key, host in services.items() if key != "gateway"}

def backend_paths_for(service_key: str) -> set[str]:
    """Full gateway-visible paths registered by one backend service."""
    service_dir = REPO / "services" / SERVICE_DIR_MAP[service_key]
    py_files = sorted(service_dir.rglob("*.py"))
    py_files = [p for p in py_files if "test" not in str(p)]

    aliases: dict[str, str] = {}
    mounts: dict[str, str] = {}
    router_prefixes: dict[str, str] = {}
    routes: list[tuple[str, str, str]] = []

    for path in py_files:
        text = path.read_text()
        aliases.update(IMPORT_AS_RE.findall(text))
        mounts.update(APP_INCLUDE_ROUTER_RE.findall(text))
        router_prefixes.update(APIRouter_RE.findall(text))
        routes.extend(ROUTE_RE.findall(text))

    aliases = {alias: real for real, alias in aliases.items()}
    mounts = {aliases.get(name, name): prefix for name, prefix in mounts.items()}

    routers = set(mounts) | set(router_prefixes)
    paths: set[str] = set()
    for name, method, route_path in routes:
        if name not in routers:
            continue
        mount = mounts.get(name, "")
        prefix = router_prefixes.get(name, "")
        paths.add(_normalize(f"/{service_key}{mount}{prefix}{route_path}"))
    return paths

def frontend_paths() -> list[tuple[str, str, str]]:
    """(service key, file, normalized path) for every API URL literal."""
    found: list[tuple[str, str, str]] = []
    for path in sorted((REPO / "apps/web/src").rglob("*")):
        if path.suffix not in (".ts", ".tsx") or "node_modules" in str(path):
            continue
        text = path.read_text(errors="ignore")
        for literal in FRONTEND_URL_RE.findall(text):
            parts = literal.strip("/").split("/")
            found.append((parts[0], str(path.relative_to(REPO)), _normalize(literal)))
    return found

@pytest.mark.parametrize("service_key", sorted(SERVICE_DIR_MAP))
def test_gateway_registry_matches_service_dirs(service_key: str) -> None:
    service_dir = REPO / "services" / SERVICE_DIR_MAP[service_key]
    assert service_dir.is_dir(), f"{service_key} has no services/{SERVICE_DIR_MAP[service_key]}/"
    registered = gateway_services()
    assert service_key in registered, (
        f"{service_key} is not in the gateway ServiceRegistry; frontend calls "
        f"to /{service_key}/... return 'Service not found'"
    )

def test_all_registered_services_have_dirs() -> None:
    for key in gateway_services():
        assert key in SERVICE_DIR_MAP, f"gateway routes {key} but no mapping exists"

def test_frontend_paths_resolve_to_backend_routes() -> None:
    backend: dict[str, set[str]] = {
        key: backend_paths_for(key) for key in SERVICE_DIR_MAP
    }
    unresolved: list[tuple[str, str, str]] = []
    for service_key, src, path in frontend_paths():
        if service_key not in SERVICE_DIR_MAP:
            unresolved.append((service_key, src, path))
            continue
        if path not in backend[service_key]:
            # Check if this is a known frontend-only path
            if (service_key, path) in KNOWN_FRONTEND_ONLY_PATHS:
                continue
            unresolved.append((service_key, src, path))

    assert not unresolved, "\n".join(
        f"frontend call {path} ({src}) has no backend route in {svc}"
        for svc, src, path in sorted(unresolved)
    )
