"""Repository layer for Auth Service."""
from typing import Optional
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User, RefreshToken, LoginAudit
from app.schemas import UserResponse
import logging

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
            logger.error(f"Error creating user: {str(e)}")
            raise

    async def get_by_email(self, email: str) -> Optional[User]:
        """Get user by email."""
        stmt = select(User).where(User.email == email)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: UUID) -> Optional[User]:
        """Get user by ID."""
        return await self.session.get(User, user_id)

    async def update(self, user_id: UUID, **kwargs) -> Optional[User]:
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
            logger.error(f"Error updating user: {str(e)}")
            raise

    async def increment_login_attempts(self, user_id: UUID) -> User:
        """Increment failed login attempts."""
        user = await self.get_by_id(user_id)
        if user:
            user.login_attempts += 1
            await self.flush()
        return user

    async def reset_login_attempts(self, user_id: UUID) -> User:
        """Reset login attempts after successful login."""
        user = await self.get_by_id(user_id)
        if user:
            user.login_attempts = 0
            user.locked_until = None
            await self.flush()
        return user


class RefreshTokenRepository(BaseRepository):
    """Repository for RefreshToken model operations."""

    async def create(self, user_id: UUID, token_hash: str, expires_at) -> RefreshToken:
        """Create refresh token."""
        try:
            token = RefreshToken(
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at
            )
            self.session.add(token)
            await self.flush()
            logger.info(f"Created refresh token for user: {user_id}")
            return token
        except Exception as e:
            await self.rollback()
            logger.error(f"Error creating refresh token: {str(e)}")
            raise

    async def get_by_token_hash(self, token_hash: str) -> Optional[RefreshToken]:
        """Get refresh token by hash."""
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id: UUID) -> Optional[RefreshToken]:
        """Get latest refresh token for user."""
        stmt = select(RefreshToken).where(
            RefreshToken.user_id == user_id
        ).order_by(RefreshToken.created_at.desc())
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def revoke(self, token_hash: str) -> bool:
        """Revoke refresh token."""
        try:
            token = await self.get_by_token_hash(token_hash)
            if token:
                await self.session.delete(token)
                await self.flush()
                logger.info("Revoked refresh token")
                return True
            return False
        except Exception as e:
            await self.rollback()
            logger.error(f"Error revoking token: {str(e)}")
            raise

    async def delete_expired(self) -> int:
        """Delete expired refresh tokens."""
        from datetime import datetime
        try:
            stmt = select(RefreshToken).where(
                RefreshToken.expires_at < datetime.utcnow()
            )
            result = await self.session.execute(stmt)
            tokens = result.scalars().all()
            
            for token in tokens:
                await self.session.delete(token)
            
            await self.flush()
            logger.info(f"Deleted {len(tokens)} expired tokens")
            return len(tokens)
        except Exception as e:
            await self.rollback()
            logger.error(f"Error deleting expired tokens: {str(e)}")
            raise


class LoginAuditRepository(BaseRepository):
    """Repository for LoginAudit model operations."""

    async def create(self, user_id: UUID, status: str, ip_address: str) -> LoginAudit:
        """Create login audit record."""
        try:
            audit = LoginAudit(
                user_id=user_id,
                status=status,
                ip_address=ip_address
            )
            self.session.add(audit)
            await self.flush()
            return audit
        except Exception as e:
            await self.rollback()
            logger.error(f"Error creating audit: {str(e)}")
            raise

    async def get_recent_failed_attempts(
        self, 
        user_id: UUID, 
        minutes: int = 5
    ) -> int:
        """Get count of failed login attempts in last N minutes."""
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(minutes=minutes)
        
        stmt = select(LoginAudit).where(
            (LoginAudit.user_id == user_id) &
            (LoginAudit.status == "failed") &
            (LoginAudit.created_at > cutoff)
        )
        result = await self.session.execute(stmt)
        audits = result.scalars().all()
        return len(audits)
