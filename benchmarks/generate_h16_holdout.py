"""One-shot, offline-only initial sealer for the H16 CRR holdout.

The generator uses direct integer/hash-bound inputs and the Python reference
engine only.  It refuses to overwrite any sealed H16 artifact.  H16 must not
be regenerated or tuned in response to native output, and it is never timed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path

from gld_research_core.crr_delta import (
    MODEL_SHA256,
    MODEL_SOURCE_ARTIFACT_SHA256,
    CrrCallInputsV1,
    CrrPitInputsV1,
)

from .h16_contract import (
    H16_CORPUS_SCHEMA_VERSION,
    H16_COUNT,
    H16_GOLDEN_SCHEMA_VERSION,
    H16_HOLDOUT_STATUS,
    H16_MANIFEST_SCHEMA_VERSION,
    H16_NATIVE_SOURCE_ARTIFACT_SHA256,
    H16_REFERENCE_WORKERS,
    H16_SOURCE_STATUS,
    H16_TIMING_STATUS,
    _input_vector_sha256,
    _run_reference_inputs_parallel,
    canonical_json_bytes,
    load_h16_corpus_document,
)


_DAY_NS = 86_400_000_000_000
_VALUATION_UTC_NS = 1_789_430_400_000_000_000
_TICK_NANO_USD = 10_000_000
_HOLDOUT_ROOT = Path(__file__).resolve().parent / "holdout"
_CORPUS_PATH = _HOLDOUT_ROOT / "h16_inputs_v0.1.json"
_GOLDEN_PATH = _HOLDOUT_ROOT / "h16_reference_golden_v0.1.json"
_MANIFEST_PATH = _HOLDOUT_ROOT / "h16_manifest_v0.1.json"


@dataclass(frozen=True, slots=True)
class _CaseSpec:
    ordinal: int
    moneyness: str
    dte_days: int
    volatility_band: str
    rate_ppm: int
    expense_yield_ppm: int
    borrow_yield_ppm: int
    spot_nano_usd: int
    strike_nano_usd: int
    option_mid_nano_usd: int
    terminal_status: str
    reason_code: str
    early_exercise_detected: bool

    @property
    def case_id(self) -> str:
        return (
            f"H16-{self.ordinal:03d}-{self.moneyness}-"
            f"{self.dte_days}D-{self.volatility_band}IV"
        )

    @property
    def rate_sign(self) -> str:
        return "NEGATIVE" if self.rate_ppm < 0 else "POSITIVE"

    @property
    def effective_yield_band(self) -> str:
        total_yield = self.expense_yield_ppm + self.borrow_yield_ppm
        return "HIGH" if total_yield >= 80_000 else "LOW"


# Midpoints are exact nano-USD integers frozen after reference-only exploration.
# They are not recomputed from floating point during corpus generation.
_CASE_SPECS = (
    _CaseSpec(1, "ITM", 45, "LOW", 20_000, 0, 0, 105_000_000_000, 100_000_000_000, 5_718_671_281, "PASS", "PASS", False),
    _CaseSpec(2, "ATM", 60, "MEDIUM", -10_000, 0, 0, 100_000_000_000, 100_000_000_000, 4_771_626_660, "PASS", "PASS", True),
    _CaseSpec(3, "OTM", 90, "HIGH", 35_000, 0, 0, 100_000_000_000, 115_000_000_000, 4_997_093_950, "PASS", "PASS", False),
    _CaseSpec(4, "ITM", 120, "MEDIUM", 15_000, 70_000, 30_000, 110_000_000_000, 100_000_000_000, 11_810_941_185, "PASS", "PASS", True),
    _CaseSpec(5, "ATM", 180, "LOW", -5_000, 50_000, 30_000, 100_000_000_000, 100_000_000_000, 4_107_003_168, "PASS", "PASS", True),
    _CaseSpec(6, "OTM", 270, "MEDIUM", 40_000, 60_000, 40_000, 100_000_000_000, 120_000_000_000, 4_228_905_335, "PASS", "PASS", True),
    _CaseSpec(7, "ITM", 360, "HIGH", -15_000, 0, 0, 115_000_000_000, 100_000_000_000, 30_643_720_819, "PASS", "PASS", True),
    _CaseSpec(8, "ATM", 45, "HIGH", 30_000, 100_000, 50_000, 100_000_000_000, 100_000_000_000, 7_029_447_448, "PASS", "PASS", True),
    _CaseSpec(9, "OTM", 120, "LOW", -20_000, 0, 0, 100_000_000_000, 110_000_000_000, 1_236_501_838, "PASS", "PASS", True),
    _CaseSpec(10, "ITM", 240, "MEDIUM", 20_000, 100_000, 50_000, 115_000_000_000, 100_000_000_000, 15_945_557_412, "PASS", "PASS", True),
    _CaseSpec(11, "ATM", 300, "MEDIUM", -10_000, 60_000, 30_000, 100_000_000_000, 100_000_000_000, 9_519_229_642, "PASS", "PASS", True),
    _CaseSpec(12, "OTM", 360, "HIGH", 50_000, 0, 0, 100_000_000_000, 115_000_000_000, 18_109_175_475, "PASS", "PASS", False),
    _CaseSpec(13, "ITM", 45, "LOW", 20_000, 0, 0, 150_000_000_000, 100_000_000_000, 50_246_271_595, "FAIL", "IV_UNIDENTIFIABLE", False),
    _CaseSpec(14, "OTM", 180, "HIGH", 20_000, 0, 0, 100_000_000_000, 105_000_000_000, 26_051_762_799, "FAIL", "TREE_NOT_CONVERGED", False),
    _CaseSpec(15, "ATM", 90, "MEDIUM", 20_000, 0, 0, 100_000_000_000, 100_000_000_000, 150_000_000_000, "FAIL", "IV_NO_BRACKET", False),
    _CaseSpec(16, "ITM", 180, "HIGH", 20_000, 0, 0, 100_000_000_000, 95_000_000_000, 29_699_207_733, "FAIL", "TREE_NOT_CONVERGED", False),
)


def _binding_hash(case_id: str, field: str) -> str:
    return sha256(f"{case_id}:{field}".encode("ascii")).hexdigest()


def _build_input(spec: _CaseSpec) -> CrrCallInputsV1:
    pit_inputs = CrrPitInputsV1(
        as_of_utc_ns=_VALUATION_UTC_NS,
        risk_free_rate_ppm=spec.rate_ppm,
        expense_yield_ppm=spec.expense_yield_ppm,
        borrow_yield_ppm=spec.borrow_yield_ppm,
        rate_curve_sha256=_binding_hash(spec.case_id, "rate-curve"),
        distribution_assumption_sha256=_binding_hash(
            spec.case_id,
            "distribution-assumption",
        ),
        borrow_assumption_sha256=_binding_hash(
            spec.case_id,
            "borrow-assumption",
        ),
    )
    return CrrCallInputsV1(
        option_snapshot_sha256=_binding_hash(spec.case_id, "option-snapshot"),
        contract_id=f"H16_CASE_{spec.ordinal:02d}_{spec.moneyness}_{spec.dte_days}D",
        contract_sha256=_binding_hash(spec.case_id, "contract"),
        underlying_top_sha256=_binding_hash(spec.case_id, "underlying-top"),
        option_quote_sha256=_binding_hash(spec.case_id, "option-quote"),
        pit_inputs=pit_inputs,
        spot_nano_usd=spec.spot_nano_usd,
        strike_nano_usd=spec.strike_nano_usd,
        option_mid_nano_usd=spec.option_mid_nano_usd,
        tick_nano_usd=_TICK_NANO_USD,
        valuation_utc_ns=_VALUATION_UTC_NS,
        expiry_utc_ns=_VALUATION_UTC_NS + spec.dte_days * _DAY_NS,
    )


def _build_corpus_document() -> tuple[dict[str, object], tuple[CrrCallInputsV1, ...]]:
    if len(_CASE_SPECS) != H16_COUNT:
        raise RuntimeError("H16_CASE_COUNT_INVALID")
    inputs = tuple(_build_input(spec) for spec in _CASE_SPECS)
    input_vector_sha256 = _input_vector_sha256(inputs)
    document = {
        "canonical_order": ["ordinal"],
        "corpus_id": "H16",
        "coverage_contract": {
            "dte_days_inclusive": [45, 360],
            "effective_yield_bands": ["LOW", "HIGH"],
            "early_exercise_values": [False, True],
            "moneyness": ["ITM", "ATM", "OTM"],
            "rate_signs": ["NEGATIVE", "POSITIVE"],
            "reference_terminal_counts": {"FAIL": 4, "PASS": 12},
            "volatility_bands": ["LOW", "MEDIUM", "HIGH"],
        },
        "expected_call_count": H16_COUNT,
        "input_vector_sha256": input_vector_sha256,
        "inputs": [
            {
                "call_input": asdict(call_input),
                "case_id": spec.case_id,
                "dte_days": spec.dte_days,
                "effective_yield_band": spec.effective_yield_band,
                "input_sha256": call_input.input_sha256,
                "moneyness": spec.moneyness,
                "ordinal": spec.ordinal,
                "preregistered_early_exercise_detected": (
                    spec.early_exercise_detected
                ),
                "preregistered_reason_code": spec.reason_code,
                "preregistered_terminal_status": spec.terminal_status,
                "rate_sign": spec.rate_sign,
                "volatility_band": spec.volatility_band,
            }
            for spec, call_input in zip(_CASE_SPECS, inputs, strict=True)
        ],
        "native_source_artifact_sha256": H16_NATIVE_SOURCE_ARTIFACT_SHA256,
        "reference_model_sha256": MODEL_SHA256,
        "schema_version": H16_CORPUS_SCHEMA_VERSION,
        "source_status": H16_SOURCE_STATUS,
        "status": H16_HOLDOUT_STATUS,
        "timing_status": H16_TIMING_STATUS,
    }
    return document, inputs


def _refuse_reseal() -> None:
    existing = tuple(
        path.name
        for path in (_CORPUS_PATH, _GOLDEN_PATH, _MANIFEST_PATH)
        if path.exists()
    )
    if existing:
        raise RuntimeError(
            "H16_ALREADY_SEALED_DO_NOT_REGENERATE_AFTER_NATIVE_EXECUTION:"
            + ",".join(existing)
        )


def seal_h16_initial_holdout() -> dict[str, str]:
    """Create H16 exactly once, using reference semantics and no timed path."""

    _refuse_reseal()
    _HOLDOUT_ROOT.mkdir(parents=True, exist_ok=True)
    corpus_document, inputs = _build_corpus_document()
    corpus_bytes = canonical_json_bytes(corpus_document)
    corpus = load_h16_corpus_document(
        corpus_document,
        corpus_bytes=corpus_bytes,
    )
    receipt = _run_reference_inputs_parallel(
        inputs,
        workers=H16_REFERENCE_WORKERS,
    )
    observed = tuple(
        (
            item.terminal_status,
            item.reason_code,
            item.early_exercise_detected,
        )
        for item in receipt.results
    )
    preregistered = tuple(
        (
            spec.terminal_status,
            spec.reason_code,
            spec.early_exercise_detected,
        )
        for spec in _CASE_SPECS
    )
    if observed != preregistered:
        raise RuntimeError(
            "H16_REFERENCE_DID_NOT_MATCH_PREREGISTRATION:"
            + repr(tuple(zip(preregistered, observed, strict=True)))
        )
    corpus_sha256 = sha256(corpus_bytes).hexdigest()
    golden_document = {
        "corpus_file_sha256": corpus_sha256,
        "corpus_id": "H16",
        "input_vector_sha256": corpus.input_vector_sha256,
        "native_source_artifact_sha256": H16_NATIVE_SOURCE_ARTIFACT_SHA256,
        "reference_model_sha256": MODEL_SHA256,
        "reference_model_source_artifact_sha256": (
            MODEL_SOURCE_ARTIFACT_SHA256
        ),
        "results": [item.as_dict() for item in receipt.results],
        "schema_version": H16_GOLDEN_SCHEMA_VERSION,
        "semantic_output_sha256": receipt.semantic_output_sha256,
        "source_status": H16_SOURCE_STATUS,
        "status": H16_HOLDOUT_STATUS,
        "terminal_counts": {"FAIL": 4, "PASS": 12},
        "timing_status": H16_TIMING_STATUS,
    }
    golden_bytes = canonical_json_bytes(golden_document)
    golden_sha256 = sha256(golden_bytes).hexdigest()
    manifest_document = {
        "files": {
            _CORPUS_PATH.name: corpus_sha256,
            _GOLDEN_PATH.name: golden_sha256,
        },
        "native_execution": {
            "allowed": False,
            "executed_against_h16": False,
        },
        "native_source_artifact_sha256": H16_NATIVE_SOURCE_ARTIFACT_SHA256,
        "reference_model_sha256": MODEL_SHA256,
        "reference_model_source_artifact_sha256": (
            MODEL_SOURCE_ARTIFACT_SHA256
        ),
        "schema_version": H16_MANIFEST_SCHEMA_VERSION,
        "source_status": H16_SOURCE_STATUS,
        "status": H16_HOLDOUT_STATUS,
        "timing_status": H16_TIMING_STATUS,
    }
    manifest_bytes = canonical_json_bytes(manifest_document)
    _CORPUS_PATH.write_bytes(corpus_bytes)
    _GOLDEN_PATH.write_bytes(golden_bytes)
    _MANIFEST_PATH.write_bytes(manifest_bytes)
    return {
        "corpus_sha256": corpus_sha256,
        "golden_sha256": golden_sha256,
        "manifest_sha256": sha256(manifest_bytes).hexdigest(),
        "reference_semantic_output_sha256": receipt.semantic_output_sha256,
    }


def main() -> None:
    print(json.dumps(seal_h16_initial_holdout(), sort_keys=True))


if __name__ == "__main__":
    main()
