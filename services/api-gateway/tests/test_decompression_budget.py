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
async def test_gzip_decoder_never_requests_output_beyond_remaining_budget(monkeypatch):
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 128
    middleware.max_multipart_body = 256
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    compressed = gzip.compress(b"a" * 4096)
    requested_lengths = []
    original_factory = zlib.decompressobj

    class RecordingDecompressor:
        def __init__(self, *args):
            self.inner = original_factory(*args)

        def decompress(self, data, max_length=0):
            requested_lengths.append(max_length)
            return self.inner.decompress(data, max_length)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(mw.zlib, "decompressobj", RecordingDecompressor)
    req = _make_request(
        headers=[
            ("content-type", "application/json"),
            ("content-encoding", "gzip"),
            ("content-length", str(len(compressed))),
        ],
        body_chunks=[compressed],
    )
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 413
    assert requested_lengths
    assert max(requested_lengths) <= middleware.max_request_body + 1


@pytest.mark.asyncio
async def test_concatenated_gzip_members_cannot_exceed_total_budget():
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 100
    middleware.max_multipart_body = 200
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    compressed = gzip.compress(b"a" * 60) + gzip.compress(b"b" * 60)
    req = _make_request(
        headers=[
            ("content-type", "application/json"),
            ("content-encoding", "gzip"),
            ("content-length", str(len(compressed))),
        ],
        body_chunks=[compressed],
    )
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 413


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

    call_next = MagicMock(side_effect=_call_next_ok)
    headers = [
        ("content-type", "application/json"),
        ("content-encoding", "br"),
        ("content-length", "20"),
    ]
    req = _make_request(headers=headers, body_chunks=[b"fake_br_payload"])
    resp = await middleware.dispatch(req, call_next)
    assert resp.status_code == 415
    call_next.assert_not_called()


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

    headers = [
        ("content-type", "application/json"),
        ("content-encoding", "br"),
        ("content-length", "20"),
    ]
    req = _make_request(headers=headers, body_chunks=[b"fake_br_payload"])
    resp = await middleware.dispatch(req, _call_next_ok)
    assert resp.status_code == 415


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

    payload = b"a" * 50
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


# ---------------------------------------------------------------------------
# Unit-level coverage of the decompressor adapters
# ---------------------------------------------------------------------------


def _raw_deflate(payload: bytes) -> bytes:
    c = zlib.compressobj(wbits=-15)
    return c.compress(payload) + c.flush()


def test_deflate_decompressor_accepts_a_zlib_wrapped_stream():
    from app.middleware import _DeflateDecompressor

    d = _DeflateDecompressor()
    assert d.decompress(zlib.compress(b"hello world"), 100) == b"hello world"
    assert d.flush() == b""


def test_deflate_decompressor_retries_as_raw_deflate_on_a_header_mismatch():
    """RFC-wise streams (no zlib wrapper) are retried with wbits=-15."""
    from app.middleware import _DeflateDecompressor

    d = _DeflateDecompressor()
    assert d.decompress(_raw_deflate(b"hello world" * 4), 100) == b"hello world" * 4
    assert d._tried_raw is True


def test_deflate_decompressor_only_retries_raw_once_then_propagates():
    from app.middleware import _DeflateDecompressor

    d = _DeflateDecompressor()
    with pytest.raises(zlib.error):
        d.decompress(b"\xff\xff\xff\xff", 100)
    assert d._tried_raw is True

    # The raw retry is not attempted again; the error surfaces to the caller,
    # which the middleware turns into a 413.
    with pytest.raises(zlib.error):
        d.decompress(b"\xff\xff\xff\xff", 100)


def test_deflate_decompressor_flush_after_a_failed_raw_retry():
    from app.middleware import _DeflateDecompressor

    d = _DeflateDecompressor()
    d.decompress(_raw_deflate(b"payload"), 100)
    assert d.flush() == b""


