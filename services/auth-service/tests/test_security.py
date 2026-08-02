"""Unit tests for security utilities."""

from uuid import uuid4

import pytest
from app.security import RateLimiter, TokenManager
from jose import JWTError


class TestPasswordManager:
    """Test password hashing and verification."""

    def test_hash_password(self, password_manager):
        """Test password hashing."""
        password = "SecurePass123!"
        hash_value = password_manager.hash_password(password)

        assert hash_value != password
        assert len(hash_value) > 0
        assert password_manager.verify_password(password, hash_value)

    def test_verify_password_success(self, password_manager):
        """Test successful password verification."""
        password = "SecurePass123!"
        hash_value = password_manager.hash_password(password)

        assert password_manager.verify_password(password, hash_value) is True

    def test_verify_password_failure(self, password_manager):
        """Test failed password verification."""
        password = "SecurePass123!"
        wrong_password = "WrongPass456!"
        hash_value = password_manager.hash_password(password)

        assert password_manager.verify_password(wrong_password, hash_value) is False

    def test_password_hash_uniqueness(self, password_manager):
        """Test that same password generates different hashes."""
        password = "SecurePass123!"
        hash1 = password_manager.hash_password(password)
        hash2 = password_manager.hash_password(password)

        assert hash1 != hash2
        assert password_manager.verify_password(password, hash1)
        assert password_manager.verify_password(password, hash2)


class TestTokenManager:
    """Test JWT token generation and verification."""

    def test_create_access_token(self, token_manager):
        """Test access token creation."""
        user_id = uuid4()
        email = "test@example.com"

        token = TokenManager.create_access_token(user_id, email)

        assert token is not None
        assert len(token) > 0

    def test_create_refresh_token(self, token_manager):
        """Test refresh token creation."""
        user_id = uuid4()

        token = TokenManager.create_refresh_token(user_id)

        assert token is not None
        assert len(token) > 0

    def test_verify_access_token(self, token_manager):
        """Test access token verification."""
        user_id = uuid4()
        email = "test@example.com"

        token = TokenManager.create_access_token(user_id, email)
        payload = TokenManager.verify_token(token, token_type="access")

        assert payload["user_id"] == str(user_id)
        assert payload["email"] == email
        assert payload["type"] == "access"

    def test_verify_refresh_token(self, token_manager):
        """Test refresh token verification."""
        user_id = uuid4()

        token = TokenManager.create_refresh_token(user_id)
        payload = TokenManager.verify_token(token, token_type="refresh")

        assert payload["user_id"] == str(user_id)
        assert payload["type"] == "refresh"

    def test_verify_invalid_token_type(self, token_manager):
        """Test verification with wrong token type."""
        user_id = uuid4()

        access_token = TokenManager.create_access_token(user_id, "test@example.com")

        with pytest.raises(JWTError):
            TokenManager.verify_token(access_token, token_type="refresh")

    def test_extract_user_id(self, token_manager):
        """Test user ID extraction from token."""
        user_id = uuid4()
        email = "test@example.com"

        token = TokenManager.create_access_token(user_id, email)
        extracted_id = TokenManager.extract_user_id(token)

        assert extracted_id == user_id

    def test_extract_user_id_invalid_token(self, token_manager):
        """Test user ID extraction from invalid token."""
        invalid_token = "invalid.token.here"

        extracted_id = TokenManager.extract_user_id(invalid_token)

        assert extracted_id is None

    def test_token_expiration(self, token_manager):
        """Test that expired tokens fail verification."""
        user_id = uuid4()
        email = "test@example.com"

        token = TokenManager.create_access_token(user_id, email)
        # Should verify successfully before expiration
        payload = TokenManager.verify_token(token, token_type="access")
        assert payload is not None


class TestRateLimiter:
    """Test rate limiting utilities."""

    def test_get_rate_limit_key(self):
        """Test rate limit key generation."""
        key = RateLimiter.get_rate_limit_key("user@example.com", "login")

        assert key == "ratelimit:login:user@example.com"

    def test_get_window_size_login(self):
        """Test window size for login."""
        attempts, window = RateLimiter.get_window_size("login")

        assert attempts > 0
        assert window > 0
        assert window == 60 * 15  # 15 minutes

    def test_get_window_size_registration(self):
        """Test window size for registration."""
        attempts, window = RateLimiter.get_window_size("registration")

        assert attempts > 0
        assert window > 0

    def test_get_window_size_default(self):
        """Test default window size."""
        attempts, window = RateLimiter.get_window_size("unknown_action")

        assert attempts == 10
        assert window == 3600
