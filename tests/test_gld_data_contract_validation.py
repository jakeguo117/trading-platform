from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import unittest
from zoneinfo import ZoneInfo

from gld_data_contracts.contracts import (
    CarrierEvidenceBundleV1,
    DataContractError,
    EntryFactBundleV1,
    SourceQualificationReceiptV1,
)
from gld_data_contracts.validation import (
    SYNTHETIC_CARRIER_ESTIMATOR_PACKAGE_SHA256,
    SYNTHETIC_CARRIER_ESTIMATOR_VERSION,
    SYNTHETIC_CARRIER_UNCERTAINTY_METHOD,
    validate_carrier_evidence,
    validate_entry_bundle,
)
from gld_simulation.canonical import canonical_json_sha256
from gld_research_core.crr_delta import RUNTIME_FINGERPRINT_SHA256


DAY_NS = 86_400_000_000_000
MINUTE_NS = 60_000_000_000
SECOND_NS = 1_000_000_000
BASE_NS = 1_767_225_600_000_000_000
XNYS_TIMEZONE = ZoneInfo("America/New_York")

ENTRY_HASH_DOMAINS = (
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
    return canonical_json_sha256({"seed": seed})


def _xnys_utc_ns(session_date: date, hour: int, minute: int) -> int:
    local = datetime.combine(
        session_date,
        time(hour, minute),
        tzinfo=XNYS_TIMEZONE,
    )
    return int(local.astimezone(timezone.utc).timestamp()) * SECOND_NS


def _rehash_entry(document: dict[str, object]) -> None:
    document["content_hashes"] = {
        f"{domain}_sha256": canonical_json_sha256(document[domain])
        for domain in ENTRY_HASH_DOMAINS
    }


def _valid_entry_document(
    *, classification: str = "SYNTHETIC_ONLY"
) -> dict[str, object]:
    first_date = date(2026, 1, 1)
    decision_index = 220
    session_dates: list[date] = []
    candidate = first_date
    while len(session_dates) < 241:
        if candidate.weekday() < 5:
            session_dates.append(candidate)
        candidate += timedelta(days=1)
    sessions: list[dict[str, object]] = []
    for index, session_date in enumerate(session_dates):
        sessions.append(
            {
                "session_ordinal": index + 1,
                "trading_date": session_date.isoformat(),
                "open_utc_ns": _xnys_utc_ns(session_date, 9, 30),
                "close_utc_ns": _xnys_utc_ns(session_date, 16, 0),
                "is_full_session": True,
            }
        )
    decision_session = sessions[decision_index]
    decision_date = str(decision_session["trading_date"])
    cutoff = int(decision_session["open_utc_ns"]) + 75 * MINUTE_NS

    daily: list[dict[str, object]] = []
    for index, session in enumerate(sessions[:decision_index]):
        close = 180_000_000_000 + index * 10_000_000
        daily.append(
            {
                "session_ordinal": session["session_ordinal"],
                "trading_date": session["trading_date"],
                "open_nano_usd": close - 100_000_000,
                "high_nano_usd": close + 200_000_000,
                "low_nano_usd": close - 200_000_000,
                "close_nano_usd": close,
                "volume": 1_000_000 + index,
                "complete": True,
                "max_event_utc_ns": int(session["close_utc_ns"]),
                "max_receive_utc_ns": int(session["close_utc_ns"]) + SECOND_NS,
            }
        )

    minute_bars: list[dict[str, object]] = []
    start_1030 = cutoff - 15 * MINUTE_NS
    for offset in range(15):
        close = 184_000_000_000 + offset * 10_000_000
        start = start_1030 + offset * MINUTE_NS
        end = start + MINUTE_NS
        minute_bars.append(
            {
                "session_ordinal": decision_session["session_ordinal"],
                "trading_date": decision_date,
                "minute_ending_ordinal": 630 + offset,
                "start_utc_ns": start,
                "end_utc_ns": end,
                "open_nano_usd": close - 5_000_000,
                "high_nano_usd": close + 10_000_000,
                "low_nano_usd": close - 10_000_000,
                "close_nano_usd": close,
                "volume": 10_000 + offset,
                "complete": True,
                "max_event_utc_ns": end,
                "max_receive_utc_ns": end,
            }
        )

    capture = cutoff + 30 * SECOND_NS
    quote_receive = cutoff + 10 * SECOND_NS
    expiry_utc_ns = int(
        datetime(2026, 12, 31, 20, tzinfo=timezone.utc).timestamp()
    ) * SECOND_NS

    def bbo(instrument_id: str, bid: int, ask: int) -> dict[str, object]:
        return {
            "instrument_id": instrument_id,
            "bid_nano_usd": bid,
            "ask_nano_usd": ask,
            "bid_size": 25,
            "ask_size": 30,
            "tick_nano_usd": 10_000_000,
            "event_utc_ns": quote_receive - SECOND_NS,
            "receive_utc_ns": quote_receive,
            "flags": ["FIRM", "REGULAR"],
        }

    option_quotes: list[dict[str, object]] = []
    for index, strike in enumerate((180_000_000_000, 185_000_000_000, 190_000_000_000)):
        contract_id = f"GLD-C-{index + 1}"
        option_quotes.append(
            {
                "contract": {
                    "contract_id": contract_id,
                    "occ_symbol": f"GLD261231C{strike // 1_000_000:08d}",
                    "underlying": "GLD",
                    "option_type": "CALL",
                    "strike_nano_usd": strike,
                    "expiry_date": "2026-12-31",
                    "last_trading_date": "2026-12-31",
                    "expiry_utc_ns": expiry_utc_ns,
                    "last_trading_utc_ns": expiry_utc_ns,
                    "activation_utc_ns": cutoff - 100 * DAY_NS,
                    "multiplier": 100,
                    "deliverable_shares": 100,
                    "deliverable": "100 GLD SHARES",
                    "currency": "USD",
                    "exchange": "SMART",
                    "tick_nano_usd": 10_000_000,
                    "standard_unadjusted": True,
                    "exercise_style": "AMERICAN",
                },
                "top_of_book": bbo(
                    contract_id,
                    4_000_000_000 - index * 500_000_000,
                    4_100_000_000 - index * 500_000_000,
                ),
            }
        )

    receipt = {
        "schema_version": "SOURCE_QUALIFICATION_RECEIPT_V1",
        "receipt_id": "source-all-v1",
        "provider_id": "LOCAL_SYNTHETIC_GENERATOR",
        "provider_version": "fixture-v1",
        "evidence_kind": "SYNTHETIC_GENERATOR",
        "entitlement_status": "NOT_APPLICABLE_SYNTHETIC",
        "field_coverage_ppm": 1_000_000,
        "complete_universe_supported": True,
        "event_time_semantics": "UTC_NS_EXPLICIT",
        "receive_time_semantics": "UTC_NS_EXPLICIT",
        "atomic_snapshot_supported": True,
        "synchronization_max_skew_ns": 50 * SECOND_NS,
        "effective_from_utc_ns": BASE_NS,
        "effective_to_utc_ns": None,
        "covered_domains": [
            "ACCOUNT",
            "CALENDAR",
            "DAILY_BARS",
            "FEES",
            "MINUTE_BARS",
            "OPTION_SNAPSHOT",
            "PIT_INPUTS",
        ],
        "evidence_sha256": _sha("source-all-v1"),
    }
    document: dict[str, object] = {
        "schema_version": "ENTRY_FACT_BUNDLE_V1",
        "classification": classification,
        "scope": "GLD_ENTRY_FACTS_ONLY",
        "bundle_id": "gld-entry-synthetic-001",
        "underlying": "GLD",
        "trading_date": decision_date,
        "cutoff_utc_ns": cutoff,
        "calendar": {
            "schema_version": "XNYS_CALENDAR_FACTS_V1",
            "source_receipt_id": "source-all-v1",
            "timezone": "America/New_York",
            "decision_session_ordinal": decision_session["session_ordinal"],
            "sessions": sessions,
        },
        "daily_bars": {
            "schema_version": "GLD_DAILY_OHLCV_FACTS_V1",
            "source_receipt_id": "source-all-v1",
            "price_scale": "NANO_USD",
            "bars": daily,
        },
        "minute_bars": {
            "schema_version": "GLD_MINUTE_OHLCV_FACTS_V1",
            "source_receipt_id": "source-all-v1",
            "price_scale": "NANO_USD",
            "bars": minute_bars,
        },
        "market_status": {
            "schema_version": "MARKET_STATUS_FACTS_V1",
            "source_receipt_id": "source-all-v1",
            "trading_date": decision_date,
            "as_of_utc_ns": cutoff,
            "market_phase": "REGULAR_TRADING",
            "complete_through_utc_ns": cutoff,
            "daily_history_complete": True,
            "intraday_history_complete": True,
            "option_universe_complete": True,
            "quote_snapshot_complete": True,
            "calendar_complete": True,
            "causal_cutoff_enforced": True,
        },
        "option_snapshot": {
            "schema_version": "GLD_CALL_ATOMIC_SNAPSHOT_V1",
            "source_receipt_id": "source-all-v1",
            "snapshot_id": "gld-option-snapshot-001",
            "capture_utc_ns": capture,
            "window_start_utc_ns": cutoff,
            "window_end_utc_ns": cutoff + MINUTE_NS,
            "universe_complete": True,
            "underlying_bbo": bbo("GLD", 184_000_000_000, 184_010_000_000),
            "option_quotes": option_quotes,
        },
        "pit_inputs": {
            "schema_version": "PIT_MARKET_INPUTS_V1",
            "source_receipt_ids": ["source-all-v1"],
            "as_of_utc_ns": cutoff,
            "rate_curve": [
                {"tenor_days": 30, "zero_rate_ppm": 42_000},
                {"tenor_days": 365, "zero_rate_ppm": 45_000},
            ],
            "expense_yield_ppm": 4_000,
            "distribution_yield_ppm": 0,
            "borrow_available": True,
            "borrow_rate_ppm": 1_000,
        },
        "fee_schedule": {
            "schema_version": "EFFECTIVE_FEE_SCHEDULE_V1",
            "source_receipt_id": "source-all-v1",
            "effective_from_utc_ns": BASE_NS,
            "effective_to_utc_ns": None,
            "currency": "USD",
            "broker_schedule_id": "broker-fees-v1",
            "exchange_schedule_id": "exchange-fees-v1",
            "clearing_schedule_id": "clearing-fees-v1",
            "regulatory_schedule_id": "regulatory-fees-v1",
            "long_entry_fee_nano_usd_per_contract": 650_000_000,
            "long_exit_fee_nano_usd_per_contract": 650_000_000,
            "short_entry_fee_nano_usd_per_contract": 650_000_000,
            "short_exit_fee_nano_usd_per_contract": 650_000_000,
        },
        "account_snapshot_a": {
            "schema_version": "ACCOUNT_SNAPSHOT_A_V1",
            "source_receipt_id": "source-all-v1",
            "as_of_utc_ns": cutoff,
            "currency": "USD",
            "net_liquidation_value_nano_usd": 100_000_000_000_000,
            "settled_cash_nano_usd": 80_000_000_000_000,
            "strategy_bankroll_nano_usd": 50_000_000_000_000,
            "strategy_high_watermark_nano_usd": 110_000_000_000_000,
            "realized_profit_nano_usd": 5_000_000_000_000,
            "realized_loss_nano_usd": 2_000_000_000_000,
            "current_gld_delta_exposure_nano_usd": 1_000_000_000_000,
            "positions": [
                {
                    "position_id": "position-1",
                    "instrument_id": "GLD",
                    "signed_contract_count": 10,
                    "multiplier": 1,
                    "currency": "USD",
                }
            ],
            "open_orders": [
                {
                    "order_id": "order-1",
                    "instrument_id": "GLD-C-1",
                    "remaining_contract_count": 1,
                    "side": "BUY",
                    "limit_nano_usd": 4_100_000_000,
                    "status": "OPEN",
                }
            ],
        },
        "rule_package": {
            "schema_version": "GLD_TECHNICAL_RULE_BINDING_V1",
            "package_id": "gld-technical-rules",
            "version": "1.0.0",
            "effective_from_utc_ns": BASE_NS,
            "cutoff_minute_ending_ordinal": 645,
            "option_window_duration_ns": MINUTE_NS,
            "max_quote_age_ns": 30 * SECOND_NS,
            "max_cross_leg_receive_skew_ns": 50 * SECOND_NS,
            "max_account_age_ns": DAY_NS,
            "min_quote_size": 1,
            "min_history_sessions": 220,
            "entry_stress_long_ticks": 1,
            "entry_stress_short_ticks": 1,
            "realized_profit_reinvestment_ppm": 500_000,
            "realized_loss_effect_ppm": 1_000_000,
            "minimum_cash_reserve_nlv_ppm": 100_000,
        },
        "model_package": {
            "schema_version": "LOCAL_OPTION_MODEL_BINDING_V1",
            "package_id": "gld-crr-local",
            "version": "1.0.0",
            "formula_id": "AMERICAN_CALL_CRR_IV_DELTA",
            "coarse_steps": 512,
            "fine_steps": 1024,
            "iv_iterations": 32,
            "rounding_mode": "HALF_EVEN_INTEGER",
            "source_code_sha256": _sha("crr-source-v1"),
            "runtime_fingerprint_sha256": RUNTIME_FINGERPRINT_SHA256,
        },
        "source_qualification_receipts": [receipt],
        "content_hashes": {},
    }
    _rehash_entry(document)
    return document


def _evidence_receipt(carrier_id: str) -> dict[str, object]:
    episodes: list[dict[str, object]] = []
    for index, split in enumerate(("DEVELOPMENT", "WALK_FORWARD", "SEALED_OOS")):
        episodes.append(
            {
                "episode_id": f"{carrier_id.lower()}-episode-{index + 1}",
                "entry_utc_ns": BASE_NS + index * DAY_NS,
                "exit_utc_ns": BASE_NS + (index + 1) * DAY_NS,
                "entry_debit_nano_usd": 500_000_000_000 + index,
                "after_cost_return_on_entry_debit_ppm": 100_000 - index * 50_000,
                "split": split,
                "input_sha256": _sha(f"{carrier_id}-episode-input-{index}"),
            }
        )
    episode_set_sha256 = canonical_json_sha256(episodes)
    distribution_id = f"{carrier_id.lower()}-returns-v1"
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
        "evidence_receipt_id": f"{carrier_id.lower()}-kelly-receipt-v1",
        "episode_cohort_id": f"{carrier_id.lower()}-cohort-v1",
        "entry_clock_id": "ENTRY_1045_ET",
        "exit_clock_id": f"{carrier_id}_EXIT_POLICY_V1",
        "cost_model_version": "COST_MODEL_V1",
        "rule_version": f"{carrier_id}_RULE_V1",
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
            "episode_set_sha256": episode_set_sha256,
            "distribution_sha256": distribution_sha256,
        },
        "expected_net_return_on_entry_debit_ppm": 50_000,
        "uncertainty_method": SYNTHETIC_CARRIER_UNCERTAINTY_METHOD,
        "full_kelly_ppm": 0,
        "robust_full_kelly_ppm": 0,
        "half_kelly_ppm": 0,
        "estimator_version": SYNTHETIC_CARRIER_ESTIMATOR_VERSION,
        "fee_schedule_sha256": _sha("fee-schedule-v1"),
        "exit_policy_sha256": _sha(f"{carrier_id}-exit-v1"),
        "input_sha256": _sha(f"{carrier_id}-evidence-input-v1"),
    }
    value["evidence_sha256"] = canonical_json_sha256(value)
    return value


