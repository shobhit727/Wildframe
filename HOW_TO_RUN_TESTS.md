# How to Run Tests

**Current State**: Unit/route tests for all 15 microservices + the shared SDK + the frontend (vitest) + a live-stack integration suite (`tests/integration/`, 93 tests). Backend suites green (780 unit/route tests, Aug 17, 2026) with one known pre-existing failure (billing `test_release_tranche_not_locked`, unrelated to recent work). The integration suite needs the dockerized stack running and skips itself otherwise.

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

## 2b️⃣ Run the Live-Stack Integration Suite

Cross-service integration tests live at `tests/integration/` (repo root). They
exercise the real HTTPS stack through the Caddy proxy (auth token lifecycle,
gateway rate limiting, cross-service authorization, billing webhook
idempotency, contract schemas, health/readiness, pipeline idempotency).
Skipped automatically when the stack is not reachable.

```bash
# From repo root — stack must be up (docker compose -f deployments/docker-compose.dev.yml up -d)
poetry run pytest tests/integration -q    # ~12 min, 93 tests
```

> ⚠️ The integration suite is **not** part of the per-service loop: the root
> `pyproject.toml` restricts `testpaths` to `services/*/tests` and
> `packages/*/tests`, so it never runs in the CI unit-test matrix. Run it
> explicitly after touching auth/gateway/billing/pipeline code.

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
| Integration (live dockerized stack) | ✅ `tests/integration/` — 93 tests, run explicitly (see §2b) |
| E2E (browser) | ❌ Playwright scripts exist but not run in CI |
| Contract tests | ❌ Pact not wired |
| Load tests | ❌ k6/Locust not written |

## 🚨 Troubleshooting

- **`fixture 'mocker' not found`** — `pip install pytest-mock` into the venv.
- **`ModuleNotFoundError: No module named 'app'`** — you're not in the service dir; `cd services/<svc>` first.
- **`app.main` / `app.models` resolves to the wrong service** — running a combined sweep from the repo root; use the per-service loop above.
- **Tests pass locally, fail in CI** — CI installs into a fresh service venv (`poetry install --with dev`); mirror with `poetry install` inside the service dir.

## 📊 Test Stats (Aug 17, 2026)

| Suite | Tests |
|-------|-------|
| auth-service | 131 |
| content-service | 88 |
| streaming-service | 80 |
| analytics-service | 65 |
| billing-service | 61 (1 known pre-existing failure) |
| admin-service | 61 |
| user-service | 51 |
| media-pipeline | 46 |
| search-service | 46 |
| api-gateway | 35 |
| moderation-service | 30 |
| recommendation-service | 26 |
| notification-service | 24 |
| creators-service | 18 |
| uploads-service | 13 |
| packages/sdk | 98 |
| **Backend unit total** | **780** |
| **tests/integration (live stack)** | **87** |
| apps/web (vitest) | 43 |

---

**Last updated**: August 17, 2026
