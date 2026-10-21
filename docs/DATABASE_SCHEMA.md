# 📊 Database Schema Reference

> ## ⚠️ Regenerated 2026-10-14 — the previous version described a schema that does not exist
>
> The earlier revision of this file documented 21 tables, **16 of which were
> never created**, under a heading that listed 12 databases while the stack
> creates 17. Writing queries against it would have failed.
>
> Everything below is now **derived from the SQLAlchemy models** — the same
> metadata `scripts/init_schemas.py` passes to `create_all` — not from a
> hand-written list. Regenerating it is a 30-second command (see "How this was
> produced"). If a table below is wrong, the fix is in the model, not here.

## Overview

Wildframe runs **one PostgreSQL database per service** and no shared schema.
There is no ORM-level cross-database foreign key anywhere: services reference
each other's rows by ID only, and consistency is maintained by application
logic and Kafka events.

### Where the truth lives

| Question | Source of truth |
|---|---|
| Which databases exist | `infrastructure/database/init-databases.sql` — it creates databases, roles and grants. It contains **zero `CREATE TABLE` statements**. |
| Which tables and columns exist | The SQLAlchemy models under `services/<svc>/app/models/` (a package) or `services/<svc>/app/models.py` (a module). |
| What actually gets created | `scripts/init_schemas.py` → `scripts/schema_bootstrap.py`, which calls `create_all` and then reconciles missing columns. |
| What exists in a running database | `\d <table>` against the live database. Models and database can drift; `init_schemas.py` reports what it repaired. |

> **There is no migration framework.** No Alembic for this path. Schema change
> means: change the model, run `python scripts/init_schemas.py`, and review
> what it prints. On a populated table it refuses an undeclared column add —
> re-run with `--allow-add <table>.<column>` only after you have confirmed the
> table is stale rather than the column being deliberately absent.

## Table of Contents

1. [How this was produced](#how-this-was-produced)
2. [Databases](#databases)
3. [Tables by database](#tables-by-database)
4. [Shared reference data](#shared-reference-data)
5. [Data flow between services](#data-flow-between-services)
6. [Backup strategy](#backup-strategy)
7. [Migration strategy](#migration-strategy)
8. [Performance](#performance-considerations)
9. [Monitoring & alerts](#monitoring--alerts)

---

## How this was produced

```
cd services/<service>
PYTHONPATH="$PWD:<repo>/packages/sdk/wildframe_auth:<repo>/packages/sdk/wildframe_compliance:<repo>/packages/sdk/wildframe_events" \
  python -c "import app.models, pkgutil, importlib; \
    from sqlalchemy.orm import DeclarativeBase; \
    mods=[app.models]; \
    [mods.append(importlib.import_module('app.models.'+m.name)) for m in pkgutil.iter_modules(app.models.__path__)]; \
    bases={id(b.metadata):b for mod in mods for n in dir(mod) for b in [getattr(mod,n,None)] \
      if isinstance(b,type) and issubclass(b,DeclarativeBase)}; \
    t=sorted({t.name for b in bases.values() for t in b.metadata.tables.values()}); \
    print(len(t), t)"
```

Two details that matter and that a naive `from app.models import Base` gets
wrong:

- **Most services keep `Base` in a module** (`app/models.py`), not a package.
- **Seven services keep several declarative `Base` classes** — content (9),
  admin (6), billing (5), analytics (3), moderation (3), streaming (2). Their
  compliance tables live in separate metadata, so `create_all` on the primary
  `Base` alone silently skips them. `scripts/schema_bootstrap.py` collects every
  `Base` for exactly this reason (see its `collect_bases()` docstring, #980).

---

## Databases

**17 databases**, all created by `infrastructure/database/init-databases.sql`:

| Database | Owned by | Tables |
|---|---|---|
| `auth_db` | auth-service | 8 |
| `users_db` | user-service | 7 |
| `content_db` | content-service | 20 |
| `streaming_db` | streaming-service | 10 |
| `search_db` | search-service | 2 |
| `recommendation_db` | recommendation-service | 2 |
| `billing_db` | billing-service | 14 |
| `analytics_db` | analytics-service | 6 |
| `notification_db` | notification-service | 2 |
| `media_db` | media-pipeline | 6 |
| `admin_db` | admin-service | 10 |
| `creators_db` | creators-service | 10 |
| `moderation_db` | moderation-service | 6 |
| `uploads_db` | uploads-service | 3 |
| `wildframe` | — | default database created by the image; not a service database |
| `wildframe_db` | — | created by the same script; no service maps to it |
| *(plus the default `postgres`)* | — | maintained by the image |

**14 service databases.** `api-gateway` owns none — it is a stateless proxy.

The 14→1 mapping is declared once, in the `SERVICES` dict at the top of
`scripts/init_schemas.py`. Change a service's database name there, not in two
places.

---

## Tables by database

**106 tables total.** Names below are exactly the `__tablename__` values the
models declare.

### `auth_db` — auth-service (8)

`users` · `refresh_tokens` · `token_blacklist` · `login_audit` ·
`security_audits` · `age_verifications` · `consent_records` · `privacy_notices`

### `users_db` — user-service (7)

`user_profiles` · `user_devices` · `user_preferences` ·
`user_subscription_profiles` · `user_consent_records` · `child_accounts` ·
`dsar_requests`

> Note: `child_accounts` and `dsar_requests` have models **and** columns, but
> the routers that serve them are not mounted — see
> [`COMPLIANCE_MASTER_PLAN.md`](COMPLIANCE_MASTER_PLAN.md). A row in either
> table is currently unreachable over HTTP.

### `content_db` — content-service (20)

`content` · `content_series` · `season` · `episode` · `genre` · `content_genre` ·
`cast_member` · `content_cast` · `content_creator` · `content_rating` ·
`content_recommendation` · `reviews` · `ad_configs` · `rights_holders` ·
`territorial_licenses` · `content_audits` · `content_dsar_records` ·
`content_eu` · `content_india` · `content_transfers`

There is no `movies`/`shows`/`seasons`/`episodes` split — a film and a series
episode are both rows in `content`, distinguished by `content_type`, and a
series is a row in `content_series`.

### `streaming_db` — streaming-service (10)

`playback_session` · `video_manifest` · `streaming_quality_profile` ·
`streaming_statistics` · `cdn_region` · `transcoding_job` ·
`download_session` · `content_maturity` · `drm_configs` ·
`accessibility_configs`

> There is no `playback_sessions`/`video_segments`/`streaming_quality_metrics`
> split, and no row-per-segment table: video variants live as JSON on
> `video_manifest.variants`.

### `search_db` — search-service (2)

`search_indexes` · `search_queries`

`search_indexes` is a **projection cache**, not the search engine. It has no
`tsvector` and no GIN index — search is Elasticsearch, and this table exists so
the service can answer "what did we index?" and rebuild.

### `recommendation_db` — recommendation-service (2)

`recommendations` · `user_preferences`

### `billing_db` — billing-service (14)

`subscriptions` · `subscription_tiers` · `purchases` · `invoices` · `refunds` ·
`stripe_webhook_events` · `billing_audits` · `commerce_records` ·
`region_floors` · `creator_pool_entries` · `creator_pool_distributions` ·
`milestones` · `milestone_tranches` · `payout_ledger`

Money is stored as `numeric`, not minor units: `subscriptions.monthly_price`,
`invoices.amount`, `purchases.price`, `refunds.amount`. The unique index on
`invoices` is on `stripe_invoice_id`, not an internal invoice number. There is
no `payments` table — a purchase is a row in `purchases`.

> `payout_ledger` is declared on more than one declarative `Base` in this
> service with disjoint columns, which is why the reconcile pass can merge them
> into NOT NULL columns the primary INSERT does not supply. That is a code
> defect, tracked separately; it is noted here because it is the one place this
> inventory is not a clean read of the database.

### `analytics_db` — analytics-service (6)

`events` · `content_view_events` · `content_performance_metrics` ·
`creator_analytics_snapshots` · `tracking_consents` · `analytics_dsar_exports`

### `notification_db` — notification-service (2)

`notifications` · `notification_preferences`

### `media_db` — media-pipeline (6)

`pipeline_jobs` · `pipeline_stage_logs` · `transcoding_jobs` ·
`video_manifest` · `streaming_quality_profile` · `outbox_events`

`transcoding_jobs`/`video_manifest`/`streaming_quality_profile` also exist in
`streaming_db`. They are **different tables in different databases** with
different columns, not a shared table.

### `admin_db` — admin-service (10)

`admin_audit_logs` · `system_configs` · `system_alerts` ·
`content_moderations` · `user_moderations` · `legal_documents` · `processors` ·
`transfer_records` · `eu_compliance` · `india_compliance`

### `creators_db` — creators-service (10)

`creator_accounts` · `creator_onboarding` · `creator_commerce` ·
`creator_payouts` · `creator_pool_balances` · `effective_floors` ·
`payout_ledger` · `milestones` · `milestone_tranches` · `inbound_events`

### `moderation_db` — moderation-service (6)

`content_flags` · `moderation_decisions` · `creator_strikes` ·
`dmca_takedowns` · `review_moderation` · `outbox_events`

### `uploads_db` — uploads-service (3)

`upload_sessions` · `upload_chunks` · `outbox_events`

`outbox_events` appears in four services' databases (`media_db`, `moderation_db`,
`uploads_db`, and see below). Each is its own table; the outbox pattern is
deliberately duplicated per service so no service reads another's database.

### Repeated table names

`outbox_events` (media, moderation, uploads), `payout_ledger` (billing,
creators), `milestones` + `milestone_tranches` (billing, creators),
`video_manifest` + `streaming_quality_profile` + `transcoding_job(s)`
(streaming, media). **These are not shared tables.** Each service owns its own
copy with its own columns; the name collision is a naming convention, not a
contract. Do not write a query that assumes two of them are the same row.

---

## Shared Reference Data

### Cross-Database References

Services reference each other's rows **by ID only**. There is no cross-database
foreign key, and there cannot be one — PostgreSQL FKs cannot span databases.

```
Example: a content view recorded by analytics
┌─────────────────────────────┐
│  analytics_db               │
│  content_view_events        │
│  ├─ viewer_id  → auth_db    │
│  │                users.id  │
│  │                (no FK)    │
│  └─ content_id  → content_db│
│                 content.id  │
│                 (no FK)     │
└─────────────────────────────┘
```

**Why not foreign keys?**

- A FK cannot span databases in PostgreSQL at all.
- It would couple service lifecycles and deployment order.
- Consistency is maintained by application logic and by Kafka events instead,
  which is also what makes a service independently deployable.

The cost is real and worth stating: **a dangling ID is possible**, and nothing
in the database will tell you. Referential integrity across services is a
runtime and observability concern, not a constraint.

---

## Data Flow Between Services

### Synchronous request flow

Auth is checked at the destination service, not only at the gateway — the
gateway is a transparent proxy.

```
1. Registration
   POST /api/v1/auth/register  → auth-service
   ├─ INSERT auth_db.users, refresh_tokens
   └─ publish user.registered

2. Start playback
   POST /api/v1/playback-sessions → streaming-service
   ├─ verify JWT (RS256, aud=wildframe-api)
   ├─ INSERT streaming_db.playback_session
   └─ INSERT content_db row? no — read content_db? no: content-service answers

3. Recommendations
   GET /api/v1/recommendations → recommendation-service
   └─ SELECT recommendation_db.recommendations
      then content-service enriches each id
```

### Event-driven updates

Services never read each other's database. Coordination happens on Kafka. The
topic list and the per-service produce/consume ACL are both defined in
`packages/sdk/wildframe_events/topics.py` — `_SERVICE_ACL` is the source of
truth for who may publish and who may subscribe, and
`scripts/kafka_init.py` creates topics and ACLs from it.

```
user.registered
    producer: auth-service
    consumers: user-service
                 └─ INSERT users_db.user_profiles

content.uploaded
    producer: uploads-service
    consumers: media-pipeline, content-service
                 media-pipeline: INSERT media_db.pipeline_jobs, then emits
                   content.scanned → content.metadata_extracted →
                   content.encoded → content.packaged
                 content-service: records the upload on the content row
```

Full topic names, in `topics.py`: `user.registered`, `user.moderated`,
`content.uploaded`, `content.uploaded.aborted`, `content.scanned`,
`content.metadata_extracted`, `content.encoded`, `content.packaged`,
`content.pipeline.failed`, `content.published`, `content.unpublished`,
`content.deleted`, `billing.checkout.session.created`,
`billing.subscription.created`, `billing.subscription.updated`,
`billing.subscription.cancelled`, `billing.payout.accrued`,
`billing.payout.transferred`, `creator.onboarded`, `creator.floor.adjusted`,
`creator.milestone.reached`, `creator.suspended`, `moderation.flagged`,
`moderation.decision_made`.

**There is no `video.watched` topic.** Watch events are recorded by
`analytics-service` in `content_view_events`; if a flow you are tracing appears
to need one, that is a design question, not a lookup.

> **Event handlers must be idempotent.** Delivery is at-least-once, so the same
> message can arrive twice. Services that publish from a transactional outbox
> (`outbox_events` in `media_db`, `moderation_db`, `uploads_db`) are the ones
> that can replay.

---

## Backup Strategy

### Backup Schedule

```
Full Backups (Daily)
├─ Time: 02:00 UTC
├─ Retention: 30 days
└─ Location: S3 with cross-region replication

Incremental Backups (Every 6 hours)
├─ Times: 00:00, 06:00, 12:00, 18:00 UTC
├─ Retention: 7 days
└─ Location: S3 regional

Transaction Logs (Continuous)
├─ Archive every 30 seconds
├─ Retention: 30 days
└─ Location: S3 with immutable version lock
```

### Backup Verification

```bash
# Weekly: Test restore on staging database
# Monthly: Full restore to verify data integrity
# Post-Incident: Verify backup captures all recent changes
```

### RTO/RPO Targets

| Scenario | RTO | RPO |
|----------|-----|-----|
| Single table corruption | 1 hour | 1 minute |
| Full database loss | 4 hours | 6 hours |
| Entire region down | 24 hours | 1 hour |

---

## Migration Strategy

> **There is no migration framework on this path.** No Alembic for schema
> creation. This section describes the actual procedure.

### Adding a column

1. **Change the model.** Add the column to the SQLAlchemy class in
   `services/<svc>/app/models/`.
2. **Run the bootstrap** from the repo root:
   ```bash
   python scripts/init_schemas.py
   ```
   It calls `create_all` (which creates new *tables*) and then reconciles
   columns: any column the model declares and the table lacks is added, and
   each one is printed as `added column <table>.<column>`.
3. **On a populated table an undeclared add is refused**, because the pass
   cannot distinguish drift from a column a human deliberately dropped. Once
   you have confirmed the table is stale rather than the column being
   deliberately absent:
   ```bash
   python scripts/init_schemas.py --allow-add <table>.<column>
   ```
   The flag is deliberately not a committed allowlist — a drop belongs to a
   database, not to the repo.
4. **If the bootstrap exits 1 on a `NotNullViolationError`**, a `nullable=False`
   column has no server default and existing rows violate it. That needs a human
   `ALTER TABLE` (or a server default) — the reconcile pass will not invent
   data.
5. **Verify against the running stack**, not against the model:
   ```bash
   docker compose -f deployments/docker-compose.dev.yml exec postgres \
     psql -U wildframe -d <db_name> -c "\d <table>"
   ```
6. **On a fresh volume** confirm the whole path: `docker compose ... down -v`
   then `up -d` then `init_schemas.py`. A schema that only works on a populated
   database is not verified.

### Adding a table

Same, minus the reconcile: `create_all` creates tables that do not exist.
Nothing needs to change in `infrastructure/database/init-databases.sql` —
that file creates *databases*, not tables, and contains zero `CREATE TABLE`.

### Backward compatibility

- New columns need defaults, or a backfill, before a `NOT NULL`.
- Deploy the code that tolerates the new column before adding it.
- Old code must ignore unknown columns — which it does, since SQLAlchemy
  selects by name.

---

## Performance Considerations

> Carried over verbatim from the previous revision. **Not re-verified** — see
> the note at the foot of this file. The example queries reference
> `movies` and `watch_history`, which **are not real tables**; read them as
> query-shape illustrations, not as queries to run.

### Query Optimization

```sql
-- ❌ Slow: no supporting index
SELECT * FROM content WHERE title = 'Inception';

-- ✅ Fast: uses the (slug, title) index
SELECT * FROM content WHERE slug = 'inception';

-- ❌ Slow: full table scan
SELECT * FROM content_view_events WHERE started_at > NOW() - INTERVAL '30 days';

-- ✅ Fast: uses the (viewer_id, started_at) index
SELECT * FROM content_view_events
WHERE viewer_id = '...' AND started_at > NOW() - INTERVAL '30 days';
```

### Caching Strategy

```
Application Tier
    ↓ (cache-aside, Redis, TTL in minutes)
Redis
    ↓ (miss)
PostgreSQL
```

Redis is used for rate limiting, caching and event deduplication. It is
**not** a source of truth: every key here must be reconstructible from
PostgreSQL.

### Connection Pooling

Each service opens an async SQLAlchemy engine against its own database. Pool
size and timeout come from that service's `DATABASE_URL` and
`app/core/database.py` — read them there rather than assuming a value. The dev
stack runs a single `postgres` container with no PgBouncer in front of it, so
the "20 connections / 1000 max clients" figures in earlier revisions described
a production topology that is **not** what this repo deploys.

---

## Monitoring & Alerts

### Key Metrics

Scrape targets are defined in `deployments/docker-compose.dev.yml` and pinned by
`tests/contract/test_prometheus_scrape_targets.py` and
`tests/contract/test_metrics_scrape_credentials.py`. Grafana dashboards and the
alerting rules are provisioned under `infrastructure/monitoring/`.

The alert thresholds listed in earlier revisions (connection count > 90%,
disk > 80%, replication lag > 1s) are **not** in the repo as alert rules.
Treat them as suggestions.

### Slow-query logging

```sql
-- Session or role level; not set by this repo's compose file
ALTER SYSTEM SET log_min_duration_statement = 1000;  -- 1 second, in ms
SELECT pg_reload_conf();
```

PostgreSQL logs are shipped to Loki. See [`MONITORING.md`](MONITORING.md) for
the collector configuration.

---

## Resources

- [PostgreSQL Docs](https://www.postgresql.org/docs/)
- [`init_schemas.py`](../scripts/init_schemas.py) — the bootstrap that creates these tables
- [Schema bootstrap runner](../scripts/schema_bootstrap.py) — `create_all` + column reconcile
- [`init-databases.sql`](../infrastructure/database/init-databases.sql) — database/role creation
- [API Documentation](API_DOCUMENTATION.md)
- [Backup Procedures](OPERATIONS.md)
- [Compliance endpoint reachability](COMPLIANCE_MASTER_PLAN.md) — which of these tables are reachable over HTTP


- [PostgreSQL Docs](https://www.postgresql.org/docs/)
- [Database Design Guide](ARCHITECTURE.md#database-schema)
- [API Documentation](API_DOCUMENTATION.md)
- [Backup Procedures](OPERATIONS.md#backup-strategy)

---

**Last verified**: 2026-10-14 — table inventory regenerated from the
SQLAlchemy models; database list from `infrastructure/database/init-databases.sql`;
service→database mapping from `scripts/init_schemas.py`.

**Not verified in this pass:** the Backup Strategy, Performance Considerations
and Monitoring & Alerts sections below are unchanged design guidance carried
over from the previous revision. They were not checked against a running
database or a backup job, and no backup tooling was found wired into the dev
stack. Treat them as proposals until someone confirms them against the
production runbook in [`OPERATIONS.md`](OPERATIONS.md).
