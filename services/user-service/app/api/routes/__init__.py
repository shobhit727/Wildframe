"""API routes for User Service."""
import logging
from typing import Annotated, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.repositories import (
    UserProfileRepository,
    UserDeviceRepository,
    UserPreferenceRepository,
    UserSubscriptionProfileRepository,
)
from app.schemas import (
    UserProfileResponse,
    UserProfileUpdateRequest,
    UserDeviceResponse,
    UserDeviceRegisterRequest,
    UserDeviceUpdateRequest,
    UserPreferenceResponse,
    UserPreferenceUpdateRequest,
    UserSubscriptionProfileResponse,
    UserProfileCompleteResponse,
)
from app.services import UserService

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_user_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserService:
    """Dependency to get UserService instance."""
    return UserService(
        profile_repo=UserProfileRepository(session),
        device_repo=UserDeviceRepository(session),
        preference_repo=UserPreferenceRepository(session),
        subscription_repo=UserSubscriptionProfileRepository(session),
    )


# Profile endpoints
@router.post(
    "/profiles",
    response_model=UserProfileResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Profiles"],
    summary="Create user profile",
)
async def create_profile(
    user_id: UUID,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserProfileResponse:
    """Create new user profile."""
    return await user_service.create_user_profile(user_id)


@router.get(
    "/profiles/{user_id}",
    response_model=UserProfileResponse,
    tags=["Profiles"],
    summary="Get user profile",
)
async def get_profile(
    user_id: UUID,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserProfileResponse:
    """Get user profile."""
    return await user_service.get_user_profile(user_id)


@router.get(
    "/profiles/{user_id}/complete",
    response_model=UserProfileCompleteResponse,
    tags=["Profiles"],
    summary="Get complete user profile",
    description="Get profile with all related data (devices, preferences, subscription)",
)
async def get_complete_profile(
    user_id: UUID,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserProfileCompleteResponse:
    """Get complete user profile with all related data."""
    return await user_service.get_complete_profile(user_id)


@router.patch(
    "/profiles/{user_id}",
    response_model=UserProfileResponse,
    tags=["Profiles"],
    summary="Update user profile",
)
async def update_profile(
    user_id: UUID,
    request: UserProfileUpdateRequest,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserProfileResponse:
    """Update user profile."""
    return await user_service.update_user_profile(user_id, request)


@router.post(
    "/profiles/{user_id}/onboarding/complete",
    response_model=UserProfileResponse,
    tags=["Profiles"],
    summary="Mark onboarding complete",
)
async def mark_onboarding_complete(
    user_id: UUID,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserProfileResponse:
    """Mark user onboarding as complete."""
    return await user_service.mark_onboarding_complete(user_id)


# Device endpoints
@router.post(
    "/devices",
    response_model=UserDeviceResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Devices"],
    summary="Register device",
)
async def register_device(
    user_id: UUID,
    request: Request,
    device_request: UserDeviceRegisterRequest,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserDeviceResponse:
    """Register new device for user."""
    ip_address = request.client.host if request.client else "unknown"
    return await user_service.register_device(user_id, device_request, ip_address)


@router.get(
    "/devices/{user_id}",
    response_model=List[UserDeviceResponse],
    tags=["Devices"],
    summary="Get user devices",
)
async def get_devices(
    user_id: UUID,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> List[UserDeviceResponse]:
    """Get all devices for user."""
    return await user_service.get_user_devices(user_id)


@router.patch(
    "/devices/{device_id}",
    response_model=UserDeviceResponse,
    tags=["Devices"],
    summary="Update device",
)
async def update_device(
    device_id: UUID,
    request: UserDeviceUpdateRequest,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserDeviceResponse:
    """Update device settings."""
    return await user_service.update_device(device_id, request)


@router.post(
    "/devices/{device_id}/deactivate",
    response_model=UserDeviceResponse,
    tags=["Devices"],
    summary="Deactivate device",
)
async def deactivate_device(
    device_id: UUID,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserDeviceResponse:
    """Deactivate device."""
    return await user_service.deactivate_device(device_id)


@router.delete(
    "/devices/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Devices"],
    summary="Remove device",
)
async def remove_device(
    device_id: UUID,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> None:
    """Remove device."""
    await user_service.remove_device(device_id)


# Preferences endpoints
@router.get(
    "/preferences/{user_id}",
    response_model=UserPreferenceResponse,
    tags=["Preferences"],
    summary="Get user preferences",
)
async def get_preferences(
    user_id: UUID,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserPreferenceResponse:
    """Get user preferences."""
    return await user_service.get_preferences(user_id)


@router.patch(
    "/preferences/{user_id}",
    response_model=UserPreferenceResponse,
    tags=["Preferences"],
    summary="Update user preferences",
)
async def update_preferences(
    user_id: UUID,
    request: UserPreferenceUpdateRequest,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserPreferenceResponse:
    """Update user preferences."""
    return await user_service.update_preferences(user_id, request)


# Subscription endpoints
@router.get(
    "/subscriptions/{user_id}",
    response_model=UserSubscriptionProfileResponse,
    tags=["Subscriptions"],
    summary="Get user subscription",
)
async def get_subscription(
    user_id: UUID,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserSubscriptionProfileResponse:
    """Get user subscription."""
    return await user_service.get_subscription(user_id)


@router.post(
    "/subscriptions/{user_id}/upgrade",
    response_model=UserSubscriptionProfileResponse,
    tags=["Subscriptions"],
    summary="Upgrade subscription",
)
async def upgrade_subscription(
    user_id: UUID,
    new_tier: str,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserSubscriptionProfileResponse:
    """Upgrade user subscription."""
    return await user_service.upgrade_subscription(user_id, new_tier)
