# Security Audit Fixes

> **Made by ChatGPT.**
>
> This file tracks the first independently reviewable remediation pass from the Wildframe security audit. It is intentionally temporary and should be removed or converted into issue references once the fixes are merged.

## Scope

This PR starts a new remediation branch from `main`. Only fixes that can be implemented and reviewed from repository evidence are included. Findings that require infrastructure/runtime confirmation remain GitHub issues.

## Initial remediation

- Harden the API gateway public-route matcher so public prefixes cannot accidentally bypass authentication.
- Add regression coverage for exact public routes, legitimate child routes, trailing slashes, and malicious prefix lookalikes.

## Later remediation (Aug 2026, tracked per GitHub issue)

Each item closed with unit tests plus live verification against the running HTTPS stack (see `STATUS.md`):

- **JWT audience verification across services** (#41, #57, #58): auth-issued tokens carry `aud: "wildframe-api"`. Every service that verifies auth tokens now decodes with `audience=settings.JWT_AUDIENCE` (`"wildframe-api"`). The api-gateway remains the single exception (it is a transparent proxy that rate-limits but does not reject proxied requests). Previously, all authenticated calls to streaming/content/admin/billing/creators/notification/search/media-pipeline failed with 401. During the #44 contract sweep the same bug was found and fixed in **user-service** (its `verify_token` decoded without an audience, so `GET /users/api/v1/profiles/{id}` 401'd even with a valid token).
- **Media-pipeline endpoint auth** (#41): `POST /api/v1/pipeline/jobs/{upload_session_id}/start` and `GET /api/v1/pipeline/jobs/{job_id}` were callable without a token; both now require a verified JWT.
- **Analytics authorization** (#63): analytics routes enforce creator/content ownership (`require_creator_access`, `require_content_access`) with fail-closed behavior on unknown content.
- **Auth fail-closed behavior** (#57): auth-service rejects invalid/expired/malformed tokens deterministically.
- **Gateway rate limiting** (#55): 429 enforcement in `proxy_request` keyed by user `sub` or IP, with regression tests.
- **Trending/fallback robustness** (#60): content-service trending endpoint no longer 500s when ranking data is missing.
- **Event outbox / atomicity** (#61): moderation/media-pipeline state changes commit before event publication; publisher survives subscriber failures (#62, aiokafka 0.14-compatible).
- **search-service `/ready`** (#41): returned a raw tuple and 500'd; fixed to a proper JSONResponse.
- **Billing webhook + schema drift** (#41): `POST /api/v1/billing/webhooks/stripe` verifies the Stripe signature, is idempotent (replay returns `idempotent:true`, exactly one PAID invoice row), and the live `invoices` table was repaired by hand (no migration framework) with `stripe_invoice_id`, `currency`, `refunded_amount`.
- **Live-stack integration suite** (#41): `tests/integration/` — 87 tests covering the gateway auth matrix + 429 flood, token lifecycle, cross-service authorization, billing webhook idempotency, contract schemas, health/readiness, and pipeline idempotency. Skips itself when the stack is down; excluded from the CI unit matrix.
- **Creators-service authorization sweep** (#43): the admin router
  (`/api/v1/admin/creators/{id}/milestones`, `.../tranches`, `.../release`,
  `.../kill`) was defined but **never mounted and had no auth dependency** —
  money-moving operations (tranche release) were unguarded by design, with a
  comment claiming "enforced by the gateway" (the gateway is a transparent
  proxy and enforces nothing). Additionally the gateway's `ServiceRegistry`
  was missing creators, moderation, and uploads, so `/creators`,
  `/moderation`, `/uploads` requests returned "Service not found" (live
  verified). Fixes:
  - `admin_router` is now mounted in `main.py` behind a new `current_admin`
    dependency (JWT signature + `aud: wildframe-api` audience + `role ==
    "admin"` claim; 401 missing/garbage, 403 non-admin). Live matrix: no
    token → 401, regular user → 403 "Admin privileges required", admin →
    200 with persisted milestone row.
  - Gateway `ServiceRegistry` now routes creators, moderation, and uploads
    (verified live: `POST /creators/api/v1/creators/onboard` returns 200
    where it previously returned "Service not found").
  - **Silent data loss**: `get_db` never committed and every repository only
    flushed, so all writes (onboard, milestones, tranches, floors, payouts)
    rolled back on session close — the service returned 200 for writes that
    never persisted (DB stayed empty; admin milestone on a fresh creator
    404'd because the onboard row was gone). All 10 mutating repository
    methods now `await self.session.commit()`.
  - **tz-aware datetimes into naive columns**: model defaults and repository
    writes used `datetime.now(UTC)` against `TIMESTAMP WITHOUT TIME ZONE`
    columns → asyncpg `DataError` → 500 on `POST /onboard`; now
    `datetime.now(UTC).replace(tzinfo=None)`.
  - Tests: +5 creators-service unit tests (`TestAdminCreatorAuth`: 401/401
    garbage/403/200 admin happy path/403 release), +6 integration tests
    (gateway routing, 401/403/401-garbage/admin-200/release-403). Suite grew
    to 93 tests; all 15 service suites (780) and the full integration suite
    pass.
- **Frontend/backend contract + route drift detection** (#44): the frontend
  call surface (48 URL literals in `apps/web/src`) is now statically checked
  against the backend route catalog derived from the gateway ServiceRegistry
  and each service's mounted routers (`tests/contract/test_route_drift.py`,
  pure stdlib, runs in CI as the `backend-route-contract` job — no docker
  needed). Any frontend path that stops resolving to a registered backend
  route fails the build. The live integration suite also gained
  `TestCriticalFrontendContract` (12 cases): tokenless failure modes (401) on
  auth/me, users profiles, streaming sessions, recommendations, billing,
  admin, creators, media; deliberately public endpoints stay open (content
  catalog 200, search 200); analytics events stays POST-only (405 on GET).
  The sweep surfaced a live bug — user-service JWT decode without audience —
  now fixed (see audience bullet).
- **Media processing** (#218, 5 findings): (1) fixed argv arrays only —
  create_subprocess_exec, no shell, metacharacter filenames stay single
  argv elements (mock-pinned); (2) per-job limits: threads clamped to
  PIPELINE_MAX_CPU_THREADS, new RLIMIT_AS memory cap
  (PIPELINE_MAX_MEMORY_BYTES, 2 GiB default, verified in a real child),
  disk quota check, duration cap `-t` now actually wired from
  PIPELINE_MAX_DURATION_SECONDS (previously configured but never passed),
  wall-clock timeout kills the process group and a kill failure can no
  longer mask CommandTimeout; (3) temp dirs removed on success, failure,
  and cancellation (new CancelledError handler); (4) no completion event
  (content.published) until every stage succeeds — partial pipelines emit
  none; (5) retries never re-run completed stages (stage_versions) and
  publish exactly once. Caps wired from settings into adapters in
  `_build_ports`. +16 media-pipeline tests.
- **Upload lifecycle** (#217, 5 findings): (1) terminal sessions — no
  chunk/complete/abort transition accepted after COMPLETE/ABORTED; (2)
  bounded pre-signed TTLs (clamped to 3600s max) + server-side session
  expiry + reaper abort/cleanup; (3) storage keys derived only from the
  unguessable session UUID — client filename never in the path, and a
  static AST test proves no route accepts a client storage key; (4)
  cleanup scoped to the owning session, retried via `storage_cleaned_at`
  on failure; (5) completion re-reads storage metadata (size, server
  checksum) and ignores client assertions. Bugs found while verifying:
  JWT decode missing `audience=wildframe-api` (all uploads requests 401);
  live DB missing `storage_cleaned_at` column (reaper 500 loop); the #43
  silent-data-loss class — `get_db` never committed, sessions/outbox rows
  rolled back (now committed in all 5 mutating repo methods). Live:
  create 200 → get 200 (persisted) → abort 200 → register 400, complete
  409; reaper log clean. +13 uploads tests.
- **Authentication lifecycle** (#221, 5 findings): (1) logout invalidates
  refresh credentials — refresh rows are hard-deleted from Postgres, so
  revocation holds across replicas (logout → refresh 401; refresh tokens are
  one-time-use, both pinned); (2) password change revokes every session —
  `change_password` now calls `revoke_all_for_user` so a rotated credential
  kills all outstanding refresh tokens; (3) inactive accounts cannot
  authenticate — `get_by_email`/`get_by_id` in the live `UserRepository`
  (`app/repositories/__init__.py`; the `user_repository.py` file is dead
  shadowed code) filter `is_active`, so login/refresh/`/me` 401 after
  deactivation; (4) token-type separation — 11 downstream services accepted
  refresh tokens (7-day, same audience) as access tokens; every decode now
  enforces `type == "access"` (401), and auth-service call sites catch the
  `JWTError` the type check raises (was a 500). Live: content POST, streaming
  sessions, `/me` reject a refresh Bearer (401) while access tokens pass
  (200/403-only); `tests/integration/test_token_type_separation.py` (5
  tests); (5) clock skew is bounded — `JWT_LEEWAY_SECONDS` 60s, beyond-leeway
  expired tokens rejected (tested). +13 auth tests (136 → 149).
- **Redis correctness** (#214, 5 findings): (1) no DB-backed cache exists
  in Redis — only ephemeral rate-limit counters and analytics event dedup,
  so no cache can outlive the DB state it mirrors; (2) every key carries a
  TTL (gateway 60s windows, auth token/cooldown windows, analytics 86_400s
  dedup TTL) — pinned by tests; (3) namespaces are disjoint per consumer
  (`rate_limit:` / `rl:` / `wf:analytics:dedup:`) with PII hashed in auth
  keys, and services use separate logical Redis DBs in compose; (4)
  corrupt/unparseable cache values fail safe: gateway's `int()` on a corrupt
  counter now fails open with a warning (was: 500 on every request),
  analytics dedup fails open, auth is fail-open by design — all
  fault-injection tested; (5) no security decision depends on Redis:
  revocation lives in Postgres `token_blacklist`, and a restart merely
  resets rate windows — live fault-injected (Redis stopped: catalog/login/
  search all 200; Redis restarted: 6th rapid login 429). +4 gateway, +5
  auth, +2 analytics tests.
- **Admin audit log tamper resistance** (#168): the audit trail
  (`admin_audit_logs`) is append-only and durably DB-backed. Verified at
  three layers with tests + live checks against the running stack: (1) no
  HTTP write routes exist for audit rows (route-surface test); (2)
  `AdminAuditLogRepository.update/delete` raise
  `AuditLogAppendOnlyError`; (3) SQLAlchemy `before_update` /
  `before_delete` event listeners reject even direct session writes —
  live-proven in the admin-service container (both raise). Read authz:
  admins see only their own logs (cross-admin GET → 404, live). Gap found
  and fixed: alert creation and acknowledgement were privileged actions
  that produced no audit record; both now write `alert_created` /
  `alert_acknowledged` entries with the acting admin id and client IP
  (live: row persisted with real IP). +8 admin-service unit tests (69
  total).
- **Archive extraction symlink handling** (#536): verified the platform has
  no archive-unpacking code path in any service, worker, or infra script
  (media-pipeline's "extract" stages are ffmpeg metadata/audio/subtitle
  extraction, not archives). `tests/contract/test_archive_sandbox.py` pins
  the absence of tarfile/zipfile/unpack_archive APIs across services and
  apps and documents the required sandboxed design (reject symlinks/
  hardlinks/absolute members; extract strictly under the worker sandbox
  root) for any future archive support.

Open/backlog: #45 (DRM scope — backlog by decision).

## Review requirements

- Do not weaken CI checks to obtain a green build.
- Verify the affected tests in GitHub Actions.
- Review each security change independently before merging.
- Do not treat unresolved audit issues as fixed merely because this PR exists.
