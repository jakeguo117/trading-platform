from __future__ import annotations

import argparse
import ast
from contextlib import redirect_stderr
import inspect
import io
from multiprocessing import get_context
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from benchmarks.crr_p1_contract import (
    load_corpus,
    load_reference_golden,
    validate_raw_sample_receipt,
)
from benchmarks.crr_p1_decision_contract import load_decision_semantic_golden
import benchmarks.crr_p1_performance as performance
from tools.run_crr_p1_performance import build_parser


ROOT = Path(__file__).resolve().parents[1]
PERFORMANCE_PATH = ROOT / "benchmarks" / "crr_p1_performance.py"
CLI_PATH = ROOT / "tools" / "run_crr_p1_performance.py"
BACKEND_SHA256 = "a" * 64
PMSET_BATT_AC = """Now drawing from 'AC Power'
 -InternalBattery-0 100%; charged; present: true"""
PMSET_BATT_BATTERY = """Now drawing from 'Battery Power'
 -InternalBattery-0 80%; discharging; present: true"""
PMSET_CUSTOM_AC0_BATTERY1 = """Battery Power:
 lowpowermode         1
AC Power:
 lowpowermode         0"""
PMSET_CUSTOM_AC1_BATTERY0 = """Battery Power:
 lowpowermode         0
AC Power:
 lowpowermode         1"""
PMSET_CUSTOM_MISSING_ACTIVE = """Battery Power:
 lowpowermode         1"""
PMSET_CUSTOM_DUPLICATE = """Battery Power:
 lowpowermode         1
AC Power:
 lowpowermode         0
 lowpowermode         1"""
PMSET_CUSTOM_MALFORMED = """Battery Power:
 lowpowermode         1
AC Power:
 lowpowermode         off"""


def _nested_descendant_target(send_connection: object) -> None:
    os.setsid()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import signal,time;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "time.sleep(300)"
            ),
        ],
        close_fds=True,
        start_new_session=True,
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )
    send_connection.send(child.pid)
    send_connection.close()
    while True:
        time.sleep(1)


class _FakeReceiveConnection:
    def __init__(
        self,
        *,
        ready: bool,
        block_recv: bool = False,
        payload: bytes = b"{}\n",
    ) -> None:
        self.ready = ready
        self.block_recv = block_recv
        self.payload = payload
        self.closed = False
        self.release = __import__("threading").Event()

    def poll(self, seconds: int) -> bool:
        del seconds
        return self.ready

    def recv_bytes(self, maximum: int) -> bytes:
        if self.block_recv:
            self.release.wait(5)
        if self.closed:
            raise OSError("connection closed")
        if len(self.payload) > maximum:
            raise OSError("payload too large")
        return self.payload

    def close(self) -> None:
        self.closed = True
        self.release.set()


class _FakeProcess:
    def __init__(self, *, alive: bool, exitcode: int | None) -> None:
        self.alive = alive
        self.exitcode = exitcode
        self.join_seconds: list[int] = []

    def join(self, seconds: int) -> None:
        self.join_seconds.append(seconds)

    def is_alive(self) -> bool:
        return self.alive


class _FakeClock:
    def __init__(self) -> None:
        self.now_ns = 10_000

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, duration_ns: int) -> None:
        self.now_ns += duration_ns


