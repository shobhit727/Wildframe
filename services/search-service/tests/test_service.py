"""Tests for Search Service business logic (indexing, search, trending)."""

from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from elasticsearch import NotFoundError
from elasticsearch.exceptions import ApiError as ElasticsearchApiError

import app.services as services_module


def no_alias_error() -> NotFoundError:
    """Elasticsearch raises NotFoundError when the alias does not exist yet."""
    return NotFoundError(404, "index_not_found_exception", {})


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
    mock.indices.update_aliases = AsyncMock()
    mock.indices.delete = AsyncMock()
    mock.indices.refresh = AsyncMock()
    mock.cluster.put_settings = AsyncMock()
    mock.bulk = AsyncMock(return_value={"errors": False, "items": []})
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
    async def test_search_user_input_cannot_inject_wildcard_or_regexp_clauses(
        self, es_mock, service
    ):
        """#582: wildcard metacharacters in user input must stay plain text.

        The service builds multi_match queries only; a hostile query such as
        ``*`` or ``a* OR b`` must never surface as wildcard/regexp/fuzzy
        clauses that force expensive scans.
        """
        es_mock.search = AsyncMock(return_value={"hits": {"hits": []}})

        for hostile in ("*", "?", "a*", "*:*", "title:~2", "(?i).*"):
            es_mock.search.reset_mock()
            result = await service.search(user_id=None, query=hostile)
            assert isinstance(result, SearchResult)

            body = es_mock.search.await_args.kwargs["body"]
            serialized = str(body)
            for clause in ("wildcard", "regexp", "query_string", "prefix", "fuzzy"):
                assert clause not in serialized, f"query {hostile!r} produced a {clause} clause"
            # Input is carried verbatim inside multi_match (analyzed as text).
            assert body["query"]["bool"]["must"][0]["multi_match"]["query"] == hostile

    @pytest.mark.asyncio
    async def test_search_always_scopes_to_published(self, es_mock, service):
        """#587: the published-only authorization filter is present on every
        search, including cursor-paginated follow-ups."""
        es_mock.search = AsyncMock(return_value={"hits": {"hits": []}})

        await service.search(user_id=None, query="x")
        body = es_mock.search.await_args.kwargs["body"]
        assert {"term": {"status": "published"}} in body["query"]["bool"]["filter"]

        await service.search(user_id=None, query="x", search_after=[5.0, "abc"])
        body = es_mock.search.await_args.kwargs["body"]
        assert {"term": {"status": "published"}} in body["query"]["bool"]["filter"]

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
        es_mock.indices.get_alias = AsyncMock(side_effect=no_alias_error())
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
        es_mock.indices.get_alias = AsyncMock(side_effect=no_alias_error())
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
        es_mock.indices.get_alias = AsyncMock(side_effect=no_alias_error())
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
        service.es.indices.delete.assert_awaited_once_with(
            index="content_v1", ignore_unavailable=True
        )


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

    def test_unparseable_release_date_yields_none(self):
        """A non-ISO release_date must not blow up indexing: year stays None."""
        doc = content_to_doc(
            {"id": "1", "title": "T", "description": "", "content_type": "movie",
             "release_date": "not-a-date"}
        )
        assert doc["release_year"] is None

    def test_genre_falls_back_to_slug_when_name_missing(self):
        doc = content_to_doc(
            {
                "id": "1",
                "title": "T",
                "description": "",
                "content_type": "movie",
                "genres": [{"slug": "noir"}, {"name": "Drama", "slug": "drama"}],
            }
        )
        assert doc["genres"] == ["noir", "Drama"]

    def test_genre_without_name_or_slug_is_dropped(self):
        doc = content_to_doc(
            {"id": "1", "title": "T", "description": "", "content_type": "movie",
             "genres": [{"id": "g1"}]}
        )
        assert doc["genres"] == []

    def test_rating_falls_back_from_audience_to_imdb_then_zero(self):
        base = {"id": "1", "title": "T", "description": "", "content_type": "movie"}
        assert content_to_doc({**base, "imdb_rating": 7.5})["rating"] == 7.5
        assert content_to_doc({**base, "audience_score": 0, "imdb_rating": 7.5})["rating"] == 7.5
        assert content_to_doc(base)["rating"] == 0.0

    def test_defaults_for_missing_optional_fields(self):
        doc = content_to_doc({"id": "1"})
        assert doc == {
            "title": "",
            "description": "",
            "content_type": "",
            "genres": [],
            "actors": [],
            "director": "",
            "release_year": None,
            "rating": 0.0,
            "status": "published",
        }

    def test_director_defaults_to_empty_without_director_cast(self):
        doc = content_to_doc(
            {
                "id": "1",
                "title": "T",
                "description": "",
                "content_type": "movie",
                "cast_members": [{"name": "A", "role": "Actor"}],
            }
        )
        assert doc["actors"] == ["A"]
        assert doc["director"] == ""

    def test_role_matching_is_case_insensitive(self):
        doc = content_to_doc(
            {
                "id": "1",
                "title": "T",
                "description": "",
                "content_type": "movie",
                "cast_members": [
                    {"name": "A", "role": "ACTRESS"},
                    {"name": "D", "role": "Director"},
                    {"name": "X", "role": "writer"},
                ],
            }
        )
        assert doc["actors"] == ["A"]
        assert doc["director"] == "D"


