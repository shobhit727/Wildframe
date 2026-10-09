from typing import Any

"""Security utilities for authentication and authorization."""


import hashlib
import logging

import bcrypt
from jose.exceptions import ExpiredSignatureError, JWTError
from wildframe_auth import JWKSUnavailableError, verify_token_with_jwks

from app.core.settings import settings

logger = logging.getLogger(__name__)


PASSWORD_MAX_LENGTH: int = 128


def _encode_password(password: str) -> bytes:
    """Validate password length and encode for bcrypt.

    Raises ValueError if password exceeds PASSWORD_MAX_LENGTH characters
    instead of silently truncating.
    """
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"password exceeds maximum length of {PASSWORD_MAX_LENGTH} characters")
    return password.encode("utf-8")


class PasswordManager:
    """Manages password hashing and verification."""

    @staticmethod
    def hash_password(password: str, rounds: int | None = None) -> str:
        """Hash a password using bcrypt."""
        rounds = rounds or settings.PASSWORD_BCRYPT_ROUNDS
        salt = bcrypt.gensalt(rounds=rounds)
        return bcrypt.hashpw(_encode_password(password), salt).decode("utf-8")

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """Verify a password against its hash."""
        try:
            return bcrypt.checkpw(
                _encode_password(password),
                password_hash.encode("utf-8"),
            )
        except (ValueError, TypeError, AttributeError):
            return False


class TokenManager:
    """Verifies JWTs against auth-service's published JWKS.

    This class used to both *mint* and *verify* tokens with the shared HMAC
    secret. Both halves are gone (#941):

    * ``create_access_token`` was removed. It signed with the committed
      development secret, so it was a forge-token factory living in the same
      module as the verifier -- and after the verifier moved to the JWKS, no
      service would have accepted its output, making it dead code as well as
      dangerous. Nothing in ``app/`` called it. auth-service is the only
      issuer in this platform.
    * ``verify_token`` now delegates to ``wildframe_auth``'s
      ``verify_token_with_jwks``, so a forged HS256 token can no longer
      authenticate. The shared secret is a committed development value and
      ``DEV_ENVIRONMENTS`` exempts it from the production validator, so the
      old path was a live bypass rather than a latent one.
    """

    @staticmethod
    async def verify_token(token: str, token_type: str = "access") -> dict[str, Any] | None:
        """Verify a JWT against the JWKS. ``None`` when the token is not valid.

        **Now a coroutine.** The replacement ``verify_token_with_jwks`` fetches
        the JWKS over the network, so keeping this synchronous would have meant
        blocking the event loop on I/O on every authenticated request. The only
        caller, ``app.api.routes.get_current_user_id``, was already a coroutine
        and now awaits this.

        The ``None``-on-invalid contract is preserved, because every caller and
        its tests depend on a bad token being an ordinary answer rather than an
        exception. One thing deliberately does *not* return ``None``: a JWKS
        outage raises, so the caller can answer 503. Folding an outage into
        ``None`` would tell the caller their token was bad when the service
        merely could not check it.
        """
        # JWKSUnavailableError must be caught *before* JWTError: it is a JWTError
        # subclass, and it is the branch that keeps a JWKS outage (fetch failure,
        # or a body that is not a JWKS) a 503 instead of a 401. Everything else
        # the verifier rejects -- bad signature, expired, wrong audience, unknown
        # kid, wrong type -- stays ``None``.
        try:
            return await verify_token_with_jwks(
                token,
                audience=settings.JWT_AUDIENCE,
                issuer=settings.JWT_ISSUER,
                url=settings.JWT_JWKS_URL,
                expected_type=token_type,
            )
        except JWKSUnavailableError:
            # Deliberately not swallowed: this is an availability failure, and
            # the caller must be able to distinguish it from a bad token.
            raise
        except ExpiredSignatureError:
            logger.debug("Token expired")
            return None
        except JWTError as e:
            logger.warning(f"Invalid token: {e}")
            return None

    @staticmethod
    def hash_token(token: str) -> str:
        """Hash a token for secure storage."""
        return hashlib.sha256(token.encode()).hexdigest()
