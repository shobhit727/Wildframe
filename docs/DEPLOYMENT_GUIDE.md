# Deployment Guide - Wildframe Platform

Complete guide for deploying Wildframe from development to production.

## Table of Contents

1. [Pre-Deployment Checklist](#pre-deployment-checklist)
2. [Database Migrations](#database-migrations)
3. [Backend Deployment](#backend-deployment)
4. [Frontend Deployment](#frontend-deployment)
5. [Infrastructure Setup](#infrastructure-setup)
6. [Monitoring & Logging](#monitoring--logging)
7. [Rollback Procedures](#rollback-procedures)
8. [Troubleshooting](#troubleshooting)

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

# Dependency scanning
pip install pip-audit
pip-audit
```

### Documentation

- [ ] API documentation updated
- [ ] Changelog updated
- [ ] Architecture changes documented
- [ ] Runbooks created/updated
- [ ] Database changes documented

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
AWS RDS automated backups (7-35 days retention)
```

## Backend Deployment

### Local Development

```bash
# Activate environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export ENVIRONMENT=development
export DEBUG=False
export DATABASE_URL=postgresql://...

# Run migrations
python manage.py migrate

# Start server
python manage.py runserver
```

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

# Monitor
aws s3 ls s3://wildframe-web/
aws cloudfront get-distribution-config --id E123ABC
```

### Docker Frontend

```bash
# Build image
docker build \
  -t wildframe-web:v1.0.0 \
  -f apps/web/Dockerfile \
  .

# Run container
docker run -p 3000:3000 \
  -e NEXT_PUBLIC_API_URL=https://api.wildframe.com \
  wildframe-web:v1.0.0
```

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

# Install necessary operators
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prometheus prometheus-community/kube-prometheus-stack
```

### Database Setup

```bash
# Create RDS instance
aws rds create-db-instance \
  --db-instance-identifier wildframe-prod \
  --db-instance-class db.t3.medium \
  --engine postgres \
  --allocated-storage 100 \
  --storage-type gp3

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
  --engine redis \
  --num-cache-nodes 1

# Enable multi-AZ
aws elasticache create-replication-group \
  --replication-group-description "Wildframe Cache" \
  --replication-group-id wildframe-cache \
  --engine redis \
  --cache-node-type cache.t3.medium \
  --num-cache-clusters 2
```

## Monitoring & Logging

### Prometheus

```bash
# Port forward to local
kubectl port-forward svc/prometheus 9090:9090 -n monitoring

# Access at http://localhost:9090

# Add recording rules
kubectl apply -f monitoring/recording-rules.yaml
```

### Grafana

```bash
# Port forward to local
kubectl port-forward svc/grafana 3000:3000 -n monitoring

# Access at http://localhost:3000
# Default: admin/admin

# Import dashboards
curl https://grafana.com/api/dashboards/12114 | \
  jq .json.dashboard | kubectl create configmap grafana-dashboard --from-file=/dev/stdin
```

### Loki

```bash
# View logs
kubectl logs -f deployment/api-gateway -n wildframe-prod

# With label filtering
kubectl logs -f deployment/api-gateway -n wildframe-prod --selector app=api-gateway
```

### Jaeger Tracing

```bash
# Port forward
kubectl port-forward svc/jaeger 16686:16686 -n monitoring

# Access at http://localhost:16686

# Search traces by service
# Filter by operation, tags, or duration
```

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

# Verify data integrity
SELECT COUNT(*) FROM users;
```

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

### Performance Debugging

```bash
# Profile application
kubectl exec -it <pod-name> -n wildframe-prod -- python -m cProfile -s cumtime

# Check slow queries
kubectl logs <pod-name> -n wildframe-prod | grep "duration:"

# Database query analysis
EXPLAIN ANALYZE SELECT * FROM users WHERE email = 'user@example.com';
```

## Deployment Scripts

### Automated Deployment

```bash
#!/bin/bash
set -e

VERSION=$1
ENVIRONMENT=${2:-staging}

# Build
docker build -t wildframe:$VERSION .

# Push
docker push 123456789.dkr.ecr.us-east-1.amazonaws.com/wildframe:$VERSION

# Deploy
kubectl set image deployment/api-gateway \
  api-gateway=123456789.dkr.ecr.us-east-1.amazonaws.com/wildframe:$VERSION \
  -n wildframe-$ENVIRONMENT

# Wait for rollout
kubectl rollout status deployment/api-gateway -n wildframe-$ENVIRONMENT

echo "Deployment of v$VERSION to $ENVIRONMENT completed"
```

---

Last Updated: 2026-05-12
