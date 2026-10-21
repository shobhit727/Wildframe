"""Regression tests for the production ``/metrics`` bearer-token guard (#469/#841).

user-service serves its own ``/metrics`` route so the guard can actually apply:
``create_app()`` passes ``register_metrics=False`` to ``wire_observability``,
otherwise the SDK's public route would shadow the gated one and hand the whole
Prometheus payload to any anonymous caller in production.

The guard is production-only by design -- the dev stack, local Prometheus and
the rest of the suite scrape without a credential -- so these tests pin both
halves of that contract: closed in production, open in development.
"""

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.settings import settings


def make_client(monkeypatch, *, env: str, **attrs) -> TestClient:
    """Build a client over a fresh ``create_app()`` with patched settings.

    The client is deliberately never entered as a context manager: the lifespan
    requires a live database and ``/metrics`` is served entirely by the route,
    so skipping startup keeps this test free of infrastructure.

    ``base_url`` is pinned to ``localhost`` because production trusts
    ``TRUSTED_HOSTS``, which does not include TestClient's default
    ``testserver`` host -- that rejection is a 400 from the host allowlist and
    would otherwise mask the status this file is asserting.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", env)
    for key, value in attrs.items():
        monkeypatch.setattr(settings, key, value)
    return TestClient(
        main_module.create_app(),
        base_url="http://localhost",
        raise_server_exceptions=False,
    )


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
    """The exact bearer token gets a real Prometheus payload."""
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
    """Exactly one ``/metrics`` route exists: this service's gated one.

    Starlette matches routes in registration order, so an SDK-registered public
    route sitting alongside this one would win and expose the payload.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    app = main_module.create_app()
    registered = [
        (route.path, route.name)
        for route in app.router.routes
        if getattr(route, "path", None) == "/metrics"
    ]
    assert registered == [("/metrics", "gated_metrics")]
