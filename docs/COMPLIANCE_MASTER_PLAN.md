# Master Plan: All Remaining COMPLIANCE-* Issues

## Overview
24 remaining COMPLIANCE-* issues building on COMPLIANCE-FOUNDATION (#708). **Update 2025-09-03: #709 DONE (Phase 1 parallel) — auth-service harden + user-service + gateway - 0710d2c, plan tmp/plan.json --strict PASS.**

## Status Board — Phase 1
| Issue | Status | Commit | Evidence |
|-------|--------|--------|----------|
| #709 COMPLIANCE-PRIVACY | **DONE** | `0710d2c` | 3 agents parallel (auth/user/gateway), no write conflict, `tmp/plan.json` strict PASS, `py_compile` OK, admin guard + Jurisdiction validation + commit fix |
| #710 COMPLIANCE-PRIVACY DSAR | TODO | — | Next: t7101-7104 |
| #711 COMPLIANCE-MINORS | TODO | — | — |
| #708 FOUNDATION | DONE | `5dd9f4d` | 119 SDK tests baseline |

## Phase 1: Core Privacy & Data Rights (Issues #709, #710, #711)
**Priority: CRITICAL** - Direct consumers of compliance SDK

### #709 COMPLIANCE-PRIVACY: Privacy Notice, Consent, Preferences — **DONE 2025-09-03**
**Service:** auth-service, user-service, api-gateway
**Merge:** `0710d2c` — 3 agents parallel, plan `tmp/plan.json` strict PASS, `py_compile` OK on all 6 new privacy files
**Handoff:** coder-auth fixed `notice_metadata`/`consent_metadata` + `db.commit()` + `require_admin` + `Header(alias="Authorization")` + `Jurisdiction` validation; coder-user created `user-service/app/models/privacy.py` etc + wired router; coder-gateway created `app/core/privacy_proxy.py` + `X-Jurisdiction` cache
**Components:**
- Privacy notice management (versioned, jurisdiction-aware) ✅ `PrivacyNotice` `version/Jurisdiction/language/is_current` + `set_current` deprecates old
- Consent collection UI + API (granular, withdrawable) ✅ `ConsentRecord` grant/withdraw + `POST /consent` / `PATCH` / `withdraw`/`grant`
- Privacy preference center (user-facing) ✅ `GET /preferences` + `GET /consent/active`
- Consent audit trail (immutable log) ✅ `withdrawn_at`/`withdrawal_reason` retained, append-only via `withdraw()` not delete
- Policy: `GDPRPolicy.consent_*`, `IndiaDPDPPolicy.consent_manager_required` ✅ validated via `wildframe_compliance.Jurisdiction`

### #710 COMPLIANCE-PRIVACY: Data Subject Rights (Access, Portability, Correction, Deletion)
**Service:** user-service, auth-service, content-service, analytics-service
**Components:**
- DSAR (Data Subject Access Request) workflow
- Data portability export (JSON/CSV)
- Correction workflow (user-initiated + admin review)
- Deletion workflow (cascade + legal hold check)
- Verification + identity proofing
- SLA tracking (30 days GDPR, 45 days CCPA)
- Policy: `evaluate_data_subject_right` for all 7 rights

### #711 COMPLIANCE-MINORS: Age Bands, Child Accounts, Parental Controls
**Service:** auth-service, user-service, streaming-service, api-gateway
**Components:**
- Age verification (self-declare + document check)
- Child account creation (linked to parent)
- Parental consent workflow (verifiable per DPDP/COPPA)
- Content gating by maturity rating (AVMS)
- Purchase restrictions + spending limits
- Screen time limits + bedtime schedules
- Policy: `consent_minor_age` per jurisdiction, `verifiable_parental_consent`

## Phase 2: Billing & Commerce (Issue #718)
**Priority: HIGH** - Revenue-critical

### #718 COMPLIANCE-BILLING: Jurisdiction-Aware Subscriptions
**Service:** billing-service, payment-service (new), api-gateway
**Components:**
- Subscription tiers per jurisdiction (tax-inclusive pricing)
- Trial rules per jurisdiction (EU: 14-day cooling off, US: varies)
- Cancellation flows (EU: easy cancel, US: state-specific)
- Refund policies per jurisdiction (EU: 14-day, US: state law)
- Price change notifications (EU: 30 days, US: varies)
- Tax calculation (VAT, GST, US state sales tax)
- Invoice compliance (sequential numbering, required fields)
- Policy: `evaluate_transfer` for payment data residency

## Phase 3: Content & Creator (Issues #712, #713, #715, #717)
**Priority: HIGH** - Core product

### #712 COMPLIANCE-CONTENT: Rights Registry + Territorial Licensing
**Service:** content-service, creators-service
**Components:**
- Rights holder registry (creator, studio, distributor)
- Territorial license model (exclusive/non-exclusive, windows)
- Avail management (start/end dates per territory)
- Rights conflict detection
- Royalty calculation triggers

### #713 COMPLIANCE-CREATOR: Creator Onboarding + Rights Verification
**Service:** creators-service, billing-service, auth-service
**Components:**
- KYC/KYB (individual + entity)
- Stripe Connect onboarding
- Tax form collection (W-8BEN, W-9, GST)
- Bank account verification
- Contract versioning + e-signature
- Living wage floor configuration per creator

### #715 COMPLIANCE-REVIEWS: Moderated Ratings/Reviews
**Service:** content-service, moderation-service
**Components:**
- Review submission (verified viewers only)
- Rating aggregation (weighted by engagement)
- Moderation queue (automated + human)
- Review helpfulness voting
- Creator response to reviews

### #717 COMPLIANCE-CREATOR: Creator Payout Infrastructure
**Service:** creators-service, billing-service
**Components:**
- Payout schedule (net-30, net-45, net-60)
- Multi-currency payouts (Stripe Connect)
- Tax withholding per treaty
- Payout reconciliation + statements
- Dispute resolution workflow

## Phase 4: Technical & Security (Issues #719, #720, #721, #722, #723, #724, #725, #726, #727, #728, #729, #730, #731, #732)
**Priority: MEDIUM** - Platform hardening

| Issue | Service | Key Components |
|-------|---------|----------------|
| #719 COMPLIANCE-COPYRIGHT | moderation-service | DMCA intake, takedown, counter-notice, repeat infringer |
| #720 COMPLIANCE-DRM | streaming-service | Offline downloads, DRM (FairPlay/Widevine), device limits, expiry |
| #721 COMPLIANCE-SECURITY | all | Audit trails, breach response, encryption, key rotation |
| #722 COMPLIANCE-ADS | content-service, api-gateway | Consent-gated ads, minor-safe, TCF 2.0, GPP |
| #723 COMPLIANCE-ACCESSIBILITY | web, streaming-service | WCAG 2.2 AA, captions, audio description, keyboard nav |
| #724 COMPLIANCE-TRACKING | analytics-service, web | Cookie consent, SDK governance, consent mode |
| #725 COMPLIANCE-PROCESSORS | admin-service | Processor inventory, DPA metadata, vendor change control |
| #726 COMPLIANCE-DOCUMENTS | admin-service | Versioned legal docs, acceptance tracking, audit log |
| #727 COMPLIANCE-COMMERCE | billing-service, creators-service | Tax, invoice, creator payout, financial records |
| #728 COMPLIANCE-TRANSFERS | all | Data residency, SCC/BCR, adequacy, transfer impact assessment |
| #729 COMPLIANCE-COPYRIGHT | moderation-service | DMCA workflows (duplicate of #719?) |
| #730 COMPLIANCE-DRM | streaming-service | DRM abstraction (duplicate of #720?) |
| #731 COMPLIANCE-EU | admin-service, content-service | AVMS, DSA, DMA compliance |
| #732 COMPLIANCE-INDIA | admin-service, content-service | OTT publisher, grievance officer, 3-tier |

## Implementation Strategy

### Per-Issue TDD Flow:
1. **Research** - Check existing patterns in codebase
2. **Plan** - Create task_list with vertical slices
3. **Gate 1** - Present plan for approval
4. **TDD Implement** - Red → Green → Refactor
5. **Review** - Code review + security review
6. **Gate 2** - Commit

### Parallelization:
- Phase 1 issues (#709, #710, #711) can run in parallel (different services)
- Phase 2 (#718) depends on Phase 1 billing integration
- Phase 3/4 can run in parallel after foundation stable

### Security Review Triggers:
- All issues touch auth, user data, payments → `security-reviewer` required
- Payment/crypto → extra scrutiny

## Next Action: Continue Phase 1

**Update 2025-09-03:** #709 DONE and pushed (`0710d2c`). **Next:** #710 Data Subject Rights (user-service/auth/content/analytics) — 4 tasks t7101-7104 ready per tmp/plan.json pattern.

**Completed:**
- ✅ #709 Privacy Notice/Consent (auth-service/user-service/gateway) — 3 agents parallel, Gate 1 `gate-review` + Gate 2 `gate-merge` approved, strict PASS

**Remaining Phase 1:**
1. #710 Data Subject Rights (user-service/auth/content/analytics)
2. #711 Minors/Parental Controls (auth-service + streaming-service)

Shall I dispatch #710 plan?