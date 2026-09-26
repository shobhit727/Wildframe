from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin

DEV_ENVIRONMENTS = {"", "development", "test"}

DEV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://wildframe:password@localhost:5432/auth_db",
    "REDIS_URL": "redis://localhost:6379/0",
    "JWT_SECRET_KEY": "dev-secret-key-change-in-production-min-32-bytes",
    "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
}

KNOWN_INSECURE_DB_CREDENTIALS = ("wildframe:password", "wildframe:wildframe_dev_password")
KNOWN_INSECURE_JWT_SECRETS = (
    "dev-secret-key",
    "dev-secret-key-change-in-production-min-32-bytes",
    "your-secret-key-change-in-production",
    "secret",
    "changeme",
)


class Settings(ComplianceSettingsMixin, BaseSettings):
    SERVICE_NAME: str = "auth-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    DATABASE_URL: str | None = None
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 5
    REDIS_URL: str | None = None
    REDIS_TIMEOUT: int = 5
    JWT_SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "RS256"
    JWT_ISSUER: str = "wildframe-auth"
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_LEEWAY_SECONDS: int = 60
    JWT_KEY_ID: str = "k1"
    JWT_PREVIOUS_SECRETS: str = ""
    JWT_PRIVATE_KEY: str | None = None
    JWT_PRIVATE_KEY_FILE: str | None = None
    JWT_PUBLIC_KEY: str | None = None
    JWT_PREVIOUS_JWKS: str = ""
    JWT_EXPIRATION_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRATION_DAYS: int = 7
    TOKEN_BLACKLIST_ENABLED: bool = True
    ADMIN_EMAILS: str = ""
    ADMIN_ROLE_VERSION: int = 0
    KAFKA_BOOTSTRAP_SERVERS: str | None = None
    KAFKA_GROUP_ID: str = "auth-service"
    KAFKA_TOPIC_USER_CREATED: str = "user.registered"
    KAFKA_TOPIC_USER_LOGIN: str = "user.login"
    KAFKA_TOPIC_TOKEN_REVOKED: str = "token.revoked"
    PASSWORD_MIN_LENGTH: int = 8
    PASSWORD_REQUIRE_UPPERCASE: bool = True
    PASSWORD_REQUIRE_DIGITS: bool = True
    PASSWORD_REQUIRE_SPECIAL: bool = True
    PASSWORD_BCRYPT_ROUNDS: int = 12
    RATE_LIMIT_ENABLED: bool = True
    LOGIN_RATE_LIMIT_ATTEMPTS: int = 5
    LOGIN_RATE_LIMIT_WINDOW: int = 900
    REGISTRATION_RATE_LIMIT_ATTEMPTS: int = 3
    REGISTRATION_RATE_LIMIT_WINDOW: int = 3600
    MFA_SETUP_RATE_LIMIT_ATTEMPTS: int = 5
    MFA_SETUP_RATE_LIMIT_WINDOW: int = 3600
    MFA_VERIFY_RATE_LIMIT_ATTEMPTS: int = 10
    MFA_VERIFY_RATE_LIMIT_WINDOW: int = 900
    MFA_DISABLE_RATE_LIMIT_ATTEMPTS: int = 5
    MFA_DISABLE_RATE_LIMIT_WINDOW: int = 3600
    MFA_LOGIN_VERIFY_RATE_LIMIT_ATTEMPTS: int = 10
    MFA_LOGIN_VERIFY_RATE_LIMIT_WINDOW: int = 900
    EMAIL_VERIFY_RATE_LIMIT_ATTEMPTS: int = 10
    EMAIL_VERIFY_RATE_LIMIT_WINDOW: int = 3600
    STEP_UP_EXPIRATION_MINUTES: int = 5
    STEP_UP_RATE_LIMIT_ATTEMPTS: int = 5
    STEP_UP_RATE_LIMIT_WINDOW: int = 900
    CORS_ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:3001"]
    CORS_ALLOW_CREDENTIALS: bool = True
    LOG_LEVEL: str = "INFO"
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8001
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
    JAEGER_ENABLED: bool = False
    JAEGER_AGENT_HOST: str = "localhost"
    JAEGER_AGENT_PORT: int = 6831
    JAEGER_SERVICE_NAME: str = "auth-service"
    EMAIL_VERIFICATION_ENABLED: bool = True
    EMAIL_VERIFICATION_EXPIRATION_HOURS: int = 24
    MFA_ENABLED: bool = True
    MFA_ISSUER_NAME: str = "Wildframe"
    MFA_CHALLENGE_EXPIRATION_MINUTES: int = 5
    MFA_BACKUP_CODES_COUNT: int = 10
    MFA_BACKUP_CODE_LENGTH: int = 8
    MFA_ENCRYPTION_KEY: str = ""
    MFA_ENCRYPTION_KEY_PREVIOUS: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _apply_development_defaults(cls, values: dict) -> dict:
        environment = values.get("ENVIRONMENT") or ""
        if environment in DEV_ENVIRONMENTS:
            for key, value in DEV_DEFAULTS.items():
                values.setdefault(key, value)
        # Prefer an explicitly generated local key file so dev reloads do not
        # rotate the auth signing key and invalidate unrelated test sessions.
        if not values.get("JWT_PRIVATE_KEY") and values.get("JWT_PRIVATE_KEY_FILE"):
            key_path = Path(str(values["JWT_PRIVATE_KEY_FILE"]))
            if key_path.is_file():
                values["JWT_PRIVATE_KEY"] = key_path.read_text(encoding="utf-8")
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
        if self.JWT_PRIVATE_KEY is None:
            raise ValueError(
                "JWT_PRIVATE_KEY must be set to a PEM private key when ENVIRONMENT is not development."
            )
        if "BEGIN PRIVATE KEY" not in self.JWT_PRIVATE_KEY:
            raise ValueError("JWT_PRIVATE_KEY must be a PEM private key")
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
