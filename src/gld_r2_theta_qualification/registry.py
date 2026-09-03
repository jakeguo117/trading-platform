"""Frozen R2 candidate registry and Development carrier dispositions."""

from __future__ import annotations

from dataclasses import dataclass, field

from gld_entry_decision_f0 import entry_gate_policy_candidates_f0

from .contracts import (
    R1FixedPolicyProjectionV1,
    R2QualificationError,
    canonical_json_bytes,
    canonical_sha256,
    derive_base_candidate_sha256,
    validate_r1_fixed_policy_projection,
)


R2_CANDIDATE_SCHEMA_VERSION = "R2_BASE_CANDIDATE_V1"
R2_CANDIDATE_REGISTRY_SCHEMA_VERSION = "R2_CANDIDATE_REGISTRY_V1"
RESEARCH_ONLY = "RESEARCH_ONLY"
HARD_STOP = "HARD_STOP"
DISABLED_CONTROL = "DISABLED_CONTROL"

DUAL_PREFERENCE = "DUAL_PREFERENCE"
LC0_ONLY = "LC0_ONLY"
BCS0_ONLY = "BCS0_ONLY"
DEVELOPMENT_REJECTED = "DEVELOPMENT_REJECTED"
DEVELOPMENT_CLASSIFIED = "DEVELOPMENT_CLASSIFIED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
NO_QUALIFIED_POLICY = "NO_QUALIFIED_POLICY"

_GATES = tuple(
    (
        candidate["trend_required_count"],
        candidate["breakout_required_count"],
    )
    for candidate in entry_gate_policy_candidates_f0()
)
_STOPS: tuple[tuple[str, int | None], ...] = (
    (HARD_STOP, 333_333),
    (HARD_STOP, 500_000),
    (HARD_STOP, 666_667),
    (DISABLED_CONTROL, None),
)
_CANDIDATE_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "candidate_id",
        "ordinal",
        "gate_index",
        "stop_index",
        "trend_required_count",
        "breakout_required_count",
        "stop_mode",
        "hard_stop_loss_ppm",
        "quantity_units",
        "promotable",
        "r1_fixed_policy_projection_sha256",
        "base_candidate_sha256",
    }
)
_REGISTRY_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "r1_fixed_policy_projection",
        "r1_fixed_policy_projection_sha256",
        "candidates",
    }
)
_CARRIER_EVIDENCE_STATUSES = frozenset(
    {"PASS", "FAIL", "UNKNOWN", INSUFFICIENT_EVIDENCE}
)
_DEVELOPMENT_DISPOSITION_SEAL = object()


@dataclass(frozen=True, slots=True)
class R2CandidateV1:
    schema_version: str
    classification: str
    candidate_id: str
    ordinal: int
    gate_index: int
    stop_index: int
    trend_required_count: int
    breakout_required_count: int
    stop_mode: str
    hard_stop_loss_ppm: int | None
    quantity_units: int
    promotable: bool
    r1_fixed_policy_projection_sha256: str
    base_candidate_sha256: str

    @property
    def hard_stop_mode(self) -> str:
        """Approved-plan spelling retained as a read-only compatibility view."""

        return self.stop_mode

    @property
    def r1_fixed_policy_sha256(self) -> str:
        """Legacy local spelling; the value is the closed projection hash."""

        return self.r1_fixed_policy_projection_sha256

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "candidate_id": self.candidate_id,
            "ordinal": self.ordinal,
            "gate_index": self.gate_index,
            "stop_index": self.stop_index,
            "trend_required_count": self.trend_required_count,
            "breakout_required_count": self.breakout_required_count,
            "stop_mode": self.stop_mode,
            "hard_stop_loss_ppm": self.hard_stop_loss_ppm,
            "quantity_units": self.quantity_units,
            "promotable": self.promotable,
            "r1_fixed_policy_projection_sha256": self.r1_fixed_policy_projection_sha256,
            "base_candidate_sha256": self.base_candidate_sha256,
        }


@dataclass(frozen=True, slots=True)
class R2CandidateRegistryV1:
    schema_version: str
    classification: str
    r1_fixed_policy_projection: R1FixedPolicyProjectionV1
    r1_fixed_policy_projection_sha256: str
    candidates: tuple[R2CandidateV1, ...]
    registry_sha256: str
    canonical_bytes: bytes

    @property
    def r1_fixed_policy_sha256(self) -> str:
        """Legacy local spelling; the value is the closed projection hash."""

        return self.r1_fixed_policy_projection_sha256

    @property
    def promotable_candidates(self) -> tuple[R2CandidateV1, ...]:
        return tuple(candidate for candidate in self.candidates if candidate.promotable)

    @property
    def control_candidates(self) -> tuple[R2CandidateV1, ...]:
        return tuple(
            candidate for candidate in self.candidates if not candidate.promotable
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "r1_fixed_policy_projection": self.r1_fixed_policy_projection.as_dict(),
            "r1_fixed_policy_projection_sha256": self.r1_fixed_policy_projection_sha256,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
        }


