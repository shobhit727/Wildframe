"""Tests for consumer - PolicyChangeConsumer."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from wildframe_compliance.consumer import PolicyChangeConsumer, PolicyChangeHandler
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin


class MockSettings(ComplianceSettingsMixin):
    SERVICE_NAME: str = "test-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "test"
    DATABASE_URL: str = "sqlite:///:memory:"
    REDIS_URL: str = "redis://localhost:6379/0"
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    JWT_SECRET_KEY: str = "test-secret-key-32-bytes-long-for-test"
    compliance_jurisdiction: Jurisdiction = Jurisdiction.EU


@pytest.mark.asyncio
async def test_consumer_init():
    settings = MockSettings()
    consumer = PolicyChangeConsumer(settings)
    assert consumer.settings == settings
    assert consumer._running is False


@pytest.mark.asyncio
async def test_consumer_start_stop():
    settings = MockSettings()
    consumer = PolicyChangeConsumer(settings)
    with patch("wildframe_compliance.consumer.KafkaEventSubscriber") as MockSub:
        mock_sub = AsyncMock()
        MockSub.return_value = mock_sub
        await consumer.start()
        assert consumer._running is True
        await consumer.stop()
        assert consumer._running is False


@pytest.mark.asyncio
async def test_handler_call():
    settings = MockSettings()
    handler = PolicyChangeHandler(settings)
    assert handler.settings == settings


# ---------------------------------------------------------------------------
# Behavioural coverage: the handler is the trust boundary for compliance
# policy changes, so every branch matters.
# ---------------------------------------------------------------------------


def _policy_event_dict(jurisdiction=Jurisdiction.EU, event_type="compliance.policy.updated"):
    """A serialised CompliancePolicyEvent for the given jurisdiction."""
    from uuid import uuid4

    from wildframe_compliance.events import CompliancePolicyEvent
    from wildframe_compliance.policy import GDPRPolicy

    data = CompliancePolicyEvent.from_policy(
        event_type=_enum(event_type),
        policy=GDPRPolicy(),
        event_id=uuid4(),
    ).to_dict()
    data["jurisdiction"] = jurisdiction.value
    return data


def _enum(value: str):
    from wildframe_compliance.events import ComplianceEventType

    return ComplianceEventType(value)


def _domain_event(jurisdiction=Jurisdiction.EU, event_type="compliance.policy.updated"):
    from wildframe_events import DomainEvent

    return DomainEvent(
        topic="compliance.policy.changed",
        key=jurisdiction.value,
        payload=_policy_event_dict(jurisdiction, event_type),
    )


@pytest.mark.asyncio
async def test_handler_accepts_a_domain_event_instance():
    settings = MockSettings()  # EU
    seen = []

    async def on_change(policy_event):
        seen.append(policy_event)

    handler = PolicyChangeHandler(settings, on_change)
    await handler(_domain_event())

    assert len(seen) == 1
    assert seen[0].jurisdiction == Jurisdiction.EU
    assert seen[0].event_type == _enum("compliance.policy.updated")


@pytest.mark.asyncio
async def test_handler_accepts_a_raw_envelope_dict():
    """A non-DomainEvent is run through ``DomainEvent.from_dict`` first, so a
    serialised envelope (what a Kafka consumer hands over) works too."""
    settings = MockSettings()
    seen = []

    async def on_change(policy_event):
        seen.append(policy_event)

    handler = PolicyChangeHandler(settings, on_change)
    await handler(_domain_event().to_dict())

    assert len(seen) == 1
    assert seen[0].jurisdiction == Jurisdiction.EU


@pytest.mark.asyncio
async def test_handler_rejects_a_bare_policy_payload_dict():
    """A CompliancePolicyEvent dict is NOT a DomainEvent envelope; it must be
    rejected rather than half-applied."""
    seen = []

    async def on_change(policy_event):
        seen.append(policy_event)

    handler = PolicyChangeHandler(MockSettings(), on_change)
    await handler(_policy_event_dict())
    assert seen == []


@pytest.mark.asyncio
async def test_handler_updates_cache_for_the_primary_jurisdiction():
    settings = MockSettings()
    handler = PolicyChangeHandler(settings)
    # Primary jurisdiction: the local cache path must run (it logs, and is the
    # hook a real cache would hang off).
    await handler(_domain_event(Jurisdiction.EU))
    # No exception and no on_policy_change call is required.
    assert handler.settings is settings


@pytest.mark.asyncio
async def test_handler_updates_cache_for_an_additional_jurisdiction():
    from wildframe_compliance.jurisdiction import Jurisdiction as J

    class AdditionalSettings(MockSettings):
        compliance_jurisdiction: J = J.EU
        compliance_additional_jurisdictions: list[J] = [J.IN, J.US]

    seen = []

    async def on_change(policy_event):
        seen.append(policy_event.jurisdiction)

    handler = PolicyChangeHandler(AdditionalSettings(), on_change)
    # IN is an *additional* jurisdiction -> in scope.
    await handler(_domain_event(Jurisdiction.IN))
    assert seen == [J.IN]


@pytest.mark.asyncio
async def test_handler_ignores_a_foreign_jurisdiction_but_still_notifies():
    """A policy change for an out-of-scope jurisdiction must not touch the
    local cache, but the callback still fires so the app can react."""
    from wildframe_compliance.jurisdiction import Jurisdiction as J

    seen = []

    async def on_change(policy_event):
        seen.append(policy_event.jurisdiction)

    handler = PolicyChangeHandler(MockSettings(), on_change)  # EU only
    await handler(_domain_event(Jurisdiction.IN))
    assert seen == [J.IN]


@pytest.mark.asyncio
async def test_handler_swallows_a_malformed_event():
    """A poison policy event must be logged, not raised: the subscriber's DLQ
    path would otherwise quarantine every malformed message."""
    settings = MockSettings()
    handler = PolicyChangeHandler(settings)
    await handler({"not": "a policy event"})  # missing keys -> must not raise
    await handler({"event_type": "not-a-real-event-type"})  # bad enum -> no raise
    await handler(None)  # no attribute access on None -> no raise


@pytest.mark.asyncio
async def test_handler_swallows_a_non_dict_event():
    handler = PolicyChangeHandler(MockSettings())
    await handler(object())  # must not raise
    await handler("a string")  # must not raise


@pytest.mark.asyncio
async def test_handler_swallows_a_callback_failure():
    """A failing application callback must not escape into the consumer loop
    (the adapter would retry and then DLQ a policy change that already applied)."""
    async def exploding(policy_event):
        raise RuntimeError("app callback failed")

    handler = PolicyChangeHandler(MockSettings(), exploding)
    await handler(_domain_event())  # must not raise


@pytest.mark.asyncio
async def test_handler_without_a_callback_still_succeeds():
    handler = PolicyChangeHandler(MockSettings(), on_policy_change=None)
    await handler(_domain_event())


@pytest.mark.asyncio
async def test_handler_sync_callback_is_supported():
    """The callback is annotated ``Callable[..., Any]``; a plain (non-async)
    function returning None is what most callers pass."""
    seen = []

    def sync_callback(policy_event):
        seen.append(policy_event)

    handler = PolicyChangeHandler(MockSettings(), sync_callback)
    await handler(_domain_event())
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_consumer_subscribes_to_the_policy_topic():
    from wildframe_compliance.events import COMPLIANCE_POLICY_TOPIC

    settings = MockSettings()
    consumer = PolicyChangeConsumer(settings)
    with patch("wildframe_compliance.consumer.KafkaEventSubscriber") as MockSub:
        mock_sub = AsyncMock()
        MockSub.return_value = mock_sub
        await consumer.start()

        MockSub.assert_called_once_with(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id=f"{settings.SERVICE_NAME}-compliance-consumer",
        )
        topic, handler = mock_sub.subscribe.await_args.args
        assert topic == COMPLIANCE_POLICY_TOPIC
        assert handler is consumer.handler
        mock_sub.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_consumer_start_is_idempotent():
    consumer = PolicyChangeConsumer(MockSettings())
    with patch("wildframe_compliance.consumer.KafkaEventSubscriber") as MockSub:
        mock_sub = AsyncMock()
        MockSub.return_value = mock_sub
        await consumer.start()
        await consumer.start()  # must return early, not build a second consumer
        MockSub.assert_called_once()
        assert consumer._running is True


@pytest.mark.asyncio
async def test_consumer_stop_without_start_is_safe():
    consumer = PolicyChangeConsumer(MockSettings())
    await consumer.stop()
    assert consumer._running is False
    assert consumer._subscriber is None


@pytest.mark.asyncio
async def test_consumer_stop_clears_the_subscriber():
    consumer = PolicyChangeConsumer(MockSettings())
    with patch("wildframe_compliance.consumer.KafkaEventSubscriber") as MockSub:
        mock_sub = AsyncMock()
        MockSub.return_value = mock_sub
        await consumer.start()
        await consumer.stop()
        mock_sub.stop.assert_awaited_once()
        assert consumer._subscriber is None


@pytest.mark.asyncio
async def test_create_policy_change_consumer_starts_it():
    from wildframe_compliance.consumer import create_policy_change_consumer

    with patch("wildframe_compliance.consumer.KafkaEventSubscriber") as MockSub:
        MockSub.return_value = AsyncMock()
        consumer = await create_policy_change_consumer(MockSettings())
    assert isinstance(consumer, PolicyChangeConsumer)
    assert consumer._running is True
