from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import unittest

from gld_normalizer.errors import NormalizationError
from gld_simulation.contracts import SizingInputV1
from gld_simulation.risk import size_position


NANO_USD = 1_000_000_000


def _sizing_input(**changes: object) -> SizingInputV1:
    values: dict[str, object] = {
        "initial_bankroll_nano_usd": 100_000 * NANO_USD,
        "cumulative_realized_positive_nano_usd": 20_000 * NANO_USD,
        "cumulative_realized_loss_nano_usd": 5_000 * NANO_USD,
        "unrealized_profit_nano_usd": 50_000 * NANO_USD,
        "current_nlv_nano_usd": 105_000 * NANO_USD,
        "peak_nlv_nano_usd": 110_000 * NANO_USD,
        "sticky_drawdown_lock_active": False,
        "settled_cash_nano_usd": 80_000 * NANO_USD,
        "robust_full_kelly_ppm": 200_000,
        "max_loss_nano_usd_per_contract": 2_000 * NANO_USD,
        "planned_loss_nano_usd_per_contract": 2_000 * NANO_USD,
        "entry_cash_nano_usd_per_contract": 2_000 * NANO_USD,
        "absolute_delta_ppm_per_contract": 500_000,
        "underlying_spot_nano_usd": 200 * NANO_USD,
        "contract_multiplier": 100,
        "current_gld_equivalent_delta_notional_nano_usd": 0,
        "max_loss_capacity_contracts": 6,
        "planned_loss_capacity_contracts": 7,
        "liquidity_capacity_contracts": 8,
    }
    values.update(changes)
    return SizingInputV1(**values)  # type: ignore[arg-type]


