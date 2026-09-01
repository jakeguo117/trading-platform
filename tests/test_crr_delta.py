from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timezone
from decimal import ROUND_UP, localcontext
from hashlib import sha256
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from gld_normalizer.errors import NormalizationError
import gld_research_core.crr_delta as crr_module
import gld_research_core.facts as facts_module
from gld_research_core.crr_delta import (
    FULL_CHAIN_EXECUTION_STATUS,
    MODEL_ARTIFACT_MANIFEST,
    MODEL_CONFIG_JSON,
    MODEL_CONFIG_SHA256,
    MODEL_CONTRACT_SHA256,
    MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256,
    MODEL_ID,
    MODEL_SHA256,
    MODEL_SOURCE_ARTIFACT_SHA256,
    RUNTIME_FINGERPRINT,
    RUNTIME_FINGERPRINT_SHA256,
    CrrCallInputsV1,
    CrrDeltaResultV1,
    CrrPitInputsV1,
    bind_crr_call_inputs_from_snapshot,
    compute_american_call_delta,
    is_verified_crr_delta_result,
)
from gld_research_core.facts import (
    OptionContractV1,
    OptionQuoteSnapshotV1,
    OptionQuoteV1,
    TopOfBookV1,
    canonical_snapshot_sha256,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
NANO = 1_000_000_000
YEAR_NS = 365 * 86_400 * NANO
VALUATION_NS = 1_800_000_000_000_000_000
DAY_NS = 86_400 * NANO


def _utc_nanoseconds(value: datetime) -> int:
    return int(value.timestamp()) * NANO


BINDING_WINDOW_START_NS = _utc_nanoseconds(
    datetime(2026, 8, 28, 14, 45, tzinfo=timezone.utc)
)
BINDING_CAPTURE_NS = BINDING_WINDOW_START_NS + 10 * NANO
BINDING_EXPIRY_NS = _utc_nanoseconds(
    datetime(2027, 2, 19, 21, 0, tzinfo=timezone.utc)
)


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _bsm_call_price_delta(
    *,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    volatility: float,
) -> tuple[float, float]:
    sigma_root_t = volatility * math.sqrt(years)
    d1 = (
        math.log(spot / strike)
        + (rate + 0.5 * volatility * volatility) * years
    ) / sigma_root_t
    d2 = d1 - sigma_root_t
    price = spot * _normal_cdf(d1) - strike * math.exp(-rate * years) * _normal_cdf(d2)
    return price, _normal_cdf(d1)


def call_inputs(**changes: object) -> CrrCallInputsV1:
    expected_price, _ = _bsm_call_price_delta(
        spot=100.0,
        strike=100.0,
        years=0.5,
        rate=0.02,
        volatility=0.30,
    )
    valuation = changes.get("valuation_utc_ns", VALUATION_NS)
    pit_values: dict[str, object] = {
        "as_of_utc_ns": valuation,
        "risk_free_rate_ppm": changes.pop("risk_free_rate_ppm", 20_000),
        "expense_yield_ppm": changes.pop("expense_yield_ppm", 0),
        "borrow_yield_ppm": changes.pop("borrow_yield_ppm", 0),
        "rate_curve_sha256": changes.pop("rate_curve_sha256", SHA_B),
        "distribution_assumption_sha256": changes.pop(
            "distribution_assumption_sha256",
            SHA_C,
        ),
        "borrow_assumption_sha256": changes.pop(
            "borrow_assumption_sha256",
            SHA_D,
        ),
    }
    pit_inputs = changes.pop("pit_inputs", None)
    if pit_inputs is None:
        pit_inputs = CrrPitInputsV1(**pit_values)  # type: ignore[arg-type]
    values: dict[str, object] = {
        "option_snapshot_sha256": SHA_A,
        "contract_id": "GLD   270219C00100000",
        "contract_sha256": SHA_B,
        "underlying_top_sha256": SHA_C,
        "option_quote_sha256": SHA_D,
        "pit_inputs": pit_inputs,
        "spot_nano_usd": 100 * NANO,
        "strike_nano_usd": 100 * NANO,
        "option_mid_nano_usd": round(expected_price * NANO),
        "tick_nano_usd": 10_000_000,
        "valuation_utc_ns": valuation,
        "expiry_utc_ns": VALUATION_NS + YEAR_NS // 2,
    }
    values.update(changes)
    return CrrCallInputsV1(**values)  # type: ignore[arg-type]


def _binding_book(
    *,
    bid_nano_usd: int,
    ask_nano_usd: int,
    ts_recv_ns: int = BINDING_CAPTURE_NS - 100_000_000,
) -> TopOfBookV1:
    return TopOfBookV1(
        bid_nano_usd=bid_nano_usd,
        ask_nano_usd=ask_nano_usd,
        bid_size=10,
        ask_size=12,
        tick_nano_usd=10_000_000,
        ts_event_ns=ts_recv_ns - 10_000_000,
        ts_recv_ns=ts_recv_ns,
        flags=(),
    )


def _binding_contract(**changes: object) -> OptionContractV1:
    values: dict[str, object] = {
        "occ_symbol": "GLD   270219C00100000",
        "underlying": "GLD",
        "right": "C",
        "exercise_style": "AMERICAN",
        "strike_nano_usd": 100 * NANO,
        "expiry_utc_ns": BINDING_EXPIRY_NS,
        "last_trading_utc_ns": BINDING_EXPIRY_NS - NANO,
        "activation_utc_ns": BINDING_WINDOW_START_NS - NANO,
        "multiplier": 100,
        "deliverable": "100 GLD",
        "currency": "USD",
        "standard_unadjusted": True,
    }
    values.update(changes)
    return OptionContractV1(**values)  # type: ignore[arg-type]


def _binding_snapshot(**changes: object) -> OptionQuoteSnapshotV1:
    contract = _binding_contract()
    option_quote = OptionQuoteV1(
        contract=contract,
        top_of_book=_binding_book(
            bid_nano_usd=8 * NANO,
            ask_nano_usd=10 * NANO,
        ),
    )
    values: dict[str, object] = {
        "signal_snapshot_sha256": SHA_A,
        "candidate_ordinal": 1,
        "candidate_provenance_sha256": SHA_D,
        "window_start_utc_ns": BINDING_WINDOW_START_NS,
        "window_end_utc_ns": BINDING_WINDOW_START_NS + 60 * NANO,
        "capture_utc_ns": BINDING_CAPTURE_NS,
        "underlying_top": _binding_book(
            bid_nano_usd=99 * NANO,
            ask_nano_usd=101 * NANO,
        ),
        "option_quotes": (option_quote,),
        "source_id": "SYNTHETIC_FIXTURE_ONLY",
        "source_version": "fixture-v1",
        "market_data_type": "SYNTHETIC_NOT_ENTITLED",
        "source_receipt_sha256": SHA_B,
    }
    values.update(changes)
    return OptionQuoteSnapshotV1(**values)  # type: ignore[arg-type]


def _pit_inputs(**changes: object) -> CrrPitInputsV1:
    values: dict[str, object] = {
        "as_of_utc_ns": BINDING_CAPTURE_NS,
        "risk_free_rate_ppm": 20_000,
        "expense_yield_ppm": 4_000,
        "borrow_yield_ppm": 1_000,
        "rate_curve_sha256": SHA_B,
        "distribution_assumption_sha256": SHA_C,
        "borrow_assumption_sha256": SHA_D,
    }
    values.update(changes)
    return CrrPitInputsV1(**values)  # type: ignore[arg-type]


class CrrDeltaEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = call_inputs()
        cls.result = compute_american_call_delta(
            cls.inputs,
            expected_model_sha256=MODEL_SHA256,
        )

    def test_model_config_is_contract_bound_and_hash_stable(self) -> None:
        self.assertEqual(MODEL_ID, "GLD_CALL_DELTA_CRR_AM_V1")
        self.assertEqual(
            MODEL_CONTRACT_SHA256,
            "06a4e216aa0a0f0c2e7192027ac5a19d288f799d32da59bf632bb26e7678caa8",
        )
        self.assertIn(MODEL_CONTRACT_SHA256, MODEL_CONFIG_JSON)
        self.assertEqual(
            json.loads(MODEL_CONFIG_JSON)["decimal_context"],
            {
                "capitals": 1,
                "clamp": 0,
                "emax": 99,
                "emin": -99,
                "precision": 50,
                "rounding": "ROUND_HALF_EVEN",
                "traps": ["DivisionByZero", "InvalidOperation", "Overflow"],
            },
        )
        self.assertEqual(
            MODEL_CONFIG_SHA256,
            sha256(MODEL_CONFIG_JSON.encode("ascii")).hexdigest(),
        )
        self.assertEqual(
            MODEL_SOURCE_ARTIFACT_SHA256,
            sha256(Path(crr_module.__file__).read_bytes()).hexdigest(),
        )
        self.assertEqual(
            MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256,
            sha256(Path(facts_module.__file__).read_bytes()).hexdigest(),
        )
        self.assertEqual(
            MODEL_ARTIFACT_MANIFEST.contract_sha256,
            MODEL_CONTRACT_SHA256,
        )
        self.assertEqual(
            MODEL_ARTIFACT_MANIFEST.config_sha256,
            MODEL_CONFIG_SHA256,
        )
        self.assertEqual(
            MODEL_ARTIFACT_MANIFEST.source_artifact_sha256,
            MODEL_SOURCE_ARTIFACT_SHA256,
        )
        self.assertEqual(
            MODEL_ARTIFACT_MANIFEST.canonical_encoder_source_artifact_sha256,
            MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256,
        )
        self.assertEqual(MODEL_SHA256, MODEL_ARTIFACT_MANIFEST.artifact_sha256)
        self.assertNotEqual(MODEL_SHA256, MODEL_CONFIG_SHA256)
        with self.assertRaises(FrozenInstanceError):
            MODEL_ARTIFACT_MANIFEST.contract_sha256 = SHA_A  # type: ignore[misc]

    def test_q_zero_american_tree_recovers_bsm_iv_and_delta(self) -> None:
        _, expected_delta = _bsm_call_price_delta(
            spot=100.0,
            strike=100.0,
            years=0.5,
            rate=0.02,
            volatility=0.30,
        )

        self.assertEqual(self.result.iv_ppm, self.result.fine_iv_ppm)
        self.assertEqual(self.result.delta_ppm, self.result.fine_delta_ppm)
        self.assertLessEqual(abs(self.result.iv_ppm - 300_000), 500)
        self.assertLessEqual(
            abs(self.result.delta_ppm - round(expected_delta * 1_000_000)),
            750,
        )
        self.assertLessEqual(
            abs(self.result.coarse_iv_ppm - self.result.fine_iv_ppm),
            100,
        )
        self.assertLessEqual(
            abs(self.result.coarse_delta_ppm - self.result.fine_delta_ppm),
            500,
        )

    def test_identical_input_has_identical_binding_and_result(self) -> None:
        with (
            patch.object(
                crr_module,
                "_compute_american_call_delta_unsealed",
                side_effect=AssertionError("public engine resolved mutable global"),
            ),
            patch.object(crr_module, "fields", return_value=()),
        ):
            repeated = compute_american_call_delta(
                call_inputs(),
                expected_model_sha256=MODEL_SHA256,
            )

        self.assertEqual(repeated, self.result)
        self.assertEqual(repeated.input_sha256, self.inputs.input_sha256)
        self.assertEqual(
            repeated.option_snapshot_sha256,
            self.inputs.option_snapshot_sha256,
        )
        self.assertEqual(repeated.contract_id, self.inputs.contract_id)
        self.assertEqual(repeated.model_contract_sha256, MODEL_CONTRACT_SHA256)
        self.assertEqual(repeated.model_config_sha256, MODEL_CONFIG_SHA256)
        self.assertEqual(
            repeated.model_source_artifact_sha256,
            MODEL_SOURCE_ARTIFACT_SHA256,
        )
        self.assertEqual(
            repeated.runtime_fingerprint_sha256,
            RUNTIME_FINGERPRINT_SHA256,
        )
        self.assertEqual(
            repeated.run_sha256,
            crr_module.CrrRunManifestV1(
                model_contract_sha256=MODEL_CONTRACT_SHA256,
                model_config_sha256=MODEL_CONFIG_SHA256,
                model_source_artifact_sha256=MODEL_SOURCE_ARTIFACT_SHA256,
                canonical_encoder_source_artifact_sha256=(
                    MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256
                ),
                model_sha256=MODEL_SHA256,
                runtime_fingerprint_sha256=RUNTIME_FINGERPRINT_SHA256,
                input_sha256=self.inputs.input_sha256,
            ).run_sha256,
        )
        self.assertIsInstance(repeated, CrrDeltaResultV1)
        self.assertTrue(is_verified_crr_delta_result(repeated))
        reconstructed = replace(repeated)
        self.assertEqual(reconstructed, repeated)
        self.assertFalse(is_verified_crr_delta_result(reconstructed))
        for field in fields(repeated):
            self.assertIn(type(getattr(repeated, field.name)), {int, str})
        with self.assertRaises(FrozenInstanceError):
            repeated.delta_ppm = 0  # type: ignore[misc]
        object.__setattr__(repeated, "delta_ppm", 0)
        self.assertFalse(is_verified_crr_delta_result(repeated))

    def test_model_hash_mismatch_fails_closed_before_solving(self) -> None:
        with self.assertRaises(NormalizationError) as raised:
            compute_american_call_delta(
                self.inputs,
                expected_model_sha256="f" * 64,
            )
        self.assertEqual(raised.exception.reason_code, "DELTA_MODEL_HASH_MISMATCH")

    def test_solver_helper_or_frozen_constant_runtime_mutation_fails_closed(
        self,
    ) -> None:
        inputs = call_inputs()

        def poisoned(*args: object, **kwargs: object) -> object:
            raise AssertionError("mutated solver helper executed")

        mutations = (
            ("_solve_iv_suite", poisoned),
            ("_suite_evaluation", poisoned),
            ("_crr_american_call_tree", poisoned),
            ("_COARSE_STEPS", (2, 3)),
            ("_FINE_STEPS", (4, 5)),
            ("_BISECTION_ITERATIONS", 1),
            ("_IV_LOWER", 0.25),
            ("_IV_UPPER", 0.50),
            ("_DELTA_SUITE_TOLERANCE", 1.0),
            ("_DECIMAL_CONTEXT_PRECISION", 2),
            ("_DECIMAL_CONTEXT_EMAX", 2),
            ("_DECIMAL_CONTEXT_TRAPS", ()),
            ("ROUND_HALF_EVEN", ROUND_UP),
            ("_new_fixed_decimal_context", poisoned),
        )
        for name, replacement in mutations:
            with self.subTest(name=name), patch.object(
                crr_module,
                name,
                replacement,
            ):
                with self.assertRaises(NormalizationError) as raised:
                    compute_american_call_delta(
                        inputs,
                        expected_model_sha256=MODEL_SHA256,
                    )
                self.assertEqual(
                    raised.exception.reason_code,
                    "DELTA_MODEL_HASH_MISMATCH",
                )

        with patch.object(crr_module.math, "exp", side_effect=AssertionError):
            with self.assertRaises(NormalizationError) as raised:
                compute_american_call_delta(
                    inputs,
                    expected_model_sha256=MODEL_SHA256,
                )
            self.assertEqual(
                raised.exception.reason_code,
                "DELTA_MODEL_HASH_MISMATCH",
            )

        for function in (
            crr_module._compute_american_call_delta_unsealed,
            crr_module._solve_iv_suite,
        ):
            with self.subTest(code_object=function.__name__):
                original_code = function.__code__
                try:
                    function.__code__ = poisoned.__code__
                    with self.assertRaises(NormalizationError) as raised:
                        compute_american_call_delta(
                            inputs,
                            expected_model_sha256=MODEL_SHA256,
                        )
                    self.assertEqual(
                        raised.exception.reason_code,
                        "DELTA_MODEL_HASH_MISMATCH",
                    )
                finally:
                    function.__code__ = original_code

    def test_participating_class_surface_mutation_fails_closed(self) -> None:
        inputs = call_inputs()

        original_result_post_init = crr_module.CrrDeltaResultV1.__post_init__

        def poisoned_result_post_init(result: CrrDeltaResultV1) -> None:
            original_result_post_init(result)
            object.__setattr__(result, "delta_ppm", 250_000)
            object.__setattr__(result, "coarse_delta_ppm", 250_000)
            object.__setattr__(result, "fine_delta_ppm", 250_000)

        original_pit_rate = crr_module.CrrPitInputsV1.risk_free_rate_ppm
        original_input_hash = crr_module.CrrCallInputsV1.input_sha256
        original_run_hash = crr_module.CrrRunManifestV1.run_sha256
        original_tree_delta = crr_module._TreeEvaluation.delta

        class PoisonedTreeDelta:
            def __get__(self, instance: object, owner: type[object]) -> object:
                if instance is None:
                    return self
                return 0.25

            def __set__(self, instance: object, value: object) -> None:
                original_tree_delta.__set__(instance, value)

        mutations = (
            (
                crr_module.CrrDeltaResultV1,
                "__post_init__",
                poisoned_result_post_init,
            ),
            (
                crr_module.CrrPitInputsV1,
                "risk_free_rate_ppm",
                property(lambda _: 30_000),
            ),
            (
                crr_module.CrrCallInputsV1,
                "input_sha256",
                property(lambda _: "e" * 64),
            ),
            (
                crr_module.CrrRunManifestV1,
                "run_sha256",
                property(lambda _: "e" * 64),
            ),
            (
                crr_module._TreeEvaluation,
                "delta",
                PoisonedTreeDelta(),
            ),
        )
        originals = (
            original_result_post_init,
            original_pit_rate,
            original_input_hash,
            original_run_hash,
            original_tree_delta,
        )
        for (owner, name, replacement), original in zip(
            mutations,
            originals,
            strict=True,
        ):
            with self.subTest(owner=owner.__name__, attribute=name):
                try:
                    setattr(owner, name, replacement)
                    with self.assertRaises(NormalizationError) as raised:
                        compute_american_call_delta(
                            inputs,
                            expected_model_sha256=MODEL_SHA256,
                        )
                    self.assertEqual(
                        raised.exception.reason_code,
                        "DELTA_MODEL_HASH_MISMATCH",
                    )
                finally:
                    setattr(owner, name, original)

        def poisoned_post_init(result: CrrDeltaResultV1) -> None:
            raise AssertionError("mutated class implementation executed")

        def poisoned_input_hash(inputs: CrrCallInputsV1) -> str:
            return "e" * 64

        callables = (
            (
                crr_module.CrrDeltaResultV1.__post_init__,
                poisoned_post_init.__code__,
            ),
            (
                crr_module.CrrCallInputsV1.input_sha256.fget,
                poisoned_input_hash.__code__,
            ),
        )
        for implementation, replacement_code in callables:
            with self.subTest(code_object=implementation.__name__):
                original_code = implementation.__code__
                try:
                    implementation.__code__ = replacement_code
                    with self.assertRaises(NormalizationError) as raised:
                        compute_american_call_delta(
                            inputs,
                            expected_model_sha256=MODEL_SHA256,
                        )
                    self.assertEqual(
                        raised.exception.reason_code,
                        "DELTA_MODEL_HASH_MISMATCH",
                    )
                finally:
                    implementation.__code__ = original_code

    def test_canonical_encoder_dependency_mutation_fails_closed(self) -> None:
        inputs = call_inputs()

        def forged_canonical_value(
            value: object,
            **kwargs: object,
        ) -> object:
            return {"forged": True}

        def forged_root_schema(value: object) -> tuple[str, str]:
            return ("forged.domain", "v1")

        mutations = (
            ("_canonical_value", forged_canonical_value),
            ("_canonical_root_schema", forged_root_schema),
            ("_CANONICAL_ROOT_SCHEMAS", {}),
            ("MAX_CANONICAL_DEPTH", 1),
        )
        for name, replacement in mutations:
            with self.subTest(name=name), patch.object(
                facts_module,
                name,
                replacement,
            ):
                self.assertFalse(is_verified_crr_delta_result(self.result))
                with self.assertRaises(NormalizationError) as raised:
                    compute_american_call_delta(
                        inputs,
                        expected_model_sha256=MODEL_SHA256,
                    )
                self.assertEqual(
                    raised.exception.reason_code,
                    "DELTA_MODEL_HASH_MISMATCH",
                )
        self.assertTrue(is_verified_crr_delta_result(self.result))

        def forged_string_encoder(value: str) -> str:
            return '"FORGED"'

        with patch.object(
            facts_module.json.encoder,
            "encode_basestring_ascii",
            forged_string_encoder,
        ):
            self.assertFalse(is_verified_crr_delta_result(self.result))
            with self.assertRaises(NormalizationError) as raised:
                compute_american_call_delta(
                    inputs,
                    expected_model_sha256=MODEL_SHA256,
                )
            self.assertEqual(
                raised.exception.reason_code,
                "DELTA_MODEL_HASH_MISMATCH",
            )
        self.assertTrue(is_verified_crr_delta_result(self.result))

    def test_verifier_revalidates_input_and_run_receipt_lineage(self) -> None:
        inputs = call_inputs()
        result = compute_american_call_delta(
            inputs,
            expected_model_sha256=MODEL_SHA256,
        )
        self.assertTrue(is_verified_crr_delta_result(result))

        object.__setattr__(inputs.pit_inputs, "risk_free_rate_ppm", 30_000)
        self.assertFalse(is_verified_crr_delta_result(result))

    def test_runtime_fingerprint_and_half_even_are_deterministic(self) -> None:
        self.assertEqual(
            RUNTIME_FINGERPRINT_SHA256,
            canonical_snapshot_sha256(RUNTIME_FINGERPRINT),
        )
        self.assertEqual(
            crr_module.CrrRuntimeFingerprintV1.current(),
            RUNTIME_FINGERPRINT,
        )
        self.assertEqual(crr_module._round_half_even_scaled(0.0000025, 1_000_000), 2)
        self.assertEqual(crr_module._round_half_even_scaled(0.0000035, 1_000_000), 4)
        self.assertEqual(crr_module._round_half_even_scaled(-0.0000025, 1_000_000), -2)
        self.assertEqual(
            FULL_CHAIN_EXECUTION_STATUS,
            "BLOCKED_REFERENCE_ENGINE_NOT_REALTIME_QUALIFIED",
        )
        self.assertFalse(hasattr(crr_module, "_seal_crr_delta_result_for_test"))
        self.assertFalse(hasattr(crr_module, "_seal_computed_crr_delta_result"))

    def test_decimal_quantization_ignores_hostile_ambient_context(self) -> None:
        inputs = call_inputs()
        baseline = compute_american_call_delta(
            inputs,
            expected_model_sha256=MODEL_SHA256,
        )
        fixed = crr_module._new_fixed_decimal_context()
        self.assertEqual(
            (
                fixed.prec,
                fixed.rounding,
                fixed.Emin,
                fixed.Emax,
                fixed.capitals,
                fixed.clamp,
            ),
            (50, "ROUND_HALF_EVEN", -99, 99, 1, 0),
        )
        self.assertEqual(
            {
                signal.__name__
                for signal, enabled in fixed.traps.items()
                if enabled
            },
            {"DivisionByZero", "InvalidOperation", "Overflow"},
        )

        with localcontext() as hostile:
            hostile.prec = 2
            hostile.rounding = ROUND_UP
            hostile.Emin = -2
            hostile.Emax = 2
            for signal in hostile.traps:
                hostile.traps[signal] = True

            self.assertEqual(
                crr_module._round_half_even_scaled(0.0000025, 1_000_000),
                2,
            )
            repeated = compute_american_call_delta(
                inputs,
                expected_model_sha256=MODEL_SHA256,
            )

        self.assertEqual(repeated, baseline)
        self.assertEqual(repeated.run_sha256, baseline.run_sha256)
        self.assertTrue(is_verified_crr_delta_result(baseline))
        self.assertTrue(is_verified_crr_delta_result(repeated))

        with self.assertRaises(NormalizationError) as raised:
            crr_module._round_half_even_scaled(1e308, 1_000_000_000)
        self.assertEqual(raised.exception.reason_code, "TREE_NUMERIC_INVALID")


class CrrCanonicalInputBindingTests(unittest.TestCase):
    def test_binding_derives_economic_identity_midpoints_and_timestamps(self) -> None:
        snapshot = _binding_snapshot()
        contract = snapshot.option_quotes[0].contract
        pit_inputs = _pit_inputs()

        inputs = bind_crr_call_inputs_from_snapshot(
            snapshot,
            contract=contract,
            pit_inputs=pit_inputs,
        )

        self.assertEqual(inputs.option_snapshot_sha256, snapshot.snapshot_sha256)
        self.assertEqual(inputs.contract_id, contract.occ_symbol)
        self.assertEqual(inputs.contract_sha256, canonical_snapshot_sha256(contract))
        self.assertEqual(
            inputs.underlying_top_sha256,
            canonical_snapshot_sha256(snapshot.underlying_top),
        )
        self.assertEqual(
            inputs.option_quote_sha256,
            canonical_snapshot_sha256(snapshot.option_quotes[0]),
        )
        self.assertEqual(inputs.pit_inputs_sha256, pit_inputs.pit_inputs_sha256)
        self.assertEqual(inputs.spot_nano_usd, 100 * NANO)
        self.assertEqual(inputs.strike_nano_usd, 100 * NANO)
        self.assertEqual(inputs.option_mid_nano_usd, 9 * NANO)
        self.assertEqual(inputs.tick_nano_usd, 10_000_000)
        self.assertEqual(inputs.valuation_utc_ns, snapshot.capture_utc_ns)
        self.assertEqual(inputs.expiry_utc_ns, contract.expiry_utc_ns)
        self.assertEqual(inputs.risk_free_rate_ppm, pit_inputs.risk_free_rate_ppm)
        self.assertEqual(inputs.expense_yield_ppm, pit_inputs.expense_yield_ppm)
        self.assertEqual(inputs.borrow_yield_ppm, pit_inputs.borrow_yield_ppm)

    def test_binding_fails_closed_on_contract_or_as_of_mismatch(self) -> None:
        snapshot = _binding_snapshot()
        with self.assertRaises(NormalizationError) as style_error:
            _binding_contract(exercise_style="EUROPEAN")
        self.assertEqual(style_error.exception.reason_code, "IDENTITY_UNRESOLVED")
        wrong_contract = _binding_contract(
            occ_symbol="GLD   270219C00101000",
            strike_nano_usd=101 * NANO,
        )
        cases = (
            (
                {"contract": wrong_contract, "pit_inputs": _pit_inputs()},
                "DELTA_INPUT_BINDING_MISMATCH",
            ),
            (
                {
                    "contract": snapshot.option_quotes[0].contract,
                    "pit_inputs": _pit_inputs(as_of_utc_ns=BINDING_CAPTURE_NS - 1),
                },
                "SNAPSHOT_NOT_CAUSAL",
            ),
        )
        for arguments, reason_code in cases:
            with self.subTest(reason_code=reason_code):
                with self.assertRaises(NormalizationError) as raised:
                    bind_crr_call_inputs_from_snapshot(snapshot, **arguments)
                self.assertEqual(raised.exception.reason_code, reason_code)

    def test_binding_rejects_future_quotes_and_non_integral_midpoints(self) -> None:
        contract = _binding_contract()
        future_option = OptionQuoteV1(
            contract=contract,
            top_of_book=_binding_book(
                bid_nano_usd=8 * NANO,
                ask_nano_usd=10 * NANO,
                ts_recv_ns=BINDING_CAPTURE_NS + 1,
            ),
        )
        future_snapshot = _binding_snapshot(option_quotes=(future_option,))
        odd_snapshot = _binding_snapshot(
            underlying_top=_binding_book(
                bid_nano_usd=99 * NANO,
                ask_nano_usd=101 * NANO + 1,
            )
        )
        cases = (
            (future_snapshot, "SNAPSHOT_NOT_CAUSAL"),
            (odd_snapshot, "DELTA_INPUT_MIDPOINT_NON_INTEGRAL"),
        )
        for snapshot, reason_code in cases:
            with self.subTest(reason_code=reason_code):
                with self.assertRaises(NormalizationError) as raised:
                    bind_crr_call_inputs_from_snapshot(
                        snapshot,
                        contract=snapshot.option_quotes[0].contract,
                        pit_inputs=_pit_inputs(),
                    )
                self.assertEqual(raised.exception.reason_code, reason_code)


class CrrDeltaValidationTests(unittest.TestCase):
    def test_nested_pit_fact_is_the_only_rate_and_yield_source_of_truth(self) -> None:
        inputs = call_inputs()
        public_field_names = {item.name for item in fields(inputs)}
        self.assertIn("pit_inputs", public_field_names)
        for duplicate in (
            "pit_inputs_sha256",
            "risk_free_rate_ppm",
            "expense_yield_ppm",
            "borrow_yield_ppm",
            "rate_curve_sha256",
            "distribution_assumption_sha256",
            "borrow_assumption_sha256",
        ):
            self.assertNotIn(duplicate, public_field_names)
        self.assertEqual(
            inputs.risk_free_rate_ppm,
            inputs.pit_inputs.risk_free_rate_ppm,
        )
        self.assertEqual(inputs.pit_inputs_sha256, inputs.pit_inputs.pit_inputs_sha256)

        with self.assertRaises(TypeError):
            replace(inputs, risk_free_rate_ppm=30_000)

        changed_pit = replace(inputs.pit_inputs, risk_free_rate_ppm=30_000)
        changed_inputs = replace(inputs, pit_inputs=changed_pit)
        self.assertEqual(changed_inputs.risk_free_rate_ppm, 30_000)
        self.assertNotEqual(changed_inputs.input_sha256, inputs.input_sha256)
        self.assertNotEqual(
            changed_inputs.pit_inputs_sha256,
            inputs.pit_inputs_sha256,
        )

        with self.assertRaises(NormalizationError) as raised:
            replace(
                inputs,
                pit_inputs=replace(
                    inputs.pit_inputs,
                    as_of_utc_ns=inputs.valuation_utc_ns - 1,
                ),
            )
        self.assertEqual(raised.exception.reason_code, "SNAPSHOT_NOT_CAUSAL")

    def test_numeric_inputs_reject_bool_float_and_sentinels(self) -> None:
        cases = (
            {"spot_nano_usd": True},
            {"spot_nano_usd": 100.0},
            {"spot_nano_usd": 2**63 - 1},
            {"valuation_utc_ns": 2**64 - 1},
            {"risk_free_rate_ppm": False},
            {"risk_free_rate_ppm": 0.02},
            {"risk_free_rate_ppm": 1_000_001},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(NormalizationError) as raised:
                    call_inputs(**changes)
                self.assertEqual(raised.exception.reason_code, "DELTA_INPUT_MISSING")

    def test_bindings_must_be_lowercase_sha256_and_contract_id_is_strict(self) -> None:
        for changes in (
            {"option_snapshot_sha256": "bad"},
            {"rate_curve_sha256": "A" * 64},
            {"distribution_assumption_sha256": ""},
            {"borrow_assumption_sha256": None},
            {"contract_id": ""},
            {"contract_id": " GLD"},
            {"contract_id": "GLD\nCALL"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(NormalizationError) as raised:
                    call_inputs(**changes)
                self.assertEqual(
                    raised.exception.reason_code,
                    "DELTA_INPUT_BINDING_MISMATCH",
                )

    def test_expired_and_excessive_maturity_fail_closed(self) -> None:
        for expiry in (
            VALUATION_NS,
            VALUATION_NS - 1,
            VALUATION_NS + 101 * YEAR_NS,
        ):
            with self.subTest(expiry=expiry):
                with self.assertRaises(NormalizationError) as raised:
                    call_inputs(expiry_utc_ns=expiry)
                self.assertEqual(raised.exception.reason_code, "T_OUT_OF_DOMAIN")

    def test_price_outside_iv_bracket_fails_closed(self) -> None:
        impossible = call_inputs(option_mid_nano_usd=150 * NANO)
        with self.assertRaises(NormalizationError) as raised:
            compute_american_call_delta(
                impossible,
                expected_model_sha256=MODEL_SHA256,
            )
        self.assertEqual(raised.exception.reason_code, "IV_NO_BRACKET")

    def test_price_at_unidentifiable_zero_volatility_limit_fails_closed(self) -> None:
        unidentifiable = call_inputs(
            option_mid_nano_usd=1,
            risk_free_rate_ppm=0,
            tick_nano_usd=10_000_000,
        )
        with self.assertRaises(NormalizationError) as raised:
            compute_american_call_delta(
                unidentifiable,
                expected_model_sha256=MODEL_SHA256,
            )
        self.assertEqual(raised.exception.reason_code, "IV_UNIDENTIFIABLE")

    def test_fixed_iv_lower_bracket_rejects_subfloor_or_ambiguous_root(self) -> None:
        steps = (64, 65)
        lower = crr_module._suite_evaluation(
            spot=100.0,
            strike=100.0,
            years=1.0,
            rate=0.0,
            effective_yield=0.0,
            volatility=0.0001,
            target_price=0.0,
            steps=steps,
        )
        cases = (
            (lower.price * 0.5, "IV_NO_BRACKET"),
            (lower.price, "IV_UNIDENTIFIABLE"),
        )
        for target_price, reason_code in cases:
            with self.subTest(reason_code=reason_code):
                with self.assertRaises(NormalizationError) as raised:
                    crr_module._solve_iv_suite(
                        spot=100.0,
                        strike=100.0,
                        years=1.0,
                        rate=0.0,
                        effective_yield=0.0,
                        target_price=target_price,
                        price_tolerance=1e-12,
                        steps=steps,
                    )
                self.assertEqual(raised.exception.reason_code, reason_code)

    def test_missing_input_object_fails_closed(self) -> None:
        with self.assertRaises(NormalizationError) as raised:
            compute_american_call_delta(  # type: ignore[arg-type]
                None,
                expected_model_sha256=MODEL_SHA256,
            )
        self.assertEqual(raised.exception.reason_code, "DELTA_INPUT_MISSING")


class CrrTreeBehaviorTests(unittest.TestCase):
    def test_high_yield_deep_itm_american_call_exercises_early(self) -> None:
        evaluation = crr_module._crr_american_call_tree(
            spot=150.0,
            strike=100.0,
            years=1.0,
            rate=0.01,
            effective_yield=0.25,
            volatility=0.20,
            steps=256,
        )
        european_price, _ = crr_module._bsm_european_call_oracle(
            spot=150.0,
            strike=100.0,
            years=1.0,
            rate=0.01,
            effective_yield=0.25,
            volatility=0.20,
        )

        self.assertGreaterEqual(evaluation.price, european_price)
        self.assertGreater(evaluation.early_exercise_nodes, 0)
        self.assertGreaterEqual(evaluation.price, 50.0)

    def test_high_yield_low_volatility_invalid_probability_fails_closed(self) -> None:
        cases = (
            ("high_q", 0.0, 1.0),
            ("negative_rate", -1.0, 0.0),
            ("negative_rate_high_q", -1.0, 1.0),
        )
        for name, rate, effective_yield in cases:
            with self.subTest(name=name):
                with self.assertRaises(NormalizationError) as raised:
                    crr_module._crr_american_call_tree(
                        spot=100.0,
                        strike=100.0,
                        years=1.0,
                        rate=rate,
                        effective_yield=effective_yield,
                        volatility=0.0001,
                        steps=512,
                    )
                self.assertEqual(
                    raised.exception.reason_code,
                    "TREE_PROBABILITY_INVALID",
                )

    def test_public_api_has_no_broker_or_order_seams(self) -> None:
        forbidden = {
            "from_ibkr",
            "from_robinhood",
            "broker_delta",
            "create_order",
            "submit_order",
        }
        for name in forbidden:
            self.assertFalse(hasattr(crr_module, name), name)
        self.assertTrue(forbidden.isdisjoint(set(crr_module.__all__)))


class CrrSyntheticAcceptanceMatrixTests(unittest.TestCase):
    """Bounded price-to-IV matrix spanning the frozen contract dimensions."""

    CASES = tuple(
        (
            f"{moneyness}_{dte}d_iv{round(volatility * 100)}",
            100.0,
            strike,
            dte,
            volatility,
        )
        for moneyness, strike in (
            ("itm", 95.0),
            ("atm", 100.0),
            ("otm", 105.0),
        )
        for dte in (30, 180, 365)
        for volatility in (0.10, 0.30, 1.00)
    )

    def test_full_cross_product_round_trips_or_fails_closed_deterministically(
        self,
    ) -> None:
        self.assertEqual(len(self.CASES), 27)
        expected_failures = {
            "itm_180d_iv100": "TREE_NOT_CONVERGED",
            "otm_180d_iv100": "TREE_NOT_CONVERGED",
        }
        observed_failures: dict[str, str] = {}
        for name, spot, strike, dte, volatility in self.CASES:
            with self.subTest(case=name):
                years = dte / 365.0
                price, expected_delta = _bsm_call_price_delta(
                    spot=spot,
                    strike=strike,
                    years=years,
                    rate=0.02,
                    volatility=volatility,
                )
                inputs = call_inputs(
                    contract_id=f"SYNTHETIC_{name}",
                    spot_nano_usd=round(spot * NANO),
                    strike_nano_usd=round(strike * NANO),
                    option_mid_nano_usd=round(price * NANO),
                    expiry_utc_ns=VALUATION_NS + dte * DAY_NS,
                )
                try:
                    result = compute_american_call_delta(
                        inputs,
                        expected_model_sha256=MODEL_SHA256,
                    )
                except NormalizationError as error:
                    observed_failures[name] = error.reason_code
                    self.assertEqual(
                        error.reason_code,
                        expected_failures.get(name),
                    )
                    continue

                self.assertNotIn(name, expected_failures)
                self.assertLessEqual(
                    abs(result.iv_ppm - round(volatility * 1_000_000)),
                    750,
                )
                self.assertLessEqual(
                    abs(result.delta_ppm - round(expected_delta * 1_000_000)),
                    1_500,
                )
                self.assertLessEqual(
                    abs(result.coarse_iv_ppm - result.fine_iv_ppm),
                    100,
                )
                self.assertLessEqual(
                    abs(result.coarse_delta_ppm - result.fine_delta_ppm),
                    500,
                )
        self.assertEqual(observed_failures, expected_failures)

    def test_low_iv_deep_itm_is_unidentifiable_and_fails_closed(self) -> None:
        years = 30 / 365.0
        price, _ = _bsm_call_price_delta(
            spot=150.0,
            strike=100.0,
            years=years,
            rate=0.02,
            volatility=0.01,
        )
        inputs = call_inputs(
            contract_id="SYNTHETIC_LOW_IV_DEEP_ITM",
            spot_nano_usd=150 * NANO,
            strike_nano_usd=100 * NANO,
            option_mid_nano_usd=round(price * NANO),
            expiry_utc_ns=VALUATION_NS + 30 * DAY_NS,
        )
        with self.assertRaises(NormalizationError) as raised:
            compute_american_call_delta(
                inputs,
                expected_model_sha256=MODEL_SHA256,
            )
        self.assertEqual(raised.exception.reason_code, "IV_UNIDENTIFIABLE")

    def test_high_iv_short_dte_model_instability_fails_closed(self) -> None:
        years = 7 / 365.0
        price, _ = _bsm_call_price_delta(
            spot=100.0,
            strike=105.0,
            years=years,
            rate=0.02,
            volatility=3.0,
        )
        with self.assertRaises(NormalizationError) as raised:
            compute_american_call_delta(
                call_inputs(
                    contract_id="SYNTHETIC_HIGH_IV_SHORT_DTE",
                    strike_nano_usd=105 * NANO,
                    option_mid_nano_usd=round(price * NANO),
                    expiry_utc_ns=VALUATION_NS + 7 * DAY_NS,
                ),
                expected_model_sha256=MODEL_SHA256,
            )
        self.assertEqual(raised.exception.reason_code, "TREE_NOT_CONVERGED")


if __name__ == "__main__":
    unittest.main()
