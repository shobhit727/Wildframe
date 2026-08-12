"""API Gateway - routing, load balancing, authentication."""

import logging
import posixpath
import random
from types import MappingProxyType

import httpx
import jwt
import redis.asyncio as redis
from fastapi import HTTPException, Request, status

from app.core.settings import settings



# Header names whose values must never appear in logs. Matched
# case-insensitively against any field attached to a LogRecord (covers both
# the formatted message and `extra={...}` payloads).
_REDACTED_HEADERS = frozenset({"authorization", "cookie", "set-cookie"})
_REDACTED_VALUE = "[REDACTED]"
_CONTROL_CHARS = (
    "".join(chr(c) for c in range(32) if c not in (9,)) + "\x7f"  # keep tab, drop the rest
)
logger = logging.getLogger(__name__)


_CONTROL_TRANSLATION: dict[str, str] = {}

def _sanitize_message(message: str) -> str:
    """Escape log-injection vectors (CR, LF, NUL, control bytes)."""
    if not message:
        return message
    return message.replace("\r", "?").replace("\n", "?").translate(_CONTROL_TRANSLATION)  # type: ignore[arg-type]


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
        }
    )

    @classmethod
    def get_service_url(cls, service: str) -> str | None:
        """Get service URL by name."""
        return cls.SERVICES.get(service)

    @classmethod
    def route_request(cls, path: str) -> tuple[str | None, str]:
        """Route request path to appropriate service.

        The path is normalized (backslashes become separators, duplicate
        slashes collapse, dot segments resolve) before the service segment is
        extracted so alternate encodings cannot route a request to a different
        service than the one the gateway classified.

        Raises ValueError when normalization moves the request outside the
        service named by the raw first path segment (e.g. "/content/../auth").
        """
        raw = path.strip("/")
        if not raw:
            return None, "/"
        if "\x00" in path:
            raise ValueError("NUL byte in request path")
        raw_first = raw.split("/", 1)[0]

        normalized = path.replace("\\", "/")
        normalized = posixpath.normpath(normalized)
        parts = normalized.strip("/").split("/")
        service = parts[0] if parts and parts[0] else ""
        if service != raw_first:
            raise ValueError("Request path escapes its service boundary")

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

class RateLimitUnavailable(Exception):
    """Raised when the rate-limit backend cannot be reached.

    The proxy maps this to HTTP 503: rate limiting FAILS CLOSED so a Redis
    outage can never silently disable abuse controls. The 503 is loud and
    bounded — the limiter is the only component touched, and the error is
    logged with the failure class for alerting.
    """


class RateLimiter:
    """Rate limiting middleware backed by Redis (shared across replicas)."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.limits = {
            "auth": 5,  # login/register: aggressive brute-force bound
            "search": 100,  # expensive search queries
            "media": 20,  # upload/finalize are expensive
            "recommendations": 200,
            "billing": 200,
            "admin": 300,
            "analytics": 200,
            "notifications": 100,
            "default": 1000,
        }

    def _key(self, user_id: str, service: str) -> str:
        """Namespaced counter key: environment + service + client identity.

        The environment prefix keeps staging/test and production counters from
        colliding on shared Redis infrastructure, and the service prefix keeps
        one service's counters from overwriting another's (issue #563).
        """
        return f"rate_limit:{settings.ENVIRONMENT}:{service}:{user_id}"

    async def check_rate_limit(self, user_id: str, service: str) -> bool:
        """Check if user has exceeded rate limit for service.

        Returns True when the request is within the limit. Raises
        RateLimitUnavailable when the backend cannot be reached (fail closed).
        """
        key = self._key(user_id, service)
        limit = self.limits.get(service, self.limits["default"])

        try:
            count = int(await self.redis.incr(key))
            if count == 1:
                # Jittered window (60-75s): counters across clients must not
                # expire in lockstep and synchronize a load spike.
                await self.redis.expire(key, 60 + random.randint(0, 15))
        except (redis.RedisError, OSError, ConnectionError) as exc:
            logger.error("Rate-limit backend failure (%s): %s", type(exc).__name__, exc)
            raise RateLimitUnavailable() from exc

        return count <= limit



class LoadBalancer:
    """Simple load balancer for service replicas."""

    async def get_healthy_instance(self, service: str) -> str | None:
        """Get healthy instance of a service."""
        url = ServiceRegistry.get_service_url(service)
        if not url:
            return None

        async with httpx.AsyncClient(timeout=2.0, follow_redirects=False) as client:

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
    otherwise None. Never raises — used by the transparent gateway proxy so
    public catalog reads work without a token while authenticated services
    upstream enforce their own auth.
    """
    from .main import auth_middleware  # late import: set in startup

    assert auth_middleware is not None, "auth_middleware initialised on startup"

    return await auth_middleware.verify_token(request)
