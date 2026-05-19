# Wildframe Operations Guide

Day-to-day operations manual for running the Wildframe platform in production.

## Table of Contents

1. [Daily Operations](#daily-operations)
2. [Monitoring & Alerting](#monitoring--alerting)
3. [Common Tasks](#common-tasks)
4. [Incident Response](#incident-response)
5. [Performance Tuning](#performance-tuning)
6. [Capacity Planning](#capacity-planning)
7. [Maintenance Windows](#maintenance-windows)

## Daily Operations

### Morning Checklist

```bash
# Check overall system health
kubectl get nodes -n wildframe-prod
kubectl get pods -n wildframe-prod --field-selector=status.phase!=Running

# Monitor error rates
curl http://prometheus:9090/api/v1/query?query=rate(http_requests_total{status=~"5.."}[5m])

# Check resource utilization
kubectl top nodes
kubectl top pods -n wildframe-prod

# Review logs for errors
kubectl logs -l app=api-gateway -n wildframe-prod --tail=100 | grep ERROR
```

### Service Health Checks

```bash
# Auth Service
curl -s https://api.wildframe.com/auth/health | jq .

# User Service  
curl -s https://api.wildframe.com/users/health | jq .

# Content Service
curl -s https://api.wildframe.com/content/health | jq .

# Streaming Service
curl -s https://api.wildframe.com/streaming/health | jq .

# All services (via API Gateway)
curl -s https://api.wildframe.com/services/health | jq .
```

### Key Metrics to Monitor

```
# Request Latency (should be < 100ms p95)
rate(http_request_duration_seconds_sum[5m]) / rate(http_requests_total[5m])

# Error Rate (should be < 0.1%)
rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m])

# Database Connection Pool (should stay < 90% utilization)
db_connections_used / db_connections_max

# Cache Hit Rate (should be > 80%)
redis_keyspace_hits / (redis_keyspace_hits + redis_keyspace_misses)

# Kafka Consumer Lag (should be < 1000 messages)
kafka_consumer_lag_sum

# Video Playback Errors (should be < 1%)
video_playback_errors_total / video_sessions_total
```

## Monitoring & Alerting

### Prometheus Dashboard

```bash
# Access Prometheus
kubectl port-forward svc/prometheus 9090:9090 -n monitoring

# Query examples
# P95 latency
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))

# Error rate
rate(http_requests_total{status=~"5.."}[5m])

# Database connection pool usage
db_connections_used{job="postgres"} / on() db_connections_max{job="postgres"}
```

### Grafana Dashboards

Key dashboards to monitor:

1. **System Health Dashboard**
   - Pod/node resource usage
   - Service availability
   - Request rates and errors

2. **Application Performance**
   - Request latency (p50, p95, p99)
   - Error rates by service
   - Database query performance

3. **Infrastructure**
   - EKS node capacity
   - RDS CPU/memory
   - ElastiCache memory usage
   - Network I/O

4. **Video Streaming**
   - Active sessions
   - Bitrate distribution
   - Startup time
   - Playback errors

5. **Business Metrics**
   - Active users
   - Subscriptions
   - Revenue
   - Content popularity

### Alert Rules

**Critical Alerts** (immediate escalation):
```yaml
- AlertName: HighErrorRate
  Condition: rate(http_requests_total{status=~"5.."}[5m]) > 0.01
  Duration: 2m
  Action: Page on-call engineer

- AlertName: ServiceDown
  Condition: up{job=~"auth|users|content|streaming"} == 0
  Duration: 1m
  Action: Page on-call engineer

- AlertName: DatabaseDown
  Condition: up{job="postgres"} == 0
  Duration: 1m
  Action: Page on-call engineer

- AlertName: OutOfMemory
  Condition: container_memory_usage_bytes / container_memory_limit_bytes > 0.95
  Duration: 5m
  Action: Page on-call engineer
```

**Warning Alerts** (team notification):
```yaml
- AlertName: HighLatency
  Condition: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 0.5
  Duration: 5m
  Action: Send to Slack #alerts

- AlertName: HighMemoryUsage
  Condition: container_memory_usage_bytes / container_memory_limit_bytes > 0.8
  Duration: 10m
  Action: Send to Slack #alerts

- AlertName: DiskUsageHigh
  Condition: node_filesystem_avail_bytes / node_filesystem_size_bytes < 0.15
  Duration: 5m
  Action: Send to Slack #alerts
```

## Common Tasks

### Scaling Services

#### Horizontal Scaling

```bash
# Scale auth service to 5 replicas
kubectl scale deployment auth-service --replicas=5 -n wildframe-prod

# Auto-scaling based on CPU
kubectl patch hpa auth-service -p '{"spec":{"minReplicas":3,"maxReplicas":20,"targetCPUUtilizationPercentage":70}}' -n wildframe-prod

# Monitor scaling
kubectl describe hpa auth-service -n wildframe-prod
```

#### Vertical Scaling (Requests & Limits)

```bash
# Update resource requests
kubectl set resources deployment auth-service \
  --requests=cpu=500m,memory=512Mi \
  --limits=cpu=1000m,memory=1Gi \
  -n wildframe-prod
```

### Database Operations

#### Viewing Logs

```bash
# Recent logs
kubectl logs -f deployment/auth-service -n wildframe-prod --tail=100

# With filtering
kubectl logs -f deployment/auth-service -n wildframe-prod --grep="ERROR"

# Multiple containers
kubectl logs -f deployment/auth-service -n wildframe-prod -c auth-service
```

#### Database Backup

```bash
# Create snapshot
aws rds create-db-snapshot \
  --db-instance-identifier wildframe-prod \
  --db-snapshot-identifier wildframe-prod-$(date +%Y%m%d-%H%M%S)

# List snapshots
aws rds describe-db-snapshots \
  --db-instance-identifier wildframe-prod

# Export to S3
aws rds start-export-task \
  --export-task-identifier wildframe-export-$(date +%Y%m%d) \
  --source-arn arn:aws:rds:region:account:snapshot:name \
  --s3-bucket-name wildframe-exports \
  --iam-role-arn arn:aws:iam::account:role/RdsExport
```

#### Database Restore

```bash
# Restore from snapshot
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier wildframe-restore \
  --db-snapshot-identifier wildframe-prod-20240101-120000

# Monitor restore
aws rds describe-db-instances --db-instance-identifier wildframe-restore
```

### Cache Management

#### Redis Operations

```bash
# Connect to Redis
kubectl exec -it svc/redis -- redis-cli

# Monitor Redis
info stats
info memory

# Clear cache
FLUSHDB

# Monitor keys
MONITOR

# Check memory usage
MEMORY STATS
```

#### Cache Invalidation

```bash
# Invalidate specific key
kubectl exec -it svc/redis -- redis-cli DEL "cache:user:123"

# Pattern invalidation
kubectl exec -it svc/redis -- redis-cli EVAL "return redis.call('del', unpack(redis.call('keys', ARGV[1])))" 0 "cache:content:*"
```

### Kafka Operations

```bash
# List topics
kubectl exec -it kafka-0 -- kafka-topics.sh --list --bootstrap-server localhost:9092

# View topic details
kubectl exec -it kafka-0 -- kafka-topics.sh --describe --topic user.registered --bootstrap-server localhost:9092

# Monitor consumer lag
kubectl exec -it kafka-0 -- kafka-consumer-groups.sh --bootstrap-server localhost:9092 --group auth-service-group --describe

# Reset consumer group
kubectl exec -it kafka-0 -- kafka-consumer-groups.sh --bootstrap-server localhost:9092 --group auth-service-group --reset-offsets --to-earliest --execute --all-topics
```

## Incident Response

### P1 Incident: Service Down

1. **Immediate (0-5 min)**
   ```bash
   # Check service status
   kubectl describe pod <pod-name> -n wildframe-prod
   kubectl logs <pod-name> -n wildframe-prod --tail=50
   
   # Check for recent deployments
   kubectl rollout history deployment/<service> -n wildframe-prod
   ```

2. **Diagnosis (5-15 min)**
   ```bash
   # Check events
   kubectl get events -n wildframe-prod --sort-by=.metadata.creationTimestamp
   
   # Check resources
   kubectl top node
   kubectl top pod <pod-name> -n wildframe-prod
   
   # Check dependencies
   # - Database: psql -h db.rds.amazonaws.com -c "SELECT 1"
   # - Redis: redis-cli PING
   # - Kafka: kafka-broker-api-versions.sh --bootstrap-server kafka:9092
   ```

3. **Recovery (15-30 min)**
   ```bash
   # Option A: Restart pod
   kubectl delete pod <pod-name> -n wildframe-prod
   
   # Option B: Rollback
   kubectl rollout undo deployment/<service> -n wildframe-prod
   kubectl rollout status deployment/<service> -n wildframe-prod
   
   # Option C: Scale down and up
   kubectl scale deployment/<service> --replicas=0 -n wildframe-prod
   sleep 10
   kubectl scale deployment/<service> --replicas=3 -n wildframe-prod
   ```

4. **Verification**
   ```bash
   # Check service health
   curl -s https://api.wildframe.com/<service>/health
   
   # Monitor for errors
   kubectl logs -f deployment/<service> -n wildframe-prod
   
   # Check metrics
   # - Error rate should decrease
   # - Latency should return to normal
   ```

### P2 Incident: High Error Rate

1. **Identify root cause**
   ```bash
   # Check error patterns
   kubectl logs deployment/api-gateway -n wildframe-prod | grep ERROR | head -20
   
   # Check Prometheus
   rate(http_requests_total{status=~"5.."}[5m])
   
   # Check specific service
   kubectl logs deployment/<service> -n wildframe-prod --tail=100 | grep -i error
   ```

2. **Mitigation**
   ```bash
   # If database slow: check connections
   SELECT count(*) FROM pg_stat_activity;
   
   # If cache full: clear cache
   redis-cli FLUSHDB
   
   # If rate limit exceeded: scale service
   kubectl scale deployment/<service> --replicas=5 -n wildframe-prod
   ```

### P3 Incident: Degraded Performance

1. **Identify bottleneck**
   - API latency: Check slow queries in logs
   - Database CPU: Check query performance
   - Cache hit rate: Monitor Redis
   - Network: Check bandwidth usage

2. **Optimize**
   ```bash
   # Add index if missing
   ALTER TABLE users ADD INDEX idx_email (email);
   
   # Update statistics
   ANALYZE TABLE users;
   
   # Scale cache
   kubernetes scale deployment redis --replicas=3
   ```

## Performance Tuning

### Database Optimization

```sql
-- Analyze slow queries
SELECT query, calls, mean_time 
FROM pg_stat_statements 
ORDER BY mean_time DESC 
LIMIT 10;

-- Add missing indexes
CREATE INDEX CONCURRENTLY idx_users_email ON users(email);
CREATE INDEX CONCURRENTLY idx_watchlist_user_id ON watchlist(user_id);

-- Update table statistics
VACUUM ANALYZE users;
ANALYZE users;

-- Check index usage
SELECT schemaname, tablename, indexname, idx_scan 
FROM pg_stat_user_indexes 
ORDER BY idx_scan DESC;
```

### Cache Optimization

```bash
# Monitor cache effectiveness
INFO stats

# Eviction policy
CONFIG SET maxmemory-policy allkeys-lru

# Memory optimization
MEMORY DOCTOR
MEMORY STATS

# Key expiration
EXPIREAT key timestamp
TTL key
```

### Query Optimization

```sql
-- Explain query plan
EXPLAIN ANALYZE
SELECT u.*, COUNT(w.id) as watchlist_count
FROM users u
LEFT JOIN watchlist w ON u.id = w.user_id
WHERE u.created_at > NOW() - INTERVAL '30 days'
GROUP BY u.id;

-- Use connection pooling
-- PgBouncer configuration for connection pooling
```

## Capacity Planning

### Metrics to Track

```
- Peak concurrent users (should have capacity for 3x)
- Database connection pool utilization
- Cache memory usage
- Network bandwidth
- Storage growth rate
- API request rate
```

### Scaling Triggers

```
Scale up when:
- CPU usage > 70% for 5 minutes
- Memory usage > 80% for 5 minutes
- Request latency p95 > 200ms
- Error rate > 0.5%
- Database connections > 80% of pool

Scale down when:
- Metrics stable at < 40% for 30 minutes
- No pending requests in queue
```

### Forecasting

```bash
# Analyze growth over time
SELECT date, concurrent_users, api_requests
FROM metrics_daily
ORDER BY date DESC
LIMIT 30;

# Estimate capacity needed
# If growing 10% per month, calculate 6-month projection
```

## Maintenance Windows

### Database Maintenance

**Recommended**: Monthly, 2 AM UTC (low traffic)

```bash
# 1. Backup
aws rds create-db-snapshot --db-instance-identifier wildframe-prod --db-snapshot-identifier pre-maintenance

# 2. Maintenance
VACUUM FULL;
REINDEX INDEX CONCURRENTLY idx_users_email;

# 3. Verify
SELECT 1;
```

### Kubernetes Node Maintenance

```bash
# 1. Drain node
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data

# 2. Perform maintenance (reboot, updates, etc.)
# 3. Uncordon node
kubectl uncordon <node-name>

# 4. Monitor pod migration
kubectl get pods -o wide -n wildframe-prod
```

### Dependency Updates

```bash
# Monthly: Check for security updates
pip list --outdated
npm outdated

# Quarterly: Major version updates
pip install --upgrade package
npm upgrade package
```

---

**Keep this guide updated as operational procedures change. Document new incidents and resolutions.**

Last Updated: 2026-05-12
