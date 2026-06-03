# 🚀 Wildframe Quick Start Guide

## Complete Platform Setup & Execution

### 1️⃣ Prerequisites (One-Time Setup)

```bash
# Install required tools
sudo apt-get update
sudo apt-get install -y docker.io docker-compose python3 python3-pip git

# Verify installations
docker --version
docker-compose --version
python3 --version

# Add user to docker group (no sudo needed)
sudo usermod -aG docker $USER
# Log out and back in for this to take effect
```

### 2️⃣ Clone & Navigate to Project

```bash
cd /home/phoenix/Desktop/wildframe
git status  # Verify you're in the repo
```

### 3️⃣ Start Complete Platform (All Services)

```bash
# Start all services in background
docker-compose -f deployments/docker-compose.dev.yml up -d

# Wait for services to initialize (~60-90 seconds)
sleep 90

# Verify all services are healthy
docker-compose -f deployments/docker-compose.dev.yml ps

# Check specific service health
curl http://localhost:8001/health  # Auth Service
curl http://localhost:8002/health  # User Service
curl http://localhost:8003/health  # Content Service
curl http://localhost:8000/health  # API Gateway (once ready)
```

### 4️⃣ View Logs

```bash
# All services
docker-compose -f deployments/docker-compose.dev.yml logs -f

# Specific service
docker-compose -f deployments/docker-compose.dev.yml logs -f auth-service

# Last 100 lines, specific service
docker-compose -f deployments/docker-compose.dev.yml logs --tail=100 auth-service
```

### 5️⃣ Run Tests

```bash
# Run all auth service tests
cd services/auth-service
python3 -m pytest tests/ -v

# Run with coverage report
python3 -m pytest tests/ --cov=app --cov-report=html
# Open htmlcov/index.html in browser to view coverage

# Run specific test
python3 -m pytest tests/test_auth_service.py::TestUserRegistration::test_register_new_user -v
```

### 6️⃣ Access Services & Dashboards

| Service | URL | Purpose |
|---------|-----|---------|
| **API Gateway** | http://localhost:8000 | Main entry point |
| **Auth Service** | http://localhost:8001 | Authentication |
| **User Service** | http://localhost:8002 | User profiles |
| **Content Service** | http://localhost:8003 | Movies & shows |
| **Streaming Service** | http://localhost:8004 | Video playback |
| **Search Service** | http://localhost:8005 | Content search |
| **Admin Service** | http://localhost:8006 | Admin panel |
| **Recommendation Service** | http://localhost:8007 | Recommendations |
| **Billing Service** | http://localhost:8008 | Payments |
| **Analytics Service** | http://localhost:8009 | Analytics |
| **Notification Service** | http://localhost:8010 | Notifications |
| **Media Pipeline** | http://localhost:8011 | Media processing |
| **Prometheus** | http://localhost:9090 | Metrics |
| **Grafana** | http://localhost:3000 | Dashboards (admin/admin) |
| **Jaeger** | http://localhost:16686 | Tracing |
| **Loki** | http://localhost:3100 | Logs |
| **pgAdmin** | http://localhost:5050 | Database UI |
| **Redis Commander** | http://localhost:8081 | Redis UI |
| **PostgreSQL** | localhost:5432 | Database |
| **Redis** | localhost:6379 | Cache |
| **Elasticsearch** | http://localhost:9200 | Search engine |

---

## Common Tasks

### Test User Registration Flow

```bash
# 1. Register new user
curl -X POST http://localhost:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "testuser@example.com",
    "password": "SecurePass123!",
    "password_confirm": "SecurePass123!"
  }'

# 2. Login
curl -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "testuser@example.com",
    "password": "SecurePass123!"
  }'
# Save the access_token from response

# 3. Get user profile (replace TOKEN with actual token)
TOKEN="eyJ0eXAiOiJKV1QiLCJhbGc..."
curl http://localhost:8002/users/me \
  -H "Authorization: Bearer $TOKEN"
```

### Develop a Service

```bash
# Hot reload enabled in docker-compose
# Edit any service code and it automatically reloads

# Example: Edit auth service
vim services/auth-service/app/main.py
# Changes are automatically reloaded

# Or rebuild if needed
docker-compose -f deployments/docker-compose.dev.yml build auth-service
docker-compose -f deployments/docker-compose.dev.yml restart auth-service
```

### View Database

```bash
# Access PostgreSQL via pgAdmin
# URL: http://localhost:5050
# Email: admin@example.com (no password set by default)

# Or via command line
docker-compose -f deployments/docker-compose.dev.yml exec postgres psql -U wildframe -d wildframe_db

# Example queries
\dt                  # List tables
SELECT * FROM users; # Query users
\q                   # Exit
```

### Stop All Services

```bash
# Stop without removing volumes (preserves data)
docker-compose -f deployments/docker-compose.dev.yml stop

# Stop and remove everything (cleanup)
docker-compose -f deployments/docker-compose.dev.yml down

# Remove all volumes too (DESTRUCTIVE - clears all data)
docker-compose -f deployments/docker-compose.dev.yml down -v
```

