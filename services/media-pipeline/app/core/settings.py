"""Configuration settings for Media Pipeline Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin

DEV_ENVIRONMENTS = {"", "development", "test"}

DEV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://wildframe:wildframe_dev_password@localhost:5432/media_db",
    "REDIS_URL": "redis://localhost:6379",
    "JWT_SECRET_KEY": "dev-secret-key-change-in-production-min-32-bytes",
    "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
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

    model_config = SettingsConfigDict(env_file=".env")

    SERVICE_NAME: str = "Media Pipeline"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str | None = None
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
    SERVER_PORT: int = 8011
    compliance_jurisdiction: Jurisdiction = Jurisdiction.GLOBAL
    compliance_additional_jurisdictions: list[Jurisdiction] = [
        Jurisdiction.EU,
        Jurisdiction.US,
        Jurisdiction.IN,
    ]
    compliance_dpo_email: str = "dpo@wildframe.com"
    compliance_grievance_officer_email: str = "grievance@wildframe.com"
    compliance_allowed_data_regions: list[str] = ["US", "EU", "IN", "SG"]
    EVENT_PUBLISHER: str = "memory"
    KAFKA_BOOTSTRAP_SERVERS: str | None = None
    OUTBOX_BATCH_SIZE: int = 100
    OUTBOX_POLL_INTERVAL_SECONDS: int = 5
    PIPELINE_MAX_STAGE_ATTEMPTS: int = 3
    PIPELINE_BACKOFF_BASE_SECONDS: float = 1.0
    PIPELINE_BACKOFF_CAP_SECONDS: float = 30.0
    PIPELINE_WORK_ROOT: str = "/tmp/wildframe/work"
    PIPELINE_QUARANTINE_ROOT: str = "/tmp/wildframe/quarantine"
    PIPELINE_STAGE_TIMEOUT_SECONDS: float = 3600.0
    PIPELINE_MAX_TOTAL_RETRY_TIME_SECONDS: float = 7200.0
    PIPELINE_JOB_LEASE_SECONDS: float = 300.0
    PIPELINE_DISK_QUOTA_BYTES: int = 0
    PIPELINE_MAX_GLOBAL_JOBS: int = 0
    PIPELINE_MAX_JOBS_PER_CONTENT: int = 0
    PIPELINE_MAX_JOBS_PER_CREATOR: int = 2
    PIPELINE_CIRCUIT_BREAKER_THRESHOLD: int = 10
    PIPELINE_MAX_DURATION_SECONDS: float = 4 * 3600.0
    PIPELINE_MAX_OUTPUT_BYTES: int = 0
    PIPELINE_MAX_CPU_THREADS: int = 2
    PIPELINE_MAX_MEMORY_BYTES: int = 2 * 1024 * 1024 * 1024
    MEDIA_PIPELINE_ADAPTERS: str = "stub"
    FFMPEG_BIN: str = "ffmpeg"
    FFPROBE_BIN: str = "ffprobe"
    METRICS_TOKEN: str = ""
    CLOUDFRONT_DISTRIBUTION_ID: str | None = None

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
        if (
            not self.MEDIA_PIPELINE_ADAPTERS
            or self.MEDIA_PIPELINE_ADAPTERS.strip() == ""
            or self.MEDIA_PIPELINE_ADAPTERS.strip() == "stub"
        ):
            raise ValueError(
                "MEDIA_PIPELINE_ADAPTERS must be set to a production adapter "
                "(e.g. 'ffmpeg') when ENVIRONMENT is production; 'stub' is "
                "only allowed in development/test environments."
            )
        return self


settings = Settings()
