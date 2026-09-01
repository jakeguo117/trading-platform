from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from gld_simulation.canonical import canonical_json_bytes
from gld_simulation.workflow import run_simulation_workflow
from tools.build_crr_native import build_native_kernel_v1


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "fixtures" / "gld_simulation" / "v1"


class GldRawSimulationPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.native_manifest = build_native_kernel_v1(repo_root=ROOT)
        manifest = json.loads(cls.native_manifest.read_bytes())
        cls.backend_evidence_sha256 = manifest["backend_evidence_sha256"]

    def run_scenario(self, scenario: str, output_dir: Path) -> object:
        return run_simulation_workflow(
            bundle_root=FIXTURE_ROOT / scenario,
            native_manifest_path=self.native_manifest,
            expected_backend_evidence_sha256=(
                self.backend_evidence_sha256
            ),
            output_dir=output_dir,
        )

    def mutate_pass_bundle(
        self,
        parent: Path,
        logical_key: str,
        mutate: object,
    ) -> Path:
        bundle_root = parent / "bundle"
        shutil.copytree(FIXTURE_ROOT / "pass", bundle_root)
        manifest_path = bundle_root / "manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        entry = next(
            item
            for item in manifest["files"]
            if item["logical_key"] == logical_key
        )
        document_path = bundle_root / entry["path"]
        document = json.loads(document_path.read_bytes())
        mutate(document)  # type: ignore[operator]
        document_bytes = canonical_json_bytes(document) + b"\n"
        document_path.write_bytes(document_bytes)
        entry["byte_size"] = len(document_bytes)
        entry["sha256"] = sha256(document_bytes).hexdigest()
        manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
        return bundle_root

    def run_mutated_pass(
        self,
        logical_key: str,
        mutate: object,
    ) -> dict[str, object]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        bundle = self.mutate_pass_bundle(root, logical_key, mutate)
        result = run_simulation_workflow(
            bundle_root=bundle,
            native_manifest_path=self.native_manifest,
            expected_backend_evidence_sha256=self.backend_evidence_sha256,
            output_dir=root / "out",
        )
        return result.decision_document

    def test_pass_derives_both_carriers_and_requires_owner_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_scenario("pass", Path(temporary) / "out")

            self.assertEqual(result.status, "SUCCESS")
            decision = result.decision_document
            self.assertEqual(
                decision["status"],
                "SIMULATED_OWNER_SELECTION_REQUIRED",
            )
            self.assertEqual(decision["classification"], "SIMULATION_ONLY")
            self.assertEqual(
                decision["schema_version"],
                "GLD_SIMULATION_DECISION_RESULT_V2",
            )
            self.assertEqual(
                decision["decision_scope"],
                "ENTRY_SIMULATION_ONLY",
            )
            self.assertEqual(
                decision["manual_control"],
                "SIMULATION_SELECTION_IN_CODEX_ONLY",
            )
            self.assertTrue(decision["owner_selection_required"])
            self.assertFalse(decision["actionable"])
            self.assertEqual(decision["broker_order_count"], 0)
            self.assertEqual(decision["gate_a"]["state"], "PASS")
            self.assertEqual(decision["crr"]["requested"], 64)
            self.assertEqual(decision["crr"]["terminal"], 64)
            self.assertEqual(decision["crr"]["failure_count"], 0)
            self.assertEqual(
                decision["crr"]["numerical_semantics_trust_boundary"],
                "SUPERVISED_CHILD_ATTESTED_PARENT_DID_NOT_RECOMPUTE_CRR_NUMERICS",
            )
            self.assertEqual(
                len(decision["crr"]["semantic_terminals"]),
                64,
            )
            self.assertEqual(
                [item["carrier_id"] for item in decision["candidates"]],
                ["LC0", "BCS0"],
            )
            lc0, bcs = decision["candidates"]
            self.assertEqual(
                lc0["profit_cap_mode"],
                "UNBOUNDED_BY_STRUCTURE",
            )
            self.assertEqual(
                bcs["profit_cap_mode"],
                "CAPPED_AT_SHORT_STRIKE",
            )
            self.assertEqual(
                lc0["profit_model_scope"],
                "NOT_APPLICABLE_UNBOUNDED_STRUCTURE",
            )
            self.assertEqual(
                bcs["profit_model_scope"],
                "THEORETICAL_EXPIRY_UNDER_SIZING_ASSUMPTIONS",
            )
            for candidate in (lc0, bcs):
                self.assertIn(
                    "net_delta_ppm_per_strategy_unit",
                    candidate,
                )
                self.assertIn(
                    "modeled_expiry_max_profit_nano_usd_per_strategy_unit",
                    candidate,
                )
                self.assertNotIn("net_delta_ppm", candidate)
                self.assertNotIn(
                    "theoretical_expiry_max_profit_nano_usd_per_contract",
                    candidate,
                )
                self.assertIn(
                    "modeled_expiry_max_profit_nano_usd_per_strategy_unit",
                    candidate["economics"],
                )
                self.assertNotIn(
                    "theoretical_expiry_max_profit_nano_usd_per_contract",
                    candidate["economics"],
                )
                self.assertNotIn("theoretical_expiry_only", candidate)
            self.assertGreater(lc0["quantity"], 0)
            self.assertGreater(bcs["quantity"], 0)
            self.assertEqual(len(lc0["legs"]), 1)
            self.assertEqual(len(bcs["legs"]), 2)
            self.assertEqual(
                lc0["legs"][0]["contract_id"],
                bcs["legs"][0]["contract_id"],
            )
            self.assertEqual(
                decision["risk_context"]["candidate_terminal_policy"],
                "BOTH_LC0_AND_BCS_MUST_PASS",
            )
            for candidate in (lc0, bcs):
                self.assertGreater(candidate["underlying_mid_nano_usd"], 0)
                self.assertIn(candidate["moneyness"], {"ITM", "ATM", "OTM"})
                self.assertEqual(candidate["h20_date"], "2026-09-28")
                self.assertEqual(
                    candidate["minimum_expiry_date"],
                    "2026-10-28",
                )
                self.assertGreater(candidate["long_coarse_delta_ppm"], 0)
                self.assertGreater(candidate["long_fine_delta_ppm"], 0)
                self.assertTrue(
                    candidate["economics"]["execution_assumptions"][
                        "include_exit_fees_in_max_loss"
                    ]
                )
                self.assertGreater(
                    candidate["economics"][
                        "planned_exit_fees_nano_usd_per_contract"
                    ],
                    0,
                )
            self.assertGreater(
                bcs["legs"][1]["strike_nano_usd"],
                bcs["legs"][0]["strike_nano_usd"],
            )
            self.assertEqual(
                result.supervisor_receipt.deadline_monotonic_ns
                - result.supervisor_receipt.supervisor_started_monotonic_ns,
                5_000_000_000,
            )
            self.assertTrue(result.decision_result_path.is_file())
            self.assertTrue(result.decision_receipt_path.is_file())
            self.assertTrue(result.decision_card_path.is_file())
            decision_bytes = result.decision_result_path.read_bytes()
            self.assertEqual(
                result.decision_result_sha256,
                sha256(decision_bytes).hexdigest(),
            )

    def test_success_artifact_crossing_hard_deadline_is_replaced_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "gld_simulation.workflow.time.monotonic_ns",
            return_value=10**30,
        ):
            result = self.run_scenario("pass", Path(temporary) / "out")

        self.assertEqual(result.status, "FAIL_CLOSED")
        self.assertEqual(
            result.decision_document["reason_code"],
            "SIM_SUPERVISOR_FINALIZATION_DEADLINE_EXCEEDED",
        )
        self.assertEqual(result.decision_document["candidates"], [])
        self.assertFalse(result.decision_document["actionable"])
        self.assertEqual(result.decision_document["broker_order_count"], 0)

    def test_complete_gate_failure_is_no_action_without_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_scenario("no_action", Path(temporary) / "out")

            decision = result.decision_document
            self.assertEqual(decision["status"], "SIMULATED_NO_ACTION")
            self.assertEqual(
                decision["manual_control"],
                "NO_ACTION_NO_ORDER_AUTHORITY",
            )
            self.assertEqual(
                decision["decision_scope"],
                "ENTRY_SIMULATION_ONLY",
            )
            self.assertEqual(decision["gate_a"]["state"], "FAIL")
            self.assertEqual(decision["candidates"], [])
            self.assertFalse(decision["owner_selection_required"])
            self.assertFalse(decision["actionable"])
            self.assertEqual(decision["broker_order_count"], 0)
            self.assertEqual(decision["crr"]["status"], "NOT_RUN_GATE_FAILED")

    def test_missing_risk_fact_is_no_decision_not_no_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_scenario("no_decision", Path(temporary) / "out")

            decision = result.decision_document
            self.assertEqual(decision["status"], "SIMULATED_NO_DECISION")
            self.assertEqual(
                decision["manual_control"],
                "NO_DECISION_NO_ORDER_AUTHORITY",
            )
            self.assertEqual(
                decision["decision_scope"],
                "ENTRY_SIMULATION_ONLY",
            )
            self.assertEqual(decision["gate_a"]["state"], "PASS")
            self.assertEqual(decision["candidates"], [])
            self.assertFalse(decision["owner_selection_required"])
            self.assertFalse(decision["actionable"])
            self.assertEqual(decision["broker_order_count"], 0)
            self.assertEqual(
                decision["crr"]["status"],
                "NOT_RUN_RISK_FACT_MISSING",
            )
            self.assertIn(
                "account_snapshot_a.settled_cash_nano_usd",
                decision["risk_context"]["missing_fields"],
            )

    def test_future_intraday_fact_fails_closed_before_crr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            bundle_root = temporary_root / "bundle"
            shutil.copytree(FIXTURE_ROOT / "pass", bundle_root)
            minute_path = bundle_root / "market" / "gld-minute-bars.json"
            minutes = json.loads(minute_path.read_bytes())
            cutoff = json.loads(
                (bundle_root / "market" / "market-status.json").read_bytes()
            )["as_of_utc_ns"]
            minutes["bars"][-1]["max_event_utc_ns"] = cutoff + 1
            minutes["bars"][-1]["max_receive_utc_ns"] = cutoff + 2
            minute_bytes = canonical_json_bytes(minutes) + b"\n"
            minute_path.write_bytes(minute_bytes)
            manifest_path = bundle_root / "manifest.json"
            manifest = json.loads(manifest_path.read_bytes())
            entry = next(
                item
                for item in manifest["files"]
                if item["logical_key"] == "minute_bars"
            )
            entry["byte_size"] = len(minute_bytes)
            entry["sha256"] = sha256(minute_bytes).hexdigest()
            manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")

            result = run_simulation_workflow(
                bundle_root=bundle_root,
                native_manifest_path=self.native_manifest,
                expected_backend_evidence_sha256=(
                    self.backend_evidence_sha256
                ),
                output_dir=temporary_root / "out",
            )

            decision = result.decision_document
            self.assertEqual(decision["status"], "SIMULATED_NO_DECISION")
            self.assertEqual(decision["gate_a"]["state"], "NOT_EVALUABLE")
            self.assertEqual(
                decision["gate_a"]["reason_code"],
                "SIM_MINUTE_CAUSALITY_INVALID",
            )
            self.assertEqual(
                decision["crr"]["status"],
                "NOT_RUN_PIPELINE_FAILED_CLOSED",
            )
            self.assertEqual(decision["candidates"], [])

    def test_daily_dates_must_be_exact_trailing_calendar_sessions(self) -> None:
        for replacement in ("2026-08-29", "2027-01-04"):
            with self.subTest(replacement=replacement):
                decision = self.run_mutated_pass(
                    "daily_bars",
                    lambda document, replacement=replacement: document["bars"][-1].update(
                        {"trading_date": replacement}
                    ),
                )
                self.assertEqual(decision["status"], "SIMULATED_NO_DECISION")
                self.assertNotEqual(decision["crr"]["status"], "EXACT64_PASS")

    def test_daily_and_minute_ohlcv_must_be_structurally_consistent(self) -> None:
        cases = (
            (
                "daily_bars",
                lambda document: document["bars"][-1].update(
                    {
                        "low_nano_usd": document["bars"][-1]["high_nano_usd"]
                        + 1,
                    }
                ),
            ),
            (
                "daily_bars",
                lambda document: document["bars"][-1].update({"volume": -1}),
            ),
            (
                "minute_bars",
                lambda document: document["bars"][-1].update(
                    {
                        "low_nano_usd": document["bars"][-1]["high_nano_usd"]
                        + 1,
                    }
                ),
            ),
            (
                "minute_bars",
                lambda document: document["bars"][-1].update({"volume": -1}),
            ),
        )
        for logical_key, mutation in cases:
            with self.subTest(logical_key=logical_key, mutation=mutation):
                decision = self.run_mutated_pass(logical_key, mutation)
                self.assertEqual(decision["status"], "SIMULATED_NO_DECISION")
                self.assertEqual(decision["candidates"], [])
                self.assertNotEqual(decision["crr"]["status"], "EXACT64_PASS")

    def test_minute_end_must_close_exactly_at_cutoff(self) -> None:
        decision = self.run_mutated_pass(
            "minute_bars",
            lambda document: document["bars"][-1].update(
                {
                    "minute_end_utc_ns": (
                        document["bars"][-1]["minute_end_utc_ns"]
                        + 3_600_000_000_000
                    )
                }
            ),
        )
        self.assertEqual(decision["status"], "SIMULATED_NO_DECISION")
        self.assertNotEqual(decision["crr"]["status"], "EXACT64_PASS")

    def test_incomplete_option_facts_fail_closed_before_crr(self) -> None:
        for field in ("option_universe_complete", "quote_snapshot_complete"):
            with self.subTest(field=field):
                decision = self.run_mutated_pass(
                    "market_status",
                    lambda document, field=field: document.update({field: False}),
                )
                self.assertEqual(decision["status"], "SIMULATED_NO_DECISION")
                self.assertEqual(decision["reason_code"], "OPTION_DATA_INCOMPLETE")
                self.assertEqual(
                    decision["crr"]["status"],
                    "NOT_RUN_OPTION_DATA_INCOMPLETE",
                )

    def test_offline_economic_extraction_lineage_is_recomputed(self) -> None:
        cases = (
            (
                "option_contracts",
                lambda document: document.update(
                    {"extracted_economic_values_sha256": "not-a-sha"}
                ),
            ),
            (
                "market_quotes",
                lambda document: document.update(
                    {"extracted_economic_values_sha256": {"bogus": True}}
                ),
            ),
            (
                "option_contracts",
                lambda document: document.update(
                    {"extracted_economic_values_sha256": "0" * 64}
                ),
            ),
            (
                "pit_inputs",
                lambda document: document.update(
                    {"risk_free_rate_ppm": document["risk_free_rate_ppm"] + 1}
                ),
            ),
            (
                "fee_schedule",
                lambda document: document.update(
                    {
                        "long_entry_fee_nano_usd_per_contract": (
                            document[
                                "long_entry_fee_nano_usd_per_contract"
                            ]
                            + 1
                        )
                    }
                ),
            ),
        )
        for logical_key, mutation in cases:
            with self.subTest(logical_key=logical_key, mutation=mutation):
                decision = self.run_mutated_pass(logical_key, mutation)
                self.assertEqual(decision["status"], "SIMULATED_NO_DECISION")
                self.assertEqual(
                    decision["reason_code"],
                    "SIM_ECONOMIC_EXTRACTION_BINDING_INVALID",
                )
                self.assertEqual(decision["candidates"], [])
                self.assertNotEqual(decision["crr"]["status"], "EXACT64_PASS")

    def test_future_or_unqualified_risk_facts_fail_closed_before_crr(self) -> None:
        cases = (
            (
                "account_snapshot_a",
                lambda document: document.update(
                    {"as_of_utc_ns": document["as_of_utc_ns"] + 86_400_000_000_000}
                ),
            ),
            (
                "frozen_kelly_receipt",
                lambda document: document.update(
                    {"frozen_at_utc_ns": document["frozen_at_utc_ns"] + 172_800_000_000_000}
                ),
            ),
            (
                "frozen_kelly_receipt",
                lambda document: document.update(
                    {"sample_end_trading_date": "2026-08-28"}
                ),
            ),
        )
        for logical_key, mutation in cases:
            with self.subTest(logical_key=logical_key, mutation=mutation):
                decision = self.run_mutated_pass(logical_key, mutation)
                self.assertEqual(decision["status"], "SIMULATED_NO_DECISION")
                self.assertNotEqual(decision["crr"]["status"], "EXACT64_PASS")

    def test_fractional_nanodollar_sma_is_evaluated_without_rounding_failure(
        self,
    ) -> None:
        decision = self.run_mutated_pass(
            "daily_bars",
            lambda document: document["bars"][0].update(
                {"close_nano_usd": document["bars"][0]["close_nano_usd"] + 1}
            ),
        )
        self.assertEqual(
            decision["status"],
            "SIMULATED_OWNER_SELECTION_REQUIRED",
        )

    def test_early_close_session_counts_toward_h20(self) -> None:
        def mutate_calendar(document: dict[str, object]) -> None:
            sessions = document["sessions"]
            current_index = next(
                index
                for index, item in enumerate(sessions)
                if item["trading_date"] == "2026-08-28"
            )
            target = sessions[current_index + 5]
            target["session_kind"] = "REGULAR_EARLY_CLOSE"
            target["regular_close_utc_ns"] -= 10_800_000_000_000

        decision = self.run_mutated_pass("calendar", mutate_calendar)
        self.assertEqual(
            decision["status"],
            "SIMULATED_OWNER_SELECTION_REQUIRED",
        )
        self.assertEqual(
            decision["candidates"][0]["h20_date"],
            "2026-09-28",
        )

    def test_persisted_sticky_drawdown_lock_blocks_recovered_account(self) -> None:
        decision = self.run_mutated_pass(
            "account_snapshot_a",
            lambda document: document.update(
                {"sticky_drawdown_lock_active": True}
            ),
        )
        self.assertEqual(decision["status"], "SIMULATED_NO_ACTION")
        self.assertEqual(
            decision["reason_code"],
            "RISK_STICKY_DRAWDOWN_LOCK_ACTIVE",
        )
        self.assertEqual(decision["candidates"], [])
        self.assertTrue(
            decision["risk_context"]["sticky_drawdown_lock_active"]
        )

    def test_nonempty_positions_or_open_orders_require_reconciliation(self) -> None:
        for field in ("positions", "open_orders"):
            with self.subTest(field=field):
                decision = self.run_mutated_pass(
                    "account_snapshot_a",
                    lambda document, field=field: document.update(
                        {field: [{"opaque_simulation_id": "UNRECONCILED"}]}
                    ),
                )
                self.assertEqual(decision["status"], "SIMULATED_NO_DECISION")
                self.assertEqual(
                    decision["reason_code"],
                    "SIM_ACCOUNT_RECONCILIATION_REQUIRED",
                )
                self.assertEqual(decision["candidates"], [])


if __name__ == "__main__":
    unittest.main()
