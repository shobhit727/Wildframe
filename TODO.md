# Wildframe Working To-Do

Living checklist: what was done recently, what is open, and what comes next.
Update it as work lands; do not let it drift from reality.

---

## Recently completed (Aug 2026 hardening + live-stack bring-up)

### CI/CD
- [x] Full CI green on main; all actions pinned to SHAs; deploy jobs gated on `vars.AWS_DEPLOY_ROLE_ARN`.
- [x] Backend Lint deterministic (pinned black/ruff); mypy strictness with scoped overrides.
- [x] ~130 GitHub issues closed with evidence across search/uploads/DB/auth/HTTP/streaming/cache/supply-chain/JWT/Terraform/K8s/CORS batches.

### Live Docker stack (28 containers)
- [x] Async engine pooling fixed in auth/content/streaming/user/creators (dialect-aware; never pass sync QueuePool to async engines).
- [x] Moderation cross-service FK removed (`creator_strikes.creator_id`).
- [x] `scripts/init_schemas.py` — fresh-stack table bootstrap (no Alembic).
- [x] `scripts/seed_demo.py` — genres/movies/series over TLS gateway + admin login; SVOD subscription + moderation row.
- [x] Kafka cluster-ID conflict resolved (stale volumes removed).

### Frontend
- [x] Tailwind v4 actually compiling (`@tailwindcss/postcss`, `postcss.config.mjs`, `@import "tailwindcss"` + `@config`) — site was unstyled before.
- [x] `PosterArt` deterministic generated key art (cards/hero/player), browse spacing fix.
- [x] HTTPS everywhere: cert regenerated with LAN IP SAN; Caddy catch-all `https://:8000`; web dev on `https://localhost:3000`; CORS allow-list includes LAN origins; gateway `RATE_LIMIT_AUTH=60/min` in dev.
- [x] `/auth-session` secureFetch (node:https + explicit dev CA, Content-Length) + single-flight refresh per cookie (rotation-safe).
- [x] Auth-gate race fixed: awaited cookie persistence in `setTokens`; Providers gate blocks render until first session check; AdminGate waits for role hydration.
- [x] Streaming playback 500 fixed: `count() FOR UPDATE` → per-user `pg_advisory_xact_lock`.

### Verification
- [x] All 15 service suites + SDK green locally; web vitest 59 passed; tsc clean.
- [x] Screenshot review of all 14 pages via headless Chromium over TLS.
- [x] Hack sweep — no exploitable holes: stored XSS escaped by React; IDOR (profile/subscription/playback) → 403; forged/tampered/refresh-as-access JWTs → 401; non-admin on admin endpoints → 403 (+ step-up reauth enforced); SQLi probes parameterized-safe; uploads traversal → 404.
- [x] Dependabot PRs: 4 merged (dashjs/next-themes/sonner/vite-plugin-react), 26 rebased onto new main.

---

### Landed since the checklist above (Aug 24)
- [x] Playback 409 lockout fixed for real: idle ACTIVE sessions reaped on next start (`PLAYBACK_SESSION_IDLE_TIMEOUT_MINUTES`, default 90) inside the advisory lock.
- [x] Admin API base now host-derived (LAN browsing no longer CORS-fails on /admin).
- [x] Signup: client validation matches NIST backend policy (12+ chars, two classes); strong passphrases accepted end-to-end.
- [x] Compose: `restart: unless-stopped` on all 28 services + health-gated depends_on — rebooted hosts self-heal; kafka healthcheck given 15s/90s headroom.
- [x] `apps/web/public/robots.txt` (private paths disallowed) — serving 200.
- [x] `docs/GO_LIVE.md` — exact runbook for the AWS/DNS/Stripe steps only the owner can do.
- [x] `scripts/dev/` — reusable browser journey, auth matrix, screenshot crawler, security probe (pinned playwright, env-parameterized, README).

## Open / next up (prioritized)

### P0 — correctness follow-ups
- [ ] Watch dependabot-rebased PR CI; merge green ones in small batches (majors like zustand 5 / kafka-python 3 need a runtime smoke test after merge).
- [ ] Add regression tests: streaming idle-reap path, `/auth-session` single-flight, `setTokens` await semantics.
- [ ] Registration should provision a default profile (auth publishes `user.registered`; user-service consumes) instead of frontend auto-create-on-404.

