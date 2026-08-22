"""Tests for InMemoryEventPublisher and InMemoryEventSubscriber."""

import pytest

from wildframe_events import (
    DomainEvent,
    InMemoryEventPublisher,
    InMemoryEventSubscriber,
    Topic,
)


@pytest.fixture
def publisher():
    return InMemoryEventPublisher()


@pytest.fixture
def subscriber():
    return InMemoryEventSubscriber()


def make_event(topic=Topic.CONTENT_UPLOADED, key="upload:abc", **payload):
    return DomainEvent(topic=topic, key=key, payload=payload, producer="uploads-service")


class TestInMemoryEventPublisher:
    async def test_publish_records_event(self, publisher):
        event = make_event()
        await publisher.publish(event)
        assert publisher.sent == [event]

    async def test_publish_many_records_in_order(self, publisher):
        events = [make_event(key=f"k{i}") for i in range(3)]
        await publisher.publish_many(events)
        assert publisher.sent == events

    async def test_publish_many_empty(self, publisher):
        await publisher.publish_many([])
        assert publisher.sent == []

    async def test_publish_appends_keep_order(self, publisher):
        await publisher.publish(make_event(key="first"))
        await publisher.publish(make_event(key="second"))
        assert [e.key for e in publisher.sent] == ["first", "second"]

    async def test_close_is_noop(self, publisher):
        await publisher.close()  # must not raise


class TestInMemoryEventSubscriber:
    async def test_handlers_receive_matching_topic(self, subscriber):
        received = []
        await subscriber.subscribe(Topic.CONTENT_UPLOADED, lambda e: received.append(e))
        event = make_event()
        await subscriber.deliver(event)
        assert received == [event]

    async def test_multiple_handlers_same_topic(self, subscriber):
        received_a, received_b = [], []
        await subscriber.subscribe(Topic.CONTENT_UPLOADED, received_a.append)
        await subscriber.subscribe(Topic.CONTENT_UPLOADED, received_b.append)
        await subscriber.deliver(make_event())
        assert len(received_a) == len(received_b) == 1

    async def test_handler_not_called_for_other_topic(self, subscriber):
        received = []
        await subscriber.subscribe(Topic.CONTENT_SCANNED, lambda e: received.append(e))
        await subscriber.deliver(make_event(topic=Topic.CONTENT_UPLOADED))
        assert received == []

    async def test_raising_handler_does_not_stop_others(self, subscriber):
        received = []

        async def boom(event):
            raise RuntimeError("handler failed")

        await subscriber.subscribe(Topic.CONTENT_UPLOADED, boom)
        await subscriber.subscribe(Topic.CONTENT_UPLOADED, lambda e: received.append(e))
        await subscriber.deliver(make_event())
        assert len(received) == 1

    async def test_raise_handler_invocation(self, subscriber):
        async def async_handler(event):
            pass

        await subscriber.subscribe(Topic.CONTENT_UPLOADED, async_handler)
        await subscriber.deliver(make_event())  # must not raise

    async def test_deliver_no_handlers_is_noop(self, subscriber):
        await subscriber.deliver(make_event())

    async def test_subscribe_without_delivery_before_start(self, subscriber):
        # start/stop are no-ops for the in-memory adapter
        await subscriber.start()
        await subscriber.stop()


class TestHandlersE2E:
    """Wire a publisher to a subscriber the way services do at app startup."""

    async def test_publish_then_deliver_wired_handlers(self, publisher, subscriber):
        received = []
        await subscriber.subscribe(Topic.CONTENT_UPLOADED, lambda e: received.append(e))
        event = make_event()
        await publisher.publish(event)
        await subscriber.deliver(event)
        assert received == [event]
        assert publisher.sent == [event]

    async def test_publish_many_then_deliver_each(self, publisher, subscriber):
        received = []
        await subscriber.subscribe(Topic.CONTENT_UPLOADED, lambda e: received.append(e))
        events = [make_event(key=f"k{i}") for i in range(5)]
        await publisher.publish_many(events)
        for event in events:
            await subscriber.deliver(event)
        assert [e.key for e in received] == [f"k{i}" for i in range(5)]


class TestCorrelationPropagation:
    """#462: handlers run with the event's correlation ID in log context."""

    async def test_deliver_reinjects_correlation_id(self, subscriber):
        from wildframe_observability.logging import get_correlation_id

        seen = {}

        async def handler(event):
            seen["corr"] = get_correlation_id()

        await subscriber.subscribe(Topic.CONTENT_UPLOADED, handler)
        event = make_event()
        object.__setattr__(event, "correlation_id", "corr-123")
        await subscriber.deliver(event)
        assert seen["corr"] == "corr-123"
        # Context is reset afterwards so unrelated work isn't misattributed.
        assert get_correlation_id() == ""

    async def test_deliver_without_correlation_leaves_context_untouched(self, subscriber):
        from wildframe_observability.logging import (
            get_correlation_id,
            set_correlation_id,
        )

        seen = {}

        async def handler(event):
            seen["corr"] = get_correlation_id()

        await subscriber.subscribe(Topic.CONTENT_UPLOADED, handler)
        # Pre-existing context must pass through to the handler unchanged.
        set_correlation_id("pre-existing")
        event = make_event()
        object.__setattr__(event, "correlation_id", "")
        await subscriber.deliver(event)
        assert seen["corr"] == "pre-existing"
        assert get_correlation_id() == "pre-existing"
        set_correlation_id("")
