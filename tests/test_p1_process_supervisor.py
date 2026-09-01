from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import replace
import gc
from hashlib import sha256
import inspect
import json
import multiprocessing
from multiprocessing.connection import Connection
import os
from pathlib import Path
import pickle
import signal
import struct
import tempfile
import threading
import time
import unittest
import weakref

import gld_research_core.p1_process_supervisor as supervisor_module
from gld_normalizer.errors import NormalizationError
from gld_research_core.p1_process_supervisor import (
    P1_CHILD_FRAME_SCHEMA,
    P1_MAX_CHILD_FRAME_BYTES,
    P1_PROCESS_TIMEOUT_NS,
    P1_REASON_CHILD_CRASHED,
    P1_REASON_DESCENDANT_LEAK,
    P1_REASON_EXECUTION_FAILED,
    P1_REASON_HARD_TIMEOUT,
    P1_REASON_INPUT_INVALID,
    P1_REASON_MULTIPLE_OUTPUTS,
    P1_REASON_OUTPUT_MALFORMED,
    P1_REASON_OUTPUT_MISSING,
    P1_REASON_START_FAILED,
    P1_REASON_SUCCESS,
    _is_before_deadline,
    _make_p1_issued_process_supervisor,
    _make_p1_process_supervisor_for_test,
    _make_p1_workflow_process_supervisor,
    run_p1_process_supervisor,
)


ROOT = Path(__file__).resolve().parents[1]
REQUEST_BYTES = b'{"request_id":"p1-test"}'
REQUEST_SHA256 = sha256(REQUEST_BYTES).hexdigest()
INVALID_REQUEST_BYTES = b'{"request_id": "p1-test"}'
INVALID_REQUEST_SHA256 = sha256(INVALID_REQUEST_BYTES).hexdigest()

_ORIGINAL_IS_BEFORE_DEADLINE = supervisor_module._is_before_deadline
_FAIL_NEXT_DEADLINE_CHECK = False


def _is_before_deadline_fails_once(now_ns: int, deadline_ns: int) -> bool:
    global _FAIL_NEXT_DEADLINE_CHECK
    if _FAIL_NEXT_DEADLINE_CHECK:
        _FAIL_NEXT_DEADLINE_CHECK = False
        raise RuntimeError("synthetic supervisor failure")
    return _ORIGINAL_IS_BEFORE_DEADLINE(now_ns, deadline_ns)


class _FakeMonotonicClock:
    def __init__(self, now_ns: int) -> None:
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, nanoseconds: int) -> None:
        self.now_ns += nanoseconds


class _QualificationPauseClock:
    def __init__(self, ready_path: Path) -> None:
        self.ready_path = ready_path
        self.lock = threading.Lock()
        self.first_ns: int | None = None
        self.now_ns = time.monotonic_ns()
        self.jumped = False

    def __call__(self) -> int:
        with self.lock:
            self.now_ns += 1_000_000
            if self.first_ns is None:
                self.first_ns = self.now_ns
            if self.ready_path.exists() and not self.jumped:
                self.now_ns = (
                    self.first_ns
                    + supervisor_module.P1_QUALIFICATION_CEILING_NS
                    + 1
                )
                self.jumped = True
            return self.now_ns


class _PersistentBaseExceptionClock:
    def __init__(self, *, fail_from_call: int) -> None:
        self.fail_from_call = fail_from_call
        self.calls = 0
        self.now_ns = time.monotonic_ns()

    def __call__(self) -> int:
        self.calls += 1
        if self.calls >= self.fail_from_call:
            raise SystemExit(f"clock-failure-{self.calls}")
        self.now_ns += 1_000_000
        return self.now_ns


class _DeadlineCrossingInvalidParser:
    def __init__(self, clock: _FakeMonotonicClock) -> None:
        self.clock = clock

    def __call__(self, raw: object, *, maximum: int) -> object:
        del raw, maximum
        self.clock.advance(P1_PROCESS_TIMEOUT_NS)
        raise ValueError("synthetic validation failure at deadline")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _frame(
    generation_token: str,
    request_sha256: str,
    *,
    outcome: str = "SUCCESS",
    reason_code: str = "CHILD_PASS",
    semantic_receipt: object | None = None,
    artifact: object | None = None,
    artifact_sha256: str | None = None,
    schema_version: str = P1_CHILD_FRAME_SCHEMA,
) -> bytes:
    if outcome == "SUCCESS":
        semantic_receipt = (
            {"receipt_kind": "synthetic_test"}
            if semantic_receipt is None
            else semantic_receipt
        )
        artifact = {"value": 1} if artifact is None else artifact
        if artifact_sha256 is None:
            artifact_sha256 = sha256(_canonical(artifact)).hexdigest()
    else:
        semantic_receipt = None
        artifact = None
        artifact_sha256 = None
    return _canonical(
        {
            "actionable": False,
            "artifact": artifact,
            "artifact_sha256": artifact_sha256,
            "broker_order_count": 0,
            "generation_token": generation_token,
            "outcome": outcome,
            "reason_code": reason_code,
            "request_sha256": request_sha256,
            "schema_version": schema_version,
            "semantic_receipt": semantic_receipt,
        }
    )


def _send_and_close(connection: object, payload: bytes) -> None:
    try:
        connection.send_bytes(payload)
    finally:
        connection.close()


