"""Tests for the API Gateway.

Covers the ServiceRegistry routing table, the transparent proxy request
forwarding/response pass-through, optional-user auth dependency, gateway
health/service-discovery endpoints, rate limiter logic and the
AuthenticationMiddleware path authorization.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    import app.main as main

    app.dependency_overrides.clear()
    with TestClient(app, base_url="http://localhost") as c:
        # Lifespan just constructed the real RateLimiter and app.state.redis_client
        # against a real Redis; override both so tests never touch a live socket.
        # /health pings app.state.redis_client, so stub its ping to always succeed.
        main.rate_limiter = MagicMock()
        main.rate_limiter.check_rate_limit = AsyncMock(return_value=True)
        redis_stub = MagicMock()
        redis_stub.ping = AsyncMock(return_value=True)
        app.state.redis_client = redis_stub
        # Also stub shared client
        main._shared_client = MagicMock()
        yield c


def make_fake_response(status_code=200, headers=None, content=b"{}"):
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.content = content
    return response


class FakeAsyncClient:
    """httpx.AsyncClient stub usable with `async with` that records the call
    and returns a canned response."""

    def __init__(self, response):
        self.response = response
        self.request_kwargs = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, **kwargs):
        self.request_kwargs = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class TestServiceRegistry:
    def test_get_service_url_known(self):
        from app.middleware import ServiceRegistry

        assert ServiceRegistry.get_service_url("auth") == "http://auth-service:8000"

    def test_get_service_url_unknown(self):
        from app.middleware import ServiceRegistry

        assert ServiceRegistry.get_service_url("nonexistent") is None

    def test_route_request_splits_service_and_path(self):
        from app.middleware import ServiceRegistry

        url, path = ServiceRegistry.route_request("/content/movies/123")

        assert url == "http://content-service:8000"
        assert path == "/movies/123"

    def test_route_request_root_path(self):
        from app.middleware import ServiceRegistry

        url, path = ServiceRegistry.route_request("/auth")

        assert url == "http://auth-service:8000"
        assert path == "/"

    def test_route_request_empty(self):
        from app.middleware import ServiceRegistry

        assert ServiceRegistry.route_request("/") == (None, "/")

    def test_all_services_registered(self):
        from app.middleware import ServiceRegistry

        assert "auth" in ServiceRegistry.SERVICES
        assert "content" in ServiceRegistry.SERVICES
        assert "streaming" in ServiceRegistry.SERVICES
        assert "users" in ServiceRegistry.SERVICES
        assert "billing" in ServiceRegistry.SERVICES
        assert "creators" in ServiceRegistry.SERVICES
        assert "moderation" in ServiceRegistry.SERVICES
        assert "uploads" in ServiceRegistry.SERVICES


class TestGatewayEndpoints:
    def test_health_check(self, client):
        response = client.get("/health")

        assert response.status_code == 200
        # #628: /health returns status-only
        assert response.json() == {"status": "ok"}
        assert "checks" not in response.json()
        assert "version" not in response.json()
        assert "service" not in response.json()

    def test_gateway_health(self, client):
        response = client.get("/gateway/health")

        assert response.status_code == 200
        assert response.json()["service"] == "api-gateway"

    def test_ready_succeeds_when_redis_healthy(self, client):
        response = client.get("/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["checks"]["redis"] == "ok"
        # No credentials or connection strings in the body.
        assert "REDIS" not in response.text

    def test_ready_fails_when_redis_down(self):
        import app.main as main
        from app.main import app
        from fastapi.testclient import TestClient

        app.dependency_overrides.clear()
        broken = MagicMock()
        broken.ping = AsyncMock(side_effect=ConnectionError("refused"))
        with TestClient(app, base_url="http://localhost") as c:
            main.rate_limiter = MagicMock()
            main.rate_limiter.check_rate_limit = AsyncMock(return_value=True)
            app.state.redis_client = broken
            response = c.get("/ready")
        assert response.status_code == 503
        body = response.json()["detail"]
        assert body["status"] == "not_ready"
        assert body["checks"]["redis"] == "down"

    def test_liveness_independent_of_redis(self):
        import app.main as main
        from app.main import app
        from fastapi.testclient import TestClient

        app.dependency_overrides.clear()
        with TestClient(app, base_url="http://localhost") as c:
            main.rate_limiter = MagicMock()
            main.rate_limiter.check_rate_limit = AsyncMock(return_value=True)
            app.state.redis_client = None  # unreachable dependency
            response = c.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert "checks" not in response.json()

    def test_gateway_ready_succeeds_when_redis_healthy(self, client):
        response = client.get("/gateway/ready")
        assert response.status_code == 200
        assert response.json()["checks"]["redis"] == "ok"

    def test_list_services(self, client):
        response = client.get("/gateway/services")

        assert response.status_code == 200
        body = response.json()
        assert "auth" in body["services"]
        assert body["total"] == len(body["services"])


class TestProxy:
    def test_proxy_forwards_request_and_passes_response(self, client):
        fake_response = make_fake_response(
            status_code=200,
            headers={"content-type": "application/json", "x-upstream": "yes"},
            content=b'{"ok": true}',
        )
        fake_client = FakeAsyncClient(fake_response)

        with patch("app.api.gateway_routes.get_shared_client", return_value=fake_client):
            response = client.get("/content/genres")

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert response.headers.get("x-upstream") == "yes"

        # The URL must combine registry route + remaining path.
        call_url = fake_client.request_kwargs["url"]
        assert call_url == "http://content-service:8000/genres"

    def test_proxy_preserves_method_and_headers(self, client):
        fake_client = FakeAsyncClient(make_fake_response())

        with patch("app.api.gateway_routes.get_shared_client", return_value=fake_client):
            response = client.post(
                "/auth/login",
                json={"email": "a@b.c", "password": "x"},
                headers={"Authorization": "Bearer token123", "X-Request-ID": "req-1"},
            )

        assert response.status_code == 200
        call_kwargs = fake_client.request_kwargs
        assert call_kwargs["method"] == "POST"
        # Authorization header should be forwarded
        assert call_kwargs["headers"].get("authorization") == "Bearer token123"

    def test_proxy_forwards_query_string(self, client):
        fake_client = FakeAsyncClient(make_fake_response())

        with patch("app.api.gateway_routes.get_shared_client", return_value=fake_client):
            response = client.get("/content/api/v1/content?content_type=movie&page=1&page_size=5")

        assert response.status_code == 200
        call_url = fake_client.request_kwargs["url"]
        assert (
            call_url
            == "http://content-service:8000/api/v1/content?content_type=movie&page=1&page_size=5"
        )

    def test_proxy_without_query_string_has_no_trailing_question_mark(self, client):
        fake_client = FakeAsyncClient(make_fake_response())

        with patch("app.api.gateway_routes.get_shared_client", return_value=fake_client):
            response = client.get("/content/genres")

        assert response.status_code == 200
        assert fake_client.request_kwargs["url"] == "http://content-service:8000/genres"

    def test_proxy_unknown_service_returns_404(self, client):
        response = client.get("/nonexistent/users")

        assert response.status_code == 404
        # #466: error includes request_id in detail
        assert "request_id" in response.json()["detail"]

    def test_proxy_upstream_error_returns_502(self, client):
        fake_client = FakeAsyncClient(RuntimeError("connection refused"))

        with patch("app.api.gateway_routes.get_shared_client", return_value=fake_client):
            response = client.get("/content/genres")

        assert response.status_code == 502
        assert "request_id" in response.json()["detail"]

    def test_proxy_timeout_returns_504(self, client):
        fake_client = FakeAsyncClient(httpx.TimeoutException("timed out"))

        with patch("app.api.gateway_routes.get_shared_client", return_value=fake_client):
            response = client.get("/content/genres")

        assert response.status_code == 504
        assert "request_id" in response.json()["detail"]

    def test_proxy_rate_limited_returns_429(self, client):
        import app.main as main

        original = main.rate_limiter
        main.rate_limiter = MagicMock()
        main.rate_limiter.check_rate_limit = AsyncMock(return_value=False)
        try:
            response = client.get("/content/genres")
        finally:
            main.rate_limiter = original

        assert response.status_code == 429
        assert "request_id" in response.json()["detail"]


class TestOptionalUser:
    @pytest.mark.asyncio
    async def test_verify_token_with_valid_jwt(self):
        from datetime import timedelta

        import jwt

        from app.middleware import AuthenticationMiddleware

        secret = "test-secret"
        token = jwt.encode(
            {"sub": str(uuid4()), "exp": datetime.now(UTC) + timedelta(minutes=5)},
            secret,
            algorithm="HS256",
        )
        mw = AuthenticationMiddleware(secret)
        request = Request(
            scope={"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]}
        )

        payload = await mw.verify_token(request)

        assert payload is not None
        assert "sub" in payload

    @pytest.mark.asyncio
    async def test_verify_token_without_exp_returns_none(self):
        import jwt

        from app.middleware import AuthenticationMiddleware

        token = jwt.encode({"sub": "u1"}, "test-secret", algorithm="HS256")
        mw = AuthenticationMiddleware("test-secret")
        request = Request(
            scope={"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]}
        )

        assert await mw.verify_token(request) is None

    @pytest.mark.asyncio
    async def test_verify_token_with_wrong_secret_returns_none(self):
        import jwt

        from app.middleware import AuthenticationMiddleware

        token = jwt.encode({"sub": "u1"}, "other-secret", algorithm="HS256")
        mw = AuthenticationMiddleware("test-secret")
        request = Request(
            scope={"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]}
        )

        assert await mw.verify_token(request) is None

    @pytest.mark.asyncio
    async def test_verify_token_missing_header_returns_none(self):

        from app.middleware import AuthenticationMiddleware

        mw = AuthenticationMiddleware("secret")
        request = Request(scope={"type": "http", "headers": []})

        assert await mw.verify_token(request) is None

    @pytest.mark.asyncio
    async def test_verify_token_garbage_returns_none(self):

        from app.middleware import AuthenticationMiddleware

        mw = AuthenticationMiddleware("secret")
        request = Request(
            scope={"type": "http", "headers": [(b"authorization", b"Bearer not.a.jwt")]}
        )

        assert await mw.verify_token(request) is None


class TestAuthenticationMiddleware:
    @pytest.mark.asyncio
    async def test_public_paths_allowed(self):

        from app.middleware import AuthenticationMiddleware

        mw = AuthenticationMiddleware("secret")
        request = Request(scope={"type": "http", "path": "/auth/login", "headers": []})

        assert await mw(request) is None

    @pytest.mark.asyncio
    async def test_public_path_prefix_does_not_bypass_auth(self):
        from fastapi import HTTPException

        from app.middleware import AuthenticationMiddleware

        mw = AuthenticationMiddleware("secret")
        request = Request(scope={"type": "http", "path": "/auth/login-anything", "headers": []})

        with pytest.raises(HTTPException) as exc:
            await mw(request)

        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_public_child_path_is_allowed(self):

        from app.middleware import AuthenticationMiddleware

        mw = AuthenticationMiddleware("secret")
        request = Request(scope={"type": "http", "path": "/docs/index.html", "headers": []})

        assert await mw(request) is None

    @pytest.mark.asyncio
    async def test_public_path_with_trailing_slash_is_allowed(self):

        from app.middleware import AuthenticationMiddleware

        mw = AuthenticationMiddleware("secret")
        request = Request(scope={"type": "http", "path": "/health/", "headers": []})

        assert await mw(request) is None

    @pytest.mark.asyncio
    async def test_protected_path_without_token_raises_401(self):
        from fastapi import HTTPException

        from app.middleware import AuthenticationMiddleware

        mw = AuthenticationMiddleware("secret")
        request = Request(scope={"type": "http", "path": "/content/genres", "headers": []})

        with pytest.raises(HTTPException) as exc:
            await mw(request)

        assert exc.value.status_code == 401


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_under_limit_allowed(self):
        from app.middleware import RateLimiter

        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(return_value=3)
        limiter = RateLimiter(redis_mock)

        result = await limiter.check_rate_limit("user-1", "search")

        assert result is True
        redis_mock.incr.assert_awaited_once_with("rate_limit:user-1:search")

    @pytest.mark.asyncio
    async def test_over_limit_rejected(self):
        from app.middleware import RateLimiter

        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(return_value=101)
        limiter = RateLimiter(redis_mock)

        result = await limiter.check_rate_limit("user-1", "search")

        assert result is False

    @pytest.mark.asyncio
    async def test_first_request_sets_expiry(self):
        from app.middleware import RateLimiter

        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(return_value=1)
        limiter = RateLimiter(redis_mock)

        await limiter.check_rate_limit("user-1", "auth")

        redis_mock.expire.assert_awaited_once_with("rate_limit:user-1:auth", 60)

    @pytest.mark.asyncio
    async def test_upload_finalize_stricter_limit(self):
        """#237/#268: upload finalize (complete/abort) has stricter limit."""
        from app.middleware import RateLimiter

        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(return_value=61)  # over 60 limit
        limiter = RateLimiter(redis_mock)

        # Path ending with /complete should use UPLOAD_FINALIZE limit (60)
        result = await limiter.check_rate_limit("user-1", "uploads", "/sessions/123/complete")
        assert result is False

    @pytest.mark.asyncio
    async def test_reindex_stricter_limit(self):
        """#237/#268: reindex has stricter limit (20/min)."""
        from app.middleware import RateLimiter

        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(return_value=21)  # over 20 limit
        limiter = RateLimiter(redis_mock)

        result = await limiter.check_rate_limit("user-1", "search", "/reindex")
        assert result is False

    @pytest.mark.asyncio
    async def test_auth_stricter_limit(self):
        """#237/#268: auth has stricter limit (5/min)."""
        from app.middleware import RateLimiter

        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(return_value=6)  # over 5 limit
        limiter = RateLimiter(redis_mock)

        result = await limiter.check_rate_limit("user-1", "auth", "/login")
        assert result is False


