# Timeout Ordering for Wildframe Platform

> ## ⚠️ Partly aspirational — verified 2026-10-14
>
> The ordering *principle* below is correct and worth following. **Several of
> the specific numbers have no implementation behind them**, and the earlier
> revision of this file presented all of them as if they did.
>
> - **There is no nginx and no envoy in this repo.** `find` over
>   `infrastructure/` and `deployments/` returns neither, and the Helm chart
>   contains no `proxy_read_timeout`. The gateway is the FastAPI
>   `api-gateway` service; its timeouts are
>   `UPSTREAM_CONNECT_TIMEOUT` / `UPSTREAM_READ_TIMEOUT` /
>   `UPSTREAM_WRITE_TIMEOUT` / `UPSTREAM_POOL_TIMEOUT` in
>   `services/api-gateway/app/core/settings.py` (**5 / 30 / 30 / 5 seconds**),
>   not a reverse-proxy directive.
> - **There is no `context.WithTimeout(ctx, 15*time.Second)` in any service.**
>   `grep -rn WithTimeout services/` returns nothing. The only request-scoped
>   deadline that exists is `asyncio.wait_for(..., timeout=2.0)` around Redis
>   pings in the gateway.
> - **The RDS parameters below ARE real**, and the document previously had
>   `statement_timeout` at 30s in the table while Terraform sets 30000 ms —
>   those agree; `lock_timeout` 10s and `idle_in_transaction_session_timeout`
>   60s also agree. That part is verified against
>   `infrastructure/terraform/main.tf`.
> - **There is no CloudFront or ALB in this repo's Terraform**, so those rows
>   are environment context, not configuration you can change here.
> - **The "Validation" section describes a CI check that does not exist.**
>   Nothing in `.github/workflows/ci-cd.yml` asserts timeout ordering.
>
> Treat the table as a design target. The enforced values are the RDS
> parameters and the gateway settings named above.

This document defines the timeout hierarchy across the request path to ensure correct ordering: upstream timeouts must be shorter than downstream timeouts to prevent orphaned work and unnecessary retries.

## Timeout Hierarchy (Outer → Inner)

| Layer | Timeout | Source | Purpose |
|-------|---------|--------|---------|
| **CloudFront** | 30s (default) | Viewer protocol | Client-facing edge timeout |
| **API Gateway / ALB** | 30s | `idle_timeout` | Load balancer connection idle |
| **Gateway (api-gateway, FastAPI)** | 30s read / 5s connect | `UPSTREAM_READ_TIMEOUT`, `UPSTREAM_CONNECT_TIMEOUT` in `app/core/settings.py` | **Real.** There is no nginx/envoy in front of it |
| **Service Mesh / mTLS** | 20s | sidecar timeout | Internal service-to-service |
| **Application Service** | ⚠️ **no such deadline exists** | — | Business logic timeout — **not implemented anywhere** |
| **Database** | 10s | `statement_timeout` (PG) | Query execution limit |
| **Redis** | 5s | client timeout | Cache operation limit |

## Ordering Rules

1. **Each layer timeout < downstream layer timeout** — prevents outer layer killing inner work prematurely.
2. **Grace period**: Each layer should have ~5s headroom above the next inner layer.
3. **Retries**: Only retry at layers where idempotency is guaranteed (GET, idempotent POST). Downstream retries + upstream timeout = double work.
4. **Circuit breakers**: Preferred over aggressive timeouts for cascading failures.

## Terraform-Enforced Values

- **RDS**: `statement_timeout = 30s`, `lock_timeout = 10s`, `idle_in_transaction_session_timeout = 60s` (see `aws_rds_cluster_parameter_group.postgres`)
- **Gateway**: `UPSTREAM_CONNECT_TIMEOUT=5.0`, `UPSTREAM_READ_TIMEOUT=30.0`,
  `UPSTREAM_WRITE_TIMEOUT=30.0`, `UPSTREAM_POOL_TIMEOUT=5.0`
  (`services/api-gateway/app/core/settings.py`). ⚠️ The
  `proxy_read_timeout = 25s` in the previous revision of this file **does not
  exist** in the Helm chart or anywhere else.
- **Services**: ⚠️ **no application-level request deadline is implemented.**
  The `context.WithTimeout(ctx, 15*time.Second)` in the previous revision was
  aspirational; `grep -rn WithTimeout services/` returns nothing.

## Validation

⚠️ **No CI check implements this today.** The items below are the intended
check, not a description of one.

The check should verify:
- `statement_timeout < gateway_timeout < alb_timeout < cloudfront_timeout`
- No service has timeout >= its caller's timeout
- All timeouts have explicit values (no defaults)

## References

- Issue #423: Proxy timeout ordering needs verification
- Issue #429/#430: Database statement/lock timeouts enforcement