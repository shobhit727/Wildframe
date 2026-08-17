"""Configuration settings for Analytics Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    SERVICE_NAME: str = "Analytics"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/analytics_db"

    # Security
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 15
    JWT_AUDIENCE: str = "wildframe-api"

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # Content ownership resolution (creator dashboard / content performance).
    # Set to http://content-service:8000 inside the docker network.
    CONTENT_SERVICE_URL: str = "http://localhost:8003"
    CONTENT_SERVICE_TIMEOUT_SECONDS: float = 3.0
    CONTENT_SERVICE_MAX_CONNECTIONS: int = 10

    # Role claims: only this role may access another user's analytics.
    PRIVILEGED_ROLE: str = "admin"

    # Logging
    LOG_LEVEL: str = "INFO"

    # CORS
    CORS_ALLOWED_ORIGINS: list[str] = ["*"]
    CORS_ALLOW_CREDENTIALS: bool = True

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


settings = Settings()
