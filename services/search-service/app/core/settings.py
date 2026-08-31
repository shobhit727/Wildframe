"""Configuration settings for Search Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin


class Settings(ComplianceSettingsMixin, BaseSettings):
    """Application settings."""

    SERVICE_NAME: str = "Search"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/search_db"

    # Security
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_ISSUER: str = "wildframe-auth"

    # Database pool budget (#64/#129): pool_size=5, max_overflow=5 limits
    # connections per service instance to prevent DB exhaustion.
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 5

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # Event bus adapter (memory for dev/test, kafka for production).
    # search-service consumes content.deleted / content.unpublished (#227)
    # to drop documents without waiting for a reindex; production MUST use
    # kafka or stale documents stay searchable.
    EVENT_PUBLISHER: str = "memory"
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"

    # Elasticsearch
    ELASTICSEARCH_URL: str = "http://elasticsearch:9200"

    # Content service (catalog backfill for indexing)
    CONTENT_SERVICE_URL: str = "http://content-service:8003"

    # Admin role version (#81/#101): bump in lockstep with ADMIN_EMAILS
    # changes so already-issued admin tokens (arv < this) are rejected.
    ADMIN_ROLE_VERSION: int = 0

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
    SERVER_PORT: int = 8005

    # Compliance: Search service indexes global content
    compliance_jurisdiction: Jurisdiction = Jurisdiction.GLOBAL
    compliance_additional_jurisdictions: list[Jurisdiction] = [Jurisdiction.EU, Jurisdiction.US, Jurisdiction.IN]
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

    class Config:
        env_file = ".env"


settings = Settings()