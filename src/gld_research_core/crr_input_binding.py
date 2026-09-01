"""Snapshot-wide deterministic CRR Call input binding helpers.

This module is deliberately separate from :mod:`gld_research_core.crr_delta`:
that pricing-engine source file is itself part of the frozen model identity.
"""

from __future__ import annotations

from typing import cast

from gld_normalizer.errors import NormalizationError

from .crr_delta import (
    CrrCallInputsV1,
    CrrPitInputsV1,
    bind_crr_call_inputs_from_snapshot,
)
from .facts import (
    OptionContractV1,
    OptionQuoteSnapshotV1,
    OptionQuoteV1,
    TopOfBookV1,
    canonical_snapshot_sha256,
)


def _exact_midpoint_nano_usd(book: TopOfBookV1) -> int:
    total = book.bid_nano_usd + book.ask_nano_usd
    if total % 2:
        raise NormalizationError("DELTA_INPUT_MIDPOINT_NON_INTEGRAL")
    return total // 2


def _bind_with_single_call_semantics(
    snapshot: OptionQuoteSnapshotV1,
    *,
    contracts: tuple[OptionContractV1, ...],
    pit_inputs: CrrPitInputsV1,
) -> tuple[CrrCallInputsV1, ...]:
    return tuple(
        bind_crr_call_inputs_from_snapshot(
            snapshot,
            contract=contract,
            pit_inputs=pit_inputs,
        )
        for contract in contracts
    )


def _is_exact_fast_path_input(
    snapshot: OptionQuoteSnapshotV1,
    *,
    contracts: tuple[OptionContractV1, ...],
    pit_inputs: CrrPitInputsV1,
) -> bool:
    if (
        type(snapshot) is not OptionQuoteSnapshotV1
        or type(pit_inputs) is not CrrPitInputsV1
        or any(type(contract) is not OptionContractV1 for contract in contracts)
        or type(snapshot.underlying_top) is not TopOfBookV1
        or type(snapshot.option_quotes) is not tuple
        or any(type(quote) is not OptionQuoteV1 for quote in snapshot.option_quotes)
    ):
        return False
    return all(
        type(quote.contract) is OptionContractV1
        and type(quote.top_of_book) is TopOfBookV1
        for quote in snapshot.option_quotes
    )


def bind_crr_call_inputs_bulk_from_snapshot(
    snapshot: OptionQuoteSnapshotV1,
    *,
    contracts: tuple[OptionContractV1, ...],
    pit_inputs: CrrPitInputsV1,
) -> tuple[CrrCallInputsV1, ...]:
    """Bind one exact ordered 64-Call vector from one immutable snapshot.

    Each returned item is hash-equivalent to the corresponding single-call
    binder result.  Snapshot-wide hashes and the quote-chain index are shared;
    per-contract binding, causality, midpoint, and object validation remain.
    """

    if (
        not isinstance(snapshot, OptionQuoteSnapshotV1)
        or type(contracts) is not tuple
        or len(contracts) != 64
        or not isinstance(pit_inputs, CrrPitInputsV1)
    ):
        raise NormalizationError("DELTA_INPUT_MISSING")
    if not _is_exact_fast_path_input(
        snapshot,
        contracts=contracts,
        pit_inputs=pit_inputs,
    ):
        return _bind_with_single_call_semantics(
            snapshot,
            contracts=contracts,
            pit_inputs=pit_inputs,
        )
    if pit_inputs.as_of_utc_ns != snapshot.capture_utc_ns:
        raise NormalizationError("SNAPSHOT_NOT_CAUSAL")

    missing_quote = object()
    duplicate_quote = object()
    quote_by_contract_id: dict[str, object] = {}
    for quote in snapshot.option_quotes:
        contract_id = quote.contract.occ_symbol
        if contract_id in quote_by_contract_id:
            quote_by_contract_id[contract_id] = duplicate_quote
        else:
            quote_by_contract_id[contract_id] = quote

    initial_snapshot_sha256: str | None = None
    underlying_top_sha256: str | None = None
    spot_nano_usd: int | None = None
    bound_inputs: list[CrrCallInputsV1] = []
    for contract in contracts:
        matched_quote = quote_by_contract_id.get(
            contract.occ_symbol,
            missing_quote,
        )
        if (
            matched_quote is missing_quote
            or matched_quote is duplicate_quote
        ):
            raise NormalizationError("DELTA_INPUT_BINDING_MISMATCH")
        option_quote = cast(OptionQuoteV1, matched_quote)
        if option_quote.contract != contract:
            raise NormalizationError("DELTA_INPUT_BINDING_MISMATCH")

        for book in (snapshot.underlying_top, option_quote.top_of_book):
            if (
                book.ts_event_ns > book.ts_recv_ns
                or book.ts_recv_ns > snapshot.capture_utc_ns
            ):
                raise NormalizationError("SNAPSHOT_NOT_CAUSAL")

        if initial_snapshot_sha256 is None:
            initial_snapshot_sha256 = snapshot.snapshot_sha256
        contract_sha256 = canonical_snapshot_sha256(contract)
        if underlying_top_sha256 is None:
            underlying_top_sha256 = canonical_snapshot_sha256(
                snapshot.underlying_top
            )
        option_quote_sha256 = canonical_snapshot_sha256(option_quote)
        if spot_nano_usd is None:
            spot_nano_usd = _exact_midpoint_nano_usd(snapshot.underlying_top)
        option_mid_nano_usd = _exact_midpoint_nano_usd(
            option_quote.top_of_book
        )
        bound_inputs.append(
            CrrCallInputsV1(
                option_snapshot_sha256=initial_snapshot_sha256,
                contract_id=contract.occ_symbol,
                contract_sha256=contract_sha256,
                underlying_top_sha256=underlying_top_sha256,
                option_quote_sha256=option_quote_sha256,
                pit_inputs=pit_inputs,
                spot_nano_usd=spot_nano_usd,
                strike_nano_usd=contract.strike_nano_usd,
                option_mid_nano_usd=option_mid_nano_usd,
                tick_nano_usd=option_quote.top_of_book.tick_nano_usd,
                valuation_utc_ns=snapshot.capture_utc_ns,
                expiry_utc_ns=contract.expiry_utc_ns,
            )
        )
    if initial_snapshot_sha256 is None:
        raise NormalizationError("DELTA_INPUT_MISSING")
    if snapshot.snapshot_sha256 != initial_snapshot_sha256:
        return _bind_with_single_call_semantics(
            snapshot,
            contracts=contracts,
            pit_inputs=pit_inputs,
        )
    return tuple(bound_inputs)


__all__ = ["bind_crr_call_inputs_bulk_from_snapshot"]
