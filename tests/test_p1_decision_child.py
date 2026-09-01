from __future__ import annotations

import ast
from copy import copy, deepcopy
from dataclasses import fields, is_dataclass, replace
from hashlib import sha256
import inspect
import json
import os
from pathlib import Path
import pickle
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from benchmarks.crr_p1_contract import load_corpus
from benchmarks.crr_p1_decision_contract import (
    BACKEND_EVIDENCE_SCHEMA_VERSION,
    load_decision_semantic_golden,
    validate_backend_evidence_envelope,
)
from gld_research_core.p1_native_decision import (
    P1_CONTRACT_SHA256,
    RESEARCH_CONTRACT_SHA256,
)
import gld_research_core.p1_decision_child as child_module
import gld_research_core.p1_process_supervisor as supervisor_module
from gld_research_core.p1_decision_child import (
    P1DecisionChildArtifactV1,
    P1DecisionChildIntegrationResultV1,
    P1DecisionChildSemanticReceiptV1,
    P1DecisionWorkflowTimingReceiptV1,
    P1DecisionRequestV1,
    _production_decision_child,
    _validate_p1_decision_child_result,
    create_p1_decision_child_runner,
    decode_p1_decision_request,
    encode_p1_decision_request,
)
from gld_research_core.p1_process_supervisor import (
    P1_MAX_REQUEST_BYTES,
    P1_REASON_CHILD_CRASHED,
    P1_REASON_EXECUTION_FAILED,
    P1_REASON_HARD_TIMEOUT,
    P1_REASON_INPUT_INVALID,
    P1_REASON_OUTPUT_MISSING,
    P1_REASON_START_FAILED,
)
from tools.build_crr_native import build_native_kernel_v1


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "gld_research_core" / "p1_decision_child.py"
U64_PROJECTION_SHA256 = (
    "5dd66c686c0c7df9f05b63f5807a2b748f1a1f83f1b271c7fb76366e8132664c"
)
U64_SEMANTIC_SHA256 = (
    "7f18fa70313c0d8dfa672e62757d06c66e99a12a7a197eb6aaeb9ccdc3c60541"
)
W64_PROJECTION_SHA256 = (
    "62d584710ff9608958ef199f8397c1c347632f08cfaafec958396d1eea80ebfd"
)
W64_SEMANTIC_SHA256 = (
    "e230592bf5325fac9942ea3c499de77a70663b8dd42909c956164fb7e09799c4"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def _target_output_missing(
    send_connection: object,
    generation_token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_monotonic_ns: int,
) -> None:
    del generation_token, request_bytes, request_sha256, deadline_monotonic_ns
    send_connection.close()


def _target_forever(
    send_connection: object,
    generation_token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_monotonic_ns: int,
) -> None:
    del send_connection, generation_token, request_bytes, request_sha256
    del deadline_monotonic_ns
    while True:
        time.sleep(0.02)


class _FakeMonotonicClock:
    def __init__(self, now_ns: int) -> None:
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, delta_ns: int) -> None:
        self.now_ns += delta_ns


class _OffsetMonotonicClock:
    def __init__(self, base_clock: object) -> None:
        self.base_clock = base_clock
        self.offset_ns = 0

    def __call__(self) -> int:
        return self.base_clock() + self.offset_ns


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _golden_document(fixture_id: str) -> dict[str, object]:
    path = (
        ROOT
        / "benchmarks"
        / "fixtures"
        / f"{fixture_id.lower()}_decision_semantic_golden_v0.1.json"
    )
    value = json.loads(path.read_bytes())
    if type(value) is not dict:
        raise AssertionError("golden root must be a dictionary")
    return value


def _encode_corpus(
    corpus: object,
    *,
    signal: object | None = None,
) -> tuple[bytes, str]:
    return encode_p1_decision_request(
        signal=corpus.signal if signal is None else signal,
        candidate_snapshots=(corpus.snapshot,),
        pit_inputs=corpus.pit_inputs,
        lc0_binding=corpus.lc0_binding,
        fees=corpus.fees,
        research_contract_sha256=RESEARCH_CONTRACT_SHA256,
        p1_contract_sha256=P1_CONTRACT_SHA256,
        rule_package_version=corpus.signal.rule_version,
        quantity=1,
    )


def _mapping_with_key(value: object, key: str) -> dict[str, object]:
    if type(value) is dict:
        if key in value:
            return value
        for item in value.values():
            try:
                return _mapping_with_key(item, key)
            except KeyError:
                pass
    elif type(value) is list:
        for item in value:
            try:
                return _mapping_with_key(item, key)
            except KeyError:
                pass
    raise KeyError(key)


def _unsafe_clone(value: object, **updates: object) -> object:
    if not is_dataclass(value) or isinstance(value, type):
        raise AssertionError("unsafe clone requires one dataclass instance")
    clone = object.__new__(type(value))
    known = {field.name for field in fields(value)}
    if not set(updates).issubset(known):
        raise AssertionError("unknown unsafe-clone field")
    for field in fields(value):
        object.__setattr__(
            clone,
            field.name,
            updates.get(field.name, getattr(value, field.name)),
        )
    return clone


