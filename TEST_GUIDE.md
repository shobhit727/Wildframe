# 🧪 Wildframe Testing Guide

## Quick Start - Running Tests

### Prerequisites
```bash
# Ensure you have Python 3.14+ and Docker installed
python3 --version
docker --version
docker-compose --version
```

### Option 1: Run All Services with Docker Compose (Recommended for Full Testing)

```bash
# Start all services, databases, and observability stack
cd /home/phoenix/Desktop/wildframe
docker-compose -f deployments/docker-compose.dev.yml up -d

# Wait for services to be healthy (60-90 seconds)
docker-compose -f deployments/docker-compose.dev.yml ps

# Verify all services are running
curl https://localhost:8000/health   # API Gateway
curl https://localhost:8001/health   # Auth Service
curl https://localhost:8002/health   # User Service
curl https://localhost:8003/health   # Content Service
```

### Option 2: Run Unit Tests Locally (Fast Feedback)

```bash
# Install system Python testing dependencies
python3 -m pip install pytest pytest-asyncio sqlalchemy pydantic fastapi

# Run tests for a specific service
cd services/auth-service
python3 -m pytest tests/ -v

# Run with coverage
python3 -m pytest tests/ --cov=app --cov-report=html

# Run only specific test class
python3 -m pytest tests/test_auth_service.py::TestUserRegistration -v

# Run only fast tests (skip integration tests)
python3 -m pytest tests/ -m "not integration" -v
```

### Option 3: Run Tests Against Docker Services

```bash
# Make sure docker-compose is running
docker-compose -f deployments/docker-compose.dev.yml up -d

# Install integration test dependencies
python3 -m pip install httpx pytest-asyncio

# Run integration tests
cd services/auth-service
python3 -m pytest tests/ -m "integration" -v --tb=short
```

---

## Testing by Service

### Auth Service Tests
```bash
cd services/auth-service

# All tests
python3 -m pytest tests/test_auth_service.py -v

# Unit tests only
python3 -m pytest tests/test_auth_service.py::TestUserRegistration -v
python3 -m pytest tests/test_auth_service.py::TestUserLogin -v
python3 -m pytest tests/test_auth_service.py::TestTokenRefresh -v
python3 -m pytest tests/test_auth_service.py::TestRateLimiting -v
python3 -m pytest tests/test_auth_service.py::TestPasswordReset -v
python3 -m pytest tests/test_auth_service.py::TestEmailVerification -v

# With coverage
python3 -m pytest tests/ --cov=app --cov-report=term-missing
```

### User Service Tests
```bash
cd services/user-service

# Profile management
python3 -m pytest tests/test_user_service.py::TestProfileManagement -v

# Device management
python3 -m pytest tests/test_user_service.py::TestDeviceManagement -v

# Session management
python3 -m pytest tests/test_user_service.py::TestSessionManagement -v

# Watch history
python3 -m pytest tests/test_user_service.py::TestWatchHistory -v
```

### Content Service Tests
```bash
cd services/content-service

# Genre management
python3 -m pytest tests/test_content_service.py::TestGenreManagement -v

# Movie management
python3 -m pytest tests/test_content_service.py::TestMovieManagement -v

# Show management
python3 -m pytest tests/test_content_service.py::TestShowManagement -v

# Search functionality
python3 -m pytest tests/test_content_service.py::TestContentSearch -v
```

### Admin Service Tests
```bash
cd services/admin-service

# User moderation
python3 -m pytest tests/test_admin_service.py::TestUserModeration -v

# Content moderation
python3 -m pytest tests/test_admin_service.py::TestContentModeration -v

# System alerts
python3 -m pytest tests/test_admin_service.py::TestSystemAlerts -v

# System configuration
python3 -m pytest tests/test_admin_service.py::TestSystemConfig -v
```

---

## API Endpoint Testing

### Test Auth Service Endpoints
```bash
# Health check
curl https://localhost:8001/health

# Register user
curl -X POST https://localhost:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "SecurePass123!",
    "password_confirm": "SecurePass123!"
  }'

# Login
curl -X POST https://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "SecurePass123!"
  }'

# Refresh token (requires TOKEN from login response)
curl -X POST https://localhost:8001/api/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{
    "refresh_token": "your_refresh_token_here"
  }'
```

### Test User Service Endpoints
```bash
# Get user profile (requires Bearer token from auth)
TOKEN="your_access_token_here"

curl https://localhost:8002/users/me \
  -H "Authorization: Bearer $TOKEN"

# Update profile
curl -X PUT https://localhost:8002/users/me \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "first_name": "John",
    "last_name": "Doe",
    "language": "en"
  }'

# List devices
curl https://localhost:8002/users/devices \
  -H "Authorization: Bearer $TOKEN"

# Register device
curl -X POST https://localhost:8002/users/devices \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "device123",
    "device_type": "web",
    "device_name": "Chrome on MacBook"
  }'
```

