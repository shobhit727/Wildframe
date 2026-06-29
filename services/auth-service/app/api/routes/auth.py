"""
Authentication API routes.
All endpoints implement proper error handling, logging, and validation.
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import DatabaseManager, get_db
from app.core.settings import settings
from app.schemas.auth import (
    TokenResponse,
    UserResponse,
    UserRegisterRequest,
    UserLoginRequest,
    RefreshTokenRequest,
    ChangePasswordRequest,
    VerifyEmailRequest,
    MFASetupRequest,
    MFAVerifyRequest,
    ErrorResponse
)
from app.repositories.user_repository import (
    UserRepository,
    RefreshTokenRepository,
    TokenBlacklistRepository,
    LoginAuditRepository
)
from app.services.auth_service import AuthService
from app.security.manager import RateLimiter, TokenManager, PasswordManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Dependency injection
rate_limiter = RateLimiter()


async def get_auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
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
        rate_limiter=rate_limiter
    )


async def get_current_user(
    authorization: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
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
            detail="Missing or invalid authorization header"
        )
    
    token = authorization.replace("Bearer ", "")
    
    # Check if blacklisted
    token_blacklist_repo = TokenBlacklistRepository(db)
    if await token_blacklist_repo.is_blacklisted(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked"
        )
    
    # Verify token
    payload = TokenManager.verify_token(token, token_type="access")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )
    
    try:
        user_id = UUID(payload["sub"])
    except (ValueError, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload"
        )
    
    return user_id


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request: UserRegisterRequest,
    auth_service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_db)
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
        token_response, refresh_token = await auth_service.register(
            request.email,
            request.password
        )
        
        # Store refresh token in response headers for security
        await db.commit()
        
        return {
            **token_response,
            "refresh_token": refresh_token
        }
        
    except ValueError as e:
        await db.rollback()
        logger.warning(f"Registration failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Registration error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: UserLoginRequest,
    http_request: Request,
    auth_service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_db)
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
            device_id=request.device_id
        )
        
        await db.commit()
        
        return {
            **token_response,
            "refresh_token": refresh_token
        }
        
    except ValueError as e:
        await db.rollback()
        logger.warning(f"Login failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Login error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: RefreshTokenRequest,
    auth_service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_db)
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
        
        return {
            **token_response,
            "refresh_token": new_refresh_token
        }
        
    except ValueError as e:
        await db.rollback()
        logger.warning(f"Token refresh failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e)
        )
    except Exception as e:
        await db.rollback()
        logger.error(f"Token refresh error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    authorization: Optional[str] = None,
    auth_service: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_db)
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
                detail="Missing or invalid authorization header"
            )
        
        token = authorization.replace("Bearer ", "")
        payload = TokenManager.verify_token(token, token_type="access")
        
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token"
            )
        
        user_id = UUID(payload["sub"])
        await auth_service.logout(token, user_id)
        await db.commit()
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Logout error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
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
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        return UserResponse.model_validate(user)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user info: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    request: ChangePasswordRequest,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
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
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Verify current password
        if not PasswordManager.verify_password(request.current_password, user.password_hash):
            logger.warning(f"Invalid current password for user: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Current password is incorrect"
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
    except Exception as e:
        await db.rollback()
        logger.error(f"Error changing password: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.post("/verify-email", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(
    request: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db)
) -> None:
    """Verify user email address.
    
    Note: This is a simplified implementation. In production,
    the code would be sent via email and verified here.
    
    Args:
        request: Email verification request
        db: Database session
        
    Raises:
        HTTPException: If verification fails
    """
    try:
        user_repo = UserRepository(db)
        user = await user_repo.get_by_email(request.email)
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # NOTE: A real flow generates a one-time code, stores it in Redis
        # (TTL'd to EMAIL_VERIFICATION_EXPIRATION_HOURS), and emails it. The
        # caller then resubmits here for validation. That Redis-backed
        # storage is not wired yet, so to avoid flagging emails as verified
        # without proof, this endpoint returns 501 until the flow is complete.
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Email verification code flow is not implemented; "
                   "verification codes are not yet generated or stored."
        )

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Email verification error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.post("/mfa/setup", status_code=status.HTTP_201_CREATED)
async def setup_mfa(
    request: MFASetupRequest,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Setup MFA for user account.
    
    Args:
        request: MFA setup request
        user_id: Current user ID
        db: Database session
        
    Returns:
        MFA setup details (QR code, backup codes, etc.)
        
    Raises:
        HTTPException: If user not found
    """
    try:
        user_repo = UserRepository(db)
        user = await user_repo.get_by_id(user_id)
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # NOTE: Full setup generates a TOTP secret, renders a provisioning QR
        # code (otpauth://...), returns backup codes, and waits for the user
        # to submit a TOTP code to /mfa/verify before enabling. The TOTP/QR
        # pipeline is not implemented, so to avoid enabling MFA with no
        # secret, this endpoint returns 501 until the flow is complete.
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="MFA setup (TOTP secret + QR provisioning) is not implemented."
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MFA setup error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )


@router.post("/mfa/verify", status_code=status.HTTP_204_NO_CONTENT)
async def verify_mfa(
    request: MFAVerifyRequest,
    user_id: UUID = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
) -> None:
    """Verify MFA code and complete MFA setup.
    
    Args:
        request: MFA verification request
        user_id: Current user ID
        db: Database session
        
    Raises:
        HTTPException: If verification fails
    """
    try:
        user_repo = UserRepository(db)
        user = await user_repo.get_by_id(user_id)
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # NOTE: Full verification checks the submitted TOTP code against the
        # user's stored secret before flipping mfa_enabled. Neither secret
        # storage nor the TOTP checker is implemented, so to avoid enabling
        # MFA without actual 2FA proof, this endpoint returns 501.
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="MFA verification (TOTP code check) is not implemented."
        )

    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"MFA verification error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )
