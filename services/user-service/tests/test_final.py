"""Compliance-surface invariants for user-service settings.

The previous version of this file was a byte-identical placeholder copied
across five services (`assert True` / `assert "EU" in ["EU", "US", ...]`).

`app.core.settings.Settings` mixes in `ComplianceSettingsMixin`, so the
compliance fields are part of the *public* configuration of this service: a
deployment can override them per environment. These tests pin the shipped
defaults and prove the fields are real, settable settings rather than dead
attributes - a regression that dropped the mixin would otherwise be invisible.
"""

import pytest
from pydantic import ValidationError

from app.core.settings import DEV_DEFAULTS, DEV_ENVIRONMENTS, Settings

# The jurisdiction + contact block the service advertises at boot.
EXPECTED_REGIONS = {"US", "EU", "IN", "SG"}
EXPECTED_CONTACT_DOMAINS = ("@wildframe.com",)


def test_compliance_defaults_are_present():
    settings = Settings()

    assert settings.compliance_dpo_email in [f"dpo{d}" for d in EXPECTED_CONTACT_DOMAINS]
    assert settings.compliance_grievance_officer_email.startswith("grievance")
    assert set(settings.compliance_allowed_data_regions) == EXPECTED_REGIONS


def test_primary_and_additional_jurisdictions_cover_the_platform():
    from wildframe_compliance.jurisdiction import Jurisdiction

    settings = Settings()

    assert settings.compliance_jurisdiction is Jurisdiction.GLOBAL
    assert set(settings.compliance_additional_jurisdictions) == {
        Jurisdiction.EU,
        Jurisdiction.US,
        Jurisdiction.IN,
    }


def test_compliance_fields_are_overridable_per_deployment():
    from wildframe_compliance.jurisdiction import Jurisdiction

    settings = Settings(
        ENVIRONMENT="development",
        compliance_jurisdiction=Jurisdiction.EU,
        compliance_additional_jurisdictions=[Jurisdiction.EU],
        compliance_allowed_data_regions=["EU"],
        compliance_dpo_email="dpo@example.test",
        compliance_grievance_officer_email="grievance@example.test",
    )

    assert settings.compliance_jurisdiction is Jurisdiction.EU
    assert settings.compliance_additional_jurisdictions == [Jurisdiction.EU]
    assert settings.compliance_allowed_data_regions == ["EU"]
    assert settings.compliance_dpo_email == "dpo@example.test"


def test_an_unknown_jurisdiction_is_rejected_at_construction():
    with pytest.raises(ValidationError):
        Settings(ENVIRONMENT="development", compliance_jurisdiction="MARS")


def test_login_rate_limit_defaults_match_the_auth_service_window():
    settings = Settings()

    # 10 attempts per 15 minutes (900s).
    assert settings.LOGIN_RATE_LIMIT_ATTEMPTS == 10
    assert settings.LOGIN_RATE_LIMIT_WINDOW == 900


def test_jwt_settings_match_the_platform_contract():
    settings = Settings()

    assert settings.JWT_ALGORITHM == "HS256"
    assert settings.JWT_AUDIENCE == "wildframe-api"
    assert settings.JWT_ISSUER == "wildframe-auth"
    assert settings.JWT_EXPIRATION_MINUTES == 15
    assert settings.PASSWORD_BCRYPT_ROUNDS == 12


def test_known_insecure_dev_jwt_secret_is_never_valid_in_production():
    dev_secret = DEV_DEFAULTS["JWT_SECRET_KEY"]
    assert dev_secret in Settings(ENVIRONMENT="development").JWT_SECRET_KEY

    with pytest.raises(ValidationError, match="strong random value"):
        Settings(
            ENVIRONMENT="production",
            DATABASE_URL="postgresql+asyncpg://u:p@db:5432/users_db",
            REDIS_URL="redis://redis:6379/0",
            KAFKA_BOOTSTRAP_SERVERS="kafka:9092",
            JWT_SECRET_KEY=dev_secret,
        )


def test_dev_environments_are_exactly_the_three_documented_ones():
    assert DEV_ENVIRONMENTS == {"", "development", "test"}
