"""Frozen typed contracts for GLD Entry Decision f F0.

The input contract carries already-validated Entry/Technical fact references
and the exact facts needed by the research decision kernel.  It intentionally
contains no winner, quantity, preference, or action field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re

from .canonical import canonical_json_bytes, canonical_json_sha256
from .errors import EntryDecisionF0Error


ENTRY_DECISION_INPUT_SCHEMA_VERSION = "ENTRY_DECISION_INPUT_F0_V1"
HISTORICAL_STRUCTURE_EVIDENCE_SCHEMA_VERSION = (
    "HISTORICAL_STRUCTURE_EVIDENCE_V2"
)
PPM = 1_000_000
EVIDENCE_RETURN_BOUND_PPM = 100 * PPM
MAX_I64 = 2**63 - 1
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")

_INPUT_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "instrument_id",
        "data_qualification_status",
        "entry_fact_bundle_sha256",
        "technical_facts_sha256",
        "atomic_snapshot_sha256",
        "universe_complete",
        "decision_cutoff_utc_ns",
        "facts_max_event_utc_ns",
        "open_gld_order_count",
        "entry_session_date",
        "h20_date",
        "h20_exit_utc_ns",
        "entry_gate_facts",
        "account_facts",
        "call_universe",
        "lc0_economics",
        "bcs0_economics",
        "structure_evidence",
        "exit_policies",
        "planned_exit_fees_nano_usd",
    }
)
_GATE_KEYS = frozenset(
    {
        "prior_close_nano_usd",
        "sma50_nano_usd",
        "sma200_nano_usd",
        "sma50_slope_nano_usd_per_session",
        "prior_high20_nano_usd",
        "minute_1044_close_nano_usd",
        "confirmation_closes_nano_usd",
    }
)
_ACCOUNT_KEYS = frozenset(
    {
        "eligible_bankroll_nano_usd",
        "current_nlv_nano_usd",
        "peak_nlv_nano_usd",
        "settled_cash_nano_usd",
        "cash_reserve_nano_usd",
        "current_signed_gld_delta_exposure_nano_usd",
        "delta_limit_nano_usd",
        "external_capacity_units",
    }
)
_CALL_KEYS = frozenset(
    {
        "contract_id",
        "expiry_date",
        "last_trading_date",
        "strike_nano_usd",
        "multiplier",
        "coarse_delta_ppm",
        "fine_delta_ppm",
        "bid_nano_usd",
        "ask_nano_usd",
        "bid_size",
        "ask_size",
        "tick_nano_usd",
        "quote_event_utc_ns",
        "quote_receive_utc_ns",
        "executable",
        "snapshot_sha256",
        "technical_facts_sha256",
        "executability_source",
    }
)
_LC_ECONOMICS_KEYS = frozenset(
    {
        "contract_id",
        "stressed_entry_debit_nano_usd",
        "stressed_max_loss_basis_nano_usd",
        "delta_notional_nano_usd",
    }
)
_BCS_ECONOMICS_KEYS = frozenset(
    {
        "long_contract_id",
        "short_contract_id",
        "stressed_entry_debit_nano_usd",
        "stressed_max_loss_basis_nano_usd",
        "delta_notional_nano_usd",
    }
)
_EVIDENCE_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "carrier_id",
        "evidence_receipt_id",
        "episode_cohort_id",
        "distribution_id",
        "expected_net_return_on_entry_debit_ppm",
        "arithmetic_mean_ppm",
        "full_kelly_ppm",
        "bootstrap_kelly_5pct_ppm",
        "robust_full_kelly_ppm",
        "half_kelly_ppm",
        "oos_episode_count",
        "fold_count",
        "coverage_ppm",
        "entry_policy_sha256",
        "fee_schedule_sha256",
        "exit_policy_sha256",
        "evidence_max_estimation_outcome_utc_ns",
        "input_sha256",
        "distribution_sha256",
        "evidence_sha256",
    }
)
_EXIT_POLICY_KEYS = frozenset(
    {
        "policy_id",
        "hard_stop_loss_ppm",
        "policy_sha256",
        "fee_schedule_sha256",
    }
)


def _mapping(value: object, keys: frozenset[str], reason: str) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != keys:
        raise EntryDecisionF0Error(reason)
    return value


def _integer(
    value: object,
    *,
    minimum: int = 0,
    maximum: int = MAX_I64,
    optional: bool = False,
) -> int | None:
    if optional and value is None:
        return None
    if type(value) is not int or not minimum <= value <= maximum:
        raise EntryDecisionF0Error("ENTRY_INPUT_INTEGER_INVALID")
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise EntryDecisionF0Error("ENTRY_INPUT_IDENTIFIER_INVALID")
    return value


def _hash(value: object) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise EntryDecisionF0Error("ENTRY_INPUT_HASH_INVALID")
    return value


def _date(value: object) -> str:
    if type(value) is not str:
        raise EntryDecisionF0Error("ENTRY_INPUT_DATE_INVALID")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise EntryDecisionF0Error("ENTRY_INPUT_DATE_INVALID") from exc
    if parsed.isoformat() != value:
        raise EntryDecisionF0Error("ENTRY_INPUT_DATE_INVALID")
    return value


@dataclass(frozen=True, slots=True)
class EntryGateFactsF0:
    prior_close_nano_usd: int | None
    sma50_nano_usd: int | None
    sma200_nano_usd: int | None
    sma50_slope_nano_usd_per_session: int | None
    prior_high20_nano_usd: int | None
    minute_1044_close_nano_usd: int | None
    confirmation_closes_nano_usd: tuple[int | None, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "prior_close_nano_usd": self.prior_close_nano_usd,
            "sma50_nano_usd": self.sma50_nano_usd,
            "sma200_nano_usd": self.sma200_nano_usd,
            "sma50_slope_nano_usd_per_session": self.sma50_slope_nano_usd_per_session,
            "prior_high20_nano_usd": self.prior_high20_nano_usd,
            "minute_1044_close_nano_usd": self.minute_1044_close_nano_usd,
            "confirmation_closes_nano_usd": list(self.confirmation_closes_nano_usd),
        }


@dataclass(frozen=True, slots=True)
class EntryAccountFactsF0:
    eligible_bankroll_nano_usd: int | None
    current_nlv_nano_usd: int | None
    peak_nlv_nano_usd: int | None
    settled_cash_nano_usd: int | None
    cash_reserve_nano_usd: int | None
    current_signed_gld_delta_exposure_nano_usd: int | None
    delta_limit_nano_usd: int | None
    external_capacity_units: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "eligible_bankroll_nano_usd": self.eligible_bankroll_nano_usd,
            "current_nlv_nano_usd": self.current_nlv_nano_usd,
            "peak_nlv_nano_usd": self.peak_nlv_nano_usd,
            "settled_cash_nano_usd": self.settled_cash_nano_usd,
            "cash_reserve_nano_usd": self.cash_reserve_nano_usd,
            "current_signed_gld_delta_exposure_nano_usd": self.current_signed_gld_delta_exposure_nano_usd,
            "delta_limit_nano_usd": self.delta_limit_nano_usd,
            "external_capacity_units": self.external_capacity_units,
        }


@dataclass(frozen=True, slots=True)
class CallCandidateF0:
    contract_id: str
    expiry_date: str
    last_trading_date: str
    strike_nano_usd: int
    multiplier: int
    coarse_delta_ppm: int
    fine_delta_ppm: int
    bid_nano_usd: int
    ask_nano_usd: int
    bid_size: int
    ask_size: int
    tick_nano_usd: int
    quote_event_utc_ns: int
    quote_receive_utc_ns: int
    executable: bool
    snapshot_sha256: str
    technical_facts_sha256: str
    executability_source: str

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_id": self.contract_id,
            "expiry_date": self.expiry_date,
            "last_trading_date": self.last_trading_date,
            "strike_nano_usd": self.strike_nano_usd,
            "multiplier": self.multiplier,
            "coarse_delta_ppm": self.coarse_delta_ppm,
            "fine_delta_ppm": self.fine_delta_ppm,
            "bid_nano_usd": self.bid_nano_usd,
            "ask_nano_usd": self.ask_nano_usd,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "tick_nano_usd": self.tick_nano_usd,
            "quote_event_utc_ns": self.quote_event_utc_ns,
            "quote_receive_utc_ns": self.quote_receive_utc_ns,
            "executable": self.executable,
            "snapshot_sha256": self.snapshot_sha256,
            "technical_facts_sha256": self.technical_facts_sha256,
            "executability_source": self.executability_source,
        }


@dataclass(frozen=True, slots=True)
class Lc0EconomicsF0:
    contract_id: str
    stressed_entry_debit_nano_usd: int
    stressed_max_loss_basis_nano_usd: int
    delta_notional_nano_usd: int

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_id": self.contract_id,
            "stressed_entry_debit_nano_usd": self.stressed_entry_debit_nano_usd,
            "stressed_max_loss_basis_nano_usd": self.stressed_max_loss_basis_nano_usd,
            "delta_notional_nano_usd": self.delta_notional_nano_usd,
        }


@dataclass(frozen=True, slots=True)
class Bcs0EconomicsF0:
    long_contract_id: str
    short_contract_id: str
    stressed_entry_debit_nano_usd: int
    stressed_max_loss_basis_nano_usd: int
    delta_notional_nano_usd: int

    def as_dict(self) -> dict[str, object]:
        return {
            "long_contract_id": self.long_contract_id,
            "short_contract_id": self.short_contract_id,
            "stressed_entry_debit_nano_usd": self.stressed_entry_debit_nano_usd,
            "stressed_max_loss_basis_nano_usd": self.stressed_max_loss_basis_nano_usd,
            "delta_notional_nano_usd": self.delta_notional_nano_usd,
        }


@dataclass(frozen=True, slots=True)
class StructureEvidenceSummaryF0:
    schema_version: str
    classification: str
    carrier_id: str
    evidence_receipt_id: str
    episode_cohort_id: str
    distribution_id: str
    expected_net_return_on_entry_debit_ppm: int
    arithmetic_mean_ppm: int
    full_kelly_ppm: int
    bootstrap_kelly_5pct_ppm: int
    robust_full_kelly_ppm: int
    half_kelly_ppm: int
    oos_episode_count: int
    fold_count: int
    coverage_ppm: int
    entry_policy_sha256: str
    fee_schedule_sha256: str
    exit_policy_sha256: str
    evidence_max_estimation_outcome_utc_ns: int
    input_sha256: str
    distribution_sha256: str
    evidence_sha256: str

    @property
    def expected_net_return_lower_bound_ppm(self) -> int:
        return self.expected_net_return_on_entry_debit_ppm

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "carrier_id": self.carrier_id,
            "evidence_receipt_id": self.evidence_receipt_id,
            "episode_cohort_id": self.episode_cohort_id,
            "distribution_id": self.distribution_id,
            "expected_net_return_on_entry_debit_ppm": self.expected_net_return_on_entry_debit_ppm,
            "arithmetic_mean_ppm": self.arithmetic_mean_ppm,
            "full_kelly_ppm": self.full_kelly_ppm,
            "bootstrap_kelly_5pct_ppm": self.bootstrap_kelly_5pct_ppm,
            "robust_full_kelly_ppm": self.robust_full_kelly_ppm,
            "half_kelly_ppm": self.half_kelly_ppm,
            "oos_episode_count": self.oos_episode_count,
            "fold_count": self.fold_count,
            "coverage_ppm": self.coverage_ppm,
            "entry_policy_sha256": self.entry_policy_sha256,
            "fee_schedule_sha256": self.fee_schedule_sha256,
            "exit_policy_sha256": self.exit_policy_sha256,
            "evidence_max_estimation_outcome_utc_ns": (
                self.evidence_max_estimation_outcome_utc_ns
            ),
            "input_sha256": self.input_sha256,
            "distribution_sha256": self.distribution_sha256,
            "evidence_sha256": self.evidence_sha256,
        }


@dataclass(frozen=True, slots=True)
class ExitPolicyBindingInputF0:
    policy_id: str
    hard_stop_loss_ppm: int
    policy_sha256: str
    fee_schedule_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "hard_stop_loss_ppm": self.hard_stop_loss_ppm,
            "policy_sha256": self.policy_sha256,
            "fee_schedule_sha256": self.fee_schedule_sha256,
        }


@dataclass(frozen=True, slots=True)
class EntryDecisionInputF0:
    schema_version: str
    classification: str
    instrument_id: str
    data_qualification_status: str
    entry_fact_bundle_sha256: str
    technical_facts_sha256: str
    atomic_snapshot_sha256: str
    universe_complete: bool
    decision_cutoff_utc_ns: int
    facts_max_event_utc_ns: int
    open_gld_order_count: int
    entry_session_date: str
    h20_date: str
    h20_exit_utc_ns: int
    entry_gate_facts: EntryGateFactsF0
    account_facts: EntryAccountFactsF0
    call_universe: tuple[CallCandidateF0, ...]
    lc0_economics: tuple[Lc0EconomicsF0, ...]
    bcs0_economics: tuple[Bcs0EconomicsF0, ...]
    lc0_evidence: StructureEvidenceSummaryF0 | None
    bcs0_evidence: StructureEvidenceSummaryF0 | None
    lc0_exit_policy: ExitPolicyBindingInputF0 | None
    bcs0_exit_policy: ExitPolicyBindingInputF0 | None
    planned_exit_fee_lc0_nano_usd: int | None
    planned_exit_fee_bcs0_nano_usd: int | None
    input_sha256: str
    canonical_bytes: bytes

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "instrument_id": self.instrument_id,
            "data_qualification_status": self.data_qualification_status,
            "entry_fact_bundle_sha256": self.entry_fact_bundle_sha256,
            "technical_facts_sha256": self.technical_facts_sha256,
            "atomic_snapshot_sha256": self.atomic_snapshot_sha256,
            "universe_complete": self.universe_complete,
            "decision_cutoff_utc_ns": self.decision_cutoff_utc_ns,
            "facts_max_event_utc_ns": self.facts_max_event_utc_ns,
            "open_gld_order_count": self.open_gld_order_count,
            "entry_session_date": self.entry_session_date,
            "h20_date": self.h20_date,
            "h20_exit_utc_ns": self.h20_exit_utc_ns,
            "entry_gate_facts": self.entry_gate_facts.as_dict(),
            "account_facts": self.account_facts.as_dict(),
            "call_universe": [item.as_dict() for item in self.call_universe],
            "lc0_economics": [item.as_dict() for item in self.lc0_economics],
            "bcs0_economics": [item.as_dict() for item in self.bcs0_economics],
            "structure_evidence": {
                "LC0": None if self.lc0_evidence is None else self.lc0_evidence.as_dict(),
                "BCS0": None if self.bcs0_evidence is None else self.bcs0_evidence.as_dict(),
            },
            "exit_policies": {
                "LC0": None if self.lc0_exit_policy is None else self.lc0_exit_policy.as_dict(),
                "BCS0": None if self.bcs0_exit_policy is None else self.bcs0_exit_policy.as_dict(),
            },
            "planned_exit_fees_nano_usd": {
                "LC0": self.planned_exit_fee_lc0_nano_usd,
                "BCS0": self.planned_exit_fee_bcs0_nano_usd,
            },
        }


def _validate_gate(value: object) -> EntryGateFactsF0:
    source = _mapping(value, _GATE_KEYS, "ENTRY_GATE_FACTS_SCHEMA_INVALID")
    closes = source["confirmation_closes_nano_usd"]
    if type(closes) is not list or len(closes) != 15:
        raise EntryDecisionF0Error("ENTRY_GATE_CONFIRMATION_WINDOW_INVALID")
    normalized_closes = tuple(
        _integer(item, minimum=1, optional=True) for item in closes
    )
    slope = _integer(
        source["sma50_slope_nano_usd_per_session"],
        minimum=-MAX_I64,
        optional=True,
    )
    return EntryGateFactsF0(
        prior_close_nano_usd=_integer(source["prior_close_nano_usd"], minimum=1, optional=True),
        sma50_nano_usd=_integer(source["sma50_nano_usd"], minimum=1, optional=True),
        sma200_nano_usd=_integer(source["sma200_nano_usd"], minimum=1, optional=True),
        sma50_slope_nano_usd_per_session=slope,
        prior_high20_nano_usd=_integer(source["prior_high20_nano_usd"], minimum=1, optional=True),
        minute_1044_close_nano_usd=_integer(source["minute_1044_close_nano_usd"], minimum=1, optional=True),
        confirmation_closes_nano_usd=normalized_closes,
    )


def _validate_account(value: object) -> EntryAccountFactsF0:
    source = _mapping(value, _ACCOUNT_KEYS, "ENTRY_ACCOUNT_FACTS_SCHEMA_INVALID")
    signed = _integer(
        source["current_signed_gld_delta_exposure_nano_usd"],
        minimum=-MAX_I64,
        optional=True,
    )
    return EntryAccountFactsF0(
        eligible_bankroll_nano_usd=_integer(source["eligible_bankroll_nano_usd"], optional=True),
        current_nlv_nano_usd=_integer(source["current_nlv_nano_usd"], optional=True),
        peak_nlv_nano_usd=_integer(source["peak_nlv_nano_usd"], optional=True),
        settled_cash_nano_usd=_integer(source["settled_cash_nano_usd"], optional=True),
        cash_reserve_nano_usd=_integer(source["cash_reserve_nano_usd"], optional=True),
        current_signed_gld_delta_exposure_nano_usd=signed,
        delta_limit_nano_usd=_integer(source["delta_limit_nano_usd"], optional=True),
        external_capacity_units=_integer(source["external_capacity_units"], maximum=1_000_000, optional=True),
    )


def _validate_call(value: object) -> CallCandidateF0:
    source = _mapping(value, _CALL_KEYS, "CALL_CANDIDATE_SCHEMA_INVALID")
    contract_id = _identifier(source["contract_id"])
    event_ns = _integer(source["quote_event_utc_ns"])
    receive_ns = _integer(source["quote_receive_utc_ns"])
    assert event_ns is not None and receive_ns is not None
    if event_ns > receive_ns:
        raise EntryDecisionF0Error("OPTION_QUOTE_TIME_INVALID")
    bid = _integer(source["bid_nano_usd"], minimum=1)
    ask = _integer(source["ask_nano_usd"], minimum=1)
    assert bid is not None and ask is not None
    if bid > ask:
        raise EntryDecisionF0Error("OPTION_QUOTE_CROSSED")
    if type(source["executable"]) is not bool:
        raise EntryDecisionF0Error("OPTION_EXECUTABILITY_INVALID")
    snapshot_sha256 = _hash(source["snapshot_sha256"])
    technical_facts_sha256 = _hash(source["technical_facts_sha256"])
    if source["executability_source"] != "REQUIRED_TECHNICAL_FACTS_V1":
        raise EntryDecisionF0Error("OPTION_EXECUTABILITY_SOURCE_INVALID")
    return CallCandidateF0(
        contract_id=contract_id,
        expiry_date=_date(source["expiry_date"]),
        last_trading_date=_date(source["last_trading_date"]),
        strike_nano_usd=_integer(source["strike_nano_usd"], minimum=1),  # type: ignore[arg-type]
        multiplier=_integer(source["multiplier"], minimum=1, maximum=100_000),  # type: ignore[arg-type]
        coarse_delta_ppm=_integer(source["coarse_delta_ppm"], maximum=PPM),  # type: ignore[arg-type]
        fine_delta_ppm=_integer(source["fine_delta_ppm"], maximum=PPM),  # type: ignore[arg-type]
        bid_nano_usd=bid,
        ask_nano_usd=ask,
        bid_size=_integer(source["bid_size"], maximum=1_000_000),  # type: ignore[arg-type]
        ask_size=_integer(source["ask_size"], maximum=1_000_000),  # type: ignore[arg-type]
        tick_nano_usd=_integer(source["tick_nano_usd"], minimum=1),  # type: ignore[arg-type]
        quote_event_utc_ns=event_ns,
        quote_receive_utc_ns=receive_ns,
        executable=source["executable"],
        snapshot_sha256=snapshot_sha256,
        technical_facts_sha256=technical_facts_sha256,
        executability_source="REQUIRED_TECHNICAL_FACTS_V1",
    )


def _validate_lc_economics(value: object) -> Lc0EconomicsF0:
    source = _mapping(value, _LC_ECONOMICS_KEYS, "LC0_ECONOMICS_SCHEMA_INVALID")
    stressed_debit = _integer(
        source["stressed_entry_debit_nano_usd"], minimum=1
    )
    max_loss_basis = _integer(
        source["stressed_max_loss_basis_nano_usd"], minimum=1
    )
    assert stressed_debit is not None and max_loss_basis is not None
    if stressed_debit != max_loss_basis:
        raise EntryDecisionF0Error("LC0_STRESSED_MAX_LOSS_BASIS_MISMATCH")
    return Lc0EconomicsF0(
        contract_id=_identifier(source["contract_id"]),
        stressed_entry_debit_nano_usd=stressed_debit,
        stressed_max_loss_basis_nano_usd=max_loss_basis,
        delta_notional_nano_usd=_integer(source["delta_notional_nano_usd"], minimum=1),  # type: ignore[arg-type]
    )


def _validate_bcs_economics(value: object) -> Bcs0EconomicsF0:
    source = _mapping(value, _BCS_ECONOMICS_KEYS, "BCS0_ECONOMICS_SCHEMA_INVALID")
    long_id = _identifier(source["long_contract_id"])
    short_id = _identifier(source["short_contract_id"])
    if long_id == short_id:
        raise EntryDecisionF0Error("BCS0_IDENTITY_INVALID")
    stressed_debit = _integer(
        source["stressed_entry_debit_nano_usd"], minimum=1
    )
    max_loss_basis = _integer(
        source["stressed_max_loss_basis_nano_usd"], minimum=1
    )
    assert stressed_debit is not None and max_loss_basis is not None
    if stressed_debit != max_loss_basis:
        raise EntryDecisionF0Error("BCS0_STRESSED_MAX_LOSS_BASIS_MISMATCH")
    return Bcs0EconomicsF0(
        long_contract_id=long_id,
        short_contract_id=short_id,
        stressed_entry_debit_nano_usd=stressed_debit,
        stressed_max_loss_basis_nano_usd=max_loss_basis,
        delta_notional_nano_usd=_integer(source["delta_notional_nano_usd"], minimum=1),  # type: ignore[arg-type]
    )


def _validate_evidence(value: object, carrier_id: str) -> StructureEvidenceSummaryF0 | None:
    if value is None:
        return None
    source = _mapping(value, _EVIDENCE_KEYS, "STRUCTURE_EVIDENCE_SCHEMA_INVALID")
    if source["schema_version"] != HISTORICAL_STRUCTURE_EVIDENCE_SCHEMA_VERSION:
        raise EntryDecisionF0Error("STRUCTURE_EVIDENCE_VERSION_INVALID")
    if source["classification"] not in {"SYNTHETIC_ONLY", "HISTORICAL_RESEARCH_CANDIDATE"}:
        raise EntryDecisionF0Error("STRUCTURE_EVIDENCE_CLASSIFICATION_INVALID")
    if source["carrier_id"] != carrier_id:
        raise EntryDecisionF0Error("STRUCTURE_EVIDENCE_CARRIER_MISMATCH")
    expected = _integer(
        source["expected_net_return_on_entry_debit_ppm"],
        minimum=-EVIDENCE_RETURN_BOUND_PPM,
        maximum=EVIDENCE_RETURN_BOUND_PPM,
    )
    mean = _integer(
        source["arithmetic_mean_ppm"],
        minimum=-EVIDENCE_RETURN_BOUND_PPM,
        maximum=EVIDENCE_RETURN_BOUND_PPM,
    )
    assert expected is not None and mean is not None
    full = _integer(source["full_kelly_ppm"], maximum=PPM)
    bootstrap_kelly = _integer(source["bootstrap_kelly_5pct_ppm"], maximum=PPM)
    robust = _integer(source["robust_full_kelly_ppm"], maximum=PPM)
    half = _integer(source["half_kelly_ppm"], maximum=PPM)
    episodes = _integer(source["oos_episode_count"], maximum=10_000_000)
    folds = _integer(source["fold_count"], maximum=10_000)
    coverage = _integer(source["coverage_ppm"], maximum=PPM)
    assert full is not None and bootstrap_kelly is not None and robust is not None
    assert half is not None and episodes is not None and folds is not None and coverage is not None
    if episodes < 100 or folds < 3 or coverage < 950_000:
        raise EntryDecisionF0Error("STRUCTURE_EVIDENCE_QUALIFICATION_INVALID")
    if expected <= 0:
        if robust != 0 or half != 0:
            raise EntryDecisionF0Error(
                "STRUCTURE_EVIDENCE_NONPOSITIVE_BOUND_KELLY_INVALID"
            )
    elif robust != min(full, bootstrap_kelly) or half != robust // 2:
        raise EntryDecisionF0Error("STRUCTURE_EVIDENCE_KELLY_INVARIANT_INVALID")
    return StructureEvidenceSummaryF0(
        schema_version=HISTORICAL_STRUCTURE_EVIDENCE_SCHEMA_VERSION,
        classification=source["classification"],  # type: ignore[arg-type]
        carrier_id=carrier_id,
        evidence_receipt_id=_identifier(source["evidence_receipt_id"]),
        episode_cohort_id=_identifier(source["episode_cohort_id"]),
        distribution_id=_identifier(source["distribution_id"]),
        expected_net_return_on_entry_debit_ppm=expected,
        arithmetic_mean_ppm=mean,
        full_kelly_ppm=full,
        bootstrap_kelly_5pct_ppm=bootstrap_kelly,
        robust_full_kelly_ppm=robust,
        half_kelly_ppm=half,
        oos_episode_count=episodes,
        fold_count=folds,
        coverage_ppm=coverage,
        entry_policy_sha256=_hash(source["entry_policy_sha256"]),
        fee_schedule_sha256=_hash(source["fee_schedule_sha256"]),
        exit_policy_sha256=_hash(source["exit_policy_sha256"]),
        evidence_max_estimation_outcome_utc_ns=_integer(
            source["evidence_max_estimation_outcome_utc_ns"]
        ),  # type: ignore[arg-type]
        input_sha256=_hash(source["input_sha256"]),
        distribution_sha256=_hash(source["distribution_sha256"]),
        evidence_sha256=_hash(source["evidence_sha256"]),
    )


def _validate_exit_policy(value: object) -> ExitPolicyBindingInputF0 | None:
    if value is None:
        return None
    source = _mapping(value, _EXIT_POLICY_KEYS, "EXIT_POLICY_SCHEMA_INVALID")
    policy_id = _identifier(source["policy_id"])
    hard_stop_loss_ppm = _integer(
        source["hard_stop_loss_ppm"], maximum=PPM
    )
    assert hard_stop_loss_ppm is not None
    fee_schedule_sha256 = _hash(source["fee_schedule_sha256"])
    policy_sha256 = _hash(source["policy_sha256"])
    expected_policy_sha256 = canonical_json_sha256(
        {
            "policy_id": policy_id,
            "hard_stop_loss_ppm": hard_stop_loss_ppm,
        }
    )
    if policy_sha256 != expected_policy_sha256:
        raise EntryDecisionF0Error("EXIT_POLICY_HASH_BINDING_MISMATCH")
    return ExitPolicyBindingInputF0(
        policy_id=policy_id,
        hard_stop_loss_ppm=hard_stop_loss_ppm,
        policy_sha256=policy_sha256,
        fee_schedule_sha256=fee_schedule_sha256,
    )


def validate_entry_decision_input_f0(document: object) -> EntryDecisionInputF0:
    """Validate and canonically sort a JSON-domain Entry f input."""

    source = _mapping(document, _INPUT_KEYS, "ENTRY_INPUT_SCHEMA_INVALID")
    canonical_json_bytes(source)
    if source["schema_version"] != ENTRY_DECISION_INPUT_SCHEMA_VERSION:
        raise EntryDecisionF0Error("ENTRY_INPUT_VERSION_INVALID")
    if source["classification"] not in {"SYNTHETIC_ONLY", "DATA_QUALIFIED"}:
        raise EntryDecisionF0Error("ENTRY_INPUT_CLASSIFICATION_INVALID")
    if source["instrument_id"] != "GLD":
        raise EntryDecisionF0Error("ENTRY_INPUT_INSTRUMENT_INVALID")
    if source["data_qualification_status"] not in {
        "STRUCTURALLY_VALID_SYNTHETIC",
        "DATA_QUALIFIED",
        "DATA_NOT_QUALIFIED",
    }:
        raise EntryDecisionF0Error("ENTRY_INPUT_QUALIFICATION_INVALID")
    if type(source["universe_complete"]) is not bool:
        raise EntryDecisionF0Error("ENTRY_INPUT_UNIVERSE_FLAG_INVALID")
    entry_session_date = _date(source["entry_session_date"])
    h20_date = _date(source["h20_date"])
    h20_exit_utc_ns = _integer(source["h20_exit_utc_ns"])
    cutoff = _integer(source["decision_cutoff_utc_ns"])
    facts_max = _integer(source["facts_max_event_utc_ns"])
    assert h20_exit_utc_ns is not None and cutoff is not None and facts_max is not None
    try:
        from gld_management_research.xnys_calendar import (
            action_1045_utc_ns,
            is_session_date,
            next_session_date,
        )

        if not is_session_date(entry_session_date):
            raise EntryDecisionF0Error("ENTRY_SESSION_NOT_XNYS_SESSION")
        expected_entry_cutoff_utc_ns = action_1045_utc_ns(entry_session_date)
        expected_h20_exit_utc_ns = action_1045_utc_ns(h20_date)
        expected_h20_date = entry_session_date
        for _ in range(20):
            expected_h20_date = next_session_date(expected_h20_date).isoformat()
    except EntryDecisionF0Error:
        raise
    except Exception as exc:
        raise EntryDecisionF0Error("H20_SESSION_DATE_INVALID") from exc
    if cutoff != expected_entry_cutoff_utc_ns:
        raise EntryDecisionF0Error("ENTRY_CUTOFF_CLOCK_INVALID")
    if h20_exit_utc_ns != expected_h20_exit_utc_ns:
        raise EntryDecisionF0Error("H20_EXIT_CLOCK_INVALID")
    if h20_date != expected_h20_date:
        raise EntryDecisionF0Error("H20_SESSION_SEQUENCE_INVALID")

    calls_raw = source["call_universe"]
    lc_raw = source["lc0_economics"]
    bcs_raw = source["bcs0_economics"]
    if type(calls_raw) is not list or type(lc_raw) is not list or type(bcs_raw) is not list:
        raise EntryDecisionF0Error("ENTRY_INPUT_LIST_INVALID")
    atomic_snapshot_sha256 = _hash(source["atomic_snapshot_sha256"])
    technical_facts_sha256 = _hash(source["technical_facts_sha256"])
    calls = tuple(
        sorted(
            (
                _validate_call(item)
                for item in calls_raw
            ),
            key=lambda item: item.contract_id,
        )
    )
    call_ids = [item.contract_id for item in calls]
    if len(call_ids) != len(set(call_ids)):
        raise EntryDecisionF0Error("CALL_UNIVERSE_DUPLICATE_IDENTITY")
    entry_date_value = date.fromisoformat(entry_session_date)
    if any(
        not (
            entry_date_value
            <= date.fromisoformat(item.last_trading_date)
            <= date.fromisoformat(item.expiry_date)
        )
        for item in calls
    ):
        raise EntryDecisionF0Error("CALL_CONTRACT_DATE_RELATION_INVALID")
    lc_economics = tuple(sorted((_validate_lc_economics(item) for item in lc_raw), key=lambda item: item.contract_id))
    if len({item.contract_id for item in lc_economics}) != len(lc_economics):
        raise EntryDecisionF0Error("LC0_ECONOMICS_DUPLICATE")
    bcs_economics = tuple(sorted((_validate_bcs_economics(item) for item in bcs_raw), key=lambda item: (item.long_contract_id, item.short_contract_id)))
    if len({(item.long_contract_id, item.short_contract_id) for item in bcs_economics}) != len(bcs_economics):
        raise EntryDecisionF0Error("BCS0_ECONOMICS_DUPLICATE")
    known = set(call_ids)
    if any(item.contract_id not in known for item in lc_economics) or any(
        item.long_contract_id not in known or item.short_contract_id not in known
        for item in bcs_economics
    ):
        raise EntryDecisionF0Error("ENTRY_INPUT_IDENTITY_MISMATCH")

    structure_evidence = _mapping(source["structure_evidence"], frozenset({"LC0", "BCS0"}), "STRUCTURE_EVIDENCE_SET_SCHEMA_INVALID")
    lc_evidence = _validate_evidence(structure_evidence["LC0"], "LC0")
    bcs_evidence = _validate_evidence(structure_evidence["BCS0"], "BCS0")
    if lc_evidence is not None and bcs_evidence is not None:
        if (
            lc_evidence.evidence_receipt_id == bcs_evidence.evidence_receipt_id
            or lc_evidence.episode_cohort_id == bcs_evidence.episode_cohort_id
            or lc_evidence.distribution_id == bcs_evidence.distribution_id
            or lc_evidence.distribution_sha256 == bcs_evidence.distribution_sha256
            or lc_evidence.evidence_sha256 == bcs_evidence.evidence_sha256
        ):
            raise EntryDecisionF0Error("STRUCTURE_EVIDENCE_NOT_INDEPENDENT")

    exit_policies = _mapping(source["exit_policies"], frozenset({"LC0", "BCS0"}), "EXIT_POLICY_SET_SCHEMA_INVALID")
    lc_exit = _validate_exit_policy(exit_policies["LC0"])
    bcs_exit = _validate_exit_policy(exit_policies["BCS0"])
    if lc_exit is not None and bcs_exit is not None and lc_exit.policy_id == bcs_exit.policy_id:
        raise EntryDecisionF0Error("HARD_STOP_POLICY_NOT_INDEPENDENT")
    fees = _mapping(source["planned_exit_fees_nano_usd"], frozenset({"LC0", "BCS0"}), "PLANNED_EXIT_FEES_SCHEMA_INVALID")

    normalized_document = {
        **source,
        "call_universe": [item.as_dict() for item in calls],
        "lc0_economics": [item.as_dict() for item in lc_economics],
        "bcs0_economics": [item.as_dict() for item in bcs_economics],
    }
    encoded = canonical_json_bytes(normalized_document)
    return EntryDecisionInputF0(
        schema_version=ENTRY_DECISION_INPUT_SCHEMA_VERSION,
        classification=source["classification"],  # type: ignore[arg-type]
        instrument_id="GLD",
        data_qualification_status=source["data_qualification_status"],  # type: ignore[arg-type]
        entry_fact_bundle_sha256=_hash(source["entry_fact_bundle_sha256"]),
        technical_facts_sha256=technical_facts_sha256,
        atomic_snapshot_sha256=atomic_snapshot_sha256,
        universe_complete=source["universe_complete"],
        decision_cutoff_utc_ns=cutoff,
        facts_max_event_utc_ns=facts_max,
        open_gld_order_count=_integer(source["open_gld_order_count"], maximum=1_000_000),  # type: ignore[arg-type]
        entry_session_date=entry_session_date,
        h20_date=h20_date,
        h20_exit_utc_ns=h20_exit_utc_ns,
        entry_gate_facts=_validate_gate(source["entry_gate_facts"]),
        account_facts=_validate_account(source["account_facts"]),
        call_universe=calls,
        lc0_economics=lc_economics,
        bcs0_economics=bcs_economics,
        lc0_evidence=lc_evidence,
        bcs0_evidence=bcs_evidence,
        lc0_exit_policy=lc_exit,
        bcs0_exit_policy=bcs_exit,
        planned_exit_fee_lc0_nano_usd=_integer(fees["LC0"], optional=True),
        planned_exit_fee_bcs0_nano_usd=_integer(fees["BCS0"], optional=True),
        input_sha256=canonical_json_sha256(normalized_document),
        canonical_bytes=encoded,
    )


__all__ = [
    "Bcs0EconomicsF0",
    "CallCandidateF0",
    "EntryAccountFactsF0",
    "EntryDecisionInputF0",
    "EntryGateFactsF0",
    "ExitPolicyBindingInputF0",
    "HISTORICAL_STRUCTURE_EVIDENCE_SCHEMA_VERSION",
    "Lc0EconomicsF0",
    "StructureEvidenceSummaryF0",
    "validate_entry_decision_input_f0",
]
