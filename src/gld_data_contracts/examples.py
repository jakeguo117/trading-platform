"""Deterministic synthetic examples for local validator demonstrations."""

from __future__ import annotations

from copy import deepcopy

from gld_simulation.canonical import canonical_json_sha256

from .contracts import DataContractError
from .validation import (
    SYNTHETIC_CARRIER_ESTIMATOR_PACKAGE_SHA256,
    SYNTHETIC_CARRIER_ESTIMATOR_VERSION,
    SYNTHETIC_CARRIER_UNCERTAINTY_METHOD,
    validate_carrier_evidence,
    validate_entry_bundle,
)


_DAY_NS = 86_400_000_000_000
_BASE_NS = 1_700_000_000_000_000_000
_ENTRY_HASH_DOMAINS = (
    "calendar",
    "daily_bars",
    "minute_bars",
    "market_status",
    "option_snapshot",
    "pit_inputs",
    "fee_schedule",
    "account_snapshot_a",
    "rule_package",
    "model_package",
    "source_qualification_receipts",
)


def _sha(seed: str) -> str:
    return canonical_json_sha256({"synthetic_seed": seed})


def _carrier_evidence(carrier_id: str) -> dict[str, object]:
    episodes = [
        {
            "episode_id": f"{carrier_id.lower()}-episode-{index + 1}",
            "entry_utc_ns": _BASE_NS + index * _DAY_NS,
            "exit_utc_ns": _BASE_NS + (index + 1) * _DAY_NS,
            "entry_debit_nano_usd": 500_000_000_000 + index,
            "after_cost_return_on_entry_debit_ppm": 100_000 - index * 50_000,
            "split": split,
            "input_sha256": _sha(f"{carrier_id}-episode-{index}"),
        }
        for index, split in enumerate(("DEVELOPMENT", "WALK_FORWARD", "SEALED_OOS"))
    ]
    distribution_id = f"{carrier_id.lower()}-synthetic-returns-v1"
    distribution_sha256 = canonical_json_sha256(
        {
            "schema_version": "CARRIER_RETURN_DISTRIBUTION_V1",
            "carrier_id": carrier_id,
            "distribution_id": distribution_id,
            "return_unit": "ON_ENTRY_DEBIT_PPM",
            "episode_returns": [
                {
                    "episode_id": item["episode_id"],
                    "split": item["split"],
                    "after_cost_return_on_entry_debit_ppm": item[
                        "after_cost_return_on_entry_debit_ppm"
                    ],
                }
                for item in episodes
            ],
        }
    )
    value: dict[str, object] = {
        "carrier_id": carrier_id,
        "evidence_receipt_id": f"{carrier_id.lower()}-synthetic-receipt-v1",
        "episode_cohort_id": f"{carrier_id.lower()}-synthetic-cohort-v1",
        "entry_clock_id": "ENTRY_1045_ET",
        "exit_clock_id": f"{carrier_id}_SYNTHETIC_EXIT_V1",
        "cost_model_version": "SYNTHETIC_COST_MODEL_V1",
        "rule_version": f"{carrier_id}_SYNTHETIC_RULE_V1",
        "episodes": episodes,
        "coverage": {
            "eligible_episode_count": 3,
            "included_episode_count": 3,
            "excluded_episode_count": 0,
            "coverage_ppm": 1_000_000,
            "exclusion_reasons": [],
        },
        "splits": {
            "development_count": 1,
            "walk_forward_count": 1,
            "sealed_oos_count": 1,
        },
        "distribution_binding": {
            "distribution_id": distribution_id,
            "return_unit": "ON_ENTRY_DEBIT_PPM",
            "episode_set_sha256": canonical_json_sha256(episodes),
            "distribution_sha256": distribution_sha256,
        },
        "expected_net_return_on_entry_debit_ppm": 50_000,
        "uncertainty_method": SYNTHETIC_CARRIER_UNCERTAINTY_METHOD,
        "full_kelly_ppm": 0,
        "robust_full_kelly_ppm": 0,
        "half_kelly_ppm": 0,
        "estimator_version": SYNTHETIC_CARRIER_ESTIMATOR_VERSION,
        "fee_schedule_sha256": _sha("synthetic-fees"),
        "exit_policy_sha256": _sha(f"{carrier_id}-exit"),
        "input_sha256": _sha(f"{carrier_id}-evidence-input"),
    }
    value["evidence_sha256"] = canonical_json_sha256(value)
    return value


