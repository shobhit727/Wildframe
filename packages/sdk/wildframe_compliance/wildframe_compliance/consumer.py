"""Policy change event consumer for services."""

import logging
from typing import Any, Callable

from wildframe_events import DomainEvent
from wildframe_compliance.events import (
    CompliancePolicyEvent,
    COMPLIANCE_POLICY_TOPIC,
)
from wildframe_compliance.settings import ComplianceSettingsMixin

logger = logging.getLogger(__name__)


class PolicyChangeHandler:
    """Handler for processing compliance policy change events."""

    def __init__(
        self,
        settings: ComplianceSettingsMixin,
        on_policy_change: Callable[[CompliancePolicyEvent], Any] | None = None,
    ):
        self.settings = settings
        self.on_policy_change = on_policy_change

    async def __call__(self, event: Any) -> None:
        """Handle a domain event."""
        try:
            domain_event = event if isinstance(event, DomainEvent) else DomainEvent.from_dict(event)
            event_data = domain_event.payload
            policy_event = CompliancePolicyEvent.from_dict(event_data)

            logger.info(
                f"Received compliance event: {policy_event.event_type.value} for {policy_event.jurisdiction.value}"
            )

            # Update local cache if relevant
            if (
                policy_event.jurisdiction == self.settings.compliance_jurisdiction
                or policy_event.jurisdiction in self.settings.compliance_additional_jurisdictions
            ):
                await self._update_local_cache(policy_event)

            # Call custom handler if provided
            if self.on_policy_change:
                await self.on_policy_change(policy_event)

        except Exception as e:
            logger.error(f"Failed to process compliance event: {e}")

    async def _update_local_cache(self, event: CompliancePolicyEvent) -> None:
        """Update local policy cache with new policy."""
        # In a real implementation, this would update a local cache or database
        # For now, we just log the update
        logger.info(
            f"Updated policy cache for {event.jurisdiction.value} to version {event.policy_version}"
        )


class PolicyChangeConsumer:
    """Consumes compliance policy change events."""

    def __init__(
        self,
        settings: ComplianceSettingsMixin,
        on_policy_change: Callable[[CompliancePolicyEvent], Any] | None = None,
    ):
        self.settings = settings
        self.handler = PolicyChangeHandler(settings, on_policy_change)
        self._subscriber: Any | None = None
        self._running = False

    async def start(self) -> None:
        """Start consuming policy change events."""
        if self._running:
            return

        from wildframe_events import KafkaEventSubscriber

        self._subscriber = KafkaEventSubscriber(
            bootstrap_servers=self.settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id=f"{self.settings.SERVICE_NAME}-compliance-consumer",
        )

        await self._subscriber.subscribe(COMPLIANCE_POLICY_TOPIC, self.handler)
        await self._subscriber.start()
        self._running = True
        logger.info(f"Started compliance policy consumer for {self.settings.SERVICE_NAME}")

    async def stop(self) -> None:
        """Stop consuming events."""
        self._running = False
        if self._subscriber:
            await self._subscriber.stop()
            self._subscriber = None
        logger.info(f"Stopped compliance policy consumer for {self.settings.SERVICE_NAME}")


async def create_policy_change_consumer(
    settings: ComplianceSettingsMixin,
    on_policy_change: Callable[[Any], Any] | None = None,
) -> PolicyChangeConsumer:
    """Factory function to create and start a policy change consumer."""
    consumer = PolicyChangeConsumer(settings, on_policy_change)
    await consumer.start()
    return consumer
