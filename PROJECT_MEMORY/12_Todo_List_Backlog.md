# 12_Todo_List_Backlog

## Priority 1: Critical Bug Fixes (Services Won't Start)
### auth-service (HIGH_URGENT)
- [x] Fix indentation error in main.py line 210 (wire_observability call)
- [x] Make settings.LOG_LEVEL accessible (confirmed exists in settings.py)
- [ ] Fix duplicate model sets (models/__init__.py vs user.py)
- [ ] Fix dead auth router (routes/__init__.py doesn't include auth.py)
- [ ] Fix authentication middleware (get_current_user no Header() injection)
- [ ] Fix self-including router issue
- [ ] Add proper JWT validation to routes

### user-service (HIGH_URGENT)
- [ ] Fix indentation error in main.py line 142
- [ ] Eliminate duplicate models (__init__.py vs user.py)
- [ ] Fix broken auth dependency (__init__.py doesn't import routes properly)
- [ ] Make JWT validation work properly
- [ ] Fix index name conflicts

### content-service (MEDIUM_URGENT)
- [ ] Fix LOG_LEVEL setting (auth needed it, content still missing in settings)
- [ ] Fix duplicate model sets (__init__.py vs content.py)
- [ ] Mount missing router in main.py
- [ ] Fix Pydantic v2 issues (regex vs pattern, min_items vs min_length)
- [ ] Fix health check misuse of raw strings

### streaming-service (HIGH_URGENT)
- [x] Fix syntax error in main.py line 210 (wire_observability indentation)
- [ ] Fix routing model lookup Issues (wrong StreamingService constructor)
- [ ] Fix dual Base instances
- [ ] Fix broken auth dependency (Header() injection)
- [ ] Fix model naming conflicts (duplicate Genre definitions)
- [ ] Fix missing LOG_LEVEL import

### admin-service (MEDIUM_URGENT)
- [ ] Restructure create_app() pattern proper FastAPI lifecycle
- [ ] Setup core settings module properly
- [ ] Implement real time_db dependency injection
- [ ] Implement Juniper auth properly with JWT
- [ ] Add proper logging configuration
- [ ] Fix missing/show summaries in click_tool_search
- [ ] Fix alert response models to include all required fields

## Priority 2: Infrastructure Cleanup
### Directory Structure (MEDIUM)
- [ ] Delete netflix_backend (legacy dead code)
- [ ] Delete empty -service directories (analytics-service, billing-service, notification-service, recommendation-service, search-service)
- [ ] Rename analytics/ → analytics-service/
- [ ] Rename billing/ → billing-service/
- [ ] Rename notification/ → notification-service/
- [ ] Rename recommendation/ → recommendation-service/
- [ ] Rename search/ → search-service/
- [ ] Rename streaming/ → streaming-service/ (already correct)
- [ ] Add orphaned services (creators, moderation, uploads) to docker-compose.yml

## Priority 3: Technical Debt Resolution (MEDIUM)
### General Code Issues
- [ ] Standardize datetime usage across all files (datetime.utcnow → datetime.now(timezone.utc))
- [ ] Fix all Pydantic v2 issues (regex → pattern, min_items → min_length, from_orm → model_validate)
- [ ] Fix mutable defaults (list, dict) in JSONB columns
- [ ] Replace blocking calls with asyncio.to_thread()

## Priority 4: Code Quality Improvements (LOW)
### Performance and Reliability
- [ ] Fix all repository pagination to use SELECT COUNT(*) instead of loading all rows
- [ ] Fix mutable default values in Allocation models
- [ ] Fix setting names inconsistencies
- [ ] Fix rate limiting implementation across services

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