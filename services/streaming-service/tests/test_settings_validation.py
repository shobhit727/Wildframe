"""Tests for ``streaming-service/app/core/settings.py``.

``validate_production_secrets`` is the service's fail-fast production guard:
outside a development environment it rejects missing values, known-insecure
credentials and short JWT keys -- and, since #489, the
``PLAYBACK_URL_SIGNING_SECRET`` that signs playback URLs.

Every case here constructs ``Settings(**kwargs)`` directly with
``_env_file=None`` so nothing reads a dotenv file and no process environment is
mutated.
"""

import pytest
from pydantic import ValidationError

from app.core.settings import (
    DEV_DEFAULTS,
    DEV_ENVIRONMENTS,
    KNOWN_INSECURE_DB_CREDENTIALS,
    KNOWN_INSECURE_JWT_SECRETS,
    KNOWN_INSECURE_PLAYBACK_SIGNING_SECRETS,
    Settings,
)

# A production-shaped baseline that passes every check; tests break ONE thing.
GOOD_DB = "postgresql+asyncpg://app_user:s3cr3t-rotated@db.internal:5432/streaming_db"
GOOD_REDIS = "redis://cache.internal:6379/1"
GOOD_JWT = "K7bQx2Zf9pLw4mNc8vRt3yHs6dJg1aEe5uIoP0zXcVb"  # 44 chars
GOOD_SIGNING = "T4r9Wq2Lm7Yb1Nc6Xz8Pd3Hs5Jg0Ae4uIo9rVzXcQb"


def prod_settings(**overrides) -> dict:
    """Build a kwargs dict that satisfies every production guard."""
    kwargs = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": GOOD_DB,
        "REDIS_URL": GOOD_REDIS,
        "JWT_SECRET_KEY": GOOD_JWT,
        "PLAYBACK_URL_SIGNING_SECRET": GOOD_SIGNING,
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
    # Untouched keys still get the dev default.
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
    assert s.PLAYBACK_URL_SIGNING_SECRET == GOOD_SIGNING


# --------------------------------------------------------- DATABASE_URL ----


@pytest.mark.unit
def test_production_requires_explicit_database_url():
    """settings.py:94-97."""
    kwargs = prod_settings()
    del kwargs["DATABASE_URL"]
    with pytest.raises(ValidationError, match="DATABASE_URL must be set explicitly"):
        Settings(_env_file=None, **kwargs)


@pytest.mark.parametrize("credential", KNOWN_INSECURE_DB_CREDENTIALS)
def test_production_rejects_known_insecure_db_credentials(credential):
    """settings.py:98-99 -- each shipped default credential is refused."""
    url = f"postgresql+asyncpg://{credential}@db.internal:5432/streaming_db"
    with pytest.raises(ValidationError, match="known default credentials"):
        Settings(_env_file=None, **prod_settings(DATABASE_URL=url))


# ----------------------------------------------------------- REDIS_URL -----


@pytest.mark.unit
def test_production_requires_explicit_redis_url():
    """settings.py:100-103."""
    kwargs = prod_settings()
    del kwargs["REDIS_URL"]
    with pytest.raises(ValidationError, match="REDIS_URL must be set explicitly"):
        Settings(_env_file=None, **kwargs)


# ------------------------------------------------------ JWT_SECRET_KEY -----


@pytest.mark.unit
def test_production_requires_jwt_secret_key():
    """settings.py:104-107."""
    kwargs = prod_settings()
    del kwargs["JWT_SECRET_KEY"]
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY must be set to a strong"):
        Settings(_env_file=None, **kwargs)


@pytest.mark.unit
@pytest.mark.parametrize("blank", ["", "   ", "\t\n "])
def test_production_rejects_blank_jwt_secret_key(blank):
    """settings.py:109-112 -- whitespace-only is as bad as missing."""
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY must be set to a strong"):
        Settings(_env_file=None, **prod_settings(JWT_SECRET_KEY=blank))


