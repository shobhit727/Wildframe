"""Lifecycle and wiring tests for ``analytics-service/app/main.py``.

``create_app()`` is 142 lines and had no coverage at all: the existing suite
uses ``TestClient(app)`` *without* entering the lifespan (it raises when no
database is reachable), and never rebuilds the app, so the production branches,
``/ready``, the ``shutting_down`` 503 gate, the drain timeout and the opaque
500 handler were all unexecuted.

Two mechanics are worth knowing before reading the tests:

* The client is deliberately **not** used as a context manager -- entering it
  runs ``lifespan``, which raises when the database is unreachable.
* ``create_app()`` calls ``wire_observability``, whose ``dictConfig`` replaces
  the root logger's handlers and therefore evicts pytest's ``caplog`` handler.
  Log assertions go through the ``main_logs`` fixture, which attaches a handler
  to the ``app.main`` logger itself.

Every test stubs the database, Redis and content-client boundaries.
"""

import asyncio
import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.content_client import ContentServiceUnavailableError
from app.core.database import DatabaseManager
from app.core.settings import settings

# --------------------------------------------------------------- helpers ----


class _Awaitable:
    """An awaitable that yields a fixed value and counts its invocations."""

    def __init__(self, value):
        self.value = value
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self

    def __await__(self):
        async def _inner():
            return self.value

        return _inner().__await__()


class _FakeRedis:
    def __init__(self, ping_result=True, ping_delay=0.0):
        self._ping_result = ping_result
        self._ping_delay = ping_delay
        self.closed = 0

    def __await__(self):
        """Model the real client: awaiting the object yields the object.

        ``redis.asyncio.Redis`` defines ``__await__`` (it returns
        ``self.initialize().__await__()``), so ``await redis.from_url(...)`` in
        ``/ready`` hands back the client itself, which is then pinged and
        closed. A double without ``__await__`` makes that ``await`` raise
        ``TypeError``, the surrounding ``except Exception`` reports redis as
        ``down``, and ``/ready`` answers 503 even with both dependencies up.
        """

        async def _identity():
            return self

        return _identity().__await__()

    async def ping(self):
        if self._ping_delay:
            await asyncio.sleep(self._ping_delay)
        return self._ping_result

    async def close(self):
        self.closed += 1


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def main_logs():
    """Capture ``app.main`` records, surviving the observability dictConfig."""
    handler = _ListHandler()
    logger = logging.getLogger("app.main")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.propagate = True


@pytest.fixture
def sink():
    """A list that stubbed teardown callbacks append to."""
    return []


@pytest.fixture
def make_client(monkeypatch):
    """Factory producing un-entered TestClients over fresh ``create_app()``s."""
    clients: list[TestClient] = []

    def _make(*, env: str = "development", **attrs) -> TestClient:
        monkeypatch.setattr(settings, "ENVIRONMENT", env)
        for key, value in attrs.items():
            monkeypatch.setattr(settings, key, value)
        app = main_module.create_app()
        # Reset the graceful-shutdown globals the middleware reads.
        main_module._shutdown_event = asyncio.Event()
        main_module._in_flight_lock = asyncio.Lock()
        main_module._in_flight_requests = 0
        app.state.shutting_down = False
        client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
        clients.append(client)
        return client

    try:
        yield _make
    finally:
        for client in clients:
            client.close()
        main_module._in_flight_requests = 0


def stub_redis(monkeypatch, client: _FakeRedis) -> _FakeRedis:
    def _from_url(*args, **kwargs):
        return client

    monkeypatch.setattr(main_module.redis, "from_url", _from_url)
    return client


# ================================== lifespan ================================


@pytest.mark.unit
async def test_lifespan_startup_sets_state_and_tears_down(monkeypatch, sink):
    """Healthy startup clears the shutdown flag; shutdown disposes clients."""
    monkeypatch.setattr(DatabaseManager, "health_check", _Awaitable(True))
    db_close = _Awaitable(None)
    monkeypatch.setattr(DatabaseManager, "close", db_close)

    async def _close_content_client():
        sink.append("content-client-closed")

    monkeypatch.setattr(main_module, "close_content_client", _close_content_client)

    app = main_module.create_app()
    async with main_module.lifespan(app):
        assert app.state.shutting_down is False
        assert main_module._shutdown_event is not None
        assert main_module._shutdown_event.is_set() is False

    assert app.state.shutting_down is True
    assert main_module._shutdown_event.is_set() is True
    assert sink == ["content-client-closed"]
    assert db_close.calls == 1


