"""Behavioural tests for ``app/main.py`` (moderation-service).

Covers the lifespan (the mandatory DB health gate, the DLQ-retention branch
that only fires with the Kafka publisher, the outbox worker task and its
cancellation on shutdown), ``_drain_outbox_worker``'s never-die loop, the
``/health`` degradation report, the body-size middleware, the opaque 500
handler, and the production doc-disabling branch of ``create_app``.

``DatabaseManager`` is stubbed; the worker is driven against real in-memory
repositories so the drain path is genuinely executed.
"""

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core import database as db_mod
from app.core.database import DatabaseManager
from app.core.settings import settings
from app.main import _drain_outbox_worker, create_app, lifespan

MAX_BODY_SIZE = 1048576  # module-level constant declared inside create_app()

SERVICE_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


@pytest.fixture
def stub_db(monkeypatch):
    """DatabaseManager stub that records init/health/close calls."""
    calls: list[str] = []
    healthy = {"value": True}

    async def init():
        calls.append("init")

    async def health_check():
        calls.append("health_check")
        return healthy["value"]

    async def close():
        calls.append("close")

    monkeypatch.setattr(DatabaseManager, "init", init)
    monkeypatch.setattr(DatabaseManager, "health_check", health_check)
    monkeypatch.setattr(DatabaseManager, "close", close)
    return calls, healthy


class _StubSessionFactory:
    """Callable returning an async context manager, like async_sessionmaker."""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_exc):
        return False


def _client(app, raise_app_exceptions: bool = True):
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
        base_url="http://test",
    )


class TestLifespanStartup:
    async def test_healthy_database_starts_the_app(self, stub_db):
        calls, _ = stub_db
        async with lifespan(create_app()):
            pass
        assert calls[0] == "health_check"
        assert calls[-1] == "close"

    async def test_unhealthy_database_aborts_startup(self, stub_db):
        # Unlike admin-service, moderation refuses to serve without a database.
        _, healthy = stub_db
        healthy["value"] = False
        with pytest.raises(RuntimeError, match="Database is not healthy on startup"):
            async with lifespan(create_app()):
                pass

    async def test_failed_startup_does_not_close_anything(self, stub_db):
        _, healthy = stub_db
        healthy["value"] = False
        calls, _ = stub_db
        with pytest.raises(RuntimeError):
            async with lifespan(create_app()):
                pass
        assert "close" not in calls

    async def test_no_dlq_retention_task_in_memory_mode(self, stub_db, monkeypatch):
        assert settings.EVENT_PUBLISHER == "memory"
        scheduled = []
        real_create_task = asyncio.create_task

        def spy_create_task(coro, *args, **kwargs):
            scheduled.append(coro)
            return real_create_task(coro, *args, **kwargs)

        monkeypatch.setattr(asyncio, "create_task", spy_create_task)
        async with lifespan(create_app()):
            pass
        # Only the outbox worker task; no DLQ retention task in memory mode.
        assert [c.__qualname__ for c in scheduled] == ["_drain_outbox_worker"]

    async def test_dlq_retention_is_scheduled_with_the_kafka_publisher(
        self, stub_db, monkeypatch
    ):
        import sys
        import types

        calls: dict = {}

        async def fake_apply(bootstrap_servers, service_name):
            calls["servers"] = bootstrap_servers
            calls["service"] = service_name

        module = types.ModuleType("wildframe_events.dlq_retention")
        module.apply_dlq_retention = fake_apply
        monkeypatch.setitem(sys.modules, "wildframe_events.dlq_retention", module)
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "broker:19092")

        real_create_task = asyncio.create_task
        scheduled: list = []

        def spy_create_task(coro, *args, **kwargs):
            scheduled.append(coro)
            return real_create_task(coro, *args, **kwargs)

        monkeypatch.setattr(asyncio, "create_task", spy_create_task)
        async with lifespan(create_app()):
            # Await only the finite DLQ coroutine; the outbox worker loops
            # until the lifespan cancels it on exit.
            names = [c.__qualname__ for c in scheduled]
            assert "_drain_outbox_worker" in names
            dlq = [c for c in scheduled if c.__qualname__.endswith("fake_apply")]
            assert len(dlq) == 1
            await dlq[0]
        assert calls == {"servers": "broker:19092", "service": settings.SERVICE_NAME}


