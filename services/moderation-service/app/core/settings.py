"""Configuration settings for the Moderation Service."""

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
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:password@localhost:5432/moderation_db"
    )

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

    # Strike policy
    # A creator is automatically suspended once this many active strikes
    # accumulate. Enforced in the service layer (not a DB constraint) so the
    # threshold is easy to tune and the suspension logic can emit an event.
    STRIKES_BEFORE_SUSPENSION: int = 3
    # How long a strike stays active before it expires (days). Expired strikes
    # no longer count toward the suspension threshold.
    STRIKE_EXPIRES_DAYS: int = 90

    # Server
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8010

    # Event bus adapter (memory for dev/test, kafka for production).
    EVENT_PUBLISHER: str = "memory"

    # Kafka bootstrap (only used when EVENT_PUBLISHER=kafka).
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"

    class Config:
        env_file = ".env"


settings = Settings()
