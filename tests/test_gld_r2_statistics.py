from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date
from hashlib import sha256
import unittest

import gld_r2_theta_qualification.forward as forward_module

from gld_r2_theta_qualification.contracts import (
    R2QualificationError,
    canonical_json_bytes,
    canonical_sha256,
)
from gld_r2_theta_qualification.forward import (
    SyntheticForwardSessionRow,
    build_synthetic_forward_diagnostic_receipt,
    record_synthetic_forward_session,
    record_synthetic_forward_terminal_episode,
    start_synthetic_exact_100_forward,
)
from gld_r2_theta_qualification.statistics import (
    PRODUCTION_BLOCK_LENGTH,
    PRODUCTION_BOOTSTRAP_REPLICATES,
    PRODUCTION_LOWER_BOUND_INDEX,
    evaluate_joint_policy_bootstrap,
    evaluate_joint_policy_sensitivities,
    evaluate_single_policy_bootstrap,
    run_carrier_episode_bootstrap,
)
from gld_management_research.xnys_calendar import (
    action_1045_utc_ns,
    next_session_date,
)


def _hash(label: str) -> str:
    return sha256(label.encode("ascii")).hexdigest()


def _session_dates(count: int) -> tuple[date, ...]:
    values = [date(2027, 1, 4)]
    while len(values) < count:
        values.append(next_session_date(values[-1]))
    return tuple(values)


_FORWARD_T0_UTC_NS = action_1045_utc_ns(date(2027, 1, 4)) - 1


class CarrierBootstrapTests(unittest.TestCase):
    def test_production_defaults_are_exact_and_use_zero_based_rank_499(self) -> None:
        result = run_carrier_episode_bootstrap(
            (25_000,) * 100,
            seed_sha256=_hash("freeze"),
        )

        self.assertEqual(result.replicates, PRODUCTION_BOOTSTRAP_REPLICATES)
        self.assertEqual(result.block_length, PRODUCTION_BLOCK_LENGTH)
        self.assertEqual(result.lower_bound_index, PRODUCTION_LOWER_BOUND_INDEX)
        self.assertEqual(len(result.bootstrap_means_ppm), 10_000)
        self.assertEqual(
            result.lower_bound_ppm,
            sorted(result.bootstrap_means_ppm)[499],
        )
        self.assertEqual(result.lower_bound_ppm, 25_000)
        self.assertGreater(result.half_kelly_ppm, 0)
        self.assertTrue(result.production_controls)
        self.assertTrue(result.statistical_pass)

    def test_test_sized_run_is_deterministic_and_fold_bound(self) -> None:
        returns = tuple((index - 12) * 10_000 for index in range(25))
        kwargs = {
            "seed_sha256": _hash("freeze"),
            "replicates": 12,
            "lower_bound_index": 1,
        }
        first = run_carrier_episode_bootstrap(
            returns,
            **kwargs,
        )
        repeated = run_carrier_episode_bootstrap(
            returns,
            **kwargs,
        )
        other_seed = run_carrier_episode_bootstrap(
            returns,
            **{**kwargs, "seed_sha256": _hash("other-freeze")},
        )
        self.assertEqual(first, repeated)
        self.assertNotEqual(first.seed_sha256, other_seed.seed_sha256)
        self.assertNotEqual(
            first.bootstrap_means_ppm,
            other_seed.bootstrap_means_ppm,
        )

    def test_more_than_r1_work_cap_is_supported_by_r2_adapter(self) -> None:
        result = run_carrier_episode_bootstrap(
            (25_000,) * 257,
            seed_sha256=_hash("freeze-257"),
        )
        self.assertEqual(result.episode_count, 257)
        self.assertTrue(result.production_controls)
        self.assertTrue(result.statistical_pass)


