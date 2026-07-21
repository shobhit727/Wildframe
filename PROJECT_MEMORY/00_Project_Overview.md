# 00_Project_Overview

## Project Name
Wildframe - OTT Streaming Platform (FastAPI Microservices)

## Architecture
- 12 independent FastAPI microservices
- Database-per-service pattern (each service has its own PostgreSQL DB)
- Async everywhere (async SQLAlchemy, redis.asyncio)
- JWT auth (15-min access + 7-day refresh tokens)
- Kafka for inter-service events
- OpenTelemetry tracing, Prometheus metrics, Grafana

## Service Layout
```
services/
├── api-gateway/          # :8000 routing, auth, rate limiting
├── auth-service/         # :8001 JWT auth, refresh tokens
├── user-service/         # :8002 profiles, devices, sessions
├── content-service/      # :8003 movies/shows/seasons/episodes
├── streaming-service/    # :8004 HLS/DASH manifests
├── search-service/       # :8005 Elasticsearch search
├── admin-service/        # :8006 moderation, flags, alerts
├── recommendation-service/ # :8007 ML recommendations
├── billing-service/      # :8008 subscriptions + Stripe
├── analytics-service/    # :8009 event analytics
├── notification-service/ # :8010 multi-channel notifications
└── media-pipeline/       # :8011 video transcoding
```

## Frontend
- apps/web/ - Next.js 15 frontend

## Key Patterns
- App factory: `create_app()` in `app/main.py`, `app = create_app()` at module level
- Layering: api/routes/ → services/ → repositories/ → models/
- Dependency injection: `Depends(get_db)` async generator yielding AsyncSession
- Health checks: `GET /health` running `SELECT 1` via `text()`
- Settings: pydantic-settings BaseSettings in `app/core/settings.py`

## CRITICAL ISSUES
1. auth-service, user-service, content-service, streaming-service, admin-service have syntax errors preventing startup
2. billing-service, notification-service, search-service, recommendation-service, analytics-service are empty directories
3. Directory naming inconsistency: some use `-service` suffix, some don't
4. Legacy Django code in netflix_backend/ should be removed
5. Multiple services have conflicting model definitions (two Base instances)

## Confidence: HIGH
Evidence: Read all service directories, main.py files, settings files, docker-compose, AGENTS.md
