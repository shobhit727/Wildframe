# WildFrame — Product Vision & Architecture Charter

WildFrame is a **global-first OTT platform built exclusively for independent
animators**. If a generic streaming service is "Netflix," WildFrame is **"Nebula
for indie animation"** — but with a hard economic guarantee: every approved
creator has a **path to a living wage**. The product exists to make independent
animation economically sustainable, not just visible.

This document is the source of truth for product scope and the architectural
decisions that follow from it. Code that contradicts this document is wrong.

---

## 1. The Core Loop

```
creator uploads → user discovers → user watches → user pays
      ↑ creator gets paid → creator uploads again ←
```

Every feature must strengthen this loop. If a feature doesn't make the loop
turn faster or fairer, it doesn't ship.

---

## 2. The Sustenance Engine (the differentiator)

A generic platform pays creators *whatever is left*. WildFrame **floors** creator
income against a living wage and backfills the gap from a shared pool. This is
the product. Nothing else matters if this does not work.

### 2.1 Living-Wage Floor

A per-minute guaranteed rate, indexed to a local living wage and reviewed
quarterly. Stored per creator in their payout currency. Example values
(the real table lives in `services/billing` and is admin-editable):

| Region | Floor (per finished minute) |
|--------|-----------------------------|
| United States | $11.70 – $39.00 |
| India | ₹975 – ₹3,250 |

A creator's **effective floor** = their tier × regional index × minutes of
their content consumed (quality-adjusted). The floor is a *minimum guarantee*,
not a cap — outsized performers earn far more.

### 2.2 Creator Pool (progressive redistribution)

A percentage of net revenue (default 15%) flows into the **Creator Pool** each
payout cycle. The pool is redistributed **pro-rata toward creators who are
below their floor**, weighted toward emerging studios. Top earners contribute
more and draw nothing until the floor is broadly met. The split is transparent
and auditable.

### 2.3 Milestone-Tranched Funding

Large creator commitments are **not** paid upfront. Funds release in tranches
**10 / 20 / 30 / 40** tied to verified milestones (script, animatic, first cut,
final). Each tranche has a **kill clause**: miss a milestone, the remaining
tranches pause and funds revert to the pool. This protects capital and incentivizes
delivery without being punitive.

### 2.4 Success Metrics (MVE)

The Minimum Viable Ecosystem proves the model works, not that it's big:

- **3+ creators paid above their floor**
- **50+ paying subscribers**
- **zero churn** over the window
- **60 days sustained**

We optimize for these, never for vanity scale.

---

## 3. Revenue Tiers (user side)

| Tier | Model | Price | Notes |
|------|-------|-------|-------|
| AVOD | Ad-supported, free | $0 | Broad funnel; creator earns from pool + ad rev share. |
| SVOD | Subscription | **$7.99/mo** | Core tier; **≥55% of net revenue to creators**. |
| TVOD | Pay-per-view | per-title | Individual film purchases. |

Every tier guarantees the creator share is calculated **before** platform
costs are taken. The ≥55% creator share is a contractual floor, not a target.

---

## 4. Creator Payouts

Payouts run through **Stripe Connect** so creators are paid directly to their
own accounts (global, multi-currency). The service:

- accrues creator earnings per cycle (views × floor + share + pool top-up),
- generates an invoice with a full breakdown,
- issues a Stripe Connect transfer,
- records the idempotency key so a retried webhook /retried payout can't
  double-pay.

Stripe webhooks (`payment_intent.succeeded`, `invoice.paid`,
`customer.subscription.updated`) are consumed idempotently and land in the
ledger exactly once.

---

## 5. Services (current → target)

The current repo has 12 generic OTT services. Under this charter they are
**re-specialized** for animation and three new services are added. Services stay
independent (own DB, migrations, events, tests, Dockerfile).

### 5.1 Existing services, re-specialized

| Service | New focus |
|---------|-----------|
| api-gateway | routing, auth, rate-limit, creator-enforce |
| auth-service | JWT, OAuth2, RBAC (roles: viewer / creator / moderator / admin) |
| user-service | viewer profiles, creator profiles, devices, sessions |
| content-service | **animation** catalog: films/series/episodes, maturity, dubs, subs |
| streaming-service | HLS/DASH playback of animated content |
| search-service | search over animation catalog + creator pages |
| recommendation-service | recs (algorithm arrives post-MVE; editorial now) |
| billing-service | **the Sustenance Engine**: tiers, floor, pool, tranches, Stripe |
| analytics-service | per-creator + per-title analytics |
| notification-service | multi-channel notifications |
| admin-service | moderation queue, platform alerts, system config, audit log |
| media-pipeline | the upload → encode → package pipeline (see §6) |

