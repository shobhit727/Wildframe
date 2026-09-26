# Wildframe Documentation Index

Wildframe is an actively developed 15-service OTT platform. The repository does not declare the platform production-ready.

## Current source of truth

- README.md — project overview, architecture, deployment requirements, known limitations
- STATUS.md — current implementation and operational status
- SECURITY.md — security reporting policy
- docs/INDEX.md — documentation map
- docs/TEST_GUIDE.md — test strategy and execution
- docs/DEPLOYMENT_GUIDE.md — CI/CD, Helm, and EKS deployment path

Historical audit/completion reports are retained for traceability only.

## Repository layout

```text
services/
  api-gateway/
  auth-service/
  user-service/
  content-service/
  streaming-service/
  search-service/
  recommendation-service/
  billing-service/
  analytics-service/
  notification-service/
  media-pipeline/
  creators-service/
  moderation-service/
  uploads-service/
  admin-service/
packages/sdk/
apps/web/
deployments/
infrastructure/
tests/integration/
scripts/
.github/workflows/
```

Every backend service keeps its runnable test suite in `services/<service>/tests/`.

## Common commands

```bash
# Start the development stack.
./start_services.sh

# Run backend service tests.
./run_tests.sh

# Run backend and SDK tests.
./run_all_tests.sh

# Run the live integration/security suite with the stack running.
poetry run pytest tests/integration -q
```

Production runtime secrets are external to Git and are provisioned into the Kubernetes `wildframe-runtime` Secret by the deployment workflow.

DRM, production Kafka ACLs/TLS policy, managed stateful-service topology, and some provider/compliance integrations remain deployment-level work; see STATUS.md.
