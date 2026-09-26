"""Application factory, lifespan and the app-level seams they wire.

Besides ``create_app``/``lifespan``/``/ready``/``/metrics``, this module also
covers the collaborators the factory pulls in at import time — the auth
dependency chain (``get_current_user_id`` / ``require_self``), the request-body
cap and the graceful-shutdown middleware.

The Redis cache helpers, the content catalog client, the generation branches
and the repository input guards are hosted here too: they are the collaborators
of the same app surface (``get_rec_service``, the /health and /ready probes,
and the preference endpoint), and the brief restricts new test modules to this
owned set.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

import app.main as main_mod
import app.services as services_mod
from app.api.recommendation_routes import get_current_user_id, require_self
from app.core.settings import settings
from app.main import create_app
from app.repositories import RecommendationRepository, UserPreferencesRepository
from app.services import RecommendationService

# ----------------------------------------------------------------------
# Fakes / helpers
# ----------------------------------------------------------------------


def _redis_mock(ping_error: Exception | None = None) -> MagicMock:
    client = MagicMock()
    client.ping = AsyncMock(side_effect=ping_error)
    client.close = AsyncMock()
    client.aclose = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock()
    client.delete = AsyncMock()
    return client


@pytest.fixture
def redis_ok(monkeypatch):
    client = _redis_mock()
    monkeypatch.setattr(
        main_mod.redis, "from_url", AsyncMock(return_value=client), raising=False
    )
    return client


@pytest.fixture
def db_stub(monkeypatch):
    db = MagicMock()
    db.health_check = AsyncMock(return_value=True)
    db.close = AsyncMock()
    monkeypatch.setattr(main_mod, "DatabaseManager", db)
    return db


@pytest.fixture
def build_app(monkeypatch, db_stub, redis_ok):
    monkeypatch.setattr(main_mod, "start_event_subscriber", AsyncMock())
    monkeypatch.setattr(main_mod, "stop_event_subscriber", AsyncMock())
    monkeypatch.setattr(main_mod, "close_catalog_client", AsyncMock())
    monkeypatch.setattr(services_mod, "close_redis_client", AsyncMock())
    return create_app, db_stub, redis_ok


def _dispatch(app: FastAPI, name_hint: str = "dispatch"):
    for middleware in app.user_middleware:
        for key, value in middleware.kwargs.items():
            if key == name_hint:
                return value
    raise AssertionError("middleware is not registered")


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/recommendations/preferences",
            "headers": raw,
            "query_string": b"",
        }
    )


def _path_request(user_id: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/api/v1/recommendations/for-user/{user_id}",
            "path_params": {"user_id": user_id},
            "headers": [],
            "query_string": b"",
        }
    )


def _access_token(**claims) -> str:
    import time

    base = {
        "sub": str(uuid4()),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
        "iat": int(time.time()),
        "exp": int(time.time()) + 900,
    }
    base.update(claims)
    return jwt.encode(
        {k: v for k, v in base.items() if v is not None},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------


class TestCreateApp:
    def test_metadata_comes_from_settings(self, build_app):
        build, _db, _redis = build_app

        app = build()

        assert app.title == settings.SERVICE_NAME
        assert app.version == settings.SERVICE_VERSION
        assert app.description == "Recommendation Service"

    def test_docs_are_exposed_outside_production(self, build_app, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "development")
        build, _db, _redis = build_app

        app = build()

        assert app.docs_url == "/docs"
        assert app.redoc_url == "/redoc"
        assert app.openapi_url == "/openapi.json"

    def test_docs_are_disabled_in_production(self, build_app, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        build, _db, _redis = build_app

        app = build()

        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None

    def test_cors_middleware_is_registered_with_settings(self, build_app):
        build, _db, _redis = build_app

        app = build()
        cors = [m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"]

        assert len(cors) == 1
        assert cors[0].kwargs["allow_credentials"] is settings.CORS_ALLOW_CREDENTIALS
        assert cors[0].kwargs["allow_origins"] == settings.CORS_ALLOWED_ORIGINS
        assert cors[0].kwargs["allow_methods"] == ["*"]
        assert cors[0].kwargs["allow_headers"] == ["*"]

    def test_observability_is_wired(self, build_app):
        build, _db, _redis = build_app

        classes = [m.cls.__name__ for m in build().user_middleware]

        assert "CorrelationMiddleware" in classes
        assert "RequestLoggingMiddleware" in classes
        assert "MetricsMiddleware" in classes

    def test_recommendation_router_is_mounted(self, build_app):
        from app.api.recommendation_routes import get_rec_service

        build, _db, _redis = build_app
        paths = _all_paths(build())

        assert "/api/v1/recommendations/for-user/{user_id}" in paths
        assert "/api/v1/recommendations/preferences/{user_id}" in paths
        assert get_rec_service is not None


def _all_paths(app: FastAPI) -> set[str]:
    """Collect every mounted path, descending through included routers."""
    paths: set[str] = set()

    def walk(routes) -> None:
        for route in routes:
            if hasattr(route, "path"):
                paths.add(route.path)
            original = getattr(route, "original_router", None)
            if original is not None:
                walk(original.routes)

    walk(app.router.routes)
    return paths


# ----------------------------------------------------------------------
# /health and /ready
# ----------------------------------------------------------------------


class TestHealth:
    def test_healthy_when_the_database_answers(self, build_app):
        build, db, _redis = build_app
        client = TestClient(build(), base_url="http://localhost")

        body = client.get("/health").json()

        assert body == {
            "status": "healthy",
            "service": "recommendation",
            "version": settings.SERVICE_VERSION,
            "database": "ok",
        }
        db.health_check.assert_awaited()

    def test_degraded_when_the_database_is_down(self, build_app):
        build, db, _redis = build_app
        db.health_check = AsyncMock(return_value=False)
        client = TestClient(build(), base_url="http://localhost")

        body = client.get("/health").json()

        assert body["status"] == "degraded"
        assert body["database"] == "unavailable"
        # Liveness still answers 200 so Kubernetes does not restart the pod.
        assert client.get("/health").status_code == 200


class TestReady:
    def test_ready_when_database_and_redis_answer(self, build_app):
        build, _db, redis_client = build_app
        client = TestClient(build(), base_url="http://localhost")

        response = client.get("/ready")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "service": "recommendation",
            "version": settings.SERVICE_VERSION,
            "checks": {"database": "ok", "redis": "ok"},
        }
        redis_client.ping.assert_awaited()
        redis_client.close.assert_awaited()

    def test_not_ready_when_the_database_is_down(self, build_app):
        build, db, redis_client = build_app
        db.health_check = AsyncMock(return_value=False)
        client = TestClient(build(), base_url="http://localhost")

        response = client.get("/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert body["checks"] == {"database": "down", "redis": "ok"}
        # Redis is still probed: the probe reports per-dependency status, so an
        # operator can tell a database outage from a Redis outage.
        redis_client.ping.assert_awaited_once()

    def test_not_ready_when_redis_is_down(self, build_app, monkeypatch):
        build, _db, _redis = build_app
        monkeypatch.setattr(
            main_mod.redis,
            "from_url",
            AsyncMock(side_effect=RuntimeError("redis unreachable")),
            raising=False,
        )
        client = TestClient(build(), base_url="http://localhost")

        response = client.get("/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert body["checks"] == {"database": "ok", "redis": "down"}

    def test_ping_failure_degrades_the_probe(self, build_app, monkeypatch):
        build, _db, _redis = build_app
        monkeypatch.setattr(
            main_mod.redis,
            "from_url",
            AsyncMock(return_value=_redis_mock(ping_error=ConnectionError("no route"))),
            raising=False,
        )
        client = TestClient(build(), base_url="http://localhost")

        response = client.get("/ready")

        assert response.status_code == 503
        assert response.json()["checks"]["redis"] == "down"

    def test_ping_timeout_degrades_the_probe(self, build_app, monkeypatch):
        build, _db, _redis = build_app

        client_obj = _redis_mock()
        client_obj.ping = lambda: asyncio.sleep(10)
        monkeypatch.setattr(
            main_mod.redis, "from_url", AsyncMock(return_value=client_obj), raising=False
        )
        client = TestClient(build(), base_url="http://localhost")

        response = client.get("/ready")

        assert response.status_code == 503
        assert response.json()["checks"]["redis"] == "down"


# ----------------------------------------------------------------------
# /metrics gating
# ----------------------------------------------------------------------


class TestMetricsGate:
    def test_open_outside_production(self, build_app):
        build, _db, _redis = build_app
        client = TestClient(build(), base_url="http://localhost")

        response = client.get("/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")

    def test_401_in_production_without_a_configured_token(self, build_app, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        monkeypatch.setattr(settings, "METRICS_TOKEN", "")
        build, _db, _redis = build_app
        client = TestClient(build(), base_url="http://localhost")

        assert client.get("/metrics").status_code == 401

    def test_401_in_production_with_a_wrong_token(self, build_app, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        monkeypatch.setattr(settings, "METRICS_TOKEN", "expected-token")
        build, _db, _redis = build_app
        client = TestClient(build(), base_url="http://localhost")

        response = client.get("/metrics", headers={"Authorization": "Bearer wrong"})

        assert response.status_code == 401
        assert response.json()["detail"] == "Unauthorized"

    def test_200_in_production_with_the_right_token(self, build_app, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        monkeypatch.setattr(settings, "METRICS_TOKEN", "expected-token")
        build, _db, _redis = build_app
        client = TestClient(build(), base_url="http://localhost")

        response = client.get("/metrics", headers={"Authorization": "Bearer expected-token"})

        assert response.status_code == 200


# ----------------------------------------------------------------------
# Middleware
# ----------------------------------------------------------------------


class TestBodySizeLimit:
    @pytest.mark.asyncio
    async def test_oversized_content_length_is_rejected_before_the_route(self, build_app):
        build, _db, _redis = build_app
        dispatch = _dispatch(build())
        called: list = []

        async def call_next(request):
            called.append(request)
            return "reached-the-route"

        response = await dispatch(_request({"content-length": "1048577"}), call_next)

        assert response.status_code == 413
        assert json.loads(response.body) == {"detail": "Request body too large"}
        assert called == []

    @pytest.mark.asyncio
    async def test_a_body_at_the_limit_is_allowed_through(self, build_app):
        build, _db, _redis = build_app
        dispatch = _dispatch(build())

        async def call_next(request):
            return "reached-the-route"

        assert await dispatch(_request({"content-length": "1048576"}), call_next) == (
            "reached-the-route"
        )

    @pytest.mark.asyncio
    async def test_a_non_numeric_content_length_is_ignored(self, build_app):
        build, _db, _redis = build_app
        dispatch = _dispatch(build())

        async def call_next(request):
            return "reached-the-route"

        assert await dispatch(_request({"content-length": "not-a-number"}), call_next) == (
            "reached-the-route"
        )

    @pytest.mark.asyncio
    async def test_a_request_without_content_length_is_allowed(self, build_app):
        build, _db, _redis = build_app
        dispatch = _dispatch(build())

        async def call_next(request):
            return "reached-the-route"

        assert await dispatch(_request(), call_next) == "reached-the-route"


class TestGracefulShutdownMiddleware:
    def test_requests_are_served_while_running(self, build_app):
        build, _db, _redis = build_app
        app = build()
        client = TestClient(app, base_url="http://localhost")

        assert client.get("/health").status_code == 200
        assert main_mod._in_flight_requests == 0

    def test_requests_are_refused_once_draining(self, build_app):
        build, _db, _redis = build_app
        app = build()
        app.state.shutting_down = True
        client = TestClient(app, base_url="http://localhost")

        response = client.get("/health")

        assert response.status_code == 503
        assert response.json() == {"detail": "Service shutting down"}
        assert response.headers["Retry-After"] == str(main_mod._MAX_DRAIN_SECONDS)

    @pytest.mark.asyncio
    async def test_in_flight_counter_is_incremented_and_released(self, build_app):
        build, _db, _redis = build_app
        app = build()
        main_mod._in_flight_lock = None
        main_mod._in_flight_requests = 0
        observed: list[int] = []

        async def call_next(request):
            observed.append(main_mod._in_flight_requests)
            return "ok"

        dispatch = _dispatch(app, "dispatch")  # body-size middleware, runs first
        track = _track_dispatch(app)

        await track(_request(), call_next)

        assert observed == [1]
        assert main_mod._in_flight_requests == 0
        assert dispatch is not None

    @pytest.mark.asyncio
    async def test_counter_is_released_even_when_the_route_raises(self, build_app):
        build, _db, _redis = build_app
        app = build()
        main_mod._in_flight_lock = None
        main_mod._in_flight_requests = 0
        track = _track_dispatch(app)

        async def call_next(request):
            raise RuntimeError("route blew up")

        with pytest.raises(RuntimeError, match="route blew up"):
            await track(_request(), call_next)

        assert main_mod._in_flight_requests == 0


def _track_dispatch(app: FastAPI):
    """Return the graceful-shutdown middleware (the one that closes over app)."""
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "BaseHTTPMiddleware":
            fn = middleware.kwargs["dispatch"]
            if fn.__name__ == "track_in_flight":
                return fn
    raise AssertionError("in-flight tracking middleware is not registered")


# ----------------------------------------------------------------------
# Opaque 500 handler
# ----------------------------------------------------------------------


class TestUnhandledExceptionHandler:
    @pytest.mark.asyncio
    async def test_returns_an_opaque_500_with_a_correlation_id(self, build_app):
        build, _db, _redis = build_app
        handler = build().exception_handlers[Exception]

        response = await handler(_request(), RuntimeError("jwt secret is hunter2"))

        assert response.status_code == 500
        body = json.loads(response.body)
        assert body["status_code"] == 500
        assert body["message"] == "Internal server error"
        assert "correlation_id" in body
        assert "hunter2" not in response.body.decode()

    def test_handler_is_registered_for_bare_exceptions(self, build_app):
        build, _db, _redis = build_app

        assert Exception in build().exception_handlers


# ----------------------------------------------------------------------
# Lifespan
# ----------------------------------------------------------------------


@pytest.fixture
def lifecycle(monkeypatch, db_stub, redis_ok):
    monkeypatch.setattr(main_mod, "start_event_subscriber", AsyncMock())
    monkeypatch.setattr(main_mod, "stop_event_subscriber", AsyncMock())
    monkeypatch.setattr(main_mod, "close_catalog_client", AsyncMock())
    monkeypatch.setattr(services_mod, "close_redis_client", AsyncMock())
    return db_stub


class TestLifespan:
    @pytest.mark.asyncio
    async def test_happy_path_startup_and_shutdown(self, lifecycle):
        app = create_app()

        async with main_mod.lifespan(app):
            assert app.state.shutting_down is False
            main_mod.start_event_subscriber.assert_awaited_once()
            main_mod.stop_event_subscriber.assert_not_awaited()
            lifecycle.close.assert_not_awaited()

        assert app.state.shutting_down is True
        assert main_mod._shutdown_event is not None
        assert main_mod._shutdown_event.is_set()
        main_mod.stop_event_subscriber.assert_awaited_once()
        main_mod.close_catalog_client.assert_awaited_once()
        lifecycle.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unhealthy_database_aborts_startup(self, lifecycle):
        lifecycle.health_check = AsyncMock(return_value=False)
        app = create_app()

        with pytest.raises(RuntimeError, match="Database is not healthy"):
            async with main_mod.lifespan(app):
                pytest.fail("the lifespan must not yield")

        main_mod.start_event_subscriber.assert_not_awaited()
        lifecycle.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dlq_retention_is_scheduled_for_kafka(self, lifecycle, monkeypatch):
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
        retention = AsyncMock()
        monkeypatch.setattr("wildframe_events.dlq_retention.apply_dlq_retention", retention)

        async with main_mod.lifespan(create_app()):
            await asyncio.sleep(0)

        retention.assert_awaited_once_with("kafka:29092", settings.SERVICE_NAME)

    @pytest.mark.asyncio
    async def test_dlq_retention_is_not_scheduled_for_memory(self, lifecycle, monkeypatch):
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")
        retention = AsyncMock()
        monkeypatch.setattr("wildframe_events.dlq_retention.apply_dlq_retention", retention)

        async with main_mod.lifespan(create_app()):
            await asyncio.sleep(0)

        retention.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_drain_waits_for_in_flight_requests(self, lifecycle, monkeypatch):
        """A request still running keeps shutdown blocked until it finishes."""
        monkeypatch.setattr(main_mod, "_MAX_DRAIN_SECONDS", 5)
        app = create_app()
        main_mod._in_flight_requests = 0

        async with main_mod.lifespan(app):
            main_mod._in_flight_requests = 1
            # Clear the counter shortly after shutdown begins.
            loop = asyncio.get_running_loop()
            loop.call_later(0.2, lambda: setattr(main_mod, "_in_flight_requests", 0))

        # Shutdown completed without the timeout branch, so the counter drained.
        assert main_mod._in_flight_requests == 0
        lifecycle.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_drain_timeout_is_tolerated(self, lifecycle, monkeypatch):
        """A request that never finishes must not block shutdown forever."""
        monkeypatch.setattr(main_mod, "_MAX_DRAIN_SECONDS", 0)
        app = create_app()
        main_mod._in_flight_requests = 0

        async with main_mod.lifespan(app):
            main_mod._in_flight_requests = 1  # never drains

        # Shutdown still completed.
        main_mod.stop_event_subscriber.assert_awaited_once()
        lifecycle.close.assert_awaited_once()
        main_mod._in_flight_requests = 0

    @pytest.mark.asyncio
    async def test_an_exception_in_the_served_period_skips_shutdown(self, lifecycle):
        """DEFECT (reported, not fixed): ``lifespan`` has no try/finally around
        ``yield``, so an exception raised while the app is serving propagates
        straight past the shutdown sequence and leaks the subscriber, the
        catalog client and the engine. ``search-service`` wraps its ``yield``
        in try/finally; this one does not.
        """
        app = create_app()

        with pytest.raises(RuntimeError, match="request blew up"):
            async with main_mod.lifespan(app):
                raise RuntimeError("request blew up")

        main_mod.stop_event_subscriber.assert_not_awaited()
        main_mod.close_catalog_client.assert_not_awaited()
        lifecycle.close.assert_not_awaited()
        assert app.state.shutting_down is False

    @pytest.mark.asyncio
    async def test_shutdown_state_is_visible_to_the_middleware(self, lifecycle):
        app = create_app()

        async with main_mod.lifespan(app):
            assert app.state.shutting_down is False

        assert app.state.shutting_down is True


# ----------------------------------------------------------------------
# Auth dependency chain (app/api/recommendation_routes.py)
# ----------------------------------------------------------------------


class TestGetCurrentUserId:
    @pytest.mark.asyncio
    async def test_valid_access_token_yields_the_subject(self):
        user_id = uuid4()

        resolved = await get_current_user_id(f"Bearer {_access_token(sub=str(user_id))}")

        assert resolved == user_id

    @pytest.mark.asyncio
    async def test_user_id_claim_is_accepted_as_a_fallback(self):
        user_id = uuid4()

        resolved = await get_current_user_id(
            f"Bearer {_access_token(sub=None, user_id=str(user_id))}"
        )

        assert resolved == user_id

    @pytest.mark.asyncio
    @pytest.mark.parametrize("header", [None, "", "Basic abc", "bearer-not", "Token x"])
    async def test_missing_or_malformed_header_is_401(self, header):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            await get_current_user_id(header)

        assert excinfo.value.status_code == 401
        assert excinfo.value.detail == "Missing or invalid authorization header"

    @pytest.mark.asyncio
    async def test_refresh_token_is_rejected_as_an_access_token(self):
        from fastapi import HTTPException

        token = _access_token(type="refresh")

        with pytest.raises(HTTPException) as excinfo:
            await get_current_user_id(f"Bearer {token}")

        assert excinfo.value.status_code == 401
        assert excinfo.value.detail == "Invalid token type"

    @pytest.mark.asyncio
    async def test_token_without_a_type_claim_is_rejected(self):
        from fastapi import HTTPException

        token = _access_token(type=None)

        with pytest.raises(HTTPException) as excinfo:
            await get_current_user_id(f"Bearer {token}")

        assert excinfo.value.status_code == 401
        assert excinfo.value.detail == "Invalid token type"

    @pytest.mark.asyncio
    async def test_undecodable_token_is_401(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            await get_current_user_id("Bearer not-a-jwt")

        assert excinfo.value.status_code == 401
        assert excinfo.value.detail == "Invalid token"

    @pytest.mark.asyncio
    async def test_token_signed_with_another_secret_is_401(self):
        from fastapi import HTTPException

        token = jwt.encode(
            {
                "sub": str(uuid4()),
                "type": "access",
                "aud": settings.JWT_AUDIENCE,
                "iss": settings.JWT_ISSUER,
                "exp": 9999999999,
            },
            "a-totally-different-signing-secret-value",
            algorithm=settings.JWT_ALGORITHM,
        )

        with pytest.raises(HTTPException) as excinfo:
            await get_current_user_id(f"Bearer {token}")

        assert excinfo.value.status_code == 401

    @pytest.mark.asyncio
    async def test_token_without_a_subject_is_401(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            await get_current_user_id(f"Bearer {_access_token(sub=None)}")

        assert excinfo.value.status_code == 401
        assert excinfo.value.detail == "Invalid token subject"

    @pytest.mark.asyncio
    async def test_non_uuid_subject_is_401(self):
        from fastapi import HTTPException

        token = _access_token(sub="not-a-uuid")

        with pytest.raises(HTTPException) as excinfo:
            await get_current_user_id(f"Bearer {token}")

        assert excinfo.value.status_code == 401
        assert excinfo.value.detail == "Invalid token subject"


class TestRequireSelf:
    @pytest.mark.asyncio
    async def test_matching_path_user_is_allowed(self):
        user_id = uuid4()

        assert await require_self(user_id, _path_request(str(user_id))) == user_id

    @pytest.mark.asyncio
    async def test_missing_path_user_is_allowed(self):
        """Defense in depth: with no path user the JWT identity is authoritative."""
        user_id = uuid4()
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/recommendations/for-user",
                "path_params": {},
                "headers": [],
                "query_string": b"",
            }
        )

        assert await require_self(user_id, request) == user_id

    @pytest.mark.asyncio
    async def test_mismatch_is_404_not_403(self):
        """A 403 would disclose that another user's account exists (#435/#622)."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            await require_self(uuid4(), _path_request(str(uuid4())))

        assert excinfo.value.status_code == 404
        assert excinfo.value.detail == "Not found"


