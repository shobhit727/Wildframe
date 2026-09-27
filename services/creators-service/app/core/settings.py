"""Configuration settings for Creators Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin

DEV_ENVIRONMENTS = {"", "development", "test"}

DEV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://postgres:password@localhost:5432/creators_db",
    "REDIS_URL": "redis://localhost:6379",
}

KNOWN_INSECURE_DB_CREDENTIALS = (
    "wildframe:password",
    "wildframe:wildframe_dev_password",
    "postgres:password",
)

class Settings(ComplianceSettingsMixin, BaseSettings):
    """Application settings."""

    SERVICE_NAME: str = "Creators"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str | None = None
    JWT_ALGORITHM: str = "RS256"
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_JWKS_URL: str = "http://auth-service:8001/.well-known/jwks.json"
    JWT_ISSUER: str = "wildframe-auth"
    JWT_EXPIRATION_MINUTES: int = 15
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 5
    REDIS_URL: str | None = None
    LOG_LEVEL: str = "INFO"
    CORS_ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "https://localhost:3000",
    ]
    CORS_ALLOW_CREDENTIALS: bool = True
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8012
    compliance_jurisdiction: Jurisdiction = Jurisdiction.GLOBAL
    compliance_additional_jurisdictions: list[Jurisdiction] = [
        Jurisdiction.EU,
        Jurisdiction.US,
        Jurisdiction.IN,
    ]
    compliance_dpo_email: str = "dpo@wildframe.com"
    compliance_grievance_officer_email: str = "grievance@wildframe.com"
    compliance_allowed_data_regions: list[str] = ["US", "EU", "IN", "SG"]
    POOL_RATE: float = 0.15
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    # Polling interval for the inbound event worker; configurable per environment.
    INBOUND_EVENT_POLL_INTERVAL_SECONDS: int = 30

    @model_validator(mode="before")
    @classmethod
    def _apply_development_defaults(cls, values: dict) -> dict:
        environment = values.get("ENVIRONMENT") or ""
        if environment in DEV_ENVIRONMENTS:
            for key, value in DEV_DEFAULTS.items():
                values.setdefault(key, value)
        return values

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.ENVIRONMENT in DEV_ENVIRONMENTS:
            return self
        if self.DATABASE_URL is None:
            raise ValueError(
                "DATABASE_URL must be set explicitly when ENVIRONMENT is not development."
            )
        if any(credential in self.DATABASE_URL for credential in KNOWN_INSECURE_DB_CREDENTIALS):
            raise ValueError("DATABASE_URL must not use known default credentials.")
        if self.REDIS_URL is None:
            raise ValueError(
                "REDIS_URL must be set explicitly when ENVIRONMENT is not development."
            )
        if self.JWT_ALGORITHM != "RS256":
            raise ValueError("JWT_ALGORITHM must be RS256.")
        if not self.JWT_JWKS_URL.strip():
            raise ValueError("JWT_JWKS_URL must be set explicitly when ENVIRONMENT is not development.")
        if not self.JWT_ISSUER.strip() or not self.JWT_AUDIENCE.strip():
            raise ValueError("JWT_ISSUER and JWT_AUDIENCE must be set explicitly when ENVIRONMENT is not development.")
        return self

    @model_validator(mode="after")
    def validate_cors_credentials(self) -> "Settings":
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


settings = Settings()
