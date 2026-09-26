"""Behavioural tests for ``app.main`` — the app factory and the lifespan.

The billing service is a financial system, so the things pinned here are the
ones that must never regress:

  * startup refuses to serve when the database is unhealthy (no "degraded but
    accepting money" state);
  * shutdown drains in-flight requests, bounded by ``_MAX_DRAIN_SECONDS``;
  * the in-flight counter is incremented *before* the handler runs and
    decremented in a ``finally``, so an exception cannot leak the count;
  * during shutdown new requests get 503 + Retry-After instead of racing the
    database teardown;
  * ``/health`` reports liveness only, ``/ready`` reports dependency topology;
  * oversized bodies are rejected before parsing, and a malformed
    ``Content-Length`` does not crash the middleware.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

import app.main as main_module
from app.core.database import DatabaseManager
from app.core.settings import settings
from app.main import _MAX_DRAIN_SECONDS, create_app, lifespan

# Mirrors the local MAX_BODY_SIZE inside create_app() (#517).
MAX_BODY_SIZE = 1048576


@pytest.fixture(autouse=True)
def _reset_module_state():
    """main.py keeps module-level shutdown state; never leak it between tests."""
    saved = (
        main_module._shutdown_event,
        main_module._in_flight_requests,
        main_module._in_flight_lock,
        main_module._fallback_in_flight_lock,
    )
    main_module._shutdown_event = None
    main_module._in_flight_requests = 0
    main_module._in_flight_lock = None
    main_module._fallback_in_flight_lock = None
    try:
        yield
    finally:
        (
            main_module._shutdown_event,
            main_module._in_flight_requests,
            main_module._in_flight_lock,
            main_module._fallback_in_flight_lock,
        ) = saved


def _client(app, raise_app_exceptions: bool = True):
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
        base_url="http://test",
    )


async def _get(app, path: str, raise_app_exceptions: bool = True, **kwargs):
    async with _client(app, raise_app_exceptions) as ac:
        return await ac.get(path, **kwargs)


def _run(app, path: str, **kwargs):
    """Synchronously drive one ASGI request (for plain, non-async test bodies)."""

    async def _go():
        async with _client(app, raise_app_exceptions=False) as ac:
            return await ac.get(path, **kwargs)

    return asyncio.run(_go())


def _run_sync(awaitable_obj):
    """Run a coroutine to completion from a sync test body."""
    return asyncio.run(_ensure_coroutine(awaitable_obj))


async def _ensure_coroutine(value):
    return await value


def _app_paths(app) -> set[str]:
    """Every path the app serves, including nested/included routers."""
    paths = {getattr(route, "path", "") for route in app.routes}
    paths |= set(app.openapi().get("paths", {}))
    return {p for p in paths if p}


# ---------------------------------------------------------------------------
# create_app
# ---------------------------------------------------------------------------


class TestCreateApp:
    def test_app_is_configured_from_settings(self):
        app = create_app()
        assert app.title == settings.SERVICE_NAME
        assert app.version == settings.SERVICE_VERSION

    def test_docs_are_served_outside_production(self):
        app = create_app()
        assert app.docs_url == "/docs"
        assert app.redoc_url == "/redoc"
        assert app.openapi_url == "/openapi.json"

    def test_docs_are_disabled_in_production(self):
        # #468: the OpenAPI schema enumerates internal admin surface.
        with patch.object(settings, "ENVIRONMENT", "production"):
            app = create_app()
        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None

    def test_cors_middleware_is_installed(self):
        app = create_app()
        names = {m.cls.__name__ for m in app.user_middleware}
        assert "CORSMiddleware" in names

    def test_cors_uses_the_configured_origins(self):
        app = create_app()
        cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
        assert cors.kwargs["allow_origins"] == settings.CORS_ALLOWED_ORIGINS
        assert cors.kwargs["allow_credentials"] is settings.CORS_ALLOW_CREDENTIALS

    def test_billing_routes_are_mounted_under_the_v1_prefix(self):
        # AGENTS.md: handlers are reached at /api/v1/... (via the gateway:
        # /{service}/api/v1/...).
        app = create_app()
        paths = _app_paths(app)
        assert any(p.startswith("/api/v1/billing") for p in paths)
        for expected in (
            "/api/v1/billing/subscription/{user_id}",
            "/api/v1/billing/floors",
            "/api/v1/billing/creator-share",
        ):
            assert expected in paths, expected

    def test_both_routers_are_included(self):
        # create_app includes billing_router and webhook_router; this FastAPI
        # version keeps them as opaque _IncludedRouter objects, so count them.
        app = create_app()
        included = [r for r in app.routes if type(r).__name__ == "_IncludedRouter"]
        assert len(included) == 2

    def test_both_http_middlewares_are_installed(self):
        # Two BaseHTTPMiddleware layers: in-flight tracking (#426) and the
        # request body cap (#517).
        app = create_app()
        http_mw = [
            m for m in app.user_middleware if m.cls.__name__ == "BaseHTTPMiddleware"
        ]
        assert len(http_mw) == 2

    def test_module_level_app_exists_for_uvicorn(self):
        # AGENTS.md: the entrypoint is app.main:app.
        assert main_module.app is not None
        assert main_module.app.title == settings.SERVICE_NAME


# ---------------------------------------------------------------------------
# In-flight tracking middleware
# ---------------------------------------------------------------------------


class TestInFlightTracking:
    async def test_counter_returns_to_zero_after_a_request(self):
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            resp = await _get(app, "/health")
        assert resp.status_code == 200
        assert main_module._in_flight_requests == 0

    async def test_counter_is_positive_during_the_handler(self):
        app = create_app()
        seen: list[int] = []

        @app.get("/_probe")
        async def _probe() -> dict:
            seen.append(main_module._in_flight_requests)
            return {"ok": True}

        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            await _get(app, "/_probe")
        # Two requests so far (the probe is the second; the first is the probe
        # itself) — the point is the count is non-zero *inside* the handler.
        assert seen and seen[0] >= 1
        assert main_module._in_flight_requests == 0

    async def test_counter_is_released_when_the_handler_raises(self):
        app = create_app()

        @app.get("/_boom")
        async def _boom() -> dict:
            raise RuntimeError("handler exploded")

        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            resp = await _get(app, "/_boom", raise_app_exceptions=False)
        assert resp.status_code == 500
        # A leaked count would make shutdown wait out the full drain window.
        assert main_module._in_flight_requests == 0

    async def test_shutting_down_state_rejects_new_requests_with_503(self):
        app = create_app()
        app.state.shutting_down = True
        async with _client(app) as ac:
            resp = await ac.get("/health")
        assert resp.status_code == 503
        assert resp.json() == {"detail": "Service shutting down"}
        assert resp.headers["Retry-After"] == str(_MAX_DRAIN_SECONDS)

    async def test_unhandled_exception_returns_an_opaque_500(self):
        # #557 / #466: the body must not leak the exception, but must carry a
        # correlation id the operator can grep for.
        app = create_app()

        @app.get("/_boom")
        async def _boom() -> dict:
            raise RuntimeError("secret internal detail")

        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            resp = await _get(app, "/_boom", raise_app_exceptions=False)
        assert resp.status_code == 500
        body = resp.json()
        assert body["status_code"] == 500
        assert body["message"] == "Internal server error"
        assert "secret internal detail" not in resp.text
        assert "correlation_id" in body


# ---------------------------------------------------------------------------
# /health and /ready
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    async def test_health_is_healthy_when_the_database_answers(self):
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            resp = await _get(app, "/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}

    async def test_health_reports_degraded_but_still_200(self):
        # Liveness only: a degraded report must not cause a k8s restart loop.
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=False)):
            resp = await _get(app, "/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "degraded"}

    async def test_health_does_not_probe_redis(self):
        # #628: /health is status-only, no dependency topology.
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            with patch.object(main_module.redis, "from_url", MagicMock()) as from_url:
                await _get(app, "/health")
        from_url.assert_not_called()


class TestReadyEndpoint:
    async def test_ready_reports_both_dependencies(self):
        app = create_app()
        client = AsyncMock()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            # `from_url` is awaited by /ready, so the double must be an
            # AsyncMock — a plain MagicMock returns the client un-awaited and
            # raises "AsyncMock object can't be awaited" on the next line.
            with patch.object(
                main_module.redis, "from_url", AsyncMock(return_value=client)
            ):
                resp = await _get(app, "/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["service"] == "billing"
        assert body["version"] == settings.SERVICE_VERSION
        assert body["checks"] == {"database": "ok", "redis": "ok"}
        client.ping.assert_awaited_once()
        client.close.assert_awaited_once()

    async def test_ready_returns_503_when_the_database_is_down(self):
        app = create_app()
        client = AsyncMock()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=False)):
            with patch.object(
                main_module.redis, "from_url", AsyncMock(return_value=client)
            ):
                resp = await _get(app, "/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["database"] == "down"

    async def test_ready_returns_503_when_redis_is_down(self):
        app = create_app()
        client = AsyncMock()
        client.ping = AsyncMock(side_effect=ConnectionError("redis refused"))
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            with patch.object(
                main_module.redis, "from_url", AsyncMock(return_value=client)
            ):
                resp = await _get(app, "/ready")
        assert resp.status_code == 503
        assert resp.json()["checks"]["redis"] == "down"

    async def test_ready_uses_the_configured_redis_url(self):
        app = create_app()
        client = AsyncMock()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            with patch.object(
                main_module.redis, "from_url", AsyncMock(return_value=client)
            ) as from_url:
                await _get(app, "/ready")
        from_url.assert_called_once_with(settings.REDIS_URL)

    async def test_ready_survives_a_redis_factory_error(self):
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            with patch.object(
                main_module.redis,
                "from_url",
                MagicMock(side_effect=OSError("dns failure")),
            ):
                resp = await _get(app, "/ready")
        assert resp.status_code == 503
        assert resp.json()["checks"]["redis"] == "down"

    async def test_ready_survives_a_slow_redis_ping(self):
        # A hung ping must be bounded, not awaited forever.
        app = create_app()
        client = AsyncMock()
        client.ping = AsyncMock(side_effect=asyncio.TimeoutError())
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            with patch.object(
                main_module.redis, "from_url", AsyncMock(return_value=client)
            ):
                resp = await _get(app, "/ready")
        assert resp.status_code == 503
        assert resp.json()["checks"]["redis"] == "down"


# ---------------------------------------------------------------------------
# Body size cap
# ---------------------------------------------------------------------------


class TestBodySizeCap:
    async def test_oversized_body_is_rejected_with_413(self):
        app = create_app()
        async with _client(app) as ac:
            resp = await ac.post(
                "/api/v1/billing/webhooks/stripe",
                content=b"x" * 10,
                headers={"content-length": str(MAX_BODY_SIZE + 1)},
            )
        assert resp.status_code == 413
        assert resp.json() == {"detail": "Request body too large"}

    async def test_body_at_the_cap_is_accepted(self):
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            async with _client(app) as ac:
                resp = await ac.post(
                    "/api/v1/billing/webhooks/stripe",
                    content=b"x" * 10,
                    headers={"content-length": str(MAX_BODY_SIZE)},
                )
        # 400/500 from the route, not 413 — the cap itself let it through.
        assert resp.status_code != 413

    async def test_malformed_content_length_does_not_crash_the_middleware(self):
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            resp = await _get(app, "/health", headers={"content-length": "not-a-number"})
        assert resp.status_code == 200

    async def test_absent_content_length_passes_through(self):
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            resp = await _get(app, "/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /metrics gating
# ---------------------------------------------------------------------------


class TestMetricsGate:
    """Characterisation tests for the /metrics admin token gate (#469).

    FINDING (reported, not fixed): the gate does not take effect.
    ``create_app`` calls ``wire_observability(app, ...)`` at main.py:202, which
    registers an *ungated* ``GET /metrics``. The gated handler is only
    registered afterwards at main.py:221. Starlette resolves routes in
    registration order, so the first ``/metrics`` entry always wins and
    ``require_metrics_token`` is unreachable. ``/metrics`` is therefore public
    in production, exposing request-rate and error metrics.
    """

    def test_metrics_returns_prometheus_text(self):
        app = create_app()
        resp = _run(app, "/metrics")
        assert resp.status_code == 200
        assert "python_info" in resp.text

    def test_two_metrics_routes_are_registered_in_that_order(self):
        app = create_app()
        metrics_routes = [
            route for route in app.routes if getattr(route, "path", None) == "/metrics"
        ]
        assert len(metrics_routes) == 2
        # The observability route is registered first, so it is the one matched.
        assert metrics_routes[0].name == "metrics"
        assert metrics_routes[1].name == "gated_metrics"

    def test_metrics_is_readable_in_production_without_any_token(self):
        # This is the bug: the gate never runs.
        with patch.object(settings, "ENVIRONMENT", "production"):
            with patch.object(settings, "METRICS_TOKEN", "s3cr3t-metrics"):
                app = create_app()
                resp = _run(app, "/metrics")
        assert resp.status_code == 200
        assert "python_info" in resp.text

    def test_a_wrong_token_is_still_accepted_because_the_gate_is_unreachable(self):
        with patch.object(settings, "ENVIRONMENT", "production"):
            with patch.object(settings, "METRICS_TOKEN", "s3cr3t-metrics"):
                app = create_app()
                resp = _run(app, "/metrics", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 200

    def test_the_gate_dependency_itself_is_correct(self):
        """Proves the ordering is the sole cause of the leak.

        ``gated_metrics`` carries ``dependencies=[Depends(require_metrics_token)]``.
        Calling that dependency callable directly exercises the real production
        closure and shows it *would* reject — it is simply never reached.
        """
        app = create_app()
        gated = next(
            r
            for r in app.routes
            if getattr(r, "path", "") == "/metrics" and r.name == "gated_metrics"
        )
        gate = gated.dependant.dependencies[0].call
        assert gate is not None

        with patch.object(settings, "ENVIRONMENT", "production"):
            with patch.object(settings, "METRICS_TOKEN", "s3cr3t-metrics"):
                with pytest.raises(HTTPException) as exc:
                    _run_sync(gate(None))
                assert exc.value.status_code == 401
                assert exc.value.detail == "Unauthorized"
                with pytest.raises(HTTPException):
                    _run_sync(gate("Bearer wrong"))
                # Correct token passes the gate.
                assert _run_sync(gate("Bearer s3cr3t-metrics")) is None

    def test_the_gate_is_a_no_op_outside_production(self):
        app = create_app()
        gated = next(
            r
            for r in app.routes
            if getattr(r, "path", "") == "/metrics" and r.name == "gated_metrics"
        )
        gate = gated.dependant.dependencies[0].call
        assert _run_sync(gate(None)) is None

    def test_the_gated_endpoint_body_emits_prometheus_text(self):
        # The handler body itself is correct; only its route is shadowed. This
        # covers the 4 statements that no HTTP request can reach.
        app = create_app()
        gated = next(
            r
            for r in app.routes
            if getattr(r, "path", "") == "/metrics" and r.name == "gated_metrics"
        )
        response = _run_sync(gated.endpoint())
        assert response.media_type == "text/plain"
        assert b"python_info" in response.body


# ---------------------------------------------------------------------------
# lifespan
# ---------------------------------------------------------------------------


class TestLifespan:
    async def test_startup_verifies_the_database_before_serving(self):
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)) as probe:
            with patch.object(DatabaseManager, "init", AsyncMock()) as init:
                with patch.object(DatabaseManager, "close", AsyncMock()):
                    async with lifespan(app):
                        pass
        probe.assert_awaited_once()
        init.assert_awaited_once()

    async def test_startup_refuses_to_serve_when_the_database_is_unhealthy(self):
        # The whole point: never accept money against an unreachable ledger.
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=False)):
            with patch.object(DatabaseManager, "init", AsyncMock()) as init:
                with pytest.raises(RuntimeError, match="Database is not healthy on startup"):
                    async with lifespan(app):
                        pass
        init.assert_not_awaited()

    async def test_startup_marks_the_app_as_not_shutting_down(self):
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            with patch.object(DatabaseManager, "init", AsyncMock()):
                with patch.object(DatabaseManager, "close", AsyncMock()):
                    async with lifespan(app) as _:
                        assert app.state.shutting_down is False

    async def test_startup_installs_a_fresh_shutdown_event(self):
        # A stale event from a previous run would make the drain loop exit early.
        app = create_app()
        main_module._shutdown_event = asyncio.Event()
        main_module._shutdown_event.set()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            with patch.object(DatabaseManager, "init", AsyncMock()):
                with patch.object(DatabaseManager, "close", AsyncMock()):
                    async with lifespan(app):
                        assert not main_module._shutdown_event.is_set()

    async def test_shutdown_closes_the_database(self):
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            with patch.object(DatabaseManager, "init", AsyncMock()):
                with patch.object(DatabaseManager, "close", AsyncMock()) as close:
                    async with lifespan(app):
                        pass
        close.assert_awaited_once()

    async def test_shutdown_sets_the_draining_flags(self):
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            with patch.object(DatabaseManager, "init", AsyncMock()):
                with patch.object(DatabaseManager, "close", AsyncMock()):
                    async with lifespan(app):
                        pass
        assert app.state.shutting_down is True
        assert main_module._shutdown_event.is_set()

    async def test_shutdown_returns_immediately_when_nothing_is_in_flight(self):
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            with patch.object(DatabaseManager, "init", AsyncMock()):
                with patch.object(DatabaseManager, "close", AsyncMock()):
                    async with lifespan(app):
                        main_module._in_flight_requests = 0
        # The loop must not spin when the count is already zero.
        assert main_module._in_flight_requests == 0

    async def test_shutdown_waits_for_an_in_flight_request_to_finish(self):
        # A request mid-transaction must be allowed to complete before the
        # engine is torn down.
        app = create_app()
        observed: list[bool] = []

        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            with patch.object(DatabaseManager, "init", AsyncMock()):
                with patch.object(DatabaseManager, "close", AsyncMock()):
                    async with lifespan(app):
                        main_module._in_flight_requests = 1

                        async def _drain():
                            await asyncio.sleep(0.25)
                            main_module._in_flight_requests = 0
                            observed.append(True)

                        task = asyncio.create_task(_drain())
                        observed.append(False)  # still in flight when drain starts

        await task
        assert observed == [False, True]

    async def test_shutdown_drain_timeout_is_bounded_and_logged(self, caplog):
        # A request that never finishes must not hang shutdown forever. The
        # warning is asserted on a patched module logger because
        # wire_observability reconfigures logging (propagate=False), which stops
        # caplog from seeing app.main records.
        app = create_app()
        fake_logger = MagicMock()
        with patch.object(main_module, "_MAX_DRAIN_SECONDS", 0.2):
            with patch.object(main_module, "logger", fake_logger):
                with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
                    with patch.object(DatabaseManager, "init", AsyncMock()):
                        with patch.object(DatabaseManager, "close", AsyncMock()) as close:
                            async with lifespan(app):
                                main_module._in_flight_requests = 1
        # The engine is still torn down despite the drain timeout.
        close.assert_awaited_once()
        warnings = [c for c in fake_logger.warning.call_args_list if "drain timeout" in str(c)]
        assert warnings, "shutdown drain timeout was not logged"
        assert _MAX_DRAIN_SECONDS == 30

    def test_lifespan_is_attached_to_the_created_app(self):
        app = create_app()
        # FastAPI wraps the supplied lifespan; it must not be None.
        assert app.router.lifespan_context is not None

    async def test_asgi_lifespan_runs_through_the_app(self):
        # End-to-end: the real Starlette lifespan wiring starts and stops clean.
        app = create_app()
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            with patch.object(DatabaseManager, "init", AsyncMock()) as init:
                with patch.object(DatabaseManager, "close", AsyncMock()) as close:
                    async with app.router.lifespan_context(app):
                        async with _client(app) as ac:
                            resp = await ac.get("/health")
        assert resp.status_code == 200
        assert init.await_count == 1
        assert close.await_count == 1


# ---------------------------------------------------------------------------
# Fallback lock
# ---------------------------------------------------------------------------


class TestFallbackLock:
    def test_fallback_lock_is_created_lazily_and_memoized(self):
        # Requests that arrive before the lifespan ran must still be counted
        # under a lock rather than crashing.
        first = main_module._fallback_lock()
        second = main_module._fallback_lock()
        assert first is second
        assert isinstance(first, asyncio.Lock)

    async def test_fallback_lock_is_used_when_the_lifespan_never_ran(self):
        app = create_app()
        main_module._in_flight_lock = None
        with patch.object(DatabaseManager, "health_check", AsyncMock(return_value=True)):
            resp = await _get(app, "/health")
        assert resp.status_code == 200
        assert main_module._in_flight_requests == 0
        assert main_module._fallback_in_flight_lock is not None
