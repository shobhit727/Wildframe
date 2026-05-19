"""
Service layer containing business logic for authentication.
Orchestrates repositories and applies domain rules.
"""

from typing import Optional, Tuple
from uuid import UUID
import logging
from datetime import datetime, timezone

from app.repositories.user_repository import (
    UserRepository,
    RefreshTokenRepository,
    TokenBlacklistRepository,
    LoginAuditRepository
)
from app.security.manager import PasswordManager, TokenManager, RateLimiter
from app.core.settings import settings

logger = logging.getLogger(__name__)


class AuthService:
    """Service for authentication operations."""
    
    def __init__(
        self,
        user_repo: UserRepository,
        refresh_token_repo: RefreshTokenRepository,
        token_blacklist_repo: TokenBlacklistRepository,
        login_audit_repo: LoginAuditRepository,
        rate_limiter: RateLimiter
    ):
        """Initialize auth service with dependencies.
        
        Args:
            user_repo: User repository
            refresh_token_repo: Refresh token repository
            token_blacklist_repo: Token blacklist repository
            login_audit_repo: Login audit repository
            rate_limiter: Rate limiter instance
        """
        self.user_repo = user_repo
        self.refresh_token_repo = refresh_token_repo
        self.token_blacklist_repo = token_blacklist_repo
        self.login_audit_repo = login_audit_repo
        self.rate_limiter = rate_limiter
    
    async def register(
        self,
        email: str,
        password: str
    ) -> Tuple[dict, str]:
        """Register a new user.
        
        Args:
            email: User email
            password: User password
            
        Returns:
            Tuple of (token_response, refresh_token)
            
        Raises:
            ValueError: If email already exists or rate limited
        """
        # Check rate limit
        rate_key = f"register:{email}"
        if not await self.rate_limiter.is_allowed(
            rate_key,
            settings.REGISTRATION_RATE_LIMIT_ATTEMPTS,
            settings.REGISTRATION_RATE_LIMIT_WINDOW
        ):
            logger.warning(f"Registration rate limit exceeded for {email}")
            raise ValueError("Too many registration attempts. Please try again later.")
        
        # Create user
        user = await self.user_repo.create(email, password)
        
        # Create tokens
        access_token = TokenManager.create_access_token(str(user.id))
        refresh_token = TokenManager.create_refresh_token(str(user.id))
        
        # Store refresh token
        await self.refresh_token_repo.create(
            user.id,
            refresh_token,
            device_id=None
        )
        
        # Log successful registration as login
        await self.login_audit_repo.log(
            email=email,
            status="registered",
            user_id=user.id
        )
        
        logger.info(f"User registered: {email}")
        
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": settings.JWT_EXPIRATION_MINUTES * 60
        }, refresh_token
    
    async def login(
        self,
        email: str,
        password: str,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
        device_id: Optional[str] = None
    ) -> Tuple[dict, str]:
        """Authenticate user and return tokens.
        
        Args:
            email: User email
            password: User password
            user_agent: User agent string
            ip_address: Client IP address
            device_id: Device identifier
            
        Returns:
            Tuple of (token_response, refresh_token)
            
        Raises:
            ValueError: If credentials invalid or account locked
        """
        # Check rate limit
        rate_key = f"login:{email}"
        if not await self.rate_limiter.is_allowed(
            rate_key,
            settings.LOGIN_RATE_LIMIT_ATTEMPTS,
            settings.LOGIN_RATE_LIMIT_WINDOW
        ):
            logger.warning(f"Login rate limit exceeded for {email}")
            await self.login_audit_repo.log(
                email=email,
                status="rate_limited",
                user_agent=user_agent,
                ip_address=ip_address,
                device_id=device_id
            )
            raise ValueError("Too many login attempts. Please try again later.")
        
        # Get user
        user = await self.user_repo.get_by_email(email)
        if not user:
            logger.warning(f"Login attempt for non-existent user: {email}")
            await self.login_audit_repo.log(
                email=email,
                status="user_not_found",
                user_agent=user_agent,
                ip_address=ip_address,
                device_id=device_id
            )
            raise ValueError("Invalid email or password")
        
        # Check if account locked
        if user.is_locked and user.locked_until > datetime.now(timezone.utc):
            logger.warning(f"Login attempt for locked account: {email}")
            await self.login_audit_repo.log(
                email=email,
                status="account_locked",
                user_id=user.id,
                user_agent=user_agent,
                ip_address=ip_address,
                device_id=device_id
            )
            raise ValueError("Account is temporarily locked. Please try again later.")
        
        # Verify password
        if not PasswordManager.verify_password(password, user.password_hash):
            logger.warning(f"Failed login attempt for user: {email}")
            await self.user_repo.update_login_attempt(user)
            await self.login_audit_repo.log(
                email=email,
                status="failed_password",
                user_id=user.id,
                user_agent=user_agent,
                ip_address=ip_address,
                device_id=device_id
            )
            
            # Lock account after max attempts
            if user.login_attempts >= settings.LOGIN_RATE_LIMIT_ATTEMPTS:
                await self.user_repo.lock_account(user, hours=1)
            
            raise ValueError("Invalid email or password")
        
        # Check if account active
        if not user.is_active:
            logger.warning(f"Login attempt for inactive account: {email}")
            await self.login_audit_repo.log(
                email=email,
                status="account_inactive",
                user_id=user.id,
                user_agent=user_agent,
                ip_address=ip_address,
                device_id=device_id
            )
            raise ValueError("Account is inactive")
        
        # Reset login attempts and update last login
        await self.user_repo.reset_login_attempts(user)
        await self.rate_limiter.reset(rate_key)
        
        # Create tokens
        access_token = TokenManager.create_access_token(str(user.id))
        refresh_token = TokenManager.create_refresh_token(str(user.id), device_id)
        
        # Store refresh token
        await self.refresh_token_repo.create(
            user.id,
            refresh_token,
            device_id=device_id,
            user_agent=user_agent,
            ip_address=ip_address
        )
        
        # Log successful login
        await self.login_audit_repo.log(
            email=email,
            status="success",
            user_id=user.id,
            user_agent=user_agent,
            ip_address=ip_address,
            device_id=device_id
        )
        
        logger.info(f"User logged in: {email}")
        
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": settings.JWT_EXPIRATION_MINUTES * 60
        }, refresh_token
    
    async def refresh_access_token(self, refresh_token: str) -> Tuple[dict, str]:
        """Get a new access token using refresh token.
        
        Args:
            refresh_token: Refresh token
            
        Returns:
            Tuple of (token_response, new_refresh_token)
            
        Raises:
            ValueError: If refresh token invalid or expired
        """
        # Verify refresh token
        payload = TokenManager.verify_token(refresh_token, token_type="refresh")
        if not payload:
            raise ValueError("Invalid or expired refresh token")
        
        user_id = UUID(payload["sub"])
        
        # Get refresh token from database
        token_hash = TokenManager.hash_token(refresh_token)
        db_token = await self.refresh_token_repo.get_by_hash(token_hash)
        
        if not db_token or db_token.revoked_at is not None:
            logger.warning(f"Refresh token refresh attempt with invalid/revoked token for user: {user_id}")
            raise ValueError("Refresh token is no longer valid")
        
        # Create new access token
        new_access_token = TokenManager.create_access_token(str(user_id))
        
        # Optionally rotate refresh token
        new_refresh_token = TokenManager.create_refresh_token(str(user_id))
        await self.refresh_token_repo.create(
            user_id,
            new_refresh_token,
            device_id=db_token.device_id
        )
        
        logger.info(f"Access token refreshed for user: {user_id}")
        
        return {
            "access_token": new_access_token,
            "token_type": "bearer",
            "expires_in": settings.JWT_EXPIRATION_MINUTES * 60
        }, new_refresh_token
    
    async def logout(self, access_token: str, user_id: UUID) -> None:
        """Logout user by blacklisting tokens.
        
        Args:
            access_token: Access token to blacklist
            user_id: User ID
        """
        # Verify token
        payload = TokenManager.verify_token(access_token, token_type="access")
        if not payload:
            raise ValueError("Invalid access token")
        
        # Add to blacklist
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=settings.JWT_EXPIRATION_MINUTES
        )
        
        await self.token_blacklist_repo.add(
            access_token,
            user_id,
            expires_at,
            reason="logout"
        )
        
        # Revoke all refresh tokens
        await self.refresh_token_repo.revoke_all_for_user(user_id)
        
        logger.info(f"User logged out: {user_id}")
