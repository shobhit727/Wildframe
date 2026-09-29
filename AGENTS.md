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

Regression tests should fail against the old behavior and pass against the new behavior. Prefer externally observable assertions over tests that merely reproduce implementation details.

## 20. Dependencies

The repository has a root Poetry project plus service-level dependency metadata and lockfiles; some services also have independent Poetry/uv configuration.

When changing dependencies, inspect the service metadata, root metadata if shared, lockfiles, Docker installation behavior, and the relevant tests. Do not add a dependency to compensate for a local package/import mistake.

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
- **`git pull --rebase` can revert uncommitted work** when the other agent
  pushes. Re-check your edits after every pull, and treat a surprising
  "nothing added to commit" as evidence the tree moved, not that your work was
  redundant.
- An absence of output is not evidence of success in a shared tree. Re-verify
  with a command whose failure mode you have actually seen.

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