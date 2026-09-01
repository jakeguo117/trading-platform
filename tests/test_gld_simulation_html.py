from __future__ import annotations

from collections import OrderedDict
import unittest

from gld_simulation.render_html import render_decision_card


DECISION_SHA256 = "d" * 64


def candidate(
    *,
    carrier_id: str,
    quantity: int,
    net_delta_ppm_per_strategy_unit: int,
    max_loss_nano_usd: int,
    total_entry_cost_nano_usd: int,
    legs: list[dict[str, object]],
) -> dict[str, object]:
    is_bcs = carrier_id == "BCS0"
    per_unit_cost = total_entry_cost_nano_usd // quantity
    return {
        "carrier_id": carrier_id,
        "status": "PASS",
        "reason_code": "SIMULATED_CANDIDATE_ELIGIBLE",
        "quantity": quantity,
        "expiry_date": "2026-10-16",
        "profit_cap_mode": (
            "CAPPED_AT_SHORT_STRIKE" if is_bcs else "UNBOUNDED_BY_STRUCTURE"
        ),
        "net_delta_ppm_per_strategy_unit": net_delta_ppm_per_strategy_unit,
        "max_loss_nano_usd": max_loss_nano_usd,
        "total_entry_cost_nano_usd": total_entry_cost_nano_usd,
        "base_entry_cost_nano_usd_per_contract": per_unit_cost,
        "modeled_expiry_max_profit_nano_usd_per_strategy_unit": (
            1_375_400_000_000 if is_bcs else None
        ),
        "profit_model_scope": (
            "THEORETICAL_EXPIRY_UNDER_SIZING_ASSUMPTIONS"
            if is_bcs
            else "NOT_APPLICABLE_UNBOUNDED_STRUCTURE"
        ),
        "underlying_mid_nano_usd": 327_500_000_000,
        "moneyness": "OTM",
        "selector_policy": {
            "coarse_fine_agreement_required": True,
            "second_best_fallback_allowed": False,
            "target_delta_ppm": 500_000 if carrier_id == "LC0" else 250_000,
        },
        "long_coarse_delta_ppm": 501_111,
        "long_fine_delta_ppm": 500_999,
        "short_coarse_delta_ppm": 249_111 if is_bcs else None,
        "short_fine_delta_ppm": 249_222 if is_bcs else None,
        "h20_date": "2026-09-16",
        "minimum_expiry_date": "2026-10-16",
        "legs": legs,
        "capacities": {
            "cash": 8,
            "delta": 5,
            "kelly": 3,
            "liquidity": 7,
            "max_loss": 4,
            "planned_loss": 4,
        },
    }


