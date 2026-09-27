from typing import Any

"""Security utilities for authentication and authorization."""


import hashlib
import logging
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError

from app.core.settings import settings

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


class PasswordManager:
    """Manages password hashing and verification."""

    @staticmethod
    def hash_password(password: str, rounds: int | None = None) -> str:
        """Hash a password using bcrypt."""
        rounds = rounds or settings.PASSWORD_BCRYPT_ROUNDS
        salt = bcrypt.gensalt(rounds=rounds)
        return bcrypt.hashpw(_encode_password(password), salt).decode("utf-8")

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """Verify a password against its hash."""
        try:
            return bcrypt.checkpw(
                _encode_password(password),
                password_hash.encode("utf-8"),
            )
        except (ValueError, TypeError):
            return False


class TokenManager:
    """Manages JWT token creation and verification."""

    @staticmethod
    def create_access_token(user_id: str, expires_delta: timedelta | None = None) -> str:
        """Create a new access token."""
        if expires_delta is None:
            expires_delta = timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)

        expires = datetime.now(UTC) + expires_delta
        payload = {"sub": str(user_id), "exp": expires, "iat": datetime.now(UTC), "type": "access"}

        jwt_secret = settings.JWT_SECRET_KEY
        if not jwt_secret:
            raise RuntimeError("JWT_SECRET_KEY is not configured")
        return jwt.encode(payload, jwt_secret, algorithm=settings.JWT_ALGORITHM)

    @staticmethod
    def verify_token(token: str, token_type: str = "access") -> dict[str, Any] | None:
        """Verify and decode a JWT token."""
        jwt_secret = settings.JWT_SECRET_KEY
        if not jwt_secret:
            raise RuntimeError("JWT_SECRET_KEY is not configured")
        try:
            payload = jwt.decode(
                token,
                jwt_secret,
                algorithms=[settings.JWT_ALGORITHM],
                audience=settings.JWT_AUDIENCE,
            )

            if payload.get("type") != token_type:
                logger.warning(f"Token type mismatch: expected {token_type}")
                return None

            return payload

        except ExpiredSignatureError:
            logger.debug("Token expired")
            return None
        except JWTError as e:
            logger.warning(f"Invalid token: {e}")
            return None

    @staticmethod
    def hash_token(token: str) -> str:
        """Hash a token for secure storage."""
        return hashlib.sha256(token.encode()).hexdigest()
