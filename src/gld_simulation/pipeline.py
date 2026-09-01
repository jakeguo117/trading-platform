"""Deterministic raw-fact to simulation DecisionResult pipeline.

The module has no provider, broker, order, network, or publication authority.
All accepted outputs are permanently non-actionable simulation artifacts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import time
from typing import Callable
from zoneinfo import ZoneInfo

from gld_normalizer.errors import NormalizationError
from gld_research_core.bcs import (
    BcsFeeScheduleV1,
    bind_lc0_selection_for_research,
)
from gld_research_core.crr_batch import _create_exact_64_batch_engine
from gld_research_core.crr_delta import MODEL_ID, MODEL_SHA256, CrrPitInputsV1
from gld_research_core.crr_input_binding import (
    bind_crr_call_inputs_bulk_from_snapshot,
)
from gld_research_core.facts import (
    OptionContractV1,
    OptionQuoteSnapshotV1,
    OptionQuoteV1,
    SignalSnapshotV1,
    TopOfBookV1,
    build_option_snapshot_candidate_ledger,
    canonical_snapshot_sha256,
    select_first_complete_option_snapshot,
)
from gld_research_core.native_crr_delta import create_native_crr_delta_engine
from gld_research_core.native_tree import load_native_tree_v1

from .canonical import MAX_CANONICAL_INTEGER_DIGITS, canonical_json_sha256
from .carriers import (
    CarrierSelectionError,
    SimulationCarrierEconomicsV1,
    SimulationCarrierSelectionV1,
    price_simulation_carriers,
    select_lc0_and_bcs,
)
from .contracts import (
    DailyGateBarV1,
    GateAInputV1,
    GateAResultV1,
    MinuteGateBarV1,
    SizingInputV1,
    SizingResultV1,
)
from .gate_a import evaluate_gate_a
from .risk import size_position


DECISION_SCHEMA_VERSION = "GLD_SIMULATION_DECISION_RESULT_V2"
PIPELINE_RECEIPT_SCHEMA_VERSION = "GLD_SIMULATION_PIPELINE_RECEIPT_V1"
PIPELINE_RULE_VERSION = "SIM_GLD_PIPELINE_V1"
SYNTHETIC_CLASSIFICATION = "SYNTHETIC_FIXTURE_ONLY"
SIMULATION_CLASSIFICATION = "SIMULATION_ONLY"
FAIL_CLOSED_REASON_CODE = "SIMULATION_PIPELINE_FAIL_CLOSED"
SAFE_FAIL_CLOSED_REASON_CODES = frozenset(
    {
        FAIL_CLOSED_REASON_CODE,
        "P1_CHILD_CRASHED",
        "P1_DEADLINE_EXCEEDED",
        "SIM_ACCOUNT_RECONCILIATION_INVALID",
        "SIM_ACCOUNT_RECONCILIATION_REQUIRED",
        "SIM_DAILY_CAUSALITY_INVALID",
        "SIM_ECONOMIC_EXTRACTION_BINDING_INVALID",
        "SIM_MINUTE_CAUSALITY_INVALID",
        "SIM_SUPERVISOR_FINALIZATION_DEADLINE_EXCEEDED",
    }
)
_NEW_YORK = ZoneInfo("America/New_York")
_NANO = 1_000_000_000
_PPM = 1_000_000
_MAX_CANONICAL_INTEGER = 10**MAX_CANONICAL_INTEGER_DIGITS
_DAILY_BAR_KEYS = {
    "trading_date",
    "open_nano_usd",
    "high_nano_usd",
    "low_nano_usd",
    "close_nano_usd",
    "volume",
    "complete",
    "max_event_utc_ns",
    "max_receive_utc_ns",
}
_MINUTE_BAR_KEYS = {
    "minute_start_utc_ns",
    "minute_end_utc_ns",
    "open_nano_usd",
    "high_nano_usd",
    "low_nano_usd",
    "close_nano_usd",
    "volume",
    "complete",
    "max_event_utc_ns",
    "max_receive_utc_ns",
}


class SimulationPipelineError(ValueError):
    """One stable fail-closed pipeline error."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SimulationEvaluationV1:
    decision_document: dict[str, object]
    pipeline_receipt_document: dict[str, object]


@dataclass(frozen=True, slots=True)
class _NormalizedMarketV1:
    rule: dict[str, object]
    rule_sha256: str
    trading_date: str
    cutoff_utc_ns: int
    prior_session_date: str
    h20_date: str
    calendar_sha256: str
    gate_result: GateAResultV1


@dataclass(frozen=True, slots=True)
class _OptionFactsV1:
    signal: SignalSnapshotV1
    snapshot: OptionQuoteSnapshotV1
    candidate_ledger_sha256: str
    pit_inputs: CrrPitInputsV1
    fees: BcsFeeScheduleV1


@dataclass(frozen=True, slots=True)
class _CalendarSessionV1:
    trading_date: date
    regular_open_utc_ns: int
    regular_close_utc_ns: int
    session_kind: str


@dataclass(frozen=True, slots=True)
class _CalendarContextV1:
    sessions: tuple[_CalendarSessionV1, ...]
    prior_sessions: tuple[_CalendarSessionV1, ...]
    current_session: _CalendarSessionV1
    forward_sessions: tuple[_CalendarSessionV1, ...]
    h20_date: str
    calendar_sha256: str


def _dict(value: object, reason_code: str) -> dict[str, object]:
    if type(value) is not dict:
        raise SimulationPipelineError(reason_code)
    return value


def _list(value: object, reason_code: str) -> list[object]:
    if type(value) is not list:
        raise SimulationPipelineError(reason_code)
    return value


def _int(
    value: object,
    reason_code: str,
    *,
    minimum: int = 0,
) -> int:
    if type(value) is not int or value < minimum:
        raise SimulationPipelineError(reason_code)
    return value


def _optional_int(
    value: object,
    reason_code: str,
    *,
    minimum: int = 0,
) -> int | None:
    if value is None:
        return None
    return _int(value, reason_code, minimum=minimum)


def _str(value: object, reason_code: str) -> str:
    if type(value) is not str or not value or not value.isascii():
        raise SimulationPipelineError(reason_code)
    return value


def _sha256_text(value: object, reason_code: str) -> str:
    text = _str(value, reason_code)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise SimulationPipelineError(reason_code)
    return text


def _bool(value: object, reason_code: str) -> bool:
    if type(value) is not bool:
        raise SimulationPipelineError(reason_code)
    return value


def _validated_ohlcv(
    item: dict[str, object],
    *,
    reason_code: str,
) -> tuple[int | None, int | None, bool]:
    open_value = _optional_int(
        item.get("open_nano_usd"), reason_code, minimum=1
    )
    high = _optional_int(
        item.get("high_nano_usd"), reason_code, minimum=1
    )
    low = _optional_int(
        item.get("low_nano_usd"), reason_code, minimum=1
    )
    close = _optional_int(
        item.get("close_nano_usd"), reason_code, minimum=1
    )
    volume = _optional_int(item.get("volume"), reason_code)
    complete = _bool(item.get("complete"), reason_code)
    values = (open_value, high, low, close, volume)
    if any(value is None for value in values):
        if complete or any(value is not None for value in values):
            raise SimulationPipelineError(reason_code)
        return None, None, False
    assert open_value is not None
    assert high is not None
    assert low is not None
    assert close is not None
    if not (
        low <= high
        and low <= open_value <= high
        and low <= close <= high
    ):
        raise SimulationPipelineError(reason_code)
    return high, close, complete


def _document(
    raw_documents: Mapping[str, object],
    logical_key: str,
) -> dict[str, object]:
    try:
        value = raw_documents[logical_key]
    except KeyError as error:
        raise SimulationPipelineError("SIM_RAW_DOCUMENT_MISSING") from error
    document = _dict(value, "SIM_RAW_DOCUMENT_INVALID")
    if document.get("classification") != SYNTHETIC_CLASSIFICATION:
        raise SimulationPipelineError("SIM_RAW_CLASSIFICATION_INVALID")
    return document


def _deadline_guard(deadline_monotonic_ns: int) -> None:
    if type(deadline_monotonic_ns) is not int or time.monotonic_ns() >= deadline_monotonic_ns:
        raise SimulationPipelineError("SIMULATION_HARD_DEADLINE")


