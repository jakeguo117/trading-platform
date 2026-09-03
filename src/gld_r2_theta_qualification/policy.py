"""Pure synthetic-only Gate, selector, preference, and q=1 policy kernels.

The module deliberately has no provider, file, network, broker, or account
integration.  It reproduces the fixed R1 decision semantics needed by R2 while
making ``UNEVALUABLE`` distinct from a fully observed ``NO_ACTION`` row.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
import re
from typing import Sequence

from gld_entry_decision_f0 import entry_gate_policy_candidates_f0
from gld_entry_decision_f0.contracts import (
    Bcs0EconomicsF0,
    CallCandidateF0,
    EntryDecisionInputF0,
    EntryGateFactsF0,
    Lc0EconomicsF0,
    validate_entry_decision_input_f0,
)
from gld_entry_decision_f0.errors import EntryDecisionF0Error

from .contracts import (
    R1FixedPolicyProjectionV1,
    R2QualificationError,
    canonical_json_bytes,
    canonical_sha256,
    validate_r1_fixed_policy_projection,
)


SYNTHETIC_ONLY = "SYNTHETIC_ONLY"
RESEARCH_ONLY = "RESEARCH_ONLY"
NO_DECISION_EFFECT = "NO_DECISION_EFFECT"
PPM = 1_000_000
MAX_RETURN_ABS_PPM = 100 * PPM

_GATE_QUOTAS = frozenset(
    (
        candidate["trend_required_count"],
        candidate["breakout_required_count"],
    )
    for candidate in entry_gate_policy_candidates_f0()
)
_CARRIER_IDS = frozenset({"LC0", "BCS0"})
_CARRIER_MODES = frozenset({"DUAL_PREFERENCE", "LC0_ONLY", "BCS0_ONLY"})
_SESSION_GATE_STATUSES = frozenset({"PASS", "NO_ACTION", "NOT_EVALUATED"})
_ROW_EVIDENCE_STATUSES = frozenset({"QUALIFIED", "UNEVALUABLE"})
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class _SyntheticResearchMarker:
    """Read-only authority boundary shared by every public result."""

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
class GateEvaluationV1(_SyntheticResearchMarker):
    """One fully classified synthetic Gate evaluation."""

    status: str
    reason_code: str
    trend_pass_count: int | None
    breakout_pass_count: int | None
    trend_required_count: int
    breakout_required_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "GLD_R2_SYNTHETIC_GATE_RESULT_V1",
            "classification": self.classification,
            "scope": self.scope,
            "authority_status": self.authority_status,
            "actionable": self.actionable,
            "broker_order_count": self.broker_order_count,
            "status": self.status,
            "reason_code": self.reason_code,
            "trend_pass_count": self.trend_pass_count,
            "breakout_pass_count": self.breakout_pass_count,
            "trend_required_count": self.trend_required_count,
            "breakout_required_count": self.breakout_required_count,
        }


def evaluate_r2_gate(
    facts: EntryGateFactsF0,
    *,
    trend_required_count: int,
    breakout_required_count: int,
) -> GateEvaluationV1:
    """Evaluate one of the four R1 Gate quotas without quota bypass."""

    if (
        type(trend_required_count) is not int
        or type(breakout_required_count) is not int
        or (trend_required_count, breakout_required_count) not in _GATE_QUOTAS
    ):
        raise R2QualificationError("R2_GATE_QUOTA_INVALID")
    if type(facts) is not EntryGateFactsF0:
        return GateEvaluationV1(
            status="UNEVALUABLE",
            reason_code="ENTRY_GATE_FACT_MISSING",
            trend_pass_count=None,
            breakout_pass_count=None,
            trend_required_count=trend_required_count,
            breakout_required_count=breakout_required_count,
        )
    scalars = (
        facts.prior_close_nano_usd,
        facts.sma50_nano_usd,
        facts.sma200_nano_usd,
        facts.sma50_slope_nano_usd_per_session,
        facts.prior_high20_nano_usd,
        facts.minute_1044_close_nano_usd,
    )
    closes = facts.confirmation_closes_nano_usd
    if (
        len(closes) != 15
        or any(value is None or type(value) is not int for value in scalars)
        or any(value is None or type(value) is not int for value in closes)
        or any(
            value is not None and value <= 0
            for index, value in enumerate(scalars)
            if index != 3
        )
        or any(value is not None and value <= 0 for value in closes)
    ):
        return GateEvaluationV1(
            status="UNEVALUABLE",
            reason_code="ENTRY_GATE_FACT_MISSING",
            trend_pass_count=None,
            breakout_pass_count=None,
            trend_required_count=trend_required_count,
            breakout_required_count=breakout_required_count,
        )
    prior_close, sma50, sma200, slope, prior_high20, minute_1044 = scalars
    assert all(value is not None for value in scalars)
    trend_pass_count = sum(
        (
            prior_close > sma50,  # type: ignore[operator]
            sma50 > sma200,  # type: ignore[operator]
            slope > 0,  # type: ignore[operator]
        )
    )
    confirmation_above_count = sum(
        value > prior_high20 for value in closes if value is not None
    )
    breakout_pass_count = sum(
        (
            minute_1044 > prior_high20,  # type: ignore[operator]
            confirmation_above_count >= 10,
        )
    )
    passed = (
        trend_pass_count >= trend_required_count
        and breakout_pass_count >= breakout_required_count
    )
    return GateEvaluationV1(
        status="PASS" if passed else "NO_ACTION",
        reason_code=("ENTRY_GATE_PASS" if passed else "ENTRY_GATE_QUOTA_NOT_MET"),
        trend_pass_count=trend_pass_count,
        breakout_pass_count=breakout_pass_count,
        trend_required_count=trend_required_count,
        breakout_required_count=breakout_required_count,
    )


@dataclass(frozen=True, slots=True)
class SelectorEvaluationV1(_SyntheticResearchMarker):
    """One independent LC0 or BCS0 selector outcome."""

    carrier_id: str
    status: str
    reason_code: str
    selected_contract_ids: tuple[str, ...]
    coarse_selected_contract_ids: tuple[str, ...]
    fine_selected_contract_ids: tuple[str, ...]
    safe_last_trading_date_inclusive: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "GLD_R2_SYNTHETIC_SELECTOR_RESULT_V1",
            "classification": self.classification,
            "scope": self.scope,
            "authority_status": self.authority_status,
            "actionable": self.actionable,
            "broker_order_count": self.broker_order_count,
            "carrier_id": self.carrier_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "selected_contract_ids": list(self.selected_contract_ids),
            "coarse_selected_contract_ids": list(
                self.coarse_selected_contract_ids
            ),
            "fine_selected_contract_ids": list(self.fine_selected_contract_ids),
            "safe_last_trading_date_inclusive": (
                self.safe_last_trading_date_inclusive
            ),
        }


def _selector_result(
    carrier_id: str,
    status: str,
    reason_code: str,
    *,
    coarse: tuple[str, ...] = (),
    fine: tuple[str, ...] = (),
    selected: tuple[str, ...] = (),
    safe_date: date | None = None,
) -> SelectorEvaluationV1:
    return SelectorEvaluationV1(
        carrier_id=carrier_id,
        status=status,
        reason_code=reason_code,
        selected_contract_ids=selected,
        coarse_selected_contract_ids=coarse,
        fine_selected_contract_ids=fine,
        safe_last_trading_date_inclusive=(
            None if safe_date is None else safe_date.isoformat()
        ),
    )


def _projection(
    policy: R1FixedPolicyProjectionV1,
) -> R1FixedPolicyProjectionV1:
    if type(policy) is not R1FixedPolicyProjectionV1:
        raise R2QualificationError("R1_FIXED_POLICY_PROJECTION_INVALID")
    validated = validate_r1_fixed_policy_projection(policy.as_dict())
    if (
        validated.projection_sha256 != policy.projection_sha256
        or validated.canonical_bytes != policy.canonical_bytes
    ):
        raise R2QualificationError("R1_FIXED_POLICY_PROJECTION_INTEGRITY_MISMATCH")
    return validated


def _safe_date(
    h20_date: str,
    policy: R1FixedPolicyProjectionV1,
) -> date | None:
    if type(h20_date) is not str:
        return None
    try:
        parsed = date.fromisoformat(h20_date)
    except ValueError:
        return None
    if parsed.isoformat() != h20_date:
        return None
    return parsed + timedelta(days=policy.expiry_safety_calendar_days)


def _qualified_synthetic_source(
    source: EntryDecisionInputF0 | object,
) -> EntryDecisionInputF0 | None:
    """Revalidate the complete sealed R1 input and its qualification boundary."""

    if type(source) is not EntryDecisionInputF0:
        return None
    if (
        type(source.call_universe) is not tuple
        or any(type(call) is not CallCandidateF0 for call in source.call_universe)
        or type(source.lc0_economics) is not tuple
        or any(type(item) is not Lc0EconomicsF0 for item in source.lc0_economics)
        or type(source.bcs0_economics) is not tuple
        or any(type(item) is not Bcs0EconomicsF0 for item in source.bcs0_economics)
    ):
        return None
    try:
        document = source.as_dict()
        if (
            canonical_json_bytes(document) != source.canonical_bytes
            or canonical_sha256(document) != source.input_sha256
        ):
            return None
        validated = validate_entry_decision_input_f0(document)
    except (AttributeError, TypeError, ValueError, EntryDecisionF0Error):
        return None
    if (
        validated.classification != SYNTHETIC_ONLY
        or validated.data_qualification_status != "STRUCTURALLY_VALID_SYNTHETIC"
        or not validated.universe_complete
        or validated.open_gld_order_count != 0
        or validated.facts_max_event_utc_ns > validated.decision_cutoff_utc_ns
    ):
        return None
    calls = validated.call_universe
    if any(
        call.quote_event_utc_ns > validated.decision_cutoff_utc_ns
        or call.quote_receive_utc_ns > validated.decision_cutoff_utc_ns
        or call.snapshot_sha256 != validated.atomic_snapshot_sha256
        or call.technical_facts_sha256 != validated.technical_facts_sha256
        for call in calls
    ):
        return None
    executable = tuple(call for call in calls if call.executable)
    if executable:
        receives = tuple(call.quote_receive_utc_ns for call in executable)
        if max(receives) - min(receives) > 1_000_000_000:
            return None
        if any(
            validated.decision_cutoff_utc_ns - call.quote_event_utc_ns
            > 5_000_000_000
            or validated.decision_cutoff_utc_ns - call.quote_receive_utc_ns
            > 5_000_000_000
            for call in executable
        ):
            return None
    return validated


def _validated_calls(
    call_universe: Sequence[CallCandidateF0] | object,
) -> tuple[CallCandidateF0, ...] | None:
    if type(call_universe) not in {tuple, list}:
        return None
    calls = tuple(call_universe)
    if not calls or any(type(call) is not CallCandidateF0 for call in calls):
        return None
    ids: set[str] = set()
    for call in calls:
        try:
            expiry = date.fromisoformat(call.expiry_date)
            last_trading = date.fromisoformat(call.last_trading_date)
        except (TypeError, ValueError):
            return None
        if (
            call.contract_id in ids
            or not _IDENTIFIER_RE.fullmatch(call.contract_id)
            or expiry.isoformat() != call.expiry_date
            or last_trading.isoformat() != call.last_trading_date
            or last_trading > expiry
            or type(call.strike_nano_usd) is not int
            or call.strike_nano_usd <= 0
            or type(call.multiplier) is not int
            or call.multiplier <= 0
            or type(call.coarse_delta_ppm) is not int
            or type(call.fine_delta_ppm) is not int
            or not 0 <= call.coarse_delta_ppm <= PPM
            or not 0 <= call.fine_delta_ppm <= PPM
            or type(call.bid_nano_usd) is not int
            or type(call.ask_nano_usd) is not int
            or call.bid_nano_usd <= 0
            or call.ask_nano_usd <= 0
            or call.bid_nano_usd > call.ask_nano_usd
            or type(call.bid_size) is not int
            or type(call.ask_size) is not int
            or call.bid_size < 0
            or call.ask_size < 0
            or type(call.tick_nano_usd) is not int
            or call.tick_nano_usd <= 0
            or type(call.executable) is not bool
            or type(call.quote_event_utc_ns) is not int
            or type(call.quote_receive_utc_ns) is not int
            or call.quote_event_utc_ns < 0
            or call.quote_receive_utc_ns < call.quote_event_utc_ns
            or type(call.snapshot_sha256) is not str
            or _SHA256_RE.fullmatch(call.snapshot_sha256) is None
            or type(call.technical_facts_sha256) is not str
            or _SHA256_RE.fullmatch(call.technical_facts_sha256) is None
            or call.executability_source != "REQUIRED_TECHNICAL_FACTS_V1"
        ):
            return None
        ids.add(call.contract_id)
    return calls


def _call_eligible(
    call: CallCandidateF0,
    *,
    delta_ppm: int,
    minimum_delta_ppm: int,
    maximum_delta_ppm: int,
    safe_last_trading_date: date,
) -> bool:
    return (
        minimum_delta_ppm <= delta_ppm <= maximum_delta_ppm
        and date.fromisoformat(call.last_trading_date)
        >= safe_last_trading_date
        and call.executable
        and call.bid_size > 0
        and call.ask_size > 0
    )


def _validated_lc_economics(
    economics: Sequence[Lc0EconomicsF0] | object,
    call_ids: frozenset[str],
) -> dict[str, Lc0EconomicsF0] | None:
    if type(economics) not in {tuple, list}:
        return None
    normalized: dict[str, Lc0EconomicsF0] = {}
    for item in economics:
        if (
            type(item) is not Lc0EconomicsF0
            or item.contract_id in normalized
            or item.contract_id not in call_ids
            or type(item.stressed_entry_debit_nano_usd) is not int
            or item.stressed_entry_debit_nano_usd <= 0
            or item.stressed_max_loss_basis_nano_usd
            != item.stressed_entry_debit_nano_usd
            or type(item.delta_notional_nano_usd) is not int
            or item.delta_notional_nano_usd <= 0
        ):
            return None
        normalized[item.contract_id] = item
    return normalized


def select_lc0_r2(
    source: EntryDecisionInputF0 | object,
    *,
    policy: R1FixedPolicyProjectionV1,
) -> SelectorEvaluationV1:
    """Select LC0 only from a revalidated, synthetic, sealed R1 input."""

    frozen = _projection(policy)
    qualified = _qualified_synthetic_source(source)
    if qualified is None:
        return _selector_result(
            "LC0", "UNEVALUABLE", "LC0_INPUT_UNEVALUABLE"
        )
    safe = _safe_date(qualified.h20_date, frozen)
    calls = _validated_calls(qualified.call_universe)
    if safe is None or calls is None:
        return _selector_result(
            "LC0", "UNEVALUABLE", "LC0_INPUT_UNEVALUABLE", safe_date=safe
        )
    by_id = _validated_lc_economics(
        qualified.lc0_economics,
        frozenset(call.contract_id for call in calls),
    )
    if by_id is None:
        return _selector_result(
            "LC0", "UNEVALUABLE", "LC0_INPUT_UNEVALUABLE", safe_date=safe
        )

    def choose(delta_field: str) -> CallCandidateF0 | None:
        eligible = [
            call
            for call in calls
            if call.contract_id in by_id
            and by_id[call.contract_id].stressed_entry_debit_nano_usd
            >= call.ask_nano_usd * call.multiplier
            and _call_eligible(
                call,
                delta_ppm=getattr(call, delta_field),
                minimum_delta_ppm=frozen.lc0_delta_min_ppm,
                maximum_delta_ppm=frozen.lc0_delta_max_ppm,
                safe_last_trading_date=safe,
            )
        ]
        return min(
            eligible,
            key=lambda call: (
                call.expiry_date,
                abs(
                    getattr(call, delta_field)
                    - frozen.lc0_delta_target_ppm
                ),
                -call.strike_nano_usd,
                call.contract_id,
            ),
            default=None,
        )

    coarse_call = choose("coarse_delta_ppm")
    fine_call = choose("fine_delta_ppm")
    coarse = () if coarse_call is None else (coarse_call.contract_id,)
    fine = () if fine_call is None else (fine_call.contract_id,)
    if not coarse or not fine:
        return _selector_result(
            "LC0",
            "NO_ACTION",
            "LC0_NO_ELIGIBLE_CONTRACT",
            coarse=coarse,
            fine=fine,
            safe_date=safe,
        )
    if coarse != fine:
        return _selector_result(
            "LC0",
            "NO_ACTION",
            "LC0_COARSE_FINE_SELECTION_DISAGREEMENT",
            coarse=coarse,
            fine=fine,
            safe_date=safe,
        )
    return _selector_result(
        "LC0",
        "PASS",
        "LC0_ELIGIBLE",
        coarse=coarse,
        fine=fine,
        selected=fine,
        safe_date=safe,
    )


def _validated_bcs_economics(
    economics: Sequence[Bcs0EconomicsF0] | object,
    call_ids: frozenset[str],
) -> tuple[Bcs0EconomicsF0, ...] | None:
    if type(economics) not in {tuple, list}:
        return None
    normalized = tuple(economics)
    identities: set[tuple[str, str]] = set()
    for item in normalized:
        if type(item) is not Bcs0EconomicsF0:
            return None
        identity = (item.long_contract_id, item.short_contract_id)
        if (
            identity in identities
            or item.long_contract_id not in call_ids
            or item.short_contract_id not in call_ids
            or item.long_contract_id == item.short_contract_id
            or type(item.stressed_entry_debit_nano_usd) is not int
            or item.stressed_entry_debit_nano_usd <= 0
            or item.stressed_max_loss_basis_nano_usd
            != item.stressed_entry_debit_nano_usd
            or type(item.delta_notional_nano_usd) is not int
            or item.delta_notional_nano_usd <= 0
        ):
            return None
        identities.add(identity)
    return normalized


def select_bcs0_r2(
    source: EntryDecisionInputF0 | object,
    *,
    policy: R1FixedPolicyProjectionV1,
) -> SelectorEvaluationV1:
    """Select BCS0 independently from a revalidated synthetic R1 input."""

    frozen = _projection(policy)
    qualified = _qualified_synthetic_source(source)
    if qualified is None:
        return _selector_result(
            "BCS0", "UNEVALUABLE", "BCS0_INPUT_UNEVALUABLE"
        )
    safe = _safe_date(qualified.h20_date, frozen)
    calls = _validated_calls(qualified.call_universe)
    if safe is None or calls is None:
        return _selector_result(
            "BCS0", "UNEVALUABLE", "BCS0_INPUT_UNEVALUABLE", safe_date=safe
        )
    calls_by_id = {call.contract_id: call for call in calls}
    pairs = _validated_bcs_economics(
        qualified.bcs0_economics,
        frozenset(calls_by_id),
    )
    if pairs is None:
        return _selector_result(
            "BCS0", "UNEVALUABLE", "BCS0_INPUT_UNEVALUABLE", safe_date=safe
        )

    def choose(
        delta_field: str,
    ) -> tuple[CallCandidateF0, CallCandidateF0] | None:
        eligible: list[tuple[CallCandidateF0, CallCandidateF0]] = []
        for pair in pairs:
            long = calls_by_id[pair.long_contract_id]
            short = calls_by_id[pair.short_contract_id]
            if (
                long.expiry_date == short.expiry_date
                and long.multiplier == short.multiplier
                and long.strike_nano_usd < short.strike_nano_usd
                and _call_eligible(
                    long,
                    delta_ppm=getattr(long, delta_field),
                    minimum_delta_ppm=frozen.bcs0_long_delta_min_ppm,
                    maximum_delta_ppm=frozen.bcs0_long_delta_max_ppm,
                    safe_last_trading_date=safe,
                )
                and _call_eligible(
                    short,
                    delta_ppm=getattr(short, delta_field),
                    minimum_delta_ppm=frozen.bcs0_short_delta_min_ppm,
                    maximum_delta_ppm=frozen.bcs0_short_delta_max_ppm,
                    safe_last_trading_date=safe,
                )
                and long.ask_nano_usd > short.bid_nano_usd
                and pair.stressed_entry_debit_nano_usd
                >= (long.ask_nano_usd - short.bid_nano_usd)
                * long.multiplier
                and pair.stressed_entry_debit_nano_usd
                < (short.strike_nano_usd - long.strike_nano_usd)
                * long.multiplier
            ):
                eligible.append((long, short))
        return min(
            eligible,
            key=lambda pair: (
                pair[0].expiry_date,
                abs(
                    getattr(pair[0], delta_field)
                    - frozen.bcs0_long_delta_target_ppm
                )
                + abs(
                    getattr(pair[1], delta_field)
                    - frozen.bcs0_short_delta_target_ppm
                ),
                abs(
                    getattr(pair[0], delta_field)
                    - frozen.bcs0_long_delta_target_ppm
                ),
                abs(
                    getattr(pair[1], delta_field)
                    - frozen.bcs0_short_delta_target_ppm
                ),
                -pair[0].strike_nano_usd,
                -pair[1].strike_nano_usd,
                pair[0].contract_id,
                pair[1].contract_id,
            ),
            default=None,
        )

    coarse_pair = choose("coarse_delta_ppm")
    fine_pair = choose("fine_delta_ppm")
    coarse = (
        ()
        if coarse_pair is None
        else (coarse_pair[0].contract_id, coarse_pair[1].contract_id)
    )
    fine = (
        ()
        if fine_pair is None
        else (fine_pair[0].contract_id, fine_pair[1].contract_id)
    )
    if not coarse or not fine:
        return _selector_result(
            "BCS0",
            "NO_ACTION",
            "BCS0_NO_ELIGIBLE_PAIR",
            coarse=coarse,
            fine=fine,
            safe_date=safe,
        )
    if coarse != fine:
        return _selector_result(
            "BCS0",
            "NO_ACTION",
            "BCS0_COARSE_FINE_SELECTION_DISAGREEMENT",
            coarse=coarse,
            fine=fine,
            safe_date=safe,
        )
    return _selector_result(
        "BCS0",
        "PASS",
        "BCS0_ELIGIBLE",
        coarse=coarse,
        fine=fine,
        selected=fine,
        safe_date=safe,
    )


@dataclass(frozen=True, slots=True)
class PreferenceCandidateV1:
    """One q=1 structure ranked only from frozen training evidence."""

    carrier_id: str
    entry_debit_nano_usd: int
    training_only_lcb_ppm: int
    planned_loss_nano_usd: int
    cash_usage_nano_usd: int

    def __post_init__(self) -> None:
        if self.carrier_id not in _CARRIER_IDS:
            raise R2QualificationError("R2_PREFERENCE_CARRIER_INVALID")
        for value in (
            self.entry_debit_nano_usd,
            self.training_only_lcb_ppm,
        ):
            if type(value) is not int or value <= 0:
                raise R2QualificationError("R2_PREFERENCE_VALUE_INVALID")
        if self.training_only_lcb_ppm > MAX_RETURN_ABS_PPM:
            raise R2QualificationError("R2_PREFERENCE_VALUE_INVALID")
        for value in (self.planned_loss_nano_usd, self.cash_usage_nano_usd):
            if type(value) is not int or value < 0:
                raise R2QualificationError("R2_PREFERENCE_VALUE_INVALID")

    @property
    def comparison_numerator(self) -> int:
        return self.entry_debit_nano_usd * self.training_only_lcb_ppm

    @property
    def quantity_units(self) -> int:
        return 1


@dataclass(frozen=True, slots=True)
class PreferenceDecisionV1(_SyntheticResearchMarker):
    """Deterministic single/dual R1 preference result for q=1 research."""

    carrier_mode: str
    decision_status: str
    preferred: PreferenceCandidateV1
    backup: PreferenceCandidateV1 | None
    ranking_order: tuple[str, ...]
    backup_requires_fresh_snapshot_and_rerun: bool
    backup_automatic_execution: bool

    def as_dict(self) -> dict[str, object]:
        def candidate(item: PreferenceCandidateV1 | None) -> object:
            if item is None:
                return None
            return {
                "carrier_id": item.carrier_id,
                "quantity_units": item.quantity_units,
                "entry_debit_nano_usd": item.entry_debit_nano_usd,
                "training_only_lcb_ppm": item.training_only_lcb_ppm,
                "comparison_numerator": item.comparison_numerator,
                "planned_loss_nano_usd": item.planned_loss_nano_usd,
                "cash_usage_nano_usd": item.cash_usage_nano_usd,
            }

        return {
            "schema_version": "GLD_R2_SYNTHETIC_Q1_PREFERENCE_RESULT_V1",
            "classification": self.classification,
            "scope": self.scope,
            "authority_status": self.authority_status,
            "actionable": self.actionable,
            "broker_order_count": self.broker_order_count,
            "carrier_mode": self.carrier_mode,
            "decision_status": self.decision_status,
            "preferred": candidate(self.preferred),
            "backup": candidate(self.backup),
            "ranking_order": list(self.ranking_order),
            "backup_requires_fresh_snapshot_and_rerun": (
                self.backup_requires_fresh_snapshot_and_rerun
            ),
            "backup_automatic_execution": self.backup_automatic_execution,
        }


def choose_q1_preference(
    candidates: Sequence[PreferenceCandidateV1],
    carrier_mode: str,
) -> PreferenceDecisionV1:
    """Rank exact q=1 candidates; a backup is informational and never runs."""

    if carrier_mode not in _CARRIER_MODES:
        raise R2QualificationError("R2_CARRIER_MODE_INVALID")
    if type(candidates) not in {tuple, list} or any(
        type(candidate) is not PreferenceCandidateV1 for candidate in candidates
    ):
        raise R2QualificationError("R2_PREFERENCE_CANDIDATES_INVALID")
    normalized = tuple(candidates)
    if len({candidate.carrier_id for candidate in normalized}) != len(normalized):
        raise R2QualificationError("R2_PREFERENCE_CANDIDATES_INVALID")
    expected = {
        "DUAL_PREFERENCE": frozenset({"LC0", "BCS0"}),
        "LC0_ONLY": frozenset({"LC0"}),
        "BCS0_ONLY": frozenset({"BCS0"}),
    }[carrier_mode]
    if frozenset(candidate.carrier_id for candidate in normalized) != expected:
        raise R2QualificationError("R2_PREFERENCE_MODE_CANDIDATE_MISMATCH")
    ranked = tuple(
        sorted(
            normalized,
            key=lambda candidate: (
                -candidate.comparison_numerator,
                candidate.planned_loss_nano_usd,
                candidate.cash_usage_nano_usd,
                0 if candidate.carrier_id == "LC0" else 1,
            ),
        )
    )
    backup = ranked[1] if len(ranked) == 2 else None
    return PreferenceDecisionV1(
        carrier_mode=carrier_mode,
        decision_status=(
            "PREFERRED_AND_BACKUP" if backup is not None else "SINGLE_PLAN"
        ),
        preferred=ranked[0],
        backup=backup,
        ranking_order=tuple(candidate.carrier_id for candidate in ranked),
        backup_requires_fresh_snapshot_and_rerun=backup is not None,
        backup_automatic_execution=False,
    )


@dataclass(frozen=True, slots=True)
class SyntheticPolicySessionInstructionV1:
    """One chronological synthetic policy-row instruction."""

    session_id: str
    gate_status: str
    row_evidence_status: str = "QUALIFIED"
    proposed_episode_id: str | None = None
    proposed_carrier_id: str | None = None
    terminal_episode_id: str | None = None
    terminal_return_ppm: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.session_id) is not str
            or _IDENTIFIER_RE.fullmatch(self.session_id) is None
            or self.gate_status not in _SESSION_GATE_STATUSES
            or self.row_evidence_status not in _ROW_EVIDENCE_STATUSES
        ):
            raise R2QualificationError("R2_POLICY_SESSION_INSTRUCTION_INVALID")
        proposed_pair = (
            self.proposed_episode_id is not None,
            self.proposed_carrier_id is not None,
        )
        terminal_pair = (
            self.terminal_episode_id is not None,
            self.terminal_return_ppm is not None,
        )
        if proposed_pair[0] != proposed_pair[1] or terminal_pair[0] != terminal_pair[1]:
            raise R2QualificationError("R2_POLICY_SESSION_INSTRUCTION_INVALID")
        for identifier in (self.proposed_episode_id, self.terminal_episode_id):
            if identifier is not None and _IDENTIFIER_RE.fullmatch(identifier) is None:
                raise R2QualificationError("R2_POLICY_EPISODE_ID_INVALID")
        if (
            self.proposed_carrier_id is not None
            and self.proposed_carrier_id not in _CARRIER_IDS
        ):
            raise R2QualificationError("R2_POLICY_CARRIER_INVALID")
        if self.terminal_return_ppm is not None and (
            type(self.terminal_return_ppm) is not int
            or not -MAX_RETURN_ABS_PPM
            <= self.terminal_return_ppm
            <= MAX_RETURN_ABS_PPM
        ):
            raise R2QualificationError("R2_POLICY_RETURN_INVALID")


@dataclass(frozen=True, slots=True)
class SyntheticPolicyLedgerRowV1:
    """One session row with terminal return attributed to its entry row."""

    session_id: str
    row_status: str
    synthetic_return_ppm: int
    episode_id: str | None
    carrier_id: str | None


@dataclass(frozen=True, slots=True)
class SyntheticPolicyLedgerResultV1(_SyntheticResearchMarker):
    """Pure sequential q=1 ledger result with no external side effect."""

    status: str
    reason_code: str
    quantity_units: int
    rows: tuple[SyntheticPolicyLedgerRowV1, ...]
    opened_episode_ids: tuple[str, ...]
    completed_episode_ids: tuple[str, ...]
    open_episode_id: str | None
    open_carrier_id: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "GLD_R2_SYNTHETIC_Q1_POLICY_LEDGER_V1",
            "classification": self.classification,
            "scope": self.scope,
            "authority_status": self.authority_status,
            "actionable": self.actionable,
            "broker_order_count": self.broker_order_count,
            "status": self.status,
            "reason_code": self.reason_code,
            "quantity_units": self.quantity_units,
            "rows": [
                {
                    "session_id": row.session_id,
                    "row_status": row.row_status,
                    "synthetic_return_ppm": row.synthetic_return_ppm,
                    "episode_id": row.episode_id,
                    "carrier_id": row.carrier_id,
                }
                for row in self.rows
            ],
            "opened_episode_ids": list(self.opened_episode_ids),
            "completed_episode_ids": list(self.completed_episode_ids),
            "open_episode_id": self.open_episode_id,
            "open_carrier_id": self.open_carrier_id,
        }


def reduce_synthetic_q1_policy_ledger(
    instructions: Sequence[SyntheticPolicySessionInstructionV1],
) -> SyntheticPolicyLedgerResultV1:
    """Reduce chronological rows with return backfill and no overlapping entry."""

    if type(instructions) not in {tuple, list} or not instructions or any(
        type(instruction) is not SyntheticPolicySessionInstructionV1
        for instruction in instructions
    ):
        raise R2QualificationError("R2_POLICY_LEDGER_INPUT_INVALID")
    session_ids = tuple(instruction.session_id for instruction in instructions)
    if len(set(session_ids)) != len(session_ids):
        raise R2QualificationError("R2_POLICY_SESSION_DUPLICATE")

    rows: list[SyntheticPolicyLedgerRowV1] = []
    opened: list[str] = []
    completed: list[str] = []
    entry_index_by_episode: dict[str, int] = {}
    open_episode: str | None = None
    open_carrier: str | None = None
    insufficient = False

    for instruction in instructions:
        if instruction.row_evidence_status == "UNEVALUABLE":
            if (
                instruction.proposed_episode_id is not None
                or instruction.terminal_episode_id is not None
            ):
                raise R2QualificationError(
                    "R2_POLICY_UNEVALUABLE_ROW_CLAIM_INVALID"
                )
            insufficient = True
            rows.append(
                SyntheticPolicyLedgerRowV1(
                    session_id=instruction.session_id,
                    row_status="UNEVALUABLE",
                    synthetic_return_ppm=0,
                    episode_id=open_episode,
                    carrier_id=open_carrier,
                )
            )
            continue
        if open_episode is not None:
            if instruction.terminal_episode_id is not None:
                if instruction.terminal_episode_id != open_episode:
                    raise R2QualificationError("R2_POLICY_TERMINAL_EPISODE_MISMATCH")
                assert instruction.terminal_return_ppm is not None
                entry_index = entry_index_by_episode[open_episode]
                rows[entry_index] = replace(
                    rows[entry_index],
                    synthetic_return_ppm=instruction.terminal_return_ppm,
                )
                rows.append(
                    SyntheticPolicyLedgerRowV1(
                        session_id=instruction.session_id,
                        row_status="OCCUPIED_EXITED",
                        synthetic_return_ppm=0,
                        episode_id=open_episode,
                        carrier_id=open_carrier,
                    )
                )
                completed.append(open_episode)
                open_episode = None
                open_carrier = None
            else:
                rows.append(
                    SyntheticPolicyLedgerRowV1(
                        session_id=instruction.session_id,
                        row_status="OCCUPIED",
                        synthetic_return_ppm=0,
                        episode_id=open_episode,
                        carrier_id=open_carrier,
                    )
                )
            continue

        if instruction.terminal_episode_id is not None:
            raise R2QualificationError("R2_POLICY_TERMINAL_WITHOUT_OPEN_EPISODE")
        if instruction.gate_status == "NO_ACTION":
            if instruction.proposed_episode_id is not None:
                raise R2QualificationError("R2_POLICY_NO_ACTION_PROPOSAL_INVALID")
            rows.append(
                SyntheticPolicyLedgerRowV1(
                    session_id=instruction.session_id,
                    row_status="NO_ACTION",
                    synthetic_return_ppm=0,
                    episode_id=None,
                    carrier_id=None,
                )
            )
            continue
        if instruction.gate_status == "NOT_EVALUATED":
            raise R2QualificationError("R2_POLICY_GATE_NOT_EVALUATED_WHILE_FLAT")
        if instruction.proposed_episode_id is None:
            raise R2QualificationError("R2_POLICY_PASS_PROPOSAL_MISSING")
        if instruction.proposed_episode_id in entry_index_by_episode:
            raise R2QualificationError("R2_POLICY_EPISODE_DUPLICATE")
        assert instruction.proposed_carrier_id is not None
        open_episode = instruction.proposed_episode_id
        open_carrier = instruction.proposed_carrier_id
        entry_index_by_episode[open_episode] = len(rows)
        opened.append(open_episode)
        rows.append(
            SyntheticPolicyLedgerRowV1(
                session_id=instruction.session_id,
                row_status="ENTRY_OPENED",
                synthetic_return_ppm=0,
                episode_id=open_episode,
                carrier_id=open_carrier,
            )
        )

    if insufficient:
        status = "INSUFFICIENT_EVIDENCE"
        reason_code = "CANDIDATE_SPECIFIC_UNEVALUABLE"
    elif open_episode is not None:
        status = "PENDING_OPEN_EPISODE"
        reason_code = "SYNTHETIC_Q1_EPISODE_PENDING"
    else:
        status = "PASS"
        reason_code = "SYNTHETIC_Q1_LEDGER_REDUCED"
    return SyntheticPolicyLedgerResultV1(
        status=status,
        reason_code=reason_code,
        quantity_units=1,
        rows=tuple(rows),
        opened_episode_ids=tuple(opened),
        completed_episode_ids=tuple(completed),
        open_episode_id=open_episode,
        open_carrier_id=open_carrier,
    )


__all__ = [
    "GateEvaluationV1",
    "PreferenceCandidateV1",
    "PreferenceDecisionV1",
    "SelectorEvaluationV1",
    "SyntheticPolicyLedgerResultV1",
    "SyntheticPolicyLedgerRowV1",
    "SyntheticPolicySessionInstructionV1",
    "choose_q1_preference",
    "evaluate_r2_gate",
    "reduce_synthetic_q1_policy_ledger",
    "select_bcs0_r2",
    "select_lc0_r2",
]
