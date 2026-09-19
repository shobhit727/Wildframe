"""Configuration settings for Recommendation Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin

DEV_ENVIRONMENTS = {"", "development", "test"}

DEV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://postgres:password@localhost:5432/recommendation_db",
    "REDIS_URL": "redis://localhost:6379",
    "JWT_SECRET_KEY": "dev-secret-key-change-in-production-min-32-bytes",
    "KAFKA_BOOTSTRAP_SERVERS": "kafka:29092",
}

KNOWN_INSECURE_DB_CREDENTIALS = (
    "wildframe:password",
    "wildframe:wildframe_dev_password",
    "postgres:password",
)
KNOWN_INSECURE_JWT_SECRETS = (
    "dev-secret-key",
    "dev-secret-key-change-in-production",
    "dev-secret-key-change-in-production-min-32-bytes",
    "your-secret-key-change-in-production",
    "secret",
    "changeme",
)


class Settings(ComplianceSettingsMixin, BaseSettings):
    """Application settings."""

    SERVICE_NAME: str = "Recommendation"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str | None = None
    CONTENT_SERVICE_URL: str = "http://content-service:8003"
    CONTENT_CATALOG_TIMEOUT_SECONDS: float = 10.0
    CONTENT_CATALOG_MAX_CONNECTIONS: int = 20
    CONTENT_CATALOG_MAX_KEEPALIVE: int = 10
    MAX_RECOMMENDATION_LIMIT: int = 100
    MAX_PREFERENCE_GENRES: int = 50
    MAX_CANDIDATES: int = 500
    MAX_CATALOG_PAGE_SIZE: int = 100
    EVENT_PUBLISHER: str = "memory"
    KAFKA_BOOTSTRAP_SERVERS: str | None = None
    KAFKA_CONSUMER_GROUP: str = "recommendation-service"
    JWT_SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "HS256"
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_ISSUER: str = "wildframe-auth"
    JWT_EXPIRATION_MINUTES: int = 15
    REDIS_URL: str | None = None
    LOG_LEVEL: str = "INFO"
    CORS_ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "https://localhost:3000",
    ]
    CORS_ALLOW_CREDENTIALS: bool = True
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8007
    compliance_jurisdiction: Jurisdiction = Jurisdiction.GLOBAL
    compliance_additional_jurisdictions: list[Jurisdiction] = [
        Jurisdiction.EU,
        Jurisdiction.US,
        Jurisdiction.IN,
    ]
    compliance_dpo_email: str = "dpo@wildframe.com"
    compliance_grievance_officer_email: str = "grievance@wildframe.com"
    compliance_allowed_data_regions: list[str] = ["US", "EU", "IN", "SG"]

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
        if self.JWT_SECRET_KEY is None:
            raise ValueError(
                "JWT_SECRET_KEY must be set to a strong random value when ENVIRONMENT is not development."
            )
        if self.JWT_SECRET_KEY in KNOWN_INSECURE_JWT_SECRETS:
            raise ValueError(
                "JWT_SECRET_KEY must be set to a strong random value when ENVIRONMENT is not development."
            )
        if len(self.JWT_SECRET_KEY) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 characters long when ENVIRONMENT is not development."
            )
        if self.KAFKA_BOOTSTRAP_SERVERS is None:
            raise ValueError(
                "KAFKA_BOOTSTRAP_SERVERS must be set explicitly when ENVIRONMENT is not development."
            )
        return self

    METRICS_TOKEN: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
