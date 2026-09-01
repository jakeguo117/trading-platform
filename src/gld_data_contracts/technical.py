"""Deterministic ``EntryFactBundleV1`` technical-feature derivation.

This module materializes facts only.  It does not evaluate Gate A, mark a
carrier PASS/FAIL, select a winner, size a position, rank a preference, or
construct a DecisionResult.
"""

from __future__ import annotations

import ctypes
from dataclasses import InitVar, dataclass, field
from datetime import date
import json
from pathlib import Path
import platform
import re
import sys
from typing import Callable, Mapping

from gld_normalizer.errors import NormalizationError
from gld_research_core.crr_delta import (
    MODEL_ID,
    MODEL_SHA256,
    MODEL_SOURCE_ARTIFACT_SHA256,
    RUNTIME_FINGERPRINT_SHA256,
    CrrCallInputsV1,
    CrrPitInputsV1,
    compute_american_call_delta,
    is_verified_crr_delta_result,
)
from gld_simulation.canonical import canonical_json_bytes, canonical_json_sha256
from gld_simulation.errors import RawBundleError

from .contracts import (
    DATA_QUALIFIED,
    STRUCTURALLY_VALID_SYNTHETIC,
    EntryFactBundleV1,
)
from .validation import validate_entry_bundle


_CAPTURED_REFERENCE_CRR_COMPUTE = compute_american_call_delta
_CAPTURED_REFERENCE_CRR_VERIFY = is_verified_crr_delta_result
_CAPTURED_SYS_GETPROFILE = sys.getprofile
_CAPTURED_SYS_GETTRACE = sys.gettrace
_DAY_NS = 86_400_000_000_000
_PPM = 1_000_000
_CONFIRMATION_ORDINALS = tuple(range(630, 645))
_CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "GLD_TECHNICAL_FEATURE_CATALOG_V1.json"
)
_FEATURE_METADATA_KEYS = {
    "authority",
    "conflict_semantics",
    "feature_id",
    "formula_expression",
    "formula_id",
    "formula_version",
    "input_fields",
    "missing_semantics",
    "rounding",
    "scale",
    "sorting",
    "unit",
    "value_type",
    "window",
}
_CATALOG_TOP_LEVEL_KEYS = frozenset(
    {
        "catalog_id",
        "catalog_version",
        "decision_effect",
        "instrument_id",
        "materialized_feature_envelope",
        "global_rules",
        "prohibited_outputs",
        "features",
    }
)
_RUNTIME_SEAL = object()
_TECHNICAL_RESULT_SEAL = object()
_MAX_I64 = 2**63 - 1
TECHNICAL_ENGINE_VERSION = "GLD_REQUIRED_TECHNICAL_FACT_ENGINE_V1"
_NUMERIC_ENVIRONMENT_SCHEMA_VERSION = "TECHNICAL_C_NUMERIC_ENVIRONMENT_V1"
_COMPOSITE_RUN_BINDING_SCHEMA_VERSION = (
    "TECHNICAL_MODEL_COMPOSITE_RUN_BINDING_V1"
)
_SUPPORTED_FE_TONEAREST_BY_PLATFORM = {
    ("Darwin", "arm64"): 0x00000000,
    ("Darwin", "x86_64"): 0x0000,
}
EXPECTED_TECHNICAL_FEATURE_CATALOG_SHA256 = (
    "23d502bb82fdde083b6439b8ef2a73f0501fbe8111de3f74ff870bf5c1d42575"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_TECHNICAL_DOCUMENT_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "data_qualification_status",
        "data_qualification_reason_codes",
        "data_qualification_sha256",
        "trusted_source_receipt_set_sha256",
        "scope",
        "underlying",
        "trading_date",
        "cutoff_utc_ns",
        "entry_bundle_sha256",
        "technical_feature_catalog_id",
        "technical_feature_catalog_sha256",
        "technical_engine_version",
        "rule_package_sha256",
        "model_package_sha256",
        "numeric_runtime_environment",
        "numeric_runtime_environment_sha256",
        "contract_facts",
        "bcs_pair_facts",
        "features",
        "research_features_included",
        "carrier_qualification_included",
        "decision_outputs_included",
    }
)
_FEATURE_MATERIALIZED_KEYS = frozenset(
    _FEATURE_METADATA_KEYS | {"value", "source_fact_hash", "feature_hash"}
)
_FORBIDDEN_TECHNICAL_OUTPUT_KEYS = frozenset(
    {
        "carrierfail",
        "carrierpass",
        "decisionresult",
        "finalquantity",
        "preference",
        "quantity",
        "signalstate",
        "winner",
    }
)
_MODEL_CONVERGENCE_FACT_KEYS = frozenset(
    {
        "status",
        "reason_code",
        "model_id",
        "model_sha256",
        "input_sha256",
        "runtime_fingerprint_sha256",
        "numeric_runtime_environment_sha256",
        "core_run_sha256",
        "run_sha256",
        "coarse_iv_ppm",
        "fine_iv_ppm",
        "coarse_delta_ppm",
        "fine_delta_ppm",
        "coarse_price_residual_nano_usd",
        "fine_price_residual_nano_usd",
    }
)
_MODEL_CONVERGENCE_NUMERIC_KEYS = frozenset(
    {
        "coarse_iv_ppm",
        "fine_iv_ppm",
        "coarse_delta_ppm",
        "fine_delta_ppm",
        "coarse_price_residual_nano_usd",
        "fine_price_residual_nano_usd",
    }
)
_FACTORED_PAIR_FEATURE_IDS = frozenset(
    {
        "QUOTE.CROSS_LEG_RECEIVE_SKEW_NS_BY_BCS_PAIR",
        "BCS0.LONG_DELTA_PPM_BY_PAIR",
        "BCS0.SHORT_DELTA_PPM_BY_PAIR",
        "BCS0.NET_DELTA_PPM_BY_PAIR",
        "BCS0.WIDTH_NANO_USD_BY_PAIR",
        "BCS0.NET_DEBIT_NANO_USD_BY_PAIR",
        "BCS0.STRESSED_DEBIT_NANO_USD_BY_PAIR",
        "BCS0.THEORETICAL_EXPIRY_CAP_NANO_USD_BY_PAIR",
        "BCS0.MAX_LOSS_BASIS_NANO_USD_BY_PAIR",
        "BCS0.LIQUIDITY_FACTS_BY_PAIR",
    }
)
_PAIR_COMPONENT_KEYS_BY_FEATURE: dict[str, tuple[str, ...]] = {
    "QUOTE.CROSS_LEG_RECEIVE_SKEW_NS_BY_BCS_PAIR": (
        "underlying_receive_utc_ns",
        "receive_utc_ns_by_contract",
    ),
    "BCS0.LONG_DELTA_PPM_BY_PAIR": ("fine_delta_ppm_by_contract",),
    "BCS0.SHORT_DELTA_PPM_BY_PAIR": ("fine_delta_ppm_by_contract",),
    "BCS0.NET_DELTA_PPM_BY_PAIR": ("fine_delta_ppm_by_contract",),
    "BCS0.WIDTH_NANO_USD_BY_PAIR": ("strike_nano_usd_by_contract",),
    "BCS0.NET_DEBIT_NANO_USD_BY_PAIR": (
        "ask_nano_usd_by_contract",
        "bid_nano_usd_by_contract",
        "multiplier_by_contract",
        "long_entry_fee_nano_usd_per_contract",
        "short_entry_fee_nano_usd_per_contract",
    ),
    "BCS0.STRESSED_DEBIT_NANO_USD_BY_PAIR": (
        "ask_nano_usd_by_contract",
        "bid_nano_usd_by_contract",
        "multiplier_by_contract",
        "long_entry_fee_nano_usd_per_contract",
        "short_entry_fee_nano_usd_per_contract",
        "tick_nano_usd_by_contract",
        "entry_stress_long_ticks",
        "entry_stress_short_ticks",
        "strike_nano_usd_by_contract",
    ),
    "BCS0.THEORETICAL_EXPIRY_CAP_NANO_USD_BY_PAIR": (
        "strike_nano_usd_by_contract",
        "multiplier_by_contract",
    ),
    "BCS0.MAX_LOSS_BASIS_NANO_USD_BY_PAIR": (
        "ask_nano_usd_by_contract",
        "bid_nano_usd_by_contract",
        "multiplier_by_contract",
        "long_entry_fee_nano_usd_per_contract",
        "short_entry_fee_nano_usd_per_contract",
        "tick_nano_usd_by_contract",
        "entry_stress_long_ticks",
        "entry_stress_short_ticks",
        "strike_nano_usd_by_contract",
    ),
    "BCS0.LIQUIDITY_FACTS_BY_PAIR": (
        "underlying_receive_utc_ns",
        "receive_utc_ns_by_contract",
        "ask_size_by_contract",
        "bid_size_by_contract",
        "spread_nano_usd_by_contract",
    ),
}
_PAIR_COMPONENT_MAP_KEYS = frozenset(
    {
        "ask_nano_usd_by_contract",
        "ask_size_by_contract",
        "bid_nano_usd_by_contract",
        "bid_size_by_contract",
        "fine_delta_ppm_by_contract",
        "multiplier_by_contract",
        "receive_utc_ns_by_contract",
        "spread_nano_usd_by_contract",
        "strike_nano_usd_by_contract",
        "tick_nano_usd_by_contract",
    }
)
_PAIR_COMPONENT_SCALAR_KEYS = frozenset(
    {
        "entry_stress_long_ticks",
        "entry_stress_short_ticks",
        "long_entry_fee_nano_usd_per_contract",
        "short_entry_fee_nano_usd_per_contract",
        "underlying_receive_utc_ns",
    }
)


