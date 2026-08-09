"""API Gateway routes - proxy requests to backend services."""

import logging
from typing import Annotated

import httpx
from app.middleware import ServiceRegistry, get_optional_user
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

logger = logging.getLogger(__name__)
router = APIRouter()

# Headers that must not be forwarded upstream (host is re-set by httpx/ASGI
# servers; the client would otherwise get the gateway's own responses).
_PROXY_AGENT_HEADERS = frozenset({"host", "content-length"})


# Health and service-list routes must be registered BEFORE the catch-all
# proxy below, otherwise "{service:path}" swallows them.
@router.get("/gateway/health")
async def gateway_health():
    """API Gateway health check."""
    return {"status": "healthy", "service": "api-gateway", "timestamp": "2026-05-29T00:00:00Z"}


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
    # Route to service
    url, path = ServiceRegistry.route_request(f"/{service}")
    if not url:
        raise HTTPException(status_code=404, detail="Service not found")

    # Enforce per-client rate limits (user id when authenticated, IP otherwise).
    from app.main import rate_limiter  # late import: set in startup

    service_name = service.split("/")[0]
    client_key = (
        str(current_user.get("sub")) if current_user else (request.client.host or "unknown")
    )
    if rate_limiter and not await rate_limiter.check_rate_limit(client_key, service_name):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Forward request
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            original_host = request.headers.get("host", "")
            headers = {
                k: v for k, v in request.headers.items() if k.lower() not in _PROXY_AGENT_HEADERS
            }
            if original_host:
                headers["host"] = original_host
            forward_url = f"{url}{path}"
            if request.url.query:
                forward_url = f"{forward_url}?{request.url.query}"
            response = await client.request(
                method=request.method,
                url=forward_url,
                headers=headers,
                content=(
                    await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
                ),
            )
    except httpx.TimeoutException:
        logger.error(f"Timeout calling {url}{path}")
        raise HTTPException(status_code=504, detail="Service timeout")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error proxying request to {url}{path}: {e}")
        raise HTTPException(status_code=502, detail="Bad gateway")

    # Strip hop-by-hop headers that must not be relayed back to the client.
    payload_headers = {
        k: v
        for k, v in response.headers.items()
        if k.lower()
        not in {
            "transfer-encoding",
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "upgrade",
        }
    }

    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=payload_headers,
        media_type=response.headers.get("content-type"),
    )
