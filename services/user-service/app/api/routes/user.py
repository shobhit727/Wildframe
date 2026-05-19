"""User service API routes."""

import logging
from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.user import (
    UserProfileResponse,
    UpdateProfileRequest,
    RegisterDeviceRequest,
    DeviceResponse,
    ListDevicesResponse,
    UserSessionResponse,
    ListSessionsResponse,
    UpdatePreferenceRequest,
    UserPreferenceResponse,
    ListWatchHistoryResponse,
    WatchHistoryItemResponse,
    ErrorResponse
)
from app.services.user import UserService
from app.security.manager import TokenManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


async def get_user_service(db: AsyncSession = Depends(get_db)) -> UserService:
    """Get user service instance."""
    return UserService(db)


async def get_current_user(
    authorization: Optional[str] = None,
) -> UUID:
    """Extract and verify current user from token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header"
        )
    
    token = authorization.replace("Bearer ", "")
    payload = TokenManager.verify_token(token)
    
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    
    return UUID(payload["sub"])


# Profile Endpoints

@router.get("/me", response_model=UserProfileResponse)
async def get_my_profile(
    user_id: UUID = Depends(get_current_user),
    service: UserService = Depends(get_user_service)
) -> UserProfileResponse:
    """Get current user profile."""
    try:
        profile = await service.get_profile(user_id)
        return profile
    except ValueError as e:
        logger.error(f"Profile not found for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error fetching profile: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch profile"
        )


@router.put("/me", response_model=UserProfileResponse)
async def update_my_profile(
    data: UpdateProfileRequest,
    user_id: UUID = Depends(get_current_user),
    service: UserService = Depends(get_user_service)
) -> UserProfileResponse:
    """Update current user profile."""
    try:
        profile = await service.update_profile(user_id, data.model_dump(exclude_none=True))
        logger.info(f"Profile updated for user {user_id}")
        return profile
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error updating profile: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update profile"
        )


# Device Endpoints

@router.post("/devices", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def register_device(
    data: RegisterDeviceRequest,
    user_id: UUID = Depends(get_current_user),
    service: UserService = Depends(get_user_service)
) -> DeviceResponse:
    """Register a new device."""
    try:
        device = await service.register_device(user_id, data.model_dump())
        logger.info(f"Device registered for user {user_id}")
        return device
    except Exception as e:
        logger.error(f"Error registering device: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register device"
        )


@router.get("/devices", response_model=ListDevicesResponse)
async def list_devices(
    active_only: bool = True,
    user_id: UUID = Depends(get_current_user),
    service: UserService = Depends(get_user_service)
) -> ListDevicesResponse:
    """List user devices."""
    try:
        devices = await service.list_devices(user_id, active_only)
        return ListDevicesResponse(devices=devices, total=len(devices))
    except Exception as e:
        logger.error(f"Error listing devices: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list devices"
        )


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_device(
    device_id: UUID,
    user_id: UUID = Depends(get_current_user),
    service: UserService = Depends(get_user_service)
) -> None:
    """Deactivate a device."""
    try:
        await service.deactivate_device(device_id, user_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error deactivating device: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to deactivate device"
        )


# Session Endpoints

@router.get("/sessions", response_model=ListSessionsResponse)
async def list_sessions(
    user_id: UUID = Depends(get_current_user),
    service: UserService = Depends(get_user_service)
) -> ListSessionsResponse:
    """List user active sessions."""
    try:
        sessions = await service.get_active_sessions(user_id)
        return ListSessionsResponse(sessions=sessions, total=len(sessions))
    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list sessions"
        )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def end_session(
    session_id: UUID,
    user_id: UUID = Depends(get_current_user),
    service: UserService = Depends(get_user_service)
) -> None:
    """End a specific session."""
    try:
        await service.end_session(user_id, session_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error ending session: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to end session"
        )


@router.post("/sessions/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all_sessions(
    user_id: UUID = Depends(get_current_user),
    service: UserService = Depends(get_user_service)
) -> None:
    """Logout from all devices."""
    try:
        await service.end_all_sessions(user_id)
    except Exception as e:
        logger.error(f"Error logging out from all devices: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to logout from all devices"
        )


# Watch History Endpoints

@router.get("/watch-history", response_model=ListWatchHistoryResponse)
async def get_watch_history(
    limit: int = 50,
    offset: int = 0,
    user_id: UUID = Depends(get_current_user),
    service: UserService = Depends(get_user_service)
) -> ListWatchHistoryResponse:
    """Get user watch history."""
    try:
        items, total = await service.get_watch_history(user_id, limit, offset)
        return ListWatchHistoryResponse(items=items, total=total)
    except Exception as e:
        logger.error(f"Error fetching watch history: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch watch history"
        )


# Preference Endpoints

@router.get("/preferences", response_model=UserPreferenceResponse)
async def get_preferences(
    user_id: UUID = Depends(get_current_user),
    service: UserService = Depends(get_user_service)
) -> UserPreferenceResponse:
    """Get user preferences."""
    try:
        prefs = await service.get_preferences(user_id)
        return prefs
    except Exception as e:
        logger.error(f"Error fetching preferences: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch preferences"
        )


@router.put("/preferences", response_model=UserPreferenceResponse)
async def update_preferences(
    data: UpdatePreferenceRequest,
    user_id: UUID = Depends(get_current_user),
    service: UserService = Depends(get_user_service)
) -> UserPreferenceResponse:
    """Update user preferences."""
    try:
        prefs = await service.update_preferences(user_id, data.model_dump(exclude_none=True))
        return prefs
    except Exception as e:
        logger.error(f"Error updating preferences: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update preferences"
        )
