"""Configuration settings for Api Gateway Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin

DEV_ENVIRONMENTS = {"", "development", "test"}

DEV_DEFAULTS = {
    "REDIS_URL": "redis://localhost:6379",
    "JWT_SECRET_KEY": "dev-secret-key-change-in-production-min-32-bytes",
}

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

    SERVICE_NAME: str = "Api Gateway"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    JWT_SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 15
    REDIS_URL: str | None = None
    LOG_LEVEL: str = "INFO"
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000
    compliance_jurisdiction: Jurisdiction = Jurisdiction.GLOBAL
    compliance_additional_jurisdictions: list[Jurisdiction] = [
        Jurisdiction.EU,
        Jurisdiction.US,
        Jurisdiction.IN,
    ]
    compliance_dpo_email: str = "dpo@wildframe.com"
    compliance_grievance_officer_email: str = "grievance@wildframe.com"
    compliance_allowed_data_regions: list[str] = ["US", "EU", "IN", "SG"]
    CORS_ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "https://localhost:3000",
    ]
    CORS_ALLOW_CREDENTIALS: bool = True
    UPSTREAM_CONNECT_TIMEOUT: float = 5.0
    UPSTREAM_READ_TIMEOUT: float = 30.0
    UPSTREAM_WRITE_TIMEOUT: float = 30.0
    UPSTREAM_POOL_TIMEOUT: float = 5.0
    UPSTREAM_MAX_CONNECTIONS: int = 100
    UPSTREAM_MAX_KEEPALIVE: int = 20
    UPSTREAM_MAX_RETRIES: int = 2
    UPSTREAM_RETRY_BASE_DELAY: float = 0.1
    UPSTREAM_MAX_RETRY_DELAY: float = 0.5
    MAX_REQUEST_BODY_SIZE: int = 5 * 1024 * 1024
    MAX_RESPONSE_BODY_SIZE: int = 10 * 1024 * 1024
    MAX_HEADER_COUNT: int = 100
    MAX_HEADER_FIELD_SIZE: int = 8192
    MAX_HEADER_TOTAL_SIZE: int = 64 * 1024
    MAX_DECOMPRESSION_RATIO: int = 10
    RATE_LIMIT_AUTH: int = 5
    RATE_LIMIT_SEARCH: int = 100
    RATE_LIMIT_UPLOAD_CREATE: int = 100
    RATE_LIMIT_UPLOAD_FINALIZE: int = 60
    RATE_LIMIT_REINDEX: int = 20
    RATE_LIMIT_DEFAULT: int = 1000
    RATE_LIMIT_BURST_WINDOW: int = 10
    RATE_LIMIT_CONCURRENCY_WINDOW: int = 5
    RATE_LIMIT_BURST_AUTH: int = 10
    RATE_LIMIT_BURST_SEARCH: int = 20
    RATE_LIMIT_BURST_UPLOAD_CREATE: int = 10
    RATE_LIMIT_BURST_UPLOAD_FINALIZE: int = 10
    RATE_LIMIT_BURST_REINDEX: int = 5
    RATE_LIMIT_BURST_DEFAULT: int = 50
    RATE_LIMIT_CONCURRENCY_AUTH: int = 5
    RATE_LIMIT_CONCURRENCY_SEARCH: int = 5
    RATE_LIMIT_CONCURRENCY_UPLOAD_CREATE: int = 3
    RATE_LIMIT_CONCURRENCY_UPLOAD_FINALIZE: int = 2
    RATE_LIMIT_CONCURRENCY_REINDEX: int = 2
    RATE_LIMIT_CONCURRENCY_DEFAULT: int = 20
    TRUST_PROXY: bool = False
    TRUSTED_PROXIES: str = ""

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
        unsafe_cors = self.CORS_ALLOW_CREDENTIALS and (
            "*" in self.CORS_ALLOWED_ORIGINS or not self.CORS_ALLOWED_ORIGINS
        )
        if unsafe_cors:
            raise ValueError(
                "JWT_SECRET_KEY must be a strong secret and CORS_ALLOWED_ORIGINS must "
                "be an explicit origin list in production (wildcard origins with "
                "credentials are rejected). "
            )
        return self

    class Config:
        env_file = ".env"


settings = Settings()
