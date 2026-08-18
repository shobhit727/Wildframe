"""
Tests for production configuration validation.

Ensures the Auth Service fails closed when running in a production
environment with missing or default-insecure secrets.
"""

import pytest
from app.core.settings import Settings

PROD_DB = "postgresql+asyncpg://app:realpass@db.example.com:5432/auth_db"
PROD_REDIS = "redis://redis.example.com:6379/0"
PROD_JWT = "k" * 64
PROD_KAFKA = "kafka.example.com:9092"

PROD_KWARGS = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": PROD_DB,
    "REDIS_URL": PROD_REDIS,
    "JWT_SECRET_KEY": PROD_JWT,
    "KAFKA_BOOTSTRAP_SERVERS": PROD_KAFKA,
}


class TestProductionSettingsFailClosed:
    def test_missing_database_url_fails(self):
        kwargs = dict(PROD_KWARGS)
        kwargs.pop("DATABASE_URL")
        with pytest.raises(ValueError, match="DATABASE_URL"):
            Settings(**kwargs)

    def test_default_database_credentials_fail(self):
        kwargs = dict(PROD_KWARGS)
        kwargs["DATABASE_URL"] = "postgresql+asyncpg://wildframe:password@db:5432/auth_db"
        with pytest.raises(ValueError):
            Settings(**kwargs)

    def test_missing_redis_url_fails(self):
        kwargs = dict(PROD_KWARGS)
        kwargs.pop("REDIS_URL")
        with pytest.raises(ValueError, match="REDIS_URL"):
            Settings(**kwargs)

    def test_default_jwt_secret_fails(self):
        for secret in ("dev-secret-key", "your-secret-key-change-in-production"):
            kwargs = dict(PROD_KWARGS)
            kwargs["JWT_SECRET_KEY"] = secret
            with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
                Settings(**kwargs)

    def test_short_jwt_secret_fails(self):
        kwargs = dict(PROD_KWARGS)
        kwargs["JWT_SECRET_KEY"] = "not-long-enough"
        with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
            Settings(**kwargs)

    def test_missing_kafka_bootstrap_fails(self):
        kwargs = dict(PROD_KWARGS)
        kwargs.pop("KAFKA_BOOTSTRAP_SERVERS")
        with pytest.raises(ValueError, match="KAFKA_BOOTSTRAP_SERVERS"):
            Settings(**kwargs)

    def test_explicit_safe_values_pass(self):
        settings = Settings(**PROD_KWARGS)
        assert settings.DATABASE_URL == PROD_DB
        assert settings.REDIS_URL == PROD_REDIS
        assert settings.JWT_SECRET_KEY == PROD_JWT
        assert settings.KAFKA_BOOTSTRAP_SERVERS == PROD_KAFKA

    def test_error_messages_do_not_leak_secrets(self):
        kwargs = dict(PROD_KWARGS)
        kwargs["DATABASE_URL"] = "postgresql+asyncpg://wildframe:password@db:5432/auth_db"
        with pytest.raises(ValueError) as exc_info:
            Settings(**kwargs)
        message = str(exc_info.value)
        assert "wildframe:password" not in message
        assert "postgresql+asyncpg" not in message

        kwargs = dict(PROD_KWARGS)
        kwargs["JWT_SECRET_KEY"] = "dev-secret-key-change-in-production-min-32-bytes"
        with pytest.raises(ValueError) as exc_info:
            Settings(**kwargs)
        message = str(exc_info.value)
        assert "dev-secret-key" not in message

    def test_unknown_environment_is_not_development(self):
        with pytest.raises(ValueError, match="DATABASE_URL"):
            Settings(ENVIRONMENT="staging")


class TestDevelopmentSettingsKeepDefaults:
    def test_development_keeps_local_defaults(self):
        settings = Settings(ENVIRONMENT="development")
        assert settings.DATABASE_URL.startswith("postgresql+asyncpg://wildframe:password@localhost")
        assert settings.REDIS_URL == "redis://localhost:6379/0"
        assert settings.JWT_SECRET_KEY is not None
        assert settings.KAFKA_BOOTSTRAP_SERVERS == "localhost:9092"

    def test_default_environment_is_development(self):
        settings = Settings()
        assert settings.ENVIRONMENT == "development"
        assert settings.DATABASE_URL.startswith("postgresql+asyncpg://")

    def test_test_environment_keeps_local_defaults(self):
        settings = Settings(ENVIRONMENT="test")
        assert settings.REDIS_URL == "redis://localhost:6379/0"
