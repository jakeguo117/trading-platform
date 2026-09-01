"""Deterministic debit Bull Call Spread research facts and pure functions.

This module combines already-normalized, hash-bound facts.  It has no provider
adapter, notification transport, order object, or execution authority.  A
structurally valid result is still research-only and explicitly non-actionable.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import re
import weakref

from gld_normalizer.errors import NormalizationError
from gld_normalizer.readers import (
    MAX_DEFINED_I64 as _MAX_DEFINED_I64,
    MAX_DEFINED_U32 as _MAX_DEFINED_U32,
    MAX_DEFINED_U64 as _MAX_DEFINED_U64,
)

from .crr_delta import (
    MODEL_SHA256,
    CrrCallInputsV1,
    CrrDeltaResultV1,
    CrrPitInputsV1,
    is_verified_crr_delta_result,
)
from .facts import (
    QUOTE_QUALITY_POLICY_SHA256,
    QUOTE_QUALITY_POLICY_VERSION,
    OptionSnapshotCandidateLedgerV1,
    OptionQuoteSnapshotV1,
    OptionQuoteV1,
    SignalSnapshotV1,
    build_option_snapshot_candidate_ledger,
    canonical_snapshot_sha256,
    select_first_complete_option_snapshot,
    validate_option_snapshot_quality,
)


_PPM = 1_000_000
_SHORT_DELTA_MIN_PPM = 200_000
_SHORT_DELTA_MAX_PPM = 300_000
_SHORT_DELTA_TARGET_PPM = 250_000
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_CONTROL_TEXT_RE = re.compile(r"[ -~]{1,256}\Z")
_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_MESSAGE_KEY_RE = re.compile(r"[a-z][a-z0-9_.]{0,255}\Z")
_LIFECYCLE_STATES = frozenset(
    {
        "PROPOSED",
        "MANAGED",
        "HOLD",
        "EXIT_DUE",
        "RECONCILIATION_BLOCKED",
    }
)
_LIFECYCLE_EVENTS = frozenset(
    {
        "TICK",
        "POLICY_FULL_EXIT",
        "EXPIRY_SAFETY",
        "SIGNAL_INVALIDATION",
        "H20",
        "PARTIAL_FILL",
        "RESIDUAL_POSITION",
        "ASSIGNMENT_DETECTED",
        "EXERCISE_DETECTED",
        "CONTRACT_ADJUSTMENT",
        "IDENTITY_MISMATCH",
        "BROKER_STATE_UNKNOWN",
    }
)


class BcsReasonV1:
    """Versioned registry for stable BCS status and failure reason codes."""

    SIGNAL_FAIL = "SIGNAL_FAIL"
    SIGNAL_NOT_EVALUABLE = "SIGNAL_NOT_EVALUABLE"
    IBKR_EXECUTABLE_QUOTE_UNAVAILABLE = "IBKR_EXECUTABLE_QUOTE_UNAVAILABLE"
    RESEARCH_ONLY_NOT_ACTIONABLE = "RESEARCH_ONLY_NOT_ACTIONABLE"
    SELECTOR_MODEL_INSTABILITY = "SELECTOR_MODEL_INSTABILITY"
    MODEL_HASH_MISMATCH = "MODEL_HASH_MISMATCH"
    UNVERIFIED_DELTA_RESULT = "UNVERIFIED_DELTA_RESULT"
    DELTA_PIT_INPUT_MISMATCH = "DELTA_PIT_INPUT_MISMATCH"
    CROSS_LEG_SKEW_EXCEEDED = "CROSS_LEG_SKEW_EXCEEDED"
    EXIT_DUE_NONEXECUTABLE = "EXIT_DUE_NONEXECUTABLE"
    MANAGEMENT_CHECKPOINT_REQUIRED = "MANAGEMENT_CHECKPOINT_REQUIRED"
    RECONCILIATION_AUTHORITY_UNAVAILABLE = (
        "RECONCILIATION_AUTHORITY_UNAVAILABLE"
    )
    RECONCILIATION_IDENTITY_MISMATCH = "RECONCILIATION_IDENTITY_MISMATCH"
    RECONCILIATION_POSITION_MISMATCH = "RECONCILIATION_POSITION_MISMATCH"
    RECONCILIATION_OPEN_ORDERS = "RECONCILIATION_OPEN_ORDERS"
    RECONCILIATION_PENDING_ASSIGNMENT = "RECONCILIATION_PENDING_ASSIGNMENT"
    RECONCILIATION_PENDING_EXERCISE = "RECONCILIATION_PENDING_EXERCISE"
    RECONCILIATION_TERMINAL_FEES_UNFINALIZED = (
        "RECONCILIATION_TERMINAL_FEES_UNFINALIZED"
    )
    LC0_AUTHORITY_NOT_IMPLEMENTED = "LC0_AUTHORITY_NOT_IMPLEMENTED"
    BOTH_CARRIERS_PASS = "BOTH_CARRIERS_PASS"
    LC0_ONLY_PASS = "LC0_ONLY_PASS"
    BCS0_ONLY_PASS = "BCS0_ONLY_PASS"
    BOTH_CARRIERS_FAIL = "BOTH_CARRIERS_FAIL"
    CARRIER_EVALUATION_INCOMPLETE = "CARRIER_EVALUATION_INCOMPLETE"
    LIFECYCLE_LINEAGE_INVALID = "BCS_LIFECYCLE_LINEAGE_INVALID"
    LIFECYCLE_LATCH_INVALID = "BCS_LIFECYCLE_LATCH_INVALID"


_REASON_ALIASES = {
    "BCS_SELECTOR_INSTABILITY": BcsReasonV1.SELECTOR_MODEL_INSTABILITY,
    "DELTA_MODEL_HASH_MISMATCH": BcsReasonV1.MODEL_HASH_MISMATCH,
    "RECEIVE_SKEW_EXCEEDED": BcsReasonV1.CROSS_LEG_SKEW_EXCEEDED,
}
_RECONCILIATION_BLOCKING_EVENTS = frozenset(
    {
        "PARTIAL_FILL",
        "RESIDUAL_POSITION",
        "ASSIGNMENT_DETECTED",
        "EXERCISE_DETECTED",
        "CONTRACT_ADJUSTMENT",
        "IDENTITY_MISMATCH",
        "BROKER_STATE_UNKNOWN",
    }
)

REAL_BCS_DECISION_EXECUTION_STATUS = (
    "NOT_IMPLEMENTED_QUALIFIED_QUOTE_LC0_AND_RECONCILIATION_BINDING_REQUIRED"
)


def _require_exact_int(
    value: object,
    *,
    reason_code: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise NormalizationError(reason_code)
    return value


def _require_signed_int(value: object, *, reason_code: str) -> int:
    return _require_exact_int(
        value,
        reason_code=reason_code,
        minimum=-_MAX_DEFINED_I64,
        maximum=_MAX_DEFINED_I64,
    )


def _require_text(value: object, *, reason_code: str) -> str:
    if (
        type(value) is not str
        or value != value.strip()
        or _CONTROL_TEXT_RE.fullmatch(value) is None
    ):
        raise NormalizationError(reason_code)
    return value


def _require_sha256(value: object, *, reason_code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise NormalizationError(reason_code)
    return value


def _require_bool(value: object, *, reason_code: str) -> bool:
    if type(value) is not bool:
        raise NormalizationError(reason_code)
    return value


def _canonical_reason_code(reason_code: str) -> str:
    canonical = _REASON_ALIASES.get(reason_code, reason_code)
    if type(canonical) is not str or _REASON_RE.fullmatch(canonical) is None:
        raise NormalizationError("BCS_REASON_INVALID")
    return canonical


def _exact_midpoint(bid_nano_usd: int, ask_nano_usd: int) -> int:
    total = bid_nano_usd + ask_nano_usd
    if total % 2:
        raise NormalizationError("DELTA_INPUT_MIDPOINT_NON_INTEGRAL")
    return total // 2


@dataclass(frozen=True, slots=True)
class Lc0SelectionBindingV1:
    signal_snapshot_sha256: str
    option_snapshot_sha256: str
    selector_contract_sha256: str
    long_call_id: str
    long_contract_sha256: str

    def __post_init__(self) -> None:
        for value in (
            self.signal_snapshot_sha256,
            self.option_snapshot_sha256,
            self.selector_contract_sha256,
            self.long_contract_sha256,
        ):
            _require_sha256(
                value,
                reason_code="BCS_LONG_LEG_BINDING_MISMATCH",
            )
        _require_text(
            self.long_call_id,
            reason_code="BCS_LONG_LEG_BINDING_MISMATCH",
        )

    @property
    def binding_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class CrrDeltaEvidenceV1:
    """One input-to-result binding; structural research evidence only."""

    inputs: CrrCallInputsV1
    result: CrrDeltaResultV1

    def __post_init__(self) -> None:
        if (
            not isinstance(self.inputs, CrrCallInputsV1)
            or not isinstance(self.inputs.pit_inputs, CrrPitInputsV1)
            or not isinstance(self.result, CrrDeltaResultV1)
            or self.result.model_sha256 != MODEL_SHA256
        ):
            raise NormalizationError(BcsReasonV1.MODEL_HASH_MISMATCH)
        if not is_verified_crr_delta_result(self.result):
            raise NormalizationError(BcsReasonV1.UNVERIFIED_DELTA_RESULT)
        if (
            self.result.input_sha256 != self.inputs.input_sha256
            or self.inputs.pit_inputs_sha256
            != canonical_snapshot_sha256(self.inputs.pit_inputs)
            or self.result.option_snapshot_sha256
            != self.inputs.option_snapshot_sha256
            or self.result.contract_id != self.inputs.contract_id
        ):
            raise NormalizationError("DELTA_INPUT_BINDING_MISMATCH")

    @property
    def evidence_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class BcsSelectionV1:
    lc0_selection_sha256: str
    candidate_ledger_sha256: str
    quote_quality_policy_version: str
    quote_quality_policy_sha256: str
    long_call_id: str
    short_call_id: str
    long_contract_sha256: str
    short_contract_sha256: str
    delta_model_sha256: str
    long_delta_input_sha256: str
    short_delta_input_sha256: str
    long_delta_evidence_sha256: str
    short_delta_evidence_sha256: str
    long_delta_run_sha256: str
    short_delta_run_sha256: str
    long_delta_runtime_fingerprint_sha256: str
    short_delta_runtime_fingerprint_sha256: str
    long_strike_nano_usd: int
    short_strike_nano_usd: int
    long_delta_ppm: int
    short_delta_ppm: int
    net_delta_ppm: int
    multiplier: int
    option_snapshot_sha256: str

    def __post_init__(self) -> None:
        for value in (
            self.lc0_selection_sha256,
            self.candidate_ledger_sha256,
            self.quote_quality_policy_sha256,
            self.long_contract_sha256,
            self.short_contract_sha256,
            self.delta_model_sha256,
            self.long_delta_input_sha256,
            self.short_delta_input_sha256,
            self.long_delta_evidence_sha256,
            self.short_delta_evidence_sha256,
            self.long_delta_run_sha256,
            self.short_delta_run_sha256,
            self.long_delta_runtime_fingerprint_sha256,
            self.short_delta_runtime_fingerprint_sha256,
        ):
            _require_sha256(
                value,
                reason_code="BCS_LONG_LEG_BINDING_MISMATCH",
            )
        if self.delta_model_sha256 != MODEL_SHA256:
            raise NormalizationError("DELTA_INPUT_BINDING_MISMATCH")
        if (
            self.quote_quality_policy_version != QUOTE_QUALITY_POLICY_VERSION
            or self.quote_quality_policy_sha256 != QUOTE_QUALITY_POLICY_SHA256
        ):
            raise NormalizationError("QUOTE_QUALITY_POLICY_MISMATCH")
        if (
            self.long_delta_input_sha256 == self.short_delta_input_sha256
            or self.long_delta_evidence_sha256
            == self.short_delta_evidence_sha256
            or self.long_delta_run_sha256 == self.short_delta_run_sha256
            or self.long_delta_runtime_fingerprint_sha256
            != self.short_delta_runtime_fingerprint_sha256
        ):
            raise NormalizationError("DELTA_INPUT_BINDING_MISMATCH")
        long_id = _require_text(
            self.long_call_id,
            reason_code="BCS_LONG_LEG_BINDING_MISMATCH",
        )
        short_id = _require_text(
            self.short_call_id,
            reason_code="BCS_LONG_LEG_BINDING_MISMATCH",
        )
        if long_id == short_id:
            raise NormalizationError("BCS_LONG_LEG_BINDING_MISMATCH")
        long_strike = _require_exact_int(
            self.long_strike_nano_usd,
            reason_code="BCS_LONG_LEG_BINDING_MISMATCH",
            minimum=1,
            maximum=_MAX_DEFINED_I64,
        )
        short_strike = _require_exact_int(
            self.short_strike_nano_usd,
            reason_code="BCS_LONG_LEG_BINDING_MISMATCH",
            minimum=1,
            maximum=_MAX_DEFINED_I64,
        )
        if short_strike <= long_strike:
            raise NormalizationError("BCS_LONG_LEG_BINDING_MISMATCH")
        long_delta = _require_exact_int(
            self.long_delta_ppm,
            reason_code="BCS_INVALID_NET_DELTA",
            minimum=1,
            maximum=_PPM,
        )
        short_delta = _require_exact_int(
            self.short_delta_ppm,
            reason_code="BCS_INVALID_NET_DELTA",
            minimum=0,
            maximum=_PPM,
        )
        net_delta = _require_exact_int(
            self.net_delta_ppm,
            reason_code="BCS_INVALID_NET_DELTA",
            minimum=1,
            maximum=_PPM,
        )
        if (
            short_delta >= long_delta
            or net_delta != long_delta - short_delta
            or not 0 < net_delta < long_delta <= _PPM
        ):
            raise NormalizationError("BCS_INVALID_NET_DELTA")
        if type(self.multiplier) is not int or self.multiplier != 100:
            raise NormalizationError("BCS_LONG_LEG_BINDING_MISMATCH")
        _require_sha256(
            self.option_snapshot_sha256,
            reason_code="DELTA_INPUT_BINDING_MISMATCH",
        )

    @property
    def selection_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class BcsFeeScheduleV1:
    source_receipt_sha256: str
    effective_from_utc_ns: int
    long_entry_fee_nano_usd_per_contract: int
    short_entry_fee_nano_usd_per_contract: int
    long_exit_fee_nano_usd_per_contract: int
    short_exit_fee_nano_usd_per_contract: int

    def __post_init__(self) -> None:
        _require_sha256(
            self.source_receipt_sha256,
            reason_code="BCS_FEE_SCHEDULE_MISSING",
        )
        _require_exact_int(
            self.effective_from_utc_ns,
            reason_code="BCS_FEE_SCHEDULE_MISSING",
            minimum=1,
            maximum=_MAX_DEFINED_U64,
        )
        for fee in (
            self.long_entry_fee_nano_usd_per_contract,
            self.short_entry_fee_nano_usd_per_contract,
            self.long_exit_fee_nano_usd_per_contract,
            self.short_exit_fee_nano_usd_per_contract,
        ):
            _require_exact_int(
                fee,
                reason_code="BCS_FEE_SCHEDULE_MISSING",
                minimum=0,
                maximum=_MAX_DEFINED_I64,
            )

    @property
    def fee_schedule_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class BcsEntryCostV1:
    bcs_selection_sha256: str
    option_snapshot_sha256: str
    fee_schedule_sha256: str
    quantity: int
    gross_expiry_width_value_nano_usd: int
    base_net_debit_nano_usd: int
    stressed_net_debit_nano_usd: int
    max_loss_nano_usd: int
    theoretical_expiry_max_profit_nano_usd: int
    theoretical_expiry_only: bool

    def __post_init__(self) -> None:
        _require_sha256(
            self.bcs_selection_sha256,
            reason_code="BCS_LONG_LEG_BINDING_MISMATCH",
        )
        _require_sha256(
            self.option_snapshot_sha256,
            reason_code="BCS_LONG_LEG_BINDING_MISMATCH",
        )
        _require_sha256(
            self.fee_schedule_sha256,
            reason_code="BCS_FEE_SCHEDULE_MISSING",
        )
        _require_exact_int(
            self.quantity,
            reason_code="BCS_INVALID_QUANTITY",
            minimum=1,
            maximum=_MAX_DEFINED_U32,
        )
        gross_width = _require_exact_int(
            self.gross_expiry_width_value_nano_usd,
            reason_code="BCS_INVALID_NET_DEBIT",
            minimum=1,
            maximum=_MAX_DEFINED_I64,
        )
        base = _require_exact_int(
            self.base_net_debit_nano_usd,
            reason_code="BCS_INVALID_NET_DEBIT",
            minimum=1,
            maximum=_MAX_DEFINED_I64,
        )
        stressed = _require_exact_int(
            self.stressed_net_debit_nano_usd,
            reason_code="BCS_INVALID_NET_DEBIT",
            minimum=1,
            maximum=_MAX_DEFINED_I64,
        )
        max_loss = _require_exact_int(
            self.max_loss_nano_usd,
            reason_code="BCS_INVALID_NET_DEBIT",
            minimum=1,
            maximum=_MAX_DEFINED_I64,
        )
        theoretical_profit = _require_exact_int(
            self.theoretical_expiry_max_profit_nano_usd,
            reason_code="BCS_INVALID_NET_DEBIT",
            minimum=1,
            maximum=_MAX_DEFINED_I64,
        )
        if (
            not base <= stressed < gross_width
            or max_loss != base
            or theoretical_profit != gross_width - base
        ):
            raise NormalizationError("BCS_INVALID_NET_DEBIT")
        if type(self.theoretical_expiry_only) is not bool or not self.theoretical_expiry_only:
            raise NormalizationError("BCS_INVALID_NET_DEBIT")

    @property
    def cost_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class BcsExitCostV1:
    bcs_selection_sha256: str
    option_snapshot_sha256: str
    fee_schedule_sha256: str
    quantity: int
    base_net_credit_nano_usd: int
    stressed_net_credit_nano_usd: int

    def __post_init__(self) -> None:
        _require_sha256(
            self.bcs_selection_sha256,
            reason_code="BCS_LONG_LEG_BINDING_MISMATCH",
        )
        _require_sha256(
            self.option_snapshot_sha256,
            reason_code="BCS_LONG_LEG_BINDING_MISMATCH",
        )
        _require_sha256(
            self.fee_schedule_sha256,
            reason_code="BCS_FEE_SCHEDULE_MISSING",
        )
        _require_exact_int(
            self.quantity,
            reason_code="BCS_INVALID_QUANTITY",
            minimum=1,
            maximum=_MAX_DEFINED_U32,
        )
        base = _require_signed_int(
            self.base_net_credit_nano_usd,
            reason_code="BCS_EXIT_COST_INVALID",
        )
        stressed = _require_signed_int(
            self.stressed_net_credit_nano_usd,
            reason_code="BCS_EXIT_COST_INVALID",
        )
        if stressed > base:
            raise NormalizationError("BCS_EXIT_COST_INVALID")

    @property
    def cost_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class BcsTradeEpisodeV1:
    episode_id: str
    signal_snapshot_sha256: str
    bcs_selection_sha256: str
    entry_option_snapshot_sha256: str
    entry_cost_sha256: str
    entry_capture_utc_ns: int
    quantity: int

    def __post_init__(self) -> None:
        _require_text(self.episode_id, reason_code="BCS_EPISODE_BINDING_MISMATCH")
        for value in (
            self.signal_snapshot_sha256,
            self.bcs_selection_sha256,
            self.entry_option_snapshot_sha256,
            self.entry_cost_sha256,
        ):
            _require_sha256(value, reason_code="BCS_EPISODE_BINDING_MISMATCH")
        _require_exact_int(
            self.entry_capture_utc_ns,
            reason_code="BCS_EPISODE_BINDING_MISMATCH",
            minimum=1,
            maximum=_MAX_DEFINED_U64,
        )
        _validated_quantity(self.quantity)

    @property
    def episode_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class BcsManagementSnapshotV1:
    episode_sha256: str
    bcs_selection_sha256: str
    option_snapshot_sha256: str
    source_receipt_sha256: str
    capture_utc_ns: int
    quote_quality_policy_version: str
    quote_quality_policy_sha256: str
    authority_status: str

    def __post_init__(self) -> None:
        for value in (
            self.episode_sha256,
            self.bcs_selection_sha256,
            self.option_snapshot_sha256,
            self.source_receipt_sha256,
        ):
            _require_sha256(
                value,
                reason_code="BCS_MANAGEMENT_SNAPSHOT_BINDING_MISMATCH",
            )
        _require_exact_int(
            self.capture_utc_ns,
            reason_code="BCS_MANAGEMENT_SNAPSHOT_BINDING_MISMATCH",
            minimum=1,
            maximum=_MAX_DEFINED_U64,
        )
        if self.quote_quality_policy_version != QUOTE_QUALITY_POLICY_VERSION:
            raise NormalizationError("QUOTE_QUALITY_POLICY_MISMATCH")
        if self.quote_quality_policy_sha256 != QUOTE_QUALITY_POLICY_SHA256:
            raise NormalizationError("QUOTE_QUALITY_POLICY_MISMATCH")
        if self.authority_status != "UNQUALIFIED_RESEARCH_ONLY":
            raise NormalizationError(
                BcsReasonV1.RECONCILIATION_AUTHORITY_UNAVAILABLE
            )

    @property
    def checkpoint_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class BrokerReconciliationSnapshotBV1:
    episode_sha256: str
    bcs_selection_sha256: str
    long_contract_sha256: str
    short_contract_sha256: str
    source_receipt_sha256: str
    positions_receipt_sha256: str
    open_orders_receipt_sha256: str
    pending_events_receipt_sha256: str
    terminal_fees_receipt_sha256: str
    capture_utc_ns: int
    long_position_quantity: int
    short_position_quantity: int
    open_order_count: int
    pending_assignment: bool
    pending_exercise: bool
    terminal_fees_nano_usd: int
    terminal_fees_final: bool
    authority_status: str

    def __post_init__(self) -> None:
        for value in (
            self.episode_sha256,
            self.bcs_selection_sha256,
            self.long_contract_sha256,
            self.short_contract_sha256,
            self.source_receipt_sha256,
            self.positions_receipt_sha256,
            self.open_orders_receipt_sha256,
            self.pending_events_receipt_sha256,
            self.terminal_fees_receipt_sha256,
        ):
            _require_sha256(
                value,
                reason_code="BCS_RECONCILIATION_SNAPSHOT_INVALID",
            )
        _require_exact_int(
            self.capture_utc_ns,
            reason_code="BCS_RECONCILIATION_SNAPSHOT_INVALID",
            minimum=1,
            maximum=_MAX_DEFINED_U64,
        )
        for quantity in (
            self.long_position_quantity,
            self.short_position_quantity,
        ):
            _require_signed_int(
                quantity,
                reason_code="BCS_RECONCILIATION_SNAPSHOT_INVALID",
            )
        _require_exact_int(
            self.open_order_count,
            reason_code="BCS_RECONCILIATION_SNAPSHOT_INVALID",
            minimum=0,
            maximum=_MAX_DEFINED_U32,
        )
        _require_bool(
            self.pending_assignment,
            reason_code="BCS_RECONCILIATION_SNAPSHOT_INVALID",
        )
        _require_bool(
            self.pending_exercise,
            reason_code="BCS_RECONCILIATION_SNAPSHOT_INVALID",
        )
        _require_signed_int(
            self.terminal_fees_nano_usd,
            reason_code="BCS_RECONCILIATION_SNAPSHOT_INVALID",
        )
        _require_bool(
            self.terminal_fees_final,
            reason_code="BCS_RECONCILIATION_SNAPSHOT_INVALID",
        )
        if self.authority_status != "UNQUALIFIED_RESEARCH_ONLY":
            raise NormalizationError(
                BcsReasonV1.RECONCILIATION_AUTHORITY_UNAVAILABLE
            )

    @property
    def receipt_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class CarrierTerminalResultV1:
    carrier_id: str
    terminal_status: str
    reason_code: str
    signal_snapshot_sha256: str
    option_snapshot_sha256: str | None
    result_sha256: str

    def __post_init__(self) -> None:
        if self.carrier_id not in {"LC0", "BCS0"}:
            raise NormalizationError("CARRIER_TERMINAL_RESULT_INVALID")
        if self.terminal_status not in {"PASS", "FAIL", "NO_DECISION"}:
            raise NormalizationError("CARRIER_TERMINAL_RESULT_INVALID")
        reason_code = _canonical_reason_code(self.reason_code)
        for value in (self.signal_snapshot_sha256, self.result_sha256):
            _require_sha256(value, reason_code="CARRIER_TERMINAL_RESULT_INVALID")
        if self.option_snapshot_sha256 is not None:
            _require_sha256(
                self.option_snapshot_sha256,
                reason_code="CARRIER_TERMINAL_RESULT_INVALID",
            )
        if self.carrier_id == "LC0":
            if (
                self.terminal_status != "NO_DECISION"
                or reason_code != BcsReasonV1.LC0_AUTHORITY_NOT_IMPLEMENTED
            ):
                raise NormalizationError("CARRIER_TERMINAL_RESULT_INVALID")
        elif (
            (self.terminal_status == "PASS")
            != (reason_code == BcsReasonV1.RESEARCH_ONLY_NOT_ACTIONABLE)
            or (self.terminal_status == "FAIL")
            != (reason_code == BcsReasonV1.SIGNAL_FAIL)
        ):
            raise NormalizationError("CARRIER_TERMINAL_RESULT_INVALID")

    @property
    def terminal_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class DecisionArtifactV1:
    signal_snapshot_sha256: str
    option_snapshot_sha256: str | None
    candidate_ledger_sha256: str | None
    research_contract_sha256: str
    rule_package_version: str
    rule_sha256: str
    delta_model_sha256: str
    fee_schedule_sha256: str
    bcs_selection_sha256: str | None
    long_delta_evidence_sha256: str | None
    short_delta_evidence_sha256: str | None
    long_delta_run_sha256: str | None
    short_delta_run_sha256: str | None
    long_delta_runtime_fingerprint_sha256: str | None
    short_delta_runtime_fingerprint_sha256: str | None
    lc0_terminal: CarrierTerminalResultV1
    bcs0_terminal: CarrierTerminalResultV1
    status: str
    reason_code: str
    owner_selection_required: bool
    actionable: bool
    broker_order_count: int

    def __post_init__(self) -> None:
        for value in (
            self.signal_snapshot_sha256,
            self.research_contract_sha256,
            self.rule_sha256,
            self.delta_model_sha256,
            self.fee_schedule_sha256,
        ):
            _require_sha256(value, reason_code="DECISION_ARTIFACT_INVALID")
        if self.option_snapshot_sha256 is not None:
            _require_sha256(
                self.option_snapshot_sha256,
                reason_code="DECISION_ARTIFACT_INVALID",
            )
        if self.candidate_ledger_sha256 is not None:
            _require_sha256(
                self.candidate_ledger_sha256,
                reason_code="DECISION_ARTIFACT_INVALID",
            )
        _require_text(
            self.rule_package_version,
            reason_code="DECISION_ARTIFACT_INVALID",
        )
        if self.delta_model_sha256 != MODEL_SHA256:
            raise NormalizationError(BcsReasonV1.MODEL_HASH_MISMATCH)
        delta_provenance = (
            self.bcs_selection_sha256,
            self.long_delta_evidence_sha256,
            self.short_delta_evidence_sha256,
            self.long_delta_run_sha256,
            self.short_delta_run_sha256,
            self.long_delta_runtime_fingerprint_sha256,
            self.short_delta_runtime_fingerprint_sha256,
        )
        if (
            not isinstance(self.lc0_terminal, CarrierTerminalResultV1)
            or not isinstance(self.bcs0_terminal, CarrierTerminalResultV1)
            or self.lc0_terminal.carrier_id != "LC0"
            or self.bcs0_terminal.carrier_id != "BCS0"
            or self.lc0_terminal.terminal_status != "NO_DECISION"
            or self.lc0_terminal.reason_code
            != BcsReasonV1.LC0_AUTHORITY_NOT_IMPLEMENTED
        ):
            raise NormalizationError("DECISION_ARTIFACT_INVALID")
        for terminal in (self.lc0_terminal, self.bcs0_terminal):
            if (
                terminal.signal_snapshot_sha256 != self.signal_snapshot_sha256
                or terminal.option_snapshot_sha256 != self.option_snapshot_sha256
            ):
                raise NormalizationError("DECISION_ARTIFACT_INVALID")
        if self.bcs0_terminal.terminal_status == "PASS":
            if (
                self.candidate_ledger_sha256 is None
                or any(value is None for value in delta_provenance)
            ):
                raise NormalizationError("DECISION_ARTIFACT_INVALID")
            for value in delta_provenance:
                _require_sha256(value, reason_code="DECISION_ARTIFACT_INVALID")
            if (
                self.long_delta_evidence_sha256
                == self.short_delta_evidence_sha256
                or self.long_delta_run_sha256 == self.short_delta_run_sha256
                or self.long_delta_runtime_fingerprint_sha256
                != self.short_delta_runtime_fingerprint_sha256
            ):
                raise NormalizationError("DECISION_ARTIFACT_INVALID")
        elif any(value is not None for value in delta_provenance):
            raise NormalizationError("DECISION_ARTIFACT_INVALID")
        expected = _decision_artifact_outcome(
            self.lc0_terminal.terminal_status,
            self.bcs0_terminal.terminal_status,
        )
        if (
            (self.status, self.reason_code, self.owner_selection_required)
            != expected
            or type(self.owner_selection_required) is not bool
            or type(self.actionable) is not bool
            or self.actionable
            or type(self.broker_order_count) is not int
            or self.broker_order_count != 0
        ):
            raise NormalizationError("DECISION_ARTIFACT_INVALID")

    @property
    def artifact_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class BlockingNotificationV1:
    reason_code: str
    message_key: str
    dedupe_key: str
    signal_snapshot_sha256: str
    option_snapshot_sha256: str | None

    def __post_init__(self) -> None:
        if type(self.reason_code) is not str or _REASON_RE.fullmatch(self.reason_code) is None:
            raise NormalizationError("BLOCKING_NOTIFICATION_INVALID")
        if type(self.message_key) is not str or _MESSAGE_KEY_RE.fullmatch(self.message_key) is None:
            raise NormalizationError("BLOCKING_NOTIFICATION_INVALID")
        _require_sha256(
            self.dedupe_key,
            reason_code="BLOCKING_NOTIFICATION_INVALID",
        )
        _require_sha256(
            self.signal_snapshot_sha256,
            reason_code="BLOCKING_NOTIFICATION_INVALID",
        )
        if self.option_snapshot_sha256 is not None:
            _require_sha256(
                self.option_snapshot_sha256,
                reason_code="BLOCKING_NOTIFICATION_INVALID",
            )


@dataclass(frozen=True, slots=True, weakref_slot=True)
class BcsEvaluationV1:
    signal_snapshot_sha256: str
    option_snapshot_sha256: str | None
    candidate_ledger_sha256: str | None
    status: str
    reason_code: str
    actionable: bool
    broker_order_count: int
    selection: BcsSelectionV1 | None
    entry_cost: BcsEntryCostV1 | None
    blocking_notifications: tuple[BlockingNotificationV1, ...]

    def __post_init__(self) -> None:
        _require_sha256(
            self.signal_snapshot_sha256,
            reason_code="BCS_EVALUATION_INVALID",
        )
        if self.option_snapshot_sha256 is not None:
            _require_sha256(
                self.option_snapshot_sha256,
                reason_code="BCS_EVALUATION_INVALID",
            )
        if self.candidate_ledger_sha256 is not None:
            _require_sha256(
                self.candidate_ledger_sha256,
                reason_code="BCS_EVALUATION_INVALID",
            )
        if self.status not in {"NO_ENTRY", "NO_DECISION", "RESEARCH_CANDIDATE"}:
            raise NormalizationError("BCS_EVALUATION_INVALID")
        if self.reason_code != _canonical_reason_code(self.reason_code):
            raise NormalizationError("BCS_EVALUATION_INVALID")
        if type(self.actionable) is not bool or self.actionable:
            raise NormalizationError("BCS_EVALUATION_INVALID")
        if type(self.broker_order_count) is not int or self.broker_order_count != 0:
            raise NormalizationError("BCS_EVALUATION_INVALID")
        if (
            type(self.blocking_notifications) is not tuple
            or any(
                not isinstance(item, BlockingNotificationV1)
                for item in self.blocking_notifications
            )
        ):
            raise NormalizationError("BCS_EVALUATION_INVALID")
        if self.status == "RESEARCH_CANDIDATE":
            if (
                not isinstance(self.selection, BcsSelectionV1)
                or not isinstance(self.entry_cost, BcsEntryCostV1)
                or self.entry_cost.bcs_selection_sha256
                != self.selection.selection_sha256
                or self.option_snapshot_sha256
                != self.selection.option_snapshot_sha256
                or self.candidate_ledger_sha256
                != self.selection.candidate_ledger_sha256
                or self.blocking_notifications
                or self.reason_code != BcsReasonV1.RESEARCH_ONLY_NOT_ACTIONABLE
            ):
                raise NormalizationError("BCS_EVALUATION_INVALID")
        elif self.status == "NO_DECISION":
            if (
                self.selection is not None
                or self.entry_cost is not None
                or len(self.blocking_notifications) != 1
                or self.blocking_notifications[0].reason_code != self.reason_code
            ):
                raise NormalizationError("BCS_EVALUATION_INVALID")
        elif (
            self.selection is not None
            or self.entry_cost is not None
            or self.blocking_notifications
            or self.reason_code != BcsReasonV1.SIGNAL_FAIL
            or self.option_snapshot_sha256 is not None
        ):
            raise NormalizationError("BCS_EVALUATION_INVALID")


def _decision_artifact_outcome(
    lc0_status: str,
    bcs0_status: str,
) -> tuple[str, str, bool]:
    statuses = (lc0_status, bcs0_status)
    if "NO_DECISION" in statuses:
        return (
            "NO_DECISION",
            BcsReasonV1.CARRIER_EVALUATION_INCOMPLETE,
            False,
        )
    if statuses == ("PASS", "PASS"):
        return (
            "OWNER_SELECTION_REQUIRED",
            BcsReasonV1.BOTH_CARRIERS_PASS,
            True,
        )
    if lc0_status == "PASS":
        return (
            "SINGLE_CARRIER_RESEARCH_CANDIDATE",
            BcsReasonV1.LC0_ONLY_PASS,
            False,
        )
    if bcs0_status == "PASS":
        return (
            "SINGLE_CARRIER_RESEARCH_CANDIDATE",
            BcsReasonV1.BCS0_ONLY_PASS,
            False,
        )
    if statuses == ("FAIL", "FAIL"):
        return ("NO_ENTRY", BcsReasonV1.BOTH_CARRIERS_FAIL, False)
    raise NormalizationError("DECISION_ARTIFACT_INVALID")


def _build_decision_artifact_unsealed(
    *,
    signal: SignalSnapshotV1,
    candidate_ledger: OptionSnapshotCandidateLedgerV1 | None,
    long_call_selection: Lc0SelectionBindingV1,
    deltas: tuple[CrrDeltaEvidenceV1, ...],
    research_contract_sha256: str,
    rule_package_version: str,
    fees: BcsFeeScheduleV1,
    quantity: int,
    evaluation_engine,
    evaluation_verifier,
) -> DecisionArtifactV1:
    """Build a fail-closed artifact from typed inputs and sealed evaluation."""

    if (
        not isinstance(signal, SignalSnapshotV1)
        or not isinstance(fees, BcsFeeScheduleV1)
        or type(rule_package_version) is not str
        or rule_package_version != signal.rule_version
    ):
        raise NormalizationError("DECISION_ARTIFACT_INVALID")
    _require_sha256(
        research_contract_sha256,
        reason_code="DECISION_ARTIFACT_INVALID",
    )
    bcs0_evaluation = evaluation_engine(
        signal=signal,
        candidate_ledger=candidate_ledger,
        long_call_selection=long_call_selection,
        deltas=deltas,
        fees=fees,
        quantity=quantity,
    )
    if not evaluation_verifier(bcs0_evaluation):
        raise NormalizationError("DECISION_ARTIFACT_INVALID")
    if bcs0_evaluation.status == "RESEARCH_CANDIDATE":
        bcs0_status = "PASS"
        if (
            bcs0_evaluation.selection is None
            or bcs0_evaluation.entry_cost is None
            or bcs0_evaluation.entry_cost.fee_schedule_sha256
            != fees.fee_schedule_sha256
            or candidate_ledger is None
            or bcs0_evaluation.candidate_ledger_sha256
            != candidate_ledger.ledger_sha256
            or bcs0_evaluation.selection.candidate_ledger_sha256
            != candidate_ledger.ledger_sha256
        ):
            raise NormalizationError("DECISION_ARTIFACT_INVALID")
        bcs_selection = bcs0_evaluation.selection
    elif bcs0_evaluation.status == "NO_ENTRY":
        bcs0_status = "FAIL"
        bcs_selection = None
    else:
        bcs0_status = "NO_DECISION"
        bcs_selection = None
    option_snapshot_sha256 = bcs0_evaluation.option_snapshot_sha256
    bcs0_terminal = CarrierTerminalResultV1(
        carrier_id="BCS0",
        terminal_status=bcs0_status,
        reason_code=bcs0_evaluation.reason_code,
        signal_snapshot_sha256=signal.snapshot_sha256,
        option_snapshot_sha256=option_snapshot_sha256,
        result_sha256=canonical_snapshot_sha256(bcs0_evaluation),
    )
    lc0_receipt = "|".join(
        (
            BcsReasonV1.LC0_AUTHORITY_NOT_IMPLEMENTED,
            signal.snapshot_sha256,
            option_snapshot_sha256 or "NONE",
            research_contract_sha256,
            rule_package_version,
            signal.rule_sha256,
        )
    )
    lc0_terminal = CarrierTerminalResultV1(
        carrier_id="LC0",
        terminal_status="NO_DECISION",
        reason_code=BcsReasonV1.LC0_AUTHORITY_NOT_IMPLEMENTED,
        signal_snapshot_sha256=signal.snapshot_sha256,
        option_snapshot_sha256=option_snapshot_sha256,
        result_sha256=sha256(lc0_receipt.encode("ascii")).hexdigest(),
    )
    status, reason_code, owner_selection_required = _decision_artifact_outcome(
        lc0_terminal.terminal_status,
        bcs0_terminal.terminal_status,
    )
    return DecisionArtifactV1(
        signal_snapshot_sha256=signal.snapshot_sha256,
        option_snapshot_sha256=option_snapshot_sha256,
        candidate_ledger_sha256=bcs0_evaluation.candidate_ledger_sha256,
        research_contract_sha256=research_contract_sha256,
        rule_package_version=rule_package_version,
        rule_sha256=signal.rule_sha256,
        delta_model_sha256=MODEL_SHA256,
        fee_schedule_sha256=fees.fee_schedule_sha256,
        bcs_selection_sha256=(
            bcs_selection.selection_sha256 if bcs_selection is not None else None
        ),
        long_delta_evidence_sha256=(
            bcs_selection.long_delta_evidence_sha256
            if bcs_selection is not None
            else None
        ),
        short_delta_evidence_sha256=(
            bcs_selection.short_delta_evidence_sha256
            if bcs_selection is not None
            else None
        ),
        long_delta_run_sha256=(
            bcs_selection.long_delta_run_sha256
            if bcs_selection is not None
            else None
        ),
        short_delta_run_sha256=(
            bcs_selection.short_delta_run_sha256
            if bcs_selection is not None
            else None
        ),
        long_delta_runtime_fingerprint_sha256=(
            bcs_selection.long_delta_runtime_fingerprint_sha256
            if bcs_selection is not None
            else None
        ),
        short_delta_runtime_fingerprint_sha256=(
            bcs_selection.short_delta_runtime_fingerprint_sha256
            if bcs_selection is not None
            else None
        ),
        lc0_terminal=lc0_terminal,
        bcs0_terminal=bcs0_terminal,
        status=status,
        reason_code=reason_code,
        owner_selection_required=owner_selection_required,
        actionable=False,
        broker_order_count=0,
    )


@dataclass(frozen=True, slots=True)
class BcsLifecycleTransitionV1:
    current_state: str
    next_state: str
    episode_sha256: str | None
    bcs_selection_sha256: str | None
    previous_transition_sha256: str | None
    latched_exit_reason: str | None
    blocking_reason: str | None
    management_checkpoint_sha256: str | None
    reconciliation_receipt_sha256: str | None
    actionable: bool
    broker_order_count: int

    def __post_init__(self) -> None:
        if self.current_state not in _LIFECYCLE_STATES or self.next_state not in _LIFECYCLE_STATES:
            raise NormalizationError("BCS_LIFECYCLE_STATE_INVALID")
        if self.latched_exit_reason is not None:
            _canonical_reason_code(self.latched_exit_reason)
        if self.blocking_reason is not None:
            _canonical_reason_code(self.blocking_reason)
        for value in (
            self.episode_sha256,
            self.bcs_selection_sha256,
            self.previous_transition_sha256,
            self.management_checkpoint_sha256,
            self.reconciliation_receipt_sha256,
        ):
            if value is not None:
                _require_sha256(value, reason_code="BCS_LIFECYCLE_STATE_INVALID")
        if (
            (self.current_state != "PROPOSED" or self.next_state != "PROPOSED")
            and (
                self.episode_sha256 is None
                or self.bcs_selection_sha256 is None
            )
        ):
            raise NormalizationError("BCS_LIFECYCLE_LINEAGE_INVALID")
        if (
            (
                self.current_state != "PROPOSED"
                and self.previous_transition_sha256 is None
            )
            or (
                self.previous_transition_sha256 is not None
                and (
                    self.episode_sha256 is None
                    or self.bcs_selection_sha256 is None
                )
            )
            or (
                (
                    self.current_state in {"MANAGED", "HOLD", "EXIT_DUE"}
                    or self.next_state in {"MANAGED", "HOLD", "EXIT_DUE"}
                )
                and self.management_checkpoint_sha256 is None
            )
        ):
            raise NormalizationError("BCS_LIFECYCLE_LINEAGE_INVALID")
        if (
            type(self.actionable) is not bool
            or self.actionable
            or type(self.broker_order_count) is not int
            or self.broker_order_count != 0
        ):
            raise NormalizationError("BCS_LIFECYCLE_STATE_INVALID")

    @property
    def transition_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


def bind_lc0_selection_for_research(
    *,
    signal: SignalSnapshotV1,
    snapshot: OptionQuoteSnapshotV1,
    selector_contract_sha256: str,
    long_call_id: str,
) -> Lc0SelectionBindingV1:
    """Bind an already-selected LC0 fixture fact without granting authority."""

    if (
        not isinstance(signal, SignalSnapshotV1)
        or not isinstance(snapshot, OptionQuoteSnapshotV1)
        or snapshot.signal_snapshot_sha256 != signal.snapshot_sha256
    ):
        raise NormalizationError("BCS_LONG_LEG_BINDING_MISMATCH")
    _require_sha256(
        selector_contract_sha256,
        reason_code="BCS_LONG_LEG_BINDING_MISMATCH",
    )
    long_quote = next(
        (
            quote
            for quote in snapshot.option_quotes
            if quote.contract.occ_symbol == long_call_id
        ),
        None,
    )
    if long_quote is None:
        raise NormalizationError("BCS_LONG_LEG_BINDING_MISMATCH")
    return Lc0SelectionBindingV1(
        signal_snapshot_sha256=signal.snapshot_sha256,
        option_snapshot_sha256=snapshot.snapshot_sha256,
        selector_contract_sha256=selector_contract_sha256,
        long_call_id=long_call_id,
        long_contract_sha256=canonical_snapshot_sha256(long_quote.contract),
    )


def _validate_candidate_ledger_selection(
    *,
    candidate_ledger: OptionSnapshotCandidateLedgerV1,
    snapshot: OptionQuoteSnapshotV1,
) -> str:
    """Prove that ``snapshot`` is the ledger's first quality-complete fact."""

    if (
        type(candidate_ledger) is not OptionSnapshotCandidateLedgerV1
        or type(snapshot) is not OptionQuoteSnapshotV1
        or candidate_ledger.signal_snapshot_sha256
        != snapshot.signal_snapshot_sha256
        or candidate_ledger.quote_quality_policy_version
        != QUOTE_QUALITY_POLICY_VERSION
        or candidate_ledger.quote_quality_policy_sha256
        != QUOTE_QUALITY_POLICY_SHA256
    ):
        raise NormalizationError("BCS_CANDIDATE_LEDGER_BINDING_MISMATCH")
    try:
        validate_option_snapshot_quality(snapshot)
    except NormalizationError as error:
        raise NormalizationError(
            _canonical_reason_code(error.reason_code)
        ) from error

    selected_index = next(
        (
            index
            for index, candidate in enumerate(candidate_ledger.candidates)
            if candidate.snapshot_sha256 == snapshot.snapshot_sha256
        ),
        None,
    )
    if selected_index is None:
        raise NormalizationError("BCS_CANDIDATE_LEDGER_BINDING_MISMATCH")
    for preceding in candidate_ledger.candidates[:selected_index]:
        try:
            validate_option_snapshot_quality(preceding)
        except NormalizationError:
            continue
        raise NormalizationError("BCS_CANDIDATE_LEDGER_BINDING_MISMATCH")
    return candidate_ledger.ledger_sha256