class TestGenreValidation:
    @pytest.mark.parametrize("genres", [None, [], ["action"], ["a", "b"]])
    def test_valid_genre_lists_pass(self, genres):
        from app.api.recommendation_routes import _validate_genre_list

        _validate_genre_list("liked_genres", genres)

    @pytest.mark.parametrize("genres", ["action", {"a": 1}, [1], ["ok", 2]])
    def test_non_string_arrays_are_422(self, genres):
        from fastapi import HTTPException

        from app.api.recommendation_routes import _validate_genre_list

        with pytest.raises(HTTPException) as excinfo:
            _validate_genre_list("liked_genres", genres)

        assert excinfo.value.status_code == 422
        assert "must be an array of strings" in excinfo.value.detail

    def test_an_oversized_array_is_422(self):
        from fastapi import HTTPException

        from app.api.recommendation_routes import _validate_genre_list

        with pytest.raises(HTTPException) as excinfo:
            _validate_genre_list(
                "disliked_genres", ["g"] * (settings.MAX_PREFERENCE_GENRES + 1)
            )

        assert excinfo.value.status_code == 422
        assert str(settings.MAX_PREFERENCE_GENRES) in excinfo.value.detail

    def test_an_array_exactly_at_the_limit_passes(self):
        from app.api.recommendation_routes import _validate_genre_list

        _validate_genre_list("liked_genres", ["g"] * settings.MAX_PREFERENCE_GENRES)