def _expected_rule_documents() -> dict[str, object]:
    return {
        "gate_a": {
            "rule_id": "SIM_A1_RANGE_HOLD_V1",
            "sma_fast_sessions": 50,
            "sma_slow_sessions": 200,
            "sma_fast_slope_lookback_sessions": 20,
            "breakout_lookback_complete_sessions": 20,
            "confirmation_lookback_complete_minutes": 15,
            "confirmation_minimum_closes_above": 10,
            "decision_minute_local": "10:44",
        },
        "bcs": {
            "selector_id": "SIM_BCS_025_V1",
            "short_target_delta_ppm": 250_000,
            "short_minimum_delta_ppm": 200_000,
            "short_maximum_delta_ppm": 300_000,
            "leg_ratio": "1:1",
            "same_expiry_required": True,
            "roll_active": False,
            "second_best_fallback_allowed": False,
        },
        "risk": {
            "policy_id": "SIM_HALF_KELLY_RISK_V1",
            "kelly_fraction_ppm": 500_000,
            "realized_positive_profit_reinvestment_ppm": 500_000,
            "realized_loss_effect_ppm": 1_000_000,
            "unrealized_profit_expands_bankroll": False,
            "minimum_post_trade_settled_cash_nlv_ppm": 100_000,
            "maximum_gld_equivalent_delta_notional_bankroll_ppm": 1_500_000,
            "planned_loss_equals_maximum_loss": True,
            "candidate_terminal_policy": "BOTH_LC0_AND_BCS_MUST_PASS",
            "entry_stress_long_ticks": 1,
            "entry_stress_short_ticks": 1,
            "include_exit_fees_in_max_loss": True,
            "drawdown_bands": [
                {"minimum_ppm": 0, "maximum_ppm": 50_000, "interval": "[0,50000]", "risk_multiplier_ppm": 1_000_000, "action": "NORMAL"},
                {"minimum_ppm": 50_000, "maximum_ppm": 100_000, "interval": "(50000,100000]", "risk_multiplier_ppm": 800_000, "action": "REDUCED"},
                {"minimum_ppm": 100_000, "maximum_ppm": 150_000, "interval": "(100000,150000)", "risk_multiplier_ppm": 400_000, "action": "DEFENSIVE"},
                {"minimum_ppm": 150_000, "maximum_ppm": 200_000, "interval": "[150000,200000)", "risk_multiplier_ppm": 0, "action": "NO_NEW_ENTRY"},
                {"minimum_ppm": 200_000, "maximum_ppm": 300_000, "interval": "[200000,300000)", "risk_multiplier_ppm": 0, "action": "EXIT_MANAGED"},
                {"minimum_ppm": 300_000, "maximum_ppm": None, "interval": "[300000,INF)", "risk_multiplier_ppm": 0, "action": "STICKY_LOCK"},
            ],
        },
        "exit_plan": {
            "fixed_take_profit": False,
            "gate_a_invalidation_active": True,
            "expiry_safety_active": True,
            "latest_full_exit_session": "H20",
            "same_expiry_roll_up": "NOT_ACTIVE_IN_SIM_V1",
        },
    }


def _validate_rule(rule: dict[str, object], cutoff_utc_ns: int) -> str:
    if (
        rule.get("schema_version") != "SIM_GLD_RULE_PACKAGE_V1"
        or rule.get("rule_package_id") != PIPELINE_RULE_VERSION
        or rule.get("underlying") != "GLD"
        or _int(rule.get("effective_from_utc_ns"), "SIM_RULE_INVALID")
        > cutoff_utc_ns
    ):
        raise SimulationPipelineError("SIM_RULE_INVALID")
    expected = _expected_rule_documents()
    for name in ("gate_a", "bcs", "risk", "exit_plan"):
        if rule.get(name) != expected[name]:
            raise SimulationPipelineError("SIM_RULE_UNSUPPORTED")
    lc0 = _dict(rule.get("lc0"), "SIM_LC0_RULE_INVALID")
    expected_lc0 = {
        "selector_id": "SIM_LC0_050_V1",
        "target_delta_ppm": 500_000,
        "minimum_delta_ppm": 450_000,
        "maximum_delta_ppm": 550_000,
        "minimum_calendar_days_after_h20": 30,
        "required_right": "C",
        "required_exercise_style": "AMERICAN",
        "required_multiplier": 100,
        "require_standard_unadjusted": True,
        "tie_break_order": [
            "ABS_DELTA_DISTANCE_ASC",
            "STRIKE_NANO_USD_DESC",
            "OCC_SYMBOL_ASCII_ASC",
        ],
        "coarse_fine_agreement_required": True,
        "second_best_fallback_allowed": False,
    }
    if lc0 != expected_lc0:
        raise SimulationPipelineError("SIM_LC0_RULE_UNSUPPORTED")
    return canonical_json_sha256(rule)


def _average_floor(values: list[int]) -> int:
    """Display-only integer floor; Gate predicates use exact rolling sums."""

    if not values:
        raise SimulationPipelineError("SIM_SMA_INPUT_INVALID")
    return sum(values) // len(values)


def _utc_local_datetime(value: int, reason_code: str) -> datetime:
    seconds, remainder = divmod(_int(value, reason_code, minimum=1), _NANO)
    if remainder:
        raise SimulationPipelineError(reason_code)
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(
            _NEW_YORK
        )
    except (OSError, OverflowError, ValueError) as error:
        raise SimulationPipelineError(reason_code) from error


def _calendar_context(
    *,
    raw_documents: Mapping[str, object],
    trading_date: str,
    cutoff_utc_ns: int,
) -> _CalendarContextV1:
    calendar = _document(raw_documents, "calendar")
    if (
        calendar.get("authority_id") != "XNYS_SYNTHETIC_CALENDAR"
        or calendar.get("timezone") != "America/New_York"
    ):
        raise SimulationPipelineError("SIM_CALENDAR_INVALID")
    try:
        current_date = date.fromisoformat(trading_date)
    except ValueError as error:
        raise SimulationPipelineError("SIM_CALENDAR_INVALID") from error

    parsed: list[_CalendarSessionV1] = []
    for raw in _list(calendar.get("sessions"), "SIM_CALENDAR_INVALID"):
        item = _dict(raw, "SIM_CALENDAR_INVALID")
        kind = _str(item.get("session_kind"), "SIM_CALENDAR_INVALID")
        if kind not in {"REGULAR_FULL_DAY", "REGULAR_EARLY_CLOSE"}:
            raise SimulationPipelineError("SIM_CALENDAR_SESSION_KIND_UNSUPPORTED")
        try:
            session_date = date.fromisoformat(
                _str(item.get("trading_date"), "SIM_CALENDAR_INVALID")
            )
        except ValueError as error:
            raise SimulationPipelineError("SIM_CALENDAR_INVALID") from error
        regular_open = _int(
            item.get("regular_open_utc_ns"),
            "SIM_CALENDAR_INVALID",
            minimum=1,
        )
        regular_close = _int(
            item.get("regular_close_utc_ns"),
            "SIM_CALENDAR_INVALID",
            minimum=1,
        )
        if regular_open >= regular_close:
            raise SimulationPipelineError("SIM_CALENDAR_INVALID")
        local_open = _utc_local_datetime(regular_open, "SIM_CALENDAR_INVALID")
        local_close = _utc_local_datetime(regular_close, "SIM_CALENDAR_INVALID")
        expected_close = (16, 0) if kind == "REGULAR_FULL_DAY" else (13, 0)
        if (
            local_open.date() != session_date
            or (local_open.hour, local_open.minute, local_open.second, local_open.microsecond)
            != (9, 30, 0, 0)
            or local_close.date() != session_date
            or (local_close.hour, local_close.minute, local_close.second, local_close.microsecond)
            != (*expected_close, 0, 0)
        ):
            raise SimulationPipelineError("SIM_CALENDAR_SESSION_TIME_INVALID")
        parsed.append(
            _CalendarSessionV1(
                trading_date=session_date,
                regular_open_utc_ns=regular_open,
                regular_close_utc_ns=regular_close,
                session_kind=kind,
            )
        )
    session_dates = tuple(item.trading_date for item in parsed)
    if session_dates != tuple(sorted(session_dates)) or len(set(session_dates)) != len(
        session_dates
    ):
        raise SimulationPipelineError("SIM_CALENDAR_SESSION_ORDER_INVALID")
    current = tuple(item for item in parsed if item.trading_date == current_date)
    if len(current) != 1:
        raise SimulationPipelineError("SIM_CURRENT_SESSION_MISSING")
    current_session = current[0]
    cutoff_local = _utc_local_datetime(cutoff_utc_ns, "SIM_MARKET_STATUS_INVALID")
    if (
        cutoff_local.date() != current_date
        or not current_session.regular_open_utc_ns
        < cutoff_utc_ns
        < current_session.regular_close_utc_ns
    ):
        raise SimulationPipelineError("SIM_MARKET_CUTOFF_OUTSIDE_SESSION")
    prior = tuple(item for item in parsed if item.trading_date < current_date)
    forward = tuple(item for item in parsed if item.trading_date > current_date)
    if len(prior) < 220:
        raise SimulationPipelineError("SIM_DAILY_CALENDAR_HISTORY_MISSING")
    if len(forward) < 20:
        raise SimulationPipelineError("SIM_H20_CALENDAR_MISSING")
    return _CalendarContextV1(
        sessions=tuple(parsed),
        prior_sessions=prior,
        current_session=current_session,
        forward_sessions=forward,
        h20_date=forward[19].trading_date.isoformat(),
        calendar_sha256=canonical_json_sha256(calendar),
    )


