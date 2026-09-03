"""Adverse one-tick synthetic execution and exit-priority kernels for R2.

All inputs are local value objects.  Missing ticks, incomplete quotes, or failed
quote-quality checks yield ``UNEVALUABLE`` and can never be converted into a
zero-return observation or a lower-priority exit assertion.
"""

from __future__ import annotations

from dataclasses import dataclass

from gld_entry_decision_f0.evidence import (
    EvidenceValidationError,
    derive_episode_after_cost,
    round_half_even_divide,
)

from .contracts import R2QualificationError


SYNTHETIC_ONLY = "SYNTHETIC_ONLY"
RESEARCH_ONLY = "RESEARCH_ONLY"
NO_DECISION_EFFECT = "NO_DECISION_EFFECT"
PPM = 1_000_000
_CARRIER_IDS = frozenset({"LC0", "BCS0"})
_HARD_STOPS = frozenset({333_333, 500_000, 666_667})


class _SyntheticResearchMarker:
    @property
    def classification(self) -> str:
        return SYNTHETIC_ONLY

    @property
    def scope(self) -> str:
        return RESEARCH_ONLY

    @property
    def authority_status(self) -> str:
        return NO_DECISION_EFFECT

    @property
    def actionable(self) -> bool:
        return False

    @property
    def broker_order_count(self) -> int:
        return 0


@dataclass(frozen=True, slots=True)
class QuoteObservationV1:
    """Minimal point-in-time quote inputs, allowing explicit missing values."""

    bid_nano_usd: int | None
    ask_nano_usd: int | None
    tick_nano_usd: int | None
    bid_size: int | None
    ask_size: int | None
    executable: bool | None
    quote_quality_pass: bool | None


@dataclass(frozen=True, slots=True)
class SideFeesV1:
    """Effective synthetic fees, already expressed per one complete contract."""

    long_entry_nano_usd: int
    long_exit_nano_usd: int
    short_entry_nano_usd: int
    short_exit_nano_usd: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (
                self.long_entry_nano_usd,
                self.long_exit_nano_usd,
                self.short_entry_nano_usd,
                self.short_exit_nano_usd,
            )
        ):
            raise R2QualificationError("R2_SIDE_FEE_INVALID")

    @classmethod
    def zero(cls) -> "SideFeesV1":
        """Return a zero-fee synthetic fixture, never a real fee authority."""

        return cls(0, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class StressedEpisodeResultV1(_SyntheticResearchMarker):
    """One adverse one-tick episode derivation or explicit unknown result."""

    carrier_id: str
    status: str
    reason_code: str
    stressed_long_entry_nano_usd: int | None = None
    stressed_long_exit_nano_usd: int | None = None
    stressed_short_entry_nano_usd: int | None = None
    stressed_short_exit_nano_usd: int | None = None
    opening_fees_nano_usd: int | None = None
    closing_fees_nano_usd: int | None = None
    entry_debit_nano_usd: int | None = None
    entry_after_cost_basis_nano_usd: int | None = None
    exit_value_nano_usd: int | None = None
    after_cost_profit_nano_usd: int | None = None
    after_cost_return_ppm: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "GLD_R2_SYNTHETIC_STRESSED_EPISODE_V1",
            "classification": self.classification,
            "scope": self.scope,
            "authority_status": self.authority_status,
            "actionable": self.actionable,
            "broker_order_count": self.broker_order_count,
            "carrier_id": self.carrier_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "stressed_long_entry_nano_usd": self.stressed_long_entry_nano_usd,
            "stressed_long_exit_nano_usd": self.stressed_long_exit_nano_usd,
            "stressed_short_entry_nano_usd": self.stressed_short_entry_nano_usd,
            "stressed_short_exit_nano_usd": self.stressed_short_exit_nano_usd,
            "opening_fees_nano_usd": self.opening_fees_nano_usd,
            "closing_fees_nano_usd": self.closing_fees_nano_usd,
            "entry_debit_nano_usd": self.entry_debit_nano_usd,
            "entry_after_cost_basis_nano_usd": (
                self.entry_after_cost_basis_nano_usd
            ),
            "exit_value_nano_usd": self.exit_value_nano_usd,
            "after_cost_profit_nano_usd": self.after_cost_profit_nano_usd,
            "after_cost_return_ppm": self.after_cost_return_ppm,
        }


