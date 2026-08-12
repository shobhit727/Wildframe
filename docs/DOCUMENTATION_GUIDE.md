# Documentation Guide: For Humans and AI

Complete guide to writing documentation that works effectively for both people and AI assistants.

## Table of Contents
1. [Principles](#principles)
2. [Structure](#structure)
3. [Writing Best Practices](#writing-best-practices)
4. [Format Specifications](#format-specifications)
5. [Examples](#examples)
6. [AI-Friendly Markup](#ai-friendly-markup)
7. [Testing Your Docs](#testing-your-docs)

---

## Principles

### For Humans
✅ Clear and concise  
✅ Progressive disclosure (simple → complex)  
✅ Real-world examples  
✅ Visual organization  
✅ Easy navigation  
✅ Searchable keywords  

### For AI
✅ Structured and consistent  
✅ Explicit relationships and hierarchies  
✅ Code samples with context  
✅ Clear problem-solution mapping  
✅ Metadata and tagging  
✅ Unambiguous language  

### Universal Principles
✅ **Accuracy** - Information must be correct and up-to-date  
✅ **Completeness** - Cover the full scope, with links to details  
✅ **Clarity** - Use simple language, avoid jargon (or define it)  
✅ **Consistency** - Use same terminology, structure, and formatting  
✅ **Maintainability** - Easy to update without breaking references  

---

## Structure

### Document Organization

Every documentation file should follow this structure:

```markdown
# Main Title

## Overview
- What is this about? (1-2 sentences)
- Who should read it? (1-2 sentences)
- Quick stats or key numbers

## Table of Contents
- Numbered sections
- Subsections
- Quick links to important sections

## Prerequisites / Getting Started
- What you need before reading
- Required knowledge
- Setup requirements

## Main Content Sections
- Progressive complexity (simple → advanced)
- Real examples after each concept
- Links to related documentation

## Common Tasks / How-To
- Step-by-step procedures
- Copy-paste ready code
- Expected outcomes

## Troubleshooting
- Common issues
- Solutions with examples
- When to escalate

## Reference
- API/CLI reference
- Links to related docs
- Glossary terms

## See Also
- Related documentation
- External resources
- Next steps
```

### Section Hierarchy

Use consistent markdown heading levels:

```markdown
# Level 1 - Document Title (only one per file)
## Level 2 - Major sections
### Level 3 - Subsections with details
#### Level 4 - Code examples, notes
```

---

## Writing Best Practices

### 1. Start with Purpose

**❌ Bad:**
```markdown
Database Connection

PostgreSQL is a database management system...
```

**✅ Good:**
```markdown
## Database Connection

Connect your application to PostgreSQL with connection pooling 
for production deployments. This section covers setup, pooling 
configuration, and connection management.

**Time to read**: 5 minutes  
**Prerequisites**: PostgreSQL 14+, Python 3.11+
```

### 2. Use Active Voice

**❌ Bad:** "The database is queried by the service"  
**✅ Good:** "The service queries the database"

### 3. Be Specific

**❌ Bad:** "Deploy the application"  
**✅ Good:** "Deploy the auth service to Kubernetes using Helm"

### 4. One Idea Per Sentence

**❌ Bad:** "Configure the database with connection pooling and enable SSL encryption while setting the max connections to 50."

**✅ Good:**
```
- Configure the database with connection pooling
- Enable SSL encryption
- Set max connections to 50
```

### 5. Add Context Before Code

**❌ Bad:**
```python
DATABASE_URL = "postgresql://..."
```

**✅ Good:**
```
Set the database URL in your `.env` file:

DATABASE_URL=postgresql://user:pass@localhost:5432/mydb

This tells SQLAlchemy where to connect.
```

### 6. Explain the "Why"

**❌ Bad:** "Add `@app.middleware("http")`"

**✅ Good:** "Add middleware to track request metrics for monitoring.
This runs on every request so we can measure latency and error rates:

```python
@app.middleware("http")
async def track_metrics(request: Request, call_next):
    ...
```

### 7. Use Examples Liberally

Include:
- Basic examples (copy-paste ready)
- Real-world examples (from actual code)
- Edge cases (what could go wrong)
- Expected output (what you should see)

---

## Format Specifications

### Markdown Extensions

Use these consistently across all docs:

#### Code Blocks with Language Specification
```markdown
\`\`\`python
def function():
    pass
\`\`\`

\`\`\`bash
command --flag value
\`\`\`

\`\`\`sql
SELECT * FROM users WHERE active = true;
\`\`\`
```

#### Admonitions (Notes, Warnings, Tips)
```markdown
> **Note**: This is important context about the topic.

> **⚠️ Warning**: Be careful here, common mistake.

> **💡 Tip**: Here's a pro tip to make this easier.

> **ℹ️ Info**: Additional information that's nice to know.
```

#### Links
```markdown
[Link text](docs/DEVELOPMENT.md)  # Internal link
[Link text](https://example.com)  # External link
[reference]: https://example.com  # Reference link
```

#### Lists
```markdown
# Unordered (use for items with no sequence)
- Item 1
- Item 2
  - Nested item
  
# Ordered (use for step-by-step)
1. First step
2. Second step
   1. Nested step
```

#### Tables
```markdown
| Column 1 | Column 2 | Column 3 |
|----------|----------|----------|
| Data     | Data     | Data     |
| Data     | Data     | Data     |
```

#### Code Inline
```markdown
Use `function_name()` for inline code references.
```

---

## Examples

### Example 1: API Endpoint Documentation

```markdown
## POST /auth/login

Authenticate a user and receive JWT access and refresh tokens.

### Request

```bash
curl -X POST https://api.wildframe.com/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "secure_password"
  }'
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| email | string | Yes | User email address |
| password | string | Yes | User password (min 8 chars) |

### Response (200 OK)

```json
{
  "access_token": "eyJhbGc...",
  "refresh_token": "eyJhbGc...",
  "expires_in": 900,
  "token_type": "Bearer",
  "user": {
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "email": "user@example.com",
    "first_name": "John",
    "last_name": "Doe"
  }
}
```

### Errors

| Status | Code | Message | Solution |
|--------|------|---------|----------|
| 401 | INVALID_CREDENTIALS | Email or password incorrect | Verify credentials and try again |
| 429 | RATE_LIMIT_EXCEEDED | Too many login attempts | Wait 30 minutes before retrying |
| 500 | INTERNAL_ERROR | Server error | Retry after 5 seconds |

### Examples

**Example 1: Successful login**
```bash
# Request
curl -X POST https://api.wildframe.com/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "MySecurePass123"
  }'

# Response
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "expires_in": 900,
  "token_type": "Bearer"
}
```

**Example 2: Using token to access protected endpoint**
```bash
curl -X GET https://api.wildframe.com/users/me \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."

# Response
{
  "id": "123e4567-e89b-12d3-a456-426614174000",
  "email": "user@example.com",
  "first_name": "John",
  "is_active": true
}
```

### See Also
- [Refresh Token Endpoint](/docs/API.md#post-authrefresh)
- [JWT Token Structure](/docs/ARCHITECTURE.md#jwt-authentication)
- [Rate Limiting](/docs/ARCHITECTURE.md#rate-limiting)
```

### Example 2: Troubleshooting Section

```markdown
## Troubleshooting

### Problem: Database Connection Timeout

**Symptoms**
- Application crashes on startup
- Error: "could not connect to server"
- Logs show connection timeout after 30 seconds

**Causes**
- PostgreSQL service not running
- Incorrect connection string
- Firewall blocking connection
- Database credentials wrong

**Solutions**

1. **Check if PostgreSQL is running**
   ```bash
   # macOS
   brew services list | grep postgres
   
   # Linux
   sudo systemctl status postgresql
   
   # Docker
   docker ps | grep postgres
   ```

2. **Verify connection string in `.env`**
   ```bash
   # Should be in format:
   DATABASE_URL=postgresql://user:password@host:port/database
   
   # Example:
   DATABASE_URL=postgresql://wildframe:pass@localhost:5432/auth_db
   ```

3. **Test connection manually**
   ```bash
   psql postgresql://user:password@localhost:5432/auth_db
   # Should show: postgres=#
   ```

4. **Check firewall (if remote database)**
   ```bash
   # Test port connectivity
   nc -zv db.example.com 5432
   # Should show: Connection succeeded
   ```

**If still failing**
- Check `/var/log/postgresql/` for detailed error logs
- Try connecting with a database client (pgAdmin, DBeaver)
- Verify network connectivity: `ping db.example.com`

### Problem: Slow Database Queries

**Symptoms**
- API endpoints responding in 500ms+
- High CPU usage on database server
- Large result sets taking too long

**Analysis**

1. **Identify slow queries**
   ```sql
   SELECT query, calls, mean_time
   FROM pg_stat_statements
   ORDER BY mean_time DESC
   LIMIT 10;
   ```

2. **Analyze query plan**
   ```sql
   EXPLAIN ANALYZE
   SELECT * FROM users WHERE email = 'user@example.com';
   ```

3. **Check for missing indexes**
   ```sql
   SELECT * FROM pg_stat_user_indexes
   WHERE idx_scan = 0;
   ```

**Solutions**
- Add indexes on frequently filtered columns
- Use connection pooling (PgBouncer)
- Optimize N+1 queries with JOINs
- Add caching layer (Redis)

**See Also**
- [Query Optimization](/docs/OPERATIONS.md#query-optimization)
- [Performance Tuning](/docs/OPERATIONS.md#performance-tuning)
```

### Example 3: How-To Guide

```markdown
## How to: Deploy a Service to Kubernetes

This guide walks through deploying the auth service to Kubernetes 
production cluster using Helm.

**Time required**: 15 minutes  
**Prerequisites**: kubectl, Helm, Docker image pushed to registry

### Step 1: Prepare Your Environment

```bash
# Set cluster context
kubectl config use-context wildframe-prod

# Verify connection
kubectl cluster-info

# Check namespace
kubectl get ns | grep wildframe
```

### Step 2: Prepare Helm Values

Create `values-prod.yaml`:

```yaml
image:
  repository: 123456789.dkr.ecr.us-east-1.amazonaws.com/auth-service
  tag: v1.0.0
  pullPolicy: IfNotPresent

replicas: 3

resources:
  requests:
    cpu: 500m
    memory: 512Mi
  limits:
    cpu: 1000m
    memory: 1Gi

env:
  DATABASE_URL: postgresql://user:pass@db.rds.amazonaws.com:5432/auth_db
  LOG_LEVEL: INFO
  ENVIRONMENT: production
```

### Step 3: Deploy with Helm

```bash
# Create release
helm upgrade --install auth-service \
  ./helm/auth-service \
  -f values-prod.yaml \
  -n wildframe-prod

# Watch deployment
kubectl get pods -n wildframe-prod -w
```

### Step 4: Verify Deployment

```bash
# Check pod status
kubectl get pods -n wildframe-prod
# Should show: Running status

# Check logs
kubectl logs deployment/auth-service -n wildframe-prod

# Test health endpoint
kubectl port-forward svc/auth-service 8000:8000 -n wildframe-prod
curl https://localhost:8000/health
```

### Troubleshooting

If pods don't start:
```bash
# Describe pod for events
kubectl describe pod <pod-name> -n wildframe-prod

# Check logs
kubectl logs <pod-name> -n wildframe-prod

# Common issues:
# - Image not found: check ECR credentials
# - Out of memory: increase limits
# - Port in use: verify no duplicate services
```

### Rollback if Needed

```bash
# Check release history
helm history auth-service -n wildframe-prod

# Rollback to previous version
helm rollback auth-service 1 -n wildframe-prod
```
```

---

## AI-Friendly Markup

### 1. Use Structured Data Format

For complex information, use consistent formatting:

```markdown
### Service Configuration

**Service Name**: auth-service  
**Language**: Python 3.11  
**Framework**: FastAPI 0.104+  
**Database**: PostgreSQL 14+  
**Cache**: Redis 7.0+  
**Port**: 8001  
**Health Check**: GET /health  
```

### 2. Add Metadata in Code Comments

```python
"""
User authentication service.

Service metadata:
- Name: auth-service
- Version: 1.0.0
- Database: auth_db
- Cache: redis://cache:6379
- Logging: structured JSON

Endpoints:
- POST /auth/register - Create new user
- POST /auth/login - Authenticate user
- POST /auth/refresh - Get new access token
"""
```

### 3. Use Decision Trees for Complex Choices

```markdown
## Choosing a Deployment Strategy

```
Start
├─ Is this a new service?
│  ├─ Yes → Use blue-green deployment
│  └─ No → Continue
├─ Is this a critical service?
│  ├─ Yes → Use canary deployment
│  └─ No → Use rolling deployment
└─ Document chosen strategy
```
```

### 4. Explicit Relationships

```markdown
## Data Model Relationships

**User Service** ←→ **Auth Service**
- User Service calls Auth Service to validate JWT tokens
- Auth Service publishes `user.registered` events
- Both services use their own PostgreSQL database

**Streaming Service** ← **User Service** ← **Auth Service**
- Streaming Service needs user profile from User Service
- User Service needs user identity from Auth Service
- Chain: Auth validates → User gets profile → Streaming serves video
```

### 5. Version and Date Information

```markdown
# API Documentation

**Version**: 2.0.0  
**Last Updated**: May 27, 2026  
**Stability**: Stable  
**Deprecations**: See [Changelog](CHANGELOG.md#v200)  

### Versions Reference

| Version | Status | Supported | End of Life |
|---------|--------|-----------|-------------|
| 2.0 | Current | Yes | May 27, 2028 |
| 1.5 | Deprecated | Yes | May 27, 2027 |
| 1.0 | Unsupported | No | May 27, 2026 |
```

---

## Testing Your Docs

### For Humans

- [ ] **Readability**: Can a junior developer understand this?
- [ ] **Completeness**: Does it cover all the main cases?
- [ ] **Clarity**: Is the language simple and jargon-free?
- [ ] **Navigation**: Can I find what I need easily?
- [ ] **Examples**: Are there real copy-paste examples?
- [ ] **Links**: Do internal links work?
- [ ] **Screenshots/Diagrams**: Are visuals helpful and current?

### For AI

- [ ] **Structure**: Is the document hierarchy clear (H1 → H2 → H3)?
- [ ] **Consistency**: Does terminology stay consistent throughout?
- [ ] **Examples**: Are code examples complete and runnable?
- [ ] **Metadata**: Is important information easily extractable?
- [ ] **Links**: Are internal references properly formatted?
- [ ] **Language**: Is language unambiguous and specific?
- [ ] **Context**: Is there enough context before code snippets?

### Quality Checklist

Before publishing documentation:

```markdown
- [ ] Grammar and spelling checked
- [ ] Code examples tested (run them!)
- [ ] Links verified (internal and external)
- [ ] Screenshots/diagrams current
- [ ] Consistent formatting (headings, lists, code)
- [ ] Cross-references complete
- [ ] Table of contents accurate
- [ ] No hardcoded version numbers (or clearly marked)
- [ ] Timestamps recent
- [ ] Tone consistent throughout
```

---

## Documentation Templates

### API Endpoint Template

Use this for consistent API documentation:

```markdown
## [METHOD] [ENDPOINT]

One-line description of what this endpoint does.

**Security**: [Authentication type]  
**Rate Limit**: [Requests per minute]  
**Versions**: [API versions]  

### Request

[Bash example]

### Parameters

[Table with parameter details]

### Response

[JSON example of 200 OK response]

### Error Responses

[Table with error codes and solutions]

### Examples

[2-3 real-world usage examples]

### See Also

[Links to related endpoints or concepts]
```

### Service Architecture Template

Use this for consistent service documentation:

```markdown
## [Service Name]

One-line description of service purpose.

### Architecture

**Language**: [Language/Framework]  
**Database**: [Database type and name]  
**Cache**: [Cache type]  
**Message Queue**: [Queue type if applicable]  
**Port**: [Port number]  

### Responsibilities

- [What this service does]
- [Key business logic]
- [What it doesn't do]

### Data Model

[Description of main entities]

### APIs

[List of main endpoints or events]

### Dependencies

[Services this depends on]

### Deployment

[How to deploy this service]

### Monitoring

[Key metrics to monitor]
```

---

## Common Pitfalls to Avoid

| Pitfall | Problem | Solution |
|---------|---------|----------|
| **Outdated examples** | Code doesn't run | Run all examples periodically |
| **Ambiguous language** | Multiple interpretations | Use specific terminology, define terms |
| **Missing context** | AI can't understand snippet | Add brief explanation before code |
| **Inconsistent structure** | Hard to scan | Use templates, consistent headings |
| **No links** | Readers can't find related info | Link liberally between docs |
| **Giant walls of text** | Hard to read | Break into smaller sections with headers |
| **No tables of contents** | Can't find sections | Add TOC with linked headings |
| **Absolute paths/hostnames** | Docs break in different environments | Use placeholders like `<domain>` |
| **No version info** | Unclear if doc is current | Add version and update date |
| **Screenshots with text** | AI can't read; breaks on locale change | Use text + code samples |

---

## Tools & Extensions

### Writing

- **Markdown Linter**: yamllint, markdownlint
- **Grammar**: Grammarly, LanguageTool
- **Spell Check**: aspell, hunspell

### Formatting

- **Code Formatter**: Prettier (Markdown), Black (Python)
- **Table Generator**: [Markdown Table Generator](https://www.tablesgenerator.com/markdown_tables)
- **Diagram Tools**: Mermaid, Graphviz, Draw.io

### Publishing

- **Static Site Generators**: MkDocs, Sphinx, Hugo
- **Documentation Platforms**: Notion, Gitbook, ReadTheDocs
- **Version Control**: Keep docs in Git alongside code

### Validation

- **Link Checker**: linkchecker, markdown-link-check
- **Code Examples**: doctest, pygments
- **Spelling**: cspell, codespell

---

## Examples from Wildframe

### Good Examples to Follow

1. **[docs/DEVELOPMENT.md](DEVELOPMENT.md)**
   - Clear code conventions with Python and TypeScript examples
   - Step-by-step development setup
   - Structured troubleshooting section

2. **[docs/OPERATIONS.md](OPERATIONS.md)**
   - Copy-paste ready commands
   - Expected output shown
   - Incident response procedures with clear steps

3. **[docs/ARCHITECTURE.md](ARCHITECTURE.md)**
   - Service descriptions with responsibilities
   - Code patterns with real examples
   - Consistent terminology throughout

---

## Summary

### For Humans, Emphasize:
1. Clear purpose statement upfront
2. Progressive complexity (simple → advanced)
3. Real examples they can copy and run
4. Visual organization with headers and lists
5. "Why" behind recommendations
6. Navigation and cross-references

### For AI, Emphasize:
1. Structured and consistent formatting
2. Explicit context before code samples
3. Unambiguous, specific language
4. Metadata and semantic markup
5. Clear problem-solution mapping
6. Versioning and timestamps

### Make Both Happy:
- Use tables for structured data
- Add code examples with explanation
- Write in active voice
- Be specific and concrete
- Keep it DRY (Don't Repeat Yourself)
- Test everything you document

---

**Remember**: Good documentation is an investment. It pays dividends in reduced confusion, faster onboarding, and better AI assistance. Take the time to write it well.

Last Updated: May 27, 2026
