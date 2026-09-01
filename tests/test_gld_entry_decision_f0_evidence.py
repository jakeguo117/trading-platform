from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Inexact, ROUND_DOWN, getcontext, setcontext
from hashlib import sha256
import unittest

from gld_entry_decision_f0.evidence import (
    EvidenceValidationError,
    circular_moving_block_bootstrap,
    derive_episode_after_cost,
    estimate_full_kelly_ppm,
    round_half_even_divide,
    validate_structure_evidence_v2,
)


PPM = 1_000_000
BASE_NS = 1_800_000_000_000_000_000
DAY_NS = 86_400_000_000_000


def _sha(label: str) -> str:
    return sha256(label.encode("ascii")).hexdigest()


def _episode(carrier_id: str, index: int, split: str, fold_id: str) -> dict[str, object]:
    common: dict[str, object] = {
        "episode_id": f"{carrier_id.lower()}-{split.lower()}-{index:03d}",
        "fold_id": fold_id,
        "split": split,
        "entry_utc_ns": BASE_NS + index * DAY_NS,
        "exit_utc_ns": BASE_NS + (index + 1) * DAY_NS,
        "multiplier": 100,
        "opening_fees_nano_usd": 100,
        "closing_fees_nano_usd": 100,
        "input_sha256": _sha(f"{carrier_id}-episode-{index}-{split}"),
    }
    if carrier_id == "LC0":
        common.update(
            {
                "long_entry_ask_nano_usd": 1_000,
                "long_exit_bid_nano_usd": 1_100 + index % 7,
                "short_entry_bid_nano_usd": None,
                "short_exit_ask_nano_usd": None,
            }
        )
    else:
        common.update(
            {
                "long_entry_ask_nano_usd": 1_000,
                "long_exit_bid_nano_usd": 1_200 + index % 7,
                "short_entry_bid_nano_usd": 400,
                "short_exit_ask_nano_usd": 500,
            }
        )
    return common


def _carrier(
    carrier_id: str,
    *,
    walk_forward_count: int = 100,
    fold_count: int = 3,
) -> dict[str, object]:
    episodes = [_episode(carrier_id, 0, "DEVELOPMENT", "DEV")]
    fold_manifest: list[dict[str, object]] = []
    cursor = 1
    base_length, remainder = divmod(walk_forward_count, fold_count)
    for fold_number in range(1, fold_count + 1):
        fold_length = base_length + (1 if fold_number <= remainder else 0)
        fold_start = cursor
        fold_end = cursor + fold_length
        fold_id = f"WF-{fold_number}"
        fold_manifest.append(
            {
                "fold_id": fold_id,
                "train_end_utc_ns": BASE_NS + fold_start * DAY_NS - 1,
                "test_start_utc_ns": BASE_NS + fold_start * DAY_NS,
                "test_end_utc_ns": BASE_NS + fold_end * DAY_NS,
            }
        )
        for index in range(fold_start, fold_end):
            episodes.append(
                _episode(carrier_id, index, "WALK_FORWARD_OOS", fold_id)
            )
        cursor = fold_end + 1
    episodes.append(
        _episode(
            carrier_id,
            cursor,
            "SEALED_OOS",
            "SEALED",
        )
    )
    included = len(episodes)
    return {
        "schema_version": "STRUCTURE_EVIDENCE_INPUT_V2",
        "carrier_id": carrier_id,
        "evidence_receipt_id": f"{carrier_id.lower()}-receipt-v2",
        "episode_cohort_id": f"{carrier_id.lower()}-cohort-v2",
        "distribution_id": f"{carrier_id.lower()}-distribution-v2",
        "entry_clock_id": "ENTRY_1045_ET",
        "exit_clock_id": f"{carrier_id}_EXIT_F0",
        "cost_model_version": "AFTER_COST_EXECUTABLE_BBO_V2",
        "rule_version": f"{carrier_id}_RULE_F0",
        "entry_policy_sha256": _sha("entry-policy-f0"),
        "fee_schedule_sha256": _sha(f"{carrier_id}-fees"),
        "exit_policy_sha256": _sha(f"{carrier_id}-exit"),
        "coverage": {
            "eligible_episode_count": included + 5,
            "included_episode_count": included,
            "excluded_episode_count": 5,
            "coverage_ppm": included * PPM // (included + 5),
            "exclusion_reasons": [
                {"reason_code": "SOURCE_GAP", "count": 5}
            ],
        },
        "fold_manifest": fold_manifest,
        "episodes": episodes,
        "estimator": {
            "estimator_id": "CIRCULAR_MBB_KELLY_V2",
            "uncertainty_method_id": "CIRCULAR_MBB_ONE_SIDED_5PCT_V1",
            "bootstrap_replicates": 10_000,
            "block_length": 20,
            "lower_bound_rank": 500,
            "kelly_quantile_rank": 500,
            "rounding_mode": "SIGNED_ROUND_HALF_EVEN",
            "hash_start_method": "SHA256_COUNTER_MOD_N_V1",
        },
    }


