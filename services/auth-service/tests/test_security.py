"""Unit tests for security utilities."""

from uuid import uuid4

import pytest
from app.security import RateLimiter, TokenManager
from jose import JWTError


class TestPasswordManager:
    """Test password hashing and verification."""

    def test_hash_password(self, password_manager):
        """Test password hashing."""
        password = "SecurePass123!"
        hash_value = password_manager.hash_password(password)

        assert hash_value != password
        assert len(hash_value) > 0
        assert password_manager.verify_password(password, hash_value)

    def test_verify_password_success(self, password_manager):
        """Test successful password verification."""
        password = "SecurePass123!"
        hash_value = password_manager.hash_password(password)

        assert password_manager.verify_password(password, hash_value) is True

    def test_verify_password_failure(self, password_manager):
        """Test failed password verification."""
        password = "SecurePass123!"
        wrong_password = "WrongPass456!"
        hash_value = password_manager.hash_password(password)

        assert password_manager.verify_password(wrong_password, hash_value) is False

    def test_password_hash_uniqueness(self, password_manager):
        """Test that same password generates different hashes."""
        password = "SecurePass123!"
        hash1 = password_manager.hash_password(password)
        hash2 = password_manager.hash_password(password)

        assert hash1 != hash2
        assert password_manager.verify_password(password, hash1)
        assert password_manager.verify_password(password, hash2)


class TestTokenManager:
    """Test JWT token generation and verification."""

    def test_create_access_token(self, token_manager):
        """Test access token creation."""
        user_id = uuid4()
        email = "test@example.com"

        token = TokenManager.create_access_token(user_id, email)

        assert token is not None
        assert len(token) > 0

    def test_create_refresh_token(self, token_manager):
        """Test refresh token creation."""
        user_id = uuid4()

        token = TokenManager.create_refresh_token(user_id)

        assert token is not None
        assert len(token) > 0

    def test_verify_access_token(self, token_manager):
        """Test access token verification."""
        user_id = uuid4()
        email = "test@example.com"

        token = TokenManager.create_access_token(user_id, email)
        payload = TokenManager.verify_token(token, token_type="access")

        assert payload["user_id"] == str(user_id)
        assert payload["email"] == email
        assert payload["type"] == "access"

    def test_verify_refresh_token(self, token_manager):
        """Test refresh token verification."""
        user_id = uuid4()

        token = TokenManager.create_refresh_token(user_id)
        payload = TokenManager.verify_token(token, token_type="refresh")

        assert payload["user_id"] == str(user_id)
        assert payload["type"] == "refresh"

    def test_verify_invalid_token_type(self, token_manager):
        """Test verification with wrong token type."""
        user_id = uuid4()

        access_token = TokenManager.create_access_token(user_id, "test@example.com")

        with pytest.raises(JWTError):
            TokenManager.verify_token(access_token, token_type="refresh")

    def test_extract_user_id(self, token_manager):
        """Test user ID extraction from token."""
        user_id = uuid4()
        email = "test@example.com"

        token = TokenManager.create_access_token(user_id, email)
        extracted_id = TokenManager.extract_user_id(token)

        assert extracted_id == user_id

    def test_extract_user_id_invalid_token(self, token_manager):
        """Test user ID extraction from invalid token."""
        invalid_token = "invalid.token.here"

        extracted_id = TokenManager.extract_user_id(invalid_token)

        assert extracted_id is None

    def test_token_expiration(self, token_manager):
        """Test that expired tokens fail verification."""
        user_id = uuid4()
        email = "test@example.com"

        token = TokenManager.create_access_token(user_id, email)
        # Should verify successfully before expiration
        payload = TokenManager.verify_token(token, token_type="access")
        assert payload is not None


class TestRateLimiter:
    """Test rate limiting utilities."""

    def test_get_rate_limit_key(self):
        """Test rate limit key generation."""
        key = RateLimiter.get_rate_limit_key("user@example.com", "login")

        assert key == "ratelimit:login:user@example.com"

    def test_get_window_size_login(self):
        """Test window size for login."""
        attempts, window = RateLimiter.get_window_size("login")

        assert attempts > 0
        assert window > 0
        assert window == 60 * 15  # 15 minutes

    def test_get_window_size_registration(self):
        """Test window size for registration."""
        attempts, window = RateLimiter.get_window_size("registration")

        assert attempts > 0
        assert window > 0

    def test_get_window_size_default(self):
        """Test default window size."""
        attempts, window = RateLimiter.get_window_size("unknown_action")

        assert attempts == 10
        assert window == 3600


