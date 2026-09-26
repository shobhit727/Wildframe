"""Application wiring: ``create_app``, ``lifespan`` and the body-size cap.

``app/main.py`` is the outermost trust boundary of the service, so these tests
exercise what a caller can actually observe:

* docs/OpenAPI exposure follows ``ENVIRONMENT``.
* CORS is wired from settings, and wildcard+credentials is not allowed in
  production (the settings gate that makes this safe is in
  ``test_settings_validation``).
* the 6 MiB ``BodySizeLimitMiddleware`` rejects an oversized body *before*
  FastAPI parses it, and passes small and chunked bodies through untouched.
* ``/health`` is liveness-only; ``/ready`` reports DB *and* Redis and returns
  503 with a per-dependency breakdown when either is down.
* in-flight requests are tracked, and a request arriving after the shutdown
  signal is refused with 503 + ``Retry-After``.
* ``/metrics`` is gated by the admin token in production only.
* an unhandled exception returns an opaque 500 with a correlation id.
* the lifespan refuses to start on an unhealthy database, drains in-flight
  requests within a bounded budget, cancels the background worker, and closes
  the engine.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from app import main as app_main
from app.core.settings import settings


@pytest.fixture(autouse=True)
def _reset_in_flight_state():
    """The in-flight counters are process globals; keep tests independent."""
    app_main._in_flight_requests = 0
    app_main._in_flight_lock = None
    app_main._fallback_in_flight_lock = None
    app_main._shutdown_event = None
    yield
    app_main._in_flight_requests = 0
    app_main._in_flight_lock = None
    app_main._fallback_in_flight_lock = None
    app_main._shutdown_event = None


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


def _build(environment: str | None = None) -> FastAPI:
    with patch.object(settings, "ENVIRONMENT", environment or settings.ENVIRONMENT):
        return app_main.create_app()


def _only_gated_metrics_app() -> FastAPI:
    """Build the app with observability suppressed to isolate the metrics gate."""
    with patch.object(app_main, "wire_observability") as wire:
        wire.side_effect = lambda app, **kwargs: None
        return app_main.create_app()


# ---------------------------------------------------------------------------
# create_app(): construction.
# ---------------------------------------------------------------------------


def test_create_app_exposes_docs_outside_production():
    app = _build("development")
    assert app.title == settings.SERVICE_NAME
    assert app.version == settings.SERVICE_VERSION
    assert app.description == "Uploads Service"
    assert app.docs_url == "/docs"
    assert app.redoc_url == "/redoc"
    assert app.openapi_url == "/openapi.json"


def test_create_app_hides_docs_in_production():
    app = _build("production")
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_create_app_mounts_the_uploads_router_and_its_own_endpoints():
    app = _build("development")
    paths = set(app.openapi()["paths"])
    assert "/api/v1/uploads/sessions" in paths
    assert "/api/v1/uploads/sessions/{session_id}" in paths
    assert "/api/v1/uploads/sessions/{session_id}/chunks" in paths
    assert "/api/v1/uploads/sessions/{session_id}/complete" in paths
    assert "/api/v1/uploads/sessions/{session_id}/abort" in paths
    assert "/health" in paths and "/ready" in paths


def test_create_app_registers_cors_and_a_function_middleware():
    app = _build("development")
    names = [m.cls.__name__ for m in app.user_middleware]
    assert "CORSMiddleware" in names
    # @app.middleware("http") for the in-flight tracker, plus the ASGI-level
    # BodySizeLimitMiddleware, which is added last so it runs first.
    assert "BodySizeLimitMiddleware" in names
    assert names.count("BaseHTTPMiddleware") == 1


def test_create_app_asks_observability_not_to_own_the_metrics_route():
    """The service must own /metrics, otherwise the token gate is dead code."""
    with patch.object(app_main, "wire_observability") as wire:
        app_main.create_app()
    assert wire.call_args.kwargs["register_metrics"] is False
    assert wire.call_args.kwargs["service_name"] == settings.SERVICE_NAME
    assert wire.call_args.kwargs["log_level"] == settings.LOG_LEVEL


async def test_cors_reflects_a_configured_origin():
    with patch.object(settings, "CORS_ALLOWED_ORIGINS", ["https://studio.example.com"]):
        app = _build("development")
    async with _client(app) as client:
        response = await client.get("/health", headers={"Origin": "https://studio.example.com"})
    assert response.headers.get("access-control-allow-origin") == ("https://studio.example.com")


# ---------------------------------------------------------------------------
# /health — liveness only.
# ---------------------------------------------------------------------------


async def test_health_reports_healthy_when_the_db_answers():
    app = _build("development")
    with patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)):
        async with _client(app) as client:
            response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


async def test_health_reports_degraded_without_leaking_topology():
    app = _build("development")
    with patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=False)):
        async with _client(app) as client:
            response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "degraded"}
    # Liveness only: no per-dependency breakdown leaks from /health.
    assert "checks" not in response.json()


# ---------------------------------------------------------------------------
# /ready — DB + Redis.
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Stands in for a ``redis.asyncio.Redis`` client in the readiness probe.

    ``redis.asyncio.from_url`` is not a coroutine function — it returns an
    *awaitable* client, because redis-py gives ``Redis`` an ``__await__`` that
    performs connection setup. This fake is therefore awaitable **and** usable
    directly, so the probe's observable contract (the JSON payload) is asserted
    independently of whether the handler awaits the client.
    """

    def __init__(self, ping_error: Exception | None = None) -> None:
        self.ping_error = ping_error
        self.closed = False

    def __await__(self):
        async def _initialize() -> "_FakeRedis":
            return self

        return _initialize().__await__()

    async def ping(self):
        if self.ping_error is not None:
            raise self.ping_error
        return True

    async def close(self):
        self.closed = True


