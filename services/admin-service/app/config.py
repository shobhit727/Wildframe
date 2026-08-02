import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "ADMIN_DATABASE_URL", "postgresql+asyncpg://admin:admin@localhost:5432/admin_db"
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"
