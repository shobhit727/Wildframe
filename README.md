# Wildframe OTT Streaming Platform

A FastAPI microservices-based OTT streaming platform. **Not yet production-ready** — core services start and CRUD works; email/MFA stubbed; no integration tests; observability stubbed.

## 🎯 Current State (August 2026)

| Service | Status |
|---------|--------|
| auth-service | ✅ Starts; JWT auth works; **email/MFA stubbed (501)** |
| user-service | ✅ Starts; CRUD works |
| content-service | ✅ Starts; CRUD works |
| streaming-service | ✅ Starts; CRUD works |
| admin-service | ✅ Starts; CRUD works |
| media-pipeline | ✅ Starts; orchestrator works |
| api-gateway | 🟡 Exists; unverified |
| creators-service | 🟡 Exists; unverified |
| moderation-service | 🟡 Exists; unverified |
| uploads-service | 🟡 Exists; unverified |
| analytics | 🟡 Exists; unverified |
| billing | 🟡 Exists; unverified |
| notification | 🟡 Exists; unverified |
| recommendation | 🟡 Exists; unverified |
| search | 🟡 Exists; unverified |

**What works**: All 6 core services import cleanly and expose CRUD APIs. Dockerfiles, k8s, Terraform exist.

**What's missing**: Email verification, MFA, integration tests, real observability SDK, secrets management, load testing, security audit.

## Technology Stack

### Backend
- **FastAPI** 0.100+: Async Python web framework
- **SQLAlchemy 2.0**: Async ORM
- **PostgreSQL 14+**: Primary database (per-service)
- **Redis 7.0+**: Caching and sessions
- **Kafka 3.0+**: Event streaming (configured, not verified)
- **Elasticsearch 8.0+**: Search (configured, not verified)
- **FFmpeg**: Video transcoding (configured, not verified)

### Frontend
- **Next.js 15**: React framework with SSR (scaffold only)
- **TypeScript**: Type-safe development
- **TailwindCSS**: Utility-first CSS

### Infrastructure
- **Kubernetes**: Container orchestration (manifests exist)
- **Docker**: Containerization (Dockerfiles exist)
- **Helm**: Kubernetes package manager (chart template exists)
- **Terraform**: Infrastructure as code (modules exist)
- **GitHub Actions**: CI/CD (consolidated workflow)
- **Prometheus**: Metrics (exposed, not verified)
- **Grafana**: Dashboards (not configured)
- **Loki**: Log aggregation (not configured)

## Project Structure

```
wildframe/
├── apps/
│   └── web/                        # Next.js web application (scaffold)
├── services/                       # Backend microservices
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
│   └── media-pipeline/             # Video transcoding
├── packages/
│   ├── sdk/                        # wildframe-observability stub
│   └── shared-types/               # Shared TypeScript types
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
- Python 3.11+
- Node.js 18+

### Local Development with Docker Compose

```bash
# Start all services (infrastructure + 6 core services)
docker-compose -f deployments/docker-compose.dev.yml up -d

# Wait for services to be ready
sleep 30

# Verify services
curl http://localhost:8000/health   # API Gateway (if running)
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

- ❌ Email verification (501)
- ❌ MFA/TOTP (501)
- ❌ Integration tests (only unit)
- ❌ Real observability SDK (stub in `packages/sdk/`)
- ❌ Secrets management (hardcoded defaults)
- ❌ Load testing / capacity planning
- ❌ Security audit

## License

Proprietary - Wildframe Platform

## Status

See `STATUS.md` for current progress and next steps.