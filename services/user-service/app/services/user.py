"""User service business logic."""

import logging
from uuid import UUID
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserProfile, Device, UserSession, WatchHistory, UserPreference
from app.repositories.user import (
    UserProfileRepository,
    DeviceRepository,
    UserSessionRepository,
    WatchHistoryRepository,
    UserPreferenceRepository
)
from app.security.manager import TokenManager

logger = logging.getLogger(__name__)


class UserService:
    """Service for user profile and session management."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.profile_repo = UserProfileRepository(db)
        self.device_repo = DeviceRepository(db)
        self.session_repo = UserSessionRepository(db)
        self.history_repo = WatchHistoryRepository(db)
        self.preference_repo = UserPreferenceRepository(db)
    
    # Profile Management
    async def create_profile(self, user_id: UUID) -> UserProfile:
        """Create new user profile."""
        profile = await self.profile_repo.create(user_id)
        logger.info(f"Created profile for user {user_id}")
        return profile
    
    async def get_profile(self, user_id: UUID) -> Optional[UserProfile]:
        """Get user profile."""
        profile = await self.profile_repo.get_by_user_id(user_id)
        if not profile:
            raise ValueError(f"Profile not found for user {user_id}")
        return profile
    
    async def update_profile(self, user_id: UUID, profile_data: dict) -> UserProfile:
        """Update user profile."""
        profile = await self.get_profile(user_id)
        profile = await self.profile_repo.update(profile, **profile_data)
        logger.info(f"Updated profile for user {user_id}")
        return profile
    
    # Device Management
    async def register_device(self, user_id: UUID, device_data: dict) -> Device:
        """Register new device."""
        device = await self.device_repo.create(user_id, **device_data)
        logger.info(f"Registered device {device.device_id} for user {user_id}")
        return device
    
    async def list_devices(self, user_id: UUID, active_only: bool = True) -> List[Device]:
        """List user devices."""
        return await self.device_repo.list_by_user(user_id, active_only)
    
    async def deactivate_device(self, device_id: UUID, user_id: UUID) -> None:
        """Deactivate device."""
        device = await self.device_repo.get_by_id(device_id)
        if not device or device.user_id != user_id:
            raise ValueError("Device not found")
        
        await self.device_repo.deactivate(device_id)
        logger.info(f"Deactivated device {device_id} for user {user_id}")
    
    # Session Management
    async def create_session(self, user_id: UUID, device_id: UUID, 
                            session_token: str, ip_address: str,
                            user_agent: str) -> UserSession:
        """Create new user session."""
        token_hash = TokenManager.hash_token(session_token)
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        
        session = await self.session_repo.create(
            user_id=user_id,
            device_id=device_id,
            session_token_hash=token_hash,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=expires_at
        )
        
        logger.info(f"Created session for user {user_id} on device {device_id}")
        return session
    
    async def get_active_sessions(self, user_id: UUID) -> List[UserSession]:
        """Get all active sessions for user."""
        return await self.session_repo.list_active_sessions(user_id)
    
    async def end_session(self, user_id: UUID, session_id: UUID) -> None:
        """End a session."""
        session = await self.session_repo.get_by_id(session_id)
        if not session or session.user_id != user_id:
            raise ValueError("Session not found")
        
        await self.session_repo.end_session(session_id)
        logger.info(f"Ended session {session_id} for user {user_id}")
    
    async def end_all_sessions(self, user_id: UUID, except_session_id: Optional[UUID] = None) -> None:
        """End all sessions for user."""
        await self.session_repo.end_all_sessions(user_id, except_session_id)
        logger.info(f"Ended all sessions for user {user_id}")
    
    async def update_session_activity(self, session_id: UUID) -> None:
        """Update session last activity."""
        await self.session_repo.update_activity(session_id)
    
    # Watch History
    async def record_watch(self, user_id: UUID, content_id: UUID,
                          content_type: str, progress_seconds: int,
                          progress_percentage: int, is_completed: bool = False) -> WatchHistory:
        """Record watch history."""
        entry = await self.history_repo.record_watch(
            user_id=user_id,
            content_id=content_id,
            content_type=content_type,
            progress_seconds=progress_seconds,
            progress_percentage=progress_percentage,
            is_completed=is_completed,
            duration_seconds=progress_seconds if is_completed else 0
        )
        
        logger.info(f"Recorded watch history for user {user_id}, content {content_id}")
        return entry
    
    async def get_watch_history(self, user_id: UUID, limit: int = 50, 
                               offset: int = 0) -> Tuple[List[WatchHistory], int]:
        """Get user watch history."""
        return await self.history_repo.get_watch_history(user_id, limit, offset)
    
    # Preferences
    async def get_preferences(self, user_id: UUID) -> UserPreference:
        """Get user preferences."""
        return await self.preference_repo.get_or_create(user_id)
    
    async def update_preferences(self, user_id: UUID, pref_data: dict) -> UserPreference:
        """Update user preferences."""
        prefs = await self.preference_repo.update(user_id, **pref_data)
        logger.info(f"Updated preferences for user {user_id}")
        return prefs
