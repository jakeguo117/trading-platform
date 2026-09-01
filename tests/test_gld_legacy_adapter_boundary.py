from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from gld_data_contracts.legacy_adapter import adapt_legacy_synthetic_bundle
from gld_simulation.errors import RawBundleError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_PASS = PROJECT_ROOT / "fixtures" / "gld_simulation" / "v1" / "pass"


class LegacyAdapterInputBoundaryTests(unittest.TestCase):
    def _copy_fixture(self, temporary: str) -> Path:
        copied = Path(temporary) / "pass"
        shutil.copytree(LEGACY_PASS, copied)
        return copied

    def _run_adapter_with_timeout(self, root: Path) -> str:
        script = """
from pathlib import Path
import sys
from gld_data_contracts.legacy_adapter import adapt_legacy_synthetic_bundle

try:
    adapt_legacy_synthetic_bundle(Path(sys.argv[1]))
except Exception as error:
    print(getattr(error, "reason_code", type(error).__name__))
else:
    raise SystemExit(2)
"""
        environment = os.environ.copy()
        existing_pythonpath = environment.get("PYTHONPATH")
        source_path = str(PROJECT_ROOT / "src")
        environment["PYTHONPATH"] = (
            source_path
            if not existing_pythonpath
            else os.pathsep.join((source_path, existing_pythonpath))
        )
        completed = subprocess.run(
            [sys.executable, "-c", script, str(root)],
            cwd=PROJECT_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout.strip()

    def test_normal_fixture_still_adapts(self) -> None:
        entry = adapt_legacy_synthetic_bundle(LEGACY_PASS)

        self.assertEqual(entry["schema_version"], "ENTRY_FACT_BUNDLE_V1")
        self.assertEqual(entry["classification"], "SYNTHETIC_ONLY")

    def test_manifest_fifo_is_rejected_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = self._copy_fixture(temporary)
            manifest_path = copied / "manifest.json"
            manifest_path.unlink()
            os.mkfifo(manifest_path)

            reason = self._run_adapter_with_timeout(copied)

        self.assertEqual(reason, "RAW_BUNDLE_NONREGULAR_FILE_FORBIDDEN")

    def test_oversized_manifest_is_rejected_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = self._copy_fixture(temporary)
            manifest_path = copied / "manifest.json"
            os.truncate(manifest_path, 1_000_001)

            reason = self._run_adapter_with_timeout(copied)

        self.assertEqual(reason, "RAW_MANIFEST_SIZE_INVALID")

    def test_registered_fifo_is_rejected_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = self._copy_fixture(temporary)
            registered_path = copied / "control" / "rule-package.json"
            registered_path.unlink()
            os.mkfifo(registered_path)

            reason = self._run_adapter_with_timeout(copied)

        self.assertEqual(reason, "RAW_BUNDLE_NONREGULAR_FILE_FORBIDDEN")

    def test_oversized_registered_file_is_rejected_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = self._copy_fixture(temporary)
            registered_path = copied / "control" / "rule-package.json"
            os.truncate(registered_path, 8_000_001)

            reason = self._run_adapter_with_timeout(copied)

        self.assertEqual(reason, "RAW_FILE_SIZE_LIMIT_EXCEEDED")

    def test_registered_file_size_must_match_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = self._copy_fixture(temporary)
            registered_path = copied / "control" / "rule-package.json"
            with registered_path.open("ab") as stream:
                stream.write(b"x")

            with self.assertRaisesRegex(
                RawBundleError,
                "RAW_FILE_SIZE_MISMATCH",
            ):
                adapt_legacy_synthetic_bundle(copied)

    def test_unregistered_file_remains_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = self._copy_fixture(temporary)
            (copied / "market" / "unregistered.json").write_text(
                "{}\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(
                RawBundleError,
                "RAW_BUNDLE_UNREGISTERED_FILE",
            ):
                adapt_legacy_synthetic_bundle(copied)

    def test_unregistered_empty_directory_remains_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = self._copy_fixture(temporary)
            (copied / "market" / "empty" / "nested").mkdir(parents=True)

            with self.assertRaisesRegex(
                RawBundleError,
                "RAW_BUNDLE_UNREGISTERED_DIRECTORY",
            ):
                adapt_legacy_synthetic_bundle(copied)

    def test_tree_entry_count_is_bounded_before_full_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = self._copy_fixture(temporary)
            market = copied / "market"
            for index in range(4_097):
                (market / f"extra-{index:04d}").touch()

            with self.assertRaisesRegex(
                RawBundleError,
                "RAW_BUNDLE_TREE_RESOURCE_LIMIT_EXCEEDED",
            ):
                adapt_legacy_synthetic_bundle(copied)


if __name__ == "__main__":
    unittest.main()
