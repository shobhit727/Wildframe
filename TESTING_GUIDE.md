# Testing Guide - Wildframe Netflix Backend

> ⚠️ Historical document retained for project history (per `STATUS.md`).
> It predates the current repo layout and is **not** authoritative. Today:
> tests live in each service's `tests/` dir and must run per-service (see
> `AGENTS.md` / `docs/TEST_GUIDE.md`); a live-stack integration suite lives
> in `tests/integration/`; and host-facing traffic only reaches services via
> the Caddy proxy at `https://localhost:8000/<service>/api/v1/...` — direct
> host ports like 8001/8002/8003 in the curl examples below are not bound.

## Quick Start

### 1. Unit & Integration Tests

Run tests for all services:
```bash
cd /home/phoenix/Desktop/wildframe
./run_all_tests.sh
```

Run tests for specific service:
```bash
cd services/streaming-service
python -m pytest app/tests -v
```

Run tests with coverage:
```bash
cd services/streaming-service
python -m pytest app/tests --cov=app --cov-report=html
```

### 2. Start Services

Start all Docker containers:
```bash
cd /home/phoenix/Desktop/wildframe
./start_services.sh
```

Or manually with docker-compose:
```bash
docker-compose -f deployments/docker-compose.dev.yml up -d
```

Check service health:
```bash
docker-compose -f deployments/docker-compose.dev.yml ps
```

### 3. API Testing

#### Auth Service (Port 8001)

Register user:
```bash
curl -X POST https://localhost:8001/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "SecurePass123!",
    "first_name": "John",
    "last_name": "Doe"
  }'
```

Login:
```bash
curl -X POST https://localhost:8001/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "SecurePass123!"
  }'
```

Verify token:
```bash
curl -X GET https://localhost:8001/auth/verify \
  -H "Authorization: Bearer {TOKEN}"
```

#### User Service (Port 8002)

Get user profile:
```bash
curl -X GET https://localhost:8002/users/me \
  -H "Authorization: Bearer {TOKEN}"
```

Update preferences:
```bash
curl -X PUT https://localhost:8002/users/preferences \
  -H "Authorization: Bearer {TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "language": "en",
    "subtitle_preference": "english"
  }'
```

#### Content Service (Port 8003)

List movies:
```bash
curl -X GET "https://localhost:8003/content/movies?limit=10&offset=0"
```

Search content:
```bash
curl -X GET "https://localhost:8003/content/search?q=action&type=movie"
```

Get movie details:
```bash
curl -X GET https://localhost:8003/content/movies/{movie_id}
```

#### Streaming Service (Port 8004)

Start streaming session:
```bash
curl -X POST https://localhost:8004/streaming/session/start \
  -H "Authorization: Bearer {TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "content_id": "{CONTENT_ID}",
    "device_id": "device-001"
  }'
```

Get video manifest:
```bash
curl -X GET "https://localhost:8004/streaming/manifest/{CONTENT_ID}?quality=1080p"
```

Update watch position:
```bash
curl -X PUT https://localhost:8004/streaming/session/{SESSION_ID}/position \
  -H "Content-Type: application/json" \
  -d '{"position_seconds": 1200}'
```

Get watch history:
```bash
curl -X GET https://localhost:8004/streaming/watch-history/{USER_ID}
```

#### Search Service (Port 8005)

Search content:
```bash
curl -X GET "https://localhost:8005/search/query?q=action&content_type=movie&limit=20"
```

Get trending:
```bash
curl -X GET "https://localhost:8005/search/trending?content_type=movie&limit=10"
```

#### Recommendation Service (Port 8007)

Get recommendations:
```bash
curl -X GET "https://localhost:8007/recommendations/for-user/{USER_ID}?limit=20"
```

Update preferences:
```bash
curl -X PUT https://localhost:8007/recommendations/preferences/{USER_ID} \
  -H "Content-Type: application/json" \
  -d '{"liked_genres": ["Action", "Thriller"]}'
```

#### Billing Service (Port 8008)

Get subscription:
```bash
curl -X GET https://localhost:8008/billing/subscription/{USER_ID}
```

Upgrade subscription:
```bash
curl -X POST https://localhost:8008/billing/upgrade/{USER_ID} \
  -H "Content-Type: application/json" \
  -d '{"tier": "premium"}'
```

#### Analytics Service (Port 8009)

Log event:
```bash
curl -X POST https://localhost:8009/analytics/events \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "{USER_ID}",
    "event_type": "play_started",
    "event_data": {"quality": "1080p"},
    "content_id": "{CONTENT_ID}"
  }'
```

