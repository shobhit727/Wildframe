# Production Service Architecture Pattern

This document describes the production-grade microservice architecture used in Wildframe, providing a template for implementing consistent, scalable services.

## Service Structure Template

Every service in the Wildframe platform follows this structure:

```
service-name/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI application entry point
│   ├── core/
│   │   ├── __init__.py
│   │   ├── settings.py            # Environment configuration
│   │   ├── database.py            # Database connection management
│   │   ├── logging.py             # Structured logging setup
│   │   └── exceptions.py          # Custom exceptions
│   ├── models/
│   │   ├── __init__.py
│   │   ├── domain.py              # SQLAlchemy ORM models
│   │   └── entities.py            # Domain entity classes
│   ├── schemas/
│   │   ├── __init__.py            # Pydantic request/response schemas
│   │   ├── requests.py
│   │   └── responses.py
│   ├── repositories/
│   │   ├── __init__.py
│   │   ├── base.py                # Base repository class
│   │   └── domain_repository.py   # Domain-specific repositories
│   ├── services/
│   │   ├── __init__.py
│   │   └── domain_service.py      # Business logic services
│   ├── api/
│   │   ├── __init__.py
│   │   ├── routes.py              # Main router setup
│   │   └── endpoints/
│   │       ├── __init__.py
│   │       └── domain.py          # Domain endpoints
│   ├── middleware/
│   │   ├── __init__.py
│   │   ├── auth.py                # Authentication middleware
│   │   └── error_handler.py       # Error handling
│   ├── events/
│   │   ├── __init__.py
│   │   ├── publishers.py          # Kafka event publishing
│   │   └── schemas.py             # Event schemas
│   ├── telemetry/
│   │   ├── __init__.py
│   │   ├── tracing.py             # OpenTelemetry setup
│   │   └── metrics.py             # Prometheus metrics
│   └── security/
│       ├── __init__.py
│       └── permissions.py         # Authorization checks
├── tests/
│   ├── __init__.py
│   ├── conftest.py                # Pytest fixtures
│   ├── unit/
│   │   └── test_services.py
│   ├── integration/
│   │   └── test_api.py
│   └── e2e/
│       └── test_workflows.py
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 001_initial.py
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── pytest.ini
├── alembic.ini
└── README.md
```

## Clean Architecture Layers

Each service implements clean architecture with clear separation of concerns:

### 1. Presentation Layer (API)
- FastAPI route handlers
- Input validation with Pydantic
- Response formatting
- HTTP status codes and error handling

**File**: `app/api/endpoints/`

**Example**:
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

### 2. Application Layer (Services)
- Use case orchestration
- Business logic coordination
- Transaction management
- Event publishing

**File**: `app/services/`

**Example**:
```python
class ItemService:
    """Orchestrates item-related business logic."""
    
    def __init__(self, repository: ItemRepository, event_publisher: EventPublisher):
        self.repository = repository
        self.event_publisher = event_publisher
    
    async def create_item(self, data: CreateItemRequest, user_id: UUID) -> Item:
        """Create item with business rule validation."""
        # Validate business rules
        if not await self._check_quota(user_id):
            raise QuotaExceeded()
        
        # Create item
        item = await self.repository.create(data)
        
        # Publish event
        await self.event_publisher.publish(ItemCreatedEvent(item_id=item.id, user_id=user_id))
        
        return item
```

### 3. Domain Layer
- Business entities
- Domain rules
- Value objects
- No external dependencies

**File**: `app/models/entities.py`

**Example**:
```python
class Item:
    """Item domain entity."""
    
    def __init__(self, id: UUID, title: str, user_id: UUID):
        self.id = id
        self.title = title
        self.user_id = user_id
    
    def validate(self) -> bool:
        """Validate domain rules."""
        return len(self.title) > 0 and len(self.title) <= 255
```

### 4. Infrastructure Layer
- Database access (repositories)
- External service integration
- Cache operations
- Event publishing

**File**: `app/repositories/`, `app/events/`

**Example**:
```python
class ItemRepository(BaseRepository):
    """Data access for items."""
    
    async def create(self, data: CreateItemRequest) -> Item:
        """Create item in database."""
        db_item = models.Item(title=data.title)
        self.session.add(db_item)
        await self.session.commit()
        return db_item
    
    async def get_by_id(self, item_id: UUID) -> Item:
        """Retrieve item by ID."""
        return await self.session.get(models.Item, item_id)
```

## Dependency Injection Pattern

