from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from jose import jwt
from tests._test_jwks import PRIVATE_PEM

from app.core.settings import settings
from app.main import create_app
from app.models import MilestoneStatus, TrancheStatus
from app.services import BillingService, MilestoneAuthorizationError
from httpx import ASGITransport, AsyncClient


def _token(user_id, role="user"):
    payload = {
        "sub": str(user_id),
        "user_id": str(user_id),
        "role": role,
        "type": "access",
        "aud": settings.JWT_AUDIENCE,
        "iss": settings.JWT_ISSUER,
    }
    return jwt.encode(payload, PRIVATE_PEM, algorithm="RS256", headers={"kid": "k1"})


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


@pytest.mark.asyncio
async def test_create_non_owner_403():
    user = uuid4()
    other = uuid4()
    token = _token(user)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/billing/milestones",
            headers={"Authorization": f"Bearer {token}"},
            json={"creator_id": str(other), "project_title": "t", "total_commitment": "100.00"},
        )
        assert resp.status_code == 403


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
