import pytest
import pytest_asyncio
import pathlib
import importlib.util
from uuid import uuid4
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


# Load the content-service models and repositories directly
def _load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(f"Cannot load module {name} from {path}")
    spec.loader.exec_module(mod)
    return mod


models_path = pathlib.Path(__file__).parents[2] / "app" / "models" / "__init__.py"
repos_path = pathlib.Path(__file__).parents[2] / "app" / "repositories" / "__init__.py"

_models_mod = _load_module("content_models", models_path)
_repos_mod = _load_module("content_repos", repos_path)

Base = _models_mod.Base
Genre = _models_mod.Genre
RightsHolder = _models_mod.RightsHolder
TerritorialLicense = _models_mod.TerritorialLicense
ContentType = _models_mod.ContentType

ContentRepository = _repos_mod.ContentRepository
SeasonRepository = _repos_mod.SeasonRepository
EpisodeRepository = _repos_mod.EpisodeRepository
GenreRepository = _repos_mod.GenreRepository


@pytest_asyncio.fixture
async def db_session(tmp_path):
    """Create a fresh async SQLite DB per test file."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'test.db'}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
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
        content_type=_models_mod.ContentType.MOVIE,
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
    by_genre = await repo.get_by_genre(genre.id)
    assert any(c.id == content.id for c in by_genre)
    updated = await repo.update(content.id, title="Space Odyssey")
    assert updated.title == "Space Odyssey"
    deleted = await repo.delete(content.id)
    assert deleted
    assert await repo.get_by_id(content.id) is None


# ---------- Season & Episode ----------
@pytest.mark.asyncio
async def test_season_episode_hierarchy(db_session: AsyncSession):
    content_repo = ContentRepository(db_session)
    content = await content_repo.create(
        title="Series X",
        slug="series-x",
        description="Series test",
        content_type=_models_mod.ContentType.SERIES,
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
    license = TerritorialLicense(content_id=uuid4(), rights_holder_id=holder.id)
    db_session.add(license)
    await db_session.flush()
    fetched_holder = await db_session.get(_models_mod.RightsHolder, holder.id)
    fetched_license = await db_session.get(_models_mod.TerritorialLicense, license.id)
    assert fetched_holder.name == "Studio Z"
    assert fetched_license.rights_holder_id == holder.id
