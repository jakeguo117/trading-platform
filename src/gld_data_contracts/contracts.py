"""Typed, immutable result contracts for GLD data-contract validation.

The validators accept ordinary JSON-domain objects, close and normalize their
schemas, then construct these copy-safe values.  No class in this module is a
decision result and none authorizes broker, provider, or order activity.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import InitVar, dataclass, field
import re
from types import MappingProxyType
from typing import Mapping

from gld_simulation.canonical import canonical_json_bytes, canonical_json_sha256


ENTRY_FACT_BUNDLE_SCHEMA_VERSION = "ENTRY_FACT_BUNDLE_V1"
SOURCE_QUALIFICATION_RECEIPT_SCHEMA_VERSION = (
    "SOURCE_QUALIFICATION_RECEIPT_V1"
)
DATA_QUALIFICATION_RESULT_SCHEMA_VERSION = "DATA_QUALIFICATION_RESULT_V1"
CARRIER_EVIDENCE_BUNDLE_SCHEMA_VERSION = "CARRIER_EVIDENCE_BUNDLE_V1"
_CARRIER_EVIDENCE_CLASSIFICATIONS = frozenset(
    {"SYNTHETIC_ONLY", "HISTORICAL_RESEARCH_CANDIDATE"}
)

STRUCTURALLY_VALID_SYNTHETIC = "STRUCTURALLY_VALID_SYNTHETIC"
DATA_QUALIFIED = "DATA_QUALIFIED"
DATA_NOT_QUALIFIED = "DATA_NOT_QUALIFIED"
DATA_QUALIFICATION_STATUSES = frozenset(
    {
        STRUCTURALLY_VALID_SYNTHETIC,
        DATA_QUALIFIED,
        DATA_NOT_QUALIFIED,
    }
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_CARRIER_EVIDENCE_NORMALIZED_KEYS = frozenset(
    {
        "carrier_id",
        "evidence_receipt_id",
        "episode_cohort_id",
        "entry_clock_id",
        "exit_clock_id",
        "cost_model_version",
        "rule_version",
        "episodes",
        "coverage",
        "splits",
        "distribution_binding",
        "expected_net_return_on_entry_debit_ppm",
        "uncertainty_method",
        "full_kelly_ppm",
        "robust_full_kelly_ppm",
        "half_kelly_ppm",
        "estimator_version",
        "fee_schedule_sha256",
        "exit_policy_sha256",
        "input_sha256",
        "evidence_sha256",
    }
)
_CARRIER_EVIDENCE_BUNDLE_NORMALIZED_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "scope",
        "underlying",
        "rule_package_sha256",
        "estimator_package_sha256",
        "carriers",
        "content_hashes",
    }
)
_DISTRIBUTION_BINDING_KEYS = frozenset(
    {
        "distribution_id",
        "return_unit",
        "episode_set_sha256",
        "distribution_sha256",
    }
)
_VALIDATED_CONTRACT_SEAL = object()


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _mutable_json_copy(value: object) -> object:
    """Copy a JSON-domain tree, thawing read-only mappings and tuples."""

    if isinstance(value, Mapping):
        return {
            key: _mutable_json_copy(item)
            for key, item in value.items()
        }
    if type(value) in {list, tuple}:
        return [_mutable_json_copy(item) for item in value]
    return deepcopy(value)


def _freeze_json(value: object) -> object:
    """Recursively freeze an already validated JSON-domain tree."""

    if type(value) is dict:
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if type(value) is list:
        return tuple(_freeze_json(item) for item in value)
    return value


def _normalized_json_document(value: object, reason_code: str) -> dict[str, object]:
    """Return an isolated JSON object or fail with one typed-seal reason."""

    try:
        copied = _mutable_json_copy(value)
        if type(copied) is not dict:
            raise TypeError("normalized document must be an object")
        canonical_json_bytes(copied)
    except (TypeError, ValueError, RecursionError, MemoryError) as exc:
        raise DataContractError(reason_code) from exc
    return copied


class DataContractError(ValueError):
    """Fail-closed contract error with a stable ASCII reason code."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        if (
            type(reason_code) is not str
            or not reason_code
            or not reason_code.isascii()
        ):
            raise ValueError("reason_code must be non-empty ASCII")
        self.reason_code = reason_code
        self.detail = detail
        message = reason_code if detail is None else f"{reason_code}: {detail}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SourceQualificationReceiptV1:
    receipt_id: str
    provider_id: str
    provider_version: str
    evidence_kind: str
    entitlement_status: str
    field_coverage_ppm: int
    complete_universe_supported: bool
    event_time_semantics: str
    receive_time_semantics: str
    atomic_snapshot_supported: bool
    synchronization_max_skew_ns: int
    effective_from_utc_ns: int
    effective_to_utc_ns: int | None
    covered_domains: tuple[str, ...]
    evidence_sha256: str
    receipt_sha256: str
    _validation_seal: InitVar[object]

    @property
    def schema_version(self) -> str:
        return SOURCE_QUALIFICATION_RECEIPT_SCHEMA_VERSION

    def __post_init__(self, _validation_seal: object) -> None:
        if (
            _validation_seal is not _VALIDATED_CONTRACT_SEAL
            or _SHA256_RE.fullmatch(self.evidence_sha256) is None
            or _SHA256_RE.fullmatch(self.receipt_sha256) is None
            or canonical_json_sha256(self.as_dict()) != self.receipt_sha256
        ):
            raise DataContractError("SOURCE_RECEIPT_TYPED_SEAL_INVALID")

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "evidence_kind": self.evidence_kind,
            "entitlement_status": self.entitlement_status,
            "field_coverage_ppm": self.field_coverage_ppm,
            "complete_universe_supported": self.complete_universe_supported,
            "event_time_semantics": self.event_time_semantics,
            "receive_time_semantics": self.receive_time_semantics,
            "atomic_snapshot_supported": self.atomic_snapshot_supported,
            "synchronization_max_skew_ns": self.synchronization_max_skew_ns,
            "effective_from_utc_ns": self.effective_from_utc_ns,
            "effective_to_utc_ns": self.effective_to_utc_ns,
            "covered_domains": list(self.covered_domains),
            "evidence_sha256": self.evidence_sha256,
        }


