"""
Core configuration module for Auth Service.
Manages environment-based settings and dependency injection.
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings

DEV_ENVIRONMENTS = {"", "development", "test"}

# Convenient local-development defaults. Never applied in production; those
# settings must be provided explicitly via the environment.
DEV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://wildframe:password@localhost:5432/auth_db",
    "REDIS_URL": "redis://localhost:6379/0",
    "JWT_SECRET_KEY": "dev-secret-key-change-in-production-min-32-bytes",
    "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
}

# Values that are never acceptable for a production deployment, even when
# explicitly supplied through the environment.
KNOWN_INSECURE_DB_CREDENTIALS = ("wildframe:password", "wildframe:wildframe_dev_password")
KNOWN_INSECURE_JWT_SECRETS = (
    "dev-secret-key",
    "dev-secret-key-change-in-production-min-32-bytes",
    "your-secret-key-change-in-production",
    "secret",
    "changeme",
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Service Configuration
    SERVICE_NAME: str = "auth-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False

    # Database Configuration
    DATABASE_URL: str | None = None
    # Database pool budget (#64/#129): pool_size=5, max_overflow=5 limits
    # connections per service instance to prevent DB exhaustion.
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 5

    # Redis Configuration

    # Redis Configuration
    REDIS_URL: str | None = None
    REDIS_TIMEOUT: int = 5

    # JWT Configuration
    JWT_SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "wildframe-auth"
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_LEEWAY_SECONDS: int = 60
    # Key rotation (#138/#442): kid stamped into minted tokens; previous
    # secrets (comma-separated) stay verifiable for a bounded overlap window.
    # Emergency revocation = remove the secret from this list and redeploy.
    JWT_KEY_ID: str = "k1"
    JWT_PREVIOUS_SECRETS: str = ""
    JWT_EXPIRATION_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRATION_DAYS: int = 7
    TOKEN_BLACKLIST_ENABLED: bool = True

    # Admin roles — comma-separated emails whose tokens/me carry role "admin".
    ADMIN_EMAILS: str = ""

    # Admin role version (#81/#101): bump this when ADMIN_EMAILS changes so
    # access tokens minted before the change (arv < this value) are rejected
    # by privileged endpoints instead of outliving the revocation.
    ADMIN_ROLE_VERSION: int = 0

    # Kafka Configuration
    KAFKA_BOOTSTRAP_SERVERS: str | None = None
    KAFKA_GROUP_ID: str = "auth-service"
    KAFKA_TOPIC_USER_CREATED: str = "user.registered"
    KAFKA_TOPIC_USER_LOGIN: str = "user.login"
    KAFKA_TOPIC_TOKEN_REVOKED: str = "token.revoked"

    # Security Configuration
    PASSWORD_MIN_LENGTH: int = 8
    PASSWORD_REQUIRE_UPPERCASE: bool = True
    PASSWORD_REQUIRE_DIGITS: bool = True
    PASSWORD_REQUIRE_SPECIAL: bool = True
    PASSWORD_BCRYPT_ROUNDS: int = 12

    # Rate Limiting Configuration
    RATE_LIMIT_ENABLED: bool = True
    LOGIN_RATE_LIMIT_ATTEMPTS: int = 5
    LOGIN_RATE_LIMIT_WINDOW: int = 900  # 15 minutes
    REGISTRATION_RATE_LIMIT_ATTEMPTS: int = 3
    REGISTRATION_RATE_LIMIT_WINDOW: int = 3600  # 1 hour
    # MFA rate limits (#241)
    MFA_SETUP_RATE_LIMIT_ATTEMPTS: int = 5
    MFA_SETUP_RATE_LIMIT_WINDOW: int = 3600
    MFA_VERIFY_RATE_LIMIT_ATTEMPTS: int = 10
    MFA_VERIFY_RATE_LIMIT_WINDOW: int = 900
    MFA_DISABLE_RATE_LIMIT_ATTEMPTS: int = 5
    MFA_DISABLE_RATE_LIMIT_WINDOW: int = 3600
    MFA_LOGIN_VERIFY_RATE_LIMIT_ATTEMPTS: int = 10
    MFA_LOGIN_VERIFY_RATE_LIMIT_WINDOW: int = 900
    # Email verification rate limit (#69/#140)
    EMAIL_VERIFY_RATE_LIMIT_ATTEMPTS: int = 10
    EMAIL_VERIFY_RATE_LIMIT_WINDOW: int = 3600

    # CORS Configuration
    CORS_ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:3001"]
    CORS_ALLOW_CREDENTIALS: bool = True

    # Observability Configuration
    LOG_LEVEL: str = "INFO"

    # Event bus: "memory" (default) or "kafka" (composed stack).
    EVENT_PUBLISHER: str = "memory"
    JAEGER_ENABLED: bool = False
    JAEGER_AGENT_HOST: str = "localhost"
    JAEGER_AGENT_PORT: int = 6831
    JAEGER_SERVICE_NAME: str = "auth-service"

    # Email Configuration
    EMAIL_VERIFICATION_ENABLED: bool = True
    EMAIL_VERIFICATION_EXPIRATION_HOURS: int = 24

    # MFA Configuration
    MFA_ENABLED: bool = True
    MFA_ISSUER_NAME: str = "Wildframe"
    MFA_CHALLENGE_EXPIRATION_MINUTES: int = 5
    MFA_BACKUP_CODES_COUNT: int = 10
    MFA_BACKUP_CODE_LENGTH: int = 8
    # Encryption key for at-rest TOTP secrets. Empty = derive from
    # JWT_SECRET_KEY (backward compatible with pre-keyring deployments).
    # MFA_ENCRYPTION_KEY_PREVIOUS lists retired keys so secrets encrypted
    # under an older key stay decryptable through ordinary key rotation
    # (a JWT secret rotation must not strand MFA enrollments).
    MFA_ENCRYPTION_KEY: str = ""
    MFA_ENCRYPTION_KEY_PREVIOUS: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _apply_development_defaults(cls, values: dict) -> dict:
        """Inject convenient defaults for local development only.

        Non-production environments keep zero-config local defaults. In
        production (and any environment that does not explicitly opt into
        development) the secrets remain unset so that validation can require
        explicit values instead of comparing against known defaults.
        """
        environment = values.get("ENVIRONMENT") or ""
        if environment in DEV_ENVIRONMENTS:
            for key, value in DEV_DEFAULTS.items():
                values.setdefault(key, value)
        return values

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Fail closed in production: require explicit, non-default secrets."""
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
                "JWT_SECRET_KEY must be set to a strong random value when "
                "ENVIRONMENT is not development."
            )
        if self.JWT_SECRET_KEY in KNOWN_INSECURE_JWT_SECRETS:
            raise ValueError(
                "JWT_SECRET_KEY must be set to a strong random value when "
                "ENVIRONMENT is not development."
            )
        if len(self.JWT_SECRET_KEY) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 characters long when "
                "ENVIRONMENT is not development."
            )

        if self.KAFKA_BOOTSTRAP_SERVERS is None:
            raise ValueError(
                "KAFKA_BOOTSTRAP_SERVERS must be set explicitly when "
                "ENVIRONMENT is not development."
            )
        return self

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance.

    Returns:
        Settings: Cached application settings
    """
    return Settings()


# Global settings instance
settings = get_settings()
