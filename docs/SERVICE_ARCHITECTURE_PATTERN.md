# 🧩 Service Architecture Pattern

Every microservice in Wildframe follows the same internal architecture. This document is the canonical reference so contributors can move between services without re-learning the layout.

**Last Updated**: June 4, 2026
**Status**: Production-Ready

---

## Why a Shared Pattern?

- **Predictability** — Every service has the same shape, so onboarding takes hours, not days.
- **Tooling** — One generator, one test runner, one linter, one CI job works for all 12 services.
- **Refactor safety** — Changes in patterns propagate cleanly because every service uses the same abstractions.

---

## High-Level Layout

```
HTTP request
   │
   ▼
┌──────────────┐
│  api/routes  │   ← FastAPI router, request/response models, status codes
└──────┬───────┘
       │ (DTO in, DTO out)
       ▼
┌──────────────┐
│   services   │   ← Business logic, validation, orchestration
└──────┬───────┘
       │ (domain model)
       ▼
┌──────────────┐
│ repositories │   ← SQLAlchemy queries, transactions, locking
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   models     │   ← ORM tables
└──────────────┘
```

The API layer **must not** know about ORM models. The repository layer **must not** import FastAPI. This keeps each layer independently testable.

---

## Directory Structure

```
services/<service>/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app factory, lifespan, router registration
│   ├── core/
│   │   ├── config.py            # Pydantic settings (env-driven)
│   │   ├── security.py          # JWT, password hashing, scopes
│   │   ├── logging.py           # Structured JSON logging
│   │   ├── tracing.py           # OpenTelemetry setup
│   │   └── database.py          # Async engine + session factory
│   ├── models/                  # SQLAlchemy declarative models
│   │   └── <domain>.py
│   ├── repositories/            # Pure data access, one file per aggregate
│   │   └── <domain>.py
│   ├── services/                # Business logic, one file per use case
│   │   └── <domain>_service.py
│   ├── api/
│   │   ├── deps.py              # FastAPI dependencies (auth, db, services)
│   │   └── routes.py            # Routers
│   ├── schemas/                 # Pydantic request/response models
│   │   └── <domain>.py
│   ├── middleware/              # Service-specific middleware (rate limit, etc.)
│   └── tests/
│       ├── conftest.py
│       ├── test_<domain>_service.py
│       └── test_<domain>_integration.py
├── Dockerfile
├── pyproject.toml
└── README.md
```

---

## Layer Responsibilities

### `main.py` — Composition Root

```python
from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.core.config import settings
from app.core.database import engine
from app.api.routes import router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    yield
    # shutdown
    await engine.dispose()

def create_app() -> FastAPI:
    app = FastAPI(title=settings.service_name, lifespan=lifespan)
    app.include_router(router, prefix="/<service-prefix>")
    return app

app = create_app()
```

`main.py` is the **only** place that wires concrete classes together. Everything else receives its dependencies.

### `core/` — Cross-Cutting Concerns

- **`config.py`** — `BaseSettings` subclass. Every env var the service reads lives here.
- **`security.py`** — JWT encode/decode, password hashing (bcrypt), permission scopes.
- **`logging.py`** — JSON formatter, correlation-id injection.
- **`tracing.py`** — `tracer = trace.get_tracer(__name__)` + OTLP exporter config.
- **`database.py`** — `create_async_engine`, `async_sessionmaker`, `get_session` dependency.

### `models/` — Persistence

- SQLAlchemy 2.0 declarative base.
- One file per aggregate root. Example: `models/user.py`, `models/session.py`.
- Use `Mapped[T]` + `mapped_column(...)` (the modern typed API).
- Always include `created_at`, `updated_at`, `is_active` for soft deletes.

### `repositories/` — Data Access

Repositories are **plain classes**, not dataclasses, with an `AsyncSession` injected via constructor:

```python
class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, user_id: UUID) -> User | None: ...
    async def get_by_email(self, email: str) -> User | None: ...
    async def create(self, user: User) -> User: ...
    async def delete(self, user_id: UUID) -> None: ...
```

Repositories own:
- Query construction
- Transaction boundaries (`session.commit()`)
- Eager-loading strategies
- Locking (`with_for_update`)

They do **not** know about HTTP, auth, or business rules.

### `services/` — Business Logic

Services orchestrate repositories and external clients:

```python
class UserService:
    def __init__(self, user_repo: UserRepository, audit_repo: AuditRepository):
        self.user_repo = user_repo
        self.audit_repo = audit_repo

    async def register(self, dto: RegisterDTO) -> User:
        existing = await self.user_repo.get_by_email(dto.email)
        if existing:
            raise EmailAlreadyTaken()
        user = User(email=dto.email, password_hash=hash_password(dto.password))
        await self.user_repo.create(user)
        await self.audit_repo.log("user.registered", user.id)
        return user
```