def _validated_delta_map(
    snapshot: OptionQuoteSnapshotV1,
    delta_evidence: object,
    *,
    snapshot_sha256: str,
) -> dict[str, CrrDeltaEvidenceV1]:
    if type(delta_evidence) is not tuple or any(
        not isinstance(item, CrrDeltaEvidenceV1) for item in delta_evidence
    ):
        raise NormalizationError("DELTA_INPUT_BINDING_MISMATCH")
    expected_ids = tuple(
        quote.contract.occ_symbol for quote in snapshot.option_quotes
    )
    actual_ids = tuple(item.result.contract_id for item in delta_evidence)
    input_hashes = tuple(item.inputs.input_sha256 for item in delta_evidence)
    if (
        len(actual_ids) != len(expected_ids)
        or set(actual_ids) != set(expected_ids)
        or len(set(input_hashes)) != len(input_hashes)
        or any(
            item.result.option_snapshot_sha256 != snapshot_sha256
            or item.inputs.option_snapshot_sha256 != snapshot_sha256
            or item.result.model_sha256 != MODEL_SHA256
            or not is_verified_crr_delta_result(item.result)
            for item in delta_evidence
        )
    ):
        raise NormalizationError("DELTA_INPUT_BINDING_MISMATCH")
    evidence_by_id = {
        item.result.contract_id: item for item in delta_evidence
    }
    spot_mid = _exact_midpoint(
        snapshot.underlying_top.bid_nano_usd,
        snapshot.underlying_top.ask_nano_usd,
    )
    for quote in snapshot.option_quotes:
        contract = quote.contract
        item = evidence_by_id[contract.occ_symbol]
        inputs = item.inputs
        option_mid = _exact_midpoint(
            quote.top_of_book.bid_nano_usd,
            quote.top_of_book.ask_nano_usd,
        )
        if (
            inputs.contract_id != contract.occ_symbol
            or inputs.contract_sha256 != canonical_snapshot_sha256(contract)
            or inputs.underlying_top_sha256
            != canonical_snapshot_sha256(snapshot.underlying_top)
            or inputs.option_quote_sha256 != canonical_snapshot_sha256(quote)
            or inputs.spot_nano_usd != spot_mid
            or inputs.strike_nano_usd != contract.strike_nano_usd
            or inputs.option_mid_nano_usd != option_mid
            or inputs.tick_nano_usd != quote.top_of_book.tick_nano_usd
            or inputs.valuation_utc_ns != snapshot.capture_utc_ns
            or inputs.expiry_utc_ns != contract.expiry_utc_ns
            or item.result.input_sha256 != inputs.input_sha256
        ):
            raise NormalizationError("DELTA_INPUT_BINDING_MISMATCH")
    return evidence_by_id


