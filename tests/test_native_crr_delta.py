from __future__ import annotations

import ast
import copy
from dataclasses import fields
import inspect
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchmarks.crr_p1_contract import load_corpus, load_reference_golden
from benchmarks.h16_contract import (
    load_h16_corpus,
    load_h16_reference_golden,
)
from gld_normalizer.errors import NormalizationError
import gld_research_core.crr_batch as batch_module
from gld_research_core.crr_delta import (
    MODEL_SHA256,
    MODEL_SOURCE_ARTIFACT_SHA256,
)
import gld_research_core.native_crr_delta as native_call_module
from gld_research_core.native_crr_delta import (
    NATIVE_CRR_EXECUTION_STATUS,
    NATIVE_EXECUTION_BACKEND_KIND,
    NATIVE_ORCHESTRATOR_SOURCE_ARTIFACT_SHA256,
    NATIVE_TREE_LOADER_SOURCE_ARTIFACT_SHA256,
    NATIVE_TREE_VERIFIER_CODE_SHA256,
    NativeCrrDeltaResultV1,
    create_native_crr_delta_engine,
    derive_native_crr_combined_backend_evidence_sha256,
)
import gld_research_core.native_tree as native_tree_module
from gld_research_core.native_tree import (
    VerifiedNativeTreeV1,
    is_verified_native_tree_v1,
    load_native_tree_v1,
)
from tools.build_crr_native import build_native_kernel_v1


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "gld_research_core" / "native_crr_delta.py"
def _pass_terminal(ordinal: int, inputs: object, result: object) -> dict[str, object]:
    return {
        "coarse_delta_ppm": result.coarse_delta_ppm,
        "coarse_early_exercise_nodes": result.coarse_early_exercise_nodes,
        "coarse_iv_ppm": result.coarse_iv_ppm,
        "coarse_price_residual_nano_usd": (
            result.coarse_price_residual_nano_usd
        ),
        "contract_id": inputs.contract_id,
        "delta_ppm": result.delta_ppm,
        "early_exercise_detected": bool(
            result.coarse_early_exercise_nodes
            or result.fine_early_exercise_nodes
        ),
        "fine_delta_ppm": result.fine_delta_ppm,
        "fine_early_exercise_nodes": result.fine_early_exercise_nodes,
        "fine_iv_ppm": result.fine_iv_ppm,
        "fine_price_residual_nano_usd": result.fine_price_residual_nano_usd,
        "input_sha256": result.input_sha256,
        "iv_ppm": result.iv_ppm,
        "ordinal": ordinal,
        "reason_code": "PASS",
        "terminal_status": "PASS",
    }


def _failure_terminal(
    ordinal: int,
    inputs: object,
    reason_code: str,
) -> dict[str, object]:
    return {
        "coarse_delta_ppm": None,
        "coarse_early_exercise_nodes": None,
        "coarse_iv_ppm": None,
        "coarse_price_residual_nano_usd": None,
        "contract_id": inputs.contract_id,
        "delta_ppm": None,
        "early_exercise_detected": False,
        "fine_delta_ppm": None,
        "fine_early_exercise_nodes": None,
        "fine_iv_ppm": None,
        "fine_price_residual_nano_usd": None,
        "input_sha256": inputs.input_sha256,
        "iv_ppm": None,
        "ordinal": ordinal,
        "reason_code": reason_code,
        "terminal_status": "FAIL",
    }


class NativeCrrDeltaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Tests explicitly build/load.  The production module has neither path.
        cls.manifest_path = build_native_kernel_v1(repo_root=ROOT)
        cls.manifest = json.loads(cls.manifest_path.read_bytes())
        cls.kernel = load_native_tree_v1(
            cls.manifest_path,
            expected_backend_evidence_sha256=cls.manifest[
                "backend_evidence_sha256"
            ],
        )
        compute, verify, combined_backend_evidence_sha256 = (
            create_native_crr_delta_engine(cls.kernel)
        )
        # Keep closure callables from becoming bound unittest methods.
        cls.compute = staticmethod(compute)
        cls.verify = staticmethod(verify)
        cls.combined_backend_evidence_sha256 = (
            combined_backend_evidence_sha256
        )
        cls.u64 = load_corpus("U64")
        cls.u64_golden = load_reference_golden("U64")
        cls.h16 = load_h16_corpus()
        cls.h16_golden = load_h16_reference_golden()

    def test_static_manifest_evidence_matches_loaded_engine_identity(self) -> None:
        derived = derive_native_crr_combined_backend_evidence_sha256(
            self.manifest_path,
            expected_native_build_backend_evidence_sha256=(
                self.manifest["backend_evidence_sha256"]
            ),
        )

        self.assertEqual(
            derived,
            self.combined_backend_evidence_sha256,
        )
    def test_u64_all_64_match_reference_terminals_field_for_field(self) -> None:
        actual: list[dict[str, object]] = []
        for ordinal, inputs in enumerate(self.u64.bound_inputs, start=1):
            result = self.compute(
                inputs,
                expected_model_sha256=MODEL_SHA256,
            )
            self.assertTrue(self.verify(result))
            self.assertIs(type(result), NativeCrrDeltaResultV1)
            self.assertEqual(result.model_sha256, MODEL_SHA256)
            actual.append(_pass_terminal(ordinal, inputs, result))

        self.assertEqual(tuple(actual), tuple(self.u64_golden["results"]))

    def test_h16_all_16_match_every_pass_and_failure_untimed(self) -> None:
        actual: list[dict[str, object]] = []
        failure_reasons: list[str] = []
        for ordinal, inputs in enumerate(self.h16.inputs, start=1):
            try:
                result = self.compute(
                    inputs,
                    expected_model_sha256=MODEL_SHA256,
                )
            except NormalizationError as error:
                failure_reasons.append(error.reason_code)
                actual.append(
                    _failure_terminal(ordinal, inputs, error.reason_code)
                )
            else:
                self.assertTrue(self.verify(result))
                actual.append(_pass_terminal(ordinal, inputs, result))

        self.assertEqual(tuple(actual), tuple(self.h16_golden["results"]))
        self.assertEqual(len(failure_reasons), 4)
        self.assertEqual(
            failure_reasons,
            [
                row["reason_code"]
                for row in self.h16_golden["results"]
                if row["terminal_status"] == "FAIL"
            ],
        )
        self.assertEqual(
            {
                row["early_exercise_detected"]
                for row in actual
                if row["terminal_status"] == "PASS"
            },
            {False, True},
        )
        self.assertEqual(self.h16_golden["timing_status"], "H16_NOT_TIMED")

    def test_exact64_native_batch_matches_frozen_u64_semantic_hash(self) -> None:
        run_batch, verify_terminal, verify_receipt = (
            batch_module._create_exact_64_batch_engine(
                compute_call=self.compute,
                verify_call=self.verify,
                backend_evidence_sha256=(
                    self.combined_backend_evidence_sha256
                ),
            )
        )

        receipt = run_batch(self.u64.bound_inputs)

        self.assertEqual(
            tuple(item.as_dict() for item in receipt.terminals),
            tuple(self.u64_golden["results"]),
        )
        self.assertEqual(
            receipt.semantic_output_sha256,
            self.u64_golden["semantic_output_sha256"],
        )
        self.assertEqual(
            receipt.backend_evidence_sha256,
            self.combined_backend_evidence_sha256,
        )
        self.assertTrue(all(verify_terminal(item) for item in receipt.terminals))
        self.assertTrue(verify_receipt(receipt))

    def test_provenance_separates_semantic_reference_from_native_execution(
        self,
    ) -> None:
        result = self.compute(
            self.u64.bound_inputs[0],
            expected_model_sha256=MODEL_SHA256,
        )
        self.assertEqual(
            result.semantic_reference_source_artifact_sha256,
            MODEL_SOURCE_ARTIFACT_SHA256,
        )
        self.assertIs(result.semantic_reference_source_executed, False)
        self.assertEqual(
            result.native_orchestrator_source_artifact_sha256,
            NATIVE_ORCHESTRATOR_SOURCE_ARTIFACT_SHA256,
        )
        self.assertEqual(
            result.native_loader_source_artifact_sha256,
            NATIVE_TREE_LOADER_SOURCE_ARTIFACT_SHA256,
        )
        self.assertEqual(
            result.native_loader_verifier_code_sha256,
            NATIVE_TREE_VERIFIER_CODE_SHA256,
        )
        self.assertNotEqual(
            result.semantic_reference_source_artifact_sha256,
            result.native_orchestrator_source_artifact_sha256,
        )
        self.assertEqual(
            result.execution_backend_kind,
            NATIVE_EXECUTION_BACKEND_KIND,
        )
        self.assertEqual(
            result.native_build_backend_evidence_sha256,
            self.manifest["backend_evidence_sha256"],
        )
        self.assertEqual(
            result.native_runtime_environment_sha256,
            self.manifest["runtime_environment_sha256"],
        )
        self.assertEqual(
            result.combined_backend_evidence_sha256,
            self.combined_backend_evidence_sha256,
        )
        self.assertEqual(
            NATIVE_CRR_EXECUTION_STATUS,
            "NATIVE_SHADOW_ONLY_NOT_P1_QUALIFIED",
        )
        self.assertTrue(self.verify(result))

    def test_verifier_rejects_constructed_copied_pickled_and_cross_factory(
        self,
    ) -> None:
        result = self.compute(
            self.u64.bound_inputs[0],
            expected_model_sha256=MODEL_SHA256,
        )
        values = {
            item.name: getattr(result, item.name)
            for item in fields(NativeCrrDeltaResultV1)
        }
        constructed = NativeCrrDeltaResultV1(**values)
        shallow = copy.copy(result)
        deep = copy.deepcopy(result)
        restored = pickle.loads(pickle.dumps(result))
        _, other_verify, other_evidence = create_native_crr_delta_engine(
            self.kernel
        )

        self.assertTrue(self.verify(result))
        for value in (constructed, shallow, deep, restored):
            self.assertEqual(value, result)
            self.assertIsNot(value, result)
            self.assertFalse(self.verify(value))
        self.assertEqual(
            other_evidence,
            self.combined_backend_evidence_sha256,
        )
        self.assertFalse(other_verify(result))

    def test_verifier_binds_result_input_kernel_and_backend_identity(self) -> None:
        inputs = self.u64.bound_inputs[0]
        result = self.compute(inputs, expected_model_sha256=MODEL_SHA256)
        original_spot = inputs.spot_nano_usd
        original_delta = result.delta_ppm
        original_backend = result.combined_backend_evidence_sha256

        try:
            object.__setattr__(inputs, "spot_nano_usd", original_spot + 1)
            self.assertFalse(self.verify(result))
        finally:
            object.__setattr__(inputs, "spot_nano_usd", original_spot)

        try:
            object.__setattr__(result, "delta_ppm", original_delta + 1)
            self.assertFalse(self.verify(result))
        finally:
            object.__setattr__(result, "delta_ppm", original_delta)

        try:
            object.__setattr__(
                result,
                "combined_backend_evidence_sha256",
                "0" * 64,
            )
            self.assertFalse(self.verify(result))
        finally:
            object.__setattr__(
                result,
                "combined_backend_evidence_sha256",
                original_backend,
            )

    def test_factory_rejects_wrapper_and_reattached_exact_type_kernel(self) -> None:
        with self.assertRaises(NormalizationError):
            create_native_crr_delta_engine(
                SimpleNamespace(
                    backend_evidence_sha256=self.manifest[
                        "backend_evidence_sha256"
                    ]
                )
            )

        fake = object.__new__(VerifiedNativeTreeV1)
        for name in (
            "manifest_path",
            "backend_evidence_sha256",
            "_manifest",
            "_module",
            "_identity_function",
            "_tree_function",
        ):
            object.__setattr__(fake, name, getattr(self.kernel, name))
        prior_diagnostic = native_tree_module._LOADED_KERNEL
        try:
            native_tree_module._LOADED_KERNEL = fake
            self.assertFalse(is_verified_native_tree_v1(fake))
            with self.assertRaises(NormalizationError) as raised:
                create_native_crr_delta_engine(fake)
            self.assertTrue(is_verified_native_tree_v1(self.kernel))
            real_compute, real_verify, real_evidence = (
                create_native_crr_delta_engine(self.kernel)
            )
            real_result = real_compute(
                self.u64.bound_inputs[0],
                expected_model_sha256=MODEL_SHA256,
            )
            self.assertTrue(real_verify(real_result))
            self.assertEqual(
                real_evidence,
                self.combined_backend_evidence_sha256,
            )
        finally:
            native_tree_module._LOADED_KERNEL = prior_diagnostic
        self.assertEqual(
            raised.exception.reason_code,
            "DELTA_MODEL_HASH_MISMATCH",
        )

    def test_loader_verifier_tamper_and_revocation_fail_closed(self) -> None:
        inputs = self.u64.bound_inputs[0]
        result = self.compute(inputs, expected_model_sha256=MODEL_SHA256)
        loader_verifier = native_tree_module.is_verified_native_tree_v1
        original_code = loader_verifier.__code__
        try:
            loader_verifier.__code__ = original_code.replace(
                co_name="poisoned_native_loader_verifier"
            )
            self.assertFalse(self.verify(result))
            with self.assertRaises(NormalizationError) as raised:
                self.compute(inputs, expected_model_sha256=MODEL_SHA256)
            self.assertEqual(
                raised.exception.reason_code,
                "DELTA_MODEL_HASH_MISMATCH",
            )
        finally:
            loader_verifier.__code__ = original_code
        self.assertTrue(self.verify(result))

        script = r'''
import json
from pathlib import Path

from benchmarks.crr_p1_contract import load_corpus
from gld_normalizer.errors import NormalizationError
from gld_research_core.crr_delta import MODEL_SHA256
from gld_research_core.native_crr_delta import create_native_crr_delta_engine
import gld_research_core.native_tree as native
from tools.build_crr_native import build_native_kernel_v1

root = Path.cwd()
manifest_path = build_native_kernel_v1(repo_root=root)
manifest = json.loads(manifest_path.read_bytes())
kernel = native.load_native_tree_v1(
    manifest_path,
    expected_backend_evidence_sha256=manifest["backend_evidence_sha256"],
)
compute, verify, _ = create_native_crr_delta_engine(kernel)
inputs = load_corpus("U64").bound_inputs[0]
result = compute(inputs, expected_model_sha256=MODEL_SHA256)
assert verify(result)
original_tree = kernel._tree_function
object.__setattr__(kernel, "_tree_function", lambda *args: None)
assert not native.is_verified_native_tree_v1(kernel)
object.__setattr__(kernel, "_tree_function", original_tree)
assert not native.is_verified_native_tree_v1(kernel)
assert not verify(result)
try:
    compute(inputs, expected_model_sha256=MODEL_SHA256)
except NormalizationError as error:
    assert error.reason_code == "DELTA_MODEL_HASH_MISMATCH"
else:
    raise AssertionError("revoked loader capability did not fail closed")
'''
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(ROOT / "src"), str(ROOT))
        )
        completed = subprocess.run(
            (sys.executable, "-c", script),
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stdout + completed.stderr,
        )

    def test_monkeypatched_orchestration_binding_fails_closed(self) -> None:
        with patch.object(
            native_call_module,
            "create_native_crr_delta_engine",
            lambda value: value,
        ):
            with self.assertRaises(NormalizationError) as raised:
                self.compute(
                    self.u64.bound_inputs[0],
                    expected_model_sha256=MODEL_SHA256,
                )
        self.assertEqual(
            raised.exception.reason_code,
            "DELTA_MODEL_HASH_MISMATCH",
        )

    def test_inner_orchestration_code_and_closure_poisoning_fail_closed(
        self,
    ) -> None:
        compute_cells = dict(
            zip(
                self.compute.__code__.co_freevars,
                self.compute.__closure__ or (),
                strict=True,
            )
        )
        numeric_compute = compute_cells["numeric_compute"].cell_contents
        original_code = numeric_compute.__code__
        try:
            numeric_compute.__code__ = original_code.replace(
                co_name="poisoned_numeric_compute"
            )
            with self.assertRaises(NormalizationError) as raised:
                self.compute(
                    self.u64.bound_inputs[0],
                    expected_model_sha256=MODEL_SHA256,
                )
            self.assertEqual(
                raised.exception.reason_code,
                "DELTA_MODEL_HASH_MISMATCH",
            )
        finally:
            numeric_compute.__code__ = original_code

        numeric_cell = compute_cells["numeric_compute"]
        original_numeric_compute = numeric_cell.cell_contents
        try:
            numeric_cell.cell_contents = lambda **values: values
            with self.assertRaises(NormalizationError) as raised:
                self.compute(
                    self.u64.bound_inputs[0],
                    expected_model_sha256=MODEL_SHA256,
                )
            self.assertEqual(
                raised.exception.reason_code,
                "DELTA_MODEL_HASH_MISMATCH",
            )
        finally:
            numeric_cell.cell_contents = original_numeric_compute

    def test_guard_registry_cell_replacement_cannot_hide_numeric_change(
        self,
    ) -> None:
        compute_cells = dict(
            zip(
                self.compute.__code__.co_freevars,
                self.compute.__closure__ or (),
                strict=True,
            )
        )
        require_runtime = compute_cells[
            "require_captured_runtime"
        ].cell_contents
        runtime_cells = dict(
            zip(
                require_runtime.__code__.co_freevars,
                require_runtime.__closure__ or (),
                strict=True,
            )
        )
        registry_cell = runtime_cells["inner_callable_state_bindings"]
        numeric_cell = compute_cells["numeric_compute"]
        original_registry = registry_cell.cell_contents
        original_numeric_compute = numeric_cell.cell_contents

        def changed_numeric_compute(**values: object) -> tuple[int, ...]:
            payload = list(original_numeric_compute(**values))
            payload[1] += 1
            payload[5] += 1
            return tuple(payload)

        try:
            numeric_cell.cell_contents = changed_numeric_compute
            registry_variants = (
                ("empty", ()),
                (
                    "same_length_duplicate",
                    (original_registry[0],) * len(original_registry),
                ),
                (
                    "same_length_partial",
                    original_registry[:-1] + (original_registry[-2],),
                ),
                ("exact_record_copy", tuple([*original_registry])),
            )
            for name, registry in registry_variants:
                with self.subTest(name=name):
                    registry_cell.cell_contents = registry
                    with self.assertRaises(NormalizationError) as raised:
                        self.compute(
                            self.u64.bound_inputs[0],
                            expected_model_sha256=MODEL_SHA256,
                        )
                    self.assertEqual(
                        raised.exception.reason_code,
                        "DELTA_MODEL_HASH_MISMATCH",
                    )
        finally:
            numeric_cell.cell_contents = original_numeric_compute
            registry_cell.cell_contents = original_registry

        result = self.compute(
            self.u64.bound_inputs[0],
            expected_model_sha256=MODEL_SHA256,
        )
        self.assertTrue(self.verify(result))

    def test_frozen_private_config_tampering_fails_before_execution(self) -> None:
        inputs = self.u64.bound_inputs[0]
        result = self.compute(inputs, expected_model_sha256=MODEL_SHA256)
        mutations = (
            ("_FINE_STEPS", native_call_module._COARSE_STEPS),
            ("_IV_SUITE_TOLERANCE", 1.0),
            ("_DECIMAL_CONTEXT_PRECISION", 20),
        )
        for name, replacement in mutations:
            with self.subTest(name=name), patch.object(
                native_call_module,
                name,
                replacement,
            ):
                self.assertFalse(self.verify(result))
                with self.assertRaises(NormalizationError) as raised:
                    self.compute(inputs, expected_model_sha256=MODEL_SHA256)
                self.assertEqual(
                    raised.exception.reason_code,
                    "DELTA_MODEL_HASH_MISMATCH",
                )

    def test_exact_type_guards_reject_bool_and_wrong_model_hash(self) -> None:
        inputs = self.u64.bound_inputs[0]
        with self.assertRaises(NormalizationError) as raised:
            self.compute(inputs, expected_model_sha256=True)
        self.assertEqual(
            raised.exception.reason_code,
            "DELTA_MODEL_HASH_MISMATCH",
        )

        result = self.compute(inputs, expected_model_sha256=MODEL_SHA256)
        values = {
            item.name: getattr(result, item.name)
            for item in fields(NativeCrrDeltaResultV1)
        }
        values["iv_ppm"] = True
        with self.assertRaises(NormalizationError):
            NativeCrrDeltaResultV1(**values)

    def test_production_surface_has_no_build_load_fallback_or_authority_path(
        self,
    ) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("tools.build_crr_native", imported_names)
        self.assertNotIn("build_native_kernel_v1", imported_names | called_names)
        self.assertNotIn("load_native_tree_v1", imported_names | called_names)
        self.assertNotIn("compute_american_call_delta", imported_names)
        for forbidden in ("broker", "provider", "order", "fallback"):
            self.assertNotIn(forbidden, native_call_module.__all__)
        self.assertEqual(
            tuple(inspect.signature(create_native_crr_delta_engine).parameters),
            ("kernel",),
        )
        self.assertNotIn("VerifiedNativeTreeV1", native_call_module.__all__)
        self.assertNotIn("load_native_tree_v1", native_call_module.__all__)
        self.assertNotIn("build_native_kernel_v1", native_call_module.__all__)
        self.assertNotIn("_LOADED_KERNEL", source)
        self.assertEqual(
            NATIVE_ORCHESTRATOR_SOURCE_ARTIFACT_SHA256,
            __import__("hashlib").sha256(MODULE_PATH.read_bytes()).hexdigest(),
        )
        native_loader_path = ROOT / "src" / "gld_research_core" / "native_tree.py"
        self.assertEqual(
            NATIVE_TREE_LOADER_SOURCE_ARTIFACT_SHA256,
            __import__("hashlib")
            .sha256(native_loader_path.read_bytes())
            .hexdigest(),
        )
        self.assertEqual(
            NATIVE_TREE_VERIFIER_CODE_SHA256,
            __import__("hashlib")
            .sha256(is_verified_native_tree_v1.__code__.co_code)
            .hexdigest(),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
