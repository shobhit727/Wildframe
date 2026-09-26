"""Behavioural tests for ``app/main.py`` (admin-service).

Covers the lifespan (DB init, unhealthy-startup warning, graceful-shutdown
drain with its timeout), the two health endpoints, the graceful-shutdown and
body-size middlewares, the /metrics token gate, the opaque 500 handler, and
the production doc-disabling branch of ``create_app``.

No Postgres, Redis or Kafka is required: ``DatabaseManager`` and
``redis.from_url`` are replaced with stubs, and the middleware is exercised
through a real ASGI client.
"""

import asyncio
import pathlib

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core import database as db_mod
from app.core.database import DatabaseManager
from app.core.settings import settings
import app.main as main_mod
from app.main import create_app, lifespan

SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _restore_module_state():
    """Keep the module-level shutdown counters clean between tests."""
    saved = (main_mod._in_flight_requests, main_mod._shutdown_event)
    yield
    main_mod._in_flight_requests, main_mod._shutdown_event = saved


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


def _client(app, raise_app_exceptions: bool = True):
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
        base_url="http://test",
    )


class TestLifespan:
    async def test_startup_initialises_the_database_and_reports_health(self, stub_db):
        calls, _ = stub_db
        app = create_app()
        async with lifespan(app):
            assert calls == ["init", "health_check"]
            assert app.state.shutting_down is False

    async def test_startup_survives_an_unhealthy_database(self, stub_db):
        # The admin service boots degraded rather than refusing to start.
        calls, healthy = stub_db
        healthy["value"] = False
        app = create_app()
        async with lifespan(app):
            assert "init" in calls
            assert app.state.shutting_down is False

    async def test_shutdown_flags_the_app_and_closes_the_database(self, stub_db):
        calls, _ = stub_db
        app = create_app()
        async with lifespan(app):
            pass
        assert app.state.shutting_down is True
        assert calls[-1] == "close"

    async def test_shutdown_sets_the_shutdown_event(self, stub_db):
        # ``lifespan`` rebinds the module global, so read it off the module.
        app = create_app()
        async with lifespan(app):
            assert main_mod._shutdown_event.is_set() is False
        assert main_mod._shutdown_event.is_set() is True

    async def test_shutdown_drains_in_flight_requests(self, stub_db, monkeypatch):
        calls, _ = stub_db
        monkeypatch.setattr("app.main._in_flight_requests", 2)
        sleeps: list[float] = []
        real_sleep = asyncio.sleep

        async def fake_sleep(delay):
            sleeps.append(delay)
            # Simulate the two in-flight requests finishing.
            monkeypatch.setattr("app.main._in_flight_requests", 0)
            await real_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        app = create_app()
        async with lifespan(app):
            pass
        assert sleeps, "drain loop never polled the in-flight counter"
        assert main_mod._MAX_DRAIN_SECONDS == 30

    async def test_shutdown_returns_immediately_when_nothing_is_in_flight(self, stub_db):
        app = create_app()
        async with lifespan(app):
            pass
        assert app.state.shutting_down is True

    async def test_shutdown_gives_up_after_the_drain_timeout(self, stub_db, monkeypatch):
        # A request that never finishes must not block shutdown forever; the
        # drain is bounded, and DatabaseManager.close() still runs afterwards.
        monkeypatch.setattr(main_mod, "_MAX_DRAIN_SECONDS", 0.05)
        monkeypatch.setattr(main_mod, "_in_flight_requests", 3)
        calls, _ = stub_db
        loop = asyncio.get_running_loop()
        started = loop.time()
        app = create_app()
        async with lifespan(app):
            pass
        elapsed = loop.time() - started
        assert elapsed < 2.0, "shutdown waited on a stuck request"
        assert calls[-1] == "close"
        assert app.state.shutting_down is True


