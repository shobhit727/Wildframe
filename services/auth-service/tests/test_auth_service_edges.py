"""Edge-branch coverage for AuthService — lockouts, 404s, email/MFA flows."""

from datetime import UTC, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.schemas import UserLoginRequest, UserRegisterRequest
from app.security import PasswordManager, SecretCipher, TokenManager
from app.services import AuthService
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def repos():
    return {
        "user_repo": AsyncMock(),
        "token_repo": AsyncMock(),
        "audit_repo": AsyncMock(),
    }


@pytest.fixture
def service(repos):
    return AuthService(
        user_repo=repos["user_repo"],
        token_repo=repos["token_repo"],
        audit_repo=repos["audit_repo"],
        password_manager=PasswordManager(),
        token_manager=TokenManager(),
    )


def make_user(**overrides):
    u = MagicMock()
    u.id = uuid4()
    u.email = "user@example.com"
    u.password_hash = PasswordManager().hash_password("password123")
    u.first_name = "First"
    u.last_name = "Last"
    u.login_attempts = 0
    u.locked_until = None
    u.email_verified = False
    u.email_verification_code = None
    u.email_verification_code_expires_at = None
    u.mfa_enabled = False
    u.mfa_secret = None
    u.backup_codes = None
    u.role = "user"
    for k, v in overrides.items():
        setattr(u, k, v)
    return u


class TestRegisterBranches:
    async def test_duplicate_email_409(self, service, repos):
        repos["user_repo"].get_by_email.return_value = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await service.register(UserRegisterRequest(email="a@b.com", password="PasSword123!"))

        assert exc.value.status_code == 409

    async def test_create_failure_wraps_500(self, service, repos):
        repos["user_repo"].get_by_email.return_value = None
        repos["user_repo"].create.side_effect = RuntimeError("boom")

        with pytest.raises(HTTPException) as exc:
            await service.register(UserRegisterRequest(email="a@b.com", password="PasSword123!"))

        assert exc.value.status_code == 500


class TestLoginBranches:
    async def test_locked_account_429(self, service, repos):
        user = make_user()
        user.locked_until = __import__("datetime").datetime.now(UTC) + timedelta(minutes=15)
        repos["user_repo"].get_by_email.return_value = user

        with pytest.raises(HTTPException) as exc:
            await service.login(
                UserLoginRequest(email="user@example.com", password="whatever"), "1.2.3.4"
            )

        assert exc.value.status_code == 429

    async def test_wrong_password_increments_and_locks(self, service, repos):
        user = _user()
        incremented = MagicMock()
        incremented.login_attempts = 5
        user.locked_until = None
        repos["user_repo"].get_by_email.return_value = user
        repos["user_repo"].increment_login_attempts.return_value = incremented
        repos["user_repo"].update.return_value = incremented

        with pytest.raises(HTTPException) as exc:
            await service.login(
                UserLoginRequest(email="user@example.com", password="wrongpass"), "1.2.3.4"
            )

        assert exc.value.status_code == 401
        repos["user_repo"].update.assert_awaited_once()
        _, kwargs = repos["user_repo"].update.await_args
        assert kwargs["locked_until"] is not None

    async def test_success_resets_attempts(self, service, repos):
        user = _user()
        repos["user_repo"].get_by_email.return_value = user
        repos["user_repo"].increment_login_attempts.return_value = user
        repos["user_repo"].update.return_value = user

        result = await service.login(
            UserLoginRequest(email="user@example.com", password="PasSword123!"), "1.2.3.4"
        )

        assert result.access_token
        repos["user_repo"].reset_login_attempts.assert_awaited_once()
        repos["token_repo"].create.assert_awaited_once()


class TestRefreshBranches:
    async def test_invalid_token_401(self, service):
        from app.security import PasswordManager as _  # noqa: F401

        service.token_manager.verify_refresh_token = MagicMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await service.refresh_token("garbage")

        assert exc.value.status_code == 401

    async def test_user_not_found_401(self, service, repos):
        service.token_manager.verify_refresh_token = MagicMock(return_value=uuid4())
        repos["user_repo"].get_by_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.refresh_token("valid-token")

        assert exc.value.status_code == 401

    async def test_token_not_stored_401(self, service, repos):
        user = _user()
        service.token_manager.verify_refresh_token = MagicMock(return_value=user.id)
        repos["user_repo"].get_by_id.return_value = user
        repos["token_repo"].get_by_token_hash.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.refresh_token("valid-token")

        assert exc.value.status_code == 401

    async def test_success_rotates_token(self, service, repos):
        user = _user()
        refresh, _, _ = service.token_manager.create_refresh_token_for_user(user)
        service.token_manager.verify_refresh_token = MagicMock(return_value=user.id)
        repos["user_repo"].get_by_id.return_value = user
        repos["user_repo"].update.return_value = user
        repos["token_repo"].get_by_token_hash.return_value = MagicMock()

        result = await service.refresh_token(refresh)

        assert result.access_token
        repos["token_repo"].revoke.assert_awaited_once()
        repos["token_repo"].create.assert_awaited_once()


