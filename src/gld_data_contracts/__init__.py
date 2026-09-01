"""Provider-neutral GLD data contracts and deterministic technical facts."""

from .contracts import (
    CarrierEvidenceBundleV1,
    DataContractError,
    DataQualificationResultV1,
    EntryFactBundleV1,
    SourceQualificationReceiptV1,
)
from .legacy_adapter import adapt_legacy_synthetic_bundle
from .technical import (
    RequiredDailyTechnicalFactsV1,
    TechnicalFactError,
    derive_required_technical_facts,
    materialize_bcs_pair_facts,
)
from .validation import validate_carrier_evidence, validate_entry_bundle


__all__ = [
    "CarrierEvidenceBundleV1",
    "DataContractError",
    "DataQualificationResultV1",
    "EntryFactBundleV1",
    "RequiredDailyTechnicalFactsV1",
    "SourceQualificationReceiptV1",
    "TechnicalFactError",
    "adapt_legacy_synthetic_bundle",
    "derive_required_technical_facts",
    "materialize_bcs_pair_facts",
    "validate_carrier_evidence",
    "validate_entry_bundle",
]
