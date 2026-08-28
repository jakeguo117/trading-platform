from __future__ import annotations

from hashlib import sha256
import inspect
from pathlib import Path
import struct
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from gld_normalizer.errors import NormalizationError
import gld_normalizer.readers as readers
from gld_normalizer.readers import (
    CSV_TRADE_HEADER,
    MAX_FIXTURE_PAYLOAD_BYTES,
    MAX_FIXTURE_RECORDS,
    REAL_SOURCE_EXECUTION_STATUS,
    SourceTradeRecord,
    decode_csv_fixture,
    decode_dbn_v1_fixture,
)


DBN_METADATA = b"synthetic-metadata-semantics-intentionally-not-parsed"


def csv_row(
    *,
    ts_recv: str = "1001",
    ts_event: str = "1000",
    price: str = "250.125000001",
    size: str = "3",
    publisher_id: str = "7",
    instrument_id: str = "11",
    depth: str = "0",
    ts_in_delta: str = "5",
    sequence: str = "6",
) -> str:
    return ",".join(
        (
            ts_recv,
            ts_event,
            "0",
            publisher_id,
            instrument_id,
            "T",
            "N",
            depth,
            price,
            size,
            "4",
            ts_in_delta,
            sequence,
        )
    )


def csv_payload(*rows: str, header: tuple[str, ...] = CSV_TRADE_HEADER) -> bytes:
    return (",".join(header) + "\n" + "\n".join(rows) + "\n").encode("ascii")


def dbn_trade_record(
    *,
    ts_event_ns: int = 1_000,
    ts_recv_ns: int = 1_001,
    price_nano_usd: int = 250_125_000_001,
    size: int = 3,
    rtype: int = 0,
    length_words: int = 12,
    publisher_id: int = 7,
    instrument_id: int = 11,
    depth: int = 0,
    ts_in_delta: int = 5,
    sequence: int = 6,
) -> bytes:
    return struct.pack(
        "<BBHIQqI4BQiI",
        length_words,
        rtype,
        publisher_id,
        instrument_id,
        ts_event_ns,
        price_nano_usd,
        size,
        ord("T"),
        ord("N"),
        4,
        depth,
        ts_recv_ns,
        ts_in_delta,
        sequence,
    )


def dbn_payload(
    *records: bytes,
    metadata: bytes = DBN_METADATA,
    declared_metadata_length: int | None = None,
) -> bytes:
    metadata_length = (
        len(metadata) if declared_metadata_length is None else declared_metadata_length
    )
    return b"DBN\x01" + struct.pack("<I", metadata_length) + metadata + b"".join(records)


