"""Settings validation for recommendation-service.

Every case constructs ``Settings(**kwargs)`` directly; the process environment
is never mutated, so these assertions are independent of whatever env the
suite runs with.
"""

import pytest
from pydantic import ValidationError

from app.core.settings import (
    DEV_DEFAULTS,
    DEV_ENVIRONMENTS,
    KNOWN_INSECURE_DB_CREDENTIALS,
    KNOWN_INSECURE_JWT_SECRETS,
    Settings,
)

STRONG_DB_URL = "postgresql+asyncpg://app_user:s3cret-passphrase@db.internal:5432/rec_db"
STRONG_JWT_SECRET = "k7Qm2Xz9Tb4LpR8vNc6Wy1Ae5Hd0Jf3Ug5SiOq7X"  # 40 chars, not a known default


def _clear_dev_env(monkeypatch) -> None:
    """Remove the env overrides the suite sets, so DEV_DEFAULTS stay visible."""
    for key in (*DEV_DEFAULTS, "ENVIRONMENT", "METRICS_TOKEN"):
        monkeypatch.delenv(key, raising=False)


def production(**overrides) -> dict:
    base = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": STRONG_DB_URL,
        "REDIS_URL": "redis://redis.internal:6379/0",
        "JWT_SECRET_KEY": STRONG_JWT_SECRET,
        "KAFKA_BOOTSTRAP_SERVERS": "kafka:29092",
    }
    base.update(overrides)
    return base


class TestProductionSecretsAccepted:
    def test_fully_specified_production_settings_validate(self):
        settings = Settings(**production())

        assert settings.ENVIRONMENT == "production"
        assert settings.DATABASE_URL == STRONG_DB_URL

    @pytest.mark.parametrize("environment", ["staging", "qa", "prod", "canary"])
    def test_any_non_dev_environment_is_gated(self, environment):
        """The gate is "not a dev environment", not the literal 'production'."""
        with pytest.raises(ValidationError):
            Settings(ENVIRONMENT=environment)


class TestDatabaseUrlGate:
    def test_missing_database_url_is_rejected(self):
        with pytest.raises(ValidationError, match="DATABASE_URL must be set explicitly"):
            Settings(**production(DATABASE_URL=None))

    @pytest.mark.parametrize("credential", KNOWN_INSECURE_DB_CREDENTIALS)
    def test_known_default_credentials_are_rejected(self, credential):
        url = f"postgresql+asyncpg://{credential}@db.internal:5432/rec_db"

        with pytest.raises(ValidationError, match="must not use known default credentials"):
            Settings(**production(DATABASE_URL=url))

    def test_unfamiliar_credentials_are_accepted(self):
        url = "postgresql+asyncpg://rec_owner:hunter2-correct-horse@db:5432/rec_db"

        assert Settings(**production(DATABASE_URL=url)).DATABASE_URL == url


class TestRedisUrlGate:
    def test_missing_redis_url_is_rejected(self):
        with pytest.raises(ValidationError, match="REDIS_URL must be set explicitly"):
            Settings(**production(REDIS_URL=None))


class TestJwtSecretGate:
    def test_missing_jwt_secret_is_rejected(self):
        with pytest.raises(ValidationError, match="JWT_SECRET_KEY must be set"):
            Settings(**production(JWT_SECRET_KEY=None))

    @pytest.mark.parametrize("secret", KNOWN_INSECURE_JWT_SECRETS)
    def test_known_default_secrets_are_rejected(self, secret):
        with pytest.raises(ValidationError, match="strong random value"):
            Settings(**production(JWT_SECRET_KEY=secret))

    def test_short_secret_is_rejected(self):
        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings(**production(JWT_SECRET_KEY="a" * 31))

    def test_exactly_32_characters_is_accepted(self):
        secret = "b" * 32

        assert Settings(**production(JWT_SECRET_KEY=secret)).JWT_SECRET_KEY == secret


class TestKafkaGate:
    def test_missing_kafka_bootstrap_is_rejected(self):
        with pytest.raises(ValidationError, match="KAFKA_BOOTSTRAP_SERVERS must be set"):
            Settings(**production(KAFKA_BOOTSTRAP_SERVERS=None))

    def test_gate_ordering_is_observable(self):
        """The first failing rule owns the error message."""
        with pytest.raises(ValidationError, match="DATABASE_URL must be set explicitly"):
            Settings(
                ENVIRONMENT="production",
                DATABASE_URL=None,
                REDIS_URL=None,
                JWT_SECRET_KEY=None,
                KAFKA_BOOTSTRAP_SERVERS=None,
            )


