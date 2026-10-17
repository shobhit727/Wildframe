from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin

DEV_ENVIRONMENTS = {"", "development", "test"}

DEV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://wildframe:password@localhost:5432/admin_db",
    "REDIS_URL": "redis://localhost:6379/0",
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
    ADMIN_ROLE_VERSION: int = 0
    SERVICE_NAME: str = "admin-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8006
    DATABASE_URL: str | None = None
    REDIS_URL: str | None = None
    # Legacy compatibility field; JWT verification uses RS256/JWKS and never reads this secret.
    JWT_SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "RS256"
    JWT_JWKS_URL: str = "http://auth-service:8000/.well-known/jwks.json"
    AUTH_SERVICE_URL: str = "http://auth-service:8000"
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_ISSUER: str = "wildframe-auth"
    LOG_LEVEL: str = "INFO"
    METRICS_TOKEN: str = ""

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
    TRUST_PROXY: bool = False

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
        """Fail fast when running outside development with unsafe configuration.

        This validator is the whole of admin-service's production guard, and
        it is not backed by a second upstream layer. ``ComplianceSettingsMixin``
        supplies compliance configuration and helpers only: it defines no
        ``default_secrets`` list and runs no secret validation, so nothing
        here can be relied on to catch a value this validator misses.

        The checks actually performed are exactly the branches below:

        * ``DATABASE_URL`` is set and free of
          KNOWN_INSECURE_DB_CREDENTIALS;
        * ``REDIS_URL`` is set;
        * ``KAFKA_BOOTSTRAP_SERVERS`` is set.

        ``JWT_SECRET_KEY`` is deliberately *not* checked here. It is a legacy
        field: verification uses RS256/JWKS and never reads the secret, and
        ``test_legacy_jwt_secret_is_optional_under_rs256_jwks`` pins that a
        production config may leave it unset. KNOWN_INSECURE_JWT_SECRETS
        exists to identify the development default (and to keep tests honest),
        not as an enforced production check.

        If a rejection is ever added here, update this docstring in the same
        change -- an overstating docstring is what led reviewers to believe
        the local checks were redundant (#882).
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
        if self.KAFKA_BOOTSTRAP_SERVERS is None:
            raise ValueError(
                "KAFKA_BOOTSTRAP_SERVERS must be set explicitly when ENVIRONMENT is not development."
            )
        return self

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
