from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import unittest

from gld_entry_decision_f0 import (
    EntryDecisionF0Error,
    canonical_json_bytes,
    canonical_json_sha256,
    entry_gate_policy_candidates_f0,
    evaluate_entry_decision_f0,
    validate_entry_decision_input_f0,
    validate_entry_policy_f0,
)
from gld_entry_decision_f0.decision import _evaluate_exit_trigger_fixture_f0


HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64
HEX_E = "e" * 64
ENTRY_CUTOFF_NS = 1_805_381_100_000_000_000
QUOTE_EVENT_NS = ENTRY_CUTOFF_NS - 1_000_000_000
QUOTE_RECEIVE_NS = ENTRY_CUTOFF_NS - 900_000_000
FACTS_MAX_EVENT_NS = ENTRY_CUTOFF_NS - 500_000_000


def policy_document(**overrides: object) -> dict[str, object]:
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
        "management_policy_sha256": HEX_A,
        "invalidation_confirmation_sessions": 2,
    }
    document.update(overrides)
    return document


def fee_schedule_sha256(carrier_id: str) -> str:
    return sha256(f"{carrier_id}-fee-schedule-f0".encode("ascii")).hexdigest()


def exit_policy_document(carrier_id: str) -> dict[str, object]:
    policy_id = f"{carrier_id}_HARD_STOP_500K_F0"
    hard_stop_loss_ppm = 500_000
    return {
        "policy_id": policy_id,
        "hard_stop_loss_ppm": hard_stop_loss_ppm,
        "policy_sha256": canonical_json_sha256(
            {
                "policy_id": policy_id,
                "hard_stop_loss_ppm": hard_stop_loss_ppm,
            }
        ),
        "fee_schedule_sha256": fee_schedule_sha256(carrier_id),
    }


def evidence_document(
    carrier_id: str,
    *,
    lower_bound_ppm: int = 120_000,
    half_kelly_ppm: int = 100_000,
) -> dict[str, object]:
    return {
        "schema_version": "HISTORICAL_STRUCTURE_EVIDENCE_V2",
        "classification": "SYNTHETIC_ONLY",
        "carrier_id": carrier_id,
        "evidence_receipt_id": f"{carrier_id}_EVIDENCE_RECEIPT_F0",
        "episode_cohort_id": f"{carrier_id}_OOS_COHORT",
        "oos_episode_count": 120,
        "fold_count": 3,
        "coverage_ppm": 970_000,
        "entry_policy_sha256": canonical_json_sha256(policy_document()),
        "fee_schedule_sha256": fee_schedule_sha256(carrier_id),
        "exit_policy_sha256": exit_policy_document(carrier_id)[
            "policy_sha256"
        ],
        "evidence_max_estimation_outcome_utc_ns": (
            ENTRY_CUTOFF_NS - 86_400_000_000_000
        ),
        "distribution_id": f"{carrier_id}_DISTRIBUTION_F0",
        "expected_net_return_on_entry_debit_ppm": lower_bound_ppm,
        "arithmetic_mean_ppm": lower_bound_ppm + 50_000,
        "full_kelly_ppm": 240_000,
        "bootstrap_kelly_5pct_ppm": 200_000,
        "robust_full_kelly_ppm": 200_000,
        "half_kelly_ppm": half_kelly_ppm,
        "input_sha256": HEX_B if carrier_id == "LC0" else HEX_C,
        "distribution_sha256": HEX_C if carrier_id == "LC0" else HEX_D,
        "evidence_sha256": HEX_D if carrier_id == "LC0" else HEX_E,
    }


def call(
    contract_id: str,
    strike: int,
    delta: int,
    *,
    expiry: str = "2027-06-18",
    last_trading: str = "2027-06-18",
    bid: int = 9_000_000_000,
    ask: int = 10_000_000_000,
    bid_size: int = 8,
    ask_size: int = 8,
) -> dict[str, object]:
    return {
        "contract_id": contract_id,
        "expiry_date": expiry,
        "last_trading_date": last_trading,
        "strike_nano_usd": strike,
        "multiplier": 100,
        "coarse_delta_ppm": delta,
        "fine_delta_ppm": delta,
        "bid_nano_usd": bid,
        "ask_nano_usd": ask,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "tick_nano_usd": 10_000_000,
        "quote_event_utc_ns": QUOTE_EVENT_NS,
        "quote_receive_utc_ns": QUOTE_RECEIVE_NS,
        "executable": True,
        "snapshot_sha256": HEX_C,
        "technical_facts_sha256": HEX_B,
        "executability_source": "REQUIRED_TECHNICAL_FACTS_V1",
    }


def input_document() -> dict[str, object]:
    return {
        "schema_version": "ENTRY_DECISION_INPUT_F0_V1",
        "classification": "SYNTHETIC_ONLY",
        "instrument_id": "GLD",
        "data_qualification_status": "STRUCTURALLY_VALID_SYNTHETIC",
        "entry_fact_bundle_sha256": HEX_A,
        "technical_facts_sha256": HEX_B,
        "atomic_snapshot_sha256": HEX_C,
        "universe_complete": True,
        "decision_cutoff_utc_ns": ENTRY_CUTOFF_NS,
        "facts_max_event_utc_ns": FACTS_MAX_EVENT_NS,
        "open_gld_order_count": 0,
        "entry_session_date": "2027-03-18",
        "h20_date": "2027-04-16",
        "h20_exit_utc_ns": 1_807_886_700_000_000_000,
        "entry_gate_facts": {
            "prior_close_nano_usd": 200_000_000_000,
            "sma50_nano_usd": 195_000_000_000,
            "sma200_nano_usd": 190_000_000_000,
            "sma50_slope_nano_usd_per_session": 100_000_000,
            "prior_high20_nano_usd": 201_000_000_000,
            "minute_1044_close_nano_usd": 202_000_000_000,
            "confirmation_closes_nano_usd": [
                202_000_000_000 for _ in range(15)
            ],
        },
        "account_facts": {
            "eligible_bankroll_nano_usd": 100_000_000_000_000,
            "current_nlv_nano_usd": 100_000_000_000_000,
            "peak_nlv_nano_usd": 100_000_000_000_000,
            "settled_cash_nano_usd": 100_000_000_000_000,
            "cash_reserve_nano_usd": 100_000_000_000,
            "current_signed_gld_delta_exposure_nano_usd": 0,
            "delta_limit_nano_usd": 2_000_000_000_000,
            "external_capacity_units": 20,
        },
        "call_universe": [
            call("LC-LONG", 190_000_000_000, 500_000),
            call("BCS-LONG", 191_000_000_000, 490_000),
            call(
                "BCS-SHORT",
                210_000_000_000,
                250_000,
                bid=4_000_000_000,
                ask=5_000_000_000,
            ),
        ],
        "lc0_economics": [
            {
                "contract_id": "LC-LONG",
                "stressed_entry_debit_nano_usd": 1_000_000_000_000,
                "stressed_max_loss_basis_nano_usd": 1_000_000_000_000,
                "delta_notional_nano_usd": 10_000_000_000,
            },
            {
                "contract_id": "BCS-LONG",
                "stressed_entry_debit_nano_usd": 1_000_000_000_000,
                "stressed_max_loss_basis_nano_usd": 1_000_000_000_000,
                "delta_notional_nano_usd": 10_000_000_000,
            },
        ],
        "bcs0_economics": [
            {
                "long_contract_id": "BCS-LONG",
                "short_contract_id": "BCS-SHORT",
                "stressed_entry_debit_nano_usd": 600_000_000_000,
                "stressed_max_loss_basis_nano_usd": 600_000_000_000,
                "delta_notional_nano_usd": 5_000_000_000,
            }
        ],
        "structure_evidence": {
            "LC0": evidence_document("LC0", lower_bound_ppm=100_000),
            "BCS0": evidence_document("BCS0", lower_bound_ppm=200_000),
        },
        "exit_policies": {
            "LC0": exit_policy_document("LC0"),
            "BCS0": exit_policy_document("BCS0"),
        },
        "planned_exit_fees_nano_usd": {"LC0": 1_000_000_000, "BCS0": 2_000_000_000},
    }


def exit_observation(carrier_id: str = "LC0") -> dict[str, object]:
    source: dict[str, object] = {
        "schema_version": "ENTRY_EXIT_TRIGGER_OBSERVATION_F0_V1",
        "carrier_id": carrier_id,
        "already_latched": False,
        "observation_utc_ns": 2_000_000_005_000_000_000,
        "max_quote_age_ns": 5_000_000_000,
        "max_cross_leg_receive_skew_ns": 1_000_000_000,
        "max_leg_spread_ppm": 200_000,
        "entry_after_cost_basis_nano_usd": 1_000_000_000_000,
        "planned_exit_fees_nano_usd": 1_000_000_000,
        "multiplier": 100,
        "hard_stop_loss_ppm": 500_000,
        "long_bid_nano_usd": 4_000_000_000,
        "long_ask_nano_usd": 4_500_000_000,
        "long_bid_size": 5,
        "long_ask_size": 5,
        "long_quote_event_utc_ns": 2_000_000_002_000_000_000,
        "long_quote_receive_utc_ns": 2_000_000_002_100_000_000,
        "short_bid_nano_usd": None,
        "short_ask_nano_usd": None,
        "short_bid_size": None,
        "short_ask_size": None,
        "short_quote_event_utc_ns": None,
        "short_quote_receive_utc_ns": None,
        "previous_trend_pass_count": 3,
        "current_trend_pass_count": 3,
        "previous_session_complete": True,
        "current_session_complete": True,
    }
    if carrier_id == "BCS0":
        source.update(
            {
                "long_bid_nano_usd": 7_000_000_000,
                "long_ask_nano_usd": 7_500_000_000,
                "short_bid_nano_usd": 3_000_000_000,
                "short_ask_nano_usd": 3_500_000_000,
                "short_bid_size": 5,
                "short_ask_size": 5,
                "short_quote_event_utc_ns": 2_000_000_002_000_000_000,
                "short_quote_receive_utc_ns": 2_000_000_002_200_000_000,
            }
        )
    return source


class EntryDecisionContractsTest(unittest.TestCase):
    def test_canonical_forbids_float_and_is_order_independent(self) -> None:
        self.assertEqual(
            canonical_json_bytes({"b": 2, "a": 1}),
            canonical_json_bytes({"a": 1, "b": 2}),
        )
        with self.assertRaisesRegex(EntryDecisionF0Error, "CANONICAL_JSON_FLOAT_FORBIDDEN"):
            canonical_json_bytes({"value": 0.5})

    def test_policy_and_input_are_closed_schema(self) -> None:
        policy = policy_document(unknown=True)
        with self.assertRaisesRegex(EntryDecisionF0Error, "ENTRY_POLICY_SCHEMA_INVALID"):
            validate_entry_policy_f0(policy)
        source = input_document()
        source["winner"] = "LC0"
        with self.assertRaisesRegex(EntryDecisionF0Error, "ENTRY_INPUT_SCHEMA_INVALID"):
            validate_entry_decision_input_f0(source)

        source = input_document()
        source["call_universe"].append(deepcopy(source["call_universe"][0]))
        with self.assertRaisesRegex(
            EntryDecisionF0Error, "CALL_UNIVERSE_DUPLICATE_IDENTITY"
        ):
            validate_entry_decision_input_f0(source)

        with self.assertRaisesRegex(
            EntryDecisionF0Error,
            "ENTRY_POLICY_INVALIDATION_CONFIRMATION_UNSUPPORTED",
        ):
            validate_entry_policy_f0(
                policy_document(invalidation_confirmation_sessions=3)
            )

    def test_typed_input_and_policy_cannot_reuse_stale_lineage(self) -> None:
        typed_input = validate_entry_decision_input_f0(input_document())
        typed_policy = validate_entry_policy_f0(policy_document())
        baseline = evaluate_entry_decision_f0(typed_input, typed_policy)
        self.assertEqual(baseline.final_status, "PREFERRED_AND_BACKUP")

        with self.assertRaisesRegex(
            EntryDecisionF0Error,
            "ENTRY_INPUT_TYPED_INTEGRITY_MISMATCH",
        ):
            evaluate_entry_decision_f0(
                replace(typed_input, universe_complete=False),
                typed_policy,
            )

        with self.assertRaisesRegex(
            EntryDecisionF0Error,
            "ENTRY_POLICY_TYPED_INTEGRITY_MISMATCH",
        ):
            evaluate_entry_decision_f0(
                typed_input,
                replace(typed_policy, trend_required_count=2),
            )

    def test_input_rejects_float_future_fact_and_incomplete_universe(self) -> None:
        source = input_document()
        source["account_facts"]["eligible_bankroll_nano_usd"] = 1.0
        with self.assertRaisesRegex(EntryDecisionF0Error, "CANONICAL_JSON_FLOAT_FORBIDDEN"):
            validate_entry_decision_input_f0(source)

        source = input_document()
        source["facts_max_event_utc_ns"] = source["decision_cutoff_utc_ns"] + 1
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.final_status, "NO_DECISION")
        self.assertEqual(result.reason_code, "FUTURE_DATA_DETECTED")
        self.assertEqual(result.trace.stages[0].state, "BLOCKED")

        source = input_document()
        source["universe_complete"] = False
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.final_status, "NO_DECISION")
        self.assertEqual(result.reason_code, "CALL_UNIVERSE_INCOMPLETE")

    def test_snapshot_hash_conflict_and_stale_quote_are_no_decision(self) -> None:
        source = input_document()
        source["call_universe"][0]["snapshot_sha256"] = HEX_D
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.final_status, "NO_DECISION")
        self.assertEqual(result.reason_code, "OPTION_ATOMIC_SNAPSHOT_MISMATCH")

        source = input_document()
        source["call_universe"][0]["quote_event_utc_ns"] -= 5_000_000_001
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.final_status, "NO_DECISION")
        self.assertEqual(result.reason_code, "OPTION_QUOTE_STALE")

    def test_nonpositive_evidence_lower_bound_accepts_required_zero_kelly(self) -> None:
        source = input_document()
        for receipt in source["structure_evidence"].values():
            receipt["expected_net_return_on_entry_debit_ppm"] = -1
            receipt["robust_full_kelly_ppm"] = 0
            receipt["half_kelly_ppm"] = 0

        normalized = validate_entry_decision_input_f0(source)
        self.assertEqual(normalized.lc0_evidence.half_kelly_ppm, 0)
        self.assertEqual(normalized.bcs0_evidence.half_kelly_ppm, 0)
        result = evaluate_entry_decision_f0(normalized, policy_document())
        self.assertEqual(result.final_status, "NO_ACTION")
        self.assertEqual(result.reason_code, "ALL_STRUCTURES_ELIMINATED")

    def test_entry_contract_accepts_full_evidence_v2_return_domain(self) -> None:
        source = input_document()
        source["structure_evidence"]["LC0"][
            "expected_net_return_on_entry_debit_ppm"
        ] = 20_000_000
        source["structure_evidence"]["LC0"]["arithmetic_mean_ppm"] = 20_050_000

        normalized = validate_entry_decision_input_f0(source)
        self.assertEqual(
            normalized.lc0_evidence.expected_net_return_on_entry_debit_ppm,
            20_000_000,
        )

    def test_h20_must_be_the_twentieth_forward_xnys_session(self) -> None:
        from gld_management_research.xnys_calendar import action_1045_utc_ns

        source = input_document()
        source["entry_session_date"] = "2027-03-19"
        source["decision_cutoff_utc_ns"] = action_1045_utc_ns("2027-03-19")
        with self.assertRaisesRegex(
            EntryDecisionF0Error, "H20_SESSION_SEQUENCE_INVALID"
        ):
            validate_entry_decision_input_f0(source)

    def test_entry_session_cutoff_and_contract_dates_are_bound(self) -> None:
        for invalid_session in ("2027-03-20", "2027-03-26"):
            with self.subTest(invalid_session=invalid_session):
                source = input_document()
                source["entry_session_date"] = invalid_session
                with self.assertRaisesRegex(
                    EntryDecisionF0Error, "ENTRY_SESSION_NOT_XNYS_SESSION"
                ):
                    validate_entry_decision_input_f0(source)

        source = input_document()
        source["decision_cutoff_utc_ns"] -= 1
        with self.assertRaisesRegex(
            EntryDecisionF0Error, "ENTRY_CUTOFF_CLOCK_INVALID"
        ):
            validate_entry_decision_input_f0(source)

        for field, invalid_date in (
            ("expiry_date", "2027-01-01"),
            ("last_trading_date", "2027-03-17"),
        ):
            with self.subTest(field=field):
                source = input_document()
                source["call_universe"][0][field] = invalid_date
                with self.assertRaisesRegex(
                    EntryDecisionF0Error,
                    "CALL_CONTRACT_DATE_RELATION_INVALID",
                ):
                    validate_entry_decision_input_f0(source)

    def test_stressed_max_loss_basis_must_equal_stressed_debit(self) -> None:
        for carrier_id, collection in (
            ("LC0", "lc0_economics"),
            ("BCS0", "bcs0_economics"),
        ):
            with self.subTest(carrier_id=carrier_id):
                source = input_document()
                source[collection][0]["stressed_max_loss_basis_nano_usd"] -= 1
                with self.assertRaisesRegex(
                    EntryDecisionF0Error,
                    f"{carrier_id}_STRESSED_MAX_LOSS_BASIS_MISMATCH",
                ):
                    validate_entry_decision_input_f0(source)

    def test_evidence_must_match_entry_exit_fee_policy_and_as_of(self) -> None:
        source = input_document()
        changed_exit = exit_policy_document("LC0")
        changed_exit["hard_stop_loss_ppm"] = 333_333
        changed_exit["policy_sha256"] = canonical_json_sha256(
            {
                "policy_id": changed_exit["policy_id"],
                "hard_stop_loss_ppm": changed_exit["hard_stop_loss_ppm"],
            }
        )
        source["exit_policies"]["LC0"] = changed_exit
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.final_status, "SINGLE_PLAN")
        self.assertEqual(result.preferred_plan.carrier_id, "BCS0")
        self.assertEqual(
            result.trace.stages[2].reason_code,
            "STRUCTURE_EVIDENCE_POLICY_BINDING_MISMATCH",
        )

        source = input_document()
        source["exit_policies"]["LC0"]["fee_schedule_sha256"] = HEX_E
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.final_status, "SINGLE_PLAN")
        self.assertEqual(result.preferred_plan.carrier_id, "BCS0")
        self.assertEqual(
            result.trace.stages[2].reason_code,
            "STRUCTURE_EVIDENCE_POLICY_BINDING_MISMATCH",
        )

        source = input_document()
        source["structure_evidence"]["LC0"][
            "evidence_max_estimation_outcome_utc_ns"
        ] = source["decision_cutoff_utc_ns"] + 1
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.final_status, "SINGLE_PLAN")
        self.assertEqual(result.preferred_plan.carrier_id, "BCS0")
        self.assertEqual(
            result.trace.stages[2].reason_code,
            "STRUCTURE_EVIDENCE_FUTURE_OUTCOME",
        )

        relaxed_policy = policy_document(
            policy_id="GLD_ENTRY_RESEARCH_TRIAL_F0_RELAXED",
            trend_required_count=2,
            breakout_required_count=1,
        )
        result = evaluate_entry_decision_f0(input_document(), relaxed_policy)
        self.assertEqual(result.final_status, "NO_ACTION")
        self.assertEqual(
            result.trace.stages[2].reason_code,
            "STRUCTURE_EVIDENCE_ENTRY_POLICY_BINDING_MISMATCH",
        )

    def test_data_qualified_input_cannot_promote_synthetic_evidence(self) -> None:
        source = input_document()
        source["classification"] = "DATA_QUALIFIED"
        source["data_qualification_status"] = "DATA_QUALIFIED"
        result = evaluate_entry_decision_f0(
            source,
            policy_document(policy_status="ACTIVE"),
        )
        self.assertEqual(result.final_status, "NO_ACTION")
        self.assertEqual(
            result.trace.stages[2].reason_code,
            "STRUCTURE_EVIDENCE_CLASSIFICATION_MISMATCH",
        )
        self.assertEqual(
            result.trace.stages[3].reason_code,
            "STRUCTURE_EVIDENCE_CLASSIFICATION_MISMATCH",
        )


