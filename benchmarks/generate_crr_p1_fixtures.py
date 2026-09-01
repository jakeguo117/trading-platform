"""Offline-only generator for the sealed CRR P1 benchmark bytes.

This module is never imported by :mod:`benchmarks.crr_p1_contract`.  It uses
synthetic Black-Scholes prices only to create economically coherent quote
midpoints, then runs the public American CRR reference engine for every static
golden terminal.  It performs no network, provider, broker, or order action.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path

from gld_research_core.bcs import (
    BcsFeeScheduleV1,
    bind_lc0_selection_for_research,
)
from gld_research_core.crr_delta import (
    MODEL_SHA256,
    CrrPitInputsV1,
    bind_crr_call_inputs_from_snapshot,
)
from gld_research_core.facts import (
    OptionContractV1,
    OptionQuoteSnapshotV1,
    OptionQuoteV1,
    SignalSnapshotV1,
    TopOfBookV1,
)

from .crr_p1_contract import (
    E1_SCHEMA_VERSION,
    FIXTURE_COUNT,
    FIXTURE_SCHEMA_VERSION,
    GOLDEN_SCHEMA_VERSION,
    RAW_SAMPLE_SCHEMA_VERSION,
    REFERENCE_WORKERS,
    STATIC_MANIFEST_SCHEMA_VERSION,
    WARMUP_RUNS,
    _corpus_semantic_sha256,
    _input_vector_sha256,
    _run_reference_inputs_parallel,
    canonical_json_bytes,
    load_corpus_document,
)


NANO = 1_000_000_000
MILLI_TO_NANO = 1_000_000
BENCHMARK_ROOT = Path(__file__).resolve().parent
FIXTURE_ROOT = BENCHMARK_ROOT / "fixtures"
STRIKE_MILLI_USD_BY_EXPIRY = (
    tuple(range(185_000, 222_501, 2_500)),
    tuple(range(180_000, 240_001, 4_000)),
    tuple(range(180_000, 240_001, 4_000)),
    tuple(range(180_000, 240_001, 4_000)),
)
EXPIRY_INSTANTS = (
    datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc),
    datetime(2026, 11, 27, 21, 0, tzinfo=timezone.utc),
    datetime(2027, 2, 26, 21, 0, tzinfo=timezone.utc),
    datetime(2027, 8, 27, 20, 0, tzinfo=timezone.utc),
)
LC0_EXPIRY_INDEX = 2
LC0_STRIKE_MILLI_USD = 200_000


def _hash(label: str) -> str:
    return sha256(label.encode("ascii")).hexdigest()


def _ns(value: datetime) -> int:
    return int(value.timestamp()) * NANO


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _synthetic_european_call_price(
    *,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    effective_yield: float,
    volatility: float,
) -> float:
    sigma_root_time = volatility * math.sqrt(years)
    d1 = (
        math.log(spot / strike)
        + (rate - effective_yield + 0.5 * volatility * volatility) * years
    ) / sigma_root_time
    d2 = d1 - sigma_root_time
    return (
        spot * math.exp(-effective_yield * years) * _normal_cdf(d1)
        - strike * math.exp(-rate * years) * _normal_cdf(d2)
    )


def _book(
    *,
    midpoint_nano_usd: int,
    capture_utc_ns: int,
    tick_nano_usd: int,
    size: int,
) -> TopOfBookV1:
    return TopOfBookV1(
        bid_nano_usd=midpoint_nano_usd - tick_nano_usd,
        ask_nano_usd=midpoint_nano_usd + tick_nano_usd,
        bid_size=size,
        ask_size=size,
        tick_nano_usd=tick_nano_usd,
        ts_event_ns=capture_utc_ns - 200_000_000,
        ts_recv_ns=capture_utc_ns - 100_000_000,
        flags=(),
    )


def _fixture_parameters(fixture_id: str) -> dict[str, object]:
    if fixture_id == "U64":
        return {
            "trading_date": "2026-08-28",
            "cutoff_utc_ns": _ns(
                datetime(2026, 8, 28, 14, 45, tzinfo=timezone.utc)
            ),
            "spot_nano_usd": 200 * NANO,
            "risk_free_rate_ppm": 20_000,
            "expense_yield_ppm": 4_000,
            "borrow_yield_ppm": 1_000,
            "volatility": 0.30,
            "source_version": "u64-v0.1",
        }
    if fixture_id == "W64":
        return {
            "trading_date": "2026-09-04",
            "cutoff_utc_ns": _ns(
                datetime(2026, 9, 4, 14, 45, tzinfo=timezone.utc)
            ),
            "spot_nano_usd": 205 * NANO,
            "risk_free_rate_ppm": 25_000,
            "expense_yield_ppm": 4_500,
            "borrow_yield_ppm": 1_500,
            "volatility": 0.35,
            "source_version": "w64-v0.1",
        }
    raise ValueError(fixture_id)


def _strike_grids(fixture_id: str) -> tuple[tuple[int, ...], ...]:
    if fixture_id == "U64":
        return STRIKE_MILLI_USD_BY_EXPIRY
    if fixture_id == "W64":
        grids = list(STRIKE_MILLI_USD_BY_EXPIRY)
        grids[LC0_EXPIRY_INDEX] = tuple(range(185_000, 260_001, 5_000))
        return tuple(grids)
    raise ValueError(fixture_id)


def _build_typed_fixture(fixture_id: str) -> tuple[
    SignalSnapshotV1,
    OptionQuoteSnapshotV1,
    CrrPitInputsV1,
    BcsFeeScheduleV1,
    object,
    tuple[object, ...],
]:
    parameters = _fixture_parameters(fixture_id)
    cutoff_utc_ns = parameters["cutoff_utc_ns"]
    capture_utc_ns = cutoff_utc_ns + 10 * NANO
    signal = SignalSnapshotV1(
        signal_id=f"gld-crr-p1-{fixture_id.lower()}",
        trading_date=parameters["trading_date"],
        rule_version="gld-research-v0.2-benchmark-only",
        rule_sha256=_hash(f"{fixture_id}:rule"),
        calendar_authority_id="XNYS_SYNTHETIC_CALENDAR",
        calendar_version="fixture-v0.1",
        calendar_sha256=_hash(f"{fixture_id}:calendar"),
        phase_receipt_sha256=_hash(f"{fixture_id}:phase"),
        completeness_receipt_sha256=_hash(f"{fixture_id}:completeness"),
        cutoff_utc_ns=cutoff_utc_ns,
        max_event_utc_ns=cutoff_utc_ns - 2 * NANO,
        max_receive_utc_ns=cutoff_utc_ns - NANO,
        signal_state="PASS",
        input_fact_sha256=_hash(f"{fixture_id}:signal-input"),
    )
    tick_nano_usd = 10_000_000
    spot_nano_usd = parameters["spot_nano_usd"]
    underlying_top = _book(
        midpoint_nano_usd=spot_nano_usd,
        capture_utc_ns=capture_utc_ns,
        tick_nano_usd=tick_nano_usd,
        size=1_000,
    )
    rate = parameters["risk_free_rate_ppm"] / 1_000_000
    effective_yield = (
        parameters["expense_yield_ppm"]
        + parameters["borrow_yield_ppm"]
    ) / 1_000_000
    quotes: list[OptionQuoteV1] = []
    for expiry, strike_grid in zip(
        EXPIRY_INSTANTS,
        _strike_grids(fixture_id),
        strict=True,
    ):
        expiry_utc_ns = _ns(expiry)
        years = (expiry_utc_ns - capture_utc_ns) / (365 * 86_400 * NANO)
        expiry_digits = expiry.strftime("%y%m%d")
        for strike_milli_usd in strike_grid:
            strike_nano_usd = strike_milli_usd * MILLI_TO_NANO
            theoretical_price = _synthetic_european_call_price(
                spot=spot_nano_usd / NANO,
                strike=strike_nano_usd / NANO,
                years=years,
                rate=rate,
                effective_yield=effective_yield,
                volatility=parameters["volatility"],
            )
            midpoint_nano_usd = (
                round(theoretical_price * NANO / tick_nano_usd)
                * tick_nano_usd
            )
            if midpoint_nano_usd <= tick_nano_usd:
                raise RuntimeError("fixture option midpoint cannot support BBO")
            contract = OptionContractV1(
                occ_symbol=(
                    f"GLD   {expiry_digits}C{strike_milli_usd:08d}"
                ),
                underlying="GLD",
                right="C",
                strike_nano_usd=strike_nano_usd,
                expiry_utc_ns=expiry_utc_ns,
                last_trading_utc_ns=expiry_utc_ns - NANO,
                activation_utc_ns=cutoff_utc_ns - 365 * 86_400 * NANO,
                multiplier=100,
                deliverable="100 GLD",
                currency="USD",
                standard_unadjusted=True,
                exercise_style="AMERICAN",
            )
            quotes.append(
                OptionQuoteV1(
                    contract=contract,
                    top_of_book=_book(
                        midpoint_nano_usd=midpoint_nano_usd,
                        capture_utc_ns=capture_utc_ns,
                        tick_nano_usd=tick_nano_usd,
                        size=100,
                    ),
                )
            )
    if len(quotes) != FIXTURE_COUNT:
        raise AssertionError(len(quotes))
    snapshot = OptionQuoteSnapshotV1(
        signal_snapshot_sha256=signal.snapshot_sha256,
        candidate_ordinal=1,
        candidate_provenance_sha256=_hash(f"{fixture_id}:candidate-1"),
        window_start_utc_ns=cutoff_utc_ns,
        window_end_utc_ns=cutoff_utc_ns + 60 * NANO,
        capture_utc_ns=capture_utc_ns,
        underlying_top=underlying_top,
        option_quotes=tuple(quotes),
        source_id="SYNTHETIC_FIXTURE_ONLY",
        source_version=parameters["source_version"],
        market_data_type="SYNTHETIC_NOT_ENTITLED",
        source_receipt_sha256=_hash(f"{fixture_id}:source-receipt"),
    )
    pit_inputs = CrrPitInputsV1(
        as_of_utc_ns=capture_utc_ns,
        risk_free_rate_ppm=parameters["risk_free_rate_ppm"],
        expense_yield_ppm=parameters["expense_yield_ppm"],
        borrow_yield_ppm=parameters["borrow_yield_ppm"],
        rate_curve_sha256=_hash(f"{fixture_id}:rate-curve"),
        distribution_assumption_sha256=_hash(
            f"{fixture_id}:distribution"
        ),
        borrow_assumption_sha256=_hash(f"{fixture_id}:borrow"),
    )
    fees = BcsFeeScheduleV1(
        source_receipt_sha256=_hash(f"{fixture_id}:fees"),
        effective_from_utc_ns=cutoff_utc_ns - NANO,
        long_entry_fee_nano_usd_per_contract=650_000_000,
        short_entry_fee_nano_usd_per_contract=650_000_000,
        long_exit_fee_nano_usd_per_contract=650_000_000,
        short_exit_fee_nano_usd_per_contract=650_000_000,
    )
    long_call_id = (
        "GLD   "
        f"{EXPIRY_INSTANTS[LC0_EXPIRY_INDEX].strftime('%y%m%d')}"
        f"C{LC0_STRIKE_MILLI_USD:08d}"
    )
    lc0_binding = bind_lc0_selection_for_research(
        signal=signal,
        snapshot=snapshot,
        selector_contract_sha256=_hash("LC0_SELECTOR_CONTRACT_V0.1"),
        long_call_id=long_call_id,
    )
    bound_inputs = tuple(
        bind_crr_call_inputs_from_snapshot(
            snapshot,
            contract=quote.contract,
            pit_inputs=pit_inputs,
        )
        for quote in snapshot.option_quotes
    )
    return (
        signal,
        snapshot,
        pit_inputs,
        fees,
        lc0_binding,
        bound_inputs,
    )


def _build_fixture_document(fixture_id: str) -> dict[str, object]:
    (
        signal,
        snapshot,
        pit_inputs,
        fees,
        lc0_binding,
        bound_inputs,
    ) = _build_typed_fixture(fixture_id)
    input_vector_sha256 = _input_vector_sha256(bound_inputs)
    return {
        "canonical_order": [
            "expiry_utc_ns",
            "strike_nano_usd",
            "occ_symbol",
        ],
        "declared_hashes": {
            "corpus_semantic_sha256": _corpus_semantic_sha256(
                signal=signal,
                snapshot=snapshot,
                pit_inputs=pit_inputs,
                fees=fees,
                lc0_binding=lc0_binding,
                input_vector_sha256=input_vector_sha256,
            ),
            "fee_schedule_sha256": fees.fee_schedule_sha256,
            "input_vector_sha256": input_vector_sha256,
            "lc0_binding_sha256": lc0_binding.binding_sha256,
            "option_snapshot_sha256": snapshot.snapshot_sha256,
            "pit_inputs_sha256": pit_inputs.pit_inputs_sha256,
            "signal_snapshot_sha256": signal.snapshot_sha256,
        },
        "expected_terminal_count": FIXTURE_COUNT,
        "fee_schedule": asdict(fees),
        "fixture_id": fixture_id,
        "lc0_binding": {
            "expected_bcs_short_call_id": "PENDING_REFERENCE_GOLDEN",
            "long_call_id": lc0_binding.long_call_id,
            "selector_contract_sha256": lc0_binding.selector_contract_sha256,
        },
        "pit_inputs": asdict(pit_inputs),
        "provenance": {
            "data_authority": "SYNTHETIC_LOCAL_ONLY",
            "provider_access": "NONE",
            "slo_pass_claimed": False,
        },
        "purpose": "SUCCESS_LOAD" if fixture_id == "U64" else "WARMUP_LOAD",
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "selected_snapshot": asdict(snapshot),
        "signal_snapshot": asdict(signal),
        "warmup_runs": 0 if fixture_id == "U64" else WARMUP_RUNS,
    }


def _choose_expected_bcs_short(
    fixture_document: dict[str, object],
    result_documents: tuple[dict[str, object], ...],
) -> str:
    long_call_id = fixture_document["lc0_binding"]["long_call_id"]
    quote_by_id = {
        quote["contract"]["occ_symbol"]: quote
        for quote in fixture_document["selected_snapshot"]["option_quotes"]
    }
    long_contract = quote_by_id[long_call_id]["contract"]
    candidates: list[tuple[int, int, str]] = []
    for terminal in result_documents:
        contract = quote_by_id[terminal["contract_id"]]["contract"]
        delta_ppm = terminal["delta_ppm"]
        if (
            terminal["terminal_status"] == "PASS"
            and contract["expiry_utc_ns"] == long_contract["expiry_utc_ns"]
            and contract["strike_nano_usd"] > long_contract["strike_nano_usd"]
            and 200_000 <= delta_ppm <= 300_000
        ):
            candidates.append(
                (
                    abs(delta_ppm - 250_000),
                    -contract["strike_nano_usd"],
                    terminal["contract_id"],
                )
            )
    if not candidates:
        raise RuntimeError(f"{fixture_document['fixture_id']} has no BCS short")
    ordered = sorted(candidates)
    if len(ordered) > 1 and ordered[0][:2] == ordered[1][:2]:
        raise RuntimeError("BCS winner is not unique before OCC lexical tie-break")
    return ordered[0][2]


def generate_fixture_and_golden(fixture_id: str) -> tuple[str, str]:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    initial_bytes = canonical_json_bytes(_build_fixture_document(fixture_id))
    document = json.loads(initial_bytes)
    initial_loaded = load_corpus_document(document, fixture_bytes=initial_bytes)
    receipt = _run_reference_inputs_parallel(
        initial_loaded.bound_inputs,
        workers=REFERENCE_WORKERS,
    )
    failures = tuple(
        item for item in receipt.results if item.terminal_status != "PASS"
    )
    if failures:
        summary = ", ".join(
            f"{item.ordinal}:{item.reason_code}" for item in failures
        )
        raise RuntimeError(f"{fixture_id} reference failures: {summary}")
    result_documents = tuple(item.as_dict() for item in receipt.results)
    expected_short = _choose_expected_bcs_short(document, result_documents)
    document["lc0_binding"]["expected_bcs_short_call_id"] = expected_short
    fixture_bytes = canonical_json_bytes(document)
    final_loaded = load_corpus_document(document, fixture_bytes=fixture_bytes)
    fixture_path = FIXTURE_ROOT / f"{fixture_id.lower()}_v0.1.json"
    fixture_path.write_bytes(fixture_bytes)
    golden = {
        "expected_bcs_short_call_id": expected_short,
        "fixture_file_sha256": sha256(fixture_bytes).hexdigest(),
        "fixture_id": fixture_id,
        "input_vector_sha256": final_loaded.input_vector_sha256,
        "reference_model_sha256": MODEL_SHA256,
        "results": list(result_documents),
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "semantic_output_sha256": receipt.semantic_output_sha256,
        "status": "REFERENCE_SEMANTIC_ONLY_NOT_SLO_QUALIFIED",
    }
    golden_bytes = canonical_json_bytes(golden)
    golden_path = (
        FIXTURE_ROOT / f"{fixture_id.lower()}_reference_golden_v0.1.json"
    )
    golden_path.write_bytes(golden_bytes)
    return sha256(fixture_bytes).hexdigest(), sha256(golden_bytes).hexdigest()


def _write_support_manifests() -> None:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    e1 = {
        "evidence_status": "MAPPED_NOT_EXECUTED",
        "mappings": [
            {
                "category": "acceptance_matrix_27",
                "claim": "EXISTING_TEST_MAPPING_ONLY",
                "node_ids": [
                    "tests/test_crr_delta.py::CrrSyntheticAcceptanceMatrixTests::test_full_cross_product_round_trips_or_fails_closed_deterministically"
                ],
            },
            {
                "category": "early_exercise_true_false",
                "claim": "EXISTING_TEST_MAPPING_ONLY",
                "node_ids": [
                    "tests/test_crr_delta.py::CrrTreeBehaviorTests::test_high_yield_deep_itm_american_call_exercises_early",
                    "tests/test_crr_delta.py::CrrDeltaEndToEndTests::test_q_zero_american_tree_recovers_bsm_iv_and_delta",
                ],
            },
            {
                "category": "iv_tree_failures",
                "claim": "EXISTING_TEST_MAPPING_ONLY",
                "node_ids": [
                    "tests/test_crr_delta.py::CrrDeltaValidationTests::test_price_outside_iv_bracket_fails_closed",
                    "tests/test_crr_delta.py::CrrDeltaValidationTests::test_price_at_unidentifiable_zero_volatility_limit_fails_closed",
                    "tests/test_crr_delta.py::CrrSyntheticAcceptanceMatrixTests::test_high_iv_short_dte_model_instability_fails_closed",
                    "tests/test_crr_delta.py::CrrTreeBehaviorTests::test_high_yield_low_volatility_invalid_probability_fails_closed",
                ],
            },
            {
                "category": "selector_instability",
                "claim": "EXISTING_TEST_MAPPING_ONLY",
                "node_ids": [
                    "tests/test_bcs.py::BcsSelectorTests::test_coarse_and_fine_winner_disagreement_fails_closed"
                ],
            },
            {
                "category": "hostile_decimal",
                "claim": "EXISTING_TEST_MAPPING_ONLY",
                "node_ids": [
                    "tests/test_crr_delta.py::CrrDeltaEndToEndTests::test_decimal_quantization_ignores_hostile_ambient_context"
                ],
            },
            {
                "category": "provenance_mutation",
                "claim": "EXISTING_TEST_MAPPING_ONLY",
                "node_ids": [
                    "tests/test_crr_delta.py::CrrDeltaEndToEndTests::test_solver_helper_or_frozen_constant_runtime_mutation_fails_closed",
                    "tests/test_crr_delta.py::CrrDeltaEndToEndTests::test_participating_class_surface_mutation_fails_closed",
                    "tests/test_crr_delta.py::CrrDeltaEndToEndTests::test_canonical_encoder_dependency_mutation_fails_closed",
                    "tests/test_crr_delta.py::CrrDeltaEndToEndTests::test_verifier_revalidates_input_and_run_receipt_lineage",
                ],
            },
        ],
        "schema_version": E1_SCHEMA_VERSION,
    }
    (FIXTURE_ROOT / "e1_manifest_v0.1.json").write_bytes(
        canonical_json_bytes(e1)
    )
    raw_sample_schema = {
        "allowed_stages": ["SINGLE_CALL", "BATCH_64", "E2E", "COLD_E2E"],
        "binding_rules": {
            "fixture_file_sha256": "EXACT_STATIC_MANIFEST_FIXTURE_ENTRY",
            "input_vector_sha256": "EXACT_LOADED_CORPUS_INPUT_VECTOR",
            "semantic_output_sha256": "EXACT_REFERENCE_GOLDEN_VECTOR",
            "single_call": "EXACT_ORDINAL_INPUT_SHA_AND_TERMINAL_FIELDS",
        },
        "clock": "perf_counter_ns",
        "foundation_status": "SCHEMA_ONLY_REFERENCE_NOT_SLO_QUALIFIED",
        "p95_method": "NEAREST_RANK_CEIL_0_95_N",
        "sample_counts": {
            "BATCH_64": 100,
            "COLD_E2E": 30,
            "E2E": 100,
            "SINGLE_CALL_PER_CONTRACT": 30,
        },
        "required_fields": [
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
        ],
        "schema_version": RAW_SAMPLE_SCHEMA_VERSION,
    }
    (FIXTURE_ROOT / "raw_sample_schema_v0.1.json").write_bytes(
        canonical_json_bytes(raw_sample_schema)
    )


def seal_static_manifest() -> str:
    _write_support_manifests()
    relative_paths = (
        "fixtures/e1_manifest_v0.1.json",
        "fixtures/raw_sample_schema_v0.1.json",
        "fixtures/u64_reference_golden_v0.1.json",
        "fixtures/u64_v0.1.json",
        "fixtures/w64_reference_golden_v0.1.json",
        "fixtures/w64_v0.1.json",
    )
    missing = tuple(
        relative_path
        for relative_path in relative_paths
        if not (BENCHMARK_ROOT / relative_path).is_file()
    )
    if missing:
        raise RuntimeError(f"cannot seal; missing {missing}")
    manifest = {
        "files": {
            relative_path: sha256(
                (BENCHMARK_ROOT / relative_path).read_bytes()
            ).hexdigest()
            for relative_path in relative_paths
        },
        "schema_version": STATIC_MANIFEST_SCHEMA_VERSION,
        "status": "STATIC_BYTES_SEALED_REFERENCE_NOT_SLO_QUALIFIED",
    }
    manifest_bytes = canonical_json_bytes(manifest)
    (FIXTURE_ROOT / "static_manifest_v0.1.json").write_bytes(manifest_bytes)
    return sha256(manifest_bytes).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", choices=("U64", "W64"))
    parser.add_argument("--seal", action="store_true")
    args = parser.parse_args()
    _write_support_manifests()
    if args.fixture is not None:
        fixture_sha256, golden_sha256 = generate_fixture_and_golden(
            args.fixture
        )
        print(
            json.dumps(
                {
                    "fixture": args.fixture,
                    "fixture_sha256": fixture_sha256,
                    "golden_sha256": golden_sha256,
                },
                sort_keys=True,
            )
        )
    if args.seal:
        print(
            json.dumps(
                {"static_manifest_sha256": seal_static_manifest()},
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
