"""Deterministic local technical indicators for GLD management Research F0."""

from __future__ import annotations

from hashlib import sha256

from .canonical import canonical_json_bytes, canonical_json_sha256
from .contracts import DailyBarF0, FeatureReceiptF0, LocalIndicatorsF0
from .errors import ManagementResearchError
from .math import PPM, clamp, round_half_even_div


def _formula_catalog_document() -> dict[str, object]:
    return {
        "schema_version": "GLD_MANAGEMENT_FORMULA_CATALOG_F0_V1",
        "rounding": "SMA50_INTEGER_FLOOR_OTHER_DIVISIONS_SIGNED_HALF_EVEN",
        "price_unit": "NANO_USD",
        "ratio_scale": "PPM",
        "formulas": [
        {
            "feature_id": "SMA50",
            "formula_id": "GLD_SMA_CLOSE_INTEGER_FLOOR",
            "formula_version": "1",
            "window": 50,
            "seed": "NONE",
        },
        {
            "feature_id": "ATR14",
            "formula_id": "GLD_ATR14_WILDER_INTEGER_F0_V1",
            "formula_version": "1",
            "window": 14,
            "seed": "FIRST_14_TRUE_RANGE_MEAN",
        },
        {
            "feature_id": "RSI14",
            "formula_id": "GLD_RSI14_WILDER_INTEGER_F0_V1",
            "formula_version": "1",
            "window": 14,
            "seed": "FIRST_14_GAIN_LOSS_MEANS",
            "flat_value_ppm": 500_000,
        },
        {
            "feature_id": "MACD_12_26_9",
            "formula_id": "GLD_MACD_12_26_9_SMA_SEED_INTEGER_F0_V1",
            "formula_version": "1",
            "periods": [12, 26, 9],
            "seed": "EACH_EMA_FIRST_PERIOD_SMA_SIGNAL_FIRST_9_MACD_MEAN",
        },
        {
            "feature_id": "DMI_ADX14",
            "formula_id": "GLD_DMI_ADX14_WILDER_INTEGER_F0_V1",
            "formula_version": "1",
            "window": 14,
            "seed": "FIRST_14_DIRECTIONAL_OBSERVATIONS_MEAN",
        },
        {
            "feature_id": "VOLUME_MEDIAN20",
            "formula_id": "GLD_PRIOR_NORMAL_VOLUME_MEDIAN20_F0_V1",
            "formula_version": "1",
            "window": 20,
            "current_session_excluded": True,
        },
        ],
    }


_FORMULA_CATALOG_CANONICAL_BYTES = canonical_json_bytes(
    _formula_catalog_document()
)
FORMULA_CATALOG_SHA256 = sha256(_FORMULA_CATALOG_CANONICAL_BYTES).hexdigest()


def formula_catalog_document_f0() -> dict[str, object]:
    """Return a detached machine-readable copy of the frozen formula catalog."""

    document = _formula_catalog_document()
    if canonical_json_bytes(document) != _FORMULA_CATALOG_CANONICAL_BYTES:
        raise ManagementResearchError("FORMULA_RUNTIME_IDENTITY_MISMATCH")
    return document


def _mean(values: list[int]) -> int:
    if not values:
        raise ManagementResearchError("INDICATOR_WINDOW_EMPTY")
    return round_half_even_div(sum(values), len(values))


def _true_ranges(bars: tuple[DailyBarF0, ...]) -> list[int]:
    result = [bars[0].high_nano_usd - bars[0].low_nano_usd]
    for previous, current in zip(bars, bars[1:]):
        result.append(
            max(
                current.high_nano_usd - current.low_nano_usd,
                abs(current.high_nano_usd - previous.close_nano_usd),
                abs(current.low_nano_usd - previous.close_nano_usd),
            )
        )
    return result


def _wilder(values: list[int], period: int) -> int:
    if len(values) < period:
        raise ManagementResearchError("INDICATOR_WINDOW_INSUFFICIENT")
    current = _mean(values[:period])
    for value in values[period:]:
        current = round_half_even_div(current * (period - 1) + value, period)
    return current


def _ema_series(values: list[int], period: int) -> list[int | None]:
    if len(values) < period:
        raise ManagementResearchError("INDICATOR_WINDOW_INSUFFICIENT")
    result: list[int | None] = [None] * len(values)
    current = _mean(values[:period])
    result[period - 1] = current
    for index in range(period, len(values)):
        current = round_half_even_div(
            current * (period - 1) + 2 * values[index], period + 1
        )
        result[index] = current
    return result


def _atr14(bars: tuple[DailyBarF0, ...]) -> int:
    return _wilder(_true_ranges(bars), 14)


def _rsi14(bars: tuple[DailyBarF0, ...]) -> int:
    gains: list[int] = []
    losses: list[int] = []
    for previous, current in zip(bars, bars[1:]):
        change = current.close_nano_usd - previous.close_nano_usd
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    average_gain = _wilder(gains, 14)
    average_loss = _wilder(losses, 14)
    if average_gain == 0 and average_loss == 0:
        return 500_000
    if average_loss == 0:
        return PPM
    if average_gain == 0:
        return 0
    return clamp(
        round_half_even_div(average_gain * PPM, average_gain + average_loss),
        0,
        PPM,
    )


