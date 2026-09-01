"""Closed-schema validation for GLD management Research F0 inputs."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import re

from .canonical import canonical_json_bytes, canonical_json_sha256
from .contracts import (
    DailyBarF0,
    ManagementActionSnapshotF0,
    ManagementEpisodeF0,
    ManagementScoreObservationF0,
    ManagementTerminalOverrideF0,
    OptionContractF0,
    QuoteF0,
)
from .errors import ManagementResearchError
from .lineage import (
    PREVIOUS_STATE_SCHEMA_VERSION,
    validate_previous_management_state_f0,
)
from .policy import POLICY_SET_VERSION
from .xnys_calendar import (
    CALENDAR_ID,
    CALENDAR_VERSION_SHA256,
    action_1045_utc_ns,
    is_session_date,
    next_session_date,
    official_close_utc_ns,
    session_kind as xnys_session_kind,
    validate_session_sequence,
)


OBSERVATION_SCHEMA_VERSION = "GLD_MANAGEMENT_SCORE_OBSERVATION_F0_V1"
ACTION_SNAPSHOT_SCHEMA_VERSION = "GLD_MANAGEMENT_ACTION_SNAPSHOT_F0_V1"
TERMINAL_OVERRIDE_SCHEMA_VERSION = "GLD_MANAGEMENT_TERMINAL_OVERRIDE_F0_V1"
FORMULA_CATALOG_VERSION = "GLD_MANAGEMENT_FORMULAS_F0_V1"
CLASSIFICATION = "SYNTHETIC_ONLY"
INSTRUMENT_ID = "GLD"
MAX_I64 = 2**63 - 1

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")

_OBSERVATION_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "instrument_id",
        "calendar_id",
        "calendar_version_sha256",
        "formula_catalog_version",
        "policy_set_version",
        "trading_date",
        "observation_utc_ns",
        "daily_bars",
        "episode",
        "previous_state",
    }
)
_BAR_KEYS = frozenset(
    {
        "session_date",
        "session_ordinal",
        "session_kind",
        "session_status",
        "official_close_utc_ns",
        "open_nano_usd",
        "high_nano_usd",
        "low_nano_usd",
        "close_nano_usd",
        "volume_shares",
    }
)
_EPISODE_KEYS = frozenset(
    {
        "episode_id",
        "carrier_id",
        "management_start_date",
        "entry_breakout_line_nano_usd",
        "entry_breakout_source_sha256",
        "original_approved_units",
        "contracts",
    }
)
_CONTRACT_KEYS = frozenset(
    {
        "contract_id",
        "role",
        "expiry_date",
        "strike_nano_usd",
        "multiplier",
        "deliverable_shares",
        "currency",
    }
)
_ACTION_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "instrument_id",
        "action_trading_date",
        "action_utc_ns",
        "episode_id",
        "carrier_id",
        "contracts",
        "current_units",
        "account_risk_cap_units",
        "liquidity_cap_units",
        "external_risk_cap_units",
        "dte",
        "fee_facts_status",
        "reconciliation_status",
        "override_status",
        "quotes",
    }
)
_QUOTE_KEYS = frozenset(
    {
        "contract_id",
        "bid_nano_usd",
        "ask_nano_usd",
        "bid_size",
        "ask_size",
        "tick_nano_usd",
        "event_utc_ns",
        "receive_utc_ns",
    }
)
_TERMINAL_OVERRIDE_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "instrument_id",
        "event_utc_ns",
        "episode_id",
        "carrier_id",
        "contracts",
        "current_units",
        "override_status",
    }
)


def _closed_dict(value: object, keys: frozenset[str], reason: str) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != keys:
        raise ManagementResearchError(reason)
    return value


def _text(value: object, reason: str, *, allowed: frozenset[str] | None = None) -> str:
    if type(value) is not str or not value.isascii():
        raise ManagementResearchError(reason)
    if allowed is not None and value not in allowed:
        raise ManagementResearchError(reason)
    return value


def _identifier(value: object, reason: str) -> str:
    text = _text(value, reason)
    if _ID_RE.fullmatch(text) is None:
        raise ManagementResearchError(reason)
    return text


def _integer(
    value: object,
    reason: str,
    *,
    minimum: int = 0,
    maximum: int = MAX_I64,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ManagementResearchError(reason)
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


def _date(value: object, reason: str) -> tuple[str, date]:
    text = _text(value, reason)
    if _DATE_RE.fullmatch(text) is None:
        raise ManagementResearchError(reason)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ManagementResearchError(reason) from exc
    if parsed.isoformat() != text:
        raise ManagementResearchError(reason)
    return text, parsed


def _contract(value: object) -> OptionContractF0:
    item = _closed_dict(value, _CONTRACT_KEYS, "CONTRACT_SCHEMA_INVALID")
    role = _text(
        item["role"],
        "CONTRACT_IDENTITY_INVALID",
        allowed=frozenset({"LONG_CALL", "SHORT_CALL"}),
    )
    expiry_date, _ = _date(item["expiry_date"], "CONTRACT_IDENTITY_INVALID")
    multiplier = _integer(item["multiplier"], "CONTRACT_IDENTITY_INVALID", minimum=1)
    deliverable = _integer(
        item["deliverable_shares"], "CONTRACT_IDENTITY_INVALID", minimum=1
    )
    currency = _text(item["currency"], "CONTRACT_IDENTITY_INVALID")
    if multiplier != 100 or deliverable != 100 or currency != "USD":
        raise ManagementResearchError("CONTRACT_IDENTITY_INVALID")
    return OptionContractF0(
        contract_id=_identifier(item["contract_id"], "CONTRACT_IDENTITY_INVALID"),
        role=role,
        expiry_date=expiry_date,
        strike_nano_usd=_integer(
            item["strike_nano_usd"], "CONTRACT_IDENTITY_INVALID", minimum=1
        ),
        multiplier=multiplier,
        deliverable_shares=deliverable,
        currency=currency,
    )


def _contracts(value: object, carrier_id: str) -> tuple[OptionContractF0, ...]:
    if type(value) is not list:
        raise ManagementResearchError("CONTRACT_SET_INVALID")
    parsed = tuple(sorted((_contract(item) for item in value), key=lambda x: (x.role, x.contract_id)))
    if len({item.contract_id for item in parsed}) != len(parsed):
        raise ManagementResearchError("CONTRACT_SET_INVALID")
    roles = tuple(item.role for item in parsed)
    if carrier_id == "LC0" and roles != ("LONG_CALL",):
        raise ManagementResearchError("CONTRACT_SET_INVALID")
    if carrier_id == "BCS0" and roles != ("LONG_CALL", "SHORT_CALL"):
        raise ManagementResearchError("CONTRACT_SET_INVALID")
    if carrier_id == "BCS0":
        long_call, short_call = parsed
        if (
            long_call.expiry_date != short_call.expiry_date
            or long_call.multiplier != short_call.multiplier
            or long_call.deliverable_shares != short_call.deliverable_shares
            or long_call.currency != short_call.currency
            or long_call.strike_nano_usd >= short_call.strike_nano_usd
        ):
            raise ManagementResearchError("CONTRACT_SET_INVALID")
    return parsed


def _episode(value: object, trading_date: date) -> ManagementEpisodeF0:
    item = _closed_dict(value, _EPISODE_KEYS, "EPISODE_SCHEMA_INVALID")
    episode_id = _identifier(item["episode_id"], "EPISODE_ID_INVALID")
    carrier_id = _text(
        item["carrier_id"],
        "CARRIER_ID_INVALID",
        allowed=frozenset({"LC0", "BCS0"}),
    )
    contracts = _contracts(item["contracts"], carrier_id)
    management_start_date, parsed_management_start = _date(
        item["management_start_date"], "EPISODE_START_DATE_INVALID"
    )
    if parsed_management_start > trading_date or not is_session_date(
        parsed_management_start
    ):
        raise ManagementResearchError("EPISODE_START_DATE_INVALID")
    if any(date.fromisoformat(contract.expiry_date) <= trading_date for contract in contracts):
        raise ManagementResearchError("CONTRACT_EXPIRY_INVALID")
    breakout_line = _integer(
        item["entry_breakout_line_nano_usd"],
        "EPISODE_BREAKOUT_FACT_INVALID",
        minimum=1,
    )
    breakout_source_hash = _text(
        item["entry_breakout_source_sha256"],
        "EPISODE_BREAKOUT_FACT_INVALID",
    )
    if breakout_source_hash != canonical_json_sha256(
        {
            "episode_id": episode_id,
            "entry_breakout_line_nano_usd": breakout_line,
        }
    ):
        raise ManagementResearchError("EPISODE_BREAKOUT_FACT_INVALID")
    return ManagementEpisodeF0(
        episode_id=episode_id,
        carrier_id=carrier_id,
        management_start_date=management_start_date,
        entry_breakout_line_nano_usd=breakout_line,
        entry_breakout_source_sha256=breakout_source_hash,
        original_approved_units=_integer(
            item["original_approved_units"], "ORIGINAL_UNITS_INVALID", minimum=1
        ),
        contracts=contracts,
    )


def _daily_bar(value: object) -> tuple[DailyBarF0, date]:
    item = _closed_dict(value, _BAR_KEYS, "DAILY_BAR_SCHEMA_INVALID")
    session_date, parsed_date = _date(item["session_date"], "DAILY_BAR_DATE_INVALID")
    session_kind = _text(
        item["session_kind"],
        "DAILY_BAR_SESSION_KIND_INVALID",
        allowed=frozenset({"NORMAL", "EARLY_CLOSE"}),
    )
    session_status = _text(
        item["session_status"],
        "DAILY_BAR_SESSION_STATUS_INVALID",
        allowed=frozenset({"COMPLETE"}),
    )
    open_price = _integer(item["open_nano_usd"], "DAILY_BAR_PRICE_INVALID", minimum=1)
    high = _integer(item["high_nano_usd"], "DAILY_BAR_PRICE_INVALID", minimum=1)
    low = _integer(item["low_nano_usd"], "DAILY_BAR_PRICE_INVALID", minimum=1)
    close = _integer(item["close_nano_usd"], "DAILY_BAR_PRICE_INVALID", minimum=1)
    if low > min(open_price, close) or high < max(open_price, close) or low > high:
        raise ManagementResearchError("DAILY_BAR_OHLC_INVALID")
    return (
        DailyBarF0(
            session_date=session_date,
            session_ordinal=_integer(
                item["session_ordinal"], "DAILY_BAR_SESSION_SEQUENCE_INVALID", minimum=1
            ),
            session_kind=session_kind,
            session_status=session_status,
            official_close_utc_ns=_integer(
                item["official_close_utc_ns"], "DAILY_BAR_TIME_INVALID", minimum=1
            ),
            open_nano_usd=open_price,
            high_nano_usd=high,
            low_nano_usd=low,
            close_nano_usd=close,
            volume_shares=_integer(item["volume_shares"], "DAILY_BAR_VOLUME_INVALID"),
        ),
        parsed_date,
    )


def validate_management_score_observation_f0(
    document: object,
) -> ManagementScoreObservationF0:
    """Validate, normalize, sort, freeze, and hash one raw observation."""

    typed_document = (
        document if isinstance(document, ManagementScoreObservationF0) else None
    )
    if typed_document is not None:
        document = typed_document.as_dict()
    canonical_json_bytes(document)
    item = _closed_dict(document, _OBSERVATION_KEYS, "OBSERVATION_SCHEMA_INVALID")
    if item["schema_version"] != OBSERVATION_SCHEMA_VERSION:
        raise ManagementResearchError("OBSERVATION_SCHEMA_VERSION_INVALID")
    if item["classification"] != CLASSIFICATION:
        raise ManagementResearchError("OBSERVATION_CLASSIFICATION_INVALID")
    if item["instrument_id"] != INSTRUMENT_ID:
        raise ManagementResearchError("OBSERVATION_INSTRUMENT_INVALID")
    if item["formula_catalog_version"] != FORMULA_CATALOG_VERSION:
        raise ManagementResearchError("FORMULA_CATALOG_VERSION_INVALID")
    if item["policy_set_version"] != POLICY_SET_VERSION:
        raise ManagementResearchError("POLICY_SET_VERSION_INVALID")
    calendar_id = _identifier(item["calendar_id"], "CALENDAR_ID_INVALID")
    calendar_hash = _text(item["calendar_version_sha256"], "CALENDAR_HASH_INVALID")
    if calendar_id != CALENDAR_ID:
        raise ManagementResearchError("CALENDAR_ID_INVALID")
    if calendar_hash != CALENDAR_VERSION_SHA256:
        raise ManagementResearchError("CALENDAR_HASH_INVALID")
    trading_date, parsed_trading_date = _date(
        item["trading_date"], "OBSERVATION_TRADING_DATE_INVALID"
    )
    observation_ns = _integer(
        item["observation_utc_ns"], "OBSERVATION_TIME_INVALID", minimum=1
    )
    raw_bars = item["daily_bars"]
    if type(raw_bars) is not list or len(raw_bars) != 220:
        raise ManagementResearchError("DAILY_BAR_COUNT_INVALID")
    bars_and_dates = [_daily_bar(value) for value in raw_bars]
    bars_and_dates.sort(key=lambda pair: pair[0].session_ordinal)
    bars = tuple(pair[0] for pair in bars_and_dates)
    dates = tuple(pair[1] for pair in bars_and_dates)
    if tuple(bar.session_ordinal for bar in bars) != tuple(range(1, 221)):
        raise ManagementResearchError("DAILY_BAR_SESSION_SEQUENCE_INVALID")
    if len(set(dates)) != 220:
        raise ManagementResearchError("DAILY_BAR_SESSION_SEQUENCE_INVALID")
    try:
        validate_session_sequence(dates)
    except ManagementResearchError as exc:
        raise ManagementResearchError(
            "DAILY_BAR_SESSION_SEQUENCE_INVALID"
        ) from exc
    for bar, session_date_value in zip(bars, dates):
        if bar.session_kind != xnys_session_kind(session_date_value):
            raise ManagementResearchError("DAILY_BAR_SESSION_KIND_MISMATCH")
        if bar.official_close_utc_ns != official_close_utc_ns(session_date_value):
            raise ManagementResearchError("DAILY_BAR_OFFICIAL_CLOSE_MISMATCH")
    if any(
        current.official_close_utc_ns <= previous.official_close_utc_ns
        for previous, current in zip(bars, bars[1:])
    ):
        raise ManagementResearchError("DAILY_BAR_TIME_SEQUENCE_INVALID")
    if any(bar.official_close_utc_ns > observation_ns for bar in bars):
        raise ManagementResearchError("DAILY_BAR_FUTURE_DATA")
    if trading_date != bars[-1].session_date or parsed_trading_date != dates[-1]:
        raise ManagementResearchError("OBSERVATION_TRADING_DATE_INVALID")
    episode = _episode(item["episode"], parsed_trading_date)
    previous_state = validate_previous_management_state_f0(
        item["previous_state"],
        episode=episode,
        previous_trading_date=bars[-2].session_date,
    )
    normalized = {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "classification": CLASSIFICATION,
        "instrument_id": INSTRUMENT_ID,
        "calendar_id": calendar_id,
        "calendar_version_sha256": calendar_hash,
        "formula_catalog_version": FORMULA_CATALOG_VERSION,
        "policy_set_version": POLICY_SET_VERSION,
        "trading_date": trading_date,
        "observation_utc_ns": observation_ns,
        "daily_bars": [bar.as_dict() for bar in bars],
        "episode": episode.as_dict(),
        "previous_state": previous_state.as_dict(),
    }
    encoded = canonical_json_bytes(normalized)
    validated = ManagementScoreObservationF0(
        schema_version=OBSERVATION_SCHEMA_VERSION,
        classification=CLASSIFICATION,
        instrument_id=INSTRUMENT_ID,
        calendar_id=calendar_id,
        calendar_version_sha256=calendar_hash,
        formula_catalog_version=FORMULA_CATALOG_VERSION,
        policy_set_version=POLICY_SET_VERSION,
        trading_date=trading_date,
        observation_utc_ns=observation_ns,
        daily_bars=bars,
        episode=episode,
        previous_state=previous_state,
        input_sha256=sha256(encoded).hexdigest(),
        canonical_bytes=encoded,
    )
    if typed_document is not None and (
        typed_document.input_sha256 != validated.input_sha256
        or typed_document.canonical_bytes != validated.canonical_bytes
    ):
        raise ManagementResearchError("OBSERVATION_TYPED_SEAL_INVALID")
    return validated


def _quote(value: object, action_ns: int) -> QuoteF0:
    item = _closed_dict(value, _QUOTE_KEYS, "QUOTE_SCHEMA_INVALID")
    bid = _integer(item["bid_nano_usd"], "QUOTE_PRICE_INVALID", minimum=1)
    ask = _integer(item["ask_nano_usd"], "QUOTE_PRICE_INVALID", minimum=1)
    tick = _integer(item["tick_nano_usd"], "QUOTE_TICK_INVALID", minimum=1)
    event_ns = _integer(item["event_utc_ns"], "QUOTE_TIME_INVALID", minimum=1)
    receive_ns = _integer(item["receive_utc_ns"], "QUOTE_TIME_INVALID", minimum=1)
    if bid > ask:
        raise ManagementResearchError("QUOTE_CROSSED")
    if bid % tick != 0 or ask % tick != 0:
        raise ManagementResearchError("QUOTE_TICK_ALIGNMENT_INVALID")
    if not event_ns <= receive_ns <= action_ns:
        raise ManagementResearchError("QUOTE_TIME_INVALID")
    return QuoteF0(
        contract_id=_identifier(item["contract_id"], "QUOTE_CONTRACT_ID_INVALID"),
        bid_nano_usd=bid,
        ask_nano_usd=ask,
        bid_size=_integer(item["bid_size"], "QUOTE_SIZE_INVALID"),
        ask_size=_integer(item["ask_size"], "QUOTE_SIZE_INVALID"),
        tick_nano_usd=tick,
        event_utc_ns=event_ns,
        receive_utc_ns=receive_ns,
    )


def validate_management_action_snapshot_f0(
    document: object,
    score_observation: ManagementScoreObservationF0 | object,
) -> ManagementActionSnapshotF0:
    """Validate and bind an action snapshot to one score observation/result."""

    canonical_json_bytes(document)
    item = _closed_dict(document, _ACTION_KEYS, "ACTION_SNAPSHOT_SCHEMA_INVALID")
    if item["schema_version"] != ACTION_SNAPSHOT_SCHEMA_VERSION:
        raise ManagementResearchError("ACTION_SNAPSHOT_SCHEMA_VERSION_INVALID")
    if item["classification"] != CLASSIFICATION:
        raise ManagementResearchError("ACTION_SNAPSHOT_CLASSIFICATION_INVALID")
    if item["instrument_id"] != INSTRUMENT_ID:
        raise ManagementResearchError("ACTION_SNAPSHOT_INSTRUMENT_INVALID")
    # score_observation is intentionally duck-typed to avoid a circular import.
    episode = getattr(score_observation, "episode", None)
    observation_ns = getattr(score_observation, "observation_utc_ns", None)
    if not isinstance(episode, ManagementEpisodeF0) or type(observation_ns) is not int:
        raise ManagementResearchError("ACTION_SCORE_BINDING_INVALID")
    action_date, parsed_action_date = _date(
        item["action_trading_date"], "ACTION_TRADING_DATE_INVALID"
    )
    action_ns = _integer(item["action_utc_ns"], "ACTION_TIME_INVALID", minimum=1)
    if action_ns <= observation_ns:
        raise ManagementResearchError("ACTION_TIME_INVALID")
    score_trading_date = getattr(score_observation, "trading_date", None)
    try:
        parsed_score_date = date.fromisoformat(score_trading_date)
    except (TypeError, ValueError) as exc:
        raise ManagementResearchError("ACTION_SCORE_BINDING_INVALID") from exc
    if parsed_action_date != next_session_date(parsed_score_date):
        raise ManagementResearchError("ACTION_TRADING_DATE_INVALID")
    if action_ns != action_1045_utc_ns(parsed_action_date):
        raise ManagementResearchError("ACTION_1045_TIME_INVALID")
    episode_id = _identifier(item["episode_id"], "ACTION_EPISODE_ID_INVALID")
    carrier_id = _text(
        item["carrier_id"],
        "ACTION_CARRIER_ID_INVALID",
        allowed=frozenset({"LC0", "BCS0"}),
    )
    contracts = _contracts(item["contracts"], carrier_id)
    if (
        episode_id != episode.episode_id
        or carrier_id != episode.carrier_id
        or tuple(value.identity_tuple() for value in contracts)
        != tuple(value.identity_tuple() for value in episode.contracts)
    ):
        raise ManagementResearchError("ACTION_EPISODE_IDENTITY_MISMATCH")
    raw_quotes = item["quotes"]
    if type(raw_quotes) is not list:
        raise ManagementResearchError("QUOTE_SET_INVALID")
    quotes = tuple(sorted((_quote(value, action_ns) for value in raw_quotes), key=lambda x: x.contract_id))
    quote_ids = tuple(quote.contract_id for quote in quotes)
    expected_quote_ids = frozenset(contract.contract_id for contract in contracts)
    if len(set(quote_ids)) != len(quote_ids) or any(
        contract_id not in expected_quote_ids for contract_id in quote_ids
    ):
        raise ManagementResearchError("QUOTE_SET_INVALID")
    dte = _optional_integer(item["dte"], "DTE_INVALID")
    expected_dte = min(
        date.fromisoformat(contract.expiry_date) for contract in contracts
    ) - parsed_action_date
    if expected_dte.days <= 0:
        raise ManagementResearchError("EXPIRY_SAFETY_TERMINAL_FACT_REQUIRED")
    if dte is not None and dte != expected_dte.days:
        raise ManagementResearchError("DTE_IDENTITY_MISMATCH")
    current_units = _integer(item["current_units"], "CURRENT_UNITS_INVALID")
    updated_state = getattr(score_observation, "updated_state", None)
    latch = getattr(updated_state, "exit_latch_status", "CLEAR")
    if current_units == 0 and latch != "EXIT_DUE_LATCHED":
        raise ManagementResearchError("CURRENT_UNITS_LIFECYCLE_CONFLICT")
    normalized = {
        "schema_version": ACTION_SNAPSHOT_SCHEMA_VERSION,
        "classification": CLASSIFICATION,
        "instrument_id": INSTRUMENT_ID,
        "action_trading_date": action_date,
        "action_utc_ns": action_ns,
        "episode_id": episode_id,
        "carrier_id": carrier_id,
        "contracts": [value.as_dict() for value in contracts],
        "current_units": current_units,
        "account_risk_cap_units": _optional_integer(
            item["account_risk_cap_units"], "ACCOUNT_RISK_CAP_INVALID"
        ),
        "liquidity_cap_units": _optional_integer(
            item["liquidity_cap_units"], "LIQUIDITY_CAP_INVALID"
        ),
        "external_risk_cap_units": _optional_integer(
            item["external_risk_cap_units"], "EXTERNAL_RISK_CAP_INVALID"
        ),
        "dte": dte,
        "fee_facts_status": _text(
            item["fee_facts_status"],
            "FEE_FACTS_STATUS_INVALID",
            allowed=frozenset({"COMPLETE", "MISSING", "CONFLICT"}),
        ),
        "reconciliation_status": _text(
            item["reconciliation_status"],
            "RECONCILIATION_STATUS_INVALID",
            allowed=frozenset(
                {"MATCHED", "RESIDUAL_LEG", "IDENTITY_MISMATCH", "PARTIAL_FILL"}
            ),
        ),
        "override_status": _text(
            item["override_status"],
            "OVERRIDE_STATUS_INVALID",
            allowed=frozenset(
                {
                    "NONE",
                    "DATA_NOT_QUALIFIED",
                }
            ),
        ),
        "quotes": [value.as_dict() for value in quotes],
    }
    return ManagementActionSnapshotF0(
        schema_version=ACTION_SNAPSHOT_SCHEMA_VERSION,
        classification=CLASSIFICATION,
        instrument_id=INSTRUMENT_ID,
        action_trading_date=action_date,
        action_utc_ns=action_ns,
        episode_id=episode_id,
        carrier_id=carrier_id,
        contracts=contracts,
        current_units=normalized["current_units"],
        account_risk_cap_units=normalized["account_risk_cap_units"],
        liquidity_cap_units=normalized["liquidity_cap_units"],
        external_risk_cap_units=normalized["external_risk_cap_units"],
        dte=normalized["dte"],
        fee_facts_status=normalized["fee_facts_status"],
        reconciliation_status=normalized["reconciliation_status"],
        override_status=normalized["override_status"],
        quotes=quotes,
        snapshot_sha256=canonical_json_sha256(normalized),
    )


def validate_management_terminal_override_f0(
    document: object,
    score_observation: ManagementScoreObservationF0 | object,
) -> ManagementTerminalOverrideF0:
    """Validate an event-driven terminal override without 10:45/DTE facts."""

    canonical_json_bytes(document)
    item = _closed_dict(
        document, _TERMINAL_OVERRIDE_KEYS, "TERMINAL_OVERRIDE_SCHEMA_INVALID"
    )
    if item["schema_version"] != TERMINAL_OVERRIDE_SCHEMA_VERSION:
        raise ManagementResearchError("TERMINAL_OVERRIDE_SCHEMA_VERSION_INVALID")
    if item["classification"] != CLASSIFICATION:
        raise ManagementResearchError("TERMINAL_OVERRIDE_CLASSIFICATION_INVALID")
    if item["instrument_id"] != INSTRUMENT_ID:
        raise ManagementResearchError("TERMINAL_OVERRIDE_INSTRUMENT_INVALID")
    episode = getattr(score_observation, "episode", None)
    observation_ns = getattr(score_observation, "observation_utc_ns", None)
    if not isinstance(episode, ManagementEpisodeF0) or type(observation_ns) is not int:
        raise ManagementResearchError("TERMINAL_OVERRIDE_SCORE_BINDING_INVALID")
    event_ns = _integer(
        item["event_utc_ns"], "TERMINAL_OVERRIDE_TIME_INVALID", minimum=1
    )
    if event_ns <= observation_ns:
        raise ManagementResearchError("TERMINAL_OVERRIDE_TIME_INVALID")
    episode_id = _identifier(
        item["episode_id"], "TERMINAL_OVERRIDE_EPISODE_INVALID"
    )
    carrier_id = _text(
        item["carrier_id"],
        "TERMINAL_OVERRIDE_CARRIER_INVALID",
        allowed=frozenset({"LC0", "BCS0"}),
    )
    contracts = _contracts(item["contracts"], carrier_id)
    if (
        episode_id != episode.episode_id
        or carrier_id != episode.carrier_id
        or tuple(value.identity_tuple() for value in contracts)
        != tuple(value.identity_tuple() for value in episode.contracts)
    ):
        raise ManagementResearchError("TERMINAL_OVERRIDE_IDENTITY_MISMATCH")
    normalized = {
        "schema_version": TERMINAL_OVERRIDE_SCHEMA_VERSION,
        "classification": CLASSIFICATION,
        "instrument_id": INSTRUMENT_ID,
        "event_utc_ns": event_ns,
        "episode_id": episode_id,
        "carrier_id": carrier_id,
        "contracts": [value.as_dict() for value in contracts],
        "current_units": _integer(item["current_units"], "CURRENT_UNITS_INVALID"),
        "override_status": _text(
            item["override_status"],
            "TERMINAL_OVERRIDE_STATUS_INVALID",
            allowed=frozenset(
                {"HARD_STOP", "EXPIRY_SAFETY", "CONFIRMED_INVALIDATION"}
            ),
        ),
    }
    return ManagementTerminalOverrideF0(
        schema_version=TERMINAL_OVERRIDE_SCHEMA_VERSION,
        classification=CLASSIFICATION,
        instrument_id=INSTRUMENT_ID,
        event_utc_ns=event_ns,
        episode_id=episode_id,
        carrier_id=carrier_id,
        contracts=contracts,
        current_units=normalized["current_units"],
        override_status=normalized["override_status"],
        snapshot_sha256=canonical_json_sha256(normalized),
    )


__all__ = [
    "ACTION_SNAPSHOT_SCHEMA_VERSION",
    "CLASSIFICATION",
    "FORMULA_CATALOG_VERSION",
    "INSTRUMENT_ID",
    "OBSERVATION_SCHEMA_VERSION",
    "POLICY_SET_VERSION",
    "PREVIOUS_STATE_SCHEMA_VERSION",
    "TERMINAL_OVERRIDE_SCHEMA_VERSION",
    "validate_management_action_snapshot_f0",
    "validate_management_score_observation_f0",
    "validate_management_terminal_override_f0",
]
