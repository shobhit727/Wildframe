"""Tests for app/main.py — the app factory, health probes, middleware and lifespan.

``create_app()`` wires CORS, trusted-host, request-tracing and body-size
middleware, two exception handlers, three unauthenticated endpoints and the
content router. ``lifespan()`` performs the startup DB probe (and the optional
Kafka DLQ-retention task) and the shutdown engine teardown. Both are exercised
here with the database manager stubbed so nothing touches a real server.
"""

import asyncio
import contextlib
import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.logging import correlation_id, request_id
from app.core.settings import settings
from app.main import create_app, lifespan

pytestmark = pytest.mark.unit

# app/main.py builds the app under test locally in several tests; this module
# level instance is a test-only copy — nothing is added to the production app.
app = create_app()

# Mirrors ``MAX_BODY_SIZE`` in create_app()'s limit_body_size middleware.
MAX_BODY_SIZE = 1048576
EXCEEDS_LIMIT = str(MAX_BODY_SIZE + 1)


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """lifespan() calls setup_logging(), which rewrites the root logger."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    saved_ids = (correlation_id.get(), request_id.get())
    try:
        yield
    finally:
        for handler in root.handlers[:]:
            if handler not in saved_handlers:
                handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        correlation_id.set(saved_ids[0])
        request_id.set(saved_ids[1])


def make_client(application, *, raise_app_exceptions: bool = True) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=raise_app_exceptions),
        base_url="http://test",
    )


@contextlib.contextmanager
def record_from(logger_name: str):
    """Capture records emitted on ``logger_name``.

    caplog cannot be used here: wire_observability() (called by create_app)
    installs a JSON handler on the root logger, which replaces the handlers
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


@pytest.fixture
def db_stub(monkeypatch):
    """Stub the DatabaseManager probes used by /health, /ready and lifespan."""
    from app.core.database import db_manager

    calls: dict[str, int] = {"health_check": 0, "close": 0}

    async def health_check():
        calls["health_check"] += 1
        return True

    async def close():
        calls["close"] += 1

    monkeypatch.setattr(db_manager, "health_check", health_check)
    monkeypatch.setattr(db_manager, "close", close)
    return calls


# --------------------------------------------------------------------------- factory
class TestCreateApp:
    def test_metadata_comes_from_settings(self):
        assert app.title == settings.SERVICE_NAME
        assert app.version == settings.SERVICE_VERSION
        assert "content" in app.description.lower()

    def test_docs_are_exposed_outside_production(self, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "development")

        dev_app = create_app()

        assert dev_app.docs_url == "/docs"
        assert dev_app.redoc_url == "/redoc"
        assert dev_app.openapi_url == "/openapi.json"

    def test_docs_are_disabled_in_production(self, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")

        prod_app = create_app()

        assert prod_app.docs_url is None
        assert prod_app.redoc_url is None
        assert prod_app.openapi_url is None

    def test_content_router_is_mounted_under_the_versioned_prefix(self):
        paths = set(app.openapi()["paths"])

        assert "/api/v1/genres" in paths
        assert "/api/v1/content" in paths
        assert "/api/v1/reindex" in paths

    def test_infrastructure_endpoints_are_present(self):
        # /metrics is served by wire_observability and is not in the OpenAPI
        # schema, so walk the middleware stack's mounted routes as well.
        paths = set(app.openapi()["paths"])
        mounted = {getattr(r, "path", None) for r in app.routes}

        assert {"/", "/health", "/ready"} <= paths
        assert "/metrics" in mounted


# ------------------------------------------------------------------- root / probes
class TestRootEndpoint:
    async def test_root_reports_service_identity(self):
        async with make_client(app) as client:
            response = await client.get("/")

        assert response.status_code == 200
        assert response.json() == {
            "service": settings.SERVICE_NAME,
            "version": settings.SERVICE_VERSION,
            "status": "running",
        }


class TestHealthCheck:
    async def test_healthy_when_database_answers(self, db_stub):
        async with make_client(app) as client:
            response = await client.get("/health")

        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "healthy"
        assert body["database"] == "connected"
        assert body["version"] == settings.SERVICE_VERSION
        assert db_stub["health_check"] == 1

    async def test_degraded_when_database_is_down(self, db_stub, monkeypatch):
        from app.core.database import db_manager

        async def unhealthy():
            return False

        monkeypatch.setattr(db_manager, "health_check", unhealthy)

        async with make_client(app) as client:
            response = await client.get("/health")

        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "degraded"
        assert body["database"] == "disconnected"


class TestReadinessCheck:
    async def test_ready_when_database_answers(self, db_stub):
        async with make_client(app) as client:
            response = await client.get("/ready")

        assert response.status_code == 200
        assert response.json() == {"ready": True}

    async def test_not_ready_returns_503_with_reason(self, db_stub, monkeypatch):
        from app.core.database import db_manager

        async def unhealthy():
            return False

        monkeypatch.setattr(db_manager, "health_check", unhealthy)

        async with make_client(app) as client:
            response = await client.get("/ready")

        assert response.status_code == 503
        assert response.json() == {"ready": False, "reason": "database_unavailable"}


# ---------------------------------------------------------------------- middleware
class TestRequestTracingMiddleware:
    async def test_supplied_ids_are_propagated_to_the_response(self):
        async with make_client(app) as client:
            response = await client.get(
                "/", headers={"X-Correlation-ID": "cid-abc", "X-Request-ID": "rid-abc"}
            )

        assert response.headers["X-Correlation-ID"] == "cid-abc"
        assert response.headers["X-Request-ID"] == "rid-abc"

    async def test_ids_are_generated_when_the_client_omits_them(self):
        async with make_client(app) as client:
            first = await client.get("/")
            second = await client.get("/")

        assert first.headers["X-Correlation-ID"]
        assert first.headers["X-Request-ID"]
        assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]

    async def test_supplied_ids_reach_the_logging_context(self):
        """A test-only echo route reads the ContextVars the middleware set."""
        application = create_app()

        @application.get("/_test/ids")
        async def ids():
            return {"correlation_id": correlation_id.get(), "request_id": request_id.get()}

        async with make_client(application) as client:
            response = await client.get(
                "/_test/ids", headers={"X-Correlation-ID": "cid-ctx", "X-Request-ID": "rid-ctx"}
            )

        assert response.json() == {"correlation_id": "cid-ctx", "request_id": "rid-ctx"}


