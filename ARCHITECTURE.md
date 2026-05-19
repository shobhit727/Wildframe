# Wildframe - Production-Grade OTT Streaming Platform Architecture

## Executive Overview

Wildframe is a distributed, horizontally-scalable OTT platform designed for millions of concurrent users. It employs event-driven architecture, microservices principles, and real-time streaming capabilities.

### Core Design Principles

1. **Microservices Architecture** - Independent services with clear boundaries
2. **Event-Driven Communication** - Kafka for async workflows, gRPC for performance-critical paths
3. **Clean Architecture** - Domain-driven design in each service
4. **Observability First** - OpenTelemetry, Prometheus, Grafana, Loki from day one
5. **Horizontal Scalability** - Stateless services, distributed caching, database sharding ready
6. **Fault Tolerance** - Circuit breakers, retry logic, graceful degradation

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│              CDN & Edge Layer (Cloudflare/AWS CloudFront)│
│         (Cache HLS/DASH manifest & segments)             │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────────────┐
│                   NGINX Ingress Controller               │
│         (TLS termination, load balancing)                │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────────────┐
│                    API Gateway Service                  │
│  (Request routing, auth verification, rate limiting)    │
└────────────────────┬────────────────────────────────────┘
                     │
    ┌────────────────┼────────────────┐
    │                │                │
┌───▼────┐    ┌─────▼───┐    ┌──────▼─────┐
│Auth    │    │User     │    │Content     │
│Service │    │Service  │    │Service     │
└────────┘    └─────────┘    └────────────┘
    │                │                │
┌───▼────────────────┼────────────────▼──────────┐
│         Kafka Event Bus (Event Streaming)      │
│  Topics: user.*, content.*, playback.*, etc   │
└────────────────────┬───────────────────────────┘
    │                │                │
┌───▼────┐   ┌──────▼──┐    ┌───────▼────┐
│Stream  │   │Recommend │   │Analytics   │
│Service │   │Service   │   │Service     │
└────────┘   └──────────┘   └────────────┘
    │                           │
┌───▼─────────────────────────▼──────────┐
│  PostgreSQL (Multi-instance + Read Replicas)
│  Redis (Distributed cache + sessions)
│  Elasticsearch (Search indexing)
│  Object Storage (Video files, thumbnails)
└─────────────────────────────────────────┘
```

## Service Inventory

### Core Services

| Service | Port | Responsibility | Key Technologies |
|---------|------|-----------------|-----------------|
| api-gateway | 8000 | Request routing, auth | FastAPI, JWT |
| auth-service | 8001 | Auth & token mgmt | FastAPI, OAuth2 |
| user-service | 8002 | User profiles | FastAPI, PostgreSQL |
| content-service | 8003 | Content catalog | FastAPI, Elasticsearch |
| streaming-service | 8004 | Video delivery | FastAPI, HLS/DASH |
| recommendation-service | 8005 | Content recommendations | FastAPI, ML |
| analytics-service | 8006 | Events & metrics | FastAPI, Kafka |
| billing-service | 8007 | Subscriptions | FastAPI, Stripe/PayPal |
| search-service | 8008 | Global search | FastAPI, Elasticsearch |
| notification-service | 8009 | Alerts & notifications | FastAPI, SendGrid/Twilio |
| admin-service | 8010 | Admin operations | FastAPI, Role-based |

## Data Layer Architecture

### PostgreSQL Strategy

**Multi-instance setup:**
- Primary node (write)
- 2 read replicas (reads)
- Connection pooling (PgBouncer - 600 max connections)
- Automatic failover with patroni

**Sharding strategy (Phase 2):**
- User data: sharded by user_id
- Watch history: sharded by user_id
- Recommendations: sharded by user_id

### Redis Architecture

```
Redis Cluster (6 nodes, 3 primary + 3 replica)
├── Session store (TTL: 24h)
├── Cache layer
│   ├── Content metadata (TTL: 1h)
│   ├── User preferences (TTL: 6h)
│   ├── Recommendation cache (TTL: 12h)
│   └── Search autocomplete (TTL: 7d)
├── Rate limiting (sliding window)
├── Distributed locks (for critical operations)
└── Pub/Sub for real-time updates
```

### Elasticsearch Architecture

```
Elasticsearch Cluster (3 master + 6 data nodes)
├── Content index
│   ├── Movies
│   ├── Series
│   ├── Actors
│   └── Directors
├── User generated content index
├── Autocomplete index (with ngram analyzers)
└── Analytics index (time-series data)
```

## Event-Driven Architecture

### Kafka Topics

```
User Events:
  - user.created (new registration)
  - user.updated (profile changes)
  - user.device_added (device login)
  - user.session_ended (logout)

