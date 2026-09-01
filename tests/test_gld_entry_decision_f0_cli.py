from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stderr
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gld_entry_decision_f0.canonical import (
    canonical_json_bytes,
    canonical_json_sha256,
)
from gld_entry_decision_f0.errors import EntryDecisionF0Error
from gld_entry_decision_f0 import evaluate_entry_decision_f0
from gld_entry_decision_f0.examples import (
    ACCEPTANCE_SCENARIOS_F0,
    build_synthetic_entry_decision_case_f0,
)
from gld_entry_decision_f0.publish import (
    publish_artifacts,
    read_canonical_json_file,
)
from gld_entry_decision_f0.render_html import (
    render_engineering_evidence,
    render_final_decision_card,
    render_owner_acceptance_index,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL = PROJECT_ROOT / "tools" / "run_gld_entry_decision_f0.py"


def _sealed_plan(
    carrier_id: str,
    *,
    safe_quantity: int,
    expected_total_net_profit_nano_usd: int,
) -> dict[str, object]:
    is_lc0 = carrier_id == "LC0"
    return {
        "carrier_id": carrier_id,
        "contract_ids": ["LC-LONG"] if is_lc0 else ["BCS-LONG", "BCS-SHORT"],
        "expiry_date": "2027-06-18",
        "safe_quantity": safe_quantity,
        "current_entry_debit_per_unit_nano_usd": 1_000_000_000_000,
        "stressed_entry_debit_per_unit_nano_usd": 100_000_000_000,
        "planned_loss_per_unit_nano_usd": 101_000_000_000,
        "delta_notional_per_unit_nano_usd": 10_000_000_000,
        "expected_net_return_lower_bound_ppm": 100_000,
        "expected_total_net_profit_nano_usd": expected_total_net_profit_nano_usd,
        "comparison_numerator": expected_total_net_profit_nano_usd * 1_000_000,
        "sizing_caps": {
            "q_kelly": safe_quantity + 5,
            "q_account": safe_quantity + 4,
            "q_cash": safe_quantity + 3,
            "q_delta": safe_quantity + 2,
            "q_liquidity": safe_quantity,
            "q_external": safe_quantity + 1,
        },
        "binding_caps": ["q_liquidity"],
        "management_policy_id": "GLD_MANAGEMENT_PRIMARY_F0",
        "management_policy_sha256": "a" * 64,
        "h20_date": "2027-04-16",
        "h20_exit_utc_ns": 1_807_886_700_000_000_000,
        "expiry_safety_calendar_days": 30,
        "invalidation_confirmation_sessions": 2,
        "hard_stop_policy_id": f"{carrier_id}_HARD_STOP_500K_F0",
        "hard_stop_policy_sha256": (
            "b" * 64 if is_lc0 else "c" * 64
        ),
        "hard_stop_loss_ppm": 500_000,
        "authority_status": "NO_DECISION_EFFECT",
    }


def _sealed_result(*, status: str = "PREFERRED_AND_BACKUP") -> dict[str, object]:
    stages: list[dict[str, object]] = []
    for order in range(1, 9):
        stage_body: dict[str, object] = {
            "stage_id": (
                "INPUT_QUALIFICATION",
                "ENTRY_GATE",
                "LC0_EVALUATION",
                "BCS0_EVALUATION",
                "SIZING",
                "PREFERENCE",
                "EXIT_POLICY_BINDING",
                "FINAL_DECISION",
            )[order - 1],
            "stage_order": order,
            "state": "PASS",
            "fact_references": [f"FACT.{order}"],
            "rule_id": f"ENTRY_F{order}",
            "rule_version": "1",
            "calculation": {"value_ppm": order},
            "reason_code": "STAGE_PASS",
            "recovery_code": "NONE",
        }
        stages.append(
            {**stage_body, "stage_sha256": canonical_json_sha256(stage_body)}
        )
    preferred: dict[str, object] | None = None
    backup: dict[str, object] | None = None
    if status in {"SINGLE_PLAN", "PREFERRED_AND_BACKUP"}:
        preferred = _sealed_plan(
            "LC0",
            safe_quantity=2,
            expected_total_net_profit_nano_usd=20_000_000_000,
        )
    if status == "PREFERRED_AND_BACKUP":
        backup = _sealed_plan(
            "BCS0",
            safe_quantity=1,
            expected_total_net_profit_nano_usd=10_000_000_000,
        )
    trace_body = {
        "schema_version": "ENTRY_DECISION_TRACE_F0_V1",
        "stages": stages,
    }
    result_body = {
        "schema_version": "ENTRY_DECISION_RESULT_F0_V1",
        "classification": "RESEARCH_ONLY",
        "authority_status": "NO_DECISION_EFFECT",
        "actionable": False,
        "broker_order_count": 0,
        "decision_status": status,
        "reason_code": "BOTH_CARRIERS_QUALIFIED",
        "input_sha256": "e" * 64,
        "policy_id": "GLD_ENTRY_RESEARCH_TRIAL_F0_STRICT",
        "policy_sha256": "f" * 64,
        "plans": {"preferred": preferred, "backup": backup},
        "decision_trace": {
            **trace_body,
            "trace_sha256": canonical_json_sha256(trace_body),
        },
    }
    return {
        **result_body,
        "result_sha256": canonical_json_sha256(result_body),
    }


def _evidence_row() -> dict[str, object]:
    return {
        "scenario_id": "both_qualified",
        "owner_label": "两个方案都合格",
        "key_input_zh": "LC0和BCS0都合格，LC0预期总净利润更高。",
        "expected_zh": "输出LC0首选、BCS0备选。",
        "actual_zh": "程序输出LC0首选、BCS0备选。",
        "purpose": "Both carriers qualify with strict preference",
        "key_input": "LC0 and BCS0 pass",
        "expected": "PREFERRED_AND_BACKUP LC0,BCS0",
        "actual": "PREFERRED_AND_BACKUP LC0,BCS0",
        "automated_evidence_status": "AUTOMATED_EVIDENCE_PASS",
        "owner_status": "OWNER_ACCEPTANCE_PENDING",
        "input_sha256": "1" * 64,
        "trace_sha256": "2" * 64,
        "result_sha256": "3" * 64,
        "evidence_bundle_sha256": "4" * 64,
        "evidence_bundle_href": "evidence/base/historical-structure-evidence-bundle-v2.json",
        "input_href": "both_qualified/entry-input-f0.json",
        "result_href": "both_qualified/entry-decision-f0.json",
        "card_href": "both_qualified/final-decision-card.html",
    }


class EntryDecisionHtmlTests(unittest.TestCase):
    def test_final_card_strictly_renders_sealed_result(self) -> None:
        html = render_final_decision_card(_sealed_result())
        self.assertIn("GLD 建仓决策 Final Decision Card", html)
        self.assertIn("首选方案与备选方案", html)
        self.assertIn("LC0", html)
        self.assertIn("BCS0", html)
        self.assertEqual(html.count("STAGE_PASS"), 8)
        self.assertEqual(html.count('<section class="stage-card">'), 8)
        for label in (
            "我们在确认什么",
            "关键事实摘要",
            "应用的人类规则",
            "程序结果",
            "直接原因中文",
            "失败时如何恢复中文",
            "六个数量硬cap与实际绑定cap",
            "Preference比较",
            "退出与持仓管理绑定",
            "展开工程证据",
        ):
            self.assertIn(label, html)
        self.assertIn("RESEARCH_ONLY", html)
        self.assertIn("NO_DECISION_EFFECT", html)
        self.assertIn("actionable=false", html)
        self.assertIn("broker_order_count=0", html)
        lowered = html.lower()
        self.assertNotIn("<script", lowered)
        self.assertNotIn("<form", lowered)
        self.assertNotIn("<button", lowered)
        self.assertNotIn("place order", lowered)

    def test_final_card_projects_gate_quota_from_sealed_trace(self) -> None:
        result = _sealed_result()
        gate_stage = result["decision_trace"]["stages"][1]
        gate_stage["calculation"] = {
            "trend": {"pass_count": 2, "required_count": 2},
            "breakout": {"pass_count": 1, "required_count": 1},
            "groups_must_separately_pass": True,
        }
        unsigned_stage = dict(gate_stage)
        unsigned_stage.pop("stage_sha256")
        gate_stage["stage_sha256"] = canonical_json_sha256(unsigned_stage)
        trace = result["decision_trace"]
        unsigned_trace = dict(trace)
        unsigned_trace.pop("trace_sha256")
        trace["trace_sha256"] = canonical_json_sha256(unsigned_trace)
        unsigned_result = dict(result)
        unsigned_result.pop("result_sha256")
        result["result_sha256"] = canonical_json_sha256(unsigned_result)

        html = render_final_decision_card(result)
        self.assertIn("本次封存政策要求趋势组至少2/3", html)
        self.assertIn("突破组至少1/2", html)
        self.assertNotIn("趋势组三项必须3/3", html)

    def test_final_card_explains_key_failures_in_chinese(self) -> None:
        cases: list[tuple[str, dict[str, object], str]] = []

        stale = build_synthetic_entry_decision_case_f0("both_qualified")
        stale["entry_input"]["call_universe"][0][
            "quote_event_utc_ns"
        ] -= 6_000_000_000
        cases.append(("stale", stale, "期权报价相对10:45判断时点已经过旧"))

        snapshot = build_synthetic_entry_decision_case_f0("both_qualified")
        snapshot["entry_input"]["call_universe"][0]["snapshot_sha256"] = "f" * 64
        cases.append(("snapshot", snapshot, "不属于本次完整原子快照"))

        evidence = build_synthetic_entry_decision_case_f0("both_qualified")
        evidence["entry_input"]["structure_evidence"]["LC0"] = None
        cases.append(("evidence", evidence, "没有独立历史证据"))

        fee = build_synthetic_entry_decision_case_f0("both_qualified")
        fee["entry_input"]["planned_exit_fees_nano_usd"] = {
            "LC0": None,
            "BCS0": None,
        }
        cases.append(("exit-fee", fee, "计算计划损失时缺少退出费用"))

        for label, case, expected_zh in cases:
            with self.subTest(label=label):
                result = evaluate_entry_decision_f0(
                    case["entry_input"],
                    case["entry_policy"],
                )
                html = render_final_decision_card(result.as_dict())
                self.assertIn(expected_zh, html)

    def test_final_card_rejects_unknown_or_missing_closed_schema_fields(self) -> None:
        result = _sealed_result()
        result["unknown_authority"] = "SHOULD_NOT_RENDER"
        with self.assertRaises(EntryDecisionF0Error) as caught:
            render_final_decision_card(result)
        self.assertEqual(
            caught.exception.reason_code,
            "DECISION_CARD_RESULT_SCHEMA_INVALID",
        )

        result = _sealed_result()
        result.pop("input_sha256")
        with self.assertRaises(EntryDecisionF0Error) as caught:
            render_final_decision_card(result)
        self.assertEqual(
            caught.exception.reason_code,
            "DECISION_CARD_RESULT_SCHEMA_INVALID",
        )

        result = _sealed_result()
        result["policy_sha256"] = "not-a-hash"
        unsigned = dict(result)
        unsigned.pop("result_sha256")
        result["result_sha256"] = canonical_json_sha256(unsigned)
        with self.assertRaises(EntryDecisionF0Error) as caught:
            render_final_decision_card(result)
        self.assertEqual(caught.exception.reason_code, "DECISION_CARD_LINEAGE_INVALID")

        result = _sealed_result()
        trace = result["decision_trace"]
        assert isinstance(trace, dict)
        trace["unknown"] = False
        unsigned = dict(result)
        unsigned.pop("result_sha256")
        result["result_sha256"] = canonical_json_sha256(unsigned)
        with self.assertRaises(EntryDecisionF0Error) as caught:
            render_final_decision_card(result)
        self.assertEqual(
            caught.exception.reason_code,
            "DECISION_CARD_TRACE_SCHEMA_INVALID",
        )

        result = _sealed_result()
        trace = result["decision_trace"]
        assert isinstance(trace, dict)
        stages = trace["stages"]
        assert isinstance(stages, list) and isinstance(stages[0], dict)
        stages[0]["unknown"] = False
        stage_unsigned = dict(stages[0])
        stage_unsigned.pop("stage_sha256")
        stages[0]["stage_sha256"] = canonical_json_sha256(stage_unsigned)
        trace_unsigned = dict(trace)
        trace_unsigned.pop("trace_sha256")
        trace["trace_sha256"] = canonical_json_sha256(trace_unsigned)
        result_unsigned = dict(result)
        result_unsigned.pop("result_sha256")
        result["result_sha256"] = canonical_json_sha256(result_unsigned)
        with self.assertRaises(EntryDecisionF0Error) as caught:
            render_final_decision_card(result)
        self.assertEqual(
            caught.exception.reason_code,
            "DECISION_CARD_STAGE_SCHEMA_INVALID",
        )

    def test_final_card_rejects_boundary_and_trace_mutation(self) -> None:
        for mutation, reason in (
            (("actionable", True), "DECISION_CARD_AUTHORITY_BOUNDARY_INVALID"),
            (("broker_order_count", 1), "DECISION_CARD_AUTHORITY_BOUNDARY_INVALID"),
        ):
            with self.subTest(reason=reason):
                result = _sealed_result()
                result[mutation[0]] = mutation[1]
                with self.assertRaises(EntryDecisionF0Error) as caught:
                    render_final_decision_card(result)
                self.assertEqual(caught.exception.reason_code, reason)
        result = _sealed_result()
        trace = result["decision_trace"]
        assert isinstance(trace, dict)
        stages = trace["stages"]
        assert isinstance(stages, list)
        stages.pop()
        with self.assertRaises(EntryDecisionF0Error) as caught:
            render_final_decision_card(result)
        self.assertEqual(caught.exception.reason_code, "DECISION_CARD_TRACE_SCHEMA_INVALID")

        result = _sealed_result()
        plans = result["plans"]
        assert isinstance(plans, dict) and isinstance(plans["preferred"], dict)
        plans["preferred"]["safe_quantity"] = 99
        with self.assertRaises(EntryDecisionF0Error) as caught:
            render_final_decision_card(result)
        self.assertEqual(
            caught.exception.reason_code,
            "DECISION_CARD_RESULT_HASH_MISMATCH",
        )

        result = _sealed_result(status="SINGLE_PLAN")
        plans = result["plans"]
        assert isinstance(plans, dict)
        plans["backup"] = plans["preferred"]
        plans["preferred"] = None
        unsigned = dict(result)
        unsigned.pop("result_sha256")
        result["result_sha256"] = canonical_json_sha256(unsigned)
        with self.assertRaises(EntryDecisionF0Error) as caught:
            render_final_decision_card(result)
        self.assertEqual(
            caught.exception.reason_code,
            "DECISION_CARD_PLAN_SLOT_INVALID",
        )

    def test_owner_and_engineering_pages_separate_plain_and_deep_evidence(self) -> None:
        rows = []
        for scenario_id, owner_label in (
            ("missing_input", "数据不完整"),
            ("gate_fail", "Gate失败"),
            ("lc_only", "仅LC0"),
            ("bcs_only", "仅BCS0"),
            ("both_qualified", "双合格"),
            ("exact_tie", "完全平局"),
            ("zero_cap", "容量为零"),
            ("policy_missing", "政策缺失"),
        ):
            row = _evidence_row()
            row["scenario_id"] = scenario_id
            row["owner_label"] = owner_label
            row["card_href"] = f"{scenario_id}/final-decision-card.html"
            rows.append(row)
        owner = render_owner_acceptance_index(rows)
        row = _evidence_row()
        engineering = render_engineering_evidence([row])
        self.assertIn("双合格时是否有确定首选和备选", owner)
        self.assertIn("程序输出LC0首选、BCS0备选", owner)
        self.assertEqual(owner.count('<section class="owner-card">'), 10)
        self.assertIn("等待Jake确认", owner)
        self.assertNotRegex(owner, r"\b[0-9a-f]{64}\b")
        self.assertIn("engineering-evidence.html", owner)
        self.assertIn("both_qualified", engineering)
        self.assertIn("1" * 64, engineering)
        self.assertIn("OWNER_ACCEPTANCE_PENDING", engineering)
        for html in (owner, engineering):
            lowered = html.lower()
            self.assertNotIn("<script", lowered)
            self.assertNotIn("<form", lowered)
            self.assertNotIn("<button", lowered)


class EntryDecisionPublicationTests(unittest.TestCase):
    def test_reader_rejects_symlink_duplicate_noncanonical_and_float(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real.json"
            link = root / "link.json"
            real.write_bytes(canonical_json_bytes({"value": 1}) + b"\n")
            link.symlink_to(real)
            with self.assertRaises(EntryDecisionF0Error) as caught:
                read_canonical_json_file(link)
            self.assertEqual(caught.exception.reason_code, "INPUT_FILE_INVALID")
            cases = (
                (b'{"a":1,"a":2}\n', "CANONICAL_JSON_DUPLICATE_KEY"),
                (b'{"a": 1}\n', "CANONICAL_JSON_ENCODING_NONCANONICAL"),
                (b'{"a":1.0}\n', "CANONICAL_JSON_FLOAT_FORBIDDEN"),
            )
            for index, (payload, reason) in enumerate(cases):
                path = root / f"case-{index}.json"
                path.write_bytes(payload)
                with self.assertRaises(EntryDecisionF0Error) as caught:
                    read_canonical_json_file(path)
                self.assertEqual(caught.exception.reason_code, reason)

            wide = root / "wide-derived.json"
            wide_value = {"derived_comparison_numerator": 10**38}
            wide.write_bytes(canonical_json_bytes(wide_value) + b"\n")
            self.assertEqual(read_canonical_json_file(wide), wide_value)

    def test_publisher_is_no_clobber_and_preserves_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            artifacts = {
                "acceptance/index.html": b"<!doctype html>",
                "acceptance-manifest.json": b'{}\n',
            }
            publish_artifacts(output, artifacts)
            self.assertEqual(
                {
                    path.relative_to(output).as_posix(): path.read_bytes()
                    for path in output.rglob("*")
                    if path.is_file()
                },
                artifacts,
            )
            with self.assertRaises(EntryDecisionF0Error) as caught:
                publish_artifacts(output, artifacts)
            self.assertEqual(caught.exception.reason_code, "OUTPUT_ALREADY_EXISTS")

    def test_staging_failure_never_exposes_partial_final_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "result"
            with patch(
                "gld_entry_decision_f0.publish._atomic_rename_no_replace",
                side_effect=EntryDecisionF0Error("OUTPUT_PUBLICATION_FAILED"),
            ):
                with self.assertRaises(EntryDecisionF0Error):
                    publish_artifacts(
                        output,
                        {"nested/result.json": b'{"ok":true}\n'},
                    )
            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".result.staging-*")), [])

            output.mkdir()
            with self.assertRaises(EntryDecisionF0Error) as caught:
                publish_artifacts(output, {"result.json": b"{}\n"})
            self.assertEqual(caught.exception.reason_code, "OUTPUT_ALREADY_EXISTS")
            self.assertEqual(list(output.iterdir()), [])

