from __future__ import annotations

import copy
from dataclasses import replace
from hashlib import sha256
import pickle
import unittest

from gld_entry_decision_f0.evidence import circular_moving_block_bootstrap
from gld_r2_theta_qualification.contracts import (
    R2QualificationError,
    canonical_json_bytes,
    canonical_sha256,
)
from gld_r2_theta_qualification.statistics import (
    build_development_carrier_receipt,
    build_joint_policy_bootstrap_receipt,
    build_walk_forward_carrier_receipt,
    run_carrier_episode_bootstrap,
)


def _hash(label: str) -> str:
    return sha256(label.encode("ascii")).hexdigest()


class CarrierKernelParityTests(unittest.TestCase):
    def test_r2_kernel_matches_r1_supported_golden_exactly(self) -> None:
        returns = tuple((index - 12) * 10_000 for index in range(25))
        seed = _hash("n>20-golden")
        r1 = circular_moving_block_bootstrap(
            returns,
            seed_sha256=seed,
            replicates=8,
            block_length=20,
            lower_bound_rank=2,
            kelly_quantile_rank=2,
        )
        r2 = run_carrier_episode_bootstrap(
            returns,
            seed_sha256=seed,
            replicates=8,
            block_length=20,
            lower_bound_index=1,
        )
        self.assertEqual(r2.bootstrap_means_ppm, r1.means_ppm)
        self.assertEqual(r2.bootstrap_full_kelly_ppm, r1.full_kelly_ppm)
        self.assertEqual(r2.lower_bound_ppm, r1.lower_bound_ppm)
        self.assertEqual(r2.bootstrap_kelly_5pct_ppm, r1.kelly_5pct_ppm)
        self.assertEqual(
            r2.bootstrap_means_ppm,
            (-12_000, 6_000, 4_000, -22_000, -20_000, -10_000, 8_000, -4_000),
        )


