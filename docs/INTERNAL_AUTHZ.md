# Caller-Scoped Internal Authorization

This document defines the authorization model for service-to-service communication within Wildframe. Each internal request must identify both the **caller** (source service) and the **allowed operations** (scopes), preventing lateral movement from a single compromised service.

## Threat Model

A generic "internal token" shared across all services allows any compromised service to call any other service with full privileges. We require **caller identity + scope authorization** per request.

## Token Format

Internal service tokens are JWTs with the following claims:

```json
{
  "iss": "wildframe-auth",
  "sub": "service:billing",
  "aud": ["wildframe-api"],
  "scope": ["billing:read", "billing:write", "subscription:manage"],
  "jti": "<unique-request-id>",
  "iat": 1234567890,
  "exp": 1234567950
}
```

| Claim | Purpose |
|-------|---------|
| `sub` | Caller identity: `service:<name>` |
| `aud` | Intended audience (API gateway / target service) |
| `scope` | List of allowed operations (fine-grained) |
| `jti` | Request ID for replay protection |

## Enforcement Points

1. **API Gateway** — Validates JWT signature, expiry, `aud` match. Forwards `X-Caller-Service` and `X-Caller-Scopes` headers.
2. **Target Service** — Middleware checks:
   - `X-Caller-Service` matches expected caller (allowlist per endpoint)
   - Required scope present in `X-Caller-Scopes`
3. **Service Mesh (optional)** — mTLS + SPIFFE identity for zero-trust network layer.

## Scope Naming Convention

```
<resource>:<action>
```

Examples:
- `content:read` — read content metadata
- `content:write` — create/update content
- `billing:subscription:manage` — manage subscriptions
- `analytics:events:ingest` — ingest analytics events

## Per-Endpoint Authorization Matrix

| Endpoint | Allowed Callers | Required Scope |
|----------|----------------|----------------|
| `POST /api/v1/content` | `service:creator-portal` | `content:write` |
| `GET /api/v1/content/:id` | `service:web`, `service:mobile` | `content:read` |
| `POST /api/v1/billing/subscriptions` | `service:payments` | `billing:subscription:manage` |
| `POST /internal/events` | `service:*` (any) | `analytics:events:ingest` |

## Implementation Checklist

- [ ] Auth service issues scoped tokens via `/internal/token` (client credentials grant)
- [ ] Gateway validates and propagates caller/scopes headers
- [ ] Each service implements scope middleware
- [ ] Allowlist config per endpoint (code or config-driven)
- [ ] Audit log: caller, target, scope, decision (allow/deny)
- [ ] Rotation: short TTL (5min), refresh via service account

## References

- Issue #445: Internal service authorization needs endpoint-level scopes
- Issue #597: Auth service token format and validation