class TestBodySizeLimitMiddleware:
    @pytest.fixture
    def echo_app(self):
        """Test-only app with a DB-free endpoint the middleware can let through."""
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
                headers={"content-type": "application/json", "content-length": EXCEEDS_LIMIT},
            )

        assert response.status_code == 413
        assert response.json() == {"detail": "Request body too large"}

    async def test_body_just_under_the_limit_is_accepted(self, echo_app):
        async with make_client(echo_app) as client:
            response = await client.post(
                "/_test/echo",
                content=b"{}",
                headers={"content-type": "application/json", "content-length": str(MAX_BODY_SIZE)},
            )

        assert response.status_code == 200
        assert response.json() == {"ok": True}

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

    async def test_rejection_precedes_the_endpoint(self, echo_app):
        """The 413 is emitted by middleware: the handler body is never run."""
        calls = []

        @echo_app.post("/_test/never")
        async def never():  # must never execute
            calls.append(1)
            return {"ok": True}

        async with make_client(echo_app) as client:
            response = await client.post(
                "/_test/never",
                content=b"{}",
                headers={"content-type": "application/json", "content-length": EXCEEDS_LIMIT},
            )

        assert response.status_code == 413
        assert calls == []


class TestCorsMiddleware:
    async def test_allowed_origin_is_reflected_with_credentials(self):
        origin = settings.CORS_ALLOWED_ORIGINS[0]

        async with make_client(app) as client:
            response = await client.get("/", headers={"Origin": origin})

        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["access-control-allow-credentials"] == "true"

    async def test_disallowed_origin_gets_no_cors_headers(self):
        async with make_client(app) as client:
            response = await client.get("/", headers={"Origin": "https://evil.example"})

        assert "access-control-allow-origin" not in response.headers


class TestTrustedHostMiddleware:
    async def test_arbitrary_host_is_accepted(self):
        async with make_client(app) as client:
            response = await client.get("/", headers={"Host": "content.internal"})

        assert response.status_code == 200


