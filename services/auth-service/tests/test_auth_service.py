"""
Comprehensive unit tests for Auth Service.
Tests cover registration, login, token refresh, and password management.
"""

from datetime import UTC, datetime, timedelta
from unittest import mock as unittest_mock
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from app.schemas import UserLoginRequest, UserRegisterRequest
from app.repositories import UserRepository
from app.security import PasswordManager, TokenManager
from app.services import AuthService
from fastapi import HTTPException


@pytest.fixture
def user_id():
    """Generate test user ID."""
    return uuid4()


@pytest.fixture
def mock_repositories():
    """Create mock repositories."""
    return {
        "user_repo": AsyncMock(),
        "token_repo": AsyncMock(),
        "audit_repo": AsyncMock(),
    }


@pytest.fixture
def mock_rate_limiter():
    """Create mock rate limiter."""
    limiter = AsyncMock()
    limiter.is_allowed = AsyncMock(return_value=True)
    limiter.reset = AsyncMock()
    return limiter


@pytest.fixture
def auth_service(mock_repositories, mock_rate_limiter):
    """Create AuthService instance with mocks."""
    return AuthService(
        user_repo=mock_repositories["user_repo"],
        token_repo=mock_repositories["token_repo"],
        audit_repo=mock_repositories["audit_repo"],
        password_manager=PasswordManager(),
        token_manager=TokenManager(),
    )


class TestPasswordManager:
    """Test PasswordManager utility."""

    def test_hash_password(self):
        """Test password hashing."""
        password = "SecurePassword123!"
        hash1 = PasswordManager.hash_password(password)

        assert hash1 != password
        assert len(hash1) > 0

    def test_verify_password_success(self):
        """Test password verification success."""
        password = "SecurePassword123!"
        hash_val = PasswordManager.hash_password(password)

        assert PasswordManager.verify_password(password, hash_val)

    def test_verify_password_failure(self):
        """Test password verification failure."""
        password = "SecurePassword123!"
        wrong_password = "WrongPassword456!"
        hash_val = PasswordManager.hash_password(password)

        assert not PasswordManager.verify_password(wrong_password, hash_val)

    def test_needs_rehash_detects_low_cost_factor(self):
        """#437: hashes with a lower cost factor than configured need upgrade."""
        import bcrypt as _bcrypt

        from app.core.settings import settings

        low = _bcrypt.hashpw(b"x", _bcrypt.gensalt(rounds=4)).decode()
        current = PasswordManager.hash_password("SecurePassword123!")

        assert PasswordManager.needs_rehash(low)
        assert not PasswordManager.needs_rehash(current)
        assert not PasswordManager.needs_rehash("not-a-bcrypt-hash")
        # The current settings rounds must be > 4 for this test to be valid.
        assert settings.PASSWORD_BCRYPT_ROUNDS > 4

    def test_dummy_hash_verifies_like_a_real_check(self):
        """#163/#436: the dummy hash accepts nothing but costs full bcrypt work."""
        dummy = PasswordManager.dummy_hash()
        assert dummy.startswith("$2")
        assert not PasswordManager.verify_password("any-guess", dummy)

    def test_normalize_email_canonicalizes_unicode_and_case(self):
        """#161: NFC + casefold so visually identical emails are one principal."""
        from app.security import normalize_email

        assert normalize_email("User@Example.COM") == "user@example.com"
        composed = "jos\u00e9@example.com"  # precomposed é
        decomposed = "jose\u0301@example.com"  # e + combining acute
        assert normalize_email(composed) == normalize_email(decomposed)