class TestPreferenceRouteBodies:
    def _service(self) -> MagicMock:
        service = MagicMock()
        service.update_preferences = AsyncMock()
        service.get_recommendations = AsyncMock(return_value=[])
        return service

    def test_object_body_maps_both_genre_lists(self, build_app):
        from app.api.recommendation_routes import get_rec_service

        build, _db, _redis = build_app
        app = build()
        service = self._service()
        app.dependency_overrides[get_rec_service] = lambda: service
        user_id = uuid4()

        response = TestClient(app, base_url="http://localhost").put(
            f"/api/v1/recommendations/preferences/{user_id}",
            json={"liked_genres": ["action"], "disliked_genres": ["horror"]},
            headers={"Authorization": f"Bearer {_access_token(sub=str(user_id))}"},
        )

        assert response.status_code == 200
        service.update_preferences.assert_awaited_once_with(
            user_id, ["action"], ["horror"]
        )

    def test_object_body_with_only_liked_genres(self, build_app):
        from app.api.recommendation_routes import get_rec_service

        build, _db, _redis = build_app
        app = build()
        service = self._service()
        app.dependency_overrides[get_rec_service] = lambda: service
        user_id = uuid4()

        TestClient(app, base_url="http://localhost").put(
            f"/api/v1/recommendations/preferences/{user_id}",
            json={"liked_genres": ["action"]},
            headers={"Authorization": f"Bearer {_access_token(sub=str(user_id))}"},
        )

        service.update_preferences.assert_awaited_once_with(user_id, ["action"], None)

    def test_object_body_with_invalid_disliked_genres_is_422(self, build_app):
        from app.api.recommendation_routes import get_rec_service

        build, _db, _redis = build_app
        app = build()
        service = self._service()
        app.dependency_overrides[get_rec_service] = lambda: service

        user_id = uuid4()
        response = TestClient(app, base_url="http://localhost").put(
            f"/api/v1/recommendations/preferences/{user_id}",
            json={"liked_genres": [], "disliked_genres": "horror"},
            headers={"Authorization": f"Bearer {_access_token(sub=str(user_id))}"},
        )

        assert response.status_code == 422
        service.update_preferences.assert_not_awaited()

    def test_mismatched_path_user_is_404(self, build_app):
        from app.api.recommendation_routes import get_rec_service

        build, _db, _redis = build_app
        app = build()
        service = self._service()
        app.dependency_overrides[get_rec_service] = lambda: service
        other = uuid4()

        response = TestClient(app, base_url="http://localhost").get(
            f"/api/v1/recommendations/for-user/{other}",
            headers={"Authorization": f"Bearer {_access_token()}"},
        )

        assert response.status_code == 404


