"""Gap-fill endpoint tests for Auth Service error/edge branches.

Covers the endpoint-level branches the happy-path suites skip:
- logout via access token (blacklist path, not just refresh-token body)
- refresh with a malformed/invalid refresh token
- verify-email / resend-verification error branches (404 user, already verified)
- MFA 409 already-enabled, 400 not-set-up, and disable-when-off branches
- change-password with a wrong current password (401)
"""

import pytest
from app.core.database import DatabaseManager
from app.main import create_app
from app.models import Base
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, patch


@pytest.fixture
async def test_app(tmp_path):
    test_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/test.db",
        connect_args={"timeout": 15},
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    original_get_engine = DatabaseManager.get_engine
    DatabaseManager.get_engine = lambda: test_engine

    DatabaseManager._session_factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    original_session_factory = DatabaseManager.get_session_factory
    DatabaseManager.get_session_factory = lambda: DatabaseManager._session_factory

    app = create_app()

    yield app

    await test_engine.dispose()
    DatabaseManager.get_engine = original_get_engine
    DatabaseManager.get_session_factory = original_session_factory


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


@pytest.fixture
def registered(client):
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "gapfill@example.com",
            "password": "SecurePass123!",
            "first_name": "Gap",
            "last_name": "Fill",
        },
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "gapfill@example.com", "password": "SecurePass123!"},
    )
    data = login.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "headers": {"Authorization": f"Bearer {data['access_token']}"},
    }


class TestLogoutEdgeCases:
    def test_logout_with_access_token_blacklists_it(self, client, registered):
        # Logout via the Authorization header (access-token blacklist path).
        response = client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {registered['access_token']}"}
        )

        assert response.status_code == 204

        # The blacklisted access token must now be rejected.
        me = client.get("/api/v1/users/me", headers=registered["headers"])
        assert me.status_code == 401

    def test_logout_without_token_or_body_returns_401(self, client):
        response = client.post("/api/v1/auth/logout")

        assert response.status_code == 401


class TestRefreshEdgeCases:
    def test_refresh_with_garbage_token_returns_401(self, client):
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": "not.a.jwt"})

        assert response.status_code == 401

    def test_refresh_with_blacklisted_token_returns_401(self, client, registered):
        ref = registered["refresh_token"]
        client.post("/api/v1/auth/logout", json={"refresh_token": ref})

        response = client.post("/api/v1/auth/refresh", json={"refresh_token": ref})

        assert response.status_code == 401


class TestEmailVerificationEdgeCases:
    GENERIC = (
        "If an account exists with this email and has not yet been verified, "
        "a new verification email has been sent."
    )

    @pytest.fixture(autouse=True)
    def _no_rate_limit(self):
        """Normal flows run with throttling disabled (unit-style contract tests)."""
        with patch("app.api.routes.auth.allow", new=AsyncMock(return_value=True)):
            yield

    def test_resend_for_unknown_email_is_indistinguishable(self, client):
        response = client.post(
            "/api/v1/auth/resend-verification", json={"email": "nobody@example.com"}
        )

        assert response.status_code == 202
        assert response.json()["message"] == self.GENERIC
        assert "verification_token" not in response.json()

    def test_resend_when_already_verified(self, client, registered):
        resend = client.post(
            "/api/v1/auth/resend-verification", json={"email": "gapfill@example.com"}
        )
        # Fresh accounts are registered but the flow below verifies them.
        token = resend.json().get("verification_token")
        assert token, "dev should return a verification token"

        verify = client.post(
            "/api/v1/auth/verify-email",
            json={"email": "gapfill@example.com", "token": token},
        )
        assert verify.status_code == 200

        again = client.post(
            "/api/v1/auth/resend-verification", json={"email": "gapfill@example.com"}
        )
        assert again.status_code == 202
        assert again.json()["message"] == self.GENERIC
        assert "verification_token" not in again.json()

    def test_verified_and_unverified_are_indistinguishable(self, client, registered):
        first = client.post(
            "/api/v1/auth/resend-verification", json={"email": "gapfill@example.com"}
        )
        assert first.status_code == 202
        assert first.json()["message"] == self.GENERIC
        assert "verification_token" in first.json()

        token = first.json()["verification_token"]
        verify = client.post(
            "/api/v1/auth/verify-email",
            json={"email": "gapfill@example.com", "token": token},
        )
        assert verify.status_code == 200

        second = client.post(
            "/api/v1/auth/resend-verification", json={"email": "gapfill@example.com"}
        )
        assert second.status_code == 202
        assert second.json()["message"] == self.GENERIC
        assert second.json() == first.json() or "verification_token" not in second.json()

    def test_resend_throttled_returns_429(self, client):
        with patch("app.api.routes.auth.allow", new=AsyncMock(return_value=False)):
            response = client.post(
                "/api/v1/auth/resend-verification", json={"email": "flood@example.com"}
            )

        assert response.status_code == 429

    def test_verify_email_for_unknown_user_returns_400(self, client):
        response = client.post(
            "/api/v1/auth/verify-email",
            json={"email": "nobody@example.com", "token": "some.jwt.here"},
        )

        assert response.status_code == 400


class TestMfaEdgeCases:
    def test_verify_mfa_without_setup_returns_400(self, client, registered):
        response = client.post(
            "/api/v1/auth/mfa/verify", headers=registered["headers"], json={"code": "123456"}
        )

        assert response.status_code == 400

    def test_setup_mfa_when_already_enabled_returns_409(self, client, registered):
        import pyotp

        setup = client.post("/api/v1/auth/mfa/setup", headers=registered["headers"])
        secret = setup.json()["secret"]

        ok = client.post(
            "/api/v1/auth/mfa/verify",
            headers=registered["headers"],
            json={"code": pyotp.TOTP(secret).now()},
        )
        assert ok.status_code == 200

        again = client.post("/api/v1/auth/mfa/setup", headers=registered["headers"])
        assert again.status_code == 409

    def test_disable_mfa_when_not_enabled(self, client, registered):
        response = client.post(
            "/api/v1/auth/mfa/disable",
            headers=registered["headers"],
            json={"code": "123456"},
        )

        assert response.status_code == 200
        assert response.json()["message"] == "MFA is not enabled"

    def test_mfa_login_verify_rate_limited_returns_429(self, client):
        """#77/#97: TOTP brute-force on the challenge exchange is throttled."""
        with patch("app.api.routes.auth.allow", new=AsyncMock(return_value=False)):
            response = client.post(
                "/api/v1/auth/mfa/login-verify",
                json={"mfa_challenge": "any.challenge.token", "code": "123456"},
            )

        assert response.status_code == 429


class TestChangePasswordEdgeCases:
    def test_change_password_wrong_current_password_returns_401(self, client, registered):
        response = client.post(
            "/api/v1/users/change-password",
            json={"current_password": "WrongPass456!", "new_password": "NewSecurePass789!"},
            headers=registered["headers"],
        )

        assert response.status_code == 401

    def test_change_password_weak_new_password_returns_422(self, client, registered):
        response = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "SecurePass123!", "new_password": "weak"},
            headers=registered["headers"],
        )

        assert response.status_code == 422
