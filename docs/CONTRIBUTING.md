# 🔨 Contributing to Wildframe

**Version**: 2.0.0  
**Last Updated**: May 27, 2026  

## Overview

Welcome to Wildframe development! This guide covers code conventions, development workflow, testing standards, and our git workflow.

**Time to read**: 10 minutes  
**Prerequisites**: Python 3.14+, Docker, Git

## Table of Contents

1. [Code Conventions](#code-conventions)
2. [Development Workflow](#development-workflow)
3. [Testing Requirements](#testing-requirements)
4. [Git Workflow](#git-workflow)
5. [Pull Request Process](#pull-request-process)
6. [Security Guidelines](#security-guidelines)

---

## Code Conventions

### Python Code Style

We follow PEP 8 with Black formatter (100 character line length).

**✅ Good Example**:
```python
from typing import Optional
from fastapi import FastAPI, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

app = FastAPI()


class UserService:
    """Service for user profile management."""
    
    def __init__(self, db: AsyncSession):
        """Initialize user service.
        
        Args:
            db: Database session
        """
        self.db = db
    
    async def get_user(self, user_id: str) -> Optional[dict]:
        """Get user by ID.
        
        Args:
            user_id: User ID to retrieve
            
        Returns:
            User dict or None if not found
            
        Raises:
            ValueError: If user_id is invalid
        """
        if not user_id:
            raise ValueError("user_id cannot be empty")
        
        return await self.db.get_user(user_id)
```

**❌ Bad Example**:
```python
# Too many things in one function
def getUser(id,session,cache,logger):
    # Do lots of stuff
    user = session.query(User).filter(User.id==id).first()
    if not user:return None
    cache.set(f"user:{id}",user)
    logger.info(f"Got user {id}")
    return user
```

### File Structure

```
services/auth-service/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app creation
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes/
│   │       ├── __init__.py
│   │       └── auth.py            # Auth endpoints
│   ├── core/
│   │   ├── __init__.py
│   │   ├── settings.py            # Configuration
│   │   ├── database.py            # DB connection
│   │   └── logging.py             # Logging setup
│   ├── models/
│   │   ├── __init__.py
│   │   └── user.py                # SQLAlchemy models
│   ├── repositories/
│   │   ├── __init__.py
│   │   └── user_repository.py     # Data access
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── auth.py                # Pydantic schemas
│   ├── services/
│   │   ├── __init__.py
│   │   └── auth_service.py        # Business logic
│   └── security/
│       ├── __init__.py
│       └── manager.py             # Auth utilities
├── tests/
│   ├── __init__.py
│   └── test_auth_service.py
├── migrations/                    # planned Alembic migrations (not yet implemented)
├── Dockerfile
├── pyproject.toml
└── README.md
```

### Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| **Classes** | PascalCase | `UserService`, `AuthRepository` |
| **Functions/Methods** | snake_case | `get_user()`, `create_token()` |
| **Constants** | UPPER_SNAKE_CASE | `MAX_RETRIES`, `JWT_EXPIRATION` |
| **Variables** | snake_case | `user_id`, `auth_token` |
| **Database Tables** | snake_case | `user_profiles`, `refresh_tokens` |
| **API Routes** | /kebab-case | `/api/v1/auth/login` |
| **Files** | snake_case | `user_repository.py` |

### Type Hints

Always use type hints for functions and classes:

```python
from typing import Optional, List, Dict
from uuid import UUID
from datetime import datetime

async def get_users(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None
) -> List[Dict[str, str]]:
    """Get paginated users."""
    pass

def create_token(user_id: UUID, expires_in: int) -> str:
    """Create JWT token."""
    pass
```

### Documentation Strings

Use Google-style docstrings:

```python
def authenticate_user(email: str, password: str) -> Optional[User]:
    """Authenticate user with email and password.
    
    This function validates credentials against the database
    and checks for account lockouts.
    
    Args:
        email: User's email address
        password: User's password (plaintext)
        
    Returns:
        User object if authentication succeeds, None otherwise
        
    Raises:
        ValueError: If email format is invalid
        AccountLockedError: If user account is locked
        
    Example:
        >>> user = authenticate_user("user@example.com", "pass123")
        >>> if user:
        ...     print(f"Welcome {user.first_name}")
    """
    pass
```

---

## Development Workflow

### 1. Setup Local Environment

```bash
# Clone repository
git clone https://github.com/wildframe/wildframe.git
cd wildframe

# Create feature branch
git checkout -b feature/new-feature

# Start services
docker-compose -f deployments/docker-compose.dev.yml up -d
```

### 2. Make Changes

```bash
# Edit files in your favorite editor
vim services/auth-service/app/api/routes/auth.py

# Changes auto-reload in container due to hot reload
# No need to restart
```

### 3. Write Tests

```bash
# Add tests as you code
vim services/auth-service/tests/test_auth_service.py

# Run tests frequently
cd services/auth-service
python3 -m pytest tests/ -v

# Run specific test
python3 -m pytest tests/test_auth_service.py::TestUserLogin -v
```

### 4. Check Code Quality

```bash
# Format code
black app/
isort app/

# Run type checker
mypy app/

# Run linter
pylint app/

# Check coverage
pytest tests/ --cov=app --cov-report=html
```

### 5. Commit Changes

```bash
# Stage changes
git add services/auth-service/

# Commit with descriptive message
git commit -m "feat: add MFA support to login endpoint

- Add TOTP-based second factor authentication
- Update user model with mfa_enabled field
- Create MFA verification service
- Add 10+ tests for MFA flow

Fixes #123"
```

---

## Testing Requirements

### Unit Tests (Required)

Test individual functions/classes in isolation:

```python
@pytest.mark.asyncio
async def test_create_user_success():
    """Test successful user creation."""
    service = UserService(mock_db)
    user = await service.create_user("user@example.com", "pass123")
    
    assert user.email == "user@example.com"
    assert user.id is not None
    mock_db.add.assert_called_once()


@pytest.mark.asyncio
async def test_create_user_invalid_email():
    """Test user creation with invalid email."""
    service = UserService(mock_db)
    
    with pytest.raises(ValueError, match="Invalid email"):
        await service.create_user("invalid-email", "pass123")
```

### Integration Tests (Required)

Test services working together:

```python
@pytest.mark.asyncio
@pytest.mark.integration
async def test_user_registration_flow(client, db):
    """Test complete user registration flow."""
    # Register user
    response = client.post("/api/v1/auth/register", json={
        "email": "newuser@example.com",
        "password": "SecurePass123!"
    })
    
    assert response.status_code == 201
    assert response.json()["email"] == "newuser@example.com"
    
    # Verify user was created in database
    user = await db.get_user_by_email("newuser@example.com")
    assert user is not None
```

### Test Coverage

**Minimum Coverage by Service**:

| Service | Coverage Target | Path |
|---------|-----------------|------|
| Auth | 85%+ | services/auth-service/app |
| User | 80%+ | services/user-service/app |
| Content | 75%+ | services/content-service/app |
| Admin | 80%+ | services/admin-service/app |

### Running Tests

```bash
# Run all tests
cd services/auth-service
python3 -m pytest tests/ -v

# Run with coverage
python3 -m pytest tests/ --cov=app --cov-report=html

# Run only fast tests
python3 -m pytest tests/ -m "not slow" -v

# Run specific test file
python3 -m pytest tests/test_auth_service.py -v

# Run with detailed output
python3 -m pytest tests/ -vv --tb=long
```

---

## Git Workflow

### Branch Naming

```
feature/                    New feature
bugfix/                     Bug fix
refactor/                   Code refactoring
docs/                       Documentation
test/                       Test improvements
chore/                      Build, CI, dependencies

Example:
  feature/user-mfa
  bugfix/password-reset-email
  refactor/service-factory
  docs/api-reference
```

### Commit Message Format

```
<type>: <subject>

<body>

<footer>

Types: feat, fix, refactor, test, docs, chore
Subject: Max 50 characters, lowercase, imperative
Body: Why and what, not how (max 72 chars per line)
Footer: Issue references, breaking changes
```

**Examples**:

```
feat: add email verification to registration

Users must now verify their email before accessing the platform.
Sends verification link via email, expires in 24 hours.

Fixes #456

Breaking Change: new email field in user creation response
```

```
fix: handle null values in watch history query

Previously would crash if user had null progress_percentage.
Now defaults to 0 when calculating completion percentage.

Fixes #789
```

---

## Pull Request Process

### Before Creating PR

- [ ] Code follows style guide (black, isort formatted)
- [ ] All tests pass locally
- [ ] New tests added for new functionality
- [ ] Test coverage ≥ target percentage
- [ ] No security vulnerabilities (run bandit)
- [ ] Documentation updated
- [ ] Commit messages follow format

### Creating PR

```bash
# Push branch
git push origin feature/new-feature

# Create PR via GitHub UI
# Title: Clear, descriptive summary
# Description: Use template below
```

### PR Description Template

```markdown
## Description
Brief summary of changes

## Type of Change
- [ ] New feature
- [ ] Bug fix
- [ ] Breaking change
- [ ] Documentation update

## Changes Made
- Change 1
- Change 2
- Change 3

## How to Test
1. Step 1
2. Step 2
3. Expected result

## Screenshots (if applicable)
[Add screenshots for UI changes]

## Checklist
- [ ] Tests pass
- [ ] Code coverage maintained/improved
- [ ] Documentation updated
- [ ] No breaking changes (or documented)
- [ ] Ready for production
```

### PR Review Process

1. **Automated Checks**:
   - ✅ Tests pass
   - ✅ Code coverage maintained
   - ✅ Linting passes
   - ✅ Type checking passes

2. **Code Review**:
   - At least 1 approval required
   - Maintainers check design decisions
   - Security review for auth/data changes

3. **Merge**:
   - Squash commits for cleaner history
   - Delete branch after merge
   - Deploy to staging automatically (CI/CD)

---

## Security Guidelines

### Authentication

❌ **Never**:
```python
# Store passwords in plain text
user.password = password

# Log sensitive data
logger.info(f"User password: {password}")

# Return tokens in response headers
return {"token": access_token}
```

✅ **Always**:
```python
# Hash passwords
user.password_hash = hash_password(password)

# Don't log secrets
logger.info(f"User {user_id} authenticated")

# Return tokens in body with secure headers
response = {"access_token": token}
response.headers["Set-Cookie"] = f"token={token}; HttpOnly; Secure"
```

### Authorization

```python
# Check permissions on every protected endpoint
@router.get("/admin/users")
async def list_all_users(current_user: User = Depends(get_current_user)):
    # Verify user is admin
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Forbidden")
    
    return await get_users()
```

### Input Validation

```python
# Always validate user input
from pydantic import BaseModel, EmailStr, Field

class UserRegisterRequest(BaseModel):
    email: EmailStr  # Validates email format
    password: str = Field(min_length=8, max_length=128)
    
# Pydantic automatically validates before your code runs
```

### SQL Injection Prevention

```python
# ❌ Never use string concatenation
query = f"SELECT * FROM users WHERE email = '{email}'"

# ✅ Always use parameterized queries
query = select(User).where(User.email == email)
```

### Secrets Management

```bash
# ❌ Never commit secrets
# DO NOT add to git:
DATABASE_PASSWORD=secret123
JWT_SECRET_KEY=my-secret-key

# ✅ Use environment variables or AWS Secrets Manager
DATABASE_PASSWORD=${DATABASE_PASSWORD}
JWT_SECRET_KEY=${JWT_SECRET_KEY}
```

---

## Code Review Checklist

When reviewing PRs, check:

- [ ] **Functionality**: Does it do what it's supposed to?
- [ ] **Testing**: Are tests adequate and passing?
- [ ] **Style**: Does code follow conventions?
- [ ] **Performance**: Any obvious inefficiencies?
- [ ] **Security**: Are there security issues?
- [ ] **Error Handling**: Are errors handled gracefully?
- [ ] **Documentation**: Is it clear and complete?
- [ ] **Dependencies**: Any unnecessary or vulnerable deps?

---

## Common Issues

### Issue: "Black format check failed"

**Solution**:
```bash
# Format automatically
black app/
isort app/

# Then commit
git add app/
git commit -m "style: format code"
```

### Issue: "Test coverage below 80%"

**Solution**:
```bash
# Check which lines are uncovered
pytest tests/ --cov=app --cov-report=html
# Open htmlcov/index.html in browser

# Add tests for those lines
# Aim for high coverage on critical paths
```

### Issue: "Type checker failed"

**Solution**:
```bash
# Run mypy to see issues
mypy app/

# Fix type hints
# If legitimate false positive, add: # type: ignore
```

---

## Resources

- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [SQLAlchemy Async](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [Pydantic V2](https://docs.pydantic.dev/latest/)
- [Pytest Docs](https://docs.pytest.org/)
- [Python PEP 8](https://www.python.org/dev/peps/pep-0008/)

---

## Questions?

- Check existing documentation
- Review similar code in the codebase
- Ask in pull request comments
- Create an issue for clarification

**Thank you for contributing!** 🙏
