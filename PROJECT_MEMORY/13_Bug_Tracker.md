# 13_Bug_Tracker

## Bug Severity Definitions

- **CRITICAL**: Service won't start, security breach, data loss
- **HIGH**: Major functionality broken, frequent runtime errors
- **MEDIUM**: Operational issues, deprecation warnings
- **LOW**: Minor issues, code smell, dead code

## Bug Status

- **OPEN**: Identified, not yet fixed
- **IN_PROGRESS**: Currently being worked on
- **FIXED**: Fixed and verified
- **WONTFIX**: Acknowledged but not fixing

---

## auth-service

### CRITICAL (Service won't start)

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| AUTH-001 | app/main.py:210 | FIXED | IndentationError - wire_observability at 0-indent |
| AUTH-002 | app/main.py:217 | OPEN | NameError - wire_observability imported inside if __name__ |
| AUTH-003 | app/models/__init__.py + user.py | OPEN | Two Base instances - split metadata |
| AUTH-004 | app/repositories/__init__.py:103 | OPEN | token_hash passed to RefreshToken which has 'token' column |
| AUTH-005 | app/repositories/__init__.py:172-176 | OPEN | LoginAudit.status doesn't exist on __init__.py model |
| AUTH-006 | app/repositories/__init__.py:194-197 | OPEN | Query references non-existent LoginAudit.status |
| AUTH-007 | app/api/routes/auth.py:62-63 | OPEN | authorization: Optional[str] = None - no Header() injection |
| AUTH-008 | app/api/routes/__init__.py | OPEN | auth.py router never included |
| AUTH-009 | app/security/__init__.py:172-183 | OPEN | TokenManager instance methods shadow static methods (recursion) |
| AUTH-010 | app/security/manager.py:189 | OPEN | await on non-coroutine redis.asyncio.from_url() |

### HIGH

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| AUTH-011 | app/api/routes/__init__.py:38-46 | OPEN | PasswordManager/TokenManager instantiated but used as static |
| AUTH-012 | app/security/__init__.py + manager.py | OPEN | JWT claim key mismatch (user_id vs sub) |
| AUTH-013 | app/services/__init__.py + auth_service.py | OPEN | Two conflicting AuthService classes |
| AUTH-014 | app/schemas/__init__.py + auth.py | OPEN | Two conflicting schema definitions |
| AUTH-015 | app/core/settings.py:32 | OPEN | Hardcoded JWT secret 'dev-secret-key' |
| AUTH-016 | app/main.py:217 | OPEN | LOG_LEVEL might not exist (FIXED - it does) |
| AUTH-017 | (multiple) | OPEN | All auth endpoints unreachable due to router issue |

### MEDIUM

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| AUTH-018 | app/services/__init__.py:86 | OPEN | UUID(int=0) sentinel for unknown user |
| AUTH-019 | (multiple) | OPEN | datetime.utcnow usage (deprecated) |
| AUTH-020 | app/models/__init__.py:94 | OPEN | UniqueConstraint on (email, is_active) allows duplicates |
| AUTH-021 | (multiple) | OPEN | UserResponse.from_orm() deprecated in Pydantic v2 |
| AUTH-022 | app/security/__init__.py:24-28, manager.py | OPEN | Two different RateLimiter implementations |
| AUTH-023 | app/api/routes/auth.py:41 | OPEN | Module-level RateLimiter singleton |

### LOW

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| AUTH-024 | app/security/__init__.py:141 | OPEN | Unreachable `import hashlib` after return |
| AUTH-025 | app/repositories/user_repository.py:97,108 | OPEN | __import__('datetime') anti-pattern |
| AUTH-026 | app/services/__init__.py:175 | OPEN | Hardcoded expires_in=900 |
| AUTH-027 | app/core/logging.py:83-90 | OPEN | File handler doesn't create logs/ dir |
| AUTH-028 | app/security/__init__.py vs manager.py | OPEN | python-jose vs pyjwt (two JWT libs) |
| AUTH-029 | app/core/settings.py:18 | OPEN | DEBUG=True default |
| AUTH-030 | app/telemetry/__init__.py:38 | OPEN | FastAPIInstrumentor.instrument() without app |
| AUTH-031 | app/models/__init__.py:86 | OPEN | last_login_ip column never populated |

---

## user-service