def _macd(bars: tuple[DailyBarF0, ...]) -> tuple[int, int, int]:
    closes = [bar.close_nano_usd for bar in bars]
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    macd_values: list[int] = []
    for fast, slow in zip(ema12, ema26):
        if fast is not None and slow is not None:
            macd_values.append(fast - slow)
    signal_values = _ema_series(macd_values, 9)
    signal = signal_values[-1]
    if signal is None:
        raise ManagementResearchError("MACD_SIGNAL_NOT_EVALUABLE")
    line = macd_values[-1]
    return line, signal, line - signal


def _dmi_adx14(bars: tuple[DailyBarF0, ...]) -> tuple[int, int, int]:
    true_ranges: list[int] = []
    plus_dm: list[int] = []
    minus_dm: list[int] = []
    for previous, current in zip(bars, bars[1:]):
        true_ranges.append(
            max(
                current.high_nano_usd - current.low_nano_usd,
                abs(current.high_nano_usd - previous.close_nano_usd),
                abs(current.low_nano_usd - previous.close_nano_usd),
            )
        )
        upward = current.high_nano_usd - previous.high_nano_usd
        downward = previous.low_nano_usd - current.low_nano_usd
        plus_dm.append(upward if upward > downward and upward > 0 else 0)
        minus_dm.append(downward if downward > upward and downward > 0 else 0)
    period = 14
    if len(true_ranges) < period * 2:
        raise ManagementResearchError("INDICATOR_WINDOW_INSUFFICIENT")
    smoothed_tr = _mean(true_ranges[:period])
    smoothed_plus = _mean(plus_dm[:period])
    smoothed_minus = _mean(minus_dm[:period])
    dx_values: list[int] = []

    def append_dx() -> tuple[int, int]:
        if smoothed_tr == 0:
            plus_di = 0
            minus_di = 0
        else:
            plus_di = clamp(
                round_half_even_div(smoothed_plus * PPM, smoothed_tr), 0, PPM
            )
            minus_di = clamp(
                round_half_even_div(smoothed_minus * PPM, smoothed_tr), 0, PPM
            )
        denominator = plus_di + minus_di
        dx_values.append(
            0
            if denominator == 0
            else clamp(
                round_half_even_div(abs(plus_di - minus_di) * PPM, denominator),
                0,
                PPM,
            )
        )
        return plus_di, minus_di

    plus_di, minus_di = append_dx()
    for tr, plus, minus in zip(
        true_ranges[period:], plus_dm[period:], minus_dm[period:]
    ):
        smoothed_tr = round_half_even_div(smoothed_tr * 13 + tr, 14)
        smoothed_plus = round_half_even_div(smoothed_plus * 13 + plus, 14)
        smoothed_minus = round_half_even_div(smoothed_minus * 13 + minus, 14)
        plus_di, minus_di = append_dx()
    adx = _wilder(dx_values, period)
    return plus_di, minus_di, adx


def _volume_median20(bars: tuple[DailyBarF0, ...]) -> int:
    prior_normal = [
        bar.volume_shares for bar in bars[:-1] if bar.session_kind == "NORMAL"
    ]
    if len(prior_normal) < 20:
        raise ManagementResearchError("VOLUME_REFERENCE_INSUFFICIENT")
    values = sorted(prior_normal[-20:])
    return round_half_even_div(values[9] + values[10], 2)


def _feature_receipt(
    *,
    feature_id: str,
    formula_id: str,
    value_name: str,
    value: object,
    source_fact_sha256: str,
) -> FeatureReceiptF0:
    body = {
        "feature_id": feature_id,
        "formula_id": formula_id,
        "formula_version": "1",
        "source_fact_sha256": source_fact_sha256,
        value_name: value,
    }
    return FeatureReceiptF0(
        feature_id=feature_id,
        formula_id=formula_id,
        formula_version="1",
        source_fact_sha256=source_fact_sha256,
        feature_sha256=canonical_json_sha256(body),
    )


