# Agent Instructions for Wildframe

Wildframe is a **FastAPI microservices** OTT streaming platform. It is *not* a
Django app — ignore any older notes that mention `manage.py`, `netflix_backend`,
or ViewSets. This is the source of truth for how the repo is actually built.

## Essential Setup

```bash
#Poetry, Python 3.11+)
pip install poetry
poetry install

# Bring up the full stack locally (12 app services + infra)
docker compose -f deployments/docker-compose.dev.yml up --build -d

# Run the backend test suite
pytest services --asyncio-mode=auto

# Frontend (Next.js 15)
cd apps/web && npm install && npm run dev   # http://localhost:3000
```

## Project Structure

```
wildframe/
├── services/                       # 12 independent FastAPI microservices
│   ├── api-gateway/               # routing, auth, rate limiting (host :8000)
│   ├── auth-service/              # JWT auth, refresh tokens, rate limiting
│   ├── user-service/              # profiles, devices, sessions, preferences
│   ├── content-service/           # movies/shows/seasons/episodes/genres
│   ├── streaming-service/         # HLS/DASH manifests, metrics
│   ├── search-service/            # Elasticsearch-backed search
│   ├── recommendation-service/    # ML recommendations
│   ├── billing-service/           # subscriptions + Stripe payments
│   ├── analytics-service/         # event analytics
│   ├── notification-service/      # multi-channel notifications
│   ├── admin-service/             # moderation, flags, alerts, config
│   └── media-pipeline/            # video transcoding
├── apps/web/                       # Next.js 15 frontend (viewer + admin)
├── deployments/
│   └── docker-compose.dev.yml      # local dev orchestration
├── infrastructure/
│   ├── kubernetes/                 # auth-service Helm-style manifests
│   ├── terraform/                  # AWS: VPC, EKS, RDS, ElastiCache, S3/CF
│   └── database/init-databases.sql # 12 service databases
├── packages/                       # shared libs (sdk, shared-types)
└── docs/                           # architecture, quickstart, API reference
```

## Architecture

- **12 microservices**, each with its own FastAPI `app`, SQLAlchemy 2.0 async
  ORM, and (where it matters) its own PostgreSQL database
  (`infrastructure/database/init-databases.sql` creates all 12).
- **Database-per-service**: never share a DB across services.
- **Async everything**: endpoints, SQLAlchemy sessions, and the Redis client
  (`redis.asyncio`) are all async. Do **not** use the unmaintained `aioredis`
  fork — the project depends on the official `redis` package.
- **JWT auth**: 15-min access + 7-day refresh tokens (`python-jose` /
  `pyjwt`). The api-gateway validates tokens and rate-limits per client.
- **Event-driven**: Kafka for inter-service events (user.registered, user.login,
  token.revoked, ...).
- **Observability**: OpenTelemetry tracing, Prometheus metrics, Grafana, Loki,
  Jaeger.

## Ports (docker-compose host ports)

| Host | Service |
|------|---------|
| 8000 | api-gateway |
| 8001 | auth-service |
| 8002 | user-service |
| 8003 | content-service |
| 8004 | streaming-service |
| 8005 | search-service |
| 8006 | admin-service |
| 8007 | recommendation-service |
| 8008 | billing-service |
| 8009 | analytics-service |
| 8010 | notification-service |
| 8011 | media-pipeline |

Inside the Docker network every service is reachable at the container port it
actually binds (8000 for most; 8003 for content, 8004 for streaming).

## Code Conventions

- **App factory**: each service exposes `create_app()` in `app/main.py` and a
  module-level `app = create_app()`. Uvicorn entrypoint is `app.main:app`.
- **Layering**: `api/routes/` → `services/` → `repositories/` → `models/`.
  Keep request/response Pydantic schemas in `schemas/`.
- **Dependency injection**: route handlers use `Depends(get_db)` where
  `get_db` is an async generator yielding an `AsyncSession`. The concrete name
  varies by service (`get_db`, `get_db_session`) — both exist as aliases in
  some services; prefer `get_db` in new code.
- **Health checks**: every service exposes `GET /health` (and some `/ready`).
  The k8s readiness probe targets `/health`. Health checks run
  `SELECT 1` against the DB — they must use a real SQLAlchemy statement
  (`text("SELECT 1")`), never a Python lambda.
- **Settings**: `pydantic-settings` `BaseSettings` classes in
  `app/core/settings.py`, loaded from env with a `.env` fallback.
- **Logging**: structured JSON logs via `python-json-logger`. Propagate
  `X-Request-ID` / `X-Correlation-ID` from request headers into log context.

## Common Patterns

- **Lifespan management**: `app/main.py` uses an `@asynccontextmanager`
  `lifespan` that verifies DB connectivity on startup and tears the engine down
  on shutdown.
- **Error responses**: a service-wide `RequestValidationError` handler returns
  a consistent `ErrorResponse(error, message, details)` JSON body.
- **Idempotency**: webhook handlers (Stripe) and state-changing POSTs must be
  idempotent — key on an external event/empotency ID, not on "did we already
  run".

## Key Files

- Per-service entrypoint: `services/<svc>/app/main.py`
- Per-service config: `services/<svc>/app/core/settings.py`
- Compose orchestration: `deployments/docker-compose.dev.yml`
- Infra: `infrastructure/terraform/main.tf`, `infrastructure/kubernetes/`
- Frontend: `apps/web/src/`

## Pitfalls to Do

- **Don't add `aioredis`** — it is not a dependency. Use `redis.asyncio`.
- **Don't shadow flat modules with empty package dirs** — a service that has
  both `app/repositories.py` and an empty `app/repositories/__init__.py` will
  silently break `from app.repositories import X`. If you add a package, move
  the flat module's contents into it.
- **Don't hardcode ports** — read `settings.SERVER_PORT`; the Dockerfile CMD
  and compose port mapping must agree with it.
- **Don't silently succeed on security endpoints** — email verification and
  MFA must not flip state without proof. Return `501` until the flow exists.
- **Don't forget the API prefix** — routes are mounted under `/api/v1`, so a
  handler at `/auth/login` is reached at `/api/v1/auth/login`.

## Documentation

- [Setup & service list](README.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Quickstart](docs/QUICKSTART.md)
- [API reference](docs/API_DOCUMENTATION.md)
- [Testing guide](docs/TEST_GUIDE.md)
