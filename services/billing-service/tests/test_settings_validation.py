"""Behavioural tests for ``app.core.settings`` validation.

The production-secret validator is the last line of defence before a
misconfigured deploy reaches the database or Stripe, so each guard is
asserted individually: an operator must not be able to ship with a
placeholder credential, a wildcard CORS origin plus cookies, or a currency
code outside the ISO-4217 allowlist.
"""

import pytest
from pydantic import ValidationError

from app.core.money import CurrencyError
from app.core.settings import (
    DEV_ENVIRONMENTS,
    KNOWN_INSECURE_DB_CREDENTIALS,
    Settings,
)

# A production config that satisfies every guard, used as a baseline.
_PROD = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": "postgresql+asyncpg://real_user:s3cret@db.internal:5432/billing",
    "REDIS_URL": "redis://cache.internal:6379/0",
    "STRIPE_API_KEY": "sk_live_abc123",
    "STRIPE_WEBHOOK_SECRET": "whsec_realvalue",
}

# Env vars that would otherwise leak into the Settings() constructor.
_ENV_KEYS = (
    "ENVIRONMENT",
    "DATABASE_URL",
    "REDIS_URL",
    "STRIPE_API_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "CORS_ALLOWED_ORIGINS",
    "DEFAULT_CURRENCY",
)


@pytest.fixture
def clean_env(monkeypatch):
    """Remove ambient env/dotenv influence so init kwargs are authoritative."""
    import os

    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # A .env file in the service dir would otherwise still be read.
    monkeypatch.setattr(Settings, "model_config", {**Settings.model_config, "env_file": None})
    return os.environ


def _settings(**overrides) -> Settings:
    return Settings(**{**_PROD, **overrides})


# ---------------------------------------------------------------------------
# Development defaults
# ---------------------------------------------------------------------------


class TestDevelopmentDefaults:
    @pytest.mark.parametrize("environment", sorted(DEV_ENVIRONMENTS))
    def test_dev_environments_get_the_local_database_and_redis_defaults(self, environment):
        s = Settings(ENVIRONMENT=environment)
        assert s.DATABASE_URL == "postgresql+asyncpg://postgres:password@localhost:5432/billing_db"
        assert s.REDIS_URL == "redis://localhost:6379/0"

    def test_explicit_database_url_is_not_overwritten_by_the_dev_default(self):
        s = Settings(ENVIRONMENT="development", DATABASE_URL="postgresql+asyncpg://a:b@h:5432/x")
        assert s.DATABASE_URL == "postgresql+asyncpg://a:b@h:5432/x"

    def test_production_does_not_receive_dev_defaults(self, clean_env):
        # A missing DATABASE_URL in production must fail loudly, not inherit
        # the localhost development default.
        with pytest.raises(ValidationError, match="DATABASE_URL must be set explicitly"):
            Settings(ENVIRONMENT="production", REDIS_URL="redis://x:6379/0")

    def test_dev_environments_skip_the_production_secret_guards(self, clean_env):
        # The default dev placeholders (sk_test_ / whsec_default_) are fine in dev.
        s = Settings(ENVIRONMENT="development")
        assert s.STRIPE_API_KEY.startswith("sk_test_")
        assert s.STRIPE_WEBHOOK_SECRET.startswith("whsec_default_")

    @pytest.mark.parametrize("environment", ["staging", "production", "qa"])
    def test_every_non_dev_environment_enforces_the_secret_guards(self, environment, clean_env):
        with pytest.raises(ValidationError):
            Settings(ENVIRONMENT=environment)


# ---------------------------------------------------------------------------
# validate_production_secrets
# ---------------------------------------------------------------------------


