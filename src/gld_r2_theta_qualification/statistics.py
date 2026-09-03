"""Deterministic, research-only bootstrap primitives for R2.

The module has no data-provider or broker integration.  It accepts only
already-qualified integer-ppm rows and produces deterministic synthetic/local
statistics.  Carrier evidence deliberately adapts the accepted R1 episode
estimator (including Kelly); policy and Forward paths are mean-only.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re

from gld_entry_decision_f0.evidence import (
    EvidenceValidationError,
    estimate_full_kelly_ppm,
    round_half_even_divide,
)

from .contracts import (
    R2QualificationError,
    canonical_json_bytes,
    canonical_sha256,
)


PPM = 1_000_000
PRODUCTION_BOOTSTRAP_REPLICATES = 10_000
PRODUCTION_BLOCK_LENGTH = 20
PRODUCTION_LOWER_BOUND_INDEX = 499
PRODUCTION_JOINT_CRITICAL_VALUE_INDEX = 9_499
REPORT_ONLY_BLOCK_LENGTHS = (10, 40)

_MAX_RETURN_ABS_PPM = 100 * PPM
_MAX_CARRIER_BOOTSTRAP_WORK_ITEMS = 20_000_000
_MAX_POLICY_BOOTSTRAP_BLOCK_DRAWS = 20_000_000
_MAX_JOINT_BLOCK_VECTOR_ADDITIONS = 50_000_000
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_EXPECTED_FOLD_IDS = ("WF1", "WF2", "WF3", "WF4", "WF5")


def _hash(value: object, reason: str = "R2_HASH_INVALID") -> str:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        raise R2QualificationError(reason)
    return value


def _identifier(value: object, reason: str = "R2_IDENTIFIER_INVALID") -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise R2QualificationError(reason)
    return value


def _integer(
    value: object,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    reason: str = "R2_INTEGER_INVALID",
) -> int:
    if type(value) is not int:
        raise R2QualificationError(reason)
    if minimum is not None and value < minimum:
        raise R2QualificationError(reason)
    if maximum is not None and value > maximum:
        raise R2QualificationError(reason)
    return value


def _normalize_returns(
    values: Sequence[int],
    *,
    empty_reason: str = "R2_RETURN_DISTRIBUTION_INVALID",
) -> tuple[int, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise R2QualificationError(empty_reason)
    normalized = tuple(
        _integer(
            value,
            minimum=-_MAX_RETURN_ABS_PPM,
            maximum=_MAX_RETURN_ABS_PPM,
            reason="R2_RETURN_PPM_INVALID",
        )
        for value in values
    )
    if not normalized:
        raise R2QualificationError(empty_reason)
    return normalized


def _validate_bootstrap_controls(
    *,
    replicates: object,
    selected_index: object,
) -> tuple[int, int]:
    count = _integer(
        replicates,
        minimum=1,
        maximum=100_000,
        reason="R2_BOOTSTRAP_REPLICATES_INVALID",
    )
    index = _integer(
        selected_index,
        minimum=0,
        maximum=count - 1,
        reason="R2_BOOTSTRAP_RANK_INVALID",
    )
    return count, index


class _StatisticsReceiptSeal:
    """Bind issuance-time bytes and hash to one exact receipt instance."""

    __slots__ = ("_canonical_bytes", "_owner", "_receipt_sha256")

    def __init__(self) -> None:
        object.__setattr__(self, "_canonical_bytes", None)
        object.__setattr__(self, "_owner", None)
        object.__setattr__(self, "_receipt_sha256", None)

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("statistics receipt seals are immutable")

    def bind(
        self,
        owner: object,
        *,
        canonical_bytes: bytes,
        receipt_sha256: str,
    ) -> None:
        if self._owner is not None:
            raise RuntimeError("statistics receipt seal is already bound")
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_canonical_bytes", canonical_bytes)
        object.__setattr__(self, "_receipt_sha256", receipt_sha256)

    def matches(
        self,
        owner: object,
        *,
        canonical_bytes: bytes,
        receipt_sha256: str,
    ) -> bool:
        return (
            self._owner is owner
            and self._canonical_bytes == canonical_bytes
            and self._receipt_sha256 == receipt_sha256
        )


@dataclass(frozen=True, slots=True)
class CarrierBootstrapKernelResult:
    """Pure carrier bootstrap output with no stage or candidate identity."""

    estimator_id: str
    seed_sha256: str
    episode_count: int
    replicates: int
    block_length: int
    lower_bound_index: int
    observed_mean_ppm: int
    bootstrap_means_ppm: tuple[int, ...]
    lower_bound_ppm: int
    full_kelly_ppm: int
    bootstrap_full_kelly_ppm: tuple[int, ...]
    bootstrap_kelly_5pct_ppm: int
    robust_full_kelly_ppm: int
    half_kelly_ppm: int
    production_controls: bool
    statistical_pass: bool


def run_carrier_episode_bootstrap(
    returns_ppm: Sequence[int],
    *,
    seed_sha256: str,
    replicates: int = PRODUCTION_BOOTSTRAP_REPLICATES,
    block_length: int = PRODUCTION_BLOCK_LENGTH,
    lower_bound_index: int = PRODUCTION_LOWER_BOUND_INDEX,
) -> CarrierBootstrapKernelResult:
    """Run the deterministic episode MBB/Kelly kernel without stage lineage."""

    returns = _normalize_returns(returns_ppm)
    normalized_seed = _hash(seed_sha256, "R2_BOOTSTRAP_SEED_INVALID")
    replicate_count, rank_index = _validate_bootstrap_controls(
        replicates=replicates,
        selected_index=lower_bound_index,
    )
    block = _integer(
        block_length,
        minimum=1,
        maximum=10_000,
        reason="R2_BOOTSTRAP_BLOCK_LENGTH_INVALID",
    )
    sample_size = len(returns)
    if sample_size * replicate_count > _MAX_CARRIER_BOOTSTRAP_WORK_ITEMS:
        raise R2QualificationError("R2_CARRIER_BOOTSTRAP_WORK_BOUND_EXCEEDED")
    blocks_needed = (sample_size + block - 1) // block
    seed = bytes.fromhex(normalized_seed)
    means: list[int] = []
    kelly_values: list[int] = []
    kelly_cache: dict[tuple[tuple[int, int], ...], int] = {}
    try:
        for replicate_index in range(replicate_count):
            sample: list[int] = []
            for block_ordinal in range(blocks_needed):
                start = _single_stream_block_start(
                    seed,
                    replicate_index=replicate_index,
                    block_ordinal=block_ordinal,
                    sample_size=sample_size,
                )
                remaining = sample_size - len(sample)
                for offset in range(min(block, remaining)):
                    sample.append(returns[(start + offset) % sample_size])
            means.append(round_half_even_divide(sum(sample), sample_size))
            histogram = tuple(sorted(Counter(sample).items()))
            full_kelly = kelly_cache.get(histogram)
            if full_kelly is None:
                full_kelly = estimate_full_kelly_ppm(sample)
                kelly_cache[histogram] = full_kelly
            kelly_values.append(full_kelly)
        observed_mean = round_half_even_divide(sum(returns), sample_size)
        raw_full_kelly = estimate_full_kelly_ppm(returns)
    except EvidenceValidationError as exc:
        raise R2QualificationError(exc.reason_code, exc.detail) from exc
    lower_bound = sorted(means)[rank_index]
    bootstrap_kelly_5pct = sorted(kelly_values)[rank_index]
    robust_full_kelly = (
        min(raw_full_kelly, bootstrap_kelly_5pct)
        if lower_bound > 0
        else 0
    )
    half_kelly = robust_full_kelly // 2
    production_controls = (
        replicate_count == PRODUCTION_BOOTSTRAP_REPLICATES
        and block == PRODUCTION_BLOCK_LENGTH
        and rank_index == PRODUCTION_LOWER_BOUND_INDEX
    )
    return CarrierBootstrapKernelResult(
        estimator_id="CIRCULAR_MBB_KELLY_V2",
        seed_sha256=normalized_seed,
        episode_count=sample_size,
        replicates=replicate_count,
        block_length=block,
        lower_bound_index=rank_index,
        observed_mean_ppm=observed_mean,
        bootstrap_means_ppm=tuple(means),
        lower_bound_ppm=lower_bound,
        full_kelly_ppm=raw_full_kelly,
        bootstrap_full_kelly_ppm=tuple(kelly_values),
        bootstrap_kelly_5pct_ppm=bootstrap_kelly_5pct,
        robust_full_kelly_ppm=robust_full_kelly,
        half_kelly_ppm=half_kelly,
        production_controls=production_controls,
        statistical_pass=lower_bound > 0 and half_kelly > 0,
    )


@dataclass(frozen=True, slots=True)
class CanonicalStatisticsReceipt:
    """Typed wrapper around one closed, canonical statistics receipt."""

    schema_version: str
    stage: str
    receipt_sha256: str
    _canonical_bytes: bytes = field(repr=False, compare=False)
    _seal: _StatisticsReceiptSeal | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def _validated_document(self) -> dict[str, object]:
        # The seal is deliberately bound to this exact immutable instance and
        # its issuance-time bytes/hash. dataclasses.replace() leaves the
        # init=False seal unset, while coordinated object-level mutation no
        # longer matches the issuance snapshot.
        if (
            type(self._seal) is not _StatisticsReceiptSeal
            or type(self._canonical_bytes) is not bytes
            or not self._seal.matches(
                self,
                canonical_bytes=self._canonical_bytes,
                receipt_sha256=self.receipt_sha256,
            )
        ):
            raise R2QualificationError("R2_STATISTICS_RECEIPT_INTEGRITY_MISMATCH")
        try:
            document = json.loads(self._canonical_bytes.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise R2QualificationError(
                "R2_STATISTICS_RECEIPT_INTEGRITY_MISMATCH"
            ) from exc
        if type(document) is not dict:
            raise R2QualificationError("R2_STATISTICS_RECEIPT_INTEGRITY_MISMATCH")
        unsigned = dict(document)
        sealed_hash = unsigned.pop("receipt_sha256", None)
        if (
            canonical_json_bytes(document) != self._canonical_bytes
            or sealed_hash != self.receipt_sha256
            or canonical_sha256(unsigned) != sealed_hash
            or document.get("schema_version") != self.schema_version
            or document.get("stage") != self.stage
        ):
            raise R2QualificationError("R2_STATISTICS_RECEIPT_INTEGRITY_MISMATCH")
        _validate_statistics_receipt_document(document)
        return document

    @property
    def canonical_bytes(self) -> bytes:
        self._validated_document()
        return bytes(self._canonical_bytes)

    @property
    def document(self) -> dict[str, object]:
        return self._validated_document()

    def as_dict(self) -> dict[str, object]:
        return self._validated_document()

    def __copy__(self) -> CanonicalStatisticsReceipt:
        self._validated_document()
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> CanonicalStatisticsReceipt:
        self._validated_document()
        memo[id(self)] = self
        return self

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("CanonicalStatisticsReceipt cannot be pickled")


def _seal_statistics_receipt(
    unsigned: dict[str, object],
) -> CanonicalStatisticsReceipt:
    receipt_hash = canonical_sha256(unsigned)
    document = {**unsigned, "receipt_sha256": receipt_hash}
    _validate_statistics_receipt_document(document)
    canonical_bytes = canonical_json_bytes(document)
    seal = _StatisticsReceiptSeal()
    receipt = CanonicalStatisticsReceipt(
        schema_version=str(unsigned["schema_version"]),
        stage=str(unsigned["stage"]),
        receipt_sha256=receipt_hash,
        _canonical_bytes=canonical_bytes,
    )
    object.__setattr__(receipt, "_seal", seal)
    seal.bind(
        receipt,
        canonical_bytes=canonical_bytes,
        receipt_sha256=receipt_hash,
    )
    return receipt


def _carrier_id(value: object) -> str:
    if value not in {"LC0", "BCS0"}:
        raise R2QualificationError("R2_CARRIER_ID_INVALID")
    return str(value)


def _qualified_coverage(
    *,
    qualified_count: object,
    eligible_count: object,
    included_episode_count: int,
) -> tuple[int, int, int]:
    eligible = _integer(
        eligible_count,
        minimum=1,
        reason="R2_COVERAGE_COUNT_INVALID",
    )
    qualified = _integer(
        qualified_count,
        minimum=0,
        maximum=eligible,
        reason="R2_COVERAGE_COUNT_INVALID",
    )
    coverage_ppm = round_half_even_divide(qualified * PPM, eligible)
    if (
        qualified != included_episode_count
        or qualified * PPM < 950_000 * eligible
    ):
        raise R2QualificationError("INSUFFICIENT_EVIDENCE")
    return qualified, eligible, coverage_ppm


def _distribution_sha256(result: CarrierBootstrapKernelResult) -> str:
    return canonical_sha256(
        {
            "schema_version": "GLD_R2_CARRIER_BOOTSTRAP_DISTRIBUTION_V1",
            "means_ppm": list(result.bootstrap_means_ppm),
            "full_kelly_ppm": list(result.bootstrap_full_kelly_ppm),
        }
    )


def _carrier_receipt_statistics(
    result: CarrierBootstrapKernelResult,
) -> dict[str, object]:
    if not result.production_controls:
        raise R2QualificationError("R2_PRODUCTION_ESTIMATOR_CONTROLS_REQUIRED")
    return {
        "estimator_id": result.estimator_id,
        "seed_sha256": result.seed_sha256,
        "bootstrap_replicates": result.replicates,
        "block_length": result.block_length,
        "lower_bound_index": result.lower_bound_index,
        "observed_mean_ppm": result.observed_mean_ppm,
        "lower_bound_ppm": result.lower_bound_ppm,
        "full_kelly_ppm": result.full_kelly_ppm,
        "bootstrap_kelly_5pct_ppm": result.bootstrap_kelly_5pct_ppm,
        "robust_full_kelly_ppm": result.robust_full_kelly_ppm,
        "half_kelly_ppm": result.half_kelly_ppm,
        "evidence_status": "PASS" if result.statistical_pass else "FAIL",
        "bootstrap_distribution_sha256": _distribution_sha256(result),
    }


_CARRIER_STATISTICS_RECEIPT_KEYS = {
    "estimator_id",
    "seed_sha256",
    "bootstrap_replicates",
    "block_length",
    "lower_bound_index",
    "observed_mean_ppm",
    "lower_bound_ppm",
    "full_kelly_ppm",
    "bootstrap_kelly_5pct_ppm",
    "robust_full_kelly_ppm",
    "half_kelly_ppm",
    "evidence_status",
    "bootstrap_distribution_sha256",
}
_DEVELOPMENT_RECEIPT_KEYS = _CARRIER_STATISTICS_RECEIPT_KEYS | {
    "schema_version",
    "classification",
    "stage",
    "development_method_freeze_sha256",
    "base_candidate_sha256",
    "carrier_id",
    "episode_count",
    "episode_returns_sha256",
    "coverage_qualified_count",
    "coverage_eligible_count",
    "coverage_ppm",
    "receipt_sha256",
}
_WF_CARRIER_RECEIPT_KEYS = _CARRIER_STATISTICS_RECEIPT_KEYS | {
    "schema_version",
    "classification",
    "stage",
    "research_freeze_sha256",
    "evaluated_candidate_sha256",
    "carrier_id",
    "fold_ids",
    "fold_episode_counts",
    "fold_count",
    "episode_count",
    "fold_returns_sha256",
    "coverage_qualified_count",
    "coverage_eligible_count",
    "coverage_ppm",
    "receipt_sha256",
}
_JOINT_RECEIPT_KEYS = {
    "schema_version",
    "classification",
    "stage",
    "research_freeze_sha256",
    "s_dev_evaluated_candidate_sha256s",
    "candidate_count",
    "fold_ids",
    "fold_row_counts",
    "fold_rows_sha256",
    "bootstrap_replicates",
    "block_length",
    "critical_value_index",
    "seed_contract_sha256",
    "raw_result_sha256",
    "observed_means_ppm",
    "critical_value_ppm",
    "simultaneous_lower_bounds_ppm",
    "candidate_passes",
    "receipt_sha256",
}


def _receipt_integrity(condition: bool) -> None:
    if not condition:
        raise R2QualificationError("R2_STATISTICS_RECEIPT_INTEGRITY_MISMATCH")


def _receipt_hash(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    _receipt_integrity(type(value) is str and _HASH_RE.fullmatch(value) is not None)
    return str(value)


def _receipt_int(
    document: Mapping[str, object],
    key: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = document.get(key)
    _receipt_integrity(type(value) is int)
    normalized = int(value)
    _receipt_integrity(minimum is None or normalized >= minimum)
    _receipt_integrity(maximum is None or normalized <= maximum)
    return normalized


def _validate_receipt_coverage(document: Mapping[str, object]) -> int:
    episode_count = _receipt_int(document, "episode_count", minimum=100)
    qualified = _receipt_int(document, "coverage_qualified_count", minimum=0)
    eligible = _receipt_int(document, "coverage_eligible_count", minimum=1)
    coverage_ppm = _receipt_int(
        document,
        "coverage_ppm",
        minimum=0,
        maximum=PPM,
    )
    _receipt_integrity(qualified == episode_count)
    _receipt_integrity(qualified <= eligible)
    _receipt_integrity(qualified * PPM >= 950_000 * eligible)
    _receipt_integrity(
        coverage_ppm == round_half_even_divide(qualified * PPM, eligible)
    )
    return episode_count


def _validate_carrier_statistics_document(
    document: Mapping[str, object],
) -> None:
    _receipt_integrity(document.get("estimator_id") == "CIRCULAR_MBB_KELLY_V2")
    _receipt_hash(document, "seed_sha256")
    _receipt_hash(document, "bootstrap_distribution_sha256")
    _receipt_integrity(
        _receipt_int(document, "bootstrap_replicates", minimum=1)
        == PRODUCTION_BOOTSTRAP_REPLICATES
    )
    _receipt_integrity(
        _receipt_int(document, "block_length", minimum=1)
        == PRODUCTION_BLOCK_LENGTH
    )
    _receipt_integrity(
        _receipt_int(document, "lower_bound_index", minimum=0)
        == PRODUCTION_LOWER_BOUND_INDEX
    )
    _receipt_int(
        document,
        "observed_mean_ppm",
        minimum=-_MAX_RETURN_ABS_PPM,
        maximum=_MAX_RETURN_ABS_PPM,
    )
    lower_bound = _receipt_int(
        document,
        "lower_bound_ppm",
        minimum=-_MAX_RETURN_ABS_PPM,
        maximum=_MAX_RETURN_ABS_PPM,
    )
    full_kelly = _receipt_int(document, "full_kelly_ppm", minimum=0, maximum=PPM)
    bootstrap_kelly = _receipt_int(
        document,
        "bootstrap_kelly_5pct_ppm",
        minimum=0,
        maximum=PPM,
    )
    robust_kelly = _receipt_int(
        document,
        "robust_full_kelly_ppm",
        minimum=0,
        maximum=PPM,
    )
    half_kelly = _receipt_int(
        document,
        "half_kelly_ppm",
        minimum=0,
        maximum=PPM // 2,
    )
    expected_robust = min(full_kelly, bootstrap_kelly) if lower_bound > 0 else 0
    _receipt_integrity(robust_kelly == expected_robust)
    _receipt_integrity(half_kelly == robust_kelly // 2)
    expected_status = "PASS" if lower_bound > 0 and half_kelly > 0 else "FAIL"
    _receipt_integrity(document.get("evidence_status") == expected_status)


def _validate_development_receipt_document(
    document: Mapping[str, object],
) -> None:
    _receipt_integrity(set(document) == _DEVELOPMENT_RECEIPT_KEYS)
    _receipt_integrity(
        document.get("schema_version") == "GLD_R2_DEVELOPMENT_CARRIER_RECEIPT_V1"
        and document.get("classification") == "SYNTHETIC_ONLY"
        and document.get("stage") == "DEVELOPMENT"
        and document.get("carrier_id") in {"LC0", "BCS0"}
    )
    freeze_hash = _receipt_hash(document, "development_method_freeze_sha256")
    candidate_hash = _receipt_hash(document, "base_candidate_sha256")
    _receipt_hash(document, "episode_returns_sha256")
    _receipt_hash(document, "receipt_sha256")
    _validate_receipt_coverage(document)
    _validate_carrier_statistics_document(document)
    _receipt_integrity(
        document.get("seed_sha256")
        == canonical_sha256(
            {
                "schema_version": "GLD_R2_CARRIER_STAGE_SEED_V1",
                "stage": "DEVELOPMENT",
                "development_method_freeze_sha256": freeze_hash,
                "base_candidate_sha256": candidate_hash,
                "carrier_id": document["carrier_id"],
            }
        )
    )


def _validate_wf_carrier_receipt_document(
    document: Mapping[str, object],
) -> None:
    _receipt_integrity(set(document) == _WF_CARRIER_RECEIPT_KEYS)
    _receipt_integrity(
        document.get("schema_version") == "GLD_R2_WF_CARRIER_RECEIPT_V1"
        and document.get("classification") == "SYNTHETIC_ONLY"
        and document.get("stage") == "WALK_FORWARD_OOS"
        and document.get("carrier_id") in {"LC0", "BCS0"}
        and document.get("fold_ids") == list(_EXPECTED_FOLD_IDS)
    )
    freeze_hash = _receipt_hash(document, "research_freeze_sha256")
    candidate_hash = _receipt_hash(document, "evaluated_candidate_sha256")
    _receipt_hash(document, "fold_returns_sha256")
    _receipt_hash(document, "receipt_sha256")
    _receipt_integrity(_receipt_int(document, "fold_count") == len(_EXPECTED_FOLD_IDS))
    fold_counts = document.get("fold_episode_counts")
    _receipt_integrity(
        type(fold_counts) is list
        and len(fold_counts) == len(_EXPECTED_FOLD_IDS)
        and all(type(value) is int and value > 0 for value in fold_counts)
    )
    episode_count = _validate_receipt_coverage(document)
    _receipt_integrity(sum(fold_counts) == episode_count)  # type: ignore[arg-type]
    _validate_carrier_statistics_document(document)
    _receipt_integrity(
        document.get("seed_sha256")
        == canonical_sha256(
            {
                "schema_version": "GLD_R2_CARRIER_STAGE_SEED_V1",
                "stage": "WALK_FORWARD_OOS",
                "research_freeze_sha256": freeze_hash,
                "evaluated_candidate_sha256": candidate_hash,
                "carrier_id": document["carrier_id"],
                "fold_ids": list(_EXPECTED_FOLD_IDS),
            }
        )
    )


def _validate_joint_receipt_document(document: Mapping[str, object]) -> None:
    _receipt_integrity(set(document) == _JOINT_RECEIPT_KEYS)
    _receipt_integrity(
        document.get("schema_version")
        == "GLD_R2_JOINT_POLICY_BOOTSTRAP_RECEIPT_V1"
        and document.get("classification") == "SYNTHETIC_ONLY"
        and document.get("stage") == "WALK_FORWARD_JOINT_POLICY"
        and document.get("fold_ids") == list(_EXPECTED_FOLD_IDS)
    )
    freeze_hash = _receipt_hash(document, "research_freeze_sha256")
    _receipt_hash(document, "fold_rows_sha256")
    _receipt_hash(document, "raw_result_sha256")
    _receipt_hash(document, "receipt_sha256")
    candidates = document.get("s_dev_evaluated_candidate_sha256s")
    _receipt_integrity(
        type(candidates) is list
        and 1 <= len(candidates) <= 12
        and len(set(candidates)) == len(candidates)
        and all(
            type(value) is str and _HASH_RE.fullmatch(value) is not None
            for value in candidates
        )
    )
    candidate_count = _receipt_int(document, "candidate_count", minimum=1, maximum=12)
    _receipt_integrity(candidate_count == len(candidates))  # type: ignore[arg-type]
    fold_counts = document.get("fold_row_counts")
    _receipt_integrity(
        type(fold_counts) is list
        and len(fold_counts) == len(_EXPECTED_FOLD_IDS)
        and all(type(value) is int and value > 0 for value in fold_counts)
    )
    _receipt_integrity(
        _receipt_int(document, "bootstrap_replicates", minimum=1)
        == PRODUCTION_BOOTSTRAP_REPLICATES
        and _receipt_int(document, "block_length", minimum=1)
        == PRODUCTION_BLOCK_LENGTH
        and _receipt_int(document, "critical_value_index", minimum=0)
        == PRODUCTION_JOINT_CRITICAL_VALUE_INDEX
    )
    expected_seed_contract = canonical_sha256(
        {
            "schema_version": "GLD_R2_JOINT_SEED_CONTRACT_V1",
            "research_freeze_sha256": freeze_hash,
            "fold_ids": list(_EXPECTED_FOLD_IDS),
            "replicates": PRODUCTION_BOOTSTRAP_REPLICATES,
            "block_length": PRODUCTION_BLOCK_LENGTH,
            "derivation": "FREEZE_FOLD_REPLICATE_BLOCK_ORDINAL",
        }
    )
    _receipt_integrity(
        _receipt_hash(document, "seed_contract_sha256") == expected_seed_contract
    )
    observed = document.get("observed_means_ppm")
    lower_bounds = document.get("simultaneous_lower_bounds_ppm")
    passes = document.get("candidate_passes")
    _receipt_integrity(
        type(observed) is list
        and len(observed) == candidate_count
        and all(
            type(value) is int and -_MAX_RETURN_ABS_PPM <= value <= _MAX_RETURN_ABS_PPM
            for value in observed
        )
        and type(lower_bounds) is list
        and len(lower_bounds) == candidate_count
        and all(type(value) is int for value in lower_bounds)
        and type(passes) is list
        and len(passes) == candidate_count
        and all(type(value) is bool for value in passes)
    )
    critical_value = _receipt_int(document, "critical_value_ppm")
    _receipt_integrity(
        lower_bounds
        == [value - critical_value for value in observed]  # type: ignore[union-attr]
        and passes == [value > 0 for value in lower_bounds]  # type: ignore[union-attr]
    )


def _validate_statistics_receipt_document(
    document: Mapping[str, object],
) -> None:
    schema = document.get("schema_version")
    if schema == "GLD_R2_DEVELOPMENT_CARRIER_RECEIPT_V1":
        _validate_development_receipt_document(document)
    elif schema == "GLD_R2_WF_CARRIER_RECEIPT_V1":
        _validate_wf_carrier_receipt_document(document)
    elif schema == "GLD_R2_JOINT_POLICY_BOOTSTRAP_RECEIPT_V1":
        _validate_joint_receipt_document(document)
    else:
        raise R2QualificationError("R2_STATISTICS_RECEIPT_INTEGRITY_MISMATCH")


def build_development_carrier_receipt(
    *,
    returns_ppm: Sequence[int],
    development_method_freeze_sha256: str,
    base_candidate_sha256: str,
    carrier_id: str,
    coverage_qualified_count: int,
    coverage_eligible_count: int,
) -> CanonicalStatisticsReceipt:
    """Build Development evidence without depending on a research freeze."""

    returns = _normalize_returns(returns_ppm)
    if len(returns) < 100:
        raise R2QualificationError("INSUFFICIENT_EVIDENCE")
    freeze_hash = _hash(
        development_method_freeze_sha256,
        "R2_DEVELOPMENT_METHOD_FREEZE_HASH_INVALID",
    )
    candidate_hash = _hash(
        base_candidate_sha256,
        "R2_BASE_CANDIDATE_HASH_INVALID",
    )
    carrier = _carrier_id(carrier_id)
    qualified, eligible, coverage_ppm = _qualified_coverage(
        qualified_count=coverage_qualified_count,
        eligible_count=coverage_eligible_count,
        included_episode_count=len(returns),
    )
    seed = canonical_sha256(
        {
            "schema_version": "GLD_R2_CARRIER_STAGE_SEED_V1",
            "stage": "DEVELOPMENT",
            "development_method_freeze_sha256": freeze_hash,
            "base_candidate_sha256": candidate_hash,
            "carrier_id": carrier,
        }
    )
    result = run_carrier_episode_bootstrap(returns, seed_sha256=seed)
    unsigned = {
        "schema_version": "GLD_R2_DEVELOPMENT_CARRIER_RECEIPT_V1",
        "classification": "SYNTHETIC_ONLY",
        "stage": "DEVELOPMENT",
        "development_method_freeze_sha256": freeze_hash,
        "base_candidate_sha256": candidate_hash,
        "carrier_id": carrier,
        "episode_count": len(returns),
        "episode_returns_sha256": canonical_sha256(list(returns)),
        "coverage_qualified_count": qualified,
        "coverage_eligible_count": eligible,
        "coverage_ppm": coverage_ppm,
        **_carrier_receipt_statistics(result),
    }
    return _seal_statistics_receipt(unsigned)


def build_walk_forward_carrier_receipt(
    *,
    fold_returns_ppm: Mapping[str, Sequence[int]],
    research_freeze_sha256: str,
    evaluated_candidate_sha256: str,
    carrier_id: str,
    coverage_qualified_count: int,
    coverage_eligible_count: int,
) -> CanonicalStatisticsReceipt:
    """Build WF-only carrier evidence over exactly five nonempty folds."""

    if not isinstance(fold_returns_ppm, Mapping):
        raise R2QualificationError("R2_WF_FOLD_SET_INVALID")
    if tuple(sorted(fold_returns_ppm)) != _EXPECTED_FOLD_IDS:
        raise R2QualificationError("R2_WF_FOLD_SET_INVALID")
    normalized_folds = tuple(
        (fold_id, _normalize_returns(fold_returns_ppm[fold_id]))
        for fold_id in _EXPECTED_FOLD_IDS
    )
    returns = tuple(
        value for _, fold_returns in normalized_folds for value in fold_returns
    )
    if len(returns) < 100:
        raise R2QualificationError("INSUFFICIENT_EVIDENCE")
    freeze_hash = _hash(
        research_freeze_sha256,
        "R2_RESEARCH_FREEZE_HASH_INVALID",
    )
    candidate_hash = _hash(
        evaluated_candidate_sha256,
        "R2_EVALUATED_CANDIDATE_HASH_INVALID",
    )
    carrier = _carrier_id(carrier_id)
    qualified, eligible, coverage_ppm = _qualified_coverage(
        qualified_count=coverage_qualified_count,
        eligible_count=coverage_eligible_count,
        included_episode_count=len(returns),
    )
    seed = canonical_sha256(
        {
            "schema_version": "GLD_R2_CARRIER_STAGE_SEED_V1",
            "stage": "WALK_FORWARD_OOS",
            "research_freeze_sha256": freeze_hash,
            "evaluated_candidate_sha256": candidate_hash,
            "carrier_id": carrier,
            "fold_ids": list(_EXPECTED_FOLD_IDS),
        }
    )
    result = run_carrier_episode_bootstrap(returns, seed_sha256=seed)
    fold_document = [
        {"fold_id": fold_id, "returns_ppm": list(values)}
        for fold_id, values in normalized_folds
    ]
    unsigned = {
        "schema_version": "GLD_R2_WF_CARRIER_RECEIPT_V1",
        "classification": "SYNTHETIC_ONLY",
        "stage": "WALK_FORWARD_OOS",
        "research_freeze_sha256": freeze_hash,
        "evaluated_candidate_sha256": candidate_hash,
        "carrier_id": carrier,
        "fold_ids": list(_EXPECTED_FOLD_IDS),
        "fold_episode_counts": [len(values) for _, values in normalized_folds],
        "fold_count": len(normalized_folds),
        "episode_count": len(returns),
        "fold_returns_sha256": canonical_sha256(fold_document),
        "coverage_qualified_count": qualified,
        "coverage_eligible_count": eligible,
        "coverage_ppm": coverage_ppm,
        **_carrier_receipt_statistics(result),
    }
    return _seal_statistics_receipt(unsigned)


@dataclass(frozen=True, slots=True)
class PolicyBootstrapResult:
    """Mean-only result for one policy path; it intentionally has no Kelly."""

    estimator_id: str
    seed_sha256: str
    row_count: int
    replicates: int
    block_length: int
    lower_bound_index: int
    observed_mean_ppm: int
    bootstrap_means_ppm: tuple[int, ...]
    lower_bound_ppm: int
    qualification_eligible: bool
    passed: bool


def _single_stream_block_start(
    seed: bytes,
    *,
    replicate_index: int,
    block_ordinal: int,
    sample_size: int,
) -> int:
    digest = sha256(
        seed
        + replicate_index.to_bytes(8, "big", signed=False)
        + block_ordinal.to_bytes(8, "big", signed=False)
    ).digest()
    return int.from_bytes(digest, "big", signed=False) % sample_size


def evaluate_single_policy_bootstrap(
    session_returns_ppm: Sequence[int],
    *,
    seed_sha256: str,
    replicates: int = PRODUCTION_BOOTSTRAP_REPLICATES,
    block_length: int = PRODUCTION_BLOCK_LENGTH,
    lower_bound_index: int = PRODUCTION_LOWER_BOUND_INDEX,
) -> PolicyBootstrapResult:
    """Run a deterministic single-policy session MBB without Kelly."""

    returns = _normalize_returns(session_returns_ppm)
    replicate_count, rank_index = _validate_bootstrap_controls(
        replicates=replicates,
        selected_index=lower_bound_index,
    )
    block = _integer(
        block_length,
        minimum=1,
        maximum=10_000,
        reason="R2_BOOTSTRAP_BLOCK_LENGTH_INVALID",
    )
    normalized_seed = _hash(seed_sha256, "R2_BOOTSTRAP_SEED_INVALID")
    sample_size = len(returns)
    blocks_needed = (sample_size + block - 1) // block
    if blocks_needed * replicate_count > _MAX_POLICY_BOOTSTRAP_BLOCK_DRAWS:
        raise R2QualificationError("R2_BOOTSTRAP_WORK_BOUND_EXCEEDED")
    prefix = [0]
    for value in returns + returns:
        prefix.append(prefix[-1] + value)
    seed = bytes.fromhex(normalized_seed)
    means: list[int] = []
    for replicate_index in range(replicate_count):
        sample_sum = 0
        remaining = sample_size
        for block_ordinal in range(blocks_needed):
            take = min(block, remaining)
            start = _single_stream_block_start(
                seed,
                replicate_index=replicate_index,
                block_ordinal=block_ordinal,
                sample_size=sample_size,
            )
            sample_sum += prefix[start + take] - prefix[start]
            remaining -= take
        means.append(round_half_even_divide(sample_sum, sample_size))
    lower_bound = sorted(means)[rank_index]
    observed = round_half_even_divide(sum(returns), sample_size)
    qualification_eligible = (
        replicate_count == PRODUCTION_BOOTSTRAP_REPLICATES
        and block == PRODUCTION_BLOCK_LENGTH
        and rank_index == PRODUCTION_LOWER_BOUND_INDEX
    )
    return PolicyBootstrapResult(
        estimator_id="R2_POLICY_SESSION_CIRCULAR_MBB_V1",
        seed_sha256=normalized_seed,
        row_count=sample_size,
        replicates=replicate_count,
        block_length=block,
        lower_bound_index=rank_index,
        observed_mean_ppm=observed,
        bootstrap_means_ppm=tuple(means),
        lower_bound_ppm=lower_bound,
        qualification_eligible=qualification_eligible,
        passed=qualification_eligible and lower_bound > 0,
    )


@dataclass(frozen=True, slots=True)
class JointPolicyBootstrapResult:
    """Synchronized per-fold, row-weighted max-statistic result."""

    estimator_id: str
    research_freeze_sha256: str
    candidate_ids: tuple[str, ...]
    fold_ids: tuple[str, ...]
    fold_row_counts: tuple[int, ...]
    fold_rows_sha256: str
    replicates: int
    block_length: int
    critical_value_index: int
    observed_means_ppm: tuple[int, ...]
    bootstrap_means_ppm: tuple[tuple[int, ...], ...]
    max_centered_errors_ppm: tuple[int, ...]
    critical_value_ppm: int
    simultaneous_lower_bounds_ppm: tuple[int, ...]
    candidate_passes: tuple[bool, ...]
    qualification_eligible: bool
    seed_contract_sha256: str


def _normalize_joint_folds(
    fold_rows_ppm: Mapping[str, Sequence[Sequence[int]]],
    *,
    candidate_count: int,
) -> tuple[tuple[str, tuple[tuple[int, ...], ...]], ...]:
    if not isinstance(fold_rows_ppm, Mapping):
        raise R2QualificationError("R2_JOINT_FOLDS_INVALID")
    if tuple(sorted(fold_rows_ppm)) != _EXPECTED_FOLD_IDS:
        raise R2QualificationError("R2_JOINT_FOLD_SET_INVALID")
    result: list[tuple[str, tuple[tuple[int, ...], ...]]] = []
    for fold_id in _EXPECTED_FOLD_IDS:
        raw_rows = fold_rows_ppm[fold_id]
        if isinstance(raw_rows, (str, bytes)) or not isinstance(raw_rows, Sequence):
            raise R2QualificationError("R2_JOINT_ROWS_INVALID")
        rows: list[tuple[int, ...]] = []
        for raw_row in raw_rows:
            if isinstance(raw_row, (str, bytes)) or not isinstance(raw_row, Sequence):
                raise R2QualificationError("INSUFFICIENT_EVIDENCE")
            if len(raw_row) != candidate_count:
                raise R2QualificationError("R2_JOINT_ROW_WIDTH_INVALID")
            try:
                row = tuple(
                    _integer(
                        value,
                        minimum=-_MAX_RETURN_ABS_PPM,
                        maximum=_MAX_RETURN_ABS_PPM,
                        reason="INSUFFICIENT_EVIDENCE",
                    )
                    for value in raw_row
                )
            except R2QualificationError as exc:
                if exc.reason_code == "INSUFFICIENT_EVIDENCE":
                    raise
                raise R2QualificationError("INSUFFICIENT_EVIDENCE") from exc
            rows.append(row)
        if not rows:
            raise R2QualificationError("INSUFFICIENT_EVIDENCE")
        result.append((fold_id, tuple(rows)))
    return tuple(result)


def _joint_block_start(
    *,
    research_freeze_sha256: str,
    fold_id: str,
    replicate_index: int,
    block_ordinal: int,
    block_length: int,
    sample_size: int,
) -> int:
    # Every required seed component is directly visible in this canonical
    # document.  No process-global RNG state is consulted.
    digest = canonical_sha256(
        {
            "schema_version": "GLD_R2_JOINT_BLOCK_START_V1",
            "research_freeze_sha256": research_freeze_sha256,
            "fold_id": fold_id,
            "replicate_index": replicate_index,
            "block_ordinal": block_ordinal,
            "block_length": block_length,
        }
    )
    return int(digest, 16) % sample_size


def _joint_fold_rows_sha256(
    folds: Sequence[tuple[str, Sequence[Sequence[int]]]],
) -> str:
    """Hash every normalized joint row in exact fold, row, and column order."""

    return canonical_sha256(
        {
            "schema_version": "GLD_R2_JOINT_FOLD_ROWS_V1",
            "folds": [
                {
                    "fold_id": fold_id,
                    "rows_ppm": [list(row) for row in rows],
                }
                for fold_id, rows in folds
            ],
        }
    )


def evaluate_joint_policy_bootstrap(
    fold_rows_ppm: Mapping[str, Sequence[Sequence[int]]],
    *,
    candidate_ids: Sequence[str],
    research_freeze_sha256: str,
    replicates: int = PRODUCTION_BOOTSTRAP_REPLICATES,
    block_length: int = PRODUCTION_BLOCK_LENGTH,
    critical_value_index: int = PRODUCTION_JOINT_CRITICAL_VALUE_INDEX,
) -> JointPolicyBootstrapResult:
    """Evaluate the frozen K-family with synchronized circular row sampling.

    The approved centering contract is exact and intentionally explicit:
    ``E = m* - m; D = max(E); LCB = m - sorted(D)[index]``.
    """

    if isinstance(candidate_ids, (str, bytes)) or not isinstance(candidate_ids, Sequence):
        raise R2QualificationError("R2_CANDIDATE_IDS_INVALID")
    candidates = tuple(
        _identifier(value, "R2_CANDIDATE_ID_INVALID") for value in candidate_ids
    )
    if not candidates or len(candidates) > 12 or len(set(candidates)) != len(candidates):
        raise R2QualificationError("R2_CANDIDATE_IDS_INVALID")
    freeze_hash = _hash(research_freeze_sha256)
    replicate_count, critical_index = _validate_bootstrap_controls(
        replicates=replicates,
        selected_index=critical_value_index,
    )
    block = _integer(
        block_length,
        minimum=1,
        maximum=10_000,
        reason="R2_BOOTSTRAP_BLOCK_LENGTH_INVALID",
    )
    if block not in {PRODUCTION_BLOCK_LENGTH, *REPORT_ONLY_BLOCK_LENGTHS}:
        raise R2QualificationError("R2_BOOTSTRAP_BLOCK_LENGTH_INVALID")
    folds = _normalize_joint_folds(
        fold_rows_ppm,
        candidate_count=len(candidates),
    )
    fold_row_counts = tuple(len(rows) for _, rows in folds)
    fold_rows_sha256 = _joint_fold_rows_sha256(folds)
    total_rows = sum(fold_row_counts)
    vector_additions = sum(
        ((len(rows) + block - 1) // block) * len(candidates)
        for _, rows in folds
    ) * replicate_count
    if vector_additions > _MAX_JOINT_BLOCK_VECTOR_ADDITIONS:
        raise R2QualificationError("R2_JOINT_BOOTSTRAP_WORK_BOUND_EXCEEDED")

    original_sums = [0] * len(candidates)
    prefix_by_fold: list[tuple[str, int, tuple[tuple[int, ...], ...]]] = []
    for fold_id, rows in folds:
        for row in rows:
            for candidate_index, value in enumerate(row):
                original_sums[candidate_index] += value
        doubled = rows + rows
        prefixes: list[list[int]] = [[0] for _ in candidates]
        for row in doubled:
            for candidate_index, value in enumerate(row):
                prefixes[candidate_index].append(
                    prefixes[candidate_index][-1] + value
                )
        prefix_by_fold.append(
            (
                fold_id,
                len(rows),
                tuple(tuple(values) for values in prefixes),
            )
        )
    observed_means = tuple(
        round_half_even_divide(value, total_rows) for value in original_sums
    )

    bootstrap_means: list[tuple[int, ...]] = []
    max_errors: list[int] = []
    for replicate_index in range(replicate_count):
        replicate_sums = [0] * len(candidates)
        for fold_id, sample_size, prefixes in prefix_by_fold:
            blocks_needed = (sample_size + block - 1) // block
            remaining = sample_size
            for block_ordinal in range(blocks_needed):
                take = min(block, remaining)
                start = _joint_block_start(
                    research_freeze_sha256=freeze_hash,
                    fold_id=fold_id,
                    replicate_index=replicate_index,
                    block_ordinal=block_ordinal,
                    block_length=block,
                    sample_size=sample_size,
                )
                for candidate_index, prefix in enumerate(prefixes):
                    replicate_sums[candidate_index] += (
                        prefix[start + take] - prefix[start]
                    )
                remaining -= take
        means = tuple(
            round_half_even_divide(value, total_rows) for value in replicate_sums
        )
        bootstrap_means.append(means)
        max_errors.append(
            max(
                boot - observed
                for boot, observed in zip(means, observed_means)
            )
        )

    critical_value = sorted(max_errors)[critical_index]
    lower_bounds = tuple(
        observed - critical_value for observed in observed_means
    )
    qualification_eligible = (
        replicate_count == PRODUCTION_BOOTSTRAP_REPLICATES
        and block == PRODUCTION_BLOCK_LENGTH
        and critical_index == PRODUCTION_JOINT_CRITICAL_VALUE_INDEX
    )
    return JointPolicyBootstrapResult(
        estimator_id="R2_SYNCHRONIZED_FOLD_WEIGHTED_MAX_MBB_V1",
        research_freeze_sha256=freeze_hash,
        candidate_ids=candidates,
        fold_ids=_EXPECTED_FOLD_IDS,
        fold_row_counts=fold_row_counts,
        fold_rows_sha256=fold_rows_sha256,
        replicates=replicate_count,
        block_length=block,
        critical_value_index=critical_index,
        observed_means_ppm=observed_means,
        bootstrap_means_ppm=tuple(bootstrap_means),
        max_centered_errors_ppm=tuple(max_errors),
        critical_value_ppm=critical_value,
        simultaneous_lower_bounds_ppm=lower_bounds,
        candidate_passes=tuple(
            qualification_eligible and value > 0 for value in lower_bounds
        ),
        qualification_eligible=qualification_eligible,
        seed_contract_sha256=canonical_sha256(
            {
                "schema_version": "GLD_R2_JOINT_SEED_CONTRACT_V1",
                "research_freeze_sha256": freeze_hash,
                "fold_ids": list(_EXPECTED_FOLD_IDS),
                "replicates": replicate_count,
                "block_length": block,
                "derivation": "FREEZE_FOLD_REPLICATE_BLOCK_ORDINAL",
            }
        ),
    )


def _ordered_sequence_sha256(values: Sequence[Sequence[int]]) -> str:
    digest = sha256()
    for value in values:
        digest.update(canonical_json_bytes(list(value)))
        digest.update(b"\n")
    return digest.hexdigest()


def build_joint_policy_bootstrap_receipt(
    *,
    fold_rows_ppm: Mapping[str, Sequence[Sequence[int]]],
    frozen_s_dev_sha256s: Sequence[str],
    matrix_candidate_sha256s: Sequence[str],
    research_freeze_sha256: str,
) -> CanonicalStatisticsReceipt:
    """Seal the production joint result against the exact frozen S_dev order."""

    if (
        isinstance(frozen_s_dev_sha256s, (str, bytes))
        or not isinstance(frozen_s_dev_sha256s, Sequence)
    ):
        raise R2QualificationError("R2_FROZEN_S_DEV_INVALID")
    frozen_s_dev = tuple(
        _hash(value, "R2_FROZEN_S_DEV_INVALID")
        for value in frozen_s_dev_sha256s
    )
    if (
        not 1 <= len(frozen_s_dev) <= 12
        or len(set(frozen_s_dev)) != len(frozen_s_dev)
    ):
        raise R2QualificationError("R2_FROZEN_S_DEV_INVALID")
    if (
        isinstance(matrix_candidate_sha256s, (str, bytes))
        or not isinstance(matrix_candidate_sha256s, Sequence)
    ):
        raise R2QualificationError("R2_JOINT_COLUMN_IDENTITY_MISMATCH")
    matrix_candidates = tuple(matrix_candidate_sha256s)
    if matrix_candidates != frozen_s_dev:
        raise R2QualificationError("R2_JOINT_COLUMN_IDENTITY_MISMATCH")
    freeze_hash = _hash(
        research_freeze_sha256,
        "R2_RESEARCH_FREEZE_HASH_INVALID",
    )
    result = evaluate_joint_policy_bootstrap(
        fold_rows_ppm,
        candidate_ids=frozen_s_dev,
        research_freeze_sha256=freeze_hash,
    )
    if not result.qualification_eligible:
        raise R2QualificationError("R2_PRODUCTION_ESTIMATOR_CONTROLS_REQUIRED")
    raw_result = {
        "schema_version": "GLD_R2_JOINT_RAW_RESULT_V1",
        "research_freeze_sha256": freeze_hash,
        "s_dev_evaluated_candidate_sha256s": list(frozen_s_dev),
        "fold_ids": list(result.fold_ids),
        "fold_row_counts": list(result.fold_row_counts),
        "fold_rows_sha256": result.fold_rows_sha256,
        "bootstrap_replicates": result.replicates,
        "block_length": result.block_length,
        "critical_value_index": result.critical_value_index,
        "seed_contract_sha256": result.seed_contract_sha256,
        "observed_means_ppm": list(result.observed_means_ppm),
        "critical_value_ppm": result.critical_value_ppm,
        "simultaneous_lower_bounds_ppm": list(
            result.simultaneous_lower_bounds_ppm
        ),
        "candidate_passes": list(result.candidate_passes),
        "bootstrap_means_sha256": _ordered_sequence_sha256(
            result.bootstrap_means_ppm
        ),
        "max_centered_errors_sha256": canonical_sha256(
            list(result.max_centered_errors_ppm)
        ),
    }
    unsigned = {
        "schema_version": "GLD_R2_JOINT_POLICY_BOOTSTRAP_RECEIPT_V1",
        "classification": "SYNTHETIC_ONLY",
        "stage": "WALK_FORWARD_JOINT_POLICY",
        "research_freeze_sha256": freeze_hash,
        "s_dev_evaluated_candidate_sha256s": list(frozen_s_dev),
        "candidate_count": len(frozen_s_dev),
        "fold_ids": list(result.fold_ids),
        "fold_row_counts": list(result.fold_row_counts),
        "fold_rows_sha256": result.fold_rows_sha256,
        "bootstrap_replicates": result.replicates,
        "block_length": result.block_length,
        "critical_value_index": result.critical_value_index,
        "seed_contract_sha256": result.seed_contract_sha256,
        "raw_result_sha256": canonical_sha256(raw_result),
        "observed_means_ppm": list(result.observed_means_ppm),
        "critical_value_ppm": result.critical_value_ppm,
        "simultaneous_lower_bounds_ppm": list(
            result.simultaneous_lower_bounds_ppm
        ),
        "candidate_passes": list(result.candidate_passes),
    }
    return _seal_statistics_receipt(unsigned)


def evaluate_joint_policy_sensitivities(
    fold_rows_ppm: Mapping[str, Sequence[Sequence[int]]],
    *,
    candidate_ids: Sequence[str],
    research_freeze_sha256: str,
    replicates: int = PRODUCTION_BOOTSTRAP_REPLICATES,
    critical_value_index: int = PRODUCTION_JOINT_CRITICAL_VALUE_INDEX,
) -> tuple[JointPolicyBootstrapResult, JointPolicyBootstrapResult]:
    """Return block-10/40 diagnostics that can never qualify a policy."""

    return tuple(
        evaluate_joint_policy_bootstrap(
            fold_rows_ppm,
            candidate_ids=candidate_ids,
            research_freeze_sha256=research_freeze_sha256,
            replicates=replicates,
            block_length=block_length,
            critical_value_index=critical_value_index,
        )
        for block_length in REPORT_ONLY_BLOCK_LENGTHS
    )  # type: ignore[return-value]


__all__ = [
    "CanonicalStatisticsReceipt",
    "CarrierBootstrapKernelResult",
    "JointPolicyBootstrapResult",
    "PPM",
    "PRODUCTION_BLOCK_LENGTH",
    "PRODUCTION_BOOTSTRAP_REPLICATES",
    "PRODUCTION_JOINT_CRITICAL_VALUE_INDEX",
    "PRODUCTION_LOWER_BOUND_INDEX",
    "PolicyBootstrapResult",
    "REPORT_ONLY_BLOCK_LENGTHS",
    "build_development_carrier_receipt",
    "build_joint_policy_bootstrap_receipt",
    "build_walk_forward_carrier_receipt",
    "evaluate_joint_policy_bootstrap",
    "evaluate_joint_policy_sensitivities",
    "evaluate_single_policy_bootstrap",
    "run_carrier_episode_bootstrap",
]
