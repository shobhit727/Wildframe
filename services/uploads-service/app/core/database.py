"""Database connection management."""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import settings


class DatabaseManager:
    """Manages database connections."""

    engine = None
    session_factory = None

    @classmethod
    async def init(cls) -> None:
        """Initialize database."""
        cls.engine = create_async_engine(
            settings.DATABASE_URL, echo=False, future=True
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
            if not cls.engine:
                await cls.init()
            async with cls.engine.connect() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False


async def get_db() -> AsyncSession:
    """Get database session."""
    if not DatabaseManager.session_factory:
        await DatabaseManager.init()
    async with DatabaseManager.session_factory() as session:
        yield session
