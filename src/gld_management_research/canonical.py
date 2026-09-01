"""Package-local strict canonical JSON used by Research F0.

This module intentionally has no dependency on any existing GLD package.  The
wire encoding matches the repository's canonical JSON convention while keeping
the management research core isolated and integer-only.
"""

from __future__ import annotations

from hashlib import sha256
import json

from .errors import ManagementResearchError


MAX_CANONICAL_DEPTH = 32
MAX_CANONICAL_NODES = 100_000
MAX_CANONICAL_STRING_CHARS = 1_048_576
MAX_CANONICAL_KEY_CHARS = 256
MAX_CANONICAL_INTEGER_DIGITS = 19


def _validate_json_value(
    value: object,
    *,
    depth: int = 0,
    nodes: list[int] | None = None,
) -> None:
    counter = [0] if nodes is None else nodes
    if depth > MAX_CANONICAL_DEPTH:
        raise ManagementResearchError("CANONICAL_JSON_DEPTH_LIMIT_EXCEEDED")
    counter[0] += 1
    if counter[0] > MAX_CANONICAL_NODES:
        raise ManagementResearchError("CANONICAL_JSON_NODE_LIMIT_EXCEEDED")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) >= 10**MAX_CANONICAL_INTEGER_DIGITS:
            raise ManagementResearchError(
                "CANONICAL_JSON_INTEGER_DIGITS_LIMIT_EXCEEDED"
            )
        return
    if type(value) is str:
        if len(value) > MAX_CANONICAL_STRING_CHARS:
            raise ManagementResearchError("CANONICAL_JSON_STRING_LIMIT_EXCEEDED")
        return
    if type(value) is float:
        raise ManagementResearchError("CANONICAL_JSON_FLOAT_FORBIDDEN")
    if type(value) is list:
        for item in value:
            _validate_json_value(item, depth=depth + 1, nodes=counter)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ManagementResearchError("CANONICAL_JSON_KEY_INVALID")
            if not key or len(key) > MAX_CANONICAL_KEY_CHARS:
                raise ManagementResearchError(
                    "CANONICAL_JSON_KEY_LENGTH_LIMIT_EXCEEDED"
                )
            _validate_json_value(item, depth=depth + 1, nodes=counter)
        return
    raise ManagementResearchError(
        "CANONICAL_JSON_TYPE_INVALID", type(value).__name__
    )


def canonical_json_bytes(value: object) -> bytes:
    """Encode one validated JSON-domain value to deterministic UTF-8 bytes."""

    try:
        _validate_json_value(value)
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except ManagementResearchError:
        raise
    except RecursionError as exc:
        raise ManagementResearchError(
            "CANONICAL_JSON_DEPTH_LIMIT_EXCEEDED"
        ) from exc
    except MemoryError as exc:
        raise ManagementResearchError(
            "CANONICAL_JSON_RESOURCE_LIMIT_EXCEEDED"
        ) from exc
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ManagementResearchError("CANONICAL_JSON_ENCODING_FAILED") from exc


def canonical_json_sha256(value: object) -> str:
    """Return the lowercase SHA-256 of canonical JSON bytes."""

    return sha256(canonical_json_bytes(value)).hexdigest()


__all__ = ["canonical_json_bytes", "canonical_json_sha256"]
