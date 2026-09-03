from __future__ import annotations

from copy import deepcopy
import re
import unittest

from gld_r2_theta_qualification.cli import build_acceptance_artifacts
from gld_r2_theta_qualification.contracts import R2QualificationError
from gld_r2_theta_qualification.publish import (
    validate_synthetic_acceptance_manifest,
)
from gld_r2_theta_qualification.render_html import (
    render_engineering_evidence,
    render_owner_acceptance_index,
)


class R2Wave1HtmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        artifacts = build_acceptance_artifacts()
        cls.artifacts = artifacts
        cls.manifest = validate_synthetic_acceptance_manifest_bytes(
            artifacts["acceptance-manifest.json"]
        )

    def test_owner_page_is_plain_chinese_and_explicit_about_current_boundary(self) -> None:
        html = render_owner_acceptance_index(self.manifest)
        self.assertIn("Trading Platform｜R2 基础设施验收", html)
        self.assertIn("16 个候选", html)
        self.assertIn("12 个可晋升", html)
        self.assertIn("4 个对照组", html)
        self.assertIn("K：尚未运行真实数据", html)
        self.assertIn("历史候选：0", html)
        self.assertIn("Forward：0", html)
        self.assertIn("最终研究 θ：0", html)
        self.assertIn("现在可以验收", html)
        self.assertIn("仍需真实数据和单独授权", html)
        self.assertIn("R2_INFRASTRUCTURE_VERIFIED", html)
        self.assertIn("METADATA_AUTHORIZATION_REQUIRED", html)
        self.assertNotRegex(html, re.compile(r"\b[0-9a-f]{64}\b"))

        lowered = html.lower()
        for forbidden in ("<script", "<form", "<button", "place order", "submit order"):
            self.assertNotIn(forbidden, lowered)

    def test_engineering_page_contains_scenarios_hashes_reasons_and_boundaries(self) -> None:
        html = render_engineering_evidence(self.manifest)
        self.assertIn("R2 工程证据", html)
        self.assertIn("AUTOMATED_EVIDENCE_PASS", html)
        self.assertIn("SYNTHETIC_ONLY", html)
        self.assertIn("NO_DECISION_EFFECT", html)
        self.assertIn("真实科学终局：未生成", html)
        self.assertIn("Owner receipt：未生成", html)
        self.assertRegex(html, re.compile(r"\b[0-9a-f]{64}\b"))
        for scenario in self.manifest.document["scenarios"]:
            self.assertIn(scenario["scenario_id"], html)
            self.assertIn(scenario["reason_code"], html)
            self.assertIn(scenario["input_path"], html)
            self.assertIn(scenario["result_path"], html)

        lowered = html.lower()
        self.assertNotIn("<script", lowered)
        self.assertNotIn("<form", lowered)
        self.assertNotIn("<button", lowered)

    def test_renderer_requires_a_validated_sealed_manifest(self) -> None:
        with self.assertRaises(R2QualificationError) as caught:
            render_owner_acceptance_index(self.manifest.document)  # type: ignore[arg-type]
        self.assertEqual(caught.exception.reason_code, "R2_ACCEPTANCE_MANIFEST_REQUIRED")

    def test_manifest_tamper_and_unsafe_href_are_rejected(self) -> None:
        tampered = deepcopy(self.manifest.document)
        tampered["candidate_flow"]["promotable_count"] = 16
        with self.assertRaises(R2QualificationError):
            validate_synthetic_acceptance_manifest(tampered)

        unsafe = deepcopy(self.manifest.document)
        unsafe["scenarios"][0]["input_path"] = "../../secret"
        unsigned = dict(unsafe)
        unsigned.pop("manifest_sha256")
        from gld_r2_theta_qualification.contracts import canonical_sha256

        unsafe["manifest_sha256"] = canonical_sha256(unsigned)
        with self.assertRaises(R2QualificationError) as caught:
            validate_synthetic_acceptance_manifest(unsafe)
        self.assertEqual(caught.exception.reason_code, "R2_ACCEPTANCE_PATH_INVALID")

        encoded = deepcopy(self.manifest.document)
        encoded["scenarios"][0]["input_path"] = "%2e%2e/secret"
        unsigned = dict(encoded)
        unsigned.pop("manifest_sha256")
        encoded["manifest_sha256"] = canonical_sha256(unsigned)
        with self.assertRaises(R2QualificationError) as caught:
            validate_synthetic_acceptance_manifest(encoded)
        self.assertEqual(caught.exception.reason_code, "R2_ACCEPTANCE_PATH_INVALID")

    def test_manifest_cannot_display_an_owner_terminal_reason(self) -> None:
        forged = deepcopy(self.manifest.document)
        forged["scenarios"][0][
            "reason_code"
        ] = "OWNER_APPROVED_R2_RESEARCH_THETA"
        unsigned = dict(forged)
        unsigned.pop("manifest_sha256")
        from gld_r2_theta_qualification.contracts import canonical_sha256

        forged["manifest_sha256"] = canonical_sha256(unsigned)
        with self.assertRaises(R2QualificationError) as caught:
            validate_synthetic_acceptance_manifest(forged)
        self.assertEqual(
            caught.exception.reason_code,
            "R2_ACCEPTANCE_SCENARIO_SEMANTICS_INVALID",
        )

    def test_manifest_cannot_forge_expected_and_actual_together(self) -> None:
        forged = deepcopy(self.manifest.document)
        fabricated = {
            "diagnostic_status": "PASS",
            "OWNER_APPROVED_R2_RESEARCH_THETA": True,
            "real_theta_generated": True,
        }
        forged["scenarios"][0]["expected"] = fabricated
        forged["scenarios"][0]["actual"] = deepcopy(fabricated)
        unsigned = dict(forged)
        unsigned.pop("manifest_sha256")
        from gld_r2_theta_qualification.contracts import canonical_sha256

        forged["manifest_sha256"] = canonical_sha256(unsigned)
        with self.assertRaises(R2QualificationError) as caught:
            validate_synthetic_acceptance_manifest(forged)
        self.assertEqual(
            caught.exception.reason_code,
            "R2_ACCEPTANCE_SCENARIO_SEMANTICS_INVALID",
        )


def validate_synthetic_acceptance_manifest_bytes(raw: bytes):
    import json

    document = json.loads(raw)
    return validate_synthetic_acceptance_manifest(document)


if __name__ == "__main__":
    unittest.main()
