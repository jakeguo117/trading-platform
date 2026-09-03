from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from gld_entry_decision_f0.canonical import canonical_json_bytes
from gld_r2_theta_qualification.examples import (
    build_synthetic_acceptance_case_v1,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL = PROJECT_ROOT / "tools" / "run_gld_r2_theta_qualification.py"
FROZEN_FIXTURE = (
    PROJECT_ROOT
    / "fixtures"
    / "gld_r2_theta_qualification"
    / "v1"
    / "acceptance-cases.json"
)


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _run(output: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(TOOL),
            *extra,
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _classifications(value: object) -> list[object]:
    found: list[object] = []
    if type(value) is dict:
        if "classification" in value:
            found.append(value["classification"])
        for child in value.values():
            found.extend(_classifications(child))
    elif type(value) is list:
        for child in value:
            found.extend(_classifications(child))
    return found


class R2Wave1CliTests(unittest.TestCase):
    def test_demo_publishes_canonical_non_actionable_acceptance_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "r2-wave1"
            completed = _run(output, "--demo-acceptance")

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), str(output))
            manifest_raw = (output / "acceptance-manifest.json").read_bytes()
            manifest = json.loads(manifest_raw)
            self.assertEqual(manifest_raw, canonical_json_bytes(manifest) + b"\n")
            self.assertEqual(manifest["classification"], "SYNTHETIC_ONLY")
            self.assertEqual(manifest["authority_status"], "NO_DECISION_EFFECT")
            self.assertEqual(manifest["status"], "R2_INFRASTRUCTURE_VERIFIED")
            self.assertEqual(manifest["next"], "METADATA_AUTHORIZATION_REQUIRED")
            self.assertFalse(manifest["actionable"])
            self.assertEqual(manifest["broker_order_count"], 0)
            self.assertFalse(manifest["real_data_accessed"])
            self.assertFalse(manifest["real_theta_produced"])
            self.assertEqual(manifest["owner_status"], "OWNER_ACCEPTANCE_PENDING")
            self.assertFalse(manifest["scientific_terminal_generated"])
            self.assertFalse(manifest["qualification_generated"])
            self.assertFalse(manifest["owner_receipt_generated"])
            self.assertEqual(manifest["candidate_flow"]["registry_count"], 16)
            self.assertEqual(manifest["candidate_flow"]["promotable_count"], 12)
            self.assertEqual(manifest["candidate_flow"]["control_count"], 4)
            self.assertIsNone(manifest["candidate_flow"]["evaluated_k"])
            self.assertEqual(manifest["candidate_flow"]["historical_theta_count"], 0)
            self.assertEqual(manifest["candidate_flow"]["forward_theta_count"], 0)
            self.assertEqual(manifest["candidate_flow"]["final_theta_count"], 0)

            unsigned = dict(manifest)
            manifest_sha256 = unsigned.pop("manifest_sha256")
            self.assertEqual(
                manifest_sha256,
                sha256(canonical_json_bytes(unsigned)).hexdigest(),
            )
            self.assertEqual(
                manifest["r1_regression_receipt"]["status"],
                "R1_ACCEPTED_BYTES_UNCHANGED",
            )
            self.assertEqual(
                manifest["r1_regression_receipt"]["tree_file_count"],
                45,
            )
            self.assertEqual(
                len(manifest["r1_regression_receipt"]["assets"]),
                46,
            )
            self.assertEqual(
                [
                    asset["role"]
                    for asset in manifest["r1_regression_receipt"]["assets"]
                ].count("OWNER_RECEIPT"),
                1,
            )
            self.assertEqual(
                manifest["determinism_receipt"]["status"],
                "REPEATED_SYNTHETIC_DIAGNOSTICS_BYTE_IDENTICAL",
            )

            contract_result = json.loads(
                (
                    output
                    / "scenarios"
                    / "contract_identity"
                    / "result.json"
                ).read_bytes()
            )
            contract_detail = contract_result["detail"]
            self.assertTrue(contract_detail["synthetic_identity_only"])
            self.assertFalse(
                contract_detail["owner_choice_changes_qualification"]
            )
            self.assertNotEqual(
                contract_detail["synthetic_owner_approve_identity_sha256"],
                contract_detail["synthetic_owner_reject_identity_sha256"],
            )
            self.assertFalse(contract_detail["historical_theta_generated"])
            self.assertFalse(contract_detail["qualification_generated"])

            tree = _tree(output)
            self.assertIn("index.html", tree)
            self.assertIn("engineering-evidence.html", tree)
            for rendered in manifest["rendered_artifacts"]:
                payload = tree[rendered["path"]]
                self.assertEqual(sha256(payload).hexdigest(), rendered["sha256"])
            for scenario in manifest["scenarios"]:
                self.assertEqual(scenario["classification"], "SYNTHETIC_ONLY")
                self.assertEqual(
                    scenario["automated_evidence_status"],
                    "AUTOMATED_EVIDENCE_PASS",
                )
                input_payload = tree[scenario["input_path"]]
                result_payload = tree[scenario["result_path"]]
                self.assertEqual(
                    sha256(input_payload).hexdigest(),
                    scenario["artifact_hashes"]["input_file_sha256"],
                )
                self.assertEqual(
                    sha256(result_payload).hexdigest(),
                    scenario["artifact_hashes"]["result_file_sha256"],
                )
                self.assertEqual(
                    input_payload,
                    canonical_json_bytes(json.loads(input_payload)) + b"\n",
                )
                self.assertEqual(
                    result_payload,
                    canonical_json_bytes(json.loads(result_payload)) + b"\n",
                )
                self.assertEqual(
                    set(_classifications(json.loads(input_payload))),
                    {"SYNTHETIC_ONLY"},
                )
                self.assertEqual(
                    set(_classifications(json.loads(result_payload))),
                    {"SYNTHETIC_ONLY"},
                )

    def test_examples_are_detached_deep_copies(self) -> None:
        first = build_synthetic_acceptance_case_v1("registry_16_to_12")
        first["inputs"]["gate_count"] = 99
        second = build_synthetic_acceptance_case_v1("registry_16_to_12")

        self.assertEqual(second["inputs"]["gate_count"], 4)

    def test_two_subprocess_outputs_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            first_run = _run(first, "--demo-acceptance")
            second_run = _run(second, "--demo-acceptance")

            self.assertEqual(first_run.returncode, 0, first_run.stderr)
            self.assertEqual(second_run.returncode, 0, second_run.stderr)
            self.assertEqual(_tree(first), _tree(second))

    def test_existing_output_is_never_clobbered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "r2-wave1"
            first = _run(output, "--demo-acceptance")
            self.assertEqual(first.returncode, 0, first.stderr)
            before = _tree(output)

            second = _run(output, "--demo-acceptance")
            self.assertEqual(second.returncode, 2)
            self.assertIn("ERROR OUTPUT_ALREADY_EXISTS", second.stderr)
            self.assertEqual(_tree(output), before)

    def test_invalid_fixture_only_emits_run_failure_not_scientific_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "bad-fixture.json"
            fixture.write_bytes(canonical_json_bytes({"classification": "SYNTHETIC_ONLY"}) + b"\n")
            output = root / "failed"

            completed = _run(output, "--fixture", str(fixture))
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(sorted(_tree(output)), ["run-receipt.json"])
            receipt = json.loads((output / "run-receipt.json").read_bytes())
            self.assertEqual(receipt["run_status"], "RUN_FAILED")
            self.assertEqual(receipt["classification"], "SYNTHETIC_ONLY")
            self.assertFalse(receipt["scientific_terminal_generated"])
            self.assertFalse(receipt["historical_theta_generated"])
            self.assertFalse(receipt["qualification_generated"])
            self.assertFalse(receipt["owner_receipt_generated"])
            self.assertFalse(receipt["actionable"])
            self.assertEqual(receipt["broker_order_count"], 0)

    def test_fixture_cannot_forge_r1_baseline_or_owner_reason(self) -> None:
        original = json.loads(FROZEN_FIXTURE.read_bytes())
        mutations = (
            lambda value: value["r1_regression"].update(
                {"tree_root": "README.md"}
            ),
            lambda value: value["scenarios"][0].update(
                {"reason_code": "OWNER_APPROVED_R2_RESEARCH_THETA"}
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, mutate in enumerate(mutations):
                with self.subTest(index=index):
                    forged = json.loads(canonical_json_bytes(original))
                    mutate(forged)
                    fixture = root / f"forged-{index}.json"
                    fixture.write_bytes(canonical_json_bytes(forged) + b"\n")
                    output = root / f"failed-{index}"
                    completed = _run(output, "--fixture", str(fixture))
                    self.assertEqual(completed.returncode, 2)
                    self.assertIn(
                        "ERROR R2_SYNTHETIC_FIXTURE_IDENTITY_INVALID",
                        completed.stderr,
                    )
                    receipt = json.loads(
                        (output / "run-receipt.json").read_bytes()
                    )
                    self.assertFalse(receipt["scientific_terminal_generated"])
                    self.assertFalse(receipt["owner_receipt_generated"])

    def test_public_package_does_not_export_raw_research_helpers(self) -> None:
        import gld_r2_theta_qualification as package

        forbidden = {
            "evaluate_joint_policy_bootstrap",
            "build_synthetic_forward_diagnostic_receipt",
            "render_owner_acceptance_index",
            "build_acceptance_artifacts",
        }
        self.assertTrue(forbidden.isdisjoint(package.__all__))


if __name__ == "__main__":
    unittest.main()