Content Events:
  - content.uploaded (new video)
  - content.transcoding_started
  - content.transcoding_completed
  - content.published
  - content.deleted

Playback Events:
  - playback.started
  - playback.paused
  - playback.resumed
  - playback.stopped
  - playback.quality_changed

Subscription Events:
  - subscription.created
  - subscription.renewed
  - subscription.cancelled
  - subscription.expired
  - payment.processed
  - payment.failed

Recommendation Events:
  - recommendation.generated
  - user_interaction.watch_completed
  - user_interaction.searched
```

### Event Processing Pattern

```
Producer → Topic → Consumer Group → Service Handler
                 ↓
            Dead Letter Topic (failed messages)
            ↓
            Retry Topic (with exponential backoff)
```

## API Design Philosophy

### Principles
1. **Versioning**: URL-based (`/api/v1/`) for major breaking changes
2. **Pagination**: Cursor-based for large datasets (not offset)
3. **Filtering**: Query parameters with standard operators
4. **Sorting**: Multi-field sorting support
5. **Rate Limiting**: Per-user token bucket algorithm
6. **Response Format**: Consistent JSON with error codes

### Example API Response
```json
{
  "status": "success|error",
  "data": { ... },
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "Content not found",
    "details": { ... }
  },
  "meta": {
    "request_id": "uuid",
    "timestamp": "ISO8601"
  }
}
```

## Authentication & Authorization Flow

```
1. User Login
   ├─ POST /auth/login
   ├─ Credentials validated against bcrypt hash
   └─ Return: access_token (15min) + refresh_token (7d)

2. API Request
   ├─ Include: Authorization: Bearer {access_token}
   ├─ JWT verified (signature, expiry, claims)
   └─ Request proceeds with user context

3. Token Refresh
   ├─ POST /auth/refresh
   ├─ Refresh token validated
   ├─ New access token issued
   └─ Refresh token rotated (new refresh token issued)

4. Logout
   ├─ Token added to revocation blacklist (Redis)
   └─ Device session terminated
```

## Video Processing Pipeline

### Upload to Delivery (Timeline)

```
0s - Video uploaded
    ├─ Virus scan (ClamAV)
    ├─ Store in temp storage
    └─ Emit: content.uploaded event

5s - Metadata extraction
    ├─ FFprobe for duration, resolution, codec
    └─ Emit: content.metadata_extracted event

10s - Transcoding jobs queued (parallel)
     ├─ 240p (h264, 500kbps)
     ├─ 480p (h264, 1.5mbps)
     ├─ 720p (h264, 3mbps)
     ├─ 1080p (h264, 6mbps)
     └─ 4K (h265, 15mbps)

Duration depends on video length and quality:
- 30min video: ~2-4 hours for all resolutions

While transcoding:
├─ Generate thumbnail at 5% mark
├─ Generate preview sprite (10 images)
└─ Emit: content.transcoding_started event

When complete:
├─ Package HLS manifests
├─ Package DASH manifests
├─ Upload to S3 with versioning
├─ Invalidate CDN cache
└─ Emit: content.transcoding_completed event

Finally:
├─ Content marked as ready for streaming
├─ Indexed in Elasticsearch
└─ Emit: content.published event
```

### Storage Layout

```
s3://wildframe-videos/
├── transcoded/
│   ├── {content_id}/
│   │   ├── 240p/
│   │   │   ├── segment_0.ts
│   │   │   ├── segment_1.ts
│   │   │   └── playlist.m3u8
│   │   ├── 480p/
│   │   ├── 720p/
│   │   ├── 1080p/
│   │   └── 4k/
│   └── manifests/
│       ├── {content_id}_hls.m3u8
│       └── {content_id}_dash.mpd
├── thumbnails/
│   └── {content_id}/
│       ├── poster.jpg (5% mark)
│       └── sprites.jpg (10-image sprite sheet)
└── originals/ (kept for 30 days)
    └── {content_id}_original.{ext}
```

## Recommendation System

### Algorithm Strategy

```
Phase 1 (Collaborative Filtering)
├─ User-based similarity (cosine similarity on watch vectors)
└─ Item-based similarity (content similarity on metadata)

Phase 2 (Content-Based Filtering)
├─ Genre match
├─ Actor/Director overlap
├─ Duration similarity
└─ Release date proximity