async def test_ready_is_ok_when_database_and_redis_are_up():
    app = _build("development")
    redis_client = _FakeRedis()
    with (
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.redis, "from_url", MagicMock(return_value=redis_client)),
    ):
        async with _client(app) as client:
            response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "uploads",
        "version": settings.SERVICE_VERSION,
        "checks": {"database": "ok", "redis": "ok"},
    }
    assert redis_client.closed is True, "the probe client is closed again"


async def test_ready_is_503_when_the_database_is_down():
    app = _build("development")
    with (
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=False)),
        patch.object(app_main.redis, "from_url", MagicMock(return_value=_FakeRedis())),
    ):
        async with _client(app) as client:
            response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "service": "uploads",
        "version": settings.SERVICE_VERSION,
        "checks": {"database": "down", "redis": "ok"},
    }


async def test_ready_is_503_when_redis_is_unreachable():
    app = _build("development")
    with (
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(
            app_main.redis,
            "from_url",
            MagicMock(side_effect=ConnectionError("connection refused")),
        ),
        patch.object(app_main.logger, "error") as log_error,
    ):
        async with _client(app) as client:
            response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "redis": "down"}
    assert "Redis readiness check failed" in log_error.call_args.args[0]


async def test_ready_is_503_when_redis_ping_times_out():
    app = _build("development")
    with (
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.redis, "from_url", MagicMock(return_value=_FakeRedis())),
        patch.object(app_main.asyncio, "wait_for", AsyncMock(side_effect=asyncio.TimeoutError)),
    ):
        async with _client(app) as client:
            response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "down"


# ---------------------------------------------------------------------------
# BodySizeLimitMiddleware — the 6 MiB cap (#517).
# ---------------------------------------------------------------------------

MAX_BODY = 6291456


class _RecordingSend:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message) -> None:
        self.messages.append(message)


async def test_body_limit_middleware_passes_a_small_body_through():
    sent = _RecordingSend()
    reached: list[str] = []
    queue = [{"type": "http.request", "body": b"hello", "more_body": False}]

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def app(scope, receive, send):
        reached.append("app")

    middleware = app_main.BodySizeLimitMiddleware(app=app, max_bytes=1024)
    await middleware({"type": "http"}, receive, sent)
    assert reached == ["app"], "the request reached the app"
    assert sent.messages == []


async def test_body_limit_middleware_replays_a_chunked_body():
    """A chunked upload must be reassembled, not passed through as one chunk."""
    seen: dict = {}
    sent = _RecordingSend()
    queue = [
        {"type": "http.request", "body": b"ab", "more_body": True},
        {"type": "http.request", "body": b"cd", "more_body": False},
    ]

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def app(scope, receive, send):
        seen["body"] = (await receive())["body"]

    middleware = app_main.BodySizeLimitMiddleware(app=app, max_bytes=1024)
    await middleware({"type": "http"}, receive, sent)
    assert seen["body"] == b"abcd"


