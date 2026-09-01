"""Deterministic synthetic fixtures for visible Research F0 acceptance."""

from __future__ import annotations

from datetime import date

from .canonical import canonical_json_sha256
from .errors import ManagementResearchError
from .lineage import (
    derive_genesis_management_state_f0,
    derive_management_state_transition_f0,
)
from .xnys_calendar import (
    CALENDAR_ID,
    CALENDAR_VERSION_SHA256,
    action_1045_utc_ns,
    next_session_date,
    official_close_utc_ns,
    previous_session_date,
    session_kind,
)


BASE_PRICE_NANO_USD = 200_000_000_000

ACCEPTANCE_SCENARIOS_F0: tuple[dict[str, object], ...] = (
    {
        "scenario_id": "neutral_hold",
        "purpose": "Neutral score remains in the HOLD no-trade zone",
        "slope_nano_usd": 0,
        "carrier_id": "LC0",
        "original_units": 4,
        "current_units": 4,
        "expected_action": "HOLD",
        "expected_display_score_bp": 5_000,
        "expected_band": "HOLD",
        "expected_target_units": 4,
        "expected_reason_code": "PRIMARY_SCORE_HOLD_BAND",
    },
    {
        "scenario_id": "defensive_day_1",
        "purpose": "First weak close waits for confirmation",
        "slope_nano_usd": -10_000,
        "carrier_id": "LC0",
        "original_units": 4,
        "current_units": 4,
        "expected_action": "HOLD",
        "expected_display_score_bp": 3_000,
        "expected_band": "DEFENSIVE_1",
        "expected_target_units": 4,
        "expected_reason_code": "WAITING_FOR_CONFIRMATION",
    },
    {
        "scenario_id": "defensive_day_2",
        "purpose": "Confirmed moderate weakness reduces four units to three",
        "slope_nano_usd": -10_000,
        "carrier_id": "LC0",
        "original_units": 4,
        "current_units": 4,
        "below_40_streak": 1,
        "expected_action": "REDUCE",
        "expected_display_score_bp": 3_000,
        "expected_band": "DEFENSIVE_1",
        "expected_target_units": 3,
        "expected_reason_code": "CONFIRMED_DEFENSIVE_REDUCTION",
    },
    {
        "scenario_id": "severe_defensive_day_2",
        "purpose": "Confirmed severe weakness reduces four units to two",
        "slope_nano_usd": -500_000_000,
        "carrier_id": "LC0",
        "original_units": 4,
        "current_units": 4,
        "below_40_streak": 1,
        "below_20_streak": 1,
        "expected_action": "REDUCE",
        "expected_display_score_bp": 851,
        "expected_band": "DEFENSIVE_2",
        "expected_target_units": 2,
        "expected_reason_code": "CONFIRMED_DEFENSIVE_REDUCTION",
    },
    {
        "scenario_id": "recovery_day_1",
        "purpose": "First strong recovery close waits for confirmation",
        "slope_nano_usd": 54_400_000,
        "carrier_id": "LC0",
        "original_units": 4,
        "current_units": 2,
        "expected_action": "HOLD",
        "expected_display_score_bp": 8_000,
        "expected_band": "RESTORE_ELIGIBLE",
        "expected_target_units": 2,
        "expected_reason_code": "WAITING_FOR_RECOVERY_CONFIRMATION",
    },
    {
        "scenario_id": "recovery_day_2",
        "purpose": "Confirmed recovery restores only one exposure tier",
        "slope_nano_usd": 54_400_000,
        "carrier_id": "LC0",
        "original_units": 4,
        "current_units": 2,
        "above_70_streak": 1,
        "expected_action": "RECOVERY_READD",
        "expected_display_score_bp": 8_000,
        "expected_band": "RESTORE_ELIGIBLE",
        "expected_target_units": 3,
        "expected_reason_code": "RECOVERY_READD_ONE_TIER",
    },
    {
        "scenario_id": "recovery_next_level",
        "purpose": "A later eligible recovery day restores three units to four",
        "slope_nano_usd": 54_400_000,
        "carrier_id": "LC0",
        "original_units": 4,
        "current_units": 3,
        "above_70_streak": 1,
        "expected_action": "RECOVERY_READD",
        "expected_display_score_bp": 8_000,
        "expected_band": "RESTORE_ELIGIBLE",
        "expected_target_units": 4,
        "expected_reason_code": "RECOVERY_READD_ONE_TIER",
    },
    {
        "scenario_id": "hard_stop_override",
        "purpose": "Hard stop overrides a strong score",
        "slope_nano_usd": 122_400_000,
        "carrier_id": "LC0",
        "original_units": 4,
        "current_units": 4,
        "override_status": "HARD_STOP",
        "expected_action": "EXIT_DUE",
        "expected_display_score_bp": 8_500,
        "expected_band": "RESTORE_ELIGIBLE",
        "expected_target_units": 0,
        "expected_reason_code": "HARD_STOP",
    },
    {
        "scenario_id": "integer_no_op",
        "purpose": "One complete unit cannot be split by a defensive tier",
        "slope_nano_usd": -500_000_000,
        "carrier_id": "LC0",
        "original_units": 1,
        "current_units": 1,
        "below_40_streak": 1,
        "below_20_streak": 1,
        "expected_action": "HOLD",
        "expected_display_score_bp": 851,
        "expected_band": "DEFENSIVE_2",
        "expected_target_units": 1,
        "expected_reason_code": "NO_COMPLETE_UNIT_REDUCTION_AVAILABLE",
    },
    {
        "scenario_id": "strong_but_stale",
        "purpose": "A stale quote blocks an otherwise eligible recovery add",
        "slope_nano_usd": 54_400_000,
        "carrier_id": "LC0",
        "original_units": 4,
        "current_units": 2,
        "above_70_streak": 1,
        "quote_age_ns": 6_000_000_000,
        "expected_action": "ACTION_BLOCKED",
        "expected_display_score_bp": 8_000,
        "expected_band": "RESTORE_ELIGIBLE",
        "expected_target_units": 2,
        "expected_reason_code": "QUOTE_STALE",
    },
    {
        "scenario_id": "latched_episode_no_reopen",
        "purpose": "A terminally latched episode at zero units never reopens",
        "slope_nano_usd": 54_400_000,
        "carrier_id": "LC0",
        "original_units": 4,
        "current_units": 0,
        "exit_latch_status": "EXIT_DUE_LATCHED",
        "expected_action": "HOLD",
        "expected_display_score_bp": 8_000,
        "expected_band": "RESTORE_ELIGIBLE",
        "expected_target_units": 0,
        "expected_reason_code": "EXIT_DUE_LATCHED_NO_POSITION",
    },
    {
        "scenario_id": "bcs_defensive_day_2",
        "purpose": "Confirmed BCS weakness reduces only one complete 1:1 unit",
        "slope_nano_usd": -10_000,
        "carrier_id": "BCS0",
        "original_units": 4,
        "current_units": 4,
        "below_40_streak": 1,
        "expected_action": "REDUCE",
        "expected_display_score_bp": 3_000,
        "expected_band": "DEFENSIVE_1",
        "expected_target_units": 3,
        "expected_reason_code": "CONFIRMED_DEFENSIVE_REDUCTION",
    },
    {
        "scenario_id": "bcs_residual_leg",
        "purpose": "BCS residual-leg reconciliation blocks a new action",
        "slope_nano_usd": -500_000_000,
        "carrier_id": "BCS0",
        "original_units": 4,
        "current_units": 4,
        "below_40_streak": 1,
        "below_20_streak": 1,
        "reconciliation_status": "RESIDUAL_LEG",
        "expected_action": "RECONCILIATION_BLOCKED",
        "expected_display_score_bp": 851,
        "expected_band": "DEFENSIVE_2",
        "expected_target_units": 4,
        "expected_reason_code": "RESIDUAL_LEG",
    },
    {
        "scenario_id": "missing_dimension",
        "purpose": (
            "A non-normal current session leaves required dimensions "
            "not evaluable and produces no action"
        ),
        "slope_nano_usd": 0,
        "carrier_id": "LC0",
        "original_units": 4,
        "current_units": 4,
        "current_session_kind": "EARLY_CLOSE",
        "expected_action": "NO_DECISION",
        "expected_display_score_bp": None,
        "expected_band": "NOT_EVALUABLE",
        "expected_target_units": 4,
        "expected_reason_code": "PRIMARY_SCORE_NOT_EVALUABLE",
    },
)


