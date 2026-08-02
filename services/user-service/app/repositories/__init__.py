"""Repository layer for User Service."""
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserDevice, UserPreference, UserProfile, UserSubscriptionProfile

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


class UserProfileRepository(BaseRepository):
    """Repository for UserProfile model."""

    async def create(self, user_id: UUID, **kwargs) -> UserProfile:
        """Create user profile."""
        try:
            profile = UserProfile(user_id=user_id, **kwargs)
            self.session.add(profile)
            await self.flush()
            logger.info(f"Created user profile: {user_id}")
            return profile
        except IntegrityError as e:
            await self.rollback()
            logger.error(f"Error creating user profile: {e!s}")
            raise

    async def get_by_user_id(self, user_id: UUID) -> UserProfile | None:
        """Get profile by user ID."""
        stmt = select(UserProfile).where(UserProfile.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, profile_id: UUID) -> UserProfile | None:
        """Get profile by ID."""
        return await self.session.get(UserProfile, profile_id)

    async def update(self, user_id: UUID, **kwargs) -> UserProfile | None:
        """Update user profile."""
        try:
            profile = await self.get_by_user_id(user_id)
            if not profile:
                return None

            for key, value in kwargs.items():
                if hasattr(profile, key) and value is not None:
                    setattr(profile, key, value)

            await self.flush()
            logger.info(f"Updated user profile: {user_id}")
            return profile
        except Exception as e:
            await self.rollback()
            logger.error(f"Error updating profile: {e!s}")
            raise

    async def mark_onboarding_complete(self, user_id: UUID) -> UserProfile | None:
        """Mark onboarding as complete and update profile completeness."""
        return await self.update(
            user_id,
            completed_onboarding=True,
            profile_completeness=100,
        )


class UserDeviceRepository(BaseRepository):
    """Repository for UserDevice model."""

    async def create(
        self,
        user_id: UUID,
        device_id: str,
        device_name: str,
        device_type: str,
        **kwargs
    ) -> UserDevice:
        """Create device."""
        try:
            device = UserDevice(
                user_id=user_id,
                device_id=device_id,
                device_name=device_name,
                device_type=device_type,
                **kwargs
            )
            self.session.add(device)
            await self.flush()
            logger.info(f"Registered device: {device_id} for user {user_id}")
            return device
        except IntegrityError:
            await self.rollback()
            logger.error(f"Device already exists: {device_id}")
            raise

    async def get_by_device_id(self, device_id: str) -> UserDevice | None:
        """Get device by device ID."""
        stmt = select(UserDevice).where(UserDevice.device_id == device_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, device_id: UUID) -> UserDevice | None:
        """Get device by database ID."""
        return await self.session.get(UserDevice, device_id)

    async def get_user_devices(self, user_id: UUID, active_only: bool = True) -> list[UserDevice]:
        """Get all devices for a user."""
        stmt = select(UserDevice).where(UserDevice.user_id == user_id)
        if active_only:
            stmt = stmt.where(UserDevice.is_active == True)
        stmt = stmt.order_by(UserDevice.last_active_at.desc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update(self, device_id: UUID, **kwargs) -> UserDevice | None:
        """Update device."""
        try:
            device = await self.get_by_id(device_id)
            if not device:
                return None

            for key, value in kwargs.items():
                if hasattr(device, key) and value is not None:
                    setattr(device, key, value)

            # Update last_active_at if updating general device status
            if any(k in kwargs for k in ["is_active", "ip_address"]):
                device.last_active_at = datetime.now(UTC)

            await self.flush()
            logger.info(f"Updated device: {device_id}")
            return device
        except Exception as e:
            await self.rollback()
            logger.error(f"Error updating device: {e!s}")
            raise

    async def mark_device_inactive(self, device_id: UUID) -> UserDevice | None:
        """Mark device as inactive."""
        return await self.update(device_id, is_active=False)

    async def delete(self, device_id: UUID) -> bool:
        """Delete device."""
        try:
            device = await self.get_by_id(device_id)
            if device:
                await self.session.delete(device)
                await self.flush()
                logger.info(f"Deleted device: {device_id}")
                return True
            return False
        except Exception as e:
            await self.rollback()
            logger.error(f"Error deleting device: {e!s}")
            raise


class UserPreferenceRepository(BaseRepository):
    """Repository for UserPreference model."""

    async def create_default(self, user_id: UUID) -> UserPreference:
        """Create default preferences for user."""
        try:
            preference = UserPreference(user_id=user_id)
            self.session.add(preference)
            await self.flush()
            logger.info(f"Created default preferences for user: {user_id}")
            return preference
        except IntegrityError:
            await self.rollback()
            logger.error(f"Preferences already exist for user: {user_id}")
            raise

    async def get_by_user_id(self, user_id: UUID) -> UserPreference | None:
        """Get preferences by user ID."""
        stmt = select(UserPreference).where(UserPreference.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, user_id: UUID, **kwargs) -> UserPreference | None:
        """Update user preferences."""
        try:
            preference = await self.get_by_user_id(user_id)
            if not preference:
                return None

            for key, value in kwargs.items():
                if hasattr(preference, key) and value is not None:
                    setattr(preference, key, value)

            await self.flush()
            logger.info(f"Updated preferences for user: {user_id}")
            return preference
        except Exception as e:
            await self.rollback()
            logger.error(f"Error updating preferences: {e!s}")
            raise


class UserSubscriptionProfileRepository(BaseRepository):
    """Repository for UserSubscriptionProfile model."""

    async def create_default(self, user_id: UUID, tier: str = "free") -> UserSubscriptionProfile:
        """Create default subscription profile."""
        try:
            subscription = UserSubscriptionProfile(
                user_id=user_id,
                subscription_tier=tier,
                max_concurrent_streams=1 if tier == "free" else (2 if tier == "basic" else 4),
                can_download=tier != "free",
                can_use_4k=tier == "premium",
                ad_free=tier != "free",
            )
            self.session.add(subscription)
            await self.flush()
            logger.info(f"Created subscription profile for user: {user_id}")
            return subscription
        except IntegrityError:
            await self.rollback()
            logger.error(f"Subscription already exists for user: {user_id}")
            raise

    async def get_by_user_id(self, user_id: UUID) -> UserSubscriptionProfile | None:
        """Get subscription by user ID."""
        stmt = select(UserSubscriptionProfile).where(UserSubscriptionProfile.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_tier(self, user_id: UUID, new_tier: str) -> UserSubscriptionProfile | None:
        """Update subscription tier."""
        try:
            subscription = await self.get_by_user_id(user_id)
            if not subscription:
                return None

            # Update tier and related limits
            subscription.subscription_tier = new_tier
            subscription.max_concurrent_streams = 1 if new_tier == "free" else (2 if new_tier == "basic" else 4)
            subscription.can_download = new_tier != "free"
            subscription.can_use_4k = new_tier == "premium"
            subscription.ad_free = new_tier != "free"

            await self.flush()
            logger.info(f"Updated subscription tier for user {user_id} to {new_tier}")
            return subscription
        except Exception as e:
            await self.rollback()
            logger.error(f"Error updating subscription tier: {e!s}")
            raise