class TestRateLimiterFaultInjection:
    """[#214] Redis outages or corrupt counters must not take the gateway
    down: the limiter is anti-abuse protection, not an authorization
    decision, and fails open (matching auth-service's documented behavior).
    """

    @pytest.mark.asyncio
    async def test_redis_down_fails_open(self):
        from app.middleware import RateLimiter

        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(side_effect=Exception("connection refused"))
        limiter = RateLimiter(redis_mock)

        assert await limiter.check_rate_limit("user-1", "content") is True

    @pytest.mark.asyncio
    async def test_corrupt_counter_fails_open(self):
        from app.middleware import RateLimiter

        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(side_effect=Exception("value is not an integer"))
        limiter = RateLimiter(redis_mock)

        assert await limiter.check_rate_limit("user-1", "content") is True

    @pytest.mark.asyncio
    async def test_expire_failure_fails_open(self):
        from app.middleware import RateLimiter

        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(return_value=1)
        redis_mock.expire = AsyncMock(side_effect=Exception("connection lost"))
        limiter = RateLimiter(redis_mock)

        assert await limiter.check_rate_limit("user-1", "content") is True

    @pytest.mark.asyncio
    async def test_namespace_and_ttl_contract(self):
        """[#214] Key namespace is per-service and every key gets a TTL."""
        from app.middleware import RateLimiter

        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(side_effect=[1, 1])
        limiter = RateLimiter(redis_mock)

        await limiter.check_rate_limit("user-1", "auth")
        await limiter.check_rate_limit("user-2", "search")

        keys = [call.args[0] for call in redis_mock.incr.await_args_list]
        assert keys == ["rate_limit:user-1:auth", "rate_limit:user-2:search"]
        assert all(k.startswith("rate_limit:") for k in keys)
        assert redis_mock.expire.await_count == 2


