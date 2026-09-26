import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import Request
from fastapi.responses import Response, StreamingResponse
from fastapi.testclient import TestClient

from app.middleware import BodyLimitMiddleware, _release_global_budget, _acquire_global_budget
import app.middleware as mw
from app.core.settings import settings


def _make_request(
    method="POST",
    path="/content/api/v1/items",
    headers=None,
    body_chunks=None,
    client_host="10.0.0.1",
):
    headers = headers or []
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": raw_headers,
        "client": (client_host, 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
        "root_path": "",
    }
    request = Request(scope)
    chunks = body_chunks or []

    async def stream():
        for c in chunks:
            yield c

    request.stream = stream
    request.headers.__dict__.get("_list", raw_headers)
    return request


async def _call_next_ok(request):
    body = b""
    async for chunk in request.stream():
        body += chunk
    return Response(content=b"ok:" + body)


async def _call_next_stream(request):
    return Response(content=b"ok")


@pytest.mark.asyncio
async def test_stream_within_limit_passes():
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 1024
    middleware.max_multipart_body = 2048
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    headers = [("content-type", "application/json"), ("content-length", "5")]
    req = _make_request(headers=headers, body_chunks=[b"hello"])
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 200
    assert b"hello" in resp.body


@pytest.mark.asyncio
async def test_stream_exceeds_limit_returns_413():
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 10
    middleware.max_multipart_body = 20
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    headers = [("content-type", "application/json")]
    req = _make_request(headers=headers, body_chunks=[b"12345", b"67890", b"1"])
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 413
    assert b"exceeds limit" in resp.body


@pytest.mark.asyncio
async def test_content_length_early_reject():
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 10
    middleware.max_multipart_body = 20
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    headers = [("content-type", "application/json"), ("content-length", "100")]
    req = _make_request(headers=headers, body_chunks=[])
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 413
    assert b"too large" in resp.body


@pytest.mark.asyncio
async def test_decompression_bomb_rejected():
    import gzip

    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 100
    middleware.max_multipart_body = 200
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    middleware.max_decompression_ratio = 10
    bomb = gzip.compress(b"a" * 500)
    headers = [
        ("content-type", "application/json"),
        ("content-encoding", "gzip"),
        ("content-length", str(len(bomb))),
    ]
    req = _make_request(headers=headers, body_chunks=[bomb])
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 413
    assert b"decompressed" in resp.body.lower() or b"bomb" in resp.body.lower()


@pytest.mark.asyncio
async def test_bounded_streaming_does_not_buffer_full_body(monkeypatch):
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 5 * 1024 * 1024
    middleware.max_multipart_body = 10 * 1024 * 1024
    middleware.max_response_body = 10 * 1024 * 1024
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    chunk = b"x" * 65536
    chunks = [chunk for _ in range(5)]
    headers = [("content-type", "application/json")]
    req = _make_request(headers=headers, body_chunks=chunks)

    seen = []

    async def call_next(req2):
        async for c in req2.stream():
            seen.append(len(c))
            assert len(c) <= 65536 * 2
        return Response(content=b"ok")

    resp = await middleware.dispatch(req, call_next)
    assert resp.status_code == 200
    assert sum(seen) == 65536 * 5
    assert len(seen) == 5


@pytest.mark.asyncio
async def test_global_budget_exceeded_returns_429(monkeypatch):
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    monkeypatch.setattr(settings, "GATEWAY_GLOBAL_BODY_BUDGET_BYTES", 10)
    monkeypatch.setattr(settings, "GATEWAY_MAX_CONCURRENT_BODIES", 20)
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 100
    middleware.max_multipart_body = 200
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    headers = [("content-type", "application/json"), ("content-length", "20")]
    req = _make_request(headers=headers, body_chunks=[b"x" * 5])

    acquired = await _acquire_global_budget(10)
    assert acquired is True
    try:
        resp = await middleware.dispatch(req, _call_next_ok)
        assert resp.status_code == 429
        assert b"Global body budget exceeded" in resp.body
    finally:
        await _release_global_budget(10)
        monkeypatch.setattr(settings, "GATEWAY_GLOBAL_BODY_BUDGET_BYTES", 50 * 1024 * 1024)
        monkeypatch.setattr(settings, "GATEWAY_MAX_CONCURRENT_BODIES", 20)
        mw._GLOBAL_BODY_CURRENT = 0
        mw._GLOBAL_STREAM_COUNT = 0


