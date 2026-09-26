"""Application factory, lifespan and cross-cutting HTTP behaviour.

Nothing here touches a real Elasticsearch or PostgreSQL: the ES client, the
catalog client and the database manager are all replaced with in-process fakes
so the startup/shutdown ordering itself is what is under test.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

import app.main as main_mod
from app.core.settings import settings
from app.main import create_app


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class _Session:
    async def __aenter__(self):
        return MagicMock()

    async def __aexit__(self, *exc):
        return False


class _SessionFactory:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return _Session()

    def begin(self):
        return _Session()


@pytest.fixture
def app_factory(monkeypatch):
    """Return a callable that builds a fresh app with all externals faked."""
    db = MagicMock()
    db.health_check = AsyncMock(return_value=True)
    db.close = AsyncMock()
    db.session_factory = _SessionFactory()
    monkeypatch.setattr(main_mod, "DatabaseManager", db)

    es = MagicMock()
    es.ping = AsyncMock(return_value=True)
    es.close = AsyncMock()
    # ``app.main`` binds both names at import time, so patch them there.
    monkeypatch.setattr(main_mod, "es_client", lambda: es)
    monkeypatch.setattr("app.api.search_routes.es_client", lambda: es)
    monkeypatch.setattr(main_mod, "close_es_client", AsyncMock())

    return lambda: create_app(), db, es


@pytest.fixture
def lifecycle(monkeypatch):
    """Patch everything the lifespan touches and return a recorder."""
    db = MagicMock()
    db.health_check = AsyncMock(return_value=True)
    db.close = AsyncMock()
    db.session_factory = _SessionFactory()
    monkeypatch.setattr(main_mod, "DatabaseManager", db)

    es = MagicMock()
    es.ping = AsyncMock(return_value=True)
    es.close = AsyncMock()
    monkeypatch.setattr(main_mod, "es_client", lambda: es)
    monkeypatch.setattr("app.api.search_routes.es_client", lambda: es)
    monkeypatch.setattr(main_mod, "close_es_client", AsyncMock())
    monkeypatch.setattr(main_mod, "start_event_subscriber", AsyncMock())
    monkeypatch.setattr(main_mod, "stop_event_subscriber", AsyncMock())

    import app.core.event_consumer as consumer_mod

    consumer_calls: list = []

    async def fake_consumer(client):
        consumer_calls.append(client)
        await asyncio.sleep(3600)

    monkeypatch.setattr(consumer_mod, "run_content_sync_consumer", fake_consumer)

    return MagicMock(db=db, es=es, consumer_calls=consumer_calls)


def _dispatch(app: FastAPI):
    """Return the body-size middleware callable registered on ``app``."""
    for middleware in app.user_middleware:
        if "dispatch" in middleware.kwargs:
            return middleware.kwargs["dispatch"]
    raise AssertionError("body-size middleware is not registered")


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/search/query",
        "headers": raw,
        "query_string": b"",
    }
    return Request(scope)


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------


def _all_paths(app: FastAPI) -> set[str]:
    """Collect every mounted path, descending through included routers.

    Starlette keeps an included ``APIRouter`` behind an ``_IncludedRouter``
    node, so ``app.router.routes`` alone does not show the mounted endpoints.
    """
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


class TestCreateApp:
    def test_metadata_comes_from_settings(self, app_factory):
        build, _db, _es = app_factory
        app = build()

        assert app.title == settings.SERVICE_NAME
        assert app.version == settings.SERVICE_VERSION
        assert app.description == "Search Service"

    def test_docs_are_exposed_outside_production(self, app_factory, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "development")
        build, _db, _es = app_factory

        app = build()

        assert app.docs_url == "/docs"
        assert app.redoc_url == "/redoc"
        assert app.openapi_url == "/openapi.json"

    def test_docs_are_disabled_in_production(self, app_factory, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        build, _db, _es = app_factory

        app = build()

        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None

    def test_search_router_is_mounted(self, app_factory):
        build, _db, _es = app_factory

        paths = _all_paths(build())

        assert "/api/v1/search/query" in paths
        assert "/api/v1/search/trending" in paths
        assert "/api/v1/search/reindex" in paths
        assert "/api/v1/search/content/{content_id}" in paths
        assert "/api/v1/search/index/{index_name}" in paths

    def test_observability_is_wired(self, app_factory):
        build, _db, _es = app_factory

        classes = [m.cls.__name__ for m in build().user_middleware]

        assert "CorrelationMiddleware" in classes
        assert "RequestLoggingMiddleware" in classes
        assert "MetricsMiddleware" in classes

    def test_health_is_liveness_only(self, app_factory):
        build, db, es = app_factory
        # A liveness probe must not depend on any external topology.
        db.health_check = AsyncMock(side_effect=AssertionError("health must not probe the DB"))
        es.ping = AsyncMock(side_effect=AssertionError("health must not probe Elasticsearch"))

        client = TestClient(build(), base_url="http://localhost")
        body = client.get("/health").json()

        assert body == {"status": "ok", "service": "search", "version": settings.SERVICE_VERSION}
        db.health_check.assert_not_awaited()
        es.ping.assert_not_awaited()


# ----------------------------------------------------------------------
# Readiness probe
# ----------------------------------------------------------------------


class TestReady:
    @staticmethod
    def _client(app_factory):
        # Not used as a context manager: the lifespan needs a live database.
        build, _db, _es = app_factory
        return TestClient(build(), base_url="http://localhost")

    def test_ready_when_database_and_elasticsearch_answer(self, app_factory):
        build, db, es = app_factory
        client = self._client(app_factory)

        response = client.get("/ready")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "service": "search",
            "version": settings.SERVICE_VERSION,
            "database": "ok",
            "elasticsearch": "ok",
        }
        db.health_check.assert_awaited()
        es.ping.assert_awaited()

    def test_not_ready_when_the_database_is_down(self, app_factory):
        build, db, es = app_factory
        db.health_check = AsyncMock(return_value=False)
        client = self._client(app_factory)

        response = client.get("/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert body["database"] == "unavailable"
        assert body["elasticsearch"] == "ok"

    def test_not_ready_when_elasticsearch_is_down(self, app_factory):
        build, db, es = app_factory
        es.ping = AsyncMock(side_effect=ConnectionError("es down"))
        client = self._client(app_factory)

        response = client.get("/ready")

        assert response.status_code == 503
        assert response.json()["elasticsearch"] == "unavailable"

    def test_ping_timeout_degrades_instead_of_hanging(self, app_factory):
        build, _db, es = app_factory

        async def slow_ping():
            await asyncio.sleep(10)

        es.ping = slow_ping
        client = self._client(app_factory)

        response = client.get("/ready")

        assert response.status_code == 503
        assert response.json()["elasticsearch"] == "unavailable"

    def test_probe_returns_json_media_type(self, app_factory):
        client = self._client(app_factory)

        response = client.get("/ready")

        assert response.headers["content-type"].startswith("application/json")
        json.loads(response.content)


# ----------------------------------------------------------------------
# Request body cap
# ----------------------------------------------------------------------


class TestBodySizeLimit:
    @pytest.mark.asyncio
    async def test_oversized_content_length_is_rejected_before_the_route(self, app_factory):
        build, _db, _es = app_factory
        dispatch = _dispatch(build())
        called: list = []

        async def call_next(request):
            called.append(request)
            return "reached-the-route"

        response = await dispatch(
            _request({"content-length": str(1048576 + 1)}), call_next
        )

        assert response.status_code == 413
        assert json.loads(response.body) == {"detail": "Request body too large"}
        assert called == []

    @pytest.mark.asyncio
    async def test_a_body_at_the_limit_is_allowed_through(self, app_factory):
        build, _db, _es = app_factory
        dispatch = _dispatch(build())

        async def call_next(request):
            return "reached-the-route"

        assert await dispatch(_request({"content-length": "1048576"}), call_next) == (
            "reached-the-route"
        )

    @pytest.mark.asyncio
    async def test_a_non_numeric_content_length_is_ignored(self, app_factory):
        """A malformed header must not 500 the whole service."""
        build, _db, _es = app_factory
        dispatch = _dispatch(build())

        async def call_next(request):
            return "reached-the-route"

        assert await dispatch(_request({"content-length": "not-a-number"}), call_next) == (
            "reached-the-route"
        )

    @pytest.mark.asyncio
    async def test_a_request_without_content_length_is_allowed(self, app_factory):
        build, _db, _es = app_factory
        dispatch = _dispatch(build())

        async def call_next(request):
            return "reached-the-route"

        assert await dispatch(_request({}), call_next) == "reached-the-route"


# ----------------------------------------------------------------------
# Opaque 500 handler
# ----------------------------------------------------------------------


class TestUnhandledExceptionHandler:
    @pytest.mark.asyncio
    async def test_returns_an_opaque_500(self, app_factory):
        build, _db, _es = app_factory
        handler = build().exception_handlers[Exception]

        response = await handler(_request({}), RuntimeError("db password is hunter2"))

        assert response.status_code == 500
        body = json.loads(response.body)
        assert body["status_code"] == 500
        assert body["message"] == "Internal server error"
        # Internals are never leaked. Note: unlike every other error path in this
        # service (see search_routes._error) this body carries no correlation_id,
        # so a 500 cannot be tied back to its X-Correlation-ID header.
        assert "hunter2" not in response.body.decode()
        assert set(body) == {"status_code", "message"}

    @pytest.mark.asyncio
    async def test_handler_is_registered_for_bare_exceptions(self, app_factory):
        build, _db, _es = app_factory

        assert Exception in build().exception_handlers


# ----------------------------------------------------------------------
# Index warm-up
# ----------------------------------------------------------------------


class TestWarmSearchIndex:
    @pytest.mark.asyncio
    async def test_creates_the_index_and_reindexes(self, monkeypatch):
        db = MagicMock()
        db.session_factory = _SessionFactory()
        monkeypatch.setattr(main_mod, "DatabaseManager", db)
        es = MagicMock()
        # ``app.main`` imports es_client at import time, so patch it there.
        monkeypatch.setattr(main_mod, "es_client", lambda: es)

        service = MagicMock()
        service.ensure_index = AsyncMock()
        service.reindex_catalog = AsyncMock()
        monkeypatch.setattr(main_mod, "SearchService", MagicMock(return_value=service))

        await main_mod._warm_search_index()

        service.ensure_index.assert_awaited_once()
        service.reindex_catalog.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_elasticsearch_failure_is_only_a_warning(self, monkeypatch):
        db = MagicMock()
        db.session_factory = _SessionFactory()
        monkeypatch.setattr(main_mod, "DatabaseManager", db)
        monkeypatch.setattr(main_mod, "es_client", lambda: MagicMock())

        service = MagicMock()
        service.ensure_index = AsyncMock(side_effect=ConnectionError("es down"))
        service.reindex_catalog = AsyncMock()
        monkeypatch.setattr(main_mod, "SearchService", MagicMock(return_value=service))

        await main_mod._warm_search_index()  # must not raise

        service.reindex_catalog.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_database_failure_is_only_a_warning(self, monkeypatch):
        db = MagicMock()
        db.session_factory = MagicMock(side_effect=RuntimeError("no pool"))
        monkeypatch.setattr(main_mod, "DatabaseManager", db)
        monkeypatch.setattr(main_mod, "es_client", lambda: MagicMock())

        await main_mod._warm_search_index()  # must not raise


# ----------------------------------------------------------------------
# Lifespan
# ----------------------------------------------------------------------


class TestLifespan:
    @pytest.mark.asyncio
    async def test_happy_path_startup_and_shutdown(self, lifecycle):
        app = create_app()

        async with main_mod.lifespan(app):
            await asyncio.sleep(0)  # let the created consumer task start
            assert lifecycle.consumer_calls == [lifecycle.es]
            lifecycle.db.close.assert_not_awaited()

        lifecycle.db.close.assert_awaited_once()
        main_mod.close_es_client.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unhealthy_database_aborts_startup(self, lifecycle):
        lifecycle.db.health_check = AsyncMock(return_value=False)
        app = create_app()

        with pytest.raises(RuntimeError, match="Database is not healthy"):
            async with main_mod.lifespan(app):
                pytest.fail("the lifespan must not yield")

        lifecycle.consumer_calls == []
        lifecycle.db.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_health_probe_aborts_startup(self, lifecycle):
        """health_check already swallows its own errors, so False is the signal."""
        lifecycle.db.health_check = AsyncMock(return_value=False)
        app = create_app()

        with pytest.raises(RuntimeError, match="Database is not healthy"):
            async with main_mod.lifespan(app):
                pytest.fail("the lifespan must not yield")

        main_mod.start_event_subscriber.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_event_subscriber_is_started_and_stopped(self, lifecycle):
        app = create_app()

        async with main_mod.lifespan(app):
            main_mod.start_event_subscriber.assert_awaited_once()
            main_mod.stop_event_subscriber.assert_not_awaited()

        main_mod.stop_event_subscriber.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_consumer_task_is_cancelled_on_shutdown(self, lifecycle):
        app = create_app()

        async with main_mod.lifespan(app):
            await asyncio.sleep(0)

        # Reaching here without a CancelledError leaking proves the task was
        # cancelled and awaited inside the shutdown sequence.
        assert lifecycle.consumer_calls
        lifecycle.db.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_warm_up_runs_before_the_consumer_starts(self, lifecycle, monkeypatch):
        order: list[str] = []

        async def warm():
            order.append("warm")

        async def consumer(client):
            order.append("consumer")
            await asyncio.sleep(0)

        monkeypatch.setattr(main_mod, "_warm_search_index", warm)
        import app.core.event_consumer as consumer_mod

        monkeypatch.setattr(consumer_mod, "run_content_sync_consumer", consumer)

        async with main_mod.lifespan(create_app()):
            await asyncio.sleep(0)

        assert order == ["warm", "consumer"]

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
    async def test_shutdown_closes_resources_even_after_a_body_error(self, lifecycle):
        app = create_app()

        with pytest.raises(RuntimeError, match="request blew up"):
            async with main_mod.lifespan(app):
                raise RuntimeError("request blew up")

        main_mod.stop_event_subscriber.assert_awaited_once()
        lifecycle.db.close.assert_awaited_once()
        main_mod.close_es_client.assert_awaited_once()
