"""Hypothetical, non-actionable position guidance for Research F0."""

from __future__ import annotations

from .canonical import canonical_json_bytes, canonical_json_sha256
from .contracts import (
    HypotheticalManagementActionF0,
    ManagementActionSnapshotF0,
    ManagementScoreResultF0,
    ManagementTerminalOverrideF0,
)
from .errors import ManagementResearchError
from .indicators import FORMULA_CATALOG_SHA256
from .math import ceil_fraction
from .policy import POLICY_SET_SHA256, action_policy_f0, verify_policy_set_f0
from .validation import (
    TERMINAL_OVERRIDE_SCHEMA_VERSION,
    validate_management_action_snapshot_f0,
    validate_management_terminal_override_f0,
)


ACTION_RESULT_SCHEMA_VERSION = "GLD_HYPOTHETICAL_MANAGEMENT_ACTION_F0_V1"
_ACTION_POLICY_ACCESSOR_IMPORT = action_policy_f0
_POLICY_VERIFIER_IMPORT = verify_policy_set_f0


def _sealed_action_policy():
    if (
        action_policy_f0 is not _ACTION_POLICY_ACCESSOR_IMPORT
        or verify_policy_set_f0 is not _POLICY_VERIFIER_IMPORT
    ):
        raise ManagementResearchError("POLICY_RUNTIME_IDENTITY_MISMATCH")
    _POLICY_VERIFIER_IMPORT()
    return _ACTION_POLICY_ACCESSOR_IMPORT()


MAX_QUOTE_AGE_NS = _sealed_action_policy().max_quote_receive_age_ns
MAX_QUOTE_EVENT_AGE_NS = _sealed_action_policy().max_quote_event_age_ns
MAX_CROSS_LEG_RECEIVE_SKEW_NS = _sealed_action_policy().max_cross_leg_receive_skew_ns


def _require_validated_score_seal(score: ManagementScoreResultF0) -> None:
    body = score.as_dict()
    unsigned = dict(body)
    result_hash = unsigned.pop("result_sha256", None)
    if (
        result_hash != score.result_sha256
        or canonical_json_sha256(unsigned) != score.result_sha256
        or canonical_json_bytes(body) != score.canonical_bytes
        or score.formula_catalog_sha256 != FORMULA_CATALOG_SHA256
        or score.policy_set_sha256 != POLICY_SET_SHA256
    ):
        raise ManagementResearchError("SCORE_TYPED_SEAL_INVALID")


def _result(
    *,
    score: ManagementScoreResultF0,
    snapshot: ManagementActionSnapshotF0 | ManagementTerminalOverrideF0,
    action: str,
    reason_code: str,
    target_units: int,
    score_cap_units: int | None,
    effective_cap_units: int | None,
    score_authority: str = "RESEARCH_ONLY",
    exit_latch_status_after: str | None = None,
) -> HypotheticalManagementActionF0:
    latch_before = score.updated_state.exit_latch_status
    latch_after = (
        latch_before if exit_latch_status_after is None else exit_latch_status_after
    )
    body = {
        "schema_version": ACTION_RESULT_SCHEMA_VERSION,
        "classification": "RESEARCH_ONLY",
        "authority_status": "NO_DECISION_EFFECT",
        "actionable": False,
        "broker_order_count": 0,
        "score_authority": score_authority,
        "action": action,
        "reason_code": reason_code,
        "current_units": snapshot.current_units,
        "target_units": target_units,
        "quantity_change_units": target_units - snapshot.current_units,
        "score_cap_units": score_cap_units,
        "effective_cap_units": effective_cap_units,
        "exit_latch_status_before": latch_before,
        "exit_latch_status_after": latch_after,
        "input_score_sha256": score.result_sha256,
        "action_snapshot_sha256": snapshot.snapshot_sha256,
        "action_policy_sha256": POLICY_SET_SHA256,
    }
    result_hash = canonical_json_sha256(body)
    final_body = {**body, "action_result_sha256": result_hash}
    return HypotheticalManagementActionF0(
        schema_version=ACTION_RESULT_SCHEMA_VERSION,
        classification="RESEARCH_ONLY",
        authority_status="NO_DECISION_EFFECT",
        actionable=False,
        broker_order_count=0,
        score_authority=score_authority,
        action=action,
        reason_code=reason_code,
        current_units=snapshot.current_units,
        target_units=target_units,
        quantity_change_units=target_units - snapshot.current_units,
        score_cap_units=score_cap_units,
        effective_cap_units=effective_cap_units,
        exit_latch_status_before=latch_before,
        exit_latch_status_after=latch_after,
        input_score_sha256=score.result_sha256,
        action_snapshot_sha256=snapshot.snapshot_sha256,
        action_policy_sha256=POLICY_SET_SHA256,
        action_result_sha256=result_hash,
        canonical_bytes=canonical_json_bytes(final_body),
    )


