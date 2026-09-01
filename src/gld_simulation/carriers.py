"""Simulation-only LC0 and BCS selection over one verified exact-64 batch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Mapping
from zoneinfo import ZoneInfo

from gld_normalizer.errors import NormalizationError
from gld_research_core.bcs import BcsFeeScheduleV1
from gld_research_core.crr_batch import (
    EXACT_BATCH_SIZE,
    CrrSemanticTerminalV1,
)
from gld_research_core.facts import OptionQuoteSnapshotV1, OptionQuoteV1
from gld_research_core.p1_native_decision import _suite_winner


_NEW_YORK = ZoneInfo("America/New_York")
_PPM = 1_000_000


def _ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


class CarrierSelectionError(ValueError):
    """A stable fail-closed simulation carrier error."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SimulationCarrierSelectionV1:
    long_quote: OptionQuoteV1
    short_quote: OptionQuoteV1
    long_coarse_delta_ppm: int
    long_delta_ppm: int
    short_coarse_delta_ppm: int
    short_delta_ppm: int
    h20_date: str
    minimum_expiry_date: str

    @property
    def net_delta_ppm(self) -> int:
        return self.long_delta_ppm - self.short_delta_ppm


@dataclass(frozen=True, slots=True)
class SimulationCarrierEconomicsV1:
    carrier_id: str
    base_entry_cost_nano_usd_per_contract: int
    stressed_entry_cost_nano_usd_per_contract: int
    sizing_max_loss_nano_usd_per_contract: int
    sizing_planned_loss_nano_usd_per_contract: int
    planned_exit_fees_nano_usd_per_contract: int
    gross_expiry_width_nano_usd_per_contract: int | None
    theoretical_expiry_max_profit_nano_usd_per_contract: int | None
    delta_notional_nano_usd_per_contract: int
    liquidity_capacity: int
    long_entry_adverse_ticks: int
    short_entry_adverse_ticks: int
    include_exit_fees_in_max_loss: bool
    long_entry_fee_nano_usd_per_contract: int
    short_entry_fee_nano_usd_per_contract: int
    long_exit_fee_nano_usd_per_contract: int
    short_exit_fee_nano_usd_per_contract: int

    def __post_init__(self) -> None:
        if self.carrier_id not in {"LC0", "BCS0"}:
            raise CarrierSelectionError("SIM_CARRIER_ECONOMICS_INVALID")
        for value in (
            self.base_entry_cost_nano_usd_per_contract,
            self.stressed_entry_cost_nano_usd_per_contract,
            self.sizing_max_loss_nano_usd_per_contract,
            self.sizing_planned_loss_nano_usd_per_contract,
            self.delta_notional_nano_usd_per_contract,
            self.liquidity_capacity,
        ):
            if type(value) is not int or value <= 0:
                raise CarrierSelectionError("SIM_CARRIER_ECONOMICS_INVALID")
        if (
            self.stressed_entry_cost_nano_usd_per_contract
            < self.base_entry_cost_nano_usd_per_contract
            or self.sizing_max_loss_nano_usd_per_contract
            != self.stressed_entry_cost_nano_usd_per_contract
            + self.planned_exit_fees_nano_usd_per_contract
            or self.sizing_planned_loss_nano_usd_per_contract
            != self.sizing_max_loss_nano_usd_per_contract
        ):
            raise CarrierSelectionError("SIM_CARRIER_ECONOMICS_INVALID")
        for value in (
            self.planned_exit_fees_nano_usd_per_contract,
            self.long_entry_fee_nano_usd_per_contract,
            self.short_entry_fee_nano_usd_per_contract,
            self.long_exit_fee_nano_usd_per_contract,
            self.short_exit_fee_nano_usd_per_contract,
            self.long_entry_adverse_ticks,
            self.short_entry_adverse_ticks,
        ):
            if type(value) is not int or value < 0:
                raise CarrierSelectionError("SIM_CARRIER_ECONOMICS_INVALID")
        if self.include_exit_fees_in_max_loss is not True:
            raise CarrierSelectionError("SIM_CARRIER_ECONOMICS_INVALID")
        if self.carrier_id == "LC0":
            if (
                self.gross_expiry_width_nano_usd_per_contract is not None
                or self.theoretical_expiry_max_profit_nano_usd_per_contract
                is not None
                or self.short_entry_fee_nano_usd_per_contract != 0
                or self.short_exit_fee_nano_usd_per_contract != 0
                or self.planned_exit_fees_nano_usd_per_contract
                != self.long_exit_fee_nano_usd_per_contract
            ):
                raise CarrierSelectionError("SIM_CARRIER_ECONOMICS_INVALID")
            return
        if (
            type(self.gross_expiry_width_nano_usd_per_contract) is not int
            or type(
                self.theoretical_expiry_max_profit_nano_usd_per_contract
            )
            is not int
            or self.gross_expiry_width_nano_usd_per_contract
            <= self.sizing_max_loss_nano_usd_per_contract
            or self.theoretical_expiry_max_profit_nano_usd_per_contract
            != self.gross_expiry_width_nano_usd_per_contract
            - self.base_entry_cost_nano_usd_per_contract
            - self.planned_exit_fees_nano_usd_per_contract
            or self.planned_exit_fees_nano_usd_per_contract
            != self.long_exit_fee_nano_usd_per_contract
            + self.short_exit_fee_nano_usd_per_contract
        ):
            raise CarrierSelectionError("SIM_BCS_INVALID_NET_DEBIT")

    @property
    def execution_assumptions(self) -> dict[str, object]:
        return {
            "include_exit_fees_in_max_loss": self.include_exit_fees_in_max_loss,
            "long_entry_adverse_ticks": self.long_entry_adverse_ticks,
            "short_entry_adverse_ticks": self.short_entry_adverse_ticks,
        }

    @property
    def fee_components_nano_usd_per_contract(self) -> dict[str, int]:
        return {
            "long_entry": self.long_entry_fee_nano_usd_per_contract,
            "long_exit": self.long_exit_fee_nano_usd_per_contract,
            "short_entry": self.short_entry_fee_nano_usd_per_contract,
            "short_exit": self.short_exit_fee_nano_usd_per_contract,
        }

    def as_dict(self) -> dict[str, object]:
        """Return every pricing assumption and derived amount for audit."""

        return {
            "base_entry_cost_nano_usd_per_contract": self.base_entry_cost_nano_usd_per_contract,
            "carrier_id": self.carrier_id,
            "delta_notional_nano_usd_per_contract": self.delta_notional_nano_usd_per_contract,
            "execution_assumptions": self.execution_assumptions,
            "fee_components_nano_usd_per_contract": self.fee_components_nano_usd_per_contract,
            "gross_expiry_width_nano_usd_per_contract": self.gross_expiry_width_nano_usd_per_contract,
            "liquidity_capacity": self.liquidity_capacity,
            "planned_exit_fees_nano_usd_per_contract": self.planned_exit_fees_nano_usd_per_contract,
            "sizing_max_loss_nano_usd_per_contract": self.sizing_max_loss_nano_usd_per_contract,
            "sizing_planned_loss_nano_usd_per_contract": self.sizing_planned_loss_nano_usd_per_contract,
            "stressed_entry_cost_nano_usd_per_contract": self.stressed_entry_cost_nano_usd_per_contract,
            "theoretical_expiry_max_profit_nano_usd_per_contract": self.theoretical_expiry_max_profit_nano_usd_per_contract,
        }