def _is_pair_eligible(
    long_quote: OptionQuoteV1,
    short_quote: OptionQuoteV1,
) -> bool:
    long_contract = long_quote.contract
    short_contract = short_quote.contract
    return (
        short_contract.right == "C"
        and short_contract.exercise_style == "AMERICAN"
        and long_contract.exercise_style == "AMERICAN"
        and short_contract.expiry_utc_ns == long_contract.expiry_utc_ns
        and short_contract.strike_nano_usd > long_contract.strike_nano_usd
        and short_contract.multiplier == long_contract.multiplier
        and short_contract.deliverable == long_contract.deliverable
        and short_contract.currency == long_contract.currency
        and short_contract.standard_unadjusted
        and long_contract.standard_unadjusted
    )


def _select_suite_winner(
    *,
    long_quote: OptionQuoteV1,
    candidates: tuple[OptionQuoteV1, ...],
    delta_by_id: dict[str, CrrDeltaEvidenceV1],
    suite: str,
) -> OptionQuoteV1:
    if suite == "coarse":
        long_delta = (
            delta_by_id[long_quote.contract.occ_symbol].result.coarse_delta_ppm
        )
        delta_field = "coarse_delta_ppm"
    else:
        long_delta = (
            delta_by_id[long_quote.contract.occ_symbol].result.fine_delta_ppm
        )
        delta_field = "fine_delta_ppm"
    eligible: list[OptionQuoteV1] = []
    for candidate in candidates:
        delta = getattr(
            delta_by_id[candidate.contract.occ_symbol].result,
            delta_field,
        )
        if (
            _is_pair_eligible(long_quote, candidate)
            and _SHORT_DELTA_MIN_PPM <= delta <= _SHORT_DELTA_MAX_PPM
            and delta < long_delta
        ):
            eligible.append(candidate)
    if not eligible:
        raise NormalizationError("BCS_NO_ELIGIBLE_SHORT_CALL")
    return min(
        eligible,
        key=lambda quote: (
            abs(
                getattr(
                    delta_by_id[quote.contract.occ_symbol].result,
                    delta_field,
                )
                - _SHORT_DELTA_TARGET_PPM
            ),
            -quote.contract.strike_nano_usd,
            quote.contract.occ_symbol,
        ),
    )


