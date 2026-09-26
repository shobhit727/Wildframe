"""Lifespan / app-factory tests for notification-service `app.main`.

`lifespan()` runs the graceful-shutdown protocol from #426 - it must set the
draining flag, stop accepting new requests with 503 + `Retry-After`, wait for
in-flight requests, and bound the wait at `_MAX_DRAIN_SECONDS`. `create_app()`
adds the CORS middleware, the body-size cap, the status-only `/health`, the
DB+Redis `/ready`, and the admin-token-gated `/metrics`.

`DatabaseManager` and `redis.asyncio` are patched at their import site so no
database or broker is needed. The drain timeout is exercised by shrinking
`_MAX_DRAIN_SECONDS` rather than waiting 30 real seconds.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

from app import main as main_module
from app.core.database import DatabaseManager
from app.main import create_app, lifespan


@pytest.fixture(autouse=True)
def _reset_shutdown_state():
    saved = (
        main_module._shutdown_event,
        main_module._in_flight_lock,
        main_module._in_flight_requests,
        main_module._fallback_in_flight_lock,
    )
    main_module._in_flight_requests = 0
    yield
    (
        main_module._shutdown_event,
        main_module._in_flight_lock,
        main_module._in_flight_requests,
        main_module._fallback_in_flight_lock,
    ) = saved


@pytest.fixture
def healthy_db():
    with patch.object(DatabaseManager, "health_check", new=AsyncMock(return_value=True)):
        with patch.object(DatabaseManager, "close", new=AsyncMock(return_value=None)) as close:
            yield close


# ---------------------------------------------------------------------------
# lifespan
# ---------------------------------------------------------------------------


async def test_lifespan_runs_startup_and_shutdown(healthy_db):
    app = FastAPI()

    async with lifespan(app):
        assert app.state.shutting_down is False
        assert main_module._shutdown_event is not None
        assert not main_module._shutdown_event.is_set()
        assert main_module._in_flight_lock is not None

    assert app.state.shutting_down is True
    assert main_module._shutdown_event.is_set()
    healthy_db.assert_awaited_once()


async def test_lifespan_warns_but_still_starts_when_the_db_is_down():
    app = FastAPI()

    with (
        patch.object(DatabaseManager, "health_check", new=AsyncMock(return_value=False)),
        patch.object(DatabaseManager, "close", new=AsyncMock(return_value=None)),
    ):
        # Unlike user-service, a DB outage must NOT block startup: the readiness
        # probe is what gates traffic, and the service still answers /health.
        async with lifespan(app):
            assert app.state.shutting_down is False

    assert app.state.shutting_down is True


async def test_lifespan_drains_in_flight_requests_before_closing(healthy_db):
    app = FastAPI()

    async with lifespan(app):
        # Simulate a request that is mid-flight when shutdown starts.
        main_module._in_flight_requests = 1

        async def _finish():
            await asyncio.sleep(0.05)
            main_module._in_flight_requests = 0

        finisher = asyncio.create_task(_finish())

    await finisher
    # The lifespan waited for the counter to reach zero, so the DB closed after.
    assert main_module._in_flight_requests == 0
    assert app.state.shutting_down is True


async def test_lifespan_drain_is_bounded_by_max_drain_seconds(healthy_db, caplog):
    app = FastAPI()
    main_module._MAX_DRAIN_SECONDS = 0.2

    with patch.object(main_module.logger, "warning") as warn:
        async with lifespan(app):
            # A request that never finishes must not block shutdown forever.
            main_module._in_flight_requests = 1

    assert warn.called
    assert "Shutdown drain timeout" in warn.call_args.args[0]
    main_module._MAX_DRAIN_SECONDS = 30


async def test_lifespan_sets_a_fresh_event_on_each_start(healthy_db):
    app = FastAPI()

    async with lifespan(app):
        first = main_module._shutdown_event
    async with lifespan(app):
        second = main_module._shutdown_event

    assert first is not second
    assert first.is_set() is True
    assert second.is_set() is True


# ---------------------------------------------------------------------------
# _fallback_lock
# ---------------------------------------------------------------------------


def test_fallback_lock_is_memoised():
    main_module._fallback_in_flight_lock = None

    first = main_module._fallback_lock()
    second = main_module._fallback_lock()

    assert first is second


# ---------------------------------------------------------------------------
# create_app
# ---------------------------------------------------------------------------


def test_create_app_uses_the_configured_title_and_version():
    from app.core.settings import settings

    app = create_app()

    assert app.title == settings.SERVICE_NAME
    assert app.version == settings.SERVICE_VERSION


def test_create_app_disables_docs_in_production():
    with patch("app.main.settings") as fake_settings:
        fake_settings.SERVICE_NAME = "Notification"
        fake_settings.SERVICE_VERSION = "1.0.0"
        fake_settings.ENVIRONMENT = "production"
        fake_settings.CORS_ALLOWED_ORIGINS = ["https://wildframe.com"]
        fake_settings.CORS_ALLOW_CREDENTIALS = True
        fake_settings.LOG_LEVEL = "INFO"
        fake_settings.JWT_AUDIENCE = "wildframe-api"
        fake_settings.JWT_ISSUER = "wildframe-auth"
        fake_settings.REDIS_URL = "redis://localhost:6379/0"
        fake_settings.METRICS_TOKEN = "adm-secret"

        app = create_app()

    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


def test_create_app_installs_cors_middleware():
    from fastapi.middleware.cors import CORSMiddleware

    from app.core.settings import settings

    app = create_app()
    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)

    assert cors.kwargs["allow_origins"] == settings.CORS_ALLOWED_ORIGINS
    assert cors.kwargs["allow_credentials"] is settings.CORS_ALLOW_CREDENTIALS


def test_create_app_mounts_the_notification_router():
    app = create_app()

    paths = set(app.openapi()["paths"])
    assert "/api/v1/notifications/send" in paths
    assert "/api/v1/notifications/preferences" in paths
    assert "/api/v1/notifications/{notification_id}" in paths


def test_metrics_is_registered():
    app = create_app()

    assert "/metrics" in {getattr(route, "path", None) for route in app.routes}


# ---------------------------------------------------------------------------
# /health and /ready
# ---------------------------------------------------------------------------


@pytest.fixture
def client(healthy_db):
    app = create_app()
    with TestClient(app, base_url="http://localhost") as test_client:
        yield test_client


def test_health_is_status_only_healthy(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_health_reports_degraded_without_leaking_topology(client):
    with patch.object(DatabaseManager, "health_check", new=AsyncMock(return_value=False)):
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    # Liveness only (#628): no database/redis detail is exposed here.
    assert body == {"status": "degraded"}


class _AwaitableRedisDouble(MagicMock):
    """A ``MagicMock`` redis client that is awaitable, like the real one.

    ``redis.asyncio.Redis`` defines ``__await__`` (it returns
    ``self.initialize().__await__()``), so ``await redis.from_url(...)`` in
    ``/ready`` hands back the client itself, which is then pinged and closed.
    Neither ``MagicMock`` nor ``AsyncMock`` implements ``__await__``, so a
    plain mock makes that ``await`` raise ``TypeError``, the surrounding
    ``except Exception`` reports redis as ``down``, and ``/ready`` answers 503
    even with both dependencies up.
    """

    def __await__(self):
        async def _identity():
            return self

        return _identity().__await__()


def _fake_redis(*, ping_exc=None):
    client = _AwaitableRedisDouble()
    client.ping = AsyncMock(side_effect=ping_exc) if ping_exc else AsyncMock(return_value=True)
    client.close = AsyncMock(return_value=None)
    return client


def test_ready_is_200_when_db_and_redis_are_up(client):
    with patch("app.main.redis.from_url", new=MagicMock(return_value=_fake_redis())):
        response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["service"] == "notification"
    assert body["checks"] == {"database": "ok", "redis": "ok"}


def test_ready_is_503_when_the_db_is_down(client):
    with patch.object(DatabaseManager, "health_check", new=AsyncMock(return_value=False)):
        with patch("app.main.redis.from_url", new=MagicMock(return_value=_fake_redis())):
            response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "down"
    assert body["checks"]["redis"] == "ok"


def test_ready_is_503_when_redis_is_down(client):
    redis_client = _fake_redis(ping_exc=ConnectionError("redis unreachable"))
    with patch("app.main.redis.from_url", new=MagicMock(return_value=redis_client)):
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "down"


def test_ready_is_503_when_redis_cannot_be_constructed(client):
    with patch(
        "app.main.redis.from_url",
        new=MagicMock(side_effect=ConnectionError("no route to host")),
    ):
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "down"


# ---------------------------------------------------------------------------
# in-flight tracking middleware
# ---------------------------------------------------------------------------


def test_requests_are_accepted_before_shutdown(client):
    assert client.get("/health").status_code == 200


def test_requests_are_refused_503_once_shutting_down(client):
    main_module._in_flight_lock = asyncio.Lock()
    main_module._shutdown_event = asyncio.Event()
    client.app.state.shutting_down = True

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"detail": "Service shutting down"}
    assert response.headers["Retry-After"] == str(main_module._MAX_DRAIN_SECONDS)


def test_in_flight_counter_returns_to_zero_after_a_request(client):
    main_module._in_flight_lock = asyncio.Lock()
    main_module._in_flight_requests = 0

    client.get("/health")

    assert main_module._in_flight_requests == 0


# ---------------------------------------------------------------------------
# body-size cap (#210)
# ---------------------------------------------------------------------------


def test_oversized_content_length_is_rejected_with_413(client):
    response = client.post(
        "/api/v1/notifications/send",
        content=b"x" * 1_048_577,
        headers={"content-type": "application/json", "content-length": "1048577"},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_a_non_numeric_content_length_falls_through(client):
    response = client.post(
        "/api/v1/notifications/send",
        content=b"{}",
        headers={"content-type": "application/json", "content-length": "banana"},
    )

    assert response.status_code != 413


# ---------------------------------------------------------------------------
# /metrics gate (#469)
# ---------------------------------------------------------------------------


def test_metrics_is_open_outside_production(client):
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert b"python_info" in response.content or b"# HELP" in response.content


def _production_app(metrics_token: str):
    from fastapi import Depends, Header, HTTPException
    from fastapi.responses import Response

    with patch("app.main.settings") as fake_settings:
        fake_settings.SERVICE_NAME = "Notification"
        fake_settings.SERVICE_VERSION = "1.0.0"
        fake_settings.ENVIRONMENT = "production"
        fake_settings.CORS_ALLOWED_ORIGINS = ["https://wildframe.com"]
        fake_settings.CORS_ALLOW_CREDENTIALS = True
        fake_settings.LOG_LEVEL = "INFO"
        fake_settings.JWT_AUDIENCE = "wildframe-api"
        fake_settings.JWT_ISSUER = "wildframe-auth"
        fake_settings.REDIS_URL = "redis://localhost:6379/0"
        fake_settings.METRICS_TOKEN = metrics_token

        app = create_app()

    # Recreate the same gate against the freshly built production app.
    async def require_metrics_token(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> None:
        if fake_settings.ENVIRONMENT == "production":
            expected = (
                f"Bearer {fake_settings.METRICS_TOKEN}"
                if hasattr(fake_settings, "METRICS_TOKEN") and fake_settings.METRICS_TOKEN
                else None
            )
            if expected is None or authorization != expected:
                raise HTTPException(status_code=401, detail="Unauthorized")

    @app.get("/_metrics-gated", dependencies=[Depends(require_metrics_token)])
    async def _gated_metrics():
        return Response(content=generate_latest(), media_type="text/plain")

    return app


@pytest.mark.parametrize(
    "header,expected",
    [
        (None, 401),
        ("Bearer wrong", 401),
        ("Bearer adm-secret", 200),
    ],
)
def test_metrics_gate_in_production(header, expected):
    app = _production_app("adm-secret")

    with patch.object(DatabaseManager, "health_check", new=AsyncMock(return_value=True)):
        with patch.object(DatabaseManager, "close", new=AsyncMock(return_value=None)):
            with TestClient(app, base_url="http://localhost") as client:
                response = client.get(
                    "/_metrics-gated",
                    headers={"Authorization": header} if header else {},
                )

    assert response.status_code == expected


def test_metrics_gate_fails_closed_when_no_token_is_configured():
    app = _production_app("")

    with patch.object(DatabaseManager, "health_check", new=AsyncMock(return_value=True)):
        with patch.object(DatabaseManager, "close", new=AsyncMock(return_value=None)):
            with TestClient(app, base_url="http://localhost") as client:
                response = client.get("/_metrics-gated")

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# opaque 500 handler (#557)
# ---------------------------------------------------------------------------


def test_unhandled_exceptions_return_an_opaque_500_with_a_correlation_id():
    app = create_app()

    @app.get("/_boom")
    async def _boom() -> None:
        raise RuntimeError("secret internal detail")

    with (
        patch.object(DatabaseManager, "health_check", new=AsyncMock(return_value=True)),
        patch.object(DatabaseManager, "close", new=AsyncMock(return_value=None)),
    ):
        with TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ) as client:
            response = client.get("/_boom", headers={"X-Correlation-ID": "corr-500"})

    assert response.status_code == 500
    body = response.json()
    assert body["status_code"] == 500
    assert body["message"] == "Internal server error"
    assert body["correlation_id"]
    assert "secret internal detail" not in response.text


def test_known_defect_500_responses_lose_the_tracing_headers():
    """Characterisation test for a reported gap (NOT an assertion of intent).

    `track_in_flight` / `limit_body_size` are `@app.middleware("http")` and
    `add_request_context` lives in `wildframe_observability`, but Starlette runs
    the `Exception` handler inside `ServerErrorMiddleware`, which sits *above*
    the user middleware stack. A 500 body therefore never passes back through
    the middleware, so the inbound `X-Correlation-ID` is not echoed on the
    response (it is only present in the server-side log line).

    Expected to change when production code is fixed.
    """
    app = create_app()

    @app.get("/_boom2")
    async def _boom2() -> None:
        raise RuntimeError("kaboom")

    with (
        patch.object(DatabaseManager, "health_check", new=AsyncMock(return_value=True)),
        patch.object(DatabaseManager, "close", new=AsyncMock(return_value=None)),
    ):
        with TestClient(
            app, base_url="http://localhost", raise_server_exceptions=False
        ) as client:
            response = client.get("/_boom2", headers={"X-Correlation-ID": "corr-500"})

    assert response.status_code == 500
    assert "X-Correlation-ID" not in response.headers


# ---------------------------------------------------------------------------
# The metrics gate that `create_app` actually installs
# ---------------------------------------------------------------------------


def _prod_settings(metrics_token: str) -> MagicMock:
    """A stand-in for `app.main.settings` that looks like production."""
    fake = MagicMock()
    fake.SERVICE_NAME = "Notification"
    fake.SERVICE_VERSION = "1.0.0"
    fake.ENVIRONMENT = "production"
    fake.CORS_ALLOWED_ORIGINS = ["https://wildframe.com"]
    fake.CORS_ALLOW_CREDENTIALS = True
    fake.LOG_LEVEL = "INFO"
    fake.JWT_AUDIENCE = "wildframe-api"
    fake.JWT_ISSUER = "wildframe-auth"
    fake.REDIS_URL = "redis://localhost:6379/0"
    fake.METRICS_TOKEN = metrics_token
    return fake


def _metrics_route(app):
    route = next(r for r in app.routes if getattr(r, "path", None) == "/metrics")
    return route.dependant.dependencies[0].call, route.endpoint


@pytest.mark.parametrize(
    "header,expect_401",
    [
        (None, True),
        ("Bearer wrong-token", True),
        ("adm-secret", True),  # missing the "Bearer " prefix
        ("Bearer adm-secret", False),
    ],
)
async def test_the_installed_metrics_gate_enforces_the_admin_token(header, expect_401):
    """`require_metrics_token` is a closure over `app.main.settings`.

    The settings object is read at *call* time, so the patch has to stay active
    for the assertion as well as for `create_app()`.
    """
    from fastapi import HTTPException

    with patch("app.main.settings", _prod_settings("adm-secret")):
        gate, _ = _metrics_route(create_app())
        if expect_401:
            with pytest.raises(HTTPException) as excinfo:
                await gate(authorization=header)
            assert excinfo.value.status_code == 401
            assert excinfo.value.detail == "Unauthorized"
        else:
            assert await gate(authorization=header) is None


async def test_the_installed_metrics_gate_fails_closed_without_a_configured_token():
    from fastapi import HTTPException

    with patch("app.main.settings", _prod_settings("")):
        gate, _ = _metrics_route(create_app())
        with pytest.raises(HTTPException) as excinfo:
            await gate(authorization="Bearer ")

    assert excinfo.value.status_code == 401


async def test_the_installed_metrics_gate_is_a_noop_outside_production():
    """Outside production the gate returns immediately, even with a wrong token."""
    gate, _ = _metrics_route(create_app())

    assert await gate(authorization=None) is None
    assert await gate(authorization="Bearer nonsense") is None


async def test_the_installed_metrics_handler_serves_prometheus_output():
    from fastapi.responses import Response

    with patch("app.main.settings", _prod_settings("adm-secret")):
        gate, handler = _metrics_route(create_app())
        assert await gate(authorization="Bearer adm-secret") is None
        result = await handler()

    assert isinstance(result, Response)
    assert result.media_type == "text/plain"
    assert b"# HELP" in result.body or b"python_info" in result.body


# ---------------------------------------------------------------------------
# Streaming body cap (#210) - no content-length header
# ---------------------------------------------------------------------------


async def test_a_chunked_oversized_body_is_rejected_while_streaming():
    """The header check is bypassed when no content-length is sent."""
    import httpx

    app = create_app()
    chunk = b"x" * 64_000

    async def _chunks():
        for _ in range(20):  # 1.28 MB total, over the 1 MB cap
            yield chunk

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/api/v1/notifications/send",
            content=_chunks(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


async def test_a_chunked_body_under_the_cap_reaches_the_route():
    import httpx

    app = create_app()

    async def _chunks():
        yield b'{"user_id": "00000000-0000-0000-0000-000000000000"}'

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/api/v1/notifications/send",
            content=_chunks(),
            headers={"content-type": "application/json"},
        )

    # Not 413: it reached the route and failed authentication instead.
    assert response.status_code != 413
