"""Frozen typed contracts for the isolated GLD management research core."""

from __future__ import annotations

from dataclasses import dataclass
import json


@dataclass(frozen=True, slots=True)
class DailyBarF0:
    session_date: str
    session_ordinal: int
    session_kind: str
    session_status: str
    official_close_utc_ns: int
    open_nano_usd: int
    high_nano_usd: int
    low_nano_usd: int
    close_nano_usd: int
    volume_shares: int

    def as_dict(self) -> dict[str, object]:
        return {
            "session_date": self.session_date,
            "session_ordinal": self.session_ordinal,
            "session_kind": self.session_kind,
            "session_status": self.session_status,
            "official_close_utc_ns": self.official_close_utc_ns,
            "open_nano_usd": self.open_nano_usd,
            "high_nano_usd": self.high_nano_usd,
            "low_nano_usd": self.low_nano_usd,
            "close_nano_usd": self.close_nano_usd,
            "volume_shares": self.volume_shares,
        }


@dataclass(frozen=True, slots=True)
class OptionContractF0:
    contract_id: str
    role: str
    expiry_date: str
    strike_nano_usd: int
    multiplier: int
    deliverable_shares: int
    currency: str

    def identity_tuple(self) -> tuple[object, ...]:
        return (
            self.contract_id,
            self.role,
            self.expiry_date,
            self.strike_nano_usd,
            self.multiplier,
            self.deliverable_shares,
            self.currency,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_id": self.contract_id,
            "role": self.role,
            "expiry_date": self.expiry_date,
            "strike_nano_usd": self.strike_nano_usd,
            "multiplier": self.multiplier,
            "deliverable_shares": self.deliverable_shares,
            "currency": self.currency,
        }


@dataclass(frozen=True, slots=True)
class ManagementEpisodeF0:
    episode_id: str
    carrier_id: str
    management_start_date: str
    entry_breakout_line_nano_usd: int
    entry_breakout_source_sha256: str
    original_approved_units: int
    contracts: tuple[OptionContractF0, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "episode_id": self.episode_id,
            "carrier_id": self.carrier_id,
            "management_start_date": self.management_start_date,
            "entry_breakout_line_nano_usd": self.entry_breakout_line_nano_usd,
            "entry_breakout_source_sha256": self.entry_breakout_source_sha256,
            "original_approved_units": self.original_approved_units,
            "contracts": [item.as_dict() for item in self.contracts],
        }


@dataclass(frozen=True, slots=True)
class PreviousManagementStateF0:
    schema_version: str
    lineage_kind: str
    previous_trading_date: str
    episode_id: str
    formula_catalog_sha256: str
    policy_set_sha256: str
    previous_score_result_sha256: str | None
    previous_action_result_sha256: str | None
    previous_primary_band: str
    below_40_streak: int
    below_20_streak: int
    above_70_streak: int
    exit_latch_status: str
    previous_score_result_canonical_bytes: bytes | None
    previous_action_result_canonical_bytes: bytes | None
    state_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "lineage_kind": self.lineage_kind,
            "previous_trading_date": self.previous_trading_date,
            "episode_id": self.episode_id,
            "formula_catalog_sha256": self.formula_catalog_sha256,
            "policy_set_sha256": self.policy_set_sha256,
            "previous_score_result_sha256": self.previous_score_result_sha256,
            "previous_action_result_sha256": self.previous_action_result_sha256,
            "previous_primary_band": self.previous_primary_band,
            "below_40_streak": self.below_40_streak,
            "below_20_streak": self.below_20_streak,
            "above_70_streak": self.above_70_streak,
            "exit_latch_status": self.exit_latch_status,
            "previous_score_result": (
                None
                if self.previous_score_result_canonical_bytes is None
                else json.loads(self.previous_score_result_canonical_bytes)
            ),
            "previous_action_result": (
                None
                if self.previous_action_result_canonical_bytes is None
                else json.loads(self.previous_action_result_canonical_bytes)
            ),
            "state_sha256": self.state_sha256,
        }


@dataclass(frozen=True, slots=True)
class UpdatedManagementStateF0:
    trading_date: str
    below_40_streak: int
    below_20_streak: int
    above_70_streak: int
    exit_latch_status: str

    def as_dict(self) -> dict[str, object]:
        return {
            "trading_date": self.trading_date,
            "below_40_streak": self.below_40_streak,
            "below_20_streak": self.below_20_streak,
            "above_70_streak": self.above_70_streak,
            "exit_latch_status": self.exit_latch_status,
        }