def _bundle() -> dict[str, object]:
    return {
        "schema_version": "HISTORICAL_STRUCTURE_EVIDENCE_BUNDLE_V2",
        "classification": "SYNTHETIC_ONLY",
        "scope": "GLD_ENTRY_STRUCTURE_EVIDENCE_ONLY",
        "underlying": "GLD",
        "carriers": [_carrier("LC0"), _carrier("BCS0")],
    }


class TestIntegerEvidenceMath(unittest.TestCase):
    def test_signed_round_half_even(self) -> None:
        self.assertEqual(round_half_even_divide(5, 2), 2)
        self.assertEqual(round_half_even_divide(7, 2), 4)
        self.assertEqual(round_half_even_divide(-5, 2), -2)
        self.assertEqual(round_half_even_divide(-7, 2), -4)

    def test_after_cost_lc_and_complete_bcs_use_executable_sides(self) -> None:
        lc = derive_episode_after_cost(_episode("LC0", 1, "WALK_FORWARD_OOS", "WF-1"), "LC0")
        self.assertEqual(lc["entry_debit_nano_usd"], 100_000)
        self.assertEqual(lc["exit_value_nano_usd"], 110_100)
        self.assertEqual(lc["after_cost_profit_nano_usd"], 9_900)
        self.assertEqual(lc["after_cost_return_on_entry_debit_ppm"], 99_000)

        bcs = derive_episode_after_cost(_episode("BCS0", 1, "WALK_FORWARD_OOS", "WF-1"), "BCS0")
        self.assertEqual(bcs["entry_debit_nano_usd"], 60_000)
        self.assertEqual(bcs["exit_value_nano_usd"], 70_100)
        self.assertEqual(bcs["after_cost_profit_nano_usd"], 9_900)
        self.assertEqual(bcs["after_cost_return_on_entry_debit_ppm"], 165_000)

    def test_full_kelly_obeys_strictly_positive_wealth_domain(self) -> None:
        self.assertEqual(estimate_full_kelly_ppm((100_000, 200_000)), PPM)
        self.assertEqual(estimate_full_kelly_ppm((-1_000_000, -200_000)), 0)
        mixed = estimate_full_kelly_ppm((1_000_000, -2_000_000))
        self.assertGreaterEqual(mixed, 0)
        self.assertLess(mixed, 500_000)
        self.assertGreater(PPM * PPM + mixed * -2_000_000, 0)

    def test_full_kelly_ignores_hostile_ambient_decimal_context(self) -> None:
        returns = (1_000_000, -500_000)
        expected = estimate_full_kelly_ppm(returns)
        original = getcontext().copy()
        try:
            context = getcontext()
            context.prec = 6
            context.rounding = ROUND_DOWN
            context.traps[Inexact] = True
            self.assertEqual(estimate_full_kelly_ppm(returns), expected)
        finally:
            setcontext(original)

    def test_bootstrap_has_exact_replicate_count_and_rank_is_observable(self) -> None:
        result = circular_moving_block_bootstrap(
            (100_000, -50_000, 25_000, 75_000),
            seed_sha256=_sha("bootstrap-seed"),
            replicates=10_000,
            block_length=20,
        )
        self.assertEqual(len(result.means_ppm), 10_000)
        self.assertEqual(len(result.full_kelly_ppm), 10_000)
        self.assertEqual(result.lower_bound_ppm, sorted(result.means_ppm)[499])
        self.assertEqual(
            result.kelly_5pct_ppm,
            sorted(result.full_kelly_ppm)[499],
        )

    def test_multiblock_bootstrap_has_a_fixed_golden_and_work_bound(self) -> None:
        returns = tuple((index - 12) * 10_000 for index in range(25))
        result = circular_moving_block_bootstrap(
            returns,
            seed_sha256=_sha("n>20-golden"),
            replicates=8,
            block_length=20,
            lower_bound_rank=2,
            kelly_quantile_rank=2,
        )
        self.assertEqual(
            result.means_ppm,
            (-12_000, 6_000, 4_000, -22_000, -20_000, -10_000, 8_000, -4_000),
        )
        self.assertEqual(
            result.full_kelly_ppm,
            (0, 781_537, 680_274, 0, 0, 0, 1_000_000, 0),
        )
        self.assertEqual(result.lower_bound_ppm, -20_000)
        self.assertEqual(result.kelly_5pct_ppm, 0)

        with self.assertRaises(EvidenceValidationError) as failure:
            circular_moving_block_bootstrap(
                tuple(range(257)),
                seed_sha256=_sha("work-bound"),
                replicates=10_000,
                block_length=20,
            )
        self.assertEqual(
            failure.exception.reason_code,
            "EVIDENCE_BOOTSTRAP_WORK_BOUND_EXCEEDED",
        )