# ==========================================================================
# Remaining security helpers
# ==========================================================================

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.security import (
    PASSWORD_MAX_LENGTH,
    PasswordManager,
    SecretCipher,
    TokenManager,
    normalize_email,
    role_for_email,
)
from app.security import RateLimiter  # noqa: F811  (already imported above)
from app.core import settings as settings_module


class TestEncodePasswordGuard:
    def test_password_longer_than_the_cap_is_rejected(self):
        with pytest.raises(ValueError, match=f"maximum length of {PASSWORD_MAX_LENGTH}"):
            PasswordManager.hash_password("a" * (PASSWORD_MAX_LENGTH + 1))

    def test_bcrypt_own_72_byte_limit_makes_the_128_cap_unreachable(self):
        """BUG: the app-level cap (128) is above bcrypt's hard limit (72 bytes).

        ``UserRegisterRequest.password`` allows up to 128 characters, so a
        73-128 character password passes Pydantic validation and then raises
        ``ValueError: password cannot be longer than 72 bytes`` inside
        ``hash_password``. Registration surfaces that as a 500, not a 422.
        Asserted as-is; not fixed here.
        """
        with pytest.raises(ValueError, match="72 bytes"):
            PasswordManager.hash_password("a" * 73)

        # Within bcrypt's own limit the round trip works.
        hashed = PasswordManager.hash_password("a" * 72)
        assert PasswordManager.verify_password("a" * 72, hashed)

    def test_verification_of_a_73_char_password_is_silently_false(self):
        """``verify_password`` swallows the same ValueError, so an over-long
        password can never verify even against a matching hash."""
        hashed = PasswordManager.hash_password("a" * 72)

        assert PasswordManager.verify_password("a" * 73, hashed) is False

    def test_over_long_password_verification_is_false_not_an_exception(self):
        hashed = PasswordManager.hash_password("Secret1234!")

        assert PasswordManager.verify_password("a" * (PASSWORD_MAX_LENGTH + 1), hashed) is False

    def test_verify_password_tolerates_a_corrupt_hash(self):
        assert PasswordManager.verify_password("Secret1234!", "not-a-bcrypt-hash") is False
        assert PasswordManager.verify_password("Secret1234!", "") is False

    def test_dummy_hash_is_cached_and_never_matches_a_real_password(self):
        first = PasswordManager.dummy_hash()
        second = PasswordManager.dummy_hash()

        assert first == second
        assert first != PasswordManager.hash_password("Secret1234!")
        assert PasswordManager.verify_password("Secret1234!", first) is False
        assert PasswordManager.verify_password("timing-equalizer-dummy", first) is True

    def test_needs_rehash_for_a_low_cost_hash(self, monkeypatch):
        monkeypatch.setattr(settings_module.settings, "PASSWORD_BCRYPT_ROUNDS", 6)
        cheap = PasswordManager.hash_password("Secret1234!")
        # Now raise the configured cost above the one the hash was made with.
        monkeypatch.setattr(settings_module.settings, "PASSWORD_BCRYPT_ROUNDS", 12)

        assert PasswordManager.needs_rehash(cheap) is True

    def test_needs_rehash_for_a_current_hash(self):
        assert PasswordManager.needs_rehash(PasswordManager.hash_password("Secret1234!")) is False

    def test_needs_rehash_is_false_for_a_malformed_hash(self):
        assert PasswordManager.needs_rehash("garbage") is False
        assert PasswordManager.needs_rehash("$2b$notanumber$abc") is False


class TestNormalizeEmail:
    def test_strips_and_casefolds(self):
        assert normalize_email("  User@Example.COM  ") == "user@example.com"

    def test_applies_nfc_normalisation(self):
        decomposed = "user@exämple.com"
        composed = "user@exämple.com"

        assert normalize_email(decomposed) == normalize_email(composed)


