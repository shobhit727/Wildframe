# Wildframe Codebase Learning Outline

This is a guided reading order for the current repository. It is intentionally organized by execution flow instead of alphabetic file order: first learn how a request enters the platform, then how one service processes it, then compare the remaining services.

## 0. Ground Rules

- Treat source code, `AGENTS.md`, `README.md`, `STATUS.md`, and the current CI workflow as authoritative.
- Ignore local `.venv`, `__pycache__`, `.mypy_cache`, `.pytest_cache`, `logs`, `node_modules`, and `.next` directories while learning.
- Each backend service has its own top-level `app` package. Run tests from the service directory; do not combine all service tests from the repository root.
- The platform currently has 15 FastAPI services, a Next.js frontend, and a shared Python SDK.

## 1. Orientation: Read These First

Read in this order:

1. `README.md` — current architecture, local stack, testing, CI/CD, and known production gaps.
2. `STATUS.md` — what is implemented now and what remains unfinished.
3. `AGENTS.md` — service list, ports, TLS, conventions, and important pitfalls.
4. `docs/ARCHITECTURE.md` — conceptual architecture; verify conflicting facts against `AGENTS.md` and the source.
5. `docs/SERVICE_ARCHITECTURE_PATTERN.md` — the intended internal service layering.
6. `docs/API_DOCUMENTATION.md` — gateway paths, authentication, errors, and public API shape.
7. `docs/TEST_GUIDE.md` — how the repository validates each layer.

Do not begin with the historical root guides such as `START_HERE.md`, `STARTUP_GUIDE.md`, `QUICKSTART.md`, `*_COMPLETE.md`, or `FINAL_EXECUTION_REPORT.md`. They describe older repository states.

## 2. Learn One Request End to End

Use content browsing as the first complete vertical slice.

### Browser to backend

1. `apps/web/src/app/layout.tsx` — frontend root layout.
2. `apps/web/src/app/providers.tsx` — client-side providers.
3. `apps/web/src/api/client.ts` — gateway URL, Axios interceptors, access-token attachment, refresh behavior, and DTO normalization.
4. `apps/web/src/proxy.ts` — frontend route protection and redirects.
5. `infrastructure/caddy/Caddyfile` — HTTPS host routing.
6. `services/api-gateway/app/main.py` — gateway startup, Redis, HTTP client, lifespan, and middleware setup.
7. `services/api-gateway/app/middleware.py` — service registry, rate limiting, optional JWT inspection, header handling, and shared-client lifecycle.
8. `services/api-gateway/app/api/gateway_routes.py` — catch-all proxy, forwarding, upstream errors, and response header filtering.

The runtime path to keep in mind is:

```text
Browser -> Next.js -> Caddy TLS -> API gateway -> selected service -> database
                                      |                         |
                                      +-> Redis rate limit      +-> Kafka events
```

### Content service vertical slice

Read these files in order, following one route from HTTP input to database:

1. `services/content-service/app/main.py`
2. `services/content-service/app/core/settings.py`
3. `services/content-service/app/core/database.py`
4. `services/content-service/app/api/routes/__init__.py`
5. The route module selected by the router in that file.
6. `services/content-service/app/schemas/__init__.py` and nearby schema files.
7. `services/content-service/app/services/__init__.py` and the called service method.
8. `services/content-service/app/repositories/__init__.py` and the called repository method.
9. `services/content-service/app/models/__init__.py` and the related model.
10. The matching test in `services/content-service/tests/`.

When reading, answer four questions: What validates input? Where is authorization checked? Where does state change? What response shape leaves the service?

## 3. The Standard FastAPI Service Shape

Most services follow this sequence:

```text
app/main.py
  -> app/api/routes/
  -> app/services/
  -> app/repositories/ or app/repositories.py
  -> app/models/
```

Cross-cutting files to inspect in every service:

- `app/main.py` — app factory, lifespan, middleware, health/readiness, router registration, and observability.
- `app/core/settings.py` — environment-backed configuration.
- `app/core/database.py` — async SQLAlchemy engine/session lifecycle.
- `app/core/logging.py` — structured logging and request context.
- `app/core/events.py` or `app/core/event_consumer.py` — event publishing and consumption when used.
- `app/api/routes/` — HTTP endpoints and dependency boundaries.
- `app/schemas/` — Pydantic request/response contracts.
- `app/services/` — business rules and transaction orchestration.
- `app/repositories/` or `app/repositories.py` — database access.
- `app/models/` — SQLAlchemy tables and relationships.
- `tests/` — fixtures, route tests, service tests, and security regressions.
- `Dockerfile`, `pyproject.toml`, and `requirements.txt` — runtime packaging.

