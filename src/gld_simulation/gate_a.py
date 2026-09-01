"""Pure Gate A evaluation for the isolated synthetic GLD workflow."""

from __future__ import annotations

from .contracts import (
    GATE_A_RULE_VERSION,
    DailyGateBarV1,
    GateAInputV1,
    GateAResultV1,
    MinuteGateBarV1,
)


_MINUTE_1030 = 10 * 60 + 30
_MINUTE_1044 = 10 * 60 + 44
_DAILY_LOOKBACK = 21
_BREAKOUT_SESSIONS = 20
_RANGE_HOLD_REQUIRED = 10


def _max_timestamp(
    daily_bars: tuple[DailyGateBarV1, ...],
    minute_bars: tuple[MinuteGateBarV1, ...],
    *,
    receive: bool,
) -> int | None:
    values = [
        (
            item.max_receive_utc_ns
            if receive
            else item.max_event_utc_ns
        )
        for item in (*daily_bars, *minute_bars)
    ]
    return max(values) if values else None


def _result(
    source: GateAInputV1,
    *,
    state: str,
    reason_code: str,
    prior_close_nano_usd: int | None = None,
    current_sma50_nano_usd: int | None = None,
    current_sma200_nano_usd: int | None = None,
    sma50_20_sessions_ago_nano_usd: int | None = None,
    breakout_nano_usd: int | None = None,
    minute_1044_close_nano_usd: int | None = None,
    range_hold_above_count: int | None = None,
    prior_close_gt_sma50: bool | None = None,
    sma50_gt_sma200: bool | None = None,
    sma50_rising_20_sessions: bool | None = None,
    minute_1044_close_gt_breakout: bool | None = None,
    range_hold_count_met: bool | None = None,
) -> GateAResultV1:
    return GateAResultV1(
        rule_version=GATE_A_RULE_VERSION,
        state=state,
        reason_code=reason_code,
        input_fact_sha256=source.input_fact_sha256,
        max_event_utc_ns=_max_timestamp(
            source.daily_bars,
            source.minute_bars,
            receive=False,
        ),
        max_receive_utc_ns=_max_timestamp(
            source.daily_bars,
            source.minute_bars,
            receive=True,
        ),
        prior_close_nano_usd=prior_close_nano_usd,
        current_sma50_nano_usd=current_sma50_nano_usd,
        current_sma200_nano_usd=current_sma200_nano_usd,
        sma50_20_sessions_ago_nano_usd=sma50_20_sessions_ago_nano_usd,
        breakout_nano_usd=breakout_nano_usd,
        minute_1044_close_nano_usd=minute_1044_close_nano_usd,
        range_hold_above_count=range_hold_above_count,
        range_hold_required_count=_RANGE_HOLD_REQUIRED,
        prior_close_gt_sma50=prior_close_gt_sma50,
        sma50_gt_sma200=sma50_gt_sma200,
        sma50_rising_20_sessions=sma50_rising_20_sessions,
        minute_1044_close_gt_breakout=minute_1044_close_gt_breakout,
        range_hold_count_met=range_hold_count_met,
    )