def _scenario(scenario_id: str) -> dict[str, object]:
    for scenario in ACCEPTANCE_SCENARIOS_F0:
        if scenario["scenario_id"] == scenario_id:
            return scenario
    raise ManagementResearchError("SYNTHETIC_SCENARIO_UNKNOWN", scenario_id)


def _session_dates_ending(end_date: date, count: int) -> list[date]:
    values = [end_date]
    while len(values) < count:
        values.append(previous_session_date(values[-1]))
    values.reverse()
    return values


def _contracts(carrier_id: str) -> list[dict[str, object]]:
    result = [
        {
            "contract_id": "GLD-20261218-C-200",
            "role": "LONG_CALL",
            "expiry_date": "2026-12-18",
            "strike_nano_usd": 200_000_000_000,
            "multiplier": 100,
            "deliverable_shares": 100,
            "currency": "USD",
        }
    ]
    if carrier_id == "BCS0":
        result.append(
            {
                "contract_id": "GLD-20261218-C-220",
                "role": "SHORT_CALL",
                "expiry_date": "2026-12-18",
                "strike_nano_usd": 220_000_000_000,
                "multiplier": 100,
                "deliverable_shares": 100,
                "currency": "USD",
            }
        )
    return result


def _raw_bars(slope: int, end_date: date) -> list[dict[str, object]]:
    dates = _session_dates_ending(end_date, 220)
    bars: list[dict[str, object]] = []
    for ordinal, session_date in enumerate(dates, start=1):
        close = BASE_PRICE_NANO_USD + (ordinal - 1) * slope
        bars.append(
            {
                "session_date": session_date.isoformat(),
                "session_ordinal": ordinal,
                "session_kind": session_kind(session_date),
                "session_status": "COMPLETE",
                "official_close_utc_ns": official_close_utc_ns(session_date),
                "open_nano_usd": close,
                "high_nano_usd": close + 1_000_000_000,
                "low_nano_usd": close - 1_000_000_000,
                "close_nano_usd": close,
                "volume_shares": 1_000_000,
            }
        )
    return bars


