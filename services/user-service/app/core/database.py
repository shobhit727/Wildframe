"""Database configuration and session management for User Service."""
import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, QueuePool

from app.core.settings import settings

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database connections and session lifecycle."""

    _engine: AsyncEngine | None = None
    _session_factory: async_sessionmaker | None = None

    @classmethod
    def get_engine(cls) -> AsyncEngine:
        """Get or create async SQLAlchemy engine."""
        if cls._engine is None:
            pool_class = NullPool if settings.ENVIRONMENT == "development" else QueuePool

            cls._engine = create_async_engine(
                settings.DATABASE_URL,
                echo=settings.DEBUG,
                pool_class=pool_class,
                pool_size=settings.DATABASE_POOL_SIZE,
                max_overflow=settings.DATABASE_MAX_OVERFLOW,
                pool_timeout=settings.DATABASE_POOL_TIMEOUT,
                pool_recycle=settings.DATABASE_POOL_RECYCLE,
                connect_args={
                    "timeout": 10,
                    "command_timeout": 60,
                },
            )
        return cls._engine

    @classmethod
    def get_session_factory(cls) -> async_sessionmaker:
        """Get or create async session factory."""
        if cls._session_factory is None:
            cls._session_factory = async_sessionmaker(
                cls.get_engine(),
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
                autocommit=False,
            )
        return cls._session_factory

    @classmethod
    async def get_session(cls) -> AsyncGenerator[AsyncSession, None]:
        """Get database session for dependency injection."""
        session_factory = cls.get_session_factory()
        async with session_factory() as session:
            try:
                yield session
            except SQLAlchemyError as e:
                logger.error(f"Database error: {e}")
                await session.rollback()
                raise
            finally:
                await session.close()

    @classmethod
    async def health_check(cls) -> bool:
        """Check database connectivity."""
        try:
            engine = cls.get_engine()
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False

    @classmethod
    async def close(cls) -> None:
        """Close database connections."""
        if cls._engine is not None:
            await cls._engine.dispose()
            cls._engine = None
            cls._session_factory = None


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Get database session for dependency injection."""
    async for session in DatabaseManager.get_session():
        yield session


# Alias kept for API routes, which declare ``Depends(get_db)``.
get_db = get_db_session