@dataclass(frozen=True, slots=True)
class DataQualificationResultV1:
    status: str
    reason_codes: tuple[str, ...]
    input_sha256: str
    trusted_source_receipt_set_sha256: str
    qualification_sha256: str
    _validation_seal: InitVar[object]

    def __post_init__(self, _validation_seal: object) -> None:
        body = {
            "schema_version": DATA_QUALIFICATION_RESULT_SCHEMA_VERSION,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "input_sha256": self.input_sha256,
            "trusted_source_receipt_set_sha256": (
                self.trusted_source_receipt_set_sha256
            ),
        }
        if (
            _validation_seal is not _VALIDATED_CONTRACT_SEAL
            or self.status not in DATA_QUALIFICATION_STATUSES
            or not self.reason_codes
            or tuple(sorted(set(self.reason_codes))) != self.reason_codes
            or any(_REASON_RE.fullmatch(item) is None for item in self.reason_codes)
            or _SHA256_RE.fullmatch(self.input_sha256) is None
            or _SHA256_RE.fullmatch(self.trusted_source_receipt_set_sha256) is None
            or _SHA256_RE.fullmatch(self.qualification_sha256) is None
            or canonical_json_sha256(body) != self.qualification_sha256
        ):
            raise DataContractError("DATA_QUALIFICATION_STATUS_INVALID")

    @property
    def schema_version(self) -> str:
        return DATA_QUALIFICATION_RESULT_SCHEMA_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "input_sha256": self.input_sha256,
            "trusted_source_receipt_set_sha256": (
                self.trusted_source_receipt_set_sha256
            ),
            "qualification_sha256": self.qualification_sha256,
        }


