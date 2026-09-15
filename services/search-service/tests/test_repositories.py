import pytest
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.models import Base as SearchBase
from app.models import SearchQuery, SearchIndex
from app.repositories import SearchQueryRepository, SearchIndexRepository

@pytest.fixture(scope="function")
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True, echo=False)
    async with engine.begin() as conn:
        # create tables for both SearchQuery and SearchIndex metadata
        await conn.run_sync(SearchBase.metadata.create_all)
    async_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session_factory() as session:
        yield session
    await engine.dispose()

@pytest.mark.asyncio
async def test_search_query_create_and_recent(async_session: AsyncSession):
    repo = SearchQueryRepository(async_session)
    user_id = uuid.uuid4()
    await repo.create(user_id=user_id, query_text="test query")
    await async_session.flush()
    recent = await repo.get_recent(user_id=user_id, limit=1)
    assert len(recent) == 1
    assert recent[0].query_text == "test query"

@pytest.mark.asyncio
async def test_search_index_upsert_and_delete(async_session: AsyncSession):
    repo = SearchIndexRepository(async_session)
    content_id = uuid.uuid4()
    # upsert new row
    idx = await repo.upsert(content_id, title="Title", content_type="movie")
    await async_session.flush()
    fetched = await repo.get_by_content_id(content_id)
    assert fetched is not None
    assert fetched.title == "Title"
    # delete and verify removal
    await repo.delete(content_id)
    await async_session.flush()
    after = await repo.get_by_content_id(content_id)
    assert after is None