def select_bcs_short_call(
    *,
    long_call_selection: Lc0SelectionBindingV1,
    candidate_ledger: OptionSnapshotCandidateLedgerV1,
    snapshot: OptionQuoteSnapshotV1,
    deltas: tuple[CrrDeltaEvidenceV1, ...],
) -> BcsSelectionV1:
    """Select one stable same-expiry short Call around 0.25 Delta."""

    if not isinstance(snapshot, OptionQuoteSnapshotV1):
        raise NormalizationError("BCS_LONG_LEG_BINDING_MISMATCH")
    if not isinstance(long_call_selection, Lc0SelectionBindingV1):
        raise NormalizationError("BCS_LONG_LEG_BINDING_MISMATCH")
    candidate_ledger_sha256 = _validate_candidate_ledger_selection(
        candidate_ledger=candidate_ledger,
        snapshot=snapshot,
    )
    snapshot_sha256 = snapshot.snapshot_sha256
    if (
        long_call_selection.option_snapshot_sha256 != snapshot_sha256
        or long_call_selection.signal_snapshot_sha256
        != snapshot.signal_snapshot_sha256
    ):
        raise NormalizationError("BCS_LONG_LEG_BINDING_MISMATCH")
    quote_by_id = {
        quote.contract.occ_symbol: quote for quote in snapshot.option_quotes
    }
    long_quote = quote_by_id.get(long_call_selection.long_call_id)
    if (
        long_quote is None
        or canonical_snapshot_sha256(long_quote.contract)
        != long_call_selection.long_contract_sha256
    ):
        raise NormalizationError("BCS_LONG_LEG_BINDING_MISMATCH")
    delta_by_id = _validated_delta_map(
        snapshot,
        deltas,
        snapshot_sha256=snapshot_sha256,
    )
    candidates = tuple(
        quote
        for quote in snapshot.option_quotes
        if quote.contract.occ_symbol != long_call_selection.long_call_id
    )
    coarse_winner = _select_suite_winner(
        long_quote=long_quote,
        candidates=candidates,
        delta_by_id=delta_by_id,
        suite="coarse",
    )
    fine_winner = _select_suite_winner(
        long_quote=long_quote,
        candidates=candidates,
        delta_by_id=delta_by_id,
        suite="fine",
    )
    if coarse_winner.contract.occ_symbol != fine_winner.contract.occ_symbol:
        raise NormalizationError(BcsReasonV1.SELECTOR_MODEL_INSTABILITY)
    long_evidence = delta_by_id[long_call_selection.long_call_id]
    short_evidence = delta_by_id[fine_winner.contract.occ_symbol]
    if long_evidence.inputs.pit_inputs != short_evidence.inputs.pit_inputs:
        raise NormalizationError(BcsReasonV1.DELTA_PIT_INPUT_MISMATCH)
    long_result = long_evidence.result
    short_result = short_evidence.result
    return BcsSelectionV1(
        lc0_selection_sha256=long_call_selection.binding_sha256,
        candidate_ledger_sha256=candidate_ledger_sha256,
        quote_quality_policy_version=QUOTE_QUALITY_POLICY_VERSION,
        quote_quality_policy_sha256=QUOTE_QUALITY_POLICY_SHA256,
        long_call_id=long_call_selection.long_call_id,
        short_call_id=fine_winner.contract.occ_symbol,
        long_contract_sha256=long_call_selection.long_contract_sha256,
        short_contract_sha256=canonical_snapshot_sha256(fine_winner.contract),
        delta_model_sha256=long_result.model_sha256,
        long_delta_input_sha256=long_result.input_sha256,
        short_delta_input_sha256=short_result.input_sha256,
        long_delta_evidence_sha256=long_evidence.evidence_sha256,
        short_delta_evidence_sha256=short_evidence.evidence_sha256,
        long_delta_run_sha256=long_result.run_sha256,
        short_delta_run_sha256=short_result.run_sha256,
        long_delta_runtime_fingerprint_sha256=(
            long_result.runtime_fingerprint_sha256
        ),
        short_delta_runtime_fingerprint_sha256=(
            short_result.runtime_fingerprint_sha256
        ),
        long_strike_nano_usd=long_quote.contract.strike_nano_usd,
        short_strike_nano_usd=fine_winner.contract.strike_nano_usd,
        long_delta_ppm=long_result.fine_delta_ppm,
        short_delta_ppm=short_result.fine_delta_ppm,
        net_delta_ppm=long_result.fine_delta_ppm - short_result.fine_delta_ppm,
        multiplier=long_quote.contract.multiplier,
        option_snapshot_sha256=snapshot_sha256,
    )