def _observation_document(
    *,
    bars: list[dict[str, object]],
    episode: dict[str, object],
    previous_state: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": "GLD_MANAGEMENT_SCORE_OBSERVATION_F0_V1",
        "classification": "SYNTHETIC_ONLY",
        "instrument_id": "GLD",
        "calendar_id": CALENDAR_ID,
        "calendar_version_sha256": CALENDAR_VERSION_SHA256,
        "formula_catalog_version": "GLD_MANAGEMENT_FORMULAS_F0_V1",
        "policy_set_version": "GLD_MANAGEMENT_POLICY_SET_F0_V1",
        "trading_date": bars[-1]["session_date"],
        "observation_utc_ns": int(bars[-1]["official_close_utc_ns"]) + 1,
        "daily_bars": bars,
        "episode": episode,
        "previous_state": previous_state,
    }


def _genesis_observation(
    *,
    scenario: dict[str, object],
    episode_id: str,
    end_date: date,
) -> dict[str, object]:
    bars = _raw_bars(int(scenario.get("slope_nano_usd", 0)), end_date)
    carrier_id = str(scenario["carrier_id"])
    episode = {
        "episode_id": episode_id,
        "carrier_id": carrier_id,
        "management_start_date": bars[-2]["session_date"],
        "entry_breakout_line_nano_usd": BASE_PRICE_NANO_USD,
        "entry_breakout_source_sha256": canonical_json_sha256(
            {
                "episode_id": episode_id,
                "entry_breakout_line_nano_usd": BASE_PRICE_NANO_USD,
            }
        ),
        "original_approved_units": int(scenario["original_units"]),
        "contracts": _contracts(carrier_id),
    }
    state = derive_genesis_management_state_f0(
        episode, str(bars[-2]["session_date"])
    )
    return _observation_document(bars=bars, episode=episode, previous_state=state)


