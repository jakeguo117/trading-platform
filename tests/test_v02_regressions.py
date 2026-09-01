from __future__ import annotations

from dataclasses import replace
import inspect
import unittest

from gld_normalizer.errors import NormalizationError
from gld_research_core.bcs import (
    BcsFeeScheduleV1,
    evaluate_bcs_research,
    next_bcs_lifecycle_state,
    price_bcs_exit,
)
from gld_research_core.facts import select_first_complete_option_snapshot
from tests.test_bcs import (
    SHA_A,
    book,
    deltas_for,
    episode_and_management,
    fee_schedule,
    later_option_snapshot,
    option_snapshot,
    reconciliation_snapshot_b,
    select_bcs_short_call,
    standard_market,
)
from tests.test_decision_facts import option_contract, option_snapshot as fact_snapshot


class SnapshotContractRegressionTests(unittest.TestCase):
    def test_occ_expiry_date_must_match_canonical_expiry_instant(self) -> None:
        valid = option_contract()
        one_day_ns = 86_400_000_000_000
        with self.assertRaises(NormalizationError) as raised:
            replace(
                valid,
                expiry_utc_ns=valid.expiry_utc_ns + one_day_ns,
                last_trading_utc_ns=valid.last_trading_utc_ns + one_day_ns,
            )
        self.assertEqual(raised.exception.reason_code, "IDENTITY_UNRESOLVED")

    def test_snapshot_rejects_contract_not_active_at_capture(self) -> None:
        snapshot = fact_snapshot()
        quote = snapshot.option_quotes[0]
        invalid_contract = replace(
            quote.contract,
            activation_utc_ns=snapshot.capture_utc_ns + 1,
        )
        with self.assertRaises(NormalizationError) as raised:
            replace(
                snapshot,
                option_quotes=(replace(quote, contract=invalid_contract),),
            )
        self.assertEqual(raised.exception.reason_code, "IDENTITY_UNRESOLVED")

    def test_selector_rejects_wrong_candidate_type_and_binds_signal_window(self) -> None:
        snapshot = fact_snapshot()
        from tests.test_decision_facts import signal_snapshot

        signal = signal_snapshot()
        with self.assertRaises(NormalizationError):
            select_first_complete_option_snapshot(
                (snapshot, object()),
                signal_snapshot=signal,
            )

        wrong_signal = signal_snapshot(signal_id="different")
        with self.assertRaises(NormalizationError) as raised:
            select_first_complete_option_snapshot(
                (snapshot,),
                signal_snapshot=wrong_signal,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "OPTION_SNAPSHOT_BINDING_MISMATCH",
        )


