"""API Gateway routes - proxy requests to backend services.

The proxy is the single public ingress for the platform, so it enforces:

- normalized routing (dot segments, encoded separators, duplicate slashes)
- a blocklist of upstream diagnostics paths (health/metrics/docs) that must
  never be reachable without the gateway's own authz layer
- request header/body hardening (size caps, duplicate-header rejection,
  Content-Length/Transfer-Encoding conflict detection, decompression limits)
- per-client rate limiting keyed on the authenticated subject or the socket
  peer (never on client-supplied forwarding headers), failing closed on
  backend outage
- outbound hardening: bounded connect/read/write timeouts, connection pool
  limits, no redirect following, response size caps, retries only for
  idempotent methods with bounded jittered backoff, trusted rewrite of
  forwarding/identity headers.
"""

import asyncio
import logging
import random
import uuid
import zlib
from typing import Annotated

import httpx
from app.core.settings import settings
from app.middleware import RateLimitUnavailable, ServiceRegistry, get_optional_user
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

logger = logging.getLogger(__name__)
router = APIRouter()

# Only these methods may be retried: retrying a POST/PATCH/DELETE can replay
# a non-idempotent mutation and duplicate side effects. Body-bearing methods
# are never replayed either, so large uploads cannot be double-sent.
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

# Headers never forwarded upstream:
# - hop-by-hop headers (host/content-length are re-set by httpx/ASGI)
# - client-supplied forwarding headers: rewritten from the trusted socket
#   peer instead, so clients cannot spoof their apparent IP (rate limiting
#   and upstream decisions must not trust attacker-controlled values)
# - service-to-service credentials and identity claims, which only the
#   gateway may inject from the verified token
# - x-correlation-id: regenerated at the edge (see SDK CorrelationMiddleware
#   trust-boundary note)
_STRIPPED_UPSTREAM_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-forwarded-port",
        "x-real-ip",
        "via",
        "x-api-key",
        "x-service-name",
        "x-service-token",
        "x-service-id",
        "x-internal-token",
        "x-user-sub",
        "x-user-id",
        "x-user-role",
        "x-user-email",
        "x-role",
        "x-authenticated-user",
        "x-auth-user",
        "x-email",
        "x-correlation-id",
    }
)

# Upstream endpoints that expose process/dependency diagnostics. They must
# not be reachable through the public proxy: the gateway has its own
# /health and /ready, and upstream /metrics would leak internals.
_FORBIDDEN_UPSTREAM_PATHS = frozenset(
    {
        "/health",
        "/ready",
        "/live",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/schema",
        "/debug",
        "/actuator",
    }
)

# Duplicate headers are rejected except for these benign multi-value ones
# (RFC 9110 allows repeating list-valued fields).
_ALLOWED_DUPLICATE_HEADERS = frozenset(
    {"accept", "accept-language", "cache-control", "x-request-id"}
)

# Hop-by-hop headers that must not be relayed back to the client.
_RESPONSE_STRIPPED_HEADERS = frozenset(
    {
        "transfer-encoding",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "upgrade",
    }
)


class UpstreamResponseTooLarge(Exception):
    """Raised when an upstream response exceeds MAX_RESPONSE_BODY_SIZE."""


def _peer_ip(request: Request) -> str:
    """Trusted client address: the actual socket peer, never a header."""
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _validate_request_headers(scope_headers: list[tuple[bytes, bytes]]) -> str | None:
    """Return a stable error message for an unacceptable header set, else None.

    Enforces per-header and total size caps, duplicate-header rejection and
    Content-Length/Transfer-Encoding conflict detection (request-smuggling
    vector). Operates on the raw ASGI scope header list because Starlette
    merges duplicates before they reach Request.headers.
    """
    total = 0
    counts: dict[str, int] = {}
    has_transfer_encoding = False
    has_content_length = False
    for raw_name, raw_value in scope_headers:
        name = raw_name.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        total += len(name) + len(value)
        if len(value) > settings.MAX_HEADER_FIELD_SIZE:
            return "Header field too large"
        counts[name] = counts.get(name, 0) + 1
        has_transfer_encoding = has_transfer_encoding or name == "transfer-encoding"
        has_content_length = has_content_length or name == "content-length"
    if total > settings.MAX_HEADER_TOTAL_SIZE:
        return "Request headers too large"
    if len(counts) > settings.MAX_HEADER_COUNT:
        return "Too many request headers"
    for name, count in counts.items():
        if count > 1 and name not in _ALLOWED_DUPLICATE_HEADERS:
            return f"Duplicate header: {name}"
    if has_transfer_encoding and has_content_length:
        return "Content-Length and Transfer-Encoding must not be combined"
    return None


