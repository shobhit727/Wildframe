# How to Run Tests

Wildframe uses service-local pytest suites, shared SDK tests, frontend Vitest tests, Playwright E2E tests, and a live cross-service integration suite.

## Backend

Run all backend services from the repository root:

```bash
./run_tests.sh
```

Run one service:

```bash
cd services/auth-service
python -m pytest tests --asyncio-mode=auto
```

Run the SDK:

```bash
PYTHONPATH="$PWD/packages/sdk" python -m pytest packages/sdk --asyncio-mode=auto -q
```

Do not run `pytest services/` from the repository root. Each backend service has a top-level `app` package, so combined root-level collection can resolve the wrong service.

## Live integration/security suite

With the development stack running:

```bash
poetry run pytest tests/integration -q
```

This suite is also executed by the dedicated CI integration job and is required for the workflow to pass.

## Frontend

From `apps/web/`:

```bash
npm test -- --run
npm run type-check
npm run lint
npm run build
npx playwright test
```

## Coverage

Backend:

```bash
cd services/auth-service
python -m pytest tests --cov=app --cov-report=term-missing
```

Frontend:

```bash
cd apps/web
npx vitest run --coverage
```

The executable CI source of truth is `.github/workflows/ci-cd.yml`.
