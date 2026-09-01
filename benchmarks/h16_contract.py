"""Sealed H16 CRR holdout loader and reference-only replay.

H16 is never a timed workload.  Its corpus and reference golden are sealed
before any native execution and this module has no native-module dependency.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
import json
from multiprocessing import get_context
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

from gld_normalizer.errors import NormalizationError
from gld_research_core.crr_delta import (
    MODEL_SHA256,
    MODEL_SOURCE_ARTIFACT_SHA256,
    CrrCallInputsV1,
    CrrPitInputsV1,
    compute_american_call_delta,
    is_verified_crr_delta_result,
)


H16_COUNT = 16
H16_REFERENCE_WORKERS = 4
H16_CORPUS_SCHEMA_VERSION = "GLD_CRR_P1_H16_CORPUS_V1"
H16_GOLDEN_SCHEMA_VERSION = "GLD_CRR_P1_H16_REFERENCE_GOLDEN_V1"
H16_MANIFEST_SCHEMA_VERSION = "GLD_CRR_P1_H16_MANIFEST_V1"
H16_HOLDOUT_STATUS = "HOLDOUT_SEALED_BEFORE_NATIVE_EXECUTION"
H16_TIMING_STATUS = "H16_NOT_TIMED"
H16_SOURCE_STATUS = "NATIVE_SOURCE_FROZEN_NOT_EXECUTED_AGAINST_H16"
H16_NATIVE_SOURCE_ARTIFACT_SHA256 = (
    "bacd7228201a94650f66d4dcc820d1a93c2aa303ad26b15ddb97fece73cd31d5"
)

_ROOT = Path(__file__).resolve().parent
_HOLDOUT_ROOT = _ROOT / "holdout"
_CORPUS_NAME = "h16_inputs_v0.1.json"
_GOLDEN_NAME = "h16_reference_golden_v0.1.json"
_MANIFEST_PATH = _HOLDOUT_ROOT / "h16_manifest_v0.1.json"

# Filled once, after the offline generator seals the corpus, reference golden,
# and manifest.  Replacement then requires an explicit source change.
H16_MANIFEST_SHA256 = (
    "737cef587b07803fe901ec8f8d64b211ddb8f9df5859599db14dd20e24c350ef"
)


class H16ContractError(ValueError):
    """One deterministic H16 holdout contract failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class H16CaseV1:
    ordinal: int
    case_id: str
    moneyness: str
    dte_days: int
    volatility_band: str
    rate_sign: str
    effective_yield_band: str
    preregistered_terminal_status: str
    preregistered_reason_code: str
    preregistered_early_exercise_detected: bool
    input_sha256: str


@dataclass(frozen=True, slots=True)
class LoadedH16CorpusV1:
    corpus_id: str
    corpus_bytes: bytes
    corpus_file_sha256: str
    document: Mapping[str, object]
    cases: tuple[H16CaseV1, ...]
    inputs: tuple[CrrCallInputsV1, ...]
    input_vector_sha256: str