class P1DecisionChildRequestCodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.u64 = load_corpus("U64")

    def test_exact_request_round_trip_is_canonical_and_hash_bound(self) -> None:
        request_bytes, request_sha256 = _encode_corpus(self.u64)

        self.assertIs(type(request_bytes), bytes)
        self.assertLessEqual(len(request_bytes), P1_MAX_REQUEST_BYTES)
        self.assertEqual(request_sha256, sha256(request_bytes).hexdigest())
        self.assertEqual(request_bytes, _canonical_bytes(json.loads(request_bytes)))
        decoded = decode_p1_decision_request(request_bytes, request_sha256)
        self.assertIs(type(decoded), P1DecisionRequestV1)
        self.assertEqual(decoded.signal, self.u64.signal)
        self.assertEqual(decoded.candidate_snapshots, (self.u64.snapshot,))
        self.assertEqual(decoded.pit_inputs, self.u64.pit_inputs)
        self.assertEqual(decoded.lc0_binding, self.u64.lc0_binding)
        self.assertEqual(decoded.fees, self.u64.fees)
        self.assertEqual(decoded.research_contract_sha256, RESEARCH_CONTRACT_SHA256)
        self.assertEqual(decoded.p1_contract_sha256, P1_CONTRACT_SHA256)
        self.assertEqual(decoded.rule_package_version, self.u64.signal.rule_version)
        self.assertEqual(decoded.quantity, 1)
        self.assertIsNotNone(_SHA256_RE.fullmatch(decoded.request_sha256))
        decoded_again = decode_p1_decision_request(request_bytes, request_sha256)
        self.assertEqual(decoded_again, decoded)
        self.assertEqual(decoded_again.request_sha256, decoded.request_sha256)

    def test_decoder_rejects_non_exact_or_noncanonical_requests(self) -> None:
        canonical, canonical_sha256 = _encode_corpus(self.u64)
        document = json.loads(canonical)

        bool_document = deepcopy(document)
        _mapping_with_key(bool_document, "quantity")["quantity"] = True
        float_document = deepcopy(document)
        _mapping_with_key(float_document, "quantity")["quantity"] = 1.0
        unknown_document = deepcopy(document)
        unknown_document["unknown_field"] = 1
        count_document = deepcopy(document)
        quotes = _mapping_with_key(count_document, "option_quotes")["option_quotes"]
        self.assertIs(type(quotes), list)
        quotes.pop()

        reversed_document = dict(reversed(tuple(document.items())))
        reordered = json.dumps(
            reversed_document,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
        first_key = next(iter(document))
        duplicate = (
            b"{"
            + json.dumps(first_key).encode("ascii")
            + b":"
            + _canonical_bytes(document[first_key])
            + b","
            + canonical[1:]
        )
        oversized = b'{"padding":"' + b"x" * P1_MAX_REQUEST_BYTES + b'"}'
        cases = (
            ("bool", _canonical_bytes(bool_document), None),
            ("float", _canonical_bytes(float_document), None),
            ("unknown", _canonical_bytes(unknown_document), None),
            ("reordered", reordered, None),
            ("count", _canonical_bytes(count_document), None),
            ("duplicate-key", duplicate, None),
            ("oversize", oversized, None),
            ("hash", canonical, "0" * 64),
        )
        for name, payload, forced_hash in cases:
            with self.subTest(name=name), self.assertRaises(ValueError):
                decode_p1_decision_request(
                    payload,
                    sha256(payload).hexdigest()
                    if forced_hash is None
                    else forced_hash,
                )
        self.assertEqual(
            decode_p1_decision_request(canonical, canonical_sha256).signal,
            self.u64.signal,
        )


class P1DecisionChildPublicSurfaceTests(unittest.TestCase):
    def test_public_signatures_and_source_keep_the_frozen_boundary(self) -> None:
        encode_signature = inspect.signature(encode_p1_decision_request)
        self.assertEqual(
            tuple(encode_signature.parameters),
            (
                "signal",
                "candidate_snapshots",
                "pit_inputs",
                "lc0_binding",
                "fees",
                "research_contract_sha256",
                "p1_contract_sha256",
                "rule_package_version",
                "quantity",
            ),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in encode_signature.parameters.values()
            )
        )
        self.assertEqual(
            tuple(inspect.signature(decode_p1_decision_request).parameters),
            ("request_bytes", "request_sha256"),
        )
        factory_signature = inspect.signature(create_p1_decision_child_runner)
        self.assertEqual(
            tuple(factory_signature.parameters),
            (
                "native_manifest_path",
                "native_build_backend_evidence_sha256",
            ),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in factory_signature.parameters.values()
            )
        )
        call_signature = inspect.signature(
            child_module._BoundP1DecisionChildRunnerV1.__call__
        )
        self.assertEqual(
            tuple(call_signature.parameters),
            ("self", "request_bytes", "request_sha256"),
        )
        typed_signature = inspect.signature(
            child_module._BoundP1DecisionChildRunnerV1.run_typed
        )
        self.assertEqual(
            tuple(typed_signature.parameters)[1:],
            tuple(encode_signature.parameters),
        )
        self.assertTrue(
            all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in tuple(typed_signature.parameters.values())[1:]
            )
        )
        self.assertFalse(
            any(
                token in name.lower()
                for name in tuple(call_signature.parameters)
                + tuple(typed_signature.parameters)
                for token in ("timeout", "deadline")
            )
        )
        self.assertEqual(
            tuple(inspect.signature(_production_decision_child).parameters),
            (
                "send_connection",
                "generation_token",
                "request_bytes",
                "request_sha256",
                "deadline_monotonic_ns",
            ),
        )
        self.assertEqual(_production_decision_child.__module__, child_module.__name__)
        self.assertNotIn("<locals>", _production_decision_child.__qualname__)

        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertFalse(
            any(module.startswith("benchmarks") for module in imported_modules)
        )
        self.assertFalse(any(module.startswith("tools") for module in imported_modules))
        self.assertNotIn("build_native_kernel_v1", source)
        banned_public_tokens = (
            "provider",
            "submit",
            "place_order",
            "broker_client",
            "fallback",
            "timeout",
        )
        self.assertFalse(
            any(
                token in name.lower()
                for name in child_module.__all__
                for token in banned_public_tokens
            )
        )

    def test_public_factory_rejects_replaced_transitive_helpers(self) -> None:
        missing_manifest = ROOT / ".build" / "does-not-exist" / "manifest.json"
        replacements = (
            "_semantic_and_artifact_from_supervisor",
            "_semantic_artifact_lineage_valid",
            "_canonical_json_bytes",
        )
        for helper_name in replacements:
            with self.subTest(helper=helper_name), patch.object(
                child_module,
                helper_name,
                lambda *args, **kwargs: None,
            ), self.assertRaises(child_module.P1DecisionChildError) as raised:
                create_p1_decision_child_runner(
                    native_manifest_path=missing_manifest,
                    native_build_backend_evidence_sha256="0" * 64,
                )
            self.assertEqual(
                raised.exception.reason_code,
                child_module.P1_DECISION_CHILD_CONFIG_INVALID,
            )