class RiskSizingTests(unittest.TestCase):
    def test_sizing_uses_half_kelly_drawdown_and_minimum_capacity(self) -> None:
        result = size_position(_sizing_input())

        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.reason_code, "SIZING_PASS")
        self.assertEqual(result.eligible_bankroll_nano_usd, 105_000 * NANO_USD)
        self.assertEqual(result.half_kelly_ppm, 100_000)
        self.assertEqual(result.drawdown_ppm, 45_454)
        self.assertEqual(result.drawdown_factor_ppm, 1_000_000)
        self.assertEqual(result.kelly_drawdown_capacity_contracts, 5)
        self.assertEqual(result.delta_capacity_contracts, 15)
        self.assertEqual(result.cash_capacity_contracts, 34)
        self.assertEqual(result.quantity, 5)
        self.assertEqual(result.binding_capacity, "KELLY_DRAWDOWN")
        self.assertEqual(result.as_dict()["quantity"], 5)

    def test_unrealized_profit_never_expands_eligible_bankroll(self) -> None:
        low = size_position(_sizing_input(unrealized_profit_nano_usd=0))
        high = size_position(
            _sizing_input(unrealized_profit_nano_usd=999_999 * NANO_USD)
        )
        self.assertEqual(
            low.eligible_bankroll_nano_usd,
            high.eligible_bankroll_nano_usd,
        )
        self.assertEqual(low.quantity, high.quantity)

    def test_drawdown_bands_apply_exact_integer_boundaries(self) -> None:
        cases = (
            (95_000, 1_000_000, "PASS"),
            (94_999, 800_000, "PASS"),
            (90_000, 800_000, "PASS"),
            (89_999, 400_000, "PASS"),
            (85_001, 400_000, "PASS"),
            (85_000, 0, "NO_ACTION"),
        )
        for nlv_usd, factor_ppm, status in cases:
            with self.subTest(nlv_usd=nlv_usd):
                result = size_position(
                    _sizing_input(
                        current_nlv_nano_usd=nlv_usd * NANO_USD,
                        peak_nlv_nano_usd=100_000 * NANO_USD,
                    )
                )
                self.assertEqual(result.drawdown_factor_ppm, factor_ppm)
                self.assertEqual(result.status, status)
                if status == "NO_ACTION":
                    self.assertEqual(result.quantity, 0)
                    self.assertEqual(
                        result.reason_code,
                        "SIZING_DRAWDOWN_BLOCKS_NEW_ENTRY",
                    )

    def test_cash_reserve_and_delta_caps_are_post_trade_limits(self) -> None:
        cash_bound = size_position(
            _sizing_input(
                settled_cash_nano_usd=14_500 * NANO_USD,
                current_nlv_nano_usd=100_000 * NANO_USD,
                peak_nlv_nano_usd=100_000 * NANO_USD,
                entry_cash_nano_usd_per_contract=2_000 * NANO_USD,
            )
        )
        self.assertEqual(cash_bound.cash_capacity_contracts, 2)
        self.assertEqual(cash_bound.quantity, 2)
        self.assertEqual(cash_bound.binding_capacity, "SETTLED_CASH")

        delta_bound = size_position(
            _sizing_input(
                absolute_delta_ppm_per_contract=1_000_000,
                underlying_spot_nano_usd=50_000 * NANO_USD,
                contract_multiplier=100,
            )
        )
        self.assertEqual(delta_bound.delta_capacity_contracts, 0)
        self.assertEqual(delta_bound.quantity, 0)
        self.assertEqual(delta_bound.status, "NO_ACTION")
        self.assertEqual(delta_bound.binding_capacity, "DELTA_NOTIONAL")

        existing_exposure_bound = size_position(
            _sizing_input(
                current_gld_equivalent_delta_notional_nano_usd=(
                    157_000 * NANO_USD
                ),
            )
        )
        self.assertEqual(existing_exposure_bound.delta_capacity_contracts, 0)
        self.assertEqual(existing_exposure_bound.quantity, 0)
        self.assertEqual(existing_exposure_bound.status, "NO_ACTION")

    def test_complete_zero_capacity_is_no_action(self) -> None:
        result = size_position(_sizing_input(liquidity_capacity_contracts=0))
        self.assertEqual(result.status, "NO_ACTION")
        self.assertEqual(result.reason_code, "SIZING_COMPLETE_ZERO_QUANTITY")
        self.assertEqual(result.quantity, 0)
        self.assertEqual(result.binding_capacity, "LIQUIDITY")

    def test_missing_fact_is_no_decision_and_has_no_quantity(self) -> None:
        result = size_position(
            _sizing_input(robust_full_kelly_ppm=None)
        )
        self.assertEqual(result.status, "NO_DECISION")
        self.assertEqual(result.reason_code, "SIZING_FACT_MISSING")
        self.assertIsNone(result.quantity)
        self.assertEqual(result.missing_fields, ("robust_full_kelly_ppm",))

    def test_sticky_drawdown_lock_missing_is_no_decision(self) -> None:
        result = size_position(
            _sizing_input(sticky_drawdown_lock_active=None)
        )

        self.assertEqual(result.status, "NO_DECISION")
        self.assertEqual(result.reason_code, "SIZING_FACT_MISSING")
        self.assertIsNone(result.quantity)
        self.assertEqual(
            result.missing_fields,
            ("sticky_drawdown_lock_active",),
        )

    def test_sticky_drawdown_lock_never_auto_unlocks_after_nlv_recovers(self) -> None:
        result = size_position(
            _sizing_input(
                sticky_drawdown_lock_active=True,
                current_nlv_nano_usd=110_000 * NANO_USD,
                peak_nlv_nano_usd=110_000 * NANO_USD,
            )
        )

        self.assertEqual(result.status, "NO_ACTION")
        self.assertEqual(result.reason_code, "SIZING_STICKY_DRAWDOWN_LOCK_ACTIVE")
        self.assertEqual(result.quantity, 0)
        self.assertEqual(result.drawdown_ppm, 0)
        self.assertEqual(result.binding_constraints, ("STICKY_DRAWDOWN_LOCK",))

    def test_disaster_drawdown_without_sticky_latch_is_conflicting_fact(self) -> None:
        result = size_position(
            _sizing_input(
                sticky_drawdown_lock_active=False,
                current_nlv_nano_usd=70_000 * NANO_USD,
                peak_nlv_nano_usd=100_000 * NANO_USD,
            )
        )

        self.assertEqual(result.status, "NO_DECISION")
        self.assertEqual(
            result.reason_code,
            "SIZING_STICKY_DRAWDOWN_LOCK_CONFLICT",
        )
        self.assertIsNone(result.quantity)

    def test_planned_loss_must_equal_max_loss_without_a_fixed_stop(self) -> None:
        result = size_position(
            _sizing_input(planned_loss_nano_usd_per_contract=1_000 * NANO_USD)
        )
        self.assertEqual(result.status, "NO_DECISION")
        self.assertEqual(
            result.reason_code,
            "SIZING_PLANNED_LOSS_MUST_EQUAL_MAX_LOSS",
        )
        self.assertIsNone(result.quantity)

    def test_exact_integer_and_frozen_contracts_reject_ambiguous_values(self) -> None:
        with self.assertRaises(NormalizationError) as raised:
            _sizing_input(robust_full_kelly_ppm=0.2)
        self.assertEqual(raised.exception.reason_code, "SIZING_INPUT_INVALID")

        with self.assertRaises(NormalizationError) as raised:
            _sizing_input(sticky_drawdown_lock_active=1)
        self.assertEqual(raised.exception.reason_code, "SIZING_INPUT_INVALID")

        with self.assertRaises(FrozenInstanceError):
            _sizing_input().contract_multiplier = 1  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
