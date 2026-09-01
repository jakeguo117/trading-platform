"""Strict canonical input reading and no-clobber R1 artifact publication."""

from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
from typing import Any

from .canonical import MAX_CANONICAL_INTEGER_DIGITS, canonical_json_bytes
from .errors import EntryDecisionF0Error


MAX_INPUT_FILE_BYTES = 32 * 1024 * 1024


def _duplicate_checked_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EntryDecisionF0Error("CANONICAL_JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _limited_integer(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > MAX_CANONICAL_INTEGER_DIGITS:
        raise EntryDecisionF0Error("CANONICAL_JSON_INTEGER_DIGITS_LIMIT_EXCEEDED")
    return int(value)


def read_canonical_json_file(path: Path) -> object:
    """Read one stable regular file containing canonical JSON plus newline."""

    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise EntryDecisionF0Error("INPUT_FILE_INVALID") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or not 1 <= before.st_size <= MAX_INPUT_FILE_BYTES
        ):
            raise EntryDecisionF0Error("INPUT_FILE_INVALID")
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
            raise EntryDecisionF0Error("INPUT_FILE_CHANGED_DURING_READ")
        raw = b"".join(chunks)
    except OSError as exc:
        raise EntryDecisionF0Error("INPUT_FILE_INVALID") from exc
    finally:
        os.close(descriptor)
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise EntryDecisionF0Error("CANONICAL_JSON_NEWLINE_INVALID")
    encoded = raw[:-1]
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EntryDecisionF0Error("CANONICAL_JSON_UTF8_INVALID") from exc
    try:
        value = json.loads(
            text,
            parse_int=_limited_integer,
            parse_float=lambda _value: (_ for _ in ()).throw(
                EntryDecisionF0Error("CANONICAL_JSON_FLOAT_FORBIDDEN")
            ),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                EntryDecisionF0Error("CANONICAL_JSON_NUMBER_INVALID")
            ),
            object_pairs_hook=_duplicate_checked_object,
        )
    except EntryDecisionF0Error:
        raise
    except (json.JSONDecodeError, UnicodeError, RecursionError, MemoryError) as exc:
        raise EntryDecisionF0Error("CANONICAL_JSON_PARSE_FAILED") from exc
    if canonical_json_bytes(value) != encoded:
        raise EntryDecisionF0Error("CANONICAL_JSON_ENCODING_NONCANONICAL")
    return value


def _relative_artifact_path(value: str) -> PurePosixPath:
    if type(value) is not str or not value or "\\" in value or ":" in value:
        raise EntryDecisionF0Error("OUTPUT_ARTIFACT_PATH_INVALID")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise EntryDecisionF0Error("OUTPUT_ARTIFACT_PATH_INVALID")
    return path


def _atomic_rename_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish one directory without replacing an existing target."""

    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        try:
            rename_exclusive = library.renamex_np
        except AttributeError as exc:
            raise EntryDecisionF0Error(
                "OUTPUT_ATOMIC_NOREPLACE_UNAVAILABLE"
            ) from exc
        rename_exclusive.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(source_bytes, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        try:
            rename_exclusive = library.renameat2
        except AttributeError as exc:
            raise EntryDecisionF0Error(
                "OUTPUT_ATOMIC_NOREPLACE_UNAVAILABLE"
            ) from exc
        rename_exclusive.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename_exclusive.restype = ctypes.c_int
        result = rename_exclusive(
            -100,
            source_bytes,
            -100,
            destination_bytes,
            0x00000001,
        )
    else:
        raise EntryDecisionF0Error("OUTPUT_ATOMIC_NOREPLACE_UNAVAILABLE")
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise EntryDecisionF0Error("OUTPUT_ALREADY_EXISTS")
    raise OSError(error_number, os.strerror(error_number), destination)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_artifacts(output_directory: Path, artifacts: dict[str, bytes]) -> None:
    """Stage, sync, and atomically publish a complete no-clobber directory."""

    if type(artifacts) is not dict or not artifacts:
        raise EntryDecisionF0Error("OUTPUT_ARTIFACT_SET_INVALID")
    normalized: list[tuple[PurePosixPath, bytes]] = []
    seen: set[PurePosixPath] = set()
    for name, payload in sorted(artifacts.items()):
        relative = _relative_artifact_path(name)
        if relative in seen or type(payload) is not bytes:
            raise EntryDecisionF0Error("OUTPUT_ARTIFACT_SET_INVALID")
        seen.add(relative)
        normalized.append((relative, payload))
    if output_directory.exists() or output_directory.is_symlink():
        raise EntryDecisionF0Error("OUTPUT_ALREADY_EXISTS")
    if not output_directory.parent.is_dir() or output_directory.parent.is_symlink():
        raise EntryDecisionF0Error("OUTPUT_PARENT_INVALID")
    staging_directory: Path | None = None
    created_files: list[Path] = []
    created_directories: list[Path] = []
    try:
        staging_directory = Path(
            tempfile.mkdtemp(
                prefix=f".{output_directory.name}.staging-",
                dir=output_directory.parent,
            )
        )
        staging_directory.chmod(0o700)
        created_directories.append(staging_directory)
        for relative, payload in normalized:
            target = staging_directory.joinpath(*relative.parts)
            missing_parents: list[Path] = []
            cursor = target.parent
            while cursor != staging_directory and not cursor.exists():
                missing_parents.append(cursor)
                cursor = cursor.parent
            for directory in reversed(missing_parents):
                directory.mkdir(mode=0o700)
                created_directories.append(directory)
            with target.open("xb") as stream:
                created_files.append(target)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        for directory in sorted(
            created_directories,
            key=lambda value: len(value.parts),
            reverse=True,
        ):
            _fsync_directory(directory)
        _atomic_rename_no_replace(staging_directory, output_directory)
        staging_directory = None
    except EntryDecisionF0Error:
        for path in reversed(created_files):
            try:
                path.unlink()
            except OSError:
                pass
        for path in sorted(
            created_directories,
            key=lambda value: len(value.parts),
            reverse=True,
        ):
            try:
                path.rmdir()
            except OSError:
                pass
        raise
    except OSError as exc:
        for path in reversed(created_files):
            try:
                path.unlink()
            except OSError:
                pass
        for path in sorted(
            created_directories,
            key=lambda value: len(value.parts),
            reverse=True,
        ):
            try:
                path.rmdir()
            except OSError:
                pass
        raise EntryDecisionF0Error("OUTPUT_PUBLICATION_FAILED") from exc


__all__ = ["publish_artifacts", "read_canonical_json_file"]
