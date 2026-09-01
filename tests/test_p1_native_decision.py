from __future__ import annotations

import ast
import copy
from dataclasses import fields, replace
import inspect
import json
from pathlib import Path
import pickle
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import patch

from benchmarks.crr_p1_contract import load_corpus, load_reference_golden
from benchmarks.crr_p1_decision_contract import (
    BACKEND_EVIDENCE_SCHEMA_VERSION,
    load_decision_semantic_golden,
    validate_backend_evidence_envelope,
)
from gld_normalizer.errors import NormalizationError
from gld_research_core.bcs import bind_lc0_selection_for_research
from gld_research_core.crr_delta import MODEL_SHA256
import gld_research_core.p1_native_decision as decision_module
from gld_research_core.p1_native_decision import (
    P1_CONTRACT_SHA256,
    RESEARCH_CONTRACT_SHA256,
    NATIVE_DECISION_SHADOW_STATUS,
    NativeDecisionShadowFailureV1,
    NativeDecisionShadowReceiptV1,
    P1NativeDecisionError,
    create_p1_native_decision_shadow_engine,
)
from gld_research_core.native_crr_delta import create_native_crr_delta_engine
from gld_research_core.native_tree import load_native_tree_v1
from tools.build_crr_native import build_native_kernel_v1


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "gld_research_core" / "p1_native_decision.py"
BACKEND_SHA256 = "a" * 64


def _golden_document(fixture_id: str) -> dict[str, object]:
    return json.loads(
        (
            ROOT
            / "benchmarks"
            / "fixtures"
            / f"{fixture_id.lower()}_decision_semantic_golden_v0.1.json"
        ).read_text(encoding="ascii")
    )


def _run_arguments(corpus: object) -> dict[str, object]:
    return {
        "signal": corpus.signal,
        "candidate_snapshots": (corpus.snapshot,),
        "pit_inputs": corpus.pit_inputs,
        "lc0_binding": corpus.lc0_binding,
        "fees": corpus.fees,
        "research_contract_sha256": RESEARCH_CONTRACT_SHA256,
        "p1_contract_sha256": P1_CONTRACT_SHA256,
        "rule_package_version": corpus.signal.rule_version,
        "quantity": 1,
    }


