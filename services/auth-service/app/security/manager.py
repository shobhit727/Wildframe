"""
Security utilities for authentication and authorization.
Includes JWT handling, password management, and rate limiting.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from functools import lru_cache
import hashlib
import hmac
import logging

import jwt
import redis.asyncio as redis
from passlib.context import CryptContext
from app.core.settings import settings

logger = logging.getLogger(__name__)

# Password hashing context using bcrypt
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class PasswordManager:
    """Manages password hashing and verification."""
    
    @staticmethod
    def hash_password(password: str, rounds: Optional[int] = None) -> str:
        """Hash a password using bcrypt.
        
        Args:
            password: Raw password to hash
            rounds: Bcrypt rounds (cost factor)
            
        Returns:
            Hashed password
        """
        rounds = rounds or settings.PASSWORD_BCRYPT_ROUNDS
        return pwd_context.hash(password, rounds=rounds)
    
    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """Verify a password against its hash.
        
        Uses constant-time comparison to prevent timing attacks.
        
        Args:
            password: Raw password to verify
            password_hash: Stored password hash
            
        Returns:
            True if password matches, False otherwise
        """
        return pwd_context.verify(password, password_hash)


class TokenManager:
    """Manages JWT token creation and verification."""
    
    @staticmethod
    def create_access_token(user_id: str, expires_delta: Optional[timedelta] = None) -> str:
        """Create a new access token.
        
        Args:
            user_id: User ID for token claim
            expires_delta: Custom expiration time
            
        Returns:
            JWT access token
        """
        if expires_delta is None:
            expires_delta = timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
        
        expires = datetime.now(timezone.utc) + expires_delta
        payload = {
            "sub": str(user_id),
            "exp": expires,
            "iat": datetime.now(timezone.utc),
            "type": "access"
        }
        
        return jwt.encode(
            payload,
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM
        )
    
    @staticmethod
    def create_refresh_token(user_id: str, device_id: Optional[str] = None) -> str:
        """Create a new refresh token.
        
        Refresh tokens are longer-lived and used to obtain new access tokens.
        
        Args:
            user_id: User ID for token claim
            device_id: Optional device identifier
            
        Returns:
            JWT refresh token
        """
        expires = datetime.now(timezone.utc) + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRATION_DAYS
        )
        payload = {
            "sub": str(user_id),
            "exp": expires,
            "iat": datetime.now(timezone.utc),
            "type": "refresh",
            "device_id": device_id
        }
        
        return jwt.encode(
            payload,
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM
        )
    
    @staticmethod
    def verify_token(token: str, token_type: str = "access") -> Optional[Dict[str, Any]]:
        """Verify and decode a JWT token.
        
        Args:
            token: JWT token to verify
            token_type: Expected token type ('access' or 'refresh')
            
        Returns:
            Decoded token payload or None if invalid
        """
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM]
            )
            
            # Verify token type matches
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
        """Hash a token for secure storage.
        
        Args:
            token: Token to hash
            
        Returns:
            Token hash
        """
        return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    """Redis-backed rate limiting using sliding window algorithm."""
    
    def __init__(self, client: redis.Redis | None = None):
        """Initialize rate limiter.

        Args:
            client: An optional pre-built async Redis client. When omitted, a
                client is lazily created from ``settings.REDIS_URL``. Accepting
                an injected client keeps the limiter testable and avoids
                opening multiple connections per worker.
        """
        self._redis = client
        self._owns_client = client is None

    async def get_redis(self) -> redis.Redis:
        """Get or create an async Redis connection.

        Uses the official ``redis.asyncio`` client (the ``redis`` package
        already declares it — do **not** fall back to the unmaintained
        ``aioredis`` fork, which is not a project dependency).

        Returns:
            Redis client instance
        """
        if self._redis is None:
            self._redis = await redis.asyncio.from_url(settings.REDIS_URL)
        return self._redis
    
    async def is_allowed(
        self,
        key: str,
        max_attempts: int,
        window_seconds: int
    ) -> bool:
        """Check if action is allowed under rate limit.
        
        Uses sliding window algorithm:
        - Tracks attempts in a Redis key with TTL
        - Returns True if under limit, False otherwise
        
        Args:
            key: Rate limit key (e.g., "login:email@example.com")
            max_attempts: Maximum attempts allowed
            window_seconds: Time window in seconds
            
        Returns:
            True if allowed, False if rate limited
        """
        redis = await self.get_redis()
        
        try:
            current = await redis.incr(key)
            
            # Set expiration on first attempt
            if current == 1:
                await redis.expire(key, window_seconds)
            
            return current <= max_attempts
            
        except Exception as e:
            logger.error(f"Rate limiter error: {e}")
            # Fail open if Redis is unavailable
            return True
    
    async def get_remaining(self, key: str, max_attempts: int) -> int:
        """Get remaining attempts for rate limited action.
        
        Args:
            key: Rate limit key
            max_attempts: Maximum attempts allowed
            
        Returns:
            Number of remaining attempts
        """
        redis = await self.get_redis()
        
        try:
            current = await redis.get(key)
            if current is None:
                return max_attempts
            return max(0, max_attempts - int(current))
        except Exception as e:
            logger.error(f"Error getting rate limit status: {e}")
            return max_attempts
    
    async def reset(self, key: str) -> None:
        """Reset rate limit for a key.
        
        Args:
            key: Rate limit key to reset
        """
        redis = await self.get_redis()
        try:
            await redis.delete(key)
        except Exception as e:
            logger.error(f"Error resetting rate limit: {e}")
    
    async def close(self) -> None:
        """Close the Redis connection if this limiter created it."""
        if self._redis and self._owns_client:
            await self._redis.close()
            self._redis = None