def _validated_projection(
    projection_input: object,
) -> R1FixedPolicyProjectionV1:
    if isinstance(projection_input, R1FixedPolicyProjectionV1):
        validated = validate_r1_fixed_policy_projection(projection_input.as_dict())
        if (
            validated.projection_sha256 != projection_input.projection_sha256
            or validated.canonical_bytes != projection_input.canonical_bytes
        ):
            raise R2QualificationError("R1_FIXED_POLICY_PROJECTION_INTEGRITY_MISMATCH")
        return validated
    return validate_r1_fixed_policy_projection(projection_input)


def _candidate_for_indices(
    gate_index: int,
    stop_index: int,
    r1_fixed_policy_projection_sha256: str,
) -> R2CandidateV1:
    ordinal = gate_index * 4 + stop_index
    trend_required, breakout_required = _GATES[gate_index]
    stop_mode, hard_stop_loss_ppm = _STOPS[stop_index]
    candidate_id = f"R2C{ordinal:02d}"
    promotable = stop_mode != DISABLED_CONTROL
    base_hash = derive_base_candidate_sha256(
        candidate_id=candidate_id,
        ordinal=ordinal,
        trend_required_count=trend_required,
        breakout_required_count=breakout_required,
        stop_mode=stop_mode,
        hard_stop_loss_ppm=hard_stop_loss_ppm,
        r1_fixed_policy_projection_sha256=r1_fixed_policy_projection_sha256,
        quantity_units=1,
    )
    return R2CandidateV1(
        schema_version=R2_CANDIDATE_SCHEMA_VERSION,
        classification=RESEARCH_ONLY,
        candidate_id=candidate_id,
        ordinal=ordinal,
        gate_index=gate_index,
        stop_index=stop_index,
        trend_required_count=trend_required,
        breakout_required_count=breakout_required,
        stop_mode=stop_mode,
        hard_stop_loss_ppm=hard_stop_loss_ppm,
        quantity_units=1,
        promotable=promotable,
        r1_fixed_policy_projection_sha256=r1_fixed_policy_projection_sha256,
        base_candidate_sha256=base_hash,
    )


def build_r2_candidate_registry(
    r1_fixed_policy_projection: R1FixedPolicyProjectionV1 | object,
) -> R2CandidateRegistryV1:
    """Build the exact 4 Gate x 4 Hard Stop row-major registry."""

    projection = _validated_projection(r1_fixed_policy_projection)
    candidates = tuple(
        _candidate_for_indices(
            gate_index,
            stop_index,
            projection.projection_sha256,
        )
        for gate_index in range(4)
        for stop_index in range(4)
    )
    document: dict[str, object] = {
        "schema_version": R2_CANDIDATE_REGISTRY_SCHEMA_VERSION,
        "classification": RESEARCH_ONLY,
        "r1_fixed_policy_projection": projection.as_dict(),
        "r1_fixed_policy_projection_sha256": projection.projection_sha256,
        "candidates": [candidate.as_dict() for candidate in candidates],
    }
    canonical = canonical_json_bytes(document)
    return R2CandidateRegistryV1(
        schema_version=R2_CANDIDATE_REGISTRY_SCHEMA_VERSION,
        classification=RESEARCH_ONLY,
        r1_fixed_policy_projection=projection,
        r1_fixed_policy_projection_sha256=projection.projection_sha256,
        candidates=candidates,
        registry_sha256=canonical_sha256(document),
        canonical_bytes=canonical,
    )


