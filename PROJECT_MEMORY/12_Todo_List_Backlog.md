# 12_Todo_List_Backlog

> **Updated Aug 9, 2026**: the backlog below is the original pre-audit list.
> Everything under Priority 1/2 has landed in the current codebase (verified
> by 551 passing backend tests + 43 frontend tests; see AUDIT_FIX_SUMMARY.md):
> dead routers included, duplicate models removed, JWT auth wired, netflix_backend
> deleted, empty `-service` dirs renamed to canonical names, orphans
> (creators/moderation/uploads) added to compose, rate limiting enforced
> (gateway + auth-service), pytest runs per-service. The relevant "in progress"
> items below have been checked accordingly.

## Priority 1: Critical Bug Fixes (Services Won't Start)
### auth-service (HIGH_URGENT)
- [x] Fix indentation error in main.py line 210 (wire_observability call)
- [x] Make settings.LOG_LEVEL accessible (confirmed exists in settings.py)
- [x] Fix duplicate model sets (models/__init__.py vs user.py)
- [x] Fix dead auth router (routes/__init__.py doesn't include auth.py)
- [x] Fix authentication middleware (get_current_user no Header() injection)
- [x] Fix self-including router issue
- [x] Add proper JWT validation to routes
- [x] MFA/TOTP + email verification implemented (no more 501 stubs)

### user-service (HIGH_URGENT)
- [x] Fix indentation error in main.py line 142
- [x] Eliminate duplicate models (__init__.py vs user.py)
- [x] Fix broken auth dependency (__init__.py doesn't import routes properly)
- [x] Make JWT validation work properly
- [x] Fix index name conflicts

### content-service (MEDIUM_URGENT)
- [x] Fix LOG_LEVEL setting (auth needed it, content still missing in settings)
- [x] Fix duplicate model sets (__init__.py vs content.py)
- [x] Mount missing router in main.py
- [x] Fix Pydantic v2 issues (regex vs pattern, min_items vs min_length)
- [x] Fix health check misuse of raw strings
- [x] Duplicate genre → 409 (Aug 9)

### streaming-service (HIGH_URGENT)
- [x] Fix syntax error in main.py line 210 (wire_observability indentation)
- [x] Fix routing model lookup Issues (wrong StreamingService constructor)
- [x] Fix dual Base instances
- [x] Fix broken auth dependency (Header() injection)
- [x] Fix model naming conflicts (duplicate Genre definitions)
- [x] Fix missing LOG_LEVEL import
- [x] Full authz hardening (Aug 9): all endpoints JWT, owner-scoped reads/writes

### admin-service (MEDIUM_URGENT)
- [x] Restructure create_app() pattern proper FastAPI lifecycle
- [x] Setup core settings module properly
- [x] Implement real time_db dependency injection
- [x] Implement Juniper auth properly with JWT
- [x] Add proper logging configuration
- [x] Fix missing/show summaries in click_tool_search
- [x] Fix alert response models to include all required fields

## Priority 2: Infrastructure Cleanup
### Directory Structure (MEDIUM)
- [x] Delete netflix_backend (legacy dead code)
- [x] Delete empty -service directories (analytics-service, billing-service, notification-service, recommendation-service, search-service)
- [x] Rename analytics/ → analytics-service/
- [x] Rename billing/ → billing-service/
- [x] Rename notification/ → notification-service/
- [x] Rename recommendation/ → recommendation-service/
- [x] Rename search/ → search-service/
- [x] Rename streaming/ → streaming-service/ (already correct)
- [x] Add orphaned services (creators, moderation, uploads) to docker-compose.yml

## Priority 3: Technical Debt Resolution (MEDIUM)
### General Code Issues
- [~] Standardize datetime usage across all files (datetime.utcnow → datetime.now(timezone.utc)) — naive/aware column mismatches fixed (Aug 9); `utcnow` still in model defaults, runs on naive columns
- [x] Fix all Pydantic v2 issues (regex → pattern, min_items → min_length, from_orm → model_validate)
- [x] Fix mutable defaults (list, dict) in JSONB columns
- [ ] Replace blocking calls with asyncio.to_thread()

## Priority 4: Code Quality Improvements (LOW)
### Performance and Reliability
- [ ] Fix all repository pagination to use SELECT COUNT(*) instead of loading all rows
- [ ] Fix mutable default values in Allocation models
- [ ] Fix setting names inconsistencies
- [x] Fix rate limiting implementation across services — gateway limiter wired (Aug 9), auth-service login limiting

## Placeholder Memory Structure
- /home/phoenix/Desktop/wildframe/PROJECT_MEMORY/features/
- /home/phoenix/Desktop/wildframe/PROJECT_MEMORY/bugs/
- /home/phoenix/Desktop/wildframe/PROJECT_MEMORY/performance/
- /home/phoenix/Desktop/wildframe/PROJECT_MEMORY/security/
- /home/phoenix/Desktop/wildframe/PROJECT_MEMORY/api/
- /home/phoenix/Desktop/wildframe/PROJECT_MEMORY/database/
- /home/phoenix/Desktop/wildframe/PROJECT_MEMORY/history/
- /home/phoenix/Desktop/wildframe/PROJECT_MEMORY/planning/
- /home/phoenix/Desktop/wildframe/PROJECT_MEMORY/decisions/