def _target_success(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del request_bytes, deadline_ns
    artifact = {
        "daemon": multiprocessing.current_process().daemon,
        "pid": os.getpid(),
        "pipe_readable": connection.readable,
        "pipe_writable": connection.writable,
    }
    _send_and_close(
        connection,
        _frame(token, request_sha256, artifact=artifact),
    )


def _target_echo_deadline(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del request_bytes
    _send_and_close(
        connection,
        _frame(
            token,
            request_sha256,
            artifact={
                "deadline_monotonic_ns": deadline_ns,
                "pid": os.getpid(),
            },
        ),
    )


def _target_execution_failed(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del request_bytes, deadline_ns
    _send_and_close(
        connection,
        _frame(
            token,
            request_sha256,
            outcome="FAIL",
            reason_code="SYNTHETIC_EXECUTION_FAILED",
        ),
    )


def _target_clean_no_output(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del token, request_bytes, request_sha256, deadline_ns
    connection.close()


def _target_crash(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del connection, token, request_bytes, request_sha256, deadline_ns
    os._exit(7)


def _target_forever(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del connection, token, request_bytes, request_sha256, deadline_ns
    while True:
        time.sleep(0.1)


def _target_ignore_term_forever(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del connection, token, request_bytes, request_sha256, deadline_ns
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(0.1)


def _target_ignore_term_late_valid(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del request_bytes
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    target_ns = deadline_ns + 10_000_000
    while time.monotonic_ns() < target_ns:
        time.sleep(0.001)
    _send_and_close(connection, _frame(token, request_sha256))
    while True:
        time.sleep(0.1)


def _target_malformed(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del token, request_bytes, request_sha256, deadline_ns
    _send_and_close(connection, b"{}")


def _target_noncanonical(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del request_bytes, deadline_ns
    canonical = _frame(token, request_sha256)
    value = json.loads(canonical)
    payload = json.dumps(value, sort_keys=True).encode("ascii")
    _send_and_close(connection, payload)


def _target_oversize(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del token, request_bytes, request_sha256, deadline_ns
    try:
        connection.send_bytes(b"x" * (P1_MAX_CHILD_FRAME_BYTES + 1))
    except OSError:
        pass
    finally:
        connection.close()


def _target_valid_frame_above_frozen_limit(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del request_bytes, deadline_ns
    artifact = {"padding": "x" * 1_048_500}
    _send_and_close(
        connection,
        _frame(token, request_sha256, artifact=artifact),
    )


def _target_wrong_token(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del token, request_bytes, deadline_ns
    _send_and_close(connection, _frame("0" * 64, request_sha256))


def _target_wrong_request_hash(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del request_bytes, request_sha256, deadline_ns
    _send_and_close(connection, _frame(token, "0" * 64))


def _target_wrong_schema(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del request_bytes, deadline_ns
    _send_and_close(
        connection,
        _frame(token, request_sha256, schema_version="RELAXED_V2"),
    )


def _target_wrong_artifact_hash(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del request_bytes, deadline_ns
    _send_and_close(
        connection,
        _frame(token, request_sha256, artifact_sha256="0" * 64),
    )


def _target_two_frames(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del request_bytes, deadline_ns
    payload = _frame(token, request_sha256)
    try:
        connection.send_bytes(payload)
        connection.send_bytes(payload)
    finally:
        connection.close()


def _target_partial_payload(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del token, request_bytes, request_sha256, deadline_ns
    os.write(connection.fileno(), struct.pack("!i", 100))
    os.write(connection.fileno(), b"{")
    connection.close()


def _target_duplicate_keys(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del token, request_bytes, request_sha256, deadline_ns
    _send_and_close(connection, b'{"x":1,"x":1}')


def _target_float_payload(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del request_bytes, deadline_ns
    artifact = {"value": 1.5}
    payload = json.dumps(
        {
            "actionable": False,
            "artifact": artifact,
            "artifact_sha256": sha256(_canonical(artifact)).hexdigest(),
            "broker_order_count": 0,
            "generation_token": token,
            "outcome": "SUCCESS",
            "reason_code": "CHILD_PASS",
            "request_sha256": request_sha256,
            "schema_version": P1_CHILD_FRAME_SCHEMA,
            "semantic_receipt": {"value": 1},
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    _send_and_close(connection, payload)


def _target_nan_payload(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del request_bytes, deadline_ns
    payload = (
        b'{"actionable":false,"artifact":{"value":NaN},'
        b'"artifact_sha256":"' + b"0" * 64 + b'",'
        b'"broker_order_count":0,"generation_token":"'
        + token.encode("ascii")
        + b'","outcome":"SUCCESS","reason_code":"CHILD_PASS",'
        b'"request_sha256":"'
        + request_sha256.encode("ascii")
        + b'","schema_version":"'
        + P1_CHILD_FRAME_SCHEMA.encode("ascii")
        + b'","semantic_receipt":{"value":1}}'
    )
    _send_and_close(connection, payload)


def _target_deadline_equality(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del request_bytes
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while time.monotonic_ns() < deadline_ns:
        time.sleep(0.001)
    _send_and_close(connection, _frame(token, request_sha256))


def _target_heavy_near_deadline_frame(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del request_bytes
    artifact = {"values": [0] * 99_000}
    payload = _frame(token, request_sha256, artifact=artifact)
    target_ns = deadline_ns - 10_000_000
    while time.monotonic_ns() < target_ns:
        time.sleep(0.001)
    _send_and_close(connection, payload)


def _target_leaves_ordinary_descendant(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del deadline_ns
    request = json.loads(request_bytes)
    pid_path = request["descendant_pid_path"]
    descendant_pid = os.fork()
    if descendant_pid == 0:
        connection.close()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while True:
            time.sleep(0.1)
    with open(pid_path, "w", encoding="ascii") as output:
        output.write(str(descendant_pid))
        output.flush()
        os.fsync(output.fileno())
    _send_and_close(connection, _frame(token, request_sha256))


def _target_uncooperative_group(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del token, request_sha256, deadline_ns
    request = json.loads(request_bytes)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    descendant_pid = os.fork()
    if descendant_pid == 0:
        connection.close()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while True:
            time.sleep(0.1)
    process_ids_path = Path(request["process_ids_path"])
    temporary_path = process_ids_path.with_suffix(".tmp")
    with open(temporary_path, "w", encoding="ascii") as output:
        json.dump(
            {"child_pid": os.getpid(), "descendant_pid": descendant_pid},
            output,
            sort_keys=True,
        )
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary_path, process_ids_path)
    while True:
        time.sleep(0.1)


def _target_interrupts_supervisor(
    connection: object,
    token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_ns: int,
) -> None:
    del connection, token, request_sha256, deadline_ns
    request = json.loads(request_bytes)
    os.kill(request["supervisor_pid"], signal.SIGINT)
    while True:
        time.sleep(0.1)


class P1ProcessSupervisorTests(unittest.TestCase):
    def runner(self, target: object) -> object:
        return _make_p1_process_supervisor_for_test(target)

    def issued_runner(self, target: object) -> tuple[object, object]:
        return _make_p1_issued_process_supervisor(target)

    def workflow_runner(self, target: object) -> tuple[object, ...]:
        return _make_p1_workflow_process_supervisor(target)

    def workflow_runner_with_clock(
        self,
        target: object,
        clock: object,
    ) -> tuple[object, ...]:
        original = supervisor_module.time.monotonic_ns
        supervisor_module.time.monotonic_ns = clock
        try:
            return self.workflow_runner(target)
        finally:
            supervisor_module.time.monotonic_ns = original

    def assert_fail_closed(self, result: object, reason_code: str) -> None:
        self.assertEqual(result.status, "FAIL_CLOSED")
        self.assertEqual(result.reason_code, reason_code)
        self.assertIsNone(result.semantic_receipt_bytes)
        self.assertIsNone(result.artifact_bytes)
        self.assertIsNone(result.artifact_sha256)
        self.assertFalse(result.actionable)
        self.assertEqual(result.broker_order_count, 0)
        self.assertFalse(result.supervisor_receipt.partial_result_published)
        self.assertEqual(
            result.supervisor_receipt.qualification_status,
            "NOT_CLAIMED",
        )

    def test_success_requires_clean_exit_one_frame_and_reconstruction(self) -> None:
        result = self.runner(_target_success)(REQUEST_BYTES, REQUEST_SHA256)

        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.reason_code, P1_REASON_SUCCESS)
        artifact = json.loads(result.artifact_bytes)
        self.assertNotEqual(artifact["pid"], os.getpid())
        self.assertFalse(artifact["daemon"])
        self.assertFalse(artifact["pipe_readable"])
        self.assertTrue(artifact["pipe_writable"])
        receipt = result.supervisor_receipt
        self.assertEqual(receipt.child_exitcode, 0)
        self.assertEqual(receipt.messages_observed, 1)
        self.assertEqual(receipt.messages_accepted, 1)
        self.assertEqual(receipt.late_messages_suppressed, 0)
        self.assertTrue(receipt.child_reaped)
        self.assertTrue(receipt.reader_joined)
        self.assertTrue(receipt.pipe_closed)
        self.assertTrue(receipt.process_closed)
        self.assertEqual(receipt.child_pid, artifact["pid"])
        self.assertEqual(receipt.child_process_group_id, artifact["pid"])
        self.assertEqual(receipt.child_session_id, artifact["pid"])
        self.assertNotEqual(
            receipt.child_process_group_id,
            receipt.parent_process_group_id,
        )
        self.assertNotEqual(receipt.child_session_id, receipt.parent_session_id)
        self.assertTrue(receipt.process_group_verified)
        self.assertTrue(receipt.isolation_barrier_released)
        self.assertTrue(receipt.process_group_gone)
        self.assertFalse(receipt.descendant_leak_observed)

    def test_public_runner_explicitly_reports_missing_production_child(self) -> None:
        result = run_p1_process_supervisor(REQUEST_BYTES, REQUEST_SHA256)
        self.assert_fail_closed(result, P1_REASON_EXECUTION_FAILED)
        self.assertEqual(result.failure_origin, "CHILD_DECLARED")
        self.assertEqual(
            result.child_reason_code,
            supervisor_module.P1_PRODUCTION_CHILD_STATUS,
        )

    def test_invalid_requests_never_start_a_child(self) -> None:
        cases = (
            (b'{"request_id": "p1-test"}', REQUEST_SHA256),
            (b'{"request_id":1.5}', sha256(b'{"request_id":1.5}').hexdigest()),
            (b'{"request_id":1,"request_id":1}', "0" * 64),
            (REQUEST_BYTES, "0" * 64),
            (bytearray(REQUEST_BYTES), REQUEST_SHA256),
            (
                b"{" + b'"x":' + b"[" * 1_100 + b"0" + b"]" * 1_100 + b"}",
                "0" * 64,
            ),
            (b"x" * 1_048_577, "0" * 64),
        )
        runner = self.runner(_target_success)
        for request_bytes, request_hash in cases:
            with self.subTest(request_bytes=request_bytes):
                result = runner(request_bytes, request_hash)
                self.assert_fail_closed(result, P1_REASON_INPUT_INVALID)
                self.assertFalse(result.supervisor_receipt.child_started)

    def test_clean_no_output_crash_and_execution_failure_are_distinct(self) -> None:
        cases = (
            (_target_clean_no_output, P1_REASON_OUTPUT_MISSING),
            (_target_crash, P1_REASON_CHILD_CRASHED),
            (_target_execution_failed, P1_REASON_EXECUTION_FAILED),
        )
        for target, reason in cases:
            with self.subTest(target=target.__name__):
                result = self.runner(target)(REQUEST_BYTES, REQUEST_SHA256)
                self.assert_fail_closed(result, reason)

    def test_malformed_frames_never_publish_a_partial_winner(self) -> None:
        targets = (
            _target_malformed,
            _target_noncanonical,
            _target_oversize,
            _target_wrong_token,
            _target_wrong_request_hash,
            _target_wrong_schema,
            _target_wrong_artifact_hash,
            _target_partial_payload,
            _target_duplicate_keys,
            _target_float_payload,
            _target_nan_payload,
        )
        for target in targets:
            with self.subTest(target=target.__name__):
                result = self.runner(target)(REQUEST_BYTES, REQUEST_SHA256)
                self.assert_fail_closed(result, P1_REASON_OUTPUT_MALFORMED)
                self.assertEqual(result.supervisor_receipt.messages_accepted, 0)

    def test_two_frames_invalidates_even_an_initial_valid_frame(self) -> None:
        result = self.runner(_target_two_frames)(REQUEST_BYTES, REQUEST_SHA256)
        self.assert_fail_closed(result, P1_REASON_MULTIPLE_OUTPUTS)
        self.assertEqual(result.supervisor_receipt.messages_observed, 2)
        self.assertEqual(result.supervisor_receipt.messages_accepted, 0)

    def test_hard_timeout_kills_sigterm_ignoring_late_writer(self) -> None:
        result = self.runner(_target_ignore_term_late_valid)(
            REQUEST_BYTES,
            REQUEST_SHA256,
        )

        self.assert_fail_closed(result, P1_REASON_HARD_TIMEOUT)
        receipt = result.supervisor_receipt
        self.assertGreaterEqual(receipt.elapsed_ns, P1_PROCESS_TIMEOUT_NS)
        self.assertLessEqual(receipt.elapsed_ns, 5_250_000_000)
        self.assertEqual(receipt.messages_accepted, 0)
        self.assertGreaterEqual(receipt.late_messages_suppressed, 1)
        self.assertTrue(receipt.terminate_sent)
        self.assertTrue(receipt.kill_sent)
        self.assertTrue(receipt.child_reaped)
        self.assertTrue(receipt.reader_joined)
        self.assertTrue(receipt.pipe_closed)
        before = result
        time.sleep(0.1)
        self.assertEqual(result, before)

    def test_forever_worker_is_terminated_and_reaped(self) -> None:
        result = self.runner(_target_forever)(REQUEST_BYTES, REQUEST_SHA256)
        self.assert_fail_closed(result, P1_REASON_HARD_TIMEOUT)
        receipt = result.supervisor_receipt
        self.assertTrue(receipt.terminate_sent)
        self.assertFalse(receipt.kill_sent)
        self.assertTrue(receipt.child_reaped)
        self.assertTrue(receipt.reader_joined)
        self.assertTrue(receipt.pipe_closed)

    def test_deadline_is_half_open_and_equality_is_not_accepted(self) -> None:
        self.assertTrue(_is_before_deadline(99, 100))
        self.assertFalse(_is_before_deadline(100, 100))
        self.assertFalse(_is_before_deadline(101, 100))

        result = self.runner(_target_deadline_equality)(
            REQUEST_BYTES,
            REQUEST_SHA256,
        )
        self.assert_fail_closed(result, P1_REASON_HARD_TIMEOUT)
        self.assertTrue(result.supervisor_receipt.deadline_reached)
        self.assertEqual(result.supervisor_receipt.messages_accepted, 0)

    def test_heavy_frame_received_before_deadline_is_not_accepted_after_it(self) -> None:
        result = self.runner(_target_heavy_near_deadline_frame)(
            REQUEST_BYTES,
            REQUEST_SHA256,
        )

        self.assert_fail_closed(result, P1_REASON_HARD_TIMEOUT)
        receipt = result.supervisor_receipt
        self.assertEqual(receipt.messages_observed, 1)
        self.assertEqual(receipt.late_messages_suppressed, 0)
        self.assertEqual(receipt.messages_accepted, 0)

    def test_factory_freezes_request_frame_and_termination_policy(self) -> None:
        success_runner = self.runner(_target_success)
        frame_runner = self.runner(_target_valid_frame_above_frozen_limit)
        timeout_runner = self.runner(_target_ignore_term_late_valid)
        originals = (
            supervisor_module.P1_PROCESS_TIMEOUT_NS,
            supervisor_module.P1_QUALIFICATION_CEILING_NS,
            supervisor_module.P1_MAX_REQUEST_BYTES,
            supervisor_module.P1_MAX_CHILD_FRAME_BYTES,
            supervisor_module.P1_TERMINATE_GRACE_NS,
            supervisor_module._is_before_deadline,
        )
        try:
            supervisor_module.P1_PROCESS_TIMEOUT_NS = 1
            supervisor_module.P1_QUALIFICATION_CEILING_NS = 2
            supervisor_module.P1_MAX_REQUEST_BYTES = 2_097_152
            supervisor_module.P1_MAX_CHILD_FRAME_BYTES = 2_097_152
            supervisor_module.P1_TERMINATE_GRACE_NS = 0
            supervisor_module._is_before_deadline = lambda *_: False

            oversized_request = (
                b'{"payload":"'
                + b"x" * supervisor_module.P1_MAX_REQUEST_BYTES
                + b'"}'
            )
            input_result = success_runner(
                oversized_request,
                sha256(oversized_request).hexdigest(),
            )
            self.assert_fail_closed(input_result, P1_REASON_INPUT_INVALID)
            self.assertFalse(input_result.supervisor_receipt.child_started)

            frame_result = frame_runner(REQUEST_BYTES, REQUEST_SHA256)
            self.assert_fail_closed(frame_result, P1_REASON_OUTPUT_MALFORMED)

            timeout_result = timeout_runner(REQUEST_BYTES, REQUEST_SHA256)
            self.assert_fail_closed(timeout_result, P1_REASON_HARD_TIMEOUT)
            self.assertGreaterEqual(
                timeout_result.supervisor_receipt.elapsed_ns,
                P1_PROCESS_TIMEOUT_NS + 80_000_000,
            )
            self.assertTrue(timeout_result.supervisor_receipt.kill_sent)
        finally:
            (
                supervisor_module.P1_PROCESS_TIMEOUT_NS,
                supervisor_module.P1_QUALIFICATION_CEILING_NS,
                supervisor_module.P1_MAX_REQUEST_BYTES,
                supervisor_module.P1_MAX_CHILD_FRAME_BYTES,
                supervisor_module.P1_TERMINATE_GRACE_NS,
                supervisor_module._is_before_deadline,
            ) = originals

    def test_ordinary_descendant_invalidates_result_and_is_reaped_by_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            pid_path = Path(temporary_directory) / "descendant.pid"
            request_bytes = _canonical(
                {"descendant_pid_path": str(pid_path)}
            )
            request_sha256 = sha256(request_bytes).hexdigest()
            descendant_pid: int | None = None
            try:
                result = self.runner(_target_leaves_ordinary_descendant)(
                    request_bytes,
                    request_sha256,
                )
                descendant_pid = int(pid_path.read_text(encoding="ascii"))

                self.assert_fail_closed(result, P1_REASON_DESCENDANT_LEAK)
                receipt = result.supervisor_receipt
                self.assertEqual(result.failure_origin, "DESCENDANT")
                self.assertTrue(receipt.descendant_leak_observed)
                self.assertTrue(receipt.process_group_verified)
                self.assertTrue(receipt.isolation_barrier_released)
                self.assertTrue(receipt.terminate_sent)
                self.assertTrue(receipt.process_group_gone)
                self.assertTrue(receipt.child_reaped)
                with self.assertRaises(ProcessLookupError):
                    os.kill(descendant_pid, 0)
            finally:
                if descendant_pid is not None:
                    try:
                        os.kill(descendant_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_keyboard_interrupt_cleans_child_pipe_reader_and_fd_before_reraise(self) -> None:
        baseline_children = {child.pid for child in multiprocessing.active_children()}
        baseline_fds = len(os.listdir("/dev/fd"))
        baseline_readers = {
            thread.ident
            for thread in threading.enumerate()
            if thread.name.startswith("gld-p1-reader-")
        }
        request_bytes = _canonical({"supervisor_pid": os.getpid()})
        request_sha256 = sha256(request_bytes).hexdigest()

        with self.assertRaises(KeyboardInterrupt):
            self.runner(_target_interrupts_supervisor)(
                request_bytes,
                request_sha256,
            )

        gc.collect()
        time.sleep(0.05)
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()},
            baseline_children,
        )
        self.assertEqual(len(os.listdir("/dev/fd")), baseline_fds)
        self.assertEqual(
            {
                thread.ident
                for thread in threading.enumerate()
                if thread.name.startswith("gld-p1-reader-")
            },
            baseline_readers,
        )

    def test_repeated_sigint_is_deferred_until_bounded_cleanup_finishes(self) -> None:
        self.assertEqual(
            self.runner(_target_success)(REQUEST_BYTES, REQUEST_SHA256).status,
            "SUCCESS",
        )
        baseline_children = {child.pid for child in multiprocessing.active_children()}
        baseline_fds = len(os.listdir("/dev/fd"))

        def send_interrupts() -> None:
            time.sleep(0.2)
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.02)
            os.kill(os.getpid(), signal.SIGINT)

        interrupter = threading.Thread(target=send_interrupts, daemon=False)
        interrupter.start()
        with self.assertRaises(KeyboardInterrupt):
            self.runner(_target_ignore_term_forever)(
                REQUEST_BYTES,
                REQUEST_SHA256,
            )
        interrupter.join(timeout=1.0)

        self.assertFalse(interrupter.is_alive())
        gc.collect()
        time.sleep(0.05)
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()},
            baseline_children,
        )
        self.assertEqual(len(os.listdir("/dev/fd")), baseline_fds)

    def test_injected_cleanup_baseexception_is_deferred_and_cleanup_retried(self) -> None:
        baseline_children = {child.pid for child in multiprocessing.active_children()}
        baseline_fds = len(os.listdir("/dev/fd"))
        base_process = multiprocessing.process.BaseProcess
        original_close = base_process.close
        injected = False

        def close_with_one_injected_exit(process: object) -> None:
            nonlocal injected
            if not injected:
                injected = True
                raise SystemExit("synthetic cleanup interruption")
            original_close(process)

        base_process.close = close_with_one_injected_exit
        try:
            with self.assertRaisesRegex(
                SystemExit,
                "synthetic cleanup interruption",
            ):
                self.runner(_target_success)(REQUEST_BYTES, REQUEST_SHA256)
        finally:
            base_process.close = original_close

        self.assertTrue(injected)
        gc.collect()
        time.sleep(0.05)
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()},
            baseline_children,
        )
        self.assertEqual(len(os.listdir("/dev/fd")), baseline_fds)

    def test_qualification_pause_still_reclaims_group_reader_pipe_and_process(
        self,
    ) -> None:
        self.assertEqual(
            self.runner(_target_success)(REQUEST_BYTES, REQUEST_SHA256).status,
            "SUCCESS",
        )
        baseline_children = {
            child.pid for child in multiprocessing.active_children()
        }
        baseline_fds = len(os.listdir("/dev/fd"))
        baseline_readers = {
            thread.ident
            for thread in threading.enumerate()
            if thread.name.startswith("gld-p1-reader-")
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            process_ids_path = Path(temporary_directory) / "process-ids.json"
            clock = _QualificationPauseClock(process_ids_path)
            original_monotonic_ns = supervisor_module.time.monotonic_ns
            supervisor_module.time.monotonic_ns = clock
            try:
                runner = self.runner(_target_uncooperative_group)
            finally:
                supervisor_module.time.monotonic_ns = original_monotonic_ns
            request_bytes = _canonical(
                {"process_ids_path": str(process_ids_path)}
            )
            request_sha256 = sha256(request_bytes).hexdigest()
            process_ids: dict[str, int] = {}
            try:
                result = runner(request_bytes, request_sha256)
                process_ids = json.loads(
                    process_ids_path.read_text(encoding="ascii")
                )

                self.assertTrue(clock.jumped)
                self.assert_fail_closed(result, P1_REASON_HARD_TIMEOUT)
                receipt = result.supervisor_receipt
                self.assertGreater(
                    receipt.elapsed_ns,
                    supervisor_module.P1_QUALIFICATION_CEILING_NS,
                )
                self.assertTrue(receipt.process_group_verified)
                self.assertTrue(receipt.isolation_barrier_released)
                self.assertTrue(receipt.terminate_sent)
                self.assertTrue(receipt.kill_sent)
                self.assertTrue(receipt.process_group_gone)
                self.assertTrue(receipt.child_reaped)
                self.assertTrue(receipt.reader_joined)
                self.assertTrue(receipt.pipe_closed)
                self.assertTrue(receipt.process_closed)
            finally:
                if not process_ids and process_ids_path.exists():
                    process_ids = json.loads(
                        process_ids_path.read_text(encoding="ascii")
                    )
                if process_ids:
                    try:
                        os.killpg(process_ids["child_pid"], signal.SIGKILL)
                    except ProcessLookupError:
                        pass

        gc.collect()
        time.sleep(0.05)
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()},
            baseline_children,
        )
        self.assertEqual(len(os.listdir("/dev/fd")), baseline_fds)
        self.assertEqual(
            {
                thread.ident
                for thread in threading.enumerate()
                if thread.name.startswith("gld-p1-reader-")
            },
            baseline_readers,
        )
        for process_id in process_ids.values():
            with self.assertRaises(ProcessLookupError):
                os.kill(process_id, 0)

    def test_cleanup_baseexception_after_qualification_pause_is_deferred(
        self,
    ) -> None:
        self.assertEqual(
            self.runner(_target_success)(REQUEST_BYTES, REQUEST_SHA256).status,
            "SUCCESS",
        )
        baseline_children = {
            child.pid for child in multiprocessing.active_children()
        }
        baseline_fds = len(os.listdir("/dev/fd"))
        baseline_readers = {
            thread.ident
            for thread in threading.enumerate()
            if thread.name.startswith("gld-p1-reader-")
        }
        baseline_signal_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        with tempfile.TemporaryDirectory() as temporary_directory:
            process_ids_path = Path(temporary_directory) / "process-ids.json"
            clock = _QualificationPauseClock(process_ids_path)
            original_monotonic_ns = supervisor_module.time.monotonic_ns
            original_signal_group = supervisor_module._signal_isolated_group
            injected = False

            def signal_group_with_one_exit(
                process_group_id: int,
                parent_group_id: int,
                signal_number: int,
            ) -> bool:
                nonlocal injected
                if signal_number == signal.SIGTERM and not injected:
                    injected = True
                    raise SystemExit("synthetic post-qualification interruption")
                return original_signal_group(
                    process_group_id,
                    parent_group_id,
                    signal_number,
                )

            supervisor_module.time.monotonic_ns = clock
            supervisor_module._signal_isolated_group = signal_group_with_one_exit
            try:
                runner = self.runner(_target_uncooperative_group)
            finally:
                supervisor_module.time.monotonic_ns = original_monotonic_ns
                supervisor_module._signal_isolated_group = original_signal_group
            request_bytes = _canonical(
                {"process_ids_path": str(process_ids_path)}
            )
            request_sha256 = sha256(request_bytes).hexdigest()
            process_ids: dict[str, int] = {}
            try:
                with self.assertRaisesRegex(
                    SystemExit,
                    "synthetic post-qualification interruption",
                ):
                    runner(request_bytes, request_sha256)
                process_ids = json.loads(
                    process_ids_path.read_text(encoding="ascii")
                )
            finally:
                if not process_ids and process_ids_path.exists():
                    process_ids = json.loads(
                        process_ids_path.read_text(encoding="ascii")
                    )
                if process_ids:
                    try:
                        os.killpg(process_ids["child_pid"], signal.SIGKILL)
                    except ProcessLookupError:
                        pass

        self.assertTrue(clock.jumped)
        self.assertTrue(injected)
        self.assertEqual(
            signal.pthread_sigmask(signal.SIG_BLOCK, set()),
            baseline_signal_mask,
        )
        gc.collect()
        time.sleep(0.05)
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()},
            baseline_children,
        )
        self.assertEqual(len(os.listdir("/dev/fd")), baseline_fds)
        self.assertEqual(
            {
                thread.ident
                for thread in threading.enumerate()
                if thread.name.startswith("gld-p1-reader-")
            },
            baseline_readers,
        )
        for process_id in process_ids.values():
            with self.assertRaises(ProcessLookupError):
                os.kill(process_id, 0)

    def test_persistent_clock_baseexception_cannot_bypass_cleanup_or_mask_restore(
        self,
    ) -> None:
        self.assertEqual(
            self.runner(_target_success)(REQUEST_BYTES, REQUEST_SHA256).status,
            "SUCCESS",
        )
        for fail_from_call in (2, 3, 4):
            with self.subTest(fail_from_call=fail_from_call):
                baseline_children = {
                    child.pid for child in multiprocessing.active_children()
                }
                baseline_fds = len(os.listdir("/dev/fd"))
                baseline_readers = {
                    thread.ident
                    for thread in threading.enumerate()
                    if thread.name.startswith("gld-p1-reader-")
                }
                baseline_signal_mask = signal.pthread_sigmask(
                    signal.SIG_BLOCK,
                    set(),
                )
                clock = _PersistentBaseExceptionClock(
                    fail_from_call=fail_from_call
                )
                original_monotonic_ns = supervisor_module.time.monotonic_ns
                supervisor_module.time.monotonic_ns = clock
                try:
                    runner = self.runner(_target_forever)
                finally:
                    supervisor_module.time.monotonic_ns = original_monotonic_ns

                observed_children: set[int] = set()
                observed_fds = -1
                observed_readers: set[int | None] = set()
                observed_signal_mask: set[signal.Signals] = set()
                try:
                    with self.assertRaisesRegex(
                        SystemExit,
                        f"clock-failure-{fail_from_call}",
                    ):
                        runner(REQUEST_BYTES, REQUEST_SHA256)
                    observed_children = {
                        child.pid
                        for child in multiprocessing.active_children()
                    }
                    observed_fds = len(os.listdir("/dev/fd"))
                    observed_readers = {
                        thread.ident
                        for thread in threading.enumerate()
                        if thread.name.startswith("gld-p1-reader-")
                    }
                    observed_signal_mask = signal.pthread_sigmask(
                        signal.SIG_BLOCK,
                        set(),
                    )
                finally:
                    for child in multiprocessing.active_children():
                        if child.pid in baseline_children:
                            continue
                        if child.pid is not None:
                            try:
                                os.killpg(child.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        try:
                            if child.is_alive():
                                child.kill()
                            child.join(timeout=1.0)
                            child.close()
                        except (OSError, ValueError):
                            pass
                    signal.pthread_sigmask(
                        signal.SIG_SETMASK,
                        baseline_signal_mask,
                    )

                gc.collect()
                time.sleep(0.05)
                self.assertEqual(observed_children, baseline_children)
                self.assertEqual(observed_fds, baseline_fds)
                self.assertEqual(observed_readers, baseline_readers)
                self.assertEqual(observed_signal_mask, baseline_signal_mask)

    def test_no_child_start_failure_retries_pipe_and_process_close(self) -> None:
        self.assertEqual(
            self.runner(_target_success)(REQUEST_BYTES, REQUEST_SHA256).status,
            "SUCCESS",
        )
        baseline_children = {
            child.pid for child in multiprocessing.active_children()
        }
        baseline_fds = len(os.listdir("/dev/fd"))
        baseline_signal_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        runner = self.runner(_target_success)
        context_type = type(multiprocessing.get_context("spawn"))
        base_process = multiprocessing.process.BaseProcess
        original_pipe = context_type.Pipe
        original_start = base_process.start
        original_process_close = base_process.close
        original_connection_close = Connection.close
        connections: list[Connection] = []
        processes: list[object] = []
        pipe_close_interrupted = False
        process_close_interrupted = False

        def pipe_with_retained_refs(
            context: object,
            *args: object,
            **kwargs: object,
        ) -> tuple[Connection, Connection]:
            created = original_pipe(context, *args, **kwargs)
            connections.extend(created)
            return created

        def start_with_failure(process: object) -> None:
            processes.append(process)
            raise RuntimeError("synthetic start failure")

        def connection_close_with_one_exit(connection: Connection) -> None:
            nonlocal pipe_close_interrupted
            if not pipe_close_interrupted and any(
                connection is retained for retained in connections
            ):
                pipe_close_interrupted = True
                raise SystemExit("synthetic no-child pipe close")
            original_connection_close(connection)

        def process_close_with_one_exit(process: object) -> None:
            nonlocal process_close_interrupted
            if not process_close_interrupted and any(
                process is retained for retained in processes
            ):
                process_close_interrupted = True
                raise SystemExit("synthetic no-child process close")
            original_process_close(process)

        caught: BaseException | None = None
        observed_connections_closed: tuple[bool, ...] = ()
        observed_processes_closed: tuple[bool, ...] = ()
        observed_fds = -1
        observed_signal_mask: set[signal.Signals] = set()
        context_type.Pipe = pipe_with_retained_refs
        base_process.start = start_with_failure
        base_process.close = process_close_with_one_exit
        Connection.close = connection_close_with_one_exit
        try:
            try:
                runner(REQUEST_BYTES, REQUEST_SHA256)
            except BaseException as error:
                caught = error
            observed_connections_closed = tuple(
                connection.closed for connection in connections
            )
            observed_processes_closed = tuple(
                bool(getattr(process, "_closed", False))
                for process in processes
            )
            observed_fds = len(os.listdir("/dev/fd"))
            observed_signal_mask = signal.pthread_sigmask(
                signal.SIG_BLOCK,
                set(),
            )
        finally:
            context_type.Pipe = original_pipe
            base_process.start = original_start
            base_process.close = original_process_close
            Connection.close = original_connection_close
            for connection in connections:
                if not connection.closed:
                    original_connection_close(connection)
            for process in processes:
                if not bool(getattr(process, "_closed", False)):
                    try:
                        original_process_close(process)
                    except (OSError, ValueError):
                        pass
            signal.pthread_sigmask(
                signal.SIG_SETMASK,
                baseline_signal_mask,
            )

        self.assertIsInstance(caught, SystemExit)
        self.assertEqual(str(caught), "synthetic no-child pipe close")
        self.assertTrue(pipe_close_interrupted)
        self.assertTrue(process_close_interrupted)
        self.assertEqual(observed_connections_closed, (True, True))
        self.assertEqual(observed_processes_closed, (True,))
        self.assertEqual(observed_fds, baseline_fds)
        self.assertEqual(observed_signal_mask, baseline_signal_mask)
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()},
            baseline_children,
        )

    def test_exported_success_result_rejects_weaker_receipt_facts(self) -> None:
        result = self.runner(_target_success)(REQUEST_BYTES, REQUEST_SHA256)
        receipt = result.supervisor_receipt
        invalid_receipts = (
            replace(receipt, child_exitcode=1),
            replace(receipt, messages_observed=2),
            replace(receipt, reader_joined=False),
            replace(receipt, pipe_closed=False),
            replace(receipt, process_closed=False),
            replace(receipt, terminate_sent=True),
            replace(
                receipt,
                child_exit_monotonic_ns=receipt.deadline_monotonic_ns,
            ),
            replace(
                receipt,
                completed_monotonic_ns=receipt.deadline_monotonic_ns,
                elapsed_ns=(
                    receipt.deadline_monotonic_ns
                    - receipt.supervisor_started_monotonic_ns
                ),
            ),
        )
        for invalid_receipt in invalid_receipts:
            with self.subTest(invalid_receipt=invalid_receipt):
                with self.assertRaises(NormalizationError):
                    replace(result, supervisor_receipt=invalid_receipt)

    def test_real_spawn_start_failure_has_no_partial_result(self) -> None:
        runner = self.runner(_target_success)
        original_module = _target_success.__module__
        try:
            _target_success.__module__ = "nonexistent_p1_target_module"
            result = runner(REQUEST_BYTES, REQUEST_SHA256)
        finally:
            _target_success.__module__ = original_module
        self.assert_fail_closed(result, P1_REASON_START_FAILED)
        self.assertFalse(result.supervisor_receipt.child_started)
        self.assertTrue(result.supervisor_receipt.pipe_closed)

    def test_repeated_invocations_leave_no_fd_child_or_reader_leak(self) -> None:
        baseline_children = {child.pid for child in multiprocessing.active_children()}
        baseline_fds = len(os.listdir("/dev/fd"))
        baseline_readers = {
            thread.ident
            for thread in threading.enumerate()
            if thread.name.startswith("gld-p1-reader-")
        }
        runner = self.runner(_target_success)
        pids: set[int] = set()
        for _ in range(12):
            result = runner(REQUEST_BYTES, REQUEST_SHA256)
            self.assertEqual(result.status, "SUCCESS")
            pids.add(json.loads(result.artifact_bytes)["pid"])
        gc.collect()
        time.sleep(0.05)

        self.assertEqual(len(pids), 12)
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()},
            baseline_children,
        )
        self.assertEqual(len(os.listdir("/dev/fd")), baseline_fds)
        self.assertEqual(
            {
                thread.ident
                for thread in threading.enumerate()
                if thread.name.startswith("gld-p1-reader-")
            },
            baseline_readers,
        )

    def test_issued_path_covers_real_supervisor_outcomes_one_shot(self) -> None:
        cases = (
            (
                _target_success,
                REQUEST_BYTES,
                REQUEST_SHA256,
                "SUCCESS",
                P1_REASON_SUCCESS,
                "NONE",
            ),
            (
                _target_success,
                INVALID_REQUEST_BYTES,
                INVALID_REQUEST_SHA256,
                "FAIL_CLOSED",
                P1_REASON_INPUT_INVALID,
                "INPUT",
            ),
            (
                _target_success,
                REQUEST_BYTES,
                "not-a-sha256",
                "FAIL_CLOSED",
                P1_REASON_INPUT_INVALID,
                "INPUT",
            ),
            (
                _target_execution_failed,
                REQUEST_BYTES,
                REQUEST_SHA256,
                "FAIL_CLOSED",
                P1_REASON_EXECUTION_FAILED,
                "CHILD_DECLARED",
            ),
            (
                _target_malformed,
                REQUEST_BYTES,
                REQUEST_SHA256,
                "FAIL_CLOSED",
                P1_REASON_OUTPUT_MALFORMED,
                "CHILD_PROTOCOL",
            ),
            (
                _target_clean_no_output,
                REQUEST_BYTES,
                REQUEST_SHA256,
                "FAIL_CLOSED",
                P1_REASON_OUTPUT_MISSING,
                "CHILD_PROTOCOL",
            ),
            (
                _target_forever,
                REQUEST_BYTES,
                REQUEST_SHA256,
                "FAIL_CLOSED",
                P1_REASON_HARD_TIMEOUT,
                "DEADLINE",
            ),
        )
        for target, request_bytes, request_hash, status, reason, origin in cases:
            with self.subTest(origin=origin):
                run_issued, consume = self.issued_runner(target)
                issued = run_issued(request_bytes, request_hash)
                self.assertFalse(hasattr(issued, "status"))
                self.assertFalse(hasattr(issued, "artifact_bytes"))
                result = consume(issued, request_hash)
                self.assertIsNotNone(result)
                self.assertEqual(result.status, status)
                self.assertEqual(result.reason_code, reason)
                self.assertEqual(result.failure_origin, origin)
                self.assertIsNone(consume(issued, request_hash))

        global _FAIL_NEXT_DEADLINE_CHECK
        original = supervisor_module._is_before_deadline
        _FAIL_NEXT_DEADLINE_CHECK = True
        supervisor_module._is_before_deadline = _is_before_deadline_fails_once
        try:
            run_issued, consume = self.issued_runner(_target_forever)
        finally:
            supervisor_module._is_before_deadline = original
        issued = run_issued(REQUEST_BYTES, REQUEST_SHA256)
        result = consume(issued, REQUEST_SHA256)
        self.assertIsNotNone(result)
        self.assertEqual(result.reason_code, P1_REASON_EXECUTION_FAILED)
        self.assertEqual(result.failure_origin, "SUPERVISOR")
        self.assertIsNone(consume(issued, REQUEST_SHA256))

    def test_issued_capability_rejects_crossing_clones_and_mutation(self) -> None:
        run_a, consume_a = self.issued_runner(_target_success)
        run_b, consume_b = self.issued_runner(_target_success)

        cross_factory = run_a(INVALID_REQUEST_BYTES, INVALID_REQUEST_SHA256)
        self.assertIsNone(consume_b(cross_factory, INVALID_REQUEST_SHA256))
        self.assertIsNotNone(
            consume_a(cross_factory, INVALID_REQUEST_SHA256)
        )

        wrong_request = run_a(INVALID_REQUEST_BYTES, INVALID_REQUEST_SHA256)
        self.assertIsNone(consume_a(wrong_request, REQUEST_SHA256))
        self.assertIsNone(
            consume_a(wrong_request, INVALID_REQUEST_SHA256)
        )

        issued = run_a(INVALID_REQUEST_BYTES, INVALID_REQUEST_SHA256)
        capability_type = type(issued)
        prefix = f"_{capability_type.__name__.lstrip('_')}__"
        binding_slots = tuple(
            prefix + suffix
            for suffix in (
                "child_target",
                "factory_binding",
                "invocation_binding",
                "policy_binding",
                "request_sha256",
            )
        )
        with self.assertRaises(TypeError):
            capability_type()
        with self.assertRaises(TypeError):
            copy(issued)
        with self.assertRaises(TypeError):
            deepcopy(issued)
        with self.assertRaises(TypeError):
            pickle.dumps(issued)
        with self.assertRaises(TypeError):
            replace(issued)

        unsafe_clone = object.__new__(capability_type)
        for slot in binding_slots:
            object.__setattr__(unsafe_clone, slot, getattr(issued, slot))
        self.assertIsNone(consume_a(unsafe_clone, INVALID_REQUEST_SHA256))
        self.assertIsNotNone(consume_a(issued, INVALID_REQUEST_SHA256))

        first = run_a(INVALID_REQUEST_BYTES, INVALID_REQUEST_SHA256)
        second = run_a(INVALID_REQUEST_BYTES, INVALID_REQUEST_SHA256)
        invocation_slot = prefix + "invocation_binding"
        object.__setattr__(
            first,
            invocation_slot,
            getattr(second, invocation_slot),
        )
        self.assertIsNone(consume_a(first, INVALID_REQUEST_SHA256))
        self.assertIsNotNone(consume_a(second, INVALID_REQUEST_SHA256))

        for slot in binding_slots:
            with self.subTest(unsafe_field=slot):
                modified = run_a(
                    INVALID_REQUEST_BYTES,
                    INVALID_REQUEST_SHA256,
                )
                object.__setattr__(modified, slot, object())
                self.assertIsNone(
                    consume_a(modified, INVALID_REQUEST_SHA256)
                )
                self.assertIsNone(
                    consume_a(modified, INVALID_REQUEST_SHA256)
                )

    def test_issued_registry_is_thread_safe_and_consume_is_atomic(self) -> None:
        run_issued, consume = self.issued_runner(_target_success)
        issued = run_issued(INVALID_REQUEST_BYTES, INVALID_REQUEST_SHA256)
        barrier = threading.Barrier(16)
        consumed: list[object] = []
        consumed_lock = threading.Lock()

        def consume_once() -> None:
            barrier.wait()
            result = consume(issued, INVALID_REQUEST_SHA256)
            with consumed_lock:
                consumed.append(result)

        consumers = [threading.Thread(target=consume_once) for _ in range(16)]
        for consumer in consumers:
            consumer.start()
        for consumer in consumers:
            consumer.join(timeout=2.0)
        self.assertTrue(all(not consumer.is_alive() for consumer in consumers))
        self.assertEqual(sum(result is not None for result in consumed), 1)

        issue_barrier = threading.Barrier(16)
        issued_results: list[object] = []
        issue_errors: list[BaseException] = []
        issue_lock = threading.Lock()

        def issue_once() -> None:
            try:
                issue_barrier.wait()
                capability = run_issued(
                    INVALID_REQUEST_BYTES,
                    INVALID_REQUEST_SHA256,
                )
                with issue_lock:
                    issued_results.append(capability)
            except BaseException as error:
                with issue_lock:
                    issue_errors.append(error)

        issuers = [threading.Thread(target=issue_once) for _ in range(16)]
        for issuer in issuers:
            issuer.start()
        for issuer in issuers:
            issuer.join(timeout=2.0)
        self.assertTrue(all(not issuer.is_alive() for issuer in issuers))
        self.assertEqual(issue_errors, [])
        self.assertEqual(len(issued_results), 16)
        self.assertTrue(
            all(
                consume(capability, INVALID_REQUEST_SHA256) is not None
                for capability in issued_results
            )
        )

    def test_issued_registry_is_bounded_and_discards_are_reclaimed(self) -> None:
        maximum_pending = supervisor_module._P1_MAX_PENDING_ISSUED_RESULTS
        run_issued, consume = self.issued_runner(_target_success)
        discarded = run_issued(
            INVALID_REQUEST_BYTES,
            INVALID_REQUEST_SHA256,
        )
        discarded_reference = weakref.ref(discarded)
        del discarded
        gc.collect()
        self.assertIsNone(discarded_reference())

        after_discard = [
            run_issued(INVALID_REQUEST_BYTES, INVALID_REQUEST_SHA256)
            for _ in range(maximum_pending)
        ]
        self.assertIsNotNone(
            consume(after_discard[0], INVALID_REQUEST_SHA256)
        )

        bounded_run, bounded_consume = self.issued_runner(_target_success)
        pending = [
            bounded_run(INVALID_REQUEST_BYTES, INVALID_REQUEST_SHA256)
            for _ in range(maximum_pending + 1)
        ]
        self.assertIsNone(
            bounded_consume(pending[0], INVALID_REQUEST_SHA256)
        )
        self.assertIsNotNone(
            bounded_consume(pending[-1], INVALID_REQUEST_SHA256)
        )

    def test_workflow_api_preserves_public_signature_and_has_no_override(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(run_p1_process_supervisor).parameters),
            ("request_bytes", "request_sha256"),
        )
        begin, run_issued, consume, finalize = self.workflow_runner(
            _target_success
        )
        self.assertEqual(tuple(inspect.signature(begin).parameters), ())
        self.assertEqual(
            tuple(inspect.signature(run_issued).parameters),
            ("ticket", "request_bytes", "request_sha256"),
        )
        self.assertEqual(
            tuple(inspect.signature(consume).parameters),
            ("ticket", "issued", "expected_request_sha256"),
        )
        self.assertEqual(
            tuple(inspect.signature(finalize).parameters),
            ("ticket", "expected_request_sha256"),
        )
        result = self.runner(_target_success)(REQUEST_BYTES, REQUEST_SHA256)
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(
            result.supervisor_receipt.deadline_monotonic_ns,
            result.supervisor_receipt.supervisor_started_monotonic_ns
            + P1_PROCESS_TIMEOUT_NS,
        )

    def test_workflow_fake_clock_boundaries_and_late_finalization(self) -> None:
        clock = _FakeMonotonicClock(10_000)
        begin, run_issued, consume, finalize = self.workflow_runner_with_clock(
            _target_success,
            clock,
        )

        before_ticket = begin()
        clock.advance(P1_PROCESS_TIMEOUT_NS - 1)
        before_issued = run_issued(
            before_ticket,
            INVALID_REQUEST_BYTES,
            INVALID_REQUEST_SHA256,
        )
        self.assertIsNotNone(before_issued)
        before_result = consume(
            before_ticket,
            before_issued,
            INVALID_REQUEST_SHA256,
        )
        self.assertIsNotNone(before_result)
        self.assertEqual(before_result.failure_origin, "INPUT")
        before_timing = finalize(before_ticket, INVALID_REQUEST_SHA256)
        self.assertIsNotNone(before_timing)
        self.assertTrue(before_timing.before_deadline)

        clock.now_ns = 20_000
        original_timeout = supervisor_module.P1_PROCESS_TIMEOUT_NS
        supervisor_module.P1_PROCESS_TIMEOUT_NS = 1
        try:
            expired_ticket = begin()
        finally:
            supervisor_module.P1_PROCESS_TIMEOUT_NS = original_timeout
        clock.advance(P1_PROCESS_TIMEOUT_NS)
        expired_issued = run_issued(
            expired_ticket,
            REQUEST_BYTES,
            REQUEST_SHA256,
        )
        self.assertIsNotNone(expired_issued)
        expired_result = consume(
            expired_ticket,
            expired_issued,
            REQUEST_SHA256,
        )
        self.assertIsNotNone(expired_result)
        self.assertEqual(expired_result.reason_code, P1_REASON_HARD_TIMEOUT)
        self.assertEqual(expired_result.failure_origin, "DEADLINE")
        self.assertFalse(expired_result.supervisor_receipt.child_started)
        self.assertEqual(
            expired_result.supervisor_receipt.deadline_monotonic_ns,
            expired_result.supervisor_receipt.supervisor_started_monotonic_ns
            + P1_PROCESS_TIMEOUT_NS,
        )
        expired_timing = finalize(expired_ticket, REQUEST_SHA256)
        self.assertIsNotNone(expired_timing)
        self.assertFalse(expired_timing.before_deadline)

        clock.now_ns = 30_000
        late_ticket = begin()
        late_issued = run_issued(
            late_ticket,
            INVALID_REQUEST_BYTES,
            INVALID_REQUEST_SHA256,
        )
        late_result = consume(
            late_ticket,
            late_issued,
            INVALID_REQUEST_SHA256,
        )
        self.assertIsNotNone(late_result)
        clock.advance(P1_PROCESS_TIMEOUT_NS)
        late_timing = finalize(late_ticket, INVALID_REQUEST_SHA256)
        self.assertIsNotNone(late_timing)
        self.assertFalse(late_timing.before_deadline)
        self.assertIsNone(finalize(late_ticket, INVALID_REQUEST_SHA256))

    def test_workflow_deadline_overrides_validation_failure_at_completion(self) -> None:
        clock = _FakeMonotonicClock(40_000)
        parser = _DeadlineCrossingInvalidParser(clock)
        original_clock = supervisor_module.time.monotonic_ns
        original_parser = supervisor_module._parse_canonical_json_bytes
        supervisor_module.time.monotonic_ns = clock
        supervisor_module._parse_canonical_json_bytes = parser
        try:
            begin, run_issued, consume, finalize = self.workflow_runner(
                _target_success
            )
        finally:
            supervisor_module.time.monotonic_ns = original_clock
            supervisor_module._parse_canonical_json_bytes = original_parser

        ticket = begin()
        issued = run_issued(ticket, REQUEST_BYTES, REQUEST_SHA256)
        self.assertIsNotNone(issued)
        result = consume(ticket, issued, REQUEST_SHA256)
        self.assertIsNotNone(result)
        self.assertEqual(result.reason_code, P1_REASON_HARD_TIMEOUT)
        self.assertEqual(result.failure_origin, "DEADLINE")
        self.assertTrue(result.supervisor_receipt.deadline_reached)
        self.assertFalse(result.supervisor_receipt.child_started)
        timing = finalize(ticket, REQUEST_SHA256)
        self.assertIsNotNone(timing)
        self.assertFalse(timing.before_deadline)

    def test_workflow_child_and_receipt_share_one_absolute_deadline(self) -> None:
        begin, run_issued, consume, finalize = self.workflow_runner(
            _target_echo_deadline
        )
        ticket = begin()
        time.sleep(0.03)
        issued = run_issued(ticket, REQUEST_BYTES, REQUEST_SHA256)
        self.assertIsNotNone(issued)
        result = consume(ticket, issued, REQUEST_SHA256)
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "SUCCESS")
        artifact = json.loads(result.artifact_bytes)
        timing = finalize(ticket, REQUEST_SHA256)
        self.assertIsNotNone(timing)
        self.assertTrue(timing.before_deadline)
        self.assertEqual(
            artifact["deadline_monotonic_ns"],
            timing.deadline_monotonic_ns,
        )
        self.assertEqual(
            result.supervisor_receipt.deadline_monotonic_ns,
            timing.deadline_monotonic_ns,
        )
        self.assertEqual(
            result.supervisor_receipt.supervisor_started_monotonic_ns,
            timing.workflow_started_monotonic_ns,
        )

    def test_workflow_uncooperative_child_cleans_by_absolute_ceiling(self) -> None:
        begin, run_issued, consume, finalize = self.workflow_runner(
            _target_ignore_term_forever
        )
        wall_started_ns = time.monotonic_ns()
        ticket = begin()
        time.sleep(0.20)
        issued = run_issued(ticket, REQUEST_BYTES, REQUEST_SHA256)
        self.assertIsNotNone(issued)
        result = consume(ticket, issued, REQUEST_SHA256)
        self.assertIsNotNone(result)
        self.assertEqual(result.reason_code, P1_REASON_HARD_TIMEOUT)
        receipt = result.supervisor_receipt
        self.assertGreaterEqual(receipt.elapsed_ns, P1_PROCESS_TIMEOUT_NS)
        self.assertLessEqual(receipt.elapsed_ns, 5_250_000_000)
        self.assertTrue(receipt.terminate_sent)
        self.assertTrue(receipt.kill_sent)
        self.assertTrue(receipt.child_reaped)
        timing = finalize(ticket, REQUEST_SHA256)
        self.assertIsNotNone(timing)
        self.assertFalse(timing.before_deadline)
        self.assertLessEqual(
            time.monotonic_ns() - wall_started_ns,
            5_350_000_000,
        )

    def test_workflow_capabilities_reject_crossing_clones_and_mutation(self) -> None:
        begin_a, run_a, consume_a, finalize_a = self.workflow_runner(
            _target_success
        )
        begin_b, run_b, consume_b, finalize_b = self.workflow_runner(
            _target_success
        )
        ticket = begin_a()
        ticket_type = type(ticket)
        ticket_prefix = f"_{ticket_type.__name__.lstrip('_')}__"
        ticket_slots = tuple(
            ticket_prefix + suffix
            for suffix in (
                "child_target",
                "deadline_monotonic_ns",
                "factory_binding",
                "policy_binding",
                "started_monotonic_ns",
                "workflow_binding",
            )
        )
        with self.assertRaises(TypeError):
            ticket_type()
        with self.assertRaises(TypeError):
            copy(ticket)
        with self.assertRaises(TypeError):
            deepcopy(ticket)
        with self.assertRaises(TypeError):
            pickle.dumps(ticket)
        with self.assertRaises(TypeError):
            replace(ticket)

        cloned_ticket = object.__new__(ticket_type)
        for slot in ticket_slots:
            object.__setattr__(cloned_ticket, slot, getattr(ticket, slot))
        self.assertIsNone(
            run_a(cloned_ticket, INVALID_REQUEST_BYTES, INVALID_REQUEST_SHA256)
        )
        self.assertIsNone(
            run_b(ticket, INVALID_REQUEST_BYTES, INVALID_REQUEST_SHA256)
        )

        issued = run_a(ticket, INVALID_REQUEST_BYTES, INVALID_REQUEST_SHA256)
        self.assertIsNotNone(issued)
        issued_type = type(issued)
        issued_prefix = f"_{issued_type.__name__.lstrip('_')}__"
        issued_slots = tuple(
            issued_prefix + suffix
            for suffix in (
                "child_target",
                "factory_binding",
                "invocation_binding",
                "policy_binding",
                "request_sha256",
                "workflow_binding",
            )
        )
        with self.assertRaises(TypeError):
            issued_type()
        with self.assertRaises(TypeError):
            copy(issued)
        with self.assertRaises(TypeError):
            deepcopy(issued)
        with self.assertRaises(TypeError):
            pickle.dumps(issued)
        with self.assertRaises(TypeError):
            replace(issued)

        cloned_issued = object.__new__(issued_type)
        for slot in issued_slots:
            object.__setattr__(cloned_issued, slot, getattr(issued, slot))
        self.assertIsNone(
            consume_a(ticket, cloned_issued, INVALID_REQUEST_SHA256)
        )
        result = consume_a(ticket, issued, INVALID_REQUEST_SHA256)
        self.assertIsNotNone(result)
        self.assertIsNotNone(finalize_a(ticket, INVALID_REQUEST_SHA256))
        self.assertIsNone(finalize_a(ticket, INVALID_REQUEST_SHA256))

        wrong_ticket = begin_a()
        wrong_issued = run_a(
            wrong_ticket,
            INVALID_REQUEST_BYTES,
            INVALID_REQUEST_SHA256,
        )
        self.assertIsNone(consume_a(wrong_ticket, wrong_issued, REQUEST_SHA256))
        self.assertIsNone(
            consume_a(wrong_ticket, wrong_issued, INVALID_REQUEST_SHA256)
        )
        self.assertIsNone(finalize_a(wrong_ticket, INVALID_REQUEST_SHA256))

        first_ticket = begin_a()
        second_ticket = begin_a()
        first_issued = run_a(
            first_ticket,
            INVALID_REQUEST_BYTES,
            INVALID_REQUEST_SHA256,
        )
        second_issued = run_a(
            second_ticket,
            INVALID_REQUEST_BYTES,
            INVALID_REQUEST_SHA256,
        )
        self.assertIsNone(
            consume_a(first_ticket, second_issued, INVALID_REQUEST_SHA256)
        )
        self.assertIsNotNone(
            consume_a(first_ticket, first_issued, INVALID_REQUEST_SHA256)
        )
        self.assertIsNotNone(
            consume_a(second_ticket, second_issued, INVALID_REQUEST_SHA256)
        )
        self.assertIsNotNone(
            finalize_a(first_ticket, INVALID_REQUEST_SHA256)
        )
        self.assertIsNotNone(
            finalize_a(second_ticket, INVALID_REQUEST_SHA256)
        )

        mutated_ticket = begin_a()
        object.__setattr__(mutated_ticket, ticket_slots[0], object())
        self.assertIsNone(
            run_a(
                mutated_ticket,
                INVALID_REQUEST_BYTES,
                INVALID_REQUEST_SHA256,
            )
        )
        mutated_result_ticket = begin_a()
        mutated_issued = run_a(
            mutated_result_ticket,
            INVALID_REQUEST_BYTES,
            INVALID_REQUEST_SHA256,
        )
        object.__setattr__(mutated_issued, issued_slots[0], object())
        self.assertIsNone(
            consume_a(
                mutated_result_ticket,
                mutated_issued,
                INVALID_REQUEST_SHA256,
            )
        )
        self.assertIsNone(
            finalize_a(mutated_result_ticket, INVALID_REQUEST_SHA256)
        )
        del begin_b, run_b, consume_b, finalize_b

    def test_workflow_consume_and_finalize_are_atomic_across_threads(self) -> None:
        begin, run_issued, consume, finalize = self.workflow_runner(
            _target_success
        )
        ticket = begin()
        issued = run_issued(
            ticket,
            INVALID_REQUEST_BYTES,
            INVALID_REQUEST_SHA256,
        )
        consume_barrier = threading.Barrier(16)
        consumed: list[object] = []
        consumed_lock = threading.Lock()

        def consume_once() -> None:
            consume_barrier.wait()
            value = consume(ticket, issued, INVALID_REQUEST_SHA256)
            with consumed_lock:
                consumed.append(value)

        consumers = [threading.Thread(target=consume_once) for _ in range(16)]
        for thread in consumers:
            thread.start()
        for thread in consumers:
            thread.join(timeout=2.0)
        self.assertTrue(all(not thread.is_alive() for thread in consumers))
        self.assertEqual(sum(value is not None for value in consumed), 1)

        finalize_barrier = threading.Barrier(16)
        finalized: list[object] = []
        finalized_lock = threading.Lock()

        def finalize_once() -> None:
            finalize_barrier.wait()
            value = finalize(ticket, INVALID_REQUEST_SHA256)
            with finalized_lock:
                finalized.append(value)

        finalizers = [threading.Thread(target=finalize_once) for _ in range(16)]
        for thread in finalizers:
            thread.start()
        for thread in finalizers:
            thread.join(timeout=2.0)
        self.assertTrue(all(not thread.is_alive() for thread in finalizers))
        self.assertEqual(sum(value is not None for value in finalized), 1)

    def test_workflow_registry_is_bounded_and_weakly_reclaims(self) -> None:
        begin, run_issued, consume, finalize = self.workflow_runner(
            _target_success
        )
        discarded_ticket = begin()
        discarded_reference = weakref.ref(discarded_ticket)
        del discarded_ticket
        gc.collect()
        self.assertIsNone(discarded_reference())

        maximum_pending = supervisor_module._P1_MAX_PENDING_ISSUED_RESULTS
        pending = [begin() for _ in range(maximum_pending + 1)]
        self.assertIsNone(
            run_issued(
                pending[0],
                INVALID_REQUEST_BYTES,
                INVALID_REQUEST_SHA256,
            )
        )
        last_issued = run_issued(
            pending[-1],
            INVALID_REQUEST_BYTES,
            INVALID_REQUEST_SHA256,
        )
        self.assertIsNotNone(last_issued)
        self.assertIsNotNone(
            consume(pending[-1], last_issued, INVALID_REQUEST_SHA256)
        )
        self.assertIsNotNone(finalize(pending[-1], INVALID_REQUEST_SHA256))

        issued_ticket = begin()
        abandoned_issued = run_issued(
            issued_ticket,
            INVALID_REQUEST_BYTES,
            INVALID_REQUEST_SHA256,
        )
        issued_reference = weakref.ref(abandoned_issued)
        del abandoned_issued
        gc.collect()
        self.assertIsNone(issued_reference())
        self.assertIsNone(finalize(issued_ticket, INVALID_REQUEST_SHA256))


if __name__ == "__main__":
    unittest.main()
