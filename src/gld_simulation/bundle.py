"""Closed, content-addressed loader for simulation-only raw GLD bundles."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Mapping

from gld_research_core.p1_process_supervisor import P1_MAX_REQUEST_BYTES

from .canonical import (
    canonical_json_bytes,
    canonical_json_sha256,
    parse_canonical_json,
)
from .errors import RawBundleError


MANIFEST_SCHEMA_VERSION = "GLD_SIMULATION_RAW_BUNDLE_MANIFEST_V1"
SYNTHETIC_CLASSIFICATION = "SYNTHETIC_FIXTURE_ONLY"
MAX_MANIFEST_BYTES = 1_000_000
MAX_RAW_FILE_BYTES = 8_000_000
MAX_RAW_BUNDLE_BYTES = 32_000_000
# The supervisor's request ceiling is 1 MiB.  Raw documents receive the full
# budget minus 64 KiB for their enclosing request keys, hashes, and the trusted
# native-manifest path.  The final supervisor still validates the whole frame.
SUPERVISOR_REQUEST_ENVELOPE_BYTES = 65_536
MAX_RAW_DOCUMENTS_CANONICAL_BYTES = (
    P1_MAX_REQUEST_BYTES - SUPERVISOR_REQUEST_ENVELOPE_BYTES
)

SUPPORTED_RAW_DOCUMENT_SCHEMAS: Mapping[str, str] = MappingProxyType(
    {
        "rule_package": "SIM_GLD_RULE_PACKAGE_V1",
        "calendar": "SIM_XNYS_CALENDAR_V1",
        "daily_bars": "SIM_GLD_DAILY_BARS_V1",
        "minute_bars": "SIM_GLD_MINUTE_BARS_V1",
        "market_status": "SIM_MARKET_STATUS_V1",
        "option_contracts": "SIM_GLD_OPTION_CONTRACTS_V1",
        "market_quotes": "SIM_GLD_MARKET_QUOTES_V1",
        "pit_inputs": "SIM_PIT_INPUTS_V1",
        "fee_schedule": "SIM_FEE_SCHEDULE_V1",
        "account_snapshot_a": "SIM_ACCOUNT_SNAPSHOT_A_V1",
        "frozen_kelly_receipt": "SIM_FROZEN_KELLY_RECEIPT_V1",
    }
)
REQUIRED_LOGICAL_KEYS = tuple(SUPPORTED_RAW_DOCUMENT_SCHEMAS)

RAW_DOCUMENT_TOP_LEVEL_KEYS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "rule_package": frozenset(
            {
                "schema_version",
                "rule_package_id",
                "classification",
                "effective_from_utc_ns",
                "underlying",
                "gate_a",
                "lc0",
                "bcs",
                "risk",
                "exit_plan",
            }
        ),
        "calendar": frozenset(
            {
                "schema_version",
                "classification",
                "authority_id",
                "calendar_version",
                "timezone",
                "sessions",
            }
        ),
        "daily_bars": frozenset(
            {
                "schema_version",
                "classification",
                "underlying",
                "price_scale",
                "bars",
            }
        ),
        "minute_bars": frozenset(
            {
                "schema_version",
                "classification",
                "underlying",
                "trading_date",
                "price_scale",
                "bars",
            }
        ),
        "market_status": frozenset(
            {
                "schema_version",
                "classification",
                "trading_date",
                "as_of_utc_ns",
                "market_phase",
                "complete_through_minute_end_utc_ns",
                "daily_history_complete",
                "intraday_history_complete",
                "option_universe_complete",
                "quote_snapshot_complete",
                "calendar_complete",
                "causal_cutoff_enforced",
            }
        ),
        "option_contracts": frozenset(
            {
                "schema_version",
                "classification",
                "underlying",
                "source",
                "extracted_economic_values_sha256",
                "contracts",
            }
        ),
        "market_quotes": frozenset(
            {
                "schema_version",
                "classification",
                "source",
                "source_version",
                "extracted_economic_values_sha256",
                "capture_utc_ns",
                "window_start_utc_ns",
                "window_end_utc_ns",
                "underlying_bbo",
                "option_bbo",
            }
        ),
        "pit_inputs": frozenset(
            {
                "schema_version",
                "classification",
                "as_of_utc_ns",
                "risk_free_rate_ppm",
                "borrow_yield_ppm",
                "expense_yield_ppm",
                "rate_curve_sha256",
                "borrow_assumption_sha256",
                "distribution_assumption_sha256",
            }
        ),
        "fee_schedule": frozenset(
            {
                "schema_version",
                "classification",
                "effective_from_utc_ns",
                "long_entry_fee_nano_usd_per_contract",
                "long_exit_fee_nano_usd_per_contract",
                "short_entry_fee_nano_usd_per_contract",
                "short_exit_fee_nano_usd_per_contract",
                "source_receipt_sha256",
            }
        ),
        "account_snapshot_a": frozenset(
            {
                "schema_version",
                "classification",
                "source",
                "as_of_utc_ns",
                "currency",
                "net_liquidation_value_nano_usd",
                "settled_cash_nano_usd",
                "strategy_bankroll_principal_nano_usd",
                "strategy_high_watermark_nlv_nano_usd",
                "cumulative_realized_profit_nano_usd",
                "cumulative_realized_loss_nano_usd",
                "unrealized_profit_nano_usd",
                "current_gld_equivalent_delta_notional_nano_usd",
                "sticky_drawdown_lock_active",
                "positions",
                "open_orders",
            }
        ),
        "frozen_kelly_receipt": frozenset(
            {
                "schema_version",
                "classification",
                "receipt_id",
                "frozen_at_utc_ns",
                "method",
                "sample_episode_count",
                "sample_end_trading_date",
                "sample_input_sha256",
                "robust_full_kelly_ppm",
                "daily_reestimation_allowed",
                "strategy_rule_package_id",
                "applicable_carriers",
                "return_distribution_id",
            }
        ),
    }
)

_MANIFEST_KEYS = {"schema_version", "bundle_id", "classification", "files"}
_FILE_ENTRY_KEYS = {"logical_key", "schema", "path", "byte_size", "sha256"}
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_ASCII_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_FORBIDDEN_NORMALIZED_KEYS = {
    "signalstate",
    "winner",
    "quantity",
    "decisionresult",
    "expectedresult",
}


@dataclass(frozen=True, slots=True)
class RawFile:
    logical_key: str
    schema: str
    path: str
    byte_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class RawBundle:
    """A fully verified bundle; no later read can escape the closed file set."""

    root: Path
    bundle_id: str
    classification: str
    files: Mapping[str, RawFile]
    manifest: Mapping[str, object]
    manifest_sha256: str
    raw_content_sha256: str
    _documents: Mapping[str, object]

    def read_json(self, logical_key: str) -> object:
        """Return the already-verified JSON document for one logical file."""

        try:
            return deepcopy(self._documents[logical_key])
        except KeyError as exc:
            raise RawBundleError("RAW_LOGICAL_KEY_UNKNOWN", logical_key) from exc

    def as_document(self) -> dict[str, object]:
        """Assemble all raw facts under their stable logical keys."""

        return {
            key: deepcopy(self._documents[key]) for key in REQUIRED_LOGICAL_KEYS
        }


def _require_dict(value: object, reason: str) -> dict[str, object]:
    if type(value) is not dict:
        raise RawBundleError(reason)
    return value


def _require_ascii_id(value: object, reason: str) -> str:
    if type(value) is not str or _ASCII_ID_RE.fullmatch(value) is None:
        raise RawBundleError(reason)
    return value


def _validate_relative_path(value: object) -> str:
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


def _normalized_field_name(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _reject_derived_answers(value: object, *, logical_key: str) -> None:
    if type(value) is dict:
        for key, item in value.items():
            normalized = _normalized_field_name(key)
            if (
                normalized in _FORBIDDEN_NORMALIZED_KEYS
                or "winner" in normalized
                or "quantity" in normalized
                or "decisionresult" in normalized
                or "signalstate" in normalized
                or "expectedresult" in normalized
            ):
                raise RawBundleError(
                    "RAW_DERIVED_ANSWER_FIELD_FORBIDDEN",
                    f"{logical_key}:{key}",
                )
            _reject_derived_answers(item, logical_key=logical_key)
    elif type(value) is list:
        for item in value:
            _reject_derived_answers(item, logical_key=logical_key)


def _scan_bundle_tree(root: Path) -> set[str]:
    files: set[str] = set()
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in tuple(directories) + tuple(names):
            candidate = current_path / name
            if candidate.is_symlink():
                raise RawBundleError(
                    "RAW_BUNDLE_SYMLINK_FORBIDDEN",
                    candidate.relative_to(root).as_posix(),
                )
        for name in names:
            candidate = current_path / name
            if not candidate.is_file():
                raise RawBundleError(
                    "RAW_BUNDLE_NONREGULAR_FILE_FORBIDDEN",
                    candidate.relative_to(root).as_posix(),
                )
            files.add(candidate.relative_to(root).as_posix())
    return files


def _parse_file_entry(value: object) -> RawFile:
    entry = _require_dict(value, "RAW_MANIFEST_FILE_ENTRY_INVALID")
    if set(entry) != _FILE_ENTRY_KEYS:
        raise RawBundleError("RAW_MANIFEST_FILE_ENTRY_SCHEMA_INVALID")
    logical_key = _require_ascii_id(
        entry["logical_key"], "RAW_MANIFEST_LOGICAL_KEY_INVALID"
    )
    schema = _require_ascii_id(entry["schema"], "RAW_MANIFEST_FILE_SCHEMA_INVALID")
    expected_schema = SUPPORTED_RAW_DOCUMENT_SCHEMAS.get(logical_key)
    if expected_schema is not None and schema != expected_schema:
        raise RawBundleError(
            "RAW_MANIFEST_FILE_SCHEMA_UNSUPPORTED", logical_key
        )
    path = _validate_relative_path(entry["path"])
    byte_size = entry["byte_size"]
    if type(byte_size) is not int or not 1 <= byte_size <= MAX_RAW_FILE_BYTES:
        raise RawBundleError("RAW_MANIFEST_BYTE_SIZE_INVALID", path)
    digest = entry["sha256"]
    if type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
        raise RawBundleError("RAW_MANIFEST_SHA256_INVALID", path)
    return RawFile(
        logical_key=logical_key,
        schema=schema,
        path=path,
        byte_size=byte_size,
        sha256=digest,
    )


def load_raw_bundle(root: Path | str) -> RawBundle:
    """Verify and load one closed simulation fixture directory.

    Integrity failures are distinct from missing business facts: JSON ``null``
    is allowed inside a valid raw document and is interpreted by later gates.
    """

    supplied_root = Path(root)
    if supplied_root.is_symlink():
        raise RawBundleError("RAW_BUNDLE_SYMLINK_FORBIDDEN", str(supplied_root))
    if not supplied_root.is_dir():
        raise RawBundleError("RAW_BUNDLE_ROOT_INVALID")
    resolved_root = supplied_root.resolve(strict=True)
    manifest_path = resolved_root / "manifest.json"
    if manifest_path.is_symlink():
        raise RawBundleError("RAW_BUNDLE_SYMLINK_FORBIDDEN", "manifest.json")
    try:
        manifest_raw = manifest_path.read_bytes()
    except OSError as exc:
        raise RawBundleError("RAW_MANIFEST_READ_FAILED") from exc
    if not 1 <= len(manifest_raw) <= MAX_MANIFEST_BYTES:
        raise RawBundleError("RAW_MANIFEST_SIZE_INVALID")
    manifest_value = parse_canonical_json(manifest_raw, source="manifest.json")
    manifest = _require_dict(manifest_value, "RAW_MANIFEST_SCHEMA_INVALID")
    if set(manifest) != _MANIFEST_KEYS:
        raise RawBundleError("RAW_MANIFEST_SCHEMA_INVALID")
    if manifest["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise RawBundleError("RAW_MANIFEST_VERSION_UNSUPPORTED")
    bundle_id = _require_ascii_id(manifest["bundle_id"], "RAW_BUNDLE_ID_INVALID")
    if manifest["classification"] != SYNTHETIC_CLASSIFICATION:
        raise RawBundleError("RAW_BUNDLE_CLASSIFICATION_INVALID")
    raw_entries = manifest["files"]
    if type(raw_entries) is not list or not raw_entries:
        raise RawBundleError("RAW_MANIFEST_FILES_INVALID")

    by_key: dict[str, RawFile] = {}
    paths: set[str] = set()
    for raw_entry in raw_entries:
        entry = _parse_file_entry(raw_entry)
        if entry.logical_key in by_key:
            raise RawBundleError("RAW_MANIFEST_LOGICAL_KEY_DUPLICATE")
        if entry.path in paths:
            raise RawBundleError("RAW_MANIFEST_PATH_DUPLICATE")
        by_key[entry.logical_key] = entry
        paths.add(entry.path)
    if set(by_key) != set(REQUIRED_LOGICAL_KEYS):
        raise RawBundleError("RAW_MANIFEST_LOGICAL_KEYS_INCOMPLETE")
    if sum(entry.byte_size for entry in by_key.values()) > MAX_RAW_BUNDLE_BYTES:
        raise RawBundleError("RAW_BUNDLE_SIZE_LIMIT_EXCEEDED")

    actual_files = _scan_bundle_tree(resolved_root)
    expected_files = paths | {"manifest.json"}
    unexpected = actual_files - expected_files
    if unexpected:
        raise RawBundleError(
            "RAW_BUNDLE_UNREGISTERED_FILE", sorted(unexpected)[0]
        )
    missing = expected_files - actual_files
    if missing:
        raise RawBundleError("RAW_BUNDLE_REGISTERED_FILE_MISSING", sorted(missing)[0])

    documents: dict[str, object] = {}
    for logical_key in REQUIRED_LOGICAL_KEYS:
        entry = by_key[logical_key]
        path = resolved_root.joinpath(*PurePosixPath(entry.path).parts)
        if path.is_symlink() or not path.is_file():
            raise RawBundleError("RAW_BUNDLE_SYMLINK_FORBIDDEN", entry.path)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise RawBundleError("RAW_FILE_READ_FAILED", entry.path) from exc
        if len(raw) != entry.byte_size:
            raise RawBundleError("RAW_FILE_SIZE_MISMATCH", entry.path)
        if sha256(raw).hexdigest() != entry.sha256:
            raise RawBundleError("RAW_FILE_SHA256_MISMATCH", entry.path)
        document = parse_canonical_json(raw, source=entry.path)
        if type(document) is not dict:
            raise RawBundleError("RAW_FILE_DOCUMENT_SCHEMA_INVALID", entry.path)
        if document.get("schema_version") != entry.schema:
            raise RawBundleError("RAW_FILE_SCHEMA_MISMATCH", entry.path)
        if document.get("classification") != SYNTHETIC_CLASSIFICATION:
            raise RawBundleError("RAW_FILE_CLASSIFICATION_INVALID", entry.path)
        if set(document) != RAW_DOCUMENT_TOP_LEVEL_KEYS[logical_key]:
            raise RawBundleError("RAW_FILE_DOCUMENT_KEYS_INVALID", entry.path)
        _reject_derived_answers(document, logical_key=logical_key)
        documents[logical_key] = document

    raw_documents = {
        key: documents[key] for key in REQUIRED_LOGICAL_KEYS
    }
    if (
        len(canonical_json_bytes(raw_documents))
        > MAX_RAW_DOCUMENTS_CANONICAL_BYTES
    ):
        raise RawBundleError(
            "RAW_DOCUMENTS_SUPERVISOR_SIZE_LIMIT_EXCEEDED"
        )

    content_binding = [
        {
            "path": entry.path,
            "byte_size": entry.byte_size,
            "sha256": entry.sha256,
        }
        for entry in sorted(by_key.values(), key=lambda item: item.path)
    ]
    return RawBundle(
        root=resolved_root,
        bundle_id=bundle_id,
        classification=SYNTHETIC_CLASSIFICATION,
        files=MappingProxyType(by_key),
        manifest=MappingProxyType(manifest),
        manifest_sha256=canonical_json_sha256(manifest),
        raw_content_sha256=canonical_json_sha256(content_binding),
        _documents=MappingProxyType(documents),
    )


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "MAX_RAW_DOCUMENTS_CANONICAL_BYTES",
    "RAW_DOCUMENT_TOP_LEVEL_KEYS",
    "REQUIRED_LOGICAL_KEYS",
    "SUPPORTED_RAW_DOCUMENT_SCHEMAS",
    "SUPERVISOR_REQUEST_ENVELOPE_BYTES",
    "RawBundle",
    "RawFile",
    "SYNTHETIC_CLASSIFICATION",
    "load_raw_bundle",
]
