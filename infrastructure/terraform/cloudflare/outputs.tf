output "app_hostname" {
  description = "Proxied hostname of the web app and api-gateway."
  value       = local.app_hostname
}

output "app_dns_record_created" {
  description = "False until origin_hostname is set; the zone baseline is still applied."
  value       = length(cloudflare_dns_record.app) > 0
}

output "cloudflare_ipv4_cidrs" {
  description = "Cloudflare egress IPv4 ranges. Feed these into the origin's security group / prefix list so only Cloudflare can reach it (ADR-0001 §5.3)."
  value       = data.cloudflare_ip_ranges.current.ipv4_cidrs
}

output "cloudflare_ipv6_cidrs" {
  description = "Cloudflare egress IPv6 ranges, for the same allow-list."
  value       = data.cloudflare_ip_ranges.current.ipv6_cidrs
}

output "cloudflare_ip_ranges_etag" {
  description = "Changes when Cloudflare publishes new ranges; re-run the origin allow-list update when it does."
  value       = data.cloudflare_ip_ranges.current.etag
}

output "waf_managed_ruleset" {
  description = "Which managed ruleset the zone executes."
  value       = local.is_free ? "Cloudflare Free Managed Ruleset" : "Cloudflare Managed Ruleset${var.enable_owasp_ruleset ? " + OWASP Core" : ""}"
}

output "admin_access_application_aud" {
  description = "Access application audience tag. The origin must validate the Cf-Access-Jwt-Assertion JWT against this AUD, or Access can be bypassed by reaching the origin directly."
  value       = var.admin_access.enabled ? cloudflare_zero_trust_access_application.admin[0].aud : null
}
