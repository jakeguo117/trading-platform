"""Deterministic research-only GLD Entry f F0 decision kernel."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
import json
from typing import Iterable

from .canonical import canonical_json_bytes, canonical_json_sha256
from .contracts import (
    PPM,
    Bcs0EconomicsF0,
    CallCandidateF0,
    EntryDecisionInputF0,
    ExitPolicyBindingInputF0,
    Lc0EconomicsF0,
    StructureEvidenceSummaryF0,
    validate_entry_decision_input_f0,
)
from .errors import EntryDecisionF0Error
from .policy import EntryPolicyF0, validate_entry_policy_f0


DECISION_RESULT_SCHEMA_VERSION = "ENTRY_DECISION_RESULT_F0_V1"
DECISION_TRACE_SCHEMA_VERSION = "ENTRY_DECISION_TRACE_F0_V1"
AUTHORITY_STATUS = "NO_DECISION_EFFECT"
CLASSIFICATION = "RESEARCH_ONLY"
TRACE_STAGE_IDS = (
    "INPUT_QUALIFICATION",
    "ENTRY_GATE",
    "LC0_EVALUATION",
    "BCS0_EVALUATION",
    "SIZING",
    "PREFERENCE",
    "EXIT_POLICY_BINDING",
    "FINAL_DECISION",
)
TRACE_STATES = frozenset({"PASS", "FAIL", "ELIMINATED", "BLOCKED", "NOT_RUN"})
FINAL_STATUSES = frozenset(
    {"NO_DECISION", "NO_ACTION", "SINGLE_PLAN", "PREFERRED_AND_BACKUP"}
)
EXIT_TRIGGER_OBSERVATION_SCHEMA_VERSION = "ENTRY_EXIT_TRIGGER_OBSERVATION_F0_V1"
EXIT_TRIGGER_RESULT_SCHEMA_VERSION = "ENTRY_EXIT_TRIGGER_RESULT_F0_V1"
_EXIT_OBSERVATION_KEYS = frozenset(
    {
        "schema_version",
        "carrier_id",
        "already_latched",
        "observation_utc_ns",
        "max_quote_age_ns",
        "max_cross_leg_receive_skew_ns",
        "max_leg_spread_ppm",
        "entry_after_cost_basis_nano_usd",
        "planned_exit_fees_nano_usd",
        "multiplier",
        "hard_stop_loss_ppm",
        "long_bid_nano_usd",
        "long_ask_nano_usd",
        "long_bid_size",
        "long_ask_size",
        "long_quote_event_utc_ns",
        "long_quote_receive_utc_ns",
        "short_bid_nano_usd",
        "short_ask_nano_usd",
        "short_bid_size",
        "short_ask_size",
        "short_quote_event_utc_ns",
        "short_quote_receive_utc_ns",
        "previous_trend_pass_count",
        "current_trend_pass_count",
        "previous_session_complete",
        "current_session_complete",
    }
)


def _detached(value: object) -> object:
    return json.loads(canonical_json_bytes(value))


@dataclass(frozen=True, slots=True)
class DecisionTraceStageF0:
    stage_id: str
    stage_order: int
    state: str
    fact_references: tuple[str, ...]
    rule_id: str
    rule_version: str
    calculation: dict[str, object]
    reason_code: str
    recovery_code: str
    stage_sha256: str

    @classmethod
    def create(
        cls,
        *,
        stage_id: str,
        state: str,
        fact_references: Iterable[str],
        rule_id: str,
        rule_version: str,
        calculation: dict[str, object],
        reason_code: str,
        recovery_code: str,
    ) -> "DecisionTraceStageF0":
        if stage_id not in TRACE_STAGE_IDS or state not in TRACE_STATES:
            raise EntryDecisionF0Error("DECISION_TRACE_STAGE_INVALID")
        order = TRACE_STAGE_IDS.index(stage_id) + 1
        body = {
            "stage_id": stage_id,
            "stage_order": order,
            "state": state,
            "fact_references": sorted(set(fact_references)),
            "rule_id": rule_id,
            "rule_version": rule_version,
            "calculation": calculation,
            "reason_code": reason_code,
            "recovery_code": recovery_code,
        }
        return cls(
            stage_id=stage_id,
            stage_order=order,
            state=state,
            fact_references=tuple(body["fact_references"]),
            rule_id=rule_id,
            rule_version=rule_version,
            calculation=_detached(calculation),  # type: ignore[arg-type]
            reason_code=reason_code,
            recovery_code=recovery_code,
            stage_sha256=canonical_json_sha256(body),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "stage_id": self.stage_id,
            "stage_order": self.stage_order,
            "state": self.state,
            "fact_references": list(self.fact_references),
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "calculation": _detached(self.calculation),
            "reason_code": self.reason_code,
            "recovery_code": self.recovery_code,
            "stage_sha256": self.stage_sha256,
        }


@dataclass(frozen=True, slots=True)
class DecisionTraceF0:
    stages: tuple[DecisionTraceStageF0, ...]
    trace_sha256: str

    @classmethod
    def create(cls, stages: Iterable[DecisionTraceStageF0]) -> "DecisionTraceF0":
        normalized = tuple(stages)
        if (
            len(normalized) != len(TRACE_STAGE_IDS)
            or tuple(item.stage_id for item in normalized) != TRACE_STAGE_IDS
            or tuple(item.stage_order for item in normalized) != tuple(range(1, 9))
        ):
            raise EntryDecisionF0Error("DECISION_TRACE_SEQUENCE_INVALID")
        body = {
            "schema_version": DECISION_TRACE_SCHEMA_VERSION,
            "stages": [item.as_dict() for item in normalized],
        }
        return cls(stages=normalized, trace_sha256=canonical_json_sha256(body))

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": DECISION_TRACE_SCHEMA_VERSION,
            "stages": [item.as_dict() for item in self.stages],
            "trace_sha256": self.trace_sha256,
        }


@dataclass(frozen=True, slots=True)
class EntryPlanF0:
    carrier_id: str
    contract_ids: tuple[str, ...]
    expiry_date: str
    safe_quantity: int
    current_entry_debit_per_unit_nano_usd: int
    stressed_entry_debit_per_unit_nano_usd: int
    planned_loss_per_unit_nano_usd: int
    delta_notional_per_unit_nano_usd: int
    expected_net_return_lower_bound_ppm: int
    expected_total_net_profit_nano_usd: int
    comparison_numerator: int
    sizing_caps: dict[str, int]
    binding_caps: tuple[str, ...]
    management_policy_id: str
    management_policy_sha256: str
    h20_date: str
    h20_exit_utc_ns: int
    expiry_safety_calendar_days: int
    invalidation_confirmation_sessions: int
    hard_stop_policy_id: str
    hard_stop_policy_sha256: str
    hard_stop_loss_ppm: int
    authority_status: str = AUTHORITY_STATUS

    def as_dict(self) -> dict[str, object]:
        return {
            "carrier_id": self.carrier_id,
            "contract_ids": list(self.contract_ids),
            "expiry_date": self.expiry_date,
            "safe_quantity": self.safe_quantity,
            "current_entry_debit_per_unit_nano_usd": self.current_entry_debit_per_unit_nano_usd,
            "stressed_entry_debit_per_unit_nano_usd": self.stressed_entry_debit_per_unit_nano_usd,
            "planned_loss_per_unit_nano_usd": self.planned_loss_per_unit_nano_usd,
            "delta_notional_per_unit_nano_usd": self.delta_notional_per_unit_nano_usd,
            "expected_net_return_lower_bound_ppm": self.expected_net_return_lower_bound_ppm,
            "expected_total_net_profit_nano_usd": self.expected_total_net_profit_nano_usd,
            "comparison_numerator": self.comparison_numerator,
            "sizing_caps": dict(sorted(self.sizing_caps.items())),
            "binding_caps": list(self.binding_caps),
            "management_policy_id": self.management_policy_id,
            "management_policy_sha256": self.management_policy_sha256,
            "h20_date": self.h20_date,
            "h20_exit_utc_ns": self.h20_exit_utc_ns,
            "expiry_safety_calendar_days": self.expiry_safety_calendar_days,
            "invalidation_confirmation_sessions": self.invalidation_confirmation_sessions,
            "hard_stop_policy_id": self.hard_stop_policy_id,
            "hard_stop_policy_sha256": self.hard_stop_policy_sha256,
            "hard_stop_loss_ppm": self.hard_stop_loss_ppm,
            "authority_status": self.authority_status,
        }


@dataclass(frozen=True, slots=True)
class EntryDecisionResultF0:
    schema_version: str
    classification: str
    authority_status: str
    actionable: bool
    broker_order_count: int
    decision_status: str
    reason_code: str
    input_sha256: str
    policy_id: str
    policy_sha256: str
    preferred_plan: EntryPlanF0 | None
    backup_plan: EntryPlanF0 | None
    trace: DecisionTraceF0
    result_sha256: str
    canonical_bytes: bytes

    @property
    def final_status(self) -> str:
        return self.decision_status

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "authority_status": self.authority_status,
            "actionable": self.actionable,
            "broker_order_count": self.broker_order_count,
            "decision_status": self.decision_status,
            "reason_code": self.reason_code,
            "input_sha256": self.input_sha256,
            "policy_id": self.policy_id,
            "policy_sha256": self.policy_sha256,
            "plans": {
                "preferred": None if self.preferred_plan is None else self.preferred_plan.as_dict(),
                "backup": None if self.backup_plan is None else self.backup_plan.as_dict(),
            },
            "decision_trace": self.trace.as_dict(),
            "result_sha256": self.result_sha256,
        }


@dataclass(frozen=True, slots=True)
class _ExitTriggerFixtureResultF0:
    schema_version: str
    classification: str
    authority_status: str
    actionable: bool
    broker_order_count: int
    carrier_id: str
    exit_state: str
    trigger_reason: str
    hard_stop_evaluable: bool
    calculation: dict[str, object]
    result_sha256: str
    canonical_bytes: bytes

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "authority_status": self.authority_status,
            "actionable": self.actionable,
            "broker_order_count": self.broker_order_count,
            "carrier_id": self.carrier_id,
            "exit_state": self.exit_state,
            "trigger_reason": self.trigger_reason,
            "hard_stop_evaluable": self.hard_stop_evaluable,
            "calculation": _detached(self.calculation),
            "result_sha256": self.result_sha256,
        }


@dataclass(frozen=True, slots=True)
class _SelectedStructure:
    carrier_id: str
    contracts: tuple[CallCandidateF0, ...]
    economics: Lc0EconomicsF0 | Bcs0EconomicsF0
    evidence: StructureEvidenceSummaryF0


def _stage(
    stage_id: str,
    state: str,
    reason: str,
    recovery: str,
    calculation: dict[str, object] | None = None,
    refs: Iterable[str] = (),
) -> DecisionTraceStageF0:
    return DecisionTraceStageF0.create(
        stage_id=stage_id,
        state=state,
        fact_references=refs,
        rule_id=f"GLD_ENTRY_{stage_id}_F0",
        rule_version="1.0.0",
        calculation={} if calculation is None else calculation,
        reason_code=reason,
        recovery_code=recovery,
    )


def _not_run(stage_id: str, blocker: str) -> DecisionTraceStageF0:
    return _stage(
        stage_id,
        "NOT_RUN",
        blocker,
        "RESOLVE_BLOCKING_STAGE_AND_RERUN",
        {"blocked_by_reason_code": blocker},
    )


def _finalize(
    source: EntryDecisionInputF0,
    policy: EntryPolicyF0,
    status: str,
    reason: str,
    stages: list[DecisionTraceStageF0],
    preferred: EntryPlanF0 | None = None,
    backup: EntryPlanF0 | None = None,
) -> EntryDecisionResultF0:
    if status not in FINAL_STATUSES or len(stages) != 8:
        raise EntryDecisionF0Error("DECISION_RESULT_STATE_INVALID")
    trace = DecisionTraceF0.create(stages)
    unsigned = {
        "schema_version": DECISION_RESULT_SCHEMA_VERSION,
        "classification": CLASSIFICATION,
        "authority_status": AUTHORITY_STATUS,
        "actionable": False,
        "broker_order_count": 0,
        "decision_status": status,
        "reason_code": reason,
        "input_sha256": source.input_sha256,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.policy_sha256,
        "plans": {
            "preferred": None if preferred is None else preferred.as_dict(),
            "backup": None if backup is None else backup.as_dict(),
        },
        "decision_trace": trace.as_dict(),
    }
    result_hash = canonical_json_sha256(unsigned)
    final = {**unsigned, "result_sha256": result_hash}
    return EntryDecisionResultF0(
        schema_version=DECISION_RESULT_SCHEMA_VERSION,
        classification=CLASSIFICATION,
        authority_status=AUTHORITY_STATUS,
        actionable=False,
        broker_order_count=0,
        decision_status=status,
        reason_code=reason,
        input_sha256=source.input_sha256,
        policy_id=policy.policy_id,
        policy_sha256=policy.policy_sha256,
        preferred_plan=preferred,
        backup_plan=backup,
        trace=trace,
        result_sha256=result_hash,
        canonical_bytes=canonical_json_bytes(final),
    )


def _blocked_result(
    source: EntryDecisionInputF0,
    policy: EntryPolicyF0,
    reason: str,
    recovery: str,
) -> EntryDecisionResultF0:
    stages = [
        _stage(
            "INPUT_QUALIFICATION",
            "BLOCKED",
            reason,
            recovery,
            {
                "data_qualification_status": source.data_qualification_status,
                "universe_complete": source.universe_complete,
                "open_gld_order_count": source.open_gld_order_count,
            },
            (
                source.entry_fact_bundle_sha256,
                source.technical_facts_sha256,
                source.atomic_snapshot_sha256,
            ),
        )
    ]
    stages.extend(_not_run(stage_id, reason) for stage_id in TRACE_STAGE_IDS[1:])
    return _finalize(source, policy, "NO_DECISION", reason, stages)


def _input_qualification_reason(
    source: EntryDecisionInputF0, policy: EntryPolicyF0
) -> tuple[str, str] | None:
    if source.data_qualification_status == "DATA_NOT_QUALIFIED":
        return "DATA_NOT_QUALIFIED", "QUALIFY_INPUT_DATA_AND_RERUN"
    if source.facts_max_event_utc_ns > source.decision_cutoff_utc_ns or any(
        call.quote_event_utc_ns > source.decision_cutoff_utc_ns
        or call.quote_receive_utc_ns > source.decision_cutoff_utc_ns
        for call in source.call_universe
    ):
        return "FUTURE_DATA_DETECTED", "REFRESH_CAUSAL_FACTS_AND_RERUN"
    if any(
        call.snapshot_sha256 != source.atomic_snapshot_sha256
        for call in source.call_universe
    ):
        return "OPTION_ATOMIC_SNAPSHOT_MISMATCH", "REFRESH_ONE_ATOMIC_SNAPSHOT"
    if any(
        call.technical_facts_sha256 != source.technical_facts_sha256
        for call in source.call_universe
    ):
        return (
            "OPTION_TECHNICAL_FACT_BINDING_MISMATCH",
            "REDERIVE_TECHNICAL_FACTS_AND_RERUN",
        )
    executable_calls = tuple(call for call in source.call_universe if call.executable)
    if executable_calls:
        receives = tuple(call.quote_receive_utc_ns for call in executable_calls)
        if max(receives) - min(receives) > 1_000_000_000:
            return (
                "OPTION_ATOMIC_SNAPSHOT_RECEIVE_SKEW_EXCEEDED",
                "REFRESH_SYNCHRONIZED_ATOMIC_SNAPSHOT",
            )
        if any(
            source.decision_cutoff_utc_ns - call.quote_event_utc_ns
            > 5_000_000_000
            or source.decision_cutoff_utc_ns - call.quote_receive_utc_ns
            > 5_000_000_000
            for call in executable_calls
        ):
            return "OPTION_QUOTE_STALE", "REFRESH_FRESH_ATOMIC_SNAPSHOT"
    if not source.universe_complete:
        return "CALL_UNIVERSE_INCOMPLETE", "REFRESH_COMPLETE_ATOMIC_UNIVERSE"
    if source.open_gld_order_count:
        return "UNRECONCILED_GLD_OPEN_ORDER", "RECONCILE_OPEN_ORDERS_AND_RERUN"
    if source.classification == "DATA_QUALIFIED":
        if source.data_qualification_status != "DATA_QUALIFIED":
            return "DATA_QUALIFICATION_CONFLICT", "REFRESH_QUALIFICATION_RECEIPT"
        if not policy.is_active:
            return "ACTIVE_ENTRY_POLICY_NOT_AVAILABLE", "OWNER_APPROVE_ACTIVE_ENTRY_POLICY"
    elif source.data_qualification_status != "STRUCTURALLY_VALID_SYNTHETIC":
        return "DATA_QUALIFICATION_CONFLICT", "USE_SYNTHETIC_QUALIFICATION_STATUS"
    if not policy.owner_approved:
        return "OWNER_APPROVED_POLICY_NOT_AVAILABLE", "OWNER_APPROVE_POLICY_AND_RERUN"
    return None


def _evaluate_gate(
    source: EntryDecisionInputF0, policy: EntryPolicyF0
) -> DecisionTraceStageF0:
    facts = source.entry_gate_facts
    scalar_values = (
        facts.prior_close_nano_usd,
        facts.sma50_nano_usd,
        facts.sma200_nano_usd,
        facts.sma50_slope_nano_usd_per_session,
        facts.prior_high20_nano_usd,
        facts.minute_1044_close_nano_usd,
    )
    if any(value is None for value in scalar_values) or any(
        value is None for value in facts.confirmation_closes_nano_usd
    ):
        return _stage(
            "ENTRY_GATE",
            "BLOCKED",
            "ENTRY_GATE_FACT_MISSING",
            "REFRESH_REQUIRED_GATE_FACTS",
            {"quota_bypass_forbidden": True},
            (source.technical_facts_sha256,),
        )
    assert facts.prior_close_nano_usd is not None
    assert facts.sma50_nano_usd is not None
    assert facts.sma200_nano_usd is not None
    assert facts.sma50_slope_nano_usd_per_session is not None
    assert facts.prior_high20_nano_usd is not None
    assert facts.minute_1044_close_nano_usd is not None
    closes = tuple(value for value in facts.confirmation_closes_nano_usd if value is not None)
    trend_checks = (
        facts.prior_close_nano_usd > facts.sma50_nano_usd,
        facts.sma50_nano_usd > facts.sma200_nano_usd,
        facts.sma50_slope_nano_usd_per_session > 0,
    )
    above_count = sum(value > facts.prior_high20_nano_usd for value in closes)
    breakout_checks = (
        facts.minute_1044_close_nano_usd > facts.prior_high20_nano_usd,
        above_count >= 10,
    )
    trend_pass_count = sum(trend_checks)
    breakout_pass_count = sum(breakout_checks)
    calculation = {
        "trend": {
            "prior_close_gt_sma50": trend_checks[0],
            "sma50_gt_sma200": trend_checks[1],
            "sma50_slope_gt_zero": trend_checks[2],
            "pass_count": trend_pass_count,
            "required_count": policy.trend_required_count,
        },
        "breakout": {
            "minute_1044_close_gt_prior_high20": breakout_checks[0],
            "confirmation_above_count": above_count,
            "confirmation_required_above_count": 10,
            "range_hold_met": breakout_checks[1],
            "pass_count": breakout_pass_count,
            "required_count": policy.breakout_required_count,
        },
        "groups_must_separately_pass": True,
    }
    passed = (
        trend_pass_count >= policy.trend_required_count
        and breakout_pass_count >= policy.breakout_required_count
    )
    return _stage(
        "ENTRY_GATE",
        "PASS" if passed else "FAIL",
        "ENTRY_GATE_PASS" if passed else "ENTRY_GATE_QUOTA_NOT_MET",
        "CONTINUE_STRUCTURE_EVALUATION" if passed else "WAIT_FOR_NEW_COMPLETE_GATE_OBSERVATION",
        calculation,
        (source.technical_facts_sha256,),
    )


def _evidence_reason(
    evidence: StructureEvidenceSummaryF0 | None,
    *,
    entry_classification: str,
    entry_policy_sha256: str,
    decision_cutoff_utc_ns: int,
    exit_policy: ExitPolicyBindingInputF0 | None,
) -> str | None:
    if evidence is None:
        return "STRUCTURE_EVIDENCE_MISSING"
    expected_classification = (
        "SYNTHETIC_ONLY"
        if entry_classification == "SYNTHETIC_ONLY"
        else "HISTORICAL_RESEARCH_CANDIDATE"
    )
    if evidence.classification != expected_classification:
        return "STRUCTURE_EVIDENCE_CLASSIFICATION_MISMATCH"
    if evidence.entry_policy_sha256 != entry_policy_sha256:
        return "STRUCTURE_EVIDENCE_ENTRY_POLICY_BINDING_MISMATCH"
    if evidence.evidence_max_estimation_outcome_utc_ns > decision_cutoff_utc_ns:
        return "STRUCTURE_EVIDENCE_FUTURE_OUTCOME"
    if exit_policy is None:
        return "STRUCTURE_EXIT_POLICY_MISSING"
    if (
        evidence.exit_policy_sha256 != exit_policy.policy_sha256
        or evidence.fee_schedule_sha256 != exit_policy.fee_schedule_sha256
    ):
        return "STRUCTURE_EVIDENCE_POLICY_BINDING_MISMATCH"
    if evidence.oos_episode_count < 100:
        return "STRUCTURE_EVIDENCE_OOS_EPISODES_INSUFFICIENT"
    if evidence.fold_count < 3:
        return "STRUCTURE_EVIDENCE_FOLDS_INSUFFICIENT"
    if evidence.coverage_ppm < 950_000:
        return "STRUCTURE_EVIDENCE_COVERAGE_INSUFFICIENT"
    if evidence.expected_net_return_lower_bound_ppm <= 0:
        return "STRUCTURE_EXPECTED_RETURN_LOWER_BOUND_NOT_POSITIVE"
    if evidence.half_kelly_ppm <= 0:
        return "STRUCTURE_HALF_KELLY_ZERO"
    return None


def _safe_last_trading_date(source: EntryDecisionInputF0, policy: EntryPolicyF0) -> date:
    return date.fromisoformat(source.h20_date) + timedelta(
        days=policy.expiry_safety_calendar_days
    )


def _call_eligible(
    call: CallCandidateF0,
    *,
    delta_value: int,
    delta_min: int,
    delta_max: int,
    safe_last_trading: date,
) -> bool:
    return (
        delta_min <= delta_value <= delta_max
        and date.fromisoformat(call.last_trading_date) >= safe_last_trading
        and call.executable
        and call.bid_size > 0
        and call.ask_size > 0
    )


def _select_lc0(
    source: EntryDecisionInputF0, policy: EntryPolicyF0
) -> tuple[_SelectedStructure | None, DecisionTraceStageF0]:
    evidence_reason = _evidence_reason(
        source.lc0_evidence,
        entry_classification=source.classification,
        entry_policy_sha256=policy.policy_sha256,
        decision_cutoff_utc_ns=source.decision_cutoff_utc_ns,
        exit_policy=source.lc0_exit_policy,
    )
    safe_date = _safe_last_trading_date(source, policy)
    economics = {item.contract_id: item for item in source.lc0_economics}

    def choose(delta_name: str) -> CallCandidateF0 | None:
        candidates = [
            item
            for item in source.call_universe
            if item.contract_id in economics
            and economics[item.contract_id].stressed_entry_debit_nano_usd
            >= item.ask_nano_usd * item.multiplier
            and _call_eligible(
                item,
                delta_value=getattr(item, delta_name),
                delta_min=policy.lc0_delta_min_ppm,
                delta_max=policy.lc0_delta_max_ppm,
                safe_last_trading=safe_date,
            )
        ]
        return min(
            candidates,
            key=lambda item: (
                item.expiry_date,
                abs(getattr(item, delta_name) - policy.lc0_delta_target_ppm),
                -item.strike_nano_usd,
                item.contract_id,
            ),
            default=None,
        )

    coarse = choose("coarse_delta_ppm")
    fine = choose("fine_delta_ppm")
    calculation: dict[str, object] = {
        "safe_last_trading_date_inclusive": safe_date.isoformat(),
        "coarse_selected_contract_id": None if coarse is None else coarse.contract_id,
        "fine_selected_contract_id": None if fine is None else fine.contract_id,
        "selected_contract_id": None,
        "universe_length": len(source.call_universe),
    }
    reason = evidence_reason
    if reason is None and (coarse is None or fine is None):
        reason = "LC0_NO_ELIGIBLE_CONTRACT"
    if reason is None and coarse.contract_id != fine.contract_id:  # type: ignore[union-attr]
        reason = "LC0_COARSE_FINE_SELECTION_DISAGREEMENT"
    if reason is not None:
        return None, _stage(
            "LC0_EVALUATION",
            "ELIMINATED",
            reason,
            "REFRESH_LC0_CANDIDATE_OR_EVIDENCE",
            calculation,
            (source.technical_facts_sha256,) + (() if source.lc0_evidence is None else (source.lc0_evidence.evidence_sha256,)),
        )
    assert fine is not None and source.lc0_evidence is not None
    calculation["selected_contract_id"] = fine.contract_id
    calculation["selected_expiry_date"] = fine.expiry_date
    calculation["selected_delta_ppm"] = fine.fine_delta_ppm
    return _SelectedStructure(
        carrier_id="LC0",
        contracts=(fine,),
        economics=economics[fine.contract_id],
        evidence=source.lc0_evidence,
    ), _stage(
        "LC0_EVALUATION",
        "PASS",
        "LC0_ELIGIBLE",
        "CONTINUE_SIZING",
        calculation,
        (source.technical_facts_sha256, source.lc0_evidence.evidence_sha256),
    )


def _select_bcs0(
    source: EntryDecisionInputF0, policy: EntryPolicyF0
) -> tuple[_SelectedStructure | None, DecisionTraceStageF0]:
    evidence_reason = _evidence_reason(
        source.bcs0_evidence,
        entry_classification=source.classification,
        entry_policy_sha256=policy.policy_sha256,
        decision_cutoff_utc_ns=source.decision_cutoff_utc_ns,
        exit_policy=source.bcs0_exit_policy,
    )
    safe_date = _safe_last_trading_date(source, policy)
    calls = {item.contract_id: item for item in source.call_universe}
    economics = {
        (item.long_contract_id, item.short_contract_id): item
        for item in source.bcs0_economics
    }

    def choose(delta_name: str) -> tuple[CallCandidateF0, CallCandidateF0] | None:
        candidates: list[tuple[CallCandidateF0, CallCandidateF0]] = []
        for pair in source.bcs0_economics:
            long = calls[pair.long_contract_id]
            short = calls[pair.short_contract_id]
            if (
                long.expiry_date == short.expiry_date
                and long.multiplier == short.multiplier
                and long.strike_nano_usd < short.strike_nano_usd
                and _call_eligible(
                    long,
                    delta_value=getattr(long, delta_name),
                    delta_min=policy.bcs0_long_delta_min_ppm,
                    delta_max=policy.bcs0_long_delta_max_ppm,
                    safe_last_trading=safe_date,
                )
                and _call_eligible(
                    short,
                    delta_value=getattr(short, delta_name),
                    delta_min=policy.bcs0_short_delta_min_ppm,
                    delta_max=policy.bcs0_short_delta_max_ppm,
                    safe_last_trading=safe_date,
                )
                and long.ask_nano_usd > short.bid_nano_usd
                and pair.stressed_entry_debit_nano_usd
                >= (long.ask_nano_usd - short.bid_nano_usd)
                * long.multiplier
                and pair.stressed_entry_debit_nano_usd
                < (short.strike_nano_usd - long.strike_nano_usd)
                * long.multiplier
            ):
                candidates.append((long, short))
        return min(
            candidates,
            key=lambda pair: (
                pair[0].expiry_date,
                abs(getattr(pair[0], delta_name) - policy.bcs0_long_delta_target_ppm)
                + abs(getattr(pair[1], delta_name) - policy.bcs0_short_delta_target_ppm),
                abs(getattr(pair[0], delta_name) - policy.bcs0_long_delta_target_ppm),
                abs(getattr(pair[1], delta_name) - policy.bcs0_short_delta_target_ppm),
                -pair[0].strike_nano_usd,
                -pair[1].strike_nano_usd,
                pair[0].contract_id,
                pair[1].contract_id,
            ),
            default=None,
        )

    coarse = choose("coarse_delta_ppm")
    fine = choose("fine_delta_ppm")
    coarse_ids = None if coarse is None else [coarse[0].contract_id, coarse[1].contract_id]
    fine_ids = None if fine is None else [fine[0].contract_id, fine[1].contract_id]
    calculation: dict[str, object] = {
        "safe_last_trading_date_inclusive": safe_date.isoformat(),
        "coarse_selected_contract_ids": coarse_ids,
        "fine_selected_contract_ids": fine_ids,
        "selected_contract_ids": None,
        "selection_independent_of_lc0": True,
        "universe_length": len(source.call_universe),
    }
    reason = evidence_reason
    if reason is None and (coarse is None or fine is None):
        reason = "BCS0_NO_ELIGIBLE_PAIR"
    if reason is None and coarse_ids != fine_ids:
        reason = "BCS0_COARSE_FINE_SELECTION_DISAGREEMENT"
    if reason is not None:
        return None, _stage(
            "BCS0_EVALUATION",
            "ELIMINATED",
            reason,
            "REFRESH_BCS0_CANDIDATE_OR_EVIDENCE",
            calculation,
            (source.technical_facts_sha256,) + (() if source.bcs0_evidence is None else (source.bcs0_evidence.evidence_sha256,)),
        )
    assert fine is not None and source.bcs0_evidence is not None
    long, short = fine
    calculation["selected_contract_ids"] = [long.contract_id, short.contract_id]
    calculation["selected_expiry_date"] = long.expiry_date
    calculation["long_delta_ppm"] = long.fine_delta_ppm
    calculation["short_delta_ppm"] = short.fine_delta_ppm
    return _SelectedStructure(
        carrier_id="BCS0",
        contracts=(long, short),
        economics=economics[(long.contract_id, short.contract_id)],
        evidence=source.bcs0_evidence,
    ), _stage(
        "BCS0_EVALUATION",
        "PASS",
        "BCS0_ELIGIBLE",
        "CONTINUE_SIZING",
        calculation,
        (source.technical_facts_sha256, source.bcs0_evidence.evidence_sha256),
    )


def _drawdown(source: EntryDecisionInputF0) -> tuple[int, int] | None:
    account = source.account_facts
    if account.current_nlv_nano_usd is None or account.peak_nlv_nano_usd is None:
        return None
    if account.peak_nlv_nano_usd <= 0:
        return None
    drawdown = max(
        0,
        (account.peak_nlv_nano_usd - account.current_nlv_nano_usd) * PPM
        // account.peak_nlv_nano_usd,
    )
    if drawdown <= 50_000:
        factor = PPM
    elif drawdown <= 100_000:
        factor = 800_000
    elif drawdown < 150_000:
        factor = 400_000
    else:
        factor = 0
    return drawdown, factor


def _size_structure(
    selected: _SelectedStructure,
    source: EntryDecisionInputF0,
    policy: EntryPolicyF0,
) -> tuple[EntryPlanF0 | None, dict[str, object], str]:
    account = source.account_facts
    required = (
        account.eligible_bankroll_nano_usd,
        account.settled_cash_nano_usd,
        account.cash_reserve_nano_usd,
        account.current_signed_gld_delta_exposure_nano_usd,
        account.delta_limit_nano_usd,
        account.external_capacity_units,
    )
    drawdown = _drawdown(source)
    if any(item is None for item in required) or drawdown is None:
        return None, {"missing_required_cap": True}, "SIZING_REQUIRED_CAP_MISSING"
    eligible, settled, reserve, signed_delta, delta_limit, q_external = required
    assert all(type(item) is int for item in required)
    assert drawdown is not None
    drawdown_ppm, factor_ppm = drawdown
    economics = selected.economics
    exit_fee = (
        source.planned_exit_fee_lc0_nano_usd
        if selected.carrier_id == "LC0"
        else source.planned_exit_fee_bcs0_nano_usd
    )
    if exit_fee is None:
        return None, {"missing_planned_exit_fee": True}, "PLANNED_EXIT_FEE_MISSING"
    planned_loss = economics.stressed_max_loss_basis_nano_usd + exit_fee
    if selected.carrier_id == "LC0":
        call = selected.contracts[0]
        current_entry_debit = call.ask_nano_usd * call.multiplier
        q_liquidity = call.ask_size
    else:
        long, short = selected.contracts
        if long.multiplier != short.multiplier:
            return None, {"multiplier_mismatch": True}, "BCS0_MULTIPLIER_MISMATCH"
        current_entry_debit = (
            long.ask_nano_usd - short.bid_nano_usd
        ) * long.multiplier
        q_liquidity = min(long.ask_size, short.bid_size)
    if current_entry_debit <= 0:
        return None, {"current_entry_debit_nano_usd": current_entry_debit}, "ENTRY_DEBIT_NOT_POSITIVE"

    q_kelly = (
        eligible
        * selected.evidence.half_kelly_ppm
        * factor_ppm
        // (PPM * PPM)
        // planned_loss
    )
    q_account = (
        eligible * policy.account_loss_budget_ppm // PPM // planned_loss
    )
    q_cash = max(0, settled - reserve) // economics.stressed_entry_debit_nano_usd
    current_positive_delta = max(0, signed_delta)
    q_delta = max(0, delta_limit - current_positive_delta) // economics.delta_notional_nano_usd
    capacities = {
        "q_kelly": q_kelly,
        "q_account": q_account,
        "q_cash": q_cash,
        "q_delta": q_delta,
        "q_liquidity": q_liquidity,
        "q_external": q_external,
    }
    safe_quantity = min(capacities.values())
    bindings = tuple(sorted(key for key, value in capacities.items() if value == safe_quantity))
    calculation: dict[str, object] = {
        "carrier_id": selected.carrier_id,
        "eligible_bankroll_nano_usd": eligible,
        "half_kelly_ppm": selected.evidence.half_kelly_ppm,
        "drawdown_ppm": drawdown_ppm,
        "drawdown_factor_ppm": factor_ppm,
        "stressed_max_loss_basis_nano_usd": economics.stressed_max_loss_basis_nano_usd,
        "planned_exit_fee_nano_usd": exit_fee,
        "planned_loss_per_unit_nano_usd": planned_loss,
        "stressed_entry_debit_nano_usd": economics.stressed_entry_debit_nano_usd,
        "delta_notional_per_unit_nano_usd": economics.delta_notional_nano_usd,
        "current_signed_delta_exposure_nano_usd": signed_delta,
        "current_positive_delta_used_nano_usd": current_positive_delta,
        **capacities,
        "safe_quantity": safe_quantity,
        "binding_caps": list(bindings),
    }
    if selected.evidence.expected_net_return_lower_bound_ppm <= 0:
        return None, calculation, "STRUCTURE_EXPECTED_RETURN_LOWER_BOUND_NOT_POSITIVE"
    if selected.evidence.half_kelly_ppm <= 0:
        return None, calculation, "STRUCTURE_HALF_KELLY_ZERO"
    if safe_quantity == 0:
        return None, calculation, "STRUCTURE_ZERO_SAFE_QUANTITY"
    exit_policy = (
        source.lc0_exit_policy if selected.carrier_id == "LC0" else source.bcs0_exit_policy
    )
    if exit_policy is None:
        return None, calculation, "STRUCTURE_EXIT_POLICY_MISSING"
    comparison_numerator = (
        current_entry_debit
        * safe_quantity
        * selected.evidence.expected_net_return_lower_bound_ppm
    )
    expected_profit = comparison_numerator // PPM
    plan = EntryPlanF0(
        carrier_id=selected.carrier_id,
        contract_ids=tuple(item.contract_id for item in selected.contracts),
        expiry_date=selected.contracts[0].expiry_date,
        safe_quantity=safe_quantity,
        current_entry_debit_per_unit_nano_usd=current_entry_debit,
        stressed_entry_debit_per_unit_nano_usd=economics.stressed_entry_debit_nano_usd,
        planned_loss_per_unit_nano_usd=planned_loss,
        delta_notional_per_unit_nano_usd=economics.delta_notional_nano_usd,
        expected_net_return_lower_bound_ppm=selected.evidence.expected_net_return_lower_bound_ppm,
        expected_total_net_profit_nano_usd=expected_profit,
        comparison_numerator=comparison_numerator,
        sizing_caps=capacities,
        binding_caps=bindings,
        management_policy_id=policy.management_policy_id,
        management_policy_sha256=policy.management_policy_sha256,
        h20_date=source.h20_date,
        h20_exit_utc_ns=source.h20_exit_utc_ns,
        expiry_safety_calendar_days=policy.expiry_safety_calendar_days,
        invalidation_confirmation_sessions=policy.invalidation_confirmation_sessions,
        hard_stop_policy_id=exit_policy.policy_id,
        hard_stop_policy_sha256=exit_policy.policy_sha256,
        hard_stop_loss_ppm=exit_policy.hard_stop_loss_ppm,
    )
    return plan, calculation, "SIZING_PASS"


def _preference(plans: list[EntryPlanF0]) -> tuple[EntryPlanF0, EntryPlanF0 | None, dict[str, object]]:
    ranked = sorted(
        plans,
        key=lambda item: (
            -item.comparison_numerator,
            item.planned_loss_per_unit_nano_usd * item.safe_quantity,
            item.current_entry_debit_per_unit_nano_usd * item.safe_quantity,
            0 if item.carrier_id == "LC0" else 1,
        ),
    )
    preferred = ranked[0]
    backup = ranked[1] if len(ranked) == 2 else None
    calculation = {
        "ranking_order": [item.carrier_id for item in ranked],
        "structures": {
            item.carrier_id: {
                "comparison_numerator": item.comparison_numerator,
                "expected_total_net_profit_nano_usd": item.expected_total_net_profit_nano_usd,
                "total_planned_loss_nano_usd": item.planned_loss_per_unit_nano_usd * item.safe_quantity,
                "current_entry_cash_usage_nano_usd": item.current_entry_debit_per_unit_nano_usd * item.safe_quantity,
            }
            for item in sorted(plans, key=lambda plan: plan.carrier_id)
        },
        "tie_break_order": [
            "EXPECTED_TOTAL_NET_PROFIT_DESC",
            "TOTAL_PLANNED_LOSS_ASC",
            "CURRENT_ENTRY_CASH_USAGE_ASC",
            "EXACT_TIE_LC0_FIRST",
        ],
        "backup_requires_fresh_snapshot_and_rerun": backup is not None,
    }
    return preferred, backup, calculation


def evaluate_entry_decision_f0(
    entry_input: object, policy_input: object
) -> EntryDecisionResultF0:
    """Run the unique research-only Entry f F0 over closed validated inputs."""

    if isinstance(entry_input, EntryDecisionInputF0):
        entry_document = entry_input.as_dict()
        if (
            canonical_json_bytes(entry_document) != entry_input.canonical_bytes
            or canonical_json_sha256(entry_document) != entry_input.input_sha256
        ):
            raise EntryDecisionF0Error("ENTRY_INPUT_TYPED_INTEGRITY_MISMATCH")
        source = validate_entry_decision_input_f0(entry_document)
    else:
        source = validate_entry_decision_input_f0(entry_input)
    if isinstance(policy_input, EntryPolicyF0):
        policy_document = policy_input.as_dict()
        if (
            canonical_json_bytes(policy_document) != policy_input.canonical_bytes
            or canonical_json_sha256(policy_document) != policy_input.policy_sha256
        ):
            raise EntryDecisionF0Error("ENTRY_POLICY_TYPED_INTEGRITY_MISMATCH")
        policy = validate_entry_policy_f0(policy_document)
    else:
        policy = validate_entry_policy_f0(policy_input)
    qualification_failure = _input_qualification_reason(source, policy)
    if qualification_failure is not None:
        return _blocked_result(source, policy, *qualification_failure)

    stages: list[DecisionTraceStageF0] = [
        _stage(
            "INPUT_QUALIFICATION",
            "PASS",
            "INPUT_QUALIFICATION_PASS",
            "CONTINUE_ENTRY_GATE",
            {
                "data_qualification_status": source.data_qualification_status,
                "universe_complete": source.universe_complete,
                "universe_length": len(source.call_universe),
                "open_gld_order_count": source.open_gld_order_count,
                "policy_status": policy.policy_status,
            },
            (
                source.entry_fact_bundle_sha256,
                source.technical_facts_sha256,
                source.atomic_snapshot_sha256,
                policy.policy_sha256,
            ),
        )
    ]
    gate_stage = _evaluate_gate(source, policy)
    stages.append(gate_stage)
    if gate_stage.state != "PASS":
        stages.extend(
            _not_run(stage_id, gate_stage.reason_code) for stage_id in TRACE_STAGE_IDS[2:]
        )
        status = "NO_DECISION" if gate_stage.state == "BLOCKED" else "NO_ACTION"
        return _finalize(source, policy, status, gate_stage.reason_code, stages)

    lc_selected, lc_stage = _select_lc0(source, policy)
    bcs_selected, bcs_stage = _select_bcs0(source, policy)
    stages.extend((lc_stage, bcs_stage))
    selected = [item for item in (lc_selected, bcs_selected) if item is not None]
    if not selected:
        stages.extend(_not_run(stage_id, "ALL_STRUCTURES_ELIMINATED") for stage_id in TRACE_STAGE_IDS[4:])
        return _finalize(source, policy, "NO_ACTION", "ALL_STRUCTURES_ELIMINATED", stages)

    plans: list[EntryPlanF0] = []
    sizing_calculations: dict[str, object] = {}
    sizing_reasons: dict[str, str] = {}
    for item in selected:
        plan, calculation, reason = _size_structure(item, source, policy)
        sizing_calculations[item.carrier_id] = calculation
        sizing_reasons[item.carrier_id] = reason
        if plan is not None:
            plans.append(plan)
    if not plans:
        blocked_reasons = {
            "SIZING_REQUIRED_CAP_MISSING",
            "PLANNED_EXIT_FEE_MISSING",
            "BCS0_MULTIPLIER_MISMATCH",
            "ENTRY_DEBIT_NOT_POSITIVE",
            "STRUCTURE_EXIT_POLICY_MISSING",
        }
        blocked = any(reason in blocked_reasons for reason in sizing_reasons.values())
        terminal_reason = (
            "SIZING_REQUIRED_FACT_BLOCKED"
            if blocked
            else "ALL_STRUCTURES_ZERO_QUANTITY"
        )
        stages.append(
            _stage(
                "SIZING",
                "BLOCKED" if blocked else "ELIMINATED",
                terminal_reason,
                (
                    "REFRESH_REQUIRED_SIZING_FACTS_AND_RERUN"
                    if blocked
                    else "REFRESH_CAPS_OR_WAIT_FOR_RISK_CAPACITY"
                ),
                {"structures": sizing_calculations, "structure_reasons": sizing_reasons},
                (source.entry_fact_bundle_sha256, source.technical_facts_sha256),
            )
        )
        stages.extend(
            _not_run(stage_id, terminal_reason) for stage_id in TRACE_STAGE_IDS[5:]
        )
        return _finalize(
            source,
            policy,
            "NO_DECISION" if blocked else "NO_ACTION",
            terminal_reason,
            stages,
        )

    stages.append(
        _stage(
            "SIZING",
            "PASS",
            "SIZING_PASS",
            "CONTINUE_PREFERENCE",
            {"structures": sizing_calculations, "structure_reasons": sizing_reasons},
            (source.entry_fact_bundle_sha256, source.technical_facts_sha256),
        )
    )
    preferred, backup, preference_calculation = _preference(plans)
    stages.append(
        _stage(
            "PREFERENCE",
            "PASS",
            "SINGLE_ELIGIBLE_STRUCTURE" if backup is None else "PREFERENCE_DETERMINED",
            "BIND_EXIT_POLICY",
            preference_calculation,
            tuple(item.evidence.evidence_sha256 for item in selected),
        )
    )
    stages.append(
        _stage(
            "EXIT_POLICY_BINDING",
            "PASS",
            "EXIT_POLICY_BOUND",
            "FORM_FINAL_RESEARCH_DECISION",
            {
                "management_policy_id": policy.management_policy_id,
                "management_policy_sha256": policy.management_policy_sha256,
                "h20_date": source.h20_date,
                "h20_exit_utc_ns": source.h20_exit_utc_ns,
                "expiry_safety_calendar_days": policy.expiry_safety_calendar_days,
                "confirmed_invalidation": {
                    "trend_pass_count_max": 1,
                    "complete_sessions_required": policy.invalidation_confirmation_sessions,
                    "latch": True,
                },
                "hard_stops": {
                    plan.carrier_id: {
                        "policy_id": plan.hard_stop_policy_id,
                        "policy_sha256": plan.hard_stop_policy_sha256,
                        "loss_ppm": plan.hard_stop_loss_ppm,
                    }
                    for plan in sorted(plans, key=lambda item: item.carrier_id)
                },
                "expiry_safety_override": True,
                "account_risk_override": True,
            },
            (
                policy.management_policy_sha256,
                *(plan.hard_stop_policy_sha256 for plan in plans),
            ),
        )
    )
    final_status = "SINGLE_PLAN" if backup is None else "PREFERRED_AND_BACKUP"
    stages.append(
        _stage(
            "FINAL_DECISION",
            "PASS",
            final_status,
            "OWNER_REVIEW_RESEARCH_DECISION_CARD",
            {
                "decision_status": final_status,
                "preferred_carrier_id": preferred.carrier_id,
                "backup_carrier_id": None if backup is None else backup.carrier_id,
                "actionable": False,
                "broker_order_count": 0,
            },
            (source.input_sha256, policy.policy_sha256),
        )
    )
    return _finalize(
        source,
        policy,
        final_status,
        final_status,
        stages,
        preferred,
        backup,
    )


def _exit_int(
    value: object,
    *,
    minimum: int = 0,
    maximum: int = 2**63 - 1,
    optional: bool = False,
) -> int | None:
    if optional and value is None:
        return None
    if type(value) is not int or not minimum <= value <= maximum:
        raise EntryDecisionF0Error("EXIT_TRIGGER_INTEGER_INVALID")
    return value


def _quote_leg_quality(
    *,
    bid: int,
    ask: int,
    bid_size: int,
    ask_size: int,
    event_ns: int,
    receive_ns: int,
    observation_ns: int,
    max_age_ns: int,
    max_spread_ppm: int,
) -> tuple[bool, dict[str, object]]:
    time_order_valid = event_ns <= receive_ns <= observation_ns
    event_age = observation_ns - event_ns if event_ns <= observation_ns else -1
    receive_age = observation_ns - receive_ns if receive_ns <= observation_ns else -1
    market_valid = bid > 0 and ask >= bid
    spread_ppm = (
        (ask - bid) * PPM // ask if market_valid and ask > 0 else None
    )
    size_valid = bid_size > 0 and ask_size > 0
    fresh = (
        time_order_valid
        and 0 <= event_age <= max_age_ns
        and 0 <= receive_age <= max_age_ns
    )
    spread_valid = spread_ppm is not None and spread_ppm <= max_spread_ppm
    return market_valid and size_valid and fresh and spread_valid, {
        "event_age_ns": event_age,
        "receive_age_ns": receive_age,
        "spread_ppm": spread_ppm,
        "market_valid": market_valid,
        "size_valid": size_valid,
        "fresh": fresh,
        "spread_valid": spread_valid,
    }


def _evaluate_exit_trigger_fixture_f0(
    document: object,
) -> _ExitTriggerFixtureResultF0:
    """Exercise unbound quote/threshold math for internal R1 fixtures only.

    This helper deliberately is not a public lifecycle interface: it has no
    Entry plan, episode, policy, fee, or previous-result lineage.  A complete
    exit state machine belongs to a later roadmap stage.
    """

    if type(document) is not dict or frozenset(document) != _EXIT_OBSERVATION_KEYS:
        raise EntryDecisionF0Error("EXIT_TRIGGER_SCHEMA_INVALID")
    canonical_json_bytes(document)
    if document["schema_version"] != EXIT_TRIGGER_OBSERVATION_SCHEMA_VERSION:
        raise EntryDecisionF0Error("EXIT_TRIGGER_VERSION_INVALID")
    carrier_id = document["carrier_id"]
    if carrier_id not in {"LC0", "BCS0"}:
        raise EntryDecisionF0Error("EXIT_TRIGGER_CARRIER_INVALID")
    for key in ("already_latched", "previous_session_complete", "current_session_complete"):
        if type(document[key]) is not bool:
            raise EntryDecisionF0Error("EXIT_TRIGGER_BOOLEAN_INVALID")

    observation_ns = _exit_int(document["observation_utc_ns"])
    max_age_ns = _exit_int(document["max_quote_age_ns"])
    max_skew_ns = _exit_int(document["max_cross_leg_receive_skew_ns"])
    max_spread_ppm = _exit_int(document["max_leg_spread_ppm"], maximum=PPM)
    basis = _exit_int(document["entry_after_cost_basis_nano_usd"], minimum=1)
    exit_fees = _exit_int(document["planned_exit_fees_nano_usd"])
    multiplier = _exit_int(document["multiplier"], minimum=1, maximum=100_000)
    hard_stop_ppm = _exit_int(
        document["hard_stop_loss_ppm"], maximum=PPM, optional=True
    )
    previous_trend = _exit_int(document["previous_trend_pass_count"], maximum=3)
    current_trend = _exit_int(document["current_trend_pass_count"], maximum=3)
    long_values = tuple(
        _exit_int(document[key], minimum=1 if "nano_usd" in key else 0)
        for key in (
            "long_bid_nano_usd",
            "long_ask_nano_usd",
            "long_bid_size",
            "long_ask_size",
            "long_quote_event_utc_ns",
            "long_quote_receive_utc_ns",
        )
    )
    assert all(value is not None for value in (
        observation_ns, max_age_ns, max_skew_ns, max_spread_ppm, basis,
        exit_fees, multiplier, previous_trend, current_trend,
    ))
    assert all(value is not None for value in long_values)
    long_bid, long_ask, long_bid_size, long_ask_size, long_event, long_receive = long_values
    long_quality, long_calculation = _quote_leg_quality(
        bid=long_bid,
        ask=long_ask,
        bid_size=long_bid_size,
        ask_size=long_ask_size,
        event_ns=long_event,
        receive_ns=long_receive,
        observation_ns=observation_ns,
        max_age_ns=max_age_ns,
        max_spread_ppm=max_spread_ppm,
    )

    short_quality = True
    short_calculation: dict[str, object] | None = None
    receive_skew_ns = 0
    if carrier_id == "BCS0":
        short_values = tuple(
            _exit_int(document[key], minimum=1 if "nano_usd" in key else 0)
            for key in (
                "short_bid_nano_usd",
                "short_ask_nano_usd",
                "short_bid_size",
                "short_ask_size",
                "short_quote_event_utc_ns",
                "short_quote_receive_utc_ns",
            )
        )
        assert all(value is not None for value in short_values)
        short_bid, short_ask, short_bid_size, short_ask_size, short_event, short_receive = short_values
        short_quality, short_calculation = _quote_leg_quality(
            bid=short_bid,
            ask=short_ask,
            bid_size=short_bid_size,
            ask_size=short_ask_size,
            event_ns=short_event,
            receive_ns=short_receive,
            observation_ns=observation_ns,
            max_age_ns=max_age_ns,
            max_spread_ppm=max_spread_ppm,
        )
        receive_skew_ns = abs(long_receive - short_receive)
        exit_value = (long_bid - short_ask) * multiplier - exit_fees
    else:
        short_fields = (
            "short_bid_nano_usd",
            "short_ask_nano_usd",
            "short_bid_size",
            "short_ask_size",
            "short_quote_event_utc_ns",
            "short_quote_receive_utc_ns",
        )
        if any(document[key] is not None for key in short_fields):
            raise EntryDecisionF0Error("EXIT_TRIGGER_LC0_SHORT_LEG_FORBIDDEN")
        exit_value = long_bid * multiplier - exit_fees
    synchronized = receive_skew_ns <= max_skew_ns
    hard_stop_evaluable = (
        hard_stop_ppm is not None
        and long_quality
        and short_quality
        and synchronized
    )
    loss_nano_usd = max(0, basis - exit_value)
    loss_ppm = loss_nano_usd * PPM // basis
    hard_stop_triggered = hard_stop_evaluable and loss_ppm >= hard_stop_ppm
    invalidation_triggered = (
        document["previous_session_complete"]
        and document["current_session_complete"]
        and previous_trend <= 1
        and current_trend <= 1
    )
    if document["already_latched"]:
        exit_state = "EXIT_DUE_LATCHED"
        trigger_reason = "PREVIOUS_LATCH"
    elif invalidation_triggered:
        exit_state = "EXIT_DUE_LATCHED"
        trigger_reason = "CONFIRMED_INVALIDATION"
    elif hard_stop_triggered:
        exit_state = "EXIT_DUE_LATCHED"
        trigger_reason = "HARD_STOP"
    else:
        exit_state = "MONITORING"
        trigger_reason = (
            "HARD_STOP_QUOTE_QUALITY_BLOCKED"
            if hard_stop_ppm is not None and not hard_stop_evaluable
            else "NO_EXIT_TRIGGER"
        )
    calculation = {
        "long_quote_quality": long_calculation,
        "short_quote_quality": short_calculation,
        "cross_leg_receive_skew_ns": receive_skew_ns,
        "cross_leg_synchronized": synchronized,
        "after_cost_executable_exit_value_nano_usd": exit_value,
        "after_cost_loss_nano_usd": loss_nano_usd,
        "after_cost_loss_ppm": loss_ppm,
        "hard_stop_loss_ppm": hard_stop_ppm,
        "hard_stop_triggered": hard_stop_triggered,
        "confirmed_invalidation_triggered": invalidation_triggered,
        "latch_cannot_clear_on_rebound": True,
        "represents_fill": False,
    }
    unsigned = {
        "schema_version": EXIT_TRIGGER_RESULT_SCHEMA_VERSION,
        "classification": CLASSIFICATION,
        "authority_status": AUTHORITY_STATUS,
        "actionable": False,
        "broker_order_count": 0,
        "carrier_id": carrier_id,
        "exit_state": exit_state,
        "trigger_reason": trigger_reason,
        "hard_stop_evaluable": hard_stop_evaluable,
        "calculation": calculation,
    }
    result_hash = canonical_json_sha256(unsigned)
    final = {**unsigned, "result_sha256": result_hash}
    return _ExitTriggerFixtureResultF0(
        schema_version=EXIT_TRIGGER_RESULT_SCHEMA_VERSION,
        classification=CLASSIFICATION,
        authority_status=AUTHORITY_STATUS,
        actionable=False,
        broker_order_count=0,
        carrier_id=carrier_id,
        exit_state=exit_state,
        trigger_reason=trigger_reason,
        hard_stop_evaluable=hard_stop_evaluable,
        calculation=_detached(calculation),  # type: ignore[arg-type]
        result_sha256=result_hash,
        canonical_bytes=canonical_json_bytes(final),
    )


__all__ = [
    "AUTHORITY_STATUS",
    "DECISION_RESULT_SCHEMA_VERSION",
    "DecisionTraceF0",
    "DecisionTraceStageF0",
    "EntryDecisionResultF0",
    "EntryPlanF0",
    "TRACE_STAGE_IDS",
    "evaluate_entry_decision_f0",
]
