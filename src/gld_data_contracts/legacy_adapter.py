"""Compatibility adapter for the closed 11-document synthetic bundle.

The adapter deliberately drops the legacy shared Kelly receipt.  It converts
only raw entry facts and preserves the source classification as synthetic.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import os
from pathlib import Path, PurePosixPath
import stat
from tempfile import TemporaryDirectory
from typing import Iterator, cast
from zoneinfo import ZoneInfo

from gld_research_core.crr_delta import (
    MODEL_ID,
    MODEL_SHA256,
    MODEL_SOURCE_ARTIFACT_SHA256,
    RUNTIME_FINGERPRINT_SHA256,
)
from gld_simulation.bundle import load_raw_bundle
from gld_simulation.canonical import (
    canonical_json_sha256,
    parse_canonical_json,
)
from gld_simulation.errors import RawBundleError


_NANO = 1_000_000_000
_MINUTE_NS = 60 * _NANO
_SECOND_NS = _NANO
_NEW_YORK = ZoneInfo("America/New_York")
_MAX_LEGACY_MANIFEST_BYTES = 1_000_000
_MAX_LEGACY_RAW_FILE_BYTES = 8_000_000
_MAX_LEGACY_RAW_BUNDLE_BYTES = 32_000_000
_MAX_LEGACY_TREE_NODES = 4_096
_MAX_LEGACY_TREE_DEPTH = 8
_OPEN_BASE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
_OPEN_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_OPEN_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_ENTRY_HASH_DOMAINS = (
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


def _raise_open_error(
    error: OSError,
    *,
    relative_path: str,
    missing_reason: str,
) -> None:
    if error.errno == errno.ELOOP:
        raise RawBundleError(
            "RAW_BUNDLE_SYMLINK_FORBIDDEN", relative_path
        ) from error
    if error.errno in {errno.ENOENT, errno.ENOTDIR}:
        raise RawBundleError(missing_reason, relative_path) from error
    raise RawBundleError("RAW_FILE_READ_FAILED", relative_path) from error


def _open_root_directory(root: Path | str) -> int:
    supplied_root = Path(root)
    try:
        descriptor = os.open(
            supplied_root,
            _OPEN_BASE_FLAGS | _OPEN_NOFOLLOW | _OPEN_DIRECTORY,
        )
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise RawBundleError(
                "RAW_BUNDLE_SYMLINK_FORBIDDEN", str(supplied_root)
            ) from error
        raise RawBundleError("RAW_BUNDLE_ROOT_INVALID") from error
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise RawBundleError("RAW_BUNDLE_ROOT_INVALID")
    return descriptor


def _validate_manifest_relative_path(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 256
        or not value.isascii()
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
        or "\\" in value
    ):
        raise RawBundleError("RAW_MANIFEST_PATH_INVALID")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or value == "manifest.json"
    ):
        raise RawBundleError("RAW_MANIFEST_PATH_INVALID", value)
    return value


def _open_relative_nofollow(
    root_descriptor: int,
    relative_path: str,
    *,
    missing_reason: str,
) -> int:
    parts = PurePosixPath(relative_path).parts
    current_descriptor = os.dup(root_descriptor)
    try:
        for part in parts[:-1]:
            try:
                metadata = os.stat(
                    part,
                    dir_fd=current_descriptor,
                    follow_symlinks=False,
                )
            except OSError as error:
                _raise_open_error(
                    error,
                    relative_path=relative_path,
                    missing_reason=missing_reason,
                )
            if stat.S_ISLNK(metadata.st_mode):
                raise RawBundleError(
                    "RAW_BUNDLE_SYMLINK_FORBIDDEN", relative_path
                )
            if not stat.S_ISDIR(metadata.st_mode):
                raise RawBundleError(missing_reason, relative_path)
            try:
                next_descriptor = os.open(
                    part,
                    _OPEN_BASE_FLAGS | _OPEN_NOFOLLOW | _OPEN_DIRECTORY,
                    dir_fd=current_descriptor,
                )
            except OSError as error:
                _raise_open_error(
                    error,
                    relative_path=relative_path,
                    missing_reason=missing_reason,
                )
            os.close(current_descriptor)
            current_descriptor = next_descriptor
        try:
            return os.open(
                parts[-1],
                _OPEN_BASE_FLAGS | _OPEN_NOFOLLOW,
                dir_fd=current_descriptor,
            )
        except OSError as error:
            _raise_open_error(
                error,
                relative_path=relative_path,
                missing_reason=missing_reason,
            )
    finally:
        os.close(current_descriptor)


def _read_regular_file_bounded(
    root_descriptor: int,
    relative_path: str,
    *,
    maximum_bytes: int,
    expected_bytes: int | None,
    missing_reason: str,
) -> bytes:
    descriptor = _open_relative_nofollow(
        root_descriptor,
        relative_path,
        missing_reason=missing_reason,
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RawBundleError(
                "RAW_BUNDLE_NONREGULAR_FILE_FORBIDDEN", relative_path
            )
        if before.st_size < 1 or before.st_size > maximum_bytes:
            reason = (
                "RAW_MANIFEST_SIZE_INVALID"
                if relative_path == "manifest.json"
                else "RAW_FILE_SIZE_LIMIT_EXCEEDED"
            )
            raise RawBundleError(reason, relative_path)
        if expected_bytes is not None and before.st_size != expected_bytes:
            raise RawBundleError("RAW_FILE_SIZE_MISMATCH", relative_path)

        chunks: list[bytes] = []
        total = 0
        while total <= maximum_bytes:
            try:
                chunk = os.read(
                    descriptor,
                    min(65_536, maximum_bytes + 1 - total),
                )
            except (BlockingIOError, OSError) as error:
                raise RawBundleError(
                    "RAW_FILE_READ_FAILED", relative_path
                ) from error
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > maximum_bytes:
            reason = (
                "RAW_MANIFEST_SIZE_INVALID"
                if relative_path == "manifest.json"
                else "RAW_FILE_SIZE_LIMIT_EXCEEDED"
            )
            raise RawBundleError(reason, relative_path)
        raw = b"".join(chunks)
        if expected_bytes is not None and len(raw) != expected_bytes:
            raise RawBundleError("RAW_FILE_SIZE_MISMATCH", relative_path)
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or len(raw) != before.st_size
        ):
            reason = (
                "RAW_MANIFEST_SIZE_INVALID"
                if relative_path == "manifest.json"
                else "RAW_FILE_SIZE_MISMATCH"
            )
            raise RawBundleError(reason, relative_path)
        return raw
    finally:
        os.close(descriptor)


def _manifest_registered_files(manifest_raw: bytes) -> list[tuple[str, int]]:
    manifest = parse_canonical_json(manifest_raw, source="manifest.json")
    if type(manifest) is not dict:
        raise RawBundleError("RAW_MANIFEST_SCHEMA_INVALID")
    entries = manifest.get("files")
    if type(entries) is not list or not entries:
        raise RawBundleError("RAW_MANIFEST_FILES_INVALID")
    result: list[tuple[str, int]] = []
    seen_paths: set[str] = set()
    total_bytes = 0
    for value in entries:
        if type(value) is not dict:
            raise RawBundleError("RAW_MANIFEST_FILE_ENTRY_INVALID")
        path = _validate_manifest_relative_path(value.get("path"))
        if path in seen_paths:
            raise RawBundleError("RAW_MANIFEST_PATH_DUPLICATE")
        byte_size = value.get("byte_size")
        if (
            type(byte_size) is not int
            or not 1 <= byte_size <= _MAX_LEGACY_RAW_FILE_BYTES
        ):
            raise RawBundleError("RAW_MANIFEST_BYTE_SIZE_INVALID", path)
        seen_paths.add(path)
        total_bytes += byte_size
        result.append((path, byte_size))
    if total_bytes > _MAX_LEGACY_RAW_BUNDLE_BYTES:
        raise RawBundleError("RAW_BUNDLE_SIZE_LIMIT_EXCEEDED")
    return result


def _scan_regular_tree(
    directory_descriptor: int,
    *,
    allowed_directories: frozenset[str],
    node_counter: list[int],
    prefix: tuple[str, ...] = (),
) -> set[str]:
    files: set[str] = set()
    try:
        names: list[str] = []
        with os.scandir(directory_descriptor) as entries:
            for entry in entries:
                node_counter[0] += 1
                if node_counter[0] > _MAX_LEGACY_TREE_NODES:
                    raise RawBundleError(
                        "RAW_BUNDLE_TREE_RESOURCE_LIMIT_EXCEEDED"
                    )
                names.append(entry.name)
        names.sort()
    except OSError as error:
        raise RawBundleError("RAW_BUNDLE_TREE_READ_FAILED") from error
    for name in names:
        relative_parts = (*prefix, name)
        relative_path = PurePosixPath(*relative_parts).as_posix()
        if len(relative_parts) > _MAX_LEGACY_TREE_DEPTH:
            raise RawBundleError(
                "RAW_BUNDLE_TREE_RESOURCE_LIMIT_EXCEEDED",
                relative_path,
            )
        try:
            metadata = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            raise RawBundleError(
                "RAW_BUNDLE_TREE_READ_FAILED", relative_path
            ) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise RawBundleError(
                "RAW_BUNDLE_SYMLINK_FORBIDDEN", relative_path
            )
        if stat.S_ISREG(metadata.st_mode):
            files.add(relative_path)
            continue
        if not stat.S_ISDIR(metadata.st_mode):
            raise RawBundleError(
                "RAW_BUNDLE_NONREGULAR_FILE_FORBIDDEN", relative_path
            )
        if relative_path not in allowed_directories:
            raise RawBundleError(
                "RAW_BUNDLE_UNREGISTERED_DIRECTORY",
                relative_path,
            )
        try:
            child_descriptor = os.open(
                name,
                _OPEN_BASE_FLAGS | _OPEN_NOFOLLOW | _OPEN_DIRECTORY,
                dir_fd=directory_descriptor,
            )
        except OSError as error:
            _raise_open_error(
                error,
                relative_path=relative_path,
                missing_reason="RAW_BUNDLE_TREE_READ_FAILED",
            )
        try:
            files.update(
                _scan_regular_tree(
                    child_descriptor,
                    allowed_directories=allowed_directories,
                    node_counter=node_counter,
                    prefix=relative_parts,
                )
            )
        finally:
            os.close(child_descriptor)
    return files


def _require_closed_tree(
    root_descriptor: int,
    registered_files: list[tuple[str, int]],
) -> None:
    expected = {path for path, _byte_size in registered_files}
    expected.add("manifest.json")
    allowed_directories = frozenset(
        PurePosixPath(*parts[:index]).as_posix()
        for path, _byte_size in registered_files
        for parts in (PurePosixPath(path).parts,)
        for index in range(1, len(parts))
    )
    actual = _scan_regular_tree(
        root_descriptor,
        allowed_directories=allowed_directories,
        node_counter=[0],
    )
    unexpected = actual - expected
    if unexpected:
        raise RawBundleError(
            "RAW_BUNDLE_UNREGISTERED_FILE", sorted(unexpected)[0]
        )
    missing = expected - actual
    if missing:
        raise RawBundleError(
            "RAW_BUNDLE_REGISTERED_FILE_MISSING", sorted(missing)[0]
        )


@contextmanager
def _safe_legacy_bundle_snapshot(root: Path | str) -> Iterator[Path]:
    """Yield a bounded regular-file snapshot safe for the legacy loader."""

    root_descriptor = _open_root_directory(root)
    try:
        manifest_raw = _read_regular_file_bounded(
            root_descriptor,
            "manifest.json",
            maximum_bytes=_MAX_LEGACY_MANIFEST_BYTES,
            expected_bytes=None,
            missing_reason="RAW_MANIFEST_READ_FAILED",
        )
        registered_files = _manifest_registered_files(manifest_raw)
        _require_closed_tree(root_descriptor, registered_files)
        captured_files = {
            path: _read_regular_file_bounded(
                root_descriptor,
                path,
                maximum_bytes=_MAX_LEGACY_RAW_FILE_BYTES,
                expected_bytes=byte_size,
                missing_reason="RAW_BUNDLE_REGISTERED_FILE_MISSING",
            )
            for path, byte_size in registered_files
        }
        _require_closed_tree(root_descriptor, registered_files)
    finally:
        os.close(root_descriptor)

    with TemporaryDirectory(prefix="gld-legacy-adapter-") as temporary:
        snapshot_root = Path(temporary) / "bundle"
        snapshot_root.mkdir()
        (snapshot_root / "manifest.json").write_bytes(manifest_raw)
        for relative_path, raw in captured_files.items():
            destination = snapshot_root.joinpath(
                *PurePosixPath(relative_path).parts
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        yield snapshot_root


def _object(value: object, label: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(f"LEGACY_ADAPTER_{label}_INVALID")
    return value


def _objects(value: object, label: str) -> list[dict[str, object]]:
    if type(value) is not list or any(type(item) is not dict for item in value):
        raise ValueError(f"LEGACY_ADAPTER_{label}_INVALID")
    return cast(list[dict[str, object]], value)


def _text(value: object, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"LEGACY_ADAPTER_{label}_INVALID")
    return value


def _integer(value: object, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"LEGACY_ADAPTER_{label}_INVALID")
    return value


def _array(value: object, label: str) -> list[object]:
    if type(value) is not list:
        raise ValueError(f"LEGACY_ADAPTER_{label}_INVALID")
    return value


def _utc_date(value: object) -> str:
    if type(value) is not int or value <= 0:
        raise ValueError("LEGACY_ADAPTER_CONTRACT_TIME_INVALID")
    seconds, remainder = divmod(value, _NANO)
    if remainder:
        raise ValueError("LEGACY_ADAPTER_CONTRACT_TIME_INVALID")
    return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(
        _NEW_YORK
    ).date().isoformat()


def _minute_ordinal(start_utc_ns: object) -> int:
    if type(start_utc_ns) is not int or start_utc_ns <= 0:
        raise ValueError("LEGACY_ADAPTER_MINUTE_TIME_INVALID")
    seconds, remainder = divmod(start_utc_ns, _NANO)
    if remainder:
        raise ValueError("LEGACY_ADAPTER_MINUTE_TIME_INVALID")
    local = datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(_NEW_YORK)
    return local.hour * 60 + local.minute


def _contract_id_from_occ(occ_symbol: str) -> str:
    """Preserve OCC text separately while producing a control-safe map key."""

    return f"OCC:{occ_symbol.replace(' ', '_')}"


def _book(
    value: object,
    *,
    instrument_id: str,
) -> dict[str, object]:
    source = _object(value, "BOOK")
    return {
        "instrument_id": instrument_id,
        "bid_nano_usd": source["bid_nano_usd"],
        "ask_nano_usd": source["ask_nano_usd"],
        "bid_size": source["bid_size"],
        "ask_size": source["ask_size"],
        "tick_nano_usd": source["tick_nano_usd"],
        "event_utc_ns": source["ts_event_ns"],
        "receive_utc_ns": source["ts_recv_ns"],
        "flags": _array(source["flags"], "BOOK_FLAGS"),
    }


def _source_receipt(
    *,
    raw_content_sha256: str,
    effective_from_utc_ns: int,
) -> dict[str, object]:
    return {
        "schema_version": "SOURCE_QUALIFICATION_RECEIPT_V1",
        "receipt_id": "legacy-synthetic-all-v1",
        "provider_id": "LOCAL_LEGACY_SYNTHETIC_ADAPTER",
        "provider_version": "ENTRY_ADAPTER_V1",
        "evidence_kind": "SYNTHETIC_GENERATOR",
        "entitlement_status": "NOT_APPLICABLE_SYNTHETIC",
        "field_coverage_ppm": 1_000_000,
        "complete_universe_supported": True,
        "event_time_semantics": "UTC_NS_EXPLICIT",
        "receive_time_semantics": "UTC_NS_EXPLICIT",
        "atomic_snapshot_supported": True,
        "synchronization_max_skew_ns": _SECOND_NS,
        "effective_from_utc_ns": effective_from_utc_ns,
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
        "evidence_sha256": raw_content_sha256,
    }


def _adapt_legacy_synthetic_bundle(root: Path | str) -> dict[str, object]:
    """Convert one verified legacy fixture to ``EntryFactBundleV1``.

    The returned document is normalized by the new validator.  It is never
    promoted beyond ``SYNTHETIC_ONLY`` and contains no Kelly, carrier, sizing,
    ranking, or decision output.
    """

    bundle = load_raw_bundle(root)
    raw = bundle.as_document()
    calendar_source = _object(raw["calendar"], "CALENDAR")
    daily_source = _object(raw["daily_bars"], "DAILY_BARS")
    minute_source = _object(raw["minute_bars"], "MINUTE_BARS")
    status_source = _object(raw["market_status"], "MARKET_STATUS")
    contracts_source = _object(raw["option_contracts"], "CONTRACTS")
    quotes_source = _object(raw["market_quotes"], "QUOTES")
    pit_source = _object(raw["pit_inputs"], "PIT_INPUTS")
    fee_source = _object(raw["fee_schedule"], "FEES")
    account_source = _object(raw["account_snapshot_a"], "ACCOUNT")
    rule_source = _object(raw["rule_package"], "RULE")

    trading_date = _text(status_source["trading_date"], "TRADING_DATE")
    cutoff_utc_ns = _integer(status_source["as_of_utc_ns"], "CUTOFF")
    receipt_id = "legacy-synthetic-all-v1"

    raw_sessions = _objects(calendar_source["sessions"], "SESSIONS")
    sessions: list[dict[str, object]] = []
    session_ordinal_by_date: dict[str, int] = {}
    for ordinal, source in enumerate(
        sorted(
            raw_sessions,
            key=lambda item: _text(item["trading_date"], "SESSION_DATE"),
        ),
        start=1,
    ):
        session_date = _text(source["trading_date"], "SESSION_DATE")
        if session_date in session_ordinal_by_date:
            raise ValueError("LEGACY_ADAPTER_CALENDAR_DUPLICATE")
        session_ordinal_by_date[session_date] = ordinal
        sessions.append(
            {
                "session_ordinal": ordinal,
                "trading_date": session_date,
                "open_utc_ns": source["regular_open_utc_ns"],
                "close_utc_ns": source["regular_close_utc_ns"],
                "is_full_session": source["session_kind"] == "REGULAR_FULL_DAY",
            }
        )
    if trading_date not in session_ordinal_by_date:
        raise ValueError("LEGACY_ADAPTER_DECISION_SESSION_MISSING")

    calendar = {
        "schema_version": "XNYS_CALENDAR_FACTS_V1",
        "source_receipt_id": receipt_id,
        "timezone": "America/New_York",
        "decision_session_ordinal": session_ordinal_by_date[trading_date],
        "sessions": sessions,
    }

    daily_bars: list[dict[str, object]] = []
    for source in _objects(daily_source["bars"], "DAILY_BARS"):
        session_date = _text(source["trading_date"], "DAILY_DATE")
        daily_bars.append(
            {
                "session_ordinal": session_ordinal_by_date[session_date],
                "trading_date": session_date,
                "open_nano_usd": source["open_nano_usd"],
                "high_nano_usd": source["high_nano_usd"],
                "low_nano_usd": source["low_nano_usd"],
                "close_nano_usd": source["close_nano_usd"],
                "volume": source["volume"],
                "complete": source["complete"],
                "max_event_utc_ns": source["max_event_utc_ns"],
                "max_receive_utc_ns": source["max_receive_utc_ns"],
            }
        )
    daily_document = {
        "schema_version": "GLD_DAILY_OHLCV_FACTS_V1",
        "source_receipt_id": receipt_id,
        "price_scale": "NANO_USD",
        "bars": daily_bars,
    }

    minute_bars: list[dict[str, object]] = []
    for source in _objects(minute_source["bars"], "MINUTE_BARS"):
        start_utc_ns = source["minute_start_utc_ns"]
        minute_bars.append(
            {
                "session_ordinal": session_ordinal_by_date[trading_date],
                "trading_date": trading_date,
                "minute_ending_ordinal": _minute_ordinal(start_utc_ns),
                "start_utc_ns": start_utc_ns,
                "end_utc_ns": source["minute_end_utc_ns"],
                "open_nano_usd": source["open_nano_usd"],
                "high_nano_usd": source["high_nano_usd"],
                "low_nano_usd": source["low_nano_usd"],
                "close_nano_usd": source["close_nano_usd"],
                "volume": source["volume"],
                "complete": source["complete"],
                "max_event_utc_ns": source["max_event_utc_ns"],
                "max_receive_utc_ns": source["max_receive_utc_ns"],
            }
        )
    minute_document = {
        "schema_version": "GLD_MINUTE_OHLCV_FACTS_V1",
        "source_receipt_id": receipt_id,
        "price_scale": "NANO_USD",
        "bars": minute_bars,
    }

    market_status = {
        "schema_version": "MARKET_STATUS_FACTS_V1",
        "source_receipt_id": receipt_id,
        "trading_date": trading_date,
        "as_of_utc_ns": cutoff_utc_ns,
        "market_phase": "REGULAR_TRADING",
        "complete_through_utc_ns": status_source[
            "complete_through_minute_end_utc_ns"
        ],
        "daily_history_complete": status_source["daily_history_complete"],
        "intraday_history_complete": status_source["intraday_history_complete"],
        "option_universe_complete": status_source["option_universe_complete"],
        "quote_snapshot_complete": status_source["quote_snapshot_complete"],
        "calendar_complete": status_source["calendar_complete"],
        "causal_cutoff_enforced": status_source["causal_cutoff_enforced"],
    }

    quote_by_id = {
        _text(item["occ_symbol"], "QUOTE_OCC_SYMBOL"): item
        for item in _objects(quotes_source["option_bbo"], "OPTION_QUOTES")
    }
    option_quotes: list[dict[str, object]] = []
    consumed_occ_symbols: set[str] = set()
    for contract in _objects(contracts_source["contracts"], "CONTRACTS"):
        occ_symbol = _text(contract["occ_symbol"], "CONTRACT_OCC_SYMBOL")
        contract_id = _contract_id_from_occ(occ_symbol)
        try:
            quote = quote_by_id[occ_symbol]
        except KeyError as error:
            raise ValueError("LEGACY_ADAPTER_OPTION_QUOTE_MISSING") from error
        consumed_occ_symbols.add(occ_symbol)
        top = _object(quote["top_of_book"], "OPTION_BOOK")
        option_quotes.append(
            {
                "contract": {
                    "contract_id": contract_id,
                    "occ_symbol": occ_symbol,
                    "underlying": contract["underlying"],
                    "option_type": "CALL",
                    "strike_nano_usd": contract["strike_nano_usd"],
                    "expiry_date": _utc_date(contract["expiry_utc_ns"]),
                    "last_trading_date": _utc_date(
                        contract["last_trading_utc_ns"]
                    ),
                    "expiry_utc_ns": contract["expiry_utc_ns"],
                    "last_trading_utc_ns": contract["last_trading_utc_ns"],
                    "activation_utc_ns": contract["activation_utc_ns"],
                    "multiplier": contract["multiplier"],
                    "deliverable_shares": contract["multiplier"],
                    "deliverable": contract["deliverable"],
                    "currency": contract["currency"],
                    "exchange": "SYNTHETIC_UNSPECIFIED",
                    "tick_nano_usd": top["tick_nano_usd"],
                    "standard_unadjusted": contract["standard_unadjusted"],
                    "exercise_style": contract["exercise_style"],
                },
                "top_of_book": _book(top, instrument_id=contract_id),
            }
        )
    if set(quote_by_id) != consumed_occ_symbols:
        raise ValueError("LEGACY_ADAPTER_OPTION_IDENTITY_MISMATCH")
    option_snapshot = {
        "schema_version": "GLD_CALL_ATOMIC_SNAPSHOT_V1",
        "source_receipt_id": receipt_id,
        "snapshot_id": f"{bundle.bundle_id}-option-snapshot",
        "capture_utc_ns": quotes_source["capture_utc_ns"],
        "window_start_utc_ns": quotes_source["window_start_utc_ns"],
        "window_end_utc_ns": quotes_source["window_end_utc_ns"],
        "universe_complete": status_source["option_universe_complete"],
        "underlying_bbo": _book(
            quotes_source["underlying_bbo"], instrument_id="GLD"
        ),
        "option_quotes": option_quotes,
    }

    pit_inputs = {
        "schema_version": "PIT_MARKET_INPUTS_V1",
        "source_receipt_ids": [receipt_id],
        "as_of_utc_ns": pit_source["as_of_utc_ns"],
        "rate_curve": [
            {
                "tenor_days": 365,
                "zero_rate_ppm": pit_source["risk_free_rate_ppm"],
            }
        ],
        "expense_yield_ppm": pit_source["expense_yield_ppm"],
        "distribution_yield_ppm": 0,
        "borrow_available": True,
        "borrow_rate_ppm": pit_source["borrow_yield_ppm"],
    }
    fee_schedule = {
        "schema_version": "EFFECTIVE_FEE_SCHEDULE_V1",
        "source_receipt_id": receipt_id,
        "effective_from_utc_ns": fee_source["effective_from_utc_ns"],
        "effective_to_utc_ns": None,
        "currency": "USD",
        "broker_schedule_id": "legacy-synthetic-composite-broker-v1",
        "exchange_schedule_id": "legacy-synthetic-composite-exchange-v1",
        "clearing_schedule_id": "legacy-synthetic-composite-clearing-v1",
        "regulatory_schedule_id": "legacy-synthetic-composite-regulatory-v1",
        "long_entry_fee_nano_usd_per_contract": fee_source[
            "long_entry_fee_nano_usd_per_contract"
        ],
        "long_exit_fee_nano_usd_per_contract": fee_source[
            "long_exit_fee_nano_usd_per_contract"
        ],
        "short_entry_fee_nano_usd_per_contract": fee_source[
            "short_entry_fee_nano_usd_per_contract"
        ],
        "short_exit_fee_nano_usd_per_contract": fee_source[
            "short_exit_fee_nano_usd_per_contract"
        ],
    }
    account_snapshot = {
        "schema_version": "ACCOUNT_SNAPSHOT_A_V1",
        "source_receipt_id": receipt_id,
        "as_of_utc_ns": account_source["as_of_utc_ns"],
        "currency": account_source["currency"],
        "net_liquidation_value_nano_usd": account_source[
            "net_liquidation_value_nano_usd"
        ],
        "settled_cash_nano_usd": account_source["settled_cash_nano_usd"],
        "strategy_bankroll_nano_usd": account_source[
            "strategy_bankroll_principal_nano_usd"
        ],
        "strategy_high_watermark_nano_usd": account_source[
            "strategy_high_watermark_nlv_nano_usd"
        ],
        "realized_profit_nano_usd": account_source[
            "cumulative_realized_profit_nano_usd"
        ],
        "realized_loss_nano_usd": account_source[
            "cumulative_realized_loss_nano_usd"
        ],
        "current_gld_delta_exposure_nano_usd": account_source[
            "current_gld_equivalent_delta_notional_nano_usd"
        ],
        "positions": _array(account_source["positions"], "POSITIONS"),
        "open_orders": _array(account_source["open_orders"], "OPEN_ORDERS"),
    }
    rule_package = {
        "schema_version": "GLD_TECHNICAL_RULE_BINDING_V1",
        "package_id": _text(rule_source["rule_package_id"], "RULE_ID"),
        "version": _text(rule_source["schema_version"], "RULE_VERSION"),
        "effective_from_utc_ns": rule_source["effective_from_utc_ns"],
        "cutoff_minute_ending_ordinal": 645,
        "option_window_duration_ns": _MINUTE_NS,
        "max_quote_age_ns": 5 * _SECOND_NS,
        "max_cross_leg_receive_skew_ns": _SECOND_NS,
        "max_account_age_ns": _SECOND_NS,
        "min_quote_size": 1,
        "min_history_sessions": 220,
        "entry_stress_long_ticks": 1,
        "entry_stress_short_ticks": 1,
        "realized_profit_reinvestment_ppm": 500_000,
        "realized_loss_effect_ppm": 1_000_000,
        "minimum_cash_reserve_nlv_ppm": 100_000,
    }
    model_package = {
        "schema_version": "LOCAL_OPTION_MODEL_BINDING_V1",
        "package_id": "gld-crr-local",
        "version": MODEL_SHA256,
        "formula_id": MODEL_ID,
        "coarse_steps": 512,
        "fine_steps": 1024,
        "iv_iterations": 32,
        "rounding_mode": "HALF_EVEN_INTEGER",
        "source_code_sha256": MODEL_SOURCE_ARTIFACT_SHA256,
        "runtime_fingerprint_sha256": RUNTIME_FINGERPRINT_SHA256,
    }
    receipts = [
        _source_receipt(
            raw_content_sha256=bundle.raw_content_sha256,
            effective_from_utc_ns=_integer(
                rule_source["effective_from_utc_ns"], "RULE_EFFECTIVE_TIME"
            ),
        )
    ]
    entry: dict[str, object] = {
        "schema_version": "ENTRY_FACT_BUNDLE_V1",
        "classification": "SYNTHETIC_ONLY",
        "scope": "GLD_ENTRY_FACTS_ONLY",
        "bundle_id": bundle.bundle_id,
        "underlying": "GLD",
        "trading_date": trading_date,
        "cutoff_utc_ns": cutoff_utc_ns,
        "calendar": calendar,
        "daily_bars": daily_document,
        "minute_bars": minute_document,
        "market_status": market_status,
        "option_snapshot": option_snapshot,
        "pit_inputs": pit_inputs,
        "fee_schedule": fee_schedule,
        "account_snapshot_a": account_snapshot,
        "rule_package": rule_package,
        "model_package": model_package,
        "source_qualification_receipts": receipts,
        "content_hashes": {},
    }
    entry["content_hashes"] = {
        f"{domain}_sha256": canonical_json_sha256(entry[domain])
        for domain in _ENTRY_HASH_DOMAINS
    }

    # Import lazily so this adapter remains independently inspectable while
    # contracts are being versioned in parallel.
    from .validation import validate_entry_bundle

    return validate_entry_bundle(entry).normalized_document


def adapt_legacy_synthetic_bundle(root: Path | str) -> dict[str, object]:
    """Fail closed with one stable adapter error for malformed nested input."""

    try:
        with _safe_legacy_bundle_snapshot(root) as safe_root:
            return _adapt_legacy_synthetic_bundle(safe_root)
    except ValueError:
        raise
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError("LEGACY_ADAPTER_SCHEMA_INVALID") from error


__all__ = ["adapt_legacy_synthetic_bundle"]