@dataclass(frozen=True, slots=True)
class EntryFactBundleV1:
    bundle_id: str
    classification: str
    trading_date: str
    cutoff_utc_ns: int
    entry_bundle_sha256: str
    canonical_bytes: bytes
    qualification: DataQualificationResultV1
    source_qualification_receipts: tuple[SourceQualificationReceiptV1, ...]
    _normalized_document: Mapping[str, object] = field(repr=False)
    _validation_seal: InitVar[object]

    @property
    def schema_version(self) -> str:
        return ENTRY_FACT_BUNDLE_SCHEMA_VERSION

    def __post_init__(self, _validation_seal: object) -> None:
        normalized = _normalized_json_document(
            self._normalized_document,
            "ENTRY_TYPED_SEAL_INVALID",
        )
        normalized_receipts = normalized.get("source_qualification_receipts")
        if (
            _validation_seal is not _VALIDATED_CONTRACT_SEAL
            or type(self.qualification) is not DataQualificationResultV1
            or type(self.source_qualification_receipts) is not tuple
            or any(
                type(item) is not SourceQualificationReceiptV1
                for item in self.source_qualification_receipts
            )
            or type(normalized_receipts) is not list
            or normalized_receipts
            != [item.as_dict() for item in self.source_qualification_receipts]
            or canonical_json_bytes(normalized) != self.canonical_bytes
            or canonical_json_sha256(normalized) != self.entry_bundle_sha256
            or self.qualification.input_sha256 != self.entry_bundle_sha256
            or normalized.get("bundle_id") != self.bundle_id
            or normalized.get("classification") != self.classification
            or normalized.get("trading_date") != self.trading_date
            or normalized.get("cutoff_utc_ns") != self.cutoff_utc_ns
        ):
            raise DataContractError("ENTRY_TYPED_SEAL_INVALID")
        object.__setattr__(
            self,
            "_normalized_document",
            _freeze_json(normalized),
        )

    @property
    def normalized_document(self) -> dict[str, object]:
        """Return an isolated copy so callers cannot mutate validated facts."""

        normalized = _mutable_json_copy(self._normalized_document)
        if type(normalized) is not dict:  # pragma: no cover - sealed invariant
            raise AssertionError("sealed entry facts are not an object")
        return normalized


@dataclass(frozen=True, slots=True)
class CarrierEvidenceV1:
    carrier_id: str
    evidence_receipt_id: str
    evidence_sha256: str
    distribution_id: str
    distribution_sha256: str
    input_sha256: str
    expected_net_return_on_entry_debit_ppm: int
    full_kelly_ppm: int
    robust_full_kelly_ppm: int
    half_kelly_ppm: int
    _normalized_document: Mapping[str, object] = field(repr=False)
    _validation_seal: InitVar[object]

    def __post_init__(self, _validation_seal: object) -> None:
        reason = "CARRIER_EVIDENCE_TYPED_SEAL_INVALID"
        normalized = _normalized_json_document(
            self._normalized_document,
            reason,
        )
        distribution = normalized.get("distribution_binding")
        if type(distribution) is not dict:
            raise DataContractError(reason)
        unsigned = dict(normalized)
        declared_evidence_sha256 = unsigned.pop("evidence_sha256", None)
        if (
            _validation_seal is not _VALIDATED_CONTRACT_SEAL
            or frozenset(normalized) != _CARRIER_EVIDENCE_NORMALIZED_KEYS
            or frozenset(distribution) != _DISTRIBUTION_BINDING_KEYS
            or type(self.carrier_id) is not str
            or self.carrier_id not in {"BCS0", "LC0"}
            or type(self.evidence_receipt_id) is not str
            or type(self.distribution_id) is not str
            or normalized.get("carrier_id") != self.carrier_id
            or normalized.get("evidence_receipt_id")
            != self.evidence_receipt_id
            or declared_evidence_sha256 != self.evidence_sha256
            or distribution.get("distribution_id") != self.distribution_id
            or distribution.get("distribution_sha256")
            != self.distribution_sha256
            or normalized.get("input_sha256") != self.input_sha256
            or normalized.get("expected_net_return_on_entry_debit_ppm")
            != self.expected_net_return_on_entry_debit_ppm
            or normalized.get("full_kelly_ppm") != self.full_kelly_ppm
            or normalized.get("robust_full_kelly_ppm")
            != self.robust_full_kelly_ppm
            or normalized.get("half_kelly_ppm") != self.half_kelly_ppm
            or type(self.expected_net_return_on_entry_debit_ppm) is not int
            or type(self.full_kelly_ppm) is not int
            or type(self.robust_full_kelly_ppm) is not int
            or type(self.half_kelly_ppm) is not int
            or not _is_sha256(self.evidence_sha256)
            or not _is_sha256(self.distribution_sha256)
            or not _is_sha256(self.input_sha256)
            or distribution.get("return_unit") != "ON_ENTRY_DEBIT_PPM"
            or not _is_sha256(distribution.get("episode_set_sha256"))
            or canonical_json_sha256(unsigned) != self.evidence_sha256
        ):
            raise DataContractError(reason)
        object.__setattr__(
            self,
            "_normalized_document",
            _freeze_json(normalized),
        )

    @property
    def normalized_document(self) -> dict[str, object]:
        normalized = _mutable_json_copy(self._normalized_document)
        if type(normalized) is not dict:  # pragma: no cover - sealed invariant
            raise AssertionError("sealed carrier evidence is not an object")
        return normalized


