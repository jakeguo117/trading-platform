"""Static HTML renderers for the GLD Entry Decision Research F0 evidence.

The renderers consume an already-produced canonical DecisionResult and its
DecisionTrace.  They never call the evaluator and never derive, rank, size, or
repair a plan.
"""

from __future__ import annotations

from html import escape
import json
from pathlib import PurePosixPath
import re

from .canonical import canonical_json_sha256
from .errors import EntryDecisionF0Error


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_TRACE_STAGE_IDS = (
    "INPUT_QUALIFICATION",
    "ENTRY_GATE",
    "LC0_EVALUATION",
    "BCS0_EVALUATION",
    "SIZING",
    "PREFERENCE",
    "EXIT_POLICY_BINDING",
    "FINAL_DECISION",
)
_TRACE_STATES = frozenset({"PASS", "FAIL", "ELIMINATED", "BLOCKED", "NOT_RUN"})
_RESULT_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "authority_status",
        "actionable",
        "broker_order_count",
        "decision_status",
        "reason_code",
        "input_sha256",
        "policy_id",
        "policy_sha256",
        "plans",
        "decision_trace",
        "result_sha256",
    }
)
_PLAN_KEYS = frozenset(
    {
        "carrier_id",
        "contract_ids",
        "expiry_date",
        "safe_quantity",
        "current_entry_debit_per_unit_nano_usd",
        "stressed_entry_debit_per_unit_nano_usd",
        "planned_loss_per_unit_nano_usd",
        "delta_notional_per_unit_nano_usd",
        "expected_net_return_lower_bound_ppm",
        "expected_total_net_profit_nano_usd",
        "comparison_numerator",
        "sizing_caps",
        "binding_caps",
        "management_policy_id",
        "management_policy_sha256",
        "h20_date",
        "h20_exit_utc_ns",
        "expiry_safety_calendar_days",
        "invalidation_confirmation_sessions",
        "hard_stop_policy_id",
        "hard_stop_policy_sha256",
        "hard_stop_loss_ppm",
        "authority_status",
    }
)
_SIZING_CAP_KEYS = frozenset(
    {"q_kelly", "q_account", "q_cash", "q_delta", "q_liquidity", "q_external"}
)
_STAGE_KEYS = frozenset(
    {
        "stage_id",
        "stage_order",
        "state",
        "fact_references",
        "rule_id",
        "rule_version",
        "calculation",
        "reason_code",
        "recovery_code",
        "stage_sha256",
    }
)

_STATUS_ZH = {
    "NO_DECISION": "不形成判断",
    "NO_ACTION": "没有合格方案",
    "SINGLE_PLAN": "一个合格方案",
    "PREFERRED_AND_BACKUP": "首选方案与备选方案",
}

_STAGE_LABELS_ZH = {
    "INPUT_QUALIFICATION": "输入资格",
    "ENTRY_GATE": "Entry Gate",
    "LC0_EVALUATION": "LC0独立评估",
    "BCS0_EVALUATION": "BCS0独立评估",
    "SIZING": "安全数量",
    "PREFERENCE": "首选排序",
    "EXIT_POLICY_BINDING": "退出政策绑定",
    "FINAL_DECISION": "最终决策",
}

_STATE_LABELS_ZH = {
    "PASS": "通过",
    "FAIL": "失败",
    "ELIMINATED": "淘汰",
    "BLOCKED": "阻止判断",
    "NOT_RUN": "因前置条件未运行",
}

_STAGE_PURPOSE_ZH = {
    "INPUT_QUALIFICATION": "确认输入事实、完整期权池和已批准研究政策是否足够可信，可以进入判断。",
    "ENTRY_GATE": "确认趋势组与突破组是否分别达到建仓门槛。",
    "LC0_EVALUATION": "独立确认是否存在符合到期、安全距离与Delta规则的单腿Call。",
    "BCS0_EVALUATION": "独立确认是否存在符合到期、安全距离与Delta规则的1:1牛市Call价差。",
    "SIZING": "确认每个候选结构在全部六个硬上限约束下最多可以保留多少完整单位。",
    "PREFERENCE": "当一个或两个结构有安全数量时，按冻结顺序形成唯一首选和备选。",
    "EXIT_POLICY_BINDING": "确认输出方案在形成时已经绑定H20、硬止损、趋势失效与到期安全规则。",
    "FINAL_DECISION": "汇总前七步，形成唯一研究结论，并再次确认不能下单。",
}

_STAGE_HUMAN_RULE_ZH = {
    "INPUT_QUALIFICATION": "完整事实、完整原子快照、完整Call universe和Owner批准的固定政策缺一不可；缺失时停止。",
    "ENTRY_GATE": "趋势组与突破组必须分别达到本次封存政策的配额，不能相互补分。",
    "LC0_EVALUATION": "LC0单独筛选；coarse与fine选择必须一致，且证据、合约与到期条件都合格。",
    "BCS0_EVALUATION": "BCS0单独筛选完整1:1组合；coarse与fine选择必须一致，不借用LC0结论。",
    "SIZING": "安全数量取Kelly、账户、现金、Delta、流动性、外部风险六个cap的最小值；0不能补成1。",
    "PREFERENCE": "依次比较预期总净利润（高者优先）、计划总损失（低者优先）、现金占用（低者优先）；完全相同固定LC0优先。",
    "EXIT_POLICY_BINDING": "每个输出方案必须绑定同一份持仓管理政策及自身硬止损；缺少任何绑定就不输出。",
    "FINAL_DECISION": "只输出NO_DECISION、NO_ACTION、SINGLE_PLAN或PREFERRED_AND_BACKUP之一；Research F0始终不可执行。",
}