class JointBootstrapTests(unittest.TestCase):
    @staticmethod
    def _folds() -> dict[str, tuple[tuple[int, ...], ...]]:
        return {
            "WF1": ((10_000, 30_000), (20_000, 40_000), (30_000, 50_000)),
            "WF2": ((-10_000, 10_000), (40_000, 60_000)),
            "WF3": ((15_000, 35_000),),
            "WF4": ((5_000, 25_000), (25_000, 45_000), (45_000, 65_000)),
            "WF5": ((0, 20_000), (50_000, 70_000)),
        }

    def test_joint_bootstrap_uses_synchronized_rows_and_approved_error_direction(self) -> None:
        result = evaluate_joint_policy_bootstrap(
            self._folds(),
            candidate_ids=("R2C00", "R2C01"),
            research_freeze_sha256=_hash("joint-freeze"),
            replicates=12,
            critical_value_index=9,
        )

        self.assertEqual(len(result.bootstrap_means_ppm), 12)
        self.assertEqual(len(result.max_centered_errors_ppm), 12)
        expected_errors = []
        for replicate in result.bootstrap_means_ppm:
            errors = tuple(
                boot - observed
                for boot, observed in zip(replicate, result.observed_means_ppm)
            )
            expected_errors.append(max(errors))
            # The two columns differ by a constant, so synchronized sampling
            # must give them exactly the same centered bootstrap error.
            self.assertEqual(errors[0], errors[1])
        self.assertEqual(result.max_centered_errors_ppm, tuple(expected_errors))
        self.assertEqual(result.critical_value_ppm, sorted(expected_errors)[9])
        self.assertEqual(
            result.simultaneous_lower_bounds_ppm,
            tuple(
                observed - result.critical_value_ppm
                for observed in result.observed_means_ppm
            ),
        )
        self.assertFalse(result.qualification_eligible)

    def test_only_exact_production_controls_can_be_qualification_eligible(self) -> None:
        result = evaluate_joint_policy_bootstrap(
            self._folds(),
            candidate_ids=("R2C00", "R2C01"),
            research_freeze_sha256=_hash("joint-freeze"),
            replicates=12,
            critical_value_index=9,
        )
        self.assertFalse(result.qualification_eligible)
        self.assertEqual(result.candidate_passes, (False, False))

    def test_fold_weighting_uses_original_row_counts(self) -> None:
        result = evaluate_joint_policy_bootstrap(
            self._folds(),
            candidate_ids=("R2C00", "R2C01"),
            research_freeze_sha256=_hash("joint-freeze"),
            replicates=4,
            critical_value_index=3,
        )
        all_rows = [row for rows in self._folds().values() for row in rows]
        expected = tuple(
            round(sum(row[index] for row in all_rows) / len(all_rows))
            for index in range(2)
        )
        self.assertEqual(result.observed_means_ppm, expected)
        self.assertEqual(result.fold_row_counts, (3, 2, 1, 3, 2))

    def test_joint_matrix_digest_binds_exact_fold_row_and_column_order(self) -> None:
        folds = self._folds()
        result = evaluate_joint_policy_bootstrap(
            folds,
            candidate_ids=("R2C00", "R2C01"),
            research_freeze_sha256=_hash("joint-freeze"),
            replicates=4,
            critical_value_index=3,
        )
        expected_digest = canonical_sha256(
            {
                "schema_version": "GLD_R2_JOINT_FOLD_ROWS_V1",
                "folds": [
                    {
                        "fold_id": fold_id,
                        "rows_ppm": [list(row) for row in folds[fold_id]],
                    }
                    for fold_id in ("WF1", "WF2", "WF3", "WF4", "WF5")
                ],
            }
        )
        self.assertEqual(result.fold_rows_sha256, expected_digest)

        changed_row = dict(folds)
        changed_row["WF3"] = ((15_001, 35_000),)
        reordered_columns = {
            fold_id: tuple(tuple(reversed(row)) for row in rows)
            for fold_id, rows in folds.items()
        }
        for altered in (changed_row, reordered_columns):
            with self.subTest(altered=altered):
                altered_result = evaluate_joint_policy_bootstrap(
                    altered,
                    candidate_ids=("R2C00", "R2C01"),
                    research_freeze_sha256=_hash("joint-freeze"),
                    replicates=4,
                    critical_value_index=3,
                )
                self.assertNotEqual(
                    altered_result.fold_rows_sha256,
                    result.fold_rows_sha256,
                )

    def test_block_10_and_40_are_report_only(self) -> None:
        reports = evaluate_joint_policy_sensitivities(
            self._folds(),
            candidate_ids=("R2C00", "R2C01"),
            research_freeze_sha256=_hash("joint-freeze"),
            replicates=4,
            critical_value_index=3,
        )
        self.assertEqual(tuple(report.block_length for report in reports), (10, 40))
        self.assertTrue(all(not report.qualification_eligible for report in reports))

    def test_candidate_wise_unknown_is_rejected_not_dropped(self) -> None:
        folds = self._folds()
        folds["WF3"] = ((15_000, None),)  # type: ignore[list-item]
        with self.assertRaises(R2QualificationError) as failure:
            evaluate_joint_policy_bootstrap(
                folds,
                candidate_ids=("R2C00", "R2C01"),
                research_freeze_sha256=_hash("joint-freeze"),
                replicates=4,
                critical_value_index=3,
            )
        self.assertEqual(failure.exception.reason_code, "INSUFFICIENT_EVIDENCE")

    def test_long_policy_ledger_uses_block_draw_bound_not_row_times_replicates(self) -> None:
        result = evaluate_single_policy_bootstrap(
            (1_000,) * 2_001,
            seed_sha256=_hash("long-policy-ledger"),
        )
        self.assertEqual(result.row_count, 2_001)
        self.assertTrue(result.qualification_eligible)
        self.assertEqual(result.lower_bound_ppm, 1_000)


