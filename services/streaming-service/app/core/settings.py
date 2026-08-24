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
    JWT_ISSUER: str = "wildframe-auth"
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_EXPIRATION_MINUTES: int = 15

    # Database pool budget (#64/#129): pool_size=5, max_overflow=5 limits
    # connections per service instance to prevent DB exhaustion.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5

    LOG_LEVEL: str = "INFO"
    CORS_ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8004
    # Concurrency limits (#281, #490)
    MAX_ACTIVE_SESSIONS: int = 5
    # ACTIVE sessions idle longer than this are reaped on next start (#490):
    # crashed players otherwise hold slots forever and 409-lock the user out.
    PLAYBACK_SESSION_IDLE_TIMEOUT_MINUTES: int = 90
    # Signed playback URLs (#489, #491)
    PLAYBACK_URL_SIGNING_SECRET: str = "dev-playback-signing-secret-change-in-production"
    PLAYBACK_URL_TTL_SECONDS: int = 3600
    # Entitlement check (#587)
    ENTITLEMENT_CHECK_ENABLED: bool = True

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