def _validate_request_syntax(request: Request) -> None:
    """Enforce request syntax hardening; raise 400/431 on violations."""
    error = _validate_request_headers(request.scope.get("headers", []))
    if not error:
        return
    if error.startswith(("Header field", "Request headers", "Too many")):
        raise HTTPException(status_code=431, detail="Request header fields too large")
    raise HTTPException(status_code=400, detail=error)


def _check_decompressed_size(body: bytes, encoding: str) -> None:
    """Validate a compressed request body without fully buffering expansion.

    Raises 413 when the decompressed size or the compression ratio exceeds
    the configured caps (decompression-bomb protection) and 400 when the
    payload is not a valid gzip/deflate stream.
    """
    try:
        if encoding == "gzip":
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        else:  # deflate
            decompressor = zlib.decompressobj()
        decompressed = 0
        for i in range(0, len(body), 8192):
            chunk = decompressor.decompress(body[i : i + 8192])
            decompressed += len(chunk)
            if decompressed > settings.MAX_REQUEST_BODY_SIZE:
                raise HTTPException(status_code=413, detail="Request body too large")
            if decompressed > len(body) * settings.MAX_DECOMPRESSION_RATIO + 1024:
                raise HTTPException(
                    status_code=413, detail="Compressed request body expands too much"
                )
        decompressor.flush()
    except zlib.error as exc:
        raise HTTPException(status_code=400, detail="Invalid compressed request body") from exc


