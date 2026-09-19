"""Configuration settings for the Uploads Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin

DEV_ENVIRONMENTS = {"", "development", "test"}

DEV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://postgres:password@localhost:5432/uploads_db",
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
    """Application settings.

    Twelve-factor: every value has a safe dev default and is overridable via
    environment variable (or a ``.env`` file loaded by pydantic-settings).
    """

    SERVICE_NAME: str = "uploads-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str | None = None
    JWT_SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 15
    JWT_ISSUER: str = "wildframe-auth"
    JWT_AUDIENCE: str = "wildframe-api"
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 5
    REDIS_URL: str | None = None
    KAFKA_BOOTSTRAP_SERVERS: str | None = None
    LOG_LEVEL: str = "INFO"
    CORS_ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "https://localhost:3000",
    ]
    CORS_ALLOW_CREDENTIALS: bool = True
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8014
    compliance_jurisdiction: Jurisdiction = Jurisdiction.GLOBAL
    compliance_additional_jurisdictions: list[Jurisdiction] = [
        Jurisdiction.EU,
        Jurisdiction.US,
        Jurisdiction.IN,
    ]
    compliance_dpo_email: str = "dpo@wildframe.com"
    compliance_grievance_officer_email: str = "grievance@wildframe.com"
    compliance_allowed_data_regions: list[str] = ["US", "EU", "IN", "SG"]
    DEFAULT_CHUNK_SIZE_BYTES: int = 5 * 1024 * 1024
    MAX_CHUNKS_PER_SESSION: int = 10_000
    MAX_UPLOAD_SIZE_BYTES: int = 10 * 1024 * 1024 * 1024
    ALLOWED_UPLOAD_MIME_TYPES: list[str] = [
        "video/mp4",
        "video/webm",
        "video/quicktime",
        "video/x-matroska",
        "audio/mpeg",
        "audio/aac",
        "audio/ogg",
        "audio/wav",
        "audio/flac",
        "audio/mp4",
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/avif",
        "image/heic",
        "image/heif",
        "application/pdf",
        "application/zip",
    ]
    CHECKSUM_VERIFY_MAX_BYTES: int = 512 * 1024 * 1024
    PRESIGNED_URL_MAX_TTL_SECONDS: int = 3600
    SESSION_EXPIRES_HOURS: int = 24
    OUTBOX_POLL_INTERVAL_SECONDS: int = 2
    REAPER_INTERVAL_SECONDS: int = 300
    OUTBOX_BATCH_SIZE: int = 100
    CLEANUP_RETRY_GRACE_SECONDS: int = 900
    STORAGE_BACKEND: str = "stub"
    EVENT_PUBLISHER: str = "memory"
    S3_REGION: str = "us-east-1"
    S3_BUCKET: str = "wildframe-uploads"
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_PRESIGNED_URL_TTL_SECONDS: int = 3600

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

    METRICS_TOKEN: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
