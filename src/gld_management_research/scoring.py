"""Dimension aggregation and fixed Research F0 management policies."""

from __future__ import annotations

from .canonical import canonical_json_bytes, canonical_json_sha256
from .contracts import (
    BreakoutDiagnosticF0,
    DimensionContributionsF0,
    ManagementScoreObservationF0,
    ManagementScoreResultF0,
    PolicyScoreF0,
    UpdatedManagementStateF0,
)
from .errors import ManagementResearchError
from .indicators import FORMULA_CATALOG_SHA256, derive_local_indicators_f0
from .math import PPM, clamp, ppm_multiply, round_half_even_div, sign
from .policy import (
    POLICY_SET_SHA256,
    ScorePolicyDefinitionF0,
    action_policy_f0,
    score_policy_definitions_f0,
    verify_policy_set_f0,
)
from .validation import validate_management_score_observation_f0


SCORE_RESULT_SCHEMA_VERSION = "GLD_MANAGEMENT_SCORE_RESULT_F0_V1"
RESULT_CLASSIFICATION = "RESEARCH_ONLY"
AUTHORITY_STATUS = "NO_DECISION_EFFECT"

_ACTION_POLICY_ACCESSOR_IMPORT = action_policy_f0
_SCORE_POLICY_ACCESSOR_IMPORT = score_policy_definitions_f0
_POLICY_VERIFIER_IMPORT = verify_policy_set_f0


def _sealed_action_policy():
    if (
        action_policy_f0 is not _ACTION_POLICY_ACCESSOR_IMPORT
        or verify_policy_set_f0 is not _POLICY_VERIFIER_IMPORT
    ):
        raise ManagementResearchError("POLICY_RUNTIME_IDENTITY_MISMATCH")
    _POLICY_VERIFIER_IMPORT()
    return _ACTION_POLICY_ACCESSOR_IMPORT()


def _sealed_score_policies():
    if (
        score_policy_definitions_f0 is not _SCORE_POLICY_ACCESSOR_IMPORT
        or verify_policy_set_f0 is not _POLICY_VERIFIER_IMPORT
    ):
        raise ManagementResearchError("POLICY_RUNTIME_IDENTITY_MISMATCH")
    _POLICY_VERIFIER_IMPORT()
    return _SCORE_POLICY_ACCESSOR_IMPORT()

def _require_validated_observation_seal(
    observation: ManagementScoreObservationF0,
) -> None:
    try:
        normalized = validate_management_score_observation_f0(
            observation.as_dict()
        )
    except ManagementResearchError as exc:
        raise ManagementResearchError(
            "OBSERVATION_TYPED_SEAL_INVALID"
        ) from exc
    if (
        normalized.input_sha256 != observation.input_sha256
        or normalized.canonical_bytes != observation.canonical_bytes
    ):
        raise ManagementResearchError("OBSERVATION_TYPED_SEAL_INVALID")


