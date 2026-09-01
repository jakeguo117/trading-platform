"""Formal T1 watchdog evidence for the fixed five-second P1 workflow.

This module exercises the real process supervisor with one deliberately
uncooperative, late-writing child.  It has no provider, broker, order, build,
network, or backend-selection authority.  A passing receipt qualifies only
the T1 timeout/late-publication/cleanup gate; it never qualifies P1 itself.
"""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import signal
import stat
import time
from typing import Mapping

from benchmarks.crr_p1_contract import (
    load_corpus,
    validate_raw_sample_receipt,
)
from benchmarks.crr_p1_decision_contract import load_decision_semantic_golden
from benchmarks import crr_p1_performance as performance
from gld_research_core.p1_decision_child import encode_p1_decision_request
from gld_research_core.p1_process_supervisor import (
    P1_PROCESS_TIMEOUT_NS,
    P1_QUALIFICATION_CEILING_NS,
    P1_REASON_HARD_TIMEOUT,
    P1_TERMINATE_GRACE_NS,
    _make_p1_workflow_process_supervisor,
)


T1_PREFLIGHT_SCHEMA_VERSION = "GLD_CRR_P1_T1_PREFLIGHT_V1"
T1_REQUEST_SCHEMA_VERSION = "GLD_CRR_P1_T1_REQUEST_V1"
T1_CASE_ID = "T1_UNCOOPERATIVE_LATE_WRITER_V1"
T1_PREFLIGHT_STATUS = "T1_PREFLIGHT_PASS"
T1_LATE_WRITE_OFFSET_NS = 10_000_000
T1_OUTPUT_FILENAME = "t1-preflight.json"

_ROOT = Path(__file__).resolve().parents[1]
_SHA256_HEX_LENGTH = 64
_MAX_EVIDENCE_FILE_BYTES = 8 * 1024 * 1024
_MAX_EVIDENCE_SET_BYTES = 64 * 1024 * 1024
_CRITICAL_PERFORMANCE_SOURCE_PATHS = (
    "src/gld_research_core/p1_decision_child.py",
    "src/gld_research_core/p1_process_supervisor.py",
)
_T1_SOURCE_PATHS = (
    "2026-08-28-2046-gld-crr-p1-benchmark-contract-v0.1.md",
    "benchmarks/crr_p1_t1_preflight.py",
    "src/gld_research_core/p1_decision_child.py",
    "src/gld_research_core/p1_process_supervisor.py",
    "tools/run_crr_p1_t1_preflight.py",
)


class T1PreflightError(RuntimeError):
    """One stable fail-closed T1 preflight reason."""

    def __init__(self, reason_code: str) -> None:
        if (
            type(reason_code) is not str
            or not reason_code
            or not reason_code.replace("_", "").isalnum()
            or reason_code != reason_code.upper()
        ):
            raise ValueError("invalid T1 preflight reason")
        self.reason_code = reason_code
        super().__init__(reason_code)


def _canonical_json_bytes(value: object, *, newline: bool) -> bytes:
    suffix = b"\n" if newline else b""
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii") + suffix
    except (RecursionError, TypeError, ValueError, UnicodeEncodeError) as error:
        raise T1PreflightError("T1_CANONICAL_JSON_INVALID") from error


def _canonical_sha256(value: object) -> str:
    return sha256(_canonical_json_bytes(value, newline=False)).hexdigest()


def _file_sha256(path: Path) -> str:
    try:
        return sha256(path.resolve(strict=True).read_bytes()).hexdigest()
    except OSError as error:
        raise T1PreflightError("T1_SOURCE_BINDING_INVALID") from error


def _safe_relative_output_path(value: object) -> PurePosixPath:
    if type(value) is not str or not value:
        raise T1PreflightError("T1_PERFORMANCE_BINDING_INVALID")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise T1PreflightError("T1_PERFORMANCE_BINDING_INVALID")
    return path


def _t1_source_lineage() -> dict[str, object]:
    files = {
        relative: _file_sha256(_ROOT / relative)
        for relative in _T1_SOURCE_PATHS
    }
    return {"files": files, "source_set_sha256": _canonical_sha256(files)}