def _bound_leg_quotes(
    *,
    selection: BcsSelectionV1,
    snapshot: OptionQuoteSnapshotV1,
    require_entry_snapshot: bool,
) -> tuple[OptionQuoteV1, OptionQuoteV1]:
    snapshot_sha256 = (
        snapshot.snapshot_sha256
        if isinstance(snapshot, OptionQuoteSnapshotV1)
        else None
    )
    if (
        not isinstance(selection, BcsSelectionV1)
        or not isinstance(snapshot, OptionQuoteSnapshotV1)
        or type(require_entry_snapshot) is not bool
        or (
            require_entry_snapshot
            and selection.option_snapshot_sha256 != snapshot_sha256
        )
    ):
        raise NormalizationError("BCS_LONG_LEG_BINDING_MISMATCH")
    quote_by_id = {
        quote.contract.occ_symbol: quote for quote in snapshot.option_quotes
    }
    try:
        long_quote = quote_by_id[selection.long_call_id]
        short_quote = quote_by_id[selection.short_call_id]
    except KeyError as error:
        raise NormalizationError("BCS_LONG_LEG_BINDING_MISMATCH") from error
    if (
        long_quote.contract.strike_nano_usd != selection.long_strike_nano_usd
        or short_quote.contract.strike_nano_usd != selection.short_strike_nano_usd
        or long_quote.contract.multiplier != selection.multiplier
        or short_quote.contract.multiplier != selection.multiplier
        or canonical_snapshot_sha256(long_quote.contract)
        != selection.long_contract_sha256
        or canonical_snapshot_sha256(short_quote.contract)
        != selection.short_contract_sha256
        or not _is_pair_eligible(long_quote, short_quote)
    ):
        raise NormalizationError("BCS_LONG_LEG_BINDING_MISMATCH")
    return long_quote, short_quote


