import re

"""API routes for User Service."""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
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
from app.security.manager import TokenManager
from app.services import UserService

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_current_user_id(
    authorization: str | None = Header(None, alias="Authorization"),
) -> UUID:
    """Validate JWT and return the authenticated user_id (sub claim)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ")
    payload = TokenManager.verify_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return UUID(payload["sub"])
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_self(
    jwt_user_id: Annotated[UUID, Depends(get_current_user_id)],
    request: Request,
) -> UUID:
    """Ensure path user_id matches the JWT user_id. Returns jwt_user_id."""
    path_user_id_raw = request.path_params.get("user_id")
    if path_user_id_raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id path parameter is required",
        )
    try:
        path_user_id = UUID(path_user_id_raw)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user_id path parameter",
        )
    if path_user_id != jwt_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this user",
        )
    return jwt_user_id


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
    jwt_user_id: Annotated[UUID, Depends(get_current_user_id)],
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserProfileResponse:
    """Create new user profile for the authenticated user."""
    return await user_service.create_user_profile(jwt_user_id)


@router.get(
    "/profiles/{user_id}",
    response_model=UserProfileResponse,
    tags=["Profiles"],
    summary="Get user profile",
)
async def get_profile(
    user_id: UUID,
    _user: Annotated[UUID, Depends(require_self)],
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
    _user: Annotated[UUID, Depends(require_self)],
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
    _user: Annotated[UUID, Depends(require_self)],
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
    _user: Annotated[UUID, Depends(require_self)],
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
    jwt_user_id: Annotated[UUID, Depends(get_current_user_id)],
    request: Request,
    device_request: UserDeviceRegisterRequest,
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserDeviceResponse:
    """Register new device for the authenticated user."""
    ip_address = request.client.host if request.client else "unknown"
    return await user_service.register_device(jwt_user_id, device_request, ip_address)


@router.get(
    "/devices/{user_id}",
    response_model=list[UserDeviceResponse],
    tags=["Devices"],
    summary="Get user devices",
)
async def get_devices(
    user_id: UUID,
    _user: Annotated[UUID, Depends(require_self)],
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> list[UserDeviceResponse]:
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
    _user: Annotated[UUID, Depends(get_current_user_id)],
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
    _user: Annotated[UUID, Depends(get_current_user_id)],
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
    _user: Annotated[UUID, Depends(get_current_user_id)],
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
    _user: Annotated[UUID, Depends(require_self)],
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
    _user: Annotated[UUID, Depends(require_self)],
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
    _user: Annotated[UUID, Depends(require_self)],
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
    _user: Annotated[UUID, Depends(require_self)],
    user_service: Annotated[UserService, Depends(get_user_service)],
) -> UserSubscriptionProfileResponse:
    """Upgrade user subscription."""
    return await user_service.upgrade_subscription(user_id, new_tier)
