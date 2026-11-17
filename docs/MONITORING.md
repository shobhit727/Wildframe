# 📊 Monitoring & Observability

**Version**: 1.1.0  
**Last Updated**: November 17, 2026  
**Stability**: Partial — metrics, dashboards and dev tracing work; log shipping and alerting do not exist yet (see [Known gaps](#known-gaps))

## Overview

Wildframe's observability stack runs in the local compose environment (`deployments/docker-compose.dev.yml`). This guide covers what is actually deployed today: metrics, logging, tracing, dashboards and the current state of alerting.

The Helm chart (`infrastructure/helm/wildframe/`) deploys only the application services. It ships no Prometheus, Grafana, Loki, Jaeger or Alertmanager; pods carry `prometheus.io/scrape` annotations for an externally operated Prometheus (`templates/deployment.yaml`). Note that such scrapes currently fail: the chart defaults each service's `ENVIRONMENT` to `production` but never sets `METRICS_TOKEN`, so the `/metrics` guard returns 401 to every caller (see [Metric Collection](#metric-collection)).

**Time to read**: 15 minutes  
**Prerequisites**: Understanding of monitoring concepts, Prometheus/Grafana basics

## Table of Contents

1. [Observability Stack](#observability-stack)
2. [Known gaps](#known-gaps)
3. [Metrics](#metrics)
4. [Logging](#logging)
5. [Distributed Tracing](#distributed-tracing)
6. [Dashboards](#dashboards)
7. [Alerting](#alerting)
8. [On-Call Procedures](#on-call-procedures)

---

## Observability Stack

What `deployments/docker-compose.dev.yml` runs on current `main`:

```
┌────────────────────────────────────────────┐
│ 15 FastAPI services                        │
│  - /metrics (prometheus_client)            │
│  - logs to stdout (mostly JSON)            │
│  - OTLP spans (only where JAEGER_ENABLED)  │
└──────┬─────────────────┬──────────────┬────┘
       │ scrape :8000    │ OTLP gRPC    │ stdout only
       │ (bearer token)  │ jaeger:4317  │ (no shipper)
       ▼                 ▼              ✕
┌────────────┐     ┌──────────┐    ┌──────────┐
│ Prometheus │◄─┐  │  Jaeger  │    │   Loki   │ (running, receives nothing)
└─────┬──────┘  │  └──────────┘    └────┬─────┘
      │         │                       │
      │   postgres-exporter :9187       │
      │   redis-exporter    :9121       │
      ▼                                 ▼
┌──────────────────────────────────────────┐
│ Grafana (datasources: Prometheus, Loki)  │
└──────────────────────────────────────────┘

No Alertmanager, no alert rules, no notification channels.
```

| Component | Compose service | Host access (dev) |
|-----------|-----------------|-------------------|
| Prometheus | `prometheus` | `https://localhost:9090` (via Caddy) |
| Grafana | `grafana` | `https://localhost:3001` (Grafana serves TLS itself, bound to 127.0.0.1) |
| Loki | `loki` | `https://localhost:3100` (via Caddy) |
| Jaeger UI | `jaeger` | `https://localhost:16686` (via Caddy) |
| Postgres exporter | `postgres-exporter` | `http://localhost:9187` (published on all host interfaces) |
| Redis exporter | `redis-exporter` | `http://localhost:9121` (published on all host interfaces) |

Host port 3000 is the web app (Caddy), not Grafana. Certificates are self-signed by `scripts/generate-dev-certs.sh`; use `curl -k` or `--cacert`.

## Known gaps

These are tracked and in flight; the doc describes `main` as it is, not as planned:

- **Alerting** — no Prometheus `rule_files`, no Alertmanager, no receivers: [#1117](https://github.com/shobhit727/Wildframe/issues/1117).
- **Log shipping** — Loki runs and is a Grafana datasource, but no Promtail/Alloy/collector or Docker logging driver sends it anything: [#1118](https://github.com/shobhit727/Wildframe/issues/1118).
- **Tracing coverage** — only 5 of 15 services set `JAEGER_ENABLED` in compose, and Helm sets it for none: [#1129](https://github.com/shobhit727/Wildframe/issues/1129). No trace context is propagated between services, so each service's spans form separate traces.
- **Helm metrics scraping** — `METRICS_TOKEN` is not set by the chart, so `/metrics` returns 401 in Kubernetes (no tracking issue yet).

---

## Metrics

### Metric Collection

All 15 services expose Prometheus-format metrics on `/metrics`. Prometheus scrapes every 15 seconds (`infrastructure/prometheus/prometheus.yml`) with jobs:

| Job | Targets |
|-----|---------|
| `prometheus` | `localhost:9090` |
| `wildframe-services` | the 15 services on `:8000`, `bearer_token_file: /etc/prometheus/metrics_bearer_token` |
| `postgres` | `postgres-exporter:9187` |
| `redis` | `redis-exporter:9121` |

Every service gates `/metrics` with `require_metrics_token`: when `ENVIRONMENT=production` the request must carry `Authorization: Bearer $METRICS_TOKEN`, otherwise it gets a 401; if `METRICS_TOKEN` is empty, every request gets a 401. In other environments the endpoint is open.

```bash
# Inside wildframe-network (not executed in this doc review)
curl -H "Authorization: Bearer $METRICS_TOKEN" http://auth-service:8000/metrics
```

### Application metrics

Application metrics come from one shared middleware, `wildframe_observability.metrics.MetricsMiddleware` (`packages/sdk/wildframe_observability/metrics.py`). No service defines its own business metrics.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `http_requests_total` | Counter | `method`, `endpoint`, `status_code`, `service` | HTTP requests handled |
| `http_request_duration_seconds` | Histogram | `method`, `endpoint`, `service` | Request latency; buckets 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10 |
| `http_active_requests` | Gauge | `service` | Requests in flight |

`endpoint` is the route template (e.g. `/api/v1/auth/login`), or `unmatched` for requests that match no route. The prometheus_client default collectors also emit `process_*` and `python_gc_*` metrics; these carry no `service` label, so only the scrape's `instance` label tells services apart.

Database and cache metrics come from the exporters (`pg_*`, `redis_*`), e.g. `pg_stat_activity_count` and `pg_settings_max_connections` (Postgres `max_connections` is 200, set in `infrastructure/database/init-databases.sql`).

Example scrape (abridged, illustrative values):

```
# HELP http_requests_total Total HTTP requests
# TYPE http_requests_total counter
http_requests_total{endpoint="/api/v1/auth/login",method="POST",service="auth-service",status_code="200"} 1543.0
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{endpoint="/api/v1/auth/login",le="0.05",method="POST",service="auth-service"} 1521.0
```

### Accessing Prometheus

```bash
# Dev compose, via Caddy (not executed in this doc review — needs the running stack)
curl -skG 'https://localhost:9090/api/v1/query' \
  --data-urlencode 'query=sum by (service) (rate(http_requests_total[5m]))'
```

---

## Logging

### Log format

Services log to stdout; auth-service and content-service also write a log file. The format depends on the service:

- **SDK `JSONFormatter`** (`packages/sdk/wildframe_observability/logging.py`), installed by `wire_observability`. One JSON object per line with `timestamp`, `level`, `logger`, `message`, `request_id`, `correlation_id`, `service_name`, plus any `extra={...}` fields and `exception` when an exception is attached. Keys matching `REDACT_FIELDS` (passwords, tokens, secrets, …) are replaced with `***REDACTED***`; values are stripped of control characters and ANSI colour codes.
- **user-service, content-service** replace it at startup with their own `dictConfig` (`app/core/logging.py`) using `pythonjsonlogger` (fields such as `asctime`, `levelname`, `name`), without `service_name` and without the SDK redaction.
- **auth-service** replaces it with `app/core/logging.py`: plain text when `ENVIRONMENT=development`, otherwise `CorrelationIdJsonFormatter` (adds `service`, `version`, `environment`).

Example SDK `JSONFormatter` line:

```json
{"timestamp": "2026-11-17T10:30:00.123456+00:00", "level": "INFO", "logger": "billing.service", "message": "payout accrued", "request_id": "6f1c…", "correlation_id": "6f1c…", "service_name": "billing", "creator_id": "c0ffee…", "amount": 42.0}
```

Do not log unnecessary personal data such as email addresses (AGENTS.md §14).

### Accessing logs

There is no log shipper, so Loki holds no service logs ([#1118](https://github.com/shobhit727/Wildframe/issues/1118)). Read logs from the containers:

```bash
docker compose -f deployments/docker-compose.dev.yml logs -f auth-service
```

Once a shipper lands, query Loki from Grafana Explore with a parser stage, e.g. `{<stream selector>} | json | level="ERROR"` — the stream labels depend on the shipper's config.

---

## Distributed Tracing

### Trace Collection

`wire_observability` (`packages/sdk/wildframe_observability/wire.py`) enables OpenTelemetry tracing only when `JAEGER_ENABLED=true`, exporting OTLP gRPC to `OTEL_EXPORTER_OTLP_ENDPOINT` (`http://jaeger:4317` in compose; Jaeger runs with `COLLECTOR_OTLP_ENABLED=true`).

In compose, `JAEGER_ENABLED: 'true'` is set only for **api-gateway, auth-service, user-service, content-service, admin-service**. The other 10 services emit no spans. The Helm chart sets neither `JAEGER_ENABLED` nor `OTEL_EXPORTER_OTLP_ENDPOINT`, so tracing is off in Kubernetes ([#1129](https://github.com/shobhit727/Wildframe/issues/1129)).

Spans come only from FastAPI auto-instrumentation of incoming requests. Outgoing HTTP calls are not instrumented and no trace context is injected, so a request through api-gateway and a backend produces separate traces. auth-service has a `setup_tracing()` helper for Redis/SQLAlchemy in `app/telemetry/__init__.py`, but nothing in `app/` calls it. There are no hand-written business spans.

### Accessing Jaeger

Dev compose: open `https://localhost:16686` (Caddy → `jaeger:16686`).

### Trace Analysis

1. **Per-service request spans**: Latency and status of each incoming request in the 5 traced services
2. **Slow endpoints**: Find the slowest requests per service and operation
3. **Error Detection**: See which requests ended in errors

Cross-service topology, critical path and database spans are not available until context propagation and client/DB instrumentation exist.

---

## Dashboards

### Provisioned Dashboards

Grafana provisions three dashboards from `infrastructure/grafana/provisioning/dashboards/`:

| File | Title | Panels |
|------|-------|--------|
| `wildframe-overview.json` | Wildframe Overview | Requests per second by service, Error rate (5xx), Request duration p95 by service, Active requests, Top endpoints by request rate |
| `wildframe-service-health.json` | Wildframe — Service Health | Services up/down, 5xx/4xx rate, p95 latency, Total RPS, Scrape health by target, Error ratio by service, request rate / p95 latency / active requests by service, busiest endpoints, runtime (CPU, resident memory, open FDs, GC) |
| `wildframe-data-services.json` | Wildframe — Data Services (Postgres, Redis) | Postgres exporter scrape OK, Database size (and over time), Connections in use, Connection usage against limit, Redis memory, clients, ops/s, cache hit ratio, ACL denials |

Known panel caveats: the runtime panels aggregate `process_*`/`python_gc_*` `by (service)`, but those series carry no `service` label, so they collapse into one series; "Connection usage against limit" divides by `pg_database_connection_limit`, which is `-1` for every database here (no per-database limit is set), so it does not show real headroom.

There are no per-service (auth/user/content/streaming) dashboards.

### Creating Custom Dashboards

In Grafana, **Dashboards → New dashboard**, then add panels with queries like:

```promql
# Request rate by service
sum by (service) (rate(http_requests_total[1m]))

# 5xx error ratio (%) by service
sum by (service) (rate(http_requests_total{status_code=~"5.."}[5m]))
  / sum by (service) (rate(http_requests_total[5m])) * 100

# P95 request latency by service
histogram_quantile(0.95, sum by (le, service) (rate(http_request_duration_seconds_bucket[5m])))

# Postgres connections used vs server max_connections
sum(pg_stat_activity_count) / max(pg_settings_max_connections)
```

---

## Alerting

**No alerting exists on `main`.** `infrastructure/prometheus/prometheus.yml` has no `rule_files` or `alerting` block, there is no Alertmanager service, Grafana provisions no alert rules or contact points, and no email/Slack/PagerDuty receiver is configured. Adding rules and Alertmanager is tracked in [#1117](https://github.com/shobhit727/Wildframe/issues/1117); see [OPERATIONS.md](OPERATIONS.md) once it lands.

Until then, watch the **Scrape health by target** panel on the Service Health dashboard, or query `count(up{job="wildframe-services"} == 0) or vector(0)` for the number of down targets. (The **Services down** stat panel uses `sum(up{…} == 0)`, which always sums to 0, so it does not show outages.)

The playbooks below are written against the conditions an alert would cover (high error ratio, high latency, service down), using the queries above.

---

## On-Call Procedures

Kubernetes commands below use the base chart namespace `wildframe` (`values.yaml`). Staging uses `wildframe-staging` and production `wildframe-production` — substitute accordingly. Deployment names equal the service key (e.g. `auth-service`).

### Response Playbook

#### High error rate

```
1. Identify affected service
   - "Error ratio by service" panel (Service Health dashboard)
   - Container logs (Loki has no service logs yet, #1118):
     docker compose -f deployments/docker-compose.dev.yml logs <service>   # dev
     kubectl logs deploy/<service> -n wildframe                            # k8s

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

#### High latency

```
1. Check database performance
   - Connections: "Connections in use" panel (Data Services dashboard), or
     sum(pg_stat_activity_count) / max(pg_settings_max_connections)
   - Active queries: SELECT pid, now() - query_start AS age, query
       FROM pg_stat_activity WHERE state = 'active' ORDER BY age DESC;
     (pg_stat_statements is not enabled in this repo's Postgres)
   - Indexing: Verify indexes on frequently queried columns

2. Check infrastructure
   - CPU usage: kubectl top pods -n wildframe   (requires metrics-server)
   - Memory requests/limits: kubectl describe node | grep -A 5 "Allocated resources"
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

#### Service down

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

### Service Level Objectives (targets, not yet encoded)

These targets are not implemented as recording rules or alerts anywhere in the repo.

| Metric | Target | SLI |
|--------|--------|-----|
| **Availability** | 99.9% | `avg_over_time(up{job="wildframe-services"}[15d])` (Prometheus retention is 15 days, so a 30-day window needs longer retention or remote storage) |
| **Latency** | P95 < 500ms | `histogram_quantile(0.95, sum by (le, service) (rate(http_request_duration_seconds_bucket[5m])))` |
| **Error Rate** | < 0.1% | `sum(rate(http_requests_total{status_code=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))` |

### Error Budget

- Monthly availability target: 99.9% = 43.2 minutes of downtime allowed (30-day month)
- Budget consumption is not tracked yet (no SLO recording rules).

---

## See Also

- [Prometheus Docs](https://prometheus.io/docs/)
- [Grafana Docs](https://grafana.com/docs/)
- [Jaeger Docs](https://www.jaegertracing.io/docs/)
- [Loki Docs](https://grafana.com/docs/loki/)
- [Operations Guide](OPERATIONS.md)
