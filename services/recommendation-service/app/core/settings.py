"""Configuration settings for Recommendation Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    SERVICE_NAME: str = "Recommendation"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/recommendation_db"

    # Content catalog
    CONTENT_SERVICE_URL: str = "http://content-service:8000"
    CONTENT_CATALOG_TIMEOUT_SECONDS: float = 10.0
    CONTENT_CATALOG_MAX_CONNECTIONS: int = 20
    CONTENT_CATALOG_MAX_KEEPALIVE: int = 10

    # Generation bounds (#228 F4): candidate/model outputs are capped so one
    # request can never fan out unbounded work or unbounded result rows.
    MAX_RECOMMENDATION_LIMIT: int = 100
    MAX_PREFERENCE_GENRES: int = 50
    MAX_CANDIDATES: int = 500
    MAX_CATALOG_PAGE_SIZE: int = 100

    # Events (#228 F3): content.deleted / content.unpublished drive cache
    # eviction. "memory" (default) for tests; "kafka" in the dev stack.
    EVENT_PUBLISHER: str = "memory"
    KAFKA_BOOTSTRAP_SERVERS: str = "kafka:29092"
    KAFKA_CONSUMER_GROUP: str = "recommendation-service"

    # Security
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_ISSUER: str = "wildframe-auth"
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
        if self.ENVIRONMENT == "production" and self.JWT_SECRET_KEY in default_secrets:
            raise ValueError(
                "JWT_SECRET_KEY must be set to a strong random value in production. "
                "Refusing to start with default insecure secret."
            )
        return self

    # Issue #469: Metrics endpoint token
    METRICS_TOKEN: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
