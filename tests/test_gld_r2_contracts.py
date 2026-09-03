from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import unittest

from gld_r2_theta_qualification.contracts import (
    CodeRuntimeBindingV1,
    R2QualificationError,
    build_r1_fixed_policy_projection,
    canonical_json_bytes,
    derive_development_method_freeze_sha256,
    derive_evaluated_candidate_sha256,
    derive_historical_theta_sha256,
    derive_owner_decision_receipt_sha256,
    derive_qualification_sha256,
    derive_research_freeze_sha256,
    validate_r1_fixed_policy_projection,
)
from gld_r2_theta_qualification.registry import (
    BCS0_ONLY,
    DEVELOPMENT_REJECTED,
    DUAL_PREFERENCE,
    DevelopmentCandidateDispositionV1,
    INSUFFICIENT_EVIDENCE,
    LC0_ONLY,
    NO_QUALIFIED_POLICY,
    build_r2_candidate_registry,
    classify_development_candidate_set,
    map_development_carrier_mode,
    validate_r2_candidate_registry,
)
from gld_r2_theta_qualification.windows import (
    COMMON_DATA_MISSING,
    QUALIFIED,
    assess_common_mask,
    assert_joint_rows_evaluable,
    build_r2_entry_label_windows,
)
from gld_entry_decision_f0 import entry_gate_policy_candidates_f0


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64


def r1_policy_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "ENTRY_POLICY_F0_V1",
        "policy_id": "GLD_ENTRY_RESEARCH_TRIAL_F0_STRICT",
        "policy_status": "RESEARCH_TRIAL",
        "owner_approved": True,
        "trend_required_count": 3,
        "breakout_required_count": 2,
        "lc0_delta_min_ppm": 450_000,
        "lc0_delta_target_ppm": 500_000,
        "lc0_delta_max_ppm": 550_000,
        "bcs0_long_delta_min_ppm": 450_000,
        "bcs0_long_delta_target_ppm": 500_000,
        "bcs0_long_delta_max_ppm": 550_000,
        "bcs0_short_delta_min_ppm": 200_000,
        "bcs0_short_delta_target_ppm": 250_000,
        "bcs0_short_delta_max_ppm": 300_000,
        "account_loss_budget_ppm": 100_000,
        "expiry_safety_calendar_days": 30,
        "management_policy_id": "GLD_MANAGEMENT_PRIMARY_F0",
        "management_policy_sha256": HASH_A,
        "invalidation_confirmation_sessions": 2,
    }
    document.update(overrides)
    return document


def r1_projection():
    return build_r1_fixed_policy_projection(r1_policy_document())


