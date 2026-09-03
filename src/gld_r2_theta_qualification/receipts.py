"""Stage-aware, closed terminal receipts for an R2 research generation.

Early terminals intentionally omit hashes for stages that never existed.  In
particular, metadata and Development conclusions never fabricate historical
theta or Forward receipt placeholders.  Owner authority is outside this
machine-terminal contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from .contracts import (
    R2QualificationError,
    canonical_json_bytes,
    canonical_sha256,
    derive_qualification_sha256,
)


GENERATION_TERMINAL_RECEIPT_SCHEMA_VERSION = (
    "R2_GENERATION_TERMINAL_RECEIPT_V1"
)
RESEARCH_ONLY = "RESEARCH_ONLY"

_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_BASE_UNSIGNED_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "generation_id",
        "stage",
        "terminal_status",
        "reason_code",
        "registry_sha256",
        "code_package_sha256",
        "runtime_sha256",
    }
)
_STAGE_HASH_FIELDS: dict[str, tuple[str, ...]] = {
    "METADATA": (),
    "DEVELOPMENT": ("development_method_freeze_sha256",),
    "HISTORICAL_WF": (
        "development_method_freeze_sha256",
        "research_freeze_sha256",
    ),
    "FORWARD": (
        "development_method_freeze_sha256",
        "research_freeze_sha256",
        "historical_theta_sha256",
        "forward_receipt_sha256",
        "qualification_sha256",
    ),
}
_STAGE_TERMINAL_REASONS: dict[str, dict[str, str]] = {
    "METADATA": {
        "DATA_NOT_QUALIFIED": "METADATA_SCHEMA_NOT_QUALIFIED",
        "INSUFFICIENT_EVIDENCE": "METADATA_COVERAGE_INSUFFICIENT",
    },
    "DEVELOPMENT": {
        "INSUFFICIENT_EVIDENCE": "DEVELOPMENT_EVIDENCE_INSUFFICIENT",
        "NO_QUALIFIED_POLICY": "ALL_DEVELOPMENT_REJECTED",
    },
    "HISTORICAL_WF": {
        "INSUFFICIENT_EVIDENCE": "HISTORICAL_WF_EVIDENCE_INSUFFICIENT",
        "NO_QUALIFIED_POLICY": "ALL_HISTORICAL_WF_REJECTED",
    },
    "FORWARD": {
        "INSUFFICIENT_EVIDENCE": "FORWARD_EVIDENCE_INSUFFICIENT",
        "NO_QUALIFIED_POLICY": "FORWARD_LCB_NONPOSITIVE",
    },
}
_AUTHORITY_REASON_TOKENS = frozenset(
    {
        "ACCEPTED",
        "APPROVAL",
        "APPROVE",
        "APPROVED",
        "AUTHORITY",
        "AUTHORIZATION",
        "AUTHORIZE",
        "AUTHORIZED",
        "GRANTED",
        "OWNER",
        "PERMISSION",
        "PERMITTED",
    }
)


def _hash(value: object, reason_code: str = "R2_TERMINAL_HASH_INVALID") -> str:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        raise R2QualificationError(reason_code)
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise R2QualificationError("R2_GENERATION_ID_INVALID")
    return value


def _reason(value: object) -> str:
    if type(value) is not str or _REASON_RE.fullmatch(value) is None:
        raise R2QualificationError("R2_TERMINAL_REASON_CODE_INVALID")
    if _AUTHORITY_REASON_TOKENS.intersection(value.split("_")):
        raise R2QualificationError("R2_TERMINAL_REASON_AUTHORITY_INVALID")
    return value


def _assert_stage_semantics(
    stage: object,
    terminal_status: object,
    reason_code: object,
) -> str:
    if type(stage) is not str or stage not in _STAGE_HASH_FIELDS:
        raise R2QualificationError("R2_TERMINAL_STAGE_INVALID")
    if (
        type(terminal_status) is not str
        or terminal_status not in _STAGE_TERMINAL_REASONS[stage]
    ):
        raise R2QualificationError("R2_TERMINAL_STATUS_STAGE_INVALID")
    reason = _reason(reason_code)
    if reason != _STAGE_TERMINAL_REASONS[stage][terminal_status]:
        raise R2QualificationError("R2_TERMINAL_REASON_STAGE_INVALID")
    return stage


def _assert_forward_qualification(
    *,
    historical_theta_sha256: object,
    forward_receipt_sha256: object,
    terminal_status: str,
    qualification_sha256: object,
) -> str:
    historical_hash = _hash(
        historical_theta_sha256, "R2_TERMINAL_STAGE_BINDING_INVALID"
    )
    forward_hash = _hash(
        forward_receipt_sha256, "R2_TERMINAL_STAGE_BINDING_INVALID"
    )
    qualification_hash = _hash(
        qualification_sha256, "R2_TERMINAL_STAGE_BINDING_INVALID"
    )
    expected = derive_qualification_sha256(
        historical_theta_sha256=historical_hash,
        forward_receipt_sha256=forward_hash,
        machine_terminal_status=terminal_status,
    )
    if qualification_hash != expected:
        raise R2QualificationError("R2_TERMINAL_QUALIFICATION_BINDING_INVALID")
    return qualification_hash


@dataclass(frozen=True, slots=True)
class R2GenerationTerminalReceiptV1:
    schema_version: str
    classification: str
    generation_id: str
    stage: str
    terminal_status: str
    reason_code: str
    registry_sha256: str
    code_package_sha256: str
    runtime_sha256: str
    development_method_freeze_sha256: str | None
    research_freeze_sha256: str | None
    historical_theta_sha256: str | None
    forward_receipt_sha256: str | None
    qualification_sha256: str | None
    receipt_sha256: str
    _canonical_bytes: bytes = field(repr=False, compare=False)

    def _stage_hash_values(self) -> dict[str, str | None]:
        return {
            "development_method_freeze_sha256": self.development_method_freeze_sha256,
            "research_freeze_sha256": self.research_freeze_sha256,
            "historical_theta_sha256": self.historical_theta_sha256,
            "forward_receipt_sha256": self.forward_receipt_sha256,
            "qualification_sha256": self.qualification_sha256,
        }

    def _unsigned_dict(self) -> dict[str, object]:
        stage = _assert_stage_semantics(
            self.stage, self.terminal_status, self.reason_code
        )
        all_values = self._stage_hash_values()
        required = frozenset(_STAGE_HASH_FIELDS[stage])
        for name, value in all_values.items():
            if name in required:
                _hash(value, "R2_TERMINAL_STAGE_BINDING_INVALID")
            elif value is not None:
                raise R2QualificationError("R2_TERMINAL_STAGE_BINDING_INVALID")
        if stage == "FORWARD":
            _assert_forward_qualification(
                historical_theta_sha256=all_values[
                    "historical_theta_sha256"
                ],
                forward_receipt_sha256=all_values["forward_receipt_sha256"],
                terminal_status=self.terminal_status,
                qualification_sha256=all_values["qualification_sha256"],
            )
        unsigned: dict[str, object] = {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "generation_id": self.generation_id,
            "stage": stage,
            "terminal_status": self.terminal_status,
            "reason_code": self.reason_code,
            "registry_sha256": self.registry_sha256,
            "code_package_sha256": self.code_package_sha256,
            "runtime_sha256": self.runtime_sha256,
        }
        unsigned.update({name: all_values[name] for name in _STAGE_HASH_FIELDS[stage]})
        return unsigned

    def as_dict(self) -> dict[str, object]:
        unsigned = self._unsigned_dict()
        if self.schema_version != GENERATION_TERMINAL_RECEIPT_SCHEMA_VERSION:
            raise R2QualificationError("R2_TERMINAL_RECEIPT_VERSION_INVALID")
        if self.classification != RESEARCH_ONLY:
            raise R2QualificationError("R2_TERMINAL_CLASSIFICATION_INVALID")
        _identifier(self.generation_id)
        _reason(self.reason_code)
        _hash(self.registry_sha256)
        _hash(self.code_package_sha256)
        _hash(self.runtime_sha256)
        _hash(self.receipt_sha256, "R2_TERMINAL_RECEIPT_HASH_INVALID")
        if canonical_sha256(unsigned) != self.receipt_sha256:
            raise R2QualificationError("R2_TERMINAL_RECEIPT_HASH_MISMATCH")
        document = {**unsigned, "receipt_sha256": self.receipt_sha256}
        if canonical_json_bytes(document) != self._canonical_bytes:
            raise R2QualificationError("R2_TERMINAL_RECEIPT_INTEGRITY_MISMATCH")
        return document


def build_generation_terminal_receipt(
    *,
    generation_id: str,
    stage: str,
    terminal_status: str,
    reason_code: str,
    registry_sha256: str,
    code_package_sha256: str,
    runtime_sha256: str,
    development_method_freeze_sha256: str | None = None,
    research_freeze_sha256: str | None = None,
    historical_theta_sha256: str | None = None,
    forward_receipt_sha256: str | None = None,
    qualification_sha256: str | None = None,
) -> R2GenerationTerminalReceiptV1:
    """Create one terminal receipt with only the hashes available at its stage."""

    stage = _assert_stage_semantics(stage, terminal_status, reason_code)
    values = {
        "development_method_freeze_sha256": development_method_freeze_sha256,
        "research_freeze_sha256": research_freeze_sha256,
        "historical_theta_sha256": historical_theta_sha256,
        "forward_receipt_sha256": forward_receipt_sha256,
        "qualification_sha256": qualification_sha256,
    }
    if stage == "FORWARD":
        historical_hash = _hash(
            historical_theta_sha256, "R2_TERMINAL_STAGE_BINDING_INVALID"
        )
        forward_hash = _hash(
            forward_receipt_sha256, "R2_TERMINAL_STAGE_BINDING_INVALID"
        )
        derived_qualification = derive_qualification_sha256(
            historical_theta_sha256=historical_hash,
            forward_receipt_sha256=forward_hash,
            machine_terminal_status=terminal_status,
        )
        if qualification_sha256 is not None:
            supplied_qualification = _hash(
                qualification_sha256, "R2_TERMINAL_STAGE_BINDING_INVALID"
            )
            if supplied_qualification != derived_qualification:
                raise R2QualificationError(
                    "R2_TERMINAL_QUALIFICATION_BINDING_INVALID"
                )
        values["qualification_sha256"] = derived_qualification
    required = frozenset(_STAGE_HASH_FIELDS[stage])
    if any(
        (name in required and value is None)
        or (name not in required and value is not None)
        for name, value in values.items()
    ):
        raise R2QualificationError("R2_TERMINAL_STAGE_BINDING_INVALID")
    unsigned: dict[str, object] = {
        "schema_version": GENERATION_TERMINAL_RECEIPT_SCHEMA_VERSION,
        "classification": RESEARCH_ONLY,
        "generation_id": _identifier(generation_id),
        "stage": stage,
        "terminal_status": terminal_status,
        "reason_code": _reason(reason_code),
        "registry_sha256": _hash(registry_sha256),
        "code_package_sha256": _hash(code_package_sha256),
        "runtime_sha256": _hash(runtime_sha256),
    }
    for name in _STAGE_HASH_FIELDS[stage]:
        unsigned[name] = _hash(values[name], "R2_TERMINAL_STAGE_BINDING_INVALID")
    receipt_hash = canonical_sha256(unsigned)
    document = {**unsigned, "receipt_sha256": receipt_hash}
    return validate_generation_terminal_receipt(document)


def validate_generation_terminal_receipt(
    document: object,
) -> R2GenerationTerminalReceiptV1:
    """Validate an exact stage-specific terminal union and its content hash."""

    if type(document) is not dict:
        raise R2QualificationError("R2_TERMINAL_RECEIPT_SCHEMA_INVALID")
    stage = document.get("stage")
    if type(stage) is not str or stage not in _STAGE_HASH_FIELDS:
        raise R2QualificationError("R2_TERMINAL_STAGE_INVALID")
    expected_keys = (
        _BASE_UNSIGNED_KEYS
        | frozenset(_STAGE_HASH_FIELDS[stage])
        | {"receipt_sha256"}
    )
    if frozenset(document) != expected_keys:
        raise R2QualificationError("R2_TERMINAL_RECEIPT_SCHEMA_INVALID")
    canonical_json_bytes(document)
    if document["schema_version"] != GENERATION_TERMINAL_RECEIPT_SCHEMA_VERSION:
        raise R2QualificationError("R2_TERMINAL_RECEIPT_VERSION_INVALID")
    if document["classification"] != RESEARCH_ONLY:
        raise R2QualificationError("R2_TERMINAL_CLASSIFICATION_INVALID")
    _assert_stage_semantics(
        stage, document["terminal_status"], document["reason_code"]
    )
    generation_id = _identifier(document["generation_id"])
    reason_code = document["reason_code"]
    registry_hash = _hash(document["registry_sha256"])
    code_hash = _hash(document["code_package_sha256"])
    runtime_hash = _hash(document["runtime_sha256"])
    receipt_hash = _hash(
        document["receipt_sha256"], "R2_TERMINAL_RECEIPT_HASH_INVALID"
    )
    unsigned = {key: document[key] for key in document if key != "receipt_sha256"}
    if canonical_sha256(unsigned) != receipt_hash:
        raise R2QualificationError("R2_TERMINAL_RECEIPT_HASH_MISMATCH")
    stage_values = {
        name: (
            _hash(document[name], "R2_TERMINAL_STAGE_BINDING_INVALID")
            if name in _STAGE_HASH_FIELDS[stage]
            else None
        )
        for name in (
            "development_method_freeze_sha256",
            "research_freeze_sha256",
            "historical_theta_sha256",
            "forward_receipt_sha256",
            "qualification_sha256",
        )
    }
    if stage == "FORWARD":
        _assert_forward_qualification(
            historical_theta_sha256=stage_values["historical_theta_sha256"],
            forward_receipt_sha256=stage_values["forward_receipt_sha256"],
            terminal_status=document["terminal_status"],
            qualification_sha256=stage_values["qualification_sha256"],
        )
    return R2GenerationTerminalReceiptV1(
        schema_version=GENERATION_TERMINAL_RECEIPT_SCHEMA_VERSION,
        classification=RESEARCH_ONLY,
        generation_id=generation_id,
        stage=stage,
        terminal_status=document["terminal_status"],
        reason_code=reason_code,
        registry_sha256=registry_hash,
        code_package_sha256=code_hash,
        runtime_sha256=runtime_hash,
        development_method_freeze_sha256=stage_values[
            "development_method_freeze_sha256"
        ],
        research_freeze_sha256=stage_values["research_freeze_sha256"],
        historical_theta_sha256=stage_values["historical_theta_sha256"],
        forward_receipt_sha256=stage_values["forward_receipt_sha256"],
        qualification_sha256=stage_values["qualification_sha256"],
        receipt_sha256=receipt_hash,
        _canonical_bytes=canonical_json_bytes(document),
    )


__all__ = [
    "GENERATION_TERMINAL_RECEIPT_SCHEMA_VERSION",
    "R2GenerationTerminalReceiptV1",
    "build_generation_terminal_receipt",
    "validate_generation_terminal_receipt",
]
