# COMPLIANCE-FOUNDATION Implementation Plan

## Overview
Build jurisdiction-aware compliance and policy configuration layer - the foundation for all 25 COMPLIANCE-* features.

## Architecture Decisions

1. **Shared SDK Package**: Create `packages/sdk/wildframe_compliance` with:
   - Jurisdiction enum and policy definitions
   - Base compliance settings mixin for services
   - Policy evaluation engine
   - Event schemas for policy changes

2. **Per-Service Integration**: Each service extends the base compliance settings

3. **Event-Driven Updates**: Kafka topic `compliance.policy.changed` for real-time policy updates

## Task List (Vertical Slices)

### Phase 1: Core Compliance SDK
- [ ] Task 1.1: Create `packages/sdk/wildframe_compliance` package structure
- [ ] Task 1.2: Define `Jurisdiction` enum (EU, US, IN, CA, AU, BR, JP, Global)
- [ ] Task 1.3: Define `CompliancePolicy` base model and jurisdiction-specific policies
- [ ] Task 1.4: Create `ComplianceSettingsMixin` for service settings
- [ ] Task 1.5: Implement policy evaluation engine (`evaluate_policy`)
- [ ] Task 1.6: Add unit tests for policy engine

### Phase 2: Policy Definitions
- [ ] Task 2.1: Define GDPR policy (data subject rights, DPIA, DPO, lawful basis)
- [ ] Task 2.2: Define US state privacy policy (CCPA/CPRA, VCDPA, CPA, CTDPA, etc.)
- [ ] Task 2.3: Define India DPDP policy (consent, child data, grievance, significant data fiduciary)
- [ ] Task 2.4: Define EU AVMS policy (content regulation, accessibility, advertising)
- [ ] Task 2.5: Define global baseline policy (security, retention, audit)
- [ ] Task 2.6: Add tests for policy definitions

### Phase 3: Service Integration
- [ ] Task 3.1: Update api-gateway settings with compliance mixin
- [ ] Task 3.2: Update auth-service settings with compliance mixin
- [ ] Task 3.3: Update user-service settings with compliance mixin
- [ ] Task 3.4: Update content-service settings with compliance mixin
- [ ] Task 3.5: Update streaming-service settings with compliance mixin
- [ ] Task 3.6: Update search-service settings with compliance mixin
- [ ] Task 3.7: Update recommendation-service settings with compliance mixin
- [ ] Task 3.8: Update billing-service settings with compliance mixin
- [ ] Task 3.9: Update analytics-service settings with compliance mixin
- [ ] Task 3.10: Update notification-service settings with compliance mixin
- [ ] Task 3.11: Update admin-service settings with compliance mixin
- [ ] Task 3.12: Update media-pipeline settings with compliance mixin
- [ ] Task 3.13: Update creators-service settings with compliance mixin
- [ ] Task 3.14: Update moderation-service settings with compliance mixin
- [ ] Task 3.15: Update uploads-service settings with compliance mixin

### Phase 4: Event System
- [ ] Task 4.1: Define `CompliancePolicyChanged` event schema
- [ ] Task 4.2: Add Kafka producer for policy changes in admin-service
- [ ] Task 4.3: Add Kafka consumer in each service for policy updates
- [ ] Task 4.4: Add integration tests for event propagation

### Phase 5: Observability
- [ ] Task 5.1: Add compliance metrics (policy evaluations, violations)
- [ ] Task 5.2: Add structured logging for compliance decisions
- [ ] Task 5.3: Add health check for compliance configuration

## Dependencies
- `pydantic-settings` (already in all services)
- `wildframe-events` (already in services for Kafka)
- `wildframe-observability` (already in services)

## Testing Strategy
- Unit tests for policy engine (target: 90% coverage)
- Integration tests for service settings loading
- Contract tests for event schemas
- E2E test for policy change propagation

## Gate 1: Plan Approval
Present this plan for approval before implementation begins.