class EntryDecisionLogicTest(unittest.TestCase):
    def test_maximum_legal_input_can_seal_wide_derived_integers(self) -> None:
        maximum_i64 = 2**63 - 1
        source = input_document()
        source["lc0_economics"] = [source["lc0_economics"][0]]
        source["bcs0_economics"] = []
        source["structure_evidence"]["BCS0"] = None
        lc_call = next(
            item
            for item in source["call_universe"]
            if item["contract_id"] == "LC-LONG"
        )
        lc_call.update(
            {
                "bid_nano_usd": 999_999_999,
                "ask_nano_usd": 1_000_000_000,
                "bid_size": 1_000_000,
                "ask_size": 1_000_000,
                "multiplier": 100,
            }
        )
        economics = source["lc0_economics"][0]
        economics.update(
            {
                "stressed_entry_debit_nano_usd": 100_000_000_000,
                "stressed_max_loss_basis_nano_usd": 100_000_000_000,
                "delta_notional_nano_usd": 1,
            }
        )
        source["planned_exit_fees_nano_usd"]["LC0"] = 0
        source["account_facts"].update(
            {
                "eligible_bankroll_nano_usd": maximum_i64,
                "current_nlv_nano_usd": maximum_i64,
                "peak_nlv_nano_usd": maximum_i64,
                "settled_cash_nano_usd": maximum_i64,
                "cash_reserve_nano_usd": 0,
                "delta_limit_nano_usd": maximum_i64,
                "external_capacity_units": 1_000_000,
            }
        )
        evidence = source["structure_evidence"]["LC0"]
        evidence.update(
            {
                "expected_net_return_on_entry_debit_ppm": 100_000_000,
                "arithmetic_mean_ppm": 100_000_000,
                "full_kelly_ppm": 1_000_000,
                "bootstrap_kelly_5pct_ppm": 1_000_000,
                "robust_full_kelly_ppm": 1_000_000,
                "half_kelly_ppm": 500_000,
            }
        )

        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.final_status, "SINGLE_PLAN")
        self.assertGreater(result.preferred_plan.comparison_numerator, 10**19)
        self.assertLess(
            len(str(result.preferred_plan.comparison_numerator)),
            64,
        )
        canonical_json_bytes(result.as_dict())

    def test_gate_candidate_quota_boundaries(self) -> None:
        source = input_document()
        source["entry_gate_facts"]["sma50_slope_nano_usd_per_session"] = -1
        source["entry_gate_facts"]["confirmation_closes_nano_usd"] = [
            202_000_000_000 for _ in range(9)
        ] + [200_000_000_000 for _ in range(6)]

        strict = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(strict.final_status, "NO_ACTION")
        self.assertEqual(strict.trace.stages[1].reason_code, "ENTRY_GATE_QUOTA_NOT_MET")

        relaxed = evaluate_entry_decision_f0(
            source,
            policy_document(trend_required_count=2, breakout_required_count=1),
        )
        self.assertEqual(relaxed.trace.stages[1].state, "PASS")
        self.assertEqual(relaxed.final_status, "NO_ACTION")
        self.assertEqual(
            relaxed.trace.stages[2].reason_code,
            "STRUCTURE_EVIDENCE_ENTRY_POLICY_BINDING_MISMATCH",
        )

    def test_all_four_gate_candidates_keep_groups_separate(self) -> None:
        trend_two_breakout_two = input_document()
        trend_two_breakout_two["entry_gate_facts"][
            "sma50_slope_nano_usd_per_session"
        ] = -1
        trend_three_breakout_one = input_document()
        trend_three_breakout_one["entry_gate_facts"][
            "confirmation_closes_nano_usd"
        ] = [202_000_000_000] * 9 + [200_000_000_000] * 6
        expectations = {
            (3, 2): ("FAIL", "FAIL"),
            (2, 2): ("PASS", "FAIL"),
            (3, 1): ("FAIL", "PASS"),
            (2, 1): ("PASS", "PASS"),
        }
        for candidate in entry_gate_policy_candidates_f0():
            key = (
                candidate["trend_required_count"],
                candidate["breakout_required_count"],
            )
            policy = policy_document(**candidate)
            with self.subTest(candidate=key, facts="2+2"):
                self.assertEqual(
                    evaluate_entry_decision_f0(
                        trend_two_breakout_two, policy
                    ).trace.stages[1].state,
                    expectations[key][0],
                )
            with self.subTest(candidate=key, facts="3+1"):
                self.assertEqual(
                    evaluate_entry_decision_f0(
                        trend_three_breakout_one, policy
                    ).trace.stages[1].state,
                    expectations[key][1],
                )

    def test_missing_gate_fact_cannot_be_bypassed_by_relaxed_quota(self) -> None:
        source = input_document()
        source["entry_gate_facts"]["sma50_slope_nano_usd_per_session"] = None
        result = evaluate_entry_decision_f0(
            source,
            policy_document(trend_required_count=2, breakout_required_count=1),
        )
        self.assertEqual(result.final_status, "NO_DECISION")
        self.assertEqual(result.trace.stages[1].state, "BLOCKED")
        self.assertEqual(result.trace.stages[1].reason_code, "ENTRY_GATE_FACT_MISSING")

    def test_gate_failure_keeps_all_downstream_stages_not_run(self) -> None:
        source = input_document()
        source["entry_gate_facts"]["prior_close_nano_usd"] = 180_000_000_000
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.final_status, "NO_ACTION")
        self.assertEqual([stage.stage_id for stage in result.trace.stages], [
            "INPUT_QUALIFICATION",
            "ENTRY_GATE",
            "LC0_EVALUATION",
            "BCS0_EVALUATION",
            "SIZING",
            "PREFERENCE",
            "EXIT_POLICY_BINDING",
            "FINAL_DECISION",
        ])
        self.assertTrue(all(stage.state == "NOT_RUN" for stage in result.trace.stages[2:]))

    def test_dual_qualified_produces_deterministic_preference_and_backup(self) -> None:
        result = evaluate_entry_decision_f0(input_document(), policy_document())
        self.assertEqual(result.final_status, "PREFERRED_AND_BACKUP")
        self.assertEqual(result.preferred_plan.carrier_id, "BCS0")
        self.assertEqual(result.backup_plan.carrier_id, "LC0")
        self.assertFalse(result.actionable)
        self.assertEqual(result.broker_order_count, 0)
        self.assertEqual(result.authority_status, "NO_DECISION_EFFECT")
        repeated = evaluate_entry_decision_f0(
            deepcopy(input_document()), deepcopy(policy_document())
        )
        self.assertEqual(result.canonical_bytes, repeated.canonical_bytes)
        unsigned = result.as_dict()
        unsigned.pop("result_sha256")
        self.assertEqual(result.result_sha256, sha256(canonical_json_bytes(unsigned)).hexdigest())

    def test_lc_and_bcs_select_independently_and_one_failure_keeps_other(self) -> None:
        both = evaluate_entry_decision_f0(input_document(), policy_document())
        self.assertEqual(both.preferred_plan.carrier_id, "BCS0")
        plans = {both.preferred_plan.carrier_id: both.preferred_plan}
        plans[both.backup_plan.carrier_id] = both.backup_plan
        self.assertEqual(plans["LC0"].contract_ids, ("LC-LONG",))
        self.assertEqual(plans["BCS0"].contract_ids[0], "BCS-LONG")
        self.assertNotEqual(plans["LC0"].contract_ids[0], plans["BCS0"].contract_ids[0])

        source = input_document()
        source["bcs0_economics"] = []
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.final_status, "SINGLE_PLAN")
        self.assertEqual(result.preferred_plan.carrier_id, "LC0")
        self.assertIsNone(result.backup_plan)
        self.assertEqual(result.trace.stages[3].state, "ELIMINATED")

    def test_stressed_debit_cannot_understate_executable_entry_cost(self) -> None:
        source = input_document()
        lc = source["lc0_economics"][0]
        lc["stressed_entry_debit_nano_usd"] = 999_000_000_000
        lc["stressed_max_loss_basis_nano_usd"] = 999_000_000_000
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.trace.stages[2].state, "PASS")
        self.assertEqual(
            result.trace.stages[2].calculation["selected_contract_id"],
            "BCS-LONG",
        )

        source = input_document()
        bcs = source["bcs0_economics"][0]
        bcs["stressed_entry_debit_nano_usd"] = 599_000_000_000
        bcs["stressed_max_loss_basis_nano_usd"] = 599_000_000_000
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.final_status, "SINGLE_PLAN")
        self.assertEqual(result.preferred_plan.carrier_id, "LC0")
        self.assertEqual(result.trace.stages[3].reason_code, "BCS0_NO_ELIGIBLE_PAIR")

    def test_selector_tie_break_and_coarse_fine_disagreement(self) -> None:
        source = input_document()
        source["call_universe"].append(call("LC-HIGHER", 192_000_000_000, 500_000))
        source["lc0_economics"].append({
            "contract_id": "LC-HIGHER",
            "stressed_entry_debit_nano_usd": 1_000_000_000_000,
            "stressed_max_loss_basis_nano_usd": 1_000_000_000_000,
            "delta_notional_nano_usd": 10_000_000_000,
        })
        result = evaluate_entry_decision_f0(source, policy_document())
        lc_stage = result.trace.stages[2]
        self.assertEqual(lc_stage.calculation["selected_contract_id"], "LC-HIGHER")

        source["call_universe"][-1]["fine_delta_ppm"] = 450_000
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.trace.stages[2].state, "ELIMINATED")
        self.assertEqual(result.trace.stages[2].reason_code, "LC0_COARSE_FINE_SELECTION_DISAGREEMENT")

    def test_bcs_selector_skips_multiplier_mismatch_before_ranking(self) -> None:
        source = input_document()
        invalid_long = call(
            "BCS-INVALID-LONG",
            192_000_000_000,
            500_000,
        )
        invalid_short = call(
            "BCS-INVALID-SHORT",
            209_000_000_000,
            250_000,
            bid=4_000_000_000,
            ask=5_000_000_000,
        )
        invalid_short["multiplier"] = 50
        source["call_universe"].extend((invalid_long, invalid_short))
        source["bcs0_economics"].append(
            {
                "long_contract_id": "BCS-INVALID-LONG",
                "short_contract_id": "BCS-INVALID-SHORT",
                "stressed_entry_debit_nano_usd": 600_000_000_000,
                "stressed_max_loss_basis_nano_usd": 600_000_000_000,
                "delta_notional_nano_usd": 5_000_000_000,
            }
        )

        result = evaluate_entry_decision_f0(source, policy_document())

        bcs_stage = result.trace.stages[3]
        self.assertEqual(bcs_stage.state, "PASS")
        self.assertEqual(
            bcs_stage.calculation["selected_contract_ids"],
            ["BCS-LONG", "BCS-SHORT"],
        )

        source = input_document()
        source["call_universe"][2]["fine_delta_ppm"] = 260_000
        source["call_universe"].append(
            call(
                "BCS-SHORT-2",
                211_000_000_000,
                260_000,
                bid=4_000_000_000,
                ask=5_000_000_000,
            )
        )
        source["call_universe"][-1]["fine_delta_ppm"] = 250_000
        source["bcs0_economics"].append(
            {
                "long_contract_id": "BCS-LONG",
                "short_contract_id": "BCS-SHORT-2",
                "stressed_entry_debit_nano_usd": 600_000_000_000,
                "stressed_max_loss_basis_nano_usd": 600_000_000_000,
                "delta_notional_nano_usd": 5_000_000_000,
            }
        )
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.trace.stages[3].state, "ELIMINATED")
        self.assertEqual(
            result.trace.stages[3].reason_code,
            "BCS0_COARSE_FINE_SELECTION_DISAGREEMENT",
        )

    def test_delta_band_boundaries_are_inclusive(self) -> None:
        source = input_document()
        source["lc0_economics"] = [source["lc0_economics"][0]]
        source["call_universe"][0]["coarse_delta_ppm"] = 450_000
        source["call_universe"][0]["fine_delta_ppm"] = 450_000
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.trace.stages[2].state, "PASS")

        source["call_universe"][0]["coarse_delta_ppm"] = 449_999
        source["call_universe"][0]["fine_delta_ppm"] = 449_999
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.trace.stages[2].state, "ELIMINATED")

    def test_safe_quantity_uses_all_caps_and_includes_exit_fee(self) -> None:
        source = input_document()
        source["account_facts"]["external_capacity_units"] = 4
        result = evaluate_entry_decision_f0(source, policy_document())
        lc = result.trace.stages[4].calculation["structures"]["LC0"]
        self.assertEqual(lc["planned_loss_per_unit_nano_usd"], 1_001_000_000_000)
        self.assertEqual(lc["q_external"], 4)

    def test_each_safe_quantity_cap_can_bind(self) -> None:
        cases = {
            "q_kelly": lambda source, policy: (
                source["structure_evidence"]["LC0"].update(
                    bootstrap_kelly_5pct_ppm=100_000,
                    robust_full_kelly_ppm=100_000,
                    half_kelly_ppm=50_000,
                )
            ),
            "q_account": lambda source, policy: policy.update(
                account_loss_budget_ppm=20_000
            ),
            "q_cash": lambda source, policy: source["account_facts"].update(
                settled_cash_nano_usd=400_000_000_000
            ),
            "q_delta": lambda source, policy: source["account_facts"].update(
                delta_limit_nano_usd=20_000_000_000
            ),
            "q_liquidity": lambda source, policy: None,
            "q_external": lambda source, policy: source["account_facts"].update(
                external_capacity_units=1
            ),
        }
        for cap_name, mutate in cases.items():
            source = input_document()
            policy = policy_document()
            mutate(source, policy)
            for evidence in source["structure_evidence"].values():
                evidence["entry_policy_sha256"] = canonical_json_sha256(policy)
            result = evaluate_entry_decision_f0(source, policy)
            sizing = result.trace.stages[4].calculation["structures"]["LC0"]
            with self.subTest(cap=cap_name):
                self.assertEqual(
                    sizing["safe_quantity"],
                    min(sizing[name] for name in cases),
                )
                self.assertIn(cap_name, sizing["binding_caps"])

    def test_missing_required_shared_cap_is_no_decision(self) -> None:
        source = input_document()
        source["account_facts"]["external_capacity_units"] = None
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.final_status, "NO_DECISION")
        self.assertEqual(result.reason_code, "SIZING_REQUIRED_FACT_BLOCKED")
        self.assertEqual(result.trace.stages[4].state, "BLOCKED")
        self.assertTrue(
            all(stage.state == "NOT_RUN" for stage in result.trace.stages[5:])
        )

    def test_negative_existing_delta_never_expands_capacity(self) -> None:
        base = input_document()
        base["account_facts"]["eligible_bankroll_nano_usd"] = 100_000_000_000_000
        base["account_facts"]["settled_cash_nano_usd"] = 100_000_000_000_000
        base["account_facts"]["current_nlv_nano_usd"] = 100_000_000_000_000
        base["account_facts"]["peak_nlv_nano_usd"] = 100_000_000_000_000
        base["account_facts"]["delta_limit_nano_usd"] = 20_000_000_000
        negative = deepcopy(base)
        negative["account_facts"]["current_signed_gld_delta_exposure_nano_usd"] = -10_000_000_000
        zero = deepcopy(base)
        zero["account_facts"]["current_signed_gld_delta_exposure_nano_usd"] = 0
        neg_result = evaluate_entry_decision_f0(negative, policy_document())
        zero_result = evaluate_entry_decision_f0(zero, policy_document())
        neg_sizing = neg_result.trace.stages[4].calculation["structures"]["LC0"]
        zero_sizing = zero_result.trace.stages[4].calculation["structures"]["LC0"]
        self.assertEqual(neg_sizing["q_delta"], zero_sizing["q_delta"])

    def test_drawdown_boundary_15_percent_eliminates_all(self) -> None:
        source = input_document()
        source["account_facts"]["current_nlv_nano_usd"] = 85_000_000_000_000
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.final_status, "NO_ACTION")
        self.assertEqual(result.trace.stages[4].reason_code, "ALL_STRUCTURES_ZERO_QUANTITY")

    def test_drawdown_5_10_15_percent_boundaries(self) -> None:
        factors = {50_000: 1_000_000, 100_000: 800_000, 150_000: 0}
        for drawdown_ppm, expected_factor in factors.items():
            with self.subTest(drawdown_ppm=drawdown_ppm):
                source = input_document()
                peak = source["account_facts"]["peak_nlv_nano_usd"]
                source["account_facts"]["current_nlv_nano_usd"] = (
                    peak * (1_000_000 - drawdown_ppm) // 1_000_000
                )
                result = evaluate_entry_decision_f0(source, policy_document())
                sizing = result.trace.stages[4].calculation["structures"]["LC0"]
                self.assertEqual(sizing["drawdown_factor_ppm"], expected_factor)

    def test_h20_plus_30_is_inclusive_and_universe_length_is_arbitrary(self) -> None:
        source = input_document()
        for candidate in source["call_universe"]:
            candidate["last_trading_date"] = "2027-05-16"  # exact H20 + 30
        for index in range(25):
            source["call_universe"].append(
                call(
                    f"OUT-{index:02d}",
                    230_000_000_000 + index,
                    100_000,
                    last_trading="2027-05-16",
                )
            )
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.trace.stages[2].state, "PASS")
        self.assertEqual(result.trace.stages[3].state, "PASS")
        self.assertEqual(result.trace.stages[2].calculation["universe_length"], 28)


