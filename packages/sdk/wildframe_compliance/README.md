# wildframe-compliance

Jurisdiction-aware compliance and policy configuration for Wildframe OTT platform.

## Features

- **Jurisdiction Support**: EU (GDPR, AVMS), US (Federal, CCPA/CPRA, state laws), India (DPDP), Canada, Brazil, Australia, Japan, Singapore, South Korea
- **Policy Engine**: Runtime evaluation of consent, data subject rights, cross-border transfers, retention, security
- **Service Integration**: Settings mixin for easy integration with FastAPI services
- **Event-Driven**: Kafka events for policy change propagation
- **Observability**: Metrics, structured logging, health checks

## Installation

```bash
pip install wildframe-compliance
```

## Quick Start

```python
from wildframe_compliance.jurisdiction import Jurisdiction
from wildframe_compliance.settings import ComplianceSettingsMixin
from wildframe_compliance.engine import PolicyEngine

class Settings(ComplianceSettingsMixin):
    SERVICE_NAME: str = "my-service"
    compliance_jurisdiction: Jurisdiction = Jurisdiction.EU

settings = Settings()
engine = PolicyEngine(settings)

# Evaluate consent
decision = engine.evaluate_consent(
    user_age=25,
    consent_given=True,
    consent_granular=True,
    consent_withdrawable=True,
)
print(decision.allowed)  # True

# Evaluate data subject right
decision = engine.evaluate_data_subject_right(
    right_type="erasure",
    user_id="user-123",
    data_categories=["profile"],
    retention_days=365,
)
print(decision.can_execute)  # True
```

## Jurisdictions

- `GLOBAL` - Global baseline (ISO 27001, SOC 2, NIST CSF)
- `EU` - European Union (GDPR, ePrivacy, AVMS, DSA, DMA)
- `US` - United States Federal (FTC, COPPA, HIPAA, GLBA)
- `US-CA` - California (CCPA/CPRA)
- `US-VA` - Virginia (VCDPA)
- `US-CO` - Colorado (CPA)
- `US-CT` - Connecticut (CTDPA)
- `US-UT` - Utah (UCPA)
- `US-TX` - Texas (TDPSA)
- `US-OR` - Oregon (OCPA)
- `US-MT` - Montana (MTCDPA)
- `US-DE` - Delaware (DPDPA)
- `US-NH` - New Hampshire (NHPPA)
- `US-NJ` - New Jersey (NJDPA)
- `US-MN` - Minnesota (MCDPA)
- `US-MD` - Maryland (MODPA)
- `US-NE` - Nebraska (NEDPA)
- `US-RI` - Rhode Island (RIDTPPA)
- `US-KY` - Kentucky (KCDPA)
- `IN` - India (DPDP Act, IT Act, OTT Rules)
- `CA` - Canada (PIPEDA)
- `CA-QC` - Quebec (Law 25)
- `BR` - Brazil (LGPD)
- `AU` - Australia (Privacy Act)
- `JP` - Japan (APPI)
- `KR` - South Korea (PIPA)
- `SG` - Singapore (PDPA)

## Policy Evaluation

The engine supports evaluating:

- **Consent**: Age verification, granularity, withdrawability, sensitive data
- **Data Subject Rights**: Access, rectification, erasure, portability, restriction, objection
- **Cross-Border Transfers**: Adequacy decisions, SCCs, BCRs, central government approval (India)
- **Retention**: Default and maximum retention periods
- **Security**: Encryption at rest/in transit, pseudonymization

## License

Proprietary - Wildframe Team