"""Settings validation for search-service.

Every case is built by constructing ``Settings(**kwargs)`` directly — the
process environment is never mutated, so these assertions are independent of
whatever ``.env``/env the suite happens to run with.
"""

import pytest
from pydantic import ValidationError

from app.core.settings import (
    DEV_DEFAULTS,
    DEV_ENVIRONMENTS,
    KNOWN_INSECURE_CURSOR_SECRETS,
    KNOWN_INSECURE_DB_CREDENTIALS,
    KNOWN_INSECURE_JWT_SECRETS,
    Settings,
)

# A credential set that passes every production gate, so each negative case can
# remove exactly one requirement.
STRONG_DB_URL = "postgresql+asyncpg://app_user:s3cret-passphrase@db.internal:5432/search_db"
STRONG_JWT_SECRET = "k7Qm2Xz9Tb4LpR8vNc6Wy1Ae5Hd0Jf3Ug5SiOq7X"  # 40 chars, not a known default
STRONG_CURSOR_SECRET = "R4t2Wq8Lm3Zx6Nv1By7Kd0Ce5Sh9Jg3Uf1Ap4To6I"  # 40 chars, not a known default


def _clear_dev_env(monkeypatch) -> None:
    """Remove the env overrides the suite sets, so DEV_DEFAULTS are visible."""
    for key in (*DEV_DEFAULTS, "ENVIRONMENT"):
        monkeypatch.delenv(key, raising=False)


def production(**overrides) -> dict:
    base = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": STRONG_DB_URL,
        "REDIS_URL": "redis://redis.internal:6379/0",
        "JWT_SECRET_KEY": STRONG_JWT_SECRET,
        "SEARCH_CURSOR_SECRET": STRONG_CURSOR_SECRET,
        "KAFKA_BOOTSTRAP_SERVERS": "kafka:29092",
    }
    base.update(overrides)
    return base


class TestProductionSecretsAccepted:
    def test_fully_specified_production_settings_validate(self):
        settings = Settings(**production())

        assert settings.ENVIRONMENT == "production"
        assert settings.DATABASE_URL == STRONG_DB_URL

    @pytest.mark.parametrize("environment", ["staging", "development-like-prod", "prod"])
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
        url = f"postgresql+asyncpg://{credential}@db.internal:5432/search_db"

        with pytest.raises(ValidationError, match="must not use known default credentials"):
            Settings(**production(DATABASE_URL=url))

    def test_unfamiliar_credentials_are_accepted(self):
        url = "postgresql+asyncpg://wildframe_owner:hunter2-correct-horse@db:5432/search_db"

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


class TestCursorSecretGate:
    """The cursor key is gated exactly as JWT_SECRET_KEY is, and independently.

    Sharing a key coupled two unrelated lifecycles, so the cursor key gets its
    own gate rather than riding on the token secret's.
    """

    def test_missing_cursor_secret_is_rejected(self):
        with pytest.raises(ValidationError, match="SEARCH_CURSOR_SECRET must be set"):
            Settings(**production(SEARCH_CURSOR_SECRET=None))

    @pytest.mark.parametrize("secret", KNOWN_INSECURE_CURSOR_SECRETS)
    def test_known_default_cursor_secrets_are_rejected(self, secret):
        with pytest.raises(ValidationError, match="strong random value"):
            Settings(**production(SEARCH_CURSOR_SECRET=secret))

    def test_the_dev_default_is_one_of_the_known_insecure_values(self):
        """The dev default must be rejected in production, or the gate is decorative."""
        assert DEV_DEFAULTS["SEARCH_CURSOR_SECRET"] in KNOWN_INSECURE_CURSOR_SECRETS

    def test_short_cursor_secret_is_rejected(self):
        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings(**production(SEARCH_CURSOR_SECRET="a" * 31))

    def test_exactly_32_characters_is_accepted(self):
        secret = "c" * 32

        assert Settings(**production(SEARCH_CURSOR_SECRET=secret)).SEARCH_CURSOR_SECRET == secret

    def test_the_cursor_secret_is_independent_of_the_jwt_secret(self):
        """A strong cursor key with a strong token key needs both, separately."""
        settings = Settings(**production())

        assert settings.SEARCH_CURSOR_SECRET == STRONG_CURSOR_SECRET
        assert settings.SEARCH_CURSOR_SECRET != settings.JWT_SECRET_KEY

    def test_a_dev_default_cursor_secret_is_rejected_even_with_a_strong_jwt_key(self):
        """A weak cursor key is not excused by a strong token key."""
        with pytest.raises(ValidationError, match="strong random value"):
            Settings(**production(SEARCH_CURSOR_SECRET=DEV_DEFAULTS["SEARCH_CURSOR_SECRET"]))

    def test_a_strong_jwt_key_does_not_satisfy_a_missing_cursor_key(self):
        with pytest.raises(ValidationError, match="SEARCH_CURSOR_SECRET must be set"):
            Settings(**production(SEARCH_CURSOR_SECRET=None))


