# Wildframe OTT Streaming Platform

A FastAPI microservices-based OTT streaming platform. **Not yet production-ready** — core services run, auth (JWT + MFA + email verification) is in, and a full security/test sweep has been completed and the results documented in [AUDIT_FIX_SUMMARY.md](AUDIT_FIX_SUMMARY.md); remaining gaps are Infra (AWS deploys, secrets management, DRM).

## 🎯 Current State (August 2026)

| Service | Status |
|---------|--------|
| auth-service | ✅ JWT + refresh tokens; MFA/TOTP (setup, verify, challenge-login, backup codes); email verification (signed token) |
| user-service | ✅ Starts; CRUD works |
| content-service | ✅ Starts; CRUD works; duplicate genres → 409 |
| streaming-service | ✅ All endpoints JWT-authed; owner-scoped reads/writes |
| admin-service | ✅ Starts; CRUD works; tests passing |
| media-pipeline | ✅ Starts; orchestrator works |
| api-gateway | ✅ Starts; per-client rate limiting enforced (429) |
| creators-service | ✅ Starts |
| moderation-service | ✅ Starts |
| uploads-service | ✅ Starts |
| analytics | ✅ Starts |
| billing | ✅ Starts; payouts owner-checked |
| notification | ✅ Starts; send works E2E |
| recommendation | ✅ Starts |
| search | ✅ Starts; Elasticsearch wired, reindex + query verified |

**What works**: All 15 services import cleanly and their suites are green (551 tests across 16 suites). CI is green for Backend Lint (ruff + black + mypy), Helm Lint (chart renders all 15 services), Frontend CI (eslint, tsc, vitest, `next build`), Security Scan, 5 Docker Build Smokes, and all 15 backend test jobs. A full audit/pentest pass landed fixes for authz gaps (streaming IDOR, billing payouts), crash-on-startup import bugs, asyncpg timezone 500s, and wiring for the gateway rate limiter. See [AUDIT_FIX_SUMMARY.md](AUDIT_FIX_SUMMARY.md) and [STATUS.md](STATUS.md).

**What's still missing**: production deploy jobs (need AWS credentials + EKS clusters in repo secrets), secrets management, load testing, DRM (Widevine/FairPlay/PlayReady), full observability sink wiring (Prometheus/Grafana/Loki/Jaeger configs exist).

## Technology Stack

### Backend
- **FastAPI** 0.111+: Async Python web framework
- **SQLAlchemy 2.0**: Async ORM
- **PostgreSQL 14+**: Primary database (per-service)
- **Redis 7.0+**: Caching and sessions
- **Kafka 3.0+**: Event streaming
- **Elasticsearch 8.x**: Search (indexed + query verified end-to-end)
- **FFmpeg**: Video transcoding (pipeline orchestrated)
- **Python 3.13**: Runtime (asyncpg 0.30.0+ required)

### Frontend
- **Next.js 16**: React framework with SSR (real catalog/player/authz flows; 43 unit tests green)
- **TypeScript**: Type-safe development
- **TailwindCSS**: Utility-first CSS
- **Docker**: Multi-stage `apps/web/Dockerfile` builds and pushes to GHCR via CI
- **npm workspaces**: monorepo install from repo root (`npm ci --legacy-peer-deps` in CI)

### Infrastructure
- **Kubernetes**: Container orchestration (manifests exist)
- **Docker**: Containerization (Dockerfiles exist)
- **Helm**: Helm chart `infrastructure/helm/wildframe` (lints and renders all 15 services)
- **Terraform**: Infrastructure as code (modules exist)
- **GitHub Actions**: CI/CD (consolidated workflow)
- **Prometheus**: Metrics (exposed, configs exist)
- **Grafana**: Dashboards (configs exist)
- **Loki**: Log aggregation (configs exist)

## Project Structure

```
wildframe/
├── apps/
│   └── web/                        # Next.js web application (scaffold)
├── services/                       # 15 Backend microservices
│   ├── api-gateway/                # Request routing and auth
│   ├── auth-service/               # Authentication and JWT
│   ├── user-service/               # User profiles and devices
│   ├── content-service/            # Content metadata
│   ├── streaming-service/          # Video streaming manifests
│   ├── search-service/             # Content search
│   ├── recommendation-service/     # ML-based recommendations
│   ├── billing-service/            # Subscriptions and payments
│   ├── analytics-service/          # Event analytics
│   ├── notification-service/       # Multi-channel notifications
│   ├── admin-service/              # Administration
│   ├── media-pipeline/             # Video transcoding
│   ├── creators-service/           # Creator onboarding
│   ├── moderation-service/         # Content moderation
│   └── uploads-service/            # File uploads
├── packages/
│   └── sdk/
│       ├── wildframe_observability/ # Observability SDK
│       └── wildframe_events/        # Event publishing
├── infrastructure/                 # Infrastructure as code
│   ├── kubernetes/                 # K8s manifests and Helm
│   ├── terraform/                  # Terraform modules
│   └── docker/                     # Docker configurations
├── deployments/                    # Docker Compose
├── docs/                           # Documentation
└── .github/workflows/              # CI/CD
```

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Python 3.11+ (3.13 recommended)
- Node.js 20.9+
- Poetry 1.8.3