def _expiry_date(expiry_utc_ns: int) -> date:
    try:
        seconds, remainder = divmod(expiry_utc_ns, 1_000_000_000)
        if type(expiry_utc_ns) is not int or remainder < 0:
            raise ValueError("invalid expiry")
        return datetime.fromtimestamp(
            seconds,
            tz=timezone.utc,
        ).astimezone(_NEW_YORK).date()
    except (OverflowError, OSError, ValueError) as error:
        raise CarrierSelectionError("SIM_LC0_EXPIRY_INVALID") from error


def _lc0_suite_winner(
    *,
    candidates: tuple[OptionQuoteV1, ...],
    terminal_by_id: Mapping[str, CrrSemanticTerminalV1],
    delta_field: str,
    target_delta_ppm: int,
    minimum_delta_ppm: int,
    maximum_delta_ppm: int,
) -> OptionQuoteV1:
    eligible: list[OptionQuoteV1] = []
    for quote in candidates:
        terminal = terminal_by_id.get(quote.contract.occ_symbol)
        delta = None if terminal is None else getattr(terminal, delta_field)
        if (
            terminal is not None
            and terminal.terminal_status == "PASS"
            and type(delta) is int
            and minimum_delta_ppm <= delta <= maximum_delta_ppm
        ):
            eligible.append(quote)
    if not eligible:
        raise CarrierSelectionError("SIM_LC0_NO_ELIGIBLE_CALL")
    return min(
        eligible,
        key=lambda quote: (
            abs(
                getattr(
                    terminal_by_id[quote.contract.occ_symbol],
                    delta_field,
                )
                - target_delta_ppm
            ),
            -quote.contract.strike_nano_usd,
            quote.contract.occ_symbol,
        ),
    )


