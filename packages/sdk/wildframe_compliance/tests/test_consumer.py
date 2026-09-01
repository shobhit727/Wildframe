"""Tests for compliance consumer module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import UTC, datetime
from uuid import uuid4

from wildframe_compliance.consumer import PolicyChangeHandler
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin
from wildframe_compliance.events import (
    CompliancePolicyEvent,
    ComplianceEventType,
    COMPLIANCE_POLICY_TOPIC,
)
from wildframe_compliance.policy import GDPRPolicy


class MockSettings(ComplianceSettingsMixin):
    SERVICE_NAME: str = "test-service"
    compliance_jurisdiction: Jurisdiction = Jurisdiction.EU
    compliance_additional_jurisdictions: list[Jurisdiction] = [Jurisdiction.US]
    compliance_dpo_email: str = "dpo@example.com"
    compliance_grievance_officer_email: str = "grievance@example.com"
    compliance_allowed_data_regions: list[str] = ["EU", "US"]
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"


class TestPolicyChangeHandler:
    """Tests for PolicyChangeHandler."""

    def setup_method(self):
        self.settings = MockSettings()
        self.handler = PolicyChangeHandler(self.settings)

    def test_handler_initialization(self):
        assert self.handler.settings == self.settings
        assert self.handler.on_policy_change is None

    def test_handler_with_callback(self):
        callback_called = []

        async def custom_callback(event):
            callback_called.append(event)

        handler = PolicyChangeHandler(self.settings, on_policy_change=custom_callback)
        assert handler.on_policy_change is custom_callback

    @pytest.mark.asyncio
    async def test_handler_processes_domain_event(self):
        from wildframe_events import DomainEvent
        from wildframe_compliance.events import CompliancePolicyEvent

        policy = GDPRPolicy()
        event = CompliancePolicyEvent.from_policy(
            event_type=ComplianceEventType.POLICY_UPDATED,
            policy=policy,
            event_id=uuid4(),
            changed_fields=["dpo_required"],
            changed_by="admin",
        )

        domain_event = DomainEvent.create(
            topic=COMPLIANCE_POLICY_TOPIC,
            key="EU",
            payload=event.to_dict(),
            producer="admin-service",
        )

        # Process the event - should not raise
        await self.handler(domain_event)

    @pytest.mark.asyncio
    async def test_handler_calls_custom_callback(self):
        callback_called = []

        async def custom_callback(event):
            callback_called.append(event)

        handler = PolicyChangeHandler(self.settings, on_policy_change=custom_callback)

        from wildframe_events import DomainEvent
        from wildframe_compliance.events import CompliancePolicyEvent

        policy = GDPRPolicy()
        event = CompliancePolicyEvent.from_policy(
            event_type=ComplianceEventType.POLICY_CREATED,
            policy=policy,
            event_id=uuid4(),
        )

        domain_event = DomainEvent.create(
            topic=COMPLIANCE_POLICY_TOPIC,
            key="EU",
            payload=event.to_dict(),
            producer="admin-service",
        )

        await handler(domain_event)

        assert len(callback_called) == 1
        assert callback_called[0].event_type == ComplianceEventType.POLICY_CREATED

    @pytest.mark.asyncio
    async def test_handler_ignores_irrelevant_jurisdiction(self):
        from wildframe_events import DomainEvent
        from wildframe_compliance.events import CompliancePolicyEvent

        # Create event for a jurisdiction not in our list
        policy = GDPRPolicy()
        policy.jurisdiction = Jurisdiction.IN  # Not in additional_jurisdictions
        event = CompliancePolicyEvent.from_policy(
            event_type=ComplianceEventType.POLICY_UPDATED,
            policy=policy,
            event_id=uuid4(),
        )

        domain_event = DomainEvent.create(
            topic=COMPLIANCE_POLICY_TOPIC,
            key="IN",
            payload=event.to_dict(),
            producer="admin-service",
        )

        # Should not raise, but also not process (no callback called)
        await self.handler(domain_event)


class TestPolicyChangeConsumer:
    """Tests for PolicyChangeConsumer."""

    @pytest.mark.asyncio
    async def test_consumer_start_stop(self):
        from wildframe_compliance.consumer import PolicyChangeConsumer

        settings = MockSettings()
        consumer = PolicyChangeConsumer(settings)

        # Mock the KafkaEventSubscriber
        with patch("wildframe_compliance.consumer.KafkaEventSubscriber") as mock_subscriber_class:
            mock_subscriber = AsyncMock()
            mock_subscriber_class.return_value = mock_subscriber

            await consumer.start()

            assert consumer._running is True
            mock_subscriber.subscribe.assert_called_once()
            mock_subscriber.start.assert_called_once()

            await consumer.stop()

            assert consumer._running is False
            mock_subscriber.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_consumer_double_start(self):
        from wildframe_compliance.consumer import PolicyChangeConsumer

        settings = MockSettings()
        consumer = PolicyChangeConsumer(settings)

        with patch("wildframe_compliance.consumer.KafkaEventSubscriber") as mock_subscriber_class:
            mock_subscriber = AsyncMock()
            mock_subscriber_class.return_value = mock_subscriber

            await consumer.start()
            await consumer.start()  # Should not start twice

            assert mock_subscriber.start.call_count == 1

            await consumer.stop()