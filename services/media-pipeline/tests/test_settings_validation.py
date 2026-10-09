"""Production secret validation for the media pipeline settings.

``Settings.validate_production_secrets`` is the boot gate: a staging/production
process that boots with a default dev database URL, a shared JWT secret or a
stub adapter set would be silently insecure. Each ``raise`` is pinned here with
the smallest set of overrides that reaches it, plus the positive cases that
must be accepted.
"""

import pytest
from pydantic import ValidationError

from app.core.settings import (
    DEV_DEFAULTS,
    KNOWN_INSECURE_DB_CREDENTIALS,
    KNOWN_INSECURE_JWT_SECRETS,
    Settings,
)

STRONG = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": "postgresql+asyncpg://wf:s3cret-from-the-vault@db.example.com:5432/media_db",
    "REDIS_URL": "redis://redis.example.com:6379",
    "JWT_SECRET_KEY": "a-very-strong-jwt-secret-key-32+chars-long!",
    "KAFKA_BOOTSTRAP_SERVERS": "kafka.example.com:9092",
    "MEDIA_PIPELINE_ADAPTERS": "ffmpeg",
}


def _with(**overrides) -> dict:
    merged = dict(STRONG)
    merged.update(overrides)
    return merged


# ---------------------------------------------------------------------------
# Dev environments short-circuit the whole gate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["", "development", "test"])
def test_dev_environments_skip_production_gate(environment):
    s = Settings(ENVIRONMENT=environment, MEDIA_PIPELINE_ADAPTERS="stub")
    assert s.ENVIRONMENT in {"", "development", "test"}


def test_dev_defaults_are_filled_in_for_development():
    s = Settings(ENVIRONMENT="development")
    for key, value in DEV_DEFAULTS.items():
        assert getattr(s, key) == value


def test_dev_defaults_do_not_override_explicit_values():
    s = Settings(ENVIRONMENT="test", DATABASE_URL="postgresql+asyncpg://x:y@host/db")
    assert s.DATABASE_URL == "postgresql+asyncpg://x:y@host/db"
    assert s.REDIS_URL == DEV_DEFAULTS["REDIS_URL"]


# ---------------------------------------------------------------------------
# The happy path.
# ---------------------------------------------------------------------------


def test_production_accepts_strong_secrets_and_a_real_adapter():
    s = Settings(**_with())
    assert s.ENVIRONMENT == "production"
    assert s.MEDIA_PIPELINE_ADAPTERS == "ffmpeg"


# ---------------------------------------------------------------------------
# Each rejection, in the order the validator applies them.
# ---------------------------------------------------------------------------


def test_production_requires_an_explicit_database_url():
    with pytest.raises(ValidationError) as exc:
        Settings(**_with(DATABASE_URL=None))
    assert "DATABASE_URL must be set explicitly" in str(exc.value)


@pytest.mark.parametrize("credential", KNOWN_INSECURE_DB_CREDENTIALS)
def test_production_rejects_known_default_database_credentials(credential):
    url = f"postgresql+asyncpg://{credential}@db.example.com:5432/media_db"
    with pytest.raises(ValidationError) as exc:
        Settings(**_with(DATABASE_URL=url))
    assert "must not use known default credentials" in str(exc.value)


def test_production_requires_an_explicit_redis_url():
    with pytest.raises(ValidationError) as exc:
        Settings(**_with(REDIS_URL=None))
    assert "REDIS_URL must be set explicitly" in str(exc.value)


def test_production_requires_an_explicit_jwt_secret():
    with pytest.raises(ValidationError) as exc:
        Settings(**_with(JWT_SECRET_KEY=None))
    assert "JWT_SECRET_KEY must be set to a strong random value" in str(exc.value)


@pytest.mark.parametrize("secret", KNOWN_INSECURE_JWT_SECRETS)
def test_production_rejects_known_default_jwt_secrets(secret):
    with pytest.raises(ValidationError) as exc:
        Settings(**_with(JWT_SECRET_KEY=secret))
    assert "must be set to a strong random value" in str(exc.value)


def test_production_rejects_a_jwt_secret_shorter_than_32_chars():
    with pytest.raises(ValidationError) as exc:
        Settings(**_with(JWT_SECRET_KEY="short-but-not-in-the-known-list"))
    assert "at least 32 characters long" in str(exc.value)


def test_production_accepts_a_32_char_jwt_secret_exactly():
    s = Settings(**_with(JWT_SECRET_KEY="x" * 32))
    assert s.JWT_SECRET_KEY == "x" * 32


def test_production_requires_explicit_kafka_bootstrap_servers():
    with pytest.raises(ValidationError) as exc:
        Settings(**_with(KAFKA_BOOTSTRAP_SERVERS=None))
    assert "KAFKA_BOOTSTRAP_SERVERS must be set explicitly" in str(exc.value)


@pytest.mark.parametrize("adapters", ["", "   ", "stub"])
def test_production_refuses_stub_adapters(adapters):
    with pytest.raises(ValidationError) as exc:
        Settings(**_with(MEDIA_PIPELINE_ADAPTERS=adapters))
    assert "MEDIA_PIPELINE_ADAPTERS must be set to a production adapter" in str(exc.value)


def test_production_also_rejects_a_non_string_adapter_selection():
    """A ``None`` adapter is caught by pydantic's type check, not the gate."""
    with pytest.raises(ValidationError) as exc:
        Settings(**_with(MEDIA_PIPELINE_ADAPTERS=None))
    assert "MEDIA_PIPELINE_ADAPTERS" in str(exc.value)


def test_staging_is_also_gated_like_production():
    """Any non-dev environment gets the full secret gate."""
    with pytest.raises(ValidationError) as exc:
        Settings(**_with(ENVIRONMENT="staging", MEDIA_PIPELINE_ADAPTERS="stub"))
    assert "MEDIA_PIPELINE_ADAPTERS" in str(exc.value)


# ---------------------------------------------------------------------------
# The pipeline resource ceilings the adapters read.
# ---------------------------------------------------------------------------


def test_pipeline_ceilings_match_the_security_module_constants():
    from app.core.security import (
        MAX_BITRATE_KBPS,
        MAX_DIMENSION_PIXELS,
        MAX_DURATION_SECONDS,
    )

    s = Settings(**_with())
    assert s.PIPELINE_MAX_DURATION_SECONDS == MAX_DURATION_SECONDS
    assert s.PIPELINE_MAX_CPU_THREADS >= 1
    assert s.PIPELINE_MAX_MEMORY_BYTES > 0
    # Sanity: the documented ceilings are what the code enforces.
    assert (MAX_DURATION_SECONDS, MAX_DIMENSION_PIXELS, MAX_BITRATE_KBPS) == (
        14400.0,
        8192,
        100_000,
    )
