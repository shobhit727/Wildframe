"""Kafka content-sync consumer: event application and consumer loop.

The consumer is driven by an injected fake ``AIOKafkaConsumer`` so the loop's
commit/stop semantics are observable without a broker, and ``_handle`` is
driven with injected collaborators so the applied index mutations are exact.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_consumer import CONSUMER_GROUP, TOPICS, _handle, run_content_sync_consumer

CATALOG_URL = "http://content.test"


def no_alias_error():
    from elasticsearch import NotFoundError

    return NotFoundError(404, "index_not_found_exception", {})


@pytest.fixture
def search_service():
    service = MagicMock()
    service.index_content = AsyncMock()
    service.delete_content = AsyncMock(return_value=True)
    return service


@pytest.fixture
def catalog():
    client = MagicMock()
    client._fetch_detail = AsyncMock(
        return_value={
            "title": "Blade",
            "description": "half vampire",
            "content_type": "movie",
            "genres": [{"name": "Action"}],
            "cast_members": [{"name": "A", "role": "actor"}],
            "release_date": "2023-05-15T00:00:00Z",
            "audience_score": 84.0,
        }
    )
    client.aclose = AsyncMock()
    return client


# ----------------------------------------------------------------------
# _handle — per-topic behaviour
# ----------------------------------------------------------------------


class TestHandlePublished:
    @pytest.mark.asyncio
    async def test_published_event_indexes_the_canonical_document(
        self, catalog, search_service
    ):
        content_id = str(uuid4())

        await _handle(
            catalog,
            search_service,
            {"topic": "content.published", "payload": {"content_id": content_id}},
        )

        catalog._fetch_detail.assert_awaited_once_with(content_id)
        args, kwargs = search_service.index_content.await_args
        assert args == (UUID(content_id),)
        assert kwargs["title"] == "Blade"
        assert kwargs["description"] == "half vampire"
        assert kwargs["content_type"] == "movie"
        assert kwargs["genres"] == ["Action"]
        assert kwargs["actors"] == ["A"]
        assert kwargs["director"] == ""
        assert kwargs["release_year"] == 2023
        assert kwargs["rating"] == 84.0
        assert kwargs["status"] == "published"

    @pytest.mark.asyncio
    async def test_published_event_falls_back_to_defaults_on_a_sparse_doc(
        self, catalog, search_service
    ):
        content_id = str(uuid4())
        catalog._fetch_detail = AsyncMock(return_value={})

        await _handle(
            catalog,
            search_service,
            {"topic": "content.published", "payload": {"content_id": content_id}},
        )

        _, kwargs = search_service.index_content.await_args
        assert kwargs["title"] == ""
        assert kwargs["description"] == ""
        # ``content_to_doc`` always emits the key, so the ``"movie"`` default in
        # ``doc.get("content_type", "movie")`` can never fire: a sparse detail
        # payload lands as an empty content_type.
        assert kwargs["content_type"] == ""
        assert kwargs["status"] == "published"

    @pytest.mark.asyncio
    async def test_bare_event_document_is_used_as_the_payload(self, catalog, search_service):
        """Older producers send the payload at the top level, without a wrapper."""
        from uuid import uuid4

        content_id = str(uuid4())

        await _handle(
            catalog,
            search_service,
            {"topic": "content.published", "content_id": content_id},
        )

        search_service.index_content.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_detail_fetch_failure_propagates_to_the_caller(
        self, catalog, search_service
    ):
        from app.services import CatalogFetchError

        content_id = str(uuid4())
        catalog._fetch_detail = AsyncMock(side_effect=CatalogFetchError("gone"))

        with pytest.raises(CatalogFetchError):
            await _handle(
                catalog,
                search_service,
                {"topic": "content.published", "payload": {"content_id": content_id}},
            )

        search_service.index_content.assert_not_awaited()


class TestHandleRemoval:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("topic", ["content.deleted", "content.unpublished"])
    async def test_removal_topics_delete_the_document(self, topic, search_service, catalog):
        content_id = str(uuid4())

        await _handle(catalog, search_service, {"topic": topic, "payload": {"content_id": content_id}})

        search_service.delete_content.assert_awaited_once_with(UUID(content_id))
        search_service.index_content.assert_not_awaited()
        catalog._fetch_detail.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_topic_is_ignored(self, search_service, catalog):
        await _handle(catalog, search_service, {"topic": "content.updated", "payload": {"content_id": "x"}})

        search_service.delete_content.assert_not_awaited()
        search_service.index_content.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_event_without_content_id_is_dropped(self, search_service, catalog):
        await _handle(catalog, search_service, {"topic": "content.published", "payload": {}})

        catalog._fetch_detail.assert_not_awaited()
        search_service.index_content.assert_not_awaited()
        search_service.delete_content.assert_not_awaited()


# ----------------------------------------------------------------------
# run_content_sync_consumer — the loop
# ----------------------------------------------------------------------


class _Msg:
    def __init__(self, topic: str, partition: int, offset: int, value: bytes):
        self.topic = topic
        self.partition = partition
        self.offset = offset
        self.value = value


class _TopicPartition:
    """Stand-in for ``aiokafka.structs.TopicPartition`` (compared by value)."""

    def __init__(self, topic: str, partition: int):
        self.topic = topic
        self.partition = partition

    def __eq__(self, other):
        return (self.topic, self.partition) == (other.topic, other.partition)

    def __hash__(self):
        return hash((self.topic, self.partition))

    def __repr__(self):
        return f"TopicPartition({self.topic!r}, {self.partition})"


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


class _SessionFactory:
    def __init__(self):
        self.sessions: list[MagicMock] = []

    def begin(self):
        session = MagicMock(spec=AsyncSession)
        self.sessions.append(session)
        return _SessionContext(session)


class _FakeConsumer:
    """Fake ``AIOKafkaConsumer`` that replays a fixed message list."""

    instances: list["_FakeConsumer"] = []

    def __init__(self, *topics, **kwargs):
        self.topics = topics
        self.kwargs = kwargs
        self.messages: list[_Msg] = []
        self.started = False
        self.stopped = False
        self.commits: list[dict] = []
        self._fail_on_start: Exception | None = None
        _FakeConsumer.instances.append(self)

    async def start(self):
        if self._fail_on_start is not None:
            raise self._fail_on_start
        self.started = True

    async def stop(self):
        self.stopped = True

    async def commit(self, offsets):
        self.commits.append(offsets)

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for message in self.messages:
            yield message


@pytest.fixture
def consumer_env(monkeypatch):
    """Wire the consumer loop onto fakes: ES, DB, catalog and Kafka."""
    import aiokafka
    import aiokafka.structs
    import app.core.database as database_mod
    import app.services as services_mod

    built: list[_FakeConsumer] = []

    def build(*topics, **kwargs):
        consumer = _FakeConsumer(*topics, **kwargs)
        built.append(consumer)
        return consumer

    # ``run_content_sync_consumer`` imports AIOKafkaConsumer *inside* the
    # function, so the real ``aiokafka`` module attribute is the seam.
    monkeypatch.setattr(aiokafka, "AIOKafkaConsumer", build)
    monkeypatch.setattr(aiokafka.structs, "TopicPartition", _TopicPartition)

    factory = _SessionFactory()
    monkeypatch.setattr(
        database_mod.DatabaseManager, "session_factory", factory, raising=False
    )

    catalog = MagicMock()
    catalog.fetch_published = AsyncMock(return_value=[])
    catalog._fetch_detail = AsyncMock(return_value={"title": "T", "content_type": "movie"})
    catalog.aclose = AsyncMock()
    monkeypatch.setattr(services_mod, "ContentCatalogClient", MagicMock(return_value=catalog))

    es = MagicMock()
    es.index = AsyncMock()
    es.delete = AsyncMock(return_value={"result": "deleted"})

    return MagicMock(es=es, catalog=catalog, factory=factory, built=built)


def _envelope(topic: str, payload: dict, partition: int = 0, offset: int = 0) -> _Msg:
    """A Kafka message whose value is a real ``DomainEvent`` serialization."""
    from wildframe_events import DomainEvent

    event = DomainEvent(topic=topic, key=f"key:{payload.get('content_id')}", payload=payload)
    return _Msg(topic, partition, offset, event.to_json().encode("utf-8"))


class TestConsumerLoop:
    @pytest.mark.asyncio
    async def test_consumes_indexes_and_commits_offsets(self, consumer_env):
        import aiokafka

        content_id = str(uuid4())
        es_index, es_delete = consumer_env.es.index, consumer_env.es.delete

        def capture_first(*topics, **kwargs):
            consumer = _FakeConsumer(*topics, **kwargs)
            consumer.messages = [
                _envelope("content.published", {"content_id": content_id}, offset=41),
                _envelope("content.deleted", {"content_id": content_id}, offset=42),
            ]
            consumer_env.built.append(consumer)
            return consumer

        consumer_env.built.clear()
        with pytest.MonkeyPatch.context() as ctx:
            ctx.setattr(aiokafka, "AIOKafkaConsumer", capture_first)
            await run_content_sync_consumer(consumer_env.es)

        consumer = consumer_env.built[-1]
        assert consumer.started is True
        assert consumer.stopped is True
        assert consumer.kwargs["bootstrap_servers"] == "kafka:29092"
        assert consumer.kwargs["group_id"] == CONSUMER_GROUP
        assert consumer.kwargs["enable_auto_commit"] is False
        assert consumer.kwargs["auto_offset_reset"] == "earliest"
        assert consumer.topics == TOPICS

        # Offsets are committed one past the processed message, per partition.
        committed = [
            (next(iter(offsets)).topic, next(iter(offsets)).partition, list(offsets.values())[0])
            for offsets in consumer.commits
        ]
        assert committed == [
            ("content.published", 0, 42),
            ("content.deleted", 0, 43),
        ]
        assert es_index.await_count == 1
        assert es_delete.await_count == 1
        assert len(consumer_env.factory.sessions) == 2
        consumer_env.catalog.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_handler_failure_stops_the_loop_before_committing(self, consumer_env):
        """A later commit must never skip an event that was not applied."""
        content_id = str(uuid4())
        consumer_env.catalog._fetch_detail = AsyncMock(
            side_effect=RuntimeError("detail fetch exploded")
        )
        import aiokafka

        def capture(*topics, **kwargs):
            consumer = _FakeConsumer(*topics, **kwargs)
            consumer.messages = [
                _envelope("content.published", {"content_id": content_id}, offset=7),
            ]
            consumer_env.built.append(consumer)
            return consumer

        with pytest.MonkeyPatch.context() as ctx:
            ctx.setattr(aiokafka, "AIOKafkaConsumer", capture)
            await run_content_sync_consumer(consumer_env.es)  # must not raise

        consumer = consumer_env.built[-1]
        assert consumer.commits == []
        assert consumer.stopped is True
        consumer_env.es.index.assert_not_awaited()
        consumer_env.catalog.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_failure_is_swallowed_and_catalog_is_closed(self, consumer_env):
        import aiokafka

        def capture(*topics, **kwargs):
            consumer = _FakeConsumer(*topics, **kwargs)
            consumer._fail_on_start = RuntimeError("broker down")
            consumer_env.built.append(consumer)
            return consumer

        with pytest.MonkeyPatch.context() as ctx:
            ctx.setattr(aiokafka, "AIOKafkaConsumer", capture)
            await run_content_sync_consumer(consumer_env.es)  # must not raise

        consumer = consumer_env.built[-1]
        assert consumer.started is False
        assert consumer.stopped is True
        consumer_env.catalog.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_failure_does_not_mask_the_original_error(self, consumer_env):
        import aiokafka

        def capture(*topics, **kwargs):
            consumer = _FakeConsumer(*topics, **kwargs)
            consumer._fail_on_start = RuntimeError("broker down")

            async def boom():
                raise RuntimeError("stop failed")

            consumer.stop = boom
            consumer_env.built.append(consumer)
            return consumer

        with pytest.MonkeyPatch.context() as ctx:
            ctx.setattr(aiokafka, "AIOKafkaConsumer", capture)
            await run_content_sync_consumer(consumer_env.es)  # must not raise

        consumer_env.catalog.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_kafka_bootstrap_is_read_from_the_environment(self, consumer_env, monkeypatch):
        import aiokafka

        monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "broker:19092")

        def capture(*topics, **kwargs):
            consumer = _FakeConsumer(*topics, **kwargs)
            consumer_env.built.append(consumer)
            return consumer

        with pytest.MonkeyPatch.context() as ctx:
            ctx.setattr(aiokafka, "AIOKafkaConsumer", capture)
            await run_content_sync_consumer(consumer_env.es)

        assert consumer_env.built[-1].kwargs["bootstrap_servers"] == "broker:19092"

    @pytest.mark.asyncio
    async def test_missing_aiokafka_disables_the_consumer(self, consumer_env, monkeypatch):
        """Without the Kafka client installed the task must exit quietly."""
        import sys

        monkeypatch.setitem(sys.modules, "aiokafka", None)
        consumer_env.catalog.aclose.assert_not_awaited()

        await run_content_sync_consumer(consumer_env.es)  # must not raise

        assert consumer_env.built == []
        consumer_env.catalog.aclose.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_session_factory_is_a_programming_error(self, consumer_env, monkeypatch):
        """The guard fires before a broker client or catalog client is built."""
        import aiokafka
        import app.core.database as database_mod
        import app.services as services_mod

        monkeypatch.setattr(database_mod.DatabaseManager, "session_factory", None)
        catalog_factory = services_mod.ContentCatalogClient

        def capture(*topics, **kwargs):
            # Reached only if the guard is removed; the assert below then fails.
            consumer_env.built.append(_FakeConsumer(*topics, **kwargs))
            return consumer_env.built[-1]

        with pytest.MonkeyPatch.context() as ctx:
            ctx.setattr(aiokafka, "AIOKafkaConsumer", capture)
            with pytest.raises(AssertionError, match="session_factory not initialized"):
                await run_content_sync_consumer(consumer_env.es)

        assert consumer_env.built == []
        assert services_mod.ContentCatalogClient is catalog_factory
        consumer_env.catalog.aclose.assert_not_awaited()