def _normalized_gate_input(
    *,
    raw_documents: Mapping[str, object],
    cutoff_utc_ns: int,
    trading_date: str,
    calendar: _CalendarContextV1,
) -> GateAInputV1:
    daily_document = _document(raw_documents, "daily_bars")
    minute_document = _document(raw_documents, "minute_bars")
    status = _document(raw_documents, "market_status")
    if (
        daily_document.get("underlying") != "GLD"
        or daily_document.get("price_scale") != "NANO_USD"
        or minute_document.get("underlying") != "GLD"
        or minute_document.get("price_scale") != "NANO_USD"
        or minute_document.get("trading_date") != trading_date
    ):
        raise SimulationPipelineError("SIM_GATE_SOURCE_INVALID")
    daily_complete = _bool(
        status.get("daily_history_complete"),
        "SIM_MARKET_STATUS_INVALID",
    )
    intraday_complete = _bool(
        status.get("intraday_history_complete"),
        "SIM_MARKET_STATUS_INVALID",
    )
    causal_enforced = _bool(
        status.get("causal_cutoff_enforced"),
        "SIM_MARKET_STATUS_INVALID",
    )

    parsed_daily: list[dict[str, object]] = []
    for raw in _list(daily_document.get("bars"), "SIM_DAILY_BARS_INVALID"):
        item = _dict(raw, "SIM_DAILY_BAR_INVALID")
        if set(item) != _DAILY_BAR_KEYS:
            raise SimulationPipelineError("SIM_DAILY_BAR_SCHEMA_INVALID")
        high, close, bar_complete = _validated_ohlcv(
            item,
            reason_code="SIM_DAILY_BAR_OHLCV_INVALID",
        )
        item_date = _str(item.get("trading_date"), "SIM_DAILY_BAR_INVALID")
        try:
            ordinal_date = date.fromisoformat(item_date)
        except ValueError as error:
            raise SimulationPipelineError("SIM_DAILY_BAR_INVALID") from error
        parsed_daily.append(
            {
                "date": ordinal_date,
                "high": high,
                "close": close,
                "complete": bar_complete and daily_complete,
                "event": _int(item.get("max_event_utc_ns"), "SIM_DAILY_BAR_INVALID", minimum=1),
                "receive": _int(item.get("max_receive_utc_ns"), "SIM_DAILY_BAR_INVALID", minimum=1),
            }
        )
    dates = tuple(item["date"] for item in parsed_daily)
    expected_sessions = calendar.prior_sessions[-220:]
    expected_dates = tuple(item.trading_date for item in expected_sessions)
    if len(parsed_daily) != 220 or dates != expected_dates:
        raise SimulationPipelineError("SIM_DAILY_CALENDAR_BINDING_INVALID")
    for item, session in zip(parsed_daily, expected_sessions, strict=True):
        if (
            item["event"] < session.regular_open_utc_ns
            or item["event"] > session.regular_close_utc_ns
            or item["receive"] < item["event"]
            or item["receive"] > session.regular_close_utc_ns
        ):
            raise SimulationPipelineError("SIM_DAILY_CAUSALITY_INVALID")
    normalized_daily: list[DailyGateBarV1] = []
    starting_index = max(0, len(parsed_daily) - 21)
    for index in range(starting_index, len(parsed_daily)):
        item = parsed_daily[index]
        closes50 = [
            candidate["close"]
            for candidate in parsed_daily[index - 49 : index + 1]
        ] if index >= 49 else []
        closes200 = [
            candidate["close"]
            for candidate in parsed_daily[index - 199 : index + 1]
        ] if index >= 199 else []
        complete50 = index >= 49 and all(
            candidate["complete"] for candidate in parsed_daily[index - 49 : index + 1]
        )
        complete200 = index >= 199 and all(
            candidate["complete"] for candidate in parsed_daily[index - 199 : index + 1]
        )
        sma50_values = [value for value in closes50 if type(value) is int]
        sma200_values = [value for value in closes200 if type(value) is int]
        sma50_sum = sum(sma50_values) if complete50 and len(sma50_values) == 50 else None
        sma200_sum = sum(sma200_values) if complete200 and len(sma200_values) == 200 else None
        sma50 = (
            _average_floor(sma50_values)
            if complete50 and len(closes50) == 50 and all(type(value) is int for value in closes50)
            else None
        )
        sma200 = (
            _average_floor(sma200_values)
            if complete200 and len(closes200) == 200 and all(type(value) is int for value in closes200)
            else None
        )
        normalized_daily.append(
            DailyGateBarV1(
                session_ordinal=len(normalized_daily) + 1,
                high_nano_usd=item["high"],
                close_nano_usd=item["close"],
                sma50_nano_usd=sma50,
                sma200_nano_usd=sma200,
                sma50_sum_nano_usd=sma50_sum,
                sma200_sum_nano_usd=sma200_sum,
                complete=bool(item["complete"]) and causal_enforced,
                max_event_utc_ns=item["event"],
                max_receive_utc_ns=item["receive"],
            )
        )

    normalized_minutes: list[MinuteGateBarV1] = []
    for raw in _list(minute_document.get("bars"), "SIM_MINUTE_BARS_INVALID"):
        item = _dict(raw, "SIM_MINUTE_BAR_INVALID")
        if set(item) != _MINUTE_BAR_KEYS:
            raise SimulationPipelineError("SIM_MINUTE_BAR_SCHEMA_INVALID")
        _, minute_close, minute_complete = _validated_ohlcv(
            item,
            reason_code="SIM_MINUTE_BAR_OHLCV_INVALID",
        )
        minute_start_ns = _int(
            item.get("minute_start_utc_ns"),
            "SIM_MINUTE_BAR_INVALID",
            minimum=1,
        )
        minute_end_ns = _int(
            item.get("minute_end_utc_ns"),
            "SIM_MINUTE_BAR_INVALID",
            minimum=1,
        )
        if minute_end_ns != minute_start_ns + 60 * _NANO:
            raise SimulationPipelineError("SIM_MINUTE_INTERVAL_INVALID")
        local = _utc_local_datetime(
            minute_start_ns,
            "SIM_MINUTE_BAR_INVALID",
        )
        if local.date().isoformat() != trading_date:
            raise SimulationPipelineError("SIM_MINUTE_DATE_MISMATCH")
        event = _int(item.get("max_event_utc_ns"), "SIM_MINUTE_BAR_INVALID", minimum=1)
        receive = _int(item.get("max_receive_utc_ns"), "SIM_MINUTE_BAR_INVALID", minimum=1)
        if (
            minute_end_ns > cutoff_utc_ns
            or event < minute_start_ns
            or event >= minute_end_ns
            or receive < event
            or receive >= minute_end_ns
        ):
            raise SimulationPipelineError("SIM_MINUTE_CAUSALITY_INVALID")
        normalized_minutes.append(
            MinuteGateBarV1(
                minute_ending_ordinal=local.hour * 60 + local.minute,
                close_nano_usd=minute_close,
                complete=(
                    minute_complete
                    and intraday_complete
                    and causal_enforced
                ),
                max_event_utc_ns=event,
                max_receive_utc_ns=receive,
            )
        )
    return GateAInputV1(
        cutoff_utc_ns=cutoff_utc_ns,
        daily_bars=tuple(normalized_daily),
        minute_bars=tuple(normalized_minutes),
    )


def _normalize_market(
    raw_documents: Mapping[str, object],
) -> _NormalizedMarketV1:
    status = _document(raw_documents, "market_status")
    trading_date = _str(status.get("trading_date"), "SIM_MARKET_STATUS_INVALID")
    cutoff = _int(status.get("as_of_utc_ns"), "SIM_MARKET_STATUS_INVALID", minimum=1)
    if (
        status.get("market_phase") != "REGULAR_OPEN"
        or status.get("complete_through_minute_end_utc_ns") != cutoff
        or _bool(status.get("calendar_complete"), "SIM_MARKET_STATUS_INVALID") is not True
    ):
        raise SimulationPipelineError("SIM_MARKET_STATUS_INVALID")
    rule = _document(raw_documents, "rule_package")
    rule_sha256 = _validate_rule(rule, cutoff)
    calendar = _calendar_context(
        raw_documents=raw_documents,
        trading_date=trading_date,
        cutoff_utc_ns=cutoff,
    )
    gate_input = _normalized_gate_input(
        raw_documents=raw_documents,
        cutoff_utc_ns=cutoff,
        trading_date=trading_date,
        calendar=calendar,
    )
    return _NormalizedMarketV1(
        rule=rule,
        rule_sha256=rule_sha256,
        trading_date=trading_date,
        cutoff_utc_ns=cutoff,
        prior_session_date=calendar.prior_sessions[-1].trading_date.isoformat(),
        h20_date=calendar.h20_date,
        calendar_sha256=calendar.calendar_sha256,
        gate_result=evaluate_gate_a(gate_input),
    )


def _book(value: object) -> TopOfBookV1:
    item = _dict(value, "SIM_TOP_OF_BOOK_INVALID")
    flags = _list(item.get("flags"), "SIM_TOP_OF_BOOK_INVALID")
    return TopOfBookV1(
        bid_nano_usd=_int(item.get("bid_nano_usd"), "SIM_TOP_OF_BOOK_INVALID"),
        ask_nano_usd=_int(item.get("ask_nano_usd"), "SIM_TOP_OF_BOOK_INVALID", minimum=1),
        bid_size=_int(item.get("bid_size"), "SIM_TOP_OF_BOOK_INVALID", minimum=1),
        ask_size=_int(item.get("ask_size"), "SIM_TOP_OF_BOOK_INVALID", minimum=1),
        tick_nano_usd=_int(item.get("tick_nano_usd"), "SIM_TOP_OF_BOOK_INVALID", minimum=1),
        ts_event_ns=_int(item.get("ts_event_ns"), "SIM_TOP_OF_BOOK_INVALID", minimum=1),
        ts_recv_ns=_int(item.get("ts_recv_ns"), "SIM_TOP_OF_BOOK_INVALID", minimum=1),
        flags=tuple(_str(flag, "SIM_TOP_OF_BOOK_INVALID") for flag in flags),
    )