def validate_r2_candidate_registry(document: object) -> R2CandidateRegistryV1:
    """Parse an exact registry and reject added, omitted, or reordered rows."""

    if type(document) is not dict or frozenset(document) != _REGISTRY_KEYS:
        raise R2QualificationError("R2_CANDIDATE_REGISTRY_SCHEMA_INVALID")
    canonical_json_bytes(document)
    if document["schema_version"] != R2_CANDIDATE_REGISTRY_SCHEMA_VERSION:
        raise R2QualificationError("R2_CANDIDATE_REGISTRY_VERSION_INVALID")
    if document["classification"] != RESEARCH_ONLY:
        raise R2QualificationError("R2_CANDIDATE_CLASSIFICATION_INVALID")
    rows = document["candidates"]
    if type(rows) is not list or len(rows) != 16:
        raise R2QualificationError("R2_CANDIDATE_REGISTRY_CARDINALITY_INVALID")

    projection = validate_r1_fixed_policy_projection(
        document["r1_fixed_policy_projection"]
    )
    if document["r1_fixed_policy_projection_sha256"] != projection.projection_sha256:
        raise R2QualificationError("R1_FIXED_POLICY_PROJECTION_HASH_MISMATCH")
    expected = build_r2_candidate_registry(projection)
    for ordinal, (actual_row, expected_candidate) in enumerate(
        zip(rows, expected.candidates, strict=True)
    ):
        if type(actual_row) is not dict or frozenset(actual_row) != _CANDIDATE_KEYS:
            raise R2QualificationError("R2_CANDIDATE_SCHEMA_INVALID", f"ordinal={ordinal}")
        if actual_row != expected_candidate.as_dict():
            raise R2QualificationError(
                "R2_CANDIDATE_REGISTRY_ROW_INVALID", f"ordinal={ordinal}"
            )

    canonical = canonical_json_bytes(document)
    return R2CandidateRegistryV1(
        schema_version=expected.schema_version,
        classification=expected.classification,
        r1_fixed_policy_projection=expected.r1_fixed_policy_projection,
        r1_fixed_policy_projection_sha256=expected.r1_fixed_policy_projection_sha256,
        candidates=expected.candidates,
        registry_sha256=canonical_sha256(document),
        canonical_bytes=canonical,
    )


@dataclass(frozen=True, slots=True)
class DevelopmentCandidateDispositionV1:
    status: str
    carrier_mode: str | None
    wf_allowed: bool
    reason_code: str
    _lc0_status: str | None = field(default=None, repr=False)
    _bcs0_status: str | None = field(default=None, repr=False)
    _integrity_seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._assert_integrity()

    def _assert_integrity(self) -> None:
        """Reject copied or directly constructed semantic forgeries."""

        if (
            type(self.status) is not str
            or (
                self.carrier_mode is not None
                and type(self.carrier_mode) is not str
            )
            or type(self.wf_allowed) is not bool
            or type(self.reason_code) is not str
            or type(self._lc0_status) is not str
            or type(self._bcs0_status) is not str
        ):
            raise R2QualificationError(
                "DEVELOPMENT_DISPOSITION_INTEGRITY_MISMATCH"
            )
        expected = _development_disposition_fields(
            self._lc0_status,
            self._bcs0_status,
        )
        actual = (
            self.status,
            self.carrier_mode,
            self.wf_allowed,
            self.reason_code,
        )
        expected_seal = (
            _DEVELOPMENT_DISPOSITION_SEAL,
            self._lc0_status,
            self._bcs0_status,
            *expected,
        )
        if actual != expected or self._integrity_seal != expected_seal:
            raise R2QualificationError(
                "DEVELOPMENT_DISPOSITION_INTEGRITY_MISMATCH"
            )

    def as_dict(self) -> dict[str, object]:
        self._assert_integrity()
        return {
            "status": self.status,
            "carrier_mode": self.carrier_mode,
            "wf_allowed": self.wf_allowed,
            "reason_code": self.reason_code,
        }


def _development_disposition_fields(
    lc0_status: str,
    bcs0_status: str,
) -> tuple[str, str | None, bool, str]:
    if (
        type(lc0_status) is not str
        or type(bcs0_status) is not str
        or lc0_status not in _CARRIER_EVIDENCE_STATUSES
        or bcs0_status not in _CARRIER_EVIDENCE_STATUSES
    ):
        raise R2QualificationError("DEVELOPMENT_CARRIER_STATUS_INVALID")
    if lc0_status in {"UNKNOWN", INSUFFICIENT_EVIDENCE} or bcs0_status in {
        "UNKNOWN",
        INSUFFICIENT_EVIDENCE,
    }:
        return (
            INSUFFICIENT_EVIDENCE,
            None,
            False,
            "DEVELOPMENT_CARRIER_EVIDENCE_INSUFFICIENT",
        )
    if lc0_status == "PASS" and bcs0_status == "PASS":
        mode = DUAL_PREFERENCE
    elif lc0_status == "PASS" and bcs0_status == "FAIL":
        mode = LC0_ONLY
    elif lc0_status == "FAIL" and bcs0_status == "PASS":
        mode = BCS0_ONLY
    else:
        return (
            DEVELOPMENT_REJECTED,
            None,
            False,
            "BOTH_CARRIERS_DEVELOPMENT_FAILED",
        )
    return ("EVALUATED", mode, True, f"{mode}_FROZEN")


def _sealed_development_disposition(
    lc0_status: str,
    bcs0_status: str,
) -> DevelopmentCandidateDispositionV1:
    fields = _development_disposition_fields(lc0_status, bcs0_status)
    return DevelopmentCandidateDispositionV1(
        status=fields[0],
        carrier_mode=fields[1],
        wf_allowed=fields[2],
        reason_code=fields[3],
        _lc0_status=lc0_status,
        _bcs0_status=bcs0_status,
        _integrity_seal=(
            _DEVELOPMENT_DISPOSITION_SEAL,
            lc0_status,
            bcs0_status,
            *fields,
        ),
    )