class TestValueTypes:
    """The exported value objects carry the documented contract."""

    def test_indexing_error_defaults_failed_to_empty_list(self):
        err = IndexingError("boom")
        assert err.failed == []
        assert str(err) == "boom"

    def test_indexing_error_keeps_failed_documents(self):
        err = IndexingError("boom", failed=[{"_id": "x", "error": {"type": "t"}}])
        assert err.failed == [{"_id": "x", "error": {"type": "t"}}]

    def test_search_result_is_immutable(self):
        result = SearchResult(results=[{"title": "A"}], next_sort=[1.0, "id"])
        assert result.results == [{"title": "A"}]
        assert result.next_sort == [1.0, "id"]
        with pytest.raises(FrozenInstanceError):
            result.next_sort = None  # type: ignore[misc]

    def test_reindex_result_is_immutable(self):
        result = ReindexResult(count=2, index_name="content_v3", switched=True)
        assert (result.count, result.index_name, result.switched) == (2, "content_v3", True)
        with pytest.raises(FrozenInstanceError):
            result.switched = False  # type: ignore[misc]

    def test_chunks_splits_into_fixed_size_slices(self):
        assert list(services_module._chunks([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
        assert list(services_module._chunks([], 3)) == []

    def test_is_versioned_index(self):
        assert SearchService._is_versioned_index("content_v1") is True
        assert SearchService._is_versioned_index("content_v") is False
        assert SearchService._is_versioned_index("content") is False


class TestSearchErrorBranches:
    """Both ES failure classes degrade to an empty page, never a 500."""

    @pytest.mark.asyncio
    async def test_elasticsearch_api_error_returns_empty_result(self, es_mock, service):
        es_mock.search = AsyncMock(
            side_effect=ElasticsearchApiError(400, "parsing_exception", {})
        )

        result = await service.search(user_id=None, query="x")

        assert result == SearchResult(results=[], next_sort=None)
        es_mock.search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_transport_error_returns_empty_result(self, es_mock, service):
        es_mock.search = AsyncMock(side_effect=ConnectionError("es down"))

        result = await service.search(user_id=None, query="x")

        assert result == SearchResult(results=[], next_sort=None)

    @pytest.mark.asyncio
    async def test_trending_scopes_by_content_type(self, es_mock, service):
        es_mock.search = AsyncMock(return_value={"hits": {"hits": []}})

        await service.trending(content_type="show", limit=3)

        body = es_mock.search.await_args.kwargs["body"]
        assert body["size"] == 3
        assert body["query"]["bool"]["must"] == [
            {"match_all": {}},
            {"term": {"content_type": "show"}},
        ]

    @pytest.mark.asyncio
    async def test_trending_returns_sources_on_success(self, es_mock, service):
        es_mock.search = AsyncMock(
            return_value={"hits": {"hits": [{"_source": {"title": "B"}}, {"_source": {"title": "A"}}]}}
        )

        assert await service.trending() == [{"title": "B"}, {"title": "A"}]


class TestIndexContentErrorPaths:
    """SQL mirror is written first; ES failure must never leave a ghost row."""

    @pytest.mark.asyncio
    async def test_sql_upsert_failure_aborts_before_es(self, es_mock, service, index_repo):
        es_mock.index = AsyncMock()
        index_repo.upsert = AsyncMock(side_effect=RuntimeError("sql down"))

        with pytest.raises(RuntimeError, match="sql down"):
            await service.index_content(uuid4(), "T", "D", "movie")

        es_mock.index.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_es_failure_compensates_by_deleting_sql_row(
        self, es_mock, service, index_repo
    ):
        content_id = uuid4()
        es_mock.index = AsyncMock(side_effect=RuntimeError("es down"))

        with pytest.raises(RuntimeError, match="es down"):
            await service.index_content(content_id, "T", "D", "movie", genres=["drama"])

        index_repo.delete.assert_awaited_once_with(content_id)

    @pytest.mark.asyncio
    async def test_compensation_delete_failure_still_reraises_es_error(
        self, es_mock, service, index_repo
    ):
        """A failed compensation must not mask the original ES failure."""
        content_id = uuid4()
        es_mock.index = AsyncMock(side_effect=RuntimeError("es down"))
        index_repo.delete = AsyncMock(side_effect=RuntimeError("compensation down"))

        with pytest.raises(RuntimeError, match="es down"):
            await service.index_content(content_id, "T", "D", "movie")

        index_repo.delete.assert_awaited_once_with(content_id)

    @pytest.mark.asyncio
    async def test_index_content_sends_canonical_document(self, es_mock, service):
        es_mock.index = AsyncMock()
        content_id = uuid4()

        await service.index_content(
            content_id, "T", "D", "movie", genres=["drama"], rating=8.5
        )

        call = es_mock.index.await_args.kwargs
        assert call["index"] == "content"
        assert call["id"] == str(content_id)
        assert call["document"] == {
            "id": str(content_id),
            "title": "T",
            "description": "D",
            "content_type": "movie",
            "genres": ["drama"],
            "rating": 8.5,
        }


class TestBulkIndexRetry:
    @pytest.mark.asyncio
    async def test_no_failures_issues_single_bulk_call(self, es_mock, service):
        es_mock.bulk = AsyncMock(return_value={"errors": False, "items": []})

        await service._bulk_index("content_v2", [{"id": "1", "title": "A"}])

        assert es_mock.bulk.await_count == 1
        operations = es_mock.bulk.await_args.kwargs["operations"]
        assert operations[0] == {"index": {"_index": "content_v2", "_id": "1"}}
        assert operations[1]["title"] == "A"

    @pytest.mark.asyncio
    async def test_transient_failure_is_retried_once_and_succeeds(self, es_mock, service):
        es_mock.bulk = AsyncMock(
            side_effect=[
                {
                    "errors": True,
                    "items": [{"index": {"_id": "1", "error": {"type": "es_rejected"}}}],
                },
                {"errors": False, "items": []},
            ]
        )

        await service._bulk_index("content_v2", [{"id": "1", "title": "A"}])

        assert es_mock.bulk.await_count == 2
        retry_ops = es_mock.bulk.await_args_list[1].kwargs["operations"]
        assert retry_ops == [
            {"index": {"_index": "content_v2", "_id": "1"}},
            {"id": "1", "title": "A", "description": "", "content_type": "",
             "genres": [], "actors": [], "director": "", "release_year": None,
             "rating": 0.0, "status": "published"},
        ]

    @pytest.mark.asyncio
    async def test_retry_retries_only_the_failed_documents(self, es_mock, service):
        es_mock.bulk = AsyncMock(
            side_effect=[
                {
                    "errors": True,
                    "items": [{"index": {"_id": "2", "error": {"type": "es_rejected"}}}],
                },
                {"errors": False, "items": []},
            ]
        )

        await service._bulk_index(
            "content_v2", [{"id": "1", "title": "A"}, {"id": "2", "title": "B"}]
        )

        retry_ops = es_mock.bulk.await_args_list[1].kwargs["operations"]
        assert [op for op in retry_ops if "index" in op] == [
            {"index": {"_index": "content_v2", "_id": "2"}}
        ]

    @pytest.mark.asyncio
    async def test_repeated_failure_raises_indexing_error_with_payload(self, es_mock, service):
        failure = {"_id": "1", "error": {"type": "es_rejected"}}
        es_mock.bulk = AsyncMock(
            side_effect=[{"errors": True, "items": [{"index": failure}]}] * 2
        )

        with pytest.raises(IndexingError) as excinfo:
            await service._bulk_index("content_v2", [{"id": "1", "title": "A"}])

        assert "1 documents failed to index after retry" in str(excinfo.value)
        assert excinfo.value.failed == [failure]
        assert es_mock.bulk.await_count == 2

    def test_collect_bulk_failures_ignores_successful_items(self, service):
        failures = service._collect_bulk_failures(
            {
                "errors": True,
                "items": [
                    {"index": {"_id": "ok"}},
                    {"delete": {"_id": "other"}},
                    {"index": {"_id": "bad", "error": {"type": "mapper_parsing"}}},
                ],
            }
        )
        assert failures == [{"_id": "bad", "error": {"type": "mapper_parsing"}}]

    def test_collect_bulk_failures_returns_empty_without_error_flag(self, service):
        assert service._collect_bulk_failures({"errors": False, "items": []}) == []
        assert service._collect_bulk_failures({}) == []


class TestCleanupAndShutdown:
    @pytest.mark.asyncio
    async def test_cleanup_index_deletes_with_ignore_unavailable(self, es_mock, service):
        await service._cleanup_index("content_v9")

        es_mock.indices.delete.assert_awaited_once_with(
            index="content_v9", ignore_unavailable=True
        )

    @pytest.mark.asyncio
    async def test_cleanup_index_swallows_delete_failure(self, es_mock, service):
        es_mock.indices.delete = AsyncMock(side_effect=RuntimeError("es down"))

        await service._cleanup_index("content_v9")  # must not raise

        es_mock.indices.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_flush_closes_the_es_client(self, es_mock, service):
        es_mock.close = AsyncMock()

        await service.flush()

        es_mock.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_content_treats_404_as_absent(self, es_mock, service):
        es_mock.delete = AsyncMock(side_effect=no_alias_error())
        index_repo = MagicMock(delete=AsyncMock())
        service.index_repo = index_repo

        found = await service.delete_content(uuid4())

        assert found is False
        index_repo.delete.assert_awaited_once()


# ----------------------------------------------------------------------
# ContentCatalogClient — outbound HTTP to content-service
# ----------------------------------------------------------------------


def _catalog_with_transport(handler) -> services_module.ContentCatalogClient:
    """Build a ContentCatalogClient whose transport is fully in-process.

    ``__init__`` still runs (it is what builds the real AsyncClient), but the
    client is immediately swapped for one backed by ``httpx.MockTransport`` so
    no socket is ever opened.
    """
    catalog = services_module.ContentCatalogClient(base_url="http://content.test")
    return catalog


async def _retarget(catalog: services_module.ContentCatalogClient, handler):
    await catalog.client.aclose()
    catalog.client = httpx.AsyncClient(
        base_url="http://content.test", transport=httpx.MockTransport(handler)
    )
    return catalog


class TestContentCatalogClient:
    @pytest.mark.asyncio
    async def test_strips_trailing_slash_from_base_url(self):
        catalog = services_module.ContentCatalogClient(base_url="http://content.test/")
        assert catalog.base_url == "http://content.test"
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_paginates_and_enriches_every_item(self):
        seen_pages: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/content":
                page = int(request.url.params["page"])
                seen_pages.append(request.url.params["page"])
                if page == 1:
                    return httpx.Response(
                        200, json=[{"id": "1", "title": "A"}, {"id": "2", "title": "B"}]
                    )
                return httpx.Response(200, json=[{"id": "3", "title": "C"}])
            content_id = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"genres": [{"name": f"g{content_id}"}]})

        catalog = await _retarget(
            services_module.ContentCatalogClient(base_url="http://content.test"), handler
        )

        items = await catalog.fetch_published(page_size=2)

        assert seen_pages == ["1", "2"]
        assert [i["id"] for i in items] == ["1", "2", "3"]
        # Detail data is merged over the list payload.
        assert items[0]["genres"] == [{"name": "g1"}]
        assert items[0]["title"] == "A"
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_list_request_asks_for_published_only(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, json=[])

        catalog = await _retarget(
            services_module.ContentCatalogClient(base_url="http://content.test"), handler
        )

        await catalog.fetch_published(page_size=7)

        assert captured["status"] == "published"
        assert captured["page_size"] == "7"
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_list_http_error_becomes_catalog_fetch_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="unavailable")

        catalog = await _retarget(
            services_module.ContentCatalogClient(base_url="http://content.test"), handler
        )

        with pytest.raises(CatalogFetchError, match="catalog list failed"):
            await catalog.fetch_published()
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_declared_oversized_body_is_refused_before_reading(self, monkeypatch):
        monkeypatch.setattr(services_module, "MAX_UPSTREAM_BODY_BYTES", 10)
        sent: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return httpx.Response(
                200, content=b"x" * 50, headers={"content-length": "50"}
            )

        catalog = await _retarget(
            services_module.ContentCatalogClient(base_url="http://content.test"), handler
        )

        with pytest.raises(CatalogFetchError, match="upstream response too large: 50"):
            await catalog.fetch_published()
        assert len(sent) == 1
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_undeclared_oversized_body_is_refused_after_reading(self, monkeypatch):
        """A missing/lying content-length must not bypass the size ceiling."""
        monkeypatch.setattr(services_module, "MAX_UPSTREAM_BODY_BYTES", 10)

        def handler(request: httpx.Request) -> httpx.Response:
            # A streamed response carries no content-length header, so the
            # header guard cannot fire and only the post-read check can save us.
            return httpx.Response(200, stream=httpx.ByteStream(b"x" * 50))

        catalog = await _retarget(
            services_module.ContentCatalogClient(base_url="http://content.test"), handler
        )

        with pytest.raises(CatalogFetchError, match="50 bytes"):
            await catalog.fetch_published()
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_malformed_json_becomes_catalog_fetch_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json")

        catalog = await _retarget(
            services_module.ContentCatalogClient(base_url="http://content.test"), handler
        )

        with pytest.raises(CatalogFetchError, match="malformed upstream JSON"):
            await catalog.fetch_published()
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_unexpected_parse_failure_is_wrapped_by_the_caller(self, monkeypatch):
        """Defensive: a non-CatalogFetchError from the parser still surfaces as one."""

        async def boom(resp):
            raise ValueError("parser exploded")

        monkeypatch.setattr(services_module, "_bounded_json", boom)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        catalog = await _retarget(
            services_module.ContentCatalogClient(base_url="http://content.test"), handler
        )

        with pytest.raises(CatalogFetchError, match="parser exploded"):
            await catalog.fetch_published()
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_detail_http_error_names_the_content_id(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/content":
                return httpx.Response(200, json=[{"id": "abc", "title": "A"}])
            return httpx.Response(500, text="boom")

        catalog = await _retarget(
            services_module.ContentCatalogClient(base_url="http://content.test"), handler
        )

        with pytest.raises(CatalogFetchError, match="detail fetch for abc failed"):
            await catalog.fetch_published()
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_non_object_detail_is_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/content":
                return httpx.Response(200, json=[{"id": "abc", "title": "A"}])
            return httpx.Response(200, json=["not", "an", "object"])

        catalog = await _retarget(
            services_module.ContentCatalogClient(base_url="http://content.test"), handler
        )

        with pytest.raises(CatalogFetchError, match="is not a JSON object"):
            await catalog.fetch_published()
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_malformed_detail_json_is_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/content":
                return httpx.Response(200, json=[{"id": "abc", "title": "A"}])
            return httpx.Response(200, content=b"{oops")

        catalog = await _retarget(
            services_module.ContentCatalogClient(base_url="http://content.test"), handler
        )

        with pytest.raises(CatalogFetchError, match="malformed upstream JSON"):
            await catalog.fetch_published()
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_unexpected_detail_parse_failure_is_wrapped(self, monkeypatch):
        """Defensive: a non-CatalogFetchError from the detail parser still surfaces."""
        original = services_module._bounded_json
        seen: list[int] = []

        async def boom(resp):
            seen.append(1)
            if len(seen) == 1:  # the list page parses fine
                return await original(resp)
            raise ValueError("detail parser exploded")

        monkeypatch.setattr(services_module, "_bounded_json", boom)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/content":
                return httpx.Response(200, json=[{"id": "abc", "title": "A"}])
            return httpx.Response(200, json={"genres": []})

        catalog = await _retarget(
            services_module.ContentCatalogClient(base_url="http://content.test"), handler
        )

        with pytest.raises(CatalogFetchError, match="malformed detail for abc: detail parser exploded"):
            await catalog.fetch_published()
        await catalog.aclose()

    @pytest.mark.asyncio
    async def test_bounded_json_returns_parsed_body(self):
        async def read():
            return b'[{"a": 1}]'

        resp = MagicMock()
        resp.headers = {}
        resp.aread = AsyncMock(side_effect=read)

        assert await services_module._bounded_json(resp) == [{"a": 1}]

    @pytest.mark.asyncio
    async def test_bounded_json_ignores_non_numeric_content_length(self):
        async def read():
            return b'{"a": 1}'

        resp = MagicMock()
        resp.headers = {"content-length": "not-a-number"}
        resp.aread = AsyncMock(side_effect=read)

        assert await services_module._bounded_json(resp) == {"a": 1}
