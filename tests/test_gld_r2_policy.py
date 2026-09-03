from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import unittest

from gld_entry_decision_f0 import (
    entry_gate_policy_candidates_f0,
    evaluate_entry_decision_f0,
    validate_entry_decision_input_f0,
)
from gld_r2_theta_qualification.contracts import (
    build_r1_fixed_policy_projection,
)
from gld_r2_theta_qualification.policy import (
    PreferenceCandidateV1,
    SyntheticPolicySessionInstructionV1,
    choose_q1_preference,
    evaluate_r2_gate,
    reduce_synthetic_q1_policy_ledger,
    select_bcs0_r2,
    select_lc0_r2,
)
from gld_r2_theta_qualification.stress import (
    QuoteObservationV1,
    SideFeesV1,
    derive_stressed_episode_after_cost,
    evaluate_exit_checkpoint,
)
from tests.test_gld_entry_decision_f0 import input_document, policy_document


def _sha(label: str) -> str:
    return sha256(label.encode("ascii")).hexdigest()


def _validated_source(document: dict[str, object] | None = None):
    return validate_entry_decision_input_f0(
        input_document() if document is None else document
    )


def _projection():
    return build_r1_fixed_policy_projection(policy_document())


def _quote(
    *,
    bid: int | None,
    ask: int | None,
    tick: int | None = 2,
    bid_size: int | None = 10,
    ask_size: int | None = 10,
    executable: bool | None = True,
    quote_quality_pass: bool | None = True,
) -> QuoteObservationV1:
    return QuoteObservationV1(
        bid_nano_usd=bid,
        ask_nano_usd=ask,
        tick_nano_usd=tick,
        bid_size=bid_size,
        ask_size=ask_size,
        executable=executable,
        quote_quality_pass=quote_quality_pass,
    )


class R2GateParityTests(unittest.TestCase):
    def test_all_four_r1_gate_candidates_match_public_r1_evaluator(self) -> None:
        scenarios = []

        trend_two_breakout_two = input_document()
        trend_two_breakout_two["entry_gate_facts"][
            "sma50_slope_nano_usd_per_session"
        ] = -1
        scenarios.append(trend_two_breakout_two)

        trend_three_breakout_one = input_document()
        trend_three_breakout_one["entry_gate_facts"][
            "confirmation_closes_nano_usd"
        ] = [202_000_000_000] * 9 + [200_000_000_000] * 6
        scenarios.append(trend_three_breakout_one)

        for source_document in scenarios:
            source = _validated_source(source_document)
            for quota in entry_gate_policy_candidates_f0():
                r1_result = evaluate_entry_decision_f0(
                    source_document,
                    policy_document(**quota),
                )
                r1_state = r1_result.trace.stages[1].state
                expected = "PASS" if r1_state == "PASS" else "NO_ACTION"
                with self.subTest(quota=quota, expected=expected):
                    actual = evaluate_r2_gate(
                        source.entry_gate_facts,
                        trend_required_count=quota["trend_required_count"],
                        breakout_required_count=quota["breakout_required_count"],
                    )
                    self.assertEqual(actual.status, expected)
                    self.assertEqual(actual.trend_pass_count, sum((
                        source.entry_gate_facts.prior_close_nano_usd
                        > source.entry_gate_facts.sma50_nano_usd,
                        source.entry_gate_facts.sma50_nano_usd
                        > source.entry_gate_facts.sma200_nano_usd,
                        source.entry_gate_facts.sma50_slope_nano_usd_per_session
                        > 0,
                    )))

    def test_missing_critical_gate_fact_is_unevaluable_not_no_action(self) -> None:
        document = input_document()
        document["entry_gate_facts"][
            "sma50_slope_nano_usd_per_session"
        ] = None
        source = _validated_source(document)

        result = evaluate_r2_gate(
            source.entry_gate_facts,
            trend_required_count=2,
            breakout_required_count=1,
        )

        self.assertEqual(result.status, "UNEVALUABLE")
        self.assertEqual(result.reason_code, "ENTRY_GATE_FACT_MISSING")
        self.assertEqual(result.classification, "SYNTHETIC_ONLY")
        self.assertEqual(result.scope, "RESEARCH_ONLY")
        self.assertEqual(result.authority_status, "NO_DECISION_EFFECT")
        self.assertFalse(result.actionable)
        self.assertEqual(result.broker_order_count, 0)


