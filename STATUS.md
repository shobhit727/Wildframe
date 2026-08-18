# Wildframe Status

**Last reviewed:** August 2026

## Overall

**Active development. Not production-ready.**

The application and CI foundations are substantially implemented, but production deployment still requires AWS/EKS configuration, runtime secrets, operational hardening, load testing, DRM, and production observability work.

## Services

All 15 backend services have Dockerfiles, Helm deployment definitions, health endpoints, and CI test jobs:

- api-gateway
- auth-service
- user-service
- content-service
- streaming-service
- search-service
- recommendation-service
- billing-service
- analytics-service
- notification-service
- media-pipeline
- creators-service
- moderation-service
- uploads-service
- admin-service

## CI status

The current CI/CD workflow is designed to validate:

- Ruff, Black, and mypy.
- Helm lint and rendering for default, staging, and production configuration.
- All 15 backend test matrices.
- SDK tests.
- Frontend lint, type-check, tests, and production build.
- Docker builds for all 15 backend services and the frontend.
- Trivy vulnerability scanning and Semgrep SAST.

Validation failures are intended to block the workflow. Deployment jobs run only after the relevant build and validation jobs succeed.

## Container publishing

Pushes to `develop` and `main` publish immutable commit-SHA-tagged images to GHCR for every backend service and the frontend.

Kubernetes deployment selects the exact SHA tag produced by the same GitHub Actions run. It does not rely on a mutable `latest` tag or the Helm chart's `appVersion`.

## Kubernetes deployment

### Staging

`develop` deploys to the `wildframe-staging` namespace in the `wildframe-staging` EKS cluster.

### Production

`main` deploys to the `wildframe-production` namespace in the `wildframe-production` EKS cluster.

Production should be protected by a GitHub Environment approval rule.

Deployment validation waits for all 15 backend deployments and then performs `/health` checks against every backend service from inside the cluster.

## Required deployment configuration

GitHub Environment secrets required by the deployment workflow:

| Secret | Purpose |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | AWS IAM role assumed through GitHub OIDC |
| `WILDFRAME_JWT_SECRET` | JWT signing secret injected through Kubernetes Secret |
| `WILDFRAME_POSTGRES_PASSWORD` | PostgreSQL password injected through Kubernetes Secret |

The EKS clusters and their supporting infrastructure must already exist. This repository does not claim to create production clusters automatically from the application deployment workflow.

## Remaining production work

- [ ] Complete AWS/EKS environment configuration.
- [ ] Configure GitHub Environment protection and approvals.
- [ ] Configure runtime secrets and secret rotation.
- [ ] Validate PostgreSQL, Redis, Kafka, and Elasticsearch production topology.
- [ ] Add load/capacity tests and SLOs.
- [ ] Complete production observability sinks, dashboards, and alert routing.
- [ ] Add backups, disaster recovery, and restore testing.
- [ ] Complete ingress, TLS, DNS, CDN, and media delivery configuration.
- [ ] Implement production DRM.
- [ ] Complete payment-provider production configuration and compliance.

## Documentation rule

Historical files such as completion summaries and old quick-start variants are retained as project history. They are not release declarations.

Use `README.md`, this file, `docs/INDEX.md`, `docs/DEPLOYMENT_GUIDE.md`, `docs/OPERATIONS.md`, and `SECURITY.md` as the current operational documentation.

## Security and QA hardening (Aug 2026)

GitHub security-audit issues are being closed oldest-first with code, unit
tests, and live verification against the running HTTPS stack. Closed so far
include #42, #43, #44, #46, #47, #49, #51, #52, #54, #55, #57, #58, #60, #61,
#62, #63, #168, #214, #217, #218, #221, #536, and #41 (open items: #45 DRM held as backlog).

Highlights:

- **Cross-service JWT audience verification** — auth-service tokens carry
  `aud: "wildframe-api"`; every service that verifies them now decodes with
  `audience=settings.JWT_AUDIENCE` (gateway decode is the one exception). This
  fixed a live bug where every authenticated call to streaming, content,
  admin, billing, creators, notification, search, and media-pipeline returned
  401.
- **Media-pipeline auth** — `POST /api/v1/pipeline/jobs/{upload_session_id}/start`
  and `GET /api/v1/pipeline/jobs/{job_id}` previously accepted unauthenticated
  calls; both now require a verified JWT.
