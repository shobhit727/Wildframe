"""
Authentication API routes.
All endpoints implement proper error handling, logging, and validation.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import pyotp
from jose.exceptions import JWTError
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User
from app.core.rate_limit import allow
from app.core.settings import settings
from app.repositories import (
    LoginAuditRepository,
    RefreshTokenRepository,
    TokenBlacklistRepository,
    UserRepository,
)
from app.schemas import (
    ChangePasswordRequest,
    MFALoginVerifyRequest,
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
from app.services import AuthService, MfaChallengeRequired

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Dependency injection
rate_limiter = RateLimiter()

# Enumeration-safe message used by the verification-resent flow (#54):
# identical for unknown, verified and unverified addresses.
_ENUMERATION_SAFE_MESSAGE = (
    "If an account exists with this email and has not yet been verified, "
    "a new verification email has been sent."
)


async def _get_user_locked(db: AsyncSession, user_id: UUID) -> User:
    """Fetch the user row with a FOR UPDATE lock (serializes MFA state
    transitions so concurrent enrollment requests cannot race)."""
    stmt = select(User).where(User.id == user_id).with_for_update()
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


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
        blacklist_repo=TokenBlacklistRepository(db),
    )


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> UUID:
    """Extract and verify current user from JWT token.

    Also verifies the auth_version (av claim) matches the current user version
    to invalidate tokens on password/role/email change (#79/#81).

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
    try:
        payload = TokenManager.verify_token(token, token_type="access")
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )
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

    # Disabled/deleted accounts must not be able to use live access tokens,
    # and access tokens minted before the last credential rotation (av <
    # the account's current auth_version) are rejected immediately (#79/#99).
    user = await UserRepository(db).get_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )
    if int(payload.get("av", 0)) != user.auth_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has been revoked"
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

        # Auto-login: generate tokens for the new user using service's token_manager
        user_id = user.id
        email = user.email
        orm_user = await UserRepository(db).get_by_id(user_id)
        access_token = TokenManager.create_access_token(
            user_id, email, orm_user.auth_version if orm_user else 0
        )
        refresh_token = TokenManager.create_refresh_token(user_id)
        refresh_hash = TokenManager.hash_refresh_token(refresh_token)
        expires_at = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRATION_DAYS)
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


