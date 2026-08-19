"""Repository layer for Auth Service."""

import logging
from datetime import UTC
from uuid import UUID

from app.models import LoginAudit, RefreshToken, TokenBlacklist, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class BaseRepository:
    """Base repository with common CRUD operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def commit(self) -> None:
        """Commit transaction."""
        await self.session.commit()

    async def rollback(self) -> None:
        """Rollback transaction."""
        await self.session.rollback()

    async def flush(self) -> None:
        """Flush pending changes."""
        await self.session.flush()


class UserRepository(BaseRepository):
    """Repository for User model operations."""

    async def create(self, email: str, password_hash: str, **kwargs) -> User:
        """Create new user."""
        try:
            user = User(email=email, password_hash=password_hash, **kwargs)
            self.session.add(user)
            await self.flush()
            logger.info(f"Created user: {email}", extra={"user_id": str(user.id)})
            return user
        except Exception as e:
            await self.rollback()
            logger.error(f"Error creating user: {e!s}")
            raise

    async def get_by_email(self, email: str) -> User | None:
        """Get user by email."""
        # Inactive (disabled/soft-deleted) accounts can never authenticate.
        from app.security import canonicalize_email
        canonical = canonicalize_email(email)
        stmt = select(User).where(User.email == canonical, User.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Get user by ID."""
        # Inactive accounts must not be resolvable for refresh/verification.
        stmt = select(User).where(User.id == user_id, User.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, user_id: UUID, **kwargs) -> User | None:
        """Update user."""
        try:
            user = await self.get_by_id(user_id)
            if not user:
                return None

            for key, value in kwargs.items():
                if hasattr(user, key):
                    setattr(user, key, value)

            await self.flush()
            logger.info(f"Updated user: {user_id}")
            return user
        except Exception as e:
            await self.rollback()
            logger.error(f"Error updating user: {e!s}")
            raise

    async def increment_login_attempts_atomic(self, user_id: UUID,
                                               max_attempts: int = 5,
                                               base_lockout_minutes: int = 15,
                                               max_lockout_hours: int = 24) -> tuple[User | None, bool]:
        """Atomically increment failed login attempts with exponential backoff (#131/#278).

        Single UPDATE with rowcount check to avoid race conditions.
        Returns (user, locked) where locked=True if account was newly locked.

        Args:
            user_id: User ID
            max_attempts: Maximum failed attempts before lockout
            base_lockout_minutes: Initial lockout duration in minutes (doubles each time)
            max_lockout_hours: Maximum lockout duration cap

        Returns:
            tuple: (User or None, locked_flag)
        """
        from sqlalchemy import update

        # First get current state
        user = await self.get_by_id(user_id)
        if user is None:
            return None, False

        new_attempts = user.login_attempts + 1
        locked = False
        locked_until = None

        if new_attempts >= max_attempts:
            locked = True
            # Exponential backoff: base * 2^(attempts - max_attempts)
            # Cap at max_lockout_hours
            exponent = new_attempts - max_attempts
            lockout_minutes = base_lockout_minutes * (2 ** exponent)
            max_minutes = max_lockout_hours * 60
            if lockout_minutes > max_minutes:
                lockout_minutes = max_minutes
            locked_until = datetime.now(UTC) + timedelta(minutes=lockout_minutes)

        # Atomic update with rowcount check
        stmt = (
            update(User)
            .where(User.id == user_id)
            .where(User.login_attempts == user.login_attempts)  # CAS
            .values(
                login_attempts=new_attempts,
                last_login_attempt_at=datetime.now(UTC),
                locked_until=locked_until if locked else User.locked_until,
            )
        )
        result = await self.session.execute(stmt)

        if result.rowcount == 0:
            # Race condition — retry once with fresh state
            return await self.increment_login_attempts_atomic(
                user_id, max_attempts, base_lockout_minutes, max_lockout_hours
            )

        # Refresh user
        await self.session.refresh(user)
        return user, locked

    async def reset_login_attempts(self, user_id: UUID) -> User:
        """Reset login attempts after successful login.

        Raises: ValueError: if the user no longer exists (race deletion).
        """
        user = await self.get_by_id(user_id)
        if user is None:
            raise ValueError(f"user {user_id} not found")
        user.login_attempts = 0
        user.locked_until = None
        user.last_login_attempt_at = None
        await self.flush()
        return user

    async def consume_email_verification_jti(self, user_id: UUID, jti: str) -> bool:
        """Atomically consume email verification JTI (#69/#140).

        Returns True if JTI was valid and consumed, False if already used or invalid.
        """
        from sqlalchemy import update

        jti_hash = hashlib.sha256(jti.encode()).hexdigest()

        stmt = (
            update(User)
            .where(User.id == user_id)
            .where(User.email_verification_token_jti == jti_hash)
            .values(email_verification_token_jti=None)
        )
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def increment_token_version(self, user_id: UUID) -> User | None:
        """Increment token_version to invalidate all existing access tokens (#79/#81).

        Called on password change, role change, email change.
        """
        from sqlalchemy import update

        stmt = (
            update(User)
            .where(User.id == user_id)
            .values(token_version=User.token_version + 1)
        )
        result = await self.session.execute(stmt)
        if result.rowcount > 0:
            user = await self.get_by_id(user_id)
            return user
        return None