class R2CandidateRegistryTests(unittest.TestCase):
    def test_registry_has_exact_row_major_candidate_space(self) -> None:
        registry = build_r2_candidate_registry(r1_projection())

        self.assertEqual(len(registry.candidates), 16)
        self.assertEqual(
            [candidate.ordinal for candidate in registry.candidates],
            list(range(16)),
        )
        self.assertEqual(
            [candidate.candidate_id for candidate in registry.candidates],
            [f"R2C{ordinal:02d}" for ordinal in range(16)],
        )
        self.assertEqual(
            [
                (candidate.trend_required_count, candidate.breakout_required_count)
                for candidate in registry.candidates[::4]
            ],
            [(3, 2), (2, 2), (3, 1), (2, 1)],
        )
        self.assertEqual(
            [candidate.hard_stop_loss_ppm for candidate in registry.candidates[:4]],
            [333_333, 500_000, 666_667, None],
        )
        self.assertEqual(
            [candidate.ordinal for candidate in registry.promotable_candidates],
            [0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13, 14],
        )
        self.assertEqual(
            [candidate.ordinal for candidate in registry.control_candidates],
            [3, 7, 11, 15],
        )
        self.assertTrue(all(candidate.quantity_units == 1 for candidate in registry.candidates))
        self.assertTrue(
            all(
                candidate.hard_stop_mode == candidate.stop_mode
                for candidate in registry.candidates
            )
        )

    def test_gate_directory_has_exact_r1_parity(self) -> None:
        registry = build_r2_candidate_registry(r1_projection())
        registry_gate_directory = tuple(
            {
                "trend_required_count": candidate.trend_required_count,
                "breakout_required_count": candidate.breakout_required_count,
            }
            for candidate in registry.candidates[::4]
        )
        self.assertEqual(registry_gate_directory, entry_gate_policy_candidates_f0())

    def test_registry_round_trip_is_closed_and_canonical(self) -> None:
        registry = build_r2_candidate_registry(r1_projection())
        document = registry.as_dict()

        reparsed = validate_r2_candidate_registry(document)
        self.assertEqual(reparsed.registry_sha256, registry.registry_sha256)
        self.assertEqual(reparsed.canonical_bytes, registry.canonical_bytes)

        document_with_extra = dict(document)
        document_with_extra["unexpected"] = True
        with self.assertRaises(R2QualificationError) as caught:
            validate_r2_candidate_registry(document_with_extra)
        self.assertEqual(caught.exception.reason_code, "R2_CANDIDATE_REGISTRY_SCHEMA_INVALID")

    def test_candidate_records_are_immutable(self) -> None:
        candidate = build_r2_candidate_registry(r1_projection()).candidates[0]
        with self.assertRaises(FrozenInstanceError):
            candidate.ordinal = 99  # type: ignore[misc]

    def test_registry_rejects_bare_hash_in_place_of_projection(self) -> None:
        with self.assertRaises(R2QualificationError) as caught:
            build_r2_candidate_registry(HASH_A)  # type: ignore[arg-type]
        self.assertEqual(
            caught.exception.reason_code,
            "R1_FIXED_POLICY_PROJECTION_SCHEMA_INVALID",
        )

        forged_projection = replace(r1_projection(), projection_sha256=HASH_F)
        with self.assertRaises(R2QualificationError) as caught:
            build_r2_candidate_registry(forged_projection)
        self.assertEqual(
            caught.exception.reason_code,
            "R1_FIXED_POLICY_PROJECTION_INTEGRITY_MISMATCH",
        )


