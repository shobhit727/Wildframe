"""API endpoints for Auth Service."""

import logging
from typing import Annotated
from uuid import UUID

from app.core.database import get_db_session
from app.repositories import (
    LoginAuditRepository,
    RefreshTokenRepository,
    TokenBlacklistRepository,
    UserRepository,
)
from app.schemas import (
    ChangePasswordRequest,
    RefreshTokenRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.security import PasswordManager, TokenManager
from app.services import AuthService
from jose.exceptions import JWTError  # type: ignore[attr-defined]
 
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.auth import router as auth_router
from app.api.routes.privacy import router as privacy_router

logger = logging.getLogger(__name__)

# Create router
router = APIRouter()

router.include_router(auth_router)
router.include_router(privacy_router)


# Dependencies
async def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthService:
    """Dependency to get AuthService instance."""
    user_repo = UserRepository(session)
    token_repo = RefreshTokenRepository(session)
    audit_repo = LoginAuditRepository(session)
    password_manager = PasswordManager()
    token_manager = TokenManager()

    return AuthService(
        user_repo=user_repo,
        token_repo=token_repo,
        audit_repo=audit_repo,
        password_manager=password_manager,
        token_manager=token_manager,
        blacklist_repo=TokenBlacklistRepository(session),
    )


async def get_current_user_id(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> str:
    """Extract user ID from JWT token in Authorization header.

    Rejects blacklisted (revoked) access tokens the same way
    ``get_current_user`` in auth.py does.
    """
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header[7:]  # Remove "Bearer " prefix

    try:
        # Reject revoked access tokens before anything else.
        token_blacklist_repo = TokenBlacklistRepository(db)
        if await token_blacklist_repo.is_blacklisted(TokenManager.hash_token(token)):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token_manager = TokenManager()
        try:
            payload = token_manager.verify_token(token, token_type="access")
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_id = payload.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Access tokens minted before the last credential rotation carry an
        # older "av" claim and are rejected immediately (#79/#99).
        user = await UserRepository(db).get_by_id(UUID(user_id))
        if user is None or int(payload.get("av", 0)) != user.auth_version:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return str(user_id)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Token verification failed: {e!s}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# Endpoints
@router.post(
    "/auth/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Authentication"],
    summary="Register new user",
    description="Create a new user account with email and password",
)
async def register(
    request: UserRegisterRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserResponse:
    """Register new user.

    Args:
        request: Registration request with email, password, first_name, last_name
        auth_service: Injected authentication service

    Returns:
        UserResponse: Created user profile

    Raises:
        HTTPException: If user already exists or validation fails
    """
    return await auth_service.register(request)


@router.post(
    "/auth/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    tags=["Authentication"],
    summary="User login",
    description="Authenticate user and return access/refresh tokens",
)
async def login(
    request: Request,
    login_request: UserLoginRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    """Authenticate user and return tokens.

    Args:
        request: HTTP request (for IP address extraction)
        login_request: Login credentials
        auth_service: Injected authentication service

    Returns:
        TokenResponse: Access and refresh tokens

    Raises:
        HTTPException: If credentials are invalid or account is locked
    """
    # Extract client IP address
    client_ip = request.client.host if request.client else "unknown"

    return await auth_service.login(login_request, client_ip)


@router.post(
    "/auth/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    tags=["Authentication"],
    summary="Refresh access token",
    description="Use refresh token to get new access token",
)
async def refresh_token(
    request: RefreshTokenRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    """Refresh access token using refresh token.

    Args:
        request: Refresh token request
        auth_service: Injected authentication service

    Returns:
        TokenResponse: New access and refresh tokens

    Raises:
        HTTPException: If refresh token is invalid or expired
    """
    return await auth_service.refresh_token(request.refresh_token)


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Authentication"],
    summary="User logout",
    description="Revoke refresh token and logout user",
)
async def logout(
    request: RefreshTokenRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> None:
    """Logout user by revoking refresh token.

    Args:
        request: Refresh token to revoke
        auth_service: Injected authentication service

    Raises:
        HTTPException: If logout fails
    """
    success = await auth_service.logout(request.refresh_token)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to logout",
        )


@router.get(
    "/users/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    tags=["Users"],
    summary="Get current user",
    description="Retrieve current user profile",
)
async def get_current_user(
    user_id: Annotated[str, Depends(get_current_user_id)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserResponse:
    """Get current user profile.

    Args:
        user_id: User ID extracted from JWT token
        auth_service: Injected authentication service

    Returns:
        UserResponse: Current user profile

    Raises:
        HTTPException: If user not found
    """
    from uuid import UUID

    return await auth_service.get_current_user(UUID(user_id))


@router.post(
    "/users/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Users"],
    summary="Change password",
    description="Change user password (requires current password)",
)
async def change_password(
    request: ChangePasswordRequest,
    user_id: Annotated[str, Depends(get_current_user_id)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> None:
    """Change user password.

    Args:
        request: Old and new password
        user_id: User ID from JWT token
        auth_service: Injected authentication service

    Raises:
        HTTPException: If old password is incorrect
    """
    from uuid import UUID

    await auth_service.change_password(
        UUID(user_id),
        request.current_password,
        request.new_password,
    )
