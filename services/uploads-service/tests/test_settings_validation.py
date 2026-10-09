"""Production secret + CORS validation for the uploads-service settings.

``Settings`` is the boot gate: a staging/production process that boots with a
dev database credential, a shared JWT secret, a wildcard CORS origin plus
credentials, or no metrics token at all would be silently insecure. Each
``raise`` is pinned here with the smallest set of overrides that reaches it,
plus the positive cases that must be accepted.
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
    "DATABASE_URL": "postgresql+asyncpg://wf:s3cret-from-the-vault@db.example.com:5432/uploads_db",
    "REDIS_URL": "redis://redis.example.com:6379",
    "JWT_SECRET_KEY": "a-very-strong-jwt-secret-key-32+chars-long!",
    "KAFKA_BOOTSTRAP_SERVERS": "kafka.example.com:9092",
    "METRICS_TOKEN": "s3cret-metrics-token",
}


def _with(**overrides) -> dict:
    merged = dict(STRONG)
    merged.update(overrides)
    return merged


# ---------------------------------------------------------------------------
# Dev environments short-circuit the whole gate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["", "development", "test"])
def test_dev_environments_skip_the_production_gate(environment):
    s = Settings(ENVIRONMENT=environment)
    assert s.ENVIRONMENT in {"", "development", "test"}


def test_dev_defaults_are_filled_in_for_development():
    s = Settings(ENVIRONMENT="development")
    for key, value in DEV_DEFAULTS.items():
        assert getattr(s, key) == value


def test_dev_defaults_do_not_override_explicit_values():
    s = Settings(ENVIRONMENT="test", DATABASE_URL="postgresql+asyncpg://x:y@host/db")
    assert s.DATABASE_URL == "postgresql+asyncpg://x:y@host/db"
    assert s.REDIS_URL == DEV_DEFAULTS["REDIS_URL"]


def test_the_service_defaults_match_the_documented_uploads_contract():
    s = Settings(ENVIRONMENT="development")
    assert s.SERVICE_NAME == "uploads-service"
    assert s.SERVER_PORT == 8014
    assert s.STORAGE_BACKEND == "stub"
    assert s.EVENT_PUBLISHER == "memory"
    assert s.JWT_AUDIENCE == "wildframe-api"
    assert s.JWT_ISSUER == "wildframe-auth"
    assert s.DEFAULT_CHUNK_SIZE_BYTES == 5 * 1024 * 1024
    assert "video/mp4" in s.ALLOWED_UPLOAD_MIME_TYPES
    assert "application/octet-stream" not in s.ALLOWED_UPLOAD_MIME_TYPES


# ---------------------------------------------------------------------------
# The happy path.
# ---------------------------------------------------------------------------


def test_production_accepts_strong_secrets():
    s = Settings(**_with())
    assert s.ENVIRONMENT == "production"
    assert s.METRICS_TOKEN == "s3cret-metrics-token"


# ---------------------------------------------------------------------------
# Each rejection, in the order the validator applies them.
# ---------------------------------------------------------------------------


def test_production_requires_an_explicit_database_url():
    with pytest.raises(ValidationError) as exc:
        Settings(**_with(DATABASE_URL=None))
    assert "DATABASE_URL must be set explicitly" in str(exc.value)


@pytest.mark.parametrize("credential", KNOWN_INSECURE_DB_CREDENTIALS)
def test_production_rejects_known_default_database_credentials(credential):
    url = f"postgresql+asyncpg://{credential}@db.example.com:5432/uploads_db"
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


def test_staging_is_also_gated_like_production():
    """Any non-dev environment gets the full secret gate."""
    with pytest.raises(ValidationError) as exc:
        Settings(**_with(ENVIRONMENT="staging", REDIS_URL=None))
    assert "REDIS_URL must be set explicitly" in str(exc.value)


def test_a_staging_environment_with_good_secrets_is_accepted():
    s = Settings(**_with(ENVIRONMENT="staging"))
    assert s.ENVIRONMENT == "staging"


# ---------------------------------------------------------------------------
# CORS: wildcard origins must never be combined with credentials (#68).
# ---------------------------------------------------------------------------


def test_production_rejects_wildcard_cors_with_credentials():
    with pytest.raises(ValidationError) as exc:
        Settings(**_with(CORS_ALLOWED_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=True))
    assert "CORS_ALLOWED_ORIGINS cannot be" in str(exc.value)


def test_production_allows_a_wildcard_cors_without_credentials():
    s = Settings(**_with(CORS_ALLOWED_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=False))
    assert s.CORS_ALLOWED_ORIGINS == ["*"]
    assert s.CORS_ALLOW_CREDENTIALS is False


def test_production_allows_explicit_origins_with_credentials():
    s = Settings(
        **_with(CORS_ALLOWED_ORIGINS=["https://studio.example.com"], CORS_ALLOW_CREDENTIALS=True)
    )
    assert s.CORS_ALLOWED_ORIGINS == ["https://studio.example.com"]


def test_development_allows_a_wildcard_cors_with_credentials():
    """The ban is production-only; dev keeps the permissive default."""
    s = Settings(ENVIRONMENT="development", CORS_ALLOWED_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=True)
    assert s.CORS_ALLOWED_ORIGINS == ["*"]


def test_a_wildcard_among_several_origins_is_not_the_wildcard_case():
    s = Settings(**_with(CORS_ALLOWED_ORIGINS=["*", "https://a.example.com"]))
    assert s.CORS_ALLOWED_ORIGINS == ["*", "https://a.example.com"]


# ---------------------------------------------------------------------------
# The storage knobs the S3 adapter reads.
# ---------------------------------------------------------------------------


def test_the_s3_knobs_line_up_with_the_presign_and_checksum_ceilings():
    s = Settings(ENVIRONMENT="development")
    assert s.PRESIGNED_URL_MAX_TTL_SECONDS == 3600
    assert s.CHECKSUM_VERIFY_MAX_BYTES == 512 * 1024 * 1024
    assert s.S3_PRESIGNED_URL_TTL_SECONDS <= s.PRESIGNED_URL_MAX_TTL_SECONDS
    assert s.MAX_UPLOAD_SIZE_BYTES == 10 * 1024 * 1024 * 1024
    assert s.MAX_CHUNKS_PER_SESSION == 10_000
    assert s.SESSION_EXPIRES_HOURS == 24
