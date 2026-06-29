# 📚 Wildframe Platform Documentation

Welcome to the Wildframe documentation! This is your complete reference for the platform.

## 🚀 Getting Started

### Quick Links
- **[Quick Start Guide](QUICKSTART.md)** - Set up and run the platform in minutes
- **[How to Run Tests](HOW_TO_RUN_TESTS.md)** - Testing your code
- **[Testing Guide](TEST_GUIDE.md)** - Comprehensive testing documentation

## 📖 Documentation by Topic

### Architecture & Design
- **[System Architecture](ARCHITECTURE.md)** - High-level system design and components
- **[Service Architecture Pattern](SERVICE_ARCHITECTURE_PATTERN.md)** - Design patterns used across services
- **[Database Schema](DATABASE_SCHEMA.md)** - Complete database design

### Development
- **[Contributing Guide](CONTRIBUTING.md)** - Code conventions and development workflow
- **[API Documentation](API_DOCUMENTATION.md)** - Complete API reference
- **[Frontend Architecture](FRONTEND_ARCHITECTURE.md)** - React/Next.js structure

### Operations
- **[Deployment Guide](DEPLOYMENT_GUIDE.md)** - How to deploy to production
- **[Operations Guide](OPERATIONS_GUIDE.md)** - Running and maintaining in production
- **[Monitoring & Observability](MONITORING.md)** - Metrics, logs, and tracing

### Reference
- **[Glossary](GLOSSARY.md)** - Technical terminology
- **[What's Included](WHATS_INCLUDED.md)** - Feature inventory

## 🎯 Common Tasks

### For Developers
1. Read: [Quick Start Guide](QUICKSTART.md)
2. Setup: Follow the one-time prerequisites
3. Develop: Create a feature branch and edit code
4. Test: Run tests locally with [How to Run Tests](HOW_TO_RUN_TESTS.md)
5. Commit: Push to GitHub and create PR

### For DevOps/SRE
1. Read: [Deployment Guide](DEPLOYMENT_GUIDE.md)
2. Setup: Deploy to AWS/Kubernetes
3. Monitor: Review [Monitoring Guide](MONITORING.md)
4. Maintain: Follow [Operations Guide](OPERATIONS_GUIDE.md)

### For QA/Testing
1. Read: [Testing Guide](TEST_GUIDE.md)
2. Setup: Start services with Docker Compose
3. Test: Run manual and automated tests
4. Report: Document findings in issues

## 📊 Platform Overview

```
Wildframe OTT Platform
├── 12 Microservices
├── PostgreSQL (Multi-database)
├── Redis (Caching & Sessions)
├── Kafka (Event Streaming)
├── Elasticsearch (Full-text Search)
└── Observability Stack (Prometheus, Grafana, Jaeger, Loki)
```

## 🔗 External Resources

- **FastAPI**: https://fastapi.tiangolo.com
- **SQLAlchemy**: https://sqlalchemy.org
- **PostgreSQL**: https://www.postgresql.org
- **Docker**: https://www.docker.com
- **Kubernetes**: https://kubernetes.io
- **Next.js**: https://nextjs.org

## 📞 Support

For issues or questions:
1. Check relevant documentation section above
2. Search GitHub issues
3. Review service logs: `docker-compose logs -f <service>`
4. Check metrics: http://localhost:9090
5. Check traces: http://localhost:16686

## 🆕 What's New

**Last Updated**: June 4, 2026

### Recent Changes
- ✅ All 12 microservices scaffolded
- ✅ Docker Compose environment configured
- ✅ 70+ tests implemented
- ✅ CI/CD pipeline set up
- ✅ Monitoring dashboards configured
- ✅ **Complete documentation suite created** (19 files, 200+ KB)
  - API_DOCUMENTATION.md - Complete API reference with examples
  - DEPLOYMENT_GUIDE.md - Production deployment procedures
  - MONITORING.md - Metrics, logging, tracing, and alerting
  - GLOSSARY.md - Technical terminology (80+ terms)
  - ARCHITECTURE.md - System design and patterns
  - QUICKSTART.md - 10-minute local setup
  - TEST_GUIDE.md - Testing playbook
  - SERVICE_ARCHITECTURE_PATTERN.md - Per-service layout
  - FRONTEND_ARCHITECTURE.md - Frontend structure
  - WHATS_INCLUDED.md - Feature inventory

### Documentation Coverage

**Total**: 19 comprehensive documentation files covering:
- ✅ System architecture and design patterns
- ✅ API reference with all endpoints
- ✅ Database schema and relationships
- ✅ Development guidelines and conventions
- ✅ Testing strategies and examples
- ✅ Deployment to production
- ✅ Operations and monitoring
- ✅ Contributing guidelines
- ✅ Technical glossary
- ✅ Per-service architecture pattern
- ✅ Frontend architecture and conventions
- ✅ Quick-start, testing, and feature inventory

---

**Next Step**: Start with [QUICKSTART.md](QUICKSTART.md) to get up and running!
