"""Five-second supervised simulation workflow and atomic artifact publisher."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from hashlib import sha256
import json
from multiprocessing.connection import Connection
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Mapping
from zoneinfo import ZoneInfo

from gld_normalizer.errors import NormalizationError
from gld_research_core.crr_batch import (
    CrrBatchError,
    CrrSemanticTerminalV1,
    execution_provenance_sha256,
    semantic_output_sha256,
)
from gld_research_core.crr_delta import MODEL_ID, MODEL_SHA256
from gld_research_core.crr_input_binding import (
    bind_crr_call_inputs_bulk_from_snapshot,
)
from gld_research_core.native_crr_delta import (
    derive_native_crr_combined_backend_evidence_sha256,
)
from gld_research_core.p1_process_supervisor import (
    P1_CHILD_FRAME_SCHEMA,
    P1_PROCESS_TIMEOUT_NS,
    P1ProcessSupervisorReceiptV1,
    P1ProcessSupervisorResultV1,
    _make_p1_workflow_process_supervisor,
)

from .bundle import RawBundle, load_raw_bundle
from .canonical import canonical_json_bytes, canonical_json_sha256
from .carriers import (
    CarrierSelectionError,
    price_simulation_carriers,
    select_lc0_and_bcs,
)
from .errors import RawBundleError
from .pipeline import (
    DECISION_SCHEMA_VERSION,
    SAFE_FAIL_CLOSED_REASON_CODES,
    PIPELINE_RECEIPT_SCHEMA_VERSION,
    SIMULATION_CLASSIFICATION,
    SimulationPipelineError,
    _NormalizedMarketV1,
    _OptionFactsV1,
    _candidate,
    _exit_plan,
    _fallback_cutoff_utc_ns,
    _normalize_market,
    _option_facts,
    _preflight_risk_facts,
    _sizing,
    evaluate_simulation_request,
    minimal_no_decision_document,
)
from .render_html import render_decision_card


SUPERVISOR_REQUEST_SCHEMA_VERSION = "GLD_SIMULATION_SUPERVISOR_REQUEST_V1"
ARTIFACT_RECEIPT_SCHEMA_VERSION = "GLD_SIMULATION_ARTIFACT_RECEIPT_V1"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_TOKEN_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z", re.ASCII)
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", re.ASCII)
_NEW_YORK = ZoneInfo("America/New_York")
_REQUEST_KEYS = {
    "schema_version",
    "bundle_id",
    "manifest_sha256",
    "raw_content_sha256",
    "raw_documents",
    "native_manifest_path",
    "expected_backend_evidence_sha256",
}
_DECISION_KEYS = {
    "schema_version",
    "classification",
    "decision_scope",
    "status",
    "reason_code",
    "actionable",
    "broker_order_count",
    "owner_selection_required",
    "bundle_id",
    "manifest_sha256",
    "raw_content_sha256",
    "trading_date",
    "cutoff_utc_ns",
    "rule_package_version",
    "rule_sha256",
    "gate_a",
    "crr",
    "risk_context",
    "candidates",
    "exit_plan",
    "manual_control",
    "simulation_limitations",
}
_PIPELINE_RECEIPT_KEYS = {
    "schema_version",
    "classification",
    "decision_semantic_sha256",
    "raw_content_sha256",
    "manifest_sha256",
    "rule_sha256",
    "gate_input_fact_sha256",
    "gate_state",
    "crr_semantic_output_sha256",
    "status",
    "actionable",
    "broker_order_count",
}
_CANDIDATE_KEYS = {
    "carrier_id",
    "status",
    "reason_code",
    "quantity",
    "expiry_date",
    "h20_date",
    "minimum_expiry_date",
    "underlying_mid_nano_usd",
    "moneyness",
    "selector_policy",
    "long_coarse_delta_ppm",
    "long_fine_delta_ppm",
    "short_coarse_delta_ppm",
    "short_fine_delta_ppm",
    "net_delta_ppm_per_strategy_unit",
    "max_loss_nano_usd",
    "total_entry_cost_nano_usd",
    "base_entry_cost_nano_usd_per_contract",
    "stressed_entry_cost_nano_usd_per_contract",
    "sizing_loss_basis",
    "delta_notional_nano_usd_per_contract",
    "gross_expiry_width_nano_usd_per_contract",
    "modeled_expiry_max_profit_nano_usd_per_strategy_unit",
    "profit_cap_mode",
    "profit_model_scope",
    "economics",
    "capacities",
    "binding_constraints",
    "selection_sha256",
    "legs",
}
_LEG_KEYS = {
    "side",
    "ratio",
    "contract_id",
    "expiry_date",
    "expiry_utc_ns",
    "strike_nano_usd",
    "bid_nano_usd",
    "ask_nano_usd",
    "bid_size",
    "ask_size",
    "coarse_delta_ppm",
    "delta_ppm",
    "fine_delta_ppm",
    "moneyness",
}
_SELECTOR_POLICY_KEYS = {
    "candidate_terminal_policy",
    "lc0",
    "bcs",
    "coarse_fine_agreement_required",
    "second_best_fallback_allowed",
}
_ECONOMICS_KEYS = {
    "base_entry_cost_nano_usd_per_contract",
    "carrier_id",
    "delta_notional_nano_usd_per_contract",
    "execution_assumptions",
    "fee_components_nano_usd_per_contract",
    "gross_expiry_width_nano_usd_per_contract",
    "liquidity_capacity",
    "planned_exit_fees_nano_usd_per_contract",
    "sizing_max_loss_nano_usd_per_contract",
    "sizing_planned_loss_nano_usd_per_contract",
    "stressed_entry_cost_nano_usd_per_contract",
    "modeled_expiry_max_profit_nano_usd_per_strategy_unit",
}
_EXECUTION_ASSUMPTION_KEYS = {
    "include_exit_fees_in_max_loss",
    "long_entry_adverse_ticks",
    "short_entry_adverse_ticks",
}
_FEE_COMPONENT_KEYS = {
    "long_entry",
    "long_exit",
    "short_entry",
    "short_exit",
}
_GATE_FULL_KEYS = {
    "input_fact_sha256",
    "max_event_utc_ns",
    "max_receive_utc_ns",
    "metrics",
    "predicates",
    "reason_code",
    "rule_version",
    "state",
}
_GATE_FALLBACK_KEYS = {"state", "reason_code", "predicates", "metrics"}
_GATE_METRIC_KEYS = {
    "breakout_nano_usd",
    "current_sma200_nano_usd",
    "current_sma50_nano_usd",
    "minute_1044_close_nano_usd",
    "prior_close_nano_usd",
    "range_hold_above_count",
    "range_hold_required_count",
    "sma50_20_sessions_ago_nano_usd",
}
_GATE_PREDICATE_KEYS = {
    "minute_1044_close_gt_breakout",
    "prior_close_gt_sma50",
    "range_hold_count_met",
    "sma50_gt_sma200",
    "sma50_rising_20_sessions",
}
_GATE_REASONS_BY_STATE = {
    "PASS": {"GATE_A_PASS"},
    "FAIL": {
        "GATE_A_1044_CLOSE_NOT_ABOVE_BREAKOUT",
        "GATE_A_PRIOR_CLOSE_NOT_ABOVE_SMA50",
        "GATE_A_RANGE_HOLD_COUNT_BELOW_MINIMUM",
        "GATE_A_SMA50_NOT_ABOVE_SMA200",
        "GATE_A_SMA50_NOT_RISING_20_SESSIONS",
    },
    "NOT_EVALUABLE": {
        "GATE_A_CAUSALITY_VIOLATION",
        "GATE_A_DAILY_DATA_INCOMPLETE",
        "GATE_A_DAILY_FACT_MISSING",
        "GATE_A_DAILY_LOOKBACK_MISSING",
        "GATE_A_DAILY_ORDINAL_DUPLICATE",
        "GATE_A_MINUTE_DATA_INCOMPLETE",
        "GATE_A_MINUTE_FACT_MISSING",
        "GATE_A_MINUTE_LOOKBACK_MISSING",
        "GATE_A_MINUTE_ORDINAL_DUPLICATE",
    },
}
_CRR_NOT_RUN_KEYS = {
    "status",
    "requested",
    "terminal",
    "failure_count",
    "model_id",
    "model_sha256",
}
_CRR_NOT_RUN_STATUSES = {
    "NOT_RUN_GATE_FAILED",
    "NOT_RUN_GATE_NOT_EVALUABLE",
    "NOT_RUN_OPTION_DATA_INCOMPLETE",
    "NOT_RUN_PIPELINE_FAILED_CLOSED",
    "NOT_RUN_RISK_FACT_MISSING",
}
_CRR_EXACT64_KEYS = {
    "bound",
    "cache_hits",
    "combined_backend_evidence_sha256",
    "early_exercise_terminal_count",
    "execution_provenance_sha256",
    "failure_count",
    "first_failure_ordinal",
    "input_vector_sha256",
    "kernel_threads",
    "model_id",
    "model_sha256",
    "native_build_backend_evidence_sha256",
    "numerical_semantics_trust_boundary",
    "requested",
    "semantic_output_sha256",
    "semantic_terminals",
    "started",
    "status",
    "terminal",
    "worker_count",
}
_RISK_FULL_KEYS = {
    "status",
    "policy_id",
    "candidate_terminal_policy",
    "account_as_of_utc_ns",
    "sticky_drawdown_lock_active",
    "frozen_kelly_receipt_id",
    "frozen_kelly_strategy_rule_package_id",
    "frozen_kelly_applicable_carriers",
    "frozen_kelly_return_distribution_id",
    "eligible_bankroll_nano_usd",
    "full_kelly_ppm",
    "half_kelly_ppm",
    "drawdown_ppm",
    "drawdown_factor_ppm",
    "positive_realized_profit_reinvestment_ppm",
    "realized_loss_effect_ppm",
    "unrealized_profit_expands_bankroll",
    "minimum_post_trade_settled_cash_nlv_ppm",
    "maximum_gld_equivalent_delta_notional_bankroll_ppm",
    "separate_max_loss_budget_mode",
    "planned_loss_equals_maximum_loss",
    "lc0_sizing",
    "bcs_sizing",
}
_SIZING_KEYS = {
    "binding_constraints",
    "capacities",
    "drawdown_factor_ppm",
    "drawdown_ppm",
    "eligible_bankroll_nano_usd",
    "full_kelly_ppm",
    "half_kelly_ppm",
    "missing_fields",
    "per_contract_delta_notional_nano_usd",
    "per_contract_max_loss_nano_usd",
    "quantity",
    "reason_code",
    "state",
}
_CAPACITY_KEYS = {
    "cash",
    "delta_notional",
    "kelly_drawdown",
    "liquidity",
    "max_loss",
    "planned_loss",
}
_SIZING_REASONS_BY_STATE = {
    "PASS": {"SIZING_PASS"},
    "NO_ACTION": {
        "SIZING_COMPLETE_ZERO_QUANTITY",
        "SIZING_DRAWDOWN_BLOCKS_NEW_ENTRY",
        "SIZING_DRAWDOWN_DISASTER_LOCK",
        "SIZING_DRAWDOWN_EXIT_MANAGED",
        "SIZING_STICKY_DRAWDOWN_LOCK_ACTIVE",
    },
    "NO_DECISION": {
        "SIZING_FACT_CONFLICT",
        "SIZING_FACT_MISSING",
        "SIZING_PLANNED_LOSS_MUST_EQUAL_MAX_LOSS",
        "SIZING_STICKY_DRAWDOWN_LOCK_CONFLICT",
    },
}
_EXIT_PLAN_FULL_KEYS = {
    "expiry_safety",
    "fixed_take_profit",
    "gate_a_invalidation",
    "latest_exit_horizon",
    "latest_full_exit_date",
    "lc1_roll_up",
}
_EXIT_PLAN_FALLBACK_KEYS = {
    "fixed_take_profit",
    "latest_exit_horizon",
    "lc1_roll_up",
}
_LC0_SELECTOR_KEYS = {
    "coarse_fine_agreement_required",
    "maximum_delta_ppm",
    "minimum_calendar_days_after_h20",
    "minimum_delta_ppm",
    "require_standard_unadjusted",
    "required_exercise_style",
    "required_multiplier",
    "required_right",
    "second_best_fallback_allowed",
    "selector_id",
    "target_delta_ppm",
    "tie_break_order",
}
_BCS_SELECTOR_KEYS = {
    "leg_ratio",
    "roll_active",
    "same_expiry_required",
    "second_best_fallback_allowed",
    "selector_id",
    "short_maximum_delta_ppm",
    "short_minimum_delta_ppm",
    "short_target_delta_ppm",
}
_NORMAL_SIMULATION_LIMITATIONS = [
    "SYNTHETIC_INPUTS_ONLY",
    "NOT_PROMOTABLE_TO_LIVE_DECISION",
    "NO_BROKER_CONNECTION",
    "NO_ORDER_CREATION_OR_SUBMISSION",
    "LC1_ROLL_UP_NOT_ACTIVE",
]
_FALLBACK_SIMULATION_LIMITATIONS = [
    "SYNTHETIC_INPUTS_ONLY",
    "PIPELINE_FAILED_CLOSED",
    "NO_BROKER_CONNECTION",
    "NO_ORDER_CREATION_OR_SUBMISSION",
]
_ALLOWED_AUTHORITY_BOUNDARY_PATHS = {
    ("actionable",),
    ("broker_order_count",),
}
_AUTHORITY_KEY_FRAGMENTS = (
    "action",
    "action_authority",
    "broker",
    "execute",
    "execution",
    "order",
    "submit",
    "submission",
    "authority",
    "trade_signal",
    "recommendation",
    "buy_now",
    "sell_now",
)


@dataclass(frozen=True, slots=True)
class SimulationWorkflowResultV1:
    status: str
    decision_document: dict[str, object]
    decision_result_sha256: str
    decision_result_path: Path
    decision_receipt_path: Path
    decision_card_path: Path
    supervisor_receipt: P1ProcessSupervisorReceiptV1


def _reject_float(_value: str) -> object:
    raise ValueError("float forbidden")


def _reject_constant(_value: str) -> object:
    raise ValueError("constant forbidden")


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _parse_canonical_bytes(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes:
        raise ValueError("bytes required")
    value = json.loads(
        raw.decode("ascii"),
        parse_float=_reject_float,
        parse_constant=_reject_constant,
        object_pairs_hook=_reject_duplicate_pairs,
    )
    if type(value) is not dict or canonical_json_bytes(value) != raw:
        raise ValueError("noncanonical request")
    return value


def _request_document(
    bundle: RawBundle,
    *,
    native_manifest_path: Path,
    expected_backend_evidence_sha256: str,
) -> dict[str, object]:
    if (
        type(expected_backend_evidence_sha256) is not str
        or _SHA256_RE.fullmatch(expected_backend_evidence_sha256) is None
    ):
        raise SimulationPipelineError("SIM_NATIVE_BACKEND_HASH_INVALID")
    resolved_manifest = _trusted_native_manifest_path(native_manifest_path)
    return {
        "schema_version": SUPERVISOR_REQUEST_SCHEMA_VERSION,
        "bundle_id": bundle.bundle_id,
        "manifest_sha256": bundle.manifest_sha256,
        "raw_content_sha256": bundle.raw_content_sha256,
        "raw_documents": bundle.as_document(),
        "native_manifest_path": str(resolved_manifest),
        "expected_backend_evidence_sha256": expected_backend_evidence_sha256,
    }


def _trusted_native_manifest_path(native_manifest_path: Path) -> Path:
    """Resolve one repo-owned native build manifest without symlink traversal."""

    try:
        lexical_absolute = Path(
            os.path.abspath(os.fspath(native_manifest_path))
        )
        if lexical_absolute.is_symlink():
            raise OSError("manifest symlink forbidden")
        resolved_manifest = lexical_absolute.resolve(strict=True)
        trusted_root = (
            Path(__file__).resolve().parents[2]
            / ".build"
            / "crr_native"
        ).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SimulationPipelineError(
            "SIM_NATIVE_MANIFEST_TRUST_ROOT_INVALID"
        ) from exc
    try:
        relative = resolved_manifest.relative_to(trusted_root)
    except ValueError as exc:
        raise SimulationPipelineError(
            "SIM_NATIVE_MANIFEST_TRUST_ROOT_INVALID"
        ) from exc
    if (
        lexical_absolute != resolved_manifest
        or not resolved_manifest.is_file()
        or len(relative.parts) != 2
        or relative.parts[1] != "manifest_v1.json"
        or _SHA256_RE.fullmatch(relative.parts[0]) is None
        or resolved_manifest.parent.parent != trusted_root
    ):
        raise SimulationPipelineError(
            "SIM_NATIVE_MANIFEST_TRUST_ROOT_INVALID"
        )
    return resolved_manifest


def _success_frame(
    *,
    generation_token: str,
    request_sha256: str,
    reason_code: str,
    semantic_receipt: dict[str, object],
    artifact: dict[str, object],
) -> bytes:
    artifact_sha256 = canonical_json_sha256(artifact)
    return canonical_json_bytes(
        {
            "schema_version": P1_CHILD_FRAME_SCHEMA,
            "generation_token": generation_token,
            "request_sha256": request_sha256,
            "outcome": "SUCCESS",
            "reason_code": reason_code,
            "semantic_receipt": semantic_receipt,
            "artifact": artifact,
            "artifact_sha256": artifact_sha256,
            "actionable": False,
            "broker_order_count": 0,
        }
    )


def _failure_frame(
    *,
    generation_token: str,
    request_sha256: str,
    reason_code: str,
) -> bytes:
    return canonical_json_bytes(
        {
            "schema_version": P1_CHILD_FRAME_SCHEMA,
            "generation_token": generation_token,
            "request_sha256": request_sha256,
            "outcome": "FAIL",
            "reason_code": reason_code,
            "semantic_receipt": None,
            "artifact": None,
            "artifact_sha256": None,
            "actionable": False,
            "broker_order_count": 0,
        }
    )


def _fallback_receipt(
    decision: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": PIPELINE_RECEIPT_SCHEMA_VERSION,
        "classification": SIMULATION_CLASSIFICATION,
        "decision_semantic_sha256": canonical_json_sha256(decision),
        "raw_content_sha256": decision.get("raw_content_sha256"),
        "manifest_sha256": decision.get("manifest_sha256"),
        "rule_sha256": decision.get("rule_sha256"),
        "gate_input_fact_sha256": None,
        "gate_state": "NOT_EVALUABLE",
        "crr_semantic_output_sha256": None,
        "status": "SIMULATED_NO_DECISION",
        "actionable": False,
        "broker_order_count": 0,
    }


def _simulation_decision_child(
    send_connection: Connection,
    generation_token: str,
    request_bytes: bytes,
    request_sha256: str,
    deadline_monotonic_ns: int,
) -> None:
    """Spawn-safe child target with exactly one bounded frame attempt."""

    payload: bytes
    request: dict[str, object] | None = None
    try:
        if (
            type(generation_token) is not str
            or _TOKEN_RE.fullmatch(generation_token) is None
            or type(request_sha256) is not str
            or _SHA256_RE.fullmatch(request_sha256) is None
            or sha256(request_bytes).hexdigest() != request_sha256
        ):
            raise SimulationPipelineError("SIM_SUPERVISOR_REQUEST_INVALID")
        request = _parse_canonical_bytes(request_bytes)
        if (
            set(request) != _REQUEST_KEYS
            or request.get("schema_version")
            != SUPERVISOR_REQUEST_SCHEMA_VERSION
            or type(request.get("raw_documents")) is not dict
        ):
            raise SimulationPipelineError("SIM_SUPERVISOR_REQUEST_INVALID")
        evaluation = evaluate_simulation_request(
            raw_documents=request["raw_documents"],
            bundle_id=request["bundle_id"],
            manifest_sha256=request["manifest_sha256"],
            raw_content_sha256=request["raw_content_sha256"],
            native_manifest_path=Path(request["native_manifest_path"]),
            expected_backend_evidence_sha256=request[
                "expected_backend_evidence_sha256"
            ],
            deadline_monotonic_ns=deadline_monotonic_ns,
        )
        payload = _success_frame(
            generation_token=generation_token,
            request_sha256=request_sha256,
            reason_code="SIMULATION_PIPELINE_RESULT_READY",
            semantic_receipt=evaluation.pipeline_receipt_document,
            artifact=evaluation.decision_document,
        )
    except (
        SimulationPipelineError,
        CarrierSelectionError,
        NormalizationError,
    ) as error:
        reason_code = getattr(error, "reason_code", "SIMULATION_PIPELINE_FAILED")
        if type(reason_code) is not str or not reason_code.isascii():
            reason_code = "SIMULATION_PIPELINE_FAILED"
        if request is None:
            payload = _failure_frame(
                generation_token=generation_token,
                request_sha256=request_sha256,
                reason_code=reason_code,
            )
        else:
            raw_documents = request.get("raw_documents")
            if type(raw_documents) is not dict:
                payload = _failure_frame(
                    generation_token=generation_token,
                    request_sha256=request_sha256,
                    reason_code="SIM_SUPERVISOR_REQUEST_INVALID",
                )
            else:
                decision = minimal_no_decision_document(
                    raw_documents=raw_documents,
                    bundle_id=request.get("bundle_id", "UNKNOWN"),
                    manifest_sha256=request.get("manifest_sha256", "0" * 64),
                    raw_content_sha256=request.get(
                        "raw_content_sha256", "0" * 64
                    ),
                    reason_code=reason_code,
                )
                payload = _success_frame(
                    generation_token=generation_token,
                    request_sha256=request_sha256,
                    reason_code="SIMULATION_PIPELINE_FAIL_CLOSED_RESULT_READY",
                    semantic_receipt=_fallback_receipt(decision),
                    artifact=decision,
                )
    except Exception:
        payload = _failure_frame(
            generation_token=generation_token,
            request_sha256=request_sha256,
            reason_code="SIMULATION_PIPELINE_UNEXPECTED_EXCEPTION",
        )
    try:
        send_connection.send_bytes(payload)
    finally:
        send_connection.close()


(
    _BEGIN_WORKFLOW,
    _RUN_ISSUED,
    _CONSUME_ISSUED,
    _FINALIZE_WORKFLOW,
) = _make_p1_workflow_process_supervisor(_simulation_decision_child)


def _validate_decision_document(
    decision: object,
    *,
    bundle: RawBundle,
    expected_backend_evidence_sha256: str,
    expected_combined_backend_evidence_sha256: str | None = None,
) -> dict[str, object]:
    """Validate the complete non-authoritative DecisionResult boundary.

    This is intentionally independent from the child-side constructors.  A
    child compromise or accidental schema drift must not gain order authority
    merely because its frame was well formed.
    """

    if (
        type(decision) is not dict
        or not _is_sha256(expected_backend_evidence_sha256)
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    if (
        set(decision) != _DECISION_KEYS
        or decision.get("schema_version") != DECISION_SCHEMA_VERSION
        or decision.get("classification") != SIMULATION_CLASSIFICATION
        or decision.get("decision_scope") != "ENTRY_SIMULATION_ONLY"
        or decision.get("actionable") is not False
        or type(decision.get("broker_order_count")) is not int
        or decision.get("broker_order_count") != 0
        or decision.get("manifest_sha256") != bundle.manifest_sha256
        or decision.get("raw_content_sha256") != bundle.raw_content_sha256
        or decision.get("bundle_id") != bundle.bundle_id
        or decision.get("status")
        not in {
            "SIMULATED_OWNER_SELECTION_REQUIRED",
            "SIMULATED_NO_ACTION",
            "SIMULATED_NO_DECISION",
        }
        or type(decision.get("reason_code")) is not str
        or _REASON_RE.fullmatch(decision["reason_code"]) is None
        or type(decision.get("bundle_id")) is not str
        or not decision["bundle_id"].isascii()
        or not decision["bundle_id"]
        or not _is_sha256(decision.get("manifest_sha256"))
        or not _is_sha256(decision.get("raw_content_sha256"))
        or not _is_sha256(decision.get("rule_sha256"))
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    is_fallback = _is_fail_closed_fallback(decision)
    rule_package, _market_status = _validate_source_bindings(
        decision,
        bundle=bundle,
        fallback=is_fallback,
    )
    normalized_market: _NormalizedMarketV1 | None = None
    if not is_fallback:
        try:
            normalized_market = _normalize_market(bundle.as_document())
        except (
            AttributeError,
            NormalizationError,
            RawBundleError,
            SimulationPipelineError,
        ) as exc:
            raise SimulationPipelineError(
                "SIM_DECISION_SOURCE_BINDING_INVALID"
            ) from exc
    _validate_gate_source_binding(
        decision,
        bundle=bundle,
        fallback=is_fallback,
        normalized_market=normalized_market,
    )
    expected_manual_control = {
        "SIMULATED_OWNER_SELECTION_REQUIRED": (
            "SIMULATION_SELECTION_IN_CODEX_ONLY"
        ),
        "SIMULATED_NO_ACTION": "NO_ACTION_NO_ORDER_AUTHORITY",
        "SIMULATED_NO_DECISION": "NO_DECISION_NO_ORDER_AUTHORITY",
    }.get(decision.get("status"))
    if decision.get("manual_control") != expected_manual_control:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    gate_state = _validate_gate_a(decision.get("gate_a"), fallback=is_fallback)
    crr_status, crr_terminals = _validate_crr(
        decision.get("crr"),
        expected_backend_evidence_sha256=expected_backend_evidence_sha256,
        expected_combined_backend_evidence_sha256=(
            expected_combined_backend_evidence_sha256
        ),
    )
    risk_status, sizing_states = _validate_risk_context(
        decision.get("risk_context"),
        decision=decision,
        rule_package=rule_package,
    )
    _validate_exit_plan(decision.get("exit_plan"), fallback=is_fallback)
    if (
        normalized_market is not None
        and decision.get("exit_plan") != _exit_plan(normalized_market)
    ):
        raise SimulationPipelineError(
            "SIM_DECISION_SOURCE_DERIVATION_INVALID"
        )
    _validate_simulation_limitations(
        decision.get("simulation_limitations"),
        fallback=is_fallback,
    )
    owner_required = decision.get("owner_selection_required")
    candidates = decision.get("candidates")
    if type(owner_required) is not bool or type(candidates) is not list:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    option_facts: _OptionFactsV1 | None = None
    if crr_terminals:
        if normalized_market is None:
            raise SimulationPipelineError(
                "SIM_DECISION_CRR_RAW_BINDING_INVALID"
            )
        option_facts = _validate_crr_raw_input_bindings(
            crr_terminals,
            bundle=bundle,
            market=normalized_market,
        )
    if owner_required:
        if (
            decision["status"] != "SIMULATED_OWNER_SELECTION_REQUIRED"
            or len(candidates) != 2
        ):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
        for index, carrier_id in enumerate(("LC0", "BCS0")):
            _validate_candidate(
                candidates[index],
                expected_carrier_id=carrier_id,
                rule_package=rule_package,
            )
        _validate_candidate_risk_bindings(
            candidates,
            risk_context=decision["risk_context"],
        )
        _validate_candidate_raw_bindings(candidates, bundle=bundle)
    elif decision["status"] == "SIMULATED_OWNER_SELECTION_REQUIRED":
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    elif candidates:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    if crr_status == "EXACT64_PASS":
        if (
            normalized_market is None
            or option_facts is None
            or not crr_terminals
        ):
            raise SimulationPipelineError(
                "SIM_DECISION_SOURCE_DERIVATION_INVALID"
            )
        _validate_post_crr_source_derivations(
            candidates,
            risk_context=decision["risk_context"],
            bundle=bundle,
            market=normalized_market,
            rule_package=rule_package,
            terminals=crr_terminals,
            option_facts=option_facts,
        )
    _reject_authority_fields(decision)
    _validate_terminal_relationships(
        decision=decision,
        fallback=is_fallback,
        gate_state=gate_state,
        crr_status=crr_status,
        risk_status=risk_status,
        sizing_states=sizing_states,
    )
    return decision


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _is_fail_closed_fallback(decision: dict[str, object]) -> bool:
    """Identify the sealed fallback from fail-closed facts, never UI wording."""

    gate = decision.get("gate_a")
    crr = decision.get("crr")
    risk = decision.get("risk_context")
    limitations = decision.get("simulation_limitations")
    return (
        decision.get("status") == "SIMULATED_NO_DECISION"
        and decision.get("reason_code") in SAFE_FAIL_CLOSED_REASON_CODES
        and type(limitations) is list
        and "PIPELINE_FAILED_CLOSED" in limitations
        and type(gate) is dict
        and gate.get("state") == "NOT_EVALUABLE"
        and gate.get("reason_code") == decision.get("reason_code")
        and type(crr) is dict
        and crr.get("status") == "NOT_RUN_PIPELINE_FAILED_CLOSED"
        and type(risk) is dict
        and risk.get("status") == "NOT_RUN_PIPELINE_FAILED_CLOSED"
    )


def _validate_source_bindings(
    decision: dict[str, object],
    *,
    bundle: RawBundle,
    fallback: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    try:
        rule_package = bundle.read_json("rule_package")
        market_status = bundle.read_json("market_status")
    except (AttributeError, RawBundleError) as exc:
        raise SimulationPipelineError(
            "SIM_DECISION_SOURCE_BINDING_INVALID"
        ) from exc
    market_cutoff = (
        market_status.get("as_of_utc_ns")
        if type(market_status) is dict
        else None
    )
    expected_cutoff = (
        _fallback_cutoff_utc_ns(market_cutoff)
        if fallback
        else market_cutoff
    )
    if (
        type(rule_package) is not dict
        or type(market_status) is not dict
        or type(rule_package.get("rule_package_id")) is not str
        or type(market_status.get("trading_date")) is not str
        or _DATE_RE.fullmatch(market_status["trading_date"]) is None
        or (
            not fallback
            and _fallback_cutoff_utc_ns(market_cutoff) != market_cutoff
        )
        or decision.get("rule_sha256")
        != canonical_json_sha256(rule_package)
        or decision.get("rule_package_version")
        != rule_package["rule_package_id"]
        or decision.get("trading_date")
        != market_status["trading_date"]
        or decision.get("cutoff_utc_ns") != expected_cutoff
    ):
        raise SimulationPipelineError(
            "SIM_DECISION_SOURCE_BINDING_INVALID"
        )
    return rule_package, market_status


def _validate_gate_source_binding(
    decision: dict[str, object],
    *,
    bundle: RawBundle,
    fallback: bool,
    normalized_market: _NormalizedMarketV1 | None,
) -> None:
    # A supervisor/pipeline failure intentionally publishes the sealed minimal
    # NOT_EVALUABLE form and makes no claim about a recomputed Gate result.
    if fallback:
        return
    if normalized_market is None:
        raise SimulationPipelineError(
            "SIM_DECISION_GATE_SOURCE_BINDING_INVALID"
        )
    expected_gate = normalized_market.gate_result.as_dict()
    if decision.get("gate_a") != expected_gate:
        raise SimulationPipelineError(
            "SIM_DECISION_GATE_SOURCE_BINDING_INVALID"
        )


def _validate_gate_a(gate: object, *, fallback: bool) -> str:
    if type(gate) is not dict:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    if fallback:
        if (
            set(gate) != _GATE_FALLBACK_KEYS
            or gate.get("state") != "NOT_EVALUABLE"
            or type(gate.get("reason_code")) is not str
            or _REASON_RE.fullmatch(gate["reason_code"]) is None
            or gate.get("predicates") != {}
            or gate.get("metrics") != {}
        ):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
        return "NOT_EVALUABLE"
    if set(gate) != _GATE_FULL_KEYS:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    state = gate.get("state")
    reason_code = gate.get("reason_code")
    metrics = gate.get("metrics")
    predicates = gate.get("predicates")
    if (
        state not in _GATE_REASONS_BY_STATE
        or reason_code not in _GATE_REASONS_BY_STATE[state]
        or gate.get("rule_version") != "SIM_A1_RANGE_HOLD_V1"
        or not _is_sha256(gate.get("input_fact_sha256"))
        or type(metrics) is not dict
        or set(metrics) != _GATE_METRIC_KEYS
        or type(predicates) is not dict
        or set(predicates) != _GATE_PREDICATE_KEYS
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    for key in ("max_event_utc_ns", "max_receive_utc_ns"):
        value = gate.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    if (
        gate["max_event_utc_ns"] is not None
        and gate["max_receive_utc_ns"] is not None
        and gate["max_receive_utc_ns"] < gate["max_event_utc_ns"]
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    for value in metrics.values():
        if value is not None and (type(value) is not int or value < 0):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    for value in predicates.values():
        if value is not None and type(value) is not bool:
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    if state in {"PASS", "FAIL"} and (
        any(value is None for value in metrics.values())
        or any(value is None for value in predicates.values())
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    return state


def _validate_crr(
    crr: object,
    *,
    expected_backend_evidence_sha256: str,
    expected_combined_backend_evidence_sha256: str | None,
) -> tuple[str, tuple[CrrSemanticTerminalV1, ...]]:
    if type(crr) is not dict:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    status = crr.get("status")
    if status in _CRR_NOT_RUN_STATUSES:
        if (
            set(crr) != _CRR_NOT_RUN_KEYS
            or crr.get("requested") != 0
            or crr.get("terminal") != 0
            or crr.get("failure_count") != 0
            or crr.get("model_id") != MODEL_ID
            or crr.get("model_sha256") != MODEL_SHA256
        ):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
        return status, ()
    if status not in {"EXACT64_PASS", "EXACT64_FAIL_CLOSED"} or set(
        crr
    ) != _CRR_EXACT64_KEYS:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    for key in (
        "combined_backend_evidence_sha256",
        "execution_provenance_sha256",
        "input_vector_sha256",
        "native_build_backend_evidence_sha256",
        "semantic_output_sha256",
    ):
        if not _is_sha256(crr.get(key)):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    if (
        crr.get("model_id") != MODEL_ID
        or crr.get("model_sha256") != MODEL_SHA256
        or crr.get("numerical_semantics_trust_boundary")
        != "SUPERVISED_CHILD_ATTESTED_PARENT_DID_NOT_RECOMPUTE_CRR_NUMERICS"
        or crr.get("native_build_backend_evidence_sha256")
        != expected_backend_evidence_sha256
        or not _is_sha256(expected_combined_backend_evidence_sha256)
        or crr.get("combined_backend_evidence_sha256")
        != expected_combined_backend_evidence_sha256
        or any(
            crr.get(key) != 64
            for key in ("requested", "bound", "started", "terminal")
        )
        or crr.get("worker_count") != 8
        or crr.get("kernel_threads") != 1
        or crr.get("cache_hits") != 0
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    for key in (
        "cache_hits",
        "early_exercise_terminal_count",
        "failure_count",
    ):
        value = crr.get(key)
        if type(value) is not int or not 0 <= value <= 64:
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    raw_terminals = crr.get("semantic_terminals")
    if type(raw_terminals) is not list or len(raw_terminals) != 64:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    try:
        terminals = tuple(
            CrrSemanticTerminalV1(**item)
            for item in raw_terminals
            if type(item) is dict
        )
        if len(terminals) != 64:
            raise CrrBatchError("BATCH_TERMINAL_VECTOR_INVALID")
        expected_semantic_sha256 = semantic_output_sha256(terminals)
        expected_input_vector_sha256 = sha256(
            canonical_json_bytes(
                [item.input_sha256 for item in terminals]
            )
            + b"\n"
        ).hexdigest()
        expected_provenance_sha256 = execution_provenance_sha256(
            backend_evidence_sha256=(
                expected_combined_backend_evidence_sha256
            ),
            input_vector_sha256=expected_input_vector_sha256,
            semantic_output_sha256_value=expected_semantic_sha256,
        )
    except (CrrBatchError, RawBundleError, TypeError) as exc:
        raise SimulationPipelineError(
            "SIM_DECISION_CRR_TERMINAL_BINDING_INVALID"
        ) from exc
    failures = tuple(
        item for item in terminals if item.terminal_status == "FAIL"
    )
    first_failure = failures[0].ordinal if failures else None
    if (
        crr.get("semantic_output_sha256") != expected_semantic_sha256
        or crr.get("input_vector_sha256")
        != expected_input_vector_sha256
        or crr.get("execution_provenance_sha256")
        != expected_provenance_sha256
        or crr.get("failure_count") != len(failures)
        or crr.get("first_failure_ordinal") != first_failure
        or crr.get("early_exercise_terminal_count")
        != sum(1 for item in terminals if item.early_exercise_detected)
        or (status == "EXACT64_PASS" and failures)
        or (status == "EXACT64_FAIL_CLOSED" and not failures)
    ):
        raise SimulationPipelineError(
            "SIM_DECISION_CRR_TERMINAL_BINDING_INVALID"
        )
    return status, terminals


def _validate_crr_raw_input_bindings(
    terminals: tuple[CrrSemanticTerminalV1, ...],
    *,
    bundle: RawBundle,
    market: _NormalizedMarketV1,
) -> _OptionFactsV1:
    """Replay only raw input binding; never invoke either CRR engine."""

    try:
        raw_documents = bundle.as_document()
        option_facts = _option_facts(
            raw_documents=raw_documents,
            raw_content_sha256=bundle.raw_content_sha256,
            bundle_id=bundle.bundle_id,
            market=market,
        )
        contracts = tuple(
            quote.contract for quote in option_facts.snapshot.option_quotes
        )
        expected_inputs = bind_crr_call_inputs_bulk_from_snapshot(
            option_facts.snapshot,
            contracts=contracts,
            pit_inputs=option_facts.pit_inputs,
        )
    except (
        AttributeError,
        CarrierSelectionError,
        NormalizationError,
        RawBundleError,
        SimulationPipelineError,
        TypeError,
        ValueError,
    ) as exc:
        raise SimulationPipelineError(
            "SIM_DECISION_CRR_RAW_BINDING_INVALID"
        ) from exc
    if tuple(
        (item.contract_id, item.input_sha256) for item in terminals
    ) != tuple(
        (item.contract_id, item.input_sha256) for item in expected_inputs
    ):
        raise SimulationPipelineError(
            "SIM_DECISION_CRR_RAW_BINDING_INVALID"
        )
    return option_facts


def _validate_post_crr_source_derivations(
    candidates: list[object],
    *,
    risk_context: object,
    bundle: RawBundle,
    market: _NormalizedMarketV1,
    rule_package: dict[str, object],
    terminals: tuple[CrrSemanticTerminalV1, ...],
    option_facts: _OptionFactsV1,
) -> None:
    """Replay raw facts through selection, economics, and sizing."""

    lc0_rule = rule_package.get("lc0")
    bcs_rule = rule_package.get("bcs")
    risk_rule = rule_package.get("risk")
    if (
        type(lc0_rule) is not dict
        or type(bcs_rule) is not dict
        or type(risk_rule) is not dict
        or type(risk_context) is not dict
    ):
        raise SimulationPipelineError(
            "SIM_DECISION_SOURCE_DERIVATION_INVALID"
        )
    integer_rule_fields = (
        lc0_rule.get("minimum_calendar_days_after_h20"),
        lc0_rule.get("target_delta_ppm"),
        lc0_rule.get("minimum_delta_ppm"),
        lc0_rule.get("maximum_delta_ppm"),
        bcs_rule.get("short_minimum_delta_ppm"),
        bcs_rule.get("short_maximum_delta_ppm"),
        risk_rule.get("entry_stress_long_ticks"),
        risk_rule.get("entry_stress_short_ticks"),
    )
    if any(type(value) is not int for value in integer_rule_fields):
        raise SimulationPipelineError(
            "SIM_DECISION_SOURCE_DERIVATION_INVALID"
        )
    try:
        selection = select_lc0_and_bcs(
            snapshot=option_facts.snapshot,
            terminals=terminals,
            h20_date=market.h20_date,
            minimum_days_after_h20=lc0_rule[
                "minimum_calendar_days_after_h20"
            ],
            lc0_target_delta_ppm=lc0_rule["target_delta_ppm"],
            lc0_minimum_delta_ppm=lc0_rule["minimum_delta_ppm"],
            lc0_maximum_delta_ppm=lc0_rule["maximum_delta_ppm"],
        )
        if not (
            bcs_rule["short_minimum_delta_ppm"]
            <= selection.short_coarse_delta_ppm
            <= bcs_rule["short_maximum_delta_ppm"]
            and bcs_rule["short_minimum_delta_ppm"]
            <= selection.short_delta_ppm
            <= bcs_rule["short_maximum_delta_ppm"]
        ):
            raise CarrierSelectionError(
                "SIM_BCS_SELECTOR_RULE_BINDING_INVALID"
            )
        lc0_economics, bcs_economics = price_simulation_carriers(
            selection=selection,
            snapshot=option_facts.snapshot,
            fees=option_facts.fees,
            long_entry_adverse_ticks=risk_rule[
                "entry_stress_long_ticks"
            ],
            short_entry_adverse_ticks=risk_rule[
                "entry_stress_short_ticks"
            ],
            include_exit_fees_in_max_loss=risk_rule[
                "include_exit_fees_in_max_loss"
            ],
        )
        raw_documents = bundle.as_document()
        account, kelly, missing = _preflight_risk_facts(
            raw_documents,
            market=market,
        )
        if missing:
            raise SimulationPipelineError("SIM_RISK_FACT_MISSING")
        spot_total = (
            option_facts.snapshot.underlying_top.bid_nano_usd
            + option_facts.snapshot.underlying_top.ask_nano_usd
        )
        if spot_total % 2:
            raise SimulationPipelineError(
                "DELTA_INPUT_MIDPOINT_NON_INTEGRAL"
            )
        spot = spot_total // 2
        lc0_sizing = _sizing(
            account=account,
            kelly=kelly,
            economics=lc0_economics,
            delta_ppm=selection.long_delta_ppm,
            spot_nano_usd=spot,
        )
        bcs_sizing = _sizing(
            account=account,
            kelly=kelly,
            economics=bcs_economics,
            delta_ppm=selection.net_delta_ppm,
            spot_nano_usd=spot,
        )
        selector_policy = {
            "candidate_terminal_policy": risk_rule.get(
                "candidate_terminal_policy"
            ),
            "lc0": lc0_rule,
            "bcs": bcs_rule,
            "coarse_fine_agreement_required": True,
            "second_best_fallback_allowed": False,
        }
        sizing_states = {lc0_sizing.state, bcs_sizing.state}
        expected_candidates = (
            [
                _candidate(
                    carrier_id="LC0",
                    selection=selection,
                    economics=lc0_economics,
                    sizing=lc0_sizing,
                    underlying_mid_nano_usd=spot,
                    selector_policy=selector_policy,
                ),
                _candidate(
                    carrier_id="BCS0",
                    selection=selection,
                    economics=bcs_economics,
                    sizing=bcs_sizing,
                    underlying_mid_nano_usd=spot,
                    selector_policy=selector_policy,
                ),
            ]
            if sizing_states == {"PASS"}
            else []
        )
        expected_risk_status = (
            "PASS" if sizing_states == {"PASS"} else "BLOCKED"
        )
    except (
        AttributeError,
        CarrierSelectionError,
        NormalizationError,
        RawBundleError,
        SimulationPipelineError,
        TypeError,
        ValueError,
    ) as exc:
        raise SimulationPipelineError(
            "SIM_DECISION_SOURCE_DERIVATION_INVALID"
        ) from exc
    if candidates != expected_candidates:
        raise SimulationPipelineError(
            "SIM_DECISION_SOURCE_DERIVATION_INVALID"
        )
    if (
        risk_context.get("status") != expected_risk_status
        or risk_context.get("account_as_of_utc_ns")
        != account.get("as_of_utc_ns")
        or risk_context.get("sticky_drawdown_lock_active")
        != account.get("sticky_drawdown_lock_active")
        or risk_context.get("frozen_kelly_receipt_id")
        != kelly.get("receipt_id")
        or risk_context.get("frozen_kelly_strategy_rule_package_id")
        != kelly.get("strategy_rule_package_id")
        or risk_context.get("frozen_kelly_applicable_carriers")
        != kelly.get("applicable_carriers")
        or risk_context.get("frozen_kelly_return_distribution_id")
        != kelly.get("return_distribution_id")
        or risk_context.get("lc0_sizing") != lc0_sizing.as_dict()
        or risk_context.get("bcs_sizing") != bcs_sizing.as_dict()
    ):
        raise SimulationPipelineError(
            "SIM_DECISION_SOURCE_DERIVATION_INVALID"
        )


def _validate_capacities(
    capacities: object,
    *,
    allow_none: bool,
) -> dict[str, object]:
    if type(capacities) is not dict or set(capacities) != _CAPACITY_KEYS:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    for value in capacities.values():
        if value is None and allow_none:
            continue
        if type(value) is not int or value < 0:
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    return capacities


def _validate_sizing(sizing: object) -> str:
    if type(sizing) is not dict or set(sizing) != _SIZING_KEYS:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    state = sizing.get("state")
    if (
        state not in _SIZING_REASONS_BY_STATE
        or sizing.get("reason_code") not in _SIZING_REASONS_BY_STATE[state]
        or type(sizing.get("binding_constraints")) is not list
        or type(sizing.get("missing_fields")) is not list
        or any(
            type(value) is not str or not value.isascii() or not value
            for value in sizing["binding_constraints"]
            + sizing["missing_fields"]
        )
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    numeric_keys = (
        "drawdown_factor_ppm",
        "drawdown_ppm",
        "eligible_bankroll_nano_usd",
        "full_kelly_ppm",
        "half_kelly_ppm",
        "per_contract_delta_notional_nano_usd",
        "per_contract_max_loss_nano_usd",
        "quantity",
    )
    if state == "NO_DECISION":
        if (
            any(sizing.get(key) is not None for key in numeric_keys)
            or sizing["binding_constraints"]
        ):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
        _validate_capacities(sizing.get("capacities"), allow_none=True)
        if any(value is not None for value in sizing["capacities"].values()):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
        return state
    if (
        any(
            type(sizing.get(key)) is not int or sizing[key] < 0
            for key in numeric_keys
        )
        or sizing["missing_fields"]
        or not sizing["binding_constraints"]
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    _validate_capacities(sizing.get("capacities"), allow_none=False)
    if state == "PASS" and sizing["quantity"] <= 0:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    return state


def _validate_risk_context(
    risk: object,
    *,
    decision: dict[str, object],
    rule_package: dict[str, object],
) -> tuple[str, tuple[str, ...]]:
    if type(risk) is not dict or type(risk.get("status")) is not str:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    status = risk["status"]
    not_run = {
        "NOT_RUN_CRR_FAILED",
        "NOT_RUN_GATE_FAILED",
        "NOT_RUN_GATE_NOT_EVALUABLE",
        "NOT_RUN_OPTION_DATA_INCOMPLETE",
        "NOT_RUN_PIPELINE_FAILED_CLOSED",
    }
    if status in not_run:
        if set(risk) != {"status"}:
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
        return status, ()
    if status == "NO_DECISION":
        missing = risk.get("missing_fields")
        if (
            set(risk) != {"status", "reason_code", "missing_fields"}
            or risk.get("reason_code") != "SIZING_FACT_MISSING"
            or type(missing) is not list
            or not missing
            or any(
                type(value) is not str or not value.isascii() or not value
                for value in missing
            )
        ):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
        return status, ()
    if status not in {"PASS", "BLOCKED"} or set(risk) != _RISK_FULL_KEYS:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    lc0 = risk.get("lc0_sizing")
    bcs = risk.get("bcs_sizing")
    lc0_state = _validate_sizing(lc0)
    bcs_state = _validate_sizing(bcs)
    if (
        risk.get("policy_id") != "SIM_HALF_KELLY_RISK_V1"
        or risk.get("candidate_terminal_policy")
        != "BOTH_LC0_AND_BCS_MUST_PASS"
        or risk.get("account_as_of_utc_ns") != decision.get("cutoff_utc_ns")
        or type(risk.get("sticky_drawdown_lock_active")) is not bool
        or type(risk.get("frozen_kelly_receipt_id")) is not str
        or not risk["frozen_kelly_receipt_id"].isascii()
        or risk.get("frozen_kelly_strategy_rule_package_id")
        != rule_package.get("rule_package_id")
        or risk.get("frozen_kelly_applicable_carriers") != ["LC0", "BCS0"]
        or risk.get("frozen_kelly_return_distribution_id")
        != "SIM_DUAL_CARRIER_CONSERVATIVE_V1"
        or risk.get("positive_realized_profit_reinvestment_ppm") != 500_000
        or risk.get("realized_loss_effect_ppm") != 1_000_000
        or risk.get("unrealized_profit_expands_bankroll") is not False
        or risk.get("minimum_post_trade_settled_cash_nlv_ppm") != 100_000
        or risk.get("maximum_gld_equivalent_delta_notional_bankroll_ppm")
        != 1_500_000
        or risk.get("separate_max_loss_budget_mode")
        != "ELIGIBLE_BANKROLL_HARD_CEILING_SIMULATION_ONLY"
        or risk.get("planned_loss_equals_maximum_loss") is not True
        or status == "PASS" and {lc0_state, bcs_state} != {"PASS"}
        or status == "BLOCKED" and lc0_state == bcs_state == "PASS"
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    assert type(lc0) is dict
    for key in (
        "eligible_bankroll_nano_usd",
        "full_kelly_ppm",
        "half_kelly_ppm",
        "drawdown_ppm",
        "drawdown_factor_ppm",
    ):
        if risk.get(key) != lc0.get(key):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    return status, (lc0_state, bcs_state)


def _validate_exit_plan(exit_plan: object, *, fallback: bool) -> None:
    if type(exit_plan) is not dict:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    if fallback:
        if (
            set(exit_plan) != _EXIT_PLAN_FALLBACK_KEYS
            or exit_plan.get("fixed_take_profit") is not False
            or exit_plan.get("latest_exit_horizon") != "H20_NOT_EVALUABLE"
            or exit_plan.get("lc1_roll_up") != "NOT_ACTIVE_IN_SIM_V1"
        ):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
        return
    if (
        set(exit_plan) != _EXIT_PLAN_FULL_KEYS
        or exit_plan.get("fixed_take_profit") is not False
        or exit_plan.get("expiry_safety")
        != "FULL_EXIT_REQUIRED_BEFORE_CONTRACT_SAFETY_BOUNDARY"
        or exit_plan.get("gate_a_invalidation")
        != "EXIT_REVIEW_REQUIRED_IF_SIM_A1_RANGE_HOLD_V1_FAILS"
        or exit_plan.get("latest_exit_horizon") != "H20"
        or type(exit_plan.get("latest_full_exit_date")) is not str
        or _DATE_RE.fullmatch(exit_plan["latest_full_exit_date"]) is None
        or exit_plan.get("lc1_roll_up") != "NOT_ACTIVE_IN_SIM_V1"
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")


def _validate_simulation_limitations(
    limitations: object,
    *,
    fallback: bool,
) -> None:
    expected = (
        _FALLBACK_SIMULATION_LIMITATIONS
        if fallback
        else _NORMAL_SIMULATION_LIMITATIONS
    )
    if limitations != expected:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")


def _validate_terminal_relationships(
    *,
    decision: dict[str, object],
    fallback: bool,
    gate_state: str,
    crr_status: str,
    risk_status: str,
    sizing_states: tuple[str, ...],
) -> None:
    status = decision["status"]
    expected_reason_code: str | None = None
    if fallback:
        valid = (
            status == "SIMULATED_NO_DECISION"
            and decision["owner_selection_required"] is False
            and not decision["candidates"]
            and gate_state == "NOT_EVALUABLE"
            and crr_status == "NOT_RUN_PIPELINE_FAILED_CLOSED"
            and risk_status == "NOT_RUN_PIPELINE_FAILED_CLOSED"
            and decision.get("reason_code")
            in SAFE_FAIL_CLOSED_REASON_CODES
            and decision.get("gate_a", {}).get("reason_code")
            == decision.get("reason_code")
        )
        expected_reason_code = decision.get("reason_code")
    elif status == "SIMULATED_OWNER_SELECTION_REQUIRED":
        valid = (
            gate_state == "PASS"
            and crr_status == "EXACT64_PASS"
            and risk_status == "PASS"
            and sizing_states == ("PASS", "PASS")
        )
        expected_reason_code = (
            "BOTH_CARRIERS_PASS_OWNER_SELECTION_REQUIRED"
        )
    elif status == "SIMULATED_NO_ACTION":
        gate_failed = (
            gate_state == "FAIL"
            and crr_status == "NOT_RUN_GATE_FAILED"
            and risk_status == "NOT_RUN_GATE_FAILED"
        )
        risk_blocked = (
            gate_state == "PASS"
            and crr_status == "EXACT64_PASS"
            and risk_status == "BLOCKED"
            and "NO_ACTION" in sizing_states
        )
        valid = gate_failed or risk_blocked
        if gate_failed:
            expected_reason_code = "GATE_A_COMPLETE_NOT_PASSED"
        elif risk_blocked:
            risk = decision.get("risk_context")
            reasons = {
                sizing.get("reason_code")
                for sizing in (
                    risk.get("lc0_sizing") if type(risk) is dict else None,
                    risk.get("bcs_sizing") if type(risk) is dict else None,
                )
                if type(sizing) is dict
            }
            if "SIZING_STICKY_DRAWDOWN_LOCK_ACTIVE" in reasons:
                expected_reason_code = (
                    "RISK_STICKY_DRAWDOWN_LOCK_ACTIVE"
                )
            elif "SIZING_DRAWDOWN_EXIT_MANAGED" in reasons:
                expected_reason_code = "RISK_DRAWDOWN_EXIT_MANAGED"
            else:
                expected_reason_code = "RISK_CAPACITY_BLOCKS_ENTRY"
    else:
        gate_not_evaluable = (
            gate_state == "NOT_EVALUABLE"
            and crr_status == "NOT_RUN_GATE_NOT_EVALUABLE"
            and risk_status == "NOT_RUN_GATE_NOT_EVALUABLE"
        )
        option_incomplete = (
            gate_state == "PASS"
            and crr_status == "NOT_RUN_OPTION_DATA_INCOMPLETE"
            and risk_status == "NOT_RUN_OPTION_DATA_INCOMPLETE"
        )
        risk_missing = (
            gate_state == "PASS"
            and crr_status == "NOT_RUN_RISK_FACT_MISSING"
            and risk_status == "NO_DECISION"
        )
        crr_failed = (
            gate_state == "PASS"
            and crr_status == "EXACT64_FAIL_CLOSED"
            and risk_status == "NOT_RUN_CRR_FAILED"
        )
        risk_not_evaluable = (
            gate_state == "PASS"
            and crr_status == "EXACT64_PASS"
            and risk_status == "BLOCKED"
            and "NO_DECISION" in sizing_states
        )
        valid = (
            gate_not_evaluable
            or option_incomplete
            or risk_missing
            or crr_failed
            or risk_not_evaluable
        )
        if gate_not_evaluable:
            expected_reason_code = "GATE_A_NOT_EVALUABLE"
        elif option_incomplete:
            expected_reason_code = "OPTION_DATA_INCOMPLETE"
        elif risk_missing:
            expected_reason_code = "RISK_FACT_MISSING"
        elif crr_failed:
            crr = decision.get("crr")
            raw_terminals = (
                crr.get("semantic_terminals")
                if type(crr) is dict
                else None
            )
            first_failure = next(
                (
                    item.get("reason_code")
                    for item in raw_terminals
                    if type(item) is dict
                    and item.get("terminal_status") == "FAIL"
                ),
                None,
            ) if type(raw_terminals) is list else None
            expected_reason_code = (
                first_failure if type(first_failure) is str else None
            )
        elif risk_not_evaluable:
            expected_reason_code = "RISK_OR_QUANTITY_NOT_EVALUABLE"
    if not valid or decision.get("reason_code") != expected_reason_code:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")


def _is_safe_nontrading_metadata_path(path: tuple[str, ...]) -> bool:
    if path == ("crr", "execution_provenance_sha256"):
        return True
    if (
        len(path) == 4
        and path[0] == "candidates"
        and path[1].isdigit()
        and path[2:] == ("economics", "execution_assumptions")
    ):
        return True
    return (
        len(path) == 5
        and path[0] == "candidates"
        and path[1].isdigit()
        and path[2:] == (
            "selector_policy",
            "lc0",
            "tie_break_order",
        )
    )


def _reject_authority_fields(
    value: object,
    *,
    path: tuple[str, ...] = (),
) -> None:
    if type(value) is dict:
        for key, nested in value.items():
            if type(key) is not str:
                raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
            nested_path = (*path, key)
            lowered = key.lower()
            if nested_path == (
                "crr",
                "execution_provenance_sha256",
            ) and not _is_sha256(
                nested
            ):
                raise SimulationPipelineError(
                    "SIM_DECISION_ARTIFACT_INVALID"
                )
            if nested_path[-1] == "tie_break_order" and (
                not _is_safe_nontrading_metadata_path(nested_path)
                or nested
                != [
                    "ABS_DELTA_DISTANCE_ASC",
                    "STRIKE_NANO_USD_DESC",
                    "OCC_SYMBOL_ASCII_ASC",
                ]
            ):
                raise SimulationPipelineError(
                    "SIM_DECISION_ARTIFACT_INVALID"
                )
            if (
                nested_path not in _ALLOWED_AUTHORITY_BOUNDARY_PATHS
                and not _is_safe_nontrading_metadata_path(nested_path)
                and any(fragment in lowered for fragment in _AUTHORITY_KEY_FRAGMENTS)
            ):
                raise SimulationPipelineError(
                    "SIM_DECISION_AUTHORITY_FIELD_FORBIDDEN"
                )
            _reject_authority_fields(nested, path=nested_path)
    elif type(value) is list:
        for index, nested in enumerate(value):
            _reject_authority_fields(nested, path=(*path, str(index)))


def _validate_candidate(
    candidate: object,
    *,
    expected_carrier_id: str,
    rule_package: dict[str, object],
) -> None:
    if type(candidate) is not dict or set(candidate) != _CANDIDATE_KEYS:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    integer_fields = (
        "quantity",
        "underlying_mid_nano_usd",
        "long_coarse_delta_ppm",
        "long_fine_delta_ppm",
        "net_delta_ppm_per_strategy_unit",
        "max_loss_nano_usd",
        "total_entry_cost_nano_usd",
        "base_entry_cost_nano_usd_per_contract",
        "stressed_entry_cost_nano_usd_per_contract",
        "delta_notional_nano_usd_per_contract",
    )
    if (
        candidate.get("carrier_id") != expected_carrier_id
        or candidate.get("status") != "PASS"
        or candidate.get("reason_code")
        != "SIMULATED_CANDIDATE_ELIGIBLE"
        or type(candidate.get("quantity")) is not int
        or candidate["quantity"] <= 0
        or type(candidate.get("expiry_date")) is not str
        or _DATE_RE.fullmatch(candidate["expiry_date"]) is None
        or type(candidate.get("h20_date")) is not str
        or _DATE_RE.fullmatch(candidate["h20_date"]) is None
        or type(candidate.get("minimum_expiry_date")) is not str
        or _DATE_RE.fullmatch(candidate["minimum_expiry_date"]) is None
        or candidate["expiry_date"] < candidate["minimum_expiry_date"]
        or candidate["minimum_expiry_date"] <= candidate["h20_date"]
        or candidate.get("moneyness") not in {"ITM", "ATM", "OTM"}
        or any(
            type(candidate.get(key)) is not int or candidate[key] < 0
            for key in integer_fields
        )
        or any(
            candidate[key] > 1_000_000
            for key in (
                "long_coarse_delta_ppm",
                "long_fine_delta_ppm",
                "net_delta_ppm_per_strategy_unit",
            )
        )
        or candidate.get("sizing_loss_basis")
        != "STRESSED_ENTRY_DEBIT_PLUS_PLANNED_EXIT_FEES_EQUALS_PLANNED_MAX_LOSS"
        or candidate.get("profit_cap_mode")
        != (
            "CAPPED_AT_SHORT_STRIKE"
            if expected_carrier_id == "BCS0"
            else "UNBOUNDED_BY_STRUCTURE"
        )
        or candidate.get("profit_model_scope")
        != (
            "THEORETICAL_EXPIRY_UNDER_SIZING_ASSUMPTIONS"
            if expected_carrier_id == "BCS0"
            else "NOT_APPLICABLE_UNBOUNDED_STRUCTURE"
        )
        or not _is_sha256(candidate.get("selection_sha256"))
        or type(candidate.get("binding_constraints")) is not list
        or not candidate["binding_constraints"]
        or any(
            item
            not in {
                "DELTA_NOTIONAL",
                "KELLY_DRAWDOWN",
                "LIQUIDITY",
                "MAX_LOSS",
                "PLANNED_LOSS",
                "SETTLED_CASH",
            }
            for item in candidate["binding_constraints"]
        )
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    _validate_capacities(candidate.get("capacities"), allow_none=False)
    for key in ("short_coarse_delta_ppm", "short_fine_delta_ppm"):
        value = candidate.get(key)
        if expected_carrier_id == "LC0":
            if value is not None:
                raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
        elif type(value) is not int or not 0 <= value <= 1_000_000:
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    _validate_selector_policy(
        candidate.get("selector_policy"),
        rule_package=rule_package,
    )
    for key in (
        "gross_expiry_width_nano_usd_per_contract",
        "modeled_expiry_max_profit_nano_usd_per_strategy_unit",
    ):
        value = candidate.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    legs = candidate.get("legs")
    expected_sides = (
        ("BUY_TO_OPEN",)
        if expected_carrier_id == "LC0"
        else ("BUY_TO_OPEN", "SELL_TO_OPEN")
    )
    if type(legs) is not list or len(legs) != len(expected_sides):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    for leg, expected_side in zip(legs, expected_sides, strict=True):
        _validate_leg(
            leg,
            expected_side=expected_side,
            expected_expiry_date=candidate["expiry_date"],
        )
    if expected_carrier_id == "BCS0" and (
        legs[1]["expiry_utc_ns"] != legs[0]["expiry_utc_ns"]
        or legs[1]["strike_nano_usd"] <= legs[0]["strike_nano_usd"]
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    expected_long_moneyness = _moneyness(
        strike_nano_usd=legs[0]["strike_nano_usd"],
        underlying_mid_nano_usd=candidate["underlying_mid_nano_usd"],
    )
    if (
        candidate["moneyness"] != expected_long_moneyness
        or legs[0]["moneyness"] != expected_long_moneyness
        or candidate["long_coarse_delta_ppm"]
        != legs[0]["coarse_delta_ppm"]
        or candidate["long_fine_delta_ppm"]
        != legs[0]["fine_delta_ppm"]
        or legs[0]["delta_ppm"] != legs[0]["fine_delta_ppm"]
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    if expected_carrier_id == "LC0":
        if (
            candidate["net_delta_ppm_per_strategy_unit"]
            != candidate["long_fine_delta_ppm"]
        ):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    elif (
        candidate["short_coarse_delta_ppm"]
        != legs[1]["coarse_delta_ppm"]
        or candidate["short_fine_delta_ppm"]
        != legs[1]["fine_delta_ppm"]
        or legs[1]["delta_ppm"] != legs[1]["fine_delta_ppm"]
        or legs[1]["moneyness"]
        != _moneyness(
            strike_nano_usd=legs[1]["strike_nano_usd"],
            underlying_mid_nano_usd=candidate["underlying_mid_nano_usd"],
        )
        or candidate["net_delta_ppm_per_strategy_unit"]
        != candidate["long_fine_delta_ppm"]
        - candidate["short_fine_delta_ppm"]
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    _validate_economics(
        candidate.get("economics"),
        candidate=candidate,
        expected_carrier_id=expected_carrier_id,
    )
    if candidate.get("selection_sha256") != canonical_json_sha256(
        _candidate_selection_preimage(candidate)
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")


def _candidate_selection_preimage(
    candidate: dict[str, object],
) -> dict[str, object]:
    return {
        "carrier_id": candidate["carrier_id"],
        "h20_date": candidate["h20_date"],
        "legs": candidate["legs"],
        "long_coarse_delta_ppm": candidate["long_coarse_delta_ppm"],
        "long_fine_delta_ppm": candidate["long_fine_delta_ppm"],
        "minimum_expiry_date": candidate["minimum_expiry_date"],
        "selector_policy": candidate["selector_policy"],
        "short_coarse_delta_ppm": candidate["short_coarse_delta_ppm"],
        "short_fine_delta_ppm": candidate["short_fine_delta_ppm"],
        "underlying_mid_nano_usd": candidate["underlying_mid_nano_usd"],
    }


def _validate_selector_policy(
    selector_policy: object,
    *,
    rule_package: dict[str, object],
) -> None:
    if type(selector_policy) is not dict or set(
        selector_policy
    ) != _SELECTOR_POLICY_KEYS:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    lc0 = selector_policy.get("lc0")
    bcs = selector_policy.get("bcs")
    risk_rule = rule_package.get("risk")
    if (
        type(lc0) is not dict
        or set(lc0) != _LC0_SELECTOR_KEYS
        or type(bcs) is not dict
        or set(bcs) != _BCS_SELECTOR_KEYS
        or type(risk_rule) is not dict
        or lc0 != rule_package.get("lc0")
        or bcs != rule_package.get("bcs")
        or selector_policy.get("candidate_terminal_policy")
        != risk_rule.get("candidate_terminal_policy")
        or selector_policy.get("candidate_terminal_policy")
        != "BOTH_LC0_AND_BCS_MUST_PASS"
        or selector_policy.get("coarse_fine_agreement_required") is not True
        or selector_policy.get("second_best_fallback_allowed") is not False
        or lc0.get("coarse_fine_agreement_required") is not True
        or lc0.get("second_best_fallback_allowed") is not False
        or bcs.get("second_best_fallback_allowed") is not False
        or bcs.get("same_expiry_required") is not True
        or bcs.get("roll_active") is not False
        or bcs.get("leg_ratio") != "1:1"
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")


def _validate_candidate_risk_bindings(
    candidates: list[object],
    *,
    risk_context: object,
) -> None:
    if type(risk_context) is not dict:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    for candidate, sizing_key in zip(
        candidates,
        ("lc0_sizing", "bcs_sizing"),
        strict=True,
    ):
        if type(candidate) is not dict:
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
        sizing = risk_context.get(sizing_key)
        economics = candidate.get("economics")
        if (
            type(sizing) is not dict
            or type(economics) is not dict
            or candidate.get("quantity") != sizing.get("quantity")
            or candidate.get("capacities") != sizing.get("capacities")
            or candidate.get("binding_constraints")
            != sizing.get("binding_constraints")
            or candidate.get("delta_notional_nano_usd_per_contract")
            != sizing.get("per_contract_delta_notional_nano_usd")
            or economics.get("sizing_max_loss_nano_usd_per_contract")
            != sizing.get("per_contract_max_loss_nano_usd")
            or economics.get("sizing_planned_loss_nano_usd_per_contract")
            != sizing.get("per_contract_max_loss_nano_usd")
        ):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")


def _validate_candidate_raw_bindings(
    candidates: list[object],
    *,
    bundle: RawBundle,
) -> None:
    try:
        contract_document = bundle.read_json("option_contracts")
        quote_document = bundle.read_json("market_quotes")
    except (AttributeError, RawBundleError) as exc:
        raise SimulationPipelineError(
            "SIM_DECISION_RAW_SELECTION_BINDING_INVALID"
        ) from exc
    if type(contract_document) is not dict or type(quote_document) is not dict:
        raise SimulationPipelineError(
            "SIM_DECISION_RAW_SELECTION_BINDING_INVALID"
        )
    raw_contracts = contract_document.get("contracts")
    raw_quotes = quote_document.get("option_bbo")
    underlying = quote_document.get("underlying_bbo")
    if (
        type(raw_contracts) is not list
        or type(raw_quotes) is not list
        or type(underlying) is not dict
    ):
        raise SimulationPipelineError(
            "SIM_DECISION_RAW_SELECTION_BINDING_INVALID"
        )
    contracts: dict[str, dict[str, object]] = {}
    quotes: dict[str, dict[str, object]] = {}
    for item in raw_contracts:
        symbol = item.get("occ_symbol") if type(item) is dict else None
        if type(symbol) is not str or symbol in contracts:
            raise SimulationPipelineError(
                "SIM_DECISION_RAW_SELECTION_BINDING_INVALID"
            )
        contracts[symbol] = item
    for item in raw_quotes:
        symbol = item.get("occ_symbol") if type(item) is dict else None
        top = item.get("top_of_book") if type(item) is dict else None
        if type(symbol) is not str or type(top) is not dict or symbol in quotes:
            raise SimulationPipelineError(
                "SIM_DECISION_RAW_SELECTION_BINDING_INVALID"
            )
        quotes[symbol] = top
    bid = underlying.get("bid_nano_usd")
    ask = underlying.get("ask_nano_usd")
    if (
        type(bid) is not int
        or type(ask) is not int
        or bid <= 0
        or ask < bid
        or (bid + ask) % 2
    ):
        raise SimulationPipelineError(
            "SIM_DECISION_RAW_SELECTION_BINDING_INVALID"
        )
    underlying_mid = (bid + ask) // 2
    for candidate in candidates:
        if (
            type(candidate) is not dict
            or candidate.get("underlying_mid_nano_usd") != underlying_mid
            or type(candidate.get("legs")) is not list
        ):
            raise SimulationPipelineError(
                "SIM_DECISION_RAW_SELECTION_BINDING_INVALID"
            )
        for leg in candidate["legs"]:
            if type(leg) is not dict:
                raise SimulationPipelineError(
                    "SIM_DECISION_RAW_SELECTION_BINDING_INVALID"
                )
            symbol = leg.get("contract_id")
            contract = contracts.get(symbol) if type(symbol) is str else None
            top = quotes.get(symbol) if type(symbol) is str else None
            if type(contract) is not dict or type(top) is not dict:
                raise SimulationPipelineError(
                    "SIM_DECISION_RAW_SELECTION_BINDING_INVALID"
                )
            expiry_ns = contract.get("expiry_utc_ns")
            if type(expiry_ns) is not int or expiry_ns < 0:
                raise SimulationPipelineError(
                    "SIM_DECISION_RAW_SELECTION_BINDING_INVALID"
                )
            expiry_date = datetime.fromtimestamp(
                expiry_ns // 1_000_000_000,
                tz=timezone.utc,
            ).astimezone(_NEW_YORK).date().isoformat()
            if (
                leg.get("expiry_utc_ns") != expiry_ns
                or leg.get("expiry_date") != expiry_date
                or leg.get("strike_nano_usd")
                != contract.get("strike_nano_usd")
                or leg.get("bid_nano_usd") != top.get("bid_nano_usd")
                or leg.get("ask_nano_usd") != top.get("ask_nano_usd")
                or leg.get("bid_size") != top.get("bid_size")
                or leg.get("ask_size") != top.get("ask_size")
            ):
                raise SimulationPipelineError(
                    "SIM_DECISION_RAW_SELECTION_BINDING_INVALID"
                )
    if (
        len(candidates) != 2
        or type(candidates[0]) is not dict
        or type(candidates[1]) is not dict
        or candidates[0]["legs"][0] != candidates[1]["legs"][0]
    ):
        raise SimulationPipelineError(
            "SIM_DECISION_RAW_SELECTION_BINDING_INVALID"
        )
def _moneyness(
    *,
    strike_nano_usd: int,
    underlying_mid_nano_usd: int,
) -> str:
    if strike_nano_usd < underlying_mid_nano_usd:
        return "ITM"
    if strike_nano_usd == underlying_mid_nano_usd:
        return "ATM"
    return "OTM"


def _validate_economics(
    economics: object,
    *,
    candidate: dict[str, object],
    expected_carrier_id: str,
) -> None:
    if type(economics) is not dict or set(economics) != _ECONOMICS_KEYS:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    execution = economics.get("execution_assumptions")
    fees = economics.get("fee_components_nano_usd_per_contract")
    if (
        economics.get("carrier_id") != expected_carrier_id
        or type(execution) is not dict
        or set(execution) != _EXECUTION_ASSUMPTION_KEYS
        or execution.get("include_exit_fees_in_max_loss") is not True
        or type(execution.get("long_entry_adverse_ticks")) is not int
        or execution["long_entry_adverse_ticks"] < 0
        or type(execution.get("short_entry_adverse_ticks")) is not int
        or execution["short_entry_adverse_ticks"] < 0
        or type(fees) is not dict
        or set(fees) != _FEE_COMPONENT_KEYS
        or any(type(value) is not int or value < 0 for value in fees.values())
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    positive_fields = (
        "base_entry_cost_nano_usd_per_contract",
        "delta_notional_nano_usd_per_contract",
        "liquidity_capacity",
        "sizing_max_loss_nano_usd_per_contract",
        "sizing_planned_loss_nano_usd_per_contract",
        "stressed_entry_cost_nano_usd_per_contract",
    )
    if any(
        type(economics.get(key)) is not int or economics[key] <= 0
        for key in positive_fields
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    planned_exit_fees = economics.get(
        "planned_exit_fees_nano_usd_per_contract"
    )
    if type(planned_exit_fees) is not int or planned_exit_fees < 0:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    for key in (
        "gross_expiry_width_nano_usd_per_contract",
        "modeled_expiry_max_profit_nano_usd_per_strategy_unit",
    ):
        value = economics.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    quantity = candidate["quantity"]
    if (
        economics["sizing_max_loss_nano_usd_per_contract"]
        != economics["sizing_planned_loss_nano_usd_per_contract"]
        or economics["sizing_max_loss_nano_usd_per_contract"]
        != economics["stressed_entry_cost_nano_usd_per_contract"]
        + planned_exit_fees
        or candidate["max_loss_nano_usd"]
        != economics["sizing_max_loss_nano_usd_per_contract"] * quantity
        or candidate["total_entry_cost_nano_usd"]
        != economics["base_entry_cost_nano_usd_per_contract"] * quantity
        or candidate["base_entry_cost_nano_usd_per_contract"]
        != economics["base_entry_cost_nano_usd_per_contract"]
        or candidate["stressed_entry_cost_nano_usd_per_contract"]
        != economics["stressed_entry_cost_nano_usd_per_contract"]
        or candidate["delta_notional_nano_usd_per_contract"]
        != economics["delta_notional_nano_usd_per_contract"]
        or candidate["gross_expiry_width_nano_usd_per_contract"]
        != economics["gross_expiry_width_nano_usd_per_contract"]
        or candidate[
            "modeled_expiry_max_profit_nano_usd_per_strategy_unit"
        ]
        != economics[
            "modeled_expiry_max_profit_nano_usd_per_strategy_unit"
        ]
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")


def _validate_leg(
    leg: object,
    *,
    expected_side: str,
    expected_expiry_date: str,
) -> None:
    if type(leg) is not dict or set(leg) != _LEG_KEYS:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    if (
        leg.get("side") != expected_side
        or leg.get("ratio") != 1
        or type(leg.get("contract_id")) is not str
        or not leg["contract_id"].isascii()
        or not leg["contract_id"]
        or leg.get("expiry_date") != expected_expiry_date
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    for key in (
        "expiry_utc_ns",
        "strike_nano_usd",
        "bid_nano_usd",
        "ask_nano_usd",
        "bid_size",
        "ask_size",
        "coarse_delta_ppm",
        "delta_ppm",
        "fine_delta_ppm",
    ):
        value = leg.get(key)
        if type(value) is not int or value < 0:
            raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    if leg["ask_nano_usd"] < leg["bid_nano_usd"]:
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")
    if (
        leg.get("moneyness") not in {"ITM", "ATM", "OTM"}
        or any(
            leg[key] > 1_000_000
            for key in (
                "coarse_delta_ppm",
                "delta_ppm",
                "fine_delta_ppm",
            )
        )
    ):
        raise SimulationPipelineError("SIM_DECISION_ARTIFACT_INVALID")


def _validate_child_semantic_receipt(
    semantic_receipt: object,
    *,
    decision: dict[str, object],
    bundle: RawBundle,
    child_artifact_sha256: str | None,
) -> dict[str, object]:
    # Source, CRR terminal, selection, economics, and sizing facts are replayed
    # by the parent before this receipt is cross-bound to the canonical artifact.
    if type(semantic_receipt) is not dict:
        raise SimulationPipelineError("SIM_CHILD_SEMANTIC_RECEIPT_INVALID")
    _reject_authority_fields(semantic_receipt)
    gate = decision.get("gate_a")
    crr = decision.get("crr")
    expected_gate_hash = (
        gate.get("input_fact_sha256") if type(gate) is dict else None
    )
    expected_gate_state = gate.get("state") if type(gate) is dict else None
    expected_crr_hash = (
        crr.get("semantic_output_sha256") if type(crr) is dict else None
    )
    decision_semantic_sha256 = canonical_json_sha256(decision)
    if (
        set(semantic_receipt) != _PIPELINE_RECEIPT_KEYS
        or semantic_receipt.get("schema_version")
        != PIPELINE_RECEIPT_SCHEMA_VERSION
        or semantic_receipt.get("classification")
        != SIMULATION_CLASSIFICATION
        or semantic_receipt.get("decision_semantic_sha256")
        != decision_semantic_sha256
        or child_artifact_sha256 != decision_semantic_sha256
        or semantic_receipt.get("raw_content_sha256")
        != bundle.raw_content_sha256
        or semantic_receipt.get("raw_content_sha256")
        != decision.get("raw_content_sha256")
        or semantic_receipt.get("manifest_sha256")
        != bundle.manifest_sha256
        or semantic_receipt.get("manifest_sha256")
        != decision.get("manifest_sha256")
        or semantic_receipt.get("rule_sha256")
        != decision.get("rule_sha256")
        or semantic_receipt.get("gate_input_fact_sha256")
        != expected_gate_hash
        or semantic_receipt.get("gate_state") != expected_gate_state
        or semantic_receipt.get("crr_semantic_output_sha256")
        != expected_crr_hash
        or semantic_receipt.get("status") != decision.get("status")
        or semantic_receipt.get("actionable") is not False
        or type(semantic_receipt.get("broker_order_count")) is not int
        or semantic_receipt.get("broker_order_count") != 0
    ):
        raise SimulationPipelineError(
            "SIM_CHILD_SEMANTIC_RECEIPT_INVALID"
        )
    for key in (
        "decision_semantic_sha256",
        "raw_content_sha256",
        "manifest_sha256",
        "rule_sha256",
    ):
        if not _is_sha256(semantic_receipt.get(key)):
            raise SimulationPipelineError(
                "SIM_CHILD_SEMANTIC_RECEIPT_INVALID"
            )
    for key in ("gate_input_fact_sha256", "crr_semantic_output_sha256"):
        value = semantic_receipt.get(key)
        if value is not None and not _is_sha256(value):
            raise SimulationPipelineError(
                "SIM_CHILD_SEMANTIC_RECEIPT_INVALID"
            )
    return semantic_receipt


def _supervisor_receipt_document(
    receipt: P1ProcessSupervisorReceiptV1,
) -> dict[str, object]:
    return {field.name: getattr(receipt, field.name) for field in fields(type(receipt))}


def _validate_workflow_finalization(
    finalization: object,
    *,
    supervisor_result: P1ProcessSupervisorResultV1,
) -> dict[str, object]:
    try:
        started = getattr(finalization, "workflow_started_monotonic_ns")
        deadline = getattr(finalization, "deadline_monotonic_ns")
        completed = getattr(finalization, "completed_monotonic_ns")
        before_deadline = getattr(finalization, "before_deadline")
    except AttributeError as exc:
        raise SimulationPipelineError(
            "SIM_SUPERVISOR_FINALIZE_FAILED"
        ) from exc
    receipt = supervisor_result.supervisor_receipt
    if (
        type(started) is not int
        or type(deadline) is not int
        or type(completed) is not int
        or type(before_deadline) is not bool
        or started < 0
        or deadline != started + P1_PROCESS_TIMEOUT_NS
        or completed < started
        or before_deadline != (completed < deadline)
        or started != receipt.supervisor_started_monotonic_ns
        or deadline != receipt.deadline_monotonic_ns
        or completed < receipt.completed_monotonic_ns
    ):
        raise SimulationPipelineError("SIM_SUPERVISOR_FINALIZE_FAILED")
    return {
        "workflow_started_monotonic_ns": started,
        "deadline_monotonic_ns": deadline,
        "completed_monotonic_ns": completed,
        "before_deadline": before_deadline,
    }


def _publish_artifacts(
    *,
    output_dir: Path,
    decision: dict[str, object],
    child_semantic_receipt: dict[str, object] | None,
    child_artifact_sha256: str | None,
    supervisor_result: P1ProcessSupervisorResultV1,
    workflow_status: str,
    workflow_finalization: dict[str, object],
    success_deadline_monotonic_ns: int | None = None,
) -> tuple[Path, Path, Path, str]:
    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    if output_dir.exists() or output_dir.is_symlink():
        raise SimulationPipelineError("SIM_OUTPUT_ALREADY_EXISTS")
    temporary = Path(tempfile.mkdtemp(prefix=".gld-sim-", dir=parent))
    try:
        decision_bytes = canonical_json_bytes(decision) + b"\n"
        decision_sha256 = sha256(decision_bytes).hexdigest()
        card_bytes = render_decision_card(
            decision,
            decision_result_sha256=decision_sha256,
        )
        card_sha256 = sha256(card_bytes).hexdigest()
        receipt_document = {
            "schema_version": ARTIFACT_RECEIPT_SCHEMA_VERSION,
            "classification": SIMULATION_CLASSIFICATION,
            "bundle_id": decision.get("bundle_id"),
            "manifest_sha256": decision.get("manifest_sha256"),
            "raw_content_sha256": decision.get("raw_content_sha256"),
            "rule_sha256": decision.get("rule_sha256"),
            "decision_result_sha256": decision_sha256,
            "decision_card_sha256": card_sha256,
            "child_artifact_sha256": child_artifact_sha256,
            "child_semantic_receipt": child_semantic_receipt,
            "crr_semantic_trust_boundary": (
                "PARENT_RECOMPUTED_HASHES_AND_RAW_INPUT_BINDINGS_"
                "CRR_NUMERICS_SUPERVISED_CHILD_ATTESTED"
            ),
            "workflow_status": workflow_status,
            "workflow_finalization": workflow_finalization,
            "supervisor_status": supervisor_result.status,
            "supervisor_reason_code": supervisor_result.reason_code,
            "supervisor_failure_origin": supervisor_result.failure_origin,
            "supervisor_receipt": _supervisor_receipt_document(
                supervisor_result.supervisor_receipt
            ),
            "actionable": False,
            "broker_order_count": 0,
        }
        receipt_bytes = canonical_json_bytes(receipt_document) + b"\n"
        decision_path = temporary / "decision-result.json"
        receipt_path = temporary / "decision-receipt.json"
        card_path = temporary / "decision-card.html"
        decision_path.write_bytes(decision_bytes)
        receipt_path.write_bytes(receipt_bytes)
        card_path.write_bytes(card_bytes)
        if (
            success_deadline_monotonic_ns is not None
            and time.monotonic_ns() >= success_deadline_monotonic_ns
        ):
            raise SimulationPipelineError(
                "SIM_ARTIFACT_DEADLINE_EXCEEDED"
            )
        os.replace(temporary, output_dir)
        return (
            output_dir / decision_path.name,
            output_dir / receipt_path.name,
            output_dir / card_path.name,
            decision_sha256,
        )
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def run_simulation_workflow(
    *,
    bundle_root: Path,
    native_manifest_path: Path,
    expected_backend_evidence_sha256: str,
    output_dir: Path,
) -> SimulationWorkflowResultV1:
    """Run one raw synthetic bundle under the fixed five-second supervisor."""

    ticket = _BEGIN_WORKFLOW()
    bundle = load_raw_bundle(bundle_root)
    trusted_native_manifest = _trusted_native_manifest_path(
        native_manifest_path
    )
    expected_combined_backend_evidence_sha256 = (
        derive_native_crr_combined_backend_evidence_sha256(
            trusted_native_manifest,
            expected_native_build_backend_evidence_sha256=(
                expected_backend_evidence_sha256
            ),
        )
    )
    request_document = _request_document(
        bundle,
        native_manifest_path=trusted_native_manifest,
        expected_backend_evidence_sha256=expected_backend_evidence_sha256,
    )
    request_bytes = canonical_json_bytes(request_document)
    request_sha256 = sha256(request_bytes).hexdigest()
    issued = _RUN_ISSUED(ticket, request_bytes, request_sha256)
    if issued is None:
        raise SimulationPipelineError("SIM_SUPERVISOR_ISSUE_FAILED")
    supervisor_result = _CONSUME_ISSUED(
        ticket,
        issued,
        request_sha256,
    )
    if supervisor_result is None:
        raise SimulationPipelineError("SIM_SUPERVISOR_CONSUME_FAILED")
    finalization = _FINALIZE_WORKFLOW(ticket, request_sha256)
    if finalization is None:
        raise SimulationPipelineError("SIM_SUPERVISOR_FINALIZE_FAILED")

    if (
        supervisor_result.supervisor_receipt.request_sha256
        != request_sha256
    ):
        raise SimulationPipelineError("SIM_SUPERVISOR_RESULT_INVALID")
    workflow_finalization = _validate_workflow_finalization(
        finalization,
        supervisor_result=supervisor_result,
    )
    before_deadline = workflow_finalization["before_deadline"] is True

    semantic_receipt: dict[str, object] | None = None
    child_artifact_sha256: str | None = None
    if supervisor_result.status == "SUCCESS" and before_deadline:
        assert supervisor_result.artifact_bytes is not None
        decision = _validate_decision_document(
            _parse_canonical_bytes(supervisor_result.artifact_bytes),
            bundle=bundle,
            expected_backend_evidence_sha256=(
                expected_backend_evidence_sha256
            ),
            expected_combined_backend_evidence_sha256=(
                expected_combined_backend_evidence_sha256
            ),
        )
        assert supervisor_result.semantic_receipt_bytes is not None
        semantic_receipt = _validate_child_semantic_receipt(
            _parse_canonical_bytes(
                supervisor_result.semantic_receipt_bytes
            ),
            decision=decision,
            bundle=bundle,
            child_artifact_sha256=supervisor_result.artifact_sha256,
        )
        child_artifact_sha256 = supervisor_result.artifact_sha256
        workflow_status = "SUCCESS"
    else:
        reason_code = (
            "SIM_SUPERVISOR_FINALIZATION_DEADLINE_EXCEEDED"
            if not before_deadline
            else (
                supervisor_result.child_reason_code
                or supervisor_result.reason_code
            )
        )
        decision = _validate_decision_document(
            minimal_no_decision_document(
                raw_documents=bundle.as_document(),
                bundle_id=bundle.bundle_id,
                manifest_sha256=bundle.manifest_sha256,
                raw_content_sha256=bundle.raw_content_sha256,
                reason_code=reason_code,
            ),
            bundle=bundle,
            expected_backend_evidence_sha256=(
                expected_backend_evidence_sha256
            ),
            expected_combined_backend_evidence_sha256=(
                expected_combined_backend_evidence_sha256
            ),
        )
        workflow_status = "FAIL_CLOSED"
    try:
        decision_path, receipt_path, card_path, decision_sha256 = (
            _publish_artifacts(
                output_dir=output_dir,
                decision=decision,
                child_semantic_receipt=semantic_receipt,
                child_artifact_sha256=child_artifact_sha256,
                supervisor_result=supervisor_result,
                workflow_status=workflow_status,
                workflow_finalization=workflow_finalization,
                success_deadline_monotonic_ns=(
                    workflow_finalization["deadline_monotonic_ns"]
                    if workflow_status == "SUCCESS"
                    else None
                ),
            )
        )
    except SimulationPipelineError as exc:
        if (
            workflow_status != "SUCCESS"
            or exc.reason_code != "SIM_ARTIFACT_DEADLINE_EXCEEDED"
        ):
            raise
        decision = _validate_decision_document(
            minimal_no_decision_document(
                raw_documents=bundle.as_document(),
                bundle_id=bundle.bundle_id,
                manifest_sha256=bundle.manifest_sha256,
                raw_content_sha256=bundle.raw_content_sha256,
                reason_code=(
                    "SIM_SUPERVISOR_FINALIZATION_DEADLINE_EXCEEDED"
                ),
            ),
            bundle=bundle,
            expected_backend_evidence_sha256=(
                expected_backend_evidence_sha256
            ),
            expected_combined_backend_evidence_sha256=(
                expected_combined_backend_evidence_sha256
            ),
        )
        semantic_receipt = None
        child_artifact_sha256 = None
        workflow_status = "FAIL_CLOSED"
        decision_path, receipt_path, card_path, decision_sha256 = (
            _publish_artifacts(
                output_dir=output_dir,
                decision=decision,
                child_semantic_receipt=None,
                child_artifact_sha256=None,
                supervisor_result=supervisor_result,
                workflow_status=workflow_status,
                workflow_finalization={
                    **workflow_finalization,
                    "artifact_completed_before_deadline": False,
                },
            )
        )
    return SimulationWorkflowResultV1(
        status=workflow_status,
        decision_document=decision,
        decision_result_sha256=decision_sha256,
        decision_result_path=decision_path,
        decision_receipt_path=receipt_path,
        decision_card_path=card_path,
        supervisor_receipt=supervisor_result.supervisor_receipt,
    )


__all__ = [
    "ARTIFACT_RECEIPT_SCHEMA_VERSION",
    "SUPERVISOR_REQUEST_SCHEMA_VERSION",
    "SimulationWorkflowResultV1",
    "run_simulation_workflow",
]