### Test Content Service Endpoints
```bash
# List genres
curl https://localhost:8003/genres

# Search movies
curl "https://localhost:8003/movies/search?query=inception"

# Get movie details
curl https://localhost:8003/movies/{movie_id}

# List trending movies
curl "https://localhost:8003/movies/trending?limit=20"

# List recent movies
curl "https://localhost:8003/movies/recent?limit=20"
```

### Test Admin Service Endpoints
```bash
# Health check
curl https://localhost:8006/health

# Get system alerts
curl https://localhost:8006/admin/alerts

# Get system stats
curl https://localhost:8006/admin/stats

# List moderated users
curl "https://localhost:8006/admin/users/moderated?status=suspended"

# List flagged content
curl "https://localhost:8006/admin/content/flagged"
```

---

## Monitoring and Observability

### Access Monitoring Dashboards

```bash
# Prometheus - Metrics
# URL: https://localhost:9090

# Grafana - Dashboards
# URL: https://localhost:3000
# Default credentials: admin / admin

# Jaeger - Distributed Tracing
# URL: https://localhost:16686

# Loki - Log Aggregation
# URL: https://localhost:3100

# pgAdmin - PostgreSQL Management
# URL: https://localhost:5050
# Email: admin@example.com
# Password: admin

# Redis Commander - Redis Management
# URL: http://localhost:8081
```

---

## Load Testing

### Install Load Testing Tools
```bash
python3 -m pip install locust
```

### Create Locustfile for Auth Service
```python
# services/auth-service/locustfile.py
from locust import HttpUser, task, between
import json

class AuthUser(HttpUser):
    wait_time = between(1, 3)
    
    @task(3)
    def register(self):
        self.client.post("/api/v1/auth/register", json={
            "email": f"user{self.client.base_url}@test.com",
            "password": "Test123456!",
            "password_confirm": "Test123456!"
        })
    
    @task(1)
    def login(self):
        self.client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "Test123456!"
        })
```

### Run Load Test
```bash
cd services/auth-service
locust -f locustfile.py -u 100 -r 10 -t 5m --headless -H https://localhost:8001
```

---

## Database Testing

### Backup Database
```bash
docker-compose -f deployments/docker-compose.dev.yml exec postgres \
  pg_dump -U wildframe wildframe_db > backup.sql
```

### Restore Database
```bash
docker-compose -f deployments/docker-compose.dev.yml exec -T postgres \
  psql -U wildframe wildframe_db < backup.sql
```

### View Database Logs
```bash
docker-compose -f deployments/docker-compose.dev.yml logs postgres
```

---

## Troubleshooting Tests

### Tests Timeout
```bash
# Increase timeout for slow systems
python3 -m pytest tests/ --timeout=300 -v
```

### Database Connection Issues
```bash
# Verify database is running
docker-compose -f deployments/docker-compose.dev.yml ps postgres

# Reset database
docker-compose -f deployments/docker-compose.dev.yml exec postgres \
  dropdb -U wildframe --if-exists wildframe_db

docker-compose -f deployments/docker-compose.dev.yml exec postgres \
  createdb -U wildframe wildframe_db
```

### Service Not Starting
```bash
# Check service logs
docker-compose -f deployments/docker-compose.dev.yml logs auth-service

# Rebuild service
docker-compose -f deployments/docker-compose.dev.yml build --no-cache auth-service

# Restart service
docker-compose -f deployments/docker-compose.dev.yml restart auth-service
```

### Port Already in Use
```bash
# Find process using port
lsof -i :8001  # For auth-service

# Kill process
kill -9 <PID>
```

---

## CI/CD Pipeline Tests

### Run CI/CD Locally (Using Act)
```bash
# Install act
brew install act

# Run GitHub Actions locally
act -j test

# Run specific workflow
act -j test -l
```

### View Workflow Logs
```bash
# GitHub Actions logs available at:
# https://github.com/yourusername/wildframe/actions
```

---

## Test Coverage Goals

| Service | Unit Tests | Integration | E2E | Target Coverage |
|---------|-----------|-------------|-----|-----------------|
| Auth | 15+ | 8+ | 5+ | 85%+ |
| User | 12+ | 6+ | 4+ | 80%+ |
| Content | 10+ | 5+ | 3+ | 75%+ |
| Admin | 14+ | 7+ | 4+ | 80%+ |
| Other Services | 8+ | 4+ | 2+ | 70%+ |

---

## Performance Benchmarks

Target SLAs:
- **Auth Register**: <200ms, p99<500ms
- **Auth Login**: <150ms, p99<400ms
- **User Profile Get**: <100ms, p99<300ms
- **Content Search**: <500ms, p99<1000ms
- **API Gateway Latency**: <50ms overhead

Track with:
```bash
# View performance metrics in Grafana
# Dashboard: Service Performance → Latency
```
