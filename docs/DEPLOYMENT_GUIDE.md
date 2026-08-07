# 🚀 Deployment Guide

**Version**: 1.0.0  
**Last Updated**: May 28, 2026  
**Stability**: Production-Ready

## Overview

This guide covers deploying Wildframe to production environments. It includes infrastructure setup, database migrations, service deployment, and post-deployment verification.

**Time to read**: 30 minutes  
**Prerequisites**: Docker, Kubernetes 1.28+, Terraform, kubectl, AWS CLI credentials

## CI/CD Deployment Note (August 2026)

The consolidated GitHub Actions workflow (`.github/workflows/ci-cd.yml`) runs
`Deploy Staging` and `Deploy Production` jobs on pushes to `main`. These steps
configure AWS credentials via `aws-actions/configure-aws-credentials@v4` and
run `aws eks update-kubeconfig` against the `wildframe-staging` /
`wildframe-production` clusters. They **currently fail** until the following
exist:

- Repo secrets `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (IAM user with
  EKS `describe-cluster` + `eks:ListClusters` + `eks:AccessKubernetesApi` or
  similar)
- `wildframe-staging` and `wildframe-production` EKS clusters reachable on
  us-east-1
- A `deployment.yaml` / image pull secret in each cluster for the GHCR images

Until then the pipeline is green on every check except the two deploy steps.

## Table of Contents

1. [Pre-Deployment Checklist](#pre-deployment-checklist)
2. [Infrastructure Setup](#infrastructure-setup)
3. [Database Setup](#database-setup)
4. [Service Deployment](#service-deployment)
5. [Verification](#verification)
6. [Troubleshooting](#troubleshooting)

---

## Pre-Deployment Checklist

Before deploying to production:

- [ ] All tests pass locally and in CI/CD
- [ ] Code review completed and approved
- [ ] Security scan completed (no critical vulnerabilities)
- [ ] Performance testing completed
- [ ] Database migrations tested on staging
- [ ] Infrastructure capacity verified
- [ ] Monitoring and alerting configured
- [ ] Backup strategy in place
- [ ] Disaster recovery tested
- [ ] Team notified of deployment window
- [ ] Rollback plan documented

---

## Infrastructure Setup

### Using Terraform

#### 1. Initialize Terraform

```bash
cd infrastructure/terraform

# Initialize Terraform (downloads providers)
terraform init

# Verify configuration
terraform validate
```

#### 2. Plan Deployment

```bash
# Review what will be created
terraform plan -out=tfplan

# Example output:
# + aws_db_instance.postgres
# + aws_elasticache_cluster.redis
# + aws_msk_cluster.kafka
# + aws_elasticsearch_domain.elasticsearch
# + aws_eks_cluster.wildframe
# ...
```

#### 3. Apply Configuration

```bash
# Deploy infrastructure
terraform apply tfplan

# Wait for completion (typically 15-20 minutes)
# Output will show resource endpoints

# Example outputs:
# database_endpoint = "wildframe-db.c9akciq32.us-east-1.rds.amazonaws.com"
# redis_endpoint = "wildframe-redis.12345.cache.amazonaws.com"
# eks_cluster_endpoint = "https://abc123.eks.amazonaws.com"
```

### Terraform Configuration Summary

```hcl
# infrastructure/terraform/main.tf

resource "aws_db_instance" "postgres" {
  identifier           = "wildframe-db"
  engine               = "postgres"
  engine_version       = "15"
  instance_class       = "db.m5.large"
  allocated_storage    = 100
  multi_az             = true  # High availability
  backup_retention     = 30    # 30-day retention
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id    = "wildframe-redis"
  engine        = "redis"
  node_type     = "cache.r6g.xlarge"
  num_cache_nodes = 3  # Cluster mode with replicas
}

resource "aws_msk_cluster" "kafka" {
  cluster_name = "wildframe-kafka"
  kafka_version = "3.4"
  number_of_broker_nodes = 3
}

resource "aws_eks_cluster" "wildframe" {
  name = "wildframe"
  version = "1.28"
  
  vpc_config {
    subnet_ids = aws_subnet.private[*].id
  }
}
```

---

## Database Setup

### 1. Create Databases

Connect to the PostgreSQL instance and run:

```bash
# SSH to bastion host or use AWS RDS proxy
psql -h wildframe-db.c9akciq32.us-east-1.rds.amazonaws.com \
     -U postgres \
     -f infrastructure/database/init-databases.sql
