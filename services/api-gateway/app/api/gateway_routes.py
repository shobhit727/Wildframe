"""API Gateway routes - proxy requests to backend services."""
import logging

import httpx
from app.middleware import ServiceRegistry, get_current_user
from fastapi import APIRouter, Depends, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter()

@router.api_route("/{service:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_request(
    request: Request,
    service: str,
    current_user: dict = Depends(get_current_user)
):
    """Proxy request to appropriate backend service."""
    # Route to service
    url, path = ServiceRegistry.route_request(f"/{service}")
    if not url:
        raise HTTPException(status_code=404, detail="Service not found")
    
    # Forward request
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Build headers
            headers = dict(request.headers)
            headers.pop("host", None)
            
            # Forward request
            response = await client.request(
                method=request.method,
                url=f"{url}{path}",
                headers=headers,
                content=await request.body() if request.method in ["POST", "PUT", "PATCH"] else None
            )
            
            return {
                "status_code": response.status_code,
                "body": response.json() if response.headers.get("content-type") == "application/json" else response.text
            }
        except httpx.TimeoutException:
            logger.error(f"Timeout calling {url}{path}")
            raise HTTPException(status_code=504, detail="Service timeout")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error proxying request to {url}{path}: {e}")
            raise HTTPException(status_code=502, detail="Bad gateway")

@router.get("/gateway/health")
async def gateway_health():
    """API Gateway health check."""
    return {"status": "healthy", "service": "api-gateway", "timestamp": "2026-05-29T00:00:00Z"}

@router.get("/gateway/services")
async def list_services():
    """List available services."""
    return {
        "services": list(ServiceRegistry.SERVICES.keys()),
        "total": len(ServiceRegistry.SERVICES)
    }
