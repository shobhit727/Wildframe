import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

"""
Security utilities for Auth Service.
Implements JWT token handling, password hashing, and validation.
"""


import logging

from app.core.settings import settings
from jose import JWTError, jwt
from passlib.context import CryptContext

logger = logging.getLogger(__name__)

# Password hashing context
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=settings.PASSWORD_BCRYPT_ROUNDS,
)


class PasswordManager:
    """Manages password hashing and verification."""

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt.
        
        Args:
            password: Plain text password
        
        Returns:
            str: Hashed password
        """
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash.
        
        Args:
            plain_password: Plain text password to verify
            hashed_password: Hashed password to verify against
        
        Returns:
            bool: True if passwords match, False otherwise
        """
        return pwd_context.verify(plain_password, hashed_password)


class TokenManager:
    """Manages JWT token generation and validation."""

    @staticmethod
    def create_access_token(user_id: UUID, email: str) -> str:
        """Create JWT access token.
        
        Args:
            user_id: User ID
            email: User email
        
        Returns:
            str: JWT access token
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)

        payload = {
            "user_id": str(user_id),
            "email": email,
            "type": "access",
            "iat": now,
            "exp": expires_at,
            "jti": f"access_{user_id}_{now.timestamp()}",
        }

        token = jwt.encode(
            payload,
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        return token

    @staticmethod
    def create_refresh_token(user_id: UUID) -> str:
        """Create JWT refresh token.
        
        Args:
            user_id: User ID
        
        Returns:
            str: JWT refresh token
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRATION_DAYS)

        payload = {
            "user_id": str(user_id),
            "type": "refresh",
            "iat": now,
            "exp": expires_at,
            "jti": f"refresh_{user_id}_{now.timestamp()}",
        }

        token = jwt.encode(
            payload,
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        return token

    @staticmethod
    def verify_token(token: str, token_type: str = "access") -> dict[str, Any]:
        """Verify and decode JWT token.
        
        Args:
            token: JWT token to verify
            token_type: Expected token type (access or refresh)
        
        Returns:
            dict: Decoded token payload
        
        Raises:
            JWTError: If token is invalid
        """
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )

            if payload.get("type") != token_type:
                raise JWTError(f"Invalid token type: expected {token_type}")

            return payload

        except JWTError as e:
            logger.warning(f"Token verification failed: {e}")
            raise

    @staticmethod
    def extract_user_id(token: str) -> UUID | None:
        """Extract user ID from token without verification.
        
        Args:
            token: JWT token
        
        Returns:
            UUID | None: User ID if extractable, None otherwise
        """
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )
            user_id_str = payload.get("user_id")
            if user_id_str:
                return UUID(user_id_str)
        except (JWTError, ValueError):
            pass

        return None

    # Convenience wrappers for service layer compatibility
    def create_access_token_for_user(self, user) -> str:
        """Create access token from a user object (service-friendly)."""
        return TokenManager.create_access_token(str(user.id), getattr(user, "email", ""))

    def create_refresh_token_for_user(self, user) -> tuple[str, str, datetime]:
        """Create refresh token and return token, hash, and expires_at."""
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRATION_DAYS)
        # Use existing static method to build token
        token = TokenManager.create_refresh_token(str(user.id))
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return token, token_hash, expires_at

    def verify_refresh_token(self, token: str) -> UUID | None:
        """Verify refresh token and return user UUID or None."""
        try:
            payload = TokenManager.verify_token(token, token_type="refresh")
            user_id = payload.get("user_id") or payload.get("sub")
            if user_id:
                return UUID(user_id)
        except Exception:  # noqa: BLE001
            return None
        return None

    def hash_refresh_token(self, token: str) -> str:
        """Return sha256 hash of given refresh token for storage."""
        return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    """Rate limiting utilities."""

    @staticmethod
    def get_rate_limit_key(
        identifier: str,
        action: str,
    ) -> str:
        """Generate rate limit cache key.
        
        Args:
            identifier: User identifier (email or IP)
            action: Action type (login, registration, etc.)
        
        Returns:
            str: Cache key
        """
        return f"ratelimit:{action}:{identifier}"

    @staticmethod
    def get_window_size(action: str) -> tuple[int, int]:
        """Get rate limit window and attempts.
        
        Args:
            action: Action type
        
        Returns:
            tuple: (attempts_allowed, window_seconds)
        """
        if action == "login":
            return (
                settings.LOGIN_RATE_LIMIT_ATTEMPTS,
                settings.LOGIN_RATE_LIMIT_WINDOW,
            )
        elif action == "registration":
            return (
                settings.REGISTRATION_RATE_LIMIT_ATTEMPTS,
                settings.REGISTRATION_RATE_LIMIT_WINDOW,
            )
        else:
            return (10, 3600)  # Default: 10 attempts per hour
