"""Configuration settings for Api Gateway Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    SERVICE_NAME: str = "Api Gateway"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Database (api-gateway is stateless — no DB)

    # Security
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 15

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

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
        if self.ENVIRONMENT == "production" and (
            self.JWT_SECRET_KEY in default_secrets or self.CORS_ALLOWED_ORIGINS == ["*"]
        ):
            raise ValueError(
                "JWT_SECRET_KEY must be a strong secret and CORS_ALLOWED_ORIGINS must "
                "be an explicit origin list in production. "
            )
        return self

    class Config:
        env_file = ".env"


settings = Settings()
