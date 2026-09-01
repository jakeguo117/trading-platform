from __future__ import annotations

import copy
from dataclasses import replace
from types import SimpleNamespace
import inspect
import pickle
import threading
import time
import unittest
from unittest.mock import patch

from gld_normalizer.errors import NormalizationError
import gld_research_core.crr_batch as batch_module
from gld_research_core.crr_batch import (
    BATCH_EXECUTION_STATUS,
    NATIVE_CONNECTION_STATUS,
    P1_QUALIFICATION_STATUS,
    CrrExact64BatchReceiptV1,
    CrrSemanticTerminalV1,
)
from benchmarks.crr_p1_contract import load_corpus, load_reference_golden


BACKEND_SHA256 = "a" * 64


class _FastVerifiedEngine:
    def __init__(
        self,
        *,
        golden_rows: tuple[dict[str, object], ...],
        fail_reasons: dict[int, str] | None = None,
        unexpected_ordinals: set[int] | None = None,
        unverified_ordinals: set[int] | None = None,
        delay_by_ordinal: dict[int, float] | None = None,
    ) -> None:
        self._row_by_input = {
            row["input_sha256"]: row for row in golden_rows
        }
        self._ordinal_by_input = {
            row["input_sha256"]: row["ordinal"] for row in golden_rows
        }
        self._fail_reasons = fail_reasons or {}
        self._unexpected_ordinals = unexpected_ordinals or set()
        self._unverified_ordinals = unverified_ordinals or set()
        self._delay_by_ordinal = delay_by_ordinal or {}
        self._verified_by_identity: dict[int, object] = {}
        self._lock = threading.Lock()
        self.calls: list[int] = []
        self.completions: list[int] = []
        self.results_by_ordinal: dict[int, object] = {}

    def compute(self, inputs: object, *, expected_model_sha256: str) -> object:
        del expected_model_sha256
        ordinal = self._ordinal_by_input[inputs.input_sha256]
        with self._lock:
            self.calls.append(ordinal)
        delay = self._delay_by_ordinal.get(ordinal, 0.0)
        if delay:
            time.sleep(delay)
        if ordinal in self._unexpected_ordinals:
            with self._lock:
                self.completions.append(ordinal)
            raise RuntimeError("synthetic unexpected failure")
        if ordinal in self._fail_reasons:
            with self._lock:
                self.completions.append(ordinal)
            raise NormalizationError(self._fail_reasons[ordinal])

        row = self._row_by_input[inputs.input_sha256]
        result = SimpleNamespace(
            input_sha256=row["input_sha256"],
            option_snapshot_sha256=inputs.option_snapshot_sha256,
            contract_id=row["contract_id"],
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
        )
        with self._lock:
            self.completions.append(ordinal)
            self.results_by_ordinal[ordinal] = result
            if ordinal not in self._unverified_ordinals:
                self._verified_by_identity[id(result)] = result
        return result

    def verify(self, value: object) -> bool:
        with self._lock:
            return self._verified_by_identity.get(id(value)) is value


def _golden_rows() -> tuple[dict[str, object], ...]:
    golden = load_reference_golden("U64")
    return tuple(golden["results"])


def _new_engine(**changes: object) -> _FastVerifiedEngine:
    return _FastVerifiedEngine(golden_rows=_golden_rows(), **changes)


