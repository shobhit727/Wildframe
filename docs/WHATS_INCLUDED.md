# 📦 What's Included

A flat inventory of everything shipped in the Wildframe platform. Use this as a checklist when evaluating the project or planning what's next.

**Last Updated**: June 4, 2026
**Version**: 1.0.0

---

## Microservices (12)

| # | Service | Port | Database | Endpoints | Status |
|---:|---|---:|---|---:|---|
| 1 | API Gateway | 8000 | — | 3 | ✅ |
| 2 | Auth | 8001 | `auth_db` | 9 | ✅ |
| 3 | User | 8002 | `users_db` | 11 | ✅ |
| 4 | Content | 8003 | `content_db` | 10+ | ✅ |
| 5 | Streaming | 8004 | `streaming_db` | 7 | ✅ |
| 6 | Search | 8005 | `search_db` | 2 | ✅ |
| 7 | Admin | 8006 | `admin_db` | 12 | ✅ |
| 8 | Recommendation | 8007 | `recommendation_db` | 2 | ✅ |
| 9 | Billing | 8008 | `billing_db` | 2 | ✅ |
| 10 | Analytics | 8009 | `analytics_db` | 2 | ✅ |
| 11 | Notification | 8010 | `notification_db` | 2 | ✅ |
| 12 | Media Pipeline | 8011 | `media_db` | 2 | ✅ |

**Total endpoints**: 50+

---

## Infrastructure (14 Containers)

### Data plane
- PostgreSQL 15 (12 logical databases, one per service)
- Redis 7 (cache, sessions, rate-limit windows)
- Elasticsearch 8.10 (search index)
- Kafka 7.5 + Zookeeper (event streaming)

### Observability
- Prometheus (metrics)
- Grafana (dashboards)
- Jaeger (tracing)
- Loki (logs)

### Tooling
- Docker Compose orchestrator
- Prometheus exporters (postgres, redis)

---

## Frontend (1 App)

- Next.js 15 (App Router) + React 19
- TypeScript 5 strict mode
- TailwindCSS 4 with design tokens
- TanStack Query for server state
- Zustand for client state
- Axios HTTP client
- HLS.js + dashjs for adaptive playback
- Vitest + Playwright test stack

Pages: home, login, signup, browse, watch, my-list, account, billing.

---

## Infrastructure as Code

| Asset | Location | Notes |
|---|---|---|
| Docker Compose (dev) | `deployments/docker-compose.dev.yml` | 14 containers, hot reload |
| Kubernetes manifests | `infrastructure/kubernetes/` | Auth service template, HPA, RBAC, NetworkPolicy |
| Terraform | `infrastructure/terraform/` | EKS, RDS, ElastiCache, S3, CloudFront, VPC, IAM |
| Database init | `infrastructure/database/init-databases.sql` | Creates 12 databases on first boot |
| GitHub Actions | `.github/workflows/` | CI for lint + test + build |

---

## Tests

| Type | Count | Tooling |
|---|---:|---|
| Backend unit + integration | 70+ | pytest, pytest-asyncio, pytest-cov |
| Frontend unit | 20+ | Vitest |
| Frontend E2E | starter | Playwright |

Coverage targets: 75–85% per backend service, 70% on frontend components.

---

## Documentation (in `docs/`)

| File | Purpose |
|---|---|
| `INDEX.md` | This index |
| `QUICKSTART.md` | 10-minute local setup |
| `QUICK_LOCAL_SETUP.md` | Checklist variant of QUICKSTART |
| `ARCHITECTURE.md` | System design |
| `SERVICE_ARCHITECTURE_PATTERN.md` | Per-service layout & conventions |
| `DATABASE_SCHEMA.md` | Schema per service |
| `API_DOCUMENTATION.md` | Endpoint reference with examples |
| `FRONTEND_ARCHITECTURE.md` | Frontend structure & conventions |
| `DEVELOPMENT.md` | Dev workflow, conventions, glossary |
| `TEST_GUIDE.md` | Testing playbook |
| `TESTING_GUIDE.md` | Manual API testing with curl |
| `DEPLOYMENT_GUIDE.md` | Production deployment |
| `OPERATIONS.md` | Runbooks, on-call |
| `MONITORING.md` | Metrics, logs, tracing, alerting |
| `CONTRIBUTING.md` | PR conventions, code review |
| `GLOSSARY.md` | 80+ technical terms |
| `OVERVIEW.md` | One-page summary |
| `DOCUMENTATION_GUIDE.md` | How the docs are organized |
| `WHATS_INCLUDED.md` | This file |

