from __future__ import annotations

from dataclasses import replace
import unittest

from gld_r2_theta_qualification.contracts import (
    R2QualificationError,
    canonical_json_bytes,
    canonical_sha256,
    derive_qualification_sha256,
)
from gld_r2_theta_qualification.receipts import (
    build_generation_terminal_receipt,
    validate_generation_terminal_receipt,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64


class GenerationTerminalReceiptTests(unittest.TestCase):
    def test_metadata_terminal_needs_no_future_stage_placeholders(self) -> None:
        receipt = build_generation_terminal_receipt(
            generation_id="R2-GEN-001",
            stage="METADATA",
            terminal_status="DATA_NOT_QUALIFIED",
            reason_code="METADATA_SCHEMA_NOT_QUALIFIED",
            registry_sha256=HASH_A,
            code_package_sha256=HASH_B,
            runtime_sha256=HASH_C,
        )
        document = receipt.as_dict()

        self.assertNotIn("development_method_freeze_sha256", document)
        self.assertNotIn("research_freeze_sha256", document)
        self.assertNotIn("historical_theta_sha256", document)
        self.assertNotIn("forward_receipt_sha256", document)
        self.assertNotIn("qualification_sha256", document)
        self.assertEqual(
            validate_generation_terminal_receipt(document).receipt_sha256,
            receipt.receipt_sha256,
        )

    def test_development_all_rejected_needs_only_development_freeze(self) -> None:
        receipt = build_generation_terminal_receipt(
            generation_id="R2-GEN-002",
            stage="DEVELOPMENT",
            terminal_status="NO_QUALIFIED_POLICY",
            reason_code="ALL_DEVELOPMENT_REJECTED",
            registry_sha256=HASH_A,
            code_package_sha256=HASH_B,
            runtime_sha256=HASH_C,
            development_method_freeze_sha256=HASH_D,
        )
        document = receipt.as_dict()

        self.assertEqual(document["development_method_freeze_sha256"], HASH_D)
        self.assertNotIn("historical_theta_sha256", document)
        self.assertNotIn("forward_receipt_sha256", document)
        self.assertNotIn("qualification_sha256", document)

    def test_stage_required_hashes_fail_closed_without_fake_values(self) -> None:
        with self.assertRaises(R2QualificationError) as caught:
            build_generation_terminal_receipt(
                generation_id="R2-GEN-003",
                stage="DEVELOPMENT",
                terminal_status="NO_QUALIFIED_POLICY",
                reason_code="ALL_DEVELOPMENT_REJECTED",
                registry_sha256=HASH_A,
                code_package_sha256=HASH_B,
                runtime_sha256=HASH_C,
            )
        self.assertEqual(
            caught.exception.reason_code,
            "R2_TERMINAL_STAGE_BINDING_INVALID",
        )

    def test_forward_terminal_binds_exact_forward_machine_result(self) -> None:
        qualification = derive_qualification_sha256(
            historical_theta_sha256=HASH_F,
            forward_receipt_sha256=HASH_A,
            machine_terminal_status="NO_QUALIFIED_POLICY",
        )
        receipt = build_generation_terminal_receipt(
            generation_id="R2-GEN-004",
            stage="FORWARD",
            terminal_status="NO_QUALIFIED_POLICY",
            reason_code="FORWARD_LCB_NONPOSITIVE",
            registry_sha256=HASH_A,
            code_package_sha256=HASH_B,
            runtime_sha256=HASH_C,
            development_method_freeze_sha256=HASH_D,
            research_freeze_sha256=HASH_E,
            historical_theta_sha256=HASH_F,
            forward_receipt_sha256=HASH_A,
        )
        document = receipt.as_dict()
        self.assertEqual(document["historical_theta_sha256"], HASH_F)
        self.assertEqual(document["forward_receipt_sha256"], HASH_A)
        self.assertEqual(document["qualification_sha256"], qualification)

        tampered = dict(document)
        tampered["runtime_sha256"] = HASH_D
        with self.assertRaises(R2QualificationError) as caught:
            validate_generation_terminal_receipt(tampered)
        self.assertEqual(
            caught.exception.reason_code,
            "R2_TERMINAL_RECEIPT_HASH_MISMATCH",
        )

    def test_receipt_is_closed_and_cannot_claim_owner_authority(self) -> None:
        with self.assertRaises(R2QualificationError) as caught:
            build_generation_terminal_receipt(
                generation_id="R2-GEN-005",
                stage="FORWARD",
                terminal_status="OWNER_APPROVED_R2_RESEARCH_THETA",
                reason_code="OWNER_APPROVED",
                registry_sha256=HASH_A,
                code_package_sha256=HASH_B,
                runtime_sha256=HASH_C,
                development_method_freeze_sha256=HASH_D,
                research_freeze_sha256=HASH_E,
                historical_theta_sha256=HASH_F,
                forward_receipt_sha256=HASH_A,
            )
        self.assertEqual(
            caught.exception.reason_code,
            "R2_TERMINAL_STATUS_STAGE_INVALID",
        )

        receipt = build_generation_terminal_receipt(
            generation_id="R2-GEN-006",
            stage="METADATA",
            terminal_status="INSUFFICIENT_EVIDENCE",
            reason_code="METADATA_COVERAGE_INSUFFICIENT",
            registry_sha256=HASH_A,
            code_package_sha256=HASH_B,
            runtime_sha256=HASH_C,
        )
        extra = receipt.as_dict()
        extra["owner_decision"] = "APPROVE"
        with self.assertRaises(R2QualificationError) as caught:
            validate_generation_terminal_receipt(extra)
        self.assertEqual(
            caught.exception.reason_code,
            "R2_TERMINAL_RECEIPT_SCHEMA_INVALID",
        )

    def test_reason_is_closed_over_stage_and_terminal(self) -> None:
        invalid_combinations = (
            ("METADATA", "DATA_NOT_QUALIFIED", "ALL_DEVELOPMENT_REJECTED"),
            (
                "DEVELOPMENT",
                "NO_QUALIFIED_POLICY",
                "DEVELOPMENT_EVIDENCE_INSUFFICIENT",
            ),
            (
                "HISTORICAL_WF",
                "INSUFFICIENT_EVIDENCE",
                "FORWARD_EVIDENCE_INSUFFICIENT",
            ),
        )
        for stage, status, reason in invalid_combinations:
            with self.subTest(stage=stage, status=status, reason=reason):
                kwargs: dict[str, object] = {}
                if stage != "METADATA":
                    kwargs["development_method_freeze_sha256"] = HASH_D
                if stage == "HISTORICAL_WF":
                    kwargs["research_freeze_sha256"] = HASH_E
                with self.assertRaises(R2QualificationError) as caught:
                    build_generation_terminal_receipt(
                        generation_id="R2-GEN-SEMANTICS",
                        stage=stage,
                        terminal_status=status,
                        reason_code=reason,
                        registry_sha256=HASH_A,
                        code_package_sha256=HASH_B,
                        runtime_sha256=HASH_C,
                        **kwargs,
                    )
                self.assertEqual(
                    caught.exception.reason_code,
                    "R2_TERMINAL_REASON_STAGE_INVALID",
                )

    def test_machine_reason_cannot_claim_owner_or_approval_authority(self) -> None:
        for reason in (
            "OWNER_REJECTED",
            "OWNER_APPROVED",
            "APPROVAL_GRANTED",
            "AUTHORIZATION_GRANTED",
        ):
            with self.subTest(reason=reason):
                with self.assertRaises(R2QualificationError) as caught:
                    build_generation_terminal_receipt(
                        generation_id="R2-GEN-AUTHORITY",
                        stage="METADATA",
                        terminal_status="DATA_NOT_QUALIFIED",
                        reason_code=reason,
                        registry_sha256=HASH_A,
                        code_package_sha256=HASH_B,
                        runtime_sha256=HASH_C,
                    )
                self.assertEqual(
                    caught.exception.reason_code,
                    "R2_TERMINAL_REASON_AUTHORITY_INVALID",
                )

    def test_forward_supplied_qualification_must_match_derived_lineage(self) -> None:
        with self.assertRaises(R2QualificationError) as caught:
            build_generation_terminal_receipt(
                generation_id="R2-GEN-QUALIFICATION-MISMATCH",
                stage="FORWARD",
                terminal_status="NO_QUALIFIED_POLICY",
                reason_code="FORWARD_LCB_NONPOSITIVE",
                registry_sha256=HASH_A,
                code_package_sha256=HASH_B,
                runtime_sha256=HASH_C,
                development_method_freeze_sha256=HASH_D,
                research_freeze_sha256=HASH_E,
                historical_theta_sha256=HASH_F,
                forward_receipt_sha256=HASH_A,
                qualification_sha256=HASH_B,
            )
        self.assertEqual(
            caught.exception.reason_code,
            "R2_TERMINAL_QUALIFICATION_BINDING_INVALID",
        )

    def test_rehashed_document_cannot_substitute_forward_qualification(self) -> None:
        receipt = build_generation_terminal_receipt(
            generation_id="R2-GEN-REHASHED-QUALIFICATION",
            stage="FORWARD",
            terminal_status="NO_QUALIFIED_POLICY",
            reason_code="FORWARD_LCB_NONPOSITIVE",
            registry_sha256=HASH_A,
            code_package_sha256=HASH_B,
            runtime_sha256=HASH_C,
            development_method_freeze_sha256=HASH_D,
            research_freeze_sha256=HASH_E,
            historical_theta_sha256=HASH_F,
            forward_receipt_sha256=HASH_A,
        )
        forged = receipt.as_dict()
        forged["qualification_sha256"] = HASH_B
        forged["receipt_sha256"] = canonical_sha256(
            {key: value for key, value in forged.items() if key != "receipt_sha256"}
        )

        with self.assertRaises(R2QualificationError) as caught:
            validate_generation_terminal_receipt(forged)
        self.assertEqual(
            caught.exception.reason_code,
            "R2_TERMINAL_QUALIFICATION_BINDING_INVALID",
        )

    def test_dataclass_copy_cannot_bypass_semantics_or_stage_lineage(self) -> None:
        metadata = build_generation_terminal_receipt(
            generation_id="R2-GEN-DATACLASS",
            stage="METADATA",
            terminal_status="DATA_NOT_QUALIFIED",
            reason_code="METADATA_SCHEMA_NOT_QUALIFIED",
            registry_sha256=HASH_A,
            code_package_sha256=HASH_B,
            runtime_sha256=HASH_C,
        )

        with self.subTest("future hash on early terminal"):
            forged_future = replace(metadata, historical_theta_sha256=HASH_D)
            with self.assertRaises(R2QualificationError) as caught:
                forged_future.as_dict()
            self.assertEqual(
                caught.exception.reason_code,
                "R2_TERMINAL_STAGE_BINDING_INVALID",
            )

        with self.subTest("fully rehashed authority wording"):
            forged_document = metadata.as_dict()
            forged_document["reason_code"] = "OWNER_APPROVED"
            forged_document["receipt_sha256"] = canonical_sha256(
                {
                    key: value
                    for key, value in forged_document.items()
                    if key != "receipt_sha256"
                }
            )
            forged_authority = replace(
                metadata,
                reason_code="OWNER_APPROVED",
                receipt_sha256=forged_document["receipt_sha256"],
                _canonical_bytes=canonical_json_bytes(forged_document),
            )
            with self.assertRaises(R2QualificationError) as caught:
                forged_authority.as_dict()
            self.assertEqual(
                caught.exception.reason_code,
                "R2_TERMINAL_REASON_AUTHORITY_INVALID",
            )


if __name__ == "__main__":
    unittest.main()
