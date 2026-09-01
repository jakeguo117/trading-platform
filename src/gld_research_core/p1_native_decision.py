"""Same-process native Call-to-Decision semantic shadow orchestration.

This module binds one already-created native Call compute/verifier pair to the
fixed exact-64 batch runner, then derives the backend-neutral BCS and Decision
projection from verified semantic terminals.  It never constructs reference
Delta evidence or an authoritative DecisionArtifact.

There is no build, kernel loading, fallback, provider, broker client, order, or
timeout path here.  A successful receipt is still non-actionable shadow
evidence and explicitly does not qualify P1 performance.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
import threading
from types import FunctionType
from typing import Callable, Mapping
import weakref

from gld_normalizer.errors import NormalizationError
from gld_normalizer.readers import MAX_DEFINED_I64, MAX_DEFINED_U32

from . import native_crr_delta as _native_call_module
from .bcs import BcsFeeScheduleV1, Lc0SelectionBindingV1
from .crr_batch import (
    BATCH_WORKER_COUNT,
    EXACT_BATCH_SIZE,
    KERNEL_THREADS,
    P1_QUALIFICATION_STATUS,
    RESULT_CACHE_HITS,
    CrrExact64BatchReceiptV1,
    CrrSemanticTerminalV1,
    _create_exact_64_batch_engine,
    semantic_output_sha256,
)
from .crr_delta import (
    MODEL_SHA256,
    CrrCallInputsV1,
    CrrPitInputsV1,
)
from .crr_input_binding import bind_crr_call_inputs_bulk_from_snapshot
from .facts import (
    QUOTE_QUALITY_POLICY_SHA256,
    QUOTE_QUALITY_POLICY_VERSION,
    OptionContractV1,
    OptionQuoteSnapshotV1,
    OptionQuoteV1,
    SignalSnapshotV1,
    TopOfBookV1,
    build_option_snapshot_candidate_ledger,
    canonical_snapshot_sha256,
    select_first_complete_option_snapshot,
)
from .native_crr_delta import (
    NATIVE_ORCHESTRATOR_SOURCE_ARTIFACT_SHA256,
    NativeCrrDeltaResultV1,
)


NATIVE_DECISION_SHADOW_STATUS = (
    "NATIVE_DECISION_SHADOW_ONLY_NOT_P1_QUALIFIED"
)
RESEARCH_CONTRACT_SHA256 = (
    "3940c27b4aec3585b9aa904a96f2118178589fafdcee46412441431ff9fcb8fc"
)
P1_CONTRACT_SHA256 = (
    "8191b769ecb222557eb0770f7e64eaf8bbcb35bf4a0d836ab91e52cc17c445d5"
)
BACKEND_EVIDENCE_COMPONENTS_SCHEMA = (
    "GLD_CRR_P1_NATIVE_DECISION_BACKEND_COMPONENTS_V1"
)
RUNTIME_SEMANTIC_SCHEMA = "GLD_CRR_P1_NATIVE_DECISION_RUNTIME_SEMANTIC_V1"
SHADOW_ARTIFACT_SCHEMA = "GLD_CRR_P1_NATIVE_DECISION_SHADOW_ARTIFACT_V1"
NATIVE_EXECUTION_SCHEMA = "GLD_CRR_P1_NATIVE_DECISION_EXECUTION_V1"
REQUEST_BINDING_SCHEMA = "GLD_CRR_P1_NATIVE_DECISION_REQUEST_BINDING_V1"

_SHORT_DELTA_MIN_PPM = 200_000
_SHORT_DELTA_MAX_PPM = 300_000
_SHORT_DELTA_TARGET_PPM = 250_000
_MAX_TEXT_CHARS = 256
_MAX_CANONICAL_DEPTH = 12
_MAX_CANONICAL_NODES = 16_384
_MAX_CANONICAL_BYTES = 2 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z", re.ASCII)


__all__ = (
    "BACKEND_EVIDENCE_COMPONENTS_SCHEMA",
    "NATIVE_DECISION_SHADOW_STATUS",
    "P1_CONTRACT_SHA256",
    "RESEARCH_CONTRACT_SHA256",
    "NativeCarrierTerminalV1",
    "NativeDecisionBackendEvidenceComponentsV1",
    "NativeDecisionEntryCostV1",
    "NativeDecisionEntryFeesV1",
    "NativeDecisionEntryQuoteV1",
    "NativeDecisionProjectionV1",
    "NativeDecisionRequestBindingV1",
    "NativeDecisionSelectionV1",
    "NativeDecisionSelectorPolicyV1",
    "NativeDecisionShadowFailureV1",
    "NativeDecisionShadowReceiptV1",
    "NativeDecisionStateV1",
    "P1NativeDecisionError",
    "create_p1_native_decision_shadow_engine",
)


class P1NativeDecisionError(ValueError):
    """One stable fail-closed native Decision shadow error."""

    def __init__(self, reason_code: str) -> None:
        if type(reason_code) is not str or _REASON_RE.fullmatch(reason_code) is None:
            raise ValueError("invalid native Decision reason code")
        self.reason_code = reason_code
        super().__init__(reason_code)


def _require_sha256(value: object, reason_code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise P1NativeDecisionError(reason_code)
    return value


def _require_text(value: object, reason_code: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > _MAX_TEXT_CHARS
        or not value.isascii()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise P1NativeDecisionError(reason_code)
    return value


def _require_exact_int(
    value: object,
    *,
    minimum: int,
    maximum: int,
    reason_code: str,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise P1NativeDecisionError(reason_code)
    return value


def _require_reason(value: object, *, allow_pass: bool = False) -> str:
    if type(value) is not str or _REASON_RE.fullmatch(value) is None:
        raise P1NativeDecisionError("NATIVE_DECISION_REASON_INVALID")
    if (value == "PASS") is not allow_pass:
        raise P1NativeDecisionError("NATIVE_DECISION_REASON_INVALID")
    return value


def _validate_canonical_value(
    value: object,
    *,
    depth: int,
    nodes: list[int],
) -> None:
    if depth > _MAX_CANONICAL_DEPTH:
        raise P1NativeDecisionError("NATIVE_DECISION_CANONICAL_JSON_INVALID")
    nodes[0] += 1
    if nodes[0] > _MAX_CANONICAL_NODES:
        raise P1NativeDecisionError("NATIVE_DECISION_CANONICAL_JSON_INVALID")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not -MAX_DEFINED_I64 <= value <= MAX_DEFINED_I64:
            raise P1NativeDecisionError("NATIVE_DECISION_CANONICAL_JSON_INVALID")
        return
    if type(value) is str:
        if len(value) > _MAX_TEXT_CHARS or not value.isascii():
            raise P1NativeDecisionError("NATIVE_DECISION_CANONICAL_JSON_INVALID")
        return
    if type(value) in {list, tuple}:
        if len(value) > EXACT_BATCH_SIZE:
            raise P1NativeDecisionError("NATIVE_DECISION_CANONICAL_JSON_INVALID")
        for item in value:
            _validate_canonical_value(item, depth=depth + 1, nodes=nodes)
        return
    if type(value) is dict:
        if len(value) > 64 or any(type(key) is not str for key in value):
            raise P1NativeDecisionError("NATIVE_DECISION_CANONICAL_JSON_INVALID")
        for key, item in value.items():
            _validate_canonical_value(key, depth=depth + 1, nodes=nodes)
            _validate_canonical_value(item, depth=depth + 1, nodes=nodes)
        return
    raise P1NativeDecisionError("NATIVE_DECISION_CANONICAL_JSON_INVALID")


def _canonical_json_bytes(value: object) -> bytes:
    _validate_canonical_value(value, depth=0, nodes=[0])
    try:
        encoded = (
            json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise P1NativeDecisionError(
            "NATIVE_DECISION_CANONICAL_JSON_INVALID"
        ) from error
    if len(encoded) > _MAX_CANONICAL_BYTES:
        raise P1NativeDecisionError("NATIVE_DECISION_CANONICAL_JSON_INVALID")
    return encoded


def _document_sha256(value: object) -> str:
    return sha256(_canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class NativeDecisionSelectorPolicyV1:
    coarse_fine_winner_must_match: bool = True
    short_delta_max_ppm: int = _SHORT_DELTA_MAX_PPM
    short_delta_min_ppm: int = _SHORT_DELTA_MIN_PPM
    short_delta_target_ppm: int = _SHORT_DELTA_TARGET_PPM
    winner_key_order: tuple[str, str, str] = (
        "absolute_delta_distance_to_target",
        "higher_strike",
        "contract_id_lexical",
    )

    def __post_init__(self) -> None:
        if (
            self.coarse_fine_winner_must_match is not True
            or type(self.short_delta_max_ppm) is not int
            or self.short_delta_max_ppm != _SHORT_DELTA_MAX_PPM
            or type(self.short_delta_min_ppm) is not int
            or self.short_delta_min_ppm != _SHORT_DELTA_MIN_PPM
            or type(self.short_delta_target_ppm) is not int
            or self.short_delta_target_ppm != _SHORT_DELTA_TARGET_PPM
            or type(self.winner_key_order) is not tuple
            or self.winner_key_order
            != (
                "absolute_delta_distance_to_target",
                "higher_strike",
                "contract_id_lexical",
            )
        ):
            raise P1NativeDecisionError("NATIVE_DECISION_SELECTOR_POLICY_INVALID")

    def as_dict(self) -> dict[str, object]:
        return {
            "coarse_fine_winner_must_match": True,
            "short_delta_max_ppm": self.short_delta_max_ppm,
            "short_delta_min_ppm": self.short_delta_min_ppm,
            "short_delta_target_ppm": self.short_delta_target_ppm,
            "winner_key_order": list(self.winner_key_order),
        }


@dataclass(frozen=True, slots=True)
class NativeDecisionSelectionV1:
    expiry_utc_ns: int
    long_call_id: str
    long_coarse_delta_ppm: int
    long_delta_ppm: int
    long_strike_nano_usd: int
    multiplier: int
    net_delta_ppm: int
    short_call_id: str
    short_coarse_delta_ppm: int
    short_delta_ppm: int
    short_strike_nano_usd: int

    def __post_init__(self) -> None:
        _require_exact_int(
            self.expiry_utc_ns,
            minimum=1,
            maximum=MAX_DEFINED_I64,
            reason_code="NATIVE_DECISION_SELECTION_INVALID",
        )
        long_id = _require_text(
            self.long_call_id, "NATIVE_DECISION_SELECTION_INVALID"
        )
        short_id = _require_text(
            self.short_call_id, "NATIVE_DECISION_SELECTION_INVALID"
        )
        if long_id == short_id:
            raise P1NativeDecisionError("NATIVE_DECISION_SELECTION_INVALID")
        for value in (
            self.long_coarse_delta_ppm,
            self.long_delta_ppm,
            self.short_coarse_delta_ppm,
            self.short_delta_ppm,
            self.net_delta_ppm,
        ):
            _require_exact_int(
                value,
                minimum=0,
                maximum=1_000_000,
                reason_code="NATIVE_DECISION_SELECTION_INVALID",
            )
        long_strike = _require_exact_int(
            self.long_strike_nano_usd,
            minimum=1,
            maximum=MAX_DEFINED_I64,
            reason_code="NATIVE_DECISION_SELECTION_INVALID",
        )
        short_strike = _require_exact_int(
            self.short_strike_nano_usd,
            minimum=1,
            maximum=MAX_DEFINED_I64,
            reason_code="NATIVE_DECISION_SELECTION_INVALID",
        )
        if (
            short_strike <= long_strike
            or type(self.multiplier) is not int
            or self.multiplier != 100
            or self.short_delta_ppm >= self.long_delta_ppm
            or self.net_delta_ppm
            != self.long_delta_ppm - self.short_delta_ppm
        ):
            raise P1NativeDecisionError("NATIVE_DECISION_SELECTION_INVALID")

    def as_dict(self) -> dict[str, object]:
        return {
            field.name: getattr(self, field.name) for field in fields(type(self))
        }


@dataclass(frozen=True, slots=True)
class NativeDecisionEntryQuoteV1:
    contract_id: str
    displayed_size: int
    price_nano_usd: int
    side: str
    tick_nano_usd: int

    def __post_init__(self) -> None:
        _require_text(self.contract_id, "NATIVE_DECISION_ENTRY_QUOTE_INVALID")
        _require_exact_int(
            self.displayed_size,
            minimum=0,
            maximum=MAX_DEFINED_U32,
            reason_code="NATIVE_DECISION_ENTRY_QUOTE_INVALID",
        )
        _require_exact_int(
            self.price_nano_usd,
            minimum=0,
            maximum=MAX_DEFINED_I64,
            reason_code="NATIVE_DECISION_ENTRY_QUOTE_INVALID",
        )
        _require_exact_int(
            self.tick_nano_usd,
            minimum=1,
            maximum=MAX_DEFINED_I64,
            reason_code="NATIVE_DECISION_ENTRY_QUOTE_INVALID",
        )
        if type(self.side) is not str or self.side not in {"ASK", "BID"}:
            raise P1NativeDecisionError("NATIVE_DECISION_ENTRY_QUOTE_INVALID")

    def as_dict(self) -> dict[str, object]:
        return {
            field.name: getattr(self, field.name) for field in fields(type(self))
        }


@dataclass(frozen=True, slots=True)
class NativeDecisionEntryFeesV1:
    long_entry_fee_nano_usd_per_contract: int
    short_entry_fee_nano_usd_per_contract: int
    total_entry_fees_nano_usd: int

    def __post_init__(self) -> None:
        for value in (
            self.long_entry_fee_nano_usd_per_contract,
            self.short_entry_fee_nano_usd_per_contract,
            self.total_entry_fees_nano_usd,
        ):
            _require_exact_int(
                value,
                minimum=0,
                maximum=MAX_DEFINED_I64,
                reason_code="NATIVE_DECISION_ENTRY_FEES_INVALID",
            )
        if self.total_entry_fees_nano_usd != (
            self.long_entry_fee_nano_usd_per_contract
            + self.short_entry_fee_nano_usd_per_contract
        ):
            raise P1NativeDecisionError("NATIVE_DECISION_ENTRY_FEES_INVALID")

    def as_dict(self) -> dict[str, object]:
        return {
            field.name: getattr(self, field.name) for field in fields(type(self))
        }


@dataclass(frozen=True, slots=True)
class NativeDecisionEntryCostV1:
    base_net_debit_nano_usd: int
    gross_expiry_width_value_nano_usd: int
    max_loss_nano_usd: int
    quantity: int
    stressed_net_debit_nano_usd: int
    theoretical_expiry_max_profit_nano_usd: int
    theoretical_expiry_only: bool

    def __post_init__(self) -> None:
        for value in (
            self.base_net_debit_nano_usd,
            self.gross_expiry_width_value_nano_usd,
            self.max_loss_nano_usd,
            self.stressed_net_debit_nano_usd,
            self.theoretical_expiry_max_profit_nano_usd,
        ):
            _require_exact_int(
                value,
                minimum=1,
                maximum=MAX_DEFINED_I64,
                reason_code="BCS_INVALID_NET_DEBIT",
            )
        if (
            type(self.quantity) is not int
            or self.quantity != 1
            or self.theoretical_expiry_only is not True
            or not self.base_net_debit_nano_usd
            <= self.stressed_net_debit_nano_usd
            < self.gross_expiry_width_value_nano_usd
            or self.max_loss_nano_usd != self.base_net_debit_nano_usd
            or self.theoretical_expiry_max_profit_nano_usd
            != self.gross_expiry_width_value_nano_usd
            - self.base_net_debit_nano_usd
        ):
            raise P1NativeDecisionError("BCS_INVALID_NET_DEBIT")

    def as_dict(self) -> dict[str, object]:
        return {
            field.name: getattr(self, field.name) for field in fields(type(self))
        }


@dataclass(frozen=True, slots=True)
class NativeCarrierTerminalV1:
    carrier_id: str
    reason_code: str
    terminal_status: str

    def __post_init__(self) -> None:
        expected = {
            "LC0": ("LC0_AUTHORITY_NOT_IMPLEMENTED", "NO_DECISION"),
            "BCS0": ("RESEARCH_ONLY_NOT_ACTIONABLE", "PASS"),
        }
        if (
            type(self.carrier_id) is not str
            or self.carrier_id not in expected
            or type(self.reason_code) is not str
            or type(self.terminal_status) is not str
            or (self.reason_code, self.terminal_status)
            != expected[self.carrier_id]
        ):
            raise P1NativeDecisionError("NATIVE_DECISION_CARRIER_INVALID")

    def as_dict(self) -> dict[str, object]:
        return {
            "carrier_id": self.carrier_id,
            "reason_code": self.reason_code,
            "terminal_status": self.terminal_status,
        }


@dataclass(frozen=True, slots=True)
class NativeDecisionStateV1:
    actionable: bool
    broker_order_count: int
    owner_selection_required: bool
    reason_code: str
    status: str

    def __post_init__(self) -> None:
        if (
            self.actionable is not False
            or type(self.broker_order_count) is not int
            or self.broker_order_count != 0
            or self.owner_selection_required is not False
            or self.reason_code != "CARRIER_EVALUATION_INCOMPLETE"
            or self.status != "NO_DECISION"
        ):
            raise P1NativeDecisionError("NATIVE_DECISION_STATE_INVALID")

    def as_dict(self) -> dict[str, object]:
        return {
            field.name: getattr(self, field.name) for field in fields(type(self))
        }


@dataclass(frozen=True, slots=True)
class NativeDecisionProjectionV1:
    carrier_terminals: tuple[NativeCarrierTerminalV1, NativeCarrierTerminalV1]
    decision: NativeDecisionStateV1
    entry_cost: NativeDecisionEntryCostV1
    entry_fees: NativeDecisionEntryFeesV1
    long_entry_quote: NativeDecisionEntryQuoteV1
    short_entry_quote: NativeDecisionEntryQuoteV1
    selection: NativeDecisionSelectionV1
    selector_policy: NativeDecisionSelectorPolicyV1

    def __post_init__(self) -> None:
        if (
            type(self.carrier_terminals) is not tuple
            or len(self.carrier_terminals) != 2
            or tuple(item.carrier_id for item in self.carrier_terminals)
            != ("LC0", "BCS0")
            or type(self.decision) is not NativeDecisionStateV1
            or type(self.entry_cost) is not NativeDecisionEntryCostV1
            or type(self.entry_fees) is not NativeDecisionEntryFeesV1
            or type(self.long_entry_quote) is not NativeDecisionEntryQuoteV1
            or type(self.short_entry_quote) is not NativeDecisionEntryQuoteV1
            or type(self.selection) is not NativeDecisionSelectionV1
            or type(self.selector_policy) is not NativeDecisionSelectorPolicyV1
            or self.long_entry_quote.contract_id != self.selection.long_call_id
            or self.long_entry_quote.side != "ASK"
            or self.short_entry_quote.contract_id != self.selection.short_call_id
            or self.short_entry_quote.side != "BID"
            or self.entry_cost.quantity != 1
        ):
            raise P1NativeDecisionError("NATIVE_DECISION_PROJECTION_INVALID")

    def as_dict(self) -> dict[str, object]:
        return {
            "carrier_terminals": [
                item.as_dict() for item in self.carrier_terminals
            ],
            "decision": self.decision.as_dict(),
            "entry_cost": self.entry_cost.as_dict(),
            "entry_fees": self.entry_fees.as_dict(),
            "entry_quotes": {
                "long": self.long_entry_quote.as_dict(),
                "short": self.short_entry_quote.as_dict(),
            },
            "selection": self.selection.as_dict(),
            "selector_policy": self.selector_policy.as_dict(),
        }

    @property
    def projection_sha256(self) -> str:
        return _document_sha256(self.as_dict())


@dataclass(frozen=True, slots=True)
class NativeDecisionRequestBindingV1:
    signal_snapshot_sha256: str
    candidate_ledger_sha256: str
    candidate_count: int
    selected_candidate_ordinal: int
    selected_snapshot_sha256: str
    pit_inputs_sha256: str
    lc0_binding_sha256: str
    fee_schedule_sha256: str
    research_contract_sha256: str
    p1_contract_sha256: str
    rule_package_version: str
    rule_sha256: str
    quantity: int
    call_input_vector_sha256: str
    delta_model_sha256: str
    quote_quality_policy_version: str
    quote_quality_policy_sha256: str

    def __post_init__(self) -> None:
        for value in (
            self.signal_snapshot_sha256,
            self.candidate_ledger_sha256,
            self.selected_snapshot_sha256,
            self.pit_inputs_sha256,
            self.lc0_binding_sha256,
            self.fee_schedule_sha256,
            self.research_contract_sha256,
            self.p1_contract_sha256,
            self.rule_sha256,
            self.call_input_vector_sha256,
            self.delta_model_sha256,
            self.quote_quality_policy_sha256,
        ):
            _require_sha256(value, "NATIVE_DECISION_REQUEST_BINDING_INVALID")
        if (
            self.research_contract_sha256 != RESEARCH_CONTRACT_SHA256
            or self.p1_contract_sha256 != P1_CONTRACT_SHA256
            or self.delta_model_sha256 != MODEL_SHA256
            or self.quote_quality_policy_version
            != QUOTE_QUALITY_POLICY_VERSION
            or self.quote_quality_policy_sha256
            != QUOTE_QUALITY_POLICY_SHA256
            or type(self.candidate_count) is not int
            or not 1 <= self.candidate_count <= 64
            or type(self.selected_candidate_ordinal) is not int
            or not 1 <= self.selected_candidate_ordinal <= self.candidate_count
            or type(self.quantity) is not int
            or self.quantity != 1
        ):
            raise P1NativeDecisionError("NATIVE_DECISION_REQUEST_BINDING_INVALID")
        _require_text(
            self.rule_package_version,
            "NATIVE_DECISION_REQUEST_BINDING_INVALID",
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": REQUEST_BINDING_SCHEMA,
            **{
                field.name: getattr(self, field.name)
                for field in fields(type(self))
            },
        }

    @property
    def request_sha256(self) -> str:
        return _document_sha256(self.as_dict())


@dataclass(frozen=True, slots=True)
class NativeDecisionBackendEvidenceComponentsV1:
    backend_evidence_sha256: str
    input_sha256: str
    execution_sha256: str
    artifact_sha256: str

    def __post_init__(self) -> None:
        for value in (
            self.backend_evidence_sha256,
            self.input_sha256,
            self.execution_sha256,
            self.artifact_sha256,
        ):
            _require_sha256(value, "NATIVE_DECISION_BACKEND_COMPONENTS_INVALID")

    def as_dict(self) -> dict[str, object]:
        return {
            field.name: getattr(self, field.name) for field in fields(type(self))
        }


def _runtime_semantic_sha256(
    *,
    request_binding: NativeDecisionRequestBindingV1,
    call_semantic_output_sha256: str,
    projection_sha256: str,
) -> str:
    return _document_sha256(
        {
            "call_semantic_output_sha256": _require_sha256(
                call_semantic_output_sha256,
                "NATIVE_DECISION_SEMANTIC_HASH_INVALID",
            ),
            "projection_sha256": _require_sha256(
                projection_sha256,
                "NATIVE_DECISION_PROJECTION_HASH_INVALID",
            ),
            "request_binding": request_binding.as_dict(),
            "schema_version": RUNTIME_SEMANTIC_SCHEMA,
        }
    )


def _shadow_artifact_sha256(
    *,
    request_binding: NativeDecisionRequestBindingV1,
    call_terminals: tuple[CrrSemanticTerminalV1, ...],
    call_semantic_output_sha256: str,
    decision_projection: NativeDecisionProjectionV1,
    projection_sha256: str,
    runtime_semantic_sha256: str,
) -> str:
    return _document_sha256(
        {
            "actionable": False,
            "broker_order_count": 0,
            "call_semantic_output_sha256": call_semantic_output_sha256,
            "call_terminals": [item.as_dict() for item in call_terminals],
            "decision_projection": decision_projection.as_dict(),
            "projection_sha256": projection_sha256,
            "request_binding": request_binding.as_dict(),
            "runtime_semantic_sha256": runtime_semantic_sha256,
            "schema_version": SHADOW_ARTIFACT_SCHEMA,
        }
    )


def _native_execution_sha256(
    *,
    backend_evidence_sha256: str,
    batch_execution_provenance_sha256: str,
    request_sha256: str,
    runtime_semantic_sha256: str,
    shadow_artifact_sha256: str,
) -> str:
    return _document_sha256(
        {
            "backend_evidence_sha256": _require_sha256(
                backend_evidence_sha256,
                "NATIVE_DECISION_BACKEND_EVIDENCE_INVALID",
            ),
            "batch_execution_provenance_sha256": _require_sha256(
                batch_execution_provenance_sha256,
                "NATIVE_DECISION_EXECUTION_HASH_INVALID",
            ),
            "batch_size": EXACT_BATCH_SIZE,
            "cache_hits": RESULT_CACHE_HITS,
            "kernel_threads": KERNEL_THREADS,
            "request_sha256": _require_sha256(
                request_sha256,
                "NATIVE_DECISION_REQUEST_HASH_INVALID",
            ),
            "runtime_semantic_sha256": _require_sha256(
                runtime_semantic_sha256,
                "NATIVE_DECISION_SEMANTIC_HASH_INVALID",
            ),
            "schema_version": NATIVE_EXECUTION_SCHEMA,
            "shadow_artifact_sha256": _require_sha256(
                shadow_artifact_sha256,
                "NATIVE_DECISION_ARTIFACT_HASH_INVALID",
            ),
            "worker_count": BATCH_WORKER_COUNT,
        }
    )


@dataclass(frozen=True, slots=True, weakref_slot=True)
class NativeDecisionShadowReceiptV1:
    execution_status: str
    p1_status: str
    requested: int
    bound: int
    started: int
    terminal: int
    worker_count: int
    kernel_threads: int
    cache_hits: int
    request_binding: NativeDecisionRequestBindingV1
    request_sha256: str
    call_terminals: tuple[CrrSemanticTerminalV1, ...]
    call_semantic_output_sha256: str
    decision_projection: NativeDecisionProjectionV1
    projection_sha256: str
    runtime_semantic_sha256: str
    shadow_artifact_sha256: str
    batch_execution_provenance_sha256: str
    backend_evidence_components: NativeDecisionBackendEvidenceComponentsV1
    actionable: bool
    broker_order_count: int

    def __post_init__(self) -> None:
        if (
            type(self.execution_status) is not str
            or self.execution_status != NATIVE_DECISION_SHADOW_STATUS
            or type(self.p1_status) is not str
            or self.p1_status != P1_QUALIFICATION_STATUS
        ):
            raise P1NativeDecisionError("NATIVE_DECISION_RECEIPT_STATUS_INVALID")
        for value, expected in (
            (self.requested, EXACT_BATCH_SIZE),
            (self.bound, EXACT_BATCH_SIZE),
            (self.started, EXACT_BATCH_SIZE),
            (self.terminal, EXACT_BATCH_SIZE),
            (self.worker_count, BATCH_WORKER_COUNT),
            (self.kernel_threads, KERNEL_THREADS),
            (self.cache_hits, RESULT_CACHE_HITS),
        ):
            if type(value) is not int or value != expected:
                raise P1NativeDecisionError("NATIVE_DECISION_RECEIPT_COUNT_INVALID")
        if (
            type(self.request_binding) is not NativeDecisionRequestBindingV1
            or self.request_sha256 != self.request_binding.request_sha256
            or type(self.call_terminals) is not tuple
            or len(self.call_terminals) != EXACT_BATCH_SIZE
            or any(
                type(item) is not CrrSemanticTerminalV1
                or item.terminal_status != "PASS"
                for item in self.call_terminals
            )
            or self.call_semantic_output_sha256
            != semantic_output_sha256(self.call_terminals)
            or type(self.decision_projection) is not NativeDecisionProjectionV1
            or self.projection_sha256
            != self.decision_projection.projection_sha256
            or self.actionable is not False
            or type(self.broker_order_count) is not int
            or self.broker_order_count != 0
        ):
            raise P1NativeDecisionError("NATIVE_DECISION_RECEIPT_INVALID")
        expected_runtime_semantic = _runtime_semantic_sha256(
            request_binding=self.request_binding,
            call_semantic_output_sha256=self.call_semantic_output_sha256,
            projection_sha256=self.projection_sha256,
        )
        expected_artifact = _shadow_artifact_sha256(
            request_binding=self.request_binding,
            call_terminals=self.call_terminals,
            call_semantic_output_sha256=self.call_semantic_output_sha256,
            decision_projection=self.decision_projection,
            projection_sha256=self.projection_sha256,
            runtime_semantic_sha256=expected_runtime_semantic,
        )
        if (
            self.runtime_semantic_sha256 != expected_runtime_semantic
            or self.shadow_artifact_sha256 != expected_artifact
            or type(self.backend_evidence_components)
            is not NativeDecisionBackendEvidenceComponentsV1
            or self.backend_evidence_components.input_sha256
            != self.request_sha256
            or self.backend_evidence_components.artifact_sha256
            != self.shadow_artifact_sha256
        ):
            raise P1NativeDecisionError("NATIVE_DECISION_RECEIPT_INVALID")
        expected_execution = _native_execution_sha256(
            backend_evidence_sha256=(
                self.backend_evidence_components.backend_evidence_sha256
            ),
            batch_execution_provenance_sha256=(
                self.batch_execution_provenance_sha256
            ),
            request_sha256=self.request_sha256,
            runtime_semantic_sha256=self.runtime_semantic_sha256,
            shadow_artifact_sha256=self.shadow_artifact_sha256,
        )
        if self.backend_evidence_components.execution_sha256 != expected_execution:
            raise P1NativeDecisionError("NATIVE_DECISION_RECEIPT_INVALID")

    def as_dict(self) -> dict[str, object]:
        return {
            "actionable": self.actionable,
            "backend_evidence_components": (
                self.backend_evidence_components.as_dict()
            ),
            "batch_execution_provenance_sha256": (
                self.batch_execution_provenance_sha256
            ),
            "bound": self.bound,
            "broker_order_count": self.broker_order_count,
            "cache_hits": self.cache_hits,
            "call_semantic_output_sha256": self.call_semantic_output_sha256,
            "call_terminals": [item.as_dict() for item in self.call_terminals],
            "decision_projection": self.decision_projection.as_dict(),
            "execution_status": self.execution_status,
            "kernel_threads": self.kernel_threads,
            "p1_status": self.p1_status,
            "projection_sha256": self.projection_sha256,
            "request_binding": self.request_binding.as_dict(),
            "request_sha256": self.request_sha256,
            "requested": self.requested,
            "runtime_semantic_sha256": self.runtime_semantic_sha256,
            "shadow_artifact_sha256": self.shadow_artifact_sha256,
            "started": self.started,
            "terminal": self.terminal,
            "worker_count": self.worker_count,
        }


@dataclass(frozen=True, slots=True, weakref_slot=True)
class NativeDecisionShadowFailureV1:
    execution_status: str
    p1_status: str
    reason_code: str
    requested: int
    bound: int
    started: int
    terminal: int
    worker_count: int
    kernel_threads: int
    cache_hits: int
    first_failure_ordinal: int | None
    request_binding: NativeDecisionRequestBindingV1
    request_sha256: str
    call_terminals: tuple[CrrSemanticTerminalV1, ...]
    call_semantic_output_sha256: str
    batch_execution_provenance_sha256: str
    backend_evidence_sha256: str
    actionable: bool
    broker_order_count: int

    def __post_init__(self) -> None:
        if (
            self.execution_status != "NATIVE_DECISION_SHADOW_FAILED_NO_DECISION"
            or self.p1_status != P1_QUALIFICATION_STATUS
        ):
            raise P1NativeDecisionError("NATIVE_DECISION_FAILURE_STATUS_INVALID")
        _require_reason(self.reason_code)
        for value, expected in (
            (self.requested, EXACT_BATCH_SIZE),
            (self.bound, EXACT_BATCH_SIZE),
            (self.started, EXACT_BATCH_SIZE),
            (self.terminal, EXACT_BATCH_SIZE),
            (self.worker_count, BATCH_WORKER_COUNT),
            (self.kernel_threads, KERNEL_THREADS),
            (self.cache_hits, RESULT_CACHE_HITS),
        ):
            if type(value) is not int or value != expected:
                raise P1NativeDecisionError("NATIVE_DECISION_FAILURE_COUNT_INVALID")
        if (
            type(self.request_binding) is not NativeDecisionRequestBindingV1
            or self.request_sha256 != self.request_binding.request_sha256
            or type(self.call_terminals) is not tuple
            or len(self.call_terminals) != EXACT_BATCH_SIZE
            or any(type(item) is not CrrSemanticTerminalV1 for item in self.call_terminals)
            or self.call_semantic_output_sha256
            != semantic_output_sha256(self.call_terminals)
            or self.actionable is not False
            or type(self.broker_order_count) is not int
            or self.broker_order_count != 0
        ):
            raise P1NativeDecisionError("NATIVE_DECISION_FAILURE_INVALID")
        expected_first_failure = next(
            (
                item.ordinal
                for item in self.call_terminals
                if item.terminal_status == "FAIL"
            ),
            None,
        )
        if (
            self.first_failure_ordinal is not None
            and type(self.first_failure_ordinal) is not int
        ) or self.first_failure_ordinal != expected_first_failure:
            raise P1NativeDecisionError("NATIVE_DECISION_FAILURE_ORDER_INVALID")
        _require_sha256(
            self.batch_execution_provenance_sha256,
            "NATIVE_DECISION_FAILURE_INVALID",
        )
        _require_sha256(
            self.backend_evidence_sha256,
            "NATIVE_DECISION_FAILURE_INVALID",
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "actionable": self.actionable,
            "backend_evidence_sha256": self.backend_evidence_sha256,
            "batch_execution_provenance_sha256": (
                self.batch_execution_provenance_sha256
            ),
            "bound": self.bound,
            "broker_order_count": self.broker_order_count,
            "cache_hits": self.cache_hits,
            "call_semantic_output_sha256": self.call_semantic_output_sha256,
            "call_terminals": [item.as_dict() for item in self.call_terminals],
            "execution_status": self.execution_status,
            "first_failure_ordinal": self.first_failure_ordinal,
            "kernel_threads": self.kernel_threads,
            "p1_status": self.p1_status,
            "reason_code": self.reason_code,
            "request_binding": self.request_binding.as_dict(),
            "request_sha256": self.request_sha256,
            "requested": self.requested,
            "started": self.started,
            "terminal": self.terminal,
            "worker_count": self.worker_count,
        }


@dataclass(frozen=True, slots=True)
class _PreparedRequestV1:
    request_binding: NativeDecisionRequestBindingV1
    candidate_ledger_sha256: str
    selected_snapshot: OptionQuoteSnapshotV1
    bound_inputs: tuple[CrrCallInputsV1, ...]


@dataclass(frozen=True, slots=True)
class _InvocationStateV1:
    signal: SignalSnapshotV1
    candidate_snapshots: tuple[OptionQuoteSnapshotV1, ...]
    pit_inputs: CrrPitInputsV1
    lc0_binding: Lc0SelectionBindingV1
    fees: BcsFeeScheduleV1
    research_contract_sha256: str
    p1_contract_sha256: str
    rule_package_version: str
    quantity: int


def _reconstruct_flat_dataclass(
    value: object,
    expected_type: type[object],
    reason_code: str,
) -> None:
    if type(value) is not expected_type:
        raise P1NativeDecisionError(reason_code)
    try:
        reconstructed = expected_type(
            **{
                item.name: getattr(value, item.name)
                for item in fields(expected_type)
            }
        )
    except (AttributeError, NormalizationError, TypeError, ValueError) as error:
        raise P1NativeDecisionError(reason_code) from error
    if reconstructed != value:
        raise P1NativeDecisionError(reason_code)


def _reconstruct_snapshot(snapshot: object, *, quantity: int) -> None:
    reason_code = "OPTION_SNAPSHOT_BINDING_MISMATCH"
    if type(snapshot) is not OptionQuoteSnapshotV1:
        raise P1NativeDecisionError(reason_code)
    try:
        underlying_top = snapshot.underlying_top
        option_quotes = snapshot.option_quotes
        if (
            type(underlying_top) is not TopOfBookV1
            or type(option_quotes) is not tuple
            or len(option_quotes) > EXACT_BATCH_SIZE
            or any(type(quote) is not OptionQuoteV1 for quote in option_quotes)
        ):
            raise P1NativeDecisionError(reason_code)
        nested_values = tuple(
            (quote, quote.contract, quote.top_of_book)
            for quote in option_quotes
        )
        if any(
            type(contract) is not OptionContractV1
            or type(book) is not TopOfBookV1
            for _, contract, book in nested_values
        ):
            raise P1NativeDecisionError(reason_code)
    except P1NativeDecisionError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise P1NativeDecisionError(reason_code) from error

    books = (underlying_top,) + tuple(
        book for _, _, book in nested_values
    )
    try:
        book_sizes = tuple(
            (book.bid_size, book.ask_size) for book in books
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise P1NativeDecisionError(reason_code) from error
    if any(
        type(bid_size) is not int or type(ask_size) is not int
        for bid_size, ask_size in book_sizes
    ):
        raise P1NativeDecisionError(reason_code)
    if any(
        bid_size < quantity or ask_size < quantity
        for bid_size, ask_size in book_sizes
    ):
        raise P1NativeDecisionError("BCS_SIZE_INSUFFICIENT")
    _reconstruct_flat_dataclass(
        underlying_top,
        TopOfBookV1,
        reason_code,
    )
    reconstructed_quotes: list[OptionQuoteV1] = []
    for quote, contract, top_of_book in nested_values:
        if type(quote) is not OptionQuoteV1:
            raise P1NativeDecisionError("OPTION_SNAPSHOT_BINDING_MISMATCH")
        _reconstruct_flat_dataclass(
            contract,
            OptionContractV1,
            reason_code,
        )
        _reconstruct_flat_dataclass(
            top_of_book,
            TopOfBookV1,
            reason_code,
        )
        try:
            reconstructed_quotes.append(
                OptionQuoteV1(
                    contract=contract,
                    top_of_book=top_of_book,
                )
            )
        except NormalizationError as error:
            raise P1NativeDecisionError(reason_code) from error
    try:
        reconstructed = OptionQuoteSnapshotV1(
            signal_snapshot_sha256=snapshot.signal_snapshot_sha256,
            candidate_ordinal=snapshot.candidate_ordinal,
            candidate_provenance_sha256=snapshot.candidate_provenance_sha256,
            window_start_utc_ns=snapshot.window_start_utc_ns,
            window_end_utc_ns=snapshot.window_end_utc_ns,
            capture_utc_ns=snapshot.capture_utc_ns,
            underlying_top=underlying_top,
            option_quotes=tuple(reconstructed_quotes),
            source_id=snapshot.source_id,
            source_version=snapshot.source_version,
            market_data_type=snapshot.market_data_type,
            source_receipt_sha256=snapshot.source_receipt_sha256,
        )
    except (AttributeError, NormalizationError, TypeError, ValueError) as error:
        raise P1NativeDecisionError(reason_code) from error
    if reconstructed != snapshot:
        raise P1NativeDecisionError(reason_code)


def _call_input_vector_sha256(
    inputs: tuple[CrrCallInputsV1, ...],
) -> str:
    return _document_sha256([item.input_sha256 for item in inputs])


def _prepare_request(
    *,
    signal: SignalSnapshotV1,
    candidate_snapshots: tuple[OptionQuoteSnapshotV1, ...],
    pit_inputs: CrrPitInputsV1,
    lc0_binding: Lc0SelectionBindingV1,
    fees: BcsFeeScheduleV1,
    research_contract_sha256: str,
    p1_contract_sha256: str,
    rule_package_version: str,
    quantity: int,
) -> _PreparedRequestV1:
    _reconstruct_flat_dataclass(
        signal,
        SignalSnapshotV1,
        "SIGNAL_SNAPSHOT_INVALID",
    )
    if signal.signal_state != "PASS":
        reason = (
            "SIGNAL_FAIL"
            if signal.signal_state == "FAIL"
            else "SIGNAL_NOT_EVALUABLE"
        )
        raise P1NativeDecisionError(reason)
    if (
        type(candidate_snapshots) is not tuple
        or not candidate_snapshots
        or len(candidate_snapshots) > 64
        or any(type(item) is not OptionQuoteSnapshotV1 for item in candidate_snapshots)
    ):
        raise P1NativeDecisionError("OPTION_CANDIDATE_LEDGER_INVALID")
    if type(quantity) is not int or quantity != 1:
        raise P1NativeDecisionError("BCS_INVALID_QUANTITY")
    for snapshot in candidate_snapshots:
        _reconstruct_snapshot(snapshot, quantity=quantity)
    if tuple(item.candidate_ordinal for item in candidate_snapshots) != tuple(
        range(1, len(candidate_snapshots) + 1)
    ):
        raise P1NativeDecisionError("OPTION_CANDIDATE_ORDER_INVALID")
    _reconstruct_flat_dataclass(
        pit_inputs,
        CrrPitInputsV1,
        "DELTA_INPUT_MISSING",
    )
    _reconstruct_flat_dataclass(
        lc0_binding,
        Lc0SelectionBindingV1,
        "BCS_LONG_LEG_BINDING_MISMATCH",
    )
    _reconstruct_flat_dataclass(
        fees,
        BcsFeeScheduleV1,
        "BCS_FEE_SCHEDULE_MISSING",
    )
    if (
        type(research_contract_sha256) is not str
        or research_contract_sha256 != RESEARCH_CONTRACT_SHA256
        or type(p1_contract_sha256) is not str
        or p1_contract_sha256 != P1_CONTRACT_SHA256
        or type(rule_package_version) is not str
        or rule_package_version != signal.rule_version
    ):
        raise P1NativeDecisionError("NATIVE_DECISION_CONTRACT_BINDING_INVALID")

    try:
        candidate_ledger = build_option_snapshot_candidate_ledger(
            candidate_snapshots,
            signal_snapshot=signal,
        )
        selected_snapshot = select_first_complete_option_snapshot(
            candidate_ledger,
            signal_snapshot=signal,
        )
    except NormalizationError as error:
        raise P1NativeDecisionError(error.reason_code) from error
    if fees.effective_from_utc_ns > selected_snapshot.capture_utc_ns:
        raise P1NativeDecisionError("BCS_FEE_SCHEDULE_MISSING")
    if len(selected_snapshot.option_quotes) != EXACT_BATCH_SIZE:
        raise P1NativeDecisionError("NATIVE_DECISION_CALL_COUNT_INVALID")

    identities: list[str] = []
    contract_hashes: list[str] = []
    order: list[tuple[int, int, str]] = []
    for quote in selected_snapshot.option_quotes:
        contract = quote.contract
        if (
            type(quote) is not OptionQuoteV1
            or type(contract) is not OptionContractV1
            or contract.right != "C"
        ):
            raise P1NativeDecisionError("NATIVE_DECISION_CALL_UNIVERSE_INVALID")
        identities.append(contract.occ_symbol)
        contract_hashes.append(canonical_snapshot_sha256(contract))
        order.append(
            (
                contract.expiry_utc_ns,
                contract.strike_nano_usd,
                contract.occ_symbol,
            )
        )
    if (
        len(set(identities)) != EXACT_BATCH_SIZE
        or len(set(contract_hashes)) != EXACT_BATCH_SIZE
    ):
        raise P1NativeDecisionError("NATIVE_DECISION_CALL_UNIVERSE_DUPLICATE")
    if tuple(order) != tuple(sorted(order)):
        raise P1NativeDecisionError("NATIVE_DECISION_CALL_ORDER_INVALID")

    snapshot_sha256 = selected_snapshot.snapshot_sha256
    long_quote = next(
        (
            quote
            for quote in selected_snapshot.option_quotes
            if quote.contract.occ_symbol == lc0_binding.long_call_id
        ),
        None,
    )
    if (
        lc0_binding.signal_snapshot_sha256 != signal.snapshot_sha256
        or lc0_binding.option_snapshot_sha256 != snapshot_sha256
        or long_quote is None
        or lc0_binding.long_contract_sha256
        != canonical_snapshot_sha256(long_quote.contract)
    ):
        raise P1NativeDecisionError("BCS_LONG_LEG_BINDING_MISMATCH")
    try:
        bound_inputs = bind_crr_call_inputs_bulk_from_snapshot(
            selected_snapshot,
            contracts=tuple(
                quote.contract for quote in selected_snapshot.option_quotes
            ),
            pit_inputs=pit_inputs,
        )
    except NormalizationError as error:
        raise P1NativeDecisionError(error.reason_code) from error
    input_hashes = tuple(item.input_sha256 for item in bound_inputs)
    if len(set(input_hashes)) != EXACT_BATCH_SIZE:
        raise P1NativeDecisionError("NATIVE_DECISION_CALL_INPUT_DUPLICATE")
    input_vector_sha256 = _call_input_vector_sha256(bound_inputs)
    request_binding = NativeDecisionRequestBindingV1(
        signal_snapshot_sha256=signal.snapshot_sha256,
        candidate_ledger_sha256=candidate_ledger.ledger_sha256,
        candidate_count=len(candidate_snapshots),
        selected_candidate_ordinal=selected_snapshot.candidate_ordinal,
        selected_snapshot_sha256=snapshot_sha256,
        pit_inputs_sha256=pit_inputs.pit_inputs_sha256,
        lc0_binding_sha256=lc0_binding.binding_sha256,
        fee_schedule_sha256=fees.fee_schedule_sha256,
        research_contract_sha256=research_contract_sha256,
        p1_contract_sha256=p1_contract_sha256,
        rule_package_version=rule_package_version,
        rule_sha256=signal.rule_sha256,
        quantity=quantity,
        call_input_vector_sha256=input_vector_sha256,
        delta_model_sha256=MODEL_SHA256,
        quote_quality_policy_version=QUOTE_QUALITY_POLICY_VERSION,
        quote_quality_policy_sha256=QUOTE_QUALITY_POLICY_SHA256,
    )
    return _PreparedRequestV1(
        request_binding=request_binding,
        candidate_ledger_sha256=candidate_ledger.ledger_sha256,
        selected_snapshot=selected_snapshot,
        bound_inputs=bound_inputs,
    )


def _pair_is_eligible(
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
        and short_contract.standard_unadjusted is True
        and long_contract.standard_unadjusted is True
    )


def _suite_winner(
    *,
    long_quote: OptionQuoteV1,
    quote_by_id: Mapping[str, OptionQuoteV1],
    terminal_by_id: Mapping[str, CrrSemanticTerminalV1],
    suite: str,
) -> OptionQuoteV1:
    delta_name = (
        "coarse_delta_ppm" if suite == "coarse" else "fine_delta_ppm"
    )
    long_terminal = terminal_by_id.get(long_quote.contract.occ_symbol)
    if long_terminal is None:
        raise P1NativeDecisionError("BCS_LONG_LEG_BINDING_MISMATCH")
    long_delta = getattr(long_terminal, delta_name)
    if type(long_delta) is not int:
        raise P1NativeDecisionError("NATIVE_DECISION_TERMINAL_INVALID")
    eligible: list[tuple[int, int, str, OptionQuoteV1]] = []
    for contract_id, quote in quote_by_id.items():
        if contract_id == long_quote.contract.occ_symbol:
            continue
        terminal = terminal_by_id.get(contract_id)
        if terminal is None:
            raise P1NativeDecisionError("NATIVE_DECISION_TERMINAL_INVALID")
        delta = getattr(terminal, delta_name)
        if (
            terminal.terminal_status == "PASS"
            and type(delta) is int
            and _pair_is_eligible(long_quote, quote)
            and _SHORT_DELTA_MIN_PPM <= delta <= _SHORT_DELTA_MAX_PPM
            and delta < long_delta
        ):
            eligible.append(
                (
                    abs(delta - _SHORT_DELTA_TARGET_PPM),
                    -quote.contract.strike_nano_usd,
                    contract_id,
                    quote,
                )
            )
    if not eligible:
        raise P1NativeDecisionError("BCS_NO_ELIGIBLE_SHORT_CALL")
    return min(eligible, key=lambda item: item[:3])[3]


def _derive_projection(
    *,
    selected_snapshot: OptionQuoteSnapshotV1,
    lc0_binding: Lc0SelectionBindingV1,
    fees: BcsFeeScheduleV1,
    quantity: int,
    terminals: tuple[CrrSemanticTerminalV1, ...],
) -> NativeDecisionProjectionV1:
    if (
        type(terminals) is not tuple
        or len(terminals) != EXACT_BATCH_SIZE
        or any(
            type(item) is not CrrSemanticTerminalV1
            or item.terminal_status != "PASS"
            for item in terminals
        )
    ):
        raise P1NativeDecisionError("NATIVE_DECISION_TERMINAL_INVALID")
    quote_by_id = {
        quote.contract.occ_symbol: quote
        for quote in selected_snapshot.option_quotes
    }
    terminal_by_id = {item.contract_id: item for item in terminals}
    if set(quote_by_id) != set(terminal_by_id):
        raise P1NativeDecisionError("NATIVE_DECISION_TERMINAL_BINDING_INVALID")
    long_quote = quote_by_id.get(lc0_binding.long_call_id)
    if long_quote is None:
        raise P1NativeDecisionError("BCS_LONG_LEG_BINDING_MISMATCH")
    coarse_winner = _suite_winner(
        long_quote=long_quote,
        quote_by_id=quote_by_id,
        terminal_by_id=terminal_by_id,
        suite="coarse",
    )
    fine_winner = _suite_winner(
        long_quote=long_quote,
        quote_by_id=quote_by_id,
        terminal_by_id=terminal_by_id,
        suite="fine",
    )
    if coarse_winner.contract.occ_symbol != fine_winner.contract.occ_symbol:
        raise P1NativeDecisionError("SELECTOR_MODEL_INSTABILITY")
    short_quote = fine_winner
    long_terminal = terminal_by_id[long_quote.contract.occ_symbol]
    short_terminal = terminal_by_id[short_quote.contract.occ_symbol]
    deltas = (
        long_terminal.coarse_delta_ppm,
        long_terminal.fine_delta_ppm,
        short_terminal.coarse_delta_ppm,
        short_terminal.fine_delta_ppm,
    )
    if any(type(value) is not int for value in deltas):
        raise P1NativeDecisionError("NATIVE_DECISION_TERMINAL_INVALID")
    long_delta = long_terminal.fine_delta_ppm
    short_delta = short_terminal.fine_delta_ppm
    selection = NativeDecisionSelectionV1(
        expiry_utc_ns=long_quote.contract.expiry_utc_ns,
        long_call_id=long_quote.contract.occ_symbol,
        long_coarse_delta_ppm=long_terminal.coarse_delta_ppm,
        long_delta_ppm=long_delta,
        long_strike_nano_usd=long_quote.contract.strike_nano_usd,
        multiplier=long_quote.contract.multiplier,
        net_delta_ppm=long_delta - short_delta,
        short_call_id=short_quote.contract.occ_symbol,
        short_coarse_delta_ppm=short_terminal.coarse_delta_ppm,
        short_delta_ppm=short_delta,
        short_strike_nano_usd=short_quote.contract.strike_nano_usd,
    )
    long_book = long_quote.top_of_book
    short_book = short_quote.top_of_book
    if long_book.ask_size < quantity or short_book.bid_size < quantity:
        raise P1NativeDecisionError("BCS_SIZE_INSUFFICIENT")
    long_entry_quote = NativeDecisionEntryQuoteV1(
        contract_id=selection.long_call_id,
        displayed_size=long_book.ask_size,
        price_nano_usd=long_book.ask_nano_usd,
        side="ASK",
        tick_nano_usd=long_book.tick_nano_usd,
    )
    short_entry_quote = NativeDecisionEntryQuoteV1(
        contract_id=selection.short_call_id,
        displayed_size=short_book.bid_size,
        price_nano_usd=short_book.bid_nano_usd,
        side="BID",
        tick_nano_usd=short_book.tick_nano_usd,
    )
    total_entry_fees = (
        fees.long_entry_fee_nano_usd_per_contract
        + fees.short_entry_fee_nano_usd_per_contract
    ) * quantity
    entry_fees = NativeDecisionEntryFeesV1(
        long_entry_fee_nano_usd_per_contract=(
            fees.long_entry_fee_nano_usd_per_contract
        ),
        short_entry_fee_nano_usd_per_contract=(
            fees.short_entry_fee_nano_usd_per_contract
        ),
        total_entry_fees_nano_usd=total_entry_fees,
    )
    multiplier_quantity = selection.multiplier * quantity
    base_debit = (
        long_book.ask_nano_usd - short_book.bid_nano_usd
    ) * multiplier_quantity + total_entry_fees
    stressed_short_bid = max(
        0,
        short_book.bid_nano_usd - short_book.tick_nano_usd,
    )
    stressed_debit = (
        long_book.ask_nano_usd
        + long_book.tick_nano_usd
        - stressed_short_bid
    ) * multiplier_quantity + total_entry_fees
    gross_width = (
        selection.short_strike_nano_usd - selection.long_strike_nano_usd
    ) * multiplier_quantity
    if (
        type(base_debit) is not int
        or type(stressed_debit) is not int
        or type(gross_width) is not int
        or not 0 < base_debit <= stressed_debit < gross_width
    ):
        raise P1NativeDecisionError("BCS_INVALID_NET_DEBIT")
    entry_cost = NativeDecisionEntryCostV1(
        base_net_debit_nano_usd=base_debit,
        gross_expiry_width_value_nano_usd=gross_width,
        max_loss_nano_usd=base_debit,
        quantity=quantity,
        stressed_net_debit_nano_usd=stressed_debit,
        theoretical_expiry_max_profit_nano_usd=gross_width - base_debit,
        theoretical_expiry_only=True,
    )
    return NativeDecisionProjectionV1(
        carrier_terminals=(
            NativeCarrierTerminalV1(
                carrier_id="LC0",
                reason_code="LC0_AUTHORITY_NOT_IMPLEMENTED",
                terminal_status="NO_DECISION",
            ),
            NativeCarrierTerminalV1(
                carrier_id="BCS0",
                reason_code="RESEARCH_ONLY_NOT_ACTIONABLE",
                terminal_status="PASS",
            ),
        ),
        decision=NativeDecisionStateV1(
            actionable=False,
            broker_order_count=0,
            owner_selection_required=False,
            reason_code="CARRIER_EVALUATION_INCOMPLETE",
            status="NO_DECISION",
        ),
        entry_cost=entry_cost,
        entry_fees=entry_fees,
        long_entry_quote=long_entry_quote,
        short_entry_quote=short_entry_quote,
        selection=selection,
        selector_policy=NativeDecisionSelectorPolicyV1(),
    )


def _closure_values(function: FunctionType) -> dict[str, object]:
    closure = function.__closure__ or ()
    if len(closure) != len(function.__code__.co_freevars):
        return {}
    try:
        return {
            name: cell.cell_contents
            for name, cell in zip(
                function.__code__.co_freevars,
                closure,
                strict=True,
            )
        }
    except ValueError:
        return {}


def _native_binding_is_valid(
    compute_call: object,
    verify_call: object,
    backend_evidence_sha256: str,
) -> bool:
    try:
        if (
            type(compute_call) is not FunctionType
            or type(verify_call) is not FunctionType
            or compute_call.__module__ != _native_call_module.__name__
            or verify_call.__module__ != _native_call_module.__name__
            or compute_call.__qualname__
            != "create_native_crr_delta_engine.<locals>.compute"
            or verify_call.__qualname__
            != "create_native_crr_delta_engine.<locals>.is_verified"
            or sys.modules.get(_native_call_module.__name__)
            is not _native_call_module
        ):
            return False
        module_path = Path(_native_call_module.__file__).resolve(strict=True)
        if (
            Path(compute_call.__code__.co_filename).resolve(strict=True)
            != module_path
            or Path(verify_call.__code__.co_filename).resolve(strict=True)
            != module_path
            or sha256(module_path.read_bytes()).hexdigest()
            != NATIVE_ORCHESTRATOR_SOURCE_ARTIFACT_SHA256
        ):
            return False
        compute_closure = _closure_values(compute_call)
        verify_closure = _closure_values(verify_call)
        shared_names = (
            "combined_backend_evidence_sha256",
            "registry_lock",
            "require_captured_runtime",
            "result_type",
            "verified_by_identity",
        )
        return (
            compute_closure.get("combined_backend_evidence_sha256")
            == backend_evidence_sha256
            and verify_closure.get("combined_backend_evidence_sha256")
            == backend_evidence_sha256
            and compute_closure.get("result_type") is NativeCrrDeltaResultV1
            and verify_closure.get("result_type") is NativeCrrDeltaResultV1
            and all(
                compute_closure.get(name) is verify_closure.get(name)
                for name in shared_names[1:]
            )
            and verify_call(None) is False
        )
    except Exception:
        return False


def _callable_state(value: Callable[..., object]) -> tuple[object, ...]:
    implementation = getattr(value, "__func__", value)
    code = getattr(implementation, "__code__", None)
    closure = getattr(implementation, "__closure__", None) or ()
    return (
        id(value),
        id(implementation),
        code,
        getattr(implementation, "__defaults__", None),
        None
        if getattr(implementation, "__kwdefaults__", None) is None
        else dict(implementation.__kwdefaults__),
        id(getattr(value, "__self__", None)),
        tuple((id(cell), id(cell.cell_contents)) for cell in closure),
    )


def _state_kwargs(state: _InvocationStateV1) -> dict[str, object]:
    return {
        "signal": state.signal,
        "candidate_snapshots": state.candidate_snapshots,
        "pit_inputs": state.pit_inputs,
        "lc0_binding": state.lc0_binding,
        "fees": state.fees,
        "research_contract_sha256": state.research_contract_sha256,
        "p1_contract_sha256": state.p1_contract_sha256,
        "rule_package_version": state.rule_package_version,
        "quantity": state.quantity,
    }


def _prepared_matches(
    original: _PreparedRequestV1,
    current: _PreparedRequestV1,
) -> bool:
    return (
        current.request_binding == original.request_binding
        and current.candidate_ledger_sha256
        == original.candidate_ledger_sha256
        and current.selected_snapshot is original.selected_snapshot
        and current.bound_inputs == original.bound_inputs
    )


def _create_p1_native_decision_shadow_engine(
    *,
    compute_call: Callable[..., object],
    verify_call: Callable[[object], bool],
    combined_backend_evidence_sha256: str,
    require_native_binding: bool,
    native_binding_validator: Callable[[object, object, str], bool],
    projection_deriver: Callable[..., NativeDecisionProjectionV1],
    batch_engine_factory: Callable[..., object],
    sha256_validator: Callable[[object, str], str],
    request_preparer: Callable[..., _PreparedRequestV1],
    prepared_request_matcher: Callable[
        [_PreparedRequestV1, _PreparedRequestV1],
        bool,
    ],
    invocation_state_serializer: Callable[[_InvocationStateV1], dict[str, object]],
    document_hasher: Callable[[object], str],
    runtime_semantic_hasher: Callable[..., str],
    shadow_artifact_hasher: Callable[..., str],
    native_execution_hasher: Callable[..., str],
    callable_state_reader: Callable[[Callable[..., object]], tuple[object, ...]],
    terminal_semantic_hasher: Callable[[tuple[CrrSemanticTerminalV1, ...]], str],
) -> tuple[
    Callable[..., NativeDecisionShadowReceiptV1 | NativeDecisionShadowFailureV1],
    Callable[[object], bool],
    Callable[[object], bool],
]:
    if (
        not callable(compute_call)
        or not callable(verify_call)
        or type(require_native_binding) is not bool
        or not callable(native_binding_validator)
        or not callable(projection_deriver)
        or not callable(batch_engine_factory)
        or not callable(sha256_validator)
        or not callable(request_preparer)
        or not callable(prepared_request_matcher)
        or not callable(invocation_state_serializer)
        or not callable(document_hasher)
        or not callable(runtime_semantic_hasher)
        or not callable(shadow_artifact_hasher)
        or not callable(native_execution_hasher)
        or not callable(callable_state_reader)
        or not callable(terminal_semantic_hasher)
    ):
        raise P1NativeDecisionError("NATIVE_DECISION_ENGINE_BINDING_INVALID")
    backend_hash = sha256_validator(
        combined_backend_evidence_sha256,
        "NATIVE_DECISION_BACKEND_EVIDENCE_INVALID",
    )
    if require_native_binding and not native_binding_validator(
        compute_call,
        verify_call,
        backend_hash,
    ):
        raise P1NativeDecisionError("NATIVE_DECISION_NATIVE_BINDING_INVALID")

    compute = compute_call
    verify = verify_call
    compute_state = callable_state_reader(compute)
    verify_state = callable_state_reader(verify)
    strict_native = require_native_binding
    prepare_request = request_preparer
    derive_projection = projection_deriver
    prepared_matches = prepared_request_matcher
    state_kwargs = invocation_state_serializer
    document_sha256 = document_hasher
    runtime_semantic_sha256 = runtime_semantic_hasher
    shadow_artifact_sha256 = shadow_artifact_hasher
    native_execution_sha256 = native_execution_hasher
    validate_native_binding = native_binding_validator
    create_batch_engine = batch_engine_factory
    require_sha256 = sha256_validator
    semantic_terminal_sha256 = terminal_semantic_hasher
    callable_state = callable_state_reader
    module_namespace = globals()
    current_module = sys.modules[__name__]
    helper_bindings = (
        (
            "bind_crr_call_inputs_bulk_from_snapshot",
            bind_crr_call_inputs_bulk_from_snapshot,
        ),
        ("_prepare_request", prepare_request),
        ("_derive_projection", derive_projection),
        ("_prepared_matches", prepared_matches),
        ("_state_kwargs", state_kwargs),
        ("_document_sha256", document_sha256),
        ("_runtime_semantic_sha256", runtime_semantic_sha256),
        ("_shadow_artifact_sha256", shadow_artifact_sha256),
        ("_native_execution_sha256", native_execution_sha256),
        ("_native_binding_is_valid", validate_native_binding),
        ("_create_exact_64_batch_engine", create_batch_engine),
        ("_require_sha256", require_sha256),
        ("semantic_output_sha256", semantic_terminal_sha256),
        ("_callable_state", callable_state),
    )
    helper_code_bindings = tuple(
        (helper, helper.__code__, helper.__defaults__, helper.__kwdefaults__)
        for _, helper in helper_bindings
    )
    run_batch, verify_pass_terminal, verify_batch_receipt = (
        create_batch_engine(
            compute_call=compute,
            verify_call=verify,
            backend_evidence_sha256=backend_hash,
        )
    )
    run_batch_state = _callable_state(run_batch)
    verify_terminal_state = _callable_state(verify_pass_terminal)
    verify_batch_state = _callable_state(verify_batch_receipt)
    receipt_type = NativeDecisionShadowReceiptV1
    failure_type = NativeDecisionShadowFailureV1
    registry_lock = threading.Lock()

    SuccessRecord = tuple[
        weakref.ReferenceType[NativeDecisionShadowReceiptV1],
        str,
        _InvocationStateV1,
        _PreparedRequestV1,
        CrrExact64BatchReceiptV1,
    ]
    FailureRecord = tuple[
        weakref.ReferenceType[NativeDecisionShadowFailureV1],
        str,
        _InvocationStateV1,
        _PreparedRequestV1,
        CrrExact64BatchReceiptV1,
    ]
    success_by_identity: dict[int, SuccessRecord] = {}
    failure_by_identity: dict[int, FailureRecord] = {}

    def require_runtime() -> None:
        try:
            intact = (
                sys.modules.get(__name__) is current_module
                and _callable_state(compute) == compute_state
                and _callable_state(verify) == verify_state
                and _callable_state(run_batch) == run_batch_state
                and _callable_state(verify_pass_terminal)
                == verify_terminal_state
                and _callable_state(verify_batch_receipt)
                == verify_batch_state
                and all(
                    module_namespace.get(name) is helper
                    for name, helper in helper_bindings
                )
                and all(
                    helper.__code__ is code
                    and helper.__defaults__ is defaults
                    and helper.__kwdefaults__ is kwdefaults
                    for helper, code, defaults, kwdefaults in helper_code_bindings
                )
                and (
                    not strict_native
                    or validate_native_binding(compute, verify, backend_hash)
                )
            )
        except Exception as error:
            raise P1NativeDecisionError(
                "NATIVE_DECISION_RUNTIME_INTEGRITY_INVALID"
            ) from error
        if not intact:
            raise P1NativeDecisionError(
                "NATIVE_DECISION_RUNTIME_INTEGRITY_INVALID"
            )

    def prepare_from_state(state: _InvocationStateV1) -> _PreparedRequestV1:
        return prepare_request(**state_kwargs(state))

    def make_failure(
        *,
        reason_code: str,
        state: _InvocationStateV1,
        prepared: _PreparedRequestV1,
        batch_receipt: CrrExact64BatchReceiptV1,
    ) -> NativeDecisionShadowFailureV1:
        failure = failure_type(
            execution_status="NATIVE_DECISION_SHADOW_FAILED_NO_DECISION",
            p1_status=P1_QUALIFICATION_STATUS,
            reason_code=reason_code,
            requested=batch_receipt.requested,
            bound=batch_receipt.bound,
            started=batch_receipt.started,
            terminal=batch_receipt.terminal,
            worker_count=batch_receipt.worker_count,
            kernel_threads=batch_receipt.kernel_threads,
            cache_hits=batch_receipt.cache_hits,
            first_failure_ordinal=batch_receipt.first_failure_ordinal,
            request_binding=prepared.request_binding,
            request_sha256=prepared.request_binding.request_sha256,
            call_terminals=batch_receipt.terminals,
            call_semantic_output_sha256=(
                batch_receipt.semantic_output_sha256
            ),
            batch_execution_provenance_sha256=(
                batch_receipt.execution_provenance_sha256
            ),
            backend_evidence_sha256=backend_hash,
            actionable=False,
            broker_order_count=0,
        )
        identity = id(failure)

        def discard(
            reference: weakref.ReferenceType[NativeDecisionShadowFailureV1],
        ) -> None:
            with registry_lock:
                record = failure_by_identity.get(identity)
                if record is not None and record[0] is reference:
                    failure_by_identity.pop(identity, None)

        reference = weakref.ref(failure, discard)
        record: FailureRecord = (
            reference,
            document_sha256(failure.as_dict()),
            state,
            prepared,
            batch_receipt,
        )
        with registry_lock:
            failure_by_identity[identity] = record
        return failure

    def register_receipt(
        receipt: NativeDecisionShadowReceiptV1,
        *,
        state: _InvocationStateV1,
        prepared: _PreparedRequestV1,
        batch_receipt: CrrExact64BatchReceiptV1,
    ) -> None:
        if (
            verify_batch_receipt(batch_receipt) is not True
            or receipt.call_terminals is not batch_receipt.terminals
            or any(
                verify_pass_terminal(item) is not True
                for item in batch_receipt.terminals
            )
        ):
            raise P1NativeDecisionError(
                "NATIVE_DECISION_BATCH_EVIDENCE_INVALID"
            )
        identity = id(receipt)

        def discard(
            reference: weakref.ReferenceType[NativeDecisionShadowReceiptV1],
        ) -> None:
            with registry_lock:
                record = success_by_identity.get(identity)
                if record is not None and record[0] is reference:
                    success_by_identity.pop(identity, None)

        reference = weakref.ref(receipt, discard)
        record: SuccessRecord = (
            reference,
            document_sha256(receipt.as_dict()),
            state,
            prepared,
            batch_receipt,
        )
        with registry_lock:
            success_by_identity[identity] = record

    def run(
        *,
        signal: SignalSnapshotV1,
        candidate_snapshots: tuple[OptionQuoteSnapshotV1, ...],
        pit_inputs: CrrPitInputsV1,
        lc0_binding: Lc0SelectionBindingV1,
        fees: BcsFeeScheduleV1,
        research_contract_sha256: str,
        p1_contract_sha256: str,
        rule_package_version: str,
        quantity: int,
    ) -> NativeDecisionShadowReceiptV1 | NativeDecisionShadowFailureV1:
        require_runtime()
        state = _InvocationStateV1(
            signal=signal,
            candidate_snapshots=candidate_snapshots,
            pit_inputs=pit_inputs,
            lc0_binding=lc0_binding,
            fees=fees,
            research_contract_sha256=research_contract_sha256,
            p1_contract_sha256=p1_contract_sha256,
            rule_package_version=rule_package_version,
            quantity=quantity,
        )
        prepared = prepare_from_state(state)
        require_runtime()
        batch_receipt = run_batch(prepared.bound_inputs)
        require_runtime()
        if (
            verify_batch_receipt(batch_receipt) is not True
            or batch_receipt.backend_evidence_sha256 != backend_hash
            or batch_receipt.input_vector_sha256
            != prepared.request_binding.call_input_vector_sha256
        ):
            raise P1NativeDecisionError(
                "NATIVE_DECISION_BATCH_EVIDENCE_INVALID"
            )
        try:
            current_prepared = prepare_from_state(state)
        except P1NativeDecisionError as error:
            raise P1NativeDecisionError(
                "NATIVE_DECISION_INPUT_MUTATED"
            ) from error
        if not prepared_matches(prepared, current_prepared):
            raise P1NativeDecisionError("NATIVE_DECISION_INPUT_MUTATED")

        if batch_receipt.first_failure_ordinal is not None:
            reason = batch_receipt.terminals[
                batch_receipt.first_failure_ordinal - 1
            ].reason_code
            return make_failure(
                reason_code=reason,
                state=state,
                prepared=prepared,
                batch_receipt=batch_receipt,
            )
        if any(
            verify_pass_terminal(item) is not True
            for item in batch_receipt.terminals
        ):
            return make_failure(
                reason_code="UNVERIFIED_DELTA_RESULT",
                state=state,
                prepared=prepared,
                batch_receipt=batch_receipt,
            )
        try:
            projection = derive_projection(
                selected_snapshot=prepared.selected_snapshot,
                lc0_binding=state.lc0_binding,
                fees=state.fees,
                quantity=state.quantity,
                terminals=batch_receipt.terminals,
            )
        except P1NativeDecisionError as error:
            return make_failure(
                reason_code=error.reason_code,
                state=state,
                prepared=prepared,
                batch_receipt=batch_receipt,
            )
        except Exception:
            return make_failure(
                reason_code="NATIVE_DECISION_PROJECTION_UNEXPECTED_EXCEPTION",
                state=state,
                prepared=prepared,
                batch_receipt=batch_receipt,
            )
        require_runtime()
        try:
            final_prepared = prepare_from_state(state)
        except P1NativeDecisionError as error:
            raise P1NativeDecisionError(
                "NATIVE_DECISION_INPUT_MUTATED"
            ) from error
        if not prepared_matches(prepared, final_prepared):
            raise P1NativeDecisionError("NATIVE_DECISION_INPUT_MUTATED")

        request_sha256 = prepared.request_binding.request_sha256
        projection_sha256 = projection.projection_sha256
        runtime_semantic = runtime_semantic_sha256(
            request_binding=prepared.request_binding,
            call_semantic_output_sha256=(
                batch_receipt.semantic_output_sha256
            ),
            projection_sha256=projection_sha256,
        )
        artifact_sha256 = shadow_artifact_sha256(
            request_binding=prepared.request_binding,
            call_terminals=batch_receipt.terminals,
            call_semantic_output_sha256=(
                batch_receipt.semantic_output_sha256
            ),
            decision_projection=projection,
            projection_sha256=projection_sha256,
            runtime_semantic_sha256=runtime_semantic,
        )
        execution_sha256 = native_execution_sha256(
            backend_evidence_sha256=backend_hash,
            batch_execution_provenance_sha256=(
                batch_receipt.execution_provenance_sha256
            ),
            request_sha256=request_sha256,
            runtime_semantic_sha256=runtime_semantic,
            shadow_artifact_sha256=artifact_sha256,
        )
        components = NativeDecisionBackendEvidenceComponentsV1(
            backend_evidence_sha256=backend_hash,
            input_sha256=request_sha256,
            execution_sha256=execution_sha256,
            artifact_sha256=artifact_sha256,
        )
        receipt = receipt_type(
            execution_status=NATIVE_DECISION_SHADOW_STATUS,
            p1_status=P1_QUALIFICATION_STATUS,
            requested=batch_receipt.requested,
            bound=batch_receipt.bound,
            started=batch_receipt.started,
            terminal=batch_receipt.terminal,
            worker_count=batch_receipt.worker_count,
            kernel_threads=batch_receipt.kernel_threads,
            cache_hits=batch_receipt.cache_hits,
            request_binding=prepared.request_binding,
            request_sha256=request_sha256,
            call_terminals=batch_receipt.terminals,
            call_semantic_output_sha256=(
                batch_receipt.semantic_output_sha256
            ),
            decision_projection=projection,
            projection_sha256=projection_sha256,
            runtime_semantic_sha256=runtime_semantic,
            shadow_artifact_sha256=artifact_sha256,
            batch_execution_provenance_sha256=(
                batch_receipt.execution_provenance_sha256
            ),
            backend_evidence_components=components,
            actionable=False,
            broker_order_count=0,
        )
        register_receipt(
            receipt,
            state=state,
            prepared=prepared,
            batch_receipt=batch_receipt,
        )
        return receipt

    def is_verified_receipt(value: object) -> bool:
        try:
            require_runtime()
            if type(value) is not receipt_type:
                return False
            with registry_lock:
                record = success_by_identity.get(id(value))
            if (
                record is None
                or record[0]() is not value
                or record[1] != document_sha256(value.as_dict())
            ):
                return False
            state = record[2]
            prepared = record[3]
            batch_receipt = record[4]
            if (
                verify_batch_receipt(batch_receipt) is not True
                or value.call_terminals is not batch_receipt.terminals
                or any(
                    verify_pass_terminal(item) is not True
                    for item in value.call_terminals
                )
            ):
                return False
            current_prepared = prepare_from_state(state)
            if not prepared_matches(prepared, current_prepared):
                return False
            expected_projection = derive_projection(
                selected_snapshot=current_prepared.selected_snapshot,
                lc0_binding=state.lc0_binding,
                fees=state.fees,
                quantity=state.quantity,
                terminals=value.call_terminals,
            )
            if expected_projection != value.decision_projection:
                return False
            reconstructed = receipt_type(
                **{
                    item.name: getattr(value, item.name)
                    for item in fields(receipt_type)
                }
            )
            return reconstructed == value
        except Exception:
            return False

    def is_verified_failure(value: object) -> bool:
        try:
            require_runtime()
            if type(value) is not failure_type:
                return False
            with registry_lock:
                record = failure_by_identity.get(id(value))
            if (
                record is None
                or record[0]() is not value
                or record[1] != document_sha256(value.as_dict())
            ):
                return False
            state = record[2]
            prepared = record[3]
            batch_receipt = record[4]
            if (
                verify_batch_receipt(batch_receipt) is not True
                or value.call_terminals is not batch_receipt.terminals
            ):
                return False
            current_prepared = prepare_from_state(state)
            if not prepared_matches(prepared, current_prepared):
                return False
            if value.first_failure_ordinal is not None:
                expected_reason = value.call_terminals[
                    value.first_failure_ordinal - 1
                ].reason_code
            else:
                try:
                    derive_projection(
                        selected_snapshot=current_prepared.selected_snapshot,
                        lc0_binding=state.lc0_binding,
                        fees=state.fees,
                        quantity=state.quantity,
                        terminals=value.call_terminals,
                    )
                except P1NativeDecisionError as error:
                    expected_reason = error.reason_code
                else:
                    return False
            if value.reason_code != expected_reason:
                return False
            reconstructed = failure_type(
                **{
                    item.name: getattr(value, item.name)
                    for item in fields(failure_type)
                }
            )
            return reconstructed == value
        except Exception:
            return False

    return run, is_verified_receipt, is_verified_failure


def _build_native_decision_engine_entrypoints(
    *,
    core_factory: Callable[..., object],
    native_binding_validator: Callable[[object, object, str], bool],
    projection_deriver: Callable[..., NativeDecisionProjectionV1],
    batch_engine_factory: Callable[..., object],
    sha256_validator: Callable[[object, str], str],
    request_preparer: Callable[..., _PreparedRequestV1],
    prepared_request_matcher: Callable[..., bool],
    invocation_state_serializer: Callable[..., dict[str, object]],
    document_hasher: Callable[[object], str],
    runtime_semantic_hasher: Callable[..., str],
    shadow_artifact_hasher: Callable[..., str],
    native_execution_hasher: Callable[..., str],
    callable_state_reader: Callable[..., tuple[object, ...]],
    terminal_semantic_hasher: Callable[..., str],
) -> tuple[
    Callable[..., object],
    Callable[..., object],
]:
    def strict_constructor(
        *,
        compute_call: Callable[..., object],
        verify_call: Callable[[object], bool],
        combined_backend_evidence_sha256: str,
    ) -> object:
        return core_factory(
            compute_call=compute_call,
            verify_call=verify_call,
            combined_backend_evidence_sha256=(
                combined_backend_evidence_sha256
            ),
            require_native_binding=True,
            native_binding_validator=native_binding_validator,
            projection_deriver=projection_deriver,
            batch_engine_factory=batch_engine_factory,
            sha256_validator=sha256_validator,
            request_preparer=request_preparer,
            prepared_request_matcher=prepared_request_matcher,
            invocation_state_serializer=invocation_state_serializer,
            document_hasher=document_hasher,
            runtime_semantic_hasher=runtime_semantic_hasher,
            shadow_artifact_hasher=shadow_artifact_hasher,
            native_execution_hasher=native_execution_hasher,
            callable_state_reader=callable_state_reader,
            terminal_semantic_hasher=terminal_semantic_hasher,
        )

    def test_constructor(
        *,
        compute_call: Callable[..., object],
        verify_call: Callable[[object], bool],
        combined_backend_evidence_sha256: str,
    ) -> object:
        return core_factory(
            compute_call=compute_call,
            verify_call=verify_call,
            combined_backend_evidence_sha256=(
                combined_backend_evidence_sha256
            ),
            require_native_binding=False,
            native_binding_validator=native_binding_validator,
            projection_deriver=projection_deriver,
            batch_engine_factory=batch_engine_factory,
            sha256_validator=sha256_validator,
            request_preparer=request_preparer,
            prepared_request_matcher=prepared_request_matcher,
            invocation_state_serializer=invocation_state_serializer,
            document_hasher=document_hasher,
            runtime_semantic_hasher=runtime_semantic_hasher,
            shadow_artifact_hasher=shadow_artifact_hasher,
            native_execution_hasher=native_execution_hasher,
            callable_state_reader=callable_state_reader,
            terminal_semantic_hasher=terminal_semantic_hasher,
        )

    test_constructor.__name__ = (
        "_create_p1_native_decision_shadow_engine_for_tests"
    )
    test_constructor.__qualname__ = test_constructor.__name__
    test_constructor.__doc__ = (
        "Private fast-engine seam for bounded semantic/adversarial tests only."
    )
    return strict_constructor, test_constructor


(
    _strict_native_decision_engine_constructor,
    _create_p1_native_decision_shadow_engine_for_tests,
) = _build_native_decision_engine_entrypoints(
    core_factory=_create_p1_native_decision_shadow_engine,
    native_binding_validator=_native_binding_is_valid,
    projection_deriver=_derive_projection,
    batch_engine_factory=_create_exact_64_batch_engine,
    sha256_validator=_require_sha256,
    request_preparer=_prepare_request,
    prepared_request_matcher=_prepared_matches,
    invocation_state_serializer=_state_kwargs,
    document_hasher=_document_sha256,
    runtime_semantic_hasher=_runtime_semantic_sha256,
    shadow_artifact_hasher=_shadow_artifact_sha256,
    native_execution_hasher=_native_execution_sha256,
    callable_state_reader=_callable_state,
    terminal_semantic_hasher=semantic_output_sha256,
)


def _build_public_native_decision_factory(
    *,
    strict_engine_constructor: Callable[..., object],
    protected_implementations: tuple[
        tuple[str, Callable[..., object]],
        ...,
    ],
) -> Callable[..., object]:
    module_namespace = globals()
    current_module = sys.modules[__name__]
    error_type = P1NativeDecisionError
    artifacts: list[tuple[object, ...]] = []
    for name, implementation in protected_implementations:
        if type(implementation) is not FunctionType:
            raise RuntimeError("native Decision implementation is not a function")
        code = implementation.__code__
        source_path = Path(code.co_filename).resolve(strict=True)
        artifacts.append(
            (
                name,
                implementation,
                code,
                implementation.__defaults__,
                None
                if implementation.__kwdefaults__ is None
                else dict(implementation.__kwdefaults__),
                source_path,
                sha256(source_path.read_bytes()).hexdigest(),
            )
        )
    captured_artifacts = tuple(artifacts)
    strict_code = strict_engine_constructor.__code__
    strict_defaults = strict_engine_constructor.__defaults__
    strict_kwdefaults = (
        None
        if strict_engine_constructor.__kwdefaults__ is None
        else dict(strict_engine_constructor.__kwdefaults__)
    )
    strict_source_path = Path(strict_code.co_filename).resolve(strict=True)
    strict_source_sha256 = sha256(strict_source_path.read_bytes()).hexdigest()
    strict_closure = strict_engine_constructor.__closure__ or ()
    if len(strict_closure) != len(strict_code.co_freevars):
        raise RuntimeError("native Decision constructor closure is incomplete")
    strict_closure_bindings = tuple(
        (name, cell, cell.cell_contents)
        for name, cell in zip(
            strict_code.co_freevars,
            strict_closure,
            strict=True,
        )
    )
    strict_closure_artifacts: list[tuple[object, ...]] = []
    for _, _, implementation in strict_closure_bindings:
        if type(implementation) is not FunctionType:
            continue
        code = implementation.__code__
        source_path = Path(code.co_filename).resolve(strict=True)
        strict_closure_artifacts.append(
            (
                implementation,
                code,
                implementation.__defaults__,
                None
                if implementation.__kwdefaults__ is None
                else dict(implementation.__kwdefaults__),
                source_path,
                sha256(source_path.read_bytes()).hexdigest(),
            )
        )
    captured_strict_closure_artifacts = tuple(strict_closure_artifacts)

    def public_factory(
        *,
        compute_call: Callable[..., object],
        verify_call: Callable[[object], bool],
        combined_backend_evidence_sha256: str,
    ) -> tuple[
        Callable[
            ...,
            NativeDecisionShadowReceiptV1 | NativeDecisionShadowFailureV1,
        ],
        Callable[[object], bool],
        Callable[[object], bool],
    ]:
        try:
            intact = (
                sys.modules.get(__name__) is current_module
                and strict_engine_constructor.__code__ is strict_code
                and strict_engine_constructor.__defaults__ is strict_defaults
                and (
                    None
                    if strict_engine_constructor.__kwdefaults__ is None
                    else dict(strict_engine_constructor.__kwdefaults__)
                )
                == strict_kwdefaults
                and Path(strict_code.co_filename).resolve(strict=True)
                == strict_source_path
                and sha256(strict_source_path.read_bytes()).hexdigest()
                == strict_source_sha256
                and len(strict_engine_constructor.__closure__ or ())
                == len(strict_closure_bindings)
                and all(
                    current_cell is captured_cell
                    and current_cell.cell_contents is implementation
                    for current_cell, (
                        _,
                        captured_cell,
                        implementation,
                    ) in zip(
                        strict_engine_constructor.__closure__ or (),
                        strict_closure_bindings,
                        strict=True,
                    )
                )
                and all(
                    implementation.__code__ is code
                    and implementation.__defaults__ is defaults
                    and (
                        None
                        if implementation.__kwdefaults__ is None
                        else dict(implementation.__kwdefaults__)
                    )
                    == kwdefaults
                    and Path(code.co_filename).resolve(strict=True)
                    == source_path
                    and sha256(source_path.read_bytes()).hexdigest()
                    == source_sha256
                    for (
                        implementation,
                        code,
                        defaults,
                        kwdefaults,
                        source_path,
                        source_sha256,
                    ) in captured_strict_closure_artifacts
                )
                and all(
                    module_namespace.get(name) is implementation
                    and implementation.__code__ is code
                    and implementation.__defaults__ is defaults
                    and (
                        None
                        if implementation.__kwdefaults__ is None
                        else dict(implementation.__kwdefaults__)
                    )
                    == kwdefaults
                    and Path(code.co_filename).resolve(strict=True)
                    == source_path
                    and sha256(source_path.read_bytes()).hexdigest()
                    == source_sha256
                    for (
                        name,
                        implementation,
                        code,
                        defaults,
                        kwdefaults,
                        source_path,
                        source_sha256,
                    ) in captured_artifacts
                )
            )
        except Exception as error:
            raise error_type(
                "NATIVE_DECISION_RUNTIME_INTEGRITY_INVALID"
            ) from error
        if not intact:
            raise error_type("NATIVE_DECISION_RUNTIME_INTEGRITY_INVALID")
        return strict_engine_constructor(
            compute_call=compute_call,
            verify_call=verify_call,
            combined_backend_evidence_sha256=(
                combined_backend_evidence_sha256
            ),
        )

    public_factory.__name__ = "create_p1_native_decision_shadow_engine"
    public_factory.__qualname__ = "create_p1_native_decision_shadow_engine"
    public_factory.__doc__ = (
        "Bind one verified native Call engine to the fixed Decision shadow path."
    )
    return public_factory


create_p1_native_decision_shadow_engine = _build_public_native_decision_factory(
    strict_engine_constructor=_strict_native_decision_engine_constructor,
    protected_implementations=(
        (
            "bind_crr_call_inputs_bulk_from_snapshot",
            bind_crr_call_inputs_bulk_from_snapshot,
        ),
        ("_native_binding_is_valid", _native_binding_is_valid),
        ("_prepare_request", _prepare_request),
        ("_derive_projection", _derive_projection),
        ("_prepared_matches", _prepared_matches),
        ("_state_kwargs", _state_kwargs),
        ("_document_sha256", _document_sha256),
        ("_runtime_semantic_sha256", _runtime_semantic_sha256),
        ("_shadow_artifact_sha256", _shadow_artifact_sha256),
        ("_native_execution_sha256", _native_execution_sha256),
        ("_create_exact_64_batch_engine", _create_exact_64_batch_engine),
        ("_callable_state", _callable_state),
        ("_require_sha256", _require_sha256),
        ("semantic_output_sha256", semantic_output_sha256),
    ),
)


del _strict_native_decision_engine_constructor
del _create_p1_native_decision_shadow_engine
del _build_native_decision_engine_entrypoints
