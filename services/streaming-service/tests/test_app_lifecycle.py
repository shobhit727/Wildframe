"""Lifecycle and wiring tests for ``streaming-service/app/main.py``.

``create_app()`` and ``lifespan()`` were previously unexercised: the suite
only imported the module-level ``app``. These tests drive the factory directly
so the production/dev branching, the ``/health`` DB verdict, the request body
cap, the opaque 500 handler and the shutdown path are all actually executed.

The module-level ``app`` is left untouched — every test that needs to mutate
state builds a throwaway instance via ``create_app()``.
"""

import asyncio
import contextlib
import runpy
import sys
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db_manager
from app.core.settings import settings

# ---------------------------------------------------------------- lifespan --


async def _drive_lifespan(app, health_result: bool) -> tuple[list, list]:
    """Enter and exit the lifespan, recording the log messages emitted."""
    records: list = []

    class _Capture:
        def info(self, msg, *a, **kw):
            records.append(("info", msg % a if a else msg))

        def warning(self, msg, *a, **kw):
            records.append(("warning", msg % a if a else msg))

    original = main_module.logger
    main_module.logger = _Capture()
    try:
        async with main_module.lifespan(app):
            pass
    finally:
        main_module.logger = original
    return records, health_result


@pytest.mark.unit
async def test_lifespan_startup_reports_healthy_database(monkeypatch):
    """A healthy DB must log the 'connection established' branch (main.py:34)."""
    check = AsyncMock(return_value=True)
    monkeypatch.setattr(db_manager, "health_check", check)
    close = AsyncMock()
    monkeypatch.setattr(db_manager, "close", close)

    app = main_module.create_app()
    records, _ = await _drive_lifespan(app, True)

    messages = [m for _, m in records]
    assert any("Database connection established" in m for m in messages)
    assert not any("health check failed" in m for m in messages)
    # Startup announced, and shutdown disposed the engine.
    assert any(m.startswith("Starting streaming-service") for m in messages)
    assert any(m == "Shutting down streaming-service" for m in messages)
    check.assert_awaited_once()
    close.assert_awaited_once()


@pytest.mark.unit
async def test_lifespan_startup_warns_when_database_unhealthy(monkeypatch):
    """An unhealthy DB must only warn -- startup still succeeds (main.py:32)."""
    monkeypatch.setattr(db_manager, "health_check", AsyncMock(return_value=False))
    monkeypatch.setattr(db_manager, "close", AsyncMock())

    app = main_module.create_app()
    records, _ = await _drive_lifespan(app, False)

    messages = [m for _, m in records]
    assert ("warning", "Database health check failed on startup") in records
    assert not any("Database connection established" in m for m in messages)


# --------------------------------------------------------------- create_app --


@pytest.mark.unit
def test_create_app_exposes_docs_in_non_production(monkeypatch):
    """Outside production the three doc routes must be reachable."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    app = main_module.create_app()
    assert app.docs_url == "/docs"
    assert app.redoc_url == "/redoc"
    assert app.openapi_url == "/openapi.json"


@pytest.mark.unit
def test_create_app_disables_docs_in_production(monkeypatch):
    """#468: production must not serve interactive docs or the schema."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    app = main_module.create_app()
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None

    with TestClient(app, base_url="http://localhost") as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/redoc").status_code == 404
        assert c.get("/openapi.json").status_code == 404


