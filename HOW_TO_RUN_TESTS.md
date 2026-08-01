# How to Run Tests

**Current State**: Unit tests only (no integration tests). 6 core services have test directories.

---

## 1️⃣ Prerequisites

```bash
# Install test dependencies
python3 -m pip install pytest pytest-asyncio --break-system-packages

# Or per-service with poetry
cd services/auth-service && poetry install --with dev
```

## 2️⃣ Run Unit Tests (No DB Required)

```bash
# Auth Service
cd services/auth-service && python3 -m pytest tests/ -v --asyncio-mode=auto

# User Service
cd services/user-service && python3 -m pytest tests/ -v --asyncio-mode=auto

# Content Service
cd services/content-service && python3 -m pytest tests/ -v --asyncio-mode=auto

# Streaming Service
cd services/streaming-service && python3 -m pytest tests/ -v --asyncio-mode=auto

# Admin Service
cd services/admin-service && python3 -m pytest tests/ -v --asyncio-mode=auto

# Media Pipeline
cd services/media-pipeline && python3 -m pytest tests/ -v --asyncio-mode=auto
```

## 3️⃣ With Coverage

```bash
cd services/auth-service
python3 -m pytest tests/ --cov=app --cov-report=term-missing --asyncio-mode=auto
```

## 4️⃣ Run All Unit Tests

```bash
for svc in auth user content streaming admin media-pipeline; do
  echo "=== $svc ==="
  cd services/${svc}-service && python3 -m pytest tests/ -v --asyncio-mode=auto
  cd -
done
```

## ⚠️ What's NOT Available

| Test Type | Status |
|-----------|--------|
| Unit tests | ✅ 6 services |
| Integration tests | ❌ None |
| E2E tests | ❌ None |
| Contract tests | ❌ None |
| Load tests | ❌ None |
| CI test automation | ⚠️ CI runs unit tests |

## 🚨 Troubleshooting

```bash
# pytest not found
python3 -m pip install pytest pytest-asyncio --break-system-packages

# ModuleNotFoundError: No module 'app'
# Ensure you're in the service directory:
cd services/auth-service

# Port conflicts
docker-compose -f deployments/docker-compose.dev.yml down
```

## 📊 Current Test Stats (Estimated)

| Service | Unit Tests | Coverage |
|---------|------------|----------|
| auth-service | ~10 | ~60% |
| user-service | ~8 | ~50% |
| content-service | ~6 | ~40% |
| streaming-service | ~5 | ~30% |
| admin-service | ~8 | ~50% |
| media-pipeline | ~4 | ~30% |

---

## 🎯 Next Steps

1. Add pytest fixtures for real DB (testcontainers or test DB)
2. Write integration tests for each service
3. Add contract tests between services
4. Add k6 load test scripts
5. Configure CI to run integration tests

---

**Last updated**: August 1, 2026