def synthetic_carrier_evidence_bundle() -> dict[str, object]:
    """Return structurally valid, explicitly synthetic independent evidence."""

    carriers = [_carrier_evidence("LC0"), _carrier_evidence("BCS0")]
    return {
        "schema_version": "CARRIER_EVIDENCE_BUNDLE_V1",
        "classification": "SYNTHETIC_ONLY",
        "scope": "GLD_ENTRY_CARRIER_EVIDENCE_ONLY",
        "underlying": "GLD",
        "rule_package_sha256": _sha("synthetic-rule-package"),
        "estimator_package_sha256": (
            SYNTHETIC_CARRIER_ESTIMATOR_PACKAGE_SHA256
        ),
        "carriers": carriers,
        "content_hashes": {
            str(item["carrier_id"]): item["evidence_sha256"]
            for item in carriers
        },
    }


def _rehash_entry(document: dict[str, object], domain: str) -> None:
    hashes = document["content_hashes"]
    if type(hashes) is not dict or domain not in _ENTRY_HASH_DOMAINS:
        raise AssertionError("invalid synthetic failure-demo domain")
    hashes[f"{domain}_sha256"] = canonical_json_sha256(document[domain])


def _observed_entry_failure(
    name: str,
    expected_reason: str,
    document: dict[str, object],
) -> dict[str, object]:
    try:
        validate_entry_bundle(document)
    except DataContractError as error:
        return {
            "name": name,
            "expected_reason_code": expected_reason,
            "observed_reason_code": error.reason_code,
            "demonstrated": error.reason_code == expected_reason,
        }
    return {
        "name": name,
        "expected_reason_code": expected_reason,
        "observed_reason_code": "UNEXPECTED_ACCEPT",
        "demonstrated": False,
    }


def failure_demonstrations(
    normalized_entry: dict[str, object],
) -> list[dict[str, object]]:
    """Execute stale, missing, identity, and shared-evidence failures."""

    results: list[dict[str, object]] = []

    stale = deepcopy(normalized_entry)
    snapshot = stale["option_snapshot"]
    if type(snapshot) is not dict:
        raise AssertionError("invalid normalized entry")
    underlying = snapshot["underlying_bbo"]
    if type(underlying) is not dict:
        raise AssertionError("invalid normalized entry")
    underlying["event_utc_ns"] = snapshot["window_start_utc_ns"]
    underlying["receive_utc_ns"] = snapshot["window_start_utc_ns"]
    _rehash_entry(stale, "option_snapshot")
    results.append(
        _observed_entry_failure("STALE_QUOTE", "ENTRY_QUOTE_STALE", stale)
    )

    missing = deepcopy(normalized_entry)
    daily = missing["daily_bars"]
    if type(daily) is not dict or type(daily["bars"]) is not list:
        raise AssertionError("invalid normalized entry")
    daily["bars"] = daily["bars"][1:]
    _rehash_entry(missing, "daily_bars")
    results.append(
        _observed_entry_failure(
            "MISSING_DAILY_SESSION",
            "ENTRY_DAILY_HISTORY_INCOMPLETE",
            missing,
        )
    )

    identity = deepcopy(normalized_entry)
    snapshot = identity["option_snapshot"]
    if type(snapshot) is not dict or type(snapshot["option_quotes"]) is not list:
        raise AssertionError("invalid normalized entry")
    first_quote = snapshot["option_quotes"][0]
    if type(first_quote) is not dict or type(first_quote["top_of_book"]) is not dict:
        raise AssertionError("invalid normalized entry")
    first_quote["top_of_book"]["instrument_id"] = "WRONG-CONTRACT"
    _rehash_entry(identity, "option_snapshot")
    results.append(
        _observed_entry_failure(
            "OPTION_IDENTITY_CONFLICT",
            "ENTRY_OPTION_IDENTITY_MISMATCH",
            identity,
        )
    )

    evidence = synthetic_carrier_evidence_bundle()
    carriers = evidence["carriers"]
    if type(carriers) is not list:
        raise AssertionError("invalid synthetic evidence")
    carriers[1]["evidence_receipt_id"] = carriers[0]["evidence_receipt_id"]
    unsigned = {
        key: value
        for key, value in carriers[1].items()
        if key != "evidence_sha256"
    }
    carriers[1]["evidence_sha256"] = canonical_json_sha256(unsigned)
    evidence["content_hashes"]["BCS0"] = carriers[1]["evidence_sha256"]
    try:
        validate_carrier_evidence(evidence)
    except DataContractError as error:
        results.append(
            {
                "name": "SHARED_CARRIER_EVIDENCE",
                "expected_reason_code": "CARRIER_EVIDENCE_NOT_INDEPENDENT",
                "observed_reason_code": error.reason_code,
                "demonstrated": error.reason_code
                == "CARRIER_EVIDENCE_NOT_INDEPENDENT",
            }
        )
    else:
        results.append(
            {
                "name": "SHARED_CARRIER_EVIDENCE",
                "expected_reason_code": "CARRIER_EVIDENCE_NOT_INDEPENDENT",
                "observed_reason_code": "UNEXPECTED_ACCEPT",
                "demonstrated": False,
            }
        )
    return results


__all__ = ["failure_demonstrations", "synthetic_carrier_evidence_bundle"]
