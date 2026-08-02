"""Service layer for User Service."""
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status

from app.repositories import (
    UserDeviceRepository,
    UserPreferenceRepository,
    UserProfileRepository,
    UserSubscriptionProfileRepository,
)
from app.schemas import (
    UserDeviceRegisterRequest,
    UserDeviceResponse,
    UserDeviceUpdateRequest,
    UserPreferenceResponse,
    UserPreferenceUpdateRequest,
    UserProfileCompleteResponse,
    UserProfileResponse,
    UserProfileUpdateRequest,
    UserSubscriptionProfileResponse,
)

logger = logging.getLogger(__name__)


class UserService:
    """Business logic for user management."""

    def __init__(
        self,
        profile_repo: UserProfileRepository,
        device_repo: UserDeviceRepository,
        preference_repo: UserPreferenceRepository,
        subscription_repo: UserSubscriptionProfileRepository,
    ):
        self.profile_repo = profile_repo
        self.device_repo = device_repo
        self.preference_repo = preference_repo
        self.subscription_repo = subscription_repo

    async def create_user_profile(self, user_id: UUID) -> UserProfileResponse:
        """Create new user profile."""
        try:
            profile = await self.profile_repo.create(user_id=user_id)
            # Create default preferences
            await self.preference_repo.create_default(user_id)
            # Create default subscription
            await self.subscription_repo.create_default(user_id, tier="free")
            await self.profile_repo.commit()
            logger.info(f"Created full user profile for: {user_id}")
            return UserProfileResponse.from_orm(profile)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error creating user profile: {e!s}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create user profile",
            )

    async def get_user_profile(self, user_id: UUID) -> UserProfileResponse:
        """Get user profile."""
        profile = await self.profile_repo.get_by_user_id(user_id)
        if not profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile not found",
            )
        return UserProfileResponse.from_orm(profile)

    async def update_user_profile(
        self,
        user_id: UUID,
        request: UserProfileUpdateRequest,
    ) -> UserProfileResponse:
        """Update user profile."""
        update_data = request.model_dump(exclude_unset=True)
        profile = await self.profile_repo.update(user_id, **update_data)
        if not profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile not found",
            )
        await self.profile_repo.commit()
        logger.info(f"Updated user profile: {user_id}")
        return UserProfileResponse.from_orm(profile)

    async def get_complete_profile(self, user_id: UUID) -> UserProfileCompleteResponse:
        """Get complete user profile with all related data."""
        profile = await self.profile_repo.get_by_user_id(user_id)
        if not profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        devices = await self.device_repo.get_user_devices(user_id)
        preferences = await self.preference_repo.get_by_user_id(user_id)
        subscription = await self.subscription_repo.get_by_user_id(user_id)

        return UserProfileCompleteResponse(
            profile=UserProfileResponse.from_orm(profile),
            devices=[UserDeviceResponse.from_orm(d) for d in devices],
            preferences=UserPreferenceResponse.from_orm(preferences),
            subscription=UserSubscriptionProfileResponse.from_orm(subscription),
        )

    # Device management
    async def register_device(
        self,
        user_id: UUID,
        request: UserDeviceRegisterRequest,
        ip_address: str,
    ) -> UserDeviceResponse:
        """Register new device."""
        try:
            device = await self.device_repo.create(
                user_id=user_id,
                device_id=request.device_id,
                device_name=request.device_name,
                device_type=request.device_type,
                os_name=request.os_name,
                os_version=request.os_version,
                browser_name=request.browser_name,
                browser_version=request.browser_version,
                ip_address=ip_address,
                user_agent=request.user_agent,
                last_active_at=datetime.now(UTC),
            )
            await self.device_repo.commit()
            logger.info(f"Registered device {request.device_id} for user {user_id}")
            return UserDeviceResponse.from_orm(device)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error registering device: {e!s}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to register device",
            )

    async def get_user_devices(self, user_id: UUID) -> list[UserDeviceResponse]:
        """Get all user devices."""
        devices = await self.device_repo.get_user_devices(user_id)
        return [UserDeviceResponse.from_orm(d) for d in devices]

    async def update_device(
        self,
        device_id: UUID,
        request: UserDeviceUpdateRequest,
    ) -> UserDeviceResponse:
        """Update device settings."""
        update_data = request.model_dump(exclude_unset=True)
        device = await self.device_repo.update(device_id, **update_data)
        if not device:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Device not found",
            )
        await self.device_repo.commit()
        logger.info(f"Updated device: {device_id}")
        return UserDeviceResponse.from_orm(device)

    async def deactivate_device(self, device_id: UUID) -> UserDeviceResponse:
        """Deactivate a device."""
        device = await self.device_repo.mark_device_inactive(device_id)
        if not device:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Device not found",
            )
        await self.device_repo.commit()
        logger.info(f"Deactivated device: {device_id}")
        return UserDeviceResponse.from_orm(device)

    async def remove_device(self, device_id: UUID) -> bool:
        """Remove device."""
        success = await self.device_repo.delete(device_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Device not found",
            )
        await self.device_repo.commit()
        logger.info(f"Removed device: {device_id}")
        return True

    # Preferences management
    async def get_preferences(self, user_id: UUID) -> UserPreferenceResponse:
        """Get user preferences."""
        preferences = await self.preference_repo.get_by_user_id(user_id)
        if not preferences:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Preferences not found",
            )
        return UserPreferenceResponse.from_orm(preferences)

    async def update_preferences(
        self,
        user_id: UUID,
        request: UserPreferenceUpdateRequest,
    ) -> UserPreferenceResponse:
        """Update user preferences."""
        update_data = request.model_dump(exclude_unset=True)
        preferences = await self.preference_repo.update(user_id, **update_data)
        if not preferences:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Preferences not found",
            )
        await self.preference_repo.commit()
        logger.info(f"Updated preferences for user: {user_id}")
        return UserPreferenceResponse.from_orm(preferences)

    # Subscription management
    async def get_subscription(self, user_id: UUID) -> UserSubscriptionProfileResponse:
        """Get user subscription."""
        subscription = await self.subscription_repo.get_by_user_id(user_id)
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription not found",
            )
        return UserSubscriptionProfileResponse.from_orm(subscription)

    async def upgrade_subscription(
        self,
        user_id: UUID,
        new_tier: str,
    ) -> UserSubscriptionProfileResponse:
        """Upgrade user subscription."""
        subscription = await self.subscription_repo.update_tier(user_id, new_tier)
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subscription not found",
            )
        await self.subscription_repo.commit()
        logger.info(f"Upgraded subscription for user {user_id} to {new_tier}")
        return UserSubscriptionProfileResponse.from_orm(subscription)

    async def mark_onboarding_complete(self, user_id: UUID) -> UserProfileResponse:
        """Mark user onboarding as complete."""
        profile = await self.profile_repo.mark_onboarding_complete(user_id)
        if not profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        await self.profile_repo.commit()
        logger.info(f"Marked onboarding complete for user: {user_id}")
        return UserProfileResponse.from_orm(profile)
