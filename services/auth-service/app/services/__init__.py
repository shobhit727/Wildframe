"""Service layer for Auth Service."""
from typing import Optional, Tuple
from uuid import UUID, uuid4
from datetime import datetime, timedelta
import logging
from fastapi import HTTPException, status

from app.repositories import UserRepository, RefreshTokenRepository, LoginAuditRepository
from app.security import PasswordManager, TokenManager
from app.schemas import (
    TokenResponse,
    UserRegisterRequest,
    UserLoginRequest,
    UserResponse,
)

logger = logging.getLogger(__name__)


class AuthService:
    """Business logic for authentication."""

    def __init__(
        self,
        user_repo: UserRepository,
        token_repo: RefreshTokenRepository,
        audit_repo: LoginAuditRepository,
        password_manager: PasswordManager,
        token_manager: TokenManager,
    ):
        self.user_repo = user_repo
        self.token_repo = token_repo
        self.audit_repo = audit_repo
        self.password_manager = password_manager
        self.token_manager = token_manager
        self.max_login_attempts = 5
        self.lockout_minutes = 15

    async def register(
        self,
        request: UserRegisterRequest,
    ) -> UserResponse:
        """Register new user."""
        # Check if user exists
        existing_user = await self.user_repo.get_by_email(request.email)
        if existing_user:
            logger.warning(f"Registration failed: user already exists: {request.email}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User with this email already exists",
            )

        # Hash password
        password_hash = self.password_manager.hash_password(request.password)

        # Create user
        try:
            user = await self.user_repo.create(
                email=request.email,
                password_hash=password_hash,
                first_name=request.first_name,
                last_name=request.last_name,
            )
            await self.user_repo.commit()
            logger.info(f"User registered: {request.email}")
            return UserResponse.from_orm(user)
        except Exception as e:
            logger.error(f"Registration error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create user",
            )

    async def login(
        self,
        request: UserLoginRequest,
        ip_address: str,
    ) -> TokenResponse:
        """Authenticate user and return tokens."""
        # Get user
        user = await self.user_repo.get_by_email(request.email)

        if not user:
            logger.warning(f"Login failed: user not found: {request.email}")
            await self.audit_repo.create(
                user_id=UUID(int=0),  # Unknown user
                status="failed",
                ip_address=ip_address,
            )
            await self.audit_repo.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        # Check if user is locked
        if user.locked_until and user.locked_until > datetime.utcnow():
            logger.warning(f"Login failed: user locked: {user.email}")
            await self.audit_repo.create(
                user_id=user.id,
                status="locked",
                ip_address=ip_address,
            )
            await self.audit_repo.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Account temporarily locked due to too many failed attempts",
            )

        # Verify password
        if not self.password_manager.verify_password(
            request.password, user.password_hash
        ):
            logger.warning(f"Login failed: invalid password: {user.email}")
            
            # Increment failed attempts
            user = await self.user_repo.increment_login_attempts(user.id)
            
            # Lock user if too many attempts
            if user.login_attempts >= self.max_login_attempts:
                locked_until = datetime.utcnow() + timedelta(minutes=self.lockout_minutes)
                user = await self.user_repo.update(
                    user.id,
                    locked_until=locked_until
                )
            
            await self.user_repo.commit()
            
            await self.audit_repo.create(
                user_id=user.id,
                status="failed",
                ip_address=ip_address,
            )
            await self.audit_repo.commit()
            
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        # Reset login attempts on successful login
        await self.user_repo.reset_login_attempts(user.id)
        user = await self.user_repo.update(
            user.id,
            last_login_at=datetime.utcnow()
        )
        await self.user_repo.commit()

        # Create audit record
        await self.audit_repo.create(
            user_id=user.id,
            status="success",
            ip_address=ip_address,
        )
        await self.audit_repo.commit()

        # Generate tokens
        access_token = self.token_manager.create_access_token(user)
        refresh_token_str, refresh_token_hash, expires_at = (
            self.token_manager.create_refresh_token(user)
        )

        # Store refresh token
        await self.token_repo.create(
            user_id=user.id,
            token_hash=refresh_token_hash,
            expires_at=expires_at,
        )
        await self.token_repo.commit()

        logger.info(f"User logged in: {user.email}")
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token_str,
            expires_in=900,  # 15 minutes
            token_type="Bearer",
        )

    async def refresh_token(self, refresh_token: str) -> TokenResponse:
        """Refresh access token."""
        # Verify and decode refresh token
        user_id = self.token_manager.verify_refresh_token(refresh_token)
        if not user_id:
            logger.warning("Token refresh failed: invalid token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

        # Get user
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            logger.warning(f"Token refresh failed: user not found: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        # Get stored refresh token
        token_hash = self.token_manager.hash_refresh_token(refresh_token)
        stored_token = await self.token_repo.get_by_token_hash(token_hash)
        if not stored_token:
            logger.warning(f"Token refresh failed: token not stored: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token not found",
            )

        # Revoke old refresh token
        await self.token_repo.revoke(token_hash)
        await self.token_repo.commit()

        # Create new tokens
        access_token = self.token_manager.create_access_token(user)
        new_refresh_token, new_token_hash, new_expires_at = (
            self.token_manager.create_refresh_token(user)
        )

        # Store new refresh token
        await self.token_repo.create(
            user_id=user.id,
            token_hash=new_token_hash,
            expires_at=new_expires_at,
        )
        await self.token_repo.commit()

        logger.info(f"Token refreshed for user: {user.email}")
        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh_token,
            expires_in=900,  # 15 minutes
            token_type="Bearer",
        )

    async def logout(self, refresh_token: str) -> bool:
        """Logout user by revoking refresh token."""
        try:
            token_hash = self.token_manager.hash_refresh_token(refresh_token)
            success = await self.token_repo.revoke(token_hash)
            await self.token_repo.commit()
            
            if success:
                logger.info("User logged out")
            return success
        except Exception as e:
            logger.error(f"Logout error: {str(e)}")
            return False

    async def get_current_user(self, user_id: UUID) -> UserResponse:
        """Get current user profile."""
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        return UserResponse.from_orm(user)

    async def change_password(
        self,
        user_id: UUID,
        old_password: str,
        new_password: str,
    ) -> bool:
        """Change user password."""
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        # Verify old password
        if not self.password_manager.verify_password(old_password, user.password_hash):
            logger.warning(f"Password change failed: invalid password: {user.email}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid password",
            )

        # Hash new password
        new_hash = self.password_manager.hash_password(new_password)

        # Update password
        await self.user_repo.update(user_id, password_hash=new_hash)
        await self.user_repo.commit()

        logger.info(f"Password changed for user: {user.email}")
        return True
