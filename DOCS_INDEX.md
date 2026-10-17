# Wildframe — Full Documentation Index

Complete index of every `.md` file in the repository with a short summary and a
pointer to the file. Files marked **current** describe how the platform is
actually built today; files marked **historical** are retained for project
history and are not release declarations (see `STATUS.md`).

**Last updated**: October 14, 2026

---

## Current operational docs (start here)

| File | Summary |
|---|---|
| [`README.md`](README.md) | Project overview: current architecture (15 FastAPI services, Next.js frontend, SDK), CI/CD pipeline, Docker builds, deployment requirements, known production gaps. |
| [`STATUS.md`](STATUS.md) | Current implementation and deployment status, remaining production work, and the Aug 2026 security/QA hardening record (closed audit issues, integration suite, test totals). |
| [`AGENTS.md`](AGENTS.md) | Agent/developer instructions: source of truth for how the repo is actually built — setup, service list, ports, HTTPS/TLS, code conventions, common patterns, and pitfalls. |
| [`SECURITY.md`](SECURITY.md) | Vulnerability reporting policy (private reporting process). |
| [`HOW_TO_RUN_TESTS.md`](HOW_TO_RUN_TESTS.md) | **Canonical test-count source.** Test commands and dated stats: 6,943 backend unit/route tests, 795 SDK, 84 contract, 110 live-stack integration, 866 frontend vitest, 122 Playwright e2e. Link here rather than repeating a count. |
| [`docs/INDEX.md`](docs/INDEX.md) | Curated index of the current operational documentation (superset: this file covers everything, including history). |
| [`DOCS_INDEX.md`](DOCS_INDEX.md) | This file — every `.md` in the repo with summaries. |

---

## docs/ — reference and guides

