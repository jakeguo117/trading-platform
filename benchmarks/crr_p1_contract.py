"""Read-only P1 CRR benchmark corpus, golden, and reference runner.

Fixture creation lives in :mod:`benchmarks.generate_crr_p1_fixtures` and is
deliberately absent from every runner entry point in this module.  The
reference runner proves semantic replay only.  It does not claim that the
Python reference engine meets any P1 latency SLO.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
import json
from multiprocessing import get_context
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, Sequence

from gld_normalizer.errors import NormalizationError
from gld_research_core.bcs import (
    BcsFeeScheduleV1,
    Lc0SelectionBindingV1,
    bind_lc0_selection_for_research,
)
from gld_research_core.crr_delta import (
    MODEL_SHA256,
    CrrCallInputsV1,
    CrrDeltaResultV1,
    CrrPitInputsV1,
    bind_crr_call_inputs_from_snapshot,
    compute_american_call_delta,
    is_verified_crr_delta_result,
)
from gld_research_core.facts import (
    OptionContractV1,
    OptionQuoteSnapshotV1,
    OptionQuoteV1,
    SignalSnapshotV1,
    TopOfBookV1,
    validate_option_snapshot_quality,
)


FIXTURE_COUNT = 64
FIXTURE_EXPIRY_COUNT = 4
FIXTURE_STRIKES_PER_EXPIRY = 16
FIXTURE_SCHEMA_VERSION = "GLD_CRR_P1_CORPUS_V1"
GOLDEN_SCHEMA_VERSION = "GLD_CRR_P1_REFERENCE_GOLDEN_V1"
E1_SCHEMA_VERSION = "GLD_CRR_P1_E1_MANIFEST_V1"
RAW_SAMPLE_SCHEMA_VERSION = "GLD_CRR_P1_RAW_SAMPLES_V1"
STATIC_MANIFEST_SCHEMA_VERSION = "GLD_CRR_P1_STATIC_MANIFEST_V1"
REFERENCE_WORKERS = 8
WARMUP_RUNS = 3
_DAY_NS = 86_400_000_000_000

_BENCHMARK_ROOT = Path(__file__).resolve().parent
_FIXTURE_ROOT = _BENCHMARK_ROOT / "fixtures"
_STATIC_MANIFEST_PATH = _FIXTURE_ROOT / "static_manifest_v0.1.json"

# Filled only after the offline generator seals every static artifact.  Keeping
# this in source makes replacement of the manifest an explicit source change.
STATIC_MANIFEST_SHA256 = (
    "5d828faf9edd8f415409aab4997cba24c01584decce315f073491f1f2e4e5ba4"
)


class BenchmarkContractError(ValueError):
    """One deterministic benchmark-contract failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class LoadedCorpusV1:
    fixture_id: str
    fixture_bytes: bytes
    fixture_sha256: str
    document: Mapping[str, object]
    signal: SignalSnapshotV1
    snapshot: OptionQuoteSnapshotV1
    pit_inputs: CrrPitInputsV1
    fees: BcsFeeScheduleV1
    lc0_binding: Lc0SelectionBindingV1
    bound_inputs: tuple[CrrCallInputsV1, ...]
    input_vector_sha256: str


@dataclass(frozen=True, slots=True)
class ReferenceTerminalV1:
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
        if type(self.ordinal) is not int or not 1 <= self.ordinal <= FIXTURE_COUNT:
            raise BenchmarkContractError("BENCHMARK_GOLDEN_ORDINAL_INVALID")
        _require_sha256(
            self.input_sha256,
            "BENCHMARK_GOLDEN_INPUT_HASH_INVALID",
        )
        if type(self.contract_id) is not str or not self.contract_id:
            raise BenchmarkContractError("BENCHMARK_GOLDEN_CONTRACT_INVALID")
        numeric_values = (
            self.iv_ppm,
            self.delta_ppm,
            self.coarse_iv_ppm,
            self.fine_iv_ppm,
            self.coarse_delta_ppm,
            self.fine_delta_ppm,
            self.coarse_price_residual_nano_usd,
            self.fine_price_residual_nano_usd,
            self.coarse_early_exercise_nodes,
            self.fine_early_exercise_nodes,
        )
        if self.terminal_status == "PASS":
            if (
                self.reason_code != "PASS"
                or any(type(value) is not int for value in numeric_values)
                or type(self.early_exercise_detected) is not bool
                or self.early_exercise_detected
                != bool(
                    self.coarse_early_exercise_nodes
                    or self.fine_early_exercise_nodes
                )
            ):
                raise BenchmarkContractError("BENCHMARK_GOLDEN_TERMINAL_INVALID")
        elif self.terminal_status == "FAIL":
            if (
                type(self.reason_code) is not str
                or not self.reason_code
                or any(value is not None for value in numeric_values)
                or self.early_exercise_detected is not False
            ):
                raise BenchmarkContractError("BENCHMARK_GOLDEN_TERMINAL_INVALID")
        else:
            raise BenchmarkContractError("BENCHMARK_GOLDEN_TERMINAL_INVALID")

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


