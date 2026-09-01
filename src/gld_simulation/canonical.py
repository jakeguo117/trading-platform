"""Strict canonical JSON helpers for reproducible simulation artifacts.

The format deliberately excludes floating-point numbers. Money, rates, and
timestamps must cross the raw-bundle boundary as exact integers or strings.
"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from .errors import RawBundleError


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
        raise RawBundleError("CANONICAL_JSON_DEPTH_LIMIT_EXCEEDED")
    counter[0] += 1
    if counter[0] > MAX_CANONICAL_NODES:
        raise RawBundleError("CANONICAL_JSON_NODE_LIMIT_EXCEEDED")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) >= 10**MAX_CANONICAL_INTEGER_DIGITS:
            raise RawBundleError(
                "CANONICAL_JSON_INTEGER_DIGITS_LIMIT_EXCEEDED"
            )
        return
    if type(value) is str:
        if len(value) > MAX_CANONICAL_STRING_CHARS:
            raise RawBundleError("CANONICAL_JSON_STRING_LIMIT_EXCEEDED")
        return
    if type(value) is float:
        raise RawBundleError("CANONICAL_JSON_FLOAT_FORBIDDEN")
    if type(value) is list:
        for item in value:
            _validate_json_value(item, depth=depth + 1, nodes=counter)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise RawBundleError("CANONICAL_JSON_KEY_INVALID")
            if not key or len(key) > MAX_CANONICAL_KEY_CHARS:
                raise RawBundleError(
                    "CANONICAL_JSON_KEY_LENGTH_LIMIT_EXCEEDED"
                )
            _validate_json_value(item, depth=depth + 1, nodes=counter)
        return
    raise RawBundleError(
        "CANONICAL_JSON_TYPE_INVALID", type(value).__name__
    )


def canonical_json_bytes(value: object) -> bytes:
    """Encode an exact JSON-domain value into one stable UTF-8 byte string."""

    try:
        _validate_json_value(value)
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except RawBundleError:
        raise
    except RecursionError as exc:
        raise RawBundleError(
            "CANONICAL_JSON_DEPTH_LIMIT_EXCEEDED"
        ) from exc
    except MemoryError as exc:
        raise RawBundleError(
            "CANONICAL_JSON_RESOURCE_LIMIT_EXCEEDED"
        ) from exc
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RawBundleError("CANONICAL_JSON_ENCODING_FAILED") from exc


def canonical_json_sha256(value: object) -> str:
    """Return the lowercase SHA-256 of :func:`canonical_json_bytes`."""

    return sha256(canonical_json_bytes(value)).hexdigest()


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RawBundleError("CANONICAL_JSON_DUPLICATE_KEY", key)
        result[key] = value
    return result


def _parse_limited_integer(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > MAX_CANONICAL_INTEGER_DIGITS:
        raise RawBundleError(
            "CANONICAL_JSON_INTEGER_DIGITS_LIMIT_EXCEEDED"
        )
    return int(value)


def parse_canonical_json(raw: bytes, *, source: str) -> object:
    """Parse bytes only if they are canonical JSON followed by one newline."""

    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise RawBundleError("CANONICAL_JSON_NEWLINE_INVALID", source)
    encoded = raw[:-1]
    try:
        text = encoded.decode("utf-8")
    except MemoryError as exc:
        raise RawBundleError(
            "CANONICAL_JSON_RESOURCE_LIMIT_EXCEEDED", source
        ) from exc
    except UnicodeDecodeError as exc:
        raise RawBundleError("CANONICAL_JSON_UTF8_INVALID", source) from exc
    try:
        value = json.loads(
            text,
            parse_int=_parse_limited_integer,
            parse_float=lambda _value: (_ for _ in ()).throw(
                RawBundleError("CANONICAL_JSON_FLOAT_FORBIDDEN", source)
            ),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                RawBundleError("CANONICAL_JSON_NUMBER_INVALID", source)
            ),
            object_pairs_hook=_reject_duplicate_object_keys,
        )
    except RawBundleError:
        raise
    except RecursionError as exc:
        raise RawBundleError(
            "CANONICAL_JSON_DEPTH_LIMIT_EXCEEDED", source
        ) from exc
    except MemoryError as exc:
        raise RawBundleError(
            "CANONICAL_JSON_RESOURCE_LIMIT_EXCEEDED", source
        ) from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise RawBundleError("CANONICAL_JSON_PARSE_FAILED", source) from exc
    if canonical_json_bytes(value) != encoded:
        raise RawBundleError("CANONICAL_JSON_ENCODING_NONCANONICAL", source)
    return value


__all__ = [
    "MAX_CANONICAL_DEPTH",
    "MAX_CANONICAL_INTEGER_DIGITS",
    "MAX_CANONICAL_KEY_CHARS",
    "MAX_CANONICAL_NODES",
    "MAX_CANONICAL_STRING_CHARS",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "parse_canonical_json",
]
