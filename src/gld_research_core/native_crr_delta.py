"""Backend-bound native shadow orchestration for the frozen Call CRR model.

The semantic model remains ``GLD_CALL_DELTA_CRR_AM_V1``.  This module copies
that model's fixed IV/Delta orchestration and substitutes only a loader-verified
native tree evaluation.  The reference Python source is therefore semantic
identity evidence, not an execution-source claim.

There is deliberately no builder, loader, fallback, provider, broker, or order
path here.  An integrator must first obtain a :class:`VerifiedNativeTreeV1`
from the strict native loader and bind it once through the public factory.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
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
import re
import sys
import threading
from types import BuiltinFunctionType, FunctionType, MappingProxyType
from typing import Callable, Mapping
import weakref

from gld_normalizer.errors import NormalizationError
from gld_normalizer.readers import (
    MAX_DEFINED_I64 as _MAX_DEFINED_I64,
    MAX_DEFINED_U64 as _MAX_DEFINED_U64,
)

from . import crr_delta as _reference_module
from . import native_tree as _native_tree_module
from .crr_delta import (
    MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256,
    MODEL_CONFIG_SHA256,
    MODEL_CONTRACT_SHA256,
    MODEL_ID,
    MODEL_SHA256,
    MODEL_SOURCE_ARTIFACT_SHA256,
    RUNTIME_FINGERPRINT_SHA256,
    CrrCallInputsV1 as _CrrCallInputsV1,
    CrrPitInputsV1 as _CrrPitInputsV1,
)
from .native_tree import (
    NATIVE_ABI_ID,
    NATIVE_ABI_VERSION,
    VerifiedNativeTreeV1 as _VerifiedNativeTreeV1,
    is_verified_native_tree_v1 as _is_verified_native_tree_v1,
    verify_native_tree_manifest_v1,
)


NATIVE_CRR_EXECUTION_STATUS = "NATIVE_SHADOW_ONLY_NOT_P1_QUALIFIED"
NATIVE_CRR_BACKEND_EVIDENCE_SCHEMA = "GLD_CRR_NATIVE_CALL_BACKEND_EVIDENCE_V1"
NATIVE_EXECUTION_BACKEND_KIND = "VERIFIED_NATIVE_TREE_SHADOW_V1"
SEMANTIC_REFERENCE_SOURCE_EXECUTED = False
NATIVE_LOADER_TRUST_PROVENANCE = "PROCESS_LOCAL_CLOSURE_ATTESTATION_V1"

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
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)

# Captured from these source bytes at import.  It is intentionally not embedded
# as a literal, which would create a self-referential artifact hash.
NATIVE_ORCHESTRATOR_SOURCE_ARTIFACT_SHA256 = sha256(
    Path(__file__).read_bytes()
).hexdigest()
NATIVE_TREE_LOADER_SOURCE_ARTIFACT_SHA256 = sha256(
    Path(_native_tree_module.__file__).read_bytes()
).hexdigest()
NATIVE_TREE_VERIFIER_CODE_SHA256 = sha256(
    _is_verified_native_tree_v1.__code__.co_code
).hexdigest()


def _require_hash(value: object) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")
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


def _require_exact_int(
    value: object,
    *,
    minimum: int,
    maximum: int,
    reason_code: str = "DELTA_INPUT_MISSING",
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise NormalizationError(reason_code)
    return value


@dataclass(frozen=True, slots=True, weakref_slot=True)
class NativeCrrDeltaResultV1:
    """Immutable semantic result plus explicit native execution provenance.

    Structural construction is not verification.  Only the matching
    factory-returned verifier can attest that an instance was emitted by its
    captured loader-created kernel in this process.
    """

    model_id: str
    model_sha256: str
    model_contract_sha256: str
    model_config_sha256: str
    semantic_reference_source_artifact_sha256: str
    canonical_encoder_source_artifact_sha256: str
    runtime_fingerprint_sha256: str
    execution_backend_kind: str
    semantic_reference_source_executed: bool
    native_orchestrator_source_artifact_sha256: str
    native_loader_source_artifact_sha256: str
    native_loader_verifier_code_sha256: str
    native_build_backend_evidence_sha256: str
    native_runtime_environment_sha256: str
    native_abi_identity_sha256: str
    combined_backend_evidence_sha256: str
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

    def __post_init__(self) -> None:
        if (
            type(self.model_id) is not str
            or self.model_id != MODEL_ID
            or type(self.model_sha256) is not str
            or self.model_sha256 != MODEL_SHA256
            or type(self.model_contract_sha256) is not str
            or self.model_contract_sha256 != MODEL_CONTRACT_SHA256
            or type(self.model_config_sha256) is not str
            or self.model_config_sha256 != MODEL_CONFIG_SHA256
            or type(self.semantic_reference_source_artifact_sha256) is not str
            or self.semantic_reference_source_artifact_sha256
            != MODEL_SOURCE_ARTIFACT_SHA256
            or type(self.canonical_encoder_source_artifact_sha256) is not str
            or self.canonical_encoder_source_artifact_sha256
            != MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256
            or type(self.runtime_fingerprint_sha256) is not str
            or self.runtime_fingerprint_sha256 != RUNTIME_FINGERPRINT_SHA256
            or type(self.execution_backend_kind) is not str
            or self.execution_backend_kind != NATIVE_EXECUTION_BACKEND_KIND
            or self.semantic_reference_source_executed is not False
            or type(self.native_orchestrator_source_artifact_sha256) is not str
            or self.native_orchestrator_source_artifact_sha256
            != NATIVE_ORCHESTRATOR_SOURCE_ARTIFACT_SHA256
            or type(self.native_loader_source_artifact_sha256) is not str
            or self.native_loader_source_artifact_sha256
            != NATIVE_TREE_LOADER_SOURCE_ARTIFACT_SHA256
            or type(self.native_loader_verifier_code_sha256) is not str
            or self.native_loader_verifier_code_sha256
            != NATIVE_TREE_VERIFIER_CODE_SHA256
        ):
            raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")
        for value in (
            self.native_build_backend_evidence_sha256,
            self.native_runtime_environment_sha256,
            self.native_abi_identity_sha256,
            self.combined_backend_evidence_sha256,
        ):
            _require_hash(value)
        _require_binding_hash(self.input_sha256)
        _require_binding_hash(self.option_snapshot_sha256)
        _require_contract_id(self.contract_id)
        for value in (self.iv_ppm, self.coarse_iv_ppm, self.fine_iv_ppm):
            _require_exact_int(
                value,
                minimum=100,
                maximum=5_000_000,
                reason_code="TREE_NOT_CONVERGED",
            )
        for value in (
            self.delta_ppm,
            self.coarse_delta_ppm,
            self.fine_delta_ppm,
        ):
            _require_exact_int(
                value,
                minimum=0,
                maximum=_PPM,
                reason_code="DELTA_OUT_OF_RANGE",
            )
        for value in (
            self.coarse_price_residual_nano_usd,
            self.fine_price_residual_nano_usd,
            self.coarse_early_exercise_nodes,
            self.fine_early_exercise_nodes,
        ):
            _require_exact_int(
                value,
                minimum=0,
                maximum=_MAX_DEFINED_I64,
                reason_code="TREE_NOT_CONVERGED",
            )
        if (
            self.iv_ppm != self.fine_iv_ppm
            or self.delta_ppm != self.fine_delta_ppm
            or abs(self.coarse_iv_ppm - self.fine_iv_ppm) > 100
            or abs(self.coarse_delta_ppm - self.fine_delta_ppm) > 500
        ):
            raise NormalizationError("TREE_NOT_CONVERGED")


@dataclass(frozen=True, slots=True)
class _SuiteEvaluation:
    volatility: float
    price: float
    delta: float
    price_residual: float
    early_exercise_nodes: int


def _canonical_evidence_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise NormalizationError("DELTA_MODEL_HASH_MISMATCH") from error


def _native_crr_backend_evidence_from_verified_manifest(
    manifest: Mapping[str, object],
    *,
    expected_native_build_backend_evidence_sha256: str,
) -> tuple[str, str]:
    """Derive ABI and combined evidence without importing native code."""

    native_build_evidence = _require_hash(
        expected_native_build_backend_evidence_sha256
    )
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("abi_id") != NATIVE_ABI_ID
        or manifest.get("abi_version") != NATIVE_ABI_VERSION
        or manifest.get("backend_evidence_sha256")
        != native_build_evidence
        or sha256(Path(__file__).resolve(strict=True).read_bytes()).hexdigest()
        != NATIVE_ORCHESTRATOR_SOURCE_ARTIFACT_SHA256
        or sha256(
            Path(_native_tree_module.__file__).resolve(strict=True).read_bytes()
        ).hexdigest()
        != NATIVE_TREE_LOADER_SOURCE_ARTIFACT_SHA256
        or sha256(_is_verified_native_tree_v1.__code__.co_code).hexdigest()
        != NATIVE_TREE_VERIFIER_CODE_SHA256
    ):
        raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")
    source_manifest_sha256 = _require_hash(
        manifest.get("source_manifest_sha256")
    )
    build_input_sha256 = _require_hash(
        manifest.get("build_input_sha256")
    )
    compiler_flags_sha256 = _require_hash(
        manifest.get("compiler_flags_sha256")
    )
    dependency_manifest_sha256 = _require_hash(
        manifest.get("dependency_manifest_sha256")
    )
    abi_document = {
        "abi_id": NATIVE_ABI_ID,
        "abi_version": NATIVE_ABI_VERSION,
        "build_input_sha256": build_input_sha256,
        "compiler_flags_sha256": compiler_flags_sha256,
        "dependency_manifest_sha256": dependency_manifest_sha256,
        "source_manifest_sha256": source_manifest_sha256,
    }
    native_abi_identity_sha256 = sha256(
        _canonical_evidence_bytes(abi_document)
    ).hexdigest()
    evidence_document = {
        "execution": {
            "backend_kind": NATIVE_EXECUTION_BACKEND_KIND,
            "native_abi_identity_sha256": native_abi_identity_sha256,
            "native_build_backend_evidence_sha256": native_build_evidence,
            "native_build_manifest_sha256": _require_hash(
                manifest.get("build_manifest_sha256")
            ),
            "native_manifest_payload_sha256": sha256(
                _canonical_evidence_bytes(dict(manifest))
            ).hexdigest(),
            "native_loader_source_artifact_sha256": (
                NATIVE_TREE_LOADER_SOURCE_ARTIFACT_SHA256
            ),
            "native_loader_trust_provenance": (
                NATIVE_LOADER_TRUST_PROVENANCE
            ),
            "native_loader_verifier_code_sha256": (
                NATIVE_TREE_VERIFIER_CODE_SHA256
            ),
            "native_loader_verifier_module": (
                _is_verified_native_tree_v1.__module__
            ),
            "native_loader_verifier_qualname": (
                _is_verified_native_tree_v1.__qualname__
            ),
            "native_orchestrator_source_artifact_sha256": (
                NATIVE_ORCHESTRATOR_SOURCE_ARTIFACT_SHA256
            ),
            "native_runtime_environment_sha256": _require_hash(
                manifest.get("runtime_environment_sha256")
            ),
            "python_runtime_fingerprint_sha256": (
                RUNTIME_FINGERPRINT_SHA256
            ),
        },
        "schema_version": NATIVE_CRR_BACKEND_EVIDENCE_SCHEMA,
        "semantic_model": {
            "canonical_encoder_source_artifact_sha256": (
                MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256
            ),
            "model_config_sha256": MODEL_CONFIG_SHA256,
            "model_contract_sha256": MODEL_CONTRACT_SHA256,
            "model_id": MODEL_ID,
            "model_sha256": MODEL_SHA256,
            "reference_source_artifact_sha256": (
                MODEL_SOURCE_ARTIFACT_SHA256
            ),
            "reference_source_executed": False,
        },
    }
    return (
        native_abi_identity_sha256,
        sha256(_canonical_evidence_bytes(evidence_document)).hexdigest(),
    )


def derive_native_crr_combined_backend_evidence_sha256(
    manifest_path: Path,
    *,
    expected_native_build_backend_evidence_sha256: str,
) -> str:
    """Statically verify a native build and derive its combined identity.

    The binary is read and hashed but never imported or executed. Native
    execution remains confined to the supervised child process.
    """

    manifest = verify_native_tree_manifest_v1(
        manifest_path,
        expected_backend_evidence_sha256=(
            expected_native_build_backend_evidence_sha256
        ),
    )
    _, combined = _native_crr_backend_evidence_from_verified_manifest(
        manifest,
        expected_native_build_backend_evidence_sha256=(
            expected_native_build_backend_evidence_sha256
        ),
    )
    return combined


def create_native_crr_delta_engine(
    kernel: _VerifiedNativeTreeV1,
) -> tuple[
    Callable[..., NativeCrrDeltaResultV1],
    Callable[[object], bool],
    str,
]:
    """Bind one loader-created native tree to the full frozen Call engine.

    The returned compute callable has the same keyword-only model-hash gate as
    the reference engine.  The verifier and combined backend evidence are
    factory-specific; callers cannot select or replace a backend per call.
    """

    native_tree_verifier = _is_verified_native_tree_v1
    native_tree_source_path = Path(_native_tree_module.__file__).resolve(
        strict=True
    )
    if (
        type(native_tree_verifier) is not FunctionType
        or _native_tree_module.is_verified_native_tree_v1
        is not native_tree_verifier
        or native_tree_verifier.__module__ != _native_tree_module.__name__
        or native_tree_verifier.__qualname__
        != "_make_native_tree_loader.<locals>.is_verified_native_tree_v1"
        or sha256(native_tree_source_path.read_bytes()).hexdigest()
        != NATIVE_TREE_LOADER_SOURCE_ARTIFACT_SHA256
        or sha256(native_tree_verifier.__code__.co_code).hexdigest()
        != NATIVE_TREE_VERIFIER_CODE_SHA256
    ):
        raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")
    try:
        loader_verified = native_tree_verifier(kernel)
    except Exception as error:
        raise NormalizationError("DELTA_MODEL_HASH_MISMATCH") from error
    if type(kernel) is not _VerifiedNativeTreeV1 or loader_verified is not True:
        raise NormalizationError("DELTA_MODEL_HASH_MISMATCH")

    # Capture primitives and all executable callables before creating the
    # process-local result registry.  Numeric orchestration below uses only
    # these closure bindings, not mutable module lookups.
    normalization_error = NormalizationError
    result_type = NativeCrrDeltaResultV1
    call_type = _CrrCallInputsV1
    pit_type = _CrrPitInputsV1
    verified_kernel_type = _VerifiedNativeTreeV1
    allocate = object.__new__
    make_reference = weakref.ref
    type_fn = type
    abs_fn = abs
    any_fn = any
    all_fn = all
    int_fn = int
    id_fn = id
    max_fn = max
    range_fn = range
    repr_fn = repr
    str_fn = str
    math_exp = math.exp
    math_isfinite = math.isfinite
    math_log = math.log
    decimal_type = Decimal
    decimal_context_type = Context
    decimal_exception_type = DecimalException
    decimal_localcontext = localcontext
    decimal_rounding = ROUND_HALF_EVEN
    decimal_traps = _DECIMAL_CONTEXT_TRAPS
    json_dumps = json.dumps
    sha256_type = sha256
    function_type = FunctionType
    mapping_proxy_type = MappingProxyType
    path_type = Path
    require_hash = _require_hash
    require_binding_hash = _require_binding_hash
    require_contract_id = _require_contract_id
    require_exact_int = _require_exact_int
    canonical_evidence_bytes = _canonical_evidence_bytes
    backend_evidence_builder = (
        _native_crr_backend_evidence_from_verified_manifest
    )
    suite_type = _SuiteEvaluation
    reference_module = _reference_module
    native_tree_module = _native_tree_module
    loader_verifier = native_tree_verifier
    loader_verifier_code = loader_verifier.__code__
    loader_verifier_defaults = loader_verifier.__defaults__
    loader_verifier_kwdefaults = (
        None
        if loader_verifier.__kwdefaults__ is None
        else dict(loader_verifier.__kwdefaults__)
    )
    loader_verifier_closure_states = tuple(
        (cell, cell.cell_contents)
        for cell in loader_verifier.__closure__ or ()
    )
    native_loader_source_sha256 = NATIVE_TREE_LOADER_SOURCE_ARTIFACT_SHA256
    native_loader_verifier_code_sha256 = NATIVE_TREE_VERIFIER_CODE_SHA256
    loader_trust_provenance = NATIVE_LOADER_TRUST_PROVENANCE
    sys_modules = sys.modules
    module_namespace = globals()
    current_module = sys_modules[__name__]
    factory_function = create_native_crr_delta_engine
    factory_code = factory_function.__code__
    inner_callable_state_bindings: tuple[
        tuple[
            Callable[..., object],
            object,
            object,
            dict[str, object] | None,
            tuple[tuple[object, object], ...],
        ],
        ...,
    ] = ()
    expected_inner_callable_count = 0
    expected_inner_registry_fingerprint = ""
    module_helper_state_bindings = tuple(
        (
            implementation,
            implementation.__code__,
            implementation.__defaults__,
            None
            if implementation.__kwdefaults__ is None
            else dict(implementation.__kwdefaults__),
        )
        for implementation in (
            require_hash,
            require_binding_hash,
            require_contract_id,
            require_exact_int,
            canonical_evidence_bytes,
            backend_evidence_builder,
        )
    )
    source_path = Path(__file__).resolve(strict=True)
    source_sha256 = sha256_type(source_path.read_bytes()).hexdigest()
    if source_sha256 != NATIVE_ORCHESTRATOR_SOURCE_ARTIFACT_SHA256:
        raise normalization_error("DELTA_MODEL_HASH_MISMATCH")

    call_fields = fields(call_type)
    pit_fields = fields(pit_type)
    result_fields = fields(result_type)
    call_slots = {item.name: getattr(call_type, item.name) for item in call_fields}
    pit_slots = {item.name: getattr(pit_type, item.name) for item in pit_fields}
    result_slots = {
        item.name: getattr(result_type, item.name) for item in result_fields
    }
    class_field_bindings = tuple(
        (
            owner,
            getattr(owner, "__dataclass_fields__"),
            tuple(getattr(owner, "__dataclass_fields__").items()),
            tuple((item, item.name, item._field_type) for item in fields(owner)),
        )
        for owner in (call_type, pit_type, result_type, suite_type)
    )
    missing_surface = object()
    class_surface_bindings: list[tuple[type[object], str, object]] = []
    class_code_bindings: list[tuple[object, object]] = []
    for owner in (call_type, pit_type, result_type, suite_type):
        names = {
            "__new__",
            "__init__",
            "__post_init__",
            "__getattribute__",
            "__setattr__",
            "__dataclass_fields__",
            *(item.name for item in fields(owner)),
            *(
                name
                for name, descriptor in vars(owner).items()
                if isinstance(descriptor, property)
            ),
        }
        for name in sorted(names):
            descriptor = getattr(owner, name, missing_surface)
            class_surface_bindings.append((owner, name, descriptor))
            for code_owner in (
                descriptor,
                getattr(descriptor, "fget", None),
                getattr(descriptor, "fset", None),
                getattr(descriptor, "fdel", None),
            ):
                if hasattr(code_owner, "__code__"):
                    class_code_bindings.append((code_owner, code_owner.__code__))

    manifest = kernel._manifest
    if type(manifest) is not mapping_proxy_type:
        raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
    manifest_bytes = canonical_evidence_bytes(dict(manifest))
    native_build_evidence = require_hash(kernel.backend_evidence_sha256)
    native_runtime_environment_sha256 = require_hash(
        manifest.get("runtime_environment_sha256")
    )
    if manifest.get("backend_evidence_sha256") != native_build_evidence:
        raise normalization_error("DELTA_MODEL_HASH_MISMATCH")

    identity_function = kernel._identity_function
    tree_function = kernel._tree_function
    native_module = kernel._module
    if (
        type(identity_function) is not BuiltinFunctionType
        or type(tree_function) is not BuiltinFunctionType
        or getattr(native_module, "identity_v1", None) is not identity_function
        or getattr(native_module, "tree_eval_v1", None) is not tree_function
    ):
        raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
    try:
        native_identity = identity_function()
    except Exception as error:
        raise normalization_error("DELTA_MODEL_HASH_MISMATCH") from error
    expected_native_identity = (
        NATIVE_ABI_ID,
        NATIVE_ABI_VERSION,
        manifest.get("source_manifest_sha256"),
        manifest.get("build_input_sha256"),
        manifest.get("compiler_flags_sha256"),
        manifest.get("dependency_manifest_sha256"),
    )
    if (
        type(native_identity) is not tuple
        or len(native_identity) != 6
        or type(native_identity[0]) is not str
        or type(native_identity[1]) is not int
        or any(type(value) is not str for value in native_identity[2:])
        or native_identity != expected_native_identity
    ):
        raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
    (
        native_abi_identity_sha256,
        combined_backend_evidence_sha256,
    ) = backend_evidence_builder(
        manifest,
        expected_native_build_backend_evidence_sha256=(
            native_build_evidence
        ),
    )

    kernel_state = (
        kernel.manifest_path,
        native_build_evidence,
        manifest,
        native_module,
        identity_function,
        tree_function,
        loader_verifier,
        native_loader_source_sha256,
        native_loader_verifier_code_sha256,
        loader_trust_provenance,
    )
    reference_values = {
        "MODEL_ID": MODEL_ID,
        "MODEL_CONTRACT_SHA256": MODEL_CONTRACT_SHA256,
        "MODEL_CONFIG_SHA256": MODEL_CONFIG_SHA256,
        "MODEL_SOURCE_ARTIFACT_SHA256": MODEL_SOURCE_ARTIFACT_SHA256,
        "MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256": (
            MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256
        ),
        "MODEL_SHA256": MODEL_SHA256,
        "RUNTIME_FINGERPRINT_SHA256": RUNTIME_FINGERPRINT_SHA256,
        "_COARSE_STEPS": _COARSE_STEPS,
        "_FINE_STEPS": _FINE_STEPS,
        "_IV_LOWER": _IV_LOWER,
        "_IV_UPPER": _IV_UPPER,
        "_BISECTION_ITERATIONS": _BISECTION_ITERATIONS,
        "_PRICE_RESIDUAL_FLOOR_USD": _PRICE_RESIDUAL_FLOOR_USD,
        "_IV_SUITE_TOLERANCE": _IV_SUITE_TOLERANCE,
        "_DELTA_SUITE_TOLERANCE": _DELTA_SUITE_TOLERANCE,
        "_NANO_PER_USD": _NANO_PER_USD,
        "_PPM": _PPM,
        "_YEAR_NS": _YEAR_NS,
        "_MAX_MATURITY_NS": _MAX_MATURITY_NS,
        "_MAX_ABS_RATE_PPM": _MAX_ABS_RATE_PPM,
    }
    if any(
        type(getattr(reference_module, name, None)) is not type(expected)
        or getattr(reference_module, name, None) != expected
        for name, expected in reference_values.items()
    ):
        raise normalization_error("DELTA_MODEL_HASH_MISMATCH")

    module_bindings = (
        ("create_native_crr_delta_engine", factory_function),
        ("NativeCrrDeltaResultV1", result_type),
        ("_SuiteEvaluation", suite_type),
        ("NormalizationError", normalization_error),
        ("_CrrCallInputsV1", call_type),
        ("_CrrPitInputsV1", pit_type),
        ("_VerifiedNativeTreeV1", verified_kernel_type),
        ("_is_verified_native_tree_v1", loader_verifier),
        ("_require_hash", require_hash),
        ("_require_binding_hash", require_binding_hash),
        ("_require_contract_id", require_contract_id),
        ("_require_exact_int", require_exact_int),
        ("_canonical_evidence_bytes", canonical_evidence_bytes),
        (
            "_native_crr_backend_evidence_from_verified_manifest",
            backend_evidence_builder,
        ),
    )
    constant_bindings = tuple(
        (name, module_namespace[name])
        for name in (
            "NATIVE_CRR_EXECUTION_STATUS",
            "NATIVE_CRR_BACKEND_EVIDENCE_SCHEMA",
            "NATIVE_EXECUTION_BACKEND_KIND",
            "SEMANTIC_REFERENCE_SOURCE_EXECUTED",
            "NATIVE_ORCHESTRATOR_SOURCE_ARTIFACT_SHA256",
            "NATIVE_TREE_LOADER_SOURCE_ARTIFACT_SHA256",
            "NATIVE_TREE_VERIFIER_CODE_SHA256",
            "NATIVE_LOADER_TRUST_PROVENANCE",
            "MODEL_ID",
            "MODEL_CONTRACT_SHA256",
            "MODEL_CONFIG_SHA256",
            "MODEL_SOURCE_ARTIFACT_SHA256",
            "MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256",
            "MODEL_SHA256",
            "RUNTIME_FINGERPRINT_SHA256",
            "NATIVE_ABI_ID",
            "NATIVE_ABI_VERSION",
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
            "_SHA256_RE",
        )
    )

    def inner_registry_fingerprint(registry: object) -> str | None:
        if type_fn(registry) is not tuple:
            return None
        signature: list[object] = []
        for record in registry:
            if type_fn(record) is not tuple or len(record) != 5:
                return None
            (
                implementation,
                expected_code,
                expected_defaults,
                expected_kwdefaults,
                closure_states,
            ) = record
            if type_fn(closure_states) is not tuple:
                return None
            if expected_kwdefaults is None:
                kwdefaults_signature = None
            elif type_fn(expected_kwdefaults) is dict and all_fn(
                type_fn(key) is str for key in expected_kwdefaults
            ):
                kwdefaults_signature = tuple(
                    (key, id_fn(value))
                    for key, value in expected_kwdefaults.items()
                )
            else:
                return None
            closure_signature: list[tuple[int, int]] = []
            for binding in closure_states:
                if type_fn(binding) is not tuple or len(binding) != 2:
                    return None
                cell, expected_value = binding
                closure_signature.append(
                    (id_fn(cell), id_fn(expected_value))
                )
            signature.append(
                (
                    id_fn(implementation),
                    id_fn(expected_code),
                    id_fn(expected_defaults),
                    kwdefaults_signature,
                    tuple(closure_signature),
                )
            )
        try:
            payload = repr_fn(tuple(signature)).encode("ascii")
        except (UnicodeEncodeError, ValueError):
            return None
        return sha256_type(payload).hexdigest()

    inner_registry_fingerprint_code = inner_registry_fingerprint.__code__
    inner_registry_fingerprint_defaults = inner_registry_fingerprint.__defaults__
    inner_registry_fingerprint_kwdefaults = (
        None
        if inner_registry_fingerprint.__kwdefaults__ is None
        else dict(inner_registry_fingerprint.__kwdefaults__)
    )
    inner_registry_fingerprint_closure_states = tuple(
        (cell, cell.cell_contents)
        for cell in inner_registry_fingerprint.__closure__ or ()
    )

    def require_loader_runtime() -> None:
        try:
            current_closure = loader_verifier.__closure__ or ()
            intact = (
                type_fn(loader_verifier) is function_type
                and sys_modules.get(native_tree_module.__name__)
                is native_tree_module
                and native_tree_module.is_verified_native_tree_v1
                is loader_verifier
                and loader_verifier.__module__ == native_tree_module.__name__
                and loader_verifier.__qualname__
                == (
                    "_make_native_tree_loader.<locals>."
                    "is_verified_native_tree_v1"
                )
                and loader_verifier.__code__ is loader_verifier_code
                and loader_verifier.__defaults__ is loader_verifier_defaults
                and loader_verifier.__kwdefaults__
                == loader_verifier_kwdefaults
                and len(current_closure)
                == len(loader_verifier_closure_states)
                and all_fn(
                    current is expected_cell
                    and current.cell_contents is expected_value
                    for current, (expected_cell, expected_value) in zip(
                        current_closure,
                        loader_verifier_closure_states,
                        strict=True,
                    )
                )
                and path_type(native_tree_module.__file__).resolve(strict=True)
                == native_tree_source_path
                and sha256_type(native_tree_source_path.read_bytes()).hexdigest()
                == native_loader_source_sha256
                and sha256_type(loader_verifier.__code__.co_code).hexdigest()
                == native_loader_verifier_code_sha256
                and loader_verifier(kernel) is True
            )
        except Exception as error:
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH") from error
        if not intact:
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH")

    def require_native_identity() -> None:
        try:
            identity = identity_function()
        except Exception as error:
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH") from error
        if (
            type_fn(identity) is not tuple
            or len(identity) != 6
            or type_fn(identity[0]) is not str
            or type_fn(identity[1]) is not int
            or any_fn(type_fn(value) is not str for value in identity[2:])
            or identity != native_identity
        ):
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH")

    def require_captured_runtime() -> None:
        try:
            fingerprint_closure = inner_registry_fingerprint.__closure__ or ()
            registry_intact = (
                type_fn(inner_callable_state_bindings) is tuple
                and type_fn(expected_inner_callable_count) is int
                and expected_inner_callable_count > 0
                and len(inner_callable_state_bindings)
                == expected_inner_callable_count
                and type_fn(expected_inner_registry_fingerprint) is str
                and len(expected_inner_registry_fingerprint) == 64
                and inner_registry_fingerprint.__code__
                is inner_registry_fingerprint_code
                and inner_registry_fingerprint.__defaults__
                is inner_registry_fingerprint_defaults
                and inner_registry_fingerprint.__kwdefaults__
                == inner_registry_fingerprint_kwdefaults
                and len(fingerprint_closure)
                == len(inner_registry_fingerprint_closure_states)
                and all_fn(
                    current is expected_cell
                    and current.cell_contents is expected_value
                    for current, (expected_cell, expected_value) in zip(
                        fingerprint_closure,
                        inner_registry_fingerprint_closure_states,
                        strict=True,
                    )
                )
                and inner_registry_fingerprint(inner_callable_state_bindings)
                == expected_inner_registry_fingerprint
            )
        except Exception as error:
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH") from error
        if not registry_intact:
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        require_loader_runtime()
        if (
            sys_modules.get(__name__) is not current_module
            or factory_function.__code__ is not factory_code
            or type_fn(kernel) is not verified_kernel_type
            or (
                kernel.manifest_path,
                kernel.backend_evidence_sha256,
                kernel._manifest,
                kernel._module,
                kernel._identity_function,
                kernel._tree_function,
                loader_verifier,
                native_loader_source_sha256,
                native_loader_verifier_code_sha256,
                loader_trust_provenance,
            )
            != kernel_state
            or type_fn(kernel._manifest) is not mapping_proxy_type
            or canonical_evidence_bytes(dict(kernel._manifest)) != manifest_bytes
            or getattr(native_module, "identity_v1", None) is not identity_function
            or getattr(native_module, "tree_eval_v1", None) is not tree_function
        ):
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for name, expected in module_bindings:
            if module_namespace.get(name) is not expected:
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for (
            implementation,
            expected_code,
            expected_defaults,
            expected_kwdefaults,
        ) in module_helper_state_bindings:
            if (
                implementation.__code__ is not expected_code
                or implementation.__defaults__ is not expected_defaults
                or implementation.__kwdefaults__ != expected_kwdefaults
            ):
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for name, expected in constant_bindings:
            current = module_namespace.get(name)
            if type_fn(current) is not type_fn(expected) or current != expected:
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for name, expected in reference_values.items():
            current = getattr(reference_module, name, None)
            if type_fn(current) is not type_fn(expected) or current != expected:
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for owner, name, expected in class_surface_bindings:
            if getattr(owner, name, missing_surface) is not expected:
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for implementation, expected_code in class_code_bindings:
            if implementation.__code__ is not expected_code:
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for owner, mapping, expected_items, field_states in class_field_bindings:
            current_mapping = getattr(owner, "__dataclass_fields__", None)
            if (
                current_mapping is not mapping
                or tuple(current_mapping.items()) != expected_items
                or any_fn(
                    item.name != expected_name
                    or item._field_type is not expected_field_type
                    for item, expected_name, expected_field_type in field_states
                )
            ):
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        for (
            implementation,
            expected_code,
            expected_defaults,
            expected_kwdefaults,
            closure_states,
        ) in inner_callable_state_bindings:
            if (
                implementation.__code__ is not expected_code
                or implementation.__defaults__ is not expected_defaults
                or implementation.__kwdefaults__ != expected_kwdefaults
                or any_fn(
                    cell.cell_contents is not expected
                    for cell, expected in closure_states
                )
            ):
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        require_native_identity()

    def read_input(
        inputs: _CrrCallInputsV1,
    ) -> tuple[dict[str, object], dict[str, object], tuple[object, ...]]:
        if type_fn(inputs) is not call_type:
            raise normalization_error("DELTA_INPUT_MISSING")
        call_values = {
            item.name: call_slots[item.name].__get__(inputs, call_type)
            for item in call_fields
        }
        pit_inputs = call_values["pit_inputs"]
        if type_fn(pit_inputs) is not pit_type:
            raise normalization_error("DELTA_INPUT_MISSING")
        pit_values = {
            item.name: pit_slots[item.name].__get__(pit_inputs, pit_type)
            for item in pit_fields
        }
        snapshot = tuple(call_values[item.name] for item in call_fields) + (
            tuple(pit_values[item.name] for item in pit_fields),
        )
        return call_values, pit_values, snapshot

    def validate_input(
        call_values: dict[str, object],
        pit_values: dict[str, object],
    ) -> tuple[int, int]:
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
                call_values[name], minimum=1, maximum=_MAX_DEFINED_I64
            )
        valuation = require_exact_int(
            call_values["valuation_utc_ns"],
            minimum=1,
            maximum=_MAX_DEFINED_U64,
        )
        expiry = require_exact_int(
            call_values["expiry_utc_ns"],
            minimum=1,
            maximum=_MAX_DEFINED_U64,
        )
        if expiry <= valuation or expiry - valuation > _MAX_MATURITY_NS:
            raise normalization_error("T_OUT_OF_DOMAIN")
        pit_as_of = require_exact_int(
            pit_values["as_of_utc_ns"],
            minimum=1,
            maximum=_MAX_DEFINED_U64,
        )
        for name in (
            "risk_free_rate_ppm",
            "expense_yield_ppm",
            "borrow_yield_ppm",
        ):
            require_exact_int(
                pit_values[name],
                minimum=-_MAX_ABS_RATE_PPM,
                maximum=_MAX_ABS_RATE_PPM,
            )
        for name in (
            "rate_curve_sha256",
            "distribution_assumption_sha256",
            "borrow_assumption_sha256",
        ):
            require_binding_hash(pit_values[name])
        if pit_as_of != valuation:
            raise normalization_error("SNAPSHOT_NOT_CAUSAL")
        return valuation, expiry

    def exact_int(value: int) -> dict[str, str]:
        return {"exact_int": str_fn(value)}

    def input_sha256(
        call_values: dict[str, object],
        pit_values: dict[str, object],
    ) -> str:
        pit_payload = {
            "fields": {
                item.name: (
                    exact_int(pit_values[item.name])
                    if type_fn(pit_values[item.name]) is int
                    else pit_values[item.name]
                )
                for item in pit_fields
            },
            "schema": "CrrPitInputsV1",
        }
        call_payload = {
            "fields": {
                item.name: (
                    pit_payload
                    if item.name == "pit_inputs"
                    else exact_int(call_values[item.name])
                    if type_fn(call_values[item.name]) is int
                    else call_values[item.name]
                )
                for item in call_fields
            },
            "schema": "CrrCallInputsV1",
        }
        envelope = {
            "domain": "gld.crr-call-inputs",
            "hash_format": "GLD_CANONICAL_JSON_V1",
            "payload": call_payload,
            "schema_version": "v1",
        }
        try:
            encoded = json_dumps(
                envelope,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
        except (TypeError, ValueError, UnicodeEncodeError) as error:
            raise normalization_error("DELTA_INPUT_BINDING_MISMATCH") from error
        return sha256_type(encoded).hexdigest()

    def native_tree_eval(
        spot: float,
        strike: float,
        years: float,
        rate: float,
        effective_yield: float,
        volatility: float,
        steps: int,
    ) -> tuple[float, float, int]:
        require_native_identity()
        try:
            raw = tree_function(
                spot,
                strike,
                years,
                rate,
                effective_yield,
                volatility,
                steps,
            )
        except Exception as error:
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH") from error
        if type_fn(raw) is not tuple or len(raw) != 4:
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        status, price, delta, early_nodes = raw
        if (
            type_fn(status) is not int
            or status not in (0, 1, 2, 3)
            or type_fn(early_nodes) is not int
        ):
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        if status == 0:
            if (
                type_fn(price) is not float
                or type_fn(delta) is not float
                or not math_isfinite(price)
                or not math_isfinite(delta)
                or price < 0.0
                or not 0.0 <= delta <= 1.0
                or early_nodes < 0
            ):
                raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
            return price, delta, early_nodes
        if price is not None or delta is not None or early_nodes != 0:
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        if status == 1:
            require_native_identity()
        reason = {
            1: "TREE_NUMERIC_INVALID",
            2: "TREE_PROBABILITY_INVALID",
            3: "DELTA_OUT_OF_RANGE",
        }[status]
        raise normalization_error(reason)

    def suite_evaluation(
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
        first_price, first_delta, first_nodes = native_tree_eval(
            spot,
            strike,
            years,
            rate,
            effective_yield,
            volatility,
            steps[0],
        )
        second_price, second_delta, second_nodes = native_tree_eval(
            spot,
            strike,
            years,
            rate,
            effective_yield,
            volatility,
            steps[1],
        )
        price = (first_price + second_price) * 0.5
        delta = (first_delta + second_delta) * 0.5
        if not math_isfinite(price) or not math_isfinite(delta):
            raise normalization_error("TREE_NUMERIC_INVALID")
        return suite_type(
            volatility=volatility,
            price=price,
            delta=delta,
            price_residual=abs_fn(price - target_price),
            early_exercise_nodes=first_nodes + second_nodes,
        )

    def zero_volatility_american_call_limit(
        *,
        spot: float,
        strike: float,
        years: float,
        rate: float,
        effective_yield: float,
    ) -> float:
        candidate_times = [0.0, years]
        if rate != effective_yield and rate * effective_yield > 0.0:
            ratio = (rate * strike) / (effective_yield * spot)
            if ratio > 0.0 and math_isfinite(ratio):
                stationary_time = math_log(ratio) / (rate - effective_yield)
                if 0.0 < stationary_time < years:
                    candidate_times.append(stationary_time)
        values = (
            spot * math_exp(-effective_yield * time)
            - strike * math_exp(-rate * time)
            for time in candidate_times
        )
        return max_fn(0.0, *values)

    def solve_iv_suite(
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
        low_limit = zero_volatility_american_call_limit(
            spot=spot,
            strike=strike,
            years=years,
            rate=rate,
            effective_yield=effective_yield,
        )
        high = suite_evaluation(
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
            raise normalization_error("IV_NO_BRACKET")
        if high.price - low_limit <= price_tolerance:
            raise normalization_error("IV_UNIDENTIFIABLE")
        if abs_fn(target_price - low_limit) <= price_tolerance:
            raise normalization_error("IV_UNIDENTIFIABLE")

        lower_endpoint: _SuiteEvaluation | None
        try:
            lower_endpoint = suite_evaluation(
                spot=spot,
                strike=strike,
                years=years,
                rate=rate,
                effective_yield=effective_yield,
                volatility=_IV_LOWER,
                target_price=target_price,
                steps=steps,
            )
        except normalization_error as error:
            if error.reason_code != "TREE_PROBABILITY_INVALID":
                raise
            lower_endpoint = None
        if lower_endpoint is not None:
            if target_price < lower_endpoint.price - price_tolerance:
                raise normalization_error("IV_NO_BRACKET")
            if abs_fn(target_price - lower_endpoint.price) <= price_tolerance:
                raise normalization_error("IV_UNIDENTIFIABLE")

        lower_volatility = _IV_LOWER
        upper_volatility = _IV_UPPER
        last_valid: _SuiteEvaluation | None = None
        lowest_valid = lower_endpoint
        for _ in range_fn(_BISECTION_ITERATIONS):
            volatility = (lower_volatility + upper_volatility) * 0.5
            try:
                evaluation = suite_evaluation(
                    spot=spot,
                    strike=strike,
                    years=years,
                    rate=rate,
                    effective_yield=effective_yield,
                    volatility=volatility,
                    target_price=target_price,
                    steps=steps,
                )
            except normalization_error as error:
                if error.reason_code != "TREE_PROBABILITY_INVALID":
                    raise
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
            raise normalization_error("TREE_PROBABILITY_INVALID")
        if lowest_valid is not None:
            if target_price < lowest_valid.price - price_tolerance:
                raise normalization_error("IV_NO_BRACKET")
            if abs_fn(target_price - lowest_valid.price) <= price_tolerance:
                raise normalization_error("IV_UNIDENTIFIABLE")
        if last_valid.price_residual > price_tolerance:
            raise normalization_error("IV_NOT_CONVERGED")
        return last_valid

    def round_half_even_scaled(value: float, scale: int) -> int:
        if not math_isfinite(value):
            raise normalization_error("TREE_NUMERIC_INVALID")
        try:
            context = decimal_context_type(
                prec=_DECIMAL_CONTEXT_PRECISION,
                rounding=decimal_rounding,
                Emin=_DECIMAL_CONTEXT_EMIN,
                Emax=_DECIMAL_CONTEXT_EMAX,
                capitals=_DECIMAL_CONTEXT_CAPITALS,
                clamp=_DECIMAL_CONTEXT_CLAMP,
                flags=[],
                traps=list(decimal_traps),
            )
            with decimal_localcontext(context) as active:
                scaled = active.multiply(
                    decimal_type(str_fn(value)), decimal_type(scale)
                )
                quantized = active.quantize(scaled, decimal_type("1"))
        except decimal_exception_type as error:
            raise normalization_error("TREE_NUMERIC_INVALID") from error
        return int_fn(quantized)

    def numeric_compute(
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
        spot = spot_nano_usd / _NANO_PER_USD
        strike = strike_nano_usd / _NANO_PER_USD
        target_price = option_mid_nano_usd / _NANO_PER_USD
        years = (expiry_utc_ns - valuation_utc_ns) / _YEAR_NS
        rate = risk_free_rate_ppm / _PPM
        effective_yield = (expense_yield_ppm + borrow_yield_ppm) / _PPM
        price_tolerance = max_fn(
            _PRICE_RESIDUAL_FLOOR_USD,
            (tick_nano_usd / _NANO_PER_USD) / 100.0,
        )
        if not all_fn(
            math_isfinite(value)
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
            raise normalization_error("DELTA_INPUT_MISSING")
        coarse = solve_iv_suite(
            spot=spot,
            strike=strike,
            years=years,
            rate=rate,
            effective_yield=effective_yield,
            target_price=target_price,
            price_tolerance=price_tolerance,
            steps=_COARSE_STEPS,
        )
        fine = solve_iv_suite(
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
            abs_fn(fine.volatility - coarse.volatility) > _IV_SUITE_TOLERANCE
            or abs_fn(fine.delta - coarse.delta) > _DELTA_SUITE_TOLERANCE
        ):
            raise normalization_error("TREE_NOT_CONVERGED")
        if not 0.0 <= coarse.delta <= 1.0 or not 0.0 <= fine.delta <= 1.0:
            raise normalization_error("DELTA_OUT_OF_RANGE")
        coarse_iv_ppm = round_half_even_scaled(coarse.volatility, _PPM)
        fine_iv_ppm = round_half_even_scaled(fine.volatility, _PPM)
        coarse_delta_ppm = round_half_even_scaled(coarse.delta, _PPM)
        fine_delta_ppm = round_half_even_scaled(fine.delta, _PPM)
        return (
            fine_iv_ppm,
            fine_delta_ppm,
            coarse_iv_ppm,
            fine_iv_ppm,
            coarse_delta_ppm,
            fine_delta_ppm,
            round_half_even_scaled(coarse.price_residual, _NANO_PER_USD),
            round_half_even_scaled(fine.price_residual, _NANO_PER_USD),
            coarse.early_exercise_nodes,
            fine.early_exercise_nodes,
        )

    ResultPayload = tuple[object, ...]
    InputSnapshot = tuple[object, ...]
    RegistryRecord = tuple[
        weakref.ReferenceType[NativeCrrDeltaResultV1],
        ResultPayload,
        _CrrCallInputsV1,
        InputSnapshot,
        str,
        tuple[object, ...],
    ]
    verified_by_identity: dict[int, RegistryRecord] = {}
    registry_lock = threading.Lock()

    def result_payload(result: NativeCrrDeltaResultV1) -> ResultPayload:
        return tuple(
            result_slots[item.name].__get__(result, result_type)
            for item in result_fields
        )

    def compute(
        inputs: _CrrCallInputsV1,
        *,
        expected_model_sha256: str,
    ) -> NativeCrrDeltaResultV1:
        require_captured_runtime()
        if (
            type_fn(expected_model_sha256) is not str
            or expected_model_sha256 != MODEL_SHA256
        ):
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        call_values, pit_values, bound_snapshot = read_input(inputs)
        valuation, expiry = validate_input(call_values, pit_values)
        bound_input_sha256 = input_sha256(call_values, pit_values)
        require_binding_hash(bound_input_sha256)
        require_captured_runtime()
        numeric = numeric_compute(
            spot_nano_usd=call_values["spot_nano_usd"],
            strike_nano_usd=call_values["strike_nano_usd"],
            option_mid_nano_usd=call_values["option_mid_nano_usd"],
            tick_nano_usd=call_values["tick_nano_usd"],
            valuation_utc_ns=valuation,
            expiry_utc_ns=expiry,
            risk_free_rate_ppm=pit_values["risk_free_rate_ppm"],
            expense_yield_ppm=pit_values["expense_yield_ppm"],
            borrow_yield_ppm=pit_values["borrow_yield_ppm"],
        )
        require_captured_runtime()
        current_call, current_pit, current_snapshot = read_input(inputs)
        if (
            current_snapshot != bound_snapshot
            or input_sha256(current_call, current_pit) != bound_input_sha256
        ):
            raise normalization_error("DELTA_INPUT_BINDING_MISMATCH")
        (
            iv_ppm,
            delta_ppm,
            coarse_iv_ppm,
            fine_iv_ppm,
            coarse_delta_ppm,
            fine_delta_ppm,
            coarse_residual,
            fine_residual,
            coarse_nodes,
            fine_nodes,
        ) = numeric
        for value in (iv_ppm, coarse_iv_ppm, fine_iv_ppm):
            require_exact_int(
                value,
                minimum=100,
                maximum=5_000_000,
                reason_code="TREE_NOT_CONVERGED",
            )
        for value in (delta_ppm, coarse_delta_ppm, fine_delta_ppm):
            require_exact_int(
                value,
                minimum=0,
                maximum=_PPM,
                reason_code="DELTA_OUT_OF_RANGE",
            )
        for value in (coarse_residual, fine_residual, coarse_nodes, fine_nodes):
            require_exact_int(
                value,
                minimum=0,
                maximum=_MAX_DEFINED_I64,
                reason_code="TREE_NOT_CONVERGED",
            )
        result_values = {
            "model_id": MODEL_ID,
            "model_sha256": MODEL_SHA256,
            "model_contract_sha256": MODEL_CONTRACT_SHA256,
            "model_config_sha256": MODEL_CONFIG_SHA256,
            "semantic_reference_source_artifact_sha256": (
                MODEL_SOURCE_ARTIFACT_SHA256
            ),
            "canonical_encoder_source_artifact_sha256": (
                MODEL_CANONICAL_ENCODER_SOURCE_ARTIFACT_SHA256
            ),
            "runtime_fingerprint_sha256": RUNTIME_FINGERPRINT_SHA256,
            "execution_backend_kind": NATIVE_EXECUTION_BACKEND_KIND,
            "semantic_reference_source_executed": False,
            "native_orchestrator_source_artifact_sha256": source_sha256,
            "native_loader_source_artifact_sha256": (
                native_loader_source_sha256
            ),
            "native_loader_verifier_code_sha256": (
                native_loader_verifier_code_sha256
            ),
            "native_build_backend_evidence_sha256": native_build_evidence,
            "native_runtime_environment_sha256": (
                native_runtime_environment_sha256
            ),
            "native_abi_identity_sha256": native_abi_identity_sha256,
            "combined_backend_evidence_sha256": (
                combined_backend_evidence_sha256
            ),
            "input_sha256": bound_input_sha256,
            "option_snapshot_sha256": call_values["option_snapshot_sha256"],
            "contract_id": call_values["contract_id"],
            "iv_ppm": iv_ppm,
            "delta_ppm": delta_ppm,
            "coarse_iv_ppm": coarse_iv_ppm,
            "fine_iv_ppm": fine_iv_ppm,
            "coarse_delta_ppm": coarse_delta_ppm,
            "fine_delta_ppm": fine_delta_ppm,
            "coarse_price_residual_nano_usd": coarse_residual,
            "fine_price_residual_nano_usd": fine_residual,
            "coarse_early_exercise_nodes": coarse_nodes,
            "fine_early_exercise_nodes": fine_nodes,
        }
        result = allocate(result_type)
        for name, value in result_values.items():
            result_slots[name].__set__(result, value)
        if (
            type_fn(result) is not result_type
            or result_payload(result)
            != tuple(result_values[item.name] for item in result_fields)
        ):
            raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
        require_captured_runtime()
        identity = id(result)

        def discard(
            reference: weakref.ReferenceType[NativeCrrDeltaResultV1],
        ) -> None:
            with registry_lock:
                record = verified_by_identity.get(identity)
                if record is not None and record[0] is reference:
                    verified_by_identity.pop(identity, None)

        reference = make_reference(result, discard)
        with registry_lock:
            verified_by_identity[identity] = (
                reference,
                result_payload(result),
                inputs,
                bound_snapshot,
                bound_input_sha256,
                kernel_state,
            )
        return result

    def is_verified(value: object) -> bool:
        try:
            require_captured_runtime()
        except Exception:
            return False
        if type_fn(value) is not result_type:
            return False
        with registry_lock:
            record = verified_by_identity.get(id(value))
        if (
            record is None
            or record[0]() is not value
            or record[1] != result_payload(value)
            or record[5] != kernel_state
        ):
            return False
        try:
            current_call, current_pit, current_snapshot = read_input(record[2])
            current_input_sha256 = input_sha256(current_call, current_pit)
            return (
                current_snapshot == record[3]
                and current_input_sha256 == record[4]
                and result_slots["input_sha256"].__get__(value, result_type)
                == current_input_sha256
                and result_slots[
                    "combined_backend_evidence_sha256"
                ].__get__(value, result_type)
                == combined_backend_evidence_sha256
                and result_slots[
                    "native_build_backend_evidence_sha256"
                ].__get__(value, result_type)
                == native_build_evidence
                and result_slots[
                    "native_loader_source_artifact_sha256"
                ].__get__(value, result_type)
                == native_loader_source_sha256
                and result_slots[
                    "native_loader_verifier_code_sha256"
                ].__get__(value, result_type)
                == native_loader_verifier_code_sha256
                and result_slots["native_abi_identity_sha256"].__get__(
                    value, result_type
                )
                == native_abi_identity_sha256
            )
        except Exception:
            return False

    inner_callables = (
        inner_registry_fingerprint,
        require_loader_runtime,
        require_native_identity,
        require_captured_runtime,
        read_input,
        validate_input,
        exact_int,
        input_sha256,
        native_tree_eval,
        suite_evaluation,
        zero_volatility_american_call_limit,
        solve_iv_suite,
        round_half_even_scaled,
        numeric_compute,
        result_payload,
        compute,
        is_verified,
    )
    expected_inner_callable_count = len(inner_callables)
    inner_callable_state_bindings = tuple(
        (
            implementation,
            implementation.__code__,
            implementation.__defaults__,
            None
            if implementation.__kwdefaults__ is None
            else dict(implementation.__kwdefaults__),
            tuple(
                (cell, cell.cell_contents)
                for name, cell in zip(
                    implementation.__code__.co_freevars,
                    implementation.__closure__ or (),
                    strict=True,
                )
                if name
                not in {
                    "inner_callable_state_bindings",
                    "expected_inner_registry_fingerprint",
                }
            ),
        )
        for implementation in inner_callables
    )
    computed_registry_fingerprint = inner_registry_fingerprint(
        inner_callable_state_bindings
    )
    if computed_registry_fingerprint is None:
        raise normalization_error("DELTA_MODEL_HASH_MISMATCH")
    expected_inner_registry_fingerprint = computed_registry_fingerprint

    return compute, is_verified, combined_backend_evidence_sha256


__all__ = (
    "NATIVE_CRR_BACKEND_EVIDENCE_SCHEMA",
    "NATIVE_CRR_EXECUTION_STATUS",
    "NATIVE_EXECUTION_BACKEND_KIND",
    "NATIVE_LOADER_TRUST_PROVENANCE",
    "NATIVE_ORCHESTRATOR_SOURCE_ARTIFACT_SHA256",
    "NATIVE_TREE_LOADER_SOURCE_ARTIFACT_SHA256",
    "NATIVE_TREE_VERIFIER_CODE_SHA256",
    "SEMANTIC_REFERENCE_SOURCE_EXECUTED",
    "NativeCrrDeltaResultV1",
    "create_native_crr_delta_engine",
    "derive_native_crr_combined_backend_evidence_sha256",
)
