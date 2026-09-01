from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from gld_normalizer.errors import NormalizationError
from gld_research_core.crr_delta import MODEL_SHA256

from benchmarks.crr_p1_contract import (
    FIXTURE_COUNT,
    RAW_SAMPLE_SCHEMA_VERSION,
    BenchmarkContractError,
    ReferenceTerminalV1,
    canonical_json_bytes,
    load_corpus,
    load_corpus_document,
    load_e1_manifest,
    load_reference_golden,
    load_static_manifest,
    nearest_rank_p95_ns,
    run_reference_corpus,
    run_reference_inputs,
    validate_raw_sample_receipt,
)


ROOT = Path(__file__).resolve().parents[1]


def _fixture_document(fixture_id: str) -> dict[str, object]:
    path = ROOT / "benchmarks" / "fixtures" / f"{fixture_id.lower()}_v0.1.json"
    return json.loads(path.read_text(encoding="ascii"))


def _success_terminal(inputs: object) -> object:
    return SimpleNamespace(
        input_sha256=inputs.input_sha256,
        option_snapshot_sha256=inputs.option_snapshot_sha256,
        contract_id=inputs.contract_id,
        iv_ppm=300_000,
        delta_ppm=500_000,
        coarse_iv_ppm=300_000,
        fine_iv_ppm=300_000,
        coarse_delta_ppm=500_000,
        fine_delta_ppm=500_000,
        coarse_price_residual_nano_usd=0,
        fine_price_residual_nano_usd=0,
        coarse_early_exercise_nodes=0,
        fine_early_exercise_nodes=0,
    )