### CRITICAL (Service won't start)

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| USR-001 | app/main.py:142 | OPEN | IndentationError - wire_observability at 0-indent |
| USR-002 | app/api/routes/user.py:38-57 | OPEN | Missing Header() injection - all auth returns 401 |
| USR-003 | app/models/__init__.py:17,55,99,142 | OPEN | Duplicate index name 'idx_user_id' on 4 tables |
| USR-004 | app/security/manager.py:24 | OPEN | settings.PASSWORD_BCRYPT_ROUNDS doesn't exist |
| USR-005 | app/api/routes/__init__.py | OPEN | No authentication on any route |
| USR-006 | app/models/__init__.py + user.py | OPEN | Two Base instances + duplicate model definitions |

### HIGH

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| USR-007 | app/api/routes/__init__.py | OPEN | routes/user.py never included (dead code) |
| USR-008 | app/models/user.py:48-49 | OPEN | datetime.utcnow in DateTime(timezone=True) columns |

### MEDIUM

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| USR-009 | app/tests/conftest.py:18-23 | OPEN | Deprecated event_loop fixture override |
| USR-010 | app/schemas/__init__.py + user.py | OPEN | Two ErrorResponse with different fields |
| USR-011 | app/core/database.py:29-38 | OPEN | NullPool with pool_size kwargs |
| USR-012 | app/services/__init__.py (12+ locations) | OPEN | Deprecated UserResponse.from_orm() |
| USR-013 | app/services/__init__.py (12+ locations) | OPEN | Mid-transaction commits prevent rollback |

### LOW

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| USR-014 | app/schemas/user.py:6 | OPEN | Unused EmailStr/field_validator imports |
| USR-015 | app/api/routes/user.py:23 | OPEN | Unused ErrorResponse import |

---

## content-service

### CRITICAL (Service won't start)

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| CON-001 | app/main.py:149 | OPEN | settings.LOG_LEVEL doesn't exist |
| CON-002 | app/core/database.py:86 | OPEN | AsyncGenerator not imported (NameError) |
| CON-003 | app/core/database.py:68 | OPEN | Raw string to conn.execute() |
| CON-004 | app/models/__init__.py + content.py | OPEN | Two Base instances |
| CON-005 | app/models/__init__.py + content.py | OPEN | Conflicting Genre/Season/Episode/ContentType classes |
| CON-006 | app/services/__init__.py + content.py | OPEN | Two incompatible ContentService classes |
| CON-007 | app/api/routes/content.py | OPEN | Router never mounted - all endpoints unreachable |

### HIGH

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| CON-008 | app/schemas/content.py:83,156 | OPEN | min_items=1 (Pydantic v2 uses min_length) |
| CON-009 | app/api/routes/content.py + services/content.py + repos/content.py | OPEN | Entire subsystem is orphaned dead code |

### MEDIUM

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| CON-010 | app/core/database.py:39-41 | OPEN | NullPool with pool_size/max_overflow |
| CON-011 | app/models/__init__.py:89,123 | OPEN | Mutable defaults on JSONB columns |
| CON-012 | app/models/__init__.py:126-127+ | OPEN | datetime.utcnow deprecated |
| CON-013 | app/repositories/content.py (10+ locations) | OPEN | Total count loads all rows into memory |
| CON-014 | app/repositories/content.py:129+ | OPEN | Cannot set fields to None in update |
| CON-015 | app/services/__init__.py:197 | OPEN | datetime.utcnow deprecated |

### LOW

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| CON-016 | app/api/routes/__init__.py:120 | OPEN | Route bypasses service layer |
| CON-017 | app/schemas/content.py:210,253 | OPEN | regex= deprecated (Pydantic v2) |
| CON-018 | app/main.py:68 | OPEN | TrustedHostMiddleware allowed_hosts=['*'] |
| CON-019 | app/schemas/content.py:262-266 | OPEN | ErrorResponse field mismatch with __init__.py |

### HIGH (Security)

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| CON-020 | app/api/routes/__init__.py:317 | OPEN | user_id from query param enables identity spoofing |

---

## streaming-service

### CRITICAL (Service won't start)

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| STR-001 | app/main.py:26-27 | OPEN | IndentationError - wire_observability inside nothing |
| STR-002 | app/main.py:46 | OPEN | wire_observability imported inside if __name__ (NameError) |
| STR-003 | app/api/streaming_routes.py:11-17 | OPEN | Wrong StreamingService constructor (4 args vs 1) |
| STR-004 | app/api/streaming_routes.py:24,31,40,47,54,61 | OPEN | Methods don't exist on StreamingService |
| STR-005 | app/core/database.py:61 | OPEN | Raw string to conn.execute() |
| STR-006 | app/models/__init__.py + streaming.py | OPEN | Two Base instances - split metadata |
| STR-007 | app/main.py | OPEN | Only broken router mounted; working router is dead code |

