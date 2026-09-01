"""Deterministic Historical Structure Evidence V2 for Entry Decision F0.

The module deliberately owns only the research evidence boundary.  It derives
episode returns from executable quote sides, estimates walk-forward evidence,
and emits immutable canonical receipts for LC0 and BCS0.  Development and
sealed OOS episodes remain visible lineage but cannot enter the estimator.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import (
    Context,
    Decimal,
    DecimalException,
    ROUND_HALF_EVEN,
    localcontext,
)
from fractions import Fraction
from hashlib import sha256
import json
import re


PPM = 1_000_000
_WEALTH_SCALE = PPM * PPM
_DERIVATIVE_SCALE = 10**30
_MAX_BOOTSTRAP_WORK_ITEMS = 2_560_000
_MAX_WALK_FORWARD_EPISODES = 256
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
MAX_EVIDENCE_INTEGER = 2**63 - 1
MAX_EVIDENCE_CANONICAL_INTEGER_DIGITS = 64

_BUNDLE_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "scope",
        "underlying",
        "carriers",
    }
)
_CARRIER_KEYS = frozenset(
    {
        "schema_version",
        "carrier_id",
        "evidence_receipt_id",
        "episode_cohort_id",
        "distribution_id",
        "entry_clock_id",
        "exit_clock_id",
        "cost_model_version",
        "rule_version",
        "entry_policy_sha256",
        "fee_schedule_sha256",
        "exit_policy_sha256",
        "coverage",
        "fold_manifest",
        "episodes",
        "estimator",
    }
)
_EPISODE_KEYS = frozenset(
    {
        "episode_id",
        "fold_id",
        "split",
        "entry_utc_ns",
        "exit_utc_ns",
        "multiplier",
        "long_entry_ask_nano_usd",
        "long_exit_bid_nano_usd",
        "short_entry_bid_nano_usd",
        "short_exit_ask_nano_usd",
        "opening_fees_nano_usd",
        "closing_fees_nano_usd",
        "input_sha256",
    }
)
_COVERAGE_KEYS = frozenset(
    {
        "eligible_episode_count",
        "included_episode_count",
        "excluded_episode_count",
        "coverage_ppm",
        "exclusion_reasons",
    }
)
_EXCLUSION_KEYS = frozenset({"reason_code", "count"})
_FOLD_KEYS = frozenset(
    {"fold_id", "train_end_utc_ns", "test_start_utc_ns", "test_end_utc_ns"}
)
_ESTIMATOR_KEYS = frozenset(
    {
        "estimator_id",
        "uncertainty_method_id",
        "bootstrap_replicates",
        "block_length",
        "lower_bound_rank",
        "kelly_quantile_rank",
        "rounding_mode",
        "hash_start_method",
    }
)


class EvidenceValidationError(ValueError):
    """Fail-closed evidence error carrying a stable reason code."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        if type(reason_code) is not str or _REASON_RE.fullmatch(reason_code) is None:
            raise ValueError("reason_code must be stable uppercase ASCII")
        if detail is not None and (type(detail) is not str or not detail.isascii()):
            raise ValueError("detail must be ASCII text")
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(reason_code if detail is None else f"{reason_code}: {detail}")


def _canonical_validate(value: object, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    counter = [0] if nodes is None else nodes
    if depth > 32:
        raise EvidenceValidationError("EVIDENCE_CANONICAL_DEPTH_EXCEEDED")
    counter[0] += 1
    if counter[0] > 250_000:
        raise EvidenceValidationError("EVIDENCE_CANONICAL_SIZE_EXCEEDED")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) >= 10**MAX_EVIDENCE_CANONICAL_INTEGER_DIGITS:
            raise EvidenceValidationError("EVIDENCE_INTEGER_RANGE_INVALID")
        return
    if type(value) is str:
        if len(value) > 1_048_576:
            raise EvidenceValidationError("EVIDENCE_STRING_TOO_LONG")
        return
    if type(value) is float:
        raise EvidenceValidationError("EVIDENCE_FLOAT_FORBIDDEN")
    if type(value) is list:
        for item in value:
            _canonical_validate(item, depth=depth + 1, nodes=counter)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or not key or len(key) > 256:
                raise EvidenceValidationError("EVIDENCE_CANONICAL_KEY_INVALID")
            _canonical_validate(item, depth=depth + 1, nodes=counter)
        return
    raise EvidenceValidationError("EVIDENCE_CANONICAL_TYPE_INVALID")


