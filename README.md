# Wildframe OTT Streaming Platform

A FastAPI microservices-based OTT streaming platform. **Core platform stabilized (Aug 2026)** — all 15 services import cleanly, compose/CI valid, JWT security hardened, lifespan health checks added; email/MFA stubbed (501); integration tests need Docker.

## 🎯 Current State (August 2026)

| Service | Status |
|---------|--------|
| auth-service | ✅ Starts; JWT auth works; **email/MFA stubbed (501)**; TokenBlacklist added |
| user-service | ✅ Starts; CRUD works; lifespan + health_check |
| content-service | ✅ Starts; CRUD works; lifespan + health_check |
| streaming-service | ✅ Starts; CRUD works; lifespan + health_check; FastAPI deprecation warnings |
| admin-service | ✅ Starts; CRUD works; lifespan + health_check |
| media-pipeline | ✅ Starts; orchestrator works; lifespan + health_check |
| api-gateway | ✅ Starts; routing + auth + rate limiting; lifespan + health_check |
| creators-service | ✅ Starts; CRUD works; lifespan + health_check |
| moderation-service | ✅ Starts; CRUD works; lifespan + health_check |
| uploads-service | ✅ Starts; CRUD works; lifespan + health_check |
| search-service | ✅ Starts; lifespan + health_check; needs `elasticsearch` dep |
| recommendation-service | ✅ Starts; lifespan + health_check; FastAPI deprecation warnings |
| billing-service | ✅ Starts; lifespan + health_check; needs `stripe` dep |
| analytics-service | ✅ Starts; lifespan + health_check; FastAPI deprecation warnings |
| notification-service | ✅ Starts; lifespan + health_check; FastAPI deprecation warnings |

**What works**: All 15 services import cleanly, expose CRUD/health endpoints, have lifespan DB health checks. Docker Compose + CI/CD valid.

**What's missing**: Email verification, MFA, integration tests (need Docker), optional deps (elasticsearch, stripe), security audit, load testing.

## Technology Stack

### Backend
- **FastAPI** 0.100+: Async Python web framework
- **SQLAlchemy 2.0**: Async ORM
- **PostgreSQL 14+**: Primary database (per-service, 15 DBs)
- **Redis 7.0+**: Caching and sessions
- **Kafka 3.0+**: Event streaming
- **Elasticsearch 8.0+**: Search (search-service)
- **FFmpeg**: Video transcoding (media-pipeline)

### Frontend
- **Next.js 15**: React framework with SSR (scaffold)
- **TypeScript**: Type-safe development
- **TailwindCSS**: Utility-first CSS

### Infrastructure
- **Kubernetes**: Container orchestration (manifests exist)
- **Docker**: Containerization (Dockerfiles on python:3.13-slim)
- **Helm**: Kubernetes package manager (chart template exists)
- **Terraform**: Infrastructure as code (modules exist)
- **GitHub Actions**: CI/CD (consolidated workflow: lint → test → build-smoke)
- **Prometheus**: Metrics (`/metrics` on all services, config in `infrastructure/prometheus/`)
- **Grafana**: Dashboards (provisioning in `infrastructure/grafana/`)
- **Loki**: Log aggregation (config in `infrastructure/loki/`)
- **Jaeger**: Distributed tracing

## Project Structure

```
wildframe/
├── apps/
│   └── web/                        # Next.js web application (scaffold)
├── services/                       # 15 Backend microservices
│   ├── api-gateway/                # Request routing, auth, rate limiting
│   ├── auth-service/               # Authentication and JWT
│   ├── user-service/               # User profiles and devices
│   ├── content-service/            # Content metadata
│   ├── streaming-service/          # Video streaming manifests
│   ├── search-service/             # Content search (Elasticsearch)
│   ├── recommendation-service/     # ML-based recommendations
│   ├── billing-service/            # Subscriptions and payments (Stripe)
│   ├── analytics-service/          # Event analytics
│   ├── notification-service/       # Multi-channel notifications
│   ├── admin-service/              # Administration & moderation
│   ├── media-pipeline/             # Video transcoding
│   ├── creators-service/           # Creator onboarding & profiles
│   ├── moderation-service/         # Content moderation
│   └── uploads-service/            # File uploads & processing
├── packages/
│   └── sdk/
│       ├── wildframe_events/       # Kafka event publishing/subscribing
│       └── wildframe_observability/ # OpenTelemetry, metrics, logging, health
├── infrastructure/                 # Infrastructure as code
│   ├── kubernetes/                 # K8s manifests and Helm
│   ├── terraform/                  # Terraform modules
│   ├── prometheus/                 # Prometheus config
│   ├── grafana/                    # Grafana provisioning
│   └── loki/                       # Loki config
├── deployments/                    # Docker Compose
├── docs/                           # Documentation
├── scripts/                        # Utility scripts (smoke-tests.sh)
└── .github/workflows/              # CI/CD
```

## Quick Start

### Prerequisites
- Docker & Docker Compose v2
- Python 3.11+ (project requires `>=3.11,<3.16`)
- Node.js 18+ (with pnpm)

### Local Development with Docker Compose

```bash
# Start all services (infrastructure + 15 app services)
docker compose -f deployments/docker-compose.dev.yml up --build -d

# Wait for services to be ready
sleep 30

# Verify services
curl http://localhost:8000/health   # API Gateway
curl http://localhost:8001/health   # Auth Service
curl http://localhost:8002/health   # User Service
# ... ports 8003-8014 per AGENTS.md port table
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

```bash
cd apps/web
npm install
npm run dev
# http://localhost:3000
```

## Architecture

See `docs/ARCHITECTURE.md` for system design, service patterns, and data architecture.

## API Documentation

All endpoints under `/api/v1` (mounted by each service).

### Authentication
- `POST /api/v1/auth/register` — Create account
- `POST /api/v1/auth/login` — Get access + refresh tokens
- `POST /api/v1/auth/refresh` — Refresh access token
- `POST /api/v1/auth/logout` — Revoke tokens
- `GET /api/v1/users/me` — Current user (requires Bearer token)

### Health Checks
- `GET /health` — Liveness
- `GET /ready` — Readiness (verifies DB)

## Development

### Code Quality
```bash
# Lint
ruff check services/
black --check services/

# Type check
mypy services/

# Tests (unit only; no integration tests yet)
pytest services/auth-service/tests --asyncio-mode=auto
pytest services/user-service/tests --asyncio-mode=auto
```

### Adding a Service
Each service follows: `api/routes` → `services` → `repositories` → `models` with `create_app()` in `main.py`.

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
- OpenTelemetry tracing (stubbed — needs real SDK)

## Known Limitations

- ❌ Email verification (501 — stubbed per AGENTS.md)
- ❌ MFA/TOTP (501 — stubbed per AGENTS.md)
- ❌ Integration tests (only unit; testcontainers needs Docker)
- ❌ Real observability SDK (stub in `packages/sdk/wildframe_observability/`)
- ✅ Secrets management hardened (model_validator fails in production with defaults)
- ❌ Load testing / capacity planning
- ❌ Security audit (96 Dependabot alerts)

## Recent Audit (August 2026)

A full audit was performed fixing 22 issues across infra, compose, services, and CI.
See **[AUDIT_FIX_SUMMARY.md](AUDIT_FIX_SUMMARY.md)** for:
- 22 fixed items (no Docker required)
- Tasks requiring Docker (testcontainers, full stack)
- Optional deps for CI
- Pre-existing debt not from audit

## License

Proprietary - Wildframe Platform

## Status

See `STATUS.md` for current progress and `AGENTS.md` for developer guidance.