"""Frozen, integer-only contracts for the isolated GLD simulation workflow.

These contracts carry normalized synthetic facts.  They do not authorize live
market data, broker access, order construction, or order submission.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re

from gld_normalizer.errors import NormalizationError


MAX_I64 = 2**63 - 1
MAX_U32 = 2**32 - 2
PPM = 1_000_000
GATE_A_RULE_VERSION = "SIM_A1_RANGE_HOLD_V1"

_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_STATE_RE = re.compile(r"(?:PASS|FAIL|NOT_EVALUABLE|NO_ACTION|NO_DECISION)\Z")
_BINDING_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")


def _exact_int(
    value: object,
    *,
    reason_code: str,
    minimum: int = 0,
    maximum: int = MAX_I64,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise NormalizationError(reason_code)
    return value


def _optional_exact_int(
    value: object,
    *,
    reason_code: str,
    minimum: int = 0,
    maximum: int = MAX_I64,
) -> int | None:
    if value is None:
        return None
    return _exact_int(
        value,
        reason_code=reason_code,
        minimum=minimum,
        maximum=maximum,
    )


def _exact_bool(value: object, *, reason_code: str) -> bool:
    if type(value) is not bool:
        raise NormalizationError(reason_code)
    return value


def _reason(value: object) -> str:
    if type(value) is not str or _REASON_RE.fullmatch(value) is None:
        raise NormalizationError("SIMULATION_REASON_INVALID")
    return value


def _state(value: object) -> str:
    if type(value) is not str or _STATE_RE.fullmatch(value) is None:
        raise NormalizationError("SIMULATION_STATE_INVALID")
    return value


def _binding(value: object) -> str:
    if type(value) is not str or _BINDING_RE.fullmatch(value) is None:
        raise NormalizationError("SIZING_RESULT_INVALID")
    return value


def canonical_document_sha256(document: object) -> str:
    """Hash a JSON-safe integer-only document with stable ASCII encoding."""

    try:
        encoded = json.dumps(
            document,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, UnicodeEncodeError, ValueError) as exc:
        raise NormalizationError("SIMULATION_CANONICAL_DOCUMENT_INVALID") from exc
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class DailyGateBarV1:
    """One normalized, already-session-qualified daily bar."""

    session_ordinal: int
    high_nano_usd: int | None
    close_nano_usd: int | None
    sma50_nano_usd: int | None
    sma200_nano_usd: int | None
    sma50_sum_nano_usd: int | None
    sma200_sum_nano_usd: int | None
    complete: bool
    max_event_utc_ns: int
    max_receive_utc_ns: int

    def __post_init__(self) -> None:
        _exact_int(
            self.session_ordinal,
            reason_code="GATE_DAILY_BAR_INVALID",
            minimum=1,
            maximum=MAX_U32,
        )
        for value in (
            self.high_nano_usd,
            self.close_nano_usd,
            self.sma50_nano_usd,
            self.sma200_nano_usd,
            self.sma50_sum_nano_usd,
            self.sma200_sum_nano_usd,
        ):
            _optional_exact_int(
                value,
                reason_code="GATE_DAILY_BAR_INVALID",
                minimum=1,
            )
        _exact_bool(self.complete, reason_code="GATE_DAILY_BAR_INVALID")
        for value in (self.max_event_utc_ns, self.max_receive_utc_ns):
            _exact_int(value, reason_code="GATE_DAILY_BAR_INVALID")
        if self.max_receive_utc_ns < self.max_event_utc_ns:
            raise NormalizationError("GATE_DAILY_BAR_INVALID")

    def as_dict(self) -> dict[str, object]:
        return {
            "close_nano_usd": self.close_nano_usd,
            "complete": self.complete,
            "high_nano_usd": self.high_nano_usd,
            "max_event_utc_ns": self.max_event_utc_ns,
            "max_receive_utc_ns": self.max_receive_utc_ns,
            "session_ordinal": self.session_ordinal,
            "sma200_nano_usd": self.sma200_nano_usd,
            "sma200_sum_nano_usd": self.sma200_sum_nano_usd,
            "sma50_nano_usd": self.sma50_nano_usd,
            "sma50_sum_nano_usd": self.sma50_sum_nano_usd,
        }


@dataclass(frozen=True, slots=True)
class MinuteGateBarV1:
    """One normalized minute close; ordinal is minute-of-day in New York."""

    minute_ending_ordinal: int
    close_nano_usd: int | None
    complete: bool
    max_event_utc_ns: int
    max_receive_utc_ns: int

    def __post_init__(self) -> None:
        _exact_int(
            self.minute_ending_ordinal,
            reason_code="GATE_MINUTE_BAR_INVALID",
            minimum=0,
            maximum=1_439,
        )
        _optional_exact_int(
            self.close_nano_usd,
            reason_code="GATE_MINUTE_BAR_INVALID",
            minimum=1,
        )
        _exact_bool(self.complete, reason_code="GATE_MINUTE_BAR_INVALID")
        for value in (self.max_event_utc_ns, self.max_receive_utc_ns):
            _exact_int(value, reason_code="GATE_MINUTE_BAR_INVALID")
        if self.max_receive_utc_ns < self.max_event_utc_ns:
            raise NormalizationError("GATE_MINUTE_BAR_INVALID")

    def as_dict(self) -> dict[str, object]:
        return {
            "close_nano_usd": self.close_nano_usd,
            "complete": self.complete,
            "max_event_utc_ns": self.max_event_utc_ns,
            "max_receive_utc_ns": self.max_receive_utc_ns,
            "minute_ending_ordinal": self.minute_ending_ordinal,
        }


@dataclass(frozen=True, slots=True)
class GateAInputV1:
    """Raw normalized Gate A inputs; it intentionally has no signal state."""

    cutoff_utc_ns: int
    daily_bars: tuple[DailyGateBarV1, ...]
    minute_bars: tuple[MinuteGateBarV1, ...]

    def __post_init__(self) -> None:
        _exact_int(self.cutoff_utc_ns, reason_code="GATE_A_INPUT_INVALID")
        if (
            type(self.daily_bars) is not tuple
            or len(self.daily_bars) > 512
            or any(not isinstance(item, DailyGateBarV1) for item in self.daily_bars)
            or type(self.minute_bars) is not tuple
            or len(self.minute_bars) > 1_440
            or any(not isinstance(item, MinuteGateBarV1) for item in self.minute_bars)
        ):
            raise NormalizationError("GATE_A_INPUT_INVALID")

    def as_dict(self) -> dict[str, object]:
        return {
            "cutoff_utc_ns": self.cutoff_utc_ns,
            "daily_bars": [item.as_dict() for item in self.daily_bars],
            "minute_bars": [item.as_dict() for item in self.minute_bars],
        }

    @property
    def input_fact_sha256(self) -> str:
        return canonical_document_sha256(self.as_dict())


@dataclass(frozen=True, slots=True)
class GateAResultV1:
    rule_version: str
    state: str
    reason_code: str
    input_fact_sha256: str
    max_event_utc_ns: int | None
    max_receive_utc_ns: int | None
    prior_close_nano_usd: int | None
    current_sma50_nano_usd: int | None
    current_sma200_nano_usd: int | None
    sma50_20_sessions_ago_nano_usd: int | None
    breakout_nano_usd: int | None
    minute_1044_close_nano_usd: int | None
    range_hold_above_count: int | None
    range_hold_required_count: int
    prior_close_gt_sma50: bool | None
    sma50_gt_sma200: bool | None
    sma50_rising_20_sessions: bool | None
    minute_1044_close_gt_breakout: bool | None
    range_hold_count_met: bool | None

    def __post_init__(self) -> None:
        if self.rule_version != GATE_A_RULE_VERSION:
            raise NormalizationError("GATE_A_RESULT_INVALID")
        _state(self.state)
        _reason(self.reason_code)
        if (
            type(self.input_fact_sha256) is not str
            or len(self.input_fact_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.input_fact_sha256)
        ):
            raise NormalizationError("GATE_A_RESULT_INVALID")
        for value in (
            self.max_event_utc_ns,
            self.max_receive_utc_ns,
            self.prior_close_nano_usd,
            self.current_sma50_nano_usd,
            self.current_sma200_nano_usd,
            self.sma50_20_sessions_ago_nano_usd,
            self.breakout_nano_usd,
            self.minute_1044_close_nano_usd,
            self.range_hold_above_count,
        ):
            _optional_exact_int(value, reason_code="GATE_A_RESULT_INVALID")
        _exact_int(
            self.range_hold_required_count,
            reason_code="GATE_A_RESULT_INVALID",
            minimum=1,
            maximum=15,
        )
        for value in (
            self.prior_close_gt_sma50,
            self.sma50_gt_sma200,
            self.sma50_rising_20_sessions,
            self.minute_1044_close_gt_breakout,
            self.range_hold_count_met,
        ):
            if value is not None:
                _exact_bool(value, reason_code="GATE_A_RESULT_INVALID")

    @property
    def predicates(self) -> dict[str, object]:
        return {
            "minute_1044_close_gt_breakout": self.minute_1044_close_gt_breakout,
            "prior_close_gt_sma50": self.prior_close_gt_sma50,
            "range_hold_count_met": self.range_hold_count_met,
            "sma50_gt_sma200": self.sma50_gt_sma200,
            "sma50_rising_20_sessions": self.sma50_rising_20_sessions,
        }

    @property
    def metrics(self) -> dict[str, object]:
        return {
            "breakout_nano_usd": self.breakout_nano_usd,
            "current_sma200_nano_usd": self.current_sma200_nano_usd,
            "current_sma50_nano_usd": self.current_sma50_nano_usd,
            "minute_1044_close_nano_usd": self.minute_1044_close_nano_usd,
            "prior_close_nano_usd": self.prior_close_nano_usd,
            "range_hold_above_count": self.range_hold_above_count,
            "range_hold_required_count": self.range_hold_required_count,
            "sma50_20_sessions_ago_nano_usd": self.sma50_20_sessions_ago_nano_usd,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "input_fact_sha256": self.input_fact_sha256,
            "max_event_utc_ns": self.max_event_utc_ns,
            "max_receive_utc_ns": self.max_receive_utc_ns,
            "metrics": self.metrics,
            "predicates": self.predicates,
            "reason_code": self.reason_code,
            "rule_version": self.rule_version,
            "state": self.state,
        }


@dataclass(frozen=True, slots=True)
class SizingInputV1:
    """Frozen synthetic account, Kelly receipt, and carrier risk facts."""

    initial_bankroll_nano_usd: int | None
    cumulative_realized_positive_nano_usd: int | None
    cumulative_realized_loss_nano_usd: int | None
    unrealized_profit_nano_usd: int | None
    current_nlv_nano_usd: int | None
    peak_nlv_nano_usd: int | None
    sticky_drawdown_lock_active: bool | None
    settled_cash_nano_usd: int | None
    robust_full_kelly_ppm: int | None
    max_loss_nano_usd_per_contract: int | None
    planned_loss_nano_usd_per_contract: int | None
    entry_cash_nano_usd_per_contract: int | None
    absolute_delta_ppm_per_contract: int | None
    underlying_spot_nano_usd: int | None
    contract_multiplier: int | None
    current_gld_equivalent_delta_notional_nano_usd: int | None
    max_loss_capacity_contracts: int | None
    planned_loss_capacity_contracts: int | None
    liquidity_capacity_contracts: int | None

    def __post_init__(self) -> None:
        money_fields = (
            self.initial_bankroll_nano_usd,
            self.cumulative_realized_positive_nano_usd,
            self.cumulative_realized_loss_nano_usd,
            self.unrealized_profit_nano_usd,
            self.current_nlv_nano_usd,
            self.peak_nlv_nano_usd,
            self.settled_cash_nano_usd,
            self.max_loss_nano_usd_per_contract,
            self.planned_loss_nano_usd_per_contract,
            self.entry_cash_nano_usd_per_contract,
            self.underlying_spot_nano_usd,
            self.current_gld_equivalent_delta_notional_nano_usd,
        )
        for value in money_fields:
            _optional_exact_int(value, reason_code="SIZING_INPUT_INVALID")
        if self.sticky_drawdown_lock_active is not None:
            _exact_bool(
                self.sticky_drawdown_lock_active,
                reason_code="SIZING_INPUT_INVALID",
            )
        _optional_exact_int(
            self.robust_full_kelly_ppm,
            reason_code="SIZING_INPUT_INVALID",
            maximum=PPM,
        )
        _optional_exact_int(
            self.absolute_delta_ppm_per_contract,
            reason_code="SIZING_INPUT_INVALID",
            maximum=PPM,
        )
        _optional_exact_int(
            self.contract_multiplier,
            reason_code="SIZING_INPUT_INVALID",
            minimum=1,
            maximum=10_000,
        )
        for value in (
            self.max_loss_capacity_contracts,
            self.planned_loss_capacity_contracts,
            self.liquidity_capacity_contracts,
        ):
            _optional_exact_int(
                value,
                reason_code="SIZING_INPUT_INVALID",
                maximum=MAX_U32,
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "absolute_delta_ppm_per_contract": self.absolute_delta_ppm_per_contract,
            "contract_multiplier": self.contract_multiplier,
            "current_gld_equivalent_delta_notional_nano_usd": self.current_gld_equivalent_delta_notional_nano_usd,
            "cumulative_realized_loss_nano_usd": self.cumulative_realized_loss_nano_usd,
            "cumulative_realized_positive_nano_usd": self.cumulative_realized_positive_nano_usd,
            "current_nlv_nano_usd": self.current_nlv_nano_usd,
            "entry_cash_nano_usd_per_contract": self.entry_cash_nano_usd_per_contract,
            "initial_bankroll_nano_usd": self.initial_bankroll_nano_usd,
            "liquidity_capacity_contracts": self.liquidity_capacity_contracts,
            "max_loss_capacity_contracts": self.max_loss_capacity_contracts,
            "max_loss_nano_usd_per_contract": self.max_loss_nano_usd_per_contract,
            "peak_nlv_nano_usd": self.peak_nlv_nano_usd,
            "planned_loss_capacity_contracts": self.planned_loss_capacity_contracts,
            "planned_loss_nano_usd_per_contract": self.planned_loss_nano_usd_per_contract,
            "robust_full_kelly_ppm": self.robust_full_kelly_ppm,
            "settled_cash_nano_usd": self.settled_cash_nano_usd,
            "sticky_drawdown_lock_active": self.sticky_drawdown_lock_active,
            "underlying_spot_nano_usd": self.underlying_spot_nano_usd,
            "unrealized_profit_nano_usd": self.unrealized_profit_nano_usd,
        }

    @property
    def input_fact_sha256(self) -> str:
        return canonical_document_sha256(self.as_dict())


@dataclass(frozen=True, slots=True)
class SizingResultV1:
    state: str
    reason_code: str
    quantity: int | None
    eligible_bankroll_nano_usd: int | None
    full_kelly_ppm: int | None
    half_kelly_ppm: int | None
    drawdown_ppm: int | None
    drawdown_factor_ppm: int | None
    per_contract_max_loss_nano_usd: int | None
    per_contract_delta_notional_nano_usd: int | None
    kelly_drawdown_capacity_contracts: int | None
    max_loss_capacity_contracts: int | None
    planned_loss_capacity_contracts: int | None
    delta_capacity_contracts: int | None
    cash_capacity_contracts: int | None
    liquidity_capacity_contracts: int | None
    binding_constraints: tuple[str, ...]
    missing_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        _state(self.state)
        _reason(self.reason_code)
        for value in (
            self.quantity,
            self.eligible_bankroll_nano_usd,
            self.full_kelly_ppm,
            self.half_kelly_ppm,
            self.drawdown_ppm,
            self.drawdown_factor_ppm,
            self.per_contract_max_loss_nano_usd,
            self.per_contract_delta_notional_nano_usd,
            self.kelly_drawdown_capacity_contracts,
            self.max_loss_capacity_contracts,
            self.planned_loss_capacity_contracts,
            self.delta_capacity_contracts,
            self.cash_capacity_contracts,
            self.liquidity_capacity_contracts,
        ):
            _optional_exact_int(value, reason_code="SIZING_RESULT_INVALID")
        if (
            type(self.binding_constraints) is not tuple
            or any(_binding(item) != item for item in self.binding_constraints)
            or type(self.missing_fields) is not tuple
            or any(
                type(item) is not str
                or not item
                or not item.isascii()
                or item != item.strip()
                for item in self.missing_fields
            )
        ):
            raise NormalizationError("SIZING_RESULT_INVALID")

    @property
    def status(self) -> str:
        """Compatibility alias for callers that use terminal-status wording."""

        return self.state

    @property
    def binding_capacity(self) -> str | None:
        return self.binding_constraints[0] if self.binding_constraints else None

    @property
    def capacities(self) -> dict[str, object]:
        return {
            "cash": self.cash_capacity_contracts,
            "delta_notional": self.delta_capacity_contracts,
            "kelly_drawdown": self.kelly_drawdown_capacity_contracts,
            "liquidity": self.liquidity_capacity_contracts,
            "max_loss": self.max_loss_capacity_contracts,
            "planned_loss": self.planned_loss_capacity_contracts,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "binding_constraints": list(self.binding_constraints),
            "capacities": self.capacities,
            "drawdown_factor_ppm": self.drawdown_factor_ppm,
            "drawdown_ppm": self.drawdown_ppm,
            "eligible_bankroll_nano_usd": self.eligible_bankroll_nano_usd,
            "full_kelly_ppm": self.full_kelly_ppm,
            "half_kelly_ppm": self.half_kelly_ppm,
            "missing_fields": list(self.missing_fields),
            "per_contract_delta_notional_nano_usd": self.per_contract_delta_notional_nano_usd,
            "per_contract_max_loss_nano_usd": self.per_contract_max_loss_nano_usd,
            "quantity": self.quantity,
            "reason_code": self.reason_code,
            "state": self.state,
        }


__all__ = [
    "DailyGateBarV1",
    "GATE_A_RULE_VERSION",
    "GateAInputV1",
    "GateAResultV1",
    "MinuteGateBarV1",
    "PPM",
    "SizingInputV1",
    "SizingResultV1",
    "canonical_document_sha256",
]