# ----------------------------------------------------------------------
# Redis cache helpers (app/services.py)
# ----------------------------------------------------------------------


class TestCacheHelpers:
    @pytest.mark.asyncio
    async def test_client_is_created_once_and_verified(self, monkeypatch):
        client = _redis_mock()
        from_url = AsyncMock(return_value=client)
        monkeypatch.setattr(services_mod, "_redis_client", None)
        monkeypatch.setattr(services_mod.redis_async, "from_url", from_url)

        first = await services_mod.get_redis_client()
        second = await services_mod.get_redis_client()

        assert first is client
        assert second is client
        from_url.assert_called_once()
        assert from_url.call_args.kwargs["decode_responses"] is True
        client.ping.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unavailable_redis_disables_the_cache(self, monkeypatch):
        from_url = AsyncMock(side_effect=RuntimeError("redis down"))
        monkeypatch.setattr(services_mod, "_redis_client", None)
        monkeypatch.setattr(services_mod.redis_async, "from_url", from_url)

        assert await services_mod.get_redis_client() is None

    @pytest.mark.asyncio
    async def test_failed_ping_disables_the_cache(self, monkeypatch):
        client = _redis_mock(ping_error=ConnectionError("no route"))
        monkeypatch.setattr(services_mod, "_redis_client", None)
        monkeypatch.setattr(
            services_mod.redis_async, "from_url", AsyncMock(return_value=client)
        )

        assert await services_mod.get_redis_client() is None

    @pytest.mark.asyncio
    async def test_close_releases_the_client(self, monkeypatch):
        client = _redis_mock()
        monkeypatch.setattr(services_mod, "_redis_client", client)

        await services_mod.close_redis_client()

        client.aclose.assert_awaited_once()
        assert services_mod._redis_client is None

    @pytest.mark.asyncio
    async def test_close_is_a_noop_without_a_client(self, monkeypatch):
        monkeypatch.setattr(services_mod, "_redis_client", None)

        await services_mod.close_redis_client()  # must not raise

    def test_cache_key_is_scoped_to_the_user(self):
        user_id = uuid4()

        assert services_mod._cache_key(user_id) == f"wf:rec:user:{user_id}"

    def test_jittered_ttl_stays_inside_the_documented_band(self):
        ttls = {services_mod._jittered_ttl() for _ in range(200)}

        assert ttls
        assert min(ttls) >= services_mod.RECOMMENDATION_CACHE_TTL_SECONDS - (
            services_mod.RECOMMENDATION_CACHE_JITTER_SECONDS
        )
        assert max(ttls) <= services_mod.RECOMMENDATION_CACHE_TTL_SECONDS + (
            services_mod.RECOMMENDATION_CACHE_JITTER_SECONDS
        )

    @pytest.mark.asyncio
    async def test_get_returns_none_without_redis(self, monkeypatch):
        monkeypatch.setattr(services_mod, "get_redis_client", AsyncMock(return_value=None))

        assert await services_mod._cache_get(uuid4()) is None

    @pytest.mark.asyncio
    async def test_get_returns_the_cached_payload(self, monkeypatch):
        client = _redis_mock()
        client.get = AsyncMock(return_value=json.dumps([{"content_id": "a", "score": 1.0}]))
        monkeypatch.setattr(services_mod, "get_redis_client", AsyncMock(return_value=client))

        assert await services_mod._cache_get(uuid4()) == [
            {"content_id": "a", "score": 1.0}
        ]

    @pytest.mark.asyncio
    async def test_get_returns_none_on_a_cache_miss(self, monkeypatch):
        monkeypatch.setattr(services_mod, "get_redis_client", AsyncMock(return_value=_redis_mock()))

        assert await services_mod._cache_get(uuid4()) is None

    @pytest.mark.asyncio
    async def test_get_swallows_a_corrupt_cache_entry(self, monkeypatch):
        client = _redis_mock()
        client.get = AsyncMock(return_value="{not json")
        monkeypatch.setattr(services_mod, "get_redis_client", AsyncMock(return_value=client))

        assert await services_mod._cache_get(uuid4()) is None

    @pytest.mark.asyncio
    async def test_get_swallows_a_redis_failure(self, monkeypatch):
        client = _redis_mock()
        client.get = AsyncMock(side_effect=RuntimeError("redis down"))
        monkeypatch.setattr(services_mod, "get_redis_client", AsyncMock(return_value=client))

        assert await services_mod._cache_get(uuid4()) is None

    @pytest.mark.asyncio
    async def test_set_is_a_noop_without_redis(self, monkeypatch):
        monkeypatch.setattr(services_mod, "get_redis_client", AsyncMock(return_value=None))

        await services_mod._cache_set(uuid4(), [{"a": 1}])  # must not raise

    @pytest.mark.asyncio
    async def test_set_writes_json_with_a_bounded_ttl(self, monkeypatch):
        client = _redis_mock()
        monkeypatch.setattr(services_mod, "get_redis_client", AsyncMock(return_value=client))
        user_id = uuid4()

        await services_mod._cache_set(user_id, [{"content_id": "a"}])

        key, value = client.set.await_args.args
        assert key == f"wf:rec:user:{user_id}"
        assert json.loads(value) == [{"content_id": "a"}]
        ttl = client.set.await_args.kwargs["ex"]
        assert 240 <= ttl <= 360

    @pytest.mark.asyncio
    async def test_set_swallows_a_redis_failure(self, monkeypatch):
        client = _redis_mock()
        client.set = AsyncMock(side_effect=RuntimeError("redis down"))
        monkeypatch.setattr(services_mod, "get_redis_client", AsyncMock(return_value=client))

        await services_mod._cache_set(uuid4(), [{"a": 1}])  # must not raise


