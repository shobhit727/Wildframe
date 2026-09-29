# Agent Instructions for Wild Frame (Wildframe)

This is the repository-level operating guide for coding agents. It describes the current implementation on main and should be updated when the implementation changes.

**New to this repository? Read `ONBOARDING.md` first.** It is the path — what to do
in what order, which commands actually work, and the traps that produce wrong
conclusions. This file is the rules; `ONBOARDING.md` is the route through them.

Two facts worth knowing before you touch anything, both established by running the
stack rather than by reading code:

- **A green CI run is not evidence the application works.** The Playwright suite
  mocks the API, so mock-versus-real drift is structurally invisible to it, and the
  Docker jobs build images without running one. For a period CI was fully green while
  the website rendered a blank page, account creation was broken, and five services
  could not start.
- **A passing build is not evidence the image is current.** A `docker compose build`
  has been observed printing `CACHED`, exiting 0, and shipping pre-fix code. Verify by
  grepping the built artifact, and use `--no-cache` plus `--force-recreate` when it
  matters.

The bugs worth finding here are mostly plumbing bugs — a value that should have
arrived somewhere and did not. When something passes a test and fails in the app,
believe the app.

## 1. Repository model

Wildframe is a monorepo for an OTT streaming platform. It contains 15 FastAPI backend services, a Next.js/TypeScript frontend, shared Python SDK packages, local Docker/Caddy orchestration, Kubernetes/Helm manifests, Terraform, database bootstrap SQL, monitoring configuration, tests, and engineering documentation.

Top-level areas:
- services/ — 15 independently runnable backend services.
- apps/web/ — Next.js frontend using the App Router.
- packages/sdk/ — shared events, authentication, observability, and compliance libraries.
- deployments/ — local Docker Compose stack and host-facing Caddy setup.
- infrastructure/ — database initialization, Caddy, Helm/Kubernetes, Prometheus/Grafana/Loki, and Terraform.
- tests/ — cross-service integration and API contract tests.
- scripts/ — local bootstrap, certificate, schema, seed, and verification helpers.
- docs/ — current architecture, development, API, deployment, testing, and operations documentation.
- PROJECT_MEMORY/ — backlog, bugs, risks, and technical debt.

The repository contains many historical completion/audit/quick-start documents. They are useful for history but are not authoritative evidence of current behavior.

## 2. Source-of-truth order

When sources disagree, use this order:
1. Code and configuration on the branch being changed.
2. Tests that execute against that code.
3. The running stack. A test suite can be green while the application is broken,
   because the suite may mock the very layer where the defect lives. When a test
   passes and the app does not work, the app wins — go and reproduce it.
4. .github/workflows/ci-cd.yml for CI behavior.
5. Current README.md, ONBOARDING.md, oner-task.md, and current docs/ architecture,
   development, testing, deployment, and operations guides.
6. Historical audit, completion, status, and implementation reports.

Before changing behavior, inspect the complete relevant path: route, schema/model, service logic, repository, settings, event handling, and tests. For cross-service work, inspect both sides of the HTTP or Kafka contract.

`ONBOARDING.md` records the traps that have produced wrong conclusions in this repo
specifically. `oner-task.md` records what a human still owes, including the
deliberate limitations and anything an agent chose not to fix.

## 3. Backend services

| Service | Responsibility | Main areas |
|---|---|---|
| api-gateway | routing, proxying, rate limiting, privacy/age/ads/billing gateway concerns | app/api, app/core, app/middleware.py |
| auth-service | authentication, JWT/JWKS, refresh lifecycle, privacy and age flows | app/api/routes, app/security, app/core |
| user-service | users, child accounts, privacy/DSAR and security state | app/api/routes, app/security, app/models |
| content-service | content metadata, ads, reviews, rights and jurisdiction policy | app/api/routes, app/models, app/schemas |
| streaming-service | playback, streaming, DRM, maturity and accessibility | app/api/routes, app/models, app/services |
| search-service | Elasticsearch search and event-driven indexing | app/api, app/services, app/core/events.py |
| recommendation-service | recommendations, catalog access, caching and events | app/api, app/services, app/core |
| billing-service | subscriptions, commerce, Stripe, tiers, payouts and refunds | app/api, app/core, app/models, app/services |
| analytics-service | tracking, analytics and DSAR | app/api, app/core, app/services |
| notification-service | notification channels, templates and persistence | app/api, app/channels.py, app/templates.py |
| admin-service | admin actions, documents, processors, compliance and transfers | app/api/routes, app/services |
| media-pipeline | transcoding/media workflow and state machine | app/core/ffmpeg.py, app/core/stages.py, app/services.py |
| creators-service | creator profiles, onboarding and payouts | app/api, app/schemas, app/services |
| moderation-service | moderation queues and DMCA workflows | app/api, app/models, app/services |
| uploads-service | upload lifecycle, storage and processing events | app/api, app/core/storage.py, app/services |

Service layout is intentionally not perfectly uniform. Some services use package directories for models/repositories/services; others use flat modules. Do not normalize layouts without checking imports and tests.

## 4. Service boundaries

- Treat every backend service as an independent application.
- Do not share PostgreSQL databases between services.
- Use explicit HTTP/API or Kafka contracts for cross-service communication; do not import another service's application code.
- Service URLs come from settings/environment. Do not hardcode Docker hostnames in business logic.
- The gateway is a proxy/boundary component. Gateway authentication or filtering does not replace authorization in the destination service.
- Authorization must occur before state-changing side effects.

