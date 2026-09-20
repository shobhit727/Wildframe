import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

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


@pytest.mark.asyncio
async def test_proxy_forwarding_uses_streaming():
    from app.main import app
    import app.main as main
    from fastapi.testclient import TestClient

    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    mock_client = MagicMock()
    captured = {}

    async def fake_request(method, url, headers, content=None):
        if content is not None:
            body = b""
            async for chunk in content:
                body += chunk
            captured["body"] = body
            captured["method"] = method
            captured["url"] = url
        else:
            captured["body"] = None
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"ok": true}'
        mock_resp.headers = {"content-type": "application/json"}
        return mock_resp

    mock_client.request = fake_request
    with patch("app.middleware.get_shared_client", return_value=mock_client):
        with patch("app.api.gateway_routes.get_shared_client", return_value=mock_client):
            with TestClient(app, base_url="http://test") as client:
                orig_limiter = main.rate_limiter
                mock_limiter = MagicMock()
                mock_limiter.check_rate_limit = AsyncMock(return_value=True)
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

    async def fake_request(method, url, headers, content=None):
        captured["content"] = content
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"[]"
        mock_resp.headers = {"content-type": "application/json"}
        return mock_resp

    mock_client.request = fake_request
    with patch("app.middleware.get_shared_client", return_value=mock_client):
        with patch("app.api.gateway_routes.get_shared_client", return_value=mock_client):
            with TestClient(app, base_url="http://test") as client:
                orig_limiter = main.rate_limiter
                mock_limiter = MagicMock()
                mock_limiter.check_rate_limit = AsyncMock(return_value=True)
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