def _option_facts(
    *,
    raw_documents: Mapping[str, object],
    raw_content_sha256: str,
    bundle_id: str,
    market: _NormalizedMarketV1,
) -> _OptionFactsV1:
    gate = market.gate_result
    if (
        gate.state != "PASS"
        or gate.max_event_utc_ns is None
        or gate.max_receive_utc_ns is None
    ):
        raise SimulationPipelineError("SIM_GATE_NOT_PASS")
    calendar = _document(raw_documents, "calendar")
    status = _document(raw_documents, "market_status")
    daily = _document(raw_documents, "daily_bars")
    minute = _document(raw_documents, "minute_bars")
    signal = SignalSnapshotV1(
        signal_id=f"sim-gld-{bundle_id}",
        trading_date=market.trading_date,
        rule_version=PIPELINE_RULE_VERSION,
        rule_sha256=market.rule_sha256,
        calendar_authority_id=_str(calendar.get("authority_id"), "SIM_CALENDAR_INVALID"),
        calendar_version=_str(calendar.get("calendar_version"), "SIM_CALENDAR_INVALID"),
        calendar_sha256=market.calendar_sha256,
        phase_receipt_sha256=canonical_json_sha256(
            {
                "calendar_sha256": market.calendar_sha256,
                "cutoff_utc_ns": market.cutoff_utc_ns,
                "market_phase": status.get("market_phase"),
                "trading_date": market.trading_date,
            }
        ),
        completeness_receipt_sha256=canonical_json_sha256(
            {
                "daily_bars_sha256": canonical_json_sha256(daily),
                "market_status_sha256": canonical_json_sha256(status),
                "minute_bars_sha256": canonical_json_sha256(minute),
            }
        ),
        cutoff_utc_ns=market.cutoff_utc_ns,
        max_event_utc_ns=gate.max_event_utc_ns,
        max_receive_utc_ns=gate.max_receive_utc_ns,
        signal_state="PASS",
        input_fact_sha256=gate.input_fact_sha256,
    )

    contract_document = _document(raw_documents, "option_contracts")
    quote_document = _document(raw_documents, "market_quotes")
    pit_document = _document(raw_documents, "pit_inputs")
    fee_document = _document(raw_documents, "fee_schedule")
    if (
        contract_document.get("underlying") != "GLD"
        or contract_document.get("source")
        != "OFFLINE_SYNTHETIC_ECONOMIC_EXTRACTION"
        or quote_document.get("source") != "SYNTHETIC_NOT_ENTITLED"
        or quote_document.get("source_version")
        != "sim-economic-extraction-v1"
    ):
        raise SimulationPipelineError("SIM_OPTION_SOURCE_INVALID")
    contract_extraction_sha256 = _sha256_text(
        contract_document.get("extracted_economic_values_sha256"),
        "SIM_ECONOMIC_EXTRACTION_BINDING_INVALID",
    )
    quote_extraction_sha256 = _sha256_text(
        quote_document.get("extracted_economic_values_sha256"),
        "SIM_ECONOMIC_EXTRACTION_BINDING_INVALID",
    )
    extraction_binding = {
        "underlying_top": quote_document.get("underlying_bbo"),
        "contracts": contract_document.get("contracts"),
        "quotes": quote_document.get("option_bbo"),
        "pit_inputs": {
            key: value
            for key, value in pit_document.items()
            if key not in {"schema_version", "classification"}
        },
        "fee_schedule": {
            key: value
            for key, value in fee_document.items()
            if key not in {"schema_version", "classification"}
        },
    }
    recomputed_extraction_sha256 = canonical_json_sha256(
        extraction_binding
    )
    if (
        contract_extraction_sha256 != quote_extraction_sha256
        or contract_extraction_sha256 != recomputed_extraction_sha256
    ):
        raise SimulationPipelineError(
            "SIM_ECONOMIC_EXTRACTION_BINDING_INVALID"
        )
    window_start = _int(
        quote_document.get("window_start_utc_ns"),
        "SIM_OPTION_QUOTES_INVALID",
        minimum=1,
    )
    window_end = _int(
        quote_document.get("window_end_utc_ns"),
        "SIM_OPTION_QUOTES_INVALID",
        minimum=1,
    )
    capture = _int(
        quote_document.get("capture_utc_ns"),
        "SIM_OPTION_QUOTES_INVALID",
        minimum=1,
    )
    if (
        window_start != market.cutoff_utc_ns
        or not window_start <= capture <= window_end
        or window_end - window_start > 60 * _NANO
    ):
        raise SimulationPipelineError("SIM_OPTION_QUOTE_WINDOW_INVALID")
    contracts: list[OptionContractV1] = []
    for raw in _list(contract_document.get("contracts"), "SIM_OPTION_CONTRACTS_INVALID"):
        item = _dict(raw, "SIM_OPTION_CONTRACT_INVALID")
        contracts.append(
            OptionContractV1(
                occ_symbol=_str(item.get("occ_symbol"), "SIM_OPTION_CONTRACT_INVALID"),
                underlying=_str(item.get("underlying"), "SIM_OPTION_CONTRACT_INVALID"),
                right=_str(item.get("right"), "SIM_OPTION_CONTRACT_INVALID"),
                strike_nano_usd=_int(item.get("strike_nano_usd"), "SIM_OPTION_CONTRACT_INVALID", minimum=1),
                expiry_utc_ns=_int(item.get("expiry_utc_ns"), "SIM_OPTION_CONTRACT_INVALID", minimum=1),
                last_trading_utc_ns=_int(item.get("last_trading_utc_ns"), "SIM_OPTION_CONTRACT_INVALID", minimum=1),
                activation_utc_ns=_int(item.get("activation_utc_ns"), "SIM_OPTION_CONTRACT_INVALID", minimum=1),
                multiplier=_int(item.get("multiplier"), "SIM_OPTION_CONTRACT_INVALID", minimum=1),
                deliverable=_str(item.get("deliverable"), "SIM_OPTION_CONTRACT_INVALID"),
                currency=_str(item.get("currency"), "SIM_OPTION_CONTRACT_INVALID"),
                standard_unadjusted=_bool(item.get("standard_unadjusted"), "SIM_OPTION_CONTRACT_INVALID"),
                exercise_style=_str(item.get("exercise_style"), "SIM_OPTION_CONTRACT_INVALID"),
            )
        )
    if len(contracts) != 64:
        raise SimulationPipelineError("SIM_OPTION_CALL_COUNT_INVALID")
    contracts.sort(
        key=lambda item: (
            item.expiry_utc_ns,
            item.strike_nano_usd,
            item.occ_symbol,
        )
    )
    books: dict[str, TopOfBookV1] = {}
    for raw in _list(quote_document.get("option_bbo"), "SIM_OPTION_QUOTES_INVALID"):
        item = _dict(raw, "SIM_OPTION_QUOTE_INVALID")
        contract_id = _str(item.get("occ_symbol"), "SIM_OPTION_QUOTE_INVALID")
        if contract_id in books:
            raise SimulationPipelineError("SIM_OPTION_QUOTE_DUPLICATE")
        books[contract_id] = _book(item.get("top_of_book"))
    if set(books) != {item.occ_symbol for item in contracts}:
        raise SimulationPipelineError("SIM_OPTION_QUOTE_BINDING_INVALID")
    option_quotes = tuple(
        OptionQuoteV1(contract=contract, top_of_book=books[contract.occ_symbol])
        for contract in contracts
    )
    underlying_top = _book(quote_document.get("underlying_bbo"))
    for book in (underlying_top, *books.values()):
        if (
            book.ts_event_ns < window_start
            or book.ts_event_ns > capture
            or book.ts_recv_ns < book.ts_event_ns
            or book.ts_recv_ns > capture
        ):
            raise SimulationPipelineError("SIM_OPTION_QUOTE_CAUSALITY_INVALID")
    provenance_sha256 = canonical_json_sha256(
        {
            "option_contracts": contract_document,
            "market_quotes": quote_document,
            "raw_content_sha256": raw_content_sha256,
        }
    )
    snapshot = OptionQuoteSnapshotV1(
        signal_snapshot_sha256=signal.snapshot_sha256,
        candidate_ordinal=1,
        candidate_provenance_sha256=provenance_sha256,
        window_start_utc_ns=window_start,
        window_end_utc_ns=window_end,
        capture_utc_ns=capture,
        underlying_top=underlying_top,
        option_quotes=option_quotes,
        source_id=_str(quote_document.get("source"), "SIM_OPTION_QUOTES_INVALID"),
        source_version=_str(quote_document.get("source_version"), "SIM_OPTION_QUOTES_INVALID"),
        market_data_type="SYNTHETIC_NOT_ENTITLED",
        source_receipt_sha256=canonical_json_sha256(
            {
                "extracted_economic_values_sha256": quote_document.get("extracted_economic_values_sha256"),
                "provenance_sha256": provenance_sha256,
                "source": quote_document.get("source"),
                "source_version": quote_document.get("source_version"),
            }
        ),
    )
    ledger = build_option_snapshot_candidate_ledger((snapshot,), signal_snapshot=signal)
    selected = select_first_complete_option_snapshot(ledger, signal_snapshot=signal)
    if selected is not snapshot:
        raise SimulationPipelineError("SIM_OPTION_SELECTION_INVALID")

    pit_as_of = _int(
        pit_document.get("as_of_utc_ns"),
        "SIM_PIT_INPUTS_INVALID",
        minimum=1,
    )
    if pit_as_of != capture:
        raise SimulationPipelineError("SIM_PIT_INPUTS_CAUSALITY_INVALID")
    pit = CrrPitInputsV1(
        as_of_utc_ns=pit_as_of,
        risk_free_rate_ppm=_int(pit_document.get("risk_free_rate_ppm"), "SIM_PIT_INPUTS_INVALID"),
        expense_yield_ppm=_int(pit_document.get("expense_yield_ppm"), "SIM_PIT_INPUTS_INVALID"),
        borrow_yield_ppm=_int(pit_document.get("borrow_yield_ppm"), "SIM_PIT_INPUTS_INVALID"),
        rate_curve_sha256=_str(pit_document.get("rate_curve_sha256"), "SIM_PIT_INPUTS_INVALID"),
        distribution_assumption_sha256=_str(pit_document.get("distribution_assumption_sha256"), "SIM_PIT_INPUTS_INVALID"),
        borrow_assumption_sha256=_str(pit_document.get("borrow_assumption_sha256"), "SIM_PIT_INPUTS_INVALID"),
    )
    fee_effective = _int(
        fee_document.get("effective_from_utc_ns"),
        "SIM_FEE_SCHEDULE_INVALID",
        minimum=1,
    )
    if fee_effective > capture:
        raise SimulationPipelineError("SIM_FEE_SCHEDULE_CAUSALITY_INVALID")
    fees = BcsFeeScheduleV1(
        source_receipt_sha256=_str(fee_document.get("source_receipt_sha256"), "SIM_FEE_SCHEDULE_INVALID"),
        effective_from_utc_ns=fee_effective,
        long_entry_fee_nano_usd_per_contract=_int(fee_document.get("long_entry_fee_nano_usd_per_contract"), "SIM_FEE_SCHEDULE_INVALID"),
        short_entry_fee_nano_usd_per_contract=_int(fee_document.get("short_entry_fee_nano_usd_per_contract"), "SIM_FEE_SCHEDULE_INVALID"),
        long_exit_fee_nano_usd_per_contract=_int(fee_document.get("long_exit_fee_nano_usd_per_contract"), "SIM_FEE_SCHEDULE_INVALID"),
        short_exit_fee_nano_usd_per_contract=_int(fee_document.get("short_exit_fee_nano_usd_per_contract"), "SIM_FEE_SCHEDULE_INVALID"),
    )
    return _OptionFactsV1(
        signal=signal,
        snapshot=snapshot,
        candidate_ledger_sha256=ledger.ledger_sha256,
        pit_inputs=pit,
        fees=fees,
    )