class R2CanonicalAndIdentityTests(unittest.TestCase):
    def test_r1_fixed_projection_excludes_candidate_and_account_dimensions(self) -> None:
        baseline = build_r1_fixed_policy_projection(r1_policy_document())
        excluded_changes = build_r1_fixed_policy_projection(
            r1_policy_document(
                trend_required_count=2,
                breakout_required_count=1,
                account_loss_budget_ppm=999_999,
            )
        )

        self.assertEqual(baseline.projection_sha256, excluded_changes.projection_sha256)
        projection_document = baseline.as_dict()
        self.assertNotIn("trend_required_count", projection_document)
        self.assertNotIn("breakout_required_count", projection_document)
        self.assertNotIn("hard_stop_loss_ppm", projection_document)
        self.assertNotIn("account_loss_budget_ppm", projection_document)
        self.assertEqual(
            projection_document["excluded_dimensions"],
            ["ACCOUNT_SIZING", "GATE", "HARD_STOP"],
        )

        selector_change = build_r1_fixed_policy_projection(
            r1_policy_document(lc0_delta_target_ppm=510_000)
        )
        self.assertNotEqual(baseline.projection_sha256, selector_change.projection_sha256)

    def test_r1_fixed_projection_is_closed_and_tamper_evident(self) -> None:
        projection = r1_projection()
        self.assertEqual(
            validate_r1_fixed_policy_projection(projection.as_dict()).projection_sha256,
            projection.projection_sha256,
        )
        extra = projection.as_dict()
        extra["account_loss_budget_ppm"] = 100_000
        with self.assertRaises(R2QualificationError) as caught:
            validate_r1_fixed_policy_projection(extra)
        self.assertEqual(
            caught.exception.reason_code,
            "R1_FIXED_POLICY_PROJECTION_SCHEMA_INVALID",
        )

    def test_canonical_wrapper_rejects_float_with_r2_error(self) -> None:
        with self.assertRaises(R2QualificationError) as caught:
            canonical_json_bytes({"not_integer_only": 1.25})
        self.assertEqual(caught.exception.reason_code, "CANONICAL_JSON_FLOAT_FORBIDDEN")

    def test_code_runtime_binding_is_closed_and_hash_validated(self) -> None:
        binding = CodeRuntimeBindingV1.from_document(
            {
                "schema_version": "R2_CODE_RUNTIME_BINDING_V1",
                "code_package_sha256": HASH_A,
                "runtime_id": "CPYTHON_3_11_LOCAL",
                "runtime_sha256": HASH_B,
            }
        )
        self.assertEqual(binding.code_package_sha256, HASH_A)
        self.assertEqual(binding.runtime_sha256, HASH_B)

        invalid = binding.as_dict()
        invalid["extra"] = "not allowed"
        with self.assertRaises(R2QualificationError) as caught:
            CodeRuntimeBindingV1.from_document(invalid)
        self.assertEqual(caught.exception.reason_code, "R2_CODE_RUNTIME_SCHEMA_INVALID")

    def test_identity_chain_keeps_owner_decision_out_of_qualification_hash(self) -> None:
        development = derive_development_method_freeze_sha256(
            registry_sha256=HASH_A,
            partition_manifest_sha256=HASH_B,
            fee_schedule_manifest_sha256=HASH_C,
            exit_stress_method_sha256=HASH_D,
            estimator_method_sha256=HASH_E,
            code_package_sha256=HASH_F,
            runtime_sha256=HASH_A,
        )
        evaluated = derive_evaluated_candidate_sha256(
            base_candidate_sha256=HASH_B,
            development_method_freeze_sha256=development,
            carrier_mode=DUAL_PREFERENCE,
            lc0_development_receipt_sha256=HASH_C,
            bcs0_development_receipt_sha256=HASH_D,
        )
        research = derive_research_freeze_sha256(
            development_method_freeze_sha256=development,
            evaluated_candidate_sha256s=(evaluated,),
            common_opportunity_manifest_sha256=HASH_E,
            joint_estimator_method_sha256=HASH_F,
            code_package_sha256=HASH_F,
            runtime_sha256=HASH_A,
        )
        historical = derive_historical_theta_sha256(
            research_freeze_sha256=research,
            evaluated_candidate_sha256=evaluated,
            wf_carrier_receipt_sha256s=(HASH_B, HASH_C),
            wf_policy_receipt_sha256=HASH_D,
        )
        qualification = derive_qualification_sha256(
            historical_theta_sha256=historical,
            forward_receipt_sha256=HASH_E,
            machine_terminal_status="QUALIFIED_POLICY_PENDING_OWNER",
        )

        approved = derive_owner_decision_receipt_sha256(
            qualification_sha256=qualification,
            owner_decision="APPROVE",
        )
        rejected = derive_owner_decision_receipt_sha256(
            qualification_sha256=qualification,
            owner_decision="REJECT",
        )

        self.assertNotEqual(approved, rejected)
        self.assertEqual(
            qualification,
            derive_qualification_sha256(
                historical_theta_sha256=historical,
                forward_receipt_sha256=HASH_E,
                machine_terminal_status="QUALIFIED_POLICY_PENDING_OWNER",
            ),
        )
        self.assertEqual(len({development, evaluated, research, historical, qualification}), 5)

        with self.assertRaises(R2QualificationError) as caught:
            derive_qualification_sha256(
                historical_theta_sha256=historical,
                forward_receipt_sha256=HASH_E,
                machine_terminal_status="DATA_NOT_QUALIFIED",
            )
        self.assertEqual(
            caught.exception.reason_code,
            "R2_MACHINE_TERMINAL_STATUS_INVALID",
        )


class R2EntryLabelWindowTests(unittest.TestCase):
    def test_six_entry_plus_label20_windows_are_half_open_and_exact(self) -> None:
        windows = build_r2_entry_label_windows(720)

        self.assertEqual(windows.base_entry_sessions, 100)
        self.assertEqual(windows.remainder_entry_sessions, 0)
        segments = (windows.development,) + windows.walk_forward
        self.assertEqual(len(segments), 6)
        self.assertEqual(
            [(segment.entry.start_index, segment.entry.end_index) for segment in segments],
            [(0, 100), (120, 220), (240, 340), (360, 460), (480, 580), (600, 700)],
        )
        self.assertTrue(all(segment.label.length == 20 for segment in segments))
        self.assertEqual(segments[-1].label.end_index, 720)
        self.assertFalse(segments[0].entry.contains(100))
        self.assertTrue(segments[0].label.contains(100))

    def test_remainder_is_assigned_only_to_wf5_entry(self) -> None:
        windows = build_r2_entry_label_windows(725)
        entry_lengths = [
            segment.entry.length
            for segment in (windows.development,) + windows.walk_forward
        ]
        self.assertEqual(entry_lengths, [100, 100, 100, 100, 100, 105])
        self.assertEqual(windows.walk_forward[-1].label.end_index, 725)

    def test_non_positive_base_fails_as_insufficient_evidence(self) -> None:
        with self.assertRaises(R2QualificationError) as caught:
            build_r2_entry_label_windows(125)
        self.assertEqual(caught.exception.reason_code, "INSUFFICIENT_EVIDENCE")


