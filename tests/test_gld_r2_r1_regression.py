from __future__ import annotations

import ast
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
R1_ACCEPTED_COMMIT = "b21400d799f7b441d35f528a5980b1ce855997a7"
R1_ACCEPTANCE_RELATIVE = Path(
    "artifacts/gld-entry-decision-f0-owner-acceptance-v1"
)
R1_OWNER_RECEIPT_RELATIVE = Path(
    "2026-09-01-2157-gld-entry-decision-f0-owner-acceptance-receipt-v0.1.md"
)
R1_ACCEPTANCE = PROJECT_ROOT / R1_ACCEPTANCE_RELATIVE
R1_OWNER_RECEIPT = PROJECT_ROOT / R1_OWNER_RECEIPT_RELATIVE
R1_TOOL = PROJECT_ROOT / "tools" / "run_gld_entry_decision_f0.py"
R2_SOURCE = PROJECT_ROOT / "src" / "gld_r2_theta_qualification"

KEY_R1_SHA256 = {
    R1_ACCEPTANCE / "acceptance-manifest.json": (
        "f0004cdb593121f1cb41f632f4b36c3399fe5d380d938e909f0cd4bcf02141a2"
    ),
    R1_ACCEPTANCE / "index.html": (
        "5590a21a5fbccc34a1ce495484f9ec16b8c29c89b10def6349c59ed6d28aa18b"
    ),
    R1_ACCEPTANCE / "engineering-evidence.html": (
        "6a96bcbacd7d121bffa2fcf5b6d3f243096936181c0bbc9d431032adf14ec35f"
    ),
    R1_ACCEPTANCE / "reference" / "GLD_ENTRY_DECISION_F0_SPEC.md": (
        "240c6dc5df135c1062db067ad6c8e215fa39a041403a488e1aed32c0f9d83c81"
    ),
    R1_ACCEPTANCE / "reference" / "GLD_ENTRY_POLICY_RESEARCH_ASSET_F0.md": (
        "1541636445c029602f7b0356b03b4149a4e8b7fb1cda545dd8b5664218ed082e"
    ),
    R1_OWNER_RECEIPT: (
        "e894aa5777a5f7b2179eb4dc3ebd8b80f12c3e80d74c2c2adca312cdb3d36231"
    ),
}

CURRENT_POLICY_ASSET_SHA256 = (
    "86d582c49d60ae2558c3f962fe689a5d3c3b2a524a3523883cb8bf9c2eeb8b51"
)
KNOWN_FRESH_REGENERATION_DIFFERENCES = frozenset(
    {
        Path("acceptance-manifest.json"),
        Path("reference/GLD_ENTRY_POLICY_RESEARCH_ASSET_F0.md"),
    }
)

FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "aiohttp",
        "alpaca",
        "http",
        "httpx",
        "ib_insync",
        "ibapi",
        "requests",
        "robin_stocks",
        "socket",
        "urllib",
        "websocket",
        "websockets",
    }
)
FORBIDDEN_R1_WRITE_TARGET_LITERALS = (
    R1_ACCEPTANCE_RELATIVE.as_posix(),
    R1_OWNER_RECEIPT_RELATIVE.as_posix(),
)


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _file_tree(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class R1AcceptedBaselineRegressionTests(unittest.TestCase):
    def test_formal_acceptance_tree_and_owner_receipt_match_accepted_commit(self) -> None:
        diff = subprocess.run(
            [
                "git",
                "diff",
                "--no-ext-diff",
                "--exit-code",
                R1_ACCEPTED_COMMIT,
                "--",
                R1_ACCEPTANCE_RELATIVE.as_posix(),
                R1_OWNER_RECEIPT_RELATIVE.as_posix(),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(diff.returncode, 0, diff.stdout + diff.stderr)

        untracked = subprocess.run(
            [
                "git",
                "ls-files",
                "--others",
                "--exclude-standard",
                "--",
                R1_ACCEPTANCE_RELATIVE.as_posix(),
                R1_OWNER_RECEIPT_RELATIVE.as_posix(),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(untracked.stdout, "")
        self.assertEqual(len(_file_tree(R1_ACCEPTANCE)), 45)

    def test_key_formal_r1_bytes_have_accepted_hashes(self) -> None:
        for path, expected_sha256 in KEY_R1_SHA256.items():
            with self.subTest(path=path.relative_to(PROJECT_ROOT)):
                self.assertTrue(path.is_file())
                self.assertEqual(_file_sha256(path), expected_sha256)

    def test_fresh_regeneration_has_only_the_two_known_owner_append_differences(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fresh = Path(temporary) / "fresh-r1"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(R1_TOOL),
                    "--demo-acceptance",
                    "--output",
                    str(fresh),
                ],
                cwd=PROJECT_ROOT,
                env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            accepted_tree = _file_tree(R1_ACCEPTANCE)
            fresh_tree = _file_tree(fresh)
            self.assertEqual(frozenset(accepted_tree), frozenset(fresh_tree))
            differences = frozenset(
                path
                for path in accepted_tree
                if accepted_tree[path] != fresh_tree[path]
            )
            self.assertEqual(
                differences,
                KNOWN_FRESH_REGENERATION_DIFFERENCES,
            )

            source_policy_asset = (
                PROJECT_ROOT / "docs" / "GLD_ENTRY_POLICY_RESEARCH_ASSET_F0.md"
            )
            fresh_policy_asset = (
                fresh / "reference" / "GLD_ENTRY_POLICY_RESEARCH_ASSET_F0.md"
            )
            self.assertEqual(
                fresh_policy_asset.read_bytes(),
                source_policy_asset.read_bytes(),
            )
            self.assertEqual(
                _file_sha256(fresh_policy_asset),
                CURRENT_POLICY_ASSET_SHA256,
            )

            accepted_manifest = json.loads(
                accepted_tree[Path("acceptance-manifest.json")]
            )
            fresh_manifest = json.loads(
                fresh_tree[Path("acceptance-manifest.json")]
            )
            accepted_reference = {
                item["asset_id"]: item for item in accepted_manifest["reference_assets"]
            }
            fresh_reference = {
                item["asset_id"]: item for item in fresh_manifest["reference_assets"]
            }
            policy_asset_id = "GLD_ENTRY_POLICY_RESEARCH_ASSET_F0"
            self.assertEqual(
                accepted_reference[policy_asset_id]["sha256"],
                KEY_R1_SHA256[
                    R1_ACCEPTANCE
                    / "reference"
                    / "GLD_ENTRY_POLICY_RESEARCH_ASSET_F0.md"
                ],
            )
            self.assertEqual(
                fresh_reference[policy_asset_id]["sha256"],
                CURRENT_POLICY_ASSET_SHA256,
            )

            normalized_accepted = dict(accepted_manifest)
            normalized_fresh = dict(fresh_manifest)
            normalized_accepted.pop("manifest_sha256")
            normalized_fresh.pop("manifest_sha256")
            normalized_accepted_references = [
                dict(item) for item in normalized_accepted["reference_assets"]
            ]
            normalized_accepted["reference_assets"] = normalized_accepted_references
            for item in normalized_accepted_references:
                if item["asset_id"] == policy_asset_id:
                    item["sha256"] = CURRENT_POLICY_ASSET_SHA256
            self.assertEqual(normalized_accepted, normalized_fresh)


class R2StaticSafetyBoundaryTests(unittest.TestCase):
    def test_r2_source_has_no_network_or_broker_imports(self) -> None:
        violations: list[str] = []
        for path in sorted(R2_SOURCE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    imported = (node.module,)
                else:
                    continue
                for module in imported:
                    root = module.split(".", 1)[0]
                    if root in FORBIDDEN_IMPORT_ROOTS:
                        violations.append(f"{path.name}:{node.lineno}:{module}")
        self.assertEqual(violations, [])

    def test_r2_source_does_not_name_formal_r1_write_targets(self) -> None:
        violations: list[str] = []
        for path in sorted(R2_SOURCE.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for target in FORBIDDEN_R1_WRITE_TARGET_LITERALS:
                if target in source:
                    violations.append(f"{path.name}:{target}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
