"""Behavioural tests for app/core/settings.py (creators-service).

Two guard rails live here: ``validate_production_secrets`` (no default DB
credentials, no dev JWT secret, explicit Redis URL outside development) and
``validate_cors_credentials`` (a wildcard origin list must not be combined with
credentialed CORS in production). Both are exercised branch by branch, along
with the development defaults that are injected before validation.
"""

import pytest
from pydantic import ValidationError

from app.core.settings import (
    DEV_DEFAULTS,
    DEV_ENVIRONMENTS,
    KNOWN_INSECURE_DB_CREDENTIALS,
    KNOWN_INSECURE_JWT_SECRETS,
    Settings,
    settings as live_settings,
)

pytestmark = pytest.mark.unit

STRONG_SECRET = "k" * 40
GOOD_DB = "postgresql+asyncpg://app_user:s3cr3t@db.internal:5432/creators_db"
GOOD_REDIS = "redis://cache.internal:6379"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Settings reads the process environment.

    Another test module in this suite exports ``DATABASE_URL`` (sqlite) to make
    the live app importable, so the environment has to be cleared for these
    tests to observe the *defaults* rather than that leak.
    """
    for name in ("ENVIRONMENT", "DATABASE_URL", "REDIS_URL", "JWT_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)


def _production_kwargs(**overrides):
    base = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": GOOD_DB,
        "REDIS_URL": GOOD_REDIS,
        "JWT_SECRET_KEY": STRONG_SECRET,
    }
    base.update(overrides)
    return base


class TestProductionAcceptsCompleteConfig:
    def test_full_production_config_validates(self):
        cfg = Settings(**_production_kwargs())

        assert cfg.ENVIRONMENT == "production"
        assert cfg.DATABASE_URL == GOOD_DB
        assert cfg.SERVICE_NAME == "Creators"

    def test_staging_is_validated_like_production(self):
        cfg = Settings(**_production_kwargs(ENVIRONMENT="staging"))

        assert cfg.ENVIRONMENT == "staging"

    def test_creators_service_defaults(self):
        cfg = Settings(ENVIRONMENT="development")

        assert cfg.SERVER_PORT == 8012
        assert cfg.POOL_RATE == 0.15
        assert cfg.JWT_EXPIRATION_MINUTES == 15
        assert cfg.CORS_ALLOW_CREDENTIALS is True
        assert cfg.JWT_AUDIENCE == "wildframe-api"

    def test_module_level_settings_is_a_validated_instance(self):
        assert isinstance(live_settings, Settings)
        assert live_settings.ENVIRONMENT in DEV_ENVIRONMENTS


class TestDevelopmentDefaults:
    def test_defaults_are_injected_when_unset(self):
        cfg = Settings(ENVIRONMENT="development")

        for key, value in DEV_DEFAULTS.items():
            assert getattr(cfg, key) == value

    def test_defaults_do_not_override_explicit_values(self):
        cfg = Settings(ENVIRONMENT="development", REDIS_URL="redis://explicit:6379")

        assert cfg.REDIS_URL == "redis://explicit:6379"

    def test_production_gets_no_injected_defaults(self):
        with pytest.raises(ValidationError, match="DATABASE_URL must be set explicitly"):
            Settings(ENVIRONMENT="production")

    @pytest.mark.parametrize("environment", sorted(DEV_ENVIRONMENTS))
    def test_dev_environments_short_circuit_the_secret_checks(self, environment):
        cfg = Settings(
            ENVIRONMENT=environment,
            DATABASE_URL="postgresql+asyncpg://postgres:password@h:5432/d",
            REDIS_URL=None,
            JWT_SECRET_KEY="secret",
        )

        assert cfg.ENVIRONMENT == environment


class TestDatabaseUrlGuard:
    def test_missing_database_url(self):
        with pytest.raises(ValidationError) as exc:
            Settings(**_production_kwargs(DATABASE_URL=None))

        assert "DATABASE_URL must be set explicitly" in str(exc.value)

    @pytest.mark.parametrize("credential", KNOWN_INSECURE_DB_CREDENTIALS)
    def test_known_default_db_credentials(self, credential):
        url = f"postgresql+asyncpg://{credential}@db.internal:5432/creators_db"

        with pytest.raises(ValidationError, match="known default credentials"):
            Settings(**_production_kwargs(DATABASE_URL=url))


class TestRedisUrlGuard:
    def test_missing_redis_url(self):
        with pytest.raises(ValidationError) as exc:
            Settings(**_production_kwargs(REDIS_URL=None))

        assert "REDIS_URL must be set explicitly" in str(exc.value)


class TestJwtSecretGuard:
    def test_missing_jwt_secret(self):
        with pytest.raises(ValidationError) as exc:
            Settings(**_production_kwargs(JWT_SECRET_KEY=None))

        assert "JWT_SECRET_KEY must be set to a strong random value" in str(exc.value)

    @pytest.mark.parametrize("secret", KNOWN_INSECURE_JWT_SECRETS)
    def test_known_insecure_jwt_secrets(self, secret):
        with pytest.raises(ValidationError, match="strong random value"):
            Settings(**_production_kwargs(JWT_SECRET_KEY=secret))

    @pytest.mark.parametrize("secret", ["x" * 31, "x", "y" * 8])
    def test_secret_shorter_than_32_characters(self, secret):
        if secret in KNOWN_INSECURE_JWT_SECRETS:
            pytest.skip("covered by the known-insecure-secret case")

        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings(**_production_kwargs(JWT_SECRET_KEY=secret))

    def test_exactly_32_characters_is_accepted(self):
        cfg = Settings(**_production_kwargs(JWT_SECRET_KEY="y" * 32))

        assert len(cfg.JWT_SECRET_KEY) == 32


class TestRejectionOrder:
    def test_database_url_is_reported_before_redis(self):
        with pytest.raises(ValidationError) as exc:
            Settings(ENVIRONMENT="production", REDIS_URL=None, JWT_SECRET_KEY=None)

        assert "DATABASE_URL must be set explicitly" in str(exc.value)

    def test_redis_is_reported_before_the_jwt_secret(self):
        with pytest.raises(ValidationError) as exc:
            Settings(
                ENVIRONMENT="production",
                DATABASE_URL=GOOD_DB,
                REDIS_URL=None,
                JWT_SECRET_KEY="tooshort",
            )

        assert "REDIS_URL must be set explicitly" in str(exc.value)


class TestCorsCredentialsGuard:
    def test_wildcard_origin_with_credentials_is_rejected_in_production(self):
        with pytest.raises(ValidationError) as exc:
            Settings(
                **_production_kwargs(CORS_ALLOWED_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=True)
            )

        assert "CORS_ALLOWED_ORIGINS cannot be ['*']" in str(exc.value)

    def test_wildcard_origin_without_credentials_is_allowed(self):
        cfg = Settings(
            **_production_kwargs(CORS_ALLOWED_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=False)
        )

        assert cfg.CORS_ALLOWED_ORIGINS == ["*"]

    def test_explicit_origins_with_credentials_are_allowed(self):
        origins = ["https://app.wildframe.com"]

        cfg = Settings(**_production_kwargs(CORS_ALLOWED_ORIGINS=origins))

        assert cfg.CORS_ALLOWED_ORIGINS == origins

    def test_wildcard_origin_is_allowed_outside_production(self):
        cfg = Settings(ENVIRONMENT="development", CORS_ALLOWED_ORIGINS=["*"])

        assert cfg.CORS_ALLOWED_ORIGINS == ["*"]