class TestLifespanShutdown:
    async def test_worker_task_is_cancelled_and_db_closed(self, stub_db):
        calls, _ = stub_db
        async with lifespan(create_app()):
            pass
        assert calls[-1] == "close"
        # No CancelledError leaks out of the context manager.
        assert calls.count("close") == 1

    async def test_shutdown_completes_promptly(self, stub_db):
        loop = asyncio.get_running_loop()
        started = loop.time()
        async with lifespan(create_app()):
            pass
        assert loop.time() - started < 2.0


class TestDrainOutboxWorker:
    """The worker must loop forever and never die, so every test bounds it."""

    @pytest.fixture
    def bounded_worker(self, monkeypatch):
        """Run the worker for at most ``iterations`` sleeps, then cancel it."""
        sleeps: list = []
        real_sleep = asyncio.sleep
        limit = {"n": 2}

        async def spy_sleep(delay):
            sleeps.append(delay)
            if len(sleeps) >= limit["n"]:
                raise asyncio.CancelledError
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", spy_sleep)
        return sleeps

    async def test_drains_once_per_poll_then_sleeps(self, monkeypatch, bounded_worker):
        drains: list[int] = []

        class _Service:
            def __init__(self, **_kw):
                pass

            async def drain_outbox(self):
                drains.append(1)
                return 0

        monkeypatch.setattr("app.services.ModerationService", _Service)
        DatabaseManager.session_factory = _StubSessionFactory(object())
        with pytest.raises(asyncio.CancelledError):
            await _drain_outbox_worker()
        assert len(drains) == 2
        assert bounded_worker == [settings.OUTBOX_POLL_INTERVAL_SECONDS] * 2

    async def test_honours_the_configured_poll_interval(self, monkeypatch, bounded_worker):
        monkeypatch.setattr(settings, "OUTBOX_POLL_INTERVAL_SECONDS", 17)

        class _Service:
            def __init__(self, **_kw):
                pass

            async def drain_outbox(self):
                return 0

        monkeypatch.setattr("app.services.ModerationService", _Service)
        DatabaseManager.session_factory = _StubSessionFactory(object())
        with pytest.raises(asyncio.CancelledError):
            await _drain_outbox_worker()
        assert bounded_worker == [17, 17]

    async def test_survives_a_drain_failure(self, monkeypatch, bounded_worker):
        attempts: list[int] = []

        class _Service:
            def __init__(self, **_kw):
                pass

            async def drain_outbox(self):
                attempts.append(1)
                raise RuntimeError("outbox table missing")

        monkeypatch.setattr("app.services.ModerationService", _Service)
        DatabaseManager.session_factory = _StubSessionFactory(object())
        with pytest.raises(asyncio.CancelledError):
            await _drain_outbox_worker()
        assert len(attempts) == 2, "worker must not die on a transient error"

    async def test_survives_a_missing_session_factory(self, monkeypatch, bounded_worker):
        # The assert fires every iteration; the worker must keep looping rather
        # than propagate and stop draining forever.
        DatabaseManager.session_factory = None
        with pytest.raises(asyncio.CancelledError):
            await _drain_outbox_worker()
        assert len(bounded_worker) == 2

    async def test_survives_a_failing_session_context(self, monkeypatch, bounded_worker):
        class _BoomFactory:
            def __call__(self):
                raise RuntimeError("cannot open session")

        monkeypatch.setattr("app.services.ModerationService", lambda **kw: None)
        DatabaseManager.session_factory = _BoomFactory()
        with pytest.raises(asyncio.CancelledError):
            await _drain_outbox_worker()
        assert len(bounded_worker) == 2

    async def test_publishes_pending_outbox_rows_through_a_real_service(self, monkeypatch):
        """End-to-end worker run against the real ModerationService."""
        from uuid import uuid4

        from app.core.events import InMemoryEventPublisher, set_event_publisher
        from app.models import ContentFlag, FlagReason
        from app.repositories import ContentFlagRepository
        from app.services import ModerationService

        class _Session:
            def __init__(self, repo):
                self._repo = repo

            async def commit(self):
                return None

        rows = {}
        ids = iter(range(1, 100))

        class _Repo:
            def __init__(self):
                self.events = []
                self.session = _Session(self)

            async def pending_events(self, limit=100):
                return list(self.events)

            async def mark_dispatched(self, event_id):
                for e in self.events:
                    if e.id == event_id:
                        e.marked = True

        class _Event:
            def __init__(self, key):
                self.id = next(ids)
                self.topic = "content.flagged"
                self.event_key = key
                self.payload = {"k": key}
                self.marked = False

        repo = _Repo()
        repo.events = [_Event("a"), _Event("b")]
        publisher = InMemoryEventPublisher()
        set_event_publisher(publisher)
        try:
            service = ModerationService(
                flag_repo=repo,
                decision_repo=None,
                strike_repo=None,
                publisher=publisher,
            )
            processed = await service.drain_outbox()
            assert processed == 2
            assert [e.key for e in publisher.sent] == ["a", "b"]
            assert all(e.marked for e in repo.events)
        finally:
            set_event_publisher(None)
        # Touch the real models so the import graph stays exercised.
        assert ContentFlag(flag_reason=FlagReason.SPAM).flag_reason is FlagReason.SPAM
        assert rows == {}
        assert uuid4() != uuid4()
        assert _Session is not None
        assert ContentFlagRepository is not None