class R2CommonMaskTests(unittest.TestCase):
    def test_missing_rows_remain_in_denominator_and_fail_below_coverage(self) -> None:
        assessment = assess_common_mask(
            [QUALIFIED] * 94 + [COMMON_DATA_MISSING] * 6
        )
        self.assertEqual(assessment.total_entry_rows, 100)
        self.assertEqual(assessment.evaluable_entry_rows, 94)
        self.assertEqual(assessment.coverage_ppm, 940_000)
        self.assertEqual(assessment.status, INSUFFICIENT_EVIDENCE)
        self.assertEqual(assessment.reason_code, "COMMON_MASK_COVERAGE_INSUFFICIENT")

    def test_candidate_specific_unevaluable_row_blocks_joint_estimation(self) -> None:
        common = [QUALIFIED, COMMON_DATA_MISSING, QUALIFIED]
        rows = {
            "R2C00": ["NO_ACTION", "COMMON_DATA_MISSING", "ACTION"],
            "R2C01": ["NO_ACTION", "COMMON_DATA_MISSING", "UNEVALUABLE"],
        }
        with self.assertRaises(R2QualificationError) as caught:
            assert_joint_rows_evaluable(common, rows)
        self.assertEqual(
            caught.exception.reason_code,
            "CANDIDATE_SPECIFIC_UNEVALUABLE",
        )

    def test_joint_matrix_rejects_non_registry_candidate_ids(self) -> None:
        with self.assertRaises(R2QualificationError) as caught:
            assert_joint_rows_evaluable([QUALIFIED], {"not-r2": ["ACTION"]})
        self.assertEqual(caught.exception.reason_code, "CANDIDATE_ROW_MATRIX_INVALID")


