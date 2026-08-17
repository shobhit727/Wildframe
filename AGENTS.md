# Agent Instructions for Wildframe

Wildframe is a **FastAPI microservices** OTT streaming platform. It is *not* a
Django app — ignore any older notes that mention `manage.py`, `netflix_backend`,
or ViewSets. This is the source of truth for how the repo is actually built.

## Essential Setup

```bash
#Poetry, Python 3.11+)
pip install poetry
poetry install

# Bring up the full stack locally (15 app services + infra)
docker compose -f deployments/docker-compose.dev.yml up --build -d

# Run the backend test suite
# Every service packs its own top-level `app` package, so tests must run
# per-service (a combined `pytest services/` run from the repo root breaks
# on shadowed `app.*` imports).
for svc in services/*/; do
  (cd "$svc" && pytest tests --asyncio-mode=auto) || exit 1
done

# Run the live-stack integration suite (needs the compose stack up; skips
# itself when the stack is down). ~12 min, 87 tests.
poetry run pytest tests/integration -q

# Frontend (Next.js 15)
cd apps/web && npm install && npm run dev   # https://localhost:3000
```

## Project Structure

```
wildframe/
├── services/                       # 15 independent FastAPI microservices
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
│   ├── media-pipeline/            # video transcoding
│   ├── creators-service/          # creator onboarding & profiles
│   ├── moderation-service/        # content moderation
│   └── uploads-service/           # file uploads & processing
├── apps/web/                       # Next.js 15 frontend (viewer + admin)
├── deployments/
│   └── docker-compose.dev.yml      # local dev orchestration
├── infrastructure/
│   ├── kubernetes/                 # auth-service Helm-style manifests
│   ├── terraform/                  # AWS: VPC, EKS, RDS, ElastiCache, S3/CF
│   └── database/init-databases.sql # 16 service databases
├── packages/
│   └── sdk/
│       ├── wildframe_events/       # Kafka event publishing/subscribing
│       └── wildframe_observability/ # OpenTelemetry, metrics, logging, health
└── docs/                           # architecture, quickstart, API reference
```

## Architecture

- **15 microservices**, each with its own FastAPI `app`, SQLAlchemy 2.0 async
  ORM, and (where it matters) its own PostgreSQL database
  (`infrastructure/database/init-databases.sql` creates all 16).
- **Database-per-service**: never share a DB across services.
- **Async everything**: endpoints, SQLAlchemy sessions, and the Redis client
  (`redis.asyncio`) are all async. Do **not** use the unmaintained `aioredis`
  fork — the project depends on the official `redis` package.
- **JWT auth**: 15-min access + 7-day refresh tokens (`python-jose` /
  `pyjwt`). The api-gateway is a **transparent proxy**: it rate-limits per
  client in `proxy_request` (key: user `sub` or IP) but does not itself reject
  proxied requests — every backend service must verify tokens at its own
  boundary.
- **JWT audience**: auth-service tokens carry `aud: "wildframe-api"`. Every
  service that verifies auth-issued tokens must decode with
  `audience=settings.JWT_AUDIENCE` (`"wildframe-api"`), or python-jose raises
  `JWTClaimsError: Invalid audience` on aud-bearing tokens. The gateway's own
  decode is the one exception (no audience check).
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
| 8012 | creators-service |
| 8013 | moderation-service |
| 8014 | uploads-service |

Inside the Docker network every service is reachable at the container port it
actually binds (8000 for most; 8003 for content, 8004 for streaming).

## HTTPS / TLS (dev)

- A **Caddy reverse proxy** (`deployments/docker-compose.dev.yml` → `caddy`
  service, config in `infrastructure/caddy/Caddyfile`) is the only host-facing
  entry point for the services, pgAdmin (:5050), Prometheus (:9090), Loki
  (:3100), Jaeger UI (:16686) and Grafana (:3001, native HTTPS). Host port
  bindings on the app services are removed from compose; `http://` on those
  ports is refused. Plain `http://` to a proxied port surfaces as
  SSL_ERROR_RX_RECORD_TOO_LONG in browsers.
- Certificates are the self-signed pair in `apps/web/certificates/`
  (`localhost.pem` / `localhost-key.pem`, SANs: localhost, 127.0.0.1, ::1).
  Regenerate with:
  `openssl req -x509 -newkey rsa:2048 -keyout apps/web/certificates/localhost-key.pem -out apps/web/certificates/localhost.pem -days 365 -nodes -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1"`
  then `chmod 644` both files (Caddy/Grafana containers read them as non-root)
  and restart `caddy` + `grafana`.
- The Next.js dev server (`apps/web`, `npm run dev`) serves HTTPS itself via
  `--experimental-https` with the same cert pair (see `dev` script); never
  point it at `http://localhost:3000`.
- `NEXT_PUBLIC_API_URL` defaults to `https://localhost:8000` (Caddy → gateway).
  Internal service-to-service traffic stays plain HTTP on the docker network —
  only host-facing ports are TLS.
- Infra ports are **not** proxied: postgres 5432, redis 6379, kafka 9092,
  zookeeper 2181, elasticsearch 9200, exporters 9121/9187, jaeger ingest
  14250/14268.

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
- **Don't use `import jwt`** — services install `python-jose`, not PyJWT;
  import `from jose import jwt` and catch `jwt.JWTError`.
- **Don't write tz-aware datetimes into naive columns** — model columns are
  `TIMESTAMP WITHOUT TIME ZONE`; pass `datetime.now(UTC).replace(tzinfo=None)`
  or the insert/update raises asyncpg `DataError` (500s seen in
  streaming/notification `ended_at`, metrics, `created_at`).
- **Don't mutate state before authorization** — e.g. `POST
  /playback-sessions/{id}/end` must fetch + owner-check first; a 403 response
  must never come after the side effect (see `end_playback_session`).
- **Don't forget the gateway rate limiter** — the api-gateway enforces 429s in
  `proxy_request` (key: user `sub` or IP). Gateway tests stub it; don't remove
  the call.
- **Don't decode auth-issued JWTs without an audience** — see the JWT
  audience rule above. A decode that omits `audience=` silently passes for
  aud-less tokens but throws for real auth-service tokens; the integration
  suite catches this.
- **Don't forget the API prefix** — routes are mounted under `/api/v1`, so a
  handler at `/auth/login` is reached at `/api/v1/auth/login` (via the gateway:
  `/{service}/api/v1/...`).
- **No migration framework** — services do not use Alembic despite older docs.
  Schema changes are applied by hand to the live dev DB (e.g. the billing
  `invoices` drift repaired in Aug 2026). Verify column lists against the
  running stack before assuming a table matches the models.

## Documentation

- [Setup & service list](README.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Quickstart](docs/QUICKSTART.md)
- [API reference](docs/API_DOCUMENTATION.md)
- [Testing guide](docs/TEST_GUIDE.md)
