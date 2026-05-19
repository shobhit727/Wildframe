# Wildframe Technical Glossary

Definitions of key terms and concepts used throughout the Wildframe platform.

## Architectural Concepts

### Microservices
Independent, loosely coupled services that own their data and communicate via APIs or events. Each service in Wildframe:
- Has its own PostgreSQL database
- Runs in its own Kubernetes pod
- Can be deployed independently
- Communicates via REST or Kafka

**Example**: Auth Service, User Service, Content Service

### Clean Architecture
Layered architecture organizing code by dependency direction and responsibility:
- **API Layer**: HTTP endpoints, request/response handling
- **Services Layer**: Business logic and use cases
- **Repository Layer**: Data access abstraction
- **Models Layer**: Database entities (SQLAlchemy)
- **Domain Layer**: Core business rules (implicit)

**Benefit**: Testability, maintainability, loose coupling

### Event-Driven Architecture
Services communicate asynchronously through events rather than direct API calls. Kafka-based in Wildframe.

**Example**: `user.registered` event triggers welcome email, profile creation, etc.

### Database per Service
Each microservice owns its independent PostgreSQL database instead of sharing one.

**Benefit**: Independent scaling, schema flexibility
**Trade-off**: Slight operational complexity (managed via RDS)

## Technology Terms

### FastAPI
Modern Python web framework featuring:
- Automatic API documentation
- Type hints for validation
- Async/await support
- Dependency injection

**Used for**: All backend services

### SQLAlchemy 2.0
Python ORM with async support, used for database access in Wildframe.
- **Session**: Database connection for a transaction
- **Query**: Database select statement
- **Model**: Python class representing a database table

### PostgreSQL
Enterprise-grade relational database with features like:
- ACID compliance
- JSON data types
- Full-text search
- Row-level security (RLS)

**Used as**: Primary data store for all services

### Redis
In-memory cache and session store used for:
- Rate limiting (sliding window)
- Session management
- Cache warming
- Pub/sub messaging

**Not used for**: Persistent data (disk backup applied)

### Kafka
Event streaming platform with topics, partitions, and consumer groups.

**Example topic**: `user.registered`
**Consumer**: Microservice subscribing to events
**Producer**: Microservice publishing events

### Elasticsearch
Full-text search engine with:
- Tokenization and stemming
- Faceted search
- Range queries
- Aggregations

**Used for**: Content discovery, indexing movie/show metadata

### Kubernetes (K8s)
Container orchestration managing:
- **Pod**: Single container or group of containers
- **Deployment**: Manages pod replicas
- **Service**: Exposes pods via DNS
- **StatefulSet**: For stateful applications
- **ConfigMap**: Configuration data
- **Secret**: Sensitive data (encrypted)

## Security Terms

### JWT (JSON Web Token)
Stateless token containing claims, signed by server:
- **Access Token**: Short-lived (15 min), included in every request
- **Refresh Token**: Long-lived (7 days), used to obtain new access token
- **Claims**: User ID, email, roles in the token

**Benefit**: Scalable, no session storage needed

### Bcrypt
Password hashing algorithm with configurable cost factor:
- **Cost factor**: Number of iterations (default: 12)
- **Salt**: Random data added to password before hashing
- **Hash**: Irreversible output used for verification

**Benefit**: Resistant to brute force attacks

### Rate Limiting
Restricting request count to prevent abuse:
- **Sliding window**: Requests in last N seconds
- **Buckets**: Per-user, per-IP, per-endpoint
- **Backoff**: Gradually increasing delays

**Example**: Max 100 login attempts per minute per user

### Correlation ID
Unique identifier tracking a request through all services:
- Added to every log entry
- Passed to downstream services
- Enables tracing a single user action across microservices

**Format**: UUID in `X-Correlation-ID` header

## DevOps Terms

### Docker
Container technology allowing:
- **Image**: Blueprint with application code and dependencies
- **Container**: Running instance of an image
- **Multi-stage build**: Separate build and runtime layers for smaller images

**Benefit**: Consistent environments from development to production

### Kubernetes (Container Orchestration)
Manages containerized applications with:
- **Deployment**: Declarative pod management
- **HPA**: Horizontal Pod Autoscaler (auto-scales replicas)
- **PDB**: Pod Disruption Budget (prevents cascading failures)
- **NetworkPolicy**: Network segmentation

**Benefit**: Self-healing, auto-scaling, rolling updates

### Helm
Package manager for Kubernetes:
- **Chart**: Collection of templates and values
- **Release**: Deployed instance of a chart
- **Values**: Configuration for a chart

**Example**: Single Helm install deploys entire Wildframe stack

### Terraform
Infrastructure as Code for AWS:
- **Resource**: AWS service (EC2, RDS, etc.)
- **Variable**: Input values
- **Output**: Values returned after creation
- **State**: Current infrastructure snapshot

**Benefit**: Version-controlled, reproducible infrastructure

### GitHub Actions
CI/CD platform for automated:
- Testing
- Building
- Deploying

**Trigger**: Push to branch or pull request

## Database Terms

### Transaction
Group of queries executed atomically:
- **ACID**: Atomicity, Consistency, Isolation, Durability
- **Commit**: Save all changes
- **Rollback**: Discard all changes

**Example**: Debit account and credit account in one transaction

### Index
Data structure accelerating query lookups:
- **B-tree**: General purpose (default)
- **GiST**: Geometric, full-text search
- **Partial index**: Only for rows matching condition

