"""Deterministic American-call CRR implied-volatility and Delta engine.

The engine accepts only already-normalized, hash-bound integer facts.  It does
not qualify those facts, contact a broker, consume vendor Greeks, or create an
order.  Black-Scholes is present solely as a private synthetic-test oracle and
is never a fallback path for :func:`compute_american_call_delta`.
"""

from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass, field, fields
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    localcontext,
)
from hashlib import sha256
import json
import math
from pathlib import Path
import platform
import re
import sys
from types import FunctionType, SimpleNamespace
import weakref

from gld_normalizer.errors import NormalizationError
from gld_normalizer.readers import (
    MAX_DEFINED_I64 as _MAX_DEFINED_I64,
    MAX_DEFINED_U64 as _MAX_DEFINED_U64,
)

from .facts import (
    OptionContractV1,
    OptionQuoteSnapshotV1,
    TopOfBookV1,
    canonical_snapshot_sha256,
)


MODEL_ID = "GLD_CALL_DELTA_CRR_AM_V1"
MODEL_CONTRACT_SHA256 = (
    "06a4e216aa0a0f0c2e7192027ac5a19d288f799d32da59bf632bb26e7678caa8"
)
FULL_CHAIN_EXECUTION_STATUS = (
    "BLOCKED_REFERENCE_ENGINE_NOT_REALTIME_QUALIFIED"
)
_COARSE_STEPS = (512, 513)
_FINE_STEPS = (1024, 1025)
_IV_LOWER = 0.0001
_IV_UPPER = 5.0
_BISECTION_ITERATIONS = 32
_PRICE_RESIDUAL_FLOOR_USD = 0.000001
_IV_SUITE_TOLERANCE = 0.0001
_DELTA_SUITE_TOLERANCE = 0.0005
_NANO_PER_USD = 1_000_000_000
_PPM = 1_000_000
_YEAR_NS = 365 * 86_400 * _NANO_PER_USD
_MAX_MATURITY_NS = 10 * _YEAR_NS
_MAX_ABS_RATE_PPM = 1_000_000
_MAX_CONTROL_TEXT_CHARS = 128
_DECIMAL_CONTEXT_PRECISION = 50
_DECIMAL_CONTEXT_EMIN = -99
_DECIMAL_CONTEXT_EMAX = 99
_DECIMAL_CONTEXT_CAPITALS = 1
_DECIMAL_CONTEXT_CLAMP = 0
_DECIMAL_CONTEXT_TRAPS = (DivisionByZero, InvalidOperation, Overflow)
_DECIMAL_CONTEXT_TRAP_NAMES = tuple(
    signal.__name__ for signal in _DECIMAL_CONTEXT_TRAPS
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _model_config_json() -> str:
    config: dict[str, object] = {
        "algorithm": "AMERICAN_CRR_CONTINUOUS_EFFECTIVE_YIELD",
        "bisection_iterations": _BISECTION_ITERATIONS,
        "coarse_steps": list(_COARSE_STEPS),
        "contract_sha256": MODEL_CONTRACT_SHA256,
        "decimal_context": {
            "capitals": _DECIMAL_CONTEXT_CAPITALS,
            "clamp": _DECIMAL_CONTEXT_CLAMP,
            "emax": _DECIMAL_CONTEXT_EMAX,
            "emin": _DECIMAL_CONTEXT_EMIN,
            "precision": _DECIMAL_CONTEXT_PRECISION,
            "rounding": "ROUND_HALF_EVEN",
            "traps": list(_DECIMAL_CONTEXT_TRAP_NAMES),
        },
        "delta_quantization": "DECIMAL_ROUND_HALF_EVEN_PPM",
        "delta_suite_tolerance_ppm": 500,
        "effective_yield": "expense_yield_plus_borrow_yield",
        "fine_steps": list(_FINE_STEPS),
        "iv_bracket_ppm": [100, 5_000_000],
        "iv_invalid_low_probability_trial": (
            "ADVANCE_LOWER_ONLY_FINAL_ROOT_MUST_HAVE_VALID_TREE_AND_RESIDUAL"
        ),
        "iv_lower_endpoint_policy": (
            "BELOW_FIXED_ENDPOINT_NO_BRACKET_WITHIN_TOLERANCE_UNIDENTIFIABLE"
        ),
        "iv_quantization": "DECIMAL_ROUND_HALF_EVEN_PPM",
        "iv_suite_tolerance_ppm": 100,
        "max_abs_input_rate_ppm": _MAX_ABS_RATE_PPM,
        "max_defined_i64": _MAX_DEFINED_I64,
        "max_defined_u64": _MAX_DEFINED_U64,
        "max_maturity_act_365f_years": 10,
        "model_id": MODEL_ID,
        "price_residual_floor_nano_usd": 1_000,
        "price_residual_tick_divisor": 100,
        "root_delta": "T1_OPTIMAL_NODE_VALUES",
        "root_delta_boundary_epsilon": "1e-12",
        "suite_aggregation": "ARITHMETIC_MEAN_PRICE_AND_DELTA_SUM_EXERCISE_NODES",
        "time_basis": "ACT_365F",
        "zero_volatility_bracket_limit": (
            "MAX_DISCOUNTED_DETERMINISTIC_AMERICAN_EXERCISE_VALUE"
        ),
    }
    return json.dumps(
        config,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


MODEL_CONFIG_JSON = _model_config_json()
MODEL_CONFIG_SHA256 = sha256(MODEL_CONFIG_JSON.encode("ascii")).hexdigest()
# This is captured once from the imported source artifact.  The digest is not
# embedded back into the file, so it avoids a self-referential literal hash.
MODEL_SOURCE_ARTIFACT_SHA256 = sha256(Path(__file__).read_bytes()).hexdigest()
MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256 = sha256(
    Path(canonical_snapshot_sha256.__code__.co_filename).read_bytes()
).hexdigest()


def _require_exact_int(
    value: object,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise NormalizationError("DELTA_INPUT_MISSING")
    return value


def _require_binding_hash(value: object) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise NormalizationError("DELTA_INPUT_BINDING_MISMATCH")
    return value


def _require_contract_id(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > _MAX_CONTROL_TEXT_CHARS
        or not value.isascii()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise NormalizationError("DELTA_INPUT_BINDING_MISMATCH")
    return value


@dataclass(frozen=True, slots=True)
class CrrModelArtifactManifestV1:
    """Immutable identity of the frozen contract, config, and source bytes."""

    model_id: str
    contract_sha256: str
    config_sha256: str
    source_artifact_sha256: str
    canonical_encoder_source_artifact_sha256: str

    def __post_init__(self) -> None:
        if self.model_id != MODEL_ID:
            raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")
        for value in (
            self.contract_sha256,
            self.config_sha256,
            self.source_artifact_sha256,
            self.canonical_encoder_source_artifact_sha256,
        ):
            _require_binding_hash(value)
        if (
            self.contract_sha256 != MODEL_CONTRACT_SHA256
            or self.config_sha256 != MODEL_CONFIG_SHA256
            or self.source_artifact_sha256 != MODEL_SOURCE_ARTIFACT_SHA256
            or self.canonical_encoder_source_artifact_sha256
            != MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256
        ):
            raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")

    @property
    def artifact_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class CrrRuntimeFingerprintV1:
    """Stable process-runtime facts that may affect float/libm behaviour."""

    python_implementation: str
    python_version: str
    python_cache_tag: str
    python_compiler: str
    operating_system: str
    operating_system_release: str
    machine: str
    byteorder: str
    float_mant_dig: int
    float_rounds: int

    def __post_init__(self) -> None:
        for value in (
            self.python_implementation,
            self.python_version,
            self.python_cache_tag,
            self.python_compiler,
            self.operating_system,
            self.operating_system_release,
            self.machine,
            self.byteorder,
        ):
            _require_contract_id(value)
        _require_exact_int(self.float_mant_dig, minimum=1, maximum=1_000)
        _require_exact_int(self.float_rounds, minimum=-1, maximum=3)

    @classmethod
    def current(cls) -> CrrRuntimeFingerprintV1:
        return cls(
            python_implementation=platform.python_implementation(),
            python_version=platform.python_version(),
            python_cache_tag=sys.implementation.cache_tag or "NO_CACHE_TAG",
            python_compiler=platform.python_compiler() or "UNKNOWN_COMPILER",
            operating_system=platform.system() or "UNKNOWN_SYSTEM",
            operating_system_release=platform.release() or "UNKNOWN_RELEASE",
            machine=platform.machine() or "UNKNOWN_MACHINE",
            byteorder=sys.byteorder,
            float_mant_dig=sys.float_info.mant_dig,
            float_rounds=sys.float_info.rounds,
        )

    @property
    def fingerprint_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


MODEL_ARTIFACT_MANIFEST = CrrModelArtifactManifestV1(
    model_id=MODEL_ID,
    contract_sha256=MODEL_CONTRACT_SHA256,
    config_sha256=MODEL_CONFIG_SHA256,
    source_artifact_sha256=MODEL_SOURCE_ARTIFACT_SHA256,
    canonical_encoder_source_artifact_sha256=(
        MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256
    ),
)
MODEL_SHA256 = MODEL_ARTIFACT_MANIFEST.artifact_sha256
RUNTIME_FINGERPRINT = CrrRuntimeFingerprintV1.current()
RUNTIME_FINGERPRINT_SHA256 = RUNTIME_FINGERPRINT.fingerprint_sha256


@dataclass(frozen=True, slots=True)
class CrrPitInputsV1:
    """Structurally point-in-time rate/yield assumptions at one exact instant."""

    as_of_utc_ns: int
    risk_free_rate_ppm: int
    expense_yield_ppm: int
    borrow_yield_ppm: int
    rate_curve_sha256: str
    distribution_assumption_sha256: str
    borrow_assumption_sha256: str

    def __post_init__(self) -> None:
        _require_exact_int(
            self.as_of_utc_ns,
            minimum=1,
            maximum=_MAX_DEFINED_U64,
        )
        for value in (
            self.risk_free_rate_ppm,
            self.expense_yield_ppm,
            self.borrow_yield_ppm,
        ):
            _require_exact_int(
                value,
                minimum=-_MAX_ABS_RATE_PPM,
                maximum=_MAX_ABS_RATE_PPM,
            )
        for value in (
            self.rate_curve_sha256,
            self.distribution_assumption_sha256,
            self.borrow_assumption_sha256,
        ):
            _require_binding_hash(value)

    @property
    def pit_inputs_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True)
class CrrCallInputsV1:
    """Structurally valid local model inputs; not proof of source authority."""

    option_snapshot_sha256: str
    contract_id: str
    contract_sha256: str
    underlying_top_sha256: str
    option_quote_sha256: str
    pit_inputs: CrrPitInputsV1
    spot_nano_usd: int
    strike_nano_usd: int
    option_mid_nano_usd: int
    tick_nano_usd: int
    valuation_utc_ns: int
    expiry_utc_ns: int

    def __post_init__(self) -> None:
        for value in (
            self.option_snapshot_sha256,
            self.contract_sha256,
            self.underlying_top_sha256,
            self.option_quote_sha256,
        ):
            _require_binding_hash(value)
        if not isinstance(self.pit_inputs, CrrPitInputsV1):
            raise NormalizationError("DELTA_INPUT_MISSING")
        _require_contract_id(self.contract_id)
        for value in (
            self.spot_nano_usd,
            self.strike_nano_usd,
            self.option_mid_nano_usd,
            self.tick_nano_usd,
        ):
            _require_exact_int(value, minimum=1, maximum=_MAX_DEFINED_I64)
        valuation = _require_exact_int(
            self.valuation_utc_ns,
            minimum=1,
            maximum=_MAX_DEFINED_U64,
        )
        expiry = _require_exact_int(
            self.expiry_utc_ns,
            minimum=1,
            maximum=_MAX_DEFINED_U64,
        )
        if expiry <= valuation or expiry - valuation > _MAX_MATURITY_NS:
            raise NormalizationError("T_OUT_OF_DOMAIN")
        if self.pit_inputs.as_of_utc_ns != valuation:
            raise NormalizationError("SNAPSHOT_NOT_CAUSAL")

    @property
    def pit_inputs_sha256(self) -> str:
        return self.pit_inputs.pit_inputs_sha256

    @property
    def risk_free_rate_ppm(self) -> int:
        return self.pit_inputs.risk_free_rate_ppm

    @property
    def expense_yield_ppm(self) -> int:
        return self.pit_inputs.expense_yield_ppm

    @property
    def borrow_yield_ppm(self) -> int:
        return self.pit_inputs.borrow_yield_ppm

    @property
    def rate_curve_sha256(self) -> str:
        return self.pit_inputs.rate_curve_sha256

    @property
    def distribution_assumption_sha256(self) -> str:
        return self.pit_inputs.distribution_assumption_sha256

    @property
    def borrow_assumption_sha256(self) -> str:
        return self.pit_inputs.borrow_assumption_sha256

    @property
    def input_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


def _exact_midpoint_nano_usd(book: TopOfBookV1) -> int:
    total = book.bid_nano_usd + book.ask_nano_usd
    if total % 2:
        raise NormalizationError("DELTA_INPUT_MIDPOINT_NON_INTEGRAL")
    return total // 2


def bind_crr_call_inputs_from_snapshot(
    snapshot: OptionQuoteSnapshotV1,
    *,
    contract: OptionContractV1,
    pit_inputs: CrrPitInputsV1,
) -> CrrCallInputsV1:
    """Derive one canonical Call input from one immutable snapshot.

    This proves structural and temporal consistency only.  It does not qualify
    the provider, rate curve, distribution coverage, or borrow authority.
    """

    if (
        not isinstance(snapshot, OptionQuoteSnapshotV1)
        or not isinstance(contract, OptionContractV1)
        or not isinstance(pit_inputs, CrrPitInputsV1)
    ):
        raise NormalizationError("DELTA_INPUT_MISSING")
    if pit_inputs.as_of_utc_ns != snapshot.capture_utc_ns:
        raise NormalizationError("SNAPSHOT_NOT_CAUSAL")

    matching_quotes = tuple(
        quote
        for quote in snapshot.option_quotes
        if quote.contract.occ_symbol == contract.occ_symbol
    )
    if len(matching_quotes) != 1 or matching_quotes[0].contract != contract:
        raise NormalizationError("DELTA_INPUT_BINDING_MISMATCH")
    option_quote = matching_quotes[0]

    for book in (snapshot.underlying_top, option_quote.top_of_book):
        if (
            book.ts_event_ns > book.ts_recv_ns
            or book.ts_recv_ns > snapshot.capture_utc_ns
        ):
            raise NormalizationError("SNAPSHOT_NOT_CAUSAL")

    return CrrCallInputsV1(
        option_snapshot_sha256=snapshot.snapshot_sha256,
        contract_id=contract.occ_symbol,
        contract_sha256=canonical_snapshot_sha256(contract),
        underlying_top_sha256=canonical_snapshot_sha256(snapshot.underlying_top),
        option_quote_sha256=canonical_snapshot_sha256(option_quote),
        pit_inputs=pit_inputs,
        spot_nano_usd=_exact_midpoint_nano_usd(snapshot.underlying_top),
        strike_nano_usd=contract.strike_nano_usd,
        option_mid_nano_usd=_exact_midpoint_nano_usd(option_quote.top_of_book),
        tick_nano_usd=option_quote.top_of_book.tick_nano_usd,
        valuation_utc_ns=snapshot.capture_utc_ns,
        expiry_utc_ns=contract.expiry_utc_ns,
    )


@dataclass(frozen=True, slots=True)
class CrrRunManifestV1:
    """Immutable identity of one model invocation, excluding its output."""

    model_contract_sha256: str
    model_config_sha256: str
    model_source_artifact_sha256: str
    canonical_encoder_source_artifact_sha256: str
    model_sha256: str
    runtime_fingerprint_sha256: str
    input_sha256: str

    def __post_init__(self) -> None:
        for value in (
            self.model_contract_sha256,
            self.model_config_sha256,
            self.model_source_artifact_sha256,
            self.canonical_encoder_source_artifact_sha256,
            self.model_sha256,
            self.runtime_fingerprint_sha256,
            self.input_sha256,
        ):
            _require_binding_hash(value)
        if (
            self.model_contract_sha256 != MODEL_CONTRACT_SHA256
            or self.model_config_sha256 != MODEL_CONFIG_SHA256
            or self.model_source_artifact_sha256 != MODEL_SOURCE_ARTIFACT_SHA256
            or self.canonical_encoder_source_artifact_sha256
            != MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256
            or self.model_sha256 != MODEL_SHA256
            or self.runtime_fingerprint_sha256 != RUNTIME_FINGERPRINT_SHA256
        ):
            raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")

    @property
    def run_sha256(self) -> str:
        return canonical_snapshot_sha256(self)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class CrrDeltaResultV1:
    model_id: str
    model_sha256: str
    input_sha256: str
    option_snapshot_sha256: str
    contract_id: str
    iv_ppm: int
    delta_ppm: int
    coarse_iv_ppm: int
    fine_iv_ppm: int
    coarse_delta_ppm: int
    fine_delta_ppm: int
    coarse_price_residual_nano_usd: int
    fine_price_residual_nano_usd: int
    coarse_early_exercise_nodes: int
    fine_early_exercise_nodes: int
    model_contract_sha256: str = field(init=False)
    model_config_sha256: str = field(init=False)
    model_source_artifact_sha256: str = field(init=False)
    canonical_encoder_source_artifact_sha256: str = field(init=False)
    runtime_fingerprint_sha256: str = field(init=False)
    run_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if self.model_id != MODEL_ID or self.model_sha256 != MODEL_SHA256:
            raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")
        _require_binding_hash(self.input_sha256)
        _require_binding_hash(self.option_snapshot_sha256)
        _require_contract_id(self.contract_id)
        for value in (self.iv_ppm, self.coarse_iv_ppm, self.fine_iv_ppm):
            _require_exact_int(value, minimum=100, maximum=5_000_000)
        for value in (
            self.delta_ppm,
            self.coarse_delta_ppm,
            self.fine_delta_ppm,
        ):
            if type(value) is not int or not 0 <= value <= _PPM:
                raise NormalizationError("DELTA_OUT_OF_RANGE")
        for value in (
            self.coarse_price_residual_nano_usd,
            self.fine_price_residual_nano_usd,
            self.coarse_early_exercise_nodes,
            self.fine_early_exercise_nodes,
        ):
            _require_exact_int(value, minimum=0, maximum=_MAX_DEFINED_I64)
        if self.iv_ppm != self.fine_iv_ppm or self.delta_ppm != self.fine_delta_ppm:
            raise NormalizationError("TREE_NOT_CONVERGED")
        if (
            abs(self.coarse_iv_ppm - self.fine_iv_ppm) > 100
            or abs(self.coarse_delta_ppm - self.fine_delta_ppm) > 500
        ):
            raise NormalizationError("TREE_NOT_CONVERGED")
        run_manifest = CrrRunManifestV1(
            model_contract_sha256=MODEL_CONTRACT_SHA256,
            model_config_sha256=MODEL_CONFIG_SHA256,
            model_source_artifact_sha256=MODEL_SOURCE_ARTIFACT_SHA256,
            canonical_encoder_source_artifact_sha256=(
                MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256
            ),
            model_sha256=MODEL_SHA256,
            runtime_fingerprint_sha256=RUNTIME_FINGERPRINT_SHA256,
            input_sha256=self.input_sha256,
        )
        object.__setattr__(
            self,
            "model_contract_sha256",
            run_manifest.model_contract_sha256,
        )
        object.__setattr__(
            self,
            "model_config_sha256",
            run_manifest.model_config_sha256,
        )
        object.__setattr__(
            self,
            "model_source_artifact_sha256",
            run_manifest.model_source_artifact_sha256,
        )
        object.__setattr__(
            self,
            "canonical_encoder_source_artifact_sha256",
            run_manifest.canonical_encoder_source_artifact_sha256,
        )
        object.__setattr__(
            self,
            "runtime_fingerprint_sha256",
            run_manifest.runtime_fingerprint_sha256,
        )
        object.__setattr__(self, "run_sha256", run_manifest.run_sha256)


@dataclass(frozen=True, slots=True)
class _TreeEvaluation:
    price: float
    delta: float
    early_exercise_nodes: int


@dataclass(frozen=True, slots=True)
class _SuiteEvaluation:
    volatility: float
    price: float
    delta: float
    price_residual: float
    early_exercise_nodes: int


def _crr_american_call_tree(
    *,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    effective_yield: float,
    volatility: float,
    steps: int,
) -> _TreeEvaluation:
    """Price one American call tree and use optimal t1 nodes for root Delta."""

    scalar_inputs = (spot, strike, years, rate, effective_yield, volatility)
    if (
        any(not math.isfinite(value) for value in scalar_inputs)
        or spot <= 0.0
        or strike <= 0.0
        or years <= 0.0
        or volatility <= 0.0
        or type(steps) is not int
        or steps < 2
    ):
        raise NormalizationError("TREE_NUMERIC_INVALID")

    dt = years / steps
    log_up = volatility * math.sqrt(dt)
    try:
        up = math.exp(log_up)
        down = 1.0 / up
        denominator = up - down
        growth = math.exp((rate - effective_yield) * dt)
        discount = math.exp(-rate * dt)
    except (OverflowError, ZeroDivisionError) as error:
        raise NormalizationError("TREE_NUMERIC_INVALID") from error
    if (
        not all(
            math.isfinite(value)
            for value in (dt, log_up, up, down, denominator, growth, discount)
        )
        or denominator <= 0.0
        or discount <= 0.0
    ):
        raise NormalizationError("TREE_NUMERIC_INVALID")
    probability = (growth - down) / denominator
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise NormalizationError("TREE_PROBABILITY_INVALID")

    log_spot = math.log(spot)
    if log_spot + steps * log_up >= math.log(float.fromhex("0x1.fffffffffffffp+1023")):
        raise NormalizationError("TREE_NUMERIC_INVALID")
    try:
        base_asset = spot * math.exp(-steps * log_up)
    except OverflowError as error:  # pragma: no cover - guarded by bounded inputs.
        raise NormalizationError("TREE_NUMERIC_INVALID") from error
    asset_ratio = up * up
    values = [0.0] * (steps + 1)
    asset = base_asset
    for node in range(steps + 1):
        intrinsic = asset - strike
        values[node] = intrinsic if intrinsic > 0.0 else 0.0
        asset *= asset_ratio

    down_weight = 1.0 - probability
    early_exercise_nodes = 0
    t1_down = math.nan
    t1_up = math.nan
    for level in range(steps - 1, -1, -1):
        base_asset *= up
        asset = base_asset
        for node in range(level + 1):
            continuation = discount * (
                down_weight * values[node] + probability * values[node + 1]
            )
            intrinsic = asset - strike
            if intrinsic > continuation:
                values[node] = intrinsic
                materiality = 1e-12 * max(1.0, abs(intrinsic), abs(continuation))
                if intrinsic > 0.0 and intrinsic - continuation > materiality:
                    early_exercise_nodes += 1
            else:
                values[node] = continuation
            asset *= asset_ratio
        if level == 1:
            t1_down = values[0]
            t1_up = values[1]

    root_denominator = spot * denominator
    if root_denominator <= 0.0:
        raise NormalizationError("TREE_NUMERIC_INVALID")
    delta = (t1_up - t1_down) / root_denominator
    price = values[0]
    if not math.isfinite(price) or not math.isfinite(delta) or price < 0.0:
        raise NormalizationError("TREE_NUMERIC_INVALID")
    if delta < -1e-12 or delta > 1.0 + 1e-12:
        raise NormalizationError("DELTA_OUT_OF_RANGE")
    delta = min(1.0, max(0.0, delta))
    return _TreeEvaluation(
        price=price,
        delta=delta,
        early_exercise_nodes=early_exercise_nodes,
    )


def _suite_evaluation(
    *,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    effective_yield: float,
    volatility: float,
    target_price: float,
    steps: tuple[int, int],
) -> _SuiteEvaluation:
    first = _crr_american_call_tree(
        spot=spot,
        strike=strike,
        years=years,
        rate=rate,
        effective_yield=effective_yield,
        volatility=volatility,
        steps=steps[0],
    )
    second = _crr_american_call_tree(
        spot=spot,
        strike=strike,
        years=years,
        rate=rate,
        effective_yield=effective_yield,
        volatility=volatility,
        steps=steps[1],
    )
    price = (first.price + second.price) * 0.5
    delta = (first.delta + second.delta) * 0.5
    if not math.isfinite(price) or not math.isfinite(delta):
        raise NormalizationError("TREE_NUMERIC_INVALID")
    return _SuiteEvaluation(
        volatility=volatility,
        price=price,
        delta=delta,
        price_residual=abs(price - target_price),
        early_exercise_nodes=(
            first.early_exercise_nodes + second.early_exercise_nodes
        ),
    )


def _zero_volatility_american_call_limit(
    *,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    effective_yield: float,
) -> float:
    """Return the deterministic-volatility limit used only for IV bracketing."""

    candidate_times = [0.0, years]
    if rate != effective_yield and rate * effective_yield > 0.0:
        ratio = (rate * strike) / (effective_yield * spot)
        if ratio > 0.0 and math.isfinite(ratio):
            stationary_time = math.log(ratio) / (rate - effective_yield)
            if 0.0 < stationary_time < years:
                candidate_times.append(stationary_time)
    values = (
        spot * math.exp(-effective_yield * time)
        - strike * math.exp(-rate * time)
        for time in candidate_times
    )
    return max(0.0, *values)


def _solve_iv_suite(
    *,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    effective_yield: float,
    target_price: float,
    price_tolerance: float,
    steps: tuple[int, int],
) -> _SuiteEvaluation:
    low_limit = _zero_volatility_american_call_limit(
        spot=spot,
        strike=strike,
        years=years,
        rate=rate,
        effective_yield=effective_yield,
    )
    high = _suite_evaluation(
        spot=spot,
        strike=strike,
        years=years,
        rate=rate,
        effective_yield=effective_yield,
        volatility=_IV_UPPER,
        target_price=target_price,
        steps=steps,
    )
    if (
        target_price < low_limit - price_tolerance
        or target_price > high.price + price_tolerance
    ):
        raise NormalizationError("IV_NO_BRACKET")
    if high.price - low_limit <= price_tolerance:
        raise NormalizationError("IV_UNIDENTIFIABLE")
    if abs(target_price - low_limit) <= price_tolerance:
        raise NormalizationError("IV_UNIDENTIFIABLE")

    lower_endpoint: _SuiteEvaluation | None
    try:
        lower_endpoint = _suite_evaluation(
            spot=spot,
            strike=strike,
            years=years,
            rate=rate,
            effective_yield=effective_yield,
            volatility=_IV_LOWER,
            target_price=target_price,
            steps=steps,
        )
    except NormalizationError as error:
        if error.reason_code != "TREE_PROBABILITY_INVALID":
            raise
        lower_endpoint = None
    if lower_endpoint is not None:
        if target_price < lower_endpoint.price - price_tolerance:
            raise NormalizationError("IV_NO_BRACKET")
        if abs(target_price - lower_endpoint.price) <= price_tolerance:
            raise NormalizationError("IV_UNIDENTIFIABLE")

    lower_volatility = _IV_LOWER
    upper_volatility = _IV_UPPER
    last_valid: _SuiteEvaluation | None = None
    lowest_valid = lower_endpoint
    for _ in range(_BISECTION_ITERATIONS):
        volatility = (lower_volatility + upper_volatility) * 0.5
        try:
            evaluation = _suite_evaluation(
                spot=spot,
                strike=strike,
                years=years,
                rate=rate,
                effective_yield=effective_yield,
                volatility=volatility,
                target_price=target_price,
                steps=steps,
            )
        except NormalizationError as error:
            if error.reason_code != "TREE_PROBABILITY_INVALID":
                raise
            # Low-volatility CRR probabilities can be undefined even when the
            # requested IV root is well inside the fixed bracket.  Advancing
            # only that invalid lower trial never fabricates a tree result.
            lower_volatility = volatility
            continue
        last_valid = evaluation
        if lowest_valid is None or evaluation.volatility < lowest_valid.volatility:
            lowest_valid = evaluation
        if evaluation.price < target_price:
            lower_volatility = volatility
        else:
            upper_volatility = volatility

    if last_valid is None:
        raise NormalizationError("TREE_PROBABILITY_INVALID")
    if lowest_valid is not None:
        if target_price < lowest_valid.price - price_tolerance:
            raise NormalizationError("IV_NO_BRACKET")
        if abs(target_price - lowest_valid.price) <= price_tolerance:
            raise NormalizationError("IV_UNIDENTIFIABLE")
    if last_valid.price_residual > price_tolerance:
        raise NormalizationError("IV_NOT_CONVERGED")
    return last_valid


def _new_fixed_decimal_context() -> Context:
    """Create the complete quantization policy without ambient inheritance."""

    return Context(
        prec=_DECIMAL_CONTEXT_PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emin=_DECIMAL_CONTEXT_EMIN,
        Emax=_DECIMAL_CONTEXT_EMAX,
        capitals=_DECIMAL_CONTEXT_CAPITALS,
        clamp=_DECIMAL_CONTEXT_CLAMP,
        flags=[],
        traps=list(_DECIMAL_CONTEXT_TRAPS),
    )


def _round_half_even_scaled(value: float, scale: int) -> int:
    if not math.isfinite(value):
        raise NormalizationError("TREE_NUMERIC_INVALID")
    try:
        with localcontext(_new_fixed_decimal_context()) as context:
            scaled = context.multiply(Decimal(str(value)), Decimal(scale))
            quantized = context.quantize(scaled, Decimal("1"))
    except DecimalException as error:
        raise NormalizationError("TREE_NUMERIC_INVALID") from error
    return int(quantized)


def _compute_american_call_delta_unsealed(
    *,
    spot_nano_usd: int,
    strike_nano_usd: int,
    option_mid_nano_usd: int,
    tick_nano_usd: int,
    valuation_utc_ns: int,
    expiry_utc_ns: int,
    risk_free_rate_ppm: int,
    expense_yield_ppm: int,
    borrow_yield_ppm: int,
) -> tuple[int, int, int, int, int, int, int, int, int, int]:
    """Compute only fixed-suite numeric facts, without provenance sealing."""

    spot = spot_nano_usd / _NANO_PER_USD
    strike = strike_nano_usd / _NANO_PER_USD
    target_price = option_mid_nano_usd / _NANO_PER_USD
    years = (expiry_utc_ns - valuation_utc_ns) / _YEAR_NS
    rate = risk_free_rate_ppm / _PPM
    effective_yield = (
        expense_yield_ppm + borrow_yield_ppm
    ) / _PPM
    price_tolerance = max(
        _PRICE_RESIDUAL_FLOOR_USD,
        (tick_nano_usd / _NANO_PER_USD) / 100.0,
    )
    if not all(
        math.isfinite(value)
        for value in (
            spot,
            strike,
            target_price,
            years,
            rate,
            effective_yield,
            price_tolerance,
        )
    ):
        raise NormalizationError("DELTA_INPUT_MISSING")

    coarse = _solve_iv_suite(
        spot=spot,
        strike=strike,
        years=years,
        rate=rate,
        effective_yield=effective_yield,
        target_price=target_price,
        price_tolerance=price_tolerance,
        steps=_COARSE_STEPS,
    )
    fine = _solve_iv_suite(
        spot=spot,
        strike=strike,
        years=years,
        rate=rate,
        effective_yield=effective_yield,
        target_price=target_price,
        price_tolerance=price_tolerance,
        steps=_FINE_STEPS,
    )
    if (
        abs(fine.volatility - coarse.volatility) > _IV_SUITE_TOLERANCE
        or abs(fine.delta - coarse.delta) > _DELTA_SUITE_TOLERANCE
    ):
        raise NormalizationError("TREE_NOT_CONVERGED")
    if not 0.0 <= coarse.delta <= 1.0 or not 0.0 <= fine.delta <= 1.0:
        raise NormalizationError("DELTA_OUT_OF_RANGE")

    coarse_iv_ppm = _round_half_even_scaled(coarse.volatility, _PPM)
    fine_iv_ppm = _round_half_even_scaled(fine.volatility, _PPM)
    coarse_delta_ppm = _round_half_even_scaled(coarse.delta, _PPM)
    fine_delta_ppm = _round_half_even_scaled(fine.delta, _PPM)
    return (
        fine_iv_ppm,
        fine_delta_ppm,
        coarse_iv_ppm,
        fine_iv_ppm,
        coarse_delta_ppm,
        fine_delta_ppm,
        _round_half_even_scaled(
            coarse.price_residual,
            _NANO_PER_USD,
        ),
        _round_half_even_scaled(
            fine.price_residual,
            _NANO_PER_USD,
        ),
        coarse.early_exercise_nodes,
        fine.early_exercise_nodes,
    )


def _create_public_crr_engine():
    """Keep the result registry and its only writer inside engine closures."""

    ResultPayload = tuple[object, ...]
    InputSnapshot = tuple[object, ...]
    RegistryRecord = tuple[
        weakref.ReferenceType[CrrDeltaResultV1],
        ResultPayload,
        CrrCallInputsV1,
        InputSnapshot,
    ]
    verified_by_identity: dict[int, RegistryRecord] = {}
    pit_type = CrrPitInputsV1
    call_type = CrrCallInputsV1
    run_type = CrrRunManifestV1
    result_type = CrrDeltaResultV1
    pit_fields = fields(pit_type)
    call_fields = fields(call_type)
    run_fields = fields(run_type)
    result_fields = fields(result_type)
    unsealed_compute = _compute_american_call_delta_unsealed
    make_reference = weakref.ref
    module_namespace = globals()
    canonical_module_namespace = canonical_snapshot_sha256.__globals__
    current_module_object = sys.modules[__name__]
    canonical_module_object = sys.modules[canonical_snapshot_sha256.__module__]
    identity_bindings = (
        ("_require_exact_int", _require_exact_int),
        ("_require_binding_hash", _require_binding_hash),
        ("_require_contract_id", _require_contract_id),
        ("_crr_american_call_tree", _crr_american_call_tree),
        ("_suite_evaluation", _suite_evaluation),
        ("_zero_volatility_american_call_limit", _zero_volatility_american_call_limit),
        ("_solve_iv_suite", _solve_iv_suite),
        ("_new_fixed_decimal_context", _new_fixed_decimal_context),
        ("_round_half_even_scaled", _round_half_even_scaled),
        ("canonical_snapshot_sha256", canonical_snapshot_sha256),
        ("_SHA256_RE", _SHA256_RE),
        ("CrrPitInputsV1", CrrPitInputsV1),
        ("CrrCallInputsV1", CrrCallInputsV1),
        ("CrrDeltaResultV1", CrrDeltaResultV1),
        ("CrrRunManifestV1", CrrRunManifestV1),
        ("_TreeEvaluation", _TreeEvaluation),
        ("_SuiteEvaluation", _SuiteEvaluation),
        ("NormalizationError", NormalizationError),
        ("Context", Context),
        ("Decimal", Decimal),
        ("DecimalException", DecimalException),
        ("localcontext", localcontext),
    )
    function_code_bindings = tuple(
        (function, function.__code__)
        for _, function in identity_bindings
        if hasattr(function, "__code__")
    )
    unsealed_compute_code = unsealed_compute.__code__
    canonical_identity_bindings = tuple(
        (name, canonical_module_namespace[name])
        for name in (
            "_canonical_root_schema",
            "_canonical_value",
            "_CANONICAL_ROOT_SCHEMAS",
            "NormalizationError",
            "canonical_snapshot_sha256",
            "fields",
            "is_dataclass",
            "json",
            "sha256",
            "sys",
        )
    )
    canonical_function_code_bindings = tuple(
        (implementation, implementation.__code__)
        for _, implementation in canonical_identity_bindings
        if hasattr(implementation, "__code__")
    )
    canonical_value_bindings = tuple(
        (name, canonical_module_namespace[name])
        for name in (
            "_CANONICAL_HASH_FORMAT",
            "MAX_CANONICAL_DEPTH",
            "MAX_CANONICAL_ENCODED_BYTES",
            "MAX_CANONICAL_INTEGER_BITS",
            "MAX_CANONICAL_NODES",
            "MAX_CONTROL_TEXT_CHARS",
        )
    )
    canonical_json_module = canonical_module_namespace["json"]
    canonical_json_encoder_type = canonical_json_module.JSONEncoder
    canonical_json_encoder_surface = tuple(
        (name, getattr(canonical_json_encoder_type, name))
        for name in ("__init__", "encode", "iterencode")
    )
    canonical_json_encoder_code_bindings = tuple(
        (implementation, implementation.__code__)
        for _, implementation in canonical_json_encoder_surface
        if hasattr(implementation, "__code__")
    )
    canonical_json_encoder_namespace = (
        canonical_json_encoder_type.iterencode.__globals__
    )
    canonical_json_dependency_bindings = tuple(
        (name, canonical_json_encoder_namespace[name])
        for name in (
            "_make_iterencode",
            "c_make_encoder",
            "encode_basestring",
            "encode_basestring_ascii",
        )
    )
    canonical_json_dependency_code_bindings = tuple(
        (implementation, implementation.__code__)
        for _, implementation in canonical_json_dependency_bindings
        if hasattr(implementation, "__code__")
    )
    canonical_json_value_bindings = (
        ("INFINITY", canonical_json_encoder_namespace["INFINITY"]),
    )
    canonical_dataclasses_namespace = canonical_module_namespace[
        "fields"
    ].__globals__
    canonical_dataclasses_value_bindings = (
        ("_FIELDS", canonical_dataclasses_namespace["_FIELDS"]),
    )
    value_bindings = tuple(
        (name, module_namespace[name])
        for name in (
            "MODEL_ID",
            "MODEL_CONTRACT_SHA256",
            "MODEL_CONFIG_JSON",
            "MODEL_CONFIG_SHA256",
            "MODEL_SOURCE_ARTIFACT_SHA256",
            "MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256",
            "MODEL_SHA256",
            "RUNTIME_FINGERPRINT_SHA256",
            "_COARSE_STEPS",
            "_FINE_STEPS",
            "_IV_LOWER",
            "_IV_UPPER",
            "_BISECTION_ITERATIONS",
            "_PRICE_RESIDUAL_FLOOR_USD",
            "_IV_SUITE_TOLERANCE",
            "_DELTA_SUITE_TOLERANCE",
            "_NANO_PER_USD",
            "_PPM",
            "_YEAR_NS",
            "_MAX_MATURITY_NS",
            "_MAX_ABS_RATE_PPM",
            "_MAX_CONTROL_TEXT_CHARS",
            "_MAX_DEFINED_I64",
            "_MAX_DEFINED_U64",
            "_DECIMAL_CONTEXT_PRECISION",
            "_DECIMAL_CONTEXT_EMIN",
            "_DECIMAL_CONTEXT_EMAX",
            "_DECIMAL_CONTEXT_CAPITALS",
            "_DECIMAL_CONTEXT_CLAMP",
            "_DECIMAL_CONTEXT_TRAPS",
            "_DECIMAL_CONTEXT_TRAP_NAMES",
            "ROUND_HALF_EVEN",
        )
    )
    math_bindings = tuple(
        (name, getattr(math, name))
        for name in ("erf", "exp", "isfinite", "log", "sqrt")
    )
    captured_builtins = {
        "OverflowError": OverflowError,
        "ZeroDivisionError": ZeroDivisionError,
        "abs": abs,
        "all": all,
        "any": any,
        "float": float,
        "int": int,
        "list": list,
        "max": max,
        "min": min,
        "range": range,
        "str": str,
        "type": type,
    }
    captured_math = SimpleNamespace(
        exp=math.exp,
        isfinite=math.isfinite,
        log=math.log,
        nan=math.nan,
        sqrt=math.sqrt,
    )
    captured_tree_type = namedtuple(
        "_CapturedTreeEvaluation",
        ("price", "delta", "early_exercise_nodes"),
    )
    captured_suite_type = namedtuple(
        "_CapturedSuiteEvaluation",
        (
            "volatility",
            "price",
            "delta",
            "price_residual",
            "early_exercise_nodes",
        ),
    )
    isolated_namespace: dict[str, object] = {
        "__builtins__": captured_builtins,
        "Decimal": Decimal,
        "DecimalException": DecimalException,
        "Context": Context,
        "NormalizationError": NormalizationError,
        "ROUND_HALF_EVEN": ROUND_HALF_EVEN,
        "_BISECTION_ITERATIONS": _BISECTION_ITERATIONS,
        "_COARSE_STEPS": _COARSE_STEPS,
        "_DECIMAL_CONTEXT_CAPITALS": _DECIMAL_CONTEXT_CAPITALS,
        "_DECIMAL_CONTEXT_CLAMP": _DECIMAL_CONTEXT_CLAMP,
        "_DECIMAL_CONTEXT_EMAX": _DECIMAL_CONTEXT_EMAX,
        "_DECIMAL_CONTEXT_EMIN": _DECIMAL_CONTEXT_EMIN,
        "_DECIMAL_CONTEXT_PRECISION": _DECIMAL_CONTEXT_PRECISION,
        "_DECIMAL_CONTEXT_TRAPS": _DECIMAL_CONTEXT_TRAPS,
        "_DELTA_SUITE_TOLERANCE": _DELTA_SUITE_TOLERANCE,
        "_FINE_STEPS": _FINE_STEPS,
        "_IV_LOWER": _IV_LOWER,
        "_IV_SUITE_TOLERANCE": _IV_SUITE_TOLERANCE,
        "_IV_UPPER": _IV_UPPER,
        "_NANO_PER_USD": _NANO_PER_USD,
        "_PPM": _PPM,
        "_PRICE_RESIDUAL_FLOOR_USD": _PRICE_RESIDUAL_FLOOR_USD,
        "_SuiteEvaluation": captured_suite_type,
        "_TreeEvaluation": captured_tree_type,
        "_YEAR_NS": _YEAR_NS,
        "localcontext": localcontext,
        "math": captured_math,
    }
    for name, implementation in (
        ("_crr_american_call_tree", _crr_american_call_tree),
        ("_suite_evaluation", _suite_evaluation),
        (
            "_zero_volatility_american_call_limit",
            _zero_volatility_american_call_limit,
        ),
        ("_solve_iv_suite", _solve_iv_suite),
        ("_new_fixed_decimal_context", _new_fixed_decimal_context),
        ("_round_half_even_scaled", _round_half_even_scaled),
        (
            "_compute_american_call_delta_unsealed",
            _compute_american_call_delta_unsealed,
        ),
    ):
        captured_implementation = FunctionType(
            implementation.__code__,
            isolated_namespace,
            implementation.__name__,
            implementation.__defaults__,
            implementation.__closure__,
        )
        if implementation.__kwdefaults__ is not None:
            captured_implementation.__kwdefaults__ = dict(
                implementation.__kwdefaults__
            )
        isolated_namespace[name] = captured_implementation
    captured_unsealed_compute = isolated_namespace[
        "_compute_american_call_delta_unsealed"
    ]
    participating_types = (
        CrrPitInputsV1,
        CrrCallInputsV1,
        CrrRunManifestV1,
        CrrDeltaResultV1,
        _TreeEvaluation,
        _SuiteEvaluation,
    )
    missing_surface = object()
    class_surface_bindings: list[tuple[type[object], str, object]] = []
    class_surface_code_bindings: list[tuple[object, object]] = []
    for owner in participating_types:
        attribute_names = {
            "__new__",
            "__init__",
            "__post_init__",
            "__getattribute__",
            "__module__",
            "__qualname__",
            "__setattr__",
            "__dataclass_fields__",
            *(item.name for item in fields(owner)),
            *(
                name
                for name, descriptor in vars(owner).items()
                if isinstance(descriptor, property)
            ),
        }
        for name in sorted(attribute_names):
            descriptor = getattr(owner, name, missing_surface)
            class_surface_bindings.append((owner, name, descriptor))
            code_owners = (
                descriptor,
                getattr(descriptor, "fget", None),
                getattr(descriptor, "fset", None),
                getattr(descriptor, "fdel", None),
            )
            class_surface_code_bindings.extend(
                (code_owner, code_owner.__code__)
                for code_owner in code_owners
                if hasattr(code_owner, "__code__")
            )
    class_dataclass_field_bindings = tuple(
        (
            owner,
            getattr(owner, "__dataclass_fields__"),
            tuple(getattr(owner, "__dataclass_fields__").items()),
            tuple(
                (item, item.name, item._field_type)
                for item in fields(owner)
            ),
        )
        for owner in participating_types
    )
    callable_state_implementations = {
        unsealed_compute,
        *(implementation for implementation, _ in function_code_bindings),
        *(
            implementation
            for implementation, _ in canonical_function_code_bindings
        ),
        *(
            implementation
            for implementation, _ in canonical_json_encoder_code_bindings
        ),
        *(
            implementation
            for implementation, _ in canonical_json_dependency_code_bindings
        ),
        *(
            implementation
            for implementation, _ in class_surface_code_bindings
        ),
    }
    callable_state_bindings = tuple(
        (
            implementation,
            implementation.__defaults__,
            None
            if implementation.__kwdefaults__ is None
            else dict(implementation.__kwdefaults__),
        )
        for implementation in callable_state_implementations
    )
    pit_slot_descriptors = {
        item.name: getattr(pit_type, item.name) for item in pit_fields
    }
    call_slot_descriptors = {
        item.name: getattr(call_type, item.name) for item in call_fields
    }
    run_slot_descriptors = {
        item.name: getattr(run_type, item.name) for item in run_fields
    }
    result_slot_descriptors = {
        item.name: getattr(result_type, item.name) for item in result_fields
    }
    normalization_error = NormalizationError
    canonical_hash = canonical_snapshot_sha256
    require_binding_hash = _require_binding_hash
    require_contract_id = _require_contract_id
    require_exact_int = _require_exact_int
    model_id = MODEL_ID
    model_contract_sha256 = MODEL_CONTRACT_SHA256
    model_config_sha256 = MODEL_CONFIG_SHA256
    model_source_artifact_sha256 = MODEL_SOURCE_ARTIFACT_SHA256
    canonical_encoder_source_artifact_sha256 = (
        MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256
    )
    model_sha256 = MODEL_SHA256
    runtime_fingerprint_sha256 = RUNTIME_FINGERPRINT_SHA256
    max_maturity_ns = _MAX_MATURITY_NS
    max_abs_rate_ppm = _MAX_ABS_RATE_PPM
    max_defined_i64 = _MAX_DEFINED_I64
    max_defined_u64 = _MAX_DEFINED_U64
    ppm = _PPM
    allocate = object.__new__

    def require_captured_runtime() -> None:
        if unsealed_compute.__code__ is not unsealed_compute_code:
            raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")
        for name, expected in identity_bindings:
            if module_namespace.get(name) is not expected:
                raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")
        for function, expected_code in function_code_bindings:
            if function.__code__ is not expected_code:
                raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")
        for name, expected in value_bindings:
            current = module_namespace.get(name)
            if type(current) is not type(expected) or current != expected:
                raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")
        for name, expected in canonical_identity_bindings:
            if canonical_module_namespace.get(name) is not expected:
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for implementation, expected_code in canonical_function_code_bindings:
            if implementation.__code__ is not expected_code:
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for name, expected in canonical_value_bindings:
            current = canonical_module_namespace.get(name)
            if type(current) is not type(expected) or current != expected:
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        if canonical_json_module.JSONEncoder is not canonical_json_encoder_type:
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for name, expected in canonical_json_encoder_surface:
            if getattr(canonical_json_encoder_type, name, None) is not expected:
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for implementation, expected_code in canonical_json_encoder_code_bindings:
            if implementation.__code__ is not expected_code:
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for name, expected in canonical_json_dependency_bindings:
            if canonical_json_encoder_namespace.get(name) is not expected:
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for implementation, expected_code in canonical_json_dependency_code_bindings:
            if implementation.__code__ is not expected_code:
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for name, expected in canonical_json_value_bindings:
            current = canonical_json_encoder_namespace.get(name)
            if type(current) is not type(expected) or current != expected:
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for name, expected in canonical_dataclasses_value_bindings:
            current = canonical_dataclasses_namespace.get(name)
            if type(current) is not type(expected) or current != expected:
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        if (
            sys.modules.get(__name__) is not current_module_object
            or sys.modules.get(canonical_snapshot_sha256.__module__)
            is not canonical_module_object
        ):
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for implementation, expected_defaults, expected_kwdefaults in (
            callable_state_bindings
        ):
            if (
                implementation.__defaults__ is not expected_defaults
                or implementation.__kwdefaults__ != expected_kwdefaults
            ):
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for name, expected in math_bindings:
            if getattr(math, name, None) is not expected:
                raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")
        for owner, name, expected in class_surface_bindings:
            if getattr(owner, name, missing_surface) is not expected:
                raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")
        for code_owner, expected_code in class_surface_code_bindings:
            if code_owner.__code__ is not expected_code:
                raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")
        for owner, mapping, expected_items, field_states in (
            class_dataclass_field_bindings
        ):
            current_mapping = getattr(owner, "__dataclass_fields__", None)
            if (
                current_mapping is not mapping
                or tuple(current_mapping.items()) != expected_items
                or any(
                    item.name != expected_name
                    or item._field_type is not expected_field_type
                    for item, expected_name, expected_field_type in field_states
                )
            ):
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")

    def payload(result: CrrDeltaResultV1) -> ResultPayload:
        return tuple(
            result_slot_descriptors[item.name].__get__(result, result_type)
            for item in result_fields
        )

    def input_snapshot(inputs: CrrCallInputsV1) -> InputSnapshot:
        if type(inputs) is not call_type:
            raise normalization_error("DELTA_INPUT_MISSING")
        values = tuple(
            call_slot_descriptors[item.name].__get__(inputs, call_type)
            for item in call_fields
        )
        pit_inputs = call_slot_descriptors["pit_inputs"].__get__(
            inputs,
            call_type,
        )
        if type(pit_inputs) is not pit_type:
            raise normalization_error("DELTA_INPUT_MISSING")
        pit_snapshot = tuple(
            pit_slot_descriptors[item.name].__get__(pit_inputs, pit_type)
            for item in pit_fields
        )
        return values + (pit_snapshot,)

    def run_values(input_sha256: str) -> dict[str, str]:
        return {
            "model_contract_sha256": model_contract_sha256,
            "model_config_sha256": model_config_sha256,
            "model_source_artifact_sha256": model_source_artifact_sha256,
            "canonical_encoder_source_artifact_sha256": (
                canonical_encoder_source_artifact_sha256
            ),
            "model_sha256": model_sha256,
            "runtime_fingerprint_sha256": runtime_fingerprint_sha256,
            "input_sha256": input_sha256,
        }

    def run_sha256(input_sha256: str) -> str:
        manifest = allocate(run_type)
        for name, value in run_values(input_sha256).items():
            run_slot_descriptors[name].__set__(manifest, value)
        return canonical_hash(manifest)

    def compute(
        inputs: CrrCallInputsV1,
        *,
        expected_model_sha256: str,
    ) -> CrrDeltaResultV1:
        """Solve CRR and register immutable in-process engine provenance."""

        require_captured_runtime()
        if (
            type(expected_model_sha256) is not str
            or expected_model_sha256 != model_sha256
        ):
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        if type(inputs) is not call_type:
            raise normalization_error("DELTA_INPUT_MISSING")

        call_values = {
            item.name: call_slot_descriptors[item.name].__get__(
                inputs,
                call_type,
            )
            for item in call_fields
        }
        pit_inputs = call_values["pit_inputs"]
        if type(pit_inputs) is not pit_type:
            raise normalization_error("DELTA_INPUT_MISSING")
        pit_values = {
            item.name: pit_slot_descriptors[item.name].__get__(
                pit_inputs,
                pit_type,
            )
            for item in pit_fields
        }

        for name in (
            "option_snapshot_sha256",
            "contract_sha256",
            "underlying_top_sha256",
            "option_quote_sha256",
        ):
            require_binding_hash(call_values[name])
        require_contract_id(call_values["contract_id"])
        for name in (
            "spot_nano_usd",
            "strike_nano_usd",
            "option_mid_nano_usd",
            "tick_nano_usd",
        ):
            require_exact_int(
                call_values[name],
                minimum=1,
                maximum=max_defined_i64,
            )
        valuation_utc_ns = require_exact_int(
            call_values["valuation_utc_ns"],
            minimum=1,
            maximum=max_defined_u64,
        )
        expiry_utc_ns = require_exact_int(
            call_values["expiry_utc_ns"],
            minimum=1,
            maximum=max_defined_u64,
        )
        if (
            expiry_utc_ns <= valuation_utc_ns
            or expiry_utc_ns - valuation_utc_ns > max_maturity_ns
        ):
            raise normalization_error("T_OUT_OF_DOMAIN")

        pit_as_of_utc_ns = require_exact_int(
            pit_values["as_of_utc_ns"],
            minimum=1,
            maximum=max_defined_u64,
        )
        for name in (
            "risk_free_rate_ppm",
            "expense_yield_ppm",
            "borrow_yield_ppm",
        ):
            require_exact_int(
                pit_values[name],
                minimum=-max_abs_rate_ppm,
                maximum=max_abs_rate_ppm,
            )
        for name in (
            "rate_curve_sha256",
            "distribution_assumption_sha256",
            "borrow_assumption_sha256",
        ):
            require_binding_hash(pit_values[name])
        if pit_as_of_utc_ns != valuation_utc_ns:
            raise normalization_error("SNAPSHOT_NOT_CAUSAL")

        input_sha256 = canonical_hash(inputs)
        require_binding_hash(input_sha256)
        bound_input_snapshot = input_snapshot(inputs)
        require_captured_runtime()
        numeric_payload = captured_unsealed_compute(
            spot_nano_usd=call_values["spot_nano_usd"],
            strike_nano_usd=call_values["strike_nano_usd"],
            option_mid_nano_usd=call_values["option_mid_nano_usd"],
            tick_nano_usd=call_values["tick_nano_usd"],
            valuation_utc_ns=valuation_utc_ns,
            expiry_utc_ns=expiry_utc_ns,
            risk_free_rate_ppm=pit_values["risk_free_rate_ppm"],
            expense_yield_ppm=pit_values["expense_yield_ppm"],
            borrow_yield_ppm=pit_values["borrow_yield_ppm"],
        )
        require_captured_runtime()
        (
            iv_ppm,
            delta_ppm,
            coarse_iv_ppm,
            fine_iv_ppm,
            coarse_delta_ppm,
            fine_delta_ppm,
            coarse_price_residual_nano_usd,
            fine_price_residual_nano_usd,
            coarse_early_exercise_nodes,
            fine_early_exercise_nodes,
        ) = numeric_payload
        if (
            input_snapshot(inputs) != bound_input_snapshot
            or canonical_hash(inputs) != input_sha256
        ):
            raise normalization_error("DELTA_INPUT_BINDING_MISMATCH")
        for value in (iv_ppm, coarse_iv_ppm, fine_iv_ppm):
            require_exact_int(value, minimum=100, maximum=5_000_000)
        for value in (delta_ppm, coarse_delta_ppm, fine_delta_ppm):
            require_exact_int(value, minimum=0, maximum=ppm)
        for value in (
            coarse_price_residual_nano_usd,
            fine_price_residual_nano_usd,
            coarse_early_exercise_nodes,
            fine_early_exercise_nodes,
        ):
            require_exact_int(value, minimum=0, maximum=max_defined_i64)
        if iv_ppm != fine_iv_ppm or delta_ppm != fine_delta_ppm:
            raise normalization_error("TREE_NOT_CONVERGED")
        if (
            abs(coarse_iv_ppm - fine_iv_ppm) > 100
            or abs(coarse_delta_ppm - fine_delta_ppm) > 500
        ):
            raise normalization_error("TREE_NOT_CONVERGED")

        receipt_values = run_values(input_sha256)
        receipt_run_sha256 = run_sha256(input_sha256)
        require_binding_hash(receipt_run_sha256)

        result_values = {
            "model_id": model_id,
            "model_sha256": model_sha256,
            "input_sha256": input_sha256,
            "option_snapshot_sha256": call_values[
                "option_snapshot_sha256"
            ],
            "contract_id": call_values["contract_id"],
            "iv_ppm": iv_ppm,
            "delta_ppm": delta_ppm,
            "coarse_iv_ppm": coarse_iv_ppm,
            "fine_iv_ppm": fine_iv_ppm,
            "coarse_delta_ppm": coarse_delta_ppm,
            "fine_delta_ppm": fine_delta_ppm,
            "coarse_price_residual_nano_usd": (
                coarse_price_residual_nano_usd
            ),
            "fine_price_residual_nano_usd": fine_price_residual_nano_usd,
            "coarse_early_exercise_nodes": coarse_early_exercise_nodes,
            "fine_early_exercise_nodes": fine_early_exercise_nodes,
            **receipt_values,
            "run_sha256": receipt_run_sha256,
        }
        result = allocate(result_type)
        for name, value in result_values.items():
            result_slot_descriptors[name].__set__(result, value)
        if type(result) is not result_type:
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        identity = id(result)

        def discard(reference: weakref.ReferenceType[CrrDeltaResultV1]) -> None:
            record = verified_by_identity.get(identity)
            if record is not None and record[0] is reference:
                verified_by_identity.pop(identity, None)

        result_payload = payload(result)
        if result_payload != tuple(
            result_values[item.name] for item in result_fields
        ):
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        require_captured_runtime()
        reference = make_reference(result, discard)
        verified_by_identity[identity] = (
            reference,
            result_payload,
            inputs,
            bound_input_snapshot,
        )
        return result

    def is_verified(value: object) -> bool:
        """Reject constructed, copied, deserialized, or mutated result objects."""

        try:
            require_captured_runtime()
        except NormalizationError:
            return False
        if type(value) is not result_type:
            return False
        record = verified_by_identity.get(id(value))
        if (
            record is None
            or record[0]() is not value
            or record[1] != payload(value)
        ):
            return False
        recorded_inputs = record[2]
        try:
            current_input_sha256 = canonical_hash(recorded_inputs)
            return (
                input_snapshot(recorded_inputs) == record[3]
                and result_slot_descriptors["input_sha256"].__get__(
                    value,
                    result_type,
                )
                == current_input_sha256
                and result_slot_descriptors["run_sha256"].__get__(
                    value,
                    result_type,
                )
                == run_sha256(current_input_sha256)
            )
        except Exception:
            return False

    return compute, is_verified


compute_american_call_delta, is_verified_crr_delta_result = (
    _create_public_crr_engine()
)
del _create_public_crr_engine


def _bsm_european_call_oracle(
    *,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    effective_yield: float,
    volatility: float,
) -> tuple[float, float]:
    """Private European analytic oracle for synthetic tests only."""

    sigma_root_time = volatility * math.sqrt(years)
    d1 = (
        math.log(spot / strike)
        + (rate - effective_yield + 0.5 * volatility * volatility) * years
    ) / sigma_root_time
    d2 = d1 - sigma_root_time
    normal_d1 = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
    normal_d2 = 0.5 * (1.0 + math.erf(d2 / math.sqrt(2.0)))
    price = (
        spot * math.exp(-effective_yield * years) * normal_d1
        - strike * math.exp(-rate * years) * normal_d2
    )
    delta = math.exp(-effective_yield * years) * normal_d1
    return price, delta


__all__ = [
    "FULL_CHAIN_EXECUTION_STATUS",
    "MODEL_ARTIFACT_MANIFEST",
    "MODEL_CONFIG_JSON",
    "MODEL_CONFIG_SHA256",
    "MODEL_CONTRACT_SHA256",
    "MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256",
    "MODEL_ID",
    "MODEL_SHA256",
    "MODEL_SOURCE_ARTIFACT_SHA256",
    "RUNTIME_FINGERPRINT",
    "RUNTIME_FINGERPRINT_SHA256",
    "CrrCallInputsV1",
    "CrrDeltaResultV1",
    "CrrModelArtifactManifestV1",
    "CrrPitInputsV1",
    "CrrRunManifestV1",
    "CrrRuntimeFingerprintV1",
    "bind_crr_call_inputs_from_snapshot",
    "compute_american_call_delta",
    "is_verified_crr_delta_result",
]
