"""Configuration settings for Analytics Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin


class Settings(ComplianceSettingsMixin, BaseSettings):
    """Application settings."""

    SERVICE_NAME: str = "Analytics"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/analytics_db"

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

    # Content ownership resolution (creator dashboard / content performance).
    # Set to http://content-service:8000 inside the docker network.
    CONTENT_SERVICE_URL: str = "http://content-service:8003"
    CONTENT_SERVICE_TIMEOUT_SECONDS: float = 3.0
    CONTENT_SERVICE_MAX_CONNECTIONS: int = 10

    # Role claims: only this role may access another user's analytics.
    PRIVILEGED_ROLE: str = "admin"

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
    SERVER_PORT: int = 8009

    # Compliance: Analytics service processes global user data
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

    # Issue #469: Metrics endpoint token
    METRICS_TOKEN: str = ""

    class Config:
        env_file = ".env"


settings = Settings()