@dataclass(frozen=True, slots=True)
class ManagementScoreObservationF0:
    schema_version: str
    classification: str
    instrument_id: str
    calendar_id: str
    calendar_version_sha256: str
    formula_catalog_version: str
    policy_set_version: str
    trading_date: str
    observation_utc_ns: int
    daily_bars: tuple[DailyBarF0, ...]
    episode: ManagementEpisodeF0
    previous_state: PreviousManagementStateF0
    input_sha256: str
    canonical_bytes: bytes

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "instrument_id": self.instrument_id,
            "calendar_id": self.calendar_id,
            "calendar_version_sha256": self.calendar_version_sha256,
            "formula_catalog_version": self.formula_catalog_version,
            "policy_set_version": self.policy_set_version,
            "trading_date": self.trading_date,
            "observation_utc_ns": self.observation_utc_ns,
            "daily_bars": [item.as_dict() for item in self.daily_bars],
            "episode": self.episode.as_dict(),
            "previous_state": self.previous_state.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class FeatureReceiptF0:
    feature_id: str
    formula_id: str
    formula_version: str
    source_fact_sha256: str
    feature_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "formula_id": self.formula_id,
            "formula_version": self.formula_version,
            "source_fact_sha256": self.source_fact_sha256,
            "feature_sha256": self.feature_sha256,
        }


@dataclass(frozen=True, slots=True)
class LocalIndicatorsF0:
    sma50_nano_usd: int
    atr14_nano_usd: int
    rsi14_ppm: int
    macd_line_nano_usd: int
    macd_signal_nano_usd: int
    macd_histogram_nano_usd: int
    plus_di14_ppm: int
    minus_di14_ppm: int
    adx14_ppm: int
    volume_median20_shares: int
    source_fact_sha256: str
    feature_receipts: tuple[FeatureReceiptF0, ...]
    indicators_sha256: str

    def values_dict(self) -> dict[str, object]:
        return {
            "sma50_nano_usd": self.sma50_nano_usd,
            "atr14_nano_usd": self.atr14_nano_usd,
            "rsi14_ppm": self.rsi14_ppm,
            "macd_line_nano_usd": self.macd_line_nano_usd,
            "macd_signal_nano_usd": self.macd_signal_nano_usd,
            "macd_histogram_nano_usd": self.macd_histogram_nano_usd,
            "plus_di14_ppm": self.plus_di14_ppm,
            "minus_di14_ppm": self.minus_di14_ppm,
            "adx14_ppm": self.adx14_ppm,
            "volume_median20_shares": self.volume_median20_shares,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            **self.values_dict(),
            "source_fact_sha256": self.source_fact_sha256,
            "feature_receipts": [item.as_dict() for item in self.feature_receipts],
            "indicators_sha256": self.indicators_sha256,
        }


@dataclass(frozen=True, slots=True)
class DimensionContributionsF0:
    structure_base_ppm: int
    volume_quality_ppm: int
    volume_alignment: int
    structure_with_volume_ppm: int
    structure_without_volume_ppm: int
    trend_direction_balance_ppm: int
    trend_strength_ppm: int
    trend_ppm: int
    rsi_component_ppm: int
    macd_component_ppm: int
    momentum_gross_strength_ppm: int
    momentum_agreement_ppm: int
    momentum_ppm: int
    dimension_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "structure_base_ppm": self.structure_base_ppm,
            "volume_quality_ppm": self.volume_quality_ppm,
            "volume_alignment": self.volume_alignment,
            "structure_with_volume_ppm": self.structure_with_volume_ppm,
            "structure_without_volume_ppm": self.structure_without_volume_ppm,
            "trend_direction_balance_ppm": self.trend_direction_balance_ppm,
            "trend_strength_ppm": self.trend_strength_ppm,
            "trend_ppm": self.trend_ppm,
            "rsi_component_ppm": self.rsi_component_ppm,
            "macd_component_ppm": self.macd_component_ppm,
            "momentum_gross_strength_ppm": self.momentum_gross_strength_ppm,
            "momentum_agreement_ppm": self.momentum_agreement_ppm,
            "momentum_ppm": self.momentum_ppm,
            "dimension_sha256": self.dimension_sha256,
        }