@pytest.mark.unit
async def test_lifespan_raises_when_the_database_is_unhealthy(monkeypatch, sink, main_logs):
    """main.py:38-40 -- an unhealthy DB must abort startup, not limp along."""
    monkeypatch.setattr(DatabaseManager, "health_check", _Awaitable(False))

    async def _close_content_client():
        sink.append("content-client-closed")

    monkeypatch.setattr(main_module, "close_content_client", _close_content_client)
    db_close = _Awaitable(None)
    monkeypatch.setattr(DatabaseManager, "close", db_close)

    app = main_module.create_app()
    with pytest.raises(RuntimeError, match="Database is not healthy on startup"):
        async with main_module.lifespan(app):
            pytest.fail("lifespan must not yield when the database is down")

    # Startup aborted before the yield, so the teardown block never ran.
    assert sink == []
    assert db_close.calls == 0
    assert any("Database health check failed" in r.getMessage() for r in main_logs)


@pytest.mark.unit
async def test_lifespan_creates_a_fresh_shutdown_event_per_startup(monkeypatch, sink):
    """Two sequential lifespans must not share a stale shutdown event."""
    monkeypatch.setattr(DatabaseManager, "health_check", _Awaitable(True))
    monkeypatch.setattr(DatabaseManager, "close", _Awaitable(None))
    monkeypatch.setattr(main_module, "close_content_client", lambda: _noop(sink))

    app = main_module.create_app()

    async with main_module.lifespan(app):
        first = main_module._shutdown_event
    async with main_module.lifespan(app):
        second = main_module._shutdown_event
        assert second is not first
        assert second.is_set() is False


async def _noop(sink: list):
    sink.append(True)


# ============================== create_app: docs ============================


@pytest.mark.unit
def test_docs_are_served_outside_production(make_client):
    """Non-production keeps interactive docs and the OpenAPI schema."""
    c = make_client(env="development")
    assert c.get("/docs").status_code == 200
    assert c.get("/redoc").status_code == 200
    schema = c.get("/openapi.json")
    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == settings.SERVICE_NAME


@pytest.mark.unit
def test_docs_are_disabled_in_production(make_client):
    """#468: production must not expose docs, redoc or the schema."""
    c = make_client(env="production")
    assert c.get("/docs").status_code == 404
    assert c.get("/redoc").status_code == 404
    assert c.get("/openapi.json").status_code == 404


