# Production-Grade OTT Platform - Contribution Guide

## Code of Conduct

We are committed to providing a welcoming and inclusive environment for all contributors.

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js 18+
- PostgreSQL 14+
- Redis 7+
- Docker & Docker Compose

### Development Setup

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
python -m uvicorn services/user-service/app/main:app --reload --port 8002
python -m uvicorn services/content-service/app/main:app --reload --port 8003
```

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
pytest services/auth-service/tests

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

Use conventional commits:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation
- `style:` Code style
- `refactor:` Code refactoring
- `perf:` Performance improvement
- `test:` Test addition
- `ci:` CI/CD changes

### 5. Push and Create Pull Request
```bash
git push origin feature/description
```

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
- Interfaces: `IPascalCase` or `PascalCase`
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

### Test Naming
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

## Documentation

### Code Documentation
- Write docstrings for all public functions/classes
- Include type hints
- Provide examples for complex logic
- Document exceptions

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

## Deployment

### Staging Deployment
```bash
git push origin feature/branch
# GitHub Actions will auto-deploy to staging
# Run smoke tests
kubectl rollout status deployment/api-gateway -n wildframe-staging
```

### Production Deployment
- Create pull request
- Wait for all tests to pass
- Get code review approval
- Merge to main
- GitHub Actions will deploy to production
- Verify deployment

## Troubleshooting

### Common Issues

#### Database Connection Failed
```bash
# Check PostgreSQL
docker-compose ps postgres
docker-compose logs postgres

# Check connection
psql -h localhost -U wildframe -d auth_db
```

#### Port Already in Use
```bash
# Find and kill process
lsof -i :8000
kill -9 <PID>
```

#### Redis Connection Failed
```bash
# Check Redis
redis-cli ping

# Clear Redis
redis-cli FLUSHDB
```

## Support

- **Documentation**: https://docs.wildframe.com
- **Issues**: https://github.com/wildframe/platform/issues
- **Discussions**: https://github.com/wildframe/platform/discussions
- **Slack**: #engineering channel in Wildframe workspace

## License

All contributions are licensed under the Proprietary Wildframe License.

---

Thank you for contributing to Wildframe! 🚀
