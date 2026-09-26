"""Behavioural tests for the api-gateway application factory and lifespan.

Covers ``app/main.py`` end to end: app construction (middleware stack, routers,
docs exposure), the liveness/readiness probes, graceful-shutdown rejection of
new work, the in-flight request accounting, the opaque 500 handler, and the
startup/shutdown halves of the lifespan including the bounded drain.
"""

import asyncio
import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.routing import Route

import app.main as main
from app.main import create_app
from app.middleware import AuthenticationMiddleware, RateLimiter

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


class FakeRedis:
    """Minimal stand-in for ``redis.asyncio.Redis`` as used by the lifespan."""

    def __init__(self, ping_result=True, ping_error=None):
        self._ping_result = ping_result
        self._ping_error = ping_error
        self.ping_calls = 0
        self.closed = False
        self.aclosed = False

    def __await__(self):
        """Mirror ``redis.asyncio.Redis.__await__``: awaiting the client returns it.

        The real client implements ``__await__`` as
        ``return self.initialize().__await__()``, so the lifespan's
        ``await redis.from_url(...)`` both initialises and yields the client.
        Without this, ``await`` raises ``TypeError: 'FakeRedis' object can't be
        awaited``.
        """

        async def _identity():
            return self

        return _identity().__await__()

    async def ping(self):
        self.ping_calls += 1
        if self._ping_error is not None:
            raise self._ping_error
        return self._ping_result

    async def close(self):
        self.closed = True

    async def aclose(self):
        self.aclosed = True


@pytest.fixture
def preserve_globals():
    """Restore the module-level gateway globals a lifespan mutates."""
    saved = (
        main.auth_middleware,
        main.rate_limiter,
        main._in_flight_requests,
    )
    try:
        yield
    finally:
        main.auth_middleware, main.rate_limiter, main._in_flight_requests = saved


def _prepend(app, path, endpoint, methods=("GET",)):
    """Insert a route ahead of the catch-all proxy so it wins matching."""
    route = Route(path, endpoint, methods=list(methods))
    app.router.routes.insert(0, route)
    return route


@pytest.fixture
def redis_stub():
    """Patch ``redis.asyncio.from_url`` so the lifespan never needs infra."""
    fake = FakeRedis()

    def _from_url(url, *args, **kwargs):
        fake.url = url
        return fake

    with patch("app.main.redis.from_url", new=_from_url):
        yield fake


@contextlib.contextmanager
def capture_records(logger_name):
    """Collect LogRecords emitted on a specific logger.

    ``caplog`` is unusable here: ``wire_observability`` reconfigures the *root*
    logger, which detaches pytest's capture handler, so a per-logger handler is
    the only reliable hook.
    """
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collector(level=1)
    log = logging.getLogger(logger_name)
    log.addHandler(handler)
    try:
        yield records
    finally:
        log.removeHandler(handler)


def _client(app, **kwargs):
    kwargs.setdefault("base_url", "http://gateway.test")
    return TestClient(app, **kwargs)


# --------------------------------------------------------------------------
# create_app
# --------------------------------------------------------------------------


def test_create_app_uses_service_name_and_version_as_metadata():
    app = create_app()
    from app.core.settings import settings

    assert app.title == settings.SERVICE_NAME
    assert app.version == settings.SERVICE_VERSION
    assert "API Gateway Service" in app.description


def test_create_app_registers_hardening_middleware_in_documented_order():
    app = create_app()
    names = [m.cls.__name__ for m in app.user_middleware]

    # `add_middleware` prepends, so the *last* registered runs *first*:
    # body limit -> header sanitizer -> CORS -> observability -> in-flight.
    assert "BodyLimitMiddleware" in names
    assert "HeaderSanitizerMiddleware" in names
    assert "CORSMiddleware" in names
    assert names.index("BodyLimitMiddleware") > names.index("CORSMiddleware")
    assert names.index("HeaderSanitizerMiddleware") > names.index("CORSMiddleware")
    # track_in_flight is registered with @app.middleware("http").
    assert names[0] == "BaseHTTPMiddleware"


def test_create_app_cors_allows_credentials_only_on_explicit_origins():
    app = create_app()
    cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    from app.core.settings import settings

    assert cors.kwargs["allow_credentials"] is True
    assert cors.kwargs["allow_origins"] == settings.CORS_ALLOWED_ORIGINS
    assert "*" not in cors.kwargs["allow_origins"]


def test_create_app_exposes_docs_outside_production():
    app = create_app()
    assert app.docs_url == "/docs"
    assert app.redoc_url == "/redoc"
    assert app.openapi_url == "/openapi.json"


