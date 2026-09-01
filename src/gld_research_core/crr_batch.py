"""Deterministic exact-64 CRR batch semantics without a connected backend.

This module deliberately does not select a reference or native CRR engine.
An integrator must inject one engine and its matching result verifier once when
creating a private batch runner.  The returned runner captures both callables
and accepts only the exact typed input vector on each invocation.

The implementation proves batch ordering, terminal collection, and semantic
hashing.  It is not an end-to-end runner, has no timeout enforcement, and does
not qualify P1 latency.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, fields
from hashlib import sha256
import json
import re
import threading
from typing import Callable
import weakref

from gld_normalizer.errors import NormalizationError
from gld_normalizer.readers import MAX_DEFINED_I64, MAX_DEFINED_U64

from .crr_delta import MODEL_SHA256, CrrCallInputsV1


BATCH_EXECUTION_STATUS = "BATCH_SEMANTICS_ONLY"
NATIVE_CONNECTION_STATUS = "NATIVE_NOT_CONNECTED"
P1_QUALIFICATION_STATUS = "P1_NOT_PASS"

EXACT_BATCH_SIZE = 64
BATCH_WORKER_COUNT = 8
KERNEL_THREADS = 1
RESULT_CACHE_HITS = 0

SEMANTIC_NUMERIC_FIELDS = (
    "iv_ppm",
    "delta_ppm",
    "coarse_iv_ppm",
    "fine_iv_ppm",
    "coarse_delta_ppm",
    "fine_delta_ppm",
    "coarse_price_residual_nano_usd",
    "fine_price_residual_nano_usd",
    "coarse_early_exercise_nodes",
    "fine_early_exercise_nodes",
)

_PROVENANCE_SCHEMA_VERSION = "GLD_CRR_EXACT_64_PROVENANCE_V1"
_UNEXPECTED_REASON = "BATCH_ENGINE_UNEXPECTED_EXCEPTION"
_UNVERIFIED_REASON = "UNVERIFIED_DELTA_RESULT"
_INVALID_RESULT_REASON = "BATCH_RESULT_INVALID"
_MAX_REASON_CHARS = 128
_MAX_CONTRACT_ID_CHARS = 64
_MAX_CANONICAL_DEPTH = 8
_MAX_CANONICAL_NODES = 4096
_MAX_CANONICAL_TEXT_CHARS = 256
_MAX_CANONICAL_BYTES = 256 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REASON_RE = re.compile(r"[A-Z0-9_]{1,128}\Z", re.ASCII)


class CrrBatchError(ValueError):
    """One stable fail-closed exact-64 batch contract error."""

    def __init__(self, reason_code: str) -> None:
        if type(reason_code) is not str or _REASON_RE.fullmatch(reason_code) is None:
            raise ValueError("invalid batch reason code")
        self.reason_code = reason_code
        super().__init__(reason_code)


def _require_sha256(value: object, reason_code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise CrrBatchError(reason_code)
    return value


def _require_exact_int(
    value: object,
    *,
    minimum: int,
    maximum: int,
    reason_code: str,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CrrBatchError(reason_code)
    return value


def _require_contract_id(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_CONTRACT_ID_CHARS
        or not value.isascii()
        or any(ord(character) < 32 or ord(character) > 126 for character in value)
    ):
        raise CrrBatchError("BATCH_TERMINAL_CONTRACT_INVALID")
    return value


def _require_reason_code(value: object, *, allow_pass: bool) -> str:
    if type(value) is not str or _REASON_RE.fullmatch(value) is None:
        raise CrrBatchError("BATCH_TERMINAL_REASON_INVALID")
    if (value == "PASS") is not allow_pass:
        raise CrrBatchError("BATCH_TERMINAL_REASON_INVALID")
    return value


def _validate_canonical_value(
    value: object,
    *,
    depth: int,
    state: list[int],
) -> None:
    if depth > _MAX_CANONICAL_DEPTH:
        raise CrrBatchError("BATCH_CANONICAL_JSON_INVALID")
    state[0] += 1
    if state[0] > _MAX_CANONICAL_NODES:
        raise CrrBatchError("BATCH_CANONICAL_JSON_INVALID")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not -MAX_DEFINED_I64 <= value <= MAX_DEFINED_I64:
            raise CrrBatchError("BATCH_CANONICAL_JSON_INVALID")
        return
    if type(value) is str:
        if len(value) > _MAX_CANONICAL_TEXT_CHARS or not value.isascii():
            raise CrrBatchError("BATCH_CANONICAL_JSON_INVALID")
        return
    if type(value) is list:
        if len(value) > EXACT_BATCH_SIZE:
            raise CrrBatchError("BATCH_CANONICAL_JSON_INVALID")
        for item in value:
            _validate_canonical_value(item, depth=depth + 1, state=state)
        return
    if type(value) is dict:
        if len(value) > 32 or any(type(key) is not str for key in value):
            raise CrrBatchError("BATCH_CANONICAL_JSON_INVALID")
        for key, item in value.items():
            _validate_canonical_value(key, depth=depth + 1, state=state)
            _validate_canonical_value(item, depth=depth + 1, state=state)
        return
    raise CrrBatchError("BATCH_CANONICAL_JSON_INVALID")


def _canonical_json_bytes(value: object) -> bytes:
    _validate_canonical_value(value, depth=0, state=[0])
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
        raise CrrBatchError("BATCH_CANONICAL_JSON_INVALID") from error
    if len(encoded) > _MAX_CANONICAL_BYTES:
        raise CrrBatchError("BATCH_CANONICAL_JSON_INVALID")
    return encoded


@dataclass(frozen=True, slots=True, weakref_slot=True)
class CrrSemanticTerminalV1:
    """One complete terminal, field-for-field compatible with U64 golden."""

    ordinal: int
    input_sha256: str
    terminal_status: str
    reason_code: str
    contract_id: str
    iv_ppm: int | None
    delta_ppm: int | None
    coarse_iv_ppm: int | None
    fine_iv_ppm: int | None
    coarse_delta_ppm: int | None
    fine_delta_ppm: int | None
    coarse_price_residual_nano_usd: int | None
    fine_price_residual_nano_usd: int | None
    coarse_early_exercise_nodes: int | None
    fine_early_exercise_nodes: int | None
    early_exercise_detected: bool

    def __post_init__(self) -> None:
        _require_exact_int(
            self.ordinal,
            minimum=1,
            maximum=EXACT_BATCH_SIZE,
            reason_code="BATCH_TERMINAL_ORDINAL_INVALID",
        )
        _require_sha256(
            self.input_sha256,
            "BATCH_TERMINAL_INPUT_HASH_INVALID",
        )
        _require_contract_id(self.contract_id)
        if (
            type(self.terminal_status) is not str
            or type(self.early_exercise_detected) is not bool
        ):
            raise CrrBatchError("BATCH_TERMINAL_INVALID")
        if self.terminal_status == "PASS":
            _require_reason_code(self.reason_code, allow_pass=True)
            for value in (
                self.iv_ppm,
                self.coarse_iv_ppm,
                self.fine_iv_ppm,
            ):
                _require_exact_int(
                    value,
                    minimum=100,
                    maximum=5_000_000,
                    reason_code="BATCH_TERMINAL_INVALID",
                )
            for value in (
                self.delta_ppm,
                self.coarse_delta_ppm,
                self.fine_delta_ppm,
            ):
                _require_exact_int(
                    value,
                    minimum=0,
                    maximum=1_000_000,
                    reason_code="BATCH_TERMINAL_INVALID",
                )
            for value in (
                self.coarse_price_residual_nano_usd,
                self.fine_price_residual_nano_usd,
                self.coarse_early_exercise_nodes,
                self.fine_early_exercise_nodes,
            ):
                _require_exact_int(
                    value,
                    minimum=0,
                    maximum=MAX_DEFINED_I64,
                    reason_code="BATCH_TERMINAL_INVALID",
                )
            if (
                self.iv_ppm != self.fine_iv_ppm
                or self.delta_ppm != self.fine_delta_ppm
                or abs(self.coarse_iv_ppm - self.fine_iv_ppm) > 100
                or abs(self.coarse_delta_ppm - self.fine_delta_ppm) > 500
                or self.early_exercise_detected
                != bool(
                    self.coarse_early_exercise_nodes
                    or self.fine_early_exercise_nodes
                )
            ):
                raise CrrBatchError("BATCH_TERMINAL_INVALID")
            return
        if self.terminal_status != "FAIL":
            raise CrrBatchError("BATCH_TERMINAL_INVALID")
        _require_reason_code(self.reason_code, allow_pass=False)
        if (
            any(getattr(self, name) is not None for name in SEMANTIC_NUMERIC_FIELDS)
            or self.early_exercise_detected is not False
        ):
            raise CrrBatchError("BATCH_TERMINAL_INVALID")

    def as_dict(self) -> dict[str, object]:
        return {
            "coarse_delta_ppm": self.coarse_delta_ppm,
            "coarse_early_exercise_nodes": self.coarse_early_exercise_nodes,
            "coarse_iv_ppm": self.coarse_iv_ppm,
            "coarse_price_residual_nano_usd": (
                self.coarse_price_residual_nano_usd
            ),
            "contract_id": self.contract_id,
            "delta_ppm": self.delta_ppm,
            "early_exercise_detected": self.early_exercise_detected,
            "fine_delta_ppm": self.fine_delta_ppm,
            "fine_early_exercise_nodes": self.fine_early_exercise_nodes,
            "fine_iv_ppm": self.fine_iv_ppm,
            "fine_price_residual_nano_usd": self.fine_price_residual_nano_usd,
            "input_sha256": self.input_sha256,
            "iv_ppm": self.iv_ppm,
            "ordinal": self.ordinal,
            "reason_code": self.reason_code,
            "terminal_status": self.terminal_status,
        }


def semantic_output_sha256(
    terminals: tuple[CrrSemanticTerminalV1, ...],
) -> str:
    """Hash only ordered semantic terminals using the U64 golden algorithm."""

    if (
        type(terminals) is not tuple
        or len(terminals) != EXACT_BATCH_SIZE
        or any(type(item) is not CrrSemanticTerminalV1 for item in terminals)
        or tuple(item.ordinal for item in terminals)
        != tuple(range(1, EXACT_BATCH_SIZE + 1))
    ):
        raise CrrBatchError("BATCH_TERMINAL_VECTOR_INVALID")
    try:
        reconstructed = tuple(
            CrrSemanticTerminalV1(**item.as_dict()) for item in terminals
        )
    except (CrrBatchError, TypeError) as error:
        raise CrrBatchError("BATCH_TERMINAL_VECTOR_INVALID") from error
    if reconstructed != terminals:
        raise CrrBatchError("BATCH_TERMINAL_VECTOR_INVALID")
    return sha256(
        _canonical_json_bytes([item.as_dict() for item in terminals])
    ).hexdigest()


def execution_provenance_sha256(
    *,
    backend_evidence_sha256: str,
    input_vector_sha256: str,
    semantic_output_sha256_value: str,
) -> str:
    """Bind fixed execution controls separately from semantic output facts."""

    backend_hash = _require_sha256(
        backend_evidence_sha256,
        "BATCH_BACKEND_EVIDENCE_INVALID",
    )
    input_hash = _require_sha256(
        input_vector_sha256,
        "BATCH_INPUT_VECTOR_HASH_INVALID",
    )
    semantic_hash = _require_sha256(
        semantic_output_sha256_value,
        "BATCH_SEMANTIC_HASH_INVALID",
    )
    payload = {
        "backend_evidence_sha256": backend_hash,
        "batch_size": EXACT_BATCH_SIZE,
        "batch_status": BATCH_EXECUTION_STATUS,
        "cache_hits": RESULT_CACHE_HITS,
        "input_vector_sha256": input_hash,
        "kernel_threads": KERNEL_THREADS,
        "native_connection_status": NATIVE_CONNECTION_STATUS,
        "p1_status": P1_QUALIFICATION_STATUS,
        "schema_version": _PROVENANCE_SCHEMA_VERSION,
        "semantic_output_sha256": semantic_hash,
        "worker_count": BATCH_WORKER_COUNT,
    }
    return sha256(_canonical_json_bytes(payload)).hexdigest()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class CrrExact64BatchReceiptV1:
    status: str
    native_connection_status: str
    p1_status: str
    requested: int
    bound: int
    started: int
    terminal: int
    worker_count: int
    kernel_threads: int
    cache_hits: int
    terminals: tuple[CrrSemanticTerminalV1, ...]
    first_failure_ordinal: int | None
    input_vector_sha256: str
    backend_evidence_sha256: str
    semantic_output_sha256: str
    execution_provenance_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.status) is not str
            or type(self.native_connection_status) is not str
            or type(self.p1_status) is not str
            or self.status != BATCH_EXECUTION_STATUS
            or self.native_connection_status != NATIVE_CONNECTION_STATUS
            or self.p1_status != P1_QUALIFICATION_STATUS
        ):
            raise CrrBatchError("BATCH_RECEIPT_STATUS_INVALID")
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
                raise CrrBatchError("BATCH_RECEIPT_COUNT_INVALID")
        expected_semantic = semantic_output_sha256(self.terminals)
        if self.semantic_output_sha256 != expected_semantic:
            raise CrrBatchError("BATCH_SEMANTIC_HASH_INVALID")
        _require_sha256(
            self.input_vector_sha256,
            "BATCH_INPUT_VECTOR_HASH_INVALID",
        )
        terminal_input_vector_sha256 = sha256(
            _canonical_json_bytes(
                [item.input_sha256 for item in self.terminals]
            )
        ).hexdigest()
        if self.input_vector_sha256 != terminal_input_vector_sha256:
            raise CrrBatchError("BATCH_INPUT_VECTOR_HASH_INVALID")
        _require_sha256(
            self.backend_evidence_sha256,
            "BATCH_BACKEND_EVIDENCE_INVALID",
        )
        expected_provenance = execution_provenance_sha256(
            backend_evidence_sha256=self.backend_evidence_sha256,
            input_vector_sha256=self.input_vector_sha256,
            semantic_output_sha256_value=self.semantic_output_sha256,
        )
        if self.execution_provenance_sha256 != expected_provenance:
            raise CrrBatchError("BATCH_PROVENANCE_HASH_INVALID")
        _require_sha256(
            self.execution_provenance_sha256,
            "BATCH_PROVENANCE_HASH_INVALID",
        )
        expected_failure = next(
            (
                item.ordinal
                for item in self.terminals
                if item.terminal_status == "FAIL"
            ),
            None,
        )
        if (
            self.first_failure_ordinal is not None
            and type(self.first_failure_ordinal) is not int
        ) or self.first_failure_ordinal != expected_failure:
            raise CrrBatchError("BATCH_FAILURE_ORDER_INVALID")

    def as_dict(self) -> dict[str, object]:
        """Return constructor-compatible fields without process-local evidence."""

        return {
            field.name: getattr(self, field.name)
            for field in fields(type(self))
        }


@dataclass(frozen=True, slots=True)
class _WorkerOutcomeV1:
    terminal: CrrSemanticTerminalV1
    inputs: CrrCallInputsV1
    result: object | None


def _failure_terminal(
    ordinal: int,
    inputs: CrrCallInputsV1,
    reason_code: str,
) -> CrrSemanticTerminalV1:
    reason = (
        reason_code
        if type(reason_code) is str
        and len(reason_code) <= _MAX_REASON_CHARS
        and _REASON_RE.fullmatch(reason_code) is not None
        and reason_code != "PASS"
        else _UNEXPECTED_REASON
    )
    return CrrSemanticTerminalV1(
        ordinal=ordinal,
        input_sha256=inputs.input_sha256,
        terminal_status="FAIL",
        reason_code=reason,
        contract_id=inputs.contract_id,
        iv_ppm=None,
        delta_ppm=None,
        coarse_iv_ppm=None,
        fine_iv_ppm=None,
        coarse_delta_ppm=None,
        fine_delta_ppm=None,
        coarse_price_residual_nano_usd=None,
        fine_price_residual_nano_usd=None,
        coarse_early_exercise_nodes=None,
        fine_early_exercise_nodes=None,
        early_exercise_detected=False,
    )


def _pass_terminal(
    ordinal: int,
    inputs: CrrCallInputsV1,
    result: object,
) -> CrrSemanticTerminalV1:
    try:
        input_sha256 = getattr(result, "input_sha256")
        option_snapshot_sha256 = getattr(result, "option_snapshot_sha256")
        contract_id = getattr(result, "contract_id")
    except Exception as error:
        raise CrrBatchError(_INVALID_RESULT_REASON) from error
    if (
        input_sha256 != inputs.input_sha256
        or option_snapshot_sha256 != inputs.option_snapshot_sha256
        or contract_id != inputs.contract_id
    ):
        raise CrrBatchError("DELTA_INPUT_BINDING_MISMATCH")
    try:
        return CrrSemanticTerminalV1(
            ordinal=ordinal,
            input_sha256=inputs.input_sha256,
            terminal_status="PASS",
            reason_code="PASS",
            contract_id=inputs.contract_id,
            iv_ppm=getattr(result, "iv_ppm"),
            delta_ppm=getattr(result, "delta_ppm"),
            coarse_iv_ppm=getattr(result, "coarse_iv_ppm"),
            fine_iv_ppm=getattr(result, "fine_iv_ppm"),
            coarse_delta_ppm=getattr(result, "coarse_delta_ppm"),
            fine_delta_ppm=getattr(result, "fine_delta_ppm"),
            coarse_price_residual_nano_usd=getattr(
                result,
                "coarse_price_residual_nano_usd",
            ),
            fine_price_residual_nano_usd=getattr(
                result,
                "fine_price_residual_nano_usd",
            ),
            coarse_early_exercise_nodes=getattr(
                result,
                "coarse_early_exercise_nodes",
            ),
            fine_early_exercise_nodes=getattr(
                result,
                "fine_early_exercise_nodes",
            ),
            early_exercise_detected=bool(
                getattr(result, "coarse_early_exercise_nodes")
                or getattr(result, "fine_early_exercise_nodes")
            ),
        )
    except CrrBatchError as error:
        if error.reason_code == "DELTA_INPUT_BINDING_MISMATCH":
            raise
        raise CrrBatchError(_INVALID_RESULT_REASON) from error
    except Exception as error:
        raise CrrBatchError(_INVALID_RESULT_REASON) from error


def _validate_bound_vector(
    inputs: object,
) -> tuple[CrrCallInputsV1, ...]:
    if (
        type(inputs) is not tuple
        or len(inputs) != EXACT_BATCH_SIZE
        or any(type(item) is not CrrCallInputsV1 for item in inputs)
    ):
        raise CrrBatchError("BATCH_BOUND_VECTOR_INVALID")
    try:
        contract_ids = tuple(item.contract_id for item in inputs)
        contract_hashes = tuple(item.contract_sha256 for item in inputs)
        input_hashes = tuple(item.input_sha256 for item in inputs)
        for contract_id in contract_ids:
            _require_contract_id(contract_id)
        for contract_hash in contract_hashes:
            _require_sha256(contract_hash, "BATCH_BOUND_CONTRACT_HASH_INVALID")
        for input_hash in input_hashes:
            _require_sha256(input_hash, "BATCH_BOUND_INPUT_HASH_INVALID")
    except CrrBatchError:
        raise
    except Exception as error:
        raise CrrBatchError("BATCH_BOUND_VECTOR_INVALID") from error
    if (
        len(set(contract_ids)) != EXACT_BATCH_SIZE
        or len(set(contract_hashes)) != EXACT_BATCH_SIZE
        or len(set(input_hashes)) != EXACT_BATCH_SIZE
    ):
        raise CrrBatchError("BATCH_BOUND_VECTOR_DUPLICATE")
    for item in inputs:
        _require_exact_int(
            item.expiry_utc_ns,
            minimum=1,
            maximum=MAX_DEFINED_U64,
            reason_code="BATCH_BOUND_VECTOR_ORDER_INVALID",
        )
        _require_exact_int(
            item.strike_nano_usd,
            minimum=1,
            maximum=MAX_DEFINED_I64,
            reason_code="BATCH_BOUND_VECTOR_ORDER_INVALID",
        )
    order = tuple(
        (item.expiry_utc_ns, item.strike_nano_usd, item.contract_id)
        for item in inputs
    )
    if order != tuple(sorted(order)):
        raise CrrBatchError("BATCH_BOUND_VECTOR_ORDER_INVALID")
    snapshots = tuple(item.option_snapshot_sha256 for item in inputs)
    for snapshot_hash in snapshots:
        _require_sha256(snapshot_hash, "BATCH_BOUND_VECTOR_SNAPSHOT_INVALID")
    if len(set(snapshots)) != 1:
        raise CrrBatchError("BATCH_BOUND_VECTOR_SNAPSHOT_INVALID")
    return inputs


def _input_vector_sha256(inputs: tuple[CrrCallInputsV1, ...]) -> str:
    return sha256(
        _canonical_json_bytes([item.input_sha256 for item in inputs])
    ).hexdigest()


def _terminal_payload(terminal: CrrSemanticTerminalV1) -> tuple[object, ...]:
    return tuple(getattr(terminal, field.name) for field in fields(type(terminal)))


def _receipt_payload(receipt: CrrExact64BatchReceiptV1) -> tuple[object, ...]:
    return tuple(getattr(receipt, field.name) for field in fields(type(receipt)))


def _create_exact_64_batch_engine(
    *,
    compute_call: Callable[..., object],
    verify_call: Callable[[object], bool],
    backend_evidence_sha256: str,
) -> tuple[
    Callable[[tuple[CrrCallInputsV1, ...]], CrrExact64BatchReceiptV1],
    Callable[[object], bool],
    Callable[[object], bool],
]:
    """Create one backend-bound runner and process-local verifiers.

    The factory is intentionally private and has no defaults.  Tests and the
    future integrator must name both matching callables and one sealed backend
    evidence hash at construction time.  Individual batch invocations cannot
    substitute an engine or verifier.
    """

    if not callable(compute_call) or not callable(verify_call):
        raise CrrBatchError("BATCH_ENGINE_BINDING_INVALID")
    backend_hash = _require_sha256(
        backend_evidence_sha256,
        "BATCH_BACKEND_EVIDENCE_INVALID",
    )
    compute = compute_call
    verify = verify_call
    terminal_type = CrrSemanticTerminalV1
    receipt_type = CrrExact64BatchReceiptV1
    input_type = CrrCallInputsV1
    evidence_lock = threading.Lock()
    EvidenceRecord = tuple[
        weakref.ReferenceType[CrrSemanticTerminalV1],
        tuple[object, ...],
        CrrCallInputsV1,
        object,
    ]
    verified_pass_by_identity: dict[int, EvidenceRecord] = {}
    ReceiptEvidenceRecord = tuple[
        weakref.ReferenceType[CrrExact64BatchReceiptV1],
        tuple[object, ...],
        str,
        tuple[weakref.ReferenceType[CrrSemanticTerminalV1], ...],
        tuple[int, ...],
        tuple[tuple[object, ...], ...],
        str,
        str,
        str,
    ]
    verified_receipt_by_identity: dict[int, ReceiptEvidenceRecord] = {}

    def run_one(ordinal: int, inputs: CrrCallInputsV1) -> _WorkerOutcomeV1:
        try:
            result = compute(inputs, expected_model_sha256=MODEL_SHA256)
            if verify(result) is not True:
                return _WorkerOutcomeV1(
                    terminal=_failure_terminal(ordinal, inputs, _UNVERIFIED_REASON),
                    inputs=inputs,
                    result=None,
                )
            terminal = _pass_terminal(ordinal, inputs, result)
            return _WorkerOutcomeV1(
                terminal=terminal,
                inputs=inputs,
                result=result,
            )
        except NormalizationError as error:
            return _WorkerOutcomeV1(
                terminal=_failure_terminal(ordinal, inputs, error.reason_code),
                inputs=inputs,
                result=None,
            )
        except CrrBatchError as error:
            return _WorkerOutcomeV1(
                terminal=_failure_terminal(ordinal, inputs, error.reason_code),
                inputs=inputs,
                result=None,
            )
        except Exception:
            return _WorkerOutcomeV1(
                terminal=_failure_terminal(ordinal, inputs, _UNEXPECTED_REASON),
                inputs=inputs,
                result=None,
            )

    def register_pass(outcome: _WorkerOutcomeV1) -> CrrSemanticTerminalV1:
        terminal = outcome.terminal
        result = outcome.result
        if terminal.terminal_status != "PASS" or result is None:
            return terminal
        try:
            if verify(result) is not True:
                return _failure_terminal(
                    terminal.ordinal,
                    outcome.inputs,
                    _UNVERIFIED_REASON,
                )
            if _pass_terminal(terminal.ordinal, outcome.inputs, result) != terminal:
                return _failure_terminal(
                    terminal.ordinal,
                    outcome.inputs,
                    _INVALID_RESULT_REASON,
                )
        except Exception:
            return _failure_terminal(
                terminal.ordinal,
                outcome.inputs,
                _UNVERIFIED_REASON,
            )

        identity = id(terminal)

        def discard(
            reference: weakref.ReferenceType[CrrSemanticTerminalV1],
        ) -> None:
            with evidence_lock:
                record = verified_pass_by_identity.get(identity)
                if record is not None and record[0] is reference:
                    verified_pass_by_identity.pop(identity, None)

        reference = weakref.ref(terminal, discard)
        record: EvidenceRecord = (
            reference,
            _terminal_payload(terminal),
            outcome.inputs,
            result,
        )
        with evidence_lock:
            verified_pass_by_identity[identity] = record
        return terminal

    def run_batch(
        inputs: tuple[CrrCallInputsV1, ...],
    ) -> CrrExact64BatchReceiptV1:
        bound_inputs = _validate_bound_vector(inputs)
        outcomes_by_ordinal: list[_WorkerOutcomeV1 | None] = [None] * EXACT_BATCH_SIZE
        with ThreadPoolExecutor(
            max_workers=BATCH_WORKER_COUNT,
            thread_name_prefix="gld-crr-exact64",
        ) as executor:
            future_bindings: dict[
                Future[_WorkerOutcomeV1],
                tuple[int, CrrCallInputsV1],
            ] = {
                executor.submit(run_one, ordinal, item): (ordinal, item)
                for ordinal, item in enumerate(bound_inputs, start=1)
            }
            for future in as_completed(tuple(future_bindings)):
                ordinal, item = future_bindings[future]
                try:
                    outcome = future.result()
                except Exception:
                    outcome = _WorkerOutcomeV1(
                        terminal=_failure_terminal(
                            ordinal,
                            item,
                            _UNEXPECTED_REASON,
                        ),
                        inputs=item,
                        result=None,
                    )
                outcomes_by_ordinal[ordinal - 1] = outcome

        if any(outcome is None for outcome in outcomes_by_ordinal):
            raise CrrBatchError("BATCH_TERMINAL_COUNT_INVALID")
        ordered_outcomes = tuple(
            outcome
            for outcome in outcomes_by_ordinal
            if outcome is not None
        )
        terminals = tuple(register_pass(outcome) for outcome in ordered_outcomes)
        semantic_hash = semantic_output_sha256(terminals)
        vector_hash = _input_vector_sha256(bound_inputs)
        provenance_hash = execution_provenance_sha256(
            backend_evidence_sha256=backend_hash,
            input_vector_sha256=vector_hash,
            semantic_output_sha256_value=semantic_hash,
        )
        first_failure = next(
            (
                item.ordinal
                for item in terminals
                if item.terminal_status == "FAIL"
            ),
            None,
        )
        return CrrExact64BatchReceiptV1(
            status=BATCH_EXECUTION_STATUS,
            native_connection_status=NATIVE_CONNECTION_STATUS,
            p1_status=P1_QUALIFICATION_STATUS,
            requested=EXACT_BATCH_SIZE,
            bound=EXACT_BATCH_SIZE,
            started=EXACT_BATCH_SIZE,
            terminal=EXACT_BATCH_SIZE,
            worker_count=BATCH_WORKER_COUNT,
            kernel_threads=KERNEL_THREADS,
            cache_hits=RESULT_CACHE_HITS,
            terminals=terminals,
            first_failure_ordinal=first_failure,
            input_vector_sha256=vector_hash,
            backend_evidence_sha256=backend_hash,
            semantic_output_sha256=semantic_hash,
            execution_provenance_sha256=provenance_hash,
        )

    def is_verified_pass_terminal(value: object) -> bool:
        """Recognize only immutable PASS terminals emitted by this runner."""

        try:
            if type(value) is not terminal_type or value.terminal_status != "PASS":
                return False
            with evidence_lock:
                record = verified_pass_by_identity.get(id(value))
            if (
                record is None
                or record[0]() is not value
                or record[1] != _terminal_payload(value)
            ):
                return False
            inputs = record[2]
            result = record[3]
            return (
                type(inputs) is input_type
                and verify(result) is True
                and _pass_terminal(value.ordinal, inputs, result) == value
            )
        except Exception:
            return False

    def register_receipt(
        receipt: CrrExact64BatchReceiptV1,
    ) -> None:
        if (
            type(receipt) is not receipt_type
            or receipt.backend_evidence_sha256 != backend_hash
            or len(receipt.terminals) != EXACT_BATCH_SIZE
            or any(
                terminal.terminal_status == "PASS"
                and not is_verified_pass_terminal(terminal)
                for terminal in receipt.terminals
            )
        ):
            raise CrrBatchError("BATCH_RECEIPT_EVIDENCE_INVALID")
        semantic_hash = semantic_output_sha256(receipt.terminals)
        vector_hash = sha256(
            _canonical_json_bytes(
                [terminal.input_sha256 for terminal in receipt.terminals]
            )
        ).hexdigest()
        provenance_hash = execution_provenance_sha256(
            backend_evidence_sha256=backend_hash,
            input_vector_sha256=vector_hash,
            semantic_output_sha256_value=semantic_hash,
        )
        if (
            receipt.input_vector_sha256 != vector_hash
            or receipt.semantic_output_sha256 != semantic_hash
            or receipt.execution_provenance_sha256 != provenance_hash
        ):
            raise CrrBatchError("BATCH_RECEIPT_EVIDENCE_INVALID")

        identity = id(receipt)

        def discard(
            reference: weakref.ReferenceType[CrrExact64BatchReceiptV1],
        ) -> None:
            with evidence_lock:
                record = verified_receipt_by_identity.get(identity)
                if record is not None and record[0] is reference:
                    verified_receipt_by_identity.pop(identity, None)

        receipt_reference = weakref.ref(receipt, discard)
        terminal_references = tuple(weakref.ref(item) for item in receipt.terminals)
        record: ReceiptEvidenceRecord = (
            receipt_reference,
            _receipt_payload(receipt),
            backend_hash,
            terminal_references,
            tuple(id(item) for item in receipt.terminals),
            tuple(_terminal_payload(item) for item in receipt.terminals),
            vector_hash,
            semantic_hash,
            provenance_hash,
        )
        with evidence_lock:
            verified_receipt_by_identity[identity] = record

    def is_verified_batch_receipt(value: object) -> bool:
        """Recognize only complete receipts emitted by this exact runner."""

        try:
            if type(value) is not receipt_type:
                return False
            with evidence_lock:
                record = verified_receipt_by_identity.get(id(value))
            if (
                record is None
                or record[0]() is not value
                or record[1] != _receipt_payload(value)
                or record[2] != backend_hash
                or value.backend_evidence_sha256 != backend_hash
                or type(value.terminals) is not tuple
                or len(value.terminals) != EXACT_BATCH_SIZE
            ):
                return False
            terminal_references = record[3]
            terminal_identities = record[4]
            terminal_payloads = record[5]
            if (
                len(terminal_references) != EXACT_BATCH_SIZE
                or tuple(id(item) for item in value.terminals)
                != terminal_identities
                or any(
                    reference() is not terminal
                    for reference, terminal in zip(
                        terminal_references,
                        value.terminals,
                        strict=True,
                    )
                )
                or tuple(_terminal_payload(item) for item in value.terminals)
                != terminal_payloads
                or any(
                    terminal.terminal_status == "PASS"
                    and not is_verified_pass_terminal(terminal)
                    for terminal in value.terminals
                )
            ):
                return False
            vector_hash = sha256(
                _canonical_json_bytes(
                    [terminal.input_sha256 for terminal in value.terminals]
                )
            ).hexdigest()
            semantic_hash = semantic_output_sha256(value.terminals)
            provenance_hash = execution_provenance_sha256(
                backend_evidence_sha256=backend_hash,
                input_vector_sha256=vector_hash,
                semantic_output_sha256_value=semantic_hash,
            )
            return (
                vector_hash == record[6] == value.input_vector_sha256
                and semantic_hash == record[7] == value.semantic_output_sha256
                and provenance_hash
                == record[8]
                == value.execution_provenance_sha256
            )
        except Exception:
            return False

    original_run_batch = run_batch

    def run_batch_with_receipt_evidence(
        inputs: tuple[CrrCallInputsV1, ...],
    ) -> CrrExact64BatchReceiptV1:
        receipt = original_run_batch(inputs)
        register_receipt(receipt)
        return receipt

    return (
        run_batch_with_receipt_evidence,
        is_verified_pass_terminal,
        is_verified_batch_receipt,
    )


__all__ = (
    "BATCH_EXECUTION_STATUS",
    "NATIVE_CONNECTION_STATUS",
    "P1_QUALIFICATION_STATUS",
    "EXACT_BATCH_SIZE",
    "BATCH_WORKER_COUNT",
    "KERNEL_THREADS",
    "RESULT_CACHE_HITS",
    "SEMANTIC_NUMERIC_FIELDS",
    "CrrBatchError",
    "CrrSemanticTerminalV1",
    "CrrExact64BatchReceiptV1",
    "semantic_output_sha256",
    "execution_provenance_sha256",
)