def derive_local_indicators_f0(
    daily_bars: tuple[DailyBarF0, ...],
) -> LocalIndicatorsF0:
    """Derive all required local indicators from exactly 220 frozen bars."""

    formula_catalog_document_f0()
    if type(daily_bars) is not tuple or len(daily_bars) != 220 or not all(
        isinstance(value, DailyBarF0) for value in daily_bars
    ):
        raise ManagementResearchError("INDICATOR_DAILY_BARS_INVALID")
    source_hash = canonical_json_sha256([bar.as_dict() for bar in daily_bars])
    prior_normal_volume_bars = [
        bar for bar in daily_bars[:-1] if bar.session_kind == "NORMAL"
    ][-20:]
    feature_source_hashes = {
        "SMA50": canonical_json_sha256(
            {
                "close_nano_usd": [
                    bar.close_nano_usd for bar in daily_bars[-50:]
                ]
            }
        ),
        "ATR14": canonical_json_sha256(
            [
                {
                    "high_nano_usd": bar.high_nano_usd,
                    "low_nano_usd": bar.low_nano_usd,
                    "close_nano_usd": bar.close_nano_usd,
                }
                for bar in daily_bars
            ]
        ),
        "RSI14": canonical_json_sha256(
            {"close_nano_usd": [bar.close_nano_usd for bar in daily_bars]}
        ),
        "MACD_12_26_9": canonical_json_sha256(
            {"close_nano_usd": [bar.close_nano_usd for bar in daily_bars]}
        ),
        "DMI_ADX14": canonical_json_sha256(
            [
                {
                    "high_nano_usd": bar.high_nano_usd,
                    "low_nano_usd": bar.low_nano_usd,
                    "close_nano_usd": bar.close_nano_usd,
                }
                for bar in daily_bars
            ]
        ),
        "VOLUME_MEDIAN20": canonical_json_sha256(
            [
                {
                    "session_date": bar.session_date,
                    "volume_shares": bar.volume_shares,
                }
                for bar in prior_normal_volume_bars
            ]
        ),
    }
    # RequiredDailyTechnicalFactsV1 is the authority for this one reused
    # feature: its published rule is integer floor, not F0 half-even.
    sma50 = sum(bar.close_nano_usd for bar in daily_bars[-50:]) // 50
    atr14 = _atr14(daily_bars)
    rsi14 = _rsi14(daily_bars)
    macd_line, macd_signal, macd_histogram = _macd(daily_bars)
    plus_di, minus_di, adx = _dmi_adx14(daily_bars)
    volume_median = _volume_median20(daily_bars)
    receipts = (
        _feature_receipt(
            feature_id="SMA50",
            formula_id="GLD_SMA_CLOSE_INTEGER_FLOOR",
            value_name="sma50_nano_usd",
            value=sma50,
            source_fact_sha256=feature_source_hashes["SMA50"],
        ),
        _feature_receipt(
            feature_id="ATR14",
            formula_id="GLD_ATR14_WILDER_INTEGER_F0_V1",
            value_name="atr14_nano_usd",
            value=atr14,
            source_fact_sha256=feature_source_hashes["ATR14"],
        ),
        _feature_receipt(
            feature_id="RSI14",
            formula_id="GLD_RSI14_WILDER_INTEGER_F0_V1",
            value_name="rsi14_ppm",
            value=rsi14,
            source_fact_sha256=feature_source_hashes["RSI14"],
        ),
        _feature_receipt(
            feature_id="MACD_12_26_9",
            formula_id="GLD_MACD_12_26_9_SMA_SEED_INTEGER_F0_V1",
            value_name="values_nano_usd",
            value=[macd_line, macd_signal, macd_histogram],
            source_fact_sha256=feature_source_hashes["MACD_12_26_9"],
        ),
        _feature_receipt(
            feature_id="DMI_ADX14",
            formula_id="GLD_DMI_ADX14_WILDER_INTEGER_F0_V1",
            value_name="values_ppm",
            value=[plus_di, minus_di, adx],
            source_fact_sha256=feature_source_hashes["DMI_ADX14"],
        ),
        _feature_receipt(
            feature_id="VOLUME_MEDIAN20",
            formula_id="GLD_PRIOR_NORMAL_VOLUME_MEDIAN20_F0_V1",
            value_name="volume_median20_shares",
            value=volume_median,
            source_fact_sha256=feature_source_hashes["VOLUME_MEDIAN20"],
        ),
    )
    values = {
        "sma50_nano_usd": sma50,
        "atr14_nano_usd": atr14,
        "rsi14_ppm": rsi14,
        "macd_line_nano_usd": macd_line,
        "macd_signal_nano_usd": macd_signal,
        "macd_histogram_nano_usd": macd_histogram,
        "plus_di14_ppm": plus_di,
        "minus_di14_ppm": minus_di,
        "adx14_ppm": adx,
        "volume_median20_shares": volume_median,
        "source_fact_sha256": source_hash,
        "feature_receipts": [receipt.as_dict() for receipt in receipts],
    }
    return LocalIndicatorsF0(
        sma50_nano_usd=sma50,
        atr14_nano_usd=atr14,
        rsi14_ppm=rsi14,
        macd_line_nano_usd=macd_line,
        macd_signal_nano_usd=macd_signal,
        macd_histogram_nano_usd=macd_histogram,
        plus_di14_ppm=plus_di,
        minus_di14_ppm=minus_di,
        adx14_ppm=adx,
        volume_median20_shares=volume_median,
        source_fact_sha256=source_hash,
        feature_receipts=receipts,
        indicators_sha256=canonical_json_sha256(values),
    )


__all__ = [
    "FORMULA_CATALOG_SHA256",
    "derive_local_indicators_f0",
    "formula_catalog_document_f0",
]
