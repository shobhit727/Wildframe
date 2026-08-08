"""Deep coverage tests for ContentService — exercises every service method and
its failure/edge branches against mocked repositories."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.services import ContentService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def service():
    """ContentService wired to fresh AsyncMock repositories."""
    svc = ContentService(AsyncMock())
    svc.content_repo = AsyncMock()
    svc.genre_repo = AsyncMock()
    svc.cast_repo = AsyncMock()
    svc.season_repo = AsyncMock()
    svc.episode_repo = AsyncMock()
    svc.rating_repo = AsyncMock()
    svc.recommendation_repo = AsyncMock()
    return svc


# ---------------------------------------------------------------------------
# Genre edge branches
# ---------------------------------------------------------------------------


class TestGenreEdgeCases:
    async def test_update_genre_delegates_and_commits(self, service):
        from app.schemas import GenreCreateRequest

        mock_genre = MagicMock()
        service.genre_repo.update.return_value = mock_genre

        result = await service.update_genre(
            uuid4(), GenreCreateRequest(name="Sci-Fi", slug="sci-fi")
        )

        assert result is mock_genre
        service.genre_repo.update.assert_awaited_once()
        service.content_repo.commit.assert_awaited_once()

    async def test_delete_genre_returns_bool(self, service):
        service.genre_repo.delete.return_value = True

        result = await service.delete_genre(uuid4())

        assert result is True
        service.genre_repo.delete.assert_awaited_once()
        service.content_repo.commit.assert_awaited_once()

    async def test_create_genre_rollback_on_error(self, service):
        from app.schemas import GenreCreateRequest

        service.genre_repo.create.side_effect = RuntimeError("db down")

        with pytest.raises(RuntimeError):
            await service.create_genre(GenreCreateRequest(name="X", slug="x"))

        service.content_repo.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# Cast member operations
# ---------------------------------------------------------------------------


class TestCastMemberOperations:
    async def test_create_cast_member(self, service):
        from app.schemas import CastMemberCreateRequest

        mock_member = MagicMock()
        service.cast_repo.create.return_value = mock_member

        result = await service.create_cast_member(
            CastMemberCreateRequest(name="Keanu", slug="keanu", bio="actor")
        )

        assert result is mock_member
        service.cast_repo.create.assert_awaited_once()
        service.content_repo.commit.assert_awaited_once()

    async def test_add_cast_member_creates_when_missing(self, service):
        from app.schemas import CastMemberCreateRequest

        content = MagicMock()
        content.cast_members = []
        member = MagicMock()
        service.content_repo.get_by_id.return_value = content
        service.cast_repo.get_by_slug.return_value = None
        service.cast_repo.create.return_value = member

        result = await service.add_cast_member(
            uuid4(), CastMemberCreateRequest(name="Keanu", slug="keanu")
        )

        assert result is member
        service.cast_repo.create.assert_awaited_once()
        assert member in content.cast_members
        service.content_repo.commit.assert_awaited_once()

    async def test_add_cast_member_skips_duplicate(self, service):
        from app.schemas import CastMemberCreateRequest

        content = MagicMock()
        member = MagicMock()
        content.cast_members = [member]
        service.content_repo.get_by_id.return_value = content
        service.cast_repo.get_by_slug.return_value = member

        result = await service.add_cast_member(
            uuid4(), CastMemberCreateRequest(name="Keanu", slug="keanu")
        )

        assert result is member
        service.cast_repo.create.assert_not_awaited()

    async def test_add_cast_member_missing_content_returns_none(self, service):
        from app.schemas import CastMemberCreateRequest

        service.content_repo.get_by_id.return_value = None

        result = await service.add_cast_member(
            uuid4(), CastMemberCreateRequest(name="Keanu", slug="keanu")
        )

        assert result is None

    async def test_list_cast_empty_for_missing_content(self, service):
        service.content_repo.get_by_id.return_value = None

        assert await service.list_cast(uuid4()) == []


# ---------------------------------------------------------------------------
# Content publish / delete / update branches
# ---------------------------------------------------------------------------


class TestContentLifecycle:
    async def test_publish_sets_published_at(self, service):
        from app.schemas import ContentPublishRequest

        service.content_repo.update.return_value = MagicMock()
        content_id = uuid4()

        await service.publish_content(content_id, ContentPublishRequest(status="published"))

        _, kwargs = service.content_repo.update.await_args
        assert kwargs["status"] == "published"
        assert "published_at" in kwargs
        service.content_repo.commit.assert_awaited_once()

    async def test_archive_does_not_set_published_at(self, service):
        from app.schemas import ContentPublishRequest

        service.content_repo.update.return_value = MagicMock()

        await service.publish_content(uuid4(), ContentPublishRequest(status="archived"))

        _, kwargs = service.content_repo.update.await_args
        assert "published_at" not in kwargs

    async def test_delete_content(self, service):
        service.content_repo.delete.return_value = True

        assert await service.delete_content(uuid4()) is True
        service.content_repo.commit.assert_awaited_once()

    async def test_update_content_not_found_returns_none(self, service):
        from app.schemas import ContentUpdateRequest

        service.content_repo.update.return_value = None

        result = await service.update_content(uuid4(), ContentUpdateRequest(title="X"))

        assert result is None
        service.content_repo.rollback.assert_awaited_once()

    async def test_update_content_replaces_genres(self, service):
        from app.schemas import ContentUpdateRequest

        content = MagicMock()
        genre_a = MagicMock()
        service.content_repo.update.return_value = content
        service.genre_repo.get_by_id.side_effect = [genre_a, None]

        await service.update_content(uuid4(), ContentUpdateRequest(genre_ids=[uuid4(), uuid4()]))

        content.genres.clear.assert_called_once()
        content.genres.append.assert_called_once_with(genre_a)


# ---------------------------------------------------------------------------
# Season branches
# ---------------------------------------------------------------------------


class TestSeasonBranches:
    async def test_get_season_wrong_content_returns_none(self, service):
        season = MagicMock(content_id=uuid4())
        service.season_repo.get_by_id.return_value = season

        assert await service.get_season(uuid4(), uuid4()) is None

    async def test_get_season_missing_returns_none(self, service):
        service.season_repo.get_by_id.return_value = None

        assert await service.get_season(uuid4(), uuid4()) is None

    async def test_delete_season_wrong_content_returns_false(self, service):
        season = MagicMock(content_id=uuid4())
        service.season_repo.get_by_id.return_value = season

        assert await service.delete_season(uuid4(), uuid4()) is False

    async def test_update_season_wrong_content_returns_none(self, service):
        from app.schemas import SeasonUpdateRequest

        season = MagicMock(content_id=uuid4())
        service.season_repo.get_by_id.return_value = season

        assert await service.update_season(uuid4(), uuid4(), SeasonUpdateRequest(title="X")) is None

    async def test_list_seasons(self, service):
        service.season_repo.get_content_seasons.return_value = [MagicMock()]

        assert len(await service.list_seasons(uuid4())) == 1


# ---------------------------------------------------------------------------
# Episode branches
# ---------------------------------------------------------------------------


class TestEpisodeBranches:
    def _season(self, content_id):
        return MagicMock(content_id=content_id)

    def _episode(self, season_id):
        return MagicMock(season_id=season_id)

    async def test_get_episode_wrong_season_returns_none(self, service):
        content_id, season_id, episode_id = uuid4(), uuid4(), uuid4()
        service.season_repo.get_by_id.return_value = self._season(content_id)
        service.episode_repo.get_by_id.return_value = self._episode(uuid4())

        assert await service.get_episode(content_id, season_id, episode_id) is None

    async def test_get_episode_missing_season_returns_none(self, service):
        service.season_repo.get_by_id.return_value = None

        assert await service.get_episode(uuid4(), uuid4(), uuid4()) is None

    async def test_delete_episode_mismatch_returns_false(self, service):
        content_id, season_id = uuid4(), uuid4()
        service.season_repo.get_by_id.return_value = self._season(content_id)
        service.episode_repo.get_by_id.return_value = self._episode(uuid4())

        assert await service.delete_episode(content_id, season_id, uuid4()) is False

    async def test_delete_episode_success(self, service):
        content_id, season_id, episode_id = uuid4(), uuid4(), uuid4()
        service.season_repo.get_by_id.return_value = self._season(content_id)
        service.episode_repo.get_by_id.return_value = self._episode(season_id)
        service.episode_repo.delete.return_value = True

        assert await service.delete_episode(content_id, season_id, episode_id) is True
        service.episode_repo.delete.assert_awaited_once_with(episode_id)

    async def test_update_episode_success(self, service):
        from app.schemas import EpisodeUpdateRequest

        content_id, season_id, episode_id = uuid4(), uuid4(), uuid4()
        updated = MagicMock()
        service.season_repo.get_by_id.return_value = self._season(content_id)
        service.episode_repo.get_by_id.return_value = self._episode(season_id)
        service.episode_repo.update.return_value = updated

        result = await service.update_episode(
            content_id, season_id, episode_id, EpisodeUpdateRequest(title="New")
        )

        assert result is updated
        service.content_repo.commit.assert_awaited_once()

    async def test_update_episode_wrong_content_returns_none(self, service):
        from app.schemas import EpisodeUpdateRequest

        service.season_repo.get_by_id.return_value = self._season(uuid4())

        assert (
            await service.update_episode(uuid4(), uuid4(), uuid4(), EpisodeUpdateRequest(title="X"))
            is None
        )

    async def test_list_episodes_wrong_content_returns_empty(self, service):
        service.season_repo.get_by_id.return_value = self._season(uuid4())

        assert await service.list_episodes(uuid4(), uuid4()) == []


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------


class TestRatings:
    async def test_rate_content_updates_avg(self, service):
        from app.schemas import ContentRatingCreateRequest

        content = MagicMock()
        ratings = [MagicMock(rating=4), MagicMock(rating=6)]
        service.rating_repo.create.return_value = MagicMock()
        service.rating_repo.get_content_ratings.return_value = ratings
        service.content_repo.get_by_id.return_value = content

        await service.rate_content(uuid4(), uuid4(), ContentRatingCreateRequest(rating=5))

        assert content.audience_score == 5.0
        assert content.total_votes == 2
        service.content_repo.commit.assert_awaited_once()

    async def test_list_ratings(self, service):
        service.rating_repo.get_content_ratings.return_value = [MagicMock()]

        assert len(await service.list_ratings(uuid4())) == 1


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


class TestRecommendations:
    async def test_add_and_remove(self, service):
        from app.schemas import ContentRecommendationCreateRequest

        mock_rec = MagicMock()
        service.recommendation_repo.create.return_value = mock_rec

        result = await service.add_recommendation(
            uuid4(),
            ContentRecommendationCreateRequest(
                recommended_content_id=uuid4(),
                similarity_score=0.9,
                recommendation_type="similar",
            ),
        )

        assert result is mock_rec
        service.content_repo.commit.assert_awaited_once()

        service.recommendation_repo.delete.return_value = True
        assert await service.remove_recommendation(uuid4()) is True
        service.recommendation_repo.delete.assert_awaited_once()

    async def test_get_recommendations(self, service):
        content_id = uuid4()
        service.recommendation_repo.get_recommendations.return_value = [MagicMock()]

        assert len(await service.get_recommendations(content_id, 5)) == 1
        service.recommendation_repo.get_recommendations.assert_awaited_once_with(content_id, 5)


# ---------------------------------------------------------------------------
# Animation-specific queries
# ---------------------------------------------------------------------------


class TestSpecialQueries:
    async def test_animation_style(self, service):
        from app.models import AnimationStyle

        service.content_repo.get_by_animation_style.return_value = [MagicMock()]

        assert len(await service.get_by_animation_style(AnimationStyle.CGI_3D)) == 1
        service.content_repo.get_by_animation_style.assert_awaited_once()

    async def test_series_episodes(self, service):
        service.content_repo.get_series_episodes.return_value = [MagicMock()]

        assert len(await service.get_series_episodes(uuid4())) == 1
        service.content_repo.get_series_episodes.assert_awaited_once()

    async def test_creator_filmography(self, service):
        service.content_repo.get_creator_filmography.return_value = [MagicMock()]

        assert len(await service.get_creator_filmography(uuid4())) == 1
        service.content_repo.get_creator_filmography.assert_awaited_once()
