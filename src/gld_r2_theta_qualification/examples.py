"""Detached deterministic inputs for the local R2 Wave 1 acceptance demo."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import re

from .contracts import (
    R2QualificationError,
    canonical_json_bytes,
    canonical_sha256,
)


SYNTHETIC_FIXTURE_SCHEMA_VERSION = "GLD_R2_SYNTHETIC_ACCEPTANCE_FIXTURE_V1"
SYNTHETIC_FIXTURE_SHA256 = (
    "53e939c748ed1f889d2e29f2db0732f7442ccbf904dcd771415490a6c5ace371"
)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_FIXTURE = (
    _PROJECT_ROOT
    / "fixtures"
    / "gld_r2_theta_qualification"
    / "v1"
    / "acceptance-cases.json"
)
_SCENARIO_IDS = (
    "contract_identity",
    "registry_16_to_12",
    "entry_label_windows",
    "policy_q1_stress",
    "joint_statistics",
    "forward_exact_100",
)
_IDENTIFIER_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_FIXTURE_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "authority_status",
        "r1_regression",
        "scenarios",
    }
)
_R1_KEYS = frozenset(
    {
        "classification",
        "accepted_commit",
        "tree_root",
        "tree_file_count",
        "tree_sha256",
        "owner_receipt_path",
        "owner_receipt_sha256",
    }
)
_SCENARIO_KEYS = frozenset(
    {
        "classification",
        "scenario_id",
        "title_zh",
        "test_boundary_zh",
        "reason_code",
        "expected",
    }
)


def default_synthetic_acceptance_fixture_path() -> Path:
    """Return the repository-owned synthetic fixture path."""

    return _DEFAULT_FIXTURE


def _synthetic_marker() -> dict[str, object]:
    return {
        "classification": "SYNTHETIC_ONLY",
        "scope": "RESEARCH_ONLY",
        "authority_status": "NO_DECISION_EFFECT",
        "actionable": False,
        "broker_order_count": 0,
        "real_data_accessed": False,
    }


def validate_synthetic_acceptance_fixture(document: object) -> dict[str, object]:
    """Validate the closed catalog and return a detached canonical copy."""

    if type(document) is not dict or frozenset(document) != _FIXTURE_KEYS:
        raise R2QualificationError("R2_SYNTHETIC_FIXTURE_SCHEMA_INVALID")
    canonical_json_bytes(document)
    if (
        document["schema_version"] != SYNTHETIC_FIXTURE_SCHEMA_VERSION
        or document["classification"] != "SYNTHETIC_ONLY"
        or document["authority_status"] != "NO_DECISION_EFFECT"
    ):
        raise R2QualificationError("R2_SYNTHETIC_FIXTURE_SEMANTICS_INVALID")
    if canonical_sha256(document) != SYNTHETIC_FIXTURE_SHA256:
        raise R2QualificationError("R2_SYNTHETIC_FIXTURE_IDENTITY_INVALID")
    regression = document["r1_regression"]
    if type(regression) is not dict or frozenset(regression) != _R1_KEYS:
        raise R2QualificationError("R2_SYNTHETIC_FIXTURE_R1_INVALID")
    if (
        regression["classification"] != "SYNTHETIC_ONLY"
        or regression["accepted_commit"]
        != "b21400d799f7b441d35f528a5980b1ce855997a7"
        or regression["tree_file_count"] != 45
        or type(regression["tree_root"]) is not str
        or type(regression["owner_receipt_path"]) is not str
        or type(regression["tree_sha256"]) is not str
        or _HASH_RE.fullmatch(regression["tree_sha256"]) is None
        or type(regression["owner_receipt_sha256"]) is not str
        or _HASH_RE.fullmatch(regression["owner_receipt_sha256"]) is None
    ):
        raise R2QualificationError("R2_SYNTHETIC_FIXTURE_R1_INVALID")
    for raw_path in (regression["tree_root"], regression["owner_receipt_path"]):
        if (
            not raw_path
            or raw_path.startswith("/")
            or "\\" in raw_path
            or ":" in raw_path
            or ".." in Path(raw_path).parts
        ):
            raise R2QualificationError("R2_SYNTHETIC_FIXTURE_R1_INVALID")

    scenarios = document["scenarios"]
    if type(scenarios) is not list or len(scenarios) != len(_SCENARIO_IDS):
        raise R2QualificationError("R2_SYNTHETIC_FIXTURE_SCENARIOS_INVALID")
    identifiers: list[str] = []
    for row in scenarios:
        if type(row) is not dict or frozenset(row) != _SCENARIO_KEYS:
            raise R2QualificationError("R2_SYNTHETIC_FIXTURE_SCENARIO_INVALID")
        identifier = row["scenario_id"]
        reason = row["reason_code"]
        if (
            row["classification"] != "SYNTHETIC_ONLY"
            or type(identifier) is not str
            or _IDENTIFIER_RE.fullmatch(identifier) is None
            or type(row["title_zh"]) is not str
            or not row["title_zh"]
            or type(row["test_boundary_zh"]) is not str
            or not row["test_boundary_zh"]
            or type(reason) is not str
            or _REASON_RE.fullmatch(reason) is None
            or type(row["expected"]) is not dict
        ):
            raise R2QualificationError("R2_SYNTHETIC_FIXTURE_SCENARIO_INVALID")
        identifiers.append(identifier)
    if tuple(identifiers) != _SCENARIO_IDS:
        raise R2QualificationError("R2_SYNTHETIC_FIXTURE_SCENARIOS_INVALID")
    return deepcopy(document)


def synthetic_acceptance_scenario_specs(
    fixture: dict[str, object],
) -> tuple[dict[str, object], ...]:
    """Return detached scenario specifications in their frozen order."""

    validated = validate_synthetic_acceptance_fixture(fixture)
    rows = validated["scenarios"]
    assert type(rows) is list
    return tuple(deepcopy(row) for row in rows)


@lru_cache(maxsize=6)
def _cached_case(scenario_id: str) -> dict[str, object]:
    if scenario_id not in _SCENARIO_IDS:
        raise R2QualificationError("R2_SYNTHETIC_SCENARIO_UNKNOWN")
    common = {
        "schema_version": "GLD_R2_SYNTHETIC_DIAGNOSTIC_INPUT_V1",
        **_synthetic_marker(),
        "scenario_id": scenario_id,
    }
    if scenario_id == "contract_identity":
        inputs: dict[str, object] = {
            "code_package_sha256": "1" * 64,
            "runtime_id": "CPYTHON_LOCAL_SYNTHETIC",
            "runtime_sha256": "2" * 64,
        }
    elif scenario_id == "registry_16_to_12":
        inputs = {
            "gate_count": 4,
            "hard_stop_variants": [333_333, 500_000, 666_667, None],
            "quantity_units": 1,
        }
    elif scenario_id == "entry_label_windows":
        inputs = {
            "total_after_warmup_sessions": 240,
            "qualified_common_rows": 95,
            "missing_common_rows": 5,
        }
    elif scenario_id == "policy_q1_stress":
        inputs = {
            "r1_synthetic_case_id": "both_qualified",
            "carrier_mode": "DUAL_PREFERENCE",
            "hard_stop_loss_ppm": 333_333,
            "synthetic_terminal_return_ppm": 25_000,
            "tick_nano_usd": 2,
            "fee_per_side_nano_usd": 1,
        }
    elif scenario_id == "joint_statistics":
        inputs = {
            "carrier_episode_count": 100,
            "carrier_return_ppm": 25_000,
            "candidate_returns_ppm": [20_000, 30_000],
            "fold_row_count": 25,
            "fold_count": 5,
            "bootstrap_replicates": 10_000,
            "block_length": 20,
        }
    else:
        inputs = {
            "exact_terminal_episode_target": 100,
            "synthetic_return_ppm": 20_000,
            "minimum_coverage_ppm": 950_000,
            "t0_session_date": "2027-01-04",
        }
    return {**common, "inputs": inputs}


def build_synthetic_acceptance_case_v1(scenario_id: str) -> dict[str, object]:
    """Build one detached input; callers cannot mutate the cached fixture."""

    return deepcopy(_cached_case(scenario_id))


__all__ = [
    "SYNTHETIC_FIXTURE_SCHEMA_VERSION",
    "SYNTHETIC_FIXTURE_SHA256",
    "build_synthetic_acceptance_case_v1",
    "default_synthetic_acceptance_fixture_path",
    "synthetic_acceptance_scenario_specs",
    "validate_synthetic_acceptance_fixture",
]
