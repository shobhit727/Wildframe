"""
Configuration settings for Content Service.
Centralized environment-based configuration management.
"""

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    # Service info
    SERVICE_NAME: str = "content-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/content_db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRATION_DAYS: int = 7

    # CORS
    CORS_ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8000"]

    # Server
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8003

    # Logging
    LOG_LEVEL: str = "INFO"

    # Database pool settings
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10

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


settings = Settings()
