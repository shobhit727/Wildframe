"""Configuration settings for the Billing Service (Sustenance Engine core).

Key settings:
  - CREATOR_SHARE_PERCENTAGE: contractual floor for creator share (>=55%)
  - CREATOR_POOL_PERCENTAGE: % of net revenue flowing to Creator Pool
  - MILESTONE_TRANCHE_PERCENTAGES: 10/20/30/40 split
  - MAX_STAGE_ATTEMPTS: kill threshold for stalled milestones
"""
from decimal import Decimal
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    SERVICE_NAME: str = "Billing"
    SERVICE_VERSION: str = "2.0.0"
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/billing_db"

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

    # Server
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8008

    # -----------------------------------------------------------------------
    # Sustenance Engine parameters (§2 + §3 of PRODUCT_VISION.md)
    # -----------------------------------------------------------------------

    # >=55% of net SVOD revenue goes to creators (contractual floor, not cap).
    CREATOR_SHARE_PERCENTAGE: Decimal = Decimal("0.55")

    # 15% of net revenue flows into the Creator Pool each payout cycle.
    CREATOR_POOL_PERCENTAGE: Decimal = Decimal("0.15")

    # SVOD subscription price.
    SVOD_MONTHLY_PRICE: Decimal = Decimal("7.99")

    # Milestone-tranched funding split (must sum to 100%).
    MILESTONE_TRANCHE_PERCENTAGES: List[Decimal] = [
        Decimal("10.00"),
        Decimal("20.00"),
        Decimal("30.00"),
        Decimal("40.00"),
    ]

    # Default currency for payouts.
    DEFAULT_CURRENCY: str = "USD"

    class Config:
        env_file = ".env"


settings = Settings()
