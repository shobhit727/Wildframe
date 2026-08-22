"""API Gateway - routing, load balancing, authentication, request hardening."""

import logging
from contextlib import asynccontextmanager
from types import MappingProxyType

import httpx
import jwt
import redis.asyncio as redis
from fastapi import HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
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

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
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
    """Rate limiting middleware with per-endpoint limits.

    Limits are configured via settings (RATE_LIMIT_* per minute).
    Key = user sub (if authenticated) or client IP.
    Fail-open on Redis errors (#214).
    """

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        from app.core.settings import settings

        # Map service/endpoint -> limit per minute
        self.limits = {
            "auth": settings.RATE_LIMIT_AUTH,
            "search": settings.RATE_LIMIT_SEARCH,
            "uploads": settings.RATE_LIMIT_UPLOAD_CREATE,  # create session
            "reindex": settings.RATE_LIMIT_REINDEX,
            "default": settings.RATE_LIMIT_DEFAULT,
        }
        # Special sub-limits for upload finalize (complete/abort)
        self._upload_finalize_limit = settings.RATE_LIMIT_UPLOAD_FINALIZE

    def _get_limit(self, service: str, path: str = "") -> int:
        """Get rate limit for a service, with sub-endpoint overrides."""
        # Upload finalize (complete/abort) has stricter limit
        if service == "uploads" and (path.endswith("/complete") or path.endswith("/abort")):
            return self._upload_finalize_limit
        # Reindex is an expensive operation on search service
        if path.endswith("/reindex"):
            from app.core.settings import settings

            return self.limits.get("reindex", settings.RATE_LIMIT_REINDEX)
        return self.limits.get(service, self.limits["default"])

    async def check_rate_limit(self, user_id: str, service: str, path: str = "") -> bool:
        """Check if user has exceeded rate limit for service.

        Fail-open on Redis errors (unavailable, corrupt counter value): the
        rate limiter is anti-abuse protection, not an authorization
        decision, and a Redis outage or restart must not take the whole
        gateway down (#214). Windows are ephemeral by design.
        """
        key = f"rate_limit:{user_id}:{service}"
        limit = self._get_limit(service, path)

        try:
            count = int(await self.redis.incr(key))
            if count == 1:
                await self.redis.expire(key, 60)  # 1 minute window
        except Exception:  # noqa: BLE001 - fail open on Redis errors
            logger.warning("Rate limiter Redis error for %s; allowing request", service)
            return True

        return count <= limit