class RefreshTokenRepository(BaseRepository):
    """Repository for RefreshToken model operations."""

    async def create(self, user_id: UUID, token_hash: str, expires_at,
                     family_id: str | None = None,
                     parent_token_hash: str | None = None) -> RefreshToken:
        """Create refresh token with optional family tracking (#183/#440).

        Args:
            user_id: User ID
            token_hash: Token hash
            expires_at: Expiration time
            family_id: Token family ID for rotation tracking
            parent_token_hash: Parent token hash for chain verification

        Returns:
            Created RefreshToken instance
        """
        try:
            token = RefreshToken(
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
                family_id=family_id,
                parent_token_hash=parent_token_hash,
            )
            self.session.add(token)
            await self.flush()
            logger.info(f"Created refresh token for user: {user_id}")
            return token
        except Exception as e:
            await self.rollback()
            logger.error(f"Error creating refresh token: {e!s}")
            raise

    async def get_by_token_hash(self, token_hash: str) -> RefreshToken | None:
        """Get refresh token by hash."""
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id: UUID) -> RefreshToken | None:
        """Get latest refresh token for user."""
        stmt = (
            select(RefreshToken)
            .where(RefreshToken.user_id == user_id)
            .order_by(RefreshToken.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    async def rotate_atomic(self, old_token_hash: str, new_token_hash: str,
                            expires_at, family_id: str | None = None) -> RefreshToken | None:
        """Atomically rotate refresh token using CAS (#183).

        Single UPDATE ... WHERE token_hash=old AND revoked_at IS NULL with rowcount check.
        Returns new token if successful, None if token already revoked/reused.

        Args:
            old_token_hash: Current token hash to rotate
            new_token_hash: New token hash
            expires_at: New token expiration
            family_id: Token family ID (generated if not provided)

        Returns:
            New RefreshToken instance if rotation succeeded, None if reuse detected
        """
        from sqlalchemy import update

        # First, get the old token to capture user_id and family_id
        old_token = await self.get_by_token_hash(old_token_hash)
        if not old_token or old_token.revoked_at is not None:
            # Token not found or already revoked — potential reuse
            if old_token and old_token.revoked_at is not None:
                # Reuse detected — revoke entire family (#183/#440)
                await self.revoke_family(old_token.family_id or "", old_token.user_id)
            return None

        # Generate family_id if not provided (preserve existing family)
        if family_id is None:
            family_id = old_token.family_id or secrets.token_urlsafe(32)

        # Atomic CAS: update only if token exists and not revoked
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.token_hash == old_token_hash)
            .where(RefreshToken.revoked_at.is_(None))
            .values(
                token_hash=new_token_hash,
                expires_at=expires_at,
                family_id=family_id,
                parent_token_hash=old_token_hash,
                revoked_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(stmt)

        if result.rowcount == 0:
            # Race condition — token was revoked between check and update
            # Check for reuse
            refreshed = await self.get_by_token_hash(old_token_hash)
            if refreshed and refreshed.revoked_at is not None:
                await self.revoke_family(refreshed.family_id or "", refreshed.user_id)
            return None

        # Create new token in same family
        new_token = RefreshToken(
            user_id=old_token.user_id,
            token_hash=new_token_hash,
            expires_at=expires_at,
            family_id=family_id,
            parent_token_hash=old_token_hash,
        )
        self.session.add(new_token)
        await self.flush()
        return new_token

    async def revoke_family(self, family_id: str, user_id: UUID) -> int:
        """Revoke all tokens in a family (reuse detection) (#183/#440).

        Args:
            family_id: Family ID to revoke
            user_id: User ID (for safety)

        Returns:
            Number of tokens revoked
        """
        from sqlalchemy import update

        if not family_id:
            return 0

        stmt = (
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id)
            .where(RefreshToken.user_id == user_id)
            .where(RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC), reuse_detected=True)
        )
        result = await self.session.execute(stmt)
        count = result.rowcount
        if count > 0:
            logger.warning(f"Revoked {count} tokens in family {family_id} for user {user_id} (reuse detected)")
        return count

    async def revoke(self, token_hash: str) -> bool:
        """Revoke refresh token (soft delete)."""
        try:
            token = await self.get_by_token_hash(token_hash)
            if token:
                token.revoked_at = datetime.now(UTC)
                await self.flush()
                logger.info("Revoked refresh token")
                return True
            return False
        except Exception as e:
            await self.rollback()
            logger.error(f"Error revoking token: {e!s}")
            raise

    async def delete_expired(self) -> int:
        """Delete expired refresh tokens."""
        from datetime import datetime

        try:
            stmt = select(RefreshToken).where(RefreshToken.expires_at < datetime.now(UTC))
            result = await self.session.execute(stmt)
            tokens = result.scalars().all()

            for token in tokens:
                await self.session.delete(token)

            await self.flush()
            logger.info(f"Deleted {len(tokens)} expired tokens")
            return len(tokens)
        except Exception as e:
            await self.rollback()
            logger.error(f"Error deleting expired tokens: {e!s}")
            raise

    async def revoke_all_for_user(self, user_id: UUID) -> int:
        """Revoke (soft delete) all refresh tokens for a user."""
        try:
            stmt = select(RefreshToken).where(RefreshToken.user_id == user_id)
            result = await self.session.execute(stmt)
            tokens = result.scalars().all()

            for token in tokens:
                token.revoked_at = datetime.now(UTC)

            await self.flush()
            logger.info(f"Revoked {len(tokens)} tokens for user: {user_id}")
            return len(tokens)
        except Exception as e:
            await self.rollback()
            logger.error(f"Error revoking tokens for user {user_id}: {e!s}")
            raise