### HIGH

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| STR-008 | app/repositories/__init__.py:219-220 | OPEN | session_id stored in content_id column |
| STR-009 | app/repositories/__init__.py:278 | OPEN | WatchHistoryRepository aliases wrong class |
| STR-010 | app/tests/test_streaming_service.py:18,31,43,52 | OPEN | Wrong constructor + missing methods in tests |
| STR-011 | app/core/settings.py:14 | OPEN | Hardcoded JWT secret |

### MEDIUM

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| STR-012 | app/schemas/__init__.py:40,41,124,206,214 | OPEN | regex= deprecated |
| STR-013 | app/api/routes/__init__.py:108 | OPEN | Query regex= deprecated |
| STR-014 | app/main.py:29-32 | OPEN | /health doesn't verify DB |
| STR-015 | app/main.py:34,39 | OPEN | Deprecated on_event |
| STR-016 | app/models/__init__.py:107,108,151,191,222,264 | OPEN | Mutable defaults |

### LOW

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| STR-017 | app/schemas/streaming.py | OPEN | class Config vs model_config inconsistency |
| STR-018 | app/api/routes/streaming.py | OPEN | Duplicate router with __init__.py |

---

## admin-service

### CRITICAL (Service won't start)

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| ADM-001 | app/main.py:12-13 | OPEN | SyntaxError - indented at module level |
| ADM-002 | app/main.py:23-24 | OPEN | Imports at wrong indent level |
| ADM-003 | app/main.py:23 | OPEN | app.core.settings module doesn't exist |
| ADM-004 | app/api/routes/admin.py:15-17 | OPEN | get_db() returns None |
| ADM-005 | app/api/routes/admin.py:20-22 | OPEN | get_current_admin_id() hardcoded "admin_user_123" |

### HIGH

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| ADM-006 | app/main.py:4 | OPEN | No create_app() factory |
| ADM-007 | app/main.py | OPEN | No lifespan context manager |
| ADM-008 | app/main.py:16-18 | OPEN | /health doesn't verify DB |
| ADM-009 | app/services/admin.py:68-73 | OPEN | flag_content missing content_type, flagged_at, created_at |
| ADM-010 | app/services/admin.py:79-84 | OPEN | resolve_content_flag missing fields |
| ADM-011 | app/services/admin.py:88-97 | OPEN | list_flagged_content missing fields |
| ADM-012 | app/services/admin.py:225-236 | OPEN | get_system_stats returns hardcoded zeros |
| ADM-013 | app/config.py | OPEN | os.getenv with hardcoded fallback secrets |