class _FastVerifiedNativeLikeEngine:
    def __init__(
        self,
        fixture_id: str,
        *,
        failures: dict[int, str] | None = None,
        unexpected: set[int] | None = None,
        unverified: set[int] | None = None,
        overrides: dict[str, dict[str, object]] | None = None,
        delays: dict[int, float] | None = None,
    ) -> None:
        rows = load_reference_golden(fixture_id)["results"]
        self._row_by_contract = {row["contract_id"]: row for row in rows}
        self._ordinal_by_contract = {
            row["contract_id"]: row["ordinal"] for row in rows
        }
        self._failures = failures or {}
        self._unexpected = unexpected or set()
        self._unverified = unverified or set()
        self._overrides = overrides or {}
        self._delays = delays or {}
        self._verified: dict[int, tuple[object, tuple[object, ...]]] = {}
        self._lock = threading.Lock()
        self.calls: list[int] = []
        self.completions: list[int] = []
        self.results: dict[int, object] = {}

    @staticmethod
    def _payload(value: object) -> tuple[object, ...]:
        return (
            value.input_sha256,
            value.option_snapshot_sha256,
            value.contract_id,
            value.iv_ppm,
            value.delta_ppm,
            value.coarse_iv_ppm,
            value.fine_iv_ppm,
            value.coarse_delta_ppm,
            value.fine_delta_ppm,
            value.coarse_price_residual_nano_usd,
            value.fine_price_residual_nano_usd,
            value.coarse_early_exercise_nodes,
            value.fine_early_exercise_nodes,
            value.combined_backend_evidence_sha256,
        )

    def compute(self, inputs: object, *, expected_model_sha256: str) -> object:
        if expected_model_sha256 != MODEL_SHA256:
            raise AssertionError(expected_model_sha256)
        ordinal = self._ordinal_by_contract[inputs.contract_id]
        with self._lock:
            self.calls.append(ordinal)
        delay = self._delays.get(ordinal, 0.0)
        if delay:
            time.sleep(delay)
        if ordinal in self._unexpected:
            with self._lock:
                self.completions.append(ordinal)
            raise RuntimeError("synthetic unexpected failure")
        if ordinal in self._failures:
            with self._lock:
                self.completions.append(ordinal)
            raise NormalizationError(self._failures[ordinal])

        row = dict(self._row_by_contract[inputs.contract_id])
        row.update(self._overrides.get(inputs.contract_id, {}))
        result = SimpleNamespace(
            input_sha256=inputs.input_sha256,
            option_snapshot_sha256=inputs.option_snapshot_sha256,
            contract_id=inputs.contract_id,
            iv_ppm=row["iv_ppm"],
            delta_ppm=row["delta_ppm"],
            coarse_iv_ppm=row["coarse_iv_ppm"],
            fine_iv_ppm=row["fine_iv_ppm"],
            coarse_delta_ppm=row["coarse_delta_ppm"],
            fine_delta_ppm=row["fine_delta_ppm"],
            coarse_price_residual_nano_usd=row[
                "coarse_price_residual_nano_usd"
            ],
            fine_price_residual_nano_usd=row[
                "fine_price_residual_nano_usd"
            ],
            coarse_early_exercise_nodes=row[
                "coarse_early_exercise_nodes"
            ],
            fine_early_exercise_nodes=row["fine_early_exercise_nodes"],
            combined_backend_evidence_sha256=BACKEND_SHA256,
        )
        with self._lock:
            self.completions.append(ordinal)
            self.results[ordinal] = result
            if ordinal not in self._unverified:
                self._verified[id(result)] = (result, self._payload(result))
        return result

    def verify(self, value: object) -> bool:
        try:
            with self._lock:
                record = self._verified.get(id(value))
            return (
                record is not None
                and record[0] is value
                and record[1] == self._payload(value)
            )
        except Exception:
            return False


def _test_factory(
    engine: _FastVerifiedNativeLikeEngine,
) -> tuple[object, object, object]:
    return decision_module._create_p1_native_decision_shadow_engine_for_tests(
        compute_call=engine.compute,
        verify_call=engine.verify,
        combined_backend_evidence_sha256=BACKEND_SHA256,
    )