class TechnicalFactError(ValueError):
    """Fail-closed derivation error with one stable ASCII reason code."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(reason_code if detail is None else f"{reason_code}: {detail}")


def _reject_forbidden_technical_outputs(value: object) -> None:
    if type(value) is dict:
        for key, item in value.items():
            normalized = "".join(
                character.lower() for character in key if character.isalnum()
            )
            if normalized in _FORBIDDEN_TECHNICAL_OUTPUT_KEYS:
                raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
            _reject_forbidden_technical_outputs(item)
    elif type(value) is list:
        for item in value:
            _reject_forbidden_technical_outputs(item)


def _validate_model_convergence_seal(
    *,
    features: list[dict[str, object]],
    contract_ids: list[str],
    numeric_runtime_environment_sha256: object,
) -> None:
    convergence_feature = next(
        (
            feature
            for feature in features
            if feature.get("feature_id")
            == "MODEL.CONVERGENCE_FACTS_BY_CONTRACT"
        ),
        None,
    )
    convergence_by_contract = (
        convergence_feature.get("value")
        if type(convergence_feature) is dict
        else None
    )
    if (
        type(numeric_runtime_environment_sha256) is not str
        or _SHA256_RE.fullmatch(numeric_runtime_environment_sha256) is None
        or type(convergence_by_contract) is not dict
        or sorted(convergence_by_contract) != contract_ids
    ):
        raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
    for contract_id in contract_ids:
        convergence = convergence_by_contract[contract_id]
        if (
            type(convergence) is not dict
            or frozenset(convergence) != _MODEL_CONVERGENCE_FACT_KEYS
            or convergence.get("model_id") != MODEL_ID
            or convergence.get("model_sha256") != MODEL_SHA256
            or convergence.get("numeric_runtime_environment_sha256")
            != numeric_runtime_environment_sha256
        ):
            raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
        status = convergence.get("status")
        reason_code = convergence.get("reason_code")
        input_sha256 = convergence.get("input_sha256")
        if status == "CONVERGED":
            core_run_sha256 = convergence.get("core_run_sha256")
            run_sha256 = convergence.get("run_sha256")
            runtime_fingerprint_sha256 = convergence.get(
                "runtime_fingerprint_sha256"
            )
            if (
                reason_code != "CONVERGED"
                or type(input_sha256) is not str
                or _SHA256_RE.fullmatch(input_sha256) is None
                or type(runtime_fingerprint_sha256) is not str
                or _SHA256_RE.fullmatch(runtime_fingerprint_sha256) is None
                or type(core_run_sha256) is not str
                or _SHA256_RE.fullmatch(core_run_sha256) is None
                or type(run_sha256) is not str
                or _SHA256_RE.fullmatch(run_sha256) is None
                or any(
                    type(convergence[key]) is not int
                    for key in _MODEL_CONVERGENCE_NUMERIC_KEYS
                )
                or not 100 <= convergence["coarse_iv_ppm"] <= 5_000_000
                or not 100 <= convergence["fine_iv_ppm"] <= 5_000_000
                or not 0 <= convergence["coarse_delta_ppm"] <= _PPM
                or not 0 <= convergence["fine_delta_ppm"] <= _PPM
                or not 0
                <= convergence["coarse_price_residual_nano_usd"]
                <= _MAX_I64
                or not 0
                <= convergence["fine_price_residual_nano_usd"]
                <= _MAX_I64
                or run_sha256
                != canonical_json_sha256(
                    {
                        "schema_version": (
                            _COMPOSITE_RUN_BINDING_SCHEMA_VERSION
                        ),
                        "core_run_sha256": core_run_sha256,
                        "numeric_runtime_environment_sha256": (
                            numeric_runtime_environment_sha256
                        ),
                    }
                )
            ):
                raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
        elif status == "NOT_CONVERGED":
            if (
                type(reason_code) is not str
                or not reason_code
                or reason_code == "CONVERGED"
                or (
                    input_sha256 is not None
                    and (
                        type(input_sha256) is not str
                        or _SHA256_RE.fullmatch(input_sha256) is None
                    )
                )
                or convergence.get("runtime_fingerprint_sha256") is not None
                or convergence.get("core_run_sha256") is not None
                or convergence.get("run_sha256") is not None
                or any(
                    convergence[key] is not None
                    for key in _MODEL_CONVERGENCE_NUMERIC_KEYS
                )
            ):
                raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
        else:
            raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")


@dataclass(frozen=True, slots=True)
class LocalCrrRuntimeV1:
    """Already-created local evaluator capability used by the pure derivation."""

    compute: Callable[..., object] = field(repr=False, compare=False)
    verify: Callable[[object], bool] = field(repr=False, compare=False)
    runtime_kind: str
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            self._seal is not _RUNTIME_SEAL
            or self.runtime_kind != "REFERENCE"
        ):
            raise TechnicalFactError("TECHNICAL_MODEL_RUNTIME_INVALID")
        if (
            self.compute is not _CAPTURED_REFERENCE_CRR_COMPUTE
            or self.verify is not _CAPTURED_REFERENCE_CRR_VERIFY
        ):
            raise TechnicalFactError(
                "TECHNICAL_MODEL_RUNTIME_IDENTITY_MISMATCH"
            )


@dataclass(frozen=True, slots=True)
class RequiredDailyTechnicalFactsV1:
    technical_facts_sha256: str
    canonical_bytes: bytes
    _seal: InitVar[object] = None

    @property
    def schema_version(self) -> str:
        return "REQUIRED_DAILY_TECHNICAL_FACTS_V1"

    def __post_init__(self, _seal: object) -> None:
        try:
            document = json.loads(self.canonical_bytes)
            encoded = canonical_json_bytes(document)
        except (TypeError, ValueError, UnicodeError) as error:
            raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID") from error
        if (
            _seal is not _TECHNICAL_RESULT_SEAL
            or type(document) is not dict
            or frozenset(document) != _TECHNICAL_DOCUMENT_KEYS
            or document.get("schema_version") != self.schema_version
            or document.get("scope") != "GLD_TECHNICAL_FACTS_ONLY_NO_DECISION"
            or document.get("underlying") != "GLD"
            or document.get("technical_engine_version")
            != TECHNICAL_ENGINE_VERSION
            or document.get("research_features_included") is not False
            or document.get("carrier_qualification_included") is not False
            or document.get("decision_outputs_included") is not False
            or document.get("classification") not in {
                "SYNTHETIC_ONLY",
                "PRODUCTION_CANDIDATE",
            }
            or (
                document.get("classification") == "SYNTHETIC_ONLY"
                and document.get("data_qualification_status")
                != STRUCTURALLY_VALID_SYNTHETIC
            )
            or (
                document.get("classification") == "PRODUCTION_CANDIDATE"
                and document.get("data_qualification_status") != DATA_QUALIFIED
            )
            or type(document.get("data_qualification_reason_codes")) is not list
            or any(
                type(item) is not str
                for item in document.get("data_qualification_reason_codes", [])
            )
            or document.get("data_qualification_reason_codes")
            != sorted(set(document.get("data_qualification_reason_codes", [])))
            or _SHA256_RE.fullmatch(
                str(document.get("data_qualification_sha256"))
            )
            is None
            or _SHA256_RE.fullmatch(
                str(document.get("trusted_source_receipt_set_sha256"))
            )
            is None
            or encoded != self.canonical_bytes
            or _SHA256_RE.fullmatch(self.technical_facts_sha256) is None
            or canonical_json_sha256(document) != self.technical_facts_sha256
        ):
            raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
        catalog, catalog_by_id = _load_catalog()
        features = document.get("features")
        contract_facts = document.get("contract_facts")
        pair_facts = document.get("bcs_pair_facts")
        numeric_environment = document.get("numeric_runtime_environment")
        if (
            document.get("technical_feature_catalog_sha256")
            != canonical_json_sha256(catalog)
            or type(features) is not list
            or type(contract_facts) is not list
            or type(pair_facts) is not dict
            or type(numeric_environment) is not dict
            or frozenset(numeric_environment)
            != {
                "schema_version",
                "c_fenv_rounding_mode",
                "c_fenv_rounding_value",
            }
            or numeric_environment.get("schema_version")
            != _NUMERIC_ENVIRONMENT_SCHEMA_VERSION
            or numeric_environment.get("c_fenv_rounding_mode")
            != "FE_TONEAREST"
            or type(numeric_environment.get("c_fenv_rounding_value")) is not int
            or numeric_environment.get("c_fenv_rounding_value") != 0
            or document.get("numeric_runtime_environment_sha256")
            != canonical_json_sha256(numeric_environment)
        ):
            raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
        feature_ids: list[str] = []
        for feature in features:
            if type(feature) is not dict or frozenset(feature) != _FEATURE_MATERIALIZED_KEYS:
                raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
            feature_id = feature.get("feature_id")
            metadata = catalog_by_id.get(feature_id) if type(feature_id) is str else None
            unsigned = {
                key: value for key, value in feature.items() if key != "feature_hash"
            }
            if (
                metadata is None
                or any(feature.get(key) != value for key, value in metadata.items())
                or _SHA256_RE.fullmatch(str(feature.get("source_fact_hash"))) is None
                or _SHA256_RE.fullmatch(str(feature.get("feature_hash"))) is None
                or canonical_json_sha256(unsigned) != feature.get("feature_hash")
            ):
                raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
            if feature_id in _FACTORED_PAIR_FEATURE_IDS:
                value = feature.get("value")
                if (
                    type(value) is not dict
                    or set(value)
                    != {
                        "representation",
                        "pair_relation_sha256",
                        "component_store_id",
                        "component_keys",
                        "component_projection_sha256",
                    }
                    or value.get("representation")
                    != "BCS_ALL_COMPATIBLE_PAIRS_FACTORED_V1"
                    or value.get("pair_relation_sha256")
                    != pair_facts.get("pair_relation_sha256")
                    or value.get("component_store_id")
                    != "BCS_PAIR_COMPONENT_STORE_V1"
                    or value.get("component_keys")
                    != list(_PAIR_COMPONENT_KEYS_BY_FEATURE[feature_id])
                    or _SHA256_RE.fullmatch(
                        str(value.get("component_projection_sha256"))
                    )
                    is None
                ):
                    raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
            feature_ids.append(feature_id)
        if feature_ids != sorted(catalog_by_id) or len(set(feature_ids)) != len(feature_ids):
            raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
        contract_ids: list[str] = []
        for fact in contract_facts:
            if (
                type(fact) is not dict
                or set(fact) != {"contract_id"}
                or type(fact["contract_id"]) is not str
            ):
                raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
            contract_ids.append(fact["contract_id"])
        if contract_ids != sorted(set(contract_ids)):
            raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
        _validate_model_convergence_seal(
            features=features,
            contract_ids=contract_ids,
            numeric_runtime_environment_sha256=document.get(
                "numeric_runtime_environment_sha256"
            ),
        )
        expected_pair_keys = {
            "schema_version",
            "representation",
            "compatibility_rule",
            "groups",
            "pair_count",
            "pair_relation_sha256",
            "component_store",
        }
        relation_unsigned = {
            key: value
            for key, value in pair_facts.items()
            if key not in {"pair_relation_sha256", "component_store"}
        }
        groups = pair_facts.get("groups")
        component_store = pair_facts.get("component_store")
        if (
            set(pair_facts) != expected_pair_keys
            or pair_facts.get("schema_version")
            != "BCS_COMPATIBLE_PAIR_RELATION_V1"
            or pair_facts.get("representation")
            != "BCS_ALL_COMPATIBLE_PAIRS_FACTORED_V1"
            or pair_facts.get("compatibility_rule")
            != (
                "SAME_EXPIRY_MULTIPLIER_DELIVERABLE_SHARES_CURRENCY_"
                "DELIVERABLE_AND_SHORT_STRIKE_GT_LONG_STRIKE"
            )
            or type(pair_facts.get("pair_count")) is not int
            or int(pair_facts.get("pair_count", -1)) < 0
            or type(groups) is not list
            or type(component_store) is not dict
            or canonical_json_sha256(relation_unsigned)
            != pair_facts.get("pair_relation_sha256")
        ):
            raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
        observed_group_contract_ids: list[str] = []
        observed_pair_count = 0
        for group in groups:
            if (
                type(group) is not dict
                or set(group)
                != {
                    "expiry_date",
                    "multiplier",
                    "deliverable_shares",
                    "currency",
                    "deliverable",
                    "contracts_by_strike",
                }
                or type(group.get("contracts_by_strike")) is not list
            ):
                raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
            contracts = group["contracts_by_strike"]
            previous_strike: int | None = None
            lower_count = 0
            equal_count = 0
            for item in contracts:
                if (
                    type(item) is not dict
                    or set(item) != {"contract_id", "strike_nano_usd"}
                    or type(item.get("contract_id")) is not str
                    or item["contract_id"] not in contract_ids
                    or type(item.get("strike_nano_usd")) is not int
                ):
                    raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
                strike = item["strike_nano_usd"]
                if previous_strike is not None and strike < previous_strike:
                    raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
                if previous_strike is None or strike != previous_strike:
                    observed_pair_count += lower_count * equal_count
                    lower_count += equal_count
                    equal_count = 1
                    previous_strike = strike
                else:
                    equal_count += 1
                observed_group_contract_ids.append(item["contract_id"])
            observed_pair_count += lower_count * equal_count
        if (
            sorted(observed_group_contract_ids) != contract_ids
            or len(set(observed_group_contract_ids))
            != len(observed_group_contract_ids)
            or observed_pair_count != pair_facts["pair_count"]
        ):
            raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
        store_unsigned = {
            key: value
            for key, value in component_store.items()
            if key != "component_store_sha256"
        }
        components = component_store.get("components")
        if (
            set(component_store)
            != {"schema_version", "components", "component_store_sha256"}
            or component_store.get("schema_version")
            != "BCS_PAIR_COMPONENT_STORE_V1"
            or type(components) is not dict
            or set(components)
            != _PAIR_COMPONENT_MAP_KEYS | _PAIR_COMPONENT_SCALAR_KEYS
            or canonical_json_sha256(store_unsigned)
            != component_store.get("component_store_sha256")
        ):
            raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
        for key in _PAIR_COMPONENT_MAP_KEYS:
            component_map = components[key]
            if type(component_map) is not dict or sorted(component_map) != contract_ids:
                raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
            for value in component_map.values():
                if key == "fine_delta_ppm_by_contract" and type(value) is dict:
                    if (
                        set(value) != {"missing_reason"}
                        or type(value["missing_reason"]) is not str
                    ):
                        raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
                elif type(value) is not int:
                    raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
        if any(type(components[key]) is not int for key in _PAIR_COMPONENT_SCALAR_KEYS):
            raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
        features_by_id = {item["feature_id"]: item for item in features}
        for feature_id, component_keys in _PAIR_COMPONENT_KEYS_BY_FEATURE.items():
            value = features_by_id[feature_id]["value"]
            projection = {key: components[key] for key in component_keys}
            if (
                canonical_json_sha256(projection)
                != value["component_projection_sha256"]
            ):
                raise TechnicalFactError("TECHNICAL_RESULT_SEAL_INVALID")
        _reject_forbidden_technical_outputs(document)

    @property
    def document(self) -> dict[str, object]:
        return json.loads(self.canonical_bytes)


def reference_crr_runtime() -> LocalCrrRuntimeV1:
    """Return the frozen reference evaluator without reading external state."""

    if (
        compute_american_call_delta is not _CAPTURED_REFERENCE_CRR_COMPUTE
        or is_verified_crr_delta_result is not _CAPTURED_REFERENCE_CRR_VERIFY
    ):
        raise TechnicalFactError("TECHNICAL_MODEL_RUNTIME_IDENTITY_MISMATCH")
    return LocalCrrRuntimeV1(
        compute=_CAPTURED_REFERENCE_CRR_COMPUTE,
        verify=_CAPTURED_REFERENCE_CRR_VERIFY,
        runtime_kind="REFERENCE",
        _seal=_RUNTIME_SEAL,
    )


def _read_live_c_fenv_rounding_value() -> int:
    """Read the calling thread's current C floating-point rounding mode."""

    process = ctypes.CDLL(None)
    fegetround = process.fegetround
    fegetround.argtypes = []
    fegetround.restype = ctypes.c_int
    return fegetround()


