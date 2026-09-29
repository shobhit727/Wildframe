# Agent Instructions for Wild Frame (Wildframe)

This is the repository-level operating guide for coding agents. It describes the current implementation on main and should be updated when the implementation changes.

**New to this repository? Read `ONBOARDING.md` first.** It is the path — what to do
in what order, which commands actually work, and the traps that produce wrong
conclusions. This file is the rules; `ONBOARDING.md` is the route through them.

| File | Read it for |
|---|---|
| `AGENTS.md` (this one) | engineering rules, conventions, testing, troubleshooting |
| `ONBOARDING.md` | the path through the work, and the current state |
| `AGENT_COORDINATION.md` | working with other agents, the shared tree, `/tmp`, subagents |
| `oner-task.md` | what a human still owes, and what agents deliberately did not fix |

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

### 8.1 Changing an API without breaking clients

The frontend, the gateway, and the service are three consumers of one contract, and
they are updated in one commit or not at all. In this monorepo that is cheap — do it
in the same change.

- **Never remove or rename a field.** Deprecate it: keep returning it, mark it
  deprecated in the schema, and stop consuming it on your own side. A removed
  response field is an outage for a client you cannot see.
- **Additive first.** A new optional field or a new endpoint is safe. A changed
  meaning of an existing field is not, however compatible the shape looks.
- **Response models are the contract.** If a Pydantic model drops a field, the field
  disappears. Check the response model, not just the database, when auditing what a
  client can see.
- **Route a changed path rather than swapping it**, then delete the old one once no
  caller remains. `tests/contract/` is where the route inventory lives — if a path
  exists there, something depends on it.
- **The E2E fixtures are a second, unreconciled contract.** They are typed against a
  hand-maintained DTO, so a backend field can change and the frontend fixture type
  will not. Two defects during the last audit existed only in that gap: a scale
  mismatch on `audience_score`, and `content.price_usd` present in the model and
  absent from the schema. When you change a payload, update `e2e/fixtures.ts` and the
  matching `types/index.ts` in the same commit.
- **Do not treat a green contract test as proof the client works.** Those tests check
  that no shared secret is used for JWT verification, not that the payloads agree.

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

### 15.1 Frontend conventions worth stating

- **A Server Component by default.** Add `"use client"` only for state, effects,
  refs, browser APIs, or event handlers. A client boundary that does not need to
  exist costs bundle size and hydration work.
- **Holding a render on an effect is a last resort.** `apps/web/src/app/providers.tsx`
  gates the entire tree on `authReady`, so if `hydrate()` never settles the whole app
  is a spinner — a single unguarded promise here takes down every route. If you must
  gate, give it a timeout and a visible failure.
- **Do not route on the client what the proxy already knows.** `src/proxy.ts`
  redirects unauthenticated users off protected routes. A second client-side guard
  duplicates that rule and will drift.
- **Auth state lives in the store, not in component state.** `useAuthStore` and the
  `__Host-wf_refresh` cookie are the source of truth; page components read them.
- **One variable per audience.** Browser code derives its API base from
  `window.location.hostname`; in-container server code uses `AUTH_SERVICE_URL`.
  Merging them produced a 502 on every hard reload — see 5.2.
- **A cookie named `__Host-` must not be given a `Domain` attribute**, in app code
  or in a test harness. The browser drops it silently and the symptom looks like an
  auth bug. This bit a verification script before it bit anything in `src/`.

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

### 19.1b Turn a repeated task into a script

**If you run the same sequence of commands more than twice, it is a script.** This is
the highest-leverage habit in this repo, and it is not optional polish.

Every serious fix in the last audit needed a loop — build, force-recreate, wait for
health, hit a real endpoint, read the logs. That loop was retyped by hand dozens of
times, and hand-retyping is where the mistakes happened: a missing
`--force-recreate` shipped an old image, a missing `--no-cache` hid the change, and
`up -d` was repeatedly assumed to have picked up a new build.

**What to script, in rough order of return:**

