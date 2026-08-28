"""Bounded semantic decoders for synthetic, in-memory trade fixtures only.

These functions deliberately do not open paths, invoke decompressors, follow
symlinks, stage artifacts, or qualify real sources.  A future real-source
reader requires a separate source-binding and atomic-staging implementation.

DBN metadata semantics are not parsed here.  Fixture callers must bind the
metadata as opaque bytes using both its exact length and SHA-256 digest.
"""

from __future__ import annotations

import calendar
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import io
import re
import struct

from .errors import NormalizationError


REAL_SOURCE_EXECUTION_STATUS = (
    "NOT_IMPLEMENTED_SOURCE_BINDING_AND_ATOMIC_STAGING_REQUIRED"
)
DBN_METADATA_SEMANTICS_STATUS = "NOT_PARSED_CALLER_BOUND_OPAQUE_BYTES_ONLY"
MAX_FIXTURE_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_FIXTURE_RECORDS = 100_000

CSV_TRADE_HEADER = (
    "ts_recv",
    "ts_event",
    "rtype",
    "publisher_id",
    "instrument_id",
    "action",
    "side",
    "depth",
    "price",
    "size",
    "flags",
    "ts_in_delta",
    "sequence",
)

DBN_V1_MAGIC = b"DBN\x01"
DBN_TRADE_RTYPE = 0
DBN_TRADE_RECORD_LENGTH = 48
MAX_DEFINED_I64 = 2**63 - 2
MAX_DEFINED_U64 = 2**64 - 2
MAX_DEFINED_U32 = 2**32 - 2
MAX_I64 = 2**63 - 1
MAX_U64 = 2**64 - 1
MAX_U32 = 2**32 - 1

_DBN_HEADER = struct.Struct("<4sI")
_DBN_RECORD_HEADER = struct.Struct("<BBHIQ")
_DBN_TRADE_REMAINDER = struct.Struct("<qI4BQiI")
_INTEGER_RE = re.compile(r"-?[0-9]+\Z")
_PRICE_RE = re.compile(r"[0-9]+(?:\.[0-9]{1,9})?\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_UTC_TIMESTAMP_RE = re.compile(
    r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,9}))?Z\Z"
)
_MAX_INTEGER_FIELD_CHARS = 20
_MAX_PRICE_FIELD_CHARS = 20
_MAX_TIMESTAMP_FIELD_CHARS = 30


@dataclass(frozen=True, slots=True)
class SourceTradeRecord:
    """Immutable typed fields decoded from one fully validated fixture row."""

    source_record_ordinal: int
    publisher_id: int
    instrument_id: int
    rtype: int
    action: str
    side: str
    depth: int
    price_nano_usd: int
    size: int
    flags: int
    ts_in_delta: int
    sequence: int
    ts_event_ns: int
    ts_recv_ns: int


def _validate_fixture_contract(
    payload: bytes,
    *,
    max_payload_bytes: int,
    max_records: int,
    expected_record_count: int,
    expected_min_ts_event_ns: int | None,
    expected_max_ts_event_ns: int | None,
) -> None:
    if type(payload) is not bytes:
        raise NormalizationError("FIXTURE_PAYLOAD_TYPE_INVALID")
    if (
        isinstance(max_payload_bytes, bool)
        or not isinstance(max_payload_bytes, int)
        or not 1 <= max_payload_bytes <= MAX_FIXTURE_PAYLOAD_BYTES
    ):
        raise NormalizationError("FIXTURE_PAYLOAD_CAP_INVALID")
    if (
        isinstance(max_records, bool)
        or not isinstance(max_records, int)
        or not 0 <= max_records <= MAX_FIXTURE_RECORDS
    ):
        raise NormalizationError("FIXTURE_RECORD_CAP_INVALID")
    if len(payload) > max_payload_bytes:
        raise NormalizationError("FIXTURE_PAYLOAD_CAP_EXCEEDED")
    if (
        isinstance(expected_record_count, bool)
        or not isinstance(expected_record_count, int)
        or expected_record_count < 0
    ):
        raise NormalizationError("EXPECTED_RECORD_COUNT_INVALID")
    if expected_record_count > max_records:
        raise NormalizationError("EXPECTED_RECORD_COUNT_EXCEEDS_CAP")
    if expected_record_count == 0:
        if (
            expected_min_ts_event_ns is not None
            or expected_max_ts_event_ns is not None
        ):
            raise NormalizationError("EXPECTED_EVENT_TIME_RANGE_INVALID")
        return
    if (
        isinstance(expected_min_ts_event_ns, bool)
        or isinstance(expected_max_ts_event_ns, bool)
        or not isinstance(expected_min_ts_event_ns, int)
        or not isinstance(expected_max_ts_event_ns, int)
        or expected_min_ts_event_ns <= 0
        or expected_max_ts_event_ns <= 0
        or expected_min_ts_event_ns > expected_max_ts_event_ns
        or expected_max_ts_event_ns > MAX_DEFINED_U64
    ):
        raise NormalizationError("EXPECTED_EVENT_TIME_RANGE_INVALID")