def pass_document() -> dict[str, object]:
    return {
        "schema_version": "GLD_SIMULATION_DECISION_RESULT_V2",
        "decision_scope": "ENTRY_SIMULATION_ONLY",
        "classification": "SIMULATION_ONLY",
        "status": "SIMULATED_OWNER_SELECTION_REQUIRED",
        "reason_code": "BOTH_CARRIERS_PASS_OWNER_SELECTION_REQUIRED",
        "actionable": False,
        "broker_order_count": 0,
        "owner_selection_required": True,
        "manual_control": "SIMULATION_SELECTION_IN_CODEX_ONLY",
        "bundle_id": "gld-synthetic-20260828-a-v1",
        "manifest_sha256": "e" * 64,
        "trading_date": "2026-08-28",
        "cutoff_utc_ns": 1_787_928_300_000_000_000,
        "rule_package_version": "SIM_GLD_PIPELINE_V1",
        "rule_sha256": "a" * 64,
        "raw_content_sha256": "b" * 64,
        "gate_a": {
            "state": "PASS",
            "reason_code": "GATE_A_PASS",
            "metrics": {
                "breakout_nano_usd": 318_750_000_000,
                "minute_1044_close_nano_usd": 319_100_000_000,
                "current_sma50_nano_usd": 316_500_000_000,
                "current_sma200_nano_usd": 300_000_000_000,
                "sma50_20_sessions_ago_nano_usd": 312_000_000_000,
                "prior_close_nano_usd": 317_000_000_000,
                "range_hold_above_count": 13,
                "range_hold_required_count": 10,
            },
            "predicates": {
                "sma50_gt_sma200": True,
                "sma50_rising_20_sessions": True,
                "prior_close_gt_sma50": True,
                "minute_1044_close_gt_breakout": True,
                "range_hold_count_met": True,
            },
        },
        "crr": {
            "status": "EXACT64_PASS",
            "requested": 64,
            "bound": 64,
            "started": 64,
            "terminal": 64,
            "failure_count": 0,
            "first_failure_ordinal": None,
            "worker_count": 4,
            "kernel_threads": 1,
            "cache_hits": 0,
            "early_exercise_terminal_count": 17,
            "model_id": "GLD_CALL_DELTA_CRR_AM_V1",
            "model_sha256": "f" * 64,
            "numerical_semantics_trust_boundary": "NATIVE_ONLY",
            "native_build_backend_evidence_sha256": "2" * 64,
            "combined_backend_evidence_sha256": "3" * 64,
            "input_vector_sha256": "4" * 64,
            "execution_provenance_sha256": "5" * 64,
            "semantic_output_sha256": "1" * 64,
            "semantic_terminals": [
                {"ordinal": ordinal, "contract_id": f"GLD-{ordinal:02d}"}
                for ordinal in range(1, 65)
            ],
        },
        "risk_context": {
            "eligible_bankroll_nano_usd": 100_000_000_000_000,
            "half_kelly_ppm": 125_000,
            "drawdown_multiplier_ppm": 800_000,
            "minimum_settled_cash_ppm": 100_000,
        },
        "candidates": [
            candidate(
                carrier_id="LC0",
                quantity=3,
                net_delta_ppm_per_strategy_unit=499_107,
                max_loss_nano_usd=2_550_000_000_000,
                total_entry_cost_nano_usd=2_550_000_000_000,
                legs=[
                    {
                        "side": "BUY_TO_OPEN",
                        "ratio": 1,
                        "contract_id": "GLD   261016C00320000",
                        "expiry_date": "2026-10-16",
                        "strike_nano_usd": 320_000_000_000,
                        "moneyness": "OTM",
                        "bid_nano_usd": 8_400_000_000,
                        "ask_nano_usd": 8_500_000_000,
                    }
                ],
            ),
            candidate(
                carrier_id="BCS0",
                quantity=4,
                net_delta_ppm_per_strategy_unit=253_219,
                max_loss_nano_usd=2_040_000_000_000,
                total_entry_cost_nano_usd=2_040_000_000_000,
                legs=[
                    {
                        "side": "BUY_TO_OPEN",
                        "ratio": 1,
                        "contract_id": "GLD   261016C00320000",
                        "expiry_date": "2026-10-16",
                        "strike_nano_usd": 320_000_000_000,
                        "moneyness": "OTM",
                        "bid_nano_usd": 8_400_000_000,
                        "ask_nano_usd": 8_500_000_000,
                    },
                    {
                        "side": "SELL_TO_OPEN",
                        "ratio": 1,
                        "contract_id": "GLD   261016C00335000",
                        "expiry_date": "2026-10-16",
                        "strike_nano_usd": 335_000_000_000,
                        "moneyness": "OTM",
                        "bid_nano_usd": 3_300_000_000,
                        "ask_nano_usd": 3_400_000_000,
                    },
                ],
            ),
        ],
        "exit_plan": {
            "fixed_take_profit": False,
            "gate_a_invalidation": (
                "EXIT_REVIEW_REQUIRED_IF_SIM_A1_RANGE_HOLD_V1_FAILS"
            ),
            "expiry_safety": "FULL_EXIT_REQUIRED_BEFORE_CONTRACT_SAFETY_BOUNDARY",
            "latest_exit_horizon": "H20",
            "latest_full_exit_date": "2026-09-16",
            "lc1_roll_up": "NOT_ACTIVE_IN_SIM_V1",
        },
        "simulation_limitations": [
            "SYNTHETIC_INPUTS_ONLY",
            "NOT_PROMOTABLE_TO_LIVE_DECISION",
            "NO_BROKER_CONNECTION",
            "NO_ORDER_CREATION_OR_SUBMISSION",
        ],
    }


