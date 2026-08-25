"""
Database configuration and session management for Auth Service.
Implements async SQLAlchemy with connection pooling and health checks.
"""

import logging
from collections.abc import AsyncGenerator

from app.core.settings import settings
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database connections and session lifecycle."""

    _engine: AsyncEngine | None = None
    _session_factory: async_sessionmaker | None = None

    @classmethod
    def get_engine(cls) -> AsyncEngine:
        """Get or create async SQLAlchemy engine.

        Returns:
            AsyncEngine: The async database engine
        """
        if cls._engine is None:
            # Pool class follows the driver, not ENVIRONMENT: NullPool only
            # for SQLite (test engines); PostgreSQL always gets a capped
            # QueuePool. Live dev against Postgres must be pooled too.
            database_url = settings.DATABASE_URL
            assert database_url is not None, "DATABASE_URL is not configured"
            # NullPool only for SQLite; for PostgreSQL omit poolclass so
            # SQLAlchemy selects its async-adapted queue pool automatically.
            is_sqlite = database_url.startswith("sqlite")
            pool_class = NullPool if is_sqlite else None

            pool_kwargs: dict = (
                {}
                if is_sqlite
                else {
                    "pool_size": 5,
                    "max_overflow": 5,
                    "pool_timeout": 30,
                    "pool_recycle": 3600,
                }
            )

            connect_args: dict = (
                {"timeout": 10}
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
            cls._engine = create_async_engine(
                database_url,
                echo=settings.DEBUG,
                **({"poolclass": pool_class} if pool_class else {}),
                pool_pre_ping=not is_sqlite,
                **pool_kwargs,
                connect_args=connect_args,
            )
        return cls._engine

    @classmethod
    def get_session_factory(cls) -> async_sessionmaker:
        """Get or create async session factory.

        Returns:
            async_sessionmaker: The session factory
        """
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
        """Get database session for dependency injection.

        Yields:
            AsyncSession: Database session
        """
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
        """Check database connectivity.

        Returns:
            bool: True if database is healthy, False otherwise
        """
        try:
            engine = cls.get_engine()
            async with engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"Database health check failed: {e}")
            return False

    @classmethod
    async def close(cls) -> None:
        """Close database connections."""
        if cls._engine is not None:
            await cls._engine.dispose()
            cls._engine = None
            cls._session_factory = None


# Dependency injection helper
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Get database session for dependency injection.

    Yields:
        AsyncSession: Database session
    """
    async for session in DatabaseManager.get_session():
        yield session


# Alias kept for API routes, which declare ``Depends(get_db)``.
get_db = get_db_session
