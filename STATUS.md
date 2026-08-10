# Wildframe Status

**Last reviewed:** August 2026

## Overall

**Active development. Not production-ready.**

The application and CI foundations are substantially implemented, but production deployment still requires AWS/EKS configuration, runtime secrets, operational hardening, load testing, DRM, and production observability work.

## Services

All 15 backend services have Dockerfiles, Helm deployment definitions, health endpoints, and CI test jobs:

- api-gateway
- auth-service
- user-service
- content-service
- streaming-service
- search-service
- recommendation-service
- billing-service
- analytics-service
- notification-service
- media-pipeline
- creators-service
- moderation-service
- uploads-service
- admin-service

## CI status

The current CI/CD workflow is designed to validate:

- Ruff, Black, and mypy.
- Helm lint and rendering for default, staging, and production configuration.
- All 15 backend test matrices.
- SDK tests.
- Frontend lint, type-check, tests, and production build.
- Docker builds for all 15 backend services and the frontend.
- Trivy vulnerability scanning and Semgrep SAST.

Validation failures are intended to block the workflow. Deployment jobs run only after the relevant build and validation jobs succeed.

## Container publishing

Pushes to `develop` and `main` publish immutable commit-SHA-tagged images to GHCR for every backend service and the frontend.

Kubernetes deployment selects the exact SHA tag produced by the same GitHub Actions run. It does not rely on a mutable `latest` tag or the Helm chart's `appVersion`.

## Kubernetes deployment

### Staging

`develop` deploys to the `wildframe-staging` namespace in the `wildframe-staging` EKS cluster.

### Production

`main` deploys to the `wildframe-production` namespace in the `wildframe-production` EKS cluster.

Production should be protected by a GitHub Environment approval rule.

Deployment validation waits for all 15 backend deployments and then performs `/health` checks against every backend service from inside the cluster.

## Required deployment configuration

GitHub Environment secrets required by the deployment workflow:

| Secret | Purpose |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | AWS IAM role assumed through GitHub OIDC |
| `WILDFRAME_JWT_SECRET` | JWT signing secret injected through Kubernetes Secret |
| `WILDFRAME_POSTGRES_PASSWORD` | PostgreSQL password injected through Kubernetes Secret |

The EKS clusters and their supporting infrastructure must already exist. This repository does not claim to create production clusters automatically from the application deployment workflow.

## Remaining production work

- [ ] Complete AWS/EKS environment configuration.
- [ ] Configure GitHub Environment protection and approvals.
- [ ] Configure runtime secrets and secret rotation.
- [ ] Validate PostgreSQL, Redis, Kafka, and Elasticsearch production topology.
- [ ] Add load/capacity tests and SLOs.
- [ ] Complete production observability sinks, dashboards, and alert routing.
- [ ] Add backups, disaster recovery, and restore testing.
- [ ] Complete ingress, TLS, DNS, CDN, and media delivery configuration.
- [ ] Implement production DRM.
- [ ] Complete payment-provider production configuration and compliance.

## Documentation rule

Historical files such as completion summaries and old quick-start variants are retained as project history. They are not release declarations.

Use `README.md`, this file, `docs/INDEX.md`, `docs/DEPLOYMENT_GUIDE.md`, `docs/OPERATIONS.md`, and `SECURITY.md` as the current operational documentation.
