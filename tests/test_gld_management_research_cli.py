from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from gld_management_research.canonical import (
    canonical_json_bytes,
    canonical_json_sha256,
)
from gld_management_research.examples import (
    ACCEPTANCE_SCENARIOS_F0,
    build_synthetic_action_snapshot_f0,
    build_synthetic_observation_f0,
)
from gld_management_research.render_html import (
    owner_acceptance_group_definitions_f0,
    render_engineering_evidence_index,
    render_management_score_card,
)
from gld_management_research.publish import read_canonical_json_file
from gld_management_research.errors import ManagementResearchError
from gld_management_research.workflow import run_management_research_f0


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL = PROJECT_ROOT / "tools" / "run_gld_management_score_f0.py"


class ManagementResearchIsolationTests(unittest.TestCase):
    def test_clean_import_does_not_load_authoritative_packages(self) -> None:
        source = """
import json
import sys
import gld_management_research
forbidden = sorted(
    name for name in sys.modules
    if name.startswith((
        'gld_simulation',
        'gld_data_contracts',
        'gld_research_core',
    ))
)
print(json.dumps(forbidden))
"""
        completed = subprocess.run(
            [sys.executable, "-c", source],
            cwd=PROJECT_ROOT,
            env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), [])


class ManagementResearchHtmlTests(unittest.TestCase):
    def _result_document(self) -> dict[str, object]:
        raw = build_synthetic_observation_f0("neutral_hold")
        return run_management_research_f0(
            raw,
            build_synthetic_action_snapshot_f0(raw, "neutral_hold"),
        )

    def test_management_card_is_static_explanatory_and_non_actionable(self) -> None:
        document = self._result_document()
        html = render_management_score_card(document)
        self.assertIn("GLD 持仓管理 Research F0", html)
        self.assertIn("Structure", html)
        self.assertIn("Trend", html)
        self.assertIn("Momentum", html)
        self.assertIn("Final Score", html)
        self.assertIn("Original approved units", html)
        self.assertIn("Shadow Policies", html)
        self.assertIn("volume quality", html)
        self.assertIn("direction balance", html)
        self.assertIn("RSI=", html)
        self.assertIn("Action policy", html)
        self.assertIn("actionable=false", html)
        self.assertIn("Exit latch", html)
        self.assertIn("Breakout Episode Diagnostic", html)
        self.assertIn("NO_ACTION_AUTHORITY", html)
        self.assertIn("RESEARCH_ONLY", html)
        self.assertIn("NO_DECISION_EFFECT", html)
        lowered = html.lower()
        self.assertNotIn("<script", lowered)
        self.assertNotIn("<form", lowered)
        self.assertNotIn("place order", lowered)
        self.assertNotIn("submit order", lowered)

    def test_owner_acceptance_group_catalog_is_closed_and_exhaustive(self) -> None:
        groups = owner_acceptance_group_definitions_f0()
        self.assertEqual(len(groups), 6)
        scenario_ids = tuple(
            scenario_id
            for group in groups
            for scenario_id in group.scenario_ids
        )
        expected_ids = tuple(
            str(specification["scenario_id"])
            for specification in ACCEPTANCE_SCENARIOS_F0
        )
        self.assertEqual(len(scenario_ids), len(set(scenario_ids)))
        self.assertEqual(frozenset(scenario_ids), frozenset(expected_ids))
        self.assertEqual(
            tuple(group.title for group in groups),
            (
                "中性环境是否保持仓位",
                "转弱是否先等待、连续确认后才分层减仓",
                "恢复是否先确认、之后逐层加回",
                "Hard Stop和终局锁定是否覆盖高分",
                "旧报价或缺数据是否停止行动",
                "BCS是否始终保持完整1:1组合",
            ),
        )

    def test_engineering_index_keeps_full_owner_pending_evidence(self) -> None:
        rows = [
            {
                "scenario_id": "neutral_hold",
                "purpose": "HOLD zone",
                "expected": "HOLD 4",
                "actual": "HOLD 4",
                "key_input": "Score 50.00 / current 4",
                "automated_evidence_status": "AUTOMATED_EVIDENCE_PASS",
                "owner_status": "OWNER_ACCEPTANCE_PENDING",
                "input_sha256": "b" * 64,
                "formula_sha256": "c" * 64,
                "policy_sha256": "d" * 64,
                "result_sha256": "a" * 64,
                "result_href": "neutral_hold/management-score-f0.json",
                "card_href": "neutral_hold/management-score-f0.html",
            }
        ]
        html = render_engineering_evidence_index(rows)
        self.assertIn("OWNER_ACCEPTANCE_PENDING", html)
        self.assertNotIn("OWNER_ACCEPTED", html)
        self.assertIn("工程证据", html)
        self.assertIn("Score 50.00 / current 4", html)
        self.assertIn("b" * 64, html)
        self.assertIn("neutral_hold/management-score-f0.html", html)
        self.assertNotIn("<script", html.lower())
        self.assertIn("NO_COMPLETE_UNIT_REDUCTION_AVAILABLE", html)
        self.assertIn("BCS所有调整保持完整1:1组合", html)
        self.assertIn("终局latch", html)