class CarrierStageReceiptTests(unittest.TestCase):
    @staticmethod
    def _forge_receipt(receipt, document):
        unsigned = dict(document)
        unsigned.pop("receipt_sha256")
        receipt_sha256 = canonical_sha256(unsigned)
        return replace(
            receipt,
            receipt_sha256=receipt_sha256,
            _canonical_bytes=canonical_json_bytes(
                {**unsigned, "receipt_sha256": receipt_sha256}
            ),
        )

    def test_development_receipt_binds_only_development_identity_and_production_gates(self) -> None:
        receipt = build_development_carrier_receipt(
            returns_ppm=(25_000,) * 100,
            development_method_freeze_sha256=_hash("dev-freeze"),
            base_candidate_sha256=_hash("base-candidate"),
            carrier_id="LC0",
            coverage_qualified_count=100,
            coverage_eligible_count=105,
        )
        document = receipt.as_dict()
        self.assertEqual(document["stage"], "DEVELOPMENT")
        self.assertEqual(
            document["development_method_freeze_sha256"],
            _hash("dev-freeze"),
        )
        self.assertEqual(document["base_candidate_sha256"], _hash("base-candidate"))
        self.assertNotIn("research_freeze_sha256", document)
        self.assertNotIn("evaluated_candidate_sha256", document)
        self.assertEqual(document["episode_count"], 100)
        self.assertEqual(document["coverage_ppm"], 952_381)
        self.assertEqual(document["bootstrap_replicates"], 10_000)
        self.assertEqual(document["block_length"], 20)
        self.assertEqual(document["lower_bound_index"], 499)
        self.assertEqual(document["evidence_status"], "PASS")
        self.assertEqual(receipt.receipt_sha256, document["receipt_sha256"])
        with self.assertRaises(R2QualificationError) as failure:
            replace(receipt, receipt_sha256=_hash("tampered")).as_dict()
        self.assertEqual(
            failure.exception.reason_code,
            "R2_STATISTICS_RECEIPT_INTEGRITY_MISMATCH",
        )

        forged_document = dict(document)
        forged_document["classification"] = "RESEARCH_ONLY"
        forged_document["research_freeze_sha256"] = _hash("injected-freeze")
        with self.assertRaises(R2QualificationError) as semantic_failure:
            self._forge_receipt(receipt, forged_document).as_dict()
        self.assertEqual(
            semantic_failure.exception.reason_code,
            "R2_STATISTICS_RECEIPT_INTEGRITY_MISMATCH",
        )

    def test_receipt_seal_is_instance_bound_copy_safe_and_not_pickleable(self) -> None:
        receipt = build_development_carrier_receipt(
            returns_ppm=(25_000,) * 100,
            development_method_freeze_sha256=_hash("dev-freeze"),
            base_candidate_sha256=_hash("base-candidate"),
            carrier_id="LC0",
            coverage_qualified_count=100,
            coverage_eligible_count=105,
        )
        self.assertIs(copy.copy(receipt), receipt)
        self.assertIs(copy.deepcopy(receipt), receipt)
        with self.assertRaises(TypeError):
            pickle.dumps(receipt)
        assert receipt._seal is not None
        for field_name, forged_value in (
            ("_owner", object()),
            ("_canonical_bytes", b"{}"),
            ("_receipt_sha256", _hash("seal-snapshot-tamper")),
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaises(AttributeError):
                    setattr(receipt._seal, field_name, forged_value)
        with self.assertRaises(R2QualificationError):
            replace(receipt).as_dict()

        document = receipt.as_dict()
        unsigned = dict(document)
        unsigned.pop("receipt_sha256")
        unsigned["observed_mean_ppm"] = 25_001
        forged_hash = canonical_sha256(unsigned)
        forged = replace(
            receipt,
            receipt_sha256=forged_hash,
            _canonical_bytes=canonical_json_bytes(
                {**unsigned, "receipt_sha256": forged_hash}
            ),
        )
        object.__setattr__(forged, "_seal", receipt._seal)
        for access in (
            lambda: forged.document,
            lambda: forged.as_dict(),
            lambda: forged.canonical_bytes,
        ):
            with self.subTest(access=access):
                with self.assertRaises(R2QualificationError) as failure:
                    access()
                self.assertEqual(
                    failure.exception.reason_code,
                    "R2_STATISTICS_RECEIPT_INTEGRITY_MISMATCH",
                )

    def test_receipt_accessors_fully_revalidate_on_every_read(self) -> None:
        receipt = build_development_carrier_receipt(
            returns_ppm=(25_000,) * 100,
            development_method_freeze_sha256=_hash("dev-freeze"),
            base_candidate_sha256=_hash("base-candidate"),
            carrier_id="LC0",
            coverage_qualified_count=100,
            coverage_eligible_count=105,
        )
        self.assertEqual(receipt.document, receipt.as_dict())
        self.assertEqual(
            receipt.canonical_bytes,
            canonical_json_bytes(receipt.document),
        )
        document = receipt.as_dict()
        unsigned = dict(document)
        unsigned.pop("receipt_sha256")
        unsigned["observed_mean_ppm"] = 25_001
        forged_hash = canonical_sha256(unsigned)
        object.__setattr__(receipt, "receipt_sha256", forged_hash)
        object.__setattr__(
            receipt,
            "_canonical_bytes",
            canonical_json_bytes({**unsigned, "receipt_sha256": forged_hash}),
        )
        for access in (
            lambda: receipt.document,
            lambda: receipt.as_dict(),
            lambda: receipt.canonical_bytes,
        ):
            with self.subTest(access=access):
                with self.assertRaises(R2QualificationError) as failure:
                    access()
                self.assertEqual(
                    failure.exception.reason_code,
                    "R2_STATISTICS_RECEIPT_INTEGRITY_MISMATCH",
                )

    def test_development_production_sample_and_coverage_gates_are_not_overridable(self) -> None:
        cases = (
            ((25_000,) * 99, 100, 100),
            ((25_000,) * 100, 94, 100),
            ((25_000,) * 100, 1, 1),
            ((25_000,) * 100, 101, 106),
        )
        for returns, qualified, eligible in cases:
            with self.subTest(episodes=len(returns), qualified=qualified):
                with self.assertRaises(R2QualificationError) as failure:
                    build_development_carrier_receipt(
                        returns_ppm=returns,
                        development_method_freeze_sha256=_hash("dev-freeze"),
                        base_candidate_sha256=_hash("base-candidate"),
                        carrier_id="LC0",
                        coverage_qualified_count=qualified,
                        coverage_eligible_count=eligible,
                    )
                self.assertEqual(failure.exception.reason_code, "INSUFFICIENT_EVIDENCE")

    def test_walk_forward_receipt_binds_exact_five_nonempty_folds(self) -> None:
        folds = {
            "WF1": (10_000,) * 20,
            "WF2": (20_000,) * 20,
            "WF3": (30_000,) * 20,
            "WF4": (40_000,) * 20,
            "WF5": (50_000,) * 20,
        }
        receipt = build_walk_forward_carrier_receipt(
            fold_returns_ppm=folds,
            research_freeze_sha256=_hash("research-freeze"),
            evaluated_candidate_sha256=_hash("evaluated-candidate"),
            carrier_id="BCS0",
            coverage_qualified_count=100,
            coverage_eligible_count=105,
        )
        document = receipt.as_dict()
        self.assertEqual(document["stage"], "WALK_FORWARD_OOS")
        self.assertEqual(document["fold_ids"], ["WF1", "WF2", "WF3", "WF4", "WF5"])
        self.assertEqual(document["fold_episode_counts"], [20, 20, 20, 20, 20])
        self.assertEqual(document["fold_count"], 5)
        self.assertEqual(document["episode_count"], 100)
        self.assertEqual(document["coverage_ppm"], 952_381)
        self.assertEqual(
            document["evaluated_candidate_sha256"],
            _hash("evaluated-candidate"),
        )
        self.assertNotIn("development_method_freeze_sha256", document)

        for bad_folds in (
            {key: value for key, value in folds.items() if key != "WF5"},
            {**folds, "WF3": ()},
            {key: value[:19] for key, value in folds.items()},
        ):
            with self.subTest(keys=tuple(bad_folds)):
                with self.assertRaises(R2QualificationError):
                    build_walk_forward_carrier_receipt(
                        fold_returns_ppm=bad_folds,
                        research_freeze_sha256=_hash("research-freeze"),
                        evaluated_candidate_sha256=_hash("evaluated-candidate"),
                        carrier_id="BCS0",
                        coverage_qualified_count=100,
                        coverage_eligible_count=105,
                    )
        with self.assertRaises(R2QualificationError) as failure:
            build_walk_forward_carrier_receipt(
                fold_returns_ppm=folds,
                research_freeze_sha256=_hash("research-freeze"),
                evaluated_candidate_sha256=_hash("evaluated-candidate"),
                carrier_id="BCS0",
                coverage_qualified_count=189,
                coverage_eligible_count=200,
            )
        self.assertEqual(failure.exception.reason_code, "INSUFFICIENT_EVIDENCE")

        with self.assertRaises(R2QualificationError) as mismatch_failure:
            build_walk_forward_carrier_receipt(
                fold_returns_ppm=folds,
                research_freeze_sha256=_hash("research-freeze"),
                evaluated_candidate_sha256=_hash("evaluated-candidate"),
                carrier_id="BCS0",
                coverage_qualified_count=1,
                coverage_eligible_count=1,
            )
        self.assertEqual(
            mismatch_failure.exception.reason_code,
            "INSUFFICIENT_EVIDENCE",
        )


class JointStageReceiptTests(unittest.TestCase):
    @staticmethod
    def _fold_rows() -> dict[str, tuple[tuple[int, ...], ...]]:
        return {
            fold_id: ((10_000 + index * 1_000, 20_000 + index * 1_000),)
            for index, fold_id in enumerate(("WF1", "WF2", "WF3", "WF4", "WF5"))
        }

    def test_joint_receipt_binds_frozen_s_dev_order_seed_and_result(self) -> None:
        s_dev = (_hash("evaluated-0"), _hash("evaluated-1"))
        receipt = build_joint_policy_bootstrap_receipt(
            fold_rows_ppm=self._fold_rows(),
            frozen_s_dev_sha256s=s_dev,
            matrix_candidate_sha256s=s_dev,
            research_freeze_sha256=_hash("research-freeze"),
        )
        document = receipt.as_dict()
        self.assertEqual(
            document["s_dev_evaluated_candidate_sha256s"],
            list(s_dev),
        )
        self.assertEqual(document["fold_ids"], ["WF1", "WF2", "WF3", "WF4", "WF5"])
        self.assertEqual(document["candidate_count"], 2)
        self.assertEqual(document["bootstrap_replicates"], 10_000)
        self.assertEqual(document["block_length"], 20)
        self.assertEqual(document["critical_value_index"], 9_499)
        self.assertRegex(document["seed_contract_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(document["fold_rows_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(document["raw_result_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(receipt.receipt_sha256, document["receipt_sha256"])

    def test_joint_receipt_lineage_changes_for_one_row_or_column_reorder(self) -> None:
        s_dev = (_hash("evaluated-0"), _hash("evaluated-1"))
        original_rows = self._fold_rows()
        original = build_joint_policy_bootstrap_receipt(
            fold_rows_ppm=original_rows,
            frozen_s_dev_sha256s=s_dev,
            matrix_candidate_sha256s=s_dev,
            research_freeze_sha256=_hash("research-freeze"),
        )
        changed_row = dict(original_rows)
        changed_row["WF3"] = ((12_001, 22_000),)
        reordered_columns = {
            fold_id: tuple(tuple(reversed(row)) for row in rows)
            for fold_id, rows in original_rows.items()
        }

        for altered_rows in (changed_row, reordered_columns):
            with self.subTest(altered_rows=altered_rows):
                altered = build_joint_policy_bootstrap_receipt(
                    fold_rows_ppm=altered_rows,
                    frozen_s_dev_sha256s=s_dev,
                    matrix_candidate_sha256s=s_dev,
                    research_freeze_sha256=_hash("research-freeze"),
                )
                self.assertNotEqual(
                    altered.as_dict()["fold_rows_sha256"],
                    original.as_dict()["fold_rows_sha256"],
                )
                self.assertNotEqual(
                    altered.as_dict()["raw_result_sha256"],
                    original.as_dict()["raw_result_sha256"],
                )
                self.assertNotEqual(
                    altered.receipt_sha256,
                    original.receipt_sha256,
                )

    def test_joint_raw_result_binds_row_order_when_statistics_are_identical(self) -> None:
        s_dev = (_hash("evaluated-0"), _hash("evaluated-1"))
        rows = self._fold_rows()
        rows["WF1"] = ((10_000, 20_000), (30_000, 40_000))
        reordered_rows = dict(rows)
        reordered_rows["WF1"] = tuple(reversed(rows["WF1"]))

        original = build_joint_policy_bootstrap_receipt(
            fold_rows_ppm=rows,
            frozen_s_dev_sha256s=s_dev,
            matrix_candidate_sha256s=s_dev,
            research_freeze_sha256=_hash("research-freeze"),
        ).as_dict()
        reordered = build_joint_policy_bootstrap_receipt(
            fold_rows_ppm=reordered_rows,
            frozen_s_dev_sha256s=s_dev,
            matrix_candidate_sha256s=s_dev,
            research_freeze_sha256=_hash("research-freeze"),
        ).as_dict()

        for statistic_key in (
            "fold_row_counts",
            "observed_means_ppm",
            "critical_value_ppm",
            "simultaneous_lower_bounds_ppm",
            "candidate_passes",
        ):
            self.assertEqual(original[statistic_key], reordered[statistic_key])
        self.assertNotEqual(
            original["fold_rows_sha256"],
            reordered["fold_rows_sha256"],
        )
        self.assertNotEqual(
            original["raw_result_sha256"],
            reordered["raw_result_sha256"],
        )
        self.assertNotEqual(
            original["receipt_sha256"],
            reordered["receipt_sha256"],
        )

    def test_joint_receipt_rejects_column_shrink_or_reorder(self) -> None:
        s_dev = (_hash("evaluated-0"), _hash("evaluated-1"))
        for matrix_candidates in ((s_dev[0],), tuple(reversed(s_dev))):
            with self.subTest(matrix_candidates=matrix_candidates):
                with self.assertRaises(R2QualificationError) as failure:
                    build_joint_policy_bootstrap_receipt(
                        fold_rows_ppm=self._fold_rows(),
                        frozen_s_dev_sha256s=s_dev,
                        matrix_candidate_sha256s=matrix_candidates,
                        research_freeze_sha256=_hash("research-freeze"),
                    )
                self.assertEqual(
                    failure.exception.reason_code,
                    "R2_JOINT_COLUMN_IDENTITY_MISMATCH",
                )


if __name__ == "__main__":
    unittest.main()