class R2SelectorParityTests(unittest.TestCase):
    def test_lc0_and_bcs0_match_public_r1_selection_on_existing_fixture(self) -> None:
        source = _validated_source()
        public = evaluate_entry_decision_f0(input_document(), policy_document())
        plans = {
            public.preferred_plan.carrier_id: public.preferred_plan,
            public.backup_plan.carrier_id: public.backup_plan,
        }

        lc0 = select_lc0_r2(
            source,
            policy=_projection(),
        )
        bcs0 = select_bcs0_r2(
            source,
            policy=_projection(),
        )

        self.assertEqual(lc0.status, "PASS")
        self.assertEqual(lc0.selected_contract_ids, plans["LC0"].contract_ids)
        self.assertEqual(bcs0.status, "PASS")
        self.assertEqual(bcs0.selected_contract_ids, plans["BCS0"].contract_ids)

    def test_lc0_uses_r1_tie_order_and_requires_coarse_fine_identity(self) -> None:
        document = input_document()
        higher = deepcopy(document["call_universe"][0])
        higher["contract_id"] = "LC-HIGHER"
        higher["strike_nano_usd"] = 192_000_000_000
        document["call_universe"].append(higher)
        economics = deepcopy(document["lc0_economics"][0])
        economics["contract_id"] = "LC-HIGHER"
        document["lc0_economics"].append(economics)
        source = _validated_source(document)

        selected = select_lc0_r2(
            source,
            policy=_projection(),
        )
        self.assertEqual(selected.selected_contract_ids, ("LC-HIGHER",))

        disagreement_document = deepcopy(document)
        disagreement_document["call_universe"][-1]["fine_delta_ppm"] = 450_000
        disagreement = _validated_source(disagreement_document)
        result = select_lc0_r2(
            disagreement,
            policy=_projection(),
        )
        self.assertEqual(result.status, "NO_ACTION")
        self.assertEqual(
            result.reason_code,
            "LC0_COARSE_FINE_SELECTION_DISAGREEMENT",
        )

    def test_bcs0_is_independent_and_obeys_exact_pair_constraints(self) -> None:
        source = _validated_source()
        result = select_bcs0_r2(
            source,
            policy=_projection(),
        )
        self.assertEqual(result.selected_contract_ids, ("BCS-LONG", "BCS-SHORT"))

        invalid = input_document()
        invalid["call_universe"][2]["multiplier"] = 50
        invalid_source = _validated_source(invalid)
        invalid_result = select_bcs0_r2(
            invalid_source,
            policy=_projection(),
        )
        self.assertEqual(invalid_result.status, "NO_ACTION")
        self.assertEqual(invalid_result.reason_code, "BCS0_NO_ELIGIBLE_PAIR")

    def test_selector_delta_boundaries_and_expiry_safety_are_inclusive(self) -> None:
        lower_boundary = input_document()
        lower_boundary["call_universe"][0]["coarse_delta_ppm"] = 450_000
        lower_boundary["call_universe"][0]["fine_delta_ppm"] = 450_000
        lower_boundary["lc0_economics"] = [
            lower_boundary["lc0_economics"][0]
        ]
        boundary_source = _validated_source(lower_boundary)
        included = select_lc0_r2(
            boundary_source,
            policy=_projection(),
        )
        self.assertEqual(included.status, "PASS")

        below = input_document()
        below["call_universe"][0]["coarse_delta_ppm"] = 449_999
        below["call_universe"][0]["fine_delta_ppm"] = 449_999
        below["lc0_economics"] = [below["lc0_economics"][0]]
        below_source = _validated_source(below)
        excluded = select_lc0_r2(
            below_source,
            policy=_projection(),
        )
        self.assertEqual(excluded.status, "NO_ACTION")

        inclusive_expiry = input_document()
        inclusive_expiry["call_universe"][0]["last_trading_date"] = "2027-05-16"
        inclusive_expiry["lc0_economics"] = [
            inclusive_expiry["lc0_economics"][0]
        ]
        inclusive_source = _validated_source(inclusive_expiry)
        safe = select_lc0_r2(
            inclusive_source,
            policy=_projection(),
        )
        self.assertEqual(safe.status, "PASS")

        too_early = input_document()
        too_early["call_universe"][0]["last_trading_date"] = "2027-05-15"
        too_early["lc0_economics"] = [too_early["lc0_economics"][0]]
        early_source = _validated_source(too_early)
        unsafe = select_lc0_r2(
            early_source,
            policy=_projection(),
        )
        self.assertEqual(unsafe.status, "NO_ACTION")

    def test_bcs0_coarse_fine_pair_disagreement_matches_public_r1(self) -> None:
        document = input_document()
        document["call_universe"][2]["fine_delta_ppm"] = 260_000
        second_short = deepcopy(document["call_universe"][2])
        second_short["contract_id"] = "BCS-SHORT-2"
        second_short["strike_nano_usd"] = 211_000_000_000
        second_short["coarse_delta_ppm"] = 260_000
        second_short["fine_delta_ppm"] = 250_000
        document["call_universe"].append(second_short)
        second_pair = deepcopy(document["bcs0_economics"][0])
        second_pair["short_contract_id"] = "BCS-SHORT-2"
        document["bcs0_economics"].append(second_pair)
        source = _validated_source(document)

        actual = select_bcs0_r2(
            source,
            policy=_projection(),
        )
        public = evaluate_entry_decision_f0(document, policy_document())

        self.assertEqual(actual.status, "NO_ACTION")
        self.assertEqual(
            actual.reason_code,
            public.trace.stages[3].reason_code,
        )

    def test_incomplete_universe_and_malformed_quote_fail_closed(self) -> None:
        incomplete_document = input_document()
        incomplete_document["universe_complete"] = False
        incomplete_source = _validated_source(incomplete_document)
        incomplete = select_lc0_r2(
            incomplete_source,
            policy=_projection(),
        )
        source = _validated_source()
        malformed = select_lc0_r2(
            replace(source, call_universe=(*source.call_universe, None)),
            policy=_projection(),
        )
        self.assertEqual(incomplete.status, "UNEVALUABLE")
        self.assertEqual(malformed.status, "UNEVALUABLE")

        mismatch_document = input_document()
        mismatch_document["call_universe"][0]["snapshot_sha256"] = _sha(
            "another-snapshot"
        )
        mismatch_source = _validated_source(mismatch_document)
        mismatch = select_lc0_r2(
            mismatch_source,
            policy=_projection(),
        )
        self.assertEqual(mismatch.status, "UNEVALUABLE")

        zero_size_document = input_document()
        zero_size_document["call_universe"][0]["bid_size"] = 0
        zero_size_document["lc0_economics"] = [
            zero_size_document["lc0_economics"][0]
        ]
        zero_size_source = _validated_source(zero_size_document)
        zero_size = select_lc0_r2(
            zero_size_source,
            policy=_projection(),
        )
        self.assertEqual(zero_size.status, "NO_ACTION")

    def test_selector_rechecks_future_stale_skew_and_fact_binding(self) -> None:
        cases: list[dict[str, object]] = []

        future = input_document()
        cutoff = future["decision_cutoff_utc_ns"]
        future["call_universe"][0]["quote_event_utc_ns"] = cutoff + 1
        future["call_universe"][0]["quote_receive_utc_ns"] = cutoff + 1
        cases.append(future)

        stale = input_document()
        stale_cutoff = stale["decision_cutoff_utc_ns"]
        for call in stale["call_universe"]:
            call["quote_event_utc_ns"] = stale_cutoff - 6_000_000_000
            call["quote_receive_utc_ns"] = stale_cutoff - 5_900_000_000
        cases.append(stale)

        skew = input_document()
        skew_cutoff = skew["decision_cutoff_utc_ns"]
        skew["call_universe"][0]["quote_event_utc_ns"] = (
            skew_cutoff - 3_100_000_000
        )
        skew["call_universe"][0]["quote_receive_utc_ns"] = (
            skew_cutoff - 3_000_000_000
        )
        cases.append(skew)

        technical = input_document()
        technical["call_universe"][0]["technical_facts_sha256"] = _sha(
            "other-technical-facts"
        )
        cases.append(technical)

        for case_index, document in enumerate(cases):
            with self.subTest(case=case_index):
                result = select_lc0_r2(
                    _validated_source(document),
                    policy=_projection(),
                )
                self.assertEqual(result.status, "UNEVALUABLE")