@pytest.mark.parametrize("insecure", KNOWN_INSECURE_JWT_SECRETS)
def test_production_rejects_known_insecure_jwt_secrets(insecure):
    """settings.py:113-116 -- the dev default and its documented aliases."""
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY must be set to a strong"):
        Settings(_env_file=None, **prod_settings(JWT_SECRET_KEY=insecure))


@pytest.mark.unit
def test_production_rejects_short_jwt_secret_key():
    """settings.py:117-120 -- 31 chars is one short of the floor."""
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(_env_file=None, **prod_settings(JWT_SECRET_KEY="a" * 31))


@pytest.mark.unit
def test_production_accepts_exactly_32_character_jwt_secret_key():
    """The length check is inclusive of the documented 32-char floor."""
    s = Settings(_env_file=None, **prod_settings(JWT_SECRET_KEY="b" * 32))
    assert len(s.JWT_SECRET_KEY) == 32


# ------------------------------------------- PLAYBACK_URL_SIGNING_SECRET ----


@pytest.mark.unit
def test_production_rejects_blank_playback_signing_secret():
    """settings.py:124-128 -- an empty signing key must not sign playback URLs."""
    with pytest.raises(
        ValidationError, match="PLAYBACK_URL_SIGNING_SECRET must be set to a strong"
    ):
        Settings(_env_file=None, **prod_settings(PLAYBACK_URL_SIGNING_SECRET="   "))


@pytest.mark.parametrize("insecure", KNOWN_INSECURE_PLAYBACK_SIGNING_SECRETS)
def test_production_rejects_shipped_default_playback_signing_secret(insecure):
    """settings.py:129-133.

    This is the #489 guard: the class default is a shipped dev string, so a
    production deploy that forgets the env var would otherwise HMAC playback
    URLs with a publicly known key.
    """
    with pytest.raises(
        ValidationError, match="PLAYBACK_URL_SIGNING_SECRET must be set to a strong"
    ):
        Settings(_env_file=None, **prod_settings(PLAYBACK_URL_SIGNING_SECRET=insecure))


@pytest.mark.unit
def test_shipped_default_signing_secret_matches_the_known_insecure_set():
    """Ties the class default to the denylist so the two cannot drift apart."""
    default = Settings.model_fields["PLAYBACK_URL_SIGNING_SECRET"].default
    assert default in KNOWN_INSECURE_PLAYBACK_SIGNING_SECRETS


@pytest.mark.unit
def test_shipped_default_jwt_secret_matches_the_known_insecure_set():
    """Same drift guard for the JWT default injected by DEV_DEFAULTS."""
    assert DEV_DEFAULTS["JWT_SECRET_KEY"] in KNOWN_INSECURE_JWT_SECRETS


# ------------------------------------------------- guard ordering / notes --


@pytest.mark.unit
def test_guard_reports_the_first_failure_in_declaration_order():
    """With several problems present the earliest check wins, not the loudest."""
    kwargs = prod_settings()
    del kwargs["DATABASE_URL"]
    kwargs["REDIS_URL"] = None
    kwargs["JWT_SECRET_KEY"] = "short"
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, **kwargs)
    assert "DATABASE_URL" in str(exc.value)


@pytest.mark.unit
def test_insecure_db_credential_is_checked_before_missing_redis():
    """A known-insecure DB URL is reported even when REDIS_URL is also absent."""
    kwargs = prod_settings(DATABASE_URL="postgresql+asyncpg://wildframe:password@h/d")
    del kwargs["REDIS_URL"]
    with pytest.raises(ValidationError, match="known default credentials"):
        Settings(_env_file=None, **kwargs)


@pytest.mark.unit
def test_no_pragma_no_cover_in_settings_module():
    """The production guard must stay measured -- no coverage escape hatches."""
    import inspect

    import app.core.settings as settings_module

    source = inspect.getsource(settings_module)
    assert "pragma: no cover" not in source
