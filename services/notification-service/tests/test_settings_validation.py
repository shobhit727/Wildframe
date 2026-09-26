"""Behavioural tests for `app.core.settings` (notification-service).

Two validators guard production boot:

* `validate_production_secrets` - no missing / known-default / weak secrets
  outside the dev environments (every deny branch asserted below).
* `validate_cors_credentials` - a wildcard origin list must never be combined
  with credentialed CORS in production (the classic reflected-origin hole).

Settings are built with `Settings(**kwargs)`; `os.environ` is never mutated, so
nothing here leaks into other tests.
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

VALID_PROD = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": "postgresql+asyncpg://appuser:s3cr3t@db.internal:5432/notification_db",
    "REDIS_URL": "redis://cache.internal:6379/0",
    "JWT_SECRET_KEY": "k" * 48,
}

# A production config that also satisfies the CORS guard.
VALID_PROD_CORS = {**VALID_PROD, "CORS_ALLOWED_ORIGINS": ["https://wildframe.com"]}


def _prod(**overrides) -> dict:
    return {**VALID_PROD_CORS, **overrides}


def _message(excinfo: ValidationError) -> str:
    return str(excinfo.value)


# ---------------------------------------------------------------------------
# Accept paths
# ---------------------------------------------------------------------------


def test_production_with_strong_secrets_is_accepted():
    settings = Settings(**VALID_PROD_CORS)

    assert settings.ENVIRONMENT == "production"
    assert settings.DATABASE_URL == VALID_PROD["DATABASE_URL"]
    assert settings.JWT_SECRET_KEY == VALID_PROD["JWT_SECRET_KEY"]


@pytest.mark.parametrize("environment", sorted(DEV_ENVIRONMENTS))
def test_dev_environments_get_defaults_injected(environment: str):
    settings = Settings(ENVIRONMENT=environment)

    assert settings.ENVIRONMENT == environment
    for key, value in DEV_DEFAULTS.items():
        assert getattr(settings, key) == value


def test_dev_defaults_do_not_override_explicit_values():
    settings = Settings(
        ENVIRONMENT="development", JWT_SECRET_KEY="explicit-secret-key-long-enough"
    )

    assert settings.JWT_SECRET_KEY == "explicit-secret-key-long-enough"


def test_explicit_none_is_not_overwritten_by_the_dev_default():
    settings = Settings(ENVIRONMENT="development", DATABASE_URL=None)

    assert settings.DATABASE_URL is None


# ---------------------------------------------------------------------------
# validate_production_secrets - deny paths
# ---------------------------------------------------------------------------


def test_production_requires_database_url():
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_prod(DATABASE_URL=None))

    assert "DATABASE_URL must be set explicitly" in _message(excinfo)


@pytest.mark.parametrize("credential", KNOWN_INSECURE_DB_CREDENTIALS)
def test_production_rejects_known_default_db_credentials(credential: str):
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_prod(DATABASE_URL=f"postgresql+asyncpg://{credential}@db:5432/ndb"))

    assert "must not use known default credentials" in _message(excinfo)


def test_production_requires_redis_url():
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_prod(REDIS_URL=None))

    assert "REDIS_URL must be set explicitly" in _message(excinfo)


def test_production_requires_jwt_secret_key():
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_prod(JWT_SECRET_KEY=None))

    assert "JWT_SECRET_KEY must be set to a strong random value" in _message(excinfo)


@pytest.mark.parametrize("secret", KNOWN_INSECURE_JWT_SECRETS)
def test_production_rejects_known_default_jwt_secrets(secret: str):
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_prod(JWT_SECRET_KEY=secret))

    assert "JWT_SECRET_KEY must be set to a strong random value" in _message(excinfo)


def test_production_requires_a_jwt_secret_of_at_least_32_chars():
    short = "a-perfectly-unique-secret-of-31"  # 31 chars
    assert len(short) == 31

    with pytest.raises(ValidationError) as excinfo:
        Settings(**_prod(JWT_SECRET_KEY=short))

    assert "at least 32 characters" in _message(excinfo)


def test_production_accepts_exactly_32_chars():
    boundary = "z" * 32

    assert Settings(**_prod(JWT_SECRET_KEY=boundary)).JWT_SECRET_KEY == boundary


def test_the_guard_applies_to_staging_too():
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_prod(ENVIRONMENT="staging", REDIS_URL=None))

    assert "REDIS_URL must be set explicitly" in _message(excinfo)


# ---------------------------------------------------------------------------
# validate_cors_credentials
# ---------------------------------------------------------------------------


def test_production_rejects_wildcard_origins_with_credentials():
    """`Access-Control-Allow-Origin: *` + cookies would reflect any origin."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            **VALID_PROD,
            CORS_ALLOWED_ORIGINS=["*"],
            CORS_ALLOW_CREDENTIALS=True,
        )

    assert "CORS_ALLOWED_ORIGINS cannot be ['*']" in _message(excinfo)


def test_production_allows_wildcard_origins_without_credentials():
    settings = Settings(
        **VALID_PROD, CORS_ALLOWED_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=False
    )

    assert settings.CORS_ALLOWED_ORIGINS == ["*"]
    assert settings.CORS_ALLOW_CREDENTIALS is False


def test_production_allows_an_explicit_origin_list_with_credentials():
    settings = Settings(
        **VALID_PROD,
        CORS_ALLOWED_ORIGINS=["https://wildframe.com"],
        CORS_ALLOW_CREDENTIALS=True,
    )

    assert settings.CORS_ALLOWED_ORIGINS == ["https://wildframe.com"]


def test_the_cors_guard_does_not_fire_outside_production():
    """Dev keeps the permissive combination it has always shipped."""
    settings = Settings(
        ENVIRONMENT="development",
        CORS_ALLOWED_ORIGINS=["*"],
        CORS_ALLOW_CREDENTIALS=True,
    )

    assert settings.CORS_ALLOWED_ORIGINS == ["*"]


# ---------------------------------------------------------------------------
# Delivery / SMTP defaults the channel layer depends on
# ---------------------------------------------------------------------------


def test_smtp_defaults_are_unconfigured_but_complete():
    settings = Settings(ENVIRONMENT="development")

    # Unconfigured host -> EmailChannel raises ChannelUnavailable by design.
    assert settings.SMTP_HOST == ""
    assert settings.SMTP_PORT == 587
    assert settings.SMTP_STARTTLS is True
    assert settings.SMTP_TIMEOUT == 10
    assert settings.SMTP_FROM == "no-reply@wildframe.local"


def test_delivery_retry_defaults_are_three_attempts_from_100ms():
    settings = Settings(ENVIRONMENT="development")

    assert settings.DELIVERY_RETRY_ATTEMPTS == 3
    assert settings.DELIVERY_RETRY_BASE_DELAY == 0.1


def test_email_daily_quota_covers_the_smtp_provider():
    settings = Settings(ENVIRONMENT="development")

    assert settings.EMAIL_DAILY_QUOTA["smtp"] == 10000


def test_metrics_token_is_empty_by_default_so_the_gate_fails_closed():
    settings = Settings(ENVIRONMENT="development")

    assert settings.METRICS_TOKEN == ""
