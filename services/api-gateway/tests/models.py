"""Model-like behavioral tests for api-gateway service.

The API gateway has no ORM models; we treat its configuration and helper
functions as the "models" to test. Tests cover Settings validation, the
ServiceRegistry lookup, and the age‑gate logic used by the privacy proxy.
"""

import pytest
from fastapi import HTTPException

# Import objects under test
from app.core.settings import Settings
from app.middleware import ServiceRegistry
from app.core.age_gate import is_age_restricted, check_age_gate


# Helper to create a minimal Request‑like object
class DummyURL:
    def __init__(self, path: str):
        self.path = path


class DummyRequest:
    def __init__(self, path: str):
        self.url = DummyURL(path)


def test_settings_production_secrets_invalid():
    """Default secrets must raise in production mode."""
    with pytest.raises(ValueError, match="JWT_SECRET_KEY must be a strong secret"):
        Settings(env_file=".env", ENVIRONMENT="production")


def test_settings_non_production_allows_defaults():
    """Non‑production settings accept default secret/key."""
    s = Settings(ENVIRONMENT="development")
    assert s.JWT_SECRET_KEY == "your-secret-key-change-in-production"
    assert s.CORS_ALLOWED_ORIGINS == [] or isinstance(s.CORS_ALLOWED_ORIGINS, list)


def test_service_registry_lookup():
    url = ServiceRegistry.get_service_url("auth")
    assert url == "http://auth-service:8000"
    assert ServiceRegistry.get_service_url("unknown") is None


def test_age_gate_not_restricted_returns_none():
    req = DummyRequest("/public/info")
    assert check_age_gate(req) is None


def test_age_gate_missing_header_raises():
    req = DummyRequest("/maturity/video")
    with pytest.raises(HTTPException) as exc:
        check_age_gate(req)
    assert exc.value.status_code == 403
    assert "Age verification required" in exc.value.detail


def test_age_gate_allows_verified_minor():
    req = DummyRequest("/maturity/video")
    result = check_age_gate(
        req,
        x_age_verified="true",
        x_is_minor="true",
        x_jurisdiction="EU",
    )
    assert result == {"age_verified": True, "is_minor": True, "jurisdiction": "EU"}


def test_is_age_restricted_detection():
    assert is_age_restricted("/maturity/film") is True
    assert is_age_restricted("/content/restricted/item") is True
    assert is_age_restricted("/open") is False
