"""Service layer for Auth Service."""

import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.repositories import (
    LoginAuditRepository,
    RefreshTokenRepository,
    TokenBlacklistRepository,
    UserRepository,
)
from app.schemas import (
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.security import PasswordManager, TokenManager
from app.core.settings import settings
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


class MfaChallengeRequired(Exception):
    """Raised when login succeeded (password) but MFA proof is pending."""

    def __init__(self, challenge_token: str):
        self.challenge_token = challenge_token
        super().__init__("MFA verification required")


class AuthService:
    """Business logic for authentication."""

    def __init__(
        self,
        user_repo: UserRepository,
        token_repo: RefreshTokenRepository,
        audit_repo: LoginAuditRepository,
        password_manager: PasswordManager,
        token_manager: TokenManager,
        blacklist_repo: TokenBlacklistRepository | None = None,
    ):
        self.user_repo = user_repo
        self.token_repo = token_repo
        self.audit_repo = audit_repo
        self.password_manager = password_manager
        self.token_manager = token_manager
        self.blacklist_repo = blacklist_repo
        self.max_login_attempts = settings.LOGIN_RATE_LIMIT_ATTEMPTS
        self.base_lockout_minutes = 15
        self.max_lockout_hours = 24

    async def register(
        self,
        request: UserRegisterRequest,
    ) -> UserResponse:
        """Register new user with password policy enforcement (#164/#224)."""
        from app.security import canonicalize_email, check_password_strength

        # Canonicalize email (#161/#186)
        canonical_email = canonicalize_email(request.email)

        # Check password strength
        strength_error = check_password_strength(request.password)
        if strength_error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=strength_error,
            )

        # Check if user exists (using canonical email)
        existing_user = await self.user_repo.get_by_email(canonical_email)
        if existing_user:
            logger.warning(f"Registration failed: user already exists: {canonical_email}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User with this email already exists",
            )

        # Hash password (enforces 72-byte limit via _encode_password)
        password_hash = self.password_manager.hash_password(request.password)

        # Create user with canonical email
        try:
            user = await self.user_repo.create(
                email=canonical_email,
                password_hash=password_hash,
                first_name=request.first_name,
                last_name=request.last_name,
            )
            await self.user_repo.commit()
            logger.info(f"User registered: {canonical_email}")
            return UserResponse.from_orm(user)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Registration error: {e!s}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create user",
            )

    async def login(
        self,
        request: UserLoginRequest,
        ip_address: str,
    ) -> TokenResponse:
        """Authenticate user and return tokens with exponential backoff lockout (#131)."""
        from app.security import canonicalize_email

        # Canonicalize email (#161/#186)
        canonical_email = canonicalize_email(request.email)

        # Get user
        user = await self.user_repo.get_by_email(canonical_email)

        if not user:
            logger.warning(f"Login failed: user not found: {canonical_email}")
            await self.audit_repo.create(
                user_id=UUID(int=0),  # Unknown user
                status="failed",
                ip_address=ip_address,
            )
            await self.audit_repo.commit()
            # Constant-time dummy hash to equalize timing (#163/#436)
            self.password_manager.dummy_hash()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        # Check if user is locked
        if user.locked_until and user.locked_until > datetime.now(UTC):
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
        if not self.password_manager.verify_password(request.password, user.password_hash):
            logger.warning(f"Login failed: invalid password: {user.email}")

            # Atomic increment with exponential backoff (#131/#278)
            user, locked = await self.user_repo.increment_login_attempts_atomic(
                user.id,
                max_attempts=self.max_login_attempts,
                base_lockout_minutes=self.base_lockout_minutes,
                max_lockout_hours=self.max_lockout_hours,
            )

            await self.audit_repo.create(
                user_id=user.id if user else UUID(int=0),
                status="failed",
                ip_address=ip_address,
            )
            await self.audit_repo.commit()

            # Constant-time dummy hash to equalize timing (#163/#436)
            self.password_manager.dummy_hash()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        # Password correct — check if rehash needed (#437)
        if self.password_manager.needs_rehash(user.password_hash):
            user.password_hash = self.password_manager.hash_password(request.password)
            await self.user_repo.flush()
            logger.info(f"Password rehashed for user: {user.email}")

        # Reset login attempts on successful login
        await self.user_repo.reset_login_attempts(user.id)
        user = await self.user_repo.update(user.id, last_login_at=datetime.now(UTC), last_login_ip=ip_address)
        assert user is not None
        await self.user_repo.commit()

        # Create audit record
        await self.audit_repo.create(
            user_id=user.id,
            status="success",
            ip_address=ip_address,
        )
        await self.audit_repo.commit()

        # MFA gate: password verified, but the user must prove TOTP possession
        if user.mfa_enabled:
            challenge = self.token_manager.create_mfa_challenge_token(user.id, user.email)
            logger.info(f"Login awaiting MFA challenge for user: {user.email}")
            raise MfaChallengeRequired(challenge)

        # Generate tokens with token_version (#79/#81)
        access_token = self.token_manager.create_access_token(user.id, user.email, user.token_version)
        (
            refresh_token_str,
            refresh_token_hash,
            expires_at,
        ) = self.token_manager.create_refresh_token_for_user(user)

        # Store refresh token with family tracking (#183/#440)
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
            token_type="bearer",
        )

    async def complete_mfa_login(
        self, challenge_token: str, code: str, ip_address: str
    ) -> TokenResponse:
        """Complete a password-verified login with a valid TOTP code."""
        user_id = self.token_manager.verify_mfa_challenge(challenge_token)
        if not user_id:
            # Constant-time dummy hash on failure (#163/#436)
            self.password_manager.dummy_hash()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired MFA challenge",
            )

        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )
        if not getattr(user, "mfa_enabled", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is not enabled for this user",
            )

        import pyotp
        from app.security import SecretCipher

        secret = SecretCipher.decrypt(user.mfa_secret) if user.mfa_secret else ""
        if not secret or not pyotp.TOTP(secret).verify(code, valid_window=1):
            # Constant-time dummy hash on failure (#163/#436)
            self.password_manager.dummy_hash()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid MFA code",
            )

        # Single-use challenge (#221): atomically consume the challenge
        if self.blacklist_repo is not None:
            challenge_hash = self.token_manager.hash_token(challenge_token)
            if await self.blacklist_repo.is_blacklisted(challenge_hash):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired MFA challenge",
                )
            from sqlalchemy.exc import IntegrityError

            try:
                await self.blacklist_repo.create(
                    token_hash=challenge_hash,
                    user_id=user_id,
                    expires_at=datetime.now(UTC)
                    + timedelta(minutes=settings.MFA_CHALLENGE_EXPIRATION_MINUTES),
                )
                await self.blacklist_repo.commit()
            except IntegrityError:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired MFA challenge",
                )

        # MFA passed — issue tokens with token_version
        access_token = self.token_manager.create_access_token(user.id, user.email, user.token_version)
        (
            refresh_token_str,
            refresh_token_hash,
            expires_at,
        ) = self.token_manager.create_refresh_token_for_user(user)

        await self.token_repo.create(
            user_id=user.id,
            token_hash=refresh_token_hash,
            expires_at=expires_at,
        )
        await self.token_repo.commit()

        logger.info(f"MFA login completed for user: {user.email}")
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token_str,
            expires_in=900,  # 15 minutes
            token_type="bearer",
        )

    async def refresh_token(self, refresh_token: str) -> TokenResponse:
        """Refresh access token with atomic rotation and reuse detection (#183/#440)."""
        # Verify and decode refresh token
        user_id = self.token_manager.verify_refresh_token(refresh_token)
        if not user_id:
            logger.warning("Token refresh failed: invalid token")
            # Constant-time dummy hash on failure (#163/#436)
            self.password_manager.dummy_hash()
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
            # Constant-time dummy hash on failure (#163/#436)
            self.password_manager.dummy_hash()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token not found",
            )

        # Atomic rotation with reuse detection (#183/#440)
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRATION_DAYS)
        new_refresh_token = self.token_manager.create_refresh_token(user.id)
        new_token_hash = self.token_manager.hash_refresh_token(new_refresh_token)

        rotated = await self.token_repo.rotate_atomic(
            old_token_hash=token_hash,
            new_token_hash=new_token_hash,
            expires_at=expires_at,
        )

        if rotated is None:
            # Reuse detected — family already revoked
            logger.warning(f"Refresh token reuse detected for user: {user.email}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

        # Create new access token with current token_version
        access_token = self.token_manager.create_access_token(user.id, user.email, user.token_version)

        await self.token_repo.commit()

        logger.info(f"Token refreshed for user: {user.email}")
        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh_token,
            expires_in=900,  # 15 minutes
            token_type="bearer",
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
        except Exception as e:  # noqa: BLE001
            logger.error(f"Logout error: {e!s}")
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
        """Change user password with token_version increment (#79/#81)."""
        from app.security import check_password_strength

        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        # Verify old password
        if not self.password_manager.verify_password(old_password, user.password_hash):
            logger.warning(f"Password change failed: invalid password: {user.email}")
            self.password_manager.dummy_hash()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid password",
            )

        # Check new password strength
        strength_error = check_password_strength(new_password)
        if strength_error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=strength_error,
            )

        # Hash new password
        new_hash = self.password_manager.hash_password(new_password)

        # Update password
        await self.user_repo.update(user_id, password_hash=new_hash)
        await self.user_repo.commit()

        # Increment token_version to invalidate all existing access tokens (#79/#81)
        await self.user_repo.increment_token_version(user_id)
        await self.user_repo.commit()

        # Security policy: a password change invalidates all existing
        # sessions — every refresh token for the user is revoked
        revoked = await self.token_repo.revoke_all_for_user(user_id)
        await self.token_repo.commit()
        logger.info(f"Password changed for user: {user.email}; revoked {revoked} refresh token(s); token_version incremented")
        return True

    async def send_email_verification(self, user_id: UUID) -> dict:
        """Send email verification code to user."""
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        if user.email_verified:
            return {"message": "Email already verified"}

        # Generate verification code
        code = f"{secrets.randbelow(1000000):06d}"
        expires_at = datetime.now(UTC) + timedelta(hours=24)

        # Store code in user record with dedicated fields
        user.email_verification_code = code
        user.email_verification_code_expires_at = expires_at
        await self.user_repo.commit()

        # TODO: Send email with code
        logger.info(f"Email verification code sent to {user.email}: {code}")

        return {"message": "Verification code sent"}

    async def verify_email(self, user_id: UUID, code: str) -> dict:
        """Verify email verification code."""
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        if user.email_verified:
            return {"message": "Email already verified"}

        # Check code
        stored_code = user.email_verification_code
        expires_at = user.email_verification_code_expires_at

        if not stored_code or stored_code != code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid verification code",
            )

        if expires_at is None or datetime.now(UTC) > expires_at:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification code expired",
            )

        # Mark email as verified
        user.email_verified = True
        user.email_verified_at = datetime.now(UTC)
        user.email_verification_code = None  # Clear code
        user.email_verification_code_expires_at = None  # Clear expiry
        await self.user_repo.commit()

        logger.info(f"Email verified for user: {user.email}")
        return {"message": "Email verified successfully"}
