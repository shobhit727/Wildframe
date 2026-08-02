from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import settings


class DatabaseManager:
    engine = None
    session_factory = None

    @classmethod
    async def init(cls):
        cls.engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG)
        cls.session_factory = async_sessionmaker(cls.engine, class_=AsyncSession)

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
    async with DatabaseManager.session_factory() as session:
        yield session
