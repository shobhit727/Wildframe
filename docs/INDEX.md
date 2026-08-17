# Wildframe Documentation Index

**Current status:** active development; not production-ready.

This index points to the documentation that should be used for current engineering decisions. Historical completion reports remain in the repository but are not release declarations.

## Start here

- [`README.md`](../README.md) — current architecture, CI/CD, deployment requirements, and production gaps.
- [`STATUS.md`](../STATUS.md) — current implementation and deployment status.
- [`SECURITY.md`](../SECURITY.md) — vulnerability reporting policy.
- [`DOCS_INDEX.md`](../DOCS_INDEX.md) — index of **every** `.md` file in the repo (current and historical) with summaries.

## Development

- [`DEVELOPMENT.md`](DEVELOPMENT.md) — development workflow.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — contribution conventions.
- [`QUICKSTART.md`](QUICKSTART.md) — local setup.
- [`TEST_GUIDE.md`](TEST_GUIDE.md) — test strategy and execution.
- [`HOW_TO_RUN_TESTS.md`](HOW_TO_RUN_TESTS.md) — test commands.

## Architecture

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — backend architecture.
- [`FRONTEND_ARCHITECTURE.md`](FRONTEND_ARCHITECTURE.md) — frontend architecture.
- [`SERVICE_ARCHITECTURE_PATTERN.md`](SERVICE_ARCHITECTURE_PATTERN.md) — service structure.
- [`DATABASE_SCHEMA.md`](DATABASE_SCHEMA.md) — database design.
- [`API_DOCUMENTATION.md`](API_DOCUMENTATION.md) — API reference.

## Operations and deployment

- [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) — current GitHub Actions, GHCR, Helm, and EKS deployment path.
- [`OPERATIONS.md`](OPERATIONS.md) — operational procedures.
- [`MONITORING.md`](MONITORING.md) — monitoring and observability.
- [`DRM_SCOPE.md`](DRM_SCOPE.md) — DRM scope and remaining work.

## Reference

- [`GLOSSARY.md`](GLOSSARY.md) — terminology.
- [`PRODUCT_VISION.md`](PRODUCT_VISION.md) — product direction.
- [`OVERVIEW.md`](OVERVIEW.md) — project overview.
- [`WHATS_INCLUDED.md`](WHATS_INCLUDED.md) — feature inventory.
- [`DOCUMENTATION_GUIDE.md`](DOCUMENTATION_GUIDE.md) — documentation conventions.

## CI/CD source of truth

The executable CI/CD definition is:

```text
.github/workflows/ci-cd.yml
```

It validates all 15 backend services, the frontend, Helm rendering, Docker builds, and security scans. Pushes to `develop` and `main` publish commit-SHA-tagged images; deployment then uses that same immutable SHA.

## Current platform inventory

```text
Wildframe OTT Platform
├── 15 FastAPI backend services
├── Next.js frontend
├── Shared Python SDK
├── PostgreSQL
├── Redis
├── Kafka
├── Elasticsearch
└── Prometheus / Grafana / Jaeger / Loki
```

## Documentation rule

Do not use documents named `*_COMPLETE.md`, `*COMPLETION*.md`, `FINAL_*REPORT.md`, or similar historical reports as evidence that the platform is production-ready. Verify the current repository state and GitHub Actions results instead.
