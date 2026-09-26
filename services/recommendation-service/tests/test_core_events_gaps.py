"""Coverage gaps in ``app/core/events.py`` for recommendation-service.

Focus areas the existing suite does not reach: the "database not initialised"
drop paths in both eviction handlers, the Redis dedup-store construction
failure, the subscriber start/stop error paths, and the cache invalidation
contract the handlers depend on.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from wildframe_events import DomainEvent, InMemoryEventPublisher, InMemoryEventSubscriber

from app.core import events as events_mod


@pytest.fixture(autouse=True)
def _fresh_subscriber():
    events_mod.reset_event_subscriber()
    yield
    events_mod.reset_event_subscriber()


def _event(topic: str, payload: dict) -> DomainEvent:
    return DomainEvent(
        topic=topic, key=f"key:{payload.get('content_id') or payload.get('user_id')}",
        payload=payload,
        producer="content-service",
    )


class _Session:
    """Async session scope; awaited repo calls need a spec'd session."""

    def __init__(self, store: list):
        self._session = MagicMock()
        self._session.commit = AsyncMock()
        self._session.rollback = AsyncMock()
        store.append(self)

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


class _Factory:
    def __init__(self):
        self.sessions: list = []

    def __call__(self):
        return _Session(self.sessions)


# ----------------------------------------------------------------------
# _handle_content_gone: uninitialised database
# ----------------------------------------------------------------------


class TestContentGoneWithoutDatabase:
    @pytest.mark.asyncio
    async def test_event_is_dropped_when_the_database_is_uninitialised(self, monkeypatch):
        """Dropping is correct: the row cannot be evicted, and nothing is raised."""
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", None)
        repo_cls = MagicMock()
        monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)

        await events_mod._handle_content_deleted(_event("content.deleted", {"content_id": str(uuid4())}))

        repo_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_unpublished_event_is_dropped_without_a_database(self, monkeypatch):
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", None)
        repo_cls = MagicMock()
        monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)

        await events_mod._handle_content_unpublished(
            _event("content.unpublished", {"content_id": str(uuid4())})
        )

        repo_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_cache_invalidation_happens_for_a_dropped_event(self, monkeypatch):
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", None)
        invalidate = AsyncMock()
        monkeypatch.setattr(events_mod, "_cache_invalidate", invalidate)

        await events_mod._handle_content_deleted(_event("content.deleted", {"content_id": str(uuid4())}))

        invalidate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_affected_user_list_skips_cache_invalidation(self, monkeypatch):
        factory = _Factory()
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", factory)
        repo_cls = MagicMock()
        repo_cls.return_value.delete_for_content = AsyncMock(return_value=[])
        monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)
        invalidate = AsyncMock()
        monkeypatch.setattr(events_mod, "_cache_invalidate", invalidate)

        await events_mod._handle_content_deleted(_event("content.deleted", {"content_id": str(uuid4())}))

        invalidate.assert_not_awaited()
        # The transaction is still committed for the delete itself.
        assert factory.sessions[0]._session.commit.await_count == 1

    @pytest.mark.asyncio
    async def test_a_commit_failure_propagates(self, monkeypatch):
        factory = _Factory()
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", factory)
        repo_cls = MagicMock()
        repo_cls.return_value.delete_for_content = AsyncMock(return_value=[uuid4()])
        monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)

        original_enter = _Session.__aenter__

        async def failing_enter(self):
            session = await original_enter(self)
            session.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
            return session

        monkeypatch.setattr(_Session, "__aenter__", failing_enter)

        with pytest.raises(RuntimeError, match="commit failed"):
            await events_mod._handle_content_deleted(
                _event("content.deleted", {"content_id": str(uuid4())})
            )


# ----------------------------------------------------------------------
# _handle_billing_subscription_change: uninitialised database
# ----------------------------------------------------------------------


class TestBillingWithoutDatabase:
    @pytest.mark.asyncio
    async def test_billing_event_is_dropped_when_the_database_is_uninitialised(self, monkeypatch):
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", None)
        repo_cls = MagicMock()
        monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)
        invalidate = AsyncMock()
        monkeypatch.setattr(events_mod, "_cache_invalidate", invalidate)

        await events_mod._handle_billing_subscription_change(
            _event("billing.subscription.cancelled", {"user_id": str(uuid4())})
        )

        repo_cls.assert_not_called()
        invalidate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_billing_eviction_runs_for_every_lifecycle_topic(self, monkeypatch):
        factory = _Factory()
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", factory)
        repo_cls = MagicMock()
        repo_cls.return_value.clear_for_user = AsyncMock(return_value=0)
        monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)
        invalidate = AsyncMock()
        monkeypatch.setattr(events_mod, "_cache_invalidate", invalidate)
        user_id = uuid4()

        for topic in (
            "billing.subscription.created",
            "billing.subscription.updated",
            "billing.subscription.cancelled",
        ):
            await events_mod._handle_billing_subscription_change(
                _event(topic, {"user_id": str(user_id)})
            )

        assert repo_cls.return_value.clear_for_user.await_count == 3
        assert [c.args[0] for c in invalidate.await_args_list] == [user_id] * 3
        assert len(factory.sessions) == 3

    @pytest.mark.asyncio
    async def test_billing_does_not_evict_content_rows(self, monkeypatch):
        """Billing changes clear rows per *user*, never per *content*."""
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _Factory())
        repo_cls = MagicMock()
        repo_cls.return_value.clear_for_user = AsyncMock(return_value=1)
        monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)

        await events_mod._handle_billing_subscription_change(
            _event("billing.subscription.created", {"user_id": str(uuid4())})
        )

        repo_cls.return_value.delete_for_content.assert_not_called()


