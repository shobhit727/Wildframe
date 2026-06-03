# Development Guide

Complete guide to developing for Wildframe: setup, conventions, implementation roadmap, and technical glossary.

## Table of Contents
1. [Development Setup](#development-setup)
2. [Development Workflow](#development-workflow)
3. [Code Conventions](#code-conventions)
4. [Testing](#testing)
5. [Implementation Roadmap](#implementation-roadmap)
6. [Technical Glossary](#technical-glossary)

---

## Development Setup

### Prerequisites
- Python 3.11+
- Node.js 18+
- PostgreSQL 14+
- Redis 7+
- Docker & Docker Compose

### Environment Setup

```bash
# Clone repository
git clone https://github.com/wildframe/platform.git
cd platform

# Install backend dependencies
pip install -r requirements.txt
pre-commit install

# Install frontend dependencies
npm install

# Start development environment
docker-compose -f deployments/docker-compose.dev.yml up -d

# Run migrations
docker-compose exec auth-service alembic upgrade head

# Start frontend
npm run dev --workspace=apps/web

# Start backend services (in separate terminals)
python -m uvicorn services/auth-service/app/main:app --reload --port 8001
```

### Code of Conduct

We are committed to providing a welcoming and inclusive environment for all contributors.

---

## Development Workflow

### 1. Create Feature Branch
```bash
git checkout -b feature/description
```

### 2. Make Changes
Follow the code conventions below.

### 3. Test Your Changes
```bash
# Run tests
pytest services/auth-service/tests -v

# Run linting
black services/auth-service/app
isort services/auth-service/app
pylint services/auth-service/app
mypy services/auth-service/app

# Frontend tests
npm run test --workspace=apps/web
npm run lint --workspace=apps/web
```

### 4. Commit Your Changes
```bash
git add .
git commit -m "feat(auth-service): add user registration endpoint"
```

### 5. Push and Create Pull Request
```bash
git push origin feature/description
```

---

## Code Conventions

### Python (Backend)

#### Style
- Use Black for formatting (line length: 100)
- Use isort for imports
- Use type hints for all functions

#### Example
```python
"""Module docstring."""
from typing import Optional
from uuid import UUID
import logging

logger = logging.getLogger(__name__)


async def get_user(user_id: UUID) -> Optional[User]:
    """Get user by ID.
    
    Args:
        user_id: The user ID
    
    Returns:
        User if found, None otherwise
    """
    return await repository.get(user_id)
```

#### Naming Conventions
- Classes: `PascalCase` (e.g., `UserService`)
- Functions: `snake_case` (e.g., `get_user`)
- Constants: `UPPER_CASE` (e.g., `MAX_RETRY_ATTEMPTS`)
- Private: `_leading_underscore`

#### Structure
```python
# 1. Module docstring
# 2. Imports (stdlib, third-party, local)
# 3. Constants
# 4. Functions/Classes
# 5. Main block
```

### TypeScript (Frontend)

#### Style
- Use ESLint and Prettier
- Use strict TypeScript settings
- Use type hints for all functions

#### Example
```typescript
/**
 * Get user by ID
 * @param userId - The user ID
 * @returns User promise
 */
export async function getUser(userId: string): Promise<User | null> {
  return await api.get(`/users/${userId}`);
}
```

#### Naming Conventions
- Interfaces: `PascalCase`
- Functions: `camelCase`
- Constants: `UPPER_CASE`
- Components: `PascalCase`

### Database

#### Naming Conventions
- Tables: `snake_case` (e.g., `user_profiles`)
- Columns: `snake_case` (e.g., `created_at`)
- Indexes: `idx_table_columns` (e.g., `idx_users_email`)

#### Standards
- Use UUID for primary keys
- Include `created_at` and `updated_at` timestamps
- Use `is_active` for soft deletes
- Add indexes for foreign keys and frequently queried columns

### Git Commits

#### Message Format
```
<type>(<scope>): <subject>

<body>

<footer>
```

#### Example
```
feat(auth): implement JWT token refresh

- Add refresh token endpoint
- Implement token rotation
- Add rate limiting

Fixes #123
```

#### Commit Types
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation
- `style:` Code style
- `refactor:` Code refactoring
- `perf:` Performance improvement
- `test:` Test addition
- `ci:` CI/CD changes

---

## Testing

### Backend Testing
```bash
# Unit tests
pytest services/auth-service/tests/unit -v

# Integration tests
pytest services/auth-service/tests/integration -v

# Coverage
pytest services/auth-service/tests --cov=app --cov-report=html

# Specific test
pytest services/auth-service/tests::test_user_registration -v
```

### Frontend Testing
```bash
# Run tests
npm run test --workspace=apps/web

# Watch mode
npm run test:watch --workspace=apps/web

# Coverage
npm run test:coverage --workspace=apps/web
```

### Test Naming Convention
```python
def test_function_with_condition_returns_expected_result():
    """Test naming convention for clarity."""
    # Arrange
    expected = ...
    
    # Act
    result = function_under_test()
    
    # Assert
    assert result == expected
```

### Pull Request Description
```markdown
## Description
Brief description of changes

## Changes
- Change 1
- Change 2

## Testing
- [ ] Unit tests added
- [ ] Integration tests added
- [ ] Manual testing done

## Related Issues
Fixes #123

## Checklist
- [ ] Code follows conventions
- [ ] Tests pass
- [ ] No console errors
- [ ] Documentation updated
```

---

## Performance Guidelines

### Backend
- API endpoints: < 100ms (p95)
- Database queries: < 50ms
- Cache hit rate: > 80%
- Error rate: < 0.1%

### Frontend
- Page load: < 3s
- First Contentful Paint: < 1.5s
- Time to Interactive: < 3s
- Cumulative Layout Shift: < 0.1

---

## Security Guidelines

### Code Review
- All code must be reviewed before merging
- Security review for any auth/payment code
- Dependency scanning for vulnerabilities

### Secrets
- Never commit secrets or API keys
- Use environment variables
- Use `.env.example` for documentation
- Rotate secrets regularly

### Dependencies
```bash
# Check for vulnerabilities
npm audit
safety check  # Python

# Update dependencies
npm update
pip list --outdated
```

---

## Implementation Roadmap

### Phase 1: Foundation (Weeks 1-2) ✅
- [x] Terraform infrastructure
- [x] Docker Compose setup
- [x] GitHub Actions CI/CD
- [x] Kubernetes manifests
- [x] Monitoring stack (Prometheus, Grafana, Loki, Jaeger)

### Phase 2: Core Services (Weeks 3-6)
- [x] Auth Service (core complete)
- [ ] Auth Service (routes remaining)
- [ ] User Service
- [ ] Content Service
- [ ] Streaming Service
- [ ] Search Service
- [ ] API Gateway

### Phase 3: Business Logic (Weeks 7-10)
- [ ] Billing Service
- [ ] Recommendation Engine
- [ ] Analytics Service
- [ ] Notification Service
- [ ] Admin Service

### Phase 4: Media Pipeline (Weeks 11-12)
- [ ] Video transcoding
- [ ] HLS packaging
- [ ] DASH packaging
- [ ] CDN integration

### Phase 5: Frontend (Weeks 13-14)
- [ ] Next.js project
- [ ] Component library
- [ ] Video player
- [ ] Content browsing
- [ ] Admin dashboard

### Phase 6: Deployment & Operations (Weeks 15-16)
- [ ] Kubernetes manifests
- [ ] Helm charts
- [ ] Deployment procedures
- [ ] Operational runbooks

### Phase 7: Testing & Quality (Ongoing)
- [ ] Unit tests
- [ ] Integration tests
- [ ] E2E tests
- [ ] Load testing
- [ ] Code quality

### Phase 8: Security (Ongoing)
- [ ] OAuth2 integration
- [ ] MFA support
- [ ] Data encryption
- [ ] Security audit

### Phase 9: Optimization & Scaling (Ongoing)
- [ ] Performance tuning
- [ ] Database optimization
- [ ] Caching strategy
- [ ] Cost optimization

---

## Troubleshooting

### Database Connection Failed
```bash
# Check PostgreSQL
docker-compose ps postgres
docker-compose logs postgres

# Check connection
psql -h localhost -U wildframe -d auth_db
```

## Quick Local Setup Checklist

Add this checklist for quick reference of immediate local actions.

- [ ] Read core docs and README

```bash
sed -n '1,200p' README.md
sed -n '1,200p' docs/DOCUMENTATION_GUIDE.md
```

- [ ] Start local infra with Docker Compose

```bash
docker compose -f deployments/docker-compose.dev.yml up --build
```

- [ ] Resolve Docker layer/blob error (if encountered)

```bash
docker system prune -a --volumes
docker builder prune -a
sudo systemctl restart docker   # or restart Docker Desktop
```

- [ ] Bring up `admin-service` and verify health

```bash
docker compose -f deployments/docker-compose.dev.yml up --build admin-service
curl http://localhost:8006/health
```

- [ ] Run unit tests (project-wide)

```bash
./run_tests.sh
# or
pytest -q
```

- [ ] Open and inspect service code you want to work on

```bash
ls -la services
code .
rg "uvicorn|FastAPI|if __name__ == \"__main__\"" -S --hidden || true
```

_Saved from session: May 27, 2026_

### Port Already in Use
```bash
# Find and kill process
lsof -i :8000
kill -9 <PID>
```

### Redis Connection Failed
```bash
# Check Redis
redis-cli ping

# Clear Redis
redis-cli FLUSHDB
```

### Docker Issues
```bash
# Clean up
docker-compose down -v
docker system prune

# Rebuild
docker-compose up --build
```

---

## Technical Glossary

### Architectural Concepts

**Microservices**: Independent, loosely coupled services that own their data and communicate via APIs or events.

**Clean Architecture**: Layered architecture (API → Services → Domain → Infrastructure) with clear separation of concerns.

**Event-Driven Architecture**: Services communicate asynchronously through events rather than direct API calls.

**Database per Service**: Each microservice owns an independent PostgreSQL database instead of sharing one.

### Technology Terms

**FastAPI**: Modern Python web framework with automatic API documentation, type hints, async/await, and dependency injection.

**SQLAlchemy 2.0**: Python ORM with async support for database access.

**PostgreSQL**: Enterprise-grade relational database with ACID compliance, JSON types, and full-text search.

**Redis**: In-memory cache and session store for rate limiting, caching, and pub/sub messaging.

**Kafka**: Event streaming platform with topics, partitions, and consumer groups.

**Elasticsearch**: Full-text search engine with tokenization, stemming, faceted search, and aggregations.

**Kubernetes (K8s)**: Container orchestration managing pods, deployments, services, and auto-scaling.

### Security Terms

**JWT (JSON Web Token)**: Stateless token containing claims (user ID, email, roles), signed by server.
- **Access Token**: Short-lived (15 min)
- **Refresh Token**: Long-lived (7 days)

**Bcrypt**: Password hashing algorithm with configurable cost factor and salt.

**Rate Limiting**: Restricting request count to prevent abuse using sliding window algorithm.

**Correlation ID**: Unique identifier tracking a request through all services and logs.

### DevOps Terms

**Docker**: Container technology creating consistent environments from development to production.

**Kubernetes**: Manages containerized applications with deployments, auto-scaling, and rolling updates.

**Helm**: Package manager for Kubernetes using charts, releases, and values.

**Terraform**: Infrastructure as Code for AWS managing resources, variables, and state.

**GitHub Actions**: CI/CD platform for automated testing, building, and deploying.

### Database Terms

**Transaction**: Group of queries executed atomically with ACID guarantees.

**Index**: Data structure accelerating query lookups (B-tree, GiST, Partial).

**Connection Pool**: Reused database connections reducing overhead.

**Migration**: Schema change applied incrementally using Alembic.

### Monitoring & Observability Terms

**Metrics**: Numerical measurements over time (latency, throughput, error rate, p95/p99).

**Logging**: Recording application events with structured JSON format and centralized collection.

**Tracing**: Tracking request flow across services (spans, traces, instrumentation).

**Alerting**: Automated notifications when metrics exceed thresholds.

### Application Terms

**Session**: User state for request duration stored in Redis or database.

**Token Refresh**: Obtaining new access token using refresh token for enhanced security.

**Rate Limiter**: Sliding window algorithm preventing abuse per-user, per-IP, per-endpoint.

**Watchlist**: User's saved content list for later viewing stored in PostgreSQL.

### Video Streaming Terms

**Adaptive Bitrate Streaming**: Dynamically adjusting video quality based on connection (HLS, DASH).

**Bitrate**: Data rate of video (240p: 500 kbps, 4K: 15000 kbps).

**Transcode**: Converting video to multiple resolutions/codecs (H.264, VP9, AV1).

---

Last Updated: May 26, 2026
