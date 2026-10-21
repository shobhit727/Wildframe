"""Tests for ``analytics-service/app/core/settings.py``.

Two independent guards live here:

* ``validate_production_secrets`` -- fail-fast outside a development
  environment on missing / known-insecure / too-short secrets.
* ``validate_cors_credentials`` -- #68: production must not combine a wildcard
  origin list with credentialed CORS.

Each case constructs ``Settings(**kwargs)`` with ``_env_file=None``; no dotenv
file is read and no process environment is mutated.
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

GOOD_DB = "postgresql+asyncpg://app_user:s3cr3t-rotated@db.internal:5432/analytics_db"
GOOD_REDIS = "redis://cache.internal:6379"
GOOD_JWT = "K7bQx2Zf9pLw4mNc8vRt3yHs6dJg1aEe5uIoP0zXcVb"  # 44 chars


def prod_settings(**overrides) -> dict:
    kwargs = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": GOOD_DB,
        "REDIS_URL": GOOD_REDIS,
        "JWT_SECRET_KEY": GOOD_JWT,
    }
    kwargs.update(overrides)
    return kwargs


# ------------------------------------------------- development defaults ----


@pytest.mark.unit
def test_development_environments_short_circuit_the_guard():
    """Each dev environment must return early and inject DEV_DEFAULTS."""
    for env in sorted(DEV_ENVIRONMENTS):
        kwargs = {"ENVIRONMENT": env} if env else {}
        s = Settings(_env_file=None, **kwargs)
        assert s.DATABASE_URL == DEV_DEFAULTS["DATABASE_URL"]
        assert s.REDIS_URL == DEV_DEFAULTS["REDIS_URL"]
        assert s.JWT_SECRET_KEY == DEV_DEFAULTS["JWT_SECRET_KEY"]


@pytest.mark.unit
def test_explicit_values_are_not_overwritten_by_dev_defaults():
    """``setdefault`` semantics: a caller-supplied value always wins."""
    s = Settings(_env_file=None, ENVIRONMENT="development", DATABASE_URL=GOOD_DB)
    assert s.DATABASE_URL == GOOD_DB
    assert s.REDIS_URL == DEV_DEFAULTS["REDIS_URL"]


@pytest.mark.unit
def test_staging_is_not_a_development_environment():
    """Only dev/test/empty bypass the guard -- 'staging' must be hardened."""
    with pytest.raises(ValidationError, match="DATABASE_URL must be set explicitly"):
        Settings(_env_file=None, ENVIRONMENT="staging")


# ------------------------------------------------- happy path in production --


@pytest.mark.unit
def test_valid_production_settings_are_accepted():
    """A fully rotated production config must construct without error."""
    s = Settings(_env_file=None, **prod_settings())
    assert s.ENVIRONMENT == "production"
    assert s.DATABASE_URL == GOOD_DB
    assert s.JWT_SECRET_KEY == GOOD_JWT


# --------------------------------------------------------- DATABASE_URL ----


@pytest.mark.unit
def test_production_requires_explicit_database_url():
    """settings.py:81-84."""
    kwargs = prod_settings()
    del kwargs["DATABASE_URL"]
    with pytest.raises(ValidationError, match="DATABASE_URL must be set explicitly"):
        Settings(_env_file=None, **kwargs)


@pytest.mark.parametrize("credential", KNOWN_INSECURE_DB_CREDENTIALS)
def test_production_rejects_known_insecure_db_credentials(credential):
    """settings.py:85-86 -- each shipped default credential is refused."""
    url = f"postgresql+asyncpg://{credential}@db.internal:5432/analytics_db"
    with pytest.raises(ValidationError, match="known default credentials"):
        Settings(_env_file=None, **prod_settings(DATABASE_URL=url))


# ----------------------------------------------------------- REDIS_URL -----


@pytest.mark.unit
def test_production_requires_explicit_redis_url():
    """settings.py:87-90."""
    kwargs = prod_settings()
    del kwargs["REDIS_URL"]
    with pytest.raises(ValidationError, match="REDIS_URL must be set explicitly"):
        Settings(_env_file=None, **kwargs)


# ------------------------------------------------------ JWT_SECRET_KEY -----


@pytest.mark.unit
def test_production_requires_jwt_secret_key():
    """settings.py:91-94."""
    kwargs = prod_settings()
    del kwargs["JWT_SECRET_KEY"]
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY must be set to a strong"):
        Settings(_env_file=None, **kwargs)


@pytest.mark.parametrize("insecure", KNOWN_INSECURE_JWT_SECRETS)
def test_production_rejects_known_insecure_jwt_secrets(insecure):
    """settings.py:95-98 -- the dev default and its documented aliases."""
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY must be set to a strong"):
        Settings(_env_file=None, **prod_settings(JWT_SECRET_KEY=insecure))


@pytest.mark.unit
def test_production_rejects_short_jwt_secret_key():
    """settings.py:99-102 -- 31 chars is one short of the floor."""
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(_env_file=None, **prod_settings(JWT_SECRET_KEY="a" * 31))


@pytest.mark.unit
def test_production_accepts_exactly_32_character_jwt_secret_key():
    """The length check is inclusive of the documented 32-char floor."""
    s = Settings(_env_file=None, **prod_settings(JWT_SECRET_KEY="b" * 32))
    assert len(s.JWT_SECRET_KEY) == 32


@pytest.mark.unit
def test_analytics_does_not_also_reject_a_blank_jwt_secret():
    """Documents an asymmetry with the streaming service.

    ``streaming-service`` additionally rejects a whitespace-only
    ``JWT_SECRET_KEY``; this service has no ``.strip()`` check, so a
    32-space "secret" is accepted here. Both are >= 32 characters and not in
    the denylist, so it slips through. Pinned so the difference is visible
    rather than accidental -- it is a weaker guard than the sibling service.
    """
    s = Settings(_env_file=None, **prod_settings(JWT_SECRET_KEY=" " * 32))
    assert s.JWT_SECRET_KEY == " " * 32


# --------------------------------------------- validate_cors_credentials -----


@pytest.mark.unit
def test_production_rejects_wildcard_origins_with_credentials():
    """#68: ``['*']`` + credentials lets any site ride the session."""
    with pytest.raises(ValidationError, match=r"cannot be \['\*'\]"):
        Settings(
            _env_file=None,
            **prod_settings(CORS_ALLOWED_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=True),
        )