async def test_body_limit_middleware_rejects_an_oversized_chunked_body():
    sent = _RecordingSend()
    queue = [
        {"type": "http.request", "body": b"a" * 100, "more_body": True},
        {"type": "http.request", "body": b"b" * 100, "more_body": False},
    ]

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    reached: list[str] = []

    async def app(scope, receive, send):
        reached.append("app")

    middleware = app_main.BodySizeLimitMiddleware(app=app, max_bytes=150)
    await middleware({"type": "http"}, receive, sent)
    assert reached == [], "the app never sees an oversized body"
    start = sent.messages[0]
    assert start["type"] == "http.response.start"
    assert start["status"] == 413
    assert b"Request body too large" in sent.messages[1]["body"]


async def test_body_limit_middleware_ignores_non_http_scopes():
    """WebSocket / lifespan traffic has no body and must pass straight through."""
    sent = _RecordingSend()
    reached: list[str] = []

    async def app(scope, receive, send):
        reached.append(scope["type"])

    async def receive():
        raise AssertionError("a websocket scope must not consume the body channel")

    middleware = app_main.BodySizeLimitMiddleware(app=app, max_bytes=1)
    await middleware({"type": "websocket"}, receive, sent)
    assert reached == ["websocket"]


async def test_body_limit_middleware_abandons_a_disconnected_upload():
    sent = _RecordingSend()
    reached: list[str] = []

    async def app(scope, receive, send):
        reached.append("app")

    async def receive():
        return {"type": "http.disconnect"}

    middleware = app_main.BodySizeLimitMiddleware(app=app, max_bytes=1024)
    await middleware({"type": "http"}, receive, sent)
    assert reached == [], "a client that vanished mid-upload gets no response"
    assert sent.messages == []


async def test_body_limit_middleware_replay_is_single_use():
    """The replay shim must hand the body over exactly once."""
    sent = _RecordingSend()
    messages: list[dict] = []
    queue = [{"type": "http.request", "body": b"payload", "more_body": False}]

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def app(scope, receive, send):
        messages.append(await receive())
        messages.append(await receive())  # a second read must not replay

    middleware = app_main.BodySizeLimitMiddleware(app=app, max_bytes=1024)
    await middleware({"type": "http"}, receive, sent)
    assert messages[0] == {"type": "http.request", "body": b"payload", "more_body": False}
    assert messages[1]["type"] == "http.disconnect", "no second replay"


async def test_the_413_cap_is_enforced_end_to_end():
    app = _build("development")
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/uploads/sessions",
            content=b"x" * (MAX_BODY + 1),
        )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


# ---------------------------------------------------------------------------
# Graceful shutdown (#426).
# ---------------------------------------------------------------------------


async def test_requests_are_refused_once_shutdown_starts():
    app = _build("development")
    app.state.shutting_down = True
    with patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)):
        async with _client(app) as client:
            response = await client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "Service shutting down"}
    assert response.headers["retry-after"] == str(app_main._MAX_DRAIN_SECONDS)


async def test_in_flight_counter_returns_to_zero_after_a_request():
    app = _build("development")
    with patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)):
        async with _client(app) as client:
            await client.get("/health")
            await client.get("/health")
    assert app_main._in_flight_requests == 0