class TestTokenManager:
    """Test TokenManager utility."""

    def test_create_access_token(self):
        """Test access token creation."""
        user_id = str(uuid4())
        token = TokenManager.create_access_token(user_id, "test@example.com")
        assert token is not None
        assert len(token) > 0

    def test_verify_token_success(self):
        """Test token verification success."""
        user_id = str(uuid4())
        token = TokenManager.create_access_token(user_id, "test@example.com")

        payload = TokenManager.verify_token(token, token_type="access")

        assert payload is not None
        assert str(payload["user_id"]) == user_id
        assert payload["type"] == "access"

    def test_verify_token_expired(self):
        """Test verification of expired token."""
        user_id = str(uuid4())

        from app.core.settings import settings

        expires = datetime.now(UTC) - timedelta(seconds=70)
        payload = {
            "sub": user_id,
            "user_id": user_id,
            "type": "access",
            "aud": settings.JWT_AUDIENCE,
            "iss": settings.JWT_ISSUER,
            "exp": expires,
            "iat": datetime.now(UTC),
        }

        from app.security.jwks import get_private_key_pem
        from jose import jwt as _jwt

        expired_token = _jwt.encode(
            payload, get_private_key_pem(), algorithm=settings.JWT_ALGORITHM, headers={"kid": settings.JWT_KEY_ID}
        )

        result = TokenManager.verify_token(expired_token, token_type="access")
        assert result is None

    def test_hash_token(self):
        """Test token hashing."""
        token = "test_token_value"
        hash1 = TokenManager.hash_token(token)
        hash2 = TokenManager.hash_token(token)

        # Same token should produce same hash
        assert hash1 == hash2
        assert hash1 != token

    def test_minted_tokens_carry_kid_header(self):
        """#138: minted tokens advertise the active key id via kid."""
        from jose import jwt as _jwt

        from app.core.settings import settings

        token = TokenManager.create_access_token(str(uuid4()), "kid@example.com")
        header = _jwt.get_unverified_header(token)
        assert header.get("kid") == settings.JWT_KEY_ID

    def test_overlap_secret_verifies_during_rotation(self, monkeypatch):
        import base64
        import json
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        from app.core.settings import settings
        from app.security.jwks import reset_cache

        def _b64(n: int) -> str:
            b = n.to_bytes((n.bit_length() + 7)//8, "big")
            return base64.urlsafe_b64encode(b).decode().rstrip("=")

        prev_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        prev_pem = prev_priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        nums = prev_priv.public_key().public_numbers()
        prev_jwk = {"kty":"RSA","kid":"k0","use":"sig","alg":"RS256","n":_b64(nums.n),"e":_b64(nums.e)}
        user_id = str(uuid4())
        now = datetime.now(UTC)
        payload = {
            "sub": user_id,
            "user_id": user_id,
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
        }
        from jose import jwt as _jwt
        old_token = _jwt.encode(payload, prev_pem, algorithm="RS256", headers={"kid":"k0"})
        assert TokenManager.verify_token(old_token) is None
        monkeypatch.setattr(settings, "JWT_PREVIOUS_JWKS", json.dumps([prev_jwk]))
        reset_cache()
        decoded = TokenManager.verify_token(old_token)
        assert decoded is not None and decoded["sub"] == user_id
        monkeypatch.setattr(settings, "JWT_PREVIOUS_JWKS", "")
        reset_cache()
        assert TokenManager.verify_token(old_token) is None


@pytest.mark.asyncio
class TestAuthServiceRegister:
    """Test registration functionality."""

    async def test_register_success(self, auth_service, mock_repositories, user_id):
        """Test successful registration."""
        email = "test@example.com"
        password = "SecurePassword123!"
        PasswordManager.hash_password(password)

        # Mock user creation - user doesn't exist
        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.email = email
        mock_user.first_name = "Test"
        mock_user.last_name = "User"
        mock_user.role = "user"
        mock_repositories["user_repo"].create.return_value = mock_user
        mock_repositories["user_repo"].get_by_email.return_value = None

        request = UserRegisterRequest(
            email=email, password=password, first_name="Test", last_name="User"
        )
        result = await auth_service.register(request)

        assert result is not None
        assert result.email == email
        # Verify the create call was made (password_hash is hashed each time so we can't exact-match)
        mock_repositories["user_repo"].create.assert_called_once()
        call_kwargs = mock_repositories["user_repo"].create.call_args.kwargs
        assert call_kwargs["email"] == email
        assert call_kwargs["first_name"] == "Test"
        assert call_kwargs["last_name"] == "User"
        assert "password_hash" in call_kwargs

    async def test_register_rate_limited(self, auth_service, mock_repositories, user_id):
        """Test that registration rate limiting would trigger (rate limiter not yet integrated)."""
        # AuthService currently doesn't integrate rate_limiter; this test
        # documents the expected behavior once rate limiting is added.
        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.email = "test@example.com"
        mock_user.first_name = "Test"
        mock_user.last_name = "User"
        mock_user.role = "user"
        mock_repositories["user_repo"].create.return_value = mock_user
        mock_repositories["user_repo"].get_by_email.return_value = None
        request = UserRegisterRequest(
            email="test@example.com",
            password="SecurePassword123!",
            first_name="Test",
            last_name="User",
        )

        # Without rate limiter integration, this currently succeeds.
        result = await auth_service.register(request)
        assert result is not None


@pytest.mark.asyncio
class TestAuthServiceLogin:
    """Test login functionality."""

    async def test_login_success(self, auth_service, mock_repositories, user_id):
        """Test successful login."""
        email = "test@example.com"
        password = "SecurePassword123!"

        # Create mock user
        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.email = email
        mock_user.password_hash = PasswordManager.hash_password(password)
        mock_user.is_active = True
        mock_user.is_locked = False
        mock_user.login_attempts = 0
        mock_user.locked_until = None

        mock_repositories["user_repo"].get_by_email.return_value = mock_user
        mock_repositories["user_repo"].update.return_value = mock_user
        mock_repositories["user_repo"].reset_login_attempts.return_value = mock_user
        mock_user.mfa_enabled = False
        mock_user.auth_version = 0

        request = UserLoginRequest(email=email, password=password)
        result = await auth_service.login(request, ip_address="127.0.0.1")

        assert result.access_token is not None
        mock_repositories["audit_repo"].create.assert_called()

    async def test_login_user_not_found(self, auth_service, mock_repositories):
        """Test login with non-existent user."""
        mock_repositories["user_repo"].get_by_email.return_value = None

        request = UserLoginRequest(email="nonexistent@example.com", password="password")

        with pytest.raises(HTTPException) as exc_info:
            await auth_service.login(request, ip_address="127.0.0.1")
        assert exc_info.value.status_code == 401

    async def test_login_user_not_found_burns_bcrypt_time(self, auth_service, mock_repositories):
        """#163/#436: unknown-user path performs a dummy verification so its
        timing matches the wrong-password path (no enumeration signal)."""
        mock_repositories["user_repo"].get_by_email.return_value = None
        real_verify = PasswordManager.verify_password
        calls: list[tuple[str, str]] = []

        def spy(password: str, hashed: str) -> bool:
            calls.append((password, hashed))
            return real_verify(password, hashed)

        with unittest_mock.patch.object(PasswordManager, "verify_password", staticmethod(spy)):
            with pytest.raises(HTTPException):
                await auth_service.login(
                    UserLoginRequest(email="ghost@example.com", password="Whatever123!"),
                    ip_address="127.0.0.1",
                )

        # Exactly one verification happened, against the shared dummy hash.
        assert len(calls) == 1
        assert calls[0][1] == PasswordManager.dummy_hash()

    async def test_login_upgrades_low_cost_hash_on_success(
        self, auth_service, mock_repositories, user_id
    ):
        """#437: successful login transparently rehashes legacy-cost hashes."""
        import bcrypt as _bcrypt

        from app.core.settings import settings

        email = "legacy@example.com"
        password = "SecurePassword123!"
        legacy_hash = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt(rounds=4)).decode()
        assert settings.PASSWORD_BCRYPT_ROUNDS > 4

        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.email = email
        mock_user.password_hash = legacy_hash
        mock_user.is_active = True
        mock_user.is_locked = False
        mock_user.login_attempts = 0
        mock_user.locked_until = None
        mock_user.mfa_enabled = False
        mock_user.auth_version = 0

        mock_repositories["user_repo"].get_by_email.return_value = mock_user
        mock_repositories["user_repo"].update.return_value = mock_user
        mock_repositories["user_repo"].reset_login_attempts.return_value = mock_user

        request = UserLoginRequest(email=email, password=password)
        result = await auth_service.login(request, ip_address="127.0.0.1")

        assert result.access_token is not None
        # The rehash update must have carried an upgraded hash.
        update_calls = mock_repositories["user_repo"].update.await_args_list
        rehashed = [
            c.kwargs.get("password_hash") for c in update_calls if "password_hash" in c.kwargs
        ]
        assert rehashed, "expected a password_hash upgrade update"
        new_hash = rehashed[0]
        assert int(new_hash.split("$")[2]) == settings.PASSWORD_BCRYPT_ROUNDS
        assert PasswordManager.verify_password(password, new_hash)

    async def test_login_invalid_password(self, auth_service, mock_repositories, user_id):
        """Test login with wrong password."""
        email = "test@example.com"
        password = "SecurePassword123!"
        wrong_password = "WrongPassword456!"

        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.email = email
        mock_user.password_hash = PasswordManager.hash_password(password)
        mock_user.is_active = True
        mock_user.is_locked = False
        mock_user.login_attempts = 0
        mock_user.locked_until = None

        # increment_login_attempts should return a user with attempts < max
        incremented_user = MagicMock()
        incremented_user.id = user_id
        incremented_user.email = email
        incremented_user.login_attempts = 1
        incremented_user.locked_until = None

        mock_repositories["user_repo"].get_by_email.return_value = mock_user
        mock_repositories["user_repo"].increment_login_attempts.return_value = incremented_user

        request = UserLoginRequest(email=email, password=wrong_password)

        with pytest.raises(HTTPException) as exc_info:
            await auth_service.login(request, ip_address="127.0.0.1")
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
class TestAuthServiceTokenRefresh:
    """Test token refresh functionality."""

    async def test_refresh_success(self, auth_service, mock_repositories, user_id):
        """Test successful token refresh."""
        # Create valid refresh token
        refresh_token = TokenManager.create_refresh_token(str(user_id))
        TokenManager.hash_token(refresh_token)

        # Mock user
        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.email = "test@example.com"
        mock_user.auth_version = 0
        mock_repositories["user_repo"].get_by_id.return_value = mock_user

        mock_token = MagicMock()
        mock_token.user_id = user_id
        mock_token.expires_at = datetime.now(UTC) + timedelta(hours=1)
        mock_repositories["token_repo"].consume.return_value = mock_token
        mock_repositories["token_repo"].get_by_token_hash.return_value = mock_token

        token_response = await auth_service.refresh_token(refresh_token)

        assert token_response.access_token is not None
        mock_repositories["token_repo"].consume.assert_called_once()

    async def test_refresh_invalid_token(self, auth_service):
        """Test refresh with invalid token."""
        with pytest.raises(HTTPException) as exc_info:
            await auth_service.refresh_token("invalid_token")
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
class TestAuthServiceLogout:
    """Test logout functionality."""

    async def test_logout_success(self, auth_service, mock_repositories, user_id):
        """Test successful logout."""
        refresh_token = TokenManager.create_refresh_token(str(user_id))

        await auth_service.logout(refresh_token)

        mock_repositories["token_repo"].revoke.assert_called_once()


