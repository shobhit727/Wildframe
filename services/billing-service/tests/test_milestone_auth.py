from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from tests._auth_tokens import mint_access_token

from app.core.settings import settings
from app.main import create_app
from app.models import MilestoneStatus, TrancheStatus
from app.services import BillingService, MilestoneAuthorizationError
from httpx import ASGITransport, AsyncClient, Response


def _token(user_id, role="user", **overrides):
    """A realistic auth-service access token (full claim set, RS256, `av`).

    Signed with the shared test key from ``tests._test_jwks`` via
    ``tests._auth_tokens``, so ``verify_token`` runs its real signature,
    audience, issuer and expiry checks instead of short-circuiting on a
    claim-less token.
    """
    return mint_access_token(user_id, role, **overrides)


@pytest.mark.asyncio
async def test_create_unauthenticated_401():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/billing/milestones",
            json={"creator_id": str(uuid4()), "project_title": "t", "total_commitment": "100.00"},
        )
        assert resp.status_code == 401
        # 401 for the right reason: no Authorization header at all, so the
        # request never reaches signature or auth-version verification.
        assert resp.json()["detail"] == "Missing or invalid authorization header"


@pytest.mark.asyncio
async def test_create_non_owner_403():
    user = uuid4()
    other = uuid4()
    token = _token(user)
    app = create_app()
    transport = ASGITransport(app=app)
    with patch("app.api.billing_routes._verify_creator", new_callable=AsyncMock) as verify:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/billing/milestones",
                headers={"Authorization": f"Bearer {token}"},
                json={"creator_id": str(other), "project_title": "t", "total_commitment": "100.00"},
            )
        assert resp.status_code == 403
        # The token itself was accepted: this is the ownership check, not an
        # authn failure, and it fires before any creator-profile lookup.
        assert resp.json()["detail"] == "You can only create milestones for your own account"
        verify.assert_not_awaited()


# ---------------------------------------------------------------------------
# Auth-version enforcement, exercised through the real route
#
# ``_enforce_auth_version`` introspects the token against auth-service
# ``/api/v1/auth/me`` and fails closed. These pin the two halves of that
# contract: a matching ``av`` lets the request reach the ownership check
# (403), while a stale or malformed one is rejected at the boundary (401).
# They also keep the suite's auth-service double honest — if it ever stops
# reporting the presented token's ``av``, these fail instead of silently
# turning every authenticated request into a 401.
# ---------------------------------------------------------------------------


def _introspection_client(body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock(status_code=status_code)
    resp.json.return_value = body
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=resp)
    return client


async def _post_milestones(token: str, client: MagicMock) -> Response:
    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    with patch("app.api.billing_routes.httpx.AsyncClient", return_value=client):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            return await ac.post(
                "/api/v1/billing/milestones",
                headers={"Authorization": f"Bearer {token}"},
                json={"creator_id": str(uuid4()), "project_title": "t", "total_commitment": "1.00"},
            )


@pytest.mark.asyncio
async def test_route_rejects_a_stale_auth_version_before_the_ownership_check():
    token = _token(uuid4(), auth_version=1)
    client = _introspection_client({"auth_version": 2})
    resp = await _post_milestones(token, client)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired token"


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_version", ["1", 1.0, None, True])
async def test_route_rejects_a_non_integer_auth_version_claim(auth_version):
    # Only a real `int` is comparable; a string, float, bool or absent value
    # must fail closed rather than be treated as a match.
    token = _token(uuid4(), auth_version=auth_version)
    client = _introspection_client({"auth_version": auth_version})
    resp = await _post_milestones(token, client)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid token payload"


@pytest.mark.asyncio
async def test_route_rejects_when_auth_service_reports_no_auth_version():
    token = _token(uuid4())
    client = _introspection_client({})
    resp = await _post_milestones(token, client)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid token payload"


@pytest.mark.asyncio
async def test_route_rejects_when_auth_version_introspection_fails():
    token = _token(uuid4())
    client = _introspection_client({"detail": "Invalid or expired token"}, status_code=401)
    resp = await _post_milestones(token, client)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired token"


