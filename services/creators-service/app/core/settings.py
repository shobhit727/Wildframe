"""Configuration settings for Creators Service."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    """Application settings."""

    SERVICE_NAME: str = "Creators"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/creators_db"

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

    # Creator Pool
    # Fraction of net revenue that flows into the Creator Pool each cycle.
    # Trade-off: higher pool_rate lifts floors faster but reduces the immediate
    # share to top earners; 0.15 is the charter default (PRODUCT_VISION §2.2).
    POOL_RATE: float = 0.15

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
