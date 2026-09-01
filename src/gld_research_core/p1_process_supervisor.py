"""Bounded spawn-process supervisor for future deterministic Decision work.

The production Decision child is intentionally not implemented here.  This
module owns only a same-process-group containment attempt, a canonical byte
protocol, hard-deadline handling, and fail-closed publication.  It is not an OS
sandbox: a hostile descendant that creates a new session or process group can
escape observation on macOS.  It has no provider, broker, order, build,
network, or backend-selection authority, and qualification is not claimed.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import json
import multiprocessing
from multiprocessing.connection import Connection
import os
import re
import secrets
import signal
from threading import Event, Lock, Thread
import time
from types import FunctionType
from typing import Callable
from weakref import ReferenceType, ref

from gld_normalizer.errors import NormalizationError


P1_CHILD_FRAME_SCHEMA = "GLD_P1_CHILD_FRAME_V1"
P1_SUPERVISOR_RECEIPT_SCHEMA = "GLD_P1_PROCESS_SUPERVISOR_RECEIPT_V1"
P1_PRODUCTION_CHILD_STATUS = "NOT_IMPLEMENTED_PRODUCTION_DECISION_CHILD"
P1_PROCESS_TIMEOUT_NS = 5_000_000_000
P1_TERMINATE_GRACE_NS = 100_000_000
P1_QUALIFICATION_CEILING_NS = 5_250_000_000
P1_SAFETY_CLEANUP_CEILING_NS = 1_000_000_000
P1_MAX_REQUEST_BYTES = 1_048_576
P1_MAX_CHILD_FRAME_BYTES = 1_048_576

_P1_MAX_PENDING_ISSUED_RESULTS = 1_024
_P1_SAFETY_CLEANUP_ATTEMPTS = 3
_P1_SAFETY_MONOTONIC_NS = time.monotonic_ns

P1_REASON_SUCCESS = "P1_SUCCESS"
P1_REASON_INPUT_INVALID = "P1_INPUT_INVALID"
P1_REASON_START_FAILED = "P1_START_FAILED"
P1_REASON_HARD_TIMEOUT = "P1_HARD_TIMEOUT"
P1_REASON_CHILD_CRASHED = "P1_CHILD_CRASHED"
P1_REASON_OUTPUT_MISSING = "P1_OUTPUT_MISSING"
P1_REASON_OUTPUT_MALFORMED = "P1_OUTPUT_MALFORMED"
P1_REASON_MULTIPLE_OUTPUTS = "P1_MULTIPLE_OUTPUTS"
P1_REASON_EXECUTION_FAILED = "P1_EXECUTION_FAILED"
P1_REASON_DESCENDANT_LEAK = "P1_DESCENDANT_LEAK"

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_FRAME_KEYS = frozenset(
    {
        "schema_version",
        "generation_token",
        "request_sha256",
        "outcome",
        "reason_code",
        "semantic_receipt",
        "artifact",
        "artifact_sha256",
        "actionable",
        "broker_order_count",
    }
)
_MAX_JSON_DEPTH = 32
_MAX_JSON_NODES = 100_000
_MAX_JSON_STRING_CHARS = 1_048_576


def _exact_int(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
    return value


def _exact_optional_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
    return value


@dataclass(frozen=True, slots=True)
class P1ProcessSupervisorReceiptV1:
    schema_version: str
    generation_token: str
    request_sha256: str
    supervisor_started_monotonic_ns: int
    deadline_monotonic_ns: int
    generation_closed_monotonic_ns: int
    child_start_monotonic_ns: int | None
    child_exit_monotonic_ns: int | None
    completed_monotonic_ns: int
    elapsed_ns: int
    child_started: bool
    child_pid: int | None
    child_exitcode: int | None
    child_process_group_id: int | None
    child_session_id: int | None
    parent_process_group_id: int
    parent_session_id: int
    process_group_verified: bool
    isolation_barrier_released: bool
    process_group_gone: bool
    descendant_leak_observed: bool
    deadline_reached: bool
    messages_observed: int
    messages_accepted: int
    late_messages_suppressed: int
    terminate_sent: bool
    kill_sent: bool
    child_reaped: bool
    reader_joined: bool
    pipe_closed: bool
    process_closed: bool
    partial_result_published: bool
    qualification_status: str

    def __post_init__(self) -> None:
        if (
            self.schema_version != P1_SUPERVISOR_RECEIPT_SCHEMA
            or type(self.generation_token) is not str
            or _SHA256_RE.fullmatch(self.generation_token) is None
            or type(self.request_sha256) is not str
            or _SHA256_RE.fullmatch(self.request_sha256) is None
            or self.qualification_status != "NOT_CLAIMED"
        ):
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        started = _exact_int(self.supervisor_started_monotonic_ns)
        deadline = _exact_int(self.deadline_monotonic_ns)
        closed = _exact_int(self.generation_closed_monotonic_ns)
        completed = _exact_int(self.completed_monotonic_ns)
        elapsed = _exact_int(self.elapsed_ns)
        child_start = _exact_optional_int(self.child_start_monotonic_ns)
        child_exit = _exact_optional_int(self.child_exit_monotonic_ns)
        child_pid = _exact_optional_int(self.child_pid)
        child_process_group_id = _exact_optional_int(
            self.child_process_group_id
        )
        child_session_id = _exact_optional_int(self.child_session_id)
        parent_process_group_id = _exact_int(
            self.parent_process_group_id,
            minimum=1,
        )
        parent_session_id = _exact_int(self.parent_session_id, minimum=1)
        if (
            deadline != started + 5_000_000_000
            or closed < started
            or completed < closed
            or elapsed != completed - started
            or (child_start is not None and child_start < started)
            or (child_exit is not None and child_exit < started)
        ):
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        for value in (
            self.child_started,
            self.process_group_verified,
            self.isolation_barrier_released,
            self.process_group_gone,
            self.descendant_leak_observed,
            self.deadline_reached,
            self.terminate_sent,
            self.kill_sent,
            self.child_reaped,
            self.reader_joined,
            self.pipe_closed,
            self.process_closed,
            self.partial_result_published,
        ):
            if type(value) is not bool:
                raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if self.partial_result_published:
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if self.child_started != (child_start is not None):
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if self.child_started != (child_pid is not None):
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if self.isolation_barrier_released and not self.process_group_verified:
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if not self.process_group_gone:
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if self.descendant_leak_observed and (
            not self.process_group_verified
            or not self.isolation_barrier_released
            or not self.terminate_sent
        ):
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if self.process_group_verified:
            if (
                child_pid is None
                or child_process_group_id != child_pid
                or child_session_id != child_pid
                or child_pid in {parent_process_group_id, parent_session_id}
            ):
                raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        elif child_process_group_id is not None or child_session_id is not None:
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if self.child_exitcode is not None and type(self.child_exitcode) is not int:
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        observed = _exact_int(self.messages_observed)
        accepted = _exact_int(self.messages_accepted)
        late = _exact_int(self.late_messages_suppressed)
        if accepted not in (0, 1) or accepted > observed or late > observed:
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if accepted and self.deadline_reached:
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if self.kill_sent and not self.terminate_sent:
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if self.process_closed and self.child_started and not self.child_reaped:
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)


@dataclass(frozen=True, slots=True)
class P1ProcessSupervisorResultV1:
    status: str
    reason_code: str
    failure_origin: str
    child_reason_code: str | None
    semantic_receipt_bytes: bytes | None
    artifact_bytes: bytes | None
    artifact_sha256: str | None
    actionable: bool
    broker_order_count: int
    supervisor_receipt: P1ProcessSupervisorReceiptV1

    def __post_init__(self) -> None:
        if self.status not in {"SUCCESS", "FAIL_CLOSED"}:
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if type(self.reason_code) is not str or _REASON_RE.fullmatch(
            self.reason_code
        ) is None:
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if self.failure_origin not in {
            "NONE",
            "INPUT",
            "START",
            "DEADLINE",
            "CHILD_EXIT",
            "CHILD_PROTOCOL",
            "CHILD_DECLARED",
            "SUPERVISOR",
            "DESCENDANT",
        }:
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if self.child_reason_code is not None and (
            type(self.child_reason_code) is not str
            or _REASON_RE.fullmatch(self.child_reason_code) is None
        ):
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if (
            type(self.actionable) is not bool
            or self.actionable
            or type(self.broker_order_count) is not int
            or self.broker_order_count != 0
            or not isinstance(
                self.supervisor_receipt,
                P1ProcessSupervisorReceiptV1,
            )
        ):
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        if self.status == "SUCCESS":
            if (
                self.reason_code != P1_REASON_SUCCESS
                or self.failure_origin != "NONE"
                or self.child_reason_code is not None
                or type(self.semantic_receipt_bytes) is not bytes
                or type(self.artifact_bytes) is not bytes
                or type(self.artifact_sha256) is not str
                or _SHA256_RE.fullmatch(self.artifact_sha256) is None
                or sha256(self.artifact_bytes).hexdigest()
                != self.artifact_sha256
                or self.supervisor_receipt.messages_accepted != 1
                or self.supervisor_receipt.messages_observed != 1
                or self.supervisor_receipt.child_exitcode != 0
                or self.supervisor_receipt.child_exit_monotonic_ns is None
                or self.supervisor_receipt.child_exit_monotonic_ns
                >= self.supervisor_receipt.deadline_monotonic_ns
                or self.supervisor_receipt.completed_monotonic_ns
                >= self.supervisor_receipt.deadline_monotonic_ns
                or self.supervisor_receipt.deadline_reached
                or self.supervisor_receipt.terminate_sent
                or self.supervisor_receipt.kill_sent
                or not self.supervisor_receipt.child_reaped
                or not self.supervisor_receipt.reader_joined
                or not self.supervisor_receipt.pipe_closed
                or not self.supervisor_receipt.process_closed
                or not self.supervisor_receipt.process_group_verified
                or not self.supervisor_receipt.isolation_barrier_released
                or not self.supervisor_receipt.process_group_gone
                or self.supervisor_receipt.descendant_leak_observed
            ):
                raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)
        elif (
            self.failure_origin == "NONE"
            or (
                self.failure_origin == "CHILD_DECLARED"
                and self.child_reason_code is None
            )
            or (
                self.failure_origin != "CHILD_DECLARED"
                and self.child_reason_code is not None
            )
            or
            self.semantic_receipt_bytes is not None
            or self.artifact_bytes is not None
            or self.artifact_sha256 is not None
            or self.supervisor_receipt.messages_accepted != 0
        ):
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)


class _P1IssuedSupervisorResultV1:
    """Opaque, process-local identity capability for one supervisor result."""

    __slots__ = (
        "__child_target",
        "__factory_binding",
        "__invocation_binding",
        "__policy_binding",
        "__request_sha256",
        "__weakref__",
    )

    def __new__(cls, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise TypeError("issued supervisor capabilities are factory-created")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("issued supervisor capabilities cannot be subclassed")

    def __copy__(self) -> object:
        raise TypeError("issued supervisor capabilities cannot be copied")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("issued supervisor capabilities cannot be copied")

    def __reduce__(self) -> object:
        raise TypeError("issued supervisor capabilities cannot be serialized")

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("issued supervisor capabilities cannot be serialized")

    def __repr__(self) -> str:
        return "<_P1IssuedSupervisorResultV1 opaque>"


@dataclass(frozen=True, slots=True)
class _P1IssuedRegistryEntryV1:
    issued_reference: ReferenceType[_P1IssuedSupervisorResultV1]
    factory_binding: object
    invocation_binding: object
    request_sha256: str
    child_target: Callable[..., None]
    policy_binding: object
    policy_snapshot: tuple[object, ...]
    result: P1ProcessSupervisorResultV1


@dataclass(frozen=True, slots=True)
class _P1BoundSupervisorCoreV1:
    run: Callable[[bytes, str], P1ProcessSupervisorResultV1]
    run_workflow: Callable[[int, bytes, str], P1ProcessSupervisorResultV1]
    monotonic_ns: Callable[[], int]
    timeout_ns: int
    child_target: Callable[..., None]
    policy_binding: object
    policy_snapshot: tuple[object, ...]


class _P1WorkflowTicketV1:
    """Opaque workflow-start authority retained only in its bound factory."""

    __slots__ = (
        "__child_target",
        "__deadline_monotonic_ns",
        "__factory_binding",
        "__policy_binding",
        "__started_monotonic_ns",
        "__workflow_binding",
        "__weakref__",
    )

    def __new__(cls, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise TypeError("workflow tickets are factory-created")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("workflow tickets cannot be subclassed")

    def __copy__(self) -> object:
        raise TypeError("workflow tickets cannot be copied")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("workflow tickets cannot be copied")

    def __reduce__(self) -> object:
        raise TypeError("workflow tickets cannot be serialized")

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("workflow tickets cannot be serialized")

    def __repr__(self) -> str:
        return "<_P1WorkflowTicketV1 opaque>"


class _P1WorkflowIssuedResultV1:
    """Opaque identity capability for one ticket-bound supervisor result."""

    __slots__ = (
        "__child_target",
        "__factory_binding",
        "__invocation_binding",
        "__policy_binding",
        "__request_sha256",
        "__workflow_binding",
        "__weakref__",
    )

    def __new__(cls, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise TypeError("workflow results are factory-created")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("workflow results cannot be subclassed")

    def __copy__(self) -> object:
        raise TypeError("workflow results cannot be copied")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("workflow results cannot be copied")

    def __reduce__(self) -> object:
        raise TypeError("workflow results cannot be serialized")

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("workflow results cannot be serialized")

    def __repr__(self) -> str:
        return "<_P1WorkflowIssuedResultV1 opaque>"


@dataclass(frozen=True, slots=True)
class _P1WorkflowTimingFinalizationV1:
    workflow_started_monotonic_ns: int
    deadline_monotonic_ns: int
    completed_monotonic_ns: int
    before_deadline: bool

    def __post_init__(self) -> None:
        started = _exact_int(self.workflow_started_monotonic_ns)
        deadline = _exact_int(self.deadline_monotonic_ns)
        completed = _exact_int(self.completed_monotonic_ns)
        if (
            deadline != started + 5_000_000_000
            or completed < started
            or type(self.before_deadline) is not bool
            or self.before_deadline != (completed < deadline)
        ):
            raise NormalizationError(P1_REASON_OUTPUT_MALFORMED)


@dataclass(slots=True)
class _P1WorkflowRegistryEntryV1:
    ticket_reference: ReferenceType[_P1WorkflowTicketV1]
    factory_binding: object
    workflow_binding: object
    child_target: Callable[..., None]
    policy_binding: object
    policy_snapshot: tuple[object, ...]
    started_monotonic_ns: int
    deadline_monotonic_ns: int
    state: str = "NEW"
    request_sha256: str | None = None
    invocation_binding: object | None = None
    issued_reference: ReferenceType[_P1WorkflowIssuedResultV1] | None = None
    result: P1ProcessSupervisorResultV1 | None = None
    consume_succeeded: bool = False


@dataclass(slots=True)
class _ReaderState:
    lock: Lock
    generation_open: bool
    frames: list[tuple[int, bytes]]
    messages_observed: int = 0
    late_messages_suppressed: int = 0
    transport_malformed: bool = False


def _reject_json_number(value: str) -> object:
    raise ValueError("non-integral JSON number")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("duplicate or invalid key")
        result[key] = value
    return result


def _validate_json_tree(
    value: object,
    *,
    depth: int = 0,
    nodes: list[int] | None = None,
    maximum_depth: int = _MAX_JSON_DEPTH,
    maximum_nodes: int = _MAX_JSON_NODES,
    maximum_string_chars: int = _MAX_JSON_STRING_CHARS,
) -> None:
    counter = [0] if nodes is None else nodes
    if depth > maximum_depth:
        raise ValueError("JSON depth")
    counter[0] += 1
    if counter[0] > maximum_nodes:
        raise ValueError("JSON nodes")
    if type(value) is dict:
        for key, item in value.items():
            if (
                type(key) is not str
                or not key
                or len(key) > 256
                or len(key) > maximum_string_chars
            ):
                raise ValueError("JSON key")
            _validate_json_tree(
                item,
                depth=depth + 1,
                nodes=counter,
                maximum_depth=maximum_depth,
                maximum_nodes=maximum_nodes,
                maximum_string_chars=maximum_string_chars,
            )
        return
    if type(value) is list:
        for item in value:
            _validate_json_tree(
                item,
                depth=depth + 1,
                nodes=counter,
                maximum_depth=maximum_depth,
                maximum_nodes=maximum_nodes,
                maximum_string_chars=maximum_string_chars,
            )
        return
    if type(value) is str:
        if len(value) > maximum_string_chars:
            raise ValueError("JSON string")
        return
    if type(value) is int:
        if value.bit_length() > 64:
            raise ValueError("JSON integer")
        return
    if type(value) is bool or value is None:
        return
    raise ValueError("JSON type")


def _canonical_json_bytes(
    value: object,
    *,
    validate_tree: Callable[..., None] = _validate_json_tree,
    json_dumps: Callable[..., str] = json.dumps,
) -> bytes:
    try:
        validate_tree(value)
        return json_dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (RecursionError, TypeError, UnicodeEncodeError, ValueError) as error:
        raise ValueError("canonical JSON") from error


def _parse_canonical_json_bytes(
    raw: object,
    *,
    maximum: int,
    json_loads: Callable[..., object] = json.loads,
    reject_pairs: Callable[..., dict[str, object]] = _reject_duplicate_pairs,
    reject_number: Callable[[str], object] = _reject_json_number,
    validate_tree: Callable[..., None] = _validate_json_tree,
    canonical_encoder: Callable[..., bytes] = _canonical_json_bytes,
) -> object:
    if type(raw) is not bytes or not raw or len(raw) > maximum:
        raise ValueError("JSON byte bounds")
    try:
        text = raw.decode("ascii", "strict")
        value = json_loads(
            text,
            object_pairs_hook=reject_pairs,
            parse_float=reject_number,
            parse_constant=reject_number,
        )
    except (
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise ValueError("JSON decode") from error
    validate_tree(value)
    if canonical_encoder(value) != raw:
        raise ValueError("noncanonical JSON")
    return value


def _valid_sha256(
    value: object,
    pattern: re.Pattern[str] = _SHA256_RE,
) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _parse_child_frame(
    raw: bytes,
    *,
    generation_token: str,
    request_sha256: str,
    maximum_frame_bytes: int,
    expected_schema: str,
    expected_keys: frozenset[str],
    reason_pattern: re.Pattern[str],
    parse_canonical_json: Callable[..., object] = _parse_canonical_json_bytes,
    canonical_encoder: Callable[..., bytes] = _canonical_json_bytes,
    sha256_digest: Callable[[bytes], object] = sha256,
    valid_sha256: Callable[[object], bool] = _valid_sha256,
) -> tuple[str, str, bytes | None, bytes | None, str | None]:
    value = parse_canonical_json(raw, maximum=maximum_frame_bytes)
    if type(value) is not dict or set(value) != expected_keys:
        raise ValueError("frame keys")
    if (
        value["schema_version"] != expected_schema
        or value["generation_token"] != generation_token
        or value["request_sha256"] != request_sha256
        or type(value["outcome"]) is not str
        or value["outcome"] not in {"SUCCESS", "FAIL"}
        or type(value["reason_code"]) is not str
        or reason_pattern.fullmatch(value["reason_code"]) is None
        or type(value["actionable"]) is not bool
        or value["actionable"]
        or type(value["broker_order_count"]) is not int
        or value["broker_order_count"] != 0
    ):
        raise ValueError("frame binding")
    if value["outcome"] == "FAIL":
        if (
            value["semantic_receipt"] is not None
            or value["artifact"] is not None
            or value["artifact_sha256"] is not None
        ):
            raise ValueError("failure payload")
        return "FAIL", value["reason_code"], None, None, None
    if (
        type(value["semantic_receipt"]) is not dict
        or type(value["artifact"]) is not dict
        or not valid_sha256(value["artifact_sha256"])
    ):
        raise ValueError("success payload")
    semantic_receipt_bytes = canonical_encoder(value["semantic_receipt"])
    artifact_bytes = canonical_encoder(value["artifact"])
    artifact_sha256 = value["artifact_sha256"]
    if sha256_digest(artifact_bytes).hexdigest() != artifact_sha256:
        raise ValueError("artifact reconstruction")
    return (
        "SUCCESS",
        value["reason_code"],
        semantic_receipt_bytes,
        artifact_bytes,
        artifact_sha256,
    )


def _encode_unbound_child_frame(
    generation_token: str,
    request_sha256: str,
) -> bytes:
    return _canonical_json_bytes(
        {
            "actionable": False,
            "artifact": None,
            "artifact_sha256": None,
            "broker_order_count": 0,
            "generation_token": generation_token,
            "outcome": "FAIL",
            "reason_code": P1_PRODUCTION_CHILD_STATUS,
            "request_sha256": request_sha256,
            "schema_version": P1_CHILD_FRAME_SCHEMA,
            "semantic_receipt": None,
        }
    )


def _unbound_production_decision_child(
    send_connection: Connection,
    generation_token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_monotonic_ns: int,
) -> None:
    """Explicit placeholder: production Decision integration is absent."""

    del request_bytes, deadline_monotonic_ns
    try:
        send_connection.send_bytes(
            _encode_unbound_child_frame(generation_token, request_sha256)
        )
    finally:
        send_connection.close()


def _session_child_entrypoint(
    child_target: Callable[..., None],
    send_connection: Connection,
    generation_token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_monotonic_ns: int,
) -> None:
    """Establish a confirmed session/group before invoking one child target."""

    try:
        previous_signal_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK,
            {signal.SIGUSR1},
        )
        os.setsid()
        process_id = os.getpid()
        if os.getpgrp() != process_id or os.getsid(0) != process_id:
            os._exit(120)
        signal.sigwait({signal.SIGUSR1})
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_signal_mask)
        child_target(
            send_connection,
            generation_token,
            request_bytes,
            request_sha256,
            deadline_monotonic_ns,
        )
    except BaseException:
        os._exit(121)
    finally:
        try:
            send_connection.close()
        except OSError:
            pass


def _is_before_deadline(now_ns: int, deadline_ns: int) -> bool:
    return now_ns < deadline_ns


def _module_level_target(target: object) -> bool:
    return (
        type(target) is FunctionType
        and type(target.__module__) is str
        and type(target.__qualname__) is str
        and "<locals>" not in target.__qualname__
    )


def _confirmed_isolated_group(process_id: int, parent_group_id: int) -> bool:
    if process_id <= 0 or process_id == parent_group_id:
        return False
    try:
        return (
            os.getpgid(process_id) == process_id
            and os.getsid(process_id) == process_id
        )
    except (OSError, ProcessLookupError):
        return False


def _isolated_group_exists(process_group_id: int, parent_group_id: int) -> bool:
    if process_group_id <= 0 or process_group_id == parent_group_id:
        return False
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_isolated_group(
    process_group_id: int,
    parent_group_id: int,
    signal_number: int,
    group_exists: Callable[[int, int], bool] = _isolated_group_exists,
) -> bool:
    if not group_exists(process_group_id, parent_group_id):
        return False
    try:
        os.killpg(process_group_id, signal_number)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def _make_receipt(
    *,
    generation_token: str,
    request_sha256: str,
    started_ns: int,
    deadline_ns: int,
    closed_ns: int,
    child_start_ns: int | None,
    child_exit_ns: int | None,
    completed_ns: int,
    child_pid: int | None,
    child_exitcode: int | None,
    child_process_group_id: int | None,
    child_session_id: int | None,
    parent_process_group_id: int,
    parent_session_id: int,
    process_group_verified: bool,
    isolation_barrier_released: bool,
    process_group_gone: bool,
    descendant_leak_observed: bool,
    deadline_reached: bool,
    messages_observed: int,
    messages_accepted: int,
    late_messages_suppressed: int,
    terminate_sent: bool,
    kill_sent: bool,
    child_reaped: bool,
    reader_joined: bool,
    pipe_closed: bool,
    process_closed: bool,
) -> P1ProcessSupervisorReceiptV1:
    return P1ProcessSupervisorReceiptV1(
        schema_version=P1_SUPERVISOR_RECEIPT_SCHEMA,
        generation_token=generation_token,
        request_sha256=request_sha256,
        supervisor_started_monotonic_ns=started_ns,
        deadline_monotonic_ns=deadline_ns,
        generation_closed_monotonic_ns=closed_ns,
        child_start_monotonic_ns=child_start_ns,
        child_exit_monotonic_ns=child_exit_ns,
        completed_monotonic_ns=completed_ns,
        elapsed_ns=completed_ns - started_ns,
        child_started=child_start_ns is not None,
        child_pid=child_pid,
        child_exitcode=child_exitcode,
        child_process_group_id=child_process_group_id,
        child_session_id=child_session_id,
        parent_process_group_id=parent_process_group_id,
        parent_session_id=parent_session_id,
        process_group_verified=process_group_verified,
        isolation_barrier_released=isolation_barrier_released,
        process_group_gone=process_group_gone,
        descendant_leak_observed=descendant_leak_observed,
        deadline_reached=deadline_reached,
        messages_observed=messages_observed,
        messages_accepted=messages_accepted,
        late_messages_suppressed=late_messages_suppressed,
        terminate_sent=terminate_sent,
        kill_sent=kill_sent,
        child_reaped=child_reaped,
        reader_joined=reader_joined,
        pipe_closed=pipe_closed,
        process_closed=process_closed,
        partial_result_published=False,
        qualification_status="NOT_CLAIMED",
    )


def _failure_result(
    reason_code: str,
    receipt: P1ProcessSupervisorReceiptV1,
    *,
    failure_origin: str,
    child_reason_code: str | None = None,
) -> P1ProcessSupervisorResultV1:
    return P1ProcessSupervisorResultV1(
        status="FAIL_CLOSED",
        reason_code=reason_code,
        failure_origin=failure_origin,
        child_reason_code=child_reason_code,
        semantic_receipt_bytes=None,
        artifact_bytes=None,
        artifact_sha256=None,
        actionable=False,
        broker_order_count=0,
        supervisor_receipt=receipt,
    )


def _make_p1_bound_supervisor_core(
    child_target: Callable[..., None],
) -> _P1BoundSupervisorCoreV1:
    """Capture one spawn-safe module target and the frozen timeout policy."""

    if not _module_level_target(child_target):
        raise TypeError("child target must be a module-level function")
    context = multiprocessing.get_context("spawn")
    captured_target = child_target
    timeout_ns = P1_PROCESS_TIMEOUT_NS
    terminate_grace_ns = P1_TERMINATE_GRACE_NS
    qualification_ceiling_ns = P1_QUALIFICATION_CEILING_NS
    safety_cleanup_ceiling_ns = P1_SAFETY_CLEANUP_CEILING_NS
    safety_cleanup_attempts = _P1_SAFETY_CLEANUP_ATTEMPTS
    safety_monotonic_ns = _P1_SAFETY_MONOTONIC_NS
    maximum_request_bytes = P1_MAX_REQUEST_BYTES
    maximum_frame_bytes = P1_MAX_CHILD_FRAME_BYTES
    expected_frame_schema = P1_CHILD_FRAME_SCHEMA
    expected_frame_keys = frozenset(_FRAME_KEYS)
    reason_pattern = re.compile(_REASON_RE.pattern)
    monotonic_ns = time.monotonic_ns
    canonical_request_parser = _parse_canonical_json_bytes
    child_frame_parser = _parse_child_frame
    sha256_digest = sha256
    valid_sha256 = _valid_sha256
    child_entrypoint = _session_child_entrypoint
    confirmed_isolated_group = _confirmed_isolated_group
    isolated_group_exists = _isolated_group_exists
    signal_isolated_group = _signal_isolated_group
    is_before_deadline = _is_before_deadline
    policy_binding = object()
    policy_snapshot: tuple[object, ...] = (
        timeout_ns,
        terminate_grace_ns,
        qualification_ceiling_ns,
        safety_cleanup_ceiling_ns,
        safety_cleanup_attempts,
        maximum_request_bytes,
        maximum_frame_bytes,
        expected_frame_schema,
        expected_frame_keys,
        reason_pattern.pattern,
        monotonic_ns,
        canonical_request_parser,
        child_frame_parser,
        sha256_digest,
        valid_sha256,
        child_entrypoint,
        confirmed_isolated_group,
        isolated_group_exists,
        signal_isolated_group,
        is_before_deadline,
        safety_monotonic_ns,
    )

    def execute(
        request_bytes: bytes,
        request_sha256: str,
        *,
        started_ns: int,
        invocation_ns: int,
    ) -> P1ProcessSupervisorResultV1:
        deadline_ns = started_ns + timeout_ns
        qualification_deadline_ns = started_ns + qualification_ceiling_ns
        generation_token = secrets.token_hex(32)
        parent_group_id = os.getpgrp()
        parent_session_id = os.getsid(0)

        def deadline_before_child(
            completed_ns: int,
        ) -> P1ProcessSupervisorResultV1:
            receipt = _make_receipt(
                generation_token=generation_token,
                request_sha256=(
                    request_sha256 if valid_sha256(request_sha256) else "0" * 64
                ),
                started_ns=started_ns,
                deadline_ns=deadline_ns,
                closed_ns=completed_ns,
                child_start_ns=None,
                child_exit_ns=None,
                completed_ns=completed_ns,
                child_pid=None,
                child_exitcode=None,
                child_process_group_id=None,
                child_session_id=None,
                parent_process_group_id=parent_group_id,
                parent_session_id=parent_session_id,
                process_group_verified=False,
                isolation_barrier_released=False,
                process_group_gone=True,
                descendant_leak_observed=False,
                deadline_reached=True,
                messages_observed=0,
                messages_accepted=0,
                late_messages_suppressed=0,
                terminate_sent=False,
                kill_sent=False,
                child_reaped=True,
                reader_joined=True,
                pipe_closed=True,
                process_closed=True,
            )
            return _failure_result(
                P1_REASON_HARD_TIMEOUT,
                receipt,
                failure_origin="DEADLINE",
            )

        if invocation_ns >= deadline_ns:
            return deadline_before_child(invocation_ns)
        try:
            request = canonical_request_parser(
                request_bytes,
                maximum=maximum_request_bytes,
            )
            if (
                type(request) is not dict
                or not valid_sha256(request_sha256)
                or sha256_digest(request_bytes).hexdigest() != request_sha256
            ):
                raise ValueError("request binding")
        except (TypeError, ValueError):
            completed_ns = monotonic_ns()
            if completed_ns >= deadline_ns:
                return deadline_before_child(completed_ns)
            receipt = _make_receipt(
                generation_token=generation_token,
                request_sha256=(
                    request_sha256 if valid_sha256(request_sha256) else "0" * 64
                ),
                started_ns=started_ns,
                deadline_ns=deadline_ns,
                closed_ns=completed_ns,
                child_start_ns=None,
                child_exit_ns=None,
                completed_ns=completed_ns,
                child_pid=None,
                child_exitcode=None,
                child_process_group_id=None,
                child_session_id=None,
                parent_process_group_id=parent_group_id,
                parent_session_id=parent_session_id,
                process_group_verified=False,
                isolation_barrier_released=False,
                process_group_gone=True,
                descendant_leak_observed=False,
                deadline_reached=False,
                messages_observed=0,
                messages_accepted=0,
                late_messages_suppressed=0,
                terminate_sent=False,
                kill_sent=False,
                child_reaped=True,
                reader_joined=True,
                pipe_closed=True,
                process_closed=True,
            )
            return _failure_result(
                P1_REASON_INPUT_INVALID,
                receipt,
                failure_origin="INPUT",
            )

        before_child_ns = monotonic_ns()
        if before_child_ns >= deadline_ns:
            return deadline_before_child(before_child_ns)

        receive_connection: Connection | None = None
        send_connection: Connection | None = None
        process: multiprocessing.Process | None = None
        reader: Thread | None = None
        reader_stop = Event()
        state = _ReaderState(
            lock=Lock(),
            generation_open=True,
            frames=[],
        )
        child_start_ns: int | None = None
        child_pid: int | None = None
        child_exit_ns: int | None = None
        child_exitcode: int | None = None
        generation_closed_ns = started_ns
        terminate_sent = False
        kill_sent = False
        child_reaped = False
        reader_joined = True
        pipe_closed = False
        process_closed = False
        deadline_reached = False
        internal_failure = False
        pending_base_exception: BaseException | None = None
        cleanup_previous_signal_mask: set[signal.Signals] | None = None
        process_group_id: int | None = None
        group_confirmed = False
        group_released = False
        containment_violation = False
        containment_empty = True
        qualification_clock_failed = False

        def remember_cleanup_error(error: BaseException) -> None:
            nonlocal internal_failure, pending_base_exception
            internal_failure = True
            if (
                pending_base_exception is None
                and not isinstance(error, Exception)
            ):
                pending_base_exception = error

        def qualification_now_or(fallback_ns: int) -> tuple[int, bool]:
            nonlocal qualification_clock_failed
            try:
                return monotonic_ns(), True
            except BaseException as error:
                qualification_clock_failed = True
                remember_cleanup_error(error)
                return fallback_ns, False

        def restore_cleanup_signal_mask() -> bool:
            nonlocal cleanup_previous_signal_mask
            if cleanup_previous_signal_mask is None:
                return False
            restored = False
            for _ in range(3):
                try:
                    signal.pthread_sigmask(
                        signal.SIG_SETMASK,
                        cleanup_previous_signal_mask,
                    )
                except BaseException as error:
                    remember_cleanup_error(error)
                else:
                    restored = True
                    break
            cleanup_previous_signal_mask = None
            return restored

        try:
            receive_connection, send_connection = context.Pipe(duplex=False)
            process = context.Process(
                target=child_entrypoint,
                args=(
                    captured_target,
                    send_connection,
                    generation_token,
                    request_bytes,
                    request_sha256,
                    deadline_ns,
                ),
            )
            process.daemon = False
            process.start()
            containment_empty = False
            child_pid = process.pid
            child_start_ns = monotonic_ns()
            send_connection.close()
            send_connection = None

            def read_frames() -> None:
                assert receive_connection is not None
                while not reader_stop.is_set():
                    try:
                        if not receive_connection.poll(0.005):
                            continue
                        received_ns = monotonic_ns()
                        raw = receive_connection.recv_bytes(
                            maxlength=maximum_frame_bytes
                        )
                    except EOFError:
                        return
                    except OSError:
                        if reader_stop.is_set():
                            return
                        with state.lock:
                            state.messages_observed += 1
                            if (
                                not state.generation_open
                                or not is_before_deadline(
                                    monotonic_ns(),
                                    deadline_ns,
                                )
                            ):
                                state.late_messages_suppressed += 1
                            else:
                                state.transport_malformed = True
                        return
                    with state.lock:
                        state.messages_observed += 1
                        if (
                            not state.generation_open
                            or not is_before_deadline(received_ns, deadline_ns)
                        ):
                            state.late_messages_suppressed += 1
                        elif len(state.frames) < 2:
                            state.frames.append((received_ns, raw))

            reader = Thread(
                target=read_frames,
                name=f"gld-p1-reader-{generation_token[:8]}",
                daemon=True,
            )
            reader.start()
            reader_joined = False

            while True:
                now_ns = monotonic_ns()
                if not is_before_deadline(now_ns, deadline_ns):
                    deadline_reached = True
                    with state.lock:
                        state.generation_open = False
                        generation_closed_ns = now_ns
                    break
                if (
                    not group_released
                    and process.pid is not None
                    and confirmed_isolated_group(process.pid, parent_group_id)
                ):
                    process_group_id = process.pid
                    group_confirmed = True
                    try:
                        os.kill(process.pid, signal.SIGUSR1)
                    except OSError:
                        internal_failure = True
                        with state.lock:
                            state.generation_open = False
                            generation_closed_ns = monotonic_ns()
                        break
                    group_released = True
                if not process.is_alive():
                    process.join(timeout=0)
                    child_exit_ns = monotonic_ns()
                    while reader.is_alive() and is_before_deadline(
                        monotonic_ns(), deadline_ns
                    ):
                        reader.join(timeout=0.005)
                    if reader.is_alive():
                        continue
                    with state.lock:
                        state.generation_open = False
                        generation_closed_ns = monotonic_ns()
                    if (
                        process_group_id is not None
                        and isolated_group_exists(
                            process_group_id,
                            parent_group_id,
                        )
                    ):
                        containment_violation = True
                    break
                process.join(
                    timeout=min(
                        0.005,
                        max(0, deadline_ns - now_ns) / 1_000_000_000,
                    )
                )
            cleanup_previous_signal_mask = signal.pthread_sigmask(
                signal.SIG_BLOCK,
                {signal.SIGINT, signal.SIGTERM},
            )
        except BaseException as error:
            remember_cleanup_error(error)
            for _ in range(3):
                if cleanup_previous_signal_mask is not None:
                    break
                try:
                    cleanup_previous_signal_mask = signal.pthread_sigmask(
                        signal.SIG_BLOCK,
                        {signal.SIGINT, signal.SIGTERM},
                    )
                except BaseException as masking_error:
                    remember_cleanup_error(masking_error)
            process_pid_observed = True
            try:
                observed_process_pid = (
                    None if process is None else process.pid
                )
            except BaseException as pid_error:
                observed_process_pid = None
                process_pid_observed = False
                remember_cleanup_error(pid_error)
            no_child_started = child_start_ns is None and (
                process is None
                or (process_pid_observed and observed_process_pid is None)
            )
            if no_child_started:
                completed_ns, completed_clock_ok = qualification_now_or(
                    deadline_ns
                )
                start_deadline_reached = (
                    not completed_clock_ok or completed_ns >= deadline_ns
                )
                for _ in range(safety_cleanup_attempts):
                    for connection in (send_connection, receive_connection):
                        if connection is None:
                            continue
                        try:
                            connection_already_closed = connection.closed
                        except BaseException as close_error:
                            connection_already_closed = False
                            remember_cleanup_error(close_error)
                        if connection_already_closed:
                            continue
                        try:
                            connection.close()
                        except OSError:
                            pass
                        except BaseException as close_error:
                            remember_cleanup_error(close_error)
                    try:
                        pipe_closed = all(
                            connection is None or connection.closed
                            for connection in (
                                send_connection,
                                receive_connection,
                            )
                        )
                    except BaseException as close_error:
                        pipe_closed = False
                        remember_cleanup_error(close_error)
                    if process is None:
                        process_closed = True
                    elif not process_closed:
                        try:
                            process.close()
                            process_closed = True
                        except (OSError, ValueError):
                            pass
                        except BaseException as close_error:
                            remember_cleanup_error(close_error)
                    if pipe_closed and process_closed:
                        break
                mask_restored = restore_cleanup_signal_mask()
                start_cleanup_complete = (
                    pipe_closed and process_closed and mask_restored
                )
                if not start_cleanup_complete:
                    cleanup_failure = NormalizationError(
                        P1_REASON_EXECUTION_FAILED
                    )
                    if pending_base_exception is not None:
                        raise cleanup_failure from pending_base_exception
                    raise cleanup_failure
                if pending_base_exception is not None:
                    raise pending_base_exception
                if not completed_clock_ok:
                    raise NormalizationError(P1_REASON_EXECUTION_FAILED)
                if isinstance(error, Exception):
                    receipt = _make_receipt(
                        generation_token=generation_token,
                        request_sha256=request_sha256,
                        started_ns=started_ns,
                        deadline_ns=deadline_ns,
                        closed_ns=completed_ns,
                        child_start_ns=None,
                        child_exit_ns=None,
                        completed_ns=completed_ns,
                        child_pid=None,
                        child_exitcode=None,
                        child_process_group_id=None,
                        child_session_id=None,
                        parent_process_group_id=parent_group_id,
                        parent_session_id=parent_session_id,
                        process_group_verified=False,
                        isolation_barrier_released=False,
                        process_group_gone=True,
                        descendant_leak_observed=False,
                        deadline_reached=start_deadline_reached,
                        messages_observed=0,
                        messages_accepted=0,
                        late_messages_suppressed=0,
                        terminate_sent=False,
                        kill_sent=False,
                        child_reaped=True,
                        reader_joined=True,
                        pipe_closed=pipe_closed,
                        process_closed=process_closed,
                    )
                    return _failure_result(
                        (
                            P1_REASON_HARD_TIMEOUT
                            if start_deadline_reached
                            else P1_REASON_START_FAILED
                        ),
                        receipt,
                        failure_origin=(
                            "DEADLINE" if start_deadline_reached else "START"
                        ),
                    )
                raise
            if child_start_ns is None:
                child_pid = observed_process_pid
                child_start_ns, _ = qualification_now_or(started_ns)
            normalization_now_ns, normalization_clock_ok = (
                qualification_now_or(deadline_ns)
            )
            deadline_reached = (
                not normalization_clock_ok
                or normalization_now_ns >= deadline_ns
            )
            try:
                with state.lock:
                    state.generation_open = False
                    generation_closed_ns = normalization_now_ns
            except BaseException as state_error:
                remember_cleanup_error(state_error)

        assert process is not None

        def process_alive() -> bool:
            if process_closed:
                return False
            try:
                return process.is_alive()
            except BaseException as error:
                remember_cleanup_error(error)
                return True

        def group_alive() -> bool:
            if process_group_id is None:
                return False
            try:
                return isolated_group_exists(
                    process_group_id,
                    parent_group_id,
                )
            except BaseException as error:
                remember_cleanup_error(error)
                return True

        def close_generation(timestamp_ns: int) -> None:
            nonlocal generation_closed_ns
            try:
                with state.lock:
                    state.generation_open = False
                    generation_closed_ns = max(
                        generation_closed_ns,
                        timestamp_ns,
                    )
            except BaseException as error:
                remember_cleanup_error(error)

        def stop_reader_and_close_pipe() -> None:
            nonlocal pipe_closed
            try:
                reader_stop.set()
            except BaseException as error:
                remember_cleanup_error(error)
            for connection in (receive_connection, send_connection):
                if connection is None:
                    continue
                try:
                    connection.close()
                except OSError:
                    pass
                except BaseException as error:
                    remember_cleanup_error(error)
            try:
                pipe_closed = all(
                    connection is None or connection.closed
                    for connection in (receive_connection, send_connection)
                )
            except BaseException as error:
                pipe_closed = False
                remember_cleanup_error(error)

        def join_reader(timeout_seconds: float) -> None:
            nonlocal reader_joined
            if reader is None:
                reader_joined = True
                return
            try:
                if reader.is_alive():
                    reader.join(timeout=max(0.0, timeout_seconds))
                reader_joined = not reader.is_alive()
            except BaseException as error:
                reader_joined = False
                remember_cleanup_error(error)

        def reap_child(timeout_seconds: float) -> None:
            nonlocal child_exit_ns, child_exitcode, child_reaped
            if process_closed:
                return
            try:
                if process.is_alive():
                    process.join(timeout=max(0.0, timeout_seconds))
                if not process.is_alive():
                    process.join(timeout=0)
                    child_reaped = True
                    if child_exit_ns is None:
                        try:
                            child_exit_ns = monotonic_ns()
                        except BaseException as error:
                            child_exit_ns = deadline_ns
                            remember_cleanup_error(error)
                    child_exitcode = process.exitcode
            except BaseException as error:
                remember_cleanup_error(error)

        def refresh_containment() -> None:
            nonlocal containment_empty
            if process_group_id is not None:
                containment_empty = not group_alive()
            else:
                containment_empty = not process_alive()

        def close_process() -> None:
            nonlocal process_closed
            if process_closed or not child_reaped:
                return
            try:
                process.close()
                process_closed = True
            except (OSError, ValueError):
                pass
            except BaseException as error:
                remember_cleanup_error(error)

        def cleanup_invariants_hold() -> bool:
            return (
                child_reaped
                and reader_joined
                and pipe_closed
                and process_closed
                and containment_empty
            )

        cleanup_finished = False
        while not cleanup_finished:
            if qualification_clock_failed:
                break
            try:
                qualification_now_ns = monotonic_ns()
                if not is_before_deadline(
                    qualification_now_ns,
                    qualification_deadline_ns,
                ):
                    break
                if (
                    process_group_id is None
                    and process.pid is not None
                    and confirmed_isolated_group(
                        process.pid,
                        parent_group_id,
                    )
                ):
                    process_group_id = process.pid
                    group_confirmed = True
                needs_termination = (
                    deadline_reached
                    or internal_failure
                    or containment_violation
                    or pending_base_exception is not None
                )
                observed_group_alive = group_alive()
                observed_process_alive = process_alive()
                if needs_termination and (
                    observed_group_alive or observed_process_alive
                ):
                    terminate_sent = True
                    group_signaled = False
                    if process_group_id is not None:
                        try:
                            group_signaled = signal_isolated_group(
                                process_group_id,
                                parent_group_id,
                                signal.SIGTERM,
                            )
                        except BaseException as error:
                            remember_cleanup_error(error)
                    if not group_signaled and process_alive():
                        try:
                            process.terminate()
                        except (OSError, ValueError):
                            pass
                        except BaseException as error:
                            remember_cleanup_error(error)
                    terminate_deadline_ns = min(
                        monotonic_ns() + terminate_grace_ns,
                        qualification_deadline_ns,
                    )
                    while is_before_deadline(
                        monotonic_ns(),
                        terminate_deadline_ns,
                    ) and (process_alive() or group_alive()):
                        reap_child(0.005)
                    if process_alive() or group_alive():
                        kill_sent = True
                        group_killed = False
                        if process_group_id is not None:
                            try:
                                group_killed = signal_isolated_group(
                                    process_group_id,
                                    parent_group_id,
                                    signal.SIGKILL,
                                )
                            except BaseException as error:
                                remember_cleanup_error(error)
                        if not group_killed and process_alive():
                            try:
                                process.kill()
                            except (OSError, ValueError):
                                pass
                            except BaseException as error:
                                remember_cleanup_error(error)
                stop_reader_and_close_pipe()
                try:
                    remaining_seconds = min(
                        0.020,
                        max(
                            0,
                            qualification_deadline_ns - monotonic_ns(),
                        )
                        / 1_000_000_000,
                    )
                except BaseException as error:
                    remaining_seconds = 0.0
                    remember_cleanup_error(error)
                reap_child(remaining_seconds)
                join_reader(remaining_seconds)
                refresh_containment()
                close_process()
                cleanup_finished = cleanup_invariants_hold()
                if not cleanup_finished and remaining_seconds > 0:
                    time.sleep(min(0.002, remaining_seconds))
            except BaseException as cleanup_error:
                remember_cleanup_error(cleanup_error)
                qualification_clock_failed = True
                break

        cleanup_complete = cleanup_invariants_hold()
        qualification_finished_ns, _ = qualification_now_or(
            qualification_deadline_ns
        )
        qualification_cleanup_within_ceiling = (
            cleanup_complete
            and qualification_finished_ns <= qualification_deadline_ns
        )
        if not qualification_cleanup_within_ceiling:
            internal_failure = True
            deadline_reached = (
                deadline_reached or qualification_finished_ns >= deadline_ns
            )
            close_generation(qualification_finished_ns)

        if not cleanup_complete:
            try:
                safety_started_ns = safety_monotonic_ns()
            except BaseException as error:
                safety_started_ns = 0
                remember_cleanup_error(error)
            safety_deadline_ns = (
                safety_started_ns + safety_cleanup_ceiling_ns
            )

            def safety_remaining_seconds(maximum: float) -> float:
                try:
                    remaining_ns = max(
                        0,
                        safety_deadline_ns - safety_monotonic_ns(),
                    )
                except BaseException as error:
                    remember_cleanup_error(error)
                    return 0.0
                return min(maximum, remaining_ns / 1_000_000_000)

            def safety_now_ns() -> int:
                try:
                    return safety_monotonic_ns()
                except BaseException as error:
                    remember_cleanup_error(error)
                    return safety_deadline_ns

            for _ in range(safety_cleanup_attempts):
                if cleanup_invariants_hold():
                    break
                try:
                    candidate_process_id = process.pid
                except BaseException as error:
                    candidate_process_id = None
                    remember_cleanup_error(error)
                if process_group_id is None and candidate_process_id is not None:
                    try:
                        if confirmed_isolated_group(
                            candidate_process_id,
                            parent_group_id,
                        ):
                            process_group_id = candidate_process_id
                            group_confirmed = True
                    except BaseException as error:
                        remember_cleanup_error(error)
                if process_alive() or group_alive():
                    terminate_sent = True
                    group_signaled = False
                    if process_group_id is not None:
                        try:
                            group_signaled = signal_isolated_group(
                                process_group_id,
                                parent_group_id,
                                signal.SIGTERM,
                            )
                        except BaseException as error:
                            remember_cleanup_error(error)
                    if not group_signaled and process_alive():
                        try:
                            process.terminate()
                        except (OSError, ValueError):
                            pass
                        except BaseException as error:
                            remember_cleanup_error(error)
                    grace_seconds = safety_remaining_seconds(
                        terminate_grace_ns / 1_000_000_000
                    )
                    grace_deadline_ns = min(
                        safety_deadline_ns,
                        safety_now_ns()
                        + int(grace_seconds * 1_000_000_000),
                    )
                    while (
                        safety_now_ns() < grace_deadline_ns
                        and (process_alive() or group_alive())
                    ):
                        reap_child(
                            min(0.005, safety_remaining_seconds(0.005))
                        )
                    if process_alive() or group_alive():
                        kill_sent = True
                        group_killed = False
                        if process_group_id is not None:
                            try:
                                group_killed = signal_isolated_group(
                                    process_group_id,
                                    parent_group_id,
                                    signal.SIGKILL,
                                )
                            except BaseException as error:
                                remember_cleanup_error(error)
                        if not group_killed and process_alive():
                            try:
                                process.kill()
                            except (OSError, ValueError):
                                pass
                            except BaseException as error:
                                remember_cleanup_error(error)
                stop_reader_and_close_pipe()
                reap_child(safety_remaining_seconds(0.050))
                join_reader(safety_remaining_seconds(0.050))
                group_probe_deadline_ns = min(
                    safety_deadline_ns,
                    safety_now_ns() + 50_000_000,
                )
                while (
                    safety_now_ns() < group_probe_deadline_ns
                    and group_alive()
                ):
                    try:
                        time.sleep(0.002)
                    except BaseException as error:
                        remember_cleanup_error(error)
                        break
                refresh_containment()
                close_process()
                cleanup_complete = cleanup_invariants_hold()

        cleanup_complete = cleanup_invariants_hold()
        mask_restored = restore_cleanup_signal_mask()
        if not cleanup_complete or not mask_restored:
            cleanup_failure = NormalizationError(
                P1_REASON_EXECUTION_FAILED
            )
            if pending_base_exception is not None:
                raise cleanup_failure from pending_base_exception
            raise cleanup_failure
        if pending_base_exception is not None:
            raise pending_base_exception

        with state.lock:
            frames = tuple(state.frames)
            messages_observed = state.messages_observed
            late_messages_suppressed = state.late_messages_suppressed
            transport_malformed = state.transport_malformed

        reason_code: str
        failure_origin: str
        child_reason_code: str | None = None
        accepted = 0
        semantic_receipt_bytes: bytes | None = None
        artifact_bytes: bytes | None = None
        artifact_sha256: str | None = None
        if deadline_reached:
            reason_code = P1_REASON_HARD_TIMEOUT
            failure_origin = "DEADLINE"
        elif containment_violation:
            reason_code = P1_REASON_DESCENDANT_LEAK
            failure_origin = "DESCENDANT"
        elif internal_failure:
            reason_code = P1_REASON_EXECUTION_FAILED
            failure_origin = "SUPERVISOR"
        elif messages_observed > 1:
            reason_code = P1_REASON_MULTIPLE_OUTPUTS
            failure_origin = "CHILD_PROTOCOL"
        elif transport_malformed:
            reason_code = P1_REASON_OUTPUT_MALFORMED
            failure_origin = "CHILD_PROTOCOL"
        elif child_exitcode is None or child_exitcode != 0:
            reason_code = P1_REASON_CHILD_CRASHED
            failure_origin = "CHILD_EXIT"
        elif messages_observed == 0:
            reason_code = P1_REASON_OUTPUT_MISSING
            failure_origin = "CHILD_PROTOCOL"
        elif len(frames) != 1:
            reason_code = P1_REASON_OUTPUT_MALFORMED
            failure_origin = "CHILD_PROTOCOL"
        elif (
            child_exit_ns is None
            or not is_before_deadline(child_exit_ns, deadline_ns)
            or not is_before_deadline(frames[0][0], deadline_ns)
        ):
            reason_code = P1_REASON_HARD_TIMEOUT
            failure_origin = "DEADLINE"
            deadline_reached = True
        else:
            try:
                (
                    child_outcome,
                    parsed_child_reason_code,
                    semantic_receipt_bytes,
                    artifact_bytes,
                    artifact_sha256,
                ) = child_frame_parser(
                    frames[0][1],
                    generation_token=generation_token,
                    request_sha256=request_sha256,
                    maximum_frame_bytes=maximum_frame_bytes,
                    expected_schema=expected_frame_schema,
                    expected_keys=expected_frame_keys,
                    reason_pattern=reason_pattern,
                )
            except (TypeError, ValueError):
                reason_code = P1_REASON_OUTPUT_MALFORMED
                failure_origin = "CHILD_PROTOCOL"
                semantic_receipt_bytes = None
                artifact_bytes = None
                artifact_sha256 = None
            else:
                if child_outcome == "SUCCESS":
                    if is_before_deadline(monotonic_ns(), deadline_ns):
                        reason_code = P1_REASON_SUCCESS
                        failure_origin = "NONE"
                        accepted = 1
                    else:
                        reason_code = P1_REASON_HARD_TIMEOUT
                        failure_origin = "DEADLINE"
                        deadline_reached = True
                        semantic_receipt_bytes = None
                        artifact_bytes = None
                        artifact_sha256 = None
                else:
                    reason_code = P1_REASON_EXECUTION_FAILED
                    failure_origin = "CHILD_DECLARED"
                    child_reason_code = parsed_child_reason_code
                    semantic_receipt_bytes = None
                    artifact_bytes = None
                    artifact_sha256 = None

        completed_ns = monotonic_ns()
        if not is_before_deadline(completed_ns, deadline_ns):
            reason_code = P1_REASON_HARD_TIMEOUT
            failure_origin = "DEADLINE"
            child_reason_code = None
            deadline_reached = True
            accepted = 0
            semantic_receipt_bytes = None
            artifact_bytes = None
            artifact_sha256 = None
        receipt = _make_receipt(
            generation_token=generation_token,
            request_sha256=request_sha256,
            started_ns=started_ns,
            deadline_ns=deadline_ns,
            closed_ns=generation_closed_ns,
            child_start_ns=child_start_ns,
            child_exit_ns=child_exit_ns,
            completed_ns=completed_ns,
            child_pid=child_pid,
            child_exitcode=child_exitcode,
            child_process_group_id=(
                process_group_id if group_confirmed else None
            ),
            child_session_id=(process_group_id if group_confirmed else None),
            parent_process_group_id=parent_group_id,
            parent_session_id=parent_session_id,
            process_group_verified=group_confirmed,
            isolation_barrier_released=group_released,
            process_group_gone=containment_empty,
            descendant_leak_observed=containment_violation,
            deadline_reached=deadline_reached,
            messages_observed=messages_observed,
            messages_accepted=accepted,
            late_messages_suppressed=late_messages_suppressed,
            terminate_sent=terminate_sent,
            kill_sent=kill_sent,
            child_reaped=child_reaped,
            reader_joined=reader_joined,
            pipe_closed=pipe_closed,
            process_closed=process_closed,
        )
        if reason_code != P1_REASON_SUCCESS:
            return _failure_result(
                reason_code,
                receipt,
                failure_origin=failure_origin,
                child_reason_code=child_reason_code,
            )
        assert semantic_receipt_bytes is not None
        assert artifact_bytes is not None
        assert artifact_sha256 is not None
        return P1ProcessSupervisorResultV1(
            status="SUCCESS",
            reason_code=P1_REASON_SUCCESS,
            failure_origin="NONE",
            child_reason_code=None,
            semantic_receipt_bytes=semantic_receipt_bytes,
            artifact_bytes=artifact_bytes,
            artifact_sha256=artifact_sha256,
            actionable=False,
            broker_order_count=0,
            supervisor_receipt=receipt,
        )

    def run(
        request_bytes: bytes,
        request_sha256: str,
    ) -> P1ProcessSupervisorResultV1:
        started_ns = monotonic_ns()
        return execute(
            request_bytes,
            request_sha256,
            started_ns=started_ns,
            invocation_ns=started_ns,
        )

    def run_workflow(
        workflow_started_monotonic_ns: int,
        request_bytes: bytes,
        request_sha256: str,
    ) -> P1ProcessSupervisorResultV1:
        invocation_ns = monotonic_ns()
        if (
            type(workflow_started_monotonic_ns) is not int
            or workflow_started_monotonic_ns < 0
            or workflow_started_monotonic_ns > invocation_ns
        ):
            raise TypeError("invalid trusted workflow start")
        return execute(
            request_bytes,
            request_sha256,
            started_ns=workflow_started_monotonic_ns,
            invocation_ns=invocation_ns,
        )

    return _P1BoundSupervisorCoreV1(
        run=run,
        run_workflow=run_workflow,
        monotonic_ns=monotonic_ns,
        timeout_ns=timeout_ns,
        child_target=captured_target,
        policy_binding=policy_binding,
        policy_snapshot=policy_snapshot,
    )


def _make_p1_process_supervisor(
    child_target: Callable[..., None],
) -> Callable[[bytes, str], P1ProcessSupervisorResultV1]:
    return _make_p1_bound_supervisor_core(child_target).run


def _make_p1_process_supervisor_for_test(
    child_target: Callable[..., None],
) -> Callable[[bytes, str], P1ProcessSupervisorResultV1]:
    return _make_p1_process_supervisor(child_target)


def _make_p1_issued_process_supervisor(
    child_target: Callable[..., None],
) -> tuple[
    Callable[[bytes, str], _P1IssuedSupervisorResultV1],
    Callable[[object, str], P1ProcessSupervisorResultV1 | None],
]:
    """Bind one integration-only runner to one-shot identity capabilities.

    ``run_issued`` retains the original supervisor result in a bounded,
    factory-local registry and returns only an opaque process-local object.
    ``consume`` atomically removes that entry before validating every binding,
    so even a failed consume attempt cannot expose or later reuse a result.
    """

    bound_runner = _make_p1_process_supervisor(child_target)
    captured_target = child_target
    factory_binding = object()
    policy_binding = object()
    policy_snapshot: tuple[object, ...] = (
        P1_PROCESS_TIMEOUT_NS,
        P1_TERMINATE_GRACE_NS,
        P1_QUALIFICATION_CEILING_NS,
        P1_SAFETY_CLEANUP_CEILING_NS,
        P1_MAX_REQUEST_BYTES,
        P1_MAX_CHILD_FRAME_BYTES,
        P1_CHILD_FRAME_SCHEMA,
        frozenset(_FRAME_KEYS),
        _SHA256_RE.pattern,
        _REASON_RE.pattern,
    )
    maximum_pending = _P1_MAX_PENDING_ISSUED_RESULTS
    valid_sha256 = _valid_sha256
    registry_lock = Lock()
    registry: OrderedDict[int, _P1IssuedRegistryEntryV1] = OrderedDict()
    capability_type = _P1IssuedSupervisorResultV1
    factory_slot = "_P1IssuedSupervisorResultV1__factory_binding"
    invocation_slot = "_P1IssuedSupervisorResultV1__invocation_binding"
    request_slot = "_P1IssuedSupervisorResultV1__request_sha256"
    child_target_slot = "_P1IssuedSupervisorResultV1__child_target"
    policy_slot = "_P1IssuedSupervisorResultV1__policy_binding"

    def run_issued(
        request_bytes: bytes,
        request_sha256: str,
    ) -> _P1IssuedSupervisorResultV1:
        original_result = bound_runner(request_bytes, request_sha256)
        invocation_binding = object()
        request_binding = request_sha256 if type(request_sha256) is str else ""
        issued = object.__new__(capability_type)
        object.__setattr__(issued, factory_slot, factory_binding)
        object.__setattr__(issued, invocation_slot, invocation_binding)
        object.__setattr__(issued, request_slot, request_binding)
        object.__setattr__(issued, child_target_slot, captured_target)
        object.__setattr__(issued, policy_slot, policy_binding)
        issued_identity = id(issued)

        def discard_issued(
            dead_reference: ReferenceType[_P1IssuedSupervisorResultV1],
            identity: int = issued_identity,
        ) -> None:
            with registry_lock:
                entry = registry.get(identity)
                if (
                    entry is not None
                    and entry.issued_reference is dead_reference
                ):
                    del registry[identity]

        issued_reference = ref(issued, discard_issued)
        entry = _P1IssuedRegistryEntryV1(
            issued_reference=issued_reference,
            factory_binding=factory_binding,
            invocation_binding=invocation_binding,
            request_sha256=request_binding,
            child_target=captured_target,
            policy_binding=policy_binding,
            policy_snapshot=policy_snapshot,
            result=original_result,
        )
        with registry_lock:
            for identity, pending in tuple(registry.items()):
                if pending.issued_reference() is None:
                    del registry[identity]
            while len(registry) >= maximum_pending:
                registry.popitem(last=False)
            registry[issued_identity] = entry
        return issued

    def consume(
        issued: object,
        expected_request_sha256: str,
    ) -> P1ProcessSupervisorResultV1 | None:
        if type(issued) is not capability_type:
            return None
        with registry_lock:
            entry = registry.get(id(issued))
            if (
                entry is None
                or entry.issued_reference() is not issued
            ):
                return None
            del registry[id(issued)]
            try:
                issued_request_sha256 = getattr(issued, request_slot)
                bindings_are_intact = (
                    getattr(issued, factory_slot) is factory_binding
                    and getattr(issued, invocation_slot)
                    is entry.invocation_binding
                    and type(issued_request_sha256) is str
                    and issued_request_sha256 == entry.request_sha256
                    and getattr(issued, child_target_slot) is captured_target
                    and getattr(issued, policy_slot) is policy_binding
                )
            except (AttributeError, TypeError):
                return None
            if (
                not bindings_are_intact
                or type(expected_request_sha256) is not str
                or expected_request_sha256 != entry.request_sha256
                or entry.factory_binding is not factory_binding
                or entry.child_target is not captured_target
                or entry.policy_binding is not policy_binding
                or entry.policy_snapshot is not policy_snapshot
                or entry.result.supervisor_receipt.request_sha256
                != (
                    expected_request_sha256
                    if valid_sha256(expected_request_sha256)
                    else "0" * 64
                )
            ):
                return None
            return entry.result

    return run_issued, consume


def _make_p1_workflow_process_supervisor(
    child_target: Callable[..., None],
) -> tuple[
    Callable[[], _P1WorkflowTicketV1],
    Callable[
        [object, bytes, str],
        _P1WorkflowIssuedResultV1 | None,
    ],
    Callable[
        [object, object, str],
        P1ProcessSupervisorResultV1 | None,
    ],
    Callable[
        [object, str],
        _P1WorkflowTimingFinalizationV1 | None,
    ],
]:
    """Bind one full parent/child workflow to one fixed five-second budget."""

    core = _make_p1_bound_supervisor_core(child_target)
    if type(core.timeout_ns) is not int or core.timeout_ns != 5_000_000_000:
        raise RuntimeError("workflow supervisor requires the fixed five-second policy")
    captured_target = core.child_target
    monotonic_ns = core.monotonic_ns
    timeout_ns = core.timeout_ns
    policy_binding = core.policy_binding
    policy_snapshot = core.policy_snapshot
    run_workflow_core = core.run_workflow
    valid_sha256 = _valid_sha256
    maximum_pending = _P1_MAX_PENDING_ISSUED_RESULTS
    factory_binding = object()
    registry_lock = Lock()
    registry: OrderedDict[int, _P1WorkflowRegistryEntryV1] = OrderedDict()
    ticket_type = _P1WorkflowTicketV1
    issued_type = _P1WorkflowIssuedResultV1

    ticket_factory_slot = "_P1WorkflowTicketV1__factory_binding"
    ticket_workflow_slot = "_P1WorkflowTicketV1__workflow_binding"
    ticket_target_slot = "_P1WorkflowTicketV1__child_target"
    ticket_policy_slot = "_P1WorkflowTicketV1__policy_binding"
    ticket_started_slot = "_P1WorkflowTicketV1__started_monotonic_ns"
    ticket_deadline_slot = "_P1WorkflowTicketV1__deadline_monotonic_ns"
    issued_factory_slot = "_P1WorkflowIssuedResultV1__factory_binding"
    issued_workflow_slot = "_P1WorkflowIssuedResultV1__workflow_binding"
    issued_invocation_slot = "_P1WorkflowIssuedResultV1__invocation_binding"
    issued_request_slot = "_P1WorkflowIssuedResultV1__request_sha256"
    issued_target_slot = "_P1WorkflowIssuedResultV1__child_target"
    issued_policy_slot = "_P1WorkflowIssuedResultV1__policy_binding"

    def ticket_is_intact(
        ticket: _P1WorkflowTicketV1,
        entry: _P1WorkflowRegistryEntryV1,
    ) -> bool:
        try:
            started = getattr(ticket, ticket_started_slot)
            deadline = getattr(ticket, ticket_deadline_slot)
            return (
                getattr(ticket, ticket_factory_slot) is factory_binding
                and getattr(ticket, ticket_workflow_slot)
                is entry.workflow_binding
                and getattr(ticket, ticket_target_slot) is captured_target
                and getattr(ticket, ticket_policy_slot) is policy_binding
                and type(started) is int
                and started == entry.started_monotonic_ns
                and type(deadline) is int
                and deadline == entry.deadline_monotonic_ns
                and deadline == started + timeout_ns
                and entry.factory_binding is factory_binding
                and entry.child_target is captured_target
                and entry.policy_binding is policy_binding
                and entry.policy_snapshot is policy_snapshot
            )
        except (AttributeError, TypeError):
            return False

    def issued_is_intact(
        issued: _P1WorkflowIssuedResultV1,
        entry: _P1WorkflowRegistryEntryV1,
    ) -> bool:
        try:
            request_sha256 = getattr(issued, issued_request_slot)
            return (
                getattr(issued, issued_factory_slot) is factory_binding
                and getattr(issued, issued_workflow_slot)
                is entry.workflow_binding
                and getattr(issued, issued_invocation_slot)
                is entry.invocation_binding
                and type(request_sha256) is str
                and request_sha256 == entry.request_sha256
                and getattr(issued, issued_target_slot) is captured_target
                and getattr(issued, issued_policy_slot) is policy_binding
            )
        except (AttributeError, TypeError):
            return False

    def begin_workflow() -> _P1WorkflowTicketV1:
        started_ns = monotonic_ns()
        if type(started_ns) is not int or started_ns < 0:
            raise TypeError("monotonic clock must return a non-negative integer")
        deadline_ns = started_ns + timeout_ns
        workflow_binding = object()
        ticket = object.__new__(ticket_type)
        object.__setattr__(ticket, ticket_factory_slot, factory_binding)
        object.__setattr__(ticket, ticket_workflow_slot, workflow_binding)
        object.__setattr__(ticket, ticket_target_slot, captured_target)
        object.__setattr__(ticket, ticket_policy_slot, policy_binding)
        object.__setattr__(ticket, ticket_started_slot, started_ns)
        object.__setattr__(ticket, ticket_deadline_slot, deadline_ns)
        ticket_identity = id(ticket)

        def discard_ticket(
            dead_reference: ReferenceType[_P1WorkflowTicketV1],
            identity: int = ticket_identity,
        ) -> None:
            with registry_lock:
                entry = registry.get(identity)
                if (
                    entry is not None
                    and entry.ticket_reference is dead_reference
                ):
                    del registry[identity]

        ticket_reference = ref(ticket, discard_ticket)
        entry = _P1WorkflowRegistryEntryV1(
            ticket_reference=ticket_reference,
            factory_binding=factory_binding,
            workflow_binding=workflow_binding,
            child_target=captured_target,
            policy_binding=policy_binding,
            policy_snapshot=policy_snapshot,
            started_monotonic_ns=started_ns,
            deadline_monotonic_ns=deadline_ns,
        )
        with registry_lock:
            for identity, pending in tuple(registry.items()):
                if pending.ticket_reference() is None:
                    del registry[identity]
            while len(registry) >= maximum_pending:
                registry.popitem(last=False)
            registry[ticket_identity] = entry
        return ticket

    def run_issued(
        ticket: object,
        request_bytes: bytes,
        request_sha256: str,
    ) -> _P1WorkflowIssuedResultV1 | None:
        if type(ticket) is not ticket_type:
            return None
        with registry_lock:
            entry = registry.get(id(ticket))
            if (
                entry is None
                or entry.ticket_reference() is not ticket
            ):
                return None
            if not ticket_is_intact(ticket, entry):
                del registry[id(ticket)]
                return None
            if entry.state != "NEW":
                return None
            entry.state = "RUNNING"
            entry.request_sha256 = (
                request_sha256 if type(request_sha256) is str else ""
            )
            entry.invocation_binding = object()
            started_ns = entry.started_monotonic_ns
            invocation_binding = entry.invocation_binding
            request_binding = entry.request_sha256
            workflow_binding = entry.workflow_binding
        try:
            original_result = run_workflow_core(
                started_ns,
                request_bytes,
                request_sha256,
            )
        except BaseException:
            with registry_lock:
                current = registry.get(id(ticket))
                if (
                    current is entry
                    and current.ticket_reference() is ticket
                    and current.state == "RUNNING"
                    and current.invocation_binding is invocation_binding
                ):
                    del registry[id(ticket)]
            raise

        issued = object.__new__(issued_type)
        object.__setattr__(issued, issued_factory_slot, factory_binding)
        object.__setattr__(issued, issued_workflow_slot, workflow_binding)
        object.__setattr__(issued, issued_invocation_slot, invocation_binding)
        object.__setattr__(issued, issued_request_slot, request_binding)
        object.__setattr__(issued, issued_target_slot, captured_target)
        object.__setattr__(issued, issued_policy_slot, policy_binding)

        def discard_issued(
            dead_reference: ReferenceType[_P1WorkflowIssuedResultV1],
            ticket_identity: int = id(ticket),
            invocation: object = invocation_binding,
        ) -> None:
            with registry_lock:
                pending = registry.get(ticket_identity)
                if (
                    pending is not None
                    and pending.state == "ISSUED"
                    and pending.invocation_binding is invocation
                    and pending.issued_reference is dead_reference
                ):
                    del registry[ticket_identity]

        issued_reference = ref(issued, discard_issued)
        with registry_lock:
            current = registry.get(id(ticket))
            if (
                current is not entry
                or current.ticket_reference() is not ticket
                or current.state != "RUNNING"
                or current.invocation_binding is not invocation_binding
                or not ticket_is_intact(ticket, current)
            ):
                if current is entry:
                    del registry[id(ticket)]
                return None
            current.state = "ISSUED"
            current.issued_reference = issued_reference
            current.result = original_result
        return issued

    def consume(
        ticket: object,
        issued: object,
        expected_request_sha256: str,
    ) -> P1ProcessSupervisorResultV1 | None:
        if type(ticket) is not ticket_type or type(issued) is not issued_type:
            return None
        with registry_lock:
            entry = registry.get(id(ticket))
            if (
                entry is None
                or entry.ticket_reference() is not ticket
            ):
                return None
            if not ticket_is_intact(ticket, entry):
                del registry[id(ticket)]
                return None
            if (
                entry.state != "ISSUED"
                or entry.issued_reference is None
                or entry.issued_reference() is not issued
            ):
                return None
            entry.state = "CONSUMED"
            entry.issued_reference = None
            original_result = entry.result
            entry.result = None
            request_matches = (
                issued_is_intact(issued, entry)
                and type(expected_request_sha256) is str
                and expected_request_sha256 == entry.request_sha256
                and original_result is not None
                and original_result.supervisor_receipt.request_sha256
                == (
                    expected_request_sha256
                    if valid_sha256(expected_request_sha256)
                    else "0" * 64
                )
            )
            if not request_matches:
                entry.state = "INVALID"
                return None
            entry.consume_succeeded = True
            return original_result

    def finalize(
        ticket: object,
        expected_request_sha256: str,
    ) -> _P1WorkflowTimingFinalizationV1 | None:
        if type(ticket) is not ticket_type:
            return None
        with registry_lock:
            entry = registry.get(id(ticket))
            if (
                entry is None
                or entry.ticket_reference() is not ticket
            ):
                return None
            if not ticket_is_intact(ticket, entry):
                del registry[id(ticket)]
                return None
            if entry.state != "CONSUMED" or not entry.consume_succeeded:
                return None
            del registry[id(ticket)]
            if (
                type(expected_request_sha256) is not str
                or expected_request_sha256 != entry.request_sha256
            ):
                return None
            completed_ns = monotonic_ns()
            if (
                type(completed_ns) is not int
                or completed_ns < entry.started_monotonic_ns
            ):
                return None
            return _P1WorkflowTimingFinalizationV1(
                workflow_started_monotonic_ns=entry.started_monotonic_ns,
                deadline_monotonic_ns=entry.deadline_monotonic_ns,
                completed_monotonic_ns=completed_ns,
                before_deadline=(completed_ns < entry.deadline_monotonic_ns),
            )

    return begin_workflow, run_issued, consume, finalize


run_p1_process_supervisor = _make_p1_process_supervisor(
    _unbound_production_decision_child
)


__all__ = (
    "P1_CHILD_FRAME_SCHEMA",
    "P1_MAX_CHILD_FRAME_BYTES",
    "P1_MAX_REQUEST_BYTES",
    "P1_PROCESS_TIMEOUT_NS",
    "P1_PRODUCTION_CHILD_STATUS",
    "P1ProcessSupervisorReceiptV1",
    "P1ProcessSupervisorResultV1",
    "run_p1_process_supervisor",
)
