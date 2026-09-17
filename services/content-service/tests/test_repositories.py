import pytest
import pytest_asyncio
import os
from collections.abc import AsyncIterator
from contextlib import ExitStack
from datetime import UTC, datetime
from uuid import uuid4
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


from testcontainers.postgres import PostgresContainer

from app.models import Base, ContentStatus, ContentType
from app.models.rights import Base as RightsBase, RightsHolder, TerritorialLicense
from app.repositories import (
    ContentRepository,
    SeasonRepository,
    EpisodeRepository,
    GenreRepository,
)


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Use disposable PostgreSQL; TEST_DATABASE_URL must name a test database."""
    with ExitStack() as stack:
        url = os.environ.get("TEST_DATABASE_URL")
        if not url:
            postgres = stack.enter_context(PostgresContainer("postgres:15"))
            url = postgres.get_connection_url()
        engine = create_async_engine(make_url(url).set(drivername="postgresql+asyncpg"), echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await conn.run_sync(RightsBase.metadata.create_all)
            async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with async_session() as session:
                yield session
        finally:
            await engine.dispose()


# ---------- Genre Repository ----------
@pytest.mark.asyncio
async def test_genre_crud(db_session: AsyncSession):
    repo = GenreRepository(db_session)
    genre = await repo.create(name="Action", slug="action")
    fetched = await repo.get_by_id(genre.id)
    assert fetched and fetched.name == "Action"
    updated = await repo.update(genre.id, name="Adventure")
    assert updated.name == "Adventure"
    all_genres = await repo.get_all()
    assert any(g.id == genre.id for g in all_genres)
    deleted = await repo.delete(genre.id)
    assert deleted
    assert await repo.get_by_id(genre.id) is None


# ---------- Content Repository ----------
@pytest.mark.asyncio
async def test_content_crud_and_filters(db_session: AsyncSession):
    genre_repo = GenreRepository(db_session)
    genre = await genre_repo.create(name="SciFi", slug="scifi")
    repo = ContentRepository(db_session)
    content = await repo.create(
        title="Space Quest",
        slug="space-quest",
        description="A sci‑fi adventure",
        content_type=ContentType.MOVIE,
        release_date=None,
        duration_minutes=None,
        original_language="en",
        country=None,
        poster_url=None,
        backdrop_url=None,
        trailer_url=None,
        imdb_rating=None,
        content_rating=None,
        is_premium=False,
        can_download=True,
        can_stream=True,
        genres=[genre],
    )
    fetched = await repo.get_by_id(content.id)
    assert fetched.title == "Space Quest"
    by_slug = await repo.get_by_slug("space-quest")
    assert by_slug.id == content.id
    await repo.update(content.id, status=ContentStatus.PUBLISHED)
    by_genre = await repo.get_by_genre(genre.id)
    assert any(c.id == content.id for c in by_genre)
    updated = await repo.update(content.id, title="Space Odyssey")
    assert updated.title == "Space Odyssey"
    deleted = await repo.delete(content.id)
    assert deleted
    soft_deleted = await repo.get_by_id(content.id)
    assert soft_deleted is not None and soft_deleted.deleted_at is not None
    assert all(c.id != content.id for c in await repo.get_by_genre(genre.id))


# ---------- Season & Episode ----------
@pytest.mark.asyncio
async def test_season_episode_hierarchy(db_session: AsyncSession):
    content_repo = ContentRepository(db_session)
    content = await content_repo.create(
        title="Series X",
        slug="series-x",
        description="Series test",
        content_type=ContentType.SERIES,
        release_date=None,
        duration_minutes=None,
        original_language="en",
        country=None,
        poster_url=None,
        backdrop_url=None,
        trailer_url=None,
        imdb_rating=None,
        content_rating=None,
        is_premium=False,
        can_download=True,
        can_stream=True,
        genres=[],
    )
    season_repo = SeasonRepository(db_session)
    season = await season_repo.create(content_id=content.id, season_number=1, title="Season 1")
    episode_repo = EpisodeRepository(db_session)
    ep = await episode_repo.create(
        content_id=content.id,
        season_id=season.id,
        episode_number=1,
        title="Episode 1",
        duration_minutes=45,
    )
    fetched_ep = await episode_repo.get_by_id(ep.id)
    assert fetched_ep.title == "Episode 1"
    eps = await episode_repo.get_season_episodes(season.id)
    assert any(e.id == ep.id for e in eps)
    await episode_repo.delete(ep.id)
    await season_repo.delete(season.id)
    await content_repo.delete(content.id)


# ---------- Rights models (no repository) ----------
@pytest.mark.asyncio
async def test_rights_models(db_session: AsyncSession):
    holder = RightsHolder(name="Studio Z", type="studio")
    db_session.add(holder)
    await db_session.flush()
    license = TerritorialLicense(
        content_id=uuid4(),
        rights_holder_id=holder.id,
        territory="US",
        avail_start=datetime(2026, 1, 1, tzinfo=UTC),
        avail_end=datetime(2027, 1, 1, tzinfo=UTC),
    )
    db_session.add(license)
    await db_session.flush()
    fetched_holder = await db_session.get(RightsHolder, holder.id)
    fetched_license = await db_session.get(TerritorialLicense, license.id)
    assert fetched_holder.name == "Studio Z"
    assert fetched_license.rights_holder_id == holder.id