def _quote_reason(quote: object) -> str | None:
    if type(quote) is not QuoteObservationV1:
        return "R2_STRESS_QUOTE_MISSING"
    values = (
        quote.bid_nano_usd,
        quote.ask_nano_usd,
        quote.tick_nano_usd,
        quote.bid_size,
        quote.ask_size,
    )
    if any(value is None for value in values):
        return "R2_STRESS_QUOTE_MISSING"
    if quote.executable is not True or quote.quote_quality_pass is not True:
        return "R2_STRESS_QUOTE_QUALITY_FAILED"
    bid, ask, tick, bid_size, ask_size = values
    if (
        any(type(value) is not int for value in values)
        or bid <= 0  # type: ignore[operator]
        or ask <= 0  # type: ignore[operator]
        or bid > ask  # type: ignore[operator]
        or tick <= 0  # type: ignore[operator]
        or bid_size <= 0  # type: ignore[operator]
        or ask_size <= 0  # type: ignore[operator]
    ):
        return "R2_STRESS_QUOTE_INVALID"
    return None


def _validate_carrier_quotes(
    *,
    carrier_id: str,
    long_quotes: tuple[object, ...],
    short_quotes: tuple[object | None, ...],
) -> str | None:
    if carrier_id not in _CARRIER_IDS:
        raise R2QualificationError("R2_CARRIER_ID_INVALID")
    for quote in long_quotes:
        reason = _quote_reason(quote)
        if reason is not None:
            return reason
    if carrier_id == "LC0":
        if any(quote is not None for quote in short_quotes):
            return "R2_STRESS_LC0_SHORT_LEG_FORBIDDEN"
        return None
    if any(quote is None for quote in short_quotes):
        return "R2_STRESS_BCS0_SHORT_LEG_MISSING"
    for quote in short_quotes:
        reason = _quote_reason(quote)
        if reason is not None:
            return reason
    return None


