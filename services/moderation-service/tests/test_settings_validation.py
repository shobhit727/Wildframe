"""Behavioural tests for ``app.core.settings`` (moderation-service).

Same production guard as every other Wildframe service, but the two copies are
not identical: moderation's version has no whitespace-only secret check and its
``Settings`` is instantiated eagerly at import (no ``lru_cache``). Both
differences are pinned here.
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
    settings,
)

STRONG_JWT = "s" + "T" * 40
PROD_BASE = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": "postgresql+asyncpg://real_user:s3cr3t@db.internal:5432/moderation_db",
    "REDIS_URL": "redis://redis.internal:6379",
    "JWT_SECRET_KEY": STRONG_JWT,
    "KAFKA_BOOTSTRAP_SERVERS": "kafka.internal:9092",
}


def _prod(**overrides):
    return {**PROD_BASE, **overrides}


class TestDevelopmentDefaults:
    def test_dev_environments(self):
        assert DEV_ENVIRONMENTS == {"", "development", "test"}

    @pytest.mark.parametrize("environment", ["", "development", "test"])
    def test_defaults_are_filled_in(self, environment):
        s = Settings(ENVIRONMENT=environment)
        for key, expected in DEV_DEFAULTS.items():
            assert getattr(s, key) == expected

    def test_explicit_values_are_not_overwritten(self):
        s = Settings(ENVIRONMENT="test", REDIS_URL="redis://explicit:6379")
        assert s.REDIS_URL == "redis://explicit:6379"

    def test_dev_boot_is_permitted_with_the_insecure_default_secret(self):
        s = Settings(ENVIRONMENT="development")
        assert s.JWT_SECRET_KEY in KNOWN_INSECURE_JWT_SECRETS
        assert any(c in s.DATABASE_URL for c in KNOWN_INSECURE_DB_CREDENTIALS)

    def test_a_non_dev_environment_gets_no_defaults(self):
        with pytest.raises(ValidationError):
            Settings(ENVIRONMENT="production")


class TestProductionAcceptsStrongConfig:
    def test_hardened_config_is_accepted(self):
        s = Settings(**PROD_BASE)
        assert s.ENVIRONMENT == "production"
        assert s.JWT_SECRET_KEY == STRONG_JWT

    def test_staging_is_guarded_like_production(self):
        with pytest.raises(ValidationError):
            Settings(**_prod(ENVIRONMENT="staging", JWT_SECRET_KEY=None))

    def test_validator_returns_self(self):
        s = Settings(**PROD_BASE)
        assert s.validate_production_secrets() is s


class TestProductionRejections:
    def test_missing_database_url(self):
        with pytest.raises(ValidationError, match="DATABASE_URL must be set explicitly"):
            Settings(**_prod(DATABASE_URL=None))

    @pytest.mark.parametrize("credential", KNOWN_INSECURE_DB_CREDENTIALS)
    def test_known_insecure_db_credentials(self, credential):
        url = f"postgresql+asyncpg://{credential}@db:5432/moderation_db"
        with pytest.raises(ValidationError, match="known default credentials"):
            Settings(**_prod(DATABASE_URL=url))

    def test_missing_redis_url(self):
        with pytest.raises(ValidationError, match="REDIS_URL must be set explicitly"):
            Settings(**_prod(REDIS_URL=None))

    def test_missing_jwt_secret(self):
        with pytest.raises(ValidationError, match="strong random value"):
            Settings(**_prod(JWT_SECRET_KEY=None))

    @pytest.mark.parametrize("insecure", KNOWN_INSECURE_JWT_SECRETS)
    def test_known_insecure_jwt_secrets(self, insecure):
        with pytest.raises(ValidationError, match="strong random value"):
            Settings(**_prod(JWT_SECRET_KEY=insecure))

    def test_short_jwt_secret(self):
        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings(**_prod(JWT_SECRET_KEY="a" * 31))

    def test_exactly_32_characters_is_accepted(self):
        s = Settings(**_prod(JWT_SECRET_KEY="z" * 32))
        assert len(s.JWT_SECRET_KEY) == 32

    def test_missing_kafka_bootstrap_servers(self):
        with pytest.raises(ValidationError, match="KAFKA_BOOTSTRAP_SERVERS must be set"):
            Settings(**_prod(KAFKA_BOOTSTRAP_SERVERS=None))


class TestKnownGap_WhitespaceOnlySecret:
    """moderation-service's guard is weaker than admin-service's here.

    ``admin-service/app/core/settings.py`` adds an explicit
    ``if not self.JWT_SECRET_KEY.strip():`` rejection before the list and length
    checks. This copy has no such check, so a production config with a
    whitespace-only (or whitespace-padded) secret is accepted. Reported, not
    fixed; these tests pin the current behaviour so the divergence is visible.
    """

    def test_whitespace_only_secret_is_accepted_in_production(self):
        s = Settings(**_prod(JWT_SECRET_KEY=" " * 40))
        assert s.JWT_SECRET_KEY == " " * 40
        assert s.ENVIRONMENT == "production"

    def test_padded_strong_secret_is_accepted_verbatim(self):
        padded = f"  {STRONG_JWT}  "
        s = Settings(**_prod(JWT_SECRET_KEY=padded))
        assert s.JWT_SECRET_KEY == padded

    def test_a_genuinely_short_secret_is_still_rejected(self):
        # The length guard is the only thing standing between a blank-ish
        # secret and production here.
        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings(**_prod(JWT_SECRET_KEY=" " * 31))


class TestModuleLevelSettings:
    def test_settings_is_instantiated_at_import(self):
        assert isinstance(settings, Settings)
        assert settings_mod.settings is settings

    def test_defaults_match_the_service_identity(self):
        assert settings.SERVICE_NAME == "Moderation"
        assert settings.SERVER_PORT == 8013
        assert settings.JWT_AUDIENCE == "wildframe-api"
        assert settings.JWT_ISSUER == "wildframe-auth"
        assert settings.ADMIN_ROLE_VERSION == 0
        assert settings.ENVIRONMENT == "development"

    def test_moderation_policy_defaults(self):
        assert settings.STRIKES_BEFORE_SUSPENSION == 3
        assert settings.STRIKE_EXPIRES_DAYS == 90
        assert settings.OUTBOX_BATCH_SIZE == 100
        assert settings.OUTBOX_POLL_INTERVAL_SECONDS == 5
        assert settings.EVENT_PUBLISHER == "memory"

    def test_no_lru_cache_on_the_settings_factory(self):
        # Unlike admin-service, this module builds Settings() once at import;
        # a second construction is an independent object.
        assert Settings(ENVIRONMENT="test") is not settings


class TestUpstreamComplianceGuard:
    def test_the_local_validator_is_the_only_enforcement_layer(self):
        from wildframe_compliance.settings import ComplianceSettingsMixin

        own = {
            name
            for name in vars(ComplianceSettingsMixin)
            if not name.startswith("__") and not name.startswith("_")
        }
        assert "default_secrets" not in own
        assert not [n for n in own if n.startswith("validate")]
