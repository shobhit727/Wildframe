"""Tests for compliance producer module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import UTC, datetime
from uuid import uuid4

from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin
from wildframe_compliance.events import (
    CompliancePolicyEvent,
    ComplianceEventType,
    COMPLIANCE_POLICY_TOPIC,
)
from wildframe_compliance.policy import GDPRPolicy


class MockSettings(ComplianceSettingsMixin):
    SERVICE_NAME: str = "admin-service"
    compliance_jurisdiction: Jurisdiction = Jurisdiction.EU
    compliance_additional_jurisdictions: list[Jurisdiction] = []
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"


class TestPolicyChangeProducer:
    """Tests for PolicyChangeProducer."""

    @pytest.mark.asyncio
    async def test_producer_start_stop(self):
        from wildframe_compliance.producer import PolicyChangeProducer

        settings = MockSettings()
        producer = PolicyChangeProducer(settings)

        with patch("wildframe_compliance.producer.KafkaEventPublisher") as mock_publisher_class:
            mock_publisher = AsyncMock()
            mock_publisher_class.return_value = mock_publisher

            await producer.start()

            assert producer._publisher is not None
            # KafkaEventPublisher is called with bootstrap_servers from settings
            mock_publisher_class.assert_called_once()

            await producer.stop()

            assert producer._publisher is None
            mock_publisher.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_policy_change(self):
        from wildframe_compliance.producer import PolicyChangeProducer

        settings = MockSettings()
        producer = PolicyChangeProducer(settings)

        with patch("wildframe_compliance.producer.KafkaEventPublisher") as mock_publisher_class:
            mock_publisher = AsyncMock()
            mock_publisher_class.return_value = mock_publisher

            await producer.start()

            policy = GDPRPolicy()
            await producer.publish_policy_change(
                event_type=ComplianceEventType.POLICY_UPDATED,
                policy=policy,
                changed_fields=["dpo_required"],
                changed_by="admin",
                correlation_id="corr-123",
            )

            # Verify publish was called
            mock_publisher.publish.assert_called_once()

            # Verify the event was created correctly
            call_args = mock_publisher.publish.call_args[0][0]
            from wildframe_events import DomainEvent
            assert isinstance(call_args, DomainEvent)
            assert call_args.topic == COMPLIANCE_POLICY_TOPIC
            assert call_args.key == "EU"
            assert call_args.producer == "admin-service"
            assert call_args.correlation_id == "corr-123"

            await producer.stop()

    @pytest.mark.asyncio
    async def test_publish_without_start_raises(self):
        from wildframe_compliance.producer import PolicyChangeProducer

        settings = MockSettings()
        producer = PolicyChangeProducer(settings)

        policy = GDPRPolicy()

        with pytest.raises(RuntimeError, match="Producer not started"):
            await producer.publish_policy_change(
                event_type=ComplianceEventType.POLICY_CREATED,
                policy=policy,
            )

    @pytest.mark.asyncio
    async def test_publish_with_string_jurisdiction(self):
        from wildframe_compliance.producer import PolicyChangeProducer

        settings = MockSettings()
        producer = PolicyChangeProducer(settings)

        with patch("wildframe_compliance.producer.KafkaEventPublisher") as mock_publisher_class:
            mock_publisher = AsyncMock()
            mock_publisher_class.return_value = mock_publisher

            await producer.start()

            # Create policy with string jurisdiction
            policy = GDPRPolicy()
            policy.jurisdiction = "EU"  # String instead of enum

            await producer.publish_policy_change(
                event_type=ComplianceEventType.POLICY_CREATED,
                policy=policy,
            )

            mock_publisher.publish.assert_called_once()
            await producer.stop()

    @pytest.mark.asyncio
    async def test_create_producer_factory(self):
        from wildframe_compliance.producer import create_policy_change_producer

        settings = MockSettings()

        with patch("wildframe_compliance.producer.KafkaEventPublisher") as mock_publisher_class:
            mock_publisher = AsyncMock()
            mock_publisher_class.return_value = mock_publisher

            producer = await create_policy_change_producer(settings)

            assert producer is not None
            assert producer._publisher is not None

            await producer.stop()