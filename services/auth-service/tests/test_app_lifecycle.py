"""Behavioural tests for ``app.main`` — ``create_app()`` and ``lifespan()``.

Redis/Kafka/the database engine are all stubbed, so no infrastructure is
required: the tests drive the real ASGI app through Starlette's ``TestClient``
(lifespan included) and assert the real behaviour of the middleware stack,
exception handlers and health endpoints.
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from app.core.database import DatabaseManager
from app.core.settings import settings
from app.models import Base
from app.security import TokenManager
from app.security import jwks as jwks_module
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import app.main at collection time. Importing it runs create_app() ->
# wire_observability() -> obs_setup_logging(), which replaces the root logger's
# handlers. Doing that here rather than inside a test keeps pytest's caplog
# handler (installed per test) from being torn out mid-test.
from app.main import create_app, lifespan  # noqa: E402


async def _null_consumer(_session_factory):
    """Stand-in for the long-running user.moderated consumer."""
    return None


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def app_engine(tmp_path):
    return create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/lifecycle.db")


@pytest.fixture
def wired_db(app_engine, monkeypatch):
    """Point ``DatabaseManager`` at a throwaway SQLite file (no Postgres)."""

    async def _create():
        async with app_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    _run(_create())

    session_factory = async_sessionmaker(
        app_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(DatabaseManager, "_engine", app_engine, raising=False)
    monkeypatch.setattr(DatabaseManager, "_session_factory", session_factory, raising=False)
    monkeypatch.setattr(DatabaseManager, "get_engine", classmethod(lambda cls: app_engine))
    monkeypatch.setattr(
        DatabaseManager,
        "get_session_factory",
        classmethod(lambda cls: session_factory),
    )
    return app_engine


@pytest.fixture
def capture_app_main_logs():
    """Re-enable propagation for ``app.main`` so caplog can see its records.

    ``wire_observability`` reconfigures logging at import time with
    ``propagate=False`` on the service loggers, which hides them from pytest's
    caplog handler for the rest of the session.
    """
    logger = logging.getLogger("app.main")
    saved = (logger.handlers[:], logger.level, logger.propagate)
    logger.handlers = []
    logger.setLevel(logging.INFO)
    logger.propagate = True
    yield
    logger.handlers[:] = saved[0]
    logger.setLevel(saved[1])
    logger.propagate = saved[2]


def _build_client(wired_db, **kwargs):
    """Build a TestClient over a freshly created app, lifespan and all."""
    from app.core import event_consumer

    async def _consumer(_session_factory):
        return None

    with patch.object(
        event_consumer, "run_user_moderation_consumer", _consumer
    ), patch("app.main.setup_logging"):
        with TestClient(create_app(), **kwargs) as test_client:
            yield test_client


@pytest.fixture
def client(wired_db):
    yield from _build_client(wired_db)


@pytest.fixture
def lenient_client(wired_db):
    """A client that returns 500 responses instead of re-raising them.

    Starlette's ServerErrorMiddleware re-raises after the registered
    ``Exception`` handler produces its response, which ``TestClient`` surfaces
    unless ``raise_server_exceptions`` is disabled.
    """
    yield from _build_client(wired_db, raise_server_exceptions=False)


# --------------------------------------------------------------------------
# lifespan
# --------------------------------------------------------------------------


def _lifespan():
    return lifespan


class TestLifespanStartup:
    def test_startup_logs_the_service_banner_and_passes_all_checks(
        self, wired_db, caplog, capture_app_main_logs
    ):
        caplog.set_level(logging.INFO)
        close = AsyncMock()

        async def _scenario():
            with patch.object(
                DatabaseManager, "health_check", AsyncMock(return_value=True)
            ), patch.object(DatabaseManager, "close", close), patch(
                "app.core.event_consumer.run_user_moderation_consumer", _null_consumer
            ), patch("app.main.setup_logging"), patch("app.main.wire_observability"):
                async with _lifespan()(None):
                    pass

        _run(_scenario())

        assert f"Starting {settings.SERVICE_NAME} v{settings.SERVICE_VERSION}" in caplog.text
        assert f"Environment: {settings.ENVIRONMENT}" in caplog.text
        assert "All startup checks passed" in caplog.text
        close.assert_awaited_once()
        assert "Shutdown complete" in caplog.text

    def test_startup_configures_logging(self, wired_db):
        with patch.object(
            DatabaseManager, "health_check", AsyncMock(return_value=True)
        ), patch.object(DatabaseManager, "close", AsyncMock()), patch(
            "app.core.event_consumer.run_user_moderation_consumer", _null_consumer
        ), patch("app.main.setup_logging") as setup:

            async def _scenario():
                async with _lifespan()(None):
                    pass

            _run(_scenario())

        setup.assert_called_once_with()

    def test_unhealthy_database_aborts_startup(self, wired_db):
        with patch.object(
            DatabaseManager, "health_check", AsyncMock(return_value=False)
        ), patch.object(DatabaseManager, "close", AsyncMock()), patch(
            "app.core.event_consumer.run_user_moderation_consumer", _null_consumer
        ), patch("app.main.setup_logging"):

            async def _scenario():
                async with _lifespan()(None):
                    pass

            with pytest.raises(RuntimeError, match="Database is not healthy on startup"):
                _run(_scenario())

    def test_consumer_is_started_with_the_session_factory(self, wired_db):
        captured = {}

        async def _consumer(session_factory):
            captured["factory"] = session_factory

        with patch.object(
            DatabaseManager, "health_check", AsyncMock(return_value=True)
        ), patch.object(DatabaseManager, "close", AsyncMock()), patch(
            "app.core.event_consumer.run_user_moderation_consumer", _consumer
        ), patch("app.main.setup_logging"):

            async def _scenario():
                async with _lifespan()(None):
                    await asyncio.sleep(0)

            _run(_scenario())

        assert "factory" in captured
        assert callable(captured["factory"])

    def test_consumer_task_is_cancelled_on_shutdown(self, wired_db):
        cancelled = asyncio.Event()

        async def _consumer(_session_factory):
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def _scenario():
            with patch.object(
                DatabaseManager, "health_check", AsyncMock(return_value=True)
            ), patch.object(DatabaseManager, "close", AsyncMock()), patch(
                "app.core.event_consumer.run_user_moderation_consumer", _consumer
            ), patch("app.main.setup_logging"), patch("app.main.wire_observability"):
                async with _lifespan()(None):
                    await asyncio.sleep(0.05)

        _run(_scenario())

        assert cancelled.is_set()

    def test_startup_failure_skips_shutdown_teardown(self, wired_db):
        close = AsyncMock()
        with patch.object(
            DatabaseManager, "health_check", AsyncMock(return_value=False)
        ), patch.object(DatabaseManager, "close", close), patch(
            "app.core.event_consumer.run_user_moderation_consumer", _null_consumer
        ), patch("app.main.setup_logging"):

            async def _scenario():
                async with _lifespan()(None):
                    pass

            with pytest.raises(RuntimeError):
                _run(_scenario())

        close.assert_not_awaited()


# --------------------------------------------------------------------------
# create_app: metadata, middleware, routes
# --------------------------------------------------------------------------


class TestCreateAppWiring:
    def test_app_metadata_comes_from_settings(self):
        app = create_app()

        assert app.title == settings.SERVICE_NAME
        assert app.version == settings.SERVICE_VERSION

    def test_openapi_is_exposed_outside_production(self):
        app = create_app()

        assert app.docs_url == "/docs"
        assert app.redoc_url == "/redoc"
        assert app.openapi_url == "/openapi.json"

    def test_openapi_is_disabled_in_production(self, monkeypatch):
        from app.main import create_app

        monkeypatch.setattr(settings, "ENVIRONMENT", "production")

        app = create_app()

        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None

    def test_trusted_host_is_wildcard_outside_production(self):
        app = create_app()

        trusted = [m for m in app.user_middleware if m.cls.__name__ == "TrustedHostMiddleware"]
        assert trusted, "TrustedHostMiddleware must be installed"
        assert trusted[0].kwargs["allowed_hosts"] == ["*"]

    def test_trusted_host_is_pinned_in_production(self, monkeypatch):
        from app.main import create_app

        monkeypatch.setattr(settings, "ENVIRONMENT", "production")

        app = create_app()

        trusted = [m for m in app.user_middleware if m.cls.__name__ == "TrustedHostMiddleware"]
        assert trusted[0].kwargs["allowed_hosts"] == [
            "localhost",
            "*.wildframe.com",
        ]

    def test_api_router_is_mounted_under_the_v1_prefix(self):
        app = create_app()

        paths = set(app.openapi()["paths"])
        assert "/api/v1/auth/login" in paths
        assert "/api/v1/privacy/notices" in paths
        assert "/api/v1/auth/mfa/setup" in paths

    def test_cors_middleware_is_installed(self):
        app = create_app()

        cors = [m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
        assert cors
        assert cors[0].kwargs["allow_origins"] == settings.CORS_ALLOWED_ORIGINS
        assert cors[0].kwargs["allow_credentials"] == settings.CORS_ALLOW_CREDENTIALS

    def test_observability_is_wired_with_service_metadata(self):
        from app.main import create_app

        with patch("app.main.wire_observability") as wire:
            create_app()

        assert wire.call_count == 1
        assert wire.call_args.kwargs["service_name"] == settings.SERVICE_NAME
        assert wire.call_args.kwargs["log_level"] == settings.LOG_LEVEL


# --------------------------------------------------------------------------
# request-context middleware
# --------------------------------------------------------------------------


class TestRequestContextMiddleware:
    def test_generates_correlation_and_request_ids(self, client):
        response = client.get("/health")

        assert response.headers["X-Correlation-ID"]
        assert response.headers["X-Request-ID"]

    def test_echoes_an_inbound_correlation_id(self, client):
        response = client.get("/health", headers={"X-Correlation-ID": "trace-abc"})

        assert response.headers["X-Correlation-ID"] == "trace-abc"

    def test_generated_ids_differ_between_requests(self, client):
        first = client.get("/health").headers["X-Request-ID"]
        second = client.get("/health").headers["X-Request-ID"]

        assert first != second

    def test_request_id_is_regenerated_per_request(self, client):
        first = client.get("/health").headers["X-Request-ID"]
        second = client.get("/health").headers["X-Request-ID"]

        assert first
        assert second
        assert first != second


# --------------------------------------------------------------------------
# body-size limit middleware
# --------------------------------------------------------------------------


class TestBodySizeLimit:
    def test_oversized_content_length_is_rejected(self, client):
        response = client.post(
            "/api/v1/auth/login",
            content=b"{}",
            headers={"content-length": "2000000", "content-type": "application/json"},
        )

        assert response.status_code == 413
        assert response.json() == {"detail": "Request body too large"}

    def test_boundary_content_length_is_accepted(self, client):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "bound@example.com", "password": "whatever"},
            headers={"content-length": "1048576"},
        )

        assert response.status_code != 413

    def test_non_numeric_content_length_is_ignored(self, client):
        response = client.post(
            "/api/v1/auth/login",
            content=b'{"email":"a@b.com","password":"x"}',
            headers={"content-length": "not-a-number", "content-type": "application/json"},
        )

        # The endpoint was reached, so the malformed header did not short-circuit.
        assert response.status_code in (401, 422)

    def test_declared_oversized_body_is_rejected_before_parsing(self, client):
        """The content-length guard must reject before the body is read."""
        response = client.post(
            "/api/v1/auth/login",
            content=b'{"email":"a@b.com","password":"x"}',
            headers={"content-length": "2000000", "content-type": "application/json"},
        )

        assert response.status_code == 413
        assert response.json() == {"detail": "Request body too large"}

# --------------------------------------------------------------------------
# validation + opaque 500 handler
# --------------------------------------------------------------------------


class TestExceptionHandlers:
    def test_validation_error_uses_the_error_response_envelope(self, client):
        response = client.post("/api/v1/auth/login", json={"email": "not-an-email"})

        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "VALIDATION_ERROR"
        assert body["message"] == "Request validation failed"
        assert isinstance(body["details"]["errors"], list)

    def test_validation_error_body_is_json_serialisable(self, client):
        response = client.post("/api/v1/auth/login", json={"email": "x", "password": 5})

        assert response.status_code == 422
        json.dumps(response.json())  # must not raise

    def test_unhandled_exception_returns_an_opaque_500(self, lenient_client):
        from app.api.routes.auth import get_auth_service

        def _explode():
            raise RuntimeError("db password hunter2 leaked")

        lenient_client.app.dependency_overrides[get_auth_service] = _explode
        try:
            response = lenient_client.post(
                "/api/v1/auth/login",
                json={"email": "boom@example.com", "password": "Secret1234!"},
            )
        finally:
            lenient_client.app.dependency_overrides.pop(get_auth_service, None)

        assert response.status_code == 500
        assert response.json() == {
            "status_code": 500,
            "message": "Internal server error",
        }
        assert "hunter2" not in response.text


# --------------------------------------------------------------------------
# health / ready / root / jwks
# --------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_reports_healthy_when_the_database_answers(self, client):
        with patch.object(
            DatabaseManager, "health_check", AsyncMock(return_value=True)
        ):
            response = client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert body["service"] == settings.SERVICE_NAME
        assert body["version"] == settings.SERVICE_VERSION
        assert body["checks"]["database"]["status"] == "healthy"
        assert body["timestamp"]

    def test_reports_unhealthy_when_the_database_is_down(self, client):
        with patch.object(
            DatabaseManager, "health_check", AsyncMock(return_value=False)
        ):
            response = client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "unhealthy"
        assert body["checks"]["database"]["status"] == "unhealthy"

    def test_health_passes_through_a_real_database_check(self, client):
        # No patching: the SQLite engine really answers SELECT 1.
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


class TestReadinessEndpoint:
    def test_ready_when_the_database_answers(self, client):
        with patch.object(
            DatabaseManager, "health_check", AsyncMock(return_value=True)
        ):
            response = client.get("/ready")

        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    def test_503_with_a_reason_when_the_database_is_down(self, client):
        with patch.object(
            DatabaseManager, "health_check", AsyncMock(return_value=False)
        ):
            response = client.get("/ready")

        assert response.status_code == 503
        assert response.json() == {"status": "not ready", "reason": "database unhealthy"}

    def test_ready_passes_through_a_real_database_check(self, client):
        response = client.get("/ready")

        assert response.status_code == 200
        assert response.json() == {"status": "ready"}


class TestRootEndpoint:
    def test_root_describes_the_service(self, client):
        response = client.get("/")

        assert response.status_code == 200
        assert response.json() == {
            "service": settings.SERVICE_NAME,
            "version": settings.SERVICE_VERSION,
            "environment": settings.ENVIRONMENT,
            "docs": "/docs",
            "openapi": "/openapi.json",
        }


class TestJwksEndpoint:
    def test_publishes_the_current_signing_key(self, client):
        jwks_module.reset_cache()
        try:
            response = client.get("/.well-known/jwks.json")
        finally:
            jwks_module.reset_cache()

        assert response.status_code == 200
        keys = response.json()["keys"]
        assert len(keys) == 1
        assert keys[0]["kid"] == settings.JWT_KEY_ID
        assert keys[0]["kty"] == "RSA"
        assert keys[0]["alg"] == settings.JWT_ALGORITHM
        assert "n" in keys[0]
        assert "e" in keys[0]

    def test_no_private_material_is_exposed(self, client):
        jwks_module.reset_cache()
        try:
            body = client.get("/.well-known/jwks.json").text
        finally:
            jwks_module.reset_cache()

        assert "PRIVATE KEY" not in body
        assert '"d"' not in body  # private exponent must never be published


class TestJwksBackedTokenFlow:
    """Guards the crypto path the jwks endpoint and every token check rely on."""

    def test_access_token_round_trips_against_the_published_jwk(self):
        user_id = uuid4()
        token = TokenManager.create_access_token(user_id, "round@example.com", 0)

        payload = TokenManager.verify_token(token, token_type="access")

        assert payload["user_id"] == str(user_id)
        assert payload["aud"] == settings.JWT_AUDIENCE
        assert jwks_module.get_jwk_for_kid(settings.JWT_KEY_ID) is not None
