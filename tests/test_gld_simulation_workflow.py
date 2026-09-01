from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from gld_research_core.p1_process_supervisor import (
    P1_PROCESS_TIMEOUT_NS,
    P1_SUPERVISOR_RECEIPT_SCHEMA,
    P1ProcessSupervisorReceiptV1,
    P1ProcessSupervisorResultV1,
)
from gld_research_core.crr_batch import (
    CrrSemanticTerminalV1,
    execution_provenance_sha256,
    semantic_output_sha256,
)
from gld_simulation.canonical import canonical_json_bytes, canonical_json_sha256
from gld_simulation.bundle import load_raw_bundle
from gld_simulation.pipeline import (
    PIPELINE_RECEIPT_SCHEMA_VERSION,
    SIMULATION_CLASSIFICATION,
    SimulationPipelineError,
    minimal_no_decision_document,
)
from gld_simulation.render_html import render_decision_card
import gld_simulation.workflow as workflow_module
from tools.build_crr_native import build_native_kernel_v1


ROOT = Path(__file__).resolve().parents[1]
PASS_BUNDLE = ROOT / "fixtures" / "gld_simulation" / "v1" / "pass"


def _receipt(
    request_sha256: str,
    *,
    success: bool,
) -> P1ProcessSupervisorReceiptV1:
    started = 1_000
    deadline = started + P1_PROCESS_TIMEOUT_NS
    return P1ProcessSupervisorReceiptV1(
        schema_version=P1_SUPERVISOR_RECEIPT_SCHEMA,
        generation_token="a" * 64,
        request_sha256=request_sha256,
        supervisor_started_monotonic_ns=started,
        deadline_monotonic_ns=deadline,
        generation_closed_monotonic_ns=4_000,
        child_start_monotonic_ns=2_000,
        child_exit_monotonic_ns=3_000,
        completed_monotonic_ns=4_000,
        elapsed_ns=3_000,
        child_started=True,
        child_pid=12_345,
        child_exitcode=0 if success else 1,
        child_process_group_id=12_345,
        child_session_id=12_345,
        parent_process_group_id=2,
        parent_session_id=1,
        process_group_verified=True,
        isolation_barrier_released=True,
        process_group_gone=True,
        descendant_leak_observed=False,
        deadline_reached=False,
        messages_observed=1 if success else 0,
        messages_accepted=1 if success else 0,
        late_messages_suppressed=0,
        terminate_sent=False,
        kill_sent=False,
        child_reaped=True,
        reader_joined=True,
        pipe_closed=True,
        process_closed=True,
        partial_result_published=False,
        qualification_status="NOT_CLAIMED",
    )


def _success_result(
    request_sha256: str,
    decision: dict[str, object],
    semantic_receipt: dict[str, object],
) -> P1ProcessSupervisorResultV1:
    artifact_bytes = canonical_json_bytes(decision)
    return P1ProcessSupervisorResultV1(
        status="SUCCESS",
        reason_code="P1_SUCCESS",
        failure_origin="NONE",
        child_reason_code=None,
        semantic_receipt_bytes=canonical_json_bytes(semantic_receipt),
        artifact_bytes=artifact_bytes,
        artifact_sha256=canonical_json_sha256(decision),
        actionable=False,
        broker_order_count=0,
        supervisor_receipt=_receipt(request_sha256, success=True),
    )


def _fail_closed_result(request_sha256: str) -> P1ProcessSupervisorResultV1:
    return P1ProcessSupervisorResultV1(
        status="FAIL_CLOSED",
        reason_code="P1_CHILD_CRASHED",
        failure_origin="CHILD_EXIT",
        child_reason_code=None,
        semantic_receipt_bytes=None,
        artifact_bytes=None,
        artifact_sha256=None,
        actionable=False,
        broker_order_count=0,
        supervisor_receipt=_receipt(request_sha256, success=False),
    )


class GldSimulationWorkflowIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        raw_documents = {
            "market_status": {
                "trading_date": "2026-08-28",
                "as_of_utc_ns": 1_000,
            },
            "rule_package": {
                "rule_package_id": "SIM_GLD_PIPELINE_V1",
            },
        }
        self.bundle = SimpleNamespace(
            bundle_id="SIM-INTEGRITY-TEST",
            manifest_sha256="c" * 64,
            raw_content_sha256="d" * 64,
            as_document=lambda: raw_documents,
            read_json=lambda logical_key: raw_documents[logical_key],
        )
        self.decision = minimal_no_decision_document(
            raw_documents=self.bundle.as_document(),
            bundle_id=self.bundle.bundle_id,
            manifest_sha256=self.bundle.manifest_sha256,
            raw_content_sha256=self.bundle.raw_content_sha256,
            reason_code="SIM_TEST_FAIL_CLOSED",
        )
        self.semantic_receipt = {
            "schema_version": PIPELINE_RECEIPT_SCHEMA_VERSION,
            "classification": SIMULATION_CLASSIFICATION,
            "decision_semantic_sha256": canonical_json_sha256(self.decision),
            "raw_content_sha256": self.decision["raw_content_sha256"],
            "manifest_sha256": self.decision["manifest_sha256"],
            "rule_sha256": self.decision["rule_sha256"],
            "gate_input_fact_sha256": None,
            "gate_state": "NOT_EVALUABLE",
            "crr_semantic_output_sha256": None,
            "status": self.decision["status"],
            "actionable": False,
            "broker_order_count": 0,
        }

    def test_fail_closed_shape_is_valid_with_no_decision_manual_control(self) -> None:
        self.assertEqual(
            self.decision["manual_control"],
            "NO_DECISION_NO_ORDER_AUTHORITY",
        )
        self.assertEqual(
            self.decision["decision_scope"],
            "ENTRY_SIMULATION_ONLY",
        )
        validated = workflow_module._validate_decision_document(
            self.decision,
            bundle=self.bundle,
            expected_backend_evidence_sha256="b" * 64,
        )
        self.assertIs(validated, self.decision)

    def test_validated_fail_closed_extreme_timestamp_still_renders_card(self) -> None:
        raw_documents = self.bundle.as_document()
        raw_documents["market_status"]["as_of_utc_ns"] = 10**100
        decision = minimal_no_decision_document(
            raw_documents=raw_documents,
            bundle_id=self.bundle.bundle_id,
            manifest_sha256=self.bundle.manifest_sha256,
            raw_content_sha256=self.bundle.raw_content_sha256,
            reason_code="SIM_TEST_FAIL_CLOSED",
        )

        validated = workflow_module._validate_decision_document(
            decision,
            bundle=self.bundle,
            expected_backend_evidence_sha256="b" * 64,
        )
        html = render_decision_card(
            validated,
            decision_result_sha256=canonical_json_sha256(validated),
        ).decode("utf-8")

        self.assertIn("禁止建仓", html)
        self.assertIn("数据时点（纽约）：未提供", html)

    def test_manual_control_must_match_terminal_status(self) -> None:
        invalid = json.loads(canonical_json_bytes(self.decision))
        invalid["manual_control"] = "NO_ACTION_NO_ORDER_AUTHORITY"
        with self.assertRaisesRegex(
            SimulationPipelineError,
            "SIM_DECISION_ARTIFACT_INVALID",
        ):
            workflow_module._validate_decision_document(
                invalid,
                bundle=self.bundle,
                expected_backend_evidence_sha256="b" * 64,
            )

    def test_decision_validator_rejects_unknown_or_authority_fields(self) -> None:
        unexpected = dict(self.decision)
        unexpected["display_hint"] = "untrusted"
        with self.assertRaisesRegex(
            SimulationPipelineError,
            "SIM_DECISION_ARTIFACT_INVALID",
        ):
            workflow_module._validate_decision_document(
                unexpected,
                bundle=self.bundle,
                expected_backend_evidence_sha256="b" * 64,
            )

        nested_authority = json.loads(canonical_json_bytes(self.decision))
        nested_authority["risk_context"]["broker_order"] = {
            "submit": True,
        }
        with self.assertRaisesRegex(
            SimulationPipelineError,
            "SIM_DECISION_ARTIFACT_INVALID",
        ):
            workflow_module._validate_decision_document(
                nested_authority,
                bundle=self.bundle,
                expected_backend_evidence_sha256="b" * 64,
            )

    def test_no_action_or_no_decision_cannot_carry_candidates(self) -> None:
        invalid = json.loads(canonical_json_bytes(self.decision))
        invalid["candidates"] = [{"carrier_id": "LC0"}]
        with self.assertRaisesRegex(
            SimulationPipelineError,
            "SIM_DECISION_ARTIFACT_INVALID",
        ):
            workflow_module._validate_decision_document(
                invalid,
                bundle=self.bundle,
                expected_backend_evidence_sha256="b" * 64,
            )

    def test_all_terminal_nested_documents_reject_unknown_keys(self) -> None:
        mutations = {
            "gate_a": lambda value: value["gate_a"].update(
                {"recommendation": "BUY"}
            ),
            "crr": lambda value: value["crr"].update(
                {"trade_signal": "ENTER"}
            ),
            "risk_context": lambda value: value["risk_context"].update(
                {"buy_now": True}
            ),
            "exit_plan": lambda value: value["exit_plan"].update(
                {"display_hint": "unknown"}
            ),
            "simulation_limitations": lambda value: value[
                "simulation_limitations"
            ].append("UNKNOWN_LIMITATION"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                invalid = json.loads(canonical_json_bytes(self.decision))
                mutate(invalid)
                with self.assertRaisesRegex(
                    SimulationPipelineError,
                    "SIM_DECISION_ARTIFACT_INVALID",
                ):
                    workflow_module._validate_decision_document(
                        invalid,
                        bundle=self.bundle,
                        expected_backend_evidence_sha256="b" * 64,
                    )

    def test_selector_and_capacity_schemas_are_exact(self) -> None:
        bundle = load_raw_bundle(PASS_BUNDLE)
        rule = bundle.read_json("rule_package")
        self.assertIsInstance(rule, dict)
        selector = {
            "candidate_terminal_policy": rule["risk"][
                "candidate_terminal_policy"
            ],
            "lc0": rule["lc0"],
            "bcs": rule["bcs"],
            "coarse_fine_agreement_required": True,
            "second_best_fallback_allowed": False,
        }
        workflow_module._validate_selector_policy(
            selector,
            rule_package=rule,
        )
        invalid_selector = json.loads(canonical_json_bytes(selector))
        invalid_selector["lc0"]["recommendation"] = "BUY"
        with self.assertRaisesRegex(
            SimulationPipelineError,
            "SIM_DECISION_ARTIFACT_INVALID",
        ):
            workflow_module._validate_selector_policy(
                invalid_selector,
                rule_package=rule,
            )

        capacities = {
            "cash": 1,
            "delta_notional": 1,
            "kelly_drawdown": 1,
            "liquidity": 1,
            "max_loss": 1,
            "planned_loss": 1,
        }
        workflow_module._validate_capacities(capacities, allow_none=False)
        capacities["buy_now"] = 1
        with self.assertRaisesRegex(
            SimulationPipelineError,
            "SIM_DECISION_ARTIFACT_INVALID",
        ):
            workflow_module._validate_capacities(
                capacities,
                allow_none=False,
            )

    def test_authority_defense_covers_trading_style_field_names(self) -> None:
        for key in ("trade_signal", "recommendation", "buy_now"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(
                    SimulationPipelineError,
                    "SIM_DECISION_AUTHORITY_FIELD_FORBIDDEN",
                ):
                    workflow_module._reject_authority_fields(
                        {"nested": {key: True}}
                    )

    def test_dual_tamper_cannot_replace_bundle_rule_identity(self) -> None:
        tampered_decision = json.loads(canonical_json_bytes(self.decision))
        tampered_decision["rule_sha256"] = "0" * 64
        tampered_receipt = dict(self.semantic_receipt)
        tampered_receipt["rule_sha256"] = "0" * 64
        tampered_receipt["decision_semantic_sha256"] = canonical_json_sha256(
            tampered_decision
        )

        def consume(_ticket: object, _issued: object, request_sha: str) -> object:
            return _success_result(
                request_sha,
                tampered_decision,
                tampered_receipt,
            )

        finalization = SimpleNamespace(
            workflow_started_monotonic_ns=1_000,
            deadline_monotonic_ns=1_000 + P1_PROCESS_TIMEOUT_NS,
            completed_monotonic_ns=4_500,
            before_deadline=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            with self.assertRaisesRegex(
                SimulationPipelineError,
                "SIM_DECISION_SOURCE_BINDING_INVALID",
            ):
                self._run_with_patched_supervisor(
                    consume_side_effect=consume,
                    finalization=finalization,
                    output=output,
                )
            self.assertFalse(output.exists())

    def test_semantic_receipt_must_match_every_artifact_lineage_field(self) -> None:
        workflow_module._validate_child_semantic_receipt(
            self.semantic_receipt,
            decision=self.decision,
            bundle=self.bundle,
            child_artifact_sha256=canonical_json_sha256(self.decision),
        )

        mutations: dict[str, object] = {
            "schema_version": "WRONG",
            "classification": "WRONG",
            "decision_semantic_sha256": "0" * 64,
            "raw_content_sha256": "0" * 64,
            "manifest_sha256": "0" * 64,
            "rule_sha256": "0" * 64,
            "gate_input_fact_sha256": "0" * 64,
            "gate_state": "PASS",
            "crr_semantic_output_sha256": "0" * 64,
            "status": "SIMULATED_NO_ACTION",
            "actionable": True,
            "broker_order_count": 1,
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                tampered = dict(self.semantic_receipt)
                tampered[key] = value
                with self.assertRaisesRegex(
                    SimulationPipelineError,
                    "SIM_CHILD_SEMANTIC_RECEIPT_INVALID",
                ):
                    workflow_module._validate_child_semantic_receipt(
                        tampered,
                        decision=self.decision,
                        bundle=self.bundle,
                        child_artifact_sha256=canonical_json_sha256(
                            self.decision
                        ),
                    )

        with self.assertRaisesRegex(
            SimulationPipelineError,
            "SIM_CHILD_SEMANTIC_RECEIPT_INVALID",
        ):
            workflow_module._validate_child_semantic_receipt(
                self.semantic_receipt,
                decision=self.decision,
                bundle=self.bundle,
                child_artifact_sha256="0" * 64,
            )

    def _run_with_patched_supervisor(
        self,
        *,
        consume_side_effect: object,
        finalization: object,
        output: Path,
    ) -> object:
        request_document = {
            "schema_version": workflow_module.SUPERVISOR_REQUEST_SCHEMA_VERSION,
            "bundle_id": self.bundle.bundle_id,
            "manifest_sha256": self.bundle.manifest_sha256,
            "raw_content_sha256": self.bundle.raw_content_sha256,
            "raw_documents": self.bundle.as_document(),
            "native_manifest_path": "/trusted/test/manifest_v1.json",
            "expected_backend_evidence_sha256": "b" * 64,
        }
        with (
            patch.object(
                workflow_module,
                "load_raw_bundle",
                return_value=self.bundle,
            ),
            patch.object(
                workflow_module,
                "_request_document",
                return_value=request_document,
            ),
            patch.object(
                workflow_module,
                "_trusted_native_manifest_path",
                return_value=Path("/trusted/test/manifest_v1.json"),
            ),
            patch.object(
                workflow_module,
                "derive_native_crr_combined_backend_evidence_sha256",
                return_value="e" * 64,
            ),
            patch.object(workflow_module, "_BEGIN_WORKFLOW", return_value=object()),
            patch.object(workflow_module, "_RUN_ISSUED", return_value=object()),
            patch.object(
                workflow_module,
                "_CONSUME_ISSUED",
                side_effect=consume_side_effect,
            ),
            patch.object(
                workflow_module,
                "_FINALIZE_WORKFLOW",
                return_value=finalization,
            ),
        ):
            return workflow_module.run_simulation_workflow(
                bundle_root=PASS_BUNDLE,
                native_manifest_path=Path("unused-by-patched-request"),
                expected_backend_evidence_sha256="b" * 64,
                output_dir=output,
            )

    def test_tampered_child_receipt_is_not_published(self) -> None:
        tampered = dict(self.semantic_receipt)
        tampered["status"] = "SIMULATED_NO_ACTION"

        def consume(_ticket: object, _issued: object, request_sha: str) -> object:
            return _success_result(request_sha, self.decision, tampered)

        finalization = SimpleNamespace(
            workflow_started_monotonic_ns=1_000,
            deadline_monotonic_ns=1_000 + P1_PROCESS_TIMEOUT_NS,
            completed_monotonic_ns=4_500,
            before_deadline=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            with self.assertRaisesRegex(
                SimulationPipelineError,
                "SIM_CHILD_SEMANTIC_RECEIPT_INVALID",
            ):
                self._run_with_patched_supervisor(
                    consume_side_effect=consume,
                    finalization=finalization,
                    output=output,
                )
            self.assertFalse(output.exists())

    def test_late_finalization_replaces_child_success_with_fail_closed(self) -> None:
        def consume(_ticket: object, _issued: object, request_sha: str) -> object:
            return _success_result(
                request_sha,
                self.decision,
                self.semantic_receipt,
            )

        deadline = 1_000 + P1_PROCESS_TIMEOUT_NS
        finalization = SimpleNamespace(
            workflow_started_monotonic_ns=1_000,
            deadline_monotonic_ns=deadline,
            completed_monotonic_ns=deadline,
            before_deadline=False,
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = self._run_with_patched_supervisor(
                consume_side_effect=consume,
                finalization=finalization,
                output=Path(temporary) / "out",
            )
            self.assertEqual(result.status, "FAIL_CLOSED")
            self.assertEqual(
                result.decision_document["reason_code"],
                "SIM_SUPERVISOR_FINALIZATION_DEADLINE_EXCEEDED",
            )
            receipt = json.loads(result.decision_receipt_path.read_bytes())
            self.assertEqual(receipt["workflow_status"], "FAIL_CLOSED")
            self.assertFalse(
                receipt["workflow_finalization"]["before_deadline"]
            )
            self.assertIsNone(receipt["child_semantic_receipt"])
            self.assertIsNone(receipt["child_artifact_sha256"])

    def test_supervisor_crash_is_returned_as_fail_closed(self) -> None:
        def consume(_ticket: object, _issued: object, request_sha: str) -> object:
            return _fail_closed_result(request_sha)

        finalization = SimpleNamespace(
            workflow_started_monotonic_ns=1_000,
            deadline_monotonic_ns=1_000 + P1_PROCESS_TIMEOUT_NS,
            completed_monotonic_ns=4_500,
            before_deadline=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = self._run_with_patched_supervisor(
                consume_side_effect=consume,
                finalization=finalization,
                output=Path(temporary) / "out",
            )
            self.assertEqual(result.status, "FAIL_CLOSED")
            self.assertEqual(
                result.decision_document["reason_code"],
                "P1_CHILD_CRASHED",
            )
            receipt = json.loads(result.decision_receipt_path.read_bytes())
            self.assertEqual(receipt["workflow_status"], "FAIL_CLOSED")
            self.assertEqual(receipt["supervisor_status"], "FAIL_CLOSED")
            self.assertIsNone(receipt["child_semantic_receipt"])

    def test_untrusted_supervisor_reason_is_not_rendered_as_instruction(self) -> None:
        def consume(_ticket: object, _issued: object, request_sha: str) -> object:
            return replace(
                _fail_closed_result(request_sha),
                reason_code="SUBMIT_ORDER_NOW",
            )

        finalization = SimpleNamespace(
            workflow_started_monotonic_ns=1_000,
            deadline_monotonic_ns=1_000 + P1_PROCESS_TIMEOUT_NS,
            completed_monotonic_ns=4_500,
            before_deadline=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = self._run_with_patched_supervisor(
                consume_side_effect=consume,
                finalization=finalization,
                output=Path(temporary) / "out",
            )
            self.assertEqual(
                result.decision_document["reason_code"],
                "SIMULATION_PIPELINE_FAIL_CLOSED",
            )
            html = result.decision_card_path.read_text()
            self.assertNotIn("SUBMIT_ORDER_NOW", html)
            receipt = json.loads(result.decision_receipt_path.read_bytes())
            self.assertEqual(
                receipt["supervisor_reason_code"],
                "SUBMIT_ORDER_NOW",
            )


class GldSimulationDerivedBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_raw_bundle(PASS_BUNDLE)
        cls.native_manifest = build_native_kernel_v1(repo_root=ROOT)
        native = json.loads(cls.native_manifest.read_bytes())
        cls.backend_evidence_sha256 = native["backend_evidence_sha256"]
        cls.temporary = tempfile.TemporaryDirectory()
        result = workflow_module.run_simulation_workflow(
            bundle_root=PASS_BUNDLE,
            native_manifest_path=cls.native_manifest,
            expected_backend_evidence_sha256=cls.backend_evidence_sha256,
            output_dir=Path(cls.temporary.name) / "pass",
        )
        cls.decision = json.loads(
            result.decision_result_path.read_bytes()
        )
        cls.combined_backend_evidence_sha256 = cls.decision["crr"][
            "combined_backend_evidence_sha256"
        ]
        artifact_receipt = json.loads(
            result.decision_receipt_path.read_bytes()
        )
        cls.semantic_receipt = artifact_receipt["child_semantic_receipt"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def _cross_bind_tamper(
        self,
        decision: dict[str, object],
        receipt: dict[str, object],
    ) -> None:
        receipt["decision_semantic_sha256"] = canonical_json_sha256(decision)
        workflow_module._validate_child_semantic_receipt(
            receipt,
            decision=decision,
            bundle=self.bundle,
            child_artifact_sha256=canonical_json_sha256(decision),
        )

    def _assert_parent_rejects_source_tamper(
        self,
        decision: dict[str, object],
        receipt: dict[str, object],
        reason_code: str,
    ) -> None:
        self._cross_bind_tamper(decision, receipt)
        with self.assertRaisesRegex(
            SimulationPipelineError,
            reason_code,
        ):
            workflow_module._validate_decision_document(
                decision,
                bundle=self.bundle,
                expected_backend_evidence_sha256=(
                    self.backend_evidence_sha256
                ),
                expected_combined_backend_evidence_sha256=(
                    self.combined_backend_evidence_sha256
                ),
            )

    def test_dual_tampered_gate_hash_is_rejected_by_parent_recompute(self) -> None:
        decision = json.loads(canonical_json_bytes(self.decision))
        receipt = json.loads(canonical_json_bytes(self.semantic_receipt))
        decision["gate_a"]["input_fact_sha256"] = "0" * 64
        receipt["gate_input_fact_sha256"] = "0" * 64
        self._cross_bind_tamper(decision, receipt)

        with self.assertRaisesRegex(
            SimulationPipelineError,
            "SIM_DECISION_GATE_SOURCE_BINDING_INVALID",
        ):
            workflow_module._validate_decision_document(
                decision,
                bundle=self.bundle,
                expected_backend_evidence_sha256=(
                    self.backend_evidence_sha256
                ),
                expected_combined_backend_evidence_sha256=(
                    self.combined_backend_evidence_sha256
                ),
            )

    def test_dual_tampered_native_hash_is_rejected_by_request_pin(self) -> None:
        decision = json.loads(canonical_json_bytes(self.decision))
        receipt = json.loads(canonical_json_bytes(self.semantic_receipt))
        decision["crr"]["native_build_backend_evidence_sha256"] = "0" * 64
        self._cross_bind_tamper(decision, receipt)

        with self.assertRaisesRegex(
            SimulationPipelineError,
            "SIM_DECISION_ARTIFACT_INVALID",
        ):
            workflow_module._validate_decision_document(
                decision,
                bundle=self.bundle,
                expected_backend_evidence_sha256=(
                    self.backend_evidence_sha256
                ),
                expected_combined_backend_evidence_sha256=(
                    self.combined_backend_evidence_sha256
                ),
            )

    def test_dual_tampered_fake_contract_is_rejected_by_raw_binding(self) -> None:
        decision = json.loads(canonical_json_bytes(self.decision))
        receipt = json.loads(canonical_json_bytes(self.semantic_receipt))
        for candidate in decision["candidates"]:
            candidate["legs"][0]["contract_id"] = "GLD_FAKE_CONTRACT"
            candidate["selection_sha256"] = canonical_json_sha256(
                workflow_module._candidate_selection_preimage(candidate)
            )
        self._cross_bind_tamper(decision, receipt)

        with self.assertRaisesRegex(
            SimulationPipelineError,
            "SIM_DECISION_RAW_SELECTION_BINDING_INVALID",
        ):
            workflow_module._validate_decision_document(
                decision,
                bundle=self.bundle,
                expected_backend_evidence_sha256=(
                    self.backend_evidence_sha256
                ),
                expected_combined_backend_evidence_sha256=(
                    self.combined_backend_evidence_sha256
                ),
            )

    def test_terminal_vector_hashes_are_recomputed_and_bound_to_raw(self) -> None:
        decision = json.loads(canonical_json_bytes(self.decision))
        receipt = json.loads(canonical_json_bytes(self.semantic_receipt))
        terminals = decision["crr"]["semantic_terminals"]
        terminals[0]["input_sha256"] = "0" * 64
        typed = tuple(CrrSemanticTerminalV1(**item) for item in terminals)
        semantic_sha256 = semantic_output_sha256(typed)
        input_sha256 = sha256(
            canonical_json_bytes([item.input_sha256 for item in typed])
            + b"\n"
        ).hexdigest()
        decision["crr"]["semantic_output_sha256"] = semantic_sha256
        decision["crr"]["input_vector_sha256"] = input_sha256
        decision["crr"]["execution_provenance_sha256"] = (
            execution_provenance_sha256(
                backend_evidence_sha256=(
                    self.combined_backend_evidence_sha256
                ),
                input_vector_sha256=input_sha256,
                semantic_output_sha256_value=semantic_sha256,
            )
        )
        receipt["crr_semantic_output_sha256"] = semantic_sha256

        self._assert_parent_rejects_source_tamper(
            decision,
            receipt,
            "SIM_DECISION_CRR_RAW_BINDING_INVALID",
        )

    def test_candidate_delta_dual_tamper_is_rejected_by_terminal_replay(self) -> None:
        decision = json.loads(canonical_json_bytes(self.decision))
        receipt = json.loads(canonical_json_bytes(self.semantic_receipt))
        for candidate in decision["candidates"]:
            candidate["long_coarse_delta_ppm"] += 1
            candidate["long_fine_delta_ppm"] += 1
            candidate["legs"][0]["coarse_delta_ppm"] += 1
            candidate["legs"][0]["fine_delta_ppm"] += 1
            candidate["legs"][0]["delta_ppm"] += 1
            candidate["net_delta_ppm_per_strategy_unit"] += 1
            candidate["selection_sha256"] = canonical_json_sha256(
                workflow_module._candidate_selection_preimage(candidate)
            )

        self._assert_parent_rejects_source_tamper(
            decision,
            receipt,
            "SIM_DECISION_SOURCE_DERIVATION_INVALID",
        )

    def test_quantity_and_capacity_dual_tamper_is_rejected_by_raw_sizing(self) -> None:
        decision = json.loads(canonical_json_bytes(self.decision))
        receipt = json.loads(canonical_json_bytes(self.semantic_receipt))
        for candidate, sizing_key in zip(
            decision["candidates"],
            ("lc0_sizing", "bcs_sizing"),
            strict=True,
        ):
            candidate["quantity"] += 1_000
            sizing = decision["risk_context"][sizing_key]
            sizing["quantity"] = candidate["quantity"]
            candidate["capacities"]["liquidity"] += 1_000
            sizing["capacities"]["liquidity"] += 1_000
            candidate["max_loss_nano_usd"] = (
                candidate["economics"][
                    "sizing_max_loss_nano_usd_per_contract"
                ]
                * candidate["quantity"]
            )
            candidate["total_entry_cost_nano_usd"] = (
                candidate["economics"][
                    "base_entry_cost_nano_usd_per_contract"
                ]
                * candidate["quantity"]
            )

        self._assert_parent_rejects_source_tamper(
            decision,
            receipt,
            "SIM_DECISION_SOURCE_DERIVATION_INVALID",
        )

    def test_economics_dual_tamper_is_rejected_by_raw_repricing(self) -> None:
        decision = json.loads(canonical_json_bytes(self.decision))
        receipt = json.loads(canonical_json_bytes(self.semantic_receipt))
        candidate = decision["candidates"][0]
        candidate["base_entry_cost_nano_usd_per_contract"] = 1
        candidate["economics"][
            "base_entry_cost_nano_usd_per_contract"
        ] = 1
        candidate["total_entry_cost_nano_usd"] = candidate["quantity"]

        self._assert_parent_rejects_source_tamper(
            decision,
            receipt,
            "SIM_DECISION_SOURCE_DERIVATION_INVALID",
        )

    def test_calendar_and_exit_dual_tamper_is_rejected_by_market_replay(self) -> None:
        decision = json.loads(canonical_json_bytes(self.decision))
        receipt = json.loads(canonical_json_bytes(self.semantic_receipt))
        for candidate in decision["candidates"]:
            candidate["h20_date"] = "2020-01-01"
            candidate["minimum_expiry_date"] = "2020-02-01"
            candidate["selection_sha256"] = canonical_json_sha256(
                workflow_module._candidate_selection_preimage(candidate)
            )
        decision["exit_plan"]["latest_full_exit_date"] = "2099-12-31"

        self._assert_parent_rejects_source_tamper(
            decision,
            receipt,
            "SIM_DECISION_SOURCE_DERIVATION_INVALID",
        )

    def test_top_level_order_instruction_reason_is_rejected(self) -> None:
        decision = json.loads(canonical_json_bytes(self.decision))
        receipt = json.loads(canonical_json_bytes(self.semantic_receipt))
        decision["reason_code"] = "SUBMIT_ORDER_NOW"

        self._assert_parent_rejects_source_tamper(
            decision,
            receipt,
            "SIM_DECISION_ARTIFACT_INVALID",
        )

    def test_owner_selection_cannot_be_dual_tampered_into_risk_no_action(self) -> None:
        decision = json.loads(canonical_json_bytes(self.decision))
        receipt = json.loads(canonical_json_bytes(self.semantic_receipt))
        decision["status"] = "SIMULATED_NO_ACTION"
        decision["reason_code"] = "RISK_CAPACITY_BLOCKS_ENTRY"
        decision["manual_control"] = "NO_ACTION_NO_ORDER_AUTHORITY"
        decision["owner_selection_required"] = False
        decision["candidates"] = []
        receipt["status"] = "SIMULATED_NO_ACTION"
        risk = decision["risk_context"]
        risk["status"] = "BLOCKED"
        for sizing_key in ("lc0_sizing", "bcs_sizing"):
            sizing = risk[sizing_key]
            sizing["state"] = "NO_ACTION"
            sizing["reason_code"] = "SIZING_COMPLETE_ZERO_QUANTITY"
            sizing["quantity"] = 0
            sizing["capacities"]["liquidity"] = 0
            sizing["binding_constraints"] = ["LIQUIDITY"]

        self._assert_parent_rejects_source_tamper(
            decision,
            receipt,
            "SIM_DECISION_SOURCE_DERIVATION_INVALID",
        )


if __name__ == "__main__":
    unittest.main()