def _dimensions(
    observation: ManagementScoreObservationF0,
    indicators: object,
) -> DimensionContributionsF0:
    current = observation.daily_bars[-1]
    previous = observation.daily_bars[-2]
    sma50 = indicators.sma50_nano_usd
    atr14 = indicators.atr14_nano_usd
    distance = current.close_nano_usd - sma50
    structure_denominator = abs(distance) + atr14
    structure_base = (
        0
        if structure_denominator == 0
        else clamp(
            round_half_even_div(distance * PPM, structure_denominator), -PPM, PPM
        )
    )
    reference_volume = indicators.volume_median20_shares
    current_volume = current.volume_shares
    volume_alignment = sign(current.close_nano_usd - previous.close_nano_usd) * sign(
        distance
    )
    if current_volume <= reference_volume:
        volume_quality = PPM
    else:
        volume_denominator = current_volume + reference_volume
        volume_height = (
            0
            if volume_denominator == 0
            else round_half_even_div(
                (current_volume - reference_volume) * PPM, volume_denominator
            )
        )
        volume_quality = PPM + volume_alignment * volume_height
    structure_with_volume = clamp(
        ppm_multiply(structure_base, volume_quality), -PPM, PPM
    )

    directional_denominator = indicators.plus_di14_ppm + indicators.minus_di14_ppm
    trend_balance = (
        0
        if directional_denominator == 0
        else clamp(
            round_half_even_div(
                (indicators.plus_di14_ppm - indicators.minus_di14_ppm) * PPM,
                directional_denominator,
            ),
            -PPM,
            PPM,
        )
    )
    trend = clamp(ppm_multiply(trend_balance, indicators.adx14_ppm), -PPM, PPM)

    rsi_component = clamp((indicators.rsi14_ppm - 500_000) * 2, -PPM, PPM)
    macd_denominator = abs(indicators.macd_histogram_nano_usd) + atr14
    macd_component = (
        0
        if macd_denominator == 0
        else clamp(
            round_half_even_div(
                indicators.macd_histogram_nano_usd * PPM, macd_denominator
            ),
            -PPM,
            PPM,
        )
    )
    momentum = clamp(round_half_even_div(rsi_component + macd_component, 2), -PPM, PPM)
    momentum_gross = round_half_even_div(
        abs(rsi_component) + abs(macd_component), 2
    )
    momentum_denominator = abs(rsi_component) + abs(macd_component)
    momentum_agreement = (
        0
        if momentum_denominator == 0
        else round_half_even_div(
            abs(rsi_component + macd_component) * PPM, momentum_denominator
        )
    )
    body = {
        "structure_base_ppm": structure_base,
        "volume_quality_ppm": volume_quality,
        "volume_alignment": volume_alignment,
        "structure_with_volume_ppm": structure_with_volume,
        "structure_without_volume_ppm": structure_base,
        "trend_direction_balance_ppm": trend_balance,
        "trend_strength_ppm": indicators.adx14_ppm,
        "trend_ppm": trend,
        "rsi_component_ppm": rsi_component,
        "macd_component_ppm": macd_component,
        "momentum_gross_strength_ppm": momentum_gross,
        "momentum_agreement_ppm": momentum_agreement,
        "momentum_ppm": momentum,
    }
    return DimensionContributionsF0(
        **body,
        dimension_sha256=canonical_json_sha256(body),
    )


def _breakout_diagnostic(
    observation: ManagementScoreObservationF0,
    indicators: object | None,
) -> BreakoutDiagnosticF0:
    current_close = observation.daily_bars[-1].close_nano_usd
    breakout_line = observation.episode.entry_breakout_line_nano_usd
    distance = current_close - breakout_line
    atr = getattr(indicators, "atr14_nano_usd", None)
    distance_atr = (
        None
        if type(atr) is not int or atr <= 0
        else round_half_even_div(distance * PPM, atr)
    )
    relationship = "ABOVE" if distance > 0 else "BELOW" if distance < 0 else "AT"
    body = {
        "authority_status": "NO_ACTION_AUTHORITY",
        "breakout_line_nano_usd": breakout_line,
        "current_close_nano_usd": current_close,
        "distance_nano_usd": distance,
        "distance_atr_ppm": distance_atr,
        "relationship": relationship,
        "source_fact_sha256": observation.episode.entry_breakout_source_sha256,
    }
    return BreakoutDiagnosticF0(
        **body,
        diagnostic_sha256=canonical_json_sha256(body),
    )


def _band(net_signal_ppm: int) -> str:
    policy = _sealed_action_policy()
    if net_signal_ppm >= policy.restore_threshold_ppm:
        return "RESTORE_ELIGIBLE"
    if net_signal_ppm >= policy.defensive_1_threshold_ppm:
        return "HOLD"
    if net_signal_ppm >= policy.defensive_2_threshold_ppm:
        return "DEFENSIVE_1"
    return "DEFENSIVE_2"


