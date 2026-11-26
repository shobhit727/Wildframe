variable "cloudflare_account_id" {
  description = "Cloudflare account ID. Needed only for the Access policy (admin_access.enabled). Not a secret."
  type        = string
  default     = ""

  validation {
    condition     = var.cloudflare_account_id == "" || can(regex("^[0-9a-f]{32}$", var.cloudflare_account_id))
    error_message = "cloudflare_account_id must be a 32-character hex ID, or empty."
  }
}

variable "zone_id" {
  description = "Cloudflare zone ID for domain_name. Not a secret."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.zone_id))
    error_message = "zone_id must be a 32-character hex ID."
  }
}

variable "domain_name" {
  description = "Apex domain of the zone. Matches var.domain_name in the AWS root."
  type        = string
  default     = "wildframe.com"
}

variable "cloudflare_plan" {
  description = <<-EOT
    Plan of the zone. Decides which WAF managed ruleset is executed and what
    the rate-limit rule may use (ADR-0001 §6.1). The owner has not chosen a
    plan yet, so this defaults to the most restrictive one.
  EOT
  type        = string
  default     = "free"

  validation {
    condition     = contains(["free", "pro", "business", "enterprise"], var.cloudflare_plan)
    error_message = "cloudflare_plan must be one of: free, pro, business, enterprise."
  }
}

# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------

variable "ssl_mode" {
  description = <<-EOT
    Edge-to-origin encryption mode. ADR-0001 requires "strict" (Full (strict)):
    Cloudflare validates the origin certificate. Changing an existing zone to
    strict breaks any proxied hostname whose origin has no valid certificate,
    so check every proxied record before the first apply.
  EOT
  type        = string
  default     = "strict"

  validation {
    condition     = contains(["full", "strict"], var.ssl_mode)
    error_message = "ssl_mode must be \"strict\" (required by ADR-0001) or, temporarily during migration, \"full\". \"off\" and \"flexible\" send plaintext to the origin and are not allowed."
  }
}

variable "min_tls_version" {
  description = "Minimum TLS version accepted from visitors (#1109 requires at least 1.2)."
  type        = string
  default     = "1.2"

  validation {
    condition     = contains(["1.2", "1.3"], var.min_tls_version)
    error_message = "min_tls_version must be 1.2 or 1.3."
  }
}

variable "hsts" {
  description = <<-EOT
    HSTS policy. OFF by default on purpose (#1109): enable it only after every
    hostname in the zone serves valid HTTPS. Raise max_age in stages (for
    example 300 -> 86400 -> 31536000). Once browsers have cached a long
    max-age, moving DNS off Cloudflare or turning off proxying can make the
    site unreachable for that long (ADR-0001 §5.2). Do not set preload until
    the owner has decided to submit the domain to the preload list.
  EOT
  type = object({
    enabled            = bool
    max_age            = number
    include_subdomains = bool
    preload            = bool
  })
  default = {
    enabled            = false
    max_age            = 0
    include_subdomains = false
    preload            = false
  }

  validation {
    condition     = var.hsts.max_age >= 0 && var.hsts.max_age <= 31536000
    error_message = "hsts.max_age must be between 0 and 31536000 seconds (one year)."
  }

  validation {
    condition     = !var.hsts.enabled || var.hsts.max_age > 0
    error_message = "hsts.enabled = true needs a max_age greater than 0."
  }

  validation {
    condition     = !var.hsts.preload || (var.hsts.enabled && var.hsts.include_subdomains && var.hsts.max_age >= 31536000)
    error_message = "hsts.preload requires enabled = true, include_subdomains = true and max_age = 31536000."
  }
}

# ---------------------------------------------------------------------------
# DNS and origin
# ---------------------------------------------------------------------------

variable "app_subdomain" {
  description = "Hostname label of the web app. Production Helm ingress host is app.wildframe.com (infrastructure/helm/values-production.yaml)."
  type        = string
  default     = "app"
}

variable "origin_hostname" {
  description = <<-EOT
    DNS name of the AWS load balancer in front of the EKS ingress. NOT PROVIDED
    yet: Terraform creates no ALB/NLB today (ADR-0001 §1.1). While this is
    empty no DNS record is created, so the TLS/WAF baseline can be applied to
    the zone without routing any traffic.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.origin_hostname == "" || can(regex("^([a-z0-9-]+\\.)+[a-z]{2,}$", var.origin_hostname))
    error_message = "origin_hostname must be a DNS name (for example a load balancer's *.elb.amazonaws.com name), or empty."
  }
}

variable "enable_authenticated_origin_pulls" {
  description = <<-EOT
    Turn on zone-level Authenticated Origin Pulls: Cloudflare presents a client
    certificate to the origin. This uses Cloudflare's shared certificate until a
    zone certificate is uploaded (see README.md: the private key must not go
    through Terraform state). It protects nothing until the origin is also set
    to REQUIRE that client certificate.
  EOT
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# WAF
# ---------------------------------------------------------------------------

variable "enable_owasp_ruleset" {
  description = <<-EOT
    Also execute the Cloudflare OWASP Core Ruleset (Pro or higher). Off by
    default: Cloudflare documents it as prone to false positives on top of the
    Cloudflare Managed Ruleset, so turn it on only with a plan to watch
    Security Events and tune it.
  EOT
  type        = bool
  default     = false
}

variable "auth_rate_limit" {
  description = <<-EOT
    Edge rate limit on the unauthenticated credential endpoints (login,
    register, MFA login). This sits in front of the api-gateway's own auth rate
    limit (RATE_LIMIT_AUTH); keep it looser than the gateway so users behind a
    shared IP are not blocked twice. null fields use plan-safe defaults:
    Free allows only a 10 s period and a 10 s timeout.
  EOT
  type = object({
    requests_per_period = optional(number)
    period              = optional(number)
    mitigation_timeout  = optional(number)
  })
  default = {}
}

# ---------------------------------------------------------------------------
# Admin access
# ---------------------------------------------------------------------------

variable "admin_access" {
  description = <<-EOT
    Put Cloudflare Access in front of app.<domain>/admin (the admin console and
    the /admin/... gateway API it calls). Deny by default: only the listed
    emails / email domains are allowed. Requires cloudflare_account_id and an
    identity provider configured in Zero Trust.

    require_mfa makes the policy demand an MFA authentication method from the
    IdP. Leave it true unless the IdP does not report MFA (Cloudflare's
    one-time PIN login does not), in which case admins would be locked out.

    Access alone is not enough: the origin must also validate the Access JWT
    (Cf-Access-Jwt-Assertion), or a request that reaches the origin directly
    skips Access (ADR-0001 §2).
  EOT
  type = object({
    enabled               = bool
    allowed_emails        = optional(list(string), [])
    allowed_email_domains = optional(list(string), [])
    require_mfa           = optional(bool, true)
    session_duration      = optional(string, "8h")
  })
  default = {
    enabled = false
  }

  validation {
    condition = !var.admin_access.enabled || (
      length(var.admin_access.allowed_emails) + length(var.admin_access.allowed_email_domains) > 0
    )
    error_message = "admin_access.enabled = true needs at least one allowed email or email domain."
  }
}