class EntryExitTriggerResearchTest(unittest.TestCase):
    def test_lc_fresh_bid_and_bcs_complete_exit_value_trigger_hard_stop(self) -> None:
        lc = _evaluate_exit_trigger_fixture_f0(exit_observation("LC0"))
        self.assertEqual(lc.exit_state, "EXIT_DUE_LATCHED")
        self.assertEqual(lc.trigger_reason, "HARD_STOP")
        self.assertTrue(lc.hard_stop_evaluable)

        bcs = _evaluate_exit_trigger_fixture_f0(exit_observation("BCS0"))
        self.assertEqual(bcs.exit_state, "EXIT_DUE_LATCHED")
        self.assertEqual(bcs.trigger_reason, "HARD_STOP")
        self.assertTrue(bcs.hard_stop_evaluable)

    def test_stale_no_size_too_wide_and_skew_cannot_trigger_hard_stop(self) -> None:
        mutations = (
            ("STALE", lambda value: value.update(long_quote_event_utc_ns=1_999_999_000_000_000_000)),
            ("NO_SIZE", lambda value: value.update(long_bid_size=0)),
            ("TOO_WIDE", lambda value: value.update(long_ask_nano_usd=10_000_000_000)),
            ("SKEW", lambda value: value.update(short_quote_receive_utc_ns=2_000_000_004_000_000_000)),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                source = exit_observation("BCS0")
                mutate(source)
                result = _evaluate_exit_trigger_fixture_f0(source)
                self.assertEqual(result.exit_state, "MONITORING")
                self.assertFalse(result.hard_stop_evaluable)

    def test_two_complete_invalidation_sessions_latch_and_never_clear(self) -> None:
        source = exit_observation("LC0")
        source["long_bid_nano_usd"] = 9_000_000_000
        source["long_ask_nano_usd"] = 9_500_000_000
        source["previous_trend_pass_count"] = 1
        source["current_trend_pass_count"] = 0
        result = _evaluate_exit_trigger_fixture_f0(source)
        self.assertEqual(result.exit_state, "EXIT_DUE_LATCHED")
        self.assertEqual(result.trigger_reason, "CONFIRMED_INVALIDATION")

        rebound = exit_observation("LC0")
        rebound["already_latched"] = True
        rebound["previous_trend_pass_count"] = 3
        rebound["current_trend_pass_count"] = 3
        latched = _evaluate_exit_trigger_fixture_f0(rebound)
        self.assertEqual(latched.exit_state, "EXIT_DUE_LATCHED")
        self.assertEqual(latched.trigger_reason, "PREVIOUS_LATCH")
        self.assertFalse(latched.actionable)
        self.assertEqual(latched.broker_order_count, 0)

    def test_exact_preference_tie_favors_lc0(self) -> None:
        source = input_document()
        source["structure_evidence"]["BCS0"]["expected_net_return_on_entry_debit_ppm"] = 100_000
        for candidate in source["call_universe"]:
            if candidate["contract_id"] == "BCS-LONG":
                candidate["ask_nano_usd"] = 14_000_000_000
        lc = source["lc0_economics"][0]
        lc["stressed_entry_debit_nano_usd"] = 1_001_000_000_000
        lc["stressed_max_loss_basis_nano_usd"] = 1_001_000_000_000
        bcs = source["bcs0_economics"][0]
        bcs["stressed_entry_debit_nano_usd"] = 1_000_000_000_000
        bcs["stressed_max_loss_basis_nano_usd"] = 1_000_000_000_000
        source["planned_exit_fees_nano_usd"]["BCS0"] = 2_000_000_000
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.preferred_plan.carrier_id, "LC0")
        comparison = result.trace.stages[5].calculation["structures"]
        self.assertEqual(
            comparison["LC0"]["comparison_numerator"],
            comparison["BCS0"]["comparison_numerator"],
        )
        self.assertEqual(
            comparison["LC0"]["total_planned_loss_nano_usd"],
            comparison["BCS0"]["total_planned_loss_nano_usd"],
        )
        self.assertEqual(
            comparison["LC0"]["current_entry_cash_usage_nano_usd"],
            comparison["BCS0"]["current_entry_cash_usage_nano_usd"],
        )

    def test_exit_policy_is_bound_per_structure(self) -> None:
        source = input_document()
        result = evaluate_entry_decision_f0(source, policy_document())
        for plan in (result.preferred_plan, result.backup_plan):
            self.assertEqual(plan.management_policy_sha256, HEX_A)
            self.assertEqual(plan.h20_date, "2027-04-16")
            self.assertEqual(plan.hard_stop_loss_ppm, 500_000)
            self.assertEqual(
                plan.hard_stop_policy_sha256,
                source["exit_policies"][plan.carrier_id]["policy_sha256"],
            )
        self.assertNotEqual(
            result.preferred_plan.hard_stop_policy_id,
            result.backup_plan.hard_stop_policy_id,
        )

    def test_qualified_path_requires_active_owner_policy(self) -> None:
        source = input_document()
        source["classification"] = "DATA_QUALIFIED"
        source["data_qualification_status"] = "DATA_QUALIFIED"
        result = evaluate_entry_decision_f0(source, policy_document())
        self.assertEqual(result.final_status, "NO_DECISION")
        self.assertEqual(result.reason_code, "ACTIVE_ENTRY_POLICY_NOT_AVAILABLE")

    def test_all_four_gate_candidates_are_preregistered(self) -> None:
        self.assertEqual(len(entry_gate_policy_candidates_f0()), 4)


if __name__ == "__main__":
    unittest.main()
