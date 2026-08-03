"""
Authentication API routes.
All endpoints implement proper error handling, logging, and validation.
"""

import logging
from typing import Annotated
from uuid import UUID

from app.core.database import get_db
from app.repositories import (
    LoginAuditRepository,
    RefreshTokenRepository,
    TokenBlacklistRepository,
    UserRepository,
)
from app.schemas import (
    ChangePasswordRequest,
    MFASetupRequest,
    MFAVerifyRequest,
    RefreshTokenRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
    VerifyEmailRequest,
)
from app.security import PasswordManager, RateLimiter, TokenManager
from app.services import AuthService
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Dependency injection
rate_limiter = RateLimiter()


async def get_auth_service(db: Annotated[AsyncSession, Depends(get_db)]) -> AuthService:
    """Get auth service instance.

    Args:
        db: Database session

    Returns:
        AuthService instance
    """
    return AuthService(
        user_repo=UserRepository(db),
        refresh_token_repo=RefreshTokenRepository(db),
        token_blacklist_repo=TokenBlacklistRepository(db),
        login_audit_repo=LoginAuditRepository(db),
        rate_limiter=rate_limiter,
    )


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    """Extract and verify current user from JWT token.

    Args:
        authorization: Authorization header
        db: Database session

    Returns:
        User ID

    Raises:
        HTTPException: If token invalid or missing
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
        )

    token = authorization.replace("Bearer ", "")

    # Check if blacklisted
    token_blacklist_repo = TokenBlacklistRepository(db)
    if await token_blacklist_repo.is_blacklisted(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked"
        )

    # Verify token
    payload = TokenManager.verify_token(token, token_type="access")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )

    try:
        user_id = UUID(payload["sub"])
    except (ValueError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload"
        )

    return user_id


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: UserRegisterRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Register a new user account.

    Args:
        request: Registration request
        auth_service: Auth service
        db: Database session

    Returns:
        Token response with access and refresh tokens

    Raises:
        HTTPException: If email already exists or validation fails
    """
    try:
        token_response, refresh_token = await auth_service.register(request.email, request.password)

        # Store refresh token in response headers for security
        await db.commit()

        return {**token_response, "refresh_token": refresh_token}

    except ValueError as e:
        await db.rollback()
        logger.warning(f"Registration failed: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        logger.error(f"Registration error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: UserLoginRequest,
    http_request: Request,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Authenticate user and return tokens.

    Args:
        request: Login request
        http_request: HTTP request for context
        auth_service: Auth service
        db: Database session

    Returns:
        Token response with access and refresh tokens

    Raises:
        HTTPException: If credentials invalid or account locked
    """
    try:
        # Extract client info
        user_agent = http_request.headers.get("user-agent")
        ip_address = http_request.client.host if http_request.client else None

        token_response, refresh_token = await auth_service.login(
            request.email,
            request.password,
            user_agent=user_agent,
            ip_address=ip_address,
            device_id=request.device_id,
        )

        await db.commit()

        return {**token_response, "refresh_token": refresh_token}

    except ValueError as e:
        await db.rollback()
        logger.warning(f"Login failed: {e}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        logger.error(f"Login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: RefreshTokenRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get new access token using refresh token.

    Args:
        request: Refresh token request
        auth_service: Auth service
        db: Database session

    Returns:
        New token response with fresh access token

    Raises:
        HTTPException: If refresh token invalid or expired
    """
    try:
        token_response, new_refresh_token = await auth_service.refresh_access_token(
            request.refresh_token
        )

        await db.commit()

        return {**token_response, "refresh_token": new_refresh_token}

    except ValueError as e:
        await db.rollback()
        logger.warning(f"Token refresh failed: {e}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        logger.error(f"Token refresh error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> None:
    """Logout user by revoking tokens.

    Args:
        authorization: Authorization header
        auth_service: Auth service
        db: Database session

    Raises:
        HTTPException: If token invalid
    """
    try:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid authorization header",
            )

        token = authorization.replace("Bearer ", "")
        payload = TokenManager.verify_token(token, token_type="access")

        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
            )

        user_id = UUID(payload["sub"])
        await auth_service.logout(token, user_id)
        await db.commit()

    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        logger.error(f"Logout error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    user_id: Annotated[UUID, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Get current user information.

    Args:
        user_id: Current user ID
        db: Database session

    Returns:
        User information

    Raises:
        HTTPException: If user not found
    """
    try:
        user_repo = UserRepository(db)
        user = await user_repo.get_by_id(user_id)

        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        return UserResponse.model_validate(user)

    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error fetching user info: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    request: ChangePasswordRequest,
    user_id: Annotated[UUID, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Change user password.

    Args:
        request: Password change request
        user_id: Current user ID
        db: Database session

    Raises:
        HTTPException: If current password invalid or user not found
    """
    try:
        user_repo = UserRepository(db)
        user = await user_repo.get_by_id(user_id)

        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # Verify current password
        if not PasswordManager.verify_password(request.current_password, user.password_hash):
            logger.warning(f"Invalid current password for user: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect"
            )

        # Update password
        user.password_hash = PasswordManager.hash_password(request.new_password)
        await db.flush()

        # Revoke all refresh tokens
        refresh_token_repo = RefreshTokenRepository(db)
        await refresh_token_repo.revoke_all_for_user(user_id)

        await db.commit()
        logger.info(f"Password changed for user: {user_id}")

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        logger.error(f"Error changing password: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error"
        )


@router.post("/verify-email", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def verify_email(
    request: VerifyEmailRequest,
    user_id: Annotated[UUID, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Stub: real email verification flow not yet implemented.

    The previous implementation abused the User model's `mfa_secret` and
    `locked_until` columns as temporary storage for verification codes and
    expiry, which silently flipped `email_verified` on without proof of
    ownership. Per AGENTS.md, return 501 until the flow exists.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Email verification is not yet implemented",
    )


@router.post("/mfa/setup", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def setup_mfa(
    request: MFASetupRequest,
    user_id: Annotated[UUID, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Stub: real MFA setup flow not yet implemented.

    The previous implementation stored the TOTP secret in plaintext on
    the User row and returned it in the response. Per AGENTS.md, return
    501 until a real TOTP provisioning flow with secure secret storage
    exists.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="MFA setup is not yet implemented",
    )


@router.post("/mfa/verify", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def verify_mfa(
    request: MFAVerifyRequest,
    user_id: Annotated[UUID, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Stub: real MFA verification flow not yet implemented."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="MFA verification is not yet implemented",
        )