class TestCreateApp:
    def test_metadata(self):
        app = create_app()
        assert app.title == "Admin Service"
        assert app.version == settings.SERVICE_VERSION

    def test_running_the_module_directly_starts_uvicorn_on_the_configured_port(
        self, monkeypatch, tmp_path
    ):
        # Covers the ``python -m app.main`` / ``python app/main.py`` entrypoint.
        import runpy
        import sys

        launched: dict = {}

        def fake_run(app_arg, **kwargs):
            launched["app"] = app_arg
            launched.update(kwargs)

        stub = type(sys)("uvicorn")
        stub.run = fake_run
        monkeypatch.setitem(sys.modules, "uvicorn", stub)
        # runpy re-executes the module, so DatabaseManager must not be stubbed.
        monkeypatch.setattr(
            "app.core.database.create_async_engine",
            lambda url, **kw: object(),
            raising=False,
        )
        script = SERVICE_ROOT / "app" / "main.py"
        runpy.run_path(str(script), run_name="__main__")
        assert launched["host"] == settings.SERVER_HOST
        assert launched["port"] == settings.SERVER_PORT
        assert launched["app"].title == "Admin Service"

    def test_docs_are_enabled_outside_production(self):
        app = create_app()
        assert settings.ENVIRONMENT != "production"
        assert app.docs_url == "/docs"
        assert app.redoc_url == "/redoc"
        assert app.openapi_url == "/openapi.json"

    def test_docs_are_disabled_in_production(self, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        app = create_app()
        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None

    def test_admin_router_is_mounted_under_the_v1_prefix(self):
        paths = set(create_app().openapi()["paths"])
        assert "/api/v1/admin/users/moderate" in paths

    def test_each_call_returns_a_distinct_app(self):
        assert create_app() is not create_app()


class TestHealthEndpoints:
    async def test_health_is_status_only_and_touches_no_dependencies(self, stub_db):
        calls, healthy = stub_db
        app = create_app()
        async with _client(app) as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert calls == [], "liveness must not probe the database"

    async def test_health_is_200_even_when_the_database_is_down(self, stub_db):
        _, healthy = stub_db
        healthy["value"] = False
        app = create_app()
        async with _client(app) as client:
            resp = await client.get("/health")
        assert resp.status_code == 200

    async def test_ready_reports_ok_when_database_and_redis_are_healthy(
        self, stub_db, monkeypatch
    ):
        _, healthy = stub_db
        healthy["value"] = True
        monkeypatch.setattr(settings, "REDIS_URL", "redis://stub:6379/0")
        monkeypatch.setattr("app.main.redis.from_url", _fake_redis_factory(ping_ok=True))
        app = create_app()
        async with _client(app) as client:
            resp = await client.get("/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["service"] == "admin-service"
        assert body["version"] == settings.SERVICE_VERSION
        assert body["checks"] == {"database": "ok", "redis": "ok"}

    async def test_ready_is_503_when_the_database_is_down(self, stub_db, monkeypatch):
        _, healthy = stub_db
        healthy["value"] = False
        monkeypatch.setattr(settings, "REDIS_URL", "redis://stub:6379/0")
        monkeypatch.setattr("app.main.redis.from_url", _fake_redis_factory(ping_ok=True))
        app = create_app()
        async with _client(app) as client:
            resp = await client.get("/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["database"] == "down"

    async def test_ready_is_503_when_redis_fails(self, stub_db, monkeypatch):
        monkeypatch.setattr(settings, "REDIS_URL", "redis://stub:6379/0")
        monkeypatch.setattr("app.main.redis.from_url", _fake_redis_factory(ping_ok=False))
        app = create_app()
        async with _client(app) as client:
            resp = await client.get("/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["checks"] == {"database": "ok", "redis": "down"}
        assert body["status"] == "not_ready"

    async def test_ready_skips_redis_when_no_url_is_configured(self, stub_db, monkeypatch):
        monkeypatch.setattr(settings, "REDIS_URL", None)
        app = create_app()
        async with _client(app) as client:
            resp = await client.get("/ready")
        assert resp.status_code == 200
        assert resp.json()["checks"] == {"database": "ok"}

    async def test_ready_treats_an_unreachable_redis_as_down(self, stub_db, monkeypatch):
        monkeypatch.setattr(settings, "REDIS_URL", "redis://stub:6379/0")

        def exploding_factory(*_a, **_kw):
            raise ConnectionError("redis unreachable")

        monkeypatch.setattr("app.main.redis.from_url", exploding_factory)
        app = create_app()
        async with _client(app) as client:
            resp = await client.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["checks"]["redis"] == "down"

    async def test_ready_closes_the_redis_client_it_opened(self, stub_db, monkeypatch):
        clients = []
        monkeypatch.setattr(settings, "REDIS_URL", "redis://stub:6379/0")
        monkeypatch.setattr(
            "app.main.redis.from_url", _fake_redis_factory(ping_ok=True, clients=clients)
        )
        app = create_app()
        async with _client(app) as client:
            await client.get("/ready")
        assert clients and all(c.closed for c in clients)


def _fake_redis_factory(ping_ok: bool, clients=None):
    """Build a stand-in for ``redis.asyncio.from_url``.

    The client is also awaitable (returning itself) so the same double works
    whether the caller writes ``await redis.from_url(...)`` or
    ``redis.from_url(...)``.
    """

    class _Client:
        def __init__(self):
            self.closed = False
            self.pinged = False

        def __await__(self):
            async def _self():
                return self

            return _self().__await__()

        async def ping(self):
            self.pinged = True
            if not ping_ok:
                raise ConnectionError("ping failed")
            return True

        async def close(self):
            self.closed = True

    def factory(*_args, **_kwargs):
        client = _Client()
        if clients is not None:
            clients.append(client)
        return client

    return factory


class TestGracefulShutdownMiddleware:
    async def test_requests_are_refused_while_shutting_down(self, stub_db):
        app = create_app()
        app.state.shutting_down = True
        async with _client(app) as client:
            resp = await client.get("/health")
        assert resp.status_code == 503
        assert resp.json() == {"detail": "Service shutting down"}
        assert resp.headers["Retry-After"] == str(main_mod._MAX_DRAIN_SECONDS)

    async def test_in_flight_counter_returns_to_zero(self, stub_db):
        app = create_app()
        async with _client(app) as client:
            for _ in range(5):
                assert (await client.get("/health")).status_code == 200
        assert main_mod._in_flight_requests == 0

    async def test_in_flight_counter_is_released_when_a_handler_raises(self, stub_db):
        app = create_app()

        @app.get("/boom")
        async def boom():
            raise RuntimeError("kaboom")

        async with _client(app, raise_app_exceptions=False) as client:
            resp = await client.get("/boom")
        assert resp.status_code == 500
        assert main_mod._in_flight_requests == 0


class TestBodySizeLimit:
    async def test_small_body_passes_through(self, stub_db):
        app = create_app()
        async with _client(app) as client:
            resp = await client.post(
                "/api/v1/admin/config",
                json={"key": "k", "value": "v", "config_type": "string"},
            )
        # Reaches the router (401: no Authorization header) rather than the 413 guard.
        assert resp.status_code == 401

    async def test_oversized_content_length_is_rejected_before_parsing(self, stub_db):
        app = create_app()
        async with _client(app) as client:
            resp = await client.post(
                "/api/v1/admin/config",
                content=b"{}",
                headers={"content-length": str(1048576 + 1)},
            )
        assert resp.status_code == 413
        assert resp.json() == {"detail": "Request body too large"}

    async def test_exactly_the_limit_is_allowed(self, stub_db):
        app = create_app()
        async with _client(app) as client:
            resp = await client.post(
                "/api/v1/admin/config",
                content=b"{}",
                headers={"content-length": "1048576"},
            )
        assert resp.status_code == 401

    async def test_non_numeric_content_length_is_ignored(self, stub_db):
        app = create_app()
        async with _client(app) as client:
            resp = await client.post(
                "/api/v1/admin/config",
                content=b"{}",
                headers={"content-length": "not-a-number"},
            )
        assert resp.status_code == 401

    async def test_streamed_body_over_the_limit_is_rejected(self, stub_db):
        app = create_app()

        async def big_stream():
            for _ in range(3):
                yield b"x" * 400_000

        async with _client(app) as client:
            resp = await client.post(
                "/api/v1/admin/config",
                content=big_stream(),
                headers={"content-length": ""},
            )
        assert resp.status_code == 413
        assert resp.json() == {"detail": "Request body too large"}


class TestMetricsGate:
    async def test_metrics_is_open_outside_production(self, stub_db):
        app = create_app()
        async with _client(app) as client:
            resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert b"python_info" in resp.content

    async def test_metrics_requires_the_token_in_production(self, stub_db, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        monkeypatch.setattr(settings, "METRICS_TOKEN", "s3cr3t-metrics")
        app = create_app()
        async with _client(app) as client:
            assert (await client.get("/metrics")).status_code == 401
            assert (
                await client.get("/metrics", headers={"Authorization": "Bearer wrong"})
            ).status_code == 401
            ok = await client.get("/metrics", headers={"Authorization": "Bearer s3cr3t-metrics"})
        assert ok.status_code == 200

    async def test_metrics_is_closed_in_production_without_a_configured_token(
        self, stub_db, monkeypatch
    ):
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        monkeypatch.setattr(settings, "METRICS_TOKEN", "")
        app = create_app()
        async with _client(app) as client:
            resp = await client.get("/metrics")
        assert resp.status_code == 401


class TestOpaqueExceptionHandler:
    async def test_unhandled_exception_returns_a_generic_500(self, stub_db):
        app = create_app()

        @app.get("/explode")
        async def explode():
            raise RuntimeError("secret internal detail: db password is hunter2")

        async with _client(app, raise_app_exceptions=False) as client:
            resp = await client.get("/explode")
        assert resp.status_code == 500
        body = resp.json()
        assert body["message"] == "Internal server error"
        assert body["status_code"] == 500
        assert "hunter2" not in resp.text
        assert "correlation_id" in body

    async def test_known_http_exceptions_keep_their_own_detail(self, stub_db):
        # The opaque 500 handler must not swallow intentional HTTPExceptions.
        app = create_app()
        async with _client(app) as client:
            resp = await client.post("/api/v1/admin/alerts", json={})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Missing or invalid Authorization header"
