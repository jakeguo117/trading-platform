"""Local CLI for GLD data-contract and technical-fact acceptance."""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import secrets
import stat
import sys

from gld_simulation.canonical import canonical_json_bytes, parse_canonical_json
from gld_simulation.errors import RawBundleError

from .contracts import DataContractError
from .examples import failure_demonstrations
from .legacy_adapter import adapt_legacy_synthetic_bundle
from .technical import (
    TechnicalFactError,
    derive_required_technical_facts,
)
from .validation import validate_entry_bundle


_MAX_ENTRY_BUNDLE_FILE_BYTES = 32 * 1024 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a provider-neutral EntryFactBundleV1 or adapt one "
            "legacy synthetic GLD bundle, then deterministically derive "
            "RequiredDailyTechnicalFactsV1."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--legacy-bundle", type=Path)
    source.add_argument("--entry-bundle", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--demo-failures", action="store_true")
    return parser


def _publish(directory_fd: int, name: str, value: object) -> None:
    encoded = canonical_json_bytes(value) + b"\n"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            os.unlink(name, dir_fd=directory_fd)
        except OSError:
            pass
        raise


def _read_entry_bundle_file(path: Path) -> bytes:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise ValueError("ENTRY_BUNDLE_FILE_INVALID") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or not 1 <= before.st_size <= _MAX_ENTRY_BUNDLE_FILE_BYTES
        ):
            raise ValueError("ENTRY_BUNDLE_FILE_INVALID")
        chunks: list[bytes] = []
        total = 0
        while total <= _MAX_ENTRY_BUNDLE_FILE_BYTES:
            chunk = os.read(
                descriptor,
                min(
                    65_536,
                    _MAX_ENTRY_BUNDLE_FILE_BYTES + 1 - total,
                ),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
        if (
            total > _MAX_ENTRY_BUNDLE_FILE_BYTES
            or total != before.st_size
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise ValueError("ENTRY_BUNDLE_FILE_CHANGED_DURING_READ")
        return b"".join(chunks)
    except OSError as error:
        raise ValueError("ENTRY_BUNDLE_FILE_INVALID") from error
    finally:
        os.close(descriptor)


def _rename_directory_noreplace(
    parent_fd: int,
    source_name: str,
    target_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameatx_np = getattr(libc, "renameatx_np", None)
    if renameatx_np is None:
        raise OSError("ATOMIC_NOREPLACE_UNAVAILABLE")
    renameatx_np.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameatx_np.restype = ctypes.c_int
    rename_excl = 0x00000004
    result = renameatx_np(
        parent_fd,
        os.fsencode(source_name),
        parent_fd,
        os.fsencode(target_name),
        rename_excl,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), target_name)


def _directory_entry_matches_open_fd(
    parent_fd: int,
    name: str,
    directory_fd: int,
) -> bool:
    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    opened = os.fstat(directory_fd)
    return (
        stat.S_ISDIR(named.st_mode)
        and named.st_dev == opened.st_dev
        and named.st_ino == opened.st_ino
    )


def _remove_owned_empty_staging_directory(
    parent_fd: int,
    name: str,
) -> None:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        return
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except OSError:
        pass


def _publish_output_directory(
    directory: Path,
    artifacts: list[tuple[str, object]],
) -> None:
    parent = directory.parent
    target_name = directory.name
    if (
        not target_name
        or target_name in {".", ".."}
        or len(os.fsencode(target_name)) > 200
        or not parent.exists()
        or not parent.is_dir()
        or parent.is_symlink()
    ):
        raise ValueError("OUTPUT_DIRECTORY_INVALID")
    parent_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_fd = os.open(parent, parent_flags)
    staging_name: str | None = None
    staging_fd = -1
    renamed = False
    published = False
    try:
        parent_metadata = os.fstat(parent_fd)
        if (
            parent_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            and not parent_metadata.st_mode & stat.S_ISVTX
        ):
            raise ValueError("OUTPUT_PARENT_PERMISSIONS_UNSAFE")
        if not artifacts or len({name for name, _value in artifacts}) != len(artifacts):
            raise ValueError("OUTPUT_ARTIFACT_SET_INVALID")
        for name, _value in artifacts:
            if not name or name in {".", ".."} or "/" in name:
                raise ValueError("OUTPUT_ARTIFACT_SET_INVALID")
        for _attempt in range(32):
            candidate = (
                f".{target_name}.staging-{os.getpid()}-{secrets.token_hex(8)}"
            )
            staging_name = candidate
            try:
                os.mkdir(candidate, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                staging_name = None
                continue
            break
        if staging_name is None:
            raise OSError("OUTPUT_STAGING_NAME_EXHAUSTED")
        staging_fd = os.open(staging_name, parent_flags, dir_fd=parent_fd)
        for name, value in artifacts:
            _publish(staging_fd, name, value)
        os.fsync(staging_fd)
        staging_by_fd = os.fstat(staging_fd)
        staging_by_name = os.stat(
            staging_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(staging_by_name.st_mode)
            or staging_by_fd.st_dev != staging_by_name.st_dev
            or staging_by_fd.st_ino != staging_by_name.st_ino
            or staging_by_name.st_uid != os.geteuid()
        ):
            raise OSError("OUTPUT_STAGING_IDENTITY_CHANGED")
        try:
            # Default to preservation before entering the side-effecting
            # syscall. Only inode reconciliation may prove cleanup safe.
            renamed = True
            _rename_directory_noreplace(parent_fd, staging_name, target_name)
            renamed = True  # committed-return boundary for fault injection
        except BaseException:
            # A signal may be delivered after the rename syscall commits but
            # before Python records the return. Reconcile names against the
            # still-open staging inode and default to preservation if the
            # state cannot be proven safe to clean up.
            renamed = True
            try:
                source_is_staging = _directory_entry_matches_open_fd(
                    parent_fd,
                    staging_name,
                    staging_fd,
                )
                target_is_staging = _directory_entry_matches_open_fd(
                    parent_fd,
                    target_name,
                    staging_fd,
                )
                if source_is_staging and not target_is_staging:
                    renamed = False
            except OSError:
                pass
            raise
        try:
            os.fsync(parent_fd)
        except OSError as error:
            try:
                _rename_directory_noreplace(
                    parent_fd,
                    target_name,
                    staging_name,
                )
                renamed = False
            except BaseException as rollback_error:
                rollback_completed = False
                renamed = True
                try:
                    published_target_is_staging = (
                        _directory_entry_matches_open_fd(
                            parent_fd,
                            target_name,
                            staging_fd,
                        )
                    )
                    rollback_name_is_staging = (
                        _directory_entry_matches_open_fd(
                            parent_fd,
                            staging_name,
                            staging_fd,
                        )
                    )
                    if (
                        rollback_name_is_staging
                        and not published_target_is_staging
                    ):
                        renamed = False
                        rollback_completed = True
                except OSError:
                    pass
                if isinstance(rollback_error, OSError):
                    if rollback_completed:
                        raise OSError(
                            "OUTPUT_PARENT_FSYNC_FAILED_ROLLED_BACK"
                        ) from error
                    published = True
                    raise OSError(
                        "OUTPUT_DURABILITY_UNCERTAIN_AFTER_PUBLISH"
                    ) from error
                raise
            raise OSError("OUTPUT_PARENT_FSYNC_FAILED_ROLLED_BACK") from error
        published = True
    finally:
        if staging_fd >= 0:
            if not published and not renamed:
                for name, _value in artifacts:
                    try:
                        os.unlink(name, dir_fd=staging_fd)
                    except OSError:
                        pass
            os.close(staging_fd)
        if staging_name is not None and not published and not renamed:
            _remove_owned_empty_staging_directory(parent_fd, staging_name)
        os.close(parent_fd)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.legacy_bundle is not None:
        entry_document = adapt_legacy_synthetic_bundle(arguments.legacy_bundle)
    else:
        entry_path = arguments.entry_bundle
        if entry_path is None:
            raise ValueError("ENTRY_BUNDLE_FILE_INVALID")
        entry_raw = _read_entry_bundle_file(entry_path)
        entry_document = parse_canonical_json(
            entry_raw, source="entry-bundle"
        )
    validated = validate_entry_bundle(entry_document)
    technical = None
    if not arguments.validate_only:
        technical = derive_required_technical_facts(validated)
    demos = (
        failure_demonstrations(validated.normalized_document)
        if arguments.demo_failures
        else []
    )
    report: dict[str, object] = {
        "schema_version": "GLD_DATA_CONTRACT_VALIDATION_REPORT_V1",
        "status": "PASS" if all(item["demonstrated"] for item in demos) else (
            "PASS" if not demos else "FAIL"
        ),
        "entry_bundle": {
            "schema_version": validated.schema_version,
            "classification": validated.classification,
            "qualification_status": validated.qualification.status,
            "qualification_reason_codes": list(
                validated.qualification.reason_codes
            ),
            "qualification_sha256": validated.qualification.qualification_sha256,
            "trusted_source_receipt_set_sha256": (
                validated.qualification.trusted_source_receipt_set_sha256
            ),
            "entry_bundle_sha256": validated.entry_bundle_sha256,
            "call_universe_count": len(
                validated.normalized_document["option_snapshot"]["option_quotes"]
            ),
        },
        "technical_facts": (
            None
            if technical is None
            else {
                "schema_version": technical.schema_version,
                "technical_facts_sha256": technical.technical_facts_sha256,
                "feature_count": len(technical.document["features"]),
                "bcs_pair_count": technical.document["bcs_pair_facts"][
                    "pair_count"
                ],
            }
        ),
        "failure_demonstrations": demos,
        "boundaries": {
            "broker_accessed": False,
            "decision_function_modified": False,
            "provider_accessed": False,
            "real_data_qualified": False,
            "trading_enabled": False,
        },
    }
    if arguments.output_dir is not None:
        artifacts: list[tuple[str, object]] = [
            ("entry-fact-bundle-v1.json", validated.normalized_document),
        ]
        if technical is not None:
            artifacts.append(
                (
                    "required-daily-technical-facts-v1.json",
                    technical.document,
                )
            )
        # The report remains last inside the atomically renamed directory.
        artifacts.append(("validation-report-v1.json", report))
        _publish_output_directory(arguments.output_dir, artifacts)
    sys.stdout.buffer.write(canonical_json_bytes(report) + b"\n")
    return 0 if report["status"] == "PASS" else 1


def guarded_main(argv: list[str] | None = None) -> int:
    """Run the CLI without exposing internal paths or tracebacks on failure."""

    try:
        return main(argv)
    except (DataContractError, TechnicalFactError, RawBundleError) as error:
        reason_code = getattr(error, "reason_code", "DATA_CONTRACT_VALIDATION_FAILED")
    except (OSError, ValueError):
        reason_code = "LOCAL_INPUT_OR_OUTPUT_INVALID"
    except Exception:
        reason_code = "UNEXPECTED_LOCAL_FAILURE"
    failure = {
        "schema_version": "GLD_DATA_CONTRACT_VALIDATION_REPORT_V1",
        "status": "FAIL",
        "reason_code": reason_code,
    }
    sys.stdout.buffer.write(canonical_json_bytes(failure) + b"\n")
    return 2


__all__ = ["guarded_main", "main"]