### MEDIUM

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| ADM-014 | app/api/routes/admin.py:12 | OPEN | Router prefix /api/admin not /api/v1/admin |
| ADM-015 | app/services/admin.py:42-49 | OPEN | get_user_moderation_history missing fields |
| ADM-016 | app/services/admin.py:53-62 | OPEN | list_moderated_users missing fields |
| ADM-017 | app/services/admin.py:102-109 | OPEN | create_alert missing fields |
| ADM-018 | app/services/admin.py:129-135 | OPEN | acknowledge_alert missing fields |
| ADM-019 | app/services/admin.py:161-167 | OPEN | set_config missing fields |
| ADM-020 | app/services/admin.py:173-179 | OPEN | get_config missing fields |
| ADM-021 | app/services/admin.py:183-192 | OPEN | list_configs missing fields |
| ADM-022 | app/repositories/admin.py:108-115 | OPEN | acknowledged_at never set |
| ADM-023 | app/models/admin.py:17-19 | OPEN | datetime.utcnow deprecated |
| ADM-024 | pyproject.toml:19 | OPEN | python = "^3.14" (doesn't exist) |
| ADM-025 | Dockerfile:3 | OPEN | python:3.11-slim (conflicts with ^3.14) |

### LOW

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| ADM-026 | app/models/admin.py:22-26 | OPEN | Duplicate indexes |
| ADM-027 | app/tests/conftest.py:7 | OPEN | Hardcoded DB credentials |

---

## media-pipeline

### CRITICAL (Service won't start)

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| MED-001 | app/models.py:50-81 | OPEN | PipelineJob has no created_at column |
| MED-002 | app/core/database.py:30 | OPEN | Raw string to conn.execute() |
| MED-003 | app/services.py:142,200-202 | OPEN | Transient job.context not persisted |
| MED-004 | pyproject.toml:19 | OPEN | python = "^3.14" doesn't exist |
| MED-005 | pyproject.toml | OPEN | wildframe-observability-sdk not declared |
| MED-006 | app/core/stages.py:173-184 | OPEN | Blocking ClamavScanner.scan() |
| MED-007 | app/main.py:26-33 | OPEN | /health doesn't verify DB |

### MEDIUM

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| MED-008 | app/core/logging.py:21-22 | OPEN | set_correlation_id is no-op |
| MED-009 | app/tests/conftest.py:18-22 | OPEN | Deprecated event_loop fixture |
| MED-010 | app/api/media_pipeline_routes.py:170 | OPEN | Returns enum not value |
| MED-011 | app/api/media_pipeline_routes.py:38-43,75-91 | OPEN | Redundant upload_session_id |

### LOW

| ID | File:Line | Status | Description |
|----|-----------|--------|-------------|
| MED-012 | app/api/media_pipeline_routes.py:144-155 | OPEN | /media/transcode uses two Body() without embed |
| MED-013 | app/repositories.py:42-45 | OPEN | save() does flush() not commit() |

---

## Empty Services

| Service | Status | Action |
|---------|--------|--------|
| services/billing-service/ | EMPTY | Delete (compose uses services/billing/) |
| services/notification-service/ | EMPTY | Delete (compose uses services/notification/) |
| services/search-service/ | EMPTY | Delete (compose uses services/search/) |
| services/recommendation-service/ | EMPTY | Delete (compose uses services/recommendation/) |
| services/analytics-service/ | EMPTY | Delete (compose uses services/analytics/) |

## Directory Anomalies

| Issue | Status | Action |
|-------|--------|--------|
| services/streaming/ exists but not in compose | OPEN | Delete (orphaned) |
| services/streaming-service/ empty but in compose | OPEN | Move code from streaming/ |
| netflix_backend/ (legacy Django) | OPEN | Delete |

---

## Cross-Cutting Issues

### DateTime Deprecation
**Status**: OPEN
**Scope**: All services with models
**Count**: ~50+ occurrences
**Files**: Multiple across services
**Fix**: `datetime.utcnow` → `datetime.now(timezone.utc)` or `func.now()`

### Pydantic v1 Patterns
**Status**: OPEN
**Scope**: All services with schemas
**Count**: ~30+ occurrences
**Patterns**:
- `regex=` → `pattern=`
- `min_items=` → `min_length=`
- `from_orm()` → `model_validate()`
- `class Config` → `model_config`
**Fix**: Migrate all to Pydantic v2

### Mutable Defaults in Columns
**Status**: OPEN
**Scope**: content-service, streaming-service, media-pipeline
**Count**: ~15 occurrences
**Fix**: `default=[]` → `default=list`, `default={}` → `default=dict`

### Hardcoded Secrets
**Status**: OPEN
**Scope**: auth-service, streaming-service, admin-service
**Fix**: Use pydantic-settings with required env vars, fail at startup

---

## Bug Statistics

| Service | Critical | High | Medium | Low | Total |
|---------|----------|------|--------|-----|-------|
| auth-service | 10 | 7 | 6 | 8 | 31 |
| user-service | 6 | 2 | 5 | 2 | 15 |
| content-service | 7 | 2 | 6 | 4 | 19+1sec |
| streaming-service | 7 | 4 | 5 | 2 | 18 |
| admin-service | 5 | 7 | 12 | 2 | 26 |
| media-pipeline | 7 | 0 | 4 | 2 | 13 |
| **TOTAL** | **42** | **22** | **38** | **20** | **122** |

Plus ~30 cross-cutting issues and 7 directory anomalies.

## Bug Tracking Discipline

- Add new bugs as discovered
- Update status as work progresses
- Close with reference to commit hash when fixed
- Tag related bugs together
- Prioritize by user-facing impact

## Confidence: HIGH
- Bugs identified from comprehensive code scan
- Each bug has file:line reference
- Severity ratings based on actual impact
