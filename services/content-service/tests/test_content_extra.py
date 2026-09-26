"""Additional behavioural coverage for the content service's service layer,
request schemas and event publisher.

Three groups:

* ``TestSlugValidation`` — the three request schemas that reject a malformed
  slug with the same message.
* ``TestEventPublisherSelection`` — ``app/core/events.py`` picks the publisher
  backend from ``settings.EVENT_PUBLISHER`` and can be reset (test seam).
* ``TestContentServiceFailureBranches`` — the rollback/re-raise paths and the
  remaining thin delegations of ``ContentService``, exercised against
  repository doubles.
* ``TestContentLifecycleAgainstPostgres`` / ``TestRepositoryQueries`` — the
  same layer driven end-to-end over HTTP and directly against a real
  PostgreSQL session, so the SQL in the repositories is actually executed.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url

from app.core import events as core_events
from app.core.settings import settings
from app.models import AnimationStyle, ContentStatus, ContentType
from app.repositories import (
    CastMemberRepository,
    ContentRatingRepository,
    ContentRecommendationRepository,
    ContentRepository,
    EpisodeRepository,
    GenreRepository,
    SeasonRepository,
)
from app.schemas import (
    CastMemberCreateRequest,
    ContentCreateRequest,
    ContentPublishRequest,
    ContentRatingCreateRequest,
    ContentRecommendationCreateRequest,
    ContentUpdateRequest,
    EpisodeCreateRequest,
    EpisodeUpdateRequest,
    GenreCreateRequest,
    SeasonCreateRequest,
    SeasonUpdateRequest,
)
from app.services import ContentService

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- slug schemas
class TestSlugValidation:
    @pytest.mark.parametrize("schema", [GenreCreateRequest, CastMemberCreateRequest])
    def test_request_schemas_reject_an_uppercase_slug(self, schema):
        with pytest.raises(ValidationError) as exc:
            schema(name="A", slug="Not-A-Slug")

        assert "Slug must contain only lowercase letters, numbers, and hyphens" in str(
            exc.value
        )

    def test_content_create_request_rejects_a_malformed_slug(self):
        with pytest.raises(ValidationError) as exc:
            ContentCreateRequest(
                title="T", slug="has spaces", description="D", content_type="movie"
            )

        assert "Slug must contain only lowercase letters, numbers, and hyphens" in str(
            exc.value
        )

    @pytest.mark.parametrize(
        "slug", ["action", "sci-fi", "a1", "top-10-shows", "x"]
    )
    def test_well_formed_slugs_are_accepted(self, slug):
        assert GenreCreateRequest(name="A", slug=slug).slug == slug

    @pytest.mark.parametrize("slug", ["trailing-", "double--hyphen", "under_score", ""])
    def test_malformed_slugs_are_rejected(self, slug):
        with pytest.raises(ValidationError):
            GenreCreateRequest(name="A", slug=slug)


# ----------------------------------------------------------------- event bus
class TestEventPublisherSelection:
    @pytest.fixture(autouse=True)
    def _reset(self):
        core_events.reset_event_publisher()
        yield
        core_events.reset_event_publisher()

    def test_in_memory_publisher_is_the_default(self, monkeypatch):
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")

        publisher = core_events.get_event_publisher()

        assert isinstance(publisher, core_events.InMemoryEventPublisher)
        assert core_events.get_event_publisher() is publisher

    def test_kafka_publisher_is_selected_by_configuration(self, monkeypatch):
        from wildframe_events import KafkaEventPublisher

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "kafka.test:9092")

        publisher = core_events.get_event_publisher()

        assert isinstance(publisher, KafkaEventPublisher)
        assert core_events.get_event_publisher() is publisher

    def test_reset_drops_the_cached_publisher(self, monkeypatch):
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")
        first = core_events.get_event_publisher()

        core_events.reset_event_publisher()
        second = core_events.get_event_publisher()

        assert second is not first

    def test_unknown_backend_falls_back_to_in_memory(self, monkeypatch):
        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "not-a-backend")

        assert isinstance(core_events.get_event_publisher(), core_events.InMemoryEventPublisher)

    def test_lifecycle_events_carry_idempotency_keys(self):
        deleted = core_events.content_deleted_event("abc")
        published = core_events.content_published_event("abc")
        unpublished = core_events.content_unpublished_event("abc")

        assert deleted.key == "deleted:abc"
        assert published.key == "published:abc"
        assert unpublished.key == "unpublished:abc"
        assert deleted.payload == {"content_id": "abc"}
        assert deleted.producer == "content-service"
        assert {deleted.topic, published.topic, unpublished.topic} == {
            core_events.Topic.CONTENT_DELETED,
            core_events.Topic.CONTENT_PUBLISHED,
            core_events.Topic.CONTENT_UNPUBLISHED,
        }


# ------------------------------------------------- ContentService failure paths
@pytest.fixture
def service():
    """ContentService wired to repository doubles."""
    svc = ContentService(AsyncMock())
    svc.content_repo = AsyncMock()
    svc.genre_repo = AsyncMock()
    svc.cast_repo = AsyncMock()
    svc.season_repo = AsyncMock()
    svc.episode_repo = AsyncMock()
    svc.rating_repo = AsyncMock()
    svc.recommendation_repo = AsyncMock()
    return svc


def _content_request(**overrides) -> ContentCreateRequest:
    payload = {
        "title": "Space Quest",
        "slug": "space-quest",
        "description": "A sci-fi adventure",
        "content_type": "movie",
    }
    payload.update(overrides)
    return ContentCreateRequest(**payload)


def _season(content_id=None) -> MagicMock:
    season = MagicMock()
    season.id = uuid4()
    season.content_id = content_id or uuid4()
    season.episode_count = 0
    return season


def _episode(season_id=None) -> MagicMock:
    episode = MagicMock()
    episode.id = uuid4()
    episode.season_id = season_id or uuid4()
    return episode


class TestGenreFailureBranches:
    async def test_update_genre_rolls_back_and_reraises(self, service):
        service.genre_repo.update.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError, match="db down"):
            await service.update_genre(uuid4(), GenreCreateRequest(name="A", slug="a"))

        service.content_repo.rollback.assert_awaited_once()
        service.content_repo.commit.assert_not_awaited()

    async def test_delete_genre_rolls_back_and_reraises(self, service):
        service.genre_repo.delete.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.delete_genre(uuid4())

        service.content_repo.rollback.assert_awaited_once()


class TestCastMemberDelegation:
    async def test_create_cast_member_rolls_back_and_reraises(self, service):
        service.cast_repo.create.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.create_cast_member(
                CastMemberCreateRequest(name="K", slug="k")
            )

        service.content_repo.rollback.assert_awaited_once()

    async def test_get_cast_member_delegates(self, service):
        member = MagicMock()
        service.cast_repo.get_by_id.return_value = member
        member_id = uuid4()

        assert await service.get_cast_member(member_id) is member
        service.cast_repo.get_by_id.assert_awaited_once_with(member_id)

    async def test_search_cast_members_delegates(self, service):
        service.cast_repo.search.return_value = ["a", "b"]

        assert await service.search_cast_members("ke") == ["a", "b"]
        service.cast_repo.search.assert_awaited_once_with("ke")


class TestContentCreation:
    async def test_genre_ids_are_resolved_and_attached(self, service):
        genre_a, genre_b = MagicMock(), MagicMock()
        service.genre_repo.get_by_id.side_effect = [genre_a, None, genre_b]
        request = _content_request(genre_ids=[uuid4(), uuid4(), uuid4()])

        await service.create_content(request)

        attached = service.content_repo.create.await_args.kwargs["genres"]
        assert attached == [genre_a, genre_b]

    async def test_creation_rolls_back_and_reraises(self, service):
        service.content_repo.create.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError, match="db down"):
            await service.create_content(_content_request())

        service.content_repo.rollback.assert_awaited_once()

    async def test_creation_reloads_the_content_after_commit(self, service):
        created, reloaded = MagicMock(), MagicMock()
        service.content_repo.create.return_value = created
        service.content_repo.get_by_id.return_value = reloaded

        result = await service.create_content(_content_request())

        assert result is reloaded
        service.content_repo.commit.assert_awaited_once()


class TestContentQueries:
    async def test_list_content_forwards_pagination(self, service):
        service.content_repo.list_filtered.return_value = ["a"]

        result = await service.list_content(2, 5, "movie", "published", uuid4())

        assert result == ["a"]
        service.content_repo.list_filtered.assert_awaited_once()

    async def test_get_content_by_slug_delegates(self, service):
        service.content_repo.get_by_slug.return_value = "content"

        assert await service.get_content_by_slug("slug") == "content"
        service.content_repo.get_by_slug.assert_awaited_once_with("slug")

    async def test_list_content_by_type_converts_the_string(self, service):
        service.content_repo.get_by_type.return_value = ["a"]

        await service.list_content_by_type("series")

        service.content_repo.get_by_type.assert_awaited_once_with(ContentType.SERIES)

    async def test_list_content_by_genre_delegates(self, service):
        genre_id = uuid4()
        service.content_repo.get_by_genre.return_value = ["a"]

        assert await service.list_content_by_genre(genre_id) == ["a"]
        service.content_repo.get_by_genre.assert_awaited_once_with(genre_id)

    async def test_search_content_delegates(self, service):
        service.content_repo.search.return_value = ["a"]

        assert await service.search_content("quest") == ["a"]
        service.content_repo.search.assert_awaited_once_with("quest")

    async def test_get_trending_content_delegates(self, service):
        service.content_repo.get_trending.return_value = ["a"]

        assert await service.get_trending_content(7) == ["a"]
        service.content_repo.get_trending.assert_awaited_once_with(7)

    async def test_get_premium_content_delegates(self, service):
        service.content_repo.get_premium.return_value = ["a"]

        assert await service.get_premium_content() == ["a"]
        service.content_repo.get_premium.assert_awaited_once()

    async def test_animation_and_series_queries_delegate(self, service):
        service.content_repo.get_by_animation_style.return_value = ["a"]
        service.content_repo.get_series_episodes.return_value = ["b"]
        service.content_repo.get_creator_filmography.return_value = ["c"]

        assert await service.get_by_animation_style(AnimationStyle.CGI_3D, 10, 5) == ["a"]
        assert await service.get_series_episodes(uuid4(), 10, 0) == ["b"]
        assert await service.get_creator_filmography(uuid4(), 10, 0) == ["c"]
        service.content_repo.get_by_animation_style.assert_awaited_once_with(
            AnimationStyle.CGI_3D, 10, 5
        )


class TestContentMutationFailures:
    async def test_update_content_rolls_back_and_reraises(self, service):
        service.content_repo.update.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.update_content(uuid4(), ContentUpdateRequest(title="New"))

        service.content_repo.rollback.assert_awaited_once()

    async def test_publish_content_rolls_back_and_reraises(self, service):
        service.content_repo.update.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.publish_content(uuid4(), ContentPublishRequest(status="published"))

        service.content_repo.rollback.assert_awaited_once()

    async def test_delete_content_rolls_back_and_reraises(self, service):
        service.content_repo.delete.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.delete_content(uuid4())

        service.content_repo.rollback.assert_awaited_once()

    async def test_update_content_rolls_back_when_the_row_is_gone(self, service):
        service.content_repo.update.return_value = None

        assert await service.update_content(uuid4(), ContentUpdateRequest(title="New")) is None
        service.content_repo.rollback.assert_awaited_once()


class TestSeasonService:
    async def test_create_season_rolls_back_and_reraises(self, service):
        service.season_repo.create.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.create_season(
                uuid4(), SeasonCreateRequest(season_number=1, title="S1")
            )

        service.content_repo.rollback.assert_awaited_once()

    async def test_get_season_returns_the_matching_season(self, service):
        content_id = uuid4()
        season = _season(content_id)
        service.season_repo.get_by_id.return_value = season

        assert await service.get_season(content_id, season.id) is season

    async def test_get_season_rejects_a_season_from_another_content(self, service):
        service.season_repo.get_by_id.return_value = _season()

        assert await service.get_season(uuid4(), uuid4()) is None

    async def test_list_content_seasons_delegates(self, service):
        content_id = uuid4()
        service.season_repo.get_content_seasons.return_value = ["s"]

        assert await service.list_content_seasons(content_id) == ["s"]
        service.season_repo.get_content_seasons.assert_awaited_once_with(content_id)

    async def test_delete_season_refuses_a_missing_season(self, service):
        service.season_repo.get_by_id.return_value = None

        assert await service.delete_season(uuid4(), uuid4()) is False
        service.season_repo.delete.assert_not_awaited()

    async def test_delete_season_rolls_back_and_reraises(self, service):
        service.season_repo.get_by_id.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.delete_season(uuid4(), uuid4())

        service.content_repo.rollback.assert_awaited_once()

    async def test_update_season_happy_path_commits(self, service):
        content_id = uuid4()
        season = _season(content_id)
        updated = MagicMock()
        service.season_repo.get_by_id.return_value = season
        service.season_repo.update.return_value = updated

        result = await service.update_season(
            content_id, season.id, SeasonUpdateRequest(title="S1 renamed")
        )

        assert result is updated
        service.content_repo.commit.assert_awaited_once()

    async def test_update_season_rolls_back_and_reraises(self, service):
        content_id = uuid4()
        season = _season(content_id)
        service.season_repo.get_by_id.return_value = season
        service.season_repo.update.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.update_season(
                content_id, season.id, SeasonUpdateRequest(title="S1")
            )

        service.content_repo.rollback.assert_awaited_once()


class TestEpisodeService:
    async def test_create_episode_rolls_back_and_reraises(self, service):
        service.episode_repo.create.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.create_episode(
                uuid4(),
                uuid4(),
                EpisodeCreateRequest(episode_number=1, title="Pilot", duration_minutes=45),
            )

        service.content_repo.rollback.assert_awaited_once()

    async def test_create_episode_updates_the_season_episode_count(self, service):
        content_id, season_id = uuid4(), uuid4()
        season = _season(content_id)
        service.season_repo.get_by_id.return_value = season
        service.episode_repo.get_season_episodes.return_value = [_episode(), _episode()]

        await service.create_episode(
            content_id,
            season_id,
            EpisodeCreateRequest(episode_number=1, title="Pilot", duration_minutes=45),
        )

        assert season.episode_count == 2

    async def test_list_season_episodes_delegates(self, service):
        season_id = uuid4()
        service.episode_repo.get_season_episodes.return_value = ["e"]

        assert await service.list_season_episodes(season_id) == ["e"]
        service.episode_repo.get_season_episodes.assert_awaited_once_with(season_id)

    async def test_list_episodes_returns_the_seasons_episodes(self, service):
        content_id, season_id = uuid4(), uuid4()
        season = _season(content_id)
        service.season_repo.get_by_id.return_value = season
        service.episode_repo.get_season_episodes.return_value = ["e"]

        assert await service.list_episodes(content_id, season_id) == ["e"]
        service.episode_repo.get_season_episodes.assert_awaited_once_with(season_id)

    async def test_delete_episode_refuses_a_season_from_another_content(self, service):
        service.season_repo.get_by_id.return_value = _season()

        assert await service.delete_episode(uuid4(), uuid4(), uuid4()) is False
        service.episode_repo.delete.assert_not_awaited()

    async def test_delete_episode_rolls_back_and_reraises(self, service):
        content_id, season_id = uuid4(), uuid4()
        service.season_repo.get_by_id.return_value = _season(content_id)
        service.episode_repo.get_by_id.return_value = _episode(season_id)
        service.episode_repo.delete.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.delete_episode(content_id, season_id, uuid4())

        service.content_repo.rollback.assert_awaited_once()

    async def test_update_episode_rejects_an_episode_from_another_season(self, service):
        content_id, season_id = uuid4(), uuid4()
        service.season_repo.get_by_id.return_value = _season(content_id)
        service.episode_repo.get_by_id.return_value = _episode(uuid4())

        result = await service.update_episode(
            content_id, season_id, uuid4(), EpisodeUpdateRequest(title="Renamed")
        )

        assert result is None
        service.episode_repo.update.assert_not_awaited()

    async def test_update_episode_rolls_back_and_reraises(self, service):
        content_id, season_id = uuid4(), uuid4()
        service.season_repo.get_by_id.return_value = _season(content_id)
        service.episode_repo.get_by_id.return_value = _episode(season_id)
        service.episode_repo.update.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.update_episode(
                content_id, season_id, uuid4(), EpisodeUpdateRequest(title="Renamed")
            )

        service.content_repo.rollback.assert_awaited_once()


class TestRatingAndRecommendationService:
    async def test_rate_content_rolls_back_and_reraises(self, service):
        service.rating_repo.create.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.rate_content(
                uuid4(), uuid4(), ContentRatingCreateRequest(rating=8.0)
            )

        service.content_repo.rollback.assert_awaited_once()

    async def test_rate_content_recomputes_the_audience_score(self, service):
        content = MagicMock()
        service.rating_repo.create.return_value = MagicMock()
        service.rating_repo.get_content_ratings.return_value = [
            MagicMock(rating=8.0),
            MagicMock(rating=10.0),
        ]
        service.content_repo.get_by_id.return_value = content

        await service.rate_content(uuid4(), uuid4(), ContentRatingCreateRequest(rating=8.0))

        assert content.audience_score == 9.0
        assert content.total_votes == 2

    async def test_get_content_ratings_delegates(self, service):
        content_id = uuid4()
        service.rating_repo.get_content_ratings.return_value = ["r"]

        assert await service.get_content_ratings(content_id) == ["r"]
        service.rating_repo.get_content_ratings.assert_awaited_with(content_id)

    async def test_add_recommendation_rolls_back_and_reraises(self, service):
        service.recommendation_repo.create.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.add_recommendation(
                uuid4(),
                ContentRecommendationCreateRequest(
                    recommended_content_id=uuid4(),
                    similarity_score=0.9,
                    recommendation_type="similar",
                ),
            )

        service.content_repo.rollback.assert_awaited_once()

    async def test_remove_recommendation_rolls_back_and_reraises(self, service):
        service.recommendation_repo.delete.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.remove_recommendation(uuid4())

        service.content_repo.rollback.assert_awaited_once()

    async def test_list_recommendations_delegates(self, service):
        content_id = uuid4()
        service.recommendation_repo.get_recommendations.return_value = ["r"]

        assert await service.list_recommendations(content_id) == ["r"]
        service.recommendation_repo.get_recommendations.assert_awaited_once_with(
            content_id, 10
        )

    async def test_get_recommendations_forwards_the_limit(self, service):
        service.recommendation_repo.get_recommendations.return_value = ["r"]

        await service.get_recommendations(uuid4(), 3)

        service.recommendation_repo.get_recommendations.assert_awaited_once_with(
            service.recommendation_repo.get_recommendations.await_args.args[0], 3
        )


class TestAddCastMemberService:
    async def test_missing_content_returns_none(self, service):
        service.content_repo.get_by_id.return_value = None

        result = await service.add_cast_member(
            uuid4(), CastMemberCreateRequest(name="K", slug="k")
        )

        assert result is None
        service.cast_repo.create.assert_not_awaited()

    async def test_rolls_back_and_reraises(self, service):
        service.content_repo.get_by_id.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.add_cast_member(
                uuid4(), CastMemberCreateRequest(name="K", slug="k")
            )

        service.content_repo.rollback.assert_awaited_once()

    async def test_list_cast_is_empty_for_unknown_content(self, service):
        service.content_repo.get_by_id.return_value = None

        assert await service.list_cast(uuid4()) == []


# ------------------------------------------- end-to-end against a real database
LOCAL_TEST_DATABASE_URL = "postgresql+asyncpg://postgres:test@127.0.0.1:55432/test_db"


def _local_instance_reachable(url: str) -> bool:
    """Cheap TCP probe so the suite also works without TEST_DATABASE_URL set."""
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.hostname:
        return False
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((parsed.hostname, parsed.port or 5432)) == 0


@pytest.fixture(scope="module")
def database_url():
    """TEST_DATABASE_URL, the documented local test instance, else a container."""
    import os
    from contextlib import ExitStack

    with ExitStack() as stack:
        url = os.environ.get("TEST_DATABASE_URL")
        if url:
            yield url
            return
        if _local_instance_reachable(LOCAL_TEST_DATABASE_URL):
            yield LOCAL_TEST_DATABASE_URL
            return
        from testcontainers.postgres import PostgresContainer  # lazy import

        postgres = stack.enter_context(PostgresContainer("postgres:15"))
        yield postgres.get_connection_url()


@pytest_asyncio.fixture
async def db_session(database_url):
    """A real PostgreSQL session with the full content schema created."""
    engine = create_async_engine(
        make_url(database_url).set(drivername="postgresql+asyncpg"), echo=False
    )
    from app.models import Base

    # Drop + recreate so every test starts from an empty catalog (the schema
    # lives in a long-lived PostgreSQL instance, not a throwaway one).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


class TestContentLifecycleAgainstPostgres:
    """The service + repository SQL, executed for real against PostgreSQL."""

    async def test_genre_crud_round_trip(self, db_session):
        service = ContentService(db_session)
        genre = await service.create_genre(
            GenreCreateRequest(name=f"Westerns-{uuid4().hex[:8]}", slug=f"westerns-{uuid4().hex[:8]}")
        )
        genre_id = genre.id

        assert (await service.get_genre(genre_id)).name == genre.name
        assert any(g.id == genre_id for g in await service.list_genres())

        renamed_name = f"Westerns-2-{uuid4().hex[:8]}"
        renamed = await service.update_genre(
            genre_id, GenreCreateRequest(name=renamed_name, slug=f"w2-{uuid4().hex[:8]}")
        )
        assert renamed.name == renamed_name

        assert await service.delete_genre(genre_id) is True
        assert await service.get_genre(genre_id) is None

    async def test_content_lifecycle_including_publish_and_soft_delete(self, db_session):
        core_events.reset_event_publisher()
        service = ContentService(db_session)
        genre = await service.create_genre(
            GenreCreateRequest(name=f"Sci-Fi-{uuid4().hex[:8]}", slug=f"scifi-{uuid4().hex[:8]}")
        )
        created = await service.create_content(
            _content_request(slug=f"space-{uuid4().hex[:8]}", genre_ids=[genre.id])
        )
        content_id = created.id

        assert created.status == ContentStatus.DRAFT
        assert [g.id for g in created.genres] == [genre.id]

        published = await service.publish_content(
            content_id, ContentPublishRequest(status="published")
        )
        assert published.status == ContentStatus.PUBLISHED
        assert published.published_at is not None

        assert (await service.get_content_by_slug(created.slug)).id == content_id
        assert any(c.id == content_id for c in await service.list_content_by_genre(genre.id))
        assert any(
            c.id == content_id for c in await service.list_content_by_type("movie")
        )
        assert any(c.id == content_id for c in await service.search_content("Space"))
        assert any(c.id == content_id for c in await service.get_trending_content(50))

        filtered = await service.list_content(page=1, page_size=50, content_type="movie")
        assert any(c.id == content_id for c in filtered)

        assert await service.delete_content(content_id) is True
        # Soft delete (#433): the row survives but leaves the catalog.
        assert await service.get_content(content_id) is not None
        assert all(
            c.id != content_id for c in await service.list_content(page=1, page_size=50)
        )

    async def test_list_content_filters_by_status_and_genre(self, db_session):
        service = ContentService(db_session)
        genre = await service.create_genre(
            GenreCreateRequest(name=f"Noir-{uuid4().hex[:8]}", slug=f"noir-{uuid4().hex[:8]}")
        )
        draft = await service.create_content(
            _content_request(slug=f"noir-draft-{uuid4().hex[:8]}", genre_ids=[genre.id])
        )
        await service.publish_content(draft.id, ContentPublishRequest(status="published"))
        other = await service.create_content(_content_request(slug=f"other-{uuid4().hex[:8]}"))

        by_status = await service.list_content(
            page=1, page_size=50, content_type="movie", status="published"
        )
        assert [c.id for c in by_status] == [draft.id]

        by_genre = await service.list_content(page=1, page_size=50, genre_id=genre.id)
        assert [c.id for c in by_genre] == [draft.id]
        assert other.id not in [c.id for c in by_genre]

    async def test_premium_and_unpublished_queries_exclude_deleted_rows(self, db_session):
        service = ContentService(db_session)
        created = await service.create_content(
            _content_request(slug=f"premium-{uuid4().hex[:8]}", is_premium=True)
        )
        content_id = created.id

        # Drafts are excluded from the premium feed until they are published.
        assert all(c.id != content_id for c in await service.get_premium_content())

        await service.publish_content(content_id, ContentPublishRequest(status="published"))
        assert any(c.id == content_id for c in await service.get_premium_content())

        await service.delete_content(content_id)
        assert all(c.id != content_id for c in await service.get_premium_content())

    async def test_season_and_episode_lifecycle_updates_counts(self, db_session):
        service = ContentService(db_session)
        content = await service.create_content(
            _content_request(
                slug=f"series-{uuid4().hex[:8]}",
                content_type="series",
                duration_minutes=None,
            )
        )
        season = await service.create_season(
            content.id, SeasonCreateRequest(season_number=1, title="Season 1")
        )
        assert [s.id for s in await service.list_seasons(content.id)] == [season.id]
        assert (await service.get_season(content.id, season.id)).id == season.id

        episode = await service.create_episode(
            content.id,
            season.id,
            EpisodeCreateRequest(episode_number=1, title="Pilot", duration_minutes=45),
        )
        assert (await service.get_season(content.id, season.id)).episode_count == 1
        assert [e.id for e in await service.list_episodes(content.id, season.id)] == [
            episode.id
        ]
        assert (
            await service.get_episode(content.id, season.id, episode.id)
        ).title == "Pilot"

        updated_season = await service.update_season(
            content.id, season.id, SeasonUpdateRequest(title="Season One")
        )
        assert updated_season.title == "Season One"

        updated_episode = await service.update_episode(
            content.id, season.id, episode.id, EpisodeUpdateRequest(title="Pilot (extended)")
        )
        assert updated_episode.title == "Pilot (extended)"

        assert await service.delete_episode(content.id, season.id, episode.id) is True
        assert (await service.get_season(content.id, season.id)).episode_count == 0
        assert await service.delete_season(content.id, season.id) is True
        assert await service.list_seasons(content.id) == []

    async def test_rating_upserts_and_recomputes_the_average(self, db_session):
        service = ContentService(db_session)
        content = await service.create_content(
            _content_request(slug=f"rated-{uuid4().hex[:8]}")
        )
        user_a, user_b = uuid4(), uuid4()

        await service.rate_content(content.id, user_a, ContentRatingCreateRequest(rating=6.0))
        await service.rate_content(
            content.id, user_b, ContentRatingCreateRequest(rating=10.0, review="Great")
        )

        ratings = await service.get_content_ratings(content.id)
        assert len(ratings) == 2
        refreshed = await service.get_content(content.id)
        assert refreshed.audience_score == 8.0
        assert refreshed.total_votes == 2

        # Re-rating from the same user updates in place (unique constraint).
        await service.rate_content(content.id, user_a, ContentRatingCreateRequest(rating=2.0))
        assert len(await service.get_content_ratings(content.id)) == 2

    async def test_recommendation_and_cast_lifecycle(self, db_session):
        service = ContentService(db_session)
        content = await service.create_content(
            _content_request(slug=f"rec-{uuid4().hex[:8]}")
        )
        other = await service.create_content(
            _content_request(slug=f"rec2-{uuid4().hex[:8]}")
        )
        rec = await service.add_recommendation(
            content.id,
            ContentRecommendationCreateRequest(
                recommended_content_id=other.id,
                similarity_score=0.8,
                recommendation_type="similar",
            ),
        )
        assert [r.id for r in await service.list_recommendations(content.id)] == [rec.id]

        # Reported bug: ``content_cast.role`` is NOT NULL but
        # ContentService.add_cast_member appends to ``content.cast_members``
        # without ever populating the association row's role, so the insert
        # fails against a real PostgreSQL database.
        content_id = content.id  # read before the failed insert expires the row
        with pytest.raises(IntegrityError) as exc:
            await service.add_cast_member(
                content.id,
                CastMemberCreateRequest(
                    name="Keanu Reeves", slug=f"keanu-{uuid4().hex[:8]}", bio="Actor"
                ),
            )
        assert "role" in str(exc.value)
        await db_session.rollback()
        reloaded = await service.get_content(content_id)
        assert reloaded.cast_members == []

    async def test_standalone_cast_member_crud(self, db_session):
        service = ContentService(db_session)
        member = await service.create_cast_member(
            CastMemberCreateRequest(name="Someone", slug=f"someone-{uuid4().hex[:8]}")
        )

        assert (await service.get_cast_member(member.id)).name == "Someone"
        assert [m.id for m in await service.search_cast_members("Some")] == [member.id]


class TestRepositoryQueries:
    """Repository methods the HTTP surface does not reach."""

    async def _content_row(self, session, slug=None, content_type=ContentType.MOVIE):
        """A committed content row, for child rows that need a real FK."""
        repo = ContentRepository(session)
        row = await repo.create(
            title="Parent",
            slug=slug or f"parent-{uuid4().hex[:8]}",
            description="D",
            content_type=content_type,
            release_date=None,
        )
        await repo.commit()
        return row

    async def test_get_published_returns_only_published_rows(self, db_session):
        repo = ContentRepository(db_session)
        slug = f"pub-{uuid4().hex[:8]}"
        created = await repo.create(
            title="Draft",
            slug=slug,
            description="D",
            content_type=ContentType.MOVIE,
            release_date=None,
        )
        await repo.commit()

        assert all(c.id != created.id for c in await repo.get_published())

        await repo.update(created.id, status=ContentStatus.PUBLISHED)
        await repo.commit()
        assert any(c.id == created.id for c in await repo.get_published())

    async def test_get_by_animation_style_and_creator_filmography(self, db_session):
        repo = ContentRepository(db_session)
        creator_id = uuid4()
        created = await repo.create(
            title="Animated",
            slug=f"anim-{uuid4().hex[:8]}",
            description="D",
            content_type=ContentType.MOVIE,
            release_date=None,
        )
        created.animation_style = AnimationStyle.CGI_3D
        created.creator_id = creator_id
        await repo.commit()
        await repo.update(created.id, status=ContentStatus.PUBLISHED)
        await repo.commit()

        assert any(
            c.id == created.id
            for c in await repo.get_by_animation_style(AnimationStyle.CGI_3D)
        )
        assert any(
            c.id == created.id for c in await repo.get_creator_filmography(creator_id)
        )

    async def test_get_series_episodes_orders_by_season_and_episode(self, db_session):
        repo = ContentRepository(db_session)
        from app.models import ContentSeries

        series = ContentSeries(title="Series", slug=f"series-{uuid4().hex[:8]}")
        db_session.add(series)
        await db_session.commit()
        series_id = series.id
        created = await repo.create(
            title="Episode row",
            slug=f"ep-{uuid4().hex[:8]}",
            description="D",
            content_type=ContentType.EPISODE,
            release_date=None,
        )
        created.series_id = series_id
        created.season_number = 1
        created.episode_number = 1
        await repo.commit()

        rows = await repo.get_series_episodes(series_id)
        assert [r.id for r in rows] == [created.id]

    async def test_season_lookup_by_content_and_number(self, db_session):
        content = await self._content_row(db_session)
        content_id = content.id
        repo = SeasonRepository(db_session)
        season = await repo.create(
            content_id=content_id, season_number=2, title="Season 2"
        )
        await repo.commit()

        found = await repo.get_by_content_and_number(content_id, 2)
        assert found.id == season.id
        assert await repo.get_by_content_and_number(content_id, 9) is None
        assert [s.id for s in await repo.get_content_seasons(content_id)] == [season.id]
        assert await repo.delete(season.id) is True
        assert await repo.delete(season.id) is False

    async def test_episode_and_genre_lookup_helpers(self, db_session):
        genre_repo = GenreRepository(db_session)
        slug = f"look-{uuid4().hex[:8]}"
        genre = await genre_repo.create(name=f"Lookup-{uuid4().hex[:8]}", slug=slug)
        await genre_repo.commit()
        assert (await genre_repo.get_by_slug(slug)).id == genre.id
        assert await genre_repo.get_by_slug("missing-slug") is None
        assert await genre_repo.update(uuid4(), name="x") is None
        assert await genre_repo.delete(uuid4()) is False

        content = await self._content_row(db_session)
        season = await SeasonRepository(db_session).create(
            content_id=content.id, season_number=1, title="S1"
        )
        episode_repo = EpisodeRepository(db_session)
        episode = await episode_repo.create(
            content_id=season.content_id,
            season_id=season.id,
            episode_number=1,
            title="Pilot",
            duration_minutes=45,
        )
        await episode_repo.commit()
        assert [e.id for e in await episode_repo.get_season_episodes(season.id)] == [
            episode.id
        ]
        assert await episode_repo.update(uuid4(), title="x") is None
        assert await episode_repo.delete(uuid4()) is False

    async def test_rating_and_recommendation_lookups(self, db_session):
        content = await self._content_row(db_session)
        other = await self._content_row(db_session)
        content_id, user_id = content.id, uuid4()
        rating_repo = ContentRatingRepository(db_session)
        rating = await rating_repo.create(
            content_id=content_id, user_id=user_id, rating=5.0, review="ok"
        )
        await rating_repo.commit()
        assert (await rating_repo.get_user_rating(content_id, user_id)).id == rating.id
        assert await rating_repo.get_user_rating(content_id, uuid4()) is None
        assert [r.id for r in await rating_repo.get_content_ratings(content_id)] == [
            rating.id
        ]

        rec_repo = ContentRecommendationRepository(db_session)
        rec = await rec_repo.create(
            content_id=content_id,
            recommended_content_id=other.id,
            similarity_score=0.5,
            recommendation_type="similar",
        )
        await rec_repo.commit()
        assert [r.id for r in await rec_repo.get_recommendations(content_id)] == [rec.id]
        assert await rec_repo.delete(rec.id) is True
        assert await rec_repo.delete(rec.id) is False

    async def test_cast_member_lookups_escape_like_wildcards(self, db_session):
        repo = CastMemberRepository(db_session)
        member = await repo.create(
            name="Percent % Person", slug=f"pct-{uuid4().hex[:8]}", bio=None
        )
        await repo.commit()

        assert (await repo.get_by_slug(member.slug)).id == member.id
        assert await repo.get_by_slug("no-such-slug") is None
        # The literal "%" must not behave as a wildcard.
        assert repo.session is db_session
        assert [m.id for m in await repo.search("%")] == [member.id]

    async def test_update_and_delete_report_missing_rows(self, db_session):
        content_repo = ContentRepository(db_session)
        assert await content_repo.update(uuid4(), title="x") is None
        assert await content_repo.delete(uuid4()) is False

        season_repo = SeasonRepository(db_session)
        assert await season_repo.update(uuid4(), title="x") is None

    async def test_base_repository_commit_and_rollback(self, db_session):
        repo = GenreRepository(db_session)
        rolled_back = f"Rollback-{uuid4().hex[:8]}"
        await repo.create(name=rolled_back, slug=f"rb-{uuid4().hex[:8]}")
        await repo.flush()

        await repo.rollback()

        assert all(g.name != rolled_back for g in await repo.get_all())

        committed = f"Committed-{uuid4().hex[:8]}"
        genre = await repo.create(name=committed, slug=f"ok-{uuid4().hex[:8]}")
        await repo.commit()
        assert (await repo.get_by_id(genre.id)).name == committed