# ==========================================================================
# Remaining AuthService branches (app/services/__init__.py)
# ==========================================================================


@pytest.fixture
def service(test_session, test_user, token_repository, audit_repository):
    from app.repositories import TokenBlacklistRepository, UserRepository
    from app.services import AuthService

    svc = AuthService(
        user_repo=UserRepository(test_session),
        token_repo=token_repository,
        audit_repo=audit_repository,
        password_manager=PasswordManager(),
        token_manager=TokenManager(),
        blacklist_repo=TokenBlacklistRepository(test_session),
    )
    # Record every audit row the service writes so assertions do not have to
    # round-trip a nil UUID through the SQLite emulated-UUID result processor.
    svc.audit_repo.created = []
    original_create = svc.audit_repo.create

    async def _create(*args, **kwargs):
        row = await original_create(*args, **kwargs)
        svc.audit_repo.created.append(row)
        return row

    svc.audit_repo.create = _create
    return svc


class TestRegistrationEventFanOut:
    async def test_publishes_user_registered(self, service, test_session):
        from app.core import events

        request = UserRegisterRequest(
            email="fanout@example.com", password="SecurePass123!", first_name="A"
        )

        publisher = __import__(
            "wildframe_events", fromlist=["InMemoryEventPublisher"]
        ).InMemoryEventPublisher()
        events.reset_event_publisher()
        with unittest_mock.patch.object(events, "get_event_publisher", return_value=publisher):
            user = await service.register(request)
        await test_session.commit()

        assert len(publisher.sent) == 1
        assert publisher.sent[0].topic == "user.registered"
        assert publisher.sent[0].key == f"registered:{user.id}"
        assert publisher.sent[0].payload["email"] == "fanout@example.com"

    async def test_publish_failure_does_not_roll_back_registration(
        self, service, test_session
    ):
        from app.core import events

        request = UserRegisterRequest(
            email="fanout-fail@example.com", password="SecurePass123!"
        )
        exploding = AsyncMock()
        exploding.publish = unittest_mock.AsyncMock(
            side_effect=RuntimeError("broker down")
        )
        events.reset_event_publisher()
        with unittest_mock.patch.object(
            events, "get_event_publisher", return_value=exploding
        ):
            user = await service.register(request)
        await test_session.commit()

        assert user.email == "fanout-fail@example.com"
        assert await UserRepository(test_session).get_by_email("fanout-fail@example.com")


