from __future__ import annotations

from dataclasses import fields
import json
import unittest

from gld_normalizer.canonical import (
    CANONICAL_FACT_EXECUTION_STATUS,
    BoundSourceRecord,
    canonical_source_json_line,
    causal_times_eligible,
    deduplicate_source_records_in_memory,
)
from gld_normalizer.errors import NormalizationError
from gld_normalizer.readers import (
    MAX_DEFINED_I64,
    MAX_DEFINED_U32,
    MAX_DEFINED_U64,
    SourceTradeRecord,
)


def source_trade(**changes: object) -> SourceTradeRecord:
    values: dict[str, object] = {
        "source_record_ordinal": 1,
        "publisher_id": 1,
        "instrument_id": 2,
        "rtype": 0,
        "action": "T",
        "side": "N",
        "depth": 0,
        "price_nano_usd": 250_000_000_000,
        "size": 10,
        "flags": 0,
        "ts_in_delta": 5,
        "sequence": 7,
        "ts_event_ns": 100,
        "ts_recv_ns": 110,
    }
    values.update(changes)
    return SourceTradeRecord(**values)  # type: ignore[arg-type]


def bound_record(
    *, source_file_id: str = "source-a", record: SourceTradeRecord | None = None
) -> BoundSourceRecord:
    return BoundSourceRecord(
        source_file_id=source_file_id,
        source_file_sha256="a" * 64,
        dataset="EQUS.MINI",
        source_schema="trades",
        canonical_symbol="GLD",
        symbol_binding_basis="REQUEST_MANIFEST_HASH_BOUND",
        record=record or source_trade(),
    )


class CanonicalSourceTests(unittest.TestCase):
    def test_final_canonical_fact_is_explicitly_not_implemented(self) -> None:
        self.assertEqual(
            CANONICAL_FACT_EXECUTION_STATUS,
            "NOT_IMPLEMENTED_QUALIFIED_SOURCE_CALENDAR_AND_PHASE_BINDING_REQUIRED",
        )

    def test_cutoff_requires_exact_bounded_integer_times(self) -> None:
        self.assertTrue(
            causal_times_eligible(ts_event_ns=100, ts_recv_ns=110, cutoff_ns=111)
        )
        self.assertFalse(
            causal_times_eligible(ts_event_ns=100, ts_recv_ns=110, cutoff_ns=110)
        )
        for invalid in (True, 1.5, 0, -1, 2**64 - 1, 2**64):
            with self.subTest(invalid=invalid):
                with self.assertRaises(NormalizationError):
                    causal_times_eligible(
                        ts_event_ns=100,
                        ts_recv_ns=110,
                        cutoff_ns=invalid,
                    )

    def test_source_record_rejects_float_bool_and_undefined_sentinels(self) -> None:
        invalid_fields = {
            "source_record_ordinal": (True, 1.5, 0),
            "publisher_id": (True, 1.5, 65_535),
            "instrument_id": (True, 1.5, 2**32 - 1),
            "depth": (True, 1.5, 255),
            "price_nano_usd": (True, 1.5, 2**63 - 1),
            "size": (True, 1.5, 2**32 - 1),
            "flags": (True, 1.5, 256),
            "ts_in_delta": (True, 1.5, 2**31 - 1),
            "sequence": (True, 1.5, 2**32 - 1),
            "ts_event_ns": (True, 1.5, 2**64 - 1),
            "ts_recv_ns": (True, 1.5, 2**64 - 1),
        }
        for field_name, invalid_values in invalid_fields.items():
            for invalid in invalid_values:
                with self.subTest(field=field_name, invalid=invalid):
                    with self.assertRaises(NormalizationError):
                        bound_record(record=source_trade(**{field_name: invalid}))

    def test_source_binding_is_mandatory(self) -> None:
        valid = bound_record()
        values = {field.name: getattr(valid, field.name) for field in fields(valid)}
        for field_name, invalid in (
            ("source_file_sha256", "short"),
            ("dataset", "OTHER"),
            ("source_schema", "ohlcv-1m"),
            ("canonical_symbol", "SLV"),
            ("symbol_binding_basis", "ASSUMED"),
        ):
            with self.subTest(field=field_name):
                with self.assertRaises(NormalizationError):
                    BoundSourceRecord(**{**values, field_name: invalid})

    def test_exact_retransmission_keeps_earliest_receive(self) -> None:
        later = bound_record(
            source_file_id="later", record=source_trade(ts_recv_ns=120)
        )
        earlier = bound_record(
            source_file_id="earlier", record=source_trade(ts_recv_ns=110)
        )
        retained, ledger = deduplicate_source_records_in_memory(
            [later, earlier], max_records=10
        )
        self.assertEqual(retained, [earlier])
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0].reason, "EXACT_RETRANSMISSION")

    def test_conflicting_payload_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            NormalizationError, "CONFLICTING_RETRANSMISSION"
        ):
            deduplicate_source_records_in_memory(
                [
                    bound_record(record=source_trade(size=10)),
                    bound_record(
                        source_file_id="other", record=source_trade(size=11)
                    ),
                ],
                max_records=10,
            )

    def test_in_memory_dedup_cap_is_strict_and_bounded(self) -> None:
        for invalid in (True, 1.5, 0, 100_001):
            with self.subTest(invalid=invalid):
                with self.assertRaises(NormalizationError):
                    deduplicate_source_records_in_memory([], max_records=invalid)
        with self.assertRaisesRegex(NormalizationError, "RESOURCE_LIMIT_EXCEEDED"):
            deduplicate_source_records_in_memory(
                [
                    bound_record(record=source_trade(sequence=1)),
                    bound_record(record=source_trade(sequence=2)),
                ],
                max_records=1,
            )

    def test_source_json_is_deterministic_and_not_a_final_fact(self) -> None:
        line = canonical_source_json_line(bound_record())
        parsed = json.loads(line)
        self.assertEqual(list(parsed), sorted(parsed))
        self.assertEqual(parsed["record"]["price_nano_usd"], "250000000000")
        self.assertNotIn("session_date", parsed)
        self.assertNotIn("market_phase", parsed)
        self.assertTrue(line.endswith("\n"))


if __name__ == "__main__":
    unittest.main()