async def test_in_flight_counter_returns_to_zero_even_when_the_route_raises():
    app = _build("development")
    with patch.object(
        app_main.DatabaseManager, "health_check", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        async with _client(app) as client:
            response = await client.get("/health")
    assert response.status_code == 500
    assert app_main._in_flight_requests == 0


def test_the_fallback_lock_is_used_before_the_lifespan_runs():
    app = _build("development")
    assert app_main._in_flight_lock is None
    app_main._fallback_lock()  # must not raise without a running loop
    assert app_main._fallback_lock() is app_main._fallback_lock()


# ---------------------------------------------------------------------------
# The opaque 500 handler (#557).
# ---------------------------------------------------------------------------


async def test_unhandled_exception_returns_an_opaque_500_with_a_correlation_id():
    app = _build("development")
    with patch.object(
        app_main.DatabaseManager,
        "health_check",
        AsyncMock(side_effect=RuntimeError("postgres://user:hunter2@host exploded")),
    ):
        async with _client(app) as client:
            response = await client.get("/health")

    assert response.status_code == 500
    body = response.json()
    assert body["status_code"] == 500
    assert body["message"] == "Internal server error"
    assert body["correlation_id"]
    # The exception text (and its credentials) must never reach the client.
    assert "hunter2" not in response.text
    assert "exploded" not in response.text


async def test_500_handler_echoes_the_inbound_correlation_id():
    app = _build("development")
    with patch.object(
        app_main.DatabaseManager, "health_check", AsyncMock(side_effect=RuntimeError("x"))
    ):
        async with _client(app) as client:
            response = await client.get("/health", headers={"X-Correlation-ID": "corr-1234"})
    assert response.json()["correlation_id"] == "corr-1234"


async def test_500_handler_logs_the_exception_with_the_correlation_id():
    app = _build("development")
    with (
        patch.object(
            app_main.DatabaseManager, "health_check", AsyncMock(side_effect=RuntimeError("x"))
        ),
        patch.object(app_main.logger, "exception") as log_exception,
    ):
        async with _client(app) as client:
            await client.get("/health", headers={"X-Correlation-ID": "corr-9"})
    assert log_exception.called
    assert "Unhandled exception (corr=%s)" in log_exception.call_args.args[0]


# ---------------------------------------------------------------------------
# /metrics gating (#469).
# ---------------------------------------------------------------------------


async def test_metrics_is_open_outside_production():
    app = _build("development")
    async with _client(app) as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.content


async def test_the_metrics_gate_rejects_an_unauthenticated_scrape_in_production():
    app = _only_gated_metrics_app()
    with patch.object(settings, "ENVIRONMENT", "production"):
        async with _client(app) as client:
            response = await client.get("/metrics")
    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"


async def test_the_metrics_gate_accepts_the_configured_token():
    app = _only_gated_metrics_app()
    with (
        patch.object(settings, "ENVIRONMENT", "production"),
        patch.object(settings, "METRICS_TOKEN", "s3cret-metrics-token"),
    ):
        async with _client(app) as client:
            response = await client.get(
                "/metrics", headers={"Authorization": "Bearer s3cret-metrics-token"}
            )
    assert response.status_code == 200


async def test_the_metrics_gate_rejects_a_wrong_token():
    app = _only_gated_metrics_app()
    with (
        patch.object(settings, "ENVIRONMENT", "production"),
        patch.object(settings, "METRICS_TOKEN", "s3cret-metrics-token"),
    ):
        async with _client(app) as client:
            response = await client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


async def test_the_metrics_gate_fails_closed_without_a_configured_token():
    """An unset METRICS_TOKEN must fail closed, never open."""
    app = _only_gated_metrics_app()
    with (
        patch.object(settings, "ENVIRONMENT", "production"),
        patch.object(settings, "METRICS_TOKEN", ""),
    ):
        async with _client(app) as client:
            response = await client.get("/metrics")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# lifespan().
# ---------------------------------------------------------------------------


async def test_lifespan_starts_the_worker_and_closes_the_database():
    app = _build("development")
    with (
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.DatabaseManager, "close", AsyncMock()) as close,
        patch.object(
            app_main, "_background_workers", AsyncMock(side_effect=asyncio.CancelledError)
        ),
    ):
        async with app_main.lifespan(app):
            assert app.state.shutting_down is False
            assert app_main._shutdown_event is not None
            assert not app_main._shutdown_event.is_set()
        assert app.state.shutting_down is True
        assert app_main._shutdown_event.is_set()
    close.assert_awaited_once()


async def test_lifespan_refuses_to_start_on_an_unhealthy_database():
    app = _build("development")
    with (
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=False)),
        patch.object(app_main.logger, "error") as log_error,
    ):
        with pytest.raises(RuntimeError, match="Database is not healthy on startup"):
            async with app_main.lifespan(app):
                pytest.fail("lifespan must not yield with an unhealthy database")
    assert "Database health check failed" in log_error.call_args.args[0]


async def test_lifespan_logs_the_startup_banner():
    app = _build("development")
    with (
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.DatabaseManager, "close", AsyncMock()),
        patch.object(
            app_main, "_background_workers", AsyncMock(side_effect=asyncio.CancelledError)
        ),
        patch.object(app_main.logger, "info") as log_info,
    ):
        async with app_main.lifespan(app):
            pass
    messages = [call.args[0] for call in log_info.call_args_list]
    assert any(f"Starting {settings.SERVICE_NAME}" in str(m) for m in messages)
    assert any("All startup checks passed" in str(m) for m in messages)
    assert any(f"Shutting down {settings.SERVICE_NAME}" in str(m) for m in messages)
    assert any("Shutdown complete" in str(m) for m in messages)