```

This creates:

```sql
-- Creates 7 service databases
CREATE DATABASE auth_db;
CREATE DATABASE user_db;
CREATE DATABASE content_db;
CREATE DATABASE admin_db;
CREATE DATABASE streaming_db;
CREATE DATABASE billing_db;
CREATE DATABASE analytics_db;

-- Creates service users with limited privileges
CREATE USER auth_user WITH PASSWORD '...';
GRANT ALL PRIVILEGES ON auth_db TO auth_user;

-- ... (repeat for other services)
```

### 2. Run Migrations

```bash
# For each service
cd services/auth-service

# Set database URL
export DATABASE_URL="postgresql://auth_user:password@wildframe-db.us-east-1.rds.amazonaws.com:5432/auth_db"

# Run migrations
alembic upgrade head
```

### 3. Verify Databases

```bash
psql -h wildframe-db.c9akciq32.us-east-1.rds.amazonaws.com \
     -U postgres \
     -c "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;"
```

Expected output:

```
     datname     
-----------------
 admin_db
 analytics_db
 auth_db
 billing_db
 content_db
 streaming_db
 user_db
```

---

## Service Deployment

### Using Kubernetes

#### 1. Build Docker Images

```bash
# Build all service images
docker build -t wildframe/auth-service:latest services/auth-service
docker build -t wildframe/user-service:latest services/user-service
docker build -t wildframe/content-service:latest services/content-service
# ... (repeat for other services)

# Tag for ECR
docker tag wildframe/auth-service:latest 123456789.dkr.ecr.us-east-1.amazonaws.com/wildframe/auth-service:latest

# Push to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 123456789.dkr.ecr.us-east-1.amazonaws.com

docker push 123456789.dkr.ecr.us-east-1.amazonaws.com/wildframe/auth-service:latest
```

#### 2. Deploy Services

```bash
# Authenticate with Kubernetes cluster
aws eks update-kubeconfig --name wildframe --region us-east-1

# Deploy namespace and services
kubectl apply -f infrastructure/kubernetes/

# Verify deployment
kubectl get pods -n wildframe
kubectl get svc -n wildframe
```

Example deployment manifest:

```yaml
# infrastructure/kubernetes/auth-service.yaml

apiVersion: apps/v1
kind: Deployment
metadata:
  name: auth-service
  namespace: wildframe
spec:
  replicas: 3
  selector:
    matchLabels:
      app: auth-service
  template:
    metadata:
      labels:
        app: auth-service
    spec:
      containers:
      - name: auth-service
        image: 123456789.dkr.ecr.us-east-1.amazonaws.com/wildframe/auth-service:latest
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: auth-secrets
              key: database_url
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 5
        resources:
          requests:
            cpu: "500m"
            memory: "512Mi"
          limits:
            cpu: "1000m"
            memory: "1024Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: auth-service
  namespace: wildframe
spec:
  selector:
    app: auth-service
  ports:
  - port: 80
    targetPort: 8000
  type: ClusterIP
---
apiVersion: autoscaling.k8s.io/v2
kind: HorizontalPodAutoscaler
metadata:
  name: auth-service-hpa
  namespace: wildframe
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: auth-service
  minReplicas: 3
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
```

#### 3. Configure Secrets

```bash
# Create secrets for database credentials
kubectl create secret generic auth-secrets \
  --from-literal=database_url="postgresql://auth_user:password@wildframe-db.us-east-1.rds.amazonaws.com:5432/auth_db" \
  -n wildframe

# Create secrets for JWT key
kubectl create secret generic jwt-secrets \
  --from-literal=jwt_secret_key="$(openssl rand -hex 32)" \
  -n wildframe

# Verify secrets created
kubectl get secrets -n wildframe
```

---

## Verification

### 1. Service Health Checks

```bash
# Check service readiness
kubectl get pods -n wildframe

# Example healthy output:
# NAME                              READY   STATUS    RESTARTS
# auth-service-5f4d8c9b7-abc12      1/1     Running   0
# user-service-5f4d8c9b7-def45      1/1     Running   0
# content-service-5f4d8c9b7-ghi78   1/1     Running   0
```

### 2. API Connectivity Test

```bash
# Get API Gateway URL
kubectl get service api-gateway -n wildframe

