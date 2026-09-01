"""Static, non-interactive HTML renderers for Research F0 evidence."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import PurePosixPath

from .errors import ManagementResearchError


@dataclass(frozen=True, slots=True)
class OwnerAcceptanceGroupF0:
    """One plain-language owner question bound to fixed synthetic scenarios."""

    group_id: str
    title: str
    question: str
    accepted_rule: str
    scenario_ids: tuple[str, ...]


_OWNER_ACCEPTANCE_GROUPS_F0: tuple[OwnerAcceptanceGroupF0, ...] = (
    OwnerAcceptanceGroupF0(
        group_id="neutral-position",
        title="中性环境是否保持仓位",
        question="分数处于中性区时，程序会不会因为轻微波动随意调仓？",
        accepted_rule="40–69.99分是真正的保持区；没有外部风险强制要求时，仓位不变。",
        scenario_ids=("neutral_hold",),
    ),
    OwnerAcceptanceGroupF0(
        group_id="defensive-confirmation",
        title="转弱是否先等待、连续确认后才分层减仓",
        question="第一次转弱是否先观察，只有连续转弱后才降低风险？",
        accepted_rule=(
            "单日转弱先保持；第二个完整交易日仍弱才减仓。中度弱势最多保留75%，"
            "严重弱势最多保留50%，不足一个完整单位时不拆分。"
        ),
        scenario_ids=(
            "defensive_day_1",
            "defensive_day_2",
            "severe_defensive_day_2",
            "integer_no_op",
        ),
    ),
    OwnerAcceptanceGroupF0(
        group_id="recovery-confirmation",
        title="恢复是否先确认、之后逐层加回",
        question="出现强势恢复时，程序会不会一次性把仓位全部加回来？",
        accepted_rule=(
            "第一天恢复只等待；连续第二天确认后，每个合格管理日只恢复一个层级，"
            "且永不超过原始批准仓位。"
        ),
        scenario_ids=(
            "recovery_day_1",
            "recovery_day_2",
            "recovery_next_level",
        ),
    ),
    OwnerAcceptanceGroupF0(
        group_id="terminal-safety",
        title="Hard Stop和终局锁定是否覆盖高分",
        question="即使技术分数很高，安全退出和已经结束的持仓是否仍具有最高优先级？",
        accepted_rule=(
            "Hard Stop等终局事实直接要求退出；同一持仓一旦终局锁定，即使以后重新转强也不能重新加仓。"
        ),
        scenario_ids=("hard_stop_override", "latched_episode_no_reopen"),
    ),
    OwnerAcceptanceGroupF0(
        group_id="data-quality",
        title="旧报价或缺数据是否停止行动",
        question="行情事实不新鲜或无法完整计算时，程序会不会猜测并继续调仓？",
        accepted_rule=(
            "旧报价阻止调仓；必需指标无法评估时不产生管理决定，也不使用旧值或单指标替代。"
        ),
        scenario_ids=("strong_but_stale", "missing_dimension"),
    ),
    OwnerAcceptanceGroupF0(
        group_id="bcs-integrity",
        title="BCS是否始终保持完整1:1组合",
        question="牛市价差在减仓或出现残腿时，程序是否始终保护完整组合？",
        accepted_rule=(
            "BCS只能按完整1:1组合调整；存在残腿或对账冲突时不得继续执行新的仓位动作。"
        ),
        scenario_ids=("bcs_defensive_day_2", "bcs_residual_leg"),
    ),
)

_OWNER_SCENARIO_LABELS: tuple[tuple[str, str], ...] = (
    ("neutral_hold", "中性环境"),
    ("defensive_day_1", "第一天转弱"),
    ("defensive_day_2", "连续两天中度转弱"),
    ("severe_defensive_day_2", "连续两天严重转弱"),
    ("integer_no_op", "只剩一个完整单位"),
    ("recovery_day_1", "第一天恢复"),
    ("recovery_day_2", "连续两天恢复"),
    ("recovery_next_level", "下一个合格恢复日"),
    ("hard_stop_override", "高分时触发Hard Stop"),
    ("latched_episode_no_reopen", "已经终局退出后再次转强"),
    ("strong_but_stale", "恢复条件满足但报价过期"),
    ("missing_dimension", "必需指标无法完整评估"),
    ("bcs_defensive_day_2", "BCS连续转弱"),
    ("bcs_residual_leg", "BCS存在残腿"),
)

_OWNER_REASON_TEXT: tuple[tuple[str, str], ...] = (
    ("PRIMARY_SCORE_HOLD_BAND", "分数处于保持区"),
    ("WAITING_FOR_CONFIRMATION", "只有第一天转弱，等待连续确认"),
    ("CONFIRMED_DEFENSIVE_REDUCTION", "连续转弱已经确认"),
    ("NO_COMPLETE_UNIT_REDUCTION_AVAILABLE", "没有可拆分的完整单位"),
    ("WAITING_FOR_RECOVERY_CONFIRMATION", "只有第一天恢复，等待连续确认"),
    ("RECOVERY_READD_ONE_TIER", "恢复已经确认，本次只恢复一个层级"),
    ("HARD_STOP", "Hard Stop具有最高优先级"),
    ("EXIT_DUE_LATCHED_NO_POSITION", "该持仓已经终局结束，禁止重新加仓"),
    ("QUOTE_STALE", "报价已经过期"),
    ("PRIMARY_SCORE_NOT_EVALUABLE", "必需事实无法完整评估"),
    ("RESIDUAL_LEG", "组合存在残腿"),
)

_OWNER_PAGE_STYLE = """    .subtitle { margin: 0 0 20px; color: #52616b; font-size: 17px; }
    .owner-grid { display: grid; gap: 18px; margin-top: 22px; }
    .owner-card { padding: 20px; border: 1px solid #cbd5df; border-radius: 14px;
      background: white; box-shadow: 0 2px 8px rgba(22, 32, 42, 0.05); }
    .owner-card h2 { margin: 0 0 16px; font-size: 21px; }
    .owner-card h3 { margin: 16px 0 6px; font-size: 15px; color: #44515c; }
    .owner-card p { margin: 0; line-height: 1.65; }
    .owner-card li { margin: 7px 0; line-height: 1.55; }
    .owner-pass { margin-top: 16px !important; padding: 10px 12px;
      border-radius: 9px; color: #155724; background: #eaf7ed; font-weight: 700; }
    .owner-fail { margin-top: 16px !important; padding: 10px 12px;
      border-radius: 9px; color: #842029; background: #f8d7da; font-weight: 700; }
    .owner-next { margin-top: 24px; padding: 18px; border: 1px solid #9eb8ce;
      border-radius: 12px; background: #edf6ff; }
"""


def owner_acceptance_group_definitions_f0() -> tuple[OwnerAcceptanceGroupF0, ...]:
    """Return the frozen six-question owner acceptance catalog."""

    return _OWNER_ACCEPTANCE_GROUPS_F0


def _value(value: object) -> str:
    if value is None:
        return "—"
    if type(value) is bool:
        return "true" if value else "false"
    return escape(str(value), quote=True)


def _score_bp(value: object) -> str:
    if type(value) is not int:
        return "NOT_EVALUABLE"
    return f"{value // 100}.{value % 100:02d}"


def _safe_href(value: object) -> str:
    if type(value) is not str or not value or "\\" in value or ":" in value:
        return "#"
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return "#"
    return escape(value, quote=True)


def _page(title: str, body: str, *, extra_style: str = "") -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont,
      "Segoe UI", "PingFang SC", sans-serif; color: #17202a; background: #f5f7fa; }}
    body {{ margin: 0; padding: 24px; }}
    main {{ max-width: 1180px; margin: 0 auto; }}
    h1 {{ margin: 0 0 8px; }} h2 {{ margin-top: 28px; }}
    .boundary {{ padding: 14px 16px; border: 1px solid #d35400;
      border-radius: 10px; background: #fff4e6; font-weight: 650; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 12px; margin-top: 16px; }}
    .metric {{ padding: 14px; border: 1px solid #d9e1e8; border-radius: 10px;
      background: white; }} .metric span {{ display: block; color: #52616b; font-size: 13px; }}
    .metric strong {{ display: block; margin-top: 6px; overflow-wrap: anywhere; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ padding: 10px; border: 1px solid #d9e1e8; text-align: left;
      vertical-align: top; overflow-wrap: anywhere; }} th {{ background: #edf2f7; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
    a {{ color: #0b62a4; }} .pending {{ color: #8a4b08; font-weight: 700; }}
{extra_style}  </style>
</head>
<body><main>{body}</main></body>
</html>
"""


def render_management_score_card(document: dict[str, object]) -> str:
    """Render one combined run or action result without scripts or controls."""

    score = document.get("score_result")
    score_document = score if type(score) is dict else {}
    action = document.get("action_result")
    action_document = action if type(action) is dict else document
    explanation = document.get("explanation")
    explanation_document = explanation if type(explanation) is dict else {}
    primary = explanation_document.get("primary_final_score")
    if type(primary) is not dict:
        policy_results = score_document.get("policy_results")
        primary = (
            policy_results[0]
            if type(policy_results) is list
            and policy_results
            and type(policy_results[0]) is dict
            else {}
        )
    indicators = explanation_document.get("indicator_facts")
    if type(indicators) is not dict:
        indicators = score_document.get("indicators")
    if type(indicators) is not dict:
        indicators = {}
    dimensions = explanation_document.get("dimension_contributions")
    if type(dimensions) is not dict:
        dimensions = score_document.get("dimensions")
    if type(dimensions) is not dict:
        dimensions = {}
    breakout = explanation_document.get("breakout_diagnostic")
    if type(breakout) is not dict:
        breakout = score_document.get("breakout_diagnostic")
    if type(breakout) is not dict:
        breakout = {}
    updated_state = explanation_document.get("updated_streak_state")
    if type(updated_state) is not dict:
        updated_state = score_document.get("updated_state")
    if type(updated_state) is not dict:
        updated_state = {}
    raw_facts = explanation_document.get("raw_fact_summary")
    if type(raw_facts) is not dict:
        raw_facts = {}
    current_bar = raw_facts.get("current_daily_bar")
    if type(current_bar) is not dict:
        current_bar = {}
    episode = raw_facts.get("episode")
    if type(episode) is not dict:
        episode = score_document.get("episode")
    if type(episode) is not dict:
        episode = {}
    action_snapshot = raw_facts.get("action_snapshot")
    if type(action_snapshot) is not dict:
        action_snapshot = {}
    quotes = action_snapshot.get("quotes")
    if type(quotes) is not list:
        quotes = []
    quote_rows = "".join(
        "<tr>"
        f"<td><code>{_value(item.get('contract_id'))}</code></td>"
        f"<td>{_value(item.get('bid_nano_usd'))} / {_value(item.get('ask_nano_usd'))}</td>"
        f"<td>{_value(item.get('bid_size'))} / {_value(item.get('ask_size'))}</td>"
        f"<td>{_value(item.get('tick_nano_usd'))}</td>"
        f"<td>{_value(item.get('event_utc_ns'))}</td>"
        f"<td>{_value(item.get('receive_utc_ns'))}</td>"
        "</tr>"
        for item in quotes
        if type(item) is dict
    )
    shadows = explanation_document.get("shadow_policy_results")
    if type(shadows) is not list:
        shadows = []
    shadow_rows = "".join(
        "<tr>"
        f"<td><code>{_value(item.get('policy_id'))}</code></td>"
        f"<td>{_score_bp(item.get('display_score_bp'))}</td>"
        f"<td>{_value(item.get('band'))}</td>"
        f"<td>{_value(item.get('action_authority'))}</td>"
        "</tr>"
        for item in shadows
        if type(item) is dict
    )
    classification = document.get(
        "classification", action_document.get("classification", "RESEARCH_ONLY")
    )
    authority = document.get(
        "authority_status",
        action_document.get("authority_status", "NO_DECISION_EFFECT"),
    )
    body = f"""
<h1>GLD 持仓管理 Research F0</h1>
<p class="boundary">{_value(classification)} · {_value(authority)} ·
actionable={_value(document.get('actionable', action_document.get('actionable', False)))} ·
不可执行研究输出 · broker_order_count={_value(document.get('broker_order_count', action_document.get('broker_order_count', 0)))}</p>
<div class="grid">
  <div class="metric"><span>Final Score</span><strong>{_score_bp(primary.get('display_score_bp'))}</strong></div>
  <div class="metric"><span>Band</span><strong>{_value(primary.get('band'))}</strong></div>
  <div class="metric"><span>Action</span><strong>{_value(action_document.get('action'))}</strong></div>
  <div class="metric"><span>Units</span><strong>{_value(action_document.get('current_units'))} → {_value(action_document.get('target_units'))}</strong></div>
  <div class="metric"><span>Reason</span><strong>{_value(action_document.get('reason_code'))}</strong></div>
  <div class="metric"><span>Original approved units</span><strong>{_value(episode.get('original_approved_units'))}</strong></div>
  <div class="metric"><span>Run hash</span><strong><code>{_value(document.get('run_sha256', action_document.get('action_result_sha256')))}</code></strong></div>
</div>
<h2>Raw Management Facts</h2>
<table><tbody>
  <tr><th>Session / close</th><td>{_value(current_bar.get('session_date'))} / {_value(current_bar.get('close_nano_usd'))}</td><th>Prior close</th><td>{_value(raw_facts.get('prior_close_nano_usd'))}</td></tr>
  <tr><th>Current volume</th><td>{_value(current_bar.get('volume_shares'))}</td><th>Carrier / episode</th><td>{_value(episode.get('carrier_id'))} / {_value(episode.get('episode_id'))}</td></tr>
  <tr><th>Pinned calendar</th><td><code>{_value(raw_facts.get('calendar_id'))}</code></td><th>Calendar hash</th><td><code>{_value(raw_facts.get('calendar_version_sha256'))}</code></td></tr>
  <tr><th>Action fact kind / clock / DTE</th><td>{_value(action_snapshot.get('snapshot_kind'))} / {_value(action_snapshot.get('action_trading_date', action_snapshot.get('event_utc_ns')))} / {_value(action_snapshot.get('dte'))}</td><th>Risk caps</th><td>{_value(action_snapshot.get('account_risk_cap_units'))} / {_value(action_snapshot.get('liquidity_cap_units'))} / {_value(action_snapshot.get('external_risk_cap_units'))}</td></tr>
  <tr><th>Action/event UTC ns</th><td>{_value(action_snapshot.get('action_utc_ns', action_snapshot.get('event_utc_ns')))}</td><th>Fee / reconciliation / override</th><td>{_value(action_snapshot.get('fee_facts_status'))} / {_value(action_snapshot.get('reconciliation_status'))} / {_value(action_snapshot.get('override_status'))}</td></tr>
</tbody></table>
<h2>Quote Quality Facts</h2>
<table><thead><tr><th>Contract</th><th>Bid / Ask</th><th>Bid / Ask size</th><th>Tick</th><th>Event UTC ns</th><th>Receive UTC ns</th></tr></thead><tbody>{quote_rows or '<tr><td colspan="6">Not required for an event-driven terminal override.</td></tr>'}</tbody></table>
<h2>Local Technical Indicators</h2>
<table><tbody>
  <tr><th>SMA50</th><td>{_value(indicators.get('sma50_nano_usd'))}</td><th>ATR14</th><td>{_value(indicators.get('atr14_nano_usd'))}</td></tr>
  <tr><th>RSI14</th><td>{_value(indicators.get('rsi14_ppm'))}</td><th>MACD histogram</th><td>{_value(indicators.get('macd_histogram_nano_usd'))}</td></tr>
  <tr><th>+DI / -DI / ADX</th><td colspan="3">{_value(indicators.get('plus_di14_ppm'))} / {_value(indicators.get('minus_di14_ppm'))} / {_value(indicators.get('adx14_ppm'))}</td></tr>
  <tr><th>Prior 20 normal-session volume median</th><td colspan="3">{_value(indicators.get('volume_median20_shares'))}</td></tr>
</tbody></table>
<h2>Dimension Contributions</h2>
<table><thead><tr><th>Dimension</th><th>Components</th><th>Final contribution</th></tr></thead><tbody>
  <tr><td>Structure</td><td>base={_value(dimensions.get('structure_base_ppm'))}; volume quality={_value(dimensions.get('volume_quality_ppm'))}; alignment={_value(dimensions.get('volume_alignment'))}</td><td>{_value(dimensions.get('structure_with_volume_ppm'))}</td></tr>
  <tr><td>Trend</td><td>direction balance={_value(dimensions.get('trend_direction_balance_ppm'))}; ADX strength={_value(dimensions.get('trend_strength_ppm'))}</td><td>{_value(dimensions.get('trend_ppm'))}</td></tr>
  <tr><td>Momentum</td><td>RSI={_value(dimensions.get('rsi_component_ppm'))}; MACD={_value(dimensions.get('macd_component_ppm'))}; gross={_value(dimensions.get('momentum_gross_strength_ppm'))}; agreement={_value(dimensions.get('momentum_agreement_ppm'))}</td><td>{_value(dimensions.get('momentum_ppm'))}</td></tr>
</tbody></table>
<h2>Breakout Episode Diagnostic</h2>
<p class="boundary">authority={_value(breakout.get('authority_status'))}；此诊断不进入任何policy score，也不能产生action。</p>
<table><tbody>
  <tr><th>Breakout line / current close</th><td>{_value(breakout.get('breakout_line_nano_usd'))} / {_value(breakout.get('current_close_nano_usd'))}</td><th>Relationship</th><td>{_value(breakout.get('relationship'))}</td></tr>
  <tr><th>Distance / ATR-scaled distance</th><td>{_value(breakout.get('distance_nano_usd'))} / {_value(breakout.get('distance_atr_ppm'))}</td><th>Diagnostic hash</th><td><code>{_value(breakout.get('diagnostic_sha256'))}</code></td></tr>
</tbody></table>
<h2>Primary Policy Calculation</h2>
<table><tbody>
  <tr><th>Weights</th><td>{_value(primary.get('weights_ppm'))}</td></tr>
  <tr><th>Weighted contributions</th><td>{_value(primary.get('contributions_ppm'))}</td></tr>
  <tr><th>Net / Gross / Agreement</th><td>{_value(primary.get('net_signal_ppm'))} / {_value(primary.get('gross_strength_ppm'))} / {_value(primary.get('agreement_ppm'))}</td></tr>
</tbody></table>
<h2>Shadow Policies</h2>
<table><thead><tr><th>Policy</th><th>Score</th><th>Band</th><th>Action authority</th></tr></thead><tbody>{shadow_rows}</tbody></table>
<h2>Streak and Exposure</h2>
<table><tbody>
  <tr><th>below 40</th><td>{_value(updated_state.get('below_40_streak'))}</td><th>below 20</th><td>{_value(updated_state.get('below_20_streak'))}</td></tr>
  <tr><th>above 70</th><td>{_value(updated_state.get('above_70_streak'))}</td><th>Effective cap</th><td>{_value(action_document.get('effective_cap_units'))}</td></tr>
  <tr><th>Exit latch</th><td>{_value(action_document.get('exit_latch_status_before'))} → {_value(action_document.get('exit_latch_status_after'))}</td><th>Score cap</th><td>{_value(action_document.get('score_cap_units'))}</td></tr>
</tbody></table>
<h2>Deterministic Evidence</h2>
<table><tbody>
  <tr><th>Observation input</th><td><code>{_value(document.get('observation_input_sha256', score_document.get('input_sha256')))}</code></td></tr>
  <tr><th>Formula catalog</th><td><code>{_value(score_document.get('formula_catalog_sha256'))}</code></td></tr>
  <tr><th>Policy set</th><td><code>{_value(score_document.get('policy_set_sha256'))}</code></td></tr>
  <tr><th>Action policy</th><td><code>{_value(action_document.get('action_policy_sha256'))}</code></td></tr>
  <tr><th>Score result</th><td><code>{_value(score_document.get('result_sha256'))}</code></td></tr>
  <tr><th>Indicators</th><td><code>{_value(indicators.get('indicators_sha256'))}</code></td></tr>
  <tr><th>Dimensions</th><td><code>{_value(dimensions.get('dimension_sha256'))}</code></td></tr>
  <tr><th>Action result</th><td><code>{_value(action_document.get('action_result_sha256'))}</code></td></tr>
  <tr><th>Action input</th><td><code>{_value(action_document.get('action_snapshot_sha256'))}</code></td></tr>
</tbody></table>
"""
    return _page("GLD 持仓管理 Research F0", body)


def _owner_lookup(
    pairs: tuple[tuple[str, str], ...], key: str, reason_code: str
) -> str:
    for candidate, value in pairs:
        if candidate == key:
            return value
    raise ManagementResearchError(reason_code)


def _owner_action_text(current_units: int, target_units: int, action: str) -> str:
    if action == "HOLD":
        return f"保持{target_units}份"
    if action == "REDUCE":
        return f"从{current_units}份降低到{target_units}份"
    if action == "RECOVERY_READD":
        return f"从{current_units}份逐层恢复到{target_units}份"
    if action == "EXIT_DUE":
        return f"退出至{target_units}份"
    if action in {"ACTION_BLOCKED", "NO_DECISION", "RECONCILIATION_BLOCKED"}:
        return f"不执行新的调仓，保持{target_units}份"
    raise ManagementResearchError("OWNER_ACCEPTANCE_ACTION_TRANSLATION_UNKNOWN")


def _owner_actual_text(row: dict[str, object]) -> str:
    scenario_id = row.get("scenario_id")
    score_bp = row.get("actual_display_score_bp")
    current_units = row.get("current_units")
    target_units = row.get("actual_target_units")
    action = row.get("actual_action")
    reason = row.get("actual_reason_code")
    if (
        type(scenario_id) is not str
        or (score_bp is not None and type(score_bp) is not int)
        or type(current_units) is not int
        or type(target_units) is not int
        or type(action) is not str
        or type(reason) is not str
    ):
        raise ManagementResearchError("OWNER_ACCEPTANCE_ROW_INVALID")
    label = _owner_lookup(
        _OWNER_SCENARIO_LABELS,
        scenario_id,
        "OWNER_ACCEPTANCE_SCENARIO_TRANSLATION_UNKNOWN",
    )
    reason_text = _owner_lookup(
        _OWNER_REASON_TEXT,
        reason,
        "OWNER_ACCEPTANCE_REASON_TRANSLATION_UNKNOWN",
    )
    score_text = "分数无法评估" if score_bp is None else f"分数{_score_bp(score_bp)}"
    action_text = _owner_action_text(current_units, target_units, action)
    return f"{label}：{score_text}；{reason_text}；程序{action_text}。"


def _owner_rows_by_scenario(
    rows: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    expected_ids = tuple(
        scenario_id
        for group in _OWNER_ACCEPTANCE_GROUPS_F0
        for scenario_id in group.scenario_ids
    )
    if len(expected_ids) != len(set(expected_ids)):
        raise ManagementResearchError("OWNER_ACCEPTANCE_CATALOG_INVALID")
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        if type(row) is not dict or type(row.get("scenario_id")) is not str:
            raise ManagementResearchError("OWNER_ACCEPTANCE_ROW_INVALID")
        scenario_id = row["scenario_id"]
        if scenario_id in result:
            raise ManagementResearchError("OWNER_ACCEPTANCE_SCENARIO_SET_INVALID")
        result[scenario_id] = row
    if frozenset(result) != frozenset(expected_ids):
        raise ManagementResearchError("OWNER_ACCEPTANCE_SCENARIO_SET_INVALID")
    return result


def render_acceptance_index(rows: list[dict[str, object]]) -> str:
    """Render the six-question, plain-language owner acceptance page."""

    rows_by_scenario = _owner_rows_by_scenario(rows)
    rendered_groups: list[str] = []
    for ordinal, group in enumerate(_OWNER_ACCEPTANCE_GROUPS_F0, start=1):
        group_rows = tuple(rows_by_scenario[value] for value in group.scenario_ids)
        actual_items = "".join(
            f"<li>{_value(_owner_actual_text(row))}</li>" for row in group_rows
        )
        passed = all(
            row.get("automated_evidence_status") == "AUTOMATED_EVIDENCE_PASS"
            for row in group_rows
        )
        result_class = "owner-pass" if passed else "owner-fail"
        result_text = (
            "✓ 程序表现与已认可规则一致（自动证据通过）"
            if passed
            else "✕ 程序表现与已认可规则不一致（自动证据失败）"
        )
        rendered_groups.append(
            f'<section class="owner-card" data-owner-group="{_value(group.group_id)}">'
            f"<h2>{ordinal}. {_value(group.title)}</h2>"
            f"<h3>我们要确认什么</h3><p>{_value(group.question)}</p>"
            f"<h3>已认可的管理规则</h3><p>{_value(group.accepted_rule)}</p>"
            f"<h3>程序实际做了什么</h3><ul>{actual_items}</ul>"
            f'<p class="{result_class}">{result_text}</p>'
            '<p><a href="engineering-evidence.html">查看工程证据（通常不需要打开）</a></p>'
            "</section>"
        )
    body = f"""
<h1>Trading Platform｜GLD 持仓管理验收</h1>
<p class="subtitle">F0 研究验证版 · 本地模拟数据 · 不连接真实交易</p>
<p class="boundary">这里验收的是：程序是否确实执行了你已经认可的六条持仓管理规则。工程细节已移到第二层。</p>
<div class="owner-grid">{''.join(rendered_groups)}</div>
<div class="owner-next">
  <h2>你的人工确认</h2>
  <p>当前状态：<strong>等待Jake确认</strong>。看完后只需回复“1–6全部认同”；如有异议，请回复对应编号和原因。</p>
  <p>本页不是收益证明，也不是交易指令；程序没有连接券商或真实账户。</p>
</div>
"""
    return _page(
        "Trading Platform｜GLD 持仓管理验收",
        body,
        extra_style=_OWNER_PAGE_STYLE,
    )


def render_engineering_evidence_index(rows: list[dict[str, object]]) -> str:
    """Render the full 14-scenario engineering evidence matrix."""

    rendered_rows: list[str] = []
    for row in rows:
        rendered_rows.append(
            "<tr>"
            f"<td><code>{_value(row.get('scenario_id'))}</code><br>{_value(row.get('purpose'))}</td>"
            f"<td>{_value(row.get('key_input'))}</td>"
            f"<td>{_value(row.get('expected'))}</td>"
            f"<td>{_value(row.get('actual'))}</td>"
            f"<td>{_value(row.get('automated_evidence_status'))}</td>"
            f"<td class=\"pending\">{_value(row.get('owner_status'))}</td>"
            f"<td><a href=\"{_safe_href(row.get('card_href'))}\">Card</a> · "
            f"<a href=\"{_safe_href(row.get('result_href'))}\">Result JSON</a> · "
            f"<a href=\"{_safe_href(row.get('observation_href'))}\">Observation</a> · "
            f"<a href=\"{_safe_href(row.get('action_snapshot_href'))}\">Action facts</a><br>"
            f"input <code>{_value(row.get('input_sha256'))}</code><br>"
            f"action input <code>{_value(row.get('action_input_sha256'))}</code><br>"
            f"formula <code>{_value(row.get('formula_sha256'))}</code><br>"
            f"policy <code>{_value(row.get('policy_sha256'))}</code><br>"
            f"result <code>{_value(row.get('result_sha256'))}</code></td>"
            "</tr>"
        )
    body = f"""
<h1>Trading Platform｜GLD 持仓管理工程证据</h1>
<p class="boundary">RESEARCH_ONLY · NO_DECISION_EFFECT · 本页保留完整自动证据，不代表Jake已经人工接受。</p>
<p><a href="index.html">返回简版Owner验收</a></p>
<table>
  <thead><tr><th>场景</th><th>关键输入</th><th>预期结果</th><th>实际结果</th><th>自动证据</th><th>人工状态</th><th>详情与hash</th></tr></thead>
  <tbody>{''.join(rendered_rows)}</tbody>
</table>
<h2>工程证据 Checklist</h2>
<ul>
  <li>14个场景的预期与实际结果逐项一致。</li>
  <li>一个完整单位无法再分时，结果保持HOLD并记录NO_COMPLETE_UNIT_REDUCTION_AVAILABLE。</li>
  <li>BCS所有调整保持完整1:1组合；残腿或对账冲突阻止新动作。</li>
  <li>终局latch后的同一episode即使再次转强，也不能从0重新加仓。</li>
  <li>相同输入重复运行、跨进程运行及输入顺序变化时，canonical JSON与hash保持一致。</li>
  <li>Primary只产生一个action，shadow controls不产生平行建议。</li>
  <li>页面没有券商连接、下单或成交确认控件，broker写入计数为0。</li>
  <li>本结果仍是Research F0，不是策略盈利证明或真实交易指令。</li>
</ul>
<p class="boundary">人工终局状态：OWNER_ACCEPTANCE_PENDING。自动证据不得修改此状态。</p>
"""
    return _page("Trading Platform｜GLD 持仓管理工程证据", body)


__all__ = [
    "OwnerAcceptanceGroupF0",
    "owner_acceptance_group_definitions_f0",
    "render_acceptance_index",
    "render_engineering_evidence_index",
    "render_management_score_card",
]
