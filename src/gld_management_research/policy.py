"""Sealed score and action policy for GLD management Research F0."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .canonical import canonical_json_bytes
from .errors import ManagementResearchError


POLICY_SET_VERSION = "GLD_MANAGEMENT_POLICY_SET_F0_V1"


@dataclass(frozen=True, slots=True)
class ScorePolicyDefinitionF0:
    policy_id: str
    structure_weight_ppm: int
    trend_weight_ppm: int
    momentum_weight_ppm: int
    volume_enabled: bool
    action_authority: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "weights_ppm": [
                self.structure_weight_ppm,
                self.trend_weight_ppm,
                self.momentum_weight_ppm,
            ],
            "volume_enabled": self.volume_enabled,
            "action_authority": self.action_authority,
        }


@dataclass(frozen=True, slots=True)
class ActionPolicyF0:
    restore_threshold_ppm: int
    defensive_1_threshold_ppm: int
    defensive_2_threshold_ppm: int
    confirmation_complete_sessions: int
    defensive_1_cap_numerator: int
    defensive_1_cap_denominator: int
    defensive_2_cap_numerator: int
    defensive_2_cap_denominator: int
    recovery_tier_fractions: tuple[tuple[int, int], ...]
    non_exit_minimum_units: int
    max_quote_event_age_ns: int
    max_quote_receive_age_ns: int
    max_cross_leg_receive_skew_ns: int
    scheduled_action_hour_et: int
    scheduled_action_minute_et: int

    def as_dict(self) -> dict[str, object]:
        return {
            "threshold_status": "TRIAL_CANDIDATE_NOT_VERIFIED",
            "thresholds_net_signal_ppm": {
                "restore_eligible_inclusive": self.restore_threshold_ppm,
                "defensive_1_exclusive": self.defensive_1_threshold_ppm,
                "defensive_2_exclusive": self.defensive_2_threshold_ppm,
            },
            "confirmation_complete_sessions": self.confirmation_complete_sessions,
            "score_cap_fractions": {
                "DEFENSIVE_1": [
                    self.defensive_1_cap_numerator,
                    self.defensive_1_cap_denominator,
                ],
                "DEFENSIVE_2": [
                    self.defensive_2_cap_numerator,
                    self.defensive_2_cap_denominator,
                ],
            },
            "non_exit_minimum_units": self.non_exit_minimum_units,
            "recovery_tier_fractions": [
                [numerator, denominator]
                for numerator, denominator in self.recovery_tier_fractions
            ],
            "quote_quality": {
                "max_event_age_ns": self.max_quote_event_age_ns,
                "max_receive_age_ns": self.max_quote_receive_age_ns,
                "max_cross_leg_receive_skew_ns": self.max_cross_leg_receive_skew_ns,
                "scheduled_snapshot_time_et": (
                    f"{self.scheduled_action_hour_et:02d}:"
                    f"{self.scheduled_action_minute_et:02d}"
                ),
            },
            "override_precedence": [
                "EXIT_DUE_LATCHED",
                "HARD_STOP",
                "EXPIRY_SAFETY",
                "CONFIRMED_INVALIDATION",
                "DATA_NOT_QUALIFIED",
                "RECONCILIATION_CONFLICT",
                "EXTERNAL_RISK_CAP_ZERO",
                "SCORE",
            ],
            "terminal_override_input": "GLD_MANAGEMENT_TERMINAL_OVERRIDE_F0_V1",
            "terminal_latch": "EXIT_DUE_LATCHED_NEVER_READD_SAME_EPISODE",
            "score_can_trigger_full_exit": False,
            "missing_required_cap": "NO_DECISION",
            "missing_execution_fact": "ACTION_BLOCKED",
        }


_SCORE_POLICIES_IMPORT: tuple[ScorePolicyDefinitionF0, ...] = (
    ScorePolicyDefinitionF0(
        "PRIMARY_F0", 500_000, 300_000, 200_000, True, True
    ),
    ScorePolicyDefinitionF0(
        "CONTROL_EQUAL_WITH_VOLUME", 333_334, 333_333, 333_333, True, False
    ),
    ScorePolicyDefinitionF0(
        "ABLATION_ROLE_NO_VOLUME", 500_000, 300_000, 200_000, False, False
    ),
    ScorePolicyDefinitionF0(
        "ABLATION_EQUAL_NO_VOLUME", 333_334, 333_333, 333_333, False, False
    ),
)

_ACTION_POLICY_IMPORT = ActionPolicyF0(
    restore_threshold_ppm=400_000,
    defensive_1_threshold_ppm=-200_000,
    defensive_2_threshold_ppm=-600_000,
    confirmation_complete_sessions=2,
    defensive_1_cap_numerator=3,
    defensive_1_cap_denominator=4,
    defensive_2_cap_numerator=1,
    defensive_2_cap_denominator=2,
    recovery_tier_fractions=((1, 2), (3, 4), (1, 1)),
    non_exit_minimum_units=1,
    max_quote_event_age_ns=5_000_000_000,
    max_quote_receive_age_ns=5_000_000_000,
    max_cross_leg_receive_skew_ns=1_000_000_000,
    scheduled_action_hour_et=10,
    scheduled_action_minute_et=45,
)


def _runtime_document() -> dict[str, object]:
    return {
        "schema_version": POLICY_SET_VERSION,
        "score_policies": [value.as_dict() for value in _SCORE_POLICIES_IMPORT],
        "action_policy": _ACTION_POLICY_IMPORT.as_dict(),
    }


_POLICY_SET_CANONICAL_BYTES = canonical_json_bytes(_runtime_document())
POLICY_SET_SHA256 = sha256(_POLICY_SET_CANONICAL_BYTES).hexdigest()


def verify_policy_set_f0() -> None:
    """Fail closed if the process-local runtime policy drifted after import."""

    if canonical_json_bytes(_runtime_document()) != _POLICY_SET_CANONICAL_BYTES:
        raise ManagementResearchError("POLICY_RUNTIME_IDENTITY_MISMATCH")


def score_policy_definitions_f0() -> tuple[ScorePolicyDefinitionF0, ...]:
    """Return the frozen, import-sealed score policy definitions."""

    verify_policy_set_f0()
    return _SCORE_POLICIES_IMPORT


def action_policy_f0() -> ActionPolicyF0:
    """Return the frozen, import-sealed action policy."""

    verify_policy_set_f0()
    return _ACTION_POLICY_IMPORT


def policy_set_document_f0() -> dict[str, object]:
    """Return a detached machine-readable copy of the sealed policy contract."""

    verify_policy_set_f0()
    return _runtime_document()


__all__ = [
    "POLICY_SET_SHA256",
    "POLICY_SET_VERSION",
    "ActionPolicyF0",
    "ScorePolicyDefinitionF0",
    "action_policy_f0",
    "policy_set_document_f0",
    "score_policy_definitions_f0",
    "verify_policy_set_f0",
]