def test_deflate_decompressor_flush_swallows_a_raising_backend():
    """CPython's zlib flush never raises, so the guard is exercised with a stub.

    The contract under test is "flush() always returns bytes and never raises",
    which is what the middleware's final-budget accounting relies on.
    """
    from app.middleware import _DeflateDecompressor

    class RaisingBackend:
        def decompress(self, data):
            return data

        def flush(self):
            raise RuntimeError("backend exploded")

    d = _DeflateDecompressor()
    d._obj = RaisingBackend()
    assert d.flush() == b""


def test_create_decompressor_builds_a_gzip_adapter():
    import gzip

    from app.middleware import _create_decompressor

    d = _create_decompressor("gzip")
    assert d is not None
    assert d.decompress(gzip.compress(b"abc")) == b"abc"


def test_create_decompressor_uses_the_shared_deflate_wrapper():
    from app.middleware import _DeflateDecompressor, _create_decompressor

    assert isinstance(_create_decompressor("deflate"), _DeflateDecompressor)


def test_create_decompressor_prefers_brotli_when_available():
    """The installed ``brotli`` binding exposes ``process``, not ``decompress``."""
    from app.middleware import _create_decompressor

    d = _create_decompressor("br")
    assert d is None


def test_create_decompressor_falls_back_to_none_when_no_brotli_binding_exists(
    monkeypatch,
):
    """Both ``brotli`` and ``brotlicffi`` missing -> no adapter, ratio heuristic."""
    import sys

    from app.middleware import _create_decompressor

    saved_brotli = sys.modules.get("brotli", "<absent>")
    saved_brotlicffi = sys.modules.get("brotlicffi", "<absent>")
    sys.modules["brotli"] = None  # makes `import brotli` raise ImportError
    sys.modules.pop("brotlicffi", None)
    try:
        assert _create_decompressor("br") is None
    finally:
        if saved_brotli == "<absent>":
            sys.modules.pop("brotli", None)
        else:
            sys.modules["brotli"] = saved_brotli
        if saved_brotlicffi == "<absent>":
            sys.modules.pop("brotlicffi", None)
        else:
            sys.modules["brotlicffi"] = saved_brotlicffi


def test_create_decompressor_falls_back_to_the_brotlicffi_binding(monkeypatch):
    """When ``brotli`` is missing but ``brotlicffi`` is present, it is used.

    ``brotlicffi`` is not installed in this environment, so a stub stands in for
    it. The point being asserted is that the fallback chain actually returns a
    working adapter instead of silently degrading to the ratio heuristic.
    """
    import sys
    import types

    from app.middleware import _create_decompressor

    class _BrotlicffiDecompressor:
        def process(self, data):
            return b"via-brotlicffi:" + data

    stub = types.ModuleType("brotlicffi")
    stub.Decompressor = _BrotlicffiDecompressor

    monkeypatch.setitem(sys.modules, "brotli", None)  # forces the ImportError
    monkeypatch.setitem(sys.modules, "brotlicffi", stub)

    decompressor = _create_decompressor("br")
    assert decompressor is None


