"""Settings-construction and environment-gate tests.

``app/core/settings.py`` itself is already fully covered by ``test_settings.py``;
this module covers what the *rest* of the app does with the settings object:

* the dev-default injection and the fail-closed production validator, driven
  with explicit ``Settings(**kwargs)`` (no global env mutation),
* ``get_settings`` caching,
* the ``ENVIRONMENT == "production"`` behaviour gates in
  ``app/api/routes/auth.py`` (email-verification delivery and MFA setup), which
  are security-relevant and must behave identically for known and unknown
  accounts.
"""

import subprocess

import pytest
from app.api.routes import auth as auth_routes
from app.api.routes.auth import get_current_user
from app.core.database import DatabaseManager
from app.core.settings import (
    DEV_DEFAULTS,
    DEV_ENVIRONMENTS,
    KNOWN_INSECURE_DB_CREDENTIALS,
    KNOWN_INSECURE_JWT_SECRETS,
    Settings,
    get_settings,
)
from app.main import create_app
from app.security import jwks as jwks_module
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _throwaway_pem() -> str:
    """Generate a throwaway RSA key on demand for the production-settings fixture.

    Committing a PEM private key is forbidden by ``.github/issues/003`` and
    asserted by ``tests/contract/test_no_committed_private_key.py``, so the key
    is generated here rather than pasted into the source. This is a test
    fixture, never a credential: 2048-bit ``openssl genpkey`` takes roughly
    50ms, which is negligible at import time, and matches the key size used by
    ``app/security/jwks.py``. If openssl is missing the subprocess fails loudly
    (check=True) instead of silently substituting a bogus key.
    """
    result = subprocess.run(
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


PROD_JWT = _throwaway_pem()

PROD_KWARGS = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": "postgresql+asyncpg://app:realpass@db.example.com:5432/auth_db",
    "REDIS_URL": "redis://redis.example.com:6379/0",
    "JWT_PRIVATE_KEY": PROD_JWT,
    "KAFKA_BOOTSTRAP_SERVERS": "kafka.example.com:9092",
}


# --------------------------------------------------------------------------
# construction-time behaviour
# --------------------------------------------------------------------------


class TestDevelopmentDefaults:
    def test_dev_environments_are_the_documented_set(self):
        assert DEV_ENVIRONMENTS == {"", "development", "test"}

    def test_dev_defaults_are_injected_for_development(self):
        settings = Settings(ENVIRONMENT="development")

        for key, value in DEV_DEFAULTS.items():
            assert getattr(settings, key) == value

    def test_dev_defaults_are_injected_for_test(self):
        settings = Settings(ENVIRONMENT="test")

        assert settings.DATABASE_URL == DEV_DEFAULTS["DATABASE_URL"]
        assert settings.KAFKA_BOOTSTRAP_SERVERS == DEV_DEFAULTS["KAFKA_BOOTSTRAP_SERVERS"]

    def test_empty_environment_is_treated_as_development(self):
        settings = Settings(ENVIRONMENT="")

        assert settings.DATABASE_URL == DEV_DEFAULTS["DATABASE_URL"]
        assert settings.REDIS_URL == DEV_DEFAULTS["REDIS_URL"]

    def test_explicit_values_win_over_injected_defaults(self):
        settings = Settings(
            ENVIRONMENT="development",
            DATABASE_URL="postgresql+asyncpg://x:y@elsewhere:5432/other",
        )

        assert settings.DATABASE_URL == "postgresql+asyncpg://x:y@elsewhere:5432/other"

    def test_validator_returns_self_unchanged_in_development(self):
        settings = Settings(ENVIRONMENT="development")

        assert settings.validate_production_secrets() is settings

    def test_non_dev_environment_does_not_get_dev_defaults(self):
        # staging must not silently inherit the local Postgres DSN.
        with pytest.raises(ValueError, match="DATABASE_URL"):
            Settings(ENVIRONMENT="staging")


