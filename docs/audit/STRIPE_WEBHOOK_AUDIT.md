# Stripe Webhook Audit

> **Audit note: created by ChatGPT during the Wildframe code audit.**

## Finding

The Stripe webhook implementation uses an in-process Python dictionary as its primary event-level idempotency guard. `_processed_events` is lost on process restart and is not shared between replicas.

The billing service also marks an event as processed only after its handler returns. Two concurrent deliveries can therefore both observe the event as unprocessed and execute the handler before either request records the in-process marker.

Some individual handlers use the same in-process mechanism with derived keys. That does not provide a durable, transactional uniqueness guarantee.

## Impact

Under retries, concurrent webhook deliveries, process restarts, or multiple billing-service replicas, the same Stripe event may be applied more than once. For billing this can cause duplicate local records or repeated state transitions.

The Stripe transfer API has an idempotency key, but that does not make the local database side effects idempotent.

## Required fix

Introduce a durable `stripe_webhook_events` table with a unique constraint on the Stripe event ID. Claim the event transactionally before executing side effects, or use an equivalent database-backed inbox/idempotency pattern. Mark processing state only as part of a transactionally safe workflow and define recovery for failed processing.

Add integration tests for:

- duplicate sequential delivery;
- duplicate concurrent delivery;
- delivery after process restart;
- delivery to multiple service replicas;
- handler failure followed by retry;
- invalid signature;
- unknown event type;
- out-of-order related events.

## Status

**Suspicious / high-confidence reliability bug.** Requires a database-backed implementation and integration testing; it should not be hidden by changing the in-process set alone.
