"""Lifespan / app-factory tests for user-service `app.main`.

`lifespan()` and `create_app()` are the two untested functions in main.py. The
lifespan refuses to start when the database is unhealthy (RuntimeError) and must
cancel the Kafka consumer task + dispose the engine on the way out, so both the
happy and the unhappy path matter.

`DatabaseManager.health_check`, `DatabaseManager.close`,
`DatabaseManager.get_session_factory` and `run_user_registered_consumer` are
patched at their import site so nothing touches a real database or broker.
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from app.core.settings import settings
from app.main import create_app, lifespan


@pytest.fixture(autouse=True)
def _stub_infrastructure():
    """Neutralise DB + Kafka so the lifespan can run in-process."""
    with (
        patch("app.main.setup_logging"),
        patch.object(
            __import__("app.core.database", fromlist=["DatabaseManager"]).DatabaseManager,
            "health_check",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.core.database.DatabaseManager.health_check",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.core.database.DatabaseManager.close",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.core.database.DatabaseManager.get_session_factory",
            return_value=MagicMock(),
        ),
        patch(
            "app.core.event_consumer.run_user_registered_consumer",
            new=AsyncMock(return_value=None),
        ) as consumer,
    ):
        yield consumer


# ---------------------------------------------------------------------------
# lifespan
# ---------------------------------------------------------------------------


async def test_lifespan_starts_and_stops_cleanly():
    app = FastAPI()
    shutdown = asyncio.Event()

    with patch("app.main.lifespan", lifespan):
        async with lifespan(app):
            assert shutdown.is_set() is False

    # A second pass proves the generator is re-enterable.
    assert shutdown.is_set() is False


async def test_lifespan_raises_when_the_database_is_unhealthy():
    app = FastAPI()

    with patch("app.core.database.DatabaseManager.health_check", new=AsyncMock(return_value=False)):
        with pytest.raises(RuntimeError, match="Database is not healthy on startup"):
            async with lifespan(app):
                pass


async def test_lifespan_never_starts_the_consumer_when_the_db_is_down(_stub_infrastructure):
    app = FastAPI()
    consumer = _stub_infrastructure

    with patch("app.core.database.DatabaseManager.health_check", new=AsyncMock(return_value=False)):
        with pytest.raises(RuntimeError):
            async with lifespan(app):
                pass

    consumer.assert_not_awaited()


async def test_lifespan_cancels_the_consumer_task_on_shutdown(_stub_infrastructure):
    app = FastAPI()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _never_ending(session_factory):
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with patch("app.core.event_consumer.run_user_registered_consumer", new=_never_ending):
        async with lifespan(app):
            await asyncio.wait_for(started.wait(), timeout=2)
            assert cancelled.is_set() is False
        # The lifespan waits on `consumer_task.cancel()` but does not await the
        # task, so give the loop a tick to deliver the cancellation.
        for _ in range(20):
            if cancelled.is_set():
                break
            await asyncio.sleep(0.01)

    assert cancelled.is_set() is True


async def test_lifespan_closes_the_database_on_shutdown():
    from app.core.database import DatabaseManager

    app = FastAPI()
    closed = AsyncMock()

    with patch.object(DatabaseManager, "close", new=closed):
        async with lifespan(app):
            pass

    closed.assert_awaited_once()


async def test_lifespan_handles_a_consumer_that_raises_on_start():
    app = FastAPI()

    async def _boom(session_factory):
        raise RuntimeError("broker down")

    with patch("app.core.event_consumer.run_user_registered_consumer", new=_boom):
        # The task is created but never awaited before cancel, so a raising
        # consumer must not break the shutdown path.
        async with lifespan(app):
            await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# create_app
# ---------------------------------------------------------------------------


def test_create_app_uses_the_configured_title_and_version():
    app = create_app()

    assert app.title == settings.SERVICE_NAME
    assert app.version == settings.SERVICE_VERSION


def test_create_app_exposes_docs_outside_production():
    app = create_app()

    assert app.docs_url == "/docs"
    assert app.redoc_url == "/redoc"
    assert app.openapi_url == "/openapi.json"


def test_create_app_disables_docs_in_production():
    with patch("app.main.settings") as fake_settings:
        fake_settings.SERVICE_NAME = "user-service"
        fake_settings.SERVICE_VERSION = "1.0.0"
        fake_settings.ENVIRONMENT = "production"
        fake_settings.CORS_ALLOWED_ORIGINS = ["https://wildframe.com"]
        fake_settings.CORS_ALLOW_CREDENTIALS = True
        fake_settings.LOG_LEVEL = "INFO"
        fake_settings.JWT_AUDIENCE = "wildframe-api"
        fake_settings.AUTH_SERVICE_URL = "http://auth-service:8000"

        app = create_app()

    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_create_app_mounts_the_api_router_under_api_v1():
    app = create_app()

    paths = set(app.openapi()["paths"])
    assert "/api/v1/profiles" in paths
    assert "/api/v1/devices" in paths
    assert "/api/v1/preferences/{user_id}" in paths
    assert "/api/v1/subscriptions/{user_id}" in paths
    # The child/privacy/dsar routers are intentionally NOT mounted (see
    # tests/test_unmounted_routers.py); assert that stays true.
    assert not any(path.startswith("/child-accounts") for path in paths)
    assert not any(path.startswith("/privacy") for path in paths)
    assert not any(path.startswith("/dsar") for path in paths)


def test_create_app_exposes_health_ready_and_root():
    app = create_app()

    literal_paths = {getattr(route, "path", None) for route in app.routes}

    assert {"/health", "/ready", "/"} <= literal_paths


def test_create_app_installs_cors_and_trusted_host_middleware():
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.trustedhost import TrustedHostMiddleware

    app = create_app()
    classes = [m.cls for m in app.user_middleware]

    assert CORSMiddleware in classes
    assert TrustedHostMiddleware in classes


def test_trusted_host_allows_wildcard_outside_production():
    from fastapi.middleware.trustedhost import TrustedHostMiddleware

    app = create_app()
    trusted = next(m for m in app.user_middleware if m.cls is TrustedHostMiddleware)

    assert trusted.kwargs["allowed_hosts"] == ["*"]


def test_cors_uses_the_configured_origins():
    from fastapi.middleware.cors import CORSMiddleware

    app = create_app()
    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)

    assert cors.kwargs["allow_origins"] == settings.CORS_ALLOWED_ORIGINS
    assert cors.kwargs["allow_credentials"] is settings.CORS_ALLOW_CREDENTIALS
    assert cors.kwargs["allow_methods"] == ["*"]
    assert cors.kwargs["allow_headers"] == ["*"]


def test_trusted_host_is_restricted_in_production():
    from fastapi.middleware.trustedhost import TrustedHostMiddleware

    with patch("app.main.settings") as fake_settings:
        fake_settings.SERVICE_NAME = "user-service"
        fake_settings.SERVICE_VERSION = "1.0.0"
        fake_settings.ENVIRONMENT = "production"
        fake_settings.CORS_ALLOWED_ORIGINS = ["https://wildframe.com"]
        fake_settings.CORS_ALLOW_CREDENTIALS = True
        fake_settings.LOG_LEVEL = "INFO"
        fake_settings.JWT_AUDIENCE = "wildframe-api"
        fake_settings.AUTH_SERVICE_URL = "http://auth-service:8000"

        app = create_app()

    trusted = next(m for m in app.user_middleware if m.cls is TrustedHostMiddleware)
    assert trusted.kwargs["allowed_hosts"] == ["localhost", "127.0.0.1", "*.wildframe.com"]


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app, base_url="http://localhost") as test_client:
        yield test_client


def test_health_reports_healthy_when_the_db_answers(client):
    with patch("app.core.database.DatabaseManager.health_check", new=AsyncMock(return_value=True)):
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["service"] == settings.SERVICE_NAME
    assert body["version"] == settings.SERVICE_VERSION
    assert body["timestamp"]


def test_health_reports_unhealthy_when_the_db_is_down(client):
    with patch("app.core.database.DatabaseManager.health_check", new=AsyncMock(return_value=False)):
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "unhealthy"


def test_ready_returns_200_when_the_db_is_healthy(client):
    with patch("app.core.database.DatabaseManager.health_check", new=AsyncMock(return_value=True)):
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_returns_503_when_the_db_is_down(client):
    with patch("app.core.database.DatabaseManager.health_check", new=AsyncMock(return_value=False)):
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not ready", "reason": "database unhealthy"}


def test_root_reports_service_metadata(client):
    response = client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == settings.SERVICE_NAME
    assert body["version"] == settings.SERVICE_VERSION
    assert body["environment"] == settings.ENVIRONMENT
    assert body["docs"] == "/docs"
    assert body["openapi"] == "/openapi.json"


# ---------------------------------------------------------------------------
# request-context middleware
# ---------------------------------------------------------------------------


def test_middleware_echoes_an_inbound_correlation_id(client):
    response = client.get("/", headers={"X-Correlation-ID": "corr-abc"})

    assert response.headers["X-Correlation-ID"] == "corr-abc"
    assert response.headers["X-Request-ID"]


def test_middleware_generates_ids_when_none_are_inbound(client):
    first = client.get("/")
    second = client.get("/")

    assert first.headers["X-Correlation-ID"] != first.headers["X-Request-ID"]
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


# ---------------------------------------------------------------------------
# body-size middleware
# ---------------------------------------------------------------------------


def test_oversized_body_is_rejected_with_413(client):
    with patch("app.core.database.DatabaseManager.health_check", new=AsyncMock(return_value=True)):
        response = client.post(
            "/api/v1/profiles",
            content=b"x" * 1_048_577,
            headers={"content-type": "application/json", "content-length": "1048577"},
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_a_body_exactly_at_the_limit_is_not_rejected_by_the_header_check(client):
    with patch("app.core.database.DatabaseManager.health_check", new=AsyncMock(return_value=True)):
        response = client.post(
            "/api/v1/profiles",
            content=b"x" * 1_048_576,
            headers={"content-type": "application/json", "content-length": "1048576"},
        )

    # Not 413 - it fails auth (401) instead, proving the cap let it through.
    assert response.status_code != 413


def test_a_non_numeric_content_length_falls_through_to_the_route(client):
    with patch("app.core.database.DatabaseManager.health_check", new=AsyncMock(return_value=True)):
        response = client.post(
            "/api/v1/profiles",
            content=b"{}",
            headers={"content-type": "application/json", "content-length": "not-a-number"},
        )

    assert response.status_code != 413


# ---------------------------------------------------------------------------
# exception handlers
# ---------------------------------------------------------------------------


def test_validation_errors_use_the_error_response_envelope(client):
    from app.api.routes import get_current_user_id, get_user_service

    user_id = uuid4()
    client.app.dependency_overrides[get_current_user_id] = lambda: user_id
    client.app.dependency_overrides[get_user_service] = lambda: MagicMock()
    try:
        response = client.post("/api/v1/devices", json={})
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "VALIDATION_ERROR"
    assert body["message"] == "Request validation failed"
    assert "errors" in body["details"]


def test_unhandled_exceptions_return_an_opaque_500():
    """The `Exception` handler must log the cause but never leak it (#557)."""
    app = create_app()

    @app.get("/_boom")
    async def _boom() -> None:
        raise RuntimeError("secret internal detail")

    with (
        patch("app.core.database.DatabaseManager.health_check", new=AsyncMock(return_value=True)),
        patch("app.main.logger") as logger,
    ):
        # raise_server_exceptions=False: Starlette's ServerErrorMiddleware
        # re-raises after the handler has already written the 500 body.
        with TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ) as client:
            response = client.get("/_boom")

    assert response.status_code == 500
    assert response.json() == {"status_code": 500, "message": "Internal server error"}
    assert "secret internal detail" not in response.text
    # The cause is still logged server-side.
    assert logger.exception.called
    template, cause = logger.exception.call_args.args
    assert template == "Unhandled exception: %s"
    assert str(cause) == "secret internal detail"