class R2DevelopmentDispositionTests(unittest.TestCase):
    def test_carrier_mapping_is_exact(self) -> None:
        cases = {
            ("PASS", "PASS"): ("EVALUATED", DUAL_PREFERENCE, True),
            ("PASS", "FAIL"): ("EVALUATED", LC0_ONLY, True),
            ("FAIL", "PASS"): ("EVALUATED", BCS0_ONLY, True),
            ("FAIL", "FAIL"): (DEVELOPMENT_REJECTED, None, False),
        }
        for statuses, expected in cases.items():
            with self.subTest(statuses=statuses):
                disposition = map_development_carrier_mode(*statuses)
                self.assertEqual(
                    (disposition.status, disposition.carrier_mode, disposition.wf_allowed),
                    expected,
                )

    def test_any_known_unknown_or_insufficient_blocks_wf(self) -> None:
        for blocked_status in ("UNKNOWN", INSUFFICIENT_EVIDENCE):
            with self.subTest(blocked_status=blocked_status):
                disposition = map_development_carrier_mode("PASS", blocked_status)
                self.assertEqual(disposition.status, INSUFFICIENT_EVIDENCE)
                self.assertFalse(disposition.wf_allowed)
                self.assertIsNone(disposition.carrier_mode)

    def test_candidate_set_requires_all_twelve_promotable_classifications(self) -> None:
        registry = build_r2_candidate_registry(r1_projection())
        dispositions = {
            candidate.candidate_id: map_development_carrier_mode("PASS", "FAIL")
            for candidate in registry.promotable_candidates
        }
        first = registry.promotable_candidates[0].candidate_id
        dispositions[first] = map_development_carrier_mode("PASS", "UNKNOWN")

        blocked = classify_development_candidate_set(registry, dispositions)
        self.assertEqual(blocked.status, INSUFFICIENT_EVIDENCE)
        self.assertFalse(blocked.wf_allowed)
        self.assertEqual(blocked.s_dev_candidate_ids, ())

        all_rejected = {
            candidate.candidate_id: map_development_carrier_mode("FAIL", "FAIL")
            for candidate in registry.promotable_candidates
        }
        closed = classify_development_candidate_set(registry, all_rejected)
        self.assertEqual(closed.status, NO_QUALIFIED_POLICY)
        self.assertFalse(closed.wf_allowed)

    def test_registry_is_revalidated_before_development_classification(self) -> None:
        registry = build_r2_candidate_registry(r1_projection())
        dispositions = {
            candidate.candidate_id: map_development_carrier_mode("PASS", "FAIL")
            for candidate in registry.promotable_candidates
        }

        forged_hash = replace(registry, registry_sha256=HASH_F)
        with self.assertRaises(R2QualificationError) as caught:
            classify_development_candidate_set(forged_hash, dispositions)
        self.assertEqual(
            caught.exception.reason_code,
            "R2_CANDIDATE_REGISTRY_INTEGRITY_MISMATCH",
        )

        control = registry.control_candidates[0]
        forged_control = replace(control, promotable=True)
        forged_rows = tuple(
            forged_control if candidate.candidate_id == control.candidate_id else candidate
            for candidate in registry.candidates
        )
        with self.assertRaises(R2QualificationError) as caught:
            classify_development_candidate_set(
                replace(registry, candidates=forged_rows),
                dispositions,
            )
        self.assertEqual(
            caught.exception.reason_code,
            "R2_CANDIDATE_REGISTRY_ROW_INVALID",
        )

    def test_unknown_disposition_cannot_be_replaced_into_all_failed(self) -> None:
        unknown = map_development_carrier_mode("PASS", "UNKNOWN")

        with self.assertRaises(R2QualificationError) as caught:
            replace(
                unknown,
                status=DEVELOPMENT_REJECTED,
                carrier_mode=None,
                wf_allowed=False,
                reason_code="BOTH_CARRIERS_DEVELOPMENT_FAILED",
            )
        self.assertEqual(
            caught.exception.reason_code,
            "DEVELOPMENT_DISPOSITION_INTEGRITY_MISMATCH",
        )

    def test_evaluated_disposition_has_closed_mode_and_reason_invariant(self) -> None:
        evaluated = map_development_carrier_mode("PASS", "FAIL")

        for changes in (
            {"carrier_mode": BCS0_ONLY},
            {"wf_allowed": False},
            {"reason_code": "BCS0_ONLY_FROZEN"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(R2QualificationError) as caught:
                    replace(evaluated, **changes)
                self.assertEqual(
                    caught.exception.reason_code,
                    "DEVELOPMENT_DISPOSITION_INTEGRITY_MISMATCH",
                )

    def test_direct_fake_disposition_construction_is_rejected(self) -> None:
        with self.assertRaises(R2QualificationError) as caught:
            DevelopmentCandidateDispositionV1(
                status=DEVELOPMENT_REJECTED,
                carrier_mode=None,
                wf_allowed=False,
                reason_code="BOTH_CARRIERS_DEVELOPMENT_FAILED",
            )
        self.assertEqual(
            caught.exception.reason_code,
            "DEVELOPMENT_DISPOSITION_INTEGRITY_MISMATCH",
        )

    def test_control_candidate_can_never_enter_s_dev(self) -> None:
        registry = build_r2_candidate_registry(r1_projection())
        dispositions = {
            candidate.candidate_id: map_development_carrier_mode("PASS", "FAIL")
            for candidate in registry.promotable_candidates
        }
        control_id = registry.control_candidates[0].candidate_id
        dispositions[control_id] = map_development_carrier_mode("PASS", "PASS")

        with self.assertRaises(R2QualificationError) as caught:
            classify_development_candidate_set(registry, dispositions)
        self.assertEqual(
            caught.exception.reason_code,
            "DEVELOPMENT_DISPOSITION_SET_INVALID",
        )


if __name__ == "__main__":
    unittest.main()