Phase 3 (Hybrid)
├─ Weighted combination of both approaches
├─ Cold-start handling (new users → popularity + genre)
└─ A/B testing framework for algorithm versions
```

### Recommendation Pipeline

```
1. Daily batch job (2am UTC)
   ├─ Fetch user watch history (last 90 days)
   ├─ Calculate user profiles
   ├─ Generate recommendations
   └─ Cache results in Redis (TTL: 24h)

2. Real-time trigger
   ├─ User completes watch (>80%)
   ├─ Recommendation service receives event
   ├─ Updates recommendations within 5 minutes
   └─ Invalidates old cached recommendations
```

## Frontend Architecture

### Directory Structure
```
apps/web/
├── public/
├── src/
│   ├── app/              # Next.js app router
│   ├── features/         # Feature modules
│   │   ├── auth/
│   │   ├── streaming/
│   │   ├── search/
│   │   └── profile/
│   ├── entities/         # Domain models
│   ├── shared/           # Shared components
│   ├── services/         # API clients
│   ├── hooks/            # Custom hooks
│   ├── stores/           # Zustand stores
│   ├── lib/              # Utilities
│   └── types/            # TypeScript definitions
├── next.config.js
├── tsconfig.json
└── package.json
```

### Streaming Page State Management

```
Zustand Store (playback-store.ts)
├── Video State
│   ├─ currentTime
│   ├─ duration
│   ├─ isPlaying
│   ├─ currentQuality
│   └─ availableQualities

├─ UI State
│   ├─ showControls
│   ├─ fullscreen
│   ├─ captionsEnabled
│   └─ selectedSubtitle

├─ Network State
│   ├─ bufferedDuration
│   ├─ currentBitrate
│   ├─ bufferingProgress
│   └─ connectionSpeed

└─ Derived State (selectors)
    ├─ canAutoPlay
    ├─ recommendedQuality
    └─ estimatedLoadTime
```

## Deployment Strategy

### Environment Strategy

```
Development
├─ Local docker-compose
├─ Single-node services
├─ SQLite option for quick iteration
└─ Mock external services

Staging
├─ 3-node Kubernetes cluster
├─ PostgreSQL read replica
├─ Redis cluster (3 nodes)
├─ Full observability stack
└─ Production-like configuration

Production
├─ Multi-region Kubernetes (AWS EKS)
├─ PostgreSQL with HA (RDS Multi-AZ)
├─ Redis Cluster (Elasticache)
├─ Elasticsearch (Managed)
├─ CDN + Object Storage
├─ Full observability + alerting
└─ Disaster recovery plan
```

### Deployment Workflow

```
1. Developer commits to main
2. GitHub Actions triggered
3. Build artifacts created
4. Push to staging
5. Run smoke tests
6. Manual approval for production
7. Blue-green deployment
8. Health checks pass
9. Monitor metrics for 5min
10. Auto-rollback if issues detected
```

## Observability Stack

### Metrics Collection (Prometheus)

```
Application Metrics
├─ Request latency (histogram)
├─ Request throughput (counter)
├─ Error rate by endpoint
├─ Service-specific metrics
│   ├─ Transcoding queue depth
│   ├─ Recommendation latency
│   ├─ Cache hit rate
│   └─ Database query duration

Infrastructure Metrics
├─ CPU/Memory usage
├─ Network I/O
├─ Disk usage
├─ Pod restart count
```

### Structured Logging (Loki)

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "level": "INFO",
  "service": "streaming-service",
  "request_id": "uuid",
  "user_id": "user123",
  "content_id": "movie456",
  "event": "playback_started",
  "duration_ms": 145,
  "quality": "720p",
  "labels": {
    "env": "production",
    "region": "us-east-1",
    "pod": "streaming-service-pod-1"
  }
}
```

### Tracing (OpenTelemetry)

```
User Request
├─ api-gateway
│  └─ 15ms
│  └─ auth verification
│     ├─ user-service (20ms)
│     │  └─ PostgreSQL query (8ms)
│     └─ Redis lookup (2ms)
│
├─ content-service
│  └─ 45ms
│  └─ Elasticsearch query (30ms)
│
└─ streaming-service
   └─ 200ms
   └─ HLS manifest generation (180ms)
   └─ CDN invalidation (20ms)
```

## Security Architecture

### Defense in Depth