@pytest.mark.asyncio
async def test_create_self_succeeds():
    user = uuid4()
    token = _token(user)
    ms = MagicMock(id=uuid4(), status=MilestoneStatus.PENDING, total_commitment=Decimal("100.00"))
    with patch("app.api.billing_routes._verify_creator", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = None
        with patch(
            "app.api.billing_routes.BillingService.create_milestone", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = ms
            app = create_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post(
                    "/api/v1/billing/milestones",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "creator_id": str(user),
                        "project_title": "t",
                        "total_commitment": "100.00",
                    },
                )
                assert resp.status_code == 200
                assert resp.json()["milestone_id"] == str(ms.id)


@pytest.mark.asyncio
async def test_create_fails_closed_when_creator_verification_is_unconfigured(monkeypatch):
    user = uuid4()
    token = _token(user)
    monkeypatch.setattr(settings, "CREATORS_SERVICE_URL", None)
    with patch(
        "app.api.billing_routes.BillingService.create_milestone", new_callable=AsyncMock
    ) as mock_create:
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/billing/milestones",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "creator_id": str(user),
                    "project_title": "t",
                    "total_commitment": "100.00",
                },
            )
    assert resp.status_code == 503
    mock_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_non_admin_403():
    user = uuid4()
    token = _token(user, role="user")
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/billing/milestones/{uuid4()}/release",
            headers={"Authorization": f"Bearer {token}"},
            json={"tranche_number": 1},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_release_unauthenticated_401():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/billing/milestones/{uuid4()}/release",
            json={"tranche_number": 1},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_release_admin_succeeds():
    admin = uuid4()
    token = _token(admin, role="admin")
    tranche = MagicMock(
        tranche_number=1,
        percentage=Decimal("10.00"),
        amount=Decimal("10.00"),
        status=TrancheStatus.RELEASED,
    )
    with patch(
        "app.api.billing_routes.BillingService.release_tranche", new_callable=AsyncMock
    ) as mock_release:
        mock_release.return_value = tranche
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/billing/milestones/{uuid4()}/release",
                headers={"Authorization": f"Bearer {token}"},
                json={"tranche_number": 1},
            )
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_kill_non_admin_403():
    user = uuid4()
    token = _token(user, role="user")
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/billing/milestones/{uuid4()}/kill",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_kill_admin_succeeds():
    admin = uuid4()
    token = _token(admin, role="admin")
    ms = MagicMock(id=uuid4(), status=MilestoneStatus.KILLED)
    with patch(
        "app.api.billing_routes.BillingService.kill_milestone", new_callable=AsyncMock
    ) as mock_kill:
        mock_kill.return_value = ms
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/billing/milestones/{uuid4()}/kill",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_service_create_cross_tenant_forbidden():
    service = BillingService(
        sub_repo=AsyncMock(),
        purchase_repo=AsyncMock(),
        inv_repo=AsyncMock(),
        floor_repo=AsyncMock(),
        pool_repo=AsyncMock(),
        milestone_repo=AsyncMock(),
        payout_repo=AsyncMock(),
        refund_repo=AsyncMock(),
        webhook_events_repo=AsyncMock(),
    )
    creator = uuid4()
    caller = uuid4()
    with pytest.raises(MilestoneAuthorizationError):
        await service.create_milestone(
            creator, "t", Decimal("10.00"), caller_id=caller, caller_is_admin=False
        )


@pytest.mark.asyncio
async def test_service_release_non_admin_forbidden():
    service = BillingService(
        sub_repo=AsyncMock(),
        purchase_repo=AsyncMock(),
        inv_repo=AsyncMock(),
        floor_repo=AsyncMock(),
        pool_repo=AsyncMock(),
        milestone_repo=AsyncMock(),
        payout_repo=AsyncMock(),
        refund_repo=AsyncMock(),
        webhook_events_repo=AsyncMock(),
    )
    with pytest.raises(MilestoneAuthorizationError):
        await service.release_tranche(uuid4(), 1, caller_id=uuid4(), caller_is_admin=False)


@pytest.mark.asyncio
async def test_service_kill_non_admin_forbidden():
    service = BillingService(
        sub_repo=AsyncMock(),
        purchase_repo=AsyncMock(),
        inv_repo=AsyncMock(),
        floor_repo=AsyncMock(),
        pool_repo=AsyncMock(),
        milestone_repo=AsyncMock(),
        payout_repo=AsyncMock(),
        refund_repo=AsyncMock(),
        webhook_events_repo=AsyncMock(),
    )
    with pytest.raises(MilestoneAuthorizationError):
        await service.kill_milestone(uuid4(), caller_id=uuid4(), caller_is_admin=False)


@pytest.mark.asyncio
async def test_service_mutations_reject_missing_caller_identity():
    service = BillingService(
        sub_repo=AsyncMock(),
        purchase_repo=AsyncMock(),
        inv_repo=AsyncMock(),
        floor_repo=AsyncMock(),
        pool_repo=AsyncMock(),
        milestone_repo=AsyncMock(),
        payout_repo=AsyncMock(),
        refund_repo=AsyncMock(),
        webhook_events_repo=AsyncMock(),
    )
    with pytest.raises(MilestoneAuthorizationError, match="caller identity"):
        await service.create_milestone(uuid4(), "t", Decimal("10.00"))
    with pytest.raises(MilestoneAuthorizationError):
        await service.release_tranche(uuid4(), 1, caller_is_admin=True)
    with pytest.raises(MilestoneAuthorizationError):
        await service.kill_milestone(uuid4(), caller_is_admin=True)