class R2PreferenceTests(unittest.TestCase):
    def test_dual_preference_uses_training_lcb_and_safe_backup(self) -> None:
        lc0 = PreferenceCandidateV1(
            carrier_id="LC0",
            entry_debit_nano_usd=1_000,
            training_only_lcb_ppm=100_000,
            planned_loss_nano_usd=900,
            cash_usage_nano_usd=1_000,
        )
        bcs0 = PreferenceCandidateV1(
            carrier_id="BCS0",
            entry_debit_nano_usd=600,
            training_only_lcb_ppm=200_000,
            planned_loss_nano_usd=500,
            cash_usage_nano_usd=600,
        )

        result = choose_q1_preference((lc0, bcs0), "DUAL_PREFERENCE")

        self.assertEqual(result.decision_status, "PREFERRED_AND_BACKUP")
        self.assertEqual(result.preferred.carrier_id, "BCS0")
        self.assertEqual(result.backup.carrier_id, "LC0")
        self.assertEqual(result.preferred.comparison_numerator, 120_000_000)
        self.assertTrue(result.backup_requires_fresh_snapshot_and_rerun)
        self.assertFalse(result.backup_automatic_execution)
        self.assertFalse(result.actionable)

    def test_preference_secondary_keys_and_exact_tie_favor_lc0(self) -> None:
        lc0 = PreferenceCandidateV1("LC0", 1_000, 100_000, 500, 1_000)
        bcs0 = PreferenceCandidateV1("BCS0", 500, 200_000, 500, 1_000)
        tied = choose_q1_preference((bcs0, lc0), "DUAL_PREFERENCE")
        self.assertEqual(tied.preferred.carrier_id, "LC0")

        less_loss = replace(bcs0, planned_loss_nano_usd=499)
        loss_result = choose_q1_preference((lc0, less_loss), "DUAL_PREFERENCE")
        self.assertEqual(loss_result.preferred.carrier_id, "BCS0")

        less_cash = replace(bcs0, cash_usage_nano_usd=999)
        cash_result = choose_q1_preference((lc0, less_cash), "DUAL_PREFERENCE")
        self.assertEqual(cash_result.preferred.carrier_id, "BCS0")

    def test_single_mode_has_no_backup(self) -> None:
        lc0 = PreferenceCandidateV1("LC0", 1_000, 100_000, 500, 1_000)
        result = choose_q1_preference((lc0,), "LC0_ONLY")
        self.assertEqual(result.decision_status, "SINGLE_PLAN")
        self.assertIsNone(result.backup)
        self.assertFalse(result.backup_requires_fresh_snapshot_and_rerun)


