"""Configuration settings for Api Gateway Service."""

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    SERVICE_NAME: str = "Api Gateway"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Database (api-gateway is stateless — no DB)

    # Security
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 15
    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # Logging
    LOG_LEVEL: str = "INFO"

    # CORS
    CORS_ALLOWED_ORIGINS: list[str] = ["*"]
    CORS_ALLOW_CREDENTIALS: bool = True

    # Upstream proxy: bounded timeouts, retry budget and connection limits.
    # Hierarchy: gateway connect (5s) < gateway read/write (30s) < proxy-side
    # overall deadline; the gateway never follows upstream redirects so a
    # redirect chain cannot outlive the bounded request.
    UPSTREAM_CONNECT_TIMEOUT: float = 5.0
    UPSTREAM_READ_TIMEOUT: float = 30.0
    UPSTREAM_WRITE_TIMEOUT: float = 30.0
    UPSTREAM_POOL_TIMEOUT: float = 5.0
    UPSTREAM_MAX_CONNECTIONS: int = 100
    UPSTREAM_MAX_KEEPALIVE: int = 20
    UPSTREAM_MAX_RETRIES: int = 2
    UPSTREAM_RETRY_BASE_DELAY: float = 0.1
    UPSTREAM_MAX_RETRY_DELAY: float = 0.5

    # Request/response hardening limits.
    MAX_REQUEST_BODY_SIZE: int = 5 * 1024 * 1024
    MAX_RESPONSE_BODY_SIZE: int = 10 * 1024 * 1024
    MAX_HEADER_COUNT: int = 100
    MAX_HEADER_FIELD_SIZE: int = 8192
    MAX_HEADER_TOTAL_SIZE: int = 64 * 1024
    MAX_DECOMPRESSION_RATIO: int = 10

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Fail fast if running in production with default insecure secrets.

        A wildcard CORS origin combined with credentials would let any site
        issue credentialed cross-origin requests, so production must configure
        an explicit origin allowlist. Validation happens at Settings
        construction, i.e. at process import: an unsafe production config
        prevents the gateway from starting at all.
        """
        default_secrets = [
            "your-secret-key-change-in-production",
            "dev-secret-key",
        ]
        unsafe_cors = self.CORS_ALLOW_CREDENTIALS and (
            "*" in self.CORS_ALLOWED_ORIGINS or not self.CORS_ALLOWED_ORIGINS
        )
        if self.ENVIRONMENT == "production" and (
            self.JWT_SECRET_KEY in default_secrets or unsafe_cors
        ):
            raise ValueError(
                "JWT_SECRET_KEY must be a strong secret and CORS_ALLOWED_ORIGINS must "
                "be an explicit origin list in production (wildcard origins with "
                "credentials are rejected). "
            )
        return self

    class Config:
        env_file = ".env"


settings = Settings()
