from __future__ import annotations

import json
import inspect
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gld_data_contracts import cli as cli_module
from gld_data_contracts.legacy_adapter import adapt_legacy_synthetic_bundle
from gld_simulation.canonical import canonical_json_bytes, canonical_json_sha256


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL = PROJECT_ROOT / "tools" / "validate_gld_data_contracts.py"
LEGACY_PASS = PROJECT_ROOT / "fixtures" / "gld_simulation" / "v1" / "pass"


class GldDataContractCliTests(unittest.TestCase):
    def test_atomic_directory_publish_cleans_failure_and_retry_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "atomic-output"
            artifacts = [
                ("first.json", {"value": 1}),
                ("second.json", {"value": 2}),
            ]
            original = cli_module._publish
            calls = 0

            def fail_second(directory_fd: int, name: str, value: object) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected publish failure")
                original(directory_fd, name, value)

            with patch.object(cli_module, "_publish", side_effect=fail_second):
                with self.assertRaises(OSError):
                    cli_module._publish_output_directory(output, artifacts)

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".atomic-output.staging-*")), [])
            cli_module._publish_output_directory(output, artifacts)
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                ["first.json", "second.json"],
            )

    def test_interrupt_returned_with_committed_mkdir_cleans_empty_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "mkdir-interrupt-output"
            original_mkdir = cli_module.os.mkdir

            def mkdir_then_interrupt(
                path: str,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> None:
                original_mkdir(path, mode=mode, dir_fd=dir_fd)
                raise KeyboardInterrupt

            with patch.object(
                cli_module.os,
                "mkdir",
                side_effect=mkdir_then_interrupt,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    cli_module._publish_output_directory(
                        output,
                        [("one.json", {"value": 1})],
                    )

            self.assertFalse(output.exists())
            self.assertEqual(
                list(root.glob(".mkdir-interrupt-output.staging-*")),
                [],
            )

    def test_atomic_publish_never_replaces_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "existing"
            output.mkdir()
            sentinel = output / "owner-file.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            with self.assertRaises(OSError):
                cli_module._publish_output_directory(
                    output,
                    [("new.json", {"value": 1})],
                )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
            self.assertFalse((output / "new.json").exists())

    def test_parent_fsync_failure_rolls_back_published_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "fsync-output"
            original_fsync = cli_module.os.fsync
            calls = 0

            def fail_parent_fsync(descriptor: int) -> None:
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise OSError("injected parent fsync failure")
                original_fsync(descriptor)

            with patch.object(
                cli_module.os,
                "fsync",
                side_effect=fail_parent_fsync,
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "OUTPUT_PARENT_FSYNC_FAILED_ROLLED_BACK",
                ):
                    cli_module._publish_output_directory(
                        output,
                        [("one.json", {"value": 1})],
                    )

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".fsync-output.staging-*")), [])

    def test_rollback_exception_after_committed_reverse_rename_cleans_up(self) -> None:
        for rollback_exception in (OSError("injected"), KeyboardInterrupt()):
            with self.subTest(exception=type(rollback_exception).__name__):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    output = root / "rollback-interrupt-output"
                    original_fsync = cli_module.os.fsync
                    original_rename = cli_module._rename_directory_noreplace
                    fsync_calls = 0
                    rename_calls = 0

                    def fail_parent_fsync(descriptor: int) -> None:
                        nonlocal fsync_calls
                        fsync_calls += 1
                        if fsync_calls == 3:
                            raise OSError("injected parent fsync failure")
                        original_fsync(descriptor)

                    def interrupt_after_reverse_rename(
                        parent_fd: int,
                        source_name: str,
                        target_name: str,
                    ) -> None:
                        nonlocal rename_calls
                        rename_calls += 1
                        original_rename(parent_fd, source_name, target_name)
                        if rename_calls == 2:
                            raise rollback_exception

                    with patch.object(
                        cli_module.os,
                        "fsync",
                        side_effect=fail_parent_fsync,
                    ), patch.object(
                        cli_module,
                        "_rename_directory_noreplace",
                        side_effect=interrupt_after_reverse_rename,
                    ):
                        expected = (
                            OSError
                            if isinstance(rollback_exception, OSError)
                            else KeyboardInterrupt
                        )
                        with self.assertRaises(expected):
                            cli_module._publish_output_directory(
                                output,
                                [("one.json", {"value": 1})],
                            )

                    self.assertFalse(output.exists())
                    self.assertEqual(
                        list(root.glob(".rollback-interrupt-output.staging-*")),
                        [],
                    )

    def test_interrupt_after_rename_preserves_complete_published_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "interrupt-output"
            artifact = {"value": 1}
            original_fsync = cli_module.os.fsync
            calls = 0

            def interrupt_parent_fsync(descriptor: int) -> None:
                nonlocal calls
                calls += 1
                if calls == 3:
                    raise KeyboardInterrupt
                original_fsync(descriptor)

            with patch.object(
                cli_module.os,
                "fsync",
                side_effect=interrupt_parent_fsync,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    cli_module._publish_output_directory(
                        output,
                        [("one.json", artifact)],
                    )

            self.assertTrue(output.is_dir())
            self.assertEqual(
                (output / "one.json").read_bytes(),
                canonical_json_bytes(artifact) + b"\n",
            )
            self.assertEqual(list(root.glob(".interrupt-output.staging-*")), [])

    def test_interrupt_returned_with_committed_rename_preserves_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "rename-interrupt-output"
            artifact = {"value": 1}
            original_rename = cli_module._rename_directory_noreplace

            def rename_then_interrupt(
                parent_fd: int,
                source_name: str,
                target_name: str,
            ) -> None:
                original_rename(parent_fd, source_name, target_name)
                raise KeyboardInterrupt

            with patch.object(
                cli_module,
                "_rename_directory_noreplace",
                side_effect=rename_then_interrupt,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    cli_module._publish_output_directory(
                        output,
                        [("one.json", artifact)],
                    )

            self.assertTrue(output.is_dir())
            self.assertEqual(
                (output / "one.json").read_bytes(),
                canonical_json_bytes(artifact) + b"\n",
            )
            self.assertEqual(
                list(root.glob(".rename-interrupt-output.staging-*")),
                [],
            )

    def test_trace_interrupt_at_committed_rename_boundary_preserves_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "trace-rename-interrupt-output"
            artifact = {"value": 1}
            source_lines, first_line = inspect.getsourcelines(
                cli_module._publish_output_directory
            )
            boundary_line = first_line + next(
                index
                for index, line in enumerate(source_lines)
                if "committed-return boundary for fault injection" in line
            )

            def interrupt_at_boundary(
                frame: object,
                event: str,
                argument: object,
            ) -> object:
                del argument
                if (
                    event == "line"
                    and getattr(frame, "f_code", None)
                    is cli_module._publish_output_directory.__code__
                    and getattr(frame, "f_lineno", None) == boundary_line
                ):
                    sys.settrace(None)
                    raise KeyboardInterrupt
                return interrupt_at_boundary

            sys.settrace(interrupt_at_boundary)
            try:
                with self.assertRaises(KeyboardInterrupt):
                    cli_module._publish_output_directory(
                        output,
                        [("one.json", artifact)],
                    )
            finally:
                sys.settrace(None)

            self.assertTrue(output.is_dir())
            self.assertEqual(
                (output / "one.json").read_bytes(),
                canonical_json_bytes(artifact) + b"\n",
            )
            self.assertEqual(
                list(root.glob(".trace-rename-interrupt-output.staging-*")),
                [],
            )

    def test_fifo_input_is_rejected_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fifo = root / "entry.fifo"
            os.mkfifo(fifo)
            result = self._run(root / "output", fifo)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                json.loads(result.stdout)["reason_code"],
                "LOCAL_INPUT_OR_OUTPUT_INVALID",
            )
            self.assertFalse((root / "output").exists())

    def test_direct_entry_change_during_read_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry_path = root / "entry.json"
            first = adapt_legacy_synthetic_bundle(LEGACY_PASS)
            first_snapshot = first["option_snapshot"]
            first_snapshot["option_quotes"] = first_snapshot["option_quotes"][:1]
            first["content_hashes"]["option_snapshot_sha256"] = (
                canonical_json_sha256(first_snapshot)
            )
            second = adapt_legacy_synthetic_bundle(LEGACY_PASS)
            second_snapshot = second["option_snapshot"]
            second_snapshot["option_quotes"] = second_snapshot["option_quotes"][:2]
            second["content_hashes"]["option_snapshot_sha256"] = (
                canonical_json_sha256(second_snapshot)
            )
            entry_path.write_bytes(canonical_json_bytes(first) + b"\n")
            replacement = canonical_json_bytes(second) + b"\n"
            original_fstat = cli_module.os.fstat
            calls = 0

            def mutate_after_initial_fstat(descriptor: int) -> os.stat_result:
                nonlocal calls
                metadata = original_fstat(descriptor)
                calls += 1
                if calls == 1:
                    entry_path.write_bytes(replacement)
                return metadata

            with patch.object(
                cli_module.os,
                "fstat",
                side_effect=mutate_after_initial_fstat,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "ENTRY_BUNDLE_FILE_CHANGED_DURING_READ",
                ):
                    cli_module._read_entry_bundle_file(entry_path)

    def _run(
        self,
        output: Path,
        entry_path: Path,
        *,
        demo_failures: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        command = [
            sys.executable,
            str(TOOL),
            "--entry-bundle",
            str(entry_path),
            "--output-dir",
            str(output),
        ]
        if demo_failures:
            command.append("--demo-failures")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        return subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_separate_processes_emit_identical_technical_fact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = adapt_legacy_synthetic_bundle(LEGACY_PASS)
            snapshot = entry["option_snapshot"]
            snapshot["option_quotes"] = snapshot["option_quotes"][:1]
            entry["content_hashes"]["option_snapshot_sha256"] = (
                canonical_json_sha256(snapshot)
            )
            entry_path = root / "entry.json"
            entry_path.write_bytes(canonical_json_bytes(entry) + b"\n")
            first = self._run(
                root / "first", entry_path, demo_failures=True
            )
            second = self._run(root / "second", entry_path)

            self.assertEqual(first.returncode, 0, first.stderr.decode())
            self.assertEqual(second.returncode, 0, second.stderr.decode())
            first_facts = (
                root / "first" / "required-daily-technical-facts-v1.json"
            ).read_bytes()
            second_facts = (
                root / "second" / "required-daily-technical-facts-v1.json"
            ).read_bytes()
            self.assertEqual(first_facts, second_facts)
            report = json.loads(first.stdout)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(
                [item["demonstrated"] for item in report["failure_demonstrations"]],
                [True, True, True, True],
            )
            self.assertEqual(
                report["entry_bundle"]["qualification_status"],
                "STRUCTURALLY_VALID_SYNTHETIC",
            )


if __name__ == "__main__":
    unittest.main()