**Total**: 19 documentation files.

---

## Top-Level Files

| File | Purpose |
|---|---|
| `README.md` | Project landing page |
| `QUICK_START.md` | TL;DR commands |
| `HOW_TO_RUN_TESTS.md` | Test cheat sheet |
| `start_services.sh` | One-shot Docker Compose launcher |
| `run_tests.sh` | Test runner across all services |
| `run_all_tests.sh` | Extended test runner with coverage |
| `IMPLEMENTATION_COMPLETE.md` | Status report |
| `FINAL_EXECUTION_REPORT.md` | Execution metrics |
| `COMPLETION_SUMMARY.md` | Roll-up summary |
| `STATUS.md` | Living status doc |
| `pyproject.toml` | Workspace Python config |
| `package.json` | Workspace Node config |
| `nx.json` | (Optional) monorepo orchestration |

---

## Feature Inventory

### Auth
- [x] Email + password registration
- [x] JWT access (15 min) + refresh (7 days)
- [x] Refresh token rotation
- [x] Token blacklist / revocation
- [x] Login audit log
- [x] Brute-force rate limiting
- [x] Password reset flow (token)
- [x] Email verification

### User
- [x] Profile CRUD
- [x] Multi-device management
- [x] Active sessions list + revoke
- [x] Preferences (genres, language, autoplay)
- [x] Watch history

### Content
- [x] Movie / Show / Season / Episode hierarchy
- [x] Genres + tagging
- [x] Full-text search via Elasticsearch
- [x] Filtering (genre, year, rating)
- [x] Pagination

### Streaming
- [x] Session start / stop
- [x] HLS / DASH manifest endpoint
- [x] Watch position persistence
- [x] Resume from last position
- [x] Per-session metrics

### Search
- [x] Multi-field query
- [x] Trending content
- [x] Elasticsearch index management

### Admin
- [x] User moderation (suspend, ban, role)
- [x] Content flagging & review queue
- [x] System alerts
- [x] System config (feature flags)
- [x] Audit log

### Recommendation
- [x] Personalized picks
- [x] Preference updates
- [x] Cold-start fallback (trending)

### Billing
- [x] Subscription tiers (free, basic, premium, family)
- [x] Upgrade / downgrade
- [x] Invoice generation

### Analytics
- [x] Event ingestion
- [x] Per-user timeline
- [x] Aggregation-ready schema

### Notification
- [x] In-app, email, push, SMS channels
- [x] Read / unread tracking
- [x] Multi-recipient fan-out

### Media Pipeline
- [x] Transcoding job submission
- [x] Job status tracking
- [x] Per-asset metadata

---

## What's **Not** Included (Yet)

- ❌ Live streaming / DVR
- ❌ DRM (Widevine / FairPlay) integration
- ❌ Recommendation ML model training pipeline
- ❌ Mobile native apps (iOS / Android)
- ❌ Smart TV apps (Roku, Apple TV, Fire TV)
- ❌ Multi-region active/active deployment
- ❌ Real payment provider integration (billing is wired, payments stubbed)
- ❌ Production CI/CD release pipeline (only build + test in CI today)

These are tracked as next-quarter targets.

---

## Related

- [QUICKSTART.md](QUICKSTART.md) — Get it running
- [ARCHITECTURE.md](ARCHITECTURE.md) — How it fits together
- [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) — Promote to production