def _valid_carrier_evidence() -> dict[str, object]:
    carriers = [_evidence_receipt("LC0"), _evidence_receipt("BCS0")]
    return {
        "schema_version": "CARRIER_EVIDENCE_BUNDLE_V1",
        "classification": "SYNTHETIC_ONLY",
        "scope": "GLD_ENTRY_CARRIER_EVIDENCE_ONLY",
        "underlying": "GLD",
        "rule_package_sha256": _sha("rule-package-v1"),
        "estimator_package_sha256": (
            SYNTHETIC_CARRIER_ESTIMATOR_PACKAGE_SHA256
        ),
        "carriers": carriers,
        "content_hashes": {
            str(carrier["carrier_id"]): carrier["evidence_sha256"]
            for carrier in carriers
        },
    }


def _refresh_evidence_hash(
    bundle: dict[str, object], carrier_index: int
) -> None:
    carriers = bundle["carriers"]
    if type(carriers) is not list:
        raise AssertionError("carriers must be a list")
    carrier = carriers[carrier_index]
    if type(carrier) is not dict:
        raise AssertionError("carrier evidence must be an object")
    unsigned = deepcopy(
        {key: value for key, value in carrier.items() if key != "evidence_sha256"}
    )
    episodes = unsigned.get("episodes")
    if type(episodes) is list:
        unsigned["episodes"] = sorted(episodes, key=lambda item: item["episode_id"])
    carrier["evidence_sha256"] = canonical_json_sha256(unsigned)
    content_hashes = bundle["content_hashes"]
    if type(content_hashes) is not dict:
        raise AssertionError("content_hashes must be an object")
    content_hashes[str(carrier["carrier_id"])] = carrier["evidence_sha256"]


