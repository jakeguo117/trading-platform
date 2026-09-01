from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import ctypes
from dataclasses import fields
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import pickle
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from gld_normalizer.errors import NormalizationError
import gld_research_core.crr_delta as reference
import gld_research_core.native_tree as native_tree_module
from gld_research_core.native_tree import (
    FROZEN_COMPILER_FLAGS,
    NATIVE_ABI_ID,
    NATIVE_ABI_VERSION,
    NATIVE_MANIFEST_SCHEMA,
    VerifiedNativeTreeV1,
    decode_native_tree_result_v1,
    is_verified_native_tree_v1,
    load_native_tree_v1,
    validate_native_identity_v1,
)
from tools.build_crr_native import build_native_kernel_v1
import tools.build_crr_native as builder_module


ROOT = Path(__file__).resolve().parents[1]


def _reference_raw(
    spot: float,
    strike: float,
    years: float,
    rate: float,
    effective_yield: float,
    volatility: float,
    steps: int,
) -> tuple[int, float | None, float | None, int]:
    try:
        result = reference._crr_american_call_tree(
            spot=spot,
            strike=strike,
            years=years,
            rate=rate,
            effective_yield=effective_yield,
            volatility=volatility,
            steps=steps,
        )
    except NormalizationError as error:
        status = {
            "TREE_NUMERIC_INVALID": 1,
            "TREE_PROBABILITY_INVALID": 2,
            "DELTA_OUT_OF_RANGE": 3,
        }[error.reason_code]
        return status, None, None, 0
    return 0, result.price, result.delta, result.early_exercise_nodes


class CrrNativeTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_path = build_native_kernel_v1(repo_root=ROOT)
        cls.manifest_bytes = cls.manifest_path.read_bytes()
        cls.manifest = json.loads(cls.manifest_bytes)
        cls.kernel = load_native_tree_v1(
            cls.manifest_path,
            expected_backend_evidence_sha256=cls.manifest[
                "backend_evidence_sha256"
            ],
        )

    def test_builder_writes_hash_addressed_canonical_manifest(self) -> None:
        self.assertEqual(self.manifest["schema_version"], NATIVE_MANIFEST_SCHEMA)
        self.assertEqual(
            self.manifest_path.parent.name,
            self.manifest["build_input_sha256"],
        )
        self.assertEqual(
            self.manifest_bytes,
            (
                json.dumps(
                    self.manifest,
                    ensure_ascii=True,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode("ascii"),
        )
        self.assertNotIn(str(ROOT).encode(), self.manifest_bytes)
        self.assertNotIn(b"/Users/", self.manifest_bytes)
        self.assertEqual(
            tuple(self.manifest["compiler_flags"]),
            FROZEN_COMPILER_FLAGS,
        )

        binary_path = self.manifest_path.parent / self.manifest["binary_filename"]
        self.assertEqual(
            sha256(binary_path.read_bytes()).hexdigest(),
            self.manifest["binary_sha256"],
        )

    def test_builder_is_idempotent_and_never_replaces_binary(self) -> None:
        binary_path = self.manifest_path.parent / self.manifest["binary_filename"]
        before = (binary_path.stat().st_ino, binary_path.stat().st_mtime_ns)
        repeated = build_native_kernel_v1(repo_root=ROOT)
        after = (binary_path.stat().st_ino, binary_path.stat().st_mtime_ns)
        self.assertEqual(repeated, self.manifest_path)
        self.assertEqual(after, before)

    def test_builder_reuse_requires_exact_nested_manifest_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            copied_manifest = tmp_root / self.manifest_path.name
            copied_binary = tmp_root / self.manifest["binary_filename"]
            shutil.copy2(
                self.manifest_path.parent / self.manifest["binary_filename"],
                copied_binary,
            )
            document = json.loads(self.manifest_bytes)
            document["runtime_environment"]["float_rounds"] = True
            document["build_manifest_sha256"] = (
                builder_module._build_manifest_sha256(document)
            )
            document["backend_evidence_sha256"] = (
                builder_module._backend_evidence_sha256(document)
            )
            copied_manifest.write_bytes(builder_module.canonical_json_bytes(document))
            expected_base = {
                key: self.manifest[key]
                for key in builder_module._BASE_MANIFEST_KEYS
            }
            self.assertFalse(
                builder_module._validate_existing(
                    copied_manifest,
                    expected_base,
                )
            )

    def test_identity_is_exact_and_manifest_bound(self) -> None:
        identity = self.kernel.identity_v1()
        self.assertEqual(
            identity,
            (
                NATIVE_ABI_ID,
                NATIVE_ABI_VERSION,
                self.manifest["source_manifest_sha256"],
                self.manifest["build_input_sha256"],
                self.manifest["compiler_flags_sha256"],
                self.manifest["dependency_manifest_sha256"],
            ),
        )
        self.assertEqual(validate_native_identity_v1(identity, self.manifest), identity)

        malformed = list(identity)
        malformed[1] = True
        with self.assertRaises(NormalizationError) as raised:
            validate_native_identity_v1(tuple(malformed), self.manifest)
        self.assertEqual(raised.exception.reason_code, "DELTA_MODEL_HASH_MISMATCH")

    def test_verified_wrapper_cannot_be_constructed_by_a_caller(self) -> None:
        with self.assertRaises(TypeError):
            native_tree_module.VerifiedNativeTreeV1(None)

    def test_process_local_verifier_rejects_clones_and_global_replacement(self) -> None:
        self.assertTrue(is_verified_native_tree_v1(self.kernel))
        self.assertIs(
            load_native_tree_v1(
                self.manifest_path,
                expected_backend_evidence_sha256=self.manifest[
                    "backend_evidence_sha256"
                ],
            ),
            self.kernel,
        )

        blank = object.__new__(VerifiedNativeTreeV1)
        self.assertFalse(is_verified_native_tree_v1(blank))
        clone = object.__new__(VerifiedNativeTreeV1)
        for field in fields(VerifiedNativeTreeV1):
            object.__setattr__(clone, field.name, getattr(self.kernel, field.name))
        self.assertFalse(is_verified_native_tree_v1(clone))

        prior_diagnostic = native_tree_module._LOADED_KERNEL
        try:
            native_tree_module._LOADED_KERNEL = clone
            self.assertFalse(is_verified_native_tree_v1(clone))
            self.assertTrue(is_verified_native_tree_v1(self.kernel))
            self.assertIs(
                load_native_tree_v1(
                    self.manifest_path,
                    expected_backend_evidence_sha256=self.manifest[
                        "backend_evidence_sha256"
                    ],
                ),
                self.kernel,
            )
        finally:
            native_tree_module._LOADED_KERNEL = prior_diagnostic

        self.assertFalse(hasattr(native_tree_module, "_new_verified_kernel"))
        self.assertFalse(hasattr(native_tree_module, "_VERIFIED_CONSTRUCTION_TOKEN"))
        self.assertFalse(
            any(name.startswith("register_verified") for name in dir(native_tree_module))
        )

    def test_verified_wrapper_cannot_be_copied_or_serialized(self) -> None:
        for operation in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(TypeError):
                    operation(self.kernel)

    def test_mutation_and_reattachment_revoke_process_local_trust(self) -> None:
        child_program = r'''
from pathlib import Path
import shutil
import sys
import tempfile
from types import MappingProxyType

from gld_normalizer.errors import NormalizationError
import gld_research_core.native_tree as native

manifest_path = Path(sys.argv[1])
backend = sys.argv[2]
case = sys.argv[3]

if case == "manifest_bytes":
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        document_path = tmp_root / manifest_path.name
        document_path.write_bytes(manifest_path.read_bytes())
        document = native._load_manifest_bytes(manifest_path)
        shutil.copy2(
            manifest_path.parent / document["binary_filename"],
            tmp_root / document["binary_filename"],
        )
        kernel = native.load_native_tree_v1(
            document_path,
            expected_backend_evidence_sha256=backend,
        )
        assert native.is_verified_native_tree_v1(kernel)
        document_path.write_bytes(document_path.read_bytes() + b" ")
        assert not native.is_verified_native_tree_v1(kernel)
else:
    kernel = native.load_native_tree_v1(
        manifest_path,
        expected_backend_evidence_sha256=backend,
    )
    assert native.is_verified_native_tree_v1(kernel)
    if case == "backend_field":
        original = kernel.backend_evidence_sha256
        object.__setattr__(kernel, "backend_evidence_sha256", "0" * 64)
        assert not native.is_verified_native_tree_v1(kernel)
        object.__setattr__(kernel, "backend_evidence_sha256", original)
    elif case == "manifest_field":
        original = kernel._manifest
        object.__setattr__(kernel, "_manifest", MappingProxyType(dict(original)))
        assert not native.is_verified_native_tree_v1(kernel)
        object.__setattr__(kernel, "_manifest", original)
    elif case == "identity_field":
        original = kernel._identity_function
        object.__setattr__(kernel, "_identity_function", kernel._tree_function)
        assert not native.is_verified_native_tree_v1(kernel)
        object.__setattr__(kernel, "_identity_function", original)
    elif case == "tree_field":
        original = kernel._tree_function
        object.__setattr__(kernel, "_tree_function", kernel._identity_function)
        assert not native.is_verified_native_tree_v1(kernel)
        object.__setattr__(kernel, "_tree_function", original)
    elif case == "module_identity":
        original = kernel._module.identity_v1
        kernel._module.identity_v1 = kernel._tree_function
        assert not native.is_verified_native_tree_v1(kernel)
        kernel._module.identity_v1 = original
    elif case == "module_tree":
        original = kernel._module.tree_eval_v1
        kernel._module.tree_eval_v1 = kernel._identity_function
        assert not native.is_verified_native_tree_v1(kernel)
        kernel._module.tree_eval_v1 = original
    else:
        raise AssertionError(case)
    assert not native.is_verified_native_tree_v1(kernel)
    try:
        native.load_native_tree_v1(
            manifest_path,
            expected_backend_evidence_sha256=backend,
        )
    except NormalizationError as error:
        assert error.reason_code == "DELTA_MODEL_HASH_MISMATCH"
    else:
        raise AssertionError("revoked kernel was reaccepted")
'''
        for case in (
            "backend_field",
            "manifest_field",
            "identity_field",
            "tree_field",
            "module_identity",
            "module_tree",
            "manifest_bytes",
        ):
            with self.subTest(case=case):
                environment = dict(os.environ)
                environment["PYTHONPATH"] = os.pathsep.join(
                    (str(ROOT / "src"), str(ROOT))
                )
                completed = subprocess.run(
                    (
                        sys.executable,
                        "-c",
                        child_program,
                        str(self.manifest_path),
                        self.manifest["backend_evidence_sha256"],
                        case,
                    ),
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

    def test_raw_float_hex_matches_reference_tree_corpus(self) -> None:
        cases = []
        for steps in (2, 256, 512, 513, 1024, 1025):
            cases.append(
                (
                    f"normal_{steps}",
                    (123.5, 117.25, 0.375, 0.017, 0.006, 0.41, steps),
                )
            )
        cases.extend(
            (
                ("early_exercise", (150.0, 100.0, 1.0, 0.01, 0.25, 0.20, 256)),
                ("probability_high_q", (100.0, 100.0, 1.0, 0.0, 1.0, 0.0001, 512)),
                ("probability_negative_rate", (100.0, 100.0, 1.0, -1.0, 0.0, 0.0001, 512)),
                ("delta_near_zero", (100.0, 500.0, 0.25, 0.02, 0.0, 0.10, 1025)),
                ("delta_near_one", (500.0, 100.0, 0.25, 0.02, 0.0, 0.10, 1025)),
            )
        )
        for name, values in cases:
            with self.subTest(name=name):
                expected = _reference_raw(*values)
                actual = self.kernel.raw_tree_eval_v1(*values)
                self.assertEqual(actual[0], expected[0])
                self.assertEqual(actual[3], expected[3])
                if expected[0] == 0:
                    self.assertEqual(actual[1].hex(), expected[1].hex())
                    self.assertEqual(actual[2].hex(), expected[2].hex())
                else:
                    self.assertIsNone(actual[1])
                    self.assertIsNone(actual[2])

    def test_numeric_domain_and_step_cap_fail_closed(self) -> None:
        invalid_cases = (
            (math.nan, 100.0, 1.0, 0.01, 0.0, 0.2, 512),
            (100.0, math.inf, 1.0, 0.01, 0.0, 0.2, 512),
            (0.0, 100.0, 1.0, 0.01, 0.0, 0.2, 512),
            (100.0, 100.0, 0.0, 0.01, 0.0, 0.2, 512),
            (100.0, 100.0, 1.0, 0.01, 0.0, 0.0, 512),
            (100.0, 100.0, 1.0, 0.01, 0.0, 0.2, 1),
            (100.0, 100.0, 1.0, 0.01, 0.0, 0.2, 1026),
        )
        for values in invalid_cases:
            with self.subTest(values=values):
                self.assertEqual(
                    self.kernel.raw_tree_eval_v1(*values),
                    (1, None, None, 0),
                )

    def test_rounding_mode_drift_is_model_hash_mismatch(self) -> None:
        libc = ctypes.CDLL(None)
        fegetround = libc.fegetround
        fegetround.argtypes = ()
        fegetround.restype = ctypes.c_int
        fesetround = libc.fesetround
        fesetround.argtypes = (ctypes.c_int,)
        fesetround.restype = ctypes.c_int
        original = fegetround()
        self.assertEqual(original, 0)
        try:
            self.assertEqual(fesetround(0x00800000), 0)  # arm64 FE_DOWNWARD
            with self.assertRaises(NormalizationError) as raised:
                self.kernel.raw_tree_eval_v1(
                    123.5,
                    117.25,
                    0.375,
                    0.017,
                    0.006,
                    0.41,
                    512,
                )
            self.assertEqual(
                raised.exception.reason_code,
                "DELTA_MODEL_HASH_MISMATCH",
            )
        finally:
            self.assertEqual(fesetround(original), 0)

    def test_python_decoder_rejects_unknown_or_malformed_native_schema(self) -> None:
        invalid = (
            (4, None, None, 0),
            (True, None, None, 0),
            (0, 1.0, 0.5, True),
            (0, 1, 0.5, 0),
            (1, 1.0, None, 0),
            (0, 1.0, 0.5),
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(NormalizationError) as raised:
                    decode_native_tree_result_v1(raw)
                self.assertEqual(
                    raised.exception.reason_code,
                    "DELTA_MODEL_HASH_MISMATCH",
                )

    def test_loader_rejects_manifest_source_flags_and_binary_tamper(self) -> None:
        mutations = (
            ("source_manifest_sha256", "0" * 64),
            ("compiler_flags_sha256", "1" * 64),
            ("backend_evidence_sha256", "2" * 64),
        )
        for field, replacement in mutations:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                tmp_root = Path(tmp)
                copied_manifest = tmp_root / self.manifest_path.name
                document = dict(self.manifest)
                document[field] = replacement
                copied_manifest.write_bytes(
                    (
                        json.dumps(document, separators=(",", ":"), sort_keys=True)
                        + "\n"
                    ).encode("ascii")
                )
                shutil.copy2(
                    self.manifest_path.parent / self.manifest["binary_filename"],
                    tmp_root / self.manifest["binary_filename"],
                )
                with self.assertRaises(NormalizationError) as raised:
                    load_native_tree_v1(
                        copied_manifest,
                        expected_backend_evidence_sha256=self.manifest[
                            "backend_evidence_sha256"
                        ],
                    )
                self.assertEqual(
                    raised.exception.reason_code,
                    "DELTA_MODEL_HASH_MISMATCH",
                )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            copied_manifest = tmp_root / self.manifest_path.name
            copied_manifest.write_bytes(self.manifest_bytes)
            copied_binary = tmp_root / self.manifest["binary_filename"]
            shutil.copy2(
                self.manifest_path.parent / self.manifest["binary_filename"],
                copied_binary,
            )
            binary = bytearray(copied_binary.read_bytes())
            binary[len(binary) // 2] ^= 1
            copied_binary.write_bytes(binary)
            with self.assertRaises(NormalizationError) as raised:
                load_native_tree_v1(
                    copied_manifest,
                    expected_backend_evidence_sha256=self.manifest[
                        "backend_evidence_sha256"
                    ],
                )
            self.assertEqual(
                raised.exception.reason_code,
                "DELTA_MODEL_HASH_MISMATCH",
            )

    def test_manifest_nested_values_require_exact_json_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            copied_manifest = tmp_root / self.manifest_path.name
            document = json.loads(self.manifest_bytes)
            self.assertEqual(document["runtime_environment"]["float_rounds"], 1)
            document["runtime_environment"]["float_rounds"] = True
            copied_manifest.write_bytes(
                (
                    json.dumps(document, separators=(",", ":"), sort_keys=True)
                    + "\n"
                ).encode("ascii")
            )
            shutil.copy2(
                self.manifest_path.parent / self.manifest["binary_filename"],
                tmp_root / self.manifest["binary_filename"],
            )
            with self.assertRaises(NormalizationError) as raised:
                native_tree_module._verify_manifest(
                    copied_manifest,
                    expected_backend_evidence_sha256=self.manifest[
                        "backend_evidence_sha256"
                    ],
                )
            self.assertEqual(
                raised.exception.reason_code,
                "DELTA_MODEL_HASH_MISMATCH",
            )

    def test_loader_never_builds_or_falls_back(self) -> None:
        missing = self.manifest_path.parent / "missing-manifest.json"
        with self.assertRaises(NormalizationError) as raised:
            load_native_tree_v1(
                missing,
                expected_backend_evidence_sha256=self.manifest[
                    "backend_evidence_sha256"
                ],
            )
        self.assertEqual(raised.exception.reason_code, "DELTA_MODEL_HASH_MISMATCH")
        self.assertFalse(missing.exists())

    def test_two_thread_gil_release_observation(self) -> None:
        values = (123.5, 117.25, 0.375, 0.017, 0.006, 0.41, 1025)
        iterations = 400

        def worker() -> tuple[int, int, int]:
            wall_start = time.perf_counter_ns()
            cpu_start = time.thread_time_ns()
            for _ in range(iterations):
                raw = self.kernel.raw_tree_eval_v1(*values)
                if raw[0] != 0:
                    raise AssertionError(raw)
            return wall_start, time.perf_counter_ns(), time.thread_time_ns() - cpu_start

        total_start = time.perf_counter_ns()
        with ThreadPoolExecutor(max_workers=2) as pool:
            observations = tuple(pool.map(lambda _: worker(), range(2)))
        total_wall = time.perf_counter_ns() - total_start

        overlap = min(item[1] for item in observations) - max(
            item[0] for item in observations
        )
        total_thread_cpu = sum(item[2] for item in observations)
        self.assertGreater(overlap, 0)
        self.assertGreater(total_thread_cpu, 0)
        self.assertGreater(total_wall, 0)
        # This is an observable concurrency receipt, not a latency or speedup SLO.
        self.assertLess(total_thread_cpu / total_wall, 2.5)


if __name__ == "__main__":
    unittest.main()