class DecisionCardHtmlTests(unittest.TestCase):
    def test_pass_card_is_a_human_readable_simulation_order_preview(self) -> None:
        document = pass_document()
        document["backend_field_added_later"] = "DO_NOT_AUTO_DUMP"
        candidates = document["candidates"]
        self.assertIsInstance(candidates, list)
        self.assertIsInstance(candidates[0], dict)
        candidates[0]["backend_candidate_field_added_later"] = "DO_NOT_AUTO_DUMP"

        first = render_decision_card(document, decision_result_sha256=DECISION_SHA256)
        second = render_decision_card(document, decision_result_sha256=DECISION_SHA256)

        self.assertIsInstance(first, bytes)
        self.assertEqual(first, second)
        html = first.decode("utf-8")
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("<title>GLD 模拟决策卡：请选择模拟方案</title>", html)
        self.assertIn("仅模拟", html)
        self.assertIn("合成数据", html)
        self.assertIn("禁止真实下单", html)
        self.assertIn("两个模拟方案均通过，请由 Jake 选择", html)
        self.assertIn("数据时点（纽约）：2026-08-28 10:45 ET", html)
        self.assertIn("A · Long Call", html)
        self.assertIn("B · Bull Call Spread", html)
        self.assertIn("模拟单腿 Call", html)
        self.assertIn("原生 1:1 组合", html)
        self.assertIn("BCS 必须作为一个原生 1:1 组合执行，不可逐腿执行", html)
        self.assertIn("3 张", html)
        self.assertIn("4 组", html)
        self.assertIn("GLD   261016C00320000", html)
        self.assertIn("GLD   261016C00335000", html)
        self.assertIn("买入开仓", html)
        self.assertIn("卖出开仓", html)
        self.assertIn("2026-10-16", html)
        self.assertIn("$320.00", html)
        self.assertIn("$335.00", html)
        self.assertIn("价外（OTM）", html)
        self.assertIn("$8.40 / $8.50", html)
        self.assertIn("$3.30 / $3.40", html)
        self.assertIn("$850.00", html)
        self.assertIn("$510.00", html)
        self.assertIn("$2,550.00", html)
        self.assertIn("$2,040.00", html)
        self.assertIn("结构上不封顶", html)
        self.assertIn("在卖出行权价处封顶", html)
        self.assertIn("bid / ask 仅用于本次模拟成本计算，不是订单限价", html)
        self.assertIn(
            "预计成本包含规则包中的模拟入场费用，因此不等于只用显示的 bid / ask × 100 简单计算",
            html,
        )
        self.assertIn("合格实时报价", html)
        self.assertIn("限价规则", html)
        self.assertIn("订单有效期（TIF）", html)
        self.assertIn("卡片有效期", html)
        self.assertIn("请在 Codex 对话中回复 A、B 或不交易", html)
        self.assertEqual(
            html.count("这只记录模拟选择，不创建、预填或提交任何券商订单。"),
            1,
        )
        self.assertIn("50 日均线 $316.50；200 日均线 $300.00；要求前者更高：通过", html)
        self.assertIn("当前 $316.50；20 个交易日前 $312.00；要求前者更高：通过", html)
        self.assertIn("前收盘价 $317.00；50 日均线 $316.50；要求前者更高：通过", html)
        self.assertIn("10:44 收盘价 $319.10；突破价 $318.75；要求前者更高：通过", html)
        self.assertIn("突破后保持分钟数：13 / 10，通过", html)
        self.assertIn("不设置固定止盈", html)
        self.assertIn("最晚完整退出日：2026-09-16（H20）", html)
        self.assertIn('<details class="audit-details">', html)
        self.assertNotIn('<details class="audit-details" open', html)
        self.assertIn(DECISION_SHA256, html)
        self.assertIn("EXACT64_PASS", html)
        self.assertIn("selector_policy", html)
        self.assertIn("early_exercise_terminal_count", html)
        self.assertIn("combined_backend_evidence_sha256", html)
        self.assertIn("numerical_semantics_trust_boundary", html)
        self.assertIn('http-equiv="Content-Security-Policy"', html)
        self.assertIn("default-src 'none'", html)

        main_layer = html.split('<details class="audit-details">', maxsplit=1)[0]
        for hidden_machine_fact in (
            DECISION_SHA256,
            "EXACT64_PASS",
            "selector_policy",
            "net_delta_ppm_per_strategy_unit",
            "max_loss_nano_usd",
            "2550000000000",
            "DO_NOT_AUTO_DUMP",
        ):
            with self.subTest(hidden_machine_fact=hidden_machine_fact):
                self.assertNotIn(hidden_machine_fact, main_layer)
        self.assertNotIn("DO_NOT_AUTO_DUMP", html)
        self.assertNotIn("推荐 A", html)
        self.assertNotIn("推荐 B", html)

    def test_rendering_does_not_depend_on_mapping_insertion_order(self) -> None:
        document = pass_document()
        reversed_document = OrderedDict(reversed(tuple(document.items())))
        candidates = document["candidates"]
        self.assertIsInstance(candidates, list)
        reversed_document["candidates"] = [
            OrderedDict(reversed(tuple(item.items())))
            for item in candidates
            if isinstance(item, dict)
        ]

        regular = render_decision_card(document, decision_result_sha256=DECISION_SHA256)
        reordered = render_decision_card(
            reversed_document,
            decision_result_sha256=DECISION_SHA256,
        )

        self.assertEqual(regular, reordered)

    def test_all_external_text_is_escaped_and_card_has_no_execution_controls(self) -> None:
        document = pass_document()
        document["reason_code"] = '<img src=x onerror="alert(1)">'
        document["broker_payload"] = "DO_NOT_RENDER_BROKER_PAYLOAD"
        candidates = document["candidates"]
        self.assertIsInstance(candidates, list)
        first_candidate = candidates[0]
        self.assertIsInstance(first_candidate, dict)
        first_candidate["economics"] = {
            "broker_payload": "DO_NOT_RENDER_NESTED_ECONOMICS"
        }
        first_candidate["capacities"] = {
            "cash": 8,
            "broker_payload": "DO_NOT_RENDER_NESTED_CAPACITY",
        }
        selector_policy = first_candidate["selector_policy"]
        self.assertIsInstance(selector_policy, dict)
        selector_policy["lc0"] = {
            "selector_id": "SIM_LC0_050_V1",
            "broker_payload": "DO_NOT_RENDER_NESTED_SELECTOR",
        }
        risk_context = document["risk_context"]
        self.assertIsInstance(risk_context, dict)
        risk_context["lc0_sizing"] = {
            "state": "PASS",
            "reason_code": "SIZING_PASS",
            "broker_payload": "DO_NOT_RENDER_NESTED_SIZING",
        }
        legs = first_candidate["legs"]
        self.assertIsInstance(legs, list)
        self.assertIsInstance(legs[0], dict)
        legs[0]["contract_id"] = "LC0<script>alert(1)</script>"

        html = render_decision_card(
            document,
            decision_result_sha256=DECISION_SHA256,
        ).decode("utf-8")

        self.assertIn("&lt;img src=x onerror=", html)
        self.assertIn("alert(1)", html)
        self.assertIn("LC0&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("DO_NOT_RENDER_BROKER_PAYLOAD", html)
        for nested_marker in (
            "DO_NOT_RENDER_NESTED_ECONOMICS",
            "DO_NOT_RENDER_NESTED_CAPACITY",
            "DO_NOT_RENDER_NESTED_SELECTOR",
            "DO_NOT_RENDER_NESTED_SIZING",
        ):
            with self.subTest(nested_marker=nested_marker):
                self.assertNotIn(nested_marker, html)
        for forbidden in (
            "<script",
            "<form",
            "<button",
            "<input",
            "<select",
            "<textarea",
            "javascript:",
            'onclick="',
            'onerror="',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, html.lower())

    def test_no_action_card_only_shows_failed_gate_and_recovery(self) -> None:
        document = pass_document()
        document.update(
            {
                "status": "SIMULATED_NO_ACTION",
                "reason_code": "GATE_A_COMPLETE_NOT_PASSED",
                "owner_selection_required": False,
                "manual_control": "NO_ACTION_NO_ORDER_AUTHORITY",
                "candidates": [],
                "gate_a": {
                    "state": "FAIL",
                    "reason_code": "GATE_A_1044_CLOSE_NOT_ABOVE_BREAKOUT",
                    "metrics": {
                        "breakout_nano_usd": 318_750_000_000,
                        "minute_1044_close_nano_usd": 318_500_000_000,
                        "range_hold_above_count": 6,
                        "range_hold_required_count": 10,
                    },
                    "predicates": {
                        "sma50_gt_sma200": True,
                        "sma50_rising_20_sessions": True,
                        "prior_close_gt_sma50": True,
                        "minute_1044_close_gt_breakout": False,
                        "range_hold_count_met": False,
                    },
                },
            }
        )

        html = render_decision_card(
            document,
            decision_result_sha256=DECISION_SHA256,
        ).decode("utf-8")

        self.assertIn("<title>GLD 模拟决策卡：今天不建仓</title>", html)
        self.assertIn("<h1>今天不建仓</h1>", html)
        self.assertIn("10:44 收盘价 $318.50；突破价 $318.75；要求前者更高：未通过", html)
        self.assertIn("突破后保持分钟数：6 / 10，未通过", html)
        self.assertIn("等待新的完整快照后重新运行", html)
        self.assertNotIn("A · Long Call", html)
        self.assertNotIn("B · Bull Call Spread", html)
        self.assertNotIn("共同退出纪律", html)
        self.assertNotIn("请在 Codex 对话中回复", html)

    def test_no_decision_card_names_each_missing_risk_fact_and_recovery(self) -> None:
        document = pass_document()
        document.update(
            {
                "status": "SIMULATED_NO_DECISION",
                "reason_code": "RISK_FACT_MISSING",
                "owner_selection_required": False,
                "manual_control": "NO_DECISION_NO_ORDER_AUTHORITY",
                "candidates": [],
                "risk_context": {
                    "status": "NO_DECISION",
                    "reason_code": "RISK_FACT_MISSING",
                    "missing_fields": [
                        "account_snapshot_a.settled_cash_nano_usd",
                        "account_snapshot_a.net_liquidation_value_nano_usd",
                        "frozen_kelly_receipt.robust_full_kelly_ppm",
                    ],
                },
            }
        )

        html = render_decision_card(
            document,
            decision_result_sha256=DECISION_SHA256,
        ).decode("utf-8")

        self.assertIn("<title>GLD 模拟决策卡：禁止建仓</title>", html)
        self.assertIn("<h1>禁止建仓：暂时无法判断</h1>", html)
        self.assertIn("当前阻断原因", html)
        self.assertIn("缺少已结算现金（settled cash）", html)
        self.assertIn("缺少账户净清算价值", html)
        self.assertIn("缺少冻结的稳健 Full-Kelly 比例", html)
        self.assertIn("补齐缺失事实后重新运行", html)
        self.assertNotIn("A · Long Call", html)
        self.assertNotIn("B · Bull Call Spread", html)
        self.assertNotIn("共同退出纪律", html)
        self.assertNotIn("请在 Codex 对话中回复", html)

    def test_risk_no_action_does_not_falsely_claim_gate_a_failed(self) -> None:
        document = pass_document()
        document.update(
            {
                "status": "SIMULATED_NO_ACTION",
                "reason_code": "RISK_STICKY_DRAWDOWN_LOCK_ACTIVE",
                "owner_selection_required": False,
                "manual_control": "NO_ACTION_NO_ORDER_AUTHORITY",
                "candidates": [],
                "risk_context": {
                    "status": "BLOCKED",
                    "lc0_sizing": {
                        "state": "NO_ACTION",
                        "reason_code": "SIZING_STICKY_DRAWDOWN_LOCK_ACTIVE",
                    },
                    "bcs_sizing": {
                        "state": "NO_ACTION",
                        "reason_code": "SIZING_COMPLETE_ZERO_QUANTITY",
                    },
                },
            }
        )

        html = render_decision_card(
            document,
            decision_result_sha256=DECISION_SHA256,
        ).decode("utf-8")

        self.assertIn("风险边界阻止新建仓", html)
        self.assertIn("粘性回撤锁已激活", html)
        self.assertIn("风险状态更新后，用新的完整快照重新运行", html)
        self.assertNotIn("Gate A 没有完整通过", html)
        self.assertNotIn("未通过的 Gate A 条件", html)

    def test_risk_no_action_names_each_binding_constraint(self) -> None:
        cases = (
            ("STICKY_DRAWDOWN_LOCK", "粘性回撤锁已激活"),
            ("KELLY_DRAWDOWN", "Half-Kelly 与回撤系数给出的风险预算不足"),
            ("MAX_LOSS", "单笔模型最大亏损预算不足"),
            ("PLANNED_LOSS", "计划亏损预算不足"),
            ("DELTA_NOTIONAL", "GLD 等效 Delta 名义敞口容量不足"),
            ("SETTLED_CASH", "已结算现金及保留现金要求不足"),
            ("LIQUIDITY", "模拟报价可成交数量不足"),
        )
        for binding_constraint, expected_label in cases:
            with self.subTest(binding_constraint=binding_constraint):
                document = pass_document()
                document.update(
                    {
                        "status": "SIMULATED_NO_ACTION",
                        "reason_code": "RISK_CAPACITY_BLOCKS_ENTRY",
                        "owner_selection_required": False,
                        "manual_control": "NO_ACTION_NO_ORDER_AUTHORITY",
                        "candidates": [],
                        "risk_context": {
                            "status": "BLOCKED",
                            "lc0_sizing": {
                                "state": "NO_ACTION",
                                "reason_code": "SIZING_COMPLETE_ZERO_QUANTITY",
                                "binding_constraints": [binding_constraint],
                            },
                            "bcs_sizing": {
                                "state": "NO_ACTION",
                                "reason_code": "SIZING_COMPLETE_ZERO_QUANTITY",
                                "binding_constraints": [binding_constraint],
                            },
                        },
                    }
                )

                html = render_decision_card(
                    document,
                    decision_result_sha256=DECISION_SHA256,
                ).decode("utf-8")

                self.assertIn(expected_label, html)
                self.assertNotIn("Gate A 没有完整通过", html)
                self.assertNotIn("A · Long Call", html)

    def test_no_decision_card_names_each_canonical_blocker_family(self) -> None:
        cases: tuple[
            tuple[str, dict[str, object], str, str],
            ...,
        ] = (
            (
                "GATE_A_NOT_EVALUABLE",
                {
                    "gate_a": {
                        "state": "NOT_EVALUABLE",
                        "reason_code": "SIM_MINUTE_CAUSALITY_INVALID",
                        "predicates": {},
                        "metrics": {},
                    }
                },
                "Gate A 所需的行情或日历事实无法验证",
                "修复 Gate A 输入并重新运行",
            ),
            (
                "OPTION_DATA_INCOMPLETE",
                {},
                "期权链或报价快照不完整",
                "取得新的完整期权快照后重新运行",
            ),
            (
                "CRR_MODEL_FAILURE",
                {
                    "crr": {
                        "status": "EXACT64_FAIL_CLOSED",
                        "failure_count": 1,
                        "first_failure_ordinal": 7,
                    }
                },
                "CRR 候选计算未全部通过",
                "修复 CRR 输入或计算失败后重新运行",
            ),
            (
                "RISK_OR_QUANTITY_NOT_EVALUABLE",
                {
                    "risk_context": {
                        "status": "BLOCKED",
                        "lc0_sizing": {
                            "state": "NO_DECISION",
                            "reason_code": "SIZING_FACT_CONFLICT",
                        },
                    }
                },
                "风险或数量事实互相冲突",
                "修复风险或数量事实后重新运行",
            ),
            (
                "SUPERVISOR_TIMEOUT",
                {
                    "gate_a": {
                        "state": "NOT_EVALUABLE",
                        "reason_code": "SUPERVISOR_TIMEOUT",
                        "predicates": {},
                        "metrics": {},
                    },
                    "crr": {"status": "NOT_RUN_PIPELINE_FAILED_CLOSED"},
                    "risk_context": {"status": "NOT_RUN_PIPELINE_FAILED_CLOSED"},
                },
                "流程在安全边界内停止",
                "解决上述阻断原因后重新运行",
            ),
        )
        for reason_code, overrides, expected_blocker, expected_recovery in cases:
            with self.subTest(reason_code=reason_code):
                document = pass_document()
                document.update(
                    {
                        "status": "SIMULATED_NO_DECISION",
                        "reason_code": reason_code,
                        "owner_selection_required": False,
                        "manual_control": "NO_DECISION_NO_ORDER_AUTHORITY",
                        "candidates": [],
                        **overrides,
                    }
                )

                html = render_decision_card(
                    document,
                    decision_result_sha256=DECISION_SHA256,
                ).decode("utf-8")

                self.assertIn(expected_blocker, html)
                self.assertIn(expected_recovery, html)
                self.assertNotIn("A · Long Call", html)
                self.assertNotIn("共同退出纪律", html)

    def test_no_decision_names_each_sizing_failure_fact(self) -> None:
        cases = (
            (
                "SIZING_PLANNED_LOSS_MUST_EQUAL_MAX_LOSS",
                [],
                "计划亏损口径与模型最大亏损不一致",
            ),
            (
                "SIZING_STICKY_DRAWDOWN_LOCK_CONFLICT",
                [],
                "粘性回撤锁状态与回撤水平冲突",
            ),
            (
                "SIZING_FACT_MISSING",
                ["robust_full_kelly_ppm"],
                "冻结的稳健 Full-Kelly 比例",
            ),
        )
        for sizing_reason, missing_fields, expected_label in cases:
            with self.subTest(sizing_reason=sizing_reason):
                document = pass_document()
                document.update(
                    {
                        "status": "SIMULATED_NO_DECISION",
                        "reason_code": "RISK_OR_QUANTITY_NOT_EVALUABLE",
                        "owner_selection_required": False,
                        "manual_control": "NO_DECISION_NO_ORDER_AUTHORITY",
                        "candidates": [],
                        "risk_context": {
                            "status": "BLOCKED",
                            "lc0_sizing": {
                                "state": "NO_DECISION",
                                "reason_code": sizing_reason,
                                "missing_fields": missing_fields,
                            },
                        },
                    }
                )

                html = render_decision_card(
                    document,
                    decision_result_sha256=DECISION_SHA256,
                ).decode("utf-8")

                self.assertIn(expected_label, html)
                self.assertIn("修复风险或数量事实后重新运行", html)
                self.assertNotIn("A · Long Call", html)

    def test_html_has_accessible_structure_and_responsive_reflow(self) -> None:
        html = render_decision_card(
            pass_document(),
            decision_result_sha256=DECISION_SHA256,
        ).decode("utf-8")

        self.assertEqual(html.count("<h1>"), 1)
        self.assertIn('<main id="main-content">', html)
        self.assertIn('<section aria-labelledby="comparison-title"', html)
        self.assertIn('<caption>方案 A 模拟订单腿</caption>', html)
        self.assertIn('<caption>方案 B 模拟订单腿</caption>', html)
        self.assertIn('<th scope="col">方向</th>', html)
        self.assertIn(
            'role="region" tabindex="0" aria-label="方案 A 模拟订单腿，可横向滚动"',
            html,
        )
        self.assertIn(
            '<pre role="region" tabindex="0" aria-label="审计详情，可滚动">',
            html,
        )
        self.assertIn(".audit-details pre:focus-visible", html)
        self.assertIn(".audit-details summary:focus-visible", html)
        self.assertIn('<th scope="row">预计总成本</th>', html)
        self.assertIn("font-size: 16px", html)
        self.assertIn("overflow-wrap: anywhere", html)
        self.assertIn("@media (max-width: 768px)", html)
        self.assertIn("grid-template-columns: 1fr", html)


if __name__ == "__main__":
    unittest.main()