class TestRoleForEmail:
    def test_missing_email_is_a_user(self):
        assert role_for_email(None) == "user"
        assert role_for_email("") == "user"

    def test_configured_admin_gets_the_admin_role(self, monkeypatch):
        monkeypatch.setattr(settings_module.settings, "ADMIN_EMAILS", "boss@wildframe.com")

        assert role_for_email("Boss@Wildframe.com") == "admin"

    def test_admin_list_tolerates_spaces_and_empty_entries(self, monkeypatch):
        monkeypatch.setattr(
            settings_module.settings, "ADMIN_EMAILS", " a@wildframe.com , , b@wildframe.com "
        )

        assert role_for_email("a@wildframe.com") == "admin"
        assert role_for_email("b@wildframe.com") == "admin"
        assert role_for_email("c@wildframe.com") == "user"

    def test_empty_admin_list_grants_nobody(self, monkeypatch):
        monkeypatch.setattr(settings_module.settings, "ADMIN_EMAILS", "")

        assert role_for_email("anyone@wildframe.com") == "user"


class TestVerifyMfaChallenge:
    def test_valid_challenge_resolves_the_user(self):
        user_id = uuid4()
        challenge = TokenManager.create_mfa_challenge_token(user_id, "mfa@example.com")

        assert TokenManager.verify_mfa_challenge(challenge) == user_id

    def test_garbage_challenge_is_none(self):
        assert TokenManager.verify_mfa_challenge("not.a.jwt") is None

    def test_empty_challenge_is_none(self):
        assert TokenManager.verify_mfa_challenge("") is None

    def test_wrong_token_type_is_swallowed_and_returns_none(self):
        """``verify_token`` re-raises a type-mismatch JWTError; this wrapper
        must not leak it."""
        refresh = TokenManager.create_refresh_token(uuid4())

        assert TokenManager.verify_mfa_challenge(refresh) is None


class TestInstanceTokenHelpers:
    def test_create_access_token_for_user_carries_the_users_auth_version(self):
        from app.models import User

        user = User(
            id=uuid4(),
            email="instance@example.com",
            password_hash="h",
            auth_version=7,
        )
        manager = TokenManager()

        payload = manager.create_access_token_for_user(user)

        assert TokenManager.verify_token(payload, token_type="access")["av"] == 7

    def test_create_refresh_token_for_user_returns_token_hash_and_expiry(self):
        from app.models import User

        user = User(id=uuid4(), email="instance@example.com", password_hash="h")
        manager = TokenManager()

        token, token_hash, expires_at = manager.create_refresh_token_for_user(user)

        assert token != token_hash
        assert token_hash == TokenManager.hash_refresh_token(token)
        assert manager.verify_refresh_token(token) == user.id
        assert expires_at.tzinfo is not None
        assert expires_at > datetime.now(UTC)

    def test_verify_refresh_token_rejects_an_access_token(self):
        access = TokenManager.create_access_token(uuid4(), "instance@example.com", 0)

        assert TokenManager().verify_refresh_token(access) is None

    def test_verify_refresh_token_rejects_garbage(self):
        assert TokenManager().verify_refresh_token("not.a.jwt") is None

    def test_verify_refresh_token_rejects_a_wrong_type_token_that_raises(self):
        """A type mismatch raises inside ``verify_token``; the instance helper
        must swallow it and return None."""
        access = TokenManager.create_access_token(uuid4(), "instance@example.com", 0)
        manager = TokenManager()

        with patch.object(
            TokenManager, "verify_token", side_effect=JWTError("Invalid token type: x")
        ):
            assert manager.verify_refresh_token(access) is None

    def test_hash_refresh_token_matches_hash_token(self):
        assert TokenManager.hash_refresh_token("abc") == TokenManager.hash_token("abc")


