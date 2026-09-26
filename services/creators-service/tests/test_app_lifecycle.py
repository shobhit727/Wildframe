"""Tests for creators-service app/main.py — the app factory, probes, middleware,
the inbound-event worker and the graceful-shutdown lifespan.

``lifespan`` starts a background poller (``_drain_inbound_events_worker``) and
owns the shutdown drain: it flips ``app.state.shutting_down``, cancels the
worker, waits for in-flight requests (bounded by ``_MAX_DRAIN_SECONDS``) and
closes the database. The app factory adds CORS, the in-flight/503 middleware,
``/health``, ``/ready`` (DB + Redis), a request body cap and an opaque 500
handler. All of it is exercised here with the database, Redis and repository
layers stubbed.
"""

import asyncio
import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

import app.main as main
from app.core.settings import settings
from app.main import _drain_inbound_events_worker, create_app, lifespan

pytestmark = pytest.mark.unit

# Mirrors MAX_BODY_SIZE in create_app()'s limit_body_size middleware.
MAX_BODY_SIZE = 1048576


@contextlib.contextmanager
def record_from(logger_name: str):
    """Capture records on ``logger_name``.

    caplog is unusable here: wire_observability() (called by create_app)
    installs its own JSON handler on the root logger, replacing the handlers
    caplog attaches.
    """
    captured: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger = logging.getLogger(logger_name)
    handler = _Collector()
    logger.addHandler(handler)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def clean_globals():
    """Reset the module-level shutdown state between tests.

    ``asyncio.Lock()`` binds to the running loop on first use, so the lock has
    to be rebuilt for every test's event loop.
    """
    saved = (
        main._shutdown_event,
        main._inbound_consumer_task,
        main._in_flight_requests,
        main._in_flight_lock,
    )
    main._shutdown_event = None
    main._inbound_consumer_task = None
    main._in_flight_requests = 0
    main._in_flight_lock = asyncio.Lock()
    yield main
    main._shutdown_event = saved[0]
    main._inbound_consumer_task = saved[1]
    main._in_flight_requests = saved[2]
    main._in_flight_lock = saved[3]


@pytest.fixture
def db_stub(monkeypatch):
    """DatabaseManager.health_check / close doubles."""
    from app.core.database import DatabaseManager

    calls: dict[str, int] = {"health_check": 0, "close": 0}

    async def health_check():
        calls["health_check"] += 1
        return True

    async def close():
        calls["close"] += 1

    monkeypatch.setattr(DatabaseManager, "health_check", health_check)
    monkeypatch.setattr(DatabaseManager, "close", close)
    return calls


@contextlib.contextmanager
def poll_interval(seconds: int):
    """Temporarily set the inbound worker's poll interval.

    ``INBOUND_EVENT_POLL_INTERVAL_SECONDS`` is not a field on ``Settings`` (the
    worker reads it through a ``getattr`` default of 30s) and pydantic refuses
    unknown attributes, so the value is written straight into the model dict.
    """
    name = "INBOUND_EVENT_POLL_INTERVAL_SECONDS"
    had_previous = name in settings.__dict__
    previous = settings.__dict__.get(name)
    object.__setattr__(settings, name, seconds)
    try:
        yield
    finally:
        if had_previous:
            object.__setattr__(settings, name, previous)
        else:
            settings.__dict__.pop(name, None)


def make_client(application, *, raise_app_exceptions: bool = True) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=raise_app_exceptions),
        base_url="http://test",
    )


@pytest.fixture
def worker_stubs(monkeypatch):
    """Replace the worker's DB session + service with test doubles."""
    commits: list[str] = []
    drains: list[int] = []

    class FakeSession:
        async def commit(self):
            commits.append("commit")

    async def fake_get_db():
        yield FakeSession()

    service = MagicMock()
    service.drain_inbound_events = AsyncMock(side_effect=lambda limit=100: drains.append(limit))

    monkeypatch.setattr(main, "get_db", fake_get_db)
    monkeypatch.setattr(main, "CreatorService", MagicMock(return_value=service))
    # ``InboundEventRepository`` is deliberately left real: its constructor only
    # stores the session, and TestInboundEventWorker asserts the wiring.
    return {"commits": commits, "drains": drains, "service": service}


@pytest.fixture(autouse=True)
def fast_poll():
    """Let the worker loop run within a test's time budget."""
    with poll_interval(0):
        yield