class P1NativeDecisionSemanticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.u64 = load_corpus("U64")
        cls.w64 = load_corpus("W64")

    def test_fast_u64_projection_and_terminal_semantics_match_frozen_golden(
        self,
    ) -> None:
        engine = _FastVerifiedNativeLikeEngine("U64")
        run, verify_receipt, verify_failure = _test_factory(engine)

        receipt = run(**_run_arguments(self.u64))
        golden = _golden_document("U64")

        self.assertIs(type(receipt), NativeDecisionShadowReceiptV1)
        self.assertEqual(receipt.execution_status, NATIVE_DECISION_SHADOW_STATUS)
        self.assertEqual(
            (receipt.requested, receipt.bound, receipt.started, receipt.terminal),
            (64, 64, 64, 64),
        )
        self.assertEqual(receipt.worker_count, 8)
        self.assertEqual(receipt.kernel_threads, 1)
        self.assertEqual(receipt.cache_hits, 0)
        self.assertEqual(
            tuple(item.as_dict() for item in receipt.call_terminals),
            tuple(golden["call_terminals"]),
        )
        self.assertEqual(
            receipt.call_semantic_output_sha256,
            golden["call_semantic_output_sha256"],
        )
        self.assertEqual(
            receipt.decision_projection.as_dict(),
            golden["decision_projection"],
        )
        self.assertEqual(
            receipt.projection_sha256,
            "5dd66c686c0c7df9f05b63f5807a2b748f1a1f83f1b271c7fb76366e8132664c",
        )
        self.assertFalse(receipt.actionable)
        self.assertEqual(receipt.broker_order_count, 0)
        self.assertTrue(verify_receipt(receipt))
        self.assertFalse(verify_failure(receipt))
        self.assertEqual(len(engine.calls), 64)

        semantic_golden = load_decision_semantic_golden("U64")
        components = receipt.backend_evidence_components.as_dict()
        envelope = validate_backend_evidence_envelope(
            {
                "schema_version": BACKEND_EVIDENCE_SCHEMA_VERSION,
                "fixture_id": "U64",
                "semantic_sha256": semantic_golden.decision_semantic_sha256,
                **components,
            }
        )
        self.assertEqual(
            semantic_golden.decision_semantic_sha256,
            "7f18fa70313c0d8dfa672e62757d06c66e99a12a7a197eb6aaeb9ccdc3c60541",
        )
        self.assertEqual(
            envelope.backend_evidence_sha256,
            receipt.backend_evidence_components.backend_evidence_sha256,
        )

    def test_selects_first_quality_complete_snapshot_not_first_candidate(
        self,
    ) -> None:
        stale_book = replace(
            self.u64.snapshot.underlying_top,
            ts_event_ns=self.u64.snapshot.capture_utc_ns - 6_200_000_000,
            ts_recv_ns=self.u64.snapshot.capture_utc_ns - 6_000_000_000,
        )
        stale = replace(
            self.u64.snapshot,
            underlying_top=stale_book,
            candidate_ordinal=1,
        )
        complete = replace(
            self.u64.snapshot,
            candidate_ordinal=2,
            candidate_provenance_sha256="f" * 64,
        )
        binding = bind_lc0_selection_for_research(
            signal=self.u64.signal,
            snapshot=complete,
            selector_contract_sha256=(
                self.u64.document["lc0_binding"]["selector_contract_sha256"]
            ),
            long_call_id=self.u64.lc0_binding.long_call_id,
        )
        engine = _FastVerifiedNativeLikeEngine("U64")
        run, verify_receipt, _ = _test_factory(engine)
        arguments = _run_arguments(self.u64)
        arguments["candidate_snapshots"] = (stale, complete)
        arguments["lc0_binding"] = binding

        receipt = run(**arguments)

        self.assertTrue(verify_receipt(receipt))
        self.assertEqual(
            receipt.request_binding.selected_snapshot_sha256,
            complete.snapshot_sha256,
        )
        self.assertEqual(receipt.request_binding.candidate_count, 2)
        self.assertEqual(len(engine.calls), 64)

    def test_all_64_complete_and_minimum_failure_has_no_partial_projection(
        self,
    ) -> None:
        engine = _FastVerifiedNativeLikeEngine(
            "U64",
            failures={17: "IV_NO_BRACKET", 41: "TREE_NOT_CONVERGED"},
            unexpected={3},
            unverified={9},
            delays={3: 0.02, 17: 0.001},
        )
        run, verify_receipt, verify_failure = _test_factory(engine)

        failure = run(**_run_arguments(self.u64))

        self.assertIs(type(failure), NativeDecisionShadowFailureV1)
        self.assertEqual(len(engine.calls), 64)
        self.assertEqual(len(engine.completions), 64)
        self.assertEqual(
            (failure.requested, failure.bound, failure.started, failure.terminal),
            (64, 64, 64, 64),
        )
        self.assertEqual(failure.first_failure_ordinal, 3)
        self.assertEqual(
            failure.call_terminals[2].reason_code,
            "BATCH_ENGINE_UNEXPECTED_EXCEPTION",
        )
        self.assertEqual(failure.call_terminals[8].reason_code, "UNVERIFIED_DELTA_RESULT")
        self.assertEqual(failure.call_terminals[16].reason_code, "IV_NO_BRACKET")
        self.assertFalse(hasattr(failure, "decision_projection"))
        self.assertFalse(hasattr(failure, "backend_evidence_components"))
        self.assertFalse(failure.actionable)
        self.assertEqual(failure.broker_order_count, 0)
        self.assertTrue(verify_failure(failure))
        self.assertFalse(verify_receipt(failure))

    def test_selector_instability_fails_after_full_batch_without_winner(self) -> None:
        engine = _FastVerifiedNativeLikeEngine(
            "U64",
            overrides={
                "GLD   270226C00232000": {
                    "coarse_delta_ppm": 249_900,
                    "fine_delta_ppm": 250_200,
                    "delta_ppm": 250_200,
                },
                "GLD   270226C00236000": {
                    "coarse_delta_ppm": 250_300,
                    "fine_delta_ppm": 249_800,
                    "delta_ppm": 249_800,
                },
            },
        )
        run, _, verify_failure = _test_factory(engine)

        failure = run(**_run_arguments(self.u64))

        self.assertIs(type(failure), NativeDecisionShadowFailureV1)
        self.assertEqual(failure.reason_code, "SELECTOR_MODEL_INSTABILITY")
        self.assertEqual(len(engine.calls), 64)
        self.assertFalse(hasattr(failure, "decision_projection"))
        self.assertTrue(verify_failure(failure))

    def test_invalid_debit_fails_after_full_batch_and_size_tamper_fails_early(
        self,
    ) -> None:
        long_id = self.u64.lc0_binding.long_call_id
        short_id = self.u64.document["lc0_binding"][
            "expected_bcs_short_call_id"
        ]
        long_quote = next(
            quote
            for quote in self.u64.snapshot.option_quotes
            if quote.contract.occ_symbol == long_id
        )
        invalid_quotes = tuple(
            replace(
                quote,
                top_of_book=replace(
                    quote.top_of_book,
                    bid_nano_usd=18_000_000_000,
                    ask_nano_usd=18_020_000_000,
                ),
            )
            if quote.contract.occ_symbol == short_id
            else quote
            for quote in self.u64.snapshot.option_quotes
        )
        invalid_snapshot = replace(
            self.u64.snapshot,
            option_quotes=invalid_quotes,
            candidate_provenance_sha256="e" * 64,
        )
        invalid_binding = bind_lc0_selection_for_research(
            signal=self.u64.signal,
            snapshot=invalid_snapshot,
            selector_contract_sha256=(
                self.u64.document["lc0_binding"]["selector_contract_sha256"]
            ),
            long_call_id=long_id,
        )
        engine = _FastVerifiedNativeLikeEngine("U64")
        run, _, verify_failure = _test_factory(engine)
        arguments = _run_arguments(self.u64)
        arguments["candidate_snapshots"] = (invalid_snapshot,)
        arguments["lc0_binding"] = invalid_binding

        failure = run(**arguments)

        self.assertEqual(failure.reason_code, "BCS_INVALID_NET_DEBIT")
        self.assertEqual(len(engine.calls), 64)
        self.assertTrue(verify_failure(failure))
        self.assertFalse(hasattr(failure, "decision_projection"))

        size_snapshot = copy.deepcopy(self.u64.snapshot)
        size_long = next(
            quote
            for quote in size_snapshot.option_quotes
            if quote.contract.occ_symbol == long_id
        )
        object.__setattr__(size_long.top_of_book, "ask_size", 0)
        size_engine = _FastVerifiedNativeLikeEngine("U64")
        size_run, _, _ = _test_factory(size_engine)
        size_arguments = _run_arguments(self.u64)
        size_arguments["candidate_snapshots"] = (size_snapshot,)
        with self.assertRaises(P1NativeDecisionError) as raised:
            size_run(**size_arguments)
        self.assertEqual(raised.exception.reason_code, "BCS_SIZE_INSUFFICIENT")
        self.assertEqual(size_engine.calls, [])

    def test_count_order_snapshot_binding_and_exact_types_fail_before_compute(
        self,
    ) -> None:
        fewer = replace(
            self.u64.snapshot,
            option_quotes=self.u64.snapshot.option_quotes[:-1],
        )
        reordered = replace(
            self.u64.snapshot,
            option_quotes=(
                self.u64.snapshot.option_quotes[1],
                self.u64.snapshot.option_quotes[0],
                *self.u64.snapshot.option_quotes[2:],
            ),
        )
        second = replace(
            self.u64.snapshot,
            candidate_ordinal=2,
            candidate_provenance_sha256="d" * 64,
        )
        bad_binding = replace(
            self.u64.lc0_binding,
            option_snapshot_sha256="0" * 64,
        )
        cases = (
            {"candidate_snapshots": [self.u64.snapshot]},
            {"candidate_snapshots": (fewer,)},
            {"candidate_snapshots": (reordered,)},
            {"candidate_snapshots": (second, self.u64.snapshot)},
            {"lc0_binding": bad_binding},
            {"pit_inputs": True},
            {"quantity": True},
            {"quantity": 1.0},
            {"research_contract_sha256": "0" * 64},
            {"p1_contract_sha256": "0" * 64},
            {"rule_package_version": True},
            {"rule_package_version": "wrong-version"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                engine = _FastVerifiedNativeLikeEngine("U64")
                run, _, _ = _test_factory(engine)
                arguments = _run_arguments(self.u64)
                arguments.update(changes)
                with self.assertRaises(P1NativeDecisionError):
                    run(**arguments)
                self.assertEqual(engine.calls, [])

    def test_malformed_nested_snapshot_values_fail_with_one_controlled_reason(
        self,
    ) -> None:
        malformed_container = copy.deepcopy(self.u64.snapshot)
        object.__setattr__(
            malformed_container,
            "option_quotes",
            list(malformed_container.option_quotes),
        )

        wrong_nested_object = copy.deepcopy(self.u64.snapshot)
        object.__setattr__(
            wrong_nested_object,
            "option_quotes",
            (object(), *wrong_nested_object.option_quotes[1:]),
        )

        missing_nested_slot = copy.deepcopy(self.u64.snapshot)
        object.__delattr__(
            missing_nested_slot.option_quotes[0],
            "top_of_book",
        )

        missing_book_size = copy.deepcopy(self.u64.snapshot)
        object.__delattr__(
            missing_book_size.option_quotes[0].top_of_book,
            "bid_size",
        )

        wrong_type_book_size = copy.deepcopy(self.u64.snapshot)
        object.__setattr__(
            wrong_type_book_size.option_quotes[0].top_of_book,
            "bid_size",
            "100",
        )

        for case_name, snapshot in (
            ("malformed_container", malformed_container),
            ("wrong_nested_object", wrong_nested_object),
            ("missing_nested_slot", missing_nested_slot),
            ("missing_book_size", missing_book_size),
            ("wrong_type_book_size", wrong_type_book_size),
        ):
            with self.subTest(case_name=case_name):
                engine = _FastVerifiedNativeLikeEngine("U64")
                run, _, _ = _test_factory(engine)
                arguments = _run_arguments(self.u64)
                arguments["candidate_snapshots"] = (snapshot,)

                with self.assertRaises(P1NativeDecisionError) as raised:
                    run(**arguments)

                self.assertEqual(
                    raised.exception.reason_code,
                    "OPTION_SNAPSHOT_BINDING_MISMATCH",
                )
                self.assertEqual(engine.calls, [])

    def test_receipt_copy_pickle_cross_factory_input_result_and_backend_tamper(
        self,
    ) -> None:
        engine = _FastVerifiedNativeLikeEngine("U64")
        run, verify_receipt, _ = _test_factory(engine)
        receipt = run(**_run_arguments(self.u64))
        constructed = NativeDecisionShadowReceiptV1(
            **{
                item.name: getattr(receipt, item.name)
                for item in fields(NativeDecisionShadowReceiptV1)
            }
        )
        copies = (
            copy.copy(receipt),
            copy.deepcopy(receipt),
            pickle.loads(pickle.dumps(receipt)),
            constructed,
        )
        self.assertTrue(verify_receipt(receipt))
        for value in copies:
            self.assertEqual(value, receipt)
            self.assertIsNot(value, receipt)
            self.assertFalse(verify_receipt(value))

        other_engine = _FastVerifiedNativeLikeEngine("U64")
        _, other_verify_receipt, _ = _test_factory(other_engine)
        self.assertFalse(other_verify_receipt(receipt))

        engine.results[1].delta_ppm += 1
        self.assertFalse(verify_receipt(receipt))

        fresh_engine = _FastVerifiedNativeLikeEngine("U64")
        fresh_run, fresh_verify, _ = _test_factory(fresh_engine)
        fresh = fresh_run(**_run_arguments(self.u64))
        object.__setattr__(
            fresh.backend_evidence_components,
            "backend_evidence_sha256",
            "b" * 64,
        )
        self.assertFalse(fresh_verify(fresh))

        mutation_engine = _FastVerifiedNativeLikeEngine("U64")
        mutation_run, mutation_verify, _ = _test_factory(mutation_engine)
        mutable_signal = copy.deepcopy(self.u64.signal)
        mutation_arguments = _run_arguments(self.u64)
        mutation_arguments["signal"] = mutable_signal
        mutated_receipt = mutation_run(**mutation_arguments)
        object.__setattr__(mutable_signal, "rule_version", "mutated-version")
        self.assertFalse(mutation_verify(mutated_receipt))

    def test_public_factory_rejects_fake_and_has_no_runtime_backend_override(
        self,
    ) -> None:
        engine = _FastVerifiedNativeLikeEngine("U64")
        with self.assertRaises(P1NativeDecisionError) as raised:
            create_p1_native_decision_shadow_engine(
                compute_call=engine.compute,
                verify_call=engine.verify,
                combined_backend_evidence_sha256=BACKEND_SHA256,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "NATIVE_DECISION_NATIVE_BINDING_INVALID",
        )

        self.assertNotIn(
            "_create_p1_native_decision_shadow_engine_for_tests",
            decision_module.__all__,
        )
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertFalse(any(name.startswith("benchmarks") for name in imported_modules))
        self.assertNotIn("native_tree", imported_modules)
        self.assertNotIn("build_native_kernel_v1", source)
        self.assertNotIn("load_native_tree_v1", source)
        public_names = set(decision_module.__all__)
        self.assertFalse(
            any(
                token in name.lower()
                for name in public_names
                for token in ("provider", "submit", "place_order", "broker_client")
            )
        )


class P1NativeDecisionRealNativeDifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        manifest_path = build_native_kernel_v1(repo_root=ROOT)
        manifest = json.loads(manifest_path.read_bytes())
        kernel = load_native_tree_v1(
            manifest_path,
            expected_backend_evidence_sha256=manifest[
                "backend_evidence_sha256"
            ],
        )
        compute, verify_call, backend_evidence = create_native_crr_delta_engine(
            kernel
        )
        run, verify_receipt, verify_failure = (
            create_p1_native_decision_shadow_engine(
                compute_call=compute,
                verify_call=verify_call,
                combined_backend_evidence_sha256=backend_evidence,
            )
        )
        cls.run_decision = staticmethod(run)
        cls.verify_receipt = staticmethod(verify_receipt)
        cls.verify_failure = staticmethod(verify_failure)
        cls.native_compute = staticmethod(compute)
        cls.native_verify_call = staticmethod(verify_call)
        cls.backend_evidence = backend_evidence
        cls.u64 = load_corpus("U64")
        cls.w64 = load_corpus("W64")

    def test_public_factory_rejects_prefactory_global_implementation_replacement(
        self,
    ) -> None:
        original_prepare_request = decision_module._prepare_request
        original_projection = decision_module._derive_projection
        original_batch_factory = decision_module._create_exact_64_batch_engine

        def changed_prepare_request(**arguments: object) -> object:
            prepared = original_prepare_request(**arguments)
            return replace(
                prepared,
                request_binding=replace(
                    prepared.request_binding,
                    rule_sha256="0" * 64,
                ),
            )

        def changed_projection(**arguments: object) -> object:
            projection = original_projection(**arguments)
            return replace(
                projection,
                entry_cost=replace(
                    projection.entry_cost,
                    stressed_net_debit_nano_usd=(
                        projection.entry_cost.stressed_net_debit_nano_usd + 1
                    ),
                ),
            )

        def changed_batch_factory(**arguments: object) -> object:
            return original_batch_factory(**arguments)

        def changed_runtime_semantic(**_arguments: object) -> str:
            return "1" * 64

        def changed_shadow_artifact(**_arguments: object) -> str:
            return "2" * 64

        def passthrough(name: str) -> object:
            original = getattr(decision_module, name)

            def replacement(*arguments: object, **keywords: object) -> object:
                return original(*arguments, **keywords)

            return replacement

        replacements = (
            ("_native_binding_is_valid", lambda *_arguments: True),
            ("_prepare_request", changed_prepare_request),
            ("_derive_projection", changed_projection),
            ("_prepared_matches", passthrough("_prepared_matches")),
            ("_state_kwargs", passthrough("_state_kwargs")),
            ("_document_sha256", passthrough("_document_sha256")),
            (
                "_runtime_semantic_sha256",
                changed_runtime_semantic,
            ),
            ("_shadow_artifact_sha256", changed_shadow_artifact),
            (
                "_native_execution_sha256",
                passthrough("_native_execution_sha256"),
            ),
            ("_callable_state", passthrough("_callable_state")),
            ("_require_sha256", passthrough("_require_sha256")),
            (
                "semantic_output_sha256",
                passthrough("semantic_output_sha256"),
            ),
            ("_create_exact_64_batch_engine", changed_batch_factory),
        )
        for name, replacement in replacements:
            with self.subTest(name=name), patch.object(
                decision_module,
                name,
                replacement,
            ):
                with self.assertRaises(P1NativeDecisionError) as raised:
                    create_p1_native_decision_shadow_engine(
                        compute_call=self.native_compute,
                        verify_call=self.native_verify_call,
                        combined_backend_evidence_sha256=self.backend_evidence,
                    )
                self.assertEqual(
                    raised.exception.reason_code,
                    "NATIVE_DECISION_RUNTIME_INTEGRITY_INVALID",
                )

    def test_module_level_core_factory_cannot_be_called_with_fake_validator(
        self,
    ) -> None:
        engine = _FastVerifiedNativeLikeEngine("U64")
        with patch.object(
            decision_module,
            "_native_binding_is_valid",
            lambda *_arguments: True,
        ):
            with self.assertRaises(AttributeError):
                getattr(
                    decision_module,
                    "_create_p1_native_decision_shadow_engine",
                )(
                    compute_call=engine.compute,
                    verify_call=engine.verify,
                    combined_backend_evidence_sha256=BACKEND_SHA256,
                    require_native_binding=True,
                    native_binding_validator=lambda *_arguments: True,
                    projection_deriver=decision_module._derive_projection,
                    batch_engine_factory=(
                        decision_module._create_exact_64_batch_engine
                    ),
                )
        self.assertEqual(engine.calls, [])
        self.assertFalse(
            hasattr(
                decision_module,
                "_PUBLIC_FACTORY_IMPLEMENTATION_ARTIFACTS",
            )
        )

    def test_public_factory_rejects_shared_test_seam_closure_mutation(
        self,
    ) -> None:
        def closure_cells(function: object) -> dict[str, object]:
            return dict(
                zip(
                    function.__code__.co_freevars,
                    function.__closure__ or (),
                    strict=True,
                )
            )

        public_cells = closure_cells(
            create_p1_native_decision_shadow_engine
        )
        strict_constructor = public_cells[
            "strict_engine_constructor"
        ].cell_contents
        strict_cells = closure_cells(strict_constructor)
        test_cells = closure_cells(
            decision_module._create_p1_native_decision_shadow_engine_for_tests
        )
        self.assertIs(
            strict_cells["request_preparer"],
            test_cells["request_preparer"],
        )
        request_preparer_cell = test_cells["request_preparer"]
        original = request_preparer_cell.cell_contents

        def changed_prepare_request(**arguments: object) -> object:
            prepared = original(**arguments)
            return replace(
                prepared,
                request_binding=replace(
                    prepared.request_binding,
                    rule_sha256="0" * 64,
                ),
            )

        try:
            request_preparer_cell.cell_contents = changed_prepare_request
            with self.assertRaises(P1NativeDecisionError) as raised:
                create_p1_native_decision_shadow_engine(
                    compute_call=self.native_compute,
                    verify_call=self.native_verify_call,
                    combined_backend_evidence_sha256=self.backend_evidence,
                )
            self.assertEqual(
                raised.exception.reason_code,
                "NATIVE_DECISION_RUNTIME_INTEGRITY_INVALID",
            )
        finally:
            request_preparer_cell.cell_contents = original

    def test_real_native_u64_matches_frozen_terminal_projection_and_semantic(
        self,
    ) -> None:
        receipt = self.run_decision(**_run_arguments(self.u64))
        golden = _golden_document("U64")

        self.assertIs(type(receipt), NativeDecisionShadowReceiptV1)
        self.assertTrue(self.verify_receipt(receipt))
        self.assertFalse(self.verify_failure(receipt))
        self.assertEqual(
            tuple(item.as_dict() for item in receipt.call_terminals),
            tuple(golden["call_terminals"]),
        )
        self.assertEqual(
            receipt.decision_projection.as_dict(),
            golden["decision_projection"],
        )
        self.assertEqual(
            receipt.projection_sha256,
            "5dd66c686c0c7df9f05b63f5807a2b748f1a1f83f1b271c7fb76366e8132664c",
        )
        self.assertEqual(
            receipt.backend_evidence_components.backend_evidence_sha256,
            self.backend_evidence,
        )

    def test_real_native_w64_runs_exactly_three_full_untimed_warmups(self) -> None:
        receipts = tuple(
            self.run_decision(**_run_arguments(self.w64)) for _ in range(3)
        )
        golden = _golden_document("W64")

        self.assertEqual(len(receipts), 3)
        for receipt in receipts:
            self.assertIs(type(receipt), NativeDecisionShadowReceiptV1)
            self.assertTrue(self.verify_receipt(receipt))
            self.assertEqual(receipt.terminal, 64)
            self.assertEqual(receipt.cache_hits, 0)
            self.assertEqual(
                tuple(item.as_dict() for item in receipt.call_terminals),
                tuple(golden["call_terminals"]),
            )
            self.assertEqual(
                receipt.decision_projection.as_dict(),
                golden["decision_projection"],
            )
            self.assertEqual(
                receipt.projection_sha256,
                "62d584710ff9608958ef199f8397c1c347632f08cfaafec958396d1eea80ebfd",
            )
        self.assertEqual(
            load_decision_semantic_golden("W64").decision_semantic_sha256,
            "e230592bf5325fac9942ea3c499de77a70663b8dd42909c956164fb7e09799c4",
        )


if __name__ == "__main__":
    unittest.main()