| File | Summary |
|---|---|
| [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | **Current** local setup guide: prerequisites, compose stack, frontend, test suite. |
| [`docs/TEST_GUIDE.md`](docs/TEST_GUIDE.md) | **Current** testing playbook: test stack, layout, fixtures, coverage, and the live-stack integration suite (`tests/integration/`, 110 tests). |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | **Current** development workflow: setup, conventions, roadmap, technical glossary. |
| [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) | Contribution conventions and expected service layout. |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Architecture overview (service structure, patterns, DB schema notes). Partly aspirational — header flags that `AGENTS.md`/`README.md` are authoritative for current facts (15 services, JWT audience, no Alembic). |
| [`docs/FRONTEND_ARCHITECTURE.md`](docs/FRONTEND_ARCHITECTURE.md) | Frontend architecture: Next.js 15 App Router, state management, video player design. |
| [`docs/SERVICE_ARCHITECTURE_PATTERN.md`](docs/SERVICE_ARCHITECTURE_PATTERN.md) | Canonical internal layout every microservice follows. |
| [`docs/DATABASE_SCHEMA.md`](docs/DATABASE_SCHEMA.md) | Database design reference: database-per-service, tables, indexes, partitioning. |
| [`docs/API_DOCUMENTATION.md`](docs/API_DOCUMENTATION.md) | API reference: base URLs, JWT auth flow (incl. `aud: wildframe-api` audience claim), rate limiting, endpoints for services 1–4, error format. |
| [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md) | Current deployment path: GitHub Actions → GHCR → Helm → AWS EKS (staging/prod), required secrets. |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Operational procedures: runbooks, monitoring, incident response, DB migrations (no Alembic — hand-applied), backups. |
| [`docs/MONITORING.md`](docs/MONITORING.md) | Observability stack: Prometheus, Grafana, Jaeger, Loki; metrics, logs, tracing, alerting. |
| [`docs/DRM_SCOPE.md`](docs/DRM_SCOPE.md) | DRM gap analysis: today Wildframe streams plaintext HLS/DASH; what Widevine/FairPlay/PlayReady would require (backlog issue #45). |
| [`docs/GLOSSARY.md`](docs/GLOSSARY.md) | Terminology reference (A–Z). |
| [`docs/PRODUCT_VISION.md`](docs/PRODUCT_VISION.md) | Product charter: OTT platform for independent animators; scope and architectural decisions that follow. |
| [`docs/WHATS_INCLUDED.md`](docs/WHATS_INCLUDED.md) | Feature/service inventory (historical — predates the 15-service layout). |
| [`docs/OVERVIEW.md`](docs/OVERVIEW.md) | Older documentation overview and reading order (historical). |
| [`docs/DOCUMENTATION_GUIDE.md`](docs/DOCUMENTATION_GUIDE.md) | How to write docs for humans and AI: principles, structure, templates. |
| [`docs/QUICK_LOCAL_SETUP.md`](docs/QUICK_LOCAL_SETUP.md) | Short local setup checklist. |
| [`docs/COMPLIANCE_MASTER_PLAN.md`](docs/COMPLIANCE_MASTER_PLAN.md) | Compliance work plan (COPPA/DPDP/GDPR/CCPA). Its "shipped" column is a **claim, not a verified inventory** — endpoints marked delivered were 404 at audit time and are now marked explicitly. |
| [`docs/COMPLIANCE_FOUNDATION_PLAN.md`](docs/COMPLIANCE_FOUNDATION_PLAN.md) | Compliance foundation plan that precedes the master plan. |
| [`docs/GO_LIVE.md`](docs/GO_LIVE.md) | Go-live checklist. |
| [`docs/INTERNAL_AUTHZ.md`](docs/INTERNAL_AUTHZ.md) | Internal service-to-service authorization reference. |
| [`docs/TIMEOUT_ORDERING.md`](docs/TIMEOUT_ORDERING.md) | Timeout propagation and ordering notes. **Partly aspirational**: it describes a reverse-proxy `proxy_read_timeout` and a `WithTimeout` wrapper that do not exist in the source. |

---

## Root historical files

| File | Summary |
|---|---|
| [`AUDIT_FIX_SUMMARY.md`](AUDIT_FIX_SUMMARY.md) | Aug 2026 audit remediation record: 22 fixed items, repo hygiene, live security pentest notes. History — issue-level fixes now tracked on GitHub. |
| [`SECURITY_AUDIT_FIXES.md`](SECURITY_AUDIT_FIXES.md) | Log of security audit remediation passes, including the Aug 2026 audience verification, pipeline auth, analytics authorization, webhook idempotency, and integration-suite work. |
| [`COMPLETION_SUMMARY.md`](COMPLETION_SUMMARY.md) | Aug 1, 2026 session summary (startup fixes for 6 core services). |
| [`FINAL_EXECUTION_REPORT.md`](FINAL_EXECUTION_REPORT.md) | Aug 1, 2026 execution report (same session; explicitly corrects an earlier "ALL COMPLETE" claim). |
| [`IMPLEMENTATION_COMPLETE.md`](IMPLEMENTATION_COMPLETE.md) | Aug 1, 2026 implementation status (~35% complete). |
| [`FRONTEND_COMPLETE.md`](FRONTEND_COMPLETE.md) | Aug 1, 2026 frontend status (scaffold only at the time). |
| [`README_COMPLETE.md`](README_COMPLETE.md) | Aug 1, 2026 honest-status README variant. |
| *(removed)* | Aug 4, 2026 documentation index, previously at the repo root. Deleted 2026-09-27: it claimed to be the entry point while duplicating `docs/INDEX.md` and this file, and its numbers and commands were stale. Superseded by `docs/INDEX.md` and this file. |
| [`QUICK_START.md`](QUICK_START.md) | Aug 1, 2026 quick reference (6-service era). |
| [`QUICKSTART.md`](QUICKSTART.md) | Older quickstart (6-service era, direct-port curl examples). |
| [`START_HERE.md`](START_HERE.md) | Early "work completed — 12 microservices" claim document (predates the current layout). |
| [`STARTUP_GUIDE.md`](STARTUP_GUIDE.md) | Early startup guide (12-service era). |
| [`TEST_GUIDE.md`](TEST_GUIDE.md) | Older testing guide with direct-port curl examples (superseded by `docs/TEST_GUIDE.md`). |
| [`TESTING_GUIDE.md`](TESTING_GUIDE.md) | Historical manual-testing guide; header notes it predates the current layout and test layout. |
| [`CLOSED_ISSUES_AUDIT.md`](CLOSED_ISSUES_AUDIT.md) | Audit of issues already closed at the time (Aug 2026). Its "0 open issues" and CI-run tallies describe that snapshot and are not current — the tracker has open items. |
| [`CLOSED_ISSUES_AUDIT_FULL.md`](CLOSED_ISSUES_AUDIT_FULL.md) | Longer version of the above; same caveat. |
| [`CODEBASE_LEARNING_OUTLINE.md`](CODEBASE_LEARNING_OUTLINE.md) | Learning-oriented walkthrough outline of the codebase. |
| [`GITHUB_ISSUES_CODE_AUDIT_2026-09-26.md`](GITHUB_ISSUES_CODE_AUDIT_2026-09-26.md) | Dated code audit against GitHub issues, 2026-09-26. |
| [`TODO-full.md`](TODO-full.md) | Full TODO inventory, dated 2026-09-26. (There is no `TODO.md`.) |
| [`web_audit_report.md`](web_audit_report.md) | Dated frontend audit. **Superseded in part**: its "no Content-Security-Policy header" finding no longer holds — `apps/web/src/proxy.ts` now sets CSP with a nonce. |

---

## apps/web — frontend docs

| File | Summary |
|---|---|
| [`apps/web/README.md`](apps/web/README.md) | Frontend README: quick start, scripts, structure. |
| [`apps/web/FRONTEND_README.md`](apps/web/FRONTEND_README.md) | Frontend feature overview (auth, browsing, player, subscriptions, etc.). |
| [`apps/web/QUICK_REFERENCE.md`](apps/web/QUICK_REFERENCE.md) | Template quick reference: dev commands and project structure. |
| [`apps/web/README_TEMPLATE.md`](apps/web/README_TEMPLATE.md) | Netlify-style template README (the frontend was scaffolded from a template). |
| [`apps/web/TEMPLATE_GUIDE.md`](apps/web/TEMPLATE_GUIDE.md) | Netlify-style template guide: install, env, structure. |
| [`apps/web/docs/INDEX.md`](apps/web/docs/INDEX.md) | Template documentation index (task-based navigation). |
| [`apps/web/docs/GETTING_STARTED.md`](apps/web/docs/GETTING_STARTED.md) | Template getting-started guide. |
| [`apps/web/docs/API_INTEGRATION.md`](apps/web/docs/API_INTEGRATION.md) | Template guide for wiring the frontend to a backend API (`NEXT_PUBLIC_API_URL`). |
| [`apps/web/docs/COMPONENTS.md`](apps/web/docs/COMPONENTS.md) | Template component catalog (Button, etc.). |
| [`apps/web/docs/CUSTOMIZATION.md`](apps/web/docs/CUSTOMIZATION.md) | Template branding/customization guide. |
| [`apps/web/docs/DEPLOYMENT.md`](apps/web/docs/DEPLOYMENT.md) | Template deployment guide (Vercel, Netlify, Docker). |
| [`apps/web/docs/TROUBLESHOOTING.md`](apps/web/docs/TROUBLESHOOTING.md) | Template troubleshooting guide (port conflicts, startup issues). |

---

## Other

| File | Summary |
|---|---|
| [`PROJECT_MEMORY/00_Project_Overview.md`](PROJECT_MEMORY/00_Project_Overview.md) | Project memory: overview of the Wildframe platform. |
| [`PROJECT_MEMORY/12_Todo_List_Backlog.md`](PROJECT_MEMORY/12_Todo_List_Backlog.md) | Engineering backlog (pre-audit list; Priority 1/2 landed — see header note). |
| [`PROJECT_MEMORY/13_Bug_Tracker.md`](PROJECT_MEMORY/13_Bug_Tracker.md) | Bug tracker with severity definitions. |
| [`PROJECT_MEMORY/14_Technical_Debt.md`](PROJECT_MEMORY/14_Technical_Debt.md) | Technical debt register (items 1–5 resolved — see header note). |
| [`PROJECT_MEMORY/21_Risk_Assessment.md`](PROJECT_MEMORY/21_Risk_Assessment.md) | Risk assessment (critical/high/medium risks). |
| [`PROJECT_MEMORY/22_Improvement_Ideas.md`](PROJECT_MEMORY/22_Improvement_Ideas.md) | Improvement ideas (shared SDK, architecture, ops). |

---

## Reading order

1. `README.md` → `STATUS.md` → `AGENTS.md`
2. `docs/QUICKSTART.md` → `docs/TEST_GUIDE.md`
3. `docs/ARCHITECTURE.md` → `docs/API_DOCUMENTATION.md` → `docs/DEPLOYMENT_GUIDE.md`

## Rules of thumb

- Historical completion reports (`*_COMPLETE.md`, `*COMPLETION*.md`,
  `FINAL_*REPORT.md`, `QUICKSTART.md`, `START_HERE.md`, `STARTUP_GUIDE.md`)
  are **not** release declarations — verify against `README.md`, `STATUS.md`,
  and GitHub Actions results instead.
- The executable CI/CD definition is `.github/workflows/ci-cd.yml` — 17 jobs (count it; it changes).
- Test counts are canonical in [`HOW_TO_RUN_TESTS.md`](HOW_TO_RUN_TESTS.md) and
  are dated. Link to it; do not copy a count into a second file.

---

## Agent / process files (not product documentation)

| File | Summary |
|---|---|
| [`AGENT_COORDINATION.md`](AGENT_COORDINATION.md) | Multi-agent workflow on a shared branch: path claims, verified git failure modes, `/tmp` hygiene, subagent rules. |
| [`ONBOARDING.md`](ONBOARDING.md) | The path through this repo: what to do in what order, which commands actually work, and the traps that produce wrong conclusions. |
| [`oner-task.md`](oner-task.md) | What a human still owes: decisions, review, blocked work, deliberate limitations. |
| [`Message-board.md`](Message-board.md) | Append-only shared coordination log for concurrent agents. Lives on the working branch, not on `main`. |
| [`spawn-board.md`](spawn-board.md) | Per-worktree agent spawn board. |
| [`apps/web/AGENTS.md`](apps/web/AGENTS.md) | Frontend-specific agent instructions. |
| [`skills/verify-against-running-stack/SKILL.md`](skills/verify-against-running-stack/SKILL.md) | Skill: prove a change works against the running stack, not just the suite. |
| [`skills/wildframe-service-change/SKILL.md`](skills/wildframe-service-change/SKILL.md) | Skill: adding/changing a route, schema, repository query or cross-service call. |
| [`skills/shared-tree-coordination/SKILL.md`](skills/shared-tree-coordination/SKILL.md) | Skill: editing the shared branch safely. |

---

## scripts/ and load-tests

| File | Summary |
|---|---|
| [`scripts/README.md`](scripts/README.md) | What each helper script does and when to run it. |
| [`scripts/dev/README.md`](scripts/dev/README.md) | Developer-only helper scripts. |
| [`load-tests/README.md`](load-tests/README.md) | Locust load-test suite against the API gateway (`:8000`): setup and run commands. **Local only — no CI job runs it.** |

---

## Undeclared secondary issue tracker

`.github/issues/*.md` is a second, undeclared issue tracker: referenced by no
document and by no workflow. Two of its five entries are factually wrong about
current code — do not act on them without re-verifying against the source.

| File | Summary |
|---|---|
| [`001-eval-exec-in-scripts.md`](.github/issues/001-eval-exec-in-scripts.md) | Claims an `eval`/`exec` usage in `scripts/`. |
| [`002-redis-asyncio-from_url-await-misuse.md`](.github/issues/002-redis-asyncio-from_url-await-misuse.md) | Claims `await redis.from_url(...)` is a runtime error. It is correct as written, and the repo's own tests defend it. |
| [`003-hardcoded-secrets-and-demo-credentials.md`](.github/issues/003-hardcoded-secrets-and-demo-credentials.md) | Hardcoded-secret findings. |
| [`004-jwt-import-inconsistencies.md`](.github/issues/004-jwt-import-inconsistencies.md) | Claims a bare `import jwt` in api-gateway that does not exist. |
| [`005-todo-fixme-technical-debt-inventory.md`](.github/issues/005-todo-fixme-technical-debt-inventory.md) | TODO/FIXME inventory. |

---

## Other package docs

| File | Summary |
|---|---|
| [`packages/sdk/wildframe_compliance/README.md`](packages/sdk/wildframe_compliance/README.md) | Shared compliance library README. |
| [`apps/web/certificates/README.md`](apps/web/certificates/README.md) | Explains the gitignored local dev certificates and how to regenerate them. |