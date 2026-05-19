# Wildframe Project File Structure

Complete directory and file organization of the Wildframe platform.

```
wildframe/
│
├── 📄 README.md                          # Project overview
├── 📄 AGENTS.md                          # Agent instructions  
├── 📄 ARCHITECTURE.md                    # High-level architecture
├── 📄 PLATFORM_ARCHITECTURE.md          # Detailed system design
├── 📄 DELIVERY_SUMMARY.md               # What's been delivered
├── 📄 pyproject.toml                    # Poetry monorepo config
├── 📄 package.json                      # NPM monorepo config
├── 📄 nx.json                           # Nx configuration
├── 📄 .gitignore                        # Git ignore patterns
│
├── 📁 apps/                             # Frontend applications
│   └── web/                             # Next.js web application
│       ├── 📄 package.json              # Dependencies
│       ├── 📄 tsconfig.json             # TypeScript config
│       ├── 📄 next.config.ts            # Next.js config
│       ├── 📄 tailwind.config.ts        # Tailwind CSS config
│       ├── 📄 .eslintrc.js              # ESLint config
│       ├── 📄 .prettierrc.json          # Prettier config
│       ├── 📄 README.md                 # Frontend documentation
│       ├── public/                      # Static assets
│       └── src/                         # Source code
│           ├── app/                     # Next.js pages (App Router)
│           │   ├── layout.tsx
│           │   ├── page.tsx
│           │   ├── auth/
│           │   ├── browse/
│           │   ├── watch/
│           │   ├── profile/
│           │   └── admin/
│           ├── components/              # React components
│           │   ├── common/
│           │   ├── layout/
│           │   ├── player/
│           │   └── content/
│           ├── hooks/                   # Custom React hooks
│           ├── lib/                     # Utility functions
│           │   └── api/
│           ├── services/                # Business logic
│           ├── stores/                  # Zustand state management
│           ├── types/                   # TypeScript types/interfaces
│           ├── config/                  # Configuration
│           ├── constants/               # Constants and enums
│           └── styles/                  # Global styles
│
├── 📁 packages/                         # Shared libraries
│   ├── shared-types/                    # Shared TypeScript types
│   ├── shared-components/               # Shared UI components
│   └── shared-utils/                    # Shared utilities
│
├── 📁 services/                         # Microservices
│   ├── auth-service/                    # ✅ IMPLEMENTED (70%)
│   │   ├── 📄 pyproject.toml
│   │   ├── 📄 Dockerfile
│   │   ├── 📄 README.md
│   │   ├── app/
│   │   │   ├── __init__.py
│   │   │   ├── main.py                  # FastAPI app
│   │   │   ├── core/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── settings.py          # Configuration
│   │   │   │   ├── database.py          # SQLAlchemy setup
│   │   │   │   └── logging.py           # JSON logging
│   │   │   ├── models/
│   │   │   │   └── __init__.py          # SQLAlchemy models
│   │   │   ├── schemas/
│   │   │   │   └── __init__.py          # Pydantic schemas
│   │   │   ├── repositories/            # Data access layer
│   │   │   ├── services/                # Business logic
│   │   │   ├── api/
│   │   │   │   └── __init__.py          # Routes (scaffolded)
│   │   │   ├── middleware/              # Cross-cutting
│   │   │   ├── security/
│   │   │   │   └── __init__.py          # JWT, hashing, rate limit
│   │   │   ├── telemetry/
│   │   │   │   └── __init__.py          # OpenTelemetry
│   │   │   └── events/                  # Kafka events
│   │   ├── tests/
│   │   │   ├── conftest.py
│   │   │   ├── unit/
│   │   │   └── integration/
│   │   └── migrations/
│   │       ├── env.py
│   │       ├── script.py.mako
│   │       └── versions/
│   │
│   ├── user-service/                    # User management
│   │   ├── 📄 pyproject.toml
│   │   ├── 📄 Dockerfile
│   │   ├── app/ (scaffolded)
│   │   ├── tests/
│   │   └── migrations/
│   │
│   ├── content-service/                 # Content management
│   │   └── (structure as user-service)
│   │
│   ├── streaming-service/               # Video playback
│   │   └── (structure as user-service)
│   │
│   ├── search-service/                  # Elasticsearch integration
│   │   └── (structure as user-service)
│   │
│   ├── recommendations-service/         # ML recommendations
│   │   └── (structure as user-service)
│   │
│   ├── billing-service/                 # Subscriptions & payments
│   │   └── (structure as user-service)
│   │
│   ├── analytics-service/               # Event aggregation
│   │   └── (structure as user-service)
│   │
│   ├── notifications-service/           # Emails & push
│   │   └── (structure as user-service)
│   │
│   ├── admin-service/                   # Admin panel backend
│   │   └── (structure as user-service)
│   │
│   ├── media-pipeline/                  # Video transcoding
│   │   └── (structure as user-service)
│   │
│   ├── api-gateway/                     # Main API gateway
│   │   └── (structure as user-service)
│   │
│   └── admin-api/                       # Admin API
│       └── (structure as user-service)
│
├── 📁 netflix_backend/                  # Legacy Django app (deprecated)
│   ├── manage.py
│   ├── requirements.txt
│   ├── db.sqlite3
│   ├── netflix_backend/
│   │   ├── settings.py
│   │   ├── urls.py
│   │   ├── wsgi.py
│   │   └── asgi.py
│   └── [various Django apps]
│
├── 📁 deployments/                      # Deployment configurations
│   └── docker-compose.dev.yml           # Local development stack (14 services)
│
├── 📁 infrastructure/                   # Infrastructure as Code
│   ├── database/
│   │   └── init-databases.sql           # PostgreSQL initialization
│   ├── docker/
│   │   ├── Dockerfile.base              # Base image
│   │   └── Dockerfile.prod              # Production image
│   ├── kubernetes/
│   │   ├── auth-service.yaml            # K8s manifests (complete)
│   │   ├── user-service.yaml            # Template for others
│   │   ├── namespace.yaml
│   │   ├── rbac.yaml
│   │   ├── network-policies.yaml
│   │   ├── configmaps.yaml
│   │   └── secrets.yaml
│   └── terraform/
│       ├── main.tf                      # AWS infrastructure
│       ├── variables.tf                 # Variables
│       ├── outputs.tf
│       ├── vpc.tf
│       ├── eks.tf
│       ├── rds.tf
│       ├── elasticache.tf
│       ├── s3.tf
│       └── cloudfront.tf
│
├── 📁 docs/                             # Documentation
│   ├── 📄 INDEX.md                      # Documentation index
│   ├── 📄 WHATS_INCLUDED.md             # Deliverables summary
│   ├── 📄 PLATFORM_ARCHITECTURE.md      # System design
│   ├── 📄 SERVICE_ARCHITECTURE_PATTERN.md
│   ├── 📄 FRONTEND_ARCHITECTURE.md
│   ├── 📄 IMPLEMENTATION_CHECKLIST.md
│   ├── 📄 DEPLOYMENT_GUIDE.md
│   ├── 📄 OPERATIONS_GUIDE.md
│   ├── 📄 CONTRIBUTING.md
│   ├── 📄 database_schema.md
│   ├── 📄 GLOSSARY.md
│   └── diagrams/
│       ├── system-architecture.drawio
│       ├── data-flow.drawio
│       └── event-flow.drawio
│
├── 📁 tools/                            # Development tools
│   ├── 📄 generate_service.py           # Service scaffolding
│   ├── 📄 seed_database.py              # Test data generation
│   ├── 📄 load_test.py                  # Performance testing
│   └── scripts/
│       ├── setup-dev.sh                 # Development setup
│       ├── setup-k8s.sh                 # Kubernetes setup
│       ├── deploy-staging.sh
│       └── deploy-prod.sh
│
├── 📁 scripts/                          # Utility scripts
│   ├── 📄 bootstrap.sh                  # Initial setup
│   ├── 📄 migrate.sh                    # Run migrations
│   ├── 📄 test.sh                       # Run tests
│   └── 📄 deploy.sh                     # Deploy to k8s
│
├── 📁 .github/                          # GitHub configuration
│   ├── workflows/
│   │   └── ci-cd.yml                    # GitHub Actions pipeline
│   ├── ISSUE_TEMPLATE/
│   └── PULL_REQUEST_TEMPLATE/
│
└── 📁 libs/                             # Legacy libraries (deprecated)
    ├── common/
    └── utils/
```

