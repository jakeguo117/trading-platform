"""Pinned XNYS calendar semantics for deterministic Research F0 fixtures.

This is a deliberately bounded, provider-neutral *research* calendar.  It
models the regular NYSE equity holiday and early-close rules needed by the F0
synthetic fixtures, plus explicitly pinned exceptional closures known within
the supported range.  It is not evidence that a market-data provider, broker,
or exchange calendar has been qualified, and it never infers unlisted ad-hoc
closures from the current clock or external state.

The implementation uses only integer/date arithmetic.  In particular, UTC
nanoseconds do not depend on the host timezone database, floating-point Unix
timestamps, the current clock, or input order.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from hashlib import sha256
from types import MappingProxyType
from typing import Iterable, Mapping

from .errors import ManagementResearchError


CALENDAR_ID = "XNYS_PINNED_RESEARCH_F0_V1"
CALENDAR_AUTHORITY_STATUS = "RESEARCH_ONLY_NOT_PROVIDER_QUALIFIED"
CALENDAR_RULE_VERSION = "XNYS_REGULAR_SESSIONS_2020_2028_F0_V1"
SUPPORTED_START_DATE = date(2020, 1, 1)
SUPPORTED_END_DATE = date(2028, 12, 31)

SESSION_NORMAL = "NORMAL"
SESSION_EARLY_CLOSE = "EARLY_CLOSE"

_SECOND_NS = 1_000_000_000
_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()
_ONE_DAY = timedelta(days=1)

# Exceptional closures must be added explicitly, named, versioned, and thereby
# bound into CALENDAR_CONTENT_SHA256.  This table is deliberately not a claim
# that future exceptional closures are known.
_PINNED_EXCEPTIONAL_CLOSURES: Mapping[date, str] = MappingProxyType(
    {
        date(2025, 1, 9): "NATIONAL_DAY_OF_MOURNING_JIMMY_CARTER",
    }
)


def _coerce_date(value: date | str) -> date:
    if type(value) is date:
        parsed = value
    elif type(value) is str and value.isascii():
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ManagementResearchError("XNYS_CALENDAR_DATE_INVALID") from exc
        if parsed.isoformat() != value:
            raise ManagementResearchError("XNYS_CALENDAR_DATE_INVALID")
    else:
        raise ManagementResearchError("XNYS_CALENDAR_DATE_INVALID")
    if not SUPPORTED_START_DATE <= parsed <= SUPPORTED_END_DATE:
        raise ManagementResearchError("XNYS_CALENDAR_DATE_OUT_OF_RANGE")
    return parsed


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        following_month = date(year + 1, 1, 1)
    else:
        following_month = date(year, month + 1, 1)
    candidate = following_month - _ONE_DAY
    return candidate - timedelta(days=(candidate.weekday() - weekday) % 7)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    actual = date(year, month, day)
    if actual.weekday() == 5:
        return actual - _ONE_DAY
    if actual.weekday() == 6:
        return actual + _ONE_DAY
    return actual


def _new_year_closure(year: int) -> date | None:
    """Return NYSE New Year's closure; Saturday has no Friday substitute."""

    actual = date(year, 1, 1)
    if actual.weekday() == 5:
        return None
    if actual.weekday() == 6:
        return actual + _ONE_DAY
    return actual


def _gregorian_easter(year: int) -> date:
    """Anonymous Gregorian computus, used only to derive Good Friday."""

    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = (h + ell - 7 * m + 114) % 31 + 1
    return date(year, month, day)


@lru_cache(maxsize=None)
def _regular_holidays(year: int) -> Mapping[date, str]:
    holidays: dict[date, str] = {}

    new_year = _new_year_closure(year)
    if new_year is not None:
        holidays[new_year] = "NEW_YEARS_DAY"

    holidays[_nth_weekday(year, 1, 0, 3)] = "MARTIN_LUTHER_KING_JR_DAY"
    holidays[_nth_weekday(year, 2, 0, 3)] = "WASHINGTONS_BIRTHDAY"
    holidays[_gregorian_easter(year) - timedelta(days=2)] = "GOOD_FRIDAY"
    holidays[_last_weekday(year, 5, 0)] = "MEMORIAL_DAY"

    if year >= 2022:
        holidays[
            _observed_fixed_holiday(year, 6, 19)
        ] = "JUNETEENTH_NATIONAL_INDEPENDENCE_DAY"

    holidays[
        _observed_fixed_holiday(year, 7, 4)
    ] = "INDEPENDENCE_DAY"
    holidays[_nth_weekday(year, 9, 0, 1)] = "LABOR_DAY"
    holidays[_nth_weekday(year, 11, 3, 4)] = "THANKSGIVING_DAY"
    holidays[
        _observed_fixed_holiday(year, 12, 25)
    ] = "CHRISTMAS_DAY"
    return MappingProxyType(holidays)


