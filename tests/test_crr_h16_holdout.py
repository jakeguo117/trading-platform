from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from benchmarks.crr_p1_contract import load_corpus
import benchmarks.h16_contract as h16_module
from benchmarks.h16_contract import (
    H16_COUNT,
    H16_MANIFEST_SHA256,
    H16_NATIVE_SOURCE_ARTIFACT_SHA256,
    H16ContractError,
    H16ReferenceReceiptV1,
    H16ReferenceTerminalV1,
    canonical_json_bytes,
    load_h16_corpus,
    load_h16_corpus_document,
    load_h16_manifest,
    load_h16_reference_golden,
    load_h16_reference_golden_document,
    replay_h16_reference,
)


ROOT = Path(__file__).resolve().parents[1]
HOLDOUT_ROOT = ROOT / "benchmarks" / "holdout"


def _read_document(name: str) -> dict[str, object]:
    return json.loads((HOLDOUT_ROOT / name).read_text(encoding="ascii"))


class CrrH16HoldoutTests(unittest.TestCase):
    def test_manifest_seals_canonical_corpus_and_golden_exactly(self) -> None:
        manifest = load_h16_manifest()
        self.assertEqual(
            sha256((HOLDOUT_ROOT / "h16_manifest_v0.1.json").read_bytes()).hexdigest(),
            H16_MANIFEST_SHA256,
        )

        self.assertEqual(
            manifest["status"],
            "HOLDOUT_SEALED_BEFORE_NATIVE_EXECUTION",
        )
        self.assertEqual(manifest["timing_status"], "H16_NOT_TIMED")
        self.assertEqual(
            manifest["source_status"],
            "NATIVE_SOURCE_FROZEN_NOT_EXECUTED_AGAINST_H16",
        )
        self.assertFalse(manifest["native_execution"]["allowed"])
        self.assertFalse(
            manifest["native_execution"]["executed_against_h16"]
        )
        for file_name, expected_sha256 in manifest["files"].items():
            raw = (HOLDOUT_ROOT / file_name).read_bytes()
            self.assertEqual(sha256(raw).hexdigest(), expected_sha256)
            self.assertEqual(raw, canonical_json_bytes(json.loads(raw)))

    def test_loader_returns_exact_direct_hash_bound_16_call_order(self) -> None:
        corpus = load_h16_corpus()

        self.assertEqual(corpus.corpus_id, "H16")
        self.assertEqual(len(corpus.inputs), H16_COUNT)
        self.assertEqual(
            tuple(item.ordinal for item in corpus.cases),
            tuple(range(1, H16_COUNT + 1)),
        )
        self.assertEqual(len({item.case_id for item in corpus.cases}), H16_COUNT)
        self.assertEqual(
            len({item.input_sha256 for item in corpus.inputs}),
            H16_COUNT,
        )
        self.assertEqual(
            tuple(case.input_sha256 for case in corpus.cases),
            tuple(item.input_sha256 for item in corpus.inputs),
        )
        self.assertEqual(
            corpus.corpus_file_sha256,
            sha256(corpus.corpus_bytes).hexdigest(),
        )
        benchmark_hashes = {
            item.input_sha256
            for fixture_id in ("U64", "W64")
            for item in load_corpus(fixture_id).bound_inputs
        }
        self.assertTrue(
            benchmark_hashes.isdisjoint(
                {item.input_sha256 for item in corpus.inputs}
            )
        )

    def test_coverage_and_preregistered_terminal_composition_are_exact(self) -> None:
        corpus = load_h16_corpus()
        golden = load_h16_reference_golden()

        self.assertEqual(
            {case.moneyness for case in corpus.cases},
            {"ITM", "ATM", "OTM"},
        )
        self.assertEqual(
            {case.volatility_band for case in corpus.cases},
            {"LOW", "MEDIUM", "HIGH"},
        )
        self.assertEqual(
            {case.rate_sign for case in corpus.cases},
            {"NEGATIVE", "POSITIVE"},
        )
        self.assertEqual(
            {case.effective_yield_band for case in corpus.cases},
            {"LOW", "HIGH"},
        )
        self.assertEqual(min(case.dte_days for case in corpus.cases), 45)
        self.assertEqual(max(case.dte_days for case in corpus.cases), 360)
        self.assertEqual(golden["terminal_counts"], {"FAIL": 4, "PASS": 12})
        self.assertEqual(
            {result["early_exercise_detected"] for result in golden["results"]},
            {False, True},
        )
        expected = tuple(
            (
                case.preregistered_terminal_status,
                case.preregistered_reason_code,
                case.preregistered_early_exercise_detected,
            )
            for case in corpus.cases
        )
        observed = tuple(
            (
                result["terminal_status"],
                result["reason_code"],
                result["early_exercise_detected"],
            )
            for result in golden["results"]
        )
        self.assertEqual(observed, expected)

    def test_reference_replays_all_16_without_timing_or_native_claim(self) -> None:
        receipt = replay_h16_reference(workers=4)
        golden = load_h16_reference_golden()

        self.assertEqual(receipt.requested, H16_COUNT)
        self.assertEqual(receipt.started, H16_COUNT)
        self.assertEqual(receipt.terminal, H16_COUNT)
        self.assertEqual(receipt.result_cache_hits, 0)
        self.assertEqual(receipt.timing_status, "H16_NOT_TIMED")
        self.assertEqual(
            receipt.holdout_status,
            "HOLDOUT_SEALED_BEFORE_NATIVE_EXECUTION",
        )
        self.assertFalse(receipt.native_executed)
        self.assertEqual(
            tuple(item.as_dict() for item in receipt.results),
            tuple(golden["results"]),
        )
        self.assertEqual(
            receipt.semantic_output_sha256,
            golden["semantic_output_sha256"],
        )

    def test_corpus_rejects_tamper_reorder_count_and_fake_hash(self) -> None:
        base = _read_document("h16_inputs_v0.1.json")
        mutations = (
            ("tamper", "H16_DECLARED_INPUT_HASH_MISMATCH"),
            ("reorder", "H16_CANONICAL_ORDER_INVALID"),
            ("count", "H16_CALL_COUNT_INVALID"),
            ("fake_hash", "H16_INPUT_VECTOR_HASH_MISMATCH"),
        )
        for mutation, reason_code in mutations:
            with self.subTest(mutation=mutation):
                document = deepcopy(base)
                inputs = document["inputs"]
                self.assertIsInstance(inputs, list)
                if mutation == "tamper":
                    inputs[0]["call_input"]["option_mid_nano_usd"] += 1
                elif mutation == "reorder":
                    inputs[0], inputs[1] = inputs[1], inputs[0]
                elif mutation == "count":
                    inputs.pop()
                elif mutation == "fake_hash":
                    document["input_vector_sha256"] = "0" * 64
                with self.assertRaises(H16ContractError) as raised:
                    load_h16_corpus_document(
                        document,
                        corpus_bytes=canonical_json_bytes(document),
                    )
                self.assertEqual(raised.exception.reason_code, reason_code)

    def test_golden_rejects_reorder_tamper_count_and_fake_hash(self) -> None:
        base = _read_document("h16_reference_golden_v0.1.json")
        mutations = (
            ("reorder", "H16_GOLDEN_ORDER_INVALID"),
            ("tamper", "H16_GOLDEN_TERMINAL_INVALID"),
            ("count", "H16_GOLDEN_COUNT_INVALID"),
            ("fake_hash", "H16_GOLDEN_CORPUS_HASH_MISMATCH"),
        )
        for mutation, reason_code in mutations:
            with self.subTest(mutation=mutation):
                document = deepcopy(base)
                results = document["results"]
                self.assertIsInstance(results, list)
                if mutation == "reorder":
                    results[0], results[1] = results[1], results[0]
                elif mutation == "tamper":
                    results[0]["delta_ppm"] += 1
                elif mutation == "count":
                    results.pop()
                elif mutation == "fake_hash":
                    document["corpus_file_sha256"] = "0" * 64
                with self.assertRaises(H16ContractError) as raised:
                    load_h16_reference_golden_document(document)
                self.assertEqual(raised.exception.reason_code, reason_code)

    def test_manifest_rejects_numeric_false_execution_facts(self) -> None:
        base = _read_document("h16_manifest_v0.1.json")
        mutations = (
            ("allowed", 0),
            ("executed_against_h16", 0.0),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                document = deepcopy(base)
                document["native_execution"][field] = value
                raw = canonical_json_bytes(document)
                with (
                    patch.object(
                        h16_module,
                        "_read_canonical_json",
                        return_value=(raw, document),
                    ),
                    patch.object(
                        h16_module,
                        "H16_MANIFEST_SHA256",
                        sha256(raw).hexdigest(),
                    ),
                ):
                    with self.assertRaises(H16ContractError) as raised:
                        load_h16_manifest()
                self.assertEqual(
                    raised.exception.reason_code,
                    "H16_MANIFEST_INVALID",
                )

    def test_corpus_rejects_numeric_root_and_nested_coverage_facts(self) -> None:
        base = _read_document("h16_inputs_v0.1.json")
        mutations = (
            ("root_count_float", "H16_CORPUS_SCHEMA_INVALID"),
            ("dte_float", "H16_COVERAGE_CONTRACT_INVALID"),
            ("early_exercise_ints", "H16_COVERAGE_CONTRACT_INVALID"),
            ("terminal_count_floats", "H16_COVERAGE_CONTRACT_INVALID"),
            ("ordinal_true", "H16_CANONICAL_ORDER_INVALID"),
        )
        for mutation, reason_code in mutations:
            with self.subTest(mutation=mutation):
                document = deepcopy(base)
                if mutation == "root_count_float":
                    document["expected_call_count"] = 16.0
                elif mutation == "dte_float":
                    document["coverage_contract"]["dte_days_inclusive"] = [
                        45.0,
                        360.0,
                    ]
                elif mutation == "early_exercise_ints":
                    document["coverage_contract"]["early_exercise_values"] = [
                        0,
                        1,
                    ]
                elif mutation == "terminal_count_floats":
                    document["coverage_contract"][
                        "reference_terminal_counts"
                    ] = {"FAIL": 4.0, "PASS": 12.0}
                elif mutation == "ordinal_true":
                    document["inputs"][0]["ordinal"] = True
                with self.assertRaises(H16ContractError) as raised:
                    load_h16_corpus_document(
                        document,
                        corpus_bytes=canonical_json_bytes(document),
                    )
                self.assertEqual(raised.exception.reason_code, reason_code)

    def test_golden_rejects_numeric_terminal_counts(self) -> None:
        base = _read_document("h16_reference_golden_v0.1.json")
        for terminal_counts in (
            {"FAIL": 4.0, "PASS": 12.0},
            {"FAIL": 4, "PASS": 12.0},
        ):
            with self.subTest(terminal_counts=terminal_counts):
                document = deepcopy(base)
                document["terminal_counts"] = terminal_counts
                with self.assertRaises(H16ContractError) as raised:
                    load_h16_reference_golden_document(document)
                self.assertEqual(
                    raised.exception.reason_code,
                    "H16_GOLDEN_SCHEMA_INVALID",
                )

    def test_golden_binds_contract_identity_to_matching_corpus_input(self) -> None:
        document = _read_document("h16_reference_golden_v0.1.json")
        document["results"][0]["contract_id"] = document["results"][1][
            "contract_id"
        ]
        document["semantic_output_sha256"] = sha256(
            canonical_json_bytes(document["results"])
        ).hexdigest()

        with self.assertRaises(H16ContractError) as raised:
            load_h16_reference_golden_document(document)
        self.assertEqual(
            raised.exception.reason_code,
            "H16_GOLDEN_INPUT_MISMATCH",
        )

    def test_reference_receipt_rejects_numeric_count_coercion(self) -> None:
        golden = _read_document("h16_reference_golden_v0.1.json")
        results = tuple(
            H16ReferenceTerminalV1(**result)
            for result in golden["results"]
        )
        valid = H16ReferenceReceiptV1(
            requested=H16_COUNT,
            started=H16_COUNT,
            terminal=H16_COUNT,
            result_cache_hits=0,
            results=results,
            semantic_output_sha256=golden["semantic_output_sha256"],
        )
        for field, value in (
            ("requested", 16.0),
            ("started", 16.0),
            ("terminal", 16.0),
            ("result_cache_hits", False),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(H16ContractError) as raised:
                    replace(valid, **{field: value})
                self.assertEqual(
                    raised.exception.reason_code,
                    "H16_REFERENCE_RECEIPT_INVALID",
                )

    def test_source_status_binds_frozen_native_source_without_importing_it(self) -> None:
        source_path = ROOT / "native" / "crr_tree_kernel_v1.c"
        self.assertEqual(
            sha256(source_path.read_bytes()).hexdigest(),
            H16_NATIVE_SOURCE_ARTIFACT_SHA256,
        )
        manifest = load_h16_manifest()
        self.assertEqual(
            manifest["native_source_artifact_sha256"],
            H16_NATIVE_SOURCE_ARTIFACT_SHA256,
        )
        for relative_path in (
            "benchmarks/h16_contract.py",
            "benchmarks/generate_h16_holdout.py",
            "tests/test_crr_h16_holdout.py",
        ):
            tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
            imported_modules = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            } | {
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            }
            self.assertFalse(
                any("native" in module.lower() for module in imported_modules),
                relative_path,
            )

    def test_offline_generator_refuses_to_reseal_existing_holdout(self) -> None:
        from benchmarks.generate_h16_holdout import seal_h16_initial_holdout

        paths = tuple(HOLDOUT_ROOT.glob("h16_*.json"))
        before = {path.name: sha256(path.read_bytes()).hexdigest() for path in paths}
        with self.assertRaises(RuntimeError) as raised:
            seal_h16_initial_holdout()
        self.assertIn("H16_ALREADY_SEALED", str(raised.exception))
        after = {path.name: sha256(path.read_bytes()).hexdigest() for path in paths}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
