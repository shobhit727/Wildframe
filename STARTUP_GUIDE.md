# Wildframe Development Startup

This guide uses the current repository layout and contains no machine-specific paths.

## Prerequisites

Docker Engine/Desktop, Docker Compose v2, Node.js, npm, Python 3.11+, and Poetry are expected.

## Start the backend stack

From the repository root:

```bash
./scripts/generate-dev-certs.sh
docker compose -f deployments/docker-compose.dev.yml up --build -d
docker compose -f deployments/docker-compose.dev.yml ps
```

For the same workflow through the helper:

```bash
./start_services.sh
```

The API gateway is exposed at `https://localhost:8000`. The frontend is available at `https://localhost:3000` when the web container is started.

## Start the frontend separately

```bash
cd apps/web
npm install
npm run dev
```

The frontend uses configured `NEXT_PUBLIC_APP_URL` and `NEXT_PUBLIC_API_URL` values; it does not require a hard-coded LAN IP.

## Run tests

```bash
./run_tests.sh
./run_all_tests.sh
poetry run pytest tests/integration -q
```

The CI workflow runs service tests, SDK tests, route-contract tests, frontend checks, Playwright E2E, security scans, Docker smoke builds, and the live integration/security suite.

## Development TLS

`scripts/generate-dev-certs.sh` generates local-only certificates under `apps/web/certificates/`. Private keys are not committed.