def _require_supported_numeric_environment() -> tuple[dict[str, object], str]:
    """Return the audited FE_TONEAREST receipt or fail closed.

    C ``FE_*`` values are ABI macros rather than portable numeric constants.
    V1 therefore supports only the explicitly audited macOS architectures and
    rejects every other platform instead of guessing its macro value.
    """

    try:
        if (
            sys.getprofile is not _CAPTURED_SYS_GETPROFILE
            or sys.gettrace is not _CAPTURED_SYS_GETTRACE
            or _CAPTURED_SYS_GETPROFILE() is not None
            or _CAPTURED_SYS_GETTRACE() is not None
        ):
            raise TechnicalFactError(
                "TECHNICAL_MODEL_RUNTIME_HOOK_UNSUPPORTED"
            )
        platform_key = (platform.system(), platform.machine())
        expected_value = _SUPPORTED_FE_TONEAREST_BY_PLATFORM[platform_key]
        actual_value = _read_live_c_fenv_rounding_value()
    except TechnicalFactError:
        raise
    except Exception as error:
        raise TechnicalFactError(
            "TECHNICAL_NUMERIC_ENVIRONMENT_UNSUPPORTED"
        ) from error
    if type(actual_value) is not int or actual_value != expected_value:
        raise TechnicalFactError("TECHNICAL_NUMERIC_ENVIRONMENT_UNSUPPORTED")
    receipt: dict[str, object] = {
        "schema_version": _NUMERIC_ENVIRONMENT_SCHEMA_VERSION,
        "c_fenv_rounding_mode": "FE_TONEAREST",
        "c_fenv_rounding_value": actual_value,
    }
    return receipt, canonical_json_sha256(receipt)


def _load_catalog() -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    try:
        value = json.loads(_CATALOG_PATH.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeError) as error:
        raise TechnicalFactError("TECHNICAL_CATALOG_UNAVAILABLE") from error
    if (
        type(value) is not dict
        or frozenset(value) != _CATALOG_TOP_LEVEL_KEYS
        or value.get("catalog_id") != "GLD_TECHNICAL_FEATURE_CATALOG_V1"
        or value.get("catalog_version") != 1
        or value.get("decision_effect")
        != "TECHNICAL_FACTS_ONLY_NO_CHANGE_TO_F"
        or value.get("instrument_id") != "GLD"
        or value.get("prohibited_outputs")
        != [
            "carrier_pass_fail",
            "decision_result",
            "final_quantity",
            "preference",
            "winner",
        ]
        or canonical_json_sha256(value)
        != EXPECTED_TECHNICAL_FEATURE_CATALOG_SHA256
    ):
        raise TechnicalFactError("TECHNICAL_CATALOG_INVALID")
    global_rules = value.get("global_rules")
    if type(global_rules) is not dict:
        raise TechnicalFactError("TECHNICAL_CATALOG_INVALID")
    resource_policy = global_rules.get("technical_derivation_resource_policy")
    if (
        type(resource_policy) is not dict
        or resource_policy.get("policy_id")
        != "GLD_TECHNICAL_LOCAL_REFERENCE_RESOURCE_POLICY_V1"
        or resource_policy.get("pair_representation")
        != "BCS_ALL_COMPATIBLE_PAIRS_FACTORED_V1"
        or resource_policy.get("explicit_contract_cardinality_limit")
        is not None
        or resource_policy.get("input_bound")
        != "CANONICAL_JSON_NODE_LIMIT_100000"
        or resource_policy.get("output_bound")
        != (
            "CANONICAL_JSON_NODE_LIMIT_100000_WITH_STABLE_"
            "TECHNICAL_OUTPUT_RESOURCE_LIMIT_EXCEEDED"
        )
        or resource_policy.get("pair_component_storage")
        != (
            "ONE_SHARED_BCS_PAIR_COMPONENT_STORE_V1_REFERENCED_BY_"
            "FEATURE_PROJECTION_HASH"
        )
        or global_rules.get("model_step_suites")
        != {
            "coarse_steps": [512, 513],
            "fine_steps": [1024, 1025],
            "suite_aggregation": (
                "ARITHMETIC_MEAN_THEN_HALF_EVEN_INTEGER_QUANTIZATION"
            ),
        }
        or global_rules.get("numeric_runtime_environment_policy")
        != {
            "accepted_rounding_mode": "FE_TONEAREST",
            "ambient_python_hook_policy": (
                "SYS_GETPROFILE_AND_SYS_GETTRACE_IDENTITIES_MUST_BE_"
                "IMPORT_CAPTURED_AND_BOTH_LIVE_VALUES_MUST_BE_NONE_AT_"
                "MODEL_ENTRY_AND_BEFORE_AND_AFTER_EACH_COMPUTE"
            ),
            "callable_identity_guard": (
                "IMPORT_CAPTURED_EXACT_COMPUTE_AND_VERIFY; "
                "PUBLIC_IMPORTED_GLOBALS_MUST_MATCH_BEFORE_RUNTIME_CREATION; "
                "SEALED_RUNTIME_REQUIRES_CAPTURED_IDENTITIES"
            ),
            "check_points": (
                "MODEL_ENTRY_AND_IMMEDIATELY_BEFORE_AND_AFTER_EACH_COMPUTE"
            ),
            "composite_run_binding_schema_version": (
                _COMPOSITE_RUN_BINDING_SCHEMA_VERSION
            ),
            "failure_reason_code": (
                "TECHNICAL_NUMERIC_ENVIRONMENT_UNSUPPORTED"
            ),
            "receipt_schema_version": _NUMERIC_ENVIRONMENT_SCHEMA_VERSION,
            "runtime_identity_failure_reason_code": (
                "TECHNICAL_MODEL_RUNTIME_IDENTITY_MISMATCH"
            ),
            "runtime_hook_failure_reason_code": (
                "TECHNICAL_MODEL_RUNTIME_HOOK_UNSUPPORTED"
            ),
            "supported_platform_abis": ["Darwin-arm64", "Darwin-x86_64"],
            "unsupported_platform_policy": (
                "FAIL_CLOSED_NO_FE_MACRO_VALUE_GUESSING"
            ),
        }
    ):
        raise TechnicalFactError("TECHNICAL_CATALOG_INVALID")
    features = value.get("features")
    if type(features) is not list or not features:
        raise TechnicalFactError("TECHNICAL_CATALOG_INVALID")
    by_id: dict[str, dict[str, object]] = {}
    for raw in features:
        if type(raw) is not dict or set(raw) != _FEATURE_METADATA_KEYS:
            raise TechnicalFactError("TECHNICAL_CATALOG_INVALID")
        feature_id = raw.get("feature_id")
        if type(feature_id) is not str or feature_id in by_id:
            raise TechnicalFactError("TECHNICAL_CATALOG_INVALID")
        by_id[feature_id] = raw
    # Reuse the production canonical encoder as an explicit float/type gate.
    canonical_json_bytes(value)
    return value, by_id