### 5.2 New services

| Service | Responsibility |
|---------|----------------|
| **uploads-service** | signed upload URLs, chunked/resumable upload, upload → pipeline handoff |
| **creators-service** | creator onboarding, KYC/Stripe Connect link, floor config, pool balance, milestones/tranches, payouts ledger |
| **moderation-service** | content review queue, flag decisions, escalation, creator strikes |

These are **separate domains**. `moderation` is not stuffed into `admin`; a
creator's financial lifecycle is not stuffed into `billing`. Clean boundaries.

---

## 6. The Media Pipeline

A real pipeline, every stage independently scalable:

```
Signed Upload → Quarantine → Virus Scan → Metadata Extract
    → Thumbnail Generate → Audio/Subtitle Extract
    → FFmpeg Multi-bitrate Encode
    → HLS Package → DASH Package
    → Object Storage (S3) → CDN Distribute → Playback
```

Each stage emits an event on success/failure and is retryable. A stuck job
lands in a dead-letter path, not a black hole. The pipeline is owned by
**media-pipeline** + **uploads-service** together.

---

## 7. Event Architecture (event-driven backbone)

Async-first. Every state change is an event. Core topics:

```
content.uploaded  content.scanned  content.metadata_extracted
content.encoded    content.packaged   content.published
billing.subscription.created/updated/cancelled
billing.checkout.session.created
billing.payout.accrued  billing.payout.transferred
creator.onboarded   creator.milestone.reached  creator.floor.adjusted
moderation.flagged  moderation.decision_made
```

Every contract documents: schema, producer, consumers, idempotency key,
ordering guarantee, retry strategy, and dead-letter queue. Events are the
integration surface — services never reach into another service's DB.

---

## 8. Architectural Principles (mandatory)

Clean Architecture, DDD, SOLID, Hexagonal where useful, Repository Pattern,
Dependency Injection, Unit of Work, CQRS where appropriate, API versioning,
Twelve-Factor App, Event-Driven.

- **Domain first:** services own their domain logic; infrastructure (DB, queue,
  storage) is a detail swapped via ports/adapters.
- **Anti-corruption layer:** translate at the boundary; never let a Stripe
  webhook shape leak into the core payout model.
- **Idempotency everywhere:** every mutating operation keys on an idempotency
  ID. Retries must be safe.
- **Async-first:** I/O-bound work is async; the pipeline and notifications are
  driven by events.

Every significant decision is documented with the trade-off and the rejected
alternative.

---

## 9. Database Design Standard

For every table: purpose, columns, constraints, indexes, foreign keys,
cascading rules, partitioning strategy, expected row count, and optimization
notes. Schemas are designed for growth — not "move fast," but "move correctly
so we never have to migrate a billion-row table at 3am."

---

## 10. API Design Standard

Every endpoint documents: route, request schema, response schema, validation,
authorization (RBAC), rate limits, and possible errors. OpenAPI-compatible,
consistent naming, versioned (`/api/v1`, …).

---

## 11. Observability (everything debuggable in production)

- structured JSON logs (with trace + request + correlation ids),
- OpenTelemetry tracing across service boundaries,
- Prometheus metrics + Grafana dashboards,
- health / readiness / liveness probes on every service,
- SLO/SLIs + alert rules, error tracking (Sentry), analytics (Plausible-equiv).

If it isn't observable, it isn't shipped.

---

## 12. Security (assume hostile users)

JWT + OAuth2, RBAC, CSRF, CORS, input validation, output sanitization, secret
management, encryption at rest/transit, audit logs, device management, rate
limiting, threat detection. **Never trust client input.** Creator financial
data is the highest-sensitivity class.

---

## 13. Frontend Architecture

Feature-first. Routing, shared UI, API SDK, Zustand/React Query state, auth
flow, HLS/DASH player, theme system, forms, error boundaries, loading states,
optimistic updates, offline-aware where sensible. Must grow for years without
rot. Includes a **creator dashboard** (analytics, earnings, milestones) and a
**moderator dashboard**, not only a viewer UI.

---

## 14. Deployment & CI/CD

Docker per service, Kubernetes manifests, Terraform (VPC, EKS, RDS, ElastiCache,
S3, CloudFront). CI runs lint, type-check, per-service tests (timed), and a
no-push Docker build. A `v*` tag drafts a release. Twelve-factor config via env.

---

## 15. Code Standard

Strongly typed, useful comments, no duplication, no massive classes/functions,
loose coupling, SOLID, testable, production-ready. Public functions documented.
Modules own one responsibility. **Never optimize for writing less code or moving
quickly. Optimize for software that operates in production.**