class ManagementResearchCliTests(unittest.TestCase):
    def test_single_run_publishes_canonical_json_and_static_html(self) -> None:
        observation = build_synthetic_observation_f0("neutral_hold")
        action = build_synthetic_action_snapshot_f0(
            observation,
            "neutral_hold",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            observation_path = root / "observation.json"
            action_path = root / "action.json"
            output = root / "result"
            observation_path.write_bytes(canonical_json_bytes(observation) + b"\n")
            action_path.write_bytes(canonical_json_bytes(action) + b"\n")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--observation",
                    str(observation_path),
                    "--action-snapshot",
                    str(action_path),
                    "--output",
                    str(output),
                ],
                cwd=PROJECT_ROOT,
                env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                [
                    "management-action-snapshot-f0.json",
                    "management-observation-f0.json",
                    "management-score-f0.html",
                    "management-score-f0.json",
                ],
            )
            raw = (output / "management-score-f0.json").read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            document = json.loads(raw)
            self.assertEqual(document["classification"], "RESEARCH_ONLY")
            self.assertFalse(document["actionable"])
            self.assertEqual(document["broker_order_count"], 0)

            second_output = root / "result-second-process"
            second = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--observation",
                    str(observation_path),
                    "--action-snapshot",
                    str(action_path),
                    "--output",
                    str(second_output),
                ],
                cwd=PROJECT_ROOT,
                env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                {
                    path.name: path.read_bytes()
                    for path in output.iterdir()
                },
                {
                    path.name: path.read_bytes()
                    for path in second_output.iterdir()
                },
            )

            permuted_observation = deepcopy(observation)
            permuted_action = deepcopy(action)
            permuted_observation["daily_bars"].reverse()
            permuted_action["contracts"].reverse()
            permuted_action["quotes"].reverse()
            permuted_observation_path = root / "observation-permuted.json"
            permuted_action_path = root / "action-permuted.json"
            permuted_observation_path.write_bytes(
                canonical_json_bytes(permuted_observation) + b"\n"
            )
            permuted_action_path.write_bytes(
                canonical_json_bytes(permuted_action) + b"\n"
            )
            permuted_output = root / "result-permuted"
            permuted = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--observation",
                    str(permuted_observation_path),
                    "--action-snapshot",
                    str(permuted_action_path),
                    "--output",
                    str(permuted_output),
                ],
                cwd=PROJECT_ROOT,
                env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(permuted.returncode, 0, permuted.stderr)
            self.assertEqual(
                {path.name: path.read_bytes() for path in output.iterdir()},
                {
                    path.name: path.read_bytes()
                    for path in permuted_output.iterdir()
                },
            )

            retry = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--observation",
                    str(observation_path),
                    "--action-snapshot",
                    str(action_path),
                    "--output",
                    str(output),
                ],
                cwd=PROJECT_ROOT,
                env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(retry.returncode, 0)
            self.assertIn("OUTPUT_ALREADY_EXISTS", retry.stderr)

    def test_demo_acceptance_generates_index_and_scenario_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "acceptance"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--demo-acceptance",
                    "--output",
                    str(output),
                ],
                cwd=PROJECT_ROOT,
                env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            index = output / "management-score-f0-acceptance" / "index.html"
            engineering = (
                output
                / "management-score-f0-acceptance"
                / "engineering-evidence.html"
            )
            manifest = output / "acceptance-manifest.json"
            self.assertTrue(index.is_file())
            self.assertTrue(engineering.is_file())
            self.assertTrue(manifest.is_file())
            manifest_document = json.loads(manifest.read_bytes())
            unsigned_manifest = dict(manifest_document)
            manifest_hash = unsigned_manifest.pop("manifest_sha256")
            self.assertEqual(
                manifest_hash, canonical_json_sha256(unsigned_manifest)
            )
            self.assertEqual(
                manifest_document["owner_status"],
                "OWNER_ACCEPTANCE_PENDING",
            )
            self.assertGreaterEqual(len(manifest_document["scenarios"]), 14)
            self.assertIn(
                "missing_dimension",
                {
                    item["scenario_id"]
                    for item in manifest_document["scenarios"]
                },
            )
            self.assertTrue(
                all(
                    item["automated_evidence_status"]
                    == "AUTOMATED_EVIDENCE_PASS"
                    for item in manifest_document["scenarios"]
                )
            )
            for item in manifest_document["scenarios"]:
                key_input = item["key_input"]
                self.assertLess(len(key_input), 500)
                self.assertNotIn("previous_score_result", key_input)
                self.assertNotIn("previous_action_result", key_input)
                self.assertIn("lineage=", key_input)
                self.assertIn("latch=", key_input)
            scenario_ids = {
                item["scenario_id"] for item in manifest_document["scenarios"]
            }
            self.assertIn("bcs_defensive_day_2", scenario_ids)
            self.assertIn("recovery_next_level", scenario_ids)
            self.assertIn("latched_episode_no_reopen", scenario_ids)
            index_text = index.read_text(encoding="utf-8")
            engineering_text = engineering.read_text(encoding="utf-8")
            self.assertIn(
                "Trading Platform｜GLD 持仓管理验收",
                index_text,
            )
            self.assertIn(
                "F0 研究验证版 · 本地模拟数据 · 不连接真实交易",
                index_text,
            )
            self.assertEqual(index_text.count('data-owner-group="'), 6)
            for title in (
                "中性环境是否保持仓位",
                "转弱是否先等待、连续确认后才分层减仓",
                "恢复是否先确认、之后逐层加回",
                "Hard Stop和终局锁定是否覆盖高分",
                "旧报价或缺数据是否停止行动",
                "BCS是否始终保持完整1:1组合",
            ):
                self.assertIn(title, index_text)
            for forbidden in (
                "Result JSON",
                "Observation",
                "Action facts",
                "hash",
                "JSON",
                "lineage=",
                "policy_sha256",
                "result_sha256",
                "WAITING_FOR_CONFIRMATION",
                "OWNER_ACCEPTANCE_PENDING",
                "Shadow Policies",
            ):
                self.assertNotIn(forbidden, index_text)
            self.assertNotRegex(index_text, r"\b[0-9a-f]{64}\b")
            self.assertNotIn("<script", index_text.lower())
            self.assertNotIn("<form", index_text.lower())
            self.assertNotIn("<button", index_text.lower())
            self.assertIn("engineering-evidence.html", index_text)
            self.assertIn("等待Jake确认", index_text)
            self.assertIn("1–6全部认同", index_text)
            self.assertEqual(
                index_text.count("程序表现与已认可规则一致（自动证据通过）"),
                6,
            )
            for actual_text in (
                "中性环境：分数50.00；分数处于保持区；程序保持4份。",
                "第一天转弱：分数30.00；只有第一天转弱，等待连续确认；程序保持4份。",
                "连续两天中度转弱：分数30.00；连续转弱已经确认；程序从4份降低到3份。",
                "连续两天严重转弱：分数8.51；连续转弱已经确认；程序从4份降低到2份。",
                "连续两天恢复：分数80.00；恢复已经确认，本次只恢复一个层级；程序从2份逐层恢复到3份。",
                "高分时触发Hard Stop：分数85.00；Hard Stop具有最高优先级；程序退出至0份。",
                "恢复条件满足但报价过期：分数80.00；报价已经过期；程序不执行新的调仓，保持2份。",
                "BCS存在残腿：分数8.51；组合存在残腿；程序不执行新的调仓，保持4份。",
            ):
                self.assertIn(actual_text, index_text)
            self.assertIn("Trading Platform｜GLD 持仓管理工程证据", engineering_text)
            for item in manifest_document["scenarios"]:
                for key in (
                    "observation_path",
                    "action_snapshot_path",
                    "result_path",
                    "card_path",
                ):
                    self.assertTrue((output / item[key]).is_file(), item[key])
                raw_result = (output / item["result_path"]).read_bytes()
                result_document = json.loads(raw_result)
                self.assertEqual(
                    raw_result, canonical_json_bytes(result_document) + b"\n"
                )
                self.assertEqual(result_document["classification"], "RESEARCH_ONLY")
                self.assertEqual(
                    result_document["authority_status"], "NO_DECISION_EFFECT"
                )
                self.assertFalse(result_document["actionable"])
                self.assertEqual(result_document["broker_order_count"], 0)
                unsigned_run = dict(result_document)
                run_hash = unsigned_run.pop("run_sha256")
                self.assertEqual(run_hash, canonical_json_sha256(unsigned_run))
                self.assertEqual(run_hash, item["result_sha256"])
                self.assertIn(
                    f"{item['scenario_id']}/management-score-f0.html",
                    engineering_text,
                )
                self.assertIn(item["scenario_id"], engineering_text)
                self.assertNotIn(item["scenario_id"], index_text)

            second_output = Path(temporary) / "acceptance-second-process"
            second = subprocess.run(
                [
                    sys.executable,
                    str(TOOL),
                    "--demo-acceptance",
                    "--output",
                    str(second_output),
                ],
                cwd=PROJECT_ROOT,
                env={"PYTHONPATH": str(PROJECT_ROOT / "src")},
                capture_output=True,
                text=True,
                check=False,
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

    def test_canonical_input_reader_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real.json"
            link = root / "link.json"
            real.write_bytes(canonical_json_bytes({"value": 1}) + b"\n")
            link.symlink_to(real)
            with self.assertRaises(ManagementResearchError) as caught:
                read_canonical_json_file(link)
            self.assertEqual(caught.exception.reason_code, "INPUT_FILE_INVALID")

    def test_canonical_input_reader_rejects_duplicate_noncanonical_and_float(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases = (
                (b'{"a":1,"a":2}\n', "CANONICAL_JSON_DUPLICATE_KEY"),
                (b'{"a": 1}\n', "CANONICAL_JSON_ENCODING_NONCANONICAL"),
                (b'{"a":1.0}\n', "CANONICAL_JSON_FLOAT_FORBIDDEN"),
            )
            for index, (payload, reason) in enumerate(cases):
                with self.subTest(reason=reason):
                    path = root / f"case-{index}.json"
                    path.write_bytes(payload)
                    with self.assertRaises(ManagementResearchError) as caught:
                        read_canonical_json_file(path)
                    self.assertEqual(caught.exception.reason_code, reason)


if __name__ == "__main__":
    unittest.main()