def _preflight_risk_facts(
    raw_documents: Mapping[str, object],
    *,
    market: _NormalizedMarketV1,
) -> tuple[dict[str, object], dict[str, object], tuple[str, ...]]:
    account = _document(raw_documents, "account_snapshot_a")
    kelly = _document(raw_documents, "frozen_kelly_receipt")
    required_account = (
        "as_of_utc_ns",
        "net_liquidation_value_nano_usd",
        "settled_cash_nano_usd",
        "strategy_bankroll_principal_nano_usd",
        "strategy_high_watermark_nlv_nano_usd",
        "cumulative_realized_profit_nano_usd",
        "cumulative_realized_loss_nano_usd",
        "unrealized_profit_nano_usd",
        "current_gld_equivalent_delta_notional_nano_usd",
        "sticky_drawdown_lock_active",
    )
    required_kelly = (
        "robust_full_kelly_ppm",
        "frozen_at_utc_ns",
        "sample_end_trading_date",
        "sample_episode_count",
        "sample_input_sha256",
        "strategy_rule_package_id",
        "applicable_carriers",
        "return_distribution_id",
    )
    missing = tuple(
        [f"account_snapshot_a.{name}" for name in required_account if account.get(name) is None]
        + [f"frozen_kelly_receipt.{name}" for name in required_kelly if kelly.get(name) is None]
    )
    if missing:
        return account, kelly, missing
    account_as_of = _int(
        account.get("as_of_utc_ns"),
        "SIM_ACCOUNT_CAUSALITY_INVALID",
        minimum=1,
    )
    frozen_at = _int(
        kelly.get("frozen_at_utc_ns"),
        "SIM_KELLY_RECEIPT_INVALID",
        minimum=1,
    )
    positions = account.get("positions")
    open_orders = account.get("open_orders")
    if type(positions) is not list or type(open_orders) is not list:
        raise SimulationPipelineError("SIM_ACCOUNT_RECONCILIATION_INVALID")
    if positions or open_orders:
        raise SimulationPipelineError(
            "SIM_ACCOUNT_RECONCILIATION_REQUIRED"
        )
    if (
        account_as_of != market.cutoff_utc_ns
        or account.get("currency") != "USD"
        or account.get("source") != "SYNTHETIC_ACCOUNT"
        or type(account.get("sticky_drawdown_lock_active")) is not bool
    ):
        raise SimulationPipelineError("SIM_ACCOUNT_CAUSALITY_INVALID")
    if (
        kelly.get("daily_reestimation_allowed") is not False
        or kelly.get("method") != "SYNTHETIC_ROBUST_LOWER_BOUND"
        or kelly.get("receipt_id")
        != "SIM_GLD_ROBUST_FULL_KELLY_20260828_V1"
        or frozen_at >= market.cutoff_utc_ns
        or kelly.get("sample_end_trading_date") != market.prior_session_date
        or _int(
            kelly.get("sample_episode_count"),
            "SIM_KELLY_RECEIPT_INVALID",
            minimum=1,
        )
        < 30
        or _sha256_text(
            kelly.get("sample_input_sha256"),
            "SIM_KELLY_RECEIPT_INVALID",
        )
        == "0" * 64
        or kelly.get("strategy_rule_package_id") != PIPELINE_RULE_VERSION
        or kelly.get("applicable_carriers") != ["LC0", "BCS0"]
        or kelly.get("return_distribution_id")
        != "SIM_DUAL_CARRIER_CONSERVATIVE_V1"
    ):
        raise SimulationPipelineError("SIM_KELLY_RECEIPT_INVALID")
    return account, kelly, missing


