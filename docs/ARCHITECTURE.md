# 🏗️ Wildframe Platform Architecture

**Version**: 1.0.0  
**Last Updated**: May 28, 2026  
**Stability**: Active development — not production-ready (see `STATUS.md`)

> Some sections below are aspirational design notes retained for history.
> For how the repo is actually built today, `AGENTS.md` and `README.md` are
> authoritative.

## Overview

Wildframe is a production-grade OTT (Over-The-Top) streaming platform built on a distributed microservices architecture. It handles video streaming, user authentication, content management, recommendations, billing, and analytics at scale.

**Key Stats**:
- 15 microservices
- 16 databases (database-per-service pattern)
- 5 infrastructure services (caching, messaging, search)
- 4 observability services (metrics, logs, tracing, profiling)

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Microservices](#microservices)
3. [Data Architecture](#data-architecture)
4. [Communication Patterns](#communication-patterns)
5. [Security Model](#security-model)
6. [Scalability](#scalability)
7. [High Availability](#high-availability)
8. [Monitoring & Observability](#monitoring--observability)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Client Layer                         │
│        (Web Browser, Mobile Apps, Smart TVs)           │
└────────────────────┬────────────────────────────────────┘
                     │ HTTPS
┌─────────────────────▼────────────────────────────────────┐
│                  API Gateway                             │
│           (Request routing, Auth validation)             │
└────────┬──────────────┬──────────────┬──────────────────┘
         │              │              │
    ┌────▼────┐    ┌────▼────┐   ┌────▼────┐
    │  Auth   │    │  User   │   │ Content │
    │ Service │    │ Service │   │ Service │
    └────┬────┘    └────┬────┘   └────┬────┘
         │              │             │
    ┌────▼──────────────▼─────────────▼────┐
    │    Shared Infrastructure              │
    │  ├─ PostgreSQL (12 databases)        │
    │  ├─ Redis (caching & sessions)       │
    │  ├─ Kafka (event streaming)          │
    │  ├─ Elasticsearch (full-text search) │
    │  └─ S3 (media storage)               │
    └───────────────────────────────────────┘
         │
    ┌────▼────────────────┐
    │  Observability      │
    │  ├─ Prometheus      │
    │  ├─ Grafana         │
    │  ├─ Jaeger          │
    │  └─ Loki            │
    └─────────────────────┘
```

### Design Principles

1. **Microservices**: Each service owns its data and business logic
2. **Async-First**: Services communicate via events (Kafka)
3. **Resilience**: Timeouts, retries, circuit breakers
4. **Observability**: Every request traceable end-to-end
5. **Security**: Zero-trust, encryption in transit/at rest

---

## Microservices

### Service Structure

```
service-name/
├── app/
│   ├── main.py                    # FastAPI app entry point
│   ├── core/
│   │   ├── settings.py            # Environment config
│   │   ├── database.py            # Database connection pooling
│   │   ├── logging.py             # Structured JSON logging
│   │   └── exceptions.py          # Custom exceptions
│   ├── models/
│   │   ├── domain.py              # SQLAlchemy ORM models
│   │   └── entities.py            # Domain entity classes
│   ├── schemas/
│   │   ├── requests.py            # Pydantic request models
│   │   └── responses.py           # Pydantic response models
│   ├── repositories/
│   │   ├── base.py                # Base repository class
│   │   └── domain_repository.py   # Domain-specific repositories
│   ├── services/
│   │   └── domain_service.py      # Business logic
│   ├── api/
│   │   ├── routes.py              # Router setup
│   │   └── endpoints/
│   │       └── domain.py          # Domain endpoints
│   ├── middleware/
│   │   ├── auth.py                # Authentication
│   │   └── error_handler.py       # Error handling
│   ├── events/
│   │   ├── publishers.py          # Kafka event publishing
│   │   └── schemas.py             # Event schemas
│   ├── telemetry/
│   │   ├── tracing.py             # OpenTelemetry
│   │   └── metrics.py             # Prometheus metrics
│   └── security/
│       └── permissions.py         # Authorization checks
├── tests/
│   ├── conftest.py                # Pytest fixtures
│   ├── unit/
│   │   └── test_services.py
│   ├── integration/
│   │   └── test_api.py
│   └── e2e/
│       └── test_workflows.py
├── migrations/
│   ├── versions/
│   │   └── 001_initial.py
├── Dockerfile
└── pyproject.toml
```

### Clean Architecture Layers

#### 1. API Layer (Presentation)
- FastAPI route handlers
- Input validation with Pydantic
- Response formatting
- HTTP status codes and error handling

```python
@router.post("/items", response_model=ItemResponse)
async def create_item(
    request: CreateItemRequest,
    service: ItemService = Depends(get_item_service),
    current_user: User = Depends(get_current_user),
) -> ItemResponse:
    """Create a new item."""
    item = await service.create_item(request, current_user.id)
    return ItemResponse.from_orm(item)
```

#### 2. Service Layer (Application)
- Use case orchestration
- Business logic coordination
- Transaction management
- Event publishing

```python
class ItemService:
    """Orchestrates item-related business logic."""
    
    async def create_item(self, data: CreateItemRequest, user_id: UUID) -> Item:
        # Validate business rules
        if not await self._check_quota(user_id):
            raise QuotaExceeded()
        
        # Create item
        item = await self.repository.create(data)
        
        # Publish event
        await self.event_publisher.publish(ItemCreatedEvent(item_id=item.id))
        
        return item
```

#### 3. Domain Layer
- Business entities
- Domain rules
- Value objects
- No external dependencies

#### 4. Infrastructure Layer
- Database access (repositories)
- External service integration
- Cache operations
- Event publishing

### Dependency Injection Pattern

Use FastAPI's `Depends()` for dependency injection:

```python
async def get_item_service() -> ItemService:
    db_session = await get_db_session()
    repository = ItemRepository(db_session)
    event_publisher = get_event_publisher()
    return ItemService(repository, event_publisher)

@router.post("/items")
async def create_item(
    request: CreateItemRequest,
    service: ItemService = Depends(get_item_service),
):
    return await service.create_item(request)
```

### Repository Pattern

Base repository for consistent data access:

```python
class BaseRepository(Generic[T]):
    """Base repository with common operations."""
    
    async def create(self, obj_in: BaseModel) -> T:
        """Create new record."""
        db_obj = self.model(**obj_in.dict())
        self.session.add(db_obj)
        await self.session.flush()
        return db_obj
    
    async def get_by_id(self, id: UUID) -> Optional[T]:
        """Get by primary key."""
        return await self.session.get(self.model, id)
    
    async def update(self, db_obj: T, obj_in: BaseModel) -> T:
        """Update record."""
        for field, value in obj_in.dict(exclude_unset=True).items():
            setattr(db_obj, field, value)
        await self.session.flush()
        return db_obj
    
    async def delete(self, id: UUID) -> None:
        """Soft delete record."""
        db_obj = await self.get_by_id(id)
        if db_obj:
            db_obj.is_active = False
            await self.session.flush()
```

### Event Publishing Pattern

Publish events to Kafka for async processing:

```python
class EventPublisher:
    """Publishes domain events to Kafka."""
    
    async def publish(self, event: DomainEvent) -> None:
        """Publish event to appropriate topic."""
        topic = self._get_topic(event)
        await self.producer.send_and_wait(
            topic,
            value=json.dumps(event.dict()),
            key=str(event.aggregate_id).encode(),
        )
```

### Error Handling Pattern

Custom exceptions for domain-specific errors:

```python
class DomainException(Exception):
    """Base domain exception."""
    
    def __init__(self, error_code: str, message: str, status_code: int = 400):
        self.error_code = error_code
        self.message = message
        self.status_code = status_code

@app.exception_handler(DomainException)
async def domain_exception_handler(request: Request, exc: DomainException):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=exc.error_code,
            message=exc.message,
        ).dict(),
    )
```

### Health Checks

Every service must implement health checks:

```python
@app.get("/health")
async def health_check() -> HealthCheckResponse:
    """Service health check."""
    db_healthy = await check_database()
    redis_healthy = await check_redis()
    
    return HealthCheckResponse(
        status="healthy" if db_healthy and redis_healthy else "unhealthy",
        checks={
            "database": {"status": "healthy" if db_healthy else "unhealthy"},
            "redis": {"status": "healthy" if redis_healthy else "unhealthy"},
        },
    )
```

---

## Frontend Architecture

### Next.js Project Structure

```
apps/web/
├── src/
│   ├── app/
│   │   ├── layout.tsx             # Root layout
│   │   ├── page.tsx               # Home page
│   │   ├── (auth)/                # Auth routes
│   │   │   ├── login/
│   │   │   └── register/
│   │   ├── (app)/                 # Protected routes
│   │   │   ├── watch/
│   │   │   ├── watchlist/
│   │   │   ├── profile/
│   │   │   └── admin/
│   │   └── api/                   # API routes
│   ├── components/
│   │   ├── layout/                # Layout components
│   │   ├── auth/                  # Auth components
│   │   ├── player/                # Video player
│   │   ├── browse/                # Content browsing
│   │   └── common/                # Shared components
│   ├── hooks/                     # Custom React hooks
│   ├── lib/
│   │   ├── api-client.ts          # API client
│   │   ├── auth.ts                # Auth utilities
│   │   └── utils.ts               # Helper utilities
│   ├── store/                     # Zustand stores
│   ├── styles/                    # Global styles
│   └── types/                     # TypeScript types
├── public/                        # Static assets
├── tsconfig.json                  # TypeScript config
├── tailwind.config.ts             # TailwindCSS config
└── next.config.ts                 # Next.js config
```

### State Management

**Zustand** for simple, lightweight state management:

```typescript
interface AuthStore {
  user: User | null;
  token: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  token: null,
  login: async (email, password) => {
    // API call
    const { data } = await api.post('/auth/login', { email, password });
    set({ user: data.user, token: data.token });
  },
  logout: () => set({ user: null, token: null }),
}));
```

### Data Fetching

**React Query** (TanStack Query) for server state management:

```typescript
const { data, isLoading, error } = useQuery(
  ['content', id],
  () => api.get(`/content/${id}`),
  { staleTime: 5 * 60 * 1000 }  // 5 minutes
);
```

### Video Player Design

- HLS/DASH support (adaptive bitrate)
- Quality selector
- Audio track selector
- Subtitle support
- Keyboard shortcuts
- Fullscreen support
- Picture-in-picture

### API Integration

```typescript
// lib/api-client.ts
const apiClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL,
});

apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});
```

### Styling Strategy

- **Framework**: TailwindCSS for utility-first styling
- **Design System**: Custom design tokens
- **Responsive**: Mobile-first approach
- **Dark Mode**: Support via Tailwind

---

## Database Schema

### Database per Service Pattern

Each microservice owns its independent PostgreSQL database:

```
auth_db         → Auth Service (users, tokens, audit logs)
users_db        → User Service (profiles, devices, preferences)
content_db      → Content Service (movies, shows, genres)
streaming_db    → Streaming Service (sessions, watch history)
billing_db      → Billing Service (subscriptions, payments)
analytics_db    → Analytics Service (events, behavior)
admin_db        → Admin Service (content, moderation)
```

### Auth Service Schema

#### users table
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    email_verified BOOLEAN DEFAULT FALSE,
    email_verified_at TIMESTAMP,
    last_login_at TIMESTAMP,
    last_login_ip INET,
    login_attempts INTEGER DEFAULT 0,
    locked_until TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_email_active ON users(email, is_active);
```

