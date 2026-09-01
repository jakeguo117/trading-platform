from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date
import unittest
from unittest.mock import patch

import gld_management_research.action as management_action_module
import gld_management_research.scoring as management_scoring_module

from gld_management_research import (
    ManagementResearchError,
    derive_management_score_f0,
    evaluate_hypothetical_management_action_f0,
    validate_management_score_observation_f0,
)
from gld_management_research.indicators import derive_local_indicators_f0
from gld_management_research.math import ceil_fraction, round_half_even_div
from gld_management_research.canonical import (
    canonical_json_bytes as management_canonical_json_bytes,
    canonical_json_sha256 as management_canonical_json_sha256,
)
from gld_management_research.indicators import FORMULA_CATALOG_SHA256
from gld_management_research.lineage import (
    derive_genesis_management_state_f0,
    derive_management_state_transition_f0,
)
from gld_management_research.policy import (
    POLICY_SET_SHA256,
    action_policy_f0,
    policy_set_document_f0,
)
from gld_management_research.scoring import _band
from gld_management_research.validation import (
    validate_management_action_snapshot_f0,
)
from gld_management_research.xnys_calendar import (
    CALENDAR_ID,
    CALENDAR_VERSION_SHA256,
    action_1045_utc_ns,
    next_session_date,
    official_close_utc_ns,
    previous_session_date,
    session_kind,
)
from gld_simulation.canonical import canonical_json_bytes


PRICE = 200_000_000_000


def _session_dates_ending(end_date: date, count: int) -> list[date]:
    values = [end_date]
    while len(values) < count:
        values.append(previous_session_date(values[-1]))
    values.reverse()
    return values


