"""Command line entry point for local GLD management Research F0 evidence."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .canonical import canonical_json_bytes, canonical_json_sha256
from .errors import ManagementResearchError
from .examples import (
    ACCEPTANCE_SCENARIOS_F0,
    build_synthetic_action_snapshot_f0,
    build_synthetic_observation_f0,
)
from .publish import publish_artifacts, read_canonical_json_file
from .render_html import (
    render_acceptance_index,
    render_engineering_evidence_index,
    render_management_score_card,
)
from .scoring import derive_management_score_f0
from .validation import (
    TERMINAL_OVERRIDE_SCHEMA_VERSION,
    validate_management_action_snapshot_f0,
    validate_management_score_observation_f0,
    validate_management_terminal_override_f0,
)
from .workflow import run_management_research_f0


def _single_artifacts(
    observation_path: Path,
    action_snapshot_path: Path,
) -> dict[str, bytes]:
    observation = read_canonical_json_file(observation_path)
    action_snapshot = read_canonical_json_file(action_snapshot_path)
    normalized_observation = validate_management_score_observation_f0(observation)
    score = derive_management_score_f0(normalized_observation)
    if (
        type(action_snapshot) is dict
        and action_snapshot.get("schema_version") == TERMINAL_OVERRIDE_SCHEMA_VERSION
    ):
        normalized_action = validate_management_terminal_override_f0(
            action_snapshot, score
        )
    else:
        normalized_action = validate_management_action_snapshot_f0(
            action_snapshot, score
        )
    normalized_observation_document = normalized_observation.as_dict()
    normalized_action_document = normalized_action.as_dict()
    run = run_management_research_f0(
        normalized_observation_document, normalized_action_document
    )
    return {
        "management-observation-f0.json": (
            canonical_json_bytes(normalized_observation_document) + b"\n"
        ),
        "management-action-snapshot-f0.json": (
            canonical_json_bytes(normalized_action_document) + b"\n"
        ),
        "management-score-f0.json": canonical_json_bytes(run) + b"\n",
        "management-score-f0.html": render_management_score_card(run).encode("utf-8"),
    }


def _actual_summary(run: dict[str, object]) -> tuple[str, int, int, str]:
    action = run.get("action_result")
    if type(action) is not dict:
        raise ManagementResearchError("ACCEPTANCE_ACTION_RESULT_INVALID")
    actual_action = action.get("action")
    actual_current = action.get("current_units")
    actual_target = action.get("target_units")
    actual_reason = action.get("reason_code")
    if (
        type(actual_action) is not str
        or type(actual_current) is not int
        or type(actual_target) is not int
        or type(actual_reason) is not str
    ):
        raise ManagementResearchError("ACCEPTANCE_ACTION_RESULT_INVALID")
    return actual_action, actual_current, actual_target, actual_reason


def _score_text(score_bp: object) -> str:
    if type(score_bp) is not int:
        return "NOT_EVALUABLE"
    return f"{score_bp // 100}.{score_bp % 100:02d}"


def _compact_previous_state_text(observation: dict[str, object]) -> str:
    state = observation.get("previous_state")
    if type(state) is not dict:
        raise ManagementResearchError("ACCEPTANCE_PREVIOUS_STATE_INVALID")
    fields = {
        "lineage": state.get("lineage_kind"),
        "previous_band": state.get("previous_primary_band"),
        "below40": state.get("below_40_streak"),
        "below20": state.get("below_20_streak"),
        "above70": state.get("above_70_streak"),
        "latch": state.get("exit_latch_status"),
    }
    if (
        any(type(fields[name]) is not str for name in ("lineage", "previous_band", "latch"))
        or any(type(fields[name]) is not int for name in ("below40", "below20", "above70"))
    ):
        raise ManagementResearchError("ACCEPTANCE_PREVIOUS_STATE_INVALID")
    return (
        f"lineage={fields['lineage']}; previous_band={fields['previous_band']}; "
        f"streaks below40={fields['below40']}, below20={fields['below20']}, "
        f"above70={fields['above70']}; latch={fields['latch']}"
    )


def _acceptance_artifacts() -> dict[str, bytes]:
    artifacts: dict[str, bytes] = {}
    rows: list[dict[str, object]] = []
    manifest_scenarios: list[dict[str, object]] = []
    mismatch = False
    for specification in ACCEPTANCE_SCENARIOS_F0:
        scenario_id = str(specification["scenario_id"])
        observation = build_synthetic_observation_f0(scenario_id)
        action_snapshot = build_synthetic_action_snapshot_f0(
            observation, scenario_id
        )
        run = run_management_research_f0(observation, action_snapshot)
        (
            actual_action,
            actual_current,
            actual_target,
            actual_reason,
        ) = _actual_summary(run)
        score_result = run["score_result"]
        if type(score_result) is not dict:
            raise ManagementResearchError("ACCEPTANCE_SCORE_RESULT_INVALID")
        policy_results = score_result.get("policy_results")
        primary = (
            policy_results[0]
            if type(policy_results) is list
            and policy_results
            and type(policy_results[0]) is dict
            else {}
        )
        actual_score_bp = primary.get("display_score_bp")
        actual_band = primary.get("band")
        expected_action = str(specification["expected_action"])
        expected_target = int(specification["expected_target_units"])
        expected_reason = str(specification["expected_reason_code"])
        expected_score_bp = specification["expected_display_score_bp"]
        expected_band = str(specification["expected_band"])
        passed = (
            actual_action == expected_action
            and actual_target == expected_target
            and actual_reason == expected_reason
            and actual_score_bp == expected_score_bp
            and actual_band == expected_band
        )
        mismatch = mismatch or not passed
        evidence_status = (
            "AUTOMATED_EVIDENCE_PASS" if passed else "AUTOMATED_EVIDENCE_FAIL"
        )
        expected_text = (
            f"Score {_score_text(expected_score_bp)} {expected_band}; "
            f"{expected_action} target={expected_target} reason={expected_reason}"
        )
        actual_text = (
            f"Score {_score_text(actual_score_bp)} {actual_band}; "
            f"{actual_action} target={actual_target} reason={actual_reason}"
        )
        result_relative = f"{scenario_id}/management-score-f0.json"
        card_relative = f"{scenario_id}/management-score-f0.html"
        observation_relative = f"{scenario_id}/management-observation-f0.json"
        action_snapshot_relative = (
            f"{scenario_id}/management-action-snapshot-f0.json"
        )
        root_prefix = "management-score-f0-acceptance/"
        artifacts[root_prefix + observation_relative] = (
            canonical_json_bytes(observation) + b"\n"
        )
        artifacts[root_prefix + action_snapshot_relative] = (
            canonical_json_bytes(action_snapshot) + b"\n"
        )
        artifacts[root_prefix + result_relative] = canonical_json_bytes(run) + b"\n"
        artifacts[root_prefix + card_relative] = render_management_score_card(run).encode(
            "utf-8"
        )
        key_input = (
            f"Score {_score_text(primary.get('display_score_bp'))}; "
            f"current {action_snapshot['current_units']}; "
            f"{_compact_previous_state_text(observation)}"
        )
        row = {
            "scenario_id": scenario_id,
            "purpose": specification["purpose"],
            "key_input": key_input,
            "expected": expected_text,
            "actual": actual_text,
            "actual_display_score_bp": actual_score_bp,
            "actual_action": actual_action,
            "actual_target_units": actual_target,
            "actual_reason_code": actual_reason,
            "current_units": actual_current,
            "automated_evidence_status": evidence_status,
            "owner_status": "OWNER_ACCEPTANCE_PENDING",
            "input_sha256": run["observation_input_sha256"],
            "action_input_sha256": run["action_result"][
                "action_snapshot_sha256"
            ],
            "formula_sha256": score_result["formula_catalog_sha256"],
            "policy_sha256": score_result["policy_set_sha256"],
            "result_sha256": run["run_sha256"],
            "result_href": result_relative,
            "card_href": card_relative,
            "observation_href": observation_relative,
            "action_snapshot_href": action_snapshot_relative,
        }
        rows.append(row)
        manifest_scenarios.append(
            {
                "scenario_id": scenario_id,
                "purpose": specification["purpose"],
                "key_input": key_input,
                "expected": {
                    "display_score_bp": expected_score_bp,
                    "band": expected_band,
                    "action": expected_action,
                    "target_units": expected_target,
                    "reason_code": expected_reason,
                },
                "actual": {
                    "display_score_bp": actual_score_bp,
                    "band": actual_band,
                    "action": actual_action,
                    "target_units": actual_target,
                    "reason_code": actual_reason,
                },
                "automated_evidence_status": evidence_status,
                "owner_status": "OWNER_ACCEPTANCE_PENDING",
                "input_sha256": run["observation_input_sha256"],
                "action_input_sha256": run["action_result"][
                    "action_snapshot_sha256"
                ],
                "formula_sha256": score_result["formula_catalog_sha256"],
                "policy_sha256": score_result["policy_set_sha256"],
                "result_sha256": run["run_sha256"],
                "observation_path": root_prefix + observation_relative,
                "action_snapshot_path": root_prefix + action_snapshot_relative,
                "result_path": root_prefix + result_relative,
                "card_path": root_prefix + card_relative,
            }
        )
    if mismatch:
        raise ManagementResearchError("ACCEPTANCE_SCENARIO_MISMATCH")
    manifest_body = {
        "schema_version": "GLD_MANAGEMENT_RESEARCH_ACCEPTANCE_MANIFEST_F0_V1",
        "classification": "RESEARCH_ONLY",
        "authority_status": "NO_DECISION_EFFECT",
        "actionable": False,
        "broker_order_count": 0,
        "owner_status": "OWNER_ACCEPTANCE_PENDING",
        "scenarios": manifest_scenarios,
    }
    manifest = {
        **manifest_body,
        "manifest_sha256": canonical_json_sha256(manifest_body),
    }
    artifacts["acceptance-manifest.json"] = canonical_json_bytes(manifest) + b"\n"
    artifacts["management-score-f0-acceptance/index.html"] = render_acceptance_index(
        rows
    ).encode("utf-8")
    artifacts[
        "management-score-f0-acceptance/engineering-evidence.html"
    ] = render_engineering_evidence_index(rows).encode("utf-8")
    return artifacts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_gld_management_score_f0",
        description="Run deterministic, non-actionable GLD management Research F0.",
    )
    parser.add_argument("--observation", type=Path)
    parser.add_argument("--action-snapshot", type=Path)
    parser.add_argument("--demo-acceptance", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run single-input or synthetic acceptance mode."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.demo_acceptance:
        if arguments.observation is not None or arguments.action_snapshot is not None:
            parser.error("--demo-acceptance cannot be combined with input files")
    elif arguments.observation is None or arguments.action_snapshot is None:
        parser.error("single mode requires --observation and --action-snapshot")
    try:
        artifacts = (
            _acceptance_artifacts()
            if arguments.demo_acceptance
            else _single_artifacts(arguments.observation, arguments.action_snapshot)
        )
        publish_artifacts(arguments.output, artifacts)
    except ManagementResearchError as exc:
        print(f"ERROR {exc.reason_code}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"ERROR CLI_FAILURE {type(exc).__name__}", file=sys.stderr)
        return 2
    print(str(arguments.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
