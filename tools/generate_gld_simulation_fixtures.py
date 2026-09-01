#!/usr/bin/env python3
"""Generate closed raw GLD simulation bundles from synthetic economics only.

The U64 corpus is an offline build-time donor for contract definitions, BBOs,
PIT assumptions, and fees. Its signal, selector bindings, declared hashes,
goldens, and expected terminals are intentionally never copied.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import ctypes
from datetime import date, datetime, time, timedelta, timezone
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Iterable
from zoneinfo import ZoneInfo

from gld_simulation.bundle import (
    MANIFEST_SCHEMA_VERSION,
    SUPPORTED_RAW_DOCUMENT_SCHEMAS,
    SYNTHETIC_CLASSIFICATION,
    load_raw_bundle,
)
from gld_simulation.canonical import canonical_json_bytes, canonical_json_sha256


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_U64 = REPOSITORY_ROOT / "benchmarks" / "fixtures" / "u64_v0.1.json"
DEFAULT_OUTPUT = REPOSITORY_ROOT / "fixtures" / "gld_simulation" / "v1"
NEW_YORK = ZoneInfo("America/New_York")
UTC = timezone.utc
H0 = date(2026, 8, 28)
MARKET_CUTOFF_UTC_NS = 1_787_928_300_000_000_000

US_MARKET_CLOSURES = {
    date(2025, 11, 27),
    date(2025, 12, 25),
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
}

FILE_LAYOUT = {
    "rule_package": (
        "control/rule-package.json",
        SUPPORTED_RAW_DOCUMENT_SCHEMAS["rule_package"],
    ),
    "calendar": (
        "market/xnys-calendar.json",
        SUPPORTED_RAW_DOCUMENT_SCHEMAS["calendar"],
    ),
    "daily_bars": (
        "market/gld-daily-bars.json",
        SUPPORTED_RAW_DOCUMENT_SCHEMAS["daily_bars"],
    ),
    "minute_bars": (
        "market/gld-minute-bars.json",
        SUPPORTED_RAW_DOCUMENT_SCHEMAS["minute_bars"],
    ),
    "market_status": (
        "market/market-status.json",
        SUPPORTED_RAW_DOCUMENT_SCHEMAS["market_status"],
    ),
    "option_contracts": (
        "market/option-contracts.json",
        SUPPORTED_RAW_DOCUMENT_SCHEMAS["option_contracts"],
    ),
    "market_quotes": (
        "market/market-quotes.json",
        SUPPORTED_RAW_DOCUMENT_SCHEMAS["market_quotes"],
    ),
    "pit_inputs": (
        "assumptions/pit-inputs.json",
        SUPPORTED_RAW_DOCUMENT_SCHEMAS["pit_inputs"],
    ),
    "fee_schedule": (
        "economics/fee-schedule.json",
        SUPPORTED_RAW_DOCUMENT_SCHEMAS["fee_schedule"],
    ),
    "account_snapshot_a": (
        "account/snapshot-a.json",
        SUPPORTED_RAW_DOCUMENT_SCHEMAS["account_snapshot_a"],
    ),
    "frozen_kelly_receipt": (
        "assumptions/frozen-kelly-receipt.json",
        SUPPORTED_RAW_DOCUMENT_SCHEMAS["frozen_kelly_receipt"],
    ),
}


def _utc_ns(value: datetime) -> int:
    return int(value.timestamp()) * 1_000_000_000


def _market_datetime(trading_date: date, value: time) -> datetime:
    return datetime.combine(trading_date, value, tzinfo=NEW_YORK).astimezone(UTC)


def _is_session(trading_date: date) -> bool:
    return trading_date.weekday() < 5 and trading_date not in US_MARKET_CLOSURES


def _previous_sessions(end: date, count: int) -> list[date]:
    sessions: list[date] = []
    cursor = end - timedelta(days=1)
    while len(sessions) < count:
        if _is_session(cursor):
            sessions.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(sessions))


def _following_sessions(start: date, count: int) -> list[date]:
    sessions: list[date] = []
    cursor = start
    while len(sessions) < count:
        if _is_session(cursor):
            sessions.append(cursor)
        cursor += timedelta(days=1)
    return sessions


def _rule_package() -> dict[str, object]:
    return {
        "schema_version": "SIM_GLD_RULE_PACKAGE_V1",
        "rule_package_id": "SIM_GLD_PIPELINE_V1",
        "classification": SYNTHETIC_CLASSIFICATION,
        "effective_from_utc_ns": MARKET_CUTOFF_UTC_NS,
        "underlying": "GLD",
        "gate_a": {
            "rule_id": "SIM_A1_RANGE_HOLD_V1",
            "sma_fast_sessions": 50,
            "sma_slow_sessions": 200,
            "sma_fast_slope_lookback_sessions": 20,
            "breakout_lookback_complete_sessions": 20,
            "confirmation_lookback_complete_minutes": 15,
            "confirmation_minimum_closes_above": 10,
            "decision_minute_local": "10:44",
        },
        "lc0": {
            "selector_id": "SIM_LC0_050_V1",
            "target_delta_ppm": 500_000,
            "minimum_delta_ppm": 450_000,
            "maximum_delta_ppm": 550_000,
            "minimum_calendar_days_after_h20": 30,
            "required_right": "C",
            "required_exercise_style": "AMERICAN",
            "required_multiplier": 100,
            "require_standard_unadjusted": True,
            "tie_break_order": [
                "ABS_DELTA_DISTANCE_ASC",
                "STRIKE_NANO_USD_DESC",
                "OCC_SYMBOL_ASCII_ASC",
            ],
            "coarse_fine_agreement_required": True,
            "second_best_fallback_allowed": False,
        },
        "bcs": {
            "selector_id": "SIM_BCS_025_V1",
            "short_target_delta_ppm": 250_000,
            "short_minimum_delta_ppm": 200_000,
            "short_maximum_delta_ppm": 300_000,
            "leg_ratio": "1:1",
            "same_expiry_required": True,
            "roll_active": False,
            "second_best_fallback_allowed": False,
        },
        "risk": {
            "policy_id": "SIM_HALF_KELLY_RISK_V1",
            "candidate_terminal_policy": "BOTH_LC0_AND_BCS_MUST_PASS",
            "entry_stress_long_ticks": 1,
            "entry_stress_short_ticks": 1,
            "include_exit_fees_in_max_loss": True,
            "kelly_fraction_ppm": 500_000,
            "realized_positive_profit_reinvestment_ppm": 500_000,
            "realized_loss_effect_ppm": 1_000_000,
            "unrealized_profit_expands_bankroll": False,
            "minimum_post_trade_settled_cash_nlv_ppm": 100_000,
            "maximum_gld_equivalent_delta_notional_bankroll_ppm": 1_500_000,
            "planned_loss_equals_maximum_loss": True,
            "drawdown_bands": [
                {
                    "minimum_ppm": 0,
                    "maximum_ppm": 50_000,
                    "interval": "[0,50000]",
                    "risk_multiplier_ppm": 1_000_000,
                    "action": "NORMAL",
                },
                {
                    "minimum_ppm": 50_000,
                    "maximum_ppm": 100_000,
                    "interval": "(50000,100000]",
                    "risk_multiplier_ppm": 800_000,
                    "action": "REDUCED",
                },
                {
                    "minimum_ppm": 100_000,
                    "maximum_ppm": 150_000,
                    "interval": "(100000,150000)",
                    "risk_multiplier_ppm": 400_000,
                    "action": "DEFENSIVE",
                },
                {
                    "minimum_ppm": 150_000,
                    "maximum_ppm": 200_000,
                    "interval": "[150000,200000)",
                    "risk_multiplier_ppm": 0,
                    "action": "NO_NEW_ENTRY",
                },
                {
                    "minimum_ppm": 200_000,
                    "maximum_ppm": 300_000,
                    "interval": "[200000,300000)",
                    "risk_multiplier_ppm": 0,
                    "action": "EXIT_MANAGED",
                },
                {
                    "minimum_ppm": 300_000,
                    "maximum_ppm": None,
                    "interval": "[300000,INF)",
                    "risk_multiplier_ppm": 0,
                    "action": "STICKY_LOCK",
                },
            ],
        },
        "exit_plan": {
            "fixed_take_profit": False,
            "gate_a_invalidation_active": True,
            "expiry_safety_active": True,
            "latest_full_exit_session": "H20",
            "same_expiry_roll_up": "NOT_ACTIVE_IN_SIM_V1",
        },
    }


def _calendar() -> dict[str, object]:
    historical = _previous_sessions(H0, 220)
    forward = _following_sessions(H0, 21)
    sessions = []
    for trading_date in historical + forward:
        sessions.append(
            {
                "trading_date": trading_date.isoformat(),
                "regular_open_utc_ns": _utc_ns(
                    _market_datetime(trading_date, time(9, 30))
                ),
                "regular_close_utc_ns": _utc_ns(
                    _market_datetime(trading_date, time(16, 0))
                ),
                "session_kind": "REGULAR_FULL_DAY",
            }
        )
    return {
        "schema_version": "SIM_XNYS_CALENDAR_V1",
        "classification": SYNTHETIC_CLASSIFICATION,
        "authority_id": "XNYS_SYNTHETIC_CALENDAR",
        "calendar_version": "2026-08-28-sim-v1",
        "timezone": "America/New_York",
        "sessions": sessions,
    }


def _daily_bars() -> dict[str, object]:
    sessions = _previous_sessions(H0, 220)
    bars = []
    for index, trading_date in enumerate(sessions):
        close = 177_100_000_000 + index * 100_000_000
        close_utc = _market_datetime(trading_date, time(16, 0))
        bars.append(
            {
                "trading_date": trading_date.isoformat(),
                "open_nano_usd": close - 150_000_000,
                "high_nano_usd": close + 400_000_000,
                "low_nano_usd": close - 500_000_000,
                "close_nano_usd": close,
                "volume": 7_000_000 + index * 1_000,
                "complete": True,
                "max_event_utc_ns": _utc_ns(close_utc) - 2_000_000_000,
                "max_receive_utc_ns": _utc_ns(close_utc) - 1_000_000_000,
            }
        )
    return {
        "schema_version": "SIM_GLD_DAILY_BARS_V1",
        "classification": SYNTHETIC_CLASSIFICATION,
        "underlying": "GLD",
        "price_scale": "NANO_USD",
        "bars": bars,
    }


def _minute_bars(*, confirmation_holds: bool) -> dict[str, object]:
    start = _market_datetime(H0, time(9, 30))
    bars = []
    for index in range(75):
        minute_start = start + timedelta(minutes=index)
        minute_end = minute_start + timedelta(minutes=1)
        if index < 60:
            close = 199_150_000_000 + index * 4_000_000
        elif confirmation_holds:
            close = 199_650_000_000 + (index % 3) * 20_000_000
        elif index < 66:
            close = 199_650_000_000
        else:
            close = 199_250_000_000
        bars.append(
            {
                "minute_start_utc_ns": _utc_ns(minute_start),
                "minute_end_utc_ns": _utc_ns(minute_end),
                "open_nano_usd": close - 20_000_000,
                "high_nano_usd": close + 30_000_000,
                "low_nano_usd": close - 40_000_000,
                "close_nano_usd": close,
                "volume": 30_000 + index * 100,
                "complete": True,
                "max_event_utc_ns": _utc_ns(minute_end) - 2_000_000_000,
                "max_receive_utc_ns": _utc_ns(minute_end) - 1_000_000_000,
            }
        )
    return {
        "schema_version": "SIM_GLD_MINUTE_BARS_V1",
        "classification": SYNTHETIC_CLASSIFICATION,
        "underlying": "GLD",
        "trading_date": H0.isoformat(),
        "price_scale": "NANO_USD",
        "bars": bars,
    }


def _market_status() -> dict[str, object]:
    return {
        "schema_version": "SIM_MARKET_STATUS_V1",
        "classification": SYNTHETIC_CLASSIFICATION,
        "trading_date": H0.isoformat(),
        "as_of_utc_ns": MARKET_CUTOFF_UTC_NS,
        "market_phase": "REGULAR_OPEN",
        "complete_through_minute_end_utc_ns": MARKET_CUTOFF_UTC_NS,
        "daily_history_complete": True,
        "intraday_history_complete": True,
        "option_universe_complete": True,
        "quote_snapshot_complete": True,
        "calendar_complete": True,
        "causal_cutoff_enforced": True,
    }


def _account(*, settled_cash_available: bool) -> dict[str, object]:
    return {
        "schema_version": "SIM_ACCOUNT_SNAPSHOT_A_V1",
        "classification": SYNTHETIC_CLASSIFICATION,
        "source": "SYNTHETIC_ACCOUNT",
        "as_of_utc_ns": MARKET_CUTOFF_UTC_NS,
        "currency": "USD",
        "net_liquidation_value_nano_usd": 250_000_000_000_000,
        "settled_cash_nano_usd": (
            225_000_000_000_000 if settled_cash_available else None
        ),
        "strategy_bankroll_principal_nano_usd": 200_000_000_000_000,
        "strategy_high_watermark_nlv_nano_usd": 260_000_000_000_000,
        "cumulative_realized_profit_nano_usd": 20_000_000_000_000,
        "cumulative_realized_loss_nano_usd": 5_000_000_000_000,
        "unrealized_profit_nano_usd": 5_000_000_000_000,
        "current_gld_equivalent_delta_notional_nano_usd": 0,
        "sticky_drawdown_lock_active": False,
        "positions": [],
        "open_orders": [],
    }


def _frozen_kelly_receipt() -> dict[str, object]:
    return {
        "schema_version": "SIM_FROZEN_KELLY_RECEIPT_V1",
        "classification": SYNTHETIC_CLASSIFICATION,
        "receipt_id": "SIM_GLD_ROBUST_FULL_KELLY_20260828_V1",
        "frozen_at_utc_ns": MARKET_CUTOFF_UTC_NS - 86_400_000_000_000,
        "method": "SYNTHETIC_ROBUST_LOWER_BOUND",
        "sample_episode_count": 120,
        "sample_end_trading_date": "2026-08-27",
        "sample_input_sha256": "4d37d86049f44bb9203c21573ef19b3a2ae14b04a85145882374d2632ccca80b",
        "robust_full_kelly_ppm": 100_000,
        "daily_reestimation_allowed": False,
        "strategy_rule_package_id": "SIM_GLD_PIPELINE_V1",
        "applicable_carriers": ["LC0", "BCS0"],
        "return_distribution_id": "SIM_DUAL_CARRIER_CONSERVATIVE_V1",
    }


def _extract_u64_economics(u64_path: Path) -> dict[str, object]:
    source = json.loads(u64_path.read_bytes())
    snapshot = source["selected_snapshot"]
    option_quotes = snapshot["option_quotes"]
    if len(option_quotes) != 64:
        raise ValueError("U64 must contain exactly 64 option quote economics")
    contracts = [deepcopy(item["contract"]) for item in option_quotes]
    quotes = [
        {
            "occ_symbol": item["contract"]["occ_symbol"],
            "top_of_book": deepcopy(item["top_of_book"]),
        }
        for item in option_quotes
    ]
    extracted_binding = {
        "underlying_top": snapshot["underlying_top"],
        "contracts": contracts,
        "quotes": quotes,
        "pit_inputs": source["pit_inputs"],
        "fee_schedule": source["fee_schedule"],
    }
    extraction_hash = canonical_json_sha256(extracted_binding)
    return {
        "option_contracts": {
            "schema_version": "SIM_GLD_OPTION_CONTRACTS_V1",
            "classification": SYNTHETIC_CLASSIFICATION,
            "underlying": "GLD",
            "source": "OFFLINE_SYNTHETIC_ECONOMIC_EXTRACTION",
            "extracted_economic_values_sha256": extraction_hash,
            "contracts": contracts,
        },
        "market_quotes": {
            "schema_version": "SIM_GLD_MARKET_QUOTES_V1",
            "classification": SYNTHETIC_CLASSIFICATION,
            "source": "SYNTHETIC_NOT_ENTITLED",
            "source_version": "sim-economic-extraction-v1",
            "extracted_economic_values_sha256": extraction_hash,
            "capture_utc_ns": snapshot["capture_utc_ns"],
            "window_start_utc_ns": snapshot["window_start_utc_ns"],
            "window_end_utc_ns": snapshot["window_end_utc_ns"],
            "underlying_bbo": deepcopy(snapshot["underlying_top"]),
            "option_bbo": quotes,
        },
        "pit_inputs": {
            "schema_version": "SIM_PIT_INPUTS_V1",
            "classification": SYNTHETIC_CLASSIFICATION,
            **deepcopy(source["pit_inputs"]),
        },
        "fee_schedule": {
            "schema_version": "SIM_FEE_SCHEDULE_V1",
            "classification": SYNTHETIC_CLASSIFICATION,
            **deepcopy(source["fee_schedule"]),
        },
    }


def _forbidden_key(value: str) -> bool:
    normalized = "".join(character.lower() for character in value if character.isalnum())
    return normalized in {
        "signalstate",
        "winner",
        "quantity",
        "decisionresult",
        "expectedresult",
    } or any(
        marker in normalized
        for marker in (
            "winner",
            "quantity",
            "decisionresult",
            "signalstate",
            "expectedresult",
        )
    )


def _assert_answer_free(value: object, *, path: str = "$") -> None:
    if type(value) is dict:
        for key, item in value.items():
            if _forbidden_key(key):
                raise ValueError(f"derived answer field forbidden at {path}.{key}")
            _assert_answer_free(item, path=f"{path}.{key}")
    elif type(value) is list:
        for index, item in enumerate(value):
            _assert_answer_free(item, path=f"{path}[{index}]")


def _write_document(path: Path, value: object) -> tuple[int, str]:
    _assert_answer_free(value)
    encoded = canonical_json_bytes(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return len(encoded), sha256(encoded).hexdigest()


def _generate_scenario(
    root: Path,
    *,
    bundle_id: str,
    documents: dict[str, object],
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    manifest_files = []
    for logical_key, (relative_path, schema) in FILE_LAYOUT.items():
        value = documents[logical_key]
        byte_size, digest = _write_document(root / relative_path, value)
        manifest_files.append(
            {
                "logical_key": logical_key,
                "schema": schema,
                "path": relative_path,
                "byte_size": byte_size,
                "sha256": digest,
            }
        )
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "classification": SYNTHETIC_CLASSIFICATION,
        "files": manifest_files,
    }
    _write_document(root / "manifest.json", manifest)


def _publish_directory_no_clobber(source: Path, destination: Path) -> None:
    """Atomically publish one directory without replacing any destination."""

    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        rename_exclusive = getattr(libc, "renamex_np", None)
        if rename_exclusive is None:
            raise OSError(errno.ENOTSUP, "exclusive directory rename unavailable")
        rename_exclusive.argtypes = (
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(source_bytes, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        rename_no_replace = getattr(libc, "renameat2", None)
        if rename_no_replace is None:
            raise OSError(errno.ENOTSUP, "no-replace directory rename unavailable")
        rename_no_replace.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename_no_replace.restype = ctypes.c_int
        result = rename_no_replace(
            -100,
            source_bytes,
            -100,
            destination_bytes,
            0x00000001,
        )
    else:
        raise OSError(errno.ENOTSUP, "no-clobber directory publish unavailable")
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(
            error_number,
            os.strerror(error_number),
            os.fspath(destination),
        )


def generate(u64_path: Path, output_root: Path) -> None:
    output_root = output_root.absolute()
    output_parent = output_root.parent
    if os.path.lexists(output_root):
        raise FileExistsError(
            errno.EEXIST,
            "refusing to replace existing fixture output",
            os.fspath(output_root),
        )
    if output_parent.is_symlink():
        raise ValueError("fixture output parent must not be a symlink")
    if not output_parent.is_dir():
        raise ValueError("fixture output parent must be an existing directory")

    extracted = _extract_u64_economics(u64_path)
    common = {
        "rule_package": _rule_package(),
        "calendar": _calendar(),
        "daily_bars": _daily_bars(),
        "market_status": _market_status(),
        "option_contracts": extracted["option_contracts"],
        "market_quotes": extracted["market_quotes"],
        "pit_inputs": extracted["pit_inputs"],
        "fee_schedule": extracted["fee_schedule"],
        "frozen_kelly_receipt": _frozen_kelly_receipt(),
    }
    scenarios = {
        "pass": {
            **deepcopy(common),
            "minute_bars": _minute_bars(confirmation_holds=True),
            "account_snapshot_a": _account(settled_cash_available=True),
        },
        "no_action": {
            **deepcopy(common),
            "minute_bars": _minute_bars(confirmation_holds=False),
            "account_snapshot_a": _account(settled_cash_available=True),
        },
        "no_decision": {
            **deepcopy(common),
            "minute_bars": _minute_bars(confirmation_holds=True),
            "account_snapshot_a": _account(settled_cash_available=False),
        },
    }
    bundle_ids = {
        "pass": "gld-synthetic-20260828-a-v1",
        "no_action": "gld-synthetic-20260828-b-v1",
        "no_decision": "gld-synthetic-20260828-c-v1",
    }
    temporary_root = Path(
        tempfile.mkdtemp(
            prefix=f".{output_root.name}.tmp-",
            dir=output_parent,
        )
    )
    os.chmod(temporary_root, 0o700, follow_symlinks=False)
    try:
        for name, documents in scenarios.items():
            _generate_scenario(
                temporary_root / name,
                bundle_id=bundle_ids[name],
                documents=documents,
            )
            load_raw_bundle(temporary_root / name)
        _publish_directory_no_clobber(temporary_root, output_root)
    finally:
        if temporary_root.exists():
            shutil.rmtree(temporary_root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--u64", type=Path, default=DEFAULT_U64)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    generate(arguments.u64, arguments.output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