**Rules**:
- A service method either **succeeds and returns a domain object** or **raises a domain exception**. No silent `None` returns.
- Cross-cutting concerns (events, metrics) happen here, not in the repository.
- Services can call other services, but only through their public interface (not by reaching into a repository the other service owns).

### `api/routes.py` — HTTP Boundary

Routes are **thin**: parse, call service, serialize. Example:

```python
@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    svc: UserService = Depends(get_user_service),
) -> UserResponse:
    user = await svc.register(payload)
    return UserResponse.model_validate(user)
```

Status codes are **declared**, not hard-coded — `201` for create, `204` for delete, `401` for unauthorized, etc.

### `api/deps.py` — Dependency Wiring

Every service exposes a small set of `Depends(...)` providers that:
1. Open a DB session.
2. Construct the repository.
3. Construct the service.
4. Yield, then close the session in a `finally`.

This is what `tests/conftest.py` overrides to inject mocks.

---

## Cross-Cutting Concerns

| Concern | Where it lives | Example |
|---|---|---|
| Auth (JWT verify) | `core/security.py` + `api/deps.py` | `current_user: User = Depends(get_current_user)` |
| Authorization (RBAC) | Service-level guard | `if user.role not in ALLOWED: raise Forbidden()` |
| Rate limiting | `middleware/rate_limit.py` | Sliding window via Redis |
| Logging | `core/logging.py` | `logger.info("user.registered", extra={"user_id": ...})` |
| Tracing | `core/tracing.py` | `with tracer.start_as_current_span("..."):` |
| Metrics | Service-level | `METRICS_REQUESTS.labels(service=...).inc()` |
| Caching | Repository-level decorator | `@cache(ttl=60, key="user:{id}")` |
| Retries / circuit breakers | HTTP client wrappers | `httpx.AsyncClient` + `tenacity` |

---

## Inter-Service Communication

| Pattern | When | Tool |
|---|---|---|
| Synchronous request/response | Need an answer now (e.g. token verify) | HTTP via `httpx.AsyncClient` |
| Asynchronous events | Fire-and-forget (e.g. user registered → send welcome email) | Kafka topic |
| Shared data | Read-only reference data (e.g. content catalog in recommendations) | REST fetch with cache |
| Service mesh | mTLS, retries, observability | Linkerd (production) |

The API Gateway is the only entry point for external clients. Service-to-service calls **bypass** the gateway and go direct.

---

## Error Model

Each service defines a small exception hierarchy:

```python
class DomainError(Exception): ...
class NotFound(DomainError): ...
class AlreadyExists(DomainError): ...
class Forbidden(DomainError): ...
class ValidationFailed(DomainError):
    def __init__(self, errors: dict): self.errors = errors
```

A single exception handler in `main.py` converts these to RFC-7807 problem-detail responses with the right HTTP status.

---

## Settings & Configuration

All env-driven, no hard-coded values:

```python
class Settings(BaseSettings):
    service_name: str = "auth-service"
    database_url: str
    jwt_secret: str
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 7
    rate_limit_per_minute: int = 60
    redis_url: str = "redis://redis:6379/0"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="WILDFRAME_")
```

`WILDFRAME_AUTH_SERVICE_DATABASE_URL` style. One prefix per service.

---

## Testing

See [TEST_GUIDE.md](TEST_GUIDE.md) for the full playbook. The pattern at a glance:

- **Unit tests** mock repositories. `services/<x>_service.py` is tested in isolation.
- **Integration tests** use the real FastAPI app + a test DB.
- **HTTP tests** use `httpx.AsyncClient(transport=ASGITransport(app))`.

---

## Anti-Patterns (Do Not)

❌ **Importing ORM models from routes** — couples HTTP to schema.
❌ **Logic in repositories** — repositories are queries, not business rules.
❌ **`@app.get("/foo")` registered in `main.py`** — all routes live in `api/routes.py`.
❌ **Cross-service direct DB access** — go through the owning service's API.
❌ **Global mutable state** — pass everything through `Depends(...)`.

---

## Related

- [ARCHITECTURE.md](ARCHITECTURE.md) — System-wide architecture
- [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) — Schema per service
- [TEST_GUIDE.md](TEST_GUIDE.md) — How to test this layout
- [CONTRIBUTING.md](CONTRIBUTING.md) — Conventions enforced in PR review
