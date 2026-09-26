"""API Gateway - routing, load balancing, authentication, request hardening."""

import asyncio
import ipaddress
import logging
import zlib
from contextlib import asynccontextmanager
from types import MappingProxyType

import httpx
import redis.asyncio as redis
from fastapi import HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from jose import JWTError
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Header names whose values must never appear in logs. Matched
# case-insensitively against any field attached to a LogRecord (covers both
# the formatted message and `extra={...}` payloads).
_REDACTED_HEADERS = frozenset({"authorization", "cookie", "set-cookie"})
_REDACTED_VALUE = "[REDACTED]"
_CONTROL_CHARS = (
    "".join(chr(c) for c in range(32) if c not in (9,)) + "\x7f"  # keep tab, drop the rest
)
_CONTROL_TRANSLATION = str.maketrans({c: "?" for c in _CONTROL_CHARS})

# Headers that must not be forwarded upstream (host is re-set by httpx/ASGI
# servers; the client would otherwise get the gateway's own responses).
_PROXY_AGENT_HEADERS = frozenset({"host", "content-length"})

# Hop-by-hop headers that are connection-scoped and must never be relayed when
# a downstream response is re-wrapped with a byte budget (#466). Mirrors
# _HOP_BY_HOP_HEADERS in app/api/gateway_routes.py (same RFC 9110 set, plus
# "trailer"). "content-length" is added at the call site: the re-wrapped body is
# counted while it streams, so any inherited length would be a lie.
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "transfer-encoding",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "trailers",
        "upgrade",
    }
)

# Headers whose duplicate occurrences would create auth/routing ambiguity
# (#520): they are rejected outright rather than comma-merged by h11.
_SECURITY_SENSITIVE_HEADERS = frozenset(
    {
        b"authorization",
        b"cookie",
        b"x-request-id",
        b"x-correlation-id",
        b"x-forwarded-for",
        b"x-forwarded-proto",
        b"x-user-id",
    }
)

# Client-supplied headers to strip/rewrite at the edge (#314, #522, #625).
# X-Forwarded-For / X-Real-IP are replaced with trusted values.
# X-User-* identity headers are dropped entirely.
# X-Correlation-ID / X-Request-ID are always regenerated server-side.
_STRIP_HEADERS = frozenset(
    {
        "x-forwarded-for",
        "x-real-ip",
        "x-user-id",
        "x-user-email",
        "x-user-roles",
        "x-correlation-id",
        "x-request-id",
    }
)


def _sanitize_message(message: str) -> str:
    """Escape log-injection vectors (CR, LF, NUL, control bytes)."""
    if not message:
        return message
    return message.replace("\r", "?").replace("\n", "?").translate(_CONTROL_TRANSLATION)