@dataclass(frozen=True, slots=True)
class H16ReferenceTerminalV1:
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
        if type(self.ordinal) is not int or not 1 <= self.ordinal <= H16_COUNT:
            raise H16ContractError("H16_GOLDEN_TERMINAL_INVALID")
        _require_sha256(self.input_sha256, "H16_GOLDEN_TERMINAL_INVALID")
        if type(self.contract_id) is not str or not self.contract_id:
            raise H16ContractError("H16_GOLDEN_TERMINAL_INVALID")
        numeric = (
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
                or any(type(value) is not int for value in numeric)
                or type(self.early_exercise_detected) is not bool
                or not 100 <= self.iv_ppm <= 5_000_000
                or not 100 <= self.coarse_iv_ppm <= 5_000_000
                or not 100 <= self.fine_iv_ppm <= 5_000_000
                or not 0 <= self.delta_ppm <= 1_000_000
                or not 0 <= self.coarse_delta_ppm <= 1_000_000
                or not 0 <= self.fine_delta_ppm <= 1_000_000
                or any(value < 0 for value in numeric[6:])
                or self.iv_ppm != self.fine_iv_ppm
                or self.delta_ppm != self.fine_delta_ppm
                or abs(self.coarse_iv_ppm - self.fine_iv_ppm) > 100
                or abs(self.coarse_delta_ppm - self.fine_delta_ppm) > 500
                or self.early_exercise_detected
                != bool(
                    self.coarse_early_exercise_nodes
                    or self.fine_early_exercise_nodes
                )
            ):
                raise H16ContractError("H16_GOLDEN_TERMINAL_INVALID")
        elif self.terminal_status == "FAIL":
            if (
                type(self.reason_code) is not str
                or not self.reason_code
                or any(value is not None for value in numeric)
                or self.early_exercise_detected is not False
            ):
                raise H16ContractError("H16_GOLDEN_TERMINAL_INVALID")
        else:
            raise H16ContractError("H16_GOLDEN_TERMINAL_INVALID")

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
class H16ReferenceReceiptV1:
    requested: int
    started: int
    terminal: int
    result_cache_hits: int
    results: tuple[H16ReferenceTerminalV1, ...]
    semantic_output_sha256: str
    timing_status: str = H16_TIMING_STATUS
    holdout_status: str = H16_HOLDOUT_STATUS
    native_executed: bool = False

    def __post_init__(self) -> None:
        for value, expected in (
            (self.requested, H16_COUNT),
            (self.started, H16_COUNT),
            (self.terminal, H16_COUNT),
            (self.result_cache_hits, 0),
        ):
            if (
                _require_exact_int(
                    value,
                    "H16_REFERENCE_RECEIPT_INVALID",
                    minimum=0,
                    maximum=H16_COUNT,
                )
                != expected
            ):
                raise H16ContractError("H16_REFERENCE_RECEIPT_INVALID")
        if (
            type(self.results) is not tuple
            or any(type(item) is not H16ReferenceTerminalV1 for item in self.results)
            or len(self.results) != H16_COUNT
            or tuple(item.ordinal for item in self.results)
            != tuple(range(1, H16_COUNT + 1))
            or self.timing_status != H16_TIMING_STATUS
            or self.holdout_status != H16_HOLDOUT_STATUS
            or self.native_executed is not False
        ):
            raise H16ContractError("H16_REFERENCE_RECEIPT_INVALID")
        _require_sha256(
            self.semantic_output_sha256,
            "H16_REFERENCE_RECEIPT_INVALID",
        )


def canonical_json_bytes(value: object) -> bytes:
    """Return the only accepted H16 JSON byte representation."""

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
        raise H16ContractError("H16_CANONICAL_JSON_INVALID") from error


