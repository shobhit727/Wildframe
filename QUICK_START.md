# Wildframe Quick Reference

**Date**: August 1, 2026  
**Status**: 6 core services start; 10 unverified; not production-ready

---

## 🚀 Get Started (Core 6 Services)

```bash
cd /home/phoenix/Desktop/wildframe

# Start infrastructure + 6 core services
docker-compose -f deployments/docker-compose.dev.yml up -d

# Wait ~30s
sleep 30

# Verify health
curl https://localhost:8001/health  # Auth
curl https://localhost:8002/health  # User
curl https://localhost:8003/health  # Content
curl https://localhost:8004/health  # Streaming
curl https://localhost:8006/health  # Admin
curl https://localhost:8011/health  # Media Pipeline
```

---

## 📦 Service Overview (Verified)

| Port | Service | Status | Endpoints | Notes |
|------|---------|--------|-----------|-------|
| 8001 | Auth | ✅ Starts | ~5 | JWT works; **email/MFA 501** |
| 8002 | User | ✅ Starts | ~8 | CRUD works |
| 8003 | Content | ✅ Starts | ~10 | CRUD works |
| 8004 | Streaming | ✅ Starts | ~8 | CRUD works |
| 8006 | Admin | ✅ Starts | ~12 | CRUD works |
| 8011 | Media Pipeline | ✅ Starts | ~3 | Orchestrator works |

**Unverified (exist but not tested)**: API Gateway (8000), Search (8005), Recommendation (8007), Billing (8008), Analytics (8009), Notification (8010), Creators, Moderation, Uploads.

---

## 🔗 API Examples (What Works)

```bash
# Register
curl -X POST https://localhost:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"SecurePass123!","first_name":"John","last_name":"Doe"}'

# Login → returns access_token + refresh_token
curl -X POST https://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"SecurePass123!"}'

# Protected endpoint
TOKEN="<access_token>"
curl https://localhost:8001/api/v1/users/me -H "Authorization: Bearer $TOKEN"

# Content
curl https://localhost:8003/api/v1/content
curl https://localhost:8003/api/v1/content/trending

# Health
curl https://localhost:8001/health
```

---

## 🧪 Testing

```bash
# Unit tests (no integration tests)
cd services/auth-service && poetry run pytest tests/ -v --asyncio-mode=auto
cd services/user-service && poetry run pytest tests/ -v --asyncio-mode=auto

# Lint
ruff check services/
black --check services/
```

---

## 📦 Tech Stack (Actual)

| Component | Version | Status |
|-----------|---------|--------|
| FastAPI | 0.104+ | ✅ |
| SQLAlchemy | 2.0 | ✅ |
| PostgreSQL | 15 | ✅ (per-service) |
| Redis | 7 | ✅ |
| Kafka | 3+ | ⚠️ Configured, unverified |
| Elasticsearch | 8+ | ⚠️ Configured, unverified |
| Prometheus | Latest | ✅ Exposed (`/metrics`) |
| Grafana/Jaeger/Loki | - | ❌ Not running |

---

## 📁 Key Directories

```
wildframe/
├── services/              # 16 service directories
├── deployments/           # docker-compose.dev.yml
├── infrastructure/        # Terraform, Kubernetes
├── apps/web/              # Next.js (scaffold)
├── packages/sdk/          # wildframe-observability (stub)
├── docs/                  # ARCHITECTURE, DEVELOPMENT, OPERATIONS
├── .github/workflows/ci-cd.yml
└── STATUS.md              # Current reality
```

---

## ⚡ Common Commands

```bash
# Start core
docker-compose -f deployments/docker-compose.dev.yml up -d

# Logs
docker-compose -f deployments/docker-compose.dev.yml logs -f auth-service

# Stop
docker-compose -f deployments/docker-compose.dev.yml down

# Tests
cd services/auth-service && poetry run pytest tests/ -v --asyncio-mode=auto

# Lint
ruff check services/
black --check services/
```

---

## 🔴 What's NOT Ready

| Area | Status |
|------|--------|
| Email verification | 501 |
| MFA/TOTP | 501 |
| Integration tests | ❌ None |
| Observability SDK | Stubbed |
| Secrets management | Hardcoded |
| Load testing | ❌ None |
| Security audit | ❌ None |
| Frontend | Scaffold only |

---

## 🛑 Stop

```bash
docker-compose -f deployments/docker-compose.dev.yml down
# With data cleanup:
docker-compose -f deployments/docker-compose.dev.yml down -v
```

---

**See `STATUS.md` for full reality check and next steps.**

**Last updated**: August 1, 2026