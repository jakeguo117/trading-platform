"""Strict loader and typed wrapper for the CRR native shadow tree kernel.

This module never builds an extension and never falls back to the Python
reference tree.  It is intentionally not wired into ``crr_delta``: the first
native milestone is differential shadow evidence only.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import importlib.machinery
import importlib.util
import json
import math
from pathlib import Path
import platform
import re
import subprocess
import sys
import sysconfig
from threading import RLock
from types import BuiltinFunctionType, MappingProxyType, ModuleType
from typing import Callable, Mapping, Sequence

from gld_normalizer.errors import NormalizationError


NATIVE_ABI_ID = "GLD_CRR_TREE_KERNEL_ABI_V1"
NATIVE_ABI_VERSION = 1
NATIVE_MANIFEST_SCHEMA = "GLD_CRR_NATIVE_BUILD_MANIFEST_V1"
SOURCE_MANIFEST_SCHEMA = "GLD_CRR_NATIVE_SOURCE_MANIFEST_V1"
DEPENDENCY_MANIFEST_SCHEMA = "GLD_CRR_NATIVE_DEPENDENCY_MANIFEST_V1"
PYTHON_HEADER_MANIFEST_SCHEMA = "GLD_CRR_NATIVE_PYTHON_HEADERS_V1"
RUNTIME_ENVIRONMENT_SCHEMA = "GLD_CRR_NATIVE_RUNTIME_ENVIRONMENT_V1"
TARGET_ENVIRONMENT_SCHEMA = "GLD_CRR_NATIVE_TARGET_ENVIRONMENT_V1"
BUILD_INPUT_SCHEMA = "GLD_CRR_NATIVE_BUILD_INPUT_V1"
BUILD_EVIDENCE_SCHEMA = "GLD_CRR_NATIVE_BACKEND_EVIDENCE_V1"

CLANG_PATH = Path("/usr/bin/clang")
OTOOL_PATH = Path("/usr/bin/otool")
XCRUN_PATH = Path("/usr/bin/xcrun")
SW_VERS_PATH = Path("/usr/bin/sw_vers")
SOURCE_TOKEN = "$REPO/native/crr_tree_kernel_v1.c"
_MODULE_NAME = "_crr_tree_kernel"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

FROZEN_COMPILER_FLAGS = (
    "-target",
    "arm64-apple-macosx26.0",
    "-mcpu=apple-m4",
    "-std=c17",
    "-O3",
    "-DNDEBUG",
    "-Wall",
    "-Wextra",
    "-Wpedantic",
    "-Werror",
    "-fno-fast-math",
    "-ffp-contract=off",
    "-frounding-math",
    "-fno-associative-math",
    "-fno-unsafe-math-optimizations",
    "-fno-finite-math-only",
    "-fno-reciprocal-math",
    "-fno-vectorize",
    "-fno-slp-vectorize",
    "-fno-builtin-exp",
    "-fno-builtin-log",
    "-fno-builtin-sqrt",
    "-fno-common",
    "-fvisibility=hidden",
    "-fstack-protector-strong",
    "-bundle",
    "-undefined",
    "dynamic_lookup",
)

_MANIFEST_KEYS = {
    "abi_id",
    "abi_version",
    "backend_evidence_sha256",
    "binary_filename",
    "binary_sha256",
    "binary_size",
    "build_input_sha256",
    "build_manifest_sha256",
    "compile_command_sha256",
    "compiler_executable",
    "compiler_executable_sha256",
    "compiler_flags",
    "compiler_flags_sha256",
    "compiler_version_sha256",
    "dependency_manifest",
    "dependency_manifest_sha256",
    "otool_dependency_sha256",
    "python_header_manifest",
    "python_header_manifest_sha256",
    "runtime_environment",
    "runtime_environment_sha256",
    "schema_version",
    "soabi",
    "source_manifest_sha256",
    "source_path",
    "source_sha256",
    "target_environment",
    "target_environment_sha256",
}
_BASE_MANIFEST_KEYS = (
    "abi_id",
    "abi_version",
    "binary_filename",
    "build_input_sha256",
    "compile_command_sha256",
    "compiler_executable",
    "compiler_executable_sha256",
    "compiler_flags",
    "compiler_flags_sha256",
    "compiler_version_sha256",
    "dependency_manifest",
    "dependency_manifest_sha256",
    "python_header_manifest",
    "python_header_manifest_sha256",
    "runtime_environment",
    "runtime_environment_sha256",
    "schema_version",
    "soabi",
    "source_manifest_sha256",
    "source_path",
    "source_sha256",
    "target_environment",
    "target_environment_sha256",
)
_BINARY_MANIFEST_KEYS = (
    "binary_sha256",
    "binary_size",
    "otool_dependency_sha256",
)
_RUNTIME_KEYS = {
    "byteorder",
    "ext_suffix",
    "float_mant_dig",
    "float_rounds",
    "machine",
    "operating_system",
    "operating_system_release",
    "python_cache_tag",
    "python_implementation",
    "python_version",
    "schema_version",
    "soabi",
}
_TARGET_KEYS = {
    "os_build",
    "schema_version",
    "sdk_version",
    "target_cpu",
    "target_triple",
}
_KNOWN_ANSWER_INPUT = (123.5, 117.25, 0.375, 0.017, 0.006, 0.41, 17)
_KNOWN_ANSWER_PRICE_HEX = "0x1.f1a42b62be278p+3"
_KNOWN_ANSWER_DELTA_HEX = "0x1.45cbb5f7e4b12p-1"
_KNOWN_ANSWER_EARLY_NODES = 0


def _fail() -> NormalizationError:
    return NormalizationError("DELTA_MODEL_HASH_MISMATCH")


def _canonical_json_bytes(value: object) -> bytes:
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
        raise _fail() from error


def _canonical_sha256(value: object) -> str:
    return sha256(_canonical_json_bytes(value)).hexdigest()


def _exact_json_equal(first: object, second: object) -> bool:
    """Compare JSON values without Python's ``True == 1`` type coercion."""

    if type(first) is not type(second):
        return False
    if type(first) is dict:
        return set(first) == set(second) and all(
            _exact_json_equal(first[key], second[key]) for key in first
        )
    if type(first) is list:
        return len(first) == len(second) and all(
            _exact_json_equal(left, right)
            for left, right in zip(first, second, strict=True)
        )
    return first == second