class R2StressTests(unittest.TestCase):
    def test_adverse_ticks_and_r1_return_math(self) -> None:
        fees = SideFeesV1(
            long_entry_nano_usd=3,
            long_exit_nano_usd=5,
            short_entry_nano_usd=4,
            short_exit_nano_usd=6,
        )
        lc = derive_stressed_episode_after_cost(
            carrier_id="LC0",
            entry_long_quote=_quote(bid=90, ask=100),
            exit_long_quote=_quote(bid=120, ask=125),
            entry_short_quote=None,
            exit_short_quote=None,
            fees=fees,
            multiplier=10,
            episode_id="LC0-E1",
            fold_id="DEV",
            split="DEVELOPMENT",
            entry_utc_ns=1,
            exit_utc_ns=2,
            input_sha256=_sha("lc0-e1"),
        )
        self.assertEqual(lc.status, "PASS")
        self.assertEqual(lc.stressed_long_entry_nano_usd, 102)
        self.assertEqual(lc.stressed_long_exit_nano_usd, 118)
        self.assertEqual(lc.entry_debit_nano_usd, 1_020)
        self.assertEqual(lc.entry_after_cost_basis_nano_usd, 1_023)
        self.assertEqual(lc.after_cost_profit_nano_usd, 152)
        self.assertEqual(lc.after_cost_return_ppm, 149_020)

        bcs = derive_stressed_episode_after_cost(
            carrier_id="BCS0",
            entry_long_quote=_quote(bid=90, ask=100),
            exit_long_quote=_quote(bid=120, ask=125),
            entry_short_quote=_quote(bid=40, ask=45),
            exit_short_quote=_quote(bid=47, ask=50),
            fees=fees,
            multiplier=10,
            episode_id="BCS0-E1",
            fold_id="DEV",
            split="DEVELOPMENT",
            entry_utc_ns=1,
            exit_utc_ns=2,
            input_sha256=_sha("bcs0-e1"),
        )
        self.assertEqual(bcs.status, "PASS")
        self.assertEqual(bcs.stressed_short_entry_nano_usd, 38)
        self.assertEqual(bcs.stressed_short_exit_nano_usd, 52)
        self.assertEqual(bcs.entry_debit_nano_usd, 640)
        self.assertEqual(bcs.entry_after_cost_basis_nano_usd, 647)
        self.assertEqual(bcs.after_cost_profit_nano_usd, 2)
        self.assertEqual(bcs.after_cost_return_ppm, 3_125)
        self.assertEqual(bcs.classification, "SYNTHETIC_ONLY")
        self.assertEqual(bcs.scope, "RESEARCH_ONLY")
        self.assertEqual(bcs.authority_status, "NO_DECISION_EFFECT")

    def test_missing_tick_or_quote_quality_is_unevaluable(self) -> None:
        no_tick = derive_stressed_episode_after_cost(
            carrier_id="LC0",
            entry_long_quote=_quote(bid=90, ask=100, tick=None),
            exit_long_quote=_quote(bid=120, ask=125),
            entry_short_quote=None,
            exit_short_quote=None,
            fees=SideFeesV1.zero(),
            multiplier=10,
            episode_id="LC0-E1",
            fold_id="DEV",
            split="DEVELOPMENT",
            entry_utc_ns=1,
            exit_utc_ns=2,
            input_sha256=_sha("lc0-e1"),
        )
        bad_quality = evaluate_exit_checkpoint(
            carrier_id="LC0",
            entry_after_cost_basis_nano_usd=1_000,
            long_quote=_quote(
                bid=50,
                ask=55,
                quote_quality_pass=False,
            ),
            short_quote=None,
            fees=SideFeesV1.zero(),
            multiplier=10,
            hard_stop_loss_ppm=500_000,
            confirmed_invalidation=True,
            h20_reached=True,
        )
        self.assertEqual(no_tick.status, "UNEVALUABLE")
        self.assertEqual(bad_quality.status, "UNEVALUABLE")

    def test_exit_tick_floor_and_disabled_control_do_not_invent_hard_stop(self) -> None:
        result = evaluate_exit_checkpoint(
            carrier_id="LC0",
            entry_after_cost_basis_nano_usd=100,
            long_quote=_quote(bid=1, ask=2, tick=2),
            short_quote=None,
            fees=SideFeesV1.zero(),
            multiplier=10,
            hard_stop_loss_ppm=None,
            confirmed_invalidation=False,
            h20_reached=False,
        )
        self.assertEqual(result.stressed_exit_value_after_fees_nano_usd, 0)
        self.assertEqual(result.after_cost_loss_ppm, 1_000_000)
        self.assertFalse(result.hard_stop_triggered)
        self.assertEqual(result.trigger_reason, "HOLD")

    def test_exit_priority_is_frozen(self) -> None:
        common = {
            "carrier_id": "LC0",
            "entry_after_cost_basis_nano_usd": 1_000,
            "long_quote": _quote(bid=50, ask=55),
            "short_quote": None,
            "fees": SideFeesV1.zero(),
            "multiplier": 10,
            "hard_stop_loss_ppm": 500_000,
        }
        hard_stop = evaluate_exit_checkpoint(
            **common,
            confirmed_invalidation=True,
            h20_reached=True,
        )
        invalidation = evaluate_exit_checkpoint(
            **{**common, "long_quote": _quote(bid=90, ask=95)},
            confirmed_invalidation=True,
            h20_reached=True,
        )
        h20 = evaluate_exit_checkpoint(
            **{**common, "long_quote": _quote(bid=90, ask=95)},
            confirmed_invalidation=False,
            h20_reached=True,
        )
        hold = evaluate_exit_checkpoint(
            **{**common, "long_quote": _quote(bid=90, ask=95)},
            confirmed_invalidation=False,
            h20_reached=False,
        )
        self.assertEqual(hard_stop.trigger_reason, "HARD_STOP")
        self.assertEqual(invalidation.trigger_reason, "CONFIRMED_INVALIDATION")
        self.assertEqual(h20.trigger_reason, "H20")
        self.assertEqual(hold.trigger_reason, "HOLD")


