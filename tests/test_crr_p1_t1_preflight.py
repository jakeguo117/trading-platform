from __future__ import annotations

import argparse
import ast
import inspect
import os
from pathlib import Path
from pathlib import PurePosixPath
import tempfile
import unittest
from unittest.mock import patch

from benchmarks import crr_p1_t1_preflight as t1
from tools.run_crr_p1_t1_preflight import build_parser


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "benchmarks" / "crr_p1_t1_preflight.py"
CLI_PATH = ROOT / "tools" / "run_crr_p1_t1_preflight.py"


class T1PreflightTests(unittest.TestCase):
    def test_cli_exposes_only_binding_and_output_arguments(self) -> None:
        parser = build_parser()
        option_strings = {
            option
            for action in parser._actions
            for option in action.option_strings
        }
        self.assertEqual(
            option_strings,
            {
                "-h",
                "--help",
                "--output-directory",
                "--performance-component-aggregate-path",
            },
        )
        with self.assertRaises(SystemExit):
            parser.parse_args([])
        parsed = parser.parse_args(
            [
                "--performance-component-aggregate-path",
                "component.json",
                "--output-directory",
                "out",
            ]
        )
        self.assertEqual(parsed.output_directory, Path("out"))

    def test_policy_has_no_timeout_or_worker_override(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        cli_source = CLI_PATH.read_text(encoding="utf-8")
        self.assertNotIn("getenv(", source)
        self.assertNotIn("environ[", source)
        self.assertNotIn("--timeout", cli_source)
        self.assertNotIn("--grace", cli_source)
        self.assertNotIn("--worker", cli_source)
        self.assertEqual(t1.T1_LATE_WRITE_OFFSET_NS, 10_000_000)

    def test_production_entry_has_exact_keyword_only_signature(self) -> None:
        signature = inspect.signature(t1.run_formal_t1_preflight)
        self.assertEqual(
            tuple(signature.parameters),
            (
                "performance_component_aggregate_path",
                "output_directory",
            ),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in signature.parameters.values()
            )
        )

    def test_module_target_is_spawn_safe_and_has_fixed_behavior(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        target = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_t1_uncooperative_late_writer"
        )
        calls = {
            ast.unparse(node.func)
            for node in ast.walk(target)
            if isinstance(node, ast.Call)
        }
        self.assertIn("signal.signal", calls)
        self.assertIn("connection.send_bytes", calls)
        self.assertIn("time.monotonic_ns", calls)

    def test_full_evidence_path_invokes_semantic_revalidators(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        required_calls = (
            "performance._validate_runner_bundle(",
            "performance._validate_lineage(",
            "performance._validate_stage_record(",
            "performance._validate_decision_sidecar_record(",
            "performance._component_aggregate(",
            "validate_raw_sample_receipt(",
        )
        for call in required_calls:
            self.assertIn(call, source)
        self.assertEqual(len(t1._expected_performance_output_paths()), 142)

    def test_evidence_reader_rejects_nested_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "root"
            outside = Path(temporary_directory) / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "lineage.json").write_text("{}\n", encoding="ascii")
            (root / "runner-01").symlink_to(outside, target_is_directory=True)
            root_descriptor = os.open(root, t1._directory_open_flags())
            try:
                with self.assertRaises(t1.T1PreflightError) as rejected:
                    t1._read_regular_at(
                        root_descriptor,
                        PurePosixPath("runner-01/lineage.json"),
                    )
            finally:
                os.close(root_descriptor)
            self.assertEqual(rejected.exception.reason_code, "T1_EVIDENCE_PATH_INVALID")

    def test_output_leaf_symlink_and_creation_race_never_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            outside = root / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("keep", encoding="ascii")
            symlink_output = root / "symlink-output"
            symlink_output.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(t1.T1PreflightError) as symlink_rejected:
                t1._prepare_output_parent(symlink_output)
            self.assertEqual(
                symlink_rejected.exception.reason_code,
                "T1_OUTPUT_DIRECTORY_EXISTS",
            )
            self.assertEqual(sentinel.read_text(encoding="ascii"), "keep")

            raced_output = root / "raced-output"
            parent_descriptor, output_parent, output_leaf = (
                t1._prepare_output_parent(raced_output)
            )
            try:
                raced_output.mkdir()
                raced_sentinel = raced_output / "t1-preflight.json"
                raced_sentinel.write_text("do-not-overwrite", encoding="ascii")
                with self.assertRaises(t1.T1PreflightError) as race_rejected:
                    t1._publish_new_output_directory(
                        parent_descriptor=parent_descriptor,
                        output_parent=output_parent,
                        output_leaf=output_leaf,
                        document={"schema_version": "TEST"},
                    )
            finally:
                os.close(parent_descriptor)
            self.assertEqual(
                race_rejected.exception.reason_code,
                "T1_OUTPUT_WRITE_FAILED",
            )
            self.assertEqual(
                raced_sentinel.read_text(encoding="ascii"),
                "do-not-overwrite",
            )

    def test_output_publication_is_exclusive_and_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "receipt"
            parent_descriptor, output_parent, output_leaf = (
                t1._prepare_output_parent(output)
            )
            try:
                path = t1._publish_new_output_directory(
                    parent_descriptor=parent_descriptor,
                    output_parent=output_parent,
                    output_leaf=output_leaf,
                    document={"schema_version": "TEST"},
                )
            finally:
                os.close(parent_descriptor)
            self.assertEqual(path, output / t1.T1_OUTPUT_FILENAME)
            self.assertEqual(
                path.read_bytes(),
                b'{"schema_version":"TEST"}\n',
            )
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)
            self.assertFalse(
                any(item.name.endswith(".tmp") for item in output.iterdir())
            )
            self.assertEqual(output.stat().st_mode & 0o777, 0o500)
            output.chmod(0o700)

    def test_temp_replacement_races_never_publish_a_receipt(self) -> None:
        for replacement in ("symlink", "regular"):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                output = root / "receipt"
                parent_descriptor, output_parent, output_leaf = (
                    t1._prepare_output_parent(output)
                )
                original_link = os.link

                def replace_then_link(
                    source: str,
                    destination: str,
                    *,
                    src_dir_fd: int,
                    dst_dir_fd: int,
                    follow_symlinks: bool,
                ) -> None:
                    os.unlink(source, dir_fd=src_dir_fd)
                    if replacement == "symlink":
                        forged = root / "forged.json"
                        forged.write_text('{"forged":true}\n', encoding="ascii")
                        os.symlink(
                            forged,
                            source,
                            dir_fd=src_dir_fd,
                        )
                    else:
                        descriptor = os.open(
                            source,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                            dir_fd=src_dir_fd,
                        )
                        try:
                            os.write(descriptor, b'{"forged":true}\n')
                        finally:
                            os.close(descriptor)
                    original_link(
                        source,
                        destination,
                        src_dir_fd=src_dir_fd,
                        dst_dir_fd=dst_dir_fd,
                        follow_symlinks=follow_symlinks,
                    )

                try:
                    with patch.object(t1.os, "link", side_effect=replace_then_link):
                        with self.assertRaises(t1.T1PreflightError) as rejected:
                            t1._publish_new_output_directory(
                                parent_descriptor=parent_descriptor,
                                output_parent=output_parent,
                                output_leaf=output_leaf,
                                document={"schema_version": "TEST"},
                            )
                finally:
                    os.close(parent_descriptor)
                self.assertEqual(
                    rejected.exception.reason_code,
                    "T1_OUTPUT_WRITE_FAILED",
                )
                self.assertFalse((output / t1.T1_OUTPUT_FILENAME).exists())

    def test_post_link_fsync_failure_rolls_back_final_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "receipt"
            parent_descriptor, output_parent, output_leaf = (
                t1._prepare_output_parent(output)
            )
            original_fsync = os.fsync
            calls = 0

            def fail_directory_fsync(descriptor: int) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic directory fsync failure")
                original_fsync(descriptor)

            try:
                with patch.object(t1.os, "fsync", side_effect=fail_directory_fsync):
                    with self.assertRaises(t1.T1PreflightError) as rejected:
                        t1._publish_new_output_directory(
                            parent_descriptor=parent_descriptor,
                            output_parent=output_parent,
                            output_leaf=output_leaf,
                            document={"schema_version": "TEST"},
                        )
            finally:
                os.close(parent_descriptor)
            self.assertEqual(rejected.exception.reason_code, "T1_OUTPUT_WRITE_FAILED")
            self.assertFalse((output / t1.T1_OUTPUT_FILENAME).exists())

    def test_external_hardlink_escape_is_rejected_and_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "receipt"
            escaped = root / "escaped-receipt.json"
            parent_descriptor, output_parent, output_leaf = (
                t1._prepare_output_parent(output)
            )
            original_link = os.link

            def escape_then_link(
                source: str,
                destination: str,
                *,
                src_dir_fd: int,
                dst_dir_fd: int,
                follow_symlinks: bool,
            ) -> None:
                original_link(
                    source,
                    escaped,
                    src_dir_fd=src_dir_fd,
                    follow_symlinks=follow_symlinks,
                )
                original_link(
                    source,
                    destination,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                    follow_symlinks=follow_symlinks,
                )

            try:
                with patch.object(t1.os, "link", side_effect=escape_then_link):
                    with self.assertRaises(t1.T1PreflightError) as rejected:
                        t1._publish_new_output_directory(
                            parent_descriptor=parent_descriptor,
                            output_parent=output_parent,
                            output_leaf=output_leaf,
                            document={"schema_version": "TEST"},
                        )
            finally:
                os.close(parent_descriptor)
            self.assertEqual(rejected.exception.reason_code, "T1_OUTPUT_WRITE_FAILED")
            self.assertFalse((output / t1.T1_OUTPUT_FILENAME).exists())
            self.assertTrue(escaped.is_file())

    def test_stale_parent_path_never_returns_a_misleading_receipt_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            parent = root / "parent"
            parent.mkdir()
            output = parent / "receipt"
            parent_descriptor, output_parent, output_leaf = (
                t1._prepare_output_parent(output)
            )
            moved = root / "parent-moved"
            parent.rename(moved)
            parent.mkdir()
            try:
                with self.assertRaises(t1.T1PreflightError) as rejected:
                    t1._publish_new_output_directory(
                        parent_descriptor=parent_descriptor,
                        output_parent=output_parent,
                        output_leaf=output_leaf,
                        document={"schema_version": "TEST"},
                    )
            finally:
                os.close(parent_descriptor)
            self.assertEqual(rejected.exception.reason_code, "T1_OUTPUT_WRITE_FAILED")
            self.assertFalse((parent / "receipt").exists())
            self.assertFalse((moved / "receipt").exists())

    def test_evidence_faults_close_owned_descriptors_and_use_stable_codes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            evidence = root / "evidence"
            evidence.mkdir()
            component = evidence / "component-aggregate.json"
            component.write_text("{}\n", encoding="ascii")
            baseline = len(os.listdir("/dev/fd"))
            original_fstat = os.fstat
            injected = False

            def fail_first_fstat(descriptor: int) -> os.stat_result:
                nonlocal injected
                if not injected:
                    injected = True
                    raise OSError("synthetic fstat failure")
                return original_fstat(descriptor)

            with patch.object(t1.os, "fstat", side_effect=fail_first_fstat):
                with self.assertRaises(t1.T1PreflightError) as root_rejected:
                    t1._open_evidence_root(component)
            self.assertEqual(
                root_rejected.exception.reason_code,
                "T1_PERFORMANCE_BINDING_INVALID",
            )
            self.assertEqual(len(os.listdir("/dev/fd")), baseline)

            root_descriptor = os.open(evidence, t1._directory_open_flags())
            try:
                baseline_with_root = len(os.listdir("/dev/fd"))
                injected = False
                with patch.object(t1.os, "fstat", side_effect=fail_first_fstat):
                    with self.assertRaises(t1.T1PreflightError) as file_rejected:
                        t1._open_regular_at(
                            root_descriptor,
                            PurePosixPath("component-aggregate.json"),
                        )
                self.assertEqual(
                    file_rejected.exception.reason_code,
                    "T1_EVIDENCE_PATH_INVALID",
                )
                self.assertEqual(len(os.listdir("/dev/fd")), baseline_with_root)

                with patch.object(
                    t1.os,
                    "read",
                    side_effect=OSError("synthetic read failure"),
                ):
                    with self.assertRaises(t1.T1PreflightError) as read_rejected:
                        t1._read_regular_at(
                            root_descriptor,
                            PurePosixPath("component-aggregate.json"),
                        )
                self.assertEqual(
                    read_rejected.exception.reason_code,
                    "T1_EVIDENCE_READ_FAILED",
                )
                self.assertEqual(len(os.listdir("/dev/fd")), baseline_with_root)
            finally:
                os.close(root_descriptor)

    def test_real_uncooperative_late_writer_passes_t1_only(self) -> None:
        evidence = t1.run_t1_watchdog_case()

        self.assertEqual(evidence["case_id"], t1.T1_CASE_ID)
        self.assertEqual(evidence["result"]["status"], "FAIL_CLOSED")
        self.assertEqual(evidence["result"]["reason_code"], "P1_HARD_TIMEOUT")
        self.assertTrue(evidence["result"]["artifact_absent"])
        self.assertTrue(evidence["result"]["semantic_receipt_absent"])
        receipt = evidence["supervisor_receipt"]
        self.assertGreaterEqual(receipt["late_messages_suppressed"], 1)
        self.assertEqual(receipt["messages_accepted"], 0)
        self.assertTrue(receipt["terminate_sent"])
        self.assertTrue(receipt["kill_sent"])
        self.assertTrue(receipt["child_reaped"])
        self.assertTrue(receipt["process_group_gone"])
        self.assertFalse(receipt["partial_result_published"])
        self.assertGreaterEqual(evidence["wall_elapsed_ns"], 5_000_000_000)
        self.assertLessEqual(evidence["wall_elapsed_ns"], 5_250_000_000)


if __name__ == "__main__":
    unittest.main()
