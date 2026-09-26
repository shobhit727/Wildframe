"""Tests for producer - PolicyChangeProducer."""

import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from wildframe_compliance.events import ComplianceEventType
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.producer import PolicyChangeProducer
from wildframe_compliance.settings import ComplianceSettingsMixin
from wildframe_compliance.policy import GDPRPolicy


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
async def test_producer_init():
    settings = MockSettings()
    producer = PolicyChangeProducer(settings)
    assert producer.settings == settings
    assert producer._publisher is None


@pytest.mark.asyncio
async def test_producer_start_stop():
    settings = MockSettings()
    producer = PolicyChangeProducer(settings)
    with patch("wildframe_compliance.producer.KafkaEventPublisher") as MockPub:
        mock_pub = AsyncMock()
        MockPub.return_value = mock_pub
        await producer.start()
        assert producer._publisher is not None
        await producer.stop()
        assert producer._publisher is None


@pytest.mark.asyncio
async def test_producer_publish_policy_change():
    settings = MockSettings()
    producer = PolicyChangeProducer(settings)
    producer._publisher = AsyncMock()
    producer._publisher.publish = AsyncMock()
    policy = GDPRPolicy()
    await producer.publish_policy_change(ComplianceEventType.POLICY_CREATED, policy)
    assert producer._publisher.publish.called


# ---------------------------------------------------------------------------
# Behavioural coverage: the producer is admin-service's only write path to the
# policy topic, so the envelope shape matters.
# ---------------------------------------------------------------------------


def _policy(jurisdiction=Jurisdiction.EU, version="1.0.0"):
    from wildframe_compliance.policy import GDPRPolicy

    policy = GDPRPolicy()
    if jurisdiction is not Jurisdiction.EU:
        policy = policy.model_copy(update={"jurisdiction": jurisdiction})
    if version != "1.0.0":
        policy = policy.model_copy(update={"version": version})
    return policy


@pytest.mark.asyncio
async def test_publish_before_start_raises():
    producer = PolicyChangeProducer(MockSettings())
    with pytest.raises(RuntimeError, match="Producer not started"):
        await producer.publish_policy_change(ComplianceEventType.POLICY_CREATED, _policy())


@pytest.mark.asyncio
async def test_start_is_idempotent():
    producer = PolicyChangeProducer(MockSettings())
    with patch("wildframe_compliance.producer.KafkaEventPublisher") as MockPub:
        MockPub.return_value = AsyncMock()
        await producer.start()
        await producer.start()
        MockPub.assert_called_once_with(bootstrap_servers="localhost:9092")
    assert producer._publisher is not None


@pytest.mark.asyncio
async def test_stop_without_start_is_safe():
    producer = PolicyChangeProducer(MockSettings())
    await producer.stop()  # must not raise
    assert producer._publisher is None


@pytest.mark.asyncio
async def test_publish_emits_to_the_policy_topic_keyed_by_jurisdiction():
    from wildframe_compliance.events import COMPLIANCE_POLICY_TOPIC

    producer = PolicyChangeProducer(MockSettings())
    producer._publisher = AsyncMock()
    await producer.publish_policy_change(ComplianceEventType.POLICY_CREATED, _policy())

    domain_event = producer._publisher.publish.await_args.args[0]
    assert domain_event.topic == COMPLIANCE_POLICY_TOPIC
    assert domain_event.key == Jurisdiction.EU.value
    assert domain_event.producer == "test-service"
    assert domain_event.payload["event_type"] == "compliance.policy.created"
    assert domain_event.payload["jurisdiction"] == "EU"
    assert domain_event.payload["changed_by"] == "admin-service"


@pytest.mark.asyncio
async def test_publish_accepts_a_raw_jurisdiction_string():
    """``jurisdiction`` is coerced from str to the enum, so a policy-like object
    carrying a string jurisdiction still produces a valid event."""
    class StringJurisdictionPolicy:
        jurisdiction = "US-CA"
        version = "2.1.0"

        def model_dump(self):
            return {"jurisdiction": "US-CA", "version": "2.1.0"}

    producer = PolicyChangeProducer(MockSettings())
    producer._publisher = AsyncMock()
    await producer.publish_policy_change(
        ComplianceEventType.POLICY_UPDATED, StringJurisdictionPolicy()
    )
    domain_event = producer._publisher.publish.await_args.args[0]
    assert domain_event.key == "US-CA"
    assert domain_event.payload["policy_version"] == "2.1.0"