# ----------------------------------------------------------------------
# Transport selection
# ----------------------------------------------------------------------


class TestTransportSelection:
    def test_kafka_subscriber_uses_the_service_consumer_group(self, monkeypatch):
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
        monkeypatch.setattr(settings, "KAFKA_CONSUMER_GROUP", "rec-workers")
        monkeypatch.setattr(settings, "REDIS_URL", "redis://redis:6379/3")

        with (
            patch.object(events_mod, "RedisDeduplicationStore") as dedup,
            patch.object(events_mod, "KafkaEventSubscriber") as ksub,
            patch("wildframe_events.KafkaEventPublisher") as kpub,
        ):
            sub = events_mod.get_event_subscriber()

        assert sub is ksub.return_value
        dedup.assert_called_once_with(
            redis_url="redis://redis:6379/3", key_prefix="wf:dedup:rec"
        )
        assert ksub.call_args.kwargs["group_id"] == "rec-workers"
        assert ksub.call_args.kwargs["client_id"] == "rec-workers"
        assert kpub.call_args.kwargs["client_id"] == "rec-workers-dlq"
        assert kpub.call_args.kwargs["bootstrap_servers"] == "kafka:29092"

    def test_redis_dedup_failure_degrades_to_no_dedup(self, monkeypatch):
        """A broken Redis must not stop the subscriber from starting."""
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "kafka")
        monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")

        with (
            patch.object(
                events_mod,
                "RedisDeduplicationStore",
                side_effect=RuntimeError("redis unreachable"),
            ),
            patch.object(events_mod, "KafkaEventSubscriber") as ksub,
            patch("wildframe_events.KafkaEventPublisher"),
        ):
            sub = events_mod.get_event_subscriber()

        assert sub is ksub.return_value
        assert ksub.call_args.kwargs["dedup_store"] is None

    def test_memory_selection_is_cached(self, monkeypatch):
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")

        first = events_mod.get_event_subscriber()

        assert isinstance(first, InMemoryEventSubscriber)
        assert events_mod.get_event_subscriber() is first

    def test_reset_drops_the_cache(self, monkeypatch):
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")
        first = events_mod.get_event_subscriber()

        events_mod.reset_event_subscriber()

        assert events_mod.get_event_subscriber() is not first


# ----------------------------------------------------------------------
# Start / stop
# ----------------------------------------------------------------------


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_registers_every_topic(self, monkeypatch):
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")

        await events_mod.start_event_subscriber()

        sub = events_mod.get_event_subscriber()
        assert set(sub._handlers) == {
            "content.deleted",
            "content.unpublished",
            "billing.subscription.created",
            "billing.subscription.updated",
            "billing.subscription.cancelled",
        }

    @pytest.mark.asyncio
    async def test_start_failure_is_swallowed(self, monkeypatch):
        subscriber = MagicMock()
        subscriber.subscribe = AsyncMock(side_effect=RuntimeError("kafka down"))
        subscriber.start = AsyncMock()
        monkeypatch.setattr(events_mod, "get_event_subscriber", lambda: subscriber)

        await events_mod.start_event_subscriber()  # must not raise

        subscriber.start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_failure_on_start_is_swallowed(self, monkeypatch):
        subscriber = MagicMock()
        subscriber.subscribe = AsyncMock()
        subscriber.start = AsyncMock(side_effect=RuntimeError("kafka down"))
        monkeypatch.setattr(events_mod, "get_event_subscriber", lambda: subscriber)

        await events_mod.start_event_subscriber()  # must not raise

        assert subscriber.subscribe.await_count == 5

    @pytest.mark.asyncio
    async def test_stop_closes_the_running_subscriber(self, monkeypatch):
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")
        await events_mod.start_event_subscriber()
        sub = events_mod.get_event_subscriber()
        sub.stop = AsyncMock()

        await events_mod.stop_event_subscriber()

        sub.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_is_a_noop_without_a_subscriber(self):
        assert events_mod._subscriber is None

        await events_mod.stop_event_subscriber()  # must not raise

    @pytest.mark.asyncio
    async def test_stop_failure_is_suppressed(self, monkeypatch):
        subscriber = MagicMock()
        subscriber.stop = AsyncMock(side_effect=RuntimeError("already closed"))
        monkeypatch.setattr(events_mod, "get_event_subscriber", lambda: subscriber)

        await events_mod.stop_event_subscriber()  # must not raise


