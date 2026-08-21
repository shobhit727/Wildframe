"""Configuration settings for the Billing Service (Sustenance Engine core).

Key settings:
  - CREATOR_SHARE_PERCENTAGE: contractual floor for creator share (>=55%)
  - CREATOR_POOL_PERCENTAGE: % of net revenue flowing to Creator Pool
  - MILESTONE_TRANCHE_PERCENTAGES: 10/20/30/40 split
  - MAX_STAGE_ATTEMPTS: kill threshold for stalled milestones
"""

from decimal import Decimal

from pydantic import model_validator
from pydantic_settings import BaseSettings

from app.core.money import validate_currency


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    SERVICE_NAME: str = "Billing"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/billing_db"
    REDIS_URL: str = "redis://localhost:6379/0"

    # Security
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_ISSUER: str = "wildframe-auth"
    JWT_ALGORITHM: str = "HS256"
    JWT_SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_EXPIRATION_MINUTES: int = 15

    # Database pool budget (#64/#129): pool_size=5, max_overflow=5 limits
    # connections per service instance to prevent DB exhaustion.
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 5

    # Logging
    LOG_LEVEL: str = "INFO"

    # CORS
    CORS_ALLOWED_ORIGINS: list[str] = ["*"]
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
    MILESTONE_TRANCHE_PERCENTAGES: list[Decimal] = [
        Decimal("10.00"),
        Decimal("20.00"),
        Decimal("30.00"),
        Decimal("40.00"),
    ]

    # Default currency for payouts.
    DEFAULT_CURRENCY: str = "USD"

    # -----------------------------------------------------------------------
    # Stripe Connect integration
    # -----------------------------------------------------------------------

    # Stripe secret key (sk_live_... or sk_test_...).
    STRIPE_API_KEY: str = "sk_test_default_change_me"

    # Stripe webhook signing secret (whsec_...).
    STRIPE_WEBHOOK_SECRET: str = "whsec_default_change_me"

    # Stripe Price ID for the SVOD $7.99/mo subscription.
    STRIPE_SVOD_PRICE_ID: str = "price_default_svod"

    # Redirect URLs after Stripe Checkout completes or is cancelled.
    STRIPE_SUCCESS_URL: str = "https://wildframe.com/billing/success"
    STRIPE_CANCEL_URL: str = "https://wildframe.com/billing/cancel"

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Fail fast if running in production with default insecure secrets."""
        if self.ENVIRONMENT == "production":
            if self.STRIPE_API_KEY.startswith("sk_test_"):
                raise ValueError("STRIPE_API_KEY must be a live key in production")
            if self.STRIPE_WEBHOOK_SECRET.startswith("whsec_default"):
                raise ValueError("STRIPE_WEBHOOK_SECRET must be set in production")
            if self.JWT_SECRET_KEY == "your-secret-key-change-in-production":
                raise ValueError("JWT_SECRET_KEY must be set in production")
        return self

    @model_validator(mode="after")
    def validate_currency(self) -> "Settings":
        """Validate DEFAULT_CURRENCY against ISO-4217 allowlist (#477/#478)."""
        validate_currency(self.DEFAULT_CURRENCY)
        return self

    @model_validator(mode="after")
    def validate_cors_credentials(self) -> "Settings":
        """Reject wildcard CORS with credentials in production (#68)."""
        if (
            self.ENVIRONMENT == "production"
            and self.CORS_ALLOWED_ORIGINS == ["*"]
            and self.CORS_ALLOW_CREDENTIALS
        ):
            raise ValueError(
                "CORS_ALLOWED_ORIGINS cannot be ['*'] with CORS_ALLOW_CREDENTIALS=True in production. "
                "Use explicit origin list or disable credentials."
            )
        return self

    # Issue #319: Per-provider daily email quota (for notification-service integration)
    EMAIL_PROVIDER_DAILY_QUOTA: dict[str, int] = {}
    # Issue #488/#545: Per-creator max concurrent jobs
    PIPELINE_MAX_JOBS_PER_CREATOR: int = 2
    # Issue #495: CloudFront distribution ID for CDN invalidation
    CLOUDFRONT_DISTRIBUTION_ID: str | None = None
    # Issue #469: Metrics endpoint token
    METRICS_TOKEN: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
