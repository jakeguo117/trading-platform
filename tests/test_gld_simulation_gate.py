from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import unittest

from gld_normalizer.errors import NormalizationError
from gld_simulation.contracts import (
    DailyGateBarV1,
    GateAInputV1,
    MinuteGateBarV1,
)
from gld_simulation.gate_a import GATE_A_RULE_VERSION, evaluate_gate_a


CUTOFF_NS = 1_800_000_000_000_000_000
NANO_USD = 1_000_000_000


def _daily_bars() -> tuple[DailyGateBarV1, ...]:
    bars: list[DailyGateBarV1] = []
    for ordinal in range(21):
        bars.append(
            DailyGateBarV1(
                session_ordinal=ordinal + 1,
                high_nano_usd=(180 + ordinal) * NANO_USD,
                close_nano_usd=(181 + ordinal) * NANO_USD,
                sma50_nano_usd=(160 + ordinal) * NANO_USD,
                sma200_nano_usd=150 * NANO_USD,
                sma50_sum_nano_usd=(160 + ordinal) * NANO_USD * 50,
                sma200_sum_nano_usd=150 * NANO_USD * 200,
                complete=True,
                max_event_utc_ns=CUTOFF_NS - 10_000 + ordinal,
                max_receive_utc_ns=CUTOFF_NS - 9_000 + ordinal,
            )
        )
    return tuple(bars)


def _minute_bars(*, above: int = 15) -> tuple[MinuteGateBarV1, ...]:
    breakout = 200 * NANO_USD
    return tuple(
        MinuteGateBarV1(
            minute_ending_ordinal=630 + ordinal,
            close_nano_usd=(breakout + NANO_USD if ordinal < above else breakout),
            complete=True,
            max_event_utc_ns=CUTOFF_NS - 5_000 + ordinal,
            max_receive_utc_ns=CUTOFF_NS - 4_000 + ordinal,
        )
        for ordinal in range(15)
    )


def _gate_input(**changes: object) -> GateAInputV1:
    values: dict[str, object] = {
        "cutoff_utc_ns": CUTOFF_NS,
        "daily_bars": _daily_bars(),
        "minute_bars": _minute_bars(),
    }
    values.update(changes)
    return GateAInputV1(**values)  # type: ignore[arg-type]


class GateAContractTests(unittest.TestCase):
    def test_pass_is_derived_from_raw_complete_bars(self) -> None:
        result = evaluate_gate_a(_gate_input())

        self.assertEqual(result.rule_version, GATE_A_RULE_VERSION)
        self.assertEqual(result.state, "PASS")
        self.assertEqual(result.reason_code, "GATE_A_PASS")
        self.assertEqual(result.breakout_nano_usd, 200 * NANO_USD)
        self.assertEqual(result.minute_1044_close_nano_usd, 201 * NANO_USD)
        self.assertEqual(result.range_hold_above_count, 15)
        self.assertEqual(result.range_hold_required_count, 10)
        self.assertEqual(result.as_dict()["state"], "PASS")
        self.assertNotIn("signal_state", _gate_input().as_dict())

    def test_complete_but_false_inputs_are_fail_not_unknown(self) -> None:
        latest = replace(
            _daily_bars()[-1],
            close_nano_usd=_daily_bars()[-1].sma50_nano_usd,
        )
        result = evaluate_gate_a(
            _gate_input(daily_bars=(*_daily_bars()[:-1], latest))
        )
        self.assertEqual(result.state, "FAIL")
        self.assertEqual(
            result.reason_code,
            "GATE_A_PRIOR_CLOSE_NOT_ABOVE_SMA50",
        )

        hold_minutes = _minute_bars(above=8)
        hold_fail = evaluate_gate_a(
            _gate_input(
                minute_bars=(
                    *hold_minutes[:-1],
                    replace(
                        hold_minutes[-1],
                        close_nano_usd=201 * NANO_USD,
                    ),
                )
            )
        )
        self.assertEqual(hold_fail.state, "FAIL")
        self.assertEqual(
            hold_fail.reason_code,
            "GATE_A_RANGE_HOLD_COUNT_BELOW_MINIMUM",
        )
        self.assertEqual(hold_fail.range_hold_above_count, 9)

    def test_missing_incomplete_and_future_facts_are_not_evaluable(self) -> None:
        cases = (
            (
                _gate_input(daily_bars=_daily_bars()[1:]),
                "GATE_A_DAILY_LOOKBACK_MISSING",
            ),
            (
                _gate_input(
                    minute_bars=(
                        *_minute_bars()[:-1],
                        replace(_minute_bars()[-1], complete=False),
                    )
                ),
                "GATE_A_MINUTE_DATA_INCOMPLETE",
            ),
            (
                _gate_input(
                    minute_bars=(
                        *_minute_bars(),
                        MinuteGateBarV1(
                            minute_ending_ordinal=645,
                            close_nano_usd=202 * NANO_USD,
                            complete=True,
                            max_event_utc_ns=CUTOFF_NS + 1,
                            max_receive_utc_ns=CUTOFF_NS + 2,
                        ),
                    )
                ),
                "GATE_A_CAUSALITY_VIOLATION",
            ),
        )
        for gate_input, reason in cases:
            with self.subTest(reason=reason):
                result = evaluate_gate_a(gate_input)
                self.assertEqual(result.state, "NOT_EVALUABLE")
                self.assertEqual(result.reason_code, reason)

    def test_exact_integer_and_frozen_contracts_reject_ambiguous_values(self) -> None:
        with self.assertRaises(NormalizationError) as raised:
            replace(_daily_bars()[0], high_nano_usd=200.0)  # type: ignore[arg-type]
        self.assertEqual(raised.exception.reason_code, "GATE_DAILY_BAR_INVALID")

        with self.assertRaises(NormalizationError):
            replace(_gate_input(), cutoff_utc_ns=True)  # type: ignore[arg-type]

        with self.assertRaises(FrozenInstanceError):
            _daily_bars()[0].complete = False  # type: ignore[misc]

    def test_input_order_is_canonical_and_duplicates_fail_closed(self) -> None:
        reversed_result = evaluate_gate_a(
            _gate_input(
                daily_bars=tuple(reversed(_daily_bars())),
                minute_bars=tuple(reversed(_minute_bars())),
            )
        )
        self.assertEqual(reversed_result.state, "PASS")

        duplicate = evaluate_gate_a(
            _gate_input(daily_bars=(*_daily_bars(), _daily_bars()[-1]))
        )
        self.assertEqual(duplicate.state, "NOT_EVALUABLE")
        self.assertEqual(duplicate.reason_code, "GATE_A_DAILY_ORDINAL_DUPLICATE")

        with_gap = tuple(
            replace(item, session_ordinal=item.session_ordinal + 1)
            if item.session_ordinal >= 11
            else item
            for item in _daily_bars()
        )
        missing_session = evaluate_gate_a(_gate_input(daily_bars=with_gap))
        self.assertEqual(missing_session.state, "NOT_EVALUABLE")
        self.assertEqual(
            missing_session.reason_code,
            "GATE_A_DAILY_LOOKBACK_MISSING",
        )


if __name__ == "__main__":
    unittest.main()