@pytest.mark.unit
def test_api_router_is_still_mounted_in_production(make_client):
    """Only the docs routes are removed; the API surface stays."""
    c = make_client(env="production")
    # No bearer token -> 401 from the auth dependency, proving the route exists.
    r = c.get("/api/v1/analytics/user-events/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 401
    assert r.json()["detail"] == "Missing or invalid authorization header"


# ============================== /health (liveness) ==========================


@pytest.mark.unit
def test_health_is_status_only_and_never_touches_the_database(make_client, monkeypatch):
    """main.py:112-116 -- liveness must stay dependency-free.

    ``health_check`` is armed to explode; a 200 proves it is not consulted.
    """

    def _explode():
        raise AssertionError("/health must not probe the database")

    monkeypatch.setattr(DatabaseManager, "health_check", _explode)

    c = make_client()
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ================================= /ready ===================================


@pytest.mark.unit
def test_ready_reports_200_when_db_and_redis_are_up(make_client, monkeypatch):
    """main.py:121-150 -- the all-healthy payload path."""
    monkeypatch.setattr(DatabaseManager, "health_check", _Awaitable(True))
    redis_client = stub_redis(monkeypatch, _FakeRedis())

    c = make_client()
    r = c.get("/ready")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["service"] == "analytics"
    assert body["version"] == settings.SERVICE_VERSION
    assert body["checks"] == {"database": "ok", "redis": "ok"}
    assert redis_client.closed == 1


@pytest.mark.unit
def test_ready_reports_503_when_the_database_is_down(make_client, monkeypatch):
    """A failing DB check alone is enough to flip the verdict."""
    monkeypatch.setattr(DatabaseManager, "health_check", _Awaitable(False))
    stub_redis(monkeypatch, _FakeRedis())

    c = make_client()
    r = c.get("/ready")

    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "down"
    assert body["checks"]["redis"] == "ok"


@pytest.mark.unit
def test_ready_reports_503_when_redis_connection_fails(
    make_client, monkeypatch, main_logs
):
    """main.py:134-137 -- a Redis connection failure is logged and denies."""
    monkeypatch.setattr(DatabaseManager, "health_check", _Awaitable(True))

    def _boom(*args, **kwargs):
        raise OSError("redis unreachable")

    monkeypatch.setattr(main_module.redis, "from_url", _boom)

    c = make_client()
    r = c.get("/ready")

    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["redis"] == "down"
    assert body["checks"]["database"] == "ok"
    assert any("Redis readiness check failed" in rec.getMessage() for rec in main_logs)


@pytest.mark.unit
def test_ready_reports_503_when_the_redis_ping_times_out(make_client, monkeypatch):
    """main.py:131 -- ``asyncio.wait_for`` must surface as a Redis outage.

    The ping sleeps far beyond the budget, so the real 2s timeout fires (the
    sleep is cut short by a patched ``wait_for`` so the test stays fast).
    """
    monkeypatch.setattr(DatabaseManager, "health_check", _Awaitable(True))
    stub_redis(monkeypatch, _FakeRedis(ping_delay=30))

    real_wait_for = asyncio.wait_for

    async def _fast_wait_for(awaitable, timeout):
        return await real_wait_for(awaitable, timeout=0.01)

    monkeypatch.setattr(main_module.asyncio, "wait_for", _fast_wait_for)

    c = make_client()
    r = c.get("/ready")

    assert r.status_code == 503
    assert r.json()["checks"]["redis"] == "down"


@pytest.mark.unit
def test_ready_reports_503_when_both_dependencies_are_down(
    make_client, monkeypatch, main_logs
):
    """Both checks failing still yields one 503 with both verdicts."""
    monkeypatch.setattr(DatabaseManager, "health_check", _Awaitable(False))

    def _boom(*args, **kwargs):
        raise OSError("redis unreachable")

    monkeypatch.setattr(main_module.redis, "from_url", _boom)

    c = make_client()
    r = c.get("/ready")

    assert r.status_code == 503
    assert r.json()["checks"] == {"database": "down", "redis": "down"}


# ====================== graceful shutdown: shutting_down ===================


@pytest.mark.unit
def test_requests_are_refused_with_503_while_shutting_down(make_client):
    """main.py:97-102 -- a draining service must reject new work with 503."""
    c = make_client()
    c.app.state.shutting_down = True
    r = c.get("/health")

    assert r.status_code == 503
    assert r.json() == {"detail": "Service shutting down"}
    assert r.headers["retry-after"] == str(main_module._MAX_DRAIN_SECONDS)


@pytest.mark.unit
def test_the_503_gate_comes_before_any_route_body(make_client, monkeypatch):
    """Even a route that would consult dependencies is intercepted first."""
    monkeypatch.setattr(DatabaseManager, "health_check", _Awaitable(True))
    stub_redis(monkeypatch, _FakeRedis())

    c = make_client()
    c.app.state.shutting_down = True
    # /ready would be 200 with both dependencies healthy.
    assert c.get("/ready").status_code == 503


@pytest.mark.unit
def test_requests_flow_again_after_shutdown_completes(make_client):
    """The flag is cleared at startup, so a fresh app serves normally."""
    c = make_client()
    assert c.get("/health").status_code == 200
    c.app.state.shutting_down = True
    assert c.get("/health").status_code == 503
    c.app.state.shutting_down = False
    assert c.get("/health").status_code == 200


# ================== graceful shutdown: in-flight drain ======================


@pytest.mark.unit
async def test_lifespan_waits_for_in_flight_requests_before_closing(
    monkeypatch, sink, main_logs
):
    """Shutdown must not close clients while a request is still running."""
    monkeypatch.setattr(DatabaseManager, "health_check", _Awaitable(True))
    monkeypatch.setattr(DatabaseManager, "close", _Awaitable(None))

    async def _close_content_client():
        sink.append(f"in_flight={main_module._in_flight_requests}")

    monkeypatch.setattr(main_module, "close_content_client", _close_content_client)

    app = main_module.create_app()
    async with main_module.lifespan(app):
        main_module._in_flight_requests = 1
        await asyncio.sleep(0.15)
        # The drain loop is polling on this; only release once it has waited.
        main_module._in_flight_requests = 0

    assert sink == ["in_flight=0"]
    assert not [r for r in main_logs if "drain timeout" in r.getMessage()]


@pytest.mark.unit
async def test_lifespan_warns_and_continues_when_the_drain_times_out(
    monkeypatch, sink, main_logs
):
    """main.py:59-64 -- a stuck request must not block shutdown forever.

    The drain bound is patched to 0.01s and one request is held open, so the
    ``asyncio.TimeoutError`` arm is taken for real.
    """
    monkeypatch.setattr(DatabaseManager, "health_check", _Awaitable(True))
    monkeypatch.setattr(DatabaseManager, "close", _Awaitable(None))
    monkeypatch.setattr(main_module, "_MAX_DRAIN_SECONDS", 0.01)

    async def _close_content_client():
        sink.append("closed")

    monkeypatch.setattr(main_module, "close_content_client", _close_content_client)

    app = main_module.create_app()
    async with main_module.lifespan(app):
        # Simulate a request that never finishes.
        main_module._in_flight_requests = 1

    # Despite the timeout, the clients are still closed.
    assert sink == ["closed"]
    warnings = [r.getMessage() for r in main_logs if r.levelname == "WARNING"]
    assert any("Shutdown drain timeout" in m for m in warnings)
    assert any("requests still in flight" in m for m in warnings)

    main_module._in_flight_requests = 0


@pytest.mark.unit
async def test_lifespan_does_not_warn_when_nothing_is_in_flight(monkeypatch, sink, main_logs):
    """A clean shutdown takes the immediate-break path, with no warning."""
    monkeypatch.setattr(DatabaseManager, "health_check", _Awaitable(True))
    monkeypatch.setattr(DatabaseManager, "close", _Awaitable(None))
    monkeypatch.setattr(main_module, "_MAX_DRAIN_SECONDS", 0.01)
    monkeypatch.setattr(main_module, "close_content_client", lambda: _noop(sink))

    app = main_module.create_app()
    async with main_module.lifespan(app):
        main_module._in_flight_requests = 0

    assert sink == [True]
    assert not [r for r in main_logs if "drain timeout" in r.getMessage()]


# ============================= /metrics gating ==============================


def _metrics_gate(monkeypatch, token: str = "the-right-token"):
    """Re-create ``require_metrics_token`` with production settings applied.

    ``require_metrics_token`` is a closure created inside ``create_app``, so it
    is recovered from the registered route's dependency graph.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "METRICS_TOKEN", token)
    app = main_module.create_app()
    route = next(
        r
        for r in app.router.routes
        if getattr(r, "path", None) == "/metrics" and getattr(r, "name", None) == "gated_metrics"
    )
    return route.dependant.dependencies[0].call


@pytest.mark.unit
def test_require_metrics_token_raises_401_without_a_token(monkeypatch):
    """The ``#469`` gate function itself denies an unauthenticated scrape."""
    gate = _metrics_gate(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(gate(None))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Unauthorized"


@pytest.mark.unit
def test_require_metrics_token_raises_401_for_a_wrong_token(monkeypatch):
    """A wrong bearer token is still a 401."""
    gate = _metrics_gate(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(gate("Bearer wrong-token"))
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_require_metrics_token_accepts_the_configured_token(monkeypatch):
    """The exact configured bearer token passes."""
    gate = _metrics_gate(monkeypatch)
    assert asyncio.run(gate("Bearer the-right-token")) is None


@pytest.mark.unit
def test_require_metrics_token_raises_when_no_token_is_configured(monkeypatch):
    """An unset METRICS_TOKEN means production cannot scrape at all."""
    gate = _metrics_gate(monkeypatch, token="")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(gate("Bearer anything"))
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_require_metrics_token_is_inert_outside_production(monkeypatch):
    """Non-production scrapes are unauthenticated by design."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "METRICS_TOKEN", "the-right-token")
    app = main_module.create_app()
    route = next(
        r for r in app.router.routes if getattr(r, "name", None) == "gated_metrics"
    )
    gate = route.dependant.dependencies[0].call
    assert asyncio.run(gate(None)) is None


@pytest.mark.unit
def test_metrics_is_open_outside_production(make_client):
    """Non-production scrapes need no token and return a Prometheus payload."""
    c = make_client(env="development", METRICS_TOKEN="")
    r = c.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert len(r.content) > 0


@pytest.mark.unit
def test_metrics_gate_is_shadowed_by_the_observability_scrape_route(
    make_client, monkeypatch
):
    """GENUINE BUG -- analytics-service/app/main.py:173 and :191.

    ``create_app()`` calls ``wire_observability(app, ...)`` at line 173 without
    ``register_metrics=False``, so an **ungated** ``/metrics`` route is
    registered there. The gated ``gated_metrics`` route is registered
    afterwards at line 191. Starlette matches routes in registration order, so
    the ungated route always wins and the ``#469`` admin-token gate is
    unreachable dead code.

    Consequence: in production ``/metrics`` returns 200 and the full Prometheus
    payload to an unauthenticated caller. This test pins that current
    behaviour so the regression is visible. If ``create_app()`` is fixed to
    pass ``register_metrics=False``, this test fails and the ``require_metrics_token``
    tests above become the end-to-end truth.
    """
    c = make_client(env="production", METRICS_TOKEN="supersecret")
    anonymous = c.get("/metrics")
    wrong = c.get("/metrics", headers={"Authorization": "Bearer wrong"})

    # Both the missing and the wrong token are served the scrape payload.
    assert anonymous.status_code == 200
    assert wrong.status_code == 200
    assert "text/plain" in anonymous.headers["content-type"]
    # The body really is a Prometheus exposition, not an empty stub.
    assert b"python_info" in anonymous.content
    assert b"python_info" in wrong.content


@pytest.mark.unit
def test_gated_metrics_handler_body_works_when_invoked_directly(monkeypatch):
    """The gated handler's own body is correct -- it is simply unreachable.

    ``app/main.py:191-196`` can never be reached over HTTP because of the
    shadowing bug above. Invoking the endpoint function directly is the only
    way to execute it, and it proves the handler itself is fine -- only the
    route ordering is wrong.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "METRICS_TOKEN", "tok")
    app = main_module.create_app()
    route = next(
        r for r in app.router.routes if getattr(r, "name", None) == "gated_metrics"
    )
    response = asyncio.run(route.endpoint())

    assert response.status_code == 200
    assert "text/plain" in response.media_type
    assert b"python_info" in response.body


# ========================== request body size cap ===========================


@pytest.mark.unit
def test_oversized_body_is_rejected_with_413(make_client):
    """main.py:165-168 -- content-length above 1 MiB is refused."""
    c = make_client()
    r = c.get("/health", headers={"content-length": "1048577"})
    assert r.status_code == 413
    assert r.json()["detail"] == "Request body too large"


@pytest.mark.unit
def test_body_at_the_cap_is_allowed(make_client):
    """The cap is inclusive of exactly 1 MiB."""
    c = make_client()
    r = c.get("/health", headers={"content-length": "1048576"})
    assert r.status_code == 200


@pytest.mark.unit
def test_unparseable_content_length_is_ignored(make_client):
    """main.py:169-170 -- a non-numeric content-length must not 500."""
    c = make_client()
    r = c.get("/health", headers={"content-length": "banana"})
    assert r.status_code == 200


@pytest.mark.unit
def test_missing_content_length_is_ignored(make_client):
    """main.py:161 -- GET without content-length takes the passthrough path."""
    c = make_client()
    r = c.get("/health")
    assert r.status_code == 200


# =========================== opaque 500 handler ============================


@pytest.mark.unit
def test_unhandled_exception_returns_opaque_500_with_a_correlation_id(make_client):
    """#557/#466 -- internals must never leak, but the corr id must."""
    c = make_client()

    @c.app.get("/_boom")
    async def _boom():
        raise RuntimeError("postgres://user:hunter2@db/prod")

    r = c.get("/_boom", headers={"X-Correlation-ID": "corr-abc-123"})

    assert r.status_code == 500
    body = r.json()
    assert body["status_code"] == 500
    assert body["message"] == "Internal server error"
    assert "hunter2" not in r.text
    # The correlation id is echoed so an operator can trace the failure.
    assert isinstance(body["correlation_id"], str)
    assert body["correlation_id"]


# ================================ CORS wiring ===============================


@pytest.mark.unit
def test_cors_origin_is_taken_from_settings(make_client):
    """CORS is configured from settings, not a hardcoded list."""
    c = make_client(
        CORS_ALLOWED_ORIGINS=["https://allowed.example"], CORS_ALLOW_CREDENTIALS=True
    )
    r = c.get("/health", headers={"Origin": "https://allowed.example"})
    assert r.headers["access-control-allow-origin"] == "https://allowed.example"


@pytest.mark.unit
def test_cors_credentials_can_be_disabled(make_client):
    """The credential flag is honoured in both directions."""
    c = make_client(
        CORS_ALLOWED_ORIGINS=["https://allowed.example"], CORS_ALLOW_CREDENTIALS=False
    )
    r = c.get("/health", headers={"Origin": "https://allowed.example"})
    assert "access-control-allow-credentials" not in r.headers


@pytest.mark.unit
def test_cors_preflight_allows_configured_methods(make_client):
    """A preflight for the configured origin is answered, not rejected."""
    c = make_client(CORS_ALLOWED_ORIGINS=["https://allowed.example"])
    r = c.options(
        "/health",
        headers={
            "Origin": "https://allowed.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://allowed.example"


# ============================ in-flight bookkeeping =========================


@pytest.mark.unit
def test_in_flight_counter_returns_to_zero_after_a_request(make_client):
    """The middleware must not leak its counter across requests."""
    c = make_client()
    for _ in range(3):
        assert c.get("/health").status_code == 200
    assert main_module._in_flight_requests == 0


@pytest.mark.unit
def test_in_flight_counter_returns_to_zero_after_a_500(make_client):
    """The ``finally`` arm must release the counter even when the route blows up."""
    c = make_client()

    @c.app.get("/_boom")
    async def _boom():
        raise RuntimeError("kaboom")

    assert c.get("/_boom").status_code == 500
    assert main_module._in_flight_requests == 0


# ============================== observability ===============================


@pytest.mark.unit
def test_observability_middleware_is_installed(make_client):
    """wire_observability contributes correlation, logging and metrics layers."""
    from wildframe_observability.metrics import MetricsMiddleware
    from wildframe_observability.middleware import (
        CorrelationMiddleware,
        RequestLoggingMiddleware,
    )

    c = make_client()
    installed = {m.cls for m in c.app.user_middleware}
    assert CorrelationMiddleware in installed
    assert RequestLoggingMiddleware in installed
    assert MetricsMiddleware in installed


@pytest.mark.unit
def test_correlation_id_is_propagated_into_the_response(make_client):
    """CorrelationMiddleware must forward X-Correlation-ID."""
    c = make_client()
    r = c.get("/health", headers={"X-Correlation-ID": "corr-round-trip"})
    assert r.status_code == 200
    assert r.headers.get("X-Correlation-ID") == "corr-round-trip"


@pytest.mark.unit
def test_request_id_is_propagated_into_the_response(make_client):
    """CorrelationMiddleware must forward X-Request-ID."""
    c = make_client()
    r = c.get("/health", headers={"X-Request-ID": "req-round-trip"})
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID") == "req-round-trip"


# ============================ import sanity =================================


@pytest.mark.unit
def test_content_client_error_type_is_importable():
    """The lifespan teardown target module exposes its error type."""
    assert issubclass(ContentServiceUnavailableError, Exception)