# ----------------------------------------------------------------------
# Content catalog client (app/services.py)
# ----------------------------------------------------------------------


class TestContentCatalogClient:
    @pytest.mark.asyncio
    async def test_strips_a_trailing_slash_from_the_base_url(self):
        import httpx

        catalog = services_mod.ContentCatalogClient(base_url="http://content.test/")
        assert catalog.base_url == "http://content.test"
        assert isinstance(catalog.client, httpx.AsyncClient)
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_fetch_genres_returns_the_payload(self):
        import httpx

        catalog = services_mod.ContentCatalogClient(base_url="http://content.test")
        await catalog.client.aclose()
        catalog.client = httpx.AsyncClient(
            base_url="http://content.test",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=[{"id": 1, "slug": "action"}])
            ),
        )

        assert await catalog.fetch_genres() == [{"id": 1, "slug": "action"}]
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_fetch_by_genre_passes_the_limit(self):
        import httpx

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["limit"] = request.url.params["limit"]
            return httpx.Response(200, json=[{"id": "c1"}])

        catalog = services_mod.ContentCatalogClient(base_url="http://content.test")
        await catalog.client.aclose()
        catalog.client = httpx.AsyncClient(
            base_url="http://content.test", transport=httpx.MockTransport(handler)
        )

        assert await catalog.fetch_by_genre(7, page_size=42) == [{"id": "c1"}]
        assert captured == {"path": "/api/v1/genres/7/content", "limit": "42"}
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_fetch_global_requests_trending(self):
        import httpx

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["limit"] = request.url.params["limit"]
            return httpx.Response(200, json=[{"id": "c2"}])

        catalog = services_mod.ContentCatalogClient(base_url="http://content.test")
        await catalog.client.aclose()
        catalog.client = httpx.AsyncClient(
            base_url="http://content.test", transport=httpx.MockTransport(handler)
        )

        assert await catalog.fetch_global(page_size=25) == [{"id": "c2"}]
        assert captured == {"path": "/api/v1/content/trending", "limit": "25"}
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_an_upstream_error_propagates(self):
        import httpx

        catalog = services_mod.ContentCatalogClient(base_url="http://content.test")
        await catalog.client.aclose()
        catalog.client = httpx.AsyncClient(
            base_url="http://content.test",
            transport=httpx.MockTransport(lambda request: httpx.Response(503, text="down")),
        )

        with pytest.raises(httpx.HTTPStatusError):
            await catalog.fetch_genres()
        await catalog.aclose()

    def test_shared_client_is_created_lazily_and_cached(self):
        services_mod._catalog_client = None
        services_mod._catalog_client_class = None

        first = services_mod.get_catalog_client()
        second = services_mod.get_catalog_client()

        assert first is second
        assert first.base_url == settings.CONTENT_SERVICE_URL
        assert services_mod._catalog_client_class is services_mod.ContentCatalogClient

    @pytest.mark.asyncio
    async def test_close_catalog_client_releases_the_pool(self):
        catalog = MagicMock()
        catalog.aclose = AsyncMock()
        services_mod._catalog_client = catalog
        services_mod._catalog_client_class = services_mod.ContentCatalogClient

        await services_mod.close_catalog_client()

        catalog.aclose.assert_awaited_once()
        assert services_mod._catalog_client is None
        assert services_mod._catalog_client_class is None

    @pytest.mark.asyncio
    async def test_close_catalog_client_is_a_noop_without_a_client(self):
        services_mod._catalog_client = None

        await services_mod.close_catalog_client()  # must not raise


