"""Wildframe Domain Events — shared event contracts.

This package defines the canonical event schemas, topic names, and
configuration for the Wildframe event-driven backbone (PRODUCT_VISION.md §7).

Every state change is an event. Core topics:
    content.uploaded     content.scanned     content.metadata_extracted
    content.encoded      content.packaged    content.published
    billing.subscription.created/updated/cancelled
    billing.checkout.session.created
    billing.payout.accrued          billing.payout.transferred
    creator.onboarded   creator.milestone.reached  creator.floor.adjusted
    moderation.flagged  moderation.decision_made

Each event contract documents:
  - schema (field names + types)
  - producer (which service emits it)
  - consumers (which services listen)
  - idempotency key (what makes a duplicate safe to ignore)
  - ordering guarantee (per-key partition order)
  - retry strategy (exponential backoff + DLQ)
  - dead-letter queue (where stuck events land)

Events are the integration surface — services never reach into another
service's DB.
"""

from wildframe_events.topics import Topic
from wildframe_events.event import DomainEvent
from wildframe_events.publisher import EventPublisher, InMemoryEventPublisher, KafkaEventPublisher
from wildframe_events.subscriber import EventSubscriber, InMemoryEventSubscriber

__all__ = [
    "Topic",
    "DomainEvent",
    "EventPublisher",
    "InMemoryEventPublisher",
    "KafkaEventPublisher",
    "EventSubscriber",
    "InMemoryEventSubscriber",
    "KafkaEventSubscriber",
]
