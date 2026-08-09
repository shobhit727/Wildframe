"""Service layer for Auth Service."""

import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.repositories import (
    LoginAuditRepository,
    RefreshTokenRepository,
    UserRepository,
)
from app.schemas import (
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.security import PasswordManager, TokenManager
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

            # Increment failed attempts
            user = await self.user_repo.increment_login_attempts(user.id)

            # Lock user if too many attempts
            if user.login_attempts >= self.max_login_attempts:
                locked_until = datetime.now(UTC) + timedelta(minutes=self.lockout_minutes)
                user = await self.user_repo.update(user.id, locked_until=locked_until)

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
        user = await self.user_repo.update(user.id, last_login_at=datetime.now(UTC))
        await self.user_repo.commit()

        # Create audit record
        await self.audit_repo.create(
            user_id=user.id,
            status="success",
            ip_address=ip_address,
        )
        await self.audit_repo.commit()

        # MFA gate: password verified, but the user must prove TOTP possession
        # before any tokens are issued.
        if getattr(user, "mfa_enabled", False):
            challenge = self.token_manager.create_mfa_challenge_token(
                user.id, getattr(user, "email", "")
            )
            logger.info(f"Login awaiting MFA challenge for user: {user.email}")
            raise MfaChallengeRequired(challenge)

        # Generate tokens
        access_token = self.token_manager.create_access_token(
            str(user.id), getattr(user, "email", "")
        )
        (
            refresh_token_str,
            refresh_token_hash,
            expires_at,
        ) = self.token_manager.create_refresh_token_for_user(user)

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
            token_type="bearer",
        )

    async def complete_mfa_login(
        self, challenge_token: str, code: str, ip_address: str
    ) -> TokenResponse:
        """Complete a password-verified login with a valid TOTP code.

        Verifies the short-lived ``mfa_challenge`` token (proof that the
        password check already passed), then validates the TOTP code before
        issuing real access/refresh tokens.
        """
        user_id = self.token_manager.verify_mfa_challenge(challenge_token)
        if not user_id:
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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid MFA code",
            )

        # MFA passed — issue tokens (mirrors the tail of login()).
        access_token = self.token_manager.create_access_token(
            str(user.id), getattr(user, "email", "")
        )
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
        access_token = self.token_manager.create_access_token(
            str(user.id), getattr(user, "email", "")
        )
        (
            new_refresh_token,
            new_token_hash,
            new_expires_at,
        ) = self.token_manager.create_refresh_token_for_user(user)

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

        if datetime.now(UTC) > expires_at:
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

    async def setup_mfa(self, user_id: UUID) -> dict:
        """Setup MFA for user - generate TOTP secret and QR code."""
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        if user.mfa_enabled:
            return {"message": "MFA already enabled"}

        # Generate TOTP secret
        secret = secrets.token_urlsafe(20)[:32]

        # Generate backup codes
        backup_codes = [secrets.token_hex(4).upper() for _ in range(10)]

        # Store secret and backup codes (encrypted in production)
        user.mfa_secret = secret
        user.backup_codes = json.dumps(backup_codes)
        await self.user_repo.commit()

        # Generate provisioning URI for QR code
        import urllib.parse

        totp_uri = (
            f"otpauth://totp/{urllib.parse.quote('Wildframe')}:"
            f"{urllib.parse.quote(user.email)}?"
            f"secret={secret}&issuer={urllib.parse.quote('Wildframe')}"
        )

        logger.info(f"MFA setup initiated for user: {user.email}")
        return {
            "secret": secret,
            "totp_uri": totp_uri,
            "backup_codes": backup_codes,
        }

    async def verify_mfa(self, user_id: UUID, code: str) -> dict:
        """Verify MFA code and enable MFA."""
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        if user.mfa_enabled:
            return {"message": "MFA already enabled"}

        if not user.mfa_secret:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA not set up",
            )

        # Verify TOTP code
        import pyotp

        totp = pyotp.TOTP(user.mfa_secret)
        if not totp.verify(code, valid_window=1):
            # Check backup codes
            if user.backup_codes:
                backup_codes = json.loads(user.backup_codes)
                if code.upper() in backup_codes:
                    # Remove used backup code
                    backup_codes.remove(code.upper())
                    user.backup_codes = json.dumps(backup_codes)
                else:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid MFA code",
                    )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid MFA code",
                )

        # Enable MFA
        user.mfa_enabled = True
        await self.user_repo.commit()

        logger.info(f"MFA enabled for user: {user.email}")
        return {"message": "MFA enabled successfully"}

    async def disable_mfa(self, user_id: UUID) -> dict:
        """Disable MFA for user."""
        user = await self.user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        user.mfa_enabled = False
        user.mfa_secret = None
        user.backup_codes = None
        await self.user_repo.commit()

        logger.info(f"MFA disabled for user: {user.email}")
        return {"message": "MFA disabled successfully"}
