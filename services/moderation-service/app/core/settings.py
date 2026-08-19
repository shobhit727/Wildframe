"""Configuration settings for the Moderation Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings.

    Twelve-factor: every value has a safe dev default and is overridable via
    environment variable (or a ``.env`` file loaded by pydantic-settings).
    """

    SERVICE_NAME: str = "Moderation"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/moderation_db"

    # Security
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 15
    # Audience of auth-service-issued tokens. Every service that verifies
    # those tokens must decode with this audience (see AGENTS.md).
    JWT_ISSUER: str = "wildframe-auth"

    # Database pool budget (#64/#129): pool_size=5, max_overflow=5 limits
    # connections per service instance to prevent DB exhaustion.
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 5

    # Redis
    # Logging
    LOG_LEVEL: str = "INFO"

    # CORS
    CORS_ALLOWED_ORIGINS: list[str] = ["*"]
    CORS_ALLOW_CREDENTIALS: bool = True

    @model_validator(mode="after")
    def validate_cors_credentials(self) -> "Settings":
        """Reject wildcard CORS with credentials in production (#68)."""
        if (
            self.ENVIRONMENT == "production"
            and self.CORS_ALLOWED_ORIGINS == ["*"]
            and self.CORS_ALLOW_CREDENTIALS
        ):
            raise ValueError(
                "CORS_ALLOWED_ORIGINS cannot be ['*'] with CORS_ALLOW_CREDENTIALS=True in production. "
                "Use explicit origin list or disable credentials."
            )
        return self


    # Server
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8010

    # Event bus adapter (memory for dev/test, kafka for production).
    EVENT_PUBLISHER: str = "memory"

    # Kafka bootstrap (only used when EVENT_PUBLISHER=kafka).
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"

    # Transactional outbox
    # Events are written to the DB in the same transaction as their business
    # state; a background worker publishes PENDING rows in batches and marks
    # them dispatched. At-least-once delivery, consumers dedupe on event_key.
    OUTBOX_BATCH_SIZE: int = 100
    OUTBOX_POLL_INTERVAL_SECONDS: int = 5

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
