"""
Repository layer for data access operations.
Uses SQLAlchemy 2.0 async patterns with proper error handling.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.models import LoginAudit, RefreshToken, TokenBlacklist, User
from app.security import PasswordManager, TokenManager
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class UserRepository:
    """Repository for user data access operations."""

    def __init__(self, db: AsyncSession):
        """Initialize repository with database session.

        Args:
            db: Async SQLAlchemy session
        """
        self.db = db

    async def create(self, email: str, password: str) -> User:
        """Create a new user.

        Args:
            email: User email
            password: Raw password (will be hashed)

        Returns:
            Created User instance

        Raises:
            IntegrityError: If email already exists
        """
        password_hash = PasswordManager.hash_password(password)

        user = User(email=email, password_hash=password_hash, is_active=True, login_attempts=0)

        self.db.add(user)

        try:
            await self.db.flush()
            logger.info(f"User created: {email}")
            return user
        except IntegrityError:
            await self.db.rollback()
            logger.warning(f"Email already exists: {email}")
            raise ValueError(f"Email {email} already registered")

    async def get_by_email(self, email: str) -> User | None:
        """Get user by email.

        Args:
            email: User email

        Returns:
            User instance or None if not found
        """
        stmt = select(User).where(User.email == email)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Get user by ID.

        Args:
            user_id: User ID

        Returns:
            User instance or None if not found
        """
        stmt = select(User).where(User.id == user_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def update_login_attempt(self, user: User) -> None:
        """Update user login attempt count.

        Args:
            user: User to update
        """
        user.login_attempts += 1
        user.last_login_attempt_at = datetime.now(UTC)
        await self.db.flush()

    async def reset_login_attempts(self, user: User) -> None:
        """Reset login attempt count after successful login.

        Args:
            user: User to reset
        """
        user.login_attempts = 0
        user.last_login_attempt_at = None
        user.last_login_at = datetime.now(UTC)
        await self.db.flush()

    async def lock_account(self, user: User, hours: int = 1) -> None:
        """Lock user account after too many failed attempts.

        Args:
            user: User to lock
            hours: Lock duration in hours
        """
        from datetime import datetime, timedelta

        user.is_locked = True
        user.locked_until = datetime.now(UTC) + timedelta(hours=hours)
        await self.db.flush()

    async def unlock_account(self, user: User) -> None:
        """Unlock user account.

        Args:
            user: User to unlock
        """
        user.is_locked = False
        user.locked_until = None
        await self.db.flush()

    async def verify_email(self, user: User) -> None:
        """Mark user email as verified.

        Args:
            user: User to verify
        """
        from datetime import datetime

        user.email_verified = True
        user.email_verified_at = datetime.now(UTC)
        await self.db.flush()


class RefreshTokenRepository:
    """Repository for refresh token operations."""

    def __init__(self, db: AsyncSession):
        """Initialize repository.

        Args:
            db: Async SQLAlchemy session
        """
        self.db = db

    async def create(
        self,
        user_id: UUID,
        token: str,
        device_id: str | None = None,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> RefreshToken:
        """Create a new refresh token.

        Args:
            user_id: User ID
            token: Raw token
            device_id: Device identifier
            user_agent: User agent string
            ip_address: Client IP address

        Returns:
            Created RefreshToken instance
        """
        token_hash = TokenManager.hash_token(token)

        refresh_token = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=datetime.now(UTC) + timedelta(days=7),
            device_id=device_id,
            user_agent=user_agent,
            ip_address=ip_address,
        )

        self.db.add(refresh_token)
        await self.db.flush()
        logger.info(f"Refresh token created for user: {user_id}")
        return refresh_token

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Get refresh token by hash.

        Args:
            token_hash: Token hash

        Returns:
            RefreshToken instance or None
        """
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def revoke(self, refresh_token: RefreshToken) -> None:
        """Revoke a refresh token.

        Args:
            refresh_token: Token to revoke
        """
        from datetime import datetime

        refresh_token.revoked_at = datetime.now(UTC)
        await self.db.flush()
        logger.info(f"Refresh token revoked: {refresh_token.id}")

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        """Revoke all refresh tokens for a user.

        Args:
            user_id: User ID
        """
        from datetime import datetime

        stmt = select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
        result = await self.db.execute(stmt)
        tokens = result.scalars().all()

        for token in tokens:
            token.revoked_at = datetime.now(UTC)

        await self.db.flush()
        logger.info(f"All refresh tokens revoked for user: {user_id}")


class TokenBlacklistRepository:
    """Repository for token blacklist operations."""

    def __init__(self, db: AsyncSession):
        """Initialize repository.

        Args:
            db: Async SQLAlchemy session
        """
        self.db = db

    async def add(
        self, token: str, user_id: UUID, expires_at, reason: str | None = None
    ) -> TokenBlacklist:
        """Add token to blacklist.

        Args:
            token: Token to blacklist
            user_id: User ID
            expires_at: Token expiration time
            reason: Revocation reason

        Returns:
            Created TokenBlacklist instance
        """
        token_hash = TokenManager.hash_token(token)

        blacklist_entry = TokenBlacklist(
            token_hash=token_hash, user_id=user_id, expires_at=expires_at, reason=reason
        )

        self.db.add(blacklist_entry)
        await self.db.flush()
        logger.info(f"Token blacklisted for user: {user_id}")
        return blacklist_entry

    async def is_blacklisted(self, token: str) -> bool:
        """Check if token is blacklisted.

        Args:
            token: Token to check

        Returns:
            True if blacklisted, False otherwise
        """
        token_hash = TokenManager.hash_token(token)
        stmt = select(TokenBlacklist).where(TokenBlacklist.token_hash == token_hash)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None


class LoginAuditRepository:
    """Repository for login audit operations."""

    def __init__(self, db: AsyncSession):
        """Initialize repository.

        Args:
            db: Async SQLAlchemy session
        """
        self.db = db

    async def log(
        self,
        email: str,
        status: str,
        user_id: UUID | None = None,
        reason: str | None = None,
        user_agent: str | None = None,
        ip_address: str | None = None,
        device_id: str | None = None,
    ) -> LoginAudit:
        """Log a login attempt.

        Args:
            email: Email used in login attempt
            status: Login status (success, failed_password, user_not_found, etc.)
            user_id: User ID (if found)
            reason: Failure reason
            user_agent: User agent string
            ip_address: Client IP address
            device_id: Device identifier

        Returns:
            Created LoginAudit instance
        """
        audit_entry = LoginAudit(
            user_id=user_id,
            email=email,
            status=status,
            reason=reason,
            user_agent=user_agent,
            ip_address=ip_address,
            device_id=device_id,
        )

        self.db.add(audit_entry)
        await self.db.flush()
        logger.info(f"Login attempt logged for {email}: {status}")
        return audit_entry
