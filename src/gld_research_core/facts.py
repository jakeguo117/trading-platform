"""Deterministic structural facts for GLD option research snapshots.

These types validate immutable, integer-only research inputs.  They do not
connect to a provider, qualify source authority, create orders, or submit them.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from itertools import chain
import json
import re
import sys
from types import MappingProxyType
from zoneinfo import ZoneInfo

from gld_normalizer.errors import NormalizationError
from gld_normalizer.readers import (
    MAX_DEFINED_I64,
    MAX_DEFINED_U32,
    MAX_DEFINED_U64,
)


REAL_OPTION_FACT_EXECUTION_STATUS = (
    "NOT_IMPLEMENTED_QUALIFIED_SOURCE_BINDING_REQUIRED"
)

OPTION_CAPTURE_WINDOW_NS = 60_000_000_000
QUOTE_QUALITY_POLICY_VERSION = "GLD_OPTION_QUOTE_QUALITY_POLICY_V1"
QUOTE_AGE_LIMIT_NS = 5_000_000_000
RECEIVE_SKEW_LIMIT_NS = 1_000_000_000
QUOTE_QUALITY_POLICY_SHA256 = sha256(
    json.dumps(
        {
            "domain": "gld.option-quote-quality-policy",
            "hash_format": "GLD_FROZEN_POLICY_JSON_V1",
            "policy_version": QUOTE_QUALITY_POLICY_VERSION,
            "quote_age_limit_ns": {"exact_int": str(QUOTE_AGE_LIMIT_NS)},
            "receive_skew_limit_ns": {
                "exact_int": str(RECEIVE_SKEW_LIMIT_NS)
            },
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
).hexdigest()
MAX_CONTROL_TEXT_CHARS = 256
MAX_BOOK_FLAGS = 64
MAX_OPTION_QUOTES = 100_000
# Structural resource guard only; it does not qualify a provider or source.
MAX_OPTION_SNAPSHOT_CANDIDATES = 64
MAX_CANONICAL_DEPTH = 32
MAX_CANONICAL_NODES = 500_000
MAX_CANONICAL_ENCODED_BYTES = 32 * 1024 * 1024
MAX_CANONICAL_INTEGER_BITS = 64

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_FLAG_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_OCC_SUFFIX_RE = re.compile(
    r"(?P<expiry>[0-9]{6})(?P<right>[CP])(?P<strike_milli_usd>[0-9]{8})\Z"
)
_SIGNAL_STATES = frozenset({"PASS", "FAIL", "NOT_EVALUABLE"})
_NEW_YORK = ZoneInfo("America/New_York")
_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_CANONICAL_HASH_FORMAT = "GLD_CANONICAL_JSON_V1"
_CANONICAL_ROOT_SCHEMAS = MappingProxyType({
    ("gld_research_core.facts", "SignalSnapshotV1"): (
        "gld.signal-snapshot",
        "v1",
    ),
    ("gld_research_core.facts", "OptionContractV1"): (
        "gld.option-contract",
        "v1",
    ),
    ("gld_research_core.facts", "TopOfBookV1"): (
        "gld.top-of-book",
        "v1",
    ),
    ("gld_research_core.facts", "OptionQuoteV1"): (
        "gld.option-quote",
        "v1",
    ),
    ("gld_research_core.facts", "OptionQuoteSnapshotV1"): (
        "gld.option-quote-snapshot",
        "v1",
    ),
    ("gld_research_core.facts", "OptionSnapshotCandidateLedgerV1"): (
        "gld.option-snapshot-candidate-ledger",
        "v1",
    ),
    ("gld_research_core.crr_delta", "CrrCallInputsV1"): (
        "gld.crr-call-inputs",
        "v1",
    ),
    ("gld_research_core.crr_delta", "CrrModelArtifactManifestV1"): (
        "gld.crr-model-artifact-manifest",
        "v1",
    ),
    ("gld_research_core.crr_delta", "CrrRuntimeFingerprintV1"): (
        "gld.crr-runtime-fingerprint",
        "v1",
    ),
    ("gld_research_core.crr_delta", "CrrPitInputsV1"): (
        "gld.crr-pit-inputs",
        "v1",
    ),
    ("gld_research_core.crr_delta", "CrrRunManifestV1"): (
        "gld.crr-run-manifest",
        "v1",
    ),
    ("gld_research_core.bcs", "Lc0SelectionBindingV1"): (
        "gld.lc0-selection-binding",
        "v1",
    ),
    ("gld_research_core.bcs", "BcsFeeScheduleV1"): (
        "gld.bcs-fee-schedule",
        "v1",
    ),
    ("gld_research_core.bcs", "CrrDeltaEvidenceV1"): (
        "gld.crr-delta-evidence",
        "v1",
    ),
    ("gld_research_core.bcs", "BcsSelectionV1"): (
        "gld.bcs-selection",
        "v1",
    ),
    ("gld_research_core.bcs", "BcsEntryCostV1"): (
        "gld.bcs-entry-cost",
        "v1",
    ),
    ("gld_research_core.bcs", "BcsExitCostV1"): (
        "gld.bcs-exit-cost",
        "v1",
    ),
    ("gld_research_core.bcs", "BcsEvaluationV1"): (
        "gld.bcs-evaluation",
        "v1",
    ),
    ("gld_research_core.bcs", "BcsTradeEpisodeV1"): (
        "gld.bcs-trade-episode",
        "v1",
    ),
    ("gld_research_core.bcs", "BcsManagementSnapshotV1"): (
        "gld.bcs-management-snapshot",
        "v1",
    ),
    ("gld_research_core.bcs", "BrokerReconciliationSnapshotBV1"): (
        "gld.broker-reconciliation-snapshot-b",
        "v1",
    ),
    ("gld_research_core.bcs", "CarrierTerminalResultV1"): (
        "gld.carrier-terminal-result",
        "v1",
    ),
    ("gld_research_core.bcs", "DecisionArtifactV1"): (
        "gld.decision-artifact",
        "v1",
    ),
    ("gld_research_core.bcs", "BcsLifecycleTransitionV1"): (
        "gld.bcs-lifecycle-transition",
        "v1",
    ),
})


def _require_exact_int(
    value: object,
    *,
    reason_code: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise NormalizationError(reason_code)
    return value


def _require_text(value: object, *, reason_code: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > MAX_CONTROL_TEXT_CHARS
        or not value.isascii()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise NormalizationError(reason_code)
    return value


def _require_sha256(value: object, *, reason_code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise NormalizationError(reason_code)
    return value


def _require_frozen_quote_quality_policy(
    *,
    quote_age_limit_ns: object,
    receive_skew_limit_ns: object,
) -> None:
    if (
        type(quote_age_limit_ns) is not int
        or quote_age_limit_ns != QUOTE_AGE_LIMIT_NS
        or type(receive_skew_limit_ns) is not int
        or receive_skew_limit_ns != RECEIVE_SKEW_LIMIT_NS
    ):
        raise NormalizationError("QUOTE_QUALITY_POLICY_MISMATCH")


def _canonical_value(
    value: object,
    *,
    _depth: int = 0,
    _nodes: list[int] | None = None,
    _active_container_ids: set[int] | None = None,
) -> object:
    """Return bounded JSON-safe content with stable integer semantics."""

    nodes = [0] if _nodes is None else _nodes
    active_container_ids = (
        set() if _active_container_ids is None else _active_container_ids
    )
    if _depth > MAX_CANONICAL_DEPTH:
        raise NormalizationError("CANONICAL_SNAPSHOT_LIMIT_EXCEEDED")
    nodes[0] += 1
    if nodes[0] > MAX_CANONICAL_NODES:
        raise NormalizationError("CANONICAL_SNAPSHOT_LIMIT_EXCEEDED")

    is_container = (
        (is_dataclass(value) and not isinstance(value, type))
        or type(value) in {dict, tuple}
    )
    container_id = id(value)
    if is_container:
        if container_id in active_container_ids:
            raise NormalizationError("CANONICAL_SNAPSHOT_INVALID")
        active_container_ids.add(container_id)
    try:
        if is_dataclass(value) and not isinstance(value, type):
            return {
                "fields": {
                    field.name: _canonical_value(
                        getattr(value, field.name),
                        _depth=_depth + 1,
                        _nodes=nodes,
                        _active_container_ids=active_container_ids,
                    )
                    for field in fields(value)
                },
                "schema": type(value).__name__,
            }
        if type(value) is dict:
            canonical_mapping: dict[str, object] = {}
            for key, item in value.items():
                if type(key) is not str or len(key) > MAX_CONTROL_TEXT_CHARS:
                    raise NormalizationError("CANONICAL_SNAPSHOT_INVALID")
                canonical_mapping[key] = _canonical_value(
                    item,
                    _depth=_depth + 1,
                    _nodes=nodes,
                    _active_container_ids=active_container_ids,
                )
            return canonical_mapping
        if type(value) is tuple:
            return [
                _canonical_value(
                    item,
                    _depth=_depth + 1,
                    _nodes=nodes,
                    _active_container_ids=active_container_ids,
                )
                for item in value
            ]
        if type(value) is int:
            if value.bit_length() > MAX_CANONICAL_INTEGER_BITS:
                raise NormalizationError("CANONICAL_SNAPSHOT_LIMIT_EXCEEDED")
            return {"exact_int": str(value)}
        if type(value) is str:
            if len(value) > MAX_CANONICAL_ENCODED_BYTES // 6:
                raise NormalizationError("CANONICAL_SNAPSHOT_LIMIT_EXCEEDED")
            return value
        if type(value) is bool or value is None:
            return value
        raise NormalizationError("CANONICAL_SNAPSHOT_INVALID")
    finally:
        if is_container:
            active_container_ids.remove(container_id)


def _canonical_root_schema(value: object) -> tuple[str, str]:
    root_type = type(value)
    type_key = (root_type.__module__, root_type.__qualname__)
    schema = _CANONICAL_ROOT_SCHEMAS.get(type_key)
    module = sys.modules.get(root_type.__module__)
    resolved_type = getattr(module, root_type.__qualname__, None) if module else None
    if schema is None or resolved_type is not root_type:
        raise NormalizationError("CANONICAL_SNAPSHOT_INVALID")
    return schema


def canonical_snapshot_sha256(value: object) -> str:
    """Hash one exact fact type in a bounded domain/version envelope."""

    domain, schema_version = _canonical_root_schema(value)
    envelope = {
        "domain": domain,
        "hash_format": _CANONICAL_HASH_FORMAT,
        "payload": _canonical_value(value),
        "schema_version": schema_version,
    }
    digest = sha256()
    encoded_bytes = 0
    encoder = json.JSONEncoder(
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    for chunk in encoder.iterencode(envelope):
        encoded = chunk.encode("ascii")
        encoded_bytes += len(encoded)
        if encoded_bytes > MAX_CANONICAL_ENCODED_BYTES:
            raise NormalizationError("CANONICAL_SNAPSHOT_LIMIT_EXCEEDED")
        digest.update(encoded)
    return digest.hexdigest()


def _new_york_cutoff_utc_ns(trading_day: date) -> int:
    local_cutoff = datetime.combine(
        trading_day,
        time(hour=10, minute=45),
        tzinfo=_NEW_YORK,
    )
    delta = local_cutoff.astimezone(timezone.utc) - _UTC_EPOCH
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000_000_000
        + delta.microseconds * 1_000
    )


@dataclass(frozen=True, slots=True)
class SignalSnapshotV1:
    signal_id: str
    trading_date: str
    rule_version: str
    rule_sha256: str
    calendar_authority_id: str
    calendar_version: str
    calendar_sha256: str
    phase_receipt_sha256: str
    completeness_receipt_sha256: str
    cutoff_utc_ns: int
    max_event_utc_ns: int
    max_receive_utc_ns: int
    signal_state: str
    input_fact_sha256: str

    def __post_init__(self) -> None:
        _require_text(self.signal_id, reason_code="SIGNAL_SNAPSHOT_INVALID")
        if type(self.trading_date) is not str:
            raise NormalizationError("TRADING_CALENDAR_BINDING_INVALID")
        try:
            trading_day = date.fromisoformat(self.trading_date)
        except ValueError as error:
            raise NormalizationError("TRADING_CALENDAR_BINDING_INVALID") from error
        if (
            trading_day.isoformat() != self.trading_date
            or trading_day.weekday() >= 5
        ):
            raise NormalizationError("TRADING_CALENDAR_BINDING_INVALID")
        _require_text(
            self.rule_version,
            reason_code="SIGNAL_RULE_BINDING_INVALID",
        )
        _require_sha256(
            self.rule_sha256,
            reason_code="SIGNAL_RULE_BINDING_INVALID",
        )
        _require_text(
            self.calendar_authority_id,
            reason_code="TRADING_CALENDAR_BINDING_INVALID",
        )
        _require_text(
            self.calendar_version,
            reason_code="TRADING_CALENDAR_BINDING_INVALID",
        )
        _require_sha256(
            self.calendar_sha256,
            reason_code="TRADING_CALENDAR_BINDING_INVALID",
        )
        for receipt_sha256 in (
            self.phase_receipt_sha256,
            self.completeness_receipt_sha256,
        ):
            _require_sha256(
                receipt_sha256,
                reason_code="SIGNAL_SNAPSHOT_INVALID",
            )
        cutoff = _require_exact_int(
            self.cutoff_utc_ns,
            reason_code="SIGNAL_SNAPSHOT_INVALID",
            minimum=1,
            maximum=MAX_DEFINED_U64,
        )
        try:
            expected_cutoff = _new_york_cutoff_utc_ns(trading_day)
        except (OverflowError, ValueError) as error:
            raise NormalizationError("TRADING_CALENDAR_BINDING_INVALID") from error
        if cutoff != expected_cutoff:
            raise NormalizationError("SIGNAL_CUTOFF_INVALID")
        max_event = _require_exact_int(
            self.max_event_utc_ns,
            reason_code="SIGNAL_SNAPSHOT_INVALID",
            minimum=1,
            maximum=MAX_DEFINED_U64,
        )
        max_receive = _require_exact_int(
            self.max_receive_utc_ns,
            reason_code="SIGNAL_SNAPSHOT_INVALID",
            minimum=1,
            maximum=MAX_DEFINED_U64,
        )
        if (
            max_event >= cutoff
            or max_receive >= cutoff
            or max_receive < max_event
        ):
            raise NormalizationError("SIGNAL_CAUSALITY_VIOLATION")
        if type(self.signal_state) is not str or self.signal_state not in _SIGNAL_STATES:
            raise NormalizationError("SIGNAL_SNAPSHOT_INVALID")
        _require_sha256(
            self.input_fact_sha256,
            reason_code="SIGNAL_SNAPSHOT_INVALID",
        )

    @property
    def snapshot_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class OptionContractV1:
    occ_symbol: str
    underlying: str
    right: str
    strike_nano_usd: int
    expiry_utc_ns: int
    last_trading_utc_ns: int
    activation_utc_ns: int
    multiplier: int
    deliverable: str
    currency: str
    standard_unadjusted: bool
    exercise_style: str

    def __post_init__(self) -> None:
        if type(self.occ_symbol) is not str or len(self.occ_symbol) != 21:
            raise NormalizationError("IDENTITY_UNRESOLVED")
        suffix = self.occ_symbol[-15:]
        root_field = self.occ_symbol[:-15]
        match = _OCC_SUFFIX_RE.fullmatch(suffix)
        if (
            match is None
            or root_field != "GLD   "
            or self.underlying != "GLD"
            or self.right != "C"
            or match.group("right") != self.right
        ):
            raise NormalizationError("IDENTITY_UNRESOLVED")
        expiry_digits = match.group("expiry")
        try:
            occ_expiry_date = date(
                2000 + int(expiry_digits[0:2]),
                int(expiry_digits[2:4]),
                int(expiry_digits[4:6]),
            )
        except ValueError as error:
            raise NormalizationError("IDENTITY_UNRESOLVED") from error
        strike = _require_exact_int(
            self.strike_nano_usd,
            reason_code="IDENTITY_UNRESOLVED",
            minimum=1,
            maximum=MAX_DEFINED_I64,
        )
        embedded_strike = int(match.group("strike_milli_usd")) * 1_000_000
        if strike != embedded_strike:
            raise NormalizationError("IDENTITY_UNRESOLVED")
        expiry = _require_exact_int(
            self.expiry_utc_ns,
            reason_code="IDENTITY_UNRESOLVED",
            minimum=1,
            maximum=MAX_DEFINED_U64,
        )
        last_trading = _require_exact_int(
            self.last_trading_utc_ns,
            reason_code="IDENTITY_UNRESOLVED",
            minimum=1,
            maximum=MAX_DEFINED_U64,
        )
        activation = _require_exact_int(
            self.activation_utc_ns,
            reason_code="IDENTITY_UNRESOLVED",
            minimum=1,
            maximum=MAX_DEFINED_U64,
        )
        if not activation < last_trading < expiry:
            raise NormalizationError("IDENTITY_UNRESOLVED")
        try:
            canonical_expiry_date = (
                _UTC_EPOCH + timedelta(seconds=expiry // 1_000_000_000)
            ).astimezone(_NEW_YORK).date()
        except (OverflowError, ValueError) as error:
            raise NormalizationError("IDENTITY_UNRESOLVED") from error
        if canonical_expiry_date != occ_expiry_date:
            raise NormalizationError("IDENTITY_UNRESOLVED")
        if type(self.multiplier) is not int or self.multiplier != 100:
            raise NormalizationError("IDENTITY_UNRESOLVED")
        if self.deliverable != "100 GLD" or self.currency != "USD":
            raise NormalizationError("IDENTITY_UNRESOLVED")
        if type(self.standard_unadjusted) is not bool or not self.standard_unadjusted:
            raise NormalizationError("IDENTITY_UNRESOLVED")
        if type(self.exercise_style) is not str or self.exercise_style != "AMERICAN":
            raise NormalizationError("IDENTITY_UNRESOLVED")


@dataclass(frozen=True, slots=True)
class TopOfBookV1:
    bid_nano_usd: int
    ask_nano_usd: int
    bid_size: int
    ask_size: int
    tick_nano_usd: int
    ts_event_ns: int
    ts_recv_ns: int
    flags: tuple[str, ...]

    def __post_init__(self) -> None:
        bid = _require_exact_int(
            self.bid_nano_usd,
            reason_code="QUOTE_MISSING",
            minimum=0,
            maximum=MAX_DEFINED_I64,
        )
        if bid == 0:
            raise NormalizationError("ZERO_BID")
        ask = _require_exact_int(
            self.ask_nano_usd,
            reason_code="QUOTE_MISSING",
            minimum=1,
            maximum=MAX_DEFINED_I64,
        )
        if bid >= ask:
            raise NormalizationError("QUOTE_CROSSED")
        _require_exact_int(
            self.bid_size,
            reason_code="QUOTE_MISSING",
            minimum=1,
            maximum=MAX_DEFINED_U32,
        )
        _require_exact_int(
            self.ask_size,
            reason_code="QUOTE_MISSING",
            minimum=1,
            maximum=MAX_DEFINED_U32,
        )
        _require_exact_int(
            self.tick_nano_usd,
            reason_code="QUOTE_MISSING",
            minimum=1,
            maximum=MAX_DEFINED_I64,
        )
        event = _require_exact_int(
            self.ts_event_ns,
            reason_code="QUOTE_MISSING",
            minimum=1,
            maximum=MAX_DEFINED_U64,
        )
        received = _require_exact_int(
            self.ts_recv_ns,
            reason_code="QUOTE_MISSING",
            minimum=1,
            maximum=MAX_DEFINED_U64,
        )
        if received < event:
            raise NormalizationError("QUOTE_MISSING")
        if type(self.flags) is not tuple or len(self.flags) > MAX_BOOK_FLAGS:
            raise NormalizationError("QUOTE_MISSING")
        if (
            any(
                type(flag) is not str or _FLAG_RE.fullmatch(flag) is None
                for flag in self.flags
            )
            or tuple(sorted(set(self.flags))) != self.flags
        ):
            raise NormalizationError("QUOTE_MISSING")


@dataclass(frozen=True, slots=True)
class OptionQuoteV1:
    contract: OptionContractV1
    top_of_book: TopOfBookV1

    def __post_init__(self) -> None:
        if not isinstance(self.contract, OptionContractV1):
            raise NormalizationError("IDENTITY_UNRESOLVED")
        if not isinstance(self.top_of_book, TopOfBookV1):
            raise NormalizationError("QUOTE_MISSING")


@dataclass(frozen=True, slots=True)
class OptionQuoteSnapshotV1:
    """Structurally bound quote content; never proof of source authority."""

    signal_snapshot_sha256: str
    candidate_ordinal: int
    candidate_provenance_sha256: str
    window_start_utc_ns: int
    window_end_utc_ns: int
    capture_utc_ns: int
    underlying_top: TopOfBookV1
    option_quotes: tuple[OptionQuoteV1, ...]
    source_id: str
    source_version: str
    market_data_type: str
    source_receipt_sha256: str

    def __post_init__(self) -> None:
        _require_sha256(
            self.signal_snapshot_sha256,
            reason_code="OPTION_SNAPSHOT_BINDING_MISMATCH",
        )
        _require_exact_int(
            self.candidate_ordinal,
            reason_code="OPTION_CANDIDATE_PROVENANCE_INVALID",
            minimum=1,
            maximum=MAX_DEFINED_U32,
        )
        _require_sha256(
            self.candidate_provenance_sha256,
            reason_code="OPTION_CANDIDATE_PROVENANCE_INVALID",
        )
        start = _require_exact_int(
            self.window_start_utc_ns,
            reason_code="OPTION_SNAPSHOT_WINDOW_INVALID",
            minimum=1,
            maximum=MAX_DEFINED_U64,
        )
        end = _require_exact_int(
            self.window_end_utc_ns,
            reason_code="OPTION_SNAPSHOT_WINDOW_INVALID",
            minimum=1,
            maximum=MAX_DEFINED_U64,
        )
        capture = _require_exact_int(
            self.capture_utc_ns,
            reason_code="OPTION_SNAPSHOT_WINDOW_INVALID",
            minimum=1,
            maximum=MAX_DEFINED_U64,
        )
        if end - start != OPTION_CAPTURE_WINDOW_NS or not start <= capture < end:
            raise NormalizationError("OPTION_SNAPSHOT_WINDOW_INVALID")
        if not isinstance(self.underlying_top, TopOfBookV1):
            raise NormalizationError("QUOTE_MISSING")
        if type(self.option_quotes) is not tuple or not self.option_quotes:
            raise NormalizationError("QUOTE_MISSING")
        if len(self.option_quotes) > MAX_OPTION_QUOTES:
            raise NormalizationError("QUOTE_MISSING")
        economic_identities: set[tuple[object, ...]] = set()
        for quote in self.option_quotes:
            if not isinstance(quote, OptionQuoteV1):
                raise NormalizationError("QUOTE_MISSING")
            contract = quote.contract
            economic_identity = (
                contract.underlying,
                contract.right,
                contract.strike_nano_usd,
                contract.occ_symbol[6:12],
                contract.multiplier,
                contract.deliverable,
                contract.currency,
                contract.standard_unadjusted,
                contract.exercise_style,
            )
            if (
                economic_identity in economic_identities
                or not contract.activation_utc_ns <= capture < contract.last_trading_utc_ns
            ):
                raise NormalizationError("IDENTITY_UNRESOLVED")
            economic_identities.add(economic_identity)
        for book in chain(
            (self.underlying_top,),
            (quote.top_of_book for quote in self.option_quotes),
        ):
            if book.ts_event_ns < start or book.ts_recv_ns < start:
                raise NormalizationError("OPTION_QUOTE_BEFORE_WINDOW")
        _require_text(
            self.source_id,
            reason_code="OPTION_SNAPSHOT_BINDING_MISMATCH",
        )
        _require_text(
            self.source_version,
            reason_code="OPTION_SNAPSHOT_BINDING_MISMATCH",
        )
        _require_text(
            self.market_data_type,
            reason_code="OPTION_SNAPSHOT_BINDING_MISMATCH",
        )
        _require_sha256(
            self.source_receipt_sha256,
            reason_code="OPTION_SNAPSHOT_BINDING_MISMATCH",
        )

    @property
    def snapshot_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class OptionSnapshotCandidateLedgerV1:
    """Complete ordered candidate evidence, without provider qualification."""

    signal_snapshot_sha256: str
    window_start_utc_ns: int
    window_end_utc_ns: int
    quote_quality_policy_version: str
    quote_quality_policy_sha256: str
    candidates: tuple[OptionQuoteSnapshotV1, ...]

    def __post_init__(self) -> None:
        _require_sha256(
            self.signal_snapshot_sha256,
            reason_code="OPTION_SNAPSHOT_BINDING_MISMATCH",
        )
        start = _require_exact_int(
            self.window_start_utc_ns,
            reason_code="OPTION_CANDIDATE_LEDGER_INVALID",
            minimum=1,
            maximum=MAX_DEFINED_U64,
        )
        end = _require_exact_int(
            self.window_end_utc_ns,
            reason_code="OPTION_CANDIDATE_LEDGER_INVALID",
            minimum=1,
            maximum=MAX_DEFINED_U64,
        )
        if end - start != OPTION_CAPTURE_WINDOW_NS:
            raise NormalizationError("OPTION_CANDIDATE_LEDGER_INVALID")
        if (
            self.quote_quality_policy_version != QUOTE_QUALITY_POLICY_VERSION
            or self.quote_quality_policy_sha256 != QUOTE_QUALITY_POLICY_SHA256
        ):
            raise NormalizationError("QUOTE_QUALITY_POLICY_MISMATCH")
        if (
            type(self.candidates) is not tuple
            or not self.candidates
            or len(self.candidates) > MAX_OPTION_SNAPSHOT_CANDIDATES
            or any(
                type(candidate) is not OptionQuoteSnapshotV1
                for candidate in self.candidates
            )
        ):
            raise NormalizationError("OPTION_CANDIDATE_LEDGER_INVALID")

        provenance: set[str] = set()
        previous_capture_utc_ns = 0
        for expected_ordinal, candidate in enumerate(self.candidates, start=1):
            if (
                candidate.signal_snapshot_sha256 != self.signal_snapshot_sha256
                or candidate.window_start_utc_ns != start
                or candidate.window_end_utc_ns != end
            ):
                raise NormalizationError("OPTION_SNAPSHOT_BINDING_MISMATCH")
            if (
                candidate.candidate_ordinal != expected_ordinal
                or candidate.candidate_provenance_sha256 in provenance
                or candidate.capture_utc_ns < previous_capture_utc_ns
            ):
                raise NormalizationError("OPTION_CANDIDATE_PROVENANCE_INVALID")
            provenance.add(candidate.candidate_provenance_sha256)
            previous_capture_utc_ns = candidate.capture_utc_ns

    @property
    def ledger_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


def build_option_snapshot_candidate_ledger(
    candidates: object,
    *,
    signal_snapshot: SignalSnapshotV1,
) -> OptionSnapshotCandidateLedgerV1:
    """Normalize one bounded candidate tuple into canonical ordinal order."""

    if type(signal_snapshot) is not SignalSnapshotV1:
        raise NormalizationError("OPTION_SNAPSHOT_BINDING_MISMATCH")
    if (
        type(candidates) is not tuple
        or not candidates
        or len(candidates) > MAX_OPTION_SNAPSHOT_CANDIDATES
        or any(type(candidate) is not OptionQuoteSnapshotV1 for candidate in candidates)
    ):
        raise NormalizationError("OPTION_CANDIDATE_LEDGER_INVALID")
    ordered_candidates = tuple(
        sorted(candidates, key=lambda candidate: candidate.candidate_ordinal)
    )
    return OptionSnapshotCandidateLedgerV1(
        signal_snapshot_sha256=signal_snapshot.snapshot_sha256,
        window_start_utc_ns=signal_snapshot.cutoff_utc_ns,
        window_end_utc_ns=(
            signal_snapshot.cutoff_utc_ns + OPTION_CAPTURE_WINDOW_NS
        ),
        quote_quality_policy_version=QUOTE_QUALITY_POLICY_VERSION,
        quote_quality_policy_sha256=QUOTE_QUALITY_POLICY_SHA256,
        candidates=ordered_candidates,
    )


def validate_option_snapshot_quality(
    snapshot: OptionQuoteSnapshotV1,
    *,
    quote_age_limit_ns: int = QUOTE_AGE_LIMIT_NS,
    receive_skew_limit_ns: int = RECEIVE_SKEW_LIMIT_NS,
) -> None:
    """Validate quote quality relative to one snapshot's own capture time."""

    if not isinstance(snapshot, OptionQuoteSnapshotV1):
        raise NormalizationError("EXECUTABLE_OPTION_SNAPSHOT_UNAVAILABLE")
    _require_frozen_quote_quality_policy(
        quote_age_limit_ns=quote_age_limit_ns,
        receive_skew_limit_ns=receive_skew_limit_ns,
    )
    if not (
        snapshot.window_start_utc_ns
        <= snapshot.capture_utc_ns
        < snapshot.window_end_utc_ns
    ):
        raise NormalizationError("OPTION_SNAPSHOT_WINDOW_INVALID")

    minimum_receive = MAX_DEFINED_U64
    maximum_receive = 0
    for book in chain(
        (snapshot.underlying_top,),
        (quote.top_of_book for quote in snapshot.option_quotes),
    ):
        if "HALTED" in book.flags:
            raise NormalizationError("MARKET_HALTED")
        if (
            book.ts_recv_ns > snapshot.capture_utc_ns
            or snapshot.capture_utc_ns - book.ts_recv_ns > QUOTE_AGE_LIMIT_NS
        ):
            raise NormalizationError("QUOTE_STALE")
        minimum_receive = min(minimum_receive, book.ts_recv_ns)
        maximum_receive = max(maximum_receive, book.ts_recv_ns)
    if maximum_receive - minimum_receive > RECEIVE_SKEW_LIMIT_NS:
        raise NormalizationError("RECEIVE_SKEW_EXCEEDED")