**Trade-off**: Faster reads, slower writes, more storage

### Connection Pool
Reused database connections reducing overhead:
- **Min connections**: Minimum kept open
- **Max connections**: Maximum allowed
- **Idle timeout**: Close after N seconds

**Example**: 50 connections per service for 10 replicas = 500 max

### Migration
Schema change applied incrementally:
- **Up**: Apply change
- **Down**: Revert change
- **Alembic**: Python migration tool

**Benefit**: Database version control, rollback capability

## Monitoring & Observability Terms

### Metrics
Numerical measurements over time:
- **Latency**: Response time (measured in milliseconds)
- **Throughput**: Requests per second
- **Error rate**: Percentage of failed requests
- **P95/P99**: 95th/99th percentile (tail latency)

**Tools**: Prometheus, Grafana

### Logging
Recording application events:
- **Structured logging**: JSON format with fields
- **Log aggregation**: Centralized collection and search
- **Log level**: INFO, WARNING, ERROR, etc.

**Tools**: Loki, ELK Stack

### Tracing
Tracking request flow across services:
- **Span**: Unit of work (one service call)
- **Trace**: Collection of spans for one request
- **Instrumentation**: Code to emit spans

**Tools**: Jaeger, Zipkin

### Alerting
Automated notification of problems:
- **Alert rule**: Condition triggering notification
- **Threshold**: Value triggering alert
- **Severity**: P1 (immediate), P2 (urgent), P3 (routine)

**Example**: Alert if error rate > 1% for 5 minutes

## Application Terms

### Session
User state for request duration:
- **Session ID**: Unique identifier
- **Session data**: User ID, permissions, preferences
- **Storage**: Redis or database

**Benefit**: Stateless API, scalable

### Token Refresh
Obtaining new access token:
1. Client sends refresh token to `/auth/refresh`
2. Server validates and issues new access token
3. Old token discarded, new token used for subsequent requests

**Benefit**: Limits impact of token compromise (15 min window)

### Rate Limiter
Sliding window algorithm:
1. Key: "user:123:login"
2. Increment counter, set expiry to now + window (60 sec)
3. If counter > limit, reject request

**Storage**: Redis for fast access

### Watchlist
User's list of saved content for later viewing:
- **Add**: `POST /watchlist` with content ID
- **Remove**: `DELETE /watchlist/{id}`
- **List**: `GET /watchlist`

**Storage**: PostgreSQL with indexes on user_id

## Video Streaming Terms

### Adaptive Bitrate Streaming
Dynamically adjusting video quality based on connection:
- **HLS**: HTTP Live Streaming (Apple)
- **DASH**: Dynamic Adaptive Streaming over HTTP
- **Manifest**: M3U8 or MPD file listing segments

**Benefit**: Smooth playback regardless of bandwidth

### Bitrate
Data rate of video, measured in kbps:
- **240p**: 500 kbps
- **480p**: 1200 kbps
- **720p**: 2500 kbps
- **1080p**: 5000 kbps
- **4K**: 15000 kbps

**Selection**: Based on bandwidth, user preference, device

### Transcode
Converting video to multiple resolutions/codecs:
- **H.264**: Standard video codec
- **VP9**: Google's video codec
- **AV1**: Newer, more efficient codec

**Process**: FFmpeg → HLS packaging → S3 upload

### Segment
Small video chunk (typically 10 seconds):
- **Benefits**: Adaptive quality switching, fast seeking
- **Storage**: S3 with CDN distribution

## API & Integration Terms

### REST
Architectural style for APIs using HTTP methods:
- **GET**: Retrieve resource
- **POST**: Create resource
- **PUT/PATCH**: Update resource
- **DELETE**: Delete resource

**Example**: `GET /api/content/123`

### API Gateway
Central entry point for all API requests:
- **Routing**: Forward to appropriate service
- **Authentication**: Verify JWT tokens
- **Rate limiting**: Per-user/per-IP limits
- **Logging**: Track all requests

### Webhook
Service-initiated callback to external system:
- **Example**: Payment provider calls `/webhook/payment` on completion
- **Retry**: Exponential backoff on failure
- **Verification**: HMAC signature validation

### OAuth2
Authorization protocol allowing third-party access:
- **Flow**: User → App → Provider → Authorization
- **Token**: Access token for API access
- **Refresh**: Token refresh for extended access

## Other Important Terms

### ACID Compliance
Database reliability guarantees:
- **Atomicity**: All or nothing
- **Consistency**: Valid state before and after
- **Isolation**: No interference between transactions
- **Durability**: Persisted after commit

### Idempotency
Operation producing same result when called multiple times:
- **Useful for**: Retry logic (safe to retry)
- **Implementation**: Unique request ID stored in database

### Soft Delete
Marking records as deleted without removing:
- **Column**: `is_active = false`
- **Benefit**: Data recovery, audit trails

### Schema Migration
Changing database structure:
- **Forward**: Add columns, create tables
- **Backward**: Remove columns, drop tables
- **Tool**: Alembic for Python/SQLAlchemy

### Horizontal Scaling
Adding more instances instead of bigger instances:
- **Benefit**: Better resilience, cost-effective
- **Requirement**: Stateless applications

### Blue-Green Deployment
Running two production environments:
- **Blue**: Current production
- **Green**: New version staging
- **Switch**: Instant traffic cutover
- **Benefit**: Zero downtime, quick rollback

---

**Note**: This glossary is a living document. Add new terms as the platform evolves.

Last Updated: 2026-05-12
