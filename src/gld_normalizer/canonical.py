"""Bounded source-record hashing and causal checks for synthetic fixtures.

Final canonical market facts are intentionally not implemented. They require
real source-binding receipts plus authority-qualified calendar and phase facts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Iterable, Mapping

from .errors import NormalizationError
from .readers import (
    MAX_DEFINED_I64,
    MAX_DEFINED_U32,
    MAX_DEFINED_U64,
    SourceTradeRecord,
)


CANONICAL_FACT_EXECUTION_STATUS = (
    "NOT_IMPLEMENTED_QUALIFIED_SOURCE_CALENDAR_AND_PHASE_BINDING_REQUIRED"
)
MAX_IN_MEMORY_DEDUP_RECORDS = 100_000
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _sha256_mapping(values: Mapping[str, object]) -> str:
    encoded = json.dumps(
        values,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


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


def _validate_source_record(record: object) -> SourceTradeRecord:
    if not isinstance(record, SourceTradeRecord):
        raise NormalizationError("SOURCE_RECORD_TYPE_INVALID")
    _require_exact_int(
        record.source_record_ordinal,
        reason_code="SOURCE_RECORD_ORDINAL_INVALID",
        minimum=1,
        maximum=MAX_DEFINED_U64,
    )
    _require_exact_int(
        record.publisher_id,
        reason_code="TRADE_PUBLISHER_ID_INVALID",
        minimum=0,
        maximum=65_534,
    )
    _require_exact_int(
        record.instrument_id,
        reason_code="TRADE_INSTRUMENT_ID_INVALID",
        minimum=0,
        maximum=MAX_DEFINED_U32,
    )
    if record.rtype != 0 or type(record.rtype) is not int:
        raise NormalizationError("TRADE_RTYPE_INVALID")
    if record.action != "T":
        raise NormalizationError("TRADE_ACTION_INVALID")
    if record.side not in {"A", "B", "N"}:
        raise NormalizationError("TRADE_SIDE_INVALID")
    _require_exact_int(
        record.depth,
        reason_code="TRADE_DEPTH_INVALID",
        minimum=0,
        maximum=254,
    )
    _require_exact_int(
        record.price_nano_usd,
        reason_code="TRADE_PRICE_INVALID",
        minimum=1,
        maximum=MAX_DEFINED_I64,
    )
    _require_exact_int(
        record.size,
        reason_code="TRADE_SIZE_INVALID",
        minimum=1,
        maximum=MAX_DEFINED_U32,
    )
    _require_exact_int(
        record.flags,
        reason_code="TRADE_FLAGS_INVALID",
        minimum=0,
        maximum=255,
    )
    _require_exact_int(
        record.ts_in_delta,
        reason_code="TRADE_TS_IN_DELTA_INVALID",
        minimum=-(2**31),
        maximum=2**31 - 2,
    )
    _require_exact_int(
        record.sequence,
        reason_code="TRADE_SEQUENCE_INVALID",
        minimum=0,
        maximum=MAX_DEFINED_U32,
    )
    _require_exact_int(
        record.ts_event_ns,
        reason_code="TRADE_TIMESTAMP_INVALID",
        minimum=1,
        maximum=MAX_DEFINED_U64,
    )
    _require_exact_int(
        record.ts_recv_ns,
        reason_code="TRADE_TIMESTAMP_INVALID",
        minimum=1,
        maximum=MAX_DEFINED_U64,
    )
    if record.ts_recv_ns < record.ts_event_ns:
        raise NormalizationError("EVENT_AFTER_RECEIVE")
    return record


@dataclass(frozen=True, slots=True)
class BoundSourceRecord:
    """A source record bound to fixture identity, not a final market fact."""

    source_file_id: str
    source_file_sha256: str
    dataset: str
    source_schema: str
    canonical_symbol: str
    symbol_binding_basis: str
    record: SourceTradeRecord

    def __post_init__(self) -> None:
        if not isinstance(self.source_file_id, str) or not self.source_file_id:
            raise NormalizationError("SOURCE_FILE_ID_INVALID")
        if (
            not isinstance(self.source_file_sha256, str)
            or _SHA256_RE.fullmatch(self.source_file_sha256) is None
        ):
            raise NormalizationError("SOURCE_FILE_SHA256_INVALID")
        if self.dataset != "EQUS.MINI" or self.source_schema != "trades":
            raise NormalizationError("SOURCE_SEMANTICS_UNQUALIFIED")
        if self.canonical_symbol != "GLD":
            raise NormalizationError("SYMBOL_BINDING_UNPROVEN")
        if self.symbol_binding_basis != "REQUEST_MANIFEST_HASH_BOUND":
            raise NormalizationError("SYMBOL_BINDING_UNPROVEN")
        _validate_source_record(self.record)

    @property
    def natural_key_hash(self) -> str:
        return _sha256_mapping(
            {
                "dataset": self.dataset,
                "instrument_id": self.record.instrument_id,
                "publisher_id": self.record.publisher_id,
                "rtype": self.record.rtype,
                "sequence": self.record.sequence,
                "ts_event_ns": str(self.record.ts_event_ns),
            }
        )

    @property
    def economic_payload_hash(self) -> str:
        return _sha256_mapping(
            {
                "action": self.record.action,
                "depth": self.record.depth,
                "flags": self.record.flags,
                "price_nano_usd": str(self.record.price_nano_usd),
                "side": self.record.side,
                "size": self.record.size,
                "ts_in_delta": self.record.ts_in_delta,
            }
        )

    @property
    def source_record_hash(self) -> str:
        values = asdict(self)
        record_values = values["record"]
        for key in ("price_nano_usd", "ts_event_ns", "ts_recv_ns"):
            record_values[key] = str(record_values[key])
        return _sha256_mapping(values)


@dataclass(frozen=True, slots=True)
class DuplicateLedgerRecord:
    natural_key_hash: str
    retained_record_hash: str
    discarded_record_hash: str
    retained_ts_recv_ns: int
    discarded_ts_recv_ns: int
    reason: str = "EXACT_RETRANSMISSION"


def causal_times_eligible(
    *, ts_event_ns: object, ts_recv_ns: object, cutoff_ns: object
) -> bool:
    event = _require_exact_int(
        ts_event_ns,
        reason_code="TRADE_TIMESTAMP_INVALID",
        minimum=1,
        maximum=MAX_DEFINED_U64,
    )
    received = _require_exact_int(
        ts_recv_ns,
        reason_code="TRADE_TIMESTAMP_INVALID",
        minimum=1,
        maximum=MAX_DEFINED_U64,
    )
    cutoff = _require_exact_int(
        cutoff_ns,
        reason_code="CUTOFF_TIMESTAMP_INVALID",
        minimum=1,
        maximum=MAX_DEFINED_U64,
    )
    if received < event:
        raise NormalizationError("EVENT_AFTER_RECEIVE")
    return event < cutoff and received < cutoff


def deduplicate_source_records_in_memory(
    records: Iterable[BoundSourceRecord], *, max_records: object
) -> tuple[list[BoundSourceRecord], list[DuplicateLedgerRecord]]:
    cap = _require_exact_int(
        max_records,
        reason_code="RESOURCE_LIMIT_INVALID",
        minimum=1,
        maximum=MAX_IN_MEMORY_DEDUP_RECORDS,
    )
    by_key: dict[str, list[BoundSourceRecord]] = {}
    for count, item in enumerate(records, start=1):
        if count > cap:
            raise NormalizationError("RESOURCE_LIMIT_EXCEEDED")
        if not isinstance(item, BoundSourceRecord):
            raise NormalizationError("BOUND_SOURCE_RECORD_TYPE_INVALID")
        by_key.setdefault(item.natural_key_hash, []).append(item)

    retained: list[BoundSourceRecord] = []
    ledger: list[DuplicateLedgerRecord] = []
    for natural_key_hash, group in by_key.items():
        if len({item.economic_payload_hash for item in group}) != 1:
            raise NormalizationError("CONFLICTING_RETRANSMISSION")
        ordered = sorted(
            group,
            key=lambda item: (
                item.record.ts_recv_ns,
                item.record.ts_event_ns,
                item.source_file_id,
                item.record.source_record_ordinal,
            ),
        )
        winner = ordered[0]
        retained.append(winner)
        for duplicate in ordered[1:]:
            ledger.append(
                DuplicateLedgerRecord(
                    natural_key_hash=natural_key_hash,
                    retained_record_hash=winner.source_record_hash,
                    discarded_record_hash=duplicate.source_record_hash,
                    retained_ts_recv_ns=winner.record.ts_recv_ns,
                    discarded_ts_recv_ns=duplicate.record.ts_recv_ns,
                )
            )

    retained.sort(
        key=lambda item: (
            item.record.ts_recv_ns,
            item.record.ts_event_ns,
            item.record.publisher_id,
            item.record.instrument_id,
            item.record.sequence,
            item.source_file_id,
            item.record.source_record_ordinal,
        )
    )
    ledger.sort(
        key=lambda item: (
            item.retained_ts_recv_ns,
            item.discarded_ts_recv_ns,
            item.discarded_record_hash,
        )
    )
    return retained, ledger


def canonical_source_json_line(item: BoundSourceRecord) -> str:
    """Serialize a fixture-bound source record, never a final market fact."""

    if not isinstance(item, BoundSourceRecord):
        raise NormalizationError("BOUND_SOURCE_RECORD_TYPE_INVALID")
    values = asdict(item)
    for key in ("price_nano_usd", "ts_event_ns", "ts_recv_ns"):
        values["record"][key] = str(values["record"][key])
    values["economic_payload_hash"] = item.economic_payload_hash
    values["natural_key_hash"] = item.natural_key_hash
    values["source_record_hash"] = item.source_record_hash
    return json.dumps(values, separators=(",", ":"), sort_keys=True) + "\n"


__all__ = [
    "CANONICAL_FACT_EXECUTION_STATUS",
    "MAX_IN_MEMORY_DEDUP_RECORDS",
    "BoundSourceRecord",
    "DuplicateLedgerRecord",
    "canonical_source_json_line",
    "causal_times_eligible",
    "deduplicate_source_records_in_memory",
]