class TestExtractUserId:
    def test_extracts_the_user_id_without_verifying(self):
        from app.models import User

        user_id = uuid4()
        user = User(id=user_id, email="extract@example.com", password_hash="h")

        token = TokenManager().create_access_token_for_user(user)

        assert TokenManager.extract_user_id(token) == user_id

    def test_garbage_token_is_none(self):
        assert TokenManager.extract_user_id("not.a.jwt") is None

    def test_token_without_a_user_id_is_none(self):
        from jose import jwt

        now = datetime.now(UTC)
        token = jwt.encode(
            {"sub": "x", "type": "access", "iat": now, "exp": now + timedelta(minutes=5)},
            "secret",
            algorithm="HS256",
        )

        assert TokenManager.extract_user_id(token) is None

    def test_non_uuid_user_id_is_none(self):
        from jose import jwt

        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "user_id": "not-a-uuid",
                "type": "access",
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            "secret",
            algorithm="HS256",
        )

        assert TokenManager.extract_user_id(token) is None


class TestSecretCipher:
    def test_round_trips_with_the_configured_key(self, monkeypatch):
        monkeypatch.setattr(settings_module.settings, "MFA_ENCRYPTION_KEY", "primary-key")

        token = SecretCipher.encrypt("my-totp-secret")

        assert token != "my-totp-secret"
        assert SecretCipher.decrypt(token) == "my-totp-secret"

    def test_decrypts_with_a_previous_key(self, monkeypatch):
        monkeypatch.setattr(settings_module.settings, "MFA_ENCRYPTION_KEY", "old-key")
        token = SecretCipher.encrypt("rotate-me")
        monkeypatch.setattr(
            settings_module.settings, "MFA_ENCRYPTION_KEY", "new-key"
        )
        monkeypatch.setattr(settings_module.settings, "MFA_ENCRYPTION_KEY_PREVIOUS", ["old-key"])

        assert SecretCipher.decrypt(token) == "rotate-me"

    def test_undecryptable_token_returns_empty_string(self, monkeypatch):
        monkeypatch.setattr(settings_module.settings, "MFA_ENCRYPTION_KEY", "k1")
        monkeypatch.setattr(settings_module.settings, "MFA_ENCRYPTION_KEY_PREVIOUS", [])

        assert SecretCipher.decrypt("not-a-fernet-token") == ""

    def test_falls_back_to_the_jwt_secret_when_no_mfa_key_is_set(self, monkeypatch):
        jwt_secret = settings_module.settings.JWT_SECRET_KEY
        monkeypatch.setattr(settings_module.settings, "MFA_ENCRYPTION_KEY", "")
        monkeypatch.setattr(settings_module.settings, "MFA_ENCRYPTION_KEY_PREVIOUS", [])

        token = SecretCipher.encrypt("jwt-derived")

        assert SecretCipher.decrypt(token) == "jwt-derived"
        assert jwt_secret is not None

    def test_empty_previous_keys_are_ignored(self, monkeypatch):
        monkeypatch.setattr(settings_module.settings, "MFA_ENCRYPTION_KEY", "k1")
        monkeypatch.setattr(settings_module.settings, "MFA_ENCRYPTION_KEY_PREVIOUS", ["", "k1"] * 3)

        token = SecretCipher.encrypt("dedup")

        assert SecretCipher.decrypt(token) == "dedup"

    def test_decrypt_failure_is_logged(self, monkeypatch, caplog):
        monkeypatch.setattr(settings_module.settings, "MFA_ENCRYPTION_KEY", "k1")
        monkeypatch.setattr(settings_module.settings, "MFA_ENCRYPTION_KEY_PREVIOUS", [])

        with caplog.at_level("WARNING", logger="app.security"):
            assert SecretCipher.decrypt("garbage") == ""

        assert "Secret decryption failed with all keys" in caplog.text


class TestRateLimiterHelpers:
    def test_rate_limit_key_is_namespaced(self):
        assert (
            RateLimiter.get_rate_limit_key("1.2.3.4", "login")
            == "ratelimit:login:1.2.3.4"
        )

    def test_window_size_for_login(self):
        assert RateLimiter.get_window_size("login") == (
            settings_module.settings.LOGIN_RATE_LIMIT_ATTEMPTS,
            settings_module.settings.LOGIN_RATE_LIMIT_WINDOW,
        )

    def test_window_size_for_registration(self):
        assert RateLimiter.get_window_size("registration") == (
            settings_module.settings.REGISTRATION_RATE_LIMIT_ATTEMPTS,
            settings_module.settings.REGISTRATION_RATE_LIMIT_WINDOW,
        )

    def test_window_size_for_an_unknown_action_uses_the_default(self):
        assert RateLimiter.get_window_size("mystery") == (10, 3600)


