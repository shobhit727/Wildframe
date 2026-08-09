"""Tests for Search Service business logic (indexing, search, trending)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services import SearchService, content_to_doc


@pytest.fixture
def es_mock():
    return MagicMock()


@pytest.fixture
def service(es_mock, query_repo, index_repo):
    return SearchService(es_mock, query_repo, index_repo)


@pytest.fixture
def query_repo():
    return MagicMock(create=AsyncMock())


@pytest.fixture
def index_repo():
    return MagicMock(create=AsyncMock())


def fake_hit(source: dict) -> dict:
    return {"_source": source}


class TestSearchService:
    @pytest.mark.asyncio
    async def test_search_builds_multi_match_and_logs_queries(self, es_mock, service, query_repo):
        es_mock.search = AsyncMock(return_value={"hits": {"hits": [fake_hit({"title": "A"})]}})

        service.query_repo = query_repo
        await service.search(user_id=uuid4(), query="action", content_type="movie", limit=5)

        body = es_mock.search.await_args.kwargs["body"]
        assert body["size"] == 5
        assert body["query"]["bool"]["must"][0]["multi_match"]["query"] == "action"
        assert body["query"]["bool"]["must"][1] == {"term": {"content_type": "movie"}}
        query_repo.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_search_anonymous_does_not_log(self, es_mock, service):
        es_mock.search = AsyncMock(return_value={"hits": {"hits": []}})
        query_repo = MagicMock()
        service.query_repo = query_repo

        await service.search(user_id=None, query="x")

        query_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_tolerates_es_failure(self, es_mock, service):
        es_mock.search = AsyncMock(side_effect=ConnectionError("es down"))

        results = await service.search(user_id=None, query="x")

        assert results == []

    @pytest.mark.asyncio
    async def test_trending_sorts_by_rating(self, es_mock, service):
        es_mock.search = AsyncMock(
            return_value={"hits": {"hits": [fake_hit({"title": "B"}), fake_hit({"title": "A"})]}}
        )

        results = await service.trending()

        body = es_mock.search.await_args.kwargs["body"]
        assert body["sort"] == [{"rating": {"order": "desc"}}]
        assert [r["title"] for r in results] == ["B", "A"]

    @pytest.mark.asyncio
    async def test_trending_tolerates_es_failure(self, es_mock, service):
        es_mock.search = AsyncMock(side_effect=ConnectionError("es down"))

        results = await service.trending()

        assert results == []

    @pytest.mark.asyncio
    async def test_index_content_writes_es_and_repo(self, es_mock, service, index_repo):
        es_mock.index = AsyncMock()

        service.index_repo = index_repo

        await service.index_content(uuid4(), "T", "D", "movie", genre="drama")

        es_mock.index.assert_awaited_once()
        index_repo.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_catalog_fetches_content_and_indexes(self, es_mock, service):
        catalog = MagicMock()
        catalog.fetch_published = AsyncMock(
            return_value=[
                {"id": uuid4(), "title": "X", "description": "d", "content_type": "movie"}
            ]
        )
        es_mock.indices.exists = AsyncMock(return_value=False)
        es_mock.indices.create = AsyncMock()
        es_mock.index = AsyncMock()

        count = await service.reindex_catalog(catalog)

        assert count == 1
        es_mock.indices.create.assert_awaited_once()
        es_mock.index.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_catalog_tolerates_catalog_failure(self, es_mock, service):
        catalog = MagicMock()
        catalog.fetch_published = AsyncMock(side_effect=RuntimeError("catalog down"))
        es_mock.indices.exists = AsyncMock(return_value=True)

        count = await service.reindex_catalog(catalog)

        assert count == 0
        es_mock.index.assert_not_called()


class TestContentToDoc:
    def test_flattens_genres(self):
        item = {
            "id": uuid4(),
            "title": "Blade",
            "description": "half vampire",
            "content_type": "movie",
            "audience_score": 84.0,
            "status": "published",
            "genres": [{"name": "Action", "slug": "action"}],
        }
        doc = content_to_doc(item)
        assert doc["title"] == "Blade"
        assert doc["genres"] == ["Action"]
        assert doc["rating"] == 84.0
        assert doc["status"] == "published"

    def test_empty_genres_become_empty_list(self):
        doc = content_to_doc(
            {"title": "x", "description": "", "content_type": "movie", "genres": []}
        )
        assert doc["genres"] == []