@dataclass(frozen=True, slots=True)
class ReferenceRunReceiptV1:
    requested: int
    bound: int
    started: int
    terminal: int
    result_cache_hits: int
    first_failure_ordinal: int | None
    results: tuple[ReferenceTerminalV1, ...]
    semantic_output_sha256: str
    timed_path: bool = False
    slo_pass_claimed: bool = False

    def __post_init__(self) -> None:
        if (
            (self.requested, self.bound, self.started, self.terminal)
            != (FIXTURE_COUNT,) * 4
            or self.result_cache_hits != 0
            or len(self.results) != FIXTURE_COUNT
            or tuple(item.ordinal for item in self.results)
            != tuple(range(1, FIXTURE_COUNT + 1))
            or self.timed_path is not False
            or self.slo_pass_claimed is not False
        ):
            raise BenchmarkContractError("BENCHMARK_RUN_RECEIPT_INVALID")
        expected_failure = next(
            (
                result.ordinal
                for result in self.results
                if result.terminal_status == "FAIL"
            ),
            None,
        )
        if self.first_failure_ordinal != expected_failure:
            raise BenchmarkContractError("BENCHMARK_FAILURE_ORDER_INVALID")
        _require_sha256(
            self.semantic_output_sha256,
            "BENCHMARK_SEMANTIC_HASH_INVALID",
        )


