# Wildframe Deployment Guide

## Scope

This document describes the current GitHub Actions → GHCR → AWS EKS deployment path.

Wildframe is **not production-ready by default**. The workflow assumes that the AWS infrastructure, EKS clusters, PostgreSQL, Redis, Kafka, Elasticsearch, DNS/TLS, and required GitHub environments already exist and are correctly configured.

### Dev TLS and Kafka trust material

Production TLS is terminated by your ingress/load balancer and is not managed
by this repository. **Development** TLS is a separate, self-contained path:
`scripts/generate-dev-certs.sh` mints a self-signed cert/key under
`apps/web/certificates/` (never committed) and also emits everything the local
Kafka brokers need to come up with TLS:

- `kafka-keystore.pem` — Kafka PEM keystore (private key and certificate
  concatenated into one file, which is what Kafka's PEM keystore format
  requires)
- `kafka-truststore.pem` — Kafka PEM truststore (the certificate alone)
- `kafka_keystore_password` and `kafka_key_password` — the two broker password
  files. `cp-kafka` reads these from **files**, not environment variables, which
  is why the password files are generated rather than only exported.

The script is idempotent and self-repairing: it skips regeneration when the web
certs already exist but still ensures the Kafka PEM bundle and the password
files are present, so it converges from any of the three partially-generated
states (nothing, web certs only, web certs + Kafka PEMs but no passwords).

The `Security Scan` job runs it before Trivy, and the Playwright config runs it
before the E2E suite, so both work from a clean checkout with no manual step.

## Pipeline

```text
Pull request
    |
    +--> lint / type-check / tests
    +--> Helm lint + render
    +--> Docker build smoke
    +--> Trivy + Semgrep

push develop
    |
    +--> publish 15 backend images + frontend to GHCR
    +--> deploy staging EKS
    +--> rollout verification
    +--> in-cluster /health checks

push main
    |
    +--> publish 15 backend images + frontend to GHCR
    +--> production Environment protection/approval
    +--> deploy production EKS
    +--> rollout verification
    +--> in-cluster /health checks
```

## CI Pipeline Details (15 jobs, several matrixed)

`.github/workflows/ci-cd.yml` defines 15 jobs. `Backend Test`,
`Docker Build Smoke`, and `Build & Push` fan out over a 15-service matrix, and
`Backend Lint` runs mypy per service.

| Stage | Jobs | Tools |
|---|---|---|
| Supply chain | 1 | `verify-supply-chain.py` (action pinning, scanner suppressions) + 4 unit tests |
| Lint | 1 (Backend) + 1 (Frontend) | ruff, black, mypy, ESLint, Prettier |
| Unit Tests | 16 (15 services + SDK) | pytest, Vitest |
| Contract | 1 | pytest (`tests/contract`, 24 route drift tests) |
| Frontend E2E | 1 | Playwright — 119 tests across 9 files, 15 routes (blocking) |
| Build | 17 (15 services + frontend + web) | Docker |
| Security | 1 | Trivy, Semgrep, CodeQL |
| Helm | 1 | helm lint (+ default/staging/production rendering) |
| Deploy | 2 | requires AWS OIDC + environment secrets |

Per-service coverage is enforced at a **95% floor** (`--cov-fail-under`); the
services currently sit at 97–99%.

**Total time**: ~15-20 minutes

### CI Pipeline Commands (reference)

```bash
# Backend lint
ruff check services/
black --check services/
mypy services/*/app --config-file pyproject.toml

# Frontend lint
cd apps/web && npm run lint

# Backend unit tests (per service)
for svc in services/*/; do
  (cd "$svc" && pytest tests --asyncio-mode=auto) || exit 1
done

# Frontend unit tests
cd apps/web && npx vitest run

# Integration tests (needs docker compose stack)
poetry run pytest tests/integration -q

# Contract tests
pytest tests/contract -q

# Frontend E2E tests
cd apps/web && npx playwright test --reporter=github

# Security scan
trivy fs --severity HIGH,CRITICAL .

# Helm lint
helm lint infrastructure/helm/wildframe
```

## GitHub configuration

Create GitHub Environments named `staging` and `production`.

Configure the following secrets in each environment:

| Secret | Purpose |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | IAM role assumed through GitHub OIDC |
| `WILDFRAME_JWT_SECRET` | JWT signing secret |
| `WILDFRAME_POSTGRES_PASSWORD` | PostgreSQL password |

For production, configure required reviewers on the `production` Environment. The deployment job itself is not an approval mechanism; GitHub Environment protection is.

## AWS OIDC

The deployment workflow uses `aws-actions/configure-aws-credentials` with `role-to-assume`. Do not add long-lived `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` credentials to the repository.

The IAM role must trust GitHub's OIDC provider and restrict the `sub` claim to this repository and the intended branch/environment.

## Container images

Each backend service is built from:

```text
services/<service>/Dockerfile
```

The frontend is built from:

```text
apps/web/Dockerfile
```

Images are published to:

```text
ghcr.io/<owner>/<repo>/<service>
```

where `<owner>/<repo>` is `${{ github.repository }}` — CI builds the image path
as `${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}/<service>` rather than hardcoding
an account name. For this repository that currently resolves to
`ghcr.io/shobhit727/Wildframe/<service>`, which is also the `repository` value
in `infrastructure/helm/wildframe/values.yaml`. The frontend image is published
as `ghcr.io/<owner>/<repo>/web-app`.

The deployment uses the immutable tag:

```text
sha-<GITHUB_SHA>
```

This prevents a deployment from silently changing because a mutable `latest` tag was overwritten.

## Kubernetes deployment

Helm chart:

```text
infrastructure/helm/wildframe
```

Staging:

```text
Cluster: wildframe-staging
Namespace: wildframe-staging
Values: infrastructure/helm/values-staging.yaml
```

Production:

```text
Cluster: wildframe-production
Namespace: wildframe-production
Values: infrastructure/helm/values-production.yaml
```

The workflow runs:

```bash
# Deploy the exact image produced by the current GitHub Actions run.
helm upgrade --install wildframe-prod infrastructure/helm/wildframe \
  -f infrastructure/helm/values-production.yaml \
  --namespace wildframe-production \
  --create-namespace \
  --set-string image.tag="sha-${GITHUB_SHA}" \
  --set-string imagePullSecrets[0].name=ghcr-pull \
  --atomic --timeout 15m
```

`--atomic` causes Helm to roll back if the upgrade fails.

## Runtime secrets

The Helm chart does not store the real JWT or PostgreSQL credentials in Git.

The deployment contract is a Kubernetes Secret named `wildframe-runtime` containing:

```text
JWT_SECRET_KEY
POSTGRES_PASSWORD
SEARCH_CURSOR_SECRET
```

The deployment workflow must create/update that secret from GitHub Environment secrets before running Helm.

Do not commit real credentials to `values.yaml`, `values-staging.yaml`, `values-production.yaml`, or any Markdown file.

### Key requirements and consumers

| Key | Consumed by | Requirements |
| --- | --- | --- |
| `JWT_SECRET_KEY` | all services | >= 32 chars, not a known-insecure value |
| `POSTGRES_PASSWORD` | all services | must match the database deployment |
| `SEARCH_CURSOR_SECRET` | `search-service` only | >= 32 chars, not a known-insecure value |

`SEARCH_CURSOR_SECRET` signs the search pagination cursor. It is deliberately a
**separate key** from `JWT_SECRET_KEY`: the two have different lifecycles, and
sharing one key would mean rotating either silently invalidates the other.

`search-service` validates this key at startup whenever `ENVIRONMENT` is not
`development`. If it is missing, too short, or a known-insecure value, the pod
fails validation and crash-loops. The chart injects it into `search-service`
only, so the other 14 services are unaffected if the key is absent — but
`search-service` will not start. A `secretKeyRef` pointing at a key that does
not exist is a hard container-start failure, not a silent empty value.

The dev default `dev-cursor-secret-change-in-production-min-32-bytes` is on the
service's known-insecure list. It satisfies the length check and is still
rejected — this is why a placeholder that looks strong enough is not safe to
copy into a real environment.

### Rotating runtime secrets

The chart never rotates secrets. Rotation is an out-of-band cluster operation
against the live Secret; the key *names* stay stable, so no chart or values
change is required.