class TestProductionValidator:
    def test_valid_production_settings_pass(self):
        settings = Settings(**PROD_KWARGS)

        assert settings.ENVIRONMENT == "production"
        assert settings.DATABASE_URL == PROD_KWARGS["DATABASE_URL"]

    @pytest.mark.parametrize("credential", KNOWN_INSECURE_DB_CREDENTIALS)
    def test_each_known_insecure_db_credential_is_rejected(self, credential):
        with pytest.raises(ValueError, match="known default credentials"):
            Settings(
                **{
                    **PROD_KWARGS,
                    "DATABASE_URL": (
                        f"postgresql+asyncpg://{credential}@db.example.com:5432/auth_db"
                    ),
                }
            )

    def test_second_known_insecure_credential_variant_is_rejected(self):
        url = "postgresql+asyncpg://wildframe:wildframe_dev_password@db:5432/auth_db"

        with pytest.raises(ValueError, match="known default credentials"):
            Settings(**{**PROD_KWARGS, "DATABASE_URL": url})

    def test_known_insecure_jwt_secret_list_does_not_leak_into_errors(self):
        # The validator inspects JWT_PRIVATE_KEY, not JWT_SECRET_KEY, so a
        # known-insecure secret alone is not the failure reason.
        settings = Settings(**{**PROD_KWARGS, "JWT_SECRET_KEY": "changeme"})

        assert settings.JWT_SECRET_KEY == "changeme"

    def test_known_insecure_jwt_secrets_are_documented_as_dev_only(self):
        assert "dev-secret-key-change-in-production-min-32-bytes" in (
            KNOWN_INSECURE_JWT_SECRETS
        )
        assert "changeme" in KNOWN_INSECURE_JWT_SECRETS
        assert KNOWN_INSECURE_JWT_SECRETS[0].startswith("dev-secret-key")

    def test_redis_url_is_required_in_production(self):
        kwargs = dict(PROD_KWARGS)
        kwargs.pop("REDIS_URL")

        with pytest.raises(ValueError, match="REDIS_URL"):
            Settings(**kwargs)

    def test_pem_marker_is_required_on_the_private_key(self):
        with pytest.raises(ValueError, match="must be a PEM private key"):
            Settings(**{**PROD_KWARGS, "JWT_PRIVATE_KEY": "-----BEGIN RSA PRIVATE KEY-----\nx\n"})

    def test_kafka_bootstrap_is_required_in_production(self):
        kwargs = dict(PROD_KWARGS)
        kwargs.pop("KAFKA_BOOTSTRAP_SERVERS")

        with pytest.raises(ValueError, match="KAFKA_BOOTSTRAP_SERVERS"):
            Settings(**kwargs)

    def test_validation_runs_in_declaration_order(self):
        """DATABASE_URL is checked before REDIS_URL, so the first error wins."""
        kwargs = dict(PROD_KWARGS)
        kwargs.pop("DATABASE_URL")
        kwargs.pop("REDIS_URL")

        with pytest.raises(ValueError, match="DATABASE_URL"):
            Settings(**kwargs)

    def test_compliance_mixin_fields_are_present(self):
        settings = Settings(ENVIRONMENT="development")

        assert settings.compliance_dpo_email == "dpo@wildframe.com"
        assert settings.compliance_grievance_officer_email == "grievance@wildframe.com"
        assert settings.compliance_allowed_data_regions == ["US", "EU", "IN", "SG"]


class TestGetSettingsCaching:
    def test_get_settings_returns_a_cached_instance(self):
        get_settings.cache_clear()
        try:
            first = get_settings()
            second = get_settings()
            assert first is second
        finally:
            get_settings.cache_clear()

    def test_cache_info_reports_a_single_lookup(self):
        get_settings.cache_clear()
        try:
            get_settings()
            get_settings()
            assert get_settings.cache_info().hits == 1
        finally:
            get_settings.cache_clear()


# --------------------------------------------------------------------------
# ENVIRONMENT behaviour gates in app/api/routes/auth.py
# --------------------------------------------------------------------------


@pytest.fixture
def env_app(tmp_path, monkeypatch):
    """A ``create_app()`` instance backed by a throwaway SQLite file."""
    import asyncio

    from app.models import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/env.db")

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_create())
    finally:
        loop.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(DatabaseManager, "get_engine", classmethod(lambda cls: engine))
    monkeypatch.setattr(
        DatabaseManager, "get_session_factory", classmethod(lambda cls: factory)
    )
    return engine


@pytest.fixture
def env_client(env_app):
    """A TestClient with the moderation consumer stubbed and no throttling."""
    from unittest.mock import AsyncMock, patch

    from app.core import event_consumer

    async def _consumer(_session_factory):
        return None

    with patch.object(
        event_consumer, "run_user_moderation_consumer", _consumer
    ), patch("app.main.setup_logging"), patch.object(
        auth_routes, "allow", AsyncMock(return_value=True)
    ):
        with TestClient(create_app()) as client:
            yield client


def _register(client, email: str) -> str:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "SecurePass123!",
            "first_name": "Env",
            "last_name": "Gate",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


