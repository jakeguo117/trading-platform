"""Frozen synthetic scenarios for visible Entry Decision R1 acceptance."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
import json

from .canonical import canonical_json_bytes, canonical_json_sha256
from .evidence import EvidenceValidationError, validate_structure_evidence_v2
from .errors import EntryDecisionF0Error


HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64
HEX_E = "e" * 64
PPM = 1_000_000
BASE_NS = 1_767_225_600_000_000_000
DAY_NS = 86_400_000_000_000
ENTRY_CUTOFF_NS = 1_805_381_100_000_000_000
QUOTE_EVENT_NS = ENTRY_CUTOFF_NS - 1_000_000_000
QUOTE_RECEIVE_NS = ENTRY_CUTOFF_NS - 900_000_000
FACTS_MAX_EVENT_NS = ENTRY_CUTOFF_NS - 500_000_000


ACCEPTANCE_SCENARIOS_F0: tuple[dict[str, object], ...] = (
    {
        "scenario_id": "missing_input",
        "purpose": "Incomplete Call universe fails closed before Entry Gate",
        "owner_label": "数据不完整",
        "key_input_zh": "Call universe被明确标记为不完整。",
        "expected_status": "NO_DECISION",
        "expected_carriers": (),
        "expected_preferred_carrier": None,
    },
    {
        "scenario_id": "gate_fail",
        "purpose": "Complete facts fail the fixed grouped Entry Gate",
        "owner_label": "数据完整但Gate失败",
        "key_input_zh": "数据完整，但prior close低于SMA50，严格Gate未达标。",
        "expected_status": "NO_ACTION",
        "expected_carriers": (),
        "expected_preferred_carrier": None,
    },
    {
        "scenario_id": "lc_only",
        "purpose": "BCS0 elimination leaves one complete LC0 plan",
        "owner_label": "只有LC0合格",
        "key_input_zh": "BCS0缺少结构economics，LC0事实完整。",
        "expected_status": "SINGLE_PLAN",
        "expected_carriers": ("LC0",),
        "expected_preferred_carrier": "LC0",
    },
    {
        "scenario_id": "bcs_only",
        "purpose": "LC0 elimination leaves one complete BCS0 plan",
        "owner_label": "只有BCS0合格",
        "key_input_zh": "LC0缺少结构economics，BCS0事实完整。",
        "expected_status": "SINGLE_PLAN",
        "expected_carriers": ("BCS0",),
        "expected_preferred_carrier": "BCS0",
    },
    {
        "scenario_id": "both_qualified",
        "purpose": "Both structures qualify and expected net profit gives a preference",
        "owner_label": "两个方案都合格",
        "key_input_zh": "LC0和BCS0都完整合格，BCS0的lower-bound预期总净利润更高。",
        "expected_status": "PREFERRED_AND_BACKUP",
        "expected_carriers": ("BCS0", "LC0"),
        "expected_preferred_carrier": "BCS0",
    },
    {
        "scenario_id": "exact_tie",
        "purpose": "An exact preference tie uses the frozen LC0 tie-break",
        "owner_label": "两个方案完全平局",
        "key_input_zh": "两方案预期净利润、planned loss和cash usage完全相同。",
        "expected_status": "PREFERRED_AND_BACKUP",
        "expected_carriers": ("LC0", "BCS0"),
        "expected_preferred_carrier": "LC0",
    },
    {
        "scenario_id": "zero_cap",
        "purpose": "A hard risk cap of zero eliminates otherwise valid structures",
        "owner_label": "硬风险容量为零",
        "key_input_zh": "账户drawdown达到禁止新Entry的边界，数量硬cap为0。",
        "expected_status": "NO_ACTION",
        "expected_carriers": (),
        "expected_preferred_carrier": None,
    },
    {
        "scenario_id": "policy_missing",
        "purpose": "An unavailable owner policy blocks a decision",
        "owner_label": "政策权限缺失",
        "key_input_zh": "Entry policy存在但owner_approved=false，因此没有可用政策权限。",
        "expected_status": "NO_DECISION",
        "expected_carriers": (),
        "expected_preferred_carrier": None,
    },
)


def _policy_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "ENTRY_POLICY_F0_V1",
        "policy_id": "GLD_ENTRY_RESEARCH_TRIAL_F0_STRICT",
        "policy_status": "RESEARCH_TRIAL",
        "owner_approved": True,
        "trend_required_count": 3,
        "breakout_required_count": 2,
        "lc0_delta_min_ppm": 450_000,
        "lc0_delta_target_ppm": 500_000,
        "lc0_delta_max_ppm": 550_000,
        "bcs0_long_delta_min_ppm": 450_000,
        "bcs0_long_delta_target_ppm": 500_000,
        "bcs0_long_delta_max_ppm": 550_000,
        "bcs0_short_delta_min_ppm": 200_000,
        "bcs0_short_delta_target_ppm": 250_000,
        "bcs0_short_delta_max_ppm": 300_000,
        "account_loss_budget_ppm": 100_000,
        "expiry_safety_calendar_days": 30,
        "management_policy_id": "GLD_MANAGEMENT_PRIMARY_F0",
        "management_policy_sha256": HEX_A,
        "invalidation_confirmation_sessions": 2,
    }
    document.update(overrides)
    return document


def _sha(label: str) -> str:
    return sha256(label.encode("ascii")).hexdigest()


def _fee_schedule_sha256(carrier_id: str) -> str:
    return _sha(f"{carrier_id}-fee-schedule-f0")


def _exit_policy_document(carrier_id: str) -> dict[str, object]:
    policy_id = f"{carrier_id}_HARD_STOP_500K_F0"
    hard_stop_loss_ppm = 500_000
    return {
        "policy_id": policy_id,
        "hard_stop_loss_ppm": hard_stop_loss_ppm,
        "policy_sha256": canonical_json_sha256(
            {
                "policy_id": policy_id,
                "hard_stop_loss_ppm": hard_stop_loss_ppm,
            }
        ),
        "fee_schedule_sha256": _fee_schedule_sha256(carrier_id),
    }


def _evidence_episode(
    carrier_id: str,
    index: int,
    split: str,
    fold_id: str,
    variant_id: str,
) -> dict[str, object]:
    common: dict[str, object] = {
        "episode_id": f"{variant_id}-{carrier_id.lower()}-{split.lower()}-{index:03d}",
        "fold_id": fold_id,
        "split": split,
        "entry_utc_ns": BASE_NS + index * DAY_NS,
        "exit_utc_ns": BASE_NS + (index + 1) * DAY_NS,
        "multiplier": 100,
        "opening_fees_nano_usd": 100,
        "closing_fees_nano_usd": 100,
        "input_sha256": _sha(
            f"{variant_id}-{carrier_id}-episode-{index}-{split}"
        ),
    }
    if variant_id == "exact-tie":
        if carrier_id == "LC0":
            common.update(
                {
                    "long_entry_ask_nano_usd": 1_000,
                    "long_exit_bid_nano_usd": 1_102,
                    "short_entry_bid_nano_usd": None,
                    "short_exit_ask_nano_usd": None,
                }
            )
        else:
            common.update(
                {
                    "long_entry_ask_nano_usd": 1_000,
                    "long_exit_bid_nano_usd": 1_162,
                    "short_entry_bid_nano_usd": 400,
                    "short_exit_ask_nano_usd": 500,
                }
            )
    elif carrier_id == "LC0":
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
                "long_exit_bid_nano_usd": 1_206 + index % 7,
                "short_entry_bid_nano_usd": 400,
                "short_exit_ask_nano_usd": 500,
            }
        )
    return common


def _evidence_carrier(carrier_id: str, variant_id: str) -> dict[str, object]:
    episodes = [
        _evidence_episode(carrier_id, 0, "DEVELOPMENT", "DEV", variant_id)
    ]
    fold_ranges = (
        ("WF-1", 2, 36),
        ("WF-2", 37, 70),
        ("WF-3", 71, 104),
    )
    for fold_id, start_index, end_index in fold_ranges:
        for index in range(start_index, end_index):
            episodes.append(
                _evidence_episode(
                    carrier_id,
                    index,
                    "WALK_FORWARD_OOS",
                    fold_id,
                    variant_id,
                )
            )
    episodes.append(
        _evidence_episode(carrier_id, 105, "SEALED_OOS", "SEALED", variant_id)
    )
    included = len(episodes)
    fold_manifest = [
        {
            "fold_id": fold_id,
            "train_end_utc_ns": BASE_NS + start_index * DAY_NS - 1,
            "test_start_utc_ns": BASE_NS + start_index * DAY_NS,
            "test_end_utc_ns": BASE_NS + end_index * DAY_NS,
        }
        for fold_id, start_index, end_index in fold_ranges
    ]
    return {
        "schema_version": "STRUCTURE_EVIDENCE_INPUT_V2",
        "carrier_id": carrier_id,
        "evidence_receipt_id": f"{variant_id}-{carrier_id.lower()}-receipt-v2",
        "episode_cohort_id": f"{variant_id}-{carrier_id.lower()}-cohort-v2",
        "distribution_id": f"{variant_id}-{carrier_id.lower()}-distribution-v2",
        "entry_clock_id": "ENTRY_1045_ET",
        "exit_clock_id": f"{variant_id.upper().replace('-', '_')}_{carrier_id}_EXIT_F0",
        "cost_model_version": "AFTER_COST_EXECUTABLE_BBO_V2",
        "rule_version": f"{variant_id.upper().replace('-', '_')}_{carrier_id}_RULE_F0",
        "entry_policy_sha256": canonical_json_sha256(_policy_document()),
        "fee_schedule_sha256": _fee_schedule_sha256(carrier_id),
        "exit_policy_sha256": _exit_policy_document(carrier_id)[
            "policy_sha256"
        ],
        "coverage": {
            "eligible_episode_count": included + 5,
            "included_episode_count": included,
            "excluded_episode_count": 5,
            "coverage_ppm": included * PPM // (included + 5),
            "exclusion_reasons": [{"reason_code": "SOURCE_GAP", "count": 5}],
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


def _raw_evidence_bundle(variant_id: str) -> dict[str, object]:
    if variant_id not in {"base", "exact-tie"}:
        raise EntryDecisionF0Error("ACCEPTANCE_EVIDENCE_VARIANT_UNKNOWN")
    return {
        "schema_version": "HISTORICAL_STRUCTURE_EVIDENCE_BUNDLE_V2",
        "classification": "SYNTHETIC_ONLY",
        "scope": "GLD_ENTRY_STRUCTURE_EVIDENCE_ONLY",
        "underlying": "GLD",
        "carriers": [
            _evidence_carrier("LC0", variant_id),
            _evidence_carrier("BCS0", variant_id),
        ],
    }


@lru_cache(maxsize=2)
def _cached_evidence_material(
    variant_id: str,
) -> tuple[bytes, bytes, bytes, tuple[tuple[str, bytes], ...], str]:
    raw_bundle = _raw_evidence_bundle(variant_id)
    try:
        validated = validate_structure_evidence_v2(raw_bundle)
    except EvidenceValidationError as exc:
        raise EntryDecisionF0Error(
            "ACCEPTANCE_EVIDENCE_VALIDATION_FAILED", exc.reason_code
        ) from exc
    receipt_bytes = tuple(
        (receipt.carrier_id, receipt.canonical_bytes)
        for receipt in validated.carriers
    )
    return (
        canonical_json_bytes(raw_bundle),
        validated.canonical_bytes,
        canonical_json_bytes(validated.as_entry_evidence()),
        receipt_bytes,
        validated.bundle_sha256,
    )


def synthetic_evidence_material_f0(variant_id: str) -> dict[str, object]:
    """Return a detached, formally estimated Evidence V2 artifact family."""

    raw, bundle, entry, receipts, bundle_hash = _cached_evidence_material(variant_id)
    return {
        "variant_id": variant_id,
        "raw_input": json.loads(raw),
        "validated_bundle": json.loads(bundle),
        "entry_evidence": json.loads(entry),
        "receipts": {
            carrier_id: json.loads(payload) for carrier_id, payload in receipts
        },
        "bundle_sha256": bundle_hash,
    }


def _call(
    contract_id: str,
    strike_nano_usd: int,
    delta_ppm: int,
    *,
    bid_nano_usd: int = 9_000_000_000,
    ask_nano_usd: int = 10_000_000_000,
) -> dict[str, object]:
    return {
        "contract_id": contract_id,
        "expiry_date": "2027-06-18",
        "last_trading_date": "2027-06-18",
        "strike_nano_usd": strike_nano_usd,
        "multiplier": 100,
        "coarse_delta_ppm": delta_ppm,
        "fine_delta_ppm": delta_ppm,
        "bid_nano_usd": bid_nano_usd,
        "ask_nano_usd": ask_nano_usd,
        "bid_size": 8,
        "ask_size": 8,
        "tick_nano_usd": 10_000_000,
        "quote_event_utc_ns": QUOTE_EVENT_NS,
        "quote_receive_utc_ns": QUOTE_RECEIVE_NS,
        "executable": True,
        "snapshot_sha256": HEX_C,
        "technical_facts_sha256": HEX_B,
        "executability_source": "REQUIRED_TECHNICAL_FACTS_V1",
    }


def _entry_input_document() -> dict[str, object]:
    return {
        "schema_version": "ENTRY_DECISION_INPUT_F0_V1",
        "classification": "SYNTHETIC_ONLY",
        "instrument_id": "GLD",
        "data_qualification_status": "STRUCTURALLY_VALID_SYNTHETIC",
        "entry_fact_bundle_sha256": HEX_A,
        "technical_facts_sha256": HEX_B,
        "atomic_snapshot_sha256": HEX_C,
        "universe_complete": True,
        "decision_cutoff_utc_ns": ENTRY_CUTOFF_NS,
        "facts_max_event_utc_ns": FACTS_MAX_EVENT_NS,
        "open_gld_order_count": 0,
        "entry_session_date": "2027-03-18",
        "h20_date": "2027-04-16",
        "h20_exit_utc_ns": 1_807_886_700_000_000_000,
        "entry_gate_facts": {
            "prior_close_nano_usd": 200_000_000_000,
            "sma50_nano_usd": 195_000_000_000,
            "sma200_nano_usd": 190_000_000_000,
            "sma50_slope_nano_usd_per_session": 100_000_000,
            "prior_high20_nano_usd": 201_000_000_000,
            "minute_1044_close_nano_usd": 202_000_000_000,
            "confirmation_closes_nano_usd": [202_000_000_000 for _ in range(15)],
        },
        "account_facts": {
            "eligible_bankroll_nano_usd": 100_000_000_000_000,
            "current_nlv_nano_usd": 100_000_000_000_000,
            "peak_nlv_nano_usd": 100_000_000_000_000,
            "settled_cash_nano_usd": 100_000_000_000_000,
            "cash_reserve_nano_usd": 100_000_000_000,
            "current_signed_gld_delta_exposure_nano_usd": 0,
            "delta_limit_nano_usd": 2_000_000_000_000,
            "external_capacity_units": 20,
        },
        "call_universe": [
            _call("LC-LONG", 190_000_000_000, 500_000),
            _call("BCS-LONG", 191_000_000_000, 490_000),
            _call(
                "BCS-SHORT",
                210_000_000_000,
                250_000,
                bid_nano_usd=4_000_000_000,
                ask_nano_usd=5_000_000_000,
            ),
        ],
        "lc0_economics": [
            {
                "contract_id": "LC-LONG",
                "stressed_entry_debit_nano_usd": 1_000_000_000_000,
                "stressed_max_loss_basis_nano_usd": 1_000_000_000_000,
                "delta_notional_nano_usd": 10_000_000_000,
            },
            {
                "contract_id": "BCS-LONG",
                "stressed_entry_debit_nano_usd": 1_000_000_000_000,
                "stressed_max_loss_basis_nano_usd": 1_000_000_000_000,
                "delta_notional_nano_usd": 10_000_000_000,
            },
        ],
        "bcs0_economics": [
            {
                "long_contract_id": "BCS-LONG",
                "short_contract_id": "BCS-SHORT",
                "stressed_entry_debit_nano_usd": 600_000_000_000,
                "stressed_max_loss_basis_nano_usd": 600_000_000_000,
                "delta_notional_nano_usd": 5_000_000_000,
            }
        ],
        "structure_evidence": {"LC0": None, "BCS0": None},
        "exit_policies": {
            "LC0": _exit_policy_document("LC0"),
            "BCS0": _exit_policy_document("BCS0"),
        },
        "planned_exit_fees_nano_usd": {
            "LC0": 1_000_000_000,
            "BCS0": 2_000_000_000,
        },
    }


def _scenario(scenario_id: str) -> dict[str, object]:
    for specification in ACCEPTANCE_SCENARIOS_F0:
        if specification["scenario_id"] == scenario_id:
            return specification
    raise EntryDecisionF0Error("ACCEPTANCE_SCENARIO_UNKNOWN", scenario_id)


def build_synthetic_entry_decision_case_f0(
    scenario_id: str,
) -> dict[str, object]:
    """Build one deterministic CLI input containing facts and frozen policy."""

    _scenario(scenario_id)
    entry_input = _entry_input_document()
    entry_policy = _policy_document()
    evidence_variant = "exact-tie" if scenario_id == "exact_tie" else "base"
    evidence_material = synthetic_evidence_material_f0(evidence_variant)
    entry_input["structure_evidence"] = evidence_material["entry_evidence"]
    if scenario_id == "missing_input":
        entry_input["universe_complete"] = False
    elif scenario_id == "gate_fail":
        gate = entry_input["entry_gate_facts"]
        assert type(gate) is dict
        gate["prior_close_nano_usd"] = 180_000_000_000
    elif scenario_id == "lc_only":
        entry_input["bcs0_economics"] = []
    elif scenario_id == "bcs_only":
        entry_input["lc0_economics"] = []
    elif scenario_id == "exact_tie":
        lc_economics = entry_input["lc0_economics"]
        bcs_economics = entry_input["bcs0_economics"]
        assert type(lc_economics) is list and type(lc_economics[0]) is dict
        assert type(bcs_economics) is list and type(bcs_economics[0]) is dict
        lc_economics[0]["stressed_entry_debit_nano_usd"] = 1_001_000_000_000
        lc_economics[0]["stressed_max_loss_basis_nano_usd"] = 1_001_000_000_000
        bcs_economics[0]["stressed_entry_debit_nano_usd"] = 1_000_000_000_000
        bcs_economics[0]["stressed_max_loss_basis_nano_usd"] = 1_000_000_000_000
        call_universe = entry_input["call_universe"]
        assert type(call_universe) is list
        for candidate in call_universe:
            if type(candidate) is dict and candidate.get("contract_id") == "BCS-LONG":
                candidate["ask_nano_usd"] = 14_000_000_000
                break
        else:
            raise EntryDecisionF0Error("ACCEPTANCE_FIXTURE_CONTRACT_MISSING")
    elif scenario_id == "zero_cap":
        account = entry_input["account_facts"]
        assert type(account) is dict
        account["current_nlv_nano_usd"] = 85_000_000_000_000
    elif scenario_id == "policy_missing":
        entry_policy["owner_approved"] = False
    return {
        "schema_version": "ENTRY_DECISION_CLI_INPUT_F0_V1",
        "entry_input": entry_input,
        "entry_policy": entry_policy,
    }


def acceptance_scenario_spec_f0(scenario_id: str) -> dict[str, object]:
    return deepcopy(_scenario(scenario_id))


__all__ = [
    "ACCEPTANCE_SCENARIOS_F0",
    "acceptance_scenario_spec_f0",
    "build_synthetic_entry_decision_case_f0",
    "synthetic_evidence_material_f0",
]
