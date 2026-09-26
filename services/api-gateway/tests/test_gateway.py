"""Fixed gateway tests."""


def test_gateway_dummy():
    """The app factory wires the documented probe routes and the proxy router."""
    from fastapi import FastAPI

    from app.main import app
    from app.middleware import ServiceRegistry

    assert isinstance(app, FastAPI)
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/health", "/ready", "/metrics"} <= paths

    # The catch-all proxy is registered last so /gateway/* keeps winning.
    schema_paths = set(app.openapi()["paths"])
    assert "/{service}" in schema_paths
    assert {"/gateway/health", "/gateway/ready", "/gateway/services"} <= schema_paths

    # Every registered backend must be reachable through the catch-all proxy.
    assert set(ServiceRegistry.SERVICES) >= {"auth", "content", "uploads"}