class LoginAuditRepository(BaseRepository):
    """Repository for LoginAudit model operations."""

    async def create(self, user_id: UUID, status: str, ip_address: str) -> LoginAudit:
        """Create login audit record."""
        try:
            audit = LoginAudit(user_id=user_id, status=status, ip_address=ip_address)
            self.session.add(audit)
            await self.flush()
            return audit
        except Exception as e:
            await self.rollback()
            logger.error(f"Error creating audit: {e!s}")
            raise

    async def get_recent_failed_attempts(self, user_id: UUID, minutes: int = 5) -> int:
        """Get count of failed login attempts in last N minutes."""
        from datetime import datetime, timedelta

        cutoff = datetime.now(UTC) - timedelta(minutes=minutes)

        stmt = select(LoginAudit).where(
            (LoginAudit.user_id == user_id)
            & (LoginAudit.status == "failed")
            & (LoginAudit.created_at > cutoff)
        )
        result = await self.session.execute(stmt)
        audits = result.scalars().all()
        return len(audits)


class TokenBlacklistRepository(BaseRepository):
    """Repository for TokenBlacklist model operations."""

    async def create(self, token_hash: str, user_id: UUID, expires_at) -> TokenBlacklist:
        """Add token to blacklist."""
        try:
            entry = TokenBlacklist(token_hash=token_hash, user_id=user_id, expires_at=expires_at)
            self.session.add(entry)
            await self.flush()
            logger.info(f"Blacklisted token for user: {user_id}")
            return entry
        except Exception as e:
            await self.rollback()
            logger.error(f"Error blacklisting token: {e!s}")
            raise

    async def is_blacklisted(self, token_hash: str) -> bool:
        """Check if token is blacklisted."""
        stmt = select(TokenBlacklist).where(TokenBlacklist.token_hash == token_hash)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def delete_expired(self) -> int:
        """Delete expired blacklist entries."""
        from datetime import datetime

        try:
            stmt = select(TokenBlacklist).where(TokenBlacklist.expires_at < datetime.now(UTC))
            result = await self.session.execute(stmt)
            entries = result.scalars().all()

            for entry in entries:
                await self.session.delete(entry)

            await self.flush()
            logger.info(f"Deleted {len(entries)} expired blacklist entries")
            return len(entries)
        except Exception as e:
            await self.rollback()
            logger.error(f"Error deleting expired blacklist entries: {e!s}")
            raise
