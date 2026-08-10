"""Database connection management."""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import settings


class DatabaseManager:
    """Manages database connections."""

    engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None

    @classmethod
    async def init(cls) -> None:
        """Initialize database."""
        cls.engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)
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
