"""Fresh-process native Decision integration with no execution authority.

The parent pins one prebuilt native manifest and submits one exact typed
Decision request to the approved spawn supervisor.  The child loads and
verifies that pinned native backend, runs the approved same-process shadow
Decision engine once, verifies its process-local result, and emits one
canonical frame.  Returned artifacts are always non-actionable and P1
qualification remains unclaimed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from hashlib import sha256
import json
from multiprocessing.connection import Connection
from pathlib import Path
import re
import threading
import time
from typing import Callable, Mapping
import weakref

from gld_normalizer.errors import NormalizationError

from .bcs import BcsFeeScheduleV1, Lc0SelectionBindingV1
from .crr_batch import (
    P1_QUALIFICATION_STATUS,
    CrrSemanticTerminalV1,
)
from .crr_delta import MODEL_SHA256, CrrPitInputsV1
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
    select_first_complete_option_snapshot,
)
from .native_crr_delta import create_native_crr_delta_engine
from .native_tree import (
    NATIVE_ABI_ID,
    NATIVE_ABI_VERSION,
    NATIVE_MANIFEST_SCHEMA,
    load_native_tree_v1,
)
from .p1_native_decision import (
    NATIVE_DECISION_SHADOW_STATUS,
    P1_CONTRACT_SHA256,
    RESEARCH_CONTRACT_SHA256,
    NativeCarrierTerminalV1,
    NativeDecisionBackendEvidenceComponentsV1,
    NativeDecisionEntryCostV1,
    NativeDecisionEntryFeesV1,
    NativeDecisionEntryQuoteV1,
    NativeDecisionProjectionV1,
    NativeDecisionRequestBindingV1,
    NativeDecisionSelectionV1,
    NativeDecisionSelectorPolicyV1,
    NativeDecisionShadowFailureV1,
    NativeDecisionShadowReceiptV1,
    NativeDecisionStateV1,
    P1NativeDecisionError,
    create_p1_native_decision_shadow_engine,
)
from .p1_process_supervisor import (
    P1_CHILD_FRAME_SCHEMA,
    P1_MAX_REQUEST_BYTES,
    P1_REASON_HARD_TIMEOUT,
    P1_REASON_INPUT_INVALID,
    P1_REASON_SUCCESS,
    P1ProcessSupervisorReceiptV1,
    P1ProcessSupervisorResultV1,
    _P1WorkflowTimingFinalizationV1,
    _make_p1_workflow_process_supervisor,
)


P1_DECISION_REQUEST_SCHEMA = "GLD_P1_DECISION_REQUEST_V1"
P1_DECISION_CHILD_CONFIG_SCHEMA = "GLD_P1_DECISION_CHILD_CONFIG_V1"
P1_DECISION_CHILD_ENVELOPE_SCHEMA = "GLD_P1_DECISION_CHILD_ENVELOPE_V1"
P1_DECISION_CHILD_ARTIFACT_SCHEMA = "GLD_P1_DECISION_CHILD_ARTIFACT_V1"
P1_DECISION_WORKFLOW_TIMING_SCHEMA = "GLD_P1_DECISION_WORKFLOW_TIMING_V1"
P1_DECISION_CHILD_SUCCESS = "P1_DECISION_CHILD_SUCCESS"
P1_DECISION_PARENT_VALIDATION_FAILED = (
    "P1_DECISION_PARENT_VALIDATION_FAILED"
)
P1_DECISION_CHILD_EXECUTION_FAILED = "P1_DECISION_CHILD_EXECUTION_FAILED"
P1_DECISION_CHILD_REQUEST_INVALID = "P1_DECISION_CHILD_REQUEST_INVALID"
P1_DECISION_CHILD_CONFIG_INVALID = "P1_DECISION_CHILD_CONFIG_INVALID"
P1_DECISION_CHILD_DEADLINE_REACHED = "P1_DECISION_CHILD_DEADLINE_REACHED"
P1_DECISION_CHILD_QUALIFICATION_STATUS = "NOT_CLAIMED"

_TYPED_REQUEST_BINDING = "TYPED_DECISION_REQUEST"
_REJECTED_INPUT_BINDING = "PARENT_REJECTED_INPUT"

_REQUEST_MAX_BYTES = 900_000
_MANIFEST_MAX_BYTES = 4 * 1024 * 1024
_WORKFLOW_FIXED_TIMEOUT_NS = 5_000_000_000
_MAX_DEPTH = 24
_MAX_NODES = 80_000
_MAX_STRING_CHARS = 16_384
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_TOKEN_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z", re.ASCII)

_SUPERVISOR_FAILURE_ORIGINS = frozenset(
    {
        "INPUT",
        "START",
        "DEADLINE",
        "CHILD_EXIT",
        "CHILD_PROTOCOL",
        "CHILD_DECLARED",
        "SUPERVISOR",
        "DESCENDANT",
    }
)

_NATIVE_MANIFEST_KEYS = frozenset(
    {
        "abi_id",
        "abi_version",
        "backend_evidence_sha256",
        "binary_filename",
        "binary_sha256",
        "binary_size",
        "build_input_sha256",
        "build_manifest_sha256",
        "compile_command_sha256",
        "compiler_executable",
        "compiler_executable_sha256",
        "compiler_flags",
        "compiler_flags_sha256",
        "compiler_version_sha256",
        "dependency_manifest",
        "dependency_manifest_sha256",
        "otool_dependency_sha256",
        "python_header_manifest",
        "python_header_manifest_sha256",
        "runtime_environment",
        "runtime_environment_sha256",
        "schema_version",
        "soabi",
        "source_manifest_sha256",
        "source_path",
        "source_sha256",
        "target_environment",
        "target_environment_sha256",
    }
)


class P1DecisionChildError(ValueError):
    """One stable local integration boundary error."""

    def __init__(self, reason_code: str) -> None:
        if type(reason_code) is not str or _REASON_RE.fullmatch(reason_code) is None:
            raise ValueError("invalid P1 Decision child reason")
        self.reason_code = reason_code
        super().__init__(reason_code)


def _require_sha256(value: object, reason_code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise P1DecisionChildError(reason_code)
    return value


def _reject_number(_value: str) -> object:
    raise ValueError("non-integral JSON number")


def _reject_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if type(key) is not str or not key or key in value:
            raise ValueError("duplicate or invalid key")
        value[key] = item
    return value


def _validate_json_tree(
    value: object,
    *,
    depth: int = 0,
    nodes: list[int] | None = None,
) -> None:
    count = [0] if nodes is None else nodes
    if depth > _MAX_DEPTH:
        raise ValueError("JSON depth")
    count[0] += 1
    if count[0] > _MAX_NODES:
        raise ValueError("JSON nodes")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if value.bit_length() > 64:
            raise ValueError("JSON integer")
        return
    if type(value) is str:
        if len(value) > _MAX_STRING_CHARS:
            raise ValueError("JSON string")
        return
    if type(value) is list:
        for item in value:
            _validate_json_tree(item, depth=depth + 1, nodes=count)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or not key or len(key) > 256:
                raise ValueError("JSON key")
            _validate_json_tree(item, depth=depth + 1, nodes=count)
        return
    raise ValueError("JSON type")


def _canonical_json_bytes(value: object, *, newline: bool = False) -> bytes:
    try:
        _validate_json_tree(value)
        text = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if newline:
            text += "\n"
        return text.encode("ascii")
    except (RecursionError, TypeError, UnicodeEncodeError, ValueError) as error:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID) from error


def _parse_canonical_json_bytes(
    raw: object,
    *,
    maximum: int,
    newline: bool = False,
) -> object:
    if type(raw) is not bytes or not raw or len(raw) > maximum:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    try:
        value = json.loads(
            raw.decode("ascii", "strict"),
            object_pairs_hook=_reject_pairs,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        _validate_json_tree(value)
    except (
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID) from error
    if _canonical_json_bytes(value, newline=newline) != raw:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    return value


def _exact_dict(
    value: object,
    keys: set[str] | frozenset[str],
    reason_code: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != set(keys):
        raise P1DecisionChildError(reason_code)
    return value


def _flat_document(value: object, expected_type: type[object]) -> dict[str, object]:
    if type(value) is not expected_type:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    try:
        return {item.name: getattr(value, item.name) for item in fields(expected_type)}
    except AttributeError as error:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID) from error


def _flat_construct(
    value: object,
    expected_type: type[object],
    reason_code: str,
) -> object:
    keys = {item.name for item in fields(expected_type)}
    document = _exact_dict(value, keys, reason_code)
    try:
        result = expected_type(**document)
    except (AttributeError, NormalizationError, P1NativeDecisionError, TypeError, ValueError) as error:
        raise P1DecisionChildError(reason_code) from error
    if type(result) is not expected_type:
        raise P1DecisionChildError(reason_code)
    return result


def _book_document(book: TopOfBookV1) -> dict[str, object]:
    document = _flat_document(book, TopOfBookV1)
    if type(document["flags"]) is not tuple:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    document["flags"] = list(document["flags"])
    return document


def _book_from_document(value: object) -> TopOfBookV1:
    keys = {item.name for item in fields(TopOfBookV1)}
    document = dict(_exact_dict(value, keys, P1_DECISION_CHILD_REQUEST_INVALID))
    flags = document["flags"]
    if type(flags) is not list or any(type(item) is not str for item in flags):
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    document["flags"] = tuple(flags)
    return _flat_construct(
        document,
        TopOfBookV1,
        P1_DECISION_CHILD_REQUEST_INVALID,
    )


def _contract_document(contract: OptionContractV1) -> dict[str, object]:
    return _flat_document(contract, OptionContractV1)


def _contract_from_document(value: object) -> OptionContractV1:
    return _flat_construct(
        value,
        OptionContractV1,
        P1_DECISION_CHILD_REQUEST_INVALID,
    )


def _quote_document(quote: OptionQuoteV1) -> dict[str, object]:
    if type(quote) is not OptionQuoteV1:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    return {
        "contract": _contract_document(quote.contract),
        "top_of_book": _book_document(quote.top_of_book),
    }


def _quote_from_document(value: object) -> OptionQuoteV1:
    document = _exact_dict(
        value,
        {"contract", "top_of_book"},
        P1_DECISION_CHILD_REQUEST_INVALID,
    )
    try:
        return OptionQuoteV1(
            contract=_contract_from_document(document["contract"]),
            top_of_book=_book_from_document(document["top_of_book"]),
        )
    except NormalizationError as error:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID) from error


def _snapshot_document(snapshot: OptionQuoteSnapshotV1) -> dict[str, object]:
    if type(snapshot) is not OptionQuoteSnapshotV1:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    document = _flat_document(snapshot, OptionQuoteSnapshotV1)
    document["underlying_top"] = _book_document(snapshot.underlying_top)
    if type(snapshot.option_quotes) is not tuple:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    document["option_quotes"] = [
        _quote_document(quote) for quote in snapshot.option_quotes
    ]
    return document


def _snapshot_from_document(value: object) -> OptionQuoteSnapshotV1:
    keys = {item.name for item in fields(OptionQuoteSnapshotV1)}
    document = dict(_exact_dict(value, keys, P1_DECISION_CHILD_REQUEST_INVALID))
    quotes = document["option_quotes"]
    if type(quotes) is not list or len(quotes) != 64:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    document["underlying_top"] = _book_from_document(document["underlying_top"])
    document["option_quotes"] = tuple(
        _quote_from_document(item) for item in quotes
    )
    return _flat_construct(
        document,
        OptionQuoteSnapshotV1,
        P1_DECISION_CHILD_REQUEST_INVALID,
    )


@dataclass(frozen=True, slots=True)
class P1DecisionRequestV1:
    signal: SignalSnapshotV1
    candidate_snapshots: tuple[OptionQuoteSnapshotV1, ...]
    pit_inputs: CrrPitInputsV1
    lc0_binding: Lc0SelectionBindingV1
    fees: BcsFeeScheduleV1
    research_contract_sha256: str
    p1_contract_sha256: str
    rule_package_version: str
    quantity: int

    def __post_init__(self) -> None:
        if (
            type(self.signal) is not SignalSnapshotV1
            or type(self.candidate_snapshots) is not tuple
            or not 1 <= len(self.candidate_snapshots) <= 64
            or any(
                type(item) is not OptionQuoteSnapshotV1
                for item in self.candidate_snapshots
            )
            or type(self.pit_inputs) is not CrrPitInputsV1
            or type(self.lc0_binding) is not Lc0SelectionBindingV1
            or type(self.fees) is not BcsFeeScheduleV1
            or self.research_contract_sha256 != RESEARCH_CONTRACT_SHA256
            or self.p1_contract_sha256 != P1_CONTRACT_SHA256
            or type(self.rule_package_version) is not str
            or self.rule_package_version != self.signal.rule_version
            or type(self.quantity) is not int
            or self.quantity != 1
        ):
            raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
        if tuple(item.candidate_ordinal for item in self.candidate_snapshots) != tuple(
            range(1, len(self.candidate_snapshots) + 1)
        ):
            raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
        for snapshot in self.candidate_snapshots:
            if (
                len(snapshot.option_quotes) != 64
            ):
                raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
            order = tuple(
                (
                    quote.contract.expiry_utc_ns,
                    quote.contract.strike_nano_usd,
                    quote.contract.occ_symbol,
                )
                for quote in snapshot.option_quotes
            )
            if order != tuple(sorted(order)) or len(set(order)) != 64:
                raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate_snapshots": [
                _snapshot_document(item) for item in self.candidate_snapshots
            ],
            "fees": _flat_document(self.fees, BcsFeeScheduleV1),
            "lc0_binding": _flat_document(
                self.lc0_binding,
                Lc0SelectionBindingV1,
            ),
            "p1_contract_sha256": self.p1_contract_sha256,
            "pit_inputs": _flat_document(self.pit_inputs, CrrPitInputsV1),
            "quantity": self.quantity,
            "research_contract_sha256": self.research_contract_sha256,
            "rule_package_version": self.rule_package_version,
            "schema_version": P1_DECISION_REQUEST_SCHEMA,
            "signal": _flat_document(self.signal, SignalSnapshotV1),
        }

    @property
    def request_sha256(self) -> str:
        return sha256(_canonical_json_bytes(self.as_dict())).hexdigest()


_REQUEST_KEYS = frozenset(
    {
        "candidate_snapshots",
        "fees",
        "lc0_binding",
        "p1_contract_sha256",
        "pit_inputs",
        "quantity",
        "research_contract_sha256",
        "rule_package_version",
        "schema_version",
        "signal",
    }
)


def _request_from_document(value: object) -> P1DecisionRequestV1:
    document = _exact_dict(
        value,
        _REQUEST_KEYS,
        P1_DECISION_CHILD_REQUEST_INVALID,
    )
    if document["schema_version"] != P1_DECISION_REQUEST_SCHEMA:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    candidates = document["candidate_snapshots"]
    if type(candidates) is not list or not 1 <= len(candidates) <= 64:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    try:
        result = P1DecisionRequestV1(
            signal=_flat_construct(
                document["signal"],
                SignalSnapshotV1,
                P1_DECISION_CHILD_REQUEST_INVALID,
            ),
            candidate_snapshots=tuple(
                _snapshot_from_document(item) for item in candidates
            ),
            pit_inputs=_flat_construct(
                document["pit_inputs"],
                CrrPitInputsV1,
                P1_DECISION_CHILD_REQUEST_INVALID,
            ),
            lc0_binding=_flat_construct(
                document["lc0_binding"],
                Lc0SelectionBindingV1,
                P1_DECISION_CHILD_REQUEST_INVALID,
            ),
            fees=_flat_construct(
                document["fees"],
                BcsFeeScheduleV1,
                P1_DECISION_CHILD_REQUEST_INVALID,
            ),
            research_contract_sha256=document["research_contract_sha256"],
            p1_contract_sha256=document["p1_contract_sha256"],
            rule_package_version=document["rule_package_version"],
            quantity=document["quantity"],
        )
    except (NormalizationError, P1NativeDecisionError, TypeError, ValueError) as error:
        if isinstance(error, P1DecisionChildError):
            raise
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID) from error
    if result.as_dict() != document:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    return result


def encode_p1_decision_request(
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
) -> tuple[bytes, str]:
    request = P1DecisionRequestV1(
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
    encoded = _canonical_json_bytes(request.as_dict())
    if len(encoded) > _REQUEST_MAX_BYTES:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    return encoded, sha256(encoded).hexdigest()


def decode_p1_decision_request(
    request_bytes: bytes,
    request_sha256: str,
) -> P1DecisionRequestV1:
    expected = _require_sha256(
        request_sha256,
        P1_DECISION_CHILD_REQUEST_INVALID,
    )
    if type(request_bytes) is not bytes or sha256(request_bytes).hexdigest() != expected:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    document = _parse_canonical_json_bytes(
        request_bytes,
        maximum=_REQUEST_MAX_BYTES,
    )
    request = _request_from_document(document)
    if _canonical_json_bytes(request.as_dict()) != request_bytes:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    return request


def _parent_input_rejection_binding(
    request_bytes: object,
) -> tuple[str, str]:
    actual_sha256 = (
        sha256(request_bytes).hexdigest()
        if type(request_bytes) is bytes
        else "0" * 64
    )
    supervisor_sha256 = _parent_input_rejection_sha256(actual_sha256)
    if supervisor_sha256 == actual_sha256:
        raise P1DecisionChildError(P1_DECISION_CHILD_EXECUTION_FAILED)
    return actual_sha256, supervisor_sha256


def _parent_input_rejection_sha256(request_sha256: object) -> str:
    inner_sha256 = _require_sha256(
        request_sha256,
        P1_DECISION_PARENT_VALIDATION_FAILED,
    )
    return sha256(
        b"GLD_P1_PARENT_INPUT_REJECTED_V1\n" + inner_sha256.encode("ascii")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class _ExpectedRequestLineageV1:
    request_binding: NativeDecisionRequestBindingV1
    selected_snapshot: OptionQuoteSnapshotV1
    ordered_call_bindings: tuple[tuple[str, str], ...]


def _expected_request_lineage(
    request: P1DecisionRequestV1,
) -> _ExpectedRequestLineageV1:
    if type(request) is not P1DecisionRequestV1 or request.signal.signal_state != "PASS":
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    try:
        ledger = build_option_snapshot_candidate_ledger(
            request.candidate_snapshots,
            signal_snapshot=request.signal,
        )
        selected = select_first_complete_option_snapshot(
            ledger,
            signal_snapshot=request.signal,
        )
        bound_inputs = bind_crr_call_inputs_bulk_from_snapshot(
            selected,
            contracts=tuple(
                quote.contract for quote in selected.option_quotes
            ),
            pit_inputs=request.pit_inputs,
        )
        input_vector_sha256 = sha256(
            _canonical_json_bytes(
                [item.input_sha256 for item in bound_inputs],
                newline=True,
            )
        ).hexdigest()
        binding = NativeDecisionRequestBindingV1(
            signal_snapshot_sha256=request.signal.snapshot_sha256,
            candidate_ledger_sha256=ledger.ledger_sha256,
            candidate_count=len(request.candidate_snapshots),
            selected_candidate_ordinal=selected.candidate_ordinal,
            selected_snapshot_sha256=selected.snapshot_sha256,
            pit_inputs_sha256=request.pit_inputs.pit_inputs_sha256,
            lc0_binding_sha256=request.lc0_binding.binding_sha256,
            fee_schedule_sha256=request.fees.fee_schedule_sha256,
            research_contract_sha256=request.research_contract_sha256,
            p1_contract_sha256=request.p1_contract_sha256,
            rule_package_version=request.rule_package_version,
            rule_sha256=request.signal.rule_sha256,
            quantity=request.quantity,
            call_input_vector_sha256=input_vector_sha256,
            delta_model_sha256=MODEL_SHA256,
            quote_quality_policy_version=QUOTE_QUALITY_POLICY_VERSION,
            quote_quality_policy_sha256=QUOTE_QUALITY_POLICY_SHA256,
        )
    except (AttributeError, NormalizationError, P1NativeDecisionError, TypeError, ValueError) as error:
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED) from error
    return _ExpectedRequestLineageV1(
        request_binding=binding,
        selected_snapshot=selected,
        ordered_call_bindings=tuple(
            (quote.contract.occ_symbol, inputs.input_sha256)
            for quote, inputs in zip(selected.option_quotes, bound_inputs)
        ),
    )


def _projection_matches_request(
    request: P1DecisionRequestV1,
    semantic: P1DecisionChildSemanticReceiptV1,
    expected: _ExpectedRequestLineageV1,
) -> bool:
    try:
        if (
            semantic.request_binding != expected.request_binding
            or tuple(
                (item.contract_id, item.input_sha256)
                for item in semantic.call_terminals
            )
            != expected.ordered_call_bindings
        ):
            return False
        snapshot = expected.selected_snapshot
        quote_by_id = {
            quote.contract.occ_symbol: quote for quote in snapshot.option_quotes
        }
        terminal_by_id = {
            item.contract_id: item for item in semantic.call_terminals
        }
        projection = semantic.decision_projection
        selection = projection.selection
        long_id = request.lc0_binding.long_call_id
        short_id = selection.short_call_id
        if (
            set(quote_by_id) != set(terminal_by_id)
            or selection.long_call_id != long_id
            or long_id not in quote_by_id
            or short_id not in quote_by_id
            or short_id == long_id
        ):
            return False
        long_quote = quote_by_id[long_id]
        short_quote = quote_by_id[short_id]
        long_terminal = terminal_by_id[long_id]
        short_terminal = terminal_by_id[short_id]
        long_contract = long_quote.contract
        short_contract = short_quote.contract

        def suite_winner(delta_field: str) -> str | None:
            long_delta = getattr(long_terminal, delta_field)
            if type(long_delta) is not int:
                return None
            eligible: list[tuple[int, int, str]] = []
            for contract_id, quote in quote_by_id.items():
                if contract_id == long_id:
                    continue
                terminal = terminal_by_id[contract_id]
                delta = getattr(terminal, delta_field)
                contract = quote.contract
                if (
                    terminal.terminal_status == "PASS"
                    and type(delta) is int
                    and 200_000 <= delta <= 300_000
                    and delta < long_delta
                    and contract.expiry_utc_ns == long_contract.expiry_utc_ns
                    and contract.strike_nano_usd > long_contract.strike_nano_usd
                ):
                    eligible.append(
                        (
                            abs(delta - 250_000),
                            -contract.strike_nano_usd,
                            contract_id,
                        )
                    )
            return None if not eligible else min(eligible)[2]

        long_book = long_quote.top_of_book
        short_book = short_quote.top_of_book
        fees = request.fees
        total_fees = (
            fees.long_entry_fee_nano_usd_per_contract
            + fees.short_entry_fee_nano_usd_per_contract
        )
        multiplier_quantity = long_contract.multiplier * request.quantity
        base_debit = (
            long_book.ask_nano_usd - short_book.bid_nano_usd
        ) * multiplier_quantity + total_fees
        stressed_debit = (
            long_book.ask_nano_usd
            + long_book.tick_nano_usd
            - max(0, short_book.bid_nano_usd - short_book.tick_nano_usd)
        ) * multiplier_quantity + total_fees
        width = (
            short_contract.strike_nano_usd - long_contract.strike_nano_usd
        ) * multiplier_quantity
        return (
            suite_winner("coarse_delta_ppm") == short_id
            and suite_winner("fine_delta_ppm") == short_id
            and selection.expiry_utc_ns == long_contract.expiry_utc_ns
            and selection.long_strike_nano_usd == long_contract.strike_nano_usd
            and selection.short_strike_nano_usd == short_contract.strike_nano_usd
            and selection.multiplier == long_contract.multiplier
            and selection.long_coarse_delta_ppm
            == long_terminal.coarse_delta_ppm
            and selection.long_delta_ppm == long_terminal.fine_delta_ppm
            and selection.short_coarse_delta_ppm
            == short_terminal.coarse_delta_ppm
            and selection.short_delta_ppm == short_terminal.fine_delta_ppm
            and selection.net_delta_ppm
            == long_terminal.fine_delta_ppm - short_terminal.fine_delta_ppm
            and projection.long_entry_quote.contract_id == long_id
            and projection.long_entry_quote.side == "ASK"
            and projection.long_entry_quote.price_nano_usd
            == long_book.ask_nano_usd
            and projection.long_entry_quote.displayed_size == long_book.ask_size
            and projection.long_entry_quote.tick_nano_usd
            == long_book.tick_nano_usd
            and projection.short_entry_quote.contract_id == short_id
            and projection.short_entry_quote.side == "BID"
            and projection.short_entry_quote.price_nano_usd
            == short_book.bid_nano_usd
            and projection.short_entry_quote.displayed_size == short_book.bid_size
            and projection.short_entry_quote.tick_nano_usd
            == short_book.tick_nano_usd
            and projection.entry_fees.long_entry_fee_nano_usd_per_contract
            == fees.long_entry_fee_nano_usd_per_contract
            and projection.entry_fees.short_entry_fee_nano_usd_per_contract
            == fees.short_entry_fee_nano_usd_per_contract
            and projection.entry_fees.total_entry_fees_nano_usd == total_fees
            and projection.entry_cost.quantity == request.quantity
            and projection.entry_cost.base_net_debit_nano_usd == base_debit
            and projection.entry_cost.stressed_net_debit_nano_usd
            == stressed_debit
            and projection.entry_cost.max_loss_nano_usd == base_debit
            and projection.entry_cost.gross_expiry_width_value_nano_usd == width
            and projection.entry_cost.theoretical_expiry_max_profit_nano_usd
            == width - base_debit
            and projection.entry_cost.theoretical_expiry_only is True
        )
    except Exception:
        return False


def _file_sha256(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID) from error
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class _PinnedNativeConfigV1:
    manifest_path: Path
    manifest_bytes: bytes
    native_manifest_sha256: str
    native_build_backend_evidence_sha256: str
    native_binary_sha256: str
    native_build_manifest_sha256: str
    binary_path: Path

    def as_dict(self) -> dict[str, object]:
        return {
            "manifest_path": str(self.manifest_path),
            "native_binary_sha256": self.native_binary_sha256,
            "native_build_backend_evidence_sha256": (
                self.native_build_backend_evidence_sha256
            ),
            "native_build_manifest_sha256": self.native_build_manifest_sha256,
            "native_manifest_sha256": self.native_manifest_sha256,
            "schema_version": P1_DECISION_CHILD_CONFIG_SCHEMA,
        }

    @property
    def config_sha256(self) -> str:
        return sha256(_canonical_json_bytes(self.as_dict())).hexdigest()

    def is_intact(self) -> bool:
        try:
            return (
                self.manifest_path.read_bytes() == self.manifest_bytes
                and sha256(self.manifest_bytes).hexdigest()
                == self.native_manifest_sha256
                and self.binary_path.is_file()
                and _file_sha256(self.binary_path) == self.native_binary_sha256
            )
        except Exception:
            return False


def _pin_native_config(
    native_manifest_path: Path,
    native_build_backend_evidence_sha256: str,
) -> _PinnedNativeConfigV1:
    backend = _require_sha256(
        native_build_backend_evidence_sha256,
        P1_DECISION_CHILD_CONFIG_INVALID,
    )
    try:
        manifest_path = Path(native_manifest_path).resolve(strict=True)
        raw = manifest_path.read_bytes()
    except (OSError, TypeError, ValueError) as error:
        raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID) from error
    if (
        not manifest_path.is_file()
        or len(raw) > _MANIFEST_MAX_BYTES
        or not str(manifest_path).isascii()
    ):
        raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
    try:
        manifest = _parse_canonical_json_bytes(
            raw,
            maximum=_MANIFEST_MAX_BYTES,
            newline=True,
        )
    except P1DecisionChildError as error:
        raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID) from error
    document = _exact_dict(
        manifest,
        _NATIVE_MANIFEST_KEYS,
        P1_DECISION_CHILD_CONFIG_INVALID,
    )
    if (
        document["schema_version"] != NATIVE_MANIFEST_SCHEMA
        or document["abi_id"] != NATIVE_ABI_ID
        or type(document["abi_version"]) is not int
        or document["abi_version"] != NATIVE_ABI_VERSION
        or document["backend_evidence_sha256"] != backend
        or not all(
            type(value) is str and _SHA256_RE.fullmatch(value) is not None
            for value in (
                document["binary_sha256"],
                document["build_manifest_sha256"],
                document["backend_evidence_sha256"],
            )
        )
        or type(document["binary_filename"]) is not str
        or "/" in document["binary_filename"]
        or "\\" in document["binary_filename"]
        or type(document["binary_size"]) is not int
        or document["binary_size"] <= 0
    ):
        raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
    try:
        binary_path = (
            manifest_path.parent / document["binary_filename"]
        ).resolve(strict=True)
        binary_valid = (
            binary_path.parent == manifest_path.parent.resolve(strict=True)
            and binary_path.stat().st_size == document["binary_size"]
            and _file_sha256(binary_path) == document["binary_sha256"]
        )
    except (OSError, TypeError, ValueError) as error:
        raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID) from error
    if not binary_valid:
        raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
    return _PinnedNativeConfigV1(
        manifest_path=manifest_path,
        manifest_bytes=raw,
        native_manifest_sha256=sha256(raw).hexdigest(),
        native_build_backend_evidence_sha256=backend,
        native_binary_sha256=document["binary_sha256"],
        native_build_manifest_sha256=document["build_manifest_sha256"],
        binary_path=binary_path,
    )


def _projection_from_document(value: object) -> NativeDecisionProjectionV1:
    document = _exact_dict(
        value,
        {
            "carrier_terminals",
            "decision",
            "entry_cost",
            "entry_fees",
            "entry_quotes",
            "selection",
            "selector_policy",
        },
        P1_DECISION_PARENT_VALIDATION_FAILED,
    )
    carriers = document["carrier_terminals"]
    quotes = _exact_dict(
        document["entry_quotes"],
        {"long", "short"},
        P1_DECISION_PARENT_VALIDATION_FAILED,
    )
    policy_document = dict(
        _exact_dict(
            document["selector_policy"],
            {
                "coarse_fine_winner_must_match",
                "short_delta_max_ppm",
                "short_delta_min_ppm",
                "short_delta_target_ppm",
                "winner_key_order",
            },
            P1_DECISION_PARENT_VALIDATION_FAILED,
        )
    )
    if (
        type(carriers) is not list
        or len(carriers) != 2
        or type(policy_document["winner_key_order"]) is not list
    ):
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    policy_document["winner_key_order"] = tuple(
        policy_document["winner_key_order"]
    )
    try:
        return NativeDecisionProjectionV1(
            carrier_terminals=tuple(
                _flat_construct(
                    item,
                    NativeCarrierTerminalV1,
                    P1_DECISION_PARENT_VALIDATION_FAILED,
                )
                for item in carriers
            ),
            decision=_flat_construct(
                document["decision"],
                NativeDecisionStateV1,
                P1_DECISION_PARENT_VALIDATION_FAILED,
            ),
            entry_cost=_flat_construct(
                document["entry_cost"],
                NativeDecisionEntryCostV1,
                P1_DECISION_PARENT_VALIDATION_FAILED,
            ),
            entry_fees=_flat_construct(
                document["entry_fees"],
                NativeDecisionEntryFeesV1,
                P1_DECISION_PARENT_VALIDATION_FAILED,
            ),
            long_entry_quote=_flat_construct(
                quotes["long"],
                NativeDecisionEntryQuoteV1,
                P1_DECISION_PARENT_VALIDATION_FAILED,
            ),
            short_entry_quote=_flat_construct(
                quotes["short"],
                NativeDecisionEntryQuoteV1,
                P1_DECISION_PARENT_VALIDATION_FAILED,
            ),
            selection=_flat_construct(
                document["selection"],
                NativeDecisionSelectionV1,
                P1_DECISION_PARENT_VALIDATION_FAILED,
            ),
            selector_policy=_flat_construct(
                policy_document,
                NativeDecisionSelectorPolicyV1,
                P1_DECISION_PARENT_VALIDATION_FAILED,
            ),
        )
    except (P1NativeDecisionError, TypeError, ValueError) as error:
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED) from error


@dataclass(frozen=True, slots=True)
class P1DecisionChildSemanticReceiptV1:
    supervisor_generation_token: str
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
            type(self.supervisor_generation_token) is not str
            or _TOKEN_RE.fullmatch(self.supervisor_generation_token) is None
        ):
            raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
        try:
            validated = NativeDecisionShadowReceiptV1(
                **{
                    item.name: getattr(self, item.name)
                    for item in fields(NativeDecisionShadowReceiptV1)
                }
            )
        except (P1NativeDecisionError, TypeError, ValueError) as error:
            raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED) from error
        native_document = self.as_dict()
        native_document.pop("supervisor_generation_token")
        if validated.as_dict() != native_document:
            raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)

    def as_dict(self) -> dict[str, object]:
        return {
            "actionable": self.actionable,
            "backend_evidence_components": self.backend_evidence_components.as_dict(),
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
            "supervisor_generation_token": self.supervisor_generation_token,
            "terminal": self.terminal,
            "worker_count": self.worker_count,
        }

    @property
    def semantic_receipt_sha256(self) -> str:
        return sha256(_canonical_json_bytes(self.as_dict())).hexdigest()


def _semantic_receipt_from_document(
    value: object,
) -> P1DecisionChildSemanticReceiptV1:
    keys = {item.name for item in fields(P1DecisionChildSemanticReceiptV1)}
    document = _exact_dict(
        value,
        keys,
        P1_DECISION_PARENT_VALIDATION_FAILED,
    )
    request_document = dict(
        _exact_dict(
            document["request_binding"],
            {item.name for item in fields(NativeDecisionRequestBindingV1)}
            | {"schema_version"},
            P1_DECISION_PARENT_VALIDATION_FAILED,
        )
    )
    if request_document.pop("schema_version") != (
        "GLD_CRR_P1_NATIVE_DECISION_REQUEST_BINDING_V1"
    ):
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    terminal_documents = document["call_terminals"]
    if type(terminal_documents) is not list or len(terminal_documents) != 64:
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    try:
        request_binding = _flat_construct(
            request_document,
            NativeDecisionRequestBindingV1,
            P1_DECISION_PARENT_VALIDATION_FAILED,
        )
        terminals = tuple(
            _flat_construct(
                item,
                CrrSemanticTerminalV1,
                P1_DECISION_PARENT_VALIDATION_FAILED,
            )
            for item in terminal_documents
        )
        projection = _projection_from_document(document["decision_projection"])
        components = _flat_construct(
            document["backend_evidence_components"],
            NativeDecisionBackendEvidenceComponentsV1,
            P1_DECISION_PARENT_VALIDATION_FAILED,
        )
        result = P1DecisionChildSemanticReceiptV1(
            supervisor_generation_token=document[
                "supervisor_generation_token"
            ],
            execution_status=document["execution_status"],
            p1_status=document["p1_status"],
            requested=document["requested"],
            bound=document["bound"],
            started=document["started"],
            terminal=document["terminal"],
            worker_count=document["worker_count"],
            kernel_threads=document["kernel_threads"],
            cache_hits=document["cache_hits"],
            request_binding=request_binding,
            request_sha256=document["request_sha256"],
            call_terminals=terminals,
            call_semantic_output_sha256=document["call_semantic_output_sha256"],
            decision_projection=projection,
            projection_sha256=document["projection_sha256"],
            runtime_semantic_sha256=document["runtime_semantic_sha256"],
            shadow_artifact_sha256=document["shadow_artifact_sha256"],
            batch_execution_provenance_sha256=document[
                "batch_execution_provenance_sha256"
            ],
            backend_evidence_components=components,
            actionable=document["actionable"],
            broker_order_count=document["broker_order_count"],
        )
    except (P1DecisionChildError, P1NativeDecisionError, TypeError, ValueError) as error:
        if isinstance(error, P1DecisionChildError):
            raise
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED) from error
    if result.as_dict() != document:
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    return result


@dataclass(frozen=True, slots=True)
class P1DecisionChildArtifactV1:
    decision_request_sha256: str
    supervisor_generation_token: str
    supervisor_request_sha256: str
    config_sha256: str
    native_manifest_sha256: str
    native_build_backend_evidence_sha256: str
    combined_backend_evidence_sha256: str
    semantic_receipt_sha256: str
    request_binding: NativeDecisionRequestBindingV1
    call_semantic_output_sha256: str
    decision_projection: NativeDecisionProjectionV1
    projection_sha256: str
    runtime_semantic_sha256: str
    backend_evidence_components: NativeDecisionBackendEvidenceComponentsV1
    decision_terminal: NativeDecisionStateV1
    execution_status: str
    p1_status: str
    actionable: bool
    broker_order_count: int
    qualification_status: str

    def __post_init__(self) -> None:
        if (
            type(self.supervisor_generation_token) is not str
            or _TOKEN_RE.fullmatch(self.supervisor_generation_token) is None
        ):
            raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
        for value in (
            self.decision_request_sha256,
            self.supervisor_request_sha256,
            self.config_sha256,
            self.native_manifest_sha256,
            self.native_build_backend_evidence_sha256,
            self.combined_backend_evidence_sha256,
            self.semantic_receipt_sha256,
            self.call_semantic_output_sha256,
            self.projection_sha256,
            self.runtime_semantic_sha256,
        ):
            _require_sha256(value, P1_DECISION_PARENT_VALIDATION_FAILED)
        if (
            type(self.request_binding) is not NativeDecisionRequestBindingV1
            or type(self.decision_projection) is not NativeDecisionProjectionV1
            or self.projection_sha256
            != self.decision_projection.projection_sha256
            or type(self.backend_evidence_components)
            is not NativeDecisionBackendEvidenceComponentsV1
            or self.combined_backend_evidence_sha256
            != self.backend_evidence_components.backend_evidence_sha256
            or type(self.decision_terminal) is not NativeDecisionStateV1
            or self.decision_terminal != self.decision_projection.decision
            or self.execution_status != NATIVE_DECISION_SHADOW_STATUS
            or self.p1_status != P1_QUALIFICATION_STATUS
            or self.actionable is not False
            or type(self.broker_order_count) is not int
            or self.broker_order_count != 0
            or self.qualification_status
            != P1_DECISION_CHILD_QUALIFICATION_STATUS
        ):
            raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)

    def as_dict(self) -> dict[str, object]:
        return {
            "actionable": self.actionable,
            "backend_evidence_components": self.backend_evidence_components.as_dict(),
            "broker_order_count": self.broker_order_count,
            "call_semantic_output_sha256": self.call_semantic_output_sha256,
            "combined_backend_evidence_sha256": (
                self.combined_backend_evidence_sha256
            ),
            "config_sha256": self.config_sha256,
            "decision_projection": self.decision_projection.as_dict(),
            "decision_request_sha256": self.decision_request_sha256,
            "decision_terminal": self.decision_terminal.as_dict(),
            "execution_status": self.execution_status,
            "native_build_backend_evidence_sha256": (
                self.native_build_backend_evidence_sha256
            ),
            "native_manifest_sha256": self.native_manifest_sha256,
            "p1_status": self.p1_status,
            "projection_sha256": self.projection_sha256,
            "qualification_status": self.qualification_status,
            "request_binding": self.request_binding.as_dict(),
            "runtime_semantic_sha256": self.runtime_semantic_sha256,
            "schema_version": P1_DECISION_CHILD_ARTIFACT_SCHEMA,
            "semantic_receipt_sha256": self.semantic_receipt_sha256,
            "supervisor_generation_token": self.supervisor_generation_token,
            "supervisor_request_sha256": self.supervisor_request_sha256,
        }

    @property
    def artifact_sha256(self) -> str:
        return sha256(_canonical_json_bytes(self.as_dict())).hexdigest()


def _semantic_artifact_lineage_valid(
    semantic: object,
    artifact: object,
    *,
    decision_request_sha256: str,
) -> bool:
    try:
        return (
            type(semantic) is P1DecisionChildSemanticReceiptV1
            and type(artifact) is P1DecisionChildArtifactV1
            and artifact.decision_request_sha256 == decision_request_sha256
            and artifact.supervisor_generation_token
            == semantic.supervisor_generation_token
            and artifact.semantic_receipt_sha256
            == semantic.semantic_receipt_sha256
            and artifact.request_binding == semantic.request_binding
            and artifact.call_semantic_output_sha256
            == semantic.call_semantic_output_sha256
            and artifact.decision_projection == semantic.decision_projection
            and artifact.projection_sha256 == semantic.projection_sha256
            and artifact.runtime_semantic_sha256
            == semantic.runtime_semantic_sha256
            and artifact.backend_evidence_components
            == semantic.backend_evidence_components
            and artifact.combined_backend_evidence_sha256
            == semantic.backend_evidence_components.backend_evidence_sha256
            and artifact.decision_terminal
            == semantic.decision_projection.decision
            and artifact.execution_status == semantic.execution_status
            and artifact.p1_status == semantic.p1_status
            and artifact.actionable is False
            and semantic.actionable is False
            and artifact.broker_order_count == 0
            and semantic.broker_order_count == 0
        )
    except Exception:
        return False


def _artifact_from_document(value: object) -> P1DecisionChildArtifactV1:
    keys = {item.name for item in fields(P1DecisionChildArtifactV1)} | {
        "schema_version"
    }
    document = dict(
        _exact_dict(
            value,
            keys,
            P1_DECISION_PARENT_VALIDATION_FAILED,
        )
    )
    if document.pop("schema_version") != P1_DECISION_CHILD_ARTIFACT_SCHEMA:
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    request_document = dict(
        _exact_dict(
            document["request_binding"],
            {item.name for item in fields(NativeDecisionRequestBindingV1)}
            | {"schema_version"},
            P1_DECISION_PARENT_VALIDATION_FAILED,
        )
    )
    if request_document.pop("schema_version") != (
        "GLD_CRR_P1_NATIVE_DECISION_REQUEST_BINDING_V1"
    ):
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    try:
        return P1DecisionChildArtifactV1(
            decision_request_sha256=document["decision_request_sha256"],
            supervisor_generation_token=document[
                "supervisor_generation_token"
            ],
            supervisor_request_sha256=document["supervisor_request_sha256"],
            config_sha256=document["config_sha256"],
            native_manifest_sha256=document["native_manifest_sha256"],
            native_build_backend_evidence_sha256=document[
                "native_build_backend_evidence_sha256"
            ],
            combined_backend_evidence_sha256=document[
                "combined_backend_evidence_sha256"
            ],
            semantic_receipt_sha256=document["semantic_receipt_sha256"],
            request_binding=_flat_construct(
                request_document,
                NativeDecisionRequestBindingV1,
                P1_DECISION_PARENT_VALIDATION_FAILED,
            ),
            call_semantic_output_sha256=document[
                "call_semantic_output_sha256"
            ],
            decision_projection=_projection_from_document(
                document["decision_projection"]
            ),
            projection_sha256=document["projection_sha256"],
            runtime_semantic_sha256=document["runtime_semantic_sha256"],
            backend_evidence_components=_flat_construct(
                document["backend_evidence_components"],
                NativeDecisionBackendEvidenceComponentsV1,
                P1_DECISION_PARENT_VALIDATION_FAILED,
            ),
            decision_terminal=_flat_construct(
                document["decision_terminal"],
                NativeDecisionStateV1,
                P1_DECISION_PARENT_VALIDATION_FAILED,
            ),
            execution_status=document["execution_status"],
            p1_status=document["p1_status"],
            actionable=document["actionable"],
            broker_order_count=document["broker_order_count"],
            qualification_status=document["qualification_status"],
        )
    except (P1DecisionChildError, P1NativeDecisionError, TypeError, ValueError) as error:
        if isinstance(error, P1DecisionChildError):
            raise
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED) from error


def _supervisor_receipt_is_exact(
    receipt: object,
    *,
    expected_request_sha256: str,
    expected_generation_token: str,
) -> bool:
    try:
        if type(receipt) is not P1ProcessSupervisorReceiptV1:
            return False
        reconstructed = P1ProcessSupervisorReceiptV1(
            **{
                item.name: getattr(receipt, item.name)
                for item in fields(P1ProcessSupervisorReceiptV1)
            }
        )
        return (
            reconstructed == receipt
            and receipt.request_sha256 == expected_request_sha256
            and receipt.generation_token == expected_generation_token
        )
    except Exception:
        return False


def _failure_state_is_valid(
    *,
    reason_code: object,
    failure_origin: object,
    child_reason_code: object,
    request_sha256: object,
    request_binding_kind: object,
    receipt: object,
    supervisor_request_sha256: object,
    supervisor_generation_token: object,
) -> bool:
    """Check only the public failure shape, never its causal provenance.

    The generic supervisor's one-shot issued capability is the authority for
    where a failure came from.  A public receipt cannot independently prove
    whether an otherwise identical no-output shape was INPUT, START,
    SUPERVISOR, or another internal branch, so this function deliberately does
    not try to reconstruct that private control flow.
    """

    try:
        if (
            type(reason_code) is not str
            or _REASON_RE.fullmatch(reason_code) is None
            or type(failure_origin) is not str
            or type(supervisor_request_sha256) is not str
            or _SHA256_RE.fullmatch(supervisor_request_sha256) is None
            or type(supervisor_generation_token) is not str
            or _TOKEN_RE.fullmatch(supervisor_generation_token) is None
            or type(request_sha256) is not str
            or _SHA256_RE.fullmatch(request_sha256) is None
            or request_binding_kind
            not in {_TYPED_REQUEST_BINDING, _REJECTED_INPUT_BINDING}
            or not _supervisor_receipt_is_exact(
                receipt,
                expected_request_sha256=supervisor_request_sha256,
                expected_generation_token=supervisor_generation_token,
            )
        ):
            return False
        if failure_origin == "PARENT_VALIDATION":
            return (
                request_binding_kind == _TYPED_REQUEST_BINDING
                and reason_code == P1_DECISION_PARENT_VALIDATION_FAILED
                and child_reason_code is None
                and supervisor_request_sha256
                != _parent_input_rejection_sha256(request_sha256)
            )
        if failure_origin == "WORKFLOW_DEADLINE":
            return (
                reason_code == P1_REASON_HARD_TIMEOUT
                and child_reason_code is None
                and (
                    (
                        request_binding_kind == _TYPED_REQUEST_BINDING
                        and supervisor_request_sha256
                        != _parent_input_rejection_sha256(request_sha256)
                    )
                    or (
                        request_binding_kind == _REJECTED_INPUT_BINDING
                        and supervisor_request_sha256
                        == _parent_input_rejection_sha256(request_sha256)
                    )
                )
            )
        if failure_origin not in _SUPERVISOR_FAILURE_ORIGINS:
            return False
        child_reason_shape = (
            type(child_reason_code) is str
            and _REASON_RE.fullmatch(child_reason_code) is not None
            if failure_origin == "CHILD_DECLARED"
            else child_reason_code is None
        )
        if not child_reason_shape:
            return False
        rejected_outer_sha256 = _parent_input_rejection_sha256(
            request_sha256
        )
        if request_binding_kind == _REJECTED_INPUT_BINDING:
            return (
                failure_origin == "INPUT"
                and reason_code == P1_REASON_INPUT_INVALID
                and supervisor_request_sha256 == rejected_outer_sha256
            )
        return (
            request_binding_kind == _TYPED_REQUEST_BINDING
            and supervisor_request_sha256 != rejected_outer_sha256
        )
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class P1DecisionWorkflowTimingReceiptV1:
    workflow_started_monotonic_ns: int
    deadline_monotonic_ns: int
    completed_monotonic_ns: int
    elapsed_ns: int
    before_deadline: bool
    decision_request_sha256: str
    supervisor_request_sha256: str
    supervisor_generation_token: str
    actionable: bool
    broker_order_count: int

    def __post_init__(self) -> None:
        for value in (
            self.workflow_started_monotonic_ns,
            self.deadline_monotonic_ns,
            self.completed_monotonic_ns,
            self.elapsed_ns,
        ):
            if type(value) is not int or value < 0:
                raise P1DecisionChildError(
                    P1_DECISION_PARENT_VALIDATION_FAILED
                )
        for value in (
            self.decision_request_sha256,
            self.supervisor_request_sha256,
        ):
            _require_sha256(value, P1_DECISION_PARENT_VALIDATION_FAILED)
        if (
            type(self.supervisor_generation_token) is not str
            or _TOKEN_RE.fullmatch(self.supervisor_generation_token) is None
            or self.deadline_monotonic_ns
            != self.workflow_started_monotonic_ns
            + _WORKFLOW_FIXED_TIMEOUT_NS
            or self.completed_monotonic_ns
            < self.workflow_started_monotonic_ns
            or self.elapsed_ns
            != self.completed_monotonic_ns
            - self.workflow_started_monotonic_ns
            or type(self.before_deadline) is not bool
            or self.before_deadline
            != (self.completed_monotonic_ns < self.deadline_monotonic_ns)
            or self.actionable is not False
            or type(self.broker_order_count) is not int
            or self.broker_order_count != 0
        ):
            raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)

    def as_dict(self) -> dict[str, object]:
        return {
            "actionable": self.actionable,
            "before_deadline": self.before_deadline,
            "broker_order_count": self.broker_order_count,
            "completed_monotonic_ns": self.completed_monotonic_ns,
            "deadline_monotonic_ns": self.deadline_monotonic_ns,
            "decision_request_sha256": self.decision_request_sha256,
            "elapsed_ns": self.elapsed_ns,
            "schema_version": P1_DECISION_WORKFLOW_TIMING_SCHEMA,
            "supervisor_generation_token": self.supervisor_generation_token,
            "supervisor_request_sha256": self.supervisor_request_sha256,
            "workflow_started_monotonic_ns": (
                self.workflow_started_monotonic_ns
            ),
        }

    @property
    def timing_receipt_sha256(self) -> str:
        return sha256(_canonical_json_bytes(self.as_dict())).hexdigest()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class P1DecisionChildIntegrationResultV1:
    status: str
    reason_code: str
    failure_origin: str
    child_reason_code: str | None
    request_sha256: str
    request_binding_kind: str
    supervisor_request_sha256: str
    supervisor_generation_token: str
    semantic_receipt: P1DecisionChildSemanticReceiptV1 | None
    artifact: P1DecisionChildArtifactV1 | None
    artifact_sha256: str | None
    supervisor_receipt: P1ProcessSupervisorReceiptV1
    workflow_timing: P1DecisionWorkflowTimingReceiptV1 | None
    actionable: bool
    broker_order_count: int
    qualification_status: str

    def __post_init__(self) -> None:
        _require_sha256(self.request_sha256, P1_DECISION_PARENT_VALIDATION_FAILED)
        _require_sha256(
            self.supervisor_request_sha256,
            P1_DECISION_PARENT_VALIDATION_FAILED,
        )
        if (
            self.status not in {"SUCCESS", "FAIL_CLOSED"}
            or type(self.reason_code) is not str
            or _REASON_RE.fullmatch(self.reason_code) is None
            or type(self.failure_origin) is not str
            or type(self.supervisor_receipt) is not P1ProcessSupervisorReceiptV1
            or self.actionable is not False
            or type(self.broker_order_count) is not int
            or self.broker_order_count != 0
            or self.qualification_status
            != P1_DECISION_CHILD_QUALIFICATION_STATUS
            or self.request_binding_kind
            not in {_TYPED_REQUEST_BINDING, _REJECTED_INPUT_BINDING}
            or type(self.supervisor_generation_token) is not str
            or _TOKEN_RE.fullmatch(self.supervisor_generation_token) is None
            or not _supervisor_receipt_is_exact(
                self.supervisor_receipt,
                expected_request_sha256=self.supervisor_request_sha256,
                expected_generation_token=self.supervisor_generation_token,
            )
        ):
            raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
        if self.status == "SUCCESS":
            if (
                self.reason_code != P1_DECISION_CHILD_SUCCESS
                or self.failure_origin != "NONE"
                or self.request_binding_kind != _TYPED_REQUEST_BINDING
                or self.child_reason_code is not None
                or type(self.semantic_receipt)
                is not P1DecisionChildSemanticReceiptV1
                or type(self.artifact) is not P1DecisionChildArtifactV1
                or self.artifact_sha256 != self.artifact.artifact_sha256
                or not _semantic_artifact_lineage_valid(
                    self.semantic_receipt,
                    self.artifact,
                    decision_request_sha256=self.request_sha256,
                )
                or self.artifact.supervisor_request_sha256
                != self.supervisor_receipt.request_sha256
                or self.semantic_receipt.supervisor_generation_token
                != self.supervisor_generation_token
                or self.artifact.supervisor_generation_token
                != self.supervisor_generation_token
                or (
                    self.workflow_timing is not None
                    and not self.workflow_timing.before_deadline
                )
            ):
                raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
        else:
            if (
                self.semantic_receipt is not None
                or self.artifact is not None
                or self.artifact_sha256 is not None
                or not _failure_state_is_valid(
                    reason_code=self.reason_code,
                    failure_origin=self.failure_origin,
                    child_reason_code=self.child_reason_code,
                    request_sha256=self.request_sha256,
                    request_binding_kind=self.request_binding_kind,
                    receipt=self.supervisor_receipt,
                    supervisor_request_sha256=self.supervisor_request_sha256,
                    supervisor_generation_token=(
                        self.supervisor_generation_token
                    ),
                )
            ):
                raise P1DecisionChildError(
                    P1_DECISION_PARENT_VALIDATION_FAILED
                )
        timing = self.workflow_timing
        if timing is not None and (
            type(timing) is not P1DecisionWorkflowTimingReceiptV1
            or timing.decision_request_sha256 != self.request_sha256
            or timing.supervisor_request_sha256
            != self.supervisor_request_sha256
            or timing.supervisor_generation_token
            != self.supervisor_generation_token
            or timing.workflow_started_monotonic_ns
            != self.supervisor_receipt.supervisor_started_monotonic_ns
            or timing.deadline_monotonic_ns
            != self.supervisor_receipt.deadline_monotonic_ns
            or timing.completed_monotonic_ns
            < self.supervisor_receipt.completed_monotonic_ns
            or timing.actionable is not False
            or timing.broker_order_count != 0
            or (
                self.failure_origin == "WORKFLOW_DEADLINE"
                and (
                    timing.before_deadline
                    or self.status != "FAIL_CLOSED"
                    or self.reason_code != P1_REASON_HARD_TIMEOUT
                )
            )
            or (
                self.failure_origin != "WORKFLOW_DEADLINE"
                and not timing.before_deadline
            )
        ):
            raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)


def _deadline_guard(deadline_monotonic_ns: object) -> None:
    if (
        type(deadline_monotonic_ns) is not int
        or time.monotonic_ns() >= deadline_monotonic_ns
    ):
        raise P1DecisionChildError(P1_DECISION_CHILD_DEADLINE_REACHED)


def _outer_envelope(
    request: P1DecisionRequestV1,
    pinned: _PinnedNativeConfigV1,
) -> tuple[bytes, str]:
    document = {
        "config": pinned.as_dict(),
        "config_sha256": pinned.config_sha256,
        "decision_request": request.as_dict(),
        "decision_request_sha256": request.request_sha256,
        "schema_version": P1_DECISION_CHILD_ENVELOPE_SCHEMA,
    }
    encoded = _canonical_json_bytes(document)
    if len(encoded) > P1_MAX_REQUEST_BYTES:
        raise P1DecisionChildError(P1_DECISION_CHILD_REQUEST_INVALID)
    return encoded, sha256(encoded).hexdigest()


def _decode_outer_envelope(
    request_bytes: bytes,
    request_sha256: str,
) -> tuple[P1DecisionRequestV1, _PinnedNativeConfigV1, str]:
    outer_sha256 = _require_sha256(
        request_sha256,
        P1_DECISION_CHILD_CONFIG_INVALID,
    )
    if type(request_bytes) is not bytes or sha256(request_bytes).hexdigest() != outer_sha256:
        raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
    value = _parse_canonical_json_bytes(
        request_bytes,
        maximum=P1_MAX_REQUEST_BYTES,
    )
    document = _exact_dict(
        value,
        {
            "config",
            "config_sha256",
            "decision_request",
            "decision_request_sha256",
            "schema_version",
        },
        P1_DECISION_CHILD_CONFIG_INVALID,
    )
    if document["schema_version"] != P1_DECISION_CHILD_ENVELOPE_SCHEMA:
        raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
    config = _exact_dict(
        document["config"],
        {
            "manifest_path",
            "native_binary_sha256",
            "native_build_backend_evidence_sha256",
            "native_build_manifest_sha256",
            "native_manifest_sha256",
            "schema_version",
        },
        P1_DECISION_CHILD_CONFIG_INVALID,
    )
    if (
        config["schema_version"] != P1_DECISION_CHILD_CONFIG_SCHEMA
        or sha256(_canonical_json_bytes(config)).hexdigest()
        != document["config_sha256"]
        or type(config["manifest_path"]) is not str
    ):
        raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
    pinned = _pin_native_config(
        Path(config["manifest_path"]),
        config["native_build_backend_evidence_sha256"],
    )
    if pinned.as_dict() != config or pinned.config_sha256 != document["config_sha256"]:
        raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
    inner_bytes = _canonical_json_bytes(document["decision_request"])
    inner_sha256 = _require_sha256(
        document["decision_request_sha256"],
        P1_DECISION_CHILD_REQUEST_INVALID,
    )
    request = decode_p1_decision_request(inner_bytes, inner_sha256)
    return request, pinned, outer_sha256


def _artifact_for_success(
    receipt: NativeDecisionShadowReceiptV1,
    *,
    decision_request_sha256: str,
    supervisor_generation_token: str,
    supervisor_request_sha256: str,
    semantic_receipt_document: Mapping[str, object],
    pinned: _PinnedNativeConfigV1,
) -> P1DecisionChildArtifactV1:
    return P1DecisionChildArtifactV1(
        decision_request_sha256=decision_request_sha256,
        supervisor_generation_token=supervisor_generation_token,
        supervisor_request_sha256=supervisor_request_sha256,
        config_sha256=pinned.config_sha256,
        native_manifest_sha256=pinned.native_manifest_sha256,
        native_build_backend_evidence_sha256=(
            pinned.native_build_backend_evidence_sha256
        ),
        combined_backend_evidence_sha256=(
            receipt.backend_evidence_components.backend_evidence_sha256
        ),
        semantic_receipt_sha256=sha256(
            _canonical_json_bytes(dict(semantic_receipt_document))
        ).hexdigest(),
        request_binding=receipt.request_binding,
        call_semantic_output_sha256=receipt.call_semantic_output_sha256,
        decision_projection=receipt.decision_projection,
        projection_sha256=receipt.projection_sha256,
        runtime_semantic_sha256=receipt.runtime_semantic_sha256,
        backend_evidence_components=receipt.backend_evidence_components,
        decision_terminal=receipt.decision_projection.decision,
        execution_status=receipt.execution_status,
        p1_status=receipt.p1_status,
        actionable=False,
        broker_order_count=0,
        qualification_status=P1_DECISION_CHILD_QUALIFICATION_STATUS,
    )


def _frame(
    generation_token: str,
    supervisor_request_sha256: str,
    *,
    outcome: str,
    reason_code: str,
    semantic_receipt: Mapping[str, object] | None = None,
    artifact: Mapping[str, object] | None = None,
) -> bytes:
    artifact_sha256 = (
        None
        if artifact is None
        else sha256(_canonical_json_bytes(dict(artifact))).hexdigest()
    )
    return _canonical_json_bytes(
        {
            "actionable": False,
            "artifact": None if artifact is None else dict(artifact),
            "artifact_sha256": artifact_sha256,
            "broker_order_count": 0,
            "generation_token": generation_token,
            "outcome": outcome,
            "reason_code": reason_code,
            "request_sha256": supervisor_request_sha256,
            "schema_version": P1_CHILD_FRAME_SCHEMA,
            "semantic_receipt": (
                None if semantic_receipt is None else dict(semantic_receipt)
            ),
        }
    )


def _production_decision_child(
    send_connection: Connection,
    generation_token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_monotonic_ns: int,
) -> None:
    """One spawn-safe production target with exactly one send attempt."""

    payload: bytes
    try:
        if (
            type(generation_token) is not str
            or _TOKEN_RE.fullmatch(generation_token) is None
        ):
            raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
        _deadline_guard(deadline_monotonic_ns)
        request, pinned, supervisor_request_sha256 = _decode_outer_envelope(
            request_bytes,
            request_sha256,
        )
        _deadline_guard(deadline_monotonic_ns)
        kernel = load_native_tree_v1(
            pinned.manifest_path,
            expected_backend_evidence_sha256=(
                pinned.native_build_backend_evidence_sha256
            ),
        )
        _deadline_guard(deadline_monotonic_ns)
        compute, verify_call, combined_backend = create_native_crr_delta_engine(
            kernel
        )
        run, verify_receipt, verify_failure = (
            create_p1_native_decision_shadow_engine(
                compute_call=compute,
                verify_call=verify_call,
                combined_backend_evidence_sha256=combined_backend,
            )
        )
        _deadline_guard(deadline_monotonic_ns)
        result = run(
            signal=request.signal,
            candidate_snapshots=request.candidate_snapshots,
            pit_inputs=request.pit_inputs,
            lc0_binding=request.lc0_binding,
            fees=request.fees,
            research_contract_sha256=request.research_contract_sha256,
            p1_contract_sha256=request.p1_contract_sha256,
            rule_package_version=request.rule_package_version,
            quantity=request.quantity,
        )
        _deadline_guard(deadline_monotonic_ns)
        if type(result) is NativeDecisionShadowReceiptV1:
            if verify_receipt(result) is not True:
                raise P1DecisionChildError(P1_DECISION_CHILD_EXECUTION_FAILED)
            semantic_document = {
                "supervisor_generation_token": generation_token,
                **result.as_dict(),
            }
            artifact = _artifact_for_success(
                result,
                decision_request_sha256=request.request_sha256,
                supervisor_generation_token=generation_token,
                supervisor_request_sha256=supervisor_request_sha256,
                semantic_receipt_document=semantic_document,
                pinned=pinned,
            )
            payload = _frame(
                generation_token,
                supervisor_request_sha256,
                outcome="SUCCESS",
                reason_code=P1_DECISION_CHILD_SUCCESS,
                semantic_receipt=semantic_document,
                artifact=artifact.as_dict(),
            )
        elif type(result) is NativeDecisionShadowFailureV1:
            if verify_failure(result) is not True:
                raise P1DecisionChildError(P1_DECISION_CHILD_EXECUTION_FAILED)
            payload = _frame(
                generation_token,
                supervisor_request_sha256,
                outcome="FAIL",
                reason_code=result.reason_code,
            )
        else:
            raise P1DecisionChildError(P1_DECISION_CHILD_EXECUTION_FAILED)
        _deadline_guard(deadline_monotonic_ns)
    except (NormalizationError, P1NativeDecisionError) as error:
        reason = getattr(error, "reason_code", P1_DECISION_CHILD_EXECUTION_FAILED)
        if type(reason) is not str or _REASON_RE.fullmatch(reason) is None:
            reason = P1_DECISION_CHILD_EXECUTION_FAILED
        payload = _frame(
            generation_token,
            request_sha256,
            outcome="FAIL",
            reason_code=reason,
        )
    except P1DecisionChildError as error:
        payload = _frame(
            generation_token,
            request_sha256,
            outcome="FAIL",
            reason_code=error.reason_code,
        )
    except Exception:
        payload = _frame(
            generation_token,
            request_sha256,
            outcome="FAIL",
            reason_code=P1_DECISION_CHILD_EXECUTION_FAILED,
        )
    try:
        send_connection.send_bytes(payload)
    finally:
        send_connection.close()


def _semantic_and_artifact_from_supervisor(
    supervisor_result: P1ProcessSupervisorResultV1,
    *,
    request: P1DecisionRequestV1,
    pinned: _PinnedNativeConfigV1,
) -> tuple[P1DecisionChildSemanticReceiptV1, P1DecisionChildArtifactV1]:
    if (
        type(supervisor_result) is not P1ProcessSupervisorResultV1
        or supervisor_result.status != "SUCCESS"
        or supervisor_result.reason_code != P1_REASON_SUCCESS
        or type(supervisor_result.semantic_receipt_bytes) is not bytes
        or type(supervisor_result.artifact_bytes) is not bytes
        or type(supervisor_result.artifact_sha256) is not str
    ):
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    semantic_document = _parse_canonical_json_bytes(
        supervisor_result.semantic_receipt_bytes,
        maximum=P1_MAX_REQUEST_BYTES,
    )
    artifact_document = _parse_canonical_json_bytes(
        supervisor_result.artifact_bytes,
        maximum=P1_MAX_REQUEST_BYTES,
    )
    semantic = _semantic_receipt_from_document(semantic_document)
    artifact = _artifact_from_document(artifact_document)
    expected_lineage = _expected_request_lineage(request)
    if (
        artifact.artifact_sha256 != supervisor_result.artifact_sha256
        or artifact.decision_request_sha256 != request.request_sha256
        or artifact.supervisor_request_sha256
        != supervisor_result.supervisor_receipt.request_sha256
        or artifact.config_sha256 != pinned.config_sha256
        or artifact.native_manifest_sha256 != pinned.native_manifest_sha256
        or artifact.native_build_backend_evidence_sha256
        != pinned.native_build_backend_evidence_sha256
        or semantic.supervisor_generation_token
        != supervisor_result.supervisor_receipt.generation_token
        or artifact.supervisor_generation_token
        != supervisor_result.supervisor_receipt.generation_token
        or not _semantic_artifact_lineage_valid(
            semantic,
            artifact,
            decision_request_sha256=request.request_sha256,
        )
        or not _projection_matches_request(
            request,
            semantic,
            expected_lineage,
        )
    ):
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    return semantic, artifact


def _supervisor_result_is_exact(
    result: object,
    *,
    expected_supervisor_request_sha256: str,
) -> bool:
    try:
        if type(result) is not P1ProcessSupervisorResultV1:
            return False
        reconstructed = P1ProcessSupervisorResultV1(
            **{
                item.name: getattr(result, item.name)
                for item in fields(P1ProcessSupervisorResultV1)
            }
        )
        return (
            reconstructed == result
            and _supervisor_receipt_is_exact(
                result.supervisor_receipt,
                expected_request_sha256=expected_supervisor_request_sha256,
                expected_generation_token=(
                    result.supervisor_receipt.generation_token
                ),
            )
        )
    except Exception:
        return False


def _failure_integration_result(
    supervisor_result: P1ProcessSupervisorResultV1,
    *,
    request_sha256: str,
    expected_supervisor_request_sha256: str,
    request_binding_kind: str = _TYPED_REQUEST_BINDING,
    reason_code: str | None = None,
    failure_origin: str | None = None,
) -> P1DecisionChildIntegrationResultV1:
    if not _supervisor_result_is_exact(
        supervisor_result,
        expected_supervisor_request_sha256=(
            expected_supervisor_request_sha256
        ),
    ):
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    if (reason_code is None) != (failure_origin is None):
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    if reason_code is not None and (
        supervisor_result.status != "SUCCESS"
        or reason_code != P1_DECISION_PARENT_VALIDATION_FAILED
        or failure_origin != "PARENT_VALIDATION"
    ):
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    return P1DecisionChildIntegrationResultV1(
        status="FAIL_CLOSED",
        reason_code=(
            supervisor_result.reason_code if reason_code is None else reason_code
        ),
        failure_origin=(
            supervisor_result.failure_origin
            if failure_origin is None
            else failure_origin
        ),
        child_reason_code=supervisor_result.child_reason_code,
        request_sha256=request_sha256,
        request_binding_kind=request_binding_kind,
        supervisor_request_sha256=(
            supervisor_result.supervisor_receipt.request_sha256
        ),
        supervisor_generation_token=(
            supervisor_result.supervisor_receipt.generation_token
        ),
        semantic_receipt=None,
        artifact=None,
        artifact_sha256=None,
        supervisor_receipt=supervisor_result.supervisor_receipt,
        workflow_timing=None,
        actionable=False,
        broker_order_count=0,
        qualification_status=P1_DECISION_CHILD_QUALIFICATION_STATUS,
    )


def _workflow_timing_receipt_from_finalization(
    finalization: object,
    *,
    candidate: P1DecisionChildIntegrationResultV1,
) -> P1DecisionWorkflowTimingReceiptV1:
    if (
        type(finalization) is not _P1WorkflowTimingFinalizationV1
        or type(candidate) is not P1DecisionChildIntegrationResultV1
        or candidate.workflow_timing is not None
    ):
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    return P1DecisionWorkflowTimingReceiptV1(
        workflow_started_monotonic_ns=(
            finalization.workflow_started_monotonic_ns
        ),
        deadline_monotonic_ns=finalization.deadline_monotonic_ns,
        completed_monotonic_ns=finalization.completed_monotonic_ns,
        elapsed_ns=(
            finalization.completed_monotonic_ns
            - finalization.workflow_started_monotonic_ns
        ),
        before_deadline=finalization.before_deadline,
        decision_request_sha256=candidate.request_sha256,
        supervisor_request_sha256=candidate.supervisor_request_sha256,
        supervisor_generation_token=candidate.supervisor_generation_token,
        actionable=False,
        broker_order_count=0,
    )


def _publish_finalized_candidate(
    candidate: P1DecisionChildIntegrationResultV1,
    timing: P1DecisionWorkflowTimingReceiptV1,
) -> P1DecisionChildIntegrationResultV1:
    if (
        type(candidate) is not P1DecisionChildIntegrationResultV1
        or candidate.workflow_timing is not None
        or type(timing) is not P1DecisionWorkflowTimingReceiptV1
        or timing.decision_request_sha256 != candidate.request_sha256
        or timing.supervisor_request_sha256
        != candidate.supervisor_request_sha256
        or timing.supervisor_generation_token
        != candidate.supervisor_generation_token
    ):
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    if not timing.before_deadline:
        return P1DecisionChildIntegrationResultV1(
            status="FAIL_CLOSED",
            reason_code=P1_REASON_HARD_TIMEOUT,
            failure_origin="WORKFLOW_DEADLINE",
            child_reason_code=None,
            request_sha256=candidate.request_sha256,
            request_binding_kind=candidate.request_binding_kind,
            supervisor_request_sha256=(
                candidate.supervisor_request_sha256
            ),
            supervisor_generation_token=(
                candidate.supervisor_generation_token
            ),
            semantic_receipt=None,
            artifact=None,
            artifact_sha256=None,
            supervisor_receipt=candidate.supervisor_receipt,
            workflow_timing=timing,
            actionable=False,
            broker_order_count=0,
            qualification_status=P1_DECISION_CHILD_QUALIFICATION_STATUS,
        )
    values = {
        item.name: getattr(candidate, item.name)
        for item in fields(P1DecisionChildIntegrationResultV1)
    }
    values["workflow_timing"] = timing
    return P1DecisionChildIntegrationResultV1(**values)


def _accept_supervisor_result(
    supervisor_result: P1ProcessSupervisorResultV1,
    *,
    request: P1DecisionRequestV1,
    pinned: _PinnedNativeConfigV1,
    expected_supervisor_request_sha256: str,
) -> P1DecisionChildIntegrationResultV1:
    if not _supervisor_result_is_exact(
        supervisor_result,
        expected_supervisor_request_sha256=(
            expected_supervisor_request_sha256
        ),
    ):
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    if supervisor_result.status != "SUCCESS":
        return _failure_integration_result(
            supervisor_result,
            request_sha256=request.request_sha256,
            expected_supervisor_request_sha256=(
                expected_supervisor_request_sha256
            ),
        )
    try:
        semantic, artifact = _semantic_and_artifact_from_supervisor(
            supervisor_result,
            request=request,
            pinned=pinned,
        )
    except Exception:
        return _failure_integration_result(
            supervisor_result,
            request_sha256=request.request_sha256,
            expected_supervisor_request_sha256=(
                expected_supervisor_request_sha256
            ),
            reason_code=P1_DECISION_PARENT_VALIDATION_FAILED,
            failure_origin="PARENT_VALIDATION",
        )
    return P1DecisionChildIntegrationResultV1(
        status="SUCCESS",
        reason_code=P1_DECISION_CHILD_SUCCESS,
        failure_origin="NONE",
        child_reason_code=None,
        request_sha256=request.request_sha256,
        request_binding_kind=_TYPED_REQUEST_BINDING,
        supervisor_request_sha256=(
            supervisor_result.supervisor_receipt.request_sha256
        ),
        supervisor_generation_token=(
            supervisor_result.supervisor_receipt.generation_token
        ),
        semantic_receipt=semantic,
        artifact=artifact,
        artifact_sha256=artifact.artifact_sha256,
        supervisor_receipt=supervisor_result.supervisor_receipt,
        workflow_timing=None,
        actionable=False,
        broker_order_count=0,
        qualification_status=P1_DECISION_CHILD_QUALIFICATION_STATUS,
    )


def _validate_p1_decision_child_result(
    result: object,
    expected_request: object,
    expected_backend_sha256: str,
    *,
    expected_config_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
    expected_supervisor_request_sha256: str | None = None,
    expected_pinned_config: object = None,
) -> bool:
    """Validate a typed result's public structure, not its origin.

    This compatibility helper cannot establish that the supervisor actually
    emitted ``result``.  Callers that need causal provenance must use
    ``runner.is_verified_result(...)`` on the exact runner that returned it.
    """

    try:
        if (
            type(result) is not P1DecisionChildIntegrationResultV1
            or type(expected_request) is not P1DecisionRequestV1
            or type(result.workflow_timing)
            is not P1DecisionWorkflowTimingReceiptV1
            or result.request_sha256 != expected_request.request_sha256
            or result.request_binding_kind != _TYPED_REQUEST_BINDING
            or type(expected_pinned_config) is not _PinnedNativeConfigV1
            or not expected_pinned_config.is_intact()
        ):
            return False
        derived_outer_sha256 = _outer_envelope(
            expected_request,
            expected_pinned_config,
        )[1]
        if (
            expected_supervisor_request_sha256 != derived_outer_sha256
            or expected_pinned_config.native_build_backend_evidence_sha256
            != expected_backend_sha256
        ):
            return False
        reconstructed = P1DecisionChildIntegrationResultV1(
            **{
                item.name: getattr(result, item.name)
                for item in fields(P1DecisionChildIntegrationResultV1)
            }
        )
        if reconstructed != result:
            return False
        if result.status == "SUCCESS":
            if (
                _SHA256_RE.fullmatch(expected_config_sha256 or "") is None
                or _SHA256_RE.fullmatch(expected_manifest_sha256 or "") is None
                or _SHA256_RE.fullmatch(
                    expected_supervisor_request_sha256 or ""
                )
                is None
            ):
                return False
            expected_lineage = _expected_request_lineage(expected_request)
            return (
                result.artifact is not None
                and result.semantic_receipt is not None
                and result.artifact.config_sha256
                == expected_pinned_config.config_sha256
                and result.artifact.native_manifest_sha256
                == expected_pinned_config.native_manifest_sha256
                and result.artifact.native_build_backend_evidence_sha256
                == expected_backend_sha256
                and result.artifact.config_sha256 == expected_config_sha256
                and result.artifact.native_manifest_sha256
                == expected_manifest_sha256
                and result.artifact.supervisor_request_sha256
                == expected_supervisor_request_sha256
                and result.supervisor_receipt.request_sha256
                == expected_supervisor_request_sha256
                and _semantic_artifact_lineage_valid(
                    result.semantic_receipt,
                    result.artifact,
                    decision_request_sha256=expected_request.request_sha256,
                )
                and _projection_matches_request(
                    expected_request,
                    result.semantic_receipt,
                    expected_lineage,
                )
                and result.artifact.artifact_sha256 == result.artifact_sha256
            )
        return (
            _SHA256_RE.fullmatch(
                expected_supervisor_request_sha256 or ""
            )
            is not None
            and result.supervisor_request_sha256
            == expected_supervisor_request_sha256
            and result.supervisor_receipt.request_sha256
            == expected_supervisor_request_sha256
            and result.supervisor_generation_token
            == result.supervisor_receipt.generation_token
            and result.semantic_receipt is None
            and result.artifact is None
            and result.artifact_sha256 is None
            and _failure_state_is_valid(
                reason_code=result.reason_code,
                failure_origin=result.failure_origin,
                child_reason_code=result.child_reason_code,
                request_sha256=result.request_sha256,
                request_binding_kind=result.request_binding_kind,
                receipt=result.supervisor_receipt,
                supervisor_request_sha256=(
                    result.supervisor_request_sha256
                ),
                supervisor_generation_token=(
                    result.supervisor_generation_token
                ),
            )
        )
    except Exception:
        return False


def _integration_result_identity_snapshot(
    value: object,
    *,
    depth: int = 0,
    nodes: list[int] | None = None,
) -> tuple[object, ...]:
    """Take an exact immutable snapshot for process-local identity evidence."""

    count = [0] if nodes is None else nodes
    if depth > _MAX_DEPTH:
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    count[0] += 1
    if count[0] > _MAX_NODES:
        raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
    if value is None or type(value) in {str, int, bool, bytes}:
        return (type(value), id(value), value)
    if type(value) is tuple:
        return (
            tuple,
            id(value),
            tuple(
                _integration_result_identity_snapshot(
                    item,
                    depth=depth + 1,
                    nodes=count,
                )
                for item in value
            ),
        )
    try:
        value_fields = fields(value)
    except (TypeError, ValueError) as error:
        raise P1DecisionChildError(
            P1_DECISION_PARENT_VALIDATION_FAILED
        ) from error
    return (
        type(value),
        id(value),
        tuple(
            (
                item.name,
                _integration_result_identity_snapshot(
                    getattr(value, item.name),
                    depth=depth + 1,
                    nodes=count,
                ),
            )
            for item in value_fields
        ),
    )


@dataclass(frozen=True, slots=True)
class _P1DecisionChildResultRegistryEntryV1:
    result_reference: weakref.ReferenceType[P1DecisionChildIntegrationResultV1]
    result_snapshot: tuple[object, ...]
    input_bytes_sha256: str
    supplied_request_sha256: str
    decision_request_sha256: str
    supervisor_request_sha256: str
    supervisor_generation_token: str
    runner_binding: object


def _make_bound_pipeline(
    *,
    pinned: _PinnedNativeConfigV1,
    run_issued: Callable[[object, bytes, str], object | None],
    consume_issued: Callable[
        [object, object, str],
        P1ProcessSupervisorResultV1 | None,
    ],
    finalize_workflow: Callable[[object, str], object | None],
    integrity_is_valid: Callable[[], bool],
    config_intact: Callable[[_PinnedNativeConfigV1], bool],
    decode_request: Callable[[bytes, str], P1DecisionRequestV1],
    encode_outer_envelope: Callable[..., tuple[bytes, str]],
    accept_supervisor_result: Callable[..., P1DecisionChildIntegrationResultV1],
    failure_result: Callable[..., P1DecisionChildIntegrationResultV1],
    timing_receipt_from_finalization: Callable[
        ...,
        P1DecisionWorkflowTimingReceiptV1,
    ],
    publish_finalized_candidate: Callable[
        [P1DecisionChildIntegrationResultV1, P1DecisionWorkflowTimingReceiptV1],
        P1DecisionChildIntegrationResultV1,
    ],
    identity_snapshot: Callable[..., tuple[object, ...]],
) -> tuple[
    Callable[[object, bytes, str], P1DecisionChildIntegrationResultV1],
    Callable[[object, object, bytes, str], bool],
    Callable[[object], bool],
    Callable[[object], bool],
]:
    """Create one runner-local execution path and hidden result registry."""

    registry_lock = threading.Lock()
    registry: dict[int, _P1DecisionChildResultRegistryEntryV1] = {}
    runner_binding = object()
    owner_lock = threading.Lock()
    owner_reference: weakref.ReferenceType[object] | None = None

    def bind_owner(owner: object) -> bool:
        nonlocal owner_reference
        try:
            reference = weakref.ref(owner)
        except TypeError:
            return False
        with owner_lock:
            if owner_reference is not None:
                return False
            owner_reference = reference
        return True

    def owner_is_valid(owner: object) -> bool:
        with owner_lock:
            reference = owner_reference
        return reference is not None and reference() is owner

    def register_result(
        result: P1DecisionChildIntegrationResultV1,
        request_bytes: bytes,
        request_sha256: str,
    ) -> P1DecisionChildIntegrationResultV1:
        if (
            type(result) is not P1DecisionChildIntegrationResultV1
            or type(request_bytes) is not bytes
            or type(request_sha256) is not str
        ):
            raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
        result_identity = id(result)

        def discard(
            reference: weakref.ReferenceType[
                P1DecisionChildIntegrationResultV1
            ],
            identity: int = result_identity,
        ) -> None:
            with registry_lock:
                entry = registry.get(identity)
                if (
                    entry is not None
                    and entry.result_reference is reference
                ):
                    del registry[identity]

        reference = weakref.ref(result, discard)
        entry = _P1DecisionChildResultRegistryEntryV1(
            result_reference=reference,
            result_snapshot=identity_snapshot(result),
            input_bytes_sha256=sha256(request_bytes).hexdigest(),
            supplied_request_sha256=request_sha256,
            decision_request_sha256=result.request_sha256,
            supervisor_request_sha256=result.supervisor_request_sha256,
            supervisor_generation_token=result.supervisor_generation_token,
            runner_binding=runner_binding,
        )
        with registry_lock:
            registry[result_identity] = entry
        return result

    def registered_result_matches(
        result: object,
        request_bytes: bytes,
        request_sha256: str,
    ) -> bool:
        try:
            if (
                type(result) is not P1DecisionChildIntegrationResultV1
                or type(request_bytes) is not bytes
                or type(request_sha256) is not str
            ):
                return False
            with registry_lock:
                entry = registry.get(id(result))
            return (
                entry is not None
                and entry.result_reference() is result
                and entry.runner_binding is runner_binding
                and entry.result_snapshot == identity_snapshot(result)
                and entry.input_bytes_sha256
                == sha256(request_bytes).hexdigest()
                and entry.supplied_request_sha256 == request_sha256
                and entry.decision_request_sha256 == result.request_sha256
                and entry.supervisor_request_sha256
                == result.supervisor_request_sha256
                and entry.supervisor_generation_token
                == result.supervisor_generation_token
            )
        except Exception:
            return False

    def is_registered_result(
        owner: object,
        result: object,
        request_bytes: bytes,
        request_sha256: str,
    ) -> bool:
        return (
            owner_is_valid(owner)
            and type(result) is P1DecisionChildIntegrationResultV1
            and type(result.workflow_timing)
            is P1DecisionWorkflowTimingReceiptV1
            and registered_result_matches(
                result,
                request_bytes,
                request_sha256,
            )
        )

    def consume_once(
        ticket: object,
        issued: object,
        expected_supervisor_request_sha256: str,
    ) -> P1ProcessSupervisorResultV1:
        try:
            # No attribute or field of ``issued`` is read before this call.
            supervisor_result = consume_issued(
                ticket,
                issued,
                expected_supervisor_request_sha256,
            )
        except Exception:
            supervisor_result = None
        if supervisor_result is None:
            raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
        return supervisor_result

    def candidate_is_exact(candidate: object) -> bool:
        try:
            if (
                type(candidate) is not P1DecisionChildIntegrationResultV1
                or candidate.workflow_timing is not None
            ):
                return False
            reconstructed = P1DecisionChildIntegrationResultV1(
                **{
                    item.name: getattr(candidate, item.name)
                    for item in fields(P1DecisionChildIntegrationResultV1)
                }
            )
            return reconstructed == candidate
        except Exception:
            return False

    def complete_consumed_workflow(
        ticket: object,
        supervisor_result: P1ProcessSupervisorResultV1,
        *,
        request: P1DecisionRequestV1 | None,
        request_bytes: bytes,
        supplied_request_sha256: str,
        decision_request_sha256: str,
        expected_supervisor_request_sha256: str,
        request_binding_kind: str,
    ) -> P1DecisionChildIntegrationResultV1:
        if not integrity_is_valid() or not config_intact(pinned):
            raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
        if request is None:
            candidate = failure_result(
                supervisor_result,
                request_sha256=decision_request_sha256,
                expected_supervisor_request_sha256=(
                    expected_supervisor_request_sha256
                ),
                request_binding_kind=request_binding_kind,
            )
        else:
            candidate = accept_supervisor_result(
                supervisor_result,
                request=request,
                pinned=pinned,
                expected_supervisor_request_sha256=(
                    expected_supervisor_request_sha256
                ),
            )
        registered_candidate = register_result(
            candidate,
            request_bytes,
            supplied_request_sha256,
        )
        if (
            not candidate_is_exact(registered_candidate)
            or not registered_result_matches(
                registered_candidate,
                request_bytes,
                supplied_request_sha256,
            )
        ):
            raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
        try:
            finalization = finalize_workflow(
                ticket,
                expected_supervisor_request_sha256,
            )
        except Exception:
            finalization = None
        if finalization is None:
            raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
        timing = timing_receipt_from_finalization(
            finalization,
            candidate=registered_candidate,
        )
        published = publish_finalized_candidate(
            registered_candidate,
            timing,
        )
        registered_published = register_result(
            published,
            request_bytes,
            supplied_request_sha256,
        )
        if not registered_result_matches(
            registered_published,
            request_bytes,
            supplied_request_sha256,
        ):
            raise P1DecisionChildError(P1_DECISION_PARENT_VALIDATION_FAILED)
        return registered_published

    def run_with_ticket(
        ticket: object,
        request_bytes: bytes,
        request_sha256: str,
    ) -> P1DecisionChildIntegrationResultV1:
        try:
            request = decode_request(request_bytes, request_sha256)
        except P1DecisionChildError:
            actual_request_sha256, rejected_supervisor_sha256 = (
                _parent_input_rejection_binding(request_bytes)
            )
            issued = run_issued(
                ticket,
                request_bytes,
                rejected_supervisor_sha256,
            )
            supervisor_result = consume_once(
                ticket,
                issued,
                rejected_supervisor_sha256,
            )
            return complete_consumed_workflow(
                ticket,
                supervisor_result,
                request=None,
                request_bytes=request_bytes,
                supplied_request_sha256=request_sha256,
                decision_request_sha256=actual_request_sha256,
                expected_supervisor_request_sha256=(
                    rejected_supervisor_sha256
                ),
                request_binding_kind=_REJECTED_INPUT_BINDING,
            )
        outer_bytes, outer_sha256 = encode_outer_envelope(request, pinned)
        issued = run_issued(ticket, outer_bytes, outer_sha256)
        supervisor_result = consume_once(ticket, issued, outer_sha256)
        return complete_consumed_workflow(
            ticket,
            supervisor_result,
            request=request,
            request_bytes=request_bytes,
            supplied_request_sha256=request_sha256,
            decision_request_sha256=request.request_sha256,
            expected_supervisor_request_sha256=outer_sha256,
            request_binding_kind=_TYPED_REQUEST_BINDING,
        )

    return run_with_ticket, is_registered_result, bind_owner, owner_is_valid


@dataclass(frozen=True, slots=True, weakref_slot=True)
class _BoundP1DecisionChildRunnerV1:
    pinned: _PinnedNativeConfigV1
    begin_workflow: Callable[[], object]
    run_with_ticket: Callable[
        [object, bytes, str],
        P1DecisionChildIntegrationResultV1,
    ]
    encode_request: Callable[..., tuple[bytes, str]]
    verify_identity: Callable[[object, object, bytes, str], bool]
    owner_is_valid: Callable[[object], bool]
    integrity_is_valid: Callable[[], bool]
    config_intact: Callable[[_PinnedNativeConfigV1], bool]
    binding_seal: tuple[int, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding_seal", self._current_binding_seal())

    def _current_binding_seal(self) -> tuple[int, ...]:
        return tuple(
            id(value)
            for value in (
                self.pinned,
                self.begin_workflow,
                self.run_with_ticket,
                self.encode_request,
                self.verify_identity,
                self.owner_is_valid,
                self.integrity_is_valid,
                self.config_intact,
            )
        )

    def _is_intact(self) -> bool:
        try:
            return (
                type(self) is _BoundP1DecisionChildRunnerV1
                and type(self.binding_seal) is tuple
                and self.binding_seal == self._current_binding_seal()
                and self.owner_is_valid(self)
                and self.integrity_is_valid()
                and self.config_intact(self.pinned)
            )
        except Exception:
            return False

    def is_verified_result(
        self,
        result: object,
        request_bytes: bytes,
        request_sha256: str,
    ) -> bool:
        """Verify exact process-local identity from this runner only."""

        return self._is_intact() and self.verify_identity(
            self,
            result,
            request_bytes,
            request_sha256,
        )

    def __call__(
        self,
        request_bytes: bytes,
        request_sha256: str,
    ) -> P1DecisionChildIntegrationResultV1:
        try:
            ticket = self.begin_workflow()
        except Exception:
            raise P1DecisionChildError(
                P1_DECISION_CHILD_EXECUTION_FAILED
            ) from None
        return self._run_with_ticket(ticket, request_bytes, request_sha256)

    def run_typed(
        self,
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
    ) -> P1DecisionChildIntegrationResultV1:
        try:
            ticket = self.begin_workflow()
        except Exception:
            raise P1DecisionChildError(
                P1_DECISION_CHILD_EXECUTION_FAILED
            ) from None
        if not self._is_intact():
            raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
        try:
            request_bytes, request_sha256 = self.encode_request(
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
        except P1DecisionChildError:
            raise
        except Exception:
            raise P1DecisionChildError(
                P1_DECISION_CHILD_REQUEST_INVALID
            ) from None
        return self._run_with_ticket(ticket, request_bytes, request_sha256)

    def _run_with_ticket(
        self,
        ticket: object,
        request_bytes: bytes,
        request_sha256: str,
    ) -> P1DecisionChildIntegrationResultV1:
        if not self._is_intact():
            raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
        try:
            result = self.run_with_ticket(
                ticket,
                request_bytes,
                request_sha256,
            )
            if not self._is_intact():
                raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
            if not self.verify_identity(
                self,
                result,
                request_bytes,
                request_sha256,
            ):
                raise P1DecisionChildError(
                    P1_DECISION_PARENT_VALIDATION_FAILED
                )
            return result
        except P1DecisionChildError:
            raise
        except Exception:
            raise P1DecisionChildError(
                P1_DECISION_CHILD_EXECUTION_FAILED
            ) from None


@dataclass(frozen=True, slots=True)
class _P1DecisionChildFactoryV1:
    begin_workflow: Callable[[], object]
    run_issued: Callable[[object, bytes, str], object | None]
    consume_issued: Callable[
        [object, object, str],
        P1ProcessSupervisorResultV1 | None,
    ]
    finalize_workflow: Callable[[object, str], object | None]
    integrity_is_valid: Callable[[], bool]
    pin_native_config: Callable[..., _PinnedNativeConfigV1]
    config_intact: Callable[[_PinnedNativeConfigV1], bool]
    make_bound_pipeline: Callable[..., tuple[Callable[..., object], ...]]
    encode_request: Callable[..., tuple[bytes, str]]
    decode_request: Callable[[bytes, str], P1DecisionRequestV1]
    encode_outer_envelope: Callable[..., tuple[bytes, str]]
    accept_supervisor_result: Callable[..., P1DecisionChildIntegrationResultV1]
    failure_result: Callable[..., P1DecisionChildIntegrationResultV1]
    timing_receipt_from_finalization: Callable[..., P1DecisionWorkflowTimingReceiptV1]
    publish_finalized_candidate: Callable[..., P1DecisionChildIntegrationResultV1]
    identity_snapshot: Callable[..., tuple[object, ...]]
    binding_seal: tuple[int, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding_seal", self._current_binding_seal())

    def _current_binding_seal(self) -> tuple[int, ...]:
        return tuple(
            id(value)
            for value in (
                self.begin_workflow,
                self.run_issued,
                self.consume_issued,
                self.finalize_workflow,
                self.integrity_is_valid,
                self.pin_native_config,
                self.config_intact,
                self.make_bound_pipeline,
                self.encode_request,
                self.decode_request,
                self.encode_outer_envelope,
                self.accept_supervisor_result,
                self.failure_result,
                self.timing_receipt_from_finalization,
                self.publish_finalized_candidate,
                self.identity_snapshot,
            )
        )

    def _is_intact(self) -> bool:
        try:
            return (
                type(self) is _P1DecisionChildFactoryV1
                and type(self.binding_seal) is tuple
                and self.binding_seal == self._current_binding_seal()
                and self.integrity_is_valid()
            )
        except Exception:
            return False

    def __call__(
        self,
        *,
        native_manifest_path: Path,
        native_build_backend_evidence_sha256: str,
    ) -> Callable[[bytes, str], P1DecisionChildIntegrationResultV1]:
        if not self._is_intact():
            raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
        try:
            pinned = self.pin_native_config(
                native_manifest_path,
                native_build_backend_evidence_sha256,
            )
            (
                run_with_ticket,
                verify_identity,
                bind_owner,
                owner_is_valid,
            ) = self.make_bound_pipeline(
                pinned=pinned,
                run_issued=self.run_issued,
                consume_issued=self.consume_issued,
                finalize_workflow=self.finalize_workflow,
                integrity_is_valid=self.integrity_is_valid,
                config_intact=self.config_intact,
                decode_request=self.decode_request,
                encode_outer_envelope=self.encode_outer_envelope,
                accept_supervisor_result=self.accept_supervisor_result,
                failure_result=self.failure_result,
                timing_receipt_from_finalization=(
                    self.timing_receipt_from_finalization
                ),
                publish_finalized_candidate=(
                    self.publish_finalized_candidate
                ),
                identity_snapshot=self.identity_snapshot,
            )
            runner = _BoundP1DecisionChildRunnerV1(
                pinned=pinned,
                begin_workflow=self.begin_workflow,
                run_with_ticket=run_with_ticket,
                encode_request=self.encode_request,
                verify_identity=verify_identity,
                owner_is_valid=owner_is_valid,
                integrity_is_valid=self.integrity_is_valid,
                config_intact=self.config_intact,
            )
            if bind_owner(runner) is not True or not runner._is_intact():
                raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
            return runner
        except P1DecisionChildError:
            raise
        except Exception:
            raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID) from None


def _build_public_factory(
    supervisor_factory: Callable[
        [Callable[..., None]],
        tuple[
            Callable[..., object],
            Callable[..., object],
            Callable[..., object],
            Callable[..., object],
        ],
    ],
    child_target: Callable[..., None],
    pin_native_config: Callable[..., _PinnedNativeConfigV1],
    encode_request: Callable[..., tuple[bytes, str]],
    decode_request: Callable[[bytes, str], P1DecisionRequestV1],
    encode_outer_envelope: Callable[..., tuple[bytes, str]],
    accept_supervisor_result: Callable[..., P1DecisionChildIntegrationResultV1],
    failure_result: Callable[..., P1DecisionChildIntegrationResultV1],
    timing_receipt_from_finalization: Callable[
        ...,
        P1DecisionWorkflowTimingReceiptV1,
    ],
    publish_finalized_candidate: Callable[
        ...,
        P1DecisionChildIntegrationResultV1,
    ],
    make_bound_pipeline: Callable[..., tuple[Callable[..., object], ...]],
    identity_snapshot: Callable[..., tuple[object, ...]],
) -> Callable[..., object]:
    workflow_capabilities = supervisor_factory(child_target)
    if (
        type(workflow_capabilities) is not tuple
        or len(workflow_capabilities) != 4
        or any(not callable(item) for item in workflow_capabilities)
    ):
        raise P1DecisionChildError(P1_DECISION_CHILD_CONFIG_INVALID)
    begin_workflow, run_issued, consume_issued, finalize_workflow = (
        workflow_capabilities
    )
    captured_target = child_target
    captured_source_path = Path(__file__).resolve(strict=True)
    captured_source_sha256 = sha256(captured_source_path.read_bytes()).hexdigest()
    module_namespace = globals()
    value_compared_types = frozenset(
        {str, int, bool, bytes, tuple, frozenset, type(None)}
    )
    module_bindings = tuple(
        (name, value)
        for name, value in module_namespace.items()
        if not name.startswith("__") and name != "_build_public_factory"
    )
    config_intact = _PinnedNativeConfigV1.is_intact
    target_module_name = (
        "_production_decision_child"
        if child_target is _production_decision_child
        else None
    )
    encoder_module_name = (
        "encode_p1_decision_request"
        if encode_request is encode_p1_decision_request
        else None
    )
    accept_module_name = (
        "_accept_supervisor_result"
        if accept_supervisor_result is _accept_supervisor_result
        else None
    )
    identity_module_name = (
        "_integration_result_identity_snapshot"
        if identity_snapshot is _integration_result_identity_snapshot
        else None
    )
    protected = (
        (target_module_name, child_target),
        ("_pin_native_config", pin_native_config),
        (encoder_module_name, encode_request),
        ("decode_p1_decision_request", decode_request),
        ("_outer_envelope", encode_outer_envelope),
        (accept_module_name, accept_supervisor_result),
        ("_failure_integration_result", failure_result),
        (
            "_workflow_timing_receipt_from_finalization",
            timing_receipt_from_finalization,
        ),
        ("_publish_finalized_candidate", publish_finalized_candidate),
        ("_make_bound_pipeline", make_bound_pipeline),
        (identity_module_name, identity_snapshot),
    )
    artifacts: list[tuple[object, ...]] = []
    for module_name, function in protected:
        code = function.__code__
        source_path = Path(code.co_filename).resolve(strict=True)
        artifacts.append(
            (
                module_name,
                function,
                code,
                function.__defaults__,
                None
                if function.__kwdefaults__ is None
                else dict(function.__kwdefaults__),
                source_path,
                sha256(source_path.read_bytes()).hexdigest(),
            )
        )
    captured_artifacts = tuple(artifacts)
    config_intact_code = config_intact.__code__

    def integrity_is_valid() -> bool:
        try:
            return (
                sha256(captured_source_path.read_bytes()).hexdigest()
                == captured_source_sha256
                and all(
                    name in module_namespace
                    and (
                        type(module_namespace.get(name)) is type(value)
                        and module_namespace.get(name) == value
                    )
                    if type(value) in value_compared_types
                    else module_namespace.get(name) is value
                    for name, value in module_bindings
                )
                and _PinnedNativeConfigV1.is_intact is config_intact
                and config_intact.__code__ is config_intact_code
                and all(
                    (module_name is None or module_namespace.get(module_name) is function)
                    and function.__code__ is code
                    and function.__defaults__ is defaults
                    and (
                        None
                        if function.__kwdefaults__ is None
                        else dict(function.__kwdefaults__)
                    )
                    == kwdefaults
                    and Path(code.co_filename).resolve(strict=True)
                    == source_path
                    and sha256(source_path.read_bytes()).hexdigest()
                    == source_sha256
                    for (
                        module_name,
                        function,
                        code,
                        defaults,
                        kwdefaults,
                        source_path,
                        source_sha256,
                    ) in captured_artifacts
                )
            )
        except Exception:
            return False

    return _P1DecisionChildFactoryV1(
        begin_workflow=begin_workflow,
        run_issued=run_issued,
        consume_issued=consume_issued,
        finalize_workflow=finalize_workflow,
        integrity_is_valid=integrity_is_valid,
        pin_native_config=pin_native_config,
        config_intact=config_intact,
        make_bound_pipeline=make_bound_pipeline,
        encode_request=encode_request,
        decode_request=decode_request,
        encode_outer_envelope=encode_outer_envelope,
        accept_supervisor_result=accept_supervisor_result,
        failure_result=failure_result,
        timing_receipt_from_finalization=timing_receipt_from_finalization,
        publish_finalized_candidate=publish_finalized_candidate,
        identity_snapshot=identity_snapshot,
    )


create_p1_decision_child_runner = _build_public_factory(
    _make_p1_workflow_process_supervisor,
    _production_decision_child,
    _pin_native_config,
    encode_p1_decision_request,
    decode_p1_decision_request,
    _outer_envelope,
    _accept_supervisor_result,
    _failure_integration_result,
    _workflow_timing_receipt_from_finalization,
    _publish_finalized_candidate,
    _make_bound_pipeline,
    _integration_result_identity_snapshot,
)


__all__ = (
    "P1_DECISION_CHILD_ARTIFACT_SCHEMA",
    "P1_DECISION_CHILD_QUALIFICATION_STATUS",
    "P1_DECISION_CHILD_SUCCESS",
    "P1_DECISION_REQUEST_SCHEMA",
    "P1_DECISION_WORKFLOW_TIMING_SCHEMA",
    "P1DecisionChildArtifactV1",
    "P1DecisionChildError",
    "P1DecisionChildIntegrationResultV1",
    "P1DecisionChildSemanticReceiptV1",
    "P1DecisionWorkflowTimingReceiptV1",
    "P1DecisionRequestV1",
    "create_p1_decision_child_runner",
    "decode_p1_decision_request",
    "encode_p1_decision_request",
)