def _validated_fees(
    fees: BcsFeeScheduleV1,
    *,
    as_of_utc_ns: int,
) -> BcsFeeScheduleV1:
    if not isinstance(fees, BcsFeeScheduleV1):
        raise NormalizationError("BCS_FEE_SCHEDULE_MISSING")
    if fees.effective_from_utc_ns > as_of_utc_ns:
        raise NormalizationError("BCS_FEE_SCHEDULE_MISSING")
    return fees


def _validated_quantity(quantity: object) -> int:
    return _require_exact_int(
        quantity,
        reason_code="BCS_INVALID_QUANTITY",
        minimum=1,
        maximum=_MAX_DEFINED_U32,
    )


def open_bcs_trade_episode_for_research(
    *,
    episode_id: str,
    signal: SignalSnapshotV1,
    selection: BcsSelectionV1,
    entry_snapshot: OptionQuoteSnapshotV1,
    entry_cost: BcsEntryCostV1,
) -> BcsTradeEpisodeV1:
    """Bind a synthetic entry checkpoint without asserting a broker position."""

    if (
        not isinstance(signal, SignalSnapshotV1)
        or not isinstance(selection, BcsSelectionV1)
        or not isinstance(entry_snapshot, OptionQuoteSnapshotV1)
        or not isinstance(entry_cost, BcsEntryCostV1)
        or entry_snapshot.signal_snapshot_sha256 != signal.snapshot_sha256
        or selection.option_snapshot_sha256 != entry_snapshot.snapshot_sha256
        or entry_cost.option_snapshot_sha256 != entry_snapshot.snapshot_sha256
        or entry_cost.bcs_selection_sha256 != selection.selection_sha256
    ):
        raise NormalizationError("BCS_EPISODE_BINDING_MISMATCH")
    _bound_leg_quotes(
        selection=selection,
        snapshot=entry_snapshot,
        require_entry_snapshot=True,
    )
    return BcsTradeEpisodeV1(
        episode_id=episode_id,
        signal_snapshot_sha256=signal.snapshot_sha256,
        bcs_selection_sha256=selection.selection_sha256,
        entry_option_snapshot_sha256=entry_snapshot.snapshot_sha256,
        entry_cost_sha256=entry_cost.cost_sha256,
        entry_capture_utc_ns=entry_snapshot.capture_utc_ns,
        quantity=entry_cost.quantity,
    )


def bind_bcs_management_snapshot_for_research(
    *,
    episode: BcsTradeEpisodeV1,
    selection: BcsSelectionV1,
    snapshot: OptionQuoteSnapshotV1,
) -> BcsManagementSnapshotV1:
    """Qualify a later pair snapshot without applying the entry-window rule."""

    if (
        not isinstance(episode, BcsTradeEpisodeV1)
        or not isinstance(selection, BcsSelectionV1)
        or not isinstance(snapshot, OptionQuoteSnapshotV1)
        or episode.bcs_selection_sha256 != selection.selection_sha256
        or snapshot.signal_snapshot_sha256 != episode.signal_snapshot_sha256
        or snapshot.capture_utc_ns <= episode.entry_capture_utc_ns
    ):
        raise NormalizationError("BCS_MANAGEMENT_SNAPSHOT_BINDING_MISMATCH")
    long_quote, short_quote = _bound_leg_quotes(
        selection=selection,
        snapshot=snapshot,
        require_entry_snapshot=False,
    )
    pair_snapshot = replace(
        snapshot,
        option_quotes=(long_quote, short_quote),
    )
    try:
        validate_option_snapshot_quality(
            pair_snapshot,
        )
    except NormalizationError as error:
        raise NormalizationError(
            _canonical_reason_code(error.reason_code)
        ) from error
    return BcsManagementSnapshotV1(
        episode_sha256=episode.episode_sha256,
        bcs_selection_sha256=selection.selection_sha256,
        option_snapshot_sha256=snapshot.snapshot_sha256,
        source_receipt_sha256=snapshot.source_receipt_sha256,
        capture_utc_ns=snapshot.capture_utc_ns,
        quote_quality_policy_version=QUOTE_QUALITY_POLICY_VERSION,
        quote_quality_policy_sha256=QUOTE_QUALITY_POLICY_SHA256,
        authority_status="UNQUALIFIED_RESEARCH_ONLY",
    )


def price_bcs_entry(
    *,
    selection: BcsSelectionV1,
    snapshot: OptionQuoteSnapshotV1,
    fees: BcsFeeScheduleV1,
    quantity: int,
) -> BcsEntryCostV1:
    """Price entry from executable sides only, including adverse tick stress."""

    try:
        validate_option_snapshot_quality(snapshot)
    except NormalizationError as error:
        raise NormalizationError(
            _canonical_reason_code(error.reason_code)
        ) from error
    long_quote, short_quote = _bound_leg_quotes(
        selection=selection,
        snapshot=snapshot,
        require_entry_snapshot=True,
    )
    fee_schedule = _validated_fees(
        fees,
        as_of_utc_ns=snapshot.capture_utc_ns,
    )
    size = _validated_quantity(quantity)
    long_book = long_quote.top_of_book
    short_book = short_quote.top_of_book
    if long_book.ask_size < size or short_book.bid_size < size:
        raise NormalizationError("BCS_SIZE_INSUFFICIENT")
    multiplier_quantity = selection.multiplier * size
    entry_fees = (
        fee_schedule.long_entry_fee_nano_usd_per_contract
        + fee_schedule.short_entry_fee_nano_usd_per_contract
    ) * size
    base_debit = (
        long_book.ask_nano_usd - short_book.bid_nano_usd
    ) * multiplier_quantity + entry_fees
    stressed_short_bid = max(
        0,
        short_book.bid_nano_usd - short_book.tick_nano_usd,
    )
    stressed_debit = (
        long_book.ask_nano_usd
        + long_book.tick_nano_usd
        - stressed_short_bid
    ) * multiplier_quantity + entry_fees
    gross_width = (
        selection.short_strike_nano_usd - selection.long_strike_nano_usd
    ) * multiplier_quantity
    if (
        not 0 < base_debit < gross_width
        or not 0 < stressed_debit < gross_width
        or max(base_debit, stressed_debit, gross_width) > _MAX_DEFINED_I64
    ):
        raise NormalizationError("BCS_INVALID_NET_DEBIT")
    return BcsEntryCostV1(
        bcs_selection_sha256=selection.selection_sha256,
        option_snapshot_sha256=snapshot.snapshot_sha256,
        fee_schedule_sha256=fee_schedule.fee_schedule_sha256,
        quantity=size,
        gross_expiry_width_value_nano_usd=gross_width,
        base_net_debit_nano_usd=base_debit,
        stressed_net_debit_nano_usd=stressed_debit,
        max_loss_nano_usd=base_debit,
        theoretical_expiry_max_profit_nano_usd=gross_width - base_debit,
        theoretical_expiry_only=True,
    )