@pytest.mark.unit
def test_production_allows_wildcard_origins_without_credentials():
    """The combination is only dangerous *with* credentials."""
    s = Settings(
        _env_file=None,
        **prod_settings(CORS_ALLOWED_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=False),
    )
    assert s.CORS_ALLOWED_ORIGINS == ["*"]


@pytest.mark.unit
def test_production_allows_an_explicit_origin_list_with_credentials():
    """The recommended fix from the #68 error message must be accepted."""
    s = Settings(
        _env_file=None,
        **prod_settings(
            CORS_ALLOWED_ORIGINS=["https://app.wildframe.com"],
            CORS_ALLOW_CREDENTIALS=True,
        ),
    )
    assert s.CORS_ALLOWED_ORIGINS == ["https://app.wildframe.com"]
    assert s.CORS_ALLOW_CREDENTIALS is True


@pytest.mark.unit
def test_non_production_allows_wildcard_origins_with_credentials():
    """The guard is production-only; dev is not constrained."""
    s = Settings(
        _env_file=None,
        ENVIRONMENT="development",
        CORS_ALLOWED_ORIGINS=["*"],
        CORS_ALLOW_CREDENTIALS=True,
    )
    assert s.CORS_ALLOWED_ORIGINS == ["*"]


@pytest.mark.unit
def test_staging_is_not_constrained_by_the_cors_guard():
    """Only the exact string 'production' trips the CORS rule."""
    s = Settings(
        _env_file=None,
        ENVIRONMENT="staging",
        DATABASE_URL=GOOD_DB,
        REDIS_URL=GOOD_REDIS,
        JWT_SECRET_KEY=GOOD_JWT,
        CORS_ALLOWED_ORIGINS=["*"],
        CORS_ALLOW_CREDENTIALS=True,
    )
    assert s.CORS_ALLOWED_ORIGINS == ["*"]


@pytest.mark.unit
def test_wildcard_must_be_the_only_origin_to_trip_the_guard():
    """``['*', 'https://x']`` is a different (also invalid) list, unguarded."""
    s = Settings(
        _env_file=None,
        **prod_settings(CORS_ALLOWED_ORIGINS=["*", "https://x.example"]),
    )
    assert s.CORS_ALLOWED_ORIGINS == ["*", "https://x.example"]


# ------------------------------------------------- other declared fields ----


@pytest.mark.unit
def test_metrics_token_defaults_to_empty_string():
    """An unset METRICS_TOKEN is '' -- which makes the production gate deny all."""
    s = Settings(_env_file=None, ENVIRONMENT="development")
    assert s.METRICS_TOKEN == ""


@pytest.mark.unit
def test_privileged_role_and_content_client_settings_have_working_defaults():
    """The authorization and outbound-HTTP knobs must exist and be sane."""
    s = Settings(_env_file=None, ENVIRONMENT="development")
    assert s.PRIVILEGED_ROLE == "admin"
    assert s.CONTENT_SERVICE_URL.startswith("http")
    assert s.CONTENT_SERVICE_TIMEOUT_SECONDS > 0
    assert s.CONTENT_SERVICE_MAX_CONNECTIONS > 0
    assert s.JWT_AUDIENCE == "wildframe-api"
    assert s.JWT_ISSUER == "wildframe-auth"


@pytest.mark.unit
def test_shipped_default_jwt_secret_matches_the_known_insecure_set():
    """Ties the DEV_DEFAULTS entry to the denylist so they cannot drift."""
    assert DEV_DEFAULTS["JWT_SECRET_KEY"] in KNOWN_INSECURE_JWT_SECRETS


@pytest.mark.unit
def test_no_pragma_no_cover_in_settings_module():
    """The production guards must stay measured -- no coverage escape hatches."""
    import inspect

    import app.core.settings as settings_module

    assert "pragma: no cover" not in inspect.getsource(settings_module)
