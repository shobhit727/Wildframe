"""Configuration settings for the Uploads Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings.

    Twelve-factor: every value has a safe dev default and is overridable via
    environment variable (or a ``.env`` file loaded by pydantic-settings).
    """

    SERVICE_NAME: str = "uploads-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/uploads_db"

    # Security
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 15
    # Audience claim carried by auth-service-issued tokens; every decode must
    # check it so tokens minted for other audiences are rejected.
    JWT_ISSUER: str = "wildframe-auth"

    # Database pool budget (#64/#129): pool_size=5, max_overflow=5 limits
    # connections per service instance to prevent DB exhaustion.
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 5

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # Logging
    LOG_LEVEL: str = "INFO"

    # CORS
    CORS_ALLOWED_ORIGINS: list[str] = ["*"]
    CORS_ALLOW_CREDENTIALS: bool = True

    # Upload tuning
    # Default chunk size in bytes (5 MiB is the S3 multi-part minimum; 5 MiB keeps
    # memory pressure low on the client while still amortizing request overhead).
    DEFAULT_CHUNK_SIZE_BYTES: int = 5 * 1024 * 1024
    # A single session may not exceed this many chunks (bounds bookkeeping).
    MAX_CHUNKS_PER_SESSION: int = 10_000
    # Hard ceiling for a single upload (file size limit, enforced server-side).
    MAX_UPLOAD_SIZE_BYTES: int = 10 * 1024 * 1024 * 1024
    # Media types a session may declare. Anything else is rejected at session
    # creation; completion re-verifies the stored object's content type.
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
    # Ceiling for server-side SHA-256 verification of the assembled object
    # (bytes). Larger objects rely on storage-provider integrity metadata
    # (multipart ETags) and record no verified checksum.
    CHECKSUM_VERIFY_MAX_BYTES: int = 512 * 1024 * 1024
    # Hard cap on pre-signed URL lifetime: endpoint-specific TTLs must never be
    # configurable to indefinite lifetimes.
    PRESIGNED_URL_MAX_TTL_SECONDS: int = 3600
    # How long an initiated/ uploading session stays alive before it is considered
    # stale and safe to reap (hours).
    SESSION_EXPIRES_HOURS: int = 24
    # Outbox drain + expiry reaper cadence (seconds).
    OUTBOX_POLL_INTERVAL_SECONDS: int = 2
    REAPER_INTERVAL_SECONDS: int = 300
    OUTBOX_BATCH_SIZE: int = 100
    # Grace period before the reaper retries storage cleanup of aborted sessions
    # whose first cleanup attempt failed (seconds).
    CLEANUP_RETRY_GRACE_SECONDS: int = 900

    # Storage / event-bus adapters.
    # ``storage_backend`` selects the pre-signed-URL provider; ``event_publisher``
    # selects the event-bus publisher. Both default to safe no-op/in-memory
    # implementations so the service boots and tests run with no external
    # dependencies. Swap to ``s3`` / ``kafka`` in production via env.
    STORAGE_BACKEND: str = "stub"
    EVENT_PUBLISHER: str = "memory"

    # S3 settings (only used when STORAGE_BACKEND=s3).
    S3_REGION: str = "us-east-1"
    S3_BUCKET: str = "wildframe-uploads"
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    # Pre-signed URL lifetime in seconds (clamped to PRESIGNED_URL_MAX_TTL_SECONDS).
    S3_PRESIGNED_URL_TTL_SECONDS: int = 3600

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
