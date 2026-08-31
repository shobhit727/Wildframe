"""Policy change event producer for admin-service."""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from wildframe_events import DomainEvent, EventPublisher
from wildframe_compliance.events import (
    CompliancePolicyEvent,
    ComplianceEventType,
    COMPLIANCE_POLICY_TOPIC,
)
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.policy import CompliancePolicy
from wildframe_compliance.settings import ComplianceSettingsMixin

logger = logging.getLogger(__name__)


class PolicyChangeProducer:
    """Produces compliance policy change events."""

    def __init__(self, settings: ComplianceSettingsMixin):
        self.settings = settings
        self._publisher: EventPublisher | None = None

    async def start(self) -> None:
        """Start the event publisher."""
        if self._publisher is None:
            from wildframe_events import KafkaEventPublisher

            self._publisher = KafkaEventPublisher(
                bootstrap_servers=self.settings.KAFKA_BOOTSTRAP_SERVERS,
            )
            logger.info(f"Started compliance policy producer for {self.settings.SERVICE_NAME}")

    async def stop(self) -> None:
        """Stop the event publisher."""
        if self._publisher:
            await self._publisher.close()
            self._publisher = None
            logger.info(f"Stopped compliance policy producer for {self.settings.SERVICE_NAME}")

    async def publish_policy_change(
        self,
        event_type: ComplianceEventType,
        policy: Any,
        changed_fields: list[str] | None = None,
        changed_by: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Publish a policy change event.

        Args:
            event_type: Type of policy change
            policy: The policy instance that changed
            changed_fields: List of fields that changed (for updates)
            changed_by: User or system that made the change
            correlation_id: Correlation ID for tracing
        """
        if not self._publisher:
            raise RuntimeError("Producer not started. Call start() first.")

        # Extract policy attributes
        jurisdiction = getattr(policy, "jurisdiction", None)
        if isinstance(jurisdiction, str):
            from wildframe_compliance.jurisdiction import Jurisdiction

            jurisdiction = Jurisdiction(jurisdiction)

        event = CompliancePolicyEvent(
            event_type=event_type,
            event_id=uuid4(),
            timestamp=datetime.now(UTC),
            jurisdiction=jurisdiction,
            policy_version=getattr(policy, "version", "1.0.0"),
            policy_data=policy.model_dump() if hasattr(policy, "model_dump") else dict(policy),
            changed_fields=changed_fields,
            changed_by=changed_by or "admin-service",
            correlation_id=correlation_id,
        )

        domain_event = DomainEvent.create(
            topic=COMPLIANCE_POLICY_TOPIC,
            key=event.jurisdiction.value,
            payload=event.to_dict(),
            producer=self.settings.SERVICE_NAME,
            correlation_id=correlation_id,
        )

        await self._publisher.publish(domain_event)
        logger.info(
            f"Published {event_type.value} for {event.jurisdiction.value} v{event.policy_version}"
        )


async def create_policy_change_producer(
    settings: ComplianceSettingsMixin,
) -> "PolicyChangeProducer":
    """Factory function to create and start a policy change producer."""
    producer = PolicyChangeProducer(settings)
    await producer.start()
    return producer
