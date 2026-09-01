from __future__ import annotations

from dataclasses import replace
import unittest

from benchmarks.crr_p1_contract import load_corpus, load_reference_golden
from gld_research_core.crr_batch import CrrSemanticTerminalV1
from gld_simulation.carriers import (
    CarrierSelectionError,
    price_simulation_carriers,
    select_lc0_and_bcs,
)


class GldSimulationCarrierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = load_corpus("U64")
        cls.terminals = tuple(
            CrrSemanticTerminalV1(**row)
            for row in load_reference_golden("U64")["results"]
        )

    def test_lc0_uses_earliest_expiry_after_h20_safety_and_bcs_reuses_long(
        self,
    ) -> None:
        selection = select_lc0_and_bcs(
            snapshot=self.corpus.snapshot,
            terminals=self.terminals,
            h20_date="2026-09-25",
            minimum_days_after_h20=30,
            lc0_target_delta_ppm=500_000,
            lc0_minimum_delta_ppm=450_000,
            lc0_maximum_delta_ppm=550_000,
        )

        self.assertEqual(selection.minimum_expiry_date, "2026-10-25")
        self.assertIn("261127", selection.long_quote.contract.occ_symbol)
        self.assertEqual(
            selection.long_quote.contract.expiry_utc_ns,
            selection.short_quote.contract.expiry_utc_ns,
        )
        self.assertGreater(
            selection.short_quote.contract.strike_nano_usd,
            selection.long_quote.contract.strike_nano_usd,
        )
        self.assertGreater(selection.long_delta_ppm, selection.short_delta_ppm)
        self.assertGreater(selection.net_delta_ppm, 0)

    def test_carrier_pricing_uses_executable_sides_fees_and_adverse_tick(
        self,
    ) -> None:
        selection = select_lc0_and_bcs(
            snapshot=self.corpus.snapshot,
            terminals=self.terminals,
            h20_date="2026-09-25",
            minimum_days_after_h20=30,
            lc0_target_delta_ppm=500_000,
            lc0_minimum_delta_ppm=450_000,
            lc0_maximum_delta_ppm=550_000,
        )

        lc0, bcs = price_simulation_carriers(
            selection=selection,
            snapshot=self.corpus.snapshot,
            fees=self.corpus.fees,
            long_entry_adverse_ticks=2,
            short_entry_adverse_ticks=3,
            include_exit_fees_in_max_loss=True,
        )

        self.assertEqual((lc0.carrier_id, bcs.carrier_id), ("LC0", "BCS0"))
        self.assertGreater(
            lc0.stressed_entry_cost_nano_usd_per_contract,
            lc0.base_entry_cost_nano_usd_per_contract,
        )
        self.assertGreater(
            bcs.stressed_entry_cost_nano_usd_per_contract,
            bcs.base_entry_cost_nano_usd_per_contract,
        )
        long_book = selection.long_quote.top_of_book
        short_book = selection.short_quote.top_of_book
        multiplier = selection.long_quote.contract.multiplier
        self.assertEqual(
            lc0.stressed_entry_cost_nano_usd_per_contract,
            (
                long_book.ask_nano_usd
                + 2 * long_book.tick_nano_usd
            )
            * multiplier
            + self.corpus.fees.long_entry_fee_nano_usd_per_contract,
        )
        self.assertEqual(
            bcs.stressed_entry_cost_nano_usd_per_contract,
            (
                long_book.ask_nano_usd
                + 2 * long_book.tick_nano_usd
                - max(
                    0,
                    short_book.bid_nano_usd
                    - 3 * short_book.tick_nano_usd,
                )
            )
            * multiplier
            + self.corpus.fees.long_entry_fee_nano_usd_per_contract
            + self.corpus.fees.short_entry_fee_nano_usd_per_contract,
        )
        self.assertLess(
            bcs.stressed_entry_cost_nano_usd_per_contract,
            bcs.gross_expiry_width_nano_usd_per_contract,
        )
        self.assertEqual(
            bcs.sizing_max_loss_nano_usd_per_contract,
            bcs.stressed_entry_cost_nano_usd_per_contract
            + self.corpus.fees.long_exit_fee_nano_usd_per_contract
            + self.corpus.fees.short_exit_fee_nano_usd_per_contract,
        )
        self.assertEqual(
            lc0.sizing_max_loss_nano_usd_per_contract,
            lc0.stressed_entry_cost_nano_usd_per_contract
            + self.corpus.fees.long_exit_fee_nano_usd_per_contract,
        )
        self.assertEqual(
            lc0.sizing_planned_loss_nano_usd_per_contract,
            lc0.sizing_max_loss_nano_usd_per_contract,
        )
        self.assertEqual(
            bcs.sizing_planned_loss_nano_usd_per_contract,
            bcs.sizing_max_loss_nano_usd_per_contract,
        )
        self.assertEqual(
            bcs.theoretical_expiry_max_profit_nano_usd_per_contract,
            bcs.gross_expiry_width_nano_usd_per_contract
            - bcs.base_entry_cost_nano_usd_per_contract
            - bcs.planned_exit_fees_nano_usd_per_contract,
        )
        self.assertEqual(
            lc0.execution_assumptions,
            {
                "include_exit_fees_in_max_loss": True,
                "long_entry_adverse_ticks": 2,
                "short_entry_adverse_ticks": 3,
            },
        )
        self.assertEqual(
            bcs.as_dict()["fee_components_nano_usd_per_contract"],
            {
                "long_entry": self.corpus.fees.long_entry_fee_nano_usd_per_contract,
                "long_exit": self.corpus.fees.long_exit_fee_nano_usd_per_contract,
                "short_entry": self.corpus.fees.short_entry_fee_nano_usd_per_contract,
                "short_exit": self.corpus.fees.short_exit_fee_nano_usd_per_contract,
            },
        )

    def test_carrier_pricing_requires_explicit_conservative_execution_policy(
        self,
    ) -> None:
        selection = select_lc0_and_bcs(
            snapshot=self.corpus.snapshot,
            terminals=self.terminals,
            h20_date="2026-09-25",
            minimum_days_after_h20=30,
            lc0_target_delta_ppm=500_000,
            lc0_minimum_delta_ppm=450_000,
            lc0_maximum_delta_ppm=550_000,
        )

        for changes in (
            {"long_entry_adverse_ticks": True},
            {"short_entry_adverse_ticks": -1},
            {"include_exit_fees_in_max_loss": False},
        ):
            assumptions: dict[str, object] = {
                "long_entry_adverse_ticks": 1,
                "short_entry_adverse_ticks": 1,
                "include_exit_fees_in_max_loss": True,
            }
            assumptions.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(CarrierSelectionError) as raised:
                    price_simulation_carriers(
                        selection=selection,
                        snapshot=self.corpus.snapshot,
                        fees=self.corpus.fees,
                        **assumptions,  # type: ignore[arg-type]
                    )
                self.assertEqual(
                    raised.exception.reason_code,
                    "SIM_CARRIER_EXECUTION_ASSUMPTIONS_INVALID",
                )

    def test_lc0_coarse_fine_winner_mismatch_has_no_second_best_fallback(
        self,
    ) -> None:
        modified = []
        for terminal in self.terminals:
            if terminal.contract_id == "GLD   261127C00200000":
                terminal = replace(
                    terminal,
                    coarse_delta_ppm=499_800,
                    fine_delta_ppm=500_200,
                    delta_ppm=500_200,
                )
            elif terminal.contract_id == "GLD   261127C00204000":
                terminal = replace(
                    terminal,
                    coarse_delta_ppm=500_100,
                    fine_delta_ppm=499_700,
                    delta_ppm=499_700,
                )
            modified.append(terminal)

        with self.assertRaises(CarrierSelectionError) as raised:
            select_lc0_and_bcs(
                snapshot=self.corpus.snapshot,
                terminals=tuple(modified),
                h20_date="2026-09-25",
                minimum_days_after_h20=30,
                lc0_target_delta_ppm=500_000,
                lc0_minimum_delta_ppm=450_000,
                lc0_maximum_delta_ppm=550_000,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "SIM_LC0_SELECTOR_MODEL_INSTABILITY",
        )

    def test_delta_notional_uses_one_conservative_ceil_for_nonintegral_value(
        self,
    ) -> None:
        selection = select_lc0_and_bcs(
            snapshot=self.corpus.snapshot,
            terminals=self.terminals,
            h20_date="2026-09-25",
            minimum_days_after_h20=30,
            lc0_target_delta_ppm=500_000,
            lc0_minimum_delta_ppm=450_000,
            lc0_maximum_delta_ppm=550_000,
        )
        top = self.corpus.snapshot.underlying_top
        nonintegral_snapshot = replace(
            self.corpus.snapshot,
            underlying_top=replace(
                top,
                bid_nano_usd=top.bid_nano_usd + 1,
                ask_nano_usd=top.ask_nano_usd + 1,
            ),
        )
        lc0, bcs = price_simulation_carriers(
            selection=selection,
            snapshot=nonintegral_snapshot,
            fees=self.corpus.fees,
            long_entry_adverse_ticks=1,
            short_entry_adverse_ticks=1,
            include_exit_fees_in_max_loss=True,
        )
        spot = (
            nonintegral_snapshot.underlying_top.bid_nano_usd
            + nonintegral_snapshot.underlying_top.ask_nano_usd
        ) // 2
        for economics, delta_ppm in (
            (lc0, selection.long_delta_ppm),
            (bcs, selection.net_delta_ppm),
        ):
            numerator = spot * 100 * delta_ppm
            self.assertNotEqual(numerator % 1_000_000, 0)
            self.assertEqual(
                economics.delta_notional_nano_usd_per_contract,
                (numerator + 999_999) // 1_000_000,
            )


if __name__ == "__main__":
    unittest.main()