def _bars(
    *,
    slope_nano_usd: int = 0,
    end_date: date = date(2025, 12, 3),
) -> list[dict[str, object]]:
    dates = _session_dates_ending(end_date, 220)
    result: list[dict[str, object]] = []
    for ordinal, session_date in enumerate(dates, start=1):
        close = PRICE + (ordinal - 1) * slope_nano_usd
        result.append(
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
    return result


def _contracts(carrier_id: str = "LC0") -> list[dict[str, object]]:
    values = [
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
        values.append(
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
    return values


def _observation(
    *,
    slope_nano_usd: int = 0,
    carrier_id: str = "LC0",
    original_units: int = 4,
    below_40_streak: int = 0,
    below_20_streak: int = 0,
    above_70_streak: int = 0,
    exit_latch_status: str = "CLEAR",
    end_date: date = date(2025, 12, 3),
) -> dict[str, object]:
    bars = _bars(slope_nano_usd=slope_nano_usd, end_date=end_date)
    episode_id = "EPISODE-F0-001"
    requires_prior = any(
        (below_40_streak, below_20_streak, above_70_streak)
    ) or exit_latch_status == "EXIT_DUE_LATCHED"
    if requires_prior:
        prior = _observation(
            slope_nano_usd=slope_nano_usd,
            carrier_id=carrier_id,
            original_units=original_units,
            end_date=date.fromisoformat(str(bars[-2]["session_date"])),
        )
        episode = deepcopy(prior["episode"])
        prior_score = derive_management_score_f0(
            validate_management_score_observation_f0(prior)
        )
        prior_action = evaluate_hypothetical_management_action_f0(
            prior_score,
            (
                _terminal_override(prior, current_units=original_units)
                if exit_latch_status == "EXIT_DUE_LATCHED"
                else _action_snapshot(
                    prior,
                    current_units=min(original_units, max(1, original_units // 2)),
                )
            ),
        )
        previous_state = derive_management_state_transition_f0(
            prior_score.as_dict(), prior_action.as_dict()
        )
        if exit_latch_status == "CLEAR" and (
            previous_state["below_40_streak"],
            previous_state["below_20_streak"],
            previous_state["above_70_streak"],
        ) != (below_40_streak, below_20_streak, above_70_streak):
            raise AssertionError("fixture streak request does not match prior score")
    else:
        episode = {
            "episode_id": episode_id,
            "carrier_id": carrier_id,
            "management_start_date": bars[-2]["session_date"],
            "entry_breakout_line_nano_usd": PRICE,
            "entry_breakout_source_sha256": management_canonical_json_sha256(
                {
                    "episode_id": episode_id,
                    "entry_breakout_line_nano_usd": PRICE,
                }
            ),
            "original_approved_units": original_units,
            "contracts": _contracts(carrier_id),
        }
        previous_state = derive_genesis_management_state_f0(
            episode, str(bars[-2]["session_date"])
        )
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


def _accelerating_observation(direction: int) -> dict[str, object]:
    document = _observation()
    bars = document["daily_bars"]
    assert isinstance(bars, list)
    for index, bar in enumerate(bars):
        assert isinstance(bar, dict)
        close = PRICE + direction * index * index * 2_000_000
        bar["open_nano_usd"] = close
        bar["high_nano_usd"] = close + 1_000_000_000
        bar["low_nano_usd"] = close - 1_000_000_000
        bar["close_nano_usd"] = close
    return document


def _action_snapshot(
    observation: dict[str, object],
    *,
    current_units: int = 4,
    override_status: str = "NONE",
    quote_age_ns: int = 1_000_000_000,
    reconciliation_status: str = "MATCHED",
    account_risk_cap_units: int | None = 10,
    liquidity_cap_units: int | None = 10,
    external_risk_cap_units: int | None = 10,
) -> dict[str, object]:
    episode = observation["episode"]
    assert isinstance(episode, dict)
    contracts = deepcopy(episode["contracts"])
    action_date = next_session_date(str(observation["trading_date"]))
    action_utc_ns = action_1045_utc_ns(action_date)
    quotes: list[dict[str, object]] = []
    assert isinstance(contracts, list)
    for item in contracts:
        assert isinstance(item, dict)
        quotes.append(
            {
                "contract_id": item["contract_id"],
                "bid_nano_usd": 5_000_000_000,
                "ask_nano_usd": 5_100_000_000,
                "bid_size": 100,
                "ask_size": 100,
                "tick_nano_usd": 10_000_000,
                "event_utc_ns": action_utc_ns - quote_age_ns,
                "receive_utc_ns": action_utc_ns - quote_age_ns,
            }
        )
    expiry_date = min(
        date.fromisoformat(str(item["expiry_date"])) for item in contracts
    )
    return {
        "schema_version": "GLD_MANAGEMENT_ACTION_SNAPSHOT_F0_V1",
        "classification": "SYNTHETIC_ONLY",
        "instrument_id": "GLD",
        "action_trading_date": action_date.isoformat(),
        "action_utc_ns": action_utc_ns,
        "episode_id": episode["episode_id"],
        "carrier_id": episode["carrier_id"],
        "contracts": contracts,
        "current_units": current_units,
        "account_risk_cap_units": account_risk_cap_units,
        "liquidity_cap_units": liquidity_cap_units,
        "external_risk_cap_units": external_risk_cap_units,
        "dte": (expiry_date - action_date).days,
        "fee_facts_status": "COMPLETE",
        "reconciliation_status": reconciliation_status,
        "override_status": override_status,
        "quotes": quotes,
    }


def _terminal_override(
    observation: dict[str, object],
    *,
    override_status: str = "HARD_STOP",
    current_units: int = 4,
) -> dict[str, object]:
    episode = observation["episode"]
    assert isinstance(episode, dict)
    return {
        "schema_version": "GLD_MANAGEMENT_TERMINAL_OVERRIDE_F0_V1",
        "classification": "SYNTHETIC_ONLY",
        "instrument_id": "GLD",
        "event_utc_ns": int(observation["observation_utc_ns"]) + 1,
        "episode_id": episode["episode_id"],
        "carrier_id": episode["carrier_id"],
        "contracts": deepcopy(episode["contracts"]),
        "current_units": current_units,
        "override_status": override_status,
    }


class ManagementMathAndIndicatorTests(unittest.TestCase):
    def test_package_local_canonical_matches_existing_golden_encoding(self) -> None:
        corpus = [
            {},
            {"z": 1, "a": [True, None, "GLD", -7]},
            {"unicode": "黄金", "nested": {"ppm": 1_000_000}},
        ]
        for value in corpus:
            with self.subTest(value=value):
                self.assertEqual(
                    management_canonical_json_bytes(value),
                    canonical_json_bytes(value),
                )

    def test_signed_half_even_division(self) -> None:
        self.assertEqual(round_half_even_div(3, 2), 2)
        self.assertEqual(round_half_even_div(5, 2), 2)
        self.assertEqual(round_half_even_div(-3, 2), -2)
        self.assertEqual(round_half_even_div(-5, 2), -2)

    def test_flat_bars_produce_neutral_indicators(self) -> None:
        validated = validate_management_score_observation_f0(_observation())
        indicators = derive_local_indicators_f0(validated.daily_bars)
        self.assertEqual(indicators.sma50_nano_usd, PRICE)
        self.assertEqual(indicators.atr14_nano_usd, 2_000_000_000)
        self.assertEqual(indicators.rsi14_ppm, 500_000)
        self.assertEqual(indicators.macd_histogram_nano_usd, 0)
        self.assertEqual(indicators.plus_di14_ppm, 0)
        self.assertEqual(indicators.minus_di14_ppm, 0)
        self.assertEqual(indicators.adx14_ppm, 0)

    def test_rising_and_falling_bars_bind_direction(self) -> None:
        rising = validate_management_score_observation_f0(
            _accelerating_observation(1)
        )
        falling = validate_management_score_observation_f0(
            _accelerating_observation(-1)
        )
        up = derive_local_indicators_f0(rising.daily_bars)
        down = derive_local_indicators_f0(falling.daily_bars)
        self.assertEqual(up.rsi14_ppm, 1_000_000)
        self.assertEqual(down.rsi14_ppm, 0)
        self.assertGreater(up.plus_di14_ppm, up.minus_di14_ppm)
        self.assertGreater(down.minus_di14_ppm, down.plus_di14_ppm)
        self.assertGreater(up.macd_histogram_nano_usd, 0)
        self.assertLess(down.macd_histogram_nano_usd, 0)

    def test_nontrivial_indicator_golden_vectors_lock_seed_and_rounding(self) -> None:
        rising = derive_local_indicators_f0(
            validate_management_score_observation_f0(
                _accelerating_observation(1)
            ).daily_bars
        )
        self.assertEqual(
            rising.values_dict(),
            {
                "sma50_nano_usd": 276_077_000_000,
                "atr14_nano_usd": 2_000_000_000,
                "rsi14_ppm": 1_000_000,
                "macd_line_nano_usd": 5_614_000_071,
                "macd_signal_nano_usd": 5_502_000_105,
                "macd_histogram_nano_usd": 111_999_966,
                "plus_di14_ppm": 411_000,
                "minus_di14_ppm": 0,
                "adx14_ppm": 1_000_000,
                "volume_median20_shares": 1_000_000,
            },
        )
        gapped = _observation()
        current = gapped["daily_bars"][-1]
        current["open_nano_usd"] = PRICE + 10_000_000_000
        current["high_nano_usd"] = PRICE + 11_000_000_000
        current["low_nano_usd"] = PRICE + 9_000_000_000
        current["close_nano_usd"] = PRICE + 10_000_000_000
        gap_indicators = derive_local_indicators_f0(
            validate_management_score_observation_f0(gapped).daily_bars
        )
        self.assertEqual(gap_indicators.atr14_nano_usd, 2_642_857_143)


class ManagementObservationAndScoreTests(unittest.TestCase):
    def test_closed_schema_sorts_bars_and_hashes_canonically(self) -> None:
        document = _observation()
        document["daily_bars"] = list(reversed(document["daily_bars"]))
        validated = validate_management_score_observation_f0(document)
        self.assertEqual(validated.daily_bars[0].session_ordinal, 1)
        self.assertEqual(validated.daily_bars[-1].session_ordinal, 220)
        self.assertEqual(
            canonical_json_bytes(validated.as_dict()),
            validated.canonical_bytes,
        )

    def test_unknown_float_duplicate_and_future_data_fail_closed(self) -> None:
        cases: list[tuple[dict[str, object], str]] = []
        unknown = _observation()
        unknown["winner"] = "LC0"
        cases.append((unknown, "OBSERVATION_SCHEMA_INVALID"))
        floating = _observation()
        floating["observation_utc_ns"] = 1.5
        cases.append((floating, "CANONICAL_JSON_FLOAT_FORBIDDEN"))
        duplicate = _observation()
        duplicate["daily_bars"][1]["session_ordinal"] = 1
        cases.append((duplicate, "DAILY_BAR_SESSION_SEQUENCE_INVALID"))
        future = _observation()
        future["observation_utc_ns"] = future["daily_bars"][-1][
            "official_close_utc_ns"
        ] - 1
        cases.append((future, "DAILY_BAR_FUTURE_DATA"))
        for document, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(ManagementResearchError) as caught:
                    validate_management_score_observation_f0(document)
                self.assertEqual(caught.exception.reason_code, reason)

    def test_flat_market_is_score_50_hold_band_and_byte_stable(self) -> None:
        document = _observation()
        first = derive_management_score_f0(
            validate_management_score_observation_f0(document)
        )
        second = derive_management_score_f0(
            validate_management_score_observation_f0(deepcopy(document))
        )
        self.assertEqual(first.score_status, "EVALUATED")
        self.assertEqual(first.primary.net_signal_ppm, 0)
        self.assertEqual(first.primary.display_score_bp, 5_000)
        self.assertEqual(first.primary.band, "HOLD")
        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        self.assertEqual(first.result_sha256, second.result_sha256)
        self.assertEqual(len(first.policy_results), 4)
        self.assertTrue(all(not item.action_authority for item in first.policy_results[1:]))

    def test_policy_catalog_is_detached_and_cannot_change_live_results(self) -> None:
        observation = validate_management_score_observation_f0(
            _accelerating_observation(1)
        )
        before = derive_management_score_f0(observation)
        detached = policy_set_document_f0()
        detached["score_policies"][0]["weights_ppm"] = [0, 0, 1_000_000]
        after = derive_management_score_f0(observation)
        self.assertEqual(before.canonical_bytes, after.canonical_bytes)
        self.assertEqual(before.policy_set_sha256, POLICY_SET_SHA256)

    def test_replaced_policy_accessor_fails_before_score_or_action(self) -> None:
        validated = validate_management_score_observation_f0(
            _accelerating_observation(1)
        )
        with patch.object(
            management_scoring_module,
            "score_policy_definitions_f0",
            lambda: (),
        ):
            with self.assertRaises(ManagementResearchError) as caught_score:
                derive_management_score_f0(validated)
        self.assertEqual(
            caught_score.exception.reason_code,
            "POLICY_RUNTIME_IDENTITY_MISMATCH",
        )

        score = derive_management_score_f0(validated)
        raw = _accelerating_observation(1)
        with patch.object(
            management_action_module,
            "action_policy_f0",
            lambda: object(),
        ):
            with self.assertRaises(ManagementResearchError) as caught_action:
                evaluate_hypothetical_management_action_f0(
                    score, _action_snapshot(raw)
                )
        self.assertEqual(
            caught_action.exception.reason_code,
            "POLICY_RUNTIME_IDENTITY_MISMATCH",
        )

    def test_nontrivial_dimension_and_all_policy_golden_values(self) -> None:
        result = derive_management_score_f0(
            validate_management_score_observation_f0(
                _accelerating_observation(1)
            )
        )
        dimensions = result.dimensions
        self.assertIsNotNone(dimensions)
        self.assertEqual(
            (
                dimensions.structure_base_ppm,
                dimensions.trend_direction_balance_ppm,
                dimensions.trend_strength_ppm,
                dimensions.trend_ppm,
                dimensions.rsi_component_ppm,
                dimensions.macd_component_ppm,
                dimensions.momentum_gross_strength_ppm,
                dimensions.momentum_agreement_ppm,
                dimensions.momentum_ppm,
            ),
            (
                908_446,
                1_000_000,
                1_000_000,
                1_000_000,
                1_000_000,
                53_030,
                526_515,
                1_000_000,
                526_515,
            ),
        )
        self.assertEqual(
            [
                (
                    item.policy_id,
                    item.net_signal_ppm,
                    item.gross_strength_ppm,
                    item.agreement_ppm,
                    item.display_score_bp,
                )
                for item in result.policy_results
            ],
            [
                ("PRIMARY_F0", 859_526, 859_526, 1_000_000, 9_298),
                (
                    "CONTROL_EQUAL_WITH_VOLUME",
                    811_654,
                    811_654,
                    1_000_000,
                    9_058,
                ),
                ("ABLATION_ROLE_NO_VOLUME", 859_526, 859_526, 1_000_000, 9_298),
                ("ABLATION_EQUAL_NO_VOLUME", 811_654, 811_654, 1_000_000, 9_058),
            ],
        )

    def test_breakout_diagnostic_has_no_policy_or_action_authority(self) -> None:
        base = _observation()
        changed = deepcopy(base)
        changed_line = PRICE + 25_000_000_000
        changed["episode"]["entry_breakout_line_nano_usd"] = changed_line
        changed["episode"]["entry_breakout_source_sha256"] = (
            management_canonical_json_sha256(
                {
                    "episode_id": changed["episode"]["episode_id"],
                    "entry_breakout_line_nano_usd": changed_line,
                }
            )
        )
        changed["previous_state"] = derive_genesis_management_state_f0(
            changed["episode"],
            str(changed["daily_bars"][-2]["session_date"]),
        )
        base_score = derive_management_score_f0(
            validate_management_score_observation_f0(base)
        )
        changed_score = derive_management_score_f0(
            validate_management_score_observation_f0(changed)
        )
        self.assertEqual(
            [item.as_dict() for item in base_score.policy_results],
            [item.as_dict() for item in changed_score.policy_results],
        )
        self.assertEqual(
            base_score.dimensions.as_dict(), changed_score.dimensions.as_dict()
        )
        self.assertNotEqual(
            base_score.breakout_diagnostic.diagnostic_sha256,
            changed_score.breakout_diagnostic.diagnostic_sha256,
        )
        self.assertEqual(
            changed_score.breakout_diagnostic.authority_status,
            "NO_ACTION_AUTHORITY",
        )
        base_action = evaluate_hypothetical_management_action_f0(
            base_score, _action_snapshot(base)
        )
        changed_action = evaluate_hypothetical_management_action_f0(
            changed_score, _action_snapshot(changed)
        )
        self.assertEqual(
            (
                base_action.action,
                base_action.target_units,
                base_action.reason_code,
            ),
            (
                changed_action.action,
                changed_action.target_units,
                changed_action.reason_code,
            ),
        )

    def test_previous_state_lineage_and_streak_conflicts_fail_closed(self) -> None:
        conflict = _observation(
            slope_nano_usd=500_000_000,
            above_70_streak=1,
        )
        state = conflict["previous_state"]
        state["below_40_streak"] = 1
        unsigned = dict(state)
        unsigned.pop("state_sha256")
        state["state_sha256"] = management_canonical_json_sha256(unsigned)
        with self.assertRaises(ManagementResearchError) as caught:
            validate_management_score_observation_f0(conflict)
        self.assertEqual(
            caught.exception.reason_code, "PREVIOUS_STATE_LINEAGE_MISMATCH"
        )

        wrong_episode = _observation()
        wrong_episode["previous_state"]["episode_id"] = "EPISODE-OTHER"
        unsigned = dict(wrong_episode["previous_state"])
        unsigned.pop("state_sha256")
        wrong_episode["previous_state"]["state_sha256"] = (
            management_canonical_json_sha256(unsigned)
        )
        with self.assertRaises(ManagementResearchError) as caught_episode:
            validate_management_score_observation_f0(wrong_episode)
        self.assertEqual(
            caught_episode.exception.reason_code,
            "PREVIOUS_STATE_LINEAGE_MISMATCH",
        )

        forged_hash = _observation(
            slope_nano_usd=500_000_000,
            above_70_streak=1,
        )
        forged_hash["previous_state"]["previous_score_result_sha256"] = "a" * 64
        unsigned = dict(forged_hash["previous_state"])
        unsigned.pop("state_sha256")
        forged_hash["previous_state"]["state_sha256"] = (
            management_canonical_json_sha256(unsigned)
        )
        with self.assertRaises(ManagementResearchError) as caught_hash:
            validate_management_score_observation_f0(forged_hash)
        self.assertEqual(
            caught_hash.exception.reason_code,
            "PREVIOUS_STATE_LINEAGE_MISMATCH",
        )

    def test_exact_score_boundaries_have_stable_band_membership(self) -> None:
        self.assertEqual(_band(400_000), "RESTORE_ELIGIBLE")
        self.assertEqual(_band(399_999), "HOLD")
        self.assertEqual(_band(-200_000), "HOLD")
        self.assertEqual(_band(-200_001), "DEFENSIVE_1")
        self.assertEqual(_band(-600_000), "DEFENSIVE_1")
        self.assertEqual(_band(-600_001), "DEFENSIVE_2")

    def test_forged_typed_observation_is_rejected(self) -> None:
        validated = validate_management_score_observation_f0(_observation())
        forged = replace(validated, input_sha256="0" * 64)
        with self.assertRaises(ManagementResearchError) as caught:
            derive_management_score_f0(forged)
        self.assertEqual(
            caught.exception.reason_code,
            "OBSERVATION_TYPED_SEAL_INVALID",
        )

    def test_current_early_close_is_not_evaluable_and_resets_streaks(self) -> None:
        document = _observation(
            slope_nano_usd=-500_000_000,
            below_40_streak=1,
            below_20_streak=1,
            end_date=date(2025, 11, 28),
        )
        result = derive_management_score_f0(
            validate_management_score_observation_f0(document)
        )
        self.assertEqual(result.score_status, "NOT_EVALUABLE")
        self.assertIn("CURRENT_SESSION_NOT_NORMAL", result.reason_codes)
        self.assertEqual(result.updated_state.below_40_streak, 0)
        self.assertEqual(result.updated_state.below_20_streak, 0)
        self.assertEqual(result.updated_state.above_70_streak, 0)

    def test_weekend_preserves_streak_but_early_close_resets_it(self) -> None:
        weekend = _observation(
            slope_nano_usd=500_000_000,
            above_70_streak=1,
            end_date=date(2025, 12, 8),
        )
        weekend_score = derive_management_score_f0(
            validate_management_score_observation_f0(weekend)
        )
        self.assertEqual(
            weekend["previous_state"]["previous_trading_date"],
            "2025-12-05",
        )
        self.assertEqual(weekend_score.trading_date, "2025-12-08")
        self.assertEqual(weekend_score.updated_state.above_70_streak, 2)

        early = _observation(
            slope_nano_usd=500_000_000,
            end_date=date(2025, 11, 28),
        )
        early_score = derive_management_score_f0(
            validate_management_score_observation_f0(early)
        )
        self.assertEqual(early_score.score_status, "NOT_EVALUABLE")
        early_action = evaluate_hypothetical_management_action_f0(
            early_score,
            _action_snapshot(early, current_units=2),
        )
        monday = _observation(
            slope_nano_usd=500_000_000,
            end_date=date(2025, 12, 1),
        )
        monday["episode"] = deepcopy(early["episode"])
        monday["previous_state"] = derive_management_state_transition_f0(
            early_score.as_dict(), early_action.as_dict()
        )
        monday_score = derive_management_score_f0(
            validate_management_score_observation_f0(monday)
        )
        self.assertEqual(monday_score.updated_state.above_70_streak, 1)

    def test_missing_required_indicator_never_falls_back_to_partial_score(self) -> None:
        validated = validate_management_score_observation_f0(_observation())
        with patch(
            "gld_management_research.scoring.derive_local_indicators_f0",
            side_effect=ManagementResearchError("MACD_SIGNAL_NOT_EVALUABLE"),
        ):
            result = derive_management_score_f0(validated)
        self.assertEqual(result.score_status, "NOT_EVALUABLE")
        self.assertEqual(result.primary.band, "NOT_EVALUABLE")
        self.assertIsNone(result.primary.net_signal_ppm)
        self.assertEqual(
            result.reason_codes, ("MACD_SIGNAL_NOT_EVALUABLE",)
        )

    def test_pinned_calendar_binds_holidays_close_clock_and_action_1045(self) -> None:
        document = _observation(end_date=date(2025, 11, 26))
        session_dates = {
            item["session_date"] for item in document["daily_bars"]
        }
        self.assertNotIn("2025-01-20", session_dates)
        self.assertNotIn("2025-04-18", session_dates)
        self.assertNotIn("2025-07-04", session_dates)
        snapshot = _action_snapshot(document)
        self.assertEqual(snapshot["action_trading_date"], "2025-11-28")
        self.assertEqual(
            snapshot["action_utc_ns"],
            action_1045_utc_ns(date(2025, 11, 28)),
        )

        wrong_close = _observation()
        wrong_close["daily_bars"][-1]["official_close_utc_ns"] += 1
        with self.assertRaises(ManagementResearchError) as caught_close:
            validate_management_score_observation_f0(wrong_close)
        self.assertEqual(
            caught_close.exception.reason_code,
            "DAILY_BAR_OFFICIAL_CLOSE_MISMATCH",
        )

        wrong_action = _action_snapshot(document)
        wrong_action["action_utc_ns"] += 1
        with self.assertRaises(ManagementResearchError) as caught_action:
            evaluate_hypothetical_management_action_f0(
                derive_management_score_f0(
                    validate_management_score_observation_f0(document)
                ),
                wrong_action,
            )
        self.assertEqual(
            caught_action.exception.reason_code, "ACTION_1045_TIME_INVALID"
        )

    def test_volume_only_scales_structure_when_above_reference(self) -> None:
        low_volume = _observation(slope_nano_usd=500_000_000)
        low_volume["daily_bars"][-1]["volume_shares"] = 500_000
        low_score = derive_management_score_f0(
            validate_management_score_observation_f0(low_volume)
        )
        self.assertEqual(low_score.dimensions.volume_quality_ppm, 1_000_000)

        aligned = _observation(slope_nano_usd=500_000_000)
        aligned["daily_bars"][-1]["volume_shares"] = 3_000_000
        aligned_score = derive_management_score_f0(
            validate_management_score_observation_f0(aligned)
        )
        self.assertEqual(aligned_score.dimensions.volume_alignment, 1)
        self.assertGreater(aligned_score.dimensions.volume_quality_ppm, 1_000_000)
        self.assertEqual(
            [item.feature_sha256 for item in low_score.indicators.feature_receipts],
            [item.feature_sha256 for item in aligned_score.indicators.feature_receipts],
        )

        opposed = _observation(slope_nano_usd=500_000_000)
        previous_close = opposed["daily_bars"][-2]["close_nano_usd"]
        current = opposed["daily_bars"][-1]
        current_close = int(previous_close) - 100_000_000
        current["open_nano_usd"] = current_close
        current["high_nano_usd"] = current_close + 1_000_000_000
        current["low_nano_usd"] = current_close - 1_000_000_000
        current["close_nano_usd"] = current_close
        current["volume_shares"] = 3_000_000
        opposed_score = derive_management_score_f0(
            validate_management_score_observation_f0(opposed)
        )
        self.assertEqual(opposed_score.dimensions.volume_alignment, -1)
        self.assertLess(opposed_score.dimensions.volume_quality_ppm, 1_000_000)

    def test_historical_early_close_volume_is_excluded_from_reference(self) -> None:
        document = _observation()
        early_bars = [
            item
            for item in document["daily_bars"][:-1]
            if item["session_kind"] == "EARLY_CLOSE"
        ]
        self.assertTrue(early_bars)
        early_bars[-1]["volume_shares"] = 999_000_000
        result = derive_management_score_f0(
            validate_management_score_observation_f0(document)
        )
        self.assertEqual(result.indicators.volume_median20_shares, 1_000_000)


class ManagementActionTests(unittest.TestCase):
    def _score(self, document: dict[str, object]):
        return derive_management_score_f0(
            validate_management_score_observation_f0(document)
        )

    def test_neutral_score_holds_current_units(self) -> None:
        observation = _observation(original_units=4)
        result = evaluate_hypothetical_management_action_f0(
            self._score(observation),
            _action_snapshot(observation, current_units=4),
        )
        self.assertEqual(result.action, "HOLD")
        self.assertEqual(result.target_units, 4)
        self.assertEqual(result.reason_code, "PRIMARY_SCORE_HOLD_BAND")
        self.assertFalse(result.actionable)
        self.assertEqual(result.broker_order_count, 0)

    def test_forged_score_and_action_snapshot_are_rejected(self) -> None:
        observation = _observation()
        score = self._score(observation)
        forged_score = replace(score, result_sha256="0" * 64)
        with self.assertRaises(ManagementResearchError) as caught_score:
            evaluate_hypothetical_management_action_f0(
                forged_score,
                _action_snapshot(observation),
            )
        self.assertEqual(
            caught_score.exception.reason_code,
            "SCORE_TYPED_SEAL_INVALID",
        )

        typed_snapshot = validate_management_action_snapshot_f0(
            _action_snapshot(observation),
            score,
        )
        forged_snapshot = replace(typed_snapshot, snapshot_sha256="0" * 64)
        with self.assertRaises(ManagementResearchError) as caught_snapshot:
            evaluate_hypothetical_management_action_f0(score, forged_snapshot)
        self.assertEqual(
            caught_snapshot.exception.reason_code,
            "ACTION_SNAPSHOT_TYPED_SEAL_INVALID",
        )

    def test_dte_must_match_bound_contract_expiry(self) -> None:
        observation = _observation()
        snapshot = _action_snapshot(observation)
        snapshot["dte"] = int(snapshot["dte"]) - 1
        with self.assertRaises(ManagementResearchError) as caught:
            evaluate_hypothetical_management_action_f0(
                self._score(observation),
                snapshot,
            )
        self.assertEqual(caught.exception.reason_code, "DTE_IDENTITY_MISMATCH")

    def test_action_unknown_float_and_identity_conflict_fail_closed(self) -> None:
        observation = _observation()
        score = self._score(observation)
        unknown = _action_snapshot(observation)
        unknown["winner"] = "LC0"
        with self.assertRaises(ManagementResearchError) as caught_unknown:
            evaluate_hypothetical_management_action_f0(score, unknown)
        self.assertEqual(
            caught_unknown.exception.reason_code,
            "ACTION_SNAPSHOT_SCHEMA_INVALID",
        )

        floating = _action_snapshot(observation)
        floating["current_units"] = 4.0
        with self.assertRaises(ManagementResearchError) as caught_float:
            evaluate_hypothetical_management_action_f0(score, floating)
        self.assertEqual(
            caught_float.exception.reason_code,
            "CANONICAL_JSON_FLOAT_FORBIDDEN",
        )

        mismatch = _action_snapshot(observation)
        mismatch["episode_id"] = "EPISODE-OTHER"
        with self.assertRaises(ManagementResearchError) as caught_identity:
            evaluate_hypothetical_management_action_f0(score, mismatch)
        self.assertEqual(
            caught_identity.exception.reason_code,
            "ACTION_EPISODE_IDENTITY_MISMATCH",
        )

    def test_missing_dte_blocks_adjustment_instead_of_guessing(self) -> None:
        observation = _observation(
            slope_nano_usd=500_000_000,
            above_70_streak=1,
        )
        snapshot = _action_snapshot(observation, current_units=2)
        snapshot["dte"] = None
        result = evaluate_hypothetical_management_action_f0(
            self._score(observation), snapshot
        )
        self.assertEqual(result.action, "ACTION_BLOCKED")
        self.assertEqual(result.reason_code, "DTE_NOT_ELIGIBLE")

        neutral = _observation()
        neutral_snapshot = _action_snapshot(neutral)
        neutral_snapshot["dte"] = None
        neutral_result = evaluate_hypothetical_management_action_f0(
            self._score(neutral), neutral_snapshot
        )
        self.assertEqual(neutral_result.action, "ACTION_BLOCKED")
        self.assertEqual(neutral_result.reason_code, "DTE_NOT_ELIGIBLE")

    def test_expiry_day_requires_terminal_override_and_cannot_hold(self) -> None:
        observation = _observation()
        action_date = next_session_date(str(observation["trading_date"]))
        observation["episode"]["contracts"][0]["expiry_date"] = (
            action_date.isoformat()
        )
        score = self._score(observation)
        snapshot = _action_snapshot(observation)
        self.assertEqual(snapshot["dte"], 0)
        with self.assertRaises(ManagementResearchError) as caught:
            evaluate_hypothetical_management_action_f0(score, snapshot)
        self.assertEqual(
            caught.exception.reason_code,
            "EXPIRY_SAFETY_TERMINAL_FACT_REQUIRED",
        )

    def test_missing_quote_blocks_adjustment_and_duplicate_quote_is_rejected(self) -> None:
        observation = _observation(
            slope_nano_usd=500_000_000,
            carrier_id="BCS0",
            above_70_streak=1,
        )
        missing = _action_snapshot(observation, current_units=2)
        missing["quotes"].pop()
        blocked = evaluate_hypothetical_management_action_f0(
            self._score(observation), missing
        )
        self.assertEqual(blocked.action, "ACTION_BLOCKED")
        self.assertEqual(blocked.reason_code, "QUOTE_SET_MISSING")

        duplicate = _action_snapshot(observation, current_units=2)
        duplicate["quotes"].append(deepcopy(duplicate["quotes"][0]))
        with self.assertRaises(ManagementResearchError) as caught:
            evaluate_hypothetical_management_action_f0(
                self._score(observation), duplicate
            )
        self.assertEqual(caught.exception.reason_code, "QUOTE_SET_INVALID")

    def test_quote_tick_alignment_and_bcs_receive_skew_fail_closed(self) -> None:
        observation = _observation()
        misaligned = _action_snapshot(observation)
        misaligned["quotes"][0]["ask_nano_usd"] += 1
        with self.assertRaises(ManagementResearchError) as caught:
            evaluate_hypothetical_management_action_f0(
                self._score(observation),
                misaligned,
            )
        self.assertEqual(
            caught.exception.reason_code,
            "QUOTE_TICK_ALIGNMENT_INVALID",
        )

        bcs = _observation(
            slope_nano_usd=500_000_000,
            carrier_id="BCS0",
            original_units=4,
            above_70_streak=1,
        )
        skewed = _action_snapshot(bcs, current_units=2)
        skewed["quotes"][1]["receive_utc_ns"] -= 1_000_000_001
        skewed["quotes"][1]["event_utc_ns"] -= 1_000_000_001
        result = evaluate_hypothetical_management_action_f0(
            self._score(bcs),
            skewed,
        )
        self.assertEqual(result.action, "ACTION_BLOCKED")
        self.assertEqual(result.reason_code, "QUOTE_RECEIVE_SKEW_EXCEEDED")

    def test_quote_age_and_receive_skew_boundaries_are_inclusive(self) -> None:
        observation = _observation(
            slope_nano_usd=500_000_000,
            carrier_id="BCS0",
            above_70_streak=1,
        )
        exact = _action_snapshot(
            observation,
            current_units=2,
            quote_age_ns=5_000_000_000,
        )
        exact["quotes"][1]["event_utc_ns"] += 1_000_000_000
        exact["quotes"][1]["receive_utc_ns"] += 1_000_000_000
        allowed = evaluate_hypothetical_management_action_f0(
            self._score(observation), exact
        )
        self.assertEqual(allowed.action, "RECOVERY_READD")

        too_old = deepcopy(exact)
        too_old["quotes"][0]["event_utc_ns"] -= 1
        blocked_age = evaluate_hypothetical_management_action_f0(
            self._score(observation), too_old
        )
        self.assertEqual(blocked_age.reason_code, "QUOTE_STALE")

        too_skewed = deepcopy(exact)
        too_skewed["quotes"][1]["event_utc_ns"] += 1
        too_skewed["quotes"][1]["receive_utc_ns"] += 1
        blocked_skew = evaluate_hypothetical_management_action_f0(
            self._score(observation), too_skewed
        )
        self.assertEqual(
            blocked_skew.reason_code, "QUOTE_RECEIVE_SKEW_EXCEEDED"
        )

    def test_hard_stop_overrides_a_strong_score(self) -> None:
        observation = _observation(slope_nano_usd=500_000_000)
        score = self._score(observation)
        self.assertGreaterEqual(score.primary.net_signal_ppm, 400_000)
        result = evaluate_hypothetical_management_action_f0(
            score,
            _terminal_override(observation, current_units=4),
        )
        self.assertEqual(result.action, "EXIT_DUE")
        self.assertEqual(result.target_units, 0)
        self.assertEqual(result.score_authority, "DIAGNOSTIC_ONLY")
        self.assertEqual(result.exit_latch_status_after, "EXIT_DUE_LATCHED")

    def test_terminal_override_needs_no_next_day_quote_fee_or_dte(self) -> None:
        observation = _observation(slope_nano_usd=500_000_000)
        result = evaluate_hypothetical_management_action_f0(
            self._score(observation),
            _terminal_override(
                observation,
                override_status="CONFIRMED_INVALIDATION",
            ),
        )
        self.assertEqual(result.action, "EXIT_DUE")
        self.assertEqual(result.reason_code, "CONFIRMED_INVALIDATION")

    def test_latched_episode_with_zero_units_never_readds(self) -> None:
        observation = _observation(
            slope_nano_usd=500_000_000,
            exit_latch_status="EXIT_DUE_LATCHED",
        )
        result = evaluate_hypothetical_management_action_f0(
            self._score(observation),
            _action_snapshot(observation, current_units=0),
        )
        self.assertEqual(result.action, "HOLD")
        self.assertEqual(result.target_units, 0)
        self.assertEqual(result.reason_code, "EXIT_DUE_LATCHED_NO_POSITION")
        state = observation["previous_state"]
        self.assertEqual(state["lineage_kind"], "PRIOR_RESULT")
        self.assertIsNotNone(state["previous_score_result"])
        self.assertIsNotNone(state["previous_action_result"])
        self.assertIsNotNone(state["previous_action_result_sha256"])
        self.assertEqual(
            (
                state["below_40_streak"],
                state["below_20_streak"],
                state["above_70_streak"],
            ),
            (0, 0, 0),
        )
        score = self._score(observation)
        self.assertEqual(
            (
                score.updated_state.below_40_streak,
                score.updated_state.below_20_streak,
                score.updated_state.above_70_streak,
            ),
            (0, 0, 0),
        )

    def test_strong_recovery_requires_confirmation_and_fresh_quote(self) -> None:
        day_one = _observation(
            slope_nano_usd=500_000_000,
            original_units=4,
        )
        score_one = self._score(day_one)
        self.assertEqual(score_one.updated_state.above_70_streak, 1)
        waiting = evaluate_hypothetical_management_action_f0(
            score_one,
            _action_snapshot(day_one, current_units=2),
        )
        self.assertEqual(waiting.action, "HOLD")
        self.assertEqual(waiting.reason_code, "WAITING_FOR_RECOVERY_CONFIRMATION")

        day_two = _observation(
            slope_nano_usd=500_000_000,
            original_units=4,
            above_70_streak=1,
        )
        score_two = self._score(day_two)
        add = evaluate_hypothetical_management_action_f0(
            score_two,
            _action_snapshot(day_two, current_units=2),
        )
        self.assertEqual(add.action, "RECOVERY_READD")
        self.assertEqual(add.target_units, 3)

        stale = evaluate_hypothetical_management_action_f0(
            score_two,
            _action_snapshot(
                day_two,
                current_units=2,
                quote_age_ns=6_000_000_000,
            ),
        )
        self.assertEqual(stale.action, "ACTION_BLOCKED")
        self.assertEqual(stale.reason_code, "QUOTE_STALE")

    def test_recovery_tiers_for_original_units_one_through_five_follow_policy(self) -> None:
        policy = action_policy_f0()
        for original in range(1, 6):
            tiers = sorted(
                {
                    max(1, ceil_fraction(original, numerator, denominator))
                    for numerator, denominator in policy.recovery_tier_fractions
                }
            )
            observation = _observation(
                slope_nano_usd=500_000_000,
                original_units=original,
                above_70_streak=1,
            )
            score = self._score(observation)
            for current in range(1, original + 1):
                with self.subTest(original=original, current=current):
                    expected = next(
                        (tier for tier in tiers if current < tier),
                        current,
                    )
                    result = evaluate_hypothetical_management_action_f0(
                        score,
                        _action_snapshot(observation, current_units=current),
                    )
                    self.assertEqual(result.target_units, expected)
                    self.assertEqual(
                        result.action,
                        "RECOVERY_READD" if expected > current else "HOLD",
                    )

    def test_override_precedence_combination_matrix(self) -> None:
        latched = _observation(
            slope_nano_usd=500_000_000,
            exit_latch_status="EXIT_DUE_LATCHED",
        )
        latched_result = evaluate_hypothetical_management_action_f0(
            self._score(latched),
            _terminal_override(
                latched,
                override_status="CONFIRMED_INVALIDATION",
                current_units=2,
            ),
        )
        self.assertEqual(
            (latched_result.action, latched_result.reason_code),
            ("EXIT_DUE", "EXIT_DUE_LATCHED"),
        )

        observation = _observation(slope_nano_usd=500_000_000)
        score = self._score(observation)
        data_not_qualified = _action_snapshot(
            observation,
            override_status="DATA_NOT_QUALIFIED",
            reconciliation_status="PARTIAL_FILL",
            external_risk_cap_units=0,
        )
        data_not_qualified["dte"] = None
        data_result = evaluate_hypothetical_management_action_f0(
            score, data_not_qualified
        )
        self.assertEqual(
            (data_result.action, data_result.reason_code),
            ("NO_DECISION", "DATA_NOT_QUALIFIED"),
        )

        reconciliation = _action_snapshot(
            observation,
            reconciliation_status="PARTIAL_FILL",
            external_risk_cap_units=0,
        )
        reconciliation_result = evaluate_hypothetical_management_action_f0(
            score, reconciliation
        )
        self.assertEqual(
            (reconciliation_result.action, reconciliation_result.reason_code),
            ("RECONCILIATION_BLOCKED", "PARTIAL_FILL"),
        )

        cap_zero_result = evaluate_hypothetical_management_action_f0(
            score,
            _action_snapshot(observation, external_risk_cap_units=0),
        )
        self.assertEqual(
            (cap_zero_result.action, cap_zero_result.reason_code),
            ("EXIT_DUE", "EXTERNAL_RISK_CAP_ZERO"),
        )

    def test_old_market_event_cannot_be_freshened_by_late_receive_time(self) -> None:
        observation = _observation(
            slope_nano_usd=500_000_000,
            above_70_streak=1,
        )
        snapshot = _action_snapshot(observation, current_units=2)
        action_ns = int(snapshot["action_utc_ns"])
        snapshot["quotes"][0]["event_utc_ns"] = action_ns - 6_000_000_000
        snapshot["quotes"][0]["receive_utc_ns"] = action_ns - 1_000_000_000
        result = evaluate_hypothetical_management_action_f0(
            self._score(observation), snapshot
        )
        self.assertEqual(result.action, "ACTION_BLOCKED")
        self.assertEqual(result.reason_code, "QUOTE_STALE")

    def test_integer_cap_matrix_for_original_units_one_through_five(self) -> None:
        for original, defensive_1_target, defensive_2_target in (
            (1, 1, 1),
            (2, 2, 1),
            (3, 3, 2),
            (4, 3, 2),
            (5, 4, 3),
        ):
            with self.subTest(original=original, band="DEFENSIVE_1"):
                observation = _observation(
                    slope_nano_usd=-1_000_000,
                    original_units=original,
                    below_40_streak=1,
                )
                result = evaluate_hypothetical_management_action_f0(
                    self._score(observation),
                    _action_snapshot(observation, current_units=original),
                )
                self.assertEqual(result.target_units, defensive_1_target)
            with self.subTest(original=original, band="DEFENSIVE_2"):
                observation = _observation(
                    slope_nano_usd=-500_000_000,
                    original_units=original,
                    below_40_streak=1,
                    below_20_streak=1,
                )
                result = evaluate_hypothetical_management_action_f0(
                    self._score(observation),
                    _action_snapshot(observation, current_units=original),
                )
                self.assertEqual(result.target_units, defensive_2_target)

    def test_external_caps_bind_hold_missing_and_zero(self) -> None:
        observation = _observation(original_units=5)
        score = self._score(observation)
        reduced = evaluate_hypothetical_management_action_f0(
            score,
            _action_snapshot(
                observation,
                current_units=5,
                account_risk_cap_units=4,
                liquidity_cap_units=3,
                external_risk_cap_units=2,
            ),
        )
        self.assertEqual((reduced.action, reduced.target_units), ("REDUCE", 2))
        self.assertEqual(reduced.score_authority, "DIAGNOSTIC_ONLY")
        stale_reduction = evaluate_hypothetical_management_action_f0(
            score,
            _action_snapshot(
                observation,
                current_units=5,
                external_risk_cap_units=2,
                quote_age_ns=6_000_000_000,
            ),
        )
        self.assertEqual(stale_reduction.action, "ACTION_BLOCKED")
        self.assertEqual(stale_reduction.score_authority, "DIAGNOSTIC_ONLY")
        missing = evaluate_hypothetical_management_action_f0(
            score,
            _action_snapshot(
                observation,
                current_units=5,
                account_risk_cap_units=None,
            ),
        )
        self.assertEqual(missing.action, "NO_DECISION")
        zero = evaluate_hypothetical_management_action_f0(
            score,
            _action_snapshot(
                observation,
                current_units=5,
                external_risk_cap_units=0,
            ),
        )
        self.assertEqual((zero.action, zero.target_units), ("EXIT_DUE", 0))
        self.assertEqual(zero.exit_latch_status_after, "EXIT_DUE_LATCHED")

    def test_integer_no_op_and_bcs_residual_leg_fail_closed(self) -> None:
        weak = _observation(
            slope_nano_usd=-500_000_000,
            original_units=1,
            below_40_streak=1,
            below_20_streak=1,
        )
        no_op = evaluate_hypothetical_management_action_f0(
            self._score(weak),
            _action_snapshot(weak, current_units=1),
        )
        self.assertEqual(no_op.action, "HOLD")
        self.assertEqual(
            no_op.reason_code,
            "NO_COMPLETE_UNIT_REDUCTION_AVAILABLE",
        )

        bcs = _observation(
            slope_nano_usd=-500_000_000,
            carrier_id="BCS0",
            original_units=4,
            below_40_streak=1,
            below_20_streak=1,
        )
        blocked = evaluate_hypothetical_management_action_f0(
            self._score(bcs),
            _action_snapshot(
                bcs,
                current_units=4,
                reconciliation_status="RESIDUAL_LEG",
            ),
        )
        self.assertEqual(blocked.action, "RECONCILIATION_BLOCKED")
        self.assertEqual(blocked.target_units, 4)

    def test_bcs_contract_and_quote_input_order_cannot_change_result(self) -> None:
        observation = _observation(
            slope_nano_usd=-10_000,
            carrier_id="BCS0",
            original_units=4,
            below_40_streak=1,
        )
        ordered = _action_snapshot(observation, current_units=4)
        reversed_observation = deepcopy(observation)
        reversed_observation["episode"]["contracts"].reverse()
        reversed_snapshot = deepcopy(ordered)
        reversed_snapshot["contracts"].reverse()
        reversed_snapshot["quotes"].reverse()
        first = evaluate_hypothetical_management_action_f0(
            self._score(observation), ordered
        )
        second = evaluate_hypothetical_management_action_f0(
            self._score(reversed_observation), reversed_snapshot
        )
        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        self.assertEqual((first.action, first.target_units), ("REDUCE", 3))


if __name__ == "__main__":
    unittest.main()