async def _read_request_body(request: Request) -> bytes:
    """Read the request body enforcing size and decompression caps."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared = int(content_length)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header")
        if declared > settings.MAX_REQUEST_BODY_SIZE:
            raise HTTPException(status_code=413, detail="Request body too large")

    body = await request.body()
    if len(body) > settings.MAX_REQUEST_BODY_SIZE:
        raise HTTPException(status_code=413, detail="Request body too large")

    encoding = request.headers.get("content-encoding", "").strip().lower()
    if encoding in ("gzip", "deflate"):
        _check_decompressed_size(body, encoding)
    elif encoding not in ("", "identity"):
        raise HTTPException(status_code=400, detail="Unsupported Content-Encoding")
    return body


async def _read_response_body(response: httpx.Response) -> bytes:
    """Stream the upstream response body, capping its total size."""
    chunks = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > settings.MAX_RESPONSE_BODY_SIZE:
            raise UpstreamResponseTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


def _retry_delay(attempt: int) -> float:
    """Bounded exponential backoff with full jitter (never exceeds cap)."""
    delay = settings.UPSTREAM_RETRY_BASE_DELAY * (2**attempt)
    return min(delay + random.random() * delay, settings.UPSTREAM_MAX_RETRY_DELAY)  # type: ignore[no-any-return]


async def _send_upstream(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict[str, str],
    content: bytes | None,
) -> tuple[int, httpx.Headers, bytes]:
    """Issue the upstream request with a bounded retry budget.

    Retries only idempotent methods and only on transport errors or 502/504
    responses; the total budget is UPSTREAM_MAX_RETRIES with bounded jittered
    backoff. Body-bearing requests are never replayed.
    """
    max_attempts = 1
    if method in _IDEMPOTENT_METHODS:
        max_attempts = settings.UPSTREAM_MAX_RETRIES + 1
    for attempt in range(max_attempts):
        try:
            async with client.stream(method, url, headers=headers, content=content) as response:
                if response.status_code in (502, 504) and attempt + 1 < max_attempts:
                    await asyncio.sleep(_retry_delay(attempt))
                    continue
                body = await _read_response_body(response)
                return response.status_code, response.headers, body
        except (httpx.TransportError, httpx.TimeoutException):
            if attempt + 1 >= max_attempts:
                raise
            await asyncio.sleep(_retry_delay(attempt))
    raise httpx.TransportError("upstream retry budget exhausted")  # pragma: no cover


# Health and service-list routes must be registered BEFORE the catch-all
# proxy below, otherwise "{service:path}" swallows them.
@router.get("/gateway/health")
async def gateway_health(request: Request):
    """Gateway liveness probe — see /ready for dependency verification."""
    return {"status": "healthy", "service": "api-gateway"}


@router.get("/gateway/ready")
async def gateway_ready(request: Request):
    """Gateway readiness probe — bounded Redis ping (max 2s).

    Returns 503 with a checks dict when Redis is unavailable; never exposes
    connection strings or credentials.
    """
    checks: dict[str, str] = {}
    overall = "ready"
    redis_client = request.app.state.redis_client
    if redis_client is None:
        checks["redis"] = "down"
        overall = "not_ready"
    else:
        try:
            await asyncio.wait_for(redis_client.ping(), timeout=2.0)
            checks["redis"] = "ok"
        except asyncio.TimeoutError:
            checks["redis"] = "timeout"
            overall = "not_ready"
        except Exception:  # noqa: BLE001
            checks["redis"] = "down"
            overall = "not_ready"
    payload = {
        "status": overall,
        "service": "api-gateway",
        "checks": checks,
    }
    if overall != "ready":
        raise HTTPException(
            status_code=503,
            detail=payload,
        )
    return payload


@router.get("/gateway/services")
async def list_services():
    """List available services."""
    return {
        "services": list(ServiceRegistry.SERVICES.keys()),
        "total": len(ServiceRegistry.SERVICES),
    }


@router.api_route("/{service:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_request(
    request: Request,
    service: str,
    current_user: Annotated[dict | None, Depends(get_optional_user)],
):
    """Proxy request to appropriate backend service.

    The proxy is transparent: the upstream status code, headers, and body are
    passed through unchanged so the frontend sees the real HTTP response.
    Authentication is delegated to the upstream services; the gateway only
    inspects (and does not require) the bearer token.
    """
    if getattr(request.app.state, "shutting_down", False):
        raise HTTPException(status_code=503, detail="Service shutting down")

    # Normalized routing: rejects dot-segment/encoded-separator escapes (400)
    # before any classification or forwarding happens.
    try:
        url, path = ServiceRegistry.route_request(f"/{service}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid request path")
    if not url:
        raise HTTPException(status_code=404, detail="Service not found")

    # Upstream diagnostics must not be reachable through the public proxy.
    if path.rstrip("/") in _FORBIDDEN_UPSTREAM_PATHS:
        raise HTTPException(status_code=404, detail="Not found")

    # Request syntax hardening (header caps, duplicates, CL/TE conflicts).
    _validate_request_syntax(request)

    # Enforce per-client rate limits (user sub when authenticated, socket
    # peer otherwise — never a client-supplied forwarding header). The
    # limiter fails closed: a Redis outage yields 503, not silent abuse.
    from app.main import rate_limiter  # late import: set in startup

    service_name = service.split("/")[0]
    client_key = str(current_user.get("sub")) if current_user else _peer_ip(request)
    if rate_limiter:
        try:
            allowed = await rate_limiter.check_rate_limit(client_key, service_name)
        except RateLimitUnavailable:
            raise HTTPException(status_code=503, detail="Rate limiter unavailable")
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": "60"},
            )

    # Read the body (bounded) before opening the upstream connection so an
    # oversized or decompression-bomb request never reaches a backend.
    body = await _read_request_body(request) if request.method in _BODY_METHODS else None

    # Build the upstream header set: strip client-controlled forwarding,
    # credential and identity headers, then inject trusted values.
    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _STRIPPED_UPSTREAM_HEADERS
    }
    headers["x-forwarded-for"] = _peer_ip(request)
    headers["x-request-id"] = request.headers.get("x-request-id") or uuid.uuid4().hex
    if current_user and current_user.get("sub"):
        headers["x-user-sub"] = str(current_user["sub"])

    forward_url = f"{url}{path}"
    if request.url.query:
        forward_url = f"{forward_url}?{request.url.query}"

    timeout = httpx.Timeout(
        connect=settings.UPSTREAM_CONNECT_TIMEOUT,
        read=settings.UPSTREAM_READ_TIMEOUT,
        write=settings.UPSTREAM_WRITE_TIMEOUT,
        pool=settings.UPSTREAM_POOL_TIMEOUT,
    )
    limits = httpx.Limits(
        max_connections=settings.UPSTREAM_MAX_CONNECTIONS,
        max_keepalive_connections=settings.UPSTREAM_MAX_KEEPALIVE,
    )

    # Outbound client: bounded timeouts, capped pool, no redirect following
    # (each hop is revalidated by the client instead), and no trust of proxy
    # environment variables so ambient HTTP_PROXY cannot redirect privileged
    # server-side requests.
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            status_code, upstream_headers, content = await _send_upstream(
                client, request.method, forward_url, headers, body
            )
    except UpstreamResponseTooLarge:
        logger.error(
            "Upstream response from %s exceeded %d bytes",
            url,
            settings.MAX_RESPONSE_BODY_SIZE,
        )
        raise HTTPException(status_code=502, detail="Upstream response too large")
    except httpx.TimeoutException:
        logger.error("Timeout calling %s%s", url, path)
        raise HTTPException(status_code=504, detail="Service timeout")
    except httpx.HTTPError as exc:
        logger.error("Error proxying request to %s%s: %s", url, path, exc)
        raise HTTPException(status_code=502, detail="Bad gateway")
    except Exception as exc:  # noqa: BLE001 - never leak upstream details
        logger.error("Unexpected error proxying request to %s%s: %s", url, path, exc)
        raise HTTPException(status_code=502, detail="Bad gateway")

    # Strip hop-by-hop headers that must not be relayed back to the client.
    payload_headers = {
        k: v for k, v in upstream_headers.items() if k.lower() not in _RESPONSE_STRIPPED_HEADERS
    }

    return Response(
        content=content,
        status_code=status_code,
        headers=payload_headers,
        media_type=upstream_headers.get("content-type"),
    )