def _validate_completed_records(
    records: list[SourceTradeRecord],
    *,
    expected_record_count: int,
    expected_min_ts_event_ns: int | None,
    expected_max_ts_event_ns: int | None,
) -> tuple[SourceTradeRecord, ...]:
    if len(records) != expected_record_count:
        raise NormalizationError("RECORD_COUNT_MISMATCH")
    if records:
        actual_min = min(record.ts_event_ns for record in records)
        actual_max = max(record.ts_event_ns for record in records)
        if (
            actual_min != expected_min_ts_event_ns
            or actual_max != expected_max_ts_event_ns
        ):
            raise NormalizationError("EVENT_TIME_RANGE_MISMATCH")
    return tuple(records)


def _parse_int(
    value: str,
    *,
    code: str,
    minimum: int,
    maximum: int,
) -> int:
    if len(value) > _MAX_INTEGER_FIELD_CHARS:
        raise NormalizationError("CSV_NUMERIC_FIELD_TOO_LONG")
    if _INTEGER_RE.fullmatch(value) is None:
        raise NormalizationError(code)
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        raise NormalizationError(code)
    return parsed


def _parse_utc_nanoseconds(value: str) -> int:
    if len(value) > _MAX_TIMESTAMP_FIELD_CHARS:
        raise NormalizationError("CSV_NUMERIC_FIELD_TOO_LONG")
    if value.isascii() and value.isdigit():
        parsed = int(value)
        if parsed > MAX_U64:
            raise NormalizationError("CSV_TIMESTAMP_INVALID")
        return parsed

    match = _UTC_TIMESTAMP_RE.fullmatch(value)
    if match is None:
        raise NormalizationError("CSV_TIMESTAMP_INVALID")
    parts = {
        key: int(match.group(key))
        for key in ("year", "month", "day", "hour", "minute", "second")
    }
    try:
        timestamp = datetime(**parts, tzinfo=timezone.utc)
    except ValueError as error:
        raise NormalizationError("CSV_TIMESTAMP_INVALID") from error
    seconds = calendar.timegm(timestamp.utctimetuple())
    fraction = (match.group("fraction") or "").ljust(9, "0")
    parsed = seconds * 1_000_000_000 + int(fraction or "0")
    if parsed > MAX_U64:
        raise NormalizationError("CSV_TIMESTAMP_INVALID")
    return parsed


def _parse_price_nano_usd(value: str) -> int:
    if len(value) > _MAX_PRICE_FIELD_CHARS:
        raise NormalizationError("CSV_NUMERIC_FIELD_TOO_LONG")
    if _PRICE_RE.fullmatch(value) is None:
        raise NormalizationError("CSV_PRICE_INVALID")
    whole, separator, fraction = value.partition(".")
    scaled = int(whole) * 1_000_000_000
    if separator:
        scaled += int(fraction.ljust(9, "0"))
    if scaled > MAX_I64:
        raise NormalizationError("CSV_PRICE_INVALID")
    return scaled