class TestUserBranches:
    async def test_get_current_user_missing_404(self, service, repos):
        repos["user_repo"].get_by_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.get_current_user(uuid4())

        assert exc.value.status_code == 404

    async def test_change_password_missing_404(self, service, repos):
        repos["user_repo"].get_by_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.change_password(uuid4(), "old", "new")

        assert exc.value.status_code == 404

    async def test_change_password_wrong_old_401(self, service, repos):
        user = make_user(password_hash=PasswordManager().hash_password("realpass"))
        repos["user_repo"].get_by_id.return_value = user

        with pytest.raises(HTTPException) as exc:
            await service.change_password(user.id, "wrong", "newpass123")

        assert exc.value.status_code == 401

    async def test_change_password_success(self, service, repos):
        user = make_user()
        repos["user_repo"].get_by_id.return_value = user

        result = await service.change_password(user.id, "password123", "newpass123")

        assert result is True
        repos["user_repo"].commit.assert_awaited_once()


class TestEmailVerificationBranches:
    async def test_send_missing_user_404(self, service, repos):
        repos["user_repo"].get_by_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await service.send_email_verification(uuid4())

        assert exc.value.status_code == 404

    async def test_send_when_already_verified(self, service, repos):
        user = make_user(email_verified=True)
        repos["user_repo"].get_by_id.return_value = user

        result = await service.send_email_verification(user.id)

        assert "already verified" in result["message"]

    async def test_send_sets_code_and_expiry(self, service, repos):
        user = make_user()
        repos["user_repo"].get_by_id.return_value = user

        await service.send_email_verification(user.id)

        assert user.email_verification_code is not None
        assert user.email_verification_code_expires_at is not None
        repos["user_repo"].commit.assert_awaited_once()

    async def test_verify_wrong_code_400(self, service, repos):
        user = make_user(email_verification_code="123456")
        repos["user_repo"].get_by_id.return_value = user

        with pytest.raises(HTTPException) as exc:
            await service.verify_email(user.id, "000000")

        assert exc.value.status_code == 400

    async def test_verify_expired_code_400(self, service, repos):
        from datetime import UTC, datetime

        user = make_user(
            email_verification_code="123456",
            email_verification_code_expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        repos["user_repo"].get_by_id.return_value = user

        with pytest.raises(HTTPException) as exc:
            await service.verify_email(user.id, "123456")

        assert exc.value.status_code == 400

    async def test_verify_success_clears_code(self, service, repos):
        from datetime import UTC, datetime

        user = make_user(
            email_verification_code="123456",
            email_verification_code_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        repos["user_repo"].get_by_id.return_value = user

        result = await service.verify_email(user.id, "123456")

        assert "verified" in result["message"]
        assert user.email_verified is True
        assert user.email_verification_code is None
        repos["user_repo"].commit.assert_awaited_once()


def _user():
    """Standalone user mock helper (not a fixture to keep it importorable)."""
    u = MagicMock()
    u.id = uuid4()
    u.email = "user@example.com"
    u.password_hash = PasswordManager().hash_password("PasSword123!")
    u.login_attempts = 0
    u.locked_until = None
    u.last_login_at = None
    u.first_name = "First"
    u.last_name = "Last"
    u.role = "user"
    u.mfa_enabled = False
    return u


class TestMfaAtLogin:
    async def test_login_with_mfa_raises_challenge(self, service, repos):
        user = make_user(mfa_enabled=True, mfa_secret=SecretCipher.encrypt("AA"))
        repos["user_repo"].get_by_email.return_value = user
        repos["user_repo"].update.return_value = user
        repos["user_repo"].reset_login_attempts.return_value = user
        from app.services import MfaChallengeRequired

        with pytest.raises(MfaChallengeRequired) as exc:
            await service.login(
                UserLoginRequest(email="user@example.com", password="password123"),
                "1.2.3.4",
            )

        assert exc.value.challenge_token
        repos["token_repo"].create.assert_not_awaited()

    async def test_complete_mfa_login_invalid_challenge_401(self, service, repos):
        service.token_manager.verify_mfa_challenge = MagicMock(return_value=None)

        with pytest.raises(HTTPException) as exc:
            await service.complete_mfa_login("bad", "123456", "1.2.3.4")

        assert exc.value.status_code == 401

    async def test_complete_mfa_login_bad_code_400(self, service, repos):
        user = make_user(mfa_enabled=True, mfa_secret=SecretCipher.encrypt("AA"))
        service.token_manager.verify_mfa_challenge = MagicMock(return_value=user.id)
        repos["user_repo"].get_by_id.return_value = user

        with pytest.raises(HTTPException) as exc:
            await service.complete_mfa_login("chal", "999999", "1.2.3.4")

        assert exc.value.status_code == 400

    async def test_complete_mfa_login_success(self, service, repos):
        import pyotp

        secret = pyotp.random_base32()
        user = make_user(mfa_enabled=True, mfa_secret=SecretCipher.encrypt(secret))
        service.token_manager.verify_mfa_challenge = MagicMock(return_value=user.id)
        repos["user_repo"].get_by_id.return_value = user
        code = pyotp.TOTP(secret).now()

        result = await service.complete_mfa_login("chal", code, "1.2.3.4")

        assert result.access_token
        repos["token_repo"].create.assert_awaited_once()
