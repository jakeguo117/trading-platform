from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import ctypes
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gld_data_contracts import technical as technical_module
from gld_data_contracts.legacy_adapter import adapt_legacy_synthetic_bundle
from gld_data_contracts.technical import (
    TechnicalFactError,
    derive_required_technical_facts,
    materialize_bcs_pair_facts,
)
from gld_data_contracts.contracts import DataContractError
from gld_data_contracts.validation import validate_entry_bundle
from gld_simulation.canonical import canonical_json_bytes, canonical_json_sha256
from gld_simulation.errors import RawBundleError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_PASS = PROJECT_ROOT / "fixtures" / "gld_simulation" / "v1" / "pass"
EXPECTED_FEATURE_HASHES = {
    "ACCOUNT.CASH_RESERVE_NANO_USD": "1900ab88fc6fffc0159c6ba0ef8671d9635ec879d722d3de2cc3c8e9d2889b43",
    "ACCOUNT.DRAWDOWN_PPM": "1ec0610e792622f7a89d7501069a6cdaa35a7fb6d7a4960901b8b062c39d18a2",
    "ACCOUNT.ELIGIBLE_BANKROLL_NANO_USD": "6a6841d65825b4a3592a1a2a84b7bc42d2c9e9de9453da76181e08c2cf775c3e",
    "ACCOUNT.EXISTING_GLD_DELTA_EXPOSURE_NANO_USD": "b09d1ef74aa6a29d6344080cc152b73aa33e4bad15cd9512e802e3b3d6e9bd21",
    "BCS0.LIQUIDITY_FACTS_BY_PAIR": "3da8b2736b58153a7220102d99e6a01bac725800866efd1671156c0c072aba52",
    "BCS0.LONG_DELTA_PPM_BY_PAIR": "8baaafefc7519e41f54fdf24428ed4a8219b5e0e7e3c5037ea6d902a27eb8824",
    "BCS0.MAX_LOSS_BASIS_NANO_USD_BY_PAIR": "ca8f1334d8e2fdab58454c9d62eaa91b8f65628309e8f4e12cf86de86f810300",
    "BCS0.NET_DEBIT_NANO_USD_BY_PAIR": "552873e77507c0a4683ac87ee2ae838da9c31739c708512df7c349a64942cca6",
    "BCS0.NET_DELTA_PPM_BY_PAIR": "b49856ae2e1143fb4a34789ce8378bcefa041ed17a9565e20113549acfe8b57c",
    "BCS0.SHORT_DELTA_PPM_BY_PAIR": "c75ee94c9096fc3e9d8244935ac505f3f8cfdbac33bf0fdcf3f1e7c7dd775319",
    "BCS0.STRESSED_DEBIT_NANO_USD_BY_PAIR": "8b3d20b8757df454a49452abc417c291886ae366fd4709ad527929200c8675e2",
    "BCS0.THEORETICAL_EXPIRY_CAP_NANO_USD_BY_PAIR": "6ea73d72aad788118add4f485ac280eba492d2d350fde081a03ca52a1cee0ef8",
    "BCS0.WIDTH_NANO_USD_BY_PAIR": "6eeed16c735dd7f03fd1ad64ea9feab92d796b5d8fbbdac01e2f56bf7c89d85d",
    "BREAKOUT.CLOSE_1044_NANO_USD": "9f317cb8fac88226e4dbe08dbe04fe7e345b37a52cd4ffc4181a4070d8300997",
    "BREAKOUT.DISTANCE_PPM": "9c0fa25fef28546a3739f24af5d05a75fd075c602350a88763dc2c436a022274",
    "BREAKOUT.HIGH20_NANO_USD": "cb01d163d2b2c71deddef7ea1ad2591fe0d42d299a8dd1e6af081be5771fd0aa",
    "CONTRACT.QUALIFICATION_FACTS_BY_CONTRACT": "4b32afab13f4838ce8088b7747a672947215781a28c4f4fd70838d11068ebef5",
    "INTRADAY.ABOVE_BREAKOUT_COUNT": "5c28b2b1b893c250c0dd154bdcfa3662db4704ff7bcf5acae59879fe39e2c673",
    "INTRADAY.CONFIRMATION_CLOSES_15_NANO_USD": "a24d5215439ddeb60e27e8a721723749ba12d3a958071e6c1641001551460afa",
    "LC0.DELTA_DISTANCE_PPM_BY_CONTRACT": "d1b89cefc1bdd0f7fac81a4294eba370337cc2c8eddf59c4364c3719b2b41584",
    "LC0.DELTA_NOTIONAL_NANO_USD_BY_CONTRACT": "56764b6a9fd2150959fc147fe278fb1a756b41c5b3035a5b30a106dab6a4068b",
    "LC0.ENTRY_DEBIT_NANO_USD_BY_CONTRACT": "ac7c73c47130f7d8b9eeb6fbbd68a849b85e19b82857e26224d01c18f725a492",
    "LC0.LIQUIDITY_FACTS_BY_CONTRACT": "6a2ea6477d7fba138aab9d90b51b2b444b25bd4360b3cd0aec865974125eaed3",
    "LC0.MAX_LOSS_BASIS_NANO_USD_BY_CONTRACT": "2705072a045487111b98c0bb9b4972ed7934631c40dbc53bb4a1f104f57ff33c",
    "LC0.STRESSED_DEBIT_NANO_USD_BY_CONTRACT": "8442a5746a6aa439f6962672cd072c42de021ead58a778ce897522bdcac62249",
    "MODEL.CONVERGENCE_FACTS_BY_CONTRACT": "5c64ac93406439d0ad6dd6024c4b5ab6f0971ab81e5d9b55da3789c75df8ec36",
    "MODEL.DELTA_COARSE_PPM_BY_CONTRACT": "dcae4e22a3aa18562a5633c96193f56b8d4492a3ca29ebf4e4c914c336da467e",
    "MODEL.DELTA_FINE_PPM_BY_CONTRACT": "8af9a6f9210802337ef5d1dafab6631785434039f683932615ad8352efde2a3f",
    "MODEL.INPUT_HASH_BY_CONTRACT": "136a469967087dba0e0ad318e0e1c83c011a388d6e23e0837b1a26c929786090",
    "MODEL.IV_PPM_BY_CONTRACT": "cd2100350de53f59a7ef4b4bd22f22d863c6da633dc4733eb60f72c388b60804",
    "QUOTE.AGE_NS_BY_CONTRACT": "78f34c29071faa0e5fa7ff22ab09f7203a4e11601dbc08403c52e5f93824c9d2",
    "QUOTE.CROSS_LEG_RECEIVE_SKEW_NS_BY_BCS_PAIR": "c53b27dd2e62a5cb4cc6c53f2345cc45fd1b9507142b8b1f2ed18e099750c4e5",
    "QUOTE.EXECUTABILITY_FACTS_BY_CONTRACT": "87edcefd3fcb3c199f7486da720b03ab39e9b31c526b7d91376da47149f64e4a",
    "QUOTE.SIZE_BY_CONTRACT": "84040f7dbc80a3be0da62bfc20e7b90459533f4c7e4dd19a66968579b571bede",
    "QUOTE.SPREAD_NANO_USD_BY_CONTRACT": "6bda655e83ec1b60303e72386a0fcc1b1fd578ffd4bb5af89fe37fc8be0619fb",
    "QUOTE.TICK_NANO_USD_BY_CONTRACT": "c842fd4370ce3691b0d0b8e6155d9bcbf3d20675cff3b60e0955d29a0122b26c",
    "QUOTE.UNDERLYING_BBO_QUALITY_FACTS": "c847ddb3065ad7c51b9d0a762ec7dc71eab5875aee3fb891567cf5347b0231b5",
    "TIME.DTE_CALENDAR_DAYS_BY_CONTRACT": "0e96424f04496eb8dd26868dbcccd840744de35774d6bad7c0c4ccda718e2012",
    "TIME.EXPIRY_SAFETY_GAP_CALENDAR_DAYS_BY_CONTRACT": "3b7514455afa3097994715d395927a4dcfad114dbbbfdcc456faece8b97d494e",
    "TIME.H20_DATE": "62aacc603f4c40c2abaa1e7737a5e719a895d354fc5bb134e1a7cedd6953663f",
    "TIME.H20_SESSION_ID": "413c895f7e720d9b5b4bfa593a3fe4fdacbc44aedae926145ad78f01e311e899",
    "TREND.PRIOR_CLOSE_NANO_USD": "60f5ef1672ab4f5d4a04ad3cbb40b4195a1da661bd525bd31b857a6ae0b1f7a1",
    "TREND.SMA200_NANO_USD": "414e1b399e4fd706b5d43de97453316f9aa6d321f14c9f18c5353c47c688d732",
    "TREND.SMA50_NANO_USD": "9e60698a7b94c94f6103bdbb453888dfc2953ff5ae04ca9241665ca0778de98f",
    "TREND.SMA50_SLOPE_20_PPM_PER_SESSION": "ab368fe2ef556337d72ce1a5a6fb25355e71e597ea44cd8187a15396c38faef2",
}


