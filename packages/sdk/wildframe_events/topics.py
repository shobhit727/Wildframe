"""Canonical topic names for the Wildframe event backbone.

Every topic is a string constant. Producing services emit to the topic;
consuming services subscribe. Topic names use dot-notation grouping:
  <domain>.<action>

Idempotency: every event carries an ``idempotency_key`` derived from
the domain entity's identity (e.g. ``billing.payout.accrued`` uses
``payout:{creator_id}:{cycle_start}``). Retried events with the same
key are deduplicated at the consumer.

Ordering: within a partition key (typically the entity ID), events are
strictly ordered. Cross-key ordering is not guaranteed.

Retry strategy: exponential backoff with jitter, max 3 attempts before
DLQ. Non-retryable errors (e.g. virus_detected) fail immediately.

DLQ: ``<topic>.dlq`` — every stuck event lands here with its error
context, never in a black hole.
"""


class Topic:
    """Canonical Kafka topic names for all Wildframe domain events."""

    # -----------------------------------------------------------------------
    # Content domain (producer: uploads-service, media-pipeline, content-service)
    # -----------------------------------------------------------------------

    #: Emitted when an upload session completes and the file is stored.
    #: Producer: uploads-service. Consumers: media-pipeline, content-service.
    #: Idempotency key: ``upload:{upload_session_id}``
    CONTENT_UPLOADED = "content.uploaded"

    #: An upload was aborted or timed out.
    #: Producer: uploads-service. Consumers: content-service (cleanup).
    #: Idempotency key: ``upload:aborted:{upload_session_id}``
    CONTENT_UPLOAD_ABORTED = "content.uploaded.aborted"

    #: Virus scan result (pass or fail). If fail, pipeline halts immediately.
    #: Producer: media-pipeline. Consumers: moderation-service, notification-service.
    #: Idempotency key: ``scan:{pipeline_job_id}``
    CONTENT_SCANNED = "content.scanned"

    #: Technical metadata (resolution, codec, duration) extracted.
    #: Producer: media-pipeline. Consumers: content-service, search-service.
    #: Idempotency key: ``metadata:{pipeline_job_id}``
    CONTENT_METADATA_EXTRACTED = "content.metadata_extracted"

    #: Encoding complete (all bitrates + HLS/DASH packages).
    #: Producer: media-pipeline. Consumers: streaming-service, content-service.
    #: Idempotency key: ``encoded:{pipeline_job_id}``
    CONTENT_ENCODED = "content.encoded"

    #: Content is packaged (HLS + DASH) and uploaded to object storage.
    #: Producer: media-pipeline. Consumers: streaming-service, content-service.
    #: Idempotency key: ``packaged:{pipeline_job_id}``
    CONTENT_PACKAGED = "content.packaged"

    #: Content is published and available for discovery/playback.
    #: Producer: content-service. Consumers: search-service, recommendation-service,
    #:   notification-service, analytics-service.
    #: Idempotency key: ``published:{content_id}``
    CONTENT_PUBLISHED = "content.published"

    #: Pipeline failed irrecoverably (virus detected, encoding error, etc.).
    #: Producer: media-pipeline. Consumers: notification-service, content-service.
    #: Idempotency key: ``failed:{pipeline_job_id}``
    CONTENT_PIPELINE_FAILED = "content.pipeline.failed"

    # -----------------------------------------------------------------------
    # Billing domain (producer: billing-service)
    # -----------------------------------------------------------------------

    #: New subscription created (AVOD→SVOD upgrade).
    #: Producer: billing-service (via Stripe webhook). Consumers: user-service,
    #:   notification-service, analytics-service, creators-service.
    #: Idempotency key: ``sub:created:{stripe_customer_id}``
    BILLING_SUBSCRIPTION_CREATED = "billing.subscription.created"

    #: Subscription updated (tier change, payment method change).
    #: Producer: billing-service. Consumers: user-service, notification-service.
    #: Idempotency key: ``sub:updated:{subscription_id}:{updated_at}``
    BILLING_SUBSCRIPTION_UPDATED = "billing.subscription.updated"

    #: Subscription cancelled (SVOD→AVOD).
    #: Producer: billing-service. Consumers: user-service, notification-service,
    #:   analytics-service.
    #: Idempotency key: ``sub:cancelled:{subscription_id}``
    BILLING_SUBSCRIPTION_CANCELLED = "billing.subscription.cancelled"

    #: Stripe Checkout Session created (pending payment).
    #: Producer: billing-service. Consumers: analytics-service.
    #: Idempotency key: ``checkout:{stripe_session_id}``
    BILLING_CHECKOUT_SESSION_CREATED = "billing.checkout.session.created"

    #: Creator payout accrued (earned but not yet transferred).
    #: Producer: billing-service (accrue_payout). Consumers: creators-service.
    #: Idempotency key: the payout's idempotency_key
    BILLING_PAYOUT_ACCRUED = "billing.payout.accrued"

    #: Creator payout transferred via Stripe Connect.
    #: Producer: billing-service. Consumers: creators-service, notification-service.
    #: Idempotency key: the payout's idempotency_key
    BILLING_PAYOUT_TRANSFERRED = "billing.payout.transferred"

    # -----------------------------------------------------------------------
    # Creator domain (producer: creators-service)
    # -----------------------------------------------------------------------

    #: Creator onboarded (profile created, Stripe Connect initiated).
    #: Producer: creators-service. Consumers: billing-service, notification-service.
    #: Idempotency key: ``creator:onboarded:{creator_id}``
    CREATOR_ONBOARDED = "creator.onboarded"

    #: A milestone was reached (tranche eligible for release).
    #: Producer: creators-service. Consumers: billing-service, notification-service.
    #: Idempotency key: ``milestone:{milestone_id}:{tranche_number}``
    CREATOR_MILESTONE_REACHED = "creator.milestone.reached"

    #: A creator's living-wage floor was adjusted (quarterly review).
    #: Producer: creators-service. Consumers: billing-service, analytics-service.
    #: Idempotency key: ``floor:{creator_id}:{effective_date}``
    CREATOR_FLOOR_ADJUSTED = "creator.floor.adjusted"

    #: Creator suspended (3 active strikes).
    #: Producer: moderation-service. Consumers: billing-service, content-service,
    #:   notification-service, creators-service.
    #: Idempotency key: ``suspended:{creator_id}``
    CREATOR_SUSPENDED = "creator.suspended"

    # -----------------------------------------------------------------------
    # Moderation domain (producer: moderation-service)
    # -----------------------------------------------------------------------

    #: Content flagged for review.
    #: Producer: moderation-service. Consumers: notification-service, analytics-service.
    #: Idempotency key: ``flagged:{flag_id}``
    MODERATION_FLAGGED = "moderation.flagged"

    #: Moderation decision made (approve/reject/escalate).
    #: Producer: moderation-service. Consumers: notification-service, creators-service,
    #:   analytics-service, content-service.
    #: Idempotency key: ``decision:{decision_id}``
    MODERATION_DECISION_MADE = "moderation.decision_made"

    # -----------------------------------------------------------------------
    # Dead-letter topics
    # -----------------------------------------------------------------------

    #: DLQ for any event that exhausted retries.
    #: Every service that consumes events also produces to its DLQ on failure.
    #: Convention: ``<original_topic>.dlq``
    DLQ_SUFFIX = ".dlq"