- **Analytics authorization** — analytics routes enforce creator/content
  ownership via the admin role or server-side owner resolution against the
  content-service.
- **Creators-service authorization sweep (#43)** — the admin router
  (`/api/v1/admin/creators/{id}/milestones`, `.../tranches`, `.../release`,
  `.../kill`) was defined but never mounted and had no auth dependency; the
  gateway refused to route `/creators` at all (ServiceRegistry was missing
  creators, moderation, and uploads). The router is now mounted behind a
  `current_admin` dependency (401/403 live-verified: no token → 401, regular
  user → 403, admin → 200), and the gateway now routes all three services.
- **Creators-service silent data loss** — `get_db` never committed and the
  repositories only flushed, so every insert/update (onboard, milestones,
  tranches, floors, payouts) rolled back on session close; the service
  returned 200 for writes that never persisted. All mutating repositories now
  commit. Also fixed tz-aware datetimes being written into naive
  `TIMESTAMP WITHOUT TIME ZONE` columns (asyncpg `DataError` → 500 on
  `POST /onboard`).
- **Frontend/backend contract + route drift detection (#44)** — a pure-static
  CI test (`tests/contract/test_route_drift.py`, new `backend-route-contract`
  GitHub Actions job) cross-checks all 48 frontend API URL literals against
  the backend route catalog (gateway ServiceRegistry + per-service mounted
  routers), so a route change that breaks the UI fails CI before merge. The
  live integration suite adds `TestCriticalFrontendContract` (12 cases):
  tokenless 401 failure modes on every protected frontend surface (auth,
  users, streaming, recommendations, billing, admin, creators, media),
  deliberately-public endpoints stay open (catalog, search), analytics events
  stays POST-only. The sweep surfaced a live bug: **user-service JWT decode
  had no audience** — `GET /users/api/v1/profiles/{id}` 401'd with a valid
  token; fixed (plus a `JWT_AUDIENCE` setting).
- **Media processing (#218)** — all five findings verified and pinned with
  16 new tests (media-pipeline 46 → 62): (1) ffmpeg/ffprobe commands are
  fixed argv arrays via create_subprocess_exec, never a shell — mock-level
  tests prove argv stays one element per argument even with shell
  metacharacters in a filename; (2) per-job limits: CPU (`-threads` clamped
  to PIPELINE_MAX_CPU_THREADS), memory (new RLIMIT_AS preexec cap,
  PIPELINE_MAX_MEMORY_BYTES 2 GiB, verified in a real child process), disk
  (PIPELINE_DISK_QUOTA_BYTES check), duration (`-t` now actually wired from
  PIPELINE_MAX_DURATION_SECONDS — it was configured but never passed to the
  encoder), wall-clock (stage timeout kills the process group; a kill
  failure can no longer mask CommandTimeout); (3) work/quarantine dirs are
  removed on success, failure, and now cancellation (new CancelledError
  handler); (4) content.published fires only after every stage completes —
  partial pipelines emit no completion event (tested at stage level);
  (5) retries never re-run a completed stage (stage_versions) and publish
  once. Adapters now receive all caps from settings in `_build_ports`.
- **Authentication lifecycle (#221)** — all five findings verified and pinned
  with 13 new tests (auth-service 136 → 149): (1) logout invalidates refresh
  credentials — revocation is Postgres-backed (refresh rows are hard-deleted),
  so it holds across replicas (logout → refresh 401, refresh is one-time-use);
  (2) password change now revokes every refresh token for the account via
  `revoke_all_for_user` (stolen sessions die with the credential rotation);
  (3) inactive accounts can no longer authenticate — `get_by_email`/`get_by_id`
  filter `is_active`, so login/refresh/`/me` all 401 (a soft-deleted user could
  previously keep logging in; note the live `UserRepository` lives in
  `app/repositories/__init__.py` — the `user_repository.py` file is dead,
  shadowed code); (4) token-type separation — refresh tokens (7-day, same
  audience) were accepted as access tokens by 11 downstream services; every
  decode site now enforces `type == "access"` (401), and auth-service call
  sites catch the `JWTError` the type check raises instead of 500ing; pinned by
  `tests/integration/test_token_type_separation.py` (5 tests, 105 → 110) plus
  live checks: content/streaming/`/me` reject a refresh Bearer token (401)
  while access tokens still pass (200/403-only); (5) clock skew is bounded —
  `JWT_LEEWAY_SECONDS` (60) expires beyond-leeway tokens (tested).
- **Upload lifecycle (#217)** — all five findings verified and pinned with
  13 new tests (uploads-service 13 → 26): (1) sessions are terminal —
  register/complete/abort cross-checks reject after COMPLETE/ABORTED
  (live: 400/409 after abort); (2) pre-signed URL TTLs are clamped to
  PRESIGNED_URL_MAX_TTL_SECONDS (3600s), sessions expire server-side, and
  the reaper aborts + cleans stale sessions; (3) storage keys derive only
  from the unguessable session UUID (client filename never in the path;
  static AST check proves no route accepts a client-chosen storage key);
  (4) cleanup is scoped per session and retried via `storage_cleaned_at`
  when it fails; (5) completion re-reads authoritative storage metadata —
  client size/checksum assertions are ignored. Three real bugs found and
  fixed while verifying: uploads-service decoded JWTs WITHOUT the
  wildframe-api audience (every request 401 — same bug class as #44's
  user-service); the live DB was missing `upload_sessions.storage_cleaned_at`
  (reaper 500'd every 2s — ALTER applied); and the same silent-data-loss
  bug as #43 — `get_db` never commits, repos only flushed, so every
  session/outbox row rolled back (live 404 on freshly created sessions;
  now committed in all 5 mutating repo methods). #536's archive sandbox
  scan now excludes `tests/` (runtime-only invariant; was tripping on a
  `n.target.id` AST string).
- **Redis correctness (#214)** — Redis is used only for ephemeral rate
  limiting (gateway `rate_limit:` keys, auth `rl:` hashed keys) and analytics
  event dedup (`wf:analytics:dedup:`) — no DB state is cached in Redis, and
  token revocation lives in Postgres (`token_blacklist`), so eviction or
  restart cannot produce stale authorization state. All keys carry TTLs and
  are namespaced (separate logical DBs per service in compose). The gateway
  rate limiter previously 500'd every request when Redis was down or held a
  corrupt counter; it now fails open with a logged warning (matching
  auth-service's documented behavior) — live fault-injected: with Redis
  stopped, catalog/login/search all returned 200; after restart the 429
  window resumed (6th login 429). Fault-injection + namespace/TTL tests
  added for all three consumers (gateway 39, auth 136, analytics 67).
- **Tamper-resistant admin audit logs (#168)** — the audit trail
  (`admin_audit_logs`) is append-only at three layers: no HTTP write routes
  (GET-only reads; a route-surface test pins it), repository update/delete
  raise, and SQLAlchemy `before_update`/`before_delete` listeners reject
  direct session writes — live-verified in the running container (update and
  delete both raise `AuditLogAppendOnlyError`). Admins read only their own
  logs (cross-admin 404 live-verified). Also fixed a completeness gap: alert
  creation/acknowledgement were privileged actions that wrote no audit row;
  both now record `alert_created`/`alert_acknowledged` with the acting
  admin id and client IP.
- **No archive extraction anywhere (#536)** — the platform has zero
  archive-unpacking code (no tarfile/zipfile/unpack_archive in any service);
  the audit's symlink-escape surface does not exist. A CI test
  (`tests/contract/test_archive_sandbox.py`) pins that invariant and
  documents the required sandboxed design for any future archive support.
- **Live-stack integration suite** — `tests/integration/` (110 tests, ~16 min):
  gateway auth matrix + 429 flood, token lifecycle, cross-service
  authorization, billing webhook idempotency (Stripe signature verification,
  exactly one PAID invoice row on replay), contract schemas, health/readiness,
  pipeline idempotency. Skips itself when the stack is down; excluded from the
  CI unit matrix by design.
- **search-service `/ready`** — returned a raw tuple and 500'd; now a proper
  JSONResponse (live 200).
- **Billing schema drift** — webhook `invoice.paid` 500'd because the live
  `invoices` table lacked `stripe_invoice_id`, `currency`, and
  `refunded_amount`; repaired by hand (no migration framework) and the webhook
  flow is now idempotent.

Test totals (Aug 18, 2026): 841 backend unit/route tests + 110 integration
tests + 18 static route-contract/sandbox tests (CI) + 43 frontend vitest
tests. One known pre-existing failure, billing
`test_release_tranche_not_locked`, is unrelated to the hardening work.
