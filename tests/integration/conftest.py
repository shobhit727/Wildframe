"""Shared fixtures and helpers for the Wildframe cross-service integration suite.

This suite talks to the LIVE development stack (see
deployments/docker-compose.dev.yml) over HTTPS through the Caddy/gateway
boundary. It deliberately performs no service-level mocking: security-sensitive
behavior is verified against real processes, real JWTs and real databases.

Run explicitly (it is intentionally excluded from the per-service test loops):

    pytest tests/integration -q

The suite skips itself with a message when the stack is not reachable, so
running it on a machine without containers is harmless.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import uuid as uuidlib
from collections import deque
from typing import Any

import httpx
import pytest

GATEWAY_URL = os.environ.get("WILDFRAME_GATEWAY_URL", "https://localhost:8000")
JWT_SECRET = os.environ.get("WILDFRAME_JWT_SECRET", "dev-secret-key")
STRIPE_WEBHOOK_SECRET = os.environ.get(
    "WILDFRAME_STRIPE_WEBHOOK_SECRET", "whsec_default_change_me"
)

# Caddy proxies each service's host port with TLS (see AGENTS.md -> HTTPS/TLS).
SERVICE_PORTS: dict[str, int] = {
    "auth": 8001,
    "user": 8002,
    "content": 8003,
    "streaming": 8004,
    "search": 8005,
    "admin": 8006,
    "recommendation": 8007,
    "billing": 8008,
    "analytics": 8009,
    "notification": 8010,
    "media": 8011,
    "creators": 8012,
    "moderation": 8013,
    "uploads": 8014,
}

AUTH_SERVICE = f"{GATEWAY_URL}/auth/api/v1/auth"
CONTENT_SERVICE = f"{GATEWAY_URL}/content/api/v1"
STREAMING_SERVICE = f"{GATEWAY_URL}/streaming/api/v1"
ANALYTICS_SERVICE = f"{GATEWAY_URL}/analytics/api/v1"
MEDIA_SERVICE = f"{GATEWAY_URL}/media/api/v1"
CREATORS_SERVICE = f"{GATEWAY_URL}/creators/api/v1"
BILLING_SERVICE = f"https://localhost:{SERVICE_PORTS['billing']}/api/v1"

REGISTER_PASSWORD = "SecurePass123!"


# ---------------------------------------------------------------------------
# Stack availability gate.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def require_stack(client: httpx.Client) -> None:
    try:
        response = client.get(f"{GATEWAY_URL}/gateway/health", timeout=5.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"Wildframe docker stack not reachable at {GATEWAY_URL} ({exc!r}). "
                    "Start it with: docker compose -f deployments/docker-compose.dev.yml up -d")
    if response.status_code != 200:
        pytest.skip(f"Wildframe stack unreachable at {GATEWAY_URL}: HTTP {response.status_code}")


@pytest.fixture(scope="session")
def client() -> httpx.Client:
    # verify=False: the dev stack uses the repo's self-signed localhost certs.
    with httpx.Client(verify=False, timeout=30.0) as c:
        yield c


# ---------------------------------------------------------------------------
# Gateway rate-limit pacing.
#
# The api-gateway enforces 5 req/min on the auth service, keyed by user `sub`
# or (pre-auth) by client IP. Every test that calls the gateway without a
# token shares the localhost IP bucket, so IP-keyed calls are paced to keep
# at most 4 in any 60s window. Tests that deliberately exceed the limit are
# required to run last in their module (see test_gateway_auth.py).
# ---------------------------------------------------------------------------

_pace_lock = threading.Lock()
_ip_keyed_window: deque[float] = deque()


def _pace_ip_keyed() -> None:
    """Block until the 60s window holds fewer than 3 IP-keyed calls.

    The gateway allows 5 auth-service requests per minute per key; keeping the
    paced window at <=3 leaves headroom for any unpaced IP-keyed request
    (e.g. a logout whose token the gateway can no longer decode) so the suite
    never trips the 429 boundary by accident.
    """
    while True:
        with _pace_lock:
            now = time.monotonic()
            while _ip_keyed_window and now - _ip_keyed_window[0] >= 60:
                _ip_keyed_window.popleft()
            if len(_ip_keyed_window) < 3:
                _ip_keyed_window.append(now)
                return
        time.sleep(2)


# ---------------------------------------------------------------------------
# JWT helpers (stdlib-only; no service packages are imported).
# ---------------------------------------------------------------------------


def decode_jwt(token: str) -> dict[str, Any]:
    """Decode (without verifying) a JWT's payload."""
    _, payload, _ = token.split(".")
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def mint_jwt(claims: dict[str, Any], secret: str = JWT_SECRET) -> str:
    """Sign a JWT with HS256 using the dev secret (for negative tests only)."""
    header = {"alg": "HS256", "typ": "JWT"}

    def _b64(obj: dict) -> bytes:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    message = _b64(header) + b"." + _b64(claims)
    signature = hmac.new(secret.encode(), message, hashlib.sha256).digest()
    return (message + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()


def mint_access_token(user_id: str | uuidlib.UUID, *, exp_delta: int = 900, **extra: Any) -> str:
    """Mint a realistic access token for the dev secret."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "user_id": str(user_id),
        "email": f"it-{user_id}@wildframe-test.example",
        "role": "user",
        "type": "access",
        "iss": "wildframe-auth",
        "aud": "wildframe-api",
        "iat": now,
        "exp": now + exp_delta,
    }
    claims.update(extra)
    return mint_jwt(claims)


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def unique_email() -> str:
    return f"it-{uuidlib.uuid4().hex[:16]}@wildframe-test.example"


def register_user(client: httpx.Client, email: str | None = None) -> dict[str, Any]:
    """Register a fresh user through the gateway and return credentials."""
    email = email or unique_email()
    _pace_ip_keyed()
    response = client.post(
        f"{AUTH_SERVICE}/register",
        json={
            "email": email,
            "password": REGISTER_PASSWORD,
            "first_name": "Integration",
            "last_name": "Tester",
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    return {
        "email": email,
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "token_type": data["token_type"],
        "expires_in": data["expires_in"],
        "user_id": decode_jwt(data["access_token"])["sub"],
    }


def ip_keyed(client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """Issue a gateway request that counts against the shared localhost IP
    bucket (no valid token key), pacing it under the 5 req/min auth limit."""
    _pace_ip_keyed()
    return getattr(client, method)(url, **kwargs)


def fetch_content_id(client: httpx.Client, token: str) -> str | None:
    """Return the id of any catalogued content item, or None if the catalog is empty."""
    response = client.get(
        f"{CONTENT_SERVICE}/content", params={"page": 1, "page_size": 1},
        headers=auth_headers(token),
    )
    if response.status_code != 200:
        return None
    items = response.json()
    return items[0]["id"] if items else None


def stripe_signature(payload: bytes, secret: str = STRIPE_WEBHOOK_SECRET) -> str:
    """Build a valid Stripe webhook signature header for the dev secret."""
    timestamp = int(time.time())
    message = f"{timestamp}.{payload.decode()}".encode()
    v1 = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={v1}"


def stripe_event(
    event_id: str, event_type: str, object_payload: dict[str, Any]
) -> bytes:
    event = {
        "id": event_id,
        "object": "event",
        "api_version": "2024-06-20",
        "created": int(time.time()),
        "livemode": False,
        "pending_webhooks": 0,
        "type": event_type,
        "data": {"object": object_payload},
    }
    return json.dumps(event).encode()
