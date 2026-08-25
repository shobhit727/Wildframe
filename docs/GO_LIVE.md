# Go-Live Runbook

Everything code-side is ready. What separates dev from production is
infrastructure that requires **your** accounts and credentials. This is the
exact order to go live.

## 0. What is already automated (no action needed)

- CI/CD: lint + 15 service test suites + SDK + frontend + Docker build smoke +
  Trivy/CodeQL on every PR; images build & push on main; deploys gated on
  `vars.AWS_DEPLOY_ROLE_ARN` (they run the moment that variable exists).
- TLS everywhere, strict CORS allow-list, TrustedHost production gate,
  CSP at runtime, NIST password policy, JWT rotation + step-up reauth,
  playback concurrency with idle reaping, HttpOnly refresh cookies.
- `deployments/docker-compose.dev.yml`: every service has
  `restart: unless-stopped` and health-gated `depends_on`, so a rebooted host
  brings the whole stack back by itself.

## 1. AWS account + Terraform (P0)

```bash
cd infrastructure/terraform
# one-time remote state (isolated from app runtime, see issue #413):
terraform init -backend-config="bucket=wildframe-tfstate" ...
terraform plan -var-file=production.tfvars   # review
terraform apply                              # VPC, EKS, RDS, ElastiCache, S3+CF
```

You need: an AWS account, an IAM role for CI
(`AWS_DEPLOY_ROLE_ARN` → repo Settings → Secrets/Variables), and a Route53
zone (or Cloudflare DNS as the Terraform assumes).

## 2. Secrets (P0)

Set in AWS Secrets Manager and wire to EKS (ExternalSecrets or SSM):

- `JWT_SECRET_KEY` (32+ random bytes — never the dev value)
- Postgres master password (RDS-managed)
- `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` (live mode)
- SMTP/notification creds

CI already refuses images built with dev defaults; services refuse to boot in
`ENVIRONMENT=production` with known-insecure secrets.

## 3. Database schema (P0)

There is no migration framework by design. From a machine with access:

```bash
python scripts/init_schemas.py   # creates all tables from the models
```

Run it once against prod RDS through the bastion/EKS job, then freeze the
schema and use expand/contract SQL for changes (#577/#578).

## 4. DNS + TLS (P0)

- Point `wildframe.com` / `api.wildframe.com` at Cloudflare/ALB.
- ACM certs for both hostnames (Terraform includes them).
- Set frontend env `NEXT_PUBLIC_API_URL=https://api.wildframe.com` at build.
- Backend `CORS_ALLOWED_ORIGINS=["https://wildframe.com"]`,
  `TRUSTED_HOSTS=["wildframe.com","api.wildframe.com"]`,
  `ENVIRONMENT=production`.

## 5. First deploy (P0)

```bash
git push origin main          # images build & push
# Deploy Staging runs automatically; verify, then approve Deploy Production
```

Both deploy jobs unblock the moment `AWS_DEPLOY_ROLE_ARN` exists.

## 6. Post-deploy verification (P0)

- `https://api.wildframe.com/health` per service → 200
- Register a real user, log in, play a title, check admin console
- Stripe test webhook → 200 in billing logs
- Grafana: dashboards populated; alert route fires to your email/Slack

## 7. Before announcing (P1)

- CloudTrail + GuardDuty verified (#415/#410), ECR scan-on-push (#412)
- S3 media bucket versioning (#411), RDS PITR confirmed by a restore drill
- Branch protection + CODEOWNERS active (#401/#402)
- Load test the streaming path; confirm `MAX_ACTIVE_SESSIONS` sizing

## The honest short version

Code: done. CI: done. What only you can provide: **an AWS account with
billing, a domain, and Stripe live keys.** Wire those into step 1–2 and the
existing pipeline takes it from there.