def _require_sha256(value: object) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise _fail()
    return value


def _file_sha256(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise _fail() from error
    return digest.hexdigest()


def _run(command: Sequence[str]) -> bytes:
    try:
        completed = subprocess.run(
            tuple(command),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            },
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise _fail() from error
    return completed.stdout


def _single_line(command: Sequence[str]) -> str:
    try:
        value = _run(command).decode("utf-8", "strict").strip()
    except UnicodeDecodeError as error:
        raise _fail() from error
    if not value or "\x00" in value:
        raise _fail()
    return value


def _runtime_environment() -> dict[str, object]:
    return {
        "byteorder": sys.byteorder,
        "ext_suffix": sysconfig.get_config_var("EXT_SUFFIX"),
        "float_mant_dig": sys.float_info.mant_dig,
        "float_rounds": sys.float_info.rounds,
        "machine": platform.machine(),
        "operating_system": platform.system(),
        "operating_system_release": platform.release(),
        "python_cache_tag": sys.implementation.cache_tag,
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "schema_version": RUNTIME_ENVIRONMENT_SCHEMA,
        "soabi": sysconfig.get_config_var("SOABI"),
    }


def _target_environment() -> dict[str, object]:
    return {
        "os_build": _single_line((str(SW_VERS_PATH), "-buildVersion")),
        "schema_version": TARGET_ENVIRONMENT_SCHEMA,
        "sdk_version": _single_line((str(XCRUN_PATH), "--show-sdk-version")),
        "target_cpu": "apple-m4",
        "target_triple": "arm64-apple-macosx26.0",
    }


def _compile_command_template(binary_filename: str) -> list[str]:
    return [
        "$CLANG",
        *FROZEN_COMPILER_FLAGS,
        "-isystem",
        "$PYTHON_INCLUDE",
        '-DGLD_SOURCE_MANIFEST_SHA256="$SOURCE_MANIFEST_SHA256"',
        '-DGLD_BUILD_INPUT_SHA256="$BUILD_INPUT_SHA256"',
        '-DGLD_COMPILER_FLAGS_SHA256="$COMPILER_FLAGS_SHA256"',
        '-DGLD_DEPENDENCY_MANIFEST_SHA256="$DEPENDENCY_MANIFEST_SHA256"',
        SOURCE_TOKEN,
        "-o",
        f"$OUTPUT/{binary_filename}",
    ]


def _resolve_token(
    token: str,
    *,
    repo_root: Path,
    python_include: Path,
    sdk_root: Path,
    clang_resource: Path,
) -> Path:
    roots = {
        "$REPO": repo_root,
        "$PYTHON_INCLUDE": python_include,
        "$SDKROOT": sdk_root,
        "$CLANG_RESOURCE": clang_resource,
    }
    for prefix, root in roots.items():
        if token == prefix:
            relative = ""
        elif token.startswith(prefix + "/"):
            relative = token[len(prefix) + 1 :]
        else:
            continue
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            if relative:
                raise _fail()
        resolved = (root / relative).resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise _fail() from error
        return resolved
    raise _fail()


def _validate_entries(
    value: object,
    *,
    repo_root: Path,
    python_include: Path,
    sdk_root: Path,
    clang_resource: Path,
) -> list[dict[str, str]]:
    if type(value) is not list:
        raise _fail()
    normalized: list[dict[str, str]] = []
    prior_path = ""
    for item in value:
        if type(item) is not dict or set(item) != {"path", "sha256"}:
            raise _fail()
        token = item["path"]
        expected_sha256 = _require_sha256(item["sha256"])
        if type(token) is not str or not token or token <= prior_path:
            raise _fail()
        path = _resolve_token(
            token,
            repo_root=repo_root,
            python_include=python_include,
            sdk_root=sdk_root,
            clang_resource=clang_resource,
        )
        if _file_sha256(path) != expected_sha256:
            raise _fail()
        normalized.append({"path": token, "sha256": expected_sha256})
        prior_path = token
    return normalized


def _normalized_otool_sha256(binary_path: Path) -> str:
    try:
        lines = _run((str(OTOOL_PATH), "-L", str(binary_path))).decode(
            "utf-8", "strict"
        ).splitlines()
    except UnicodeDecodeError as error:
        raise _fail() from error
    if not lines:
        raise _fail()
    normalized = ["$BINARY:"]
    normalized.extend(line.rstrip() for line in lines[1:])
    return sha256(("\n".join(normalized) + "\n").encode("utf-8")).hexdigest()


def _build_manifest_sha256(document: Mapping[str, object]) -> str:
    try:
        payload = {
            key: document[key]
            for key in (*_BASE_MANIFEST_KEYS, *_BINARY_MANIFEST_KEYS)
        }
    except KeyError as error:
        raise _fail() from error
    return _canonical_sha256(payload)


def _backend_evidence_sha256(document: Mapping[str, object]) -> str:
    try:
        payload = {
            "binary_sha256": document["binary_sha256"],
            "build_input_sha256": document["build_input_sha256"],
            "build_manifest_sha256": document["build_manifest_sha256"],
            "dependency_manifest_sha256": document[
                "dependency_manifest_sha256"
            ],
            "runtime_environment_sha256": document[
                "runtime_environment_sha256"
            ],
            "schema_version": BUILD_EVIDENCE_SCHEMA,
            "source_manifest_sha256": document["source_manifest_sha256"],
            "target_environment_sha256": document[
                "target_environment_sha256"
            ],
        }
    except KeyError as error:
        raise _fail() from error
    return _canonical_sha256(payload)


def _load_manifest_bytes(manifest_path: Path) -> dict[str, object]:
    try:
        manifest_bytes = manifest_path.read_bytes()
        document = json.loads(manifest_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _fail() from error
    if (
        type(document) is not dict
        or set(document) != _MANIFEST_KEYS
        or manifest_bytes != _canonical_json_bytes(document)
    ):
        raise _fail()
    return document


def _verify_manifest(
    manifest_path: Path,
    *,
    expected_backend_evidence_sha256: str,
) -> tuple[dict[str, object], Path, tuple[int, int]]:
    expected_backend = _require_sha256(expected_backend_evidence_sha256)
    document = _load_manifest_bytes(manifest_path)
    if (
        document["schema_version"] != NATIVE_MANIFEST_SCHEMA
        or document["abi_id"] != NATIVE_ABI_ID
        or type(document["abi_version"]) is not int
        or document["abi_version"] != NATIVE_ABI_VERSION
        or document["compiler_executable"] != "$CLANG"
        or document["source_path"] != SOURCE_TOKEN
        or document["backend_evidence_sha256"] != expected_backend
        or document["soabi"] != sysconfig.get_config_var("SOABI")
    ):
        raise _fail()
    for key in (
        "backend_evidence_sha256",
        "binary_sha256",
        "build_input_sha256",
        "build_manifest_sha256",
        "compile_command_sha256",
        "compiler_executable_sha256",
        "compiler_flags_sha256",
        "compiler_version_sha256",
        "dependency_manifest_sha256",
        "otool_dependency_sha256",
        "python_header_manifest_sha256",
        "runtime_environment_sha256",
        "source_manifest_sha256",
        "source_sha256",
        "target_environment_sha256",
    ):
        _require_sha256(document[key])
    if type(document["binary_size"]) is not int or document["binary_size"] <= 0:
        raise _fail()
    if (
        type(document["compiler_flags"]) is not list
        or tuple(document["compiler_flags"]) != FROZEN_COMPILER_FLAGS
        or _canonical_sha256(document["compiler_flags"])
        != document["compiler_flags_sha256"]
    ):
        raise _fail()

    repo_root = Path(__file__).resolve().parents[2]
    source_path = repo_root / "native" / "crr_tree_kernel_v1.c"
    source_sha256 = _file_sha256(source_path)
    if source_sha256 != document["source_sha256"]:
        raise _fail()
    source_manifest_sha256 = _canonical_sha256(
        {
            "schema_version": SOURCE_MANIFEST_SCHEMA,
            "source_path": SOURCE_TOKEN,
            "source_sha256": source_sha256,
        }
    )
    if source_manifest_sha256 != document["source_manifest_sha256"]:
        raise _fail()

    try:
        python_include = Path(sysconfig.get_paths()["include"]).resolve(strict=True)
        sdk_root = Path(
            _single_line((str(XCRUN_PATH), "--show-sdk-path"))
        ).resolve(strict=True)
        clang_resource = Path(
            _single_line((str(CLANG_PATH), "-print-resource-dir"))
        ).resolve(strict=True)
    except (KeyError, OSError) as error:
        raise _fail() from error
    dependencies = _validate_entries(
        document["dependency_manifest"],
        repo_root=repo_root,
        python_include=python_include,
        sdk_root=sdk_root,
        clang_resource=clang_resource,
    )
    headers = _validate_entries(
        document["python_header_manifest"],
        repo_root=repo_root,
        python_include=python_include,
        sdk_root=sdk_root,
        clang_resource=clang_resource,
    )
    if any(entry not in dependencies for entry in headers):
        raise _fail()
    if (
        _canonical_sha256(
            {
                "dependencies": dependencies,
                "schema_version": DEPENDENCY_MANIFEST_SCHEMA,
            }
        )
        != document["dependency_manifest_sha256"]
        or _canonical_sha256(
            {
                "headers": headers,
                "schema_version": PYTHON_HEADER_MANIFEST_SCHEMA,
            }
        )
        != document["python_header_manifest_sha256"]
    ):
        raise _fail()

    runtime_environment = _runtime_environment()
    target_environment = _target_environment()
    if (
        type(document["runtime_environment"]) is not dict
        or set(document["runtime_environment"]) != _RUNTIME_KEYS
        or not _exact_json_equal(
            document["runtime_environment"],
            runtime_environment,
        )
        or _canonical_sha256(runtime_environment)
        != document["runtime_environment_sha256"]
        or type(document["target_environment"]) is not dict
        or set(document["target_environment"]) != _TARGET_KEYS
        or not _exact_json_equal(
            document["target_environment"],
            target_environment,
        )
        or _canonical_sha256(target_environment)
        != document["target_environment_sha256"]
    ):
        raise _fail()
    if (
        _file_sha256(CLANG_PATH) != document["compiler_executable_sha256"]
        or sha256(_run((str(CLANG_PATH), "--version"))).hexdigest()
        != document["compiler_version_sha256"]
    ):
        raise _fail()
    binary_filename = document["binary_filename"]
    expected_suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if (
        type(binary_filename) is not str
        or binary_filename != f"{_MODULE_NAME}{expected_suffix}"
        or "/" in binary_filename
        or "\\" in binary_filename
    ):
        raise _fail()
    if (
        _canonical_sha256(_compile_command_template(binary_filename))
        != document["compile_command_sha256"]
    ):
        raise _fail()
    build_input_sha256 = _canonical_sha256(
        {
            "compile_command_sha256": document["compile_command_sha256"],
            "compiler_executable_sha256": document[
                "compiler_executable_sha256"
            ],
            "compiler_flags_sha256": document["compiler_flags_sha256"],
            "compiler_version_sha256": document["compiler_version_sha256"],
            "dependency_manifest_sha256": document[
                "dependency_manifest_sha256"
            ],
            "python_header_manifest_sha256": document[
                "python_header_manifest_sha256"
            ],
            "runtime_environment_sha256": document[
                "runtime_environment_sha256"
            ],
            "schema_version": BUILD_INPUT_SCHEMA,
            "source_manifest_sha256": document["source_manifest_sha256"],
            "target_environment_sha256": document[
                "target_environment_sha256"
            ],
        }
    )
    if build_input_sha256 != document["build_input_sha256"]:
        raise _fail()

    binary_path = (manifest_path.parent / binary_filename).resolve(strict=True)
    if binary_path.parent != manifest_path.parent.resolve(strict=True):
        raise _fail()
    before = binary_path.stat()
    before_identity = (before.st_ino, before.st_size)
    if (
        before.st_size != document["binary_size"]
        or _file_sha256(binary_path) != document["binary_sha256"]
        or _normalized_otool_sha256(binary_path)
        != document["otool_dependency_sha256"]
        or _build_manifest_sha256(document) != document["build_manifest_sha256"]
        or _backend_evidence_sha256(document)
        != document["backend_evidence_sha256"]
    ):
        raise _fail()
    return document, binary_path, before_identity


def verify_native_tree_manifest_v1(
    manifest_path: Path,
    *,
    expected_backend_evidence_sha256: str,
) -> Mapping[str, object]:
    """Verify one build manifest and binary without importing the extension.

    This read-only boundary performs the same manifest, source, dependency,
    runtime, binary hash, size, and linkage checks used by the native loader.
    It deliberately stops before ``exec_module`` and returns an immutable
    canonical manifest for parent-side evidence derivation.
    """

    try:
        resolved_manifest_path = Path(manifest_path).resolve(strict=True)
        document, binary_path, before_identity = _verify_manifest(
            resolved_manifest_path,
            expected_backend_evidence_sha256=(
                expected_backend_evidence_sha256
            ),
        )
        after = binary_path.stat()
        if (
            (after.st_ino, after.st_size) != before_identity
            or _file_sha256(binary_path) != document["binary_sha256"]
        ):
            raise _fail()
        return MappingProxyType(dict(document))
    except NormalizationError as error:
        if error.reason_code == "DELTA_MODEL_HASH_MISMATCH":
            raise
        raise _fail() from error
    except Exception as error:
        raise _fail() from error


def validate_native_identity_v1(
    identity: object,
    manifest: Mapping[str, object],
) -> tuple[str, int, str, str, str, str]:
    """Validate the six-field extension identity against one verified manifest."""

    if type(identity) is not tuple or len(identity) != 6:
        raise _fail()
    if (
        type(identity[0]) is not str
        or type(identity[1]) is not int
        or any(type(value) is not str for value in identity[2:])
    ):
        raise _fail()
    expected = (
        NATIVE_ABI_ID,
        NATIVE_ABI_VERSION,
        manifest.get("source_manifest_sha256"),
        manifest.get("build_input_sha256"),
        manifest.get("compiler_flags_sha256"),
        manifest.get("dependency_manifest_sha256"),
    )
    if identity != expected:
        raise _fail()
    return identity


def decode_native_tree_result_v1(
    raw: object,
) -> tuple[int, float | None, float | None, int]:
    """Reject every result outside the frozen four-field native schema."""

    if type(raw) is not tuple or len(raw) != 4:
        raise _fail()
    status, price, delta, early_exercise_nodes = raw
    if type(status) is not int or status not in (0, 1, 2, 3):
        raise _fail()
    if type(early_exercise_nodes) is not int:
        raise _fail()
    if status == 0:
        if (
            type(price) is not float
            or type(delta) is not float
            or not math.isfinite(price)
            or not math.isfinite(delta)
            or price < 0.0
            or not 0.0 <= delta <= 1.0
            or early_exercise_nodes < 0
        ):
            raise _fail()
    elif price is not None or delta is not None or early_exercise_nodes != 0:
        raise _fail()
    return status, price, delta, early_exercise_nodes


@dataclass(frozen=True, slots=True)
class NativeTreeEvaluationV1:
    price: float
    delta: float
    early_exercise_nodes: int


@dataclass(frozen=True, slots=True, init=False)
class VerifiedNativeTreeV1:
    manifest_path: Path
    backend_evidence_sha256: str
    _manifest: Mapping[str, object]
    _module: ModuleType
    _identity_function: Callable[[], object]
    _tree_function: Callable[..., object]

    def __new__(cls, *args: object, **kwargs: object) -> VerifiedNativeTreeV1:
        raise TypeError("VerifiedNativeTreeV1 is loader-constructed only")

    def __reduce__(self) -> object:
        raise TypeError("VerifiedNativeTreeV1 cannot be serialized")

    def __reduce_ex__(self, protocol: int) -> object:
        raise TypeError("VerifiedNativeTreeV1 cannot be serialized")

    def __copy__(self) -> object:
        raise TypeError("VerifiedNativeTreeV1 cannot be copied")

    def __deepcopy__(self, memo: object) -> object:
        raise TypeError("VerifiedNativeTreeV1 cannot be copied")

    def identity_v1(self) -> tuple[str, int, str, str, str, str]:
        try:
            identity = self._identity_function()
        except Exception as error:
            raise _fail() from error
        return validate_native_identity_v1(identity, self._manifest)

    def raw_tree_eval_v1(
        self,
        spot: float,
        strike: float,
        years: float,
        rate: float,
        effective_yield: float,
        volatility: float,
        steps: int,
        /,
    ) -> tuple[int, float | None, float | None, int]:
        # The native identity function also verifies IEEE-754 and FE_TONEAREST.
        # Calling it on both sides of a numeric-invalid result separates an
        # input-domain failure from floating-point environment drift without
        # adding a third native API or a ctypes side channel.
        self.identity_v1()
        try:
            raw = self._tree_function(
                spot,
                strike,
                years,
                rate,
                effective_yield,
                volatility,
                steps,
            )
        except Exception as error:
            raise _fail() from error
        decoded = decode_native_tree_result_v1(raw)
        if decoded[0] == 1:
            self.identity_v1()
        return decoded

    def tree_eval_v1(
        self,
        spot: float,
        strike: float,
        years: float,
        rate: float,
        effective_yield: float,
        volatility: float,
        steps: int,
        /,
    ) -> NativeTreeEvaluationV1:
        status, price, delta, early_exercise_nodes = self.raw_tree_eval_v1(
            spot,
            strike,
            years,
            rate,
            effective_yield,
            volatility,
            steps,
        )
        if status != 0:
            reason = {
                1: "TREE_NUMERIC_INVALID",
                2: "TREE_PROBABILITY_INVALID",
                3: "DELTA_OUT_OF_RANGE",
            }[status]
            raise NormalizationError(reason)
        assert price is not None and delta is not None
        return NativeTreeEvaluationV1(
            price=price,
            delta=delta,
            early_exercise_nodes=early_exercise_nodes,
        )


_LOADED_KERNEL: VerifiedNativeTreeV1 | None = None


def _load_extension(
    binary_path: Path,
    document: Mapping[str, object],
) -> tuple[ModuleType, Callable[[], object], Callable[..., object]]:
    if _MODULE_NAME in sys.modules:
        raise _fail()
    try:
        loader = importlib.machinery.ExtensionFileLoader(
            _MODULE_NAME,
            str(binary_path),
        )
        spec = importlib.util.spec_from_file_location(
            _MODULE_NAME,
            binary_path,
            loader=loader,
        )
        if spec is None or spec.loader is not loader:
            raise _fail()
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
    except Exception as error:
        raise _fail() from error
    if Path(module.__file__).resolve(strict=True) != binary_path:
        raise _fail()
    identity_function = getattr(module, "identity_v1", None)
    tree_function = getattr(module, "tree_eval_v1", None)
    if (
        type(identity_function) is not BuiltinFunctionType
        or type(tree_function) is not BuiltinFunctionType
        or getattr(identity_function, "__self__", None) is not module
        or getattr(tree_function, "__self__", None) is not module
    ):
        raise _fail()
    try:
        identity = identity_function()
    except Exception as error:
        raise _fail() from error
    validate_native_identity_v1(identity, document)
    return module, identity_function, tree_function


def _make_native_tree_loader() -> tuple[
    Callable[..., VerifiedNativeTreeV1],
    Callable[[object], bool],
]:
    """Create one closure-held process trust registry and loader capability."""

    lock = RLock()
    trusted_kernel: VerifiedNativeTreeV1 | None = None
    # Exact object references plus immutable evidence captured only after KAT.
    attestation: tuple[object, ...] | None = None
    revoked = False

    verify_manifest = _verify_manifest
    load_extension = _load_extension
    file_sha256 = _file_sha256
    canonical_json_bytes = _canonical_json_bytes
    validate_identity = validate_native_identity_v1
    fail = _fail

    def is_verified_native_tree_v1(value: object) -> bool:
        """Return true only for this process's intact, KAT-verified instance."""

        nonlocal revoked
        with lock:
            if (
                revoked
                or type(value) is not VerifiedNativeTreeV1
                or value is not trusted_kernel
                or attestation is None
            ):
                return False
            (
                manifest_path,
                backend_evidence_sha256,
                manifest,
                module,
                identity_function,
                tree_function,
                manifest_bytes,
                binary_path,
                binary_identity,
                abi_identity,
            ) = attestation
            try:
                fields_intact = (
                    value.manifest_path is manifest_path
                    and value.backend_evidence_sha256
                    is backend_evidence_sha256
                    and value._manifest is manifest
                    and value._module is module
                    and value._identity_function is identity_function
                    and value._tree_function is tree_function
                )
                module_intact = (
                    type(module) is ModuleType
                    and getattr(module, "identity_v1", None)
                    is identity_function
                    and getattr(module, "tree_eval_v1", None) is tree_function
                    and type(identity_function) is BuiltinFunctionType
                    and type(tree_function) is BuiltinFunctionType
                    and getattr(identity_function, "__self__", None) is module
                    and getattr(tree_function, "__self__", None) is module
                    and Path(module.__file__).resolve(strict=True) == binary_path
                )
                binary_stat = binary_path.stat()
                binary_intact = (
                    (binary_stat.st_ino, binary_stat.st_size) == binary_identity
                    and file_sha256(binary_path)
                    == manifest.get("binary_sha256")
                )
                evidence_intact = (
                    manifest_path.read_bytes() == manifest_bytes
                    and canonical_json_bytes(dict(manifest)) == manifest_bytes
                    and manifest.get("backend_evidence_sha256")
                    == backend_evidence_sha256
                )
                current_identity = identity_function()
                identity_intact = (
                    current_identity == abi_identity
                    and validate_identity(current_identity, manifest)
                    == abi_identity
                )
            except Exception:
                fields_intact = False
                module_intact = False
                binary_intact = False
                evidence_intact = False
                identity_intact = False
            if not (
                fields_intact
                and module_intact
                and binary_intact
                and evidence_intact
                and identity_intact
            ):
                revoked = True
                return False
            return True

    def load_native_tree_v1(
        manifest_path: Path,
        *,
        expected_backend_evidence_sha256: str,
    ) -> VerifiedNativeTreeV1:
        """Load one exact prebuilt pair and register only after its KAT."""

        nonlocal attestation, revoked, trusted_kernel
        global _LOADED_KERNEL
        with lock:
            try:
                resolved_manifest_path = Path(manifest_path).resolve(strict=True)
                document, binary_path, before_identity = verify_manifest(
                    resolved_manifest_path,
                    expected_backend_evidence_sha256=(
                        expected_backend_evidence_sha256
                    ),
                )
                verified_manifest_bytes = canonical_json_bytes(document)
                if resolved_manifest_path.read_bytes() != verified_manifest_bytes:
                    raise fail()
                if trusted_kernel is not None:
                    if (
                        not revoked
                        and trusted_kernel.manifest_path
                        == resolved_manifest_path
                        and trusted_kernel.backend_evidence_sha256
                        == expected_backend_evidence_sha256
                        and is_verified_native_tree_v1(trusted_kernel)
                    ):
                        _LOADED_KERNEL = trusted_kernel
                        return trusted_kernel
                    raise fail()
                module, identity_function, tree_function = load_extension(
                    binary_path,
                    document,
                )
                after = binary_path.stat()
                if (
                    (after.st_ino, after.st_size) != before_identity
                    or file_sha256(binary_path) != document["binary_sha256"]
                ):
                    raise fail()
                kernel = object.__new__(VerifiedNativeTreeV1)
                manifest = MappingProxyType(dict(document))
                object.__setattr__(
                    kernel,
                    "manifest_path",
                    resolved_manifest_path,
                )
                object.__setattr__(
                    kernel,
                    "backend_evidence_sha256",
                    expected_backend_evidence_sha256,
                )
                object.__setattr__(kernel, "_manifest", manifest)
                object.__setattr__(kernel, "_module", module)
                object.__setattr__(
                    kernel,
                    "_identity_function",
                    identity_function,
                )
                object.__setattr__(kernel, "_tree_function", tree_function)
                known = kernel.raw_tree_eval_v1(*_KNOWN_ANSWER_INPUT)
                if (
                    known[0] != 0
                    or known[1] is None
                    or known[2] is None
                    or known[1].hex() != _KNOWN_ANSWER_PRICE_HEX
                    or known[2].hex() != _KNOWN_ANSWER_DELTA_HEX
                    or known[3] != _KNOWN_ANSWER_EARLY_NODES
                ):
                    raise fail()
                abi_identity = kernel.identity_v1()
                if resolved_manifest_path.read_bytes() != verified_manifest_bytes:
                    raise fail()
                trusted_kernel = kernel
                attestation = (
                    kernel.manifest_path,
                    kernel.backend_evidence_sha256,
                    kernel._manifest,
                    kernel._module,
                    kernel._identity_function,
                    kernel._tree_function,
                    verified_manifest_bytes,
                    binary_path,
                    before_identity,
                    abi_identity,
                )
                if not is_verified_native_tree_v1(kernel):
                    raise fail()
                _LOADED_KERNEL = kernel
                return kernel
            except NormalizationError as error:
                if error.reason_code == "DELTA_MODEL_HASH_MISMATCH":
                    raise
                raise fail() from error
            except Exception as error:
                raise fail() from error

    return load_native_tree_v1, is_verified_native_tree_v1


load_native_tree_v1, is_verified_native_tree_v1 = _make_native_tree_loader()
del _make_native_tree_loader


__all__ = (
    "FROZEN_COMPILER_FLAGS",
    "NATIVE_ABI_ID",
    "NATIVE_ABI_VERSION",
    "NATIVE_MANIFEST_SCHEMA",
    "NativeTreeEvaluationV1",
    "VerifiedNativeTreeV1",
    "decode_native_tree_result_v1",
    "is_verified_native_tree_v1",
    "load_native_tree_v1",
    "validate_native_identity_v1",
    "verify_native_tree_manifest_v1",
)