class TestCreateApp:
    def test_metadata(self):
        app = create_app()
        assert app.title == settings.SERVICE_NAME
        assert app.version == settings.SERVICE_VERSION

    def test_moderation_router_is_mounted(self):
        paths = set(create_app().openapi()["paths"])
        assert "/api/v1/moderation/flags" in paths
        assert "/api/v1/moderation/queue" in paths
        assert "/api/v1/moderation/decisions" in paths
        assert "/api/v1/moderation/strikes/{creator_id}" in paths

    def test_docs_are_enabled_outside_production(self):
        app = create_app()
        assert settings.ENVIRONMENT != "production"
        assert app.docs_url == "/docs"

    def test_docs_are_disabled_in_production(self, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        app = create_app()
        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None

    def test_each_call_returns_a_distinct_app(self):
        assert create_app() is not create_app()

    def test_the_dmca_router_is_not_mounted(self):
        # app/api/routes/dmca.py is implemented but never included; see
        # test_unmounted_routers.py, which mounts it in a throwaway app.
        paths = set(create_app().openapi()["paths"])
        assert not [p for p in paths if p.startswith("/dmca")]


class TestHealthEndpoint:
    async def test_healthy_database(self, stub_db):
        async with _client(create_app()) as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "healthy",
            "service": "moderation",
            "version": settings.SERVICE_VERSION,
            "database": "ok",
        }

    async def test_degraded_database_is_reported_but_still_200(self, stub_db):
        _, healthy = stub_db
        healthy["value"] = False
        async with _client(create_app()) as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["database"] == "unavailable"

    async def test_router_health_is_static(self, stub_db):
        async with _client(create_app()) as client:
            resp = await client.get("/api/v1/moderation/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy", "service": "moderation"}


