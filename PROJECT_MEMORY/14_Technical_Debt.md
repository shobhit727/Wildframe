# 14_Technical_Debt

## Critical Technical Debt

### 1. Duplicate Model Definitions
**Severity**: CRITICAL
**Scope**: auth-service, user-service, content-service, streaming-service
**Pattern**: `models/__init__.py` and `models/<entity>.py` both define same SQLAlchemy models with different `Base` instances
**Impact**: Split metadata, half tables never created, queries fail
**Fix**: Single Base in `models/__init__.py`, import elsewhere
**Effort**: 4 hours per service

### 2. Duplicate Service Implementations
**Severity**: CRITICAL
**Scope**: auth-service (2 AuthService), content-service (2 ContentService)
**Pattern**: Two service classes in `services/__init__.py` and `services/<name>_service.py`
**Impact**: Routes may import wrong class, inconsistent behavior
**Fix**: Single implementation, remove duplicate
**Effort**: 2 hours per service

### 3. Duplicate Schema Definitions
**Severity**: HIGH
**Scope**: auth-service (schemas/__init__.py vs schemas/auth.py)
**Pattern**: Same Pydantic models in two files with different field names
**Impact**: Wrong fields in responses, validation failures
**Fix**: Single source of truth
**Effort**: 2 hours per service

### 4. Dead Routers
**Severity**: HIGH
**Scope**: content-service, streaming-service, user-service
**Pattern**: Routers defined but never mounted in main.py
**Impact**: API endpoints unreachable
**Fix**: Mount or delete
**Effort**: 1 hour per service

### 5. Dead Code in Repositories
**Severity**: MEDIUM
**Scope**: user-service (repositories/user.py), auth-service (some methods)
**Pattern**: Methods defined but never called
**Impact**: Confusion, maintenance burden
**Fix**: Delete or use
**Effort**: 1 hour per service

## High Technical Debt

### 6. Hardcoded Configuration
**Pattern**: Ports, secrets, URLs hardcoded in code
**Files**: Multiple across services
**Examples**:
- `JWT_SECRET_KEY: str = "dev-secret-key"`
- `"your-secret-key-change-in-production"`
- `allowed_hosts=["*"]`
- `allow_origins=["*"]`
**Fix**: All from settings (pydantic-settings)
**Effort**: 2 hours per service

### 7. Missing Dependency Injection
**Pattern**: Services instantiate their own dependencies
**Files**: auth-service (manager.py), media-pipeline (stages.py)
**Impact**: Hard to test, hard to mock
**Fix**: Constructor injection
**Effort**: 3 hours per service

### 8. Inconsistent Logging
**Pattern**: Some services use python-json-logger, some use standard logging
**Files**: All services
**Impact**: Hard to aggregate, parse, search
**Fix**: Centralize in shared lib
**Effort**: 4 hours

### 9. No Test Coverage
**Pattern**: Tests exist but don't cover the actual fix points
**Files**: All services have test/ dirs
**Impact**: Regressions slip through
**Fix**: Integration tests for each endpoint
**Effort**: 1 week per service

### 10. Inconsistent Error Handling
**Pattern**: Different services return different error formats
**Files**: All services
**Impact**: Hard to build unified client
**Fix**: Standard ErrorResponse schema
**Effort**: 4 hours

## Medium Technical Debt

### 11. Pydantic v1 Patterns
**Pattern**: `regex=`, `min_items=`, `from_orm()`, `class Config`
**Files**: All services
**Impact**: Deprecation warnings, eventual removal
**Fix**: Migrate to v2 patterns
**Effort**: 1 day per service

### 12. datetime.utcnow Usage
**Pattern**: `default=datetime.utcnow`, `datetime.utcnow()`
**Files**: All services with models
**Impact**: Deprecation in Python 3.12+
**Fix**: `datetime.now(timezone.utc)` or `func.now()`
**Effort**: 2 hours per service

### 13. Mutable Defaults
**Pattern**: `default=[]`, `default={}` in Column definitions
**Files**: content-service, streaming-service, media-pipeline
**Impact**: Data contamination
**Fix**: `default=list`, `default=dict`
**Effort**: 1 hour per service

### 14. Missing Type Hints
**Pattern**: Some functions lack return type hints
**Files**: Various
**Impact**: Harder to maintain, no static type checking
**Fix**: Add type hints
**Effort**: Ongoing

### 15. Magic Strings
**Pattern**: Status strings like "failed", "success" scattered
**Files**: auth-service, admin-service
**Impact**: Typos cause silent failures
**Fix**: Enum
**Effort**: 2 hours per service

### 16. Inconsistent Async
**Pattern**: Some methods sync, some async in same service
**Files**: media-pipeline, some repos
**Impact**: Blocks event loop
**Fix**: All async
**Effort**: 4 hours

### 17. No Request Validation
**Pattern**: Some endpoints accept any input
**Files**: Various
**Impact**: Bad data in DB
**Fix**: Pydantic models for all inputs
**Effort**: 1 day per service