def _canonical_bytes(value: object) -> bytes:
    _canonical_validate(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise EvidenceValidationError("EVIDENCE_CANONICAL_ENCODING_FAILED") from exc


def _canonical_sha256(value: object) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def _closed(value: object, keys: frozenset[str], reason: str) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != keys:
        raise EvidenceValidationError(reason)
    return value


def _list(value: object, reason: str) -> list[object]:
    if type(value) is not list:
        raise EvidenceValidationError(reason)
    return value


def _integer(
    value: object,
    *,
    minimum: int | None = None,
    maximum: int | None = MAX_EVIDENCE_INTEGER,
) -> int:
    if type(value) is not int:
        raise EvidenceValidationError("EVIDENCE_INTEGER_REQUIRED")
    if minimum is not None and value < minimum:
        raise EvidenceValidationError("EVIDENCE_INTEGER_RANGE_INVALID")
    if maximum is not None and value > maximum:
        raise EvidenceValidationError("EVIDENCE_INTEGER_RANGE_INVALID")
    return value


def _identifier(value: object, reason: str = "EVIDENCE_IDENTIFIER_INVALID") -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise EvidenceValidationError(reason)
    return value


def _reason(value: object) -> str:
    if type(value) is not str or _REASON_RE.fullmatch(value) is None:
        raise EvidenceValidationError("EVIDENCE_EXCLUSION_REASON_INVALID")
    return value


def _hash(value: object) -> str:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        raise EvidenceValidationError("EVIDENCE_HASH_INVALID")
    return value


def round_half_even_divide(numerator: int, denominator: int) -> int:
    """Return signed integer division rounded to the nearest even integer."""

    if type(numerator) is not int or type(denominator) is not int:
        raise EvidenceValidationError("EVIDENCE_INTEGER_REQUIRED")
    if denominator <= 0:
        raise EvidenceValidationError("EVIDENCE_DENOMINATOR_INVALID")
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    doubled = remainder * 2
    if doubled > denominator or (doubled == denominator and quotient % 2 == 1):
        quotient += 1
    return sign * quotient


def _normalize_episode(value: object, carrier_id: str) -> dict[str, object]:
    raw = _closed(value, _EPISODE_KEYS, "EVIDENCE_EPISODE_SCHEMA_INVALID")
    split = raw["split"]
    if split not in {"DEVELOPMENT", "WALK_FORWARD_OOS", "SEALED_OOS"}:
        raise EvidenceValidationError("EVIDENCE_SPLIT_INVALID")
    entry_ns = _integer(raw["entry_utc_ns"], minimum=0)
    exit_ns = _integer(raw["exit_utc_ns"], minimum=0)
    if exit_ns <= entry_ns:
        raise EvidenceValidationError("EVIDENCE_EPISODE_TIME_INVALID")
    long_entry = _integer(raw["long_entry_ask_nano_usd"], minimum=1)
    long_exit = _integer(raw["long_exit_bid_nano_usd"], minimum=0)
    short_entry_raw = raw["short_entry_bid_nano_usd"]
    short_exit_raw = raw["short_exit_ask_nano_usd"]
    if carrier_id == "LC0":
        if short_entry_raw is not None or short_exit_raw is not None:
            raise EvidenceValidationError("EVIDENCE_LC_SHORT_LEG_FORBIDDEN")
        short_entry: int | None = None
        short_exit: int | None = None
    else:
        short_entry = _integer(short_entry_raw, minimum=0)
        short_exit = _integer(short_exit_raw, minimum=0)
    return {
        "episode_id": _identifier(raw["episode_id"], "EVIDENCE_EPISODE_ID_INVALID"),
        "fold_id": _identifier(raw["fold_id"], "EVIDENCE_FOLD_ID_INVALID"),
        "split": split,
        "entry_utc_ns": entry_ns,
        "exit_utc_ns": exit_ns,
        "multiplier": _integer(raw["multiplier"], minimum=1, maximum=100_000),
        "long_entry_ask_nano_usd": long_entry,
        "long_exit_bid_nano_usd": long_exit,
        "short_entry_bid_nano_usd": short_entry,
        "short_exit_ask_nano_usd": short_exit,
        "opening_fees_nano_usd": _integer(raw["opening_fees_nano_usd"], minimum=0),
        "closing_fees_nano_usd": _integer(raw["closing_fees_nano_usd"], minimum=0),
        "input_sha256": _hash(raw["input_sha256"]),
    }


def derive_episode_after_cost(value: object, carrier_id: str) -> dict[str, object]:
    """Derive one after-cost return from executable entry/exit quote sides."""

    if carrier_id not in {"LC0", "BCS0"}:
        raise EvidenceValidationError("EVIDENCE_CARRIER_INVALID")
    episode = _normalize_episode(value, carrier_id)
    multiplier = int(episode["multiplier"])
    long_entry = int(episode["long_entry_ask_nano_usd"])
    long_exit = int(episode["long_exit_bid_nano_usd"])
    if carrier_id == "LC0":
        entry_debit = long_entry * multiplier
        exit_value = long_exit * multiplier
    else:
        short_entry = int(episode["short_entry_bid_nano_usd"])
        short_exit = int(episode["short_exit_ask_nano_usd"])
        entry_debit = (long_entry - short_entry) * multiplier
        exit_value = (long_exit - short_exit) * multiplier
    if entry_debit <= 0:
        raise EvidenceValidationError("EVIDENCE_ENTRY_DEBIT_INVALID")
    after_cost_profit = (
        exit_value
        - entry_debit
        - int(episode["opening_fees_nano_usd"])
        - int(episode["closing_fees_nano_usd"])
    )
    episode_return = round_half_even_divide(after_cost_profit * PPM, entry_debit)
    return {
        **episode,
        "entry_debit_nano_usd": entry_debit,
        "exit_value_nano_usd": exit_value,
        "after_cost_profit_nano_usd": after_cost_profit,
        "after_cost_return_on_entry_debit_ppm": episode_return,
    }


def _maximum_safe_fraction_ppm(returns_ppm: tuple[int, ...]) -> int:
    maximum = PPM
    for episode_return in returns_ppm:
        if episode_return < 0:
            maximum = min(
                maximum,
                (_WEALTH_SCALE - 1) // (-episode_return),
            )
    return max(0, maximum)


def _derivative_sign(counts: tuple[tuple[int, int], ...], fraction_ppm: int) -> int:
    approximate = 0
    for episode_return, count in counts:
        denominator = _WEALTH_SCALE + fraction_ppm * episode_return
        if denominator <= 0:
            return -1
        approximate += round_half_even_divide(
            count * episode_return * _DERIVATIVE_SCALE,
            denominator,
        )
    error_bound = len(counts)
    if abs(approximate) > error_bound:
        return 1 if approximate > 0 else -1
    exact = sum(
        (
            Fraction(count * episode_return, _WEALTH_SCALE + fraction_ppm * episode_return)
            for episode_return, count in counts
        ),
        Fraction(0, 1),
    )
    return (exact > 0) - (exact < 0)


def _log_growth(counts: tuple[tuple[int, int], ...], fraction_ppm: int) -> Decimal:
    context = Context(
        prec=80,
        rounding=ROUND_HALF_EVEN,
        Emin=-999_999,
        Emax=999_999,
        capitals=1,
        clamp=0,
    )
    for signal in context.traps:
        context.traps[signal] = False
    try:
        with localcontext(context):
            scale = Decimal(_WEALTH_SCALE)
            total = Decimal(0)
            for episode_return, count in counts:
                wealth = _WEALTH_SCALE + fraction_ppm * episode_return
                if wealth <= 0:
                    raise EvidenceValidationError(
                        "EVIDENCE_KELLY_WEALTH_NONPOSITIVE"
                    )
                total += Decimal(count) * (Decimal(wealth) / scale).ln()
            if not total.is_finite():
                raise EvidenceValidationError("EVIDENCE_DECIMAL_EVALUATION_FAILED")
            return +total
    except EvidenceValidationError:
        raise
    except DecimalException as exc:
        raise EvidenceValidationError("EVIDENCE_DECIMAL_EVALUATION_FAILED") from exc


def _estimate_full_kelly_counts(
    counts: tuple[tuple[int, int], ...]
) -> int:
    if not counts or any(count <= 0 for _, count in counts):
        raise EvidenceValidationError("EVIDENCE_RETURN_DISTRIBUTION_INVALID")
    maximum = _maximum_safe_fraction_ppm(
        tuple(episode_return for episode_return, _ in counts)
    )
    if maximum == 0 or _derivative_sign(counts, 0) <= 0:
        return 0
    if _derivative_sign(counts, maximum) >= 0:
        return maximum

    low = 0
    high = maximum
    while low + 1 < high:
        middle = (low + high) // 2
        if _derivative_sign(counts, middle) >= 0:
            low = middle
        else:
            high = middle
    low_growth = _log_growth(counts, low)
    high_growth = _log_growth(counts, high)
    return high if high_growth > low_growth else low


def estimate_full_kelly_ppm(returns_ppm: tuple[int, ...] | list[int]) -> int:
    """Maximize empirical log growth on the safe integer-ppm domain."""

    if type(returns_ppm) not in {tuple, list} or not returns_ppm:
        raise EvidenceValidationError("EVIDENCE_RETURN_DISTRIBUTION_INVALID")
    normalized: list[int] = []
    for value in returns_ppm:
        normalized.append(_integer(value, minimum=-100 * PPM, maximum=100 * PPM))
    counts = tuple(
        sorted(
            (episode_return, count)
            for episode_return, count in Counter(normalized).items()
        )
    )
    return _estimate_full_kelly_counts(counts)


@dataclass(frozen=True, slots=True)
class BootstrapDistributionV2:
    means_ppm: tuple[int, ...]
    full_kelly_ppm: tuple[int, ...]
    lower_bound_ppm: int
    kelly_5pct_ppm: int


def _block_start(seed: bytes, replicate_index: int, block_index: int, sample_size: int) -> int:
    digest = sha256(
        seed
        + replicate_index.to_bytes(8, "big", signed=False)
        + block_index.to_bytes(8, "big", signed=False)
    ).digest()
    return int.from_bytes(digest, "big", signed=False) % sample_size


def circular_moving_block_bootstrap(
    returns_ppm: tuple[int, ...] | list[int],
    *,
    seed_sha256: str,
    replicates: int = 10_000,
    block_length: int = 20,
    lower_bound_rank: int = 500,
    kelly_quantile_rank: int = 500,
) -> BootstrapDistributionV2:
    """Run deterministic circular moving-block bootstrap without RNG state."""

    if type(returns_ppm) not in {tuple, list} or not returns_ppm:
        raise EvidenceValidationError("EVIDENCE_RETURN_DISTRIBUTION_INVALID")
    returns = tuple(_integer(value, minimum=-100 * PPM, maximum=100 * PPM) for value in returns_ppm)
    seed = bytes.fromhex(_hash(seed_sha256))
    replicate_count = _integer(replicates, minimum=1, maximum=100_000)
    block = _integer(block_length, minimum=1, maximum=10_000)
    mean_rank = _integer(lower_bound_rank, minimum=1, maximum=replicate_count)
    kelly_rank = _integer(kelly_quantile_rank, minimum=1, maximum=replicate_count)
    sample_size = len(returns)
    if sample_size * replicate_count > _MAX_BOOTSTRAP_WORK_ITEMS:
        raise EvidenceValidationError("EVIDENCE_BOOTSTRAP_WORK_BOUND_EXCEEDED")
    blocks_needed = (sample_size + block - 1) // block
    means: list[int] = []
    kelly_values: list[int] = []
    kelly_cache: dict[tuple[tuple[int, int], ...], int] = {}
    for replicate_index in range(replicate_count):
        sample: list[int] = []
        for block_index in range(blocks_needed):
            start = _block_start(seed, replicate_index, block_index, sample_size)
            remaining = sample_size - len(sample)
            for offset in range(min(block, remaining)):
                sample.append(returns[(start + offset) % sample_size])
        means.append(round_half_even_divide(sum(sample), sample_size))
        histogram = tuple(sorted(Counter(sample).items()))
        full_kelly = kelly_cache.get(histogram)
        if full_kelly is None:
            full_kelly = _estimate_full_kelly_counts(histogram)
            kelly_cache[histogram] = full_kelly
        kelly_values.append(full_kelly)
    sorted_means = sorted(means)
    sorted_kelly = sorted(kelly_values)
    return BootstrapDistributionV2(
        means_ppm=tuple(means),
        full_kelly_ppm=tuple(kelly_values),
        lower_bound_ppm=sorted_means[mean_rank - 1],
        kelly_5pct_ppm=sorted_kelly[kelly_rank - 1],
    )


@dataclass(frozen=True, slots=True)
class StructureEvidenceReceiptV2:
    carrier_id: str
    evidence_receipt_id: str
    episode_cohort_id: str
    distribution_id: str
    input_sha256: str
    episode_set_sha256: str
    distribution_sha256: str
    evidence_sha256: str
    expected_net_return_on_entry_debit_ppm: int
    expected_net_return_lower_bound_ppm: int
    full_kelly_ppm: int
    bootstrap_kelly_5pct_ppm: int
    robust_full_kelly_ppm: int
    half_kelly_ppm: int
    oos_episode_count: int
    fold_count: int
    coverage_ppm: int
    entry_policy_sha256: str
    fee_schedule_sha256: str
    exit_policy_sha256: str
    evidence_max_estimation_outcome_utc_ns: int
    development_used_in_estimate: bool
    sealed_oos_used_in_estimate: bool
    _canonical_bytes: bytes = field(repr=False)

    @property
    def lower_bound_ppm(self) -> int:
        return self.expected_net_return_lower_bound_ppm

    @property
    def canonical_bytes(self) -> bytes:
        return bytes(self._canonical_bytes)

    def _sealed_receipt_document(self) -> dict[str, object]:
        value = json.loads(self._canonical_bytes.decode("utf-8"))
        if type(value) is not dict:  # pragma: no cover - sealed invariant
            raise AssertionError("evidence receipt is not an object")
        if _canonical_bytes(value) != self._canonical_bytes:
            raise EvidenceValidationError("EVIDENCE_RECEIPT_TYPED_INTEGRITY_MISMATCH")
        unsigned = dict(value)
        sealed_hash = unsigned.pop("evidence_sha256", None)
        if type(sealed_hash) is not str or _canonical_sha256(unsigned) != sealed_hash:
            raise EvidenceValidationError("EVIDENCE_RECEIPT_TYPED_INTEGRITY_MISMATCH")
        coverage = value["coverage"]
        split_counts = value["split_counts"]
        folds = value["walk_forward_fold_manifest"]
        distribution = value["distribution_binding"]
        if not (
            type(coverage) is dict
            and type(split_counts) is dict
            and type(folds) is list
            and type(distribution) is dict
            and self.carrier_id == value["carrier_id"]
            and self.evidence_receipt_id == value["evidence_receipt_id"]
            and self.episode_cohort_id == value["episode_cohort_id"]
            and self.distribution_id == distribution["distribution_id"]
            and self.input_sha256 == value["input_sha256"]
            and self.episode_set_sha256 == distribution["episode_set_sha256"]
            and self.distribution_sha256 == distribution["distribution_sha256"]
            and self.evidence_sha256 == sealed_hash
            and self.expected_net_return_on_entry_debit_ppm
            == value["expected_net_return_on_entry_debit_ppm"]
            and self.expected_net_return_lower_bound_ppm
            == value["expected_net_return_lower_bound_ppm"]
            and self.full_kelly_ppm == value["full_kelly_ppm"]
            and self.bootstrap_kelly_5pct_ppm
            == value["bootstrap_kelly_5pct_ppm"]
            and self.robust_full_kelly_ppm == value["robust_full_kelly_ppm"]
            and self.half_kelly_ppm == value["half_kelly_ppm"]
            and self.oos_episode_count == split_counts["walk_forward_oos_count"]
            and self.fold_count == len(folds)
            and self.coverage_ppm == coverage["coverage_ppm"]
            and self.entry_policy_sha256 == value["entry_policy_sha256"]
            and self.fee_schedule_sha256 == value["fee_schedule_sha256"]
            and self.exit_policy_sha256 == value["exit_policy_sha256"]
            and self.evidence_max_estimation_outcome_utc_ns
            == value["evidence_max_estimation_outcome_utc_ns"]
            and self.development_used_in_estimate
            == value["development_used_in_estimate"]
            and self.sealed_oos_used_in_estimate
            == value["sealed_oos_used_in_estimate"]
        ):
            raise EvidenceValidationError("EVIDENCE_RECEIPT_TYPED_INTEGRITY_MISMATCH")
        return value

    def as_dict(self) -> dict[str, object]:
        return self._sealed_receipt_document()

    def as_entry_summary(self) -> dict[str, object]:
        """Return the closed summary consumed by ``EntryDecisionInputF0``.

        The legacy-looking expected-return field intentionally carries the
        preregistered lower bound; the arithmetic mean remains separately
        visible.  This prevents the Entry f preference stage from silently
        substituting a point estimate for the conservative comparison input.
        """

        receipt = self._sealed_receipt_document()
        coverage = receipt["coverage"]
        split_counts = receipt["split_counts"]
        fold_manifest = receipt["walk_forward_fold_manifest"]
        distribution = receipt["distribution_binding"]
        assert type(coverage) is dict
        assert type(split_counts) is dict
        assert type(fold_manifest) is list
        assert type(distribution) is dict
        return {
            "schema_version": "HISTORICAL_STRUCTURE_EVIDENCE_V2",
            "classification": "SYNTHETIC_ONLY",
            "carrier_id": receipt["carrier_id"],
            "evidence_receipt_id": receipt["evidence_receipt_id"],
            "episode_cohort_id": receipt["episode_cohort_id"],
            "distribution_id": distribution["distribution_id"],
            "expected_net_return_on_entry_debit_ppm": (
                receipt["expected_net_return_lower_bound_ppm"]
            ),
            "arithmetic_mean_ppm": (
                receipt["expected_net_return_on_entry_debit_ppm"]
            ),
            "full_kelly_ppm": receipt["full_kelly_ppm"],
            "bootstrap_kelly_5pct_ppm": receipt["bootstrap_kelly_5pct_ppm"],
            "robust_full_kelly_ppm": receipt["robust_full_kelly_ppm"],
            "half_kelly_ppm": receipt["half_kelly_ppm"],
            "oos_episode_count": split_counts["walk_forward_oos_count"],
            "fold_count": len(fold_manifest),
            "coverage_ppm": coverage["coverage_ppm"],
            "entry_policy_sha256": receipt["entry_policy_sha256"],
            "fee_schedule_sha256": receipt["fee_schedule_sha256"],
            "exit_policy_sha256": receipt["exit_policy_sha256"],
            "evidence_max_estimation_outcome_utc_ns": (
                receipt["evidence_max_estimation_outcome_utc_ns"]
            ),
            "input_sha256": receipt["input_sha256"],
            "distribution_sha256": distribution["distribution_sha256"],
            "evidence_sha256": receipt["evidence_sha256"],
        }


@dataclass(frozen=True, slots=True)
class HistoricalStructureEvidenceBundleV2:
    classification: str
    bundle_sha256: str
    canonical_bytes: bytes
    carriers: tuple[StructureEvidenceReceiptV2, ...]

    @property
    def schema_version(self) -> str:
        return "HISTORICAL_STRUCTURE_EVIDENCE_BUNDLE_V2"

    def as_dict(self) -> dict[str, object]:
        value = json.loads(self.canonical_bytes.decode("utf-8"))
        if type(value) is not dict:  # pragma: no cover - sealed invariant
            raise AssertionError("evidence bundle is not an object")
        return value

    def as_entry_evidence(self) -> dict[str, object]:
        """Return fixed LC0/BCS0 summaries ready for the Entry input contract."""

        return {
            receipt.carrier_id: receipt.as_entry_summary()
            for receipt in self.carriers
        }


def _normalize_coverage(value: object, episode_count: int) -> dict[str, object]:
    raw = _closed(value, _COVERAGE_KEYS, "EVIDENCE_COVERAGE_SCHEMA_INVALID")
    eligible = _integer(raw["eligible_episode_count"], minimum=1)
    included = _integer(raw["included_episode_count"], minimum=1)
    excluded = _integer(raw["excluded_episode_count"], minimum=0)
    coverage_ppm = _integer(raw["coverage_ppm"], minimum=0, maximum=PPM)
    exclusion_values = _list(raw["exclusion_reasons"], "EVIDENCE_EXCLUSIONS_INVALID")
    exclusions: list[dict[str, object]] = []
    for value_item in exclusion_values:
        item = _closed(value_item, _EXCLUSION_KEYS, "EVIDENCE_EXCLUSION_SCHEMA_INVALID")
        exclusions.append(
            {
                "reason_code": _reason(item["reason_code"]),
                "count": _integer(item["count"], minimum=1),
            }
        )
    exclusions.sort(key=lambda item: str(item["reason_code"]))
    reason_codes = [str(item["reason_code"]) for item in exclusions]
    if len(reason_codes) != len(set(reason_codes)):
        raise EvidenceValidationError("EVIDENCE_EXCLUSION_DUPLICATE")
    if (
        eligible != included + excluded
        or included != episode_count
        or sum(int(item["count"]) for item in exclusions) != excluded
        or coverage_ppm != included * PPM // eligible
    ):
        raise EvidenceValidationError("EVIDENCE_COVERAGE_INVALID")
    if coverage_ppm < 950_000:
        raise EvidenceValidationError("EVIDENCE_COVERAGE_INSUFFICIENT")
    return {
        "eligible_episode_count": eligible,
        "included_episode_count": included,
        "excluded_episode_count": excluded,
        "coverage_ppm": coverage_ppm,
        "exclusion_reasons": exclusions,
    }


def _normalize_estimator(value: object) -> dict[str, object]:
    raw = _closed(value, _ESTIMATOR_KEYS, "EVIDENCE_ESTIMATOR_SCHEMA_INVALID")
    normalized = {
        "estimator_id": _identifier(raw["estimator_id"]),
        "uncertainty_method_id": _identifier(raw["uncertainty_method_id"]),
        "bootstrap_replicates": _integer(raw["bootstrap_replicates"], minimum=1),
        "block_length": _integer(raw["block_length"], minimum=1),
        "lower_bound_rank": _integer(raw["lower_bound_rank"], minimum=1),
        "kelly_quantile_rank": _integer(raw["kelly_quantile_rank"], minimum=1),
        "rounding_mode": _identifier(raw["rounding_mode"]),
        "hash_start_method": _identifier(raw["hash_start_method"]),
    }
    required = {
        "estimator_id": "CIRCULAR_MBB_KELLY_V2",
        "uncertainty_method_id": "CIRCULAR_MBB_ONE_SIDED_5PCT_V1",
        "bootstrap_replicates": 10_000,
        "block_length": 20,
        "lower_bound_rank": 500,
        "kelly_quantile_rank": 500,
        "rounding_mode": "SIGNED_ROUND_HALF_EVEN",
        "hash_start_method": "SHA256_COUNTER_MOD_N_V1",
    }
    if normalized != required:
        raise EvidenceValidationError("EVIDENCE_ESTIMATOR_POLICY_INVALID")
    return normalized


def _normalize_fold_manifest(value: object) -> list[dict[str, object]]:
    raw_folds = _list(value, "EVIDENCE_FOLD_MANIFEST_INVALID")
    if not 3 <= len(raw_folds) <= 32:
        raise EvidenceValidationError("EVIDENCE_FOLDS_INSUFFICIENT")
    folds: list[dict[str, object]] = []
    for value_item in raw_folds:
        item = _closed(
            value_item, _FOLD_KEYS, "EVIDENCE_FOLD_MANIFEST_SCHEMA_INVALID"
        )
        train_end = _integer(item["train_end_utc_ns"], minimum=0)
        test_start = _integer(item["test_start_utc_ns"], minimum=0)
        test_end = _integer(item["test_end_utc_ns"], minimum=0)
        if not train_end < test_start < test_end:
            raise EvidenceValidationError("EVIDENCE_FOLD_BOUNDARY_INVALID")
        folds.append(
            {
                "fold_id": _identifier(
                    item["fold_id"], "EVIDENCE_FOLD_ID_INVALID"
                ),
                "train_end_utc_ns": train_end,
                "test_start_utc_ns": test_start,
                "test_end_utc_ns": test_end,
            }
        )
    folds.sort(key=lambda item: (int(item["test_start_utc_ns"]), str(item["fold_id"])))
    fold_ids = [str(item["fold_id"]) for item in folds]
    if len(fold_ids) != len(set(fold_ids)):
        raise EvidenceValidationError("EVIDENCE_FOLD_DUPLICATE")
    for previous, current in zip(folds, folds[1:], strict=False):
        if (
            int(previous["test_end_utc_ns"])
            > int(current["train_end_utc_ns"])
            or int(previous["train_end_utc_ns"])
            >= int(current["train_end_utc_ns"])
            or int(current["train_end_utc_ns"])
            >= int(current["test_start_utc_ns"])
        ):
            raise EvidenceValidationError("EVIDENCE_FOLD_SEQUENCE_INVALID")
    return folds


def _validate_episode_fold_assignment(
    episodes: list[dict[str, object]], folds: list[dict[str, object]]
) -> None:
    fold_by_id = {str(item["fold_id"]): item for item in folds}
    used_fold_ids: set[str] = set()
    development = [item for item in episodes if item["split"] == "DEVELOPMENT"]
    walk_forward = [item for item in episodes if item["split"] == "WALK_FORWARD_OOS"]
    sealed = [item for item in episodes if item["split"] == "SEALED_OOS"]
    if not development or not walk_forward or not sealed:
        raise EvidenceValidationError("EVIDENCE_SPLITS_INCOMPLETE")
    first_test_start = int(folds[0]["test_start_utc_ns"])
    last_test_end = int(folds[-1]["test_end_utc_ns"])
    if max(int(item["exit_utc_ns"]) for item in development) > first_test_start:
        raise EvidenceValidationError("EVIDENCE_DEVELOPMENT_LEAKAGE")
    if min(int(item["entry_utc_ns"]) for item in sealed) < last_test_end:
        raise EvidenceValidationError("EVIDENCE_SEALED_OOS_LEAKAGE")
    for episode in walk_forward:
        fold_id = str(episode["fold_id"])
        fold = fold_by_id.get(fold_id)
        if fold is None:
            raise EvidenceValidationError("EVIDENCE_FOLD_ASSIGNMENT_INVALID")
        entry_ns = int(episode["entry_utc_ns"])
        if not (
            int(fold["test_start_utc_ns"])
            <= entry_ns
            < int(fold["test_end_utc_ns"])
            and int(episode["exit_utc_ns"])
            <= int(fold["test_end_utc_ns"])
        ):
            raise EvidenceValidationError("EVIDENCE_FOLD_ASSIGNMENT_INVALID")
        used_fold_ids.add(fold_id)
    if used_fold_ids != set(fold_by_id):
        raise EvidenceValidationError("EVIDENCE_FOLD_EMPTY")


def _normalize_carrier(value: object) -> tuple[dict[str, object], str]:
    raw = _closed(value, _CARRIER_KEYS, "EVIDENCE_CARRIER_SCHEMA_INVALID")
    if raw["schema_version"] != "STRUCTURE_EVIDENCE_INPUT_V2":
        raise EvidenceValidationError("EVIDENCE_CARRIER_VERSION_UNSUPPORTED")
    carrier_id = raw["carrier_id"]
    if carrier_id not in {"LC0", "BCS0"}:
        raise EvidenceValidationError("EVIDENCE_CARRIER_INVALID")
    episode_values = _list(raw["episodes"], "EVIDENCE_EPISODES_INVALID")
    if not episode_values:
        raise EvidenceValidationError("EVIDENCE_OPAQUE_ONLY")
    if len(episode_values) > 10_000:
        raise EvidenceValidationError("EVIDENCE_EPISODE_LIMIT_EXCEEDED")
    episodes = [_normalize_episode(item, str(carrier_id)) for item in episode_values]
    episodes.sort(key=lambda item: (int(item["entry_utc_ns"]), str(item["episode_id"])))
    episode_ids = [str(item["episode_id"]) for item in episodes]
    if len(episode_ids) != len(set(episode_ids)):
        raise EvidenceValidationError("EVIDENCE_EPISODE_DUPLICATE")
    episode_input_hashes = [str(item["input_sha256"]) for item in episodes]
    if len(episode_input_hashes) != len(set(episode_input_hashes)):
        raise EvidenceValidationError("EVIDENCE_EPISODE_SOURCE_DUPLICATE")
    episode_intervals = [
        (int(item["entry_utc_ns"]), int(item["exit_utc_ns"]))
        for item in episodes
    ]
    if len(episode_intervals) != len(set(episode_intervals)):
        raise EvidenceValidationError("EVIDENCE_EPISODE_INTERVAL_DUPLICATE")
    fold_manifest = _normalize_fold_manifest(raw["fold_manifest"])
    _validate_episode_fold_assignment(episodes, fold_manifest)
    coverage = _normalize_coverage(raw["coverage"], len(episodes))
    estimator = _normalize_estimator(raw["estimator"])
    normalized = {
        "schema_version": "STRUCTURE_EVIDENCE_INPUT_V2",
        "carrier_id": str(carrier_id),
        "evidence_receipt_id": _identifier(raw["evidence_receipt_id"]),
        "episode_cohort_id": _identifier(raw["episode_cohort_id"]),
        "distribution_id": _identifier(raw["distribution_id"]),
        "entry_clock_id": _identifier(raw["entry_clock_id"]),
        "exit_clock_id": _identifier(raw["exit_clock_id"]),
        "cost_model_version": _identifier(raw["cost_model_version"]),
        "rule_version": _identifier(raw["rule_version"]),
        "entry_policy_sha256": _hash(raw["entry_policy_sha256"]),
        "fee_schedule_sha256": _hash(raw["fee_schedule_sha256"]),
        "exit_policy_sha256": _hash(raw["exit_policy_sha256"]),
        "coverage": coverage,
        "fold_manifest": fold_manifest,
        "episodes": episodes,
        "estimator": estimator,
    }
    return normalized, str(carrier_id)


def _derive_receipt(normalized: dict[str, object], carrier_id: str) -> StructureEvidenceReceiptV2:
    raw_episodes = normalized["episodes"]
    if type(raw_episodes) is not list:  # pragma: no cover - normalized invariant
        raise AssertionError("episodes are not a list")
    episodes = [derive_episode_after_cost(item, carrier_id) for item in raw_episodes]
    walk_forward = [item for item in episodes if item["split"] == "WALK_FORWARD_OOS"]
    if (
        not any(item["split"] == "DEVELOPMENT" for item in episodes)
        or not any(item["split"] == "SEALED_OOS" for item in episodes)
    ):
        raise EvidenceValidationError("EVIDENCE_SPLITS_INCOMPLETE")
    if len(walk_forward) < 100:
        raise EvidenceValidationError("EVIDENCE_OOS_EPISODES_INSUFFICIENT")
    if len(walk_forward) > _MAX_WALK_FORWARD_EPISODES:
        raise EvidenceValidationError("EVIDENCE_BOOTSTRAP_WORK_BOUND_EXCEEDED")
    fold_manifest = normalized["fold_manifest"]
    if type(fold_manifest) is not list:  # pragma: no cover - normalized invariant
        raise AssertionError("fold manifest is not a list")
    returns = tuple(
        int(item["after_cost_return_on_entry_debit_ppm"])
        for item in walk_forward
    )
    distribution_content = {
        "schema_version": "STRUCTURE_RETURN_DISTRIBUTION_V2",
        "carrier_id": carrier_id,
        "distribution_id": normalized["distribution_id"],
        "return_unit": "ON_ENTRY_DEBIT_PPM",
        "walk_forward_oos_returns": [
            {
                "episode_id": item["episode_id"],
                "fold_id": item["fold_id"],
                "after_cost_return_on_entry_debit_ppm": item[
                    "after_cost_return_on_entry_debit_ppm"
                ],
            }
            for item in walk_forward
        ],
    }
    distribution_sha256 = _canonical_sha256(distribution_content)
    estimator = normalized["estimator"]
    if type(estimator) is not dict:  # pragma: no cover - normalized invariant
        raise AssertionError("estimator is not an object")
    bootstrap_seed_sha256 = _canonical_sha256(
        {
            "carrier_id": carrier_id,
            "distribution_sha256": distribution_sha256,
            "estimator": estimator,
        }
    )
    bootstrap = circular_moving_block_bootstrap(
        returns,
        seed_sha256=bootstrap_seed_sha256,
        replicates=int(estimator["bootstrap_replicates"]),
        block_length=int(estimator["block_length"]),
        lower_bound_rank=int(estimator["lower_bound_rank"]),
        kelly_quantile_rank=int(estimator["kelly_quantile_rank"]),
    )
    arithmetic_mean = round_half_even_divide(sum(returns), len(returns))
    full_kelly = estimate_full_kelly_ppm(returns)
    if bootstrap.lower_bound_ppm <= 0:
        robust_full_kelly = 0
    else:
        robust_full_kelly = min(full_kelly, bootstrap.kelly_5pct_ppm)
    half_kelly = robust_full_kelly // 2
    coverage = normalized["coverage"]
    if type(coverage) is not dict:  # pragma: no cover - normalized invariant
        raise AssertionError("coverage is not an object")
    split_counts = {
        "development_count": sum(item["split"] == "DEVELOPMENT" for item in episodes),
        "walk_forward_oos_count": len(walk_forward),
        "sealed_oos_count": sum(item["split"] == "SEALED_OOS" for item in episodes),
    }
    evidence_max_estimation_outcome_utc_ns = max(
        int(item["exit_utc_ns"]) for item in walk_forward
    )
    input_sha256 = _canonical_sha256(normalized)
    episode_set_sha256 = _canonical_sha256(episodes)
    estimator_sha256 = _canonical_sha256(estimator)
    receipt_without_hash: dict[str, object] = {
        "schema_version": "STRUCTURE_EVIDENCE_RECEIPT_V2",
        "authority_status": "RESEARCH_ONLY",
        "actionable": False,
        "carrier_id": carrier_id,
        "evidence_receipt_id": normalized["evidence_receipt_id"],
        "episode_cohort_id": normalized["episode_cohort_id"],
        "entry_clock_id": normalized["entry_clock_id"],
        "exit_clock_id": normalized["exit_clock_id"],
        "cost_model_version": normalized["cost_model_version"],
        "rule_version": normalized["rule_version"],
        "entry_policy_sha256": normalized["entry_policy_sha256"],
        "fee_schedule_sha256": normalized["fee_schedule_sha256"],
        "exit_policy_sha256": normalized["exit_policy_sha256"],
        "coverage": deepcopy(coverage),
        "walk_forward_fold_manifest": deepcopy(fold_manifest),
        "split_counts": split_counts,
        "episodes": episodes,
        "development_used_in_estimate": False,
        "sealed_oos_used_in_estimate": False,
        "evidence_max_estimation_outcome_utc_ns": (
            evidence_max_estimation_outcome_utc_ns
        ),
        "distribution_binding": {
            "distribution_id": normalized["distribution_id"],
            "return_unit": "ON_ENTRY_DEBIT_PPM",
            "episode_set_sha256": episode_set_sha256,
            "distribution_sha256": distribution_sha256,
        },
        "expected_net_return_on_entry_debit_ppm": arithmetic_mean,
        "expected_net_return_lower_bound_ppm": bootstrap.lower_bound_ppm,
        "uncertainty_method_id": estimator["uncertainty_method_id"],
        "bootstrap_replicates": estimator["bootstrap_replicates"],
        "bootstrap_block_length": estimator["block_length"],
        "lower_bound_rank": estimator["lower_bound_rank"],
        "full_kelly_ppm": full_kelly,
        "bootstrap_kelly_5pct_ppm": bootstrap.kelly_5pct_ppm,
        "robust_full_kelly_ppm": robust_full_kelly,
        "half_kelly_ppm": half_kelly,
        "estimator_id": estimator["estimator_id"],
        "estimator_sha256": estimator_sha256,
        "bootstrap_seed_sha256": bootstrap_seed_sha256,
        "input_sha256": input_sha256,
    }
    evidence_sha256 = _canonical_sha256(receipt_without_hash)
    receipt_document = {**receipt_without_hash, "evidence_sha256": evidence_sha256}
    encoded = _canonical_bytes(receipt_document)
    return StructureEvidenceReceiptV2(
        carrier_id=carrier_id,
        evidence_receipt_id=str(normalized["evidence_receipt_id"]),
        episode_cohort_id=str(normalized["episode_cohort_id"]),
        distribution_id=str(normalized["distribution_id"]),
        input_sha256=input_sha256,
        episode_set_sha256=episode_set_sha256,
        distribution_sha256=distribution_sha256,
        evidence_sha256=evidence_sha256,
        expected_net_return_on_entry_debit_ppm=arithmetic_mean,
        expected_net_return_lower_bound_ppm=bootstrap.lower_bound_ppm,
        full_kelly_ppm=full_kelly,
        bootstrap_kelly_5pct_ppm=bootstrap.kelly_5pct_ppm,
        robust_full_kelly_ppm=robust_full_kelly,
        half_kelly_ppm=half_kelly,
        oos_episode_count=len(walk_forward),
        fold_count=len(fold_manifest),
        coverage_ppm=int(coverage["coverage_ppm"]),
        entry_policy_sha256=str(normalized["entry_policy_sha256"]),
        fee_schedule_sha256=str(normalized["fee_schedule_sha256"]),
        exit_policy_sha256=str(normalized["exit_policy_sha256"]),
        evidence_max_estimation_outcome_utc_ns=(
            evidence_max_estimation_outcome_utc_ns
        ),
        development_used_in_estimate=False,
        sealed_oos_used_in_estimate=False,
        _canonical_bytes=encoded,
    )


def _precheck_independence(carriers: list[object]) -> None:
    if len(carriers) != 2 or any(type(item) is not dict for item in carriers):
        return
    first, second = carriers
    if {first.get("carrier_id"), second.get("carrier_id")} != {"LC0", "BCS0"}:
        return
    for key in (
        "evidence_receipt_id",
        "episode_cohort_id",
        "distribution_id",
        "exit_clock_id",
        "exit_policy_sha256",
    ):
        if first.get(key) is not None and first.get(key) == second.get(key):
            raise EvidenceValidationError("EVIDENCE_NOT_INDEPENDENT", key)
    first_episodes = first.get("episodes")
    second_episodes = second.get("episodes")
    if type(first_episodes) is list and type(second_episodes) is list:
        first_ids = {
            item.get("episode_id") for item in first_episodes if type(item) is dict
        }
        second_ids = {
            item.get("episode_id") for item in second_episodes if type(item) is dict
        }
        if first_ids & second_ids:
            raise EvidenceValidationError("EVIDENCE_NOT_INDEPENDENT", "episode_id")


def validate_structure_evidence_v2(document: object) -> HistoricalStructureEvidenceBundleV2:
    """Validate and estimate independent LC0/BCS0 Historical Evidence V2."""

    _canonical_bytes(document)
    raw = _closed(document, _BUNDLE_KEYS, "EVIDENCE_BUNDLE_SCHEMA_INVALID")
    if raw["schema_version"] != "HISTORICAL_STRUCTURE_EVIDENCE_BUNDLE_V2":
        raise EvidenceValidationError("EVIDENCE_BUNDLE_VERSION_UNSUPPORTED")
    if raw["classification"] != "SYNTHETIC_ONLY":
        raise EvidenceValidationError("EVIDENCE_CLASSIFICATION_UNSUPPORTED")
    if (
        raw["scope"] != "GLD_ENTRY_STRUCTURE_EVIDENCE_ONLY"
        or raw["underlying"] != "GLD"
    ):
        raise EvidenceValidationError("EVIDENCE_SCOPE_INVALID")
    carrier_values = _list(raw["carriers"], "EVIDENCE_CARRIERS_INVALID")
    _precheck_independence(carrier_values)
    normalized_pairs = [_normalize_carrier(item) for item in carrier_values]
    normalized_pairs.sort(key=lambda pair: pair[1])
    if tuple(pair[1] for pair in normalized_pairs) != ("BCS0", "LC0"):
        raise EvidenceValidationError("EVIDENCE_CARRIERS_INVALID")
    episode_source_sets = []
    for normalized, _ in normalized_pairs:
        episodes = normalized["episodes"]
        if type(episodes) is not list:  # pragma: no cover - normalized invariant
            raise AssertionError("episodes are not a list")
        episode_source_sets.append(
            {str(item["input_sha256"]) for item in episodes}
        )
    if episode_source_sets[0] & episode_source_sets[1]:
        raise EvidenceValidationError(
            "EVIDENCE_NOT_INDEPENDENT", "episode_input_sha256"
        )
    receipts = tuple(
        _derive_receipt(normalized, carrier_id)
        for normalized, carrier_id in normalized_pairs
    )
    for attribute in (
        "evidence_receipt_id",
        "episode_cohort_id",
        "distribution_id",
        "input_sha256",
        "episode_set_sha256",
        "distribution_sha256",
        "evidence_sha256",
    ):
        values = [getattr(receipt, attribute) for receipt in receipts]
        if len(values) != len(set(values)):
            raise EvidenceValidationError("EVIDENCE_NOT_INDEPENDENT", attribute)
    normalized_bundle: dict[str, object] = {
        "schema_version": "HISTORICAL_STRUCTURE_EVIDENCE_BUNDLE_V2",
        "classification": "SYNTHETIC_ONLY",
        "scope": "GLD_ENTRY_STRUCTURE_EVIDENCE_ONLY",
        "underlying": "GLD",
        "authority_status": "RESEARCH_ONLY",
        "actionable": False,
        "policy_winner": None,
        "active_entry_policy_available": False,
        "carriers": [receipt.as_dict() for receipt in receipts],
        "content_hashes": {
            receipt.carrier_id: receipt.evidence_sha256 for receipt in receipts
        },
    }
    encoded = _canonical_bytes(normalized_bundle)
    return HistoricalStructureEvidenceBundleV2(
        classification="SYNTHETIC_ONLY",
        bundle_sha256=sha256(encoded).hexdigest(),
        canonical_bytes=encoded,
        carriers=receipts,
    )


def evaluate_policy_research_f0(document: object) -> HistoricalStructureEvidenceBundleV2:
    """Explicit research-only entry point; never produces an Active theta."""

    return validate_structure_evidence_v2(document)


__all__ = [
    "BootstrapDistributionV2",
    "EvidenceValidationError",
    "HistoricalStructureEvidenceBundleV2",
    "StructureEvidenceReceiptV2",
    "circular_moving_block_bootstrap",
    "derive_episode_after_cost",
    "estimate_full_kelly_ppm",
    "evaluate_policy_research_f0",
    "round_half_even_divide",
    "validate_structure_evidence_v2",
]