def _refresh_distribution_binding(
    bundle: dict[str, object], carrier_index: int
) -> None:
    carrier = bundle["carriers"][carrier_index]
    episodes = sorted(carrier["episodes"], key=lambda item: item["episode_id"])
    distribution = carrier["distribution_binding"]
    distribution["episode_set_sha256"] = canonical_json_sha256(episodes)
    distribution["distribution_sha256"] = canonical_json_sha256(
        {
            "schema_version": "CARRIER_RETURN_DISTRIBUTION_V1",
            "carrier_id": carrier["carrier_id"],
            "distribution_id": distribution["distribution_id"],
            "return_unit": distribution["return_unit"],
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
    _refresh_evidence_hash(bundle, carrier_index)


class EntryFactBundleValidationTests(unittest.TestCase):
    def test_valid_synthetic_entry_is_typed_content_addressed_and_copy_safe(self) -> None:
        raw = _valid_entry_document()
        result = validate_entry_bundle(raw)
        self.assertIsInstance(result, EntryFactBundleV1)
        self.assertEqual(result.bundle_id, "gld-entry-synthetic-001")
        self.assertEqual(
            result.qualification.status, "STRUCTURALLY_VALID_SYNTHETIC"
        )
        self.assertEqual(
            result.qualification.trusted_source_receipt_set_sha256,
            canonical_json_sha256([]),
        )
        self.assertEqual(len(result.entry_bundle_sha256), 64)
        self.assertEqual(canonical_json_sha256(result.normalized_document), result.entry_bundle_sha256)
        self.assertEqual(result.canonical_bytes, result.canonical_bytes)
        self.assertEqual(len(result.source_qualification_receipts), 1)
        self.assertIsInstance(
            result.source_qualification_receipts[0], SourceQualificationReceiptV1
        )
        qualification_document = result.qualification.as_dict()
        qualification_hash = qualification_document.pop("qualification_sha256")
        self.assertEqual(
            qualification_hash, canonical_json_sha256(qualification_document)
        )
        copied = result.normalized_document
        copied["bundle_id"] = "mutated"
        self.assertEqual(result.normalized_document["bundle_id"], "gld-entry-synthetic-001")

    def test_order_of_objects_and_semantic_sets_does_not_change_output(self) -> None:
        first = _valid_entry_document()
        second = deepcopy(first)
        second = dict(reversed(tuple(second.items())))
        for domain, member in (
            ("calendar", "sessions"),
            ("daily_bars", "bars"),
            ("minute_bars", "bars"),
            ("option_snapshot", "option_quotes"),
            ("pit_inputs", "rate_curve"),
        ):
            container = second[domain]
            self.assertIs(type(container), dict)
            values = container[member]
            self.assertIs(type(values), list)
            values.reverse()
        receipts = second["source_qualification_receipts"]
        self.assertIs(type(receipts), list)
        covered_domains = receipts[0]["covered_domains"]
        self.assertIs(type(covered_domains), list)
        covered_domains.reverse()
        option_snapshot = second["option_snapshot"]
        self.assertIs(type(option_snapshot), dict)
        underlying_bbo = option_snapshot["underlying_bbo"]
        self.assertIs(type(underlying_bbo), dict)
        flags = underlying_bbo["flags"]
        self.assertIs(type(flags), list)
        flags.reverse()
        self.assertEqual(
            validate_entry_bundle(first).canonical_bytes,
            validate_entry_bundle(second).canonical_bytes,
        )

    def test_closed_schema_float_and_prefilled_derivations_are_rejected(self) -> None:
        cases: list[tuple[str, object]] = []
        unknown = _valid_entry_document()
        unknown["surprise"] = True
        cases.append(("ENTRY_SCHEMA_INVALID", unknown))
        floating = _valid_entry_document()
        floating["cutoff_utc_ns"] = 1.5
        cases.append(("CANONICAL_JSON_FLOAT_FORBIDDEN", floating))
        for forbidden_field in (
            "sma50_nano_usd",
            "iv_ppm",
            "delta_ppm",
            "winner",
            "quantity",
            "preference",
            "DecisionResult",
        ):
            derived = _valid_entry_document()
            market = derived["market_status"]
            self.assertIs(type(market), dict)
            market[forbidden_field] = 1
            cases.append(("ENTRY_DERIVED_FIELD_FORBIDDEN", derived))
        for reason, document in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(
                DataContractError, reason
            ):
                validate_entry_bundle(document)

    def test_minimum_history_and_complete_variable_call_universe_are_enforced(self) -> None:
        short_history = _valid_entry_document()
        daily = short_history["daily_bars"]
        self.assertIs(type(daily), dict)
        bars = daily["bars"]
        self.assertIs(type(bars), list)
        daily["bars"] = bars[1:]
        _rehash_entry(short_history)
        with self.assertRaisesRegex(DataContractError, "ENTRY_DAILY_HISTORY_INCOMPLETE"):
            validate_entry_bundle(short_history)

        short_future_calendar = _valid_entry_document()
        calendar = short_future_calendar["calendar"]
        self.assertIs(type(calendar), dict)
        sessions = calendar["sessions"]
        self.assertIs(type(sessions), list)
        decision_ordinal = int(calendar["decision_session_ordinal"])
        self.assertEqual(
            sum(
                int(item["session_ordinal"]) > decision_ordinal
                for item in sessions
            ),
            20,
        )
        validate_entry_bundle(deepcopy(short_future_calendar))
        sessions.pop()
        _rehash_entry(short_future_calendar)
        with self.assertRaisesRegex(
            DataContractError,
            "ENTRY_CALENDAR_COVERAGE_INCOMPLETE",
        ):
            validate_entry_bundle(short_future_calendar)

        for count in (1, 3, 7):
            with self.subTest(call_count=count):
                variable = _valid_entry_document()
                snapshot = variable["option_snapshot"]
                self.assertIs(type(snapshot), dict)
                quotes = snapshot["option_quotes"]
                self.assertIs(type(quotes), list)
                while len(quotes) < count:
                    clone = deepcopy(quotes[-1])
                    contract = clone["contract"]
                    top = clone["top_of_book"]
                    self.assertIs(type(contract), dict)
                    self.assertIs(type(top), dict)
                    contract_id = f"GLD-C-{len(quotes) + 1}"
                    contract["contract_id"] = contract_id
                    strike = int(contract["strike_nano_usd"]) + 1_000_000_000
                    contract["strike_nano_usd"] = strike
                    contract["occ_symbol"] = (
                        f"GLD261231C{strike // 1_000_000:08d}"
                    )
                    top["instrument_id"] = contract_id
                    quotes.append(clone)
                snapshot["option_quotes"] = quotes[:count]
                _rehash_entry(variable)
                self.assertEqual(
                    len(
                        validate_entry_bundle(variable)
                        .normalized_document["option_snapshot"]["option_quotes"]
                    ),
                    count,
                )

    def test_stale_incomplete_identity_and_time_mismatch_fail_closed(self) -> None:
        cases: list[tuple[str, dict[str, object]]] = []
        stale = _valid_entry_document()
        snapshot = stale["option_snapshot"]
        self.assertIs(type(snapshot), dict)
        underlying_bbo = snapshot["underlying_bbo"]
        self.assertIs(type(underlying_bbo), dict)
        underlying_bbo["receive_utc_ns"] = int(snapshot["window_start_utc_ns"])
        underlying_bbo["event_utc_ns"] = int(snapshot["window_start_utc_ns"])
        rule = stale["rule_package"]
        self.assertIs(type(rule), dict)
        rule["max_quote_age_ns"] = 29 * SECOND_NS
        _rehash_entry(stale)
        cases.append(("ENTRY_QUOTE_STALE", stale))

        incomplete = _valid_entry_document()
        snapshot = incomplete["option_snapshot"]
        status = incomplete["market_status"]
        self.assertIs(type(snapshot), dict)
        self.assertIs(type(status), dict)
        snapshot["universe_complete"] = False
        status["option_universe_complete"] = False
        _rehash_entry(incomplete)
        cases.append(("ENTRY_OPTION_UNIVERSE_INCOMPLETE", incomplete))

        identity = _valid_entry_document()
        snapshot = identity["option_snapshot"]
        self.assertIs(type(snapshot), dict)
        quotes = snapshot["option_quotes"]
        self.assertIs(type(quotes), list)
        top = quotes[0]["top_of_book"]
        self.assertIs(type(top), dict)
        top["instrument_id"] = "WRONG-CONTRACT"
        _rehash_entry(identity)
        cases.append(("ENTRY_OPTION_IDENTITY_MISMATCH", identity))

        occ_identity = _valid_entry_document()
        snapshot = occ_identity["option_snapshot"]
        self.assertIs(type(snapshot), dict)
        quotes = snapshot["option_quotes"]
        self.assertIs(type(quotes), list)
        contract = quotes[0]["contract"]
        self.assertIs(type(contract), dict)
        contract["strike_nano_usd"] = (
            int(contract["strike_nano_usd"]) + 1_000_000_000
        )
        _rehash_entry(occ_identity)
        cases.append(("ENTRY_OPTION_OCC_IDENTITY_MISMATCH", occ_identity))

        incomplete_through = _valid_entry_document()
        market_status = incomplete_through["market_status"]
        self.assertIs(type(market_status), dict)
        market_status["complete_through_utc_ns"] = (
            int(incomplete_through["cutoff_utc_ns"]) - 1
        )
        _rehash_entry(incomplete_through)
        cases.append(("ENTRY_MARKET_STATUS_INCOMPLETE", incomplete_through))

        stale_status_clock = _valid_entry_document()
        market_status = stale_status_clock["market_status"]
        self.assertIs(type(market_status), dict)
        market_status["as_of_utc_ns"] = (
            int(stale_status_clock["cutoff_utc_ns"]) - 60 * MINUTE_NS
        )
        _rehash_entry(stale_status_clock)
        cases.append(("ENTRY_MARKET_STATUS_INCOMPLETE", stale_status_clock))

        wrong_xnys_clock = _valid_entry_document()
        calendar = wrong_xnys_clock["calendar"]
        self.assertIs(type(calendar), dict)
        sessions = calendar["sessions"]
        self.assertIs(type(sessions), list)
        decision_ordinal = int(calendar["decision_session_ordinal"])
        decision = next(
            item
            for item in sessions
            if int(item["session_ordinal"]) == decision_ordinal
        )
        hour_ns = 60 * MINUTE_NS
        decision["open_utc_ns"] = int(decision["open_utc_ns"]) + hour_ns
        decision["close_utc_ns"] = int(decision["close_utc_ns"]) + hour_ns
        wrong_xnys_clock["cutoff_utc_ns"] = (
            int(wrong_xnys_clock["cutoff_utc_ns"]) + hour_ns
        )
        _rehash_entry(wrong_xnys_clock)
        cases.append(("ENTRY_CALENDAR_SESSION_INVALID", wrong_xnys_clock))

        daily_before_session = _valid_entry_document()
        daily = daily_before_session["daily_bars"]
        self.assertIs(type(daily), dict)
        bars = daily["bars"]
        self.assertIs(type(bars), list)
        bars[0]["max_event_utc_ns"] = 0
        _rehash_entry(daily_before_session)
        cases.append(("ENTRY_DAILY_BAR_INVALID", daily_before_session))

        minute_before_interval = _valid_entry_document()
        minute = minute_before_interval["minute_bars"]
        self.assertIs(type(minute), dict)
        bars = minute["bars"]
        self.assertIs(type(bars), list)
        bars[0]["max_event_utc_ns"] = 0
        _rehash_entry(minute_before_interval)
        cases.append(("ENTRY_MINUTE_BAR_INVALID", minute_before_interval))

        future_minute = _valid_entry_document()
        minute = future_minute["minute_bars"]
        self.assertIs(type(minute), dict)
        bars = minute["bars"]
        self.assertIs(type(bars), list)
        extra = deepcopy(bars[-1])
        extra["minute_ending_ordinal"] = 700
        extra["start_utc_ns"] = int(future_minute["cutoff_utc_ns"]) + MINUTE_NS
        extra["end_utc_ns"] = int(extra["start_utc_ns"]) + MINUTE_NS
        extra["max_event_utc_ns"] = extra["end_utc_ns"]
        extra["max_receive_utc_ns"] = extra["end_utc_ns"]
        bars.append(extra)
        _rehash_entry(future_minute)
        cases.append(("ENTRY_MINUTE_BAR_INVALID", future_minute))

        zero_high_watermark = _valid_entry_document()
        snapshot_a = zero_high_watermark["account_snapshot_a"]
        self.assertIs(type(snapshot_a), dict)
        snapshot_a["strategy_high_watermark_nano_usd"] = 0
        _rehash_entry(zero_high_watermark)
        cases.append(("ENTRY_ACCOUNT_INVALID", zero_high_watermark))

        arithmetic_overflow = _valid_entry_document()
        snapshot = arithmetic_overflow["option_snapshot"]
        self.assertIs(type(snapshot), dict)
        quotes = snapshot["option_quotes"]
        self.assertIs(type(quotes), list)
        contract = quotes[0]["contract"]
        self.assertIs(type(contract), dict)
        contract["multiplier"] = 2**63 - 1
        contract["standard_unadjusted"] = False
        _rehash_entry(arithmetic_overflow)
        cases.append(
            (
                "ENTRY_TECHNICAL_ARITHMETIC_RANGE_INVALID",
                arithmetic_overflow,
            )
        )

        overlong_contract_id = _valid_entry_document()
        snapshot = overlong_contract_id["option_snapshot"]
        self.assertIs(type(snapshot), dict)
        quotes = snapshot["option_quotes"]
        self.assertIs(type(quotes), list)
        contract = quotes[0]["contract"]
        top = quotes[0]["top_of_book"]
        self.assertIs(type(contract), dict)
        self.assertIs(type(top), dict)
        contract["contract_id"] = "A" * 128
        top["instrument_id"] = "A" * 128
        _rehash_entry(overlong_contract_id)
        cases.append(("ENTRY_OPTION_CONTRACT_INVALID", overlong_contract_id))

        fees = _valid_entry_document()
        fee_schedule = fees["fee_schedule"]
        self.assertIs(type(fee_schedule), dict)
        fee_schedule["effective_from_utc_ns"] = int(fees["cutoff_utc_ns"]) + 1
        _rehash_entry(fees)
        cases.append(("ENTRY_FEE_TIME_MISALIGNED", fees))

        account = _valid_entry_document()
        snapshot_a = account["account_snapshot_a"]
        self.assertIs(type(snapshot_a), dict)
        snapshot_a["as_of_utc_ns"] = int(account["cutoff_utc_ns"]) - DAY_NS - 1
        _rehash_entry(account)
        cases.append(("ENTRY_ACCOUNT_STALE", account))

        future = _valid_entry_document()
        pit = future["pit_inputs"]
        snapshot = future["option_snapshot"]
        self.assertIs(type(pit), dict)
        self.assertIs(type(snapshot), dict)
        pit["as_of_utc_ns"] = int(snapshot["capture_utc_ns"]) + 1
        _rehash_entry(future)
        cases.append(("ENTRY_FUTURE_DATA_FORBIDDEN", future))

        duplicate = _valid_entry_document()
        snapshot = duplicate["option_snapshot"]
        self.assertIs(type(snapshot), dict)
        quotes = snapshot["option_quotes"]
        self.assertIs(type(quotes), list)
        quotes.append(deepcopy(quotes[0]))
        _rehash_entry(duplicate)
        cases.append(("ENTRY_OPTION_CONTRACT_DUPLICATE", duplicate))

        wrong_unit = _valid_entry_document()
        daily = wrong_unit["daily_bars"]
        self.assertIs(type(daily), dict)
        daily["price_scale"] = "USD"
        _rehash_entry(wrong_unit)
        cases.append(("ENTRY_DAILY_BARS_VERSION_UNSUPPORTED", wrong_unit))

        insufficient_size = _valid_entry_document()
        snapshot = insufficient_size["option_snapshot"]
        self.assertIs(type(snapshot), dict)
        quotes = snapshot["option_quotes"]
        self.assertIs(type(quotes), list)
        quote_book = quotes[0]["top_of_book"]
        self.assertIs(type(quote_book), dict)
        quote_book["bid_size"] = 0
        _rehash_entry(insufficient_size)
        cases.append(("ENTRY_BBO_INVALID", insufficient_size))

        tick_identity = _valid_entry_document()
        snapshot = tick_identity["option_snapshot"]
        self.assertIs(type(snapshot), dict)
        quotes = snapshot["option_quotes"]
        self.assertIs(type(quotes), list)
        contract = quotes[0]["contract"]
        self.assertIs(type(contract), dict)
        contract["tick_nano_usd"] = int(contract["tick_nano_usd"]) * 2
        _rehash_entry(tick_identity)
        cases.append(("ENTRY_OPTION_IDENTITY_MISMATCH", tick_identity))

        deliverable_identity = _valid_entry_document()
        snapshot = deliverable_identity["option_snapshot"]
        self.assertIs(type(snapshot), dict)
        quotes = snapshot["option_quotes"]
        self.assertIs(type(quotes), list)
        contract = quotes[0]["contract"]
        self.assertIs(type(contract), dict)
        contract["deliverable_shares"] = int(contract["multiplier"]) - 1
        _rehash_entry(deliverable_identity)
        cases.append(
            (
                "ENTRY_OPTION_DELIVERABLE_IDENTITY_MISMATCH",
                deliverable_identity,
            )
        )

        for reason, document in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(
                DataContractError, reason
            ):
                validate_entry_bundle(document)

    def test_content_hashes_are_verified_after_normalization(self) -> None:
        document = _valid_entry_document()
        hashes = document["content_hashes"]
        self.assertIs(type(hashes), dict)
        hashes["daily_bars_sha256"] = "0" * 64
        with self.assertRaisesRegex(DataContractError, "ENTRY_CONTENT_HASH_MISMATCH"):
            validate_entry_bundle(document)

    def test_production_candidate_reports_data_qualification_without_upgrading_synthetic(self) -> None:
        unqualified = _valid_entry_document(classification="PRODUCTION_CANDIDATE")
        result = validate_entry_bundle(unqualified)
        self.assertEqual(result.qualification.status, "DATA_NOT_QUALIFIED")
        self.assertIn("SOURCE_ENTITLEMENT_NOT_VERIFIED", result.qualification.reason_codes)
        self.assertEqual(
            result.qualification.trusted_source_receipt_set_sha256,
            canonical_json_sha256([]),
        )

        qualified = _valid_entry_document(classification="PRODUCTION_CANDIDATE")
        receipts = qualified["source_qualification_receipts"]
        self.assertIs(type(receipts), list)
        receipts[0]["evidence_kind"] = "PROVIDER_CAPABILITY_RECEIPT"
        receipts[0]["entitlement_status"] = "VERIFIED"
        _rehash_entry(qualified)
        result = validate_entry_bundle(qualified)
        self.assertEqual(result.qualification.status, "DATA_NOT_QUALIFIED")
        self.assertIn(
            "SOURCE_RECEIPT_NOT_TRUSTED", result.qualification.reason_codes
        )
        trusted_hash = result.source_qualification_receipts[0].receipt_sha256
        result = validate_entry_bundle(
            qualified,
            trusted_source_receipt_sha256=frozenset({trusted_hash}),
        )
        self.assertEqual(result.qualification.status, "DATA_NOT_QUALIFIED")
        self.assertIn(
            "SOURCE_OBSERVATION_ATTESTATION_NOT_IMPLEMENTED",
            result.qualification.reason_codes,
        )
        self.assertEqual(
            result.qualification.trusted_source_receipt_set_sha256,
            canonical_json_sha256([trusted_hash]),
        )

        changed_observation = deepcopy(qualified)
        daily = changed_observation["daily_bars"]
        self.assertIs(type(daily), dict)
        bars = daily["bars"]
        self.assertIs(type(bars), list)
        bars[-1]["close_nano_usd"] = int(bars[-1]["close_nano_usd"]) + 1
        _rehash_entry(changed_observation)
        changed_result = validate_entry_bundle(
            changed_observation,
            trusted_source_receipt_sha256=frozenset({trusted_hash}),
        )
        self.assertEqual(
            changed_result.qualification.status, "DATA_NOT_QUALIFIED"
        )
        self.assertIn(
            "SOURCE_OBSERVATION_ATTESTATION_NOT_IMPLEMENTED",
            changed_result.qualification.reason_codes,
        )

        forged = deepcopy(qualified)
        forged_receipts = forged["source_qualification_receipts"]
        self.assertIs(type(forged_receipts), list)
        forged_receipts[0]["provider_id"] = "FORGED_PROVIDER"
        forged_receipts[0]["event_time_semantics"] = "UNKNOWN"
        forged_receipts[0]["receive_time_semantics"] = "UNKNOWN"
        _rehash_entry(forged)
        forged_result = validate_entry_bundle(
            forged,
            trusted_source_receipt_sha256=frozenset({trusted_hash}),
        )
        self.assertEqual(
            forged_result.qualification.status, "DATA_NOT_QUALIFIED"
        )
        self.assertIn(
            "SOURCE_RECEIPT_NOT_TRUSTED",
            forged_result.qualification.reason_codes,
        )
        self.assertIn(
            "SOURCE_TIME_SEMANTICS_NOT_VERIFIED",
            forged_result.qualification.reason_codes,
        )

        synthetic = _valid_entry_document()
        synthetic_receipts = synthetic["source_qualification_receipts"]
        self.assertIs(type(synthetic_receipts), list)
        synthetic_hash = validate_entry_bundle(
            synthetic
        ).source_qualification_receipts[0].receipt_sha256
        result = validate_entry_bundle(
            synthetic,
            trusted_source_receipt_sha256=frozenset({synthetic_hash}),
        )
        self.assertEqual(
            result.qualification.status, "STRUCTURALLY_VALID_SYNTHETIC"
        )
        with self.assertRaisesRegex(
            DataContractError, "TRUSTED_SOURCE_RECEIPT_SET_INVALID"
        ):
            validate_entry_bundle(
                synthetic,
                trusted_source_receipt_sha256={synthetic_hash},  # type: ignore[arg-type]
            )


class CarrierEvidenceValidationTests(unittest.TestCase):
    def test_valid_independent_evidence_is_typed_and_deterministic(self) -> None:
        first = _valid_carrier_evidence()
        second = deepcopy(first)
        carriers = second["carriers"]
        self.assertIs(type(carriers), list)
        carriers.reverse()
        for carrier in carriers:
            episodes = carrier["episodes"]
            self.assertIs(type(episodes), list)
            episodes.reverse()
            distribution = carrier["distribution_binding"]
            self.assertIs(type(distribution), dict)
            distribution["episode_set_sha256"] = canonical_json_sha256(
                sorted(episodes, key=lambda item: item["episode_id"])
            )
            carrier_index = carriers.index(carrier)
            _refresh_evidence_hash(second, carrier_index)
        a = validate_carrier_evidence(first)
        b = validate_carrier_evidence(second)
        self.assertIsInstance(a, CarrierEvidenceBundleV1)
        self.assertEqual(a.classification, "SYNTHETIC_ONLY")
        self.assertEqual(a.canonical_bytes, b.canonical_bytes)
        self.assertEqual(tuple(item.carrier_id for item in a.carriers), ("BCS0", "LC0"))

    def test_shared_kelly_receipt_hash_or_distribution_is_rejected(self) -> None:
        for shared_field in (
            "evidence_receipt_id",
            "evidence_sha256",
            "distribution_id",
            "distribution_sha256",
            "input_sha256",
        ):
            with self.subTest(shared_field=shared_field):
                bundle = _valid_carrier_evidence()
                carriers = bundle["carriers"]
                self.assertIs(type(carriers), list)
                first = carriers[0]
                second = carriers[1]
                self.assertIs(type(first), dict)
                self.assertIs(type(second), dict)
                if shared_field in {"distribution_id", "distribution_sha256"}:
                    first_binding = first["distribution_binding"]
                    second_binding = second["distribution_binding"]
                    self.assertIs(type(first_binding), dict)
                    self.assertIs(type(second_binding), dict)
                    second_binding[shared_field] = first_binding[shared_field]
                    _refresh_evidence_hash(bundle, 1)
                elif shared_field == "evidence_sha256":
                    second[shared_field] = first[shared_field]
                    hashes = bundle["content_hashes"]
                    self.assertIs(type(hashes), dict)
                    hashes["BCS0"] = second[shared_field]
                else:
                    second[shared_field] = first[shared_field]
                    _refresh_evidence_hash(bundle, 1)
                with self.assertRaisesRegex(
                    DataContractError, "CARRIER_EVIDENCE_NOT_INDEPENDENT"
                ):
                    validate_carrier_evidence(bundle)

    def test_opaque_only_and_unbound_distribution_are_rejected(self) -> None:
        opaque = _valid_carrier_evidence()
        carriers = opaque["carriers"]
        self.assertIs(type(carriers), list)
        carrier = carriers[0]
        self.assertIs(type(carrier), dict)
        carrier["episodes"] = []
        _refresh_evidence_hash(opaque, 0)
        with self.assertRaisesRegex(DataContractError, "CARRIER_EVIDENCE_OPAQUE_ONLY"):
            validate_carrier_evidence(opaque)

        unbound = _valid_carrier_evidence()
        carriers = unbound["carriers"]
        self.assertIs(type(carriers), list)
        carrier = carriers[0]
        self.assertIs(type(carrier), dict)
        del carrier["distribution_binding"]
        _refresh_evidence_hash(unbound, 0)
        with self.assertRaisesRegex(
            DataContractError, "CARRIER_DISTRIBUTION_BINDING_REQUIRED"
        ):
            validate_carrier_evidence(unbound)

    def test_evidence_hash_split_counts_and_half_kelly_are_verified(self) -> None:
        cases: list[tuple[str, dict[str, object]]] = []
        bad_hash = _valid_carrier_evidence()
        carriers = bad_hash["carriers"]
        self.assertIs(type(carriers), list)
        carriers[0]["evidence_sha256"] = "0" * 64
        bad_hash["content_hashes"]["LC0"] = "0" * 64
        cases.append(("CARRIER_EVIDENCE_HASH_MISMATCH", bad_hash))

        bad_split = _valid_carrier_evidence()
        carriers = bad_split["carriers"]
        self.assertIs(type(carriers), list)
        carriers[0]["splits"]["sealed_oos_count"] = 0
        _refresh_evidence_hash(bad_split, 0)
        cases.append(("CARRIER_EVIDENCE_SPLIT_MISMATCH", bad_split))

        bad_kelly = _valid_carrier_evidence()
        carriers = bad_kelly["carriers"]
        self.assertIs(type(carriers), list)
        carriers[0]["half_kelly_ppm"] = 60_001
        _refresh_evidence_hash(bad_kelly, 0)
        cases.append(("CARRIER_EVIDENCE_KELLY_INVALID", bad_kelly))

        for reason, document in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(
                DataContractError, reason
            ):
                validate_carrier_evidence(document)

    def test_expected_return_and_distribution_are_recomputed_from_episodes(self) -> None:
        bundle = _valid_carrier_evidence()
        carrier = bundle["carriers"][0]
        for episode in carrier["episodes"]:
            episode["after_cost_return_on_entry_debit_ppm"] = -1_000_000
        carrier["expected_net_return_on_entry_debit_ppm"] = 100_000_000
        _refresh_distribution_binding(bundle, 0)

        with self.assertRaisesRegex(
            DataContractError, "CARRIER_EXPECTED_RETURN_INVALID"
        ):
            validate_carrier_evidence(bundle)

        carrier["expected_net_return_on_entry_debit_ppm"] = -1_000_000
        _refresh_distribution_binding(bundle, 0)
        validate_carrier_evidence(bundle)

    def test_historical_candidate_and_self_reported_kelly_fail_closed(self) -> None:
        historical = _valid_carrier_evidence()
        historical["classification"] = "HISTORICAL_RESEARCH_CANDIDATE"
        with self.assertRaisesRegex(
            DataContractError,
            "CARRIER_HISTORICAL_ESTIMATOR_NOT_IMPLEMENTED",
        ):
            validate_carrier_evidence(historical)

        self_reported = _valid_carrier_evidence()
        carrier = self_reported["carriers"][0]
        carrier["full_kelly_ppm"] = 1_000_000
        carrier["robust_full_kelly_ppm"] = 1_000_000
        carrier["half_kelly_ppm"] = 500_000
        _refresh_evidence_hash(self_reported, 0)
        with self.assertRaisesRegex(
            DataContractError,
            "CARRIER_EVIDENCE_KELLY_NOT_ESTIMATED",
        ):
            validate_carrier_evidence(self_reported)


if __name__ == "__main__":
    unittest.main()