_REASON_ZH = {
    "INPUT_QUALIFICATION_PASS": "输入事实、期权池和研究政策均通过资格检查。",
    "CALL_UNIVERSE_INCOMPLETE": "Call universe不完整，程序不能判断。",
    "OWNER_APPROVED_POLICY_NOT_AVAILABLE": "没有可用的Owner批准政策，程序不能判断。",
    "ACTIVE_ENTRY_POLICY_NOT_AVAILABLE": "当前没有可用的建仓政策。",
    "DATA_NOT_QUALIFIED": "输入数据没有通过资格化。",
    "DATA_QUALIFICATION_CONFLICT": "数据资格信息相互冲突。",
    "FUTURE_DATA_DETECTED": "输入包含判断时点之后的数据。",
    "OPTION_ATOMIC_SNAPSHOT_MISMATCH": "至少一个期权报价不属于本次完整原子快照。",
    "OPTION_TECHNICAL_FACT_BINDING_MISMATCH": "至少一个期权报价没有绑定本次Technical Facts。",
    "OPTION_ATOMIC_SNAPSHOT_RECEIVE_SKEW_EXCEEDED": "同一快照内的期权报价接收时间差超过允许范围。",
    "OPTION_QUOTE_STALE": "期权报价相对10:45判断时点已经过旧。",
    "UNRECONCILED_GLD_OPEN_ORDER": "存在未对账的GLD开放订单。",
    "ENTRY_GATE_PASS": "趋势组和突破组均分别达到固定门槛。",
    "ENTRY_GATE_QUOTA_NOT_MET": "至少一个Gate分组没有达到自己的固定门槛。",
    "ENTRY_GATE_FACT_MISSING": "Gate所需事实不完整。",
    "LC0_ELIGIBLE": "LC0存在一个独立合格合约。",
    "LC0_NO_ELIGIBLE_CONTRACT": "LC0没有符合全部规则的合约。",
    "LC0_COARSE_FINE_SELECTION_DISAGREEMENT": "LC0的粗筛与精算选择不一致。",
    "BCS0_ELIGIBLE": "BCS0存在一个独立合格的完整1:1组合。",
    "BCS0_NO_ELIGIBLE_PAIR": "BCS0没有符合全部规则的完整组合。",
    "BCS0_COARSE_FINE_SELECTION_DISAGREEMENT": "BCS0的粗筛与精算选择不一致。",
    "STRUCTURE_EVIDENCE_MISSING": "该结构没有独立历史证据，因此被单独淘汰。",
    "STRUCTURE_EVIDENCE_CLASSIFICATION_MISMATCH": "该结构的证据成熟度与本次输入资格不匹配。",
    "STRUCTURE_EVIDENCE_ENTRY_POLICY_BINDING_MISMATCH": "该结构的历史证据不是由本次Entry政策产生，不能复用。",
    "STRUCTURE_EVIDENCE_FUTURE_OUTCOME": "该结构证据使用了本次判断时点之后的结果。",
    "STRUCTURE_EVIDENCE_POLICY_BINDING_MISMATCH": "该结构证据绑定的退出政策或费用版本与本次方案不同。",
    "STRUCTURE_EVIDENCE_OOS_EPISODES_INSUFFICIENT": "该结构的OOS样本少于最低要求。",
    "STRUCTURE_EVIDENCE_FOLDS_INSUFFICIENT": "该结构的有效walk-forward folds少于最低要求。",
    "STRUCTURE_EVIDENCE_COVERAGE_INSUFFICIENT": "该结构的证据覆盖率低于最低要求。",
    "STRUCTURE_EXPECTED_RETURN_LOWER_BOUND_NOT_POSITIVE": "该结构的预期净收益下界不为正。",
    "STRUCTURE_HALF_KELLY_ZERO": "该结构的稳健Half-Kelly为0。",
    "STRUCTURE_EXIT_POLICY_MISSING": "该结构没有完整绑定独立退出政策。",
    "ALL_STRUCTURES_ELIMINATED": "LC0和BCS0都在独立资格检查中被淘汰。",
    "SIZING_PASS": "至少一个结构的六个硬cap允许完整单位。",
    "ALL_STRUCTURES_ZERO_QUANTITY": "所有候选结构的安全数量都是0，因此不形成方案。",
    "SIZING_REQUIRED_FACT_BLOCKED": "计算数量所需的必要风险事实缺失。",
    "SIZING_REQUIRED_CAP_MISSING": "至少一个必要硬cap缺失。",
    "PLANNED_EXIT_FEE_MISSING": "计算计划损失时缺少退出费用。",
    "BCS0_MULTIPLIER_MISMATCH": "BCS0两腿的合约乘数不一致，不能组成完整1:1单位。",
    "ENTRY_DEBIT_NOT_POSITIVE": "按可执行报价计算的入场净支出不为正。",
    "STRUCTURE_ZERO_SAFE_QUANTITY": "该结构被至少一个硬cap限制为0。",
    "SINGLE_ELIGIBLE_STRUCTURE": "只有一个结构保有正的安全数量，因此直接成为唯一方案。",
    "PREFERENCE_DETERMINED": "两个结构均合格，已按固定比较顺序确定首选与备选。",
    "EXIT_POLICY_BOUND": "每个输出方案已经完整绑定退出与持仓管理规则。",
    "NO_DECISION": "必要事实或政策权限不足，程序没有形成判断。",
    "NO_ACTION": "事实已经评估，但没有可输出的正数量方案。",
    "SINGLE_PLAN": "只有一个结构完整合格，程序只输出这一套方案。",
    "PREFERRED_AND_BACKUP": "两个结构均合格，程序已给出唯一首选和备选。",
}

_RECOVERY_ZH = {
    "NONE": "无需恢复；这是测试夹具中的已完成阶段。",
    "CONTINUE_ENTRY_GATE": "继续进入Gate判断。",
    "CONTINUE_STRUCTURE_EVALUATION": "继续分别评估LC0与BCS0。",
    "CONTINUE_SIZING": "保留该合格结构并进入数量计算。",
    "CONTINUE_PREFERENCE": "用正数量方案继续执行固定偏好排序。",
    "BIND_EXIT_POLICY": "把完整退出与持仓管理政策绑定到输出方案。",
    "FORM_FINAL_RESEARCH_DECISION": "形成最终的不可执行研究结论。",
    "OWNER_REVIEW_RESEARCH_DECISION_CARD": "由Owner阅读本卡片并人工验收。",
    "REFRESH_COMPLETE_ATOMIC_UNIVERSE": "重新提供同一原子快照中的完整Call universe后重跑。",
    "OWNER_APPROVE_POLICY_AND_RERUN": "由Owner批准固定政策后重新运行。",
    "OWNER_APPROVE_ACTIVE_ENTRY_POLICY": "由Owner明确启用一份建仓政策后重跑。",
    "QUALIFY_INPUT_DATA_AND_RERUN": "先完成输入数据资格化，再重新运行。",
    "RECONCILE_OPEN_ORDERS_AND_RERUN": "先完成开放订单对账，再重新运行。",
    "REFRESH_CAUSAL_FACTS_AND_RERUN": "移除未来数据并刷新同一判断时点的因果事实后重跑。",
    "REFRESH_ONE_ATOMIC_SNAPSHOT": "重新取得一份完整、同源的期权原子快照后重跑。",
    "REDERIVE_TECHNICAL_FACTS_AND_RERUN": "从已绑定的原始事实重新计算Technical Facts后重跑。",
    "REFRESH_SYNCHRONIZED_ATOMIC_SNAPSHOT": "重新取得接收时间同步的完整期权快照后重跑。",
    "REFRESH_FRESH_ATOMIC_SNAPSHOT": "在允许的新鲜度窗口内取得新报价后重跑。",
    "REFRESH_QUALIFICATION_RECEIPT": "刷新并核对数据资格回执后重跑。",
    "USE_SYNTHETIC_QUALIFICATION_STATUS": "Synthetic输入必须使用对应的结构有效资格状态。",
    "REFRESH_REQUIRED_GATE_FACTS": "补齐并刷新Gate所需事实后重跑。",
    "WAIT_FOR_NEW_COMPLETE_GATE_OBSERVATION": "等待下一份完整观察；当前Gate失败本身不能被补分。",
    "REFRESH_LC0_CANDIDATE_OR_EVIDENCE": "刷新LC0候选或独立证据后重跑。",
    "REFRESH_BCS0_CANDIDATE_OR_EVIDENCE": "刷新BCS0候选或独立证据后重跑。",
    "REFRESH_REQUIRED_SIZING_FACTS_AND_RERUN": "补齐数量和风险上限事实后重跑。",
    "REFRESH_CAPS_OR_WAIT_FOR_RISK_CAPACITY": "等待风险容量恢复，或用新的完整cap事实重跑。",
    "RESOLVE_BLOCKING_STAGE_AND_RERUN": "先解决上游停止原因；本阶段不能跳过上游单独恢复。",
}