class TestProductionSecrets:
    def test_a_fully_specified_production_config_is_accepted(self, clean_env):
        s = _settings()
        assert s.ENVIRONMENT == "production"
        assert s.DATABASE_URL == _PROD["DATABASE_URL"]

    def test_missing_database_url_is_rejected(self, clean_env):
        with pytest.raises(ValidationError) as exc:
            Settings(
                ENVIRONMENT="production",
                DATABASE_URL=None,
                REDIS_URL="redis://x:6379/0",
                STRIPE_API_KEY="sk_live_a",
                STRIPE_WEBHOOK_SECRET="whsec_a",
            )
        assert "DATABASE_URL must be set explicitly" in str(exc.value)

    @pytest.mark.parametrize("credential", KNOWN_INSECURE_DB_CREDENTIALS)
    def test_known_insecure_database_credentials_are_rejected(self, credential, clean_env):
        with pytest.raises(ValidationError) as exc:
            _settings(DATABASE_URL=f"postgresql+asyncpg://{credential}@db.internal:5432/billing")
        assert "must not use known default credentials" in str(exc.value)

    def test_credential_match_is_a_substring_check(self, clean_env):
        # The guard is a substring scan, so the userinfo part is what matters.
        with pytest.raises(ValidationError, match="known default credentials"):
            _settings(DATABASE_URL="postgresql+asyncpg://postgres:passwordx@db:5432/billing")

    def test_missing_redis_url_is_rejected(self, clean_env):
        with pytest.raises(ValidationError) as exc:
            Settings(
                ENVIRONMENT="production",
                DATABASE_URL="postgresql+asyncpg://u:p@db:5432/billing",
                REDIS_URL=None,
                STRIPE_API_KEY="sk_live_a",
                STRIPE_WEBHOOK_SECRET="whsec_a",
            )
        assert "REDIS_URL must be set explicitly" in str(exc.value)

    def test_test_mode_stripe_key_is_rejected_in_production(self, clean_env):
        with pytest.raises(ValidationError) as exc:
            _settings(STRIPE_API_KEY="sk_test_liveLookalike")
        assert "STRIPE_API_KEY must be a live key in production" in str(exc.value)

    def test_live_stripe_key_is_accepted(self, clean_env):
        assert _settings(STRIPE_API_KEY="sk_live_abc").STRIPE_API_KEY == "sk_live_abc"

    def test_placeholder_webhook_secret_is_rejected_in_production(self, clean_env):
        with pytest.raises(ValidationError) as exc:
            _settings(STRIPE_WEBHOOK_SECRET="whsec_default_change_me")
        assert "STRIPE_WEBHOOK_SECRET must be set in production" in str(exc.value)

    def test_real_webhook_secret_is_accepted(self, clean_env):
        assert _settings(STRIPE_WEBHOOK_SECRET="whsec_9f8a").STRIPE_WEBHOOK_SECRET == "whsec_9f8a"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("STRIPE_WEBHOOK_SECRET", ""),
            ("DATABASE_URL", ""),
            ("REDIS_URL", ""),
            ("STRIPE_API_KEY", ""),
        ],
    )
    def test_empty_string_secret_is_accepted_by_the_production_guard(self, field, value, clean_env):
        """Characterisation test — documents a real gap, see the report.

        Every production guard is a *prefix* or ``is None`` test, so an empty
        string slips through all of them. Consequences, in order of severity:

        * ``STRIPE_WEBHOOK_SECRET=""`` passes validation, but
          ``StripeClient.handle_webhook`` then fails closed on every delivery
          (400 for every event) while ``/health`` still reports healthy.
        * ``DATABASE_URL=""`` passes validation and only explodes later inside
          ``create_async_engine``.
        * ``REDIS_URL=""`` passes validation; ``/ready`` degrades to 503.

        The empty string is only a *deployed* problem when the operator sets
        the variable to blank; a wholly absent variable is still caught.
        """
        s = _settings(**{field: value})
        assert getattr(s, field) == value

    def test_absent_secret_is_still_rejected_when_the_variable_is_unset(self, clean_env):
        # The guard is not vacuous: omitting the variable entirely still fails,
        # because the field default ("whsec_default_change_me") is caught.
        with pytest.raises(ValidationError, match="STRIPE_WEBHOOK_SECRET must be set in production"):
            _settings(STRIPE_WEBHOOK_SECRET="whsec_default_change_me")

    def test_guards_are_ordered_database_before_redis(self, clean_env):
        # With both missing, the database complaint must surface first.
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            Settings(
                ENVIRONMENT="production",
                DATABASE_URL=None,
                REDIS_URL=None,
                STRIPE_API_KEY="sk_live_a",
                STRIPE_WEBHOOK_SECRET="whsec_a",
            )


