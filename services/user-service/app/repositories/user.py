"""User service repository layer for data access."""

import logging
from uuid import UUID
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from sqlalchemy import select, and_, desc, asc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserProfile, Device, UserSession, WatchHistory, UserPreference

logger = logging.getLogger(__name__)


class UserProfileRepository:
    """Repository for user profile operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def create(self, user_id: UUID) -> UserProfile:
        """Create new user profile."""
        profile = UserProfile(user_id=user_id)
        self.session.add(profile)
        await self.session.flush()
        return profile
    
    async def get_by_user_id(self, user_id: UUID) -> Optional[UserProfile]:
        """Get profile by user ID."""
        stmt = select(UserProfile).where(
            and_(
                UserProfile.user_id == user_id,
                UserProfile.deleted_at.is_(None)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def update(self, profile: UserProfile, **kwargs) -> UserProfile:
        """Update profile with new data."""
        for key, value in kwargs.items():
            if hasattr(profile, key) and value is not None:
                setattr(profile, key, value)
        profile.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return profile
    
    async def delete(self, user_id: UUID) -> None:
        """Soft delete profile."""
        profile = await self.get_by_user_id(user_id)
        if profile:
            profile.deleted_at = datetime.now(timezone.utc)
            await self.session.flush()


class DeviceRepository:
    """Repository for device management."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def create(self, user_id: UUID, **device_data) -> Device:
        """Register new device."""
        device = Device(user_id=user_id, **device_data)
        self.session.add(device)
        await self.session.flush()
        return device
    
    async def get_by_id(self, device_id: UUID) -> Optional[Device]:
        """Get device by ID."""
        stmt = select(Device).where(
            and_(
                Device.id == device_id,
                Device.deleted_at.is_(None)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_by_device_id(self, device_id: str) -> Optional[Device]:
        """Get device by device ID."""
        stmt = select(Device).where(
            and_(
                Device.device_id == device_id,
                Device.deleted_at.is_(None)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def list_by_user(self, user_id: UUID, active_only: bool = True) -> List[Device]:
        """List devices for user."""
        stmt = select(Device).where(Device.user_id == user_id)
        if active_only:
            stmt = stmt.where(Device.is_active.is_(True))
        stmt = stmt.where(Device.deleted_at.is_(None))
        stmt = stmt.order_by(desc(Device.last_seen_at))
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def update_last_seen(self, device_id: UUID) -> None:
        """Update device last seen timestamp."""
        device = await self.get_by_id(device_id)
        if device:
            device.last_seen_at = datetime.now(timezone.utc)
            await self.session.flush()
    
    async def deactivate(self, device_id: UUID) -> None:
        """Deactivate device."""
        device = await self.get_by_id(device_id)
        if device:
            device.is_active = False
            await self.session.flush()


class UserSessionRepository:
    """Repository for user session management."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def create(self, user_id: UUID, device_id: UUID, **session_data) -> UserSession:
        """Create new session."""
        session = UserSession(user_id=user_id, device_id=device_id, **session_data)
        self.session.add(session)
        await self.session.flush()
        return session
    
    async def get_by_id(self, session_id: UUID) -> Optional[UserSession]:
        """Get session by ID."""
        stmt = select(UserSession).where(UserSession.id == session_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_by_token_hash(self, token_hash: str) -> Optional[UserSession]:
        """Get session by token hash."""
        stmt = select(UserSession).where(
            and_(
                UserSession.session_token_hash == token_hash,
                UserSession.is_active.is_(True)
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def list_active_sessions(self, user_id: UUID) -> List[UserSession]:
        """List active sessions for user."""
        stmt = select(UserSession).where(
            and_(
                UserSession.user_id == user_id,
                UserSession.is_active.is_(True),
                UserSession.expires_at > datetime.now(timezone.utc)
            )
        )
        stmt = stmt.order_by(desc(UserSession.last_activity_at))
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def update_activity(self, session_id: UUID) -> None:
        """Update session last activity."""
        session = await self.get_by_id(session_id)
        if session:
            session.last_activity_at = datetime.now(timezone.utc)
            await self.session.flush()
    
    async def end_session(self, session_id: UUID, reason: str = "logout") -> None:
        """End a session."""
        session = await self.get_by_id(session_id)
        if session:
            session.is_active = False
            session.ended_at = datetime.now(timezone.utc)
            session.end_reason = reason
            await self.session.flush()
    
    async def end_all_sessions(self, user_id: UUID, except_session_id: Optional[UUID] = None) -> None:
        """End all sessions for user except one."""
        stmt = select(UserSession).where(
            and_(
                UserSession.user_id == user_id,
                UserSession.is_active.is_(True)
            )
        )
        if except_session_id:
            stmt = stmt.where(UserSession.id != except_session_id)
        
        result = await self.session.execute(stmt)
        sessions = result.scalars().all()
        
        for session in sessions:
            session.is_active = False
            session.ended_at = datetime.now(timezone.utc)
            session.end_reason = "user_logout_all_devices"
        
        await self.session.flush()


class WatchHistoryRepository:
    """Repository for watch history tracking."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def record_watch(self, user_id: UUID, content_id: UUID, 
                          content_type: str, **data) -> WatchHistory:
        """Record or update watch history."""
        stmt = select(WatchHistory).where(
            and_(
                WatchHistory.user_id == user_id,
                WatchHistory.content_id == content_id
            )
        )
        result = await self.session.execute(stmt)
        entry = result.scalar_one_or_none()
        
        if entry:
            entry.progress_seconds = data.get('progress_seconds', entry.progress_seconds)
            entry.progress_percentage = data.get('progress_percentage', entry.progress_percentage)
            entry.is_completed = data.get('is_completed', entry.is_completed)
            entry.watch_count += 1
            entry.last_watched_at = datetime.now(timezone.utc)
            entry.updated_at = datetime.now(timezone.utc)
        else:
            entry = WatchHistory(
                user_id=user_id,
                content_id=content_id,
                content_type=content_type,
                **data
            )
            self.session.add(entry)
        
        await self.session.flush()
        return entry
    
    async def get_watch_history(self, user_id: UUID, limit: int = 50, 
                               offset: int = 0) -> tuple[List[WatchHistory], int]:
        """Get user watch history with pagination."""
        # Count total
        count_stmt = select(func.count()).select_from(WatchHistory).where(
            WatchHistory.user_id == user_id
        )
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar() or 0
        
        # Get paginated results
        stmt = select(WatchHistory).where(
            WatchHistory.user_id == user_id
        ).order_by(desc(WatchHistory.last_watched_at))
        stmt = stmt.limit(limit).offset(offset)
        
        result = await self.session.execute(stmt)
        entries = result.scalars().all()
        
        return entries, total


class UserPreferenceRepository:
    """Repository for user preferences."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def get_or_create(self, user_id: UUID) -> UserPreference:
        """Get or create user preferences."""
        stmt = select(UserPreference).where(UserPreference.user_id == user_id)
        result = await self.session.execute(stmt)
        pref = result.scalar_one_or_none()
        
        if not pref:
            pref = UserPreference(user_id=user_id)
            self.session.add(pref)
            await self.session.flush()
        
        return pref
    
    async def update(self, user_id: UUID, **kwargs) -> UserPreference:
        """Update user preferences."""
        pref = await self.get_or_create(user_id)
        
        for key, value in kwargs.items():
            if hasattr(pref, key) and value is not None:
                setattr(pref, key, value)
        
        pref.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return pref