def _parse_trade_enum(value: str, *, code: str, allowed: frozenset[str]) -> str:
    if len(value) != 1 or not value.isascii() or value not in allowed:
        raise NormalizationError(code)
    return value


def _decode_trade_enum(value: int, *, code: str, allowed: frozenset[str]) -> str:
    if not 0 <= value <= 127:
        raise NormalizationError(code)
    return _parse_trade_enum(chr(value), code=code, allowed=allowed)


def _validate_trade_fields(
    *,
    publisher_id: int,
    instrument_id: int,
    depth: int,
    price_nano_usd: int,
    size: int,
    flags: int,
    ts_in_delta: int,
    sequence: int,
    ts_event_ns: int,
    ts_recv_ns: int,
) -> None:
    if not 0 <= publisher_id <= 65_534:
        raise NormalizationError("TRADE_PUBLISHER_ID_INVALID")
    if not 0 <= instrument_id <= MAX_DEFINED_U32:
        raise NormalizationError("TRADE_INSTRUMENT_ID_INVALID")
    if not 0 <= depth <= 254:
        raise NormalizationError("TRADE_DEPTH_INVALID")
    if price_nano_usd <= 0 or price_nano_usd > MAX_DEFINED_I64:
        raise NormalizationError("TRADE_PRICE_INVALID")
    if size <= 0 or size > MAX_DEFINED_U32:
        raise NormalizationError("TRADE_SIZE_INVALID")
    if not 0 <= flags <= 255:
        raise NormalizationError("TRADE_FLAGS_INVALID")
    if not -2_147_483_648 <= ts_in_delta <= 2_147_483_646:
        raise NormalizationError("TRADE_TS_IN_DELTA_INVALID")
    if not 0 <= sequence <= MAX_DEFINED_U32:
        raise NormalizationError("TRADE_SEQUENCE_INVALID")
    if (
        ts_event_ns <= 0
        or ts_recv_ns <= 0
        or ts_event_ns > MAX_DEFINED_U64
        or ts_recv_ns > MAX_DEFINED_U64
    ):
        raise NormalizationError("TRADE_TIMESTAMP_INVALID")
    if ts_recv_ns < ts_event_ns:
        raise NormalizationError("EVENT_AFTER_RECEIVE")


def _decode_csv_row(row: list[str], *, ordinal: int) -> SourceTradeRecord:
    if len(row) != len(CSV_TRADE_HEADER):
        raise NormalizationError("CSV_ROW_FIELD_COUNT_MISMATCH")
    ts_recv_ns = _parse_utc_nanoseconds(row[0])
    ts_event_ns = _parse_utc_nanoseconds(row[1])
    rtype = _parse_int(row[2], code="CSV_RTYPE_INVALID", minimum=0, maximum=255)
    if rtype != DBN_TRADE_RTYPE:
        raise NormalizationError("CSV_RTYPE_MISMATCH")
    publisher_id = _parse_int(
        row[3], code="CSV_PUBLISHER_ID_INVALID", minimum=0, maximum=65_535
    )
    instrument_id = _parse_int(
        row[4], code="CSV_INSTRUMENT_ID_INVALID", minimum=0, maximum=MAX_U32
    )
    action = _parse_trade_enum(
        row[5], code="TRADE_ACTION_INVALID", allowed=frozenset({"T"})
    )
    side = _parse_trade_enum(
        row[6], code="TRADE_SIDE_INVALID", allowed=frozenset({"A", "B", "N"})
    )
    depth = _parse_int(row[7], code="CSV_DEPTH_INVALID", minimum=0, maximum=255)
    price_nano_usd = _parse_price_nano_usd(row[8])
    size = _parse_int(
        row[9], code="CSV_SIZE_INVALID", minimum=0, maximum=MAX_U32
    )
    flags = _parse_int(row[10], code="CSV_FLAGS_INVALID", minimum=0, maximum=255)
    ts_in_delta = _parse_int(
        row[11],
        code="CSV_TS_IN_DELTA_INVALID",
        minimum=-2_147_483_648,
        maximum=2_147_483_647,
    )
    sequence = _parse_int(
        row[12], code="CSV_SEQUENCE_INVALID", minimum=0, maximum=MAX_U32
    )
    _validate_trade_fields(
        publisher_id=publisher_id,
        instrument_id=instrument_id,
        depth=depth,
        price_nano_usd=price_nano_usd,
        size=size,
        flags=flags,
        ts_in_delta=ts_in_delta,
        sequence=sequence,
        ts_event_ns=ts_event_ns,
        ts_recv_ns=ts_recv_ns,
    )
    return SourceTradeRecord(
        source_record_ordinal=ordinal,
        publisher_id=publisher_id,
        instrument_id=instrument_id,
        rtype=rtype,
        action=action,
        side=side,
        depth=depth,
        price_nano_usd=price_nano_usd,
        size=size,
        flags=flags,
        ts_in_delta=ts_in_delta,
        sequence=sequence,
        ts_event_ns=ts_event_ns,
        ts_recv_ns=ts_recv_ns,
    )