def _expected_performance_output_paths() -> frozenset[str]:
    paths = {"cold/cold-e2e-semantic-sidecar.json", "cold/raw-cold-e2e.json"}
    for ordinal in (1, 2):
        prefix = f"runner-{ordinal:02d}"
        paths.update(
            {
                f"{prefix}/e2e-semantic-sidecar.json",
                f"{prefix}/lineage.json",
                f"{prefix}/raw-batch-64.json",
                f"{prefix}/raw-e2e.json",
                f"{prefix}/runner-aggregate.json",
                f"{prefix}/w64-warmup-sidecar.json",
            }
        )
        paths.update(
            f"{prefix}/raw-single-call-{call_ordinal:02d}.json"
            for call_ordinal in range(1, 65)
        )
    return frozenset(paths)


def _require_sha256(value: object, reason_code: str) -> str:
    if (
        type(value) is not str
        or len(value) != _SHA256_HEX_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise T1PreflightError(reason_code)
    return value


def _directory_open_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise T1PreflightError("T1_NOFOLLOW_NOT_SUPPORTED")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_evidence_root(component_aggregate_path: Path) -> int:
    absolute = Path(os.path.abspath(os.fspath(component_aggregate_path)))
    if absolute.name != "component-aggregate.json":
        raise T1PreflightError("T1_PERFORMANCE_BINDING_INVALID")
    descriptor: int | None = None
    try:
        descriptor = os.open(absolute.parent, _directory_open_flags())
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("evidence root is not a directory")
        return descriptor
    except OSError as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise T1PreflightError("T1_PERFORMANCE_BINDING_INVALID") from error


def _open_regular_at(root_descriptor: int, relative: PurePosixPath) -> int:
    current: int | None = None
    descriptor: int | None = None
    try:
        current = os.dup(root_descriptor)
        for part in relative.parts[:-1]:
            following = os.open(part, _directory_open_flags(), dir_fd=current)
            os.close(current)
            current = following
        descriptor = os.open(
            relative.parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=current,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_EVIDENCE_FILE_BYTES
        ):
            raise T1PreflightError("T1_EVIDENCE_FILE_INVALID")
        result = descriptor
        descriptor = None
        return result
    except T1PreflightError:
        raise
    except OSError as error:
        raise T1PreflightError("T1_EVIDENCE_PATH_INVALID") from error
    finally:
        for owned in (descriptor, current):
            if owned is not None:
                try:
                    os.close(owned)
                except OSError:
                    pass


def _read_regular_at(root_descriptor: int, relative: PurePosixPath) -> bytes:
    descriptor = _open_regular_at(root_descriptor, relative)
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, min(1_048_576, _MAX_EVIDENCE_FILE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_EVIDENCE_FILE_BYTES:
                raise T1PreflightError("T1_EVIDENCE_FILE_INVALID")
    except T1PreflightError:
        raise
    except OSError as error:
        raise T1PreflightError("T1_EVIDENCE_READ_FAILED") from error
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    return b"".join(chunks)


def _parse_canonical_document(raw: bytes) -> dict[str, object]:
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as error:
        raise T1PreflightError("T1_PERFORMANCE_BINDING_INVALID") from error
    if type(document) is not dict or raw != _canonical_json_bytes(
        document,
        newline=True,
    ):
        raise T1PreflightError("T1_PERFORMANCE_BINDING_INVALID")
    return document


def _load_all_performance_documents(
    component_aggregate_path: Path,
) -> tuple[bytes, dict[str, object], dict[str, dict[str, object]]]:
    root_descriptor = _open_evidence_root(component_aggregate_path)
    try:
        aggregate_raw = _read_regular_at(
            root_descriptor,
            PurePosixPath("component-aggregate.json"),
        )
        aggregate = _parse_canonical_document(aggregate_raw)
        output_hashes = aggregate.get("output_file_sha256")
        if (
            type(output_hashes) is not dict
            or frozenset(output_hashes) != _expected_performance_output_paths()
        ):
            raise T1PreflightError("T1_PERFORMANCE_BINDING_INVALID")
        documents: dict[str, dict[str, object]] = {}
        total_bytes = len(aggregate_raw)
        for relative_value in sorted(output_hashes):
            relative = _safe_relative_output_path(relative_value)
            expected_sha256 = _require_sha256(
                output_hashes[relative_value],
                "T1_PERFORMANCE_OUTPUT_HASH_MISMATCH",
            )
            raw = _read_regular_at(root_descriptor, relative)
            total_bytes += len(raw)
            if total_bytes > _MAX_EVIDENCE_SET_BYTES:
                raise T1PreflightError("T1_EVIDENCE_SET_TOO_LARGE")
            if sha256(raw).hexdigest() != expected_sha256:
                raise T1PreflightError("T1_PERFORMANCE_OUTPUT_HASH_MISMATCH")
            documents[relative_value] = _parse_canonical_document(raw)
        return aggregate_raw, aggregate, documents
    finally:
        os.close(root_descriptor)


def _runner_bundle_from_documents(
    documents: Mapping[str, dict[str, object]],
    *,
    runner_process_ordinal: int,
) -> dict[str, object]:
    prefix = f"runner-{runner_process_ordinal:02d}"
    return {
        "e2e_semantic_sidecar": documents[
            f"{prefix}/e2e-semantic-sidecar.json"
        ],
        "lineage": documents[f"{prefix}/lineage.json"],
        "raw_receipts": {
            "batch_64": documents[f"{prefix}/raw-batch-64.json"],
            "e2e": documents[f"{prefix}/raw-e2e.json"],
            "single_call": [
                documents[f"{prefix}/raw-single-call-{ordinal:02d}.json"]
                for ordinal in range(1, 65)
            ],
        },
        "runner_aggregate": documents[f"{prefix}/runner-aggregate.json"],
        "runner_process_ordinal": runner_process_ordinal,
        "schema_version": performance.PERFORMANCE_RUNNER_SCHEMA_VERSION,
        "status": "PASS",
        "w64_warmup_sidecar": documents[
            f"{prefix}/w64-warmup-sidecar.json"
        ],
    }


def _validate_cold_evidence(
    documents: Mapping[str, dict[str, object]],
    *,
    stable_lineage: tuple[object, ...],
) -> tuple[list[int], list[dict[str, object]]]:
    raw_receipt = documents["cold/raw-cold-e2e.json"]
    sidecar = documents["cold/cold-e2e-semantic-sidecar.json"]
    try:
        validate_raw_sample_receipt(raw_receipt)
    except Exception as error:
        raise T1PreflightError("T1_COLD_EVIDENCE_INVALID") from error
    samples = raw_receipt.get("samples_ns")
    records = sidecar.get("records")
    if (
        raw_receipt.get("stage") != "COLD_E2E"
        or raw_receipt.get("runner_process_ordinal") != 1
        or type(samples) is not list
        or len(samples) != performance.FORMAL_PERFORMANCE_PLAN.cold_e2e_samples
        or set(sidecar)
        != {
            "expected_samples",
            "records",
            "schema_version",
            "status",
            "w64_warmup_count_per_process",
        }
        or sidecar.get("expected_samples")
        != performance.FORMAL_PERFORMANCE_PLAN.cold_e2e_samples
        or sidecar.get("schema_version")
        != performance.PERFORMANCE_COLD_SIDECAR_SCHEMA_VERSION
        or sidecar.get("status") != "PASS"
        or sidecar.get("w64_warmup_count_per_process") != 0
        or type(records) is not list
        or len(records) != performance.FORMAL_PERFORMANCE_PLAN.cold_e2e_samples
    ):
        raise T1PreflightError("T1_COLD_EVIDENCE_INVALID")

    u64 = load_corpus("U64")
    decision_loaded = load_decision_semantic_golden("U64")
    decision = performance._thaw_json(decision_loaded.document)
    request_binding = performance._expected_request_binding(u64, decision)
    _, request_sha256 = encode_p1_decision_request(
        **performance._typed_request_kwargs(u64)
    )
    validated_records: list[dict[str, object]] = []
    for process_ordinal, (sample, record) in enumerate(
        zip(samples, records, strict=True),
        start=1,
    ):
        if (
            type(record) is not dict
            or set(record)
            != {
                "lineage",
                "outer_process_elapsed_ns",
                "process_ordinal",
                "record",
                "schema_version",
                "status",
                "w64_warmup_count",
            }
            or record.get("outer_process_elapsed_ns") != sample
            or record.get("process_ordinal") != process_ordinal
            or record.get("schema_version")
            != performance.PERFORMANCE_COLD_SIDECAR_SCHEMA_VERSION
            or record.get("status") != "PASS"
            or record.get("w64_warmup_count") != 0
            or type(record.get("lineage")) is not dict
            or type(record.get("record")) is not dict
        ):
            raise T1PreflightError("T1_COLD_EVIDENCE_INVALID")
        try:
            lineage = performance._validate_lineage(record["lineage"])
            if performance._stable_lineage_identity(lineage) != stable_lineage:
                raise T1PreflightError("T1_PERFORMANCE_LINEAGE_INVALID")
            performance._validate_stage_record(
                record["record"],
                fixture_id="U64",
                plan=performance.FORMAL_PERFORMANCE_PLAN,
            )
            performance._validate_decision_sidecar_record(
                record["record"],
                fixture_id="U64",
                expected_document=decision,
                expected_request_binding=request_binding,
                expected_request_sha256=request_sha256,
                expected_combined_backend_sha256=lineage["backend"][
                    "combined_backend_evidence_sha256"
                ],
            )
        except T1PreflightError:
            raise
        except Exception as error:
            raise T1PreflightError("T1_COLD_EVIDENCE_INVALID") from error
        validated_records.append(record)
    return samples, validated_records


def _load_performance_binding(
    component_aggregate_path: Path,
) -> dict[str, object]:
    raw, aggregate, documents = _load_all_performance_documents(
        component_aggregate_path
    )
    output_hashes = aggregate.get("output_file_sha256")
    lineage_binding = aggregate.get("lineage_binding")
    if (
        aggregate.get("status") != "MEASUREMENT_COMPLETE"
        or aggregate.get("p1_overall_status") != "P1_PERFORMANCE_NOT_PASS"
        or aggregate.get("decision_status") != "NO_DECISION"
        or aggregate.get("actionable") is not False
        or aggregate.get("broker_order_count") != 0
        or aggregate.get("runner_process_count") != 2
        or aggregate.get("cold_semantic_record_count") != 30
        or aggregate.get("machine_qualification_pass") is not True
        or type(output_hashes) is not dict
        or frozenset(output_hashes) != _expected_performance_output_paths()
        or type(lineage_binding) is not dict
    ):
        raise T1PreflightError("T1_PERFORMANCE_BINDING_INVALID")
    runner_bundles = [
        _runner_bundle_from_documents(
            documents,
            runner_process_ordinal=ordinal,
        )
        for ordinal in (1, 2)
    ]
    try:
        for ordinal, bundle in enumerate(runner_bundles, start=1):
            performance._validate_runner_bundle(
                bundle,
                runner_process_ordinal=ordinal,
            )
        stable_lineage = performance._stable_lineage_identity(
            runner_bundles[0]["lineage"]
        )
        if performance._stable_lineage_identity(
            runner_bundles[1]["lineage"]
        ) != stable_lineage:
            raise T1PreflightError("T1_PERFORMANCE_LINEAGE_INVALID")
        cold_samples, cold_records = _validate_cold_evidence(
            documents,
            stable_lineage=stable_lineage,
        )
        recomputed = performance._component_aggregate(
            runner_bundles=runner_bundles,
            cold_samples_ns=cold_samples,
            cold_records=cold_records,
            output_file_sha256=output_hashes,
        )
    except T1PreflightError:
        raise
    except Exception as error:
        raise T1PreflightError("T1_PERFORMANCE_EVIDENCE_INVALID") from error
    if recomputed != aggregate:
        raise T1PreflightError("T1_PERFORMANCE_AGGREGATE_MISMATCH")

    first_lineage = runner_bundles[0]["lineage"]
    current_performance_source = performance._source_lineage()
    current_machine = performance._machine_receipt()
    current_runtime = performance._runtime_receipt()
    if (
        current_machine.get("machine_qualification_pass") is not True
        or first_lineage.get("source") != current_performance_source
        or first_lineage.get("machine") != current_machine
        or first_lineage.get("runtime") != current_runtime
    ):
        raise T1PreflightError("T1_CURRENT_ENVIRONMENT_NOT_BOUND")
    source_files = current_performance_source.get("files")
    if type(source_files) is not dict or any(
        source_files.get(relative) != _file_sha256(_ROOT / relative)
        for relative in _CRITICAL_PERFORMANCE_SOURCE_PATHS
    ):
        raise T1PreflightError("T1_CRITICAL_SOURCE_MISMATCH")
    return {
        "backend_sha256": lineage_binding["backend_sha256"],
        "component_aggregate_sha256": sha256(raw).hexdigest(),
        "machine": current_machine,
        "output_file_count": len(output_hashes),
        "performance_source": current_performance_source,
        "runtime": current_runtime,
    }


def _t1_request_bytes() -> bytes:
    return _canonical_json_bytes(
        {"case_id": T1_CASE_ID, "schema_version": T1_REQUEST_SCHEMA_VERSION},
        newline=False,
    )


def _t1_late_success_frame(token: str, request_sha256: str) -> bytes:
    artifact = {"case_id": T1_CASE_ID, "terminal": "LATE_SUCCESS"}
    return _canonical_json_bytes(
        {
            "actionable": False,
            "artifact": artifact,
            "artifact_sha256": _canonical_sha256(artifact),
            "broker_order_count": 0,
            "generation_token": token,
            "outcome": "SUCCESS",
            "reason_code": "T1_LATE_SUCCESS",
            "request_sha256": request_sha256,
            "schema_version": "GLD_P1_CHILD_FRAME_V1",
            "semantic_receipt": {"case_id": T1_CASE_ID},
        },
        newline=False,
    )


def _t1_uncooperative_late_writer(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_monotonic_ns: int,
) -> None:
    """Ignore TERM, publish after the deadline, then remain uncooperative."""

    del request_bytes
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    late_write_ns = deadline_monotonic_ns + T1_LATE_WRITE_OFFSET_NS
    while time.monotonic_ns() < late_write_ns:
        time.sleep(0.001)
    try:
        connection.send_bytes(_t1_late_success_frame(token, request_sha256))
    except OSError:
        pass
    while True:
        time.sleep(0.1)


def run_t1_watchdog_case() -> dict[str, object]:
    """Run the fixed real-process T1 case and return a verified evidence body."""

    if (
        P1_PROCESS_TIMEOUT_NS != 5_000_000_000
        or P1_QUALIFICATION_CEILING_NS != 5_250_000_000
        or P1_TERMINATE_GRACE_NS != 100_000_000
    ):
        raise T1PreflightError("T1_TIMEOUT_POLICY_CHANGED")
    request_bytes = _t1_request_bytes()
    request_sha256 = sha256(request_bytes).hexdigest()
    begin, run_issued, consume, finalize = _make_p1_workflow_process_supervisor(
        _t1_uncooperative_late_writer
    )
    wall_started_ns = time.monotonic_ns()
    ticket = begin()
    issued = run_issued(ticket, request_bytes, request_sha256)
    if issued is None:
        raise T1PreflightError("T1_SUPERVISOR_RESULT_NOT_ISSUED")
    result = consume(ticket, issued, request_sha256)
    if result is None:
        raise T1PreflightError("T1_SUPERVISOR_RESULT_NOT_CONSUMED")
    timing = finalize(ticket, request_sha256)
    wall_completed_ns = time.monotonic_ns()
    if timing is None:
        raise T1PreflightError("T1_WORKFLOW_NOT_FINALIZED")
    receipt = result.supervisor_receipt
    workflow_elapsed_ns = (
        timing.completed_monotonic_ns - timing.workflow_started_monotonic_ns
    )
    wall_elapsed_ns = wall_completed_ns - wall_started_ns
    if (
        result.status != "FAIL_CLOSED"
        or result.reason_code != P1_REASON_HARD_TIMEOUT
        or result.failure_origin != "DEADLINE"
        or result.child_reason_code is not None
        or result.semantic_receipt_bytes is not None
        or result.artifact_bytes is not None
        or result.artifact_sha256 is not None
        or result.actionable is not False
        or result.broker_order_count != 0
        or receipt.deadline_monotonic_ns
        != timing.deadline_monotonic_ns
        or receipt.supervisor_started_monotonic_ns
        != timing.workflow_started_monotonic_ns
        or receipt.elapsed_ns < P1_PROCESS_TIMEOUT_NS
        or receipt.elapsed_ns > P1_QUALIFICATION_CEILING_NS
        or workflow_elapsed_ns < P1_PROCESS_TIMEOUT_NS
        or workflow_elapsed_ns > P1_QUALIFICATION_CEILING_NS
        or wall_elapsed_ns < P1_PROCESS_TIMEOUT_NS
        or wall_elapsed_ns > P1_QUALIFICATION_CEILING_NS
        or timing.before_deadline is not False
        or receipt.deadline_reached is not True
        or receipt.messages_accepted != 0
        or receipt.messages_observed < 1
        or receipt.late_messages_suppressed < 1
        or receipt.terminate_sent is not True
        or receipt.kill_sent is not True
        or receipt.child_reaped is not True
        or receipt.reader_joined is not True
        or receipt.pipe_closed is not True
        or receipt.process_closed is not True
        or receipt.process_group_verified is not True
        or receipt.isolation_barrier_released is not True
        or receipt.process_group_gone is not True
        or receipt.descendant_leak_observed is not False
        or receipt.partial_result_published is not False
    ):
        raise T1PreflightError("T1_WATCHDOG_SEMANTICS_NOT_VERIFIED")
    return {
        "case_id": T1_CASE_ID,
        "request_sha256": request_sha256,
        "result": {
            "actionable": result.actionable,
            "artifact_absent": result.artifact_bytes is None,
            "broker_order_count": result.broker_order_count,
            "failure_origin": result.failure_origin,
            "reason_code": result.reason_code,
            "semantic_receipt_absent": result.semantic_receipt_bytes is None,
            "status": result.status,
        },
        "supervisor_receipt": asdict(receipt),
        "wall_elapsed_ns": wall_elapsed_ns,
        "workflow_timing": asdict(timing),
    }


def _prepare_output_parent(output_directory: Path) -> tuple[int, Path, str]:
    absolute = Path(os.path.abspath(os.fspath(output_directory)))
    leaf = absolute.name
    if not leaf or leaf in {".", ".."}:
        raise T1PreflightError("T1_OUTPUT_DIRECTORY_INVALID")
    try:
        parent_descriptor = os.open(
            absolute.parent,
            _directory_open_flags(),
        )
        if not stat.S_ISDIR(os.fstat(parent_descriptor).st_mode):
            raise OSError("output parent is not a directory")
        try:
            os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise T1PreflightError("T1_OUTPUT_DIRECTORY_EXISTS")
        return parent_descriptor, absolute.parent, leaf
    except T1PreflightError:
        if "parent_descriptor" in locals():
            os.close(parent_descriptor)
        raise
    except OSError as error:
        if "parent_descriptor" in locals():
            os.close(parent_descriptor)
        raise T1PreflightError("T1_OUTPUT_DIRECTORY_INVALID") from error


def _same_file_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _directory_path_matches_descriptor(path: Path, descriptor: int) -> bool:
    reopened: int | None = None
    try:
        reopened = os.open(path, _directory_open_flags())
        return _same_file_identity(os.fstat(reopened), os.fstat(descriptor))
    finally:
        if reopened is not None:
            os.close(reopened)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("short output write")
        offset += written


def _read_exact_payload(descriptor: int, expected_size: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= expected_size:
        chunk = os.read(descriptor, min(1_048_576, expected_size + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > expected_size:
            break
    return b"".join(chunks)


def _publish_new_output_directory(
    *,
    parent_descriptor: int,
    output_parent: Path,
    output_leaf: str,
    document: Mapping[str, object],
) -> Path:
    payload = _canonical_json_bytes(dict(document), newline=True)
    temporary_name = f".{T1_OUTPUT_FILENAME}.{os.getpid()}.tmp"
    output_descriptor: int | None = None
    temporary_descriptor: int | None = None
    final_descriptor: int | None = None
    temporary_created = False
    final_created = False
    directory_created = False
    directory_read_only = False
    try:
        if not _directory_path_matches_descriptor(
            output_parent,
            parent_descriptor,
        ):
            raise OSError("output parent identity changed")
        os.mkdir(output_leaf, mode=0o700, dir_fd=parent_descriptor)
        directory_created = True
        expected_metadata = os.stat(
            output_leaf,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        output_descriptor = os.open(
            output_leaf,
            _directory_open_flags(),
            dir_fd=parent_descriptor,
        )
        actual_metadata = os.fstat(output_descriptor)
        if (
            not stat.S_ISDIR(expected_metadata.st_mode)
            or (expected_metadata.st_dev, expected_metadata.st_ino)
            != (actual_metadata.st_dev, actual_metadata.st_ino)
        ):
            raise OSError("output directory identity changed")
        temporary_descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=output_descriptor,
        )
        temporary_created = True
        written_metadata = os.fstat(temporary_descriptor)
        if (
            not stat.S_ISREG(written_metadata.st_mode)
            or written_metadata.st_nlink != 1
        ):
            raise OSError("temporary output is not regular")
        _write_all(temporary_descriptor, payload)
        os.fsync(temporary_descriptor)
        os.fchmod(temporary_descriptor, 0o400)
        temporary_metadata = os.stat(
            temporary_name,
            dir_fd=output_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(temporary_metadata.st_mode)
            or stat.S_IMODE(temporary_metadata.st_mode) != 0o400
            or temporary_metadata.st_nlink != 1
            or not _same_file_identity(written_metadata, temporary_metadata)
        ):
            raise OSError("temporary output identity changed")
        os.link(
            temporary_name,
            T1_OUTPUT_FILENAME,
            src_dir_fd=output_descriptor,
            dst_dir_fd=output_descriptor,
            follow_symlinks=False,
        )
        final_created = True
        linked_temporary_metadata = os.stat(
            temporary_name,
            dir_fd=output_descriptor,
            follow_symlinks=False,
        )
        linked_final_metadata = os.stat(
            T1_OUTPUT_FILENAME,
            dir_fd=output_descriptor,
            follow_symlinks=False,
        )
        linked_held_metadata = os.fstat(temporary_descriptor)
        if (
            not stat.S_ISREG(linked_temporary_metadata.st_mode)
            or not stat.S_ISREG(linked_final_metadata.st_mode)
            or not stat.S_ISREG(linked_held_metadata.st_mode)
            or stat.S_IMODE(linked_temporary_metadata.st_mode) != 0o400
            or stat.S_IMODE(linked_final_metadata.st_mode) != 0o400
            or stat.S_IMODE(linked_held_metadata.st_mode) != 0o400
            or linked_temporary_metadata.st_nlink != 2
            or linked_final_metadata.st_nlink != 2
            or linked_held_metadata.st_nlink != 2
            or not _same_file_identity(
                written_metadata,
                linked_temporary_metadata,
            )
            or not _same_file_identity(written_metadata, linked_final_metadata)
        ):
            raise OSError("linked output identity changed")
        os.unlink(temporary_name, dir_fd=output_descriptor)
        temporary_created = False
        os.fchmod(output_descriptor, 0o500)
        directory_read_only = True
        final_descriptor = os.open(
            T1_OUTPUT_FILENAME,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=output_descriptor,
        )
        opened_final_metadata = os.fstat(final_descriptor)
        final_payload = _read_exact_payload(final_descriptor, len(payload))
        final_path_metadata = os.stat(
            T1_OUTPUT_FILENAME,
            dir_fd=output_descriptor,
            follow_symlinks=False,
        )
        retained_metadata = os.fstat(temporary_descriptor)
        if (
            not stat.S_ISREG(opened_final_metadata.st_mode)
            or not stat.S_ISREG(final_path_metadata.st_mode)
            or not stat.S_ISREG(retained_metadata.st_mode)
            or stat.S_IMODE(opened_final_metadata.st_mode) != 0o400
            or stat.S_IMODE(final_path_metadata.st_mode) != 0o400
            or stat.S_IMODE(retained_metadata.st_mode) != 0o400
            or opened_final_metadata.st_nlink != 1
            or final_path_metadata.st_nlink != 1
            or retained_metadata.st_nlink != 1
            or not _same_file_identity(written_metadata, opened_final_metadata)
            or not _same_file_identity(written_metadata, final_path_metadata)
            or not _same_file_identity(written_metadata, retained_metadata)
            or final_payload != payload
            or sha256(final_payload).digest() != sha256(payload).digest()
            or not _directory_path_matches_descriptor(
                output_parent,
                parent_descriptor,
            )
        ):
            raise OSError("published output verification failed")
        os.fsync(output_descriptor)
    except OSError as error:
        if output_descriptor is not None:
            if directory_read_only:
                try:
                    os.fchmod(output_descriptor, 0o700)
                except OSError:
                    pass
            for created, name in (
                (final_created, T1_OUTPUT_FILENAME),
                (temporary_created, temporary_name),
            ):
                if created:
                    try:
                        os.unlink(name, dir_fd=output_descriptor)
                    except OSError:
                        pass
            if directory_created:
                try:
                    current_output_metadata = os.stat(
                        output_leaf,
                        dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                    if _same_file_identity(
                        current_output_metadata,
                        os.fstat(output_descriptor),
                    ):
                        os.rmdir(output_leaf, dir_fd=parent_descriptor)
                except OSError:
                    pass
        raise T1PreflightError("T1_OUTPUT_WRITE_FAILED") from error
    finally:
        for descriptor in (
            final_descriptor,
            temporary_descriptor,
            output_descriptor,
        ):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    return output_parent / output_leaf / T1_OUTPUT_FILENAME


def run_formal_t1_preflight(
    *,
    performance_component_aggregate_path: Path,
    output_directory: Path,
) -> Path:
    """Bind and write one formal T1-only receipt without overwriting output."""

    if not isinstance(performance_component_aggregate_path, Path) or not isinstance(
        output_directory,
        Path,
    ):
        raise T1PreflightError("T1_CLI_INPUT_INVALID")
    parent_descriptor, output_parent, output_leaf = _prepare_output_parent(
        output_directory
    )
    try:
        performance_before = _load_performance_binding(
            performance_component_aggregate_path
        )
        t1_source_before = _t1_source_lineage()
        watchdog = run_t1_watchdog_case()
        performance_after = _load_performance_binding(
            performance_component_aggregate_path
        )
        t1_source_after = _t1_source_lineage()
        if (
            performance_before != performance_after
            or t1_source_before != t1_source_after
        ):
            raise T1PreflightError("T1_EVIDENCE_CHANGED_DURING_RUN")

        document: dict[str, object] = {
            "actionable": False,
            "broker_order_count": 0,
            "decision_status": "NO_DECISION",
            "p1_overall_status": "P1_PERFORMANCE_NOT_PASS",
            "performance_binding": performance_before,
            "qualification_scope": "T1_ONLY",
            "schema_version": T1_PREFLIGHT_SCHEMA_VERSION,
            "t1_preflight_status": T1_PREFLIGHT_STATUS,
            "t1_source": t1_source_before,
            "watchdog": watchdog,
        }
        return _publish_new_output_directory(
            parent_descriptor=parent_descriptor,
            output_parent=output_parent,
            output_leaf=output_leaf,
            document=document,
        )
    finally:
        os.close(parent_descriptor)


__all__ = [
    "T1PreflightError",
    "T1_PREFLIGHT_SCHEMA_VERSION",
    "T1_PREFLIGHT_STATUS",
    "run_formal_t1_preflight",
    "run_t1_watchdog_case",
]