def price_bcs_exit(
    *,
    selection: BcsSelectionV1,
    episode: BcsTradeEpisodeV1,
    management_snapshot: BcsManagementSnapshotV1,
    snapshot: OptionQuoteSnapshotV1,
    fees: BcsFeeScheduleV1,
    quantity: int,
) -> BcsExitCostV1:
    """Price a full combo exit from executable sides with adverse tick stress."""

    if (
        not isinstance(episode, BcsTradeEpisodeV1)
        or not isinstance(management_snapshot, BcsManagementSnapshotV1)
    ):
        raise NormalizationError("BCS_MANAGEMENT_SNAPSHOT_BINDING_MISMATCH")
    expected_checkpoint = bind_bcs_management_snapshot_for_research(
        episode=episode,
        selection=selection,
        snapshot=snapshot,
    )
    if expected_checkpoint != management_snapshot:
        raise NormalizationError("BCS_MANAGEMENT_SNAPSHOT_BINDING_MISMATCH")
    long_quote, short_quote = _bound_leg_quotes(
        selection=selection,
        snapshot=snapshot,
        require_entry_snapshot=False,
    )
    fee_schedule = _validated_fees(
        fees,
        as_of_utc_ns=snapshot.capture_utc_ns,
    )
    size = _validated_quantity(quantity)
    if size != episode.quantity:
        raise NormalizationError("BCS_INVALID_QUANTITY")
    long_book = long_quote.top_of_book
    short_book = short_quote.top_of_book
    if long_book.bid_size < size or short_book.ask_size < size:
        raise NormalizationError("BCS_SIZE_INSUFFICIENT")
    multiplier_quantity = selection.multiplier * size
    exit_fees = (
        fee_schedule.long_exit_fee_nano_usd_per_contract
        + fee_schedule.short_exit_fee_nano_usd_per_contract
    ) * size
    base_credit = (
        long_book.bid_nano_usd - short_book.ask_nano_usd
    ) * multiplier_quantity - exit_fees
    stressed_long_bid = max(
        0,
        long_book.bid_nano_usd - long_book.tick_nano_usd,
    )
    stressed_credit = (
        stressed_long_bid
        - short_book.ask_nano_usd
        - short_book.tick_nano_usd
    ) * multiplier_quantity - exit_fees
    if (
        not -_MAX_DEFINED_I64 <= stressed_credit <= base_credit <= _MAX_DEFINED_I64
    ):
        raise NormalizationError("BCS_EXIT_COST_INVALID")
    return BcsExitCostV1(
        bcs_selection_sha256=selection.selection_sha256,
        option_snapshot_sha256=snapshot.snapshot_sha256,
        fee_schedule_sha256=fee_schedule.fee_schedule_sha256,
        quantity=size,
        base_net_credit_nano_usd=base_credit,
        stressed_net_credit_nano_usd=stressed_credit,
    )


def _reconciliation_blocking_reason(
    *,
    snapshot_b: BrokerReconciliationSnapshotBV1,
    episode: BcsTradeEpisodeV1 | None,
    selection: BcsSelectionV1 | None,
) -> str:
    if (
        not isinstance(episode, BcsTradeEpisodeV1)
        or not isinstance(selection, BcsSelectionV1)
        or episode.bcs_selection_sha256 != selection.selection_sha256
        or snapshot_b.episode_sha256 != episode.episode_sha256
        or snapshot_b.bcs_selection_sha256 != selection.selection_sha256
        or snapshot_b.long_contract_sha256 != selection.long_contract_sha256
        or snapshot_b.short_contract_sha256 != selection.short_contract_sha256
        or snapshot_b.capture_utc_ns <= episode.entry_capture_utc_ns
    ):
        return BcsReasonV1.RECONCILIATION_IDENTITY_MISMATCH
    if snapshot_b.pending_assignment:
        return BcsReasonV1.RECONCILIATION_PENDING_ASSIGNMENT
    if snapshot_b.pending_exercise:
        return BcsReasonV1.RECONCILIATION_PENDING_EXERCISE
    if (
        snapshot_b.long_position_quantity != 0
        or snapshot_b.short_position_quantity != 0
    ):
        return BcsReasonV1.RECONCILIATION_POSITION_MISMATCH
    if snapshot_b.open_order_count != 0:
        return BcsReasonV1.RECONCILIATION_OPEN_ORDERS
    if not snapshot_b.terminal_fees_final:
        return BcsReasonV1.RECONCILIATION_TERMINAL_FEES_UNFINALIZED
    return BcsReasonV1.RECONCILIATION_AUTHORITY_UNAVAILABLE


def _validate_management_lineage(
    *,
    episode: BcsTradeEpisodeV1 | None,
    selection: BcsSelectionV1 | None,
    management_snapshot: BcsManagementSnapshotV1 | None,
) -> bool:
    if management_snapshot is None:
        return False
    if (
        not isinstance(episode, BcsTradeEpisodeV1)
        or not isinstance(selection, BcsSelectionV1)
        or not isinstance(management_snapshot, BcsManagementSnapshotV1)
        or episode.bcs_selection_sha256 != selection.selection_sha256
        or management_snapshot.episode_sha256 != episode.episode_sha256
        or management_snapshot.bcs_selection_sha256 != selection.selection_sha256
        or management_snapshot.capture_utc_ns <= episode.entry_capture_utc_ns
    ):
        raise NormalizationError("BCS_MANAGEMENT_SNAPSHOT_BINDING_MISMATCH")
    return True


def next_bcs_lifecycle_state(
    *,
    current_state: str,
    events: tuple[str, ...] = (),
    latched_exit_reason: str | None = None,
    episode: BcsTradeEpisodeV1 | None = None,
    selection: BcsSelectionV1 | None = None,
    management_snapshot: BcsManagementSnapshotV1 | None = None,
    reconciliation_snapshot_b: BrokerReconciliationSnapshotBV1 | None = None,
    previous_transition: BcsLifecycleTransitionV1 | None = None,
) -> BcsLifecycleTransitionV1:
    """Advance research state while withholding all broker terminal authority."""

    if current_state not in _LIFECYCLE_STATES:
        raise NormalizationError("BCS_LIFECYCLE_STATE_INVALID")
    if (
        type(events) is not tuple
        or len(set(events)) != len(events)
        or any(type(event) is not str or event not in _LIFECYCLE_EVENTS for event in events)
    ):
        raise NormalizationError("BCS_LIFECYCLE_STATE_INVALID")
    has_episode = isinstance(episode, BcsTradeEpisodeV1)
    has_selection = isinstance(selection, BcsSelectionV1)
    if has_episode != has_selection:
        raise NormalizationError(BcsReasonV1.LIFECYCLE_LINEAGE_INVALID)
    if has_episode and has_selection:
        assert episode is not None and selection is not None
        if episode.bcs_selection_sha256 != selection.selection_sha256:
            raise NormalizationError(BcsReasonV1.LIFECYCLE_LINEAGE_INVALID)
        episode_sha256 = episode.episode_sha256
        selection_sha256 = selection.selection_sha256
    else:
        episode_sha256 = None
        selection_sha256 = None
    if current_state != "PROPOSED":
        if (
            episode_sha256 is None
            or selection_sha256 is None
            or not isinstance(previous_transition, BcsLifecycleTransitionV1)
            or previous_transition.next_state != current_state
            or previous_transition.episode_sha256 != episode_sha256
            or previous_transition.bcs_selection_sha256 != selection_sha256
            or (
                current_state in {"MANAGED", "HOLD", "EXIT_DUE"}
                and previous_transition.management_checkpoint_sha256 is None
            )
        ):
            raise NormalizationError(BcsReasonV1.LIFECYCLE_LINEAGE_INVALID)
    elif previous_transition is not None:
        if (
            episode_sha256 is None
            or selection_sha256 is None
            or not isinstance(previous_transition, BcsLifecycleTransitionV1)
            or previous_transition.current_state != "PROPOSED"
            or previous_transition.next_state != "PROPOSED"
            or previous_transition.episode_sha256 != episode_sha256
            or previous_transition.bcs_selection_sha256 != selection_sha256
            or previous_transition.blocking_reason
            != BcsReasonV1.MANAGEMENT_CHECKPOINT_REQUIRED
            or previous_transition.management_checkpoint_sha256 is not None
            or previous_transition.reconciliation_receipt_sha256 is not None
        ):
            raise NormalizationError(BcsReasonV1.LIFECYCLE_LINEAGE_INVALID)
    event_set = frozenset(events)
    triggered_exit_reason = next(
        (
            reason
            for reason in (
                "POLICY_FULL_EXIT",
                "EXPIRY_SAFETY",
                "SIGNAL_INVALIDATION",
                "H20",
            )
            if reason in event_set
        ),
        None,
    )
    caller_latch_receipt = (
        _canonical_reason_code(latched_exit_reason)
        if latched_exit_reason is not None
        else None
    )
    previous_latch = (
        previous_transition.latched_exit_reason
        if previous_transition is not None
        else None
    )
    effective_exit_reason = previous_latch or triggered_exit_reason
    if (
        caller_latch_receipt is not None
        and caller_latch_receipt != effective_exit_reason
    ):
        raise NormalizationError(BcsReasonV1.LIFECYCLE_LATCH_INVALID)
    if current_state == "EXIT_DUE" and effective_exit_reason is None:
        raise NormalizationError("BCS_LIFECYCLE_STATE_INVALID")
    management_is_valid = _validate_management_lineage(
        episode=episode,
        selection=selection,
        management_snapshot=management_snapshot,
    )
    management_hash = (
        management_snapshot.checkpoint_sha256
        if management_is_valid and management_snapshot is not None
        else (
            previous_transition.management_checkpoint_sha256
            if previous_transition is not None
            else None
        )
    )
    previous_transition_sha256 = (
        previous_transition.transition_sha256
        if previous_transition is not None
        else None
    )
    receipt_hash = None
    if reconciliation_snapshot_b is not None:
        if not isinstance(
            reconciliation_snapshot_b,
            BrokerReconciliationSnapshotBV1,
        ):
            raise NormalizationError("BCS_LIFECYCLE_STATE_INVALID")
        receipt_hash = reconciliation_snapshot_b.receipt_sha256

    def transition(
        *,
        next_state: str,
        exit_reason: str | None,
        blocking_reason: str | None = None,
    ) -> BcsLifecycleTransitionV1:
        return BcsLifecycleTransitionV1(
            current_state=current_state,
            next_state=next_state,
            episode_sha256=episode_sha256,
            bcs_selection_sha256=selection_sha256,
            previous_transition_sha256=previous_transition_sha256,
            latched_exit_reason=exit_reason,
            blocking_reason=blocking_reason,
            management_checkpoint_sha256=management_hash,
            reconciliation_receipt_sha256=receipt_hash,
            actionable=False,
            broker_order_count=0,
        )

    if reconciliation_snapshot_b is not None:
        reconciliation_reason = _reconciliation_blocking_reason(
            snapshot_b=reconciliation_snapshot_b,
            episode=episode,
            selection=selection,
        )
        return transition(
            next_state="RECONCILIATION_BLOCKED",
            exit_reason=effective_exit_reason,
            blocking_reason=reconciliation_reason,
        )
    if event_set & _RECONCILIATION_BLOCKING_EVENTS:
        blocking_reason = next(
            event
            for event in (
                "IDENTITY_MISMATCH",
                "CONTRACT_ADJUSTMENT",
                "ASSIGNMENT_DETECTED",
                "EXERCISE_DETECTED",
                "PARTIAL_FILL",
                "RESIDUAL_POSITION",
                "BROKER_STATE_UNKNOWN",
            )
            if event in event_set
        )
        return transition(
            next_state="RECONCILIATION_BLOCKED",
            exit_reason=effective_exit_reason,
            blocking_reason=blocking_reason,
        )
    if current_state == "RECONCILIATION_BLOCKED":
        return transition(
            next_state=current_state,
            exit_reason=effective_exit_reason,
            blocking_reason=BcsReasonV1.RECONCILIATION_AUTHORITY_UNAVAILABLE,
        )
    if current_state == "EXIT_DUE":
        return transition(
            next_state="EXIT_DUE",
            exit_reason=effective_exit_reason,
            blocking_reason=BcsReasonV1.EXIT_DUE_NONEXECUTABLE,
        )
    if current_state == "PROPOSED" and not management_is_valid:
        return transition(
            next_state="PROPOSED",
            exit_reason=effective_exit_reason,
            blocking_reason=BcsReasonV1.MANAGEMENT_CHECKPOINT_REQUIRED,
        )
    return transition(
        next_state=(
            "EXIT_DUE" if effective_exit_reason is not None
            else "MANAGED"
            if current_state == "PROPOSED"
            else "HOLD"
        ),
        exit_reason=effective_exit_reason,
    )


