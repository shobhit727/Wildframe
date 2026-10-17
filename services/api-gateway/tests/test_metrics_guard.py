"""Regression tests for the production ``/metrics`` bearer-token guard (#469/#841).

api-gateway is the one service where the guard could not simply be copied from
the domain services. Its router ends in a catch-all ``/{service:path}`` proxy
route, and Starlette matches routes in registration order -- so a ``/metrics``
route declared after ``include_router()`` is unreachable. Before this change
the SDK's public route sat in exactly that position and was dead: the catch-all
answered ``404 Service not found`` for every scrape, so the gateway had been
absent from monitoring rather than leaking telemetry.

These tests pin both the guard and the ordering that makes it reachable.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Match

import app.main as main_module
from app.core.settings import settings


def make_client(monkeypatch, *, env: str, **attrs) -> TestClient:
    """Build a client over a fresh ``create_app()`` with patched settings.

    The client is deliberately never entered as a context manager: the lifespan
    provisions Redis and the shared HTTP client, none of which ``/metrics``
    needs now that the route is matched before the proxy catch-all.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", env)
    for key, value in attrs.items():
        monkeypatch.setattr(settings, key, value)
    return TestClient(
        main_module.create_app(),
        base_url="http://localhost",
        raise_server_exceptions=False,
    )


def _winning_route(app, path: str):
    """Return the first route that fully matches ``path`` -- the one that runs.

    Listing routes is not enough here: a shadowed route still appears in
    ``app.routes`` while never being reached, which is precisely the bug that
    kept the gateway's metrics dark.
    """
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    for route in app.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return route
    return None


@pytest.mark.unit
def test_metrics_requires_a_token_in_production(monkeypatch):
    """An anonymous production scrape is refused."""
    client = make_client(monkeypatch, env="production", METRICS_TOKEN="s3cret")
    assert client.get("/metrics").status_code == 401


@pytest.mark.unit
def test_metrics_rejects_a_wrong_token_in_production(monkeypatch):
    """A wrong bearer token is refused too."""
    client = make_client(monkeypatch, env="production", METRICS_TOKEN="s3cret")
    response = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


@pytest.mark.unit
def test_metrics_accepts_the_configured_token_in_production(monkeypatch):
    """The exact bearer token gets a real Prometheus payload.

    This is the assertion that was impossible before: the route was shadowed by
    the proxy catch-all, so the gateway answered 404 and no scrape ever landed.
    """
    client = make_client(monkeypatch, env="production", METRICS_TOKEN="s3cret")
    response = client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert b"# HELP" in response.content


@pytest.mark.unit
def test_metrics_is_open_in_development(monkeypatch):
    """Local scraping stays unauthenticated so dev Prometheus is not locked out."""
    client = make_client(monkeypatch, env="development", METRICS_TOKEN="")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert b"# HELP" in response.content


@pytest.mark.unit
def test_production_fails_closed_when_no_token_is_configured(monkeypatch):
    """An unset METRICS_TOKEN must deny, not silently open the endpoint."""
    client = make_client(monkeypatch, env="production", METRICS_TOKEN="")
    response = client.get("/metrics", headers={"Authorization": "Bearer anything"})
    assert response.status_code == 401


@pytest.mark.unit
def test_the_scrape_route_is_not_shadowed(monkeypatch):
    """``gated_metrics`` must win the match for ``/metrics``, not the catch-all.

    This is the ordering guarantee. ``app.routes`` would still list the route
    even if the proxy catch-all shadowed it, so the assertion is made against
    the route that actually resolves.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    app = main_module.create_app()

    registered = [
        (route.path, route.name)
        for route in app.router.routes
        if getattr(route, "path", None) == "/metrics"
    ]
    assert registered == [("/metrics", "gated_metrics")], (
        "the SDK's public /metrics route is registered again and will shadow the "
        "guarded one; pass register_metrics=False to wire_observability()"
    )

    winner = _winning_route(app, "/metrics")
    assert winner is not None, "no route resolves /metrics"
    assert getattr(winner, "name", None) == "gated_metrics", (
        f"/metrics resolves to {getattr(winner, 'name', winner)!r}, not the gated "
        "route -- the proxy catch-all was registered first and is shadowing it"
    )


@pytest.mark.unit
def test_the_proxy_catch_all_still_routes_other_paths(monkeypatch):
    """Registering /metrics early must not break the gateway's proxy job.

    The catch-all is what makes the gateway a gateway, so it has to stay
    reachable for a genuine service path. It is matched as part of the included
    router rather than as a top-level route, so the check walks to the route
    that actually handles the request inside the winning match.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    app = main_module.create_app()
    winner = _winning_route(app, "/auth/login")

    assert winner is not None, "no route resolves /auth/login"
    assert getattr(winner, "name", None) != "gated_metrics"

    handled = [
        getattr(candidate, "name", None)
        for candidate in getattr(winner, "_effective_candidates", [])
    ]
    assert "proxy_request" in handled, (
        f"/auth/login is no longer handled by the proxy catch-all (resolved to "
        f"{handled!r}); the gateway would stop routing service traffic"
    )