@dataclass(frozen=True, slots=True)
class BreakoutDiagnosticF0:
    authority_status: str
    breakout_line_nano_usd: int
    current_close_nano_usd: int
    distance_nano_usd: int
    distance_atr_ppm: int | None
    relationship: str
    source_fact_sha256: str
    diagnostic_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "authority_status": self.authority_status,
            "breakout_line_nano_usd": self.breakout_line_nano_usd,
            "current_close_nano_usd": self.current_close_nano_usd,
            "distance_nano_usd": self.distance_nano_usd,
            "distance_atr_ppm": self.distance_atr_ppm,
            "relationship": self.relationship,
            "source_fact_sha256": self.source_fact_sha256,
            "diagnostic_sha256": self.diagnostic_sha256,
        }


@dataclass(frozen=True, slots=True)
class PolicyScoreF0:
    policy_id: str
    structure_weight_ppm: int
    trend_weight_ppm: int
    momentum_weight_ppm: int
    volume_enabled: bool
    structure_contribution_ppm: int | None
    trend_contribution_ppm: int | None
    momentum_contribution_ppm: int | None
    net_signal_ppm: int | None
    gross_strength_ppm: int | None
    agreement_ppm: int | None
    display_score_bp: int | None
    band: str
    action_authority: bool
    policy_result_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "weights_ppm": {
                "structure": self.structure_weight_ppm,
                "trend": self.trend_weight_ppm,
                "momentum": self.momentum_weight_ppm,
            },
            "volume_enabled": self.volume_enabled,
            "contributions_ppm": {
                "structure": self.structure_contribution_ppm,
                "trend": self.trend_contribution_ppm,
                "momentum": self.momentum_contribution_ppm,
            },
            "net_signal_ppm": self.net_signal_ppm,
            "gross_strength_ppm": self.gross_strength_ppm,
            "agreement_ppm": self.agreement_ppm,
            "display_score_bp": self.display_score_bp,
            "band": self.band,
            "action_authority": self.action_authority,
            "policy_result_sha256": self.policy_result_sha256,
        }


@dataclass(frozen=True, slots=True)
class ManagementScoreResultF0:
    schema_version: str
    classification: str
    authority_status: str
    actionable: bool
    broker_order_count: int
    instrument_id: str
    trading_date: str
    observation_utc_ns: int
    episode: ManagementEpisodeF0
    input_sha256: str
    formula_catalog_sha256: str
    policy_set_sha256: str
    score_status: str
    reason_codes: tuple[str, ...]
    indicators: LocalIndicatorsF0 | None
    dimensions: DimensionContributionsF0 | None
    breakout_diagnostic: BreakoutDiagnosticF0
    policy_results: tuple[PolicyScoreF0, ...]
    updated_state: UpdatedManagementStateF0
    result_sha256: str
    canonical_bytes: bytes

    @property
    def primary(self) -> PolicyScoreF0:
        return self.policy_results[0]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "authority_status": self.authority_status,
            "actionable": self.actionable,
            "broker_order_count": self.broker_order_count,
            "instrument_id": self.instrument_id,
            "trading_date": self.trading_date,
            "observation_utc_ns": self.observation_utc_ns,
            "episode": self.episode.as_dict(),
            "input_sha256": self.input_sha256,
            "formula_catalog_sha256": self.formula_catalog_sha256,
            "policy_set_sha256": self.policy_set_sha256,
            "score_status": self.score_status,
            "reason_codes": list(self.reason_codes),
            "indicators": None if self.indicators is None else self.indicators.as_dict(),
            "dimensions": None if self.dimensions is None else self.dimensions.as_dict(),
            "breakout_diagnostic": self.breakout_diagnostic.as_dict(),
            "policy_results": [item.as_dict() for item in self.policy_results],
            "updated_state": self.updated_state.as_dict(),
            "result_sha256": self.result_sha256,
        }