# ----------------------------------------------------------------------
# RecommendationService read/generation paths
# ----------------------------------------------------------------------


# Stable content ids: ``generate`` coerces every id through ``UUID()``.
C1 = "00000000-0000-0000-0000-000000000001"
C2 = "00000000-0000-0000-0000-000000000002"
C3 = "00000000-0000-0000-0000-000000000003"
R1 = {"id": C1, "audience_score": 50.0}
R2 = {"id": C2, "audience_score": 50.0}
R3 = {"id": C3, "audience_score": 90.0}


def _row(content_id: str, score: float = 0.9, reason: str = "Because"):
    row = MagicMock()
    row.content_id = content_id
    row.score = score
    row.reason = reason
    return row


def _prefs(liked=None, disliked=None, updated_at=None):
    import datetime

    prefs = MagicMock()
    prefs.liked_genres = liked
    prefs.disliked_genres = disliked
    prefs.updated_at = updated_at or datetime.datetime(2000, 1, 1)
    return prefs


class TestGetRecommendationsBranches:
    @pytest.mark.asyncio
    async def test_a_cached_rail_short_circuits_everything(self, monkeypatch):
        cached = [{"content_id": str(uuid4()), "score": 1.0, "reason": "cached"}] * 30
        monkeypatch.setattr(services_mod, "_cache_get", AsyncMock(return_value=cached))
        pref_repo = MagicMock()
        rec_repo = MagicMock()
        service = RecommendationService(pref_repo, rec_repo)

        result = await service.get_recommendations(uuid4(), limit=5)

        assert len(result) == 5
        pref_repo.get_or_create.assert_not_called()
        rec_repo.get_for_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_fresh_rows_are_served_from_storage(self, monkeypatch):
        monkeypatch.setattr(services_mod, "_cache_get", AsyncMock(return_value=None))
        monkeypatch.setattr(services_mod, "_cache_set", AsyncMock())
        import datetime

        generated_at = datetime.datetime(2024, 1, 1)
        pref_repo = MagicMock()
        pref_repo.get_or_create = AsyncMock(
            return_value=_prefs(updated_at=datetime.datetime(2023, 1, 1))
        )
        rows = [_row("00000000-0000-0000-0000-000000000001")]
        rec_repo = MagicMock()
        rec_repo.get_for_user = AsyncMock(return_value=rows)
        rec_repo.latest_created_at = AsyncMock(return_value=generated_at)
        service = RecommendationService(pref_repo, rec_repo)

        result = await service.get_recommendations(uuid4(), limit=20)

        assert result == [{"content_id": C1, "score": 0.9, "reason": "Because"}]
        service.generate = AsyncMock()
        service.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stale_rows_are_regenerated(self, monkeypatch):
        monkeypatch.setattr(services_mod, "_cache_get", AsyncMock(return_value=None))
        monkeypatch.setattr(services_mod, "_cache_set", AsyncMock())
        import datetime

        pref_repo = MagicMock()
        pref_repo.get_or_create = AsyncMock(
            return_value=_prefs(updated_at=datetime.datetime(2025, 1, 1))
        )
        stale = [_row(C1)]
        fresh = [_row(C2, score=0.5)]
        rec_repo = MagicMock()
        rec_repo.get_for_user = AsyncMock(side_effect=[stale, fresh])
        rec_repo.latest_created_at = AsyncMock(
            return_value=datetime.datetime(2024, 1, 1)
        )
        pref_repo.session = MagicMock()
        pref_repo.session.rollback = AsyncMock()
        service = RecommendationService(pref_repo, rec_repo)
        service.generate = AsyncMock()

        result = await service.get_recommendations(uuid4(), limit=20)

        service.generate.assert_awaited_once()
        assert result[0]["content_id"] == C2

    @pytest.mark.asyncio
    async def test_generation_failure_falls_back_to_stored_rows(self, monkeypatch):
        monkeypatch.setattr(services_mod, "_cache_get", AsyncMock(return_value=None))
        monkeypatch.setattr(services_mod, "_cache_set", AsyncMock())
        rows = [_row(C1)]
        pref_repo = MagicMock()
        pref_repo.get_or_create = AsyncMock(return_value=_prefs())
        rec_repo = MagicMock()
        rec_repo.get_for_user = AsyncMock(return_value=rows)
        rec_repo.latest_created_at = AsyncMock(return_value=None)
        service = RecommendationService(pref_repo, rec_repo)
        service.generate = AsyncMock(side_effect=RuntimeError("catalog down"))

        result = await service.get_recommendations(uuid4(), limit=20)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_limit_is_clamped_to_the_configured_maximum(self, monkeypatch):
        monkeypatch.setattr(services_mod, "_cache_get", AsyncMock(return_value=None))
        monkeypatch.setattr(services_mod, "_cache_set", AsyncMock())
        rows = [_row(f"00000000-0000-0000-0000-00000000000{i}") for i in range(1, 6)]
        pref_repo = MagicMock()
        pref_repo.get_or_create = AsyncMock(return_value=_prefs())
        rec_repo = MagicMock()
        rec_repo.get_for_user = AsyncMock(return_value=rows)
        rec_repo.latest_created_at = AsyncMock(return_value=None)
        service = RecommendationService(pref_repo, rec_repo)

        result = await service.get_recommendations(uuid4(), limit=1_000_000)

        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_a_hostile_zero_limit_is_floored_at_one(self, monkeypatch):
        monkeypatch.setattr(services_mod, "_cache_get", AsyncMock(return_value=None))
        monkeypatch.setattr(services_mod, "_cache_set", AsyncMock())
        rows = [_row(C1)]
        pref_repo = MagicMock()
        pref_repo.get_or_create = AsyncMock(return_value=_prefs())
        rec_repo = MagicMock()
        rec_repo.get_for_user = AsyncMock(return_value=rows)
        rec_repo.latest_created_at = AsyncMock(return_value=None)
        service = RecommendationService(pref_repo, rec_repo)

        result = await service.get_recommendations(uuid4(), limit=0)

        assert len(result) == 1