class CrrP1BenchmarkContractTests(unittest.TestCase):
    def test_static_manifest_hashes_every_canonical_artifact(self) -> None:
        manifest = load_static_manifest()

        self.assertEqual(
            manifest["schema_version"],
            "GLD_CRR_P1_STATIC_MANIFEST_V1",
        )
        for relative_path, expected_sha256 in manifest["files"].items():
            path = ROOT / "benchmarks" / relative_path
            raw = path.read_bytes()
            self.assertEqual(sha256(raw).hexdigest(), expected_sha256)
            self.assertEqual(raw, canonical_json_bytes(json.loads(raw)))

    def test_fixture_loads_exact_typed_64_call_corpus(self) -> None:
        for fixture_id in ("U64", "W64"):
            with self.subTest(fixture_id=fixture_id):
                corpus = load_corpus(fixture_id)
                self.assertEqual(corpus.fixture_id, fixture_id)
                self.assertEqual(
                    len(corpus.snapshot.option_quotes),
                    FIXTURE_COUNT,
                )
                self.assertEqual(len(corpus.bound_inputs), 64)
                dtes_ns = tuple(
                    quote.contract.expiry_utc_ns
                    - corpus.snapshot.capture_utc_ns
                    for quote in corpus.snapshot.option_quotes
                )
                self.assertGreaterEqual(min(dtes_ns), 30 * 86_400 * 1_000_000_000)
                self.assertLessEqual(max(dtes_ns), 365 * 86_400 * 1_000_000_000)
                self.assertEqual(
                    len({item.contract_id for item in corpus.bound_inputs}),
                    64,
                )
                self.assertTrue(
                    all(
                        item.option_snapshot_sha256
                        == corpus.snapshot.snapshot_sha256
                        for item in corpus.bound_inputs
                    )
                )
                self.assertIn(
                    corpus.lc0_binding.long_call_id,
                    {item.contract_id for item in corpus.bound_inputs},
                )
                self.assertEqual(
                    corpus.fixture_sha256,
                    sha256(corpus.fixture_bytes).hexdigest(),
                )

    def test_u64_and_w64_have_different_economic_and_canonical_hashes(self) -> None:
        u64 = load_corpus("U64")
        w64 = load_corpus("W64")

        self.assertNotEqual(u64.fixture_sha256, w64.fixture_sha256)
        self.assertNotEqual(
            u64.snapshot.snapshot_sha256,
            w64.snapshot.snapshot_sha256,
        )
        self.assertNotEqual(u64.input_vector_sha256, w64.input_vector_sha256)
        self.assertNotEqual(
            u64.pit_inputs.pit_inputs_sha256,
            w64.pit_inputs.pit_inputs_sha256,
        )
        self.assertNotEqual(
            tuple(item.input_sha256 for item in u64.bound_inputs),
            tuple(item.input_sha256 for item in w64.bound_inputs),
        )

    def test_reference_engine_recomputes_all_static_golden_terminals(self) -> None:
        for fixture_id in ("U64", "W64"):
            with self.subTest(fixture_id=fixture_id):
                receipt = run_reference_corpus(fixture_id, workers=8)
                golden = load_reference_golden(fixture_id)
                self.assertEqual(receipt.requested, 64)
                self.assertEqual(receipt.bound, 64)
                self.assertEqual(receipt.started, 64)
                self.assertEqual(receipt.terminal, 64)
                self.assertEqual(receipt.result_cache_hits, 0)
                self.assertIsNone(receipt.first_failure_ordinal)
                self.assertEqual(
                    tuple(item.as_dict() for item in receipt.results),
                    tuple(golden["results"]),
                )
                self.assertEqual(
                    receipt.semantic_output_sha256,
                    golden["semantic_output_sha256"],
                )
                self.assertTrue(
                    all(
                        item.terminal_status == "PASS"
                        for item in receipt.results
                    )
                )

    def test_loader_rejects_count_identity_order_and_hash_mutations(self) -> None:
        mutations = (
            ("fewer", "BENCHMARK_CALL_COUNT_INVALID"),
            ("more", "BENCHMARK_CALL_COUNT_INVALID"),
            ("duplicate", "BENCHMARK_CONTRACT_IDENTITY_DUPLICATE"),
            ("reordered", "BENCHMARK_CANONICAL_ORDER_INVALID"),
            ("tampered", "BENCHMARK_DECLARED_HASH_MISMATCH"),
        )
        for mutation, reason_code in mutations:
            with self.subTest(mutation=mutation):
                document = deepcopy(_fixture_document("U64"))
                quotes = document["selected_snapshot"]["option_quotes"]
                self.assertIsInstance(quotes, list)
                if mutation == "fewer":
                    quotes.pop()
                elif mutation == "more":
                    quotes.append(deepcopy(quotes[-1]))
                    quotes[-1]["contract"]["occ_symbol"] = (
                        "GLD   270827C00244000"
                    )
                    quotes[-1]["contract"]["strike_nano_usd"] = (
                        244_000_000_000
                    )
                elif mutation == "duplicate":
                    quotes[1] = deepcopy(quotes[0])
                elif mutation == "reordered":
                    quotes[0], quotes[1] = quotes[1], quotes[0]
                elif mutation == "tampered":
                    document["declared_hashes"][
                        "option_snapshot_sha256"
                    ] = "0" * 64

                with self.assertRaises(BenchmarkContractError) as raised:
                    load_corpus_document(
                        document,
                        fixture_bytes=canonical_json_bytes(document),
                    )
                self.assertEqual(raised.exception.reason_code, reason_code)

    def test_golden_rejects_reorder_and_field_tampering(self) -> None:
        golden = deepcopy(load_reference_golden("U64"))
        golden["results"][0], golden["results"][1] = (
            golden["results"][1],
            golden["results"][0],
        )
        with self.assertRaises(BenchmarkContractError) as reordered:
            load_reference_golden("U64", document=golden)
        self.assertEqual(
            reordered.exception.reason_code,
            "BENCHMARK_GOLDEN_ORDER_INVALID",
        )

        golden = deepcopy(load_reference_golden("U64"))
        golden["results"][0]["delta_ppm"] += 1
        with self.assertRaises(BenchmarkContractError) as tampered:
            load_reference_golden("U64", document=golden)
        self.assertEqual(
            tampered.exception.reason_code,
            "BENCHMARK_GOLDEN_HASH_MISMATCH",
        )

    def test_runner_starts_all_calls_after_failure_and_has_no_result_cache(self) -> None:
        corpus = load_corpus("U64")
        calls: list[str] = []

        def engine(inputs: object, *, expected_model_sha256: str) -> object:
            self.assertEqual(expected_model_sha256, MODEL_SHA256)
            calls.append(inputs.contract_id)
            if len(calls) in {1, 65}:
                raise NormalizationError("IV_NO_BRACKET")
            return _success_terminal(inputs)

        first = run_reference_inputs(
            corpus.bound_inputs,
            compute_call=engine,
            verify_call=lambda _: True,
        )
        second = run_reference_inputs(
            corpus.bound_inputs,
            compute_call=engine,
            verify_call=lambda _: True,
        )

        self.assertEqual(len(calls), 128)
        self.assertEqual(first.requested, 64)
        self.assertEqual(first.started, 64)
        self.assertEqual(first.terminal, 64)
        self.assertEqual(second.requested, 64)
        self.assertEqual(second.started, 64)
        self.assertEqual(second.terminal, 64)
        self.assertEqual(first.first_failure_ordinal, 1)
        self.assertEqual(second.first_failure_ordinal, 1)
        self.assertEqual(first.result_cache_hits, 0)
        self.assertEqual(second.result_cache_hits, 0)
        self.assertEqual(
            [item.ordinal for item in first.results],
            list(range(1, 65)),
        )
        self.assertEqual(
            sum(item.terminal_status == "PASS" for item in first.results),
            63,
        )

    def test_runner_refuses_non_64_duplicate_and_reordered_bound_inputs(self) -> None:
        corpus = load_corpus("U64")
        invalid_vectors = (
            corpus.bound_inputs[:-1],
            corpus.bound_inputs + (corpus.bound_inputs[-1],),
            corpus.bound_inputs[:1]
            + (corpus.bound_inputs[0],)
            + corpus.bound_inputs[2:],
            (corpus.bound_inputs[1], corpus.bound_inputs[0])
            + corpus.bound_inputs[2:],
        )
        for vector in invalid_vectors:
            with self.subTest(size=len(vector)):
                with self.assertRaises(BenchmarkContractError):
                    run_reference_inputs(
                        vector,
                        compute_call=lambda inputs, **_: _success_terminal(
                            inputs
                        ),
                        verify_call=lambda _: True,
                    )

    def test_e1_manifest_maps_existing_tests_without_claiming_execution(self) -> None:
        manifest = load_e1_manifest()

        self.assertEqual(manifest["evidence_status"], "MAPPED_NOT_EXECUTED")
        self.assertTrue(
            {
                "acceptance_matrix_27",
                "early_exercise_true_false",
                "iv_tree_failures",
                "selector_instability",
                "hostile_decimal",
                "provenance_mutation",
            }
            <= {item["category"] for item in manifest["mappings"]}
        )
        for mapping in manifest["mappings"]:
            self.assertEqual(mapping["claim"], "EXISTING_TEST_MAPPING_ONLY")
            for node_id in mapping["node_ids"]:
                file_name, _class_name, method_name = node_id.split("::")
                source = (ROOT / file_name).read_text(encoding="utf-8")
                self.assertIn(f"def {method_name}(", source)

    def test_nearest_rank_p95_and_raw_sample_schema_are_exact(self) -> None:
        self.assertEqual(nearest_rank_p95_ns(range(1, 21)), 19)
        self.assertEqual(nearest_rank_p95_ns([9]), 9)
        with self.assertRaises(BenchmarkContractError) as empty:
            nearest_rank_p95_ns([])
        self.assertEqual(
            empty.exception.reason_code,
            "BENCHMARK_LATENCY_SAMPLE_INVALID",
        )

        corpus = load_corpus("U64")
        golden = load_reference_golden("U64")
        terminal = golden["results"][0]
        receipt = {
            "backend_evidence_sha256": "a" * 64,
            "bound_calls_per_sample": 1,
            "call_input_sha256": terminal["input_sha256"],
            "call_terminal_semantic": terminal,
            "schema_version": RAW_SAMPLE_SCHEMA_VERSION,
            "fixture_id": "U64",
            "fixture_file_sha256": corpus.fixture_sha256,
            "stage": "SINGLE_CALL",
            "contract_ordinal": 1,
            "clock": "perf_counter_ns",
            "exceptions_count": 0,
            "gc_policy": "DISABLED_DURING_TIMED_SAMPLE",
            "input_vector_sha256": corpus.input_vector_sha256,
            "kernel_threads_per_worker": 1,
            "outliers_removed": False,
            "p95_method": "NEAREST_RANK_CEIL_0_95_N",
            "runner_process_ordinal": 1,
            "samples_ns": list(range(1, 31)),
            "reported_p95_ns": 29,
            "requested_calls_per_sample": 1,
            "result_cache_hits": 0,
            "semantic_mismatch_count": 0,
            "semantic_output_sha256": golden["semantic_output_sha256"],
            "slo_pass_claimed": False,
            "started_calls_per_sample": 1,
            "terminal_calls_per_sample": 1,
            "terminal_count_mismatch_count": 0,
            "timer_overhead_subtracted": False,
            "worker_count": 1,
        }
        self.assertEqual(validate_raw_sample_receipt(receipt), 29)
        receipts = {"U64": receipt}

        w64 = load_corpus("W64")
        w64_golden = load_reference_golden("W64")
        w64_terminal = w64_golden["results"][0]
        w64_timed_receipt = dict(receipt)
        w64_timed_receipt.update(
            {
                "fixture_id": "W64",
                "fixture_file_sha256": w64.fixture_sha256,
                "input_vector_sha256": w64.input_vector_sha256,
                "semantic_output_sha256": w64_golden[
                    "semantic_output_sha256"
                ],
                "call_input_sha256": w64_terminal["input_sha256"],
                "call_terminal_semantic": w64_terminal,
            }
        )
        with self.assertRaises(BenchmarkContractError) as w64_rejected:
            validate_raw_sample_receipt(w64_timed_receipt)
        self.assertEqual(
            w64_rejected.exception.reason_code,
            "BENCHMARK_RAW_SAMPLE_RECEIPT_INVALID",
        )

        for field, value in (
            ("clock", "monotonic_ns"),
            ("p95_method", "INTERPOLATED"),
            ("slo_pass_claimed", True),
        ):
            with self.subTest(field=field):
                invalid = dict(receipts["U64"])
                invalid[field] = value
                with self.assertRaises(BenchmarkContractError):
                    validate_raw_sample_receipt(invalid)

        for field in (
            "fixture_file_sha256",
            "input_vector_sha256",
            "semantic_output_sha256",
            "call_input_sha256",
        ):
            with self.subTest(binding=field):
                invalid = dict(receipts["U64"])
                invalid[field] = "f" * 64
                with self.assertRaises(BenchmarkContractError) as raised:
                    validate_raw_sample_receipt(invalid)
                self.assertEqual(
                    raised.exception.reason_code,
                    "BENCHMARK_RAW_SAMPLE_BINDING_MISMATCH",
                )

        invalid = dict(receipts["U64"])
        invalid_terminal = deepcopy(invalid["call_terminal_semantic"])
        invalid_terminal["delta_ppm"] += 1
        invalid["call_terminal_semantic"] = invalid_terminal
        with self.assertRaises(BenchmarkContractError) as raised:
            validate_raw_sample_receipt(invalid)
        self.assertEqual(
            raised.exception.reason_code,
            "BENCHMARK_RAW_SAMPLE_BINDING_MISMATCH",
        )

    def test_fixture_objects_are_frozen_and_declared_lc0_binding_is_exact(self) -> None:
        corpus = load_corpus("U64")
        with self.assertRaises(Exception):
            setattr(
                corpus.snapshot,
                "option_quotes",
                corpus.snapshot.option_quotes[:-1],
            )
        self.assertEqual(
            corpus.lc0_binding.binding_sha256,
            corpus.document["declared_hashes"]["lc0_binding_sha256"],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
