"""
Core configuration module for Auth Service.
Manages environment-based settings and dependency injection.
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Service Configuration
    SERVICE_NAME: str = "auth-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False

    # Database Configuration
    DATABASE_URL: str = "postgresql+asyncpg://wildframe:password@localhost:5432/auth_db"
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_POOL_TIMEOUT: int = 30
    DATABASE_POOL_RECYCLE: int = 3600

    # Redis Configuration
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_TIMEOUT: int = 5

    # JWT Configuration
    JWT_SECRET_KEY: str = "dev-secret-key"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRATION_DAYS: int = 7
    TOKEN_BLACKLIST_ENABLED: bool = True

    # Admin roles — comma-separated emails whose tokens/me carry role "admin".
    ADMIN_EMAILS: str = ""

    # Kafka Configuration
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_GROUP_ID: str = "auth-service"
    KAFKA_TOPIC_USER_CREATED: str = "user.registered"
    KAFKA_TOPIC_USER_LOGIN: str = "user.login"
    KAFKA_TOPIC_TOKEN_REVOKED: str = "token.revoked"

    # Security Configuration
    PASSWORD_MIN_LENGTH: int = 8
    PASSWORD_REQUIRE_UPPERCASE: bool = True
    PASSWORD_REQUIRE_DIGITS: bool = True
    PASSWORD_REQUIRE_SPECIAL: bool = True
    PASSWORD_BCRYPT_ROUNDS: int = 12

    # Rate Limiting Configuration
    RATE_LIMIT_ENABLED: bool = True
    LOGIN_RATE_LIMIT_ATTEMPTS: int = 5
    LOGIN_RATE_LIMIT_WINDOW: int = 900  # 15 minutes
    REGISTRATION_RATE_LIMIT_ATTEMPTS: int = 3
    REGISTRATION_RATE_LIMIT_WINDOW: int = 3600  # 1 hour

    # CORS Configuration
    CORS_ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:3001"]
    CORS_ALLOW_CREDENTIALS: bool = True

    # Observability Configuration
    LOG_LEVEL: str = "INFO"
    JAEGER_ENABLED: bool = False
    JAEGER_AGENT_HOST: str = "localhost"
    JAEGER_AGENT_PORT: int = 6831
    JAEGER_SERVICE_NAME: str = "auth-service"

    # Email Configuration
    EMAIL_VERIFICATION_ENABLED: bool = True
    EMAIL_VERIFICATION_EXPIRATION_HOURS: int = 24

    # MFA Configuration
    MFA_ENABLED: bool = True
    MFA_ISSUER_NAME: str = "Wildframe"
    MFA_CHALLENGE_EXPIRATION_MINUTES: int = 5
    MFA_BACKUP_CODES_COUNT: int = 10
    MFA_BACKUP_CODE_LENGTH: int = 8

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Fail fast if running in production with default insecure secrets."""
        default_secrets = [
            "your-secret-key-change-in-production",
            "dev-secret-key",
        ]
        if self.ENVIRONMENT == "production" and self.JWT_SECRET_KEY in default_secrets:
            raise ValueError(
                "JWT_SECRET_KEY must be set to a strong random value in production. "
                "Refusing to start with default insecure secret."
            )
        return self

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance.

    Returns:
        Settings: Cached application settings
    """
    return Settings()


# Global settings instance
settings = get_settings()
