"""Configuration settings for Media Pipeline Service."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(env_file=".env")

    SERVICE_NAME: str = "Media Pipeline"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://wildframe:wildframe_dev_password@localhost:5432/media_db"
    )

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
    # Logging
    LOG_LEVEL: str = "INFO"

    # CORS
    CORS_ALLOWED_ORIGINS: list[str] = ["*"]
    CORS_ALLOW_CREDENTIALS: bool = True

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


    # Transactional outbox
    # Events are written to the DB in the same transaction as their business
    # state; a background worker publishes PENDING rows in batches and marks
    # them dispatched. At-least-once delivery, consumers dedupe on event_key.
    OUTBOX_BATCH_SIZE: int = 100
    OUTBOX_POLL_INTERVAL_SECONDS: int = 5

    # Pipeline retry / DLQ tuning.
    PIPELINE_MAX_STAGE_ATTEMPTS: int = 3
    PIPELINE_BACKOFF_BASE_SECONDS: float = 1.0
    PIPELINE_BACKOFF_CAP_SECONDS: float = 30.0

    # Pipeline hardening / resource limits (additive, generous defaults so
    # existing tests with stubs are unaffected).
    PIPELINE_WORK_ROOT: str = "/tmp/wildframe/work"
    PIPELINE_QUARANTINE_ROOT: str = "/tmp/wildframe/quarantine"
    PIPELINE_STAGE_TIMEOUT_SECONDS: float = 3600.0
    PIPELINE_MAX_TOTAL_RETRY_TIME_SECONDS: float = 7200.0
    PIPELINE_JOB_LEASE_SECONDS: float = 300.0
    PIPELINE_DISK_QUOTA_BYTES: int = 0  # 0 = unlimited
    PIPELINE_MAX_GLOBAL_JOBS: int = 0  # 0 = unlimited
    PIPELINE_MAX_JOBS_PER_CONTENT: int = 0  # 0 = unlimited
    PIPELINE_CIRCUIT_BREAKER_THRESHOLD: int = 10
    PIPELINE_MAX_DURATION_SECONDS: float = 4 * 3600.0
    PIPELINE_MAX_OUTPUT_BYTES: int = 0  # 0 = unlimited (per rendition)
    PIPELINE_MAX_CPU_THREADS: int = 2
    # Per-job address-space cap for ffmpeg/ffprobe children (bytes); enforced
    # via RLIMIT_AS in the child before exec. 0 = unlimited.
    PIPELINE_MAX_MEMORY_BYTES: int = 2 * 1024 * 1024 * 1024  # 2 GiB

    # Adapter selection: "stub" (default, no binaries) or "ffmpeg" (real
    # hardened subprocess adapters). Controlled via env for prod vs test.
    MEDIA_PIPELINE_ADAPTERS: str = "stub"
    FFMPEG_BIN: str = "ffmpeg"
    FFPROBE_BIN: str = "ffprobe"


settings = Settings()