# Test endpoint
curl -X GET https://api.wildframe.com/health \
  -H "Content-Type: application/json"

# Expected response:
# {"status": "healthy", "timestamp": "2026-05-28T15:00:00Z"}
```

### 3. Database Connectivity

```bash
# Check database connections
kubectl exec -it auth-service-pod -n wildframe -- \
  psql -h wildframe-db.us-east-1.rds.amazonaws.com \
       -U auth_user \
       -d auth_db \
       -c "SELECT version();"
```

### 4. Cache Connectivity

```bash
# Test Redis connection
kubectl exec -it auth-service-pod -n wildframe -- \
  redis-cli -h wildframe-redis.us-east-1.cache.amazonaws.com ping

# Expected response: PONG
```

### 5. End-to-End Test

```bash
# Register a test user
curl -X POST https://api.wildframe.com/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "TestPass123!",
    "first_name": "Test",
    "last_name": "User"
  }'

# Expected response: 201 Created with user data
```

---

## Post-Deployment

### 1. Enable Monitoring

```bash
# Deploy Prometheus
kubectl apply -f infrastructure/kubernetes/prometheus.yaml

# Deploy Grafana
kubectl apply -f infrastructure/kubernetes/grafana.yaml

# Access Grafana dashboard
kubectl port-forward svc/grafana 3000:3000 -n wildframe
# Open http://localhost:3000
```

### 2. Configure Alerting

```bash
# Deploy AlertManager
kubectl apply -f infrastructure/kubernetes/alertmanager.yaml

# Configure notification channels (email, Slack, PagerDuty)
# in Alertmanager configuration
```

### 3. Setup Logging

```bash
# Deploy Loki for log aggregation
kubectl apply -f infrastructure/kubernetes/loki.yaml

# Deploy Promtail for log collection
kubectl apply -f infrastructure/kubernetes/promtail.yaml
```

### 4. Enable Tracing

```bash
# Deploy Jaeger for distributed tracing
kubectl apply -f infrastructure/kubernetes/jaeger.yaml

# Services will automatically send traces to Jaeger
```

---

## Scaling

### Horizontal Scaling

```bash
# Increase number of replicas
kubectl scale deployment auth-service --replicas=5 -n wildframe

# Or edit deployment
kubectl edit deployment auth-service -n wildframe
# Change spec.replicas to desired number
```

### Vertical Scaling

```bash
# Increase resource allocation
kubectl set resources deployment auth-service \
  --limits=cpu=2000m,memory=2048Mi \
  --requests=cpu=1000m,memory=1024Mi \
  -n wildframe
```

---

## Troubleshooting

### Service Not Starting

```bash
# Check pod events
kubectl describe pod auth-service-5f4d8c9b7-abc12 -n wildframe

# View logs
kubectl logs auth-service-5f4d8c9b7-abc12 -n wildframe

# Check resource constraints
kubectl top pods -n wildframe
```

### Database Connection Issues

```bash
# Verify database is reachable
kubectl exec -it auth-service-pod -n wildframe -- \
  nc -zv wildframe-db.us-east-1.rds.amazonaws.com 5432

# Test with psql
kubectl exec -it auth-service-pod -n wildframe -- \
  psql -h wildframe-db.us-east-1.rds.amazonaws.com -U auth_user -d auth_db -c "SELECT 1;"
```

### High Memory Usage

```bash
# Check memory metrics
kubectl top pods -n wildframe --sort-by=memory

# Increase memory limit
kubectl set resources deployment auth-service \
  --limits=memory=2048Mi \
  -n wildframe

# Restart pods to apply changes
kubectl rollout restart deployment auth-service -n wildframe
```

---

## Rollback

If issues occur after deployment:

```bash
# Check rollout history
kubectl rollout history deployment auth-service -n wildframe

# Rollback to previous version
kubectl rollout undo deployment auth-service -n wildframe

# Verify rollback
kubectl get pods -n wildframe
```

---

## See Also

- [Operations Guide](OPERATIONS.md)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [Terraform Documentation](https://www.terraform.io/docs/)