- **Anything you ran three or more times.** No threshold debate; you already know.
- **Anything that has to be right in a specific order.** Build before recreate,
  install before test, lock before install. A script cannot forget step two.
- **Anything where a past failure was a missing flag.** Every one of those becomes a
  permanent guard the moment it is in a script.
- **Anything you need to do again after a rebuild or a fresh volume.** Schema
  bootstrap, stack bring-up, a smoke pass.

**Where it goes:** `scripts/`, documented in `scripts/README.md`, referenced from
this section. Not `/tmp` — see `AGENT_COORDINATION.md` 23.6.

**The bar is that someone else can run it without you.** A script you have to
explain is a note, not a tool. That means: a usage comment at the top, sane
defaults, an `--help` or a printed usage line, and **a meaningful exit code** so it
can gate a build rather than only be read.

**Verify a new script before you commit it.** Run it against the running stack. Every
harness written in the last audit reported a failure caused by its own bug before it
worked: a syntax error, a cookie injected with an attribute the browser silently
drops, and a form submit that reused an email and correctly got a `409`. A script that
fails for the wrong reason sends the next person after a defect that is not there.

**Do not delete a working script because it looks redundant.** If two scripts overlap,
merge them and keep the one that is more general. If you find yourself retyping
something, the duplication is the finding.

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

Agent workflow lives in **`AGENT_COORDINATION.md`** — read it before you touch a shared
branch. It covers claiming paths, the verified git failure modes, what belongs in
`/tmp` and what does not, when to dispatch a subagent, and the rules for what a human
must decide. It was extracted from this file when it reached 378 lines, because
everything below is engineering guidance you need whether or not other agents are
active.

## 24. Security

Never commit real credentials, tokens, private keys, production connection strings, or unnecessary personal data.

Use SECURITY.md for genuine vulnerability reporting. Do not disclose exploitable secrets in public issues or pull requests.

### 24.1 Handling a suspected secret

If you find a credential in the repository, an agent, or a log:

- **Do not decide yourself whether it is authorized.** That is a human decision, and
  the evidence is often contradictory — one commit asserting "owner-authorized"
  while another says it should never have been there. Record what you found, in
  `oner-task.md`, and stop.
- **Redaction is not remediation.** A credential that was ever committed must be
  treated as compromised and **rotated**. Rewriting history does not un-clone anything.
- **Do not quote the value**, not even into a board entry. A previous agent re-quoted a
  fragment into the board to make a point, and the entry had to be removed. Refer to
  it by location, not content.
- **Scratch files are not a safe place for it either.** `/tmp` is world-readable on most
  systems and `.gitignore` does not cover it. See AGENT_COORDINATION.md 23.6.

A secret in a dev compose file is still a secret, and a LAN IP with a TLS-terminating
listener in front of it is not a secret but is a machine-specific hardcode that breaks
everybody else's setup — see AGENT_COORDINATION.md 23.4.

## 25. Pull requests

Use descriptive branches such as fix/auth-jwt-audience, fix/billing-refund-reconciliation, test/gateway-body-limit-regression, or docs/update-architecture-guide.

PR descriptions should state what changed, why, tests run, and any security, data, deployment, or configuration implications.

**Attach the evidence, not just the claim.** "Tests pass" is not reviewable. Include
the failing output before the fix and the passing output after, and say which command
produced each. If a check could not be run, say so rather than leaving it implied.

Do not merge a PR unless the task explicitly requires it. The normal agent workflow is branch -> focused commit(s) -> PR -> human review.

## 26. Troubleshooting: symptom first

Reach for the logs before theorising. Every row here cost real time to derive.