_FIELD_LABELS = {
    "carrier_id": "方案类型",
    "preference_rank": "偏好顺序",
    "preference_status": "偏好状态",
    "quantity": "完整合约单位数",
    "safe_quantity": "安全数量",
    "target_quantity": "目标数量",
    "contract_ids": "合约",
    "long_contract_id": "Long Call",
    "short_contract_id": "Short Call",
    "entry_debit_nano_usd": "入场成本（nano_usd）",
    "stressed_debit_nano_usd": "压力成本（nano_usd）",
    "max_loss_nano_usd": "最大损失基础（nano_usd）",
    "expected_total_net_profit_nano_usd": "预期总净利润（nano_usd）",
    "expected_total_net_profit": "预期总净利润",
    "current_entry_debit_per_unit_nano_usd": "当前入场成本/单位（nano_usd）",
    "stressed_entry_debit_per_unit_nano_usd": "压力入场成本/单位（nano_usd）",
    "planned_loss_per_unit_nano_usd": "计划损失/单位（nano_usd）",
    "delta_notional_per_unit_nano_usd": "Delta名义敞口/单位（nano_usd）",
    "expected_net_return_lower_bound_ppm": "预期净收益下界（ppm）",
    "comparison_numerator": "偏好比较整数分子",
    "sizing_caps": "全部数量硬cap",
    "binding_caps": "实际绑定cap",
    "management_policy_id": "持仓管理政策",
    "management_policy_sha256": "持仓管理政策hash",
    "h20_date": "H20日期",
    "h20_exit_utc_ns": "H20 10:45 ET（UTC ns）",
    "expiry_safety_calendar_days": "Expiry safety间隔（日）",
    "invalidation_confirmation_sessions": "失效确认所需完整session",
    "hard_stop_policy_id": "Hard Stop政策",
    "hard_stop_policy_sha256": "Hard Stop政策hash",
    "hard_stop_loss_ppm": "Hard Stop亏损阈值（ppm）",
    "authority_status": "权限状态",
    "reason_code": "原因代码",
    "plan_sha256": "方案 hash",
}

_OWNER_ACCEPTANCE_GROUPS: tuple[dict[str, object], ...] = (
    {
        "title": "缺数据与Gate失败是否正确区分",
        "question": "程序能否区分“事实不足，不能判断”和“事实完整，但条件不通过”？",
        "accepted_rule": "缺必要事实输出NO_DECISION；事实完整而Gate失败输出NO_ACTION。",
        "scenario_ids": ("missing_input", "gate_fail"),
    },
    {
        "title": "Gate是否按两个组和固定政策判断",
        "question": "Trend和Breakout是否分别计数，并按冻结的3/3与2/2政策判断？",
        "accepted_rule": "两个组必须分别达到quota；一组的富余不能补另一组的不足。",
        "scenario_ids": ("gate_fail", "both_qualified"),
    },
    {
        "title": "LC0与BCS0是否真正独立",
        "question": "两个结构是否拥有各自的筛选、economics、evidence、Kelly和失败原因？",
        "accepted_rule": "LC0与BCS0分别评估，不共享winner、收益分布或Kelly receipt。",
        "scenario_ids": ("lc_only", "bcs_only", "both_qualified"),
    },
    {
        "title": "一个结构失败时另一个是否仍可输出",
        "question": "程序会不会因为一个结构失败而错误取消另一个完整合格方案？",
        "accepted_rule": "只有一个合格时输出该单一方案，不要求两个结构同时通过。",
        "scenario_ids": ("lc_only", "bcs_only"),
    },
    {
        "title": "数量是否取所有硬cap的最小值",
        "question": "Kelly、账户、现金、Delta、流动性和外部风险是否都能限制数量？",
        "accepted_rule": "safe quantity取全部硬cap最小值；任何适用硬cap为0都不能向上补成1。",
        "scenario_ids": ("both_qualified", "zero_cap"),
    },
    {
        "title": "双合格时是否有确定首选和备选",
        "question": "两个方案都合格时，程序是否仍给出明确preference，并处理完全平局？",
        "accepted_rule": "先比较lower-bound预期总净利润；完全平局使用固定LC0 tie-break。",
        "scenario_ids": ("both_qualified", "exact_tie"),
    },
    {
        "title": "每个方案是否绑定完整退出政策",
        "question": "最终方案是否在建仓时就绑定H20、Hard Stop、失效确认与expiry safety？",
        "accepted_rule": "每个输出计划均携带自身退出政策和Management policy hash。",
        "scenario_ids": ("both_qualified",),
    },
    {
        "title": "只看卡片是否能复述全过程",
        "question": "不查看代码时，能否从卡片说明输入、Gate、结构、数量、偏好和退出绑定？",
        "accepted_rule": "Final Decision Card直接展示最终计划和顺序固定的八步Decision Trace。",
        "scenario_ids": ("both_qualified",),
    },
    {
        "title": "中途停止是否说明位置、原因和恢复条件",
        "question": "流程fail closed时，是否保留全部八个stage并解释为何未继续？",
        "accepted_rule": "失败stage记录reason/recovery；所有后续stage保留为NOT_RUN而不是消失。",
        "scenario_ids": ("missing_input", "gate_fail", "zero_cap", "policy_missing"),
    },
    {
        "title": "相同输入是否完全相同且broker writes恒为0",
        "question": "跨进程和Call/economics列表顺序变化后，JSON、hash与卡片是否仍逐字节相同？",
        "accepted_rule": "相同事实经canonical排序后产生相同输出；所有场景broker_order_count=0。",
        "scenario_ids": (
            "missing_input",
            "gate_fail",
            "lc_only",
            "bcs_only",
            "both_qualified",
            "exact_tie",
            "zero_cap",
            "policy_missing",
        ),
    },
)