class TestEvidenceBundleV2(unittest.TestCase):
    def test_typed_receipt_adapter_rejects_stale_seal(self) -> None:
        bundle = validate_structure_evidence_v2(_bundle())
        receipt = bundle.carriers[0]
        changed = replace(
            receipt,
            expected_net_return_lower_bound_ppm=1,
        )
        with self.assertRaises(EvidenceValidationError) as failure:
            changed.as_entry_summary()
        self.assertEqual(
            failure.exception.reason_code,
            "EVIDENCE_RECEIPT_TYPED_INTEGRITY_MISMATCH",
        )

    def test_receipt_can_seal_wide_derived_nano_usd_values(self) -> None:
        raw = _bundle()
        carrier = next(
            item for item in raw["carriers"] if item["carrier_id"] == "LC0"
        )
        episode = next(
            item
            for item in carrier["episodes"]
            if item["split"] == "WALK_FORWARD_OOS"
        )
        episode["long_entry_ask_nano_usd"] = 2**63 - 1
        episode["long_exit_bid_nano_usd"] = 2**63 - 1
        episode["multiplier"] = 100_000

        result = validate_structure_evidence_v2(raw)
        receipt = next(
            item for item in result.carriers if item.carrier_id == "LC0"
        ).as_dict()
        derived_episode = next(
            item
            for item in receipt["episodes"]
            if item["episode_id"] == episode["episode_id"]
        )
        self.assertGreater(derived_episode["entry_debit_nano_usd"], 10**19)

    def test_valid_independent_bundle_is_deterministic_and_sorted(self) -> None:
        raw = _bundle()
        first = validate_structure_evidence_v2(raw)
        reordered = deepcopy(raw)
        reordered["carriers"].reverse()
        for carrier in reordered["carriers"]:
            carrier["episodes"].reverse()
        second = validate_structure_evidence_v2(reordered)

        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        self.assertEqual(first.bundle_sha256, second.bundle_sha256)
        self.assertEqual(
            tuple(receipt.carrier_id for receipt in first.carriers),
            ("BCS0", "LC0"),
        )
        for receipt in first.carriers:
            self.assertEqual(receipt.oos_episode_count, 100)
            self.assertEqual(receipt.fold_count, 3)
            self.assertGreaterEqual(receipt.coverage_ppm, 950_000)
            self.assertGreater(receipt.expected_net_return_lower_bound_ppm, 0)
            self.assertEqual(receipt.full_kelly_ppm, PPM)
            self.assertEqual(receipt.robust_full_kelly_ppm, PPM)
            self.assertEqual(receipt.half_kelly_ppm, 500_000)
            self.assertFalse(receipt.development_used_in_estimate)
            self.assertFalse(receipt.sealed_oos_used_in_estimate)
        entry_summaries = first.as_entry_evidence()
        self.assertEqual(set(entry_summaries), {"LC0", "BCS0"})
        for summary in entry_summaries.values():
            self.assertEqual(
                summary["expected_net_return_on_entry_debit_ppm"],
                sorted(
                    receipt.expected_net_return_lower_bound_ppm
                    for receipt in first.carriers
                    if receipt.carrier_id == summary["carrier_id"]
                )[0],
            )
            self.assertEqual(summary["schema_version"], "HISTORICAL_STRUCTURE_EVIDENCE_V2")

    def test_development_and_sealed_are_excluded_from_estimate(self) -> None:
        raw = _bundle()
        baseline = validate_structure_evidence_v2(raw)
        changed = deepcopy(raw)
        for carrier in changed["carriers"]:
            for episode in carrier["episodes"]:
                if episode["split"] != "WALK_FORWARD_OOS":
                    episode["long_exit_bid_nano_usd"] = 1
                    if carrier["carrier_id"] == "BCS0":
                        episode["short_exit_ask_nano_usd"] = 50_000
        updated = validate_structure_evidence_v2(changed)

        for old, new in zip(baseline.carriers, updated.carriers, strict=True):
            self.assertEqual(
                old.expected_net_return_on_entry_debit_ppm,
                new.expected_net_return_on_entry_debit_ppm,
            )
            self.assertEqual(
                old.expected_net_return_lower_bound_ppm,
                new.expected_net_return_lower_bound_ppm,
            )
            self.assertEqual(old.distribution_sha256, new.distribution_sha256)
            self.assertNotEqual(old.input_sha256, new.input_sha256)
            self.assertNotEqual(old.evidence_sha256, new.evidence_sha256)

    def test_coverage_oos_and_fold_minimums_fail_closed(self) -> None:
        cases: list[tuple[dict[str, object], str]] = []

        ninety_nine = _bundle()
        ninety_nine["carriers"][0] = _carrier("LC0", walk_forward_count=99)
        cases.append((ninety_nine, "EVIDENCE_OOS_EPISODES_INSUFFICIENT"))

        two_folds = _bundle()
        two_folds["carriers"][0] = _carrier("LC0", fold_count=2)
        cases.append((two_folds, "EVIDENCE_FOLDS_INSUFFICIENT"))

        low_coverage = _bundle()
        coverage = low_coverage["carriers"][0]["coverage"]
        coverage.update(
            {
                "eligible_episode_count": 204,
                "included_episode_count": 102,
                "excluded_episode_count": 102,
                "coverage_ppm": 500_000,
                "exclusion_reasons": [
                    {"reason_code": "SOURCE_GAP", "count": 102}
                ],
            }
        )
        cases.append((low_coverage, "EVIDENCE_COVERAGE_INSUFFICIENT"))

        missing_sealed = _bundle()
        carrier = missing_sealed["carriers"][0]
        carrier["episodes"] = [
            item
            for item in carrier["episodes"]
            if item["split"] != "SEALED_OOS"
        ]
        coverage = carrier["coverage"]
        coverage.update(
            {
                "eligible_episode_count": 106,
                "included_episode_count": 101,
                "excluded_episode_count": 5,
                "coverage_ppm": 101 * PPM // 106,
            }
        )
        cases.append((missing_sealed, "EVIDENCE_SPLITS_INCOMPLETE"))

        for document, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(EvidenceValidationError) as failure:
                    validate_structure_evidence_v2(document)
                self.assertEqual(failure.exception.reason_code, reason)

    def test_shared_or_opaque_evidence_and_schema_tampering_are_rejected(self) -> None:
        shared = _bundle()
        shared["carriers"][1]["evidence_receipt_id"] = shared["carriers"][0]["evidence_receipt_id"]
        with self.assertRaises(EvidenceValidationError) as failure:
            validate_structure_evidence_v2(shared)
        self.assertEqual(failure.exception.reason_code, "EVIDENCE_NOT_INDEPENDENT")

        shared_sources = _bundle()
        lc_episodes = shared_sources["carriers"][0]["episodes"]
        bcs_episodes = shared_sources["carriers"][1]["episodes"]
        for lc_episode, bcs_episode in zip(lc_episodes, bcs_episodes, strict=True):
            bcs_episode["input_sha256"] = lc_episode["input_sha256"]
        with self.assertRaises(EvidenceValidationError) as failure:
            validate_structure_evidence_v2(shared_sources)
        self.assertEqual(failure.exception.reason_code, "EVIDENCE_NOT_INDEPENDENT")

        invalid_fold = _bundle()
        carrier = invalid_fold["carriers"][0]
        fold_two = next(
            episode
            for episode in carrier["episodes"]
            if episode["fold_id"] == "WF-2"
        )
        fold_two["fold_id"] = "WF-1"
        with self.assertRaises(EvidenceValidationError) as failure:
            validate_structure_evidence_v2(invalid_fold)
        self.assertEqual(
            failure.exception.reason_code, "EVIDENCE_FOLD_ASSIGNMENT_INVALID"
        )

        exit_leak = _bundle()
        carrier = exit_leak["carriers"][0]
        fold_one = next(
            fold
            for fold in carrier["fold_manifest"]
            if fold["fold_id"] == "WF-1"
        )
        last_fold_one_episode = max(
            (
                episode
                for episode in carrier["episodes"]
                if episode["fold_id"] == "WF-1"
            ),
            key=lambda episode: episode["entry_utc_ns"],
        )
        last_fold_one_episode["exit_utc_ns"] = fold_one["test_end_utc_ns"] + 1
        with self.assertRaises(EvidenceValidationError) as failure:
            validate_structure_evidence_v2(exit_leak)
        self.assertEqual(
            failure.exception.reason_code, "EVIDENCE_FOLD_ASSIGNMENT_INVALID"
        )

        opaque = _bundle()
        opaque["carriers"][0]["episodes"] = []
        with self.assertRaises(EvidenceValidationError) as failure:
            validate_structure_evidence_v2(opaque)
        self.assertEqual(failure.exception.reason_code, "EVIDENCE_OPAQUE_ONLY")

        duplicate_source = _bundle()
        episodes = duplicate_source["carriers"][0]["episodes"]
        episodes[1]["input_sha256"] = episodes[0]["input_sha256"]
        with self.assertRaises(EvidenceValidationError) as failure:
            validate_structure_evidence_v2(duplicate_source)
        self.assertEqual(
            failure.exception.reason_code,
            "EVIDENCE_EPISODE_SOURCE_DUPLICATE",
        )

        duplicate_interval = _bundle()
        episodes = duplicate_interval["carriers"][0]["episodes"]
        episodes[1]["entry_utc_ns"] = episodes[0]["entry_utc_ns"]
        episodes[1]["exit_utc_ns"] = episodes[0]["exit_utc_ns"]
        with self.assertRaises(EvidenceValidationError) as failure:
            validate_structure_evidence_v2(duplicate_interval)
        self.assertEqual(
            failure.exception.reason_code,
            "EVIDENCE_EPISODE_INTERVAL_DUPLICATE",
        )

        unknown = _bundle()
        unknown["unexpected"] = True
        with self.assertRaises(EvidenceValidationError) as failure:
            validate_structure_evidence_v2(unknown)
        self.assertEqual(failure.exception.reason_code, "EVIDENCE_BUNDLE_SCHEMA_INVALID")

        floated = _bundle()
        floated["carriers"][0]["coverage"]["coverage_ppm"] = 0.96
        with self.assertRaises(EvidenceValidationError) as failure:
            validate_structure_evidence_v2(floated)
        self.assertEqual(failure.exception.reason_code, "EVIDENCE_FLOAT_FORBIDDEN")

    def test_lower_bound_not_positive_zeros_robust_and_half_kelly(self) -> None:
        raw = _bundle()
        for carrier in raw["carriers"]:
            for episode in carrier["episodes"]:
                if episode["split"] == "WALK_FORWARD_OOS":
                    episode["long_exit_bid_nano_usd"] = 1
                    if carrier["carrier_id"] == "BCS0":
                        episode["short_exit_ask_nano_usd"] = 50_000
        result = validate_structure_evidence_v2(raw)
        for receipt in result.carriers:
            self.assertLessEqual(receipt.expected_net_return_lower_bound_ppm, 0)
            self.assertEqual(receipt.robust_full_kelly_ppm, 0)
            self.assertEqual(receipt.half_kelly_ppm, 0)


if __name__ == "__main__":
    unittest.main()
