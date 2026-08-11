"""
Security utilities for Auth Service.
Implements JWT token handling, password hashing, and validation.
"""

import base64
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
import bcrypt
from app.core.settings import settings
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from app.models import User

logger = logging.getLogger(__name__)


PASSWORD_MAX_LENGTH: int = 128


def _encode_password(password: str) -> bytes:
    """Validate password length and encode for bcrypt.

    Raises ValueError if password exceeds PASSWORD_MAX_LENGTH characters
    instead of silently truncating.
    """
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"password exceeds maximum length of {PASSWORD_MAX_LENGTH} characters")
    return password.encode("utf-8")


def role_for_email(email: str | None) -> str:
    """Return the role for an email based on the ADMIN_EMAILS allow-list."""
    if not email:
        return "user"
    admins = {a.strip().lower() for a in settings.ADMIN_EMAILS.split(",") if a.strip()}
    return "admin" if email.strip().lower() in admins else "user"


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
        salt = bcrypt.gensalt(rounds=settings.PASSWORD_BCRYPT_ROUNDS)
        return bcrypt.hashpw(_encode_password(password), salt).decode("utf-8")

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash.

        Args:
            plain_password: Plain text password to verify
            hashed_password: Hashed password to verify against

        Returns:
            bool: True if passwords match, False otherwise
        """
        try:
            return bcrypt.checkpw(
                _encode_password(plain_password),
                hashed_password.encode("utf-8"),
            )
        except (ValueError, TypeError):
            return False


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
            "sub": str(user_id),
            "user_id": str(user_id),
            "email": email,
            "role": role_for_email(email),
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
            "sub": str(user_id),
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
    def create_email_verification_token(user_id: UUID, email: str) -> str:
        """Create a JWT used as an email ownership proof.

        Args:
            user_id: User ID
            email: User email

        Returns:
            str: Signed JWT, valid for settings.EMAIL_VERIFICATION_EXPIRATION_HOURS
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=settings.EMAIL_VERIFICATION_EXPIRATION_HOURS)
        payload = {
            "user_id": str(user_id),
            "email": email,
            "type": "email_verification",
            "iat": now,
            "exp": expires_at,
            "jti": f"emailverify_{user_id}_{now.timestamp()}",
        }
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    @staticmethod
    def create_mfa_challenge_token(user_id: UUID, email: str) -> str:
        """Create a short-lived token proving password login succeeded.

        Issued after a successful password check when the user has MFA enabled.
        It must be exchanged for real tokens via a valid TOTP code within
        ``MFA_CHALLENGE_EXPIRATION_MINUTES``.
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=settings.MFA_CHALLENGE_EXPIRATION_MINUTES)
        payload = {
            "sub": str(user_id),
            "user_id": str(user_id),
            "email": email,
            "type": "mfa_challenge",
            "iat": now,
            "exp": expires_at,
            "jti": f"mfa_{user_id}_{now.timestamp()}",
        }
        return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    @staticmethod
    def verify_mfa_challenge(token: str) -> UUID | None:
        """Verify an mfa_challenge token and return the user UUID or None."""
        try:
            payload = TokenManager.verify_token(token, token_type="mfa_challenge")
            if payload is None:
                return None
            user_id = payload.get("user_id") or payload.get("sub")
            if user_id:
                return UUID(user_id)
        except Exception:  # noqa: BLE001
            return None
        return None

    @staticmethod
    def hash_token(token: str) -> str:
        """Hash a token for secure storage using SHA-256."""
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def verify_token(token: str, token_type: str = "access") -> dict[str, Any] | None:
        """Verify and decode JWT token.

        Args:
            token: JWT token to verify
            token_type: Expected token type (access or refresh)

        Returns:
            dict | None: Decoded token payload, or None if invalid/expired
        """
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )

            if payload.get("type") != token_type:
                logger.warning(f"Invalid token type: expected {token_type}")
                raise JWTError(f"Invalid token type: expected {token_type}")

            return payload

        except ExpiredSignatureError:
            logger.debug("Token expired")
            return None
        except JWTError as e:
            if "Invalid token type" in str(e):
                raise
            logger.warning(f"Token verification failed: {e}")
            return None

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
    def create_access_token_for_user(self, user: User) -> str:
        """Create access token from a user object (service-friendly)."""
        return TokenManager.create_access_token(user.id, user.email)

    def create_refresh_token_for_user(self, user: User) -> tuple[str, str, datetime]:
        """Create refresh token and return token, hash, and expires_at."""
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRATION_DAYS)
        # Use existing static method to build token
        token = TokenManager.create_refresh_token(user.id)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return token, token_hash, expires_at

    def verify_refresh_token(self, token: str) -> UUID | None:
        """Verify refresh token and return user UUID or None."""
        try:
            payload = TokenManager.verify_token(token, token_type="refresh")
            if payload is None:
                return None
            user_id = payload.get("user_id") or payload.get("sub")
            if user_id:
                return UUID(user_id)
        except Exception:  # noqa: BLE001
            return None
        return None

    @staticmethod
    def hash_refresh_token(token: str) -> str:
        """Return sha256 hash of given refresh token for storage."""
        return hashlib.sha256(token.encode()).hexdigest()


class SecretCipher:
    """Index-encrypts at-rest secrets (TOTP MFA) with a key derived from
    the JWT secret. Keys are unique to this app instance; they do not need
    to survive cluster-wide rotation. Never store MFA secrets in plaintext."""

    @staticmethod
    def _fernet():
        from cryptography.fernet import Fernet

        key = base64.urlsafe_b64encode(hashlib.sha256(settings.JWT_SECRET_KEY.encode()).digest())
        return Fernet(key)

    @classmethod
    def encrypt(cls, plaintext: str) -> str:
        return str(cls._fernet().encrypt(plaintext.encode()).decode())

    @classmethod
    def decrypt(cls, token: str) -> str:
        try:
            return str(cls._fernet().decrypt(token.encode()).decode())
        except Exception:  # noqa: BLE001
            return ""


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
