"""Strict input reading and no-clobber publication for Research F0."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any

from .canonical import canonical_json_bytes
from .errors import ManagementResearchError


MAX_INPUT_FILE_BYTES = 32 * 1024 * 1024


def _duplicate_checked_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManagementResearchError("CANONICAL_JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _limited_integer(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > 19:
        raise ManagementResearchError("CANONICAL_JSON_INTEGER_DIGITS_LIMIT_EXCEEDED")
    return int(value)


def read_canonical_json_file(path: Path) -> object:
    """Read one canonical JSON document terminated by exactly one newline."""

    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise ManagementResearchError("INPUT_FILE_INVALID") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or not 1 <= before.st_size <= MAX_INPUT_FILE_BYTES
        ):
            raise ManagementResearchError("INPUT_FILE_INVALID")
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_INPUT_FILE_BYTES:
            chunk = os.read(
                descriptor,
                min(65_536, MAX_INPUT_FILE_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
        if (
            total > MAX_INPUT_FILE_BYTES
            or total != before.st_size
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise ManagementResearchError("INPUT_FILE_CHANGED_DURING_READ")
        raw = b"".join(chunks)
    except OSError as exc:
        raise ManagementResearchError("INPUT_FILE_INVALID") from exc
    finally:
        os.close(descriptor)
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise ManagementResearchError("CANONICAL_JSON_NEWLINE_INVALID")
    encoded = raw[:-1]
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManagementResearchError("CANONICAL_JSON_UTF8_INVALID") from exc
    try:
        value = json.loads(
            text,
            parse_int=_limited_integer,
            parse_float=lambda _value: (_ for _ in ()).throw(
                ManagementResearchError("CANONICAL_JSON_FLOAT_FORBIDDEN")
            ),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ManagementResearchError("CANONICAL_JSON_NUMBER_INVALID")
            ),
            object_pairs_hook=_duplicate_checked_object,
        )
    except ManagementResearchError:
        raise
    except (json.JSONDecodeError, UnicodeError, RecursionError, MemoryError) as exc:
        raise ManagementResearchError("CANONICAL_JSON_PARSE_FAILED") from exc
    if canonical_json_bytes(value) != encoded:
        raise ManagementResearchError("CANONICAL_JSON_ENCODING_NONCANONICAL")
    return value


def _relative_artifact_path(value: str) -> PurePosixPath:
    if type(value) is not str or not value or "\\" in value or ":" in value:
        raise ManagementResearchError("OUTPUT_ARTIFACT_PATH_INVALID")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ManagementResearchError("OUTPUT_ARTIFACT_PATH_INVALID")
    return path


def publish_artifacts(
    output_directory: Path,
    artifacts: dict[str, bytes],
) -> None:
    """Publish a complete artifact set into one new directory, never clobbering."""

    if type(artifacts) is not dict or not artifacts:
        raise ManagementResearchError("OUTPUT_ARTIFACT_SET_INVALID")
    normalized: list[tuple[PurePosixPath, bytes]] = []
    seen: set[PurePosixPath] = set()
    for name, payload in sorted(artifacts.items()):
        relative = _relative_artifact_path(name)
        if relative in seen or type(payload) is not bytes:
            raise ManagementResearchError("OUTPUT_ARTIFACT_SET_INVALID")
        seen.add(relative)
        normalized.append((relative, payload))
    if output_directory.exists() or output_directory.is_symlink():
        raise ManagementResearchError("OUTPUT_ALREADY_EXISTS")
    if (
        not output_directory.parent.is_dir()
        or output_directory.parent.is_symlink()
    ):
        raise ManagementResearchError("OUTPUT_PARENT_INVALID")
    created_files: list[Path] = []
    created_directories: list[Path] = []
    try:
        output_directory.mkdir(mode=0o700)
        created_directories.append(output_directory)
        for relative, payload in normalized:
            target = output_directory.joinpath(*relative.parts)
            missing_parents: list[Path] = []
            cursor = target.parent
            while cursor != output_directory and not cursor.exists():
                missing_parents.append(cursor)
                cursor = cursor.parent
            for directory in reversed(missing_parents):
                directory.mkdir(mode=0o700)
                created_directories.append(directory)
            with target.open("xb") as stream:
                # Track immediately after exclusive creation so a short write
                # or fsync failure cannot leave an unowned partial artifact.
                created_files.append(target)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
    except ManagementResearchError:
        raise
    except OSError as exc:
        for path in reversed(created_files):
            try:
                path.unlink()
            except OSError:
                pass
        for path in reversed(created_directories):
            try:
                path.rmdir()
            except OSError:
                pass
        raise ManagementResearchError("OUTPUT_PUBLICATION_FAILED") from exc


__all__ = ["publish_artifacts", "read_canonical_json_file"]
