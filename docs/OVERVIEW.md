# Wildframe Documentation Overview

Quick reference guide to what's included in the Wildframe platform and current project status.

## 📚 Documentation Structure

We've consolidated 10 separate documentation files into 4 main guides for easier navigation:

| Document | Coverage |
|----------|----------|
| **OVERVIEW.md** | Quick start, reading order, common tasks reference |
| **ARCHITECTURE.md** | System design, microservice patterns, frontend architecture, database schema |
| **DEVELOPMENT.md** | Setup, code conventions, implementation roadmap, technical glossary |
| **OPERATIONS.md** | Deployment, monitoring, daily operations, incident response, troubleshooting |
| **DOCUMENTATION_GUIDE.md** | Writing docs for humans AND AI, templates, best practices |

---

## ✅ What's Completed & Production-Ready

### Architecture & Documentation
- **ARCHITECTURE.md** - Complete system design with data flows, security model, patterns
- **SERVICE_ARCHITECTURE_PATTERN.md** - Template for all services (clean architecture layers, dependency injection, repositories)
- **FRONTEND_ARCHITECTURE.md** - Next.js patterns, component hierarchy, state management, video player design
- **database_schema.md** - Complete database design with indexing and partitioning strategies

### Backend Infrastructure
- **Docker Compose** - 14 services fully configured (PostgreSQL, Redis, Kafka, Elasticsearch, monitoring stack)
- **Kubernetes Manifests** - Auth Service complete K8s manifest with deployments, services, HPA, RBAC, network policies
- **Terraform Infrastructure** - EKS, RDS, ElastiCache, S3, CloudFront, VPC configuration
- **GitHub Actions CI/CD** - Automated testing, building, and deployment pipeline

### Auth Service (Complete Core)
- Settings with Pydantic validation
- Async SQLAlchemy database manager
- JSON structured logging with correlation IDs
- OpenTelemetry Jaeger tracing
- Security utilities (password hashing, JWT, rate limiting)
- API routes scaffolded (ready for implementation)

### Project Status
- **Overall Progress**: 35% Complete
- **Foundation Phase**: ✅ Complete
- **Core Services**: 🔄 In Progress (Auth complete, others scaffolded)
- **Estimated Timeline**: 16 weeks (4 months) for full implementation

---

## 🎯 Quick Start

### 1. Read the Docs in This Order

**First Time (5-10 min)**
- [README.md](../README.md) - Project vision and tech stack
- This document - Overview of what's included

