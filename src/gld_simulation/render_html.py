"""Deterministic, presentation-only renderer for simulation decision facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from html import escape
import json
from typing import Final
from zoneinfo import ZoneInfo


_NEW_YORK: Final[ZoneInfo] = ZoneInfo("America/New_York")

_GATE_COMPARISONS: Final[
    tuple[tuple[str, str, str, str, str, str], ...]
] = (
    (
        "sma50_gt_sma200",
        "均线趋势",
        "current_sma50_nano_usd",
        "50 日均线",
        "current_sma200_nano_usd",
        "200 日均线",
    ),
    (
        "sma50_rising_20_sessions",
        "50 日均线上升趋势",
        "current_sma50_nano_usd",
        "当前",
        "sma50_20_sessions_ago_nano_usd",
        "20 个交易日前",
    ),
    (
        "prior_close_gt_sma50",
        "前收盘价位置",
        "prior_close_nano_usd",
        "前收盘价",
        "current_sma50_nano_usd",
        "50 日均线",
    ),
    (
        "minute_1044_close_gt_breakout",
        "盘中突破",
        "minute_1044_close_nano_usd",
        "10:44 收盘价",
        "breakout_nano_usd",
        "突破价",
    ),
)

_RISK_BINDING_LABELS: Final[dict[str, str]] = {
    "STICKY_DRAWDOWN_LOCK": "粘性回撤锁已激活",
    "KELLY_DRAWDOWN": "Half-Kelly 与回撤系数给出的风险预算不足",
    "MAX_LOSS": "单笔模型最大亏损预算不足",
    "PLANNED_LOSS": "计划亏损预算不足",
    "DELTA_NOTIONAL": "GLD 等效 Delta 名义敞口容量不足",
    "SETTLED_CASH": "已结算现金及保留现金要求不足",
    "LIQUIDITY": "模拟报价可成交数量不足",
}

_NO_ACTION_SIZING_REASON_LABELS: Final[dict[str, str]] = {
    "SIZING_STICKY_DRAWDOWN_LOCK_ACTIVE": "粘性回撤锁已激活",
    "SIZING_DRAWDOWN_BLOCKS_NEW_ENTRY": "当前回撤水平已阻止新建仓",
    "SIZING_DRAWDOWN_DISASTER_LOCK": "账户已触发严重回撤锁",
    "SIZING_DRAWDOWN_EXIT_MANAGED": "账户仍处于回撤退出管理状态",
}

_NO_DECISION_SIZING_REASON_LABELS: Final[dict[str, str]] = {
    "SIZING_FACT_CONFLICT": "风险或数量事实互相冲突",
    "SIZING_PLANNED_LOSS_MUST_EQUAL_MAX_LOSS": (
        "计划亏损口径与模型最大亏损不一致"
    ),
    "SIZING_STICKY_DRAWDOWN_LOCK_CONFLICT": (
        "粘性回撤锁状态与回撤水平冲突"
    ),
}

_RISK_FIELD_LABELS: Final[dict[str, str]] = {
    "as_of_utc_ns": "账户快照时间",
    "net_liquidation_value_nano_usd": "账户净清算价值",
    "settled_cash_nano_usd": "已结算现金（settled cash）",
    "strategy_bankroll_principal_nano_usd": "策略本金",
    "strategy_high_watermark_nlv_nano_usd": "策略净值高水位",
    "cumulative_realized_profit_nano_usd": "累计已实现盈利",
    "cumulative_realized_positive_nano_usd": "累计已实现盈利",
    "cumulative_realized_loss_nano_usd": "累计已实现亏损",
    "unrealized_profit_nano_usd": "未实现盈利",
    "current_gld_equivalent_delta_notional_nano_usd": (
        "当前 GLD 等效 Delta 名义敞口"
    ),
    "sticky_drawdown_lock_active": "粘性回撤锁状态",
    "robust_full_kelly_ppm": "冻结的稳健 Full-Kelly 比例",
    "frozen_at_utc_ns": "Kelly 回执冻结时间",
    "sample_end_trading_date": "Kelly 样本截止交易日",
    "sample_episode_count": "Kelly 样本交易数量",
    "sample_input_sha256": "Kelly 样本输入身份",
    "strategy_rule_package_id": "Kelly 回执绑定的策略规则版本",
    "applicable_carriers": "Kelly 回执适用的策略结构",
    "return_distribution_id": "Kelly 回执收益分布版本",
    "initial_bankroll_nano_usd": "策略本金",
    "current_nlv_nano_usd": "账户净清算价值",
    "peak_nlv_nano_usd": "策略净值高水位",
    "max_loss_nano_usd_per_contract": "每份模型最大亏损",
    "planned_loss_nano_usd_per_contract": "每份计划亏损",
    "entry_cash_nano_usd_per_contract": "每份建仓现金需求",
    "absolute_delta_ppm_per_contract": "每份绝对 Delta",
    "underlying_spot_nano_usd": "GLD 现货价格",
    "contract_multiplier": "合约乘数",
    "max_loss_capacity_contracts": "模型最大亏损容量",
    "planned_loss_capacity_contracts": "计划亏损容量",
    "liquidity_capacity_contracts": "模拟报价流动性容量",
}

_IDENTITY_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "schema_version",
    "decision_scope",
    "classification",
    "status",
    "reason_code",
    "actionable",
    "broker_order_count",
    "owner_selection_required",
    "manual_control",
    "bundle_id",
    "manifest_sha256",
    "trading_date",
    "cutoff_utc_ns",
    "rule_package_version",
    "rule_sha256",
    "raw_content_sha256",
)

_GATE_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "state",
    "reason_code",
    "rule_version",
    "predicates",
    "metrics",
    "input_fact_sha256",
    "max_event_utc_ns",
    "max_receive_utc_ns",
)

_CRR_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "status",
    "requested",
    "bound",
    "started",
    "terminal",
    "failure_count",
    "first_failure_ordinal",
    "worker_count",
    "kernel_threads",
    "cache_hits",
    "early_exercise_terminal_count",
    "model_id",
    "model_sha256",
    "numerical_semantics_trust_boundary",
    "native_build_backend_evidence_sha256",
    "combined_backend_evidence_sha256",
    "input_vector_sha256",
    "execution_provenance_sha256",
    "semantic_output_sha256",
)

_RISK_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "status",
    "reason_code",
    "missing_fields",
    "account_as_of_utc_ns",
    "eligible_bankroll_nano_usd",
    "policy_id",
    "full_kelly_ppm",
    "half_kelly_ppm",
    "drawdown_factor_ppm",
    "drawdown_multiplier_ppm",
    "drawdown_ppm",
    "minimum_settled_cash_ppm",
    "minimum_post_trade_settled_cash_nlv_ppm",
    "maximum_gld_equivalent_delta_notional_bankroll_ppm",
    "candidate_terminal_policy",
    "frozen_kelly_applicable_carriers",
    "frozen_kelly_receipt_id",
    "frozen_kelly_return_distribution_id",
    "frozen_kelly_strategy_rule_package_id",
    "planned_loss_equals_maximum_loss",
    "positive_realized_profit_reinvestment_ppm",
    "realized_loss_effect_ppm",
    "separate_max_loss_budget_mode",
    "sticky_drawdown_lock_active",
    "unrealized_profit_expands_bankroll",
)

_CANDIDATE_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "carrier_id",
    "status",
    "reason_code",
    "quantity",
    "expiry_date",
    "profit_cap_mode",
    "net_delta_ppm_per_strategy_unit",
    "max_loss_nano_usd",
    "total_entry_cost_nano_usd",
    "base_entry_cost_nano_usd_per_contract",
    "stressed_entry_cost_nano_usd_per_contract",
    "delta_notional_nano_usd_per_contract",
    "gross_expiry_width_nano_usd_per_contract",
    "modeled_expiry_max_profit_nano_usd_per_strategy_unit",
    "profit_model_scope",
    "sizing_loss_basis",
    "underlying_mid_nano_usd",
    "moneyness",
    "selection_sha256",
    "long_coarse_delta_ppm",
    "long_fine_delta_ppm",
    "short_coarse_delta_ppm",
    "short_fine_delta_ppm",
    "h20_date",
    "minimum_expiry_date",
)

_CAPACITY_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "cash",
    "delta",
    "delta_notional",
    "kelly",
    "kelly_drawdown",
    "liquidity",
    "max_loss",
    "planned_loss",
)

_SIZING_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "state",
    "reason_code",
    "quantity",
    "eligible_bankroll_nano_usd",
    "full_kelly_ppm",
    "half_kelly_ppm",
    "drawdown_ppm",
    "drawdown_factor_ppm",
    "per_contract_delta_notional_nano_usd",
    "per_contract_max_loss_nano_usd",
)

_SELECTOR_POLICY_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "candidate_terminal_policy",
    "coarse_fine_agreement_required",
    "second_best_fallback_allowed",
    "target_delta_ppm",
)

_LC0_SELECTOR_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "selector_id",
    "target_delta_ppm",
    "minimum_delta_ppm",
    "maximum_delta_ppm",
    "minimum_calendar_days_after_h20",
    "coarse_fine_agreement_required",
    "require_standard_unadjusted",
    "required_exercise_style",
    "required_multiplier",
    "required_right",
    "second_best_fallback_allowed",
)

_BCS_SELECTOR_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "selector_id",
    "leg_ratio",
    "same_expiry_required",
    "roll_active",
    "short_target_delta_ppm",
    "short_minimum_delta_ppm",
    "short_maximum_delta_ppm",
    "second_best_fallback_allowed",
)

_ECONOMICS_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "carrier_id",
    "base_entry_cost_nano_usd_per_contract",
    "stressed_entry_cost_nano_usd_per_contract",
    "planned_exit_fees_nano_usd_per_contract",
    "sizing_max_loss_nano_usd_per_contract",
    "sizing_planned_loss_nano_usd_per_contract",
    "delta_notional_nano_usd_per_contract",
    "gross_expiry_width_nano_usd_per_contract",
    "modeled_expiry_max_profit_nano_usd_per_strategy_unit",
    "liquidity_capacity",
)

_EXECUTION_ASSUMPTION_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "include_exit_fees_in_max_loss",
    "long_entry_adverse_ticks",
    "short_entry_adverse_ticks",
)

_FEE_COMPONENT_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "long_entry",
    "long_exit",
    "short_entry",
    "short_exit",
)

_LEG_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "side",
    "ratio",
    "contract_id",
    "expiry_date",
    "expiry_utc_ns",
    "strike_nano_usd",
    "moneyness",
    "bid_nano_usd",
    "ask_nano_usd",
    "bid_size",
    "ask_size",
    "delta_ppm",
    "coarse_delta_ppm",
    "fine_delta_ppm",
)

_EXIT_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "fixed_take_profit",
    "gate_a_invalidation",
    "expiry_safety",
    "latest_exit_horizon",
    "latest_full_exit_date",
    "lc1_roll_up",
)


def _text(value: object) -> str:
    """Return an escaped, deterministic scalar representation."""

    if value is None:
        raw = "null"
    elif isinstance(value, bool):
        raw = "true" if value else "false"
    elif isinstance(value, (str, int)):
        raw = str(value)
    elif isinstance(value, (Mapping, Sequence)):
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    else:
        raw = str(value)
    return escape(raw, quote=True)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _pick(source: Mapping[str, object], fields: Sequence[str]) -> dict[str, object]:
    return {field: source[field] for field in fields if field in source}


def _list_items(labels: Sequence[str], *, fallback: str) -> str:
    rendered = list(dict.fromkeys(labels)) or [fallback]
    return "".join(f"<li>{_text(label)}</li>" for label in rendered)


def _sizing_documents(risk_context: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    return tuple(
        _mapping(risk_context.get(key))
        for key in ("lc0_sizing", "bcs_sizing")
        if isinstance(risk_context.get(key), Mapping)
    )


def _risk_binding_labels(risk_context: Mapping[str, object]) -> list[str]:
    observed: set[str] = set()
    for sizing in _sizing_documents(risk_context):
        observed.update(_string_list(sizing.get("binding_constraints")))
    return [
        label
        for code, label in _RISK_BINDING_LABELS.items()
        if code in observed
    ]


def _risk_missing_labels(value: object) -> list[str]:
    labels: list[str] = []
    unknown_observed = False
    for raw_field in sorted(set(_string_list(value))):
        field_name = raw_field.rsplit(".", maxsplit=1)[-1]
        label = _RISK_FIELD_LABELS.get(field_name)
        if label is None:
            unknown_observed = True
        elif label not in labels:
            labels.append(label)
    if unknown_observed:
        labels.append("完成风险或数量判断所需的其他事实")
    return labels


def _no_action_risk_labels(risk_context: Mapping[str, object]) -> list[str]:
    labels = _risk_binding_labels(risk_context)
    observed_reasons = {
        str(sizing.get("reason_code"))
        for sizing in _sizing_documents(risk_context)
    }
    for code, label in _NO_ACTION_SIZING_REASON_LABELS.items():
        if code in observed_reasons and label not in labels:
            labels.append(label)
    return labels


def _no_decision_risk_labels(risk_context: Mapping[str, object]) -> list[str]:
    labels: list[str] = []
    for sizing in _sizing_documents(risk_context):
        reason_code = sizing.get("reason_code")
        if reason_code == "SIZING_FACT_MISSING":
            for label in _risk_missing_labels(sizing.get("missing_fields")):
                if label not in labels:
                    labels.append(label)
            continue
        label = _NO_DECISION_SIZING_REASON_LABELS.get(str(reason_code))
        if label is not None and label not in labels:
            labels.append(label)
    return labels


def _string_list(value: object) -> list[str]:
    return [item for item in _sequence(value) if isinstance(item, str)]


def _capacity_projection(value: object) -> dict[str, object]:
    return _pick(_mapping(value), _CAPACITY_AUDIT_FIELDS)


def _sizing_projection(value: object) -> dict[str, object]:
    sizing = _mapping(value)
    projection = _pick(sizing, _SIZING_AUDIT_FIELDS)
    if "capacities" in sizing:
        projection["capacities"] = _capacity_projection(sizing.get("capacities"))
    if "binding_constraints" in sizing:
        projection["binding_constraints"] = _string_list(
            sizing.get("binding_constraints")
        )
    if "missing_fields" in sizing:
        projection["missing_fields"] = _string_list(sizing.get("missing_fields"))
    return projection


def _risk_projection(value: object) -> dict[str, object]:
    risk = _mapping(value)
    projection = _pick(risk, _RISK_AUDIT_FIELDS)
    if "binding_constraints" in risk:
        projection["binding_constraints"] = _string_list(
            risk.get("binding_constraints")
        )
    if "lc0_sizing" in risk:
        projection["lc0_sizing"] = _sizing_projection(risk.get("lc0_sizing"))
    if "bcs_sizing" in risk:
        projection["bcs_sizing"] = _sizing_projection(risk.get("bcs_sizing"))
    return projection


def _selector_policy_projection(value: object) -> dict[str, object]:
    policy = _mapping(value)
    projection = _pick(policy, _SELECTOR_POLICY_AUDIT_FIELDS)
    if "lc0" in policy:
        lc0 = _mapping(policy.get("lc0"))
        lc0_projection = _pick(lc0, _LC0_SELECTOR_AUDIT_FIELDS)
        if "tie_break_order" in lc0:
            lc0_projection["tie_break_order"] = _string_list(
                lc0.get("tie_break_order")
            )
        projection["lc0"] = lc0_projection
    if "bcs" in policy:
        projection["bcs"] = _pick(
            _mapping(policy.get("bcs")),
            _BCS_SELECTOR_AUDIT_FIELDS,
        )
    return projection


def _economics_projection(value: object) -> dict[str, object]:
    economics = _mapping(value)
    projection = _pick(economics, _ECONOMICS_AUDIT_FIELDS)
    if "execution_assumptions" in economics:
        projection["execution_assumptions"] = _pick(
            _mapping(economics.get("execution_assumptions")),
            _EXECUTION_ASSUMPTION_AUDIT_FIELDS,
        )
    if "fee_components_nano_usd_per_contract" in economics:
        projection["fee_components_nano_usd_per_contract"] = _pick(
            _mapping(economics.get("fee_components_nano_usd_per_contract")),
            _FEE_COMPONENT_AUDIT_FIELDS,
        )
    return projection


def _format_nano_usd(value: object) -> str:
    """Format integer nano-USD using deterministic half-up cent rounding."""

    if type(value) is not int:
        return "未提供"
    absolute = abs(value)
    cents, remainder = divmod(absolute, 10_000_000)
    if remainder >= 5_000_000:
        cents += 1
    dollars, fractional = divmod(cents, 100)
    sign = "-" if value < 0 else ""
    return f"{sign}${dollars:,}.{fractional:02d}"


def _format_cutoff_et(value: object) -> str:
    if type(value) is not int or value < 0:
        return "未提供"
    seconds, _nanoseconds = divmod(value, 1_000_000_000)
    try:
        instant = datetime.fromtimestamp(seconds, timezone.utc).astimezone(
            _NEW_YORK
        )
    except (OverflowError, OSError, ValueError):
        return "未提供"
    return instant.strftime("%Y-%m-%d %H:%M ET")


def _format_side(value: object) -> str:
    return {
        "BUY": "买入",
        "BUY_TO_OPEN": "买入开仓",
        "SELL": "卖出",
        "SELL_TO_OPEN": "卖出开仓",
    }.get(str(value), "未提供")


def _format_moneyness(value: object) -> str:
    return {
        "OTM": "价外（OTM）",
        "ITM": "价内（ITM）",
        "ATM": "平值（ATM）",
    }.get(str(value), "未提供")


def _candidate_by_id(
    candidates: Sequence[object],
    carrier_id: str,
) -> Mapping[str, object]:
    for raw_candidate in candidates:
        candidate = _mapping(raw_candidate)
        if candidate.get("carrier_id") == carrier_id:
            return candidate
    return {}


def _per_strategy_unit_cost(candidate: Mapping[str, object]) -> object:
    explicit = candidate.get("base_entry_cost_nano_usd_per_contract")
    if type(explicit) is int:
        return explicit
    total = candidate.get("total_entry_cost_nano_usd")
    quantity = candidate.get("quantity")
    if type(total) is int and type(quantity) is int and quantity > 0:
        return total // quantity
    return None


def _profit_cap_text(candidate: Mapping[str, object]) -> str:
    mode = candidate.get("profit_cap_mode")
    if mode == "UNBOUNDED_BY_STRUCTURE":
        return "结构上不封顶"
    if mode == "CAPPED_AT_SHORT_STRIKE":
        return "在卖出行权价处封顶"
    return "未提供"


def _quantity_text(candidate: Mapping[str, object], *, is_bcs: bool) -> str:
    quantity = candidate.get("quantity")
    if type(quantity) is not int:
        return "未提供"
    return f"{quantity} {'组' if is_bcs else '张'}"


def _summary_row(label: str, value: object, *, css_class: str = "") -> str:
    class_attribute = f' class="{css_class}"' if css_class else ""
    return (
        "<tr>"
        f'<th scope="row">{_text(label)}</th>'
        f"<td{class_attribute}>{_text(value)}</td>"
        "</tr>"
    )


def _render_candidate_summary(
    candidate: Mapping[str, object],
    *,
    choice: str,
    title: str,
    structure: str,
    is_bcs: bool,
) -> str:
    rows = "".join(
        (
            _summary_row("结构", structure),
            _summary_row("组合数量", _quantity_text(candidate, is_bcs=is_bcs)),
            _summary_row(
                "每份预计成本" if not is_bcs else "每组预计成本",
                _format_nano_usd(_per_strategy_unit_cost(candidate)),
                css_class="money",
            ),
            _summary_row(
                "预计总成本",
                _format_nano_usd(candidate.get("total_entry_cost_nano_usd")),
                css_class="money",
            ),
            _summary_row(
                "模型最大亏损",
                _format_nano_usd(candidate.get("max_loss_nano_usd")),
                css_class="money",
            ),
            _summary_row("收益上限", _profit_cap_text(candidate)),
        )
    )
    return (
        '<article class="choice-card">'
        '<header class="choice-heading">'
        f'<p class="choice-label">方案 {_text(choice)}</p>'
        f"<h3>{_text(choice)} · {_text(title)}</h3>"
        "</header>"
        f'<div class="table-scroll" role="region" tabindex="0" aria-label="方案 {_text(choice)} 核心对比，可横向滚动">'
        f'<table><caption>方案 {_text(choice)} 核心对比</caption><tbody>{rows}</tbody></table>'
        "</div>"
        "</article>"
    )


def _render_leg_rows(candidate: Mapping[str, object]) -> str:
    candidate_quantity = candidate.get("quantity")
    rendered: list[str] = []
    for index, raw_leg in enumerate(_sequence(candidate.get("legs")), start=1):
        leg = _mapping(raw_leg)
        ratio = leg.get("ratio")
        leg_quantity: object = "未提供"
        if type(candidate_quantity) is int and type(ratio) is int:
            leg_quantity = f"{candidate_quantity * ratio} 张"
        quote = (
            f"{_format_nano_usd(leg.get('bid_nano_usd'))} / "
            f"{_format_nano_usd(leg.get('ask_nano_usd'))}"
        )
        rendered.append(
            "<tr>"
            f'<th scope="row">第 {index} 腿</th>'
            f"<td>{_text(_format_side(leg.get('side')))}</td>"
            f"<td>{_text(leg_quantity)}</td>"
            f'<td class="mono">{_text(leg.get("contract_id"))}</td>'
            f"<td>{_text(leg.get('expiry_date'))}</td>"
            f'<td class="money">{_text(_format_nano_usd(leg.get("strike_nano_usd")))}</td>'
            f"<td>{_text(_format_moneyness(leg.get('moneyness')))}</td>"
            f'<td class="money">{_text(quote)}</td>'
            "</tr>"
        )
    return "".join(rendered)


def _render_order_preview(
    candidate: Mapping[str, object],
    *,
    choice: str,
    title: str,
    is_bcs: bool,
) -> str:
    combo_notice = (
        '<p class="combo-warning">BCS 必须作为一个原生 1:1 组合执行，不可逐腿执行。</p>'
        if is_bcs
        else '<p class="structure-note">模拟单腿 Call。</p>'
    )
    return (
        '<article class="order-card">'
        f"<h3>方案 {_text(choice)} · {_text(title)}</h3>"
        f"{combo_notice}"
        f'<div class="table-scroll" role="region" tabindex="0" aria-label="方案 {_text(choice)} 模拟订单腿，可横向滚动">'
        '<table class="order-table">'
        f"<caption>方案 {_text(choice)} 模拟订单腿</caption>"
        "<thead><tr>"
        '<th scope="col">腿</th>'
        '<th scope="col">方向</th>'
        '<th scope="col">数量</th>'
        '<th scope="col">合约</th>'
        '<th scope="col">到期日</th>'
        '<th scope="col">行权价</th>'
        '<th scope="col">价内 / 价外</th>'
        '<th scope="col">计算用 bid / ask</th>'
        "</tr></thead>"
        f"<tbody>{_render_leg_rows(candidate)}</tbody>"
        "</table>"
        "</div>"
        "</article>"
    )


def _gate_items(gate_a: Mapping[str, object], *, failed_only: bool) -> str:
    predicates = _mapping(gate_a.get("predicates"))
    metrics = _mapping(gate_a.get("metrics"))
    items: list[str] = []
    for key, label, left_key, left_label, right_key, right_label in _GATE_COMPARISONS:
        result = predicates.get(key)
        if failed_only and result is not False:
            continue
        state = "通过" if result is True else "未通过" if result is False else "无法判断"
        state_class = "pass" if result is True else "fail"
        items.append(
            f'<li class="check-{state_class}">{_text(label)}：'
            f"{_text(left_label)} {_text(_format_nano_usd(metrics.get(left_key)))}；"
            f"{_text(right_label)} {_text(_format_nano_usd(metrics.get(right_key)))}；"
            f"要求前者更高：{_text(state)}</li>"
        )

    range_result = predicates.get("range_hold_count_met")
    if not failed_only or range_result is False:
        above = metrics.get("range_hold_above_count")
        required = metrics.get("range_hold_required_count")
        state = (
            "通过"
            if range_result is True
            else "未通过"
            if range_result is False
            else "无法判断"
        )
        state_class = "pass" if range_result is True else "fail"
        items.append(
            f'<li class="check-{state_class}">突破后保持分钟数：'
            f"{_text(above)} / {_text(required)}，{_text(state)}</li>"
        )
    if not items:
        return '<li class="check-fail">Gate A 未通过，但没有可展示的逐项结果。</li>'
    return "".join(items)


def _render_gate_summary(gate_a: Mapping[str, object], *, failed_only: bool) -> str:
    title = "未通过的 Gate A 条件" if failed_only else "Gate A 条件"
    return (
        '<section class="content-section" aria-labelledby="gate-title">'
        f'<h2 id="gate-title">{title}</h2>'
        f'<ul class="checklist">{_gate_items(gate_a, failed_only=failed_only)}</ul>'
        "</section>"
    )


def _render_exit_plan(exit_plan: Mapping[str, object]) -> str:
    latest_date = exit_plan.get("latest_full_exit_date")
    latest_horizon = exit_plan.get("latest_exit_horizon")
    return (
        '<section class="content-section" aria-labelledby="exit-title">'
        '<h2 id="exit-title">共同退出纪律</h2>'
        '<ul class="plain-list">'
        "<li>不设置固定止盈。</li>"
        "<li>Gate A 失效时需要进行退出复核。</li>"
        "<li>必须在合约安全边界前全部平仓。</li>"
        f"<li>最晚完整退出日：{_text(latest_date)}（{_text(latest_horizon)}）。</li>"
        "<li>本模拟版本不启用向上 roll。</li>"
        "</ul>"
        "</section>"
    )


def _render_pass_content(decision_document: Mapping[str, object]) -> str:
    candidates = _sequence(decision_document.get("candidates"))
    lc0 = _candidate_by_id(candidates, "LC0")
    bcs0 = _candidate_by_id(candidates, "BCS0")
    gate_a = _mapping(decision_document.get("gate_a"))
    exit_plan = _mapping(decision_document.get("exit_plan"))
    return (
        '<section aria-labelledby="comparison-title" class="content-section">'
        '<h2 id="comparison-title">A / B 方案对比</h2>'
        '<p class="section-intro">两套结构均通过当前模拟规则；本卡不替你推荐任何一套。</p>'
        '<div class="comparison-grid">'
        f"{_render_candidate_summary(lc0, choice='A', title='Long Call', structure='模拟单腿 Call', is_bcs=False)}"
        f"{_render_candidate_summary(bcs0, choice='B', title='Bull Call Spread', structure='原生 1:1 组合', is_bcs=True)}"
        "</div>"
        "</section>"
        '<section class="content-section" aria-labelledby="preview-title">'
        '<h2 id="preview-title">模拟订单预览</h2>'
        '<p class="quote-warning"><strong>注意：</strong>bid / ask 仅用于本次模拟成本计算，不是订单限价。</p>'
        '<p class="cost-note">预计成本包含规则包中的模拟入场费用，因此不等于只用显示的 bid / ask × 100 简单计算。</p>'
        '<div class="order-stack">'
        f"{_render_order_preview(lc0, choice='A', title='Long Call', is_bcs=False)}"
        f"{_render_order_preview(bcs0, choice='B', title='Bull Call Spread', is_bcs=True)}"
        "</div>"
        '<aside class="live-blockers" aria-labelledby="live-blockers-title">'
        '<h3 id="live-blockers-title">为什么现在不能真实下单</h3>'
        '<p>真实执行仍缺：合格实时报价、限价规则、订单有效期（TIF）和卡片有效期。</p>'
        "</aside>"
        "</section>"
        '<section class="next-step" aria-labelledby="next-step-title">'
        '<h2 id="next-step-title">你的下一步</h2>'
        '<p><strong>请在 Codex 对话中回复 A、B 或不交易。</strong></p>'
        '<p>这只记录模拟选择，不创建、预填或提交任何券商订单。</p>'
        "</section>"
        f"{_render_gate_summary(gate_a, failed_only=False)}"
        f"{_render_exit_plan(exit_plan)}"
    )


def _render_no_action_content(decision_document: Mapping[str, object]) -> str:
    gate_a = _mapping(decision_document.get("gate_a"))
    if gate_a.get("state") == "FAIL":
        return (
            '<section class="state-explanation" aria-labelledby="state-reason-title">'
            '<h2 id="state-reason-title">为什么今天不建仓</h2>'
            '<p>Gate A 没有完整通过，因此程序没有生成任何候选方案。</p>'
            "</section>"
            f"{_render_gate_summary(gate_a, failed_only=True)}"
            '<section class="next-step" aria-labelledby="next-step-title">'
            '<h2 id="next-step-title">下一步</h2>'
            '<p><strong>等待新的完整快照后重新运行。</strong></p>'
            "</section>"
        )

    risk_context = _mapping(decision_document.get("risk_context"))
    blocker_items = _list_items(
        _no_action_risk_labels(risk_context),
        fallback="风险或数量容量不足，当前规则计算出的可建仓数量为 0",
    )
    return (
        '<section class="state-explanation" aria-labelledby="state-reason-title">'
        '<h2 id="state-reason-title">风险边界阻止新建仓</h2>'
        '<p>Gate A 已通过，但以下风险或数量边界不允许生成建仓候选：</p>'
        f'<ul class="plain-list">{blocker_items}</ul>'
        "</section>"
        '<section class="next-step" aria-labelledby="next-step-title">'
        '<h2 id="next-step-title">下一步</h2>'
        '<p><strong>风险状态更新后，用新的完整快照重新运行。</strong></p>'
        "</section>"
    )


def _missing_fact_items(risk_context: Mapping[str, object]) -> str:
    labels = [
        f"缺少{label}"
        for label in _risk_missing_labels(risk_context.get("missing_fields"))
    ]
    return _list_items(
        labels,
        fallback="系统已安全停止，未生成交易结论",
    )


def _render_no_decision_content(decision_document: Mapping[str, object]) -> str:
    reason_code = decision_document.get("reason_code")
    risk_context = _mapping(decision_document.get("risk_context"))
    crr = _mapping(decision_document.get("crr"))
    if reason_code == "RISK_FACT_MISSING":
        blockers = _missing_fact_items(risk_context)
        recovery = "补齐缺失事实后重新运行。"
    elif reason_code == "GATE_A_NOT_EVALUABLE":
        blockers = "<li>Gate A 所需的行情或日历事实无法验证</li>"
        recovery = "修复 Gate A 输入并重新运行。"
    elif reason_code == "OPTION_DATA_INCOMPLETE":
        blockers = "<li>期权链或报价快照不完整</li>"
        recovery = "取得新的完整期权快照后重新运行。"
    elif reason_code == "CRR_MODEL_FAILURE" or crr.get("status") == "EXACT64_FAIL_CLOSED":
        blockers = "<li>CRR 候选计算未全部通过</li>"
        recovery = "修复 CRR 输入或计算失败后重新运行。"
    elif reason_code == "RISK_OR_QUANTITY_NOT_EVALUABLE":
        blockers = _list_items(
            _no_decision_risk_labels(risk_context),
            fallback="风险或数量计算无法完成",
        )
        recovery = "修复风险或数量事实后重新运行。"
    else:
        blockers = "<li>流程在安全边界内停止</li>"
        recovery = "解决上述阻断原因后重新运行。"
    return (
        '<section class="state-explanation" aria-labelledby="state-reason-title">'
        '<h2 id="state-reason-title">当前阻断原因</h2>'
        f'<ul class="plain-list">{blockers}</ul>'
        '<p>在关键事实完整前，程序不会生成候选方案或订单信息。</p>'
        "</section>"
        '<section class="next-step" aria-labelledby="next-step-title">'
        '<h2 id="next-step-title">下一步</h2>'
        f'<p><strong>{_text(recovery)}</strong></p>'
        "</section>"
    )


def _candidate_audit_projection(raw_candidate: object) -> dict[str, object]:
    candidate = _mapping(raw_candidate)
    projection = _pick(candidate, _CANDIDATE_AUDIT_FIELDS)
    if "binding_constraints" in candidate:
        projection["binding_constraints"] = _string_list(
            candidate.get("binding_constraints")
        )
    if "capacities" in candidate:
        projection["capacities"] = _capacity_projection(candidate.get("capacities"))
    if "selector_policy" in candidate:
        projection["selector_policy"] = _selector_policy_projection(
            candidate.get("selector_policy")
        )
    if "economics" in candidate:
        projection["economics"] = _economics_projection(candidate.get("economics"))
    projection["legs"] = [
        _pick(_mapping(raw_leg), _LEG_AUDIT_FIELDS)
        for raw_leg in _sequence(candidate.get("legs"))
    ]
    return projection


def _audit_projection(
    decision_document: Mapping[str, object],
    *,
    decision_result_sha256: str,
) -> dict[str, object]:
    identity = _pick(decision_document, _IDENTITY_AUDIT_FIELDS)
    identity["decision_result_sha256"] = decision_result_sha256
    crr_source = _mapping(decision_document.get("crr"))
    crr = _pick(crr_source, _CRR_AUDIT_FIELDS)
    semantic_terminals = _sequence(crr_source.get("semantic_terminals"))
    if semantic_terminals:
        crr["semantic_terminal_vector_count"] = len(semantic_terminals)
    return {
        "identity": identity,
        "gate_a": _pick(_mapping(decision_document.get("gate_a")), _GATE_AUDIT_FIELDS),
        "crr": crr,
        "risk_context": _risk_projection(decision_document.get("risk_context")),
        "candidates": [
            _candidate_audit_projection(candidate)
            for candidate in _sequence(decision_document.get("candidates"))
        ],
        "exit_plan": _pick(
            _mapping(decision_document.get("exit_plan")),
            _EXIT_AUDIT_FIELDS,
        ),
        "simulation_limitations": list(
            _sequence(decision_document.get("simulation_limitations"))
        ),
    }


def _render_audit_details(
    decision_document: Mapping[str, object],
    *,
    decision_result_sha256: str,
) -> str:
    raw_json = json.dumps(
        _audit_projection(
            decision_document,
            decision_result_sha256=decision_result_sha256,
        ),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    return (
        '<details class="audit-details">'
        "<summary>审计详情（机器字段）</summary>"
        '<p>包含 hash、CRR、selector、容量以及 nano / ppm / ns 原始字段。</p>'
        '<pre role="region" tabindex="0" aria-label="审计详情，可滚动">'
        f"{_text(raw_json)}</pre>"
        "</details>"
    )


def render_decision_card(
    decision_document: Mapping[str, object],
    *,
    decision_result_sha256: str,
) -> bytes:
    """Render validated canonical decision facts as inert, deterministic HTML.

    The renderer performs no strategy calculation, emits no broker payload, and
    exposes no execution affordance. Human-facing content is an explicit field
    projection; all dynamic text is escaped before interpolation.
    """

    status = decision_document.get("status")
    cutoff_et = _format_cutoff_et(decision_document.get("cutoff_utc_ns"))
    if status == "SIMULATED_OWNER_SELECTION_REQUIRED":
        page_title = "GLD 模拟决策卡：请选择模拟方案"
        heading = "两个模拟方案均通过，请由 Jake 选择"
        lead = "程序已完成 Gate A、候选筛选和风险数量计算。这里展示的是模拟选择，不是真实订单。"
        status_class = "status-choice"
        main_content = _render_pass_content(decision_document)
    elif status == "SIMULATED_NO_ACTION":
        page_title = "GLD 模拟决策卡：今天不建仓"
        heading = "今天不建仓"
        lead = "规则已完成判断：当前建仓条件不成立。"
        status_class = "status-no-action"
        main_content = _render_no_action_content(decision_document)
    else:
        page_title = "GLD 模拟决策卡：禁止建仓"
        heading = "禁止建仓：暂时无法判断"
        lead = "关键事实不完整或流程已安全停止，本次没有可执行结论。"
        status_class = "status-no-decision"
        main_content = _render_no_decision_content(decision_document)

    audit_details = _render_audit_details(
        decision_document,
        decision_result_sha256=decision_result_sha256,
    )
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'none'; connect-src 'none'; img-src 'none'; font-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; worker-src 'none'; manifest-src 'none'; base-uri 'none'; form-action 'none'">
  <title>{_text(page_title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #172019;
      --muted: #5c665e;
      --paper: #f4f3ed;
      --panel: #fffef9;
      --line: #d5d7cf;
      --forest: #173f2e;
      --forest-soft: #e7efe9;
      --amber: #9a640e;
      --amber-soft: #fff2cf;
      --danger: #8a2929;
      --danger-soft: #fae5e2;
      --shadow: 0 10px 30px rgba(23, 32, 25, .08);
    }}
    * {{ box-sizing: border-box; }}
    html {{ background: var(--paper); }}
    body {{
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI",
        "PingFang SC", "Hiragino Sans GB", sans-serif;
      font-size: 16px;
      line-height: 1.6;
    }}
    .safety-banner {{
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
      width: 100%;
      padding: 12px 16px;
      background: #102c21;
      color: #fff;
      font-weight: 800;
      letter-spacing: .03em;
      text-align: center;
    }}
    .safety-banner span + span::before {{ content: "｜"; margin: 0 10px; color: #c8d8cf; }}
    main {{ width: min(1120px, calc(100% - 32px)); margin: 28px auto 56px; }}
    h1, h2, h3, p {{ margin-top: 0; }}
    h1 {{
      max-width: 26ch;
      margin-bottom: 12px;
      font-size: clamp(30px, 5vw, 52px);
      line-height: 1.15;
      overflow-wrap: anywhere;
    }}
    h2 {{ margin-bottom: 14px; font-size: clamp(23px, 3vw, 32px); line-height: 1.25; }}
    h3 {{ margin-bottom: 12px; font-size: 20px; line-height: 1.35; overflow-wrap: anywhere; }}
    .decision-hero {{
      padding: clamp(24px, 4vw, 42px);
      border-left: 8px solid var(--forest);
      background: var(--panel);
      box-shadow: var(--shadow);
    }}
    .status-no-action {{ border-left-color: var(--amber); }}
    .status-no-decision {{ border-left-color: var(--danger); }}
    .status-kicker {{ margin-bottom: 8px; color: var(--muted); font-weight: 800; }}
    .status-no-action .status-kicker {{ color: var(--amber); }}
    .status-no-decision .status-kicker {{ color: var(--danger); }}
    .decision-lead {{ max-width: 72ch; margin-bottom: 10px; font-size: 18px; }}
    .data-time {{ margin-bottom: 0; color: var(--muted); font-variant-numeric: tabular-nums; }}
    .content-section, .state-explanation, .next-step, .audit-details {{
      margin-top: 24px;
      padding: clamp(20px, 3vw, 30px);
      border: 1px solid var(--line);
      background: var(--panel);
    }}
    .section-intro {{ color: var(--muted); }}
    .comparison-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }}
    .choice-card, .order-card {{ min-width: 0; border: 1px solid var(--line); background: #fff; }}
    .choice-heading {{ padding: 18px 20px 10px; background: var(--forest-soft); }}
    .choice-label {{ margin-bottom: 4px; color: var(--muted); font-weight: 750; }}
    .choice-heading h3 {{ margin-bottom: 0; }}
    .table-scroll {{ max-width: 100%; overflow-x: auto; }}
    .table-scroll:focus-visible {{ outline: 3px solid var(--amber); outline-offset: 3px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 16px; }}
    caption {{ padding: 12px 16px; color: var(--muted); font-weight: 700; text-align: left; }}
    th, td {{ padding: 11px 13px; border-bottom: 1px solid #e7e8e2; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 700; }}
    tbody th {{ width: 42%; }}
    tr:last-child th, tr:last-child td {{ border-bottom: 0; }}
    .money {{ white-space: nowrap; font-variant-numeric: tabular-nums; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap: anywhere; }}
    .order-stack {{ display: grid; gap: 18px; }}
    .order-card {{ padding: 20px; }}
    .order-table {{ min-width: 860px; }}
    .structure-note, .combo-warning, .quote-warning {{ padding: 12px 14px; background: var(--forest-soft); }}
    .combo-warning, .quote-warning {{ background: var(--amber-soft); }}
    .live-blockers {{ margin-top: 18px; padding: 18px; border-left: 5px solid var(--danger); background: var(--danger-soft); }}
    .live-blockers p:last-child {{ margin-bottom: 0; }}
    .next-step {{ border: 2px solid var(--forest); background: var(--forest-soft); }}
    .next-step p:last-child {{ margin-bottom: 0; }}
    .checklist, .plain-list {{ margin: 0; padding-left: 1.35rem; }}
    .checklist li, .plain-list li {{ margin: 8px 0; }}
    .check-pass strong {{ color: var(--forest); }}
    .check-fail strong {{ color: var(--danger); }}
    .audit-details summary {{
      min-height: 44px;
      cursor: pointer;
      font-size: 18px;
      font-weight: 800;
      line-height: 44px;
    }}
    .audit-details summary:focus-visible {{
      outline: 3px solid var(--amber);
      outline-offset: 3px;
    }}
    .audit-details pre {{
      max-height: 640px;
      margin: 12px 0 0;
      padding: 16px;
      overflow: auto;
      border: 1px solid var(--line);
      background: #f7f7f3;
      font-size: 13px;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}
    .audit-details pre:focus-visible {{
      outline: 3px solid var(--amber);
      outline-offset: 3px;
    }}
    footer {{ margin-top: 24px; color: var(--muted); font-size: 15px; }}
    @media (max-width: 768px) {{
      main {{ width: min(100% - 20px, 1120px); margin-top: 10px; }}
      .decision-hero, .content-section, .state-explanation, .next-step, .audit-details {{ padding: 20px; }}
      .comparison-grid {{ grid-template-columns: 1fr; }}
      .choice-card, .order-card {{ width: 100%; }}
      h1 {{ max-width: 100%; }}
    }}
    @media (max-width: 420px) {{
      main {{ width: 100%; margin-bottom: 32px; }}
      .decision-hero, .content-section, .state-explanation, .next-step, .audit-details {{
        margin-top: 12px;
        padding: 18px 14px;
        border-right: 0;
        border-left-width: 0;
      }}
      .decision-hero {{ border-left-width: 6px; }}
      th, td {{ padding: 10px; }}
    }}
  </style>
</head>
<body>
  <header class="safety-banner" aria-label="模拟安全边界">
    <span>仅模拟</span><span>合成数据</span><span>禁止真实下单</span>
  </header>
  <main id="main-content">
    <header class="decision-hero {_text(status_class)}">
      <p class="status-kicker">GLD 建仓模拟决策卡</p>
      <h1>{_text(heading)}</h1>
      <p class="decision-lead">{_text(lead)}</p>
      <p class="data-time">数据时点（纽约）：{_text(cutoff_et)}</p>
    </header>
    {main_content}
    {audit_details}
    <footer>
      <p>本文件只呈现程序生成的模拟事实，不是交易建议，不创建券商订单，也不具备下单权限。</p>
    </footer>
  </main>
</body>
</html>
"""
    return document.encode("utf-8")


__all__ = ["render_decision_card"]
