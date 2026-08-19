"""
Security utilities for Auth Service.
Implements JWT token handling, password hashing, and validation.
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
import bcrypt
from app.core.settings import settings
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from app.models import User

logger = logging.getLogger(__name__)


# Common password blocklist (top ~50 breached passwords) — #164
COMMON_PASSWORDS: frozenset[str] = frozenset({
    "password", "123456", "123456789", "guest", "qwerty", "12345678",
    "111111", "12345", "col123456", "123123", "1234567", "1234",
    "1234567890", "123456a", "abc123", "password1", "123456789a",
    "letmein", "admin", "welcome", "monkey", "dragon", "master",
    "hello", "freedom", "whatever", "qazwsx", "trustno1", "654321",
    "666666", "123321", "mustang", "michael", "shadow", "sunshine",
    "iloveyou", "football", "superman", "123qwe", "starwars", "jordan",
    "charlie", "andrew", "michelle", "love", "secret", "jennifer",
})

# Bcrypt input limit — #224
BCRYPT_MAX_BYTES: int = 72


def _jwt_secret() -> str:
    """Return the configured JWT secret (validated non-None at boot)."""
    secret = settings.JWT_SECRET_KEY
    assert secret is not None, "JWT_SECRET_KEY is not configured"
    return secret


# JWT key set support (#138): list of {"kid": "...", "secret": "..."}
# Current key is first; previous key(s) accepted for verification overlap.
def _jwt_key_set() -> list[dict[str, str]]:
    """Return list of valid JWT keys for verification.

    Keys are read from settings.JWT_KEYS (JSON list of {"kid", "secret"}).
    Falls back to single-key mode using JWT_SECRET_KEY for backward compat.
    """
    raw = getattr(settings, "JWT_KEYS", None)
    if raw:
        try:
            keys = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(keys, list) and keys:
                return keys
        except (json.JSONDecodeError, TypeError):
            pass
    # Fallback: single key with deterministic kid
    return [{"kid": "default", "secret": _jwt_secret()}]


PASSWORD_MAX_LENGTH: int = 128


def _encode_password(password: str) -> bytes:
    """Validate password length and encode for bcrypt.

    Raises ValueError if password exceeds BCRYPT_MAX_BYTES when encoded
    instead of silently truncating. Also enforces PASSWORD_MAX_LENGTH chars.
    """
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"password exceeds maximum length of {PASSWORD_MAX_LENGTH} characters")
    encoded = password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_BYTES:
        raise ValueError(f"password exceeds bcrypt limit of {BCRYPT_MAX_BYTES} bytes")
    return encoded


def canonicalize_email(email: str) -> str:
    """Canonicalize email per #161/#186: NFKC normalize + lowercase.

    Args:
        email: Raw email string

    Returns:
        Canonicalized email
    """
    if not email:
        return ""
    # NFKC normalization handles compatibility characters, then lowercase
    return unicodedata.normalize("NFKC", email.strip()).lower()


def check_password_strength(password: str) -> str | None:
    """Validate password against policy and common password blocklist.

    Returns error message string if invalid, None if valid.
    """
    if len(password) < 12:
        return "Password must be at least 12 characters"
    if password in COMMON_PASSWORDS:
        return "Password is too common; choose a stronger password"
    if not any(c.isupper() for c in password):
        return "Password must contain an uppercase letter"
    if not any(c.isdigit() for c in password):
        return "Password must contain a digit"
    if not any(not c.isalnum() for c in password):
        return "Password must contain a special character"
    return None


def role_for_email(email: str | None) -> str:
    """Return the role for an email based on the ADMIN_EMAILS allow-list."""
    if not email:
        return "user"
    admins = {a.strip().lower() for a in settings.ADMIN_EMAILS.split(",") if a.strip()}
    return "admin" if canonicalize_email(email) in admins else "user"

class PasswordManager:
    """Manages password hashing and verification."""

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using bcrypt.

        Args:
            password: Plain text password

        Returns:
            str: Hashed password
        """
        salt = bcrypt.gensalt(rounds=settings.PASSWORD_BCRYPT_ROUNDS)
        return bcrypt.hashpw(_encode_password(password), salt).decode("utf-8")

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash.

        Args:
            plain_password: Plain text password to verify
            hashed_password: Hashed password to verify against

        Returns:
            bool: True if passwords match, False otherwise
        """
        try:
            return bcrypt.checkpw(
                _encode_password(plain_password),
                hashed_password.encode("utf-8"),
            )
        except (ValueError, TypeError):
            return False

    @staticmethod
    def needs_rehash(hashed_password: str) -> bool:
        """Check if hash uses fewer rounds than current config (#437).

        Args:
            hashed_password: Existing bcrypt hash

        Returns:
            bool: True if rehash needed
        """
        try:
            # bcrypt hash format: $2b$12$... where 12 is the rounds
            parts = hashed_password.split("$")
            if len(parts) >= 3 and parts[1] in ("2a", "2b", "2y"):
                stored_rounds = int(parts[2])
                return stored_rounds < settings.PASSWORD_BCRYPT_ROUNDS
        except (ValueError, IndexError):
            pass
        return False

    @staticmethod
    def dummy_hash() -> None:
        """Run bcrypt against a dummy hash to equalize timing (#163/#436).

        Call this on any authentication failure path (user not found,
        wrong password, invalid token, etc.) to prevent timing attacks.
        """
        bcrypt.checkpw(b"dummy", b"$2b$12$dummydummydummydummydummydu")

    @staticmethod
    def get_hash_rounds(hashed_password: str) -> int | None:
        """Extract bcrypt rounds from hash for inspection."""
        try:
            parts = hashed_password.split("$")
            if len(parts) >= 3 and parts[1] in ("2a", "2b", "2y"):
                return int(parts[2])
        except (ValueError, IndexError):
            pass
        return None
class TokenManager:
    """Manages JWT token generation and validation."""

    @staticmethod
    def _current_kid() -> str:
        """Get current key ID from key set."""
        keys = _jwt_key_set()
        return keys[0]["kid"] if keys else "default"

    @staticmethod
    def _signing_secret() -> str:
        """Get current signing secret."""
        keys = _jwt_key_set()
        return keys[0]["secret"] if keys else _jwt_secret()

    @staticmethod
    def create_access_token(user_id: UUID, email: str, token_version: int = 0) -> str:
        """Create JWT access token.

        Args:
            user_id: User ID
            email: User email
            token_version: User token version for revocation (#79/#81)

        Returns:
            str: JWT access token
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
        canonical_email = canonicalize_email(email)

        payload = {
            "sub": str(user_id),
            "user_id": str(user_id),
            "email": canonical_email,
            "role": role_for_email(canonical_email),
            "type": "access",
            "iat": now,
            "exp": expires_at,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "jti": f"access_{user_id}_{now.timestamp()}",
            "tv": token_version,
            "kid": TokenManager._current_kid(),
        }

        token = jwt.encode(
            payload,
            TokenManager._signing_secret(),
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": TokenManager._current_kid()},
        )
        return token

    @staticmethod
    def create_refresh_token(user_id: UUID) -> str:
        """Create JWT refresh token.

        Args:
            user_id: User ID

        Returns:
            str: JWT refresh token
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRATION_DAYS)

        payload = {
            "sub": str(user_id),
            "user_id": str(user_id),
            "type": "refresh",
            "iat": now,
            "exp": expires_at,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "jti": f"refresh_{user_id}_{now.timestamp()}",
            "kid": TokenManager._current_kid(),
        }

        token = jwt.encode(
            payload,
            TokenManager._signing_secret(),
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": TokenManager._current_kid()},
        )
        return token

    @staticmethod
    def create_email_verification_token(user_id: UUID, email: str) -> str:
        """Create a JWT used as an email ownership proof.

        Args:
            user_id: User ID
            email: User email

        Returns:
            str: Signed JWT, valid for settings.EMAIL_VERIFICATION_EXPIRATION_HOURS
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=settings.EMAIL_VERIFICATION_EXPIRATION_HOURS)
        canonical_email = canonicalize_email(email)
        payload = {
            "user_id": str(user_id),
            "email": canonical_email,
            "type": "email_verification",
            "iat": now,
            "exp": expires_at,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "jti": f"emailverify_{user_id}_{now.timestamp()}",
            "kid": TokenManager._current_kid(),
        }
        return jwt.encode(payload, TokenManager._signing_secret(), algorithm=settings.JWT_ALGORITHM, headers={"kid": TokenManager._current_kid()})

    @staticmethod
    def create_mfa_challenge_token(user_id: UUID, email: str) -> str:
        """Create a short-lived token proving password login succeeded.

        Issued after a successful password check when the user has MFA enabled.
        It must be exchanged for real tokens via a valid TOTP code within
        ``MFA_CHALLENGE_EXPIRATION_MINUTES``.
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=settings.MFA_CHALLENGE_EXPIRATION_MINUTES)
        canonical_email = canonicalize_email(email)
        payload = {
            "sub": str(user_id),
            "user_id": str(user_id),
            "email": canonical_email,
            "type": "mfa_challenge",
            "iat": now,
            "exp": expires_at,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "jti": f"mfa_{user_id}_{now.timestamp()}",
            "kid": TokenManager._current_kid(),
        }
        return jwt.encode(payload, TokenManager._signing_secret(), algorithm=settings.JWT_ALGORITHM, headers={"kid": TokenManager._current_kid()})

    @staticmethod
    def verify_mfa_challenge(token: str) -> UUID | None:
        """Verify an mfa_challenge token and return the user UUID or None."""
        try:
            payload = TokenManager.verify_token(token, token_type="mfa_challenge")
            if payload is None:
                return None
            user_id = payload.get("user_id") or payload.get("sub")
            if user_id:
                return UUID(user_id)
        except Exception:  # noqa: BLE001
            return None
        return None

    @staticmethod
    def hash_token(token: str) -> str:
        """Hash a token for secure storage using SHA-256."""
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def verify_token(token: str, token_type: str = "access") -> dict[str, Any] | None:
        """Verify and decode JWT token with key rotation support (#138).

        Tries current key first, then previous keys from JWT_KEYS.

        Args:
            token: JWT token to verify
            token_type: Expected token type (access or refresh)

        Returns:
            dict | None: Decoded token payload, or None if invalid/expired
        """
        # Extract kid from header to select correct key
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid", "default")
        except JWTError:
            kid = "default"

        # Find matching key
        keys = _jwt_key_set()
        secret = None
        for k in keys:
            if k.get("kid") == kid:
                secret = k["secret"]
                break
        if secret is None:
            secret = keys[0]["secret"] if keys else _jwt_secret()

        try:
            payload = jwt.decode(
                token,
                secret,
                algorithms=[settings.JWT_ALGORITHM],
                issuer=settings.JWT_ISSUER,
                audience=settings.JWT_AUDIENCE,
                options={"leeway": settings.JWT_LEEWAY_SECONDS},
            )

            if payload.get("type") != token_type:
                logger.warning(f"Invalid token type: expected {token_type}")
                raise JWTError(f"Invalid token type: expected {token_type}")

            return payload

        except ExpiredSignatureError:
            logger.debug("Token expired")
            return None
        except JWTError as e:
            if "Invalid token type" in str(e):
                raise
            logger.warning(f"Token verification failed: {e}")
            return None

    @staticmethod
    def extract_user_id(token: str) -> UUID | None:
        """Extract user ID from token without verification.

        Args:
            token: JWT token

        Returns:
            UUID | None: User ID if extractable, None otherwise
        """
        try:
            payload = jwt.decode(
                token,
                TokenManager._signing_secret(),
                algorithms=[settings.JWT_ALGORITHM],
                options={
                    "verify_signature": False,
                    "verify_aud": False,
                    "verify_iss": False,
                    "verify_exp": False,
                },
            )
            user_id_str = payload.get("user_id")
            if user_id_str:
                return UUID(user_id_str)
        except (JWTError, ValueError, IndexError, UnicodeDecodeError, json.JSONDecodeError):
            pass

        return None

    # Convenience wrappers for service layer compatibility
    def create_access_token_for_user(self, user: User) -> str:
        """Create access token from a user object (service-friendly)."""
        return TokenManager.create_access_token(user.id, user.email, user.token_version)

    def create_refresh_token_for_user(self, user: User) -> tuple[str, str, datetime]:
        """Create refresh token and return token, hash, and expires_at."""
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRATION_DAYS)
        # Use existing static method to build token
        token = TokenManager.create_refresh_token(user.id)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return token, token_hash, expires_at

    def verify_refresh_token(self, token: str) -> UUID | None:
        """Verify refresh token and return user UUID or None."""
        try:
            payload = TokenManager.verify_token(token, token_type="refresh")
            if payload is None:
                return None
            user_id = payload.get("user_id") or payload.get("sub")
            if user_id:
                return UUID(user_id)
        except Exception:  # noqa: BLE001
            return None
        return None

    @staticmethod
    def hash_refresh_token(token: str) -> str:
        """Return sha256 hash of given refresh token for storage."""
        return hashlib.sha256(token.encode()).hexdigest()


class SecretCipher:
    """Index-encrypts at-rest secrets (TOTP MFA) with a dedicated key.

    Encryption always uses the current key (``MFA_ENCRYPTION_KEY``, or the
    JWT-secret-derived key when unset). Decryption tries the current key
    first, then every ``MFA_ENCRYPTION_KEY_PREVIOUS`` entry, so rotating the
    encryption key never strands existing MFA enrollments and any replica
    sharing the settings can decrypt the same secrets (finding 2). Never
    store MFA secrets in plaintext."""

    @staticmethod
    def _fernet(key_str: str):
        from cryptography.fernet import Fernet

        key = base64.urlsafe_b64encode(hashlib.sha256(key_str.encode()).digest())
        return Fernet(key)

    @classmethod
    def _keys(cls) -> list[str]:
        keys: list[str] = []
        if settings.MFA_ENCRYPTION_KEY:
            keys.append(settings.MFA_ENCRYPTION_KEY)
        if settings.JWT_SECRET_KEY:
            keys.append(settings.JWT_SECRET_KEY)
        keys.extend(k for k in settings.MFA_ENCRYPTION_KEY_PREVIOUS if k)
        return keys

    @classmethod
    def encrypt(cls, plaintext: str) -> str:
        return str(cls._fernet(cls._keys()[0]).encrypt(plaintext.encode()).decode())

    @classmethod
    def decrypt(cls, token: str) -> str:
        from cryptography.fernet import InvalidToken

        last_error: Exception | None = None
        for key in cls._keys():
            try:
                return str(cls._fernet(key).decrypt(token.encode()).decode())
            except (InvalidToken, ValueError, TypeError) as exc:  # noqa: PERF203
                last_error = exc
        logger.warning(f"Secret decryption failed with all keys: {last_error!s}")
        return ""


class RateLimiter:
    """Rate limiting utilities."""

    @staticmethod
    def get_rate_limit_key(
        identifier: str,
        action: str,
    ) -> str:
        """Generate rate limit cache key.

        Args:
            identifier: User identifier (email or IP)
            action: Action type (login, registration, etc.)

        Returns:
            str: Cache key
        """
        return f"ratelimit:{action}:{identifier}"

    @staticmethod
    def get_window_size(action: str) -> tuple[int, int]:
        """Get rate limit window and attempts.

        Args:
            action: Action type

        Returns:
            tuple: (attempts_allowed, window_seconds)
        """
        if action == "login":
            return (
                settings.LOGIN_RATE_LIMIT_ATTEMPTS,
                settings.LOGIN_RATE_LIMIT_WINDOW,
            )
        elif action == "registration":
            return (
                settings.REGISTRATION_RATE_LIMIT_ATTEMPTS,
                settings.REGISTRATION_RATE_LIMIT_WINDOW,
            )
        else:
            return (10, 3600)  # Default: 10 attempts per hour