Get user events:
```bash
curl -X GET "https://localhost:8009/analytics/user-events/{USER_ID}?limit=100"
```

#### Notification Service (Port 8010)

Send notification:
```bash
curl -X POST https://localhost:8010/notifications/send \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "{USER_ID}",
    "title": "New Content Available",
    "message": "Check out the new releases",
    "channel": "in-app"
  }'
```

#### Media Pipeline Service (Port 8011)

Start transcoding:
```bash
curl -X POST https://localhost:8011/media/transcode \
  -H "Content-Type: application/json" \
  -d '{
    "content_id": "{CONTENT_ID}",
    "source_url": "https://example.com/video.mp4"
  }'
```

Get transcoding status:
```bash
curl -X GET https://localhost:8011/media/job-status/{CONTENT_ID}
```

### 4. Monitoring

- **Prometheus**: https://localhost:9090
- **Grafana**: https://localhost:3000 (admin/admin)
- **Jaeger**: https://localhost:16686
- **Loki**: https://localhost:3100

### 5. Database Operations

Connect to PostgreSQL:
```bash
psql -h localhost -U wildframe -d postgres
```

View all databases:
```sql
\l
```

Connect to specific database:
```sql
\c streaming_db
```

View tables:
```sql
\dt
```

### 6. Cache Operations

Connect to Redis:
```bash
redis-cli -h localhost -p 6379
```

Check all keys:
```bash
KEYS *
```

### 7. Logs

View logs for specific service:
```bash
docker-compose -f deployments/docker-compose.dev.yml logs -f streaming-service
```

View all logs:
```bash
docker-compose -f deployments/docker-compose.dev.yml logs -f
```

## Service Architecture

### 8 Microservices Running on Ports 8000-8011

1. **API Gateway (8000)** - Request routing, authentication, rate limiting
2. **Auth Service (8001)** - User authentication, JWT tokens
3. **User Service (8002)** - User profiles, sessions, preferences
4. **Content Service (8003)** - Movie/show catalog, metadata
5. **Streaming Service (8004)** - Video streaming, watch history
6. **Search Service (8005)** - Full-text search (Elasticsearch)
7. **Admin Service (8006)** - Content moderation, user management
8. **Recommendation Service (8007)** - Personalized recommendations
9. **Billing Service (8008)** - Subscriptions, payments
10. **Analytics Service (8009)** - Event tracking, user behavior
11. **Notification Service (8010)** - Multi-channel notifications
12. **Media Pipeline (8011)** - Video transcoding

### Infrastructure

- **PostgreSQL 15** - 12 databases (one per service)
- **Redis 7** - 11 database slots
- **Elasticsearch 8.10** - Full-text search
- **Kafka 7.5** - Event streaming
- **Prometheus** - Metrics collection
- **Grafana** - Dashboards
- **Jaeger** - Distributed tracing
- **Loki** - Log aggregation

## Troubleshooting

### Service won't start
```bash
# Check logs
docker-compose logs -f {service-name}

# Restart service
docker-compose restart {service-name}
```

### Database connection error
```bash
# Verify PostgreSQL is running
docker-compose ps postgres

# Check database initialization
docker-compose logs postgres
```

### Redis connection error
```bash
# Verify Redis is running
docker-compose ps redis

# Test Redis connection
redis-cli -h localhost ping
```

### Test failures
1. Ensure Docker containers are running
2. Check environment variables in docker-compose.dev.yml
3. Verify database migrations have run
4. Check service logs for errors

## Test Coverage Goals

- **Auth Service**: 85%+
- **User Service**: 80%+
- **Content Service**: 75%+
- **Streaming Service**: 70%+ (video streaming is hard to test)
- **Search Service**: 75%+
- **Recommendation Service**: 70%+
- **Billing Service**: 80%+
- **Analytics Service**: 85%+
- **Notification Service**: 75%+
- **Media Pipeline**: 70%+
- **API Gateway**: 80%+

## Performance Testing

Load test with Apache Bench:
```bash
ab -n 1000 -c 10 https://localhost:8000/health
```

Load test with wrk:
```bash
wrk -t4 -c100 -d30s https://localhost:8000/health
```

## Integration Testing

All services support inter-service communication via:
- **Synchronous**: HTTP/REST (via API Gateway)
- **Asynchronous**: Kafka events
- **Shared state**: Redis cache, PostgreSQL

## Deployment

For production deployment, see [DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md)