class EntryDecisionCliFailureReceiptTests(unittest.TestCase):
    def test_invalid_input_writes_only_run_failed_receipt(self) -> None:
        cases = (
            (b'{"a":1,"a":2}\n', "CANONICAL_JSON_DUPLICATE_KEY"),
            (b'{"a": 1}\n', "CANONICAL_JSON_ENCODING_NONCANONICAL"),
            (b'{"a":1.0}\n', "CANONICAL_JSON_FLOAT_FORBIDDEN"),
            (
                canonical_json_bytes(
                    {
                        "schema_version": "ENTRY_DECISION_CLI_INPUT_F0_V1",
                        "entry_input": {},
                        "entry_policy": {},
                        "unknown": True,
                    }
                )
                + b"\n",
                "CLI_INPUT_SCHEMA_INVALID",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, (payload, reason) in enumerate(cases):
                with self.subTest(reason=reason):
                    input_path = root / f"input-{index}.json"
                    output = root / f"output-{index}"
                    input_path.write_bytes(payload)
                    completed = subprocess.run(
                        [
                            sys.executable,
                            str(TOOL),
                            "--input",
                            str(input_path),
                            "--output",
                            str(output),
                        ],
                        cwd=PROJECT_ROOT,
                        env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(completed.returncode, 2)
                    self.assertIn(reason, completed.stderr)
                    self.assertEqual(
                        [path.name for path in output.iterdir()],
                        ["run-receipt.json"],
                    )
                    receipt = json.loads(
                        (output / "run-receipt.json").read_bytes()
                    )
                    self.assertEqual(receipt["run_status"], "RUN_FAILED")
                    self.assertEqual(receipt["reason_code"], reason)
                    self.assertFalse(receipt["decision_result_generated"])
                    self.assertFalse(receipt["actionable"])
                    self.assertEqual(receipt["broker_order_count"], 0)
                    unsigned = dict(receipt)
                    receipt_hash = unsigned.pop("receipt_sha256")
                    self.assertEqual(
                        receipt_hash,
                        canonical_json_sha256(unsigned),
                    )
                    self.assertFalse(
                        any("decision-result" in path.name for path in output.iterdir())
                    )

    def test_unexpected_runtime_error_is_a_run_failed_receipt(self) -> None:
        from gld_entry_decision_f0.cli import main

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            with patch(
                "gld_entry_decision_f0.cli._single_artifacts",
                side_effect=RuntimeError("synthetic evaluator failure"),
            ):
                with redirect_stderr(io.StringIO()) as stderr:
                    status = main(
                        [
                            "--input",
                            str(Path(temporary) / "unused.json"),
                            "--output",
                            str(output),
                        ]
                    )
            self.assertEqual(status, 2)
            self.assertIn("UNEXPECTED_LOCAL_ERROR", stderr.getvalue())
            self.assertEqual(
                [path.name for path in output.iterdir()],
                ["run-receipt.json"],
            )
            receipt = json.loads((output / "run-receipt.json").read_bytes())
            self.assertEqual(receipt["run_status"], "RUN_FAILED")
            self.assertEqual(receipt["reason_code"], "UNEXPECTED_LOCAL_ERROR")
            self.assertFalse(receipt["decision_result_generated"])


class EntryDecisionCliAcceptanceTests(unittest.TestCase):
    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(TOOL), *arguments],
            cwd=PROJECT_ROOT,
            env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_demo_acceptance_publishes_owner_engineering_and_reference_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "acceptance"
            completed = self._run(
                "--demo-acceptance",
                "--output",
                str(output),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest_path = output / "acceptance-manifest.json"
            owner_path = output / "index.html"
            engineering_path = output / "engineering-evidence.html"
            self.assertTrue(manifest_path.is_file())
            self.assertTrue(owner_path.is_file())
            self.assertTrue(engineering_path.is_file())
            manifest_raw = manifest_path.read_bytes()
            manifest = json.loads(manifest_raw)
            self.assertEqual(
                manifest_raw,
                canonical_json_bytes(manifest) + b"\n",
            )
            unsigned_manifest = dict(manifest)
            manifest_hash = unsigned_manifest.pop("manifest_sha256")
            self.assertEqual(
                manifest_hash,
                canonical_json_sha256(unsigned_manifest),
            )
            self.assertEqual(manifest["owner_status"], "OWNER_ACCEPTANCE_PENDING")
            for rendered in manifest["rendered_artifacts"]:
                rendered_path = output / rendered["path"]
                self.assertTrue(rendered_path.is_file())
                self.assertEqual(
                    rendered["sha256"],
                    sha256(rendered_path.read_bytes()).hexdigest(),
                )
            self.assertEqual(len(manifest["scenarios"]), 8)
            self.assertEqual(
                tuple(item["scenario_id"] for item in manifest["scenarios"]),
                tuple(str(item["scenario_id"]) for item in ACCEPTANCE_SCENARIOS_F0),
            )
            self.assertTrue(
                all(
                    item["automated_evidence_status"]
                    == "AUTOMATED_EVIDENCE_PASS"
                    for item in manifest["scenarios"]
                )
            )
            owner_html = owner_path.read_text(encoding="utf-8")
            engineering_html = engineering_path.read_text(encoding="utf-8")
            self.assertEqual(owner_html.count('<section class="owner-card">'), 10)
            for title in (
                "缺数据与Gate失败是否正确区分",
                "Gate是否按两个组和固定政策判断",
                "LC0与BCS0是否真正独立",
                "一个结构失败时另一个是否仍可输出",
                "数量是否取所有硬cap的最小值",
                "双合格时是否有确定首选和备选",
                "每个方案是否绑定完整退出政策",
                "只看卡片是否能复述全过程",
                "中途停止是否说明位置、原因和恢复条件",
                "相同输入是否完全相同且broker writes恒为0",
            ):
                self.assertIn(title, owner_html)
            self.assertNotRegex(owner_html, r"\b[0-9a-f]{64}\b")
            self.assertIn("等待Jake确认", owner_html)
            self.assertIn("R1全部认同", owner_html)
            self.assertIn("OWNER_ACCEPTANCE_PENDING", engineering_html)
            for html in (owner_html, engineering_html):
                lowered = html.lower()
                self.assertNotIn("<script", lowered)
                self.assertNotIn("<form", lowered)
                self.assertNotIn("<button", lowered)
                self.assertNotIn("place order", lowered)
                self.assertNotIn("submit order", lowered)

            references = {
                item["asset_id"]: item for item in manifest["reference_assets"]
            }
            for name in (
                "GLD_ENTRY_DECISION_F0_SPEC",
                "GLD_ENTRY_POLICY_RESEARCH_ASSET_F0",
            ):
                self.assertIn(name, references)
                reference_path = output / references[name]["path"]
                self.assertTrue(reference_path.is_file())
                self.assertEqual(
                    references[name]["sha256"],
                    sha256(reference_path.read_bytes()).hexdigest(),
                )

            evidence_by_variant = {
                item["variant_id"]: item
                for item in manifest["evidence_variants"]
            }
            self.assertEqual(
                frozenset(evidence_by_variant),
                frozenset({"base", "exact-tie"}),
            )
            evidence_receipts: dict[str, dict[str, dict[str, object]]] = {}
            for variant_id, evidence in evidence_by_variant.items():
                input_path = output / evidence["input_path"]
                bundle_path = output / evidence["bundle_path"]
                input_raw = input_path.read_bytes()
                bundle_raw = bundle_path.read_bytes()
                self.assertEqual(
                    input_raw,
                    canonical_json_bytes(json.loads(input_raw)) + b"\n",
                )
                self.assertEqual(
                    bundle_raw,
                    canonical_json_bytes(json.loads(bundle_raw)) + b"\n",
                )
                self.assertEqual(
                    evidence["input_sha256"],
                    canonical_json_sha256(json.loads(input_raw)),
                )
                self.assertEqual(
                    evidence["bundle_sha256"],
                    sha256(bundle_raw[:-1]).hexdigest(),
                )
                receipt_map: dict[str, dict[str, object]] = {}
                for receipt_record in evidence["receipts"]:
                    receipt_path = output / receipt_record["path"]
                    receipt_raw = receipt_path.read_bytes()
                    receipt = json.loads(receipt_raw)
                    self.assertEqual(
                        receipt_raw,
                        canonical_json_bytes(receipt) + b"\n",
                    )
                    self.assertEqual(
                        receipt["evidence_sha256"],
                        receipt_record["evidence_sha256"],
                    )
                    unsigned_receipt = dict(receipt)
                    evidence_hash = unsigned_receipt.pop("evidence_sha256")
                    self.assertEqual(
                        evidence_hash,
                        canonical_json_sha256(unsigned_receipt),
                    )
                    self.assertEqual(
                        receipt["split_counts"],
                        {
                            "development_count": 1,
                            "sealed_oos_count": 1,
                            "walk_forward_oos_count": 100,
                        },
                    )
                    self.assertFalse(receipt["development_used_in_estimate"])
                    self.assertFalse(receipt["sealed_oos_used_in_estimate"])
                    self.assertEqual(
                        len(
                            {
                                episode["fold_id"]
                                for episode in receipt["episodes"]
                                if episode["split"] == "WALK_FORWARD_OOS"
                            }
                        ),
                        3,
                    )
                    self.assertGreaterEqual(receipt["coverage"]["coverage_ppm"], 950_000)
                    receipt_map[receipt_record["carrier_id"]] = receipt
                evidence_receipts[variant_id] = receipt_map

            for item in manifest["scenarios"]:
                for path_key in ("input_path", "result_path", "trace_path", "card_path"):
                    self.assertTrue((output / item[path_key]).is_file(), item[path_key])
                result_raw = (output / item["result_path"]).read_bytes()
                result = json.loads(result_raw)
                self.assertEqual(result_raw, canonical_json_bytes(result) + b"\n")
                self.assertEqual(result["classification"], "RESEARCH_ONLY")
                self.assertEqual(result["authority_status"], "NO_DECISION_EFFECT")
                self.assertFalse(result["actionable"])
                self.assertEqual(result["broker_order_count"], 0)
                self.assertEqual(len(result["decision_trace"]["stages"]), 8)
                self.assertEqual(result["result_sha256"], item["result_sha256"])
                for key, path_key in (
                    ("input_file_sha256", "input_path"),
                    ("result_file_sha256", "result_path"),
                    ("trace_file_sha256", "trace_path"),
                    ("card_file_sha256", "card_path"),
                ):
                    self.assertEqual(
                        item["artifact_hashes"][key],
                        sha256((output / item[path_key]).read_bytes()).hexdigest(),
                    )
                determinism = item["determinism_receipt"]
                self.assertEqual(
                    determinism["status"],
                    "CROSS_PROCESS_BYTE_IDENTICAL",
                )
                self.assertEqual(
                    determinism["local"],
                    determinism["independent_process"],
                )
                self.assertEqual(
                    determinism["input_order_status"],
                    "NORMALIZED_INPUT_ORDER_BYTE_IDENTICAL",
                )
                self.assertTrue(
                    determinism["local"][
                        "within_process_permutation_identical"
                    ]
                )
                self.assertEqual(
                    determinism["local"]["baseline"],
                    determinism["local"]["permuted_input_order"],
                )
                case = json.loads((output / item["input_path"]).read_bytes())
                variant_id = item["evidence_variant_id"]
                self.assertEqual(
                    item["evidence_bundle_sha256"],
                    evidence_by_variant[variant_id]["bundle_sha256"],
                )
                entry_evidence = case["entry_input"]["structure_evidence"]
                for carrier_id in ("LC0", "BCS0"):
                    receipt = evidence_receipts[variant_id][carrier_id]
                    self.assertEqual(
                        entry_evidence[carrier_id]["evidence_sha256"],
                        receipt["evidence_sha256"],
                    )
                    self.assertEqual(
                        entry_evidence[carrier_id][
                            "expected_net_return_on_entry_debit_ppm"
                        ],
                        receipt["expected_net_return_lower_bound_ppm"],
                    )
                card = (output / item["card_path"]).read_text(encoding="utf-8")
                self.assertIn(result["decision_status"], card)
                self.assertIn(result["result_sha256"], card)
                self.assertEqual(card.count('<section class="stage-card">'), 8)
                self.assertIn("六个数量硬cap与实际绑定cap", card)
                self.assertIn("Preference比较", card)
                self.assertIn("退出与持仓管理绑定", card)
                self.assertNotIn("<script", card.lower())
                self.assertNotIn("<form", card.lower())
                self.assertNotIn("<button", card.lower())
                if item["scenario_id"] == "exact_tie":
                    preference = result["decision_trace"]["stages"][5]["calculation"]
                    lc = preference["structures"]["LC0"]
                    bcs = preference["structures"]["BCS0"]
                    for key in (
                        "comparison_numerator",
                        "total_planned_loss_nano_usd",
                        "current_entry_cash_usage_nano_usd",
                    ):
                        self.assertEqual(lc[key], bcs[key], key)
                    self.assertEqual(preference["ranking_order"], ["LC0", "BCS0"])
                    self.assertEqual(
                        result["plans"]["preferred"]["carrier_id"],
                        "LC0",
                    )

            second_output = Path(temporary) / "acceptance-second"
            second = self._run(
                "--demo-acceptance",
                "--output",
                str(second_output),
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            first_tree = {
                path.relative_to(output): path.read_bytes()
                for path in output.rglob("*")
                if path.is_file()
            }
            second_tree = {
                path.relative_to(second_output): path.read_bytes()
                for path in second_output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(first_tree, second_tree)

    def test_single_mode_normalizes_list_order_and_is_no_clobber(self) -> None:
        case = build_synthetic_entry_decision_case_f0("both_qualified")
        permuted = deepcopy(case)
        entry = permuted["entry_input"]
        assert isinstance(entry, dict)
        for key in ("call_universe", "lc0_economics", "bcs0_economics"):
            values = entry[key]
            assert isinstance(values, list)
            values.reverse()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.json"
            permuted_path = root / "input-permuted.json"
            first_output = root / "first"
            second_output = root / "second"
            input_path.write_bytes(canonical_json_bytes(case) + b"\n")
            permuted_path.write_bytes(canonical_json_bytes(permuted) + b"\n")
            first = self._run(
                "--input", str(input_path), "--output", str(first_output)
            )
            second = self._run(
                "--input", str(permuted_path), "--output", str(second_output)
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            first_tree = {
                path.relative_to(first_output): path.read_bytes()
                for path in first_output.rglob("*")
                if path.is_file()
            }
            second_tree = {
                path.relative_to(second_output): path.read_bytes()
                for path in second_output.rglob("*")
                if path.is_file()
            }
            self.assertEqual(first_tree, second_tree)
            self.assertEqual(
                frozenset(path.as_posix() for path in first_tree),
                frozenset(
                    {
                        "entry-decision-input-f0.json",
                        "entry-decision-result-f0.json",
                        "decision-trace-f0.json",
                        "final-decision-card.html",
                    }
                ),
            )
            retry = self._run(
                "--input", str(input_path), "--output", str(first_output)
            )
            self.assertEqual(retry.returncode, 2)
            self.assertIn("OUTPUT_ALREADY_EXISTS", retry.stderr)


if __name__ == "__main__":
    unittest.main()
