"""Database management for Streaming Service."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool, QueuePool
import logging

from app.core.settings import settings
from app.models import Base

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database connections."""
    _instance = None
    _engine = None
    _session_factory = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def get_engine(self):
        """Get or create database engine."""
        if self._engine is None:
            pool_class = NullPool if settings.ENVIRONMENT == "development" else QueuePool
            self._engine = create_async_engine(
                settings.DATABASE_URL,
                echo=settings.DEBUG,
                future=True,
                pool_pre_ping=True,
                poolclass=pool_class,
                pool_size=settings.DB_POOL_SIZE,
                max_overflow=settings.DB_MAX_OVERFLOW,
            )
        return self._engine
    
    def get_session_factory(self):
        """Get or create session factory."""
        if self._session_factory is None:
            engine = self.get_engine()
            self._session_factory = async_sessionmaker(
                engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
            )
        return self._session_factory
    
    async def get_session(self):
        """Get async database session."""
        factory = self.get_session_factory()
        async with factory() as session:
            yield session
    
    async def health_check(self) -> bool:
        """Check database health."""
        try:
            engine = self.get_engine()
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False
    
    async def close(self):
        """Close database connections."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None


db_manager = DatabaseManager()


async def get_db_session() -> AsyncSession:
    """Get database session for dependency injection.

    Yields:
        AsyncSession: Database session
    """
    factory = db_manager.get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Alias kept for API routes, which declare ``Depends(get_db)``.
get_db = get_db_session