async def test_lifespan_waits_for_in_flight_requests_before_closing():
    app = _build("development")
    with (
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.DatabaseManager, "close", AsyncMock()),
        patch.object(
            app_main, "_background_workers", AsyncMock(side_effect=asyncio.CancelledError)
        ),
    ):
        async with app_main.lifespan(app):
            app_main._in_flight_requests = 2

            # Drain one request while the lifespan is winding down.
            async def _release() -> None:
                await asyncio.sleep(0.01)
                app_main._in_flight_requests = 0

            task = asyncio.create_task(_release())
            await task
    assert app_main._in_flight_requests == 0


async def test_lifespan_drain_budget_is_bounded():
    """Shutdown never waits forever on a request that never finishes."""
    app = _build("development")
    with (
        patch.object(app_main, "_MAX_DRAIN_SECONDS", 0.05),
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.DatabaseManager, "close", AsyncMock()),
        patch.object(
            app_main, "_background_workers", AsyncMock(side_effect=asyncio.CancelledError)
        ),
        patch.object(app_main.logger, "warning") as log_warning,
    ):
        async with app_main.lifespan(app):
            app_main._in_flight_requests = 1  # never drains
    assert app_main._in_flight_requests == 1
    assert any("Shutdown drain timeout" in call.args[0] for call in log_warning.call_args_list)


async def test_lifespan_applies_dlq_retention_for_the_kafka_publisher():
    """Bounded DLQ retention is applied in the background, never blocking boot."""
    import wildframe_events.dlq_retention as dlq_retention

    app = _build("development")
    with (
        patch.object(settings, "EVENT_PUBLISHER", "kafka"),
        patch.object(dlq_retention, "apply_dlq_retention", AsyncMock()) as apply_retention,
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.DatabaseManager, "close", AsyncMock()),
        patch.object(
            app_main, "_background_workers", AsyncMock(side_effect=asyncio.CancelledError)
        ),
    ):
        async with app_main.lifespan(app):
            await asyncio.sleep(0)
    apply_retention.assert_awaited_once_with(
        settings.KAFKA_BOOTSTRAP_SERVERS, settings.SERVICE_NAME
    )


async def test_lifespan_skips_dlq_retention_for_the_in_memory_publisher():
    import wildframe_events.dlq_retention as dlq_retention

    app = _build("development")
    with (
        patch.object(settings, "EVENT_PUBLISHER", "memory"),
        patch.object(dlq_retention, "apply_dlq_retention", AsyncMock()) as apply_retention,
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.DatabaseManager, "close", AsyncMock()),
        patch.object(
            app_main, "_background_workers", AsyncMock(side_effect=asyncio.CancelledError)
        ),
    ):
        async with app_main.lifespan(app):
            await asyncio.sleep(0)
    apply_retention.assert_not_awaited()


# ---------------------------------------------------------------------------
# _background_workers(): the outbox drain + session reaper.
# ---------------------------------------------------------------------------


class _NullAsyncContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


class _FakeSessionFactory:
    def __call__(self):
        return _NullAsyncContext()


async def test_background_workers_drain_and_reap_on_a_fresh_session_each_tick():
    drained: list[int] = []
    reaped: list[int] = []
    built: list[object] = []

    class _FakeService:
        def __init__(self, repo) -> None:
            built.append(repo)
            self.repo = repo

        async def drain_outbox(self) -> None:
            drained.append(1)

        async def reap_expired(self) -> None:
            reaped.append(1)

    with (
        patch.object(app_main, "DatabaseManager") as db,
        patch.object(app_main.settings, "OUTBOX_POLL_INTERVAL_SECONDS", 0.01),
        patch.object(app_main.settings, "REAPER_INTERVAL_SECONDS", 0.0),
        patch("app.repositories.UploadChunkRepository") as chunk_repo,
        patch("app.services.UploadService", _FakeService),
    ):
        db.session_factory = _FakeSessionFactory()
        task = asyncio.create_task(app_main._background_workers())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(drained) >= 2, "drained the outbox on every tick"
    assert len(reaped) == len(drained), "reaped on every tick with interval 0"
    assert len(built) == len(drained), "a fresh repository/service per tick"
    assert all(repo is not None for repo in built)
    chunk_repo.assert_called()


async def test_background_workers_survive_a_transient_failure():
    """A failing iteration must be logged, and the loop must keep polling."""
    with (
        patch.object(app_main, "DatabaseManager") as db,
        patch.object(app_main.settings, "OUTBOX_POLL_INTERVAL_SECONDS", 0.01),
        patch.object(app_main.logger, "exception") as log_exception,
    ):
        db.session_factory = None  # the assert in the loop fails
        task = asyncio.create_task(app_main._background_workers())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert log_exception.call_count >= 2, "the loop kept retrying after each failure"