def _sizing(
    *,
    account: dict[str, object],
    kelly: dict[str, object],
    economics: SimulationCarrierEconomicsV1,
    delta_ppm: int,
    spot_nano_usd: int,
) -> SizingResultV1:
    principal = _optional_int(account.get("strategy_bankroll_principal_nano_usd"), "SIM_ACCOUNT_INVALID")
    positive = _optional_int(account.get("cumulative_realized_profit_nano_usd"), "SIM_ACCOUNT_INVALID")
    loss = _optional_int(account.get("cumulative_realized_loss_nano_usd"), "SIM_ACCOUNT_INVALID")
    eligible_estimate = None
    if principal is not None and positive is not None and loss is not None:
        eligible_estimate = max(0, principal + positive // 2 - loss)
    hard_capacity = (
        None
        if eligible_estimate is None
        else eligible_estimate // economics.sizing_max_loss_nano_usd_per_contract
    )
    return size_position(
        SizingInputV1(
            initial_bankroll_nano_usd=principal,
            cumulative_realized_positive_nano_usd=positive,
            cumulative_realized_loss_nano_usd=loss,
            unrealized_profit_nano_usd=_optional_int(account.get("unrealized_profit_nano_usd"), "SIM_ACCOUNT_INVALID"),
            current_nlv_nano_usd=_optional_int(account.get("net_liquidation_value_nano_usd"), "SIM_ACCOUNT_INVALID"),
            peak_nlv_nano_usd=_optional_int(account.get("strategy_high_watermark_nlv_nano_usd"), "SIM_ACCOUNT_INVALID"),
            sticky_drawdown_lock_active=account.get(
                "sticky_drawdown_lock_active"
            ),
            settled_cash_nano_usd=_optional_int(account.get("settled_cash_nano_usd"), "SIM_ACCOUNT_INVALID"),
            robust_full_kelly_ppm=_optional_int(kelly.get("robust_full_kelly_ppm"), "SIM_KELLY_RECEIPT_INVALID"),
            max_loss_nano_usd_per_contract=economics.sizing_max_loss_nano_usd_per_contract,
            planned_loss_nano_usd_per_contract=economics.sizing_planned_loss_nano_usd_per_contract,
            entry_cash_nano_usd_per_contract=economics.stressed_entry_cost_nano_usd_per_contract,
            absolute_delta_ppm_per_contract=delta_ppm,
            underlying_spot_nano_usd=spot_nano_usd,
            contract_multiplier=100,
            current_gld_equivalent_delta_notional_nano_usd=_optional_int(
                account.get("current_gld_equivalent_delta_notional_nano_usd"),
                "SIM_ACCOUNT_INVALID",
            ),
            max_loss_capacity_contracts=hard_capacity,
            planned_loss_capacity_contracts=hard_capacity,
            liquidity_capacity_contracts=economics.liquidity_capacity,
        )
    )


def _expiry_date_text(expiry_utc_ns: int) -> str:
    return datetime.fromtimestamp(
        expiry_utc_ns // _NANO,
        tz=timezone.utc,
    ).astimezone(_NEW_YORK).date().isoformat()


def _candidate(
    *,
    carrier_id: str,
    selection: SimulationCarrierSelectionV1,
    economics: SimulationCarrierEconomicsV1,
    sizing: SizingResultV1,
    underlying_mid_nano_usd: int,
    selector_policy: dict[str, object],
) -> dict[str, object]:
    if sizing.state != "PASS" or type(sizing.quantity) is not int or sizing.quantity <= 0:
        raise SimulationPipelineError("SIM_CANDIDATE_SIZING_INVALID")
    quantity = sizing.quantity
    long_book = selection.long_quote.top_of_book
    expiry_date = _expiry_date_text(selection.long_quote.contract.expiry_utc_ns)
    long_moneyness = (
        "ITM"
        if selection.long_quote.contract.strike_nano_usd
        < underlying_mid_nano_usd
        else "ATM"
        if selection.long_quote.contract.strike_nano_usd
        == underlying_mid_nano_usd
        else "OTM"
    )
    legs: list[dict[str, object]] = [
        {
            "side": "BUY_TO_OPEN",
            "ratio": 1,
            "contract_id": selection.long_quote.contract.occ_symbol,
            "expiry_date": expiry_date,
            "expiry_utc_ns": selection.long_quote.contract.expiry_utc_ns,
            "strike_nano_usd": selection.long_quote.contract.strike_nano_usd,
            "bid_nano_usd": long_book.bid_nano_usd,
            "ask_nano_usd": long_book.ask_nano_usd,
            "bid_size": long_book.bid_size,
            "ask_size": long_book.ask_size,
            "coarse_delta_ppm": selection.long_coarse_delta_ppm,
            "delta_ppm": selection.long_delta_ppm,
            "fine_delta_ppm": selection.long_delta_ppm,
            "moneyness": long_moneyness,
        }
    ]
    net_delta_ppm_per_strategy_unit = selection.long_delta_ppm
    if carrier_id == "BCS0":
        short_book = selection.short_quote.top_of_book
        short_moneyness = (
            "ITM"
            if selection.short_quote.contract.strike_nano_usd
            < underlying_mid_nano_usd
            else "ATM"
            if selection.short_quote.contract.strike_nano_usd
            == underlying_mid_nano_usd
            else "OTM"
        )
        legs.append(
            {
                "side": "SELL_TO_OPEN",
                "ratio": 1,
                "contract_id": selection.short_quote.contract.occ_symbol,
                "expiry_date": expiry_date,
                "expiry_utc_ns": selection.short_quote.contract.expiry_utc_ns,
                "strike_nano_usd": selection.short_quote.contract.strike_nano_usd,
                "bid_nano_usd": short_book.bid_nano_usd,
                "ask_nano_usd": short_book.ask_nano_usd,
                "bid_size": short_book.bid_size,
                "ask_size": short_book.ask_size,
                "coarse_delta_ppm": selection.short_coarse_delta_ppm,
                "delta_ppm": selection.short_delta_ppm,
                "fine_delta_ppm": selection.short_delta_ppm,
                "moneyness": short_moneyness,
            }
        )
        net_delta_ppm_per_strategy_unit = selection.net_delta_ppm
    economics_document = economics.as_dict()
    economics_document[
        "modeled_expiry_max_profit_nano_usd_per_strategy_unit"
    ] = economics_document.pop(
        "theoretical_expiry_max_profit_nano_usd_per_contract"
    )
    selection_document = {
        "carrier_id": carrier_id,
        "h20_date": selection.h20_date,
        "legs": legs,
        "long_coarse_delta_ppm": selection.long_coarse_delta_ppm,
        "long_fine_delta_ppm": selection.long_delta_ppm,
        "minimum_expiry_date": selection.minimum_expiry_date,
        "selector_policy": selector_policy,
        "short_coarse_delta_ppm": (
            selection.short_coarse_delta_ppm
            if carrier_id == "BCS0"
            else None
        ),
        "short_fine_delta_ppm": (
            selection.short_delta_ppm if carrier_id == "BCS0" else None
        ),
        "underlying_mid_nano_usd": underlying_mid_nano_usd,
    }
    return {
        "carrier_id": carrier_id,
        "status": "PASS",
        "reason_code": "SIMULATED_CANDIDATE_ELIGIBLE",
        "quantity": quantity,
        "expiry_date": expiry_date,
        "h20_date": selection.h20_date,
        "minimum_expiry_date": selection.minimum_expiry_date,
        "underlying_mid_nano_usd": underlying_mid_nano_usd,
        "moneyness": long_moneyness,
        "selector_policy": selector_policy,
        "long_coarse_delta_ppm": selection.long_coarse_delta_ppm,
        "long_fine_delta_ppm": selection.long_delta_ppm,
        "short_coarse_delta_ppm": (
            selection.short_coarse_delta_ppm if carrier_id == "BCS0" else None
        ),
        "short_fine_delta_ppm": (
            selection.short_delta_ppm if carrier_id == "BCS0" else None
        ),
        "net_delta_ppm_per_strategy_unit": net_delta_ppm_per_strategy_unit,
        "max_loss_nano_usd": economics.sizing_max_loss_nano_usd_per_contract * quantity,
        "total_entry_cost_nano_usd": economics.base_entry_cost_nano_usd_per_contract * quantity,
        "base_entry_cost_nano_usd_per_contract": economics.base_entry_cost_nano_usd_per_contract,
        "stressed_entry_cost_nano_usd_per_contract": economics.stressed_entry_cost_nano_usd_per_contract,
        "sizing_loss_basis": "STRESSED_ENTRY_DEBIT_PLUS_PLANNED_EXIT_FEES_EQUALS_PLANNED_MAX_LOSS",
        "delta_notional_nano_usd_per_contract": sizing.per_contract_delta_notional_nano_usd,
        "gross_expiry_width_nano_usd_per_contract": economics.gross_expiry_width_nano_usd_per_contract,
        "modeled_expiry_max_profit_nano_usd_per_strategy_unit": economics.theoretical_expiry_max_profit_nano_usd_per_contract,
        "profit_cap_mode": (
            "CAPPED_AT_SHORT_STRIKE"
            if carrier_id == "BCS0"
            else "UNBOUNDED_BY_STRUCTURE"
        ),
        "profit_model_scope": (
            "THEORETICAL_EXPIRY_UNDER_SIZING_ASSUMPTIONS"
            if carrier_id == "BCS0"
            else "NOT_APPLICABLE_UNBOUNDED_STRUCTURE"
        ),
        "economics": economics_document,
        "capacities": sizing.capacities,
        "binding_constraints": list(sizing.binding_constraints),
        "selection_sha256": canonical_json_sha256(selection_document),
        "legs": legs,
    }


def _crr_not_run(status: str) -> dict[str, object]:
    return {
        "status": status,
        "requested": 0,
        "terminal": 0,
        "failure_count": 0,
        "model_id": MODEL_ID,
        "model_sha256": MODEL_SHA256,
    }


def _exit_plan(market: _NormalizedMarketV1) -> dict[str, object]:
    return {
        "fixed_take_profit": False,
        "gate_a_invalidation": "EXIT_REVIEW_REQUIRED_IF_SIM_A1_RANGE_HOLD_V1_FAILS",
        "expiry_safety": "FULL_EXIT_REQUIRED_BEFORE_CONTRACT_SAFETY_BOUNDARY",
        "latest_exit_horizon": "H20",
        "latest_full_exit_date": market.h20_date,
        "lc1_roll_up": "NOT_ACTIVE_IN_SIM_V1",
    }


def _decision(
    *,
    bundle_id: str,
    manifest_sha256: str,
    raw_content_sha256: str,
    market: _NormalizedMarketV1,
    status: str,
    reason_code: str,
    crr: dict[str, object],
    risk_context: dict[str, object],
    candidates: list[dict[str, object]],
) -> dict[str, object]:
    owner_required = status == "SIMULATED_OWNER_SELECTION_REQUIRED"
    if owner_required and [item.get("carrier_id") for item in candidates] != ["LC0", "BCS0"]:
        raise SimulationPipelineError("SIM_OWNER_SELECTION_CANDIDATES_INVALID")
    manual_control_by_status = {
        "SIMULATED_OWNER_SELECTION_REQUIRED": (
            "SIMULATION_SELECTION_IN_CODEX_ONLY"
        ),
        "SIMULATED_NO_ACTION": "NO_ACTION_NO_ORDER_AUTHORITY",
        "SIMULATED_NO_DECISION": "NO_DECISION_NO_ORDER_AUTHORITY",
    }
    try:
        manual_control = manual_control_by_status[status]
    except KeyError as exc:
        raise SimulationPipelineError("SIM_DECISION_STATUS_INVALID") from exc
    return {
        "schema_version": DECISION_SCHEMA_VERSION,
        "classification": SIMULATION_CLASSIFICATION,
        "decision_scope": "ENTRY_SIMULATION_ONLY",
        "status": status,
        "reason_code": reason_code,
        "actionable": False,
        "broker_order_count": 0,
        "owner_selection_required": owner_required,
        "bundle_id": bundle_id,
        "manifest_sha256": manifest_sha256,
        "raw_content_sha256": raw_content_sha256,
        "trading_date": market.trading_date,
        "cutoff_utc_ns": market.cutoff_utc_ns,
        "rule_package_version": PIPELINE_RULE_VERSION,
        "rule_sha256": market.rule_sha256,
        "gate_a": market.gate_result.as_dict(),
        "crr": crr,
        "risk_context": risk_context,
        "candidates": candidates,
        "exit_plan": _exit_plan(market),
        "manual_control": manual_control,
        "simulation_limitations": [
            "SYNTHETIC_INPUTS_ONLY",
            "NOT_PROMOTABLE_TO_LIVE_DECISION",
            "NO_BROKER_CONNECTION",
            "NO_ORDER_CREATION_OR_SUBMISSION",
            "LC1_ROLL_UP_NOT_ACTIVE",
        ],
    }


def evaluate_simulation_request(
    *,
    raw_documents: Mapping[str, object],
    bundle_id: str,
    manifest_sha256: str,
    raw_content_sha256: str,
    native_manifest_path: Path,
    expected_backend_evidence_sha256: str,
    deadline_monotonic_ns: int,
) -> SimulationEvaluationV1:
    """Evaluate one verified raw bundle inside the fixed-deadline child."""

    _deadline_guard(deadline_monotonic_ns)
    market = _normalize_market(raw_documents)
    gate = market.gate_result
    if gate.state == "FAIL":
        decision = _decision(
            bundle_id=bundle_id,
            manifest_sha256=manifest_sha256,
            raw_content_sha256=raw_content_sha256,
            market=market,
            status="SIMULATED_NO_ACTION",
            reason_code="GATE_A_COMPLETE_NOT_PASSED",
            crr=_crr_not_run("NOT_RUN_GATE_FAILED"),
            risk_context={"status": "NOT_RUN_GATE_FAILED"},
            candidates=[],
        )
        return _evaluation(decision, market)
    if gate.state != "PASS":
        decision = _decision(
            bundle_id=bundle_id,
            manifest_sha256=manifest_sha256,
            raw_content_sha256=raw_content_sha256,
            market=market,
            status="SIMULATED_NO_DECISION",
            reason_code="GATE_A_NOT_EVALUABLE",
            crr=_crr_not_run("NOT_RUN_GATE_NOT_EVALUABLE"),
            risk_context={"status": "NOT_RUN_GATE_NOT_EVALUABLE"},
            candidates=[],
        )
        return _evaluation(decision, market)

    market_status = _document(raw_documents, "market_status")
    option_universe_complete = _bool(
        market_status.get("option_universe_complete"),
        "SIM_MARKET_STATUS_INVALID",
    )
    quote_snapshot_complete = _bool(
        market_status.get("quote_snapshot_complete"),
        "SIM_MARKET_STATUS_INVALID",
    )
    if not option_universe_complete or not quote_snapshot_complete:
        decision = _decision(
            bundle_id=bundle_id,
            manifest_sha256=manifest_sha256,
            raw_content_sha256=raw_content_sha256,
            market=market,
            status="SIMULATED_NO_DECISION",
            reason_code="OPTION_DATA_INCOMPLETE",
            crr=_crr_not_run("NOT_RUN_OPTION_DATA_INCOMPLETE"),
            risk_context={"status": "NOT_RUN_OPTION_DATA_INCOMPLETE"},
            candidates=[],
        )
        return _evaluation(decision, market)

    account, kelly, missing_risk = _preflight_risk_facts(
        raw_documents,
        market=market,
    )
    if missing_risk:
        decision = _decision(
            bundle_id=bundle_id,
            manifest_sha256=manifest_sha256,
            raw_content_sha256=raw_content_sha256,
            market=market,
            status="SIMULATED_NO_DECISION",
            reason_code="RISK_FACT_MISSING",
            crr=_crr_not_run("NOT_RUN_RISK_FACT_MISSING"),
            risk_context={
                "status": "NO_DECISION",
                "reason_code": "SIZING_FACT_MISSING",
                "missing_fields": list(missing_risk),
            },
            candidates=[],
        )
        return _evaluation(decision, market)

    _deadline_guard(deadline_monotonic_ns)
    option_facts = _option_facts(
        raw_documents=raw_documents,
        raw_content_sha256=raw_content_sha256,
        bundle_id=bundle_id,
        market=market,
    )
    _deadline_guard(deadline_monotonic_ns)
    kernel = load_native_tree_v1(
        native_manifest_path,
        expected_backend_evidence_sha256=expected_backend_evidence_sha256,
    )
    compute, verify_call, combined_backend_sha256 = create_native_crr_delta_engine(kernel)
    run_batch, _, verify_batch = _create_exact_64_batch_engine(
        compute_call=compute,
        verify_call=verify_call,
        backend_evidence_sha256=combined_backend_sha256,
    )
    contracts = tuple(quote.contract for quote in option_facts.snapshot.option_quotes)
    bound_inputs = bind_crr_call_inputs_bulk_from_snapshot(
        option_facts.snapshot,
        contracts=contracts,
        pit_inputs=option_facts.pit_inputs,
    )
    batch = run_batch(bound_inputs)
    _deadline_guard(deadline_monotonic_ns)
    if verify_batch(batch) is not True:
        raise SimulationPipelineError("SIM_CRR_BATCH_UNVERIFIED")
    failures = tuple(item for item in batch.terminals if item.terminal_status != "PASS")
    crr_document = {
        "status": "EXACT64_PASS" if not failures else "EXACT64_FAIL_CLOSED",
        "requested": batch.requested,
        "bound": batch.bound,
        "started": batch.started,
        "terminal": batch.terminal,
        "failure_count": len(failures),
        "first_failure_ordinal": batch.first_failure_ordinal,
        "worker_count": batch.worker_count,
        "kernel_threads": batch.kernel_threads,
        "cache_hits": batch.cache_hits,
        "model_id": MODEL_ID,
        "model_sha256": MODEL_SHA256,
        "numerical_semantics_trust_boundary": (
            "SUPERVISED_CHILD_ATTESTED_PARENT_DID_NOT_RECOMPUTE_CRR_NUMERICS"
        ),
        "native_build_backend_evidence_sha256": expected_backend_evidence_sha256,
        "combined_backend_evidence_sha256": combined_backend_sha256,
        "input_vector_sha256": batch.input_vector_sha256,
        "semantic_output_sha256": batch.semantic_output_sha256,
        "execution_provenance_sha256": batch.execution_provenance_sha256,
        "early_exercise_terminal_count": sum(1 for item in batch.terminals if item.early_exercise_detected),
        "semantic_terminals": [item.as_dict() for item in batch.terminals],
    }
    if failures:
        decision = _decision(
            bundle_id=bundle_id,
            manifest_sha256=manifest_sha256,
            raw_content_sha256=raw_content_sha256,
            market=market,
            status="SIMULATED_NO_DECISION",
            reason_code=failures[0].reason_code,
            crr=crr_document,
            risk_context={"status": "NOT_RUN_CRR_FAILED"},
            candidates=[],
        )
        return _evaluation(decision, market)

    lc0_rule = _dict(market.rule.get("lc0"), "SIM_LC0_RULE_INVALID")
    bcs_rule = _dict(market.rule.get("bcs"), "SIM_BCS_RULE_INVALID")
    risk_rule = _dict(market.rule.get("risk"), "SIM_RISK_RULE_INVALID")
    selection = select_lc0_and_bcs(
        snapshot=option_facts.snapshot,
        terminals=batch.terminals,
        h20_date=market.h20_date,
        minimum_days_after_h20=_int(lc0_rule.get("minimum_calendar_days_after_h20"), "SIM_LC0_RULE_INVALID"),
        lc0_target_delta_ppm=_int(lc0_rule.get("target_delta_ppm"), "SIM_LC0_RULE_INVALID"),
        lc0_minimum_delta_ppm=_int(lc0_rule.get("minimum_delta_ppm"), "SIM_LC0_RULE_INVALID"),
        lc0_maximum_delta_ppm=_int(lc0_rule.get("maximum_delta_ppm"), "SIM_LC0_RULE_INVALID"),
    )
    bcs_minimum = _int(
        bcs_rule.get("short_minimum_delta_ppm"),
        "SIM_BCS_RULE_INVALID",
    )
    bcs_maximum = _int(
        bcs_rule.get("short_maximum_delta_ppm"),
        "SIM_BCS_RULE_INVALID",
    )
    if not (
        bcs_minimum
        <= selection.short_coarse_delta_ppm
        <= bcs_maximum
        and bcs_minimum <= selection.short_delta_ppm <= bcs_maximum
    ):
        raise SimulationPipelineError("SIM_BCS_SELECTOR_RULE_BINDING_INVALID")
    selector_policy = {
        "candidate_terminal_policy": risk_rule.get(
            "candidate_terminal_policy"
        ),
        "lc0": lc0_rule,
        "bcs": bcs_rule,
        "coarse_fine_agreement_required": True,
        "second_best_fallback_allowed": False,
    }
    lc0_binding = bind_lc0_selection_for_research(
        signal=option_facts.signal,
        snapshot=option_facts.snapshot,
        selector_contract_sha256=canonical_json_sha256(lc0_rule),
        long_call_id=selection.long_quote.contract.occ_symbol,
    )
    lc0_economics, bcs_economics = price_simulation_carriers(
        selection=selection,
        snapshot=option_facts.snapshot,
        fees=option_facts.fees,
        long_entry_adverse_ticks=_int(
            risk_rule.get("entry_stress_long_ticks"),
            "SIM_RISK_RULE_INVALID",
        ),
        short_entry_adverse_ticks=_int(
            risk_rule.get("entry_stress_short_ticks"),
            "SIM_RISK_RULE_INVALID",
        ),
        include_exit_fees_in_max_loss=_bool(
            risk_rule.get("include_exit_fees_in_max_loss"),
            "SIM_RISK_RULE_INVALID",
        ),
    )
    spot_total = option_facts.snapshot.underlying_top.bid_nano_usd + option_facts.snapshot.underlying_top.ask_nano_usd
    if spot_total % 2:
        raise SimulationPipelineError("DELTA_INPUT_MIDPOINT_NON_INTEGRAL")
    spot = spot_total // 2
    lc0_sizing = _sizing(
        account=account,
        kelly=kelly,
        economics=lc0_economics,
        delta_ppm=selection.long_delta_ppm,
        spot_nano_usd=spot,
    )
    bcs_sizing = _sizing(
        account=account,
        kelly=kelly,
        economics=bcs_economics,
        delta_ppm=selection.net_delta_ppm,
        spot_nano_usd=spot,
    )
    risk_context = {
        "status": "PASS" if lc0_sizing.state == bcs_sizing.state == "PASS" else "BLOCKED",
        "policy_id": "SIM_HALF_KELLY_RISK_V1",
        "candidate_terminal_policy": "BOTH_LC0_AND_BCS_MUST_PASS",
        "account_as_of_utc_ns": account.get("as_of_utc_ns"),
        "sticky_drawdown_lock_active": account.get(
            "sticky_drawdown_lock_active"
        ),
        "frozen_kelly_receipt_id": kelly.get("receipt_id"),
        "frozen_kelly_strategy_rule_package_id": kelly.get(
            "strategy_rule_package_id"
        ),
        "frozen_kelly_applicable_carriers": kelly.get(
            "applicable_carriers"
        ),
        "frozen_kelly_return_distribution_id": kelly.get(
            "return_distribution_id"
        ),
        "eligible_bankroll_nano_usd": lc0_sizing.eligible_bankroll_nano_usd,
        "full_kelly_ppm": lc0_sizing.full_kelly_ppm,
        "half_kelly_ppm": lc0_sizing.half_kelly_ppm,
        "drawdown_ppm": lc0_sizing.drawdown_ppm,
        "drawdown_factor_ppm": lc0_sizing.drawdown_factor_ppm,
        "positive_realized_profit_reinvestment_ppm": 500_000,
        "realized_loss_effect_ppm": 1_000_000,
        "unrealized_profit_expands_bankroll": False,
        "minimum_post_trade_settled_cash_nlv_ppm": 100_000,
        "maximum_gld_equivalent_delta_notional_bankroll_ppm": 1_500_000,
        "separate_max_loss_budget_mode": "ELIGIBLE_BANKROLL_HARD_CEILING_SIMULATION_ONLY",
        "planned_loss_equals_maximum_loss": True,
        "lc0_sizing": lc0_sizing.as_dict(),
        "bcs_sizing": bcs_sizing.as_dict(),
    }
    if "NO_DECISION" in {lc0_sizing.state, bcs_sizing.state}:
        decision = _decision(
            bundle_id=bundle_id,
            manifest_sha256=manifest_sha256,
            raw_content_sha256=raw_content_sha256,
            market=market,
            status="SIMULATED_NO_DECISION",
            reason_code="RISK_OR_QUANTITY_NOT_EVALUABLE",
            crr=crr_document,
            risk_context=risk_context,
            candidates=[],
        )
        return _evaluation(decision, market)
    if "NO_ACTION" in {lc0_sizing.state, bcs_sizing.state}:
        no_action_reasons = {
            lc0_sizing.reason_code,
            bcs_sizing.reason_code,
        }
        if "SIZING_STICKY_DRAWDOWN_LOCK_ACTIVE" in no_action_reasons:
            top_level_reason = "RISK_STICKY_DRAWDOWN_LOCK_ACTIVE"
        elif "SIZING_DRAWDOWN_EXIT_MANAGED" in no_action_reasons:
            top_level_reason = "RISK_DRAWDOWN_EXIT_MANAGED"
        else:
            top_level_reason = "RISK_CAPACITY_BLOCKS_ENTRY"
        decision = _decision(
            bundle_id=bundle_id,
            manifest_sha256=manifest_sha256,
            raw_content_sha256=raw_content_sha256,
            market=market,
            status="SIMULATED_NO_ACTION",
            reason_code=top_level_reason,
            crr=crr_document,
            risk_context=risk_context,
            candidates=[],
        )
        return _evaluation(decision, market)

    candidates = [
        _candidate(
            carrier_id="LC0",
            selection=selection,
            economics=lc0_economics,
            sizing=lc0_sizing,
            underlying_mid_nano_usd=spot,
            selector_policy=selector_policy,
        ),
        _candidate(
            carrier_id="BCS0",
            selection=selection,
            economics=bcs_economics,
            sizing=bcs_sizing,
            underlying_mid_nano_usd=spot,
            selector_policy=selector_policy,
        ),
    ]
    decision = _decision(
        bundle_id=bundle_id,
        manifest_sha256=manifest_sha256,
        raw_content_sha256=raw_content_sha256,
        market=market,
        status="SIMULATED_OWNER_SELECTION_REQUIRED",
        reason_code="BOTH_CARRIERS_PASS_OWNER_SELECTION_REQUIRED",
        crr=crr_document,
        risk_context=risk_context,
        candidates=candidates,
    )
    _deadline_guard(deadline_monotonic_ns)
    return _evaluation(decision, market)


def _evaluation(
    decision: dict[str, object],
    market: _NormalizedMarketV1,
) -> SimulationEvaluationV1:
    receipt = {
        "schema_version": PIPELINE_RECEIPT_SCHEMA_VERSION,
        "classification": SIMULATION_CLASSIFICATION,
        "decision_semantic_sha256": canonical_json_sha256(decision),
        "raw_content_sha256": decision["raw_content_sha256"],
        "manifest_sha256": decision["manifest_sha256"],
        "rule_sha256": market.rule_sha256,
        "gate_input_fact_sha256": market.gate_result.input_fact_sha256,
        "gate_state": market.gate_result.state,
        "crr_semantic_output_sha256": _dict(decision["crr"], "SIM_CRR_RECEIPT_INVALID").get("semantic_output_sha256"),
        "status": decision["status"],
        "actionable": False,
        "broker_order_count": 0,
    }
    return SimulationEvaluationV1(
        decision_document=decision,
        pipeline_receipt_document=receipt,
    )


def minimal_no_decision_document(
    *,
    raw_documents: Mapping[str, object],
    bundle_id: str,
    manifest_sha256: str,
    raw_content_sha256: str,
    reason_code: str,
) -> dict[str, object]:
    """Build a deterministic fallback only when the core cannot form facts."""

    public_reason_code = (
        reason_code
        if type(reason_code) is str
        and reason_code in SAFE_FAIL_CLOSED_REASON_CODES
        else FAIL_CLOSED_REASON_CODE
    )
    status = raw_documents.get("market_status")
    rule = raw_documents.get("rule_package")
    status_document = status if type(status) is dict else {}
    rule_document = rule if type(rule) is dict else {}
    return {
        "schema_version": DECISION_SCHEMA_VERSION,
        "classification": SIMULATION_CLASSIFICATION,
        "decision_scope": "ENTRY_SIMULATION_ONLY",
        "status": "SIMULATED_NO_DECISION",
        "reason_code": public_reason_code,
        "actionable": False,
        "broker_order_count": 0,
        "owner_selection_required": False,
        "bundle_id": bundle_id,
        "manifest_sha256": manifest_sha256,
        "raw_content_sha256": raw_content_sha256,
        "trading_date": status_document.get("trading_date"),
        "cutoff_utc_ns": _fallback_cutoff_utc_ns(
            status_document.get("as_of_utc_ns")
        ),
        "rule_package_version": rule_document.get("rule_package_id"),
        "rule_sha256": canonical_json_sha256(rule_document),
        "gate_a": {
            "state": "NOT_EVALUABLE",
            "reason_code": public_reason_code,
            "predicates": {},
            "metrics": {},
        },
        "crr": _crr_not_run("NOT_RUN_PIPELINE_FAILED_CLOSED"),
        "risk_context": {"status": "NOT_RUN_PIPELINE_FAILED_CLOSED"},
        "candidates": [],
        "exit_plan": {
            "fixed_take_profit": False,
            "latest_exit_horizon": "H20_NOT_EVALUABLE",
            "lc1_roll_up": "NOT_ACTIVE_IN_SIM_V1",
        },
        "manual_control": "NO_DECISION_NO_ORDER_AUTHORITY",
        "simulation_limitations": [
            "SYNTHETIC_INPUTS_ONLY",
            "PIPELINE_FAILED_CLOSED",
            "NO_BROKER_CONNECTION",
            "NO_ORDER_CREATION_OR_SUBMISSION",
        ],
    }


def _fallback_cutoff_utc_ns(value: object) -> int | None:
    """Keep only a timestamp that can cross the canonical artifact boundary."""

    if type(value) is int and 0 <= value < _MAX_CANONICAL_INTEGER:
        return value
    return None


__all__ = [
    "DECISION_SCHEMA_VERSION",
    "FAIL_CLOSED_REASON_CODE",
    "SAFE_FAIL_CLOSED_REASON_CODES",
    "PIPELINE_RECEIPT_SCHEMA_VERSION",
    "PIPELINE_RULE_VERSION",
    "SIMULATION_CLASSIFICATION",
    "SimulationEvaluationV1",
    "SimulationPipelineError",
    "evaluate_simulation_request",
    "minimal_no_decision_document",
]
