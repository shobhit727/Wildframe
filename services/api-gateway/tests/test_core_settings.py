"""Behavioural tests for the api-gateway configuration model.

The interesting surface is the ``validate_production_secrets`` model validator
(``app/core/settings.py``): it must let development/test environments fall
through untouched, and it must refuse to boot a non-dev deployment that has no
Redis URL, a missing/known-insecure/short JWT secret, or credentialed CORS with
a wildcard (or empty) origin list.
"""

import pytest
from pydantic import ValidationError

from app.core.settings import (
    DEV_DEFAULTS,
    DEV_ENVIRONMENTS,
    KNOWN_INSECURE_JWT_SECRETS,
    Settings,
)

# A secret that passes every production check: long, not on the denylist.
STRONG_SECRET = "k" * 48


def _prod(**overrides):
    """Build a Settings instance that would be valid in production."""
    base = {
        "ENVIRONMENT": "production",
        "REDIS_URL": "rediss://cache.internal:6379/0",
        "JWT_SECRET_KEY": STRONG_SECRET,
        "CORS_ALLOWED_ORIGINS": ["https://app.wildframe.com"],
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


# --------------------------------------------------------------------------
# development defaults
# --------------------------------------------------------------------------


@pytest.mark.parametrize("environment", sorted(DEV_ENVIRONMENTS))
def test_dev_environments_receive_the_bundled_dev_defaults(environment):
    s = Settings(_env_file=None, ENVIRONMENT=environment)
    assert s.REDIS_URL == DEV_DEFAULTS["REDIS_URL"]
    assert s.JWT_SECRET_KEY == DEV_DEFAULTS["JWT_SECRET_KEY"]


def test_explicit_values_win_over_the_development_defaults():
    """setdefault must not clobber what the operator supplied."""
    s = Settings(
        _env_file=None,
        ENVIRONMENT="test",
        REDIS_URL="redis://127.0.0.1:6399",
        JWT_SECRET_KEY="a-perfectly-explicit-secret-value-1234",
    )
    assert s.REDIS_URL == "redis://127.0.0.1:6399"
    assert s.JWT_SECRET_KEY == "a-perfectly-explicit-secret-value-1234"


@pytest.mark.parametrize("environment", sorted(DEV_ENVIRONMENTS - {""}))
def test_dev_environments_never_trip_the_production_guards(environment):
    """A dev build carrying a well-known dev secret is perfectly legal."""
    s = Settings(_env_file=None, ENVIRONMENT=environment)
    assert s.JWT_SECRET_KEY == DEV_DEFAULTS["JWT_SECRET_KEY"]
    # The validator short-circuits, so no Redis/CORS requirement is imposed.
    assert s.REDIS_URL == DEV_DEFAULTS["REDIS_URL"]


def test_validate_production_secrets_returns_self_unchanged_in_development():
    s = Settings(_env_file=None, ENVIRONMENT="development")
    assert s.validate_production_secrets() is s


# --------------------------------------------------------------------------
# production guards
# --------------------------------------------------------------------------


def test_production_requires_an_explicit_redis_url():
    with pytest.raises(ValidationError, match="REDIS_URL must be set explicitly"):
        _prod(REDIS_URL=None)


def test_production_requires_a_jwt_secret():
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY must be set to a strong"):
        _prod(JWT_SECRET_KEY=None)


@pytest.mark.parametrize("insecure", KNOWN_INSECURE_JWT_SECRETS)
def test_production_rejects_every_known_insecure_jwt_secret(insecure):
    """The denylist exists so the dev secret cannot leak into production.

    The denylist is consulted *before* the length floor, so a short denylisted
    value must still raise the denylist error rather than the length error.
    """
    with pytest.raises(ValidationError, match="must be set to a strong random value"):
        _prod(JWT_SECRET_KEY=insecure)


def test_production_rejects_a_short_jwt_secret():
    with pytest.raises(ValidationError, match="at least 32 characters"):
        _prod(JWT_SECRET_KEY="s" * 31)


@pytest.mark.parametrize("secret", [" " * 40, f"{'s' * 32} ", " {0} ".format("s" * 32)])
def test_production_rejects_whitespace_jwt_secrets(secret):
    with pytest.raises(ValidationError):
        _prod(JWT_SECRET_KEY=secret)


def test_production_accepts_a_secret_exactly_at_the_length_floor():
    s = _prod(JWT_SECRET_KEY="s" * 32)
    assert len(s.JWT_SECRET_KEY) == 32


def test_production_rejects_wildcard_cors_origins_with_credentials():
    with pytest.raises(ValidationError, match="explicit origin list in production"):
        _prod(CORS_ALLOWED_ORIGINS=["https://app.wildframe.com", "*"])


def test_production_rejects_an_empty_cors_origin_list_with_credentials():
    with pytest.raises(ValidationError, match="explicit origin list in production"):
        _prod(CORS_ALLOWED_ORIGINS=[])


def test_production_allows_wildcard_origins_when_credentials_are_disabled():
    """No credentials means no cookie-bearing cross-origin risk."""
    s = _prod(CORS_ALLOWED_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=False)
    assert s.CORS_ALLOWED_ORIGINS == ["*"]


def test_redis_url_is_checked_before_the_jwt_secret():
    """A deployment missing both secrets must complain about Redis first."""
    with pytest.raises(ValidationError) as exc:
        _prod(REDIS_URL=None, JWT_SECRET_KEY=None)
    assert "REDIS_URL" in str(exc.value)
    assert "JWT_SECRET_KEY" not in str(exc.value)


def test_production_accepts_a_fully_hardened_configuration():
    s = _prod()
    assert s.ENVIRONMENT == "production"
    assert s.JWT_SECRET_KEY == STRONG_SECRET
    assert s.REDIS_URL == "rediss://cache.internal:6379/0"
    assert s.CORS_ALLOW_CREDENTIALS is True


# --------------------------------------------------------------------------
# module-level settings singleton
# --------------------------------------------------------------------------


def test_module_level_settings_carries_the_gateway_defaults():
    from app.core.settings import settings

    assert settings.SERVICE_NAME == "Api Gateway"
    assert settings.SERVER_PORT == 8000
    assert settings.JWT_ALGORITHM == "HS256"
    assert settings.JWT_EXPIRATION_MINUTES == 15
    assert settings.MAX_DECOMPRESSION_RATIO == 10
    assert settings.TRUST_PROXY is False


def test_settings_declares_a_compliance_dataframe():
    from wildframe_compliance.jurisdiction import Jurisdiction

    from app.core.settings import settings

    assert settings.compliance_dpo_email
    assert settings.compliance_grievance_officer_email
    assert Jurisdiction.EU in settings.compliance_additional_jurisdictions
    assert "US" in settings.compliance_allowed_data_regions