@dataclass(frozen=True, slots=True)
class DevelopmentCandidateSetDispositionV1:
    status: str
    wf_allowed: bool
    s_dev_candidate_ids: tuple[str, ...]
    k: int
    reason_code: str

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "wf_allowed": self.wf_allowed,
            "s_dev_candidate_ids": list(self.s_dev_candidate_ids),
            "k": self.k,
            "reason_code": self.reason_code,
        }


def map_development_carrier_mode(
    lc0_status: str,
    bcs0_status: str,
) -> DevelopmentCandidateDispositionV1:
    """Map two independently sufficient carrier verdicts without OOS downgrade."""

    return _sealed_development_disposition(lc0_status, bcs0_status)


def _validated_registry_instance(
    registry: R2CandidateRegistryV1,
) -> R2CandidateRegistryV1:
    """Rebuild an in-memory registry before any classification consumes it."""

    try:
        validated = validate_r2_candidate_registry(registry.as_dict())
    except (AttributeError, TypeError) as exc:
        raise R2QualificationError(
            "R2_CANDIDATE_REGISTRY_INTEGRITY_MISMATCH"
        ) from exc
    if validated != registry:
        raise R2QualificationError("R2_CANDIDATE_REGISTRY_INTEGRITY_MISMATCH")
    return validated


def classify_development_candidate_set(
    registry: R2CandidateRegistryV1,
    dispositions: dict[str, DevelopmentCandidateDispositionV1],
) -> DevelopmentCandidateSetDispositionV1:
    """Freeze S_dev only after all 12 promotable candidates are classified."""

    if type(registry) is not R2CandidateRegistryV1 or type(dispositions) is not dict:
        raise R2QualificationError("DEVELOPMENT_DISPOSITION_SET_INVALID")
    registry = _validated_registry_instance(registry)
    expected_ids = tuple(
        candidate.candidate_id for candidate in registry.promotable_candidates
    )
    actual_ids = frozenset(dispositions)
    if not actual_ids <= frozenset(expected_ids):
        raise R2QualificationError("DEVELOPMENT_DISPOSITION_SET_INVALID")
    if actual_ids != frozenset(expected_ids):
        return DevelopmentCandidateSetDispositionV1(
            status=INSUFFICIENT_EVIDENCE,
            wf_allowed=False,
            s_dev_candidate_ids=(),
            k=0,
            reason_code="DEVELOPMENT_CANDIDATE_CLASSIFICATION_INCOMPLETE",
        )
    ordered = tuple(dispositions[candidate_id] for candidate_id in expected_ids)
    for item in ordered:
        if type(item) is not DevelopmentCandidateDispositionV1:
            raise R2QualificationError("DEVELOPMENT_DISPOSITION_SET_INVALID")
        item._assert_integrity()
    if any(item.status == INSUFFICIENT_EVIDENCE for item in ordered):
        return DevelopmentCandidateSetDispositionV1(
            status=INSUFFICIENT_EVIDENCE,
            wf_allowed=False,
            s_dev_candidate_ids=(),
            k=0,
            reason_code="DEVELOPMENT_CARRIER_EVIDENCE_INSUFFICIENT",
        )
    s_dev = tuple(
        candidate_id
        for candidate_id, item in zip(expected_ids, ordered, strict=True)
        if item.status == "EVALUATED"
    )
    if not s_dev:
        return DevelopmentCandidateSetDispositionV1(
            status=NO_QUALIFIED_POLICY,
            wf_allowed=False,
            s_dev_candidate_ids=(),
            k=0,
            reason_code="ALL_DEVELOPMENT_REJECTED",
        )
    return DevelopmentCandidateSetDispositionV1(
        status=DEVELOPMENT_CLASSIFIED,
        wf_allowed=True,
        s_dev_candidate_ids=s_dev,
        k=len(s_dev),
        reason_code="S_DEV_FROZEN",
    )


__all__ = [
    "BCS0_ONLY",
    "DEVELOPMENT_CLASSIFIED",
    "DEVELOPMENT_REJECTED",
    "DISABLED_CONTROL",
    "DUAL_PREFERENCE",
    "DevelopmentCandidateDispositionV1",
    "DevelopmentCandidateSetDispositionV1",
    "HARD_STOP",
    "INSUFFICIENT_EVIDENCE",
    "LC0_ONLY",
    "NO_QUALIFIED_POLICY",
    "R2CandidateRegistryV1",
    "R2CandidateV1",
    "build_r2_candidate_registry",
    "classify_development_candidate_set",
    "map_development_carrier_mode",
    "validate_r2_candidate_registry",
]
