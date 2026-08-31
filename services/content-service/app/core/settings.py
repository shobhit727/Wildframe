"""
Configuration settings for Content Service.
Centralized environment-based configuration management.
"""

from pydantic import model_validator
from pydantic_settings import BaseSettings

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin


class Settings(ComplianceSettingsMixin, BaseSettings):
    # Admin role version (#81/#101): bump in lockstep with auth-service
    # ADMIN_EMAILS changes so already-issued admin tokens (arv < this)
    # are rejected at this service's admin boundary.
    ADMIN_ROLE_VERSION: int = 0
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
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_ISSUER: str = "wildframe-auth"

    # Database pool budget (#64/#129): pool_size=5, max_overflow=5 limits
    # connections per service instance to prevent DB exhaustion.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    REFRESH_TOKEN_EXPIRATION_DAYS: int = 7

    # CORS
    CORS_ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8000"]

    # Server
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8003

    # Compliance: Content service handles global catalog data
    compliance_jurisdiction: Jurisdiction = Jurisdiction.GLOBAL
    compliance_additional_jurisdictions: list[Jurisdiction] = [
        Jurisdiction.EU,
        Jurisdiction.US,
        Jurisdiction.IN,
    ]
    compliance_dpo_email: str = "dpo@wildframe.com"
    compliance_grievance_officer_email: str = "grievance@wildframe.com"
    compliance_allowed_data_regions: list[str] = ["US", "EU", "IN", "SG"]

    # Logging
    LOG_LEVEL: str = "INFO"

    # Event bus adapter (memory for dev/test, kafka for production).
    # content-service produces content.published / content.deleted /
    # content.unpublished (#227): consumers rely on those events to keep
    # their indexes in sync, so production MUST use kafka.
    EVENT_PUBLISHER: str = "memory"
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"

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
