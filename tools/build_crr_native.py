#!/usr/bin/env python3
"""Explicit offline builder for the CRR native shadow tree kernel.

The builder has no import-time side effects and never replaces a completed
hash-addressed artifact.  Its manifest intentionally contains only canonical
path tokens, never the local repository or user-home path.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from typing import Mapping, Sequence


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


class NativeBuildError(RuntimeError):
    """One deterministic offline build failure."""


def canonical_json_bytes(value: object) -> bytes:
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


def _canonical_sha256(value: object) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def _exact_json_equal(first: object, second: object) -> bool:
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


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
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
        stderr = getattr(error, "stderr", b"")
        message = stderr.decode("utf-8", "replace")[-2000:]
        raise NativeBuildError(f"NATIVE_BUILD_COMMAND_FAILED: {message}") from error
    return completed.stdout


def _single_line(command: Sequence[str]) -> str:
    value = _run(command).decode("utf-8", "strict").strip()
    if not value or "\x00" in value:
        raise NativeBuildError("NATIVE_BUILD_ENVIRONMENT_INVALID")
    return value


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _path_token(
    path: Path,
    *,
    repo_root: Path,
    python_include: Path,
    sdk_root: Path,
    clang_resource: Path,
) -> str:
    resolved = path.resolve(strict=True)
    roots = (
        (repo_root, "$REPO"),
        (python_include, "$PYTHON_INCLUDE"),
        (sdk_root, "$SDKROOT"),
        (clang_resource, "$CLANG_RESOURCE"),
    )
    for root, token in roots:
        if _is_relative_to(resolved, root):
            relative = resolved.relative_to(root).as_posix()
            return token if relative == "." else f"{token}/{relative}"
    raise NativeBuildError("NATIVE_BUILD_UNMAPPED_DEPENDENCY")


def _parse_dependency_output(output: bytes) -> tuple[Path, ...]:
    text = output.decode("utf-8", "strict").replace("\\\n", " ")
    if ":" not in text:
        raise NativeBuildError("NATIVE_BUILD_DEPENDENCY_SCAN_INVALID")
    _, values = text.split(":", 1)
    try:
        paths = tuple(Path(value) for value in shlex.split(values))
    except ValueError as error:
        raise NativeBuildError("NATIVE_BUILD_DEPENDENCY_SCAN_INVALID") from error
    if not paths:
        raise NativeBuildError("NATIVE_BUILD_DEPENDENCY_SCAN_INVALID")
    return paths


def _manifest_entries(
    paths: Sequence[Path],
    *,
    repo_root: Path,
    python_include: Path,
    sdk_root: Path,
    clang_resource: Path,
    excluded: frozenset[Path] = frozenset(),
) -> list[dict[str, str]]:
    entries: dict[str, str] = {}
    excluded_resolved = {path.resolve(strict=True) for path in excluded}
    for path in paths:
        resolved = path.resolve(strict=True)
        if resolved in excluded_resolved:
            continue
        token = _path_token(
            resolved,
            repo_root=repo_root,
            python_include=python_include,
            sdk_root=sdk_root,
            clang_resource=clang_resource,
        )
        value = _file_sha256(resolved)
        prior = entries.setdefault(token, value)
        if prior != value:
            raise NativeBuildError("NATIVE_BUILD_DEPENDENCY_COLLISION")
    return [
        {"path": path, "sha256": entries[path]}
        for path in sorted(entries)
    ]


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


def _target_environment(*, sdk_version: str, os_build: str) -> dict[str, object]:
    return {
        "os_build": os_build,
        "schema_version": TARGET_ENVIRONMENT_SCHEMA,
        "sdk_version": sdk_version,
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


def _normalized_otool_sha256(binary_path: Path) -> str:
    lines = _run((str(OTOOL_PATH), "-L", str(binary_path))).decode(
        "utf-8", "strict"
    ).splitlines()
    if not lines:
        raise NativeBuildError("NATIVE_BUILD_OTOOL_INVALID")
    normalized = ["$BINARY:"]
    normalized.extend(line.rstrip() for line in lines[1:])
    return sha256(("\n".join(normalized) + "\n").encode("utf-8")).hexdigest()


def _build_manifest_sha256(document: Mapping[str, object]) -> str:
    payload = {
        key: document[key]
        for key in (*_BASE_MANIFEST_KEYS, *_BINARY_MANIFEST_KEYS)
    }
    return _canonical_sha256(payload)


def _backend_evidence_sha256(document: Mapping[str, object]) -> str:
    return _canonical_sha256(
        {
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
    )


def _validate_existing(
    manifest_path: Path,
    expected_base: Mapping[str, object],
) -> bool:
    if not manifest_path.is_file():
        return False
    try:
        manifest_bytes = manifest_path.read_bytes()
        document = json.loads(manifest_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if type(document) is not dict or manifest_bytes != canonical_json_bytes(document):
        return False
    if set(document) != {
        *_BASE_MANIFEST_KEYS,
        *_BINARY_MANIFEST_KEYS,
        "backend_evidence_sha256",
        "build_manifest_sha256",
    }:
        return False
    if any(
        not _exact_json_equal(document.get(key), value)
        for key, value in expected_base.items()
    ):
        return False
    binary_path = manifest_path.parent / str(document["binary_filename"])
    if not binary_path.is_file():
        return False
    if (
        _file_sha256(binary_path) != document["binary_sha256"]
        or binary_path.stat().st_size != document["binary_size"]
        or _normalized_otool_sha256(binary_path)
        != document["otool_dependency_sha256"]
        or _build_manifest_sha256(document) != document["build_manifest_sha256"]
        or _backend_evidence_sha256(document)
        != document["backend_evidence_sha256"]
    ):
        return False
    return True


def build_native_kernel_v1(*, repo_root: Path) -> Path:
    """Build once into ``.build/crr_native/<build_input_sha256>/``."""

    repo_root = Path(repo_root).resolve(strict=True)
    source_path = repo_root / "native" / "crr_tree_kernel_v1.c"
    if not source_path.is_file():
        raise NativeBuildError("NATIVE_BUILD_SOURCE_MISSING")
    for required in (CLANG_PATH, OTOOL_PATH, XCRUN_PATH, SW_VERS_PATH):
        if not required.is_file():
            raise NativeBuildError("NATIVE_BUILD_TOOL_MISSING")

    python_include_value = sysconfig.get_paths().get("include")
    ext_suffix = sysconfig.get_config_var("EXT_SUFFIX")
    soabi = sysconfig.get_config_var("SOABI")
    if not all(type(value) is str and value for value in (python_include_value, ext_suffix, soabi)):
        raise NativeBuildError("NATIVE_BUILD_PYTHON_ABI_INVALID")
    python_include = Path(python_include_value).resolve(strict=True)
    sdk_root = Path(_single_line((str(XCRUN_PATH), "--show-sdk-path"))).resolve(
        strict=True
    )
    sdk_version = _single_line((str(XCRUN_PATH), "--show-sdk-version"))
    os_build = _single_line((str(SW_VERS_PATH), "-buildVersion"))
    clang_resource = Path(
        _single_line((str(CLANG_PATH), "-print-resource-dir"))
    ).resolve(strict=True)

    dependency_command = (
        str(CLANG_PATH),
        "-target",
        "arm64-apple-macosx26.0",
        "-mcpu=apple-m4",
        "-std=c17",
        "-isystem",
        str(python_include),
        '-DGLD_SOURCE_MANIFEST_SHA256="DEPENDENCY_SCAN"',
        '-DGLD_BUILD_INPUT_SHA256="DEPENDENCY_SCAN"',
        '-DGLD_COMPILER_FLAGS_SHA256="DEPENDENCY_SCAN"',
        '-DGLD_DEPENDENCY_MANIFEST_SHA256="DEPENDENCY_SCAN"',
        "-M",
        str(source_path),
    )
    dependency_paths = _parse_dependency_output(_run(dependency_command))
    dependency_entries = _manifest_entries(
        dependency_paths,
        repo_root=repo_root,
        python_include=python_include,
        sdk_root=sdk_root,
        clang_resource=clang_resource,
        excluded=frozenset((source_path,)),
    )
    python_header_paths = tuple(
        path
        for path in dependency_paths
        if _is_relative_to(path.resolve(strict=True), python_include)
    )
    python_header_entries = _manifest_entries(
        python_header_paths,
        repo_root=repo_root,
        python_include=python_include,
        sdk_root=sdk_root,
        clang_resource=clang_resource,
    )

    source_sha256 = _file_sha256(source_path)
    source_manifest_sha256 = _canonical_sha256(
        {
            "schema_version": SOURCE_MANIFEST_SCHEMA,
            "source_path": SOURCE_TOKEN,
            "source_sha256": source_sha256,
        }
    )
    dependency_manifest_sha256 = _canonical_sha256(
        {
            "dependencies": dependency_entries,
            "schema_version": DEPENDENCY_MANIFEST_SCHEMA,
        }
    )
    python_header_manifest_sha256 = _canonical_sha256(
        {
            "headers": python_header_entries,
            "schema_version": PYTHON_HEADER_MANIFEST_SCHEMA,
        }
    )
    runtime_environment = _runtime_environment()
    runtime_environment_sha256 = _canonical_sha256(runtime_environment)
    target_environment = _target_environment(
        sdk_version=sdk_version,
        os_build=os_build,
    )
    target_environment_sha256 = _canonical_sha256(target_environment)
    compiler_executable_sha256 = _file_sha256(CLANG_PATH)
    compiler_version_sha256 = sha256(
        _run((str(CLANG_PATH), "--version"))
    ).hexdigest()
    compiler_flags_sha256 = _canonical_sha256(list(FROZEN_COMPILER_FLAGS))
    binary_filename = f"_crr_tree_kernel{ext_suffix}"
    compile_command_sha256 = _canonical_sha256(
        _compile_command_template(binary_filename)
    )

    build_input_sha256 = _canonical_sha256(
        {
            "compile_command_sha256": compile_command_sha256,
            "compiler_executable_sha256": compiler_executable_sha256,
            "compiler_flags_sha256": compiler_flags_sha256,
            "compiler_version_sha256": compiler_version_sha256,
            "dependency_manifest_sha256": dependency_manifest_sha256,
            "python_header_manifest_sha256": python_header_manifest_sha256,
            "runtime_environment_sha256": runtime_environment_sha256,
            "schema_version": BUILD_INPUT_SCHEMA,
            "source_manifest_sha256": source_manifest_sha256,
            "target_environment_sha256": target_environment_sha256,
        }
    )
    base_manifest: dict[str, object] = {
        "abi_id": NATIVE_ABI_ID,
        "abi_version": NATIVE_ABI_VERSION,
        "binary_filename": binary_filename,
        "build_input_sha256": build_input_sha256,
        "compile_command_sha256": compile_command_sha256,
        "compiler_executable": "$CLANG",
        "compiler_executable_sha256": compiler_executable_sha256,
        "compiler_flags": list(FROZEN_COMPILER_FLAGS),
        "compiler_flags_sha256": compiler_flags_sha256,
        "compiler_version_sha256": compiler_version_sha256,
        "dependency_manifest": dependency_entries,
        "dependency_manifest_sha256": dependency_manifest_sha256,
        "python_header_manifest": python_header_entries,
        "python_header_manifest_sha256": python_header_manifest_sha256,
        "runtime_environment": runtime_environment,
        "runtime_environment_sha256": runtime_environment_sha256,
        "schema_version": NATIVE_MANIFEST_SCHEMA,
        "soabi": soabi,
        "source_manifest_sha256": source_manifest_sha256,
        "source_path": SOURCE_TOKEN,
        "source_sha256": source_sha256,
        "target_environment": target_environment,
        "target_environment_sha256": target_environment_sha256,
    }

    build_root = repo_root / ".build" / "crr_native"
    build_root.mkdir(parents=True, exist_ok=True)
    final_directory = build_root / build_input_sha256
    manifest_path = final_directory / "manifest_v1.json"
    if final_directory.exists():
        if _validate_existing(manifest_path, base_manifest):
            return manifest_path
        raise NativeBuildError("NATIVE_BUILD_IMMUTABLE_ARTIFACT_CONFLICT")

    temporary_directory = Path(
        tempfile.mkdtemp(prefix=".tmp-crr-native-", dir=build_root)
    )
    try:
        binary_path = temporary_directory / binary_filename
        compile_command = [
            str(CLANG_PATH),
            *FROZEN_COMPILER_FLAGS,
            "-isystem",
            str(python_include),
            f'-DGLD_SOURCE_MANIFEST_SHA256="{source_manifest_sha256}"',
            f'-DGLD_BUILD_INPUT_SHA256="{build_input_sha256}"',
            f'-DGLD_COMPILER_FLAGS_SHA256="{compiler_flags_sha256}"',
            f'-DGLD_DEPENDENCY_MANIFEST_SHA256="{dependency_manifest_sha256}"',
            str(source_path),
            "-o",
            str(binary_path),
        ]
        _run(compile_command)
        # The dependency scan precedes compilation so its digest can be
        # embedded in identity_v1.  Re-scan and re-hash every input before
        # publication to close the scan-to-compile provenance window.
        post_dependency_paths = _parse_dependency_output(_run(dependency_command))
        post_dependency_entries = _manifest_entries(
            post_dependency_paths,
            repo_root=repo_root,
            python_include=python_include,
            sdk_root=sdk_root,
            clang_resource=clang_resource,
            excluded=frozenset((source_path,)),
        )
        post_python_header_paths = tuple(
            path
            for path in post_dependency_paths
            if _is_relative_to(path.resolve(strict=True), python_include)
        )
        post_python_header_entries = _manifest_entries(
            post_python_header_paths,
            repo_root=repo_root,
            python_include=python_include,
            sdk_root=sdk_root,
            clang_resource=clang_resource,
        )
        if (
            _file_sha256(source_path) != source_sha256
            or post_dependency_entries != dependency_entries
            or post_python_header_entries != python_header_entries
            or _file_sha256(CLANG_PATH) != compiler_executable_sha256
            or sha256(_run((str(CLANG_PATH), "--version"))).hexdigest()
            != compiler_version_sha256
            or Path(_single_line((str(XCRUN_PATH), "--show-sdk-path"))).resolve(
                strict=True
            )
            != sdk_root
            or _single_line((str(XCRUN_PATH), "--show-sdk-version"))
            != sdk_version
            or _single_line((str(SW_VERS_PATH), "-buildVersion")) != os_build
            or Path(
                _single_line((str(CLANG_PATH), "-print-resource-dir"))
            ).resolve(strict=True)
            != clang_resource
            or _runtime_environment() != runtime_environment
        ):
            raise NativeBuildError("NATIVE_BUILD_INPUT_CHANGED_DURING_BUILD")
        document = {
            **base_manifest,
            "binary_sha256": _file_sha256(binary_path),
            "binary_size": binary_path.stat().st_size,
            "otool_dependency_sha256": _normalized_otool_sha256(binary_path),
        }
        document["build_manifest_sha256"] = _build_manifest_sha256(document)
        document["backend_evidence_sha256"] = _backend_evidence_sha256(document)
        (temporary_directory / "manifest_v1.json").write_bytes(
            canonical_json_bytes(document)
        )
        temporary_directory.rename(final_directory)
    except BaseException:
        if temporary_directory.exists():
            shutil.rmtree(temporary_directory)
        raise
    return manifest_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args(argv)
    manifest_path = build_native_kernel_v1(repo_root=arguments.repo_root)
    if arguments.json:
        manifest = json.loads(manifest_path.read_bytes())
        print(
            json.dumps(
                {
                    "backend_evidence_sha256": manifest[
                        "backend_evidence_sha256"
                    ],
                    "binary_sha256": manifest["binary_sha256"],
                    "manifest_path": str(manifest_path),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    else:
        print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