def _require_exact_keys(
    value: object,
    expected: set[str],
    reason_code: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected:
        raise H16ContractError(reason_code)
    return value


def _require_exact_int(
    value: object,
    reason_code: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if (
        type(value) is not int
        or (minimum is not None and value < minimum)
        or (maximum is not None and value > maximum)
    ):
        raise H16ContractError(reason_code)
    return value


def _require_exact_bool(value: object, reason_code: str) -> bool:
    if type(value) is not bool:
        raise H16ContractError(reason_code)
    return value


def _require_exact_list(
    value: object,
    expected: list[object],
    reason_code: str,
) -> list[object]:
    if (
        type(value) is not list
        or len(value) != len(expected)
        or any(
            type(actual) is not type(expected_item) or actual != expected_item
            for actual, expected_item in zip(value, expected, strict=True)
        )
    ):
        raise H16ContractError(reason_code)
    return value


def _require_terminal_counts(
    value: object,
    reason_code: str,
) -> dict[str, object]:
    counts = _require_exact_keys(
        value,
        {"FAIL", "PASS"},
        reason_code,
    )
    fail_count = _require_exact_int(
        counts["FAIL"],
        reason_code,
        minimum=0,
        maximum=H16_COUNT,
    )
    pass_count = _require_exact_int(
        counts["PASS"],
        reason_code,
        minimum=0,
        maximum=H16_COUNT,
    )
    if (fail_count, pass_count) != (4, 12):
        raise H16ContractError(reason_code)
    return counts


def _require_coverage_contract(value: object) -> dict[str, object]:
    reason_code = "H16_COVERAGE_CONTRACT_INVALID"
    coverage = _require_exact_keys(
        value,
        {
            "dte_days_inclusive",
            "effective_yield_bands",
            "early_exercise_values",
            "moneyness",
            "rate_signs",
            "reference_terminal_counts",
            "volatility_bands",
        },
        reason_code,
    )
    _require_exact_list(
        coverage["dte_days_inclusive"],
        [45, 360],
        reason_code,
    )
    _require_exact_list(
        coverage["effective_yield_bands"],
        ["LOW", "HIGH"],
        reason_code,
    )
    _require_exact_list(
        coverage["early_exercise_values"],
        [False, True],
        reason_code,
    )
    _require_exact_list(
        coverage["moneyness"],
        ["ITM", "ATM", "OTM"],
        reason_code,
    )
    _require_exact_list(
        coverage["rate_signs"],
        ["NEGATIVE", "POSITIVE"],
        reason_code,
    )
    _require_terminal_counts(
        coverage["reference_terminal_counts"],
        reason_code,
    )
    _require_exact_list(
        coverage["volatility_bands"],
        ["LOW", "MEDIUM", "HIGH"],
        reason_code,
    )
    return coverage


def _require_sha256(value: object, reason_code: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise H16ContractError(reason_code)
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
        raise H16ContractError("H16_STATIC_ARTIFACT_INVALID") from error
    if type(document) is not dict or raw != canonical_json_bytes(document):
        raise H16ContractError("H16_CANONICAL_JSON_INVALID")
    return raw, document


def load_h16_manifest() -> dict[str, object]:
    raw, document = _read_canonical_json(_MANIFEST_PATH)
    if sha256(raw).hexdigest() != H16_MANIFEST_SHA256:
        raise H16ContractError("H16_MANIFEST_HASH_MISMATCH")
    root = _require_exact_keys(
        document,
        {
            "files",
            "native_execution",
            "native_source_artifact_sha256",
            "reference_model_sha256",
            "reference_model_source_artifact_sha256",
            "schema_version",
            "source_status",
            "status",
            "timing_status",
        },
        "H16_MANIFEST_INVALID",
    )
    execution = _require_exact_keys(
        root["native_execution"],
        {"allowed", "executed_against_h16"},
        "H16_MANIFEST_INVALID",
    )
    files = _require_exact_keys(
        root["files"],
        {_CORPUS_NAME, _GOLDEN_NAME},
        "H16_MANIFEST_INVALID",
    )
    allowed = _require_exact_bool(
        execution["allowed"],
        "H16_MANIFEST_INVALID",
    )
    executed_against_h16 = _require_exact_bool(
        execution["executed_against_h16"],
        "H16_MANIFEST_INVALID",
    )
    if (
        root["schema_version"] != H16_MANIFEST_SCHEMA_VERSION
        or root["status"] != H16_HOLDOUT_STATUS
        or root["timing_status"] != H16_TIMING_STATUS
        or root["source_status"] != H16_SOURCE_STATUS
        or root["native_source_artifact_sha256"]
        != H16_NATIVE_SOURCE_ARTIFACT_SHA256
        or root["reference_model_sha256"] != MODEL_SHA256
        or root["reference_model_source_artifact_sha256"]
        != MODEL_SOURCE_ARTIFACT_SHA256
        or allowed is not False
        or executed_against_h16 is not False
    ):
        raise H16ContractError("H16_MANIFEST_INVALID")
    for value in files.values():
        _require_sha256(value, "H16_MANIFEST_INVALID")
    return document


def _load_manifest_bound_json(
    file_name: str,
) -> tuple[bytes, dict[str, object]]:
    manifest = load_h16_manifest()
    expected_sha256 = manifest["files"].get(file_name)
    if expected_sha256 is None:
        raise H16ContractError("H16_STATIC_ARTIFACT_UNREGISTERED")
    raw, document = _read_canonical_json(_HOLDOUT_ROOT / file_name)
    if sha256(raw).hexdigest() != expected_sha256:
        raise H16ContractError("H16_STATIC_ARTIFACT_HASH_MISMATCH")
    return raw, document


_PIT_KEYS = {
    "as_of_utc_ns",
    "borrow_assumption_sha256",
    "borrow_yield_ppm",
    "distribution_assumption_sha256",
    "expense_yield_ppm",
    "rate_curve_sha256",
    "risk_free_rate_ppm",
}
_CALL_KEYS = {
    "contract_id",
    "contract_sha256",
    "expiry_utc_ns",
    "option_mid_nano_usd",
    "option_quote_sha256",
    "option_snapshot_sha256",
    "pit_inputs",
    "spot_nano_usd",
    "strike_nano_usd",
    "tick_nano_usd",
    "underlying_top_sha256",
    "valuation_utc_ns",
}


def _typed_call_input(document: object) -> CrrCallInputsV1:
    values = _require_exact_keys(
        document,
        _CALL_KEYS,
        "H16_CALL_INPUT_SCHEMA_INVALID",
    )
    pit_values = _require_exact_keys(
        values["pit_inputs"],
        _PIT_KEYS,
        "H16_PIT_INPUT_SCHEMA_INVALID",
    )
    for key in (
        "as_of_utc_ns",
        "borrow_yield_ppm",
        "expense_yield_ppm",
        "risk_free_rate_ppm",
    ):
        _require_exact_int(pit_values[key], "H16_PIT_INPUT_TYPE_INVALID")
    for key in (
        "expiry_utc_ns",
        "option_mid_nano_usd",
        "spot_nano_usd",
        "strike_nano_usd",
        "tick_nano_usd",
        "valuation_utc_ns",
    ):
        _require_exact_int(values[key], "H16_CALL_INPUT_TYPE_INVALID")
    for key in (
        "borrow_assumption_sha256",
        "distribution_assumption_sha256",
        "rate_curve_sha256",
    ):
        _require_sha256(pit_values[key], "H16_PIT_INPUT_TYPE_INVALID")
    for key in (
        "contract_sha256",
        "option_quote_sha256",
        "option_snapshot_sha256",
        "underlying_top_sha256",
    ):
        _require_sha256(values[key], "H16_CALL_INPUT_TYPE_INVALID")
    if type(values["contract_id"]) is not str:
        raise H16ContractError("H16_CALL_INPUT_TYPE_INVALID")
    try:
        pit_inputs = CrrPitInputsV1(**pit_values)  # type: ignore[arg-type]
        return CrrCallInputsV1(
            option_snapshot_sha256=values["option_snapshot_sha256"],
            contract_id=values["contract_id"],
            contract_sha256=values["contract_sha256"],
            underlying_top_sha256=values["underlying_top_sha256"],
            option_quote_sha256=values["option_quote_sha256"],
            pit_inputs=pit_inputs,
            spot_nano_usd=values["spot_nano_usd"],
            strike_nano_usd=values["strike_nano_usd"],
            option_mid_nano_usd=values["option_mid_nano_usd"],
            tick_nano_usd=values["tick_nano_usd"],
            valuation_utc_ns=values["valuation_utc_ns"],
            expiry_utc_ns=values["expiry_utc_ns"],
        )  # type: ignore[arg-type]
    except NormalizationError as error:
        raise H16ContractError("H16_TYPED_INPUT_INVALID") from error


def _input_vector_sha256(inputs: Sequence[CrrCallInputsV1]) -> str:
    return sha256(
        canonical_json_bytes([item.input_sha256 for item in inputs])
    ).hexdigest()


def load_h16_corpus_document(
    document: object,
    *,
    corpus_bytes: bytes,
) -> LoadedH16CorpusV1:
    """Validate already-decoded H16 bytes without consulting the manifest."""

    root = _require_exact_keys(
        document,
        {
            "canonical_order",
            "corpus_id",
            "coverage_contract",
            "expected_call_count",
            "input_vector_sha256",
            "inputs",
            "native_source_artifact_sha256",
            "reference_model_sha256",
            "schema_version",
            "source_status",
            "status",
            "timing_status",
        },
        "H16_CORPUS_SCHEMA_INVALID",
    )
    if corpus_bytes != canonical_json_bytes(document):
        raise H16ContractError("H16_CANONICAL_JSON_INVALID")
    expected_call_count = _require_exact_int(
        root["expected_call_count"],
        "H16_CORPUS_SCHEMA_INVALID",
        minimum=H16_COUNT,
        maximum=H16_COUNT,
    )
    _require_exact_list(
        root["canonical_order"],
        ["ordinal"],
        "H16_CORPUS_SCHEMA_INVALID",
    )
    _require_coverage_contract(root["coverage_contract"])
    if (
        root["schema_version"] != H16_CORPUS_SCHEMA_VERSION
        or root["corpus_id"] != "H16"
        or expected_call_count != H16_COUNT
        or root["status"] != H16_HOLDOUT_STATUS
        or root["timing_status"] != H16_TIMING_STATUS
        or root["source_status"] != H16_SOURCE_STATUS
        or root["native_source_artifact_sha256"]
        != H16_NATIVE_SOURCE_ARTIFACT_SHA256
        or root["reference_model_sha256"] != MODEL_SHA256
    ):
        raise H16ContractError("H16_CORPUS_SCHEMA_INVALID")
    documents = root["inputs"]
    if type(documents) is not list or len(documents) != H16_COUNT:
        raise H16ContractError("H16_CALL_COUNT_INVALID")
    ordinals = tuple(
        item.get("ordinal") if type(item) is dict else None
        for item in documents
    )
    if (
        any(type(ordinal) is not int for ordinal in ordinals)
        or ordinals != tuple(range(1, H16_COUNT + 1))
    ):
        raise H16ContractError("H16_CANONICAL_ORDER_INVALID")

    cases: list[H16CaseV1] = []
    inputs: list[CrrCallInputsV1] = []
    for document_case in documents:
        values = _require_exact_keys(
            document_case,
            {
                "call_input",
                "case_id",
                "dte_days",
                "effective_yield_band",
                "input_sha256",
                "moneyness",
                "ordinal",
                "preregistered_early_exercise_detected",
                "preregistered_reason_code",
                "preregistered_terminal_status",
                "rate_sign",
                "volatility_band",
            },
            "H16_CASE_SCHEMA_INVALID",
        )
        ordinal = _require_exact_int(
            values["ordinal"],
            "H16_CASE_TYPE_INVALID",
            minimum=1,
            maximum=H16_COUNT,
        )
        dte_days = _require_exact_int(
            values["dte_days"],
            "H16_CASE_TYPE_INVALID",
            minimum=45,
            maximum=360,
        )
        string_keys = (
            "case_id",
            "effective_yield_band",
            "moneyness",
            "preregistered_reason_code",
            "preregistered_terminal_status",
            "rate_sign",
            "volatility_band",
        )
        if any(type(values[key]) is not str for key in string_keys) or type(
            values["preregistered_early_exercise_detected"]
        ) is not bool:
            raise H16ContractError("H16_CASE_TYPE_INVALID")
        declared_input_sha256 = _require_sha256(
            values["input_sha256"],
            "H16_DECLARED_INPUT_HASH_MISMATCH",
        )
        call_input = _typed_call_input(values["call_input"])
        if declared_input_sha256 != call_input.input_sha256:
            raise H16ContractError("H16_DECLARED_INPUT_HASH_MISMATCH")
        actual_moneyness = (
            "ITM"
            if call_input.spot_nano_usd > call_input.strike_nano_usd
            else (
                "OTM"
                if call_input.spot_nano_usd < call_input.strike_nano_usd
                else "ATM"
            )
        )
        actual_rate_sign = (
            "NEGATIVE" if call_input.risk_free_rate_ppm < 0 else "POSITIVE"
        )
        effective_yield_ppm = (
            call_input.expense_yield_ppm + call_input.borrow_yield_ppm
        )
        actual_effective_yield_band = (
            "HIGH" if effective_yield_ppm >= 80_000 else "LOW"
        )
        expected_case_id = (
            f"H16-{ordinal:03d}-{actual_moneyness}-"
            f"{dte_days}D-{values['volatility_band']}IV"
        )
        expected_contract_id = (
            f"H16_CASE_{ordinal:02d}_{actual_moneyness}_{dte_days}D"
        )
        if (
            not 45 <= dte_days <= 360
            or call_input.expiry_utc_ns - call_input.valuation_utc_ns
            != dte_days * 86_400_000_000_000
            or values["case_id"] != expected_case_id
            or call_input.contract_id != expected_contract_id
            or values["moneyness"] != actual_moneyness
            or values["volatility_band"] not in {"LOW", "MEDIUM", "HIGH"}
            or values["rate_sign"] != actual_rate_sign
            or values["effective_yield_band"]
            != actual_effective_yield_band
            or values["preregistered_terminal_status"] not in {"PASS", "FAIL"}
            or (
                values["preregistered_terminal_status"] == "PASS"
                and values["preregistered_reason_code"] != "PASS"
            )
            or (
                values["preregistered_terminal_status"] == "FAIL"
                and values["preregistered_early_exercise_detected"] is not False
            )
        ):
            raise H16ContractError("H16_CASE_SEMANTICS_INVALID")
        cases.append(
            H16CaseV1(
                ordinal=ordinal,
                case_id=values["case_id"],
                moneyness=values["moneyness"],
                dte_days=dte_days,
                volatility_band=values["volatility_band"],
                rate_sign=values["rate_sign"],
                effective_yield_band=values["effective_yield_band"],
                preregistered_terminal_status=values[
                    "preregistered_terminal_status"
                ],
                preregistered_reason_code=values[
                    "preregistered_reason_code"
                ],
                preregistered_early_exercise_detected=values[
                    "preregistered_early_exercise_detected"
                ],
                input_sha256=declared_input_sha256,
            )
        )
        inputs.append(call_input)
    case_tuple = tuple(cases)
    input_tuple = tuple(inputs)
    if (
        len({item.case_id for item in case_tuple}) != H16_COUNT
        or len({item.contract_id for item in input_tuple}) != H16_COUNT
        or len({item.input_sha256 for item in input_tuple}) != H16_COUNT
    ):
        raise H16ContractError("H16_INPUT_IDENTITY_DUPLICATE")
    actual_vector_sha256 = _input_vector_sha256(input_tuple)
    if root["input_vector_sha256"] != actual_vector_sha256:
        raise H16ContractError("H16_INPUT_VECTOR_HASH_MISMATCH")
    observed_counts = {
        terminal_status: sum(
            case.preregistered_terminal_status == terminal_status
            for case in case_tuple
        )
        for terminal_status in ("FAIL", "PASS")
    }
    if (
        observed_counts != {"FAIL": 4, "PASS": 12}
        or {item.moneyness for item in case_tuple} != {"ITM", "ATM", "OTM"}
        or {item.volatility_band for item in case_tuple}
        != {"LOW", "MEDIUM", "HIGH"}
        or {item.rate_sign for item in case_tuple}
        != {"NEGATIVE", "POSITIVE"}
        or {item.effective_yield_band for item in case_tuple} != {"LOW", "HIGH"}
        or {item.dte_days for item in case_tuple} < {45, 360}
        or {
            item.preregistered_early_exercise_detected
            for item in case_tuple
            if item.preregistered_terminal_status == "PASS"
        }
        != {False, True}
    ):
        raise H16ContractError("H16_COVERAGE_CONTRACT_INVALID")
    return LoadedH16CorpusV1(
        corpus_id="H16",
        corpus_bytes=corpus_bytes,
        corpus_file_sha256=sha256(corpus_bytes).hexdigest(),
        document=_freeze_json(document),  # type: ignore[arg-type]
        cases=case_tuple,
        inputs=input_tuple,
        input_vector_sha256=actual_vector_sha256,
    )


def load_h16_corpus() -> LoadedH16CorpusV1:
    raw, document = _load_manifest_bound_json(_CORPUS_NAME)
    return load_h16_corpus_document(document, corpus_bytes=raw)


def _failure_terminal(
    ordinal: int,
    inputs: CrrCallInputsV1,
    reason_code: str,
) -> H16ReferenceTerminalV1:
    return H16ReferenceTerminalV1(
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


def _reference_worker(
    payload: tuple[int, CrrCallInputsV1],
) -> dict[str, object]:
    ordinal, inputs = payload
    try:
        result = compute_american_call_delta(
            inputs,
            expected_model_sha256=MODEL_SHA256,
        )
        if (
            not is_verified_crr_delta_result(result)
            or result.input_sha256 != inputs.input_sha256
            or result.contract_id != inputs.contract_id
        ):
            terminal = _failure_terminal(
                ordinal,
                inputs,
                "UNVERIFIED_DELTA_RESULT",
            )
        else:
            terminal = H16ReferenceTerminalV1(
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
                fine_price_residual_nano_usd=(
                    result.fine_price_residual_nano_usd
                ),
                coarse_early_exercise_nodes=(
                    result.coarse_early_exercise_nodes
                ),
                fine_early_exercise_nodes=result.fine_early_exercise_nodes,
                early_exercise_detected=bool(
                    result.coarse_early_exercise_nodes
                    or result.fine_early_exercise_nodes
                ),
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
) -> H16ReferenceReceiptV1:
    if (
        type(inputs) is not tuple
        or len(inputs) != H16_COUNT
        or any(type(item) is not CrrCallInputsV1 for item in inputs)
        or type(workers) is not int
        or workers != H16_REFERENCE_WORKERS
    ):
        raise H16ContractError("H16_REFERENCE_RUN_POLICY_INVALID")
    payloads = tuple(enumerate(inputs, start=1))
    with ProcessPoolExecutor(
        max_workers=H16_REFERENCE_WORKERS,
        mp_context=get_context("spawn"),
    ) as pool:
        documents = tuple(pool.map(_reference_worker, payloads, chunksize=1))
    results = tuple(H16ReferenceTerminalV1(**item) for item in documents)
    semantic_output_sha256 = sha256(
        canonical_json_bytes([item.as_dict() for item in results])
    ).hexdigest()
    return H16ReferenceReceiptV1(
        requested=H16_COUNT,
        started=H16_COUNT,
        terminal=H16_COUNT,
        result_cache_hits=0,
        results=results,
        semantic_output_sha256=semantic_output_sha256,
    )


def load_h16_reference_golden_document(
    document: object,
) -> dict[str, object]:
    root = _require_exact_keys(
        document,
        {
            "corpus_file_sha256",
            "corpus_id",
            "input_vector_sha256",
            "native_source_artifact_sha256",
            "reference_model_sha256",
            "reference_model_source_artifact_sha256",
            "results",
            "schema_version",
            "semantic_output_sha256",
            "source_status",
            "status",
            "terminal_counts",
            "timing_status",
        },
        "H16_GOLDEN_SCHEMA_INVALID",
    )
    corpus = load_h16_corpus()
    if root["corpus_file_sha256"] != corpus.corpus_file_sha256:
        raise H16ContractError("H16_GOLDEN_CORPUS_HASH_MISMATCH")
    results_document = root["results"]
    if type(results_document) is not list or len(results_document) != H16_COUNT:
        raise H16ContractError("H16_GOLDEN_COUNT_INVALID")
    terminal_counts = _require_terminal_counts(
        root["terminal_counts"],
        "H16_GOLDEN_SCHEMA_INVALID",
    )
    if (
        root["schema_version"] != H16_GOLDEN_SCHEMA_VERSION
        or root["corpus_id"] != "H16"
        or root["input_vector_sha256"] != corpus.input_vector_sha256
        or root["native_source_artifact_sha256"]
        != H16_NATIVE_SOURCE_ARTIFACT_SHA256
        or root["reference_model_sha256"] != MODEL_SHA256
        or root["reference_model_source_artifact_sha256"]
        != MODEL_SOURCE_ARTIFACT_SHA256
        or root["status"] != H16_HOLDOUT_STATUS
        or root["timing_status"] != H16_TIMING_STATUS
        or root["source_status"] != H16_SOURCE_STATUS
    ):
        raise H16ContractError("H16_GOLDEN_SCHEMA_INVALID")
    results = tuple(
        H16ReferenceTerminalV1(**item)  # type: ignore[arg-type]
        for item in results_document
    )
    if tuple(item.ordinal for item in results) != tuple(
        range(1, H16_COUNT + 1)
    ):
        raise H16ContractError("H16_GOLDEN_ORDER_INVALID")
    actual_input_bindings = tuple(
        (item.ordinal, item.input_sha256, item.contract_id)
        for item in results
    )
    expected_input_bindings = tuple(
        (ordinal, item.input_sha256, item.contract_id)
        for ordinal, item in enumerate(corpus.inputs, start=1)
    )
    if actual_input_bindings != expected_input_bindings:
        raise H16ContractError("H16_GOLDEN_INPUT_MISMATCH")
    expected_preregistration = tuple(
        (
            case.preregistered_terminal_status,
            case.preregistered_reason_code,
            case.preregistered_early_exercise_detected,
        )
        for case in corpus.cases
    )
    actual_preregistration = tuple(
        (
            item.terminal_status,
            item.reason_code,
            item.early_exercise_detected,
        )
        for item in results
    )
    if actual_preregistration != expected_preregistration:
        raise H16ContractError("H16_GOLDEN_PREREGISTRATION_MISMATCH")
    actual_counts = {
        status: sum(item.terminal_status == status for item in results)
        for status in ("FAIL", "PASS")
    }
    pass_volatility_bands = {
        case.volatility_band
        for case, result in zip(corpus.cases, results, strict=True)
        if result.terminal_status == "PASS"
        and type(result.iv_ppm) is int
        and (
            (case.volatility_band == "LOW" and result.iv_ppm <= 250_000)
            or (
                case.volatility_band == "MEDIUM"
                and 250_000 < result.iv_ppm < 500_000
            )
            or (case.volatility_band == "HIGH" and result.iv_ppm >= 500_000)
        )
    }
    if actual_counts != terminal_counts:
        raise H16ContractError("H16_GOLDEN_TERMINAL_COUNT_MISMATCH")
    if pass_volatility_bands != {"LOW", "MEDIUM", "HIGH"}:
        raise H16ContractError("H16_GOLDEN_VOLATILITY_COVERAGE_INVALID")
    semantic_output_sha256 = sha256(
        canonical_json_bytes([item.as_dict() for item in results])
    ).hexdigest()
    if root["semantic_output_sha256"] != semantic_output_sha256:
        raise H16ContractError("H16_GOLDEN_SEMANTIC_HASH_MISMATCH")
    return document  # type: ignore[return-value]


def load_h16_reference_golden() -> dict[str, object]:
    _, document = _load_manifest_bound_json(_GOLDEN_NAME)
    return load_h16_reference_golden_document(document)


def replay_h16_reference(
    *,
    workers: int = H16_REFERENCE_WORKERS,
) -> H16ReferenceReceiptV1:
    """Replay all H16 reference terminals; explicitly not a timed path."""

    corpus = load_h16_corpus()
    receipt = _run_reference_inputs_parallel(corpus.inputs, workers=workers)
    golden = load_h16_reference_golden()
    if (
        tuple(item.as_dict() for item in receipt.results)
        != tuple(golden["results"])
        or receipt.semantic_output_sha256 != golden["semantic_output_sha256"]
    ):
        raise H16ContractError("H16_REFERENCE_GOLDEN_MISMATCH")
    return receipt


__all__ = (
    "H16_COUNT",
    "H16_MANIFEST_SHA256",
    "H16_NATIVE_SOURCE_ARTIFACT_SHA256",
    "H16ContractError",
    "H16ReferenceReceiptV1",
    "H16ReferenceTerminalV1",
    "LoadedH16CorpusV1",
    "canonical_json_bytes",
    "load_h16_corpus",
    "load_h16_corpus_document",
    "load_h16_manifest",
    "load_h16_reference_golden",
    "load_h16_reference_golden_document",
    "replay_h16_reference",
)
