"""Database connection management."""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, QueuePool

from app.core.settings import settings


class DatabaseManager:
    """Manages database connections."""

    engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None

    @classmethod
    async def init(cls) -> None:
        """Initialize database."""
        # Use NullPool for SQLite (in-memory tests), QueuePool for PostgreSQL
        if settings.DATABASE_URL.startswith("sqlite"):
            poolclass: type[NullPool | QueuePool] = NullPool
            pool_kwargs = {}
        else:
            poolclass = QueuePool
            pool_kwargs = {
                "pool_size": 5,
                "max_overflow": 5,
                "pool_timeout": 30,
                "pool_recycle": 3600,
                "pool_pre_ping": True,
            }

        cls.engine = create_async_engine(
            settings.DATABASE_URL,
            echo=False,
            future=True,
            poolclass=poolclass,
            connect_args=(
                {
                    "command_timeout": 30,
                    "server_settings": {
                        "statement_timeout": "10000",  # 10s cap (#429)
                        "lock_timeout": "5000",  # bounded lock waits (#430)
                        "idle_in_transaction_session_timeout": "30000",
                    },
                }
                if not settings.DATABASE_URL.startswith("sqlite")
                else {}
            ),
            **pool_kwargs,
        )
        cls.session_factory = async_sessionmaker(
            cls.engine, class_=AsyncSession, expire_on_commit=False
        )

    @classmethod
    async def close(cls) -> None:
        """Close database connection."""
        if cls.engine:
            await cls.engine.dispose()

    @classmethod
    async def health_check(cls) -> bool:
        """Check database health."""
        try:
            if cls.engine is None:
                await cls.init()
            assert cls.engine is not None
            async with cls.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Get database session."""
    if DatabaseManager.session_factory is None:
        await DatabaseManager.init()
    assert DatabaseManager.session_factory is not None
    async with DatabaseManager.session_factory() as session:
        yield session
