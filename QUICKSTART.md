# 🚀 Wildframe Quick Start Guide

**Current State**: 6 core services start and expose CRUD APIs. Email/MFA stubbed. No integration tests. Observability stubbed.

---

## 1️⃣ Prerequisites

```bash
# Required
docker & docker-compose
python 3.11+
node 18+

# Optional (for local dev without Docker)
poetry
postgresql 14+
redis 7+
```

## 2️⃣ Start Core Platform (6 Services + Infra)

```bash
cd /home/phoenix/Desktop/wildframe

# Start infrastructure + 6 core services
docker-compose -f deployments/docker-compose.dev.yml up -d

# Wait ~30s for services to initialize
sleep 30

# Verify health
curl http://localhost:8001/health  # Auth Service
curl http://localhost:8002/health  # User Service
curl http://localhost:8003/health  # Content Service
curl http://localhost:8004/health  # Streaming Service
curl http://localhost:8006/health  # Admin Service
curl http://localhost:8011/health  # Media Pipeline
```

**What runs**: Postgres (per-service), Redis, Kafka + 6 core services.

**What does NOT run**: Search, Recommendation, Billing, Analytics, Notification, API Gateway (exist but unverified), Prometheus/Grafana/Jaeger/Loki (not in compose).

---

## 3️⃣ Run Services Locally (Dev)

```bash
# Auth Service (port 8001)
cd services/auth-service && poetry install && poetry run uvicorn app.main:app --reload --port 8001

# User Service (port 8002)
cd services/user-service && poetry install && poetry run uvicorn app.main:app --reload --port 8002

# Content Service (port 8003)
cd services/content-service && poetry install && poetry run uvicorn app.main:app --reload --port 8003

# Streaming Service (port 8004)
cd services/streaming-service && poetry install && poetry run uvicorn app.main:app --reload --port 8004

# Admin Service (port 8006)
cd services/admin-service && poetry install && poetry run uvicorn app.main:app --reload --port 8006

# Media Pipeline (port 8011)
cd services/media-pipeline && poetry install && poetry run uvicorn app.main:app --reload --port 8011
```

---

## 4️⃣ Test Auth Flow (What Works)

```bash
# 1. Register (works)
curl -X POST http://localhost:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"SecurePass123!","first_name":"John","last_name":"Doe"}'

# 2. Login (works - returns access_token + refresh_token)
curl -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"SecurePass123!"}'

# 3. Use access_token for protected endpoints
TOKEN="<access_token_from_login>"
curl http://localhost:8001/api/v1/users/me -H "Authorization: Bearer $TOKEN"
```

**What returns 501 (not implemented)**:
- `POST /api/v1/auth/verify-email` — email verification
- `POST /api/v1/auth/mfa/setup` — MFA setup
- `POST /api/v1/auth/mfa/verify` — MFA verify

---

## 5️⃣ Test Other Services

```bash
# User Service - create profile (needs auth)
curl -X POST http://localhost:8002/api/v1/profiles \
  -H "Authorization: Bearer $TOKEN"

# Content Service - list content
curl http://localhost:8003/api/v1/content

# Streaming Service - health
curl http://localhost:8004/health

# Admin Service - health
curl http://localhost:8006/health

# Media Pipeline - health
curl http://localhost:8011/health
```

---

## 6️⃣ Run Tests

```bash
# Unit tests only (no integration tests yet)
cd services/auth-service && poetry run pytest tests/ -v --asyncio-mode=auto
cd services/user-service && poetry run pytest tests/ -v --asyncio-mode=auto
cd services/content-service && poetry run pytest tests/ -v --asyncio-mode=auto
cd services/streaming-service && poetry run pytest tests/ -v --asyncio-mode=auto
cd services/admin-service && poetry run pytest tests/ -v --asyncio-mode=auto
cd services/media-pipeline && poetry run pytest tests/ -v --asyncio-mode=auto

# Lint
ruff check services/
black --check services/
```

---

## 7️⃣ Frontend (Scaffold Only)

```bash
cd apps/web
npm install
npm run dev
# http://localhost:3000
```

---

## ⚠️ Known Limitations

| Feature | Status |
|---------|--------|
| Email verification | 501 Not Implemented |
| MFA/TOTP | 501 Not Implemented |
| Integration tests | ❌ None |
| Observability SDK | Stubbed (`packages/sdk/`) |
| Secrets management | Hardcoded defaults |
| Load testing | ❌ None |
| Security audit | ❌ None |
| Search/Rec/Billing/Analytics/Notification | Exist but unverified |

---

## 🛑 Stop Everything

```bash
docker-compose -f deployments/docker-compose.dev.yml down
# With data cleanup (DESTRUCTIVE):
docker-compose -f deployments/docker-compose.dev.yml down -v
```

---

## 📂 Quick Reference

```bash
# Start all
docker-compose -f deployments/docker-compose.dev.yml up -d

# Logs
docker-compose -f deployments/docker-compose.dev.yml logs -f auth-service

# Rebuild
docker-compose -f deployments/docker-compose.dev.yml build auth-service

# Tests
cd services/auth-service && poetry run pytest tests/ -v --asyncio-mode=auto

# Lint
ruff check services/
black --check services/
```