def _multiplier(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 100_000:
        raise R2QualificationError("R2_STRESS_MULTIPLIER_INVALID")
    return value


def derive_stressed_episode_after_cost(
    *,
    carrier_id: str,
    entry_long_quote: QuoteObservationV1,
    exit_long_quote: QuoteObservationV1,
    entry_short_quote: QuoteObservationV1 | None,
    exit_short_quote: QuoteObservationV1 | None,
    fees: SideFeesV1,
    multiplier: int,
    episode_id: str,
    fold_id: str,
    split: str,
    entry_utc_ns: int,
    exit_utc_ns: int,
    input_sha256: str,
) -> StressedEpisodeResultV1:
    """Apply adverse ticks then reuse R1's public after-cost return derivation."""

    reason = _validate_carrier_quotes(
        carrier_id=carrier_id,
        long_quotes=(entry_long_quote, exit_long_quote),
        short_quotes=(entry_short_quote, exit_short_quote),
    )
    if type(fees) is not SideFeesV1:
        raise R2QualificationError("R2_SIDE_FEE_INVALID")
    contract_multiplier = _multiplier(multiplier)
    if reason is not None:
        return StressedEpisodeResultV1(
            carrier_id=carrier_id,
            status="UNEVALUABLE",
            reason_code=reason,
        )
    assert entry_long_quote.ask_nano_usd is not None
    assert entry_long_quote.tick_nano_usd is not None
    assert exit_long_quote.bid_nano_usd is not None
    assert exit_long_quote.tick_nano_usd is not None
    stressed_long_entry = (
        entry_long_quote.ask_nano_usd + entry_long_quote.tick_nano_usd
    )
    stressed_long_exit = max(
        0,
        exit_long_quote.bid_nano_usd - exit_long_quote.tick_nano_usd,
    )
    if carrier_id == "LC0":
        stressed_short_entry = None
        stressed_short_exit = None
        opening_fees = fees.long_entry_nano_usd
        closing_fees = fees.long_exit_nano_usd
    else:
        assert entry_short_quote is not None
        assert exit_short_quote is not None
        assert entry_short_quote.bid_nano_usd is not None
        assert entry_short_quote.tick_nano_usd is not None
        assert exit_short_quote.ask_nano_usd is not None
        assert exit_short_quote.tick_nano_usd is not None
        stressed_short_entry = max(
            0,
            entry_short_quote.bid_nano_usd - entry_short_quote.tick_nano_usd,
        )
        stressed_short_exit = (
            exit_short_quote.ask_nano_usd + exit_short_quote.tick_nano_usd
        )
        opening_fees = (
            fees.long_entry_nano_usd + fees.short_entry_nano_usd
        )
        closing_fees = fees.long_exit_nano_usd + fees.short_exit_nano_usd

    episode = {
        "episode_id": episode_id,
        "fold_id": fold_id,
        "split": split,
        "entry_utc_ns": entry_utc_ns,
        "exit_utc_ns": exit_utc_ns,
        "multiplier": contract_multiplier,
        "long_entry_ask_nano_usd": stressed_long_entry,
        "long_exit_bid_nano_usd": stressed_long_exit,
        "short_entry_bid_nano_usd": stressed_short_entry,
        "short_exit_ask_nano_usd": stressed_short_exit,
        "opening_fees_nano_usd": opening_fees,
        "closing_fees_nano_usd": closing_fees,
        "input_sha256": input_sha256,
    }
    try:
        derived = derive_episode_after_cost(episode, carrier_id)
    except EvidenceValidationError as exc:
        return StressedEpisodeResultV1(
            carrier_id=carrier_id,
            status="UNEVALUABLE",
            reason_code=exc.reason_code,
            stressed_long_entry_nano_usd=stressed_long_entry,
            stressed_long_exit_nano_usd=stressed_long_exit,
            stressed_short_entry_nano_usd=stressed_short_entry,
            stressed_short_exit_nano_usd=stressed_short_exit,
            opening_fees_nano_usd=opening_fees,
            closing_fees_nano_usd=closing_fees,
        )
    entry_debit = int(derived["entry_debit_nano_usd"])
    return StressedEpisodeResultV1(
        carrier_id=carrier_id,
        status="PASS",
        reason_code="R2_STRESSED_EPISODE_DERIVED",
        stressed_long_entry_nano_usd=stressed_long_entry,
        stressed_long_exit_nano_usd=stressed_long_exit,
        stressed_short_entry_nano_usd=stressed_short_entry,
        stressed_short_exit_nano_usd=stressed_short_exit,
        opening_fees_nano_usd=opening_fees,
        closing_fees_nano_usd=closing_fees,
        entry_debit_nano_usd=entry_debit,
        entry_after_cost_basis_nano_usd=entry_debit + opening_fees,
        exit_value_nano_usd=int(derived["exit_value_nano_usd"]),
        after_cost_profit_nano_usd=int(derived["after_cost_profit_nano_usd"]),
        after_cost_return_ppm=int(
            derived["after_cost_return_on_entry_debit_ppm"]
        ),
    )


@dataclass(frozen=True, slots=True)
class ExitCheckpointResultV1(_SyntheticResearchMarker):
    """One synthetic exit checkpoint with the frozen R2 priority order."""

    carrier_id: str
    status: str
    reason_code: str
    trigger_reason: str
    exit_due: bool
    stressed_exit_value_after_fees_nano_usd: int | None
    after_cost_loss_nano_usd: int | None
    after_cost_loss_ppm: int | None
    hard_stop_triggered: bool | None

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "GLD_R2_SYNTHETIC_EXIT_CHECKPOINT_V1",
            "classification": self.classification,
            "scope": self.scope,
            "authority_status": self.authority_status,
            "actionable": self.actionable,
            "broker_order_count": self.broker_order_count,
            "carrier_id": self.carrier_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "trigger_reason": self.trigger_reason,
            "exit_due": self.exit_due,
            "stressed_exit_value_after_fees_nano_usd": (
                self.stressed_exit_value_after_fees_nano_usd
            ),
            "after_cost_loss_nano_usd": self.after_cost_loss_nano_usd,
            "after_cost_loss_ppm": self.after_cost_loss_ppm,
            "hard_stop_triggered": self.hard_stop_triggered,
            "priority_order": [
                "HARD_STOP",
                "CONFIRMED_INVALIDATION",
                "H20",
                "HOLD",
            ],
        }


