# Operations & Deployment Guide

Complete guide for deploying and operating Wildframe in production environments.

## Table of Contents
1. [Pre-Deployment Checklist](#pre-deployment-checklist)
2. [Database Migrations](#database-migrations)
3. [Backend Deployment](#backend-deployment)
4. [Frontend Deployment](#frontend-deployment)
5. [Infrastructure Setup](#infrastructure-setup)
6. [Monitoring & Alerting](#monitoring--alerting)
7. [Daily Operations](#daily-operations)
8. [Incident Response](#incident-response)
9. [Rollback Procedures](#rollback-procedures)
10. [Troubleshooting](#troubleshooting)

---

## Pre-Deployment Checklist

### Code Quality

```bash
# Run all tests
cd netflix_backend
pytest tests/ --cov

# Type checking
mypy app/

# Linting
pylint app/
black --check app/
isort --check app/

# Frontend
cd ../apps/web
npm run build
npm run type-check
npm run lint
```

### Security Checks

```bash
# Check for vulnerabilities
safety check  # Python
npm audit     # JavaScript

# SAST scanning
bandit -r app/  # Python code security
```

### Documentation
- [ ] API documentation updated
- [ ] Changelog updated
- [ ] Architecture changes documented
- [ ] Runbooks created/updated
- [ ] Database changes documented

---

## Database Migrations

### PostgreSQL Migrations

```bash
# Create migration
cd netflix_backend
alembic revision --autogenerate -m "description"

# Review migration
cat alembic/versions/xxxxx_description.py

# Apply migration (local)
alembic upgrade head

# Apply migration (production)
kubectl exec -it deployment/api-gateway -n wildframe-prod \
  -- alembic upgrade head

# Rollback if needed
alembic downgrade -1
```

### Backup Strategy

```bash
# Full backup before major deployment
pg_dump -U postgres postgres_db > backup_$(date +%Y%m%d_%H%M%S).sql

# Automated backups
# AWS RDS automated backups (7-35 days retention)
```

---

## Backend Deployment

### Docker Build

```bash
# Build image
docker build \
  -t wildframe-backend:v1.0.0 \
  -f netflix_backend/Dockerfile \
  .

# Tag for registry
docker tag wildframe-backend:v1.0.0 \
  123456789.dkr.ecr.us-east-1.amazonaws.com/wildframe-backend:v1.0.0

# Push to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin 123456789.dkr.ecr.us-east-1.amazonaws.com

docker push \
  123456789.dkr.ecr.us-east-1.amazonaws.com/wildframe-backend:v1.0.0
```

### Kubernetes Deployment

```bash
# Update image in deployment
kubectl set image deployment/api-gateway \
  api-gateway=123456789.dkr.ecr.us-east-1.amazonaws.com/wildframe-backend:v1.0.0 \
  -n wildframe-prod

# Check rollout status
kubectl rollout status deployment/api-gateway -n wildframe-prod

# Monitor pods
kubectl get pods -n wildframe-prod -w

# View logs
kubectl logs -f deployment/api-gateway -n wildframe-prod

# Describe deployment
kubectl describe deployment api-gateway -n wildframe-prod
```

### Helm Deployment (Alternative)

```bash
# Create/update Helm values
cat > values-prod.yaml <<EOF
image:
  repository: 123456789.dkr.ecr.us-east-1.amazonaws.com/wildframe-backend
  tag: v1.0.0
replicas: 3
environment: production
database:
  url: postgresql://user:pass@db.xxx.rds.amazonaws.com:5432/db
EOF

# Deploy with Helm
helm upgrade --install wildframe \
  ./helm/wildframe \
  -f values-prod.yaml \
  -n wildframe-prod

# Check deployment
helm status wildframe -n wildframe-prod

# Rollback if needed
helm rollback wildframe 1 -n wildframe-prod
```

---

## Frontend Deployment

### Build Optimization

```bash
# Create optimized build
cd apps/web
npm run build

# Verify build size
du -sh .next/

# Analyze bundle
npm run build -- --analyze
```

### Vercel Deployment

```bash
# Set up Vercel project
vercel link

# Add environment variables
vercel env add NEXT_PUBLIC_API_URL=https://api.wildframe.com

# Deploy
vercel deploy --prod

# Check deployment
vercel env list
vercel logs
```

### S3 + CloudFront Deployment

```bash
# Build
npm run build
npm run export  # If using static export

# Deploy to S3
aws s3 sync out/ s3://wildframe-web \
  --delete \
  --cache-control "public, max-age=3600"

# Invalidate CloudFront
aws cloudfront create-invalidation \
  --distribution-id E123ABC \
  --paths "/*"
```

---

## Infrastructure Setup

### AWS Resources

```bash
# Initialize Terraform
cd infrastructure/terraform
terraform init

# Plan changes
terraform plan -var-file=prod.tfvars

# Apply changes
terraform apply -var-file=prod.tfvars

# Get outputs
terraform output
```

### Kubernetes Cluster

```bash
# Create cluster
eksctl create cluster \
  --name wildframe-prod \
  --version 1.28 \
  --region us-east-1 \
  --nodes 3 \
  --node-type t3.xlarge

# Configure kubectl
aws eks update-kubeconfig \
  --region us-east-1 \
  --name wildframe-prod
```

### Database Setup

```bash
# Create RDS instance
aws rds create-db-instance \
  --db-instance-identifier wildframe-prod \
  --db-instance-class db.t3.medium \
  --engine postgres \
  --allocated-storage 100

# Create read replica
aws rds create-db-instance-read-replica \
  --db-instance-identifier wildframe-prod-replica \
  --source-db-instance-identifier wildframe-prod

# Setup backup
aws rds modify-db-instance \
  --db-instance-identifier wildframe-prod \
  --backup-retention-period 30 \
  --preferred-backup-window "03:00-04:00"
```

### Cache Setup

```bash
# Create ElastiCache cluster
aws elasticache create-cache-cluster \
  --cache-cluster-id wildframe-cache \
  --cache-node-type cache.t3.medium \
  --engine redis

# Enable multi-AZ
aws elasticache create-replication-group \
  --replication-group-description "Wildframe Cache" \
  --replication-group-id wildframe-cache \
  --engine redis \
  --num-cache-clusters 2
```

---

## Monitoring & Alerting

### Prometheus

```bash
# Port forward to local
kubectl port-forward svc/prometheus 9090:9090 -n monitoring

# Access at http://localhost:9090

# Query examples
# P95 latency
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))

# Error rate
rate(http_requests_total{status=~"5.."}[5m])
```

### Grafana

```bash
# Port forward to local
kubectl port-forward svc/grafana 3000:3000 -n monitoring

# Access at http://localhost:3000
# Default: admin/admin
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
```

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
```

---

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
```

### Common Tasks

#### Horizontal Scaling

```bash
# Scale auth service to 5 replicas
kubectl scale deployment auth-service --replicas=5 -n wildframe-prod

# Auto-scaling based on CPU
kubectl patch hpa auth-service -p '{"spec":{"minReplicas":3,"maxReplicas":20}}' -n wildframe-prod
```

#### Database Operations

```bash
# Create snapshot
aws rds create-db-snapshot \
  --db-instance-identifier wildframe-prod \
  --db-snapshot-identifier wildframe-prod-$(date +%Y%m%d-%H%M%S)

# Restore from snapshot
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier wildframe-restore \
  --db-snapshot-identifier wildframe-prod-20240101-120000
```

#### Redis Management

```bash
# Connect to Redis
kubectl exec -it svc/redis -- redis-cli

# Monitor Redis
info stats
info memory

# Clear cache
FLUSHDB

# Check memory usage
MEMORY STATS
```

---

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

### P2 Incident: High Error Rate

1. **Identify root cause**
   ```bash
   # Check error patterns
   kubectl logs deployment/api-gateway -n wildframe-prod | grep ERROR | head -20
   
   # Check Prometheus
   rate(http_requests_total{status=~"5.."}[5m])
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
   # Add missing index
   ALTER TABLE users ADD INDEX idx_email (email);
   
   # Update statistics
   ANALYZE TABLE users;
   ```

---

## Rollback Procedures

### Kubernetes Rollback

```bash
# Check rollout history
kubectl rollout history deployment/api-gateway -n wildframe-prod

# Rollback to previous version
kubectl rollout undo deployment/api-gateway -n wildframe-prod

# Rollback to specific revision
kubectl rollout undo deployment/api-gateway \
  --to-revision=3 \
  -n wildframe-prod

# Monitor rollback
kubectl rollout status deployment/api-gateway -n wildframe-prod
```

### Helm Rollback

```bash
# Check release history
helm history wildframe -n wildframe-prod

# Rollback
helm rollback wildframe 1 -n wildframe-prod

# Rollback with cleanup
helm rollback wildframe 1 --cleanup-on-fail -n wildframe-prod
```

### Database Rollback

```bash
# Restore from backup
psql -U postgres -d postgres < backup.sql

# Or using RDS Backtrack
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier wildframe-restore \
  --db-snapshot-identifier wildframe-snapshot
```

---

## Troubleshooting

### Common Issues

#### Pods Not Starting

```bash
# Check pod status
kubectl describe pod <pod-name> -n wildframe-prod

# Check logs
kubectl logs <pod-name> -n wildframe-prod

# Check events
kubectl get events -n wildframe-prod --sort-by='.lastTimestamp'
```

#### High Memory Usage

```bash
# Check resource usage
kubectl top pods -n wildframe-prod

# Scale up if needed
kubectl scale deployment api-gateway --replicas=5 -n wildframe-prod

# Check memory limits
kubectl describe node
```

#### Database Connection Issues

```bash
# Check connectivity
telnet db.xxx.rds.amazonaws.com 5432

# Check security groups
aws ec2 describe-security-groups --filter Name=group-name,Values=wildframe-db

# Check RDS status
aws rds describe-db-instances --db-instance-identifier wildframe-prod
```

#### API Rate Limiting

```bash
# Check rate limit status
curl -I https://api.wildframe.com/health

# Adjust rate limits
kubectl set env deployment/api-gateway \
  RATE_LIMIT_REQUESTS=100 \
  -n wildframe-prod
```

### Health Checks

```bash
# Application health
curl https://api.wildframe.com/health

# Database
kubectl exec -it <pod-name> -n wildframe-prod -- psql -c "SELECT 1"

# Cache
kubectl exec -it <pod-name> -n wildframe-prod -- redis-cli ping

# External API
curl -I https://api.wildframe.com/api/content
```

---

Last Updated: May 26, 2026
