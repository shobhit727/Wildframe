import os

# ponytail: no ``load_dotenv()`` here on purpose. python-dotenv's discovery
# walks *up* the tree, so importing this module used to pull the repo-root
# ``.env`` into ``os.environ`` process-wide, where ``app.core.settings`` then
# read it as an explicitly-set value and never applied DEV_DEFAULTS.


DATABASE_URL = os.getenv(
    "ADMIN_DATABASE_URL", "postgresql+asyncpg://admin:admin@localhost:5432/admin_db"
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"
