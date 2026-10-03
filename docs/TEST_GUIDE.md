# 🧪 Wildframe Testing Guide

Comprehensive reference for writing, running, and debugging tests across the Wildframe platform.

**Last Updated**: September 27, 2026

---

## Table of Contents

1. [Test Stack](#test-stack)
2. [Test Layout](#test-layout)
3. [Running Tests](#running-tests)
4. [Writing Tests](#writing-tests)
5. [Fixtures & Mocks](#fixtures--mocks)
6. [Coverage](#coverage)
7. [Integration & E2E](#integration--e2e)
8. [Troubleshooting](#troubleshooting)

---

## Test Stack

| Layer | Tool | Why |
|---|---|---|
| Backend unit / route | **pytest** + **pytest-asyncio** (`--asyncio-mode=auto`) | De facto Python test framework, async-native |
| HTTPX | **httpx** (ASGITransport / TestClient) | In-process app testing |
| Mocking | **unittest.mock** (`AsyncMock`, `MagicMock`, `patch`) + **pytest-mock** (`mocker` fixture) | Stub external dependencies |
| Coverage | **pytest-cov** | Track line + branch coverage; CI enforces a 95% floor |
| Frontend unit | **Vitest** | Fast, ESM-native, Jest-compatible API |
| Frontend E2E | **Playwright** | 119 tests across 9 spec files / 15 routes — blocking in the `frontend-e2e` CI job |
| Contract | **pytest** + static analysis | `tests/contract/` — 24 frontend↔backend route drift tests |
| Load | **Locust** | Harness at `load-tests/`, not yet run in CI |

---

## Test Layout

Every backend service follows the same structure (tests live at the **service
root**, not inside `app/`):

```
services/<service>/
├── app/
│   ├── api/                 # routes
│   ├── core/                # config, security
│   ├── models/              # SQLAlchemy models
│   ├── repositories/        # data access
│   └── services/            # business logic
├── tests/
│   ├── conftest.py          # shared fixtures
│   ├── test_<area>.py       # route/service unit tests
│   └── test_<area>_edges.py # edge-case coverage
├── Dockerfile
└── pyproject.toml
```

Test files **must** start with `test_`. Pytest auto-discovers them.

> ⚠️ Every service defines its own top-level `app` package, so **always run
> pytest from inside the service directory** — a combined `pytest services/`
> sweep from the repo root breaks on shadowed `app.*` imports.

---

## Running Tests

### All services (from repo root — per-service, never a combined sweep)

```bash
for svc in services/*/; do
  (cd "$svc" && pytest tests --asyncio-mode=auto) || exit 1
done
```

### Shared SDK (from repo root)

The SDK tests live in four directories and need the repo root's
`pyproject.toml`, so run them with `packages/sdk` on `PYTHONPATH`:

```bash
PYTHONPATH="$PWD/packages/sdk" python -m pytest -c pyproject.toml \
  packages/sdk/tests/ \
  packages/sdk/wildframe_compliance/tests/ \
  packages/sdk/wildframe_events/tests/ \
  packages/sdk/wildframe_observability/tests/ \
  --asyncio-mode=auto
```

### Single service

```bash
cd services/auth-service
python3 -m pytest tests/ --asyncio-mode=auto
```

### Single file

```bash
python3 -m pytest tests/test_auth_service.py -v
```

### Single test by name

```bash
python3 -m pytest tests/test_auth_service.py -v -k "test_register_success"
```

### Stop on first failure (faster feedback)

```bash
python3 -m pytest tests/ -x
```

### Run only the previously failed tests

```bash
python3 -m pytest tests/ --lf
```

---

## Writing Tests

### Test naming

- File: `test_<unit>.py`
- Function: `test_<behavior>_<expected_outcome>`

Examples:
- `test_register_user_returns_201`
- `test_login_with_invalid_password_raises_401`
- `test_get_subscription_handles_missing_user`

### Async tests

All FastAPI service tests are async. Run pytest with `--asyncio-mode=auto`
(no per-test `@pytest.mark.asyncio` decorator needed):

```python
def test_register_user_returns_201(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "a@b.com", "password": "Pass123!"},
    )
    assert response.status_code == 201
```

### Pure unit tests (no I/O)

Test business logic in isolation by injecting mocks. See `services/auth-service/tests/test_auth_service.py` for the canonical pattern.

---

## Fixtures & Mocks

Each service ships with a `conftest.py` exposing reusable fixtures. Common
patterns across suites (names vary by service):

| Fixture | Purpose |
|---|---|
| `client` | FastAPI `TestClient` bound to the app with dependencies overridden |
| `fake_service` / `fake_*_repo` | Pre-built `AsyncMock` repos/service under test |
| `auth_user_id` / `TEST_USER_ID` | Fixed authenticated user UUID; the `get_current_user_id` dependency is overridden with it |
| `override_deps` (autouse) | Registers `app.dependency_overrides` for the suite and clears them after |
| `make_*()` helpers | `MagicMock` factory functions producing realistic model instances |

### Mocks vs. fakes — when to use which

- **Mock** (`AsyncMock`): external services (Kafka, Elasticsearch, payment provider, email).
- **Fake** (in-memory SQLite, lightweight dicts): repositories and DB.
- **Real**: rarely used except for narrow integration suites.

```python
@pytest.fixture
def mock_payment_provider():
    provider = AsyncMock()
    provider.charge.return_value = {"status": "ok", "id": "ch_123"}
    return provider
```

---

## Coverage

### Generate a report

```bash
cd services/auth-service
python3 -m pytest tests/ --cov=app --cov-report=term-missing
```

For an HTML report:

```bash
python3 -m pytest tests/ --cov=app --cov-report=html
open htmlcov/index.html
```

### Target thresholds

Every service currently sits at **97–99%** coverage. CI runs coverage per
service (`--cov=app`) and **fails the build below a 95% floor**
(`--cov-fail-under="${COVERAGE_FLOOR:-95}"`), so coverage must not drop.

```bash
cd services/auth-service
pytest tests --cov=app --cov-report=term-missing:skip-covered \
  --cov-fail-under=95 --asyncio-mode=auto
```

---

## Integration & E2E

### Live-stack integration suite (`tests/integration/`, repo root)

Since Aug 2026 the repo ships a cross-service integration suite that runs
against the **real dockerized stack** through the Caddy proxy (HTTPS). 110
tests across 7 modules + `conftest.py`:

- `test_gateway_auth.py` — edge auth matrix through the gateway (expired /
  wrong-audience / malformed tokens, public vs. protected routes) and the
  gateway rate limiter (429 flood test, run last with drain sleeps).
- `test_auth_token_lifecycle.py` — register → login → refresh → logout /
  token revocation.
- `test_authorization_cross_service.py` — per-service authorization and
  audience verification (auth, content, analytics, billing, creators,
  notification, search, streaming, admin, media-pipeline).
- `test_billing_webhook_idempotency.py` — Stripe webhook: signature
  verification (unsigned → 400), first delivery `handled:true`, replay
  `idempotent:true`, exactly one PAID invoice row.
- `test_contract_schemas.py` — shared response shapes across services.
- `test_health_readiness.py` — `/health` and `/ready` for every service
  (search `/ready` regression).
- `test_pipeline_idempotency.py` — media-pipeline job start/get now require
  a verified JWT; repeated `start` calls are idempotent.

```bash
# From repo root — stack must be up; skips itself if the stack is down
poetry run pytest tests/integration -q    # ~12 min
```

> ⚠️ The integration suite is deliberately **excluded** from the per-service
> loop (root `pyproject.toml` `testpaths` only cover `services/*/tests` and
> `packages/*/tests`), so CI's unit matrix does not run it. It is not
> testcontainers-based; it treats the compose stack as the test target.
> HTTP requests use `verify=False` (self-signed dev certs), and IP-keyed
> requests are paced (≤3 per 60 s window) so the gateway rate limiter does
> not flake the suite.

### Frontend E2E Tests (Playwright)

The `frontend-e2e` CI job runs Playwright against the dockerized stack and is
**blocking**.

**Test Suites** (`apps/web/e2e/`, 9 spec files):
- `auth.spec.ts` — login, signup, protected-route redirects
- `home.spec.ts`, `browse.spec.ts` — landing and catalogue
- `watch.spec.ts` — playback page: movie, series, seasons/episodes
- `account.spec.ts`, `my-list.spec.ts` — account and My List
- `billing.spec.ts` — subscription page
- `creator.spec.ts` — creator pages
- `admin.spec.ts` — admin console and its sub-routes

**Run locally:**
```bash
# Terminal 1: Start the frontend dev server
cd apps/web
npm run dev

# Terminal 2: Run Playwright tests
npx playwright install --with-deps chromium   # first run only
npx playwright test
```

**Run in CI:**
```bash
cd apps/web
npx playwright test --reporter=github
```

**Test count:** **119 tests across 9 spec files**, covering **15 routes**
(`/`, `/login`, `/signup`, `/browse`, `/watch/[id]`, `/account`, `/my-list`,
`/billing`, `/creator`, `/admin`, `/admin/users`, `/admin/alerts`,
`/admin/audit`, `/admin/config`, `/admin/flags`).

**Configuration:** `apps/web/playwright.config.ts`
- Base URL: `https://localhost:3000` (HTTPS with self-signed certs)
- Single browser: Chromium (CI), multi-browser locally
- Web server: Starts `npm run dev` automatically
- Dev TLS material is produced by `scripts/generate-dev-certs.sh`, which the
  config invokes automatically
- HTTPS errors ignored (self-signed certs)
- Timeout: 300s for web server startup

---

### Route Contract Tests

Static analysis test that validates frontend API calls match backend routes:

```bash
pytest tests/contract -q
```

24 tests verifying frontend paths resolve to registered backend routes.

> ⚠️ **One known failure.** `test_frontend_paths_resolve_to_backend_routes`
> currently reports ~26 unresolved paths, all of which are mock URL literals in
> `apps/web/src/api/__tests__/admin.test.ts` and
> `apps/web/src/api/__tests__/client.data.test.ts`. The scan at
> `tests/contract/test_route_drift.py:152` globs all of `apps/web/src` without
> excluding `__tests__`. Excluding test fixtures the scan reports **0**
> unresolved paths, so there is no real route drift — the fix belongs in the
> contract test's file filter.
Known frontend-only paths are documented in `tests/contract/test_route_drift.py`.

### Smoke test (after deployment)

```bash
curl https://localhost:8000/health
curl -X POST https://localhost:8000/auth/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${WILDFRAME_DEMO_EMAIL:-demo@wildframe.com}\",\"password\":\"${WILDFRAME_DEMO_PASSWORD:-<your-demo-password>}\"}"
# password is env-driven; see scripts/seed_demo.py — never use a committed fixed demo password
```

---

## Best Practices

1. **One assertion concept per test.** Split tests when behavior diverges.
2. **Use `parametrize` for table-driven cases.**
   ```python
   @pytest.mark.parametrize("tier,limit", [("free", 1), ("basic", 5), ("premium", 10)])
   def test_stream_quality_by_tier(tier, limit): ...
   ```
3. **Test the unhappy path.** Every endpoint needs tests for 400, 401, 403, 404, 409, 422.
4. **No `time.sleep`.** Use `freezegun` for time, `asyncio.sleep` for awaiting background tasks.
5. **Keep tests deterministic.** Seed random generators, isolate DBs, don't rely on external services.
6. **Don't mock what you own.** If the bug is in your repository, test the real one against a test DB.

---

## Troubleshooting

**`RuntimeError: Event loop is closed`** — run pytest with `--asyncio-mode=auto`.

**`fixture 'mocker' not found`** — install pytest-mock into the venv.

**`ModuleNotFoundError: No module named 'app'` / wrong `app.models` imported** — you ran pytest from outside the service dir (or a combined `pytest services/` sweep). `cd services/<svc>` first.

**`asyncpg.exceptions.UndefinedTableError` / `UndefinedColumnError`** — the live dev DB is missing a table/column the models expect. Re-run the schema bootstrap, which creates missing tables and adds any column the models declare:
```bash
python scripts/init_schemas.py   # prints "added column <table>.<column>" for anything it repaired
#   On a populated table an undeclared add is REFUSED, because the pass cannot tell
#   drift from a column a human deliberately dropped. Re-run with:
#     python scripts/init_schemas.py --allow-add <table>.<column>
#   only after you have confirmed the table is stale. The flag is deliberately not
#   a committed allowlist: a drop belongs to a database, not to the repo.
```
Verify against the running stack, e.g.:
```bash
docker compose -f deployments/docker-compose.dev.yml exec postgres \
  psql -U wildframe -d <db_name> -c "\d <table>"
```
Only fall back to a manual `ALTER TABLE` when the bootstrap reports a column it cannot add (see docs/OPERATIONS.md "Database Migrations").

**Coverage missing lines even though they ran** — The file is loaded via a different path. Check `pyproject.toml`'s `[tool.coverage.run] source` list.

**Test passes locally, fails in CI** — Usually a port collision or missing env var. Mirror CI locally with:
```bash
docker compose -f deployments/docker-compose.dev.yml up -d
```

---

## Related

- [HOW_TO_RUN_TESTS.md](../HOW_TO_RUN_TESTS.md) — Cheat sheet
- [TESTING_GUIDE.md](../TESTING_GUIDE.md) — Manual API testing with curl
- [SERVICE_ARCHITECTURE_PATTERN.md](SERVICE_ARCHITECTURE_PATTERN.md) — Why the test layout looks the way it does
- [FRONTEND_ARCHITECTURE.md](FRONTEND_ARCHITECTURE.md) — Frontend structure and conventions