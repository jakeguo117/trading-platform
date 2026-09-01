"""Pure composition workflow for visible GLD management Research F0 runs."""

from __future__ import annotations

from . import (
    derive_management_score_f0,
    evaluate_hypothetical_management_action_f0,
    validate_management_score_observation_f0,
)
from .canonical import canonical_json_bytes, canonical_json_sha256
from .contracts import ManagementActionSnapshotF0
from .validation import (
    TERMINAL_OVERRIDE_SCHEMA_VERSION,
    validate_management_action_snapshot_f0,
    validate_management_terminal_override_f0,
)


RUN_SCHEMA_VERSION = "GLD_MANAGEMENT_RESEARCH_RUN_F0_V1"


def run_management_research_f0(
    observation_document: object,
    action_snapshot_document: object,
) -> dict[str, object]:
    """Run the deterministic score and hypothetical action as one document."""

    observation = validate_management_score_observation_f0(observation_document)
    score = derive_management_score_f0(observation)
    if (
        type(action_snapshot_document) is dict
        and action_snapshot_document.get("schema_version")
        == TERMINAL_OVERRIDE_SCHEMA_VERSION
    ):
        action_snapshot = validate_management_terminal_override_f0(
            action_snapshot_document, score
        )
    else:
        action_snapshot = validate_management_action_snapshot_f0(
            action_snapshot_document, score
        )
    action = evaluate_hypothetical_management_action_f0(
        score, action_snapshot
    )
    primary = score.primary
    explanation = {
        "raw_fact_summary": {
            "calendar_id": observation.calendar_id,
            "calendar_version_sha256": observation.calendar_version_sha256,
            "prior_close_nano_usd": observation.daily_bars[-2].close_nano_usd,
            "current_daily_bar": observation.daily_bars[-1].as_dict(),
            "episode": observation.episode.as_dict(),
            "previous_state": observation.previous_state.as_dict(),
            "action_snapshot": (
                {
                    "snapshot_kind": "SCHEDULED_1045_ET",
                    "action_trading_date": action_snapshot.action_trading_date,
                    "action_utc_ns": action_snapshot.action_utc_ns,
                    "current_units": action_snapshot.current_units,
                    "account_risk_cap_units": action_snapshot.account_risk_cap_units,
                    "liquidity_cap_units": action_snapshot.liquidity_cap_units,
                    "external_risk_cap_units": action_snapshot.external_risk_cap_units,
                    "dte": action_snapshot.dte,
                    "fee_facts_status": action_snapshot.fee_facts_status,
                    "reconciliation_status": action_snapshot.reconciliation_status,
                    "override_status": action_snapshot.override_status,
                    "quotes": [quote.as_dict() for quote in action_snapshot.quotes],
                }
                if isinstance(action_snapshot, ManagementActionSnapshotF0)
                else {
                    "snapshot_kind": "EVENT_DRIVEN_TERMINAL_OVERRIDE",
                    "event_utc_ns": action_snapshot.event_utc_ns,
                    "current_units": action_snapshot.current_units,
                    "override_status": action_snapshot.override_status,
                }
            ),
        },
        "indicator_facts": (
            None if score.indicators is None else score.indicators.as_dict()
        ),
        "dimension_contributions": (
            None if score.dimensions is None else score.dimensions.as_dict()
        ),
        "breakout_diagnostic": score.breakout_diagnostic.as_dict(),
        "primary_final_score": primary.as_dict(),
        "shadow_policy_results": [
            policy.as_dict() for policy in score.policy_results[1:]
        ],
        "updated_streak_state": score.updated_state.as_dict(),
        "position_action": {
            "current_units": action.current_units,
            "score_cap_units": action.score_cap_units,
            "effective_cap_units": action.effective_cap_units,
            "target_units": action.target_units,
            "quantity_change_units": action.quantity_change_units,
            "action": action.action,
            "reason_code": action.reason_code,
            "score_authority": action.score_authority,
            "exit_latch_status_before": action.exit_latch_status_before,
            "exit_latch_status_after": action.exit_latch_status_after,
            "action_policy_sha256": action.action_policy_sha256,
        },
    }
    body = {
        "schema_version": RUN_SCHEMA_VERSION,
        "classification": "RESEARCH_ONLY",
        "authority_status": "NO_DECISION_EFFECT",
        "actionable": False,
        "broker_order_count": 0,
        "instrument_id": score.instrument_id,
        "trading_date": score.trading_date,
        "observation_input_sha256": observation.input_sha256,
        "score_result": score.as_dict(),
        "action_result": action.as_dict(),
        "explanation": explanation,
    }
    run_hash = canonical_json_sha256(body)
    return {**body, "run_sha256": run_hash}


def canonical_run_bytes(document: dict[str, object]) -> bytes:
    """Return canonical bytes for one already-composed run document."""

    return canonical_json_bytes(document)


__all__ = [
    "RUN_SCHEMA_VERSION",
    "canonical_run_bytes",
    "run_management_research_f0",
]
