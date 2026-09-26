"""Tests for the production guard rails in app/core/settings.py.

``Settings.validate_production_secrets`` is the last line of defence that stops
a staging-grade configuration (default DB credentials, dev JWT secret, missing
Redis/Kafka URLs) from reaching production. Every rejection branch is exercised
here, plus the dev-environment escape hatch and the development defaults that
are injected before validation runs.
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
GOOD_DB = "postgresql+asyncpg://app_user:s3cr3t@db.internal:5432/content_db"
GOOD_REDIS = "redis://cache.internal:6379/0"
GOOD_KAFKA = "kafka.internal:9092"


def _production_kwargs(**overrides):
    base = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": GOOD_DB,
        "REDIS_URL": GOOD_REDIS,
        "JWT_SECRET_KEY": STRONG_SECRET,
        "KAFKA_BOOTSTRAP_SERVERS": GOOD_KAFKA,
    }
    base.update(overrides)
    return base


class TestProductionAcceptsCompleteConfig:
    def test_full_production_config_validates(self):
        cfg = Settings(**_production_kwargs())

        assert cfg.ENVIRONMENT == "production"
        assert cfg.DATABASE_URL == GOOD_DB
        assert cfg.JWT_SECRET_KEY == STRONG_SECRET

    def test_staging_environment_is_treated_as_production_like(self):
        cfg = Settings(**_production_kwargs(ENVIRONMENT="staging"))

        assert cfg.ENVIRONMENT == "staging"

    @pytest.mark.parametrize("environment", sorted(DEV_ENVIRONMENTS))
    def test_dev_environments_short_circuit_the_secret_checks(self, environment):
        cfg = Settings(
            ENVIRONMENT=environment,
            DATABASE_URL="postgresql+asyncpg://postgres:password@h:5432/d",
            REDIS_URL=None,
            JWT_SECRET_KEY="secret",
            KAFKA_BOOTSTRAP_SERVERS=None,
        )

        assert cfg.ENVIRONMENT == environment

    def test_dev_defaults_are_injected_when_unset(self):
        cfg = Settings(ENVIRONMENT="development")

        for key, value in DEV_DEFAULTS.items():
            assert getattr(cfg, key) == value

    def test_dev_defaults_do_not_override_explicit_values(self):
        cfg = Settings(ENVIRONMENT="development", REDIS_URL="redis://explicit:6379")

        assert cfg.REDIS_URL == "redis://explicit:6379"

    def test_production_gets_no_injected_defaults(self):
        with pytest.raises(ValidationError, match="DATABASE_URL must be set explicitly"):
            Settings(ENVIRONMENT="production")

    def test_module_level_settings_is_a_validated_instance(self):
        assert isinstance(live_settings, Settings)
        assert live_settings.SERVICE_NAME == "content-service"
        assert live_settings.ENVIRONMENT in DEV_ENVIRONMENTS


class TestProductionRejectsMissingDatabaseUrl:
    def test_missing_database_url(self):
        with pytest.raises(ValidationError) as exc:
            Settings(**_production_kwargs(DATABASE_URL=None))

        assert "DATABASE_URL must be set explicitly" in str(exc.value)

    @pytest.mark.parametrize("credential", KNOWN_INSECURE_DB_CREDENTIALS)
    def test_known_default_db_credentials(self, credential):
        url = f"postgresql+asyncpg://{credential}@db.internal:5432/content_db"

        with pytest.raises(ValidationError, match="known default credentials"):
            Settings(**_production_kwargs(DATABASE_URL=url))

    def test_weak_password_containing_a_known_credential_is_rejected(self):
        url = "postgresql+asyncpg://svc:wildframe:password@db.internal:5432/content_db"

        with pytest.raises(ValidationError, match="known default credentials"):
            Settings(**_production_kwargs(DATABASE_URL=url))


class TestProductionRejectsMissingRedisUrl:
    def test_missing_redis_url(self):
        with pytest.raises(ValidationError) as exc:
            Settings(**_production_kwargs(REDIS_URL=None))

        assert "REDIS_URL must be set explicitly" in str(exc.value)


class TestProductionRejectsWeakJwtSecret:
    def test_missing_jwt_secret(self):
        with pytest.raises(ValidationError) as exc:
            Settings(**_production_kwargs(JWT_SECRET_KEY=None))

        assert "JWT_SECRET_KEY must be set to a strong random value" in str(exc.value)

    @pytest.mark.parametrize("secret", KNOWN_INSECURE_JWT_SECRETS)
    def test_known_insecure_jwt_secrets(self, secret):
        with pytest.raises(ValidationError, match="strong random value"):
            Settings(**_production_kwargs(JWT_SECRET_KEY=secret))

    @pytest.mark.parametrize("secret", ["x" * 31, "x", ""])
    def test_secret_shorter_than_32_characters(self, secret):
        if secret in KNOWN_INSECURE_JWT_SECRETS:
            pytest.skip("covered by the known-insecure-secret case")

        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings(**_production_kwargs(JWT_SECRET_KEY=secret))

    def test_exactly_32_characters_is_accepted(self):
        cfg = Settings(**_production_kwargs(JWT_SECRET_KEY="y" * 32))

        assert len(cfg.JWT_SECRET_KEY) == 32


class TestProductionRejectsMissingKafka:
    def test_missing_kafka_bootstrap_servers(self):
        with pytest.raises(ValidationError) as exc:
            Settings(**_production_kwargs(KAFKA_BOOTSTRAP_SERVERS=None))

        assert "KAFKA_BOOTSTRAP_SERVERS must be set explicitly" in str(exc.value)


class TestRejectionOrder:
    def test_database_url_is_checked_before_redis(self):
        """The first failing guard wins so the operator sees one message at a time."""
        with pytest.raises(ValidationError) as exc:
            Settings(
                ENVIRONMENT="production",
                REDIS_URL=None,
                JWT_SECRET_KEY=None,
                KAFKA_BOOTSTRAP_SERVERS=None,
            )

        assert "DATABASE_URL must be set explicitly" in str(exc.value)

    def test_jwt_is_checked_before_kafka(self):
        with pytest.raises(ValidationError) as exc:
            Settings(
                ENVIRONMENT="production",
                DATABASE_URL=GOOD_DB,
                REDIS_URL=GOOD_REDIS,
                JWT_SECRET_KEY="tooshort",
                KAFKA_BOOTSTRAP_SERVERS=None,
            )

        assert "at least 32 characters" in str(exc.value)


class TestNonSecretSettings:
    def test_jwt_audience_and_issuer_defaults(self):
        cfg = Settings(ENVIRONMENT="development")

        assert cfg.JWT_AUDIENCE == "wildframe-api"
        assert cfg.JWT_ISSUER == "wildframe-auth"
        assert cfg.JWT_ALGORITHM == "HS256"

    def test_content_service_port_and_cors_defaults(self):
        cfg = Settings(ENVIRONMENT="development")

        assert cfg.SERVER_PORT == 8003
        assert cfg.CORS_ALLOWED_ORIGINS == [
            "http://localhost:3000",
            "http://localhost:8000",
        ]

    def test_event_publisher_defaults_to_memory(self):
        cfg = Settings(ENVIRONMENT="development")

        assert cfg.EVENT_PUBLISHER == "memory"
        assert cfg.ADMIN_ROLE_VERSION == 0
