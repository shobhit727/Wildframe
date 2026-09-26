"""Behavioural tests for `app.core.settings.validate_production_secrets`.

The validator is a production guardrail: outside the dev environments it must
refuse to boot with missing/known-bad/weak secrets. Each deny branch is
asserted here, plus the one accept branch, so a future refactor cannot silently
drop a guard.

Settings are constructed directly (`Settings(**kwargs)`) rather than by mutating
`os.environ`, so nothing in this module leaks into other tests.
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

# A fully valid production config; individual tests delete/poison one field.
VALID_PROD = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": "postgresql+asyncpg://appuser:s3cr3t@db.internal:5432/users_db",
    "REDIS_URL": "redis://cache.internal:6379/0",
    "JWT_SECRET_KEY": "k" * 48,
    "KAFKA_BOOTSTRAP_SERVERS": "kafka.internal:9092",
}


def _prod(**overrides) -> dict:
    return {**VALID_PROD, **overrides}


def _message(excinfo: ValidationError) -> str:
    return str(excinfo.value)


# ---------------------------------------------------------------------------
# Accept paths
# ---------------------------------------------------------------------------


def test_production_with_strong_secrets_is_accepted():
    settings = Settings(**VALID_PROD)

    assert settings.ENVIRONMENT == "production"
    assert settings.DATABASE_URL == VALID_PROD["DATABASE_URL"]
    assert settings.JWT_SECRET_KEY == VALID_PROD["JWT_SECRET_KEY"]


@pytest.mark.parametrize("environment", sorted(DEV_ENVIRONMENTS))
def test_dev_environments_get_defaults_injected(environment: str):
    """`""`, `development` and `test` all skip the production guard AND backfill."""
    settings = Settings(ENVIRONMENT=environment)

    assert settings.ENVIRONMENT == environment
    for key, value in DEV_DEFAULTS.items():
        assert getattr(settings, key) == value


def test_dev_defaults_do_not_override_explicit_values():
    settings = Settings(
        ENVIRONMENT="development",
        JWT_SECRET_KEY="explicit-secret-key-that-is-long-enough",
    )

    assert settings.JWT_SECRET_KEY == "explicit-secret-key-that-is-long-enough"


def test_explicit_none_is_not_overwritten_by_dev_default():
    """`values.setdefault` only fills *missing* keys - an explicit None survives.

    The dev branch therefore lets a half-configured service boot, which is the
    documented dev behaviour (and why the guard is keyed on ENVIRONMENT).
    """
    settings = Settings(ENVIRONMENT="development", DATABASE_URL=None)

    assert settings.DATABASE_URL is None
    assert settings.JWT_SECRET_KEY == DEV_DEFAULTS["JWT_SECRET_KEY"]


# ---------------------------------------------------------------------------
# Deny paths - production only
# ---------------------------------------------------------------------------


def test_production_requires_database_url():
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_prod(DATABASE_URL=None))

    assert "DATABASE_URL must be set explicitly" in _message(excinfo)


@pytest.mark.parametrize("credential", KNOWN_INSECURE_DB_CREDENTIALS)
def test_production_rejects_known_default_db_credentials(credential: str):
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_prod(DATABASE_URL=f"postgresql+asyncpg://{credential}@db:5432/users_db"))

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


def test_production_requires_jwt_secret_of_at_least_32_chars():
    short_secret = "a-perfectly-unique-secret-of-31"  # 31 chars
    assert len(short_secret) == 31

    with pytest.raises(ValidationError) as excinfo:
        Settings(**_prod(JWT_SECRET_KEY=short_secret))

    assert "at least 32 characters" in _message(excinfo)


def test_production_accepts_exactly_32_chars():
    boundary = "z" * 32

    settings = Settings(**_prod(JWT_SECRET_KEY=boundary))

    assert settings.JWT_SECRET_KEY == boundary


def test_production_requires_kafka_bootstrap_servers():
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_prod(KAFKA_BOOTSTRAP_SERVERS=None))

    assert "KAFKA_BOOTSTRAP_SERVERS must be set explicitly" in _message(excinfo)


def test_guard_fires_for_staging_too_not_only_production():
    """Any non-dev environment is treated as production-like."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(**_prod(ENVIRONMENT="staging", KAFKA_BOOTSTRAP_SERVERS=None))

    assert "KAFKA_BOOTSTRAP_SERVERS must be set explicitly" in _message(excinfo)


def test_validation_order_is_db_before_redis():
    """Both missing -> the DB error is the one reported (first check wins)."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="k" * 48,
            KAFKA_BOOTSTRAP_SERVERS="kafka:9092",
        )

    assert "DATABASE_URL must be set explicitly" in _message(excinfo)
