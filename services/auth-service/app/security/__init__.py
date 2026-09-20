import base64
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import bcrypt
from app.core.settings import settings
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from app.models import User

logger = logging.getLogger(__name__)

PASSWORD_MAX_LENGTH: int = 128

COMMON_PASSWORDS: frozenset[str] = frozenset(
    {
        "password",
        "password1",
        "password123",
        "passw0rd",
        "qwerty123",
        "letmein1",
        "welcome1",
        "admin123",
        "iloveyou1",
        "abc12345",
        "p@ssw0rd",
        "12345678",
        "123456789",
        "1234567890",
        "password1234",
        "password12345",
        "qwerty123456",
        "1q2w3e4r5t6y",
        "aaaaaaaaaaaa",
        "123412341234",
        "abcdabcdabcd",
        "letmein12345",
        "welcomewelco",
        "administrator1",
    }
)


def normalize_email(email: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFC", email).strip().casefold()


def _encode_password(password: str) -> bytes:
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"password exceeds maximum length of {PASSWORD_MAX_LENGTH} characters")
    return password.encode("utf-8")


def role_for_email(email: str | None) -> str:
    if not email:
        return "user"
    admins = {a.strip().lower() for a in settings.ADMIN_EMAILS.split(",") if a.strip()}
    return "admin" if email.strip().lower() in admins else "user"


def _get_signing_key() -> str:
    from app.security.jwks import get_private_key_pem

    return get_private_key_pem()


def _verify_via_jwks(token: str, token_type: str) -> dict:
    from app.security.jwks import get_jwk_for_kid

    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise JWTError(f"invalid header: {e}") from e
    alg = header.get("alg")
    kid = header.get("kid")
    if alg != settings.JWT_ALGORITHM:
        raise JWTError(f"unsupported alg {alg}")
    if not kid:
        raise JWTError("missing kid")
    jwk = get_jwk_for_kid(kid)
    if jwk is None:
        raise JWTError(f"unknown kid {kid}")
    payload = jwt.decode(
        token,
        jwk,
        algorithms=[settings.JWT_ALGORITHM],
        issuer=settings.JWT_ISSUER,
        audience=settings.JWT_AUDIENCE,
        options={"leeway": settings.JWT_LEEWAY_SECONDS},
    )
    if payload.get("type") != token_type:
        raise JWTError(f"Invalid token type: expected {token_type}")
    return payload


class PasswordManager:
    _dummy_hash: str | None = None

    @staticmethod
    def hash_password(password: str) -> str:
        salt = bcrypt.gensalt(rounds=settings.PASSWORD_BCRYPT_ROUNDS)
        return bcrypt.hashpw(_encode_password(password), salt).decode("utf-8")

    @classmethod
    def dummy_hash(cls) -> str:
        if cls._dummy_hash is None:
            salt = bcrypt.gensalt(rounds=settings.PASSWORD_BCRYPT_ROUNDS)
            cls._dummy_hash = bcrypt.hashpw(b"timing-equalizer-dummy", salt).decode("utf-8")
        return cls._dummy_hash

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        try:
            return bcrypt.checkpw(
                _encode_password(plain_password),
                hashed_password.encode("utf-8"),
            )
        except (ValueError, TypeError):
            return False

    @staticmethod
    def needs_rehash(hashed_password: str) -> bool:
        try:
            rounds = int(hashed_password.split("$")[2])
        except (IndexError, ValueError):
            return False
        return rounds < settings.PASSWORD_BCRYPT_ROUNDS


