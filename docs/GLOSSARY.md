# 📖 Glossary

**Version**: 1.0.0  
**Last Updated**: May 28, 2026  
**Stability**: Reference

## A

**Access Token**
A short-lived JWT token used to authenticate API requests. Typically valid for 1 hour.
*See also: [Refresh Token](#refresh-token), [JWT](#jwt)*

**ADR** (Architecture Decision Record)
A document that records a significant architectural decision made for Wildframe.
*Example: "ADR-001: Use microservices architecture instead of monolith"*

**API Gateway**
A service that routes HTTP requests to appropriate backend services. Handles authentication, rate limiting, and request validation.
*See also: [Routing](#routing)*

**Asyncio**
Python's built-in asynchronous I/O library used for concurrent request handling in FastAPI services.
*Usage: Enables handling thousands of concurrent connections efficiently*

**Authentication**
The process of verifying a user's identity using credentials (email and password).
*See also: [Authorization](#authorization), [JWT](#jwt)*

**Authorization**
The process of determining what resources a user is permitted to access.
*Example: Premium users can access 4K video, free users limited to 720p*

---

## B

**Backup**
A copy of data created for recovery purposes. Wildframe maintains daily backups with 30-day retention.

**Billing Service**
Microservice responsible for subscription management, payment processing, and invoice generation.
*Port: 8005*

**Bitrate**
The amount of video data transmitted per second, measured in Mbps. Higher bitrate = better quality.
*Examples: 480p (2 Mbps), 1080p (5 Mbps), 4K (15 Mbps)*

**Bucket** (S3)
A container in Amazon S3 used to store media files, thumbnails, and other objects.
*Example: `wildframe-media-us-east-1` bucket stores all video files*

---

## C

**Cache**
Fast, temporary storage of frequently accessed data. Wildframe uses Redis for caching.
*Benefits: Reduces database load, faster response times*

**CDN** (Content Delivery Network)
A network of geographically distributed servers that delivers content with low latency.
*Example: CloudFront distributes video segments globally*

**CI/CD** (Continuous Integration/Continuous Deployment)
Automated pipeline for testing code and deploying to production.
*Wildframe uses GitHub Actions for CI/CD*

**Connection Pool**
A set of database connections kept open and reused to avoid connection overhead.
*Typical size: 10-20 connections per service*

**Container**
A lightweight package containing application code, dependencies, and runtime.
*See also: [Docker](#docker), [Kubernetes](#kubernetes)*

**Content Service**
Microservice managing movie/show catalog, metadata, and search indexing.
*Port: 8002*

**CRUD** (Create, Read, Update, Delete)
Standard database operations.
*REST methods: POST (create), GET (read), PUT (update), DELETE (delete)*

---

## D

**Dashboard**
A Grafana visualization showing metrics, charts, and alerts in real-time.
*See also: [Grafana](#grafana)*

**Database-per-Service**
An architecture pattern where each microservice owns its own database.
*Advantage: Independent scaling, flexible schema changes*

**Deployment**
The process of releasing code to production environment.
*See also: [Rollout](#rollout), [Rollback](#rollback)*

**Docker**
A containerization platform that packages applications with dependencies.
*Wildframe uses Docker for local development and production*

**Domain**
In event-driven architecture, a domain represents a business concept (e.g., "User", "Content", "Payment").

---

## E

**Elasticsearch**
A distributed search engine used for full-text search across Wildframe's content catalog.
*Used by: Search Service*

**Endpoint**
A specific URL path in an API that performs an action.
*Example: `POST /auth/login` is an endpoint that authenticates users*

**Error Budget**
The allowed amount of downtime/errors within an SLA period.
*Example: 99.9% SLA allows 43.2 minutes of downtime per month*

**Event**
A notification that something happened (e.g., "user.registered", "video.uploaded").
*Transport: Kafka topics*

**Event-Driven**
An architecture where services communicate via events rather than direct API calls.
*Advantages: Loose coupling, asynchronous communication*

---

## F

**FastAPI**
A modern Python web framework for building async APIs with automatic documentation.
*Used by: All 12 microservices*

**Federation**
(In future phases) The ability for multiple Wildframe instances to sync and interoperate.

**Filter**
A query parameter that narrows results based on criteria.
*Example: `GET /movies?genre=action` filters movies to show only action films*

---

## G

**Gateway** 
*See: [API Gateway](#api-gateway)*

**Grafana**
A visualization and monitoring platform used to create dashboards and alerts.
*Access: `https://localhost:3000`*

**GraphQL**
(Potential future addition) A query language for APIs that allows clients to request specific fields.

---

## H

**Health Check**
A periodic request to verify a service is running and responsive.
*Endpoint: `GET /health`*
*Kubernetes uses health checks for pod management*

**HPA** (Horizontal Pod Autoscaler)
Kubernetes feature that automatically scales pod replicas based on metrics.
*Example: Scale from 3 to 10 replicas when CPU > 70%*

**HTTP Status Code**
A three-digit code indicating the result of an HTTP request.
*Categories: 2xx (success), 4xx (client error), 5xx (server error)*

---

## I

**Index** (Database)
A database structure that speeds up queries on specific columns.
*Example: Index on `user.email` makes login queries fast*

**Index** (Elasticsearch)
A collection of documents in Elasticsearch that can be searched.
*Example: The "movies" index contains all movie metadata*

**Idempotent**
An operation that produces the same result regardless of how many times it's called.
*Example: Withdrawing $100 twice is NOT idempotent; it should only happen once*

---

## J

**Jaeger**
A distributed tracing system that tracks requests across multiple services.
*Used for: Identifying bottlenecks, debugging failures*

**JWT** (JSON Web Token)
An encoded token containing user identity and permissions.
*Format: `header.payload.signature`*
*Advantage: Stateless authentication, no session storage needed*

---

## K

**Kafka**
A distributed event streaming platform used for asynchronous communication.
*Replaces: Direct REST API calls between services for events*
*Benefits: Decoupling, high throughput, failure resilience*

**Kubernetes (K8s)**
Container orchestration platform for deploying and managing containerized applications.
*Wildframe uses K8s for production deployment*
*Key concepts: Pods, Deployments, Services, StatefulSets*

---

## L

**Loki**
A log aggregation system that indexes logs for easy searching and analysis.
*Stores logs from: All services via Promtail*
*Query language: LogQL*

**Load Balancer**
A service that distributes traffic across multiple replicas of a service.
*In Kubernetes: Handled by Service resources*

**Latency**
The time delay between requesting data and receiving it.
*Measurement: Milliseconds (ms)*
*SLO: P95 latency < 500ms*

---

## M

**Manifest** (Kubernetes)
A YAML file describing Kubernetes resources (Deployments, Services, ConfigMaps, etc.).
*Location: `infrastructure/kubernetes/`*

**Manifest** (Streaming)
A file describing video segments and bitrate options for adaptive streaming.
*Format: HLS (.m3u8) or DASH (.mpd)*

**Microservice**
A small, independent service that handles one business capability.
*Example: Auth Service handles only authentication*
*Advantage: Independent deployment, scaling, and technology stack*

**Migration** (Database)
A version-controlled script that modifies database schema.
*Tool: Alembic (Python)*
*Example: Add new column, create index, etc.*

**Mutation**
In GraphQL, an operation that modifies data (vs. Query for reading data).

---

## N

**Namespace**
A Kubernetes logical partition that isolates resources.
*Wildframe resources are in: `wildframe` namespace*
*Common namespaces: `default`, `kube-system`, `monitoring`*

**Notification Service**
Microservice that sends emails, push notifications, SMS, and in-app notifications.
*Port: 8008*

---

## O

**Observability**
The ability to understand system state from external outputs (metrics, logs, traces).
*Components: Prometheus (metrics), Loki (logs), Jaeger (traces)*

**ORM** (Object-Relational Mapping)
A tool that maps database tables to Python objects.
*Wildframe uses: SQLAlchemy 2.0*
*Benefit: Write database queries in Python instead of SQL*

**OTT** (Over-The-Top)
A service delivering content over the internet, bypassing traditional TV distribution.
*Example: Netflix, Disney+, Wildframe are OTT services*

---

## P

**Pagination**
Dividing large result sets into smaller pages.
*Example: Fetch movies 20 at a time with `limit=20&offset=0`*

**Payload**
The actual data being transmitted in a request or response.
*Example: JSON body in `POST /auth/login` is the payload*

**Pod**
The smallest deployable unit in Kubernetes, typically containing one container.
*Metaphor: Like a container in Docker, but managed by Kubernetes*

**Prometheus**
A time-series database and monitoring system that collects metrics from applications.
*Scrape interval: Every 15 seconds by default*
*Query language: PromQL*

**Pull Request (PR)**
A request to merge code changes into the main branch.
*Process: Feature branch → PR → Review → Merge*

---

## Q

**QoS** (Quality of Service)
Guarantees about performance characteristics (latency, throughput, availability).
*Relates to: SLA, SLO, SLI*

**Query**
A request for data, typically with filters and sorting.
*Example: `GET /movies?genre=action&sort=-rating`*

---

## R

**RBAC** (Role-Based Access Control)
A system for controlling which users can perform which actions.
*Example: Admins can delete content, users cannot*

**Redis**
An in-memory data store used for caching, sessions, and rate limiting.
*Allocation: Each service gets 1 Redis database (0-10)*

**Refresh Token**
A long-lived token used to obtain new access tokens.
*Lifetime: 30 days*
*Why: Access tokens are short-lived for security*

**Repository** (Code)
A Git repository containing source code.
*Location: `services/`, `infrastructure/`, `apps/`*

**Repository** (Data Access Pattern)
A design pattern that abstracts database queries.
*Example: `UserRepository.find_by_email()` instead of raw SQL*

**REST** (Representational State Transfer)
An API design pattern using HTTP methods (GET, POST, PUT, DELETE) on resources.
*Alternative: GraphQL*

**Rollback**
Reverting to a previous version after a bad deployment.
*Command: `kubectl rollout undo deployment auth-service`*

**Rollout**
The gradual deployment of a new version to production.
*Strategy: Blue-green, canary, rolling*

**Route**
A mapping from a URL path to a handler function.
*Example: `POST /auth/login` routes to the `login()` function*

**Routing**
The process of directing requests to appropriate services.
*Handled by: API Gateway*

---

## S

**SLA** (Service Level Agreement)
A contract guaranteeing specific performance metrics.
*Example: "99.9% availability"*

**SLI** (Service Level Indicator)
A measurable metric that indicates how well SLO is being met.
*Example: Uptime percentage, error rate*

**SLO** (Service Level Objective)
A target for performance metrics, part of an SLA.
*Example: "99.9% uptime" is an SLO*

**Schema**
The structure of a database, defining tables, columns, and relationships.
*See: [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md)*

**Search Service**
Microservice providing full-text search on content catalog.
*Port: 8004*
*Technology: Elasticsearch*

**Segment** (Video)
A small video file (typically 6-10 seconds) that is combined for streaming.
*Advantage: Easy switching between bitrates, resuming interrupted playback*

**Session**
A period of interaction with the service, typically tied to an authenticated user.
*Storage: Redis*
*Lifetime: User configurable, typically 30 days*

**Soft Delete**
Marking data as deleted without physically removing it from the database.
*Implementation: `deleted_at` column set to current timestamp*
*Benefit: Preserves referential integrity, enables auditing*

**Span** (Tracing)
A unit of work in a distributed trace, representing a single operation.
*Example: "Database query" or "API call" is a span*
*Metadata: Duration, timestamp, status, error details*

**SQLAlchemy**
A Python ORM (Object-Relational Mapping) library for database access.
*Version: 2.0*
*Feature: Async support with SQLAlchemy 2.0*

**Stateless**
A service that doesn't store session state between requests.
*Advantage: Easy to scale horizontally*
*Wildframe: Mostly stateless; sessions stored in Redis*

**Streaming Service**
Microservice that handles video manifests, playback sessions, and watch position.
*Port: 8003*

---

## T

**Telemetry**
Automated collection of data from applications (metrics, logs, traces).
*Components: Prometheus, Loki, Jaeger*

**Terraform**
An Infrastructure-as-Code tool for provisioning cloud resources.
*Used for: AWS resource creation (RDS, ElastiCache, EKS)*

**Token Blacklist**
A list of tokens that have been logged out or revoked.
*Storage: Redis*
*Lifetime: 30 days (token's original lifetime)*

**Trace**
A complete request path through multiple services.
*Example: User login spans auth-service → user-service → database*
*Tool: Jaeger visualizes traces*

---

## U

**UUID** (Universally Unique Identifier)
A 128-bit identifier guaranteed to be globally unique.
*Format: `550e8400-e29b-41d4-a716-446655440000`*
*Wildframe uses UUID v4 for all primary keys*

**User Service**
Microservice managing user profiles, devices, sessions, and preferences.
*Port: 8001*

---

## V

**Validation**
Checking that input data meets requirements.
*Example: Email format, password strength, required fields*
*Tool: Pydantic in FastAPI*

**Version** (API)
Different versions of an API to maintain backward compatibility.
*Example: `/v1/movies`, `/v2/movies` with different response formats*

**Volume** (Kubernetes)
Persistent storage in Kubernetes for data that survives pod restarts.
*Types: EmptyDir, ConfigMap, Secret, PVC (PersistentVolumeClaim)*

---

## W

**Webhook**
A callback mechanism where an external system calls your API when something happens.
*Planned: Future addition for billing notifications, content updates*

**Watch History**
A record of content a user has started watching.
*Stored in: User Service database*
*Fields: Content ID, watch position, timestamp*

**Wildframe**
An open-source OTT (Over-The-Top) streaming platform for video content delivery.

---

## X

**X-Request-ID**
A unique identifier assigned to each request for tracking and debugging.
*Format: Appears in logs, trace spans, error responses*
*Generated by: API Gateway*

---

## Y

*No common terms starting with Y*

---

## Z

**Zipkin** 
A distributed tracing system (alternative to Jaeger).
*Wildframe uses: Jaeger instead*

**Zero-Trust Security**
A security model that doesn't trust any user or service by default.
*Implementation: All requests require authentication, even from internal services*

---

## See Also

- [API Documentation](API_DOCUMENTATION.md)
- [Architecture Guide](ARCHITECTURE.md)
- [Database Schema](DATABASE_SCHEMA.md)
- [Operations Guide](OPERATIONS.md)
