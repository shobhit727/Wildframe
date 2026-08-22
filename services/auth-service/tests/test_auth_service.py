"""
Comprehensive unit tests for Auth Service.
Tests cover registration, login, token refresh, and password management.
"""

from datetime import UTC, datetime, timedelta
from unittest import mock as unittest_mock
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.schemas import UserLoginRequest, UserRegisterRequest
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

        # Create token with immediate expiration
        expires = datetime.now(UTC) - timedelta(seconds=1)
        payload = {"sub": user_id, "exp": expires, "iat": datetime.now(UTC), "type": "access"}

        import jwt
        from app.core.settings import settings

        expired_token = jwt.encode(
            payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
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

        # Mock token retrieval
        mock_token = MagicMock()
        mock_token.device_id = None
        mock_token.revoked_at = None
        mock_repositories["token_repo"].get_by_token_hash.return_value = mock_token

        token_response = await auth_service.refresh_token(refresh_token)

        assert token_response.access_token is not None
        mock_repositories["token_repo"].get_by_token_hash.assert_called_once()

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
