"""Pure integer-only Half-Kelly and capacity sizing for simulation artifacts."""

from __future__ import annotations

from .contracts import PPM, SizingInputV1, SizingResultV1


_CASH_RESERVE_PPM = 100_000
_DELTA_NOTIONAL_LIMIT_PPM = 1_500_000
_DRAWDOWN_5_PPM = 50_000
_DRAWDOWN_10_PPM = 100_000
_DRAWDOWN_15_PPM = 150_000
_DRAWDOWN_20_PPM = 200_000
_DRAWDOWN_30_PPM = 300_000

_REQUIRED_FIELDS = (
    "initial_bankroll_nano_usd",
    "cumulative_realized_positive_nano_usd",
    "cumulative_realized_loss_nano_usd",
    "current_nlv_nano_usd",
    "peak_nlv_nano_usd",
    "sticky_drawdown_lock_active",
    "settled_cash_nano_usd",
    "robust_full_kelly_ppm",
    "max_loss_nano_usd_per_contract",
    "planned_loss_nano_usd_per_contract",
    "entry_cash_nano_usd_per_contract",
    "absolute_delta_ppm_per_contract",
    "underlying_spot_nano_usd",
    "contract_multiplier",
    "current_gld_equivalent_delta_notional_nano_usd",
    "max_loss_capacity_contracts",
    "planned_loss_capacity_contracts",
    "liquidity_capacity_contracts",
)


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


def _empty_result(
    *,
    state: str,
    reason_code: str,
    missing_fields: tuple[str, ...] = (),
) -> SizingResultV1:
    return SizingResultV1(
        state=state,
        reason_code=reason_code,
        quantity=None,
        eligible_bankroll_nano_usd=None,
        full_kelly_ppm=None,
        half_kelly_ppm=None,
        drawdown_ppm=None,
        drawdown_factor_ppm=None,
        per_contract_max_loss_nano_usd=None,
        per_contract_delta_notional_nano_usd=None,
        kelly_drawdown_capacity_contracts=None,
        max_loss_capacity_contracts=None,
        planned_loss_capacity_contracts=None,
        delta_capacity_contracts=None,
        cash_capacity_contracts=None,
        liquidity_capacity_contracts=None,
        binding_constraints=(),
        missing_fields=missing_fields,
    )


def _drawdown_factor(drawdown_ppm: int) -> int:
    if drawdown_ppm <= _DRAWDOWN_5_PPM:
        return PPM
    if drawdown_ppm <= _DRAWDOWN_10_PPM:
        return 800_000
    if drawdown_ppm < _DRAWDOWN_15_PPM:
        return 400_000
    return 0


def _drawdown_reason(drawdown_ppm: int) -> str:
    if drawdown_ppm >= _DRAWDOWN_30_PPM:
        return "SIZING_DRAWDOWN_DISASTER_LOCK"
    if drawdown_ppm >= _DRAWDOWN_20_PPM:
        return "SIZING_DRAWDOWN_EXIT_MANAGED"
    return "SIZING_DRAWDOWN_BLOCKS_NEW_ENTRY"


