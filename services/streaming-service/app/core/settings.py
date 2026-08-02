"""Configuration for Streaming Service."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""

    SERVICE_NAME: str = "streaming-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/streaming_db"
    REDIS_URL: str = "redis://localhost:6379/1"
    JWT_SECRET_KEY: str = "dev-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    CORS_ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8004
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
