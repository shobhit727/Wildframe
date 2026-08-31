"""Jurisdiction definitions for compliance policies."""

from enum import Enum


class Jurisdiction(str, Enum):
    """Supported jurisdictions for compliance policies.

    Each jurisdiction has its own regulatory framework that Wildframe must comply with.
    """

    # European Union
    EU = "EU"

    # United States (federal + state-level)
    US = "US"
    US_CA = "US-CA"  # CCPA/CPRA
    US_VA = "US-VA"  # VCDPA
    US_CO = "US-CO"  # CPA
    US_CT = "US-CT"  # CTDPA
    US_UT = "US-UT"  # UCPA
    US_TX = "US-TX"  # TDPSA
    US_OR = "US-OR"  # OCPA
    US_MT = "US-MT"  # MTCDPA
    US_DE = "US-DE"  # DPDPA
    US_NH = "US-NH"  # NHPPA
    US_NJ = "US-NJ"  # NJDPA
    US_MN = "US-MN"  # MCDPA
    US_MD = "US-MD"  # MODPA
    US_NE = "US-NE"  # NEDPA
    US_RI = "US-RI"  # RIDTPPA
    US_KY = "US-KY"  # KCDPA

    # India
    IN = "IN"

    # Canada
    CA = "CA"
    CA_QC = "CA-QC"  # Law 25

    # Brazil
    BR = "BR"

    # Australia
    AU = "AU"

    # Japan
    JP = "JP"

    # South Korea
    KR = "KR"

    # Singapore
    SG = "SG"

    # Global baseline (minimum standards applied everywhere)
    GLOBAL = "GLOBAL"

    @property
    def parent(self) -> "Jurisdiction | None":
        """Get the parent jurisdiction for hierarchical lookups."""
        parent_map = {
            Jurisdiction.US_CA: Jurisdiction.US,
            Jurisdiction.US_VA: Jurisdiction.US,
            Jurisdiction.US_CO: Jurisdiction.US,
            Jurisdiction.US_CT: Jurisdiction.US,
            Jurisdiction.US_UT: Jurisdiction.US,
            Jurisdiction.US_TX: Jurisdiction.US,
            Jurisdiction.US_OR: Jurisdiction.US,
            Jurisdiction.US_MT: Jurisdiction.US,
            Jurisdiction.US_DE: Jurisdiction.US,
            Jurisdiction.US_NH: Jurisdiction.US,
            Jurisdiction.US_NJ: Jurisdiction.US,
            Jurisdiction.US_MN: Jurisdiction.US,
            Jurisdiction.US_MD: Jurisdiction.US,
            Jurisdiction.US_NE: Jurisdiction.US,
            Jurisdiction.US_RI: Jurisdiction.US,
            Jurisdiction.US_KY: Jurisdiction.US,
            Jurisdiction.CA_QC: Jurisdiction.CA,
        }
        return parent_map.get(self)

    @property
    def display_name(self) -> str:
        """Human-readable name for the jurisdiction."""
        names = {
            Jurisdiction.EU: "European Union",
            Jurisdiction.US: "United States (Federal)",
            Jurisdiction.US_CA: "California (CCPA/CPRA)",
            Jurisdiction.US_VA: "Virginia (VCDPA)",
            Jurisdiction.US_CO: "Colorado (CPA)",
            Jurisdiction.US_CT: "Connecticut (CTDPA)",
            Jurisdiction.US_UT: "Utah (UCPA)",
            Jurisdiction.US_TX: "Texas (TDPSA)",
            Jurisdiction.US_OR: "Oregon (OCPA)",
            Jurisdiction.US_MT: "Montana (MTCDPA)",
            Jurisdiction.US_DE: "Delaware (DPDPA)",
            Jurisdiction.US_NH: "New Hampshire (NHPPA)",
            Jurisdiction.US_NJ: "New Jersey (NJDPA)",
            Jurisdiction.US_MN: "Minnesota (MCDPA)",
            Jurisdiction.US_MD: "Maryland (MODPA)",
            Jurisdiction.US_NE: "Nebraska (NEDPA)",
            Jurisdiction.US_RI: "Rhode Island (RIDTPPA)",
            Jurisdiction.US_KY: "Kentucky (KCDPA)",
            Jurisdiction.IN: "India",
            Jurisdiction.CA: "Canada (Federal)",
            Jurisdiction.CA_QC: "Quebec (Law 25)",
            Jurisdiction.BR: "Brazil (LGPD)",
            Jurisdiction.AU: "Australia (Privacy Act)",
            Jurisdiction.JP: "Japan (APPI)",
            Jurisdiction.KR: "South Korea (PIPA)",
            Jurisdiction.SG: "Singapore (PDPA)",
            Jurisdiction.GLOBAL: "Global Baseline",
        }
        return names.get(self, self.value)

    @property
    def regulations(self) -> list[str]:
        """List of applicable regulations for this jurisdiction."""
        regs = {
            Jurisdiction.EU: ["GDPR", "ePrivacy", "AVMS Directive", "DSA", "DMA"],
            Jurisdiction.US: ["FTC Act", "COPPA", "HIPAA", "GLBA"],
            Jurisdiction.US_CA: ["CCPA", "CPRA"],
            Jurisdiction.US_VA: ["VCDPA"],
            Jurisdiction.US_CO: ["CPA"],
            Jurisdiction.US_CT: ["CTDPA"],
            Jurisdiction.US_UT: ["UCPA"],
            Jurisdiction.US_TX: ["TDPSA"],
            Jurisdiction.US_OR: ["OCPA"],
            Jurisdiction.US_MT: ["MTCDPA"],
            Jurisdiction.US_DE: ["DPDPA"],
            Jurisdiction.US_NH: ["NHPPA"],
            Jurisdiction.US_NJ: ["NJDPA"],
            Jurisdiction.US_MN: ["MCDPA"],
            Jurisdiction.US_MD: ["MODPA"],
            Jurisdiction.US_NE: ["NEDPA"],
            Jurisdiction.US_RI: ["RIDTPPA"],
            Jurisdiction.US_KY: ["KCDPA"],
            Jurisdiction.IN: ["DPDP Act", "IT Act", "OTT Rules"],
            Jurisdiction.CA: ["PIPEDA"],
            Jurisdiction.CA_QC: ["Law 25"],
            Jurisdiction.BR: ["LGPD"],
            Jurisdiction.AU: ["Privacy Act 1988", "Notifiable Data Breaches Scheme"],
            Jurisdiction.JP: ["APPI"],
            Jurisdiction.KR: ["PIPA"],
            Jurisdiction.SG: ["PDPA"],
            Jurisdiction.GLOBAL: ["ISO 27001", "SOC 2", "NIST CSF"],
        }
        return regs.get(self, [])