# ------------------------------------------------------------------ app factory
class TestCreateApp:
    def test_metadata_comes_from_settings(self):
        application = create_app()

        assert application.title == settings.SERVICE_NAME
        assert application.version == settings.SERVICE_VERSION
        assert "Creators" in application.description

    def test_docs_are_exposed_outside_production(self, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "development")

        application = create_app()

        assert application.docs_url == "/docs"
        assert application.redoc_url == "/redoc"
        assert application.openapi_url == "/openapi.json"

    def test_docs_are_disabled_in_production(self, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")

        application = create_app()

        assert application.docs_url is None
        assert application.redoc_url is None
        assert application.openapi_url is None

    def test_creator_and_admin_routers_are_mounted(self):
        paths = create_app().openapi()["paths"]

        assert "/api/v1/creators/onboard" in paths
        assert "/api/v1/creators/me" in paths
        assert "/api/v1/admin/creators/{creator_id}/milestones" in paths


class TestHealthEndpoint:
    async def test_health_is_status_only(self, db_stub):
        application = create_app()

        async with make_client(application) as client:
            response = await client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        # Liveness must not depend on the database (#628).
        assert db_stub["health_check"] == 0


class TestReadinessEndpoint:
    @pytest.fixture
    def redis_ok(self, monkeypatch):
        client = MagicMock()
        client.ping = AsyncMock(return_value=True)
        client.close = AsyncMock()
        monkeypatch.setattr(main.redis, "from_url", AsyncMock(return_value=client))
        return client

    async def test_ready_when_database_and_redis_answer(self, db_stub, redis_ok):
        application = create_app()

        async with make_client(application) as client:
            response = await client.get("/ready")

        body = response.json()
        assert response.status_code == 200
        assert body == {
            "status": "ready",
            "service": "creators",
            "version": settings.SERVICE_VERSION,
            "checks": {"database": "ok", "redis": "ok"},
        }
        redis_ok.close.assert_awaited_once()

    async def test_database_down_makes_the_service_not_ready(self, db_stub, redis_ok, monkeypatch):
        from app.core.database import DatabaseManager

        async def unhealthy():
            return False

        monkeypatch.setattr(DatabaseManager, "health_check", unhealthy)
        application = create_app()

        async with make_client(application) as client:
            response = await client.get("/ready")

        body = response.json()
        assert response.status_code == 503
        assert body["status"] == "not_ready"
        assert body["checks"]["database"] == "down"
        assert body["checks"]["redis"] == "ok"

    async def test_redis_down_makes_the_service_not_ready(self, db_stub, monkeypatch):
        client = MagicMock()
        client.ping = AsyncMock(side_effect=ConnectionError("redis down"))
        client.close = AsyncMock()
        monkeypatch.setattr(main.redis, "from_url", AsyncMock(return_value=client))
        application = create_app()

        with record_from("app.main") as records:
            async with make_client(application) as http:
                response = await http.get("/ready")

        body = response.json()
        assert response.status_code == 503
        assert body["status"] == "not_ready"
        assert body["checks"]["redis"] == "down"
        assert any("Redis readiness check failed" in r.getMessage() for r in records)

    async def test_redis_connect_failure_is_contained(self, db_stub, monkeypatch):
        monkeypatch.setattr(
            main.redis, "from_url", MagicMock(side_effect=OSError("cannot connect"))
        )
        application = create_app()

        async with make_client(application) as client:
            response = await client.get("/ready")

        assert response.status_code == 503
        assert response.json()["checks"]["redis"] == "down"


class TestInFlightMiddleware:
    async def test_requests_are_counted_and_released(self, db_stub):
        application = create_app()
        application.state.shutting_down = False

        async with make_client(application) as client:
            await client.get("/health")
            assert main._in_flight_requests == 0
            await client.get("/health")

        assert main._in_flight_requests == 0

    async def test_requests_are_refused_while_shutting_down(self, db_stub):
        application = create_app()
        application.state.shutting_down = True

        async with make_client(application) as client:
            response = await client.get("/health")

        assert response.status_code == 503
        assert response.json() == {"detail": "Service shutting down"}
        assert response.headers["Retry-After"] == str(main._MAX_DRAIN_SECONDS)

    async def test_a_refused_request_is_not_counted(self, db_stub):
        application = create_app()
        application.state.shutting_down = True

        async with make_client(application) as client:
            await client.get("/health")

        assert main._in_flight_requests == 0


class TestBodySizeLimitMiddleware:
    @pytest.fixture
    def echo_app(self):
        """Test-only app with a DB-free endpoint."""
        application = create_app()

        @application.post("/_test/echo")
        async def echo():
            return {"ok": True}

        return application

    async def test_oversized_body_is_rejected_with_413(self, echo_app):
        async with make_client(echo_app) as client:
            response = await client.post(
                "/_test/echo",
                content=b"{}",
                headers={
                    "content-type": "application/json",
                    "content-length": str(MAX_BODY_SIZE + 1),
                },
            )

        assert response.status_code == 413
        assert response.json() == {"detail": "Request body too large"}

    async def test_body_at_the_limit_is_accepted(self, echo_app):
        async with make_client(echo_app) as client:
            response = await client.post(
                "/_test/echo",
                content=b"{}",
                headers={"content-type": "application/json", "content-length": str(MAX_BODY_SIZE)},
            )

        assert response.status_code == 200

    async def test_empty_content_length_passes_through(self, echo_app):
        async with make_client(echo_app) as client:
            response = await client.post(
                "/_test/echo", content=b"", headers={"content-length": ""}
            )

        assert response.status_code == 200

    async def test_non_numeric_content_length_is_ignored(self, echo_app):
        async with make_client(echo_app) as client:
            response = await client.post(
                "/_test/echo", content=b"", headers={"content-length": "not-a-number"}
            )

        assert response.status_code == 200


class TestGeneralExceptionHandler:
    async def test_unhandled_exception_returns_an_opaque_500(self, db_stub):
        application = create_app()

        @application.get("/_test/boom")
        async def boom():
            raise RuntimeError("kaboom")

        with record_from("app.main") as records:
            async with make_client(application, raise_app_exceptions=False) as client:
                response = await client.get("/_test/boom")

        assert response.status_code == 500
        assert response.json() == {"status_code": 500, "message": "Internal server error"}
        # The internal message must not leak into the response body.
        assert "kaboom" not in response.text
        assert any("Unhandled exception" in r.getMessage() for r in records)


class TestCorsMiddleware:
    async def test_allowed_origin_is_reflected(self, db_stub):
        origin = settings.CORS_ALLOWED_ORIGINS[0]
        application = create_app()

        async with make_client(application) as client:
            response = await client.get("/health", headers={"Origin": origin})

        assert response.headers["access-control-allow-origin"] == origin

    async def test_credentials_flag_follows_settings(self, db_stub, monkeypatch):
        monkeypatch.setattr(settings, "CORS_ALLOW_CREDENTIALS", False)
        application = create_app()

        async with make_client(application) as client:
            response = await client.get("/health")

        assert "access-control-allow-credentials" not in response.headers


# ------------------------------------------------------------- inbound worker
class TestInboundEventWorker:
    async def test_drains_pending_events_and_commits(self, worker_stubs):
        task = asyncio.create_task(_drain_inbound_events_worker())
        try:
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert worker_stubs["drains"], "the worker never drained"
        assert worker_stubs["drains"][0] == 100
        assert worker_stubs["commits"]

    async def test_service_is_wired_with_real_repositories(self, worker_stubs, monkeypatch):
        from app.repositories import (
            CreatorAccountRepository,
            CreatorPoolBalanceRepository,
            EffectiveFloorRepository,
            InboundEventRepository,
            MilestoneRepository,
            PayoutLedgerRepository,
        )

        built: list[MagicMock] = []

        def factory(*args, **kwargs):
            service = MagicMock(**kwargs)
            service.drain_inbound_events = AsyncMock(return_value=0)
            built.append(service)
            return service

        monkeypatch.setattr(main, "CreatorService", factory)

        task = asyncio.create_task(_drain_inbound_events_worker())
        try:
            await asyncio.sleep(0.05)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert built, "the worker never built a CreatorService"
        service = built[0]
        assert isinstance(service.inbound_repo, InboundEventRepository)
        assert isinstance(service.acct_repo, CreatorAccountRepository)
        assert isinstance(service.floor_repo, EffectiveFloorRepository)
        assert isinstance(service.pool_repo, CreatorPoolBalanceRepository)
        assert isinstance(service.milestone_repo, MilestoneRepository)
        assert isinstance(service.ledger_repo, PayoutLedgerRepository)

    async def test_cancellation_is_logged_and_propagated(self, worker_stubs):
        with record_from("app.main") as records:
            task = asyncio.create_task(_drain_inbound_events_worker())
            await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert any(
            "Inbound event consumer worker cancelled" in r.getMessage() for r in records
        )

    async def test_a_failing_iteration_is_logged_and_the_worker_survives(
        self, worker_stubs, monkeypatch
    ):
        attempts: list[int] = []

        async def exploding_get_db():
            attempts.append(1)
            raise RuntimeError("db unavailable")
            yield  # makes this an async generator

        monkeypatch.setattr(main, "get_db", exploding_get_db)

        with record_from("app.main") as records:
            task = asyncio.create_task(_drain_inbound_events_worker())
            await asyncio.sleep(0.05)
            still_running = not task.done()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert len(attempts) > 1, "the worker must retry after a failure"
        assert still_running
        assert any(
            "inbound event drain iteration failed" in r.getMessage() for r in records
        )

    async def test_poll_interval_is_read_from_settings(self, worker_stubs):
        observed: list[int] = []
        worker_stubs["service"].drain_inbound_events = AsyncMock(
            side_effect=lambda limit=100: observed.append(limit)
        )

        with poll_interval(30):
            # A 30s interval must not fire within the test window.
            task = asyncio.create_task(_drain_inbound_events_worker())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert observed == []


# --------------------------------------------------------------------- lifespan
class TestLifespan:
    async def test_startup_and_shutdown_sequence(self, db_stub, worker_stubs):
        application = create_app()

        with record_from("app.main") as records:
            async with lifespan(application):
                assert db_stub["health_check"] == 1
                assert db_stub["close"] == 0
                assert application.state.shutting_down is False
                assert main._inbound_consumer_task is not None
                assert not main._inbound_consumer_task.done()
                assert main._shutdown_event is not None
                assert main._shutdown_event.is_set() is False

            assert application.state.shutting_down is True
            assert main._shutdown_event.is_set() is True
            assert main._inbound_consumer_task.cancelled() is True
            assert db_stub["close"] == 1

        messages = [r.getMessage() for r in records]
        assert f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}" in messages
        assert f"Environment: {settings.ENVIRONMENT}" in messages
        assert "Inbound event consumer started" in messages
        assert "All startup checks passed" in messages
        assert f"Shutting down {settings.SERVICE_NAME}" in messages
        assert "Inbound event consumer stopped" in messages
        assert "Shutdown complete" in messages

    async def test_unhealthy_database_aborts_startup(self, db_stub, worker_stubs, monkeypatch):
        from app.core.database import DatabaseManager

        async def unhealthy():
            return False

        monkeypatch.setattr(DatabaseManager, "health_check", unhealthy)
        application = create_app()

        with record_from("app.main") as records:
            with pytest.raises(RuntimeError, match="Database is not healthy on startup"):
                async with lifespan(application):
                    pytest.fail("the lifespan must not yield when the database is down")

        assert main._inbound_consumer_task is None
        assert db_stub["close"] == 0
        assert any("Database health check failed" in r.getMessage() for r in records)

    async def test_shutdown_waits_for_in_flight_requests(self, db_stub, worker_stubs):
        application = create_app()
        # Simulate a request that never finishes: the counter stays > 0.
        main._in_flight_requests = 0

        async def release_soon():
            await asyncio.sleep(0.02)
            main._in_flight_requests = 0

        releaser = asyncio.create_task(release_soon())
        main._in_flight_requests = 1
        async with lifespan(application):
            pass
        await releaser

        assert main._in_flight_requests == 0
        assert db_stub["close"] == 1

    async def test_shutdown_drain_is_bounded(self, db_stub, worker_stubs, monkeypatch):
        monkeypatch.setattr(main, "_MAX_DRAIN_SECONDS", 0.05)
        application = create_app()
        main._in_flight_requests = 1  # never drains

        with record_from("app.main") as records:
            async with lifespan(application):
                pass

        assert db_stub["close"] == 1
        assert main._in_flight_requests == 1
        assert any("Shutdown drain timeout" in r.getMessage() for r in records)

    async def test_lifespan_is_attached_to_the_created_app(self, db_stub, worker_stubs):
        application = create_app()

        async with application.router.lifespan_context(application):
            assert db_stub["health_check"] == 1

        assert db_stub["close"] == 1
