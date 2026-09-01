"""Closed-schema validators for GLD entry facts and carrier evidence.

All normalization is deterministic: JSON object keys are canonicalized by the
encoder and every order-insensitive collection is explicitly sorted after
duplicate detection.  The functions use no clock, randomness, network, or
provider access.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time, timezone
import re
from types import MappingProxyType
from zoneinfo import ZoneInfo

from gld_simulation.canonical import canonical_json_bytes, canonical_json_sha256
from gld_simulation.errors import RawBundleError

from .contracts import (
    CARRIER_EVIDENCE_BUNDLE_SCHEMA_VERSION,
    DATA_NOT_QUALIFIED,
    DATA_QUALIFIED,
    ENTRY_FACT_BUNDLE_SCHEMA_VERSION,
    SOURCE_QUALIFICATION_RECEIPT_SCHEMA_VERSION,
    STRUCTURALLY_VALID_SYNTHETIC,
    CarrierEvidenceBundleV1,
    CarrierEvidenceV1,
    DataContractError,
    DataQualificationResultV1,
    EntryFactBundleV1,
    SourceQualificationReceiptV1,
    _VALIDATED_CONTRACT_SEAL,
)


MAX_I64 = 2**63 - 1
MIN_I64 = -(2**63)
MAX_CANONICAL_INTEGER = 10**19 - 1
PPM = 1_000_000
DAY_NS = 86_400_000_000_000
SECOND_NS = 1_000_000_000
XNYS_TIMEZONE = ZoneInfo("America/New_York")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_OCC_SUFFIX_RE = re.compile(r"(\d{6})([CP])(\d{8})\Z")
SYNTHETIC_CARRIER_ESTIMATOR_VERSION = (
    "SYNTHETIC_ALL_EPISODE_ARITHMETIC_MEAN_V1"
)
SYNTHETIC_CARRIER_UNCERTAINTY_METHOD = (
    "NOT_ESTIMATED_SYNTHETIC_ONLY_V1"
)
SYNTHETIC_CARRIER_ESTIMATOR_PACKAGE_SHA256 = canonical_json_sha256(
    {
        "schema_version": "CARRIER_ESTIMATOR_PACKAGE_V1",
        "classification": "SYNTHETIC_ONLY",
        "expected_return_rule": SYNTHETIC_CARRIER_ESTIMATOR_VERSION,
        "uncertainty_method": SYNTHETIC_CARRIER_UNCERTAINTY_METHOD,
        "kelly_rule": "ZERO_NOT_ESTIMATED",
    }
)

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

_ENTRY_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "scope",
        "bundle_id",
        "underlying",
        "trading_date",
        "cutoff_utc_ns",
        *ENTRY_HASH_DOMAINS,
        "content_hashes",
    }
)
_CONTENT_HASH_KEYS = frozenset(
    f"{domain}_sha256" for domain in ENTRY_HASH_DOMAINS
)

_CALENDAR_KEYS = frozenset(
    {
        "schema_version",
        "source_receipt_id",
        "timezone",
        "decision_session_ordinal",
        "sessions",
    }
)
_SESSION_KEYS = frozenset(
    {
        "session_ordinal",
        "trading_date",
        "open_utc_ns",
        "close_utc_ns",
        "is_full_session",
    }
)
_BAR_CONTAINER_KEYS = frozenset(
    {"schema_version", "source_receipt_id", "price_scale", "bars"}
)
_DAILY_BAR_KEYS = frozenset(
    {
        "session_ordinal",
        "trading_date",
        "open_nano_usd",
        "high_nano_usd",
        "low_nano_usd",
        "close_nano_usd",
        "volume",
        "complete",
        "max_event_utc_ns",
        "max_receive_utc_ns",
    }
)
_MINUTE_BAR_KEYS = frozenset(
    {
        "session_ordinal",
        "trading_date",
        "minute_ending_ordinal",
        "start_utc_ns",
        "end_utc_ns",
        "open_nano_usd",
        "high_nano_usd",
        "low_nano_usd",
        "close_nano_usd",
        "volume",
        "complete",
        "max_event_utc_ns",
        "max_receive_utc_ns",
    }
)
_MARKET_STATUS_KEYS = frozenset(
    {
        "schema_version",
        "source_receipt_id",
        "trading_date",
        "as_of_utc_ns",
        "market_phase",
        "complete_through_utc_ns",
        "daily_history_complete",
        "intraday_history_complete",
        "option_universe_complete",
        "quote_snapshot_complete",
        "calendar_complete",
        "causal_cutoff_enforced",
    }
)
_OPTION_SNAPSHOT_KEYS = frozenset(
    {
        "schema_version",
        "source_receipt_id",
        "snapshot_id",
        "capture_utc_ns",
        "window_start_utc_ns",
        "window_end_utc_ns",
        "universe_complete",
        "underlying_bbo",
        "option_quotes",
    }
)
_OPTION_QUOTE_KEYS = frozenset({"contract", "top_of_book"})
_CONTRACT_KEYS = frozenset(
    {
        "contract_id",
        "occ_symbol",
        "underlying",
        "option_type",
        "strike_nano_usd",
        "expiry_date",
        "last_trading_date",
        "expiry_utc_ns",
        "last_trading_utc_ns",
        "activation_utc_ns",
        "multiplier",
        "deliverable_shares",
        "deliverable",
        "currency",
        "exchange",
        "tick_nano_usd",
        "standard_unadjusted",
        "exercise_style",
    }
)
_BBO_KEYS = frozenset(
    {
        "instrument_id",
        "bid_nano_usd",
        "ask_nano_usd",
        "bid_size",
        "ask_size",
        "tick_nano_usd",
        "event_utc_ns",
        "receive_utc_ns",
        "flags",
    }
)
_PIT_KEYS = frozenset(
    {
        "schema_version",
        "source_receipt_ids",
        "as_of_utc_ns",
        "rate_curve",
        "expense_yield_ppm",
        "distribution_yield_ppm",
        "borrow_available",
        "borrow_rate_ppm",
    }
)
_RATE_POINT_KEYS = frozenset({"tenor_days", "zero_rate_ppm"})
_FEE_KEYS = frozenset(
    {
        "schema_version",
        "source_receipt_id",
        "effective_from_utc_ns",
        "effective_to_utc_ns",
        "currency",
        "broker_schedule_id",
        "exchange_schedule_id",
        "clearing_schedule_id",
        "regulatory_schedule_id",
        "long_entry_fee_nano_usd_per_contract",
        "long_exit_fee_nano_usd_per_contract",
        "short_entry_fee_nano_usd_per_contract",
        "short_exit_fee_nano_usd_per_contract",
    }
)
_ACCOUNT_KEYS = frozenset(
    {
        "schema_version",
        "source_receipt_id",
        "as_of_utc_ns",
        "currency",
        "net_liquidation_value_nano_usd",
        "settled_cash_nano_usd",
        "strategy_bankroll_nano_usd",
        "strategy_high_watermark_nano_usd",
        "realized_profit_nano_usd",
        "realized_loss_nano_usd",
        "current_gld_delta_exposure_nano_usd",
        "positions",
        "open_orders",
    }
)
_POSITION_KEYS = frozenset(
    {
        "position_id",
        "instrument_id",
        "signed_contract_count",
        "multiplier",
        "currency",
    }
)
_OPEN_ORDER_KEYS = frozenset(
    {
        "order_id",
        "instrument_id",
        "remaining_contract_count",
        "side",
        "limit_nano_usd",
        "status",
    }
)
_RULE_KEYS = frozenset(
    {
        "schema_version",
        "package_id",
        "version",
        "effective_from_utc_ns",
        "cutoff_minute_ending_ordinal",
        "option_window_duration_ns",
        "max_quote_age_ns",
        "max_cross_leg_receive_skew_ns",
        "max_account_age_ns",
        "min_quote_size",
        "min_history_sessions",
        "entry_stress_long_ticks",
        "entry_stress_short_ticks",
        "realized_profit_reinvestment_ppm",
        "realized_loss_effect_ppm",
        "minimum_cash_reserve_nlv_ppm",
    }
)
_MODEL_KEYS = frozenset(
    {
        "schema_version",
        "package_id",
        "version",
        "formula_id",
        "coarse_steps",
        "fine_steps",
        "iv_iterations",
        "rounding_mode",
        "source_code_sha256",
        "runtime_fingerprint_sha256",
    }
)
_SOURCE_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "receipt_id",
        "provider_id",
        "provider_version",
        "evidence_kind",
        "entitlement_status",
        "field_coverage_ppm",
        "complete_universe_supported",
        "event_time_semantics",
        "receive_time_semantics",
        "atomic_snapshot_supported",
        "synchronization_max_skew_ns",
        "effective_from_utc_ns",
        "effective_to_utc_ns",
        "covered_domains",
        "evidence_sha256",
    }
)

_FORBIDDEN_DERIVED_NORMALIZED_KEYS = frozenset(
    {
        "sma",
        "sma50",
        "sma200",
        "sma50nanousd",
        "sma200nanousd",
        "iv",
        "ivppm",
        "impliedvolatility",
        "impliedvolatilityppm",
        "delta",
        "deltappm",
        "coarsedeltappm",
        "finedeltappm",
        "winner",
        "quantity",
        "preference",
        "decisionresult",
        "signalstate",
        "carrierpass",
        "carrierfail",
    }
)
_DERIVED_KEY_EXEMPTIONS = frozenset(
    {"currentglddeltaexposurenanousd"}
)

_CARRIER_BUNDLE_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "scope",
        "underlying",
        "rule_package_sha256",
        "estimator_package_sha256",
        "carriers",
        "content_hashes",
    }
)
_CARRIER_KEYS = frozenset(
    {
        "carrier_id",
        "evidence_receipt_id",
        "episode_cohort_id",
        "entry_clock_id",
        "exit_clock_id",
        "cost_model_version",
        "rule_version",
        "episodes",
        "coverage",
        "splits",
        "distribution_binding",
        "expected_net_return_on_entry_debit_ppm",
        "uncertainty_method",
        "full_kelly_ppm",
        "robust_full_kelly_ppm",
        "half_kelly_ppm",
        "estimator_version",
        "fee_schedule_sha256",
        "exit_policy_sha256",
        "input_sha256",
        "evidence_sha256",
    }
)
_EPISODE_KEYS = frozenset(
    {
        "episode_id",
        "entry_utc_ns",
        "exit_utc_ns",
        "entry_debit_nano_usd",
        "after_cost_return_on_entry_debit_ppm",
        "split",
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
_SPLIT_KEYS = frozenset(
    {"development_count", "walk_forward_count", "sealed_oos_count"}
)
_DISTRIBUTION_KEYS = frozenset(
    {
        "distribution_id",
        "return_unit",
        "episode_set_sha256",
        "distribution_sha256",
    }
)


def _canonical_bytes(value: object) -> bytes:
    try:
        return canonical_json_bytes(value)
    except RawBundleError as exc:
        raise DataContractError(exc.reason_code, exc.detail) from exc


def _canonical_sha256(value: object) -> str:
    try:
        return canonical_json_sha256(value)
    except RawBundleError as exc:
        raise DataContractError(exc.reason_code, exc.detail) from exc


def _object(value: object, reason: str) -> dict[str, object]:
    if type(value) is not dict:
        raise DataContractError(reason)
    return value


def _closed(
    value: object, keys: frozenset[str], reason: str
) -> dict[str, object]:
    result = _object(value, reason)
    if set(result) != keys:
        raise DataContractError(reason)
    return result


def _list(value: object, reason: str) -> list[object]:
    if type(value) is not list:
        raise DataContractError(reason)
    return value


def _string(
    value: object,
    reason: str,
    *,
    allowed: frozenset[str] | None = None,
    maximum: int = 256,
) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or not value.isascii()
        or (allowed is not None and value not in allowed)
    ):
        raise DataContractError(reason)
    return value


def _identifier(
    value: object,
    reason: str,
    *,
    maximum: int = 128,
) -> str:
    result = _string(value, reason, maximum=maximum)
    if _ID_RE.fullmatch(result) is None:
        raise DataContractError(reason)
    return result


def _ascii_text(value: object, reason: str, *, maximum: int = 256) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or not value.isascii()
        or value != value.strip()
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in value)
    ):
        raise DataContractError(reason)
    return value


def _sha256(value: object, reason: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise DataContractError(reason)
    return value


def _integer(
    value: object,
    reason: str,
    *,
    minimum: int = 0,
    maximum: int = MAX_I64,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise DataContractError(reason)
    return value


def _optional_integer(
    value: object,
    reason: str,
    *,
    minimum: int = 0,
    maximum: int = MAX_I64,
) -> int | None:
    if value is None:
        return None
    return _integer(value, reason, minimum=minimum, maximum=maximum)


def _boolean(value: object, reason: str) -> bool:
    if type(value) is not bool:
        raise DataContractError(reason)
    return value


def _date(value: object, reason: str) -> str:
    result = _string(value, reason, maximum=10)
    if _DATE_RE.fullmatch(result) is None:
        raise DataContractError(reason)
    try:
        parsed = datetime.strptime(result, "%Y-%m-%d").date()
    except ValueError as exc:
        raise DataContractError(reason) from exc
    if parsed.isoformat() != result:
        raise DataContractError(reason)
    return result


def _utc_date(value: int) -> str:
    try:
        return datetime.fromtimestamp(
            value // 1_000_000_000, tz=timezone.utc
        ).date().isoformat()
    except (OverflowError, OSError, ValueError) as exc:
        raise DataContractError("UTC_NS_OUT_OF_RANGE") from exc


def _xnys_local_datetime(value: int, reason: str) -> datetime:
    if value % SECOND_NS != 0:
        raise DataContractError(reason)
    try:
        return datetime.fromtimestamp(
            value // SECOND_NS,
            tz=timezone.utc,
        ).astimezone(XNYS_TIMEZONE)
    except (OverflowError, OSError, ValueError) as exc:
        raise DataContractError(reason) from exc


def _verify_occ_identity(
    occ_symbol: str,
    *,
    expiry_date: str,
    strike_nano_usd: int,
) -> None:
    match = _OCC_SUFFIX_RE.search(occ_symbol)
    if match is None:
        raise DataContractError("ENTRY_OPTION_OCC_IDENTITY_MISMATCH")
    root = occ_symbol[: match.start()].rstrip()
    if root != "GLD" or match.group(2) != "C":
        raise DataContractError("ENTRY_OPTION_OCC_IDENTITY_MISMATCH")
    try:
        occ_expiry = datetime.strptime(match.group(1), "%y%m%d").date()
    except ValueError as exc:
        raise DataContractError("ENTRY_OPTION_OCC_IDENTITY_MISMATCH") from exc
    occ_strike_nano_usd = int(match.group(3)) * 1_000_000
    if (
        occ_expiry.isoformat() != expiry_date
        or occ_strike_nano_usd != strike_nano_usd
    ):
        raise DataContractError("ENTRY_OPTION_OCC_IDENTITY_MISMATCH")


def _normalized_key(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _reject_entry_derived_fields(value: object) -> None:
    if type(value) is dict:
        for key, item in value.items():
            normalized = _normalized_key(key)
            if (
                normalized not in _DERIVED_KEY_EXEMPTIONS
                and (
                    normalized in _FORBIDDEN_DERIVED_NORMALIZED_KEYS
                    or normalized.startswith("sma50")
                    or normalized.startswith("sma200")
                    or normalized.startswith("impliedvolatility")
                    or normalized.startswith("coarsedelta")
                    or normalized.startswith("finedelta")
                    or "decisionresult" in normalized
                    or "preference" in normalized
                    or "winner" in normalized
                    or "quantity" in normalized
                )
            ):
                raise DataContractError(
                    "ENTRY_DERIVED_FIELD_FORBIDDEN", key
                )
            _reject_entry_derived_fields(item)
    elif type(value) is list:
        for item in value:
            _reject_entry_derived_fields(item)


def _sort_unique(
    values: list[dict[str, object]],
    *,
    key: str,
    reason: str,
) -> list[dict[str, object]]:
    ordered = sorted(values, key=lambda item: item[key])
    if any(
        ordered[index - 1][key] == ordered[index][key]
        for index in range(1, len(ordered))
    ):
        raise DataContractError(reason)
    return ordered


def _normalize_source_receipt(
    value: object, *, cutoff_utc_ns: int
) -> tuple[dict[str, object], SourceQualificationReceiptV1]:
    raw = _closed(value, _SOURCE_RECEIPT_KEYS, "SOURCE_RECEIPT_SCHEMA_INVALID")
    if raw["schema_version"] != SOURCE_QUALIFICATION_RECEIPT_SCHEMA_VERSION:
        raise DataContractError("SOURCE_RECEIPT_VERSION_UNSUPPORTED")
    receipt_id = _identifier(raw["receipt_id"], "SOURCE_RECEIPT_ID_INVALID")
    provider_id = _identifier(raw["provider_id"], "SOURCE_PROVIDER_ID_INVALID")
    provider_version = _identifier(
        raw["provider_version"], "SOURCE_PROVIDER_VERSION_INVALID"
    )
    evidence_kind = _string(
        raw["evidence_kind"],
        "SOURCE_EVIDENCE_KIND_INVALID",
        allowed=frozenset(
            {"SYNTHETIC_GENERATOR", "PROVIDER_CAPABILITY_RECEIPT"}
        ),
    )
    entitlement_status = _string(
        raw["entitlement_status"],
        "SOURCE_ENTITLEMENT_STATUS_INVALID",
        allowed=frozenset(
            {"NOT_APPLICABLE_SYNTHETIC", "VERIFIED", "NOT_VERIFIED"}
        ),
    )
    field_coverage_ppm = _integer(
        raw["field_coverage_ppm"],
        "SOURCE_FIELD_COVERAGE_INVALID",
        maximum=PPM,
    )
    complete_universe_supported = _boolean(
        raw["complete_universe_supported"],
        "SOURCE_UNIVERSE_CAPABILITY_INVALID",
    )
    event_time_semantics = _identifier(
        raw["event_time_semantics"], "SOURCE_TIME_SEMANTICS_INVALID"
    )
    receive_time_semantics = _identifier(
        raw["receive_time_semantics"], "SOURCE_TIME_SEMANTICS_INVALID"
    )
    atomic_snapshot_supported = _boolean(
        raw["atomic_snapshot_supported"],
        "SOURCE_ATOMIC_CAPABILITY_INVALID",
    )
    synchronization_max_skew_ns = _integer(
        raw["synchronization_max_skew_ns"],
        "SOURCE_SYNCHRONIZATION_INVALID",
    )
    effective_from = _integer(
        raw["effective_from_utc_ns"], "SOURCE_EFFECTIVE_TIME_INVALID"
    )
    effective_to = _optional_integer(
        raw["effective_to_utc_ns"], "SOURCE_EFFECTIVE_TIME_INVALID"
    )
    if (
        effective_from > cutoff_utc_ns
        or (effective_to is not None and cutoff_utc_ns >= effective_to)
    ):
        raise DataContractError("SOURCE_RECEIPT_NOT_EFFECTIVE")
    domains_raw = _list(raw["covered_domains"], "SOURCE_DOMAINS_INVALID")
    domains = sorted(
        _identifier(item, "SOURCE_DOMAIN_INVALID") for item in domains_raw
    )
    if not domains or len(domains) != len(set(domains)):
        raise DataContractError("SOURCE_DOMAINS_INVALID")
    evidence_sha256 = _sha256(
        raw["evidence_sha256"], "SOURCE_EVIDENCE_HASH_INVALID"
    )
    normalized = {
        "schema_version": SOURCE_QUALIFICATION_RECEIPT_SCHEMA_VERSION,
        "receipt_id": receipt_id,
        "provider_id": provider_id,
        "provider_version": provider_version,
        "evidence_kind": evidence_kind,
        "entitlement_status": entitlement_status,
        "field_coverage_ppm": field_coverage_ppm,
        "complete_universe_supported": complete_universe_supported,
        "event_time_semantics": event_time_semantics,
        "receive_time_semantics": receive_time_semantics,
        "atomic_snapshot_supported": atomic_snapshot_supported,
        "synchronization_max_skew_ns": synchronization_max_skew_ns,
        "effective_from_utc_ns": effective_from,
        "effective_to_utc_ns": effective_to,
        "covered_domains": domains,
        "evidence_sha256": evidence_sha256,
    }
    typed = SourceQualificationReceiptV1(
        receipt_id=receipt_id,
        provider_id=provider_id,
        provider_version=provider_version,
        evidence_kind=evidence_kind,
        entitlement_status=entitlement_status,
        field_coverage_ppm=field_coverage_ppm,
        complete_universe_supported=complete_universe_supported,
        event_time_semantics=event_time_semantics,
        receive_time_semantics=receive_time_semantics,
        atomic_snapshot_supported=atomic_snapshot_supported,
        synchronization_max_skew_ns=synchronization_max_skew_ns,
        effective_from_utc_ns=effective_from,
        effective_to_utc_ns=effective_to,
        covered_domains=tuple(domains),
        evidence_sha256=evidence_sha256,
        receipt_sha256=_canonical_sha256(normalized),
        _validation_seal=_VALIDATED_CONTRACT_SEAL,
    )
    return normalized, typed


def _normalize_calendar(
    value: object, *, trading_date: str, cutoff_utc_ns: int
) -> dict[str, object]:
    raw = _closed(value, _CALENDAR_KEYS, "ENTRY_CALENDAR_SCHEMA_INVALID")
    if raw["schema_version"] != "XNYS_CALENDAR_FACTS_V1":
        raise DataContractError("ENTRY_CALENDAR_VERSION_UNSUPPORTED")
    source_receipt_id = _identifier(
        raw["source_receipt_id"], "ENTRY_SOURCE_REFERENCE_INVALID"
    )
    if raw["timezone"] != "America/New_York":
        raise DataContractError("ENTRY_CALENDAR_TIMEZONE_INVALID")
    decision_ordinal = _integer(
        raw["decision_session_ordinal"],
        "ENTRY_DECISION_SESSION_INVALID",
        minimum=1,
        maximum=2**32 - 1,
    )
    sessions_raw = _list(raw["sessions"], "ENTRY_CALENDAR_SESSIONS_INVALID")
    sessions: list[dict[str, object]] = []
    for item in sessions_raw:
        session = _closed(item, _SESSION_KEYS, "ENTRY_CALENDAR_SESSION_INVALID")
        ordinal = _integer(
            session["session_ordinal"],
            "ENTRY_CALENDAR_SESSION_INVALID",
            minimum=1,
            maximum=2**32 - 1,
        )
        session_date = _date(
            session["trading_date"], "ENTRY_CALENDAR_SESSION_INVALID"
        )
        open_ns = _integer(
            session["open_utc_ns"], "ENTRY_CALENDAR_SESSION_INVALID"
        )
        close_ns = _integer(
            session["close_utc_ns"], "ENTRY_CALENDAR_SESSION_INVALID"
        )
        full = _boolean(
            session["is_full_session"], "ENTRY_CALENDAR_SESSION_INVALID"
        )
        local_open = _xnys_local_datetime(
            open_ns, "ENTRY_CALENDAR_SESSION_INVALID"
        )
        local_close = _xnys_local_datetime(
            close_ns, "ENTRY_CALENDAR_SESSION_INVALID"
        )
        if (
            open_ns >= close_ns
            or local_open.date().isoformat() != session_date
            or local_close.date().isoformat() != session_date
            or local_open.weekday() >= 5
            or local_open.time() != time(9, 30)
        ):
            raise DataContractError("ENTRY_CALENDAR_SESSION_INVALID")
        sessions.append(
            {
                "session_ordinal": ordinal,
                "trading_date": session_date,
                "open_utc_ns": open_ns,
                "close_utc_ns": close_ns,
                "is_full_session": full,
            }
        )
    sessions = _sort_unique(
        sessions,
        key="session_ordinal",
        reason="ENTRY_CALENDAR_SESSION_DUPLICATE",
    )
    if len({item["trading_date"] for item in sessions}) != len(sessions):
        raise DataContractError("ENTRY_CALENDAR_DATE_DUPLICATE")
    for previous, current in zip(sessions, sessions[1:]):
        if (
            str(previous["trading_date"]) >= str(current["trading_date"])
            or int(previous["open_utc_ns"]) >= int(current["open_utc_ns"])
            or int(previous["close_utc_ns"]) >= int(current["close_utc_ns"])
        ):
            raise DataContractError("ENTRY_CALENDAR_ORDER_INVALID")
    decision = next(
        (
            session
            for session in sessions
            if session["session_ordinal"] == decision_ordinal
        ),
        None,
    )
    cutoff_local = _xnys_local_datetime(
        cutoff_utc_ns, "ENTRY_DECISION_SESSION_INVALID"
    )
    if (
        decision is None
        or decision["trading_date"] != trading_date
        or cutoff_local.date().isoformat() != trading_date
        or cutoff_local.time() != time(10, 45)
        or not int(decision["open_utc_ns"]) < cutoff_utc_ns < int(decision["close_utc_ns"])
        or cutoff_utc_ns - int(decision["open_utc_ns"]) != 75 * 60_000_000_000
    ):
        raise DataContractError("ENTRY_DECISION_SESSION_INVALID")
    prior_count = sum(
        int(item["session_ordinal"]) < decision_ordinal for item in sessions
    )
    future_count = sum(
        int(item["session_ordinal"]) > decision_ordinal for item in sessions
    )
    if prior_count < 220 or future_count < 20:
        raise DataContractError("ENTRY_CALENDAR_COVERAGE_INCOMPLETE")
    return {
        "schema_version": "XNYS_CALENDAR_FACTS_V1",
        "source_receipt_id": source_receipt_id,
        "timezone": "America/New_York",
        "decision_session_ordinal": decision_ordinal,
        "sessions": sessions,
    }


def _normalize_ohlcv(
    raw: dict[str, object],
    *,
    reason: str,
) -> tuple[int, int, int, int, int]:
    open_price = _integer(raw["open_nano_usd"], reason, minimum=1)
    high = _integer(raw["high_nano_usd"], reason, minimum=1)
    low = _integer(raw["low_nano_usd"], reason, minimum=1)
    close = _integer(raw["close_nano_usd"], reason, minimum=1)
    volume = _integer(raw["volume"], reason)
    if high < max(open_price, close) or low > min(open_price, close) or low > high:
        raise DataContractError(reason)
    return open_price, high, low, close, volume


def _normalize_daily_bars(
    value: object,
    *,
    cutoff_utc_ns: int,
    decision_ordinal: int,
    calendar_by_ordinal: dict[int, dict[str, object]],
    min_history_sessions: int,
) -> dict[str, object]:
    raw = _closed(value, _BAR_CONTAINER_KEYS, "ENTRY_DAILY_BARS_SCHEMA_INVALID")
    if raw["schema_version"] != "GLD_DAILY_OHLCV_FACTS_V1" or raw["price_scale"] != "NANO_USD":
        raise DataContractError("ENTRY_DAILY_BARS_VERSION_UNSUPPORTED")
    source_receipt_id = _identifier(
        raw["source_receipt_id"], "ENTRY_SOURCE_REFERENCE_INVALID"
    )
    bars_raw = _list(raw["bars"], "ENTRY_DAILY_BARS_INVALID")
    bars: list[dict[str, object]] = []
    for item in bars_raw:
        bar = _closed(item, _DAILY_BAR_KEYS, "ENTRY_DAILY_BAR_INVALID")
        ordinal = _integer(
            bar["session_ordinal"],
            "ENTRY_DAILY_BAR_INVALID",
            minimum=1,
            maximum=2**32 - 1,
        )
        trading_date = _date(bar["trading_date"], "ENTRY_DAILY_BAR_INVALID")
        open_price, high, low, close, volume = _normalize_ohlcv(
            bar, reason="ENTRY_DAILY_BAR_INVALID"
        )
        complete = _boolean(bar["complete"], "ENTRY_DAILY_BAR_INVALID")
        event_ns = _integer(
            bar["max_event_utc_ns"], "ENTRY_DAILY_BAR_INVALID"
        )
        receive_ns = _integer(
            bar["max_receive_utc_ns"], "ENTRY_DAILY_BAR_INVALID"
        )
        calendar_session = calendar_by_ordinal.get(ordinal)
        if (
            calendar_session is None
            or calendar_session["trading_date"] != trading_date
            or ordinal >= decision_ordinal
            or not complete
            or event_ns < int(calendar_session["open_utc_ns"])
            or event_ns > int(calendar_session["close_utc_ns"])
            or receive_ns < event_ns
            or receive_ns > cutoff_utc_ns
        ):
            raise DataContractError("ENTRY_DAILY_BAR_INVALID")
        bars.append(
            {
                "session_ordinal": ordinal,
                "trading_date": trading_date,
                "open_nano_usd": open_price,
                "high_nano_usd": high,
                "low_nano_usd": low,
                "close_nano_usd": close,
                "volume": volume,
                "complete": complete,
                "max_event_utc_ns": event_ns,
                "max_receive_utc_ns": receive_ns,
            }
        )
    bars = _sort_unique(
        bars,
        key="session_ordinal",
        reason="ENTRY_DAILY_BAR_DUPLICATE",
    )
    if len(bars) < min_history_sessions:
        raise DataContractError("ENTRY_DAILY_HISTORY_INCOMPLETE")
    required_ordinals = {
        int(item["session_ordinal"])
        for item in sorted(
            (
                item
                for item in calendar_by_ordinal.values()
                if int(item["session_ordinal"]) < decision_ordinal
            ),
            key=lambda item: int(item["session_ordinal"]),
        )[-min_history_sessions:]
    }
    if not required_ordinals.issubset(
        {int(item["session_ordinal"]) for item in bars}
    ):
        raise DataContractError("ENTRY_DAILY_HISTORY_INCOMPLETE")
    return {
        "schema_version": "GLD_DAILY_OHLCV_FACTS_V1",
        "source_receipt_id": source_receipt_id,
        "price_scale": "NANO_USD",
        "bars": bars,
    }


def _normalize_minute_bars(
    value: object,
    *,
    trading_date: str,
    cutoff_utc_ns: int,
    decision_ordinal: int,
) -> dict[str, object]:
    raw = _closed(value, _BAR_CONTAINER_KEYS, "ENTRY_MINUTE_BARS_SCHEMA_INVALID")
    if raw["schema_version"] != "GLD_MINUTE_OHLCV_FACTS_V1" or raw["price_scale"] != "NANO_USD":
        raise DataContractError("ENTRY_MINUTE_BARS_VERSION_UNSUPPORTED")
    source_receipt_id = _identifier(
        raw["source_receipt_id"], "ENTRY_SOURCE_REFERENCE_INVALID"
    )
    bars_raw = _list(raw["bars"], "ENTRY_MINUTE_BARS_INVALID")
    bars: list[dict[str, object]] = []
    for item in bars_raw:
        bar = _closed(item, _MINUTE_BAR_KEYS, "ENTRY_MINUTE_BAR_INVALID")
        ordinal = _integer(
            bar["session_ordinal"], "ENTRY_MINUTE_BAR_INVALID", minimum=1
        )
        bar_date = _date(bar["trading_date"], "ENTRY_MINUTE_BAR_INVALID")
        minute_ordinal = _integer(
            bar["minute_ending_ordinal"],
            "ENTRY_MINUTE_BAR_INVALID",
            maximum=1_439,
        )
        start_ns = _integer(bar["start_utc_ns"], "ENTRY_MINUTE_BAR_INVALID")
        end_ns = _integer(bar["end_utc_ns"], "ENTRY_MINUTE_BAR_INVALID")
        open_price, high, low, close, volume = _normalize_ohlcv(
            bar, reason="ENTRY_MINUTE_BAR_INVALID"
        )
        complete = _boolean(bar["complete"], "ENTRY_MINUTE_BAR_INVALID")
        event_ns = _integer(
            bar["max_event_utc_ns"], "ENTRY_MINUTE_BAR_INVALID"
        )
        receive_ns = _integer(
            bar["max_receive_utc_ns"], "ENTRY_MINUTE_BAR_INVALID"
        )
        local_start = _xnys_local_datetime(
            start_ns, "ENTRY_MINUTE_TIME_ALIGNMENT_INVALID"
        )
        if (
            ordinal != decision_ordinal
            or bar_date != trading_date
            or end_ns - start_ns != 60_000_000_000
            or end_ns > cutoff_utc_ns
            or local_start.date().isoformat() != bar_date
            or local_start.hour * 60 + local_start.minute != minute_ordinal
            or local_start.second != 0
            or not complete
            or event_ns < start_ns
            or event_ns > end_ns
            or receive_ns < event_ns
            or receive_ns > cutoff_utc_ns
        ):
            raise DataContractError("ENTRY_MINUTE_BAR_INVALID")
        if 630 <= minute_ordinal <= 644:
            expected_end = cutoff_utc_ns - (644 - minute_ordinal) * 60_000_000_000
            if end_ns != expected_end:
                raise DataContractError("ENTRY_MINUTE_TIME_ALIGNMENT_INVALID")
        bars.append(
            {
                "session_ordinal": ordinal,
                "trading_date": bar_date,
                "minute_ending_ordinal": minute_ordinal,
                "start_utc_ns": start_ns,
                "end_utc_ns": end_ns,
                "open_nano_usd": open_price,
                "high_nano_usd": high,
                "low_nano_usd": low,
                "close_nano_usd": close,
                "volume": volume,
                "complete": complete,
                "max_event_utc_ns": event_ns,
                "max_receive_utc_ns": receive_ns,
            }
        )
    bars = _sort_unique(
        bars,
        key="minute_ending_ordinal",
        reason="ENTRY_MINUTE_BAR_DUPLICATE",
    )
    required_ordinals = set(range(630, 645))
    actual_ordinals = {
        int(item["minute_ending_ordinal"])
        for item in bars
        if int(item["minute_ending_ordinal"]) in required_ordinals
    }
    if actual_ordinals != required_ordinals:
        raise DataContractError("ENTRY_INTRADAY_CONFIRMATION_INCOMPLETE")
    return {
        "schema_version": "GLD_MINUTE_OHLCV_FACTS_V1",
        "source_receipt_id": source_receipt_id,
        "price_scale": "NANO_USD",
        "bars": bars,
    }


def _normalize_market_status(
    value: object, *, trading_date: str, cutoff_utc_ns: int
) -> dict[str, object]:
    raw = _closed(
        value, _MARKET_STATUS_KEYS, "ENTRY_MARKET_STATUS_SCHEMA_INVALID"
    )
    if raw["schema_version"] != "MARKET_STATUS_FACTS_V1":
        raise DataContractError("ENTRY_MARKET_STATUS_VERSION_UNSUPPORTED")
    source_receipt_id = _identifier(
        raw["source_receipt_id"], "ENTRY_SOURCE_REFERENCE_INVALID"
    )
    status_date = _date(
        raw["trading_date"], "ENTRY_MARKET_STATUS_INVALID"
    )
    as_of = _integer(raw["as_of_utc_ns"], "ENTRY_MARKET_STATUS_INVALID")
    phase = _string(
        raw["market_phase"],
        "ENTRY_MARKET_PHASE_INVALID",
        allowed=frozenset({"REGULAR_TRADING"}),
    )
    complete_through = _integer(
        raw["complete_through_utc_ns"], "ENTRY_MARKET_STATUS_INVALID"
    )
    flags = {
        key: _boolean(raw[key], "ENTRY_MARKET_STATUS_INVALID")
        for key in (
            "daily_history_complete",
            "intraday_history_complete",
            "option_universe_complete",
            "quote_snapshot_complete",
            "calendar_complete",
            "causal_cutoff_enforced",
        )
    }
    if not flags["option_universe_complete"]:
        raise DataContractError("ENTRY_OPTION_UNIVERSE_INCOMPLETE")
    if not flags["quote_snapshot_complete"]:
        raise DataContractError("ENTRY_QUOTE_SNAPSHOT_INCOMPLETE")
    if (
        status_date != trading_date
        or as_of != cutoff_utc_ns
        or complete_through != cutoff_utc_ns
        or not all(flags.values())
    ):
        raise DataContractError("ENTRY_MARKET_STATUS_INCOMPLETE")
    return {
        "schema_version": "MARKET_STATUS_FACTS_V1",
        "source_receipt_id": source_receipt_id,
        "trading_date": status_date,
        "as_of_utc_ns": as_of,
        "market_phase": phase,
        "complete_through_utc_ns": complete_through,
        **flags,
    }


def _normalize_bbo(
    value: object,
    *,
    capture_utc_ns: int,
    window_start_utc_ns: int,
    window_end_utc_ns: int,
    max_quote_age_ns: int,
    min_quote_size: int,
) -> dict[str, object]:
    raw = _closed(value, _BBO_KEYS, "ENTRY_BBO_SCHEMA_INVALID")
    instrument_id = _identifier(raw["instrument_id"], "ENTRY_BBO_INVALID")
    bid = _integer(raw["bid_nano_usd"], "ENTRY_BBO_INVALID", minimum=1)
    ask = _integer(raw["ask_nano_usd"], "ENTRY_BBO_INVALID", minimum=1)
    bid_size = _integer(raw["bid_size"], "ENTRY_BBO_INVALID")
    ask_size = _integer(raw["ask_size"], "ENTRY_BBO_INVALID")
    tick = _integer(raw["tick_nano_usd"], "ENTRY_BBO_INVALID", minimum=1)
    event_ns = _integer(raw["event_utc_ns"], "ENTRY_BBO_INVALID")
    receive_ns = _integer(raw["receive_utc_ns"], "ENTRY_BBO_INVALID")
    flags_raw = _list(raw["flags"], "ENTRY_BBO_INVALID")
    flags = sorted(_identifier(item, "ENTRY_BBO_INVALID") for item in flags_raw)
    if len(flags) != len(set(flags)):
        raise DataContractError("ENTRY_BBO_FLAG_DUPLICATE")
    if (
        bid > ask
        or bid_size < min_quote_size
        or ask_size < min_quote_size
        or bid % tick != 0
        or ask % tick != 0
        or not window_start_utc_ns <= event_ns < window_end_utc_ns
        or not window_start_utc_ns <= receive_ns < window_end_utc_ns
        or receive_ns < event_ns
        or receive_ns > capture_utc_ns
    ):
        raise DataContractError("ENTRY_BBO_INVALID")
    if capture_utc_ns - receive_ns > max_quote_age_ns:
        raise DataContractError("ENTRY_QUOTE_STALE")
    return {
        "instrument_id": instrument_id,
        "bid_nano_usd": bid,
        "ask_nano_usd": ask,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "tick_nano_usd": tick,
        "event_utc_ns": event_ns,
        "receive_utc_ns": receive_ns,
        "flags": flags,
    }


def _normalize_contract(
    value: object, *, cutoff_utc_ns: int
) -> dict[str, object]:
    raw = _closed(value, _CONTRACT_KEYS, "ENTRY_OPTION_CONTRACT_SCHEMA_INVALID")
    contract_id = _identifier(
        raw["contract_id"],
        "ENTRY_OPTION_CONTRACT_INVALID",
        maximum=127,
    )
    occ_symbol = _ascii_text(
        raw["occ_symbol"], "ENTRY_OPTION_CONTRACT_INVALID", maximum=64
    )
    if raw["underlying"] != "GLD" or raw["option_type"] != "CALL":
        raise DataContractError("ENTRY_OPTION_CONTRACT_INVALID")
    strike = _integer(
        raw["strike_nano_usd"], "ENTRY_OPTION_CONTRACT_INVALID", minimum=1
    )
    expiry_date = _date(raw["expiry_date"], "ENTRY_OPTION_CONTRACT_INVALID")
    last_trading_date = _date(
        raw["last_trading_date"], "ENTRY_OPTION_CONTRACT_INVALID"
    )
    expiry_ns = _integer(raw["expiry_utc_ns"], "ENTRY_OPTION_CONTRACT_INVALID")
    last_trading_ns = _integer(
        raw["last_trading_utc_ns"], "ENTRY_OPTION_CONTRACT_INVALID"
    )
    activation_ns = _integer(
        raw["activation_utc_ns"], "ENTRY_OPTION_CONTRACT_INVALID"
    )
    multiplier = _integer(
        raw["multiplier"], "ENTRY_OPTION_CONTRACT_INVALID", minimum=1
    )
    deliverable_shares = _integer(
        raw["deliverable_shares"],
        "ENTRY_OPTION_CONTRACT_INVALID",
        minimum=1,
    )
    deliverable = _string(
        raw["deliverable"], "ENTRY_OPTION_CONTRACT_INVALID"
    )
    currency = _string(
        raw["currency"],
        "ENTRY_OPTION_CONTRACT_INVALID",
        allowed=frozenset({"USD"}),
    )
    exchange = _identifier(raw["exchange"], "ENTRY_OPTION_CONTRACT_INVALID")
    tick = _integer(
        raw["tick_nano_usd"], "ENTRY_OPTION_CONTRACT_INVALID", minimum=1
    )
    standard_unadjusted = _boolean(
        raw["standard_unadjusted"], "ENTRY_OPTION_CONTRACT_INVALID"
    )
    exercise_style = _string(
        raw["exercise_style"],
        "ENTRY_OPTION_CONTRACT_INVALID",
        allowed=frozenset({"AMERICAN", "EUROPEAN"}),
    )
    _verify_occ_identity(
        occ_symbol,
        expiry_date=expiry_date,
        strike_nano_usd=strike,
    )
    if (
        not activation_ns <= cutoff_utc_ns < expiry_ns
        or last_trading_ns > expiry_ns
        or _utc_date(expiry_ns) != expiry_date
        or _utc_date(last_trading_ns) != last_trading_date
    ):
        raise DataContractError("ENTRY_OPTION_CONTRACT_INVALID")
    if standard_unadjusted and deliverable_shares != multiplier:
        raise DataContractError("ENTRY_OPTION_DELIVERABLE_IDENTITY_MISMATCH")
    return {
        "contract_id": contract_id,
        "occ_symbol": occ_symbol,
        "underlying": "GLD",
        "option_type": "CALL",
        "strike_nano_usd": strike,
        "expiry_date": expiry_date,
        "last_trading_date": last_trading_date,
        "expiry_utc_ns": expiry_ns,
        "last_trading_utc_ns": last_trading_ns,
        "activation_utc_ns": activation_ns,
        "multiplier": multiplier,
        "deliverable_shares": deliverable_shares,
        "deliverable": deliverable,
        "currency": currency,
        "exchange": exchange,
        "tick_nano_usd": tick,
        "standard_unadjusted": standard_unadjusted,
        "exercise_style": exercise_style,
    }


def _normalize_option_snapshot(
    value: object,
    *,
    cutoff_utc_ns: int,
    option_window_duration_ns: int,
    max_quote_age_ns: int,
    max_cross_leg_receive_skew_ns: int,
    min_quote_size: int,
) -> dict[str, object]:
    raw = _closed(
        value, _OPTION_SNAPSHOT_KEYS, "ENTRY_OPTION_SNAPSHOT_SCHEMA_INVALID"
    )
    if raw["schema_version"] != "GLD_CALL_ATOMIC_SNAPSHOT_V1":
        raise DataContractError("ENTRY_OPTION_SNAPSHOT_VERSION_UNSUPPORTED")
    source_receipt_id = _identifier(
        raw["source_receipt_id"], "ENTRY_SOURCE_REFERENCE_INVALID"
    )
    snapshot_id = _identifier(raw["snapshot_id"], "ENTRY_SNAPSHOT_ID_INVALID")
    capture = _integer(raw["capture_utc_ns"], "ENTRY_SNAPSHOT_TIME_INVALID")
    window_start = _integer(
        raw["window_start_utc_ns"], "ENTRY_SNAPSHOT_TIME_INVALID"
    )
    window_end = _integer(
        raw["window_end_utc_ns"], "ENTRY_SNAPSHOT_TIME_INVALID"
    )
    universe_complete = _boolean(
        raw["universe_complete"], "ENTRY_OPTION_UNIVERSE_INCOMPLETE"
    )
    if not universe_complete:
        raise DataContractError("ENTRY_OPTION_UNIVERSE_INCOMPLETE")
    if (
        window_start != cutoff_utc_ns
        or window_end - window_start != option_window_duration_ns
        or not window_start <= capture < window_end
    ):
        raise DataContractError("ENTRY_SNAPSHOT_TIME_INVALID")
    underlying_bbo = _normalize_bbo(
        raw["underlying_bbo"],
        capture_utc_ns=capture,
        window_start_utc_ns=window_start,
        window_end_utc_ns=window_end,
        max_quote_age_ns=max_quote_age_ns,
        min_quote_size=min_quote_size,
    )
    if underlying_bbo["instrument_id"] != "GLD":
        raise DataContractError("ENTRY_UNDERLYING_IDENTITY_MISMATCH")
    quotes_raw = _list(raw["option_quotes"], "ENTRY_OPTION_QUOTES_INVALID")
    if not quotes_raw:
        raise DataContractError("ENTRY_OPTION_UNIVERSE_INCOMPLETE")
    quotes: list[dict[str, object]] = []
    for item in quotes_raw:
        quote = _closed(item, _OPTION_QUOTE_KEYS, "ENTRY_OPTION_QUOTE_INVALID")
        contract = _normalize_contract(quote["contract"], cutoff_utc_ns=cutoff_utc_ns)
        top = _normalize_bbo(
            quote["top_of_book"],
            capture_utc_ns=capture,
            window_start_utc_ns=window_start,
            window_end_utc_ns=window_end,
            max_quote_age_ns=max_quote_age_ns,
            min_quote_size=min_quote_size,
        )
        if (
            top["instrument_id"] != contract["contract_id"]
            or top["tick_nano_usd"] != contract["tick_nano_usd"]
        ):
            raise DataContractError("ENTRY_OPTION_IDENTITY_MISMATCH")
        quotes.append({"contract": contract, "top_of_book": top})
    quotes = sorted(quotes, key=lambda item: item["contract"]["contract_id"])
    ids = [item["contract"]["contract_id"] for item in quotes]
    occ_symbols = [item["contract"]["occ_symbol"] for item in quotes]
    if len(ids) != len(set(ids)) or len(occ_symbols) != len(set(occ_symbols)):
        raise DataContractError("ENTRY_OPTION_CONTRACT_DUPLICATE")
    receive_times = [
        int(underlying_bbo["receive_utc_ns"]),
        *(int(item["top_of_book"]["receive_utc_ns"]) for item in quotes),
    ]
    if max(receive_times) - min(receive_times) > max_cross_leg_receive_skew_ns:
        raise DataContractError("ENTRY_CROSS_LEG_RECEIVE_SKEW_EXCEEDED")
    return {
        "schema_version": "GLD_CALL_ATOMIC_SNAPSHOT_V1",
        "source_receipt_id": source_receipt_id,
        "snapshot_id": snapshot_id,
        "capture_utc_ns": capture,
        "window_start_utc_ns": window_start,
        "window_end_utc_ns": window_end,
        "universe_complete": universe_complete,
        "underlying_bbo": underlying_bbo,
        "option_quotes": quotes,
    }


def _normalize_pit_inputs(
    value: object, *, snapshot_capture_utc_ns: int
) -> dict[str, object]:
    raw = _closed(value, _PIT_KEYS, "ENTRY_PIT_INPUTS_SCHEMA_INVALID")
    if raw["schema_version"] != "PIT_MARKET_INPUTS_V1":
        raise DataContractError("ENTRY_PIT_INPUTS_VERSION_UNSUPPORTED")
    receipt_values = _list(
        raw["source_receipt_ids"], "ENTRY_SOURCE_REFERENCE_INVALID"
    )
    receipts = sorted(
        _identifier(item, "ENTRY_SOURCE_REFERENCE_INVALID")
        for item in receipt_values
    )
    if not receipts or len(receipts) != len(set(receipts)):
        raise DataContractError("ENTRY_SOURCE_REFERENCE_INVALID")
    as_of = _integer(raw["as_of_utc_ns"], "ENTRY_PIT_INPUTS_INVALID")
    if as_of > snapshot_capture_utc_ns:
        raise DataContractError("ENTRY_FUTURE_DATA_FORBIDDEN")
    curve_raw = _list(raw["rate_curve"], "ENTRY_RATE_CURVE_INVALID")
    curve: list[dict[str, object]] = []
    for item in curve_raw:
        point = _closed(item, _RATE_POINT_KEYS, "ENTRY_RATE_POINT_INVALID")
        curve.append(
            {
                "tenor_days": _integer(
                    point["tenor_days"], "ENTRY_RATE_POINT_INVALID", minimum=1
                ),
                "zero_rate_ppm": _integer(
                    point["zero_rate_ppm"],
                    "ENTRY_RATE_POINT_INVALID",
                    minimum=-PPM,
                    maximum=5 * PPM,
                ),
            }
        )
    curve = _sort_unique(
        curve, key="tenor_days", reason="ENTRY_RATE_TENOR_DUPLICATE"
    )
    if not curve:
        raise DataContractError("ENTRY_RATE_CURVE_INVALID")
    expense = _integer(
        raw["expense_yield_ppm"],
        "ENTRY_PIT_INPUTS_INVALID",
        maximum=PPM,
    )
    distribution = _integer(
        raw["distribution_yield_ppm"],
        "ENTRY_PIT_INPUTS_INVALID",
        maximum=PPM,
    )
    borrow_available = _boolean(
        raw["borrow_available"], "ENTRY_PIT_INPUTS_INVALID"
    )
    borrow_rate = _integer(
        raw["borrow_rate_ppm"],
        "ENTRY_PIT_INPUTS_INVALID",
        minimum=-PPM,
        maximum=5 * PPM,
    )
    return {
        "schema_version": "PIT_MARKET_INPUTS_V1",
        "source_receipt_ids": receipts,
        "as_of_utc_ns": as_of,
        "rate_curve": curve,
        "expense_yield_ppm": expense,
        "distribution_yield_ppm": distribution,
        "borrow_available": borrow_available,
        "borrow_rate_ppm": borrow_rate,
    }


def _normalize_fee_schedule(
    value: object, *, cutoff_utc_ns: int
) -> dict[str, object]:
    raw = _closed(value, _FEE_KEYS, "ENTRY_FEE_SCHEMA_INVALID")
    if raw["schema_version"] != "EFFECTIVE_FEE_SCHEDULE_V1":
        raise DataContractError("ENTRY_FEE_VERSION_UNSUPPORTED")
    source_receipt_id = _identifier(
        raw["source_receipt_id"], "ENTRY_SOURCE_REFERENCE_INVALID"
    )
    effective_from = _integer(
        raw["effective_from_utc_ns"], "ENTRY_FEE_TIME_MISALIGNED"
    )
    effective_to = _optional_integer(
        raw["effective_to_utc_ns"], "ENTRY_FEE_TIME_MISALIGNED"
    )
    if (
        effective_from > cutoff_utc_ns
        or (effective_to is not None and cutoff_utc_ns >= effective_to)
    ):
        raise DataContractError("ENTRY_FEE_TIME_MISALIGNED")
    result: dict[str, object] = {
        "schema_version": "EFFECTIVE_FEE_SCHEDULE_V1",
        "source_receipt_id": source_receipt_id,
        "effective_from_utc_ns": effective_from,
        "effective_to_utc_ns": effective_to,
        "currency": _string(
            raw["currency"],
            "ENTRY_FEE_INVALID",
            allowed=frozenset({"USD"}),
        ),
    }
    for key in (
        "broker_schedule_id",
        "exchange_schedule_id",
        "clearing_schedule_id",
        "regulatory_schedule_id",
    ):
        result[key] = _identifier(raw[key], "ENTRY_FEE_INVALID")
    for key in (
        "long_entry_fee_nano_usd_per_contract",
        "long_exit_fee_nano_usd_per_contract",
        "short_entry_fee_nano_usd_per_contract",
        "short_exit_fee_nano_usd_per_contract",
    ):
        result[key] = _integer(raw[key], "ENTRY_FEE_INVALID")
    return result


def _normalize_account(
    value: object, *, cutoff_utc_ns: int, max_account_age_ns: int
) -> dict[str, object]:
    raw = _closed(value, _ACCOUNT_KEYS, "ENTRY_ACCOUNT_SCHEMA_INVALID")
    if raw["schema_version"] != "ACCOUNT_SNAPSHOT_A_V1":
        raise DataContractError("ENTRY_ACCOUNT_VERSION_UNSUPPORTED")
    source_receipt_id = _identifier(
        raw["source_receipt_id"], "ENTRY_SOURCE_REFERENCE_INVALID"
    )
    as_of = _integer(raw["as_of_utc_ns"], "ENTRY_ACCOUNT_TIME_INVALID")
    if as_of > cutoff_utc_ns:
        raise DataContractError("ENTRY_FUTURE_DATA_FORBIDDEN")
    if cutoff_utc_ns - as_of > max_account_age_ns:
        raise DataContractError("ENTRY_ACCOUNT_STALE")
    positions_raw = _list(raw["positions"], "ENTRY_POSITIONS_INVALID")
    positions: list[dict[str, object]] = []
    for item in positions_raw:
        position = _closed(item, _POSITION_KEYS, "ENTRY_POSITION_INVALID")
        positions.append(
            {
                "position_id": _identifier(
                    position["position_id"], "ENTRY_POSITION_INVALID"
                ),
                "instrument_id": _identifier(
                    position["instrument_id"], "ENTRY_POSITION_INVALID"
                ),
                "signed_contract_count": _integer(
                    position["signed_contract_count"],
                    "ENTRY_POSITION_INVALID",
                    minimum=MIN_I64,
                ),
                "multiplier": _integer(
                    position["multiplier"],
                    "ENTRY_POSITION_INVALID",
                    minimum=1,
                ),
                "currency": _string(
                    position["currency"],
                    "ENTRY_POSITION_INVALID",
                    allowed=frozenset({"USD"}),
                ),
            }
        )
    positions = _sort_unique(
        positions, key="position_id", reason="ENTRY_POSITION_DUPLICATE"
    )
    orders_raw = _list(raw["open_orders"], "ENTRY_OPEN_ORDERS_INVALID")
    orders: list[dict[str, object]] = []
    for item in orders_raw:
        order = _closed(item, _OPEN_ORDER_KEYS, "ENTRY_OPEN_ORDER_INVALID")
        orders.append(
            {
                "order_id": _identifier(order["order_id"], "ENTRY_OPEN_ORDER_INVALID"),
                "instrument_id": _identifier(
                    order["instrument_id"], "ENTRY_OPEN_ORDER_INVALID"
                ),
                "remaining_contract_count": _integer(
                    order["remaining_contract_count"],
                    "ENTRY_OPEN_ORDER_INVALID",
                    minimum=1,
                ),
                "side": _string(
                    order["side"],
                    "ENTRY_OPEN_ORDER_INVALID",
                    allowed=frozenset({"BUY", "SELL"}),
                ),
                "limit_nano_usd": _integer(
                    order["limit_nano_usd"],
                    "ENTRY_OPEN_ORDER_INVALID",
                    minimum=1,
                ),
                "status": _string(
                    order["status"],
                    "ENTRY_OPEN_ORDER_INVALID",
                    allowed=frozenset({"OPEN", "PARTIALLY_FILLED"}),
                ),
            }
        )
    orders = _sort_unique(
        orders, key="order_id", reason="ENTRY_OPEN_ORDER_DUPLICATE"
    )
    result: dict[str, object] = {
        "schema_version": "ACCOUNT_SNAPSHOT_A_V1",
        "source_receipt_id": source_receipt_id,
        "as_of_utc_ns": as_of,
        "currency": _string(
            raw["currency"],
            "ENTRY_ACCOUNT_INVALID",
            allowed=frozenset({"USD"}),
        ),
    }
    for key in (
        "net_liquidation_value_nano_usd",
        "settled_cash_nano_usd",
        "strategy_bankroll_nano_usd",
        "strategy_high_watermark_nano_usd",
        "realized_profit_nano_usd",
        "realized_loss_nano_usd",
    ):
        minimum = 1 if key == "strategy_high_watermark_nano_usd" else 0
        result[key] = _integer(
            raw[key],
            "ENTRY_ACCOUNT_INVALID",
            minimum=minimum,
        )
    result["current_gld_delta_exposure_nano_usd"] = _integer(
        raw["current_gld_delta_exposure_nano_usd"],
        "ENTRY_ACCOUNT_INVALID",
        minimum=MIN_I64,
    )
    result["positions"] = positions
    result["open_orders"] = orders
    return result


def _validate_technical_arithmetic_bounds(
    *,
    option_snapshot: dict[str, object],
    fee_schedule: dict[str, object],
    account: dict[str, object],
    rule_package: dict[str, object],
) -> None:
    quotes = option_snapshot["option_quotes"]
    underlying = option_snapshot["underlying_bbo"]
    if type(quotes) is not list or type(underlying) is not dict:
        raise DataContractError("ENTRY_TECHNICAL_ARITHMETIC_RANGE_INVALID")
    group_max_tick: dict[tuple[object, ...], int] = {}
    for quote in quotes:
        contract = quote["contract"]
        book = quote["top_of_book"]
        if type(contract) is not dict or type(book) is not dict:
            raise DataContractError("ENTRY_TECHNICAL_ARITHMETIC_RANGE_INVALID")
        key = (
            contract["expiry_date"],
            contract["multiplier"],
            contract["currency"],
            contract["deliverable"],
        )
        group_max_tick[key] = max(
            group_max_tick.get(key, 0),
            int(book["tick_nano_usd"]),
        )
    long_fee = int(fee_schedule["long_entry_fee_nano_usd_per_contract"])
    short_fee = int(fee_schedule["short_entry_fee_nano_usd_per_contract"])
    long_stress = int(rule_package["entry_stress_long_ticks"])
    short_stress = int(rule_package["entry_stress_short_ticks"])
    underlying_ask = int(underlying["ask_nano_usd"])
    for quote in quotes:
        contract = quote["contract"]
        book = quote["top_of_book"]
        if type(contract) is not dict or type(book) is not dict:
            raise DataContractError("ENTRY_TECHNICAL_ARITHMETIC_RANGE_INVALID")
        multiplier = int(contract["multiplier"])
        key = (
            contract["expiry_date"],
            contract["multiplier"],
            contract["currency"],
            contract["deliverable"],
        )
        upper_values = (
            int(contract["strike_nano_usd"]) * multiplier,
            underlying_ask * multiplier,
            (
                int(book["ask_nano_usd"])
                + int(book["tick_nano_usd"]) * long_stress
            )
            * multiplier
            + long_fee,
            (
                int(book["ask_nano_usd"])
                + int(book["tick_nano_usd"]) * long_stress
                + group_max_tick[key] * short_stress
            )
            * multiplier
            + long_fee
            + short_fee,
        )
        if any(value > MAX_CANONICAL_INTEGER for value in upper_values):
            raise DataContractError(
                "ENTRY_TECHNICAL_ARITHMETIC_RANGE_INVALID"
            )
    eligible_bankroll_upper = (
        int(account["strategy_bankroll_nano_usd"])
        + int(account["realized_profit_nano_usd"])
    )
    if eligible_bankroll_upper > MAX_CANONICAL_INTEGER:
        raise DataContractError("ENTRY_TECHNICAL_ARITHMETIC_RANGE_INVALID")


def _normalize_rule_package(
    value: object, *, cutoff_utc_ns: int
) -> dict[str, object]:
    raw = _closed(value, _RULE_KEYS, "ENTRY_RULE_PACKAGE_SCHEMA_INVALID")
    if raw["schema_version"] != "GLD_TECHNICAL_RULE_BINDING_V1":
        raise DataContractError("ENTRY_RULE_PACKAGE_VERSION_UNSUPPORTED")
    effective_from = _integer(
        raw["effective_from_utc_ns"], "ENTRY_RULE_PACKAGE_INVALID"
    )
    cutoff_minute = _integer(
        raw["cutoff_minute_ending_ordinal"],
        "ENTRY_RULE_PACKAGE_INVALID",
        maximum=1_439,
    )
    if effective_from > cutoff_utc_ns or cutoff_minute != 645:
        raise DataContractError("ENTRY_RULE_PACKAGE_INVALID")
    result = {
        "schema_version": "GLD_TECHNICAL_RULE_BINDING_V1",
        "package_id": _identifier(raw["package_id"], "ENTRY_RULE_PACKAGE_INVALID"),
        "version": _identifier(raw["version"], "ENTRY_RULE_PACKAGE_INVALID"),
        "effective_from_utc_ns": effective_from,
        "cutoff_minute_ending_ordinal": cutoff_minute,
        "option_window_duration_ns": _integer(
            raw["option_window_duration_ns"],
            "ENTRY_RULE_PACKAGE_INVALID",
            minimum=1,
        ),
        "max_quote_age_ns": _integer(
            raw["max_quote_age_ns"], "ENTRY_RULE_PACKAGE_INVALID", minimum=1
        ),
        "max_cross_leg_receive_skew_ns": _integer(
            raw["max_cross_leg_receive_skew_ns"],
            "ENTRY_RULE_PACKAGE_INVALID",
            minimum=1,
        ),
        "max_account_age_ns": _integer(
            raw["max_account_age_ns"],
            "ENTRY_RULE_PACKAGE_INVALID",
            minimum=1,
        ),
        "min_quote_size": _integer(
            raw["min_quote_size"], "ENTRY_RULE_PACKAGE_INVALID", minimum=1
        ),
        "min_history_sessions": _integer(
            raw["min_history_sessions"],
            "ENTRY_RULE_PACKAGE_INVALID",
            minimum=220,
            maximum=512,
        ),
        "entry_stress_long_ticks": _integer(
            raw["entry_stress_long_ticks"],
            "ENTRY_RULE_PACKAGE_INVALID",
            maximum=1_000,
        ),
        "entry_stress_short_ticks": _integer(
            raw["entry_stress_short_ticks"],
            "ENTRY_RULE_PACKAGE_INVALID",
            maximum=1_000,
        ),
        "realized_profit_reinvestment_ppm": _integer(
            raw["realized_profit_reinvestment_ppm"],
            "ENTRY_RULE_PACKAGE_INVALID",
            maximum=PPM,
        ),
        "realized_loss_effect_ppm": _integer(
            raw["realized_loss_effect_ppm"],
            "ENTRY_RULE_PACKAGE_INVALID",
            maximum=PPM,
        ),
        "minimum_cash_reserve_nlv_ppm": _integer(
            raw["minimum_cash_reserve_nlv_ppm"],
            "ENTRY_RULE_PACKAGE_INVALID",
            maximum=PPM,
        ),
    }
    if result["option_window_duration_ns"] != 60_000_000_000:
        raise DataContractError("ENTRY_RULE_PACKAGE_INVALID")
    return result


def _normalize_model_package(value: object) -> dict[str, object]:
    raw = _closed(value, _MODEL_KEYS, "ENTRY_MODEL_PACKAGE_SCHEMA_INVALID")
    if raw["schema_version"] != "LOCAL_OPTION_MODEL_BINDING_V1":
        raise DataContractError("ENTRY_MODEL_PACKAGE_VERSION_UNSUPPORTED")
    coarse = _integer(
        raw["coarse_steps"], "ENTRY_MODEL_PACKAGE_INVALID", minimum=1
    )
    fine = _integer(raw["fine_steps"], "ENTRY_MODEL_PACKAGE_INVALID", minimum=1)
    if fine <= coarse:
        raise DataContractError("ENTRY_MODEL_PACKAGE_INVALID")
    return {
        "schema_version": "LOCAL_OPTION_MODEL_BINDING_V1",
        "package_id": _identifier(raw["package_id"], "ENTRY_MODEL_PACKAGE_INVALID"),
        "version": _identifier(raw["version"], "ENTRY_MODEL_PACKAGE_INVALID"),
        "formula_id": _identifier(raw["formula_id"], "ENTRY_MODEL_PACKAGE_INVALID"),
        "coarse_steps": coarse,
        "fine_steps": fine,
        "iv_iterations": _integer(
            raw["iv_iterations"],
            "ENTRY_MODEL_PACKAGE_INVALID",
            minimum=1,
            maximum=1_024,
        ),
        "rounding_mode": _string(
            raw["rounding_mode"],
            "ENTRY_MODEL_PACKAGE_INVALID",
            allowed=frozenset({"HALF_EVEN_INTEGER"}),
        ),
        "source_code_sha256": _sha256(
            raw["source_code_sha256"], "ENTRY_MODEL_PACKAGE_INVALID"
        ),
        "runtime_fingerprint_sha256": _sha256(
            raw["runtime_fingerprint_sha256"],
            "ENTRY_MODEL_PACKAGE_INVALID",
        ),
    }


def _verify_source_reference(
    receipt_map: dict[str, SourceQualificationReceiptV1],
    receipt_id: str,
    required_domain: str,
) -> None:
    receipt = receipt_map.get(receipt_id)
    if receipt is None or required_domain not in receipt.covered_domains:
        raise DataContractError(
            "ENTRY_SOURCE_REFERENCE_INVALID", required_domain
        )


def _qualification_result(
    *,
    classification: str,
    input_sha256: str,
    receipts: tuple[SourceQualificationReceiptV1, ...],
    max_cross_leg_receive_skew_ns: int,
    trusted_source_receipt_sha256: frozenset[str],
) -> DataQualificationResultV1:
    if classification == "SYNTHETIC_ONLY":
        status = STRUCTURALLY_VALID_SYNTHETIC
        reasons = ("SYNTHETIC_SOURCE_ONLY",)
        effective_trusted_source_receipt_sha256 = frozenset()
    else:
        effective_trusted_source_receipt_sha256 = (
            trusted_source_receipt_sha256
        )
        reason_set: set[str] = {
            "SOURCE_OBSERVATION_ATTESTATION_NOT_IMPLEMENTED"
        }
        for receipt in receipts:
            if receipt.receipt_sha256 not in trusted_source_receipt_sha256:
                reason_set.add("SOURCE_RECEIPT_NOT_TRUSTED")
            if receipt.evidence_kind != "PROVIDER_CAPABILITY_RECEIPT":
                reason_set.add("SOURCE_CAPABILITY_NOT_VERIFIED")
            if receipt.entitlement_status != "VERIFIED":
                reason_set.add("SOURCE_ENTITLEMENT_NOT_VERIFIED")
            if (
                receipt.event_time_semantics != "UTC_NS_EXPLICIT"
                or receipt.receive_time_semantics != "UTC_NS_EXPLICIT"
            ):
                reason_set.add("SOURCE_TIME_SEMANTICS_NOT_VERIFIED")
            if receipt.field_coverage_ppm != PPM:
                reason_set.add("SOURCE_FIELD_COVERAGE_INCOMPLETE")
            if "OPTION_SNAPSHOT" in receipt.covered_domains:
                if not receipt.complete_universe_supported:
                    reason_set.add("SOURCE_COMPLETE_UNIVERSE_NOT_VERIFIED")
                if not receipt.atomic_snapshot_supported:
                    reason_set.add("SOURCE_ATOMIC_SNAPSHOT_NOT_VERIFIED")
                if (
                    receipt.synchronization_max_skew_ns
                    > max_cross_leg_receive_skew_ns
                ):
                    reason_set.add("SOURCE_SYNCHRONIZATION_NOT_QUALIFIED")
        reasons = tuple(sorted(reason_set))
        status = DATA_QUALIFIED if not reasons else DATA_NOT_QUALIFIED
        if not reasons:
            reasons = ("ALL_SOURCE_QUALIFICATION_GATES_PASSED",)
    qualification_body = {
        "schema_version": "DATA_QUALIFICATION_RESULT_V1",
        "status": status,
        "reason_codes": list(reasons),
        "input_sha256": input_sha256,
        "trusted_source_receipt_set_sha256": _canonical_sha256(
            sorted(effective_trusted_source_receipt_sha256)
        ),
    }
    return DataQualificationResultV1(
        status=status,
        reason_codes=reasons,
        input_sha256=input_sha256,
        trusted_source_receipt_set_sha256=str(
            qualification_body["trusted_source_receipt_set_sha256"]
        ),
        qualification_sha256=_canonical_sha256(qualification_body),
        _validation_seal=_VALIDATED_CONTRACT_SEAL,
    )


def validate_entry_bundle(
    document: object,
    *,
    trusted_source_receipt_sha256: frozenset[str] = frozenset(),
) -> EntryFactBundleV1:
    """Validate and normalize one provider-neutral EntryFactBundleV1.

    A structurally valid synthetic bundle returns a synthetic qualification
    result; it can never become ``DATA_QUALIFIED`` merely by being complete.
    A production candidate additionally requires every complete canonical
    receipt hash to be present in the caller-owned immutable trust set.  V1
    still returns ``DATA_NOT_QUALIFIED`` because per-observation domain-hash
    attestation is not implemented; capability evidence alone cannot promote
    the current facts.  The document cannot declare its own receipt trusted.
    """

    if type(trusted_source_receipt_sha256) is not frozenset:
        raise DataContractError("TRUSTED_SOURCE_RECEIPT_SET_INVALID")
    for digest in trusted_source_receipt_sha256:
        _sha256(digest, "TRUSTED_SOURCE_RECEIPT_SET_INVALID")
    _canonical_bytes(document)
    _reject_entry_derived_fields(document)
    raw = _closed(document, _ENTRY_KEYS, "ENTRY_SCHEMA_INVALID")
    if raw["schema_version"] != ENTRY_FACT_BUNDLE_SCHEMA_VERSION:
        raise DataContractError("ENTRY_VERSION_UNSUPPORTED")
    classification = _string(
        raw["classification"],
        "ENTRY_CLASSIFICATION_INVALID",
        allowed=frozenset({"SYNTHETIC_ONLY", "PRODUCTION_CANDIDATE"}),
    )
    if raw["scope"] != "GLD_ENTRY_FACTS_ONLY" or raw["underlying"] != "GLD":
        raise DataContractError("ENTRY_SCOPE_INVALID")
    bundle_id = _identifier(raw["bundle_id"], "ENTRY_BUNDLE_ID_INVALID")
    trading_date = _date(raw["trading_date"], "ENTRY_TRADING_DATE_INVALID")
    cutoff = _integer(raw["cutoff_utc_ns"], "ENTRY_CUTOFF_INVALID")

    receipts_raw = _list(
        raw["source_qualification_receipts"],
        "SOURCE_RECEIPTS_INVALID",
    )
    normalized_receipt_pairs = [
        _normalize_source_receipt(item, cutoff_utc_ns=cutoff)
        for item in receipts_raw
    ]
    normalized_receipt_pairs.sort(key=lambda pair: pair[1].receipt_id)
    typed_receipts = tuple(pair[1] for pair in normalized_receipt_pairs)
    if (
        not typed_receipts
        or len({item.receipt_id for item in typed_receipts}) != len(typed_receipts)
    ):
        raise DataContractError("SOURCE_RECEIPT_DUPLICATE")
    normalized_receipts = [pair[0] for pair in normalized_receipt_pairs]
    receipt_map = {item.receipt_id: item for item in typed_receipts}

    rule_package = _normalize_rule_package(raw["rule_package"], cutoff_utc_ns=cutoff)
    model_package = _normalize_model_package(raw["model_package"])
    calendar = _normalize_calendar(
        raw["calendar"], trading_date=trading_date, cutoff_utc_ns=cutoff
    )
    decision_ordinal = int(calendar["decision_session_ordinal"])
    calendar_by_ordinal = {
        int(item["session_ordinal"]): item for item in calendar["sessions"]
    }
    daily_bars = _normalize_daily_bars(
        raw["daily_bars"],
        cutoff_utc_ns=cutoff,
        decision_ordinal=decision_ordinal,
        calendar_by_ordinal=calendar_by_ordinal,
        min_history_sessions=int(rule_package["min_history_sessions"]),
    )
    minute_bars = _normalize_minute_bars(
        raw["minute_bars"],
        trading_date=trading_date,
        cutoff_utc_ns=cutoff,
        decision_ordinal=decision_ordinal,
    )
    market_status = _normalize_market_status(
        raw["market_status"], trading_date=trading_date, cutoff_utc_ns=cutoff
    )
    option_snapshot = _normalize_option_snapshot(
        raw["option_snapshot"],
        cutoff_utc_ns=cutoff,
        option_window_duration_ns=int(rule_package["option_window_duration_ns"]),
        max_quote_age_ns=int(rule_package["max_quote_age_ns"]),
        max_cross_leg_receive_skew_ns=int(
            rule_package["max_cross_leg_receive_skew_ns"]
        ),
        min_quote_size=int(rule_package["min_quote_size"]),
    )
    pit_inputs = _normalize_pit_inputs(
        raw["pit_inputs"],
        snapshot_capture_utc_ns=int(option_snapshot["capture_utc_ns"]),
    )
    fee_schedule = _normalize_fee_schedule(
        raw["fee_schedule"], cutoff_utc_ns=cutoff
    )
    account = _normalize_account(
        raw["account_snapshot_a"],
        cutoff_utc_ns=cutoff,
        max_account_age_ns=int(rule_package["max_account_age_ns"]),
    )
    _validate_technical_arithmetic_bounds(
        option_snapshot=option_snapshot,
        fee_schedule=fee_schedule,
        account=account,
        rule_package=rule_package,
    )

    references = (
        (calendar["source_receipt_id"], "CALENDAR"),
        (daily_bars["source_receipt_id"], "DAILY_BARS"),
        (minute_bars["source_receipt_id"], "MINUTE_BARS"),
        (market_status["source_receipt_id"], "MINUTE_BARS"),
        (option_snapshot["source_receipt_id"], "OPTION_SNAPSHOT"),
        (fee_schedule["source_receipt_id"], "FEES"),
        (account["source_receipt_id"], "ACCOUNT"),
    )
    for receipt_id, domain in references:
        _verify_source_reference(receipt_map, str(receipt_id), domain)
    for receipt_id in pit_inputs["source_receipt_ids"]:
        _verify_source_reference(receipt_map, str(receipt_id), "PIT_INPUTS")
    option_receipt = receipt_map[str(option_snapshot["source_receipt_id"])]
    if (
        option_receipt.effective_from_utc_ns
        > int(option_snapshot["capture_utc_ns"])
        or (
            option_receipt.effective_to_utc_ns is not None
            and int(option_snapshot["capture_utc_ns"])
            >= option_receipt.effective_to_utc_ns
        )
    ):
        raise DataContractError("SOURCE_RECEIPT_NOT_EFFECTIVE")

    hashes_raw = _closed(
        raw["content_hashes"], _CONTENT_HASH_KEYS, "ENTRY_CONTENT_HASH_SCHEMA_INVALID"
    )
    normalized_domains: dict[str, object] = {
        "calendar": calendar,
        "daily_bars": daily_bars,
        "minute_bars": minute_bars,
        "market_status": market_status,
        "option_snapshot": option_snapshot,
        "pit_inputs": pit_inputs,
        "fee_schedule": fee_schedule,
        "account_snapshot_a": account,
        "rule_package": rule_package,
        "model_package": model_package,
        "source_qualification_receipts": normalized_receipts,
    }
    normalized_hashes: dict[str, object] = {}
    for domain in ENTRY_HASH_DOMAINS:
        key = f"{domain}_sha256"
        declared = _sha256(hashes_raw[key], "ENTRY_CONTENT_HASH_INVALID")
        actual = _canonical_sha256(normalized_domains[domain])
        if declared != actual:
            raise DataContractError("ENTRY_CONTENT_HASH_MISMATCH", domain)
        normalized_hashes[key] = declared

    normalized: dict[str, object] = {
        "schema_version": ENTRY_FACT_BUNDLE_SCHEMA_VERSION,
        "classification": classification,
        "scope": "GLD_ENTRY_FACTS_ONLY",
        "bundle_id": bundle_id,
        "underlying": "GLD",
        "trading_date": trading_date,
        "cutoff_utc_ns": cutoff,
        **normalized_domains,
        "content_hashes": normalized_hashes,
    }
    encoded = _canonical_bytes(normalized)
    digest = _canonical_sha256(normalized)
    qualification = _qualification_result(
        classification=classification,
        input_sha256=digest,
        receipts=typed_receipts,
        max_cross_leg_receive_skew_ns=int(
            rule_package["max_cross_leg_receive_skew_ns"]
        ),
        trusted_source_receipt_sha256=trusted_source_receipt_sha256,
    )
    return EntryFactBundleV1(
        bundle_id=bundle_id,
        classification=classification,
        trading_date=trading_date,
        cutoff_utc_ns=cutoff,
        entry_bundle_sha256=digest,
        canonical_bytes=encoded,
        qualification=qualification,
        source_qualification_receipts=typed_receipts,
        _normalized_document=MappingProxyType(deepcopy(normalized)),
        _validation_seal=_VALIDATED_CONTRACT_SEAL,
    )


def _normalize_episode(value: object) -> dict[str, object]:
    raw = _closed(value, _EPISODE_KEYS, "CARRIER_EPISODE_SCHEMA_INVALID")
    entry_ns = _integer(raw["entry_utc_ns"], "CARRIER_EPISODE_INVALID")
    exit_ns = _integer(raw["exit_utc_ns"], "CARRIER_EPISODE_INVALID")
    if exit_ns <= entry_ns:
        raise DataContractError("CARRIER_EPISODE_INVALID")
    return {
        "episode_id": _identifier(raw["episode_id"], "CARRIER_EPISODE_INVALID"),
        "entry_utc_ns": entry_ns,
        "exit_utc_ns": exit_ns,
        "entry_debit_nano_usd": _integer(
            raw["entry_debit_nano_usd"],
            "CARRIER_EPISODE_INVALID",
            minimum=1,
        ),
        "after_cost_return_on_entry_debit_ppm": _integer(
            raw["after_cost_return_on_entry_debit_ppm"],
            "CARRIER_EPISODE_INVALID",
            minimum=-10 * PPM,
            maximum=100 * PPM,
        ),
        "split": _string(
            raw["split"],
            "CARRIER_EPISODE_INVALID",
            allowed=frozenset({"DEVELOPMENT", "WALK_FORWARD", "SEALED_OOS"}),
        ),
        "input_sha256": _sha256(
            raw["input_sha256"], "CARRIER_EPISODE_INVALID"
        ),
    }


def _trunc_toward_zero(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise DataContractError("CARRIER_DISTRIBUTION_BINDING_INVALID")
    if numerator >= 0:
        return numerator // denominator
    return -((-numerator) // denominator)


def _normalize_carrier(value: object) -> tuple[dict[str, object], CarrierEvidenceV1]:
    raw_object = _object(value, "CARRIER_EVIDENCE_SCHEMA_INVALID")
    if "distribution_binding" not in raw_object:
        raise DataContractError("CARRIER_DISTRIBUTION_BINDING_REQUIRED")
    raw = _closed(raw_object, _CARRIER_KEYS, "CARRIER_EVIDENCE_SCHEMA_INVALID")
    carrier_id = _string(
        raw["carrier_id"],
        "CARRIER_ID_INVALID",
        allowed=frozenset({"LC0", "BCS0"}),
    )
    episodes_raw = _list(raw["episodes"], "CARRIER_EVIDENCE_OPAQUE_ONLY")
    if not episodes_raw:
        raise DataContractError("CARRIER_EVIDENCE_OPAQUE_ONLY")
    episodes = [_normalize_episode(item) for item in episodes_raw]
    episodes = _sort_unique(
        episodes, key="episode_id", reason="CARRIER_EPISODE_DUPLICATE"
    )
    coverage_raw = _closed(
        raw["coverage"], _COVERAGE_KEYS, "CARRIER_COVERAGE_SCHEMA_INVALID"
    )
    eligible = _integer(
        coverage_raw["eligible_episode_count"], "CARRIER_COVERAGE_INVALID"
    )
    included = _integer(
        coverage_raw["included_episode_count"], "CARRIER_COVERAGE_INVALID"
    )
    excluded = _integer(
        coverage_raw["excluded_episode_count"], "CARRIER_COVERAGE_INVALID"
    )
    coverage_ppm = _integer(
        coverage_raw["coverage_ppm"],
        "CARRIER_COVERAGE_INVALID",
        maximum=PPM,
    )
    exclusions_raw = _list(
        coverage_raw["exclusion_reasons"], "CARRIER_COVERAGE_INVALID"
    )
    exclusions: list[dict[str, object]] = []
    for item in exclusions_raw:
        exclusion = _closed(item, _EXCLUSION_KEYS, "CARRIER_COVERAGE_INVALID")
        reason = _string(
            exclusion["reason_code"], "CARRIER_COVERAGE_INVALID"
        )
        if _REASON_RE.fullmatch(reason) is None:
            raise DataContractError("CARRIER_COVERAGE_INVALID")
        exclusions.append(
            {
                "reason_code": reason,
                "count": _integer(
                    exclusion["count"], "CARRIER_COVERAGE_INVALID", minimum=1
                ),
            }
        )
    exclusions = _sort_unique(
        exclusions,
        key="reason_code",
        reason="CARRIER_EXCLUSION_REASON_DUPLICATE",
    )
    if (
        eligible != included + excluded
        or included != len(episodes)
        or sum(int(item["count"]) for item in exclusions) != excluded
        or coverage_ppm != (included * PPM) // eligible
    ):
        raise DataContractError("CARRIER_COVERAGE_INVALID")
    coverage = {
        "eligible_episode_count": eligible,
        "included_episode_count": included,
        "excluded_episode_count": excluded,
        "coverage_ppm": coverage_ppm,
        "exclusion_reasons": exclusions,
    }
    splits_raw = _closed(
        raw["splits"], _SPLIT_KEYS, "CARRIER_EVIDENCE_SPLIT_MISMATCH"
    )
    splits = {
        key: _integer(
            splits_raw[key], "CARRIER_EVIDENCE_SPLIT_MISMATCH", minimum=1
        )
        for key in (
            "development_count",
            "walk_forward_count",
            "sealed_oos_count",
        )
    }
    actual_split_counts = {
        "development_count": sum(item["split"] == "DEVELOPMENT" for item in episodes),
        "walk_forward_count": sum(item["split"] == "WALK_FORWARD" for item in episodes),
        "sealed_oos_count": sum(item["split"] == "SEALED_OOS" for item in episodes),
    }
    if splits != actual_split_counts:
        raise DataContractError("CARRIER_EVIDENCE_SPLIT_MISMATCH")
    distribution_raw = _closed(
        raw["distribution_binding"],
        _DISTRIBUTION_KEYS,
        "CARRIER_DISTRIBUTION_BINDING_REQUIRED",
    )
    distribution = {
        "distribution_id": _identifier(
            distribution_raw["distribution_id"],
            "CARRIER_DISTRIBUTION_BINDING_INVALID",
        ),
        "return_unit": _string(
            distribution_raw["return_unit"],
            "CARRIER_DISTRIBUTION_BINDING_INVALID",
            allowed=frozenset({"ON_ENTRY_DEBIT_PPM"}),
        ),
        "episode_set_sha256": _sha256(
            distribution_raw["episode_set_sha256"],
            "CARRIER_DISTRIBUTION_BINDING_INVALID",
        ),
        "distribution_sha256": _sha256(
            distribution_raw["distribution_sha256"],
            "CARRIER_DISTRIBUTION_BINDING_INVALID",
        ),
    }
    if distribution["episode_set_sha256"] != _canonical_sha256(episodes):
        raise DataContractError("CARRIER_DISTRIBUTION_BINDING_INVALID")
    distribution_content = {
        "schema_version": "CARRIER_RETURN_DISTRIBUTION_V1",
        "carrier_id": carrier_id,
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
    if distribution["distribution_sha256"] != _canonical_sha256(
        distribution_content
    ):
        raise DataContractError("CARRIER_DISTRIBUTION_BINDING_INVALID")
    expected_return = _integer(
        raw["expected_net_return_on_entry_debit_ppm"],
        "CARRIER_EXPECTED_RETURN_INVALID",
        minimum=-10 * PPM,
        maximum=100 * PPM,
    )
    observed_mean_return = _trunc_toward_zero(
        sum(
            int(item["after_cost_return_on_entry_debit_ppm"])
            for item in episodes
        ),
        len(episodes),
    )
    if expected_return != observed_mean_return:
        raise DataContractError("CARRIER_EXPECTED_RETURN_INVALID")
    full_kelly = _integer(
        raw["full_kelly_ppm"], "CARRIER_EVIDENCE_KELLY_INVALID", maximum=PPM
    )
    robust_kelly = _integer(
        raw["robust_full_kelly_ppm"],
        "CARRIER_EVIDENCE_KELLY_INVALID",
        maximum=PPM,
    )
    half_kelly = _integer(
        raw["half_kelly_ppm"],
        "CARRIER_EVIDENCE_KELLY_INVALID",
        maximum=PPM,
    )
    if robust_kelly > full_kelly or half_kelly != robust_kelly // 2:
        raise DataContractError("CARRIER_EVIDENCE_KELLY_INVALID")
    uncertainty_method = _identifier(
        raw["uncertainty_method"], "CARRIER_EVIDENCE_INVALID"
    )
    estimator_version = _identifier(
        raw["estimator_version"], "CARRIER_EVIDENCE_INVALID"
    )
    if (
        uncertainty_method != SYNTHETIC_CARRIER_UNCERTAINTY_METHOD
        or estimator_version != SYNTHETIC_CARRIER_ESTIMATOR_VERSION
        or full_kelly != 0
        or robust_kelly != 0
        or half_kelly != 0
    ):
        raise DataContractError("CARRIER_EVIDENCE_KELLY_NOT_ESTIMATED")
    unsigned: dict[str, object] = {
        "carrier_id": carrier_id,
        "evidence_receipt_id": _identifier(
            raw["evidence_receipt_id"], "CARRIER_EVIDENCE_RECEIPT_ID_INVALID"
        ),
        "episode_cohort_id": _identifier(
            raw["episode_cohort_id"], "CARRIER_EVIDENCE_INVALID"
        ),
        "entry_clock_id": _identifier(
            raw["entry_clock_id"], "CARRIER_EVIDENCE_INVALID"
        ),
        "exit_clock_id": _identifier(
            raw["exit_clock_id"], "CARRIER_EVIDENCE_INVALID"
        ),
        "cost_model_version": _identifier(
            raw["cost_model_version"], "CARRIER_EVIDENCE_INVALID"
        ),
        "rule_version": _identifier(
            raw["rule_version"], "CARRIER_EVIDENCE_INVALID"
        ),
        "episodes": episodes,
        "coverage": coverage,
        "splits": splits,
        "distribution_binding": distribution,
        "expected_net_return_on_entry_debit_ppm": expected_return,
        "uncertainty_method": uncertainty_method,
        "full_kelly_ppm": full_kelly,
        "robust_full_kelly_ppm": robust_kelly,
        "half_kelly_ppm": half_kelly,
        "estimator_version": estimator_version,
        "fee_schedule_sha256": _sha256(
            raw["fee_schedule_sha256"], "CARRIER_EVIDENCE_INVALID"
        ),
        "exit_policy_sha256": _sha256(
            raw["exit_policy_sha256"], "CARRIER_EVIDENCE_INVALID"
        ),
        "input_sha256": _sha256(
            raw["input_sha256"], "CARRIER_EVIDENCE_INVALID"
        ),
    }
    declared_hash = _sha256(
        raw["evidence_sha256"], "CARRIER_EVIDENCE_HASH_INVALID"
    )
    if declared_hash != _canonical_sha256(unsigned):
        raise DataContractError("CARRIER_EVIDENCE_HASH_MISMATCH", carrier_id)
    normalized = {**unsigned, "evidence_sha256": declared_hash}
    typed = CarrierEvidenceV1(
        carrier_id=carrier_id,
        evidence_receipt_id=str(unsigned["evidence_receipt_id"]),
        evidence_sha256=declared_hash,
        distribution_id=str(distribution["distribution_id"]),
        distribution_sha256=str(distribution["distribution_sha256"]),
        input_sha256=str(unsigned["input_sha256"]),
        expected_net_return_on_entry_debit_ppm=expected_return,
        full_kelly_ppm=full_kelly,
        robust_full_kelly_ppm=robust_kelly,
        half_kelly_ppm=half_kelly,
        _normalized_document=MappingProxyType(deepcopy(normalized)),
        _validation_seal=_VALIDATED_CONTRACT_SEAL,
    )
    return normalized, typed


def _precheck_carrier_independence(carriers: list[object]) -> None:
    if len(carriers) != 2 or any(type(item) is not dict for item in carriers):
        return
    first, second = carriers
    fields = ("evidence_receipt_id", "evidence_sha256", "input_sha256")
    for field in fields:
        if first.get(field) is not None and first.get(field) == second.get(field):
            raise DataContractError("CARRIER_EVIDENCE_NOT_INDEPENDENT", field)
    first_distribution = first.get("distribution_binding")
    second_distribution = second.get("distribution_binding")
    if type(first_distribution) is dict and type(second_distribution) is dict:
        for field in (
            "distribution_id",
            "distribution_sha256",
            "episode_set_sha256",
        ):
            if (
                first_distribution.get(field) is not None
                and first_distribution.get(field) == second_distribution.get(field)
            ):
                raise DataContractError(
                    "CARRIER_EVIDENCE_NOT_INDEPENDENT", field
                )


def validate_carrier_evidence(document: object) -> CarrierEvidenceBundleV1:
    """Validate independent LC0 and BCS0 episode-bound evidence."""

    _canonical_bytes(document)
    raw = _closed(
        document, _CARRIER_BUNDLE_KEYS, "CARRIER_EVIDENCE_BUNDLE_SCHEMA_INVALID"
    )
    if raw["schema_version"] != CARRIER_EVIDENCE_BUNDLE_SCHEMA_VERSION:
        raise DataContractError("CARRIER_EVIDENCE_BUNDLE_VERSION_UNSUPPORTED")
    classification = _string(
        raw["classification"],
        "CARRIER_EVIDENCE_CLASSIFICATION_INVALID",
        allowed=frozenset(
            {"SYNTHETIC_ONLY", "HISTORICAL_RESEARCH_CANDIDATE"}
        ),
    )
    if (
        raw["scope"] != "GLD_ENTRY_CARRIER_EVIDENCE_ONLY"
        or raw["underlying"] != "GLD"
    ):
        raise DataContractError("CARRIER_EVIDENCE_SCOPE_INVALID")
    rule_hash = _sha256(
        raw["rule_package_sha256"], "CARRIER_EVIDENCE_BINDING_INVALID"
    )
    estimator_hash = _sha256(
        raw["estimator_package_sha256"], "CARRIER_EVIDENCE_BINDING_INVALID"
    )
    if classification != "SYNTHETIC_ONLY":
        raise DataContractError(
            "CARRIER_HISTORICAL_ESTIMATOR_NOT_IMPLEMENTED"
        )
    if estimator_hash != SYNTHETIC_CARRIER_ESTIMATOR_PACKAGE_SHA256:
        raise DataContractError("CARRIER_ESTIMATOR_PACKAGE_INVALID")
    carriers_raw = _list(raw["carriers"], "CARRIER_EVIDENCE_CARRIERS_INVALID")
    _precheck_carrier_independence(carriers_raw)
    normalized_pairs = [_normalize_carrier(item) for item in carriers_raw]
    normalized_pairs.sort(key=lambda pair: pair[1].carrier_id)
    typed = tuple(pair[1] for pair in normalized_pairs)
    if tuple(item.carrier_id for item in typed) != ("BCS0", "LC0"):
        raise DataContractError("CARRIER_EVIDENCE_CARRIERS_INVALID")
    normalized_carriers = [pair[0] for pair in normalized_pairs]
    hashes_raw = _closed(
        raw["content_hashes"],
        frozenset({"LC0", "BCS0"}),
        "CARRIER_EVIDENCE_CONTENT_HASH_SCHEMA_INVALID",
    )
    normalized_hashes: dict[str, object] = {}
    for carrier in typed:
        declared = _sha256(
            hashes_raw[carrier.carrier_id],
            "CARRIER_EVIDENCE_CONTENT_HASH_INVALID",
        )
        if declared != carrier.evidence_sha256:
            raise DataContractError(
                "CARRIER_EVIDENCE_CONTENT_HASH_MISMATCH", carrier.carrier_id
            )
        normalized_hashes[carrier.carrier_id] = declared
    normalized: dict[str, object] = {
        "schema_version": CARRIER_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        "classification": classification,
        "scope": "GLD_ENTRY_CARRIER_EVIDENCE_ONLY",
        "underlying": "GLD",
        "rule_package_sha256": rule_hash,
        "estimator_package_sha256": estimator_hash,
        "carriers": normalized_carriers,
        "content_hashes": normalized_hashes,
    }
    encoded = _canonical_bytes(normalized)
    return CarrierEvidenceBundleV1(
        classification=classification,
        bundle_sha256=_canonical_sha256(normalized),
        canonical_bytes=encoded,
        carriers=typed,
        _normalized_document=MappingProxyType(deepcopy(normalized)),
        _validation_seal=_VALIDATED_CONTRACT_SEAL,
    )


__all__ = ["validate_carrier_evidence", "validate_entry_bundle"]