def canonical_json_bytes(value: object) -> bytes:
    """Return the only accepted static benchmark JSON representation."""

    try:
        return (
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
        raise BenchmarkContractError("BENCHMARK_CANONICAL_JSON_INVALID") from error


def _require_exact_keys(
    value: object,
    expected: set[str],
    reason_code: str,
) -> Mapping[str, object]:
    if type(value) is not dict or set(value) != expected:
        raise BenchmarkContractError(reason_code)
    return value


def _require_sha256(value: object, reason_code: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise BenchmarkContractError(reason_code)
    return value


def _freeze_json(value: object) -> object:
    if type(value) is dict:
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if type(value) is list:
        return tuple(_freeze_json(item) for item in value)
    return value


def _read_canonical_json(path: Path) -> tuple[bytes, dict[str, object]]:
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise BenchmarkContractError("BENCHMARK_STATIC_ARTIFACT_INVALID") from error
    if type(document) is not dict or raw != canonical_json_bytes(document):
        raise BenchmarkContractError("BENCHMARK_CANONICAL_JSON_INVALID")
    return raw, document


def load_static_manifest() -> dict[str, object]:
    raw, document = _read_canonical_json(_STATIC_MANIFEST_PATH)
    if sha256(raw).hexdigest() != STATIC_MANIFEST_SHA256:
        raise BenchmarkContractError("BENCHMARK_STATIC_MANIFEST_HASH_MISMATCH")
    root = _require_exact_keys(
        document,
        {"schema_version", "files", "status"},
        "BENCHMARK_STATIC_MANIFEST_INVALID",
    )
    if (
        root["schema_version"] != STATIC_MANIFEST_SCHEMA_VERSION
        or root["status"] != "STATIC_BYTES_SEALED_REFERENCE_NOT_SLO_QUALIFIED"
        or type(root["files"]) is not dict
    ):
        raise BenchmarkContractError("BENCHMARK_STATIC_MANIFEST_INVALID")
    for relative_path, artifact_sha256 in root["files"].items():
        if (
            type(relative_path) is not str
            or relative_path.startswith(("/", ".."))
            or ".." in Path(relative_path).parts
        ):
            raise BenchmarkContractError("BENCHMARK_STATIC_MANIFEST_INVALID")
        _require_sha256(
            artifact_sha256,
            "BENCHMARK_STATIC_MANIFEST_INVALID",
        )
    return document


def _load_manifest_bound_json(relative_path: str) -> tuple[bytes, dict[str, object]]:
    manifest = load_static_manifest()
    files = manifest["files"]
    expected_sha256 = files.get(relative_path)
    if expected_sha256 is None:
        raise BenchmarkContractError("BENCHMARK_STATIC_ARTIFACT_UNREGISTERED")
    raw, document = _read_canonical_json(_BENCHMARK_ROOT / relative_path)
    if sha256(raw).hexdigest() != expected_sha256:
        raise BenchmarkContractError("BENCHMARK_STATIC_ARTIFACT_HASH_MISMATCH")
    return raw, document


def _typed_book(document: object) -> TopOfBookV1:
    values = _require_exact_keys(
        document,
        {
            "ask_nano_usd",
            "ask_size",
            "bid_nano_usd",
            "bid_size",
            "flags",
            "tick_nano_usd",
            "ts_event_ns",
            "ts_recv_ns",
        },
        "BENCHMARK_BOOK_SCHEMA_INVALID",
    )
    if type(values["flags"]) is not list:
        raise BenchmarkContractError("BENCHMARK_BOOK_SCHEMA_INVALID")
    return TopOfBookV1(
        bid_nano_usd=values["bid_nano_usd"],
        ask_nano_usd=values["ask_nano_usd"],
        bid_size=values["bid_size"],
        ask_size=values["ask_size"],
        tick_nano_usd=values["tick_nano_usd"],
        ts_event_ns=values["ts_event_ns"],
        ts_recv_ns=values["ts_recv_ns"],
        flags=tuple(values["flags"]),
    )


def _typed_contract(document: object) -> OptionContractV1:
    values = _require_exact_keys(
        document,
        {
            "activation_utc_ns",
            "currency",
            "deliverable",
            "exercise_style",
            "expiry_utc_ns",
            "last_trading_utc_ns",
            "multiplier",
            "occ_symbol",
            "right",
            "standard_unadjusted",
            "strike_nano_usd",
            "underlying",
        },
        "BENCHMARK_CONTRACT_SCHEMA_INVALID",
    )
    return OptionContractV1(**values)  # type: ignore[arg-type]


def _typed_quote(document: object) -> OptionQuoteV1:
    values = _require_exact_keys(
        document,
        {"contract", "top_of_book"},
        "BENCHMARK_QUOTE_SCHEMA_INVALID",
    )
    return OptionQuoteV1(
        contract=_typed_contract(values["contract"]),
        top_of_book=_typed_book(values["top_of_book"]),
    )


def _validate_quote_vector(documents: object) -> tuple[OptionQuoteV1, ...]:
    if type(documents) is not list or len(documents) != FIXTURE_COUNT:
        raise BenchmarkContractError("BENCHMARK_CALL_COUNT_INVALID")
    raw_contracts = tuple(
        _require_exact_keys(
            _require_exact_keys(
                document,
                {"contract", "top_of_book"},
                "BENCHMARK_QUOTE_SCHEMA_INVALID",
            )["contract"],
            {
                "activation_utc_ns",
                "currency",
                "deliverable",
                "exercise_style",
                "expiry_utc_ns",
                "last_trading_utc_ns",
                "multiplier",
                "occ_symbol",
                "right",
                "standard_unadjusted",
                "strike_nano_usd",
                "underlying",
            },
            "BENCHMARK_CONTRACT_SCHEMA_INVALID",
        )
        for document in documents
    )
    identities = tuple(contract["occ_symbol"] for contract in raw_contracts)
    if len(set(identities)) != FIXTURE_COUNT:
        raise BenchmarkContractError("BENCHMARK_CONTRACT_IDENTITY_DUPLICATE")
    order = tuple(
        (
            contract["expiry_utc_ns"],
            contract["strike_nano_usd"],
            contract["occ_symbol"],
        )
        for contract in raw_contracts
    )
    if order != tuple(sorted(order)):
        raise BenchmarkContractError("BENCHMARK_CANONICAL_ORDER_INVALID")
    expiry_counts = Counter(contract["expiry_utc_ns"] for contract in raw_contracts)
    if (
        len(expiry_counts) != FIXTURE_EXPIRY_COUNT
        or set(expiry_counts.values()) != {FIXTURE_STRIKES_PER_EXPIRY}
    ):
        raise BenchmarkContractError("BENCHMARK_EXPIRY_STRIKE_GRID_INVALID")
    return tuple(_typed_quote(document) for document in documents)


def _typed_signal(document: object) -> SignalSnapshotV1:
    values = _require_exact_keys(
        document,
        {
            "calendar_authority_id",
            "calendar_sha256",
            "calendar_version",
            "completeness_receipt_sha256",
            "cutoff_utc_ns",
            "input_fact_sha256",
            "max_event_utc_ns",
            "max_receive_utc_ns",
            "phase_receipt_sha256",
            "rule_sha256",
            "rule_version",
            "signal_id",
            "signal_state",
            "trading_date",
        },
        "BENCHMARK_SIGNAL_SCHEMA_INVALID",
    )
    return SignalSnapshotV1(**values)  # type: ignore[arg-type]


def _typed_snapshot(
    document: object,
    quotes: tuple[OptionQuoteV1, ...],
) -> OptionQuoteSnapshotV1:
    values = _require_exact_keys(
        document,
        {
            "candidate_ordinal",
            "candidate_provenance_sha256",
            "capture_utc_ns",
            "market_data_type",
            "option_quotes",
            "signal_snapshot_sha256",
            "source_id",
            "source_receipt_sha256",
            "source_version",
            "underlying_top",
            "window_end_utc_ns",
            "window_start_utc_ns",
        },
        "BENCHMARK_SNAPSHOT_SCHEMA_INVALID",
    )
    return OptionQuoteSnapshotV1(
        signal_snapshot_sha256=values["signal_snapshot_sha256"],
        candidate_ordinal=values["candidate_ordinal"],
        candidate_provenance_sha256=values[
            "candidate_provenance_sha256"
        ],
        window_start_utc_ns=values["window_start_utc_ns"],
        window_end_utc_ns=values["window_end_utc_ns"],
        capture_utc_ns=values["capture_utc_ns"],
        underlying_top=_typed_book(values["underlying_top"]),
        option_quotes=quotes,
        source_id=values["source_id"],
        source_version=values["source_version"],
        market_data_type=values["market_data_type"],
        source_receipt_sha256=values["source_receipt_sha256"],
    )


def _typed_pit(document: object) -> CrrPitInputsV1:
    values = _require_exact_keys(
        document,
        {
            "as_of_utc_ns",
            "borrow_assumption_sha256",
            "borrow_yield_ppm",
            "distribution_assumption_sha256",
            "expense_yield_ppm",
            "rate_curve_sha256",
            "risk_free_rate_ppm",
        },
        "BENCHMARK_PIT_SCHEMA_INVALID",
    )
    return CrrPitInputsV1(**values)  # type: ignore[arg-type]


def _typed_fees(document: object) -> BcsFeeScheduleV1:
    values = _require_exact_keys(
        document,
        {
            "effective_from_utc_ns",
            "long_entry_fee_nano_usd_per_contract",
            "long_exit_fee_nano_usd_per_contract",
            "short_entry_fee_nano_usd_per_contract",
            "short_exit_fee_nano_usd_per_contract",
            "source_receipt_sha256",
        },
        "BENCHMARK_FEE_SCHEMA_INVALID",
    )
    return BcsFeeScheduleV1(**values)  # type: ignore[arg-type]


def _input_vector_sha256(inputs: Sequence[CrrCallInputsV1]) -> str:
    return sha256(
        canonical_json_bytes([item.input_sha256 for item in inputs])
    ).hexdigest()


def _corpus_semantic_sha256(
    *,
    signal: SignalSnapshotV1,
    snapshot: OptionQuoteSnapshotV1,
    pit_inputs: CrrPitInputsV1,
    fees: BcsFeeScheduleV1,
    lc0_binding: Lc0SelectionBindingV1,
    input_vector_sha256: str,
) -> str:
    return sha256(
        canonical_json_bytes(
            {
                "fee_schedule_sha256": fees.fee_schedule_sha256,
                "input_vector_sha256": input_vector_sha256,
                "lc0_binding_sha256": lc0_binding.binding_sha256,
                "option_snapshot_sha256": snapshot.snapshot_sha256,
                "pit_inputs_sha256": pit_inputs.pit_inputs_sha256,
                "signal_snapshot_sha256": signal.snapshot_sha256,
            }
        )
    ).hexdigest()


def load_corpus_document(
    document: object,
    *,
    fixture_bytes: bytes,
) -> LoadedCorpusV1:
    """Validate one already-decoded corpus without consulting file paths."""

    root = _require_exact_keys(
        document,
        {
            "canonical_order",
            "declared_hashes",
            "expected_terminal_count",
            "fee_schedule",
            "fixture_id",
            "lc0_binding",
            "pit_inputs",
            "provenance",
            "purpose",
            "schema_version",
            "selected_snapshot",
            "signal_snapshot",
            "warmup_runs",
        },
        "BENCHMARK_FIXTURE_SCHEMA_INVALID",
    )
    if fixture_bytes != canonical_json_bytes(document):
        raise BenchmarkContractError("BENCHMARK_CANONICAL_JSON_INVALID")
    fixture_id = root["fixture_id"]
    if (
        root["schema_version"] != FIXTURE_SCHEMA_VERSION
        or fixture_id not in {"U64", "W64"}
        or root["expected_terminal_count"] != FIXTURE_COUNT
        or root["canonical_order"]
        != ["expiry_utc_ns", "strike_nano_usd", "occ_symbol"]
        or root["purpose"]
        != ("SUCCESS_LOAD" if fixture_id == "U64" else "WARMUP_LOAD")
        or root["warmup_runs"] != (0 if fixture_id == "U64" else WARMUP_RUNS)
    ):
        raise BenchmarkContractError("BENCHMARK_FIXTURE_SCHEMA_INVALID")
    provenance = _require_exact_keys(
        root["provenance"],
        {"data_authority", "provider_access", "slo_pass_claimed"},
        "BENCHMARK_PROVENANCE_BOUNDARY_INVALID",
    )
    if provenance != {
        "data_authority": "SYNTHETIC_LOCAL_ONLY",
        "provider_access": "NONE",
        "slo_pass_claimed": False,
    }:
        raise BenchmarkContractError("BENCHMARK_PROVENANCE_BOUNDARY_INVALID")

    snapshot_document = _require_exact_keys(
        root["selected_snapshot"],
        {
            "candidate_ordinal",
            "candidate_provenance_sha256",
            "capture_utc_ns",
            "market_data_type",
            "option_quotes",
            "signal_snapshot_sha256",
            "source_id",
            "source_receipt_sha256",
            "source_version",
            "underlying_top",
            "window_end_utc_ns",
            "window_start_utc_ns",
        },
        "BENCHMARK_SNAPSHOT_SCHEMA_INVALID",
    )
    quotes = _validate_quote_vector(snapshot_document["option_quotes"])
    try:
        signal = _typed_signal(root["signal_snapshot"])
        snapshot = _typed_snapshot(root["selected_snapshot"], quotes)
        validate_option_snapshot_quality(snapshot)
        pit_inputs = _typed_pit(root["pit_inputs"])
        fees = _typed_fees(root["fee_schedule"])
    except NormalizationError as error:
        raise BenchmarkContractError("BENCHMARK_TYPED_FIXTURE_INVALID") from error
    if snapshot.signal_snapshot_sha256 != signal.snapshot_sha256:
        raise BenchmarkContractError("BENCHMARK_SIGNAL_SNAPSHOT_MISMATCH")
    if any(
        not 30 * _DAY_NS
        <= quote.contract.expiry_utc_ns - snapshot.capture_utc_ns
        <= 365 * _DAY_NS
        for quote in snapshot.option_quotes
    ):
        raise BenchmarkContractError("BENCHMARK_DTE_RANGE_INVALID")

    lc0_document = _require_exact_keys(
        root["lc0_binding"],
        {
            "expected_bcs_short_call_id",
            "long_call_id",
            "selector_contract_sha256",
        },
        "BENCHMARK_LC0_BINDING_INVALID",
    )
    try:
        lc0_binding = bind_lc0_selection_for_research(
            signal=signal,
            snapshot=snapshot,
            selector_contract_sha256=lc0_document[
                "selector_contract_sha256"
            ],
            long_call_id=lc0_document["long_call_id"],
        )
        bound_inputs = tuple(
            bind_crr_call_inputs_from_snapshot(
                snapshot,
                contract=quote.contract,
                pit_inputs=pit_inputs,
            )
            for quote in snapshot.option_quotes
        )
    except NormalizationError as error:
        raise BenchmarkContractError("BENCHMARK_INPUT_BINDING_INVALID") from error
    input_vector_sha256 = _input_vector_sha256(bound_inputs)
    corpus_semantic_sha256 = _corpus_semantic_sha256(
        signal=signal,
        snapshot=snapshot,
        pit_inputs=pit_inputs,
        fees=fees,
        lc0_binding=lc0_binding,
        input_vector_sha256=input_vector_sha256,
    )
    declared = _require_exact_keys(
        root["declared_hashes"],
        {
            "corpus_semantic_sha256",
            "fee_schedule_sha256",
            "input_vector_sha256",
            "lc0_binding_sha256",
            "option_snapshot_sha256",
            "pit_inputs_sha256",
            "signal_snapshot_sha256",
        },
        "BENCHMARK_DECLARED_HASH_MISMATCH",
    )
    actual = {
        "corpus_semantic_sha256": corpus_semantic_sha256,
        "fee_schedule_sha256": fees.fee_schedule_sha256,
        "input_vector_sha256": input_vector_sha256,
        "lc0_binding_sha256": lc0_binding.binding_sha256,
        "option_snapshot_sha256": snapshot.snapshot_sha256,
        "pit_inputs_sha256": pit_inputs.pit_inputs_sha256,
        "signal_snapshot_sha256": signal.snapshot_sha256,
    }
    if dict(declared) != actual:
        raise BenchmarkContractError("BENCHMARK_DECLARED_HASH_MISMATCH")
    return LoadedCorpusV1(
        fixture_id=fixture_id,
        fixture_bytes=fixture_bytes,
        fixture_sha256=sha256(fixture_bytes).hexdigest(),
        document=_freeze_json(document),  # type: ignore[arg-type]
        signal=signal,
        snapshot=snapshot,
        pit_inputs=pit_inputs,
        fees=fees,
        lc0_binding=lc0_binding,
        bound_inputs=bound_inputs,
        input_vector_sha256=input_vector_sha256,
    )


def load_corpus(fixture_id: str) -> LoadedCorpusV1:
    if fixture_id not in {"U64", "W64"}:
        raise BenchmarkContractError("BENCHMARK_FIXTURE_ID_INVALID")
    relative_path = f"fixtures/{fixture_id.lower()}_v0.1.json"
    raw, document = _load_manifest_bound_json(relative_path)
    return load_corpus_document(document, fixture_bytes=raw)


def _terminal_from_result(
    ordinal: int,
    inputs: CrrCallInputsV1,
    result: object,
    *,
    verified: bool,
) -> ReferenceTerminalV1:
    if not verified:
        return _failure_terminal(ordinal, inputs, "UNVERIFIED_DELTA_RESULT")
    required = (
        "input_sha256",
        "contract_id",
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
    if any(not hasattr(result, name) for name in required):
        return _failure_terminal(ordinal, inputs, "REFERENCE_RESULT_INVALID")
    if (
        result.input_sha256 != inputs.input_sha256
        or result.contract_id != inputs.contract_id
    ):
        return _failure_terminal(ordinal, inputs, "DELTA_INPUT_BINDING_MISMATCH")
    return ReferenceTerminalV1(
        ordinal=ordinal,
        input_sha256=inputs.input_sha256,
        terminal_status="PASS",
        reason_code="PASS",
        contract_id=inputs.contract_id,
        iv_ppm=result.iv_ppm,
        delta_ppm=result.delta_ppm,
        coarse_iv_ppm=result.coarse_iv_ppm,
        fine_iv_ppm=result.fine_iv_ppm,
        coarse_delta_ppm=result.coarse_delta_ppm,
        fine_delta_ppm=result.fine_delta_ppm,
        coarse_price_residual_nano_usd=(
            result.coarse_price_residual_nano_usd
        ),
        fine_price_residual_nano_usd=result.fine_price_residual_nano_usd,
        coarse_early_exercise_nodes=result.coarse_early_exercise_nodes,
        fine_early_exercise_nodes=result.fine_early_exercise_nodes,
        early_exercise_detected=bool(
            result.coarse_early_exercise_nodes
            or result.fine_early_exercise_nodes
        ),
    )


def _failure_terminal(
    ordinal: int,
    inputs: CrrCallInputsV1,
    reason_code: str,
) -> ReferenceTerminalV1:
    return ReferenceTerminalV1(
        ordinal=ordinal,
        input_sha256=inputs.input_sha256,
        terminal_status="FAIL",
        reason_code=reason_code,
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


def _validate_bound_vector(
    inputs: object,
) -> tuple[CrrCallInputsV1, ...]:
    if (
        type(inputs) is not tuple
        or len(inputs) != FIXTURE_COUNT
        or any(type(item) is not CrrCallInputsV1 for item in inputs)
    ):
        raise BenchmarkContractError("BENCHMARK_BOUND_VECTOR_INVALID")
    identities = tuple(item.contract_id for item in inputs)
    input_hashes = tuple(item.input_sha256 for item in inputs)
    if len(set(identities)) != FIXTURE_COUNT or len(set(input_hashes)) != FIXTURE_COUNT:
        raise BenchmarkContractError("BENCHMARK_BOUND_VECTOR_DUPLICATE")
    order = tuple(
        (item.expiry_utc_ns, item.strike_nano_usd, item.contract_id)
        for item in inputs
    )
    if order != tuple(sorted(order)):
        raise BenchmarkContractError("BENCHMARK_BOUND_VECTOR_ORDER_INVALID")
    if len({item.option_snapshot_sha256 for item in inputs}) != 1:
        raise BenchmarkContractError("BENCHMARK_BOUND_VECTOR_SNAPSHOT_INVALID")
    return inputs


def _receipt(results: tuple[ReferenceTerminalV1, ...]) -> ReferenceRunReceiptV1:
    semantic_output_sha256 = sha256(
        canonical_json_bytes([item.as_dict() for item in results])
    ).hexdigest()
    first_failure = next(
        (item.ordinal for item in results if item.terminal_status == "FAIL"),
        None,
    )
    return ReferenceRunReceiptV1(
        requested=FIXTURE_COUNT,
        bound=FIXTURE_COUNT,
        started=FIXTURE_COUNT,
        terminal=FIXTURE_COUNT,
        result_cache_hits=0,
        first_failure_ordinal=first_failure,
        results=results,
        semantic_output_sha256=semantic_output_sha256,
    )


def run_reference_inputs(
    inputs: tuple[CrrCallInputsV1, ...],
    *,
    compute_call: Callable[..., object] = compute_american_call_delta,
    verify_call: Callable[[object], bool] = is_verified_crr_delta_result,
) -> ReferenceRunReceiptV1:
    """Run all 64 in order, including after a lower-ordinal failure.

    This serial entry point exists to test runner semantics.  It contains no
    result cache and no winner-first or candidate-pruning branch.
    """

    bound_inputs = _validate_bound_vector(inputs)
    results: list[ReferenceTerminalV1] = []
    for ordinal, call_inputs in enumerate(bound_inputs, start=1):
        try:
            result = compute_call(
                call_inputs,
                expected_model_sha256=MODEL_SHA256,
            )
            terminal = _terminal_from_result(
                ordinal,
                call_inputs,
                result,
                verified=verify_call(result),
            )
        except NormalizationError as error:
            terminal = _failure_terminal(
                ordinal,
                call_inputs,
                error.reason_code,
            )
        except Exception:
            terminal = _failure_terminal(
                ordinal,
                call_inputs,
                "REFERENCE_ENGINE_UNEXPECTED_EXCEPTION",
            )
        results.append(terminal)
    return _receipt(tuple(results))


def _reference_worker(
    payload: tuple[int, CrrCallInputsV1],
) -> dict[str, object]:
    ordinal, inputs = payload
    try:
        result = compute_american_call_delta(
            inputs,
            expected_model_sha256=MODEL_SHA256,
        )
        terminal = _terminal_from_result(
            ordinal,
            inputs,
            result,
            verified=is_verified_crr_delta_result(result),
        )
    except NormalizationError as error:
        terminal = _failure_terminal(ordinal, inputs, error.reason_code)
    except Exception:
        terminal = _failure_terminal(
            ordinal,
            inputs,
            "REFERENCE_ENGINE_UNEXPECTED_EXCEPTION",
        )
    return terminal.as_dict()


def _run_reference_inputs_parallel(
    inputs: tuple[CrrCallInputsV1, ...],
    *,
    workers: int,
) -> ReferenceRunReceiptV1:
    bound_inputs = _validate_bound_vector(inputs)
    if type(workers) is not int or workers != REFERENCE_WORKERS:
        raise BenchmarkContractError("BENCHMARK_WORKER_POLICY_INVALID")
    payloads = tuple(enumerate(bound_inputs, start=1))
    with ProcessPoolExecutor(
        max_workers=REFERENCE_WORKERS,
        mp_context=get_context("spawn"),
    ) as pool:
        documents = tuple(pool.map(_reference_worker, payloads, chunksize=1))
    results = tuple(ReferenceTerminalV1(**document) for document in documents)
    return _receipt(results)


def _validate_golden_document(
    fixture_id: str,
    document: object,
) -> dict[str, object]:
    root = _require_exact_keys(
        document,
        {
            "expected_bcs_short_call_id",
            "fixture_file_sha256",
            "fixture_id",
            "input_vector_sha256",
            "reference_model_sha256",
            "results",
            "schema_version",
            "semantic_output_sha256",
            "status",
        },
        "BENCHMARK_GOLDEN_SCHEMA_INVALID",
    )
    corpus = load_corpus(fixture_id)
    if (
        root["schema_version"] != GOLDEN_SCHEMA_VERSION
        or root["fixture_id"] != fixture_id
        or root["fixture_file_sha256"] != corpus.fixture_sha256
        or root["input_vector_sha256"] != corpus.input_vector_sha256
        or root["reference_model_sha256"] != MODEL_SHA256
        or root["status"] != "REFERENCE_SEMANTIC_ONLY_NOT_SLO_QUALIFIED"
        or type(root["results"]) is not list
        or len(root["results"]) != FIXTURE_COUNT
    ):
        raise BenchmarkContractError("BENCHMARK_GOLDEN_SCHEMA_INVALID")
    results = tuple(
        ReferenceTerminalV1(**result)  # type: ignore[arg-type]
        for result in root["results"]
    )
    if tuple(item.ordinal for item in results) != tuple(range(1, 65)):
        raise BenchmarkContractError("BENCHMARK_GOLDEN_ORDER_INVALID")
    if tuple(item.input_sha256 for item in results) != tuple(
        item.input_sha256 for item in corpus.bound_inputs
    ):
        raise BenchmarkContractError("BENCHMARK_GOLDEN_INPUT_MISMATCH")
    expected_semantic_hash = sha256(
        canonical_json_bytes([item.as_dict() for item in results])
    ).hexdigest()
    if root["semantic_output_sha256"] != expected_semantic_hash:
        raise BenchmarkContractError("BENCHMARK_GOLDEN_HASH_MISMATCH")
    lc0_document = corpus.document["lc0_binding"]
    expected_winner = _golden_bcs_short_winner(corpus, results)
    if (
        root["expected_bcs_short_call_id"] != expected_winner
        or lc0_document["expected_bcs_short_call_id"] != expected_winner
    ):
        raise BenchmarkContractError("BENCHMARK_BCS_WINNER_MISMATCH")
    return document  # type: ignore[return-value]


def _golden_bcs_short_winner(
    corpus: LoadedCorpusV1,
    results: tuple[ReferenceTerminalV1, ...],
) -> str:
    quote_by_id = {
        quote.contract.occ_symbol: quote for quote in corpus.snapshot.option_quotes
    }
    long_quote = quote_by_id[corpus.lc0_binding.long_call_id]
    result_by_id = {result.contract_id: result for result in results}

    def winner(suite: str) -> str:
        candidates: list[tuple[int, int, str]] = []
        attribute = f"{suite}_delta_ppm"
        for contract_id, quote in quote_by_id.items():
            result = result_by_id[contract_id]
            delta_ppm = getattr(result, attribute)
            if (
                result.terminal_status == "PASS"
                and type(delta_ppm) is int
                and quote.contract.expiry_utc_ns
                == long_quote.contract.expiry_utc_ns
                and quote.contract.strike_nano_usd
                > long_quote.contract.strike_nano_usd
                and 200_000 <= delta_ppm <= 300_000
            ):
                candidates.append(
                    (
                        abs(delta_ppm - 250_000),
                        -quote.contract.strike_nano_usd,
                        contract_id,
                    )
                )
        if not candidates:
            raise BenchmarkContractError("BENCHMARK_BCS_WINNER_MISMATCH")
        return min(candidates)[2]

    coarse_winner = winner("coarse")
    fine_winner = winner("fine")
    if coarse_winner != fine_winner:
        raise BenchmarkContractError("BENCHMARK_BCS_WINNER_MISMATCH")
    return fine_winner


def load_reference_golden(
    fixture_id: str,
    *,
    document: object | None = None,
) -> dict[str, object]:
    if fixture_id not in {"U64", "W64"}:
        raise BenchmarkContractError("BENCHMARK_FIXTURE_ID_INVALID")
    if document is None:
        _, document = _load_manifest_bound_json(
            f"fixtures/{fixture_id.lower()}_reference_golden_v0.1.json"
        )
    return _validate_golden_document(fixture_id, document)


def run_reference_corpus(
    fixture_id: str,
    *,
    workers: int = REFERENCE_WORKERS,
) -> ReferenceRunReceiptV1:
    """Replay static corpus semantics; this is explicitly not a timed path."""

    corpus = load_corpus(fixture_id)
    receipt = _run_reference_inputs_parallel(corpus.bound_inputs, workers=workers)
    golden = load_reference_golden(fixture_id)
    if (
        tuple(item.as_dict() for item in receipt.results)
        != tuple(golden["results"])
        or receipt.semantic_output_sha256 != golden["semantic_output_sha256"]
    ):
        raise BenchmarkContractError("BENCHMARK_REFERENCE_GOLDEN_MISMATCH")
    return receipt


def load_e1_manifest() -> dict[str, object]:
    _, document = _load_manifest_bound_json("fixtures/e1_manifest_v0.1.json")
    root = _require_exact_keys(
        document,
        {"evidence_status", "mappings", "schema_version"},
        "BENCHMARK_E1_MANIFEST_INVALID",
    )
    if (
        root["schema_version"] != E1_SCHEMA_VERSION
        or root["evidence_status"] != "MAPPED_NOT_EXECUTED"
        or type(root["mappings"]) is not list
        or not root["mappings"]
    ):
        raise BenchmarkContractError("BENCHMARK_E1_MANIFEST_INVALID")
    categories: set[str] = set()
    node_ids: set[str] = set()
    for mapping in root["mappings"]:
        values = _require_exact_keys(
            mapping,
            {"category", "claim", "node_ids"},
            "BENCHMARK_E1_MANIFEST_INVALID",
        )
        if (
            type(values["category"]) is not str
            or values["category"] in categories
            or values["claim"] != "EXISTING_TEST_MAPPING_ONLY"
            or type(values["node_ids"]) is not list
            or not values["node_ids"]
            or any(
                type(node_id) is not str
                or node_id.count("::") != 2
                or node_id in node_ids
                for node_id in values["node_ids"]
            )
        ):
            raise BenchmarkContractError("BENCHMARK_E1_MANIFEST_INVALID")
        categories.add(values["category"])
        node_ids.update(values["node_ids"])
    return document


def nearest_rank_p95_ns(samples_ns: Iterable[int]) -> int:
    samples = tuple(samples_ns)
    if (
        not samples
        or any(type(sample) is not int or sample <= 0 for sample in samples)
    ):
        raise BenchmarkContractError("BENCHMARK_LATENCY_SAMPLE_INVALID")
    ordered = sorted(samples)
    index = ((95 * len(ordered) + 99) // 100) - 1
    return ordered[index]


def validate_raw_sample_receipt(document: object) -> int:
    # Loading the schema makes raw receipts depend on sealed schema bytes even
    # though validation is intentionally implemented without jsonschema.
    _, schema = _load_manifest_bound_json("fixtures/raw_sample_schema_v0.1.json")
    if schema.get("schema_version") != RAW_SAMPLE_SCHEMA_VERSION:
        raise BenchmarkContractError("BENCHMARK_RAW_SAMPLE_SCHEMA_INVALID")
    root = _require_exact_keys(
        document,
        {
            "backend_evidence_sha256",
            "bound_calls_per_sample",
            "call_input_sha256",
            "call_terminal_semantic",
            "clock",
            "contract_ordinal",
            "exceptions_count",
            "fixture_id",
            "fixture_file_sha256",
            "gc_policy",
            "input_vector_sha256",
            "kernel_threads_per_worker",
            "outliers_removed",
            "p95_method",
            "reported_p95_ns",
            "requested_calls_per_sample",
            "result_cache_hits",
            "runner_process_ordinal",
            "samples_ns",
            "schema_version",
            "semantic_mismatch_count",
            "semantic_output_sha256",
            "slo_pass_claimed",
            "started_calls_per_sample",
            "stage",
            "terminal_calls_per_sample",
            "terminal_count_mismatch_count",
            "timer_overhead_subtracted",
            "worker_count",
        },
        "BENCHMARK_RAW_SAMPLE_RECEIPT_INVALID",
    )
    stage = root["stage"]
    expected_sample_count = {
        "SINGLE_CALL": 30,
        "BATCH_64": 100,
        "E2E": 100,
        "COLD_E2E": 30,
    }.get(stage)
    expected_calls = 1 if stage == "SINGLE_CALL" else FIXTURE_COUNT
    expected_workers = 1 if stage == "SINGLE_CALL" else REFERENCE_WORKERS
    if (
        root["schema_version"] != RAW_SAMPLE_SCHEMA_VERSION
        or root["fixture_id"] != "U64"
        or expected_sample_count is None
        or root["clock"] != "perf_counter_ns"
        or root["p95_method"] != "NEAREST_RANK_CEIL_0_95_N"
        or type(root["runner_process_ordinal"]) is not int
        or root["runner_process_ordinal"] < 1
        or type(root["samples_ns"]) is not list
        or len(root["samples_ns"]) != expected_sample_count
        or (
            stage == "SINGLE_CALL"
            and (
                type(root["contract_ordinal"]) is not int
                or not 1 <= root["contract_ordinal"] <= FIXTURE_COUNT
            )
        )
        or (
            stage != "SINGLE_CALL"
            and (
                root["contract_ordinal"] is not None
                or root["call_input_sha256"] is not None
                or root["call_terminal_semantic"] is not None
            )
        )
        or (
            root["requested_calls_per_sample"],
            root["bound_calls_per_sample"],
            root["started_calls_per_sample"],
            root["terminal_calls_per_sample"],
        )
        != (expected_calls,) * 4
        or root["worker_count"] != expected_workers
        or root["kernel_threads_per_worker"] != 1
        or root["gc_policy"] not in {"ENABLED", "DISABLED_DURING_TIMED_SAMPLE"}
        or root["result_cache_hits"] != 0
        or root["exceptions_count"] != 0
        or root["semantic_mismatch_count"] != 0
        or root["terminal_count_mismatch_count"] != 0
        or root["outliers_removed"] is not False
        or root["timer_overhead_subtracted"] is not False
        or root["slo_pass_claimed"] is not False
    ):
        raise BenchmarkContractError("BENCHMARK_RAW_SAMPLE_RECEIPT_INVALID")
    for hash_field in (
        "backend_evidence_sha256",
        "fixture_file_sha256",
        "input_vector_sha256",
        "semantic_output_sha256",
    ):
        _require_sha256(
            root[hash_field],
            "BENCHMARK_RAW_SAMPLE_RECEIPT_INVALID",
        )
    corpus = load_corpus(root["fixture_id"])
    golden = load_reference_golden(root["fixture_id"])
    if (
        root["fixture_file_sha256"] != corpus.fixture_sha256
        or root["input_vector_sha256"] != corpus.input_vector_sha256
        or root["semantic_output_sha256"]
        != golden["semantic_output_sha256"]
    ):
        raise BenchmarkContractError("BENCHMARK_RAW_SAMPLE_BINDING_MISMATCH")
    if stage == "SINGLE_CALL":
        ordinal = root["contract_ordinal"]
        terminal = golden["results"][ordinal - 1]
        if (
            root["call_input_sha256"] != terminal["input_sha256"]
            or root["call_terminal_semantic"] != terminal
        ):
            raise BenchmarkContractError(
                "BENCHMARK_RAW_SAMPLE_BINDING_MISMATCH"
            )
    p95 = nearest_rank_p95_ns(root["samples_ns"])
    if type(root["reported_p95_ns"]) is not int or root["reported_p95_ns"] != p95:
        raise BenchmarkContractError("BENCHMARK_RAW_SAMPLE_RECEIPT_INVALID")
    return p95


__all__ = [
    "E1_SCHEMA_VERSION",
    "FIXTURE_COUNT",
    "FIXTURE_SCHEMA_VERSION",
    "GOLDEN_SCHEMA_VERSION",
    "RAW_SAMPLE_SCHEMA_VERSION",
    "REFERENCE_WORKERS",
    "STATIC_MANIFEST_SHA256",
    "WARMUP_RUNS",
    "BenchmarkContractError",
    "LoadedCorpusV1",
    "ReferenceRunReceiptV1",
    "ReferenceTerminalV1",
    "canonical_json_bytes",
    "load_corpus",
    "load_corpus_document",
    "load_e1_manifest",
    "load_reference_golden",
    "load_static_manifest",
    "nearest_rank_p95_ns",
    "run_reference_corpus",
    "run_reference_inputs",
    "validate_raw_sample_receipt",
]