# ---------------------------------------------------------------------------
# Topic metadata — for documentation and validation
# ---------------------------------------------------------------------------

TOPIC_METADATA = {
    Topic.CONTENT_UPLOADED: {
        "producer": "uploads-service",
        "consumers": ["media-pipeline", "content-service"],
        "idempotency_key_pattern": "upload:{upload_session_id}",
        "retry_strategy": "exponential_backoff(max_attempts=3, base=1s, cap=60s)",
    },
    Topic.CONTENT_UPLOAD_ABORTED: {
        "producer": "uploads-service",
        "consumers": ["content-service"],
        "idempotency_key_pattern": "upload:aborted:{upload_session_id}",
        "retry_strategy": "exponential_backoff(max_attempts=3)",
    },
    Topic.CONTENT_SCANNED: {
        "producer": "media-pipeline",
        "consumers": ["moderation-service", "notification-service"],
        "idempotency_key_pattern": "scan:{pipeline_job_id}",
        "retry_strategy": "exponential_backoff(max_attempts=3)",
        "non_retryable": ["virus_detected"],
    },
    Topic.CONTENT_METADATA_EXTRACTED: {
        "producer": "media-pipeline",
        "consumers": ["content-service", "search-service"],
        "idempotency_key_pattern": "metadata:{pipeline_job_id}",
        "retry_strategy": "exponential_backoff(max_attempts=3)",
    },
    Topic.CONTENT_ENCODED: {
        "producer": "media-pipeline",
        "consumers": ["streaming-service", "content-service"],
        "idempotency_key_pattern": "encoded:{pipeline_job_id}",
        "retry_strategy": "exponential_backoff(max_attempts=3)",
    },
    Topic.CONTENT_PACKAGED: {
        "producer": "media-pipeline",
        "consumers": ["streaming-service", "content-service"],
        "idempotency_key_pattern": "packaged:{pipeline_job_id}",
        "retry_strategy": "exponential_backoff(max_attempts=3)",
    },
    Topic.CONTENT_PUBLISHED: {
        "producer": "content-service",
        "consumers": [
            "search-service",
            "recommendation-service",
            "notification-service",
            "analytics-service",
        ],
        "idempotency_key_pattern": "published:{content_id}",
        "retry_strategy": "exponential_backoff(max_attempts=3)",
    },
    Topic.CONTENT_PIPELINE_FAILED: {
        "producer": "media-pipeline",
        "consumers": ["notification-service", "content-service"],
        "idempotency_key_pattern": "failed:{pipeline_job_id}",
        "retry_strategy": "none",  # Terminal event
    },
    Topic.BILLING_SUBSCRIPTION_CREATED: {
        "producer": "billing-service",
        "consumers": [
            "user-service",
            "notification-service",
            "analytics-service",
            "creators-service",
        ],
        "idempotency_key_pattern": "sub:created:{stripe_customer_id}",
        "retry_strategy": "exponential_backoff(max_attempts=5)",
    },
    Topic.BILLING_SUBSCRIPTION_UPDATED: {
        "producer": "billing-service",
        "consumers": ["user-service", "notification-service"],
        "idempotency_key_pattern": "sub:updated:{subscription_id}:{updated_at}",
        "retry_strategy": "exponential_backoff(max_attempts=3)",
    },
    Topic.BILLING_SUBSCRIPTION_CANCELLED: {
        "producer": "billing-service",
        "consumers": ["user-service", "notification-service", "analytics-service"],
        "idempotency_key_pattern": "sub:cancelled:{subscription_id}",
        "retry_strategy": "exponential_backoff(max_attempts=3)",
    },
    Topic.BILLING_CHECKOUT_SESSION_CREATED: {
        "producer": "billing-service",
        "consumers": ["analytics-service"],
        "idempotency_key_pattern": "checkout:{stripe_session_id}",
        "retry_strategy": "exponential_backoff(max_attempts=3)",
    },
    Topic.BILLING_PAYOUT_ACCRUED: {
        "producer": "billing-service",
        "consumers": ["creators-service"],
        "idempotency_key_pattern": "payout:{idempotency_key}",
        "retry_strategy": "exponential_backoff(max_attempts=5)",
    },
    Topic.BILLING_PAYOUT_TRANSFERRED: {
        "producer": "billing-service",
        "consumers": ["creators-service", "notification-service"],
        "idempotency_key_pattern": "payout:{idempotency_key}",
        "retry_strategy": "exponential_backoff(max_attempts=5)",
    },
    Topic.CREATOR_ONBOARDED: {
        "producer": "creators-service",
        "consumers": ["billing-service", "notification-service"],
        "idempotency_key_pattern": "creator:onboarded:{creator_id}",
        "retry_strategy": "exponential_backoff(max_attempts=3)",
    },
    Topic.CREATOR_FLOOR_ADJUSTED: {
        "producer": "creators-service",
        "consumers": ["billing-service", "analytics-service"],
        "idempotency_key_pattern": "floor:{creator_id}:{effective_date}",
        "retry_strategy": "exponential_backoff(max_attempts=3)",
    },
    Topic.CREATOR_MILESTONE_REACHED: {
        "producer": "creators-service",
        "consumers": ["billing-service", "notification-service"],
        "idempotency_key_pattern": "milestone:{milestone_id}:{tranche_number}",
        "retry_strategy": "exponential_backoff(max_attempts=3)",
    },
    Topic.MODERATION_FLAGGED: {
        "producer": "moderation-service",
        "consumers": ["notification-service", "analytics-service"],
        "idempotency_key_pattern": "flagged:{flag_id}",
        "retry_strategy": "exponential_backoff(max_attempts=3)",
    },
    Topic.MODERATION_DECISION_MADE: {
        "producer": "moderation-service",
        "consumers": [
            "notification-service",
            "creators-service",
            "analytics-service",
            "content-service",
        ],
        "idempotency_key_pattern": "decision:{decision_id}",
        "retry_strategy": "exponential_backoff(max_attempts=3)",
    },
    Topic.CREATOR_SUSPENDED: {
        "producer": "moderation-service",
        "consumers": [
            "billing-service",
            "content-service",
            "notification-service",
            "creators-service",
        ],
        "idempotency_key_pattern": "suspended:{creator_id}",
        "retry_strategy": "exponential_backoff(max_attempts=5)",
    },
}
