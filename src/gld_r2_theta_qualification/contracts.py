"""Closed, deterministic contracts for R2 theta qualification infrastructure.

This module deliberately contains no market-data, provider, or broker access.  It
only supplies package-local errors, canonical JSON adapters, and the non-cyclic
identity chain that binds each R2 research stage to its frozen inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Iterable

from gld_entry_decision_f0.canonical import (
    canonical_json_bytes as _entry_canonical_json_bytes,
)
from gld_entry_decision_f0.errors import EntryDecisionF0Error
from gld_entry_decision_f0.policy import (
    ENTRY_POLICY_SCHEMA_VERSION,
    EntryPolicyF0,
    validate_entry_policy_f0,
)


CODE_RUNTIME_BINDING_SCHEMA_VERSION = "R2_CODE_RUNTIME_BINDING_V1"
R1_FIXED_POLICY_PROJECTION_SCHEMA_VERSION = "R1_FIXED_POLICY_PROJECTION_V1"

_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_RE = re.compile(r"[A-Z0-9][A-Z0-9_.:-]{0,127}\Z")
_CODE_RUNTIME_KEYS = frozenset(
    {
        "schema_version",
        "code_package_sha256",
        "runtime_id",
        "runtime_sha256",
    }
)
_CARRIER_MODES = frozenset({"DUAL_PREFERENCE", "LC0_ONLY", "BCS0_ONLY"})
_MACHINE_TERMINALS = frozenset(
    {
        "INSUFFICIENT_EVIDENCE",
        "NO_QUALIFIED_POLICY",
        "QUALIFIED_POLICY_PENDING_OWNER",
    }
)
_OWNER_DECISIONS = frozenset({"APPROVE", "REJECT"})
_R1_FIXED_PROJECTION_KEYS = frozenset(
    {
        "schema_version",
        "source_entry_policy_schema_version",
        "source_policy_id",
        "source_management_policy_id",
        "source_management_policy_sha256",
        "selector_contract_version",
        "selector_delta_sources",
        "selector_requires_coarse_fine_identity",
        "lc0_delta_min_ppm",
        "lc0_delta_target_ppm",
        "lc0_delta_max_ppm",
        "lc0_selector_tie_break_order",
        "bcs0_long_delta_min_ppm",
        "bcs0_long_delta_target_ppm",
        "bcs0_long_delta_max_ppm",
        "bcs0_short_delta_min_ppm",
        "bcs0_short_delta_target_ppm",
        "bcs0_short_delta_max_ppm",
        "bcs0_selector_tie_break_order",
        "preference_contract_version",
        "preference_tie_break_order",
        "backup_requires_fresh_snapshot_and_rerun",
        "max_hold_xnys_sessions",
        "invalidation_confirmation_sessions",
        "invalidation_latched",
        "expiry_safety_calendar_days",
        "expiry_safety_inclusive",
        "quantity_units",
        "excluded_dimensions",
    }
)
_SELECTOR_DELTA_SOURCES = ("COARSE_DELTA_PPM", "FINE_DELTA_PPM")
_LC0_SELECTOR_TIE_BREAK_ORDER = (
    "EXPIRY_ASC",
    "TARGET_DISTANCE_ASC",
    "STRIKE_DESC",
    "CONTRACT_ID_ASC",
)
_BCS0_SELECTOR_TIE_BREAK_ORDER = (
    "EXPIRY_ASC",
    "TOTAL_TARGET_DISTANCE_ASC",
    "LONG_TARGET_DISTANCE_ASC",
    "SHORT_TARGET_DISTANCE_ASC",
    "LONG_STRIKE_DESC",
    "SHORT_STRIKE_DESC",
    "LONG_CONTRACT_ID_ASC",
    "SHORT_CONTRACT_ID_ASC",
)
_PREFERENCE_TIE_BREAK_ORDER = (
    "EXPECTED_TOTAL_NET_PROFIT_DESC",
    "TOTAL_PLANNED_LOSS_ASC",
    "CURRENT_ENTRY_CASH_USAGE_ASC",
    "EXACT_TIE_LC0_FIRST",
)
_EXCLUDED_R1_DIMENSIONS = ("ACCOUNT_SIZING", "GATE", "HARD_STOP")


class R2QualificationError(ValueError):
    """Fail-closed R2 error carrying one stable ASCII reason code."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        if type(reason_code) is not str or _REASON_RE.fullmatch(reason_code) is None:
            raise ValueError("reason_code must be stable uppercase ASCII")
        if detail is not None and (type(detail) is not str or not detail.isascii()):
            raise ValueError("detail must be ASCII text")
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(reason_code if detail is None else f"{reason_code}: {detail}")