def _trunc_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise TechnicalFactError("TECHNICAL_DIVISOR_INVALID")
    if numerator >= 0:
        return numerator // denominator
    return -((-numerator) // denominator)


def _half_even_div_two(value: int) -> int:
    quotient, remainder = divmod(value, 2)
    if remainder and quotient % 2:
        return quotient + 1
    return quotient


def _date(value: object, reason_code: str) -> date:
    if type(value) is not str:
        raise TechnicalFactError(reason_code)
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise TechnicalFactError(reason_code) from error


def _feature(
    metadata: dict[str, object],
    *,
    value: object,
    source_facts: object,
) -> dict[str, object]:
    try:
        source_fact_hash = canonical_json_sha256(source_facts)
        unsigned = {
            **metadata,
            "value": value,
            "source_fact_hash": source_fact_hash,
        }
        return {**unsigned, "feature_hash": canonical_json_sha256(unsigned)}
    except RawBundleError as error:
        raise TechnicalFactError("TECHNICAL_VALUE_OUT_OF_RANGE") from error


def _rate_for_days(rate_curve: list[dict[str, object]], days: int) -> int:
    points = sorted(
        (
            (int(item["tenor_days"]), int(item["zero_rate_ppm"]))
            for item in rate_curve
        ),
        key=lambda item: item[0],
    )
    if not points:
        raise TechnicalFactError("TECHNICAL_RATE_CURVE_MISSING")
    if days <= points[0][0]:
        return points[0][1]
    if days >= points[-1][0]:
        return points[-1][1]
    for left, right in zip(points, points[1:], strict=False):
        if left[0] <= days <= right[0]:
            numerator = (right[1] - left[1]) * (days - left[0])
            return left[1] + _trunc_div(numerator, right[0] - left[0])
    raise TechnicalFactError("TECHNICAL_RATE_CURVE_MISSING")


def _missing(reason_code: str) -> dict[str, str]:
    return {"missing_reason": reason_code}


def _exact_result_int(
    value: object,
    *,
    minimum: int,
    maximum: int,
    contract_id: str,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise TechnicalFactError(
            "TECHNICAL_MODEL_RESULT_RANGE_INVALID", contract_id
        )
    return value


def _exact_result_sha256(value: object, *, contract_id: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise TechnicalFactError(
            "TECHNICAL_MODEL_RESULT_BINDING_MISMATCH",
            contract_id,
        )
    return value


def _validated_model_values(
    computed: object,
    *,
    contract_id: str,
) -> dict[str, int]:
    iv_ppm = _exact_result_int(
        getattr(computed, "iv_ppm", None),
        minimum=100,
        maximum=5_000_000,
        contract_id=contract_id,
    )
    coarse_iv_ppm = _exact_result_int(
        getattr(computed, "coarse_iv_ppm", None),
        minimum=100,
        maximum=5_000_000,
        contract_id=contract_id,
    )
    fine_iv_ppm = _exact_result_int(
        getattr(computed, "fine_iv_ppm", None),
        minimum=100,
        maximum=5_000_000,
        contract_id=contract_id,
    )
    delta_ppm = _exact_result_int(
        getattr(computed, "delta_ppm", None),
        minimum=0,
        maximum=_PPM,
        contract_id=contract_id,
    )
    coarse_delta_ppm = _exact_result_int(
        getattr(computed, "coarse_delta_ppm", None),
        minimum=0,
        maximum=_PPM,
        contract_id=contract_id,
    )
    fine_delta_ppm = _exact_result_int(
        getattr(computed, "fine_delta_ppm", None),
        minimum=0,
        maximum=_PPM,
        contract_id=contract_id,
    )
    coarse_residual = _exact_result_int(
        getattr(computed, "coarse_price_residual_nano_usd", None),
        minimum=0,
        maximum=_MAX_I64,
        contract_id=contract_id,
    )
    fine_residual = _exact_result_int(
        getattr(computed, "fine_price_residual_nano_usd", None),
        minimum=0,
        maximum=_MAX_I64,
        contract_id=contract_id,
    )
    if (
        iv_ppm != fine_iv_ppm
        or delta_ppm != fine_delta_ppm
        or abs(coarse_iv_ppm - fine_iv_ppm) > 100
        or abs(coarse_delta_ppm - fine_delta_ppm) > 500
    ):
        raise TechnicalFactError(
            "TECHNICAL_MODEL_RESULT_CONVERGENCE_INVALID", contract_id
        )
    return {
        "iv_ppm": iv_ppm,
        "coarse_iv_ppm": coarse_iv_ppm,
        "fine_iv_ppm": fine_iv_ppm,
        "delta_ppm": delta_ppm,
        "coarse_delta_ppm": coarse_delta_ppm,
        "fine_delta_ppm": fine_delta_ppm,
        "coarse_price_residual_nano_usd": coarse_residual,
        "fine_price_residual_nano_usd": fine_residual,
    }


def _model_projection(
    *,
    entry: dict[str, object],
    option_quotes: list[dict[str, object]],
    runtime: LocalCrrRuntimeV1,
) -> tuple[dict[str, dict[str, object]], dict[str, object], str]:
    numeric_environment, numeric_environment_sha256 = (
        _require_supported_numeric_environment()
    )
    model = entry["model_package"]
    if type(model) is not dict or (
        model.get("formula_id") != MODEL_ID
        or model.get("version") != MODEL_SHA256
        or model.get("source_code_sha256") != MODEL_SOURCE_ARTIFACT_SHA256
        or model.get("runtime_fingerprint_sha256")
        != RUNTIME_FINGERPRINT_SHA256
        or model.get("coarse_steps") != 512
        or model.get("fine_steps") != 1024
        or model.get("iv_iterations") != 32
        or model.get("rounding_mode") != "HALF_EVEN_INTEGER"
    ):
        raise TechnicalFactError("TECHNICAL_MODEL_PACKAGE_UNSUPPORTED")
    snapshot = entry["option_snapshot"]
    pit = entry["pit_inputs"]
    if type(snapshot) is not dict or type(pit) is not dict:
        raise TechnicalFactError("TECHNICAL_MODEL_INPUT_INVALID")
    underlying = snapshot["underlying_bbo"]
    if type(underlying) is not dict:
        raise TechnicalFactError("TECHNICAL_MODEL_INPUT_INVALID")
    capture = int(snapshot["capture_utc_ns"])
    spot_mid = _half_even_div_two(
        int(underlying["bid_nano_usd"]) + int(underlying["ask_nano_usd"])
    )
    rate_curve = pit["rate_curve"]
    if type(rate_curve) is not list:
        raise TechnicalFactError("TECHNICAL_RATE_CURVE_MISSING")
    snapshot_sha256 = canonical_json_sha256(snapshot)
    underlying_sha256 = canonical_json_sha256(underlying)
    result: dict[str, dict[str, object]] = {}
    for quote in option_quotes:
        contract = quote["contract"]
        book = quote["top_of_book"]
        if type(contract) is not dict or type(book) is not dict:
            raise TechnicalFactError("TECHNICAL_MODEL_INPUT_INVALID")
        contract_id = str(contract["contract_id"])
        input_sha256: str | None = None
        reason_code: str | None = None
        try:
            if pit.get("borrow_available") is not True:
                raise NormalizationError("BORROW_FACT_UNAVAILABLE")
            expiry = int(contract["expiry_utc_ns"])
            if not (
                contract.get("underlying") == "GLD"
                and contract.get("option_type") == "CALL"
                and contract.get("exercise_style") == "AMERICAN"
                and int(contract["strike_nano_usd"]) > 0
                and int(contract["activation_utc_ns"]) <= capture
                and expiry > capture
            ):
                raise NormalizationError("CONTRACT_NOT_MODEL_ELIGIBLE")
            maturity_days = max(1, (expiry - capture + _DAY_NS - 1) // _DAY_NS)
            risk_free_rate_ppm = _rate_for_days(rate_curve, maturity_days)
            local_pit = CrrPitInputsV1(
                as_of_utc_ns=capture,
                risk_free_rate_ppm=risk_free_rate_ppm,
                expense_yield_ppm=(
                    int(pit["expense_yield_ppm"])
                    + int(pit["distribution_yield_ppm"])
                ),
                borrow_yield_ppm=int(pit["borrow_rate_ppm"]),
                rate_curve_sha256=canonical_json_sha256(rate_curve),
                distribution_assumption_sha256=canonical_json_sha256(
                    {
                        "as_of_utc_ns": pit["as_of_utc_ns"],
                        "distribution_yield_ppm": pit["distribution_yield_ppm"],
                    }
                ),
                borrow_assumption_sha256=canonical_json_sha256(
                    {
                        "as_of_utc_ns": pit["as_of_utc_ns"],
                        "borrow_available": pit["borrow_available"],
                        "borrow_rate_ppm": pit["borrow_rate_ppm"],
                    }
                ),
            )
            call_inputs = CrrCallInputsV1(
                option_snapshot_sha256=snapshot_sha256,
                contract_id=contract_id,
                contract_sha256=canonical_json_sha256(contract),
                underlying_top_sha256=underlying_sha256,
                option_quote_sha256=canonical_json_sha256(quote),
                pit_inputs=local_pit,
                spot_nano_usd=spot_mid,
                strike_nano_usd=int(contract["strike_nano_usd"]),
                option_mid_nano_usd=_half_even_div_two(
                    int(book["bid_nano_usd"]) + int(book["ask_nano_usd"])
                ),
                tick_nano_usd=int(book["tick_nano_usd"]),
                valuation_utc_ns=capture,
                expiry_utc_ns=expiry,
            )
            input_sha256 = call_inputs.input_sha256
            _require_supported_numeric_environment()
            try:
                computed = runtime.compute(
                    call_inputs,
                    expected_model_sha256=MODEL_SHA256,
                )
            finally:
                _require_supported_numeric_environment()
            if runtime.verify(computed) is not True:
                raise TechnicalFactError("TECHNICAL_MODEL_RESULT_UNVERIFIED", contract_id)
            if (
                getattr(computed, "model_id", None) != MODEL_ID
                or
                getattr(computed, "model_sha256", None) != MODEL_SHA256
                or getattr(computed, "input_sha256", None) != input_sha256
                or getattr(computed, "contract_id", None) != contract_id
            ):
                raise TechnicalFactError("TECHNICAL_MODEL_RESULT_BINDING_MISMATCH", contract_id)
            numeric = _validated_model_values(
                computed,
                contract_id=contract_id,
            )
            core_run_sha256 = _exact_result_sha256(
                getattr(computed, "run_sha256", None),
                contract_id=contract_id,
            )
            composite_run_binding = {
                "schema_version": _COMPOSITE_RUN_BINDING_SCHEMA_VERSION,
                "core_run_sha256": core_run_sha256,
                "numeric_runtime_environment_sha256": (
                    numeric_environment_sha256
                ),
            }
            result[contract_id] = {
                "status": "CONVERGED",
                "reason_code": "CONVERGED",
                "model_id": MODEL_ID,
                "model_sha256": MODEL_SHA256,
                "input_sha256": input_sha256,
                "runtime_fingerprint_sha256": _exact_result_sha256(
                    getattr(computed, "runtime_fingerprint_sha256", None),
                    contract_id=contract_id,
                ),
                "numeric_runtime_environment_sha256": (
                    numeric_environment_sha256
                ),
                "core_run_sha256": core_run_sha256,
                "run_sha256": canonical_json_sha256(composite_run_binding),
                **numeric,
            }
        except NormalizationError as error:
            reason_code = error.reason_code
        if reason_code is not None:
            result[contract_id] = {
                "status": "NOT_CONVERGED",
                "reason_code": reason_code,
                "model_id": MODEL_ID,
                "model_sha256": MODEL_SHA256,
                "input_sha256": input_sha256,
                "runtime_fingerprint_sha256": None,
                "numeric_runtime_environment_sha256": (
                    numeric_environment_sha256
                ),
                "core_run_sha256": None,
                "run_sha256": None,
                "iv_ppm": None,
                "coarse_iv_ppm": None,
                "fine_iv_ppm": None,
                "delta_ppm": None,
                "coarse_delta_ppm": None,
                "fine_delta_ppm": None,
                "coarse_price_residual_nano_usd": None,
                "fine_price_residual_nano_usd": None,
            }
    return result, numeric_environment, numeric_environment_sha256


def derive_required_technical_facts(
    source: EntryFactBundleV1 | Mapping[str, object],
) -> RequiredDailyTechnicalFactsV1:
    """Derive canonical V1 technical facts from one validated raw bundle."""

    if type(source) is EntryFactBundleV1:
        revalidated = validate_entry_bundle(source.normalized_document)
        if (
            source.entry_bundle_sha256 != revalidated.entry_bundle_sha256
            or source.canonical_bytes != revalidated.canonical_bytes
        ):
            raise TechnicalFactError("TECHNICAL_ENTRY_BUNDLE_SEAL_INVALID")
        validated = revalidated
    else:
        validated = validate_entry_bundle(dict(source))
    if (
        validated.classification == "SYNTHETIC_ONLY"
        and validated.qualification.status != STRUCTURALLY_VALID_SYNTHETIC
    ):
        raise TechnicalFactError("TECHNICAL_ENTRY_QUALIFICATION_INVALID")
    if validated.classification == "PRODUCTION_CANDIDATE":
        raise TechnicalFactError("TECHNICAL_ENTRY_DATA_NOT_QUALIFIED")
    entry = validated.normalized_document
    catalog, metadata_by_id = _load_catalog()
    runtime = reference_crr_runtime()

    daily_container = entry["daily_bars"]
    minute_container = entry["minute_bars"]
    calendar_container = entry["calendar"]
    snapshot = entry["option_snapshot"]
    account = entry["account_snapshot_a"]
    fees = entry["fee_schedule"]
    rules = entry["rule_package"]
    if any(
        type(value) is not dict
        for value in (
            daily_container,
            minute_container,
            calendar_container,
            snapshot,
            account,
            fees,
            rules,
        )
    ):
        raise TechnicalFactError("TECHNICAL_ENTRY_BUNDLE_INVALID")
    daily_container = dict(daily_container)
    minute_container = dict(minute_container)
    calendar_container = dict(calendar_container)
    snapshot = dict(snapshot)
    account = dict(account)
    fees = dict(fees)
    rules = dict(rules)
    daily = list(daily_container["bars"])
    minutes = list(minute_container["bars"])
    sessions = list(calendar_container["sessions"])
    option_quotes = list(snapshot["option_quotes"])
    if len(daily) < 220:
        raise TechnicalFactError("TECHNICAL_DAILY_HISTORY_MISSING")
    closes = [int(item["close_nano_usd"]) for item in daily]
    highs = [int(item["high_nano_usd"]) for item in daily]
    prior_close = closes[-1]
    sma50 = sum(closes[-50:]) // 50
    sma200 = sum(closes[-200:]) // 200
    old_sma50 = sum(closes[-70:-20]) // 50
    if old_sma50 <= 0:
        raise TechnicalFactError("TECHNICAL_SMA_INPUT_INVALID")
    slope = ((sma50 - old_sma50) * _PPM) // (old_sma50 * 20)
    breakout = max(highs[-20:])

    minute_by_ordinal = {int(item["minute_ending_ordinal"]): item for item in minutes}
    if set(_CONFIRMATION_ORDINALS) - set(minute_by_ordinal):
        raise TechnicalFactError("TECHNICAL_CONFIRMATION_WINDOW_MISSING")
    confirmation = [
        int(minute_by_ordinal[ordinal]["close_nano_usd"])
        for ordinal in _CONFIRMATION_ORDINALS
    ]
    close_1044 = confirmation[-1]
    breakout_distance_ppm = ((close_1044 - breakout) * _PPM) // breakout
    above_count = sum(value > breakout for value in confirmation)

    decision_ordinal = int(calendar_container["decision_session_ordinal"])
    forward = [
        item for item in sessions if int(item["session_ordinal"]) > decision_ordinal
    ]
    if len(forward) < 20:
        raise TechnicalFactError("TECHNICAL_H20_CALENDAR_MISSING")
    h20 = forward[19]
    h20_date = _date(h20["trading_date"], "TECHNICAL_H20_DATE_INVALID")
    trading_date = _date(entry["trading_date"], "TECHNICAL_TRADING_DATE_INVALID")

    quotes_by_id = {
        str(item["contract"]["contract_id"]): item for item in option_quotes
    }
    option_quotes = [quotes_by_id[key] for key in sorted(quotes_by_id)]
    capture = int(snapshot["capture_utc_ns"])
    underlying_book = snapshot["underlying_bbo"]
    if type(underlying_book) is not dict:
        raise TechnicalFactError("TECHNICAL_UNDERLYING_QUOTE_INVALID")
    underlying_receive = int(underlying_book["receive_utc_ns"])
    underlying_mid = _half_even_div_two(
        int(underlying_book["bid_nano_usd"])
        + int(underlying_book["ask_nano_usd"])
    )
    underlying_spread = int(underlying_book["ask_nano_usd"]) - int(
        underlying_book["bid_nano_usd"]
    )
    underlying_age = capture - underlying_receive
    underlying_quality = {
        "spread_nano_usd": underlying_spread,
        "quote_age_ns": underlying_age,
        "bid_size": int(underlying_book["bid_size"]),
        "ask_size": int(underlying_book["ask_size"]),
        "minimum_displayed_size": min(
            int(underlying_book["bid_size"]),
            int(underlying_book["ask_size"]),
        ),
        "tick_nano_usd": int(underlying_book["tick_nano_usd"]),
        "quote_flags": list(underlying_book["flags"]),
        "executability_facts": {
            "age_within_limit": underlying_age
            <= int(rules["max_quote_age_ns"]),
            "ask_size_meets_minimum": int(underlying_book["ask_size"])
            >= int(rules["min_quote_size"]),
            "bid_size_meets_minimum": int(underlying_book["bid_size"])
            >= int(rules["min_quote_size"]),
            "positive_non_crossed_market": int(
                underlying_book["ask_nano_usd"]
            )
            > int(underlying_book["bid_nano_usd"])
            > 0,
        },
    }

    dte: dict[str, int] = {}
    expiry_gap: dict[str, int] = {}
    contract_qualification: dict[str, dict[str, bool]] = {}
    spreads: dict[str, int] = {}
    ages: dict[str, int] = {}
    sizes: dict[str, dict[str, int]] = {}
    ticks: dict[str, int] = {}
    executability: dict[str, dict[str, object]] = {}
    by_contract: dict[str, tuple[dict[str, object], dict[str, object]]] = {}
    for quote in option_quotes:
        contract = dict(quote["contract"])
        book = dict(quote["top_of_book"])
        contract_id = str(contract["contract_id"])
        by_contract[contract_id] = (contract, book)
        expiry_date = _date(contract["expiry_date"], "TECHNICAL_EXPIRY_DATE_INVALID")
        last_trading_date = _date(
            contract["last_trading_date"],
            "TECHNICAL_LAST_TRADING_DATE_INVALID",
        )
        dte[contract_id] = (expiry_date - trading_date).days
        expiry_gap[contract_id] = (last_trading_date - h20_date).days
        spread = int(book["ask_nano_usd"]) - int(book["bid_nano_usd"])
        age = capture - int(book["receive_utc_ns"])
        skew = abs(int(book["receive_utc_ns"]) - underlying_receive)
        spreads[contract_id] = spread
        ages[contract_id] = age
        sizes[contract_id] = {
            "ask_size": int(book["ask_size"]),
            "bid_size": int(book["bid_size"]),
            "minimum_displayed_size": min(
                int(book["ask_size"]), int(book["bid_size"])
            ),
        }
        ticks[contract_id] = int(book["tick_nano_usd"])
        qualification = {
            "active_at_capture": int(contract["activation_utc_ns"]) <= capture,
            "american_exercise": contract["exercise_style"] == "AMERICAN",
            "call_option": contract["option_type"] == "CALL",
            "currency_usd": contract["currency"] == "USD",
            "identity_bound_to_quote": book["instrument_id"] == contract_id,
            "last_trading_after_capture": int(contract["last_trading_utc_ns"]) > capture,
            "positive_multiplier": int(contract["multiplier"]) > 0,
            "positive_strike": int(contract["strike_nano_usd"]) > 0,
            "standard_unadjusted": contract["standard_unadjusted"] is True,
            "tick_identity_consistent": int(contract["tick_nano_usd"]) == int(book["tick_nano_usd"]),
            "underlying_gld": contract["underlying"] == "GLD",
        }
        contract_qualification[contract_id] = qualification
        executability[contract_id] = {
            "age_within_limit": age <= int(rules["max_quote_age_ns"]),
            "ask_size_meets_minimum": int(book["ask_size"]) >= int(rules["min_quote_size"]),
            "bid_size_meets_minimum": int(book["bid_size"]) >= int(rules["min_quote_size"]),
            "cross_leg_receive_skew_ns": skew,
            "cross_leg_receive_skew_within_limit": skew <= int(rules["max_cross_leg_receive_skew_ns"]),
            "positive_non_crossed_market": int(book["ask_nano_usd"]) > int(book["bid_nano_usd"]) > 0,
            "quote_flags": list(book["flags"]),
        }

    structural_groups: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for contract_id, (contract, _book_value) in by_contract.items():
        key = (
            contract["expiry_date"],
            contract["multiplier"],
            contract["deliverable_shares"],
            contract["currency"],
            contract["deliverable"],
        )
        structural_groups.setdefault(key, []).append(
            {
                "contract_id": contract_id,
                "strike_nano_usd": int(contract["strike_nano_usd"]),
            }
        )
    relation_groups: list[dict[str, object]] = []
    pair_count = 0
    for key in sorted(structural_groups, key=lambda item: tuple(str(value) for value in item)):
        contracts = sorted(
            structural_groups[key],
            key=lambda item: (item["strike_nano_usd"], item["contract_id"]),
        )
        lower_count = 0
        index = 0
        while index < len(contracts):
            strike = contracts[index]["strike_nano_usd"]
            end = index
            while end < len(contracts) and contracts[end]["strike_nano_usd"] == strike:
                end += 1
            equal_count = end - index
            pair_count += lower_count * equal_count
            lower_count += equal_count
            index = end
        relation_groups.append(
            {
                "expiry_date": key[0],
                "multiplier": key[1],
                "deliverable_shares": key[2],
                "currency": key[3],
                "deliverable": key[4],
                "contracts_by_strike": contracts,
            }
        )
    pair_relation_unsigned: dict[str, object] = {
        "schema_version": "BCS_COMPATIBLE_PAIR_RELATION_V1",
        "representation": "BCS_ALL_COMPATIBLE_PAIRS_FACTORED_V1",
        "compatibility_rule": (
            "SAME_EXPIRY_MULTIPLIER_DELIVERABLE_SHARES_CURRENCY_"
            "DELIVERABLE_AND_SHORT_STRIKE_GT_LONG_STRIKE"
        ),
        "groups": relation_groups,
        "pair_count": pair_count,
    }
    pair_relation = {
        **pair_relation_unsigned,
        "pair_relation_sha256": canonical_json_sha256(pair_relation_unsigned),
    }

    (
        model_projection,
        numeric_runtime_environment,
        numeric_runtime_environment_sha256,
    ) = _model_projection(
        entry=entry,
        option_quotes=option_quotes,
        runtime=runtime,
    )
    model_iv: dict[str, object] = {}
    model_coarse_delta: dict[str, object] = {}
    model_fine_delta: dict[str, object] = {}
    model_convergence: dict[str, dict[str, object]] = {}
    model_inputs: dict[str, object] = {}
    for contract_id in sorted(model_projection):
        projected = model_projection[contract_id]
        reason = str(projected["reason_code"])
        converged = projected["status"] == "CONVERGED"
        model_iv[contract_id] = projected["iv_ppm"] if converged else _missing(reason)
        model_coarse_delta[contract_id] = projected["coarse_delta_ppm"] if converged else _missing(reason)
        model_fine_delta[contract_id] = projected["fine_delta_ppm"] if converged else _missing(reason)
        model_inputs[contract_id] = (
            projected["input_sha256"]
            if projected["input_sha256"] is not None
            else _missing(reason)
        )
        model_convergence[contract_id] = {
            key: projected[key]
            for key in (
                "status",
                "reason_code",
                "model_id",
                "model_sha256",
                "input_sha256",
                "runtime_fingerprint_sha256",
                "numeric_runtime_environment_sha256",
                "core_run_sha256",
                "run_sha256",
                "coarse_iv_ppm",
                "fine_iv_ppm",
                "coarse_delta_ppm",
                "fine_delta_ppm",
                "coarse_price_residual_nano_usd",
                "fine_price_residual_nano_usd",
            )
        }

    lc_delta_distance: dict[str, object] = {}
    lc_entry_debit: dict[str, int] = {}
    lc_stressed_debit: dict[str, int] = {}
    lc_max_loss: dict[str, int] = {}
    lc_delta_notional: dict[str, object] = {}
    lc_liquidity: dict[str, dict[str, object]] = {}
    for contract_id in sorted(by_contract):
        contract, book = by_contract[contract_id]
        multiplier = int(contract["multiplier"])
        entry_debit = int(book["ask_nano_usd"]) * multiplier + int(
            fees["long_entry_fee_nano_usd_per_contract"]
        )
        stressed = (
            int(book["ask_nano_usd"])
            + int(book["tick_nano_usd"]) * int(rules["entry_stress_long_ticks"])
        ) * multiplier + int(fees["long_entry_fee_nano_usd_per_contract"])
        lc_entry_debit[contract_id] = entry_debit
        lc_stressed_debit[contract_id] = stressed
        lc_max_loss[contract_id] = stressed
        projected = model_projection[contract_id]
        if projected["status"] == "CONVERGED":
            fine_delta = int(projected["fine_delta_ppm"])
            lc_delta_distance[contract_id] = abs(fine_delta - 500_000)
            lc_delta_notional[contract_id] = _trunc_div(
                underlying_mid * multiplier * fine_delta,
                _PPM,
            )
        else:
            reason = str(projected["reason_code"])
            lc_delta_distance[contract_id] = _missing(reason)
            lc_delta_notional[contract_id] = _missing(reason)
        lc_liquidity[contract_id] = {
            "ask_size": int(book["ask_size"]),
            "quote_age_ns": ages[contract_id],
            "quote_flags": list(book["flags"]),
            "spread_nano_usd": spreads[contract_id],
            "tick_nano_usd": ticks[contract_id],
            "executability_facts": executability[contract_id],
        }

    strike_by_contract = {
        contract_id: int(contract["strike_nano_usd"])
        for contract_id, (contract, _book) in sorted(by_contract.items())
    }
    multiplier_by_contract = {
        contract_id: int(contract["multiplier"])
        for contract_id, (contract, _book) in sorted(by_contract.items())
    }
    ask_by_contract = {
        contract_id: int(book["ask_nano_usd"])
        for contract_id, (_contract, book) in sorted(by_contract.items())
    }
    bid_by_contract = {
        contract_id: int(book["bid_nano_usd"])
        for contract_id, (_contract, book) in sorted(by_contract.items())
    }
    receive_by_contract = {
        contract_id: int(book["receive_utc_ns"])
        for contract_id, (_contract, book) in sorted(by_contract.items())
    }
    ask_size_by_contract = {
        contract_id: int(book["ask_size"])
        for contract_id, (_contract, book) in sorted(by_contract.items())
    }
    bid_size_by_contract = {
        contract_id: int(book["bid_size"])
        for contract_id, (_contract, book) in sorted(by_contract.items())
    }
    pair_components: dict[str, object] = {
        "ask_nano_usd_by_contract": ask_by_contract,
        "ask_size_by_contract": ask_size_by_contract,
        "bid_nano_usd_by_contract": bid_by_contract,
        "bid_size_by_contract": bid_size_by_contract,
        "fine_delta_ppm_by_contract": model_fine_delta,
        "multiplier_by_contract": multiplier_by_contract,
        "receive_utc_ns_by_contract": receive_by_contract,
        "spread_nano_usd_by_contract": spreads,
        "strike_nano_usd_by_contract": strike_by_contract,
        "tick_nano_usd_by_contract": ticks,
        "entry_stress_long_ticks": rules["entry_stress_long_ticks"],
        "entry_stress_short_ticks": rules["entry_stress_short_ticks"],
        "long_entry_fee_nano_usd_per_contract": fees[
            "long_entry_fee_nano_usd_per_contract"
        ],
        "short_entry_fee_nano_usd_per_contract": fees[
            "short_entry_fee_nano_usd_per_contract"
        ],
        "underlying_receive_utc_ns": underlying_receive,
    }
    component_store_unsigned: dict[str, object] = {
        "schema_version": "BCS_PAIR_COMPONENT_STORE_V1",
        "components": pair_components,
    }
    component_store = {
        **component_store_unsigned,
        "component_store_sha256": canonical_json_sha256(
            component_store_unsigned
        ),
    }
    pair_relation = {**pair_relation, "component_store": component_store}
    pair_relation_sha256 = str(pair_relation["pair_relation_sha256"])

    def component_projection(component_keys: tuple[str, ...]) -> dict[str, object]:
        return {key: pair_components[key] for key in component_keys}

    def factored_pair_value(feature_id: str) -> dict[str, object]:
        component_keys = _PAIR_COMPONENT_KEYS_BY_FEATURE[feature_id]
        return {
            "representation": "BCS_ALL_COMPATIBLE_PAIRS_FACTORED_V1",
            "pair_relation_sha256": pair_relation_sha256,
            "component_store_id": "BCS_PAIR_COMPONENT_STORE_V1",
            "component_keys": list(component_keys),
            "component_projection_sha256": canonical_json_sha256(
                component_projection(component_keys)
            ),
        }

    cross_pair_skew = factored_pair_value(
        "QUOTE.CROSS_LEG_RECEIVE_SKEW_NS_BY_BCS_PAIR"
    )
    bcs_long_delta = factored_pair_value("BCS0.LONG_DELTA_PPM_BY_PAIR")
    bcs_short_delta = factored_pair_value("BCS0.SHORT_DELTA_PPM_BY_PAIR")
    bcs_net_delta = factored_pair_value("BCS0.NET_DELTA_PPM_BY_PAIR")
    bcs_width = factored_pair_value("BCS0.WIDTH_NANO_USD_BY_PAIR")
    bcs_debit = factored_pair_value("BCS0.NET_DEBIT_NANO_USD_BY_PAIR")
    bcs_stressed = factored_pair_value("BCS0.STRESSED_DEBIT_NANO_USD_BY_PAIR")
    bcs_cap = factored_pair_value(
        "BCS0.THEORETICAL_EXPIRY_CAP_NANO_USD_BY_PAIR"
    )
    bcs_max_loss = factored_pair_value(
        "BCS0.MAX_LOSS_BASIS_NANO_USD_BY_PAIR"
    )
    bcs_liquidity = factored_pair_value("BCS0.LIQUIDITY_FACTS_BY_PAIR")
    debit_components = component_projection(
        _PAIR_COMPONENT_KEYS_BY_FEATURE["BCS0.NET_DEBIT_NANO_USD_BY_PAIR"]
    )
    stress_components = component_projection(
        _PAIR_COMPONENT_KEYS_BY_FEATURE[
            "BCS0.STRESSED_DEBIT_NANO_USD_BY_PAIR"
        ]
    )

    nlv = int(account["net_liquidation_value_nano_usd"])
    high_watermark = int(account["strategy_high_watermark_nano_usd"])
    drawdown = max(0, _trunc_div((high_watermark - nlv) * _PPM, high_watermark))
    eligible_bankroll = max(
        0,
        int(account["strategy_bankroll_nano_usd"])
        + _trunc_div(
            int(account["realized_profit_nano_usd"])
            * int(rules["realized_profit_reinvestment_ppm"]),
            _PPM,
        )
        - _trunc_div(
            int(account["realized_loss_nano_usd"])
            * int(rules["realized_loss_effect_ppm"]),
            _PPM,
        ),
    )
    cash_reserve = _trunc_div(
        nlv * int(rules["minimum_cash_reserve_nlv_ppm"]), _PPM
    )

    values: dict[str, object] = {
        "TREND.PRIOR_CLOSE_NANO_USD": prior_close,
        "TREND.SMA50_NANO_USD": sma50,
        "TREND.SMA200_NANO_USD": sma200,
        "TREND.SMA50_SLOPE_20_PPM_PER_SESSION": slope,
        "BREAKOUT.HIGH20_NANO_USD": breakout,
        "BREAKOUT.CLOSE_1044_NANO_USD": close_1044,
        "BREAKOUT.DISTANCE_PPM": breakout_distance_ppm,
        "INTRADAY.CONFIRMATION_CLOSES_15_NANO_USD": confirmation,
        "INTRADAY.ABOVE_BREAKOUT_COUNT": above_count,
        "TIME.DTE_CALENDAR_DAYS_BY_CONTRACT": dte,
        "TIME.H20_SESSION_ID": f"XNYS:{h20['session_ordinal']}:{h20_date.isoformat()}",
        "TIME.H20_DATE": h20_date.isoformat(),
        "TIME.EXPIRY_SAFETY_GAP_CALENDAR_DAYS_BY_CONTRACT": expiry_gap,
        "CONTRACT.QUALIFICATION_FACTS_BY_CONTRACT": contract_qualification,
        "QUOTE.SPREAD_NANO_USD_BY_CONTRACT": spreads,
        "QUOTE.AGE_NS_BY_CONTRACT": ages,
        "QUOTE.CROSS_LEG_RECEIVE_SKEW_NS_BY_BCS_PAIR": cross_pair_skew,
        "QUOTE.SIZE_BY_CONTRACT": sizes,
        "QUOTE.TICK_NANO_USD_BY_CONTRACT": ticks,
        "QUOTE.UNDERLYING_BBO_QUALITY_FACTS": underlying_quality,
        "QUOTE.EXECUTABILITY_FACTS_BY_CONTRACT": executability,
        "MODEL.IV_PPM_BY_CONTRACT": model_iv,
        "MODEL.DELTA_COARSE_PPM_BY_CONTRACT": model_coarse_delta,
        "MODEL.DELTA_FINE_PPM_BY_CONTRACT": model_fine_delta,
        "MODEL.CONVERGENCE_FACTS_BY_CONTRACT": model_convergence,
        "MODEL.INPUT_HASH_BY_CONTRACT": model_inputs,
        "LC0.DELTA_DISTANCE_PPM_BY_CONTRACT": lc_delta_distance,
        "LC0.ENTRY_DEBIT_NANO_USD_BY_CONTRACT": lc_entry_debit,
        "LC0.STRESSED_DEBIT_NANO_USD_BY_CONTRACT": lc_stressed_debit,
        "LC0.MAX_LOSS_BASIS_NANO_USD_BY_CONTRACT": lc_max_loss,
        "LC0.DELTA_NOTIONAL_NANO_USD_BY_CONTRACT": lc_delta_notional,
        "LC0.LIQUIDITY_FACTS_BY_CONTRACT": lc_liquidity,
        "BCS0.LONG_DELTA_PPM_BY_PAIR": bcs_long_delta,
        "BCS0.SHORT_DELTA_PPM_BY_PAIR": bcs_short_delta,
        "BCS0.NET_DELTA_PPM_BY_PAIR": bcs_net_delta,
        "BCS0.WIDTH_NANO_USD_BY_PAIR": bcs_width,
        "BCS0.NET_DEBIT_NANO_USD_BY_PAIR": bcs_debit,
        "BCS0.STRESSED_DEBIT_NANO_USD_BY_PAIR": bcs_stressed,
        "BCS0.THEORETICAL_EXPIRY_CAP_NANO_USD_BY_PAIR": bcs_cap,
        "BCS0.MAX_LOSS_BASIS_NANO_USD_BY_PAIR": bcs_max_loss,
        "BCS0.LIQUIDITY_FACTS_BY_PAIR": bcs_liquidity,
        "ACCOUNT.DRAWDOWN_PPM": drawdown,
        "ACCOUNT.ELIGIBLE_BANKROLL_NANO_USD": eligible_bankroll,
        "ACCOUNT.CASH_RESERVE_NANO_USD": cash_reserve,
        "ACCOUNT.EXISTING_GLD_DELTA_EXPOSURE_NANO_USD": account[
            "current_gld_delta_exposure_nano_usd"
        ],
    }
    if set(values) != set(metadata_by_id):
        missing = sorted(set(metadata_by_id) - set(values))
        extra = sorted(set(values) - set(metadata_by_id))
        raise TechnicalFactError(
            "TECHNICAL_CATALOG_IMPLEMENTATION_MISMATCH",
            f"missing={missing};extra={extra}",
        )

    daily_close_points = [
        {
            "session_ordinal": int(item["session_ordinal"]),
            "close_nano_usd": int(item["close_nano_usd"]),
        }
        for item in daily
    ]
    daily_high_points = [
        {
            "session_ordinal": int(item["session_ordinal"]),
            "high_nano_usd": int(item["high_nano_usd"]),
        }
        for item in daily
    ]
    confirmation_points = [
        {
            "minute_ending_ordinal": ordinal,
            "close_nano_usd": int(minute_by_ordinal[ordinal]["close_nano_usd"]),
        }
        for ordinal in _CONFIRMATION_ORDINALS
    ]
    contract_time_inputs = {
        contract_id: {
            "expiry_date": contract["expiry_date"],
            "last_trading_date": contract["last_trading_date"],
        }
        for contract_id, (contract, _book) in sorted(by_contract.items())
    }
    contract_qualification_inputs = {
        contract_id: {
            "capture_utc_ns": capture,
            "contract": {
                key: contract[key]
                for key in (
                    "activation_utc_ns",
                    "contract_id",
                    "currency",
                    "exercise_style",
                    "last_trading_utc_ns",
                    "multiplier",
                    "option_type",
                    "standard_unadjusted",
                    "strike_nano_usd",
                    "tick_nano_usd",
                    "underlying",
                )
            },
            "quote_identity": {
                "instrument_id": book["instrument_id"],
                "tick_nano_usd": book["tick_nano_usd"],
            },
        }
        for contract_id, (contract, book) in sorted(by_contract.items())
    }
    quote_market_inputs = {
        contract_id: {
            key: book[key]
            for key in (
                "ask_nano_usd",
                "ask_size",
                "bid_nano_usd",
                "bid_size",
                "flags",
                "receive_utc_ns",
                "tick_nano_usd",
            )
        }
        for contract_id, (_contract, book) in sorted(by_contract.items())
    }
    model_source_inputs = {
        "model_package": entry["model_package"],
        "numeric_runtime_environment": numeric_runtime_environment,
        "numeric_runtime_environment_sha256": (
            numeric_runtime_environment_sha256
        ),
        "input_sha256_by_contract": model_inputs,
    }
    model_core_input_source_inputs = {
        "model_package": entry["model_package"],
        "input_sha256_by_contract": model_inputs,
    }
    pair_source = {
        "pair_relation_sha256": pair_relation_sha256,
    }
    selected_inputs_by_feature: dict[str, object] = {
        "TREND.PRIOR_CLOSE_NANO_USD": {
            "trading_date": entry["trading_date"],
            "prior_session_close": daily_close_points[-1],
        },
        "TREND.SMA50_NANO_USD": {"window": daily_close_points[-50:]},
        "TREND.SMA200_NANO_USD": {"window": daily_close_points[-200:]},
        "TREND.SMA50_SLOPE_20_PPM_PER_SESSION": {
            "current_window": daily_close_points[-50:],
            "lagged_window": daily_close_points[-70:-20],
        },
        "BREAKOUT.HIGH20_NANO_USD": {"window": daily_high_points[-20:]},
        "BREAKOUT.CLOSE_1044_NANO_USD": {
            "timezone": calendar_container["timezone"],
            "selected_minute": confirmation_points[-1],
        },
        "BREAKOUT.DISTANCE_PPM": {
            "BREAKOUT.CLOSE_1044_NANO_USD": close_1044,
            "BREAKOUT.HIGH20_NANO_USD": breakout,
        },
        "INTRADAY.CONFIRMATION_CLOSES_15_NANO_USD": {
            "timezone": calendar_container["timezone"],
            "window": confirmation_points,
        },
        "INTRADAY.ABOVE_BREAKOUT_COUNT": {
            "BREAKOUT.HIGH20_NANO_USD": breakout,
            "INTRADAY.CONFIRMATION_CLOSES_15_NANO_USD": confirmation,
        },
        "TIME.DTE_CALENDAR_DAYS_BY_CONTRACT": {
            "trading_date": entry["trading_date"],
            "expiry_date_by_contract": {
                key: item["expiry_date"] for key, item in contract_time_inputs.items()
            },
        },
        "TIME.H20_SESSION_ID": {
            "decision_session_ordinal": decision_ordinal,
            "first_20_forward_sessions": [
                {
                    "session_ordinal": int(item["session_ordinal"]),
                    "trading_date": item["trading_date"],
                }
                for item in forward[:20]
            ],
        },
        "TIME.H20_DATE": {
            "decision_session_ordinal": decision_ordinal,
            "first_20_forward_sessions": [
                {
                    "session_ordinal": int(item["session_ordinal"]),
                    "trading_date": item["trading_date"],
                }
                for item in forward[:20]
            ],
        },
        "TIME.EXPIRY_SAFETY_GAP_CALENDAR_DAYS_BY_CONTRACT": {
            "TIME.H20_DATE": h20_date.isoformat(),
            "last_trading_date_by_contract": {
                key: item["last_trading_date"]
                for key, item in contract_time_inputs.items()
            },
        },
        "CONTRACT.QUALIFICATION_FACTS_BY_CONTRACT": contract_qualification_inputs,
        "QUOTE.SPREAD_NANO_USD_BY_CONTRACT": {
            key: {
                "ask_nano_usd": item["ask_nano_usd"],
                "bid_nano_usd": item["bid_nano_usd"],
            }
            for key, item in quote_market_inputs.items()
        },
        "QUOTE.AGE_NS_BY_CONTRACT": {
            "capture_utc_ns": capture,
            "receive_utc_ns_by_contract": receive_by_contract,
        },
        "QUOTE.CROSS_LEG_RECEIVE_SKEW_NS_BY_BCS_PAIR": {
            **pair_source,
            "snapshot_id": snapshot["snapshot_id"],
            "underlying_receive_utc_ns": underlying_receive,
            "receive_utc_ns_by_contract": receive_by_contract,
        },
        "QUOTE.SIZE_BY_CONTRACT": {
            key: {
                "ask_size": item["ask_size"],
                "bid_size": item["bid_size"],
            }
            for key, item in quote_market_inputs.items()
        },
        "QUOTE.TICK_NANO_USD_BY_CONTRACT": {
            key: {
                "contract_tick_nano_usd": by_contract[key][0]["tick_nano_usd"],
                "quote_tick_nano_usd": item["tick_nano_usd"],
            }
            for key, item in quote_market_inputs.items()
        },
        "QUOTE.UNDERLYING_BBO_QUALITY_FACTS": {
            "capture_utc_ns": capture,
            "underlying_bbo": {
                key: underlying_book[key]
                for key in (
                    "ask_nano_usd",
                    "ask_size",
                    "bid_nano_usd",
                    "bid_size",
                    "flags",
                    "receive_utc_ns",
                    "tick_nano_usd",
                )
            },
            "rules": {
                key: rules[key]
                for key in ("max_quote_age_ns", "min_quote_size")
            },
        },
        "QUOTE.EXECUTABILITY_FACTS_BY_CONTRACT": {
            "capture_utc_ns": capture,
            "underlying_receive_utc_ns": underlying_receive,
            "quotes_by_contract": quote_market_inputs,
            "rules": {
                key: rules[key]
                for key in (
                    "max_cross_leg_receive_skew_ns",
                    "max_quote_age_ns",
                    "min_quote_size",
                )
            },
        },
        "MODEL.IV_PPM_BY_CONTRACT": model_source_inputs,
        "MODEL.DELTA_COARSE_PPM_BY_CONTRACT": model_source_inputs,
        "MODEL.DELTA_FINE_PPM_BY_CONTRACT": model_source_inputs,
        "MODEL.CONVERGENCE_FACTS_BY_CONTRACT": model_source_inputs,
        "MODEL.INPUT_HASH_BY_CONTRACT": model_core_input_source_inputs,
        "LC0.DELTA_DISTANCE_PPM_BY_CONTRACT": {
            "MODEL.DELTA_FINE_PPM_BY_CONTRACT": model_fine_delta,
        },
        "LC0.ENTRY_DEBIT_NANO_USD_BY_CONTRACT": {
            "ask_nano_usd_by_contract": ask_by_contract,
            "multiplier_by_contract": multiplier_by_contract,
            "long_entry_fee_nano_usd_per_contract": fees[
                "long_entry_fee_nano_usd_per_contract"
            ],
        },
        "LC0.STRESSED_DEBIT_NANO_USD_BY_CONTRACT": {
            "ask_nano_usd_by_contract": ask_by_contract,
            "tick_nano_usd_by_contract": ticks,
            "multiplier_by_contract": multiplier_by_contract,
            "entry_stress_long_ticks": rules["entry_stress_long_ticks"],
            "long_entry_fee_nano_usd_per_contract": fees[
                "long_entry_fee_nano_usd_per_contract"
            ],
        },
        "LC0.MAX_LOSS_BASIS_NANO_USD_BY_CONTRACT": {
            "LC0.STRESSED_DEBIT_NANO_USD_BY_CONTRACT": lc_stressed_debit,
        },
        "LC0.DELTA_NOTIONAL_NANO_USD_BY_CONTRACT": {
            "underlying_bid_nano_usd": underlying_book["bid_nano_usd"],
            "underlying_ask_nano_usd": underlying_book["ask_nano_usd"],
            "multiplier_by_contract": multiplier_by_contract,
            "MODEL.DELTA_FINE_PPM_BY_CONTRACT": model_fine_delta,
        },
        "LC0.LIQUIDITY_FACTS_BY_CONTRACT": {
            "QUOTE.SPREAD_NANO_USD_BY_CONTRACT": spreads,
            "QUOTE.AGE_NS_BY_CONTRACT": ages,
            "QUOTE.SIZE_BY_CONTRACT": sizes,
            "QUOTE.TICK_NANO_USD_BY_CONTRACT": ticks,
            "QUOTE.EXECUTABILITY_FACTS_BY_CONTRACT": executability,
        },
        "BCS0.LONG_DELTA_PPM_BY_PAIR": {
            **pair_source,
            "MODEL.DELTA_FINE_PPM_BY_CONTRACT": model_fine_delta,
        },
        "BCS0.SHORT_DELTA_PPM_BY_PAIR": {
            **pair_source,
            "MODEL.DELTA_FINE_PPM_BY_CONTRACT": model_fine_delta,
        },
        "BCS0.NET_DELTA_PPM_BY_PAIR": {
            **pair_source,
            "MODEL.DELTA_FINE_PPM_BY_CONTRACT": model_fine_delta,
        },
        "BCS0.WIDTH_NANO_USD_BY_PAIR": {
            **pair_source,
            "strike_nano_usd_by_contract": strike_by_contract,
        },
        "BCS0.NET_DEBIT_NANO_USD_BY_PAIR": {
            **pair_source,
            **debit_components,
        },
        "BCS0.STRESSED_DEBIT_NANO_USD_BY_PAIR": {
            **pair_source,
            **stress_components,
        },
        "BCS0.THEORETICAL_EXPIRY_CAP_NANO_USD_BY_PAIR": {
            **pair_source,
            "strike_nano_usd_by_contract": strike_by_contract,
            "multiplier_by_contract": multiplier_by_contract,
        },
        "BCS0.MAX_LOSS_BASIS_NANO_USD_BY_PAIR": {
            **pair_source,
            **stress_components,
        },
        "BCS0.LIQUIDITY_FACTS_BY_PAIR": {
            **pair_source,
            "underlying_receive_utc_ns": underlying_receive,
            "receive_utc_ns_by_contract": receive_by_contract,
            "ask_size_by_contract": ask_size_by_contract,
            "bid_size_by_contract": bid_size_by_contract,
            "spread_nano_usd_by_contract": spreads,
        },
        "ACCOUNT.DRAWDOWN_PPM": {
            "net_liquidation_value_nano_usd": nlv,
            "strategy_high_watermark_nano_usd": high_watermark,
        },
        "ACCOUNT.ELIGIBLE_BANKROLL_NANO_USD": {
            "strategy_bankroll_nano_usd": account["strategy_bankroll_nano_usd"],
            "realized_profit_nano_usd": account["realized_profit_nano_usd"],
            "realized_loss_nano_usd": account["realized_loss_nano_usd"],
            "realized_profit_reinvestment_ppm": rules[
                "realized_profit_reinvestment_ppm"
            ],
            "realized_loss_effect_ppm": rules["realized_loss_effect_ppm"],
        },
        "ACCOUNT.CASH_RESERVE_NANO_USD": {
            "net_liquidation_value_nano_usd": nlv,
            "minimum_cash_reserve_nlv_ppm": rules[
                "minimum_cash_reserve_nlv_ppm"
            ],
        },
        "ACCOUNT.EXISTING_GLD_DELTA_EXPOSURE_NANO_USD": {
            "current_gld_delta_exposure_nano_usd": account[
                "current_gld_delta_exposure_nano_usd"
            ],
        },
    }
    if set(selected_inputs_by_feature) != set(values):
        raise TechnicalFactError("TECHNICAL_FEATURE_SOURCE_BINDING_MISSING")

    source_by_feature = {
        feature_id: {
            "projection_version": "SELECTED_FORMULA_INPUTS_V1",
            "declared_input_fields": metadata_by_id[feature_id]["input_fields"],
            "selected_inputs": selected_inputs_by_feature[feature_id],
        }
        for feature_id in values
    }
    features = [
        _feature(
            metadata_by_id[feature_id],
            value=values[feature_id],
            source_facts=source_by_feature[feature_id],
        )
        for feature_id in sorted(values)
    ]
    catalog_sha256 = canonical_json_sha256(catalog)
    document: dict[str, object] = {
        "schema_version": "REQUIRED_DAILY_TECHNICAL_FACTS_V1",
        "classification": entry["classification"],
        "data_qualification_status": validated.qualification.status,
        "data_qualification_reason_codes": list(
            validated.qualification.reason_codes
        ),
        "data_qualification_sha256": (
            validated.qualification.qualification_sha256
        ),
        "trusted_source_receipt_set_sha256": (
            validated.qualification.trusted_source_receipt_set_sha256
        ),
        "scope": "GLD_TECHNICAL_FACTS_ONLY_NO_DECISION",
        "underlying": "GLD",
        "trading_date": entry["trading_date"],
        "cutoff_utc_ns": entry["cutoff_utc_ns"],
        "entry_bundle_sha256": validated.entry_bundle_sha256,
        "technical_feature_catalog_id": catalog["catalog_id"],
        "technical_feature_catalog_sha256": catalog_sha256,
        "technical_engine_version": TECHNICAL_ENGINE_VERSION,
        "rule_package_sha256": canonical_json_sha256(entry["rule_package"]),
        "model_package_sha256": canonical_json_sha256(entry["model_package"]),
        "numeric_runtime_environment": numeric_runtime_environment,
        "numeric_runtime_environment_sha256": (
            numeric_runtime_environment_sha256
        ),
        "contract_facts": [
            {"contract_id": contract_id}
            for contract_id in sorted(by_contract)
        ],
        "bcs_pair_facts": pair_relation,
        "features": features,
        "research_features_included": False,
        "carrier_qualification_included": False,
        "decision_outputs_included": False,
    }
    try:
        encoded = canonical_json_bytes(document)
        document_sha256 = canonical_json_sha256(document)
    except RawBundleError as error:
        if error.reason_code in {
            "CANONICAL_JSON_NODE_LIMIT_EXCEEDED",
            "CANONICAL_JSON_RESOURCE_LIMIT_EXCEEDED",
        }:
            raise TechnicalFactError(
                "TECHNICAL_OUTPUT_RESOURCE_LIMIT_EXCEEDED"
            ) from error
        raise TechnicalFactError("TECHNICAL_VALUE_OUT_OF_RANGE") from error
    return RequiredDailyTechnicalFactsV1(
        technical_facts_sha256=document_sha256,
        canonical_bytes=encoded,
        _seal=_TECHNICAL_RESULT_SEAL,
    )


def materialize_bcs_pair_facts(
    technical_facts: RequiredDailyTechnicalFactsV1,
    *,
    long_contract_id: str,
    short_contract_id: str,
) -> dict[str, object]:
    """Decode one exact BCS pair from the linear factored representation.

    This performs no pair selection, eligibility decision, sizing, or
    preference ranking.  The caller must name both contracts explicitly.
    """

    if (
        type(technical_facts) is not RequiredDailyTechnicalFactsV1
        or type(long_contract_id) is not str
        or type(short_contract_id) is not str
        or long_contract_id == short_contract_id
    ):
        raise TechnicalFactError("TECHNICAL_BCS_PAIR_IDENTITY_INVALID")
    document = technical_facts.document
    relation = document["bcs_pair_facts"]
    if (
        type(relation) is not dict
        or type(relation.get("groups")) is not list
        or type(relation.get("component_store")) is not dict
        or type(relation["component_store"].get("components")) is not dict
    ):
        raise TechnicalFactError("TECHNICAL_BCS_PAIR_RELATION_INVALID")
    component_values = relation["component_store"]["components"]
    compatible = False
    long_strike = 0
    short_strike = 0
    for group in relation["groups"]:
        contracts = {
            item["contract_id"]: int(item["strike_nano_usd"])
            for item in group["contracts_by_strike"]
        }
        if long_contract_id in contracts and short_contract_id in contracts:
            long_strike = contracts[long_contract_id]
            short_strike = contracts[short_contract_id]
            compatible = short_strike > long_strike
            break
    if not compatible:
        raise TechnicalFactError("TECHNICAL_BCS_PAIR_IDENTITY_INVALID")
    materialized = {
        item["feature_id"]: item["value"] for item in document["features"]
    }

    def components(feature_id: str) -> dict[str, object]:
        value = materialized[feature_id]
        expected_keys = _PAIR_COMPONENT_KEYS_BY_FEATURE[feature_id]
        projection = {key: component_values[key] for key in expected_keys}
        if (
            type(value) is not dict
            or value.get("pair_relation_sha256")
            != relation["pair_relation_sha256"]
            or value.get("component_store_id")
            != "BCS_PAIR_COMPONENT_STORE_V1"
            or value.get("component_keys") != list(expected_keys)
            or value.get("component_projection_sha256")
            != canonical_json_sha256(projection)
        ):
            raise TechnicalFactError("TECHNICAL_BCS_PAIR_RELATION_INVALID")
        return projection

    delta_by_contract = components(
        "BCS0.LONG_DELTA_PPM_BY_PAIR"
    )["fine_delta_ppm_by_contract"]
    if type(delta_by_contract) is not dict:
        raise TechnicalFactError("TECHNICAL_BCS_PAIR_RELATION_INVALID")
    long_delta = delta_by_contract[long_contract_id]
    short_delta = delta_by_contract[short_contract_id]
    if type(long_delta) is int and type(short_delta) is int:
        net_delta: object = long_delta - short_delta
    else:
        missing = long_delta if type(long_delta) is dict else short_delta
        net_delta = missing

    skew_components = components(
        "QUOTE.CROSS_LEG_RECEIVE_SKEW_NS_BY_BCS_PAIR"
    )
    receive_by_contract = skew_components["receive_utc_ns_by_contract"]
    if type(receive_by_contract) is not dict:
        raise TechnicalFactError("TECHNICAL_BCS_PAIR_RELATION_INVALID")
    receive_times = (
        int(skew_components["underlying_receive_utc_ns"]),
        int(receive_by_contract[long_contract_id]),
        int(receive_by_contract[short_contract_id]),
    )
    cross_leg_skew = max(receive_times) - min(receive_times)

    debit_components = components("BCS0.NET_DEBIT_NANO_USD_BY_PAIR")
    asks = debit_components["ask_nano_usd_by_contract"]
    bids = debit_components["bid_nano_usd_by_contract"]
    multipliers = debit_components["multiplier_by_contract"]
    if any(type(value) is not dict for value in (asks, bids, multipliers)):
        raise TechnicalFactError("TECHNICAL_BCS_PAIR_RELATION_INVALID")
    multiplier = int(multipliers[long_contract_id])
    debit = (
        (int(asks[long_contract_id]) - int(bids[short_contract_id]))
        * multiplier
        + int(debit_components["long_entry_fee_nano_usd_per_contract"])
        + int(debit_components["short_entry_fee_nano_usd_per_contract"])
    )
    debit_value: object = (
        debit if debit > 0 else _missing("BCS_NET_DEBIT_NONPOSITIVE")
    )

    stress_components = components("BCS0.STRESSED_DEBIT_NANO_USD_BY_PAIR")
    ticks_by_contract = stress_components["tick_nano_usd_by_contract"]
    if type(ticks_by_contract) is not dict:
        raise TechnicalFactError("TECHNICAL_BCS_PAIR_RELATION_INVALID")
    stressed = (
        int(asks[long_contract_id])
        + int(ticks_by_contract[long_contract_id])
        * int(stress_components["entry_stress_long_ticks"])
        - int(bids[short_contract_id])
        + int(ticks_by_contract[short_contract_id])
        * int(stress_components["entry_stress_short_ticks"])
    ) * multiplier + int(
        stress_components["long_entry_fee_nano_usd_per_contract"]
    ) + int(
        stress_components["short_entry_fee_nano_usd_per_contract"]
    )
    width = short_strike - long_strike
    cap = width * multiplier
    stressed_value: object
    if stressed <= 0 or stressed > cap:
        stressed_value = _missing("BCS_STRESSED_DEBIT_OUT_OF_BOUNDS")
    else:
        stressed_value = stressed

    liquidity_components = components("BCS0.LIQUIDITY_FACTS_BY_PAIR")
    ask_sizes = liquidity_components["ask_size_by_contract"]
    bid_sizes = liquidity_components["bid_size_by_contract"]
    pair_spreads = liquidity_components["spread_nano_usd_by_contract"]
    if any(type(value) is not dict for value in (ask_sizes, bid_sizes, pair_spreads)):
        raise TechnicalFactError("TECHNICAL_BCS_PAIR_RELATION_INVALID")
    pair_values: dict[str, object] = {
        "QUOTE.CROSS_LEG_RECEIVE_SKEW_NS_BY_BCS_PAIR": cross_leg_skew,
        "BCS0.LONG_DELTA_PPM_BY_PAIR": long_delta,
        "BCS0.SHORT_DELTA_PPM_BY_PAIR": short_delta,
        "BCS0.NET_DELTA_PPM_BY_PAIR": net_delta,
        "BCS0.WIDTH_NANO_USD_BY_PAIR": width,
        "BCS0.NET_DEBIT_NANO_USD_BY_PAIR": debit_value,
        "BCS0.STRESSED_DEBIT_NANO_USD_BY_PAIR": stressed_value,
        "BCS0.THEORETICAL_EXPIRY_CAP_NANO_USD_BY_PAIR": cap,
        "BCS0.MAX_LOSS_BASIS_NANO_USD_BY_PAIR": stressed_value,
        "BCS0.LIQUIDITY_FACTS_BY_PAIR": {
            "cross_leg_receive_skew_ns": cross_leg_skew,
            "long_ask_size": int(ask_sizes[long_contract_id]),
            "long_spread_nano_usd": int(pair_spreads[long_contract_id]),
            "minimum_entry_size": min(
                int(ask_sizes[long_contract_id]),
                int(bid_sizes[short_contract_id]),
            ),
            "short_bid_size": int(bid_sizes[short_contract_id]),
            "short_spread_nano_usd": int(pair_spreads[short_contract_id]),
        },
    }
    unsigned: dict[str, object] = {
        "schema_version": "BCS_PAIR_TECHNICAL_FACTS_V1",
        "pair_id": f"{long_contract_id}|{short_contract_id}",
        "long_contract_id": long_contract_id,
        "short_contract_id": short_contract_id,
        "pair_relation_sha256": relation["pair_relation_sha256"],
        "features": pair_values,
    }
    try:
        return {
            **unsigned,
            "pair_facts_sha256": canonical_json_sha256(unsigned),
        }
    except RawBundleError as error:
        raise TechnicalFactError("TECHNICAL_VALUE_OUT_OF_RANGE") from error


__all__ = [
    "RequiredDailyTechnicalFactsV1",
    "TechnicalFactError",
    "derive_required_technical_facts",
    "materialize_bcs_pair_facts",
]