class BcsRegressionTests(unittest.TestCase):
    def test_exit_can_use_a_later_quote_snapshot_for_the_same_pair(self) -> None:
        entry_snapshot, long_call_selection, deltas = standard_market()
        selection = select_bcs_short_call(
            long_call_selection=long_call_selection,
            snapshot=entry_snapshot,
            deltas=deltas,
        )
        episode, later_snapshot, management = episode_and_management(
            entry_snapshot,
            selection,
            fee_schedule(),
            quantity=1,
        )

        result = price_bcs_exit(
            selection=selection,
            episode=episode,
            management_snapshot=management,
            snapshot=later_snapshot,
            fees=fee_schedule(),
            quantity=1,
        )

        self.assertEqual(result.option_snapshot_sha256, later_snapshot.snapshot_sha256)

        changed_identity = replace(
            later_snapshot.option_quotes[1],
            contract=replace(
                later_snapshot.option_quotes[1].contract,
                expiry_utc_ns=(
                    later_snapshot.option_quotes[1].contract.expiry_utc_ns + 1
                ),
                last_trading_utc_ns=(
                    later_snapshot.option_quotes[1].contract.last_trading_utc_ns
                    + 1
                ),
            ),
        )
        mismatched_snapshot = replace(
            later_snapshot,
            option_quotes=(later_snapshot.option_quotes[0], changed_identity),
        )
        with self.assertRaises(NormalizationError) as raised:
            price_bcs_exit(
                selection=selection,
                episode=episode,
                management_snapshot=management,
                snapshot=mismatched_snapshot,
                fees=fee_schedule(),
                quantity=1,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "BCS_LONG_LEG_BINDING_MISMATCH",
        )

    def test_research_evaluator_has_no_ibkr_authority_boolean(self) -> None:
        self.assertNotIn(
            "ibkr_executable_quote_available",
            inspect.signature(evaluate_bcs_research).parameters,
        )

    def test_reconciliation_cannot_clear_from_string_event_or_arbitrary_hash(self) -> None:
        self.assertNotIn(
            "reconciliation_snapshot_b_sha256",
            inspect.signature(next_bcs_lifecycle_state).parameters,
        )
        with self.assertRaises(NormalizationError):
            next_bcs_lifecycle_state(
                current_state="RECONCILIATION_BLOCKED",
                events=("BROKER_RECONCILED_CLOSED",),
            )

        entry_snapshot, long_call_selection, deltas = standard_market()
        selection = select_bcs_short_call(
            long_call_selection=long_call_selection,
            snapshot=entry_snapshot,
            deltas=deltas,
        )
        episode, _, _ = episode_and_management(
            entry_snapshot,
            selection,
            fee_schedule(),
            quantity=1,
        )
        initial_block = next_bcs_lifecycle_state(
            current_state="PROPOSED",
            events=("POLICY_FULL_EXIT", "CONTRACT_ADJUSTMENT"),
            episode=episode,
            selection=selection,
        )
        receipt = reconciliation_snapshot_b(episode, selection)
        blocked = next_bcs_lifecycle_state(
            current_state="RECONCILIATION_BLOCKED",
            episode=episode,
            selection=selection,
            reconciliation_snapshot_b=receipt,
            previous_transition=initial_block,
        )
        self.assertEqual(blocked.next_state, "RECONCILIATION_BLOCKED")
        self.assertEqual(blocked.latched_exit_reason, "POLICY_FULL_EXIT")
        self.assertEqual(blocked.reconciliation_receipt_sha256, receipt.receipt_sha256)

    def test_adjustment_and_identity_mismatch_block_reconciliation(self) -> None:
        entry_snapshot, long_call_selection, deltas = standard_market()
        selection = select_bcs_short_call(
            long_call_selection=long_call_selection,
            snapshot=entry_snapshot,
            deltas=deltas,
        )
        episode, _, management = episode_and_management(
            entry_snapshot,
            selection,
            fee_schedule(),
            quantity=1,
        )
        managed = next_bcs_lifecycle_state(
            current_state="PROPOSED",
            episode=episode,
            selection=selection,
            management_snapshot=management,
        )
        hold = next_bcs_lifecycle_state(
            current_state="MANAGED",
            episode=episode,
            selection=selection,
            previous_transition=managed,
        )
        for event in ("CONTRACT_ADJUSTMENT", "IDENTITY_MISMATCH"):
            with self.subTest(event=event):
                transition = next_bcs_lifecycle_state(
                    current_state="HOLD",
                    events=(event,),
                    episode=episode,
                    selection=selection,
                    previous_transition=hold,
                )
                self.assertEqual(
                    transition.next_state,
                    "RECONCILIATION_BLOCKED",
                )

    def test_fee_fact_hash_is_derived_from_fees_not_caller_supplied(self) -> None:
        schedule = BcsFeeScheduleV1(
            source_receipt_sha256=SHA_A,
            effective_from_utc_ns=1,
            long_entry_fee_nano_usd_per_contract=100,
            short_entry_fee_nano_usd_per_contract=200,
            long_exit_fee_nano_usd_per_contract=300,
            short_exit_fee_nano_usd_per_contract=400,
        )
        changed = replace(
            schedule,
            short_exit_fee_nano_usd_per_contract=401,
        )
        self.assertNotEqual(schedule.fee_schedule_sha256, changed.fee_schedule_sha256)


if __name__ == "__main__":
    unittest.main()