Not every service has every file or uses exactly the same module granularity. Read the imports from `main.py` and the router to locate the real implementation instead of assuming a filename.

## 4. Authentication and Authorization

Read authentication before studying protected business endpoints:

1. `services/auth-service/app/main.py`
2. `services/auth-service/app/core/settings.py`
3. `services/auth-service/app/security/__init__.py` — password hashing, JWT claims, token decoding, key overlap, and token types.
4. `services/auth-service/app/api/routes/auth.py` — register, login, refresh, logout, revocation, and MFA flows.
5. `services/auth-service/app/models/` — user, auth-version, and revocation state.
6. `services/auth-service/tests/` — expected security behavior.
7. `services/content-service/app/api/routes/__init__.py` — representative downstream JWT verification and audience checking.

Important model: the gateway is a transparent proxy. It may inspect a bearer token for rate limiting, but each backend service must authenticate and authorize the request at its own boundary.

The browser stores the access token in memory. The refresh token is persisted through `apps/web/src/app/auth-session/route.ts` as an HttpOnly cookie. Follow that route together with `apps/web/src/api/client.ts` to understand refresh and logout behavior.

## 5. Shared SDK

Read the shared packages after understanding one service.

### Events

1. `packages/sdk/wildframe_events/__init__.py` — public API and event envelope.
2. `packages/sdk/wildframe_events/publisher.py` — in-memory and Kafka publishing.
3. `packages/sdk/wildframe_events/subscriber.py` — consumption, retries, dead letter handling, and deduplication.
4. `packages/sdk/wildframe_events/tests/` — executable event contracts.
5. `services/content-service/app/core/events.py` — a service integration.

### Observability

1. `packages/sdk/wildframe_observability/wire.py` — logging, correlation IDs, Prometheus metrics, and optional tracing.
2. `packages/sdk/wildframe_observability/` — instrumentation helpers and tests.
3. `infrastructure/prometheus/prometheus.yml` — scrape configuration.
4. `infrastructure/grafana/`, `infrastructure/loki/`, and the Jaeger settings in `deployments/docker-compose.dev.yml`.

### Compliance

Read `packages/sdk/wildframe_compliance/` when working on privacy, retention, DSAR, audit, or data classification behavior.

## 6. Backend Services: Recommended Order

For each service, read `app/main.py`, then its router, settings/database, service layer, repository/model layer, and finally tests. The list below gives the domain question to keep in mind.

1. `services/auth-service/` — identity, passwords, JWTs, MFA, sessions, and revocation. Start with `app/security/__init__.py` and `app/api/routes/auth.py`.
2. `services/user-service/` — profiles, preferences, devices, sessions, and user-owned data authorization.
3. `services/content-service/` — movies, series, seasons, episodes, genres, catalog permissions, and content events.
4. `services/streaming-service/` — playback sessions, manifests, stream authorization, metrics, and playback lifecycle.
5. `services/search-service/` — Elasticsearch indexing and query behavior.
6. `services/recommendation-service/` — recommendation inputs, ranking, and fallback behavior.
7. `services/billing-service/` — subscriptions, invoices, Stripe-facing webhooks, and idempotency.
8. `services/analytics-service/` — event ingestion, aggregation, and access control around analytics data.
9. `services/notification-service/` — notification persistence, delivery, and event-driven triggers.
10. `services/media-pipeline/` — upload/transcode jobs, media state, and pipeline idempotency.
11. `services/uploads-service/` — upload authorization, file metadata, and processing handoff.
12. `services/creators-service/` — creator onboarding and creator profiles.
13. `services/moderation-service/` — moderation workflows, flags, and review state.
14. `services/admin-service/` — administrative operations, alerts, moderation controls, and configuration.
15. `services/api-gateway/` — revisit after the services; then the routing, security, and rate-limit decisions will be easier to evaluate.

For every service, compare these files rather than reading every initializer:

```text
<service>/app/main.py
<service>/app/core/settings.py
<service>/app/core/database.py
<service>/app/api/routes/
<service>/app/services/
<service>/app/repositories/  (or app/repositories.py)
<service>/app/models/
<service>/app/schemas/
<service>/tests/
<service>/Dockerfile
<service>/pyproject.toml
```

## 7. Frontend Learning Path

Read `apps/web/src/` in this order:

1. `app/layout.tsx` and `app/providers.tsx` — application shell and providers.
2. `app/page.tsx` — landing/authenticated home behavior.
3. `app/login/page.tsx` and `app/signup/page.tsx` — auth UI and API calls.
4. `api/client.ts` — API contract, token lifecycle, normalization, and retries.
5. `app/auth-session/route.ts` — server-side refresh-token cookie boundary.
6. `proxy.ts` — request-time route protection.
7. `stores/auth.ts` — client auth state.
8. `app/browse/page.tsx` — catalog browsing.
9. `app/watch/[id]/page.tsx` — playback and streaming-service integration.
10. `app/admin/layout.tsx` and `app/admin/` — admin route tree and role checks.
11. `components/`, `hooks/`, `types/`, `utils/`, and `config/` — shared UI and supporting contracts.
12. `src/__tests__/` and `playwright.config.ts` — frontend unit and E2E tests.

When following a screen, trace: page -> hook/component -> API client -> gateway path -> service route -> normalized UI type.

## 8. Local Runtime and Deployment

Read local orchestration before Kubernetes:

1. `deployments/docker-compose.dev.yml` — PostgreSQL, Redis, Kafka, Elasticsearch, observability tools, Caddy, all services, and the web app.
2. `infrastructure/database/init-databases.sql` — database-per-service names.
3. `infrastructure/caddy/Caddyfile` — host ports and TLS routing.
4. `apps/web/certificates/` — development certificates used by Caddy and Next.js.
5. `scripts/init_schemas.py` — schema creation from SQLAlchemy metadata.
6. `scripts/seed_demo.py` — demo data and the first useful manual workflow.

Then read Kubernetes and delivery:

1. `infrastructure/helm/wildframe/Chart.yaml`
2. `infrastructure/helm/wildframe/values.yaml`
3. `infrastructure/helm/wildframe/templates/deployment.yaml`
4. The remaining templates for services, ingress, network policies, probes, PDBs, and Kafka topics.
5. `infrastructure/helm/values-staging.yaml` and `infrastructure/helm/values-production.yaml`.
6. `infrastructure/terraform/main.tf` and the neighboring Terraform modules.
7. `.github/workflows/ci-cd.yml` — the executable lint, test, build, scan, and deploy pipeline.

Note a current documentation/code inconsistency: `AGENTS.md` says schemas are created from SQLAlchemy models and there is no migration framework, while the default Helm values still contain an Alembic migration job. Treat that as a follow-up to verify, not as an assumption about runtime behavior.

## 9. Testing as a Learning Tool

Read tests in this order:

1. One auth route test in `services/auth-service/tests/`.
2. One gateway proxy/rate-limit test in `services/api-gateway/tests/`.
3. One content route/service test in `services/content-service/tests/`.
4. SDK tests under `packages/sdk/*/tests/`.
5. Contract tests under `tests/contract/`.
6. Live-stack tests under `tests/integration/`.
7. Frontend tests under `apps/web/src/__tests__/` and E2E setup in `apps/web/playwright.config.ts`.
8. `load-tests/locustfile.py` for gateway performance assumptions.

The normal backend command is:

```bash
for svc in services/*/; do
  (cd "$svc" && pytest tests --asyncio-mode=auto) || exit 1
done
```

## 10. Suggested Study Projects

Use small changes to test your understanding:

1. Add or update a health/readiness assertion in one service.
2. Trace a login request from the browser to JWT issuance and back.
3. Add a focused repository/service test without changing the public API.
4. Follow a content event from publisher to subscriber.
5. Trace a playback request and identify every authorization boundary.
6. Render the Helm chart and find where one service's port and probe are set.
7. Run the frontend contract tests after changing an API DTO.

The goal is not to memorize every file. For each feature, be able to locate the route, contract, business rule, persistence operation, event side effect, frontend caller, and test that protects it.