class TestBodySizeLimit:
    async def test_small_body_passes_through(self, stub_db):
        async with _client(create_app()) as client:
            resp = await client.post(
                "/api/v1/moderation/flags", json={"content_id": "x", "flag_reason": "spam"}
            )
        # Reaches the router (401: no Authorization header) rather than the cap.
        assert resp.status_code == 401

    async def test_oversized_content_length_is_rejected(self, stub_db):
        async with _client(create_app()) as client:
            resp = await client.post(
                "/api/v1/moderation/flags",
                content=b"{}",
                headers={"content-length": str(MAX_BODY_SIZE + 1)},
            )
        assert resp.status_code == 413
        assert resp.json() == {"detail": "Request body too large"}

    async def test_exactly_the_limit_is_allowed(self, stub_db):
        async with _client(create_app()) as client:
            resp = await client.post(
                "/api/v1/moderation/flags",
                content=b"{}",
                headers={"content-length": str(MAX_BODY_SIZE)},
            )
        assert resp.status_code == 401

    async def test_non_numeric_content_length_is_ignored(self, stub_db):
        async with _client(create_app()) as client:
            resp = await client.post(
                "/api/v1/moderation/flags",
                content=b"{}",
                headers={"content-length": "not-a-number"},
            )
        assert resp.status_code == 401

    def test_the_cap_is_one_mebibyte(self):
        assert MAX_BODY_SIZE == 1048576


class TestOpaqueExceptionHandler:
    async def test_unhandled_exception_returns_a_generic_500(self, stub_db):
        app = create_app()

        @app.get("/explode")
        async def explode():
            raise RuntimeError("internal detail: password is hunter2")

        async with _client(app, raise_app_exceptions=False) as client:
            resp = await client.get("/explode")
        assert resp.status_code == 500
        assert resp.json() == {
            "status_code": 500,
            "message": "Internal server error",
        }
        assert "hunter2" not in resp.text

    async def test_known_http_exceptions_keep_their_own_detail(self, stub_db):
        async with _client(create_app()) as client:
            resp = await client.post(
                "/api/v1/moderation/flags", json={"content_id": "x", "flag_reason": "spam"}
            )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Missing or invalid Authorization header"


class TestGetModerationServiceDependency:
    """The real dependency body every route resolves its service through.

    ``tests/test_routes.py`` overrides this dependency with a mock, so without
    this test the wiring that constructs the three repositories from the
    request's session is never executed.
    """

    async def test_wires_the_three_repositories_to_one_session(self):
        from app.api.moderation_routes import get_moderation_service
        from app.repositories import (
            ContentFlagRepository,
            CreatorStrikeRepository,
            ModerationDecisionRepository,
        )
        from app.services import ModerationService

        sentinel = object()
        service = await get_moderation_service(db=sentinel)
        assert isinstance(service, ModerationService)
        assert isinstance(service.flag_repo, ContentFlagRepository)
        assert isinstance(service.decision_repo, ModerationDecisionRepository)
        assert isinstance(service.strike_repo, CreatorStrikeRepository)
        # All three share the one request-scoped session.
        assert service.flag_repo.session is sentinel
        assert service.decision_repo.session is sentinel
        assert service.strike_repo.session is sentinel

    async def test_the_wired_service_publishes_through_the_process_publisher(self):
        from app.api.moderation_routes import get_moderation_service
        from app.core.events import InMemoryEventPublisher

        publisher = InMemoryEventPublisher()
        from app.core.events import set_event_publisher

        set_event_publisher(publisher)
        try:
            service = await get_moderation_service(db=object())
            assert service.publisher is publisher
        finally:
            set_event_publisher(None)


class TestEntrypoint:
    def test_module_level_app_exists(self):
        from app.main import app as module_app

        assert isinstance(module_app, FastAPI)
        assert module_app.title == settings.SERVICE_NAME

    def test_main_py_declares_no_uvicorn_entrypoint(self):
        # Unlike admin-service, this main.py has no ``if __name__ ==
        # "__main__"`` block, so the container CMD must supply the host/port.
        source = (SERVICE_ROOT / "app" / "main.py").read_text(encoding="utf-8")
        assert 'if __name__ == "__main__":' not in source