## 📊 File Statistics

```
Total Files:           150+

By Type:
├── Python Files      40+
├── TypeScript Files  30+
├── YAML Files        20+
├── Markdown Files    12+
├── Config Files      15+
├── SQL Files         5+
└── Other             28+

By Size:
├── Large (> 300 lines)    15
├── Medium (100-300)       40
├── Small (< 100)          95
```

## 🎯 Key File Locations

### Getting Started
- `README.md` - Start here
- `docs/INDEX.md` - Documentation navigation
- `DELIVERY_SUMMARY.md` - What's been delivered

### Architecture
- `PLATFORM_ARCHITECTURE.md` - System design
- `docs/SERVICE_ARCHITECTURE_PATTERN.md` - Service template
- `docs/FRONTEND_ARCHITECTURE.md` - Frontend design

### Backend
- `services/auth-service/` - Implemented service example
- `services/*/` - Other service scaffolding
- `infrastructure/` - Infrastructure setup

### Frontend
- `apps/web/` - Next.js application
- `apps/web/src/types/` - TypeScript types
- `apps/web/src/config/` - Configuration

### Infrastructure
- `deployments/docker-compose.dev.yml` - Local dev
- `infrastructure/kubernetes/` - K8s manifests
- `infrastructure/terraform/` - AWS infrastructure
- `.github/workflows/` - CI/CD pipeline

### Operations
- `docs/DEPLOYMENT_GUIDE.md` - Deployment
- `docs/OPERATIONS_GUIDE.md` - Day-to-day ops
- `docs/CONTRIBUTING.md` - Development standards

## 🔗 Cross-References

### For Backend Development
1. Start: `services/auth-service/` (reference implementation)
2. Pattern: `docs/SERVICE_ARCHITECTURE_PATTERN.md`
3. Database: `infrastructure/database/init-databases.sql`
4. Contribute: `docs/CONTRIBUTING.md`

### For Frontend Development
1. Start: `apps/web/`
2. Types: `apps/web/src/types/`
3. Pattern: `docs/FRONTEND_ARCHITECTURE.md`
4. Setup: `apps/web/README.md`

### For DevOps
1. Infrastructure: `infrastructure/terraform/`
2. Kubernetes: `infrastructure/kubernetes/`
3. Deployment: `docs/DEPLOYMENT_GUIDE.md`
4. Operations: `docs/OPERATIONS_GUIDE.md`

### For Product
1. Features: `PLATFORM_ARCHITECTURE.md`
2. Timeline: `docs/IMPLEMENTATION_CHECKLIST.md`
3. Progress: `DELIVERY_SUMMARY.md`

---

**All files are organized for maximum productivity and clarity. Each team member knows exactly where to look.**

Last Updated: 2026-05-12
