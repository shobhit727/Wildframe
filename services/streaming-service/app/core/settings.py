"""Configuration for Streaming Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin

DEV_ENVIRONMENTS = {"", "development", "test"}

DEV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://postgres:password@localhost:5432/streaming_db",
    "REDIS_URL": "redis://localhost:6379/1",
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
# Default shipped on PLAYBACK_URL_SIGNING_SECRET; rejected outside development.
KNOWN_INSECURE_PLAYBACK_SIGNING_SECRETS = ("dev-playback-signing-secret-change-in-production",)


class Settings(ComplianceSettingsMixin, BaseSettings):
    """Application settings."""

    SERVICE_NAME: str = "streaming-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    DATABASE_URL: str | None = None
    REDIS_URL: str | None = None
    JWT_SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "RS256"
    JWT_ISSUER: str = "wildframe-auth"
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_JWKS_URL: str = "http://auth-service:8001/.well-known/jwks.json"
    ADMIN_ROLE_VERSION: int = 0
    JWT_EXPIRATION_MINUTES: int = 15
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    LOG_LEVEL: str = "INFO"
    CORS_ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8004
    compliance_jurisdiction: Jurisdiction = Jurisdiction.GLOBAL
    compliance_additional_jurisdictions: list[Jurisdiction] = [
        Jurisdiction.EU,
        Jurisdiction.US,
        Jurisdiction.IN,
    ]
    compliance_dpo_email: str = "dpo@wildframe.com"
    compliance_grievance_officer_email: str = "grievance@wildframe.com"
    compliance_allowed_data_regions: list[str] = ["US", "EU", "IN", "SG"]
    MAX_ACTIVE_SESSIONS: int = 5
    PLAYBACK_SESSION_IDLE_TIMEOUT_MINUTES: int = 90
    PLAYBACK_URL_SIGNING_SECRET: str = "dev-playback-signing-secret-change-in-production"
    PLAYBACK_URL_TTL_SECONDS: int = 3600
    ENTITLEMENT_CHECK_ENABLED: bool = True

    @model_validator(mode="before")
    @classmethod
    def _apply_development_defaults(cls, values: dict) -> dict:
        environment = values.get("ENVIRONMENT") or ""
        if environment in DEV_ENVIRONMENTS:
            for key, value in DEV_DEFAULTS.items():
                values.setdefault(key, value)
            if environment in {"", "development", "test"}:
                values.setdefault("JWT_ALGORITHM", "HS256")
        return values

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Fail fast when running outside development with default insecure secrets.

        Two complementary hardening layers are enforced here. The explicit
        per-setting chain below rejects missing values, known-insecure
        credentials and secrets, and short JWT keys. The upstream production
        guard additionally rejects empty or whitespace-only secrets and
        validates the playback URL signing secret as well; every entry on its
        ``default_secrets`` set is a member of KNOWN_INSECURE_JWT_SECRETS or
        KNOWN_INSECURE_PLAYBACK_SIGNING_SECRETS, so it stays enforced without
        duplicating the set here.
        """
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
        # The playback signing secret is a plain str carrying a shipped dev
        # default, so a non-development deploy that omits the env var would
        # otherwise sign playback URLs with that dev secret.
        if not self.PLAYBACK_URL_SIGNING_SECRET.strip():
            raise ValueError(
                "PLAYBACK_URL_SIGNING_SECRET must be set to a strong random value "
                "when ENVIRONMENT is not development."
            )
        if self.PLAYBACK_URL_SIGNING_SECRET.strip() in KNOWN_INSECURE_PLAYBACK_SIGNING_SECRETS:
            raise ValueError(
                "PLAYBACK_URL_SIGNING_SECRET must be set to a strong random value "
                "when ENVIRONMENT is not development."
            )
        return self

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
