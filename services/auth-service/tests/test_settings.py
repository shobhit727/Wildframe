"""
Tests for production configuration validation.

Ensures the Auth Service fails closed when running in a production
environment with missing or default-insecure secrets.
"""

import pytest
from app.core.settings import Settings

PROD_DB = "postgresql+asyncpg://app:realpass@db.example.com:5432/auth_db"
PROD_REDIS = "redis://redis.example.com:6379/0"
PROD_JWT = """-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQCsfYc3wbRl3CMy
YsVZcpZ09DJaJVwQRTbDZrDULASBu/2f0OdJJyKt7dA8SZ0YRINXfxdOGySkA0cO
iiroZMS8YBM0kAiUyiRdfhEMj+DDweuw2GdTKmQlYYNnwpPKkoNj7M4qL28nwhAR
uBldRJWl7kd35Kq6bOLUzt7sr68Ku+W19IVWPEU2DByaMeAUh8uOz3trB1rpPhH/
KGrWcFmg8OAOVf6uBP6vhNTTQVUGlEmg5pc7WBk/0pki7FFJO+Gnb9P046BZd72h
mDRUd0djCWjeXrQyWq6mewH/y8phVI5Bbd/TPp7S3YVEF6NsvvsLuikR+A6lr3X7
7MiWGOV9AgMBAAECggEABeEObYxU437mFTbQHq0c6zYSjEeHNIfDZtQWdVjdu3VG
nv6eD3x9vltjdFaW++eD6YTLrM4YiN2Su7BiZ4LdwMTuiqVZIYclR6l/F8REpy9y
ItgxZ2CDwtFoyu3THz2wrER7P21X9s0ywoPZm3f1uM7fETtSRWHmNlYpu3v+dZll
FsgeR/dzgtni4qLf9wy51S7oOsKU4+qtJjCZqgwxKI0Xee5k0ZdhZjsrjGpKz0cz
Awao9R/DMX5zGCbAm8VXKw/Ya/F6kEDz0KwLCFpPdx+riTh1IsjXiLWjYcIKA0PL
NWEcSfkInWxDtlNO/zY8+R9+EA38D8xspV/K+b2XCQKBgQDU1iUcr0gjUWVrYouj
ozOxj2FdFYb5Xx7gJbVis0+8uLUHmbV9s9EqGuCJ6rpCRrg1k8J4FS5rGDCmxRio
it/Ox+XPa6opa4YCJqT7EQvXNcO+/GskNDRDID3L7qPociLYvJ66MSYhPmwj3/CZ
k8h4SsTXJU5vbfC9sT1hsDKXpQKBgQDPeLo6bYJ5lsnZWvmvmefXbCJ10Oyx0iTN
hxOjsnyWv3Z94s5ulbpZgpyyVHnA074iEI9ARCK11ShXF9lcEeJeo3v6/Z4lC71o
NIM7DTqxxHMsHZqzztIQAYjtIe0Hv3uwoNKCMjt+Emh7p3sX4mQUv5ZDGzc0JM7b
YlRdd8ju+QKBgGT/6kSeeWEpMzOuZA2XWOSd4dpGaPLVzNUZj+XyqZgpHt8odhPc
zRlp/7vzA8iHvsrN/670fj6cEBpT1cvFe0epXMj9kpZtS/6hUBFEmZXbEbUEG+Pm
Uha4qhqoeGfKIfcwKzK4OBv2f2LW1lpK4wsSkC54qav/RAsAnNxKvPdxAoGBAKLd
3Bt30iAO/h+RqkZuZDCZI6gnPVgOZnOtYP51ZBaW8La78F+hTGtt/AKGDBoSXsSx
CTNjCXiCf6t2/luncnPmlLIgnB/qymJeLtKRfQ0F8X+lMceLSR3lho7YvhECAWBT
r00jj85VNw4zGI9UWkprZ9MAL2LQrk5ML3w8R1FJAoGBAJNsMHsImGCvcYcCm/JY
VUqtii+Ko43Bxxa3cBIwLafszt/RmHFoILjgKrPKY8qerntM79216IL1O+JUgKT6
yTgAzrkJR2yhpk3/77JNtD6zlXShRnbURcZGNeLlSdGEfAsqxcD3D/WMzKrg05sW
f1SJ8o3AR/cKS/wuQY/cgNcD
-----END PRIVATE KEY-----"""
PROD_KAFKA = "kafka.example.com:9092"

PROD_KWARGS = {
    "ENVIRONMENT": "production",
    "DATABASE_URL": PROD_DB,
    "REDIS_URL": PROD_REDIS,
    "JWT_PRIVATE_KEY": PROD_JWT,
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
        kwargs = dict(PROD_KWARGS)
        kwargs.pop("JWT_PRIVATE_KEY")
        with pytest.raises(ValueError, match="JWT_PRIVATE_KEY"):
            Settings(**kwargs)

    def test_short_jwt_secret_fails(self):
        kwargs = dict(PROD_KWARGS)
        kwargs["JWT_PRIVATE_KEY"] = "not-a-pem"
        with pytest.raises(ValueError, match="JWT_PRIVATE_KEY"):
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
        assert settings.JWT_PRIVATE_KEY == PROD_JWT
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
        kwargs["JWT_PRIVATE_KEY"] = "not-a-pem"
        with pytest.raises(ValueError) as exc_info:
            Settings(**kwargs)
        message = str(exc_info.value)
        assert "not-a-pem" not in message

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