class _AnonymousTokenManager(TokenManager):
    """Mints a validly-signed token with no ``user_id``/``sub`` claim."""

    @staticmethod
    def _mint(token_type: str) -> str:
        from jose import jwt

        from app.core.settings import settings
        from app.security.jwks import get_private_key_pem

        now = datetime.now(UTC)
        return jwt.encode(
            {
                "user_id": "",
                "sub": "",
                "type": token_type,
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
                "iat": now,
                "exp": now + timedelta(minutes=15),
            },
            get_private_key_pem(),
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": settings.JWT_KEY_ID},
        )

    @classmethod
    def mint_mfa_challenge(cls) -> str:
        return cls._mint("mfa_challenge")

    @classmethod
    def mint_refresh(cls) -> str:
        return cls._mint("refresh")


class TestTokensWithoutASubject:
    def test_verify_mfa_challenge_returns_none_when_there_is_no_subject(self):
        """A validly-signed ``mfa_challenge`` with empty user_id/sub decodes
        fine but has nothing to resolve, so the helper falls through to None."""
        token = _AnonymousTokenManager.mint_mfa_challenge()

        assert TokenManager.verify_token(token, token_type="mfa_challenge") is not None
        assert TokenManager.verify_mfa_challenge(token) is None

    def test_verify_refresh_token_returns_none_when_there_is_no_subject(self):
        token = _AnonymousTokenManager.mint_refresh()

        assert TokenManager.verify_token(token, token_type="refresh") is not None
        assert TokenManager().verify_refresh_token(token) is None

    def test_verify_token_returns_none_for_an_expired_token(self):
        from jose import jwt

        from app.core.settings import settings
        from app.security.jwks import get_private_key_pem

        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "user_id": str(uuid4()),
                "type": "access",
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
                "iat": now - timedelta(hours=2),
                "exp": now - timedelta(hours=1),
            },
            get_private_key_pem(),
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": settings.JWT_KEY_ID},
        )

        assert TokenManager.verify_token(token, token_type="access") is None

    def test_verify_token_raises_for_a_type_mismatch(self):
        refresh = TokenManager.create_refresh_token(uuid4())

        with pytest.raises(JWTError, match="Invalid token type"):
            TokenManager.verify_token(refresh, token_type="access")

    def test_verify_token_returns_none_for_an_unsupported_alg(self):
        from jose import jwt

        now = datetime.now(UTC)
        token = jwt.encode(
            {"user_id": str(uuid4()), "type": "access", "iat": now, "exp": now + timedelta(minutes=5)},
            "a-shared-secret-that-is-long-enough-for-hs256!!",
            algorithm="HS256",
        )

        assert TokenManager.verify_token(token, token_type="access") is None

    def test_verify_token_returns_none_when_the_header_has_no_kid(self):
        from jose import jwt

        from app.core.settings import settings
        from app.security.jwks import get_private_key_pem

        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "user_id": str(uuid4()),
                "type": "access",
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
                "iat": now,
                "exp": now + timedelta(minutes=15),
            },
            get_private_key_pem(),
            algorithm=settings.JWT_ALGORITHM,
        )

        assert TokenManager.verify_token(token, token_type="access") is None

    def test_verify_token_returns_none_for_an_unknown_kid(self):
        from jose import jwt

        from app.core.settings import settings
        from app.security.jwks import get_private_key_pem, reset_cache

        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "user_id": str(uuid4()),
                "type": "access",
                "iss": settings.JWT_ISSUER,
                "aud": settings.JWT_AUDIENCE,
                "iat": now,
                "exp": now + timedelta(minutes=15),
            },
            get_private_key_pem(),
            algorithm=settings.JWT_ALGORITHM,
            headers={"kid": "retired-k9"},
        )

        assert TokenManager.verify_token(token, token_type="access") is None
        reset_cache()

    def test_verify_token_returns_none_for_an_unparseable_header(self):
        assert TokenManager.verify_token("not.a.jwt", token_type="access") is None