## 5. FastAPI conventions

Most services expose create_app() and a module-level app from app/main.py, with Uvicorn using app.main:app.

Preferred flow:
HTTP route -> validation/schema -> business service -> repository/model.

Use async FastAPI handlers, async SQLAlchemy sessions, Pydantic schemas at API boundaries, pydantic-settings for environment configuration, structured logging, and the service's existing lifespan/resource management.

Health endpoints are part of the deployment contract. Database health checks must use a real SQL statement such as text("SELECT 1"), not a Python callable.

### 5.1 Adding an endpoint, file by file

Layout is consistent across services — do not normalize it, some services use flat
modules and some use packages. Check the target service first.

```
services/<service>/app/
  main.py            create_app() + module-level `app`
  api/routes/        the HTTP layer
  schemas/           Pydantic models at the boundary
  services/          business logic
  repositories/      data access
  models/            SQLAlchemy models
  core/settings.py   pydantic-settings
```

1. **Schema** in `app/schemas/` — request *and* response models, separately. Never
   return a password hash, access token, refresh token, or internal auth state in a
   response model.
2. **Repository** in `app/repositories/` — all queries live here. Keep routes free of
   SQL.
3. **Service** in `app/services/` — business rules, and the only place that composes
   repositories.
4. **Route** in `app/api/routes/` — parse, authorize, call the service, map errors to
   status codes. No business logic, no queries.
5. **Register** the route in that service's router aggregation (`api/routes/__init__.py`),
   and confirm with `curl -sk https://localhost:<port>/openapi.json` that your path
   actually appears. A route you forget to register returns 404 while the code looks
   correct.
6. **Test** it against the running service, not just the suite. See 19.2.

**Authorization happens before state-changing side effects**, in the route or the
service — never after the write.

### 5.2 Calling another service

Use the service's own settings for the URL, never a hardcoded Docker hostname. Follow
the pattern already in `app/core/settings.py` — several services already carry
`AUTH_SERVICE_URL: http://auth-service:8000` and `JWT_JWKS_URL`, and that is the
convention.

**Be explicit about which side of the trust boundary the code runs on.** Browser code
and in-container server code need different base URLs, and merging them into one
variable is a real bug we shipped: server-side code that used a *public* host resolved
to the wrong container from inside the network and returned 502 on every request.

## 6. Database

Backend persistence uses SQLAlchemy 2.x and PostgreSQL/asyncpg.

- Keep database ownership inside the service.
- Verify actual schema expectations before changing models or queries; historical documents contain schema drift.
- Be careful with timestamp columns that are stored without timezone information. Use the representation expected by the existing model/database column.
- The repository contains Alembic-related configuration and Helm migration-job infrastructure, while services also contain direct schema/bootstrap behavior. Inspect the target service and deployment path before introducing a migration convention.
- Never make a production schema change only in a model if the deployment path requires a corresponding database operation.

## 7. Authentication and authorization

JWT behavior is security-sensitive.

- Auth-issued tokens use the configured audience, currently wildframe-api in the main code path.
- Services verifying auth-issued tokens should decode with the configured audience.
- The shared auth verifier currently restricts signing algorithms to RS256 and expects exp, iat, iss, aud, sub, and type claims.
- Access and refresh tokens are distinct. Never accept a refresh token where an access token is required.
- Prefer JWKS verification for downstream services rather than distributing private signing keys.
- Enforce ownership, role, and step-up requirements before mutation.
- Do not turn an unimplemented security flow into a false success response.

Relevant shared code is packages/sdk/wildframe_auth. Gateway token handling is a separate boundary and must not be treated as downstream authorization.

## 8. API paths

Backend routes use the /api/v1 convention. When debugging a path, trace the complete chain:

frontend/client path -> gateway route -> service prefix -> FastAPI route.

Update the API client, gateway, backend route, and contract tests together when an external route changes.

## 9. Kafka and events

Shared event code lives in packages/sdk/wildframe_events. Use its DomainEvent, topic definitions, publisher, and subscriber abstractions instead of creating a second event envelope.

The SDK implements in-memory and Kafka publishers, payload validation, size limits, bounded retries, idempotent Kafka production where configured, at-least-once consumption, handler retries, dead-letter quarantine, optional deduplication, and Prometheus counters.

Event handlers must be idempotent. A message may be delivered more than once.

aiokafka compatibility matters. Before changing topic/admin behavior, inspect the installed dependency range, the implementation, and its tests. In particular inspect packages/sdk/wildframe_events/dlq_retention.py before changing DLQ retention.

## 10. Redis

Use the official redis package and redis.asyncio. Do not add the obsolete standalone aioredis package.

Keep Redis operations asynchronous. Preserve existing failure semantics for rate limiting, caching, and deduplication; do not introduce unbounded retry loops.

## 11. Billing

Billing code requires stronger review than ordinary CRUD code.

- Use existing money helpers and explicit currency minor-unit rules. Never assume every currency has two decimal places.
- Validate client-controlled prices against server-side catalog/tier data.
- Make Stripe/webhook operations idempotent.
- Preserve payout ledger and reconciliation invariants.
- Authenticate and authorize before changing billing state.
- Use the existing JWT verification path instead of duplicating token parsing.

