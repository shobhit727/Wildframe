"""Configuration settings for Recommendation Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin


class Settings(ComplianceSettingsMixin, BaseSettings):
    """Application settings."""

    SERVICE_NAME: str = "Recommendation"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/recommendation_db"

    # Content catalog
    CONTENT_SERVICE_URL: str = "http://content-service:8003"
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
    # Explicit allow-list (#68): wildcard origins paired with credentials are
    # rejected by browsers and invite CSRF; production must override with the
    # real frontend origin(s).
    CORS_ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "https://localhost:3000",
    ]
    CORS_ALLOW_CREDENTIALS: bool = True

    # Server
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8007

    # Compliance: Recommendation service processes global user behavior
    compliance_jurisdiction: Jurisdiction = Jurisdiction.GLOBAL
    compliance_additional_jurisdictions: list[Jurisdiction] = [
        Jurisdiction.EU,
        Jurisdiction.US,
        Jurisdiction.IN,
    ]
    compliance_dpo_email: str = "dpo@wildframe.com"
    compliance_grievance_officer_email: str = "grievance@wildframe.com"
    compliance_allowed_data_regions: list[str] = ["US", "EU", "IN", "SG"]

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
