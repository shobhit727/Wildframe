from typing import Any

"""Security utilities for authentication and authorization."""


import hashlib
import logging
from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext

from app.core.settings import settings

logger = logging.getLogger(__name__)

# Password hashing context using bcrypt
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class PasswordManager:
    """Manages password hashing and verification."""
    
    @staticmethod
    def hash_password(password: str, rounds: int | None = None) -> str:
        """Hash a password using bcrypt."""
        rounds = rounds or settings.PASSWORD_BCRYPT_ROUNDS
        return pwd_context.hash(password, rounds=rounds)
    
    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """Verify a password against its hash."""
        return pwd_context.verify(password, password_hash)


class TokenManager:
    """Manages JWT token creation and verification."""
    
    @staticmethod
    def create_access_token(user_id: str, expires_delta: timedelta | None = None) -> str:
        """Create a new access token."""
        if expires_delta is None:
            expires_delta = timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
        
        expires = datetime.now(UTC) + expires_delta
        payload = {
            "sub": str(user_id),
            "exp": expires,
            "iat": datetime.now(UTC),
            "type": "access"
        }
        
        return jwt.encode(
            payload,
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM
        )
    
    @staticmethod
    def verify_token(token: str, token_type: str = "access") -> dict[str, Any] | None:
        """Verify and decode a JWT token."""
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM]
            )
            
            if payload.get("type") != token_type:
                logger.warning(f"Token type mismatch: expected {token_type}")
                return None
            
            return payload
            
        except jwt.ExpiredSignatureError:
            logger.debug("Token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            return None
    
    @staticmethod
    def hash_token(token: str) -> str:
        """Hash a token for secure storage."""
        return hashlib.sha256(token.encode()).hexdigest()