def decode_csv_fixture(
    payload: bytes,
    *,
    max_payload_bytes: int,
    max_records: int,
    expected_record_count: int,
    expected_min_ts_event_ns: int | None,
    expected_max_ts_event_ns: int | None,
) -> tuple[SourceTradeRecord, ...]:
    """Atomically decode a bounded uncompressed CSV fixture in memory."""

    _validate_fixture_contract(
        payload,
        max_payload_bytes=max_payload_bytes,
        max_records=max_records,
        expected_record_count=expected_record_count,
        expected_min_ts_event_ns=expected_min_ts_event_ns,
        expected_max_ts_event_ns=expected_max_ts_event_ns,
    )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise NormalizationError("CSV_DECODE_FAILED") from error
    try:
        csv_reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(csv_reader, None)
        if header is None:
            raise NormalizationError("CSV_HEADER_MISSING")
        if tuple(header) != CSV_TRADE_HEADER:
            raise NormalizationError("CSV_HEADER_MISMATCH")
        decoded: list[SourceTradeRecord] = []
        for ordinal, row in enumerate(csv_reader, start=1):
            if ordinal > max_records:
                raise NormalizationError("FIXTURE_RECORD_CAP_EXCEEDED")
            decoded.append(_decode_csv_row(row, ordinal=ordinal))
    except csv.Error as error:
        raise NormalizationError("CSV_PARSE_FAILED") from error
    return _validate_completed_records(
        decoded,
        expected_record_count=expected_record_count,
        expected_min_ts_event_ns=expected_min_ts_event_ns,
        expected_max_ts_event_ns=expected_max_ts_event_ns,
    )