class SyntheticForwardExact100Tests(unittest.TestCase):
    def _collect(
        self,
        *,
        qualified_sessions: int = 100,
        unknown_episode_ordinal: int | None = None,
        synthetic_return_ppm: int = 20_000,
    ):
        state = start_synthetic_exact_100_forward(
            synthetic_theta_sha256=_hash("historical-theta"),
            t0_utc_ns=_FORWARD_T0_UTC_NS,
        )
        extra_unqualified = 100 - qualified_sessions
        dates = _session_dates(100 + extra_unqualified)
        for ordinal, session_date in enumerate(dates, start=1):
            session_id = f"XNYS-{session_date.isoformat()}"
            observation_ns = action_1045_utc_ns(session_date)
            state = record_synthetic_forward_session(
                state,
                session_id=session_id,
                session_date=session_date.isoformat(),
                session_ordinal=ordinal,
                observation_utc_ns=observation_ns,
                input_sha256=_hash(f"session-{ordinal}"),
                qualified=ordinal > extra_unqualified,
            )
        for episode_ordinal, session_date in enumerate(
            dates[extra_unqualified:],
            start=1,
        ):
            session_id = f"XNYS-{session_date.isoformat()}"
            observation_ns = action_1045_utc_ns(session_date)
            unknown = episode_ordinal == unknown_episode_ordinal
            state = record_synthetic_forward_terminal_episode(
                state,
                episode_id=f"EP-{episode_ordinal:03d}",
                entry_session_id=session_id,
                exit_session_id=session_id,
                exit_utc_ns=observation_ns,
                status="UNKNOWN" if unknown else "COMPLETE",
                synthetic_return_ppm=None if unknown else synthetic_return_ppm,
                input_sha256=_hash(f"episode-{episode_ordinal}"),
            )
        return state

    def test_hundredth_terminal_episode_seals_and_101st_is_rejected(self) -> None:
        state = self._collect()
        self.assertEqual(state.collection_status, "SEALED_SYNTHETIC_READY_FOR_DIAGNOSTIC")
        self.assertEqual(state.terminal_episode_count, 100)
        self.assertEqual(state.coverage_ppm, 1_000_000)
        self.assertIsNotNone(state.sealed_manifest_sha256)
        self.assertIsNotNone(state.sealed_manifest_bytes)
        with self.assertRaises(FrozenInstanceError):
            state.collection_status = "PENDING_SYNTHETIC_FORWARD_SEAL"  # type: ignore[misc]
        with self.assertRaises(R2QualificationError) as failure:
            record_synthetic_forward_terminal_episode(
                state,
                episode_id="EP-101",
                entry_session_id=state.sessions[-1].session_id,
                exit_session_id=state.sessions[-1].session_id,
                exit_utc_ns=state.sessions[-1].observation_utc_ns,
                status="COMPLETE",
                synthetic_return_ppm=20_000,
                input_sha256=_hash("episode-101"),
            )
        self.assertEqual(failure.exception.reason_code, "FORWARD_ALREADY_SEALED")

    def test_unknown_or_sub_95pct_coverage_seals_insufficient_without_extension(self) -> None:
        unknown = self._collect(unknown_episode_ordinal=17)
        low_coverage = self._collect(qualified_sessions=94)
        for state in (unknown, low_coverage):
            with self.subTest(reason=state.seal_reason_code):
                self.assertEqual(
                    state.collection_status,
                    "SEALED_SYNTHETIC_INSUFFICIENT_EVIDENCE",
                )
                self.assertEqual(state.terminal_episode_count, 100)
                receipt = build_synthetic_forward_diagnostic_receipt(
                    state,
                    synthetic_scenario_sha256=_hash("research-freeze"),
                    test_replicates=8,
                    test_lower_bound_index=0,
                )
                self.assertEqual(
                    receipt.diagnostic_status,
                    "SYNTHETIC_INSUFFICIENT_EVIDENCE",
                )

    def test_synthetic_forward_emits_only_a_diagnostic_receipt(self) -> None:
        state = self._collect(synthetic_return_ppm=20_000)
        diagnostic = build_synthetic_forward_diagnostic_receipt(
            state,
            synthetic_scenario_sha256=_hash("research-freeze"),
            test_replicates=8,
            test_lower_bound_index=0,
        )
        self.assertEqual(
            diagnostic.diagnostic_status,
            "SYNTHETIC_DIAGNOSTIC_ONLY",
        )
        self.assertGreater(diagnostic.synthetic_lower_bound_ppm, 0)
        document = diagnostic.as_dict()
        self.assertEqual(document["classification"], "SYNTHETIC_ONLY")
        self.assertNotIn("qualification_sha256", document)
        self.assertNotIn("owner_decision", document)
        for forbidden_name in (
            "ForwardQualificationReceipt",
            "build_forward_qualification_receipt",
            "build_owner_decision_receipt",
            "start_exact_100_forward",
        ):
            self.assertFalse(hasattr(forward_module, forbidden_name))

    def test_forward_container_rejects_real_classification(self) -> None:
        state = start_synthetic_exact_100_forward(
            synthetic_theta_sha256=_hash("synthetic-theta"),
            t0_utc_ns=_FORWARD_T0_UTC_NS,
        )
        forged = replace(state, classification="RESEARCH_ONLY")
        first = _session_dates(1)[0]
        with self.assertRaises(R2QualificationError) as failure:
            record_synthetic_forward_session(
                forged,
                session_id=f"XNYS-{first.isoformat()}",
                session_date=first.isoformat(),
                session_ordinal=1,
                observation_utc_ns=action_1045_utc_ns(first),
                input_sha256=_hash("first"),
                qualified=True,
            )
        self.assertEqual(
            failure.exception.reason_code,
            "FORWARD_SEAL_SEMANTICS_INVALID",
        )

    def test_session_t0_continuity_and_clock_fail_closed(self) -> None:
        first, second, third = _session_dates(3)
        cases = (
            (
                "wrong-first",
                second,
                1,
                action_1045_utc_ns(second),
                "FORWARD_FIRST_SESSION_AFTER_T0_INVALID",
            ),
            (
                "wrong-clock",
                first,
                1,
                action_1045_utc_ns(first) + 60_000_000_000,
                "FORWARD_SESSION_CLOCK_INVALID",
            ),
        )
        for label, session_date, ordinal, observation_ns, reason in cases:
            with self.subTest(case=label):
                state = start_synthetic_exact_100_forward(
                    synthetic_theta_sha256=_hash("historical-theta"),
                    t0_utc_ns=_FORWARD_T0_UTC_NS,
                )
                with self.assertRaises(R2QualificationError) as failure:
                    record_synthetic_forward_session(
                        state,
                        session_id=f"XNYS-{session_date.isoformat()}",
                        session_date=session_date.isoformat(),
                        session_ordinal=ordinal,
                        observation_utc_ns=observation_ns,
                        input_sha256=_hash(label),
                        qualified=True,
                    )
                self.assertEqual(failure.exception.reason_code, reason)

        state = start_synthetic_exact_100_forward(
            synthetic_theta_sha256=_hash("historical-theta"),
            t0_utc_ns=_FORWARD_T0_UTC_NS,
        )
        state = record_synthetic_forward_session(
            state,
            session_id=f"XNYS-{first.isoformat()}",
            session_date=first.isoformat(),
            session_ordinal=1,
            observation_utc_ns=action_1045_utc_ns(first),
            input_sha256=_hash("first"),
            qualified=True,
        )
        with self.assertRaises(R2QualificationError) as failure:
            record_synthetic_forward_session(
                state,
                session_id=f"XNYS-{third.isoformat()}",
                session_date=third.isoformat(),
                session_ordinal=2,
                observation_utc_ns=action_1045_utc_ns(third),
                input_sha256=_hash("gap"),
                qualified=True,
            )
        self.assertEqual(
            failure.exception.reason_code,
            "FORWARD_SESSION_DATE_NOT_CONTIGUOUS",
        )

    def test_q1_overlap_and_h21_fail_closed(self) -> None:
        dates = _session_dates(22)
        state = start_synthetic_exact_100_forward(
            synthetic_theta_sha256=_hash("historical-theta"),
            t0_utc_ns=_FORWARD_T0_UTC_NS,
        )
        for ordinal, session_date in enumerate(dates, start=1):
            state = record_synthetic_forward_session(
                state,
                session_id=f"XNYS-{session_date.isoformat()}",
                session_date=session_date.isoformat(),
                session_ordinal=ordinal,
                observation_utc_ns=action_1045_utc_ns(session_date),
                input_sha256=_hash(f"topology-{ordinal}"),
                qualified=True,
            )
        with self.assertRaises(R2QualificationError) as failure:
            record_synthetic_forward_terminal_episode(
                state,
                episode_id="EP-H21",
                entry_session_id=state.sessions[0].session_id,
                exit_session_id=state.sessions[21].session_id,
                exit_utc_ns=state.sessions[21].observation_utc_ns,
                status="COMPLETE",
                synthetic_return_ppm=1_000,
                input_sha256=_hash("h21"),
            )
        self.assertEqual(failure.exception.reason_code, "FORWARD_EPISODE_EXCEEDS_H20")

        state = record_synthetic_forward_terminal_episode(
            state,
            episode_id="EP-001",
            entry_session_id=state.sessions[0].session_id,
            exit_session_id=state.sessions[1].session_id,
            exit_utc_ns=state.sessions[1].observation_utc_ns,
            status="COMPLETE",
            synthetic_return_ppm=1_000,
            input_sha256=_hash("first-episode"),
        )
        with self.assertRaises(R2QualificationError) as failure:
            record_synthetic_forward_terminal_episode(
                state,
                episode_id="EP-002",
                entry_session_id=state.sessions[1].session_id,
                exit_session_id=state.sessions[2].session_id,
                exit_utc_ns=state.sessions[2].observation_utc_ns,
                status="COMPLETE",
                synthetic_return_ppm=1_000,
                input_sha256=_hash("overlap"),
            )
        self.assertEqual(failure.exception.reason_code, "FORWARD_EPISODE_OVERLAP_INVALID")

    def test_synthetic_diagnostic_receipt_rejects_hash_tamper(self) -> None:
        diagnostic = build_synthetic_forward_diagnostic_receipt(
            self._collect(synthetic_return_ppm=20_000),
            synthetic_scenario_sha256=_hash("research-freeze"),
            test_replicates=8,
            test_lower_bound_index=0,
        )
        diagnostic_before = diagnostic.as_dict()
        self.assertNotIn("owner", diagnostic_before)
        self.assertNotIn("qualification_sha256", diagnostic_before)
        tampered = replace(diagnostic, diagnostic_receipt_sha256=_hash("tampered"))
        with self.assertRaises(R2QualificationError) as failure:
            tampered.as_dict()
        self.assertEqual(
            failure.exception.reason_code,
            "SYNTHETIC_DIAGNOSTIC_RECEIPT_INTEGRITY_MISMATCH",
        )

    def test_self_consistent_hash_cannot_replace_exact_100_semantics(self) -> None:
        state = start_synthetic_exact_100_forward(
            synthetic_theta_sha256=_hash("historical-theta"),
            t0_utc_ns=_FORWARD_T0_UTC_NS,
        )
        rows = tuple(
            SyntheticForwardSessionRow(
                session_id=f"XNYS-{session_date.isoformat()}",
                session_date=session_date.isoformat(),
                session_ordinal=ordinal,
                observation_utc_ns=action_1045_utc_ns(session_date),
                input_sha256=_hash(f"forged-session-{ordinal}"),
                qualified=True,
                synthetic_return_ppm=20_000,
            )
            for ordinal, session_date in enumerate(_session_dates(100), start=1)
        )
        proto = replace(
            state,
            collection_status="SEALED_SYNTHETIC_READY_FOR_DIAGNOSTIC",
            sessions=rows,
            seal_reason_code="SYNTHETIC_FORWARD_EXACT_100_COMPLETE",
        )
        document = forward_module._manifest_document(
            proto,
            collection_status=proto.collection_status,
            reason_code=proto.seal_reason_code,
        )
        forged = replace(
            proto,
            sealed_manifest_sha256=canonical_sha256(document),
            sealed_manifest_bytes=canonical_json_bytes(document),
        )
        with self.assertRaises(R2QualificationError) as failure:
            build_synthetic_forward_diagnostic_receipt(
                forged,
                synthetic_scenario_sha256=_hash("research-freeze"),
            )
        self.assertEqual(
            failure.exception.reason_code,
            "FORWARD_SEAL_SEMANTICS_INVALID",
        )

    def test_unqualified_rows_are_not_estimator_rows(self) -> None:
        state = start_synthetic_exact_100_forward(
            synthetic_theta_sha256=_hash("historical-theta"),
            t0_utc_ns=_FORWARD_T0_UTC_NS,
        )
        dates = _session_dates(105)
        for ordinal, session_date in enumerate(dates, start=1):
            state = record_synthetic_forward_session(
                state,
                session_id=f"XNYS-{session_date.isoformat()}",
                session_date=session_date.isoformat(),
                session_ordinal=ordinal,
                observation_utc_ns=action_1045_utc_ns(session_date),
                input_sha256=_hash(f"session-{ordinal}"),
                qualified=ordinal > 5,
            )
        for ordinal, session_date in enumerate(dates[5:], start=1):
            session_id = f"XNYS-{session_date.isoformat()}"
            state = record_synthetic_forward_terminal_episode(
                state,
                episode_id=f"EP-{ordinal:03d}",
                entry_session_id=session_id,
                exit_session_id=session_id,
                exit_utc_ns=action_1045_utc_ns(session_date),
                status="COMPLETE",
                synthetic_return_ppm=-1_000,
                input_sha256=_hash(f"episode-{ordinal}"),
            )
        diagnostic = build_synthetic_forward_diagnostic_receipt(
            state,
            synthetic_scenario_sha256=_hash("research-freeze"),
            test_replicates=8,
            test_lower_bound_index=0,
        )
        self.assertEqual(diagnostic.synthetic_observed_mean_ppm, -1_000)

    def test_complete_episode_cannot_use_an_unqualified_row(self) -> None:
        state = start_synthetic_exact_100_forward(
            synthetic_theta_sha256=_hash("historical-theta"),
            t0_utc_ns=_FORWARD_T0_UTC_NS,
        )
        session_date = _session_dates(1)[0]
        session_id = f"XNYS-{session_date.isoformat()}"
        state = record_synthetic_forward_session(
            state,
            session_id=session_id,
            session_date=session_date.isoformat(),
            session_ordinal=1,
            observation_utc_ns=action_1045_utc_ns(session_date),
            input_sha256=_hash("session-1"),
            qualified=False,
        )
        with self.assertRaises(R2QualificationError) as failure:
            record_synthetic_forward_terminal_episode(
                state,
                episode_id="EP-001",
                entry_session_id=session_id,
                exit_session_id=session_id,
                exit_utc_ns=action_1045_utc_ns(session_date),
                status="COMPLETE",
                synthetic_return_ppm=10_000,
                input_sha256=_hash("episode-1"),
            )
        self.assertEqual(
            failure.exception.reason_code,
            "FORWARD_COMPLETE_REQUIRES_QUALIFIED_SESSIONS",
        )

    def test_nonpositive_synthetic_lcb_remains_diagnostic_only(self) -> None:
        state = self._collect(synthetic_return_ppm=-20_000)
        diagnostic = build_synthetic_forward_diagnostic_receipt(
            state,
            synthetic_scenario_sha256=_hash("research-freeze"),
            test_replicates=8,
            test_lower_bound_index=7,
        )
        self.assertEqual(
            diagnostic.diagnostic_status,
            "SYNTHETIC_DIAGNOSTIC_ONLY",
        )
        self.assertLessEqual(diagnostic.synthetic_lower_bound_ppm, 0)


if __name__ == "__main__":
    unittest.main()
