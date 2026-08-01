# Wildframe Platform - Documentation Index

**Current**: August 1, 2026 — Core services startup-fixed; production gaps remain

---

## 📖 Main Documents (Start Here)

1. **[STATUS.md](STATUS.md)** ⭐ **START HERE**
   - Current reality: what works, what's missing, next steps
   - Service-by-service verification status
   - Production readiness checklist

2. **[README.md](README.md)**
   - Project overview with honest state
   - Quick start for 6 core services
   - Known limitations table

3. **[QUICKSTART.md](QUICKSTART.md)**
   - 4-step local dev setup
   - Auth flow examples
   - What works / what returns 501

4. **[IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md)**
   - This session's actual work
   - Reality check vs old claims
   - Remaining production gaps

5. **[FINAL_EXECUTION_REPORT.md](FINAL_EXECUTION_REPORT.md)**
   - Session summary with reality check
   - Verified vs old claims

---

## 📋 Detailed Documentation (Accurate)

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
| **Honest status & next steps** | [STATUS.md](STATUS.md) |
| **Quick start (6 services)** | [QUICKSTART.md](QUICKSTART.md) |
| **What actually works** | [README.md](README.md) |
| **This session's work** | [IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md) |
| **Reality vs old claims** | [FINAL_EXECUTION_REPORT.md](FINAL_EXECUTION_REPORT.md) |
| **Architecture & patterns** | `docs/ARCHITECTURE.md` |
| **Development workflow** | `docs/DEVELOPMENT.md` |
| **Operations/runbooks** | `docs/OPERATIONS.md` |

---

## 📁 Project Structure (Current)

```
wildframe/
├── STATUS.md                    # ⭐ Current reality
├── README.md                    # Honest overview
├── QUICKSTART.md                # Local dev setup
├── IMPLEMENTATION_COMPLETE.md   # This session
├── FINAL_EXECUTION_REPORT.md    # Session summary
├── QUICKSTART.md                # Quick start
├── QUICK_START.md               # Quick reference
├── COMPLETION_SUMMARY.md        # Session summary
├── FRONTEND_COMPLETE.md         # Frontend reality
├── README.md                    # Honest overview
├── AGENTS.md                    # Agent instructions
├── start_services.sh            # Not verified
├── run_all_tests.sh             # Not verified
├── services/                    # 16 service dirs (6 verified)
├── deployments/
│   └── docker-compose.dev.yml   # Core 6 + infra
├── infrastructure/
│   ├── kubernetes/              # K8s manifests
│   └── terraform/               # IaC
├── apps/web/                    # Next.js (scaffold)
├── packages/sdk/                # wildframe-observability (stub)
├── docs/                        # 13 docs (3 accurate)
└── .github/workflows/ci-cd.yml  # Consolidated CI/CD
```

---

## 🎯 Quick Links

| Service | Health Check | Notes |
|---------|--------------|-------|
| Auth | `curl localhost:8001/health` | JWT works; email/MFA 501 |
| User | `curl localhost:8002/health` | CRUD works |
| Content | `curl localhost:8003/health` | CRUD works |
| Streaming | `curl localhost:8004/health` | CRUD works |
| Admin | `curl localhost:8006/health` | CRUD works |
| Media Pipeline | `curl localhost:8011/health` | Orchestrator works |

---

## ⚡ Quick Commands

```bash
# Start 6 core services + infra
docker-compose -f deployments/docker-compose.dev.yml up -d

# Verify
curl localhost:8001/health  # Auth
curl localhost:8002/health  # User
curl localhost:8003/health  # Content

# Tests
cd services/auth-service && poetry run pytest tests/ -v --asyncio-mode=auto

# Lint
ruff check services/
black --check services/
```

---

## 📊 Reality Summary

| Area | Status |
|------|--------|
| 6 core services | ✅ Import & start |
| Email/MFA | ❌ 501 stubs |
| Integration tests | ❌ None |
| Observability SDK | ❌ Stub |
| Secrets management | ❌ Hardcoded |
| Load testing | ❌ None |
| Security audit | ❌ None |

---

## 📞 Quick Help

| Question | Answer |
|----------|--------|
| **What actually works?** | See `STATUS.md` |
| **How to start?** | See `QUICKSTART.md` |
| **What's missing?** | See `IMPLEMENTATION_COMPLETE.md` |
| **Architecture?** | `docs/ARCHITECTURE.md` |
| **Code conventions?** | `docs/DEVELOPMENT.md` |

---

**Last updated**: August 1, 2026  
**Start with**: [STATUS.md](STATUS.md)