Read the relevant billing security/regression tests before modifying payment, refund, payout, or subscription behavior.

## 12. Media and uploads

media-pipeline is a stateful workflow. Inspect app/core/stages.py, app/core/ffmpeg.py, app/services.py, models, repositories, and state-machine tests before changing it.

FFmpeg inputs, outputs, filenames, and arguments are security-sensitive. Do not convert user-controlled strings into unrestricted shell commands.

uploads-service has an explicit lifecycle and storage abstraction. Preserve authorization, state transitions, storage cleanup, event publication, file validation, and path-traversal protections.

## 13. Privacy, compliance, moderation

Content, user, auth, admin, creators, moderation, and analytics code contains privacy/compliance behavior. Shared compliance code is in packages/sdk/wildframe_compliance.

Preserve explicit authorization, consent, jurisdiction, audit, and error/status semantics. Do not weaken a policy check simply to satisfy a happy-path test.

## 14. Observability

Shared observability code is in packages/sdk/wildframe_observability. The platform uses OpenTelemetry, Prometheus, Grafana, Loki, and Jaeger.

Preserve X-Request-ID and X-Correlation-ID propagation. Do not log passwords, tokens, private keys, full authorization headers, sensitive payment information, or unnecessary personal data.

Do not place unbounded values such as user IDs, request IDs, tokens, or arbitrary URLs into metric labels.

## 15. Frontend

apps/web is a Next.js + TypeScript application.

Important areas:
- src/app/ — App Router pages and routes.
- src/api/ — API client and admin API helpers.
- src/components/ — reusable UI, authentication, browse, player, and admin components.
- src/hooks/ and src/stores/ — client hooks and state.
- src/utils/ — auth-cookie, CSP, and query-client helpers.
- src/proxy.ts — frontend request/proxy behavior.
- src/__tests__/ — Vitest tests.
- e2e/ — Playwright tests.

Keep browser/server boundaries explicit. Note that `apps/web/AGENTS.md` directs agents to
`node_modules/next/dist/docs/`; that directory is not present in this install, so verify
Next.js behaviour against the installed source under `node_modules/next/dist/` instead. Never move server-only secrets into client components. The frontend API base URL is environment-driven and normally targets the HTTPS gateway in local development.

## 16. Local development and TLS

The local stack is defined by deployments/docker-compose.dev.yml. Caddy provides the host-facing TLS/routing layer.

Relevant files include infrastructure/caddy/Caddyfile and scripts/generate-dev-certs.sh. Generated development certificates under apps/web/certificates are not committed.

Inside the Docker network, service-to-service traffic normally uses container HTTP ports. Host-facing traffic is TLS-aware; do not change this boundary casually.

## 17. Infrastructure

The repository uses Docker Compose, Helm/Kubernetes, Terraform, PostgreSQL bootstrap SQL, Caddy, Prometheus, Grafana, Loki, and Jaeger.

When changing Helm, run lint and render the relevant values for default, staging, and production. Review security contexts, probes, NetworkPolicies, service accounts, resources, and external-service validation.

When changing Terraform, preserve least privilege and never embed credentials.

## 18. CI and quality gates

The authoritative workflow is .github/workflows/ci-cd.yml. It covers backend lint/type checks/tests, SDK tests, Helm validation, frontend validation, Docker build smoke tests, and security scanning.

Root pyproject.toml defines Black, isort, Ruff, and mypy policy. Do not create a conflicting global style policy in one service.

CI should fail loudly. Do not add continue-on-error, || true, blanket test skips, or broad exception swallowing just to make validation green.

## 19. Testing

Backend suites should normally be run from each service directory because the services use top-level app packages. A repository-root pytest sweep can cause app.* import shadowing.

