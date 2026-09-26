"""Behavioural tests for ``app.core.settings`` (admin-service).

The production guard is the only thing standing between a misconfigured
deployment and a service that boots with the repository's public dev JWT
secret, so every rejection branch is asserted individually, plus the
development-default path that the whole dev stack relies on.
"""

import pytest
from pydantic import ValidationError

from app.core import settings as settings_mod
from app.core.settings import (
    DEV_DEFAULTS,
    DEV_ENVIRONMENTS,
    KNOWN_INSECURE_DB_CREDENTIALS,
    KNOWN_INSECURE_JWT_SECRETS,
    Settings,
    get_settings,
)

# A production-shaped payload that passes every guard; individual tests copy
# and break exactly one field.
STRONG_JWT = "k" + "Q" * 40  # 41 chars, not on the insecure list
PROD_BASE = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": "postgresql+asyncpg://real_user:s3cr3t-pass@db.internal:5432/admin_db",
    "REDIS_URL": "redis://redis.internal:6379/2",
    "JWT_SECRET_KEY": STRONG_JWT,
    "KAFKA_BOOTSTRAP_SERVERS": "kafka.internal:9092",
}


def _prod(**overrides):
    return {**PROD_BASE, **overrides}


class TestDevelopmentDefaults:
    def test_empty_environment_is_a_dev_environment(self):
        assert "" in DEV_ENVIRONMENTS

    @pytest.mark.parametrize("environment", ["", "development", "test"])
    def test_dev_defaults_are_filled_in(self, environment):
        s = Settings(ENVIRONMENT=environment)
        for key, expected in DEV_DEFAULTS.items():
            assert getattr(s, key) == expected

    def test_dev_defaults_do_not_override_explicit_values(self):
        s = Settings(ENVIRONMENT="test", DATABASE_URL="postgresql+asyncpg://u:p@h/db")
        assert s.DATABASE_URL == "postgresql+asyncpg://u:p@h/db"

    def test_dev_environment_skips_every_production_guard(self):
        # The dev default JWT secret is on the insecure list, yet dev boots.
        s = Settings(ENVIRONMENT="development")
        assert s.JWT_SECRET_KEY in KNOWN_INSECURE_JWT_SECRETS
        assert s.JWT_SECRET_KEY is not None

    def test_dev_default_db_credential_is_on_the_insecure_list(self):
        s = Settings(ENVIRONMENT="development")
        assert any(c in s.DATABASE_URL for c in KNOWN_INSECURE_DB_CREDENTIALS)


class TestProductionAcceptsStrongConfig:
    def test_fully_hardened_production_config_is_accepted(self):
        s = Settings(**PROD_BASE)
        assert s.ENVIRONMENT == "production"
        assert s.DATABASE_URL == PROD_BASE["DATABASE_URL"]
        assert s.JWT_SECRET_KEY == STRONG_JWT

    def test_staging_is_also_guarded_like_production(self):
        s = Settings(**_prod(ENVIRONMENT="staging"))
        assert s.ENVIRONMENT == "staging"

    def test_validator_returns_self(self):
        s = Settings(**PROD_BASE)
        assert s.validate_production_secrets() is s


class TestProductionRejections:
    def test_missing_database_url(self):
        with pytest.raises(ValidationError, match="DATABASE_URL must be set explicitly"):
            Settings(**_prod(DATABASE_URL=None))

    @pytest.mark.parametrize("credential", KNOWN_INSECURE_DB_CREDENTIALS)
    def test_known_insecure_db_credentials(self, credential):
        url = f"postgresql+asyncpg://{credential}@db:5432/admin_db"
        with pytest.raises(ValidationError, match="known default credentials"):
            Settings(**_prod(DATABASE_URL=url))

    def test_missing_redis_url(self):
        with pytest.raises(ValidationError, match="REDIS_URL must be set explicitly"):
            Settings(**_prod(REDIS_URL=None))

    def test_missing_jwt_secret(self):
        with pytest.raises(ValidationError, match="JWT_SECRET_KEY must be set to a strong"):
            Settings(**_prod(JWT_SECRET_KEY=None))

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_blank_jwt_secret_rejected(self, blank):
        with pytest.raises(ValidationError, match="strong random value"):
            Settings(**_prod(JWT_SECRET_KEY=blank))

    @pytest.mark.parametrize("insecure", KNOWN_INSECURE_JWT_SECRETS)
    def test_known_insecure_jwt_secrets(self, insecure):
        with pytest.raises(ValidationError, match="strong random value"):
            Settings(**_prod(JWT_SECRET_KEY=insecure))

    def test_short_jwt_secret(self):
        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings(**_prod(JWT_SECRET_KEY="a" * 31))

    def test_exactly_32_char_jwt_secret_is_accepted(self):
        secret = "z" * 32
        s = Settings(**_prod(JWT_SECRET_KEY=secret))
        assert s.JWT_SECRET_KEY == secret

    def test_missing_kafka_bootstrap_servers(self):
        with pytest.raises(ValidationError, match="KAFKA_BOOTSTRAP_SERVERS must be set"):
            Settings(**_prod(KAFKA_BOOTSTRAP_SERVERS=None))


class TestUpstreamComplianceGuard:
    def test_local_validator_is_the_only_enforcement_layer(self):
        # The docstring on ``validate_production_secrets`` credits an "upstream
        # production guard" with rejecting blank secrets and a
        # ``default_secrets`` list. Pin what the installed mixin actually does
        # so the test suite tells the truth about how many layers exist.
        from wildframe_compliance.settings import ComplianceSettingsMixin

        own = {
            name
            for name in vars(ComplianceSettingsMixin)
            if not name.startswith("__") and not name.startswith("_")
        }
        assert "default_secrets" not in own, (
            "upstream mixin now exposes default_secrets; the local "
            "KNOWN_INSECURE_JWT_SECRETS list must be re-checked for overlap"
        )
        # No upstream validator is declared either: the local one is the guard.
        assert not [n for n in own if n.startswith("validate")]

    def test_blank_secret_is_rejected_by_the_local_guard_alone(self):
        # Proves the local validator alone is sufficient for the blank case
        # attributed to the upstream guard in the docstring.
        with pytest.raises(ValidationError, match="strong random value"):
            Settings(**_prod(JWT_SECRET_KEY="   "))

    @pytest.mark.parametrize("insecure", KNOWN_INSECURE_JWT_SECRETS)
    def test_each_locally_enforced_secret_is_actually_rejected(self, insecure):
        with pytest.raises(ValidationError):
            Settings(**_prod(JWT_SECRET_KEY=insecure))


class TestGetSettings:
    def test_get_settings_is_cached(self):
        assert get_settings() is get_settings()

    def test_get_settings_returns_a_settings_instance(self):
        assert isinstance(get_settings(), Settings)

    def test_module_level_settings_is_the_cached_singleton(self):
        assert settings_mod.settings is get_settings()

    def test_service_identity_defaults(self):
        s = Settings(ENVIRONMENT="test")
        assert s.SERVICE_NAME == "admin-service"
        assert s.SERVER_PORT == 8006
        assert s.JWT_AUDIENCE == "wildframe-api"
        assert s.JWT_ISSUER == "wildframe-auth"
        assert s.ADMIN_ROLE_VERSION == 0
