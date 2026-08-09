# How to Run Tests

**Current State**: Unit/route tests for all 15 microservices + the shared SDK + the frontend (vitest). All suites green (551 backend + 43 frontend, Aug 9, 2026). Integration tests that need real Postgres/Redis/Kafka/ES are not part of CI yet.

---

## 1️⃣ Prerequisites

```bash
# From repo root — Python venv + dev deps (pytest, pytest-asyncio, pytest-mock, coverage)
pip install poetry && poetry install
# or use an existing venv:
.venv/bin/pip install pytest pytest-asyncio pytest-mock pytest-cov

# Frontend (npm-workspaces monorepo — install from ROOT, not apps/web)
npm install --legacy-peer-deps
```

## 2️⃣ Run Backend Tests

Every service packs its own top-level `app` package, so **tests must run
per-service** — a combined `pytest services/` run from the repo root breaks on
shadowed `app.*` imports.

```bash
# All 15 services + SDK
for svc in services/*/; do
  (cd "$svc" && pytest tests --asyncio-mode=auto) || exit 1
done
(cd packages/sdk && PYTHONPATH="$PWD" pytest tests --asyncio-mode=auto)

# One service
cd services/auth-service && pytest tests --asyncio-mode=auto

# One file / one test
cd services/streaming-service
pytest tests/test_routes.py -k "end_playback_session" -v
```

## 3️⃣ With Coverage

```bash
cd services/auth-service
pytest tests --cov=app --cov-report=term-missing --asyncio-mode=auto
```

## 4️⃣ Frontend Tests

```bash
cd apps/web
npx vitest run          # run once (CI uses `npm test -- --run`)
npx vitest              # watch mode
npm run type-check      # tsc --noEmit
npm run lint            # eslint . (Next 16 removed `next lint`)
npm run build           # production build
```

## ⚠️ Not Available (yet)

| Test Type | Status |
|-----------|--------|
| Unit/route tests | ✅ 15 services + SDK + frontend |
| Integration (real Postgres/Redis/Kafka/ES) | ❌ testcontainers suites not written |
| E2E (browser) | ❌ Playwright scripts exist but not run in CI |
| Contract tests | ❌ Pact not wired |
| Load tests | ❌ k6/Locust not written |

## 🚨 Troubleshooting

- **`fixture 'mocker' not found`** — `pip install pytest-mock` into the venv.
- **`ModuleNotFoundError: No module named 'app'`** — you're not in the service dir; `cd services/<svc>` first.
- **`app.main` / `app.models` resolves to the wrong service** — running a combined sweep from the repo root; use the per-service loop above.
- **Tests pass locally, fail in CI** — CI installs into a fresh service venv (`poetry install --with dev`); mirror with `poetry install` inside the service dir.

## 📊 Test Stats (Aug 9, 2026 — all passing)

| Suite | Tests |
|-------|-------|
| auth-service | 109 |
| content-service | 81 |
| streaming-service | 71 |
| billing-service | 54 |
| user-service | 51 |
| admin-service | 47 |
| api-gateway | 26 |
| moderation-service | 25 |
| creators-service | 16 |
| media-pipeline | 15 |
| analytics-service | 14 |
| recommendation-service | 14 |
| search-service | 17 |
| uploads-service | 13 |
| notification-service | 8 |
| packages/sdk | 49 |
| **Backend total** | **551** |
| apps/web (vitest) | 43 |

---

**Last updated**: August 9, 2026
