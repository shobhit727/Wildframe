import gzip
import zlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request
from fastapi.responses import Response

import app.middleware as mw
from app.middleware import BodyLimitMiddleware


def _make_request(method="POST", path="/content/api/v1/items", headers=None, body_chunks=None):
    headers = headers or []
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": raw_headers,
        "client": ("10.0.0.1", 12345),
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

    request.stream = stream  # type: ignore[method-assign]
    return request


async def _call_next_ok(request):
    body = b""
    async for chunk in request.stream():
        body += chunk
    return Response(content=b"ok:" + body)


@pytest.mark.asyncio
async def test_legit_gzip_within_limit():
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 1024
    middleware.max_multipart_body = 2048
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    payload = b'{"hello":"world"}'
    compressed = gzip.compress(payload)
    headers = [
        ("content-type", "application/json"),
        ("content-encoding", "gzip"),
        ("content-length", str(len(compressed))),
    ]
    req = _make_request(headers=headers, body_chunks=[compressed])
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_gzip_bomb_exceeds_limit():
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 100
    middleware.max_multipart_body = 200
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    bomb_plain = b"a" * 500
    compressed = gzip.compress(bomb_plain)
    headers = [
        ("content-type", "application/json"),
        ("content-encoding", "gzip"),
        ("content-length", str(len(compressed))),
    ]
    req = _make_request(headers=headers, body_chunks=[compressed])
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 413
    assert b"decompressed" in resp.body.lower() or b"bomb" in resp.body.lower()


@pytest.mark.asyncio
async def test_legit_deflate_within_limit():
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 1024
    middleware.max_multipart_body = 2048
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    payload = b'{"hello":"world"}'
    compressed = zlib.compress(payload)
    headers = [
        ("content-type", "application/json"),
        ("content-encoding", "deflate"),
        ("content-length", str(len(compressed))),
    ]
    req = _make_request(headers=headers, body_chunks=[compressed])
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_deflate_bomb_exceeds_limit():
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 100
    middleware.max_multipart_body = 200
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    bomb_plain = b"a" * 500
    compressed = zlib.compress(bomb_plain)
    headers = [
        ("content-type", "application/json"),
        ("content-encoding", "deflate"),
        ("content-length", str(len(compressed))),
    ]
    req = _make_request(headers=headers, body_chunks=[compressed])
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_brotli_bomb_mock():
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 100
    middleware.max_multipart_body = 200
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536

    class FakeBrotliDecompressor:
        def process(self, data):
            return b"a" * 500

        def flush(self):
            return b""

    with patch("app.middleware._create_decompressor", return_value=FakeBrotliDecompressor()):
        headers = [
            ("content-type", "application/json"),
            ("content-encoding", "br"),
            ("content-length", "20"),
        ]
        req = _make_request(headers=headers, body_chunks=[b"fake_br_payload"])
        resp = await middleware.dispatch(req, _call_next_ok)
        assert resp.status_code == 413


@pytest.mark.asyncio
async def test_brotli_legit_mock():
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 500
    middleware.max_multipart_body = 1000
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536

    class FakeBrotliDecompressor:
        def process(self, data):
            return b'{"ok": true}'

        def flush(self):
            return b""

    with patch("app.middleware._create_decompressor", return_value=FakeBrotliDecompressor()):
        headers = [
            ("content-type", "application/json"),
            ("content-encoding", "br"),
            ("content-length", "20"),
        ]
        req = _make_request(headers=headers, body_chunks=[b"fake_br_payload"])
        resp = await middleware.dispatch(req, _call_next_ok)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_streaming_incremental_budget():
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 100
    middleware.max_multipart_body = 200
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536

    class IncrementalDecompressor:
        def __init__(self):
            self.calls = 0

        def decompress(self, data):
            self.calls += 1
            if self.calls == 1:
                return b"a" * 60
            return b"a" * 60

        def flush(self):
            return b""

    with patch("app.middleware._create_decompressor", return_value=IncrementalDecompressor()):
        with patch("app.middleware._decompress_chunk", side_effect=lambda d, c: d.decompress(c)):
            with patch("app.middleware._flush_decompressor", side_effect=lambda d: d.flush()):
                headers = [
                    ("content-type", "application/json"),
                    ("content-encoding", "gzip"),
                    ("content-length", "20"),
                ]
                req = _make_request(headers=headers, body_chunks=[b"chunk1", b"chunk2"])
                resp = await middleware.dispatch(req, _call_next_ok)
                assert resp.status_code == 413


@pytest.mark.asyncio
async def test_heuristic_not_reject_legit_high_ratio_mock():
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

    class SmallDecompressor:
        def decompress(self, data):
            return b"a" * 50

        def flush(self):
            return b""

    with patch("app.middleware._create_decompressor", return_value=SmallDecompressor()):
        headers = [
            ("content-type", "application/json"),
            ("content-encoding", "gzip"),
            ("content-length", "20"),
        ]
        req = _make_request(headers=headers, body_chunks=[b"fake_payload_20_bytes__"])
        resp = await middleware.dispatch(req, _call_next_ok)
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_invalid_compressed_returns_413():
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 1024
    middleware.max_multipart_body = 2048
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    headers = [
        ("content-type", "application/json"),
        ("content-encoding", "gzip"),
        ("content-length", "20"),
    ]
    req = _make_request(headers=headers, body_chunks=[b"not a gzip payload at all"])
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 413
    assert (
        b"invalid" in resp.body.lower()
        or b"decompressed" in resp.body.lower()
        or b"bomb" in resp.body.lower()
    )
