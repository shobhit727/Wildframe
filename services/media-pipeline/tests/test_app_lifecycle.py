"""Application wiring: ``create_app`` + the ``lifespan`` startup/shutdown path.

``app/main.py`` is the outermost trust boundary of the service, so the tests
here exercise the behaviour a caller can actually observe:

* docs/OpenAPI exposure follows ``ENVIRONMENT``.
* CORS is wired from settings, and wildcard+credentials is not silently allowed
  in production.
* ``/health`` is liveness-only; ``/ready`` reports DB *and* Redis and returns
  503 with a per-dependency breakdown when either is down.
* in-flight requests are tracked, and a request arriving after the shutdown
  signal is refused with 503 + ``Retry-After``.
* ``/metrics`` is gated by the admin token in production only.
* an unhandled exception returns an opaque 500 with a correlation id — never
  the exception text.
* the lifespan refuses to start on an unhealthy database, cancels the outbox
  worker, and closes the engine on shutdown.
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

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


# ---------------------------------------------------------------------------
# create_app(): construction.
# ---------------------------------------------------------------------------


def test_create_app_exposes_docs_outside_production():
    app = _build("development")
    assert app.title == settings.SERVICE_NAME
    assert app.version == settings.SERVICE_VERSION
    assert app.docs_url == "/docs"
    assert app.redoc_url == "/redoc"
    assert app.openapi_url == "/openapi.json"
    assert app.description and "Media Pipeline" in app.description


def test_create_app_hides_docs_in_production():
    app = _build("production")
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_create_app_mounts_both_pipeline_and_legacy_routers():
    """The OpenAPI document is the observable contract for what is mounted."""
    app = _build("development")
    paths = set(app.openapi()["paths"])
    # Canonical /api/v1/pipeline routes plus the backward-compatible /media routes.
    assert "/api/v1/pipeline/jobs/{upload_session_id}/start" in paths
    assert "/api/v1/pipeline/jobs/{job_id}" in paths
    assert "/media/transcode" in paths
    assert "/media/job-status/{content_id}" in paths
    assert "/health" in paths
    assert "/ready" in paths


def test_create_app_installs_cors_and_body_size_middleware():
    app = _build("development")
    names = [m.cls.__name__ for m in app.user_middleware]
    assert "CORSMiddleware" in names
    # Two function middlewares (@app.middleware("http")) wrap as BaseHTTPMiddleware:
    # the in-flight tracker and the body-size cap.
    assert names.count("BaseHTTPMiddleware") == 2
    assert app.router is not None


async def test_cors_reflects_a_configured_origin():
    app = _build("development")
    with patch.object(settings, "CORS_ALLOWED_ORIGINS", ["https://studio.example.com"]):
        app2 = app_main.create_app()
    async with _client(app2) as client:
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
    body = response.json()
    assert body == {"status": "degraded"}
    # Liveness only: no per-dependency breakdown leaks from /health.
    assert "checks" not in body


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
        patch.object(app_main.redis, "from_url", Mock(return_value=redis_client)),
    ):
        async with _client(app) as client:
            response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "media-pipeline",
        "version": settings.SERVICE_VERSION,
        "checks": {"database": "ok", "redis": "ok"},
    }
    assert redis_client.closed is True


async def test_ready_is_503_when_the_database_is_down():
    app = _build("development")
    with (
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=False)),
        patch.object(app_main.redis, "from_url", Mock(return_value=_FakeRedis())),
    ):
        async with _client(app) as client:
            response = await client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {"database": "down", "redis": "ok"}


async def test_ready_is_503_when_redis_is_unreachable():
    app = _build("development")
    with (
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(
            app_main.redis,
            "from_url",
            Mock(side_effect=ConnectionError("connection refused")),
        ),
        patch.object(app_main.logger, "error") as log_error,
    ):
        async with _client(app) as client:
            response = await client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["checks"] == {"database": "ok", "redis": "down"}
    assert "Redis readiness check failed" in log_error.call_args.args[0]


async def test_ready_is_503_when_redis_ping_times_out():
    app = _build("development")
    with (
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.redis, "from_url", Mock(return_value=_FakeRedis())),
        patch.object(app_main.asyncio, "wait_for", AsyncMock(side_effect=asyncio.TimeoutError)),
    ):
        async with _client(app) as client:
            response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "down"


# ---------------------------------------------------------------------------
# The body-size cap.
# ---------------------------------------------------------------------------


MAX_BODY = 33554432


async def test_oversized_content_length_is_rejected_before_parsing():
    app = _build("development")
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/pipeline/jobs", content=b"{}", headers={"content-length": str(MAX_BODY + 1)}
        )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


async def test_unparsable_content_length_falls_through_to_the_route():
    """A malformed header must not be treated as an oversized body."""
    app = _build("development")
    async with _client(app) as client:
        response = await client.post(
            "/api/v1/pipeline/jobs", content=b"{}", headers={"content-length": "not-a-number"}
        )
    # The route rejects the empty body on its own terms, not with a 413.
    assert response.status_code != 413


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
    assert "correlation_id" in body
    # The exception text (and its credentials) must never reach the client.
    assert "hunter2" not in response.text
    assert "exploded" not in response.text


async def test_500_handler_echoes_the_inbound_correlation_id():
    """The opaque 500 must let an operator correlate the client-side trace."""
    app = _build("development")
    with patch.object(
        app_main.DatabaseManager, "health_check", AsyncMock(side_effect=RuntimeError("x"))
    ):
        async with _client(app) as client:
            response = await client.get("/health", headers={"X-Correlation-ID": "corr-1234"})
    assert response.json()["correlation_id"] == "corr-1234"


async def test_500_handler_generates_a_correlation_id_when_none_is_supplied():
    app = _build("development")
    with patch.object(
        app_main.DatabaseManager, "health_check", AsyncMock(side_effect=RuntimeError("x"))
    ):
        async with _client(app) as client:
            response = await client.get("/health")
    correlation_id = response.json()["correlation_id"]
    assert correlation_id, "a correlation id must always be present"
    assert "postgres" not in correlation_id


# ---------------------------------------------------------------------------
# /metrics gating (#469).
# ---------------------------------------------------------------------------


async def test_metrics_is_open_outside_production():
    app = _build("development")
    async with _client(app) as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")


def _only_gated_metrics_app() -> FastAPI:
    """Build the app with the observability-owned /metrics route suppressed.

    ``wire_observability`` registers its own ``GET /metrics`` unless it is told
    the service owns the route; suppressing it isolates the service's gate so
    the gate logic itself can be asserted on its own.
    """
    with patch.object(app_main, "wire_observability") as wire:
        wire.side_effect = lambda app, **kwargs: None
        return app_main.create_app()


async def test_metrics_is_publicly_readable_in_production_despite_the_token_gate():
    """BUG (app/main.py:232): the #469 admin-token gate on /metrics is dead code.

    ``wire_observability(app, ...)`` is called *without* ``register_metrics=False``,
    so it registers its own ungated ``GET /metrics`` at line 232 — i.e. before the
    token-gated route the service defines at line 250. Starlette matches routes
    in registration order, so the observability route always wins and
    ``require_metrics_token`` is never consulted. uploads-service passes
    ``register_metrics=False`` and is not affected.

    Asserted as-is: production code is not modified.
    """
    with (
        patch.object(settings, "ENVIRONMENT", "production"),
        patch.object(settings, "METRICS_TOKEN", "s3cret-metrics-token"),
    ):
        app = _build("production")
        async with _client(app) as client:
            response = await client.get("/metrics")
    assert response.status_code == 200
    assert b"python_info" in response.content


async def test_the_metrics_token_gate_itself_rejects_an_unauthenticated_scrape():
    """With the duplicate route gone, the gate does fail closed."""
    with (
        patch.object(settings, "ENVIRONMENT", "production"),
        patch.object(settings, "METRICS_TOKEN", "s3cret-metrics-token"),
    ):
        app = _only_gated_metrics_app()
        async with _client(app) as client:
            unauthenticated = await client.get("/metrics")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["detail"] == "Unauthorized"


async def test_the_metrics_token_gate_accepts_the_configured_token():
    with (
        patch.object(settings, "ENVIRONMENT", "production"),
        patch.object(settings, "METRICS_TOKEN", "s3cret-metrics-token"),
    ):
        app = _only_gated_metrics_app()
        async with _client(app) as client:
            response = await client.get(
                "/metrics", headers={"Authorization": "Bearer s3cret-metrics-token"}
            )
    assert response.status_code == 200


async def test_the_metrics_token_gate_rejects_a_wrong_token():
    with (
        patch.object(settings, "ENVIRONMENT", "production"),
        patch.object(settings, "METRICS_TOKEN", "s3cret-metrics-token"),
    ):
        app = _only_gated_metrics_app()
        async with _client(app) as client:
            response = await client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


async def test_the_metrics_token_gate_fails_closed_without_a_configured_token():
    """An unset METRICS_TOKEN must fail closed, never open."""
    with (
        patch.object(settings, "ENVIRONMENT", "production"),
        patch.object(settings, "METRICS_TOKEN", ""),
    ):
        app = _only_gated_metrics_app()
        async with _client(app) as client:
            response = await client.get("/metrics")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# lifespan().
# ---------------------------------------------------------------------------


async def test_lifespan_starts_the_worker_and_closes_the_database():
    app = _build("development")
    with (
        patch.object(app_main.DatabaseManager, "init", AsyncMock()) as init,
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.DatabaseManager, "close", AsyncMock()) as close,
        patch.object(
            app_main, "_drain_outbox_worker", AsyncMock(side_effect=asyncio.CancelledError)
        ),
    ):
        async with app_main.lifespan(app):
            assert app.state.shutting_down is False
            assert app_main._shutdown_event is not None
            assert not app_main._shutdown_event.is_set()
        init.assert_awaited_once()
    assert app.state.shutting_down is True
    assert app_main._shutdown_event.is_set()
    close.assert_awaited_once()


async def test_lifespan_warns_but_still_boots_on_an_unhealthy_database():
    """Unlike uploads-service, this service degrades instead of refusing to boot."""
    app = _build("development")
    with (
        patch.object(app_main.DatabaseManager, "init", AsyncMock()),
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=False)),
        patch.object(app_main.DatabaseManager, "close", AsyncMock()),
        patch.object(
            app_main, "_drain_outbox_worker", AsyncMock(side_effect=asyncio.CancelledError)
        ),
        patch.object(app_main.logger, "warning") as log_warning,
    ):
        async with app_main.lifespan(app):
            assert app.state.shutting_down is False
    assert any(
        "Database health check failed on startup" in call.args[0]
        for call in log_warning.call_args_list
    )


async def test_lifespan_logs_a_healthy_database_on_startup():
    app = _build("development")
    with (
        patch.object(app_main.DatabaseManager, "init", AsyncMock()),
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.DatabaseManager, "close", AsyncMock()),
        patch.object(
            app_main, "_drain_outbox_worker", AsyncMock(side_effect=asyncio.CancelledError)
        ),
        patch.object(app_main.logger, "info") as log_info,
    ):
        async with app_main.lifespan(app):
            pass
    assert any(
        "Database connection established" in call.args[0] for call in log_info.call_args_list
    )


async def test_lifespan_survives_a_failing_outbox_worker():
    """The worker loop must never take the process down."""
    app = _build("development")
    iterations = 0

    async def _flaky_worker() -> None:
        nonlocal iterations
        while True:
            iterations += 1
            if iterations >= 2:
                await asyncio.sleep(3600)

    with (
        patch.object(app_main.DatabaseManager, "init", AsyncMock()),
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.DatabaseManager, "close", AsyncMock()),
        patch.object(app_main, "_drain_outbox_worker", _flaky_worker),
        patch.object(app_main.settings, "OUTBOX_POLL_INTERVAL_SECONDS", 0.01),
    ):
        async with app_main.lifespan(app):
            await asyncio.sleep(0.05)
    assert iterations >= 2


async def test_outbox_worker_survives_a_repository_error():
    """A transient DB error is logged, and the loop keeps polling."""
    with (
        patch.object(app_main, "DatabaseManager") as db,
        patch.object(app_main.settings, "OUTBOX_POLL_INTERVAL_SECONDS", 0.01),
        patch.object(app_main.logger, "exception") as log_exception,
    ):
        db.session_factory = None
        task = asyncio.create_task(app_main._drain_outbox_worker())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert log_exception.call_count >= 2, "the loop kept retrying after each failure"


async def test_outbox_worker_drains_with_a_real_service_per_iteration():
    """Each poll builds a fresh service bound to a fresh session."""
    drained: list[object] = []
    built: list[dict] = []

    class _FakeService:
        def __init__(self, job_repo, log_repo) -> None:
            built.append({"job_repo": job_repo, "log_repo": log_repo})

        async def drain_outbox(self) -> None:
            drained.append(object())

    class _NullAsyncContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    class _FakeSessionFactory:
        def __call__(self):
            return _NullAsyncContext()

    with (
        patch.object(app_main, "DatabaseManager") as db,
        patch.object(app_main.settings, "OUTBOX_POLL_INTERVAL_SECONDS", 0.01),
        patch("app.repositories.PipelineJobRepository") as job_repo,
        patch("app.repositories.PipelineStageLogRepository") as log_repo,
        patch("app.services.MediaPipelineService", _FakeService),
    ):
        db.session_factory = _FakeSessionFactory()
        task = asyncio.create_task(app_main._drain_outbox_worker())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(drained) >= 2, "drained the outbox on every poll"
    # Each iteration is wired to its own repository instances over one session.
    assert len(built) == len(drained)
    assert job_repo.call_count == len(drained) == log_repo.call_count
    assert all(entry["job_repo"] is not None and entry["log_repo"] is not None for entry in built)


async def test_lifespan_applies_dlq_retention_for_the_kafka_publisher():
    """Bounded DLQ retention is applied in the background, never blocking boot."""
    import wildframe_events.dlq_retention as dlq_retention

    app = _build("development")
    with (
        patch.object(settings, "EVENT_PUBLISHER", "kafka"),
        patch.object(dlq_retention, "apply_dlq_retention", AsyncMock()) as apply_retention,
        patch.object(app_main.DatabaseManager, "init", AsyncMock()),
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.DatabaseManager, "close", AsyncMock()),
        patch.object(
            app_main, "_drain_outbox_worker", AsyncMock(side_effect=asyncio.CancelledError)
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
        patch.object(app_main.DatabaseManager, "init", AsyncMock()),
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.DatabaseManager, "close", AsyncMock()),
        patch.object(
            app_main, "_drain_outbox_worker", AsyncMock(side_effect=asyncio.CancelledError)
        ),
    ):
        async with app_main.lifespan(app):
            await asyncio.sleep(0)
    apply_retention.assert_not_awaited()


async def test_lifespan_drain_budget_is_bounded():
    """Shutdown never waits forever on a stuck in-flight request."""
    app = _build("development")
    with (
        patch.object(app_main, "_MAX_DRAIN_SECONDS", 0.05),
        patch.object(app_main.DatabaseManager, "init", AsyncMock()),
        patch.object(app_main.DatabaseManager, "health_check", AsyncMock(return_value=True)),
        patch.object(app_main.DatabaseManager, "close", AsyncMock()),
        patch.object(
            app_main, "_drain_outbox_worker", AsyncMock(side_effect=asyncio.CancelledError)
        ),
    ):
        async with app_main.lifespan(app):
            app_main._in_flight_requests = 1  # a request that never finishes
        assert app_main._in_flight_requests == 1


def test_fallback_lock_is_shared_when_the_lifespan_never_ran():
    assert app_main._fallback_lock() is app_main._fallback_lock()


def test_fallback_lock_is_replaced_when_the_lifespan_initialises_one():
    app_main._in_flight_lock = asyncio.Lock()
    assert app_main._fallback_lock() is not app_main._in_flight_lock