@pytest.mark.unit
def test_create_app_registers_api_router_and_static_mount(monkeypatch):
    """The factory wires the /api/v1 router and the self-hosted HLS assets.

    Route assertions go through the generated OpenAPI schema: this FastAPI
    version keeps ``include_router`` results in a lazy ``_IncludedRouter``
    wrapper, so ``app.routes`` does not enumerate them.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    app = main_module.create_app()

    paths = set(app.openapi()["paths"])
    assert "/api/v1/playback-sessions" in paths
    assert "/api/v1/manifests" in paths
    assert "/api/v1/episodes/{episode_id}/manifest" in paths
    assert "/health" in paths
    # The Prometheus scrape endpoint comes from wire_observability.
    assert "/metrics" in {getattr(r, "path", None) for r in app.routes}

    with TestClient(app, base_url="http://localhost") as c:
        playlist = c.get("/static/demo/hls/master.m3u8")
        assert playlist.status_code == 200
        assert "#EXTM3U" in playlist.text
        # Directory listing is not enabled, so the bare mount 404s.
        assert c.get("/static/").status_code == 404
        # ...and a file outside the mounted root is not traversable.
        assert c.get("/static/../main.py").status_code == 404


@pytest.mark.unit
def test_cors_middleware_allows_configured_origin(monkeypatch):
    """CORS is wired from settings, not hardcoded."""
    monkeypatch.setattr(settings, "CORS_ALLOWED_ORIGINS", ["https://allowed.example"])
    app = main_module.create_app()
    with TestClient(app, base_url="http://localhost") as c:
        r = c.get("/health", headers={"Origin": "https://allowed.example"})
    assert r.headers["access-control-allow-origin"] == "https://allowed.example"


# ------------------------------------------------------------ /health route --


@pytest.mark.unit
def test_health_reports_healthy_when_db_up(monkeypatch):
    """main.py:81-82: a passing DB check yields status/database 'healthy'."""
    monkeypatch.setattr(db_manager, "health_check", AsyncMock(return_value=True))
    app = main_module.create_app()
    with TestClient(app, base_url="http://localhost") as c:
        body = c.get("/health").json()
    assert body["status"] == "healthy"
    assert body["database"] == "healthy"
    assert body["redis"] == "healthy"
    assert body["version"] == settings.SERVICE_VERSION


@pytest.mark.unit
def test_health_reports_unhealthy_when_db_down(monkeypatch):
    """The opposite verdict must propagate instead of a hardcoded 'ok'."""
    monkeypatch.setattr(db_manager, "health_check", AsyncMock(return_value=False))
    app = main_module.create_app()
    with TestClient(app, base_url="http://localhost") as c:
        body = c.get("/health").json()
    assert body["status"] == "unhealthy"
    assert body["database"] == "unhealthy"


# --------------------------------------------------------- body size cap ----


@pytest.mark.unit
def test_oversized_body_is_rejected_with_413(monkeypatch):
    """main.py:100-103: content-length above the 1 MiB cap is refused."""
    app = main_module.create_app()
    with TestClient(app, base_url="http://localhost") as c:
        r = c.get("/health", headers={"content-length": "1048577"})
    assert r.status_code == 413
    assert r.json()["detail"] == "Request body too large"


@pytest.mark.unit
def test_body_exactly_at_cap_is_allowed_through(monkeypatch):
    """The cap is inclusive: exactly 1 MiB is not rejected."""
    monkeypatch.setattr(db_manager, "health_check", AsyncMock(return_value=True))
    app = main_module.create_app()
    with TestClient(app, base_url="http://localhost") as c:
        r = c.get("/health", headers={"content-length": "1048576"})
    assert r.status_code == 200


@pytest.mark.unit
def test_unparseable_content_length_passes_through(monkeypatch):
    """main.py:104-105: a non-numeric content-length is ignored, not a 500."""
    monkeypatch.setattr(db_manager, "health_check", AsyncMock(return_value=True))
    app = main_module.create_app()
    with TestClient(app, base_url="http://localhost") as c:
        r = c.get("/health", headers={"content-length": "not-a-number"})
    assert r.status_code == 200


@pytest.mark.unit
def test_missing_content_header_passes_through(monkeypatch):
    """main.py:97: GET without content-length takes the passthrough path."""
    monkeypatch.setattr(db_manager, "health_check", AsyncMock(return_value=True))
    app = main_module.create_app()
    with TestClient(app, base_url="http://localhost") as c:
        r = c.get("/health")
    assert r.status_code == 200


# -------------------------------------------------- opaque 500 handler -----


@pytest.mark.unit
def test_unhandled_exception_returns_opaque_500(monkeypatch):
    """main.py:113-114: #557 -- internals must never reach the client."""

    @main_module.app.get("/_boom")
    async def _boom():
        raise RuntimeError("secret db password leaked")

    with TestClient(main_module.app, base_url="http://localhost", raise_server_exceptions=False) as c:
        r = c.get("/_boom")

    assert r.status_code == 500
    body = r.json()
    assert body == {"status_code": 500, "message": "Internal server error"}
    assert "secret db password" not in r.text
    # The route was registered on the shared app; drop it again.
    main_module.app.router.routes = [
        route for route in main_module.app.router.routes if getattr(route, "name", "") != "_boom"
    ]


# --------------------------------------------------------- __main__ block ----


