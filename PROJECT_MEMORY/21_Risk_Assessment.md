# 21_Risk_Assessment

## CRITICAL RISKS (Service Will Not Start)

### 1. SyntaxError in main.py - services won't import
**Affected**: auth-service, user-service, streaming-service, admin-service
**Cause**: Indentation errors from `wire_observability` import at wrong indent level
**Impact**: All requests fail with 500
**Mitigation**: Fix indentation in main.py, move imports to top
**Confidence**: HIGH

### 2. Duplicate Base instances - split metadata
**Affected**: auth-service, user-service, content-service, streaming-service
**Cause**: Two `Base = declarative_base()` in __init__.py and submodule
**Impact**: Half the tables never created, queries fail with "relation does not exist"
**Mitigation**: Single Base in __init__.py, import from there
**Confidence**: HIGH

### 3. Missing Header() injection - all auth returns 401
**Affected**: auth-service, user-service, content-service, streaming-service
**Cause**: `authorization: Optional[str] = None` instead of `Header(None, alias="Authorization")`
**Impact**: No authenticated request can succeed
**Mitigation**: Use `Request` and extract from headers, or `Header()` annotation
**Confidence**: HIGH

### 4. Routers never mounted - all endpoints unreachable
**Affected**: content-service, streaming-service
**Cause**: main.py imports router from wrong file
**Impact**: Entire API surface is dead code
**Mitigation**: Mount correct router in create_app()
**Confidence**: HIGH

## HIGH RISKS (Broken Functionality)

### 5. Hardcoded JWT secret - tokens can be forged
**Affected**: auth-service, streaming-service, admin-service
**Cause**: `JWT_SECRET_KEY: str = "dev-secret-key"` default
**Impact**: Any attacker can forge valid tokens
**Mitigation**: Require env var, fail at startup if missing
**Confidence**: HIGH

### 6. User ID from query param - identity spoofing
**Affected**: content-service (rate_content)
**Cause**: `user_id: UUID = Query(...)` instead of from JWT
**Impact**: Any user can act as any other user
**Mitigation**: Extract user_id from authenticated context
**Confidence**: HIGH

### 7. get_current_admin_id() returns hardcoded string
**Affected**: admin-service
**Cause**: Stub implementation, no JWT validation
**Impact**: No actual authentication on admin endpoints
**Mitigation**: Real JWT validation
**Confidence**: HIGH

### 8. await on non-coroutine
**Affected**: auth-service (security/manager.py:189)
**Cause**: `await redis.asyncio.from_url(...)` but from_url is sync
**Impact**: TypeError at startup
**Mitigation**: Remove await
**Confidence**: HIGH

### 9. TokenManager infinite recursion
**Affected**: auth-service (security/__init__.py:172-183)
**Cause**: Instance methods shadow static methods, call self recursively
**Impact**: RecursionError on every auth call
**Mitigation**: Remove duplicate instance methods
**Confidence**: HIGH

## MEDIUM RISKS (Operational Issues)

### 10. Health check doesn't verify DB
**Affected**: streaming-service, admin-service, media-pipeline
**Cause**: Static response or `text("SELECT 1")` not used
**Impact**: k8s reports healthy when DB is down
**Mitigation**: Real DB check
**Confidence**: HIGH

### 11. Blocking calls in async context
**Affected**: media-pipeline (stages.py:173)
**Cause**: `cd.scan(path)` blocks event loop
**Impact**: All other requests stall
**Mitigation**: `await asyncio.to_thread(...)`
**Confidence**: HIGH

### 12. Pagination loads all rows
**Affected**: content-service (repositories/content.py)
**Cause**: `len(query.all())` for total
**Impact**: OOM on large tables
**Mitigation**: `SELECT COUNT(*)`
**Confidence**: HIGH

### 13. Mutable defaults in Column
**Affected**: content-service, streaming-service, media-pipeline
**Cause**: `default=[]`, `default={}` shared across instances
**Impact**: Data contamination between rows
**Mitigation**: `default=list`, `default=dict`
**Confidence**: MEDIUM

## LOW RISKS (Technical Debt)

### 14. Deprecated datetime.utcnow
**Affected**: All services with models
**Cause**: Python 3.12+ deprecation
**Impact**: DeprecationWarning, removal in 3.14
**Mitigation**: `datetime.now(timezone.utc)`
**Confidence**: HIGH

