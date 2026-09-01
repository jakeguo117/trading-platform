"""Deterministic GLD management scoring Research F0.

The package produces non-actionable research artifacts only.  It performs no
network, broker, provider, clock, or order operation.
"""

from .action import evaluate_hypothetical_management_action_f0
from .contracts import (
    HypotheticalManagementActionF0,
    LocalIndicatorsF0,
    ManagementActionSnapshotF0,
    ManagementScoreObservationF0,
    ManagementScoreResultF0,
)
from .errors import ManagementResearchError
from .lineage import (
    derive_genesis_management_state_f0,
    derive_management_state_transition_f0,
)
from .scoring import derive_management_score_f0
from .validation import validate_management_score_observation_f0


__all__ = [
    "HypotheticalManagementActionF0",
    "LocalIndicatorsF0",
    "ManagementActionSnapshotF0",
    "ManagementResearchError",
    "ManagementScoreObservationF0",
    "ManagementScoreResultF0",
    "derive_management_score_f0",
    "derive_genesis_management_state_f0",
    "derive_management_state_transition_f0",
    "evaluate_hypothetical_management_action_f0",
    "validate_management_score_observation_f0",
]
