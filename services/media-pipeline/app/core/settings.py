"""Configuration settings for Media Pipeline Service."""
from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    """Application settings."""
    
    SERVICE_NAME: str = "Media Pipeline"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/media-pipeline_db"
    
    # Security
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 15
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # CORS
    CORS_ALLOWED_ORIGINS: List[str] = ["*"]
    CORS_ALLOW_CREDENTIALS: bool = True

    # Event bus: "memory" (default, no-op + log) or "kafka".
    EVENT_PUBLISHER: str = "memory"
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"

    # Pipeline retry / DLQ tuning.
    PIPELINE_MAX_STAGE_ATTEMPTS: int = 3
    PIPELINE_BACKOFF_BASE_SECONDS: float = 1.0
    PIPELINE_BACKOFF_CAP_SECONDS: float = 30.0

    class Config:
        env_file = ".env"

settings = Settings()
