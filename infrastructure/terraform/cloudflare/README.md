# Cloudflare edge (Terraform)

Configuration for the Cloudflare edge described in
[ADR-0001](../../../docs/adr/0001-aws-eks-with-cloudflare-edge.md) (option B,
split edge) and the security baseline in issue #1109.

**Status: not applied.** Nothing in this directory has been run against any
Cloudflare account. The first `terraform plan` must be read by the account
owner before any `apply`.

## What it manages

| Area | Resource | Default |
|---|---|---|
| Edge to origin TLS | `cloudflare_zone_setting.ssl` | `strict` (Full (strict)) |
| Visitor TLS | `min_tls_version`, `tls_1_3` | TLS 1.2 minimum, TLS 1.3 on |
| HTTP to HTTPS | `always_use_https`, `automatic_https_rewrites` | on |
| HSTS | `security_header` | **off**: staged on later, see below |
| DNS | `cloudflare_dns_record.app` | not created until `origin_hostname` is set |
| WAF | `cloudflare_ruleset.managed_waf` | Free Managed Ruleset on Free; Cloudflare Managed Ruleset on Pro+; OWASP optional on Pro+ |
| Rate limit | `cloudflare_ruleset.rate_limit` | one rule on login / register / MFA login, per IP |
| Origin pulls | `cloudflare_authenticated_origin_pulls_settings` | off |
| Admin | `cloudflare_zero_trust_access_*` | off; deny by default when on, MFA required |

What it does **not** manage, on purpose:

- **Video.** Video stays on S3 + CloudFront + WAFv2 in `../main.tf`. Below
  Enterprise, Cloudflare's terms do not allow serving video through the CDN
  without one of its paid video/developer products (ADR-0001 §3).
- **Account MFA** (#1109). Enforcing 2FA is an account setting. Importing the
  account into Terraform state for one flag is more risk than it saves, so do
  it in the dashboard: *Manage Account → Members → Enforce Two-Factor*. The
  administrator must turn on their own MFA first.
- **The origin side.** There is no AWS load balancer in Terraform yet, so the
  origin allow-list and the origin's client-certificate requirement can't be
  written here. The outputs give you what they need.
- **Frontend hosting on Workers.** The web app stays on EKS behind this edge,
  as ADR-0001 decides.

## Before the first plan

1. **Account MFA on.** Turn on MFA for the administrator and enforce it
   account-wide (#1109). Do this before creating an API token.
2. **Create a scoped API token.** Use *My Profile → API Tokens → Create
   Token*, limited to the one zone (and to the account, for Access only):
   - Zone → Zone Settings → Edit
   - Zone → DNS → Edit
   - Zone → Zone WAF → Edit
   - Zone → SSL and Certificates → Edit (Authenticated Origin Pulls)
   - Account → Access: Apps and Policies → Edit (only if `admin_access` is on)

   Give it an expiry date. Put it in your shell, never in a file:
   `export CLOUDFLARE_API_TOKEN=...`
3. **Copy the example variables:**
   `cp terraform.tfvars.example terraform.tfvars`. The real `terraform.tfvars`
   is gitignored. Fill in `zone_id` and `cloudflare_plan`.
4. **Check every existing proxied record.** Full (strict) breaks any proxied
   hostname whose origin has no valid certificate. List the zone's DNS records
   and confirm each one serves HTTPS before you apply.

## Plan, import, apply

```bash
cd infrastructure/terraform/cloudflare
terraform init            # writes .terraform.lock.hcl: commit it
terraform plan -out=edge.tfplan
```

**Import existing entry-point rulesets first.** A zone has one entry-point
ruleset per phase. If the dashboard has already created one (most zones have
one for `http_request_firewall_managed`), the create fails. Import it so
Terraform takes it over:

```bash
# Find the IDs (read-only):
curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones/$ZONE_ID/rulesets" \
  | jq -r '.result[] | select(.kind=="zone") | "\(.phase) \(.id)"'

terraform import cloudflare_ruleset.managed_waf "zones/$ZONE_ID/<ruleset id>"
terraform import cloudflare_ruleset.rate_limit  "zones/$ZONE_ID/<ruleset id>"
```

Importing replaces the dashboard's rules with the rules in `main.tf` on the
next apply. Read the plan diff for anything you want to keep.

Then apply the reviewed plan: `terraform apply edge.tfplan`.

## Staging HSTS

Turn HSTS on only after every hostname in the zone serves valid HTTPS. Raise
`max_age` one step at a time and wait at least the previous `max_age` between
steps (the steps are in `terraform.tfvars.example`). Leave `preload` off until
the owner decides to submit the domain to the preload list; getting off that
list takes months.

## Verify after apply (read-only)

These are #1109's acceptance checks. Attach the output to the issue.

```bash
Z="https://api.cloudflare.com/client/v4/zones/$ZONE_ID"
H="Authorization: Bearer $CLOUDFLARE_API_TOKEN"
for s in ssl min_tls_version always_use_https security_header; do
  curl -s -H "$H" "$Z/settings/$s" | jq -c '.result | {id, value}'
done
curl -s -H "$H" "$Z/rulesets/phases/http_request_firewall_managed/entrypoint" \
  | jq -c '.result.rules[] | {action, id: .action_parameters.id, enabled}'
curl -sI http://app.wildframe.com/ | grep -i '^location'   # expect https://
```

## Origin lock-down (follow-up on the AWS side)

Once a load balancer exists:

- Allow inbound 443 only from the `cloudflare_ipv4_cidrs` and
  `cloudflare_ipv6_cidrs` outputs, as a customer-managed prefix list. AWS has
  no managed list for Cloudflare. Re-run when `cloudflare_ip_ranges_etag`
  changes.
- Turn on `enable_authenticated_origin_pulls` and make the ingress **require**
  the client certificate. Cloudflare's shared certificate is presented by
  every Cloudflare customer's traffic. ADR-0001 asks for a zone certificate the
  operator controls; upload it with the API or dashboard, not through
  Terraform, so its private key never lands in Terraform state.
- With `admin_access` on, the admin-service and the web app must validate the
  `Cf-Access-Jwt-Assertion` header against `admin_access_application_aud`.
  Without that check, Access can be bypassed by reaching the origin directly.

## How this was checked

The provider binary could not be downloaded in the authoring environment, so
`terraform validate` against the real provider schema has **not** run. What
did run:

- `terraform fmt -check` on this directory (Terraform 1.9.8).
- Every attribute used here (70) checked against the generated schema docs of
  `cloudflare/terraform-provider-cloudflare` at v5.29.0, top level and nested.
- All `validation` blocks and the `locals` (rate-limit defaults per plan, the
  rate-limit expression) exercised with `terraform plan` on a provider-free
  copy.

Run `terraform init && terraform validate` before the first plan.