class TestCompleteMfaLoginBranches:
    async def test_unknown_challenge_is_401(self, service):
        with pytest.raises(HTTPException) as exc_info:
            await service.complete_mfa_login("not.a.jwt", "123456", ip_address="1.1.1.1")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid or expired MFA challenge"

    async def test_challenge_for_a_deleted_user_is_401(self, service, test_user):
        challenge = TokenManager.create_mfa_challenge_token(test_user.id, test_user.email)
        test_user.is_active = False
        await service.user_repo.commit()

        with pytest.raises(HTTPException) as exc_info:
            await service.complete_mfa_login(challenge, "123456", ip_address="1.1.1.1")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "User not found"

    async def test_mfa_disabled_after_the_challenge_is_400(self, service, test_user):
        from app.security import SecretCipher
        import pyotp

        secret = pyotp.random_base32()
        test_user.mfa_secret = SecretCipher.encrypt(secret)
        test_user.mfa_enabled = False
        await service.user_repo.commit()
        challenge = TokenManager.create_mfa_challenge_token(test_user.id, test_user.email)

        with pytest.raises(HTTPException) as exc_info:
            await service.complete_mfa_login(
                challenge, pyotp.TOTP(secret).now(), ip_address="1.1.1.1"
            )

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "MFA is not enabled for this user"

    async def test_wrong_totp_code_is_400(self, service, test_user):
        from app.security import SecretCipher
        import pyotp

        test_user.mfa_secret = SecretCipher.encrypt(pyotp.random_base32())
        test_user.mfa_enabled = True
        await service.user_repo.commit()
        challenge = TokenManager.create_mfa_challenge_token(test_user.id, test_user.email)

        with pytest.raises(HTTPException) as exc_info:
            await service.complete_mfa_login(challenge, "000000", ip_address="1.1.1.1")

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Invalid MFA code"

    async def test_concurrent_challenge_consumption_is_401(self, service, test_user):
        """The loser of the blacklist INSERT race gets a 401, not a 500."""
        from sqlalchemy.exc import IntegrityError

        from app.security import SecretCipher
        import pyotp

        secret = pyotp.random_base32()
        test_user.mfa_secret = SecretCipher.encrypt(secret)
        test_user.mfa_enabled = True
        await service.user_repo.commit()
        challenge = TokenManager.create_mfa_challenge_token(test_user.id, test_user.email)

        with unittest_mock.patch.object(
            type(service.blacklist_repo),
            "create",
            AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("dup"))),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await service.complete_mfa_login(
                    challenge, pyotp.TOTP(secret).now(), ip_address="1.1.1.1"
                )

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid or expired MFA challenge"

    async def test_completes_and_issues_tokens(self, service, test_user):
        from app.security import SecretCipher
        import pyotp

        secret = pyotp.random_base32()
        test_user.mfa_secret = SecretCipher.encrypt(secret)
        test_user.mfa_enabled = True
        await service.user_repo.commit()
        challenge = TokenManager.create_mfa_challenge_token(test_user.id, test_user.email)

        response = await service.complete_mfa_login(
            challenge, pyotp.TOTP(secret).now(), ip_address="1.1.1.1"
        )

        assert response.token_type == "bearer"
        assert response.expires_in == 900
        assert TokenManager.verify_token(response.access_token, token_type="access")