def canonical_json_bytes(value: object) -> bytes:
    """Return repository-compatible canonical JSON with an R2-local error type."""

    try:
        return _entry_canonical_json_bytes(value)
    except EntryDecisionF0Error as exc:
        raise R2QualificationError(exc.reason_code, exc.detail) from exc


def canonical_sha256(value: object) -> str:
    """Return lowercase SHA-256 over canonical JSON bytes."""

    return sha256(canonical_json_bytes(value)).hexdigest()


# Keep the repository's established spelling available to downstream modules.
canonical_json_sha256 = canonical_sha256


def _require_sha256(value: object, reason_code: str = "R2_HASH_INVALID") -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise R2QualificationError(reason_code)
    return value


def _require_identifier(value: object, reason_code: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise R2QualificationError(reason_code)
    return value


def _require_hash_tuple(
    values: Iterable[str],
    *,
    reason_code: str,
    minimum: int = 1,
    maximum: int = 16,
) -> tuple[str, ...]:
    if type(values) is not tuple:
        raise R2QualificationError(reason_code)
    validated = tuple(_require_sha256(value, reason_code) for value in values)
    if not minimum <= len(validated) <= maximum or len(set(validated)) != len(
        validated
    ):
        raise R2QualificationError(reason_code)
    return validated


@dataclass(frozen=True, slots=True)
class CodeRuntimeBindingV1:
    """Exact code-package and runtime identity bound into every method freeze."""

    schema_version: str
    code_package_sha256: str
    runtime_id: str
    runtime_sha256: str
    binding_sha256: str
    canonical_bytes: bytes

    @classmethod
    def from_document(cls, document: object) -> "CodeRuntimeBindingV1":
        if type(document) is not dict or frozenset(document) != _CODE_RUNTIME_KEYS:
            raise R2QualificationError("R2_CODE_RUNTIME_SCHEMA_INVALID")
        canonical = canonical_json_bytes(document)
        if document["schema_version"] != CODE_RUNTIME_BINDING_SCHEMA_VERSION:
            raise R2QualificationError("R2_CODE_RUNTIME_VERSION_INVALID")
        code_hash = _require_sha256(
            document["code_package_sha256"], "R2_CODE_PACKAGE_HASH_INVALID"
        )
        runtime_id = _require_identifier(
            document["runtime_id"], "R2_RUNTIME_ID_INVALID"
        )
        runtime_hash = _require_sha256(
            document["runtime_sha256"], "R2_RUNTIME_HASH_INVALID"
        )
        return cls(
            schema_version=CODE_RUNTIME_BINDING_SCHEMA_VERSION,
            code_package_sha256=code_hash,
            runtime_id=runtime_id,
            runtime_sha256=runtime_hash,
            binding_sha256=sha256(canonical).hexdigest(),
            canonical_bytes=canonical,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "code_package_sha256": self.code_package_sha256,
            "runtime_id": self.runtime_id,
            "runtime_sha256": self.runtime_sha256,
        }


def _exact_string_tuple(
    value: object,
    expected: tuple[str, ...],
    reason_code: str,
) -> tuple[str, ...]:
    if type(value) is not list or tuple(value) != expected:
        raise R2QualificationError(reason_code)
    return expected


def _bounded_int(
    value: object,
    *,
    minimum: int,
    maximum: int,
    reason_code: str,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise R2QualificationError(reason_code)
    return value


@dataclass(frozen=True, slots=True)
class R1FixedPolicyProjectionV1:
    """Closed R1 semantics held fixed while R2 varies Gate and Hard Stop.

    Gate, Hard Stop, and account-sizing fields are deliberately absent from the
    projection.  Their exclusion is itself canonical content so a registry
    consumer can distinguish an intentional research dimension from omission.
    """

    schema_version: str
    source_entry_policy_schema_version: str
    source_policy_id: str
    source_management_policy_id: str
    source_management_policy_sha256: str
    selector_contract_version: str
    selector_delta_sources: tuple[str, ...]
    selector_requires_coarse_fine_identity: bool
    lc0_delta_min_ppm: int
    lc0_delta_target_ppm: int
    lc0_delta_max_ppm: int
    lc0_selector_tie_break_order: tuple[str, ...]
    bcs0_long_delta_min_ppm: int
    bcs0_long_delta_target_ppm: int
    bcs0_long_delta_max_ppm: int
    bcs0_short_delta_min_ppm: int
    bcs0_short_delta_target_ppm: int
    bcs0_short_delta_max_ppm: int
    bcs0_selector_tie_break_order: tuple[str, ...]
    preference_contract_version: str
    preference_tie_break_order: tuple[str, ...]
    backup_requires_fresh_snapshot_and_rerun: bool
    max_hold_xnys_sessions: int
    invalidation_confirmation_sessions: int
    invalidation_latched: bool
    expiry_safety_calendar_days: int
    expiry_safety_inclusive: bool
    quantity_units: int
    excluded_dimensions: tuple[str, ...]
    projection_sha256: str
    canonical_bytes: bytes

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_entry_policy_schema_version": self.source_entry_policy_schema_version,
            "source_policy_id": self.source_policy_id,
            "source_management_policy_id": self.source_management_policy_id,
            "source_management_policy_sha256": self.source_management_policy_sha256,
            "selector_contract_version": self.selector_contract_version,
            "selector_delta_sources": list(self.selector_delta_sources),
            "selector_requires_coarse_fine_identity": self.selector_requires_coarse_fine_identity,
            "lc0_delta_min_ppm": self.lc0_delta_min_ppm,
            "lc0_delta_target_ppm": self.lc0_delta_target_ppm,
            "lc0_delta_max_ppm": self.lc0_delta_max_ppm,
            "lc0_selector_tie_break_order": list(self.lc0_selector_tie_break_order),
            "bcs0_long_delta_min_ppm": self.bcs0_long_delta_min_ppm,
            "bcs0_long_delta_target_ppm": self.bcs0_long_delta_target_ppm,
            "bcs0_long_delta_max_ppm": self.bcs0_long_delta_max_ppm,
            "bcs0_short_delta_min_ppm": self.bcs0_short_delta_min_ppm,
            "bcs0_short_delta_target_ppm": self.bcs0_short_delta_target_ppm,
            "bcs0_short_delta_max_ppm": self.bcs0_short_delta_max_ppm,
            "bcs0_selector_tie_break_order": list(self.bcs0_selector_tie_break_order),
            "preference_contract_version": self.preference_contract_version,
            "preference_tie_break_order": list(self.preference_tie_break_order),
            "backup_requires_fresh_snapshot_and_rerun": (
                self.backup_requires_fresh_snapshot_and_rerun
            ),
            "max_hold_xnys_sessions": self.max_hold_xnys_sessions,
            "invalidation_confirmation_sessions": self.invalidation_confirmation_sessions,
            "invalidation_latched": self.invalidation_latched,
            "expiry_safety_calendar_days": self.expiry_safety_calendar_days,
            "expiry_safety_inclusive": self.expiry_safety_inclusive,
            "quantity_units": self.quantity_units,
            "excluded_dimensions": list(self.excluded_dimensions),
        }


def validate_r1_fixed_policy_projection(
    document: object,
) -> R1FixedPolicyProjectionV1:
    """Validate exact fixed R1 content; added candidate dimensions fail closed."""

    if type(document) is not dict or frozenset(document) != _R1_FIXED_PROJECTION_KEYS:
        raise R2QualificationError("R1_FIXED_POLICY_PROJECTION_SCHEMA_INVALID")
    canonical = canonical_json_bytes(document)
    if document["schema_version"] != R1_FIXED_POLICY_PROJECTION_SCHEMA_VERSION:
        raise R2QualificationError("R1_FIXED_POLICY_PROJECTION_VERSION_INVALID")
    if document["source_entry_policy_schema_version"] != ENTRY_POLICY_SCHEMA_VERSION:
        raise R2QualificationError("R1_SOURCE_POLICY_VERSION_INVALID")
    source_policy_id = _require_identifier(
        document["source_policy_id"], "R1_SOURCE_POLICY_ID_INVALID"
    )
    management_policy_id = _require_identifier(
        document["source_management_policy_id"],
        "R1_MANAGEMENT_POLICY_ID_INVALID",
    )
    management_hash = _require_sha256(
        document["source_management_policy_sha256"],
        "R1_MANAGEMENT_POLICY_HASH_INVALID",
    )
    if document["selector_contract_version"] != "ENTRY_F0_SELECTORS_V1":
        raise R2QualificationError("R1_SELECTOR_CONTRACT_INVALID")
    selector_sources = _exact_string_tuple(
        document["selector_delta_sources"],
        _SELECTOR_DELTA_SOURCES,
        "R1_SELECTOR_CONTRACT_INVALID",
    )
    if document["selector_requires_coarse_fine_identity"] is not True:
        raise R2QualificationError("R1_SELECTOR_CONTRACT_INVALID")

    numeric_names = (
        "lc0_delta_min_ppm",
        "lc0_delta_target_ppm",
        "lc0_delta_max_ppm",
        "bcs0_long_delta_min_ppm",
        "bcs0_long_delta_target_ppm",
        "bcs0_long_delta_max_ppm",
        "bcs0_short_delta_min_ppm",
        "bcs0_short_delta_target_ppm",
        "bcs0_short_delta_max_ppm",
    )
    numeric = {
        name: _bounded_int(
            document[name],
            minimum=0,
            maximum=1_000_000,
            reason_code="R1_FIXED_POLICY_DELTA_INVALID",
        )
        for name in numeric_names
    }
    for prefix in ("lc0_delta", "bcs0_long_delta", "bcs0_short_delta"):
        if not (
            numeric[f"{prefix}_min_ppm"]
            <= numeric[f"{prefix}_target_ppm"]
            <= numeric[f"{prefix}_max_ppm"]
        ):
            raise R2QualificationError("R1_FIXED_POLICY_DELTA_INVALID")
    lc_order = _exact_string_tuple(
        document["lc0_selector_tie_break_order"],
        _LC0_SELECTOR_TIE_BREAK_ORDER,
        "R1_SELECTOR_CONTRACT_INVALID",
    )
    bcs_order = _exact_string_tuple(
        document["bcs0_selector_tie_break_order"],
        _BCS0_SELECTOR_TIE_BREAK_ORDER,
        "R1_SELECTOR_CONTRACT_INVALID",
    )
    if document["preference_contract_version"] != "ENTRY_F0_PREFERENCE_V1":
        raise R2QualificationError("R1_PREFERENCE_CONTRACT_INVALID")
    preference_order = _exact_string_tuple(
        document["preference_tie_break_order"],
        _PREFERENCE_TIE_BREAK_ORDER,
        "R1_PREFERENCE_CONTRACT_INVALID",
    )
    if document["backup_requires_fresh_snapshot_and_rerun"] is not True:
        raise R2QualificationError("R1_PREFERENCE_CONTRACT_INVALID")
    max_hold = _bounded_int(
        document["max_hold_xnys_sessions"],
        minimum=20,
        maximum=20,
        reason_code="R1_H20_CONTRACT_INVALID",
    )
    invalidation_sessions = _bounded_int(
        document["invalidation_confirmation_sessions"],
        minimum=2,
        maximum=2,
        reason_code="R1_INVALIDATION_CONTRACT_INVALID",
    )
    if document["invalidation_latched"] is not True:
        raise R2QualificationError("R1_INVALIDATION_CONTRACT_INVALID")
    expiry_days = _bounded_int(
        document["expiry_safety_calendar_days"],
        minimum=0,
        maximum=366,
        reason_code="R1_EXPIRY_SAFETY_INVALID",
    )
    if document["expiry_safety_inclusive"] is not True:
        raise R2QualificationError("R1_EXPIRY_SAFETY_INVALID")
    quantity_units = _bounded_int(
        document["quantity_units"],
        minimum=1,
        maximum=1,
        reason_code="R2_QUANTITY_UNITS_UNSUPPORTED",
    )
    excluded = _exact_string_tuple(
        document["excluded_dimensions"],
        _EXCLUDED_R1_DIMENSIONS,
        "R1_FIXED_POLICY_EXCLUSIONS_INVALID",
    )
    return R1FixedPolicyProjectionV1(
        schema_version=R1_FIXED_POLICY_PROJECTION_SCHEMA_VERSION,
        source_entry_policy_schema_version=ENTRY_POLICY_SCHEMA_VERSION,
        source_policy_id=source_policy_id,
        source_management_policy_id=management_policy_id,
        source_management_policy_sha256=management_hash,
        selector_contract_version="ENTRY_F0_SELECTORS_V1",
        selector_delta_sources=selector_sources,
        selector_requires_coarse_fine_identity=True,
        lc0_delta_min_ppm=numeric["lc0_delta_min_ppm"],
        lc0_delta_target_ppm=numeric["lc0_delta_target_ppm"],
        lc0_delta_max_ppm=numeric["lc0_delta_max_ppm"],
        lc0_selector_tie_break_order=lc_order,
        bcs0_long_delta_min_ppm=numeric["bcs0_long_delta_min_ppm"],
        bcs0_long_delta_target_ppm=numeric["bcs0_long_delta_target_ppm"],
        bcs0_long_delta_max_ppm=numeric["bcs0_long_delta_max_ppm"],
        bcs0_short_delta_min_ppm=numeric["bcs0_short_delta_min_ppm"],
        bcs0_short_delta_target_ppm=numeric["bcs0_short_delta_target_ppm"],
        bcs0_short_delta_max_ppm=numeric["bcs0_short_delta_max_ppm"],
        bcs0_selector_tie_break_order=bcs_order,
        preference_contract_version="ENTRY_F0_PREFERENCE_V1",
        preference_tie_break_order=preference_order,
        backup_requires_fresh_snapshot_and_rerun=True,
        max_hold_xnys_sessions=max_hold,
        invalidation_confirmation_sessions=invalidation_sessions,
        invalidation_latched=True,
        expiry_safety_calendar_days=expiry_days,
        expiry_safety_inclusive=True,
        quantity_units=quantity_units,
        excluded_dimensions=excluded,
        projection_sha256=sha256(canonical).hexdigest(),
        canonical_bytes=canonical,
    )


def build_r1_fixed_policy_projection(
    policy_input: object,
) -> R1FixedPolicyProjectionV1:
    """Project one validated R1 Entry policy onto only R2-fixed semantics."""

    try:
        if isinstance(policy_input, EntryPolicyF0):
            policy_document = policy_input.as_dict()
            if (
                canonical_json_bytes(policy_document) != policy_input.canonical_bytes
                or canonical_sha256(policy_document) != policy_input.policy_sha256
            ):
                raise R2QualificationError("R1_TYPED_POLICY_INTEGRITY_MISMATCH")
            policy = validate_entry_policy_f0(policy_document)
        else:
            policy = validate_entry_policy_f0(policy_input)
    except R2QualificationError:
        raise
    except EntryDecisionF0Error as exc:
        raise R2QualificationError("R1_ENTRY_POLICY_INVALID", exc.reason_code) from exc

    return validate_r1_fixed_policy_projection(
        {
            "schema_version": R1_FIXED_POLICY_PROJECTION_SCHEMA_VERSION,
            "source_entry_policy_schema_version": policy.schema_version,
            "source_policy_id": policy.policy_id,
            "source_management_policy_id": policy.management_policy_id,
            "source_management_policy_sha256": policy.management_policy_sha256,
            "selector_contract_version": "ENTRY_F0_SELECTORS_V1",
            "selector_delta_sources": list(_SELECTOR_DELTA_SOURCES),
            "selector_requires_coarse_fine_identity": True,
            "lc0_delta_min_ppm": policy.lc0_delta_min_ppm,
            "lc0_delta_target_ppm": policy.lc0_delta_target_ppm,
            "lc0_delta_max_ppm": policy.lc0_delta_max_ppm,
            "lc0_selector_tie_break_order": list(_LC0_SELECTOR_TIE_BREAK_ORDER),
            "bcs0_long_delta_min_ppm": policy.bcs0_long_delta_min_ppm,
            "bcs0_long_delta_target_ppm": policy.bcs0_long_delta_target_ppm,
            "bcs0_long_delta_max_ppm": policy.bcs0_long_delta_max_ppm,
            "bcs0_short_delta_min_ppm": policy.bcs0_short_delta_min_ppm,
            "bcs0_short_delta_target_ppm": policy.bcs0_short_delta_target_ppm,
            "bcs0_short_delta_max_ppm": policy.bcs0_short_delta_max_ppm,
            "bcs0_selector_tie_break_order": list(_BCS0_SELECTOR_TIE_BREAK_ORDER),
            "preference_contract_version": "ENTRY_F0_PREFERENCE_V1",
            "preference_tie_break_order": list(_PREFERENCE_TIE_BREAK_ORDER),
            "backup_requires_fresh_snapshot_and_rerun": True,
            "max_hold_xnys_sessions": 20,
            "invalidation_confirmation_sessions": policy.invalidation_confirmation_sessions,
            "invalidation_latched": True,
            "expiry_safety_calendar_days": policy.expiry_safety_calendar_days,
            "expiry_safety_inclusive": True,
            "quantity_units": 1,
            "excluded_dimensions": list(_EXCLUDED_R1_DIMENSIONS),
        }
    )


def derive_base_candidate_sha256(
    *,
    candidate_id: str,
    ordinal: int,
    trend_required_count: int,
    breakout_required_count: int,
    stop_mode: str,
    hard_stop_loss_ppm: int | None,
    r1_fixed_policy_projection_sha256: str,
    quantity_units: int = 1,
) -> str:
    """Bind one base policy without any evidence or result fields."""

    _require_identifier(candidate_id, "R2_CANDIDATE_ID_INVALID")
    if type(ordinal) is not int or not 0 <= ordinal < 16:
        raise R2QualificationError("R2_CANDIDATE_ORDINAL_INVALID")
    if type(trend_required_count) is not int or trend_required_count not in {2, 3}:
        raise R2QualificationError("R2_CANDIDATE_GATE_INVALID")
    if type(breakout_required_count) is not int or breakout_required_count not in {
        1,
        2,
    }:
        raise R2QualificationError("R2_CANDIDATE_GATE_INVALID")
    if stop_mode not in {"HARD_STOP", "DISABLED_CONTROL"}:
        raise R2QualificationError("R2_CANDIDATE_STOP_MODE_INVALID")
    if stop_mode == "HARD_STOP":
        if type(hard_stop_loss_ppm) is not int or hard_stop_loss_ppm not in {
            333_333,
            500_000,
            666_667,
        }:
            raise R2QualificationError("R2_CANDIDATE_HARD_STOP_INVALID")
    elif hard_stop_loss_ppm is not None:
        raise R2QualificationError("R2_CANDIDATE_CONTROL_STOP_INVALID")
    if type(quantity_units) is not int or quantity_units != 1:
        raise R2QualificationError("R2_QUANTITY_UNITS_UNSUPPORTED")
    return canonical_sha256(
        {
            "schema_version": "R2_BASE_CANDIDATE_IDENTITY_V1",
            "classification": "RESEARCH_ONLY",
            "candidate_id": candidate_id,
            "ordinal": ordinal,
            "trend_required_count": trend_required_count,
            "breakout_required_count": breakout_required_count,
            "stop_mode": stop_mode,
            "hard_stop_loss_ppm": hard_stop_loss_ppm,
            "quantity_units": quantity_units,
            "r1_fixed_policy_projection_sha256": _require_sha256(
                r1_fixed_policy_projection_sha256,
                "R2_R1_FIXED_POLICY_PROJECTION_HASH_INVALID",
            ),
        }
    )


def derive_development_method_freeze_sha256(
    *,
    registry_sha256: str,
    partition_manifest_sha256: str,
    fee_schedule_manifest_sha256: str,
    exit_stress_method_sha256: str,
    estimator_method_sha256: str,
    code_package_sha256: str,
    runtime_sha256: str,
) -> str:
    """Bind every method input that must precede Development outcome access."""

    payload = {
        "schema_version": "R2_DEVELOPMENT_METHOD_FREEZE_IDENTITY_V1",
        "registry_sha256": _require_sha256(registry_sha256),
        "partition_manifest_sha256": _require_sha256(partition_manifest_sha256),
        "fee_schedule_manifest_sha256": _require_sha256(
            fee_schedule_manifest_sha256
        ),
        "exit_stress_method_sha256": _require_sha256(exit_stress_method_sha256),
        "estimator_method_sha256": _require_sha256(estimator_method_sha256),
        "code_package_sha256": _require_sha256(
            code_package_sha256, "R2_CODE_PACKAGE_HASH_INVALID"
        ),
        "runtime_sha256": _require_sha256(
            runtime_sha256, "R2_RUNTIME_HASH_INVALID"
        ),
    }
    return canonical_sha256(payload)


def derive_evaluated_candidate_sha256(
    *,
    base_candidate_sha256: str,
    development_method_freeze_sha256: str,
    carrier_mode: str,
    lc0_development_receipt_sha256: str,
    bcs0_development_receipt_sha256: str,
) -> str:
    """Bind a Development-selected carrier mode before any WF outcome access."""

    if carrier_mode not in _CARRIER_MODES:
        raise R2QualificationError("R2_CARRIER_MODE_INVALID")
    return canonical_sha256(
        {
            "schema_version": "R2_EVALUATED_CANDIDATE_IDENTITY_V1",
            "base_candidate_sha256": _require_sha256(base_candidate_sha256),
            "development_method_freeze_sha256": _require_sha256(
                development_method_freeze_sha256
            ),
            "carrier_mode": carrier_mode,
            # Both receipts are required even for a single-carrier mode: the failed
            # sibling is evidence for why the mode was frozen, not a hidden omission.
            "lc0_development_receipt_sha256": _require_sha256(
                lc0_development_receipt_sha256
            ),
            "bcs0_development_receipt_sha256": _require_sha256(
                bcs0_development_receipt_sha256
            ),
        }
    )


def derive_research_freeze_sha256(
    *,
    development_method_freeze_sha256: str,
    evaluated_candidate_sha256s: tuple[str, ...],
    common_opportunity_manifest_sha256: str,
    joint_estimator_method_sha256: str,
    code_package_sha256: str,
    runtime_sha256: str,
) -> str:
    """Freeze the ordered S_dev family and joint method before WF access."""

    candidates = _require_hash_tuple(
        evaluated_candidate_sha256s,
        reason_code="R2_EVALUATED_CANDIDATE_SET_INVALID",
        minimum=1,
        maximum=12,
    )
    return canonical_sha256(
        {
            "schema_version": "R2_RESEARCH_FREEZE_IDENTITY_V1",
            "development_method_freeze_sha256": _require_sha256(
                development_method_freeze_sha256
            ),
            "evaluated_candidate_sha256s": list(candidates),
            "common_opportunity_manifest_sha256": _require_sha256(
                common_opportunity_manifest_sha256
            ),
            "joint_estimator_method_sha256": _require_sha256(
                joint_estimator_method_sha256
            ),
            "code_package_sha256": _require_sha256(
                code_package_sha256, "R2_CODE_PACKAGE_HASH_INVALID"
            ),
            "runtime_sha256": _require_sha256(
                runtime_sha256, "R2_RUNTIME_HASH_INVALID"
            ),
        }
    )


def derive_historical_theta_sha256(
    *,
    research_freeze_sha256: str,
    evaluated_candidate_sha256: str,
    wf_carrier_receipt_sha256s: tuple[str, ...],
    wf_policy_receipt_sha256: str,
) -> str:
    """Bind the exact WF-only champion evidence, never Development outcomes."""

    carrier_receipts = _require_hash_tuple(
        wf_carrier_receipt_sha256s,
        reason_code="R2_WF_CARRIER_RECEIPT_SET_INVALID",
        minimum=1,
        maximum=2,
    )
    return canonical_sha256(
        {
            "schema_version": "R2_HISTORICAL_THETA_IDENTITY_V1",
            "research_freeze_sha256": _require_sha256(research_freeze_sha256),
            "evaluated_candidate_sha256": _require_sha256(
                evaluated_candidate_sha256
            ),
            "wf_carrier_receipt_sha256s": list(carrier_receipts),
            "wf_policy_receipt_sha256": _require_sha256(
                wf_policy_receipt_sha256
            ),
        }
    )


def derive_qualification_sha256(
    *,
    historical_theta_sha256: str,
    forward_receipt_sha256: str,
    machine_terminal_status: str,
) -> str:
    """Bind machine evidence only; Owner approval is intentionally absent."""

    if machine_terminal_status not in _MACHINE_TERMINALS:
        raise R2QualificationError("R2_MACHINE_TERMINAL_STATUS_INVALID")
    return canonical_sha256(
        {
            "schema_version": "R2_QUALIFICATION_IDENTITY_V1",
            "stage": "FORWARD",
            "historical_theta_sha256": _require_sha256(
                historical_theta_sha256
            ),
            "forward_receipt_sha256": _require_sha256(forward_receipt_sha256),
            "machine_terminal_status": machine_terminal_status,
        }
    )


def derive_owner_decision_receipt_sha256(
    *,
    qualification_sha256: str,
    owner_decision: str,
) -> str:
    """Return only a one-way identity hash, never an Owner authority receipt.

    Issuance, authentication, time, and authorization belong to a separately
    authorized receipt builder.  This pure function cannot approve a theta.
    """

    if owner_decision not in _OWNER_DECISIONS:
        raise R2QualificationError("R2_OWNER_DECISION_INVALID")
    return canonical_sha256(
        {
            "schema_version": "R2_OWNER_DECISION_RECEIPT_IDENTITY_V1",
            "qualification_sha256": _require_sha256(qualification_sha256),
            "owner_decision": owner_decision,
        }
    )


__all__ = [
    "CODE_RUNTIME_BINDING_SCHEMA_VERSION",
    "CodeRuntimeBindingV1",
    "R1_FIXED_POLICY_PROJECTION_SCHEMA_VERSION",
    "R1FixedPolicyProjectionV1",
    "R2QualificationError",
    "build_r1_fixed_policy_projection",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "canonical_sha256",
    "derive_base_candidate_sha256",
    "derive_development_method_freeze_sha256",
    "derive_evaluated_candidate_sha256",
    "derive_historical_theta_sha256",
    "derive_owner_decision_receipt_sha256",
    "derive_qualification_sha256",
    "derive_research_freeze_sha256",
    "validate_r1_fixed_policy_projection",
]