@dataclass(frozen=True, slots=True)
class CarrierEvidenceBundleV1:
    classification: str
    bundle_sha256: str
    canonical_bytes: bytes
    carriers: tuple[CarrierEvidenceV1, ...]
    _normalized_document: Mapping[str, object] = field(repr=False)
    _validation_seal: InitVar[object]

    @property
    def schema_version(self) -> str:
        return CARRIER_EVIDENCE_BUNDLE_SCHEMA_VERSION

    def __post_init__(self, _validation_seal: object) -> None:
        reason = "CARRIER_EVIDENCE_BUNDLE_TYPED_SEAL_INVALID"
        normalized = _normalized_json_document(
            self._normalized_document,
            reason,
        )
        normalized_carriers = normalized.get("carriers")
        content_hashes = normalized.get("content_hashes")
        if (
            _validation_seal is not _VALIDATED_CONTRACT_SEAL
            or type(normalized_carriers) is not list
            or type(content_hashes) is not dict
            or type(self.carriers) is not tuple
            or len(self.carriers) != 2
            or any(type(item) is not CarrierEvidenceV1 for item in self.carriers)
        ):
            raise DataContractError(reason)
        carrier_ids = tuple(item.carrier_id for item in self.carriers)
        carrier_documents = [item.normalized_document for item in self.carriers]
        expected_content_hashes = {
            item.carrier_id: item.evidence_sha256 for item in self.carriers
        }
        if (
            frozenset(normalized)
            != _CARRIER_EVIDENCE_BUNDLE_NORMALIZED_KEYS
            or type(self.classification) is not str
            or self.classification not in _CARRIER_EVIDENCE_CLASSIFICATIONS
            or normalized.get("schema_version") != self.schema_version
            or normalized.get("classification") != self.classification
            or normalized.get("scope") != "GLD_ENTRY_CARRIER_EVIDENCE_ONLY"
            or normalized.get("underlying") != "GLD"
            or not _is_sha256(normalized.get("rule_package_sha256"))
            or not _is_sha256(normalized.get("estimator_package_sha256"))
            or carrier_ids != ("BCS0", "LC0")
            or normalized_carriers != carrier_documents
            or frozenset(content_hashes) != frozenset({"BCS0", "LC0"})
            or content_hashes != expected_content_hashes
            or type(self.canonical_bytes) is not bytes
            or canonical_json_bytes(normalized) != self.canonical_bytes
            or not _is_sha256(self.bundle_sha256)
            or canonical_json_sha256(normalized) != self.bundle_sha256
        ):
            raise DataContractError(reason)

        independence_fields = (
            "evidence_receipt_id",
            "evidence_sha256",
            "input_sha256",
        )
        first, second = self.carriers
        if any(
            getattr(first, field) == getattr(second, field)
            for field in independence_fields
        ):
            raise DataContractError(reason)
        first_distribution = first.normalized_document["distribution_binding"]
        second_distribution = second.normalized_document["distribution_binding"]
        if (
            type(first_distribution) is not dict
            or type(second_distribution) is not dict
        ):
            raise DataContractError(reason)
        if any(
            first_distribution[field] == second_distribution[field]
            for field in (
                "distribution_id",
                "distribution_sha256",
                "episode_set_sha256",
            )
        ):
            raise DataContractError(reason)

        object.__setattr__(
            self,
            "_normalized_document",
            _freeze_json(normalized),
        )

    @property
    def normalized_document(self) -> dict[str, object]:
        normalized = _mutable_json_copy(self._normalized_document)
        if type(normalized) is not dict:  # pragma: no cover - sealed invariant
            raise AssertionError("sealed carrier evidence bundle is not an object")
        return normalized


__all__ = [
    "CARRIER_EVIDENCE_BUNDLE_SCHEMA_VERSION",
    "CarrierEvidenceBundleV1",
    "CarrierEvidenceV1",
    "DATA_NOT_QUALIFIED",
    "DATA_QUALIFICATION_RESULT_SCHEMA_VERSION",
    "DATA_QUALIFICATION_STATUSES",
    "DATA_QUALIFIED",
    "DataContractError",
    "DataQualificationResultV1",
    "ENTRY_FACT_BUNDLE_SCHEMA_VERSION",
    "EntryFactBundleV1",
    "SOURCE_QUALIFICATION_RECEIPT_SCHEMA_VERSION",
    "STRUCTURALLY_VALID_SYNTHETIC",
    "SourceQualificationReceiptV1",
]
