"""Frozen CRR P1 performance measurement and evidence coordinator.

The production entry point accepts only one prebuilt native manifest, its
expected build evidence hash, and a new local output directory.  Measurement
counts, order, worker budgets, fixtures, clocks, percentile method, and SLOs
are source constants.  The existing raw-sample schema remains non-claiming;
separate aggregates evaluate latency while the P1 overall status stays closed
until independent E1 and T1 receipts are supplied and verified elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
import gc
from hashlib import sha256
import json
from multiprocessing import get_context
from multiprocessing.connection import Connection
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import threading
import time
from types import MappingProxyType
from typing import Callable, Mapping, Protocol, Sequence

from gld_research_core.crr_batch import _create_exact_64_batch_engine
from gld_research_core.crr_delta import MODEL_SHA256
from gld_research_core.facts import (
    QUOTE_QUALITY_POLICY_SHA256,
    QUOTE_QUALITY_POLICY_VERSION,
)
from gld_research_core.native_crr_delta import create_native_crr_delta_engine
from gld_research_core.native_tree import load_native_tree_v1
from gld_research_core.p1_decision_child import (
    P1DecisionChildIntegrationResultV1,
    create_p1_decision_child_runner,
    encode_p1_decision_request,
)
from gld_research_core.p1_native_decision import (
    NATIVE_EXECUTION_SCHEMA,
    P1_CONTRACT_SHA256,
    REQUEST_BINDING_SCHEMA,
    RESEARCH_CONTRACT_SHA256,
    RUNTIME_SEMANTIC_SCHEMA,
    SHADOW_ARTIFACT_SCHEMA,
)

from .crr_p1_contract import (
    FIXTURE_COUNT,
    RAW_SAMPLE_SCHEMA_VERSION,
    REFERENCE_WORKERS,
    STATIC_MANIFEST_SHA256,
    WARMUP_RUNS,
    LoadedCorpusV1,
    canonical_json_bytes,
    load_corpus,
    load_reference_golden,
    nearest_rank_p95_ns,
    validate_raw_sample_receipt,
)
from .crr_p1_decision_contract import (
    DECISION_STATIC_MANIFEST_SHA256,
    LoadedDecisionSemanticGoldenV1,
    assert_formal_timed_receipt_permitted,
    load_decision_semantic_golden,
    semantic_document_sha256,
)


PERFORMANCE_RUNNER_SCHEMA_VERSION = "GLD_CRR_P1_PERFORMANCE_RUNNER_V1"
PERFORMANCE_WARMUP_SIDECAR_SCHEMA_VERSION = (
    "GLD_CRR_P1_W64_WARMUP_SIDECAR_V1"
)
PERFORMANCE_E2E_SIDECAR_SCHEMA_VERSION = (
    "GLD_CRR_P1_E2E_SEMANTIC_SIDECAR_V1"
)
PERFORMANCE_COLD_SIDECAR_SCHEMA_VERSION = (
    "GLD_CRR_P1_COLD_E2E_SEMANTIC_SIDECAR_V1"
)
PERFORMANCE_RUNNER_AGGREGATE_SCHEMA_VERSION = (
    "GLD_CRR_P1_RUNNER_AGGREGATE_V1"
)
PERFORMANCE_COMPONENT_AGGREGATE_SCHEMA_VERSION = (
    "GLD_CRR_P1_COMPONENT_AGGREGATE_V1"
)
PERFORMANCE_LINEAGE_SCHEMA_VERSION = "GLD_CRR_P1_PERFORMANCE_LINEAGE_V1"
PERFORMANCE_PROCESS_SCHEMA_VERSION = "GLD_CRR_P1_PERFORMANCE_PROCESS_V1"

SINGLE_CALL_SLO_NS = 250_000_000
BATCH_64_SLO_NS = 2_000_000_000
E2E_SLO_NS = 3_000_000_000
_KERNEL_THREADS_PER_WORKER = 1
_RESULT_CACHE_HITS = 0
_QUALIFICATION_STATUS = "P1_PERFORMANCE_NOT_PASS"
_PLUGIN_OUTER_LIFECYCLE_NOT_VERIFIED = "NOT_VERIFIED"
_PLUGIN_OUTER_LIFECYCLE_PERSISTENT_VERIFIED = (
    "VERIFIED_PERSISTENT_OUTER"
)
_PLUGIN_OUTER_LIFECYCLE_FRESH_VERIFIED = (
    "VERIFIED_FRESH_OUTER_PER_INVOCATION"
)
# This production state is source-frozen.  It has no CLI/environment override
# and may change only with separately accepted lifecycle evidence.
_CURRENT_PLUGIN_OUTER_PROCESS_LIFECYCLE = (
    _PLUGIN_OUTER_LIFECYCLE_NOT_VERIFIED
)
_PLUGIN_LIFECYCLE_BLOCKER = "PLUGIN_OUTER_PROCESS_LIFECYCLE_NOT_VERIFIED"
_COLD_E2E_SLO_BLOCKER = "COLD_E2E_SLO_NOT_PASS"
_MAX_PROCESS_PAYLOAD_BYTES = 64 * 1024 * 1024
_FORMAL_RUNNER_OUTER_HARD_LIMIT_SECONDS = 30 * 60
_COLD_OUTER_HARD_LIMIT_SECONDS = 30
_OUTER_TERMINATE_GRACE_SECONDS = 2
_OUTER_TEARDOWN_GRACE_SECONDS = 5
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_ROOT = Path(__file__).resolve().parents[1]


class PerformanceRunError(RuntimeError):
    """One stable fail-closed performance-run reason."""

    def __init__(self, reason_code: str) -> None:
        if (
            type(reason_code) is not str
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", reason_code) is None
        ):
            raise ValueError("invalid performance reason")
        self.reason_code = reason_code
        super().__init__(reason_code)


def _raise_outer_termination(
    signal_number: int,
    frame: object,
) -> None:
    del signal_number, frame
    raise SystemExit("performance outer termination")


def _establish_outer_process_boundary() -> None:
    try:
        os.setsid()
        signal.signal(signal.SIGTERM, _raise_outer_termination)
    except (OSError, RuntimeError, ValueError) as error:
        raise PerformanceRunError(
            "PERFORMANCE_OUTER_PROCESS_BOUNDARY_INVALID"
        ) from error
    process_id = os.getpid()
    if os.getpgrp() != process_id or os.getsid(0) != process_id:
        raise PerformanceRunError(
            "PERFORMANCE_OUTER_PROCESS_BOUNDARY_INVALID"
        )


@dataclass(frozen=True, slots=True)
class _PerformancePlanV1:
    fixture_count: int
    warmup_runs: int
    single_repetitions: int
    batch_samples: int
    e2e_samples: int
    cold_e2e_samples: int
    batch_workers: int

    def __post_init__(self) -> None:
        values = (
            self.fixture_count,
            self.warmup_runs,
            self.single_repetitions,
            self.batch_samples,
            self.e2e_samples,
            self.cold_e2e_samples,
            self.batch_workers,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise PerformanceRunError("PERFORMANCE_PLAN_INVALID")


FORMAL_PERFORMANCE_PLAN = _PerformancePlanV1(
    fixture_count=FIXTURE_COUNT,
    warmup_runs=WARMUP_RUNS,
    single_repetitions=30,
    batch_samples=100,
    e2e_samples=100,
    cold_e2e_samples=30,
    batch_workers=REFERENCE_WORKERS,
)


class _StageDriver(Protocol):
    def run_w64_e2e(self) -> dict[str, object]: ...

    def run_single(self, ordinal: int) -> dict[str, object]: ...

    def run_batch(self) -> dict[str, object]: ...

    def run_u64_e2e(self) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class _MeasuredRunnerV1:
    single_samples_by_contract: tuple[tuple[int, ...], ...]
    single_pooled_samples_ns: tuple[int, ...]
    single_execution_order: tuple[int, ...]
    batch_samples_ns: tuple[int, ...]
    e2e_samples_ns: tuple[int, ...]
    w64_semantic_records: tuple[dict[str, object], ...]
    e2e_semantic_records: tuple[dict[str, object], ...]

    @property
    def single_contract_p95_ns(self) -> tuple[int, ...]:
        return tuple(
            nearest_rank_p95_ns(samples)
            for samples in self.single_samples_by_contract
        )

    @property
    def single_pooled_p95_ns(self) -> int:
        return nearest_rank_p95_ns(self.single_pooled_samples_ns)

    @property
    def batch_p95_ns(self) -> int:
        return nearest_rank_p95_ns(self.batch_samples_ns)

    @property
    def e2e_p95_ns(self) -> int:
        return nearest_rank_p95_ns(self.e2e_samples_ns)


def _require_sha256(value: object, reason_code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise PerformanceRunError(reason_code)
    return value


def _canonical_sha256(value: object) -> str:
    try:
        return sha256(canonical_json_bytes(value)).hexdigest()
    except Exception as error:
        raise PerformanceRunError("PERFORMANCE_CANONICAL_JSON_INVALID") from error


def _compact_canonical_sha256(value: object) -> str:
    """Match the production Decision artifact encoder (no trailing newline)."""

    try:
        raw = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, UnicodeEncodeError, ValueError) as error:
        raise PerformanceRunError("PERFORMANCE_CANONICAL_JSON_INVALID") from error
    return sha256(raw).hexdigest()


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw_json(item) for item in value]
    if type(value) is list:
        return [_thaw_json(item) for item in value]
    return value


def _validate_stage_record(
    record: object,
    *,
    fixture_id: str,
    plan: _PerformancePlanV1,
) -> dict[str, object]:
    if type(record) is not dict:
        raise PerformanceRunError("PERFORMANCE_SEMANTIC_RECORD_INVALID")
    if (
        record.get("fixture_id") != fixture_id
        or (
            record.get("requested"),
            record.get("bound"),
            record.get("started"),
            record.get("terminal"),
        )
        != (plan.fixture_count,) * 4
        or record.get("worker_count") != plan.batch_workers
        or record.get("kernel_threads") != _KERNEL_THREADS_PER_WORKER
        or record.get("cache_hits") != _RESULT_CACHE_HITS
        or type(record.get("semantic_receipt")) is not dict
    ):
        raise PerformanceRunError("PERFORMANCE_SEMANTIC_MISMATCH")
    return record


def _call_stage(
    callback: Callable[[], dict[str, object]],
    *,
    unexpected_reason: str,
) -> dict[str, object]:
    try:
        return callback()
    except PerformanceRunError:
        raise
    except Exception as error:
        raise PerformanceRunError(unexpected_reason) from error


def _positive_duration(start_ns: object, end_ns: object) -> int:
    if (
        type(start_ns) is not int
        or type(end_ns) is not int
        or end_ns <= start_ns
    ):
        raise PerformanceRunError("PERFORMANCE_CLOCK_INVALID")
    return end_ns - start_ns


def _execute_measurement_plan(
    *,
    plan: _PerformancePlanV1,
    driver: _StageDriver,
    clock_ns: Callable[[], int],
) -> _MeasuredRunnerV1:
    warmup_records: list[dict[str, object]] = []
    for _ in range(plan.warmup_runs):
        record = _call_stage(
            driver.run_w64_e2e,
            unexpected_reason="PERFORMANCE_W64_WARMUP_FAILED",
        )
        warmup_records.append(
            _validate_stage_record(record, fixture_id="W64", plan=plan)
        )

    samples_by_contract: list[list[int]] = [
        [] for _ in range(plan.fixture_count)
    ]
    pooled: list[int] = []
    execution_order: list[int] = []
    for _ in range(plan.single_repetitions):
        for ordinal in range(1, plan.fixture_count + 1):
            try:
                started_ns = clock_ns()
                record = driver.run_single(ordinal)
                if (
                    type(record) is not dict
                    or record.get("ordinal") != ordinal
                    or type(record.get("terminal")) is not dict
                    or record["terminal"].get("ordinal") != ordinal
                ):
                    raise PerformanceRunError(
                        "PERFORMANCE_SINGLE_SEMANTIC_MISMATCH"
                    )
                completed_ns = clock_ns()
            except PerformanceRunError:
                raise
            except Exception as error:
                raise PerformanceRunError(
                    "PERFORMANCE_SINGLE_EXECUTION_FAILED"
                ) from error
            duration_ns = _positive_duration(started_ns, completed_ns)
            samples_by_contract[ordinal - 1].append(duration_ns)
            pooled.append(duration_ns)
            execution_order.append(ordinal)

    batch_samples: list[int] = []
    for _ in range(plan.batch_samples):
        try:
            started_ns = clock_ns()
            record = driver.run_batch()
            if type(record) is not dict or (
                record.get("fixture_id"),
                record.get("requested"),
                record.get("bound"),
                record.get("started"),
                record.get("terminal"),
                record.get("worker_count"),
                record.get("kernel_threads"),
                record.get("cache_hits"),
            ) != (
                "U64",
                plan.fixture_count,
                plan.fixture_count,
                plan.fixture_count,
                plan.fixture_count,
                plan.batch_workers,
                _KERNEL_THREADS_PER_WORKER,
                _RESULT_CACHE_HITS,
            ):
                raise PerformanceRunError(
                    "PERFORMANCE_BATCH_SEMANTIC_MISMATCH"
                )
            completed_ns = clock_ns()
        except PerformanceRunError:
            raise
        except Exception as error:
            raise PerformanceRunError(
                "PERFORMANCE_BATCH_EXECUTION_FAILED"
            ) from error
        batch_samples.append(_positive_duration(started_ns, completed_ns))

    e2e_samples: list[int] = []
    e2e_records: list[dict[str, object]] = []
    for _ in range(plan.e2e_samples):
        try:
            started_ns = clock_ns()
            record = driver.run_u64_e2e()
            validated = _validate_stage_record(
                record,
                fixture_id="U64",
                plan=plan,
            )
            completed_ns = clock_ns()
        except PerformanceRunError:
            raise
        except Exception as error:
            raise PerformanceRunError(
                "PERFORMANCE_E2E_EXECUTION_FAILED"
            ) from error
        e2e_samples.append(_positive_duration(started_ns, completed_ns))
        e2e_records.append(validated)

    expected_order = tuple(range(1, plan.fixture_count + 1)) * (
        plan.single_repetitions
    )
    if (
        tuple(execution_order) != expected_order
        or any(
            len(samples) != plan.single_repetitions
            for samples in samples_by_contract
        )
        or len(pooled) != plan.fixture_count * plan.single_repetitions
        or len(batch_samples) != plan.batch_samples
        or len(e2e_samples) != plan.e2e_samples
        or len(warmup_records) != plan.warmup_runs
        or len(e2e_records) != plan.e2e_samples
    ):
        raise PerformanceRunError("PERFORMANCE_SAMPLE_COUNT_INVALID")

    return _MeasuredRunnerV1(
        single_samples_by_contract=tuple(
            tuple(samples) for samples in samples_by_contract
        ),
        single_pooled_samples_ns=tuple(pooled),
        single_execution_order=tuple(execution_order),
        batch_samples_ns=tuple(batch_samples),
        e2e_samples_ns=tuple(e2e_samples),
        w64_semantic_records=tuple(warmup_records),
        e2e_semantic_records=tuple(e2e_records),
    )


def _execute_measurement_plan_for_test(
    *,
    plan: _PerformancePlanV1,
    driver: _StageDriver,
    clock_ns: Callable[[], int],
) -> _MeasuredRunnerV1:
    """Exercise the sequencing core with a deliberately small test plan."""

    if plan == FORMAL_PERFORMANCE_PLAN:
        raise PerformanceRunError("PERFORMANCE_TEST_PLAN_MUST_BE_SMALL")
    return _execute_measurement_plan(plan=plan, driver=driver, clock_ns=clock_ns)


def _build_raw_receipt(
    *,
    stage: str,
    runner_process_ordinal: int,
    samples_ns: Sequence[int],
    backend_evidence_sha256: str,
    corpus: LoadedCorpusV1,
    golden: Mapping[str, object],
    contract_ordinal: int | None = None,
    call_terminal_semantic: Mapping[str, object] | None = None,
) -> dict[str, object]:
    backend_hash = _require_sha256(
        backend_evidence_sha256,
        "PERFORMANCE_BACKEND_EVIDENCE_INVALID",
    )
    if stage == "SINGLE_CALL":
        expected_calls = 1
        worker_count = 1
        if (
            type(contract_ordinal) is not int
            or not 1 <= contract_ordinal <= FIXTURE_COUNT
            or call_terminal_semantic is None
        ):
            raise PerformanceRunError("PERFORMANCE_RAW_RECEIPT_INVALID")
        call_terminal = dict(call_terminal_semantic)
        call_input_sha256: str | None = _require_sha256(
            call_terminal.get("input_sha256"),
            "PERFORMANCE_RAW_RECEIPT_INVALID",
        )
    else:
        expected_calls = FIXTURE_COUNT
        worker_count = REFERENCE_WORKERS
        if contract_ordinal is not None or call_terminal_semantic is not None:
            raise PerformanceRunError("PERFORMANCE_RAW_RECEIPT_INVALID")
        call_terminal = None
        call_input_sha256 = None
    samples = tuple(samples_ns)
    receipt: dict[str, object] = {
        "backend_evidence_sha256": backend_hash,
        "bound_calls_per_sample": expected_calls,
        "call_input_sha256": call_input_sha256,
        "call_terminal_semantic": call_terminal,
        "clock": "perf_counter_ns",
        "contract_ordinal": contract_ordinal,
        "exceptions_count": 0,
        "fixture_id": "U64",
        "fixture_file_sha256": corpus.fixture_sha256,
        "gc_policy": (
            "ENABLED"
            if gc.isenabled()
            else "DISABLED_DURING_TIMED_SAMPLE"
        ),
        "input_vector_sha256": corpus.input_vector_sha256,
        "kernel_threads_per_worker": _KERNEL_THREADS_PER_WORKER,
        "outliers_removed": False,
        "p95_method": "NEAREST_RANK_CEIL_0_95_N",
        "reported_p95_ns": nearest_rank_p95_ns(samples),
        "requested_calls_per_sample": expected_calls,
        "result_cache_hits": _RESULT_CACHE_HITS,
        "runner_process_ordinal": runner_process_ordinal,
        "samples_ns": list(samples),
        "schema_version": RAW_SAMPLE_SCHEMA_VERSION,
        "semantic_mismatch_count": 0,
        "semantic_output_sha256": golden["semantic_output_sha256"],
        "slo_pass_claimed": False,
        "started_calls_per_sample": expected_calls,
        "stage": stage,
        "terminal_calls_per_sample": expected_calls,
        "terminal_count_mismatch_count": 0,
        "timer_overhead_subtracted": False,
        "worker_count": worker_count,
    }
    try:
        validated_p95 = validate_raw_sample_receipt(receipt)
    except Exception as error:
        raise PerformanceRunError("PERFORMANCE_RAW_RECEIPT_INVALID") from error
    if validated_p95 != receipt["reported_p95_ns"]:
        raise PerformanceRunError("PERFORMANCE_RAW_RECEIPT_INVALID")
    return receipt


def _build_formal_raw_receipt_for_test(**kwargs: object) -> dict[str, object]:
    return _build_raw_receipt(**kwargs)  # type: ignore[arg-type]


def _evaluate_runner_latency(
    *,
    single_contract_p95_ns: Sequence[int],
    batch_p95_ns: int,
    e2e_p95_ns: int,
) -> dict[str, object]:
    values = tuple(single_contract_p95_ns)
    if (
        not values
        or any(type(item) is not int or item <= 0 for item in values)
        or type(batch_p95_ns) is not int
        or batch_p95_ns <= 0
        or type(e2e_p95_ns) is not int
        or e2e_p95_ns <= 0
    ):
        raise PerformanceRunError("PERFORMANCE_AGGREGATE_INVALID")
    single_gate_value = max(values)
    component_pass = {
        "SINGLE_CALL": single_gate_value <= SINGLE_CALL_SLO_NS,
        "BATCH_64": batch_p95_ns <= BATCH_64_SLO_NS,
        "E2E": e2e_p95_ns <= E2E_SLO_NS,
    }
    failed = [name for name, passed in component_pass.items() if not passed]
    return {
        "batch_64_slo_ns": BATCH_64_SLO_NS,
        "batch_gate_pass": component_pass["BATCH_64"],
        "batch_p95_ns": batch_p95_ns,
        "e2e_gate_pass": component_pass["E2E"],
        "e2e_p95_ns": e2e_p95_ns,
        "e2e_slo_ns": E2E_SLO_NS,
        "failed_components": failed,
        "runner_latency_gate_pass": not failed,
        "single_call_slo_ns": SINGLE_CALL_SLO_NS,
        "single_gate_pass": component_pass["SINGLE_CALL"],
        "single_gate_value_ns": single_gate_value,
    }


def _evaluate_runner_latency_for_test(**kwargs: object) -> dict[str, object]:
    return _evaluate_runner_latency(**kwargs)  # type: ignore[arg-type]


def _evaluate_cold_gate(
    *,
    cold_p95_ns: int,
    plugin_outer_process_lifecycle: str,
) -> dict[str, object]:
    if type(cold_p95_ns) is not int or cold_p95_ns <= 0:
        raise PerformanceRunError("PERFORMANCE_COLD_GATE_INVALID")
    conditional_pass = cold_p95_ns <= E2E_SLO_NS
    if (
        plugin_outer_process_lifecycle
        == _PLUGIN_OUTER_LIFECYCLE_NOT_VERIFIED
    ):
        return {
            "conditional_gate_pass": conditional_pass,
            "gate_applied": False,
            "gate_pass": False,
            "gate_policy": "BLOCK_UNTIL_LIFECYCLE_VERIFIED",
            "lifecycle_verified": False,
            "plugin_outer_process_lifecycle": (
                _PLUGIN_OUTER_LIFECYCLE_NOT_VERIFIED
            ),
            "promotion_blocker": _PLUGIN_LIFECYCLE_BLOCKER,
            "report_only": False,
        }
    if (
        plugin_outer_process_lifecycle
        == _PLUGIN_OUTER_LIFECYCLE_PERSISTENT_VERIFIED
    ):
        return {
            "conditional_gate_pass": conditional_pass,
            "gate_applied": False,
            "gate_pass": None,
            "gate_policy": "REPORT_ONLY_VERIFIED_PERSISTENT_OUTER",
            "lifecycle_verified": True,
            "plugin_outer_process_lifecycle": (
                _PLUGIN_OUTER_LIFECYCLE_PERSISTENT_VERIFIED
            ),
            "promotion_blocker": None,
            "report_only": True,
        }
    if (
        plugin_outer_process_lifecycle
        == _PLUGIN_OUTER_LIFECYCLE_FRESH_VERIFIED
    ):
        return {
            "conditional_gate_pass": conditional_pass,
            "gate_applied": True,
            "gate_pass": conditional_pass,
            "gate_policy": (
                "FORMAL_GATE_VERIFIED_FRESH_OUTER_PER_INVOCATION"
            ),
            "lifecycle_verified": True,
            "plugin_outer_process_lifecycle": (
                _PLUGIN_OUTER_LIFECYCLE_FRESH_VERIFIED
            ),
            "promotion_blocker": (
                None if conditional_pass else _COLD_E2E_SLO_BLOCKER
            ),
            "report_only": False,
        }
    raise PerformanceRunError("PERFORMANCE_PLUGIN_LIFECYCLE_INVALID")


def _evaluate_cold_gate_for_test(**kwargs: object) -> dict[str, object]:
    return _evaluate_cold_gate(**kwargs)  # type: ignore[arg-type]


def _terminal_from_native_result(
    ordinal: int,
    inputs: object,
    result: object,
) -> dict[str, object]:
    return {
        "coarse_delta_ppm": result.coarse_delta_ppm,
        "coarse_early_exercise_nodes": result.coarse_early_exercise_nodes,
        "coarse_iv_ppm": result.coarse_iv_ppm,
        "coarse_price_residual_nano_usd": (
            result.coarse_price_residual_nano_usd
        ),
        "contract_id": inputs.contract_id,
        "delta_ppm": result.delta_ppm,
        "early_exercise_detected": bool(
            result.coarse_early_exercise_nodes
            or result.fine_early_exercise_nodes
        ),
        "fine_delta_ppm": result.fine_delta_ppm,
        "fine_early_exercise_nodes": result.fine_early_exercise_nodes,
        "fine_iv_ppm": result.fine_iv_ppm,
        "fine_price_residual_nano_usd": result.fine_price_residual_nano_usd,
        "input_sha256": result.input_sha256,
        "iv_ppm": result.iv_ppm,
        "ordinal": ordinal,
        "reason_code": "PASS",
        "terminal_status": "PASS",
    }


def _typed_request_kwargs(corpus: LoadedCorpusV1) -> dict[str, object]:
    return {
        "candidate_snapshots": (corpus.snapshot,),
        "fees": corpus.fees,
        "lc0_binding": corpus.lc0_binding,
        "p1_contract_sha256": P1_CONTRACT_SHA256,
        "pit_inputs": corpus.pit_inputs,
        "quantity": 1,
        "research_contract_sha256": RESEARCH_CONTRACT_SHA256,
        "rule_package_version": corpus.signal.rule_version,
        "signal": corpus.signal,
    }


def _expected_request_binding(
    corpus: LoadedCorpusV1,
    expected_decision_document: Mapping[str, object],
) -> dict[str, object]:
    binding = {
        "call_input_vector_sha256": corpus.input_vector_sha256,
        "candidate_count": 1,
        "candidate_ledger_sha256": expected_decision_document["lineage"][
            "candidate_ledger_sha256"
        ],
        "delta_model_sha256": MODEL_SHA256,
        "fee_schedule_sha256": corpus.fees.fee_schedule_sha256,
        "lc0_binding_sha256": corpus.lc0_binding.binding_sha256,
        "p1_contract_sha256": P1_CONTRACT_SHA256,
        "pit_inputs_sha256": corpus.pit_inputs.pit_inputs_sha256,
        "quantity": 1,
        "quote_quality_policy_sha256": QUOTE_QUALITY_POLICY_SHA256,
        "quote_quality_policy_version": QUOTE_QUALITY_POLICY_VERSION,
        "research_contract_sha256": RESEARCH_CONTRACT_SHA256,
        "rule_package_version": corpus.signal.rule_version,
        "rule_sha256": corpus.signal.rule_sha256,
        "schema_version": REQUEST_BINDING_SCHEMA,
        "selected_candidate_ordinal": 1,
        "selected_snapshot_sha256": corpus.snapshot.snapshot_sha256,
        "signal_snapshot_sha256": corpus.signal.snapshot_sha256,
    }
    lineage = expected_decision_document["lineage"]
    expected_from_golden = {
        "call_input_vector_sha256": lineage["input_vector_sha256"],
        "candidate_ledger_sha256": lineage["candidate_ledger_sha256"],
        "delta_model_sha256": lineage["delta_model_sha256"],
        "fee_schedule_sha256": lineage["fee_schedule_sha256"],
        "lc0_binding_sha256": lineage["lc0_binding_sha256"],
        "p1_contract_sha256": lineage["p1_contract_sha256"],
        "quote_quality_policy_sha256": lineage[
            "quote_quality_policy_sha256"
        ],
        "quote_quality_policy_version": lineage[
            "quote_quality_policy_version"
        ],
        "research_contract_sha256": lineage["research_contract_sha256"],
        "rule_package_version": lineage["rule_package_version"],
        "rule_sha256": lineage["rule_sha256"],
        "selected_snapshot_sha256": lineage["option_snapshot_sha256"],
        "signal_snapshot_sha256": lineage["signal_snapshot_sha256"],
    }
    if any(binding.get(key) != value for key, value in expected_from_golden.items()):
        raise PerformanceRunError("PERFORMANCE_REQUEST_BINDING_INVALID")
    return binding


def _validate_actual_decision_semantic(
    semantic: object,
    *,
    expected_decision_document: Mapping[str, object],
    expected_request_binding: Mapping[str, object],
    expected_combined_backend_sha256: str,
) -> str:
    if type(semantic) is not dict:
        raise PerformanceRunError("PERFORMANCE_E2E_SEMANTIC_MISMATCH")
    request_binding = semantic.get("request_binding")
    components = semantic.get("backend_evidence_components")
    if type(request_binding) is not dict or type(components) is not dict:
        raise PerformanceRunError("PERFORMANCE_E2E_SEMANTIC_MISMATCH")
    request_sha256 = _canonical_sha256(request_binding)
    runtime_semantic_sha256 = _canonical_sha256(
        {
            "call_semantic_output_sha256": semantic.get(
                "call_semantic_output_sha256"
            ),
            "projection_sha256": semantic.get("projection_sha256"),
            "request_binding": request_binding,
            "schema_version": RUNTIME_SEMANTIC_SCHEMA,
        }
    )
    shadow_artifact_sha256 = _canonical_sha256(
        {
            "actionable": False,
            "broker_order_count": 0,
            "call_semantic_output_sha256": semantic.get(
                "call_semantic_output_sha256"
            ),
            "call_terminals": semantic.get("call_terminals"),
            "decision_projection": semantic.get("decision_projection"),
            "projection_sha256": semantic.get("projection_sha256"),
            "request_binding": request_binding,
            "runtime_semantic_sha256": runtime_semantic_sha256,
            "schema_version": SHADOW_ARTIFACT_SCHEMA,
        }
    )
    batch_provenance_sha256 = semantic.get(
        "batch_execution_provenance_sha256"
    )
    expected_execution_sha256 = _canonical_sha256(
        {
            "backend_evidence_sha256": expected_combined_backend_sha256,
            "batch_execution_provenance_sha256": batch_provenance_sha256,
            "batch_size": FIXTURE_COUNT,
            "cache_hits": _RESULT_CACHE_HITS,
            "kernel_threads": _KERNEL_THREADS_PER_WORKER,
            "request_sha256": request_sha256,
            "runtime_semantic_sha256": runtime_semantic_sha256,
            "schema_version": NATIVE_EXECUTION_SCHEMA,
            "shadow_artifact_sha256": shadow_artifact_sha256,
            "worker_count": REFERENCE_WORKERS,
        }
    )
    actual_frozen_document = dict(expected_decision_document)
    actual_frozen_document["call_semantic_output_sha256"] = semantic.get(
        "call_semantic_output_sha256"
    )
    actual_frozen_document["call_terminals"] = semantic.get("call_terminals")
    actual_frozen_document["decision_projection"] = semantic.get(
        "decision_projection"
    )
    reconstructed_decision_semantic_sha256 = semantic_document_sha256(
        actual_frozen_document
    )
    if (
        request_binding != expected_request_binding
        or semantic.get("request_sha256") != request_sha256
        or semantic.get("runtime_semantic_sha256")
        != runtime_semantic_sha256
        or semantic.get("shadow_artifact_sha256")
        != shadow_artifact_sha256
        or semantic.get("call_terminals")
        != expected_decision_document["call_terminals"]
        or semantic.get("call_semantic_output_sha256")
        != expected_decision_document["call_semantic_output_sha256"]
        or semantic.get("decision_projection")
        != expected_decision_document["decision_projection"]
        or semantic.get("projection_sha256")
        != _canonical_sha256(expected_decision_document["decision_projection"])
        or reconstructed_decision_semantic_sha256
        != expected_decision_document["decision_semantic_sha256"]
        or components.get("backend_evidence_sha256")
        != expected_combined_backend_sha256
        or components.get("input_sha256") != request_sha256
        or components.get("artifact_sha256") != shadow_artifact_sha256
        or components.get("execution_sha256") != expected_execution_sha256
        or semantic.get("actionable") is not False
        or semantic.get("broker_order_count") != 0
    ):
        raise PerformanceRunError("PERFORMANCE_E2E_SEMANTIC_MISMATCH")
    for value in (
        batch_provenance_sha256,
        semantic.get("runtime_semantic_sha256"),
        semantic.get("shadow_artifact_sha256"),
        components.get("execution_sha256"),
    ):
        _require_sha256(value, "PERFORMANCE_E2E_SEMANTIC_MISMATCH")
    return reconstructed_decision_semantic_sha256


class _RealStageDriver:
    def __init__(
        self,
        *,
        u64: LoadedCorpusV1,
        w64: LoadedCorpusV1,
        u64_golden: Mapping[str, object],
        w64_golden: Mapping[str, object],
        u64_decision_golden: LoadedDecisionSemanticGoldenV1,
        w64_decision_golden: LoadedDecisionSemanticGoldenV1,
        compute_call: Callable[..., object],
        verify_call: Callable[[object], bool],
        run_batch: Callable[[tuple[object, ...]], object],
        verify_batch: Callable[[object], bool],
        decision_runner: object,
        combined_backend_evidence_sha256: str,
        native_build_backend_evidence_sha256: str,
    ) -> None:
        self.u64 = u64
        self.w64 = w64
        self.u64_golden = u64_golden
        self.w64_golden = w64_golden
        self.u64_decision = _thaw_json(u64_decision_golden.document)
        self.w64_decision = _thaw_json(w64_decision_golden.document)
        self._u64_request_binding = _expected_request_binding(
            u64,
            self.u64_decision,
        )
        self._w64_request_binding = _expected_request_binding(
            w64,
            self.w64_decision,
        )
        self.compute_call = compute_call
        self.verify_call = verify_call
        self.run_batch_call = run_batch
        self.verify_batch = verify_batch
        self.decision_runner = decision_runner
        self.combined_backend_evidence_sha256 = _require_sha256(
            combined_backend_evidence_sha256,
            "PERFORMANCE_BACKEND_EVIDENCE_INVALID",
        )
        self.native_build_backend_evidence_sha256 = _require_sha256(
            native_build_backend_evidence_sha256,
            "PERFORMANCE_BACKEND_EVIDENCE_INVALID",
        )
        self._u64_kwargs = _typed_request_kwargs(u64)
        self._w64_kwargs = _typed_request_kwargs(w64)
        self._u64_request = encode_p1_decision_request(**self._u64_kwargs)
        self._w64_request = encode_p1_decision_request(**self._w64_kwargs)

    def run_single(self, ordinal: int) -> dict[str, object]:
        inputs = self.u64.bound_inputs[ordinal - 1]
        result = self.compute_call(inputs, expected_model_sha256=MODEL_SHA256)
        if self.verify_call(result) is not True:
            raise PerformanceRunError("PERFORMANCE_SINGLE_RESULT_UNVERIFIED")
        terminal = _terminal_from_native_result(ordinal, inputs, result)
        expected = self.u64_golden["results"][ordinal - 1]
        if (
            terminal != expected
            or result.combined_backend_evidence_sha256
            != self.combined_backend_evidence_sha256
        ):
            raise PerformanceRunError("PERFORMANCE_SINGLE_SEMANTIC_MISMATCH")
        return {"ordinal": ordinal, "terminal": terminal}

    def run_batch(self) -> dict[str, object]:
        receipt = self.run_batch_call(self.u64.bound_inputs)
        actual = tuple(item.as_dict() for item in receipt.terminals)
        if (
            self.verify_batch(receipt) is not True
            or actual != tuple(self.u64_golden["results"])
            or receipt.semantic_output_sha256
            != self.u64_golden["semantic_output_sha256"]
            or receipt.backend_evidence_sha256
            != self.combined_backend_evidence_sha256
        ):
            raise PerformanceRunError("PERFORMANCE_BATCH_SEMANTIC_MISMATCH")
        return {
            "bound": receipt.bound,
            "cache_hits": receipt.cache_hits,
            "fixture_id": "U64",
            "kernel_threads": receipt.kernel_threads,
            "requested": receipt.requested,
            "semantic_output_sha256": receipt.semantic_output_sha256,
            "started": receipt.started,
            "terminal": receipt.terminal,
            "worker_count": receipt.worker_count,
        }

    def _run_decision(self, fixture_id: str) -> dict[str, object]:
        if fixture_id == "U64":
            kwargs = self._u64_kwargs
            request_bytes, request_sha256 = self._u64_request
            expected = self.u64_decision
            expected_request_binding = self._u64_request_binding
        elif fixture_id == "W64":
            kwargs = self._w64_kwargs
            request_bytes, request_sha256 = self._w64_request
            expected = self.w64_decision
            expected_request_binding = self._w64_request_binding
        else:
            raise PerformanceRunError("PERFORMANCE_FIXTURE_INVALID")
        runner = self.decision_runner
        result = runner.run_typed(**kwargs)
        if (
            type(result) is not P1DecisionChildIntegrationResultV1
            or runner.is_verified_result(
                result,
                request_bytes,
                request_sha256,
            )
            is not True
            or result.status != "SUCCESS"
            or result.semantic_receipt is None
            or result.artifact is None
            or result.artifact_sha256 is None
            or result.actionable is not False
            or result.broker_order_count != 0
        ):
            raise PerformanceRunError("PERFORMANCE_E2E_RESULT_UNVERIFIED")
        semantic = result.semantic_receipt.as_dict()
        artifact = result.artifact.as_dict()
        reconstructed_decision_semantic_sha256 = (
            _validate_actual_decision_semantic(
                semantic,
                expected_decision_document=expected,
                expected_request_binding=expected_request_binding,
                expected_combined_backend_sha256=(
                    self.combined_backend_evidence_sha256
                ),
            )
        )
        if (
            semantic.get("requested") != FIXTURE_COUNT
            or semantic.get("bound") != FIXTURE_COUNT
            or semantic.get("started") != FIXTURE_COUNT
            or semantic.get("terminal") != FIXTURE_COUNT
            or semantic.get("worker_count") != REFERENCE_WORKERS
            or semantic.get("kernel_threads") != _KERNEL_THREADS_PER_WORKER
            or semantic.get("cache_hits") != _RESULT_CACHE_HITS
            or semantic.get("call_terminals") != expected["call_terminals"]
            or semantic.get("call_semantic_output_sha256")
            != expected["call_semantic_output_sha256"]
            or semantic.get("decision_projection")
            != expected["decision_projection"]
            or semantic.get("projection_sha256")
            != _canonical_sha256(expected["decision_projection"])
            or artifact.get("native_build_backend_evidence_sha256")
            != self.native_build_backend_evidence_sha256
            or artifact.get("combined_backend_evidence_sha256")
            != self.combined_backend_evidence_sha256
            or artifact.get("actionable") is not False
            or artifact.get("broker_order_count") != 0
            or result.artifact_sha256 != _compact_canonical_sha256(artifact)
        ):
            raise PerformanceRunError("PERFORMANCE_E2E_SEMANTIC_MISMATCH")
        return {
            "artifact_sha256": result.artifact_sha256,
            "bound": semantic["bound"],
            "cache_hits": semantic["cache_hits"],
            "decision_request_sha256": request_sha256,
            "decision_semantic_golden_sha256": expected[
                "decision_semantic_sha256"
            ],
            "fixture_id": fixture_id,
            "kernel_threads": semantic["kernel_threads"],
            "projection_sha256": semantic["projection_sha256"],
            "reconstructed_decision_semantic_sha256": (
                reconstructed_decision_semantic_sha256
            ),
            "requested": semantic["requested"],
            "runtime_semantic_sha256": semantic["runtime_semantic_sha256"],
            "semantic_receipt": semantic,
            "semantic_receipt_sha256": _compact_canonical_sha256(semantic),
            "started": semantic["started"],
            "supervisor_generation_token": result.supervisor_generation_token,
            "supervisor_request_sha256": result.supervisor_request_sha256,
            "terminal": semantic["terminal"],
            "worker_count": semantic["worker_count"],
        }

    def run_w64_e2e(self) -> dict[str, object]:
        return self._run_decision("W64")

    def run_u64_e2e(self) -> dict[str, object]:
        return self._run_decision("U64")


def _read_manifest_document(path: Path) -> tuple[bytes, dict[str, object]]:
    try:
        raw = path.resolve(strict=True).read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PerformanceRunError("PERFORMANCE_NATIVE_MANIFEST_INVALID") from error
    if type(document) is not dict or raw != canonical_json_bytes(document):
        raise PerformanceRunError("PERFORMANCE_NATIVE_MANIFEST_INVALID")
    return raw, document


def _backend_lineage(
    manifest_raw: bytes,
    manifest: Mapping[str, object],
    *,
    expected_build_evidence_sha256: str,
    combined_backend_evidence_sha256: str,
) -> dict[str, object]:
    selected_fields = (
        "abi_id",
        "abi_version",
        "backend_evidence_sha256",
        "binary_filename",
        "binary_sha256",
        "binary_size",
        "build_input_sha256",
        "build_manifest_sha256",
        "compile_command_sha256",
        "compiler_executable",
        "compiler_executable_sha256",
        "compiler_flags",
        "compiler_flags_sha256",
        "compiler_version_sha256",
        "dependency_manifest_sha256",
        "otool_dependency_sha256",
        "python_header_manifest_sha256",
        "runtime_environment_sha256",
        "schema_version",
        "source_manifest_sha256",
        "source_sha256",
        "target_environment_sha256",
    )
    values = {name: manifest.get(name) for name in selected_fields}
    if (
        manifest.get("backend_evidence_sha256")
        != expected_build_evidence_sha256
    ):
        raise PerformanceRunError("PERFORMANCE_BACKEND_EVIDENCE_MISMATCH")
    return {
        "combined_backend_evidence_sha256": _require_sha256(
            combined_backend_evidence_sha256,
            "PERFORMANCE_BACKEND_EVIDENCE_INVALID",
        ),
        "expected_native_build_backend_evidence_sha256": (
            expected_build_evidence_sha256
        ),
        "manifest_fields": values,
        "native_manifest_sha256": sha256(manifest_raw).hexdigest(),
    }


def _file_sha256(path: Path) -> str:
    try:
        return sha256(path.resolve(strict=True).read_bytes()).hexdigest()
    except OSError as error:
        raise PerformanceRunError("PERFORMANCE_SOURCE_BINDING_INVALID") from error


def _source_lineage() -> dict[str, object]:
    relative_paths = (
        "2026-08-28-1515-gld-research-contract-v0.2.md",
        "2026-08-28-2046-gld-crr-p1-benchmark-contract-v0.1.md",
        "benchmarks/__init__.py",
        "benchmarks/crr_p1_contract.py",
        "benchmarks/crr_p1_decision_contract.py",
        "benchmarks/crr_p1_performance.py",
        "native/crr_tree_kernel_v1.c",
        "pyproject.toml",
        "src/gld_normalizer/__init__.py",
        "src/gld_normalizer/canonical.py",
        "src/gld_normalizer/errors.py",
        "src/gld_normalizer/readers.py",
        "src/gld_normalizer/time_semantics.py",
        "src/gld_research_core/__init__.py",
        "src/gld_research_core/bcs.py",
        "src/gld_research_core/crr_batch.py",
        "src/gld_research_core/crr_delta.py",
        "src/gld_research_core/crr_input_binding.py",
        "src/gld_research_core/facts.py",
        "src/gld_research_core/native_crr_delta.py",
        "src/gld_research_core/native_tree.py",
        "src/gld_research_core/p1_decision_child.py",
        "src/gld_research_core/p1_native_decision.py",
        "src/gld_research_core/p1_process_supervisor.py",
        "tools/build_crr_native.py",
        "tools/run_crr_p1_performance.py",
    )
    files = {path: _file_sha256(_ROOT / path) for path in relative_paths}
    return {
        "files": files,
        "source_set_sha256": _canonical_sha256(files),
    }


def _fixture_lineage(
    u64: LoadedCorpusV1,
    w64: LoadedCorpusV1,
    u64_golden: Mapping[str, object],
    w64_golden: Mapping[str, object],
    u64_decision: LoadedDecisionSemanticGoldenV1,
    w64_decision: LoadedDecisionSemanticGoldenV1,
) -> dict[str, object]:
    return {
        "decision_static_manifest_sha256": DECISION_STATIC_MANIFEST_SHA256,
        "static_manifest_sha256": STATIC_MANIFEST_SHA256,
        "u64": {
            "call_semantic_output_sha256": u64_golden[
                "semantic_output_sha256"
            ],
            "decision_projection_sha256": u64_decision.projection_sha256,
            "decision_semantic_sha256": (
                u64_decision.decision_semantic_sha256
            ),
            "fixture_file_sha256": u64.fixture_sha256,
            "input_vector_sha256": u64.input_vector_sha256,
        },
        "w64": {
            "call_semantic_output_sha256": w64_golden[
                "semantic_output_sha256"
            ],
            "decision_projection_sha256": w64_decision.projection_sha256,
            "decision_semantic_sha256": (
                w64_decision.decision_semantic_sha256
            ),
            "fixture_file_sha256": w64.fixture_sha256,
            "input_vector_sha256": w64.input_vector_sha256,
        },
    }


def _read_command(args: tuple[str, ...]) -> str | None:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value if value else None


_ACTIVE_POWER_SOURCE_RE = re.compile(
    r"Now drawing from '(AC Power|Battery Power)'\Z",
    re.ASCII,
)
_POWER_PROFILE_SECTION_RE = re.compile(
    r"(AC Power|Battery Power):[ \t]*\Z",
    re.ASCII,
)
_ANY_PROFILE_SECTION_RE = re.compile(r"[^ \t].*:[ \t]*\Z", re.ASCII)
_LOW_POWER_MODE_RE = re.compile(
    r"[ \t]+lowpowermode[ \t]+([01])[ \t]*\Z",
    re.ASCII,
)


def _parse_active_power_source(power_status: object) -> str | None:
    if type(power_status) is not str or not power_status:
        return None
    declarations = [
        line.strip()
        for line in power_status.splitlines()
        if line.strip().startswith("Now drawing from")
    ]
    if len(declarations) != 1:
        return None
    match = _ACTIVE_POWER_SOURCE_RE.fullmatch(declarations[0])
    return match.group(1) if match is not None else None


def _parse_active_low_power_mode(
    custom_profiles: object,
    *,
    active_power_source: object,
) -> int | None:
    if (
        type(custom_profiles) is not str
        or not custom_profiles
        or active_power_source not in {"AC Power", "Battery Power"}
    ):
        return None
    lines = custom_profiles.splitlines()
    matching_sections = [
        index
        for index, line in enumerate(lines)
        if (
            (match := _POWER_PROFILE_SECTION_RE.fullmatch(line)) is not None
            and match.group(1) == active_power_source
        )
    ]
    if len(matching_sections) != 1:
        return None
    start = matching_sections[0] + 1
    end = len(lines)
    for index in range(start, len(lines)):
        if _ANY_PROFILE_SECTION_RE.fullmatch(lines[index]) is not None:
            end = index
            break
    values: list[int] = []
    for line in lines[start:end]:
        if "lowpowermode" not in line:
            continue
        match = _LOW_POWER_MODE_RE.fullmatch(line)
        if match is None:
            return None
        values.append(int(match.group(1)))
    return values[0] if len(values) == 1 else None


def _machine_receipt() -> dict[str, object]:
    brand = _read_command(("/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"))
    build = _read_command(("/usr/sbin/sysctl", "-n", "kern.osversion"))
    physical = _read_command(("/usr/sbin/sysctl", "-n", "hw.physicalcpu"))
    logical = _read_command(("/usr/sbin/sysctl", "-n", "hw.logicalcpu"))
    memory = _read_command(("/usr/sbin/sysctl", "-n", "hw.memsize"))
    power = _read_command(("/usr/bin/pmset", "-g", "batt"))
    power_settings = _read_command(("/usr/bin/pmset", "-g", "custom"))
    thermal = _read_command(("/usr/bin/pmset", "-g", "therm"))
    memory_bytes: int | None
    try:
        memory_bytes = int(memory) if memory is not None else None
    except ValueError:
        memory_bytes = None
    active_power_source = _parse_active_power_source(power)
    active_low_power_mode = _parse_active_low_power_mode(
        power_settings,
        active_power_source=active_power_source,
    )
    active_power_profile_valid = (
        active_power_source in {"AC Power", "Battery Power"}
        and type(active_low_power_mode) is int
        and active_low_power_mode in {0, 1}
    )
    ac_power = active_power_source == "AC Power"
    low_power_off = active_low_power_mode == 0
    thermal_nominal = thermal is not None and "No thermal warning level" in thermal
    machine = {
        "ac_power": ac_power,
        "active_low_power_mode": active_low_power_mode,
        "active_power_source": active_power_source,
        "architecture": platform.machine(),
        "chip_model": brand or platform.processor(),
        "logical_cpu_count": int(logical) if logical and logical.isdigit() else os.cpu_count(),
        "low_power_mode_off": low_power_off,
        "memory_bytes": memory_bytes,
        "os_build": build,
        "os_release": platform.mac_ver()[0] or platform.release(),
        "os_system": platform.system(),
        "physical_cpu_count": int(physical) if physical and physical.isdigit() else None,
        "thermal_nominal": thermal_nominal,
    }
    qualification_match = {
        "ac_power": ac_power,
        "active_power_profile_valid": active_power_profile_valid,
        "architecture": machine["architecture"] == "arm64",
        "chip_model": "Apple M4" in str(machine["chip_model"]),
        "logical_cpu_count": machine["logical_cpu_count"] == 10,
        "low_power_mode_off": low_power_off,
        "memory_bytes": memory_bytes == 16 * 1024**3,
        "os_build": machine["os_build"] == "25F84",
        "os_release": machine["os_release"] == "26.5.2",
        "os_system": machine["os_system"] == "Darwin",
        "physical_cpu_count": machine["physical_cpu_count"] == 10,
        "python_version": platform.python_version() == "3.14.3",
        "thermal_nominal": thermal_nominal,
    }
    return {
        "machine": machine,
        "machine_qualification_checks": qualification_match,
        "machine_qualification_pass": all(qualification_match.values()),
        "machine_sha256": _canonical_sha256(machine),
    }


def _runtime_receipt() -> dict[str, object]:
    executable = Path(sys.executable).resolve(strict=True)
    clock = time.get_clock_info("perf_counter")
    runtime = {
        "clock_adjustable": clock.adjustable,
        "clock_implementation": clock.implementation,
        "clock_monotonic": clock.monotonic,
        "clock_resolution_ns": max(1, round(clock.resolution * 1_000_000_000)),
        "gc_enabled": gc.isenabled(),
        "python_cache_tag": sys.implementation.cache_tag,
        "python_executable_sha256": _file_sha256(executable),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
    }
    return {
        "runtime": runtime,
        "runtime_sha256": _canonical_sha256(runtime),
    }


def _process_receipt(
    *,
    process_role: str,
    process_ordinal: int,
    process_started_monotonic_ns: int,
    source_set_sha256: str,
    machine_sha256: str,
    runtime_sha256: str,
) -> dict[str, object]:
    process = {
        "machine_sha256": machine_sha256,
        "parent_process_id": os.getppid(),
        "process_id": os.getpid(),
        "process_ordinal": process_ordinal,
        "process_role": process_role,
        "process_started_monotonic_ns": process_started_monotonic_ns,
        "runtime_sha256": runtime_sha256,
        "schema_version": PERFORMANCE_PROCESS_SCHEMA_VERSION,
        "source_set_sha256": source_set_sha256,
    }
    return {
        "process": process,
        "process_sha256": _canonical_sha256(process),
    }


@dataclass(frozen=True, slots=True)
class _RealRunnerContextV1:
    driver: _RealStageDriver
    u64: LoadedCorpusV1
    w64: LoadedCorpusV1
    u64_golden: Mapping[str, object]
    w64_golden: Mapping[str, object]
    lineage: dict[str, object]
    combined_backend_evidence_sha256: str


def _initialize_real_context(
    *,
    native_manifest_path: Path,
    expected_backend_evidence_sha256: str,
    process_role: str,
    process_ordinal: int,
    process_started_monotonic_ns: int,
) -> _RealRunnerContextV1:
    expected_build_hash = _require_sha256(
        expected_backend_evidence_sha256,
        "PERFORMANCE_BACKEND_EVIDENCE_INVALID",
    )
    u64 = load_corpus("U64")
    w64 = load_corpus("W64")
    u64_golden = load_reference_golden("U64")
    w64_golden = load_reference_golden("W64")
    u64_decision = load_decision_semantic_golden("U64")
    w64_decision = load_decision_semantic_golden("W64")
    assert_formal_timed_receipt_permitted(u64_decision)
    if (
        w64_decision.formal_timed_receipt_permitted is not False
        or len(u64.bound_inputs) != FIXTURE_COUNT
        or len(w64.bound_inputs) != FIXTURE_COUNT
    ):
        raise PerformanceRunError("PERFORMANCE_FIXTURE_ROLE_INVALID")

    manifest_raw, manifest = _read_manifest_document(native_manifest_path)
    kernel = load_native_tree_v1(
        native_manifest_path,
        expected_backend_evidence_sha256=expected_build_hash,
    )
    compute_call, verify_call, combined_backend_hash = (
        create_native_crr_delta_engine(kernel)
    )
    run_batch, _, verify_batch = _create_exact_64_batch_engine(
        compute_call=compute_call,
        verify_call=verify_call,
        backend_evidence_sha256=combined_backend_hash,
    )
    decision_runner = create_p1_decision_child_runner(
        native_manifest_path=native_manifest_path,
        native_build_backend_evidence_sha256=expected_build_hash,
    )
    driver = _RealStageDriver(
        u64=u64,
        w64=w64,
        u64_golden=u64_golden,
        w64_golden=w64_golden,
        u64_decision_golden=u64_decision,
        w64_decision_golden=w64_decision,
        compute_call=compute_call,
        verify_call=verify_call,
        run_batch=run_batch,
        verify_batch=verify_batch,
        decision_runner=decision_runner,
        combined_backend_evidence_sha256=combined_backend_hash,
        native_build_backend_evidence_sha256=expected_build_hash,
    )
    source = _source_lineage()
    fixtures = _fixture_lineage(
        u64,
        w64,
        u64_golden,
        w64_golden,
        u64_decision,
        w64_decision,
    )
    backend = _backend_lineage(
        manifest_raw,
        manifest,
        expected_build_evidence_sha256=expected_build_hash,
        combined_backend_evidence_sha256=combined_backend_hash,
    )
    machine = _machine_receipt()
    runtime = _runtime_receipt()
    process = _process_receipt(
        process_role=process_role,
        process_ordinal=process_ordinal,
        process_started_monotonic_ns=process_started_monotonic_ns,
        source_set_sha256=source["source_set_sha256"],
        machine_sha256=machine["machine_sha256"],
        runtime_sha256=runtime["runtime_sha256"],
    )
    lineage = {
        "backend": backend,
        "fixtures_and_goldens": fixtures,
        "lineage_sha256": "",
        "machine": machine,
        "process": process,
        "runtime": runtime,
        "schema_version": PERFORMANCE_LINEAGE_SCHEMA_VERSION,
        "source": source,
    }
    lineage["lineage_sha256"] = _canonical_sha256(
        {key: value for key, value in lineage.items() if key != "lineage_sha256"}
    )
    return _RealRunnerContextV1(
        driver=driver,
        u64=u64,
        w64=w64,
        u64_golden=MappingProxyType(dict(u64_golden)),
        w64_golden=MappingProxyType(dict(w64_golden)),
        lineage=lineage,
        combined_backend_evidence_sha256=combined_backend_hash,
    )


def _runner_bundle(
    *,
    context: _RealRunnerContextV1,
    measured: _MeasuredRunnerV1,
    runner_process_ordinal: int,
) -> dict[str, object]:
    single_receipts = []
    for ordinal, samples in enumerate(
        measured.single_samples_by_contract,
        start=1,
    ):
        terminal = context.u64_golden["results"][ordinal - 1]
        single_receipts.append(
            _build_raw_receipt(
                stage="SINGLE_CALL",
                runner_process_ordinal=runner_process_ordinal,
                samples_ns=samples,
                backend_evidence_sha256=(
                    context.combined_backend_evidence_sha256
                ),
                corpus=context.u64,
                golden=context.u64_golden,
                contract_ordinal=ordinal,
                call_terminal_semantic=terminal,
            )
        )
    batch_receipt = _build_raw_receipt(
        stage="BATCH_64",
        runner_process_ordinal=runner_process_ordinal,
        samples_ns=measured.batch_samples_ns,
        backend_evidence_sha256=context.combined_backend_evidence_sha256,
        corpus=context.u64,
        golden=context.u64_golden,
    )
    e2e_receipt = _build_raw_receipt(
        stage="E2E",
        runner_process_ordinal=runner_process_ordinal,
        samples_ns=measured.e2e_samples_ns,
        backend_evidence_sha256=context.combined_backend_evidence_sha256,
        corpus=context.u64,
        golden=context.u64_golden,
    )
    warmup_sidecar = {
        "expected_warmup_runs": WARMUP_RUNS,
        "records": list(measured.w64_semantic_records),
        "runner_process_ordinal": runner_process_ordinal,
        "schema_version": PERFORMANCE_WARMUP_SIDECAR_SCHEMA_VERSION,
        "status": "PASS",
        "timed": False,
    }
    e2e_sidecar = {
        "expected_samples": FORMAL_PERFORMANCE_PLAN.e2e_samples,
        "records": [
            {"sample_ordinal": index, **record}
            for index, record in enumerate(
                measured.e2e_semantic_records,
                start=1,
            )
        ],
        "runner_process_ordinal": runner_process_ordinal,
        "schema_version": PERFORMANCE_E2E_SIDECAR_SCHEMA_VERSION,
        "status": "PASS",
    }
    latency = _evaluate_runner_latency(
        single_contract_p95_ns=measured.single_contract_p95_ns,
        batch_p95_ns=measured.batch_p95_ns,
        e2e_p95_ns=measured.e2e_p95_ns,
    )
    raw_receipt_hashes = {
        "batch_64": _canonical_sha256(batch_receipt),
        "e2e": _canonical_sha256(e2e_receipt),
        "single_call": [
            _canonical_sha256(receipt) for receipt in single_receipts
        ],
    }
    runner_aggregate = {
        "actionable": False,
        "broker_order_count": 0,
        "decision_status": "NO_DECISION",
        "e1_preflight_status": "NOT_SUPPLIED_OR_VERIFIED",
        "e2e_semantic_sidecar_sha256": _canonical_sha256(e2e_sidecar),
        "latency": latency,
        "lineage_sha256": context.lineage["lineage_sha256"],
        "p1_overall_status": _QUALIFICATION_STATUS,
        "pooled_single_p95_ns": measured.single_pooled_p95_ns,
        "pooled_single_samples_ns": list(
            measured.single_pooled_samples_ns
        ),
        "raw_receipt_sha256": raw_receipt_hashes,
        "runner_process_ordinal": runner_process_ordinal,
        "schema_version": PERFORMANCE_RUNNER_AGGREGATE_SCHEMA_VERSION,
        "single_contract_p95_ns": [
            {"contract_ordinal": ordinal, "p95_ns": p95}
            for ordinal, p95 in enumerate(
                measured.single_contract_p95_ns,
                start=1,
            )
        ],
        "single_execution_order": list(measured.single_execution_order),
        "status": "PASS",
        "t1_preflight_status": "NOT_SUPPLIED_OR_VERIFIED",
        "w64_warmup_sidecar_sha256": _canonical_sha256(warmup_sidecar),
    }
    return {
        "e2e_semantic_sidecar": e2e_sidecar,
        "lineage": context.lineage,
        "raw_receipts": {
            "batch_64": batch_receipt,
            "e2e": e2e_receipt,
            "single_call": single_receipts,
        },
        "runner_aggregate": runner_aggregate,
        "runner_process_ordinal": runner_process_ordinal,
        "schema_version": PERFORMANCE_RUNNER_SCHEMA_VERSION,
        "status": "PASS",
        "w64_warmup_sidecar": warmup_sidecar,
    }


def _failure_bundle(
    *,
    process_role: str,
    process_ordinal: int,
    reason_code: str,
) -> dict[str, object]:
    return {
        "failure_reason_code": reason_code,
        "p1_overall_status": _QUALIFICATION_STATUS,
        "process_ordinal": process_ordinal,
        "process_role": process_role,
        "schema_version": PERFORMANCE_RUNNER_SCHEMA_VERSION,
        "status": "FAIL",
    }


def _send_process_payload(connection: Connection, document: object) -> None:
    raw = canonical_json_bytes(document)
    if len(raw) > _MAX_PROCESS_PAYLOAD_BYTES:
        raise PerformanceRunError("PERFORMANCE_PROCESS_PAYLOAD_TOO_LARGE")
    connection.send_bytes(raw)


def _formal_runner_process_entry(
    send_connection: Connection,
    native_manifest_path: str,
    expected_backend_evidence_sha256: str,
    runner_process_ordinal: int,
) -> None:
    started_ns = time.perf_counter_ns()
    try:
        _establish_outer_process_boundary()
        context = _initialize_real_context(
            native_manifest_path=Path(native_manifest_path),
            expected_backend_evidence_sha256=expected_backend_evidence_sha256,
            process_role="RUNNER",
            process_ordinal=runner_process_ordinal,
            process_started_monotonic_ns=started_ns,
        )
        if gc.isenabled() is not True:
            raise PerformanceRunError("PERFORMANCE_GC_POLICY_INVALID")
        measured = _execute_measurement_plan(
            plan=FORMAL_PERFORMANCE_PLAN,
            driver=context.driver,
            clock_ns=time.perf_counter_ns,
        )
        if gc.isenabled() is not True:
            raise PerformanceRunError("PERFORMANCE_GC_POLICY_INVALID")
        document = _runner_bundle(
            context=context,
            measured=measured,
            runner_process_ordinal=runner_process_ordinal,
        )
    except PerformanceRunError as error:
        document = _failure_bundle(
            process_role="RUNNER",
            process_ordinal=runner_process_ordinal,
            reason_code=error.reason_code,
        )
    except BaseException:
        document = _failure_bundle(
            process_role="RUNNER",
            process_ordinal=runner_process_ordinal,
            reason_code="PERFORMANCE_RUNNER_UNEXPECTED_FAILURE",
        )
    try:
        _send_process_payload(send_connection, document)
    finally:
        send_connection.close()


def _cold_process_entry(
    send_connection: Connection,
    native_manifest_path: str,
    expected_backend_evidence_sha256: str,
    cold_process_ordinal: int,
) -> None:
    started_ns = time.perf_counter_ns()
    try:
        _establish_outer_process_boundary()
        context = _initialize_real_context(
            native_manifest_path=Path(native_manifest_path),
            expected_backend_evidence_sha256=expected_backend_evidence_sha256,
            process_role="COLD_E2E",
            process_ordinal=cold_process_ordinal,
            process_started_monotonic_ns=started_ns,
        )
        record = context.driver.run_u64_e2e()
        _validate_stage_record(
            record,
            fixture_id="U64",
            plan=FORMAL_PERFORMANCE_PLAN,
        )
        document = {
            "lineage": context.lineage,
            "process_ordinal": cold_process_ordinal,
            "record": record,
            "schema_version": PERFORMANCE_COLD_SIDECAR_SCHEMA_VERSION,
            "status": "PASS",
            "w64_warmup_count": 0,
        }
    except PerformanceRunError as error:
        document = _failure_bundle(
            process_role="COLD_E2E",
            process_ordinal=cold_process_ordinal,
            reason_code=error.reason_code,
        )
    except BaseException:
        document = _failure_bundle(
            process_role="COLD_E2E",
            process_ordinal=cold_process_ordinal,
            reason_code="PERFORMANCE_COLD_PROCESS_UNEXPECTED_FAILURE",
        )
    try:
        _send_process_payload(send_connection, document)
    finally:
        send_connection.close()


def _formal_launch_schedule(plan: _PerformancePlanV1) -> tuple[tuple[str, int], ...]:
    return (
        ("RUNNER", 1),
        ("RUNNER", 2),
        *(
            ("COLD_E2E", ordinal)
            for ordinal in range(1, plan.cold_e2e_samples + 1)
        ),
    )


def _formal_launch_schedule_for_test(
    plan: _PerformancePlanV1,
) -> tuple[tuple[str, int], ...]:
    return _formal_launch_schedule(plan)


def _parse_process_payload(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_PROCESS_PAYLOAD_BYTES:
        raise PerformanceRunError("PERFORMANCE_PROCESS_PAYLOAD_INVALID")
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PerformanceRunError("PERFORMANCE_PROCESS_PAYLOAD_INVALID") from error
    if type(document) is not dict or raw != canonical_json_bytes(document):
        raise PerformanceRunError("PERFORMANCE_PROCESS_PAYLOAD_INVALID")
    return document


def _outer_hard_limit_seconds(process_role: str) -> int:
    if process_role == "RUNNER":
        return _FORMAL_RUNNER_OUTER_HARD_LIMIT_SECONDS
    if process_role == "COLD_E2E":
        return _COLD_OUTER_HARD_LIMIT_SECONDS
    raise PerformanceRunError("PERFORMANCE_PROCESS_ROLE_INVALID")


def _descendant_process_ids(root_process_id: int) -> tuple[int, ...]:
    if type(root_process_id) is not int or root_process_id <= 1:
        return ()
    try:
        completed = subprocess.run(
            ("/bin/ps", "-axo", "pid=,ppid="),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if completed.returncode != 0:
        return ()
    children: dict[int, list[int]] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or not all(field.isdigit() for field in fields):
            continue
        process_id, parent_process_id = (int(field) for field in fields)
        if process_id > 1 and parent_process_id > 0:
            children.setdefault(parent_process_id, []).append(process_id)
    descendants: list[int] = []
    pending = list(children.get(root_process_id, ()))
    seen = {root_process_id}
    while pending:
        process_id = pending.pop(0)
        if process_id in seen or process_id in {os.getpid(), os.getppid()}:
            continue
        seen.add(process_id)
        descendants.append(process_id)
        pending.extend(children.get(process_id, ()))
    return tuple(descendants)


def _signal_exact_process_or_isolated_group(
    process_id: int,
    signal_number: int,
) -> None:
    if (
        type(process_id) is not int
        or process_id <= 1
        or process_id in {os.getpid(), os.getppid()}
    ):
        return
    try:
        process_group_id = os.getpgid(process_id)
        if process_group_id == process_id and process_group_id != os.getpgrp():
            os.killpg(process_group_id, signal_number)
        else:
            os.kill(process_id, signal_number)
    except (ProcessLookupError, PermissionError):
        return


def _cleanup_outer_process(process: object) -> None:
    process_id = process.pid
    if process_id is None:
        return
    tracked_descendants = set(_descendant_process_ids(process_id))
    for descendant in reversed(tuple(tracked_descendants)):
        _signal_exact_process_or_isolated_group(descendant, signal.SIGTERM)
    _signal_exact_process_or_isolated_group(process_id, signal.SIGTERM)
    process.join(_OUTER_TERMINATE_GRACE_SECONDS)
    if process.is_alive():
        tracked_descendants.update(_descendant_process_ids(process_id))
        for descendant in reversed(tuple(tracked_descendants)):
            _signal_exact_process_or_isolated_group(descendant, signal.SIGKILL)
        _signal_exact_process_or_isolated_group(process_id, signal.SIGKILL)
        process.join(_OUTER_TERMINATE_GRACE_SECONDS)
    for descendant in reversed(tuple(tracked_descendants)):
        _signal_exact_process_or_isolated_group(descendant, signal.SIGKILL)


def _bounded_receive_and_reap(
    *,
    receive_connection: object,
    process: object,
    process_role: str,
) -> tuple[bytes, int]:
    completed = threading.Event()
    state: dict[str, object] = {}

    def read_complete_frame() -> None:
        try:
            state["raw"] = receive_connection.recv_bytes(
                _MAX_PROCESS_PAYLOAD_BYTES
            )
        except BaseException as error:
            state["error"] = error
        finally:
            completed.set()

    reader = threading.Thread(
        target=read_complete_frame,
        name=f"gld-p1-{process_role.lower()}-frame-reader",
        daemon=True,
    )
    reader.start()
    if not completed.wait(_outer_hard_limit_seconds(process_role)):
        _cleanup_outer_process(process)
        try:
            receive_connection.close()
        except OSError:
            pass
        reader.join(_OUTER_TEARDOWN_GRACE_SECONDS)
        if reader.is_alive():
            raise PerformanceRunError(
                "PERFORMANCE_FRAME_READER_TEARDOWN_INVALID"
            )
        raise PerformanceRunError("PERFORMANCE_OUTER_PROCESS_HARD_LIMIT")
    reader.join(_OUTER_TEARDOWN_GRACE_SECONDS)
    if reader.is_alive():
        raise PerformanceRunError("PERFORMANCE_FRAME_READER_TEARDOWN_INVALID")
    read_error = state.get("error")
    if read_error is not None:
        raise PerformanceRunError(
            "PERFORMANCE_PROCESS_OUTPUT_MISSING"
        ) from read_error
    raw = state.get("raw")
    if type(raw) is not bytes:
        raise PerformanceRunError("PERFORMANCE_PROCESS_OUTPUT_MISSING")
    process.join(_OUTER_TEARDOWN_GRACE_SECONDS)
    completed_ns = time.perf_counter_ns()
    if process.exitcode != 0 or process.is_alive():
        raise PerformanceRunError(
            "PERFORMANCE_OUTER_PROCESS_TEARDOWN_INVALID"
        )
    return raw, completed_ns


def _launch_one_process(
    *,
    process_role: str,
    process_ordinal: int,
    native_manifest_path: Path,
    expected_backend_evidence_sha256: str,
) -> tuple[dict[str, object], int]:
    context = get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    target = (
        _formal_runner_process_entry
        if process_role == "RUNNER"
        else _cold_process_entry
    )
    process = context.Process(
        target=target,
        args=(
            send_connection,
            str(native_manifest_path),
            expected_backend_evidence_sha256,
            process_ordinal,
        ),
        name=f"gld-p1-{process_role.lower()}-{process_ordinal:02d}",
    )
    started_ns = time.perf_counter_ns()
    try:
        process.start()
        send_connection.close()
        try:
            raw, completed_ns = _bounded_receive_and_reap(
                receive_connection=receive_connection,
                process=process,
                process_role=process_role,
            )
        finally:
            receive_connection.close()
    except BaseException:
        try:
            send_connection.close()
        except OSError:
            pass
        try:
            receive_connection.close()
        except OSError:
            pass
        if process.pid is not None and process.is_alive():
            _cleanup_outer_process(process)
        if process.pid is not None and not process.is_alive():
            process.close()
        raise
    process.close()
    document = _parse_process_payload(raw)
    duration_ns = _positive_duration(started_ns, completed_ns)
    return document, duration_ns


def _prepare_output_directory(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.exists():
        if not resolved.is_dir() or any(resolved.iterdir()):
            raise PerformanceRunError("PERFORMANCE_OUTPUT_DIRECTORY_NOT_EMPTY")
    else:
        try:
            resolved.mkdir(parents=True)
        except OSError as error:
            raise PerformanceRunError("PERFORMANCE_OUTPUT_DIRECTORY_INVALID") from error
    return resolved


def _write_canonical(path: Path, document: object) -> str:
    raw = canonical_json_bytes(document)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(raw)
        os.replace(temporary, path)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise PerformanceRunError("PERFORMANCE_OUTPUT_WRITE_FAILED") from error
    return sha256(raw).hexdigest()


def _validate_lineage(document: object) -> dict[str, object]:
    if type(document) is not dict:
        raise PerformanceRunError("PERFORMANCE_LINEAGE_INVALID")
    lineage_hash = document.get("lineage_sha256")
    if (
        document.get("schema_version") != PERFORMANCE_LINEAGE_SCHEMA_VERSION
        or type(document.get("source")) is not dict
        or type(document.get("fixtures_and_goldens")) is not dict
        or type(document.get("backend")) is not dict
        or type(document.get("machine")) is not dict
        or type(document.get("runtime")) is not dict
        or type(document.get("process")) is not dict
        or lineage_hash
        != _canonical_sha256(
            {
                key: value
                for key, value in document.items()
                if key != "lineage_sha256"
            }
        )
    ):
        raise PerformanceRunError("PERFORMANCE_LINEAGE_INVALID")
    source = document["source"]
    fixtures = document["fixtures_and_goldens"]
    backend = document["backend"]
    machine = document["machine"]
    runtime = document["runtime"]
    process = document["process"]
    if (
        type(source.get("files")) is not dict
        or source.get("source_set_sha256")
        != _canonical_sha256(source["files"])
        or type(machine.get("machine")) is not dict
        or machine.get("machine_sha256")
        != _canonical_sha256(machine["machine"])
        or type(runtime.get("runtime")) is not dict
        or runtime.get("runtime_sha256")
        != _canonical_sha256(runtime["runtime"])
        or type(process.get("process")) is not dict
        or process.get("process_sha256")
        != _canonical_sha256(process["process"])
        or process["process"].get("source_set_sha256")
        != source["source_set_sha256"]
        or process["process"].get("machine_sha256")
        != machine["machine_sha256"]
        or process["process"].get("runtime_sha256")
        != runtime["runtime_sha256"]
        or fixtures.get("static_manifest_sha256")
        != STATIC_MANIFEST_SHA256
        or fixtures.get("decision_static_manifest_sha256")
        != DECISION_STATIC_MANIFEST_SHA256
        or type(fixtures.get("u64")) is not dict
        or type(fixtures.get("w64")) is not dict
        or type(backend.get("manifest_fields")) is not dict
        or backend.get("expected_native_build_backend_evidence_sha256")
        != backend["manifest_fields"].get("backend_evidence_sha256")
    ):
        raise PerformanceRunError("PERFORMANCE_LINEAGE_INVALID")
    for fixture_id in ("u64", "w64"):
        for item in fixtures[fixture_id].values():
            _require_sha256(item, "PERFORMANCE_LINEAGE_INVALID")
    for field in (
        "combined_backend_evidence_sha256",
        "expected_native_build_backend_evidence_sha256",
        "native_manifest_sha256",
    ):
        _require_sha256(backend.get(field), "PERFORMANCE_LINEAGE_INVALID")
    return document


def _stable_lineage_identity(lineage: Mapping[str, object]) -> tuple[object, ...]:
    source = lineage["source"]
    machine = lineage["machine"]
    runtime = lineage["runtime"]
    return (
        source["source_set_sha256"],
        _canonical_sha256(lineage["fixtures_and_goldens"]),
        _canonical_sha256(lineage["backend"]),
        machine["machine_sha256"],
        runtime["runtime_sha256"],
    )


def _validate_decision_sidecar_record(
    record: object,
    *,
    fixture_id: str,
    expected_document: Mapping[str, object],
    expected_request_binding: Mapping[str, object],
    expected_request_sha256: str,
    expected_combined_backend_sha256: str,
) -> dict[str, object]:
    if type(record) is not dict or type(record.get("semantic_receipt")) is not dict:
        raise PerformanceRunError("PERFORMANCE_SEMANTIC_SIDECAR_INVALID")
    semantic = record["semantic_receipt"]
    components = semantic.get("backend_evidence_components")
    reconstructed_decision_semantic_sha256 = (
        _validate_actual_decision_semantic(
            semantic,
            expected_decision_document=expected_document,
            expected_request_binding=expected_request_binding,
            expected_combined_backend_sha256=(
                expected_combined_backend_sha256
            ),
        )
    )
    if (
        type(components) is not dict
        or record.get("fixture_id") != fixture_id
        or record.get("decision_request_sha256") != expected_request_sha256
        or record.get("decision_semantic_golden_sha256")
        != expected_document["decision_semantic_sha256"]
        or record.get("reconstructed_decision_semantic_sha256")
        != reconstructed_decision_semantic_sha256
        or record.get("projection_sha256")
        != _canonical_sha256(expected_document["decision_projection"])
        or semantic.get("call_terminals") != expected_document["call_terminals"]
        or semantic.get("call_semantic_output_sha256")
        != expected_document["call_semantic_output_sha256"]
        or semantic.get("decision_projection")
        != expected_document["decision_projection"]
        or semantic.get("projection_sha256") != record.get("projection_sha256")
        or semantic.get("runtime_semantic_sha256")
        != record.get("runtime_semantic_sha256")
        or components.get("backend_evidence_sha256")
        != expected_combined_backend_sha256
        or record.get("semantic_receipt_sha256")
        != _compact_canonical_sha256(semantic)
        or semantic.get("actionable") is not False
        or semantic.get("broker_order_count") != 0
    ):
        raise PerformanceRunError("PERFORMANCE_SEMANTIC_SIDECAR_INVALID")
    for field in (
        "artifact_sha256",
        "decision_request_sha256",
        "decision_semantic_golden_sha256",
        "projection_sha256",
        "reconstructed_decision_semantic_sha256",
        "runtime_semantic_sha256",
        "semantic_receipt_sha256",
        "supervisor_generation_token",
        "supervisor_request_sha256",
    ):
        _require_sha256(
            record.get(field),
            "PERFORMANCE_SEMANTIC_SIDECAR_INVALID",
        )
    return record


def _validate_runner_bundle(
    document: object,
    *,
    runner_process_ordinal: int,
) -> dict[str, object]:
    if (
        type(document) is not dict
        or document.get("schema_version") != PERFORMANCE_RUNNER_SCHEMA_VERSION
        or document.get("status") != "PASS"
        or document.get("runner_process_ordinal") != runner_process_ordinal
        or type(document.get("lineage")) is not dict
        or type(document.get("raw_receipts")) is not dict
        or type(document.get("w64_warmup_sidecar")) is not dict
        or type(document.get("e2e_semantic_sidecar")) is not dict
        or type(document.get("runner_aggregate")) is not dict
    ):
        reason = (
            document.get("failure_reason_code")
            if type(document) is dict
            else None
        )
        raise PerformanceRunError(
            reason
            if type(reason) is str
            else "PERFORMANCE_RUNNER_BUNDLE_INVALID"
        )
    lineage = document["lineage"]
    _validate_lineage(lineage)
    raw_receipts = document["raw_receipts"]
    single_receipts = raw_receipts.get("single_call")
    batch_receipt = raw_receipts.get("batch_64")
    e2e_receipt = raw_receipts.get("e2e")
    if (
        type(single_receipts) is not list
        or len(single_receipts) != FIXTURE_COUNT
        or type(batch_receipt) is not dict
        or type(e2e_receipt) is not dict
    ):
        raise PerformanceRunError("PERFORMANCE_RUNNER_BUNDLE_INVALID")
    for ordinal, receipt in enumerate(single_receipts, start=1):
        if (
            type(receipt) is not dict
            or receipt.get("contract_ordinal") != ordinal
            or receipt.get("runner_process_ordinal")
            != runner_process_ordinal
            or receipt.get("gc_policy") != "ENABLED"
        ):
            raise PerformanceRunError("PERFORMANCE_RUNNER_BUNDLE_INVALID")
        try:
            validate_raw_sample_receipt(receipt)
        except Exception as error:
            raise PerformanceRunError(
                "PERFORMANCE_RUNNER_BUNDLE_INVALID"
            ) from error
    for receipt, stage in (
        (batch_receipt, "BATCH_64"),
        (e2e_receipt, "E2E"),
    ):
        if (
            receipt.get("stage") != stage
            or receipt.get("runner_process_ordinal")
            != runner_process_ordinal
            or receipt.get("gc_policy") != "ENABLED"
        ):
            raise PerformanceRunError("PERFORMANCE_RUNNER_BUNDLE_INVALID")
        try:
            validate_raw_sample_receipt(receipt)
        except Exception as error:
            raise PerformanceRunError(
                "PERFORMANCE_RUNNER_BUNDLE_INVALID"
            ) from error

    warmup = document["w64_warmup_sidecar"]
    e2e_sidecar = document["e2e_semantic_sidecar"]
    warmup_records = warmup.get("records")
    e2e_records = e2e_sidecar.get("records")
    if (
        warmup.get("schema_version")
        != PERFORMANCE_WARMUP_SIDECAR_SCHEMA_VERSION
        or warmup.get("expected_warmup_runs") != WARMUP_RUNS
        or warmup.get("timed") is not False
        or type(warmup_records) is not list
        or len(warmup_records) != WARMUP_RUNS
        or e2e_sidecar.get("schema_version")
        != PERFORMANCE_E2E_SIDECAR_SCHEMA_VERSION
        or e2e_sidecar.get("expected_samples")
        != FORMAL_PERFORMANCE_PLAN.e2e_samples
        or type(e2e_records) is not list
        or len(e2e_records) != FORMAL_PERFORMANCE_PLAN.e2e_samples
    ):
        raise PerformanceRunError("PERFORMANCE_RUNNER_BUNDLE_INVALID")
    u64 = load_corpus("U64")
    w64 = load_corpus("W64")
    u64_reference = load_reference_golden("U64")
    w64_reference = load_reference_golden("W64")
    u64_decision_loaded = load_decision_semantic_golden("U64")
    w64_decision_loaded = load_decision_semantic_golden("W64")
    u64_decision = _thaw_json(u64_decision_loaded.document)
    w64_decision = _thaw_json(w64_decision_loaded.document)
    expected_fixture_lineage = _fixture_lineage(
        u64,
        w64,
        u64_reference,
        w64_reference,
        u64_decision_loaded,
        w64_decision_loaded,
    )
    if lineage["fixtures_and_goldens"] != expected_fixture_lineage:
        raise PerformanceRunError("PERFORMANCE_LINEAGE_INVALID")
    u64_request_binding = _expected_request_binding(u64, u64_decision)
    w64_request_binding = _expected_request_binding(w64, w64_decision)
    _, u64_request_sha256 = encode_p1_decision_request(
        **_typed_request_kwargs(u64)
    )
    _, w64_request_sha256 = encode_p1_decision_request(
        **_typed_request_kwargs(w64)
    )
    combined_backend_hash = lineage["backend"][
        "combined_backend_evidence_sha256"
    ]
    for record in warmup_records:
        _validate_stage_record(
            record,
            fixture_id="W64",
            plan=FORMAL_PERFORMANCE_PLAN,
        )
        _validate_decision_sidecar_record(
            record,
            fixture_id="W64",
            expected_document=w64_decision,
            expected_request_binding=w64_request_binding,
            expected_request_sha256=w64_request_sha256,
            expected_combined_backend_sha256=combined_backend_hash,
        )
    for sample_ordinal, record in enumerate(e2e_records, start=1):
        if record.get("sample_ordinal") != sample_ordinal:
            raise PerformanceRunError("PERFORMANCE_RUNNER_BUNDLE_INVALID")
        _validate_stage_record(
            record,
            fixture_id="U64",
            plan=FORMAL_PERFORMANCE_PLAN,
        )
        _validate_decision_sidecar_record(
            record,
            fixture_id="U64",
            expected_document=u64_decision,
            expected_request_binding=u64_request_binding,
            expected_request_sha256=u64_request_sha256,
            expected_combined_backend_sha256=combined_backend_hash,
        )

    aggregate = document["runner_aggregate"]
    if (
        aggregate.get("actionable") is not False
        or aggregate.get("broker_order_count") != 0
        or aggregate.get("decision_status") != "NO_DECISION"
        or aggregate.get("p1_overall_status") != _QUALIFICATION_STATUS
        or aggregate.get("e1_preflight_status")
        != "NOT_SUPPLIED_OR_VERIFIED"
        or aggregate.get("t1_preflight_status")
        != "NOT_SUPPLIED_OR_VERIFIED"
        or type(aggregate.get("latency")) is not dict
        or lineage["runtime"]["runtime"].get("gc_enabled") is not True
    ):
        raise PerformanceRunError("PERFORMANCE_RUNNER_BUNDLE_INVALID")
    expected_order = list(range(1, FIXTURE_COUNT + 1)) * 30
    pooled_samples = [
        single_receipts[ordinal]["samples_ns"][repetition]
        for repetition in range(30)
        for ordinal in range(FIXTURE_COUNT)
    ]
    single_p95_values = [
        receipt["reported_p95_ns"] for receipt in single_receipts
    ]
    expected_latency = _evaluate_runner_latency(
        single_contract_p95_ns=single_p95_values,
        batch_p95_ns=batch_receipt["reported_p95_ns"],
        e2e_p95_ns=e2e_receipt["reported_p95_ns"],
    )
    expected_single_p95 = [
        {"contract_ordinal": ordinal, "p95_ns": p95}
        for ordinal, p95 in enumerate(single_p95_values, start=1)
    ]
    expected_raw_hashes = {
        "batch_64": _canonical_sha256(batch_receipt),
        "e2e": _canonical_sha256(e2e_receipt),
        "single_call": [
            _canonical_sha256(receipt) for receipt in single_receipts
        ],
    }
    if (
        aggregate.get("runner_process_ordinal")
        != runner_process_ordinal
        or aggregate.get("lineage_sha256") != lineage["lineage_sha256"]
        or aggregate.get("latency") != expected_latency
        or aggregate.get("single_contract_p95_ns") != expected_single_p95
        or aggregate.get("single_execution_order") != expected_order
        or aggregate.get("pooled_single_samples_ns") != pooled_samples
        or aggregate.get("pooled_single_p95_ns")
        != nearest_rank_p95_ns(pooled_samples)
        or aggregate.get("raw_receipt_sha256") != expected_raw_hashes
        or aggregate.get("w64_warmup_sidecar_sha256")
        != _canonical_sha256(warmup)
        or aggregate.get("e2e_semantic_sidecar_sha256")
        != _canonical_sha256(e2e_sidecar)
    ):
        raise PerformanceRunError("PERFORMANCE_RUNNER_BUNDLE_INVALID")
    return document


def _write_runner_artifacts(
    output_directory: Path,
    bundle: Mapping[str, object],
    *,
    runner_process_ordinal: int,
) -> dict[str, str]:
    directory = output_directory / f"runner-{runner_process_ordinal:02d}"
    directory.mkdir()
    raw = bundle["raw_receipts"]
    hashes: dict[str, str] = {}
    for ordinal, receipt in enumerate(raw["single_call"], start=1):
        name = f"raw-single-call-{ordinal:02d}.json"
        hashes[f"runner-{runner_process_ordinal:02d}/{name}"] = _write_canonical(
            directory / name,
            receipt,
        )
    for name, key in (
        ("raw-batch-64.json", "batch_64"),
        ("raw-e2e.json", "e2e"),
    ):
        hashes[f"runner-{runner_process_ordinal:02d}/{name}"] = _write_canonical(
            directory / name,
            raw[key],
        )
    for name, key in (
        ("w64-warmup-sidecar.json", "w64_warmup_sidecar"),
        ("e2e-semantic-sidecar.json", "e2e_semantic_sidecar"),
        ("lineage.json", "lineage"),
        ("runner-aggregate.json", "runner_aggregate"),
    ):
        hashes[f"runner-{runner_process_ordinal:02d}/{name}"] = _write_canonical(
            directory / name,
            bundle[key],
        )
    return hashes


def _component_aggregate(
    *,
    runner_bundles: Sequence[Mapping[str, object]],
    cold_samples_ns: Sequence[int],
    cold_records: Sequence[Mapping[str, object]],
    output_file_sha256: Mapping[str, str],
) -> dict[str, object]:
    if len(runner_bundles) != 2 or len(cold_samples_ns) != 30:
        raise PerformanceRunError("PERFORMANCE_COMPONENT_AGGREGATE_INVALID")
    runner_aggregates = [bundle["runner_aggregate"] for bundle in runner_bundles]
    runner_latency = [aggregate["latency"] for aggregate in runner_aggregates]
    combined_single_p95: list[int] = []
    for ordinal in range(FIXTURE_COUNT):
        combined_single_p95.append(
            nearest_rank_p95_ns(
                tuple(
                    sample
                    for bundle in runner_bundles
                    for sample in bundle["raw_receipts"]["single_call"][ordinal][
                        "samples_ns"
                    ]
                )
            )
        )
    combined_batch_samples = tuple(
        sample
        for bundle in runner_bundles
        for sample in bundle["raw_receipts"]["batch_64"]["samples_ns"]
    )
    combined_e2e_samples = tuple(
        sample
        for bundle in runner_bundles
        for sample in bundle["raw_receipts"]["e2e"]["samples_ns"]
    )
    combined_pooled_single = tuple(
        sample
        for aggregate in runner_aggregates
        for sample in aggregate["pooled_single_samples_ns"]
    )
    combined_latency = _evaluate_runner_latency(
        single_contract_p95_ns=combined_single_p95,
        batch_p95_ns=nearest_rank_p95_ns(combined_batch_samples),
        e2e_p95_ns=nearest_rank_p95_ns(combined_e2e_samples),
    )
    warm_latency_gate_pass = (
        all(item["runner_latency_gate_pass"] is True for item in runner_latency)
        and combined_latency["runner_latency_gate_pass"] is True
    )
    cold_p95 = nearest_rank_p95_ns(cold_samples_ns)
    cold_gate = _evaluate_cold_gate(
        cold_p95_ns=cold_p95,
        plugin_outer_process_lifecycle=(
            _CURRENT_PLUGIN_OUTER_PROCESS_LIFECYCLE
        ),
    )
    latency_gate_pass = warm_latency_gate_pass and (
        cold_gate["gate_applied"] is not True
        or cold_gate["gate_pass"] is True
    )
    machine_pass = all(
        bundle["lineage"]["machine"]["machine_qualification_pass"] is True
        for bundle in runner_bundles
    )
    blockers = [
        "E1_PREFLIGHT_NOT_SUPPLIED_OR_VERIFIED",
        "T1_PREFLIGHT_NOT_SUPPLIED_OR_VERIFIED",
    ]
    cold_blocker = cold_gate["promotion_blocker"]
    if type(cold_blocker) is str:
        blockers.append(cold_blocker)
    if not latency_gate_pass:
        blockers.append("LATENCY_GATE_NOT_PASS")
    if not machine_pass:
        blockers.append("QUALIFICATION_MACHINE_NOT_VERIFIED")
    first_lineage = runner_bundles[0]["lineage"]
    return {
        "actionable": False,
        "broker_order_count": 0,
        "cold_e2e": {
            **cold_gate,
            "p95_ns": cold_p95,
            "samples_ns": list(cold_samples_ns),
            "slo_ns": E2E_SLO_NS,
        },
        "cold_semantic_record_count": len(cold_records),
        "combined_components": {
            "batch_samples_ns": list(combined_batch_samples),
            "e2e_samples_ns": list(combined_e2e_samples),
            "latency": combined_latency,
            "pooled_single_p95_ns": nearest_rank_p95_ns(
                combined_pooled_single
            ),
            "pooled_single_samples_ns": list(combined_pooled_single),
            "single_contract_p95_ns": [
                {"contract_ordinal": ordinal, "p95_ns": p95}
                for ordinal, p95 in enumerate(combined_single_p95, start=1)
            ],
        },
        "decision_status": "NO_DECISION",
        "latency_gate_pass": latency_gate_pass,
        "lineage_binding": {
            "backend_sha256": _canonical_sha256(first_lineage["backend"]),
            "cold_process_sha256": [
                record["lineage"]["process"]["process_sha256"]
                for record in cold_records
            ],
            "fixtures_and_goldens_sha256": _canonical_sha256(
                first_lineage["fixtures_and_goldens"]
            ),
            "machine_sha256": first_lineage["machine"]["machine_sha256"],
            "runner_process_sha256": [
                bundle["lineage"]["process"]["process_sha256"]
                for bundle in runner_bundles
            ],
            "runtime_sha256": first_lineage["runtime"]["runtime_sha256"],
            "source_set_sha256": first_lineage["source"][
                "source_set_sha256"
            ],
        },
        "machine_qualification_pass": machine_pass,
        "output_file_sha256": dict(output_file_sha256),
        "p1_blocking_gates": blockers,
        "p1_promotion_gate_pass": not blockers,
        "p1_overall_status": _QUALIFICATION_STATUS,
        "runner_aggregates": runner_aggregates,
        "runner_process_count": len(runner_bundles),
        "schema_version": PERFORMANCE_COMPONENT_AGGREGATE_SCHEMA_VERSION,
        "status": "MEASUREMENT_COMPLETE",
        "warm_latency_gate_pass": warm_latency_gate_pass,
    }


def _failed_component_aggregate(
    *,
    reason_code: str,
    runner_processes_completed: int,
    cold_processes_completed: int,
    output_file_sha256: Mapping[str, str],
) -> dict[str, object]:
    lifecycle_policy = _evaluate_cold_gate(
        cold_p95_ns=E2E_SLO_NS,
        plugin_outer_process_lifecycle=(
            _CURRENT_PLUGIN_OUTER_PROCESS_LIFECYCLE
        ),
    )
    blockers = [
        "E1_PREFLIGHT_NOT_SUPPLIED_OR_VERIFIED",
        "T1_PREFLIGHT_NOT_SUPPLIED_OR_VERIFIED",
        "PERFORMANCE_MEASUREMENT_FAILED",
    ]
    lifecycle_blocker = lifecycle_policy["promotion_blocker"]
    if type(lifecycle_blocker) is str:
        blockers.append(lifecycle_blocker)
    return {
        "actionable": False,
        "broker_order_count": 0,
        "cold_processes_completed": cold_processes_completed,
        "decision_status": "NO_DECISION",
        "latency_gate_pass": False,
        "measurement_failure_reason_code": reason_code,
        "output_file_sha256": dict(output_file_sha256),
        "p1_blocking_gates": blockers,
        "p1_promotion_gate_pass": False,
        "p1_overall_status": _QUALIFICATION_STATUS,
        "plugin_outer_process_lifecycle": (
            _CURRENT_PLUGIN_OUTER_PROCESS_LIFECYCLE
        ),
        "runner_processes_completed": runner_processes_completed,
        "schema_version": PERFORMANCE_COMPONENT_AGGREGATE_SCHEMA_VERSION,
        "status": "MEASUREMENT_FAILED",
    }


def run_formal_performance(
    *,
    native_manifest_path: Path,
    expected_backend_evidence_sha256: str,
    output_directory: Path,
) -> Path:
    """Run the fixed two-runner plus Cold30 measurement and write evidence."""

    manifest_path = Path(native_manifest_path).resolve(strict=True)
    expected_hash = _require_sha256(
        expected_backend_evidence_sha256,
        "PERFORMANCE_BACKEND_EVIDENCE_INVALID",
    )
    destination = _prepare_output_directory(Path(output_directory))
    runner_bundles: list[dict[str, object]] = []
    cold_records: list[dict[str, object]] = []
    cold_samples: list[int] = []
    output_hashes: dict[str, str] = {}
    aggregate_path = destination / "component-aggregate.json"
    try:
        if gc.isenabled() is not True:
            raise PerformanceRunError("PERFORMANCE_GC_POLICY_INVALID")
        coordinator_u64 = load_corpus("U64")
        coordinator_u64_decision = _thaw_json(
            load_decision_semantic_golden("U64").document
        )
        coordinator_u64_request_binding = _expected_request_binding(
            coordinator_u64,
            coordinator_u64_decision,
        )
        _, coordinator_u64_request_sha256 = encode_p1_decision_request(
            **_typed_request_kwargs(coordinator_u64)
        )
        coordinator_source_set_sha256 = _source_lineage()[
            "source_set_sha256"
        ]
        coordinator_manifest_raw, _ = _read_manifest_document(manifest_path)
        coordinator_manifest_sha256 = sha256(
            coordinator_manifest_raw
        ).hexdigest()
        stable_lineage: tuple[object, ...] | None = None
        for process_role, process_ordinal in _formal_launch_schedule(
            FORMAL_PERFORMANCE_PLAN
        ):
            document, outer_duration_ns = _launch_one_process(
                process_role=process_role,
                process_ordinal=process_ordinal,
                native_manifest_path=manifest_path,
                expected_backend_evidence_sha256=expected_hash,
            )
            if process_role == "RUNNER":
                bundle = _validate_runner_bundle(
                    document,
                    runner_process_ordinal=process_ordinal,
                )
                if (
                    bundle["lineage"]["source"]["source_set_sha256"]
                    != coordinator_source_set_sha256
                    or bundle["lineage"]["backend"][
                        "native_manifest_sha256"
                    ]
                    != coordinator_manifest_sha256
                ):
                    raise PerformanceRunError(
                        "PERFORMANCE_COORDINATOR_LINEAGE_MISMATCH"
                    )
                current_lineage = _stable_lineage_identity(bundle["lineage"])
                if stable_lineage is None:
                    stable_lineage = current_lineage
                elif current_lineage != stable_lineage:
                    raise PerformanceRunError(
                        "PERFORMANCE_CROSS_PROCESS_LINEAGE_MISMATCH"
                    )
                runner_bundles.append(bundle)
                output_hashes.update(
                    _write_runner_artifacts(
                        destination,
                        bundle,
                        runner_process_ordinal=process_ordinal,
                    )
                )
            else:
                if (
                    document.get("status") != "PASS"
                    or document.get("schema_version")
                    != PERFORMANCE_COLD_SIDECAR_SCHEMA_VERSION
                    or document.get("w64_warmup_count") != 0
                    or type(document.get("record")) is not dict
                    or type(document.get("lineage")) is not dict
                ):
                    reason = document.get("failure_reason_code")
                    raise PerformanceRunError(
                        reason
                        if type(reason) is str
                        else "PERFORMANCE_COLD_BUNDLE_INVALID"
                    )
                _validate_lineage(document["lineage"])
                if _stable_lineage_identity(document["lineage"]) != stable_lineage:
                    raise PerformanceRunError(
                        "PERFORMANCE_CROSS_PROCESS_LINEAGE_MISMATCH"
                    )
                _validate_stage_record(
                    document["record"],
                    fixture_id="U64",
                    plan=FORMAL_PERFORMANCE_PLAN,
                )
                _validate_decision_sidecar_record(
                    document["record"],
                    fixture_id="U64",
                    expected_document=coordinator_u64_decision,
                    expected_request_binding=(
                        coordinator_u64_request_binding
                    ),
                    expected_request_sha256=(
                        coordinator_u64_request_sha256
                    ),
                    expected_combined_backend_sha256=document["lineage"][
                        "backend"
                    ]["combined_backend_evidence_sha256"],
                )
                cold_samples.append(outer_duration_ns)
                cold_records.append(
                    {
                        "outer_process_elapsed_ns": outer_duration_ns,
                        **document,
                    }
                )

        first_bundle = runner_bundles[0]
        first_raw = first_bundle["raw_receipts"]["e2e"]
        if gc.isenabled() is not True:
            raise PerformanceRunError("PERFORMANCE_GC_POLICY_INVALID")
        cold_raw = _build_raw_receipt(
            stage="COLD_E2E",
            runner_process_ordinal=1,
            samples_ns=cold_samples,
            backend_evidence_sha256=first_raw["backend_evidence_sha256"],
            corpus=load_corpus("U64"),
            golden=load_reference_golden("U64"),
        )
        cold_sidecar = {
            "expected_samples": FORMAL_PERFORMANCE_PLAN.cold_e2e_samples,
            "records": cold_records,
            "schema_version": PERFORMANCE_COLD_SIDECAR_SCHEMA_VERSION,
            "status": "PASS",
            "w64_warmup_count_per_process": 0,
        }
        cold_directory = destination / "cold"
        cold_directory.mkdir()
        output_hashes["cold/raw-cold-e2e.json"] = _write_canonical(
            cold_directory / "raw-cold-e2e.json",
            cold_raw,
        )
        output_hashes["cold/cold-e2e-semantic-sidecar.json"] = _write_canonical(
            cold_directory / "cold-e2e-semantic-sidecar.json",
            cold_sidecar,
        )
        aggregate = _component_aggregate(
            runner_bundles=runner_bundles,
            cold_samples_ns=cold_samples,
            cold_records=cold_records,
            output_file_sha256=output_hashes,
        )
        _write_canonical(aggregate_path, aggregate)
    except PerformanceRunError as error:
        failure = _failed_component_aggregate(
            reason_code=error.reason_code,
            runner_processes_completed=len(runner_bundles),
            cold_processes_completed=len(cold_records),
            output_file_sha256=output_hashes,
        )
        _write_canonical(aggregate_path, failure)
        raise
    except Exception as error:
        reason_code = "PERFORMANCE_COORDINATOR_UNEXPECTED_FAILURE"
        failure = _failed_component_aggregate(
            reason_code=reason_code,
            runner_processes_completed=len(runner_bundles),
            cold_processes_completed=len(cold_records),
            output_file_sha256=output_hashes,
        )
        _write_canonical(aggregate_path, failure)
        raise PerformanceRunError(reason_code) from error
    return aggregate_path


__all__ = (
    "BATCH_64_SLO_NS",
    "E2E_SLO_NS",
    "FORMAL_PERFORMANCE_PLAN",
    "PERFORMANCE_COMPONENT_AGGREGATE_SCHEMA_VERSION",
    "PerformanceRunError",
    "SINGLE_CALL_SLO_NS",
    "run_formal_performance",
)
