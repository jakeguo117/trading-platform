"""Closed, versioned policy contract for GLD Entry Decision f F0."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .canonical import canonical_json_bytes, canonical_json_sha256
from .errors import EntryDecisionF0Error


ENTRY_POLICY_SCHEMA_VERSION = "ENTRY_POLICY_F0_V1"
PPM = 1_000_000
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_POLICY_KEYS = frozenset(
    {
        "schema_version",
        "policy_id",
        "policy_status",
        "owner_approved",
        "trend_required_count",
        "breakout_required_count",
        "lc0_delta_min_ppm",
        "lc0_delta_target_ppm",
        "lc0_delta_max_ppm",
        "bcs0_long_delta_min_ppm",
        "bcs0_long_delta_target_ppm",
        "bcs0_long_delta_max_ppm",
        "bcs0_short_delta_min_ppm",
        "bcs0_short_delta_target_ppm",
        "bcs0_short_delta_max_ppm",
        "account_loss_budget_ppm",
        "expiry_safety_calendar_days",
        "management_policy_id",
        "management_policy_sha256",
        "invalidation_confirmation_sessions",
    }
)
_GATE_CANDIDATES = ((3, 2), (2, 2), (3, 1), (2, 1))


def _int(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise EntryDecisionF0Error("ENTRY_POLICY_INTEGER_INVALID")
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise EntryDecisionF0Error("ENTRY_POLICY_IDENTIFIER_INVALID")
    return value


@dataclass(frozen=True, slots=True)
class EntryPolicyF0:
    schema_version: str
    policy_id: str
    policy_status: str
    owner_approved: bool
    trend_required_count: int
    breakout_required_count: int
    lc0_delta_min_ppm: int
    lc0_delta_target_ppm: int
    lc0_delta_max_ppm: int
    bcs0_long_delta_min_ppm: int
    bcs0_long_delta_target_ppm: int
    bcs0_long_delta_max_ppm: int
    bcs0_short_delta_min_ppm: int
    bcs0_short_delta_target_ppm: int
    bcs0_short_delta_max_ppm: int
    account_loss_budget_ppm: int
    expiry_safety_calendar_days: int
    management_policy_id: str
    management_policy_sha256: str
    invalidation_confirmation_sessions: int
    policy_sha256: str
    canonical_bytes: bytes

    @property
    def is_active(self) -> bool:
        return self.policy_status == "ACTIVE" and self.owner_approved

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "policy_status": self.policy_status,
            "owner_approved": self.owner_approved,
            "trend_required_count": self.trend_required_count,
            "breakout_required_count": self.breakout_required_count,
            "lc0_delta_min_ppm": self.lc0_delta_min_ppm,
            "lc0_delta_target_ppm": self.lc0_delta_target_ppm,
            "lc0_delta_max_ppm": self.lc0_delta_max_ppm,
            "bcs0_long_delta_min_ppm": self.bcs0_long_delta_min_ppm,
            "bcs0_long_delta_target_ppm": self.bcs0_long_delta_target_ppm,
            "bcs0_long_delta_max_ppm": self.bcs0_long_delta_max_ppm,
            "bcs0_short_delta_min_ppm": self.bcs0_short_delta_min_ppm,
            "bcs0_short_delta_target_ppm": self.bcs0_short_delta_target_ppm,
            "bcs0_short_delta_max_ppm": self.bcs0_short_delta_max_ppm,
            "account_loss_budget_ppm": self.account_loss_budget_ppm,
            "expiry_safety_calendar_days": self.expiry_safety_calendar_days,
            "management_policy_id": self.management_policy_id,
            "management_policy_sha256": self.management_policy_sha256,
            "invalidation_confirmation_sessions": self.invalidation_confirmation_sessions,
        }


def validate_entry_policy_f0(document: object) -> EntryPolicyF0:
    """Validate one exact policy document; unknown fields fail closed."""

    if type(document) is not dict or frozenset(document) != _POLICY_KEYS:
        raise EntryDecisionF0Error("ENTRY_POLICY_SCHEMA_INVALID")
    canonical_json_bytes(document)
    if document["schema_version"] != ENTRY_POLICY_SCHEMA_VERSION:
        raise EntryDecisionF0Error("ENTRY_POLICY_VERSION_INVALID")
    policy_id = _identifier(document["policy_id"])
    policy_status = document["policy_status"]
    if policy_status not in {"RESEARCH_TRIAL", "ACTIVE"}:
        raise EntryDecisionF0Error("ENTRY_POLICY_STATUS_INVALID")
    if type(document["owner_approved"]) is not bool:
        raise EntryDecisionF0Error("ENTRY_POLICY_OWNER_APPROVAL_INVALID")
    trend_required = _int(document["trend_required_count"], minimum=0, maximum=3)
    breakout_required = _int(document["breakout_required_count"], minimum=0, maximum=2)
    if (trend_required, breakout_required) not in _GATE_CANDIDATES:
        raise EntryDecisionF0Error("ENTRY_POLICY_GATE_CANDIDATE_INVALID")

    numeric = {
        key: _int(document[key], minimum=0, maximum=PPM)
        for key in (
            "lc0_delta_min_ppm",
            "lc0_delta_target_ppm",
            "lc0_delta_max_ppm",
            "bcs0_long_delta_min_ppm",
            "bcs0_long_delta_target_ppm",
            "bcs0_long_delta_max_ppm",
            "bcs0_short_delta_min_ppm",
            "bcs0_short_delta_target_ppm",
            "bcs0_short_delta_max_ppm",
            "account_loss_budget_ppm",
        )
    }
    for prefix in ("lc0_delta", "bcs0_long_delta", "bcs0_short_delta"):
        if not (
            numeric[f"{prefix}_min_ppm"]
            <= numeric[f"{prefix}_target_ppm"]
            <= numeric[f"{prefix}_max_ppm"]
        ):
            raise EntryDecisionF0Error("ENTRY_POLICY_DELTA_BAND_INVALID")
    safety_days = _int(
        document["expiry_safety_calendar_days"], minimum=0, maximum=366
    )
    invalidation_sessions = _int(
        document["invalidation_confirmation_sessions"], minimum=1, maximum=20
    )
    if invalidation_sessions != 2:
        raise EntryDecisionF0Error(
            "ENTRY_POLICY_INVALIDATION_CONFIRMATION_UNSUPPORTED"
        )
    management_policy_id = _identifier(document["management_policy_id"])
    management_hash = document["management_policy_sha256"]
    if type(management_hash) is not str or _SHA256_RE.fullmatch(management_hash) is None:
        raise EntryDecisionF0Error("ENTRY_POLICY_HASH_INVALID")
    encoded = canonical_json_bytes(document)
    return EntryPolicyF0(
        schema_version=ENTRY_POLICY_SCHEMA_VERSION,
        policy_id=policy_id,
        policy_status=policy_status,
        owner_approved=document["owner_approved"],
        trend_required_count=trend_required,
        breakout_required_count=breakout_required,
        lc0_delta_min_ppm=numeric["lc0_delta_min_ppm"],
        lc0_delta_target_ppm=numeric["lc0_delta_target_ppm"],
        lc0_delta_max_ppm=numeric["lc0_delta_max_ppm"],
        bcs0_long_delta_min_ppm=numeric["bcs0_long_delta_min_ppm"],
        bcs0_long_delta_target_ppm=numeric["bcs0_long_delta_target_ppm"],
        bcs0_long_delta_max_ppm=numeric["bcs0_long_delta_max_ppm"],
        bcs0_short_delta_min_ppm=numeric["bcs0_short_delta_min_ppm"],
        bcs0_short_delta_target_ppm=numeric["bcs0_short_delta_target_ppm"],
        bcs0_short_delta_max_ppm=numeric["bcs0_short_delta_max_ppm"],
        account_loss_budget_ppm=numeric["account_loss_budget_ppm"],
        expiry_safety_calendar_days=safety_days,
        management_policy_id=management_policy_id,
        management_policy_sha256=management_hash,
        invalidation_confirmation_sessions=invalidation_sessions,
        policy_sha256=canonical_json_sha256(document),
        canonical_bytes=encoded,
    )


def entry_gate_policy_candidates_f0() -> tuple[dict[str, int], ...]:
    """Return all four preregistered gate candidates in fixed order."""

    return tuple(
        {
            "trend_required_count": trend,
            "breakout_required_count": breakout,
        }
        for trend, breakout in _GATE_CANDIDATES
    )


__all__ = [
    "ENTRY_POLICY_SCHEMA_VERSION",
    "EntryPolicyF0",
    "entry_gate_policy_candidates_f0",
    "validate_entry_policy_f0",
]
