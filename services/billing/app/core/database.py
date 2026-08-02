
"""Database connection management for the Billing Service.

Provides the DatabaseManager singleton and the get_db() async generator
used as a FastAPI dependency for injecting AsyncSession instances.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import settings


class DatabaseManager:
    """Manages database connections and session lifecycle."""

    engine = None
    session_factory = None

    @classmethod
    async def init(cls) -> None:
        """Initialize the async engine and session factory."""
        cls.engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
        cls.session_factory = async_sessionmaker(
            cls.engine, class_=AsyncSession, expire_on_commit=False,
        )

    @classmethod
    async def close(cls) -> None:
        """Dispose of the engine (called on shutdown)."""
        if cls.engine:
            await cls.engine.dispose()

    @classmethod
    async def health_check(cls) -> bool:
        """Check database connectivity by executing SELECT 1."""
        try:
            if not cls.engine:
                await cls.init()
            async with cls.engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    async def get_session(cls):
        """Yield an AsyncSession (for use outside of FastAPI DI)."""
        if not cls.session_factory:
            await cls.init()
        async with cls.session_factory() as session:
            yield session


async def get_db() -> AsyncSession:
    """FastAPI dependency that yields an AsyncSession per request."""
    if not DatabaseManager.session_factory:
        await DatabaseManager.init()
    async with DatabaseManager.session_factory() as session:
        yield session


# Alias for backward compatibility with existing route imports.
get_db_session = get_db
