"""Health/readiness behavior of every service through the TLS edge.

The audit asks whether health/readiness is trustworthy when dependencies are
unavailable; the minimum contract — every service answers /health with a JSON
status and its own /ready when present — is verified here for all 15 services
plus the gateway.
"""

from __future__ import annotations

import httpx
import pytest

from conftest import GATEWAY_URL, SERVICE_PORTS

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def direct_clients() -> dict[str, httpx.Client]:
    clients = {}
    for name, port in SERVICE_PORTS.items():
        clients[name] = httpx.Client(
            verify=False, timeout=10.0, base_url=f"https://localhost:{port}"
        )
    yield clients
    for c in clients.values():
        c.close()


def test_gateway_health(client: httpx.Client) -> None:
    for path in ("/health", "/gateway/health"):
        response = client.get(f"{GATEWAY_URL}{path}")
        assert response.status_code == 200


@pytest.mark.parametrize("service", sorted(SERVICE_PORTS))
def test_service_health(direct_clients: dict[str, httpx.Client], service: str) -> None:
    response = direct_clients[service].get("/health")
    assert response.status_code == 200, f"{service} /health failed: {response.text}"
    body = response.json()
    assert body.get("status") in {"healthy", "ok"}, f"{service} health body: {body}"


@pytest.mark.parametrize("service", sorted(SERVICE_PORTS))
def test_service_ready_when_defined(
    direct_clients: dict[str, httpx.Client], service: str
) -> None:
    response = direct_clients[service].get("/ready")
    # /ready is optional per service; when present it must succeed.
    assert response.status_code in (200, 404), f"{service} /ready: {response.status_code}"