class TestRefreshTokenBranches:
    async def test_user_id_mismatch_is_401(self, service, test_user, test_session):
        from app.models import RefreshToken

        real_user = await service.user_repo.get_by_email(test_user.email)
        other = RefreshToken(
            user_id=uuid4(),
            token_hash=TokenManager.hash_refresh_token(
                TokenManager.create_refresh_token(real_user.id)
            ),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        test_session.add(other)
        await test_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await service.refresh_token(
                TokenManager.create_refresh_token(real_user.id)
            )

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Refresh token not found"

    async def test_expired_stored_token_is_401(self, service, test_user, test_session):
        from app.models import RefreshToken

        user = await service.user_repo.get_by_email(test_user.email)
        expired_token = TokenManager.create_refresh_token(user.id)
        test_session.add(
            RefreshToken(
                user_id=user.id,
                token_hash=TokenManager.hash_refresh_token(expired_token),
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        await test_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await service.refresh_token(expired_token)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Refresh token expired"

    async def test_rotates_a_valid_refresh_token(self, service, test_user, test_session):
        from app.models import RefreshToken

        user = await service.user_repo.get_by_email(test_user.email)
        original = TokenManager.create_refresh_token(user.id)
        test_session.add(
            RefreshToken(
                user_id=user.id,
                token_hash=TokenManager.hash_refresh_token(original),
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        await test_session.commit()

        response = await service.refresh_token(original)

        assert response.refresh_token != original
        assert TokenManager.verify_token(response.access_token, token_type="access")
        # The old row is gone; the new one exists.
        assert (
            await service.token_repo.get_by_token_hash(
                TokenManager.hash_refresh_token(original)
            )
            is None
        )

    async def test_suspended_user_cannot_refresh(self, service, test_user, test_session):
        from app.models import RefreshToken

        user = await service.user_repo.get_by_email(test_user.email)
        token = TokenManager.create_refresh_token(user.id)
        test_session.add(
            RefreshToken(
                user_id=user.id,
                token_hash=TokenManager.hash_refresh_token(token),
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        user.is_active = False
        await test_session.commit()

        # BUG-adjacent: `get_by_id` also filters `is_active IS TRUE`, so the
        # dedicated 403 "Account suspended" branch at
        # app/services/__init__.py:348 is unreachable and the caller sees 401.
        with pytest.raises(HTTPException) as exc_info:
            await service.refresh_token(token)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "User not found"

    async def test_unknown_user_cannot_refresh(self, service):
        orphan = TokenManager.create_refresh_token(uuid4())

        with pytest.raises(HTTPException) as exc_info:
            await service.refresh_token(orphan)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "User not found"


class TestLogoutAndProfileHelpers:
    async def test_logout_returns_false_when_the_token_is_unknown(self, service):
        assert await service.logout("never-issued") is False

    async def test_logout_returns_true_for_a_known_token(self, service, test_user, test_session):
        from app.models import RefreshToken

        user = await service.user_repo.get_by_email(test_user.email)
        token = TokenManager.create_refresh_token(user.id)
        test_session.add(
            RefreshToken(
                user_id=user.id,
                token_hash=TokenManager.hash_refresh_token(token),
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        await test_session.commit()

        assert await service.logout(token) is True

    async def test_logout_swallows_repository_errors(self, service):
        with unittest_mock.patch.object(
            type(service.token_repo), "revoke", AsyncMock(side_effect=RuntimeError("io"))
        ):
            assert await service.logout("any-token") is False

    async def test_get_current_user_returns_the_profile(self, service, test_user):
        user = await service.user_repo.get_by_email(test_user.email)

        profile = await service.get_current_user(user.id)

        assert profile.id == user.id
        assert profile.email == test_user.email
        assert profile.role == "user"

    async def test_get_current_user_for_a_missing_user_is_404(self, service):
        with pytest.raises(HTTPException) as exc_info:
            await service.get_current_user(uuid4())

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "User not found"

    async def test_change_password_revokes_refresh_tokens(self, service, test_user, test_session):
        from app.models import RefreshToken

        user = await service.user_repo.get_by_email(test_user.email)
        test_session.add(
            RefreshToken(
                user_id=user.id,
                token_hash="session-token",
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        await test_session.commit()
        before = user.auth_version

        assert await service.change_password(user.id, "testpass123", "BrandNewPass456!") is True
        await test_session.commit()

        refreshed = await service.user_repo.get_by_email(test_user.email)
        assert refreshed.auth_version == before + 1
        assert await service.token_repo.get_by_token_hash("session-token") is None
        assert PasswordManager.verify_password("BrandNewPass456!", refreshed.password_hash)

    async def test_change_password_wrong_old_password_is_401(self, service, test_user):
        user = await service.user_repo.get_by_email(test_user.email)

        with pytest.raises(HTTPException) as exc_info:
            await service.change_password(user.id, "wrong-password", "BrandNewPass456!")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid password"

    async def test_change_password_for_a_missing_user_is_404(self, service):
        with pytest.raises(HTTPException) as exc_info:
            await service.change_password(uuid4(), "old-password", "BrandNewPass456!")

        assert exc_info.value.status_code == 404


class TestLoginRemainingBranches:
    async def test_suspended_account_is_401_because_lookup_filters_it(
        self, service, test_user
    ):
        """The dedicated 403 "Account suspended" branch is unreachable.

        ``UserRepository.get_by_email`` filters on ``is_active IS TRUE``, so a
        suspended account never reaches the ``if not user.is_active`` check at
        app/services/__init__.py:143 — it is reported as "user not found"
        instead. Asserted as-is; not fixed here.
        """
        user = await service.user_repo.get_by_email(test_user.email)
        user.is_active = False
        await service.user_repo.commit()

        with pytest.raises(HTTPException) as exc_info:
            await service.login(
                UserLoginRequest(email=test_user.email, password="testpass123"),
                ip_address="1.1.1.1",
            )

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid email or password"

    async def test_stored_naive_lock_raises_a_type_error_instead_of_429(
        self, service, test_user, test_session
    ):
        """BUG: the locked-account 429 is unreachable through the database.

        ``users.locked_until`` is a naive column (AGENTS.md: TIMESTAMP WITHOUT
        TIME ZONE), so a stored lock reads back without tzinfo. ``login`` then
        evaluates ``user.locked_until > datetime.now(UTC)``
        (app/services/__init__.py:128) and raises
        ``TypeError: can't compare offset-naive and offset-aware datetimes`` —
        a 500 on the login path instead of the intended 429. ``User.is_locked``
        normalises tzinfo before comparing; ``login`` does not. Asserted as-is;
        not fixed here.
        """
        from app.models import User

        user = await service.user_repo.get_by_email(test_user.email)
        # A naive value is what a TIMESTAMP WITHOUT TIME ZONE column yields.
        user.locked_until = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)
        await service.user_repo.commit()

        # Re-reading repopulates the identity-mapped row from the stored value.
        reloaded = await service.user_repo.get_by_email(test_user.email)
        assert reloaded.locked_until.tzinfo is None

        with pytest.raises(TypeError, match="offset-naive and offset-aware"):
            await service.login(
                UserLoginRequest(email=test_user.email, password="testpass123"),
                ip_address="1.1.1.1",
            )

    async def test_locked_account_is_429_when_the_value_is_tz_aware(
        self, service, test_user, test_session
    ):
        """The intended behaviour, isolated from the tz bug above.

        Handing ``login`` a user whose ``locked_until`` still carries tzinfo
        (which is what a ``TIMESTAMP WITH TIME ZONE`` column would return)
        produces the 429 and the ``locked`` audit row.
        """
        from app.models import LoginAudit, User
        from sqlalchemy import select

        stored = await service.user_repo.get_by_email(test_user.email)
        locked = User(
            id=stored.id,
            email=stored.email,
            password_hash=stored.password_hash,
            login_attempts=stored.login_attempts,
            locked_until=datetime.now(UTC) + timedelta(minutes=5),
        )

        with unittest_mock.patch.object(
            service.user_repo, "get_by_email", AsyncMock(return_value=locked)
        ):
            with pytest.raises(HTTPException) as exc_info:
                await service.login(
                    UserLoginRequest(email=stored.email, password="testpass123"),
                    ip_address="1.1.1.1",
                )
        await test_session.commit()

        assert exc_info.value.status_code == 429
        assert "temporarily locked" in exc_info.value.detail

        statuses = [
            row.status
            for row in (
                await test_session.execute(
                    select(LoginAudit).where(LoginAudit.user_id == stored.id)
                )
            ).scalars()
        ]
        assert "locked" in statuses

    async def test_repeated_failures_lock_the_account(self, service, test_user, test_session):
        request = UserLoginRequest(email=test_user.email, password="WrongPass456!")

        for _ in range(5):
            with pytest.raises(HTTPException):
                await service.login(request, ip_address="1.1.1.1")
        await test_session.commit()

        user = await service.user_repo.get_by_email(test_user.email)
        assert user.login_attempts == 5
        assert user.locked_until is not None
        # The lock is readable through the property even though the stored
        # value is naive (User.is_locked normalises tzinfo before comparing).
        assert user.is_locked is True

    async def test_successful_login_resets_attempts_and_stamps_last_login(
        self, service, test_user
    ):
        user = await service.user_repo.get_by_email(test_user.email)
        user.login_attempts = 3
        await service.user_repo.commit()

        response = await service.login(
            UserLoginRequest(email=test_user.email, password="testpass123"),
            ip_address="1.1.1.1",
        )

        assert response.token_type == "bearer"
        refreshed = await service.user_repo.get_by_email(test_user.email)
        assert refreshed.login_attempts == 0
        assert refreshed.last_login_at is not None

    async def test_successful_login_upgrades_a_weak_hash(self, service, test_user, monkeypatch):
        from app.core import settings as settings_module

        user = await service.user_repo.get_by_email(test_user.email)
        monkeypatch.setattr(settings_module.settings, "PASSWORD_BCRYPT_ROUNDS", 5)
        cheap = PasswordManager.hash_password("testpass123")
        user.password_hash = cheap
        await service.user_repo.commit()
        monkeypatch.setattr(settings_module.settings, "PASSWORD_BCRYPT_ROUNDS", 12)

        await service.login(
            UserLoginRequest(email=test_user.email, password="testpass123"),
            ip_address="1.1.1.1",
        )

        upgraded = await service.user_repo.get_by_email(test_user.email)
        assert upgraded.password_hash != cheap
        assert PasswordManager.needs_rehash(upgraded.password_hash) is False

    async def test_mfa_enabled_user_receives_a_challenge(self, service, test_user):
        from app.security import SecretCipher
        from app.services import MfaChallengeRequired
        import pyotp

        user = await service.user_repo.get_by_email(test_user.email)
        user.mfa_enabled = True
        user.mfa_secret = SecretCipher.encrypt(pyotp.random_base32())
        await service.user_repo.commit()

        with pytest.raises(MfaChallengeRequired) as exc_info:
            await service.login(
                UserLoginRequest(email=test_user.email, password="testpass123"),
                ip_address="1.1.1.1",
            )

        assert TokenManager.verify_mfa_challenge(exc_info.value.challenge_token) == user.id

    async def test_unknown_user_login_is_audited_against_a_null_user(self, service, test_session):
        with pytest.raises(HTTPException) as exc_info:
            await service.login(
                UserLoginRequest(email="nobody@example.com", password="whatever123"),
                ip_address="1.1.1.1",
            )
        await test_session.commit()

        assert exc_info.value.status_code == 401

        from app.models import LoginAudit

        audited = service.audit_repo.created[-1]
        assert isinstance(audited, LoginAudit)
        # Login records unknown users against the nil UUID, not NULL.
        assert str(audited.user_id) == "00000000-0000-0000-0000-000000000000"
        assert audited.status == "failed"
        assert audited.ip_address == "1.1.1.1"


class TestRegistrationFailurePaths:
    async def test_duplicate_email_is_409(self, service, test_user):
        with pytest.raises(HTTPException) as exc_info:
            await service.register(
                UserRegisterRequest(email=test_user.email, password="SecurePass123!")
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "User with this email already exists"

    async def test_repository_failure_becomes_500(self, service):
        with unittest_mock.patch.object(
            service.user_repo, "create", AsyncMock(side_effect=RuntimeError("pg down"))
        ):
            with pytest.raises(HTTPException) as exc_info:
                await service.register(
                    UserRegisterRequest(email="pgdown@example.com", password="SecurePass123!")
                )

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Failed to create user"


class TestMfaChallengeReuse:
    async def test_already_consumed_challenge_is_401(self, service, test_user, test_session):
        """The blacklist insert is preceded by an ``is_blacklisted`` probe."""
        from app.repositories import TokenBlacklistRepository
        from app.security import SecretCipher
        import pyotp

        secret = pyotp.random_base32()
        test_user.mfa_secret = SecretCipher.encrypt(secret)
        test_user.mfa_enabled = True
        await service.user_repo.commit()
        challenge = TokenManager.create_mfa_challenge_token(test_user.id, test_user.email)
        await TokenBlacklistRepository(test_session).create(
            token_hash=TokenManager.hash_token(challenge),
            user_id=test_user.id,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        await test_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await service.complete_mfa_login(
                challenge, pyotp.TOTP(secret).now(), ip_address="1.1.1.1"
            )

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Invalid or expired MFA challenge"


class TestRefreshUserMismatch:
    async def test_stored_token_owned_by_another_user_is_401(self, service, test_user, test_session):
        """The token verifies to user A but the stored row belongs to user B."""
        from app.models import RefreshToken

        user = await service.user_repo.get_by_email(test_user.email)
        token = TokenManager.create_refresh_token(user.id)
        test_session.add(
            RefreshToken(
                user_id=uuid4(),
                token_hash=TokenManager.hash_refresh_token(token),
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        await test_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await service.refresh_token(token)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Refresh token not found"