def _normalized_key(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _assert_answer_free(test: unittest.TestCase, value: object) -> None:
    if type(value) is dict:
        for key, item in value.items():
            normalized = _normalized_key(key)
            test.assertNotIn(
                normalized,
                {
                    "sma50",
                    "sma200",
                    "iv",
                    "delta",
                    "winner",
                    "quantity",
                    "preference",
                    "decisionresult",
                },
            )
            _assert_answer_free(test, item)
    elif type(value) is list:
        for item in value:
            _assert_answer_free(test, item)


class LegacySyntheticAdapterTests(unittest.TestCase):
    def test_adapter_keeps_the_old_bundle_synthetic_and_answer_free(self) -> None:
        entry = adapt_legacy_synthetic_bundle(LEGACY_PASS)

        self.assertEqual(entry["schema_version"], "ENTRY_FACT_BUNDLE_V1")
        self.assertEqual(entry["classification"], "SYNTHETIC_ONLY")
        self.assertEqual(entry["underlying"], "GLD")
        self.assertEqual(len(entry["daily_bars"]["bars"]), 220)
        self.assertEqual(len(entry["option_snapshot"]["option_quotes"]), 64)
        self.assertNotIn("frozen_kelly_receipt", entry)
        _assert_answer_free(self, entry)

    def test_legacy_manifest_file_order_does_not_change_adapted_bytes(self) -> None:
        expected = adapt_legacy_synthetic_bundle(LEGACY_PASS)
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "pass"
            shutil.copytree(LEGACY_PASS, copied)
            manifest_path = copied / "manifest.json"
            manifest = json.loads(manifest_path.read_bytes())
            manifest["files"].reverse()
            manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")

            actual = adapt_legacy_synthetic_bundle(copied)

        self.assertEqual(canonical_json_bytes(actual), canonical_json_bytes(expected))

    def test_legacy_registered_file_missing_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "pass"
            shutil.copytree(LEGACY_PASS, copied)
            manifest = json.loads((copied / "manifest.json").read_bytes())
            missing_path = copied / manifest["files"][0]["path"]
            missing_path.unlink()
            with self.assertRaisesRegex(
                RawBundleError,
                "RAW_BUNDLE_REGISTERED_FILE_MISSING",
            ):
                adapt_legacy_synthetic_bundle(copied)

    def test_full_legacy_bundle_derives_all_64_contracts(self) -> None:
        result = derive_required_technical_facts(
            adapt_legacy_synthetic_bundle(LEGACY_PASS)
        )
        document = result.document

        self.assertEqual(
            result.technical_facts_sha256,
            "c785e82304f0af148e290d1a170f3fa941288e48ed3e44e3417deb7da6f9911b",
        )
        self.assertEqual(len(document["contract_facts"]), 64)
        self.assertEqual(document["bcs_pair_facts"]["pair_count"], 480)
        self.assertEqual(len(document["features"]), 45)


class RequiredDailyTechnicalFactsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.entry = adapt_legacy_synthetic_bundle(LEGACY_PASS)
        snapshot = cls.entry["option_snapshot"]
        snapshot["option_quotes"] = snapshot["option_quotes"][:2]
        cls.entry["content_hashes"]["option_snapshot_sha256"] = (
            canonical_json_sha256(snapshot)
        )
        cls.result = derive_required_technical_facts(cls.entry)

    @staticmethod
    def _features(document: dict[str, object]) -> dict[str, object]:
        return {
            item["feature_id"]: item["value"]
            for item in document["features"]
        }

    def test_same_validated_entry_derives_identical_canonical_bytes(self) -> None:
        second = derive_required_technical_facts(
            deepcopy(self.entry)
        )

        self.assertEqual(self.result.canonical_bytes, second.canonical_bytes)
        self.assertEqual(
            self.result.technical_facts_sha256,
            second.technical_facts_sha256,
        )

    def test_synthetic_typed_input_cannot_inject_external_trust_state(self) -> None:
        typed = validate_entry_bundle(
            deepcopy(self.entry),
            trusted_source_receipt_sha256=frozenset({"1" * 64}),
        )
        from_typed = derive_required_technical_facts(typed)

        self.assertEqual(
            typed.qualification.trusted_source_receipt_set_sha256,
            canonical_json_sha256([]),
        )
        self.assertEqual(
            from_typed.canonical_bytes,
            self.result.canonical_bytes,
        )
        self.assertEqual(
            from_typed.technical_facts_sha256,
            self.result.technical_facts_sha256,
        )

    def test_raw_semantic_list_order_does_not_change_technical_bytes(self) -> None:
        reordered = deepcopy(self.entry)
        for domain, member in (
            ("calendar", "sessions"),
            ("daily_bars", "bars"),
            ("minute_bars", "bars"),
            ("option_snapshot", "option_quotes"),
            ("pit_inputs", "rate_curve"),
        ):
            reordered[domain][member].reverse()
        reordered["source_qualification_receipts"].reverse()
        reordered["option_snapshot"]["underlying_bbo"]["flags"].reverse()

        result = derive_required_technical_facts(reordered)

        self.assertEqual(result.canonical_bytes, self.result.canonical_bytes)
        self.assertEqual(
            result.technical_facts_sha256,
            self.result.technical_facts_sha256,
        )

    def test_output_contains_facts_but_no_carrier_or_decision_answers(self) -> None:
        document = self.result.document

        self.assertEqual(
            document["schema_version"], "REQUIRED_DAILY_TECHNICAL_FACTS_V1"
        )
        self.assertEqual(
            document["data_qualification_status"],
            "STRUCTURALLY_VALID_SYNTHETIC",
        )
        self.assertEqual(
            document["data_qualification_reason_codes"],
            ["SYNTHETIC_SOURCE_ONLY"],
        )
        self.assertEqual(len(document["contract_facts"]), 2)
        self.assertEqual(document["bcs_pair_facts"]["pair_count"], 1)
        encoded = self.result.canonical_bytes.decode("ascii").lower()
        for forbidden in (
            '"carrier_pass"',
            '"carrier_fail"',
            '"winner"',
            '"quantity"',
            '"preference"',
            '"decisionresult"',
        ):
            self.assertNotIn(forbidden, encoded)

    def test_research_only_bundle_cannot_enter_daily_fact_input_or_hash(self) -> None:
        entry = deepcopy(self.entry)
        entry["research_feature_bundle"] = {
            "authority": "RESEARCH_ONLY",
            "atr_ppm": 123,
        }
        with self.assertRaisesRegex(DataContractError, "ENTRY_SCHEMA_INVALID"):
            derive_required_technical_facts(entry)
        self.assertFalse(self.result.document["research_features_included"])

    def test_result_typed_seal_rejects_hash_forgery(self) -> None:
        with self.assertRaisesRegex(
            TechnicalFactError,
            "TECHNICAL_RESULT_SEAL_INVALID",
        ):
            replace(self.result, technical_facts_sha256="0" * 64)

    def test_result_seal_recomputes_model_composite_run_binding(self) -> None:
        document = self.result.document
        convergence_feature = next(
            feature
            for feature in document["features"]
            if feature["feature_id"]
            == "MODEL.CONVERGENCE_FACTS_BY_CONTRACT"
        )
        first = "OCC:GLD___261009C00185000"
        convergence_feature["value"][first]["core_run_sha256"] = "0" * 64
        feature_unsigned = {
            key: value
            for key, value in convergence_feature.items()
            if key != "feature_hash"
        }
        convergence_feature["feature_hash"] = canonical_json_sha256(
            feature_unsigned
        )
        with self.assertRaisesRegex(
            TechnicalFactError,
            "TECHNICAL_RESULT_SEAL_INVALID",
        ):
            technical_module.RequiredDailyTechnicalFactsV1(
                technical_facts_sha256=canonical_json_sha256(document),
                canonical_bytes=canonical_json_bytes(document),
                _seal=technical_module._TECHNICAL_RESULT_SEAL,
            )

    def test_result_seal_rejects_not_converged_core_run_hash(self) -> None:
        entry = deepcopy(self.entry)
        entry["pit_inputs"]["borrow_available"] = False
        entry["content_hashes"]["pit_inputs_sha256"] = canonical_json_sha256(
            entry["pit_inputs"]
        )
        document = derive_required_technical_facts(entry).document
        convergence_feature = next(
            feature
            for feature in document["features"]
            if feature["feature_id"]
            == "MODEL.CONVERGENCE_FACTS_BY_CONTRACT"
        )
        first = "OCC:GLD___261009C00185000"
        convergence_feature["value"][first]["core_run_sha256"] = "0" * 64
        feature_unsigned = {
            key: value
            for key, value in convergence_feature.items()
            if key != "feature_hash"
        }
        convergence_feature["feature_hash"] = canonical_json_sha256(
            feature_unsigned
        )
        with self.assertRaisesRegex(
            TechnicalFactError,
            "TECHNICAL_RESULT_SEAL_INVALID",
        ):
            technical_module.RequiredDailyTechnicalFactsV1(
                technical_facts_sha256=canonical_json_sha256(document),
                canonical_bytes=canonical_json_bytes(document),
                _seal=technical_module._TECHNICAL_RESULT_SEAL,
            )

    def test_shared_pair_component_store_cannot_be_resealed_independently(self) -> None:
        document = self.result.document
        store = document["bcs_pair_facts"]["component_store"]
        components = store["components"]
        first = "OCC:GLD___261009C00185000"
        components["ask_nano_usd_by_contract"][first] += 10_000_000
        store_unsigned = {
            key: value
            for key, value in store.items()
            if key != "component_store_sha256"
        }
        store["component_store_sha256"] = canonical_json_sha256(store_unsigned)
        encoded = canonical_json_bytes(document)
        with self.assertRaisesRegex(
            TechnicalFactError,
            "TECHNICAL_RESULT_SEAL_INVALID",
        ):
            technical_module.RequiredDailyTechnicalFactsV1(
                technical_facts_sha256=canonical_json_sha256(document),
                canonical_bytes=encoded,
                _seal=technical_module._TECHNICAL_RESULT_SEAL,
            )

    def test_unqualified_production_input_cannot_emit_technical_facts(self) -> None:
        entry = deepcopy(self.entry)
        entry["classification"] = "PRODUCTION_CANDIDATE"
        with self.assertRaisesRegex(
            TechnicalFactError,
            "TECHNICAL_ENTRY_DATA_NOT_QUALIFIED",
        ):
            derive_required_technical_facts(entry)

    def test_model_runtime_fingerprint_is_explicit_and_pinned(self) -> None:
        entry = deepcopy(self.entry)
        model_package = entry["model_package"]
        self.assertIs(type(model_package), dict)
        model_package["runtime_fingerprint_sha256"] = "0" * 64
        entry["content_hashes"]["model_package_sha256"] = (
            canonical_json_sha256(model_package)
        )
        with self.assertRaisesRegex(
            TechnicalFactError,
            "TECHNICAL_MODEL_PACKAGE_UNSUPPORTED",
        ):
            derive_required_technical_facts(entry)

    def test_model_convergence_binds_live_c_numeric_environment(self) -> None:
        document = self.result.document
        features = self._features(document)
        first = "OCC:GLD___261009C00185000"
        convergence = features["MODEL.CONVERGENCE_FACTS_BY_CONTRACT"][first]
        numeric_environment = {
            "schema_version": "TECHNICAL_C_NUMERIC_ENVIRONMENT_V1",
            "c_fenv_rounding_mode": "FE_TONEAREST",
            "c_fenv_rounding_value": 0,
        }
        numeric_environment_sha256 = canonical_json_sha256(numeric_environment)
        self.assertEqual(
            document["numeric_runtime_environment"],
            numeric_environment,
        )
        self.assertEqual(
            document["numeric_runtime_environment_sha256"],
            numeric_environment_sha256,
        )
        self.assertEqual(
            convergence["numeric_runtime_environment_sha256"],
            numeric_environment_sha256,
        )
        self.assertNotIn("numeric_runtime_environment", convergence)
        self.assertEqual(
            convergence["core_run_sha256"],
            "03e761f954d1cf68e8a913aea89c821bbc432f0efe27d2824358a9ea348a5c85",
        )
        self.assertEqual(
            convergence["run_sha256"],
            canonical_json_sha256(
                {
                    "schema_version": (
                        "TECHNICAL_MODEL_COMPOSITE_RUN_BINDING_V1"
                    ),
                    "core_run_sha256": convergence["core_run_sha256"],
                    "numeric_runtime_environment_sha256": (
                        numeric_environment_sha256
                    ),
                }
            ),
        )

    def test_numeric_environment_read_failure_is_fail_closed(self) -> None:
        with patch.object(
            technical_module,
            "_read_live_c_fenv_rounding_value",
            side_effect=OSError("unavailable"),
        ):
            with self.assertRaisesRegex(
                TechnicalFactError,
                "TECHNICAL_NUMERIC_ENVIRONMENT_UNSUPPORTED",
            ):
                derive_required_technical_facts(deepcopy(self.entry))

    def test_transient_rounding_wrapper_is_rejected_before_crr(self) -> None:
        key = (platform.system(), platform.machine())
        fe_upward_by_platform = {
            ("Darwin", "arm64"): 0x00400000,
            ("Darwin", "x86_64"): 0x0800,
        }
        if key not in fe_upward_by_platform:
            self.skipTest("actual fenv regression is supported on macOS only")
        libc = ctypes.CDLL(None)
        libc.fesetround.argtypes = [ctypes.c_int]
        libc.fesetround.restype = ctypes.c_int
        original_compute = technical_module.compute_american_call_delta
        invoked = False

        def transient_rounding_wrapper(*args, **kwargs):
            nonlocal invoked
            invoked = True
            if libc.fesetround(fe_upward_by_platform[key]) != 0:
                raise AssertionError("FESETROUND_DURING_COMPUTE_FAILED")
            try:
                return original_compute(*args, **kwargs)
            finally:
                if libc.fesetround(0) != 0:
                    raise AssertionError("FESETROUND_RESET_FAILED")

        try:
            with patch.object(
                technical_module,
                "compute_american_call_delta",
                transient_rounding_wrapper,
            ):
                with self.assertRaisesRegex(
                    TechnicalFactError,
                    "TECHNICAL_MODEL_RUNTIME_IDENTITY_MISMATCH",
                ):
                    derive_required_technical_facts(deepcopy(self.entry))
        finally:
            self.assertEqual(libc.fesetround(0), 0)
        self.assertFalse(invoked)

    def test_local_runtime_rejects_non_captured_callable_identity(self) -> None:
        cases = (
            (
                lambda *_args, **_kwargs: None,
                technical_module.is_verified_crr_delta_result,
            ),
            (
                technical_module.compute_american_call_delta,
                lambda _result: True,
            ),
        )
        for compute, verify in cases:
            with self.subTest(compute=compute, verify=verify):
                with self.assertRaisesRegex(
                    TechnicalFactError,
                    "TECHNICAL_MODEL_RUNTIME_IDENTITY_MISMATCH",
                ):
                    technical_module.LocalCrrRuntimeV1(
                        compute=compute,
                        verify=verify,
                        runtime_kind="REFERENCE",
                        _seal=technical_module._RUNTIME_SEAL,
                    )

    def test_public_verify_global_drift_is_rejected_before_crr(self) -> None:
        with patch.object(
            technical_module,
            "is_verified_crr_delta_result",
            lambda _result: True,
        ):
            with self.assertRaisesRegex(
                TechnicalFactError,
                "TECHNICAL_MODEL_RUNTIME_IDENTITY_MISMATCH",
            ):
                derive_required_technical_facts(deepcopy(self.entry))

    def test_transient_profile_rounding_hook_is_rejected_before_crr(self) -> None:
        key = (platform.system(), platform.machine())
        fe_upward_by_platform = {
            ("Darwin", "arm64"): 0x00400000,
            ("Darwin", "x86_64"): 0x0800,
        }
        if key not in fe_upward_by_platform:
            self.skipTest("actual fenv regression is supported on macOS only")
        libc = ctypes.CDLL(None)
        libc.fesetround.argtypes = [ctypes.c_int]
        libc.fesetround.restype = ctypes.c_int
        core_invoked = False

        def profile(frame, event, _arg):
            nonlocal core_invoked
            if frame.f_code.co_name == "_compute_american_call_delta_unsealed":
                if event == "call":
                    core_invoked = True
                    if libc.fesetround(fe_upward_by_platform[key]) != 0:
                        raise AssertionError("FESETROUND_PROFILE_CALL_FAILED")
                elif event == "return":
                    if libc.fesetround(0) != 0:
                        raise AssertionError("FESETROUND_PROFILE_RETURN_FAILED")
            return profile

        try:
            sys.setprofile(profile)
            with self.assertRaisesRegex(
                TechnicalFactError,
                "TECHNICAL_MODEL_RUNTIME_HOOK_UNSUPPORTED",
            ):
                derive_required_technical_facts(deepcopy(self.entry))
        finally:
            sys.setprofile(None)
            self.assertEqual(libc.fesetround(0), 0)
        self.assertFalse(core_invoked)

    def test_active_trace_hook_is_fail_closed(self) -> None:
        def trace(_frame, _event, _arg):
            return trace

        try:
            sys.settrace(trace)
            with self.assertRaisesRegex(
                TechnicalFactError,
                "TECHNICAL_MODEL_RUNTIME_HOOK_UNSUPPORTED",
            ):
                derive_required_technical_facts(deepcopy(self.entry))
        finally:
            sys.settrace(None)

    def test_actual_fe_upward_subprocess_is_stably_rejected(self) -> None:
        key = (platform.system(), platform.machine())
        fe_upward_by_platform = {
            ("Darwin", "arm64"): 0x00400000,
            ("Darwin", "x86_64"): 0x0800,
        }
        if key not in fe_upward_by_platform:
            self.skipTest("actual fenv regression is supported on macOS only")
        code = """
import ctypes
from copy import deepcopy
import platform

from gld_data_contracts import technical as technical_module
from gld_data_contracts.legacy_adapter import adapt_legacy_synthetic_bundle
from gld_data_contracts.technical import TechnicalFactError, derive_required_technical_facts
from gld_simulation.canonical import canonical_json_sha256

entry = adapt_legacy_synthetic_bundle(r"%s")
entry["option_snapshot"]["option_quotes"] = entry["option_snapshot"]["option_quotes"][:2]
entry["content_hashes"]["option_snapshot_sha256"] = canonical_json_sha256(entry["option_snapshot"])
first = derive_required_technical_facts(deepcopy(entry))
second = derive_required_technical_facts(deepcopy(entry))
if first.canonical_bytes != second.canonical_bytes:
    raise SystemExit("DEFAULT_BYTES_DIFFER")
libc = ctypes.CDLL(None)
libc.fesetround.argtypes = [ctypes.c_int]
libc.fesetround.restype = ctypes.c_int
fe_upward = %d
if libc.fesetround(fe_upward) != 0:
    raise SystemExit("FESETROUND_FAILED")

def require_rejected(label):
    try:
        derive_required_technical_facts(deepcopy(entry))
    except TechnicalFactError as error:
        if error.reason_code != "TECHNICAL_NUMERIC_ENVIRONMENT_UNSUPPORTED":
            raise
    else:
        raise SystemExit(label + "_WAS_ACCEPTED")

require_rejected("FE_UPWARD_AT_MODEL_ENTRY")
if libc.fesetround(0) != 0:
    raise SystemExit("FESETROUND_RESET_FAILED")
print("TECHNICAL_NUMERIC_ENVIRONMENT_UNSUPPORTED")
""" % (LEGACY_PASS, fe_upward_by_platform[key])
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout={completed.stdout!r} stderr={completed.stderr!r}",
        )
        self.assertEqual(
            completed.stdout.strip(),
            "TECHNICAL_NUMERIC_ENVIRONMENT_UNSUPPORTED",
        )

    def test_deliverable_shares_are_part_of_bcs_pair_identity(self) -> None:
        base = deepcopy(self.entry)
        pit = base["pit_inputs"]
        self.assertIs(type(pit), dict)
        pit["borrow_available"] = False
        base["content_hashes"]["pit_inputs_sha256"] = canonical_json_sha256(pit)
        baseline = derive_required_technical_facts(base)

        changed = deepcopy(base)
        snapshot = changed["option_snapshot"]
        self.assertIs(type(snapshot), dict)
        snapshot["option_quotes"][1]["contract"]["deliverable_shares"] = 50
        snapshot["option_quotes"][1]["contract"]["standard_unadjusted"] = False
        changed["content_hashes"]["option_snapshot_sha256"] = (
            canonical_json_sha256(snapshot)
        )
        result = derive_required_technical_facts(changed)

        self.assertEqual(baseline.document["bcs_pair_facts"]["pair_count"], 1)
        self.assertEqual(result.document["bcs_pair_facts"]["pair_count"], 0)
        self.assertNotEqual(
            baseline.document["bcs_pair_facts"]["pair_relation_sha256"],
            result.document["bcs_pair_facts"]["pair_relation_sha256"],
        )
        baseline_features = {
            item["feature_id"]: item for item in baseline.document["features"]
        }
        changed_features = {
            item["feature_id"]: item for item in result.document["features"]
        }
        for feature_id in technical_module._FACTORED_PAIR_FEATURE_IDS:
            self.assertNotEqual(
                baseline_features[feature_id]["feature_hash"],
                changed_features[feature_id]["feature_hash"],
            )
        with self.assertRaisesRegex(
            TechnicalFactError,
            "TECHNICAL_BCS_PAIR_IDENTITY_INVALID",
        ):
            materialize_bcs_pair_facts(
                result,
                long_contract_id="OCC:GLD___261009C00185000",
                short_contract_id="OCC:GLD___261009C00187500",
            )

    def test_cross_leg_provenance_binds_snapshot_identity(self) -> None:
        base = deepcopy(self.entry)
        pit = base["pit_inputs"]
        self.assertIs(type(pit), dict)
        pit["borrow_available"] = False
        base["content_hashes"]["pit_inputs_sha256"] = canonical_json_sha256(pit)
        baseline = derive_required_technical_facts(base)

        changed = deepcopy(base)
        snapshot = changed["option_snapshot"]
        self.assertIs(type(snapshot), dict)
        snapshot["snapshot_id"] = "same-facts-new-snapshot-id"
        changed["content_hashes"]["option_snapshot_sha256"] = (
            canonical_json_sha256(snapshot)
        )
        result = derive_required_technical_facts(changed)
        feature_id = "QUOTE.CROSS_LEG_RECEIVE_SKEW_NS_BY_BCS_PAIR"
        baseline_feature = next(
            item for item in baseline.document["features"]
            if item["feature_id"] == feature_id
        )
        changed_feature = next(
            item for item in result.document["features"]
            if item["feature_id"] == feature_id
        )
        self.assertNotEqual(
            baseline_feature["source_fact_hash"],
            changed_feature["source_fact_hash"],
        )
        self.assertNotEqual(
            baseline_feature["feature_hash"],
            changed_feature["feature_hash"],
        )

    def test_same_version_catalog_metadata_tamper_is_rejected(self) -> None:
        catalog = json.loads(
            (
                PROJECT_ROOT
                / "docs"
                / "GLD_TECHNICAL_FEATURE_CATALOG_V1.json"
            ).read_bytes()
        )
        catalog["features"][0]["formula_expression"] = "tampered / 49"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            path.write_bytes(canonical_json_bytes(catalog) + b"\n")
            with patch.object(technical_module, "_CATALOG_PATH", path):
                with self.assertRaisesRegex(
                    TechnicalFactError,
                    "TECHNICAL_CATALOG_INVALID",
                ):
                    derive_required_technical_facts(self.entry)

    def test_unrelated_account_change_does_not_rehash_trend_provenance(self) -> None:
        baseline = {
            item["feature_id"]: item
            for item in self.result.document["features"]
        }
        entry = deepcopy(self.entry)
        account = entry["account_snapshot_a"]
        self.assertIs(type(account), dict)
        account["settled_cash_nano_usd"] = (
            int(account["settled_cash_nano_usd"]) + 1
        )
        entry["content_hashes"]["account_snapshot_a_sha256"] = (
            canonical_json_sha256(account)
        )
        changed = derive_required_technical_facts(entry)
        materialized = {
            item["feature_id"]: item for item in changed.document["features"]
        }
        trend_id = "TREND.PRIOR_CLOSE_NANO_USD"
        self.assertEqual(
            baseline[trend_id]["source_fact_hash"],
            materialized[trend_id]["source_fact_hash"],
        )
        self.assertEqual(
            baseline[trend_id]["feature_hash"],
            materialized[trend_id]["feature_hash"],
        )

    def test_unselected_daily_volume_does_not_rehash_trend_provenance(self) -> None:
        baseline = {
            item["feature_id"]: item
            for item in self.result.document["features"]
        }
        entry = deepcopy(self.entry)
        daily = entry["daily_bars"]
        self.assertIs(type(daily), dict)
        daily["bars"][0]["volume"] = int(daily["bars"][0]["volume"]) + 1
        entry["content_hashes"]["daily_bars_sha256"] = canonical_json_sha256(
            daily
        )

        changed = derive_required_technical_facts(entry)
        materialized = {
            item["feature_id"]: item for item in changed.document["features"]
        }
        for trend_id in (
            "TREND.PRIOR_CLOSE_NANO_USD",
            "TREND.SMA50_NANO_USD",
            "TREND.SMA200_NANO_USD",
            "TREND.SMA50_SLOPE_20_PPM_PER_SESSION",
            "BREAKOUT.HIGH20_NANO_USD",
        ):
            with self.subTest(feature_id=trend_id):
                self.assertEqual(
                    baseline[trend_id]["source_fact_hash"],
                    materialized[trend_id]["source_fact_hash"],
                )
                self.assertEqual(
                    baseline[trend_id]["feature_hash"],
                    materialized[trend_id]["feature_hash"],
                )

    def test_daily_window_sentinels_bind_only_the_declared_sessions(self) -> None:
        base = deepcopy(self.entry)
        pit = base["pit_inputs"]
        self.assertIs(type(pit), dict)
        pit["borrow_available"] = False
        base["content_hashes"]["pit_inputs_sha256"] = canonical_json_sha256(pit)
        baseline = self._features(derive_required_technical_facts(base).document)

        def shifted(index: int, offset: int, *, high_only: bool = False) -> dict[str, object]:
            entry = deepcopy(base)
            daily = entry["daily_bars"]
            self.assertIs(type(daily), dict)
            bar = daily["bars"][index]
            if high_only:
                bar["high_nano_usd"] = int(bar["high_nano_usd"]) + offset
            else:
                for field in (
                    "open_nano_usd",
                    "high_nano_usd",
                    "low_nano_usd",
                    "close_nano_usd",
                ):
                    bar[field] = int(bar[field]) + offset
            entry["content_hashes"]["daily_bars_sha256"] = canonical_json_sha256(
                daily
            )
            return self._features(derive_required_technical_facts(entry).document)

        outside = shifted(0, 1_000_000_000)
        for feature_id in (
            "TREND.PRIOR_CLOSE_NANO_USD",
            "TREND.SMA50_NANO_USD",
            "TREND.SMA200_NANO_USD",
            "TREND.SMA50_SLOPE_20_PPM_PER_SESSION",
            "BREAKOUT.HIGH20_NANO_USD",
        ):
            self.assertEqual(outside[feature_id], baseline[feature_id])

        sma200_boundary = shifted(-200, 200_000_000)
        self.assertEqual(
            sma200_boundary["TREND.SMA200_NANO_USD"],
            baseline["TREND.SMA200_NANO_USD"] + 1_000_000,
        )
        self.assertEqual(
            sma200_boundary["TREND.SMA50_NANO_USD"],
            baseline["TREND.SMA50_NANO_USD"],
        )

        sma50_boundary = shifted(-50, 5_000_000_000)
        self.assertEqual(
            sma50_boundary["TREND.SMA50_NANO_USD"],
            baseline["TREND.SMA50_NANO_USD"] + 100_000_000,
        )
        self.assertNotEqual(
            sma50_boundary["TREND.SMA50_SLOPE_20_PPM_PER_SESSION"],
            baseline["TREND.SMA50_SLOPE_20_PPM_PER_SESSION"],
        )

        high20_boundary = shifted(-20, 10_000_000_000, high_only=True)
        self.assertGreater(
            high20_boundary["BREAKOUT.HIGH20_NANO_USD"],
            baseline["BREAKOUT.HIGH20_NANO_USD"],
        )
        self.assertEqual(
            high20_boundary["TREND.SMA50_NANO_USD"],
            baseline["TREND.SMA50_NANO_USD"],
        )

    def test_integer_rounding_modes_are_explicit_at_half_and_negative_boundaries(self) -> None:
        self.assertEqual(technical_module._half_even_div_two(3), 2)
        self.assertEqual(technical_module._half_even_div_two(5), 2)
        self.assertEqual(technical_module._half_even_div_two(-3), -2)
        self.assertEqual(technical_module._half_even_div_two(-5), -2)
        self.assertEqual(technical_module._trunc_div(-3, 2), -1)

        entry = deepcopy(self.entry)
        daily = entry["daily_bars"]
        self.assertIs(type(daily), dict)
        step = 100_000_001
        closes = []
        for index, bar in enumerate(daily["bars"]):
            close = 300_000_000_000 - index * step
            closes.append(close)
            for field in (
                "open_nano_usd",
                "high_nano_usd",
                "low_nano_usd",
                "close_nano_usd",
            ):
                bar[field] = close
        entry["content_hashes"]["daily_bars_sha256"] = canonical_json_sha256(
            daily
        )
        pit = entry["pit_inputs"]
        self.assertIs(type(pit), dict)
        pit["borrow_available"] = False
        entry["content_hashes"]["pit_inputs_sha256"] = canonical_json_sha256(pit)

        features = self._features(derive_required_technical_facts(entry).document)
        current = sum(closes[-50:]) // 50
        lagged = sum(closes[-70:-20]) // 50
        numerator = (current - lagged) * 1_000_000
        denominator = lagged * 20
        expected_floor = numerator // denominator
        toward_zero = technical_module._trunc_div(numerator, denominator)
        self.assertLess(expected_floor, 0)
        self.assertEqual(
            features["TREND.SMA50_SLOPE_20_PPM_PER_SESSION"],
            expected_floor,
        )
        self.assertEqual(expected_floor, toward_zero - 1)

    def test_pass_fixture_golden_features_cover_each_required_domain(self) -> None:
        document = self.result.document
        features = self._features(document)
        first = "OCC:GLD___261009C00185000"
        pair = f"{first}|OCC:GLD___261009C00187500"
        decoded_pair = materialize_bcs_pair_facts(
            self.result,
            long_contract_id=first,
            short_contract_id="OCC:GLD___261009C00187500",
        )
        decoded_features = decoded_pair["features"]
        decoded_unsigned = {
            key: value
            for key, value in decoded_pair.items()
            if key != "pair_facts_sha256"
        }
        self.assertEqual(
            decoded_pair["pair_facts_sha256"],
            canonical_json_sha256(decoded_unsigned),
        )
        with self.assertRaisesRegex(
            TechnicalFactError,
            "TECHNICAL_BCS_PAIR_IDENTITY_INVALID",
        ):
            materialize_bcs_pair_facts(
                self.result,
                long_contract_id="OCC:GLD___261009C00187500",
                short_contract_id=first,
            )

        self.assertEqual(
            self.result.technical_facts_sha256,
            "69a45ab2e070dcb0ca48a673a2bc7407f56f29499354ffde32d555952db2284e",
        )

        expected_scalars = {
            "TREND.PRIOR_CLOSE_NANO_USD": 199_000_000_000,
            "TREND.SMA50_NANO_USD": 196_550_000_000,
            "TREND.SMA200_NANO_USD": 189_050_000_000,
            "TREND.SMA50_SLOPE_20_PPM_PER_SESSION": 514,
            "BREAKOUT.HIGH20_NANO_USD": 199_400_000_000,
            "BREAKOUT.CLOSE_1044_NANO_USD": 199_690_000_000,
            "BREAKOUT.DISTANCE_PPM": 1_454,
            "INTRADAY.ABOVE_BREAKOUT_COUNT": 15,
            "TIME.H20_DATE": "2026-09-28",
            "ACCOUNT.DRAWDOWN_PPM": 38_461,
            "ACCOUNT.ELIGIBLE_BANKROLL_NANO_USD": 205_000_000_000_000,
            "ACCOUNT.CASH_RESERVE_NANO_USD": 25_000_000_000_000,
            "ACCOUNT.EXISTING_GLD_DELTA_EXPOSURE_NANO_USD": 0,
        }
        for feature_id, expected in expected_scalars.items():
            with self.subTest(feature_id=feature_id):
                self.assertEqual(features[feature_id], expected)

        expected_contract = {
            "TIME.DTE_CALENDAR_DAYS_BY_CONTRACT": 42,
            "TIME.EXPIRY_SAFETY_GAP_CALENDAR_DAYS_BY_CONTRACT": 11,
            "QUOTE.SPREAD_NANO_USD_BY_CONTRACT": 20_000_000,
            "QUOTE.AGE_NS_BY_CONTRACT": 100_000_000,
            "QUOTE.TICK_NANO_USD_BY_CONTRACT": 10_000_000,
            "MODEL.IV_PPM_BY_CONTRACT": 300_229,
            "MODEL.DELTA_COARSE_PPM_BY_CONTRACT": 796_764,
            "MODEL.DELTA_FINE_PPM_BY_CONTRACT": 796_731,
            "LC0.DELTA_DISTANCE_PPM_BY_CONTRACT": 296_731,
            "LC0.ENTRY_DEBIT_NANO_USD_BY_CONTRACT": 1_776_650_000_000,
            "LC0.STRESSED_DEBIT_NANO_USD_BY_CONTRACT": 1_777_650_000_000,
            "LC0.MAX_LOSS_BASIS_NANO_USD_BY_CONTRACT": 1_777_650_000_000,
            "LC0.DELTA_NOTIONAL_NANO_USD_BY_CONTRACT": 15_934_620_000_000,
        }
        for feature_id, expected in expected_contract.items():
            with self.subTest(feature_id=feature_id):
                self.assertEqual(features[feature_id][first], expected)

        expected_pair = {
            "QUOTE.CROSS_LEG_RECEIVE_SKEW_NS_BY_BCS_PAIR": 0,
            "BCS0.LONG_DELTA_PPM_BY_PAIR": 796_731,
            "BCS0.SHORT_DELTA_PPM_BY_PAIR": 757_896,
            "BCS0.NET_DELTA_PPM_BY_PAIR": 38_835,
            "BCS0.WIDTH_NANO_USD_BY_PAIR": 2_500_000_000,
            "BCS0.NET_DEBIT_NANO_USD_BY_PAIR": 190_300_000_000,
            "BCS0.STRESSED_DEBIT_NANO_USD_BY_PAIR": 192_300_000_000,
            "BCS0.THEORETICAL_EXPIRY_CAP_NANO_USD_BY_PAIR": 250_000_000_000,
            "BCS0.MAX_LOSS_BASIS_NANO_USD_BY_PAIR": 192_300_000_000,
        }
        for feature_id, expected in expected_pair.items():
            with self.subTest(feature_id=feature_id):
                self.assertEqual(decoded_features[feature_id], expected)

        self.assertEqual(
            features["TIME.H20_SESSION_ID"],
            "XNYS:241:2026-09-28",
        )
        self.assertEqual(
            len(features["INTRADAY.CONFIRMATION_CLOSES_15_NANO_USD"]),
            15,
        )
        self.assertTrue(
            all(
                features["CONTRACT.QUALIFICATION_FACTS_BY_CONTRACT"][first].values()
            )
        )
        self.assertEqual(
            features["QUOTE.SIZE_BY_CONTRACT"][first],
            {"ask_size": 100, "bid_size": 100, "minimum_displayed_size": 100},
        )
        self.assertEqual(
            features["QUOTE.UNDERLYING_BBO_QUALITY_FACTS"],
            {
                "ask_size": 1000,
                "bid_size": 1000,
                "executability_facts": {
                    "age_within_limit": True,
                    "ask_size_meets_minimum": True,
                    "bid_size_meets_minimum": True,
                    "positive_non_crossed_market": True,
                },
                "minimum_displayed_size": 1000,
                "quote_age_ns": 100_000_000,
                "quote_flags": [],
                "spread_nano_usd": 20_000_000,
                "tick_nano_usd": 10_000_000,
            },
        )
        convergence = features["MODEL.CONVERGENCE_FACTS_BY_CONTRACT"][first]
        self.assertEqual(convergence["status"], "CONVERGED")
        self.assertEqual(
            convergence["runtime_fingerprint_sha256"],
            "300e6050a7d82087a4d31468a9df1a8711e406d656f2b87538df5f1dda7afc82",
        )
        self.assertEqual(
            convergence["run_sha256"],
            "89f474a27efff265514eb9886c2a0ad1d2e26f3b5389d6cdbac6546df6cd2228",
        )
        self.assertEqual(
            features["MODEL.INPUT_HASH_BY_CONTRACT"][first],
            convergence["input_sha256"],
        )
        self.assertEqual(
            decoded_features["BCS0.LIQUIDITY_FACTS_BY_PAIR"][
                "minimum_entry_size"
            ],
            100,
        )

        self.assertEqual(len(document["features"]), 45)
        self.assertEqual(document["bcs_pair_facts"]["pair_count"], 1)
        self.assertEqual(
            {
                materialized["feature_id"]: materialized["feature_hash"]
                for materialized in document["features"]
            },
            EXPECTED_FEATURE_HASHES,
        )
        for materialized in document["features"]:
            unsigned = {
                key: value
                for key, value in materialized.items()
                if key != "feature_hash"
            }
            self.assertEqual(
                materialized["feature_hash"], canonical_json_sha256(unsigned)
            )

    def test_confirmation_boundary_is_strictly_ten_of_fifteen(self) -> None:
        entry = deepcopy(self.entry)
        minute_container = entry["minute_bars"]
        self.assertIs(type(minute_container), dict)
        for bar in minute_container["bars"]:
            if 630 <= bar["minute_ending_ordinal"] <= 634:
                close = 199_400_000_000
                bar["open_nano_usd"] = close
                bar["high_nano_usd"] = close + 10_000_000
                bar["low_nano_usd"] = close - 10_000_000
                bar["close_nano_usd"] = close
        entry["content_hashes"]["minute_bars_sha256"] = canonical_json_sha256(
            minute_container
        )

        result = derive_required_technical_facts(entry)
        features = self._features(result.document)
        self.assertEqual(features["INTRADAY.ABOVE_BREAKOUT_COUNT"], 10)
        self.assertEqual(
            features["INTRADAY.CONFIRMATION_CLOSES_15_NANO_USD"].count(
                199_400_000_000
            ),
            5,
        )

    def test_expiry_safety_gap_preserves_zero_and_negative_boundaries(self) -> None:
        first = "OCC:GLD___261009C00185000"

        def result_for_gap(days_from_h20: int) -> dict[str, object]:
            entry = deepcopy(self.entry)
            snapshot = entry["option_snapshot"]
            self.assertIs(type(snapshot), dict)
            contract = snapshot["option_quotes"][0]["contract"]
            original_last_ns = int(contract["last_trading_utc_ns"])
            contract["last_trading_date"] = (
                "2026-09-28" if days_from_h20 == 0 else "2026-09-27"
            )
            contract["last_trading_utc_ns"] = original_last_ns - (
                (11 - days_from_h20) * 86_400_000_000_000
            )
            entry["content_hashes"]["option_snapshot_sha256"] = (
                canonical_json_sha256(snapshot)
            )
            pit = entry["pit_inputs"]
            self.assertIs(type(pit), dict)
            pit["borrow_available"] = False
            entry["content_hashes"]["pit_inputs_sha256"] = canonical_json_sha256(
                pit
            )
            return self._features(derive_required_technical_facts(entry).document)

        self.assertEqual(
            result_for_gap(0)[
                "TIME.EXPIRY_SAFETY_GAP_CALENDAR_DAYS_BY_CONTRACT"
            ][first],
            0,
        )
        self.assertEqual(
            result_for_gap(-1)[
                "TIME.EXPIRY_SAFETY_GAP_CALENDAR_DAYS_BY_CONTRACT"
            ][first],
            -1,
        )

    def test_receive_skew_limit_is_inclusive_then_fails_by_one_ns(self) -> None:
        entry = deepcopy(self.entry)
        snapshot = entry["option_snapshot"]
        self.assertIs(type(snapshot), dict)
        underlying = snapshot["underlying_bbo"]
        self.assertIs(type(underlying), dict)
        capture = snapshot["capture_utc_ns"]
        underlying["receive_utc_ns"] = capture - 1_100_000_000
        underlying["event_utc_ns"] = capture - 1_200_000_000
        entry["content_hashes"]["option_snapshot_sha256"] = canonical_json_sha256(
            snapshot
        )
        validate_entry_bundle(entry)

        underlying["receive_utc_ns"] -= 1
        underlying["event_utc_ns"] -= 1
        entry["content_hashes"]["option_snapshot_sha256"] = canonical_json_sha256(
            snapshot
        )
        with self.assertRaisesRegex(
            DataContractError, "ENTRY_CROSS_LEG_RECEIVE_SKEW_EXCEEDED"
        ):
            validate_entry_bundle(entry)

    def test_quote_age_limit_is_inclusive_then_fails_by_one_ns(self) -> None:
        entry = deepcopy(self.entry)
        snapshot = entry["option_snapshot"]
        rules = entry["rule_package"]
        self.assertIs(type(snapshot), dict)
        self.assertIs(type(rules), dict)
        capture = int(snapshot["capture_utc_ns"])
        limit = int(rules["max_quote_age_ns"])
        books = [
            snapshot["underlying_bbo"],
            *(
                quote["top_of_book"]
                for quote in snapshot["option_quotes"]
            ),
        ]
        for book in books:
            self.assertIs(type(book), dict)
            book["receive_utc_ns"] = capture - limit
            book["event_utc_ns"] = capture - limit
        entry["content_hashes"]["option_snapshot_sha256"] = (
            canonical_json_sha256(snapshot)
        )
        validate_entry_bundle(entry)

        for book in books:
            book["receive_utc_ns"] -= 1
            book["event_utc_ns"] -= 1
        entry["content_hashes"]["option_snapshot_sha256"] = (
            canonical_json_sha256(snapshot)
        )
        with self.assertRaisesRegex(DataContractError, "ENTRY_QUOTE_STALE"):
            validate_entry_bundle(entry)

    def test_thousand_contract_universe_uses_one_shared_pair_component_store(self) -> None:
        entry = deepcopy(self.entry)
        snapshot = entry["option_snapshot"]
        self.assertIs(type(snapshot), dict)
        template = snapshot["option_quotes"][0]
        quotes = []
        for index in range(1_000):
            quote = deepcopy(template)
            contract_id = f"SYNTH-C-{index:04d}"
            strike = 100_000_000_000 + index * 1_000_000_000
            quote["contract"]["contract_id"] = contract_id
            quote["contract"]["occ_symbol"] = (
                f"GLD261009C{strike // 1_000_000:08d}"
            )
            quote["contract"]["strike_nano_usd"] = strike
            quote["top_of_book"]["instrument_id"] = contract_id
            quotes.append(quote)
        snapshot["option_quotes"] = quotes
        entry["content_hashes"]["option_snapshot_sha256"] = canonical_json_sha256(
            snapshot
        )
        pit_inputs = entry["pit_inputs"]
        self.assertIs(type(pit_inputs), dict)
        pit_inputs["borrow_available"] = False
        entry["content_hashes"]["pit_inputs_sha256"] = canonical_json_sha256(
            pit_inputs
        )

        result = derive_required_technical_facts(entry)
        document = result.document
        self.assertEqual(len(document["contract_facts"]), 1_000)
        self.assertEqual(document["bcs_pair_facts"]["pair_count"], 499_500)
        self.assertEqual(
            document["bcs_pair_facts"]["component_store"]["schema_version"],
            "BCS_PAIR_COMPONENT_STORE_V1",
        )
        self.assertLess(len(result.canonical_bytes), 10_000_000)

        entry = validate_entry_bundle(entry).normalized_document
        snapshot = entry["option_snapshot"]
        self.assertIs(type(snapshot), dict)
        quotes = snapshot["option_quotes"]
        self.assertIs(type(quotes), list)
        template = quotes[0]
        for index in range(1_000, 1_200):
            quote = deepcopy(template)
            contract_id = f"SYNTH-C-{index:04d}"
            strike = 100_000_000_000 + index * 1_000_000_000
            quote["contract"]["contract_id"] = contract_id
            quote["contract"]["occ_symbol"] = (
                f"GLD261009C{strike // 1_000_000:08d}"
            )
            quote["contract"]["strike_nano_usd"] = strike
            quote["top_of_book"]["instrument_id"] = contract_id
            quotes.append(quote)
        snapshot["option_quotes"] = quotes
        entry["content_hashes"]["option_snapshot_sha256"] = canonical_json_sha256(
            snapshot
        )
        with self.assertRaisesRegex(
            TechnicalFactError,
            "TECHNICAL_OUTPUT_RESOURCE_LIMIT_EXCEEDED",
        ):
            derive_required_technical_facts(entry)


if __name__ == "__main__":
    unittest.main()