def select_lc0_and_bcs(
    *,
    snapshot: OptionQuoteSnapshotV1,
    terminals: tuple[CrrSemanticTerminalV1, ...],
    h20_date: str,
    minimum_days_after_h20: int,
    lc0_target_delta_ppm: int,
    lc0_minimum_delta_ppm: int,
    lc0_maximum_delta_ppm: int,
) -> SimulationCarrierSelectionV1:
    """Select LC0 around 0.50 Delta, then the existing 0.25 BCS short leg."""

    if (
        type(snapshot) is not OptionQuoteSnapshotV1
        or type(terminals) is not tuple
        or len(terminals) != EXACT_BATCH_SIZE
        or any(type(item) is not CrrSemanticTerminalV1 for item in terminals)
        or any(item.terminal_status != "PASS" for item in terminals)
    ):
        raise CarrierSelectionError("SIM_CRR_BATCH_NOT_ALL_PASS")
    try:
        h20 = date.fromisoformat(h20_date)
    except (TypeError, ValueError) as error:
        raise CarrierSelectionError("SIM_LC0_EXPIRY_INVALID") from error
    for value in (
        minimum_days_after_h20,
        lc0_target_delta_ppm,
        lc0_minimum_delta_ppm,
        lc0_maximum_delta_ppm,
    ):
        if type(value) is not int:
            raise CarrierSelectionError("SIM_LC0_SELECTOR_INVALID")
    if (
        minimum_days_after_h20 < 0
        or not 0
        <= lc0_minimum_delta_ppm
        <= lc0_target_delta_ppm
        <= lc0_maximum_delta_ppm
        <= _PPM
    ):
        raise CarrierSelectionError("SIM_LC0_SELECTOR_INVALID")

    quote_by_id = {
        quote.contract.occ_symbol: quote for quote in snapshot.option_quotes
    }
    terminal_by_id = {item.contract_id: item for item in terminals}
    if (
        len(quote_by_id) != EXACT_BATCH_SIZE
        or set(quote_by_id) != set(terminal_by_id)
    ):
        raise CarrierSelectionError("SIM_CRR_TERMINAL_BINDING_INVALID")
    minimum_expiry = h20 + timedelta(days=minimum_days_after_h20)
    eligible_expiries = tuple(
        sorted(
            {
                _expiry_date(quote.contract.expiry_utc_ns)
                for quote in snapshot.option_quotes
                if _expiry_date(quote.contract.expiry_utc_ns)
                >= minimum_expiry
            }
        )
    )
    if not eligible_expiries:
        raise CarrierSelectionError("SIM_LC0_NO_ELIGIBLE_EXPIRY")
    earliest = eligible_expiries[0]
    expiry_candidates = tuple(
        quote
        for quote in snapshot.option_quotes
        if _expiry_date(quote.contract.expiry_utc_ns) == earliest
    )
    coarse_winner = _lc0_suite_winner(
        candidates=expiry_candidates,
        terminal_by_id=terminal_by_id,
        delta_field="coarse_delta_ppm",
        target_delta_ppm=lc0_target_delta_ppm,
        minimum_delta_ppm=lc0_minimum_delta_ppm,
        maximum_delta_ppm=lc0_maximum_delta_ppm,
    )
    fine_winner = _lc0_suite_winner(
        candidates=expiry_candidates,
        terminal_by_id=terminal_by_id,
        delta_field="fine_delta_ppm",
        target_delta_ppm=lc0_target_delta_ppm,
        minimum_delta_ppm=lc0_minimum_delta_ppm,
        maximum_delta_ppm=lc0_maximum_delta_ppm,
    )
    if coarse_winner.contract.occ_symbol != fine_winner.contract.occ_symbol:
        raise CarrierSelectionError("SIM_LC0_SELECTOR_MODEL_INSTABILITY")
    long_quote = fine_winner
    try:
        bcs_coarse = _suite_winner(
            long_quote=long_quote,
            quote_by_id=quote_by_id,
            terminal_by_id=terminal_by_id,
            suite="coarse",
        )
        bcs_fine = _suite_winner(
            long_quote=long_quote,
            quote_by_id=quote_by_id,
            terminal_by_id=terminal_by_id,
            suite="fine",
        )
    except (NormalizationError, ValueError) as error:
        reason_code = getattr(error, "reason_code", "SIM_BCS_SELECTION_FAILED")
        raise CarrierSelectionError(reason_code) from error
    if bcs_coarse.contract.occ_symbol != bcs_fine.contract.occ_symbol:
        raise CarrierSelectionError("SELECTOR_MODEL_INSTABILITY")

    long_terminal = terminal_by_id[long_quote.contract.occ_symbol]
    short_terminal = terminal_by_id[bcs_fine.contract.occ_symbol]
    if (
        type(long_terminal.coarse_delta_ppm) is not int
        or type(long_terminal.fine_delta_ppm) is not int
        or type(short_terminal.coarse_delta_ppm) is not int
        or type(short_terminal.fine_delta_ppm) is not int
        or short_terminal.fine_delta_ppm >= long_terminal.fine_delta_ppm
    ):
        raise CarrierSelectionError("SIM_CRR_TERMINAL_INVALID")
    return SimulationCarrierSelectionV1(
        long_quote=long_quote,
        short_quote=bcs_fine,
        long_coarse_delta_ppm=long_terminal.coarse_delta_ppm,
        long_delta_ppm=long_terminal.fine_delta_ppm,
        short_coarse_delta_ppm=short_terminal.coarse_delta_ppm,
        short_delta_ppm=short_terminal.fine_delta_ppm,
        h20_date=h20.isoformat(),
        minimum_expiry_date=minimum_expiry.isoformat(),
    )