@pytest.mark.asyncio
async def test_concurrent_stream_count_budget(monkeypatch):
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    monkeypatch.setattr(settings, "GATEWAY_MAX_CONCURRENT_BODIES", 1)
    monkeypatch.setattr(settings, "GATEWAY_GLOBAL_BODY_BUDGET_BYTES", 50 * 1024 * 1024)
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 1000
    middleware.max_multipart_body = 2000
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    headers = [("content-type", "application/json"), ("content-length", "10")]
    req = _make_request(headers=headers, body_chunks=[b"hello"])

    await _acquire_global_budget(10)
    try:
        resp = await middleware.dispatch(req, _call_next_ok)
        assert resp.status_code == 429
    finally:
        await _release_global_budget(10)
        monkeypatch.setattr(settings, "GATEWAY_MAX_CONCURRENT_BODIES", 20)
        mw._GLOBAL_BODY_CURRENT = 0
        mw._GLOBAL_STREAM_COUNT = 0


@pytest.mark.asyncio
async def test_response_streaming_limit():
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 1024
    middleware.max_multipart_body = 2048
    middleware.max_response_body = 10
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536

    async def gen():
        yield b"12345"
        yield b"67890"
        yield b"1"

    streaming = StreamingResponse(content=gen())

    async def call_next(req):
        return streaming

    headers = [("content-type", "application/json")]
    req = _make_request(method="GET", headers=headers, body_chunks=[])
    resp = await middleware.dispatch(req, call_next)
    assert isinstance(resp, StreamingResponse)
    total = 0
    caught = False
    try:
        async for chunk in resp.body_iterator:
            total += len(chunk)
    except ValueError as exc:
        assert "exceeds limit" in str(exc)
        caught = True
    assert caught is True


class _FakeRawHeaders(dict):
    """Dict that also exposes httpx's ``.raw`` list of (bytes, bytes) pairs."""

    @property
    def raw(self):
        return [(k.encode("latin-1"), str(v).encode("latin-1")) for k, v in self.items()]