def _policy_score(
    policy: ScorePolicyDefinitionF0,
    dimensions: DimensionContributionsF0 | None,
) -> PolicyScoreF0:
    structure_weight = policy.structure_weight_ppm
    trend_weight = policy.trend_weight_ppm
    momentum_weight = policy.momentum_weight_ppm
    volume_enabled = policy.volume_enabled
    action_authority = policy.action_authority
    policy_id = policy.policy_id
    if dimensions is None:
        body = {
            "policy_id": policy_id,
            "weights_ppm": {
                "structure": structure_weight,
                "trend": trend_weight,
                "momentum": momentum_weight,
            },
            "volume_enabled": volume_enabled,
            "contributions_ppm": {
                "structure": None,
                "trend": None,
                "momentum": None,
            },
            "net_signal_ppm": None,
            "gross_strength_ppm": None,
            "agreement_ppm": None,
            "display_score_bp": None,
            "band": "NOT_EVALUABLE",
            "action_authority": action_authority,
        }
        return PolicyScoreF0(
            policy_id=policy_id,
            structure_weight_ppm=structure_weight,
            trend_weight_ppm=trend_weight,
            momentum_weight_ppm=momentum_weight,
            volume_enabled=volume_enabled,
            structure_contribution_ppm=None,
            trend_contribution_ppm=None,
            momentum_contribution_ppm=None,
            net_signal_ppm=None,
            gross_strength_ppm=None,
            agreement_ppm=None,
            display_score_bp=None,
            band="NOT_EVALUABLE",
            action_authority=action_authority,
            policy_result_sha256=canonical_json_sha256(body),
        )
    structure_value = (
        dimensions.structure_with_volume_ppm
        if volume_enabled
        else dimensions.structure_without_volume_ppm
    )
    structure_contribution = ppm_multiply(structure_weight, structure_value)
    trend_contribution = ppm_multiply(trend_weight, dimensions.trend_ppm)
    momentum_contribution = ppm_multiply(momentum_weight, dimensions.momentum_ppm)
    net_signal = clamp(
        structure_contribution + trend_contribution + momentum_contribution,
        -PPM,
        PPM,
    )
    gross_strength = (
        abs(structure_contribution)
        + abs(trend_contribution)
        + abs(momentum_contribution)
    )
    agreement = (
        0
        if gross_strength == 0
        else clamp(
            round_half_even_div(abs(net_signal) * PPM, gross_strength), 0, PPM
        )
    )
    display_score = clamp(
        round_half_even_div((net_signal + PPM) * 10_000, 2 * PPM), 0, 10_000
    )
    band = _band(net_signal)
    body = {
        "policy_id": policy_id,
        "weights_ppm": {
            "structure": structure_weight,
            "trend": trend_weight,
            "momentum": momentum_weight,
        },
        "volume_enabled": volume_enabled,
        "contributions_ppm": {
            "structure": structure_contribution,
            "trend": trend_contribution,
            "momentum": momentum_contribution,
        },
        "net_signal_ppm": net_signal,
        "gross_strength_ppm": gross_strength,
        "agreement_ppm": agreement,
        "display_score_bp": display_score,
        "band": band,
        "action_authority": action_authority,
    }
    return PolicyScoreF0(
        policy_id=policy_id,
        structure_weight_ppm=structure_weight,
        trend_weight_ppm=trend_weight,
        momentum_weight_ppm=momentum_weight,
        volume_enabled=volume_enabled,
        structure_contribution_ppm=structure_contribution,
        trend_contribution_ppm=trend_contribution,
        momentum_contribution_ppm=momentum_contribution,
        net_signal_ppm=net_signal,
        gross_strength_ppm=gross_strength,
        agreement_ppm=agreement,
        display_score_bp=display_score,
        band=band,
        action_authority=action_authority,
        policy_result_sha256=canonical_json_sha256(body),
    )


def _updated_state(
    observation: ManagementScoreObservationF0,
    primary: PolicyScoreF0 | None,
) -> UpdatedManagementStateF0:
    previous = observation.previous_state
    if previous.exit_latch_status == "EXIT_DUE_LATCHED":
        return UpdatedManagementStateF0(
            observation.trading_date,
            0,
            0,
            0,
            "EXIT_DUE_LATCHED",
        )
    if primary is None or primary.net_signal_ppm is None:
        return UpdatedManagementStateF0(
            observation.trading_date,
            0,
            0,
            0,
            previous.exit_latch_status,
        )
    net = primary.net_signal_ppm
    policy = _sealed_action_policy()
    maximum = policy.confirmation_complete_sessions
    return UpdatedManagementStateF0(
        trading_date=observation.trading_date,
        below_40_streak=(
            min(previous.below_40_streak + 1, maximum)
            if net < policy.defensive_1_threshold_ppm
            else 0
        ),
        below_20_streak=(
            min(previous.below_20_streak + 1, maximum)
            if net < policy.defensive_2_threshold_ppm
            else 0
        ),
        above_70_streak=(
            min(previous.above_70_streak + 1, maximum)
            if net >= policy.restore_threshold_ppm
            else 0
        ),
        exit_latch_status=previous.exit_latch_status,
    )


