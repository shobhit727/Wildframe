# 📊 Monitoring & Observability

**Version**: 1.0.0  
**Last Updated**: May 28, 2026  
**Stability**: Production-Ready

## Overview

Wildframe uses a comprehensive observability stack to monitor system health, performance, and user experience. This guide covers metrics collection, logging, tracing, and alerting.

**Time to read**: 20 minutes  
**Prerequisites**: Understanding of monitoring concepts, Prometheus/Grafana basics

## Table of Contents

1. [Observability Stack](#observability-stack)
2. [Metrics](#metrics)
3. [Logging](#logging)
4. [Distributed Tracing](#distributed-tracing)
5. [Dashboards](#dashboards)
6. [Alerting](#alerting)
7. [On-Call Procedures](#on-call-procedures)

---

## Observability Stack

```
┌─────────────────────┐
│   Applications      │ (Services emitting metrics, logs, traces)
└──────────┬──────────┘
           │
    ┌──────┴──────┬────────────┬────────────┐
    │             │            │            │
┌───▼───┐  ┌─────▼──┐  ┌─────▼──┐  ┌─────▼──┐
│Promtail│  │ Otel   │  │ Statsd │  │ Spans  │
│(logs)  │  │Exporter│  │Exporter│  │Exporter│
└───┬───┘  └────┬───┘  └────┬───┘  └────┬───┘
    │           │           │           │
    ▼           ▼           ▼           ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────┐
│  Loki    │ │Prometheus│ │Prometheus│ │Jaeger│
│(logs)    │ │(metrics) │ │(metrics) │ │(traces)
└──────┬───┘ └──────┬───┘ └──────┬───┘ └──┬───┘
       │           │            │        │
       └───────┬───┴────────────┴───┬────┘
               │                    │
          ┌────▼─────────┐    ┌─────▼────┐
          │  Grafana      │    │  Jaeger   │
          │(Dashboards)   │    │  (Traces) │
          └────┬──────────┘    └─────┬─────┘
               │                     │
               └──────────┬──────────┘
                          │
                    ┌─────▼──────┐
                    │AlertManager │
                    │ (Alerting)  │
                    └─────┬───────┘
                          │
               ┌──────────┴──────────┐
               │                     │
          ┌────▼────┐        ┌──────▼──┐
          │  Email  │        │  Slack  │
          │Notifications│  │Notifications│
          └─────────┘        └─────────┘
```

---

## Metrics

### Metric Collection

All services emit metrics in Prometheus format:

```bash
# Prometheus scrapes metrics from each service every 15 seconds
GET http://auth-service:8000/metrics
```

Example metrics response:

```
# HELP python_gc_objects_collected_total Objects collected during gc
# TYPE python_gc_objects_collected_total counter
python_gc_objects_collected_total{generation="0"} 321.0

# HELP fastapi_requests_total Total HTTP requests
# TYPE fastapi_requests_total counter
fastapi_requests_total{method="POST",path="/auth/login",status="200"} 1543.0
fastapi_requests_total{method="POST",path="/auth/login",status="401"} 24.0

# HELP fastapi_request_duration_seconds HTTP request latency in seconds
# TYPE fastapi_request_duration_seconds histogram
fastapi_request_duration_seconds_bucket{method="POST",path="/auth/login",le="0.005"} 324.0
fastapi_request_duration_seconds_bucket{method="POST",path="/auth/login",le="0.01"} 1402.0
fastapi_request_duration_seconds_bucket{method="POST",path="/auth/login",le="0.05"} 1521.0
fastapi_request_duration_seconds_bucket{method="POST",path="/auth/login",le="+Inf"} 1543.0

# HELP database_connection_pool_size Connection pool current size
# TYPE database_connection_pool_size gauge
database_connection_pool_size{service="auth_db"} 10.0

# HELP database_connection_pool_available Available connections in pool
# TYPE database_connection_pool_available gauge
database_connection_pool_available{service="auth_db"} 7.0
```

### Key Metrics by Service

#### Auth Service

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `auth_registrations_total` | Counter | endpoint, status | Total user registrations |
| `auth_login_attempts_total` | Counter | endpoint, status | Total login attempts |
| `auth_token_generation_seconds` | Histogram | endpoint | Time to generate JWT |
| `auth_failed_password_resets_total` | Counter | reason | Failed password resets |
| `auth_rate_limit_hits_total` | Counter | endpoint | Rate limit violations |

#### User Service

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `user_profiles_active_total` | Gauge | none | Total active user profiles |
| `user_devices_registered_total` | Counter | device_type | Registered devices by type |
| `user_sessions_active_total` | Gauge | none | Active user sessions |
| `user_watch_history_entries_total` | Counter | content_type | Watch history entries |

#### Content Service

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `content_items_total` | Gauge | content_type | Total content items |
| `content_requests_total` | Counter | content_id, status | Content requests |
| `content_search_latency_seconds` | Histogram | index | Search query latency |
| `content_ingestion_duration_seconds` | Histogram | source | Content ingestion time |

#### Streaming Service

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `streaming_sessions_active_total` | Gauge | none | Active streaming sessions |
| `streaming_bandwidth_bytes_total` | Counter | quality, region | Bandwidth consumed |
| `streaming_segment_duration_seconds` | Histogram | quality | Segment delivery time |
| `streaming_errors_total` | Counter | error_type | Streaming errors |

### Accessing Prometheus

```bash
# Port forward to Prometheus
kubectl port-forward svc/prometheus 9090:9090 -n wildframe

# Query metrics via API
curl 'http://localhost:9090/api/v1/query?query=fastapi_requests_total'

# Example response:
# {
#   "data": {
#     "result": [
#       {
#         "metric": {"method": "POST", "path": "/auth/login", "status": "200"},
#         "value": [1685268000, "1543"]
#       }
#     ]
#   }
# }
```

---

## Logging

### Log Collection

Services emit structured JSON logs. Promtail collects and sends to Loki:

```bash
# Service logs (structured JSON)
{"level": "INFO", "timestamp": "2026-05-28T15:30:00Z", "message": "User login successful", "user_id": "123e4567", "email": "user@example.com"}

# Loki stores and indexes for querying
```

### Accessing Logs

```bash
# Port forward to Grafana Explore
kubectl port-forward svc/grafana 3000:3000 -n wildframe

# Then in Grafana UI:
# 1. Click "Explore"
# 2. Select "Loki" data source
# 3. Enter query: {service="auth-service"}
# 4. Click "Run query"
```

### Log Query Examples

```loki
# All logs from auth service
{service="auth-service"}

# Error level logs
{service="auth-service"} | level="ERROR"

# Login attempts in last hour
{service="auth-service"} | message="User login" | last=1h

# Requests slower than 1 second
{service="auth-service"} | duration_seconds > 1

# Failed database queries
{service="user-service"} | message=~"database.*error"
```

---

## Distributed Tracing

### Trace Collection

Services emit trace spans for every request. Jaeger collects and visualizes:

```bash
# Service: auth-service, Operation: POST /auth/login
Trace ID: 4bf92f3577b34da6a3ce929d0e0e4736
├── Span: api_request (2.3ms)
│   ├── Span: validate_email (0.1ms)
│   ├── Span: hash_password (1.2ms)
│   ├── Span: database_insert (0.8ms)
│   └── Span: send_email (0.2ms)
```

### Accessing Jaeger

```bash
# Port forward to Jaeger UI
kubectl port-forward svc/jaeger 16686:16686 -n wildframe

# Open http://localhost:16686
```

### Trace Analysis

1. **Service Topology**: View all services communicating in your request
2. **Critical Path**: Identify which operations take the most time
3. **Error Detection**: See where errors occur in the request chain
4. **Performance Bottlenecks**: Identify slow database queries, API calls, etc.

---

## Dashboards

### Pre-built Dashboards

#### 1. System Overview

```
Wildframe System Overview
├── Service Status (up/down)
├── Request Rate (requests/sec)
├── Error Rate (errors/sec)
├── Latency (p50, p95, p99)
├── CPU Usage by Service
├── Memory Usage by Service
└── Database Connections
```

#### 2. Auth Service Dashboard

```
Auth Service Metrics
├── Registrations (daily)
├── Login Success Rate
├── Failed Login Attempts
├── Token Generation Latency
├── Password Reset Success Rate
├── Rate Limit Hits
└── Database Query Performance
```

#### 3. User Service Dashboard

```
User Service Metrics
├── Active User Profiles
├── Device Registrations
├── Session Activity
├── Watch History Growth
├── User Preference Changes
└── Session Error Rate
```

#### 4. Content Service Dashboard

```
Content Service Metrics
├── Total Content Items
├── Content Search Queries
├── Search Latency
├── Content Ingestion Status
├── Popular Content
└── Genre Distribution
```

#### 5. Streaming Service Dashboard

```
Streaming Service Metrics
├── Active Streams
├── Bandwidth Usage
├── Video Quality Distribution
├── Playback Errors
├── Regional Distribution
└── Device Types
```

### Creating Custom Dashboards

```bash
# 1. In Grafana UI, click "+" → "New Dashboard"
# 2. Add panels with queries like:

# Query: Request rate over time
rate(fastapi_requests_total[1m])

# Query: Error rate percentage
rate(fastapi_requests_total{status=~"5.."}[1m]) / rate(fastapi_requests_total[1m]) * 100

# Query: P95 request latency
histogram_quantile(0.95, fastapi_request_duration_seconds)

# Query: Database connection pool usage
database_connection_pool_size - database_connection_pool_available
```

---

## Alerting

### Alert Rules

Alerts are triggered based on metric thresholds:

```yaml
# prometheus-rules.yaml
groups:
- name: wildframe_alerts
  rules:
  - alert: HighErrorRate
    expr: rate(fastapi_requests_total{status=~"5.."}[5m]) > 0.05
    for: 5m
    annotations:
      summary: "High error rate detected (>5%)"
      description: "Service {{ $labels.service }} has error rate of {{ $value }}"
  
  - alert: HighLatency
    expr: histogram_quantile(0.95, fastapi_request_duration_seconds) > 1
    for: 10m
    annotations:
      summary: "High request latency"
      description: "P95 latency is {{ $value }}s"
  
  - alert: DatabaseConnectionPoolExhausted
    expr: database_connection_pool_available < 1
    for: 2m
    annotations:
      summary: "Database connection pool exhausted"
      description: "No available connections for {{ $labels.service }}"
  
  - alert: ServiceDown
    expr: up == 0
    for: 1m
    annotations:
      summary: "Service is down"
      description: "{{ $labels.service }} has not been responding for 1 minute"
```

### Notification Channels

Alerts are sent to:

1. **Email** - Critical alerts sent to ops team
2. **Slack** - All alerts posted to #wildframe-alerts channel
3. **PagerDuty** - Critical alerts trigger on-call rotation
4. **Dashboard** - All alerts visible in Grafana UI

### Managing Alerts

```bash
# View active alerts
curl 'http://prometheus:9090/api/v1/alerts'

# Silence an alert
# In Grafana: Click alert → "Manage alerts" → "Silence alert"
# Specify duration (1 hour, 1 day, permanently)

# Re-enable silenced alert
# In Grafana: Click "Silence" badge → "Unsilence"
```

---

## On-Call Procedures

### Alert Response Playbook

#### HighErrorRate Alert

```
1. Identify affected service
   - Check which service is in the alert
   - Look at error logs in Loki

2. Determine error type
   - Database errors?
   - API timeouts?
   - Validation failures?
   - Rate limiting?

3. Quick fixes
   - Restart service:
     kubectl rollout restart deployment auth-service -n wildframe
   - Scale up replicas if CPU/memory high:
     kubectl scale deployment auth-service --replicas=5 -n wildframe
   - Check dependent services (database, cache)

4. If fix fails
   - Rollback to previous version:
     kubectl rollout undo deployment auth-service -n wildframe
   - Escalate to team lead
```

#### HighLatency Alert

```
1. Check database performance
   - Slow queries: SELECT * FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10
   - Connection pool: Check available connections
   - Indexing: Verify indexes on frequently queried columns

2. Check infrastructure
   - CPU usage: kubectl top pods -n wildframe
   - Memory usage: kubectl describe node | grep -A 5 "Allocated resources"
   - Network: Check inter-pod communication

3. Quick fixes
   - Scale up replicas
   - Increase resource limits
   - Kill slow queries (with caution)

4. Long-term fixes
   - Add database indexes
   - Optimize slow queries
   - Implement caching
```

#### ServiceDown Alert

```
1. Check service status
   kubectl get pod <service-name>-xxx -n wildframe
   kubectl logs <service-name>-xxx -n wildframe

2. Verify dependencies
   - Is database reachable?
   - Is Redis available?
   - Is Kafka broker running?

3. Restart service
   kubectl delete pod <service-name>-xxx -n wildframe
   # Kubernetes will auto-restart

4. If still failing
   - Check infrastructure
   - Rollback recent deployment
   - Escalate to infrastructure team
```

---

## SLOs & SLIs

### Service Level Objectives

| Metric | Target | SLI |
|--------|--------|-----|
| **Availability** | 99.9% | Service uptime (is up == 1) |
| **Latency** | P95 < 500ms | histogram_quantile(0.95, request_duration_seconds) |
| **Error Rate** | < 0.1% | rate(requests_total{status=~"5.."}[5m]) |

### Error Budget

- Monthly availability target: 99.9% = 43.2 minutes of downtime allowed
- Current month used: 12.5 minutes (29% of budget)
- Remaining: 30.7 minutes

---

## See Also

- [Prometheus Docs](https://prometheus.io/docs/)
- [Grafana Docs](https://grafana.com/docs/)
- [Jaeger Docs](https://www.jaegertracing.io/docs/)
- [Loki Docs](https://grafana.com/docs/loki/)
- [Operations Guide](OPERATIONS.md)