def size_position(source: SizingInputV1) -> SizingResultV1:
    """Return the minimum safe integer capacity or fail closed.

    The robust full-Kelly receipt is an input fact.  This function only halves
    that frozen fraction; it never estimates or retrains Kelly online.
    """

    missing_fields = tuple(
        field_name
        for field_name in _REQUIRED_FIELDS
        if getattr(source, field_name) is None
    )
    if missing_fields:
        return _empty_result(
            state="NO_DECISION",
            reason_code="SIZING_FACT_MISSING",
            missing_fields=missing_fields,
        )

    initial = source.initial_bankroll_nano_usd
    realized_positive = source.cumulative_realized_positive_nano_usd
    realized_loss = source.cumulative_realized_loss_nano_usd
    current_nlv = source.current_nlv_nano_usd
    peak_nlv = source.peak_nlv_nano_usd
    sticky_drawdown_lock_active = source.sticky_drawdown_lock_active
    settled_cash = source.settled_cash_nano_usd
    full_kelly = source.robust_full_kelly_ppm
    per_contract_max_loss = source.max_loss_nano_usd_per_contract
    per_contract_planned_loss = source.planned_loss_nano_usd_per_contract
    per_contract_cash = source.entry_cash_nano_usd_per_contract
    delta_ppm = source.absolute_delta_ppm_per_contract
    spot = source.underlying_spot_nano_usd
    multiplier = source.contract_multiplier
    current_delta_notional = (
        source.current_gld_equivalent_delta_notional_nano_usd
    )
    max_loss_capacity = source.max_loss_capacity_contracts
    planned_loss_capacity = source.planned_loss_capacity_contracts
    liquidity_capacity = source.liquidity_capacity_contracts
    assert initial is not None
    assert realized_positive is not None
    assert realized_loss is not None
    assert current_nlv is not None
    assert peak_nlv is not None
    assert sticky_drawdown_lock_active is not None
    assert settled_cash is not None
    assert full_kelly is not None
    assert per_contract_max_loss is not None
    assert per_contract_planned_loss is not None
    assert per_contract_cash is not None
    assert delta_ppm is not None
    assert spot is not None
    assert multiplier is not None
    assert current_delta_notional is not None
    assert max_loss_capacity is not None
    assert planned_loss_capacity is not None
    assert liquidity_capacity is not None

    if (
        peak_nlv == 0
        or current_nlv > peak_nlv
        or per_contract_max_loss == 0
        or per_contract_cash == 0
        or spot == 0
        or delta_ppm == 0
    ):
        return _empty_result(
            state="NO_DECISION",
            reason_code="SIZING_FACT_CONFLICT",
        )
    if per_contract_planned_loss != per_contract_max_loss:
        return _empty_result(
            state="NO_DECISION",
            reason_code="SIZING_PLANNED_LOSS_MUST_EQUAL_MAX_LOSS",
        )

    eligible_bankroll = max(
        0,
        initial + realized_positive // 2 - realized_loss,
    )
    half_kelly = full_kelly // 2
    drawdown_ppm = ((peak_nlv - current_nlv) * PPM) // peak_nlv
    if drawdown_ppm >= _DRAWDOWN_30_PPM and not sticky_drawdown_lock_active:
        return _empty_result(
            state="NO_DECISION",
            reason_code="SIZING_STICKY_DRAWDOWN_LOCK_CONFLICT",
        )
    drawdown_factor_ppm = (
        0
        if sticky_drawdown_lock_active
        else _drawdown_factor(drawdown_ppm)
    )

    kelly_risk_budget = (
        eligible_bankroll * half_kelly * drawdown_factor_ppm // (PPM * PPM)
    )
    kelly_drawdown_capacity = kelly_risk_budget // per_contract_max_loss

    per_contract_delta_notional = _ceil_div(
        spot * multiplier * delta_ppm,
        PPM,
    )
    delta_limit = eligible_bankroll * _DELTA_NOTIONAL_LIMIT_PPM // PPM
    remaining_delta_capacity = max(0, delta_limit - current_delta_notional)
    delta_capacity = remaining_delta_capacity // per_contract_delta_notional

    required_reserve = _ceil_div(current_nlv * _CASH_RESERVE_PPM, PPM)
    available_cash = max(0, settled_cash - required_reserve)
    cash_capacity = available_cash // per_contract_cash

    capacities = (
        ("KELLY_DRAWDOWN", kelly_drawdown_capacity),
        ("MAX_LOSS", max_loss_capacity),
        ("PLANNED_LOSS", planned_loss_capacity),
        ("DELTA_NOTIONAL", delta_capacity),
        ("SETTLED_CASH", cash_capacity),
        ("LIQUIDITY", liquidity_capacity),
    )
    quantity = min(value for _, value in capacities)
    bindings = tuple(name for name, value in capacities if value == quantity)
    if sticky_drawdown_lock_active:
        quantity = 0
        bindings = ("STICKY_DRAWDOWN_LOCK",)
        state = "NO_ACTION"
        reason_code = "SIZING_STICKY_DRAWDOWN_LOCK_ACTIVE"
    elif drawdown_factor_ppm == 0:
        state = "NO_ACTION"
        reason_code = _drawdown_reason(drawdown_ppm)
    elif quantity == 0:
        state = "NO_ACTION"
        reason_code = "SIZING_COMPLETE_ZERO_QUANTITY"
    else:
        state = "PASS"
        reason_code = "SIZING_PASS"

    return SizingResultV1(
        state=state,
        reason_code=reason_code,
        quantity=quantity,
        eligible_bankroll_nano_usd=eligible_bankroll,
        full_kelly_ppm=full_kelly,
        half_kelly_ppm=half_kelly,
        drawdown_ppm=drawdown_ppm,
        drawdown_factor_ppm=drawdown_factor_ppm,
        per_contract_max_loss_nano_usd=per_contract_max_loss,
        per_contract_delta_notional_nano_usd=per_contract_delta_notional,
        kelly_drawdown_capacity_contracts=kelly_drawdown_capacity,
        max_loss_capacity_contracts=max_loss_capacity,
        planned_loss_capacity_contracts=planned_loss_capacity,
        delta_capacity_contracts=delta_capacity,
        cash_capacity_contracts=cash_capacity,
        liquidity_capacity_contracts=liquidity_capacity,
        binding_constraints=bindings,
        missing_fields=(),
    )


__all__ = ["size_position"]