class _FakeDriver:
    def __init__(
        self,
        clock: _FakeClock,
        *,
        fixture_count: int,
        fail_at: tuple[str, int] | None = None,
        mismatch_at: tuple[str, int] | None = None,
    ) -> None:
        self.clock = clock
        self.fixture_count = fixture_count
        self.fail_at = fail_at
        self.mismatch_at = mismatch_at
        self.events: list[tuple[str, int | str]] = []
        self.stage_counts = {"W64": 0, "SINGLE": 0, "BATCH": 0, "E2E": 0}

    def _begin(self, stage: str, marker: int | str) -> int:
        self.stage_counts[stage] += 1
        occurrence = self.stage_counts[stage]
        self.events.append((stage, marker))
        if self.fail_at == (stage, occurrence):
            raise RuntimeError("synthetic failure")
        self.clock.advance(occurrence * 10 + len(stage))
        return occurrence

    def run_w64_e2e(self) -> dict[str, object]:
        occurrence = self._begin("W64", "W64")
        fixture_id = (
            "U64"
            if self.mismatch_at == ("W64", occurrence)
            else "W64"
        )
        return {
            "fixture_id": fixture_id,
            "requested": self.fixture_count,
            "bound": self.fixture_count,
            "started": self.fixture_count,
            "terminal": self.fixture_count,
            "worker_count": 8,
            "kernel_threads": 1,
            "cache_hits": 0,
            "semantic_receipt": {"fixture_id": fixture_id},
        }

    def run_single(self, ordinal: int) -> dict[str, object]:
        occurrence = self._begin("SINGLE", ordinal)
        actual_ordinal = (
            ordinal + 1
            if self.mismatch_at == ("SINGLE", occurrence)
            else ordinal
        )
        return {
            "ordinal": actual_ordinal,
            "terminal": {"ordinal": actual_ordinal},
        }

    def run_batch(self) -> dict[str, object]:
        occurrence = self._begin("BATCH", "U64")
        terminal = (
            self.fixture_count - 1
            if self.mismatch_at == ("BATCH", occurrence)
            else self.fixture_count
        )
        return {
            "fixture_id": "U64",
            "requested": self.fixture_count,
            "bound": self.fixture_count,
            "started": self.fixture_count,
            "terminal": terminal,
            "worker_count": 8,
            "kernel_threads": 1,
            "cache_hits": 0,
            "semantic_output_sha256": "b" * 64,
        }

    def run_u64_e2e(self) -> dict[str, object]:
        occurrence = self._begin("E2E", "U64")
        fixture_id = (
            "W64"
            if self.mismatch_at == ("E2E", occurrence)
            else "U64"
        )
        return {
            "fixture_id": fixture_id,
            "requested": self.fixture_count,
            "bound": self.fixture_count,
            "started": self.fixture_count,
            "terminal": self.fixture_count,
            "worker_count": 8,
            "kernel_threads": 1,
            "cache_hits": 0,
            "semantic_receipt": {"fixture_id": fixture_id},
        }


class MachineReceiptTests(unittest.TestCase):
    def _receipt(
        self,
        *,
        power_status: str,
        custom_profiles: str,
    ) -> dict[str, object]:
        command_values = {
            "machdep.cpu.brand_string": "Apple M4",
            "kern.osversion": "25F84",
            "hw.physicalcpu": "10",
            "hw.logicalcpu": "10",
            "hw.memsize": str(16 * 1024**3),
            "batt": power_status,
            "custom": custom_profiles,
            "therm": "No thermal warning level has been recorded",
        }

        def read_command(args: tuple[str, ...]) -> str | None:
            return command_values.get(args[-1])

        with (
            patch.object(performance, "_read_command", side_effect=read_command),
            patch.object(performance.platform, "machine", return_value="arm64"),
            patch.object(
                performance.platform,
                "mac_ver",
                return_value=("26.5.2", ("", "", ""), ""),
            ),
            patch.object(performance.platform, "system", return_value="Darwin"),
            patch.object(
                performance.platform,
                "python_version",
                return_value="3.14.3",
            ),
            patch.object(performance.os, "cpu_count", return_value=10),
        ):
            return performance._machine_receipt()

    def test_ac_active_ignores_battery_low_power_profile(self) -> None:
        receipt = self._receipt(
            power_status=PMSET_BATT_AC,
            custom_profiles=PMSET_CUSTOM_AC0_BATTERY1,
        )

        self.assertEqual(receipt["machine"]["active_power_source"], "AC Power")
        self.assertEqual(receipt["machine"]["active_low_power_mode"], 0)
        self.assertTrue(
            receipt["machine_qualification_checks"][
                "active_power_profile_valid"
            ]
        )
        self.assertTrue(
            receipt["machine_qualification_checks"]["low_power_mode_off"]
        )
        self.assertTrue(receipt["machine_qualification_pass"])

    def test_ac_active_low_power_one_fails(self) -> None:
        receipt = self._receipt(
            power_status=PMSET_BATT_AC,
            custom_profiles=PMSET_CUSTOM_AC1_BATTERY0,
        )

        self.assertEqual(receipt["machine"]["active_low_power_mode"], 1)
        self.assertFalse(
            receipt["machine_qualification_checks"]["low_power_mode_off"]
        )
        self.assertFalse(receipt["machine_qualification_pass"])

    def test_battery_current_selects_battery_section_only(self) -> None:
        receipt = self._receipt(
            power_status=PMSET_BATT_BATTERY,
            custom_profiles=PMSET_CUSTOM_AC1_BATTERY0,
        )

        self.assertEqual(
            receipt["machine"]["active_power_source"],
            "Battery Power",
        )
        self.assertEqual(receipt["machine"]["active_low_power_mode"], 0)
        self.assertTrue(
            receipt["machine_qualification_checks"]["low_power_mode_off"]
        )
        self.assertFalse(receipt["machine_qualification_checks"]["ac_power"])
        self.assertFalse(receipt["machine_qualification_pass"])

    def test_missing_duplicate_malformed_or_source_mismatch_fail_closed(self) -> None:
        duplicate_section = """AC Power:
 lowpowermode 0
AC Power:
 lowpowermode 0"""
        cases = (
            ("missing-active-section", PMSET_CUSTOM_MISSING_ACTIVE),
            ("duplicate-setting", PMSET_CUSTOM_DUPLICATE),
            ("duplicate-section", duplicate_section),
            ("malformed-setting", PMSET_CUSTOM_MALFORMED),
        )
        for name, custom_profiles in cases:
            with self.subTest(name=name):
                receipt = self._receipt(
                    power_status=PMSET_BATT_AC,
                    custom_profiles=custom_profiles,
                )
                self.assertEqual(
                    receipt["machine"]["active_power_source"],
                    "AC Power",
                )
                self.assertIsNone(
                    receipt["machine"]["active_low_power_mode"]
                )
                self.assertFalse(
                    receipt["machine_qualification_checks"][
                        "active_power_profile_valid"
                    ]
                )
                self.assertFalse(
                    receipt["machine_qualification_checks"][
                        "low_power_mode_off"
                    ]
                )
                self.assertFalse(receipt["machine_qualification_pass"])

        for power_status in (
            "Now drawing from 'UPS Power'",
            PMSET_BATT_AC + "\nNow drawing from 'AC Power'",
        ):
            with self.subTest(power_status=power_status):
                receipt = self._receipt(
                    power_status=power_status,
                    custom_profiles=PMSET_CUSTOM_AC0_BATTERY1,
                )
                self.assertIsNone(
                    receipt["machine"]["active_power_source"]
                )
                self.assertIsNone(
                    receipt["machine"]["active_low_power_mode"]
                )
                self.assertFalse(receipt["machine_qualification_pass"])


class PerformanceSequenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = performance._PerformancePlanV1(
            fixture_count=3,
            warmup_runs=2,
            single_repetitions=3,
            batch_samples=4,
            e2e_samples=5,
            cold_e2e_samples=2,
            batch_workers=8,
        )

    def test_small_plan_has_fixed_order_counts_raw_samples_and_p95(self) -> None:
        clock = _FakeClock()
        driver = _FakeDriver(clock, fixture_count=self.plan.fixture_count)

        measured = performance._execute_measurement_plan_for_test(
            plan=self.plan,
            driver=driver,
            clock_ns=clock,
        )

        self.assertEqual(
            driver.events[:2],
            [("W64", "W64"), ("W64", "W64")],
        )
        self.assertEqual(
            measured.single_execution_order,
            (1, 2, 3, 1, 2, 3, 1, 2, 3),
        )
        self.assertEqual(
            tuple(len(samples) for samples in measured.single_samples_by_contract),
            (3, 3, 3),
        )
        self.assertEqual(len(measured.single_pooled_samples_ns), 9)
        self.assertEqual(len(measured.batch_samples_ns), 4)
        self.assertEqual(len(measured.e2e_samples_ns), 5)
        self.assertEqual(len(measured.w64_semantic_records), 2)
        self.assertEqual(len(measured.e2e_semantic_records), 5)
        self.assertEqual(
            measured.single_pooled_p95_ns,
            performance.nearest_rank_p95_ns(
                measured.single_pooled_samples_ns
            ),
        )
        self.assertEqual(
            measured.batch_p95_ns,
            performance.nearest_rank_p95_ns(measured.batch_samples_ns),
        )
        self.assertEqual(
            measured.e2e_p95_ns,
            performance.nearest_rank_p95_ns(measured.e2e_samples_ns),
        )

    def test_nearest_rank_uses_ceil_rank_without_interpolation(self) -> None:
        self.assertEqual(
            performance.nearest_rank_p95_ns(range(1, 101)),
            95,
        )
        self.assertEqual(
            performance.nearest_rank_p95_ns((90, 10, 80, 20)),
            90,
        )

    def test_any_failure_or_semantic_mismatch_prevents_p95(self) -> None:
        cases = (
            ("exception", {"fail_at": ("SINGLE", 2)}),
            ("single-mismatch", {"mismatch_at": ("SINGLE", 2)}),
            ("batch-terminal-mismatch", {"mismatch_at": ("BATCH", 1)}),
            ("e2e-semantic-mismatch", {"mismatch_at": ("E2E", 1)}),
        )
        for name, changes in cases:
            with self.subTest(name=name):
                clock = _FakeClock()
                driver = _FakeDriver(
                    clock,
                    fixture_count=self.plan.fixture_count,
                    **changes,
                )
                with patch.object(
                    performance,
                    "nearest_rank_p95_ns",
                    side_effect=AssertionError("P95 must not be computed"),
                ) as p95, self.assertRaises(performance.PerformanceRunError):
                    performance._execute_measurement_plan_for_test(
                        plan=self.plan,
                        driver=driver,
                        clock_ns=clock,
                    )
                p95.assert_not_called()

    def test_warmup_is_exact_and_never_adaptive(self) -> None:
        clock = _FakeClock()
        driver = _FakeDriver(clock, fixture_count=self.plan.fixture_count)

        performance._execute_measurement_plan_for_test(
            plan=self.plan,
            driver=driver,
            clock_ns=clock,
        )

        self.assertEqual(driver.stage_counts["W64"], 2)
        first_timed_index = next(
            index
            for index, event in enumerate(driver.events)
            if event[0] != "W64"
        )
        self.assertEqual(first_timed_index, 2)

    def test_coordinator_schedule_is_two_runners_then_cold_processes(self) -> None:
        self.assertEqual(
            performance._formal_launch_schedule_for_test(self.plan),
            (
                ("RUNNER", 1),
                ("RUNNER", 2),
                ("COLD_E2E", 1),
                ("COLD_E2E", 2),
            ),
        )


class PerformanceReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = load_corpus("U64")
        cls.golden = load_reference_golden("U64")

    def test_existing_raw_schema_stays_non_claiming_and_valid(self) -> None:
        terminal = self.golden["results"][0]
        single = performance._build_formal_raw_receipt_for_test(
            stage="SINGLE_CALL",
            runner_process_ordinal=1,
            samples_ns=tuple(range(1, 31)),
            backend_evidence_sha256=BACKEND_SHA256,
            corpus=self.corpus,
            golden=self.golden,
            contract_ordinal=1,
            call_terminal_semantic=terminal,
        )
        batch = performance._build_formal_raw_receipt_for_test(
            stage="BATCH_64",
            runner_process_ordinal=1,
            samples_ns=tuple(range(1, 101)),
            backend_evidence_sha256=BACKEND_SHA256,
            corpus=self.corpus,
            golden=self.golden,
        )

        self.assertFalse(single["slo_pass_claimed"])
        self.assertFalse(batch["slo_pass_claimed"])
        self.assertEqual(validate_raw_sample_receipt(single), 29)
        self.assertEqual(validate_raw_sample_receipt(batch), 95)

        with patch.object(performance.gc, "isenabled", return_value=False):
            disabled = performance._build_formal_raw_receipt_for_test(
                stage="BATCH_64",
                runner_process_ordinal=1,
                samples_ns=tuple(range(1, 101)),
                backend_evidence_sha256=BACKEND_SHA256,
                corpus=self.corpus,
                golden=self.golden,
            )
        self.assertEqual(
            disabled["gc_policy"],
            "DISABLED_DURING_TIMED_SAMPLE",
        )

    def test_aggregate_slo_is_separate_and_uses_max_single_contract(self) -> None:
        passing = performance._evaluate_runner_latency_for_test(
            single_contract_p95_ns=(1, 250_000_000, 249_999_999),
            batch_p95_ns=2_000_000_000,
            e2e_p95_ns=3_000_000_000,
        )
        failing = performance._evaluate_runner_latency_for_test(
            single_contract_p95_ns=(1, 250_000_001, 2),
            batch_p95_ns=2_000_000_001,
            e2e_p95_ns=3_000_000_001,
        )

        self.assertTrue(passing["runner_latency_gate_pass"])
        self.assertEqual(passing["single_gate_value_ns"], 250_000_000)
        self.assertFalse(failing["runner_latency_gate_pass"])
        self.assertEqual(
            failing["failed_components"],
            ["SINGLE_CALL", "BATCH_64", "E2E"],
        )

    def test_parent_semantic_sidecar_check_recomputes_golden_and_hashes(self) -> None:
        expected = performance._thaw_json(
            load_decision_semantic_golden("U64").document
        )
        _, request_sha256 = performance.encode_p1_decision_request(
            **performance._typed_request_kwargs(self.corpus)
        )
        request_binding = performance._expected_request_binding(
            self.corpus,
            expected,
        )
        native_request_sha256 = performance._canonical_sha256(request_binding)
        batch_provenance_sha256 = "b" * 64
        semantic = {
            "actionable": False,
            "batch_execution_provenance_sha256": batch_provenance_sha256,
            "broker_order_count": 0,
            "call_semantic_output_sha256": expected[
                "call_semantic_output_sha256"
            ],
            "call_terminals": expected["call_terminals"],
            "decision_projection": expected["decision_projection"],
            "projection_sha256": performance._canonical_sha256(
                expected["decision_projection"]
            ),
            "request_binding": request_binding,
            "request_sha256": native_request_sha256,
        }
        runtime_sha256 = performance._canonical_sha256(
            {
                "call_semantic_output_sha256": semantic[
                    "call_semantic_output_sha256"
                ],
                "projection_sha256": semantic["projection_sha256"],
                "request_binding": request_binding,
                "schema_version": performance.RUNTIME_SEMANTIC_SCHEMA,
            }
        )
        semantic["runtime_semantic_sha256"] = runtime_sha256
        shadow_artifact_sha256 = performance._canonical_sha256(
            {
                "actionable": False,
                "broker_order_count": 0,
                "call_semantic_output_sha256": semantic[
                    "call_semantic_output_sha256"
                ],
                "call_terminals": semantic["call_terminals"],
                "decision_projection": semantic["decision_projection"],
                "projection_sha256": semantic["projection_sha256"],
                "request_binding": request_binding,
                "runtime_semantic_sha256": runtime_sha256,
                "schema_version": performance.SHADOW_ARTIFACT_SCHEMA,
            }
        )
        semantic["shadow_artifact_sha256"] = shadow_artifact_sha256
        execution_sha256 = performance._canonical_sha256(
            {
                "backend_evidence_sha256": BACKEND_SHA256,
                "batch_execution_provenance_sha256": (
                    batch_provenance_sha256
                ),
                "batch_size": 64,
                "cache_hits": 0,
                "kernel_threads": 1,
                "request_sha256": native_request_sha256,
                "runtime_semantic_sha256": runtime_sha256,
                "schema_version": performance.NATIVE_EXECUTION_SCHEMA,
                "shadow_artifact_sha256": shadow_artifact_sha256,
                "worker_count": 8,
            }
        )
        semantic["backend_evidence_components"] = {
            "artifact_sha256": shadow_artifact_sha256,
            "backend_evidence_sha256": BACKEND_SHA256,
            "execution_sha256": execution_sha256,
            "input_sha256": native_request_sha256,
        }
        record = {
            "artifact_sha256": "d" * 64,
            "decision_request_sha256": request_sha256,
            "decision_semantic_golden_sha256": expected[
                "decision_semantic_sha256"
            ],
            "fixture_id": "U64",
            "projection_sha256": semantic["projection_sha256"],
            "reconstructed_decision_semantic_sha256": expected[
                "decision_semantic_sha256"
            ],
            "runtime_semantic_sha256": runtime_sha256,
            "semantic_receipt": semantic,
            "semantic_receipt_sha256": (
                performance._compact_canonical_sha256(semantic)
            ),
            "supervisor_generation_token": "e" * 64,
            "supervisor_request_sha256": "f" * 64,
        }

        self.assertIs(
            performance._validate_decision_sidecar_record(
                record,
                fixture_id="U64",
                expected_document=expected,
                expected_request_binding=request_binding,
                expected_request_sha256=request_sha256,
                expected_combined_backend_sha256=BACKEND_SHA256,
            ),
            record,
        )
        record["semantic_receipt_sha256"] = "0" * 64
        with self.assertRaises(performance.PerformanceRunError):
            performance._validate_decision_sidecar_record(
                record,
                fixture_id="U64",
                expected_document=expected,
                expected_request_binding=request_binding,
                expected_request_sha256=request_sha256,
                expected_combined_backend_sha256=BACKEND_SHA256,
            )

    def test_failed_aggregate_never_promotes_p1(self) -> None:
        failure = performance._failed_component_aggregate(
            reason_code="PERFORMANCE_SAMPLE_COUNT_INVALID",
            runner_processes_completed=1,
            cold_processes_completed=0,
            output_file_sha256={},
        )

        self.assertEqual(failure["p1_overall_status"], "P1_PERFORMANCE_NOT_PASS")
        self.assertEqual(failure["decision_status"], "NO_DECISION")
        self.assertFalse(failure["actionable"])
        self.assertEqual(failure["broker_order_count"], 0)
        self.assertIn(
            "E1_PREFLIGHT_NOT_SUPPLIED_OR_VERIFIED",
            failure["p1_blocking_gates"],
        )
        self.assertIn(
            "T1_PREFLIGHT_NOT_SUPPLIED_OR_VERIFIED",
            failure["p1_blocking_gates"],
        )
        self.assertIn(
            "PLUGIN_OUTER_PROCESS_LIFECYCLE_NOT_VERIFIED",
            failure["p1_blocking_gates"],
        )
        self.assertFalse(failure["p1_promotion_gate_pass"])
        self.assertEqual(
            failure["plugin_outer_process_lifecycle"],
            "NOT_VERIFIED",
        )

    def test_cold_gate_requires_explicit_verified_lifecycle(self) -> None:
        self.assertEqual(
            performance._CURRENT_PLUGIN_OUTER_PROCESS_LIFECYCLE,
            performance._PLUGIN_OUTER_LIFECYCLE_NOT_VERIFIED,
        )
        unknown = performance._evaluate_cold_gate_for_test(
            cold_p95_ns=1,
            plugin_outer_process_lifecycle=(
                performance._PLUGIN_OUTER_LIFECYCLE_NOT_VERIFIED
            ),
        )
        self.assertFalse(unknown["lifecycle_verified"])
        self.assertFalse(unknown["gate_applied"])
        self.assertFalse(unknown["report_only"])
        self.assertFalse(unknown["gate_pass"])
        self.assertEqual(
            unknown["promotion_blocker"],
            "PLUGIN_OUTER_PROCESS_LIFECYCLE_NOT_VERIFIED",
        )

        persistent = performance._evaluate_cold_gate_for_test(
            cold_p95_ns=performance.E2E_SLO_NS + 1,
            plugin_outer_process_lifecycle=(
                performance._PLUGIN_OUTER_LIFECYCLE_PERSISTENT_VERIFIED
            ),
        )
        self.assertTrue(persistent["lifecycle_verified"])
        self.assertFalse(persistent["gate_applied"])
        self.assertTrue(persistent["report_only"])
        self.assertIsNone(persistent["gate_pass"])
        self.assertFalse(persistent["conditional_gate_pass"])
        self.assertIsNone(persistent["promotion_blocker"])

        fresh_pass = performance._evaluate_cold_gate_for_test(
            cold_p95_ns=performance.E2E_SLO_NS,
            plugin_outer_process_lifecycle=(
                performance._PLUGIN_OUTER_LIFECYCLE_FRESH_VERIFIED
            ),
        )
        self.assertTrue(fresh_pass["gate_applied"])
        self.assertFalse(fresh_pass["report_only"])
        self.assertTrue(fresh_pass["gate_pass"])
        self.assertIsNone(fresh_pass["promotion_blocker"])

        fresh_fail = performance._evaluate_cold_gate_for_test(
            cold_p95_ns=performance.E2E_SLO_NS + 1,
            plugin_outer_process_lifecycle=(
                performance._PLUGIN_OUTER_LIFECYCLE_FRESH_VERIFIED
            ),
        )
        self.assertTrue(fresh_fail["gate_applied"])
        self.assertFalse(fresh_fail["gate_pass"])
        self.assertEqual(
            fresh_fail["promotion_blocker"],
            "COLD_E2E_SLO_NOT_PASS",
        )

        with self.assertRaises(performance.PerformanceRunError):
            performance._evaluate_cold_gate_for_test(
                cold_p95_ns=1,
                plugin_outer_process_lifecycle="PERSISTENT_ASSUMED",
            )

    def test_component_aggregate_wires_cold_lifecycle_gate(self) -> None:
        def runner_bundle(process_marker: str) -> dict[str, object]:
            return {
                "lineage": {
                    "backend": {"backend_sha256": BACKEND_SHA256},
                    "fixtures_and_goldens": {"fixture_id": "U64"},
                    "machine": {
                        "machine_qualification_pass": True,
                        "machine_sha256": "1" * 64,
                    },
                    "process": {"process_sha256": process_marker * 64},
                    "runtime": {"runtime_sha256": "2" * 64},
                    "source": {"source_set_sha256": "3" * 64},
                },
                "raw_receipts": {
                    "batch_64": {"samples_ns": [1]},
                    "e2e": {"samples_ns": [1]},
                    "single_call": [
                        {"samples_ns": [1]}
                        for _ in range(performance.FIXTURE_COUNT)
                    ],
                },
                "runner_aggregate": {
                    "latency": {"runner_latency_gate_pass": True},
                    "pooled_single_samples_ns": [1],
                },
            }

        bundles = [runner_bundle("4"), runner_bundle("5")]

        def aggregate(
            *,
            lifecycle: str,
            cold_sample_ns: int,
        ) -> dict[str, object]:
            with patch.object(
                performance,
                "_CURRENT_PLUGIN_OUTER_PROCESS_LIFECYCLE",
                lifecycle,
            ):
                return performance._component_aggregate(
                    runner_bundles=bundles,
                    cold_samples_ns=(cold_sample_ns,) * 30,
                    cold_records=(),
                    output_file_sha256={},
                )

        unknown = aggregate(
            lifecycle=performance._PLUGIN_OUTER_LIFECYCLE_NOT_VERIFIED,
            cold_sample_ns=1,
        )
        self.assertIn(
            "PLUGIN_OUTER_PROCESS_LIFECYCLE_NOT_VERIFIED",
            unknown["p1_blocking_gates"],
        )
        self.assertFalse(unknown["p1_promotion_gate_pass"])

        persistent = aggregate(
            lifecycle=(
                performance._PLUGIN_OUTER_LIFECYCLE_PERSISTENT_VERIFIED
            ),
            cold_sample_ns=performance.E2E_SLO_NS + 1,
        )
        self.assertTrue(persistent["cold_e2e"]["report_only"])
        self.assertTrue(persistent["latency_gate_pass"])
        self.assertNotIn(
            "COLD_E2E_SLO_NOT_PASS",
            persistent["p1_blocking_gates"],
        )
        self.assertNotIn(
            "LATENCY_GATE_NOT_PASS",
            persistent["p1_blocking_gates"],
        )

        fresh_pass = aggregate(
            lifecycle=performance._PLUGIN_OUTER_LIFECYCLE_FRESH_VERIFIED,
            cold_sample_ns=performance.E2E_SLO_NS,
        )
        self.assertTrue(fresh_pass["cold_e2e"]["gate_applied"])
        self.assertTrue(fresh_pass["cold_e2e"]["gate_pass"])
        self.assertTrue(fresh_pass["latency_gate_pass"])

        fresh_fail = aggregate(
            lifecycle=performance._PLUGIN_OUTER_LIFECYCLE_FRESH_VERIFIED,
            cold_sample_ns=performance.E2E_SLO_NS + 1,
        )
        self.assertFalse(fresh_fail["cold_e2e"]["gate_pass"])
        self.assertFalse(fresh_fail["latency_gate_pass"])
        self.assertIn(
            "COLD_E2E_SLO_NOT_PASS",
            fresh_fail["p1_blocking_gates"],
        )
        self.assertIn(
            "LATENCY_GATE_NOT_PASS",
            fresh_fail["p1_blocking_gates"],
        )