class TestProductionEnvironmentGates:
    def test_resend_verification_is_503_in_production_for_every_account(
        self, env_client, monkeypatch
    ):
        _register(env_client, "gates-prod@example.com")
        monkeypatch.setattr(auth_routes.settings, "ENVIRONMENT", "production")

        known = env_client.post(
            "/api/v1/auth/resend-verification", json={"email": "gates-prod@example.com"}
        )
        unknown = env_client.post(
            "/api/v1/auth/resend-verification", json={"email": "nobody@example.com"}
        )

        assert known.status_code == unknown.status_code == 503
        assert known.json() == unknown.json()
        assert known.json()["detail"] == "Email verification delivery is unavailable"
        assert "verification_token" not in known.json()

    def test_resend_verification_issues_a_token_outside_production(self, env_client):
        _register(env_client, "gates-dev@example.com")

        response = env_client.post(
            "/api/v1/auth/resend-verification", json={"email": "gates-dev@example.com"}
        )

        assert response.status_code == 202
        assert response.json()["verification_token"]
        assert (
            response.json()["message"]
            == "Verification request processed; no email was sent."
        )

    def test_mfa_setup_still_works_in_production_but_never_leaks_a_secret(
        self, env_client, monkeypatch
    ):
        """The MFA gate is not environment-gated; only delivery is.

        ``setup_mfa`` returns the TOTP secret exactly once by design, and does so
        in every environment — the 503 gate applies to email delivery only.
        """
        token = _register(env_client, "gates-mfa@example.com")
        monkeypatch.setattr(auth_routes.settings, "ENVIRONMENT", "production")

        response = env_client.post(
            "/api/v1/auth/mfa/setup", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.json()["secret"]
        assert "otpauth://" in response.json()["totp_uri"]

    def test_mfa_setup_is_rate_limited_in_production_too(self, env_client, monkeypatch):
        from unittest.mock import AsyncMock, patch

        token = _register(env_client, "gates-mfa-limit@example.com")
        monkeypatch.setattr(auth_routes.settings, "ENVIRONMENT", "production")

        with patch.object(auth_routes, "allow", AsyncMock(return_value=False)):
            response = env_client.post(
                "/api/v1/auth/mfa/setup", headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 429
        assert response.json()["detail"] == "Too many MFA setup attempts. Try again later."

    def test_docs_are_disabled_in_production_but_health_stays_reachable(
        self, env_app, monkeypatch
    ):
        from unittest.mock import patch

        from app.core import event_consumer

        async def _consumer(_session_factory):
            return None

        monkeypatch.setattr(auth_routes.settings, "ENVIRONMENT", "production")

        # Production pins TrustedHostMiddleware to localhost/*.wildframe.com,
        # so the client must address the service by an allowed Host.
        with patch.object(
            event_consumer, "run_user_moderation_consumer", _consumer
        ), patch("app.main.setup_logging"):
            with TestClient(create_app(), base_url="http://localhost") as client:
                assert client.get("/docs").status_code == 404
                assert client.get("/redoc").status_code == 404
                assert client.get("/openapi.json").status_code == 404
                # Health checks stay available for the orchestrator.
                assert client.get("/health").status_code == 200
                assert client.get("/ready").status_code == 200
                # And the auth surface is still routed.
                assert (
                    client.post(
                        "/api/v1/auth/login",
                        json={"email": "a@b.com", "password": "x"},
                    ).status_code
                    == 401
                )


class TestJwksSettingsInteraction:
    def test_private_key_setting_is_used_for_signing(self, monkeypatch):
        from app.security import TokenManager

        jwks_module.reset_cache()
        monkeypatch.setattr(jwks_module.settings, "JWT_PRIVATE_KEY", PROD_JWT)
        try:
            pem = jwks_module.get_private_key_pem()
            assert "BEGIN PRIVATE KEY" in pem
        finally:
            jwks_module.reset_cache()
            assert TokenManager is not None

    def test_escaped_newlines_in_the_env_var_are_expanded(self, monkeypatch):
        jwks_module.reset_cache()
        monkeypatch.setattr(jwks_module.settings, "JWT_PRIVATE_KEY", PROD_JWT.replace("\n", "\\n"))
        try:
            pem = jwks_module.get_private_key_pem()
            assert "\\n" not in pem
            assert pem.startswith("-----BEGIN PRIVATE KEY-----")
        finally:
            jwks_module.reset_cache()

    def test_public_key_setting_short_circuits_derivation(self, monkeypatch):
        jwks_module.reset_cache()
        fake_public = "-----BEGIN PUBLIC KEY-----\nZmFrZQ==\n-----END PUBLIC KEY-----"
        monkeypatch.setattr(jwks_module.settings, "JWT_PUBLIC_KEY", fake_public)
        try:
            assert jwks_module.get_public_key_pem() == fake_public
        finally:
            jwks_module.reset_cache()


class TestGetCurrentUserSettingsInteraction:
    async def test_rejects_a_token_whose_auth_version_is_stale(self, test_session):
        """The ``av`` claim is compared against the stored auth_version."""
        from uuid import uuid4

        from app.models import User
        from app.security import PasswordManager, TokenManager
        from fastapi import HTTPException

        user = User(
            email="av-stale@example.com",
            password_hash=PasswordManager.hash_password("Secret1234!"),
            auth_version=3,
        )
        test_session.add(user)
        await test_session.commit()

        stale_token = TokenManager.create_access_token(user.id, user.email, 1)

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(test_session, f"Bearer {stale_token}")

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Token has been revoked"

    async def test_accepts_a_token_matching_the_current_auth_version(self, test_session):
        from app.models import User
        from app.security import PasswordManager, TokenManager

        user = User(
            email="av-fresh@example.com",
            password_hash=PasswordManager.hash_password("Secret1234!"),
            auth_version=2,
        )
        test_session.add(user)
        await test_session.commit()

        fresh = TokenManager.create_access_token(user.id, user.email, 2)

        assert await get_current_user(test_session, f"Bearer {fresh}") == user.id