def evaluate_exit_checkpoint(
    *,
    carrier_id: str,
    entry_after_cost_basis_nano_usd: int,
    long_quote: QuoteObservationV1,
    short_quote: QuoteObservationV1 | None,
    fees: SideFeesV1,
    multiplier: int,
    hard_stop_loss_ppm: int | None,
    confirmed_invalidation: bool,
    h20_reached: bool,
) -> ExitCheckpointResultV1:
    """Evaluate ``HARD_STOP > INVALIDATION > H20 > HOLD`` fail closed."""

    if (
        type(entry_after_cost_basis_nano_usd) is not int
        or entry_after_cost_basis_nano_usd <= 0
        or type(confirmed_invalidation) is not bool
        or type(h20_reached) is not bool
    ):
        raise R2QualificationError("R2_EXIT_CHECKPOINT_INPUT_INVALID")
    if hard_stop_loss_ppm is not None and hard_stop_loss_ppm not in _HARD_STOPS:
        raise R2QualificationError("R2_HARD_STOP_THRESHOLD_INVALID")
    if type(fees) is not SideFeesV1:
        raise R2QualificationError("R2_SIDE_FEE_INVALID")
    contract_multiplier = _multiplier(multiplier)
    reason = _validate_carrier_quotes(
        carrier_id=carrier_id,
        long_quotes=(long_quote,),
        short_quotes=(short_quote,),
    )
    if reason is not None:
        return ExitCheckpointResultV1(
            carrier_id=carrier_id,
            status="UNEVALUABLE",
            reason_code=reason,
            trigger_reason="UNEVALUABLE",
            exit_due=False,
            stressed_exit_value_after_fees_nano_usd=None,
            after_cost_loss_nano_usd=None,
            after_cost_loss_ppm=None,
            hard_stop_triggered=None,
        )
    assert long_quote.bid_nano_usd is not None
    assert long_quote.tick_nano_usd is not None
    stressed_long_exit = max(
        0,
        long_quote.bid_nano_usd - long_quote.tick_nano_usd,
    )
    if carrier_id == "LC0":
        exit_value = (
            stressed_long_exit * contract_multiplier
            - fees.long_exit_nano_usd
        )
    else:
        assert short_quote is not None
        assert short_quote.ask_nano_usd is not None
        assert short_quote.tick_nano_usd is not None
        stressed_short_exit = (
            short_quote.ask_nano_usd + short_quote.tick_nano_usd
        )
        exit_value = (
            (stressed_long_exit - stressed_short_exit) * contract_multiplier
            - fees.long_exit_nano_usd
            - fees.short_exit_nano_usd
        )
    loss = max(0, entry_after_cost_basis_nano_usd - exit_value)
    loss_ppm = round_half_even_divide(
        loss * PPM,
        entry_after_cost_basis_nano_usd,
    )
    hard_stop = (
        hard_stop_loss_ppm is not None and loss_ppm >= hard_stop_loss_ppm
    )
    if hard_stop:
        trigger = "HARD_STOP"
    elif confirmed_invalidation:
        trigger = "CONFIRMED_INVALIDATION"
    elif h20_reached:
        trigger = "H20"
    else:
        trigger = "HOLD"
    return ExitCheckpointResultV1(
        carrier_id=carrier_id,
        status="PASS",
        reason_code=(
            "R2_EXIT_DUE" if trigger != "HOLD" else "R2_EXIT_HOLD"
        ),
        trigger_reason=trigger,
        exit_due=trigger != "HOLD",
        stressed_exit_value_after_fees_nano_usd=exit_value,
        after_cost_loss_nano_usd=loss,
        after_cost_loss_ppm=loss_ppm,
        hard_stop_triggered=hard_stop,
    )


__all__ = [
    "ExitCheckpointResultV1",
    "QuoteObservationV1",
    "SideFeesV1",
    "StressedEpisodeResultV1",
    "derive_stressed_episode_after_cost",
    "evaluate_exit_checkpoint",
]