def holiday_name(value: date | str) -> str | None:
    """Return a pinned closure name, or ``None`` for a non-holiday date."""

    session_date = _coerce_date(value)
    exceptional = _PINNED_EXCEPTIONAL_CLOSURES.get(session_date)
    if exceptional is not None:
        return exceptional
    return _regular_holidays(session_date.year).get(session_date)


def is_session_date(value: date | str) -> bool:
    """Return whether ``value`` is an in-range regular XNYS session date."""

    session_date = _coerce_date(value)
    return session_date.weekday() < 5 and holiday_name(session_date) is None


def _require_session_date(value: date | str) -> date:
    session_date = _coerce_date(value)
    if not is_session_date(session_date):
        raise ManagementResearchError("XNYS_CALENDAR_NOT_A_SESSION")
    return session_date


def next_session_date(value: date | str) -> date:
    """Return the first pinned session strictly after ``value``."""

    candidate = _coerce_date(value) + _ONE_DAY
    while candidate <= SUPPORTED_END_DATE:
        if is_session_date(candidate):
            return candidate
        candidate += _ONE_DAY
    raise ManagementResearchError("XNYS_CALENDAR_NEXT_SESSION_OUT_OF_RANGE")


def previous_session_date(value: date | str) -> date:
    """Return the first pinned session strictly before ``value``."""

    candidate = _coerce_date(value) - _ONE_DAY
    while candidate >= SUPPORTED_START_DATE:
        if is_session_date(candidate):
            return candidate
        candidate -= _ONE_DAY
    raise ManagementResearchError("XNYS_CALENDAR_PREVIOUS_SESSION_OUT_OF_RANGE")


def validate_previous_session(
    previous_value: date | str,
    current_value: date | str,
) -> tuple[date, date]:
    """Validate an ordered pair of adjacent pinned sessions."""

    previous = _require_session_date(previous_value)
    current = _require_session_date(current_value)
    if next_session_date(previous) != current:
        raise ManagementResearchError("XNYS_CALENDAR_PREVIOUS_SESSION_INVALID")
    return previous, current


def validate_session_sequence(
    values: Iterable[date | str],
) -> tuple[date, ...]:
    """Validate a non-empty, ordered, gap-free sequence of pinned sessions."""

    if isinstance(values, (str, bytes)):
        raise ManagementResearchError("XNYS_CALENDAR_SESSION_SEQUENCE_INVALID")
    try:
        parsed = tuple(_require_session_date(value) for value in values)
    except TypeError as exc:
        raise ManagementResearchError(
            "XNYS_CALENDAR_SESSION_SEQUENCE_INVALID"
        ) from exc
    if not parsed:
        raise ManagementResearchError("XNYS_CALENDAR_SESSION_SEQUENCE_EMPTY")
    if len(set(parsed)) != len(parsed):
        raise ManagementResearchError("XNYS_CALENDAR_SESSION_SEQUENCE_INVALID")
    for previous, current in zip(parsed, parsed[1:]):
        if next_session_date(previous) != current:
            raise ManagementResearchError(
                "XNYS_CALENDAR_SESSION_SEQUENCE_INVALID"
            )
    return parsed


def is_valid_session_sequence(values: Iterable[date | str]) -> bool:
    """Boolean convenience wrapper around :func:`validate_session_sequence`."""

    try:
        validate_session_sequence(values)
    except ManagementResearchError:
        return False
    return True


@lru_cache(maxsize=None)
def _early_close_dates(year: int) -> frozenset[date]:
    values: set[date] = set()

    # NYSE's Independence Day convention within the pinned schedule is July 3
    # when July 3 itself is a session.  No substitute early close is inferred
    # when July 3 is a weekend or the observed Independence Day holiday.
    july_third = date(year, 7, 3)
    if is_session_date(july_third):
        values.add(july_third)

    thanksgiving = _nth_weekday(year, 11, 3, 4)
    day_after_thanksgiving = thanksgiving + _ONE_DAY
    if is_session_date(day_after_thanksgiving):
        values.add(day_after_thanksgiving)

    christmas_eve = date(year, 12, 24)
    if is_session_date(christmas_eve):
        values.add(christmas_eve)
    return frozenset(values)