def _notification_for(
    *,
    reason_code: str,
    signal_snapshot_sha256: str,
    option_snapshot_sha256: str | None,
) -> BlockingNotificationV1:
    reason_code = _canonical_reason_code(reason_code)
    message_key = (
        "gld.bcs.ibkr_executable_quote_unavailable"
        if reason_code == BcsReasonV1.IBKR_EXECUTABLE_QUOTE_UNAVAILABLE
        else "gld.bcs.research_blocked"
    )
    canonical = "|".join(
        (
            reason_code,
            message_key,
            signal_snapshot_sha256,
            option_snapshot_sha256 or "NONE",
        )
    )
    return BlockingNotificationV1(
        reason_code=reason_code,
        message_key=message_key,
        dedupe_key=sha256(canonical.encode("ascii")).hexdigest(),
        signal_snapshot_sha256=signal_snapshot_sha256,
        option_snapshot_sha256=option_snapshot_sha256,
    )


def _no_decision(
    *,
    reason_code: str,
    signal: SignalSnapshotV1,
    snapshot: OptionQuoteSnapshotV1 | None,
    candidate_ledger_sha256: str | None,
) -> BcsEvaluationV1:
    reason_code = _canonical_reason_code(reason_code)
    option_hash = snapshot.snapshot_sha256 if snapshot is not None else None
    notification = _notification_for(
        reason_code=reason_code,
        signal_snapshot_sha256=signal.snapshot_sha256,
        option_snapshot_sha256=option_hash,
    )
    return BcsEvaluationV1(
        signal_snapshot_sha256=signal.snapshot_sha256,
        option_snapshot_sha256=option_hash,
        candidate_ledger_sha256=candidate_ledger_sha256,
        status="NO_DECISION",
        reason_code=reason_code,
        actionable=False,
        broker_order_count=0,
        selection=None,
        entry_cost=None,
        blocking_notifications=(notification,),
    )


def _evaluate_bcs_research_unsealed(
    *,
    signal: SignalSnapshotV1,
    candidate_ledger: OptionSnapshotCandidateLedgerV1 | None,
    long_call_selection: Lc0SelectionBindingV1,
    deltas: tuple[CrrDeltaEvidenceV1, ...],
    fees: BcsFeeScheduleV1 | None,
    quantity: int,
) -> BcsEvaluationV1:
    """Return only research status; never execution eligibility or an order."""

    if not isinstance(signal, SignalSnapshotV1):
        raise NormalizationError("SIGNAL_SNAPSHOT_INVALID")
    if candidate_ledger is not None:
        if (
            type(candidate_ledger) is not OptionSnapshotCandidateLedgerV1
            or candidate_ledger.signal_snapshot_sha256
            != signal.snapshot_sha256
            or candidate_ledger.window_start_utc_ns != signal.cutoff_utc_ns
        ):
            raise NormalizationError("OPTION_SNAPSHOT_BINDING_MISMATCH")
        candidate_ledger_sha256 = candidate_ledger.ledger_sha256
    else:
        candidate_ledger_sha256 = None
    if signal.signal_state == "FAIL":
        return BcsEvaluationV1(
            signal_snapshot_sha256=signal.snapshot_sha256,
            option_snapshot_sha256=None,
            candidate_ledger_sha256=candidate_ledger_sha256,
            status="NO_ENTRY",
            reason_code=BcsReasonV1.SIGNAL_FAIL,
            actionable=False,
            broker_order_count=0,
            selection=None,
            entry_cost=None,
            blocking_notifications=(),
        )
    if signal.signal_state == "NOT_EVALUABLE":
        return _no_decision(
            reason_code=BcsReasonV1.SIGNAL_NOT_EVALUABLE,
            signal=signal,
            snapshot=None,
            candidate_ledger_sha256=candidate_ledger_sha256,
        )
    if candidate_ledger is None:
        return _no_decision(
            reason_code=BcsReasonV1.IBKR_EXECUTABLE_QUOTE_UNAVAILABLE,
            signal=signal,
            snapshot=None,
            candidate_ledger_sha256=None,
        )
    try:
        selected_snapshot = select_first_complete_option_snapshot(
            candidate_ledger,
            signal_snapshot=signal,
        )
    except NormalizationError as error:
        return _no_decision(
            reason_code=_canonical_reason_code(error.reason_code),
            signal=signal,
            snapshot=None,
            candidate_ledger_sha256=candidate_ledger_sha256,
        )
    try:
        selection = select_bcs_short_call(
            long_call_selection=long_call_selection,
            candidate_ledger=candidate_ledger,
            snapshot=selected_snapshot,
            deltas=deltas,
        )
        entry_cost = price_bcs_entry(
            selection=selection,
            snapshot=selected_snapshot,
            fees=fees,  # type: ignore[arg-type]
            quantity=quantity,
        )
    except NormalizationError as error:
        return _no_decision(
            reason_code=_canonical_reason_code(error.reason_code),
            signal=signal,
            snapshot=selected_snapshot,
            candidate_ledger_sha256=candidate_ledger_sha256,
        )
    return BcsEvaluationV1(
        signal_snapshot_sha256=signal.snapshot_sha256,
        option_snapshot_sha256=selected_snapshot.snapshot_sha256,
        candidate_ledger_sha256=candidate_ledger_sha256,
        status="RESEARCH_CANDIDATE",
        reason_code=BcsReasonV1.RESEARCH_ONLY_NOT_ACTIONABLE,
        actionable=False,
        broker_order_count=0,
        selection=selection,
        entry_cost=entry_cost,
        blocking_notifications=(),
    )


def _create_public_bcs_evaluator(unsealed_evaluate):
    """Seal evaluation identity and content inside the public evaluator closure."""

    EvaluationType = BcsEvaluationV1
    hasher = canonical_snapshot_sha256
    reference_factory = weakref.ref
    verified_by_identity: dict[
        int,
        tuple[weakref.ReferenceType[BcsEvaluationV1], str],
    ] = {}

    def evaluate(
        *,
        signal: SignalSnapshotV1,
        candidate_ledger: OptionSnapshotCandidateLedgerV1 | None,
        long_call_selection: Lc0SelectionBindingV1,
        deltas: tuple[CrrDeltaEvidenceV1, ...],
        fees: BcsFeeScheduleV1 | None,
        quantity: int,
    ) -> BcsEvaluationV1:
        result = unsealed_evaluate(
            signal=signal,
            candidate_ledger=candidate_ledger,
            long_call_selection=long_call_selection,
            deltas=deltas,
            fees=fees,
            quantity=quantity,
        )
        identity = id(result)

        def discard(reference: weakref.ReferenceType[BcsEvaluationV1]) -> None:
            record = verified_by_identity.get(identity)
            if record is not None and record[0] is reference:
                verified_by_identity.pop(identity, None)

        reference = reference_factory(result, discard)
        verified_by_identity[identity] = (reference, hasher(result))
        return result

    def is_verified(value: object) -> bool:
        if type(value) is not EvaluationType:
            return False
        record = verified_by_identity.get(id(value))
        return (
            record is not None
            and record[0]() is value
            and record[1] == hasher(value)
        )

    return evaluate, is_verified


evaluate_bcs_research, is_verified_bcs_evaluation = _create_public_bcs_evaluator(
    _evaluate_bcs_research_unsealed
)
del _create_public_bcs_evaluator


def _create_public_decision_artifact_builder(
    unsealed_builder,
    evaluation_engine,
    evaluation_verifier,
    candidate_ledger_builder,
):
    """Capture the only accepted evaluator and verifier for artifact creation."""

    def build(
        *,
        signal: SignalSnapshotV1,
        candidate_ledger: tuple[OptionQuoteSnapshotV1, ...],
        long_call_selection: Lc0SelectionBindingV1,
        deltas: tuple[CrrDeltaEvidenceV1, ...],
        research_contract_sha256: str,
        rule_package_version: str,
        fees: BcsFeeScheduleV1,
        quantity: int,
    ) -> DecisionArtifactV1:
        if type(candidate_ledger) is not tuple:
            raise NormalizationError("OPTION_CANDIDATE_LEDGER_INVALID")
        normalized_candidate_ledger = (
            candidate_ledger_builder(
                candidate_ledger,
                signal_snapshot=signal,
            )
            if candidate_ledger
            else None
        )
        return unsealed_builder(
            signal=signal,
            candidate_ledger=normalized_candidate_ledger,
            long_call_selection=long_call_selection,
            deltas=deltas,
            research_contract_sha256=research_contract_sha256,
            rule_package_version=rule_package_version,
            fees=fees,
            quantity=quantity,
            evaluation_engine=evaluation_engine,
            evaluation_verifier=evaluation_verifier,
        )

    return build


build_decision_artifact = _create_public_decision_artifact_builder(
    _build_decision_artifact_unsealed,
    evaluate_bcs_research,
    is_verified_bcs_evaluation,
    build_option_snapshot_candidate_ledger,
)
del _create_public_decision_artifact_builder


__all__ = [
    "REAL_BCS_DECISION_EXECUTION_STATUS",
    "BcsReasonV1",
    "Lc0SelectionBindingV1",
    "CrrDeltaEvidenceV1",
    "BcsSelectionV1",
    "BcsFeeScheduleV1",
    "BcsEntryCostV1",
    "BcsExitCostV1",
    "BcsTradeEpisodeV1",
    "BcsManagementSnapshotV1",
    "BrokerReconciliationSnapshotBV1",
    "CarrierTerminalResultV1",
    "DecisionArtifactV1",
    "BlockingNotificationV1",
    "BcsEvaluationV1",
    "BcsLifecycleTransitionV1",
    "bind_lc0_selection_for_research",
    "select_bcs_short_call",
    "price_bcs_entry",
    "open_bcs_trade_episode_for_research",
    "bind_bcs_management_snapshot_for_research",
    "price_bcs_exit",
    "next_bcs_lifecycle_state",
    "evaluate_bcs_research",
    "is_verified_bcs_evaluation",
    "build_decision_artifact",
]
