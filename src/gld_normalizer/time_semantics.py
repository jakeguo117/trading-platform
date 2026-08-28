"""Non-authoritative time semantics and calendar hash-binding facts.

Only opening-transition classification is operational.  Every API that would
grant calendar or market-phase authority fails closed until a real external
trust anchor is configured outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from enum import Enum
import hashlib
import json
from string import hexdigits
from typing import Final, NoReturn
from zoneinfo import ZoneInfo

from gld_normalizer.errors import NormalizationError


NEW_YORK: Final = ZoneInfo("America/New_York")
UTC_EPOCH: Final = datetime(1970, 1, 1, tzinfo=timezone.utc)
NANOSECONDS_PER_MINUTE: Final = 60_000_000_000
MAX_VALID_UTC_NANOSECONDS: Final = 2**64 - 2
MAX_CALENDAR_SESSIONS: Final = 10_000
MAX_CONTROL_TEXT_CHARS: Final = 256
MAX_RECEIPT_PROOF_BYTES: Final = 4_096


class MarketPhase(str, Enum):
    PREMARKET = "PREMARKET"
    CORE_OPEN_AUCTION = "CORE_OPEN_AUCTION"
    OPENING_TRANSITION_UNCLASSIFIED = "OPENING_TRANSITION_UNCLASSIFIED"
    CORE_CONTINUOUS = "CORE_CONTINUOUS"
    HALT_OR_REOPEN_AUCTION = "HALT_OR_REOPEN_AUCTION"
    POSTMARKET = "POSTMARKET"
    UNKNOWN = "UNKNOWN"


class CalendarTerminalState(str, Enum):
    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class CalendarSession:
    session_date: date
    is_half_day: bool


@dataclass(frozen=True, slots=True)
class CalendarPayload:
    source_sha256: str
    version: str
    sessions: tuple[CalendarSession, ...]
    sessions_sha256: str


@dataclass(frozen=True, slots=True)
class CalendarQualificationReceipt:
    """Unverified receipt content; this type conveys no authority."""

    authority_id: str
    verifier_version: str
    payload_sha256: str
    source_sha256: str
    sessions_sha256: str
    calendar_version: str
    terminal_state: CalendarTerminalState
    issued_at_utc_ns: int
    proof: bytes


@dataclass(frozen=True, slots=True)
class CalendarHashBindingValidation:
    """Structural hash agreement that explicitly does not grant authority."""

    sessions_sha256: str
    payload_sha256: str

    @property
    def authority_qualified(self) -> bool:
        return False


def _require_nonempty_text(value: object, reason_code: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_CONTROL_TEXT_CHARS
    ):
        raise NormalizationError(
            reason_code,
            (
                f"{field} must be non-empty and no longer than "
                f"{MAX_CONTROL_TEXT_CHARS} characters"
            ),
        )
    return value


def _require_sha256(value: object, reason_code: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in hexdigits for character in value)
    ):
        raise NormalizationError(
            reason_code,
            f"{field} must be 64 hexadecimal characters",
        )
    return value.lower()


def _require_utc_nanoseconds(value: object, field_name: str) -> int:
    if (
        type(value) is not int
        or value < 1
        or value > MAX_VALID_UTC_NANOSECONDS
    ):
        raise NormalizationError(
            "INVALID_UTC_NANOSECONDS",
            (
                f"{field_name} must be an exact int in the inclusive range "
                f"1..{MAX_VALID_UTC_NANOSECONDS}"
            ),
        )
    return value


def _regular_open_utc_ns(regular_open: object) -> int:
    if not isinstance(regular_open, datetime):
        raise NormalizationError(
            "INVALID_REGULAR_OPEN_TYPE",
            "regular_open must be a datetime",
        )
    if regular_open.tzinfo is None or regular_open.utcoffset() is None:
        raise NormalizationError(
            "TIMEZONE_NAIVE",
            "regular_open must be timezone-aware",
        )
    timezone_key = getattr(regular_open.tzinfo, "key", None)
    if timezone_key != NEW_YORK.key:
        raise NormalizationError(
            "UNSUPPORTED_TIMEZONE",
            "regular_open must use America/New_York",
        )
    local_open = regular_open.astimezone(NEW_YORK)
    if local_open.timetz().replace(tzinfo=None) != time(9, 30):
        raise NormalizationError(
            "INVALID_REGULAR_OPEN",
            "regular_open must be 09:30:00 America/New_York",
        )

    delta = local_open.astimezone(timezone.utc) - UTC_EPOCH
    total_microseconds = (
        (delta.days * 86_400 + delta.seconds) * 1_000_000
        + delta.microseconds
    )
    return _require_utc_nanoseconds(
        total_microseconds * 1_000,
        "regular_open_utc_ns",
    )


def _validated_calendar_sessions(
    value: object,
) -> tuple[CalendarSession, ...]:
    if type(value) is not tuple:
        raise NormalizationError(
            "INVALID_CALENDAR_SESSIONS",
            "calendar sessions must be an immutable tuple",
        )
    sessions = value
    if len(sessions) > MAX_CALENDAR_SESSIONS:
        raise NormalizationError(
            "CALENDAR_SESSION_CAP_EXCEEDED",
            (
                "calendar sessions must not exceed "
                f"{MAX_CALENDAR_SESSIONS} rows"
            ),
        )
    seen: set[date] = set()
    previous: date | None = None
    for index, row in enumerate(sessions):
        if not isinstance(row, CalendarSession):
            raise NormalizationError(
                "INVALID_CALENDAR_ROW",
                f"calendar row {index} must be a CalendarSession",
            )
        current = row.session_date
        if isinstance(current, datetime) or not isinstance(current, date):
            raise NormalizationError(
                "INVALID_SESSION_DATE",
                f"calendar row {index} must contain a date",
            )
        if type(row.is_half_day) is not bool:
            raise NormalizationError(
                "INVALID_CALENDAR_ROW",
                f"calendar row {index} is_half_day must be boolean",
            )
        if current in seen:
            raise NormalizationError(
                "DUPLICATE_SESSION_DATE",
                f"duplicate calendar session: {current.isoformat()}",
            )
        if previous is not None and current < previous:
            raise NormalizationError(
                "UNSORTED_SESSION_DATES",
                "calendar sessions must be strictly increasing",
            )
        seen.add(current)
        previous = current
    return sessions


def canonical_calendar_sessions_sha256(sessions: object) -> str:
    validated = _validated_calendar_sessions(sessions)
    canonical_rows = [
        {
            "is_half_day": row.is_half_day,
            "session_date": row.session_date.isoformat(),
        }
        for row in validated
    ]
    encoded = json.dumps(
        canonical_rows,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def canonical_calendar_payload_sha256(payload: object) -> str:
    if not isinstance(payload, CalendarPayload):
        raise NormalizationError(
            "INVALID_CALENDAR_PAYLOAD",
            "payload must be a CalendarPayload",
        )
    source_sha256 = _require_sha256(
        payload.source_sha256,
        "INVALID_CALENDAR_SOURCE_SHA256",
        "calendar source_sha256",
    )
    version = _require_nonempty_text(
        payload.version,
        "MISSING_CALENDAR_VERSION",
        "calendar version",
    )
    sessions_sha256 = _require_sha256(
        payload.sessions_sha256,
        "INVALID_CALENDAR_SESSIONS_SHA256",
        "calendar sessions_sha256",
    )
    encoded = json.dumps(
        {
            "sessions_sha256": sessions_sha256,
            "source_sha256": source_sha256,
            "version": version,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _validate_calendar_receipt_structure(
    receipt: object,
) -> CalendarQualificationReceipt:
    if not isinstance(receipt, CalendarQualificationReceipt):
        raise NormalizationError(
            "CALENDAR_QUALIFICATION_RECEIPT_REQUIRED",
            "receipt must be a CalendarQualificationReceipt",
        )
    if not isinstance(receipt.terminal_state, CalendarTerminalState):
        raise NormalizationError(
            "INVALID_CALENDAR_RECEIPT",
            "calendar terminal state must be a controlled enum",
        )
    _require_nonempty_text(
        receipt.authority_id,
        "INVALID_CALENDAR_RECEIPT",
        "calendar authority_id",
    )
    _require_nonempty_text(
        receipt.verifier_version,
        "INVALID_CALENDAR_RECEIPT",
        "calendar verifier_version",
    )
    _require_sha256(
        receipt.payload_sha256,
        "INVALID_CALENDAR_RECEIPT",
        "calendar receipt payload_sha256",
    )
    _require_sha256(
        receipt.source_sha256,
        "INVALID_CALENDAR_RECEIPT",
        "calendar receipt source_sha256",
    )
    _require_sha256(
        receipt.sessions_sha256,
        "INVALID_CALENDAR_RECEIPT",
        "calendar receipt sessions_sha256",
    )
    _require_nonempty_text(
        receipt.calendar_version,
        "INVALID_CALENDAR_RECEIPT",
        "calendar receipt version",
    )
    _require_utc_nanoseconds(receipt.issued_at_utc_ns, "issued_at_utc_ns")
    if (
        not isinstance(receipt.proof, bytes)
        or not receipt.proof
        or len(receipt.proof) > MAX_RECEIPT_PROOF_BYTES
    ):
        raise NormalizationError(
            "INVALID_CALENDAR_RECEIPT",
            (
                "calendar receipt proof must be non-empty bytes no longer "
                f"than {MAX_RECEIPT_PROOF_BYTES} bytes"
            ),
        )
    return receipt


def validate_calendar_receipt_hash_binding(
    payload: CalendarPayload,
    receipt: CalendarQualificationReceipt,
) -> CalendarHashBindingValidation:
    """Validate structural hash agreement without granting authority."""

    if not isinstance(payload, CalendarPayload):
        raise NormalizationError(
            "INVALID_CALENDAR_PAYLOAD",
            "payload must be a CalendarPayload",
        )
    sessions = _validated_calendar_sessions(payload.sessions)
    computed_sessions_sha256 = canonical_calendar_sessions_sha256(sessions)
    declared_sessions_sha256 = _require_sha256(
        payload.sessions_sha256,
        "INVALID_CALENDAR_SESSIONS_SHA256",
        "calendar sessions_sha256",
    )
    if computed_sessions_sha256 != declared_sessions_sha256:
        raise NormalizationError(
            "CALENDAR_SESSIONS_HASH_MISMATCH",
            "canonical sessions hash does not match payload",
        )
    payload_sha256 = canonical_calendar_payload_sha256(payload)
    structured_receipt = _validate_calendar_receipt_structure(receipt)
    if (
        structured_receipt.payload_sha256.lower() != payload_sha256
        or structured_receipt.source_sha256.lower()
        != payload.source_sha256.lower()
        or structured_receipt.sessions_sha256.lower()
        != computed_sessions_sha256
        or structured_receipt.calendar_version != payload.version
    ):
        raise NormalizationError(
            "CALENDAR_RECEIPT_BINDING_MISMATCH",
            "calendar receipt does not bind the supplied payload",
        )
    return CalendarHashBindingValidation(
        sessions_sha256=computed_sessions_sha256,
        payload_sha256=payload_sha256,
    )


def classify_opening_transition(
    event_utc_ns: int,
    regular_open: datetime,
) -> MarketPhase | None:
    """Classify only the half-open local interval ``[09:30, 09:31)``."""

    event_ns = _require_utc_nanoseconds(event_utc_ns, "event_utc_ns")
    opening_start_ns = _regular_open_utc_ns(regular_open)
    opening_end_ns = opening_start_ns + NANOSECONDS_PER_MINUTE
    if opening_start_ns <= event_ns < opening_end_ns:
        return MarketPhase.OPENING_TRANSITION_UNCLASSIFIED
    return None


def qualify_calendar(
    payload: CalendarPayload,
    receipt: CalendarQualificationReceipt,
) -> NoReturn:
    raise NormalizationError(
        "CALENDAR_AUTHORITY_UNAVAILABLE",
        "no external calendar trust anchor is configured",
    )


def map_session_horizon(h0: date, calendar: object) -> NoReturn:
    raise NormalizationError(
        "CALENDAR_AUTHORITY_UNAVAILABLE",
        "session horizons require an externally qualified calendar",
    )


def qualify_market_phase(receipt: object) -> NoReturn:
    raise NormalizationError(
        "PHASE_AUTHORITY_UNAVAILABLE",
        "no external market-phase trust anchor is configured",
    )


def fast_confirmation_eligible(
    event_utc_ns: int,
    regular_open: datetime,
    qualified_phase: object,
) -> NoReturn:
    raise NormalizationError(
        "PHASE_AUTHORITY_UNAVAILABLE",
        "fast confirmation requires an externally qualified market phase",
    )


__all__ = [
    "CalendarHashBindingValidation",
    "CalendarPayload",
    "CalendarQualificationReceipt",
    "CalendarSession",
    "CalendarTerminalState",
    "MAX_CALENDAR_SESSIONS",
    "MAX_CONTROL_TEXT_CHARS",
    "MAX_RECEIPT_PROOF_BYTES",
    "MarketPhase",
    "canonical_calendar_payload_sha256",
    "canonical_calendar_sessions_sha256",
    "classify_opening_transition",
    "fast_confirmation_eligible",
    "map_session_horizon",
    "qualify_calendar",
    "qualify_market_phase",
    "validate_calendar_receipt_hash_binding",
]
