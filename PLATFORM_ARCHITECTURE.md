# Wildframe OTT Platform - Production Architecture

## Table of Contents
1. [System Overview](#system-overview)
2. [Architecture Principles](#architecture-principles)
3. [Service Architecture](#service-architecture)
4. [Data Architecture](#data-architecture)
5. [Event-Driven Architecture](#event-driven-architecture)
6. [Streaming Architecture](#streaming-architecture)
7. [Infrastructure Architecture](#infrastructure-architecture)
8. [Security Architecture](#security-architecture)
9. [Observability Architecture](#observability-architecture)
10. [Caching Strategy](#caching-strategy)

## System Overview

Wildframe is a production-grade OTT (Over-The-Top) streaming platform designed for:
- **Scalability**: Horizontal scaling across 1000s of concurrent users
- **Reliability**: 99.99% uptime SLA with graceful degradation
- **Performance**: <100ms API response times, <2s video startup
- **Maintainability**: Clear service boundaries, minimal coupling
- **Observability**: Full distributed tracing, metrics, and structured logging

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client Layer                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │  Web Frontend    │  │  Mobile App      │  │  Smart TV    │  │
│  │  (Next.js)       │  │  (React Native)  │  │  (Native)    │  │
│  └──────────────────┘  └──────────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                 CDN & API Gateway Layer                          │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  Cloudflare CDN + DDoS Protection                          │ │
│  └────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  API Gateway (FastAPI) - Rate Limiting, Auth, Routing     │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                   Microservices Layer                            │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ Auth Service     │  │ User Service     │  │ Content      │  │
│  │ - JWT tokens     │  │ - Profiles       │  │ - Metadata   │  │
│  │ - OAuth2         │  │ - Preferences    │  │ - Genres     │  │
│  │ - Sessions       │  │ - Devices        │  │ - Episodes   │  │
│  └──────────────────┘  └──────────────────┘  └──────────────┘  │
│                                                                  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ Streaming        │  │ Search Service   │  │ Billing      │  │
│  │ - HLS/DASH       │  │ - Elasticsearch  │  │ - Plans      │  │
│  │ - Adaptive BR    │  │ - Autocomplete   │  │ - Invoices   │  │
│  │ - Session mgmt   │  │ - Faceted search │  │ - Payments   │  │
│  └──────────────────┘  └──────────────────┘  └──────────────┘  │
│                                                                  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ Recommendation   │  │ Analytics        │  │ Admin        │  │
│  │ - ML Models      │  │ - Event tracking │  │ - Management │  │
│  │ - Personalized   │  │ - Dashboards     │  │ - Moderation │  │
│  │ - Trending       │  │ - User behavior  │  │ - Reports    │  │
│  └──────────────────┘  └──────────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              Message Queue & Event Stream Layer                 │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  Apache Kafka - Event-driven async communication          │ │
│  │  Topics: user.*, content.*, playback.*, billing.*        │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Data & Storage Layer                          │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ PostgreSQL       │  │ Redis            │  │ Elasticsearch│  │
│  │ - Master DB      │  │ - Cache Layer    │  │ - Search     │  │
│  │ - Replicas       │  │ - Sessions       │  │ - Analytics  │  │
│  │ - Backups        │  │ - Rate limits    │  │ - Autocomplete│ │
│  └──────────────────┘  └──────────────────┘  └──────────────┘  │
│                                                                  │
│  ┌──────────────────┐  ┌──────────────────┐                     │
│  │ S3/Object Store  │  │ Time-series DB   │                     │
│  │ - Video files    │  │ - Metrics        │                     │
│  │ - Thumbnails     │  │ - Events         │                     │
│  │ - Manifests      │  │ - Analytics      │                     │
│  └──────────────────┘  └──────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              Media Pipeline & Transcoding Layer                 │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  Worker Cluster (Kubernetes Jobs/Batch)                   │ │
│  │  - Video transcoding (FFmpeg)                             │ │
│  │  - Thumbnail generation                                   │ │
│  │  - Metadata extraction                                    │ │
│  │  - HLS/DASH packaging                                     │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Architecture Principles

### 1. **Microservices with Service Boundaries**
- Each service owns its database (Database per Service pattern)
- Services communicate via REST/gRPC or async messaging
- Loose coupling, high cohesion
- Independent deployment and scaling

### 2. **Clean Architecture in Each Service**
```
Service Structure:
├── presentation/          # API routes, request/response handling
├── application/           # Use cases, business logic orchestration
├── domain/                # Business rules, entities, value objects
├── infrastructure/        # Database, external services, repositories
└── shared/                # Cross-cutting concerns, utilities
```

### 3. **Event-Driven for Async Operations**
- Critical events published to Kafka
- Dead-letter queues for failed processing
- Idempotent event handlers
- Audit trail for compliance

### 4. **Database per Service Pattern**
```
Auth Service         → auth_db
User Service         → users_db
Content Service      → content_db
Streaming Service    → streaming_db
Billing Service      → billing_db
Analytics Service    → analytics_db
Search Service       → Elasticsearch clusters
```

### 5. **Caching Strategy (Cache-Aside Pattern)**
```
1. Check Redis cache
2. If miss → fetch from DB/service
3. Store result in Redis
4. Return to client
5. Invalidate on writes
```

### 6. **Resilience Patterns**
- Circuit breakers for external calls
- Exponential backoff with jitter
- Bulkhead pattern for resource isolation
- Graceful degradation
- Retry with idempotency keys

## Service Architecture

### API Gateway
**Purpose**: Single entry point, routing, authentication enforcement, rate limiting
```
Routes /api/v1/* → appropriate service
Enforces JWT authentication
Implements rate limiting (Redis-backed)
Adds distributed trace context
Compresses responses
CORS handling
```

**Scaling**: Horizontal, behind load balancer

### Auth Service
**Purpose**: User authentication, authorization, token management
- JWT token generation (15min access, 7d refresh)
- Token refresh and revocation
- OAuth2/OIDC integration support
- Brute-force protection (Redis rate limits)
- Session management
- MFA support

**Technology**: FastAPI, PostgreSQL, Redis
**Scaling**: Stateless, horizontal

### User Service
**Purpose**: User profiles, preferences, device management
- User registration and profile management
- Device tracking (for concurrent stream limits)
- User preferences and settings
- Watch history (basic, detailed in streaming-service)

**Technology**: FastAPI, PostgreSQL, Redis
**Scaling**: Stateless, horizontal

### Content Service
**Purpose**: Content metadata, catalog management
- Movie/show/episode metadata
- Genre management
- Content availability by region/platform
- Content relationships (sequels, series, collections)

**Technology**: FastAPI, PostgreSQL, Redis for caching
**Scaling**: Stateless, horizontal

### Streaming Service
**Purpose**: Manifest generation, playback management, stream analytics
- HLS/DASH manifest generation
- Playback session management
- Quality adaptation logic
- Subtitle/audio track management
- Watch progress tracking

**Technology**: FastAPI, PostgreSQL, Redis
**Scaling**: Stateless, horizontal

### Search Service
**Purpose**: Content discovery, search, autocomplete
- Elasticsearch for full-text search
- Fuzzy matching, typo tolerance
- Autocomplete suggestions
- Faceted search (genre, year, rating)
- Recommendation-assisted search

**Technology**: FastAPI, Elasticsearch
**Scaling**: Elasticsearch cluster scaling

### Recommendation Service
**Purpose**: Content recommendations, personalization
- Collaborative filtering
- Content-based recommendations
- Trending content aggregation
- User cohort analysis
- A/B testing framework

**Technology**: FastAPI, PostgreSQL, ML models
**Scaling**: Model serving via dedicated inference servers

### Billing Service
**Purpose**: Subscription management, payment processing
- Subscription plan management
- Invoice generation
- Payment processing (Stripe/PayPal integration)
- Churn prediction
- Revenue analytics

**Technology**: FastAPI, PostgreSQL, Redis, Payment providers
**Scaling**: Stateless, horizontal

### Analytics Service
**Purpose**: Event collection, aggregation, dashboards
- Event ingestion from services
- Real-time dashboards
- User behavior analysis
- Performance metrics
- Business intelligence

**Technology**: FastAPI, TimeSeries DB, Prometheus
**Scaling**: Event partitioning by service/timestamp

### Notification Service
**Purpose**: Multi-channel notifications
- Email notifications
- Push notifications
- SMS (optional)
- In-app notifications
- Notification preferences

**Technology**: FastAPI, Redis, Message providers (SendGrid, FCM)
**Scaling**: Queue-based processing

### Admin Service
**Purpose**: Content management, moderation, administration
- Content publishing workflows
- Moderation tools
- User management
- Reporting and insights
- System health monitoring

**Technology**: FastAPI, PostgreSQL
**Scaling**: Can be scaled independently

### Media Pipeline
**Purpose**: Video ingestion, transcoding, packaging
- Multi-resolution transcoding (240p-4K)
- HLS/DASH packaging
- Thumbnail generation
- Metadata extraction
- CDN invalidation

**Architecture**: Kubernetes Jobs/Worker Pods
**Technology**: FFmpeg, S3, Kafka consumers
**Scaling**: Auto-scaling based on queue depth

## Data Architecture

### Database Schema Strategy

#### Core Entities
```sql
-- Users domain
users (id, email, password_hash, created_at, updated_at, is_active)
user_profiles (id, user_id, bio, avatar_url, preferences_json)
devices (id, user_id, device_id, device_type, last_seen, is_active)
user_sessions (id, user_id, device_id, refresh_token, expires_at)

-- Content domain
genres (id, name, slug, description)
content (id, title, description, release_date, genre_id, duration)
movies (id, content_id, director, budget, revenue)
shows (id, content_id, episode_count)
seasons (id, show_id, season_number, episode_count)
episodes (id, season_id, episode_number, title, duration, air_date)
video_files (id, episode_id, resolution, bitrate, url, format, size)

-- Streaming domain
playback_sessions (id, user_id, content_id, started_at, last_position, status)
watch_history (id, user_id, content_id, watched_at, duration_watched, progress)
streaming_events (id, user_id, session_id, event_type, timestamp, metadata_json)

-- Billing domain
subscription_plans (id, name, price, features_json, tier)
user_subscriptions (id, user_id, plan_id, start_date, end_date, status)
invoices (id, user_id, subscription_id, amount, status, created_at)
payments (id, invoice_id, provider, transaction_id, status, created_at)

-- Analytics domain
analytics_events (id, user_id, event_type, event_data_json, timestamp)
```

### Indexing Strategy
```sql
-- Performance-critical indexes
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_content_genre_id ON content(genre_id);
CREATE INDEX idx_episodes_season_id ON episodes(season_id);
CREATE INDEX idx_video_files_resolution ON video_files(resolution);
CREATE INDEX idx_playback_sessions_user_id ON playback_sessions(user_id);
CREATE INDEX idx_watch_history_user_id_timestamp ON watch_history(user_id, watched_at DESC);
CREATE INDEX idx_subscriptions_user_id ON user_subscriptions(user_id) WHERE is_active = true;

-- Composite indexes for common queries
CREATE INDEX idx_users_email_is_active ON users(email, is_active);
CREATE INDEX idx_content_release_genre ON content(release_date DESC, genre_id);
CREATE INDEX idx_episodes_show_season ON episodes(show_id, season_id);
```

### Partitioning Strategy
```
-- Time-based partitioning for large tables
watch_history: partitioned by month on watched_at
analytics_events: partitioned by day on timestamp
streaming_events: partitioned by week on timestamp
```

## Event-Driven Architecture

### Event Topics (Kafka)

#### User Events
- `user.registered`: New user created
- `user.email_verified`: Email verification completed
- `user.password_changed`: Password updated
- `user.device_added`: New device registered

#### Content Events
- `content.created`: New content added
- `content.published`: Content becomes available
- `content.updated`: Metadata updated
- `content.removed`: Content deleted/archived

#### Playback Events
- `playback.started`: User started watching
- `playback.paused`: User paused
- `playback.resumed`: User resumed
- `playback.completed`: User finished watching
- `playback.quality_changed`: Adaptive bitrate change
- `playback.error`: Playback error occurred

#### Subscription Events
- `subscription.created`: New subscription
- `subscription.activated`: Subscription active
- `subscription.renewed`: Auto-renewal
- `subscription.cancelled`: User cancelled
- `subscription.expired`: Subscription ended

#### Billing Events
- `payment.initiated`: Payment started
- `payment.completed`: Payment successful
- `payment.failed`: Payment failed
- `invoice.generated`: Invoice created

#### Recommendation Events
- `recommendation.generated`: Recommendations computed
- `recommendation.engaged`: User interacted with recommendation

### Event Schema (Avro/JSON Schema)
```json
{
  "event_id": "uuid",
  "event_type": "user.registered",
  "occurred_at": "2026-05-12T10:30:00Z",
  "user_id": "uuid",
  "tenant_id": "uuid",
  "source_service": "auth-service",
  "trace_id": "uuid",
  "span_id": "uuid",
  "payload": {
    "email": "user@example.com",
    "signup_method": "email"
  },
  "version": "1"
}
```

### Event Processing
```
Topic Consumers:
user.registered       → notification-service, analytics-service
playback.started      → analytics-service, recommendation-service
playback.completed    → watch_history, analytics-service
subscription.created  → notification-service, billing-service
payment.completed     → notification-service, analytics-service

Dead Letter Queue: {topic}-dlq
- Automatic retry with exponential backoff
- Manual replay capability
- Alert on threshold breach
```

## Streaming Architecture

### HLS/DASH Delivery

#### Video Renditions (Adaptive Bitrate)
```
Resolution  Bitrate    Codec    Frame Rate    Use Case
────────────────────────────────────────────────────────
240p        500kbps    H.264    24fps         Mobile 3G
360p        1Mbps      H.264    24fps         Mobile 4G
480p        2.5Mbps    H.264    30fps         Tablets
720p        5Mbps      H.264    30fps         Desktop/HD
1080p       8Mbps      H.265    60fps         Full HD
4K          20Mbps     H.265    60fps         Ultra HD (optional)
```

#### HLS Manifest Example
```
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0

#EXT-X-STREAM-INF:BANDWIDTH=500000,RESOLUTION=240x135
240p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=360x202
360p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=480x270
480p.m3u8
```

#### Playback Session Management
```
1. Client requests playback initiation
2. Streaming service validates subscription/content access
3. Session created with:
   - User ID, content ID, device ID
   - Max concurrent streams check
   - DRM token generation (optional)
   - CDN URL assignment
4. Client receives manifest URL + session token
5. Client streams from CDN
6. Events sent via heartbeat API
```

## Infrastructure Architecture

### Kubernetes Cluster Design
```
Master Nodes: 3 (high-availability)
Worker Nodes: Auto-scaling (min 10, max 100)

Node Pools:
- general-pool: Generic workloads (min 5)
- api-pool: API services (min 3)
- compute-pool: Transcoding jobs (min 2, max 50, spot instances)
- db-pool: Database workloads (min 3, on-demand)
```

### Service Deployment Pattern
```yaml
Each Service:
- Deployment with 3+ replicas
- Service + Ingress for routing
- HorizontalPodAutoscaler (CPU 70%, Memory 80%)
- PodDisruptionBudget for high availability
- Network policies for security
- Resource requests/limits
```

## Security Architecture

### Authentication Flow
```
1. User login with email/password
2. Auth service validates credentials
3. JWT access token (15min) + refresh token (7d) generated
4. Tokens stored securely
5. Every request includes access token
6. On expiry, refresh token used for new access token
7. Token revocation on logout
```

### Authorization Model (RBAC)
```
Roles: admin, content_manager, moderator, user
Permissions: read_content, write_content, manage_users, etc.
Permission checks at:
  - API Gateway level (coarse-grained)
  - Service level (fine-grained)
```

### Data Security
```
- Encryption at rest (AES-256)
- Encryption in transit (TLS 1.3)
- Password hashing (Argon2)
- Secrets management (Kubernetes Secrets, HashiCorp Vault)
- API key rotation
- CORS policies
- CSRF tokens for state-changing operations
```

## Observability Architecture

### Logging Strategy
```
Application Logs:
- Structured JSON logging
- Log levels: DEBUG, INFO, WARN, ERROR, CRITICAL
- Correlation IDs on every log

Log Aggregation:
- Loki for log storage
- Promtail for log collection
- LogQL for log queries
- Retention: 30 days hot, 1 year cold storage
```

### Metrics Strategy
```
Prometheus Metrics:
- Request latency (p50, p95, p99)
- Error rates by endpoint
- Queue depths
- Cache hit rates
- Database connection pool stats
- Business metrics (signups, subscriptions, revenue)

Scrape intervals: 15 seconds
Retention: 15 days local, 1 year in long-term storage
```

### Distributed Tracing
```
OpenTelemetry Integration:
- Trace every request through services
- Capture SQL queries
- Capture external API calls
- Span tags: user_id, tenant_id, service, endpoint

Jaeger backend for trace storage and visualization
```

## Caching Strategy

### Multi-Layer Caching

#### Level 1: Client-Side
```
- Browser cache for static assets (30 days)
- Local storage for user preferences
- In-memory React Query cache (5 minutes)
```

#### Level 2: CDN Cache
```
- CloudFlare for static content (CSS, JS, images)
- Manifest files (1 hour TTL)
- Thumbnail images (30 days TTL)
```

#### Level 3: Redis Cache
```
Key Patterns:
user:{user_id}:profile → 24 hours
content:{content_id}:metadata → 7 days
genres → 30 days
trending:{period} → 1 hour
recommendations:{user_id} → 6 hours
session:{session_id} → Session TTL

Cache Invalidation:
- Write-through on data changes
- Event-driven invalidation via Kafka
- Scheduled cache refresh for slow data
```

#### Level 4: Database-Level
```
Query optimization:
- Connection pooling (pgBouncer)
- Query result caching where applicable
- Materialized views for complex queries
```

## Deployment Strategy

### Blue-Green Deployments
```
1. Deploy new version to "green" environment
2. Run integration tests
3. Switch traffic gradually (canary: 5% → 25% → 50% → 100%)
4. Monitor metrics for regressions
5. Rollback capability if needed
```

### Rolling Deployments
```
- For backward-compatible changes
- Update 1 pod at a time
- Health checks between updates
- MaxSurge: 1, MaxUnavailable: 0
```

## Production Readiness Checklist

- [ ] All services have health checks
- [ ] Circuit breakers configured for external calls
- [ ] Rate limiting enabled on API Gateway
- [ ] Database backups automated (daily + weekly)
- [ ] Load testing completed (1000 concurrent users)
- [ ] Disaster recovery plan documented
- [ ] Incident response procedures defined
- [ ] On-call rotation established
- [ ] Monitoring alerts configured
- [ ] Security audit completed
- [ ] Compliance requirements verified (GDPR, CCPA, etc.)
- [ ] Documentation complete
- [ ] Team trained on deployment procedures

---

**Next Steps**: Implementation of individual services following this architecture.