class TestDevelopmentDefaults:
    @pytest.mark.parametrize("environment", sorted(DEV_ENVIRONMENTS))
    def test_dev_environments_get_the_documented_defaults(self, environment, monkeypatch):
        _clear_dev_env(monkeypatch)
        settings = Settings(ENVIRONMENT=environment)

        for key, value in DEV_DEFAULTS.items():
            assert getattr(settings, key) == value

    def test_environment_variables_win_over_the_dev_defaults(self, monkeypatch):
        _clear_dev_env(monkeypatch)
        monkeypatch.setenv("REDIS_URL", "redis://env-host:6379/9")

        settings = Settings(ENVIRONMENT="test")

        assert settings.REDIS_URL == "redis://env-host:6379/9"
        # The remaining defaults are still applied.
        assert settings.DATABASE_URL == DEV_DEFAULTS["DATABASE_URL"]

    def test_explicit_values_win_over_the_defaults(self, monkeypatch):
        _clear_dev_env(monkeypatch)
        settings = Settings(
            ENVIRONMENT="test",
            DATABASE_URL="postgresql+asyncpg://real:pw@realhost:5432/other",
        )

        assert settings.DATABASE_URL == "postgresql+asyncpg://real:pw@realhost:5432/other"

    def test_unset_environment_treated_as_development(self, monkeypatch):
        _clear_dev_env(monkeypatch)

        settings = Settings()

        assert settings.ENVIRONMENT == "development"
        assert settings.DATABASE_URL == DEV_DEFAULTS["DATABASE_URL"]
        assert settings.KAFKA_BOOTSTRAP_SERVERS == DEV_DEFAULTS["KAFKA_BOOTSTRAP_SERVERS"]

    def test_defaults_are_not_applied_outside_dev_environments(self, monkeypatch):
        _clear_dev_env(monkeypatch)

        with pytest.raises(ValidationError, match="DATABASE_URL must be set explicitly"):
            Settings(ENVIRONMENT="production")

    def test_weak_secrets_are_tolerated_in_development(self, monkeypatch):
        _clear_dev_env(monkeypatch)
        settings = Settings(ENVIRONMENT="development", JWT_SECRET_KEY="secret")

        assert settings.JWT_SECRET_KEY == "secret"


class TestMetricsToken:
    def test_defaults_to_empty(self, monkeypatch):
        _clear_dev_env(monkeypatch)

        assert Settings(ENVIRONMENT="development").METRICS_TOKEN == ""

    def test_is_configurable_in_production(self):
        assert Settings(**production(METRICS_TOKEN="t0ken")).METRICS_TOKEN == "t0ken"

    def test_empty_token_passes_validation(self):
        """An empty token is *not* rejected here; /metrics rejects it at request time."""
        assert Settings(**production(METRICS_TOKEN="")).METRICS_TOKEN == ""


class TestNoCorsGate:
    def test_wildcard_origins_with_credentials_are_not_gated(self, monkeypatch):
        """Unlike search-service, this service has no CORS production validator."""
        _clear_dev_env(monkeypatch)

        settings = Settings(
            ENVIRONMENT="production",
            DATABASE_URL=STRONG_DB_URL,
            REDIS_URL="redis://redis:6379/0",
            JWT_SECRET_KEY=STRONG_JWT_SECRET,
            KAFKA_BOOTSTRAP_SERVERS="kafka:29092",
            CORS_ALLOWED_ORIGINS=["*"],
            CORS_ALLOW_CREDENTIALS=True,
        )

        assert settings.CORS_ALLOWED_ORIGINS == ["*"]
        assert settings.CORS_ALLOW_CREDENTIALS is True


class TestDefaults:
    def test_service_identity_and_version(self, monkeypatch):
        _clear_dev_env(monkeypatch)
        settings = Settings()

        assert settings.SERVICE_NAME == "Recommendation"
        assert settings.SERVICE_VERSION == "1.0.0"
        assert settings.SERVER_PORT == 8007

    def test_jwt_contract(self, monkeypatch):
        _clear_dev_env(monkeypatch)
        settings = Settings()

        assert settings.JWT_ALGORITHM == "HS256"
        assert settings.JWT_AUDIENCE == "wildframe-api"
        assert settings.JWT_ISSUER == "wildframe-auth"
        assert settings.JWT_EXPIRATION_MINUTES == 15

    def test_bounds_are_sane(self, monkeypatch):
        _clear_dev_env(monkeypatch)
        settings = Settings()

        assert settings.MAX_RECOMMENDATION_LIMIT == 100
        assert settings.MAX_PREFERENCE_GENRES == 50
        assert settings.MAX_CANDIDATES == 500
        assert settings.MAX_CATALOG_PAGE_SIZE == 100

    def test_catalog_transport_defaults(self, monkeypatch):
        _clear_dev_env(monkeypatch)
        settings = Settings()

        assert settings.CONTENT_SERVICE_URL == "http://content-service:8003"
        assert settings.CONTENT_CATALOG_TIMEOUT_SECONDS == 10.0
        assert settings.CONTENT_CATALOG_MAX_CONNECTIONS == 20
        assert settings.CONTENT_CATALOG_MAX_KEEPALIVE == 10

    def test_event_transport_defaults(self, monkeypatch):
        _clear_dev_env(monkeypatch)
        settings = Settings()

        assert settings.EVENT_PUBLISHER == "memory"
        assert settings.KAFKA_CONSUMER_GROUP == "recommendation-service"

    def test_compliance_defaults(self, monkeypatch):
        _clear_dev_env(monkeypatch)
        settings = Settings()

        # tests/conftest.py stubs wildframe_compliance with a plain Enum, so the
        # values are ordinals rather than the real string codes.
        assert settings.compliance_jurisdiction.name == "GLOBAL"
        assert [j.name for j in settings.compliance_additional_jurisdictions] == [
            "EU",
            "US",
            "IN",
        ]
        assert settings.compliance_dpo_email == "dpo@wildframe.com"
        assert settings.compliance_grievance_officer_email == "grievance@wildframe.com"
        assert settings.compliance_allowed_data_regions == ["US", "EU", "IN", "SG"]
