from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SERVICE_NAME: str = "admin-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8006
    DATABASE_URL: str = "postgresql+asyncpg://wildframe:password@localhost:5432/admin_db"
    JWT_SECRET_KEY: str = "dev-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    LOG_LEVEL: str = "INFO"
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