def _adjustment_block_reason(snapshot: ManagementActionSnapshotF0) -> str | None:
    policy = _sealed_action_policy()
    if snapshot.fee_facts_status != "COMPLETE":
        return "FEE_FACTS_NOT_COMPLETE"
    if snapshot.dte is None or snapshot.dte <= 0:
        return "DTE_NOT_ELIGIBLE"
    if frozenset(quote.contract_id for quote in snapshot.quotes) != frozenset(
        contract.contract_id for contract in snapshot.contracts
    ):
        return "QUOTE_SET_MISSING"
    if any(quote.bid_size <= 0 or quote.ask_size <= 0 for quote in snapshot.quotes):
        return "QUOTE_SIZE_INSUFFICIENT"
    if any(
        snapshot.action_utc_ns - quote.event_utc_ns
        > policy.max_quote_event_age_ns
        or snapshot.action_utc_ns - quote.receive_utc_ns
        > policy.max_quote_receive_age_ns
        for quote in snapshot.quotes
    ):
        return "QUOTE_STALE"
    receive_times = [quote.receive_utc_ns for quote in snapshot.quotes]
    if (
        max(receive_times) - min(receive_times)
        > policy.max_cross_leg_receive_skew_ns
    ):
        return "QUOTE_RECEIVE_SKEW_EXCEEDED"
    return None


def _reduce_or_block(
    *,
    score: ManagementScoreResultF0,
    snapshot: ManagementActionSnapshotF0,
    target: int,
    score_cap: int | None,
    effective_cap: int,
    reason: str,
    score_authority: str = "RESEARCH_ONLY",
) -> HypotheticalManagementActionF0:
    blocked = _adjustment_block_reason(snapshot)
    if blocked is not None:
        return _result(
            score=score,
            snapshot=snapshot,
            action="ACTION_BLOCKED",
            reason_code=blocked,
            target_units=snapshot.current_units,
            score_cap_units=score_cap,
            effective_cap_units=effective_cap,
            score_authority=score_authority,
        )
    return _result(
        score=score,
        snapshot=snapshot,
        action="REDUCE",
        reason_code=reason,
        target_units=target,
        score_cap_units=score_cap,
        effective_cap_units=effective_cap,
        score_authority=score_authority,
    )


