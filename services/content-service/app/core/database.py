"""
Database connection management for Content Service.
Handles SQLAlchemy async engine and session factory.
"""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.settings import settings

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database connections and sessions."""

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
            # Pool class follows the driver: NullPool only for SQLite test
            # engines; PostgreSQL always gets a capped QueuePool so live dev
            # is pooled too.
            is_sqlite = settings.DATABASE_URL.startswith("sqlite")
            pool_class = NullPool if is_sqlite else None

            pool_kwargs: dict = {} if is_sqlite else {
                "pool_size": 5,
                "max_overflow": 5,
                "pool_timeout": 30,
                "pool_recycle": 3600,
            }

            connect_args: dict = (
                {}
                if is_sqlite
                else {
                    "command_timeout": 30,
                    "server_settings": {
                        "statement_timeout": "10000",  # 10s cap (#429)
                        "lock_timeout": "5000",  # bounded lock waits (#430)
                        "idle_in_transaction_session_timeout": "30000",
                    },
                }
            )

            self._engine = create_async_engine(
                settings.DATABASE_URL,
                echo=settings.DEBUG,
                future=True,
                **({"poolclass": pool_class} if pool_class else {}),
                pool_pre_ping=not is_sqlite,
                **pool_kwargs,
                connect_args=connect_args,
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
        except Exception as e:  # noqa: BLE001
            logger.error(f"Database health check failed: {e}")
            return False

    async def close(self):
        """Close database connections."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None


# Global database manager instance
db_manager = DatabaseManager()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Get database session for API dependency injection.

    Wraps ``db_manager.get_session`` so route handlers can use
    ``Depends(get_db)``.

    Yields:
        AsyncSession: Database session
    """
    async for session in db_manager.get_session():
        yield session
