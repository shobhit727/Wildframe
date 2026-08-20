# Timeout Ordering for Wildframe Platform

This document defines the timeout hierarchy across the request path to ensure correct ordering: upstream timeouts must be shorter than downstream timeouts to prevent orphaned work and unnecessary retries.

## Timeout Hierarchy (Outer → Inner)

| Layer | Timeout | Source | Purpose |
|-------|---------|--------|---------|
| **CloudFront** | 30s (default) | Viewer protocol | Client-facing edge timeout |
| **API Gateway / ALB** | 30s | `idle_timeout` | Load balancer connection idle |
| **Gateway Service (nginx/envoy)** | 25s | `proxy_read_timeout` | Gateway upstream timeout |
| **Service Mesh / mTLS** | 20s | sidecar timeout | Internal service-to-service |
| **Application Service** | 15s | code-level context deadline | Business logic timeout |
| **Database** | 10s | `statement_timeout` (PG) | Query execution limit |
| **Redis** | 5s | client timeout | Cache operation limit |

## Ordering Rules

1. **Each layer timeout < downstream layer timeout** — prevents outer layer killing inner work prematurely.
2. **Grace period**: Each layer should have ~5s headroom above the next inner layer.
3. **Retries**: Only retry at layers where idempotency is guaranteed (GET, idempotent POST). Downstream retries + upstream timeout = double work.
4. **Circuit breakers**: Preferred over aggressive timeouts for cascading failures.

## Terraform-Enforced Values

- **RDS**: `statement_timeout = 30s`, `lock_timeout = 10s`, `idle_in_transaction_session_timeout = 60s` (see `aws_rds_cluster_parameter_group.postgres`)
- **Gateway**: `proxy_read_timeout = 25s` (helm values)
- **Services**: `context.WithTimeout(ctx, 15*time.Second)` for request handlers

## Validation

CI should verify:
- `statement_timeout < gateway_timeout < alb_timeout < cloudfront_timeout`
- No service has timeout >= its caller's timeout
- All timeouts have explicit values (no defaults)

## References

- Issue #423: Proxy timeout ordering needs verification
- Issue #429/#430: Database statement/lock timeouts enforcement