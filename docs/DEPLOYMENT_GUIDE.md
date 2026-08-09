# Wildframe Deployment Guide

## Scope

This document describes the current GitHub Actions → GHCR → AWS EKS deployment path.

Wildframe is **not production-ready by default**. The workflow assumes that the AWS infrastructure, EKS clusters, PostgreSQL, Redis, Kafka, Elasticsearch, DNS/TLS, and required GitHub environments already exist and are correctly configured.

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

## GitHub configuration

Create GitHub Environments named `staging` and `production`.

Configure the following secrets in each environment:

| Secret | Purpose |
| --- | --- |
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
ghcr.io/shobhit727/wildframe/<service>
```

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
```

The deployment workflow must create/update that secret from GitHub Environment secrets before running Helm.

Do not commit real credentials to `values.yaml`, `values-staging.yaml`, `values-production.yaml`, or any Markdown file.

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
- [ ] GHCR package access works from the cluster.
- [ ] PostgreSQL, Redis, Kafka, and Elasticsearch endpoints are configured.
- [ ] Ingress, TLS, DNS, and CDN are configured.
- [ ] Backups and restore procedures have been tested.
- [ ] Monitoring and alert routing are configured.
- [ ] Load and failure testing has been completed.

## What this guide does not claim

A green CI run proves that the repository's automated checks passed. It does **not** prove that AWS infrastructure, credentials, databases, external integrations, DNS, TLS, CDN, payments, DRM, or disaster recovery are production-ready.