class TokenManager:
    @staticmethod
    def create_access_token(user_id: UUID, email: str, auth_version: int = 0) -> str:
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=settings.JWT_EXPIRATION_MINUTES)
        payload = {
            "sub": str(user_id),
            "user_id": str(user_id),
            "email": email,
            "role": role_for_email(email),
            "type": "access",
            "av": auth_version,
            "arv": settings.ADMIN_ROLE_VERSION,
            "iat": now,
            "exp": expires_at,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "jti": f"access_{user_id}_{now.timestamp()}",
        }
        return jwt.encode(
            payload,
            _get_signing_key(),
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": settings.JWT_KEY_ID},
        )

    @staticmethod
    def create_refresh_token(user_id: UUID) -> str:
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
        }
        return jwt.encode(
            payload,
            _get_signing_key(),
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": settings.JWT_KEY_ID},
        )

    @staticmethod
    def create_email_verification_token(user_id: UUID, email: str) -> str:
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=settings.EMAIL_VERIFICATION_EXPIRATION_HOURS)
        payload = {
            "user_id": str(user_id),
            "email": email,
            "type": "email_verification",
            "iat": now,
            "exp": expires_at,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "jti": f"emailverify_{user_id}_{now.timestamp()}",
        }
        return jwt.encode(
            payload,
            _get_signing_key(),
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": settings.JWT_KEY_ID},
        )

    @staticmethod
    def create_mfa_challenge_token(user_id: UUID, email: str) -> str:
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=settings.MFA_CHALLENGE_EXPIRATION_MINUTES)
        payload = {
            "sub": str(user_id),
            "user_id": str(user_id),
            "email": email,
            "type": "mfa_challenge",
            "iat": now,
            "exp": expires_at,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "jti": f"mfa_{user_id}_{now.timestamp()}",
        }
        return jwt.encode(
            payload,
            _get_signing_key(),
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": settings.JWT_KEY_ID},
        )

    @staticmethod
    def verify_mfa_challenge(token: str) -> UUID | None:
        try:
            payload = TokenManager.verify_token(token, token_type="mfa_challenge")
            if payload is None:
                return None
            user_id = payload.get("user_id") or payload.get("sub")
            if user_id:
                return UUID(user_id)
        except Exception:
            return None
        return None

    @staticmethod
    def create_admin_step_up_token(
        user_id: UUID,
        email: str,
        auth_version: int,
        amr: list[str],
        scope: str = "admin:destructive",
    ) -> str:
        now = datetime.now(UTC)
        exp_minutes = getattr(settings, "STEP_UP_EXPIRATION_MINUTES", 5)
        expires_at = now + timedelta(minutes=exp_minutes)
        payload = {
            "sub": str(user_id),
            "user_id": str(user_id),
            "email": email,
            "role": "admin",
            "type": "admin_step_up",
            "av": auth_version,
            "arv": settings.ADMIN_ROLE_VERSION,
            "amr": amr,
            "scope": scope,
            "iat": now,
            "exp": expires_at,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "jti": f"stepup_{user_id}_{now.timestamp()}_{uuid.uuid4().hex[:8]}",
        }
        return jwt.encode(
            payload,
            _get_signing_key(),
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": settings.JWT_KEY_ID},
        )

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def verify_token(token: str, token_type: str = "access") -> dict[str, Any] | None:
        try:
            payload = _verify_via_jwks(token, token_type)
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
        try:
            payload = jwt.decode(
                token,
                "",
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

    def create_access_token_for_user(self, user: User) -> str:
        return TokenManager.create_access_token(user.id, user.email, user.auth_version)

    def create_refresh_token_for_user(self, user: User) -> tuple[str, str, datetime]:
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRATION_DAYS)
        token = TokenManager.create_refresh_token(user.id)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return token, token_hash, expires_at

    def verify_refresh_token(self, token: str) -> UUID | None:
        try:
            payload = TokenManager.verify_token(token, token_type="refresh")
            if payload is None:
                return None
            user_id = payload.get("user_id") or payload.get("sub")
            if user_id:
                return UUID(user_id)
        except Exception:
            return None
        return None

    @staticmethod
    def hash_refresh_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()


class SecretCipher:
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
            except (InvalidToken, ValueError, TypeError) as exc:
                last_error = exc
        logger.warning(f"Secret decryption failed with all keys: {last_error!s}")
        return ""


class RateLimiter:
    @staticmethod
    def get_rate_limit_key(
        identifier: str,
        action: str,
    ) -> str:
        return f"ratelimit:{action}:{identifier}"

    @staticmethod
    def get_window_size(action: str) -> tuple[int, int]:
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
            return (10, 3600)
