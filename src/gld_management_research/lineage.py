"""Canonical previous-score/action lineage for Research F0 streak state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import re

from .canonical import canonical_json_bytes, canonical_json_sha256
from .contracts import ManagementEpisodeF0, PreviousManagementStateF0
from .errors import ManagementResearchError
from .indicators import FORMULA_CATALOG_SHA256
from .math import PPM, clamp, round_half_even_div
from .policy import (
    POLICY_SET_SHA256,
    action_policy_f0,
    score_policy_definitions_f0,
    verify_policy_set_f0,
)


PREVIOUS_STATE_SCHEMA_VERSION = "GLD_MANAGEMENT_STREAK_STATE_F0_V1"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_ACTION_POLICY_ACCESSOR_IMPORT = action_policy_f0
_SCORE_POLICY_ACCESSOR_IMPORT = score_policy_definitions_f0
_POLICY_VERIFIER_IMPORT = verify_policy_set_f0


def _sealed_action_policy():
    if (
        action_policy_f0 is not _ACTION_POLICY_ACCESSOR_IMPORT
        or verify_policy_set_f0 is not _POLICY_VERIFIER_IMPORT
    ):
        raise ManagementResearchError("POLICY_RUNTIME_IDENTITY_MISMATCH")
    _POLICY_VERIFIER_IMPORT()
    return _ACTION_POLICY_ACCESSOR_IMPORT()


def _sealed_score_policies():
    if (
        score_policy_definitions_f0 is not _SCORE_POLICY_ACCESSOR_IMPORT
        or verify_policy_set_f0 is not _POLICY_VERIFIER_IMPORT
    ):
        raise ManagementResearchError("POLICY_RUNTIME_IDENTITY_MISMATCH")
    _POLICY_VERIFIER_IMPORT()
    return _SCORE_POLICY_ACCESSOR_IMPORT()

_SCORE_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "authority_status",
        "actionable",
        "broker_order_count",
        "instrument_id",
        "trading_date",
        "observation_utc_ns",
        "episode",
        "input_sha256",
        "formula_catalog_sha256",
        "policy_set_sha256",
        "score_status",
        "reason_codes",
        "indicators",
        "dimensions",
        "breakout_diagnostic",
        "policy_results",
        "updated_state",
        "result_sha256",
    }
)
_POLICY_RESULT_KEYS = frozenset(
    {
        "policy_id",
        "weights_ppm",
        "volume_enabled",
        "contributions_ppm",
        "net_signal_ppm",
        "gross_strength_ppm",
        "agreement_ppm",
        "display_score_bp",
        "band",
        "action_authority",
        "policy_result_sha256",
    }
)
_UPDATED_STATE_KEYS = frozenset(
    {
        "trading_date",
        "below_40_streak",
        "below_20_streak",
        "above_70_streak",
        "exit_latch_status",
    }
)
_ACTION_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "authority_status",
        "actionable",
        "broker_order_count",
        "score_authority",
        "action",
        "reason_code",
        "current_units",
        "target_units",
        "quantity_change_units",
        "score_cap_units",
        "effective_cap_units",
        "exit_latch_status_before",
        "exit_latch_status_after",
        "input_score_sha256",
        "action_snapshot_sha256",
        "action_policy_sha256",
        "action_result_sha256",
    }
)
_STATE_KEYS = frozenset(
    {
        "schema_version",
        "lineage_kind",
        "previous_trading_date",
        "episode_id",
        "formula_catalog_sha256",
        "policy_set_sha256",
        "previous_score_result_sha256",
        "previous_action_result_sha256",
        "previous_primary_band",
        "below_40_streak",
        "below_20_streak",
        "above_70_streak",
        "exit_latch_status",
        "previous_score_result",
        "previous_action_result",
        "state_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class _PriorScore:
    canonical_bytes: bytes
    result_sha256: str
    trading_date: str
    episode_document: dict[str, object]
    episode_id: str
    primary_band: str
    below_40_streak: int
    below_20_streak: int
    above_70_streak: int
    exit_latch_status: str


@dataclass(frozen=True, slots=True)
class _PriorAction:
    canonical_bytes: bytes
    action_result_sha256: str
    exit_latch_status_after: str


def _closed(value: object, keys: frozenset[str], reason: str) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != keys:
        raise ManagementResearchError(reason)
    return value


def _integer(value: object, reason: str, *, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ManagementResearchError(reason)
    return value


def _text(value: object, reason: str) -> str:
    if type(value) is not str or not value.isascii():
        raise ManagementResearchError(reason)
    return value


def _sha(value: object, reason: str) -> str:
    text = _text(value, reason)
    if _SHA256_RE.fullmatch(text) is None:
        raise ManagementResearchError(reason)
    return text


def _score_band(net_signal_ppm: int) -> str:
    policy = _sealed_action_policy()
    if net_signal_ppm >= policy.restore_threshold_ppm:
        return "RESTORE_ELIGIBLE"
    if net_signal_ppm >= policy.defensive_1_threshold_ppm:
        return "HOLD"
    if net_signal_ppm >= policy.defensive_2_threshold_ppm:
        return "DEFENSIVE_1"
    return "DEFENSIVE_2"


def _validate_prior_score(document: object) -> _PriorScore:
    if verify_policy_set_f0 is not _POLICY_VERIFIER_IMPORT:
        raise ManagementResearchError("POLICY_RUNTIME_IDENTITY_MISMATCH")
    _POLICY_VERIFIER_IMPORT()
    canonical_json_bytes(document)
    score = _closed(document, _SCORE_KEYS, "PREVIOUS_SCORE_SCHEMA_INVALID")
    unsigned = dict(score)
    result_hash = _sha(
        unsigned.pop("result_sha256", None), "PREVIOUS_SCORE_HASH_INVALID"
    )
    if canonical_json_sha256(unsigned) != result_hash:
        raise ManagementResearchError("PREVIOUS_SCORE_HASH_INVALID")
    if (
        score["schema_version"] != "GLD_MANAGEMENT_SCORE_RESULT_F0_V1"
        or score["classification"] != "RESEARCH_ONLY"
        or score["authority_status"] != "NO_DECISION_EFFECT"
        or score["actionable"] is not False
        or type(score["broker_order_count"]) is not int
        or score["broker_order_count"] != 0
        or score["instrument_id"] != "GLD"
        or score["formula_catalog_sha256"] != FORMULA_CATALOG_SHA256
        or score["policy_set_sha256"] != POLICY_SET_SHA256
    ):
        raise ManagementResearchError("PREVIOUS_SCORE_AUTHORITY_INVALID")
    trading_date = _text(score["trading_date"], "PREVIOUS_SCORE_DATE_INVALID")
    try:
        if date.fromisoformat(trading_date).isoformat() != trading_date:
            raise ValueError
    except ValueError as exc:
        raise ManagementResearchError("PREVIOUS_SCORE_DATE_INVALID") from exc
    episode = score["episode"]
    if type(episode) is not dict:
        raise ManagementResearchError("PREVIOUS_SCORE_EPISODE_INVALID")
    episode_id = _text(episode.get("episode_id"), "PREVIOUS_SCORE_EPISODE_INVALID")
    policies = score["policy_results"]
    definitions = _sealed_score_policies()
    if type(policies) is not list or len(policies) != len(definitions):
        raise ManagementResearchError("PREVIOUS_SCORE_POLICY_INVALID")
    for raw_policy, definition in zip(policies, definitions):
        result = _closed(
            raw_policy, _POLICY_RESULT_KEYS, "PREVIOUS_SCORE_POLICY_INVALID"
        )
        policy_unsigned = dict(result)
        policy_hash = _sha(
            policy_unsigned.pop("policy_result_sha256", None),
            "PREVIOUS_SCORE_POLICY_INVALID",
        )
        if (
            canonical_json_sha256(policy_unsigned) != policy_hash
            or result["policy_id"] != definition.policy_id
            or result["weights_ppm"]
            != {
                "structure": definition.structure_weight_ppm,
                "trend": definition.trend_weight_ppm,
                "momentum": definition.momentum_weight_ppm,
            }
            or result["volume_enabled"] is not definition.volume_enabled
            or result["action_authority"] is not definition.action_authority
        ):
            raise ManagementResearchError("PREVIOUS_SCORE_POLICY_INVALID")
    primary = policies[0]
    net = primary["net_signal_ppm"]
    band = primary["band"]
    score_status = score["score_status"]
    if score_status == "EVALUATED":
        net_value = _integer(
            net, "PREVIOUS_SCORE_PRIMARY_INVALID", minimum=-PPM, maximum=PPM
        )
        expected_band = _score_band(net_value)
        expected_display = clamp(
            round_half_even_div((net_value + PPM) * 10_000, 2 * PPM),
            0,
            10_000,
        )
        if band != expected_band or primary["display_score_bp"] != expected_display:
            raise ManagementResearchError("PREVIOUS_SCORE_PRIMARY_INVALID")
    elif score_status == "NOT_EVALUABLE":
        if net is not None or band != "NOT_EVALUABLE" or primary["display_score_bp"] is not None:
            raise ManagementResearchError("PREVIOUS_SCORE_PRIMARY_INVALID")
    else:
        raise ManagementResearchError("PREVIOUS_SCORE_STATUS_INVALID")
    updated = _closed(
        score["updated_state"],
        _UPDATED_STATE_KEYS,
        "PREVIOUS_SCORE_STATE_INVALID",
    )
    if updated["trading_date"] != trading_date:
        raise ManagementResearchError("PREVIOUS_SCORE_STATE_INVALID")
    maximum = _sealed_action_policy().confirmation_complete_sessions
    below_40 = _integer(
        updated["below_40_streak"],
        "PREVIOUS_SCORE_STATE_INVALID",
        maximum=maximum,
    )
    below_20 = _integer(
        updated["below_20_streak"],
        "PREVIOUS_SCORE_STATE_INVALID",
        maximum=maximum,
    )
    above_70 = _integer(
        updated["above_70_streak"],
        "PREVIOUS_SCORE_STATE_INVALID",
        maximum=maximum,
    )
    latch = _text(
        updated["exit_latch_status"], "PREVIOUS_SCORE_STATE_INVALID"
    )
    if latch not in {"CLEAR", "EXIT_DUE_LATCHED"}:
        raise ManagementResearchError("PREVIOUS_SCORE_STATE_INVALID")
    streaks_match = (
        (latch == "EXIT_DUE_LATCHED" and (below_40, below_20, above_70) == (0, 0, 0))
        or (
            latch == "CLEAR"
            and (
                (band in {"HOLD", "NOT_EVALUABLE"} and (below_40, below_20, above_70) == (0, 0, 0))
                or (band == "RESTORE_ELIGIBLE" and below_40 == 0 and below_20 == 0 and above_70 >= 1)
                or (band == "DEFENSIVE_1" and below_40 >= 1 and below_20 == 0 and above_70 == 0)
                or (band == "DEFENSIVE_2" and 1 <= below_20 <= below_40 and above_70 == 0)
            )
        )
    )
    if not streaks_match:
        raise ManagementResearchError("PREVIOUS_SCORE_STATE_INVALID")
    return _PriorScore(
        canonical_bytes=canonical_json_bytes(score),
        result_sha256=result_hash,
        trading_date=trading_date,
        episode_document=episode,
        episode_id=episode_id,
        primary_band=band,
        below_40_streak=below_40,
        below_20_streak=below_20,
        above_70_streak=above_70,
        exit_latch_status=latch,
    )


def _validate_prior_action(document: object, score: _PriorScore) -> _PriorAction:
    canonical_json_bytes(document)
    action = _closed(document, _ACTION_KEYS, "PREVIOUS_ACTION_SCHEMA_INVALID")
    unsigned = dict(action)
    action_hash = _sha(
        unsigned.pop("action_result_sha256", None),
        "PREVIOUS_ACTION_HASH_INVALID",
    )
    if canonical_json_sha256(unsigned) != action_hash:
        raise ManagementResearchError("PREVIOUS_ACTION_HASH_INVALID")
    if (
        action["schema_version"] != "GLD_HYPOTHETICAL_MANAGEMENT_ACTION_F0_V1"
        or action["classification"] != "RESEARCH_ONLY"
        or action["authority_status"] != "NO_DECISION_EFFECT"
        or action["actionable"] is not False
        or type(action["broker_order_count"]) is not int
        or action["broker_order_count"] != 0
        or action["input_score_sha256"] != score.result_sha256
        or action["action_policy_sha256"] != POLICY_SET_SHA256
    ):
        raise ManagementResearchError("PREVIOUS_ACTION_AUTHORITY_INVALID")
    current = _integer(action["current_units"], "PREVIOUS_ACTION_UNITS_INVALID")
    target = _integer(action["target_units"], "PREVIOUS_ACTION_UNITS_INVALID")
    change = action["quantity_change_units"]
    if type(change) is not int or change != target - current:
        raise ManagementResearchError("PREVIOUS_ACTION_UNITS_INVALID")
    before = _text(
        action["exit_latch_status_before"], "PREVIOUS_ACTION_LATCH_INVALID"
    )
    after = _text(
        action["exit_latch_status_after"], "PREVIOUS_ACTION_LATCH_INVALID"
    )
    if (
        before != score.exit_latch_status
        or before not in {"CLEAR", "EXIT_DUE_LATCHED"}
        or after not in {"CLEAR", "EXIT_DUE_LATCHED"}
        or (before == "EXIT_DUE_LATCHED" and after != "EXIT_DUE_LATCHED")
        or (action["action"] == "EXIT_DUE" and after != "EXIT_DUE_LATCHED")
        or (after == "EXIT_DUE_LATCHED" and before == "CLEAR" and action["action"] != "EXIT_DUE")
    ):
        raise ManagementResearchError("PREVIOUS_ACTION_LATCH_INVALID")
    return _PriorAction(
        canonical_bytes=canonical_json_bytes(action),
        action_result_sha256=action_hash,
        exit_latch_status_after=after,
    )


def _state_document(
    *,
    lineage_kind: str,
    previous_trading_date: str,
    episode_id: str,
    previous_score: _PriorScore | None,
    previous_action: _PriorAction | None,
    primary_band: str,
    below_40_streak: int,
    below_20_streak: int,
    above_70_streak: int,
    exit_latch_status: str,
) -> dict[str, object]:
    body = {
        "schema_version": PREVIOUS_STATE_SCHEMA_VERSION,
        "lineage_kind": lineage_kind,
        "previous_trading_date": previous_trading_date,
        "episode_id": episode_id,
        "formula_catalog_sha256": FORMULA_CATALOG_SHA256,
        "policy_set_sha256": POLICY_SET_SHA256,
        "previous_score_result_sha256": (
            None if previous_score is None else previous_score.result_sha256
        ),
        "previous_action_result_sha256": (
            None if previous_action is None else previous_action.action_result_sha256
        ),
        "previous_primary_band": primary_band,
        "below_40_streak": below_40_streak,
        "below_20_streak": below_20_streak,
        "above_70_streak": above_70_streak,
        "exit_latch_status": exit_latch_status,
        "previous_score_result": (
            None
            if previous_score is None
            else json.loads(previous_score.canonical_bytes)
        ),
        "previous_action_result": (
            None
            if previous_action is None
            else json.loads(previous_action.canonical_bytes)
        ),
    }
    return {**body, "state_sha256": canonical_json_sha256(body)}


def derive_genesis_management_state_f0(
    episode_document: dict[str, object], previous_trading_date: str
) -> dict[str, object]:
    """Create the only allowed zero-streak genesis receipt for an episode."""

    canonical_json_bytes(episode_document)
    if (
        type(episode_document) is not dict
        or episode_document.get("management_start_date") != previous_trading_date
    ):
        raise ManagementResearchError("PREVIOUS_STATE_GENESIS_INVALID")
    episode_id = _text(
        episode_document.get("episode_id"), "PREVIOUS_STATE_GENESIS_INVALID"
    )
    return _state_document(
        lineage_kind="GENESIS",
        previous_trading_date=previous_trading_date,
        episode_id=episode_id,
        previous_score=None,
        previous_action=None,
        primary_band="HOLD",
        below_40_streak=0,
        below_20_streak=0,
        above_70_streak=0,
        exit_latch_status="CLEAR",
    )


def derive_management_state_transition_f0(
    previous_score_result: object,
    previous_action_result: object,
) -> dict[str, object]:
    """Derive next-day streak/latch state from two canonical F0 results."""

    score = _validate_prior_score(previous_score_result)
    action = _validate_prior_action(previous_action_result, score)
    terminal = action.exit_latch_status_after == "EXIT_DUE_LATCHED"
    return _state_document(
        lineage_kind="PRIOR_RESULT",
        previous_trading_date=score.trading_date,
        episode_id=score.episode_id,
        previous_score=score,
        previous_action=action,
        primary_band=score.primary_band,
        below_40_streak=0 if terminal else score.below_40_streak,
        below_20_streak=0 if terminal else score.below_20_streak,
        above_70_streak=0 if terminal else score.above_70_streak,
        exit_latch_status=action.exit_latch_status_after,
    )


def validate_previous_management_state_f0(
    document: object,
    *,
    episode: ManagementEpisodeF0,
    previous_trading_date: str,
) -> PreviousManagementStateF0:
    """Validate a genesis or prior-result state and return an immutable receipt."""

    canonical_json_bytes(document)
    state = _closed(document, _STATE_KEYS, "PREVIOUS_STATE_SCHEMA_INVALID")
    lineage_kind = state.get("lineage_kind")
    if lineage_kind == "GENESIS":
        expected = derive_genesis_management_state_f0(
            episode.as_dict(), previous_trading_date
        )
    elif lineage_kind == "PRIOR_RESULT":
        expected = derive_management_state_transition_f0(
            state.get("previous_score_result"),
            state.get("previous_action_result"),
        )
        if (
            expected["previous_trading_date"] != previous_trading_date
            or canonical_json_bytes(
                state["previous_score_result"]["episode"]
            )
            != canonical_json_bytes(episode.as_dict())
        ):
            raise ManagementResearchError("PREVIOUS_STATE_LINEAGE_MISMATCH")
    else:
        raise ManagementResearchError("PREVIOUS_STATE_LINEAGE_INVALID")
    if canonical_json_bytes(state) != canonical_json_bytes(expected):
        raise ManagementResearchError("PREVIOUS_STATE_LINEAGE_MISMATCH")
    score_document = state["previous_score_result"]
    action_document = state["previous_action_result"]
    return PreviousManagementStateF0(
        schema_version=PREVIOUS_STATE_SCHEMA_VERSION,
        lineage_kind=lineage_kind,
        previous_trading_date=state["previous_trading_date"],
        episode_id=state["episode_id"],
        formula_catalog_sha256=state["formula_catalog_sha256"],
        policy_set_sha256=state["policy_set_sha256"],
        previous_score_result_sha256=state["previous_score_result_sha256"],
        previous_action_result_sha256=state["previous_action_result_sha256"],
        previous_primary_band=state["previous_primary_band"],
        below_40_streak=state["below_40_streak"],
        below_20_streak=state["below_20_streak"],
        above_70_streak=state["above_70_streak"],
        exit_latch_status=state["exit_latch_status"],
        previous_score_result_canonical_bytes=(
            None if score_document is None else canonical_json_bytes(score_document)
        ),
        previous_action_result_canonical_bytes=(
            None if action_document is None else canonical_json_bytes(action_document)
        ),
        state_sha256=state["state_sha256"],
    )


__all__ = [
    "PREVIOUS_STATE_SCHEMA_VERSION",
    "derive_genesis_management_state_f0",
    "derive_management_state_transition_f0",
    "validate_previous_management_state_f0",
]
