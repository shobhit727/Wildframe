# Cloudflare edge baseline for the interactive hostnames (web app, API via the
# gateway, admin). Video stays on CloudFront + WAFv2 in ../main.tf (ADR-0001
# option B); nothing here proxies or caches video.

locals {
  app_hostname = "${var.app_subdomain}.${var.domain_name}"

  # Managed WAF ruleset IDs (Cloudflare docs, "WAF managed rulesets").
  # Free zones can only execute the Free Managed Ruleset; Pro and higher get
  # the full Cloudflare Managed Ruleset (ADR-0001 §6.1).
  free_managed_ruleset_id       = "77454fe2d30c4220b5701f6fdfb893ba"
  cloudflare_managed_ruleset_id = "efb7b8c949ac4650a09736fc376e9aee"
  owasp_core_ruleset_id         = "4814384a9e5d4991b9815dcfc25d2f1f"

  is_free = var.cloudflare_plan == "free"

  # Rate limiting: Free allows 1 rule, a 10 s counting period and a 10 s
  # mitigation timeout. Pro allows periods up to 1 min and timeouts up to 1 h.
  rate_limit = {
    requests_per_period = coalesce(var.auth_rate_limit.requests_per_period, local.is_free ? 10 : 20)
    period              = coalesce(var.auth_rate_limit.period, local.is_free ? 10 : 60)
    mitigation_timeout  = coalesce(var.auth_rate_limit.mitigation_timeout, local.is_free ? 10 : 600)
  }

  # The browser calls the api-gateway with the service prefix
  # (apps/web/src/api/client.ts), so these are the public paths of
  # services/auth-service/app/api/routes/auth.py behind the gateway.
  credential_paths = [
    "/auth/api/v1/auth/login",
    "/auth/api/v1/auth/register",
    "/auth/api/v1/auth/mfa/login-verify",
  ]
}

# ---------------------------------------------------------------------------
# Zone TLS baseline (#1109)
# ---------------------------------------------------------------------------

resource "cloudflare_zone_setting" "ssl" {
  zone_id    = var.zone_id
  setting_id = "ssl"
  value      = var.ssl_mode
}

resource "cloudflare_zone_setting" "min_tls_version" {
  zone_id    = var.zone_id
  setting_id = "min_tls_version"
  value      = var.min_tls_version
}

resource "cloudflare_zone_setting" "tls_1_3" {
  zone_id    = var.zone_id
  setting_id = "tls_1_3"
  value      = "on"
}

resource "cloudflare_zone_setting" "always_use_https" {
  zone_id    = var.zone_id
  setting_id = "always_use_https"
  value      = "on"
}

resource "cloudflare_zone_setting" "automatic_https_rewrites" {
  zone_id    = var.zone_id
  setting_id = "automatic_https_rewrites"
  value      = "on"
}

# Staged HSTS. With the default (enabled = false) this resource pins HSTS
# OFF, which is the state #1109 asks for until HTTPS coverage is verified.
resource "cloudflare_zone_setting" "security_header" {
  zone_id    = var.zone_id
  setting_id = "security_header"
  value = {
    strict_transport_security = {
      enabled            = var.hsts.enabled
      max_age            = var.hsts.max_age
      include_subdomains = var.hsts.include_subdomains
      preload            = var.hsts.preload
      nosniff            = true
    }
  }
}

# ---------------------------------------------------------------------------
# DNS: proxied app hostname -> AWS load balancer
# ---------------------------------------------------------------------------

# Created only once origin_hostname is known. Proxied, so visitors only ever
# see Cloudflare addresses and every request passes the WAF below.
resource "cloudflare_dns_record" "app" {
  count = var.origin_hostname == "" ? 0 : 1

  zone_id = var.zone_id
  name    = local.app_hostname
  type    = "CNAME"
  content = var.origin_hostname
  proxied = true
  ttl     = 1 # "automatic"; required for proxied records
  comment = "Wildframe web app + api-gateway (EKS ingress). Managed by infrastructure/terraform/cloudflare."
}

# ---------------------------------------------------------------------------
# Origin lock-down (ADR-0001 §2, §5.3)
# ---------------------------------------------------------------------------

resource "cloudflare_authenticated_origin_pulls_settings" "zone" {
  count = var.enable_authenticated_origin_pulls ? 1 : 0

  zone_id = var.zone_id
  enabled = true
}

# Published Cloudflare egress ranges, for the origin's allow-list. AWS has no
# managed prefix list for Cloudflare (only for CloudFront), so the AWS side has
# to build one from this output. See outputs.tf.
data "cloudflare_ip_ranges" "current" {}

# ---------------------------------------------------------------------------
# WAF managed rules (#1109: "deploy the appropriate managed ruleset using an
# explicit execute rule")
# ---------------------------------------------------------------------------