**Understanding the Design (20 min)**
- [ARCHITECTURE.md](ARCHITECTURE.md#system-architecture) - System overview
- [ARCHITECTURE.md](ARCHITECTURE.md#service-architecture-pattern) - Service patterns

**Development (30 min)**
- [DEVELOPMENT.md](DEVELOPMENT.md#development-setup) - Setting up your environment
- [DEVELOPMENT.md](DEVELOPMENT.md#code-conventions) - Code standards

**Deployment (15 min)**
- [OPERATIONS.md](OPERATIONS.md#pre-deployment-checklist) - Pre-deployment
- [OPERATIONS.md](OPERATIONS.md#backend-deployment) - Deployment steps

**Contributing to Docs (Optional)**
- [DOCUMENTATION_GUIDE.md](DOCUMENTATION_GUIDE.md) - Writing docs for humans AND AI

### 2. Set Up Development Environment

```bash
# Clone and setup
git clone https://github.com/wildframe/platform.git
cd platform

# Backend setup
pip install -r requirements.txt
docker-compose -f deployments/docker-compose.dev.yml up -d

# Frontend setup
npm install
npm run dev --workspace=apps/web
```

See [DEVELOPMENT.md](DEVELOPMENT.md#development-setup) for full instructions.

### 3. Run the Platform

```bash
# Start services
python -m uvicorn services/auth-service/app/main:app --reload --port 8001

# Access
- Frontend: http://localhost:3000
- API: http://localhost:8001
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (admin/admin)
```

---

## 📊 Architecture at a Glance

### 13 Microservices
```
API Gateway → Auth Service → User Service → Content Service
           ↓
       Streaming Service ↔ Search Service ↔ Recommendation Engine
           ↓
  Billing Service ↔ Analytics Service ↔ Admin Service
           ↓
Notification Service ↔ Media Pipeline
```

### Technology Stack

**Backend**: FastAPI + SQLAlchemy 2.0 (async)  
**Database**: PostgreSQL (database-per-service)  
**Cache**: Redis 7.0+  
**Events**: Kafka 3.0+  
**Search**: Elasticsearch 8.0+  
**Frontend**: Next.js 15 + React 18 + TypeScript  
**Infrastructure**: Kubernetes + Terraform + Docker  
**Observability**: Prometheus + Grafana + Loki + Jaeger  

---

## 🔄 Implementation Roadmap

### ✅ Completed (Phase 1)
- Infrastructure as Code (Terraform)
- Docker Compose development environment
- Kubernetes manifests and RBAC
- CI/CD pipeline (GitHub Actions)
- Monitoring stack setup
- Auth Service core implementation

### 🔄 In Progress (Phase 2)
- Auth Service endpoints (register, login, refresh, logout)
- User Service
- Content Service
- Streaming Service
- Search Service
- API Gateway

### 📋 Upcoming (Phase 3+)
- Billing, Recommendations, Analytics services
- Frontend application
- Media transcoding pipeline
- Full test coverage
- Security audit

See [DEVELOPMENT.md](DEVELOPMENT.md#implementation-roadmap) for detailed timeline.

---

## 🛠 Common Tasks

### Development
- See [DEVELOPMENT.md](DEVELOPMENT.md#development-workflow) for workflow and conventions
- See [DEVELOPMENT.md](DEVELOPMENT.md#testing) for testing procedures

### Deployment
- See [OPERATIONS.md](OPERATIONS.md#backend-deployment) for deployment steps
- See [OPERATIONS.md](OPERATIONS.md#rollback-procedures) for rollback procedures

### Operations
- See [OPERATIONS.md](OPERATIONS.md#daily-operations) for operational tasks
- See [OPERATIONS.md](OPERATIONS.md#incident-response) for incident procedures

### Troubleshooting
- See [OPERATIONS.md](OPERATIONS.md#troubleshooting) for common issues

---

## 📖 Reference Materials

### For Understanding Concepts
- [DEVELOPMENT.md](DEVELOPMENT.md#technical-glossary) - Technical glossary
- [ARCHITECTURE.md](ARCHITECTURE.md#key-concepts) - Key architectural concepts

### For Implementation Details
- [ARCHITECTURE.md](ARCHITECTURE.md#service-architecture-pattern) - Service patterns
- [DEVELOPMENT.md](DEVELOPMENT.md#code-conventions) - Code conventions

### For Operations
- [OPERATIONS.md](OPERATIONS.md) - All operational procedures
- [ARCHITECTURE.md](ARCHITECTURE.md#database-schema) - Database design

### For Documentation Contributors
- [DOCUMENTATION_GUIDE.md](DOCUMENTATION_GUIDE.md) - How to write docs for humans and AI

---

## 📁 Directory Structure

```
wildframe/
├── docs/                          # 📚 You are here
│   ├── OVERVIEW.md               # This file
│   ├── ARCHITECTURE.md           # System design & patterns
│   ├── DEVELOPMENT.md            # Setup & conventions
│   ├── OPERATIONS.md             # Deployment & ops
│   └── DOCUMENTATION_GUIDE.md    # Writing docs for humans & AI
├── apps/web/                     # Next.js frontend
├── services/                     # 13 microservices
│   ├── auth-service/            # ✅ Core complete
│   ├── user-service/
│   ├── content-service/
│   └── ... (10 more)
├── infrastructure/
│   ├── kubernetes/              # K8s manifests
│   ├── terraform/               # IaC for AWS
│   └── docker/                  # Docker configs
├── deployments/
│   └── docker-compose.dev.yml   # Local dev setup
└── README.md                     # Project overview
```

---

## 🚀 Getting Help

### Quick Links
- **Issues**: [GitHub Issues](https://github.com/wildframe/platform/issues)
- **Discussions**: [GitHub Discussions](https://github.com/wildframe/platform/discussions)
- **Slack**: #engineering channel

### Documentation Search
Use keyboard shortcut `Cmd/Ctrl + F` to search within any document.

---

## 🤝 Contributing

1. Read [DEVELOPMENT.md](DEVELOPMENT.md#development-workflow) for workflow
2. Follow [code conventions](DEVELOPMENT.md#code-conventions)
3. Add tests and documentation
4. Create pull request with clear description

---

## 📅 Project Timeline

| Phase | Duration | Status |
|-------|----------|--------|
| Phase 1: Foundation | 2 weeks | ✅ Complete |
| Phase 2: Core Services | 4 weeks | 🔄 In Progress |
| Phase 3: Business Logic | 4 weeks | 📋 Planned |
| Phase 4: Media Pipeline | 2 weeks | 📋 Planned |
| Phase 5: Frontend | 2 weeks | 📋 Planned |
| Phase 6: Ops & Deploy | 2 weeks | 📋 Planned |

**Current**: May 26, 2026 | **Next Review**: June 2, 2026

---

## 📝 Last Updated
May 26, 2026

For detailed information on any topic, refer to the specific documentation files listed above.
