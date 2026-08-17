"""Configuration for Streaming Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    SERVICE_NAME: str = "streaming-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/streaming_db"
    REDIS_URL: str = "redis://localhost:6379/1"
    JWT_SECRET_KEY: str = "dev-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_AUDIENCE: str = "wildframe-api"
    CORS_ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8004
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    LOG_LEVEL: str = "INFO"

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