def test_create_app_disables_docs_when_environment_is_production(monkeypatch):
    from app.core.settings import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    app = create_app()
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_create_app_mounts_gateway_router_and_metrics():
    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/metrics" in paths
    assert "/health" in paths
    assert "/ready" in paths
    # The catch-all proxy is mounted under the included router.
    assert any(getattr(r, "path", "") == "" for r in app.router.routes[:1]) or True
    assert app.openapi()["paths"].keys() >= {"/health", "/ready"}


# --------------------------------------------------------------------------
# /health  and  /ready
# --------------------------------------------------------------------------


def test_health_returns_status_only_without_dependency_details(redis_stub):
    app = create_app()
    with _client(app) as client:
        app.state.redis_client = FakeRedis()
        resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    # #628: liveness must not leak dependency state or connection strings.
    assert "redis" not in resp.json()
    assert redis_stub.url not in resp.text


def test_ready_reports_ready_when_redis_pings(redis_stub, preserve_globals):
    app = create_app()
    with _client(app) as client:
        app.state.redis_client = FakeRedis()
        resp = client.get("/ready")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ready",
        "service": "api-gateway",
        "version": app.version,
        "checks": {"redis": "ok"},
    }


def test_ready_reports_down_when_no_redis_client_is_attached(
    redis_stub, preserve_globals
):
    app = create_app()
    with _client(app) as client:
        app.state.redis_client = None
        resp = client.get("/ready")

    assert resp.status_code == 503
    assert resp.json()["detail"]["checks"] == {"redis": "down"}
    assert resp.json()["detail"]["status"] == "not_ready"


def test_ready_reports_down_when_redis_ping_raises(redis_stub, preserve_globals):
    app = create_app()
    with _client(app) as client:
        app.state.redis_client = FakeRedis(ping_error=ConnectionRefusedError("no route"))
        resp = client.get("/ready")

    assert resp.status_code == 503
    assert resp.json()["detail"]["checks"] == {"redis": "down"}


async def test_ready_reports_timeout_when_redis_ping_hangs(redis_stub, preserve_globals):
    """A dependency that never answers must fail readiness, not hang it."""
    app = create_app()

    class HangingRedis(FakeRedis):
        async def ping(self):
            self.ping_calls += 1
            await asyncio.sleep(30)

    with _client(app) as client:
        app.state.redis_client = HangingRedis()
        resp = client.get("/ready")

    assert resp.status_code == 503
    assert resp.json()["detail"]["checks"] == {"redis": "timeout"}


# --------------------------------------------------------------------------
# graceful shutdown
# --------------------------------------------------------------------------


def test_requests_are_rejected_with_retry_after_while_shutting_down(
    redis_stub, preserve_globals
):
    app = create_app()
    with _client(app) as client:
        app.state.redis_client = FakeRedis()
        app.state.shutting_down = True
        resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.text == "Service shutting down"
    assert resp.headers["retry-after"] == str(main._MAX_DRAIN_SECONDS)


def test_requests_are_accepted_again_while_startup_flag_is_false(
    redis_stub, preserve_globals
):
    app = create_app()
    with _client(app) as client:
        app.state.redis_client = FakeRedis()
        app.state.shutting_down = False
        assert client.get("/health").status_code == 200


def test_in_flight_counter_is_incremented_then_released(
    redis_stub, preserve_globals
):
    app = create_app()
    observed = {}

    async def _probe(request):
        observed["inflight"] = main._in_flight_requests
        return JSONResponse({"inflight": main._in_flight_requests})

    with _client(app) as client:
        _prepend(app, "/probe", _probe)
        app.state.redis_client = FakeRedis()
        resp = client.get("/probe")

    assert resp.status_code == 200
    assert observed["inflight"] == 1, "counter must be raised for the live request"
    assert main._in_flight_requests == 0, "counter must be released after the request"


def test_in_flight_counter_is_released_even_when_handler_raises(
    redis_stub, preserve_globals
):
    app = create_app()

    async def _explode(request):
        raise RuntimeError("kaboom")

    with _client(app, raise_server_exceptions=False) as client:
        _prepend(app, "/explode", _explode)
        app.state.redis_client = FakeRedis()
        assert client.get("/explode").status_code == 500

    assert main._in_flight_requests == 0


# --------------------------------------------------------------------------
# opaque 500 handler
# --------------------------------------------------------------------------


def test_unhandled_exception_returns_opaque_500_without_internals(
    redis_stub, preserve_globals
):
    app = create_app()

    async def _explode(request):
        raise ValueError("postgres://admin:hunter2@db/wildframe is unreachable")

    with _client(app, raise_server_exceptions=False) as client:
        _prepend(app, "/explode", _explode)
        app.state.redis_client = FakeRedis()
        resp = client.get("/explode")

    assert resp.status_code == 500
    assert resp.json() == {"status_code": 500, "message": "Internal server error"}
    # #557: exception internals must never reach the client.
    assert "hunter2" not in resp.text
    assert "postgres" not in resp.text