class _FakeStreamResponse:
    def __init__(self, body=b"", status_code=200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = _FakeRawHeaders(headers or {"content-type": "application/json"})

    async def aiter_raw(self):
        if self._body:
            yield self._body


class _FakeStreamContext:
    """Async context manager mimicking ``httpx.AsyncClient.stream``.

    The request body is drained on ``__aenter__`` because real httpx sends the
    request before the response is available.
    """

    def __init__(self, response, content=None, captured=None):
        self._response = response
        self._content = content
        self._captured = captured

    async def __aenter__(self):
        if self._content is not None:
            body = b""
            async for chunk in self._content:
                body += chunk
            self._captured["body"] = body
        return self._response

    async def __aexit__(self, *exc_info):
        return False


def _make_fake_stream(captured, body):
    def fake_stream(method=None, url=None, headers=None, content=None, **kwargs):
        captured["content"] = content
        captured["method"] = method
        captured["url"] = url
        return _FakeStreamContext(
            _FakeStreamResponse(body=body), content=content, captured=captured
        )

    return fake_stream


@pytest.mark.asyncio
async def test_proxy_forwarding_uses_streaming():
    from app.main import app
    import app.main as main
    from fastapi.testclient import TestClient

    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    mock_client = MagicMock()
    captured = {}

    mock_client.stream = _make_fake_stream(captured, b'{"ok": true}')
    with patch("app.middleware.get_shared_client", return_value=mock_client):
        with patch("app.api.gateway_routes.get_shared_client", return_value=mock_client):
            with TestClient(app, base_url="http://test") as client:
                orig_limiter = main.rate_limiter
                mock_limiter = MagicMock()
                mock_limiter.check_rate_limit = AsyncMock(return_value=True)
                # proxy_request awaits acquire_rate_limits() (an async method on
                # the real RateLimiter). A bare MagicMock attribute is not
                # awaitable, so it must be stubbed as an AsyncMock returning
                # the real (allowed, lease) tuple.
                mock_limiter.acquire_rate_limits = AsyncMock(return_value=(True, None))
                mock_limiter.release_rate_limits = AsyncMock(return_value=None)
                main.rate_limiter = mock_limiter
                redis_stub = MagicMock()
                redis_stub.ping = AsyncMock(return_value=True)
                app.state.redis_client = redis_stub
                resp = client.post(
                    "/content/api/v1/items",
                    content=b"hello world",
                    headers={"content-type": "application/json"},
                )
                assert resp.status_code == 200
                assert captured.get("body") == b"hello world"
                assert captured.get("method") == "POST"
                main.rate_limiter = orig_limiter
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0


@pytest.mark.asyncio
async def test_proxy_get_has_no_body_stream():
    from app.main import app
    import app.main as main
    from fastapi.testclient import TestClient

    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    mock_client = MagicMock()
    captured = {}

    mock_client.stream = _make_fake_stream(captured, b"[]")
    with patch("app.middleware.get_shared_client", return_value=mock_client):
        with patch("app.api.gateway_routes.get_shared_client", return_value=mock_client):
            with TestClient(app, base_url="http://test") as client:
                orig_limiter = main.rate_limiter
                mock_limiter = MagicMock()
                mock_limiter.check_rate_limit = AsyncMock(return_value=True)
                # proxy_request awaits acquire_rate_limits() (an async method on
                # the real RateLimiter). A bare MagicMock attribute is not
                # awaitable, so it must be stubbed as an AsyncMock returning
                # the real (allowed, lease) tuple.
                mock_limiter.acquire_rate_limits = AsyncMock(return_value=(True, None))
                mock_limiter.release_rate_limits = AsyncMock(return_value=None)
                main.rate_limiter = mock_limiter
                redis_stub = MagicMock()
                redis_stub.ping = AsyncMock(return_value=True)
                app.state.redis_client = redis_stub
                resp = client.get("/content/api/v1/items")
                assert resp.status_code == 200
                assert captured["content"] is None
                main.rate_limiter = orig_limiter
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0


@pytest.mark.asyncio
async def test_chunk_size_setting_used():
    middleware = BodyLimitMiddleware(app=MagicMock())
    assert hasattr(middleware, "chunk_size")
    assert middleware.chunk_size == getattr(settings, "GATEWAY_BODY_STREAM_CHUNK_SIZE", 65536)


# ---------------------------------------------------------------------------
# Transparent proxy behaviour (app/api/gateway_routes.py -> proxy_request)
# ---------------------------------------------------------------------------


class _FakeUpstreamResponse:
    """Stands in for the httpx streaming response of a backend service.

    ``httpx.Headers`` is used on purpose: its ``.raw`` list keeps duplicate
    fields (e.g. repeated Set-Cookie) exactly as a real upstream would send.
    """

    def __init__(self, chunks=(), status_code=200, headers=()):
        self._chunks = list(chunks)
        self.status_code = status_code
        self.headers = httpx.Headers(list(headers))

    async def aiter_raw(self):
        for chunk in self._chunks:
            yield chunk


class _FakeStreamContext:
    """Async context manager mimicking ``httpx.AsyncClient.stream``.

    The request body is drained on ``__aenter__`` because real httpx sends the
    request before the response is available. ``closed`` records that the
    response was released on exit.
    """

    def __init__(self, response, content, captured, error=None):
        self._response = response
        self._content = content
        self._captured = captured
        self._error = error

    async def __aenter__(self):
        if self._content is not None:
            body = b""
            async for chunk in self._content:
                body += chunk
            self._captured["body"] = body
        if self._error is not None:
            raise self._error
        return self._response

    async def __aexit__(self, *exc_info):
        self._captured["closed"] = True
        return False


class _FakeUpstreamClient:
    def __init__(self, response=None, error=None):
        self.captured = {}
        self._response = response
        self._error = error

    def stream(self, method=None, url=None, headers=None, content=None, **kwargs):
        self.captured.update(
            method=method,
            url=url,
            headers=dict(headers or {}),
            content=content,
            closed=False,
        )
        return _FakeStreamContext(
            self._response, content, self.captured, self._error
        )


def _upstream(chunks=(), status_code=200, headers=(), error=None):
    if error is not None:
        return _FakeUpstreamClient(error=error)
    return _FakeUpstreamClient(
        response=_FakeUpstreamResponse(chunks, status_code, headers)
    )


@contextlib.contextmanager
def gateway(upstream, limiter_result=True, app=None):
    """Yield ``(TestClient, captured, limiter)`` with upstream + limiter stubbed.

    The globals must be replaced *inside* the TestClient context: entering the
    client runs the lifespan, which installs a real ``RateLimiter`` over the
    gateway's globals.
    """
    import app.main as main
    from app.middleware import AuthenticationMiddleware

    if app is None:
        app = main.app
    app.dependency_overrides.clear()

    saved_limiter = main.rate_limiter
    saved_auth = main.auth_middleware
    limiter = MagicMock()
    limiter.check_rate_limit = AsyncMock(return_value=limiter_result)
    # proxy_request awaits acquire_rate_limits() (an async method on the real
    # RateLimiter) and unpacks the real (allowed, lease_id) tuple. A bare
    # MagicMock attribute is not awaitable, so stub it as an AsyncMock and
    # honour limiter_result so a denying limiter still yields a 429.
    limiter.acquire_rate_limits = AsyncMock(
        return_value=(limiter_result, None)
    )
    limiter.release_rate_limits = AsyncMock(return_value=None)

    try:
        with patch(
            "app.api.gateway_routes.get_shared_client", return_value=upstream
        ):
            with TestClient(app, base_url="http://test") as client:
                main.rate_limiter = limiter
                main.auth_middleware = AuthenticationMiddleware("test-secret")
                app.state.redis_client = None
                try:
                    yield client, upstream.captured, limiter
                finally:
                    main.rate_limiter = saved_limiter
                    main.auth_middleware = saved_auth
    finally:
        main.rate_limiter = saved_limiter
        main.auth_middleware = saved_auth


def _bearer(sub="user-42"):
    from jose import jwt

    return jwt.encode(
        {"sub": sub, "exp": int(time.time()) + 600}, "test-secret", algorithm="HS256"
    )


# -- gateway's own service routes -----------------------------------------


def test_gateway_health_probe_reports_healthy():
    with gateway(_upstream()) as (client, _cap, _lim):
        resp = client.get("/gateway/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy", "service": "api-gateway"}


def test_gateway_ready_reports_ready_when_redis_answers():
    import app.main as main

    stub = MagicMock()
    stub.ping = AsyncMock(return_value=True)
    with gateway(_upstream(), app=main.app) as (client, _cap, _lim):
        main.app.state.redis_client = stub
        resp = client.get("/gateway/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "service": "api-gateway", "checks": {"redis": "ok"}}


def test_gateway_ready_returns_503_when_no_redis_client_is_attached():
    with gateway(_upstream()) as (client, _cap, _lim):
        resp = client.get("/gateway/ready")
    assert resp.status_code == 503
    assert resp.json()["detail"]["checks"] == {"redis": "down"}


def test_gateway_ready_returns_503_when_redis_ping_raises():
    import app.main as main

    stub = MagicMock()
    stub.ping = AsyncMock(side_effect=ConnectionError("refused"))
    with gateway(_upstream(), app=main.app) as (client, _cap, _lim):
        main.app.state.redis_client = stub
        resp = client.get("/gateway/ready")
    assert resp.status_code == 503
    assert resp.json()["detail"]["checks"] == {"redis": "down"}


async def test_gateway_ready_reports_timeout_when_redis_never_answers():
    """A hung Redis must fail readiness on a bounded timer, not hang it."""
    import app.main as main

    class HangingRedis:
        async def ping(self):
            await asyncio.sleep(30)

    with gateway(_upstream(), app=main.app) as (client, _cap, _lim):
        main.app.state.redis_client = HangingRedis()
        resp = client.get("/gateway/ready")
    assert resp.status_code == 503
    assert resp.json()["detail"]["checks"] == {"redis": "timeout"}


def test_list_services_enumerates_the_every_registered_service():
    from app.middleware import ServiceRegistry

    with gateway(_upstream()) as (client, _cap, _lim):
        resp = client.get("/gateway/services")

    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == len(ServiceRegistry.SERVICES)
    assert body["services"] == list(ServiceRegistry.SERVICES.keys())
    for expected in ("auth", "users", "content", "streaming", "uploads"):
        assert expected in body["services"]


# -- routing ---------------------------------------------------------------


def test_proxy_returns_404_for_a_service_missing_from_the_registry():
    with gateway(_upstream()) as (client, cap, _lim):
        resp = client.get("/not-a-service/api/v1/things")

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error"] == "Service not found"
    assert isinstance(detail["request_id"], str) and detail["request_id"]
    assert "url" not in cap, "no upstream call may be made for an unknown service"


def test_proxy_forwards_the_query_string_verbatim():
    up = _upstream([b"[]"])
    with gateway(up) as (client, cap, _lim):
        resp = client.get("/content/api/v1/titles?page=2&sort=release_year")

    assert resp.status_code == 200
    assert cap["url"] == "http://content-service:8000/api/v1/titles?page=2&sort=release_year"


def test_proxy_routes_a_bare_service_name_to_the_service_root():
    up = _upstream([b"{}"])
    with gateway(up) as (client, cap, _lim):
        resp = client.get("/uploads")

    assert resp.status_code == 200
    assert cap["url"] == "http://uploads-service:8000/"


def test_proxy_replaces_host_and_drops_content_length_from_forwarded_headers():
    up = _upstream([b"{}"])
    with gateway(up) as (client, cap, _lim):
        client.post(
            "/content/api/v1/titles",
            content=b'{"a":1}',
            headers={"content-type": "application/json", "x-trace": "keep-me"},
        )

    assert "content-length" not in cap["headers"]
    assert cap["headers"]["host"] == "test"
    assert cap["headers"]["x-trace"] == "keep-me"
    assert cap["body"] == b'{"a":1}'


# -- response passthrough --------------------------------------------------


def test_proxy_passes_upstream_status_and_body_through_untouched():
    up = _upstream([b'{"detail":"nope"}'], status_code=418)
    with gateway(up) as (client, _cap, _lim):
        resp = client.get("/content/api/v1/teapot")

    assert resp.status_code == 418
    assert resp.content == b'{"detail":"nope"}'


def test_proxy_preserves_duplicate_set_cookie_headers():
    up = _upstream(
        [b"ok"],
        headers=[
            ("set-cookie", "session=abc; Path=/"),
            ("set-cookie", "csrf=def; Path=/"),
            ("content-type", "application/json"),
        ],
    )
    with gateway(up) as (client, _cap, _lim):
        resp = client.get("/auth/api/v1/session")

    assert resp.status_code == 200
    # Both cookies must reach the client; a dict-based copy would collapse them.
    assert resp.headers.get_list("set-cookie") == [
        "session=abc; Path=/",
        "csrf=def; Path=/",
    ]


def test_proxy_drops_headers_named_by_the_upstream_connection_header():
    """RFC 9110: a header listed in Connection is hop-by-hop for this hop too."""
    up = _upstream(
        [b"hello"],
        headers=[
            ("content-type", "text/plain"),
            ("connection", "x-drop-me, keep-alive"),
            ("x-drop-me", "should-not-survive"),
            ("keep-alive", "timeout=5"),
            ("transfer-encoding", "chunked"),
            ("x-keep-me", "should-survive"),
        ],
    )
    with gateway(up) as (client, _cap, _lim):
        resp = client.get("/content/api/v1/raw")

    assert resp.status_code == 200
    assert "x-drop-me" not in resp.headers
    assert "keep-alive" not in resp.headers
    assert "transfer-encoding" not in resp.headers
    assert resp.headers["x-keep-me"] == "should-survive"


def test_proxy_recomputes_content_length_from_the_buffered_body():
    up = _upstream([b"0123456789"], headers=[("content-length", "999")])
    with gateway(up) as (client, _cap, _lim):
        resp = client.get("/content/api/v1/raw")

    assert resp.headers["content-length"] == "10"


def test_proxy_closes_the_upstream_response_before_returning():
    up = _upstream([b"ok"])
    with gateway(up) as (client, cap, _lim):
        client.get("/content/api/v1/raw")
    assert cap["closed"] is True


# -- failure mapping -------------------------------------------------------


def test_proxy_rejects_an_oversized_upstream_body_with_502(monkeypatch):
    monkeypatch.setattr(settings, "MAX_RESPONSE_BODY_SIZE", 10)
    up = _upstream([b"a" * 6, b"b" * 6, b"c" * 6])
    with gateway(up) as (client, _cap, _lim):
        resp = client.get("/content/api/v1/huge")

    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "Response body too large"
    assert up.captured["closed"] is True, "the upstream response must still be released"


def test_proxy_maps_an_upstream_timeout_to_504():
    up = _upstream(error=httpx.ReadTimeout("upstream too slow"))
    with gateway(up) as (client, _cap, _lim):
        resp = client.get("/content/api/v1/slow")

    assert resp.status_code == 504
    assert resp.json()["detail"]["error"] == "Service timeout"


def test_proxy_maps_a_transport_failure_to_502_bad_gateway():
    up = _upstream(error=httpx.ConnectError("connection refused"))
    with gateway(up) as (client, _cap, _lim):
        resp = client.get("/content/api/v1/down")

    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "Bad gateway"


def test_proxy_maps_a_streaming_failure_to_502_bad_gateway():
    """A non-HTTP error raised by the client itself becomes 502, not 500."""
    up = _upstream(error=RuntimeError("client.stream blew up"))
    with gateway(up) as (client, _cap, _lim):
        resp = client.get("/content/api/v1/x")

    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "Bad gateway"


def test_missing_shared_client_surfaces_as_a_502_bad_gateway():
    """``get_shared_client()`` is called *inside* proxy_request's try block.

    A gateway whose lifespan never ran therefore no longer leaks the generic
    500 handler output: the RuntimeError is swallowed by the generic
    ``except Exception`` arm and the caller gets the gateway's own 502 /
    ``Bad gateway`` envelope, which is both a specific non-2xx status and
    something a client can act on. Pinned so moving the call back out of the
    try block fails loudly instead of silently reintroducing an opaque 500.
    """
    import app.main as main
    from app.middleware import AuthenticationMiddleware

    saved_limiter = main.rate_limiter
    saved_auth = main.auth_middleware
    try:
        with patch(
            "app.api.gateway_routes.get_shared_client",
            side_effect=RuntimeError("Shared AsyncClient not initialized"),
        ):
            with TestClient(
                main.app, base_url="http://test", raise_server_exceptions=False
            ) as client:
                limiter = MagicMock()
                limiter.check_rate_limit = AsyncMock(return_value=True)
                # proxy_request awaits acquire_rate_limits() before touching the
                # shared client, so it must be an awaitable AsyncMock here too.
                limiter.acquire_rate_limits = AsyncMock(return_value=(True, None))
                limiter.release_rate_limits = AsyncMock(return_value=None)
                main.rate_limiter = limiter
                main.auth_middleware = AuthenticationMiddleware("test-secret")
                main.app.state.redis_client = None
                resp = client.get("/content/api/v1/x")
    finally:
        main.rate_limiter = saved_limiter
        main.auth_middleware = saved_auth

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["error"] == "Bad gateway"
    assert isinstance(detail["request_id"], str) and detail["request_id"]
    # No partial response leaks: the body is the error envelope and nothing
    # else, and the internal exception text never reaches the client.
    assert set(resp.json()) == {"detail"}
    assert b"Shared AsyncClient" not in resp.content
    assert b"Traceback" not in resp.content


# -- rate-limit keying -----------------------------------------------------


def test_proxy_keys_the_rate_limit_on_the_authenticated_subject():
    """The JWT ``sub`` is the account dimension of the in-flight lease.

    ``acquire_rate_limits(real_ip, service, path, *, account_id, device_id)``
    is the entry point; the account id arrives as a keyword argument.
    """
    up = _upstream([b"{}"])
    with gateway(up) as (client, _cap, limiter):
        client.get("/content/api/v1/titles", headers={"authorization": f"Bearer {_bearer('sub-7')}"})

    assert limiter.acquire_rate_limits.call_args.kwargs["account_id"] == "sub-7"


def test_proxy_omits_account_id_for_anonymous_requests():
    """No bearer token means no account dimension to key a lease on."""
    up = _upstream([b"{}"])
    with gateway(up) as (client, _cap, limiter):
        client.get("/content/api/v1/titles")

    assert limiter.acquire_rate_limits.call_args.kwargs["account_id"] is None


def test_proxy_takes_the_leftmost_forwarded_for_entry_as_the_client_ip():
    """A spoofable XFF chain must not widen the caller's rate-limit key.

    The client IP is the *first positional* argument of acquire_rate_limits.
    """
    up = _upstream([b"{}"])
    with gateway(up) as (client, _cap, limiter):
        client.get(
            "/content/api/v1/titles",
            headers={"x-forwarded-for": "203.0.113.9, 70.41.3.18, 150.172.238.178"},
        )

    assert limiter.acquire_rate_limits.call_args.args[0] == "203.0.113.9"


def test_proxy_uses_the_socket_peer_when_no_forwarding_headers_are_present():
    """With no forwarding headers the socket peer is the rate-limit key."""
    up = _upstream([b"{}"])
    with gateway(up) as (client, _cap, limiter):
        client.get("/content/api/v1/titles")

    call = limiter.acquire_rate_limits.call_args
    assert call.args[0] == "testclient"
    assert call.kwargs["device_id"] is None


def test_proxy_forwards_the_device_id_dimension():
    """X-Device-Id reaches the limiter so per-device caps can be enforced."""
    up = _upstream([b"{}"])
    with gateway(up) as (client, _cap, limiter):
        client.get(
            "/content/api/v1/titles", headers={"x-device-id": "device-abc"}
        )

    assert limiter.acquire_rate_limits.call_args.kwargs["device_id"] == "device-abc"


def test_proxy_uses_the_first_path_segment_as_the_rate_limit_service_name():
    """The lease is keyed on ``(service, remaining path)``, not the raw path.

    service is the 2nd positional argument and the remainder the 3rd, so a
    per-service bucket and per-path overrides (e.g. /reindex) both resolve.
    """
    up = _upstream([b"{}"])
    with gateway(up) as (client, _cap, limiter):
        client.get("/search/api/v1/titles")

    args = limiter.acquire_rate_limits.call_args.args
    assert args[1] == "search"
    assert args[2] == "/api/v1/titles"


def test_proxy_returns_429_when_the_rate_limiter_denies():
    up = _upstream([b"{}"])
    with gateway(up, limiter_result=False) as (client, _cap, _lim):
        resp = client.get("/content/api/v1/titles")

    assert resp.status_code == 429
    assert resp.json()["detail"]["error"] == "Rate limit exceeded"
    assert "url" not in up.captured, "a denied request must never reach upstream"


# -- method handling -------------------------------------------------------


def test_proxy_sends_no_body_for_delete_requests():
    up = _upstream([b"{}"])
    with gateway(up) as (client, cap, _lim):
        resp = client.delete("/content/api/v1/titles/1")

    assert resp.status_code == 200
    assert cap["method"] == "DELETE"
    assert cap["content"] is None


def test_proxy_streams_a_patch_body_to_upstream():
    up = _upstream([b"{}"])
    with gateway(up) as (client, cap, _lim):
        client.patch(
            "/content/api/v1/titles/1",
            content=b'{"name":"new"}',
            headers={"content-type": "application/json"},
        )

    assert cap["method"] == "PATCH"
    assert cap["body"] == b'{"name":"new"}'


# ---------------------------------------------------------------------------
# BodyLimitMiddleware: request-header guards
# ---------------------------------------------------------------------------


def _limiter(**overrides):
    """A BodyLimitMiddleware with predictable, small limits."""
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 1024
    middleware.max_multipart_body = 2048
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    middleware.max_decompression_ratio = 10
    for name, value in overrides.items():
        setattr(middleware, name, value)
    return middleware


async def test_too_many_request_headers_is_rejected_with_431():
    middleware = _limiter(max_header_count=2)
    req = _make_request(
        headers=[("a", "1"), ("b", "2"), ("c", "3"), ("d", "4")],
    )
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 431
    assert b"Too many headers: 4 > 2" in resp.body


async def test_an_oversized_header_field_is_rejected_with_431():
    middleware = _limiter(max_header_field_size=8)
    req = _make_request(headers=[("x-long", "a-very-long-header-value")])
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 431
    assert b"Header field too large (max 8)" in resp.body


async def test_the_aggregate_header_budget_is_enforced_with_431():
    middleware = _limiter(max_header_total_size=20)
    req = _make_request(
        headers=[("x-a", "1234567890"), ("x-b", "1234567890"), ("x-c", "1234567890")]
    )
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 431
    assert b"Total header size too large (max 20)" in resp.body


async def test_the_request_id_is_echoed_on_a_header_limit_rejection():
    middleware = _limiter(max_header_count=1)
    req = _make_request(headers=[("a", "1"), ("x-request-id", "req-123"), ("b", "2")])
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 431
    assert resp.headers["X-Request-ID"] == "req-123"


@pytest.mark.parametrize(
    "duplicated",
    [b"authorization", b"cookie", b"x-user-id", b"x-forwarded-proto"],
)
async def test_duplicate_security_sensitive_headers_are_rejected_with_400(duplicated):
    """#520: ambiguous duplicates must be refused, not comma-merged by h11."""
    middleware = _limiter()
    name = duplicated.decode()
    req = _make_request(
        headers=[(name, "first"), (name, "second"), ("accept", "*/*")],
    )
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 400
    assert b"Duplicate header not allowed: " + duplicated in resp.body


async def test_repeated_non_sensitive_headers_are_allowed():
    middleware = _limiter()
    req = _make_request(headers=[("accept", "a"), ("accept", "b")])
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# BodyLimitMiddleware: content-encoding / content-length parsing
# ---------------------------------------------------------------------------


async def test_a_non_numeric_content_length_is_ignored():
    middleware = _limiter(max_request_body=10)
    req = _make_request(
        headers=[("content-type", "application/json"), ("content-length", "not-a-number")],
        body_chunks=[b"hello"],
    )
    resp = await middleware.dispatch(req, _call_next_ok)
    # The bogus length is discarded, so enforcement falls back to the
    # stream-counters rather than rejecting on a parse error.
    assert resp.status_code == 200
    assert b"hello" in resp.body


def _spy_call_next():
    """Return ``(call_next, reached)``; ``reached`` records any invocation.

    Used by the content-encoding rejection tests to prove the request is
    refused *before* the body is ever handed downstream.
    """
    reached = []

    async def call_next(request):
        reached.append(request)
        return await _call_next_ok(request)

    return call_next, reached


@pytest.mark.parametrize("content_encoding", ["identity, gzip", "identity, unknown-thing"])
async def test_a_stacked_content_encoding_is_rejected_with_415(content_encoding):
    """Double-decompression smuggling defence: stacked encodings are refused.

    The gateway decompresses exactly once. Accepting ``identity, gzip`` and
    then relaying the original content-encoding upstream would let the backend
    decode a *second* time, so any layering is a 415 instead of a guess.
    """
    import gzip

    middleware = _limiter(max_request_body=1024)
    payload = gzip.compress(b'{"stacked": true}')
    call_next, reached = _spy_call_next()
    req = _make_request(
        headers=[
            ("content-type", "application/json"),
            ("content-encoding", content_encoding),
            ("content-length", str(len(payload))),
        ],
        body_chunks=[payload],
    )
    resp = await middleware.dispatch(req, call_next)

    assert resp.status_code == 415
    assert b"Stacked content encodings are not supported" in resp.body
    assert reached == [], "a rejected encoding must never be forwarded downstream"
    assert b"stacked" not in resp.body, "the request body must not leak back"


@pytest.mark.parametrize("content_encoding", ["br", "unknown-thing", "compress"])
async def test_an_unrecognised_content_encoding_is_rejected_with_415(content_encoding):
    """A strict allowlist keeps the gateway the only decoder.

    Anything outside identity/gzip/deflate used to be forwarded with its
    content-encoding intact, handing the decode decision to the backend. It is
    now refused with 415 before the body is read.
    """
    middleware = _limiter()
    call_next, reached = _spy_call_next()
    req = _make_request(
        headers=[
            ("content-type", "application/json"),
            ("content-encoding", content_encoding),
        ],
        body_chunks=[b'{"plain": true}'],
    )
    resp = await middleware.dispatch(req, call_next)

    assert resp.status_code == 415
    assert b"Content encoding is not supported" in resp.body
    assert reached == [], "an unsupported encoding must never be forwarded downstream"
    assert b"plain" not in resp.body, "the request body must not leak back"


async def test_uploads_paths_get_the_doubled_multipart_budget():
    """``/uploads`` is served from the multipart limit, not the JSON one."""
    middleware = _limiter(max_request_body=10, max_multipart_body=40)
    req = _make_request(
        path="/uploads/api/v1/objects",
        headers=[("content-type", "application/json"), ("content-length", "30")],
        body_chunks=[b"x" * 30],
    )
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 200
    assert b"x" * 30 in resp.body


async def test_a_non_uploads_path_still_hits_the_tighter_json_budget():
    middleware = _limiter(max_request_body=10, max_multipart_body=40)
    req = _make_request(
        path="/content/api/v1/items",
        headers=[("content-type", "application/json"), ("content-length", "30")],
        body_chunks=[b"x" * 30],
    )
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# BodyLimitMiddleware: streaming body accounting
# ---------------------------------------------------------------------------


async def test_empty_chunks_are_skipped_without_counting():
    middleware = _limiter(max_request_body=5)
    req = _make_request(
        headers=[("content-type", "application/json")],
        body_chunks=[b"", b"ab", b"", b"cd", b""],
    )
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 200
    assert b"ok:abcd" in resp.body


async def test_an_unrelated_value_error_from_the_handler_is_propagated():
    """Only budget/integrity failures become 413; a handler bug must surface."""
    middleware = _limiter()

    async def _call_next(request):
        raise ValueError("unrelated handler bug")

    req = _make_request(headers=[("content-type", "application/json")])
    with pytest.raises(ValueError, match="unrelated handler bug"):
        await middleware.dispatch(req, _call_next)


# ---------------------------------------------------------------------------
# BodyLimitMiddleware: response side
# ---------------------------------------------------------------------------


async def test_authorized_responses_are_marked_private_and_no_store():
    """Authenticated payloads must not sit in a shared cache."""
    middleware = _limiter()
    req = _make_request(
        method="GET",
        headers=[("authorization", "Bearer token")],
        body_chunks=[],
    )
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.headers["Cache-Control"] == "private, no-store"


async def test_anonymous_responses_are_not_marked_private():
    middleware = _limiter()
    req = _make_request(method="GET", headers=[], body_chunks=[])
    resp = await middleware.dispatch(req, _call_next_ok)
    assert "cache-control" not in {k.lower() for k in resp.headers}


async def test_an_immutable_response_header_map_does_not_break_the_request():
    """A frozen/odd header mapping must not turn a good request into a 500."""
    middleware = _limiter()

    class FrozenHeaders(dict):
        def __setitem__(self, key, value):
            raise RuntimeError("immutable mapping")

    class OddResponse:
        status_code = 200
        headers = FrozenHeaders()
        body = b"ok"

    async def _call_next(request):
        return OddResponse()

    req = _make_request(
        method="GET", headers=[("authorization", "Bearer t")], body_chunks=[]
    )
    resp = await middleware.dispatch(req, _call_next)
    assert resp.status_code == 200
    assert resp.body == b"ok"


async def test_an_oversized_buffered_response_is_rejected_with_502():
    middleware = _limiter(max_response_body=10)
    req = _make_request(method="GET", headers=[], body_chunks=[])

    async def _call_next(request):
        return Response(content=b"y" * 64)

    resp = await middleware.dispatch(req, _call_next)
    assert resp.status_code == 502
    assert b"Response body too large: 64 > 10" in resp.body


async def test_a_response_within_the_cap_is_passed_through_untouched():
    middleware = _limiter(max_response_body=1024)
    original = Response(content=b"small", headers={"X-Upstream": "yes"})

    async def _call_next(request):
        return original

    req = _make_request(method="GET", headers=[], body_chunks=[])
    resp = await middleware.dispatch(req, _call_next)
    assert resp is original
    assert resp.headers["X-Upstream"] == "yes"


async def test_rewrapped_streaming_response_normalises_text_and_buffer_chunks():
    """str chunks are encoded and other buffer types are coerced to bytes."""
    middleware = _limiter(max_response_body=1024)

    async def gen():
        yield "text-part"
        yield bytearray(b"buffer-part")

    streaming = StreamingResponse(content=gen())

    async def _call_next(request):
        return streaming

    req = _make_request(method="GET", headers=[], body_chunks=[])
    resp = await middleware.dispatch(req, _call_next)
    assert isinstance(resp, StreamingResponse)

    body = b"".join([chunk async for chunk in resp.body_iterator])
    assert body == b"text-partbuffer-part"


async def test_rewrapped_streaming_response_drops_hop_by_hop_headers():
    middleware = _limiter(max_response_body=1024)

    async def gen():
        yield b"payload"

    streaming = StreamingResponse(content=gen())
    streaming.raw_headers = streaming.raw_headers + [
        (b"x-duplicate", b"one"),
        (b"x-duplicate", b"two"),
    ]

    async def _call_next(request):
        return streaming

    req = _make_request(method="GET", headers=[], body_chunks=[])
    resp = await middleware.dispatch(req, _call_next)

    names = [name.decode().lower() for name, _ in resp.raw_headers]
    assert "transfer-encoding" not in names
    assert "content-length" not in names
    assert names.count("x-duplicate") == 2, "duplicate fields must survive"
    assert resp.status_code == 200