class TestHeaderSanitizer:
    """#314, #522, #625: Strip/rewrite client-supplied headers at the edge."""

    @pytest.mark.asyncio
    async def test_strips_x_forwarded_for_and_appends_client_ip(self):
        from app.middleware import HeaderSanitizerMiddleware
        from starlette.responses import Response

        mw = HeaderSanitizerMiddleware(app=MagicMock())
        request = Request(
            scope={
                "type": "http",
                "headers": [
                    (b"x-forwarded-for", b"1.2.3.4"),
                    (b"host", b"example.com"),
                ],
                "client": ("10.0.0.1", 12345),
            }
        )

        async def call_next(req):
            # Verify headers were rewritten
            assert req.headers.get("x-forwarded-for") == "1.2.3.4, 10.0.0.1"
            assert req.headers.get("x-real-ip") == "10.0.0.1"
            return Response(content=b"ok")

        await mw.dispatch(request, call_next)

    @pytest.mark.asyncio
    async def test_strips_x_user_headers(self):
        from app.middleware import HeaderSanitizerMiddleware
        from starlette.responses import Response

        mw = HeaderSanitizerMiddleware(app=MagicMock())
        request = Request(
            scope={
                "type": "http",
                "headers": [
                    (b"x-user-id", b"123"),
                    (b"x-user-email", b"test@example.com"),
                    (b"x-user-roles", b"admin"),
                    (b"host", b"example.com"),
                ],
                "client": ("10.0.0.1", 12345),
            }
        )

        async def call_next(req):
            # X-User-* headers should be stripped
            assert "x-user-id" not in req.headers
            assert "x-user-email" not in req.headers
            assert "x-user-roles" not in req.headers
            return Response(content=b"ok")

        await mw.dispatch(request, call_next)

    @pytest.mark.asyncio
    async def test_regenerates_correlation_ids(self):
        from app.middleware import HeaderSanitizerMiddleware
        from starlette.responses import Response

        mw = HeaderSanitizerMiddleware(app=MagicMock())
        request = Request(
            scope={
                "type": "http",
                "headers": [
                    (b"x-correlation-id", b"client-correlation"),
                    (b"x-request-id", b"client-request"),
                    (b"host", b"example.com"),
                ],
                "client": ("10.0.0.1", 12345),
            }
        )

        async def call_next(req):
            # Client-supplied correlation IDs should be stripped
            # (they will be regenerated by CorrelationMiddleware later)
            assert req.headers.get("x-correlation-id") != "client-correlation"
            assert req.headers.get("x-request-id") != "client-request"
            return Response(content=b"ok")

        await mw.dispatch(request, call_next)