### P1 — product/engineering issues still open (~79)
- [ ] Token-family theft detection cluster (#597/#126/#183/#440).
- [ ] DRM scoping (#45) and MFA derivation (#78) decisions.
- [ ] Integration suite expansion (#41) against live compose.
- [ ] Kafka ACLs (#550/#551), service-to-service auth (#444/#445).
- [ ] Rollback tests (#158), migration strategy (#156/#577/#578), renditions (#284), outbox retry (#295).

### P2 — infra (needs real AWS account/session)
- [ ] Umbrellas #636–#639: CloudTrail, GuardDuty, IRSA, IMDSv2, ECR provenance, state isolation.
- [ ] Terraform plan/apply dry-run in CI with a throwaway account.

### P3 — polish
- [ ] Brighten PosterArt hero backdrop (currently very dark behind title text).
- [ ] Watch page: package a real demo HLS asset so the player plays instead of the Retry surface.
- [ ] BUG: watch-page metadata block (incl. My List toggle) not rendering — `content` query on /watch fails; investigate.
- [ ] Next 16 deprecations: rename `middleware.ts` → `proxy.ts`; drop dead `eslint` key from next.config.
- [ ] Root landing page right half is empty — consider PosterArt collage backdrop.

---

## Production-readiness checklist (what stands between dev and prod)

### P0 — hard blockers (no prod without these)
1. **Real AWS account + Terraform apply** — VPC/EKS/RDS/ElastiCache/S3+Cloudflare; isolated state backend & locking (#413); KMS key-policy separation (#414); CloudTrail org coverage (#415); GuardDuty (#410); ECR scan-on-push + base-image patching (#412); S3 media versioning/recovery (#411).
2. **Secrets management** — every dev default rotated (`dev-secret-key`, `wildframe_dev_password`, demo creds removed); JWT secret from KMS/Secrets Manager with rotation runbook; Stripe live keys + webhook signature enforcement.
3. **Migrations** — repo has none. Baseline Alembic (or blessed SQL runbooks) + expand/contract policy (#577/#578) before first schema change in prod.
4. **Domain/TLS/CORS** — real certs (ACM/CF), `CORS_ALLOWED_ORIGINS` set to real origins only, `TrustedHostMiddleware` production list (code already gates on `ENVIRONMENT=production`), cookies `Secure` (web already keys off NODE_ENV).
5. **Backups & DR** — RDS automated backups + PITR, restore drill actually executed, S3 lifecycle/versioning verified.
6. **Observability wired to real destinations** — persistent Grafana/Loki volumes or S3-backed Loki, alert routes to on-call, Jaeger retention; tamper-resistant logs (#416).
7. **Email deliverability** — SPF/DKIM/DMARC for the sending domain (#408) if notification-service sends.

### P1 — security hardening before public traffic
8. Refresh-token family theft detection + bounded retention (#597/#440).
9. Service-to-service authn/expiry scopes (#444/#445) and Kafka ACLs (#550/#551), DLQ retention (#553).
10. Proxy parsing consistency / smuggling hardening (#521/#522), cookie scope minimization (#525).
11. Repo controls: branch protection + required reviews (#401), CODEOWNERS on sensitive paths (#402), secret scanning + push protection (#403).
12. Immutable, provenance-attested image references in manifests (#395); Swagger/ReDoc disabled or authed in prod (#468).

### P2 — confidence
13. Load test the streaming path (concurrency limits sized against `MAX_ACTIVE_SESSIONS`), CDN invalidation coordinated with deploys (#495).
14. Staging environment deployed by the CI pipeline with a post-deploy smoke suite (the integration suite already exists — point it at staging).
15. Runbooks: rotate JWT secret, revoke user tokens at scale (auth_version bump), restore-from-backup, Kafka partition lag triage.

**Rule of thumb:** P0 = money/data loss or full compromise; P1 = targeted attack surface; P2 = operational confidence. Ship nothing while any P0 is unchecked.