# -------------------------------------------------------------- exception handlers
class TestValidationExceptionHandler:
    async def test_query_validation_failure_uses_the_error_envelope(self):
        async with make_client(app) as client:
            response = await client.get("/api/v1/content?page=0")

        body = response.json()
        assert response.status_code == 422
        assert body["status_code"] == 422
        assert body["message"] == "Request validation failed"
        assert "detail" in body

    async def test_malformed_path_parameter_uses_the_error_envelope(self):
        async with make_client(app) as client:
            response = await client.get("/api/v1/genres/not-a-uuid")

        body = response.json()
        assert response.status_code == 422
        assert body["message"] == "Request validation failed"


class TestGeneralExceptionHandler:
    async def test_unhandled_exception_returns_opaque_500_outside_production(self):
        application = create_app()

        @application.get("/_test/boom")
        async def boom():
            raise RuntimeError("kaboom")

        async with make_client(application, raise_app_exceptions=False) as client:
            response = await client.get("/_test/boom")

        body = response.json()
        assert response.status_code == 500
        assert body["status_code"] == 500
        assert body["message"] == "Internal server error"
        assert body["detail"] == "kaboom"

    async def test_unhandled_exception_hides_internals_in_production(self, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        application = create_app()

        @application.get("/_test/boom")
        async def boom():
            raise RuntimeError("kaboom")

        async with make_client(application, raise_app_exceptions=False) as client:
            response = await client.get("/_test/boom")

        body = response.json()
        assert response.status_code == 500
        assert body["message"] == "Internal server error"
        assert body["detail"] is None


# ----------------------------------------------------------------------- lifespan
class TestLifespan:
    @pytest.fixture(autouse=True)
    def _isolate_logging_config(self, monkeypatch):
        """lifespan() calls setup_logging(), which reconfigures the root logger.

        Neutralising it keeps the session's log capture intact so startup
        warnings can be asserted on; setup_logging itself is covered by
        tests/test_core_logging.py.
        """
        import app.main as main

        monkeypatch.setattr(main, "setup_logging", lambda: None)

    async def test_startup_probes_the_database_and_shutdown_closes_it(self, db_stub):
        application = create_app()

        async with lifespan(application):
            assert db_stub["health_check"] == 1
            assert db_stub["close"] == 0

        assert db_stub["close"] == 1

    async def test_unhealthy_database_warns_but_still_serves(self, db_stub, monkeypatch):
        from app.core.database import db_manager

        async def unhealthy():
            return False

        monkeypatch.setattr(db_manager, "health_check", unhealthy)

        with record_from("app.main") as records:
            async with lifespan(create_app()):
                # Startup completes even with the database down — the service
                # comes up "degraded" instead of crash-looping.
                pass

        warnings = [r for r in records if r.levelno == logging.WARNING]
        assert [r.getMessage() for r in warnings] == [
            "Database health check failed at startup"
        ]
        assert db_stub["close"] == 1

    async def test_healthy_database_logs_the_connection(self, db_stub):
        with record_from("app.main") as records:
            async with lifespan(create_app()):
                pass

        messages = [r.getMessage() for r in records]
        assert f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}" in messages
        assert "Database connection established" in messages
        assert f"Shutting down {settings.SERVICE_NAME}" in messages

    async def test_kafka_dlq_retention_task_is_scheduled(self, db_stub, monkeypatch):
        import wildframe_events.dlq_retention as dlq

        calls = []

        async def fake_apply(bootstrap_servers, client_id, **kwargs):
            calls.append((bootstrap_servers, client_id))
            return 3

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "kafka.test:9092")
        monkeypatch.setattr(dlq, "apply_dlq_retention", fake_apply)

        async with lifespan(create_app()):
            # create_task() only schedules; yield the loop so it actually runs.
            await asyncio.sleep(0)

        assert calls == [("kafka.test:9092", settings.SERVICE_NAME)]

    async def test_no_dlq_task_without_kafka(self, db_stub, monkeypatch):
        import wildframe_events.dlq_retention as dlq

        async def boom(*args, **kwargs):  # must not run
            raise AssertionError("apply_dlq_retention must not run for the memory publisher")

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")
        monkeypatch.setattr(dlq, "apply_dlq_retention", boom)

        async with lifespan(create_app()):
            await asyncio.sleep(0)

        assert db_stub["close"] == 1

    async def test_the_app_runs_its_own_lifespan(self, db_stub):
        """The created app wires the production lifespan into its router."""
        application = create_app()

        async with application.router.lifespan_context(application):
            assert db_stub["health_check"] == 1

        assert db_stub["close"] == 1