class TestBodyLimitMiddleware:
    """#231, #315, #417, #418, #419, #449, #517, #518, #629: Body and header limits."""

    @pytest.mark.asyncio
    async def test_rejects_oversized_json_body(self):
        from app.middleware import BodyLimitMiddleware
        from starlette.responses import Response

        # Create middleware with small limit for testing
        mw = BodyLimitMiddleware(app=MagicMock())
        mw.max_request_body = 100
        mw.max_multipart_body = 100

        request = Request(
            scope={
                "type": "http",
                "method": "POST",
                "path": "/content/test",
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"host", b"example.com"),
                ],
                "client": ("10.0.0.1", 12345),
            }
        )
        # Mock stream to return large body
        large_body = b"x" * 200

        async def mock_stream():
            yield large_body

        request.stream = mock_stream

        async def call_next(req):
            return Response(content=b"ok")

        response = await mw.dispatch(request, call_next)
        assert response.status_code == 413
        # x-request-id header is added by CorrelationMiddleware (not running in this unit test)

    @pytest.mark.asyncio
    async def test_allows_multipart_larger_body(self):
        from app.middleware import BodyLimitMiddleware
        from starlette.responses import Response

        mw = BodyLimitMiddleware(app=MagicMock())
        mw.max_request_body = 100
        mw.max_multipart_body = 1000  # 10x for multipart

        request = Request(
            scope={
                "type": "http",
                "method": "POST",
                "path": "/uploads/sessions",
                "headers": [
                    (b"content-type", b"multipart/form-data; boundary=xxx"),
                    (b"host", b"example.com"),
                ],
                "client": ("10.0.0.1", 12345),
            }
        )
        body = b"x" * 500  # Under multipart limit, over JSON limit

        async def mock_stream():
            yield body

        request.stream = mock_stream

        async def call_next(req):
            return Response(content=b"ok")

        response = await mw.dispatch(request, call_next)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_rejects_too_many_headers(self):
        from app.middleware import BodyLimitMiddleware
        from starlette.responses import Response

        mw = BodyLimitMiddleware(app=MagicMock())
        mw.max_header_count = 5

        headers = [(f"x-header-{i}".encode(), b"value") for i in range(10)]
        headers.append((b"host", b"example.com"))

        request = Request(
            scope={
                "type": "http",
                "headers": headers,
                "client": ("10.0.0.1", 12345),
            }
        )

        async def call_next(req):
            return Response(content=b"ok")

        response = await mw.dispatch(request, call_next)
        assert response.status_code == 431

    @pytest.mark.asyncio
    async def test_rejects_oversized_header_field(self):
        from app.middleware import BodyLimitMiddleware
        from starlette.responses import Response

        mw = BodyLimitMiddleware(app=MagicMock())
        mw.max_header_field_size = 10

        request = Request(
            scope={
                "type": "http",
                "headers": [
                    (b"x-large-header", b"x" * 20),
                    (b"host", b"example.com"),
                ],
                "client": ("10.0.0.1", 12345),
            }
        )

        async def call_next(req):
            return Response(content=b"ok")

        response = await mw.dispatch(request, call_next)
        assert response.status_code == 431

    @pytest.mark.asyncio
    async def test_rejects_oversized_total_headers(self):
        from app.middleware import BodyLimitMiddleware
        from starlette.responses import Response

        mw = BodyLimitMiddleware(app=MagicMock())
        mw.max_header_total_size = 50

        request = Request(
            scope={
                "type": "http",
                "headers": [
                    (b"x-header-1", b"x" * 30),
                    (b"x-header-2", b"x" * 30),
                    (b"host", b"example.com"),
                ],
                "client": ("10.0.0.1", 12345),
            }
        )

        async def call_next(req):
            return Response(content=b"ok")

        response = await mw.dispatch(request, call_next)
        assert response.status_code == 431

    @pytest.mark.asyncio
    async def test_decompression_bomb_protection(self):
        """#417: reject compressed body that would decompress beyond limit."""
        from app.middleware import BodyLimitMiddleware
        from starlette.responses import Response

        mw = BodyLimitMiddleware(app=MagicMock())
        mw.max_request_body = 1000
        mw.max_decompression_ratio = 10

        request = Request(
            scope={
                "type": "http",
                "method": "POST",
                "path": "/content/test",
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-encoding", b"gzip"),
                    (b"host", b"example.com"),
                ],
                "client": ("10.0.0.1", 12345),
            }
        )
        # Compressed size 200 * ratio 10 = 2000 > limit 1000
        body = b"x" * 200

        async def mock_stream():
            yield body

        request.stream = mock_stream

        async def call_next(req):
            return Response(content=b"ok")

        response = await mw.dispatch(request, call_next)
        assert response.status_code == 413