class HeaderSanitizerMiddleware(BaseHTTPMiddleware):
    """Strip/rewrite client-supplied headers at the edge (#314, #522, #625).

    - X-Forwarded-For: append client IP to trusted chain, drop client value
    - X-Real-IP: replace with client IP
    - X-User-*: drop entirely (identity headers must come from auth)
    - X-Correlation-ID / X-Request-ID: always regenerate server-side
    """

    async def dispatch(self, request: Request, call_next):
        # Get client IP
        client_ip = request.client.host if request.client else "unknown"

        # Build new headers dict with sanitized values
        new_headers = {}
        for key, value in request.headers.items():
            key_lower = key.lower()
            if key_lower in _STRIP_HEADERS:
                continue  # drop client-supplied value
            new_headers[key] = value

        # Rewrite X-Forwarded-For: append client IP to existing (trusted) chain
        # If no existing XFF, start new chain with client IP
        existing_xff = request.headers.get("x-forwarded-for")
        if existing_xff:
            new_headers["x-forwarded-for"] = f"{existing_xff}, {client_ip}"
        else:
            new_headers["x-forwarded-for"] = client_ip

        # Rewrite X-Real-IP to client IP
        new_headers["x-real-ip"] = client_ip

        # Create a new request with sanitized headers
        # We can't mutate request.headers directly (immutable), so we use scope
        scope = request.scope
        scope = dict(scope)
        scope["headers"] = [(k.lower().encode(), v.encode()) for k, v in new_headers.items()]

        # Use the modified scope for the rest of the request
        # Delete _headers to force re-parse (setting to None doesn't work due to hasattr check)
        if hasattr(request, "_headers"):
            del request._headers
        request.scope = scope

        response = await call_next(request)
        return response


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """Enforce request body and header limits (#231, #315, #417, #418, #419, #449, #517, #518, #629).

    - MAX_REQUEST_BODY_SIZE: reject 413 before buffering full body
    - MAX_HEADER_COUNT/FIELD/TOTAL: reject 431
    - Service-level caps: multipart (uploads) vs JSON (default)
    - Response body streaming with MAX_RESPONSE_BODY_SIZE cap (502/504)
    - Decompression bomb protection (MAX_DECOMPRESSION_RATIO)
    """

    def __init__(self, app):
        super().__init__(app)
        from app.core.settings import settings

        self.max_request_body = settings.MAX_REQUEST_BODY_SIZE
        self.max_response_body = settings.MAX_RESPONSE_BODY_SIZE
        self.max_header_count = settings.MAX_HEADER_COUNT
        self.max_header_field_size = settings.MAX_HEADER_FIELD_SIZE
        self.max_header_total_size = settings.MAX_HEADER_TOTAL_SIZE
        self.max_decompression_ratio = settings.MAX_DECOMPRESSION_RATIO

        # Uploads service uses multipart — larger cap
        self.max_multipart_body = settings.MAX_REQUEST_BODY_SIZE * 2  # 10MB for multipart

    async def dispatch(self, request: Request, call_next):
        # Check header limits early (#417, #418, #419)
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

        # Duplicate security-sensitive headers must be rejected, not merged
        # (#520): downstream layers may parse a different occurrence than the
        # one the gateway authorized. ASGI exposes duplicates in raw headers.
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

        # Determine body limit based on content type and service
        content_type = request.headers.get("content-type", "").lower()
        is_multipart = content_type.startswith("multipart/")
        service = request.url.path.strip("/").split("/")[0] if request.url.path.strip("/") else ""
        body_limit = (
            self.max_multipart_body
            if (is_multipart or service == "uploads")
            else self.max_request_body
        )

        # Read body in chunks, enforcing limit before full buffering (#231, #417)
        content_length = request.headers.get("content-length")
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
                pass  # Invalid content-length, let streaming handle it

        # Stream body with limit enforcement
        chunks = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > body_limit:
                return Response(
                    content=f"Request body exceeds limit: {total} > {body_limit}",
                    status_code=413,
                    headers={"X-Request-ID": request.headers.get("x-request-id", "")},
                )
            chunks.append(chunk)

        # Decompression bomb check for compressed content
        if request.headers.get("content-encoding", "").lower() in ("gzip", "deflate", "br"):
            # Heuristic: if compressed size * ratio > limit, likely bomb
            compressed_size = total
            if compressed_size > 0 and compressed_size * self.max_decompression_ratio > body_limit:
                return Response(
                    content=(
                        f"Potential decompression bomb: {compressed_size} * "
                        f"{self.max_decompression_ratio} > {body_limit}"
                    ),
                    status_code=413,
                    headers={"X-Request-ID": request.headers.get("x-request-id", "")},
                )

        # Reconstruct request with buffered body
        body = b"".join(chunks)
        scope = dict(request.scope)
        scope["body"] = body
        request.scope = scope
        request._body = body

        # Process request
        response = await call_next(request)

        # Authenticated responses are personalizable — never shared-cacheable
        # (#526): stamp private/no-store so CDNs and browsers cannot retain
        # one user's data for another.
        try:
            if request.headers.get("authorization"):
                response.headers["Cache-Control"] = "private, no-store"
        except Exception:  # noqa: BLE001 - header stamping must never break proxying
            pass

        # Stream response body with limit enforcement (#449, #517, #518, #629)
        if isinstance(response, StreamingResponse):
            return await self._stream_with_limit(response)
        elif hasattr(response, "body") and response.body is not None:
            body_len = len(response.body)
            if body_len > self.max_response_body:
                return Response(
                    content=f"Response body too large: {body_len} > {self.max_response_body}",
                    status_code=502,
                    headers={"X-Request-ID": request.headers.get("x-request-id", "")},
                )

        return response

    async def _stream_with_limit(self, response: StreamingResponse) -> Response:
        """Stream response body with size limit, return 502 if exceeded."""
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.body_iterator:
            if isinstance(chunk, bytes):
                chunk_bytes = chunk
            elif isinstance(chunk, str):
                chunk_bytes = chunk.encode()
            else:
                chunk_bytes = bytes(chunk)
            total += len(chunk_bytes)
            if total > self.max_response_body:
                return Response(
                    content=f"Response body exceeds limit: {total} > {self.max_response_body}",
                    status_code=502,
                    headers={"X-Request-ID": response.headers.get("x-request-id", "")},
                )
            chunks.append(chunk_bytes)

        body = b"".join(chunks)
        return Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )


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
            "/docs",
            "/openapi.json",
        }
    )

    def __init__(self, jwt_secret: str):
        self.jwt_secret = jwt_secret

    async def verify_token(self, request: Request) -> dict | None:
        """Verify a JWT token from the Authorization header."""
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return None

        try:
            scheme, token = auth_header.split()
            if scheme.lower() != "bearer":
                return None

            # Require an expiration claim so tokens without an expiry cannot
            # become effectively permanent bearer credentials.
            payload = jwt.decode(
                token,
                self.jwt_secret,
                algorithms=["HS256"],
                options={"require": ["exp"]},
            )
            return payload
        except Exception:  # noqa: BLE001
            logger.warning("Token verification failed", exc_info=True)
            return None

    async def __call__(self, request: Request) -> dict | None:
        """Middleware to check authentication on protected routes."""
        # Match public routes exactly or as a child path. Using startswith(path)
        # alone would accidentally make paths such as /auth/login-anything public.
        request_path = request.url.path.rstrip("/") or "/"
        if any(
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
