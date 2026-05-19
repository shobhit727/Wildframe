"""
Comprehensive unit tests for Auth Service.
Tests cover registration, login, token refresh, and password management.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timedelta, timezone

from app.security.manager import PasswordManager, TokenManager
from app.services.auth_service import AuthService


@pytest.fixture
def user_id():
    """Generate test user ID."""
    return uuid4()


@pytest.fixture
def mock_repositories():
    """Create mock repositories."""
    return {
        "user_repo": AsyncMock(),
        "refresh_token_repo": AsyncMock(),
        "token_blacklist_repo": AsyncMock(),
        "login_audit_repo": AsyncMock(),
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
        refresh_token_repo=mock_repositories["refresh_token_repo"],
        token_blacklist_repo=mock_repositories["token_blacklist_repo"],
        login_audit_repo=mock_repositories["login_audit_repo"],
        rate_limiter=mock_rate_limiter,
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


class TestTokenManager:
    """Test TokenManager utility."""
    
    def test_create_access_token(self):
        """Test access token creation."""
        user_id = str(uuid4())
        token = TokenManager.create_access_token(user_id)
        
        assert token is not None
        assert len(token) > 0
    
    def test_verify_token_success(self):
        """Test token verification success."""
        user_id = str(uuid4())
        token = TokenManager.create_access_token(user_id)
        
        payload = TokenManager.verify_token(token, token_type="access")
        
        assert payload is not None
        assert str(payload["sub"]) == user_id
        assert payload["type"] == "access"
    
    def test_verify_token_expired(self):
        """Test verification of expired token."""
        user_id = str(uuid4())
        
        # Create token with immediate expiration
        expires = datetime.now(timezone.utc) - timedelta(seconds=1)
        payload = {
            "sub": user_id,
            "exp": expires,
            "iat": datetime.now(timezone.utc),
            "type": "access"
        }
        
        from app.core.settings import settings
        import jwt
        expired_token = jwt.encode(
            payload,
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM
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
        
        # Mock user creation
        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.email = email
        mock_repositories["user_repo"].create.return_value = mock_user
        
        token_response, refresh_token = await auth_service.register(email, password)
        
        assert token_response["access_token"] is not None
        assert token_response["token_type"] == "bearer"
        assert refresh_token is not None
        mock_repositories["user_repo"].create.assert_called_once_with(email, password)
    
    async def test_register_rate_limited(self, auth_service, mock_rate_limiter):
        """Test registration rate limiting."""
        mock_rate_limiter.is_allowed.return_value = False
        
        with pytest.raises(ValueError, match="Too many registration attempts"):
            await auth_service.register("test@example.com", "SecurePassword123!")


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
        
        mock_repositories["user_repo"].get_by_email.return_value = mock_user
        
        token_response, refresh_token = await auth_service.login(
            email,
            password,
            user_agent="TestAgent",
            ip_address="127.0.0.1"
        )
        
        assert token_response["access_token"] is not None
        assert refresh_token is not None
        mock_repositories["login_audit_repo"].log.assert_called()
    
    async def test_login_user_not_found(self, auth_service, mock_repositories):
        """Test login with non-existent user."""
        mock_repositories["user_repo"].get_by_email.return_value = None
        
        with pytest.raises(ValueError, match="Invalid email or password"):
            await auth_service.login("nonexistent@example.com", "password")
    
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
        
        mock_repositories["user_repo"].get_by_email.return_value = mock_user
        
        with pytest.raises(ValueError, match="Invalid email or password"):
            await auth_service.login(email, wrong_password)


@pytest.mark.asyncio
class TestAuthServiceTokenRefresh:
    """Test token refresh functionality."""
    
    async def test_refresh_success(self, auth_service, mock_repositories, user_id):
        """Test successful token refresh."""
        # Create valid refresh token
        refresh_token = TokenManager.create_refresh_token(str(user_id))
        token_hash = TokenManager.hash_token(refresh_token)
        
        # Mock token retrieval
        mock_token = MagicMock()
        mock_token.device_id = None
        mock_token.revoked_at = None
        mock_repositories["refresh_token_repo"].get_by_hash.return_value = mock_token
        
        token_response, new_refresh_token = await auth_service.refresh_access_token(refresh_token)
        
        assert token_response["access_token"] is not None
        assert new_refresh_token is not None
        mock_repositories["refresh_token_repo"].get_by_hash.assert_called_once()
    
    async def test_refresh_invalid_token(self, auth_service):
        """Test refresh with invalid token."""
        with pytest.raises(ValueError, match="Invalid or expired refresh token"):
            await auth_service.refresh_access_token("invalid_token")


@pytest.mark.asyncio
class TestAuthServiceLogout:
    """Test logout functionality."""
    
    async def test_logout_success(self, auth_service, mock_repositories, user_id):
        """Test successful logout."""
        access_token = TokenManager.create_access_token(str(user_id))
        
        await auth_service.logout(access_token, user_id)
        
        mock_repositories["token_blacklist_repo"].add.assert_called_once()
        mock_repositories["refresh_token_repo"].revoke_all_for_user.assert_called_once_with(user_id)