Typical commands:
- cd services/auth-service && pytest tests --asyncio-mode=auto
- for svc in services/*/; do (cd "$svc" && pytest tests --asyncio-mode=auto) || exit 1; done
- cd apps/web && npm run test
- cd apps/web && npx playwright test
- poetry run pytest tests/integration -q
- pytest tests/contract -q

For authentication, billing, gateway, event, upload, media-pipeline, and authorization changes, run the relevant security/regression tests rather than only a happy-path test.

### 19.1 Writing a test that can actually fail

Regression tests must fail against the old behavior and pass against the new. The
hard part is proving they can fail at all, and we have shipped tests that could not.

**Verify red/green before you commit.** Revert your fix, run the new test, confirm it
fails, restore, confirm it passes. If you never saw it red, you do not know what it
is testing.

**A test that passes for the wrong reason is worse than no test.** Real examples from
this repo:

- A test asserting only "the request succeeded" passed against a *completely broken*
  build. The code under test swallowed its own exceptions, so the app came back
  healthy and fully uninstrumented, and every assertion passed against nothing.
  Fix: assert the side effect that proves the mechanism ran — a recorded span, a
  written row, an actual call — not just the absence of an error.
- A test whose only assertion was `not.toContain('some-url')` passed against an empty
  list. Fix: assert length, then contents.
- A downgrade that broke an unrelated package made the test red for the wrong reason.
  **A red test is not proof until you have read why it is red.** Check the failure
  message names the actual defect.
- A test that shrank its input to fit the old limit passed, having defeated its own
  purpose. Use realistically sized data — the bug is often that the real value is
  bigger than anyone assumed.

**Do not mock the layer where the defect lives.** Several of the worst bugs here were
invisible precisely because the tests mocked that layer. A dependency-injection test
proves your code calls what you told it to call; it cannot prove the real thing works.

**Prefer externally observable assertions.** Status code, persisted row, emitted
event, written cookie. Mock-heavy tests pass when the product is broken.

### 19.2 Verifying a change against the running app

Ready-made harnesses live in `scripts/` — see `scripts/README.md`:

- `node scripts/browser-check.mjs` — does each page actually render in a browser?
  Reports inputs, CSP violations, visible text, and the post-redirect URL.
- `node scripts/causation-check.mjs` — strips one response header and reports whether
  the symptom disappears. Turns a hypothesis into a demonstrated cause, or rules a
  layer out. This is how the blank page was diagnosed.
- `node scripts/auth-flow-check.mjs` — can a real user register and stay signed in?
  Registration returning 201 is not sufficient; three defects once hid behind it.

The suite is necessary and not sufficient. Before calling work done:

```bash
docker compose -f deployments/docker-compose.dev.yml build --no-cache <service>
docker compose -f deployments/docker-compose.dev.yml up -d --no-deps --force-recreate <service>
curl -sk https://localhost:<port>/<real endpoint>     # a real route, not /health
docker compose -f deployments/docker-compose.dev.yml logs <service> --tail 200
```

Hit a **real** endpoint. Three services returned 200 on `/health` while every real
route 500'd, because the health path skipped the middleware where the bug lived.

When a fix is not a plain code change — a dependency pin, a build flag, a deployment
setting — also prove it took effect in the artifact, not just on disk. A build has been
observed printing `CACHED`, exiting 0, and shipping pre-fix code. Grep the built
artifact for the value you expect.

For anything the browser has to run, drive a real browser. A page can return 200 with
an empty body and look fine to `curl`.

### 19.3 Proving causation, not correlation

When you infer a cause from a symptom, try to break it on purpose. Strip the one
header, comment out the one line, restore the old value — and confirm the symptom
appears and disappears with it. If you cannot make the bug come back on demand, you
have a theory, not a diagnosis, and you should say so.

## 20. Dependencies

The repository has a root Poetry project plus service-level dependency metadata and lockfiles; some services also have independent Poetry/uv configuration.

When changing dependencies, inspect the service metadata, root metadata if shared, lockfiles, Docker installation behavior, and the relevant tests. Do not add a dependency to compensate for a local package/import mistake.

**A floor you raise for one conflict can create a different one.** Two dependency
resolutions in this repo did exactly that, and both looked correct in isolation:

- Three manifests pinned `opentelemetry-instrumentation-fastapi` to disjoint ranges,
  making every per-service lock unresolvable. Unifying them on `^0.49b0` fixed the
  conflict — and `0.49b0` is a two-year-old release that crashes against the FastAPI
  version we pin, 500ing every route registered via `include_router`. See #978.
- Fixing that forced the API/SDK floor to `1.43.0`, which in turn broke
  `opentelemetry-exporter-jaeger`, discontinued upstream at `1.21.0` with no later
  release. There was no version of that exporter that worked, so the exporter had to
  be replaced with OTLP.

**When you bump one, check the whole family and the thing it depends on.** These
packages pin each other exactly (`sdk 1.43.0` requires `semantic-conventions==0.64b0`),
and a partial upgrade produces a set that installs and then fails at runtime, or does
not install at all. `pip check` must be clean before you call it done — and note that
`pip check` reporting a conflict in a *transitive* package is how you find out.

**Install before you type-check or test.** CI runs `poetry install --no-interaction
--with dev` per service. Running mypy or pytest against an empty or stale venv produces
confident, entirely fictional errors — an `import-untyped` storm across 13 services
from a package that was never installed, in one recorded case.

**Do not report a vulnerability you have not confirmed.** A scratch venv produced a
"redis 5.3.1 has 2 HIGH CVEs" finding that actually came from `msgpack` and
`setuptools`. Confirm the package name in the finding before escalating it. For real
signal use `.github/scripts/verify-supply-chain.py`; a local Trivy run over the working tree
flags generated dev certificates, which are gitignored and never committed.

## 21. Documentation

Do not add another top-level completion report, final report, or duplicate quick-start document.

Prefer README.md, STATUS.md, DOCS_INDEX.md, docs/ARCHITECTURE.md, docs/DEVELOPMENT.md, docs/QUICKSTART.md, docs/TEST_GUIDE.md, docs/DEPLOYMENT_GUIDE.md, docs/OPERATIONS.md, SECURITY.md, and PROJECT_MEMORY/ for current engineering context.

Historical audit material should be dated and clearly described as historical.

## 22. GitHub issues

The issue tracker contains current defects, historical audit findings, duplicates, and findings that may already be fixed in source.

Before declaring an issue fixed:
1. Verify the behavior against the current code.
2. Identify the exact implementation and regression test establishing the fix.
3. Check duplicate issue families.
4. Record the evidence in the issue or PR.

Do not assume that an open issue is necessarily an unfixed defect, and do not assume that a closed issue proves the current code is still correct.

## 23. Agent workflow

1. Read this file and any nearer service-specific AGENTS.md.
2. Read `Message-board.md` when another agent may be active in the same tree. It
   carries standing notices, per-file work claims, and verification recipes.
3. Locate the entry point and trace the complete execution path.
4. Inspect models, schemas, settings, repositories, events, and tests.
5. Inspect cross-service contracts for cross-service changes.
6. Make the smallest coherent change.
7. Add or update a regression test.
8. Run focused tests and applicable lint/type checks.
9. Run contract/integration tests when an external contract changed.
10. Review the final diff for accidental security, API, dependency, or documentation changes.
11. Update current documentation if behavior or setup changed.
12. Re-read `Message-board.md` before pushing, and post a `Files:` claim for
    any path you edited or are about to edit.

Do not mix unrelated cleanup into the same PR.

### 23.1 Concurrent agents in a shared working tree

When more than one agent works this branch at the same time, in the same
checkout:

- `Message-board.md` is the coordination point. It lives on
  `audit/fix-open-github-issues`, **not on `main`**, so read it with an explicit
  ref:
  ```bash
  gh api "repos/shobhit727/Wildframe/contents/Message-board.md?ref=audit/fix-open-github-issues" --jq '.content' | base64 -d
  ```
  Prefer this over `raw.githubusercontent.com`, which is CDN-cached and has been
  observed serving a stale copy minutes after a successful push.
- **Claim paths before editing them.** Post a `Files:` entry and check for an
  existing claim. Overlap is the main way work is lost here.
- **`git add` does not clear an already-staged change.** Another agent's staged
  deletions or edits will be swept into your commit. Always
  `git reset && git add <exactly your paths>`, then confirm with
  `git show --stat HEAD --format=""`.
- **A quiet push is not success.** A push that printed only a fast-forward hint
  had already lost the commit to a concurrent reset. Confirm with
  `git log origin/<branch> --oneline | head` and, better, verify your *content*
  arrived: `git show origin/<branch>:Message-board.md | grep -c '<distinctive string>'`.
  Verify by content, not by SHA — re-application by another agent moves the SHA
  while preserving the change.
- **The board is one shared file, so rebase silently eats appends.** A conflict
  resolved by taking `origin`'s version discards your appended entry, and
  `rebase --continue` then commits that reduced file. Your commit lands with the
  text missing. Re-append *inside* the retry loop, after each rebase, not once
  before it.
- **`git pull --rebase` refuses outright on a dirty tree.** That refusal is
  protective. For a merge, confirm the incoming commits do not touch the dirty
  files, fingerprint those files before and after, and compare — then it is safe
  while their work stays byte-identical.
- **A dirty tree you did not create will block your rebase, and therefore your
  push.** This is the same event as the bullet above seen from the other side, and
  it is the one that actually stops you: you cannot rebase, so you cannot push, so
  you re-run the same failing push until you give up. It is not a network failure
  and retrying will never clear it. Do not "fix" it by committing their file into
  your commit, and do not `git checkout --` it away.
  Fingerprint, stash, rebase, restore, verify:
  ```bash
  BEFORE=$(md5sum <their-file> | cut -d' ' -f1)
  git stash push -q -- <their-file>
  git pull --rebase -q origin <branch>
  git push -q origin <branch>
  git stash pop -q
  AFTER=$(md5sum <their-file> | cut -d' ' -f1)
  [ "$BEFORE" = "$AFTER" ] || echo "their work changed — stop and reconcile"
  ```
  Untracked files (`??`) do not block a rebase; only tracked modifications do. Check
  `git status --porcelain` first to see which you are dealing with.
- **Stash is not a safe parking space.** A stash left behind by a crashed agent is
  invisible to the next one. Pop what you stash, in the same turn, and verify the
  fingerprint.
- Another agent may leave the checkout **detached mid-rebase** with the board
  conflicted. Re-attach before concluding anything about your own work:
  `git rebase --abort; git merge --abort; git checkout -B <branch> origin/<branch>`.
- **`git worktree add --detach <dir> <sha>` is the only reliable way to learn what
  CI actually sees.** The shared dirty tree tells you about *someone's* WIP.
  Fetch first: a SHA read from the GitHub API is not a local ref, and
  `git worktree add` fails on it with `invalid reference`.

### 23.2 Verified failure modes

Each item below cost real work on `audit/fix-open-github-issues`. They are
recorded here because the symptom is misleading in every case, so the instinct is
to trust the wrong signal.

**Landing work under churn**

- A quiet `git push` is not success. A push that printed only a fast-forward hint
  had already lost the commit to a concurrent reset. Confirm with
  `git log origin/<branch> --oneline | head` and, better, verify your *content*
  arrived: `git show origin/<branch>:Message-board.md | grep -c '<distinctive string>'`.
  Verify by content, not by SHA — re-application by another agent moves the SHA
  while preserving the change.
- **The board is one shared file, so rebase silently eats appends.** A conflict
  resolved by taking `origin`'s version discards your appended entry, and
  `rebase --continue` then commits that reduced file. Your commit lands with the
  text missing. Re-append *inside* the retry loop, after each rebase, not once
  before it.
- `git pull --rebase` refuses outright on a dirty tree. That refusal is
  protective. For a merge, confirm the incoming commits do not touch the dirty
  files, fingerprint those files before and after, and compare — then it is safe
  while their work stays byte-identical.
- Another agent may leave the checkout **detached mid-rebase** with the board
  conflicted. Re-attach before concluding anything about your own work:
  `git rebase --abort; git merge --abort; git checkout -B <branch> origin/<branch>`.
- `git worktree add --detach <dir> <sha>` is the only reliable way to learn what
  CI actually sees. The shared dirty tree tells you about *someone's* WIP.
  Fetch first: a SHA read from the GitHub API is not a local ref, and
  `git worktree add` fails on it with `invalid reference`.

**`cancel-in-progress` and the push/review deadlock**

`ci-cd.yml` uses a per-ref concurrency group with `cancel-in-progress: true`, so
every push kills the run in flight. Under multi-agent push rates this yields
*zero* verdicts — not failures, no signal at all. Worse, posting a board update
is itself a commit and therefore itself a push, so the coordination mechanism
causes the CI it is coordinating. The cure is a quiet window or deliberate
batching (one push per ~10 minutes), not weakening the guard. Do not silence
`cancel-in-progress` to make the dashboard look greener: that hides real
regressions behind stale runs.

**Conclusions drawn from a bad check**
- **Do not report a lead as a finding.** A strong hypothesis was presented to the
  user as the likely cause, and it turned out to be disproven by the very agent
  dispatched to test it. Say "this is the strongest lead" until it is proven, and
  correct it publicly when it is not.
- **A check that cannot fail is worse than no check.** Reading `self.__next_f: 0`
  as "the script never ran" produced a confident "the site is still blank" when the
  page was rendering perfectly. React consumes those pushes; the check was wrong, the
  conclusion built on it was wrong, and it nearly caused a working fix to be
  reverted. Verify the check itself before trusting its output.
- **Grepping a rendered page for markup is not a test.** A curl-and-grep for
  `<input` said "0 inputs" on a page that a real browser showed with five. Use a
  browser for anything the browser renders.
- **Mocking an environment that can raise can mask the real error.** A test that
  patched a module whose import had broken passed, because the patch replaced the
  failure with a stub. The app was fully broken underneath.

**Local verification that lied**

- **Install before you type-check or test.** CI runs
  `poetry install --no-interaction --with dev` in each service directory before
  mypy and pytest. Running either against a fresh or empty service venv produces
  confident, entirely fictional errors — an `import-untyped` storm across 13
  services from a package that was never installed, in this case.
- Use a clean worktree plus a real `poetry install` to reproduce CI. A venv
  inherited from the repo root is drifted and will disagree with the pipeline in
  both directions.
- A scratch venv you built by hand can invent vulnerabilities. A "redis 5.3.1
  has 2 HIGH CVEs" claim came from `msgpack` and `setuptools` in that scratch
  venv, not from redis. Confirm the package name in the finding before reporting
  it.
- Trivy over the working tree flags locally generated dev certificates. Those are
  gitignored (`.gitignore`: `apps/web/certificates/*.pem`) and never committed, so
  a local non-zero exit is not a CI result. `verify-supply-chain.py` is the check
  that reflects committed content.
- Do not hide a tool's error output while debugging. `2>/dev/null` on
  `git worktree add` hid a failure, and the next command then analysed an empty
  directory and produced meaningless output.
- `grep | head -N` truncated away the match that mattered and produced the
  opposite conclusion. Read the full match list before concluding a file is
  clean.
- Bash `seq` is `seq FIRST INCREMENT LAST`. `seq 10 60 10` is "start 10, step 60,
  stop 10" and yields a single value. A watcher built on it ran one poll and
  reported nine minutes of quiet. Prove a polling loop iterates: emit a
  per-iteration heartbeat and check elapsed wall time.
- Simulate a regression with the *actual* defect. Setting `algorithms=['HS256']`
  on a call whose key still comes from JWKS is not the vulnerability, and a gate
  correctly ignoring it looked like a broken gate.
- Expect fixing one failure to expose the next. `set -euo pipefail` aborts the
  per-service mypy loop at the first error, so each fix uncovers the following
  latent one. Budget for a sequence of them rather than assuming one fix ends
  the job.

### 23.3 Human-only tasks

Some work must not be done by an agent, because it is a decision rather than an
implementation. The current list lives in `oner-task.md` at the repository root.

Read it before declaring work complete. It records what is blocked, what needs
review, what needs a product or security decision, and which limitations are
deliberate. Leaving an item off that file is how it becomes a surprise three
commits later.

Do not resolve these yourself:

- **Credentials and secrets.** Whether a committed credential is authorized is a
  human decision. Redaction is not remediation; rotation is. See `SECURITY.md`.
- **Merging.** No agent merges to `main` or force-pushes a protected branch.
- **Product behaviour.** Changing onboarding, redirects, or user-visible copy to
  make a check pass is a product decision, not a bug fix.
- **Review sign-off.** PR #938 and any successor need a human reviewer.

When you finish a task, add anything a human still owes to `oner-task.md` rather
than leaving it only in a commit message or on the board. If you deliberately do
not fix something, say so there and say why.

### 23.4 Mistakes that are specific to this repository

Not traps in general — mistakes that have actually been made here, with the
consequence each one produced.

- **Forgetting to register a route.** A correct route that is never added to the
  aggregator returns 404 while the code looks right. Check `openapi.json` on the
  running service.
- **One env var for both sides of a trust boundary.** `NEXT_PUBLIC_API_URL` served
  both browser code (needs the public host) and in-container server code (needs the
  docker-internal name). Server-side calls got 502 on every request.
- **A per-request nonce against prerendered HTML.** Strict CSP plus static
  prerendering means the nonce has nothing to attach to, the inline script is blocked,
  and the whole app renders a blank body. See #981.
- **Trusting `create_all` to keep the schema current.** It is `checkfirst`: it creates
  missing tables and never touches existing ones, so nine of twenty content tables had
  never been created on a fresh volume. Use `scripts/init_schemas.py`.
- **Hardcoding a machine-specific host in committed config.** A LAN IP on Caddy's
  cleartext port made the browser unable to reach the API at all, and it was
  overridden a correct `.env` value.
- **Bumping a dependency without checking the family.** See 20 — two resolutions here
  fixed one conflict by creating another.
- **Silencing a tool to make a check pass.** `except: pass`, `ignoreBuildErrors`,
  `continue-on-error`, `|| true`, blanket skips. AGENTS.md forbids it, and a silenced
  failure is how a total outage shipped through a green pipeline.
- **Leaving conflict markers behind.** A shared checkout has been left with
  syntactically invalid Python in seven files, which stopped five services importing
  and hid every regression in them. If a merge or rebase is interrupted, restore the
  tree rather than leaving it for the next agent.
- **Resolving a board or docs conflict by taking `origin`.** That silently discards
  your appended text while `rebase --continue` commits the reduced file anyway.

### 23.5 Working with other agents

Other agents are working this branch in the same checkout, right now. Coordination
is not overhead here; it is how work survives.

**Read the board before you start and before you finish.** The `Files:` claims are the
only record of who owns what. An agent that never reads it will eventually edit a file
someone else is mid-way through, and the conflict will surface as a broken file rather
than as a conflict.

**Post a `Files:` claim before you edit, and check for an existing claim first.** If
someone has already claimed the path, do not edit it — find out whether their work is
finished, or pick a different path. Overlap is the main way work is lost in this repo.

**What goes on the board** is coordination: claims, handovers, and findings that block
other people. **What goes in `oner-task.md`** is anything a human must decide. Do not
put backlog on the board; it scrolls away and duplicates. Do not put a human decision
on the board; see 23.3.

**A board entry should be readable by someone with no context.** State what you
observed, with the command and the output, not the conclusion alone. "500 with
`UndefinedColumnError: column content.price_usd does not exist`" is useful; "content
is broken" is not. Include what you already ruled out, so the next agent does not
re-derive it — that is the single most valuable part of a handover.

**Hand off rather than silently absorbing.** If a task is outside what you were asked
to do, or needs a decision that is not yours, dispatch an agent for it or record it in
`oner-task.md`. Do not quietly leave it undone, and do not quietly expand into another
agent's claimed path. This repo has real multi-agent coordination available; using it
beats writing a paragraph about why you did not.

**Take only what you can finish.** If you claim ten paths and finish three, the other
seven are now a trap for the next agent. Claim narrowly, release explicitly, and say in
your final entry which claims you have released.

**Verify another agent's report rather than repeating it.** An agent saying a bug is
fixed is a claim, not evidence. Re-run the check yourself against the running system.
In this repo an agent reported a green end-to-end flow that another agent then broke
by rebuilding the image with a stale cache, so the report was true when written and
false an hour later.

**When you are wrong, say so on the board, in the open.** Several of the most useful
entries in this repo are corrections: a wrong root cause, a bad verification command,
an overstated scope. Record them where the next agent will read them instead of
quietly fixing the code and leaving the wrong conclusion in the history.

### 23.6 Scratch space: what belongs in /tmp and what does not

`/tmp` is for scratch. It is not versioned, not reviewed, not shared with the next
agent, and **it does not survive a reboot**. Everything you have learned, and
everything reusable, must end up in the repository.

**Never leave these in /tmp:**

- **A reusable script, harness, or tool.** Anything another agent could run. These
  belong in `scripts/`, with a line in `scripts/README.md` and a pointer from
  `AGENTS.md` 19.2. Seventeen one-off browser harnesses sat in `/tmp` during the last
  audit and carried real knowledge — how to find a Chromium build, that the dev cert
  is self-signed, that `curl` cannot tell you whether a page hydrated — all of it
  lost on reboot. That knowledge is what `scripts/browser-check.mjs`,
  `causation-check.mjs` and `auth-flow-check.mjs` now preserve.
- **A procedure you had to work out.** If you derived a command sequence, a
  diagnostic order, or a set of steps by trial, that is repository knowledge. Put it
  in `ONBOARDING.md`, `docs/`, or a script — not in a shell history.
- **Findings, evidence, or a diagnosis.** Commit it, or put it in
  `oner-task.md`, or post it to the board. A conclusion that exists only in `/tmp`
  will be re-derived from scratch by the next agent, slowly.
- **Anything that would embarrass you to lose.** Test output you cited as proof,
  a traceback you quoted, a before/after comparison.

**Fine to keep in /tmp:**

- Genuinely disposable probes: a one-off `node -e`, a scratch query, a log dump.
- Another agent's in-flight work you had to move aside to rebase. **Stash or copy it,
  finish your push, and put it straight back** — see 23.1. A stash is not storage.
- Large generated output: a build log, a heap snapshot, a full dump you only need
  while debugging.
- A backup of a tree you restored from git. Note its location in your board entry so
  the owner can retrieve it, and say plainly that `/tmp` will not survive a reboot.

**The test to apply:** if the next agent would have to write it again, it belongs in
the repository. If you would be annoyed to explain its loss, it belongs in the
repository. If it is dead the moment you close the terminal, `/tmp` is fine.

**Never put credentials in `/tmp` either** — it is world-readable on most systems and
is not covered by `.gitignore`, which only protects the repository. See 24.

### 23.7 When to use a subagent

This repo has real multi-agent dispatch available. Use it — a paragraph explaining
why you did not is worth less than a 15-line prompt.

**Dispatch one when:**

- **The investigation is already done and the remaining work is execution.** This is
  the best case. You hand over a diagnosis plus the hypotheses you ruled out, and the
  agent implements without re-deriving anything. All four dispatches during the last
  audit were this shape.
- **The task needs a full verification loop** — rebuild, restart, drive a browser,
  prove red/green. That is a lot of mechanical work that does not need your context.
- **You want an independent second pair of eyes** on a diagnosis you are not fully
  confident in. Prefer a separate agent over re-reading your own work: it will not
  notice what you have already convinced yourself of.
- **Two tasks are genuinely independent** and could run in parallel. Do not dispatch
  for work that touches the same files.

**Do not dispatch when** the task is small, when you already know the fix, or when
the bottleneck is a decision rather than an implementation. Overhead is real: a
subagent has no memory of your session and you must write its prompt from scratch.

**Write the prompt as a handover, not a task ticket.** The most valuable thing you
can include is **what you have already ruled out**, with the evidence. An agent told
"CSP is the cause" re-tests the CSP; an agent told "the proxy sets the request
header, Next reads it at render.js:407, the nonce regex accepts our hex nonce, CSP is
not in `ipcForbiddenHeaders`, and both header-delivery mechanisms were tried and
neither worked" starts somewhere new. Every real finding during the audit came from
that.

Also state, explicitly:

- **The hard constraints**, including what must not be done. "Do not weaken the CSP",
  "do not use `ignoreBuildErrors`", "these uncommitted changes are someone else's and
  must not be reverted".
- **What you have already changed**, so the agent builds on it instead of
  reimplementing or reverting it.
- **That it must not commit, push, or touch the board.** Other agents share the
  tree. You own git and coordination.
- **That an honest failure report beats a confident wrong one.** Say it. Agents given
  permission to report "this does not work because X" return far more useful reports
  than ones pushed toward a green result.

**The agent's report is a claim, not evidence.** Re-run its key check yourself
against the running system before you tell the user it is fixed. In this repo every
agent that reported a fix was correct about the code and we were still wrong once
about the cause — one was sent after a lead I had already presented as most likely,
and it disproved it.

**A good report tells you what it did not do.** "I did not verify this", "this is
unverified", "these are pre-existing failures I confirmed by stashing" — that is
what makes a report trustworthy enough to act on.

**Expect the agent to find things you missed.** That is the point. In this audit a
dispatched agent found a rate-limit confound that had invalidated a comparison, a
test that passed vacuously against a real certificate, and a latent
`node:https` bug exposed by a config change. None were visible from my side.

## 24. Security

Never commit real credentials, tokens, private keys, production connection strings, or unnecessary personal data.

Use SECURITY.md for genuine vulnerability reporting. Do not disclose exploitable secrets in public issues or pull requests.

## 25. Pull requests

Use descriptive branches such as fix/auth-jwt-audience, fix/billing-refund-reconciliation, test/gateway-body-limit-regression, or docs/update-architecture-guide.

PR descriptions should state what changed, why, tests run, and any security, data, deployment, or configuration implications.

Do not merge a PR unless the task explicitly requires it. The normal agent workflow is branch -> focused commit(s) -> PR -> human review.

## Quick reference

| Concern | Location |
|---|---|
| Agent coordination | `Message-board.md` (claims, notices, recipes) |
| Human-only tasks | `oner-task.md` (decisions, review, blocked work) |
| New agent start here | `ONBOARDING.md` (path, traps, current state) |
| Backend entrypoint | services/<service>/app/main.py |
| Routes | services/<service>/app/api/ |
| Settings | services/<service>/app/core/settings.py |
| Business logic | services/<service>/app/services/ |
| Persistence | services/<service>/app/repositories/ |
| Models | services/<service>/app/models/ |
| Backend tests | services/<service>/tests/ |
| Frontend | apps/web/src/ |
| Shared events | packages/sdk/wildframe_events/ |
| Shared auth | packages/sdk/wildframe_auth/ |
| Compliance | packages/sdk/wildframe_compliance/ |
| Observability | packages/sdk/wildframe_observability/ |
| Local stack | deployments/docker-compose.dev.yml |
| Helm | infrastructure/helm/ |
| Terraform | infrastructure/terraform/ |
| Database bootstrap | infrastructure/database/ |
| CI/CD | .github/workflows/ci-cd.yml |
| Security policy | SECURITY.md |

This guide is intentionally operational. If a code change invalidates an instruction here, update AGENTS.md in the same change.