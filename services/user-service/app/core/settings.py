"""Core configuration for User Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin

DEV_ENVIRONMENTS = {"", "development", "test"}

DEV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://wildframe:wildframe_dev_password@localhost:5432/users_db",
    "REDIS_URL": "redis://localhost:6379/0",
    "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
    "JWT_SECRET_KEY": "dev-secret-key-change-in-production-min-32-bytes",
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

    SERVICE_NAME: str = "user-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    DATABASE_URL: str | None = None
    REDIS_URL: str | None = None
    KAFKA_BOOTSTRAP_SERVERS: str | None = None
    KAFKA_CONSUMER_GROUP: str = "user-service"
    AUTH_SERVICE_URL: str = "http://auth-service:8000"
    JWT_SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "RS256"
    JWT_ISSUER: str = "wildframe-auth"
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_JWKS_URL: str = "http://auth-service:8001/.well-known/jwks.json"
    JWT_KEY_ID: str = "k1"
    JWT_ISSUER: str = "wildframe-auth"
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_EXPIRATION_MINUTES: int = 15
    PASSWORD_BCRYPT_ROUNDS: int = 12
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 5
    CORS_ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "https://wildframe.com",
    ]
    CORS_ALLOW_CREDENTIALS: bool = True
    LOG_LEVEL: str = "INFO"
    JAEGER_ENABLED: bool = True
    JAEGER_AGENT_HOST: str = "localhost"
    JAEGER_AGENT_PORT: int = 6831
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8002
    compliance_jurisdiction: Jurisdiction = Jurisdiction.GLOBAL
    compliance_additional_jurisdictions: list[Jurisdiction] = [
        Jurisdiction.EU,
        Jurisdiction.US,
        Jurisdiction.IN,
    ]
    compliance_dpo_email: str = "dpo@wildframe.com"
    compliance_grievance_officer_email: str = "grievance@wildframe.com"
    compliance_allowed_data_regions: list[str] = ["US", "EU", "IN", "SG"]
    LOGIN_RATE_LIMIT_ATTEMPTS: int = 10
    LOGIN_RATE_LIMIT_WINDOW: int = 900

    @model_validator(mode="before")
    @classmethod
    def _apply_development_defaults(cls, values: dict) -> dict:
        environment = values.get("ENVIRONMENT") or ""
        if environment in DEV_ENVIRONMENTS:
            for key, value in DEV_DEFAULTS.items():
                values.setdefault(key, value)
            if environment == "test":
                values.setdefault("JWT_ALGORITHM", "HS256")
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
        if self.KAFKA_BOOTSTRAP_SERVERS is None:
            raise ValueError(
                "KAFKA_BOOTSTRAP_SERVERS must be set explicitly when ENVIRONMENT is not development."
            )
        return self

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