# A zone has exactly one entry-point ruleset per phase. If the dashboard has
# already created one, `terraform apply` fails until it is imported; README.md
# has the import command.
resource "cloudflare_ruleset" "managed_waf" {
  zone_id     = var.zone_id
  name        = "Wildframe managed WAF"
  description = "Zone entry point for http_request_firewall_managed. Managed by Terraform."
  kind        = "zone"
  phase       = "http_request_firewall_managed"

  rules = concat(
    [
      {
        ref         = local.is_free ? "wf_execute_free_managed" : "wf_execute_cloudflare_managed"
        description = local.is_free ? "Execute Cloudflare Free Managed Ruleset" : "Execute Cloudflare Managed Ruleset"
        expression  = "true"
        action      = "execute"
        enabled     = true
        action_parameters = {
          id = local.is_free ? local.free_managed_ruleset_id : local.cloudflare_managed_ruleset_id
        }
      },
    ],
    var.enable_owasp_ruleset && !local.is_free ? [
      {
        ref         = "wf_execute_owasp_core"
        description = "Execute Cloudflare OWASP Core Ruleset (default PL1, threshold Medium)"
        expression  = "true"
        action      = "execute"
        enabled     = true
        action_parameters = {
          id = local.owasp_core_ruleset_id
        }
      },
    ] : [],
  )

  lifecycle {
    precondition {
      condition     = !(var.enable_owasp_ruleset && local.is_free)
      error_message = "enable_owasp_ruleset needs a Pro or higher plan; the OWASP Core Ruleset is not available on Free."
    }
  }
}

# ---------------------------------------------------------------------------
# Rate limiting on credential endpoints
# ---------------------------------------------------------------------------

# One rule: that is the whole Free-plan allowance. Counts by IP per data
# centre (cf.colo.id + ip.src is the characteristic set every plan supports).
resource "cloudflare_ruleset" "rate_limit" {
  zone_id     = var.zone_id
  name        = "Wildframe rate limits"
  description = "Zone entry point for http_ratelimit. Managed by Terraform."
  kind        = "zone"
  phase       = "http_ratelimit"

  rules = [
    {
      ref         = "wf_rl_credential_endpoints"
      description = "Rate limit login, register and MFA login per IP"
      expression = format(
        "(http.host eq \"%s\" and http.request.method eq \"POST\" and http.request.uri.path in {%s})",
        local.app_hostname,
        join(" ", [for p in local.credential_paths : "\"${p}\""]),
      )
      action  = "block"
      enabled = true
      ratelimit = {
        characteristics     = ["cf.colo.id", "ip.src"]
        period              = local.rate_limit.period
        requests_per_period = local.rate_limit.requests_per_period
        mitigation_timeout  = local.rate_limit.mitigation_timeout
      }
    },
  ]

  lifecycle {
    precondition {
      condition     = !local.is_free || (local.rate_limit.period == 10 && local.rate_limit.mitigation_timeout == 10)
      error_message = "On the Free plan the rate-limit period and mitigation_timeout must both be 10 seconds."
    }
    precondition {
      condition     = var.cloudflare_plan != "pro" || (local.rate_limit.period <= 60 && local.rate_limit.mitigation_timeout <= 3600)
      error_message = "On the Pro plan the rate-limit period must be <= 60 s and mitigation_timeout <= 3600 s."
    }
  }
}

# ---------------------------------------------------------------------------
# Cloudflare Access for the admin console (ADR-0001 §2)
# ---------------------------------------------------------------------------

resource "cloudflare_zero_trust_access_policy" "admins" {
  count = var.admin_access.enabled ? 1 : 0

  account_id       = var.cloudflare_account_id
  name             = "Wildframe admins"
  decision         = "allow"
  session_duration = var.admin_access.session_duration

  include = concat(
    [for e in var.admin_access.allowed_emails : { email = { email = e } }],
    [for d in var.admin_access.allowed_email_domains : { email_domain = { domain = d } }],
  )

  require = var.admin_access.require_mfa ? [{ auth_method = { auth_method = "mfa" } }] : null

  lifecycle {
    precondition {
      condition     = var.cloudflare_account_id != ""
      error_message = "admin_access.enabled = true needs cloudflare_account_id."
    }
  }
}

resource "cloudflare_zero_trust_access_application" "admin" {
  count = var.admin_access.enabled ? 1 : 0

  zone_id          = var.zone_id
  name             = "Wildframe admin"
  type             = "self_hosted"
  domain           = "${local.app_hostname}/admin"
  session_duration = var.admin_access.session_duration

  # Covers the admin pages (/admin, /admin/...) and the admin-service API the
  # console calls through the gateway (/admin/api/v1/admin/...,
  # apps/web/src/api/admin.ts).
  destinations = [
    {
      type = "public"
      uri  = "${local.app_hostname}/admin"
    },
  ]

  app_launcher_visible       = false
  http_only_cookie_attribute = true
  same_site_cookie_attribute = "strict"
  enable_binding_cookie      = true

  policies = [
    {
      id         = cloudflare_zero_trust_access_policy.admins[0].id
      precedence = 1
    },
  ]
}