class P1DecisionChildRealIntegrationTests(unittest.TestCase):
    _u64_cached: (
        tuple[object, object, bytes, str, P1DecisionRequestV1] | None
    ) = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_path = build_native_kernel_v1(repo_root=ROOT)
        cls.manifest_document = json.loads(cls.manifest_path.read_bytes())
        cls.backend_evidence_sha256 = cls.manifest_document[
            "backend_evidence_sha256"
        ]
        cls.native_manifest_sha256 = sha256(
            cls.manifest_path.read_bytes()
        ).hexdigest()
        cls.pinned_config = child_module._pin_native_config(
            cls.manifest_path,
            cls.backend_evidence_sha256,
        )
        cls.u64 = load_corpus("U64")
        cls.w64 = load_corpus("W64")

    @classmethod
    def _runner(cls) -> object:
        return create_p1_decision_child_runner(
            native_manifest_path=cls.manifest_path,
            native_build_backend_evidence_sha256=cls.backend_evidence_sha256,
        )

    @staticmethod
    def _typed_kwargs(corpus: object) -> dict[str, object]:
        return {
            "signal": corpus.signal,
            "candidate_snapshots": (corpus.snapshot,),
            "pit_inputs": corpus.pit_inputs,
            "lc0_binding": corpus.lc0_binding,
            "fees": corpus.fees,
            "research_contract_sha256": RESEARCH_CONTRACT_SHA256,
            "p1_contract_sha256": P1_CONTRACT_SHA256,
            "rule_package_version": corpus.signal.rule_version,
            "quantity": 1,
        }

    @staticmethod
    def _clocked_workflow_factory(
        clock: object,
        before_finalize: object = None,
    ) -> object:
        def factory(child_target: object) -> tuple[object, object, object, object]:
            original_clock = supervisor_module.time.monotonic_ns
            supervisor_module.time.monotonic_ns = clock
            try:
                begin, run_issued, consume, finalize = (
                    child_module._make_p1_workflow_process_supervisor(
                        child_target
                    )
                )
            finally:
                supervisor_module.time.monotonic_ns = original_clock

            def wrapped_finalize(
                ticket: object,
                expected_sha256: str,
            ) -> object:
                if before_finalize is not None:
                    before_finalize()
                return finalize(ticket, expected_sha256)

            return begin, run_issued, consume, wrapped_finalize

        return factory

    @classmethod
    def _u64_success(
        cls,
    ) -> tuple[object, object, bytes, str, P1DecisionRequestV1]:
        if cls._u64_cached is None:
            request_bytes, request_sha256 = _encode_corpus(cls.u64)
            decoded = decode_p1_decision_request(request_bytes, request_sha256)
            runner = cls._runner()
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PYTHONPATH", None)
                result = runner(request_bytes, request_sha256)
            cls._u64_cached = (
                runner,
                result,
                request_bytes,
                request_sha256,
                decoded,
            )
        return cls._u64_cached

    @classmethod
    def _alternate_runner(
        cls,
        child_target: object,
        supervisor_factory: object = None,
        encode_request: object = None,
        accept_supervisor_result: object = None,
        identity_snapshot: object = None,
    ) -> object:
        factory = child_module._build_public_factory(
            child_module._make_p1_workflow_process_supervisor
            if supervisor_factory is None
            else supervisor_factory,
            child_target,
            child_module._pin_native_config,
            child_module.encode_p1_decision_request
            if encode_request is None
            else encode_request,
            child_module.decode_p1_decision_request,
            child_module._outer_envelope,
            child_module._accept_supervisor_result
            if accept_supervisor_result is None
            else accept_supervisor_result,
            child_module._failure_integration_result,
            child_module._workflow_timing_receipt_from_finalization,
            child_module._publish_finalized_candidate,
            child_module._make_bound_pipeline,
            child_module._integration_result_identity_snapshot
            if identity_snapshot is None
            else identity_snapshot,
        )
        return factory(
            native_manifest_path=cls.manifest_path,
            native_build_backend_evidence_sha256=(
                cls.backend_evidence_sha256
            ),
        )

    def assert_fail_closed(self, result: object) -> None:
        self.assertIs(type(result), P1DecisionChildIntegrationResultV1)
        self.assertEqual(result.status, "FAIL_CLOSED")
        self.assertIsNone(result.semantic_receipt)
        self.assertIsNone(result.artifact)
        self.assertIsNone(result.artifact_sha256)
        self.assertIs(
            type(result.workflow_timing),
            P1DecisionWorkflowTimingReceiptV1,
        )
        self.assertFalse(result.actionable)
        self.assertEqual(result.broker_order_count, 0)
        self.assertEqual(result.qualification_status, "NOT_CLAIMED")
        self.assertIn(
            result.request_binding_kind,
            {"TYPED_DECISION_REQUEST", "PARENT_REJECTED_INPUT"},
        )
        self.assertFalse(result.supervisor_receipt.partial_result_published)
        self.assertEqual(
            result.supervisor_generation_token,
            result.supervisor_receipt.generation_token,
        )
        self.assertEqual(
            result.supervisor_request_sha256,
            result.supervisor_receipt.request_sha256,
        )

    def validation_context(self, result: object) -> dict[str, object]:
        self.assertIs(type(result), P1DecisionChildIntegrationResultV1)
        self.assertIs(type(result.artifact), P1DecisionChildArtifactV1)
        return {
            "expected_config_sha256": result.artifact.config_sha256,
            "expected_manifest_sha256": result.artifact.native_manifest_sha256,
            "expected_supervisor_request_sha256": (
                result.supervisor_receipt.request_sha256
            ),
            "expected_pinned_config": self.pinned_config,
        }

    def typed_failure_validation_context(
        self,
        result: object,
    ) -> dict[str, object]:
        self.assertIs(type(result), P1DecisionChildIntegrationResultV1)
        return {
            "expected_supervisor_request_sha256": (
                result.supervisor_receipt.request_sha256
            ),
            "expected_pinned_config": self.pinned_config,
        }

    def assert_success_facts(
        self,
        result: object,
        *,
        fixture_id: str,
        request_bytes: bytes,
        request_sha256: str,
        expected_projection_sha256: str,
    ) -> None:
        golden = _golden_document(fixture_id)
        self.assertIs(type(result), P1DecisionChildIntegrationResultV1)
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.reason_code, "P1_DECISION_CHILD_SUCCESS")
        self.assertEqual(result.failure_origin, "NONE")
        self.assertIsNone(result.child_reason_code)
        self.assertFalse(result.actionable)
        self.assertEqual(result.broker_order_count, 0)
        self.assertEqual(result.qualification_status, "NOT_CLAIMED")
        self.assertEqual(
            result.request_binding_kind,
            "TYPED_DECISION_REQUEST",
        )
        self.assertIs(type(result.semantic_receipt), P1DecisionChildSemanticReceiptV1)
        self.assertIs(type(result.artifact), P1DecisionChildArtifactV1)

        semantic = result.semantic_receipt.as_dict()
        self.assertEqual(
            semantic["supervisor_generation_token"],
            result.supervisor_receipt.generation_token,
        )
        self.assertEqual(semantic["requested"], 64)
        self.assertEqual(semantic["bound"], 64)
        self.assertEqual(semantic["started"], 64)
        self.assertEqual(semantic["terminal"], 64)
        self.assertEqual(semantic["worker_count"], 8)
        self.assertEqual(semantic["kernel_threads"], 1)
        self.assertEqual(semantic["cache_hits"], 0)
        self.assertEqual(semantic["call_terminals"], golden["call_terminals"])
        self.assertEqual(
            semantic["call_semantic_output_sha256"],
            golden["call_semantic_output_sha256"],
        )
        self.assertEqual(semantic["decision_projection"], golden["decision_projection"])
        self.assertEqual(semantic["projection_sha256"], expected_projection_sha256)
        self.assertNotIn("decision_semantic_sha256", semantic)

        artifact = result.artifact.as_dict()
        required_artifact_keys = {
            "decision_request_sha256",
            "supervisor_request_sha256",
            "config_sha256",
            "native_manifest_sha256",
            "native_build_backend_evidence_sha256",
            "combined_backend_evidence_sha256",
            "semantic_receipt_sha256",
            "request_binding",
            "call_semantic_output_sha256",
            "decision_projection",
            "projection_sha256",
            "runtime_semantic_sha256",
            "backend_evidence_components",
            "decision_terminal",
            "execution_status",
            "p1_status",
            "actionable",
            "broker_order_count",
            "qualification_status",
        }
        self.assertTrue(required_artifact_keys.issubset(artifact))
        self.assertEqual(artifact["decision_request_sha256"], request_sha256)
        self.assertEqual(
            artifact["supervisor_generation_token"],
            result.supervisor_receipt.generation_token,
        )
        self.assertEqual(
            result.supervisor_generation_token,
            result.supervisor_receipt.generation_token,
        )
        self.assertEqual(
            result.supervisor_request_sha256,
            result.supervisor_receipt.request_sha256,
        )
        self.assertEqual(result.request_sha256, request_sha256)
        self.assertEqual(
            artifact["supervisor_request_sha256"],
            result.supervisor_receipt.request_sha256,
        )
        self.assertNotEqual(
            result.supervisor_receipt.request_sha256,
            request_sha256,
        )
        self.assertEqual(request_sha256, sha256(request_bytes).hexdigest())
        self.assertEqual(artifact["native_manifest_sha256"], self.native_manifest_sha256)
        self.assertEqual(
            artifact["native_build_backend_evidence_sha256"],
            self.backend_evidence_sha256,
        )
        self.assertEqual(
            artifact["combined_backend_evidence_sha256"],
            semantic["backend_evidence_components"]["backend_evidence_sha256"],
        )
        self.assertEqual(
            artifact["semantic_receipt_sha256"],
            sha256(_canonical_bytes(semantic)).hexdigest(),
        )
        self.assertEqual(artifact["request_binding"], semantic["request_binding"])
        self.assertEqual(
            artifact["call_semantic_output_sha256"],
            semantic["call_semantic_output_sha256"],
        )
        self.assertEqual(artifact["decision_projection"], semantic["decision_projection"])
        self.assertEqual(artifact["projection_sha256"], semantic["projection_sha256"])
        self.assertEqual(
            artifact["runtime_semantic_sha256"],
            semantic["runtime_semantic_sha256"],
        )
        self.assertEqual(
            artifact["backend_evidence_components"],
            semantic["backend_evidence_components"],
        )
        self.assertEqual(
            artifact["decision_terminal"],
            semantic["decision_projection"]["decision"],
        )
        self.assertEqual(artifact["execution_status"], semantic["execution_status"])
        self.assertEqual(artifact["p1_status"], semantic["p1_status"])
        self.assertFalse(artifact["actionable"])
        self.assertEqual(artifact["broker_order_count"], 0)
        self.assertEqual(artifact["qualification_status"], "NOT_CLAIMED")
        self.assertNotIn("decision_semantic_sha256", artifact)
        self.assertEqual(
            result.artifact_sha256,
            sha256(_canonical_bytes(artifact)).hexdigest(),
        )
        self.assertNotEqual(result.artifact_sha256, semantic["shadow_artifact_sha256"])

        components = semantic["backend_evidence_components"]
        envelope = validate_backend_evidence_envelope(
            {
                "artifact_sha256": components["artifact_sha256"],
                "backend_evidence_sha256": components[
                    "backend_evidence_sha256"
                ],
                "execution_sha256": components["execution_sha256"],
                "fixture_id": fixture_id,
                "input_sha256": components["input_sha256"],
                "schema_version": BACKEND_EVIDENCE_SCHEMA_VERSION,
                "semantic_sha256": golden["decision_semantic_sha256"],
            }
        )
        self.assertEqual(envelope.fixture_id, fixture_id)
        self.assertEqual(
            envelope.semantic_sha256,
            golden["decision_semantic_sha256"],
        )

        receipt = result.supervisor_receipt
        self.assertEqual(receipt.messages_observed, 1)
        self.assertEqual(receipt.messages_accepted, 1)
        self.assertEqual(receipt.child_exitcode, 0)
        self.assertTrue(receipt.child_reaped)
        self.assertTrue(receipt.reader_joined)
        self.assertTrue(receipt.pipe_closed)
        self.assertTrue(receipt.process_closed)
        self.assertTrue(receipt.process_group_verified)
        self.assertTrue(receipt.process_group_gone)
        self.assertFalse(receipt.descendant_leak_observed)
        self.assertEqual(receipt.qualification_status, "NOT_CLAIMED")
        self.assertFalse(receipt.partial_result_published)
        self.assertIsNotNone(receipt.child_pid)
        self.assertNotEqual(receipt.child_pid, os.getpid())
        timing = result.workflow_timing
        self.assertIs(type(timing), P1DecisionWorkflowTimingReceiptV1)
        self.assertTrue(timing.before_deadline)
        self.assertEqual(
            timing.workflow_started_monotonic_ns,
            receipt.supervisor_started_monotonic_ns,
        )
        self.assertEqual(
            timing.deadline_monotonic_ns,
            receipt.deadline_monotonic_ns,
        )
        self.assertGreaterEqual(
            timing.completed_monotonic_ns,
            receipt.completed_monotonic_ns,
        )
        self.assertEqual(
            timing.elapsed_ns,
            timing.completed_monotonic_ns
            - timing.workflow_started_monotonic_ns,
        )
        self.assertEqual(timing.decision_request_sha256, request_sha256)
        self.assertEqual(
            timing.supervisor_request_sha256,
            result.supervisor_request_sha256,
        )
        self.assertEqual(
            timing.supervisor_generation_token,
            result.supervisor_generation_token,
        )
        self.assertFalse(timing.actionable)
        self.assertEqual(timing.broker_order_count, 0)

    def test_factory_rejects_missing_tampered_or_mismatched_native_config(
        self,
    ) -> None:
        missing_path = ROOT / ".build" / "crr_native" / "missing" / "manifest.json"
        cases: list[tuple[str, Path, str]] = [
            ("missing", missing_path, self.backend_evidence_sha256),
            ("backend", self.manifest_path, "0" * 64),
        ]
        with tempfile.TemporaryDirectory() as directory:
            tampered_path = Path(directory) / "manifest.json"
            tampered = deepcopy(self.manifest_document)
            tampered["backend_evidence_sha256"] = "0" * 64
            tampered_path.write_bytes(_canonical_bytes(tampered) + b"\n")
            cases.append(("tampered", tampered_path, self.backend_evidence_sha256))
            for name, manifest_path, backend_sha256 in cases:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    create_p1_decision_child_runner(
                        native_manifest_path=manifest_path,
                        native_build_backend_evidence_sha256=backend_sha256,
                    )

    def test_bound_runner_rejects_later_parent_helper_replacement(self) -> None:
        request_bytes, request_sha256 = _encode_corpus(self.u64)
        runner = self._runner()
        with patch.object(
            child_module,
            "_semantic_and_artifact_from_supervisor",
            lambda *args, **kwargs: None,
        ), self.assertRaises(child_module.P1DecisionChildError) as raised:
            runner(request_bytes, request_sha256)
        self.assertEqual(
            raised.exception.reason_code,
            child_module.P1_DECISION_CHILD_CONFIG_INVALID,
        )

    def test_runner_callable_state_is_sealed_and_parent_errors_are_normalized(
        self,
    ) -> None:
        request_bytes, request_sha256 = _encode_corpus(self.u64)
        runner = self._runner()
        self.assertFalse(hasattr(runner, "__code__"))
        changed_runner = deepcopy(runner)
        object.__setattr__(
            changed_runner,
            "run_with_ticket",
            lambda *args, **kwargs: None,
        )
        with self.assertRaises(child_module.P1DecisionChildError) as changed:
            changed_runner(request_bytes, request_sha256)
        self.assertEqual(
            changed.exception.reason_code,
            child_module.P1_DECISION_CHILD_CONFIG_INVALID,
        )

        def raising_supervisor_factory(
            _child_target: object,
        ) -> tuple[object, object, object, object]:
            def begin_workflow() -> object:
                return object()

            def raising_run_issued(
                _ticket: object,
                _payload: bytes,
                _payload_sha256: str,
            ) -> object:
                raise RuntimeError("SENSITIVE_INTERNAL_DETAIL")

            def unreachable_consume(
                _ticket: object,
                _issued: object,
                _expected_sha256: str,
            ) -> object:
                raise AssertionError("consume must not run")

            def unreachable_finalize(
                _ticket: object,
                _expected_sha256: str,
            ) -> object:
                raise AssertionError("finalize must not run")

            return (
                begin_workflow,
                raising_run_issued,
                unreachable_consume,
                unreachable_finalize,
            )

        raising_runner = self._alternate_runner(
            _target_output_missing,
            raising_supervisor_factory,
        )
        with self.assertRaises(child_module.P1DecisionChildError) as normalized:
            raising_runner(request_bytes, request_sha256)
        self.assertEqual(
            normalized.exception.reason_code,
            child_module.P1_DECISION_CHILD_EXECUTION_FAILED,
        )
        self.assertNotIn("SENSITIVE_INTERNAL_DETAIL", str(normalized.exception))

    def test_capability_is_consumed_before_any_result_field_is_read(self) -> None:
        request_bytes, request_sha256 = _encode_corpus(self.u64)
        attribute_reads: list[str] = []
        consume_calls: list[str] = []

        class UnreadableIssued:
            def __getattribute__(self, name: str) -> object:
                attribute_reads.append(name)
                raise AssertionError("issued capability field was read")

        issued = UnreadableIssued()

        ticket = object()

        def fake_supervisor_factory(
            _child_target: object,
        ) -> tuple[object, object, object, object]:
            def begin_workflow() -> object:
                return ticket

            def run_issued(
                candidate_ticket: object,
                _payload: bytes,
                _payload_sha256: str,
            ) -> object:
                self.assertIs(candidate_ticket, ticket)
                return issued

            def consume(
                candidate_ticket: object,
                candidate: object,
                expected_sha256: str,
            ) -> None:
                self.assertIs(candidate_ticket, ticket)
                self.assertIs(candidate, issued)
                consume_calls.append(expected_sha256)
                return None

            def finalize(
                _candidate_ticket: object,
                _expected_sha256: str,
            ) -> object:
                raise AssertionError("finalize must not run")

            return begin_workflow, run_issued, consume, finalize

        factory = child_module._build_public_factory(
            fake_supervisor_factory,
            _target_output_missing,
            child_module._pin_native_config,
            child_module.encode_p1_decision_request,
            child_module.decode_p1_decision_request,
            child_module._outer_envelope,
            child_module._accept_supervisor_result,
            child_module._failure_integration_result,
            child_module._workflow_timing_receipt_from_finalization,
            child_module._publish_finalized_candidate,
            child_module._make_bound_pipeline,
            child_module._integration_result_identity_snapshot,
        )
        runner = factory(
            native_manifest_path=self.manifest_path,
            native_build_backend_evidence_sha256=self.backend_evidence_sha256,
        )

        with self.assertRaises(child_module.P1DecisionChildError) as raised:
            runner(request_bytes, request_sha256)

        self.assertEqual(
            raised.exception.reason_code,
            child_module.P1_DECISION_PARENT_VALIDATION_FAILED,
        )
        self.assertEqual(len(consume_calls), 1)
        self.assertEqual(attribute_reads, [])

    def test_runner_identity_registry_rejects_clone_cross_product(self) -> None:
        success_runner, success, success_bytes, success_sha256, _ = (
            self._u64_success()
        )
        fail_signal = replace(self.u64.signal, signal_state="FAIL")
        failure_bytes, failure_sha256 = _encode_corpus(
            self.u64,
            signal=fail_signal,
        )
        failure_runner = self._runner()
        failure = failure_runner(failure_bytes, failure_sha256)
        self.assertTrue(
            success_runner.is_verified_result(
                success,
                success_bytes,
                success_sha256,
            )
        )
        self.assertTrue(
            failure_runner.is_verified_result(
                failure,
                failure_bytes,
                failure_sha256,
            )
        )

        clone_factories = (
            ("copy", copy),
            ("deepcopy", deepcopy),
            ("replace", replace),
            ("pickle", lambda value: pickle.loads(pickle.dumps(value))),
            ("unsafe-clone", _unsafe_clone),
        )
        authentic = (
            (
                "success",
                success_runner,
                success,
                success_bytes,
                success_sha256,
            ),
            (
                "child-declared",
                failure_runner,
                failure,
                failure_bytes,
                failure_sha256,
            ),
        )
        for outcome, runner, result, request_bytes, request_sha256 in authentic:
            for operation, clone_factory in clone_factories:
                with self.subTest(outcome=outcome, operation=operation):
                    clone = clone_factory(result)
                    self.assertIsNot(clone, result)
                    self.assertFalse(
                        runner.is_verified_result(
                            clone,
                            request_bytes,
                            request_sha256,
                        )
                    )

    def test_registry_rejects_cross_invocation_and_unsafe_mutation(self) -> None:
        runner = self._runner()
        first_bytes = _canonical_bytes({"schema_version": "INVALID_A"})
        second_bytes = _canonical_bytes({"schema_version": "INVALID_B"})
        first_sha256 = sha256(first_bytes).hexdigest()
        second_sha256 = sha256(second_bytes).hexdigest()
        first = runner(first_bytes, first_sha256)
        second = runner(second_bytes, second_sha256)

        self.assertTrue(
            runner.is_verified_result(first, first_bytes, first_sha256)
        )
        self.assertTrue(
            runner.is_verified_result(second, second_bytes, second_sha256)
        )
        self.assertFalse(
            runner.is_verified_result(first, second_bytes, second_sha256)
        )
        self.assertFalse(
            runner.is_verified_result(second, first_bytes, first_sha256)
        )
        object.__setattr__(first, "reason_code", P1_REASON_START_FAILED)
        self.assertFalse(
            runner.is_verified_result(first, first_bytes, first_sha256)
        )

    def test_registry_rejects_same_input_from_independent_factory(self) -> None:
        request_bytes = _canonical_bytes({"schema_version": "INVALID_FACTORY"})
        request_sha256 = sha256(request_bytes).hexdigest()
        first_runner = self._runner()
        second_runner = self._alternate_runner(
            child_module._production_decision_child
        )
        first = first_runner(request_bytes, request_sha256)
        second = second_runner(request_bytes, request_sha256)

        self.assertTrue(
            first_runner.is_verified_result(
                first,
                request_bytes,
                request_sha256,
            )
        )
        self.assertTrue(
            second_runner.is_verified_result(
                second,
                request_bytes,
                request_sha256,
            )
        )
        self.assertFalse(
            first_runner.is_verified_result(
                second,
                request_bytes,
                request_sha256,
            )
        )
        self.assertFalse(
            second_runner.is_verified_result(
                first,
                request_bytes,
                request_sha256,
            )
        )

    def test_runner_rejects_wrong_transport_hash_before_starting_child(self) -> None:
        request_bytes, _ = _encode_corpus(self.u64)

        result = self._runner()(request_bytes, "0" * 64)

        self.assert_fail_closed(result)
        self.assertEqual(result.reason_code, P1_REASON_INPUT_INVALID)
        self.assertEqual(result.failure_origin, "INPUT")
        self.assertFalse(result.supervisor_receipt.child_started)

    def test_parent_codec_rejects_canonical_wrong_schema_before_child_start(
        self,
    ) -> None:
        request_bytes = _canonical_bytes(
            {"schema_version": "NOT_A_P1_DECISION_REQUEST"}
        )
        request_sha256 = sha256(request_bytes).hexdigest()

        result = self._runner()(request_bytes, request_sha256)

        self.assert_fail_closed(result)
        self.assertEqual(result.status, "FAIL_CLOSED")
        self.assertEqual(result.reason_code, P1_REASON_INPUT_INVALID)
        self.assertEqual(result.failure_origin, "INPUT")
        self.assertIsNone(result.child_reason_code)
        self.assertEqual(result.request_sha256, request_sha256)
        self.assertEqual(
            result.request_binding_kind,
            "PARENT_REJECTED_INPUT",
        )
        self.assertFalse(result.supervisor_receipt.child_started)
        self.assertEqual(result.supervisor_receipt.messages_observed, 0)
        self.assertEqual(result.supervisor_receipt.messages_accepted, 0)

    def test_input_rejection_binding_cannot_attach_to_unrelated_typed_request(
        self,
    ) -> None:
        malformed_bytes = _canonical_bytes(
            {"schema_version": "NOT_A_P1_DECISION_REQUEST"}
        )
        malformed_sha256 = sha256(malformed_bytes).hexdigest()
        rejected = self._runner()(malformed_bytes, malformed_sha256)
        u64_bytes, u64_sha256 = _encode_corpus(self.u64)
        u64_request = decode_p1_decision_request(u64_bytes, u64_sha256)
        zero_receipt = _unsafe_clone(
            rejected.supervisor_receipt,
            request_sha256="0" * 64,
        )
        unrelated = _unsafe_clone(
            rejected,
            request_sha256=u64_sha256,
            supervisor_request_sha256="0" * 64,
            supervisor_receipt=zero_receipt,
        )
        self.assertFalse(
            _validate_p1_decision_child_result(
                unrelated,
                expected_request=u64_request,
                expected_backend_sha256=self.backend_evidence_sha256,
                expected_supervisor_request_sha256="0" * 64,
                expected_pinned_config=self.pinned_config,
            )
        )
        with self.assertRaises(child_module.P1DecisionChildError):
            replace(
                rejected,
                request_sha256=u64_sha256,
                supervisor_request_sha256="0" * 64,
                supervisor_receipt=zero_receipt,
            )

    def test_structural_validator_is_not_failure_origin_provenance(self) -> None:
        malformed_bytes = _canonical_bytes(
            {"schema_version": "NOT_A_P1_DECISION_REQUEST"}
        )
        runner = self._runner()
        rejected = runner(
            malformed_bytes,
            sha256(malformed_bytes).hexdigest(),
        )
        u64_bytes, u64_sha256 = _encode_corpus(self.u64)
        u64_request = decode_p1_decision_request(u64_bytes, u64_sha256)
        actual_outer_sha256 = child_module._outer_envelope(
            u64_request,
            self.pinned_config,
        )[1]
        start_shaped_receipt = _unsafe_clone(
            rejected.supervisor_receipt,
            request_sha256=actual_outer_sha256,
        )
        start_shaped_timing = _unsafe_clone(
            rejected.workflow_timing,
            decision_request_sha256=u64_sha256,
            supervisor_request_sha256=actual_outer_sha256,
        )
        relabelled = _unsafe_clone(
            rejected,
            reason_code=P1_REASON_START_FAILED,
            failure_origin="START",
            request_sha256=u64_sha256,
            request_binding_kind="TYPED_DECISION_REQUEST",
            supervisor_request_sha256=actual_outer_sha256,
            supervisor_receipt=start_shaped_receipt,
            workflow_timing=start_shaped_timing,
        )
        self.assertTrue(
            _validate_p1_decision_child_result(
                relabelled,
                expected_request=u64_request,
                expected_backend_sha256=self.backend_evidence_sha256,
                expected_supervisor_request_sha256=actual_outer_sha256,
                expected_pinned_config=self.pinned_config,
            )
        )
        self.assertFalse(
            runner.is_verified_result(
                relabelled,
                u64_bytes,
                u64_sha256,
            )
        )

    def test_real_u64_fresh_child_matches_frozen_semantics_without_pythonpath(
        self,
    ) -> None:
        runner, result, request_bytes, request_sha256, request = (
            self._u64_success()
        )

        self.assert_success_facts(
            result,
            fixture_id="U64",
            request_bytes=request_bytes,
            request_sha256=request_sha256,
            expected_projection_sha256=U64_PROJECTION_SHA256,
        )
        self.assertTrue(
            runner.is_verified_result(
                result,
                request_bytes,
                request_sha256,
            )
        )
        self.assertTrue(
            _validate_p1_decision_child_result(
                result,
                expected_request=request,
                expected_backend_sha256=self.backend_evidence_sha256,
                **self.validation_context(result),
            )
        )
        self.assertEqual(
            load_decision_semantic_golden("U64").decision_semantic_sha256,
            U64_SEMANTIC_SHA256,
        )

    def test_real_u64_typed_entry_matches_bytes_semantics(self) -> None:
        request_bytes, request_sha256 = _encode_corpus(self.u64)
        runner = self._runner()

        result = runner.run_typed(**self._typed_kwargs(self.u64))

        self.assert_success_facts(
            result,
            fixture_id="U64",
            request_bytes=request_bytes,
            request_sha256=request_sha256,
            expected_projection_sha256=U64_PROJECTION_SHA256,
        )
        self.assertTrue(
            runner.is_verified_result(
                result,
                request_bytes,
                request_sha256,
            )
        )

    def test_typed_entry_begins_once_before_encoding(self) -> None:
        events: list[str] = []
        malformed = _canonical_bytes({"schema_version": "INVALID_TYPED"})
        malformed_sha256 = sha256(malformed).hexdigest()

        def ordered_supervisor_factory(
            child_target: object,
        ) -> tuple[object, object, object, object]:
            begin, run_issued, consume, finalize = (
                child_module._make_p1_workflow_process_supervisor(
                    child_target
                )
            )

            def ordered_begin() -> object:
                events.append("begin")
                return begin()

            def ordered_run(
                ticket: object,
                payload: bytes,
                payload_sha256: str,
            ) -> object:
                events.append("run")
                return run_issued(ticket, payload, payload_sha256)

            def ordered_consume(
                ticket: object,
                issued: object,
                expected_sha256: str,
            ) -> object:
                events.append("consume")
                return consume(ticket, issued, expected_sha256)

            def ordered_finalize(
                ticket: object,
                expected_sha256: str,
            ) -> object:
                events.append("finalize")
                return finalize(ticket, expected_sha256)

            return (
                ordered_begin,
                ordered_run,
                ordered_consume,
                ordered_finalize,
            )

        def ordered_encoder(**_values: object) -> tuple[bytes, str]:
            events.append("encode")
            return malformed, malformed_sha256

        runner = self._alternate_runner(
            _target_output_missing,
            ordered_supervisor_factory,
            ordered_encoder,
        )

        result = runner.run_typed(**self._typed_kwargs(self.u64))

        self.assert_fail_closed(result)
        self.assertEqual(result.failure_origin, "INPUT")
        self.assertEqual(
            events,
            ["begin", "encode", "run", "consume", "finalize"],
        )

    def test_clean_isolated_interpreter_imports_package_and_runs_child(self) -> None:
        request_bytes, request_sha256 = _encode_corpus(self.u64)
        script = r'''
import json
from pathlib import Path
import sys

repo_root = Path(sys.argv[1]).resolve()
manifest_path = Path(sys.argv[2]).resolve()
backend_sha256 = sys.argv[3]
request_path = Path(sys.argv[4]).resolve()
request_sha256 = sys.argv[5]
for entry in tuple(sys.path):
    if entry and Path(entry).resolve() == repo_root:
        raise SystemExit("repository root leaked into isolated sys.path")
sys.path.insert(0, str(repo_root / "src"))
import gld_research_core.p1_decision_child as decision_child

module_path = Path(decision_child.__file__).resolve()
if repo_root / "src" not in module_path.parents:
    raise SystemExit("package was not imported from the explicit src root")
runner = decision_child.create_p1_decision_child_runner(
    native_manifest_path=manifest_path,
    native_build_backend_evidence_sha256=backend_sha256,
)
result = runner(request_path.read_bytes(), request_sha256)
print(json.dumps({
    "child_reaped": result.supervisor_receipt.child_reaped,
    "projection_sha256": result.artifact.projection_sha256,
    "status": result.status,
}))
'''
        with tempfile.TemporaryDirectory() as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_bytes(request_bytes)
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"PYTHONHOME", "PYTHONPATH"}
            }
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    script,
                    str(ROOT),
                    str(self.manifest_path),
                    self.backend_evidence_sha256,
                    str(request_path),
                    request_sha256,
                ],
                cwd=directory,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        isolated = json.loads(completed.stdout)
        self.assertEqual(isolated["status"], "SUCCESS")
        self.assertTrue(isolated["child_reaped"])
        self.assertEqual(
            isolated["projection_sha256"],
            U64_PROJECTION_SHA256,
        )

    def test_real_w64_runs_exactly_three_complete_fresh_child_warmups(
        self,
    ) -> None:
        request_bytes, request_sha256 = _encode_corpus(self.w64)
        runner = self._runner()
        results = tuple(runner(request_bytes, request_sha256) for _ in range(3))

        self.assertEqual(len(results), 3)
        child_pids: list[int] = []
        for result in results:
            self.assert_success_facts(
                result,
                fixture_id="W64",
                request_bytes=request_bytes,
                request_sha256=request_sha256,
                expected_projection_sha256=W64_PROJECTION_SHA256,
            )
            child_pids.append(result.supervisor_receipt.child_pid)
        self.assertEqual(len(set(child_pids)), 3)
        self.assertEqual(
            load_decision_semantic_golden("W64").decision_semantic_sha256,
            W64_SEMANTIC_SHA256,
        )

    def test_signal_fail_is_child_declared_with_no_semantic_or_artifact(
        self,
    ) -> None:
        fail_signal = replace(self.u64.signal, signal_state="FAIL")
        request_bytes, request_sha256 = _encode_corpus(self.u64, signal=fail_signal)

        runner = self._runner()
        result = runner(request_bytes, request_sha256)

        self.assert_fail_closed(result)
        self.assertEqual(result.reason_code, P1_REASON_EXECUTION_FAILED)
        self.assertEqual(result.failure_origin, "CHILD_DECLARED")
        self.assertEqual(result.child_reason_code, "SIGNAL_FAIL")
        self.assertEqual(result.supervisor_receipt.messages_observed, 1)
        self.assertEqual(result.supervisor_receipt.messages_accepted, 0)
        self.assertEqual(result.supervisor_receipt.child_exitcode, 0)
        self.assertTrue(result.supervisor_receipt.child_reaped)
        self.assertTrue(result.supervisor_receipt.process_group_gone)
        decoded = decode_p1_decision_request(request_bytes, request_sha256)
        self.assertTrue(
            runner.is_verified_result(
                result,
                request_bytes,
                request_sha256,
            )
        )
        self.assertTrue(
            _validate_p1_decision_child_result(
                result,
                expected_request=decoded,
                expected_backend_sha256=self.backend_evidence_sha256,
                **self.typed_failure_validation_context(result),
            )
        )
        for name, changed_result in (
            (
                "origin",
                _unsafe_clone(result, failure_origin="INPUT"),
            ),
            (
                "child-reason",
                _unsafe_clone(result, child_reason_code=None),
            ),
            (
                "outer-request",
                _unsafe_clone(result, supervisor_request_sha256="0" * 64),
            ),
            (
                "reason-origin-pair",
                _unsafe_clone(
                    result,
                    reason_code=P1_REASON_CHILD_CRASHED,
                    failure_origin="INPUT",
                    child_reason_code=None,
                ),
            ),
            (
                "nested-receipt",
                _unsafe_clone(
                    result,
                    supervisor_receipt=_unsafe_clone(
                        result.supervisor_receipt,
                        request_sha256="0" * 64,
                    ),
                ),
            ),
        ):
            with self.subTest(name=name):
                self.assertFalse(
                    runner.is_verified_result(
                        changed_result,
                        request_bytes,
                        request_sha256,
                    )
                )

    def test_real_output_missing_is_preserved_only_by_issuing_runner(self) -> None:
        request_bytes, request_sha256 = _encode_corpus(self.u64)
        consume_calls: list[str] = []

        finalize_calls: list[str] = []

        def counting_supervisor_factory(
            child_target: object,
        ) -> tuple[object, object, object, object]:
            begin, run_issued, consume, finalize = (
                child_module._make_p1_workflow_process_supervisor(
                    child_target
                )
            )

            def consume_once(
                ticket: object,
                issued: object,
                expected_sha256: str,
            ) -> object:
                consume_calls.append(expected_sha256)
                return consume(ticket, issued, expected_sha256)

            def finalize_once(ticket: object, expected_sha256: str) -> object:
                finalize_calls.append(expected_sha256)
                return finalize(ticket, expected_sha256)

            return begin, run_issued, consume_once, finalize_once

        runner = self._alternate_runner(
            _target_output_missing,
            counting_supervisor_factory,
        )

        result = runner(request_bytes, request_sha256)

        self.assert_fail_closed(result)
        self.assertEqual(result.reason_code, P1_REASON_OUTPUT_MISSING)
        self.assertEqual(result.failure_origin, "CHILD_PROTOCOL")
        self.assertIsNone(result.child_reason_code)
        self.assertEqual(result.supervisor_receipt.messages_observed, 0)
        self.assertEqual(result.supervisor_receipt.messages_accepted, 0)
        self.assertEqual(consume_calls, [result.supervisor_request_sha256])
        self.assertEqual(finalize_calls, [result.supervisor_request_sha256])
        self.assertTrue(
            runner.is_verified_result(result, request_bytes, request_sha256)
        )
        relabelled = replace(
            result,
            reason_code=P1_REASON_EXECUTION_FAILED,
            failure_origin="SUPERVISOR",
        )
        self.assertFalse(
            runner.is_verified_result(
                relabelled,
                request_bytes,
                request_sha256,
            )
        )

    def test_finalize_none_is_stable_parent_validation_failure(self) -> None:
        request_bytes, request_sha256 = _encode_corpus(self.u64)

        def missing_finalize_factory(
            child_target: object,
        ) -> tuple[object, object, object, object]:
            begin, run_issued, consume, _finalize = (
                child_module._make_p1_workflow_process_supervisor(
                    child_target
                )
            )

            def missing_finalize(
                _ticket: object,
                _expected_sha256: str,
            ) -> None:
                return None

            return begin, run_issued, consume, missing_finalize

        runner = self._alternate_runner(
            _target_output_missing,
            missing_finalize_factory,
        )

        with self.assertRaises(child_module.P1DecisionChildError) as raised:
            runner(request_bytes, request_sha256)

        self.assertEqual(
            raised.exception.reason_code,
            child_module.P1_DECISION_PARENT_VALIDATION_FAILED,
        )

    def test_candidate_identity_is_registered_before_finalization(self) -> None:
        request_bytes = _canonical_bytes({"schema_version": "INVALID_ORDER"})
        request_sha256 = sha256(request_bytes).hexdigest()
        events: list[str] = []

        def ordered_factory(
            child_target: object,
        ) -> tuple[object, object, object, object]:
            begin, run_issued, consume, finalize = (
                child_module._make_p1_workflow_process_supervisor(
                    child_target
                )
            )

            def observed_finalize(
                ticket: object,
                expected_sha256: str,
            ) -> object:
                events.append("finalize")
                return finalize(ticket, expected_sha256)

            return begin, run_issued, consume, observed_finalize

        def observed_snapshot(
            value: object,
            **kwargs: object,
        ) -> tuple[object, ...]:
            if type(value) is P1DecisionChildIntegrationResultV1:
                events.append(
                    "candidate-identity"
                    if value.workflow_timing is None
                    else "published-identity"
                )
            return child_module._integration_result_identity_snapshot(
                value,
                **kwargs,
            )

        runner = self._alternate_runner(
            _target_output_missing,
            ordered_factory,
            identity_snapshot=observed_snapshot,
        )

        result = runner(request_bytes, request_sha256)

        self.assert_fail_closed(result)
        self.assertLess(events.index("candidate-identity"), events.index("finalize"))
        self.assertLess(events.index("finalize"), events.index("published-identity"))

    def test_workflow_five_second_gate_never_publishes_partial_success(self) -> None:
        request_bytes, request_sha256 = _encode_corpus(self.u64)
        runner = self._alternate_runner(_target_forever)
        started = time.monotonic()

        result = runner(request_bytes, request_sha256)
        elapsed = time.monotonic() - started

        self.assert_fail_closed(result)
        self.assertEqual(result.reason_code, P1_REASON_HARD_TIMEOUT)
        self.assertEqual(result.failure_origin, "WORKFLOW_DEADLINE")
        self.assertFalse(result.workflow_timing.before_deadline)
        self.assertGreaterEqual(
            result.supervisor_receipt.elapsed_ns,
            5_000_000_000,
        )
        self.assertLessEqual(
            result.supervisor_receipt.elapsed_ns,
            5_250_000_000,
        )
        self.assertGreaterEqual(elapsed, 5.0)
        self.assertLess(elapsed, 5.5)
        self.assertTrue(
            runner.is_verified_result(result, request_bytes, request_sha256)
        )

    def test_typed_encoding_consumes_ticket_budget_before_child_start(self) -> None:
        clock = _FakeMonotonicClock(10_000)

        def deadline_crossing_encoder(**values: object) -> tuple[bytes, str]:
            clock.advance(5_000_000_000)
            return child_module.encode_p1_decision_request(**values)

        runner = self._alternate_runner(
            child_module._production_decision_child,
            self._clocked_workflow_factory(clock),
            deadline_crossing_encoder,
        )
        request_bytes, request_sha256 = _encode_corpus(self.u64)

        result = runner.run_typed(**self._typed_kwargs(self.u64))

        self.assert_fail_closed(result)
        self.assertEqual(result.reason_code, P1_REASON_HARD_TIMEOUT)
        self.assertEqual(result.failure_origin, "WORKFLOW_DEADLINE")
        self.assertFalse(result.supervisor_receipt.child_started)
        self.assertFalse(result.workflow_timing.before_deadline)
        self.assertEqual(
            result.workflow_timing.workflow_started_monotonic_ns,
            10_000,
        )
        self.assertTrue(
            runner.is_verified_result(result, request_bytes, request_sha256)
        )

    def test_workflow_half_open_completion_boundary_is_exact(self) -> None:
        request_bytes = _canonical_bytes({"schema_version": "INVALID_BOUNDARY"})
        request_sha256 = sha256(request_bytes).hexdigest()

        before_clock = _FakeMonotonicClock(20_000)

        def finish_before() -> None:
            before_clock.now_ns = 20_000 + 5_000_000_000 - 1

        before_runner = self._alternate_runner(
            _target_output_missing,
            self._clocked_workflow_factory(before_clock, finish_before),
        )
        before = before_runner(request_bytes, request_sha256)
        self.assert_fail_closed(before)
        self.assertEqual(before.failure_origin, "INPUT")
        self.assertTrue(before.workflow_timing.before_deadline)

        boundary_clock = _FakeMonotonicClock(30_000)

        def finish_at_boundary() -> None:
            boundary_clock.now_ns = 30_000 + 5_000_000_000

        boundary_runner = self._alternate_runner(
            _target_output_missing,
            self._clocked_workflow_factory(
                boundary_clock,
                finish_at_boundary,
            ),
        )
        boundary = boundary_runner(request_bytes, request_sha256)
        self.assert_fail_closed(boundary)
        self.assertEqual(boundary.reason_code, P1_REASON_HARD_TIMEOUT)
        self.assertEqual(boundary.failure_origin, "WORKFLOW_DEADLINE")
        self.assertFalse(boundary.workflow_timing.before_deadline)
        self.assertEqual(
            boundary.workflow_timing.completed_monotonic_ns,
            boundary.workflow_timing.deadline_monotonic_ns,
        )

    def test_parent_validation_crossing_deadline_discards_success_payload(
        self,
    ) -> None:
        real_monotonic_ns = time.monotonic_ns
        clock = _OffsetMonotonicClock(real_monotonic_ns)

        def deadline_crossing_accept(*args: object, **kwargs: object) -> object:
            candidate = child_module._accept_supervisor_result(
                *args,
                **kwargs,
            )
            clock.offset_ns = 5_000_000_000
            return candidate

        runner = self._alternate_runner(
            child_module._production_decision_child,
            self._clocked_workflow_factory(clock),
            accept_supervisor_result=deadline_crossing_accept,
        )
        request_bytes, request_sha256 = _encode_corpus(self.u64)

        result = runner(request_bytes, request_sha256)

        self.assert_fail_closed(result)
        self.assertEqual(result.reason_code, P1_REASON_HARD_TIMEOUT)
        self.assertEqual(result.failure_origin, "WORKFLOW_DEADLINE")
        self.assertFalse(result.workflow_timing.before_deadline)
        self.assertEqual(result.supervisor_receipt.messages_accepted, 1)
        self.assertFalse(result.supervisor_receipt.partial_result_published)
        self.assertIsNone(result.semantic_receipt)
        self.assertIsNone(result.artifact)
        self.assertIsNone(result.artifact_sha256)
        self.assertTrue(
            runner.is_verified_result(result, request_bytes, request_sha256)
        )

    def test_success_generation_is_bound_to_one_supervisor_invocation(self) -> None:
        first_runner, first, request_bytes, request_sha256, request = (
            self._u64_success()
        )
        second_runner = self._runner()
        second = second_runner(request_bytes, request_sha256)
        self.assertNotEqual(
            first.supervisor_receipt.generation_token,
            second.supervisor_receipt.generation_token,
        )
        self.assertNotEqual(first.artifact_sha256, second.artifact_sha256)
        self.assertTrue(
            first_runner.is_verified_result(
                first,
                request_bytes,
                request_sha256,
            )
        )
        self.assertFalse(
            first_runner.is_verified_result(
                second,
                request_bytes,
                request_sha256,
            )
        )
        self.assertFalse(
            second_runner.is_verified_result(
                first,
                request_bytes,
                request_sha256,
            )
        )
        mixed = _unsafe_clone(
            first,
            supervisor_receipt=second.supervisor_receipt,
        )
        self.assertFalse(
            first_runner.is_verified_result(
                mixed,
                request_bytes,
                request_sha256,
            )
        )
        with self.assertRaises(child_module.P1DecisionChildError):
            replace(first, supervisor_receipt=second.supervisor_receipt)
        payload_mixed = _unsafe_clone(
            second,
            semantic_receipt=first.semantic_receipt,
            artifact=first.artifact,
            artifact_sha256=first.artifact_sha256,
        )
        self.assertFalse(
            second_runner.is_verified_result(
                payload_mixed,
                request_bytes,
                request_sha256,
            )
        )

    def test_parent_validator_rejects_artifact_backend_and_projection_tampering(
        self,
    ) -> None:
        _, result, _, _, request = self._u64_success()
        self.assertTrue(
            _validate_p1_decision_child_result(
                result,
                expected_request=request,
                expected_backend_sha256=self.backend_evidence_sha256,
                **self.validation_context(result),
            )
        )
        artifact_hash_tamper = _unsafe_clone(result, artifact_sha256="0" * 64)
        backend_artifact = _unsafe_clone(
            result.artifact,
            native_build_backend_evidence_sha256="0" * 64,
        )
        backend_tamper = _unsafe_clone(result, artifact=backend_artifact)
        projection_artifact = _unsafe_clone(
            result.artifact,
            projection_sha256="0" * 64,
        )
        projection_tamper = _unsafe_clone(result, artifact=projection_artifact)
        components = _unsafe_clone(
            result.semantic_receipt.backend_evidence_components,
            backend_evidence_sha256="0" * 64,
        )
        semantic = _unsafe_clone(
            result.semantic_receipt,
            backend_evidence_components=components,
        )
        semantic_backend_tamper = _unsafe_clone(result, semantic_receipt=semantic)
        for name, tampered in (
            ("artifact", artifact_hash_tamper),
            ("backend-artifact", backend_tamper),
            ("projection", projection_tamper),
            ("backend-semantic", semantic_backend_tamper),
        ):
            with self.subTest(name=name):
                self.assertFalse(
                    _validate_p1_decision_child_result(
                        tampered,
                        expected_request=request,
                        expected_backend_sha256=self.backend_evidence_sha256,
                        **self.validation_context(result),
                    )
                )

    def test_parent_validator_binds_semantics_to_the_exact_request(self) -> None:
        _, result, _, _, _ = self._u64_success()
        w64_bytes, w64_sha256 = _encode_corpus(self.w64)
        w64_request = decode_p1_decision_request(w64_bytes, w64_sha256)
        changed_artifact = replace(
            result.artifact,
            decision_request_sha256=w64_sha256,
        )
        changed_result = _unsafe_clone(
            result,
            request_sha256=w64_sha256,
            artifact=changed_artifact,
            artifact_sha256=changed_artifact.artifact_sha256,
        )
        self.assertFalse(
            _validate_p1_decision_child_result(
                changed_result,
                expected_request=w64_request,
                expected_backend_sha256=self.backend_evidence_sha256,
                **self.validation_context(result),
            )
        )

    def test_parent_validator_requires_exact_pinned_and_supervisor_lineage(
        self,
    ) -> None:
        _, result, _, _, request = self._u64_success()
        context = self.validation_context(result)
        changed_artifact = replace(
            result.artifact,
            config_sha256="0" * 64,
            native_manifest_sha256="1" * 64,
        )
        changed_result = replace(
            result,
            artifact=changed_artifact,
            artifact_sha256=changed_artifact.artifact_sha256,
        )
        self.assertFalse(
            _validate_p1_decision_child_result(
                changed_result,
                expected_request=request,
                expected_backend_sha256=self.backend_evidence_sha256,
                **context,
            )
        )
        supervisor_changed_artifact = replace(
            result.artifact,
            supervisor_request_sha256="2" * 64,
        )
        with self.assertRaises(child_module.P1DecisionChildError):
            replace(
                result,
                artifact=supervisor_changed_artifact,
                artifact_sha256=supervisor_changed_artifact.artifact_sha256,
            )

    def test_parent_validation_shape_still_requires_runner_identity(self) -> None:
        malformed_bytes = _canonical_bytes(
            {"schema_version": "NOT_A_P1_DECISION_REQUEST"}
        )
        runner = self._runner()
        rejected = runner(
            malformed_bytes,
            sha256(malformed_bytes).hexdigest(),
        )
        u64_bytes, u64_sha256 = _encode_corpus(self.u64)
        u64_request = decode_p1_decision_request(u64_bytes, u64_sha256)
        actual_outer_sha256 = child_module._outer_envelope(
            u64_request,
            self.pinned_config,
        )[1]
        no_child_accepted_receipt = replace(
            rejected.supervisor_receipt,
            request_sha256=actual_outer_sha256,
            messages_observed=1,
            messages_accepted=1,
        )
        accepted_shape_timing = _unsafe_clone(
            rejected.workflow_timing,
            decision_request_sha256=u64_sha256,
            supervisor_request_sha256=actual_outer_sha256,
        )
        invalid_parent_failure = _unsafe_clone(
            rejected,
            reason_code=child_module.P1_DECISION_PARENT_VALIDATION_FAILED,
            failure_origin="PARENT_VALIDATION",
            child_reason_code=None,
            request_sha256=u64_sha256,
            request_binding_kind="TYPED_DECISION_REQUEST",
            supervisor_request_sha256=actual_outer_sha256,
            supervisor_receipt=no_child_accepted_receipt,
            workflow_timing=accepted_shape_timing,
        )
        self.assertTrue(
            _validate_p1_decision_child_result(
                invalid_parent_failure,
                expected_request=u64_request,
                expected_backend_sha256=self.backend_evidence_sha256,
                expected_supervisor_request_sha256=actual_outer_sha256,
                expected_pinned_config=self.pinned_config,
            )
        )
        self.assertFalse(
            runner.is_verified_result(
                invalid_parent_failure,
                u64_bytes,
                u64_sha256,
            )
        )

    def test_parent_validation_accepts_real_success_receipt_shape(self) -> None:
        runner, success, request_bytes, request_sha256, request = (
            self._u64_success()
        )
        parent_failure = replace(
            success,
            status="FAIL_CLOSED",
            reason_code=child_module.P1_DECISION_PARENT_VALIDATION_FAILED,
            failure_origin="PARENT_VALIDATION",
            child_reason_code=None,
            semantic_receipt=None,
            artifact=None,
            artifact_sha256=None,
        )
        self.assert_fail_closed(parent_failure)
        self.assertTrue(
            _validate_p1_decision_child_result(
                parent_failure,
                expected_request=request,
                expected_backend_sha256=self.backend_evidence_sha256,
                **self.validation_context(success),
            )
        )
        self.assertFalse(
            runner.is_verified_result(
                parent_failure,
                request_bytes,
                request_sha256,
            )
        )

    def test_zz_equal_valued_nested_replacement_invalidates_identity(self) -> None:
        success_runner, success, success_bytes, success_sha256, _ = (
            self._u64_success()
        )
        original_semantic = success.semantic_receipt
        equal_semantic = deepcopy(original_semantic)
        self.assertEqual(equal_semantic, original_semantic)
        self.assertIsNot(equal_semantic, original_semantic)
        object.__setattr__(success, "semantic_receipt", equal_semantic)
        self.assertFalse(
            success_runner.is_verified_result(
                success,
                success_bytes,
                success_sha256,
            )
        )

        timing_bytes = _canonical_bytes({"schema_version": "INVALID_TIMING"})
        timing_sha256 = sha256(timing_bytes).hexdigest()
        timing_runner = self._runner()
        timing_result = timing_runner(timing_bytes, timing_sha256)
        original_timing = timing_result.workflow_timing
        equal_timing = deepcopy(original_timing)
        self.assertEqual(equal_timing, original_timing)
        self.assertIsNot(equal_timing, original_timing)
        object.__setattr__(timing_result, "workflow_timing", equal_timing)
        self.assertFalse(
            timing_runner.is_verified_result(
                timing_result,
                timing_bytes,
                timing_sha256,
            )
        )

        failure_bytes = _canonical_bytes({"schema_version": "INVALID_NESTED"})
        failure_sha256 = sha256(failure_bytes).hexdigest()
        failure_runner = self._runner()
        failure = failure_runner(failure_bytes, failure_sha256)
        original_supervisor_receipt = failure.supervisor_receipt
        equal_supervisor_receipt = deepcopy(original_supervisor_receipt)
        self.assertEqual(equal_supervisor_receipt, original_supervisor_receipt)
        self.assertIsNot(
            equal_supervisor_receipt,
            original_supervisor_receipt,
        )
        object.__setattr__(
            failure,
            "supervisor_receipt",
            equal_supervisor_receipt,
        )
        self.assertFalse(
            failure_runner.is_verified_result(
                failure,
                failure_bytes,
                failure_sha256,
            )
        )


if __name__ == "__main__":
    unittest.main()
