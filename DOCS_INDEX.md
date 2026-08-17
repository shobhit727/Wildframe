# Wildframe — Full Documentation Index

Complete index of every `.md` file in the repository with a short summary and a
pointer to the file. Files marked **current** describe how the platform is
actually built today; files marked **historical** are retained for project
history and are not release declarations (see `STATUS.md`).

**Last updated**: August 17, 2026

---

## Current operational docs (start here)

| File | Summary |
|---|---|
| [`README.md`](README.md) | Project overview: current architecture (15 FastAPI services, Next.js frontend, SDK), CI/CD pipeline, Docker builds, deployment requirements, known production gaps. |
| [`STATUS.md`](STATUS.md) | Current implementation and deployment status, remaining production work, and the Aug 2026 security/QA hardening record (closed audit issues, integration suite, test totals). |
| [`AGENTS.md`](AGENTS.md) | Agent/developer instructions: source of truth for how the repo is actually built — setup, service list, ports, HTTPS/TLS, code conventions, common patterns, and pitfalls. |
| [`SECURITY.md`](SECURITY.md) | Vulnerability reporting policy (private reporting process). |
| [`HOW_TO_RUN_TESTS.md`](HOW_TO_RUN_TESTS.md) | Test commands and current stats: 775 backend unit/route tests, 87 live-stack integration tests, 43 frontend vitest tests. |
| [`docs/INDEX.md`](docs/INDEX.md) | Curated index of the current operational documentation (superset: this file covers everything, including history). |
| [`DOCS_INDEX.md`](DOCS_INDEX.md) | This file — every `.md` in the repo with summaries. |

---

## docs/ — reference and guides

| File | Summary |
|---|---|
| [`docs/QUICKSTART.md`](docs/QUICKSTART.md) | **Current** local setup guide: prerequisites, compose stack, frontend, test suite. |
| [`docs/TEST_GUIDE.md`](docs/TEST_GUIDE.md) | **Current** testing playbook: test stack, layout, fixtures, coverage, and the live-stack integration suite (`tests/integration/`, 87 tests). |
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
| [`docs/INDEX.md`](docs/INDEX.md) | Curated index of current operational docs (see above). |

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
| [`INDEX.md`](INDEX.md) | Aug 4, 2026 documentation index (superseded by `docs/INDEX.md` and this file). |
| [`QUICK_START.md`](QUICK_START.md) | Aug 1, 2026 quick reference (6-service era). |
| [`QUICKSTART.md`](QUICKSTART.md) | Older quickstart (6-service era, direct-port curl examples). |
| [`START_HERE.md`](START_HERE.md) | Early "work completed — 12 microservices" claim document (predates the current layout). |
| [`STARTUP_GUIDE.md`](STARTUP_GUIDE.md) | Early startup guide (12-service era). |
| [`TEST_GUIDE.md`](TEST_GUIDE.md) | Older testing guide with direct-port curl examples (superseded by `docs/TEST_GUIDE.md`). |
| [`TESTING_GUIDE.md`](TESTING_GUIDE.md) | Historical manual-testing guide; header notes it predates the current layout and test layout. |

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
| [`load-tests/README.md`](load-tests/README.md) | Locust load-test suite against the API gateway (`:8000`): setup and run commands. |
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
- The executable CI/CD definition is `.github/workflows/ci-cd.yml`.