def decode_dbn_v1_fixture(
    payload: bytes,
    *,
    max_payload_bytes: int,
    max_records: int,
    expected_metadata_sha256: str,
    expected_metadata_length: int,
    expected_record_count: int,
    expected_min_ts_event_ns: int | None,
    expected_max_ts_event_ns: int | None,
) -> tuple[SourceTradeRecord, ...]:
    """Atomically decode bounded DBN v1 bytes with opaque metadata binding."""

    _validate_fixture_contract(
        payload,
        max_payload_bytes=max_payload_bytes,
        max_records=max_records,
        expected_record_count=expected_record_count,
        expected_min_ts_event_ns=expected_min_ts_event_ns,
        expected_max_ts_event_ns=expected_max_ts_event_ns,
    )
    if (
        isinstance(expected_metadata_length, bool)
        or not isinstance(expected_metadata_length, int)
        or expected_metadata_length <= 0
        or expected_metadata_length > max_payload_bytes
    ):
        raise NormalizationError("EXPECTED_METADATA_LENGTH_INVALID")
    if (
        not isinstance(expected_metadata_sha256, str)
        or _SHA256_RE.fullmatch(expected_metadata_sha256) is None
    ):
        raise NormalizationError("EXPECTED_METADATA_SHA256_INVALID")
    if len(payload) < _DBN_HEADER.size:
        raise NormalizationError("DBN_HEADER_TRUNCATED")
    magic, metadata_length = _DBN_HEADER.unpack_from(payload)
    if magic != DBN_V1_MAGIC:
        raise NormalizationError("DBN_MAGIC_MISMATCH")
    if metadata_length != expected_metadata_length:
        raise NormalizationError("DBN_METADATA_LENGTH_MISMATCH")
    metadata_start = _DBN_HEADER.size
    metadata_end = metadata_start + metadata_length
    if metadata_end > len(payload):
        raise NormalizationError("DBN_METADATA_TRUNCATED")
    metadata = payload[metadata_start:metadata_end]
    if sha256(metadata).hexdigest() != expected_metadata_sha256:
        raise NormalizationError("DBN_METADATA_SHA256_MISMATCH")

    decoded: list[SourceTradeRecord] = []
    offset = metadata_end
    while offset < len(payload):
        if len(decoded) >= max_records:
            raise NormalizationError("FIXTURE_RECORD_CAP_EXCEEDED")
        remaining = len(payload) - offset
        if remaining < _DBN_RECORD_HEADER.size:
            raise NormalizationError("DBN_RECORD_HEADER_TRUNCATED")
        length_words, rtype, publisher_id, instrument_id, ts_event_ns = (
            _DBN_RECORD_HEADER.unpack_from(payload, offset)
        )
        record_length = length_words * 4
        if record_length != DBN_TRADE_RECORD_LENGTH:
            raise NormalizationError("DBN_RECORD_LENGTH_MISMATCH")
        if rtype != DBN_TRADE_RTYPE:
            raise NormalizationError("DBN_RTYPE_MISMATCH")
        if remaining < record_length:
            raise NormalizationError("DBN_RECORD_TRUNCATED")
        remainder_offset = offset + _DBN_RECORD_HEADER.size
        (
            price_nano_usd,
            size,
            action_byte,
            side_byte,
            flags,
            depth,
            ts_recv_ns,
            ts_in_delta,
            sequence,
        ) = _DBN_TRADE_REMAINDER.unpack_from(payload, remainder_offset)
        action = _decode_trade_enum(
            action_byte, code="TRADE_ACTION_INVALID", allowed=frozenset({"T"})
        )
        side = _decode_trade_enum(
            side_byte,
            code="TRADE_SIDE_INVALID",
            allowed=frozenset({"A", "B", "N"}),
        )
        _validate_trade_fields(
            publisher_id=publisher_id,
            instrument_id=instrument_id,
            depth=depth,
            price_nano_usd=price_nano_usd,
            size=size,
            flags=flags,
            ts_in_delta=ts_in_delta,
            sequence=sequence,
            ts_event_ns=ts_event_ns,
            ts_recv_ns=ts_recv_ns,
        )
        decoded.append(
            SourceTradeRecord(
                source_record_ordinal=len(decoded) + 1,
                publisher_id=publisher_id,
                instrument_id=instrument_id,
                rtype=rtype,
                action=action,
                side=side,
                depth=depth,
                price_nano_usd=price_nano_usd,
                size=size,
                flags=flags,
                ts_in_delta=ts_in_delta,
                sequence=sequence,
                ts_event_ns=ts_event_ns,
                ts_recv_ns=ts_recv_ns,
            )
        )
        offset += record_length

    return _validate_completed_records(
        decoded,
        expected_record_count=expected_record_count,
        expected_min_ts_event_ns=expected_min_ts_event_ns,
        expected_max_ts_event_ns=expected_max_ts_event_ns,
    )


__all__ = [
    "CSV_TRADE_HEADER",
    "DBN_METADATA_SEMANTICS_STATUS",
    "MAX_FIXTURE_PAYLOAD_BYTES",
    "MAX_FIXTURE_RECORDS",
    "REAL_SOURCE_EXECUTION_STATUS",
    "SourceTradeRecord",
    "decode_csv_fixture",
    "decode_dbn_v1_fixture",
]