class CrrExact64BatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = load_corpus("U64")
        cls.golden = load_reference_golden("U64")

    def _runner(
        self,
        engine: _FastVerifiedEngine,
    ) -> tuple[object, object]:
        return batch_module._create_exact_64_batch_engine(
            compute_call=engine.compute,
            verify_call=engine.verify,
            backend_evidence_sha256=BACKEND_SHA256,
        )

    def test_exact_64_success_matches_u64_golden_schema_and_hash(self) -> None:
        engine = _new_engine(
            delay_by_ordinal={1: 0.025, 2: 0.015, 3: 0.005}
        )
        (
            run_batch,
            is_verified_terminal,
            is_verified_receipt,
        ) = self._runner(engine)

        receipt = run_batch(self.corpus.bound_inputs)

        self.assertEqual(receipt.status, BATCH_EXECUTION_STATUS)
        self.assertEqual(
            receipt.native_connection_status,
            NATIVE_CONNECTION_STATUS,
        )
        self.assertEqual(receipt.p1_status, P1_QUALIFICATION_STATUS)
        self.assertEqual(
            (
                receipt.requested,
                receipt.bound,
                receipt.started,
                receipt.terminal,
            ),
            (64, 64, 64, 64),
        )
        self.assertEqual(receipt.worker_count, 8)
        self.assertEqual(receipt.kernel_threads, 1)
        self.assertEqual(receipt.cache_hits, 0)
        self.assertIsNone(receipt.first_failure_ordinal)
        self.assertEqual(
            tuple(item.ordinal for item in receipt.terminals),
            tuple(range(1, 65)),
        )
        self.assertNotEqual(engine.completions, list(range(1, 65)))
        self.assertEqual(
            tuple(item.as_dict() for item in receipt.terminals),
            tuple(self.golden["results"]),
        )
        self.assertEqual(
            receipt.semantic_output_sha256,
            self.golden["semantic_output_sha256"],
        )
        self.assertEqual(len(receipt.execution_provenance_sha256), 64)
        self.assertTrue(is_verified_terminal(receipt.terminals[0]))
        self.assertTrue(is_verified_receipt(receipt))

        constructed = CrrSemanticTerminalV1(
            **receipt.terminals[0].as_dict()
        )
        self.assertFalse(is_verified_terminal(constructed))
        restored = pickle.loads(pickle.dumps(receipt.terminals[0]))
        self.assertEqual(restored, receipt.terminals[0])
        self.assertFalse(is_verified_terminal(restored))

    def test_all_64_finish_after_failures_and_first_failure_is_min_ordinal(
        self,
    ) -> None:
        engine = _new_engine(
            fail_reasons={17: "IV_NO_BRACKET", 41: "TREE_NOT_CONVERGED"},
            unexpected_ordinals={3},
            delay_by_ordinal={3: 0.02, 17: 0.001},
        )
        (
            run_batch,
            is_verified_terminal,
            is_verified_receipt,
        ) = self._runner(engine)

        receipt = run_batch(self.corpus.bound_inputs)

        self.assertEqual(len(engine.calls), 64)
        self.assertEqual(len(engine.completions), 64)
        self.assertEqual(receipt.terminal, 64)
        self.assertEqual(receipt.first_failure_ordinal, 3)
        self.assertEqual(
            receipt.terminals[2].reason_code,
            "BATCH_ENGINE_UNEXPECTED_EXCEPTION",
        )
        self.assertEqual(receipt.terminals[16].reason_code, "IV_NO_BRACKET")
        self.assertEqual(
            receipt.terminals[40].reason_code,
            "TREE_NOT_CONVERGED",
        )
        self.assertFalse(is_verified_terminal(receipt.terminals[2]))
        self.assertTrue(is_verified_terminal(receipt.terminals[0]))
        self.assertTrue(is_verified_receipt(receipt))

    def test_unverified_result_fails_and_never_gets_process_local_evidence(
        self,
    ) -> None:
        engine = _new_engine(unverified_ordinals={9})
        (
            run_batch,
            is_verified_terminal,
            is_verified_receipt,
        ) = self._runner(engine)

        receipt = run_batch(self.corpus.bound_inputs)

        terminal = receipt.terminals[8]
        self.assertEqual(terminal.terminal_status, "FAIL")
        self.assertEqual(terminal.reason_code, "UNVERIFIED_DELTA_RESULT")
        self.assertFalse(is_verified_terminal(terminal))
        self.assertFalse(
            is_verified_terminal(engine.results_by_ordinal[9])
        )
        self.assertTrue(is_verified_receipt(receipt))

    def test_no_cache_or_candidate_pruning_across_runs(self) -> None:
        engine = _new_engine()
        run_batch, _, is_verified_receipt = self._runner(engine)

        first = run_batch(self.corpus.bound_inputs)
        second = run_batch(self.corpus.bound_inputs)

        self.assertEqual(len(engine.calls), 128)
        self.assertEqual(set(engine.calls[:64]), set(range(1, 65)))
        self.assertEqual(set(engine.calls[64:]), set(range(1, 65)))
        self.assertEqual(first.cache_hits, 0)
        self.assertEqual(second.cache_hits, 0)
        self.assertEqual(
            first.semantic_output_sha256,
            second.semantic_output_sha256,
        )
        self.assertTrue(is_verified_receipt(first))
        self.assertTrue(is_verified_receipt(second))

    def test_semantic_hash_excludes_execution_provenance(self) -> None:
        first_engine = _new_engine()
        first_runner, _, first_receipt_verifier = self._runner(first_engine)
        first = first_runner(self.corpus.bound_inputs)

        second_engine = _new_engine()
        (
            second_runner,
            _,
            second_receipt_verifier,
        ) = batch_module._create_exact_64_batch_engine(
            compute_call=second_engine.compute,
            verify_call=second_engine.verify,
            backend_evidence_sha256="b" * 64,
        )
        second = second_runner(self.corpus.bound_inputs)

        self.assertEqual(
            first.semantic_output_sha256,
            second.semantic_output_sha256,
        )
        self.assertNotEqual(
            first.execution_provenance_sha256,
            second.execution_provenance_sha256,
        )
        self.assertTrue(first_receipt_verifier(first))
        self.assertTrue(second_receipt_verifier(second))
        self.assertFalse(first_receipt_verifier(second))
        self.assertFalse(second_receipt_verifier(first))

    def test_rejects_count_duplicate_order_and_snapshot_mutations(self) -> None:
        inputs = self.corpus.bound_inputs
        invalid_vectors = (
            (inputs[:-1], "BATCH_BOUND_VECTOR_INVALID"),
            (inputs + (inputs[-1],), "BATCH_BOUND_VECTOR_INVALID"),
            (
                inputs[:1] + (inputs[0],) + inputs[2:],
                "BATCH_BOUND_VECTOR_DUPLICATE",
            ),
            (
                (inputs[1], inputs[0]) + inputs[2:],
                "BATCH_BOUND_VECTOR_ORDER_INVALID",
            ),
            (
                inputs[:-1]
                + (
                    replace(
                        inputs[-1],
                        option_snapshot_sha256="b" * 64,
                    ),
                ),
                "BATCH_BOUND_VECTOR_SNAPSHOT_INVALID",
            ),
            (
                inputs[:1]
                + (
                    replace(
                        inputs[1],
                        contract_sha256=inputs[0].contract_sha256,
                    ),
                )
                + inputs[2:],
                "BATCH_BOUND_VECTOR_DUPLICATE",
            ),
        )
        for vector, reason_code in invalid_vectors:
            with self.subTest(reason_code=reason_code):
                engine = _new_engine()
                run_batch, _, _ = self._runner(engine)
                with self.assertRaises(batch_module.CrrBatchError) as raised:
                    run_batch(vector)
                self.assertEqual(raised.exception.reason_code, reason_code)
                self.assertEqual(engine.calls, [])

    def test_rejects_duplicate_input_hash_even_with_unique_contracts(self) -> None:
        engine = _new_engine()
        run_batch, _, _ = self._runner(engine)
        with patch(
            "gld_research_core.crr_delta.canonical_snapshot_sha256",
            return_value="f" * 64,
        ):
            with self.assertRaises(batch_module.CrrBatchError) as raised:
                run_batch(self.corpus.bound_inputs)
        self.assertEqual(
            raised.exception.reason_code,
            "BATCH_BOUND_VECTOR_DUPLICATE",
        )
        self.assertEqual(engine.calls, [])

    def test_terminal_and_receipt_reject_bool_as_int_and_oversize_text(
        self,
    ) -> None:
        golden_terminal = dict(self.golden["results"][0])
        golden_terminal["ordinal"] = True
        with self.assertRaises(batch_module.CrrBatchError):
            CrrSemanticTerminalV1(**golden_terminal)

        golden_terminal = dict(self.golden["results"][0])
        golden_terminal["coarse_early_exercise_nodes"] = True
        with self.assertRaises(batch_module.CrrBatchError):
            CrrSemanticTerminalV1(**golden_terminal)

        failure = dict(self.golden["results"][0])
        for field in batch_module.SEMANTIC_NUMERIC_FIELDS:
            failure[field] = None
        failure.update(
            terminal_status="FAIL",
            reason_code="X" * 129,
            early_exercise_detected=False,
        )
        with self.assertRaises(batch_module.CrrBatchError):
            CrrSemanticTerminalV1(**failure)

        engine = _new_engine()
        run_batch, _, _ = self._runner(engine)
        receipt = run_batch(self.corpus.bound_inputs)
        values = receipt.as_dict()
        values["requested"] = True
        with self.assertRaises(batch_module.CrrBatchError):
            CrrExact64BatchReceiptV1(**values)

        failed_engine = _new_engine(fail_reasons={1: "IV_NO_BRACKET"})
        failed_runner, _, _ = self._runner(failed_engine)
        failed_receipt = failed_runner(self.corpus.bound_inputs)
        values = failed_receipt.as_dict()
        values["first_failure_ordinal"] = True
        with self.assertRaises(batch_module.CrrBatchError):
            CrrExact64BatchReceiptV1(**values)

        values = receipt.as_dict()
        values["input_vector_sha256"] = "f" * 64
        values["execution_provenance_sha256"] = (
            batch_module.execution_provenance_sha256(
                backend_evidence_sha256=values[
                    "backend_evidence_sha256"
                ],
                input_vector_sha256=values["input_vector_sha256"],
                semantic_output_sha256_value=values[
                    "semantic_output_sha256"
                ],
            )
        )
        with self.assertRaises(batch_module.CrrBatchError):
            CrrExact64BatchReceiptV1(**values)

    def test_public_surface_has_no_backend_selection_or_trading_seam(self) -> None:
        engine = _new_engine()
        run_batch, _, _ = self._runner(engine)

        self.assertEqual(tuple(inspect.signature(run_batch).parameters), ("inputs",))
        self.assertNotIn("_create_exact_64_batch_engine", batch_module.__all__)
        self.assertFalse(hasattr(batch_module, "compute_american_call_delta"))
        self.assertFalse(
            any(
                token in name.lower()
                for name in batch_module.__all__
                for token in ("broker", "order", "provider", "backend")
            )
        )

    def test_receipt_verifier_rejects_every_structural_copy(self) -> None:
        engine = _new_engine()
        run_batch, _, is_verified_receipt = self._runner(engine)
        receipt = run_batch(self.corpus.bound_inputs)
        other_engine = _new_engine()
        _, _, other_runner_receipt_verifier = self._runner(other_engine)

        direct = CrrExact64BatchReceiptV1(**receipt.as_dict())
        replaced = replace(receipt)
        shallow = copy.copy(receipt)
        deep = copy.deepcopy(receipt)
        restored = pickle.loads(pickle.dumps(receipt))

        self.assertTrue(is_verified_receipt(receipt))
        self.assertFalse(other_runner_receipt_verifier(receipt))
        for candidate in (direct, replaced, shallow, deep, restored):
            with self.subTest(candidate_type=type(candidate).__name__):
                self.assertEqual(candidate, receipt)
                self.assertIsNot(candidate, receipt)
                self.assertFalse(is_verified_receipt(candidate))

    def test_receipt_verifier_binds_ordered_terminal_identities(self) -> None:
        engine = _new_engine()
        run_batch, _, is_verified_receipt = self._runner(engine)
        receipt = run_batch(self.corpus.bound_inputs)
        self.assertTrue(is_verified_receipt(receipt))

        equal_but_constructed = CrrSemanticTerminalV1(
            **receipt.terminals[0].as_dict()
        )
        object.__setattr__(
            receipt,
            "terminals",
            (equal_but_constructed,) + receipt.terminals[1:],
        )

        self.assertEqual(
            receipt.terminals[0].as_dict(),
            self.golden["results"][0],
        )
        self.assertFalse(is_verified_receipt(receipt))

    def test_receipt_verifier_rejects_rehung_backend_provenance(self) -> None:
        engine = _new_engine()
        run_batch, is_verified_terminal, is_verified_receipt = self._runner(
            engine
        )
        receipt = run_batch(self.corpus.bound_inputs)
        self.assertTrue(
            all(
                is_verified_terminal(terminal)
                for terminal in receipt.terminals
            )
        )

        values = receipt.as_dict()
        values["backend_evidence_sha256"] = "b" * 64
        values["execution_provenance_sha256"] = (
            batch_module.execution_provenance_sha256(
                backend_evidence_sha256=values[
                    "backend_evidence_sha256"
                ],
                input_vector_sha256=values["input_vector_sha256"],
                semantic_output_sha256_value=values[
                    "semantic_output_sha256"
                ],
            )
        )
        rehung = CrrExact64BatchReceiptV1(**values)

        self.assertEqual(rehung.terminals, receipt.terminals)
        self.assertFalse(is_verified_receipt(rehung))

    def test_receipt_verifier_rejects_original_after_in_place_tamper(self) -> None:
        engine = _new_engine()
        run_batch, _, is_verified_receipt = self._runner(engine)
        receipt = run_batch(self.corpus.bound_inputs)
        self.assertTrue(is_verified_receipt(receipt))

        replacement_backend = "b" * 64
        replacement_provenance = batch_module.execution_provenance_sha256(
            backend_evidence_sha256=replacement_backend,
            input_vector_sha256=receipt.input_vector_sha256,
            semantic_output_sha256_value=receipt.semantic_output_sha256,
        )
        object.__setattr__(
            receipt,
            "backend_evidence_sha256",
            replacement_backend,
        )
        object.__setattr__(
            receipt,
            "execution_provenance_sha256",
            replacement_provenance,
        )

        self.assertFalse(is_verified_receipt(receipt))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