### 15. Pydantic v1 patterns in v2
**Affected**: All services with schemas
**Cause**: `regex=`, `min_items=`, `from_orm()`, `class Config`
**Impact**: DeprecationWarning, eventual removal
**Mitigation**: `pattern=`, `min_length=`, `model_validate()`, `model_config`
**Confidence**: HIGH

### 16. Empty service directories
**Affected**: billing, notification, search, recommendation, analytics
**Cause**: Wrong directory structure (compose uses non-suffixed, AGENTS says suffixed)
**Impact**: Confusion, no implementation
**Mitigation**: Rename to match compose or AGENTS
**Confidence**: HIGH

## INFRASTRUCTURE RISKS

### 17. Docker compose references empty directories
**Affected**: streaming-service
**Cause**: `./services/streaming-service:/app` but dir is empty
**Impact**: Container build fails or runs nothing
**Mitigation**: Move code from services/streaming/ to services/streaming-service/
**Confidence**: HIGH

### 18. Python version mismatch
**Affected**: admin-service, media-pipeline
**Cause**: `python = "^3.14"` (doesn't exist) vs Dockerfile 3.11
**Impact**: Poetry install fails
**Mitigation**: Change to `^3.11`
**Confidence**: HIGH

### 19. No observability SDK dependency
**Affected**: media-pipeline
**Cause**: Imports `wildframe_observability.wire` but not in pyproject.toml
**Impact**: ImportError at startup
**Mitigation**: Add to dependencies
**Confidence**: HIGH

## SECURITY RISKS

### 20. SQL injection potential
**Affected**: services using f-strings in queries
**Cause**: Direct user input in SQL
**Impact**: Data breach
**Mitigation**: Always use SQLAlchemy parameterized queries
**Confidence**: MEDIUM (need to verify each query)

### 21. CORS wildcard
**Affected**: services allowing all origins
**Cause**: `allow_origins=["*"]` with credentials
**Impact**: CSRF attacks
**Mitigation**: Specific origins list
**Confidence**: MEDIUM

### 22. TrustedHost wildcard
**Affected**: content-service
**Cause**: `allowed_hosts=["*"]`
**Impact**: Host header injection
**Mitigation**: Specific hosts
**Confidence**: MEDIUM

## DEPENDENCY RISKS

### 23. Two JWT libraries
**Affected**: auth-service
**Cause**: `python-jose` and `pyjwt` both imported in different files
**Impact**: Subtle token validation differences
**Mitigation**: Standardize on one
**Confidence**: HIGH

### 24. aioredis vs redis
**Affected**: services using Redis
**Cause**: aioredis is unmaintained
**Impact**: Deprecation, security vulnerabilities
**Mitigation**: Use redis.asyncio
**Confidence**: HIGH (per AGENTS.md)

## SCALABILITY RISKS

### 25. No connection pooling config
**Affected**: services using NullPool in dev
**Cause**: NullPool in production code paths
**Impact**: Performance issues under load
**Mitigation**: Conditional pooling
**Confidence**: MEDIUM

### 26. Sync DB calls
**Affected**: services mixing sync and async
**Cause**: Some code paths use sync DB
**Impact**: Blocks event loop
**Mitigation**: All async
**Confidence**: MEDIUM

## MAINTENANCE RISKS

### 27. Dead code throughout
**Affected**: all services
**Cause**: Multiple implementations, orphaned modules
**Impact**: Confusion, accidental use of wrong code
**Mitigation**: Delete dead code, single source of truth
**Confidence**: HIGH

### 28. No version constraints
**Affected**: services with old patterns
**Cause**: Python 3.11+ but using 3.12+ features
**Impact**: Compatibility issues
**Mitigation**: Clear version targets
**Confidence**: MEDIUM

## OVERALL RISK PROFILE

**Critical risks**: 4 (services won't start)
**High risks**: 5 (broken functionality)
**Medium risks**: 4 (operational issues)
**Low risks**: 3 (technical debt)
**Infrastructure**: 3
**Security**: 3
**Dependencies**: 2
**Scalability**: 2
**Maintenance**: 2

**Total: ~28 risk categories, ~130 individual bugs**

## RISK MITIGATION PRIORITY

1. **Fix all CRITICAL (services won't start)** - 1 day effort
2. **Fix all HIGH (broken functionality)** - 1 week effort
3. **Clean up directory structure** - 1 day effort
4. **Fix MEDIUM (operational)** - 1 week effort
5. **Address LOW (tech debt)** - ongoing

## Confidence: HIGH
- Risks identified from comprehensive code scan
- Severity ratings based on actual impact analysis
- Mitigations are standard solutions