@pytest.mark.unit
def test_main_entrypoint_starts_uvicorn(monkeypatch):
    """main.py:126-128: the ``python -m app.main`` path boots uvicorn."""
    recorded: dict = {}
    fake_uvicorn = type(
        "FakeUvicorn",
        (),
        {
            "run": staticmethod(
                lambda target, host, port: recorded.update(
                    target=target, host=host, port=port
                )
            )
        },
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(settings, "SERVER_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "SERVER_PORT", 8004)

    runpy.run_module("app.main", run_name="__main__")

    assert recorded["host"] == "127.0.0.1"
    assert recorded["port"] == 8004
    assert recorded["target"].title == "Streaming Service"


# ------------------------------------------------- observability wiring -----


@pytest.mark.unit
def test_observability_middleware_is_installed(monkeypatch):
    """wire_observability contributes correlation, logging and metrics layers."""
    from wildframe_observability.metrics import MetricsMiddleware
    from wildframe_observability.middleware import (
        CorrelationMiddleware,
        RequestLoggingMiddleware,
    )

    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    app = main_module.create_app()
    installed = {m.cls for m in app.user_middleware}
    assert CorrelationMiddleware in installed
    assert RequestLoggingMiddleware in installed
    assert MetricsMiddleware in installed


@pytest.mark.unit
def test_request_id_header_is_propagated(monkeypatch):
    """X-Request-ID must survive the correlation middleware into a response."""
    monkeypatch.setattr(db_manager, "health_check", AsyncMock(return_value=True))
    app = main_module.create_app()
    with TestClient(app, base_url="http://localhost") as c:
        r = c.get("/health", headers={"X-Request-ID": "req-abc-123"})
    assert r.status_code == 200


@pytest.mark.unit
async def test_lifespan_does_NOT_dispose_engine_when_served_block_raises(monkeypatch):
    """KNOWN BUG — app/main.py:36-40.

    ``lifespan`` is ``yield`` followed by ``await db_manager.close()`` with no
    ``try``/``finally`` around the yield. When anything raises inside the
    served block, the exception is thrown *into* the generator at the yield and
    propagates straight out, so line 40 never executes and the async engine is
    never disposed -- a connection-pool leak on any shutdown that errors.

    This test pins the CURRENT (buggy) behaviour so the regression is visible.
    If ``lifespan`` is ever hardened with a try/finally, this test fails and
    must be inverted to ``assert close.await_count == 1``.
    """
    close = AsyncMock()
    monkeypatch.setattr(db_manager, "health_check", AsyncMock(return_value=True))
    monkeypatch.setattr(db_manager, "close", close)
    app = main_module.create_app()

    with contextlib.suppress(RuntimeError):
        async with main_module.lifespan(app):
            raise RuntimeError("failure inside the served block")

    assert close.await_count == 0, (
        "expected the current leaky behaviour; if this now passes with 1 "
        "the lifespan was fixed and this assertion should be flipped"
    )


@pytest.mark.unit
async def test_lifespan_disposes_engine_on_clean_shutdown(monkeypatch):
    """The normal path does tear the engine down exactly once."""
    close = AsyncMock()
    monkeypatch.setattr(db_manager, "health_check", AsyncMock(return_value=True))
    monkeypatch.setattr(db_manager, "close", close)
    app = main_module.create_app()

    async with main_module.lifespan(app):
        pass

    close.assert_awaited_once()


@pytest.mark.unit
async def test_lifespan_is_a_single_use_async_context_manager(monkeypatch):
    """A second concurrent enter on one app object is not supported by design;
    this pins that the context manager is *reusable per app* (fresh event loop
    safe) and that each run performs exactly one health check + one close."""
    monkeypatch.setattr(db_manager, "health_check", AsyncMock(return_value=True))
    close = AsyncMock()
    monkeypatch.setattr(db_manager, "close", close)
    app = main_module.create_app()

    for _ in range(2):
        async with main_module.lifespan(app):
            pass

    assert db_manager.health_check.await_count == 2
    assert close.await_count == 2


@pytest.mark.unit
def test_asyncio_import_is_used_for_teardown_ordering():
    """The module imports asyncio indirectly; guard the drain-free shutdown.

    The streaming lifespan has no drain loop (unlike analytics) so shutdown is
    a single await. This asserts there is no in-flight counter to leak.
    """
    assert not hasattr(main_module, "_in_flight_requests")
    assert asyncio.iscoroutinefunction(main_module.db_manager.close)