@router.post(
    "/login",
    response_model=TokenResponse | dict,
)
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
        Token response with access and refresh tokens, or an MFA challenge
        when the user has MFA enabled.

    Raises:
        HTTPException: If credentials invalid or account locked
    """
    try:
        # Extract client info
        ip_address = http_request.client.host if http_request.client else "unknown"

        token_response = await auth_service.login(request, ip_address=ip_address)

        await db.commit()

        return token_response.model_dump()

    except MfaChallengeRequired as exc:
        return {
            "requires_mfa": True,
            "mfa_challenge": exc.challenge_token,
            "expires_in": settings.MFA_CHALLENGE_EXPIRATION_MINUTES * 60,
        }
    except HTTPException:
        await db.rollback()
        raise


@router.post("/mfa/login-verify", response_model=TokenResponse)
async def mfa_login_verify(
    request: MFALoginVerifyRequest,
    http_request: Request,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Complete an MFA-gated login: exchange a challenge token + TOTP code
    for real access/refresh tokens."""
    ip_address = http_request.client.host if http_request.client else "unknown"

    # Anti-brute-force (#77/#97): a valid challenge can be probed with
    # guessed TOTP codes. Bound attempts per IP and per user; fail-open on
    # Redis outage so availability is not traded for security here.
    if not await allow(f"mfa:verify:ip:{ip_address}", max_requests=30, window_seconds=900):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again later.",
        )
    challenge_user_id = TokenManager.verify_mfa_challenge(request.mfa_challenge)
    if challenge_user_id is not None and not await allow(
        f"mfa:verify:user:{challenge_user_id}", max_requests=10, window_seconds=900
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again later.",
        )

    try:
        token_response = await auth_service.complete_mfa_login(
            request.mfa_challenge, request.code, ip_address=ip_address
        )
        await db.commit()
        return token_response.model_dump()
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        logger.error(f"MFA login verification error: {e}")
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
        try:
            payload = TokenManager.verify_token(token, token_type="access")
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
            )
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
        # Advance the account's auth version: every access token issued
        # before this change carries an older "av" claim and is rejected
        # at the boundary (#79/#99).
        user.auth_version += 1
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
    try:
        payload = TokenManager.verify_token(request.token, token_type="email_verification")
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )
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

    # Single-use enforcement (#80/#100): the token's hash is recorded in the
    # blacklist table with the token's own expiry, atomically on success.
    # A replayed token (or a concurrent second use) hits the unique
    # constraint and is rejected.
    token_hash = TokenManager.hash_token(request.token)
    blacklist_repo = TokenBlacklistRepository(db)
    if await blacklist_repo.is_blacklisted(token_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(token_user_id)
    if not user or user.email != request.email:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    expires_at = datetime.fromtimestamp(payload["exp"], tz=UTC)
    try:
        if not user.email_verified:
            user.email_verified = True
            user.email_verified_at = datetime.now(UTC)
        await blacklist_repo.create(
            token_hash=token_hash, user_id=token_user_id, expires_at=expires_at
        )
        await db.commit()
        logger.info(f"Email verified for user: {user.email}")
    except IntegrityError:
        # Concurrent use won the consumption race — treat as replay.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )

    return {"message": "Email verified successfully"}


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(
    request: ResendVerificationRequest,
    request_obj: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Send a new email verification token for an existing, unverified account.

    Enumeration-resistant (#54): the response is identical whether the
    email is unknown, verified, or unverified. Abuse controls: per-IP and
    per-email quotas plus a cooldown between sends (Redis-backed, fail-open).
    In dev the token is returned so the flow is exercisable without an email
    provider; in production it is never returned to the caller.
    """
    email = request.email.strip().lower()
    client_ip = request_obj.client.host if request_obj.client else "unknown"

    # Anti-abuse: per-IP quota + per-email quota with a send cooldown.
    # Explicit 429s here do not leak account existence (applies to all emails).
    if not await allow(
        f"resend:ip:{client_ip}", max_requests=10, window_seconds=3600
    ) or not await allow(
        f"resend:email:{email}", max_requests=10, window_seconds=3600, cooldown_seconds=120
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again later.",
        )

    user_repo = UserRepository(db)
    user = await user_repo.get_by_email(email)
    if not user or user.email_verified:
        # Indistinguishable from the real send, for unknown and verified
        # addresses alike — no 404, no "already verified" signal.
        logger.info("Resend-verification requested for %s (no email issued)", email)
        return {"message": _ENUMERATION_SAFE_MESSAGE}

    token = TokenManager.create_email_verification_token(user.id, user.email)
    logger.info("Email verification token issued for %s", user.email)

    response: dict = {"message": _ENUMERATION_SAFE_MESSAGE}
    if settings.ENVIRONMENT != "production":
        response["verification_token"] = token
    return response


@router.post("/mfa/setup")
async def setup_mfa(
    user_id: Annotated[UUID, Depends(get_current_user)],
    request_obj: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Generate a TOTP secret and provisioning URI for the authenticator app.

    The secret is encrypted at rest. It is returned exactly once so the
    client can seed the authenticator; re-enabling MFA issues a fresh secret.
    """
    # Rate limit: per-IP + per-user (#241)
    client_ip = request_obj.client.host if request_obj.client else "unknown"
    if not await allow(
        f"mfa:setup:ip:{client_ip}",
        max_requests=settings.MFA_SETUP_RATE_LIMIT_ATTEMPTS,
        window_seconds=settings.MFA_SETUP_RATE_LIMIT_WINDOW,
    ) or not await allow(
        f"mfa:setup:user:{user_id}",
        max_requests=settings.MFA_SETUP_RATE_LIMIT_ATTEMPTS,
        window_seconds=settings.MFA_SETUP_RATE_LIMIT_WINDOW,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many MFA setup attempts. Try again later.",
        )

    user = await _get_user_locked(db, user_id)
    if user.mfa_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MFA is already enabled")
    if user.mfa_secret:
        # A pending (not-yet-verified) enrollment already exists. Refusing to
        # overwrite it means a concurrent setup request can never replace the
        # secret another request just issued (#221 finding 4).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA setup already pending; verify the issued secret first",
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
    request_obj: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Enable MFA after a correct TOTP code from the just-provisioned secret."""
    # Rate limit: per-IP + per-user (#241)
    client_ip = request_obj.client.host if request_obj.client else "unknown"
    if not await allow(
        f"mfa:verify:ip:{client_ip}",
        max_requests=settings.MFA_VERIFY_RATE_LIMIT_ATTEMPTS,
        window_seconds=settings.MFA_VERIFY_RATE_LIMIT_WINDOW,
    ) or not await allow(
        f"mfa:verify:user:{user_id}",
        max_requests=settings.MFA_VERIFY_RATE_LIMIT_ATTEMPTS,
        window_seconds=settings.MFA_VERIFY_RATE_LIMIT_WINDOW,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many MFA verification attempts. Try again later.",
        )

    user = await _get_user_locked(db, user_id)
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
    request_obj: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Disable MFA after a valid TOTP code, clearing the stored secret."""
    # Rate limit: per-IP + per-user (#241)
    client_ip = request_obj.client.host if request_obj.client else "unknown"
    if not await allow(
        f"mfa:disable:ip:{client_ip}",
        max_requests=settings.MFA_DISABLE_RATE_LIMIT_ATTEMPTS,
        window_seconds=settings.MFA_DISABLE_RATE_LIMIT_WINDOW,
    ) or not await allow(
        f"mfa:disable:user:{user_id}",
        max_requests=settings.MFA_DISABLE_RATE_LIMIT_ATTEMPTS,
        window_seconds=settings.MFA_DISABLE_RATE_LIMIT_WINDOW,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many MFA disable attempts. Try again later.",
        )

    user = await _get_user_locked(db, user_id)
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
