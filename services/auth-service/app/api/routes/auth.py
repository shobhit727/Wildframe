"""
Authentication API routes.
All endpoints implement proper error handling, logging, and validation.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import pyotp
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.settings import settings
from app.repositories import (
    LoginAuditRepository,
    RefreshTokenRepository,
    TokenBlacklistRepository,
    UserRepository,
)
from app.schemas import (
    ChangePasswordRequest,
    MFAVerifyRequest,
    RefreshTokenRequest,
    ResendVerificationRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
    VerifyEmailRequest,
)
from app.security import PasswordManager, RateLimiter, SecretCipher, TokenManager, role_for_email
from app.services import AuthService

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
        token_repo=RefreshTokenRepository(db),
        audit_repo=LoginAuditRepository(db),
        password_manager=PasswordManager(),
        token_manager=TokenManager(),
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
    if await token_blacklist_repo.is_blacklisted(TokenManager.hash_token(token)):
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
        user_id = UUID(payload.get("user_id") or payload.get("sub"))
    except (ValueError, KeyError, TypeError):
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
    """Register a new user account and auto-login.

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
        user = await auth_service.register(request)
        await db.commit()

        # Auto-login: generate tokens for the new user
        user_id = user.id
        email = user.email
        access_token = TokenManager.create_access_token(user_id, email)
        token_manager = TokenManager()
        refresh_token, refresh_hash, expires_at = token_manager.create_refresh_token_for_user(user)
        token_repo = RefreshTokenRepository(db)
        await token_repo.create(user_id=user_id, token_hash=refresh_hash, expires_at=expires_at)
        await db.commit()

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=900,
            token_type="bearer",
        ).model_dump()

    except HTTPException:
        await db.rollback()
        raise
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
        ip_address = http_request.client.host if http_request.client else None

        token_response = await auth_service.login(request, ip_address=ip_address)

        await db.commit()

        return token_response.model_dump()

    except HTTPException:
        await db.rollback()
        raise
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
        token_response = await auth_service.refresh_token(request.refresh_token)

        await db.commit()

        return token_response.model_dump()

    except HTTPException:
        await db.rollback()
        raise
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
    request: RefreshTokenRequest | None = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> None:
    """Logout by revoking the refresh token or blacklisting the access token.

    Accepts either a JSON body with a refresh_token or an Authorization
    header with a Bearer access token.

    Args:
        request: Optional refresh token body
        authorization: Authorization header (Bearer access token)
        auth_service: Auth service
        db: Database session

    Raises:
        HTTPException: If token invalid
    """
    try:
        if request and request.refresh_token:
            await auth_service.logout(request.refresh_token)
            await db.commit()
            return

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

        user_id = UUID(payload.get("user_id") or payload.get("sub"))
        token_hash = TokenManager.hash_token(token)
        expires_at = datetime.fromtimestamp(payload["exp"], tz=UTC)
        blacklist_repo = TokenBlacklistRepository(db)
        await blacklist_repo.create(token_hash=token_hash, user_id=user_id, expires_at=expires_at)
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

        data = UserResponse.model_validate(user).model_dump()
        data["role"] = role_for_email(user.email)
        return data

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


@router.post("/verify-email")
async def verify_email(
    request: VerifyEmailRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Verify email via a signed ownership token.

    The token is a JWT of type ``email_verification`` bound to the user and
    email. It is only issued after account creation or an explicit resend,
    so verifying proves possession of the inbox link/address it was sent to.
    """
    payload = TokenManager.verify_token(request.token, token_type="email_verification")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )

    try:
        token_user_id = UUID(payload.get("user_id") or payload.get("sub"))
    except (ValueError, KeyError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification token"
        )

    if payload.get("email") != request.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token email does not match requested email",
        )

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(token_user_id)
    if not user or user.email != request.email:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if not user.email_verified:
        user.email_verified = True
        user.email_verified_at = datetime.now(UTC)
        await db.commit()
        logger.info(f"Email verified for user: {user.email}")

    return {"message": "Email verified successfully"}


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(
    request: ResendVerificationRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Send a new email verification token for an existing, unverified account.

    In dev the token is returned so the flow is exercisable without an email
    provider; in production it is logged for the mail transport and never
    returned to the caller.
    """
    email = request.email.strip().lower()
    user_repo = UserRepository(db)
    user = await user_repo.get_by_email(email)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.email_verified:
        return {"message": "Email already verified"}

    token = TokenManager.create_email_verification_token(user.id, user.email)
    logger.info(f"Email verification token issued for {user.email}")

    response: dict = {"message": "Verification email sent"}
    if settings.ENVIRONMENT != "production":
        response["verification_token"] = token
    return response


@router.post("/mfa/setup")
async def setup_mfa(
    user_id: Annotated[UUID, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Generate a TOTP secret and provisioning URI for the authenticator app.

    The secret is encrypted at rest. It is returned exactly once so the
    client can seed the authenticator; re-enabling MFA issues a fresh secret.
    """
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="MFA is already enabled"
        )

    secret = pyotp.random_base32()
    issuer = settings.MFA_ISSUER_NAME
    totp_uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=issuer)

    user.mfa_secret = SecretCipher.encrypt(secret)
    await db.commit()
    logger.info(f"MFA setup issued for user: {user.email}")

    return {"secret": secret, "totp_uri": totp_uri}


@router.post("/mfa/verify", responses={200: {"description": "MFA enabled"}})
async def verify_mfa(
    request: MFAVerifyRequest,
    user_id: Annotated[UUID, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Enable MFA after a correct TOTP code from the just-provisioned secret."""
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.mfa_enabled:
        return {"message": "MFA already enabled"}
    if not user.mfa_secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA not set up")

    secret = SecretCipher.decrypt(user.mfa_secret)
    if not secret or not pyotp.TOTP(secret).verify(request.code, valid_window=1):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid MFA code")

    user.mfa_enabled = True
    await db.commit()
    logger.info(f"MFA enabled for user: {user.email}")
    return {"message": "MFA enabled successfully"}


@router.post("/mfa/disable")
async def disable_mfa(
    request: MFAVerifyRequest,
    user_id: Annotated[UUID, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Disable MFA after a valid TOTP code, clearing the stored secret."""
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not user.mfa_enabled:
        return {"message": "MFA is not enabled"}

    secret = SecretCipher.decrypt(user.mfa_secret) if user.mfa_secret else ""
    if not secret or not pyotp.TOTP(secret).verify(request.code, valid_window=1):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid MFA code")

    user.mfa_enabled = False
    user.mfa_secret = None
    await db.commit()
    logger.info(f"MFA disabled for user: {user.email}")
    return {"message": "MFA disabled successfully"}