```bash
# Generate a value that satisfies the service validator.
openssl rand -base64 48

# Update the Secret in place, preserving the other keys.
kubectl -n wildframe-production create secret generic wildframe-runtime \
  --from-literal=JWT_SECRET_KEY="$WILDFRAME_JWT_SECRET" \
  --from-literal=POSTGRES_PASSWORD="$WILDFRAME_POSTGRES_PASSWORD" \
  --from-literal=SEARCH_CURSOR_SECRET="$NEW_CURSOR_SECRET" \
  --dry-run=client -o yaml | kubectl apply -f -

# secretKeyRef values are read at container start, so pods must roll to pick
# up the new value.
kubectl -n wildframe-production rollout restart deployment/search-service
kubectl -n wildframe-production rollout status deployment/search-service
```

What breaks while rotating `SEARCH_CURSOR_SECRET`:

- **Only `search-service` is affected.** It is injected into that deployment alone.
- **In-flight pagination cursors are invalidated.** Any cursor a client is
  holding fails HMAC verification and the client restarts pagination. Expect a
  brief UX blip, not a security event, and no impact on stored data.
- **Issued JWTs are unaffected.** That separation is the reason this is a
  distinct key; rotating it does not sign users out.
- **Staging is independent.** It reads the same Secret name in the
  `wildframe-staging` namespace, so rotate each namespace separately.

Rotate `JWT_SECRET_KEY` and `POSTGRES_PASSWORD` the same way, but note that
rotating `JWT_SECRET_KEY` invalidates existing access tokens and forces a
re-login across all services.

> Provisioning gap: the deployment workflow currently does not create or update
> `wildframe-runtime`; it only creates the `ghcr-pull` image-pull secret. Until
> that is wired up, the `kubectl` command above is the actual rotation path, and
> the Secret must be seeded manually before the first deploy or every pod that
> references it will fail to start. Tracked separately from this change.

## Image pull authentication

The deployment creates a namespace-local `ghcr-pull` Docker registry secret and configures the Helm deployment to use it. The GitHub token used for this must have package read access.

If GHCR packages are made public later, the pull secret can remain; it is harmless and keeps the deployment contract consistent.

## Health and rollout verification

Every backend deployment has Kubernetes readiness and liveness probes using `/health`.

After Helm succeeds, CI waits for all 15 backend deployments:

```bash
# Fail the deployment if any service does not become ready.
for svc in $SERVICES; do
  kubectl rollout status "deployment/$svc" --timeout=10m
done
```

The pipeline then runs `/health` against every service from an ephemeral in-cluster curl pod. Failures are not ignored.

## Failure behavior

Deployment validation must be blocking. Do not add `continue-on-error: true` or `|| true` to deployment verification, smoke tests, rollout status, security scans, or application tests merely to make the workflow green.

If a deployment fails, inspect the workflow job logs and Kubernetes state before retrying:

```bash
# Inspect rollout state.
kubectl get deployments -n wildframe-production

# Inspect failed pods.
kubectl get pods -n wildframe-production

# Inspect recent events.
kubectl get events -n wildframe-production --sort-by=.lastTimestamp
```

## Production prerequisites

Before enabling automatic production deployment, verify:

- [ ] EKS cluster exists and is reachable by the deployment IAM role.
- [ ] GitHub OIDC trust policy is restricted to this repository/environment.
- [ ] `production` Environment requires approval.
- [ ] `AWS_DEPLOY_ROLE_ARN` is configured.
- [ ] `WILDFRAME_JWT_SECRET` is configured.
- [ ] `WILDFRAME_POSTGRES_PASSWORD` matches the database deployment.
- [ ] `WILDFRAME_SEARCH_CURSOR_SECRET` is configured (>= 32 chars, not a known-insecure value) and present in the `wildframe-runtime` Secret.
- [ ] GHCR package access works from the cluster.
- [ ] PostgreSQL, Redis, Kafka, and Elasticsearch endpoints are configured.
- [ ] Ingress, TLS, DNS, and CDN are configured.
- [ ] Backups and restore procedures have been tested.
- [ ] Monitoring and alert routing are configured.
- [ ] Load and failure testing has been completed.

## What this guide does not claim

A green CI run proves that the repository's automated checks passed. It does **not** prove that AWS infrastructure, credentials, databases, external integrations, DNS, TLS, CDN, payments, DRM, or disaster recovery are production-ready.