@pytest.mark.asyncio
async def test_known_bug_a_plain_dict_policy_crashes_publish():
    """BUG (producer.py:62-66 vs :74): the ``dict(policy)`` fallback at :74
    exists to support non-pydantic policies, but the jurisdiction is read with
    ``getattr(policy, "jurisdiction", None)`` — a dict has no such attribute, so
    ``jurisdiction`` stays ``None`` and :82 raises
    ``AttributeError: 'NoneType' object has no attribute 'value'``.

    A caller that passes a plain dict gets a crash instead of an event. Pinned
    as-is: fixing it is a production change, not a test change.
    """
    producer = PolicyChangeProducer(MockSettings())
    producer._publisher = AsyncMock()
    with pytest.raises(AttributeError, match="NoneType"):
        await producer.publish_policy_change(
            ComplianceEventType.POLICY_DELETED,
            {"jurisdiction": Jurisdiction.IN, "version": "0.9.0"},
        )
    producer._publisher.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_known_bug_an_object_without_jurisdiction_fails_too():
    """Same root cause, no-dict variant. ``object()`` has no ``jurisdiction``
    and no ``model_dump``, so :74 raises ``TypeError: 'object' object is not
    iterable`` from ``dict(policy)`` before :82 is even reached."""
    producer = PolicyChangeProducer(MockSettings())
    producer._publisher = AsyncMock()
    with pytest.raises(TypeError, match="not iterable"):
        await producer.publish_policy_change(ComplianceEventType.POLICY_CREATED, object())


@pytest.mark.asyncio
async def test_publish_defaults_version_when_the_policy_has_none():
    class NoVersion:
        jurisdiction = Jurisdiction.EU

        def model_dump(self):
            return {}

    producer = PolicyChangeProducer(MockSettings())
    producer._publisher = AsyncMock()
    await producer.publish_policy_change(ComplianceEventType.POLICY_ACTIVATED, NoVersion())
    assert producer._publisher.publish.await_args.args[0].payload["policy_version"] == "1.0.0"


@pytest.mark.asyncio
async def test_publish_records_changed_fields_and_actor():
    producer = PolicyChangeProducer(MockSettings())
    producer._publisher = AsyncMock()
    await producer.publish_policy_change(
        ComplianceEventType.POLICY_UPDATED,
        _policy(),
        changed_fields=["consent_minor_age", "breach_notification_hours"],
        changed_by="admin@example.com",
    )
    payload = producer._publisher.publish.await_args.args[0].payload
    assert payload["changed_fields"] == [
        "consent_minor_age",
        "breach_notification_hours",
    ]
    assert payload["changed_by"] == "admin@example.com"


@pytest.mark.asyncio
async def test_publish_threads_the_correlation_id_onto_the_envelope():
    producer = PolicyChangeProducer(MockSettings())
    producer._publisher = AsyncMock()
    await producer.publish_policy_change(
        ComplianceEventType.POLICY_UPDATED, _policy(), correlation_id="corr-42"
    )
    domain_event = producer._publisher.publish.await_args.args[0]
    assert domain_event.correlation_id == "corr-42"
    assert domain_event.payload["correlation_id"] == "corr-42"


@pytest.mark.asyncio
async def test_publish_assigns_a_unique_event_id_and_utc_timestamp():
    producer = PolicyChangeProducer(MockSettings())
    producer._publisher = AsyncMock()
    ids = set()
    for _ in range(3):
        await producer.publish_policy_change(ComplianceEventType.POLICY_UPDATED, _policy())
        payload = producer._publisher.publish.await_args.args[0].payload
        ids.add(payload["event_id"])
        assert payload["timestamp"].endswith("+00:00")
    assert len(ids) == 3


@pytest.mark.asyncio
async def test_publish_propagates_a_transport_failure():
    """A broker failure must reach the caller: admin-service has to know the
    policy change was not recorded."""
    producer = PolicyChangeProducer(MockSettings())
    publisher = AsyncMock()
    publisher.publish = AsyncMock(side_effect=RuntimeError("broker down"))
    producer._publisher = publisher
    with pytest.raises(RuntimeError, match="broker down"):
        await producer.publish_policy_change(ComplianceEventType.POLICY_CREATED, _policy())


@pytest.mark.asyncio
async def test_create_policy_change_producer_starts_it():
    from wildframe_compliance.producer import create_policy_change_producer

    with patch("wildframe_compliance.producer.KafkaEventPublisher") as MockPub:
        MockPub.return_value = AsyncMock()
        producer = await create_policy_change_producer(MockSettings())
    assert isinstance(producer, PolicyChangeProducer)
    assert producer._publisher is not None
