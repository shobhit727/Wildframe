# How to Run Tests

**Current State**: Unit/route tests for all 15 microservices (**6,943** collected), the shared SDK (**795**), the frontend (**866** vitest across 46 files), Playwright E2E (**122** across 10 files / 15 routes), a contract suite (`tests/contract`, **84** tests across 12 files), and a live-stack integration suite (`tests/integration/`, 110 tests across 8 modules). The contract suite is green. The integration suite needs the dockerized stack running and skips itself otherwise.

> **Every number in this file was re-measured on 2026-10-14** against
> `audit/fix-open-github-issues`. Counts move every time a test lands — the
> Test Stats table below is dated for exactly that reason, and it also gives
> the command that regenerates each row. Do not copy a count from here into
> another document; link to this file instead.
>
> **One measurement caveat.** `auth-service` reports 877 collected *excluding*
> `tests/test_telemetry.py` (15 tests), which cannot be imported in an
> environment without `opentelemetry-exporter-otlp-proto-grpc` — a declared
> dependency in `services/auth-service/pyproject.toml` that CI installs. On a
> correctly provisioned machine that service collects 892. Every other figure
> here was collected with the SDK on `PYTHONPATH`, matching CI.

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
  (cd "$svc" && PYTHONPATH="$PWD" pytest tests --asyncio-mode=auto) || exit 1
done

# One service
cd services/auth-service && pytest tests --asyncio-mode=auto

# One file / one test
cd services/streaming-service
pytest tests/test_routes.py -k "end_playback_session" -v
```

> ⚠️ **`PYTHONPATH="$PWD"` is required, not optional.** CI sets it
> (`.github/workflows/ci-cd.yml`), and `auth-service`, `content-service` and
> `streaming-service` need it because their `tests/` directory has no
> `__init__.py`. Without it pytest resolves imports against the wrong path.
>
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
# Frontend/backend route contract + supply-chain contract tests (84 tests, 12 files)
pytest tests/contract -q

# Supply-chain guard (action pinning + scanner suppressions) (4 tests)
pytest tests/test_supply_chain_guard.py -q
```

`tests/contract/` is broader than route drift. It also enforces
`test_no_shared_secret_jwt_verification.py` (no `jwt.decode` against a shared
secret), `test_no_committed_private_key.py`, `test_archive_sandbox.py`, the
compose-smoke contract, the Prometheus scrape targets, the mypy policy findings,
and the Caddy/plaintext-listener invariant. All **84 pass**.

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

Coverage details, the 85% CI floor, and the coverage command are in §6.

## 4️⃣ Frontend Tests

```bash
cd apps/web
npx vitest run          # run once — 866 tests / 46 files (CI uses `npm test -- --run`)
npx vitest              # watch mode
npm run type-check      # tsc --noEmit
npm run lint            # eslint . (Next 16 removed `next lint`)
npm run build           # production build
```

## 5️⃣ E2E Tests

Playwright covers **122** tests across **10** spec files and **15** routes
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

CI enforces a floor of **85%** per service
(`--cov-fail-under="${COVERAGE_FLOOR:-85}"`,
`.github/workflows/ci-cd.yml`) and fails the build below it. Override the floor
for a one-off local run by exporting `COVERAGE_FLOOR`:

```bash
cd services/auth-service
COVERAGE_FLOOR=85 pytest tests --cov=app --cov-report=term-missing:skip-covered \
  --cov-fail-under="${COVERAGE_FLOOR:-85}" --asyncio-mode=auto
```

## ⚠️ Not Available (yet)

| Test Type | Status |
|-----------|--------|
| Unit/route tests | ✅ 15 services (6,943) + SDK (795) + frontend (866) — see the auth-service caveat above |
| Integration (live dockerized stack) | ✅ `tests/integration/` — 110 tests across 8 modules, run explicitly (see §2b) |
| Contract / supply-chain contract | ✅ `tests/contract` — 84 tests across 12 files, all passing, blocking in the `backend-route-contract` job (see §2a) |
| Supply-chain guard | ✅ `tests/test_supply_chain_guard.py` — 4 tests, blocking in the `supply-chain-guard` job |
| E2E (browser) | ✅ Playwright — 122 tests / 10 files / 15 routes, blocking in the `frontend-e2e` job (see §5) |
| Load tests | ⚠️ Locust harness exists at `load-tests/locustfile.py` and runs locally, but is **not** wired into CI |

## 🚨 Troubleshooting

- **`fixture 'mocker' not found`** — `pip install pytest-mock` into the venv.
- **`ModuleNotFoundError: No module named 'app'`** — you're not in the service dir; `cd services/<svc>` first.
- **`app.main` / `app.models` resolves to the wrong service** — running a combined sweep from the repo root; use the per-service loop above.
- **Tests pass locally, fail in CI** — CI installs into a fresh service venv (`poetry install --with dev`); mirror with `poetry install` inside the service dir.

## 📊 Test Stats

Measured **2026-10-14** on `audit/fix-open-github-issues`.

### Backend services — `pytest tests --collect-only -q` per service

Run from each service directory with the SDK on `PYTHONPATH` (the CI shape);
collection counts equal run counts, verified against a full run of
`notification-service` (302 collected / 302 passed).

| Service | Tests |
|---------|-------|
| billing-service | 895 |
| auth-service | 877 † |
| admin-service | 495 |
| media-pipeline | 478 |
| user-service | 427 |
| uploads-service | 418 |
| content-service | 446 |
| api-gateway | 416 |
| search-service | 398 |
| analytics-service | 381 |
| moderation-service | 377 |
| streaming-service | 376 |
| creators-service | 342 |
| recommendation-service | 315 |
| notification-service | 302 |
| **Backend services total (15)** | **6,943** |

† `auth-service` excludes `tests/test_telemetry.py` (15 tests), which needs
`opentelemetry-exporter-otlp-proto-grpc` — declared in the service's
`pyproject.toml` and installed by CI, absent from the environment this was
measured in. The true CI-visible total is **6,958**.

### Other suites

| Suite | Tests | How measured |
|-------|-------|--------------|
| `packages/sdk` (`tests/` + `wildframe_compliance/tests/`) | 795 | `pytest -c pyproject.toml` with `packages/sdk` on `PYTHONPATH` |
| `tests/contract` | 84 (12 files) | `pytest tests/contract -q` |
| `tests/test_supply_chain_guard.py` | 4 | `pytest tests/test_supply_chain_guard.py -q` |
| `tests/integration` (live stack, not in CI) | 110 (8 modules) | `pytest tests/integration --collect-only -q` |
| `apps/web` — vitest | 866 (46 files) | `npx vitest run` |
| `apps/web` — Playwright | 122 (10 files, 15 routes) | `npx playwright test --list` |

`packages/sdk/wildframe_events/tests/` contains only `__init__.py`, so it
contributes 0. CI's `backend-sdk-test` job runs
`wildframe_compliance/tests/`, `wildframe_events/tests/` and
`wildframe_observability/tests/`; `packages/sdk/tests/` is a separate step.

Per-service coverage is enforced at an **85% floor** in CI (`COVERAGE_FLOOR`).

---

**Last updated**: 2026-10-14 — all counts re-measured against `audit/fix-open-github-issues`