def _as_result_dict(value: object) -> dict[str, object]:
    if hasattr(value, "as_dict") and callable(value.as_dict):
        value = value.as_dict()
    if type(value) is not dict or frozenset(value) != _RESULT_KEYS:
        raise EntryDecisionF0Error("DECISION_CARD_RESULT_SCHEMA_INVALID")
    if (
        value.get("classification") != "RESEARCH_ONLY"
        or value.get("authority_status") != "NO_DECISION_EFFECT"
        or value.get("actionable") is not False
        or value.get("broker_order_count") != 0
    ):
        raise EntryDecisionF0Error("DECISION_CARD_AUTHORITY_BOUNDARY_INVALID")
    if value.get("schema_version") != "ENTRY_DECISION_RESULT_F0_V1":
        raise EntryDecisionF0Error("DECISION_CARD_RESULT_SCHEMA_INVALID")
    if type(value.get("reason_code")) is not str:
        raise EntryDecisionF0Error("DECISION_CARD_RESULT_SCHEMA_INVALID")
    if (
        type(value.get("input_sha256")) is not str
        or _SHA256_RE.fullmatch(value["input_sha256"]) is None
        or type(value.get("policy_id")) is not str
        or not value["policy_id"]
        or type(value.get("policy_sha256")) is not str
        or _SHA256_RE.fullmatch(value["policy_sha256"]) is None
    ):
        raise EntryDecisionF0Error("DECISION_CARD_LINEAGE_INVALID")
    status = value.get("decision_status")
    if type(status) is not str or status not in _STATUS_ZH:
        raise EntryDecisionF0Error("DECISION_CARD_STATUS_INVALID")
    plans = value.get("plans")
    if type(plans) is not dict or frozenset(plans) != frozenset({"preferred", "backup"}):
        raise EntryDecisionF0Error("DECISION_CARD_PLAN_SET_INVALID")
    if any(plan is not None and type(plan) is not dict for plan in plans.values()):
        raise EntryDecisionF0Error("DECISION_CARD_PLAN_SET_INVALID")
    if plans.get("preferred") is None and plans.get("backup") is not None:
        raise EntryDecisionF0Error("DECISION_CARD_PLAN_SLOT_INVALID")
    plan_values = tuple(plan for plan in plans.values() if type(plan) is dict)
    if any(frozenset(plan) != _PLAN_KEYS for plan in plan_values):
        raise EntryDecisionF0Error("DECISION_CARD_PLAN_SCHEMA_INVALID")
    if any(
        plan.get("carrier_id") not in {"LC0", "BCS0"}
        or type(plan.get("safe_quantity")) is not int
        or plan.get("safe_quantity", 0) <= 0
        or plan.get("authority_status") != "NO_DECISION_EFFECT"
        for plan in plan_values
    ):
        raise EntryDecisionF0Error("DECISION_CARD_PLAN_AUTHORITY_INVALID")
    if len({plan["carrier_id"] for plan in plan_values}) != len(plan_values):
        raise EntryDecisionF0Error("DECISION_CARD_PLAN_SET_INVALID")
    for plan in plan_values:
        sizing_caps = plan.get("sizing_caps")
        binding_caps = plan.get("binding_caps")
        if (
            type(plan.get("contract_ids")) is not list
            or not plan["contract_ids"]
            or any(type(value) is not str or not value for value in plan["contract_ids"])
            or type(sizing_caps) is not dict
            or frozenset(sizing_caps) != _SIZING_CAP_KEYS
            or any(type(value) is not int or value < 0 for value in sizing_caps.values())
            or type(binding_caps) is not list
            or not binding_caps
            or any(value not in _SIZING_CAP_KEYS for value in binding_caps)
            or type(plan.get("management_policy_id")) is not str
            or not plan["management_policy_id"]
            or type(plan.get("management_policy_sha256")) is not str
            or _SHA256_RE.fullmatch(plan["management_policy_sha256"]) is None
        ):
            raise EntryDecisionF0Error("DECISION_CARD_PLAN_SCHEMA_INVALID")
    result_hash = value.get("result_sha256")
    if type(result_hash) is not str or _SHA256_RE.fullmatch(result_hash) is None:
        raise EntryDecisionF0Error("DECISION_CARD_RESULT_HASH_INVALID")
    trace = value.get("decision_trace")
    if type(trace) is not dict or frozenset(trace) != frozenset(
        {"schema_version", "stages", "trace_sha256"}
    ):
        raise EntryDecisionF0Error("DECISION_CARD_TRACE_SCHEMA_INVALID")
    if trace.get("schema_version") != "ENTRY_DECISION_TRACE_F0_V1":
        raise EntryDecisionF0Error("DECISION_CARD_TRACE_SCHEMA_INVALID")
    trace_hash = trace.get("trace_sha256")
    stages = trace.get("stages")
    if (
        type(trace_hash) is not str
        or _SHA256_RE.fullmatch(trace_hash) is None
        or type(stages) is not list
        or len(stages) != 8
    ):
        raise EntryDecisionF0Error("DECISION_CARD_TRACE_SCHEMA_INVALID")
    for expected_order, stage in enumerate(stages, start=1):
        if type(stage) is not dict or frozenset(stage) != _STAGE_KEYS:
            raise EntryDecisionF0Error("DECISION_CARD_STAGE_SCHEMA_INVALID")
        if stage.get("stage_order") != expected_order:
            raise EntryDecisionF0Error("DECISION_CARD_STAGE_ORDER_INVALID")
        if stage.get("stage_id") != _TRACE_STAGE_IDS[expected_order - 1]:
            raise EntryDecisionF0Error("DECISION_CARD_STAGE_ORDER_INVALID")
        if stage.get("state") not in _TRACE_STATES:
            raise EntryDecisionF0Error("DECISION_CARD_STAGE_SCHEMA_INVALID")
        if any(
            type(stage.get(key)) is not str
            for key in (
                "stage_id",
                "state",
                "rule_id",
                "rule_version",
                "reason_code",
                "recovery_code",
                "stage_sha256",
            )
        ):
            raise EntryDecisionF0Error("DECISION_CARD_STAGE_SCHEMA_INVALID")
        stage_hash = stage.get("stage_sha256")
        if type(stage_hash) is not str or _SHA256_RE.fullmatch(stage_hash) is None:
            raise EntryDecisionF0Error("DECISION_CARD_STAGE_HASH_INVALID")
        if (
            type(stage.get("fact_references")) is not list
            or any(type(value) is not str for value in stage["fact_references"])
            or type(stage.get("calculation")) is not dict
        ):
            raise EntryDecisionF0Error("DECISION_CARD_STAGE_SCHEMA_INVALID")
        unsigned_stage = dict(stage)
        sealed_stage_hash = unsigned_stage.pop("stage_sha256")
        if sealed_stage_hash != canonical_json_sha256(unsigned_stage):
            raise EntryDecisionF0Error("DECISION_CARD_STAGE_HASH_MISMATCH")
    unsigned_trace = dict(trace)
    unsigned_trace.pop("trace_sha256")
    if trace_hash != canonical_json_sha256(unsigned_trace):
        raise EntryDecisionF0Error("DECISION_CARD_TRACE_HASH_MISMATCH")
    unsigned_result = dict(value)
    unsigned_result.pop("result_sha256")
    if result_hash != canonical_json_sha256(unsigned_result):
        raise EntryDecisionF0Error("DECISION_CARD_RESULT_HASH_MISMATCH")
    return value


def _value(value: object) -> str:
    if value is None:
        return "—"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in {dict, list}:
        try:
            rendered = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise EntryDecisionF0Error("DECISION_CARD_VALUE_INVALID") from exc
        return escape(rendered, quote=True)
    return escape(str(value), quote=True)