#### refresh_tokens table
```sql
CREATE TABLE refresh_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token VARCHAR(500) NOT NULL UNIQUE,
    device_id VARCHAR(255),
    ip_address INET,
    expires_at TIMESTAMP NOT NULL,
    revoked_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_refresh_tokens_user_expires ON refresh_tokens(user_id, expires_at);
CREATE INDEX idx_refresh_tokens_device ON refresh_tokens(device_id, user_id);
```

#### token_blacklist table
```sql
CREATE TABLE token_blacklist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    jti VARCHAR(500) NOT NULL UNIQUE,
    user_id UUID NOT NULL,
    revoked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_token_blacklist_jti ON token_blacklist(jti);
```

### Cross-Service Communication

Services communicate via:
1. **REST APIs** for synchronous requests
2. **Kafka Events** for asynchronous updates
3. **GraphQL** for complex queries (optional)

### Indexing Strategy

- Add indexes on foreign keys
- Index frequently queried columns
- Create composite indexes for common WHERE/ORDER BY combinations
- Use partial indexes for filtered queries

### Schema Evolution

> ⚠️ Historical note: the services do **not** use Alembic despite the
> instructions below. There is no migration framework; schema changes are
> applied by hand to the live dev DB and drift has been repaired manually
> (e.g. billing `invoices`, Aug 2026). Keep model columns in sync with the
> running stack.