def evaluate_gate_a(source: GateAInputV1) -> GateAResultV1:
    """Evaluate ``SIM_A1_RANGE_HOLD_V1`` from facts, never a supplied state."""

    daily_bars = tuple(
        sorted(source.daily_bars, key=lambda item: item.session_ordinal)
    )
    minute_bars = tuple(
        sorted(source.minute_bars, key=lambda item: item.minute_ending_ordinal)
    )

    if any(
        item.max_event_utc_ns >= source.cutoff_utc_ns
        or item.max_receive_utc_ns >= source.cutoff_utc_ns
        for item in (*daily_bars, *minute_bars)
    ) or any(
        item.minute_ending_ordinal > _MINUTE_1044 for item in minute_bars
    ):
        return _result(
            source,
            state="NOT_EVALUABLE",
            reason_code="GATE_A_CAUSALITY_VIOLATION",
        )

    daily_ordinals = tuple(item.session_ordinal for item in daily_bars)
    if len(set(daily_ordinals)) != len(daily_ordinals):
        return _result(
            source,
            state="NOT_EVALUABLE",
            reason_code="GATE_A_DAILY_ORDINAL_DUPLICATE",
        )
    minute_ordinals = tuple(item.minute_ending_ordinal for item in minute_bars)
    if len(set(minute_ordinals)) != len(minute_ordinals):
        return _result(
            source,
            state="NOT_EVALUABLE",
            reason_code="GATE_A_MINUTE_ORDINAL_DUPLICATE",
        )
    if len(daily_bars) < _DAILY_LOOKBACK:
        return _result(
            source,
            state="NOT_EVALUABLE",
            reason_code="GATE_A_DAILY_LOOKBACK_MISSING",
        )

    selected_daily = daily_bars[-_DAILY_LOOKBACK:]
    if any(
        current.session_ordinal != previous.session_ordinal + 1
        for previous, current in zip(selected_daily, selected_daily[1:])
    ):
        return _result(
            source,
            state="NOT_EVALUABLE",
            reason_code="GATE_A_DAILY_LOOKBACK_MISSING",
        )
    if not all(item.complete for item in selected_daily):
        return _result(
            source,
            state="NOT_EVALUABLE",
            reason_code="GATE_A_DAILY_DATA_INCOMPLETE",
        )
    if any(
        item.high_nano_usd is None
        or item.close_nano_usd is None
        or item.sma50_nano_usd is None
        or item.sma200_nano_usd is None
        or item.sma50_sum_nano_usd is None
        or item.sma200_sum_nano_usd is None
        for item in selected_daily
    ):
        return _result(
            source,
            state="NOT_EVALUABLE",
            reason_code="GATE_A_DAILY_FACT_MISSING",
        )

    minute_by_ordinal = {item.minute_ending_ordinal: item for item in minute_bars}
    required_minute_ordinals = tuple(range(_MINUTE_1030, _MINUTE_1044 + 1))
    if any(ordinal not in minute_by_ordinal for ordinal in required_minute_ordinals):
        return _result(
            source,
            state="NOT_EVALUABLE",
            reason_code="GATE_A_MINUTE_LOOKBACK_MISSING",
        )
    selected_minutes = tuple(
        minute_by_ordinal[ordinal] for ordinal in required_minute_ordinals
    )
    if not all(item.complete for item in selected_minutes):
        return _result(
            source,
            state="NOT_EVALUABLE",
            reason_code="GATE_A_MINUTE_DATA_INCOMPLETE",
        )
    if any(item.close_nano_usd is None for item in selected_minutes):
        return _result(
            source,
            state="NOT_EVALUABLE",
            reason_code="GATE_A_MINUTE_FACT_MISSING",
        )

    latest = selected_daily[-1]
    twenty_sessions_ago = selected_daily[0]
    breakout_highs = tuple(
        item.high_nano_usd for item in selected_daily[-_BREAKOUT_SESSIONS:]
    )
    if any(value is None for value in breakout_highs):
        return _result(
            source,
            state="NOT_EVALUABLE",
            reason_code="GATE_A_DAILY_FACT_MISSING",
        )

    prior_close = latest.close_nano_usd
    current_sma50 = latest.sma50_nano_usd
    current_sma200 = latest.sma200_nano_usd
    old_sma50 = twenty_sessions_ago.sma50_nano_usd
    current_sma50_sum = latest.sma50_sum_nano_usd
    current_sma200_sum = latest.sma200_sum_nano_usd
    old_sma50_sum = twenty_sessions_ago.sma50_sum_nano_usd
    minute_1044_close = selected_minutes[-1].close_nano_usd
    assert prior_close is not None
    assert current_sma50 is not None
    assert current_sma200 is not None
    assert old_sma50 is not None
    assert current_sma50_sum is not None
    assert current_sma200_sum is not None
    assert old_sma50_sum is not None
    assert minute_1044_close is not None
    assert all(value is not None for value in breakout_highs)
    breakout = max(value for value in breakout_highs if value is not None)
    minute_closes = tuple(item.close_nano_usd for item in selected_minutes)
    assert all(value is not None for value in minute_closes)
    above_count = sum(
        1 for value in minute_closes if value is not None and value > breakout
    )

    predicate_values = {
        "prior_close_gt_sma50": prior_close * 50 > current_sma50_sum,
        "sma50_gt_sma200": current_sma50_sum * 4 > current_sma200_sum,
        "sma50_rising_20_sessions": current_sma50_sum > old_sma50_sum,
        "minute_1044_close_gt_breakout": minute_1044_close > breakout,
        "range_hold_count_met": above_count >= _RANGE_HOLD_REQUIRED,
    }
    reason_by_predicate = (
        ("prior_close_gt_sma50", "GATE_A_PRIOR_CLOSE_NOT_ABOVE_SMA50"),
        ("sma50_gt_sma200", "GATE_A_SMA50_NOT_ABOVE_SMA200"),
        ("sma50_rising_20_sessions", "GATE_A_SMA50_NOT_RISING_20_SESSIONS"),
        (
            "minute_1044_close_gt_breakout",
            "GATE_A_1044_CLOSE_NOT_ABOVE_BREAKOUT",
        ),
        (
            "range_hold_count_met",
            "GATE_A_RANGE_HOLD_COUNT_BELOW_MINIMUM",
        ),
    )
    failed_reason = next(
        (
            reason
            for predicate, reason in reason_by_predicate
            if not predicate_values[predicate]
        ),
        None,
    )
    return _result(
        source,
        state="PASS" if failed_reason is None else "FAIL",
        reason_code=failed_reason or "GATE_A_PASS",
        prior_close_nano_usd=prior_close,
        current_sma50_nano_usd=current_sma50,
        current_sma200_nano_usd=current_sma200,
        sma50_20_sessions_ago_nano_usd=old_sma50,
        breakout_nano_usd=breakout,
        minute_1044_close_nano_usd=minute_1044_close,
        range_hold_above_count=above_count,
        prior_close_gt_sma50=predicate_values["prior_close_gt_sma50"],
        sma50_gt_sma200=predicate_values["sma50_gt_sma200"],
        sma50_rising_20_sessions=predicate_values["sma50_rising_20_sessions"],
        minute_1044_close_gt_breakout=predicate_values[
            "minute_1044_close_gt_breakout"
        ],
        range_hold_count_met=predicate_values["range_hold_count_met"],
    )


__all__ = ["GATE_A_RULE_VERSION", "evaluate_gate_a"]