def build_synthetic_observation_f0(scenario_id: str) -> dict[str, object]:
    """Build one deterministic raw observation from a registered scenario."""

    scenario = _scenario(scenario_id)
    slope = int(scenario.get("slope_nano_usd", 0))
    end_date = (
        date(2025, 11, 28)
        if scenario.get("current_session_kind") == "EARLY_CLOSE"
        else date(2025, 12, 3)
    )
    bars = _raw_bars(slope, end_date)
    episode_id = f"EPISODE-{scenario_id.upper().replace('_', '-')}"
    requires_prior = any(
        int(scenario.get(name, 0))
        for name in ("below_40_streak", "below_20_streak", "above_70_streak")
    ) or scenario.get("exit_latch_status") == "EXIT_DUE_LATCHED"
    if not requires_prior:
        return _genesis_observation(
            scenario=scenario, episode_id=episode_id, end_date=end_date
        )

    prior = _genesis_observation(
        scenario=scenario,
        episode_id=episode_id,
        end_date=date.fromisoformat(str(bars[-2]["session_date"])),
    )
    from .action import evaluate_hypothetical_management_action_f0
    from .scoring import derive_management_score_f0
    from .validation import validate_management_score_observation_f0

    prior_score = derive_management_score_f0(
        validate_management_score_observation_f0(prior)
    )
    if scenario.get("exit_latch_status") == "EXIT_DUE_LATCHED":
        prior_action_document: dict[str, object] = {
            "schema_version": "GLD_MANAGEMENT_TERMINAL_OVERRIDE_F0_V1",
            "classification": "SYNTHETIC_ONLY",
            "instrument_id": "GLD",
            "event_utc_ns": int(prior["observation_utc_ns"]) + 1,
            "episode_id": prior["episode"]["episode_id"],
            "carrier_id": prior["episode"]["carrier_id"],
            "contracts": prior["episode"]["contracts"],
            "current_units": int(scenario["original_units"]),
            "override_status": "HARD_STOP",
        }
    else:
        prior_action_document = build_synthetic_action_snapshot_f0(
            prior, scenario_id
        )
    prior_action = evaluate_hypothetical_management_action_f0(
        prior_score, prior_action_document
    )
    state = derive_management_state_transition_f0(
        prior_score.as_dict(), prior_action.as_dict()
    )
    expected_streaks = (
        int(scenario.get("below_40_streak", 0)),
        int(scenario.get("below_20_streak", 0)),
        int(scenario.get("above_70_streak", 0)),
    )
    if scenario.get("exit_latch_status") != "EXIT_DUE_LATCHED" and (
        state["below_40_streak"],
        state["below_20_streak"],
        state["above_70_streak"],
    ) != expected_streaks:
        raise ManagementResearchError("SYNTHETIC_PRIOR_STATE_MISMATCH")
    return _observation_document(
        bars=bars,
        episode=prior["episode"],
        previous_state=state,
    )


def build_synthetic_action_snapshot_f0(
    observation: dict[str, object],
    scenario_id: str,
) -> dict[str, object]:
    """Build the bound next-session 10:45 research action snapshot."""

    scenario = _scenario(scenario_id)
    episode = observation.get("episode")
    if type(episode) is not dict or type(episode.get("contracts")) is not list:
        raise ManagementResearchError("SYNTHETIC_OBSERVATION_EPISODE_INVALID")
    contracts = [dict(value) for value in episode["contracts"]]
    override_status = str(scenario.get("override_status", "NONE"))
    if override_status in {
        "HARD_STOP",
        "EXPIRY_SAFETY",
        "CONFIRMED_INVALIDATION",
    }:
        return {
            "schema_version": "GLD_MANAGEMENT_TERMINAL_OVERRIDE_F0_V1",
            "classification": "SYNTHETIC_ONLY",
            "instrument_id": "GLD",
            "event_utc_ns": int(observation["observation_utc_ns"]) + 1,
            "episode_id": episode["episode_id"],
            "carrier_id": episode["carrier_id"],
            "contracts": contracts,
            "current_units": int(scenario["current_units"]),
            "override_status": override_status,
        }
    quote_age_ns = int(scenario.get("quote_age_ns", 1_000_000_000))
    action_date = next_session_date(str(observation["trading_date"]))
    action_ns = action_1045_utc_ns(action_date)
    quotes = [
        {
            "contract_id": contract["contract_id"],
            "bid_nano_usd": 5_000_000_000,
            "ask_nano_usd": 5_100_000_000,
            "bid_size": 100,
            "ask_size": 100,
            "tick_nano_usd": 10_000_000,
            "event_utc_ns": action_ns - quote_age_ns,
            "receive_utc_ns": action_ns - quote_age_ns,
        }
        for contract in contracts
    ]
    expiry_date = date.fromisoformat(str(contracts[0]["expiry_date"]))
    return {
        "schema_version": "GLD_MANAGEMENT_ACTION_SNAPSHOT_F0_V1",
        "classification": "SYNTHETIC_ONLY",
        "instrument_id": "GLD",
        "action_trading_date": action_date.isoformat(),
        "action_utc_ns": action_ns,
        "episode_id": episode["episode_id"],
        "carrier_id": episode["carrier_id"],
        "contracts": contracts,
        "current_units": int(scenario["current_units"]),
        "account_risk_cap_units": 10,
        "liquidity_cap_units": 10,
        "external_risk_cap_units": 10,
        "dte": (expiry_date - action_date).days,
        "fee_facts_status": "COMPLETE",
        "reconciliation_status": str(
            scenario.get("reconciliation_status", "MATCHED")
        ),
        "override_status": override_status,
        "quotes": quotes,
    }


def acceptance_scenario_spec_f0(scenario_id: str) -> dict[str, object]:
    """Return a copy of the pre-registered expected acceptance behavior."""

    return dict(_scenario(scenario_id))


__all__ = [
    "ACCEPTANCE_SCENARIOS_F0",
    "acceptance_scenario_spec_f0",
    "build_synthetic_action_snapshot_f0",
    "build_synthetic_observation_f0",
]