class TestGenerationBranches:
    def _catalog(self, genres=None, by_genre=None, global_items=None):
        """``by_genre`` is one item batch per ``fetch_by_genre`` call."""
        catalog = MagicMock()
        catalog.fetch_genres = AsyncMock(return_value=genres or [])
        catalog.fetch_by_genre = AsyncMock(side_effect=[list(b) for b in (by_genre or [])])
        catalog.fetch_global = AsyncMock(return_value=global_items or [])
        catalog.aclose = AsyncMock()
        return catalog

    def _repos(self):
        pref_repo = MagicMock()
        pref_repo.session = MagicMock()
        pref_repo.session.commit = AsyncMock()
        pref_repo.session.rollback = AsyncMock()
        rec_repo = MagicMock()
        rec_repo.clear_for_user = AsyncMock()
        rec_repo.create = AsyncMock()
        return pref_repo, rec_repo

    @pytest.mark.asyncio
    async def test_candidate_cap_stops_scoring_early(self, monkeypatch):
        monkeypatch.setattr(services_mod.settings, "MAX_CANDIDATES", 1)
        monkeypatch.setattr(
            services_mod,
            "get_catalog_client",
            lambda: self._catalog(
                genres=[{"id": "1", "slug": "action", "name": "Action"}],
                by_genre=[[{"id": C1}, {"id": C2}]],
            ),
        )
        pref_repo, rec_repo = self._repos()
        service = RecommendationService(pref_repo, rec_repo)

        assert await service.generate(uuid4(), ["action"], []) == 1

    @pytest.mark.asyncio
    async def test_disliked_content_is_excluded_by_genre_id(self, monkeypatch):
        monkeypatch.setattr(
            services_mod,
            "get_catalog_client",
            lambda: self._catalog(
                genres=[
                    {"id": "1", "slug": "action", "name": "Action"},
                    {"id": "9", "slug": "horror", "name": "Horror"},
                ],
                by_genre=[
                    [
                        {"id": C1, "genres": [{"id": "9", "slug": "horror"}]},
                        {"id": C2, "genres": [{"id": "1", "slug": "action"}]},
                    ]
                ],
            ),
        )
        pref_repo, rec_repo = self._repos()
        service = RecommendationService(pref_repo, rec_repo)

        assert await service.generate(uuid4(), ["action"], ["horror"]) == 1
        created = [c.args[1] for c in rec_repo.create.await_args_list]
        assert str(created[0]) == C2

    @pytest.mark.asyncio
    async def test_duplicate_content_is_scored_once(self, monkeypatch):
        monkeypatch.setattr(
            services_mod,
            "get_catalog_client",
            lambda: self._catalog(
                genres=[
                    {"id": "1", "slug": "action", "name": "Action"},
                    {"id": "2", "slug": "thriller", "name": "Thriller"},
                ],
                by_genre=[[{"id": C1}], [{"id": C1}, {"id": C2}]],
            ),
        )
        pref_repo, rec_repo = self._repos()
        service = RecommendationService(pref_repo, rec_repo)

        assert await service.generate(uuid4(), ["action", "thriller"], []) == 2

    @pytest.mark.asyncio
    async def test_the_global_fallback_stops_at_the_candidate_cap(self, monkeypatch):
        monkeypatch.setattr(services_mod.settings, "MAX_CANDIDATES", 1)
        monkeypatch.setattr(
            services_mod,
            "get_catalog_client",
            lambda: self._catalog(
                global_items=[{"id": C1}, {"id": C2}],
            ),
        )
        pref_repo, rec_repo = self._repos()
        service = RecommendationService(pref_repo, rec_repo)

        assert await service.generate(uuid4(), [], []) == 1
        reasons = [c.kwargs["reason"] for c in rec_repo.create.await_args_list]
        assert reasons == ["Popular on Wildframe"]

    @pytest.mark.asyncio
    async def test_the_global_fallback_honours_dislikes(self, monkeypatch):
        monkeypatch.setattr(
            services_mod,
            "get_catalog_client",
            lambda: self._catalog(
                genres=[{"id": "9", "slug": "horror", "name": "Horror"}],
                global_items=[
                    {"id": C1, "genres": [{"id": "9", "slug": "horror"}]},
                    {"id": C2},
                    {"id": C3},
                ],
            ),
        )
        pref_repo, rec_repo = self._repos()
        service = RecommendationService(pref_repo, rec_repo)

        assert await service.generate(uuid4(), [], ["horror"]) == 2

    @pytest.mark.asyncio
    async def test_the_global_fallback_dedupes_repeated_content(self, monkeypatch):
        monkeypatch.setattr(
            services_mod,
            "get_catalog_client",
            lambda: self._catalog(global_items=[{"id": C1}, {"id": C1}]),
        )
        pref_repo, rec_repo = self._repos()
        service = RecommendationService(pref_repo, rec_repo)

        assert await service.generate(uuid4(), [], []) == 1

    @pytest.mark.asyncio
    async def test_a_catalog_failure_rolls_back_and_reraises(self, monkeypatch):
        catalog = self._catalog()
        catalog.fetch_genres = AsyncMock(side_effect=RuntimeError("content down"))
        monkeypatch.setattr(services_mod, "get_catalog_client", lambda: catalog)
        pref_repo, rec_repo = self._repos()
        service = RecommendationService(pref_repo, rec_repo)

        with pytest.raises(RuntimeError, match="content down"):
            await service.generate(uuid4(), ["action"], [])

        pref_repo.session.rollback.assert_awaited_once()
        pref_repo.session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_preference_lists_are_truncated(self, monkeypatch):
        monkeypatch.setattr(services_mod.settings, "MAX_PREFERENCE_GENRES", 1)
        seen: list = []

        async def fetch_by_genre(genre_id, page_size=100):
            seen.append(genre_id)
            return []

        catalog = self._catalog(
            genres=[
                {"id": "1", "slug": "action", "name": "Action"},
                {"id": "2", "slug": "comedy", "name": "Comedy"},
            ]
        )
        catalog.fetch_by_genre = fetch_by_genre
        monkeypatch.setattr(services_mod, "get_catalog_client", lambda: catalog)
        pref_repo, rec_repo = self._repos()
        service = RecommendationService(pref_repo, rec_repo)

        assert await service.generate(uuid4(), ["action", "comedy"], []) == 0
        assert seen == ["1"]

    @pytest.mark.asyncio
    async def test_results_are_ranked_by_score_then_id(self, monkeypatch):
        monkeypatch.setattr(
            services_mod,
            "get_catalog_client",
            lambda: self._catalog(
                genres=[{"id": "1", "slug": "action", "name": "Action"}],
                by_genre=[[R2, R1, R3]],
            ),
        )
        pref_repo, rec_repo = self._repos()
        service = RecommendationService(pref_repo, rec_repo)

        await service.generate(uuid4(), ["action"], [])

        ranked = [str(c.args[1]) for c in rec_repo.create.await_args_list]
        # Highest score first, ties broken by ascending content id.
        assert ranked == [R3["id"], R1["id"], R2["id"]]

    @pytest.mark.asyncio
    async def test_stored_rows_are_cleared_before_regeneration(self, monkeypatch):
        monkeypatch.setattr(
            services_mod,
            "get_catalog_client",
            lambda: self._catalog(
                genres=[{"id": "1", "slug": "action", "name": "Action"}],
                by_genre=[[{"id": C1}]],
            ),
        )
        pref_repo, rec_repo = self._repos()
        service = RecommendationService(pref_repo, rec_repo)
        user_id = uuid4()

        await service.generate(user_id, ["action"], [])

        rec_repo.clear_for_user.assert_awaited_once_with(user_id)
        assert rec_repo.create.await_args.kwargs["algorithm"] == "genre-based"


