# 🧪 Wildframe Testing Guide

Comprehensive reference for writing, running, and debugging tests across the Wildframe platform.

**Last Updated**: August 17, 2026

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
| Coverage | **pytest-cov** | Track line + branch coverage |
| Frontend unit | **Vitest** | Fast, ESM-native, Jest-compatible API |
| Frontend E2E | **Playwright** | Scripts exist, not yet run in CI |
| Load | **k6** | Optional, not yet written |

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

| Service type | Target |
|---|---|
| Auth, Billing | 85%+ |
| User, Admin, Streaming | 80%+ |
| Content, Search, Recommendation, Analytics, Notification, Media Pipeline | 75%+ |
| API Gateway | 70%+ |

Coverage **must not drop** in a PR. CI currently runs coverage per service
(`--cov=app`) but does **not** fail the build below a threshold — thresholds
are aspirational; add `--cov-fail-under` once suites stabilize.

---

## Integration & E2E

### Live-stack integration suite (`tests/integration/`, repo root)

Since Aug 2026 the repo ships a cross-service integration suite that runs
against the **real dockerized stack** through the Caddy proxy (HTTPS). 87
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

### Full platform E2E

The dockerized stack (`deployments/docker-compose.dev.yml`) is the E2E target:
boot it, then probe endpoints per [API_DOCUMENTATION.md](API_DOCUMENTATION.md).
The Aug 9, 2026 security sweep used exactly this flow (see
[AUDIT_FIX_SUMMARY.md](../AUDIT_FIX_SUMMARY.md)).

### Smoke test (after deployment)

```bash
curl https://localhost:8000/health
curl -X POST https://localhost:8000/auth/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@wildframe.com","password":"DemoPass123!"}'
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

**`asyncpg.exceptions.UndefinedTableError`** — the live dev DB is missing a column/table the models expect (no migration framework; drift is repaired by hand). Verify against the running stack, e.g.:
```bash
docker compose -f deployments/docker-compose.dev.yml exec postgres \
  psql -U wildframe -d <db_name> -c "\d <table>"
```
then `ALTER TABLE` as needed (see docs/OPERATIONS.md "Database Migrations").

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