def select_first_complete_option_snapshot(
    candidates: object,
    *,
    signal_snapshot: SignalSnapshotV1,
    quote_age_limit_ns: int = QUOTE_AGE_LIMIT_NS,
    receive_skew_limit_ns: int = RECEIVE_SKEW_LIMIT_NS,
) -> OptionQuoteSnapshotV1:
    """Select the earliest data-quality-complete snapshot, never the best price."""

    if not isinstance(signal_snapshot, SignalSnapshotV1):
        raise NormalizationError("OPTION_SNAPSHOT_BINDING_MISMATCH")
    _require_frozen_quote_quality_policy(
        quote_age_limit_ns=quote_age_limit_ns,
        receive_skew_limit_ns=receive_skew_limit_ns,
    )
    candidate_ledger = (
        candidates
        if type(candidates) is OptionSnapshotCandidateLedgerV1
        else build_option_snapshot_candidate_ledger(
            candidates,
            signal_snapshot=signal_snapshot,
        )
    )
    signal_snapshot_sha256 = signal_snapshot.snapshot_sha256
    if (
        candidate_ledger.signal_snapshot_sha256 != signal_snapshot_sha256
        or candidate_ledger.window_start_utc_ns != signal_snapshot.cutoff_utc_ns
        or candidate_ledger.window_end_utc_ns
        != signal_snapshot.cutoff_utc_ns + OPTION_CAPTURE_WINDOW_NS
    ):
        raise NormalizationError("OPTION_SNAPSHOT_BINDING_MISMATCH")

    for candidate in candidate_ledger.candidates:
        try:
            validate_option_snapshot_quality(
                candidate,
            )
        except NormalizationError as error:
            if error.reason_code not in {
                "OPTION_SNAPSHOT_WINDOW_INVALID",
                "QUOTE_STALE",
                "RECEIVE_SKEW_EXCEEDED",
                "MARKET_HALTED",
            }:
                raise
            continue
        return candidate
    raise NormalizationError("EXECUTABLE_OPTION_SNAPSHOT_UNAVAILABLE")


__all__ = [
    "MAX_OPTION_SNAPSHOT_CANDIDATES",
    "QUOTE_AGE_LIMIT_NS",
    "QUOTE_QUALITY_POLICY_SHA256",
    "QUOTE_QUALITY_POLICY_VERSION",
    "REAL_OPTION_FACT_EXECUTION_STATUS",
    "RECEIVE_SKEW_LIMIT_NS",
    "OptionContractV1",
    "OptionQuoteSnapshotV1",
    "OptionQuoteV1",
    "OptionSnapshotCandidateLedgerV1",
    "SignalSnapshotV1",
    "TopOfBookV1",
    "build_option_snapshot_candidate_ledger",
    "canonical_snapshot_sha256",
    "select_first_complete_option_snapshot",
    "validate_option_snapshot_quality",
]
