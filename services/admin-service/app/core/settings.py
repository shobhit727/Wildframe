from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin


class Settings(ComplianceSettingsMixin, BaseSettings):
    # Admin role version (#81/#101): bump in lockstep with auth-service
    # ADMIN_EMAILS changes so already-issued admin tokens (arv < this)
    # are rejected at this service's admin boundary.
    ADMIN_ROLE_VERSION: int = 0
    SERVICE_NAME: str = "admin-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8006
    DATABASE_URL: str = "postgresql+asyncpg://wildframe:password@localhost:5432/admin_db"
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET_KEY: str = "dev-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_ISSUER: str = "wildframe-auth"
    LOG_LEVEL: str = "INFO"

    # Compliance: Admin service has global admin access
    compliance_jurisdiction: Jurisdiction = Jurisdiction.GLOBAL
    compliance_additional_jurisdictions: list[Jurisdiction] = [Jurisdiction.EU, Jurisdiction.US, Jurisdiction.IN]
    compliance_dpo_email: str = "dpo@wildframe.com"
    compliance_grievance_officer_email: str = "grievance@wildframe.com"
    compliance_allowed_data_regions: list[str] = ["US", "EU", "IN", "SG"]

    # Event bus: "memory" (default, no-op + log) or "kafka".
    EVENT_PUBLISHER: str = "memory"
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    # When True, the first X-Forwarded-For hop is trusted for audit IPs.
    # Keep False unless this service only sits behind a trusted proxy.
    TRUST_PROXY: bool = False

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

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()