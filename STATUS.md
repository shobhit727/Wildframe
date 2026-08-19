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
#62, #63, #168, #214, #217, #218, #221, #222, #223, #225, #227, #228, #536, and #41 (open items: #45 DRM held as backlog).

Newest additions:

- **Credential-rotation session invalidation (#77, #79, #80, #81, #97, #98,
  #99, #100, #101)** — users gain an `auth_version` column; access tokens
  carry an `av` claim and are rejected at the auth-service boundary the
  moment a password changes (stale token 401 live-verified, fresh token
  works). Email-verification tokens are consumed exactly once (atomic
  blacklist insert, replay → 400, concurrent use single-success). MFA
  challenge exchange is rate-limited per IP and per user. Admin tokens carry
  an `arv` role-version claim that content/search/moderation/admin services
  compare against their own `ADMIN_ROLE_VERSION` — revoking an admin (remove
  from `ADMIN_EMAILS` + bump the version) is immediate, not at token expiry.

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
- **Admin security (#225)** — the moderation-service was a fully
  unauthenticated admin-equivalent surface (flag, queue, decisions,
  strikes) with caller-supplied actor identity. Now: every moderation
  endpoint requires an auth-service access token with the `wildframe-api`
  audience; flagging is open to any authenticated user but the reporter is
  the token subject, and queue/decisions/strikes require the admin role
  with the moderator taken from the token — body-supplied `reporter_id` /
  `moderator_id` are ignored. Admin-service: refresh tokens now 401 (were
  500 via an unimported `status`); alert acknowledgment audit records the
  real client IP (was hardcoded `0.0.0.0`); every mutation+audit pair
  persists in a single transaction (was two commits — a crash between them
  left an un-audited mutation). No bulk admin operations exist: pinned with
  guard tests (single-item schemas, no bulk routes). Live-verified: queue
  401 no token / 403 user / 200 admin, decisions 403 user, flags 401 no
  token / 201 user, strikes 200 admin, admin alerts + refresh token 401.
- **Search integrity (#227)** — all five findings verified and pinned (55
  tests): (1) deletes/unpublishes propagate to the index via Kafka —
  content-service now publishes `content.deleted` / `content.unpublished`
  after commit (SDK `KafkaEventPublisher`, `EVENT_PUBLISHER=kafka` in the
  dev stack; in-memory publisher in tests), and search-service subscribes
  and removes the document by `_id` (idempotent; malformed payloads
  dropped; SDK dedup + DLQ). Live-verified end-to-end: publish → reindex →
  search finds → DELETE → gone with **no** reindex; same for
  archive/unpublish; startup warm-up backfills the catalog automatically.
  Live verification also surfaced and fixed three pre-existing robustness
  bugs: `ensure_index` 500s on an orphaned `content_v<N>` index (interrupted
  reindex) and on the ES-8 positional `update_aliases` call — both now
  handled (orphans adopted, `body=` kwarg); search-query logging wrote
  tz-aware datetimes into naive `TIMESTAMP` columns (asyncpg DataError →
  500 on every `/query`); warm-up used `async for` on an `async_sessionmaker`
  and silently never backfilled. (2) the app applies the ES-8
  `indices.id_field_data` cluster setting at index creation because the
  cursor tie-break sorts by `_id` (was failing with empty results on a
  fresh cluster); (3) bounds are capped at the route layer (limit ≤ 100,
  trending ≤ 50, query ≤ 200 chars) with cursor-only pagination and
  integrity-protected cursors — pinned with guard tests; (4) no
  user-specific cache keys in search-service — searches are content-keyed
  and public by design, pinned; (5) delete-by-`_id` + SDK event dedup makes
  redelivery harmless — pinned. 13 new tests (content-service 88 → 92,
  search-service 46 → 56).
- **Recommendation isolation (#228)** — all five findings verified and
  pinned (17 new tests, recommendation-service 26 → 43): (1) the stored
  recommendation rows are a per-user cache of generation output, so every
  input that affects personalization must invalidate them — `get_recommendations`
  now regenerates whenever the preferences row (`prefs.updated_at`) is
  newer than the newest stored row, so liked/disliked genres, preferred
  languages, and watch frequency can never be silently ignored by a stale
  cache; (2) disliked-genre content is excluded at generation in both the
  genre-scored and the trending-fallback paths, and the freshness rule
  above guarantees served rows always reflect current dislikes (no blocked-
  or private-content concepts exist anywhere in the platform — verified —
  so those sub-findings are vacuous); (3) deleted/unpublished content can
  no longer survive in stored rows — recommendation-service now consumes
  `content.deleted` / `content.unpublished` (SDK Kafka subscriber,
  `EVENT_PUBLISHER=kafka` in the dev stack) and evicts the rows for that
  title across all users; live-verified end-to-end: content in recs →
  DELETE/unpublish → consumed (`removed=1`) → gone with no regeneration;
  (4) generation is bounded — route layer rejects `limit > 100` (422) and
  preference lists over 50 genres (422), the service clamps internally
  (limit, genre lists) and caps the scored candidate set at 500;
  (5) per-user cache-key isolation — rows are keyed by `user_id` with
  user-scoped queries and `require_self` IDOR protection; pinned with
  two-user isolation tests (no cross-user row mixing, eviction is
  cross-user by content id, not by cache key). Guard tests cover every
  finding.
- **OAuth security (#223)** — all five findings are vacuous: the platform
  ships no OAuth at all (no routes, settings, schemas, DB tables, or
  frontend flows; verified across all services + gateway + frontend). Four
  guard tests now pin the absence and require the audit's five requirements
  (unpredictable/session-bound/single-use state, exact redirect allowlist,
  single-use authorization codes, safe account linking, issuer + client_id
  claim validation) before any OAuth code can land.
- **MFA lifecycle (#222)** — all five findings verified and pinned with 13
  new tests (auth-service 149 → 157; five dead service-layer MFA tests that
  pinned unused code were removed): (1) MFA challenges are single-use and
  atomically consumed — the challenge hash is inserted into the
  `token_blacklist` table (unique PK) before tokens are issued, so replay of
  a consumed challenge 401s even with a still-valid TOTP code, and
  concurrent consumption is race-safe (live: login-verify 200 → replay 401);
  (2) TOTP secrets survive key rotation — `SecretCipher` now uses a keyring
  (`MFA_ENCRYPTION_KEY` current + `MFA_ENCRYPTION_KEY_PREVIOUS` retired
  keys, defaulting to the JWT-secret-derived key for backward compat), so a
  key rotation never strands enrollments and any replica sharing settings
  can decrypt; (3) no recovery codes are issued or stored — the live setup
  flow returns only `secret` + `totp_uri` and never writes `backup_codes`
  (the plaintext-code generator lived in dead service-layer code, now
  deleted); (4) enrollment cannot be replaced — setup refuses with 409 when
  a pending (not-yet-verified) secret exists and all MFA state transitions
  take a `SELECT ... FOR UPDATE` row lock (live: setup → setup = 409);
  (5) disabling MFA requires a valid TOTP code, not just a session (live:
  wrong code 400, valid code 200 and login stops challenging).
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

Test totals (Aug 18, 2026): 895 backend unit/route tests + 110 integration
tests + 18 static route-contract/sandbox tests (CI) + 43 frontend vitest
tests. One known pre-existing failure, billing
`test_release_tranche_not_locked`, is unrelated to the hardening work.