### Local Development with Docker Compose

```bash
# Start all services (infrastructure + 15 services)
docker compose -f deployments/docker-compose.dev.yml up -d

# Wait for services to be ready
sleep 30

# Verify services
curl http://localhost:8000/health   # API Gateway
curl http://localhost:8001/health   # Auth Service
curl http://localhost:8002/health   # User Service
curl http://localhost:8003/health   # Content Service
curl http://localhost:8004/health   # Streaming Service
curl http://localhost:8006/health   # Admin Service
curl http://localhost:8011/health   # Media Pipeline
```

### Run Services Individually (Dev)

```bash
# Auth Service
cd services/auth-service
poetry install
poetry run uvicorn app.main:app --reload --port 8001

# User Service
cd services/user-service
poetry install
poetry run uvicorn app.main:app --reload --port 8002

# Content Service
cd services/content-service
poetry install
poetry run uvicorn app.main:app --reload --port 8003

# Streaming Service
cd services/streaming-service
poetry install
poetry run uvicorn app.main:app --reload --port 8004

# Admin Service
cd services/admin-service
poetry install
poetry run uvicorn app.main:app --reload --port 8006

# Media Pipeline
cd services/media-pipeline
poetry install
poetry run uvicorn app.main:app --reload --port 8011
```

### Frontend

The repo is an npm-workspaces monorepo — install from the root, run in `apps/web`:

```bash
# From repo root (installs all workspaces incl. apps/web)
npm install --legacy-peer-deps   # headlessui@1 peers react<=18; app runs react 19

cd apps/web
npm run dev
# http://localhost:3000
```

### CI/CD

All pipeline checks are green: Backend Lint, Helm Lint, Backend Test (15 per-service jobs + SDK), Frontend CI (eslint → tsc → vitest → `next build`), Security Scan, Docker Build Smokes, Build & Push Frontend. Deploy Staging/Production additionally require `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` repo secrets plus `wildframe-staging`/`wildframe-production` EKS clusters — without them the deploy steps fail.

## Architecture

See `docs/ARCHITECTURE.md` for system design, service patterns, and data architecture.

## API Documentation

All endpoints under `/api/v1` (mounted by each service).

### Authentication
- `POST /api/v1/auth/register` — Create account (returns verification token)
- `POST /api/v1/auth/verify-email` — Confirm email via signed ownership token
- `POST /api/v1/auth/login` — Get access + refresh tokens (returns `requires_mfa` challenge if MFA is on)
- `POST /api/v1/auth/mfa/login-verify` — Complete an MFA-gated login (TOTP)
- `POST /api/v1/auth/refresh` — Refresh access token
- `POST /api/v1/auth/logout` — Revoke tokens
- `GET /api/v1/users/me` — Current user (requires Bearer token)
- `POST /api/v1/mfa/setup|verify|disable` — TOTP lifecycle (auth-service)

### Health Checks
- `GET /health` — Liveness
- `GET /ready` — Readiness (verifies DB)

### Rate Limiting
The api-gateway enforces per-client rate limits on proxied requests (authenticated: by user `sub`, otherwise by client IP). Exceeding the limit returns `429` with `Retry-After`. Tests that exercise the gateway must stub the limiter (`test_gateway.py` shows the pattern).

## Development

### Code Quality
```bash
# Lint (all passing)
ruff check services/
black --check services/

# Type check
for dir in services/*/app; do
  mypy "$dir"
 done

# Tests (per service, uses local pytest.ini — a combined `pytest services/` 
# run breaks on shadowed `app.*` imports)
for svc in services/*/; do
  (cd "$svc" && poetry run pytest tests --asyncio-mode=auto) || exit 1
done
```

### Adding a Service
Each service follows: `api/routes` → `services` → `repositories` → `models` with `create_app()` in `main.py`. FastAPI dependency injection uses `Annotated[Type, Depends(...)]` — never `Depends()` in argument defaults.

## Deployment

### Docker
```bash
docker build -f services/auth-service/Dockerfile -t auth-service:latest .
```

### Kubernetes
```bash
kubectl apply -f infrastructure/kubernetes/
# Helm chart: infrastructure/helm/
```

### Terraform
```bash
cd infrastructure/terraform
terraform init && terraform plan
```

## Monitoring

- Prometheus metrics: `/metrics` on each service
- Structured JSON logs with correlation IDs
- OpenTelemetry tracing via `wildframe-observability-sdk`

## Known Limitations

- ❌ Deploy jobs — blocked until AWS repo secrets + EKS clusters configured
- ❌ Secrets management (hardcoded dev defaults in settings; prod validation fails fast on them)
- ❌ Load testing / capacity planning
- ❌ DRM (Widevine/FairPlay/PlayReady) — plaintext HLS/DASH only; see [docs/DRM_SCOPE.md](docs/DRM_SCOPE.md)
- ❌ Observability sinks (OTel exporters to Jaeger wired; Grafana/Loki dashboards need finishing)

## License

Proprietary - Wildframe Platform

## Status

See `STATUS.md` for current progress and next steps.
