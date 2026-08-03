"""API Gateway - routing, load balancing, authentication."""

import logging
from types import MappingProxyType

import httpx
import jwt
import redis.asyncio as redis
from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)


class ServiceRegistry:
    """Registry of backend services."""

    # Inside the Docker network each service is reachable on the port its
    # container actually listens on. Most bind 8000; content binds 8003 and
    # streaming binds 8004 (their settings.SERVER_PORT). The hostnames are the
    # stable TLS/DNS names docker-compose assigns; keep them, but fix the ports
    # so the gateway stops sending proxied requests to the wrong port.
    SERVICES: MappingProxyType[str, str] = MappingProxyType(
        {
            "auth": "http://auth-service:8000",
            "users": "http://user-service:8000",
            "content": "http://content-service:8003",
            "streaming": "http://streaming-service:8004",
            "search": "http://search-service:8000",
            "recommendations": "http://recommendation-service:8000",
            "billing": "http://billing-service:8000",
            "analytics": "http://analytics-service:8000",
            "notifications": "http://notification-service:8000",
            "media": "http://media-pipeline:8000",
            "admin": "http://admin-service:8000",
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
            "/docs",
            "/openapi.json",
        }
    )

    def __init__(self, jwt_secret: str):
        self.jwt_secret = jwt_secret

    async def verify_token(self, request: Request) -> dict | None:
        """Verify JWT token from Authorization header."""
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return None

        try:
            scheme, token = auth_header.split()
            if scheme.lower() != "bearer":
                return None

            payload = jwt.decode(token, self.jwt_secret, algorithms=["HS256"])
            return payload
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Token verification failed: {e}")
            return None

    async def __call__(self, request: Request) -> dict | None:
        """Middleware to check authentication on protected routes."""
        # Check if path is public
        if any(request.url.path.startswith(path) for path in self.PUBLIC_PATHS):
            return None

        # Verify token for protected routes
        token_payload = await self.verify_token(request)
        if not token_payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing authentication token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return token_payload


class RateLimiter:
    """Rate limiting middleware."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.limits = {
            "auth": 5,  # 5 requests per minute
            "search": 100,  # 100 requests per minute
            "default": 1000,  # 1000 requests per minute
        }

    async def check_rate_limit(self, user_id: str, service: str) -> bool:
        """Check if user has exceeded rate limit for service."""
        key = f"rate_limit:{user_id}:{service}"
        limit = self.limits.get(service, self.limits["default"])

        count = await self.redis.incr(key)
        if count == 1:
            await self.redis.expire(key, 60)  # 1 minute window

        return count <= limit


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
                logger.warning(f"Health check failed for {service}")

        return None


async def get_current_user(request: Request) -> dict:
    """Dependency to get current authenticated user.

    Reads the bearer token from the Authorization header and validates it.
    Used by gateway routes that need to know the caller. For routes that
    should bypass auth (public paths) use a different dependency.
    """
    from .main import auth_middleware  # late import: set in startup

    token_payload = await auth_middleware.verify_token(request)
    if not token_payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    return token_payload