def price_simulation_carriers(
    *,
    selection: SimulationCarrierSelectionV1,
    snapshot: OptionQuoteSnapshotV1,
    fees: BcsFeeScheduleV1,
    long_entry_adverse_ticks: int,
    short_entry_adverse_ticks: int,
    include_exit_fees_in_max_loss: bool,
) -> tuple[SimulationCarrierEconomicsV1, SimulationCarrierEconomicsV1]:
    """Price LC0 and BCS using explicit, auditable execution assumptions."""

    if type(selection) is not SimulationCarrierSelectionV1:
        raise CarrierSelectionError("SIM_CARRIER_SELECTION_INVALID")
    if type(snapshot) is not OptionQuoteSnapshotV1 or type(fees) is not BcsFeeScheduleV1:
        raise CarrierSelectionError("SIM_CARRIER_INPUT_INVALID")
    if (
        type(long_entry_adverse_ticks) is not int
        or long_entry_adverse_ticks < 0
        or type(short_entry_adverse_ticks) is not int
        or short_entry_adverse_ticks < 0
        or include_exit_fees_in_max_loss is not True
    ):
        raise CarrierSelectionError("SIM_CARRIER_EXECUTION_ASSUMPTIONS_INVALID")
    if fees.effective_from_utc_ns > snapshot.capture_utc_ns:
        raise CarrierSelectionError("BCS_FEE_SCHEDULE_MISSING")
    long_book = selection.long_quote.top_of_book
    short_book = selection.short_quote.top_of_book
    multiplier = selection.long_quote.contract.multiplier
    if multiplier != 100:
        raise CarrierSelectionError("SIM_CARRIER_INPUT_INVALID")
    spot_total = snapshot.underlying_top.bid_nano_usd + snapshot.underlying_top.ask_nano_usd
    if spot_total % 2:
        raise CarrierSelectionError("DELTA_INPUT_MIDPOINT_NON_INTEGRAL")
    spot = spot_total // 2

    lc0_base = (
        long_book.ask_nano_usd * multiplier
        + fees.long_entry_fee_nano_usd_per_contract
    )
    lc0_stressed = (
        (
            long_book.ask_nano_usd
            + long_entry_adverse_ticks * long_book.tick_nano_usd
        )
        * multiplier
        + fees.long_entry_fee_nano_usd_per_contract
    )
    lc0_exit_fees = fees.long_exit_fee_nano_usd_per_contract
    lc0_sizing_loss = lc0_stressed + lc0_exit_fees
    lc0_delta_notional = _ceil_div(
        spot * multiplier * selection.long_delta_ppm,
        _PPM,
    )
    lc0 = SimulationCarrierEconomicsV1(
        carrier_id="LC0",
        base_entry_cost_nano_usd_per_contract=lc0_base,
        stressed_entry_cost_nano_usd_per_contract=lc0_stressed,
        sizing_max_loss_nano_usd_per_contract=lc0_sizing_loss,
        sizing_planned_loss_nano_usd_per_contract=lc0_sizing_loss,
        planned_exit_fees_nano_usd_per_contract=lc0_exit_fees,
        gross_expiry_width_nano_usd_per_contract=None,
        theoretical_expiry_max_profit_nano_usd_per_contract=None,
        delta_notional_nano_usd_per_contract=lc0_delta_notional,
        liquidity_capacity=long_book.ask_size,
        long_entry_adverse_ticks=long_entry_adverse_ticks,
        short_entry_adverse_ticks=short_entry_adverse_ticks,
        include_exit_fees_in_max_loss=include_exit_fees_in_max_loss,
        long_entry_fee_nano_usd_per_contract=fees.long_entry_fee_nano_usd_per_contract,
        short_entry_fee_nano_usd_per_contract=0,
        long_exit_fee_nano_usd_per_contract=fees.long_exit_fee_nano_usd_per_contract,
        short_exit_fee_nano_usd_per_contract=0,
    )

    total_fees = (
        fees.long_entry_fee_nano_usd_per_contract
        + fees.short_entry_fee_nano_usd_per_contract
    )
    bcs_base = (
        (long_book.ask_nano_usd - short_book.bid_nano_usd) * multiplier
        + total_fees
    )
    stressed_short_bid = max(
        0,
        short_book.bid_nano_usd
        - short_entry_adverse_ticks * short_book.tick_nano_usd,
    )
    bcs_stressed = (
        (
            long_book.ask_nano_usd
            + long_entry_adverse_ticks * long_book.tick_nano_usd
            - stressed_short_bid
        )
        * multiplier
        + total_fees
    )
    gross_width = (
        selection.short_quote.contract.strike_nano_usd
        - selection.long_quote.contract.strike_nano_usd
    ) * multiplier
    bcs_exit_fees = (
        fees.long_exit_fee_nano_usd_per_contract
        + fees.short_exit_fee_nano_usd_per_contract
    )
    bcs_sizing_loss = bcs_stressed + bcs_exit_fees
    if not 0 < bcs_base <= bcs_stressed <= bcs_sizing_loss < gross_width:
        raise CarrierSelectionError("SIM_BCS_INVALID_NET_DEBIT")
    bcs_delta_notional = _ceil_div(
        spot * multiplier * selection.net_delta_ppm,
        _PPM,
    )
    bcs = SimulationCarrierEconomicsV1(
        carrier_id="BCS0",
        base_entry_cost_nano_usd_per_contract=bcs_base,
        stressed_entry_cost_nano_usd_per_contract=bcs_stressed,
        sizing_max_loss_nano_usd_per_contract=bcs_sizing_loss,
        sizing_planned_loss_nano_usd_per_contract=bcs_sizing_loss,
        planned_exit_fees_nano_usd_per_contract=bcs_exit_fees,
        gross_expiry_width_nano_usd_per_contract=gross_width,
        theoretical_expiry_max_profit_nano_usd_per_contract=(
            gross_width - bcs_base - bcs_exit_fees
        ),
        delta_notional_nano_usd_per_contract=bcs_delta_notional,
        liquidity_capacity=min(long_book.ask_size, short_book.bid_size),
        long_entry_adverse_ticks=long_entry_adverse_ticks,
        short_entry_adverse_ticks=short_entry_adverse_ticks,
        include_exit_fees_in_max_loss=include_exit_fees_in_max_loss,
        long_entry_fee_nano_usd_per_contract=fees.long_entry_fee_nano_usd_per_contract,
        short_entry_fee_nano_usd_per_contract=fees.short_entry_fee_nano_usd_per_contract,
        long_exit_fee_nano_usd_per_contract=fees.long_exit_fee_nano_usd_per_contract,
        short_exit_fee_nano_usd_per_contract=fees.short_exit_fee_nano_usd_per_contract,
    )
    return lc0, bcs


__all__ = [
    "CarrierSelectionError",
    "SimulationCarrierEconomicsV1",
    "SimulationCarrierSelectionV1",
    "price_simulation_carriers",
    "select_lc0_and_bcs",
]