def session_kind(value: date | str) -> str:
    """Return ``NORMAL`` or ``EARLY_CLOSE`` for one pinned session."""

    session_date = _require_session_date(value)
    if session_date in _early_close_dates(session_date.year):
        return SESSION_EARLY_CLOSE
    return SESSION_NORMAL


def eastern_utc_offset_hours(value: date | str) -> int:
    """Return the pinned U.S. Eastern UTC offset (-5 or -4) for a date.

    F0's supported range uses the post-2007 U.S. daylight-saving rule.  Both
    10:45 and the official close are far from the transition hour, so a
    session-date test is sufficient and unambiguous.
    """

    session_date = _coerce_date(value)
    dst_start = _nth_weekday(session_date.year, 3, 6, 2)
    dst_end = _nth_weekday(session_date.year, 11, 6, 1)
    return -4 if dst_start <= session_date < dst_end else -5


def _local_wall_time_to_utc_ns(
    session_date: date,
    *,
    hour: int,
    minute: int,
) -> int:
    utc_hour = hour - eastern_utc_offset_hours(session_date)
    seconds = (
        (session_date.toordinal() - _EPOCH_ORDINAL) * 86_400
        + utc_hour * 3_600
        + minute * 60
    )
    return seconds * _SECOND_NS


def official_close_utc_ns(value: date | str) -> int:
    """Return 16:00 ET (normal) or 13:00 ET (early) as integer UTC ns."""

    session_date = _require_session_date(value)
    close_hour = 13 if session_kind(session_date) == SESSION_EARLY_CLOSE else 16
    return _local_wall_time_to_utc_ns(session_date, hour=close_hour, minute=0)


def action_1045_utc_ns(value: date | str) -> int:
    """Return the pinned session's 10:45 ET action clock as integer UTC ns."""

    session_date = _require_session_date(value)
    return _local_wall_time_to_utc_ns(session_date, hour=10, minute=45)


def _calendar_content_bytes() -> bytes:
    """Serialize every supported session and named weekday closure."""

    rows = [
        f"{CALENDAR_ID}|{CALENDAR_RULE_VERSION}|"
        f"{SUPPORTED_START_DATE.isoformat()}|{SUPPORTED_END_DATE.isoformat()}\n"
    ]
    candidate = SUPPORTED_START_DATE
    while candidate <= SUPPORTED_END_DATE:
        if is_session_date(candidate):
            rows.append(
                f"{candidate.isoformat()}|SESSION|{session_kind(candidate)}|"
                f"{official_close_utc_ns(candidate)}|"
                f"{action_1045_utc_ns(candidate)}\n"
            )
        elif candidate.weekday() < 5:
            rows.append(
                f"{candidate.isoformat()}|CLOSED|{holiday_name(candidate)}\n"
            )
        candidate += _ONE_DAY
    return "".join(rows).encode("ascii")


# This is a content hash of the complete bounded session/weekday-closure table,
# not merely a label.  Any change to a session, named closure, close, action
# clock, rule version, or range produces a different receipt automatically.
CALENDAR_CONTENT_SHA256 = sha256(_calendar_content_bytes()).hexdigest()
CALENDAR_VERSION_SHA256 = CALENDAR_CONTENT_SHA256


def calendar_receipt() -> dict[str, object]:
    """Return the closed, stable receipt bound to this research calendar."""

    return {
        "calendar_id": CALENDAR_ID,
        "calendar_rule_version": CALENDAR_RULE_VERSION,
        "calendar_content_sha256": CALENDAR_CONTENT_SHA256,
        "authority_status": CALENDAR_AUTHORITY_STATUS,
        "supported_start_date": SUPPORTED_START_DATE.isoformat(),
        "supported_end_date": SUPPORTED_END_DATE.isoformat(),
        "timezone": "America/New_York",
        "scope": "REGULAR_RULES_PLUS_PINNED_EXCEPTIONAL_CLOSURES",
    }


__all__ = [
    "CALENDAR_AUTHORITY_STATUS",
    "CALENDAR_CONTENT_SHA256",
    "CALENDAR_ID",
    "CALENDAR_RULE_VERSION",
    "CALENDAR_VERSION_SHA256",
    "SESSION_EARLY_CLOSE",
    "SESSION_NORMAL",
    "SUPPORTED_END_DATE",
    "SUPPORTED_START_DATE",
    "action_1045_utc_ns",
    "calendar_receipt",
    "eastern_utc_offset_hours",
    "holiday_name",
    "is_session_date",
    "is_valid_session_sequence",
    "next_session_date",
    "official_close_utc_ns",
    "previous_session_date",
    "session_kind",
    "validate_previous_session",
    "validate_session_sequence",
]