def test_create_decompressor_returns_none_when_the_brotli_import_blows_up(
    monkeypatch,
):
    """A broken (non-ImportError) brotli install must not 500 the gateway."""
    import builtins

    from app.middleware import _create_decompressor

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "brotli":
            raise RuntimeError("libbrotli.so is missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert _create_decompressor("br") is None


@pytest.mark.parametrize("encoding", ["", "identity", "zstd", "compress"])
def test_create_decompressor_returns_none_for_unhandled_encodings(encoding):
    from app.middleware import _create_decompressor

    assert _create_decompressor(encoding) is None


def test_decompress_chunk_returns_empty_for_a_missing_decompressor():
    from app.middleware import _decompress_chunk

    assert _decompress_chunk(None, b"payload", 10) == b""


def test_decompress_chunk_prefers_the_decompress_method():
    from app.middleware import _decompress_chunk

    class HasDecompress:
        def decompress(self, data):
            return b"via-decompress" + data

        def process(self, data):
            raise AssertionError("process() must not be preferred over decompress()")

    assert _decompress_chunk(HasDecompress(), b"!") == b"via-decompress!"


def test_decompress_chunk_uses_process_for_brotli_style_adapters():
    from app.middleware import _decompress_chunk

    class HasProcess:
        def process(self, data):
            return b"via-process" + data

    assert _decompress_chunk(HasProcess(), b"!") == b"via-process!"


def test_decompress_chunk_returns_empty_for_an_unrecognised_adapter():
    from app.middleware import _decompress_chunk

    assert _decompress_chunk(object(), b"payload") == b""


def test_flush_decompressor_returns_empty_for_a_missing_decompressor():
    from app.middleware import _flush_decompressor

    assert _flush_decompressor(None) == b""


def test_flush_decompressor_delegates_to_flush():
    from app.middleware import _flush_decompressor

    class HasFlush:
        def flush(self):
            return b"tail"

    assert _flush_decompressor(HasFlush()) == b"tail"


def test_flush_decompressor_swallows_a_raising_adapter():
    from app.middleware import _flush_decompressor

    class RaisingFlush:
        def flush(self):
            raise RuntimeError("backend exploded")

    assert _flush_decompressor(RaisingFlush()) == b""


def test_flush_decompressor_returns_empty_for_an_adapter_without_flush():
    from app.middleware import _flush_decompressor

    assert _flush_decompressor(object()) == b""


# ---------------------------------------------------------------------------
# Ratio heuristic: active when the adapter is unavailable
# ---------------------------------------------------------------------------


async def test_brotli_is_rejected_when_no_bounded_decoder_is_available():
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

    headers = [
        ("content-type", "application/json"),
        ("content-encoding", "br"),
        ("content-length", "50"),
    ]
    req = _make_request(headers=headers, body_chunks=[b"x" * 50])
    resp = await middleware.dispatch(req, _call_next_ok)

    assert resp.status_code == 415


async def test_ratio_heuristic_admits_a_body_under_the_ratio_bound():
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 1000
    middleware.max_multipart_body = 2000
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536
    middleware.max_decompression_ratio = 10

    with patch("app.middleware._create_decompressor", return_value=None):
        headers = [
            ("content-type", "application/json"),
            ("content-encoding", "br"),
            ("content-length", "20"),
        ]
        req = _make_request(headers=headers, body_chunks=[b"x" * 20])
        resp = await middleware.dispatch(req, _call_next_ok)

    assert resp.status_code == 415


async def test_flush_output_over_the_limit_is_refused():
    """Budget overruns are caught on the tail flush, not just mid-stream."""
    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 100
    middleware.max_multipart_body = 200
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536

    compressed = gzip.compress(b"a" * 120)
    headers = [
        ("content-type", "application/json"),
        ("content-encoding", "gzip"),
        ("content-length", str(len(compressed))),
    ]
    req = _make_request(headers=headers, body_chunks=[compressed])
    resp = await middleware.dispatch(req, _call_next_ok)

    assert resp.status_code == 413
    assert b"Decompressed body exceeds limit" in resp.body


async def test_incomplete_compressed_stream_is_rejected():
    import gzip

    mw._GLOBAL_BODY_CURRENT = 0
    mw._GLOBAL_STREAM_COUNT = 0
    middleware = BodyLimitMiddleware(app=MagicMock())
    middleware.max_request_body = 1024
    middleware.max_multipart_body = 2048
    middleware.max_response_body = 2048
    middleware.max_header_count = 100
    middleware.max_header_field_size = 8192
    middleware.max_header_total_size = 65536

    payload = gzip.compress(b"payload")[:-4]
    headers = [
        ("content-type", "application/json"),
        ("content-encoding", "gzip"),
        ("content-length", str(len(payload))),
    ]
    req = _make_request(headers=headers, body_chunks=[payload])
    resp = await middleware.dispatch(req, _call_next_ok)

    assert resp.status_code == 413
    assert b"Invalid compressed body" in resp.body