def evaluate_hypothetical_management_action_f0(
    score: ManagementScoreResultF0,
    action_snapshot: object,
) -> HypotheticalManagementActionF0:
    """Map the primary score to hypothetical unit guidance, never an order."""

    if not isinstance(score, ManagementScoreResultF0):
        raise ManagementResearchError("SCORE_TYPED_CONTRACT_REQUIRED")
    if verify_policy_set_f0 is not _POLICY_VERIFIER_IMPORT:
        raise ManagementResearchError("POLICY_RUNTIME_IDENTITY_MISMATCH")
    _POLICY_VERIFIER_IMPORT()
    _require_validated_score_seal(score)
    if isinstance(action_snapshot, ManagementTerminalOverrideF0):
        try:
            normalized_terminal = validate_management_terminal_override_f0(
                action_snapshot.as_dict(), score
            )
        except ManagementResearchError as exc:
            raise ManagementResearchError(
                "TERMINAL_OVERRIDE_TYPED_SEAL_INVALID"
            ) from exc
        if normalized_terminal.snapshot_sha256 != action_snapshot.snapshot_sha256:
            raise ManagementResearchError("TERMINAL_OVERRIDE_TYPED_SEAL_INVALID")
        snapshot: ManagementActionSnapshotF0 | ManagementTerminalOverrideF0 = (
            normalized_terminal
        )
    elif (
        type(action_snapshot) is dict
        and action_snapshot.get("schema_version") == TERMINAL_OVERRIDE_SCHEMA_VERSION
    ):
        snapshot = validate_management_terminal_override_f0(action_snapshot, score)
    elif isinstance(action_snapshot, ManagementActionSnapshotF0):
        try:
            normalized_snapshot = validate_management_action_snapshot_f0(
                action_snapshot.as_dict(), score
            )
        except ManagementResearchError as exc:
            raise ManagementResearchError(
                "ACTION_SNAPSHOT_TYPED_SEAL_INVALID"
            ) from exc
        if normalized_snapshot.snapshot_sha256 != action_snapshot.snapshot_sha256:
            raise ManagementResearchError("ACTION_SNAPSHOT_TYPED_SEAL_INVALID")
        snapshot = normalized_snapshot
    else:
        snapshot = validate_management_action_snapshot_f0(action_snapshot, score)

    if score.updated_state.exit_latch_status == "EXIT_DUE_LATCHED":
        if snapshot.current_units == 0:
            return _result(
                score=score,
                snapshot=snapshot,
                action="HOLD",
                reason_code="EXIT_DUE_LATCHED_NO_POSITION",
                target_units=0,
                score_cap_units=0,
                effective_cap_units=0,
                score_authority="DIAGNOSTIC_ONLY",
            )
        return _result(
            score=score,
            snapshot=snapshot,
            action="EXIT_DUE",
            reason_code="EXIT_DUE_LATCHED",
            target_units=0,
            score_cap_units=0,
            effective_cap_units=0,
            score_authority="DIAGNOSTIC_ONLY",
        )
    if isinstance(snapshot, ManagementTerminalOverrideF0):
        return _result(
            score=score,
            snapshot=snapshot,
            action="EXIT_DUE",
            reason_code=snapshot.override_status,
            target_units=0,
            score_cap_units=0,
            effective_cap_units=0,
            score_authority="DIAGNOSTIC_ONLY",
            exit_latch_status_after="EXIT_DUE_LATCHED",
        )
    if snapshot.override_status == "DATA_NOT_QUALIFIED":
        return _result(
            score=score,
            snapshot=snapshot,
            action="NO_DECISION",
            reason_code="DATA_NOT_QUALIFIED",
            target_units=snapshot.current_units,
            score_cap_units=None,
            effective_cap_units=None,
            score_authority="DIAGNOSTIC_ONLY",
        )
    if snapshot.dte is None:
        return _result(
            score=score,
            snapshot=snapshot,
            action="ACTION_BLOCKED",
            reason_code="DTE_NOT_ELIGIBLE",
            target_units=snapshot.current_units,
            score_cap_units=None,
            effective_cap_units=None,
        )
    if snapshot.reconciliation_status != "MATCHED":
        return _result(
            score=score,
            snapshot=snapshot,
            action="RECONCILIATION_BLOCKED",
            reason_code=snapshot.reconciliation_status,
            target_units=snapshot.current_units,
            score_cap_units=None,
            effective_cap_units=None,
            score_authority="DIAGNOSTIC_ONLY",
        )
    caps = (
        snapshot.account_risk_cap_units,
        snapshot.liquidity_cap_units,
        snapshot.external_risk_cap_units,
    )
    if any(value is None for value in caps):
        return _result(
            score=score,
            snapshot=snapshot,
            action="NO_DECISION",
            reason_code="REQUIRED_RISK_CAP_MISSING",
            target_units=snapshot.current_units,
            score_cap_units=None,
            effective_cap_units=None,
        )
    exact_caps = tuple(value for value in caps if value is not None)
    external_ceiling = min(score.episode.original_approved_units, *exact_caps)
    if external_ceiling == 0:
        return _result(
            score=score,
            snapshot=snapshot,
            action="EXIT_DUE",
            reason_code="EXTERNAL_RISK_CAP_ZERO",
            target_units=0,
            score_cap_units=0,
            effective_cap_units=0,
            score_authority="DIAGNOSTIC_ONLY",
            exit_latch_status_after="EXIT_DUE_LATCHED",
        )
    if score.score_status != "EVALUATED" or score.primary.net_signal_ppm is None:
        return _result(
            score=score,
            snapshot=snapshot,
            action="NO_DECISION",
            reason_code="PRIMARY_SCORE_NOT_EVALUABLE",
            target_units=snapshot.current_units,
            score_cap_units=None,
            effective_cap_units=external_ceiling,
        )

    # Account, liquidity, and other external caps remain binding in every band
    # and do not wait for a score confirmation streak.
    if snapshot.current_units > external_ceiling:
        return _reduce_or_block(
            score=score,
            snapshot=snapshot,
            target=external_ceiling,
            score_cap=None,
            effective_cap=external_ceiling,
            reason="EXTERNAL_CAP_REDUCTION",
            score_authority="DIAGNOSTIC_ONLY",
        )

    original = score.episode.original_approved_units
    band = score.primary.band
    state = score.updated_state
    policy = _sealed_action_policy()
    if band == "HOLD":
        return _result(
            score=score,
            snapshot=snapshot,
            action="HOLD",
            reason_code="PRIMARY_SCORE_HOLD_BAND",
            target_units=snapshot.current_units,
            score_cap_units=snapshot.current_units,
            effective_cap_units=min(snapshot.current_units, external_ceiling),
        )

    if band == "RESTORE_ELIGIBLE":
        score_cap = original
        effective_cap = min(score_cap, external_ceiling)
        if snapshot.current_units >= effective_cap:
            return _result(
                score=score,
                snapshot=snapshot,
                action="HOLD",
                reason_code="ALREADY_AT_RESTORE_CAP",
                target_units=snapshot.current_units,
                score_cap_units=score_cap,
                effective_cap_units=effective_cap,
            )
        if state.above_70_streak < policy.confirmation_complete_sessions:
            return _result(
                score=score,
                snapshot=snapshot,
                action="HOLD",
                reason_code="WAITING_FOR_RECOVERY_CONFIRMATION",
                target_units=snapshot.current_units,
                score_cap_units=score_cap,
                effective_cap_units=effective_cap,
            )
        tiers = sorted(
            {
                max(1, ceil_fraction(original, numerator, denominator))
                for numerator, denominator in policy.recovery_tier_fractions
            }
        )
        next_tier = next(
            (value for value in tiers if snapshot.current_units < value <= effective_cap),
            None,
        )
        if next_tier is None:
            return _result(
                score=score,
                snapshot=snapshot,
                action="HOLD",
                reason_code="RECOVERY_READD_CAP_BINDING",
                target_units=snapshot.current_units,
                score_cap_units=score_cap,
                effective_cap_units=effective_cap,
            )
        blocked = _adjustment_block_reason(snapshot)
        if blocked is not None:
            return _result(
                score=score,
                snapshot=snapshot,
                action="ACTION_BLOCKED",
                reason_code=blocked,
                target_units=snapshot.current_units,
                score_cap_units=score_cap,
                effective_cap_units=effective_cap,
            )
        return _result(
            score=score,
            snapshot=snapshot,
            action="RECOVERY_READD",
            reason_code="RECOVERY_READD_ONE_TIER",
            target_units=next_tier,
            score_cap_units=score_cap,
            effective_cap_units=effective_cap,
        )

    if band not in {"DEFENSIVE_1", "DEFENSIVE_2"}:
        raise ManagementResearchError("PRIMARY_SCORE_BAND_INVALID")
    if band == "DEFENSIVE_1":
        score_cap = max(
            policy.non_exit_minimum_units,
            ceil_fraction(
                original,
                policy.defensive_1_cap_numerator,
                policy.defensive_1_cap_denominator,
            ),
        )
        confirmed = (
            state.below_40_streak >= policy.confirmation_complete_sessions
        )
    else:
        score_cap = max(
            policy.non_exit_minimum_units,
            ceil_fraction(
                original,
                policy.defensive_2_cap_numerator,
                policy.defensive_2_cap_denominator,
            ),
        )
        confirmed = (
            state.below_20_streak >= policy.confirmation_complete_sessions
        )
    effective_cap = min(score_cap, external_ceiling)
    if not confirmed:
        return _result(
            score=score,
            snapshot=snapshot,
            action="HOLD",
            reason_code="WAITING_FOR_CONFIRMATION",
            target_units=snapshot.current_units,
            score_cap_units=score_cap,
            effective_cap_units=effective_cap,
        )
    if snapshot.current_units > effective_cap:
        return _reduce_or_block(
            score=score,
            snapshot=snapshot,
            target=effective_cap,
            score_cap=score_cap,
            effective_cap=effective_cap,
            reason="CONFIRMED_DEFENSIVE_REDUCTION",
        )
    if snapshot.current_units == original and score_cap == original:
        reason = "NO_COMPLETE_UNIT_REDUCTION_AVAILABLE"
    elif snapshot.current_units < effective_cap:
        reason = "WEAK_ZONE_ADD_FORBIDDEN"
    else:
        reason = "ALREADY_AT_DEFENSIVE_CAP"
    return _result(
        score=score,
        snapshot=snapshot,
        action="HOLD",
        reason_code=reason,
        target_units=snapshot.current_units,
        score_cap_units=score_cap,
        effective_cap_units=effective_cap,
    )


__all__ = [
    "ACTION_RESULT_SCHEMA_VERSION",
    "MAX_CROSS_LEG_RECEIVE_SKEW_NS",
    "MAX_QUOTE_EVENT_AGE_NS",
    "MAX_QUOTE_AGE_NS",
    "evaluate_hypothetical_management_action_f0",
]
