# How to Run Tests

**Current State**: Unit/route tests for all 15 microservices (**6,217** tests), the shared SDK (**694**), the frontend (**805** vitest across 44 files), Playwright E2E (**119** across 9 files / 15 routes), a contract suite (`tests/contract`, **24** tests), and a live-stack integration suite (`tests/integration/`, 110 tests). The 15 service suites, the SDK, and the frontend suite are green. The contract suite has **one known failure** — see §2a. The integration suite needs the dockerized stack running and skips itself otherwise.

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
# All 15 services
for svc in services/*/; do
  (cd "$svc" && pytest tests --asyncio-mode=auto) || exit 1
done

# One service
cd services/auth-service && pytest tests --asyncio-mode=auto

# One file / one test
cd services/streaming-service
pytest tests/test_routes.py -k "end_playback_session" -v
```

### SDK

The SDK tests span four directories and need the repo root's `pyproject.toml`,
so run them from the repo root with `packages/sdk` on `PYTHONPATH`. The
`wildframe_compliance` package resolves to `packages/sdk/wildframe_compliance`
— the 12-line repo-root stub that used to shadow it was deleted, so there is
no repo-root `wildframe_compliance/` package to import from.

```bash
PYTHONPATH="$PWD/packages/sdk" python -m pytest -c pyproject.toml \
  packages/sdk/tests/ \
  packages/sdk/wildframe_compliance/tests/ \
  packages/sdk/wildframe_events/tests/ \
  packages/sdk/wildframe_observability/tests/ \
  --asyncio-mode=auto
```

## 2a️⃣ Contract and Supply-Chain Tests

Both run from the repo root and both are **blocking CI jobs** (`backend-route-contract`
and `supply-chain-guard` respectively).

```bash
# Frontend/backend route drift (24 tests)
pytest tests/contract -q

# Supply-chain guard (action pinning + scanner suppressions) (4 tests)
pytest tests/test_supply_chain_guard.py -q
```

> ⚠️ **Known failure.** `test_frontend_paths_resolve_to_backend_routes` fails
> with ~26 unresolved paths. The cause is not route drift: every unresolved
> literal comes from mock URL strings inside
> `apps/web/src/api/__tests__/admin.test.ts` and
> `apps/web/src/api/__tests__/client.data.test.ts`, and
> `tests/contract/test_route_drift.py:152` globs **all** of `apps/web/src`
> without excluding `__tests__`. The same scan over production frontend source
> (excluding `__tests__`) reports **0** unresolved paths, so there is no real
> frontend↔backend drift. The fix belongs in the contract test's file filter,
> not in the frontend.

## 2b️⃣ Run the Live-Stack Integration Suite

Cross-service integration tests live at `tests/integration/` (repo root). They
exercise the real HTTPS stack through the Caddy proxy (auth token lifecycle,
gateway rate limiting, cross-service authorization, billing webhook
idempotency, contract schemas, health/readiness, pipeline idempotency).
Skipped automatically when the stack is not reachable.

```bash
# From repo root — stack must be up (docker compose -f deployments/docker-compose.dev.yml up -d)
poetry run pytest tests/integration -q    # ~16 min, 110 tests
```

> ⚠️ The integration suite is **not** part of the per-service loop: the root
> `pyproject.toml` restricts `testpaths` to `services/*/tests` and
> `packages/*/tests`, so it never runs in the CI unit-test matrix. Run it
> explicitly after touching auth/gateway/billing/pipeline code.

## 3️⃣ With Coverage

Coverage details, the 95% CI floor, and the coverage command are in §6.

## 4️⃣ Frontend Tests

```bash
cd apps/web
npx vitest run          # run once — 805 tests / 44 files (CI uses `npm test -- --run`)
npx vitest              # watch mode
npm run type-check      # tsc --noEmit
npm run lint            # eslint . (Next 16 removed `next lint`)
npm run build           # production build
```

## 5️⃣ E2E Tests

Playwright covers **119** tests across **9** spec files and **15** routes
(`/`, `/login`, `/signup`, `/browse`, `/watch/[id]`, `/account`, `/my-list`,
`/billing`, `/creator`, `/admin`, `/admin/users`, `/admin/alerts`,
`/admin/audit`, `/admin/config`, `/admin/flags`). The `frontend-e2e` CI job is
**blocking**.

```bash
cd apps/web
npx playwright install --with-deps chromium   # first run only
npx playwright test
```

The Playwright config invokes `scripts/generate-dev-certs.sh` itself, so dev
TLS material is produced automatically. To generate it up front instead:

```bash
bash scripts/generate-dev-certs.sh
```

## 6️⃣ Coverage

Per-service coverage is 97–99%. CI enforces a **95% floor** and fails the build
below it:

```bash
cd services/auth-service
pytest tests --cov=app --cov-report=term-missing:skip-covered \
  --cov-fail-under=95 --asyncio-mode=auto
```

## ⚠️ Not Available (yet)

| Test Type | Status |
|-----------|--------|
| Unit/route tests | ✅ 15 services (6,217) + SDK (694) + frontend (805) |
| Integration (live dockerized stack) | ✅ `tests/integration/` — 110 tests, run explicitly (see §2b) |
| Contract / route drift | 🟡 `tests/contract` — 24 tests, blocking in the `backend-route-contract` job. 23 pass; 1 known failure from the scanner reading `__tests__` fixtures (see §2a) |
| Supply-chain guard | ✅ `tests/test_supply_chain_guard.py` — 4 tests, blocking in the `supply-chain-guard` job |
| E2E (browser) | ✅ Playwright — 119 tests / 9 files / 15 routes, blocking in the `frontend-e2e` job (see §5) |
| Load tests | ❌ Locust harness exists at `load-tests/`, not yet run in CI |

## 🚨 Troubleshooting

- **`fixture 'mocker' not found`** — `pip install pytest-mock` into the venv.
- **`ModuleNotFoundError: No module named 'app'`** — you're not in the service dir; `cd services/<svc>` first.
- **`app.main` / `app.models` resolves to the wrong service** — running a combined sweep from the repo root; use the per-service loop above.
- **Tests pass locally, fail in CI** — CI installs into a fresh service venv (`poetry install --with dev`); mirror with `poetry install` inside the service dir.

## 📊 Test Stats

| Suite | Tests |
|-------|-------|
| auth-service | 766 |
| billing-service | 845 |
| admin-service | 483 |
| media-pipeline | 440 |
| content-service | 390 |
| user-service | 375 |
| uploads-service | 376 |
| streaming-service | 359 |
| analytics-service | 346 |
| moderation-service | 318 |
| search-service | 303 |
| notification-service | 289 |
| creators-service | 297 |
| recommendation-service | 275 |
| api-gateway | 354 |
| **Backend services total (15)** | **6,217** |
| packages/sdk (incl. compliance, events, observability) | 694 |
| tests/contract (route drift) | 24 |
| tests/test_supply_chain_guard.py | 4 |
| **tests/integration (live stack, not in CI)** | **110** |
| apps/web — vitest (44 files) | 805 |
| apps/web — Playwright (9 files, 15 routes) | 119 |

Per-service coverage: **97–99%**, with a **95% floor** enforced in CI.

---

**Last updated**: September 27, 2026 — all counts re-measured against `audit/fix-open-github-issues`
