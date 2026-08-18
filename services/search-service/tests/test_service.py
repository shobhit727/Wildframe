"""Tests for Search Service business logic (indexing, search, trending)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services import (
    SearchService,
    content_to_doc,
    CatalogFetchError,
    IndexingError,
    ReindexResult,
    SearchResult,
)


@pytest.fixture
def es_mock():
    """Mock Elasticsearch client with properly nested indices namespace."""
    mock = MagicMock()
    mock.indices = MagicMock()
    mock.indices.exists = AsyncMock()
    mock.indices.create = AsyncMock()
    mock.indices.put_alias = AsyncMock()
    mock.indices.get_alias = AsyncMock()
    mock.indices.get = AsyncMock()
    mock.cluster = MagicMock()
    mock.cluster.put_settings = AsyncMock()
    mock.indices.update_aliases = AsyncMock()
    mock.indices.delete = AsyncMock()
    return mock


@pytest.fixture
def query_repo():
    return MagicMock(create=AsyncMock())


@pytest.fixture
def index_repo():
    return MagicMock(upsert=AsyncMock(), delete=AsyncMock())


@pytest.fixture
def service(es_mock, query_repo, index_repo):
    return SearchService(es_mock, query_repo, index_repo)


def fake_hit(source: dict, sort: list | None = None) -> dict:
    hit = {"_source": source}
    if sort is not None:
        hit["sort"] = sort
    return hit


class TestSearchService:
    @pytest.mark.asyncio
    async def test_search_builds_multi_match_and_logs_queries(self, es_mock, service, query_repo):
        es_mock.search = AsyncMock(return_value={"hits": {"hits": [fake_hit({"title": "A"})]}})

        service.query_repo = query_repo
        result = await service.search(
            user_id=uuid4(), query="action", content_type="movie", limit=5
        )

        assert isinstance(result, SearchResult)
        assert len(result.results) == 1
        body = es_mock.search.await_args.kwargs["body"]
        assert body["size"] == 5
        assert body["query"]["bool"]["must"][0]["multi_match"]["query"] == "action"
        assert body["query"]["bool"]["must"][1] == {"term": {"content_type": "movie"}}
        assert body["query"]["bool"]["filter"] == [{"term": {"status": "published"}}]
        assert body["sort"] == [{"rating": {"order": "desc"}}, {"_id": {"order": "asc"}}]
        assert body["timeout"] == "5s"
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

        result = await service.search(user_id=None, query="x")

        assert isinstance(result, SearchResult)
        assert result.results == []
        assert result.next_sort is None

    @pytest.mark.asyncio
    async def test_search_returns_search_after_cursor(self, es_mock, service):
        """search_after is returned when we hit the page limit."""
        es_mock.search = AsyncMock(
            return_value={
                "hits": {
                    "hits": [
                        fake_hit({"title": "A"}, sort=[9.0, "id1"]),
                        fake_hit({"title": "B"}, sort=[8.5, "id2"]),
                    ]
                }
            }
        )

        result = await service.search(user_id=None, query="test", limit=2)

        assert len(result.results) == 2
        assert result.next_sort == [8.5, "id2"]

    @pytest.mark.asyncio
    async def test_search_no_cursor_when_partial_page(self, es_mock, service):
        es_mock.search = AsyncMock(
            return_value={
                "hits": {
                    "hits": [
                        fake_hit({"title": "A"}, sort=[9.0, "id1"]),
                    ]
                }
            }
        )

        result = await service.search(user_id=None, query="test", limit=2)

        assert result.next_sort is None

    @pytest.mark.asyncio
    async def test_search_rejects_empty_query(self, service):
        with pytest.raises(ValueError, match="query must not be empty"):
            await service.search(user_id=None, query="")

    @pytest.mark.asyncio
    async def test_search_rejects_whitespace_query(self, service):
        with pytest.raises(ValueError, match="query must not be empty"):
            await service.search(user_id=None, query="   ")

    @pytest.mark.asyncio
    async def test_search_rejects_long_query(self, service):
        with pytest.raises(ValueError, match="query too long"):
            await service.search(user_id=None, query="x" * 201)

    @pytest.mark.asyncio
    async def test_search_rejects_control_chars(self, service):
        with pytest.raises(ValueError, match="control characters"):
            await service.search(user_id=None, query="test\x00query")

    @pytest.mark.asyncio
    async def test_search_with_search_after(self, es_mock, service):
        es_mock.search = AsyncMock(
            return_value={"hits": {"hits": [fake_hit({"title": "C"}, sort=[8.0, "id3"])]}}
        )

        result = await service.search(
            user_id=None, query="test", limit=2, search_after=[9.0, "id1"]
        )

        body = es_mock.search.await_args.kwargs["body"]
        assert body["search_after"] == [9.0, "id1"]
        assert result.results[0]["title"] == "C"

    @pytest.mark.asyncio
    async def test_trending_sorts_by_rating(self, es_mock, service):
        es_mock.search = AsyncMock(
            return_value={"hits": {"hits": [fake_hit({"title": "B"}), fake_hit({"title": "A"})]}}
        )

        results = await service.trending()

        body = es_mock.search.await_args.kwargs["body"]
        assert body["sort"] == [{"rating": {"order": "desc"}}, {"_id": {"order": "asc"}}]
        assert body["query"]["bool"]["filter"] == [{"term": {"status": "published"}}]
        assert [r["title"] for r in results] == ["B", "A"]

    @pytest.mark.asyncio
    async def test_trending_tolerates_es_failure(self, es_mock, service):
        es_mock.search = AsyncMock(side_effect=ConnectionError("es down"))

        results = await service.trending()

        assert results == []

    @pytest.mark.asyncio
    async def test_index_content_upserts_es_and_repo(self, es_mock, service, index_repo):
        es_mock.index = AsyncMock()
        service.index_repo = index_repo

        await service.index_content(uuid4(), "T", "D", "movie", genre="drama")

        es_mock.index.assert_awaited_once()
        index_repo.upsert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_catalog_fetches_content_and_indexes(self, es_mock, service):
        catalog = MagicMock()
        catalog.fetch_published = AsyncMock(
            return_value=[
                {"id": uuid4(), "title": "X", "description": "d", "content_type": "movie"}
            ]
        )
        # Mock alias target to return None (fresh install)
        es_mock.indices.get_alias = AsyncMock(side_effect=Exception("NotFound"))
        es_mock.indices.exists = AsyncMock(return_value=False)
        es_mock.indices.create = AsyncMock()
        es_mock.indices.put_alias = AsyncMock()
        es_mock.bulk = AsyncMock(return_value={"errors": False, "items": []})

        result = await service.reindex_catalog(catalog)

        assert isinstance(result, ReindexResult)
        assert result.count == 1
        assert result.switched is True
        assert result.index_name.startswith("content_v")
        es_mock.indices.create.assert_awaited()
        es_mock.indices.put_alias.assert_awaited()
        es_mock.bulk.assert_awaited()

    @pytest.mark.asyncio
    async def test_reindex_catalog_tolerates_catalog_failure(self, es_mock, service):
        catalog = MagicMock()
        catalog.fetch_published = AsyncMock(side_effect=CatalogFetchError("catalog down"))
        es_mock.indices.get_alias = AsyncMock(return_value={"content_v1": {}})

        with pytest.raises(CatalogFetchError):
            await service.reindex_catalog(catalog)

    @pytest.mark.asyncio
    async def test_ensure_index_adopts_orphaned_versioned_index(self, es_mock, service):
        """An orphaned content_v<N> (interrupted reindex) must not 500 startup.

        ensure_index creates the alias instead of failing on create (#227 F1).
        """
        es_mock.indices.get_alias = AsyncMock(side_effect=Exception("NotFound"))
        es_mock.indices.exists = AsyncMock(side_effect=lambda index: index == "content_v1")
        es_mock.indices.create = AsyncMock()

        target = await service.ensure_index()

        assert target == "content_v1"
        es_mock.indices.create.assert_not_awaited()
        es_mock.indices.put_alias.assert_awaited_once_with(index="content_v1", name="content")
        es_mock.cluster.put_settings.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_catalog_empty_returns_no_switch(self, es_mock, service):
        catalog = MagicMock()
        catalog.fetch_published = AsyncMock(return_value=[])
        es_mock.indices.get_alias = AsyncMock(return_value={"content_v1": {}})
        es_mock.indices.delete = AsyncMock()

        result = await service.reindex_catalog(catalog)

        assert result.count == 0
        assert result.switched is False
        es_mock.indices.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_catalog_bulk_failure_raises(self, es_mock, service):
        item_id = uuid4()
        catalog = MagicMock()
        catalog.fetch_published = AsyncMock(
            return_value=[
                {"id": item_id, "title": "X", "description": "d", "content_type": "movie"}
            ]
        )
        es_mock.indices.get_alias = AsyncMock(side_effect=Exception("NotFound"))
        es_mock.indices.exists = AsyncMock(return_value=False)
        es_mock.indices.create = AsyncMock()
        es_mock.indices.put_alias = AsyncMock()
        es_mock.bulk = AsyncMock(
            return_value={
                "errors": True,
                "items": [
                    {"index": {"_id": str(item_id), "error": {"type": "mapper_parsing_exception"}}}
                ],
            }
        )

        with pytest.raises(IndexingError):
            await service.reindex_catalog(catalog)

    @pytest.mark.asyncio
    async def test_delete_content_found(self, es_mock, service):
        es_mock.delete = AsyncMock(return_value={"result": "deleted", "found": True})
        service.index_repo = MagicMock(delete=AsyncMock())

        found = await service.delete_content(uuid4())

        assert found is True
        service.index_repo.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_content_not_found(self, es_mock, service):
        es_mock.delete = AsyncMock(return_value={"result": "not_found", "found": False})
        service.index_repo = MagicMock(delete=AsyncMock())

        found = await service.delete_content(uuid4())

        assert found is False
        service.index_repo.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_index_safeguards(self, service):
        service._alias_target = AsyncMock(return_value="content_v2")
        service.es.indices.delete = AsyncMock()

        # Refuses alias itself
        with pytest.raises(ValueError, match="refusing to delete the search alias"):
            await service.delete_index("content")

        # Refuses currently aliased index
        with pytest.raises(ValueError, match="currently aliased"):
            await service.delete_index("content_v2")

        # Refuses non-versioned name
        with pytest.raises(ValueError, match="only versioned indices"):
            await service.delete_index("random")

        # Allows versioned non-aliased
        await service.delete_index("content_v1")
        service.es.indices.delete.assert_awaited_once_with(index="content_v1")


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

    def test_maps_actors_and_director(self):
        item = {
            "id": uuid4(),
            "title": "Test",
            "description": "",
            "content_type": "movie",
            "cast_members": [
                {"name": "Actor One", "role": "actor"},
                {"name": "Actor Two", "role": "actress"},
                {"name": "Director Name", "role": "director"},
            ],
        }
        doc = content_to_doc(item)
        assert doc["actors"] == ["Actor One", "Actor Two"]
        assert doc["director"] == "Director Name"

    def test_maps_release_year_from_date(self):
        item = {
            "id": uuid4(),
            "title": "Test",
            "description": "",
            "content_type": "movie",
            "release_date": "2023-05-15T00:00:00Z",
        }
        doc = content_to_doc(item)
        assert doc["release_year"] == 2023

    def test_missing_release_date_is_none(self):
        item = {"id": uuid4(), "title": "Test", "description": "", "content_type": "movie"}
        doc = content_to_doc(item)
        assert doc["release_year"] is None