class R2SyntheticPolicyLedgerTests(unittest.TestCase):
    def test_q1_ledger_backfills_entry_row_and_never_opens_while_occupied(self) -> None:
        rows = (
            SyntheticPolicySessionInstructionV1(
                session_id="S1",
                gate_status="PASS",
                proposed_episode_id="E1",
                proposed_carrier_id="LC0",
            ),
            SyntheticPolicySessionInstructionV1(
                session_id="S2",
                gate_status="PASS",
                proposed_episode_id="E2",
                proposed_carrier_id="BCS0",
            ),
            SyntheticPolicySessionInstructionV1(
                session_id="S3",
                gate_status="NO_ACTION",
                terminal_episode_id="E1",
                terminal_return_ppm=100_000,
            ),
            SyntheticPolicySessionInstructionV1(
                session_id="S4",
                gate_status="PASS",
                proposed_episode_id="E3",
                proposed_carrier_id="BCS0",
            ),
            SyntheticPolicySessionInstructionV1(
                session_id="S5",
                gate_status="NO_ACTION",
                terminal_episode_id="E3",
                terminal_return_ppm=-50_000,
            ),
        )

        result = reduce_synthetic_q1_policy_ledger(rows)

        self.assertEqual(
            tuple(row.synthetic_return_ppm for row in result.rows),
            (100_000, 0, 0, -50_000, 0),
        )
        self.assertEqual(
            tuple(row.row_status for row in result.rows),
            (
                "ENTRY_OPENED",
                "OCCUPIED",
                "OCCUPIED_EXITED",
                "ENTRY_OPENED",
                "OCCUPIED_EXITED",
            ),
        )
        self.assertEqual(result.status, "PASS")
        self.assertIsNone(result.open_episode_id)
        self.assertEqual(result.opened_episode_ids, ("E1", "E3"))
        self.assertEqual(result.completed_episode_ids, ("E1", "E3"))
        self.assertEqual(result.quantity_units, 1)
        self.assertEqual(result.classification, "SYNTHETIC_ONLY")
        self.assertEqual(result.scope, "RESEARCH_ONLY")
        self.assertEqual(result.authority_status, "NO_DECISION_EFFECT")
        self.assertFalse(result.actionable)
        self.assertEqual(result.broker_order_count, 0)

    def test_unevaluable_row_is_not_silently_zero_evidence(self) -> None:
        result = reduce_synthetic_q1_policy_ledger((
            SyntheticPolicySessionInstructionV1(
                session_id="S1",
                gate_status="NOT_EVALUATED",
                row_evidence_status="UNEVALUABLE",
            ),
        ))
        self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result.rows[0].row_status, "UNEVALUABLE")

    def test_unevaluable_occupied_row_cannot_be_swallowed(self) -> None:
        result = reduce_synthetic_q1_policy_ledger((
            SyntheticPolicySessionInstructionV1(
                session_id="S1",
                gate_status="PASS",
                proposed_episode_id="E1",
                proposed_carrier_id="LC0",
            ),
            SyntheticPolicySessionInstructionV1(
                session_id="S2",
                gate_status="NOT_EVALUATED",
                row_evidence_status="UNEVALUABLE",
            ),
        ))
        self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result.rows[1].row_status, "UNEVALUABLE")

    def test_unclosed_episode_is_pending_never_pass(self) -> None:
        result = reduce_synthetic_q1_policy_ledger((
            SyntheticPolicySessionInstructionV1(
                session_id="S1",
                gate_status="PASS",
                proposed_episode_id="E1",
                proposed_carrier_id="LC0",
            ),
        ))
        self.assertEqual(result.status, "PENDING_OPEN_EPISODE")
        self.assertEqual(result.open_episode_id, "E1")


if __name__ == "__main__":
    unittest.main()