class TestUpdatePreferences:
    @pytest.mark.asyncio
    async def test_preference_update_bumps_updated_at_and_regenerates(self, monkeypatch):
        prefs = _prefs(liked=["action"], disliked=None)
        pref_repo = MagicMock()
        pref_repo.get_or_create = AsyncMock(return_value=prefs)
        pref_repo.session = MagicMock()
        pref_repo.session.commit = AsyncMock()
        service = RecommendationService(pref_repo, MagicMock())
        service.generate = AsyncMock(return_value=0)
        invalidate = AsyncMock()
        monkeypatch.setattr(services_mod, "_cache_invalidate", invalidate)
        user_id = uuid4()

        result = await service.update_preferences(user_id, ["comedy"])

        assert result is prefs
        assert prefs.liked_genres == ["comedy"]
        assert prefs.disliked_genres is None
        assert prefs.updated_at.tzinfo is not None
        invalidate.assert_awaited_once_with(user_id)
        service.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_none_arguments_leave_existing_genres_untouched(self, monkeypatch):
        prefs = _prefs(liked=["action"], disliked=["horror"])
        pref_repo = MagicMock()
        pref_repo.get_or_create = AsyncMock(return_value=prefs)
        pref_repo.session = MagicMock()
        pref_repo.session.commit = AsyncMock()
        service = RecommendationService(pref_repo, MagicMock())
        service.generate = AsyncMock()
        monkeypatch.setattr(services_mod, "_cache_invalidate", AsyncMock())

        await service.update_preferences(uuid4())

        assert prefs.liked_genres == ["action"]
        assert prefs.disliked_genres == ["horror"]

    @pytest.mark.asyncio
    async def test_a_generation_failure_still_keeps_the_preference(self, monkeypatch):
        prefs = _prefs()
        pref_repo = MagicMock()
        pref_repo.get_or_create = AsyncMock(return_value=prefs)
        pref_repo.session = MagicMock()
        pref_repo.session.commit = AsyncMock()
        service = RecommendationService(pref_repo, MagicMock())
        service.generate = AsyncMock(side_effect=RuntimeError("catalog down"))
        monkeypatch.setattr(services_mod, "_cache_invalidate", AsyncMock())

        result = await service.update_preferences(uuid4(), ["action"])

        assert result is prefs
        assert prefs.liked_genres == ["action"]


# ----------------------------------------------------------------------
# Repository input guards (app/repositories.py)
# ----------------------------------------------------------------------


class TestRepositoryGuards:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf")])
    async def test_non_finite_scores_are_rejected(self, score):
        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        with pytest.raises(ValueError, match="finite number"):
            await RecommendationRepository(session).create(uuid4(), uuid4(), score)

        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_finite_score_is_accepted(self):
        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()

        rec = await RecommendationRepository(session).create(
            uuid4(), uuid4(), 0.75, reason="r", algorithm="cf"
        )

        assert rec is not None
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_concurrent_insert_race_is_resolved_by_re_reading(self):
        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock(side_effect=_integrity_error())
        winner = MagicMock()
        # First SELECT misses, the INSERT loses the race, the re-SELECT wins.
        first = MagicMock()
        first.scalar_one_or_none.return_value = None
        second = MagicMock()
        second.scalar_one_or_none.return_value = winner
        session.execute = AsyncMock(side_effect=[first, second])
        session.begin_nested = MagicMock(
            side_effect=lambda: _FailingIntegrityScope(session)
        )

        prefs = await UserPreferencesRepository(session).get_or_create(uuid4())

        assert prefs is winner
        assert session.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_an_unresolvable_race_is_reraised(self):
        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock(side_effect=_integrity_error())
        missing = MagicMock()
        missing.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=missing)
        session.begin_nested = MagicMock(
            side_effect=lambda: _FailingIntegrityScope(session)
        )

        with pytest.raises(IntegrityError):
            await UserPreferencesRepository(session).get_or_create(uuid4())


class _FailingIntegrityScope:
    """``session.begin_nested()`` scope: the nested flush raises IntegrityError."""

    def __init__(self, session: MagicMock):
        self._session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _integrity_error() -> IntegrityError:
    return IntegrityError("INSERT INTO user_preferences", {}, Exception("duplicate key"))