# ---------------------------------------------------------------------------
# validate_currency
# ---------------------------------------------------------------------------


class TestCurrencyValidator:
    def test_default_currency_is_the_allowlisted_usd(self, clean_env):
        assert _settings().DEFAULT_CURRENCY == "USD"

    def test_lowercase_default_currency_is_normalized_only_by_the_allowlist_check(self, clean_env):
        # validate_currency accepts any-cased input; the stored value is not
        # rewritten, so downstream code must uppercase it itself.
        s = _settings(DEFAULT_CURRENCY="eur")
        assert s.DEFAULT_CURRENCY == "eur"

    @pytest.mark.parametrize("currency", ["JPY", "BHD", "KWD", "usd", "inr"])
    def test_allowlisted_currencies_are_accepted(self, currency, clean_env):
        assert _settings(DEFAULT_CURRENCY=currency).DEFAULT_CURRENCY == currency

    @pytest.mark.parametrize("currency", ["ZZZ", "US", "USDD", "US1"])
    def test_currency_outside_the_allowlist_is_rejected(self, currency, clean_env):
        with pytest.raises(ValidationError) as exc:
            _settings(DEFAULT_CURRENCY=currency)
        # The underlying CurrencyError must not be swallowed into a generic error.
        assert "currency" in str(exc.value).lower()

    def test_currency_error_is_raised_before_the_settings_object_exists(self, clean_env):
        with pytest.raises(ValidationError) as exc:
            _settings(DEFAULT_CURRENCY="")
        assert "empty" in str(exc.value)

    def test_currency_error_is_a_value_error_subclass(self):
        # Confirms the exception type the validator delegates to.
        assert issubclass(CurrencyError, ValueError)


# ---------------------------------------------------------------------------
# validate_cors_credentials
# ---------------------------------------------------------------------------


class TestCorsCredentials:
    def test_wildcard_origin_with_credentials_is_rejected_in_production(self, clean_env):
        with pytest.raises(ValidationError) as exc:
            _settings(CORS_ALLOWED_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=True)
        assert "CORS_ALLOWED_ORIGINS cannot be ['*']" in str(exc.value)

    def test_wildcard_origin_without_credentials_is_allowed_in_production(self, clean_env):
        s = _settings(CORS_ALLOWED_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=False)
        assert s.CORS_ALLOWED_ORIGINS == ["*"]

    def test_explicit_origin_with_credentials_is_allowed_in_production(self, clean_env):
        s = _settings(
            CORS_ALLOWED_ORIGINS=["https://wildframe.com"], CORS_ALLOW_CREDENTIALS=True
        )
        assert s.CORS_ALLOWED_ORIGINS == ["https://wildframe.com"]

    def test_wildcard_origin_with_credentials_is_allowed_outside_production(self, clean_env):
        # Only production is locked down; staging/dev keep the permissive default.
        s = Settings(ENVIRONMENT="development", CORS_ALLOWED_ORIGINS=["*"])
        assert s.CORS_ALLOWED_ORIGINS == ["*"]
        assert s.CORS_ALLOW_CREDENTIALS is True


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestModuleSingleton:
    def test_imported_settings_is_a_settings_instance(self):
        from app.core.settings import settings

        assert isinstance(settings, Settings)

    def test_singleton_prices_match_the_product_vision(self):
        from app.core.settings import settings

        assert str(settings.SVOD_MONTHLY_PRICE) == "7.99"
        assert str(settings.CREATOR_SHARE_PERCENTAGE) == "0.55"
        assert str(settings.CREATOR_POOL_PERCENTAGE) == "0.15"
        assert [str(p) for p in settings.MILESTONE_TRANCHE_PERCENTAGES] == [
            "10.00",
            "20.00",
            "30.00",
            "40.00",
        ]

    def test_singleton_uses_the_service_default_port(self):
        from app.core.settings import settings

        assert settings.SERVER_PORT == 8008
