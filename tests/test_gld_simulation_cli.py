from __future__ import annotations

from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gld_simulation.canonical import canonical_json_bytes
from gld_simulation.cli import main
from tools.build_crr_native import build_native_kernel_v1


ROOT = Path(__file__).resolve().parents[1]
PASS_BUNDLE = ROOT / "fixtures" / "gld_simulation" / "v1" / "pass"


class GldSimulationCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.native_manifest = build_native_kernel_v1(repo_root=ROOT)
        manifest = json.loads(cls.native_manifest.read_bytes())
        cls.backend_evidence_sha256 = manifest["backend_evidence_sha256"]

    def _run(self, bundle: Path, output: Path) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools" / "run_gld_simulation.py"),
                "--bundle",
                str(bundle),
                "--native-manifest",
                str(self.native_manifest),
                "--expected-backend-evidence-sha256",
                self.backend_evidence_sha256,
                "--output",
                str(output),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )

    def test_fresh_processes_are_byte_deterministic_and_raw_change_rehashes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_out = root / "first"
            second_out = root / "second"
            first = self._run(PASS_BUNDLE, first_out)
            second = self._run(PASS_BUNDLE, second_out)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            first_decision = (first_out / "decision-result.json").read_bytes()
            second_decision = (second_out / "decision-result.json").read_bytes()
            first_card = (first_out / "decision-card.html").read_bytes()
            second_card = (second_out / "decision-card.html").read_bytes()
            self.assertEqual(first_decision, second_decision)
            self.assertEqual(first_card, second_card)

            changed_bundle = root / "changed-bundle"
            shutil.copytree(PASS_BUNDLE, changed_bundle)
            account_path = changed_bundle / "account" / "snapshot-a.json"
            account = json.loads(account_path.read_bytes())
            account["unrealized_profit_nano_usd"] += 1
            account_bytes = canonical_json_bytes(account) + b"\n"
            account_path.write_bytes(account_bytes)
            manifest_path = changed_bundle / "manifest.json"
            manifest = json.loads(manifest_path.read_bytes())
            entry = next(
                item
                for item in manifest["files"]
                if item["logical_key"] == "account_snapshot_a"
            )
            entry["byte_size"] = len(account_bytes)
            entry["sha256"] = sha256(account_bytes).hexdigest()
            manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")

            changed_out = root / "changed"
            changed = self._run(changed_bundle, changed_out)
            self.assertEqual(changed.returncode, 0, changed.stderr)
            changed_decision = (changed_out / "decision-result.json").read_bytes()
            self.assertNotEqual(changed_decision, first_decision)

    def test_cli_never_overwrites_an_existing_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            first = self._run(
                ROOT / "fixtures" / "gld_simulation" / "v1" / "no_action",
                output,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            before = (output / "decision-result.json").read_bytes()

            second = self._run(
                ROOT / "fixtures" / "gld_simulation" / "v1" / "no_action",
                output,
            )
            self.assertEqual(second.returncode, 1)
            self.assertEqual(second.stderr.strip(), "SIM_OUTPUT_ALREADY_EXISTS")
            self.assertEqual(
                (output / "decision-result.json").read_bytes(),
                before,
            )

    def test_cli_rejects_native_manifest_outside_repo_trust_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside_manifest = root / "manifest_v1.json"
            shutil.copyfile(self.native_manifest, outside_manifest)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "src")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "run_gld_simulation.py"),
                    "--bundle",
                    str(PASS_BUNDLE),
                    "--native-manifest",
                    str(outside_manifest),
                    "--expected-backend-evidence-sha256",
                    self.backend_evidence_sha256,
                    "--output",
                    str(root / "out"),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(
                result.stderr.strip(),
                "SIM_NATIVE_MANIFEST_TRUST_ROOT_INVALID",
            )
            self.assertFalse((root / "out").exists())

    def test_cli_returns_nonzero_for_supervisor_fail_closed_result(self) -> None:
        result = SimpleNamespace(
            status="FAIL_CLOSED",
            decision_document={"reason_code": "P1_HARD_TIMEOUT"},
            decision_card_path=Path("unused.html"),
        )
        with (
            patch("gld_simulation.cli.run_simulation_workflow", return_value=result),
            patch("sys.stderr", new_callable=StringIO) as stderr,
            patch("sys.stdout", new_callable=StringIO) as stdout,
        ):
            return_code = main(
                [
                    "--bundle",
                    "bundle",
                    "--native-manifest",
                    "manifest.json",
                    "--expected-backend-evidence-sha256",
                    "0" * 64,
                    "--output",
                    "out",
                ]
            )

        self.assertEqual(return_code, 1)
        self.assertEqual(stderr.getvalue(), "P1_HARD_TIMEOUT\n")
        self.assertEqual(stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