| Symptom | Most likely cause | First command |
|---|---|---|
| Service healthy, every real route 500s | middleware raising before the handler | `logs <svc> \| grep -A20 Error` |
| Blank page, HTTP 200, curl looks fine | page never hydrated; CSP blocked the inline script | `node scripts/browser-check.mjs /route` |
| `502` from a Next.js server-side fetch | in-container code used a public host; `localhost` is the container itself | `exec <svc> curl -sv http://api-gateway:8000` |
| `UndefinedColumnError` on a fresh volume | `create_all` is `checkfirst` — 9 of 20 content tables were never created | `python scripts/init_schemas.py` |
| Build says `CACHED`, image has old code | layer cache; `--no-cache` plus `--force-recreate` | `build --no-cache && up -d --force-recreate` |
| `up -d` did not pick up a new image | container was reused, not recreated | `up -d --no-deps --force-recreate` |
| Register returns 201 but UI says it failed | session rejected after the account was created | `node scripts/auth-flow-check.mjs` |
| Hard reload bounces to /login | server-side session read failing | `curl -sk https://localhost:3000/auth-session` |
| Services healthy but Kafka operations fail | ACLs never created; `ALLOW_EVERYONE_IF_NO_ACL_FOUND=false` | see issue #893 |
| Kafka healthy but `listTopics` times out | healthcheck only proves TLS/SASL, not metadata | `logs kafka \| tail` |
| `pip check` reports a transitive conflict | partial upgrade; the family pins each other | `pip check` then align the whole family |
| Thousands of `import-untyped` errors | deps not installed in this venv | `poetry install --with dev` |
| A test passes but the app is broken | the test mocks the layer where the value should arrive | run it against the running stack |
| Push says success, commit is missing | branch moved; check content, not exit code | `git show origin/<b>:<path> \| grep -c` |
| Push rejected, tree is dirty | someone else's uncommitted work — see AGENT_COORDINATION.md 23.1 | `git status --porcelain` |

## 27. Before you claim something is done

A single checkable list. Most of these cost me a correction during the last audit.

- [ ] Ran the change against the **running stack**, not only the test suite
- [ ] Hit a **real endpoint**, not `/health` — a healthy service can 500 on every route
- [ ] For anything browser-rendered: `node scripts/browser-check.mjs` passes
- [ ] If a schema changed: `python scripts/init_schemas.py` run, verified on a **fresh
      volume**, and the migration is additive or explicitly reviewed
- [ ] If a dependency moved: `pip check` clean, and the **whole family** moved together
- [ ] Verified the **built artifact** contains the change, not just the source tree
- [ ] New test **fails without the fix** — revert and watch it go red
- [ ] Board updated: claim released, findings recorded
- [ ] `oner-task.md` updated with anything you deliberately left undone
- [ ] Nothing reusable left in `/tmp` — see 23.6
- [ ] Diff reviewed for accidental security, API, dependency or docs changes
- [ ] No secrets in the diff, in a log, or in a board entry — see 24.1

**If you cannot tick a box, say so** and name it. An honest partial result is far more
useful than a confident wrong one, and it is what the next agent can act on.

## 28. Reviewing someone else's change

Reviewers: the branch is large, so spend your attention where the risk is.

- [ ] **Does the test actually fail without the fix?** Ask to see it go red. A test
      that passes both ways is decoration.
- [ ] **What was ruled out, and is the conclusion consistent with it?** Check the
      reasoning against the evidence, not just the conclusion.
- [ ] **Did anything get weakened to make something pass?** `except: pass`,
      `ignoreBuildErrors`, `continue-on-error`, `|| true`, a deleted assertion, a
      lowered threshold. These are the single most common way an outage ships green.
- [ ] **Is there a scope creep into unrelated files?** Especially other agents' paths.
- [ ] **Schema and dependency changes** are the highest-risk review targets: does the
      change work on a fresh volume, and does the dependency family move together?
- [ ] **Claims that were verified, versus claims that were assumed.** Ask which.

A reviewer's most valuable output is sometimes "this does not prove what it claims",
not a list of style nits. Say so plainly when that is your finding.

## Quick reference

| Concern | Location |
|---|---|
| Agent coordination | `Message-board.md` (claims, notices, recipes) |
| Human-only tasks | `oner-task.md` (decisions, review, blocked work) |
| New agent start here | `ONBOARDING.md` (path, traps, current state) |
| Multi-agent / shared tree | `AGENT_COORDINATION.md` (claims, /tmp, subagents) |
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