class HeaderRedactionFilter(logging.Filter):
    """Mask Authorization/Cookie/Set-Cookie values and sanitize messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if msg:
            record.msg = _sanitize_message(msg)
            record.args = ()
        for attr in list(vars(record)):
            if attr.startswith("_"):
                continue
            value = getattr(record, attr, None)
            if isinstance(value, str) and attr.lower() in _REDACTED_HEADERS:
                setattr(record, attr, _REDACTED_VALUE)
        return True


def install_header_redaction() -> None:
    """Attach HeaderRedactionFilter to every existing handler on the root logger.

    Idempotent — safe to call repeatedly (e.g. on app startup).
    """
    filt = HeaderRedactionFilter()
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, HeaderRedactionFilter) for f in handler.filters):
            handler.addFilter(filt)


# Module-level shared AsyncClient for upstream requests (#123).
# Initialized on startup, closed on shutdown. Limits honor settings.
_shared_client: httpx.AsyncClient | None = None

_GLOBAL_BODY_LOCK = asyncio.Lock()
_GLOBAL_BODY_CURRENT = 0
_GLOBAL_STREAM_COUNT = 0


async def _acquire_global_budget(reserve: int) -> bool:
    global _GLOBAL_BODY_CURRENT, _GLOBAL_STREAM_COUNT
    from app.core.settings import settings

    max_bytes = getattr(settings, "GATEWAY_GLOBAL_BODY_BUDGET_BYTES", 50 * 1024 * 1024)
    max_streams = getattr(settings, "GATEWAY_MAX_CONCURRENT_BODIES", 20)
    async with _GLOBAL_BODY_LOCK:
        if _GLOBAL_BODY_CURRENT + reserve > max_bytes:
            return False
        if _GLOBAL_STREAM_COUNT + 1 > max_streams:
            return False
        _GLOBAL_BODY_CURRENT += reserve
        _GLOBAL_STREAM_COUNT += 1
        return True


async def _release_global_budget(reserve: int) -> None:
    global _GLOBAL_BODY_CURRENT, _GLOBAL_STREAM_COUNT
    async with _GLOBAL_BODY_LOCK:
        _GLOBAL_BODY_CURRENT = max(0, _GLOBAL_BODY_CURRENT - reserve)
        _GLOBAL_STREAM_COUNT = max(0, _GLOBAL_STREAM_COUNT - 1)


@asynccontextmanager
async def shared_client_lifespan():
    """Lifespan context for the shared httpx.AsyncClient.

    Creates a single client with connection pooling limits derived from
    settings (UPSTREAM_MAX_CONNECTIONS, UPSTREAM_MAX_KEEPALIVE) and
    timeouts from settings (UPSTREAM_CONNECT_TIMEOUT, UPSTREAM_READ_TIMEOUT,
    UPSTREAM_WRITE_TIMEOUT, UPSTREAM_POOL_TIMEOUT).
    """
    global _shared_client
    from app.core.settings import settings

    limits = httpx.Limits(
        max_connections=settings.UPSTREAM_MAX_CONNECTIONS,
        max_keepalive_connections=settings.UPSTREAM_MAX_KEEPALIVE,
    )
    timeout = httpx.Timeout(
        connect=settings.UPSTREAM_CONNECT_TIMEOUT,
        read=settings.UPSTREAM_READ_TIMEOUT,
        write=settings.UPSTREAM_WRITE_TIMEOUT,
        pool=settings.UPSTREAM_POOL_TIMEOUT,
    )
    _shared_client = httpx.AsyncClient(limits=limits, timeout=timeout)
    logger.info(
        "Shared AsyncClient initialized: max_connections=%d, max_keepalive=%d",
        settings.UPSTREAM_MAX_CONNECTIONS,
        settings.UPSTREAM_MAX_KEEPALIVE,
    )
    try:
        yield
    finally:
        if _shared_client:
            await _shared_client.aclose()
            _shared_client = None
            logger.info("Shared AsyncClient closed")


def get_shared_client() -> httpx.AsyncClient:
    """Get the shared AsyncClient instance.

    Raises RuntimeError if called outside of lifespan (client not initialized).
    """
    if _shared_client is None:
        raise RuntimeError("Shared AsyncClient not initialized — call within lifespan")
    return _shared_client


class RateLimiter:
    """Independent IP and account limits with burst and concurrency.

    Dimensions:
    global: not enforced at gateway (downstream services handle global caps)
    tenant: single-tenant deployment, no tenant key
    user/account: per JWT sub when authenticated
    IP: per derived real IP always enforced
    device: per X-Device-Id header when present
    """

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        from app.core.settings import settings

        self.limits = {
            "auth": settings.RATE_LIMIT_AUTH,
            "search": settings.RATE_LIMIT_SEARCH,
            "uploads": settings.RATE_LIMIT_UPLOAD_CREATE,
            "reindex": settings.RATE_LIMIT_REINDEX,
            "default": settings.RATE_LIMIT_DEFAULT,
        }
        self._upload_finalize_limit = settings.RATE_LIMIT_UPLOAD_FINALIZE
        self.burst_limits = {
            "auth": settings.RATE_LIMIT_BURST_AUTH,
            "search": settings.RATE_LIMIT_BURST_SEARCH,
            "uploads": settings.RATE_LIMIT_BURST_UPLOAD_CREATE,
            "reindex": settings.RATE_LIMIT_BURST_REINDEX,
            "default": settings.RATE_LIMIT_BURST_DEFAULT,
        }
        self._burst_upload_finalize = settings.RATE_LIMIT_BURST_UPLOAD_FINALIZE
        self._burst_window = settings.RATE_LIMIT_BURST_WINDOW
        self.concurrency_limits = {
            "auth": settings.RATE_LIMIT_CONCURRENCY_AUTH,
            "search": settings.RATE_LIMIT_CONCURRENCY_SEARCH,
            "uploads": settings.RATE_LIMIT_CONCURRENCY_UPLOAD_CREATE,
            "reindex": settings.RATE_LIMIT_CONCURRENCY_REINDEX,
            "default": settings.RATE_LIMIT_CONCURRENCY_DEFAULT,
        }
        self._concurrency_upload_finalize = settings.RATE_LIMIT_CONCURRENCY_UPLOAD_FINALIZE
        self._concurrency_window = settings.RATE_LIMIT_CONCURRENCY_WINDOW
        self._window = 60

    def _get_limit(self, service: str, path: str = "") -> int:
        if service == "uploads" and path.endswith(("/complete", "/abort")):
            return self._upload_finalize_limit
        if path.endswith("/reindex"):
            from app.core.settings import settings

            return self.limits.get("reindex", settings.RATE_LIMIT_REINDEX)
        return self.limits.get(service, self.limits["default"])

    def _get_burst_limit(self, service: str, path: str = "") -> int | None:
        if service == "uploads" and path.endswith(("/complete", "/abort")):
            return self._burst_upload_finalize
        if path.endswith("/reindex"):
            return self.burst_limits.get("reindex")
        if service in ("search", "uploads", "auth"):
            return self.burst_limits.get(service)
        return None

    def _get_concurrency_limit(self, service: str, path: str = "") -> int | None:
        if service == "uploads" and path.endswith(("/complete", "/abort")):
            return self._concurrency_upload_finalize
        if path.endswith("/reindex"):
            return self.concurrency_limits.get("reindex")
        if service in ("search", "uploads", "auth"):
            return self.concurrency_limits.get(service)
        return None

    async def _check_key(self, key: str, limit: int, window: int, service: str) -> bool:
        try:
            count = int(await self.redis.incr(key))
            if count == 1:
                await self.redis.expire(key, window)
        except Exception:  # noqa: BLE001
            if service == "auth":
                logger.warning("Rate limiter Redis error for %s; denying request", service)
                return False
            logger.warning("Rate limiter Redis error for %s; allowing request", service)
            return True
        return count <= limit

    async def _check_dual(
        self,
        ip: str,
        service: str,
        path: str = "",
        account_id: str | None = None,
        device_id: str | None = None,
    ) -> bool:
        limit = self._get_limit(service, path)
        if not await self._check_key(f"rate_limit:ip:{ip}:{service}", limit, self._window, service):
            return False
        if account_id and not await self._check_key(
            f"rate_limit:account:{account_id}:{service}",
            limit,
            self._window,
            service,
        ):
            return False
        if device_id and not await self._check_key(
            f"rate_limit:device:{device_id}:{service}", limit, self._window, service
        ):
            return False
        burst_limit = self._get_burst_limit(service, path)
        if burst_limit is not None:
            if not await self._check_key(
                f"rate_limit:burst:ip:{ip}:{service}",
                burst_limit,
                self._burst_window,
                service,
            ):
                return False
            if account_id and not await self._check_key(
                f"rate_limit:burst:account:{account_id}:{service}",
                burst_limit,
                self._burst_window,
                service,
            ):
                return False
            if device_id and not await self._check_key(
                f"rate_limit:burst:device:{device_id}:{service}",
                burst_limit,
                self._burst_window,
                service,
            ):
                return False
        conc_limit = self._get_concurrency_limit(service, path)
        if conc_limit is not None:
            if not await self._check_key(
                f"rate_limit:concurrent:ip:{ip}:{service}",
                conc_limit,
                self._concurrency_window,
                service,
            ):
                return False
            if account_id and not await self._check_key(
                f"rate_limit:concurrent:account:{account_id}:{service}",
                conc_limit,
                self._concurrency_window,
                service,
            ):
                return False
            if device_id and not await self._check_key(
                f"rate_limit:concurrent:device:{device_id}:{service}",
                conc_limit,
                self._concurrency_window,
                service,
            ):
                return False
        return True

    async def check_rate_limits(
        self,
        ip: str,
        service: str,
        path: str = "",
        account_id: str | None = None,
        device_id: str | None = None,
    ) -> bool:
        return await self._check_dual(ip, service, path, account_id, device_id)

    async def check_rate_limit(self, *args, **kwargs) -> bool:
        ip = kwargs.pop("ip", None)
        account_id = kwargs.pop("account_id", None)
        device_id = kwargs.pop("device_id", None)
        user_id_kw = kwargs.pop("user_id", None)
        if account_id is None and user_id_kw is not None:
            account_id = user_id_kw
        service_kw = kwargs.pop("service", None)
        path_kw = kwargs.pop("path", "")

        if ip is not None or account_id is not None or device_id is not None:
            if service_kw is not None:
                service = service_kw
            elif len(args) >= 1:
                service = args[0]
            else:
                raise TypeError("service required")
            if path_kw != "" or "path" in kwargs:
                path = path_kw
            elif len(args) >= 2:
                path = args[1]
            else:
                path = ""
            if ip is None:
                ip = "unknown"
            return await self._check_dual(ip, service, path, account_id, device_id)
        user_id = None
        service = None
        path = ""
        if len(args) >= 1:
            user_id = args[0]
        if len(args) >= 2:
            service = args[1]
        if len(args) >= 3:
            path = args[2]
        if user_id_kw is not None:
            user_id = user_id_kw
        if service_kw is not None:
            service = service_kw
        elif "service" in kwargs:
            service = kwargs["service"]
        if "path" in kwargs:
            path = kwargs["path"]
        elif path_kw != "":
            path = path_kw
        if user_id is None or service is None:
            return True
        limit = self._get_limit(service, path)
        key = f"rate_limit:{user_id}:{service}"
        return await self._check_key(key, limit, self._window, service)


def _trusted_proxies() -> list[str]:
    from app.core.settings import settings

    raw = getattr(settings, "TRUSTED_PROXIES", "") or ""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    return []


def _is_trusted_ip(ip_str: str, trusted: list[str]) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for entry in trusted:
        entry = entry.strip()
        if not entry:
            continue
        if "/" in entry:
            try:
                net = ipaddress.ip_network(entry, strict=False)
                if ip in net:
                    return True
            except ValueError:
                continue
        else:
            try:
                if ip == ipaddress.ip_address(entry):
                    return True
            except ValueError:
                if ip_str == entry:
                    return True
    return False


def _derive_real_ip(socket_ip: str, xff_header: str | None) -> str:
    from app.core.settings import settings

    trust_proxy = bool(getattr(settings, "TRUST_PROXY", False))
    if not trust_proxy:
        return socket_ip
    trusted = _trusted_proxies()
    # Never trust forwarded headers unless the direct peer is explicitly in
    # the configured proxy trust set. An empty set is not an implicit wildcard.
    if not trusted or not _is_trusted_ip(socket_ip, trusted):
        return socket_ip
    if not xff_header:
        return socket_ip
    parts = [p.strip() for p in xff_header.split(",") if p.strip()]
    if not parts:
        return socket_ip
    for ip in reversed(parts):
        if not trusted:
            try:
                ipaddress.ip_address(ip)
                return ip
            except ValueError:
                continue
        else:
            if not _is_trusted_ip(ip, trusted):
                try:
                    ipaddress.ip_address(ip)
                    return ip
                except ValueError:
                    continue
    return socket_ip


class HeaderSanitizerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        socket_ip = request.client.host if request.client else "unknown"
        existing_xff = request.headers.get("x-forwarded-for")
        real_ip = _derive_real_ip(socket_ip, existing_xff)
        new_headers = {}
        for key, value in request.headers.items():
            if key.lower() in _STRIP_HEADERS:
                continue
            new_headers[key] = value
        new_headers["x-forwarded-for"] = real_ip
        new_headers["x-real-ip"] = real_ip
        scope = dict(request.scope)
        scope["headers"] = [(k.lower().encode(), v.encode()) for k, v in new_headers.items()]
        if hasattr(request, "_headers"):
            del request._headers
        request.scope = scope
        response = await call_next(request)
        return response


class _DeflateDecompressor:
    def __init__(self) -> None:
        self._obj = zlib.decompressobj()
        self._tried_raw = False

    def decompress(self, data: bytes) -> bytes:
        try:
            return self._obj.decompress(data)
        except zlib.error as exc:
            if not self._tried_raw and "incorrect header" in str(exc).lower():
                self._tried_raw = True
                self._obj = zlib.decompressobj(-15)
                return self._obj.decompress(data)
            raise

    def flush(self) -> bytes:
        try:
            return self._obj.flush()
        except Exception:
            return b""


def _create_decompressor(encoding: str):
    enc = encoding.strip().lower()
    if enc == "gzip":
        return zlib.decompressobj(31)
    if enc == "deflate":
        return _DeflateDecompressor()
    if enc == "br":
        try:
            import brotli

            return brotli.Decompressor()
        except ImportError:
            try:
                import brotlicffi as brotli  # type: ignore

                return brotli.Decompressor()
            except ImportError:
                return None
        except Exception:
            return None
    return None


def _decompress_chunk(decompressor, chunk: bytes) -> bytes:
    if decompressor is None:
        return b""
    if hasattr(decompressor, "decompress"):
        return decompressor.decompress(chunk)
    if hasattr(decompressor, "process"):
        return decompressor.process(chunk)
    return b""


def _flush_decompressor(decompressor) -> bytes:
    if decompressor is None:
        return b""
    if hasattr(decompressor, "flush"):
        try:
            return decompressor.flush()
        except Exception:
            return b""
    return b""


class BodyLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        from app.core.settings import settings

        self.max_request_body = settings.MAX_REQUEST_BODY_SIZE
        self.max_response_body = settings.MAX_RESPONSE_BODY_SIZE
        self.max_header_count = settings.MAX_HEADER_COUNT
        self.max_header_field_size = settings.MAX_HEADER_FIELD_SIZE
        self.max_header_total_size = settings.MAX_HEADER_TOTAL_SIZE
        self.max_decompression_ratio = settings.MAX_DECOMPRESSION_RATIO
        self.max_multipart_body = settings.MAX_REQUEST_BODY_SIZE * 2
        self.chunk_size = getattr(settings, "GATEWAY_BODY_STREAM_CHUNK_SIZE", 65536)

    async def dispatch(self, request: Request, call_next):
        header_count = len(request.headers)
        if header_count > self.max_header_count:
            return Response(
                content=f"Too many headers: {header_count} > {self.max_header_count}",
                status_code=431,
                headers={"X-Request-ID": request.headers.get("x-request-id", "")},
            )
        total_header_size = 0
        for k, v in request.headers.items():
            if len(k) > self.max_header_field_size or len(v) > self.max_header_field_size:
                return Response(
                    content=f"Header field too large (max {self.max_header_field_size})",
                    status_code=431,
                    headers={"X-Request-ID": request.headers.get("x-request-id", "")},
                )
            total_header_size += len(k) + len(v)
            if total_header_size > self.max_header_total_size:
                return Response(
                    content=f"Total header size too large (max {self.max_header_total_size})",
                    status_code=431,
                    headers={"X-Request-ID": request.headers.get("x-request-id", "")},
                )
        _seen: dict[bytes, int] = {}
        for raw_k, _ in request.headers.raw:
            lowered = raw_k.lower()
            if lowered in _SECURITY_SENSITIVE_HEADERS:
                _seen[lowered] = _seen.get(lowered, 0) + 1
                if _seen[lowered] > 1:
                    return Response(
                        content=f"Duplicate header not allowed: {lowered.decode('latin-1')}",
                        status_code=400,
                        headers={"X-Request-ID": request.headers.get("x-request-id", "")},
                    )
        content_type = request.headers.get("content-type", "").lower()
        is_multipart = content_type.startswith("multipart/")
        service = request.url.path.strip("/").split("/")[0] if request.url.path.strip("/") else ""
        body_limit = (
            self.max_multipart_body
            if (is_multipart or service == "uploads")
            else self.max_request_body
        )
        content_length = request.headers.get("content-length")
        cl: int | None = None
        if content_length:
            try:
                cl = int(content_length)
                if cl > body_limit:
                    return Response(
                        content=f"Request body too large: {cl} > {body_limit}",
                        status_code=413,
                        headers={"X-Request-ID": request.headers.get("x-request-id", "")},
                    )
            except ValueError:
                cl = None
        raw_enc = request.headers.get("content-encoding", "")
        enc = ""
        is_compressed = False
        if raw_enc:
            low = raw_enc.strip().lower()
            if "," in low:
                parts = [p.strip() for p in low.split(",") if p.strip()]
                for p in reversed(parts):
                    if p in ("gzip", "deflate", "br"):
                        enc = p
                        break
                else:
                    enc = low
            else:
                enc = low
            is_compressed = enc in ("gzip", "deflate", "br")
        method = request.method
        has_body = method in ("POST", "PUT", "PATCH", "DELETE") or cl is not None
        if (
            has_body
            and request.headers.get("content-length") is None
            and method in ("POST", "PUT", "PATCH")
        ):
            has_body = True
        reserve = 0
        acquired = False
        if has_body:
            reserve = cl if cl is not None else body_limit
            if reserve:
                acquired = await _acquire_global_budget(reserve)
                if not acquired:
                    return Response(
                        content="Global body budget exceeded",
                        status_code=429,
                        headers={"X-Request-ID": request.headers.get("x-request-id", "")},
                    )
        try:
            original_stream = request.stream
            limit = body_limit
            max_ratio = self.max_decompression_ratio
            decompressor = _create_decompressor(enc) if is_compressed else None
            decompressed_total = 0

            async def bounded_stream():
                nonlocal decompressed_total
                total = 0
                async for chunk in original_stream():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > limit:
                        raise ValueError(f"Request body exceeds limit: {total} > {limit}")
                    if is_compressed:
                        if decompressor is not None:
                            try:
                                out = _decompress_chunk(decompressor, chunk)
                            except Exception as exc:
                                raise ValueError(f"Invalid compressed body: {exc}") from exc
                            decompressed_total += len(out)
                            if decompressed_total > limit:
                                raise ValueError(
                                    f"Decompressed body exceeds limit: {decompressed_total} > {limit}"
                                )
                        else:
                            if total * max_ratio > limit:
                                raise ValueError(
                                    f"Potential decompression bomb: {total} * {max_ratio} > {limit}"
                                )
                    yield chunk
                if is_compressed and decompressor is not None:
                    try:
                        out = _flush_decompressor(decompressor)
                    except Exception as exc:
                        raise ValueError(f"Invalid compressed body: {exc}") from exc
                    if out:
                        decompressed_total += len(out)
                        if decompressed_total > limit:
                            raise ValueError(
                                f"Decompressed body exceeds limit: {decompressed_total} > {limit}"
                            )

            request.stream = bounded_stream  # type: ignore[method-assign]

            try:
                response = await call_next(request)
            except ValueError as exc:
                msg = str(exc)
                if (
                    "exceeds limit" in msg
                    or "decompression bomb" in msg
                    or "Decompressed body" in msg
                    or "Invalid compressed" in msg
                ):
                    return Response(
                        content=msg,
                        status_code=413,
                        headers={"X-Request-ID": request.headers.get("x-request-id", "")},
                    )
                raise
            try:
                if request.headers.get("authorization"):
                    response.headers["Cache-Control"] = "private, no-store"
            except Exception:  # noqa: BLE001, S110
                pass
            if isinstance(response, StreamingResponse):
                return self._stream_response_with_limit(response)
            if hasattr(response, "body") and response.body is not None:
                body_len = len(response.body)
                if body_len > self.max_response_body:
                    return Response(
                        content=f"Response body too large: {body_len} > {self.max_response_body}",
                        status_code=502,
                        headers={"X-Request-ID": request.headers.get("x-request-id", "")},
                    )
            # Passed through untouched, so the downstream response keeps its
            # exact raw headers (names, casing, duplicate fields) — rebuilding
            # it here is what used to collapse them.
            return response
        finally:
            if acquired and reserve:
                await _release_global_budget(reserve)

    def _stream_response_with_limit(self, response: StreamingResponse) -> StreamingResponse:
        max_body = self.max_response_body
        orig = response.body_iterator

        async def limited():
            total = 0
            async for chunk in orig:
                if isinstance(chunk, bytes):
                    chunk_bytes = chunk
                elif isinstance(chunk, str):
                    chunk_bytes = chunk.encode()
                else:
                    chunk_bytes = bytes(chunk)
                total += len(chunk_bytes)
                if total > max_body:
                    raise ValueError(f"Response body exceeds limit: {total} > {max_body}")
                yield chunk_bytes

        # Carry the original raw headers over verbatim so exact header names
        # and duplicate fields (e.g. repeated Set-Cookie) survive the re-wrap;
        # dict(response.headers) would collapse them. Hop-by-hop headers and
        # content-length are dropped: the body is re-counted as it streams and
        # the budget can abort it mid-flight, so any inherited length is a lie.
        excluded_headers = _HOP_BY_HOP_HEADERS | {"content-length"}
        limited_response = StreamingResponse(
            content=limited(),
            status_code=response.status_code,
            media_type=response.media_type,
            background=response.background,
        )
        limited_response.raw_headers = [
            (name, value)
            for name, value in response.raw_headers
            if name.decode("latin-1").lower() not in excluded_headers
        ]
        return limited_response


class ServiceRegistry:
    """Registry of backend services."""

    # Inside the Docker network each service is reachable on the port its
    # container actually listens on. The dev compose files map host
    # ports 8003/8004 to the content/streaming containers, but inside the
    # network every service's uvicorn binds 8000 (the Dockerfile CMD or the
    # compose command override with no --port flag).
    SERVICES: MappingProxyType[str, str] = MappingProxyType(
        {
            "auth": "http://auth-service:8000",
            "users": "http://user-service:8000",
            "content": "http://content-service:8000",
            "streaming": "http://streaming-service:8000",
            "search": "http://search-service:8000",
            "recommendations": "http://recommendation-service:8000",
            "billing": "http://billing-service:8000",
            "analytics": "http://analytics-service:8000",
            "notifications": "http://notification-service:8000",
            "media": "http://media-pipeline:8000",
            "admin": "http://admin-service:8000",
            "creators": "http://creators-service:8000",
            "moderation": "http://moderation-service:8000",
            "uploads": "http://uploads-service:8000",
        }
    )

    @classmethod
    def get_service_url(cls, service: str) -> str | None:
        """Get service URL by name."""
        return cls.SERVICES.get(service)

    @classmethod
    def route_request(cls, path: str) -> tuple[str | None, str]:
        """Route request path to appropriate service."""
        parts = path.strip("/").split("/")
        if not parts:
            return None, ""

        service = parts[0]
        remaining_path = "/" + "/".join(parts[1:]) if len(parts) > 1 else "/"
        url = cls.get_service_url(service)

        return url, remaining_path


class AuthenticationMiddleware:
    """Authentication middleware for API Gateway."""

    PUBLIC_PATHS: frozenset[str] = frozenset(
        {
            "/auth/register",
            "/auth/login",
            "/health",
            "/ready",
            "/gateway/health",
            "/gateway/ready",
        }
    )

    def __init__(self, jwks_url: str):
        self.jwks_url = jwks_url

    async def verify_token(self, request: Request) -> dict | None:
        """Verify a JWT token from the Authorization header."""
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return None

        try:
            scheme, token = auth_header.split()
            if scheme.lower() != "bearer":
                return None

            # Optional identity extraction only; upstream services enforce audience.
            # Expiry remains mandatory even at this transparent proxy boundary
            # (python-jose spells "exp is required" as require_exp, not a
            # `require` list).
            payload = jwt.decode(
                token,
                self.jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False, "require_exp": True},
            )
            return payload
        except (JWTError, ValueError):  # ValueError: malformed auth header
            logger.warning("Token verification failed", exc_info=True)
            return None

    async def __call__(self, request: Request) -> dict | None:
        """Middleware to check authentication on protected routes."""
        request_path = request.url.path.rstrip("/") or "/"
        from app.core.settings import settings

        is_docs_path = request_path in (
            "/docs",
            "/redoc",
            "/openapi.json",
        ) or request_path.startswith(("/docs/", "/redoc/", "/openapi.json/"))
        if is_docs_path and settings.ENVIRONMENT == "production":
            pass
        elif any(
            request_path == path or request_path.startswith(f"{path}/")
            for path in self.PUBLIC_PATHS
        ):
            return None

        # Verify token for protected routes.
        token_payload = await self.verify_token(request)
        if not token_payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing authentication token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return token_payload


class LoadBalancer:
    """Simple load balancer for service replicas."""

    async def get_healthy_instance(self, service: str) -> str | None:
        """Get healthy instance of a service."""
        url = ServiceRegistry.get_service_url(service)
        if not url:
            return None

        # Check health
        async with httpx.AsyncClient(timeout=2.0) as client:
            try:
                response = await client.get(f"{url}/health")
                if response.status_code == 200:
                    return url
            except Exception:  # noqa: BLE001
                logger.warning("Health check failed for %s", service)

        return None


async def get_current_user(request: Request) -> dict:
    """Dependency to get current authenticated user.

    Reads the bearer token from the Authorization header and validates it.
    Used by gateway routes that need to know the caller. For routes that
    should bypass auth (public paths) use a different dependency.
    """
    from .main import auth_middleware  # late import: set in startup

    assert auth_middleware is not None, "auth_middleware initialised on startup"

    token_payload = await auth_middleware.verify_token(request)
    if not token_payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    return token_payload


async def get_optional_user(request: Request) -> dict | None:
    """Optional auth dependency.

    Returns the verified token payload when a valid bearer token is present,
    otherwise None. Never raises - used by the transparent gateway proxy so
    public catalog reads work without a token while authenticated services
    upstream enforce their own auth.
    """
    from .main import auth_middleware  # late import: set in startup

    assert auth_middleware is not None, "auth_middleware initialised on startup"

    return await auth_middleware.verify_token(request)


async def age_middleware(request: Request, call_next):
    """Middleware that propagates age claims from JWT to upstream headers."""
    # In prod, decode JWT and inject X-Age-Verified, X-Is-Minor
    # For now, pass through and let age_gate enforce
    response = await call_next(request)
    # Add Vary to help caches
    response.headers["Vary"] = "X-Jurisdiction, X-Age-Verified"
    return response