def _result(
    *,
    observation: ManagementScoreObservationF0,
    score_status: str,
    reason_codes: tuple[str, ...],
    indicators: object | None,
    dimensions: DimensionContributionsF0 | None,
) -> ManagementScoreResultF0:
    _POLICY_VERIFIER_IMPORT()
    policies = tuple(
        _policy_score(policy, dimensions)
        for policy in _sealed_score_policies()
    )
    updated = _updated_state(observation, policies[0] if policies else None)
    breakout = _breakout_diagnostic(observation, indicators)
    body = {
        "schema_version": SCORE_RESULT_SCHEMA_VERSION,
        "classification": RESULT_CLASSIFICATION,
        "authority_status": AUTHORITY_STATUS,
        "actionable": False,
        "broker_order_count": 0,
        "instrument_id": observation.instrument_id,
        "trading_date": observation.trading_date,
        "observation_utc_ns": observation.observation_utc_ns,
        "episode": observation.episode.as_dict(),
        "input_sha256": observation.input_sha256,
        "formula_catalog_sha256": FORMULA_CATALOG_SHA256,
        "policy_set_sha256": POLICY_SET_SHA256,
        "score_status": score_status,
        "reason_codes": list(reason_codes),
        "indicators": None if indicators is None else indicators.as_dict(),
        "dimensions": None if dimensions is None else dimensions.as_dict(),
        "breakout_diagnostic": breakout.as_dict(),
        "policy_results": [policy.as_dict() for policy in policies],
        "updated_state": updated.as_dict(),
    }
    result_hash = canonical_json_sha256(body)
    final_body = {**body, "result_sha256": result_hash}
    return ManagementScoreResultF0(
        schema_version=SCORE_RESULT_SCHEMA_VERSION,
        classification=RESULT_CLASSIFICATION,
        authority_status=AUTHORITY_STATUS,
        actionable=False,
        broker_order_count=0,
        instrument_id=observation.instrument_id,
        trading_date=observation.trading_date,
        observation_utc_ns=observation.observation_utc_ns,
        episode=observation.episode,
        input_sha256=observation.input_sha256,
        formula_catalog_sha256=FORMULA_CATALOG_SHA256,
        policy_set_sha256=POLICY_SET_SHA256,
        score_status=score_status,
        reason_codes=reason_codes,
        indicators=indicators,
        dimensions=dimensions,
        breakout_diagnostic=breakout,
        policy_results=policies,
        updated_state=updated,
        result_sha256=result_hash,
        canonical_bytes=canonical_json_bytes(final_body),
    )


def derive_management_score_f0(
    observation: ManagementScoreObservationF0,
) -> ManagementScoreResultF0:
    """Derive local indicators, dimensions, four policies, and streak state."""

    if not isinstance(observation, ManagementScoreObservationF0):
        raise ManagementResearchError("OBSERVATION_TYPED_CONTRACT_REQUIRED")
    if verify_policy_set_f0 is not _POLICY_VERIFIER_IMPORT:
        raise ManagementResearchError("POLICY_RUNTIME_IDENTITY_MISMATCH")
    _POLICY_VERIFIER_IMPORT()
    _require_validated_observation_seal(observation)
    if observation.daily_bars[-1].session_kind != "NORMAL":
        return _result(
            observation=observation,
            score_status="NOT_EVALUABLE",
            reason_codes=("CURRENT_SESSION_NOT_NORMAL",),
            indicators=None,
            dimensions=None,
        )
    try:
        indicators = derive_local_indicators_f0(observation.daily_bars)
        dimensions = _dimensions(observation, indicators)
    except ManagementResearchError as exc:
        return _result(
            observation=observation,
            score_status="NOT_EVALUABLE",
            reason_codes=(exc.reason_code,),
            indicators=None,
            dimensions=None,
        )
    return _result(
        observation=observation,
        score_status="EVALUATED",
        reason_codes=("SCORE_EVALUATED",),
        indicators=indicators,
        dimensions=dimensions,
    )


__all__ = [
    "AUTHORITY_STATUS",
    "POLICY_SET_SHA256",
    "RESULT_CLASSIFICATION",
    "SCORE_RESULT_SCHEMA_VERSION",
    "derive_management_score_f0",
]
