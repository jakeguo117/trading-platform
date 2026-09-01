"""Strict deterministic canonical JSON for the isolated Entry f F0 package."""

from __future__ import annotations

from hashlib import sha256
import json

from .errors import EntryDecisionF0Error


MAX_CANONICAL_DEPTH = 32
MAX_CANONICAL_NODES = 100_000
MAX_CANONICAL_STRING_CHARS = 1_048_576
MAX_CANONICAL_KEY_CHARS = 256
# Input contracts remain bounded to signed 64-bit facts.  Derived quantities
# multiply nano-USD, contract counts, and ppm values, so their exact canonical
# representation needs a wider deterministic envelope.
MAX_CANONICAL_INTEGER_DIGITS = 64


def _validate(value: object, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    counter = [0] if nodes is None else nodes
    if depth > MAX_CANONICAL_DEPTH:
        raise EntryDecisionF0Error("CANONICAL_JSON_DEPTH_LIMIT_EXCEEDED")
    counter[0] += 1
    if counter[0] > MAX_CANONICAL_NODES:
        raise EntryDecisionF0Error("CANONICAL_JSON_NODE_LIMIT_EXCEEDED")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if abs(value) >= 10**MAX_CANONICAL_INTEGER_DIGITS:
            raise EntryDecisionF0Error("CANONICAL_JSON_INTEGER_DIGITS_LIMIT_EXCEEDED")
        return
    if type(value) is str:
        if len(value) > MAX_CANONICAL_STRING_CHARS:
            raise EntryDecisionF0Error("CANONICAL_JSON_STRING_LIMIT_EXCEEDED")
        return
    if type(value) is float:
        raise EntryDecisionF0Error("CANONICAL_JSON_FLOAT_FORBIDDEN")
    if type(value) is list:
        for item in value:
            _validate(item, depth=depth + 1, nodes=counter)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise EntryDecisionF0Error("CANONICAL_JSON_KEY_INVALID")
            if not key or len(key) > MAX_CANONICAL_KEY_CHARS:
                raise EntryDecisionF0Error("CANONICAL_JSON_KEY_LENGTH_LIMIT_EXCEEDED")
            _validate(item, depth=depth + 1, nodes=counter)
        return
    raise EntryDecisionF0Error("CANONICAL_JSON_TYPE_INVALID", type(value).__name__)


def canonical_json_bytes(value: object) -> bytes:
    try:
        _validate(value)
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except EntryDecisionF0Error:
        raise
    except RecursionError as exc:
        raise EntryDecisionF0Error("CANONICAL_JSON_DEPTH_LIMIT_EXCEEDED") from exc
    except MemoryError as exc:
        raise EntryDecisionF0Error("CANONICAL_JSON_RESOURCE_LIMIT_EXCEEDED") from exc
    except (TypeError, ValueError, UnicodeError) as exc:
        raise EntryDecisionF0Error("CANONICAL_JSON_ENCODING_FAILED") from exc


def canonical_json_sha256(value: object) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


__all__ = [
    "MAX_CANONICAL_INTEGER_DIGITS",
    "canonical_json_bytes",
    "canonical_json_sha256",
]