Use Alembic for database migrations:

```bash
# Create migration
alembic revision --autogenerate -m "description"

# Apply migration
alembic upgrade head

# Rollback
alembic downgrade -1
```

---

## Key Concepts

### Microservices
Independent, loosely coupled services that own their data and communicate via APIs or events.

**Benefits**: Independent scaling, schema flexibility, technology diversity
**Trade-offs**: Operational complexity, network latency, consistency challenges

### Clean Architecture
Layered architecture with clear separation of concerns: API → Services → Domain → Infrastructure

**Benefits**: Testability, maintainability, loose coupling, easy to modify

### Event-Driven Architecture
Services communicate asynchronously through events rather than direct API calls, enabling real-time data synchronization.

**Example**: `user.registered` event triggers welcome email, profile creation, analytics tracking, etc.

### Database per Service
Each microservice owns an independent PostgreSQL database instead of sharing one.

**Benefit**: Independent scaling and schema flexibility
**Challenge**: Distributed transactions, eventual consistency

### JWT Authentication
Stateless token containing claims (user ID, email, roles), signed by server.

**Access Token**: Short-lived (15 min), included in every request
**Refresh Token**: Long-lived (7 days), used to obtain new access token

Access tokens carry `aud: "wildframe-api"`; every verifying service decodes
with that audience (`settings.JWT_AUDIENCE`) or python-jose raises
`JWTClaimsError: Invalid audience`. The api-gateway is a transparent proxy —
it rate-limits proxied requests (keyed by user `sub` or IP) but does not
reject them itself; each backend service enforces auth at its own boundary.

### Rate Limiting
Sliding window algorithm preventing abuse (enforced in the gateway's
`proxy_request`):
- **Key**: authenticated user `sub` (JWT) when present, otherwise client IP.
- **Limits**: auth routes 5/min, search 100/min, default 1000/min.
- **Response**: `429 Too Many Requests` with `Retry-After`.

### Correlation ID
Unique identifier tracking a request through all services and all logs, enabling distributed tracing.

---

Last Updated: August 17, 2026