class FixtureDecoderTests(unittest.TestCase):
    def assert_reason(self, reason_code: str, operation) -> None:
        with self.assertRaises(NormalizationError) as raised:
            operation()
        self.assertEqual(raised.exception.reason_code, reason_code)

    def decode_csv(self, payload: bytes, **overrides) -> tuple[SourceTradeRecord, ...]:
        arguments = {
            "max_payload_bytes": 8_192,
            "max_records": 4,
            "expected_record_count": 1,
            "expected_min_ts_event_ns": 1_000,
            "expected_max_ts_event_ns": 1_000,
        }
        arguments.update(overrides)
        return decode_csv_fixture(payload, **arguments)

    def decode_dbn(self, payload: bytes, **overrides) -> tuple[SourceTradeRecord, ...]:
        arguments = {
            "max_payload_bytes": 8_192,
            "max_records": 4,
            "expected_metadata_sha256": sha256(DBN_METADATA).hexdigest(),
            "expected_metadata_length": len(DBN_METADATA),
            "expected_record_count": 1,
            "expected_min_ts_event_ns": 1_000,
            "expected_max_ts_event_ns": 1_000,
        }
        arguments.update(overrides)
        return decode_dbn_v1_fixture(payload, **arguments)

    def test_real_source_execution_is_explicitly_not_implemented(self) -> None:
        self.assertEqual(
            REAL_SOURCE_EXECUTION_STATUS,
            "NOT_IMPLEMENTED_SOURCE_BINDING_AND_ATOMIC_STAGING_REQUIRED",
        )
        self.assertFalse(hasattr(readers, "iter_csv_zstd_trades"))
        self.assertFalse(hasattr(readers, "iter_dbn_v1_zstd_trades"))
        self.assertFalse(inspect.isgeneratorfunction(decode_csv_fixture))
        self.assertFalse(inspect.isgeneratorfunction(decode_dbn_v1_fixture))

    def test_csv_returns_immutable_records_only_after_full_validation(self) -> None:
        records = self.decode_csv(csv_payload(csv_row()))

        self.assertIsInstance(records, tuple)
        self.assertEqual(
            records,
            (
                SourceTradeRecord(
                    source_record_ordinal=1,
                    publisher_id=7,
                    instrument_id=11,
                    rtype=0,
                    action="T",
                    side="N",
                    depth=0,
                    price_nano_usd=250_125_000_001,
                    size=3,
                    flags=4,
                    ts_in_delta=5,
                    sequence=6,
                    ts_event_ns=1_000,
                    ts_recv_ns=1_001,
                ),
            ),
        )

    def test_csv_failed_tail_is_atomic(self) -> None:
        payload = csv_payload(csv_row(), csv_row(ts_event="1002", ts_recv="1001"))

        self.assert_reason(
            "EVENT_AFTER_RECEIVE",
            lambda: self.decode_csv(
                payload,
                expected_record_count=2,
                expected_min_ts_event_ns=1_000,
                expected_max_ts_event_ns=1_002,
            ),
        )

    def test_csv_rejects_header_row_and_minmax_mismatch(self) -> None:
        wrong_header = list(CSV_TRADE_HEADER)
        wrong_header[0], wrong_header[1] = wrong_header[1], wrong_header[0]
        self.assert_reason(
            "CSV_HEADER_MISMATCH",
            lambda: self.decode_csv(csv_payload(header=tuple(wrong_header))),
        )
        self.assert_reason(
            "RECORD_COUNT_MISMATCH",
            lambda: self.decode_csv(csv_payload(csv_row()), expected_record_count=2),
        )
        self.assert_reason(
            "EVENT_TIME_RANGE_MISMATCH",
            lambda: self.decode_csv(
                csv_payload(csv_row()), expected_max_ts_event_ns=1_001
            ),
        )

    def test_csv_rejects_oversized_numeric_before_integer_conversion(self) -> None:
        payload = csv_payload(csv_row(size="9" * 1_000))

        self.assert_reason(
            "CSV_NUMERIC_FIELD_TOO_LONG", lambda: self.decode_csv(payload)
        )

    def test_csv_enforces_payload_and_record_caps(self) -> None:
        payload = csv_payload(csv_row(), csv_row(ts_recv="1002", ts_event="1001"))
        self.assert_reason(
            "FIXTURE_PAYLOAD_CAP_EXCEEDED",
            lambda: self.decode_csv(payload, max_payload_bytes=len(payload) - 1),
        )
        self.assert_reason(
            "EXPECTED_RECORD_COUNT_EXCEEDS_CAP",
            lambda: self.decode_csv(
                payload,
                max_records=1,
                expected_record_count=2,
                expected_min_ts_event_ns=1_000,
                expected_max_ts_event_ns=1_001,
            ),
        )

    def test_dbn_returns_immutable_records_after_metadata_binding(self) -> None:
        records = self.decode_dbn(dbn_payload(dbn_trade_record()))

        self.assertIsInstance(records, tuple)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].price_nano_usd, 250_125_000_001)

    def test_dbn_rejects_malformed_magic_metadata_length_and_hash(self) -> None:
        valid = dbn_payload(dbn_trade_record())
        self.assert_reason(
            "DBN_MAGIC_MISMATCH", lambda: self.decode_dbn(b"BAD\x01" + valid[4:])
        )
        self.assert_reason(
            "DBN_METADATA_LENGTH_MISMATCH",
            lambda: self.decode_dbn(
                dbn_payload(
                    dbn_trade_record(),
                    declared_metadata_length=len(DBN_METADATA) + 1,
                )
            ),
        )
        self.assert_reason(
            "DBN_METADATA_SHA256_MISMATCH",
            lambda: self.decode_dbn(valid, expected_metadata_sha256="0" * 64),
        )

    def test_dbn_rejects_truncated_metadata(self) -> None:
        payload = b"DBN\x01" + struct.pack("<I", len(DBN_METADATA)) + DBN_METADATA[:-1]

        self.assert_reason(
            "DBN_METADATA_TRUNCATED", lambda: self.decode_dbn(payload)
        )

    def test_dbn_failed_tail_is_atomic(self) -> None:
        payload = dbn_payload(dbn_trade_record(), dbn_trade_record()[:-1])

        self.assert_reason(
            "DBN_RECORD_TRUNCATED",
            lambda: self.decode_dbn(
                payload,
                expected_record_count=2,
                expected_min_ts_event_ns=1_000,
                expected_max_ts_event_ns=1_000,
            ),
        )

    def test_dbn_rejects_semantic_and_resource_failures(self) -> None:
        bad_time = dbn_payload(dbn_trade_record(ts_event_ns=1_002, ts_recv_ns=1_001))
        self.assert_reason("EVENT_AFTER_RECEIVE", lambda: self.decode_dbn(bad_time))

        two_records = dbn_payload(
            dbn_trade_record(), dbn_trade_record(ts_event_ns=1_001, ts_recv_ns=1_002)
        )
        self.assert_reason(
            "EXPECTED_RECORD_COUNT_EXCEEDS_CAP",
            lambda: self.decode_dbn(
                two_records,
                max_records=1,
                expected_record_count=2,
                expected_min_ts_event_ns=1_000,
                expected_max_ts_event_ns=1_001,
            ),
        )
        self.assert_reason(
            "FIXTURE_PAYLOAD_CAP_EXCEEDED",
            lambda: self.decode_dbn(
                two_records, max_payload_bytes=len(two_records) - 1
            ),
        )

    def test_csv_and_dbn_reject_the_same_undefined_field_sentinels(self) -> None:
        cases = (
            (
                {"publisher_id": "65535"},
                {"publisher_id": 65_535},
                "TRADE_PUBLISHER_ID_INVALID",
            ),
            (
                {"instrument_id": str(2**32 - 1)},
                {"instrument_id": 2**32 - 1},
                "TRADE_INSTRUMENT_ID_INVALID",
            ),
            ({"depth": "255"}, {"depth": 255}, "TRADE_DEPTH_INVALID"),
            (
                {"ts_in_delta": str(2**31 - 1)},
                {"ts_in_delta": 2**31 - 1},
                "TRADE_TS_IN_DELTA_INVALID",
            ),
            (
                {"sequence": str(2**32 - 1)},
                {"sequence": 2**32 - 1},
                "TRADE_SEQUENCE_INVALID",
            ),
        )
        for csv_fields, dbn_fields, reason_code in cases:
            with self.subTest(reason_code=reason_code, source="CSV"):
                self.assert_reason(
                    reason_code,
                    lambda csv_fields=csv_fields: self.decode_csv(
                        csv_payload(csv_row(**csv_fields))
                    ),
                )
            with self.subTest(reason_code=reason_code, source="DBN"):
                self.assert_reason(
                    reason_code,
                    lambda dbn_fields=dbn_fields: self.decode_dbn(
                        dbn_payload(dbn_trade_record(**dbn_fields))
                    ),
                )

    def test_invalid_caps_fail_closed(self) -> None:
        payload = csv_payload(csv_row())
        self.assert_reason(
            "FIXTURE_PAYLOAD_CAP_INVALID",
            lambda: self.decode_csv(payload, max_payload_bytes=0),
        )
        self.assert_reason(
            "FIXTURE_RECORD_CAP_INVALID",
            lambda: self.decode_csv(payload, max_records=-1),
        )
        self.assert_reason(
            "FIXTURE_PAYLOAD_CAP_INVALID",
            lambda: self.decode_csv(
                payload, max_payload_bytes=MAX_FIXTURE_PAYLOAD_BYTES + 1
            ),
        )
        self.assert_reason(
            "FIXTURE_RECORD_CAP_INVALID",
            lambda: self.decode_csv(payload, max_records=MAX_FIXTURE_RECORDS + 1),
        )
        self.assert_reason(
            "EXPECTED_RECORD_COUNT_EXCEEDS_CAP",
            lambda: self.decode_csv(
                payload,
                max_records=0,
                expected_record_count=1,
                expected_min_ts_event_ns=1_000,
                expected_max_ts_event_ns=1_000,
            ),
        )


if __name__ == "__main__":
    unittest.main()
