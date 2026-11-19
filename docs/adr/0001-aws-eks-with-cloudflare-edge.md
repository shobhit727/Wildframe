# ADR-0001: AWS EKS compute with a Cloudflare edge

| Field | Value |
|---|---|
| Status | **Proposed** — not accepted until the approvals in [§8](#8-approvals-required) are recorded |
| Date | 2026-11-18 |
| Scope | Production compute platform (WS4) and public edge: DNS, TLS, WAF, CDN, admin access (WS5) |
| Related | #1109 (Cloudflare account/edge baseline), #1110 (private EKS API from CI), #1111 (EKS version policy), #1112 (production Kafka broker) |
| Supersedes | — |

Vendor facts below were retrieved from primary vendor documentation on
**2026-11-17**; each carries a source tag `[Sn]` resolved in [§11](#11-sources).
Prices, plan limits and support dates change — re-check them before acting.
Anything that could not be confirmed from a primary source is marked
**UNVERIFIED**. Inputs nobody has supplied are marked **NOT PROVIDED**; this
ADR does not invent them.

This is a documentation-only record. It changes no Terraform, Helm or CI
configuration, and nothing here has been applied to any live account.

---

## 1. Context

### 1.1 What the repository provisions today

| Area | Current state | Evidence |
|---|---|---|
| Region | Single region, `us-east-1` (Terraform default, state backend, CI deploy jobs) | `infrastructure/terraform/variables.tf` (`aws_region`), `main.tf` `backend "s3"`, `.github/workflows/ci-cd.yml` `aws-region: us-east-1` |
| Compute | EKS cluster `wildframe-<env>` with a managed node group (default 3–20 nodes, `t3.xlarge`/`t3.2xlarge`) | `main.tf` `aws_eks_cluster.main`, `aws_eks_node_group.general` |
| EKS API | **Private endpoint only**: `endpoint_private_access = true`, `endpoint_public_access = false` | `main.tf` `aws_eks_cluster.main.vpc_config` |
| EKS version | Default `1.31`; validation allows only `1.30`, `1.31`, `1.32` | `variables.tf` `kubernetes_version` |
| Data | Aurora PostgreSQL cluster (`db.r6g.xlarge` ×2 default), ElastiCache Redis replication group (`cache.r6g.xlarge` ×3 default), KMS keys | `main.tf` `aws_rds_cluster.postgres`, `aws_elasticache_replication_group.redis` |
| Kafka | **Not provisioned**; production Helm values point at `kafka-prod:9092` | `infrastructure/helm/values-production.yaml` (`kafkaBootstrap`); #1112 |
| Video edge | S3 bucket (private, OAC) → **CloudFront** distribution → **WAFv2** web ACL (`scope = "CLOUDFRONT"`: AWS Common + Known Bad Inputs managed groups, 10 000-request/IP rate rule) | `main.tf` `aws_s3_bucket.videos`, `aws_cloudfront_distribution.videos`, `aws_wafv2_web_acl.videos` |
| CloudFront signing | No trusted key groups / signed URLs or cookies configured; query strings not forwarded | `main.tf` `aws_cloudfront_distribution.videos` |
| API certificate | `aws_acm_certificate.api` for `api.<domain>` with `validation_method = "DNS"` but **no validation record or `aws_acm_certificate_validation`** — it never becomes `ISSUED` through Terraform | `main.tf` `aws_acm_certificate.api` |
| Providers | `kubernetes`, `helm`, `random`, `tls` are declared but **no resource uses them**; the `kubernetes`/`helm` provider blocks point at the private EKS endpoint | `main.tf` `terraform.required_providers`, `provider "kubernetes"`, `provider "helm"` |
| Ingress | Helm Ingress for `api-gateway` with `className: nginx`; production host `app.wildframe.com` | `infrastructure/helm/wildframe/values.yaml`, `values-production.yaml`, `templates/ingress.yaml` |
| CI/CD | GitHub-hosted `ubuntu-latest` runners assume `vars.AWS_DEPLOY_ROLE_ARN` via GitHub OIDC, run `aws eks update-kubeconfig`, then `helm upgrade --install` | `.github/workflows/ci-cd.yml` `deploy-staging`, `deploy-production` |
| Cloudflare | **Nothing in IaC.** `docs/GO_LIVE.md` says "Cloudflare DNS as the Terraform assumes" and "ACM certs for both hostnames (Terraform includes them)"; neither is true of the current Terraform. A Cloudflare account exists and has an open hardening baseline (#1109). | `docs/GO_LIVE.md` §1, §4; #1109 |
| Content protection | Plaintext HLS/DASH; multi-DRM is unimplemented | `docs/DRM_SCOPE.md` |

### 1.2 Business constraints (from the founder planning corpus, summarised)

- **Launch sequence:** India first (commercial launch targeted ~2027-02), then the
  United States, then the EU.
- **Data residency:** an India launch gate requires `ap-south-1` as the primary
  location for Indian data; an EU region must exist before EU launch. The
  founder decided "AWS, multiple regions"; the specific region names are a
  planning judgment, not a founder decision.
- **Privacy law:** GDPR and India's DPDP Act apply. A CDN is a data processor and
  must appear on the subprocessor register with a DPA. The corpus treats IP
  addresses as personal data under both regimes (to be confirmed by counsel).
- **Cost:** self-funded; cost discipline (budget alarms, right-sizing,
  managed services over headcount) is an explicit expectation. The corpus
  recommends *buying* the CDN rather than building edge delivery.

These constraints come from a private planning corpus that is not in this
repository; they are summarised, not quoted.

### 1.3 Open issues this decision touches

- **#1110:** deploy jobs on GitHub-hosted runners cannot reach a private-only EKS
  API endpoint.
- **#1111:** the Terraform version allow-list is stale (see §6.4).
- **#1109:** Cloudflare account MFA and zone TLS/WAF baseline are not in place.
- **#1112:** no production Kafka broker is provisioned.

## 2. Decision

**Proposed:**

1. **Compute stays on AWS EKS** (Kubernetes via the existing Helm chart), in the
   region(s) the owner selects under the residency constraints in §1.2.
   Workload identity uses **EKS Pod Identity** (AWS's stated recommendation
   [S30]); application secrets come from **AWS Secrets Manager** via External
   Secrets Operator or the AWS Secrets Store CSI provider (both support Pod
   Identity [S41][S42]).
2. **Cloudflare is the public edge for the interactive hostnames** — web app,
   API and admin:
   - authoritative DNS with proxied records [S1];
   - SSL/TLS mode **Full (strict)** to the origin, using a publicly trusted
     certificate or a Cloudflare Origin CA certificate [S3][S4];
   - origin lock-down by **Authenticated Origin Pulls** with a zone-level
     certificate the operator controls [S5], plus an origin allow-list of the
     published Cloudflare IP ranges [S6];
   - WAF managed rules and rate-limiting rules on whichever plan the owner buys
     (§6.1) [S7][S8];
   - admin hostnames behind **Cloudflare Access** with MFA, and the origin
     validates the Access JWT so that requests bypassing Access are rejected
     [S15].
3. **Video delivery edge: owner decision required.** Terraform today serves
   video through CloudFront + WAFv2, and Cloudflare's terms restrict serving
   video through its CDN below Enterprise (§3). This ADR presents the options
   and **recommends Option B** (split edge) but does not decide it.

## 3. Conflict: Terraform already provisions a CloudFront + WAFv2 edge

Adopting Cloudflare does not automatically replace what is in
`infrastructure/terraform/main.tf`. Two facts drive the choice:

- Cloudflare's Service-Specific Terms (Application Services):
  > "Unless you are an Enterprise customer, Cloudflare offers specific Paid
  > Services (e.g., the Developer Platform, Images, and Stream) that you must
  > use in order to serve video and other large files via the CDN." [S10]

  Cloudflare documents **Stream Delivery** (bring-your-own video through the
  CDN) as Enterprise-only [S11]. Cloudflare **Stream** documentation does not
  mention Widevine, FairPlay or PlayReady — DRM support in Stream is
  **UNVERIFIED** (absent from the docs searched), which matters because
  `docs/DRM_SCOPE.md` targets multi-DRM.
- AWS bills nothing for origin fetches from AWS origins (S3, ALB, including
  private VPC origins) to CloudFront [S47]. Traffic from AWS to a non-AWS CDN
  such as Cloudflare is ordinary internet data transfer out [INFERENCE — the
  free-origin-fetch statement covers CloudFront only; per-GB S3/EC2 internet
  egress rates for the three regions are **UNVERIFIED** because the pricing
  pages render them dynamically]. AWS is not listed as a Cloudflare Bandwidth
  Alliance partner [S63].

### Options

| | Option | What changes | Assessment |
|---|---|---|---|
| A | **Cloudflare-only edge** — drop CloudFront + WAFv2 | Video served through Cloudflare; S3 (or R2) as origin | Below Enterprise, the terms require a Cloudflare paid service for video and name "the Developer Platform, Images, and Stream" [S10]. Paths: **Stream** (no documented DRM); **Enterprise** Stream Delivery [S11]; or a **Developer Platform** path such as R2 + Workers — whether that satisfies the terms for self-packaged HLS/DRM is **UNVERIFIED** and needs written confirmation from Cloudflare. S3→Cloudflare egress is internet transfer out (§3, inference). R2 avoids egress fees [S12] but offers jurisdictions only for `eu`, `us`, `fedramp` — no India [S13] — and does not implement bucket policies, versioning or SSE-KMS [S14]. **Not recommended** for launch without that confirmation or an Enterprise quote. |
| B | **Split edge (recommended)** — Cloudflare for app/API/admin; keep CloudFront + WAFv2 for video only | Video on a dedicated hostname (e.g. a media subdomain) as a DNS-only record pointing at CloudFront; Cloudflare proxies the rest. Today's distribution has no `aliases` and uses the default certificate unless `cloudfront_acm_certificate_arn` is set, so B needs a follow-up Terraform change: an alias plus a validated `us-east-1` ACM certificate | Video keeps free origin fetch [S47], India edge locations [S46], native signed cookies for HLS [S52] and the existing S3 OAC lock-down. Interactive traffic gets Cloudflare WAF, Access and DNS. Cost: two edge vendors, two WAF rule sets, two subprocessor entries. |
| C | **CloudFront-only edge** — no Cloudflare in the request path | Put the app/API load balancer behind CloudFront, ideally as a **VPC origin** so it stays in private subnets [S51]. Terraform has no ALB today; one would come from the AWS Load Balancer Controller (follow-up, §5.5). Cloudflare at most as registrar/DNS | Single vendor and IaC provider; AWS-managed origin prefix list exists for CloudFront only [S50]. Flat-rate plans bundle WAF, DDoS protection and Route 53 DNS; VPC origins require the Business tier or above on flat-rate pricing [S48]. Loses Cloudflare Access for admin (an AWS-native substitute is not researched here). **Recommended fallback** if the owner wants one edge vendor. |
| D | **Stacked** — Cloudflare in front of CloudFront | Every request traverses both CDNs | Video still flows through the Cloudflare CDN (same terms as A) and pays both vendors. **Rejected.** |

**Recommendation:** Option **B** for launch. It keeps the video path that
already exists in Terraform, avoids an Enterprise contract before traffic is
known, and still delivers the Cloudflare security features the interactive
hostnames benefit from most (WAF, Access, account-level 2FA enforcement
[S20]). If the owner prefers a single edge vendor, choose **C**; choose **A**
only together with a Cloudflare Enterprise quote and a DRM path.

## 4. Alternatives considered

### 4.1 Cloudflare Workers (Python) as compute — rejected

- Python Workers run on Pyodide inside a V8 isolate; `threading` and
  `multiprocessing` are non-functional; memory is 128 MB per isolate [S60].
  Whether Python Workers are GA is **UNVERIFIED**.
- TCP sockets (`connect()`) are a public beta, cannot be created in global scope
  or shared across requests, and cannot reach private network IPs [S61] — the
  15 services' long-lived Kafka consumers have no equivalent.
- No India data-location control: Durable Object jurisdictions (the Workers
  primitive for pinning state) are only `eu`, `us`, `fedramp` [S64].
- Porting 15 FastAPI services would discard the tested Helm deployment path.

### 4.2 Cloudflare Containers as compute — rejected

- GA since 2026-04-13 [S62]; requires the Workers Paid plan and a Worker /
  Durable Object to drive the lifecycle; no built-in autoscaling; disk is
  ephemeral [S62].
- Placement offers an `APAC` region but jurisdictions only for `eu`, `us`,
  `fedramp`; jurisdiction does not govern where images or snapshots are stored
  [S62] — insufficient for the India residency gate.
- Reaching Aurora/ElastiCache/Kafka in a private VPC would need a tunnel;
  long-lived outbound TCP from a Container to a private VPC is **UNVERIFIED**.

### 4.3 Amazon ECS on Fargate instead of EKS — not chosen now, kept as a trigger

- No orchestration fee [S54] versus $0.10 per EKS cluster-hour on standard
  support [S45]; no Kubernetes version upgrades to run.
- Available in `ap-south-1` and `eu-central-1` [S55]; per-task IAM roles and
  per-task security groups replace Pod Identity and NetworkPolicy [S54].
- Cost: the Helm chart, NetworkPolicies and deploy jobs would be rewritten as
  task definitions (inference — no AWS page states this). The corpus explicitly
  favours keeping the built topology and reducing ops load through managed
  services. Revisit under the triggers in §9.

### 4.4 CloudFront-only edge — kept as Option C

See §3. Strongest on cost and origin privacy; weakest on admin access tooling
and on reusing the existing Cloudflare account.

## 5. Consequences

### 5.1 Cost

All business inputs are **NOT PROVIDED**: monthly infrastructure spend
ceiling, expected traffic (GB delivered, requests, concurrent viewers), and the
Cloudflare plan tier. Cloudflare plan prices were **not researched**. Vendor
list prices on 2026-11-17:

| Item | List price | Source |
|---|---|---|
| EKS control plane | $0.10 per cluster-hour (standard support); $0.60 (extended support) | [S45] |
| AWS WAF | $5 per web ACL-month, $1 per rule or managed rule group-month, $0.60 per million requests | [S49] |
| CloudFront pay-as-you-go, India | $0.109/GB for the next 9 TB after the account-wide 1 TB/month free tier; origin-bound transfer $0.160/GB | [S47] |
| CloudFront pay-as-you-go, US/Europe | $0.085/GB for the next 9 TB | [S47] |
| CloudFront flat-rate | $0 / $15 / $200 / $1,000 per distribution-month (Free/Pro/Business/Premium); no overage charges, but delivery may be adjusted after sustained usage above the allowance | [S48] |
| Cloudflare R2 | $0.015/GB-month standard storage; no egress charges | [S12] |
| Cloudflare Stream | $5 per month per 1,000 minutes stored; $1 per 1,000 minutes delivered | [S17] |
| AWS Shield Advanced | From $3,000/month, 1-year commitment | [S53] |

Observations:

- One EKS cluster on standard support is about $73/month in control-plane fees
  (730 h × $0.10). The same cluster on an extended-support version is about
  $438/month — the Terraform allow-list currently forces extended support (§6.4).
- The Terraform default sizing (5 × `t3.xlarge` desired, 2 × `db.r6g.xlarge`,
  3 × `cache.r6g.xlarge`) was not priced here and should be reviewed against
  the spend ceiling once it is provided.
- Option B adds AWS egress for the interactive (non-video) traffic that leaves
  through Cloudflare; Option C does not (free origin fetch). With video on
  CloudFront, the interactive share is expected to be small (inference — no
  traffic data).

### 5.2 Lock-in

- Compute: low. Helm/Kubernetes is portable across managed Kubernetes services.
- Edge: medium. DNS, WAF rules, Cache Rules and Access policies are
  vendor-specific configuration; nothing is in IaC yet, so adopting Cloudflare
  should come with a Terraform `cloudflare` provider module (follow-up).
- HSTS: once enabled with a long max-age, moving DNS off Cloudflare or turning
  off proxying can make the site unreachable for the max-age period [S21].

### 5.3 Security

- **Origin lock-down for a Cloudflare-fronted ALB** has no AWS-managed prefix
  list (that exists only for CloudFront [S50]); use Authenticated Origin Pulls
  [S5] *and* a customer-managed prefix list built from cloudflare.com/ips [S6],
  maintained per region. Cloudflare Origin CA certificates are trusted only by
  Cloudflare and Cloudflare sends no expiry notifications [S4] — expiry must be
  monitored by us.
- **Cloudflare plan limits** shape the WAF: the Cloudflare Managed Ruleset and
  OWASP Core Ruleset need Pro or higher; rate-limiting rules number 1/2/5/100 on
  Free/Pro/Business/Enterprise (Enterprise depends on contract), IP-based only on Free/Pro [S7][S8].
- **Admin surface:** Access is deny-by-default and integrates any SAML/OIDC IdP
  with MFA [S15]. Cloudflare's SASE reference architecture says many Cloudflare
  One capabilities are free for up to 50 users [S16]; the exact Access seat
  limit on the free plan is **UNVERIFIED** (the plans page did not render it).
- **Account hygiene (#1109):** enforce member 2FA [S20] before routing
  production traffic through the account.
- The existing CloudFront WAFv2 rules remain the only WAF for video under
  Options B and C.

### 5.4 Privacy and residency

- Cloudflare becomes a processor of request metadata (including IP addresses)
  for the proxied hostnames: add it to the subprocessor register with a DPA.
- By default Cloudflare may store logs in core data centres globally; the
  Customer Metadata Boundary can be set only to the US or the EU, and the Data
  Localization Suite is an Enterprise-only add-on (India is supported for
  Regional Services, not for the metadata boundary) [S18][S19]. Whether this is
  acceptable under DPDP for Indian users is a **legal question for counsel** —
  not answered here.
- Application data stays in AWS regions under our control in every option;
  EKS, Aurora PostgreSQL, ElastiCache, MSK and Secrets Manager are available in
  `ap-south-1`, `ap-south-2` and `eu-central-1` [S43]. RDS has a lower default
  DB-instance quota in `ap-south-1` (20 vs 40) [S43].

### 5.5 Operations

- **CI reachability (#1110).** A private-only API endpoint accepts traffic only
  from the VPC or a connected network [S32]. Allow-listing GitHub-hosted runner
  IPs on a public endpoint is not viable: EKS allows at most 40 public-access
  CIDRs and the quota cannot be raised [S33]; GitHub's hosted-runner IPs are
  dynamic and GitHub advises against IP allow-listing [S34]; GitHub's private
  networking for hosted runners is documented for Azure VNETs only and needs
  larger runners [S35]. Viable paths are self-hosted runners inside the VPC
  (e.g. Actions Runner Controller [S36]) or a controlled tunnel; GitHub also
  documents other private-network patterns (e.g. WireGuard, an OIDC-gated API
  gateway) that were not evaluated here [UNVERIFIED]. Kubernetes
  access for the deploy role should use EKS access entries (the `aws-auth`
  ConfigMap is deprecated) [S37], with the OIDC trust policy scoped on the
  `sub` claim to the deployment environment [S38]. The choice belongs to #1110.
- **Ingress controller.** The chart defaults to `className: nginx`, but the
  ingress-nginx project was retired in March 2026 and its README tells new users
  not to deploy it [S40]. The AWS Load Balancer Controller (IngressClass `alb`,
  `target-type: ip`, ACM certificate via the `certificate-arn` annotation) is
  the AWS-supported path [S39]. This
  is a follow-up chart change, not part of this ADR.
- **Two control planes** (AWS + Cloudflare) under Option B: two consoles, two
  WAF policies, two sets of credentials. Cloudflare API tokens can be scoped
  per account/zone/permission with expiry and client-IP filtering [S22].

## 6. Preconditions surfaced by this review (tracked elsewhere)

### 6.1 Cloudflare plan

**NOT PROVIDED.** Free gives only the Free Managed Ruleset and one rate-limit
rule [S7][S8]; Pro is the minimum for the Cloudflare Managed and OWASP
rulesets; Business is required to keep another provider authoritative for DNS
(CNAME/partial setup) [S2]; Enterprise is required for Option A.

### 6.2 Region

Terraform, the state backend and CI target `us-east-1` only. The India launch
gate calls for `ap-south-1` as primary for Indian data. Choosing the launch
region(s) is an owner decision that this ADR depends on but does not make.

### 6.3 API certificate

`aws_acm_certificate.api` (for `api.<domain>`) is never validated by Terraform
and nothing attaches it to a load balancer; meanwhile the production ingress
host is `app.wildframe.com` (`infrastructure/helm/values-production.yaml`).
Hostnames and certificates must be reconciled first. Under Full (strict) the
origin then needs a valid certificate for the hostname Cloudflare connects to:
either a validated ACM certificate attached to the load balancer (validating
with Cloudflare DNS as the authority is **UNVERIFIED** here — not researched)
or a Cloudflare Origin CA certificate on the origin [S3][S4].

### 6.4 EKS version (#1111)

Standard support today covers 1.34–1.37; 1.31–1.33 are in extended support;
1.30 is no longer offered. Extended support for **1.31 ends 2026-11-26** and
1.32 on 2027-03-23; after extended support ends AWS may upgrade the control
plane at any time without notification [S44]. The current default (1.31) and
allow-list (1.30–1.32) therefore cannot produce a standard-support cluster.

## 7. Out of scope

- Any Terraform, Helm or workflow change; any `terraform apply`; any change to a
  live AWS or Cloudflare account.
- The production Kafka provider (#1112), DRM provider selection
  (`docs/DRM_SCOPE.md`), and CI runner implementation (#1110).

## 8. Approvals required

| Decision | Approver | Status |
|---|---|---|
| EKS (vs ECS on Fargate) for compute, and Cloudflare in front of app/API/admin hostnames | Owner (founder) | **Pending** |
| Video edge option A / B / C | Owner (founder) | **Pending** |
| Cloudflare plan tier and edge spend ceiling | Owner (founder) | **Pending — NOT PROVIDED** |
| Launch region(s) and residency routing | Owner (founder), with privacy counsel | **Pending** |
| Cloudflare as subprocessor (DPA; metadata outside India) | Privacy/legal counsel | **Pending** |
| Origin lock-down and Access design | Security reviewer | **Pending** |
| CI path to the private EKS API | Platform/infra owner (#1110) | **Pending** |

This ADR moves to **Accepted** only when the first four rows are approved.

## 9. Reconsideration triggers

Revisit this decision when any of the following occurs:

- The DRM provider is chosen and requires a specific CDN or packaging path.
- Monthly edge spend crosses the owner's ceiling (**NOT PROVIDED**), or video
  traffic approaches the CloudFront flat-rate allowance in use.
- Cloudflare changes its video terms, adds an India R2 jurisdiction or India
  metadata boundary, or the owner buys Enterprise.
- DPDP rules or a designation as a Significant Data Fiduciary impose
  localisation that the edge cannot meet.
- EU launch planning starts (EU region, metadata boundary, SCCs).
- Kubernetes operations (version upgrades, add-ons) outweigh the benefit of
  Helm portability for the team size — re-evaluate ECS on Fargate (§4.3).
- More than the plan's rate-limit rules or advanced rate-limiting
  characteristics are needed at the edge.

## 10. Related documents

- [`docs/DEPLOYMENT_GUIDE.md`](../DEPLOYMENT_GUIDE.md) — current deploy path.
- [`docs/GO_LIVE.md`](../GO_LIVE.md) — go-live runbook (its Cloudflare/ACM
  statements disagree with Terraform; see §1.1).
- [`docs/DRM_SCOPE.md`](../DRM_SCOPE.md) — content-protection gap.
- [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) — service architecture.

## 11. Sources

All retrieved 2026-11-17.

**Cloudflare**

- [S1] Proxy status — <https://developers.cloudflare.com/dns/proxy-status/>
- [S2] Partial (CNAME) setup — <https://developers.cloudflare.com/dns/zone-setups/partial-setup/>
- [S3] Full (strict) — <https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/>
- [S4] Origin CA certificates — <https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/>
- [S5] Authenticated Origin Pulls — <https://developers.cloudflare.com/ssl/origin-configuration/authenticated-origin-pull/>
- [S6] Cloudflare IP ranges — <https://www.cloudflare.com/ips/>
- [S7] WAF managed rules — <https://developers.cloudflare.com/waf/managed-rules/>
- [S8] Rate limiting rules — <https://developers.cloudflare.com/waf/rate-limiting-rules/>
- [S10] Service-Specific Terms, Application Services — <https://www.cloudflare.com/service-specific-terms-application-services/>
- [S11] Delivering videos with Cloudflare — <https://developers.cloudflare.com/fundamentals/reference/policies-compliances/delivering-videos-with-cloudflare/>
- [S12] R2 pricing — <https://developers.cloudflare.com/r2/pricing/>
- [S13] R2 data location — <https://developers.cloudflare.com/r2/reference/data-location/>
- [S14] R2 S3 API compatibility — <https://developers.cloudflare.com/r2/api/s3/api/>
- [S15] Access, self-hosted public application — <https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/>
- [S16] SASE reference architecture — <https://developers.cloudflare.com/reference-architecture/architectures/sase/>
- [S17] Stream pricing — <https://developers.cloudflare.com/stream/pricing/>
- [S18] Data Localization Suite and region support — <https://developers.cloudflare.com/data-localization/>, <https://developers.cloudflare.com/data-localization/region-support/>
- [S19] Customer Metadata Boundary — <https://developers.cloudflare.com/data-localization/metadata-boundary/>
- [S20] Two-factor authentication — <https://developers.cloudflare.com/fundamentals/user-profiles/2fa/>
- [S21] HSTS — <https://developers.cloudflare.com/ssl/edge-certificates/additional-options/http-strict-transport-security/>
- [S60] Python Workers and Workers limits — <https://developers.cloudflare.com/workers/languages/python/>, <https://developers.cloudflare.com/workers/languages/python/stdlib/>, <https://developers.cloudflare.com/workers/platform/limits/>
- [S61] TCP sockets and betas — <https://developers.cloudflare.com/workers/runtime-apis/tcp-sockets/>, <https://developers.cloudflare.com/workers/platform/betas/>
- [S62] Containers GA, overview, placement, FAQ — <https://developers.cloudflare.com/changelog/post/2026-04-13-containers-sandbox-ga/>, <https://developers.cloudflare.com/containers/>, <https://developers.cloudflare.com/containers/concepts/placement/>, <https://developers.cloudflare.com/containers/faq/>
- [S63] Bandwidth Alliance — <https://www.cloudflare.com/bandwidth-alliance/>
- [S64] Durable Objects data location — <https://developers.cloudflare.com/durable-objects/reference/data-location/>
- [S22] Create API token — <https://developers.cloudflare.com/fundamentals/api/get-started/create-token/>

**AWS, GitHub, Kubernetes**

- [S30] EKS service-account access options — <https://docs.aws.amazon.com/eks/latest/userguide/service-accounts.html>
- [S32] EKS cluster endpoint access — <https://docs.aws.amazon.com/eks/latest/userguide/cluster-endpoint.html>
- [S33] EKS endpoints and quotas — <https://docs.aws.amazon.com/general/latest/gr/eks.html>
- [S34] GitHub IP addresses — <https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-githubs-ip-addresses>
- [S35] Azure private networking for GitHub-hosted runners — <https://docs.github.com/en/organizations/managing-organization-settings/about-azure-private-networking-for-github-hosted-runners-in-your-organization>
- [S36] Actions Runner Controller — <https://docs.github.com/en/actions/concepts/runners/actions-runner-controller>
- [S37] EKS access entries; `aws-auth` deprecation — <https://docs.aws.amazon.com/eks/latest/userguide/access-entries.html>, <https://docs.aws.amazon.com/eks/latest/userguide/auth-configmap.html>
- [S38] GitHub OIDC in AWS — <https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws>
- [S39] AWS Load Balancer Controller; ALB ingress; controller annotations — <https://docs.aws.amazon.com/eks/latest/userguide/aws-load-balancer-controller.html>, <https://docs.aws.amazon.com/eks/latest/userguide/alb-ingress.html>, <https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/guide/ingress/annotations/>
- [S40] Ingress NGINX retirement — <https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/>, <https://github.com/kubernetes/ingress-nginx>
- [S41] External Secrets Operator, AWS access — <https://external-secrets.io/latest/provider/aws-access/>
- [S42] AWS Secrets and Configuration Provider on EKS — <https://docs.aws.amazon.com/secretsmanager/latest/userguide/ascp-eks-installation.html>
- [S43] Regional endpoints and quotas — <https://docs.aws.amazon.com/general/latest/gr/eks.html>, <https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Concepts.RegionsAndAvailabilityZones.html>, <https://docs.aws.amazon.com/general/latest/gr/rds-service.html>, <https://docs.aws.amazon.com/general/latest/gr/elasticache-service.html>, <https://docs.aws.amazon.com/general/latest/gr/msk.html>, <https://docs.aws.amazon.com/general/latest/gr/asm.html>
- [S44] EKS Kubernetes versions — <https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html>
- [S45] EKS pricing — <https://aws.amazon.com/eks/pricing/>
- [S46] CloudFront features (edge locations) — <https://aws.amazon.com/cloudfront/features/>
- [S47] CloudFront pay-as-you-go pricing — <https://aws.amazon.com/cloudfront/pricing/pay-as-you-go/>
- [S48] CloudFront flat-rate pricing — <https://aws.amazon.com/cloudfront/pricing/>, <https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/flat-rate-pricing-plan.html>
- [S49] AWS WAF pricing — <https://aws.amazon.com/waf/pricing/>
- [S50] AWS-managed prefix lists — <https://docs.aws.amazon.com/vpc/latest/userguide/working-with-aws-managed-prefix-lists.html>
- [S51] CloudFront VPC origins — <https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html>
- [S52] CloudFront signed URLs vs signed cookies — <https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-choosing-signed-urls-cookies.html>
- [S53] AWS Shield pricing — <https://aws.amazon.com/shield/pricing/>
- [S54] Amazon ECS pricing; task IAM roles; awsvpc networking — <https://aws.amazon.com/ecs/pricing/>, <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html>, <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-networking-awsvpc.html>
- [S55] Fargate Regions — <https://docs.aws.amazon.com/AmazonECS/latest/developerguide/AWS_Fargate-Regions.html>
