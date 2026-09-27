"""Billing httpx integration."""

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.stripe_client import StripeClient
from app.main import create_app


@pytest.mark.asyncio
async def test_billing_health():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200


def _access_token(*, role: str | None) -> tuple[str, UUID]:
    """Mint an access token the billing boundary will accept.

    Returns the token and its subject so callers address ``/user/{sub}`` paths
    that ``require_self`` will actually match.
    """
    from app.core.settings import settings

    user_id = uuid4()
    claims = {
        "sub": str(user_id),
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
    }
    if role is not None:
        claims["role"] = role
    token = jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, user_id


MILESTONE_REQUESTS = [
    (
        "post",
        "/api/v1/billing/milestones",
        {"creator_id": str(uuid4()), "project_title": "p", "total_commitment": "100.00"},
    ),
    ("post", "/api/v1/billing/milestones/{mid}/release", {"tranche_number": 1}),
    ("post", "/api/v1/billing/milestones/{mid}/kill", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", MILESTONE_REQUESTS)
async def test_milestone_endpoints_reject_anonymous_callers(method, path, body):
    """Milestone money movement must never be reachable unauthenticated (#786)."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await getattr(ac, method)(path.format(mid=uuid4()), json=body)
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", MILESTONE_REQUESTS)
async def test_milestone_endpoints_require_admin_role(method, path, body):
    """A plain authenticated user must not be able to move milestone money."""
    app = create_app()
    transport = ASGITransport(app=app)
    token, _ = _access_token(role=None)
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await getattr(ac, method)(path.format(mid=uuid4()), json=body, headers=headers)
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_paid_subscribe_requires_checkout_not_a_local_entitlement(monkeypatch):
    """POST /subscribe must not grant SVOD without a verified payment (#787).

    The entitlement is written by the checkout.session.completed webhook, so
    the route may only return a checkout URL.
    """
    created: dict = {}

    def _fake_checkout(**kwargs):
        created.update(kwargs)
        return type("Session", (), {"url": "https://checkout.test/session"})()

    monkeypatch.setattr(StripeClient, "create_checkout_session", _fake_checkout)

    token, user_id = _access_token(role=None)
    app = create_app()
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/billing/subscribe/{user_id}", json={"tier": "svod"}, headers=headers
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "checkout_required"
    assert body["checkout_url"].startswith("https://")
    assert created["user_id"] == user_id
    assert created["tier"] == "svod"


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["platinum", "premium", "gold"])
async def test_unknown_tier_never_reaches_the_payment_provider(monkeypatch, tier):
    """An unrecognised tier must not create a chargeable Stripe session."""

    def _boom(**kwargs):  # pragma: no cover - must never be called
        raise AssertionError("Stripe must not be called for an unknown tier")

    monkeypatch.setattr(StripeClient, "create_checkout_session", _boom)

    token, user_id = _access_token(role=None)
    app = create_app()
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/billing/subscribe/{user_id}", json={"tier": tier}, headers=headers
        )

    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_tvod_subscribe_points_at_the_purchase_endpoint(monkeypatch):
    """TVOD is per-title; subscribing to it directly is a client error."""

    def _boom(**kwargs):  # pragma: no cover - must never be called
        raise AssertionError("Stripe must not be called for a TVOD subscription")

    monkeypatch.setattr(StripeClient, "create_checkout_session", _boom)

    token, user_id = _access_token(role=None)
    app = create_app()
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/billing/subscribe/{user_id}", json={"tier": "tvod"}, headers=headers
        )

    assert resp.status_code == 400, resp.text