### Restart Everything

```bash
# Full restart
docker-compose -f deployments/docker-compose.dev.yml down
docker-compose -f deployments/docker-compose.dev.yml up -d
```

---

## Development Workflow

### 1. Create Feature Branch
```bash
git checkout -b feature/new-endpoint
```

### 2. Edit Service Code
```bash
vim services/auth-service/app/api/routes/auth.py
# Changes auto-reload in docker container
```

### 3. Write Tests
```bash
vim services/auth-service/tests/test_auth_service.py
```

### 4. Run Tests
```bash
cd services/auth-service
python3 -m pytest tests/ -v
```

### 5. Commit & Push
```bash
git add .
git commit -m "feat: add new endpoint"
git push origin feature/new-endpoint
```

### 6. Create Pull Request
```
GitHub UI: Create PR, tests run automatically via CI/CD
```

---

## Performance Optimization

### Monitor Resource Usage
```bash
# CPU and memory usage
docker stats

# Detailed service metrics
curl http://localhost:9090/metrics | head -50
```

### Scale Services (Production)
```bash
# In production (Kubernetes), scale auth service to 3 replicas:
kubectl scale deployment auth-service --replicas=3

# Or with docker-compose (not recommended for production):
docker-compose -f deployments/docker-compose.dev.yml up -d --scale auth-service=3
```

---

## Troubleshooting

### Port 8000 Already in Use
```bash
# Find what's using port 8000
lsof -i :8000

# Kill the process
kill -9 <PID>

# Then restart
docker-compose -f deployments/docker-compose.dev.yml restart
```

### Services Won't Start
```bash
# Check logs
docker-compose -f deployments/docker-compose.dev.yml logs auth-service

# Rebuild without cache
docker-compose -f deployments/docker-compose.dev.yml build --no-cache

# Start fresh
docker-compose -f deployments/docker-compose.dev.yml down -v
docker-compose -f deployments/docker-compose.dev.yml up -d
```

### Database Connection Error
```bash
# Wait a bit longer for database to fully initialize
sleep 30

# Verify postgres is healthy
docker-compose -f deployments/docker-compose.dev.yml ps postgres

# Check postgres logs
docker-compose -f deployments/docker-compose.dev.yml logs postgres

# Reset database
docker-compose -f deployments/docker-compose.dev.yml down -v
docker-compose -f deployments/docker-compose.dev.yml up -d
```

### Out of Memory
```bash
# Reduce resource limits in docker-compose.dev.yml
# Or increase Docker's memory allocation in Docker Desktop preferences

# Check current memory usage
docker stats --no-stream
```

---

## Project Structure

```
wildframe/
├── services/                 # 12 microservices
│   ├── auth-service/        # Authentication (JWT, MFA)
│   ├── user-service/        # User profiles & sessions
│   ├── content-service/     # Movies & shows
│   ├── streaming-service/   # Video playback tracking
│   ├── search-service/      # Full-text search
│   ├── recommendation-service/  # ML recommendations
│   ├── billing-service/     # Subscriptions & payments
│   ├── analytics-service/   # Event tracking
│   ├── notification-service/ # Email & push
│   ├── admin-service/       # Moderation & config
│   ├── media-pipeline/      # Video encoding
│   └── api-gateway/         # Request routing
├── apps/web/                # Next.js frontend
├── deployments/             # Docker Compose configs
├── infrastructure/          # Kubernetes & Terraform
├── docs/                    # Documentation (created)
├── TEST_GUIDE.md           # Testing guide (created)
└── docker-compose.dev.yml  # Local dev environment
```

---

## Next Steps

1. **Verify Setup**: Run `docker-compose ps` and confirm all services are running
2. **Run Tests**: Execute `cd services/auth-service && python3 -m pytest tests/ -v`
3. **Test Endpoints**: Use curl commands above to test APIs
4. **View Dashboards**: Access Grafana (http://localhost:3000) for metrics
5. **Start Development**: Create feature branches and develop as needed

---

## Quick Reference Commands

```bash
# Start all services
docker-compose -f deployments/docker-compose.dev.yml up -d

# Stop all services
docker-compose -f deployments/docker-compose.dev.yml down

# View logs
docker-compose -f deployments/docker-compose.dev.yml logs -f

# Run tests
cd services/auth-service && python3 -m pytest tests/ -v

# Rebuild a service
docker-compose -f deployments/docker-compose.dev.yml build auth-service

# Access database
docker-compose -f deployments/docker-compose.dev.yml exec postgres psql -U wildframe

# Clean everything (WARNING: deletes data)
docker-compose -f deployments/docker-compose.dev.yml down -v
```

---

## Getting Help

- **Logs**: `docker-compose logs -f <service>`
- **Metrics**: Visit http://localhost:9090 (Prometheus)
- **Dashboards**: Visit http://localhost:3000 (Grafana)
- **Traces**: Visit http://localhost:16686 (Jaeger)
- **Documentation**: Check README.md and other .md files in repo root