def _safe_href(value: object) -> str:
    if type(value) is not str or not value or "\\" in value or ":" in value:
        return "#"
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return "#"
    return escape(value, quote=True)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont,
      "Segoe UI", "PingFang SC", sans-serif; color: #17202a; background: #f5f7fa; }}
    body {{ margin: 0; padding: 24px; }} main {{ max-width: 1180px; margin: 0 auto; }}
    h1 {{ margin: 0 0 8px; }} h2 {{ margin-top: 28px; }}
    .subtitle {{ margin: 0 0 18px; color: #52616b; }}
    .boundary {{ padding: 14px 16px; border: 1px solid #d35400; border-radius: 10px;
      background: #fff4e6; font-weight: 650; line-height: 1.55; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px; margin-top: 16px; }}
    .metric, .owner-card, .stage-card, .plan-card {{ padding: 16px; border: 1px solid #d9e1e8;
      border-radius: 12px; background: white; }}
    .metric span {{ display: block; color: #52616b; font-size: 13px; }}
    .metric strong {{ display: block; margin-top: 6px; overflow-wrap: anywhere; }}
    .owner-grid {{ display: grid; gap: 18px; margin-top: 20px; }}
    .owner-card h2 {{ margin: 0 0 12px; }} .owner-card h3 {{ margin: 16px 0 6px; }}
    .stage-grid {{ display: grid; gap: 16px; margin-top: 18px; }}
    .stage-card h3, .plan-card h3 {{ margin: 0 0 12px; }}
    .stage-fields {{ display: grid; grid-template-columns: minmax(150px, 0.3fr) 1fr;
      gap: 8px 16px; margin: 0; }}
    .stage-fields dt {{ color: #52616b; font-weight: 650; }}
    .stage-fields dd {{ margin: 0; line-height: 1.55; }}
    details {{ margin-top: 14px; padding: 10px 12px; border: 1px solid #e1e7ed;
      border-radius: 8px; background: #f8fafc; }}
    summary {{ cursor: pointer; color: #34495e; font-weight: 650; }}
    .details-table {{ margin-top: 10px; }}
    .section-note {{ color: #52616b; line-height: 1.55; }}
    .pass {{ color: #155724; background: #eaf7ed; padding: 9px; border-radius: 8px;
      font-weight: 700; }} .fail {{ color: #842029; background: #f8d7da; padding: 9px;
      border-radius: 8px; font-weight: 700; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ padding: 10px; border: 1px solid #d9e1e8; text-align: left;
      vertical-align: top; overflow-wrap: anywhere; }} th {{ background: #edf2f7; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
    a {{ color: #0b62a4; }} .pending {{ color: #8a4b08; font-weight: 700; }}
  </style>
</head>
<body><main>{body}</main></body>
</html>
"""


def _plan_title(index: int, plan: dict[str, object]) -> str:
    if index == 0:
        return "首选方案"
    if index == 1:
        return "备选方案"
    return f"方案 {index + 1}"


def _render_plan(index: int, plan: dict[str, object]) -> str:
    raw_rows = "".join(
        f"<tr><th>{escape(_FIELD_LABELS.get(key, key))}</th><td>{_value(value)}</td></tr>"
        for key, value in sorted(plan.items())
    )
    return f"""
<section class="plan-card">
  <h3>{_plan_title(index, plan)}：{_value(plan.get('carrier_id'))}</h3>
  <div class="grid">
    <div class="metric"><span>完整合约单位</span><strong>{_value(plan.get('safe_quantity'))}</strong></div>
    <div class="metric"><span>合约</span><strong>{_value(plan.get('contract_ids'))}</strong></div>
    <div class="metric"><span>预期总净利润下界对应值</span><strong>{_value(plan.get('expected_total_net_profit_nano_usd'))} nano_usd</strong></div>
    <div class="metric"><span>到期日</span><strong>{_value(plan.get('expiry_date'))}</strong></div>
  </div>
  <details><summary>展开完整方案工程字段</summary>
    <table class="details-table"><tbody>{raw_rows}</tbody></table>
  </details>
</section>
"""


def _reason_zh(reason_code: object) -> str:
    if type(reason_code) is not str:
        return "程序没有提供可解释的原因。"
    return _REASON_ZH.get(reason_code, "该工程原因尚无中文短句，请在展开详情中查看原始reason code。")


def _recovery_zh(recovery_code: object) -> str:
    if type(recovery_code) is not str:
        return "程序没有提供恢复路径。"
    return _RECOVERY_ZH.get(recovery_code, "请先处理上游事实或政策问题，再用完整输入重新运行。")


def _stage_fact_summary(stage: dict[str, object]) -> str:
    stage_id = stage.get("stage_id")
    calculation = stage.get("calculation")
    if type(calculation) is not dict:
        return "该阶段没有可展示的结构化事实摘要。"
    blocked_by = calculation.get("blocked_by_reason_code")
    if type(blocked_by) is str:
        return f"上游已停止：{_value(_reason_zh(blocked_by))}"
    if stage_id == "INPUT_QUALIFICATION":
        return (
            f"数据资格：{_value(calculation.get('data_qualification_status'))}；"
            f"完整期权池：{_value(calculation.get('universe_complete'))}；"
            f"合约数量：{_value(calculation.get('universe_length'))}；"
            f"开放GLD订单：{_value(calculation.get('open_gld_order_count'))}；"
            f"政策状态：{_value(calculation.get('policy_status'))}。"
        )
    if stage_id == "ENTRY_GATE":
        trend = calculation.get("trend")
        breakout = calculation.get("breakout")
        if type(trend) is dict and type(breakout) is dict:
            return (
                f"趋势组 {_value(trend.get('pass_count'))}/{_value(trend.get('required_count'))}；"
                f"突破组 {_value(breakout.get('pass_count'))}/{_value(breakout.get('required_count'))}；"
                f"两组独立通过：{_value(calculation.get('groups_must_separately_pass'))}。"
            )
    if stage_id == "LC0_EVALUATION":
        return (
            f"粗筛：{_value(calculation.get('coarse_selected_contract_id'))}；"
            f"精算：{_value(calculation.get('fine_selected_contract_id'))}；"
            f"最终合约：{_value(calculation.get('selected_contract_id'))}；"
            f"Delta：{_value(calculation.get('selected_delta_ppm'))} ppm；"
            f"到期：{_value(calculation.get('selected_expiry_date'))}。"
        )
    if stage_id == "BCS0_EVALUATION":
        return (
            f"粗筛组合：{_value(calculation.get('coarse_selected_contract_ids'))}；"
            f"精算组合：{_value(calculation.get('fine_selected_contract_ids'))}；"
            f"最终1:1组合：{_value(calculation.get('selected_contract_ids'))}；"
            f"Long/Short Delta：{_value(calculation.get('long_delta_ppm'))}/"
            f"{_value(calculation.get('short_delta_ppm'))} ppm。"
        )
    if stage_id == "SIZING":
        structures = calculation.get("structures")
        structure_reasons = calculation.get("structure_reasons")
        if type(structures) is dict and structures:
            summaries = []
            for carrier_id in ("LC0", "BCS0"):
                item = structures.get(carrier_id)
                if type(item) is dict:
                    reason_code = (
                        structure_reasons.get(carrier_id)
                        if type(structure_reasons) is dict
                        else None
                    )
                    summaries.append(
                        f"{carrier_id}安全数量 {_value(item.get('safe_quantity'))}，"
                        f"绑定cap {_value(item.get('binding_caps'))}，"
                        f"状态原因 {_reason_zh(reason_code)}"
                    )
            if summaries:
                return "；".join(summaries) + "。"
    if stage_id == "PREFERENCE":
        return (
            f"固定排序结果：{_value(calculation.get('ranking_order'))}；"
            f"备选使用前需刷新并重跑："
            f"{_value(calculation.get('backup_requires_fresh_snapshot_and_rerun'))}。"
        )
    if stage_id == "EXIT_POLICY_BINDING":
        invalidation = calculation.get("confirmed_invalidation")
        sessions = invalidation.get("complete_sessions_required") if type(invalidation) is dict else None
        return (
            f"持仓管理政策：{_value(calculation.get('management_policy_id'))}；"
            f"H20：{_value(calculation.get('h20_date'))}；"
            f"到期安全间隔：{_value(calculation.get('expiry_safety_calendar_days'))}日；"
            f"趋势失效确认：{_value(sessions)}个完整session。"
        )
    if stage_id == "FINAL_DECISION":
        return (
            f"最终状态：{_value(calculation.get('decision_status'))}；"
            f"首选：{_value(calculation.get('preferred_carrier_id'))}；"
            f"备选：{_value(calculation.get('backup_carrier_id'))}；"
            f"可执行：{_value(calculation.get('actionable'))}；"
            f"券商写入：{_value(calculation.get('broker_order_count'))}。"
        )
    return "该阶段没有额外的Owner摘要；完整计算保留在展开详情中。"


def _stage_human_rule(stage: dict[str, object]) -> str:
    stage_id = str(stage["stage_id"])
    if stage_id != "ENTRY_GATE":
        return _STAGE_HUMAN_RULE_ZH[stage_id]
    calculation = stage.get("calculation")
    if type(calculation) is not dict:
        return _STAGE_HUMAN_RULE_ZH[stage_id]
    trend = calculation.get("trend")
    breakout = calculation.get("breakout")
    if type(trend) is not dict or type(breakout) is not dict:
        return _STAGE_HUMAN_RULE_ZH[stage_id]
    trend_required = trend.get("required_count")
    breakout_required = breakout.get("required_count")
    if type(trend_required) is not int or type(breakout_required) is not int:
        return _STAGE_HUMAN_RULE_ZH[stage_id]
    return (
        f"本次封存政策要求趋势组至少{trend_required}/3，"
        f"突破组至少{breakout_required}/2；两组分别通过，不能相互补分。"
    )


def _render_stage_cards(stages: list[object]) -> str:
    rendered: list[str] = []
    for stage in stages:
        assert type(stage) is dict
        stage_id = str(stage["stage_id"])
        engineering_rows = (
            f"<tr><th>calculation</th><td>{_value(stage.get('calculation'))}</td></tr>"
            f"<tr><th>fact references</th><td>{_value(stage.get('fact_references'))}</td></tr>"
            f"<tr><th>rule</th><td><code>{_value(stage.get('rule_id'))}</code> / "
            f"<code>{_value(stage.get('rule_version'))}</code></td></tr>"
            f"<tr><th>reason / recovery code</th><td><code>{_value(stage.get('reason_code'))}</code> / "
            f"<code>{_value(stage.get('recovery_code'))}</code></td></tr>"
            f"<tr><th>stage hash</th><td><code>{_value(stage.get('stage_sha256'))}</code></td></tr>"
        )
        rendered.append(
            '<section class="stage-card">'
            f"<h3>{_value(stage.get('stage_order'))}. {_value(_STAGE_LABELS_ZH[stage_id])}</h3>"
            '<dl class="stage-fields">'
            f"<dt>我们在确认什么</dt><dd>{_value(_STAGE_PURPOSE_ZH[stage_id])}</dd>"
            f"<dt>关键事实摘要</dt><dd>{_stage_fact_summary(stage)}</dd>"
            f"<dt>应用的人类规则</dt><dd>{_value(_stage_human_rule(stage))}</dd>"
            f"<dt>程序结果</dt><dd><strong>{_value(_STATE_LABELS_ZH[str(stage.get('state'))])}</strong></dd>"
            f"<dt>直接原因中文</dt><dd>{_value(_reason_zh(stage.get('reason_code')))}</dd>"
            f"<dt>失败时如何恢复中文</dt><dd>{_value(_recovery_zh(stage.get('recovery_code')))}</dd>"
            "</dl>"
            "<details><summary>展开工程证据</summary>"
            f'<table class="details-table"><tbody>{engineering_rows}</tbody></table>'
            "</details></section>"
        )
    return '<div class="stage-grid">' + "".join(rendered) + "</div>"


def _render_structure_comparison(stages: list[object]) -> str:
    by_id = {stage["stage_id"]: stage for stage in stages if type(stage) is dict}
    rows: list[str] = []
    for carrier_id in ("LC0", "BCS0"):
        stage = by_id[f"{carrier_id}_EVALUATION"]
        calculation = stage.get("calculation")
        assert type(calculation) is dict
        selected = (
            calculation.get("selected_contract_id")
            if carrier_id == "LC0"
            else calculation.get("selected_contract_ids")
        )
        delta = (
            calculation.get("selected_delta_ppm")
            if carrier_id == "LC0"
            else [calculation.get("long_delta_ppm"), calculation.get("short_delta_ppm")]
        )
        rows.append(
            "<tr>"
            f"<th>{carrier_id}</th>"
            f"<td>{_value(_STATE_LABELS_ZH[str(stage.get('state'))])}</td>"
            f"<td>{_value(selected)}</td><td>{_value(delta)}</td>"
            f"<td>{_value(_reason_zh(stage.get('reason_code')))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>结构</th><th>独立资格结果</th><th>选中合约</th>"
        "<th>Delta ppm</th><th>直接原因</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _render_sizing_projection(stages: list[object]) -> str:
    stage = next(stage for stage in stages if type(stage) is dict and stage["stage_id"] == "SIZING")
    calculation = stage.get("calculation")
    assert type(calculation) is dict
    structures = calculation.get("structures")
    if type(structures) is not dict or not structures:
        return '<p class="section-note">本场景未进入可展示的数量计算。</p>'
    rows: list[str] = []
    for carrier_id in ("LC0", "BCS0"):
        item = structures.get(carrier_id)
        if type(item) is not dict:
            continue
        rows.append(
            "<tr>"
            f"<th>{carrier_id}</th>"
            f"<td>{_value(item.get('q_kelly'))}</td><td>{_value(item.get('q_account'))}</td>"
            f"<td>{_value(item.get('q_cash'))}</td><td>{_value(item.get('q_delta'))}</td>"
            f"<td>{_value(item.get('q_liquidity'))}</td><td>{_value(item.get('q_external'))}</td>"
            f"<td><strong>{_value(item.get('safe_quantity'))}</strong></td>"
            f"<td><strong>{_value(item.get('binding_caps'))}</strong></td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>结构</th><th>Kelly</th><th>账户</th><th>现金</th>"
        "<th>Delta</th><th>流动性</th><th>外部风险</th><th>安全数量</th><th>绑定cap</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _render_preference_projection(stages: list[object]) -> str:
    stage = next(stage for stage in stages if type(stage) is dict and stage["stage_id"] == "PREFERENCE")
    calculation = stage.get("calculation")
    assert type(calculation) is dict
    structures = calculation.get("structures")
    if type(structures) is not dict or not structures:
        return '<p class="section-note">没有正数量方案，因此本场景未进行Preference比较。</p>'
    rows: list[str] = []
    for carrier_id in ("LC0", "BCS0"):
        item = structures.get(carrier_id)
        if type(item) is not dict:
            continue
        rows.append(
            "<tr>"
            f"<th>{carrier_id}</th>"
            f"<td>{_value(item.get('expected_total_net_profit_nano_usd'))}</td>"
            f"<td>{_value(item.get('total_planned_loss_nano_usd'))}</td>"
            f"<td>{_value(item.get('current_entry_cash_usage_nano_usd'))}</td>"
            "</tr>"
        )
    return (
        f"<p class=\"section-note\">固定排序结果：<strong>{_value(calculation.get('ranking_order'))}</strong>。"
        "只有使用新的完整快照重跑后，备选才可重新成为首选。</p>"
        "<table><thead><tr><th>结构</th><th>预期总净利润 nano_usd</th>"
        "<th>计划总损失 nano_usd</th><th>当前现金占用 nano_usd</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _render_exit_projection(stages: list[object]) -> str:
    stage = next(stage for stage in stages if type(stage) is dict and stage["stage_id"] == "EXIT_POLICY_BINDING")
    calculation = stage.get("calculation")
    assert type(calculation) is dict
    if stage.get("state") != "PASS":
        return '<p class="section-note">本场景没有输出方案，因此没有形成新的退出政策绑定。</p>'
    invalidation = calculation.get("confirmed_invalidation")
    invalidation_sessions = (
        invalidation.get("complete_sessions_required") if type(invalidation) is dict else None
    )
    hard_stops = calculation.get("hard_stops")
    hard_stop_rows = ""
    if type(hard_stops) is dict:
        hard_stop_rows = "".join(
            f"<tr><th>{carrier_id}硬止损</th><td>{_value(value.get('loss_ppm') if type(value) is dict else None)} ppm · "
            f"{_value(value.get('policy_id') if type(value) is dict else None)}</td></tr>"
            for carrier_id, value in sorted(hard_stops.items())
        )
    return f"""
<table><tbody>
  <tr><th>持仓管理政策</th><td>{_value(calculation.get('management_policy_id'))}</td></tr>
  <tr><th>H20退出时点</th><td>{_value(calculation.get('h20_date'))} / {_value(calculation.get('h20_exit_utc_ns'))} UTC ns</td></tr>
  <tr><th>到期安全</th><td>提前 {_value(calculation.get('expiry_safety_calendar_days'))} 个日历日；独立override={_value(calculation.get('expiry_safety_override'))}</td></tr>
  <tr><th>趋势失效确认</th><td>{_value(invalidation_sessions)} 个完整session；latch={_value(invalidation.get('latch') if type(invalidation) is dict else None)}</td></tr>
  <tr><th>账户风险override</th><td>{_value(calculation.get('account_risk_override'))}</td></tr>
  {hard_stop_rows}
</tbody></table>
"""


def render_final_decision_card(document: object) -> str:
    """Render one Final Decision Card from the sealed result only."""

    result = _as_result_dict(document)
    status = str(result["decision_status"])
    plans_container = result["plans"]
    assert type(plans_container) is dict
    plans = tuple(
        plan
        for plan in (
            plans_container.get("preferred"),
            plans_container.get("backup"),
        )
        if type(plan) is dict
    )
    expected_plan_count = {
        "NO_DECISION": 0,
        "NO_ACTION": 0,
        "SINGLE_PLAN": 1,
        "PREFERRED_AND_BACKUP": 2,
    }[status]
    if len(plans) != expected_plan_count:
        raise EntryDecisionF0Error("DECISION_CARD_PLAN_STATUS_CONFLICT")
    trace = result["decision_trace"]
    assert type(trace) is dict
    stages = trace["stages"]
    assert type(stages) is list
    plan_sections = "".join(
        _render_plan(index, plan)
        for index, plan in enumerate(plans)
        if type(plan) is dict
    )
    if not plan_sections:
        plan_sections = '<p class="boundary">本场景没有可输出的合格方案；程序未猜测或补造方案。</p>'
    stage_cards = _render_stage_cards(stages)
    structure_comparison = _render_structure_comparison(stages)
    sizing_projection = _render_sizing_projection(stages)
    preference_projection = _render_preference_projection(stages)
    exit_projection = _render_exit_projection(stages)
    body = f"""
<h1>Trading Platform｜GLD 建仓决策 Final Decision Card</h1>
<p class="subtitle">R1 本地研究验证版 · 相同canonical输入产生相同输出</p>
<p class="boundary">RESEARCH_ONLY · NO_DECISION_EFFECT · actionable=false ·
broker_order_count=0 · 不连接券商，不构成交易指令或收益证明。</p>
<div class="grid">
  <div class="metric"><span>最终状态</span><strong>{_value(_STATUS_ZH[status])}</strong></div>
  <div class="metric"><span>为什么</span><strong>{_value(_reason_zh(result['reason_code']))}</strong></div>
  <div class="metric"><span>合格方案数量</span><strong>{len(plans)}</strong></div>
  <div class="metric"><span>Owner下一步</span><strong>阅读八步过程并人工验收</strong></div>
</div>
<h2>最终方案</h2>
{plan_sections}
<h2>LC0 / BCS0 独立资格比较</h2>
{structure_comparison}
<h2>六个数量硬cap与实际绑定cap</h2>
<p class="section-note">程序直接采用canonical trace中已经形成的六个cap与安全数量；卡片不重新计算数量。</p>
{sizing_projection}
<h2>Preference比较</h2>
{preference_projection}
<h2>退出与持仓管理绑定</h2>
{exit_projection}
<h2>八步判断过程</h2>
<p class="section-note">主视图先解释业务含义；原始计算、规则ID、reason/recovery code和hash保留在每张卡片的工程证据中。</p>
{stage_cards}
<h2>确定性与权限边界</h2>
<p class="boundary">这张卡只是sealed canonical DecisionResult的静态投影。它不重新筛选、不重新计算数量、不重新排序，也不能连接券商。</p>
<details><summary>展开完整结果lineage与工程代码</summary>
  <table class="details-table"><tbody>
    <tr><th>decision status / reason</th><td><code>{_value(status)}</code> / <code>{_value(result['reason_code'])}</code></td></tr>
    <tr><th>Trace hash</th><td><code>{_value(trace['trace_sha256'])}</code></td></tr>
    <tr><th>Result hash</th><td><code>{_value(result['result_sha256'])}</code></td></tr>
    <tr><th>Input hash</th><td><code>{_value(result['input_sha256'])}</code></td></tr>
    <tr><th>Entry policy</th><td><code>{_value(result['policy_id'])}</code> / <code>{_value(result['policy_sha256'])}</code></td></tr>
    <tr><th>权限</th><td>{_value(result['classification'])} / {_value(result['authority_status'])}</td></tr>
    <tr><th>执行能力</th><td>actionable={_value(result['actionable'])}; broker_order_count={_value(result['broker_order_count'])}</td></tr>
  </tbody></table>
</details>
"""
    return _page("Trading Platform｜GLD 建仓决策 Final Decision Card", body)


def render_owner_acceptance_index(rows: list[dict[str, object]]) -> str:
    """Render the frozen ten-question Owner acceptance homepage."""

    if type(rows) is not list or not rows:
        raise EntryDecisionF0Error("OWNER_ACCEPTANCE_ROW_SET_INVALID")
    rows_by_id: dict[str, dict[str, object]] = {}
    for row in rows:
        if type(row) is not dict:
            raise EntryDecisionF0Error("OWNER_ACCEPTANCE_ROW_INVALID")
        scenario_id = row.get("scenario_id")
        if type(scenario_id) is not str or scenario_id in rows_by_id:
            raise EntryDecisionF0Error("OWNER_ACCEPTANCE_ROW_INVALID")
        rows_by_id[scenario_id] = row
    expected_ids = frozenset(
        scenario_id
        for group in _OWNER_ACCEPTANCE_GROUPS
        for scenario_id in group["scenario_ids"]
    )
    if frozenset(rows_by_id) != expected_ids:
        raise EntryDecisionF0Error("OWNER_ACCEPTANCE_ROW_SET_INVALID")
    cards: list[str] = []
    for ordinal, group in enumerate(_OWNER_ACCEPTANCE_GROUPS, start=1):
        group_rows = tuple(rows_by_id[value] for value in group["scenario_ids"])
        passed = all(
            row.get("automated_evidence_status") == "AUTOMATED_EVIDENCE_PASS"
            for row in group_rows
        )
        status_class = "pass" if passed else "fail"
        status_text = "✓ 程序实际输出与预期一致" if passed else "✕ 程序实际输出与预期不一致"
        actual_items = "".join(
            f"<li>{_value(row.get('owner_label'))}：{_value(row.get('actual_zh'))} "
            f"<a href=\"{_safe_href(row.get('card_href'))}\">查看卡片</a></li>"
            for row in group_rows
        )
        cards.append(
            '<section class="owner-card">'
            f"<h2>{ordinal}. {_value(group['title'])}</h2>"
            f"<h3>要确认什么</h3><p>{_value(group['question'])}</p>"
            f"<h3>已冻结的规则</h3><p>{_value(group['accepted_rule'])}</p>"
            f"<h3>程序实际输出</h3><ul>{actual_items}</ul>"
            f'<p class="{status_class}">{status_text}</p>'
            "</section>"
        )
    body = f"""
<h1>Trading Platform｜GLD 建仓决策 R1 人工验收</h1>
<p class="subtitle">本地 synthetic Research F0 · 不连接真实行情、账户或券商</p>
<p class="boundary">这里验收的是：相同事实是否稳定通过八步判断，最终只给出一个明确状态；两个方案都合格时，是否仍有固定首选和备选。</p>
<div class="owner-grid">{''.join(cards)}</div>
<h2>Jake人工确认</h2>
<p class="boundary">当前状态：等待Jake确认。请逐项查看；全部符合时回复“R1全部认同”。自动证据不能代替人工接受。</p>
<p><a href="engineering-evidence.html">查看完整工程证据、canonical JSON和hash</a></p>
"""
    return _page("Trading Platform｜GLD 建仓决策 R1 人工验收", body)


def render_engineering_evidence(rows: list[dict[str, object]]) -> str:
    """Render the complete scenario/hash evidence matrix."""

    if type(rows) is not list or not rows:
        raise EntryDecisionF0Error("ENGINEERING_EVIDENCE_ROW_SET_INVALID")
    rendered: list[str] = []
    for row in rows:
        if type(row) is not dict:
            raise EntryDecisionF0Error("ENGINEERING_EVIDENCE_ROW_INVALID")
        rendered.append(
            "<tr>"
            f"<td><code>{_value(row.get('scenario_id'))}</code><br>{_value(row.get('purpose'))}</td>"
            f"<td>{_value(row.get('key_input'))}</td>"
            f"<td>{_value(row.get('expected'))}</td>"
            f"<td>{_value(row.get('actual'))}</td>"
            f"<td>{_value(row.get('automated_evidence_status'))}</td>"
            f'<td class="pending">{_value(row.get("owner_status"))}</td>'
            f"<td><a href=\"{_safe_href(row.get('card_href'))}\">Decision Card</a> · "
            f"<a href=\"{_safe_href(row.get('result_href'))}\">DecisionResult</a> · "
            f"<a href=\"{_safe_href(row.get('input_href'))}\">Raw input</a><br>"
            f"input <code>{_value(row.get('input_sha256'))}</code><br>"
            f"trace <code>{_value(row.get('trace_sha256'))}</code><br>"
            f"evidence <code>{_value(row.get('evidence_bundle_sha256'))}</code> "
            f"<a href=\"{_safe_href(row.get('evidence_bundle_href'))}\">Evidence V2</a><br>"
            f"result <code>{_value(row.get('result_sha256'))}</code></td>"
            "</tr>"
        )
    body = f"""
<h1>Trading Platform｜GLD 建仓决策 R1 工程证据</h1>
<p class="boundary">RESEARCH_ONLY · NO_DECISION_EFFECT · actionable=false · broker_order_count=0 · OWNER_ACCEPTANCE_PENDING</p>
<p><a href="index.html">返回人工验收首页</a></p>
<table><thead><tr><th>场景</th><th>关键输入</th><th>预期</th><th>实际</th><th>自动证据</th><th>Owner状态</th><th>文件与hash</th></tr></thead>
<tbody>{''.join(rendered)}</tbody></table>
<h2>工程验收 Checklist</h2>
<ul>
  <li>缺输入或政策缺失时fail closed为NO_DECISION。</li>
  <li>Gate失败或风险容量为零时不输出可执行方案。</li>
  <li>只有LC0或BCS0合格时，只输出对应一个方案。</li>
  <li>两者都合格时输出固定首选和备选；精确平局使用固定tie-break。</li>
  <li>每个DecisionResult包含顺序固定的八个trace stages及稳定hash。</li>
  <li>相同输入重复运行、Call/economics列表顺序变化及跨进程运行后，canonical JSON、card和hash逐字节一致。</li>
  <li>HTML不重新计算结果，不含脚本、表单、券商连接或交易控件。</li>
  <li>本结果不是策略盈利证明、真实数据资格或交易授权。</li>
</ul>
"""
    return _page("Trading Platform｜GLD 建仓决策 R1 工程证据", body)


__all__ = [
    "render_engineering_evidence",
    "render_final_decision_card",
    "render_owner_acceptance_index",
]