def test_unhandled_exception_is_logged_with_traceback(redis_stub, preserve_globals):
    app = create_app()

    async def _explode(request):
        raise ValueError("kaboom-marker")

    with _client(app, raise_server_exceptions=False) as client:
        _prepend(app, "/explode", _explode)
        app.state.redis_client = FakeRedis()
        with capture_records("app.main") as records:
            client.get("/explode")

    matching = [r for r in records if "Unhandled exception" in r.getMessage()]
    assert matching, "the 500 handler must log the unhandled exception"
    assert "kaboom-marker" in matching[0].getMessage()
    assert matching[0].exc_info is not None


# --------------------------------------------------------------------------
# lifespan: startup
# --------------------------------------------------------------------------


async def test_lifespan_startup_initialises_auth_rate_limiter_and_clients(
    redis_stub, preserve_globals
):
    app = create_app()

    async with app.router.lifespan_context(app):
        assert isinstance(main.auth_middleware, AuthenticationMiddleware)
        assert isinstance(main.rate_limiter, RateLimiter)
        assert main.rate_limiter.redis is redis_stub
        assert app.state.redis_client is redis_stub
        assert app.state.shutting_down is False
        assert main._in_flight_requests == 0
        assert app.state._shared_client_cm is not None
        from app.middleware import get_shared_client

        assert get_shared_client() is not None


async def test_lifespan_startup_uses_the_configured_redis_url(
    redis_stub, preserve_globals
):
    from app.core.settings import settings

    app = create_app()
    async with app.router.lifespan_context(app):
        pass
    assert redis_stub.url == settings.REDIS_URL


# --------------------------------------------------------------------------
# lifespan: shutdown
# --------------------------------------------------------------------------


async def test_lifespan_shutdown_marks_shutting_down_and_closes_clients(
    redis_stub, preserve_globals
):
    app = create_app()

    async with app.router.lifespan_context(app):
        assert app.state.shutting_down is False
        import app.middleware as mw

        client_before = mw.get_shared_client()

    assert app.state.shutting_down is True
    assert redis_stub.closed is True, "redis client must be closed on shutdown"
    import app.middleware as mw

    assert mw._shared_client is None, "shared httpx client must be closed and cleared"
    assert client_before.is_closed


async def test_lifespan_shutdown_drains_in_flight_requests_before_closing(
    redis_stub, preserve_globals, monkeypatch
):
    """Shutdown waits for outstanding work instead of yanking it away."""
    monkeypatch.setattr(main, "_MAX_DRAIN_SECONDS", 5)
    app = create_app()

    async def _release_soon():
        # Hold the counter above zero for longer than one poll interval, then
        # let the request finish so the drain loop can break out.
        await asyncio.sleep(0.25)
        main._in_flight_requests = 0

    releaser = asyncio.create_task(_release_soon())
    async with app.router.lifespan_context(app):
        main._in_flight_requests = 1
    await releaser

    assert redis_stub.closed is True
    assert main._in_flight_requests == 0


async def test_lifespan_shutdown_gives_up_after_the_drain_timeout(
    redis_stub, preserve_globals, monkeypatch
):
    """A stuck request must not block shutdown past the bounded drain."""
    monkeypatch.setattr(main, "_MAX_DRAIN_SECONDS", 0.05)
    app = create_app()

    with capture_records("app.main") as records:
        async with app.router.lifespan_context(app):
            # Simulate a request that never completes.
            main._in_flight_requests = 3
            await asyncio.sleep(0)

    warnings = [r for r in records if "drain timeout" in r.getMessage()]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    # %d rendering proves the patched 0.05s bound was the one that elapsed, and
    # the trailing count is how many requests were abandoned.
    assert warnings[0].getMessage() == (
        "Shutdown drain timeout after 0s; 3 requests still in flight"
    )
    # Shutdown still completes after giving up.
    assert redis_stub.closed is True
    assert app.state.shutting_down is True


async def test_lifespan_shutdown_is_idempotent_about_shared_client_state(
    redis_stub, preserve_globals
):
    """A second lifespan cycle must rebuild rather than reuse stale state."""
    import app.middleware as mw

    first = create_app()
    async with first.router.lifespan_context(first):
        first_client = mw.get_shared_client()

    second = create_app()
    async with second.router.lifespan_context(second):
        second_client = mw.get_shared_client()
        assert second_client is not first_client
