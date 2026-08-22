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

## Open / next up (prioritized)

### P0 — correctness follow-ups
- [ ] Watch dependabot-rebased PR CI; merge green ones in small batches (majors like zustand 5 / kafka-python 3 need a runtime smoke test after merge).
- [ ] Add regression tests: streaming advisory-lock path (concurrent starts ≤ MAX_ACTIVE_SESSIONS), `/auth-session` single-flight, `setTokens` await semantics.
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
- [ ] Next 16 deprecations: rename `middleware.ts` → `proxy.ts`; drop dead `eslint` key from next.config.
- [ ] Root landing page right half is empty — consider PosterArt collage backdrop.
