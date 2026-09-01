from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timezone
import inspect
import unittest
from unittest.mock import patch

from gld_normalizer.errors import NormalizationError
import gld_research_core.bcs as bcs_module
from gld_research_core.bcs import (
    BcsManagementSnapshotV1,
    BcsEntryCostV1,
    BcsEvaluationV1,
    BcsExitCostV1,
    BcsFeeScheduleV1,
    BcsLifecycleTransitionV1,
    BcsSelectionV1,
    BcsTradeEpisodeV1,
    BrokerReconciliationSnapshotBV1,
    BlockingNotificationV1,
    CrrDeltaEvidenceV1,
    DecisionArtifactV1,
    Lc0SelectionBindingV1,
    bind_bcs_management_snapshot_for_research,
    bind_lc0_selection_for_research,
    build_decision_artifact,
    evaluate_bcs_research,
    is_verified_bcs_evaluation,
    next_bcs_lifecycle_state,
    open_bcs_trade_episode_for_research,
    price_bcs_entry,
    price_bcs_exit,
    select_bcs_short_call as _select_bcs_short_call,
)
from gld_research_core.crr_delta import (
    MODEL_ID,
    MODEL_SHA256,
    CrrCallInputsV1,
    CrrDeltaResultV1,
    CrrPitInputsV1,
    bind_crr_call_inputs_from_snapshot,
    compute_american_call_delta,
)
from gld_research_core.facts import (
    QUOTE_QUALITY_POLICY_SHA256,
    QUOTE_QUALITY_POLICY_VERSION,
    OptionContractV1,
    OptionQuoteSnapshotV1,
    OptionQuoteV1,
    OptionSnapshotCandidateLedgerV1,
    SignalSnapshotV1,
    TopOfBookV1,
    build_option_snapshot_candidate_ledger,
    canonical_snapshot_sha256,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
def utc_nanoseconds(value: datetime) -> int:
    return int(value.timestamp()) * 1_000_000_000


WINDOW_START_NS = utc_nanoseconds(
    datetime(2026, 8, 28, 14, 45, tzinfo=timezone.utc)
)
WINDOW_END_NS = WINDOW_START_NS + 60_000_000_000
CAPTURE_NS = WINDOW_START_NS + 10_000_000_000
EXPIRY_NS = utc_nanoseconds(
    datetime(2026, 12, 18, 21, 0, tzinfo=timezone.utc)
)
NANO = 1_000_000_000


def signal_snapshot(signal_state: str = "PASS") -> SignalSnapshotV1:
    return SignalSnapshotV1(
        signal_id="gld-gate-a-2026-08-28",
        trading_date="2026-08-28",
        rule_version="gld-research-v0.2",
        rule_sha256=SHA_A,
        calendar_authority_id="XNYS",
        calendar_version="fixture-v1",
        calendar_sha256=SHA_A,
        phase_receipt_sha256=SHA_B,
        completeness_receipt_sha256=SHA_A,
        cutoff_utc_ns=WINDOW_START_NS,
        max_event_utc_ns=WINDOW_START_NS - 2,
        max_receive_utc_ns=WINDOW_START_NS - 1,
        signal_state=signal_state,
        input_fact_sha256=SHA_B,
    )


def book(
    *,
    bid: int,
    ask: int,
    bid_size: int = 20,
    ask_size: int = 20,
    tick: int = 10_000_000,
) -> TopOfBookV1:
    return TopOfBookV1(
        bid_nano_usd=bid,
        ask_nano_usd=ask,
        bid_size=bid_size,
        ask_size=ask_size,
        tick_nano_usd=tick,
        ts_event_ns=CAPTURE_NS - 200_000_000,
        ts_recv_ns=CAPTURE_NS - 100_000_000,
        flags=(),
    )


def option_quote(
    strike_usd: int,
    *,
    occ_symbol: str | None = None,
    expiry_ns: int = EXPIRY_NS,
    bid: int = 4 * NANO,
    ask: int = 5 * NANO,
    bid_size: int = 20,
    ask_size: int = 20,
    tick: int = 10_000_000,
) -> OptionQuoteV1:
    strike_milli = strike_usd * 1_000
    identity = occ_symbol or f"GLD   261218C{strike_milli:08d}"
    return OptionQuoteV1(
        contract=OptionContractV1(
            occ_symbol=identity,
            underlying="GLD",
            right="C",
            exercise_style="AMERICAN",
            strike_nano_usd=strike_usd * NANO,
            expiry_utc_ns=expiry_ns,
            last_trading_utc_ns=expiry_ns - 1_000_000_000,
            activation_utc_ns=WINDOW_START_NS - 1_000_000_000,
            multiplier=100,
            deliverable="100 GLD",
            currency="USD",
            standard_unadjusted=True,
        ),
        top_of_book=book(
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            tick=tick,
        ),
    )


def option_snapshot(
    quotes: tuple[OptionQuoteV1, ...],
    *,
    signal: SignalSnapshotV1 | None = None,
    window_start_utc_ns: int = WINDOW_START_NS,
    capture_utc_ns: int = CAPTURE_NS,
    candidate_ordinal: int = 1,
    candidate_provenance_sha256: str | None = None,
) -> OptionQuoteSnapshotV1:
    bound_signal = signal or signal_snapshot()
    return OptionQuoteSnapshotV1(
        signal_snapshot_sha256=bound_signal.snapshot_sha256,
        window_start_utc_ns=window_start_utc_ns,
        window_end_utc_ns=window_start_utc_ns + 60_000_000_000,
        capture_utc_ns=capture_utc_ns,
        candidate_ordinal=candidate_ordinal,
        candidate_provenance_sha256=(
            candidate_provenance_sha256
            or (SHA_B if candidate_ordinal == 1 else SHA_C)
        ),
        underlying_top=book(
            bid=299_900_000_000,
            ask=300_100_000_000,
        ),
        option_quotes=quotes,
        source_id="SYNTHETIC_FIXTURE_ONLY",
        source_version="fixture-v1",
        market_data_type="SYNTHETIC_NOT_ENTITLED",
        source_receipt_sha256=SHA_A,
    )


def delta_evidence(
    snapshot: OptionQuoteSnapshotV1,
    contract_id: str,
    fine_delta_ppm: int,
    *,
    coarse_delta_ppm: int | None = None,
    snapshot_sha256: str | None = None,
) -> CrrDeltaEvidenceV1:
    coarse = fine_delta_ppm if coarse_delta_ppm is None else coarse_delta_ppm
    quote = next(
        item for item in snapshot.option_quotes if item.contract.occ_symbol == contract_id
    )
    pit_inputs = CrrPitInputsV1(
        as_of_utc_ns=snapshot.capture_utc_ns,
        risk_free_rate_ppm=40_000,
        expense_yield_ppm=4_000,
        borrow_yield_ppm=1_000,
        rate_curve_sha256=SHA_A,
        distribution_assumption_sha256=SHA_B,
        borrow_assumption_sha256=SHA_A,
    )
    inputs = bind_crr_call_inputs_from_snapshot(
        snapshot,
        contract=quote.contract,
        pit_inputs=pit_inputs,
    )
    if snapshot_sha256 is not None and snapshot_sha256 != snapshot.snapshot_sha256:
        inputs = replace(inputs, option_snapshot_sha256=snapshot_sha256)
    result = CrrDeltaResultV1(
        model_id=MODEL_ID,
        model_sha256=MODEL_SHA256,
        input_sha256=inputs.input_sha256,
        option_snapshot_sha256=snapshot_sha256 or snapshot.snapshot_sha256,
        contract_id=contract_id,
        iv_ppm=300_000,
        delta_ppm=fine_delta_ppm,
        coarse_iv_ppm=300_000,
        fine_iv_ppm=300_000,
        coarse_delta_ppm=coarse,
        fine_delta_ppm=fine_delta_ppm,
        coarse_price_residual_nano_usd=1_000,
        fine_price_residual_nano_usd=1_000,
        coarse_early_exercise_nodes=0,
        fine_early_exercise_nodes=0,
    )
    with patch.object(
        bcs_module,
        "is_verified_crr_delta_result",
        return_value=True,
    ):
        return CrrDeltaEvidenceV1(inputs=inputs, result=result)


def deltas_for(
    snapshot: OptionQuoteSnapshotV1,
    values: tuple[int, ...],
    *,
    coarse_values: tuple[int, ...] | None = None,
) -> tuple[CrrDeltaEvidenceV1, ...]:
    coarse = coarse_values or values
    snapshot_sha256 = snapshot.snapshot_sha256
    return tuple(
        delta_evidence(
            snapshot,
            quote.contract.occ_symbol,
            fine,
            coarse_delta_ppm=coarse_value,
            snapshot_sha256=snapshot_sha256,
        )
        for quote, fine, coarse_value in zip(
            snapshot.option_quotes,
            values,
            coarse,
            strict=True,
        )
    )


def standard_market(
    *,
    long_bid: int = 9 * NANO,
    long_ask: int = 10 * NANO,
    short_bid: int = 4 * NANO,
    short_ask: int = 5 * NANO,
    long_bid_size: int = 20,
    long_ask_size: int = 20,
    short_bid_size: int = 20,
    short_ask_size: int = 20,
) -> tuple[
    OptionQuoteSnapshotV1,
    Lc0SelectionBindingV1,
    tuple[CrrDeltaEvidenceV1, ...],
]:
    long_quote = option_quote(
        300,
        bid=long_bid,
        ask=long_ask,
        bid_size=long_bid_size,
        ask_size=long_ask_size,
    )
    short_quote = option_quote(
        310,
        bid=short_bid,
        ask=short_ask,
        bid_size=short_bid_size,
        ask_size=short_ask_size,
    )
    snapshot = option_snapshot((long_quote, short_quote))
    return (
        snapshot,
        bind_lc0_selection_for_research(
            signal=signal_snapshot(),
            snapshot=snapshot,
            selector_contract_sha256=SHA_B,
            long_call_id=long_quote.contract.occ_symbol,
        ),
        deltas_for(snapshot, (650_000, 250_000)),
    )


def lc0_binding(
    snapshot: OptionQuoteSnapshotV1,
    long_call_id: str,
    *,
    signal: SignalSnapshotV1 | None = None,
) -> Lc0SelectionBindingV1:
    return bind_lc0_selection_for_research(
        signal=signal or signal_snapshot(),
        snapshot=snapshot,
        selector_contract_sha256=SHA_B,
        long_call_id=long_call_id,
    )


def fee_schedule(**changes: object) -> BcsFeeScheduleV1:
    values: dict[str, object] = {
        "source_receipt_sha256": SHA_A,
        "effective_from_utc_ns": WINDOW_START_NS - 1,
        "long_entry_fee_nano_usd_per_contract": 100_000_000,
        "short_entry_fee_nano_usd_per_contract": 200_000_000,
        "long_exit_fee_nano_usd_per_contract": 150_000_000,
        "short_exit_fee_nano_usd_per_contract": 250_000_000,
    }
    values.update(changes)
    return BcsFeeScheduleV1(**values)  # type: ignore[arg-type]


def later_option_snapshot(
    entry_snapshot: OptionQuoteSnapshotV1,
    *,
    receive_offsets_ns: tuple[int, ...] | None = None,
    flags_by_leg: tuple[tuple[str, ...], ...] | None = None,
) -> OptionQuoteSnapshotV1:
    window_start = entry_snapshot.window_end_utc_ns + 60_000_000_000
    capture = window_start + 10_000_000_000
    offsets = receive_offsets_ns or (100_000_000,) * (
        len(entry_snapshot.option_quotes) + 1
    )
    flags = flags_by_leg or ((),) * (len(entry_snapshot.option_quotes) + 1)

    def refreshed(
        old: TopOfBookV1,
        *,
        receive_offset_ns: int,
        book_flags: tuple[str, ...],
    ) -> TopOfBookV1:
        return replace(
            old,
            ts_event_ns=capture - receive_offset_ns - 100_000_000,
            ts_recv_ns=capture - receive_offset_ns,
            flags=book_flags,
        )

    underlying = refreshed(
        entry_snapshot.underlying_top,
        receive_offset_ns=offsets[0],
        book_flags=flags[0],
    )
    quotes = tuple(
        replace(
            quote,
            top_of_book=refreshed(
                quote.top_of_book,
                receive_offset_ns=offset,
                book_flags=book_flags,
            ),
        )
        for quote, offset, book_flags in zip(
            entry_snapshot.option_quotes,
            offsets[1:],
            flags[1:],
            strict=True,
        )
    )
    return replace(
        entry_snapshot,
        candidate_ordinal=entry_snapshot.candidate_ordinal + 1,
        window_start_utc_ns=window_start,
        window_end_utc_ns=window_start + 60_000_000_000,
        capture_utc_ns=capture,
        underlying_top=underlying,
        option_quotes=quotes,
    )


def episode_and_management(
    snapshot: OptionQuoteSnapshotV1,
    selection: BcsSelectionV1,
    fees: BcsFeeScheduleV1,
    *,
    quantity: int,
) -> tuple[
    BcsTradeEpisodeV1,
    OptionQuoteSnapshotV1,
    BcsManagementSnapshotV1,
]:
    entry_cost = price_bcs_entry(
        selection=selection,
        snapshot=snapshot,
        fees=fees,
        quantity=quantity,
    )
    episode = open_bcs_trade_episode_for_research(
        episode_id="gld-bcs-fixture-episode",
        signal=signal_snapshot(),
        selection=selection,
        entry_snapshot=snapshot,
        entry_cost=entry_cost,
    )
    later = later_option_snapshot(snapshot)
    management = bind_bcs_management_snapshot_for_research(
        episode=episode,
        selection=selection,
        snapshot=later,
    )
    return episode, later, management


def reconciliation_snapshot_b(
    episode: BcsTradeEpisodeV1,
    selection: BcsSelectionV1,
    **changes: object,
) -> BrokerReconciliationSnapshotBV1:
    values: dict[str, object] = {
        "episode_sha256": episode.episode_sha256,
        "bcs_selection_sha256": selection.selection_sha256,
        "long_contract_sha256": selection.long_contract_sha256,
        "short_contract_sha256": selection.short_contract_sha256,
        "source_receipt_sha256": SHA_A,
        "positions_receipt_sha256": SHA_B,
        "open_orders_receipt_sha256": SHA_A,
        "pending_events_receipt_sha256": SHA_B,
        "terminal_fees_receipt_sha256": SHA_A,
        "capture_utc_ns": episode.entry_capture_utc_ns + 1,
        "long_position_quantity": 0,
        "short_position_quantity": 0,
        "open_order_count": 0,
        "pending_assignment": False,
        "pending_exercise": False,
        "terminal_fees_nano_usd": 400_000_000,
        "terminal_fees_final": True,
        "authority_status": "UNQUALIFIED_RESEARCH_ONLY",
    }
    values.update(changes)
    return BrokerReconciliationSnapshotBV1(**values)  # type: ignore[arg-type]


def select_bcs_short_call(**kwargs: object) -> BcsSelectionV1:
    """Exercise selector rules with synthetic sealed-result verification mocked."""

    arguments = dict(kwargs)
    if "candidate_ledger" not in arguments:
        snapshot = arguments.get("snapshot")
        if not isinstance(snapshot, OptionQuoteSnapshotV1):
            raise AssertionError("test selector requires an option snapshot")
        arguments["candidate_ledger"] = build_option_snapshot_candidate_ledger(
            (snapshot,),
            signal_snapshot=signal_snapshot(),
        )
    with patch.object(
        bcs_module,
        "is_verified_crr_delta_result",
        return_value=True,
    ):
        return _select_bcs_short_call(**arguments)  # type: ignore[arg-type]


def evaluate_bcs_research_synthetic(**kwargs: object) -> BcsEvaluationV1:
    arguments = dict(kwargs)
    candidates = arguments.get("candidate_ledger")
    if type(candidates) is tuple:
        signal = arguments.get("signal")
        if not isinstance(signal, SignalSnapshotV1):
            raise AssertionError("test evaluator requires a signal snapshot")
        arguments["candidate_ledger"] = (
            build_option_snapshot_candidate_ledger(
                candidates,
                signal_snapshot=signal,
            )
            if candidates
            else None
        )
    with patch.object(
        bcs_module,
        "is_verified_crr_delta_result",
        return_value=True,
    ):
        return evaluate_bcs_research(**arguments)  # type: ignore[arg-type]


class BcsSelectorTests(unittest.TestCase):
    def test_selector_accepts_real_engine_sealed_crr_results(self) -> None:
        long_quote = option_quote(
            300,
            bid=17_500_000_000,
            ask=18_500_000_000,
        )
        short_quote = option_quote(
            335,
            bid=5_800_000_000,
            ask=6_000_000_000,
        )
        snapshot = option_snapshot((long_quote, short_quote))
        pit_inputs = CrrPitInputsV1(
            as_of_utc_ns=snapshot.capture_utc_ns,
            risk_free_rate_ppm=40_000,
            expense_yield_ppm=4_000,
            borrow_yield_ppm=1_000,
            rate_curve_sha256=SHA_A,
            distribution_assumption_sha256=SHA_B,
            borrow_assumption_sha256=SHA_A,
        )
        evidence = tuple(
            CrrDeltaEvidenceV1(
                inputs=(
                    inputs := bind_crr_call_inputs_from_snapshot(
                        snapshot,
                        contract=quote.contract,
                        pit_inputs=pit_inputs,
                    )
                ),
                result=compute_american_call_delta(
                    inputs,
                    expected_model_sha256=MODEL_SHA256,
                ),
            )
            for quote in snapshot.option_quotes
        )

        selected = _select_bcs_short_call(
            long_call_selection=lc0_binding(
                snapshot,
                long_quote.contract.occ_symbol,
            ),
            candidate_ledger=build_option_snapshot_candidate_ledger(
                (snapshot,),
                signal_snapshot=signal_snapshot(),
            ),
            snapshot=snapshot,
            deltas=evidence,
        )

        self.assertEqual(selected.short_call_id, short_quote.contract.occ_symbol)
        self.assertGreaterEqual(selected.short_delta_ppm, 200_000)
        self.assertLessEqual(selected.short_delta_ppm, 300_000)

    def test_target_then_higher_strike_then_occ_lexical_tie_break(self) -> None:
        long_quote = option_quote(300)

        closest = option_snapshot(
            (long_quote, option_quote(310), option_quote(320))
        )
        selected = select_bcs_short_call(
            long_call_selection=lc0_binding(
                closest,
                long_quote.contract.occ_symbol,
            ),
            snapshot=closest,
            deltas=deltas_for(closest, (650_000, 240_000, 249_000)),
        )
        self.assertEqual(selected.short_strike_nano_usd, 320 * NANO)

        higher = option_snapshot(
            (long_quote, option_quote(310), option_quote(320))
        )
        selected = select_bcs_short_call(
            long_call_selection=lc0_binding(
                higher,
                long_quote.contract.occ_symbol,
            ),
            snapshot=higher,
            deltas=deltas_for(higher, (650_000, 240_000, 260_000)),
        )
        self.assertEqual(selected.short_strike_nano_usd, 320 * NANO)

        self.assertEqual(
            selected.short_call_id,
            "GLD   261218C00320000",
        )

    def test_long_leg_is_the_supplied_long_call_and_is_never_reselected(self) -> None:
        lower = option_quote(290)
        chosen_long = option_quote(300)
        short = option_quote(310)
        snapshot = option_snapshot((lower, chosen_long, short))
        selected = select_bcs_short_call(
            long_call_selection=lc0_binding(
                snapshot,
                chosen_long.contract.occ_symbol,
            ),
            snapshot=snapshot,
            deltas=deltas_for(snapshot, (700_000, 650_000, 250_000)),
        )
        self.assertEqual(selected.long_call_id, chosen_long.contract.occ_symbol)
        self.assertEqual(selected.long_strike_nano_usd, 300 * NANO)
        self.assertEqual(selected.short_call_id, short.contract.occ_symbol)

    def test_coarse_and_fine_winner_disagreement_fails_closed(self) -> None:
        long_quote = option_quote(300)
        first = option_quote(310)
        second = option_quote(320)
        snapshot = option_snapshot((long_quote, first, second))
        deltas = deltas_for(
            snapshot,
            (650_000, 249_900, 250_200),
            coarse_values=(650_000, 250_400, 249_700),
        )
        with self.assertRaises(NormalizationError) as raised:
            select_bcs_short_call(
                long_call_selection=lc0_binding(
                    snapshot,
                    long_quote.contract.occ_symbol,
                ),
                snapshot=snapshot,
                deltas=deltas,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "SELECTOR_MODEL_INSTABILITY",
        )

    def test_snapshot_and_delta_binding_are_exact_and_complete(self) -> None:
        snapshot, long_call_selection, deltas = standard_market()
        with self.assertRaises(NormalizationError) as unverified:
            CrrDeltaEvidenceV1(
                inputs=deltas[0].inputs,
                result=replace(deltas[0].result),
            )
        self.assertEqual(
            unverified.exception.reason_code,
            "UNVERIFIED_DELTA_RESULT",
        )
        forged_inputs = replace(
            deltas[1].inputs,
            spot_nano_usd=deltas[1].inputs.spot_nano_usd + NANO,
        )
        with patch.object(
            bcs_module,
            "is_verified_crr_delta_result",
            return_value=True,
        ):
            forged_evidence = CrrDeltaEvidenceV1(
                inputs=forged_inputs,
                result=replace(
                    deltas[1].result,
                    input_sha256=forged_inputs.input_sha256,
                ),
            )
        cases = (
            deltas[:-1],
            deltas + (deltas[-1],),
            tuple(item.result for item in deltas),
            (
                deltas[0],
                delta_evidence(
                    snapshot,
                    deltas[1].result.contract_id,
                    250_000,
                    snapshot_sha256=SHA_B,
                ),
            ),
            (deltas[0], forged_evidence),
        )
        for invalid in cases:
            with self.subTest(invalid=invalid):
                with self.assertRaises(NormalizationError) as raised:
                    select_bcs_short_call(
                        long_call_selection=long_call_selection,
                        snapshot=snapshot,
                        deltas=invalid,
                    )
                self.assertEqual(
                    raised.exception.reason_code,
                    "DELTA_INPUT_BINDING_MISMATCH",
                )

        with self.assertRaises(NormalizationError) as raised:
            select_bcs_short_call(
                long_call_selection=replace(
                    long_call_selection,
                    long_call_id="GLD   261218C00999000",
                ),
                snapshot=snapshot,
                deltas=deltas,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "BCS_LONG_LEG_BINDING_MISMATCH",
        )

        with self.assertRaises(NormalizationError) as signal_lineage:
            select_bcs_short_call(
                long_call_selection=replace(
                    long_call_selection,
                    signal_snapshot_sha256=SHA_A,
                ),
                snapshot=snapshot,
                deltas=deltas,
            )
        self.assertEqual(
            signal_lineage.exception.reason_code,
            "BCS_LONG_LEG_BINDING_MISMATCH",
        )

        changed_pit = replace(
            deltas[1].inputs.pit_inputs,
            risk_free_rate_ppm=(
                deltas[1].inputs.pit_inputs.risk_free_rate_ppm + 1
            ),
        )
        changed_inputs = replace(deltas[1].inputs, pit_inputs=changed_pit)
        with patch.object(
            bcs_module,
            "is_verified_crr_delta_result",
            return_value=True,
        ):
            changed_evidence = CrrDeltaEvidenceV1(
                inputs=changed_inputs,
                result=replace(
                    deltas[1].result,
                    input_sha256=changed_inputs.input_sha256,
                ),
            )
        with self.assertRaises(NormalizationError) as pit_mismatch:
            select_bcs_short_call(
                long_call_selection=long_call_selection,
                snapshot=snapshot,
                deltas=(deltas[0], changed_evidence),
            )
        self.assertEqual(
            pit_mismatch.exception.reason_code,
            "DELTA_PIT_INPUT_MISMATCH",
        )

        with self.assertRaises(NormalizationError) as raised:
            select_bcs_short_call(
                long_call_selection=replace(
                    long_call_selection,
                    long_contract_sha256=SHA_A,
                ),
                snapshot=snapshot,
                deltas=deltas,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "BCS_LONG_LEG_BINDING_MISMATCH",
        )

    def test_short_must_pass_both_delta_suites_and_contract_pairing_rules(self) -> None:
        long_quote = option_quote(300)
        lower_strike = option_quote(290)
        different_expiry = option_quote(310, expiry_ns=EXPIRY_NS + 1)
        too_high_delta = option_quote(320)
        snapshot = option_snapshot(
            (long_quote, lower_strike, different_expiry, too_high_delta)
        )
        deltas = deltas_for(
            snapshot,
            (650_000, 250_000, 250_000, 300_000),
            coarse_values=(650_000, 250_000, 250_000, 300_500),
        )
        with self.assertRaises(NormalizationError) as raised:
            select_bcs_short_call(
                long_call_selection=lc0_binding(
                    snapshot,
                    long_quote.contract.occ_symbol,
                ),
                snapshot=snapshot,
                deltas=deltas,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "BCS_NO_ELIGIBLE_SHORT_CALL",
        )

    def test_selection_is_frozen_slotted_and_enforces_net_delta_invariant(self) -> None:
        snapshot, long_call_selection, deltas = standard_market()
        selected = select_bcs_short_call(
            long_call_selection=long_call_selection,
            snapshot=snapshot,
            deltas=deltas,
        )
        self.assertIsInstance(selected, BcsSelectionV1)
        self.assertEqual(
            selected.quote_quality_policy_version,
            QUOTE_QUALITY_POLICY_VERSION,
        )
        self.assertEqual(
            selected.quote_quality_policy_sha256,
            QUOTE_QUALITY_POLICY_SHA256,
        )
        self.assertEqual(selected.net_delta_ppm, 400_000)
        self.assertEqual(selected.option_snapshot_sha256, snapshot.snapshot_sha256)
        self.assertEqual(
            selected.long_delta_input_sha256,
            deltas[0].inputs.input_sha256,
        )
        self.assertEqual(
            selected.short_delta_input_sha256,
            deltas[1].inputs.input_sha256,
        )
        self.assertEqual(
            selected.long_delta_evidence_sha256,
            deltas[0].evidence_sha256,
        )
        self.assertEqual(
            selected.short_delta_evidence_sha256,
            deltas[1].evidence_sha256,
        )
        self.assertEqual(selected.long_delta_run_sha256, deltas[0].result.run_sha256)
        self.assertEqual(selected.short_delta_run_sha256, deltas[1].result.run_sha256)
        self.assertEqual(
            selected.long_delta_runtime_fingerprint_sha256,
            deltas[0].result.runtime_fingerprint_sha256,
        )
        self.assertEqual(
            selected.short_delta_runtime_fingerprint_sha256,
            deltas[1].result.runtime_fingerprint_sha256,
        )
        with self.assertRaises(FrozenInstanceError):
            selected.net_delta_ppm = 1  # type: ignore[misc]
        with self.assertRaises(NormalizationError) as raised:
            replace(selected, net_delta_ppm=0)
        self.assertEqual(raised.exception.reason_code, "BCS_INVALID_NET_DELTA")
        with self.assertRaises(NormalizationError) as duplicate_evidence:
            replace(
                selected,
                short_delta_evidence_sha256=selected.long_delta_evidence_sha256,
            )
        self.assertEqual(
            duplicate_evidence.exception.reason_code,
            "DELTA_INPUT_BINDING_MISMATCH",
        )
        with self.assertRaises(NormalizationError) as mixed_runtime:
            replace(
                selected,
                short_delta_runtime_fingerprint_sha256=SHA_A,
            )
        self.assertEqual(
            mixed_runtime.exception.reason_code,
            "DELTA_INPUT_BINDING_MISMATCH",
        )
        with self.assertRaises(NormalizationError) as wrong_policy:
            replace(selected, quote_quality_policy_sha256=SHA_A)
        self.assertEqual(
            wrong_policy.exception.reason_code,
            "QUOTE_QUALITY_POLICY_MISMATCH",
        )

    def test_direct_selector_and_entry_reject_unqualified_quote_quality(self) -> None:
        snapshot, long_call_selection, deltas = standard_market()
        selection = select_bcs_short_call(
            long_call_selection=long_call_selection,
            snapshot=snapshot,
            deltas=deltas,
        )
        stale_book = replace(
            snapshot.underlying_top,
            ts_event_ns=snapshot.capture_utc_ns - 6_100_000_000,
            ts_recv_ns=snapshot.capture_utc_ns - 6_000_000_000,
        )
        skewed_book = replace(
            snapshot.underlying_top,
            ts_event_ns=snapshot.capture_utc_ns - 1_300_000_000,
            ts_recv_ns=snapshot.capture_utc_ns - 1_200_000_000,
        )
        halted_book = replace(snapshot.underlying_top, flags=("HALTED",))
        cases = (
            (replace(snapshot, underlying_top=stale_book), "QUOTE_STALE"),
            (
                replace(snapshot, underlying_top=skewed_book),
                "CROSS_LEG_SKEW_EXCEEDED",
            ),
            (replace(snapshot, underlying_top=halted_book), "MARKET_HALTED"),
        )
        for invalid_snapshot, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(NormalizationError) as selector_error:
                    select_bcs_short_call(
                        long_call_selection=long_call_selection,
                        snapshot=invalid_snapshot,
                        deltas=deltas,
                    )
                self.assertEqual(selector_error.exception.reason_code, reason)
                with self.assertRaises(NormalizationError) as price_error:
                    price_bcs_entry(
                        selection=selection,
                        snapshot=invalid_snapshot,
                        fees=fee_schedule(),
                        quantity=1,
                    )
                self.assertEqual(price_error.exception.reason_code, reason)


class BcsCostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot, self.long_call_selection, self.deltas = standard_market()
        self.selection = select_bcs_short_call(
            long_call_selection=self.long_call_selection,
            snapshot=self.snapshot,
            deltas=self.deltas,
        )
        self.fees = fee_schedule()

    def test_entry_uses_long_ask_short_bid_all_fees_and_both_adverse_ticks(self) -> None:
        result = price_bcs_entry(
            selection=self.selection,
            snapshot=self.snapshot,
            fees=self.fees,
            quantity=2,
        )
        self.assertIsInstance(result, BcsEntryCostV1)
        self.assertEqual(result.bcs_selection_sha256, self.selection.selection_sha256)
        self.assertEqual(result.base_net_debit_nano_usd, 1_200_600_000_000)
        self.assertEqual(result.stressed_net_debit_nano_usd, 1_204_600_000_000)
        self.assertEqual(result.max_loss_nano_usd, result.base_net_debit_nano_usd)
        self.assertEqual(
            result.theoretical_expiry_max_profit_nano_usd,
            799_400_000_000,
        )
        self.assertTrue(result.theoretical_expiry_only)

    def test_exit_uses_long_bid_short_ask_all_fees_and_both_adverse_ticks(self) -> None:
        episode, later, management = episode_and_management(
            self.snapshot,
            self.selection,
            self.fees,
            quantity=2,
        )
        result = price_bcs_exit(
            selection=self.selection,
            episode=episode,
            management_snapshot=management,
            snapshot=later,
            fees=self.fees,
            quantity=2,
        )
        self.assertIsInstance(result, BcsExitCostV1)
        self.assertEqual(result.bcs_selection_sha256, self.selection.selection_sha256)
        self.assertEqual(result.base_net_credit_nano_usd, 799_200_000_000)
        self.assertEqual(result.stressed_net_credit_nano_usd, 795_200_000_000)

    def test_entry_and_exit_require_the_relevant_side_size(self) -> None:
        entry_snapshot, long_call_selection, deltas = standard_market(
            long_ask_size=1,
            short_bid_size=1,
        )
        entry_selection = select_bcs_short_call(
            long_call_selection=long_call_selection,
            snapshot=entry_snapshot,
            deltas=deltas,
        )
        with self.assertRaises(NormalizationError) as raised:
            price_bcs_entry(
                selection=entry_selection,
                snapshot=entry_snapshot,
                fees=self.fees,
                quantity=2,
            )
        self.assertEqual(raised.exception.reason_code, "BCS_SIZE_INSUFFICIENT")

        exit_snapshot, long_call_selection, deltas = standard_market(
            long_bid_size=1,
            short_ask_size=1,
        )
        exit_selection = select_bcs_short_call(
            long_call_selection=long_call_selection,
            snapshot=exit_snapshot,
            deltas=deltas,
        )
        episode, later, management = episode_and_management(
            exit_snapshot,
            exit_selection,
            self.fees,
            quantity=2,
        )
        with self.assertRaises(NormalizationError) as raised:
            price_bcs_exit(
                selection=exit_selection,
                episode=episode,
                management_snapshot=management,
                snapshot=later,
                fees=self.fees,
                quantity=2,
            )
        self.assertEqual(raised.exception.reason_code, "BCS_SIZE_INSUFFICIENT")

    def test_invalid_or_width_exceeding_debit_fails_closed(self) -> None:
        for long_ask, short_bid in ((10 * NANO, 11 * NANO), (16 * NANO, NANO)):
            with self.subTest(long_ask=long_ask, short_bid=short_bid):
                snapshot, long_call_selection, deltas = standard_market(
                    long_bid=9 * NANO,
                    long_ask=long_ask,
                    short_bid=short_bid,
                    short_ask=short_bid + NANO,
                )
                selection = select_bcs_short_call(
                    long_call_selection=long_call_selection,
                    snapshot=snapshot,
                    deltas=deltas,
                )
                with self.assertRaises(NormalizationError) as raised:
                    price_bcs_entry(
                        selection=selection,
                        snapshot=snapshot,
                        fees=self.fees,
                        quantity=1,
                    )
                self.assertEqual(
                    raised.exception.reason_code,
                    "BCS_INVALID_NET_DEBIT",
                )

    def test_fee_schedule_is_hash_bound_nonnegative_and_required(self) -> None:
        for changes in (
            {"source_receipt_sha256": "bad"},
            {"effective_from_utc_ns": 0},
            {"long_entry_fee_nano_usd_per_contract": -1},
            {"short_exit_fee_nano_usd_per_contract": True},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(NormalizationError) as raised:
                    fee_schedule(**changes)
                self.assertEqual(
                    raised.exception.reason_code,
                    "BCS_FEE_SCHEDULE_MISSING",
                )

        with self.assertRaises(NormalizationError) as raised:
            price_bcs_entry(
                selection=self.selection,
                snapshot=self.snapshot,
                fees=None,  # type: ignore[arg-type]
                quantity=1,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "BCS_FEE_SCHEDULE_MISSING",
        )

        with self.assertRaises(NormalizationError) as raised:
            price_bcs_entry(
                selection=self.selection,
                snapshot=self.snapshot,
                fees=fee_schedule(effective_from_utc_ns=CAPTURE_NS + 1),
                quantity=1,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "BCS_FEE_SCHEDULE_MISSING",
        )


class BcsManagementSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot, long_binding, deltas = standard_market()
        self.selection = select_bcs_short_call(
            long_call_selection=long_binding,
            snapshot=self.snapshot,
            deltas=deltas,
        )
        self.fees = fee_schedule()
        self.entry_cost = price_bcs_entry(
            selection=self.selection,
            snapshot=self.snapshot,
            fees=self.fees,
            quantity=1,
        )
        self.episode = open_bcs_trade_episode_for_research(
            episode_id="gld-bcs-management-test",
            signal=signal_snapshot(),
            selection=self.selection,
            entry_snapshot=self.snapshot,
            entry_cost=self.entry_cost,
        )

    def test_management_snapshot_is_later_episode_bound_and_revalidated_on_exit(self) -> None:
        with self.assertRaises(NormalizationError) as same_capture:
            bind_bcs_management_snapshot_for_research(
                episode=self.episode,
                selection=self.selection,
                snapshot=self.snapshot,
            )
        self.assertEqual(
            same_capture.exception.reason_code,
            "BCS_MANAGEMENT_SNAPSHOT_BINDING_MISMATCH",
        )

        later = later_option_snapshot(self.snapshot)
        checkpoint = bind_bcs_management_snapshot_for_research(
            episode=self.episode,
            selection=self.selection,
            snapshot=later,
        )
        self.assertNotIn(
            "quote_age_limit_ns",
            inspect.signature(
                bind_bcs_management_snapshot_for_research
            ).parameters,
        )
        self.assertNotIn(
            "receive_skew_limit_ns",
            inspect.signature(
                bind_bcs_management_snapshot_for_research
            ).parameters,
        )
        self.assertEqual(
            checkpoint.quote_quality_policy_version,
            QUOTE_QUALITY_POLICY_VERSION,
        )
        self.assertEqual(
            checkpoint.quote_quality_policy_sha256,
            QUOTE_QUALITY_POLICY_SHA256,
        )
        with self.assertRaises(NormalizationError) as policy_mismatch:
            replace(
                checkpoint,
                quote_quality_policy_sha256=SHA_A,
            )
        self.assertEqual(
            policy_mismatch.exception.reason_code,
            "QUOTE_QUALITY_POLICY_MISMATCH",
        )
        other_episode = replace(self.episode, episode_id="different-episode")
        other_checkpoint = bind_bcs_management_snapshot_for_research(
            episode=other_episode,
            selection=self.selection,
            snapshot=later,
        )
        with self.assertRaises(NormalizationError) as wrong_episode:
            price_bcs_exit(
                selection=self.selection,
                episode=self.episode,
                management_snapshot=other_checkpoint,
                snapshot=later,
                fees=self.fees,
                quantity=1,
            )
        self.assertEqual(
            wrong_episode.exception.reason_code,
            "BCS_MANAGEMENT_SNAPSHOT_BINDING_MISMATCH",
        )

        result = price_bcs_exit(
            selection=self.selection,
            episode=self.episode,
            management_snapshot=checkpoint,
            snapshot=later,
            fees=self.fees,
            quantity=1,
        )
        self.assertEqual(result.option_snapshot_sha256, later.snapshot_sha256)
        self.assertEqual(result.bcs_selection_sha256, self.selection.selection_sha256)

    def test_stale_skew_or_halted_management_pair_fails_closed(self) -> None:
        cases = (
            (
                later_option_snapshot(
                    self.snapshot,
                    receive_offsets_ns=(6_000_000_000, 100_000_000, 100_000_000),
                ),
                "QUOTE_STALE",
            ),
            (
                later_option_snapshot(
                    self.snapshot,
                    receive_offsets_ns=(100_000_000, 100_000_000, 1_200_000_000),
                ),
                "CROSS_LEG_SKEW_EXCEEDED",
            ),
            (
                later_option_snapshot(
                    self.snapshot,
                    flags_by_leg=((), (), ("HALTED",)),
                ),
                "MARKET_HALTED",
            ),
        )
        for later, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(NormalizationError) as raised:
                    bind_bcs_management_snapshot_for_research(
                        episode=self.episode,
                        selection=self.selection,
                        snapshot=later,
                    )
                self.assertEqual(raised.exception.reason_code, reason)


class BcsLifecycleTests(unittest.TestCase):
    def lineage_fixture(self) -> tuple[
        BcsSelectionV1,
        BcsTradeEpisodeV1,
        BcsManagementSnapshotV1,
        BcsLifecycleTransitionV1,
        BcsLifecycleTransitionV1,
    ]:
        snapshot, long_binding, deltas = standard_market()
        selection = select_bcs_short_call(
            long_call_selection=long_binding,
            snapshot=snapshot,
            deltas=deltas,
        )
        episode, _, management = episode_and_management(
            snapshot,
            selection,
            fee_schedule(),
            quantity=1,
        )
        managed = next_bcs_lifecycle_state(
            current_state="PROPOSED",
            episode=episode,
            selection=selection,
            management_snapshot=management,
        )
        hold = next_bcs_lifecycle_state(
            current_state="MANAGED",
            episode=episode,
            selection=selection,
            previous_transition=managed,
        )
        return selection, episode, management, managed, hold

    def test_proposed_requires_matching_episode_selection_and_management(self) -> None:
        blocked = next_bcs_lifecycle_state(current_state="PROPOSED")
        self.assertEqual(blocked.next_state, "PROPOSED")
        self.assertEqual(
            blocked.blocking_reason,
            "MANAGEMENT_CHECKPOINT_REQUIRED",
        )

        snapshot, long_binding, deltas = standard_market()
        selection = select_bcs_short_call(
            long_call_selection=long_binding,
            snapshot=snapshot,
            deltas=deltas,
        )
        episode, _, management = episode_and_management(
            snapshot,
            selection,
            fee_schedule(),
            quantity=1,
        )
        managed = next_bcs_lifecycle_state(
            current_state="PROPOSED",
            episode=episode,
            selection=selection,
            management_snapshot=management,
        )
        self.assertEqual(managed.next_state, "MANAGED")
        self.assertEqual(
            managed.management_checkpoint_sha256,
            management.checkpoint_sha256,
        )
        self.assertEqual(managed.episode_sha256, episode.episode_sha256)
        self.assertEqual(
            managed.bcs_selection_sha256,
            selection.selection_sha256,
        )

        for invalid in (
            replace(management, episode_sha256=SHA_A),
            replace(
                management,
                capture_utc_ns=episode.entry_capture_utc_ns,
            ),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(NormalizationError) as raised:
                    next_bcs_lifecycle_state(
                        current_state="PROPOSED",
                        episode=episode,
                        selection=selection,
                        management_snapshot=invalid,
                    )
                self.assertEqual(
                    raised.exception.reason_code,
                    "BCS_MANAGEMENT_SNAPSHOT_BINDING_MISMATCH",
                )

    def test_exit_latch_has_fixed_priority_and_cannot_fall_back_to_hold(self) -> None:
        selection, episode, _, _, hold = self.lineage_fixture()
        transition = next_bcs_lifecycle_state(
            current_state="HOLD",
            events=(
                "H20",
                "SIGNAL_INVALIDATION",
                "EXPIRY_SAFETY",
                "POLICY_FULL_EXIT",
            ),
            episode=episode,
            selection=selection,
            previous_transition=hold,
        )
        self.assertIsInstance(transition, BcsLifecycleTransitionV1)
        self.assertEqual(transition.next_state, "EXIT_DUE")
        self.assertEqual(transition.latched_exit_reason, "POLICY_FULL_EXIT")

        due = next_bcs_lifecycle_state(
            current_state="EXIT_DUE",
            latched_exit_reason=transition.latched_exit_reason,
            episode=episode,
            selection=selection,
            previous_transition=transition,
        )
        self.assertEqual(due.next_state, "EXIT_DUE")
        self.assertEqual(due.latched_exit_reason, "POLICY_FULL_EXIT")
        self.assertEqual(due.blocking_reason, "EXIT_DUE_NONEXECUTABLE")

    def test_exit_latch_authority_cannot_be_changed_cleared_or_injected(self) -> None:
        selection, episode, management, _, hold = self.lineage_fixture()
        exit_due = next_bcs_lifecycle_state(
            current_state="HOLD",
            events=("POLICY_FULL_EXIT",),
            episode=episode,
            selection=selection,
            previous_transition=hold,
        )
        with self.assertRaises(NormalizationError) as changed_latch:
            next_bcs_lifecycle_state(
                current_state="EXIT_DUE",
                latched_exit_reason="H20",
                episode=episode,
                selection=selection,
                previous_transition=exit_due,
            )
        self.assertEqual(
            changed_latch.exception.reason_code,
            "BCS_LIFECYCLE_LATCH_INVALID",
        )

        blocked = next_bcs_lifecycle_state(
            current_state="HOLD",
            events=("POLICY_FULL_EXIT", "PARTIAL_FILL"),
            episode=episode,
            selection=selection,
            previous_transition=hold,
        )
        carried = next_bcs_lifecycle_state(
            current_state="RECONCILIATION_BLOCKED",
            episode=episode,
            selection=selection,
            previous_transition=blocked,
        )
        self.assertEqual(carried.latched_exit_reason, "POLICY_FULL_EXIT")

        with self.assertRaises(NormalizationError) as injected_latch:
            next_bcs_lifecycle_state(
                current_state="PROPOSED",
                latched_exit_reason="H20",
                episode=episode,
                selection=selection,
                management_snapshot=management,
            )
        self.assertEqual(
            injected_latch.exception.reason_code,
            "BCS_LIFECYCLE_LATCH_INVALID",
        )
        observed = next_bcs_lifecycle_state(
            current_state="PROPOSED",
            events=("H20",),
            episode=episode,
            selection=selection,
            management_snapshot=management,
        )
        self.assertEqual(observed.next_state, "EXIT_DUE")
        self.assertEqual(observed.latched_exit_reason, "H20")

    def test_proposed_retry_carries_observed_latch_until_management(self) -> None:
        selection, episode, management, managed, _ = self.lineage_fixture()
        first_attempt = next_bcs_lifecycle_state(
            current_state="PROPOSED",
            events=("H20",),
            episode=episode,
            selection=selection,
        )
        self.assertEqual(first_attempt.next_state, "PROPOSED")
        self.assertEqual(first_attempt.latched_exit_reason, "H20")
        self.assertEqual(
            first_attempt.blocking_reason,
            "MANAGEMENT_CHECKPOINT_REQUIRED",
        )

        retry = next_bcs_lifecycle_state(
            current_state="PROPOSED",
            episode=episode,
            selection=selection,
            previous_transition=first_attempt,
        )
        self.assertEqual(retry.next_state, "PROPOSED")
        self.assertEqual(retry.latched_exit_reason, "H20")
        self.assertEqual(
            retry.previous_transition_sha256,
            first_attempt.transition_sha256,
        )

        exit_due = next_bcs_lifecycle_state(
            current_state="PROPOSED",
            episode=episode,
            selection=selection,
            management_snapshot=management,
            previous_transition=retry,
        )
        self.assertEqual(exit_due.next_state, "EXIT_DUE")
        self.assertEqual(exit_due.latched_exit_reason, "H20")
        self.assertEqual(
            exit_due.management_checkpoint_sha256,
            management.checkpoint_sha256,
        )

        for invalid_previous in (
            managed,
            replace(first_attempt, episode_sha256=SHA_A),
            replace(first_attempt, bcs_selection_sha256=SHA_A),
        ):
            with self.subTest(invalid_previous=invalid_previous):
                with self.assertRaises(NormalizationError) as raised:
                    next_bcs_lifecycle_state(
                        current_state="PROPOSED",
                        episode=episode,
                        selection=selection,
                        previous_transition=invalid_previous,
                    )
                self.assertEqual(
                    raised.exception.reason_code,
                    "BCS_LIFECYCLE_LINEAGE_INVALID",
                )

    def test_post_proposed_states_reject_bare_or_mismatched_lineage(self) -> None:
        for state in ("MANAGED", "HOLD", "EXIT_DUE"):
            with self.subTest(state=state):
                arguments: dict[str, object] = {"current_state": state}
                if state == "EXIT_DUE":
                    arguments["latched_exit_reason"] = "POLICY_FULL_EXIT"
                with self.assertRaises(NormalizationError) as raised:
                    next_bcs_lifecycle_state(**arguments)  # type: ignore[arg-type]
                self.assertEqual(
                    raised.exception.reason_code,
                    "BCS_LIFECYCLE_LINEAGE_INVALID",
                )

        selection, episode, _, managed, hold = self.lineage_fixture()
        with self.assertRaises(NormalizationError) as wrong_previous_state:
            next_bcs_lifecycle_state(
                current_state="MANAGED",
                episode=episode,
                selection=selection,
                previous_transition=hold,
            )
        self.assertEqual(
            wrong_previous_state.exception.reason_code,
            "BCS_LIFECYCLE_LINEAGE_INVALID",
        )
        with self.assertRaises(NormalizationError) as wrong_episode:
            next_bcs_lifecycle_state(
                current_state="MANAGED",
                episode=replace(episode, episode_id="different-episode"),
                selection=selection,
                previous_transition=managed,
            )
        self.assertEqual(
            wrong_episode.exception.reason_code,
            "BCS_LIFECYCLE_LINEAGE_INVALID",
        )
        with self.assertRaises(NormalizationError) as stripped_previous:
            replace(hold, previous_transition_sha256=None)
        self.assertEqual(
            stripped_previous.exception.reason_code,
            "BCS_LIFECYCLE_LINEAGE_INVALID",
        )
        with self.assertRaises(NormalizationError) as stripped_management:
            replace(managed, management_checkpoint_sha256=None)
        self.assertEqual(
            stripped_management.exception.reason_code,
            "BCS_LIFECYCLE_LINEAGE_INVALID",
        )

    def test_public_lifecycle_cannot_claim_closing_or_closed_without_adapter(self) -> None:
        selection, episode, management, _, hold = self.lineage_fixture()
        exit_due = next_bcs_lifecycle_state(
            current_state="HOLD",
            events=("POLICY_FULL_EXIT",),
            episode=episode,
            selection=selection,
            previous_transition=hold,
        )
        with self.assertRaises(NormalizationError):
            next_bcs_lifecycle_state(
                current_state="EXIT_DUE",
                events=("EXECUTABLE_COMBO_AVAILABLE",),
                latched_exit_reason="POLICY_FULL_EXIT",
                episode=episode,
                selection=selection,
                previous_transition=exit_due,
            )
        with self.assertRaises(NormalizationError):
            next_bcs_lifecycle_state(current_state="CLOSING")

        due = next_bcs_lifecycle_state(
            current_state="EXIT_DUE",
            latched_exit_reason="POLICY_FULL_EXIT",
            episode=episode,
            selection=selection,
            management_snapshot=management,
            previous_transition=exit_due,
        )
        self.assertEqual(due.next_state, "EXIT_DUE")
        self.assertEqual(due.latched_exit_reason, "POLICY_FULL_EXIT")
        self.assertEqual(due.blocking_reason, "EXIT_DUE_NONEXECUTABLE")
        self.assertFalse(due.actionable)
        self.assertEqual(due.broker_order_count, 0)

        receipt = reconciliation_snapshot_b(episode, selection)
        blocked = next_bcs_lifecycle_state(
            current_state="EXIT_DUE",
            latched_exit_reason="POLICY_FULL_EXIT",
            episode=episode,
            selection=selection,
            reconciliation_snapshot_b=receipt,
            previous_transition=exit_due,
        )
        self.assertEqual(blocked.next_state, "RECONCILIATION_BLOCKED")
        self.assertEqual(blocked.latched_exit_reason, "POLICY_FULL_EXIT")
        self.assertEqual(
            blocked.blocking_reason,
            "RECONCILIATION_AUTHORITY_UNAVAILABLE",
        )
        self.assertEqual(
            blocked.reconciliation_receipt_sha256,
            receipt.receipt_sha256,
        )

    def test_partial_residual_assignment_exercise_or_unknown_blocks_reconciliation(self) -> None:
        selection, episode, _, managed, hold = self.lineage_fixture()
        events = (
            "PARTIAL_FILL",
            "RESIDUAL_POSITION",
            "ASSIGNMENT_DETECTED",
            "EXERCISE_DETECTED",
            "BROKER_STATE_UNKNOWN",
        )
        for event in events:
            with self.subTest(event=event):
                transition = next_bcs_lifecycle_state(
                    current_state="MANAGED",
                    events=(event,),
                    episode=episode,
                    selection=selection,
                    previous_transition=managed,
                )
                self.assertEqual(
                    transition.next_state,
                    "RECONCILIATION_BLOCKED",
                )
                self.assertEqual(
                    transition.blocking_reason,
                    event,
                )
                self.assertIsNone(transition.latched_exit_reason)

        simultaneous = next_bcs_lifecycle_state(
            current_state="HOLD",
            events=("POLICY_FULL_EXIT", "PARTIAL_FILL"),
            episode=episode,
            selection=selection,
            previous_transition=hold,
        )
        self.assertEqual(simultaneous.next_state, "RECONCILIATION_BLOCKED")
        self.assertEqual(simultaneous.latched_exit_reason, "POLICY_FULL_EXIT")
        self.assertEqual(simultaneous.blocking_reason, "PARTIAL_FILL")

    def test_typed_snapshot_b_checks_every_terminal_dimension_and_keeps_latch(self) -> None:
        selection, episode, _, _, hold = self.lineage_fixture()
        exit_due = next_bcs_lifecycle_state(
            current_state="HOLD",
            events=("SIGNAL_INVALIDATION",),
            episode=episode,
            selection=selection,
            previous_transition=hold,
        )
        cases = (
            ({"episode_sha256": SHA_A}, "RECONCILIATION_IDENTITY_MISMATCH"),
            ({"long_position_quantity": 1}, "RECONCILIATION_POSITION_MISMATCH"),
            ({"open_order_count": 1}, "RECONCILIATION_OPEN_ORDERS"),
            ({"pending_assignment": True}, "RECONCILIATION_PENDING_ASSIGNMENT"),
            ({"pending_exercise": True}, "RECONCILIATION_PENDING_EXERCISE"),
            (
                {"terminal_fees_final": False},
                "RECONCILIATION_TERMINAL_FEES_UNFINALIZED",
            ),
            ({}, "RECONCILIATION_AUTHORITY_UNAVAILABLE"),
        )
        for changes, reason in cases:
            with self.subTest(reason=reason):
                receipt = reconciliation_snapshot_b(episode, selection, **changes)
                transition = next_bcs_lifecycle_state(
                    current_state="EXIT_DUE",
                    latched_exit_reason="SIGNAL_INVALIDATION",
                    episode=episode,
                    selection=selection,
                    reconciliation_snapshot_b=receipt,
                    previous_transition=exit_due,
                )
                self.assertEqual(transition.next_state, "RECONCILIATION_BLOCKED")
                self.assertEqual(transition.latched_exit_reason, "SIGNAL_INVALIDATION")
                self.assertEqual(transition.blocking_reason, reason)
                self.assertEqual(
                    transition.reconciliation_receipt_sha256,
                    receipt.receipt_sha256,
                )
                self.assertFalse(transition.actionable)
                self.assertEqual(transition.broker_order_count, 0)

        with self.assertRaises(NormalizationError) as fake_authority:
            reconciliation_snapshot_b(
                episode,
                selection,
                authority_status="QUALIFIED",
            )
        self.assertEqual(
            fake_authority.exception.reason_code,
            "RECONCILIATION_AUTHORITY_UNAVAILABLE",
        )


class BcsResearchEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signal = signal_snapshot()
        long_quote = option_quote(300, bid=9 * NANO, ask=10 * NANO)
        short_quote = option_quote(310, bid=4 * NANO, ask=5 * NANO)
        self.snapshot = option_snapshot((long_quote, short_quote), signal=self.signal)
        self.long_call_selection = lc0_binding(
            self.snapshot,
            long_quote.contract.occ_symbol,
            signal=self.signal,
        )
        self.deltas = deltas_for(self.snapshot, (650_000, 250_000))
        self.fees = fee_schedule()

    def evaluate(self, **changes: object) -> BcsEvaluationV1:
        values: dict[str, object] = {
            "signal": self.signal,
            "candidate_ledger": (self.snapshot,),
            "long_call_selection": self.long_call_selection,
            "deltas": self.deltas,
            "fees": self.fees,
            "quantity": 1,
        }
        if "snapshot" in changes:
            snapshot = changes.pop("snapshot")
            changes["candidate_ledger"] = (
                () if snapshot is None else (snapshot,)
            )
        values.update(changes)
        return evaluate_bcs_research_synthetic(**values)

    def test_signal_fail_returns_no_entry_without_notification(self) -> None:
        result = self.evaluate(
            signal=signal_snapshot("FAIL"),
            snapshot=None,
            deltas=(),
            fees=None,
        )
        self.assertEqual(result.status, "NO_ENTRY")
        self.assertFalse(result.actionable)
        self.assertEqual(result.broker_order_count, 0)
        self.assertEqual(result.blocking_notifications, ())

    def test_not_evaluable_or_pipeline_failure_returns_one_deduped_notification(self) -> None:
        not_evaluable = self.evaluate(
            signal=signal_snapshot("NOT_EVALUABLE"),
            snapshot=None,
        )
        self.assertEqual(not_evaluable.status, "NO_DECISION")
        self.assertEqual(len(not_evaluable.blocking_notifications), 1)
        self.assertIsInstance(
            not_evaluable.blocking_notifications[0],
            BlockingNotificationV1,
        )

        missing_fees = self.evaluate(fees=None)
        repeated = self.evaluate(fees=None)
        self.assertEqual(missing_fees.status, "NO_DECISION")
        self.assertEqual(missing_fees.reason_code, "BCS_FEE_SCHEDULE_MISSING")
        self.assertEqual(len(missing_fees.blocking_notifications), 1)
        self.assertEqual(missing_fees, repeated)

    def test_ibkr_quote_unavailable_reason_and_message_key_are_fixed(self) -> None:
        result = self.evaluate(
            snapshot=None,
        )
        self.assertEqual(result.status, "NO_DECISION")
        self.assertEqual(result.reason_code, "IBKR_EXECUTABLE_QUOTE_UNAVAILABLE")
        self.assertEqual(len(result.blocking_notifications), 1)
        self.assertEqual(
            result.blocking_notifications[0].message_key,
            "gld.bcs.ibkr_executable_quote_unavailable",
        )

    def test_candidate_ledger_rejects_snapshot_from_another_signal(self) -> None:
        other_signal = SignalSnapshotV1(
            signal_id="other-signal",
            trading_date=self.signal.trading_date,
            rule_version=self.signal.rule_version,
            rule_sha256=self.signal.rule_sha256,
            calendar_authority_id=self.signal.calendar_authority_id,
            calendar_version=self.signal.calendar_version,
            calendar_sha256=self.signal.calendar_sha256,
            phase_receipt_sha256=self.signal.phase_receipt_sha256,
            completeness_receipt_sha256=self.signal.completeness_receipt_sha256,
            cutoff_utc_ns=self.signal.cutoff_utc_ns,
            max_event_utc_ns=self.signal.max_event_utc_ns,
            max_receive_utc_ns=self.signal.max_receive_utc_ns,
            signal_state="PASS",
            input_fact_sha256=self.signal.input_fact_sha256,
        )
        mismatched = option_snapshot(
            self.snapshot.option_quotes,
            signal=other_signal,
        )
        with self.assertRaises(NormalizationError) as raised:
            build_option_snapshot_candidate_ledger(
                (mismatched,),
                signal_snapshot=self.signal,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "OPTION_SNAPSHOT_BINDING_MISMATCH",
        )

    def test_stale_structural_snapshot_cannot_become_a_research_candidate(self) -> None:
        stale_book = replace(
            self.snapshot.underlying_top,
            ts_event_ns=self.snapshot.capture_utc_ns - 6_100_000_000,
            ts_recv_ns=self.snapshot.capture_utc_ns - 6_000_000_000,
        )
        stale_snapshot = replace(self.snapshot, underlying_top=stale_book)

        result = self.evaluate(snapshot=stale_snapshot)

        self.assertEqual(result.status, "NO_DECISION")
        self.assertEqual(
            result.reason_code,
            "EXECUTABLE_OPTION_SNAPSHOT_UNAVAILABLE",
        )

    def test_candidate_ledger_selects_once_and_uses_second_qualified_ordinal(self) -> None:
        stale_book = replace(
            self.snapshot.underlying_top,
            ts_event_ns=self.snapshot.capture_utc_ns - 6_100_000_000,
            ts_recv_ns=self.snapshot.capture_utc_ns - 6_000_000_000,
        )
        ordinal_one = replace(self.snapshot, underlying_top=stale_book)
        ordinal_two = option_snapshot(
            self.snapshot.option_quotes,
            signal=self.signal,
            capture_utc_ns=CAPTURE_NS + 1_000_000_000,
            candidate_ordinal=2,
        )
        ledger = build_option_snapshot_candidate_ledger(
            (ordinal_one, ordinal_two),
            signal_snapshot=self.signal,
        )
        long_selection = lc0_binding(
            ordinal_two,
            ordinal_two.option_quotes[0].contract.occ_symbol,
            signal=self.signal,
        )
        deltas = deltas_for(ordinal_two, (650_000, 250_000))
        original_selector = bcs_module.select_first_complete_option_snapshot
        with (
            patch.object(
                bcs_module,
                "is_verified_crr_delta_result",
                return_value=True,
            ),
            patch.object(
                bcs_module,
                "select_first_complete_option_snapshot",
                wraps=original_selector,
            ) as selector_spy,
        ):
            result = evaluate_bcs_research(
                signal=self.signal,
                candidate_ledger=ledger,
                long_call_selection=long_selection,
                deltas=deltas,
                fees=self.fees,
                quantity=1,
            )

        self.assertEqual(selector_spy.call_count, 1)
        self.assertIs(selector_spy.call_args.args[0], ledger)
        self.assertEqual(result.status, "RESEARCH_CANDIDATE")
        self.assertEqual(
            result.option_snapshot_sha256,
            ordinal_two.snapshot_sha256,
        )

    def test_structurally_valid_candidate_is_explicitly_not_actionable(self) -> None:
        result = self.evaluate()
        self.assertEqual(result.status, "RESEARCH_CANDIDATE")
        self.assertEqual(result.reason_code, "RESEARCH_ONLY_NOT_ACTIONABLE")
        self.assertFalse(result.actionable)
        self.assertEqual(result.broker_order_count, 0)
        self.assertIsNotNone(result.selection)
        self.assertIsNotNone(result.entry_cost)
        self.assertEqual(result.blocking_notifications, ())
        for item in (result, result.selection, result.entry_cost):
            self.assertTrue(hasattr(type(item), "__slots__"))
            self.assertGreater(len(fields(item)), 0)

    def test_no_broker_provider_order_roll_or_notification_send_seams_exist(self) -> None:
        banned = {
            "from_ibkr",
            "from_robinhood",
            "create_order",
            "submit_order",
            "send_notification",
            "roll_bcs",
            "replace_leg",
        }
        module_names = set(dir(bcs_module))
        package_names = set(dir(__import__("gld_research_core")))
        self.assertTrue(banned.isdisjoint(module_names))
        self.assertTrue(banned.isdisjoint(package_names))
        public_functions = {
            name
            for name in bcs_module.__all__
            if inspect.isfunction(getattr(bcs_module, name))
        }
        self.assertEqual(
            public_functions,
            {
                "bind_lc0_selection_for_research",
                "bind_bcs_management_snapshot_for_research",
                "build_decision_artifact",
                "evaluate_bcs_research",
                "is_verified_bcs_evaluation",
                "next_bcs_lifecycle_state",
                "open_bcs_trade_episode_for_research",
                "price_bcs_entry",
                "price_bcs_exit",
                "select_bcs_short_call",
            },
        )


class DecisionArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signal = signal_snapshot()
        long_quote = option_quote(300, bid=9 * NANO, ask=10 * NANO)
        short_quote = option_quote(310, bid=4 * NANO, ask=5 * NANO)
        self.snapshot = option_snapshot(
            (long_quote, short_quote),
            signal=self.signal,
        )
        self.fees = fee_schedule()
        self.long_call_selection = lc0_binding(
            self.snapshot,
            long_quote.contract.occ_symbol,
            signal=self.signal,
        )
        self.deltas = deltas_for(self.snapshot, (650_000, 250_000))

    def build(self, **changes: object) -> DecisionArtifactV1:
        values: dict[str, object] = {
            "signal": self.signal,
            "candidate_ledger": (self.snapshot,),
            "long_call_selection": self.long_call_selection,
            "deltas": self.deltas,
            "research_contract_sha256": SHA_B,
            "rule_package_version": self.signal.rule_version,
            "fees": self.fees,
            "quantity": 1,
        }
        values.update(changes)
        with patch.object(
            bcs_module,
            "is_verified_crr_delta_result",
            return_value=True,
        ):
            return build_decision_artifact(**values)  # type: ignore[arg-type]

    def test_missing_lc0_authority_makes_owner_selection_unreachable(self) -> None:
        artifact = self.build()
        repeated = self.build()
        self.assertIsInstance(artifact, DecisionArtifactV1)
        self.assertEqual(artifact.status, "NO_DECISION")
        self.assertEqual(artifact.reason_code, "CARRIER_EVALUATION_INCOMPLETE")
        self.assertFalse(artifact.owner_selection_required)
        self.assertFalse(artifact.actionable)
        self.assertEqual(artifact.broker_order_count, 0)
        self.assertEqual(artifact.lc0_terminal.terminal_status, "NO_DECISION")
        self.assertEqual(
            artifact.lc0_terminal.reason_code,
            "LC0_AUTHORITY_NOT_IMPLEMENTED",
        )
        self.assertEqual(artifact.bcs0_terminal.terminal_status, "PASS")
        with self.assertRaises(NormalizationError) as fake_lc0_pass:
            replace(
                artifact.lc0_terminal,
                terminal_status="PASS",
                reason_code="RESEARCH_ONLY_NOT_ACTIONABLE",
            )
        self.assertEqual(
            fake_lc0_pass.exception.reason_code,
            "CARRIER_TERMINAL_RESULT_INVALID",
        )
        with self.assertRaises(NormalizationError) as inconsistent_bcs:
            replace(artifact.bcs0_terminal, terminal_status="FAIL")
        self.assertEqual(
            inconsistent_bcs.exception.reason_code,
            "CARRIER_TERMINAL_RESULT_INVALID",
        )
        self.assertEqual(artifact.signal_snapshot_sha256, self.signal.snapshot_sha256)
        self.assertEqual(artifact.option_snapshot_sha256, self.snapshot.snapshot_sha256)
        self.assertEqual(artifact.rule_package_version, self.signal.rule_version)
        self.assertEqual(artifact.rule_sha256, self.signal.rule_sha256)
        self.assertEqual(artifact.delta_model_sha256, MODEL_SHA256)
        self.assertEqual(artifact.fee_schedule_sha256, self.fees.fee_schedule_sha256)
        selection = select_bcs_short_call(
            long_call_selection=self.long_call_selection,
            snapshot=self.snapshot,
            deltas=self.deltas,
        )
        self.assertEqual(artifact.bcs_selection_sha256, selection.selection_sha256)
        self.assertEqual(
            artifact.long_delta_evidence_sha256,
            selection.long_delta_evidence_sha256,
        )
        self.assertEqual(
            artifact.short_delta_evidence_sha256,
            selection.short_delta_evidence_sha256,
        )
        self.assertEqual(artifact.long_delta_run_sha256, selection.long_delta_run_sha256)
        self.assertEqual(artifact.short_delta_run_sha256, selection.short_delta_run_sha256)
        self.assertEqual(
            artifact.long_delta_runtime_fingerprint_sha256,
            selection.long_delta_runtime_fingerprint_sha256,
        )
        self.assertEqual(
            artifact.short_delta_runtime_fingerprint_sha256,
            selection.short_delta_runtime_fingerprint_sha256,
        )
        self.assertEqual(artifact, repeated)
        self.assertEqual(artifact.artifact_sha256, repeated.artifact_sha256)
        with self.assertRaises(FrozenInstanceError):
            artifact.status = "NO_ENTRY"  # type: ignore[misc]
        with self.assertRaises(NormalizationError) as fake_owner:
            replace(
                artifact,
                status="OWNER_SELECTION_REQUIRED",
                reason_code="BOTH_CARRIERS_PASS",
                owner_selection_required=True,
            )
        self.assertEqual(
            fake_owner.exception.reason_code,
            "DECISION_ARTIFACT_INVALID",
        )
        with self.assertRaises(NormalizationError) as non_bool:
            replace(artifact, owner_selection_required=0)
        self.assertEqual(
            non_bool.exception.reason_code,
            "DECISION_ARTIFACT_INVALID",
        )
        with self.assertRaises(NormalizationError) as collapsed_run:
            replace(
                artifact,
                short_delta_run_sha256=artifact.long_delta_run_sha256,
            )
        self.assertEqual(
            collapsed_run.exception.reason_code,
            "DECISION_ARTIFACT_INVALID",
        )

    def test_full_candidate_ledger_changes_selection_evaluation_and_artifact_hashes(
        self,
    ) -> None:
        ordinal_two = option_snapshot(
            self.snapshot.option_quotes,
            signal=self.signal,
            capture_utc_ns=CAPTURE_NS + 1_000_000_000,
            candidate_ordinal=2,
            candidate_provenance_sha256=SHA_C,
        )
        stale_a = replace(
            self.snapshot.underlying_top,
            ts_event_ns=CAPTURE_NS - 6_100_000_000,
            ts_recv_ns=CAPTURE_NS - 6_000_000_000,
        )
        stale_b = replace(
            self.snapshot.underlying_top,
            ts_event_ns=CAPTURE_NS - 7_100_000_000,
            ts_recv_ns=CAPTURE_NS - 7_000_000_000,
        )
        rejected_a = replace(
            self.snapshot,
            underlying_top=stale_a,
            candidate_provenance_sha256=SHA_A,
        )
        rejected_b = replace(
            self.snapshot,
            underlying_top=stale_b,
            candidate_provenance_sha256=SHA_A,
        )
        ledger_a = build_option_snapshot_candidate_ledger(
            (rejected_a, ordinal_two),
            signal_snapshot=self.signal,
        )
        ledger_b = build_option_snapshot_candidate_ledger(
            (rejected_b, ordinal_two),
            signal_snapshot=self.signal,
        )
        long_selection = lc0_binding(
            ordinal_two,
            ordinal_two.option_quotes[0].contract.occ_symbol,
            signal=self.signal,
        )
        deltas = deltas_for(ordinal_two, (650_000, 250_000))
        evaluation_a = evaluate_bcs_research_synthetic(
            signal=self.signal,
            candidate_ledger=ledger_a,
            long_call_selection=long_selection,
            deltas=deltas,
            fees=self.fees,
            quantity=1,
        )
        evaluation_b = evaluate_bcs_research_synthetic(
            signal=self.signal,
            candidate_ledger=ledger_b,
            long_call_selection=long_selection,
            deltas=deltas,
            fees=self.fees,
            quantity=1,
        )
        self.assertEqual(
            evaluation_a.option_snapshot_sha256,
            evaluation_b.option_snapshot_sha256,
        )
        self.assertEqual(evaluation_a.candidate_ledger_sha256, ledger_a.ledger_sha256)
        self.assertEqual(evaluation_b.candidate_ledger_sha256, ledger_b.ledger_sha256)
        self.assertIsNotNone(evaluation_a.selection)
        self.assertIsNotNone(evaluation_b.selection)
        assert evaluation_a.selection is not None
        assert evaluation_b.selection is not None
        self.assertNotEqual(
            evaluation_a.selection.selection_sha256,
            evaluation_b.selection.selection_sha256,
        )
        self.assertNotEqual(
            canonical_snapshot_sha256(evaluation_a),
            canonical_snapshot_sha256(evaluation_b),
        )
        artifact_a = self.build(
            candidate_ledger=ledger_a.candidates,
            long_call_selection=long_selection,
            deltas=deltas,
        )
        artifact_b = self.build(
            candidate_ledger=ledger_b.candidates,
            long_call_selection=long_selection,
            deltas=deltas,
        )
        self.assertEqual(artifact_a.candidate_ledger_sha256, ledger_a.ledger_sha256)
        self.assertEqual(artifact_b.candidate_ledger_sha256, ledger_b.ledger_sha256)
        self.assertNotEqual(artifact_a.artifact_sha256, artifact_b.artifact_sha256)

        with self.assertRaises(NormalizationError) as not_a_member:
            select_bcs_short_call(
                long_call_selection=self.long_call_selection,
                candidate_ledger=ledger_a,
                snapshot=self.snapshot,
                deltas=self.deltas,
            )
        self.assertEqual(
            not_a_member.exception.reason_code,
            "BCS_CANDIDATE_LEDGER_BINDING_MISMATCH",
        )
        with self.assertRaises(NormalizationError) as mismatched_evaluation:
            replace(
                evaluation_a,
                selection=replace(
                    evaluation_a.selection,
                    candidate_ledger_sha256=ledger_b.ledger_sha256,
                ),
            )
        self.assertEqual(
            mismatched_evaluation.exception.reason_code,
            "BCS_EVALUATION_INVALID",
        )

    def test_builder_uses_typed_evaluator_and_no_decision_precedes_other_states(self) -> None:
        self.assertNotIn(
            "carrier_terminal_result_for_research",
            bcs_module.__all__,
        )
        self.assertFalse(
            hasattr(bcs_module, "carrier_terminal_result_for_research")
        )
        self.assertNotIn(
            "lc0_terminal",
            inspect.signature(build_decision_artifact).parameters,
        )
        self.assertNotIn(
            "bcs0_evaluation",
            inspect.signature(build_decision_artifact).parameters,
        )

        evaluation = evaluate_bcs_research_synthetic(
            signal=self.signal,
            candidate_ledger=(self.snapshot,),
            long_call_selection=self.long_call_selection,
            deltas=self.deltas,
            fees=self.fees,
            quantity=1,
        )
        self.assertTrue(is_verified_bcs_evaluation(evaluation))
        self.assertFalse(is_verified_bcs_evaluation(replace(evaluation)))

        with (
            patch.object(
                bcs_module,
                "evaluate_bcs_research",
                side_effect=AssertionError("mutable evaluator global resolved"),
            ),
            patch.object(
                bcs_module,
                "is_verified_bcs_evaluation",
                return_value=False,
            ),
        ):
            captured_engine_artifact = self.build()
        self.assertEqual(captured_engine_artifact.status, "NO_DECISION")

        fail_signal = signal_snapshot("FAIL")
        fail_snapshot = option_snapshot(
            self.snapshot.option_quotes,
            signal=fail_signal,
        )
        fail_artifact = self.build(
            signal=fail_signal,
            candidate_ledger=(),
            long_call_selection=lc0_binding(
                fail_snapshot,
                fail_snapshot.option_quotes[0].contract.occ_symbol,
                signal=fail_signal,
            ),
            deltas=(),
            rule_package_version=fail_signal.rule_version,
        )
        self.assertEqual(fail_artifact.status, "NO_DECISION")
        self.assertEqual(
            fail_artifact.reason_code,
            "CARRIER_EVALUATION_INCOMPLETE",
        )
        self.assertEqual(fail_artifact.bcs0_terminal.terminal_status, "FAIL")
        self.assertIsNone(fail_artifact.option_snapshot_sha256)
        self.assertIsNone(fail_artifact.bcs_selection_sha256)

        self.assertEqual(
            bcs_module._decision_artifact_outcome("PASS", "NO_DECISION"),
            ("NO_DECISION", "CARRIER_EVALUATION_INCOMPLETE", False),
        )
        self.assertEqual(
            bcs_module._decision_artifact_outcome("PASS", "PASS"),
            ("OWNER_SELECTION_REQUIRED", "BOTH_CARRIERS_PASS", True),
        )

        with self.assertRaises(NormalizationError) as wrong_version:
            self.build(rule_package_version="wrong-version")
        self.assertEqual(
            wrong_version.exception.reason_code,
            "DECISION_ARTIFACT_INVALID",
        )
        with self.assertRaises(NormalizationError) as non_text_version:
            self.build(rule_package_version=True)
        self.assertEqual(
            non_text_version.exception.reason_code,
            "DECISION_ARTIFACT_INVALID",
        )


if __name__ == "__main__":
    unittest.main()
