"""Database connection management for Admin Service."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import QueuePool

from app.core.settings import settings


class DatabaseManager:
    """Manages database connections and session lifecycle."""

    engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None

    @classmethod
    async def init(cls) -> None:
        """Initialize the async engine and session factory."""
        cls.engine = create_async_engine(
            settings.DATABASE_URL,
            echo=settings.DEBUG,
            future=True,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=5,
            pool_timeout=30,
            pool_recycle=3600,
            pool_pre_ping=True,
            connect_args={
                "command_timeout": 30,
            },
        )
        cls.session_factory = async_sessionmaker(
            cls.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @classmethod
    async def health_check(cls) -> bool:
        if not cls.engine:
            return False
        try:
            async with cls.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    async def close(cls):
        if cls.engine:
            await cls.engine.dispose()


async def get_db():
    if not DatabaseManager.session_factory:
        await DatabaseManager.init()
    assert DatabaseManager.session_factory is not None
    async with DatabaseManager.session_factory() as session:
        yield session