@dataclass(frozen=True, slots=True)
class QuoteF0:
    contract_id: str
    bid_nano_usd: int
    ask_nano_usd: int
    bid_size: int
    ask_size: int
    tick_nano_usd: int
    event_utc_ns: int
    receive_utc_ns: int

    def as_dict(self) -> dict[str, object]:
        return {
            "contract_id": self.contract_id,
            "bid_nano_usd": self.bid_nano_usd,
            "ask_nano_usd": self.ask_nano_usd,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "tick_nano_usd": self.tick_nano_usd,
            "event_utc_ns": self.event_utc_ns,
            "receive_utc_ns": self.receive_utc_ns,
        }


@dataclass(frozen=True, slots=True)
class ManagementActionSnapshotF0:
    schema_version: str
    classification: str
    instrument_id: str
    action_trading_date: str
    action_utc_ns: int
    episode_id: str
    carrier_id: str
    contracts: tuple[OptionContractF0, ...]
    current_units: int
    account_risk_cap_units: int | None
    liquidity_cap_units: int | None
    external_risk_cap_units: int | None
    dte: int | None
    fee_facts_status: str
    reconciliation_status: str
    override_status: str
    quotes: tuple[QuoteF0, ...]
    snapshot_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "instrument_id": self.instrument_id,
            "action_trading_date": self.action_trading_date,
            "action_utc_ns": self.action_utc_ns,
            "episode_id": self.episode_id,
            "carrier_id": self.carrier_id,
            "contracts": [item.as_dict() for item in self.contracts],
            "current_units": self.current_units,
            "account_risk_cap_units": self.account_risk_cap_units,
            "liquidity_cap_units": self.liquidity_cap_units,
            "external_risk_cap_units": self.external_risk_cap_units,
            "dte": self.dte,
            "fee_facts_status": self.fee_facts_status,
            "reconciliation_status": self.reconciliation_status,
            "override_status": self.override_status,
            "quotes": [item.as_dict() for item in self.quotes],
        }


@dataclass(frozen=True, slots=True)
class ManagementTerminalOverrideF0:
    schema_version: str
    classification: str
    instrument_id: str
    event_utc_ns: int
    episode_id: str
    carrier_id: str
    contracts: tuple[OptionContractF0, ...]
    current_units: int
    override_status: str
    snapshot_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "instrument_id": self.instrument_id,
            "event_utc_ns": self.event_utc_ns,
            "episode_id": self.episode_id,
            "carrier_id": self.carrier_id,
            "contracts": [item.as_dict() for item in self.contracts],
            "current_units": self.current_units,
            "override_status": self.override_status,
        }


@dataclass(frozen=True, slots=True)
class HypotheticalManagementActionF0:
    schema_version: str
    classification: str
    authority_status: str
    actionable: bool
    broker_order_count: int
    score_authority: str
    action: str
    reason_code: str
    current_units: int
    target_units: int
    quantity_change_units: int
    score_cap_units: int | None
    effective_cap_units: int | None
    exit_latch_status_before: str
    exit_latch_status_after: str
    input_score_sha256: str
    action_snapshot_sha256: str
    action_policy_sha256: str
    action_result_sha256: str
    canonical_bytes: bytes

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "authority_status": self.authority_status,
            "actionable": self.actionable,
            "broker_order_count": self.broker_order_count,
            "score_authority": self.score_authority,
            "action": self.action,
            "reason_code": self.reason_code,
            "current_units": self.current_units,
            "target_units": self.target_units,
            "quantity_change_units": self.quantity_change_units,
            "score_cap_units": self.score_cap_units,
            "effective_cap_units": self.effective_cap_units,
            "exit_latch_status_before": self.exit_latch_status_before,
            "exit_latch_status_after": self.exit_latch_status_after,
            "input_score_sha256": self.input_score_sha256,
            "action_snapshot_sha256": self.action_snapshot_sha256,
            "action_policy_sha256": self.action_policy_sha256,
            "action_result_sha256": self.action_result_sha256,
        }


__all__ = [
    "BreakoutDiagnosticF0",
    "DailyBarF0",
    "DimensionContributionsF0",
    "FeatureReceiptF0",
    "HypotheticalManagementActionF0",
    "LocalIndicatorsF0",
    "ManagementActionSnapshotF0",
    "ManagementEpisodeF0",
    "ManagementScoreObservationF0",
    "ManagementScoreResultF0",
    "ManagementTerminalOverrideF0",
    "OptionContractF0",
    "PolicyScoreF0",
    "PreviousManagementStateF0",
    "QuoteF0",
    "UpdatedManagementStateF0",
]
