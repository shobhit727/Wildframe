# 🧪 Wildframe Testing Guide

Comprehensive reference for writing, running, and debugging tests across the Wildframe platform.

**Last Updated**: June 4, 2026

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
| Backend unit / integration | **pytest** + **pytest-asyncio** | De facto Python test framework, async-native |
| HTTPX / async DB | **httpx.AsyncClient**, **aiosqlite** | Real async I/O in tests |
| Mocking | **unittest.mock** (`AsyncMock`, `MagicMock`, `patch`) | Stub external dependencies |
| Coverage | **pytest-cov** | Track line + branch coverage |
| Frontend unit | **Vitest** | Fast, ESM-native, Jest-compatible API |
| Frontend E2E | **Playwright** | Cross-browser, network interception |
| Load | **k6** | Optional, in [load/](../load) scripts |

---

## Test Layout

Every backend service follows the same structure:

```
services/<service>/
├── app/
│   ├── api/                 # routes
│   ├── core/                # config, security
│   ├── models/              # SQLAlchemy models
│   ├── repositories/        # data access
│   ├── services/            # business logic
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py      # shared fixtures
│       ├── test_<area>.py   # unit tests
│       └── test_<area>_integration.py
├── Dockerfile
└── pyproject.toml
```

Test files **must** start with `test_`. Pytest auto-discovers them.

---

## Running Tests

### One-shot script

```bash
# From project root
./run_tests.sh
```

The script iterates over every `services/*` directory and runs the suite with coverage.

### Single service

```bash
cd services/auth-service
python3 -m pytest tests/ -v
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

All FastAPI service tests are async. Mark them with `@pytest.mark.asyncio`:

```python
import pytest

@pytest.mark.asyncio
async def test_register_user_returns_201(async_client):
    response = await async_client.post(
        "/auth/register",
        json={"email": "a@b.com", "password": "Pass123!"},
    )
    assert response.status_code == 201
```

### Pure unit tests (no I/O)

Test business logic in isolation by injecting mocks. See `services/auth-service/tests/test_auth_service.py` for the canonical pattern.

---

## Fixtures & Mocks

Each service ships with a `conftest.py` exposing reusable fixtures:

| Fixture | Purpose |
|---|---|
| `async_client` | `httpx.AsyncClient` bound to the FastAPI app via `ASGITransport` |
| `db_session` | Async SQLAlchemy session against an in-memory SQLite or test DB |
| `mock_repositories` | Pre-built `AsyncMock` repos for the service under test |
| `mock_rate_limiter` | Stub rate limiter allowing all requests |
| `auth_headers` | `{"Authorization": "Bearer <jwt>"}` for a seeded user |
| `seed_user` | Insert a user and return the model instance |

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

Coverage **must not drop** in a PR — CI fails the build below the threshold.

---

## Integration & E2E

### Service-level integration

These tests boot the FastAPI app + a real (test) database. They live in `tests/test_*_integration.py`.

```bash
python3 -m pytest tests/ -m integration -v
```

### Full platform E2E

`run_tests.sh` (or a CI workflow) brings up the whole Docker Compose stack, waits for health, then runs the suites. Useful for catching contract drift between services.

### Smoke test (after deployment)

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke@wildframe.io","password":"Smoke123!"}'
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

**`RuntimeError: Event loop is closed`** — You forgot `@pytest.mark.asyncio` on an async test.

**`asyncpg.exceptions.UndefinedTableError`** — Migrations not applied. Run:
```bash
docker compose -f deployments/docker-compose.dev.yml exec auth-service alembic upgrade head
```

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