```
Layer 1: Network
├─ TLS 1.3 everywhere
├─ Mutual TLS for service-to-service
└─ Web Application Firewall (CloudFlare)

Layer 2: API Gateway
├─ Rate limiting (per-user token bucket)
├─ JWT validation
├─ CORS policy enforcement
└─ Input validation & sanitization

Layer 3: Service
├─ RBAC (Role-Based Access Control)
├─ Database-level encryption
├─ Secrets management (HashiCorp Vault)
└─ Audit logging

Layer 4: Data
├─ Encryption at rest (AES-256)
├─ Encryption in transit (TLS 1.3)
├─ Sensitive field masking in logs
└─ GDPR compliance (right to deletion)
```

### Secret Management

```
Development: .env files (gitignored)
Staging: HashiCorp Vault (Kubernetes auth)
Production: AWS Secrets Manager + Vault

Rotation Policy:
├─ API keys: every 90 days
├─ Database passwords: every 180 days
├─ JWT signing key: every 365 days
└─ TLS certificates: auto-renew before expiry
```

## Performance Optimization

### Caching Strategy

```
CDN (Edge Layer)
├─ TTL: 24h for HLS/DASH manifests
├─ TTL: 1h for content metadata
└─ Instant invalidation on update

Application Cache (Redis)
├─ Session: TTL 24h
├─ Content: TTL 1h (tagged invalidation)
├─ Recommendations: TTL 12h
├─ Search results: TTL 30min
└─ User preferences: TTL 6h

Browser Cache
├─ Static assets: immutable (1 year)
├─ API responses: private, max-age 300s
└─ Video segments: public, max-age 86400s
```

### Query Optimization

```
PostgreSQL
├─ Index strategy
│   ├─ Single column: user_id, content_id, created_at
│   ├─ Composite: (user_id, created_at DESC)
│   └─ Partial: active subscriptions only
├─ Query patterns
│   ├─ Connection pooling (PgBouncer)
│   ├─ Read replicas for analytics
│   └─ Prepared statements for common queries
└─ Monitoring
    ├─ Slow query log (> 500ms)
    └─ Query execution plans analysis

Elasticsearch
├─ Index sharding: 5 primary shards per index
├─ Replica strategy: 1 replica for availability
├─ Refresh interval: 1s (real-time search)
└─ Field analysis: ngram for autocomplete
```

## Horizontal Scaling Strategy

### Service Scaling Triggers

```
API Gateway:
├─ CPU > 70% → scale up
├─ CPU < 20% → scale down (5min cooldown)
└─ Max replicas: 10, Min replicas: 2

Content Service:
├─ Requests/sec > 500 → scale up
└─ Max replicas: 8

Streaming Service:
├─ Active connections > 80% capacity → scale up
└─ Connection capacity: 1000 per pod × N pods

Recommendation Service:
├─ Queue depth > 100k → scale up
└─ Max replicas: 5
```

### Database Scaling

```
PostgreSQL
├─ Vertical: increase instance size first
├─ Horizontal: read replicas for queries
├─ Sharding: user_id based (when >500k users)

Redis
├─ Cluster mode: 6 nodes (3 primary + 3 replica)
├─ Auto-failover: promote replica on primary failure
└─ Eviction policy: allkeys-lru (LRU for keys)

Elasticsearch
├─ Data nodes: scale horizontally based on index size
├─ Master nodes: odd number (3, 5, 7)
└─ Shard allocation: consider rebalancing
```

## Disaster Recovery

### RTO/RPO Targets

```
Critical Services (e.g., Auth, Payment):
├─ RTO: 5 minutes
├─ RPO: 1 minute
└─ Strategy: Multi-region active-active

Important Services (e.g., Streaming):
├─ RTO: 15 minutes
├─ RPO: 5 minutes
└─ Strategy: Multi-region active-passive

Standard Services (e.g., Recommendations):
├─ RTO: 1 hour
├─ RPO: 1 hour
└─ Strategy: Automated restoration from backups
```

### Backup Strategy

```
PostgreSQL
├─ WAL archiving: continuous to S3
├─ Full backup: daily at 2am UTC
├─ Point-in-time recovery: 30 days
└─ Cross-region replication: S3 bucket replication

Redis
├─ Snapshot: every 6 hours to S3
├─ AOF (Append-Only File): disabled (BGSAVE sufficient)
└─ Cross-region: active-active cluster

Elasticsearch
├─ Snapshot: daily to S3
├─ Repository rotation: keep 30 days
└─ Verification: restore test weekly
```

---

This architecture provides the foundation for a production-grade OTT platform. Each component is designed for:
- **10M+ users** at launch
- **1M+ concurrent streams** capability
- **99.99% availability** (4 nines)
- **Sub-second API latency** (p99 < 500ms)
- **Real-time event processing** (< 5s latency)