Use FastAPI's Depends() for dependency injection:

```python
# Define dependency
async def get_item_service() -> ItemService:
    db_session = await get_db_session()
    repository = ItemRepository(db_session)
    event_publisher = get_event_publisher()
    return ItemService(repository, event_publisher)

# Use in endpoint
@router.post("/items")
async def create_item(
    request: CreateItemRequest,
    service: ItemService = Depends(get_item_service),
):
    return await service.create_item(request)
```

## Repository Pattern

Base repository for consistent data access:

```python
class BaseRepository(Generic[T]):
    """Base repository with common operations."""
    
    def __init__(self, session: AsyncSession, model: Type[T]):
        self.session = session
        self.model = model
    
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

## Event Publishing Pattern

Publish events to Kafka for async processing:

```python
class EventPublisher:
    """Publishes domain events to Kafka."""
    
    def __init__(self, kafka_producer: AIOKafkaProducer):
        self.producer = kafka_producer
    
    async def publish(self, event: DomainEvent) -> None:
        """Publish event to appropriate topic."""
        topic = self._get_topic(event)
        await self.producer.send_and_wait(
            topic,
            value=json.dumps(event.dict()),
            key=str(event.aggregate_id).encode(),
        )
```

## Error Handling Pattern

Custom exceptions for domain-specific errors:

```python
class DomainException(Exception):
    """Base domain exception."""
    
    def __init__(self, error_code: str, message: str, status_code: int = 400):
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        super().__init__(message)

class QuotaExceeded(DomainException):
    def __init__(self):
        super().__init__(
            error_code="QUOTA_EXCEEDED",
            message="User has exceeded quota",
            status_code=429,
        )

# In endpoint
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

## Testing Pattern

Comprehensive test structure:

```python
# conftest.py
@pytest.fixture
async def db_session():
    """Fixture for test database session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async_session_maker = async_sessionmaker(engine)
    async with async_session_maker() as session:
        yield session
    
    await engine.dispose()

# test_services.py
@pytest.mark.asyncio
async def test_create_item(db_session):
    """Test item creation."""
    repository = ItemRepository(db_session)
    service = ItemService(repository, mock_event_publisher)
    
    request = CreateItemRequest(title="Test Item")
    item = await service.create_item(request, user_id)
    
    assert item.title == "Test Item"
    assert item.user_id == user_id
```

## Metrics and Monitoring

Track key metrics:

```python
from prometheus_client import Counter, Histogram

request_count = Counter(
    "service_requests_total",
    "Total requests",
    ["method", "endpoint", "status"],
)

request_duration = Histogram(
    "service_request_duration_seconds",
    "Request duration",
    ["method", "endpoint"],
)

# In middleware
@app.middleware("http")
async def track_metrics(request: Request, call_next):
    with request_duration.labels(
        method=request.method,
        endpoint=request.url.path,
    ).time():
        response = await call_next(request)
    
    request_count.labels(
        method=request.method,
        endpoint=request.url.path,
        status=response.status_code,
    ).inc()
    
    return response
```

## Configuration Management

Environment-based configuration:

```python
# .env.development
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/db_dev
REDIS_URL=redis://localhost:6379/0
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
DEBUG=True
LOG_LEVEL=DEBUG

# .env.production
DATABASE_URL=postgresql+asyncpg://user:pass@prod-db:5432/db
REDIS_URL=redis://prod-cache:6379/0
KAFKA_BOOTSTRAP_SERVERS=kafka-1:9092,kafka-2:9092,kafka-3:9092
DEBUG=False
LOG_LEVEL=INFO
```

## Health Checks

Every service must implement health checks:

```python
@app.get("/health")
async def health_check() -> HealthCheckResponse:
    """Service health check."""
    db_healthy = await check_database()
    redis_healthy = await check_redis()
    
    overall = db_healthy and redis_healthy
    
    return HealthCheckResponse(
        status="healthy" if overall else "unhealthy",
        checks={
            "database": {"status": "healthy" if db_healthy else "unhealthy"},
            "redis": {"status": "healthy" if redis_healthy else "unhealthy"},
        },
    )
```

## Deployment Considerations

- Each service runs independently
- Services scale horizontally
- Database connections pooled
- Graceful shutdown on signals
- Health checks for orchestration
- Structured logging for debugging
- Metrics for monitoring
- Traces for performance analysis

---

This pattern ensures consistency, maintainability, and scalability across all services in the Wildframe platform.