class TestKafkaGate:
    def test_missing_kafka_bootstrap_is_rejected(self):
        with pytest.raises(ValidationError, match="KAFKA_BOOTSTRAP_SERVERS must be set"):
            Settings(**production(KAFKA_BOOTSTRAP_SERVERS=None))

    def test_database_url_is_validated_before_redis(self):
        """Gate ordering is observable: the first failing rule wins the message."""
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
        """Env-provided values are already in ``values`` when the hook runs."""
        _clear_dev_env(monkeypatch)
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://env:pw@envhost:5432/env_db")

        settings = Settings(ENVIRONMENT="test")

        assert settings.DATABASE_URL == "postgresql+asyncpg://env:pw@envhost:5432/env_db"
        # The other defaults are still applied.
        assert settings.REDIS_URL == DEV_DEFAULTS["REDIS_URL"]

    def test_explicit_values_win_over_the_defaults(self, monkeypatch):
        _clear_dev_env(monkeypatch)
        settings = Settings(
            ENVIRONMENT="test",
            DATABASE_URL="postgresql+asyncpg://real:pw@realhost:5432/other",
        )

        assert settings.DATABASE_URL == "postgresql+asyncpg://real:pw@realhost:5432/other"

    def test_unset_environment_treated_as_development(self, monkeypatch):
        _clear_dev_env(monkeypatch)
        monkeypatch.delenv("ENVIRONMENT", raising=False)

        settings = Settings()

        assert settings.ENVIRONMENT == "development"
        assert settings.DATABASE_URL == DEV_DEFAULTS["DATABASE_URL"]

    def test_defaults_are_not_applied_outside_dev_environments(self, monkeypatch):
        _clear_dev_env(monkeypatch)

        with pytest.raises(ValidationError, match="DATABASE_URL must be set explicitly"):
            Settings(ENVIRONMENT="production")

    def test_weak_secrets_are_tolerated_in_development(self, monkeypatch):
        _clear_dev_env(monkeypatch)
        settings = Settings(ENVIRONMENT="development", JWT_SECRET_KEY="secret")

        assert settings.JWT_SECRET_KEY == "secret"

    def test_the_cursor_dev_default_is_usable_but_distinct(self, monkeypatch):
        """Documented dev posture: a real-looking length, a different value.

        The length has to clear the production gate so a dev default copied
        into an environment file is not a tripwire, and it has to differ from the
        token secret so the two keys stay uncoupled even by accident.
        """
        _clear_dev_env(monkeypatch)
        settings = Settings(ENVIRONMENT="development")

        assert len(DEV_DEFAULTS["SEARCH_CURSOR_SECRET"]) >= 32
        assert DEV_DEFAULTS["SEARCH_CURSOR_SECRET"] != DEV_DEFAULTS["JWT_SECRET_KEY"]
        assert settings.SEARCH_CURSOR_SECRET == DEV_DEFAULTS["SEARCH_CURSOR_SECRET"]

    def test_the_cursor_dev_default_never_reaches_a_production_instance(self, monkeypatch):
        _clear_dev_env(monkeypatch)

        with pytest.raises(ValidationError, match="strong random value"):
            Settings(**production(SEARCH_CURSOR_SECRET=DEV_DEFAULTS["SEARCH_CURSOR_SECRET"]))


class TestCorsCredentialsGate:
    def test_wildcard_origins_with_credentials_are_rejected_in_production(self):
        with pytest.raises(ValidationError, match=r"\['\*'\] with CORS_ALLOW_CREDENTIALS=True"):
            Settings(**production(CORS_ALLOWED_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=True))

    def test_wildcard_origins_are_fine_without_credentials(self):
        settings = Settings(**production(CORS_ALLOWED_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=False))

        assert settings.CORS_ALLOWED_ORIGINS == ["*"]

    def test_explicit_origins_with_credentials_are_allowed(self):
        origins = ["https://app.wildframe.com"]

        settings = Settings(**production(CORS_ALLOWED_ORIGINS=origins))

        assert settings.CORS_ALLOWED_ORIGINS == origins

    def test_wildcard_with_credentials_is_allowed_outside_production(self):
        settings = Settings(
            ENVIRONMENT="staging",
            DATABASE_URL=STRONG_DB_URL,
            REDIS_URL="redis://redis:6379/0",
            JWT_SECRET_KEY=STRONG_JWT_SECRET,
            SEARCH_CURSOR_SECRET=STRONG_CURSOR_SECRET,
            KAFKA_BOOTSTRAP_SERVERS="kafka:29092",
            CORS_ALLOWED_ORIGINS=["*"],
            CORS_ALLOW_CREDENTIALS=True,
        )

        assert settings.CORS_ALLOWED_ORIGINS == ["*"]


class TestDefaults:
    def test_service_identity_and_version(self):
        settings = Settings()

        assert settings.SERVICE_NAME == "Search"
        assert settings.SERVICE_VERSION == "1.0.0"

    def test_jwt_contract(self):
        settings = Settings()

        assert settings.JWT_ALGORITHM == "HS256"
        assert settings.JWT_AUDIENCE == "wildframe-api"
        assert settings.JWT_ISSUER == "wildframe-auth"

    def test_backends_and_transport(self):
        settings = Settings()

        assert settings.EVENT_PUBLISHER == "memory"
        assert settings.ELASTICSEARCH_URL == "http://elasticsearch:9200"
        assert settings.CONTENT_SERVICE_URL == "http://content-service:8003"
        assert settings.SERVER_PORT == 8005

    def test_compliance_defaults(self):
        settings = Settings()

        assert settings.compliance_jurisdiction.value == "GLOBAL"
        assert [j.value for j in settings.compliance_additional_jurisdictions] == [
            "EU",
            "US",
            "IN",
        ]
        assert settings.compliance_dpo_email == "dpo@wildframe.com"
        assert settings.compliance_grievance_officer_email == "grievance@wildframe.com"
        assert settings.compliance_allowed_data_regions == ["US", "EU", "IN", "SG"]