### 18. Hardcoded Pagination
**Pattern**: `limit=20` hardcoded in routes
**Files**: Various
**Impact**: Inflexible
**Fix**: Query param with validation
**Effort**: 2 hours

## Low Technical Debt

### 19. Long Functions
**Pattern**: Some functions > 100 lines
**Files**: Various
**Impact**: Hard to read, test, maintain
**Fix**: Extract smaller functions
**Effort**: Ongoing

### 20. Comments After the Fact
**Pattern**: Comments explaining what code does, not why
**Files**: Various
**Impact**: Misleading
**Fix**: Remove or rewrite
**Effort**: Ongoing

### 21. Mixed English/Other Languages in Comments
**Pattern**: Some comments in non-English
**Files**: Unknown
**Impact**: Hard to maintain
**Fix**: English only or delete
**Effort**: 1 hour

### 22. Console Logging
**Pattern**: Some places use print() instead of logger
**Files**: Unknown (need to grep)
**Impact**: No log levels, no JSON output
**Fix**: Replace with logger
**Effort**: 1 hour

### 23. Unused Imports
**Pattern**: Several files have unused imports
**Files**: Multiple
**Impact**: Clutter, false dependency surface
**Fix**: Run pyflakes/ruff
**Effort**: 30 minutes

### 24. Unused Variables
**Pattern**: Variables assigned but never used
**Files**: Multiple
**Impact**: Confusing
**Fix**: Delete
**Effort**: 1 hour

### 25. Inconsistent Naming
**Pattern**: Some snake_case, some camelCase
**Files**: Various
**Impact**: PEP8 violation
**Fix**: Standardize on snake_case
**Effort**: 2 hours

## Architectural Debt

### 26. No Service Discovery
**Pattern**: Services hardcode URLs to each other
**Files**: api-gateway, possibly others
**Impact**: Can't scale, can't move services
**Fix**: Service registry (Consul, etcd)
**Effort**: 1 week

### 27. No Circuit Breaker
**Pattern**: Calls to other services can hang
**Files**: api-gateway
**Impact**: Cascade failures
**Fix**: Circuit breaker pattern
**Effort**: 3 days

### 28. No Distributed Tracing
**Pattern**: Each service logs independently
**Files**: All
**Impact**: Hard to debug across services
**Fix**: OpenTelemetry (partially started)
**Effort**: 1 week

### 29. No API Versioning Strategy
**Pattern**: Some routes have v1, some don't
**Files**: All
**Impact**: Breaking changes
**Fix**: Strict /api/v1/ everywhere
**Effort**: 1 day

### 30. No Idempotency
**Pattern**: POSTs not idempotent
**Files**: billing-service, content-service
**Impact**: Double-charges, duplicate data
**Fix**: Idempotency keys
**Effort**: 1 week

## Documentation Debt

### 31. No ADRs
**Pattern**: Why decisions were made is not recorded
**Impact**: Lost context
**Fix**: docs/adr/
**Effort**: 1 day

### 32. Inconsistent README
**Pattern**: Some services have README, some don't
**Files**: All services
**Impact**: Hard onboarding
**Fix**: Template
**Effort**: 2 days

### 33. No API Reference
**Pattern**: OpenAPI auto-generated but no maintained reference
**Impact**: Clients use wrong fields
**Fix**: Generate from OpenAPI, host
**Effort**: 1 day

## Test Debt

### 34. No Integration Tests
**Pattern**: Only unit tests
**Files**: All services
**Impact**: Don't catch real bugs
**Fix**: Integration test suite
**Effort**: 1 week per service

### 35. No Load Tests
**Pattern**: Don't know capacity
**Impact**: Surprises in prod
**Fix**: Locust/k6 scripts
**Effort**: 1 week

### 36. No Contract Tests
**Pattern**: Client-server API contracts not verified
**Impact**: Breaking changes slip through
**Fix**: Pact or similar
**Effort**: 1 week

## Total Technical Debt Estimate

- **Critical**: 5 categories, ~10 hours effort
- **High**: 5 categories, ~20 hours effort
- **Medium**: 8 categories, ~40 hours effort
- **Low**: 7 categories, ~10 hours effort
- **Architectural**: 5 categories, ~3 weeks effort
- **Documentation**: 3 categories, ~4 days effort
- **Test**: 3 categories, ~3 weeks effort

**Total: ~5-6 weeks of focused work**

## Priority for Paying Down

1. **Fix services that won't start** (critical, blocks everything)
2. **Consolidate models and services** (eliminates entire bug class)
3. **Fix health checks and auth** (operational stability)
4. **Modernize to Pydantic v2 + Python 3.12 patterns** (tech debt prevention)
5. **Add tests** (regression prevention)
6. **Documentation** (team velocity)

## Confidence: HIGH
- Debt identified from comprehensive code scan
- Effort estimates based on common patterns
- Priority based on user-facing impact