# ----------------------------------------------------------------------
# End-to-end through the SDK in-memory bus
# ----------------------------------------------------------------------


class TestInMemoryBusRoundTrip:
    @pytest.mark.asyncio
    async def test_published_event_reaches_the_eviction_handler(self, monkeypatch):
        """publisher -> subscriber -> handler, with no broker involved."""
        from app.core.settings import settings

        monkeypatch.setattr(settings, "EVENT_PUBLISHER", "memory")
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _Factory())
        repo_cls = MagicMock()
        affected = [uuid4()]
        repo_cls.return_value.delete_for_content = AsyncMock(return_value=affected)
        monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)
        invalidate = AsyncMock()
        monkeypatch.setattr(events_mod, "_cache_invalidate", invalidate)

        publisher = InMemoryEventPublisher()
        await events_mod.start_event_subscriber()
        subscriber = events_mod.get_event_subscriber()
        content_id = str(uuid4())

        await publisher.publish(
            DomainEvent(topic="content.deleted", key=content_id, payload={"content_id": content_id})
        )
        # InMemoryEventSubscriber has no bus of its own: deliver explicitly.
        for event in publisher.sent:
            await subscriber.deliver(event)

        assert len(publisher.sent) == 1
        repo_cls.return_value.delete_for_content.assert_awaited_once()
        invalidate.assert_awaited_once_with(affected[0])

    @pytest.mark.asyncio
    async def test_a_failing_handler_does_not_break_delivery(self, monkeypatch):
        """InMemoryEventSubscriber isolates handler failures per event."""
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _Factory())
        repo_cls = MagicMock()
        repo_cls.return_value.delete_for_content = AsyncMock(
            side_effect=[RuntimeError("db down"), []]
        )
        monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)

        sub = InMemoryEventSubscriber()
        await sub.subscribe("content.deleted", events_mod._handle_content_deleted)

        event = _event("content.deleted", {"content_id": str(uuid4())})
        await sub.deliver(event)  # handler raises, deliver swallows it
        await sub.deliver(event)  # the next event is still processed

        assert repo_cls.return_value.delete_for_content.await_count == 2

    @pytest.mark.asyncio
    async def test_delivering_twice_reruns_the_handler(self, monkeypatch):
        """Idempotence comes from the delete being a no-op, not from dedup here."""
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _Factory())
        repo_cls = MagicMock()
        repo_cls.return_value.delete_for_content = AsyncMock(return_value=[])
        monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)

        sub = InMemoryEventSubscriber()
        await sub.subscribe("content.deleted", events_mod._handle_content_deleted)

        event = _event("content.deleted", {"content_id": str(uuid4())})
        await sub.deliver(event)
        await sub.deliver(event)

        assert repo_cls.return_value.delete_for_content.await_count == 2

    @pytest.mark.asyncio
    async def test_unregistered_topics_are_ignored(self, monkeypatch):
        monkeypatch.setattr(events_mod.DatabaseManager, "session_factory", _Factory())
        repo_cls = MagicMock()
        monkeypatch.setattr(events_mod, "RecommendationRepository", repo_cls)

        sub = InMemoryEventSubscriber()
        await sub.subscribe("content.deleted", events_mod._handle_content_deleted)
        await sub.deliver(_event("content.published", {"content_id": str(uuid4())}))

        repo_cls.assert_not_called()


# ----------------------------------------------------------------------
# Cache invalidation contract used by the handlers
# ----------------------------------------------------------------------


class TestCacheInvalidationContract:
    @pytest.mark.asyncio
    async def test_invalidation_is_a_noop_without_redis(self, monkeypatch):
        import app.services as services_mod

        monkeypatch.setattr(services_mod, "_redis_client", None)
        monkeypatch.setattr(
            services_mod, "get_redis_client", AsyncMock(return_value=None)
        )

        await services_mod._cache_invalidate(uuid4())  # must not raise

    @pytest.mark.asyncio
    async def test_invalidation_deletes_the_user_key(self, monkeypatch):
        import app.services as services_mod

        client = MagicMock()
        client.delete = AsyncMock()
        monkeypatch.setattr(services_mod, "get_redis_client", AsyncMock(return_value=client))
        user_id = uuid4()

        await services_mod._cache_invalidate(user_id)

        client.delete.assert_awaited_once_with(f"wf:rec:user:{user_id}")

    @pytest.mark.asyncio
    async def test_redis_failure_is_swallowed(self, monkeypatch):
        """A cache outage must not fail the event handler."""
        import app.services as services_mod

        client = MagicMock()
        client.delete = AsyncMock(side_effect=RuntimeError("redis down"))
        monkeypatch.setattr(services_mod, "get_redis_client", AsyncMock(return_value=client))

        await services_mod._cache_invalidate(uuid4())  # must not raise
