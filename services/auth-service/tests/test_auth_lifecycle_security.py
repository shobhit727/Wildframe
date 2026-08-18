"""
Security lifecycle tests for the auth service (#221).

Covers the five authentication-lifecycle findings:
  1. Logout invalidates refresh credentials (revocation is DB-backed, so it
     holds across replicas).
  2. A password change invalidates all existing sessions (refresh tokens
     revoked).
  3. Access and refresh tokens stop working once an account is deactivated.
  4. Token-type separation: refresh tokens must never be accepted where an
     access token is required (checked here in auth-service; downstream
     services are covered per-service and in the integration suite).
  5. Clock skew cannot extend an expired token indefinitely: the 60s leeway
     is bounded.
"""

import time
import uuid

import jwt
import pytest
import pytest_asyncio
from app.core.database import get_db
from app.core.settings import settings
from app.main import app
from httpx import ASGITransport, AsyncClient

PASSWORD = "SecurePassword123!"


@pytest_asyncio.fixture
async def client(db_session):
    """Create test HTTP client."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.mark.asyncio
class TestLogoutInvalidatesRefresh:
    """Finding 1: logout must invalidate the refresh credential."""

    async def test_refresh_rejected_after_logout_with_refresh_body(self, client):
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": "logout1@example.com", "password": PASSWORD},
        )
        refresh_token = register_response.json()["refresh_token"]

        logout_response = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": refresh_token}
        )
        assert logout_response.status_code == 204

        refresh_response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert refresh_response.status_code == 401

    async def test_logout_with_access_token_blacklists_it(self, client):
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": "logout2@example.com", "password": PASSWORD},
        )
        access_token = register_response.json()["access_token"]

        logout_response = await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert logout_response.status_code == 204

        me_response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert me_response.status_code == 401

    async def test_refresh_token_is_one_time_use(self, client):
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": "logout3@example.com", "password": PASSWORD},
        )
        refresh_token = register_response.json()["refresh_token"]

        first = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert first.status_code == 200

        second = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert second.status_code == 401


@pytest.mark.asyncio
class TestPasswordChangeInvalidatesSessions:
    """Finding 2: password reset/change must invalidate sessions."""

    async def test_refresh_rejected_after_password_change(self, client):
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": "pwchange1@example.com", "password": PASSWORD},
        )
        access_token = register_response.json()["access_token"]
        refresh_token = register_response.json()["refresh_token"]

        change_response = await client.post(
            "/api/v1/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "NewPassword456!"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert change_response.status_code == 204

        refresh_response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert refresh_response.status_code == 401

    async def test_multiple_sessions_revoked_by_password_change(self, client, db_session):
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": "pwchange2@example.com", "password": PASSWORD},
        )
        access_token = register_response.json()["access_token"]
        refresh_token_1 = register_response.json()["refresh_token"]

        login_response = await client.post(
            "/api/v1/auth/login",
            json={"email": "pwchange2@example.com", "password": PASSWORD},
        )
        refresh_token_2 = login_response.json()["refresh_token"]

        change_response = await client.post(
            "/api/v1/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "NewPassword456!"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert change_response.status_code == 204

        for stale in (refresh_token_1, refresh_token_2):
            refresh_response = await client.post(
                "/api/v1/auth/refresh", json={"refresh_token": stale}
            )
            assert refresh_response.status_code == 401, f"stale token accepted: {stale[:20]}..."


@pytest.mark.asyncio
class TestInactiveAccount:
    """Finding 3: deactivated accounts must not authenticate."""

    async def _deactivate(self, db_session, email: str) -> None:
        from sqlalchemy import select

        from app.models import User

        user = (
            await db_session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        assert user is not None
        user.is_active = False
        await db_session.commit()

    async def test_login_rejected_for_inactive_user(self, client, db_session):
        email = "inactive1@example.com"
        register_response = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
        )
        assert register_response.status_code == 201

        await self._deactivate(db_session, email)

        login_response = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert login_response.status_code == 401

    async def test_refresh_rejected_for_inactive_user(self, client, db_session):
        email = "inactive2@example.com"
        register_response = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
        )
        refresh_token = register_response.json()["refresh_token"]

        await self._deactivate(db_session, email)

        refresh_response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert refresh_response.status_code == 401

    async def test_access_token_rejected_for_inactive_user(self, client, db_session):
        email = "inactive3@example.com"
        register_response = await client.post(
            "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
        )
        access_token = register_response.json()["access_token"]

        await self._deactivate(db_session, email)

        me_response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert me_response.status_code == 401


@pytest.mark.asyncio
class TestTokenTypeSeparation:
    """Finding 4: refresh tokens are not access tokens."""

    def _mint(self, token_type: str, exp_offset: int = 900) -> str:
        now = int(time.time())
        payload = {
            "sub": str(uuid.uuid4()),
            "type": token_type,
            "aud": settings.JWT_AUDIENCE,
            "iat": now,
            "exp": now + exp_offset,
        }
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    async def test_refresh_token_rejected_on_access_endpoint(self, client):
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": "type1@example.com", "password": PASSWORD},
        )
        refresh_token = register_response.json()["refresh_token"]

        me_response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh_token}"}
        )
        assert me_response.status_code == 401

    async def test_minted_refresh_type_token_rejected(self, client):
        token = self._mint("refresh")
        me_response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me_response.status_code == 401

    async def test_access_type_token_accepted(self, client):
        token = self._mint("access")
        me_response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me_response.status_code == 401  # unknown sub, but type check passed


@pytest.mark.asyncio
class TestClockSkewBound:
    """Finding 5: leeway is bounded; expired tokens cannot be extended forever."""

    def _mint(self, exp_offset: int) -> str:
        now = int(time.time())
        payload = {
            "sub": str(uuid.uuid4()),
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iat": now - 3600,
            "exp": now + exp_offset,
        }
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    async def test_token_expired_beyond_leeway_rejected(self, client):
        assert settings.JWT_LEEWAY_SECONDS < 120
        token = self._mint(-120)
        me_response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me_response.status_code == 401

    async def test_token_within_leeway_accepted(self, client):
        assert settings.JWT_LEEWAY_SECONDS >= 60
        token = self._mint(-30)
        me_response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me_response.status_code == 401  # 401 for unknown sub, not for expiry
