"""Tests for the API Gateway.

Covers the ServiceRegistry routing table, the transparent proxy request
forwarding/response pass-through, optional-user auth dependency, gateway
health/service-discovery endpoints, rate limiter logic and the
AuthenticationMiddleware path authorization.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
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
        yield c


def make_fake_response(status_code=200, headers=None, content=b"{}"):
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.content = content
    return response


class FakeAsyncClient:
    """httpx.AsyncClient stub usable with `async with` that records the call
    and returns a canned response via streaming interface."""

    def __init__(self, response):
        self.response = response
        self.request_kwargs = None
        self.stream_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, **kwargs):
        self.request_kwargs = kwargs
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def stream(self, method, url, **kwargs):
        """Return an async context manager yielding a streaming response."""
        self.request_kwargs = {"method": method, "url": url, **kwargs}
        self.stream_called = True

        class StreamResponse:
            def __init__(self, resp):
                self.resp = resp
                self.status_code = getattr(resp, "status_code", 200)
                self.headers = getattr(resp, "headers", {})

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def aiter_bytes(self):
                content = getattr(self.resp, "content", b"{}")
                if isinstance(content, str):
                    content = content.encode()
                if content:
                    yield content

        if isinstance(self.response, Exception):
            raise self.response

        return StreamResponse(self.response)


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


class TestGatewayEndpoints:
    def test_health_check(self, client):
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

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
        assert response.json()["status"] == "healthy"
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

        with patch("app.api.gateway_routes.httpx.AsyncClient", return_value=fake_client):
            response = client.get("/content/genres")

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert response.headers.get("x-upstream") == "yes"

        # The URL must combine registry route + remaining path.
        call_url = fake_client.request_kwargs["url"]
        assert call_url == "http://content-service:8000/genres"

    def test_proxy_preserves_method_and_headers(self, client):
        fake_client = FakeAsyncClient(make_fake_response())

        with patch("app.api.gateway_routes.httpx.AsyncClient", return_value=fake_client):
            response = client.post(
                "/auth/login",
                json={"email": "a@b.c", "password": "x"},
                headers={"Authorization": "Bearer token123", "X-Request-ID": "req-1"},
            )

        assert response.status_code == 200
        call_kwargs = fake_client.request_kwargs
        assert call_kwargs["method"] == "POST"
        assert call_kwargs["headers"].get("x-request-id") == "req-1"

    def test_proxy_forwards_query_string(self, client):
        fake_client = FakeAsyncClient(make_fake_response())

        with patch("app.api.gateway_routes.httpx.AsyncClient", return_value=fake_client):
            response = client.get("/content/api/v1/content?content_type=movie&page=1&page_size=5")

        assert response.status_code == 200
        call_url = fake_client.request_kwargs["url"]
        assert (
            call_url
            == "http://content-service:8000/api/v1/content?content_type=movie&page=1&page_size=5"
        )

    def test_proxy_without_query_string_has_no_trailing_question_mark(self, client):
        fake_client = FakeAsyncClient(make_fake_response())

        with patch("app.api.gateway_routes.httpx.AsyncClient", return_value=fake_client):
            response = client.get("/content/genres")

        assert response.status_code == 200
        assert fake_client.request_kwargs["url"] == "http://content-service:8000/genres"

    def test_proxy_unknown_service_returns_404(self, client):
        response = client.get("/nonexistent/users")

        assert response.status_code == 404

    def test_proxy_upstream_error_returns_502(self, client):
        fake_client = FakeAsyncClient(RuntimeError("connection refused"))

        with patch("app.api.gateway_routes.httpx.AsyncClient", return_value=fake_client):
            response = client.get("/content/genres")

        assert response.status_code == 502

    def test_proxy_timeout_returns_504(self, client):
        import httpx

        fake_client = FakeAsyncClient(httpx.TimeoutException("timed out"))

        with patch("app.api.gateway_routes.httpx.AsyncClient", return_value=fake_client):
            response = client.get("/content/genres")

        assert response.status_code == 504

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
        assert response.json()["detail"] == "Rate limit exceeded"


class TestOptionalUser:
    @pytest.mark.asyncio
    async def test_verify_token_with_valid_jwt(self):
        from datetime import timedelta

        import jwt
        from fastapi import Request

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
        from fastapi import Request

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
        from fastapi import Request

        from app.middleware import AuthenticationMiddleware

        token = jwt.encode({"sub": "u1"}, "other-secret", algorithm="HS256")
        mw = AuthenticationMiddleware("test-secret")
        request = Request(
            scope={"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]}
        )

        assert await mw.verify_token(request) is None

    @pytest.mark.asyncio
    async def test_verify_token_missing_header_returns_none(self):
        from fastapi import Request

        from app.middleware import AuthenticationMiddleware

        mw = AuthenticationMiddleware("secret")
        request = Request(scope={"type": "http", "headers": []})

        assert await mw.verify_token(request) is None

    @pytest.mark.asyncio
    async def test_verify_token_garbage_returns_none(self):
        from fastapi import Request

        from app.middleware import AuthenticationMiddleware

        mw = AuthenticationMiddleware("secret")
        request = Request(
            scope={"type": "http", "headers": [(b"authorization", b"Bearer not.a.jwt")]}
        )

        assert await mw.verify_token(request) is None


class TestAuthenticationMiddleware:
    @pytest.mark.asyncio
    async def test_public_paths_allowed(self):
        from fastapi import Request

        from app.middleware import AuthenticationMiddleware

        mw = AuthenticationMiddleware("secret")
        request = Request(scope={"type": "http", "path": "/auth/login", "headers": []})

        assert await mw(request) is None

    @pytest.mark.asyncio
    async def test_public_path_prefix_does_not_bypass_auth(self):
        from fastapi import HTTPException, Request

        from app.middleware import AuthenticationMiddleware

        mw = AuthenticationMiddleware("secret")
        request = Request(scope={"type": "http", "path": "/auth/login-anything", "headers": []})

        with pytest.raises(HTTPException) as exc:
            await mw(request)

        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_public_child_path_is_allowed(self):
        from fastapi import Request

        from app.middleware import AuthenticationMiddleware

        mw = AuthenticationMiddleware("secret")
        request = Request(scope={"type": "http", "path": "/docs/index.html", "headers": []})

        assert await mw(request) is None

    @pytest.mark.asyncio
    async def test_public_path_with_trailing_slash_is_allowed(self):
        from fastapi import Request

        from app.middleware import AuthenticationMiddleware

        mw = AuthenticationMiddleware("secret")
        request = Request(scope={"type": "http", "path": "/health/", "headers": []})

        assert await mw(request) is None

    @pytest.mark.asyncio
    async def test_protected_path_without_token_raises_401(self):
        from fastapi import HTTPException, Request

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
        from app.core.settings import settings

        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(return_value=3)
        limiter = RateLimiter(redis_mock)

        result = await limiter.check_rate_limit("user-1", "search")

        assert result is True
        redis_mock.incr.assert_awaited_once_with(f"rate_limit:{settings.ENVIRONMENT}:search:user-1")

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
        from app.core.settings import settings

        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(return_value=1)
        limiter = RateLimiter(redis_mock)

        await limiter.check_rate_limit("user-1", "auth")

        # First request: expire called with jittered TTL (60-75s)
        assert redis_mock.expire.await_count == 1
        call_args = redis_mock.expire.await_args
        assert call_args[0][0] == f"rate_limit:{settings.ENVIRONMENT}:auth:user-1"
        assert 60 <= call_args[0][1] <= 75
