# Wildframe

Wildframe is a FastAPI microservices OTT streaming platform with a Next.js frontend, Docker images, Helm/Kubernetes deployment, and GitHub Actions CI/CD.

> **Status: active development. Not production-ready.**

## Current architecture

- **15 backend services:** API gateway, auth, users, content, streaming, search, recommendations, billing, analytics, notifications, media pipeline, creators, moderation, uploads, and admin.
- **Frontend:** Next.js + TypeScript in `apps/web`.
- **Shared SDK:** `packages/sdk` for events and observability.
- **Infrastructure:** Docker, Kubernetes, Helm, Terraform, PostgreSQL, Redis, Kafka, Elasticsearch, Prometheus, Grafana, Loki, and Jaeger.

## CI/CD

The authoritative pipeline is `.github/workflows/ci-cd.yml`.

### Pull requests

CI runs:

1. Ruff, Black, and mypy.
2. Helm lint plus rendering of default, staging, and production values.
3. All 15 backend service test suites.
4. SDK tests.
5. Frontend lint, type-check, tests, and production build.
6. Docker build smoke tests for **all 15 backend images and the frontend image**.
7. Trivy and Semgrep security scanning.

Test and type-check failures are blocking. The workflow does not intentionally use `continue-on-error` or `|| true` for validation steps.

### Image publishing

Pushes to `main` and `develop` publish every backend service image and the frontend image to GHCR. Images are tagged with the commit SHA and branch reference. Deployments use the immutable SHA tag rather than the Helm chart's `appVersion`.

### Kubernetes deployment

- `develop` → staging EKS cluster.
- `main` → production EKS cluster.
- Helm uses `--atomic` and waits for every service deployment to become ready.
- Deployment verification checks `/health` for every backend service from inside the cluster.
- AWS authentication uses GitHub Actions OIDC; long-lived AWS access keys are not used by the deployment workflow.
- GHCR pull credentials are provisioned into the target namespace by the deployment job.

Production and staging deployment require GitHub environment secrets:

- `AWS_DEPLOY_ROLE_ARN` — IAM role trusted by GitHub's OIDC provider.
- `WILDFRAME_JWT_SECRET` — runtime JWT signing secret.
- `WILDFRAME_POSTGRES_PASSWORD` — database password matching the target PostgreSQL deployment.

Production should also use GitHub Environment protection rules for human approval before deployment.

## Docker

Every backend service has its own Dockerfile under `services/<service>/Dockerfile`. The frontend uses `apps/web/Dockerfile`.

Build an individual backend image locally:

```bash
# Build one service from the repository root.
docker build -f services/auth-service/Dockerfile -t wildframe/auth-service:dev .
```

Build the frontend:

```bash
# Build the Next.js production image from the repository root.
docker build -f apps/web/Dockerfile -t wildframe/web:dev .
```

## Local development

Prerequisites:

- Docker and Docker Compose
- Python 3.13
- Node.js 20+
- Poetry 1.8.3

Start the development stack:

```bash
# Start local infrastructure and application containers.
docker compose -f deployments/docker-compose.dev.yml up -d
```

For service-specific development, see `docs/DEVELOPMENT.md` and `docs/QUICKSTART.md`.

## Testing

The CI workflow is the primary reproducible validation environment. For local testing, see:

- `docs/TEST_GUIDE.md`
- `TESTING_GUIDE.md`
- `HOW_TO_RUN_TESTS.md`

Backend unit/route tests run per service (a combined `pytest services/` sweep
from the repo root breaks on shadowed `app.*` imports):

```bash
for svc in services/*/; do
  (cd "$svc" && pytest tests --asyncio-mode=auto) || exit 1
done
```

The repo also ships a live-stack integration suite (`tests/integration/`, 87
tests) that exercises the full HTTPS stack — auth token lifecycle, gateway
rate limiting, cross-service authorization/audience verification, billing
webhook idempotency, health/readiness, and pipeline idempotency. It needs the
compose stack up (skips itself when down) and is intentionally excluded from
the CI unit matrix:

```bash
poetry run pytest tests/integration -q    # ~12 min
```

Avoid treating old completion reports as current test evidence. CI results from the current commit are authoritative.

## Documentation source of truth

The repository contains several historical completion summaries and overlapping quick-start documents. They are retained for project history, but they should not be interpreted as a current release declaration.

For current information, start with:

- `README.md` — current project status and architecture.
- `STATUS.md` — current implementation status.
- `DOCS_INDEX.md` — index of every `.md` file in the repo with summaries.
- `docs/INDEX.md` — documentation index.
- `docs/DEPLOYMENT_GUIDE.md` — deployment architecture and requirements.
- `docs/OPERATIONS.md` — operational procedures.
- `SECURITY.md` — vulnerability reporting policy.
- `PROJECT_MEMORY/` — engineering backlog, bugs, risks, and technical debt.

## Known production gaps

Wildframe is not production-ready yet. Major remaining work includes:

- production secrets and database credential lifecycle;
- load and capacity testing;
- DRM for protected media;
- complete observability sink/dashboard wiring;
- production ingress, TLS, DNS, CDN, and external media delivery;
- disaster recovery, backups, restore testing, and operational runbooks;
- payment-provider production configuration and compliance work;
- final AWS/EKS hardening and environment-specific configuration.

## Security

Do not report vulnerabilities through public GitHub issues or pull requests. See `SECURITY.md` for the private reporting process.

## License

Proprietary — Wildframe Platform.