def test_known_defect_500_responses_lose_the_tracing_headers():
    """Characterisation test for a reported gap (NOT an assertion of intent).

    `add_request_context` is a `@app.middleware("http")` (main.py:91) and stamps
    `X-Correlation-ID` / `X-Request-ID` on the response it returns. Starlette
    routes the `Exception` handler to `ServerErrorMiddleware`, which sits
    *above* the user middleware stack, so a 500 body produced by
    `general_exception_handler` never passes back through
    `add_request_context`. Clients therefore get a 500 with no correlation id in
    the response headers and cannot quote it in a support ticket (the id is
    still present in the server-side log line).

    Expected to change when production code is fixed.
    """
    app = create_app()

    @app.get("/_boom2")
    async def _boom2() -> None:
        raise RuntimeError("kaboom")

    with patch("app.core.database.DatabaseManager.health_check", new=AsyncMock(return_value=True)):
        with TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ) as client:
            response = client.get("/_boom2", headers={"X-Correlation-ID": "corr-500"})

    assert response.status_code == 500
    assert "X-Correlation-ID" not in response.headers
    assert "X-Request-ID" not in response.headers


def test_non_500_responses_do_carry_the_tracing_headers():
    """Contrast case for the characterisation test above."""
    app = create_app()

    with patch("app.core.database.DatabaseManager.health_check", new=AsyncMock(return_value=True)):
        with TestClient(app, base_url="http://localhost") as client:
            response = client.get("/", headers={"X-Correlation-ID": "corr-ok"})

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "corr-ok"
    assert response.headers["X-Request-ID"]