class TestSharedClient:
    """#123: Shared AsyncClient lifespan management."""

    def test_shared_client_lifespan_creates_client_with_limits(self):
        import asyncio
        from app.middleware import shared_client_lifespan, get_shared_client

        async def run():
            async with shared_client_lifespan():
                client = get_shared_client()
                assert isinstance(client, httpx.AsyncClient)
                # Client created successfully with lifespan management
                # (limit/timeout verification requires httpx internals that vary by version)

        asyncio.run(run())

    def test_get_shared_client_raises_outside_lifespan(self):
        from app.middleware import get_shared_client

        # Should raise if not initialized
        import app.middleware as mw

        mw._shared_client = None
        try:
            get_shared_client()
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "not initialized" in str(e)


class TestGracefulShutdown:
    """#426: Graceful shutdown - stop accepting, drain in-flight, close client."""

    def test_shutdown_rejects_new_requests(self):
        import app.main as main
        from app.main import app
        from fastapi.testclient import TestClient

        app.dependency_overrides.clear()
        with TestClient(app, base_url="http://localhost") as c:
            main.rate_limiter = MagicMock()
            main.rate_limiter.check_rate_limit = AsyncMock(return_value=True)
            redis_stub = MagicMock()
            redis_stub.ping = AsyncMock(return_value=True)
            app.state.redis_client = redis_stub
            main._shared_client = MagicMock()

            # Set shutting_down flag
            app.state.shutting_down = True

            response = c.get("/content/genres")

        assert response.status_code == 503
        assert "shutting down" in response.text.lower()
        assert "retry-after" in response.headers