class PerformancePublicBoundaryTests(unittest.TestCase):
    def test_formal_plan_and_public_signature_are_frozen(self) -> None:
        plan = performance.FORMAL_PERFORMANCE_PLAN
        self.assertEqual(
            (
                plan.fixture_count,
                plan.warmup_runs,
                plan.single_repetitions,
                plan.batch_samples,
                plan.e2e_samples,
                plan.cold_e2e_samples,
                plan.batch_workers,
            ),
            (64, 3, 30, 100, 100, 30, 8),
        )
        self.assertEqual(
            tuple(inspect.signature(performance.run_formal_performance).parameters),
            (
                "native_manifest_path",
                "expected_backend_evidence_sha256",
                "output_directory",
            ),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in inspect.signature(
                    performance.run_formal_performance
                ).parameters.values()
            )
        )

    def test_cli_has_only_three_explicit_configuration_inputs(self) -> None:
        parser = build_parser()
        self.assertIs(type(parser), argparse.ArgumentParser)
        destinations = {
            action.dest
            for action in parser._actions
            if action.dest != "help"
        }
        self.assertEqual(
            destinations,
            {
                "native_manifest_path",
                "expected_backend_evidence_sha256",
                "output_directory",
            },
        )
        parsed = parser.parse_args(
            [
                "--native-manifest-path",
                "/tmp/manifest.json",
                "--expected-backend-evidence-sha256",
                BACKEND_SHA256,
                "--output-directory",
                "/tmp/output",
            ]
        )
        self.assertEqual(parsed.expected_backend_evidence_sha256, BACKEND_SHA256)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "--native-manifest-path",
                    "/tmp/manifest.json",
                    "--expected-backend-evidence-sha256",
                    BACKEND_SHA256,
                    "--output-directory",
                    "/tmp/output",
                    "--samples",
                    "1",
                ]
            )

    def test_production_sources_have_no_runtime_tuning_or_backend_discovery(self) -> None:
        source = (
            PERFORMANCE_PATH.read_text(encoding="utf-8")
            + CLI_PATH.read_text(encoding="utf-8")
        ).lower()
        forbidden = (
            "getenv(",
            "environ[",
            "latest",
            "build_native_kernel_v1",
            "broker_delta",
            "candidate_pruning",
            "--workers",
            "--warmup",
            "--timeout",
            "--steps",
            "--fixture",
            "--cache",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_outer_process_waits_are_fixed_and_bounded(self) -> None:
        self.assertEqual(performance._outer_hard_limit_seconds("RUNNER"), 1800)
        self.assertEqual(performance._outer_hard_limit_seconds("COLD_E2E"), 30)
        tree = ast.parse(PERFORMANCE_PATH.read_text(encoding="utf-8"))
        joins = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join"
        ]
        self.assertTrue(joins)
        self.assertTrue(all(node.args or node.keywords for node in joins))
        source = PERFORMANCE_PATH.read_text(encoding="utf-8")
        self.assertIn("threading.Thread(", source)
        self.assertIn("completed.wait(", source)
        self.assertIn("os.setsid()", source)
        self.assertIn("_descendant_process_ids", source)

        timeout_connection = _FakeReceiveConnection(
            ready=True,
            block_recv=True,
        )
        self.assertTrue(timeout_connection.poll(0))
        timeout_process = _FakeProcess(alive=True, exitcode=None)
        cleanup_calls: list[object] = []
        started = time.monotonic()
        with patch.object(
            performance,
            "_outer_hard_limit_seconds",
            return_value=0.05,
        ), patch.object(
            performance,
            "_cleanup_outer_process",
            side_effect=lambda value: (
                cleanup_calls.append(value),
                setattr(value, "alive", False),
            ),
        ), self.assertRaises(performance.PerformanceRunError) as timeout:
            performance._bounded_receive_and_reap(
                receive_connection=timeout_connection,
                process=timeout_process,
                process_role="COLD_E2E",
            )
        self.assertEqual(
            timeout.exception.reason_code,
            "PERFORMANCE_OUTER_PROCESS_HARD_LIMIT",
        )
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(cleanup_calls, [timeout_process])
        self.assertTrue(timeout_connection.closed)
        self.assertEqual(timeout_process.join_seconds, [])

        teardown_connection = _FakeReceiveConnection(ready=True)
        teardown_process = _FakeProcess(alive=True, exitcode=None)
        with self.assertRaises(performance.PerformanceRunError) as teardown:
            performance._bounded_receive_and_reap(
                receive_connection=teardown_connection,
                process=teardown_process,
                process_role="COLD_E2E",
            )
        self.assertEqual(
            teardown.exception.reason_code,
            "PERFORMANCE_OUTER_PROCESS_TEARDOWN_INVALID",
        )
        self.assertEqual(teardown_process.join_seconds, [5])

    def test_outer_cleanup_reaps_exact_nested_process_tree(self) -> None:
        context = get_context("spawn")
        receive_connection, send_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=_nested_descendant_target,
            args=(send_connection,),
        )
        child_process_id: int | None = None
        process.start()
        send_connection.close()
        try:
            self.assertTrue(receive_connection.poll(5))
            child_process_id = receive_connection.recv()
            deadline = time.monotonic() + 5
            while (
                child_process_id
                not in performance._descendant_process_ids(process.pid)
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            self.assertIn(
                child_process_id,
                performance._descendant_process_ids(process.pid),
            )

            performance._cleanup_outer_process(process)

            self.assertFalse(process.is_alive())
            deadline = time.monotonic() + 5
            child_running = True
            while child_running and time.monotonic() < deadline:
                completed = subprocess.run(
                    ("/bin/ps", "-o", "stat=", "-p", str(child_process_id)),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                state = completed.stdout.strip()
                child_running = bool(state) and not state.startswith("Z")
                if child_running:
                    time.sleep(0.05)
            self.assertFalse(child_running)
        finally:
            receive_connection.close()
            if process.is_alive():
                process.kill()
                process.join(2)
            if child_process_id is not None:
                try:
                    os.kill(child_process_id, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.pid is not None and not process.is_alive():
                process.close()


if __name__ == "__main__":
    unittest.main()
