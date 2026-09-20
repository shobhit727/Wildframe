from decimal import Decimal

from pydantic import model_validator
from pydantic_settings import BaseSettings

from app.core.money import validate_currency
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin

DEV_ENVIRONMENTS = {"", "development", "test"}

DEV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://postgres:password@localhost:5432/billing_db",
    "REDIS_URL": "redis://localhost:6379/0",
}

KNOWN_INSECURE_DB_CREDENTIALS = (
    "wildframe:password",
    "wildframe:wildframe_dev_password",
    "postgres:password",
)


class Settings(ComplianceSettingsMixin, BaseSettings):
    SERVICE_NAME: str = "Billing"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str | None = None
    REDIS_URL: str | None = None
    JWT_AUDIENCE: str = "wildframe-api"
    JWT_ISSUER: str = "wildframe-auth"
    JWT_ALGORITHM: str = "RS256"
    JWT_JWKS_URL: str = "http://auth-service:8000/.well-known/jwks.json"
    JWT_LEEWAY_SECONDS: int = 60
    JWT_EXPIRATION_MINUTES: int = 15
    DATABASE_POOL_SIZE: int = 5
    DATABASE_MAX_OVERFLOW: int = 5
    LOG_LEVEL: str = "INFO"
    CORS_ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "https://localhost:3000",
    ]
    CORS_ALLOW_CREDENTIALS: bool = True
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8008
    compliance_jurisdiction: Jurisdiction = Jurisdiction.GLOBAL
    compliance_additional_jurisdictions: list[Jurisdiction] = [
        Jurisdiction.EU,
        Jurisdiction.US,
        Jurisdiction.IN,
    ]
    compliance_dpo_email: str = "dpo@wildframe.com"
    compliance_grievance_officer_email: str = "grievance@wildframe.com"
    compliance_allowed_data_regions: list[str] = ["US", "EU", "IN", "SG"]
    CREATOR_SHARE_PERCENTAGE: Decimal = Decimal("0.55")
    CREATOR_POOL_PERCENTAGE: Decimal = Decimal("0.15")
    SVOD_MONTHLY_PRICE: Decimal = Decimal("7.99")
    MILESTONE_TRANCHE_PERCENTAGES: list[Decimal] = [
        Decimal("10.00"),
        Decimal("20.00"),
        Decimal("30.00"),
        Decimal("40.00"),
    ]
    DEFAULT_CURRENCY: str = "USD"
    STRIPE_API_KEY: str = "sk_test_default_change_me"
    STRIPE_WEBHOOK_SECRET: str = "whsec_default_change_me"
    STRIPE_SVOD_PRICE_ID: str = "price_default_svod"
    STRIPE_SUCCESS_URL: str = "https://wildframe.com/billing/success"
    STRIPE_CANCEL_URL: str = "https://wildframe.com/billing/cancel"
    CREATORS_SERVICE_URL: str = "http://creators-service:8000"
    AUTH_SERVICE_URL: str = "http://auth-service:8000"

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
        if self.STRIPE_API_KEY.startswith("sk_test_"):
            raise ValueError("STRIPE_API_KEY must be a live key in production")
        if self.STRIPE_WEBHOOK_SECRET.startswith("whsec_default"):
            raise ValueError("STRIPE_WEBHOOK_SECRET must be set in production")
        return self

    @model_validator(mode="after")
    def validate_currency(self) -> "Settings":
        validate_currency(self.DEFAULT_CURRENCY)
        return self

    @model_validator(mode="after")
    def validate_cors_credentials(self) -> "Settings":
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

    EMAIL_PROVIDER_DAILY_QUOTA: dict[str, int] = {}
    PIPELINE_MAX_JOBS_PER_CREATOR: int = 2
    CLOUDFRONT_DISTRIBUTION_ID: str | None = None
    METRICS_TOKEN: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
