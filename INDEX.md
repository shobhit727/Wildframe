# Wildframe Platform - Documentation Index

**Current**: August 4, 2026 — Full audit complete; 22 issues fixed; platform stabilized

---

## 📖 Main Documents (Start Here)

1. **[AUDIT_FIX_SUMMARY.md](AUDIT_FIX_SUMMARY.md)** ⭐ **START HERE**
   - Full audit: 22 fixed items, Docker-required tasks, pre-existing debt
   - What needs Docker vs what doesn't

2. **[STATUS.md](STATUS.md)**
   - Current reality: what works, what's missing, next steps
   - Service-by-service verification status

3. **[AGENTS.md](AGENTS.md)**
   - Developer guide: 15 services, port table, conventions, pitfalls
   - Source of truth for repo structure

4. **[README.md](README.md)**
   - Project overview with honest state
   - Quick start for all 15 services
   - Known limitations table

5. **[QUICKSTART.md](QUICKSTART.md)**
   - 4-step local dev setup
   - Auth flow examples
   - What works / what returns 501

---

## 📋 Detailed Documentation

| Doc | Status | Path |
|-----|--------|------|
| Architecture | ✅ Accurate | `docs/ARCHITECTURE.md` |
| Development | ✅ Accurate | `docs/DEVELOPMENT.md` |
| Operations | ✅ Accurate | `docs/OPERATIONS.md` |
| API Documentation | ⚠️ Outdated | `docs/API_DOCUMENTATION.md` |
| Database Schema | ⚠️ Outdated | `docs/DATABASE_SCHEMA.md` |
| Deployment Guide | ⚠️ Outdated | `docs/DEPLOYMENT_GUIDE.md` |
| Monitoring | ⚠️ Outdated | `docs/MONITORING.md` |
| Contributing | ✅ Accurate | `docs/CONTRIBUTING.md` |
| Glossary | ✅ Accurate | `docs/GLOSSARY.md` |

---

## 🚀 Quick Navigation

| Goal | Document |
|------|----------|
| **Audit results & fixes** | [AUDIT_FIX_SUMMARY.md](AUDIT_FIX_SUMMARY.md) |
| **Honest status & next steps** | [STATUS.md](STATUS.md) |
| **Quick start (15 services)** | [QUICKSTART.md](QUICKSTART.md) |
| **What actually works** | [README.md](README.md) |
| **Developer guide** | [AGENTS.md](AGENTS.md) |
| **Architecture & patterns** | `docs/ARCHITECTURE.md` |
| **Development workflow** | `docs/DEVELOPMENT.md` |

---

## 📁 Project Structure (Current)

```
wildframe/
├── AUDIT_FIX_SUMMARY.md           # ⭐ Audit results (22 fixes)
├── AGENTS.md                      # Developer guide (15 services)
├── STATUS.md                      # Current reality
├── README.md                      # Honest overview
├── QUICKSTART.md                  # Local dev setup
├── services/                      # 15 service dirs (all import cleanly)
├── deployments/
│   └── docker-compose.dev.yml     # 15 services + infra
├── infrastructure/
│   ├── kubernetes/                # K8s manifests
│   ├── terraform/                 # IaC
│   ├── prometheus/                # Prometheus config ✅
│   ├── grafana/                   # Grafana provisioning ✅
│   ├── loki/                      # Loki config ✅
│   └── database/init-databases.sql # 16 DBs ✅
├── packages/sdk/
│   ├── wildframe_events/          # Kafka events
│   └── wildframe_observability/   # Observability SDK
├── apps/web/                      # Next.js (scaffold)
├── scripts/                       # smoke-tests.sh
└── .github/workflows/ci-cd.yml   # CI/CD (pnpm, lint, test, build)
```

---

## 🎯 Quick Links — All 15 Services

| Service | Port | Health Check | Notes |
|---------|------|--------------|-------|
| api-gateway | 8000 | `curl localhost:8000/health` | Routing + auth + rate limiting |
| auth-service | 8001 | `curl localhost:8001/health` | JWT works; email/MFA 501 |
| user-service | 8002 | `curl localhost:8002/health` | CRUD works |
| content-service | 8003 | `curl localhost:8003/health` | CRUD works |
| streaming-service | 8004 | `curl localhost:8004/health` | CRUD works |
| search-service | 8005 | `curl localhost:8005/health` | Needs elasticsearch dep |
| admin-service | 8006 | `curl localhost:8006/health` | CRUD works |
| recommendation-service | 8007 | `curl localhost:8007/health` | CRUD works |
| billing-service | 8008 | `curl localhost:8008/health` | Needs stripe dep |
| analytics-service | 8009 | `curl localhost:8009/health` | CRUD works |
| notification-service | 8010 | `curl localhost:8010/health` | CRUD works |
| media-pipeline | 8011 | `curl localhost:8011/health` | Orchestrator works |
| creators-service | 8012 | `curl localhost:8012/health` | CRUD works |
| moderation-service | 8013 | `curl localhost:8013/health` | CRUD works |
| uploads-service | 8014 | `curl localhost:8014/health` | CRUD works |

---

## ⚡ Quick Commands

```bash
# Start all 15 services + infra
docker compose -f deployments/docker-compose.dev.yml up --build -d

# Verify
curl localhost:8001/health  # Auth
curl localhost:8002/health  # User

# Tests (needs Docker for testcontainers)
pytest services --asyncio-mode=auto

# Lint
ruff check services/
black --check services/
```

---

## 📊 Reality Summary

| Area | Status |
|------|--------|
| 15 services | ✅ Import & start |
| Docker Compose | ✅ Valid, all configs exist |
| CI/CD | ✅ Lint passing, pnpm fixed |
| Lifespan + health checks | ✅ All 15 services |
| JWT security | ✅ Production hard-fail on defaults |
| Email/MFA | ❌ 501 stubs (per AGENTS.md) |
| Integration tests | ❌ Need Docker (testcontainers) |
| Observability SDK | 🟡 Stub wired into all services |
| Secrets management | ✅ Hardened (model_validator) |
| Load testing | ❌ None |
| Security audit | ❌ 96 Dependabot alerts |

---

**Last updated**: August 4, 2026  
**Start with**: [AUDIT_FIX_SUMMARY.md](AUDIT_FIX_SUMMARY.md)