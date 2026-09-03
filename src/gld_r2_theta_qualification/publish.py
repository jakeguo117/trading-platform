"""R2 adapters for canonical input, sealed manifests, and atomic publication."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re

from gld_entry_decision_f0.errors import EntryDecisionF0Error
from gld_entry_decision_f0.publish import (
    publish_artifacts as _publish_r1_artifacts,
)
from gld_entry_decision_f0.publish import (
    read_canonical_json_file as _read_r1_canonical_json_file,
)

from .contracts import (
    R2QualificationError,
    canonical_json_bytes,
    canonical_sha256,
)


ACCEPTANCE_MANIFEST_SCHEMA_VERSION = (
    "GLD_R2_SYNTHETIC_ACCEPTANCE_MANIFEST_V1"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_EXPECTED_SCENARIOS = (
    "contract_identity",
    "registry_16_to_12",
    "entry_label_windows",
    "policy_q1_stress",
    "joint_statistics",
    "forward_exact_100",
)
_EXPECTED_SCENARIO_META = {
    "contract_identity": (
        "Contract and identity chain",
        "Local canonical and non-cyclic identity only; no theta or qualification.",
        "SYNTHETIC_CONTRACT_DIAGNOSTIC_PASS",
    ),
    "registry_16_to_12": (
        "16 candidates and 12 promotable",
        "Synthetic 4 Gate x 4 Hard Stop registry only; no real ranking.",
        "SYNTHETIC_REGISTRY_DIAGNOSTIC_PASS",
    ),
    "entry_label_windows": (
        "Windows and common mask",
        "Synthetic ENTRY/LABEL20 and common mask only; no market data.",
        "SYNTHETIC_WINDOW_DIAGNOSTIC_PASS",
    ),
    "policy_q1_stress": (
        "Policy q1 and stress path",
        "Synthetic q=1 selectors, stress, and exit priority only; no order.",
        "SYNTHETIC_POLICY_DIAGNOSTIC_PASS",
    ),
    "joint_statistics": (
        "Carrier and joint statistics",
        "Synthetic fixed-seed MBB only; no real qualification.",
        "SYNTHETIC_STATISTICS_DIAGNOSTIC_PASS",
    ),
    "forward_exact_100": (
        "Exact-100 Forward state machine",
        "Synthetic Exact-100 state machine only; not Forward evidence.",
        "SYNTHETIC_FORWARD_DIAGNOSTIC_PASS",
    ),
}
_EXPECTED_SCENARIO_RESULTS = {
    "contract_identity": {
        "canonical_round_trip": True,
        "diagnostic_status": "PASS",
        "identity_chain": "NON_CYCLIC",
    },
    "registry_16_to_12": {
        "control_count": 4,
        "diagnostic_status": "PASS",
        "promotable_count": 12,
        "registry_count": 16,
    },
    "entry_label_windows": {
        "base_entry_sessions": 20,
        "coverage_ppm": 950_000,
        "development_entry_sessions": 20,
        "diagnostic_status": "PASS",
        "walk_forward_fold_count": 5,
    },
    "policy_q1_stress": {
        "bcs0_selector_status": "PASS",
        "diagnostic_status": "PASS",
        "exit_trigger": "HARD_STOP",
        "gate_status": "PASS",
        "lc0_selector_status": "PASS",
        "ledger_entry_return_ppm": 25_000,
        "preferred_carrier": "BCS0",
        "stressed_episode_status": "PASS",
    },
    "joint_statistics": {
        "block_length": 20,
        "bootstrap_replicates": 10_000,
        "carrier_lower_bound_ppm": 25_000,
        "diagnostic_status": "PASS",
        "joint_candidate_passes": [True, True],
        "joint_lower_bounds_ppm": [20_000, 30_000],
    },
    "forward_exact_100": {
        "coverage_ppm": 1_000_000,
        "diagnostic_status": "PASS",
        "exact_episode_count": 100,
        "forward_lower_bound_ppm": 20_000,
        "sealed": True,
    },
}
_EXPECTED_R1_TREE_SHA256 = (
    "751c4d70723fa64a04d5feafa3bf243ee9c8119031c85a454ad8ef28d80d9934"
)
_EXPECTED_R1_OWNER_RECEIPT_SHA256 = (
    "e894aa5777a5f7b2179eb4dc3ebd8b80f12c3e80d74c2c2adca312cdb3d36231"
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "scope",
        "authority_status",
        "status",
        "next",
        "actionable",
        "broker_order_count",
        "real_data_accessed",
        "real_theta_produced",
        "scientific_terminal_generated",
        "qualification_generated",
        "owner_receipt_generated",
        "owner_status",
        "candidate_flow",
        "r1_regression_receipt",
        "determinism_receipt",
        "scenarios",
        "rendered_artifacts",
        "manifest_sha256",
    }
)
_CANDIDATE_FLOW_KEYS = frozenset(
    {
        "registry_count",
        "promotable_count",
        "control_count",
        "evaluated_k",
        "evaluated_k_status",
        "historical_theta_count",
        "forward_theta_count",
        "final_theta_count",
    }
)
_SCENARIO_KEYS = frozenset(
    {
        "scenario_id",
        "title_zh",
        "classification",
        "expected",
        "actual",
        "reason_code",
        "automated_evidence_status",
        "owner_status",
        "input_sha256",
        "result_sha256",
        "artifact_hashes",
        "input_path",
        "result_path",
        "test_boundary_zh",
    }
)
_R1_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "accepted_commit",
        "status",
        "tree_root",
        "tree_file_count",
        "tree_sha256",
        "assets",
        "receipt_sha256",
    }
)
_R1_ASSET_KEYS = frozenset(
    {
        "classification",
        "role",
        "path",
        "expected_sha256",
        "actual_sha256",
        "status",
    }
)
_DETERMINISM_KEYS = frozenset(
    {
        "schema_version",
        "classification",
        "status",
        "first_run_sha256",
        "second_run_sha256",
        "receipt_sha256",
    }
)
_ARTIFACT_HASH_KEYS = frozenset(
    {"input_file_sha256", "result_file_sha256"}
)
_RENDERED_ARTIFACT_KEYS = frozenset({"path", "sha256"})


class _ManifestSeal:
    """Bind validated bytes to the exact instance produced by the validator."""

    __slots__ = ("_canonical_bytes", "_manifest_sha256", "_owner")

    def __init__(self) -> None:
        object.__setattr__(self, "_canonical_bytes", None)
        object.__setattr__(self, "_manifest_sha256", None)
        object.__setattr__(self, "_owner", None)

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("manifest seals are immutable")

    def bind(
        self,
        owner: object,
        *,
        canonical_bytes: bytes,
        manifest_sha256: str,
    ) -> None:
        if self._owner is not None:
            raise RuntimeError("manifest seal is already bound")
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_canonical_bytes", canonical_bytes)
        object.__setattr__(self, "_manifest_sha256", manifest_sha256)

    def matches(
        self,
        owner: object,
        *,
        canonical_bytes: bytes,
        manifest_sha256: str,
    ) -> bool:
        return (
            self._owner is owner
            and self._canonical_bytes == canonical_bytes
            and self._manifest_sha256 == manifest_sha256
        )


def read_canonical_json_file(path: Path) -> object:
    """Reuse the R1 strict reader and adapt only its error type."""

    try:
        return _read_r1_canonical_json_file(path)
    except EntryDecisionF0Error as exc:
        raise R2QualificationError(exc.reason_code, exc.detail) from exc


def publish_artifacts(output_directory: Path, artifacts: dict[str, bytes]) -> None:
    """Reuse R1's directory-level atomic no-clobber publisher."""

    try:
        _publish_r1_artifacts(output_directory, artifacts)
    except EntryDecisionF0Error as exc:
        raise R2QualificationError(exc.reason_code, exc.detail) from exc


def _hash(value: object, reason: str = "R2_ACCEPTANCE_HASH_INVALID") -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise R2QualificationError(reason)
    return value


def _safe_relative_path(value: object) -> str:
    if type(value) is not str or not value or "\\" in value or ":" in value:
        raise R2QualificationError("R2_ACCEPTANCE_PATH_INVALID")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise R2QualificationError("R2_ACCEPTANCE_PATH_INVALID")
    return value


def _sealed_receipt_hash(document: dict[str, object], key: str) -> None:
    unsigned = dict(document)
    sealed = _hash(unsigned.pop(key))
    if canonical_sha256(unsigned) != sealed:
        raise R2QualificationError("R2_ACCEPTANCE_RECEIPT_INTEGRITY_MISMATCH")


@dataclass(frozen=True, slots=True)
class R2SyntheticAcceptanceManifestV1:
    """Immutable validated projection source for both R2 acceptance pages."""

    manifest_sha256: str
    _canonical_bytes: bytes
    _seal: _ManifestSeal

    def __copy__(self) -> R2SyntheticAcceptanceManifestV1:
        return self

    def __deepcopy__(
        self,
        memo: dict[int, object],
    ) -> R2SyntheticAcceptanceManifestV1:
        memo[id(self)] = self
        return self

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("validated acceptance manifests cannot be pickled")

    @property
    def document(self) -> dict[str, object]:
        if type(self._seal) is not _ManifestSeal:
            raise R2QualificationError("R2_ACCEPTANCE_MANIFEST_INTEGRITY_MISMATCH")
        if type(self._canonical_bytes) is not bytes:
            raise R2QualificationError("R2_ACCEPTANCE_MANIFEST_INTEGRITY_MISMATCH")
        try:
            value = json.loads(self._canonical_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise R2QualificationError(
                "R2_ACCEPTANCE_MANIFEST_INTEGRITY_MISMATCH"
            ) from exc
        if type(value) is not dict:
            raise R2QualificationError("R2_ACCEPTANCE_MANIFEST_INTEGRITY_MISMATCH")
        encoded, sealed_hash = _validate_manifest_document(value)
        if (
            encoded != self._canonical_bytes
            or sealed_hash != self.manifest_sha256
            or not self._seal.matches(
                self,
                canonical_bytes=self._canonical_bytes,
                manifest_sha256=self.manifest_sha256,
            )
        ):
            raise R2QualificationError("R2_ACCEPTANCE_MANIFEST_INTEGRITY_MISMATCH")
        return value


def _validate_r1_receipt(value: object) -> None:
    if type(value) is not dict or frozenset(value) != _R1_RECEIPT_KEYS:
        raise R2QualificationError("R2_R1_REGRESSION_RECEIPT_INVALID")
    if (
        value["schema_version"] != "GLD_R2_R1_REGRESSION_RECEIPT_V1"
        or value["classification"] != "SYNTHETIC_ONLY"
        or value["accepted_commit"]
        != "b21400d799f7b441d35f528a5980b1ce855997a7"
        or value["status"] != "R1_ACCEPTED_BYTES_UNCHANGED"
        or value["tree_file_count"] != 45
        or value["tree_sha256"] != _EXPECTED_R1_TREE_SHA256
    ):
        raise R2QualificationError("R2_R1_REGRESSION_RECEIPT_INVALID")
    tree_root = _safe_relative_path(value["tree_root"])
    assets = value["assets"]
    if type(assets) is not list or len(assets) != 46:
        raise R2QualificationError("R2_R1_REGRESSION_RECEIPT_INVALID")
    paths: list[str] = []
    tree_rows: list[dict[str, object]] = []
    owner_count = 0
    for asset in assets:
        if type(asset) is not dict or frozenset(asset) != _R1_ASSET_KEYS:
            raise R2QualificationError("R2_R1_REGRESSION_RECEIPT_INVALID")
        path = _safe_relative_path(asset["path"])
        paths.append(path)
        role = asset["role"]
        if (
            asset["classification"] != "SYNTHETIC_ONLY"
            or asset["status"] != "BYTE_IDENTICAL"
            or _hash(asset["expected_sha256"])
            != _hash(asset["actual_sha256"])
        ):
            raise R2QualificationError("R2_R1_REGRESSION_RECEIPT_INVALID")
        if role == "TREE_FILE":
            prefix = f"{tree_root}/"
            if not path.startswith(prefix):
                raise R2QualificationError("R2_R1_REGRESSION_RECEIPT_INVALID")
            tree_rows.append(
                {
                    "path": path.removeprefix(prefix),
                    "sha256": asset["actual_sha256"],
                }
            )
        elif role == "OWNER_RECEIPT":
            owner_count += 1
            if asset["actual_sha256"] != _EXPECTED_R1_OWNER_RECEIPT_SHA256:
                raise R2QualificationError("R2_R1_REGRESSION_RECEIPT_INVALID")
        else:
            raise R2QualificationError("R2_R1_REGRESSION_RECEIPT_INVALID")
    if len(paths) != len(set(paths)):
        raise R2QualificationError("R2_R1_REGRESSION_RECEIPT_INVALID")
    tree_document = {
        "schema_version": "GLD_R1_ACCEPTANCE_TREE_DIGEST_V1",
        "file_count": len(tree_rows),
        "files": tree_rows,
    }
    if (
        len(tree_rows) != 45
        or owner_count != 1
        or canonical_sha256(tree_document) != value["tree_sha256"]
    ):
        raise R2QualificationError("R2_R1_REGRESSION_RECEIPT_INVALID")
    _sealed_receipt_hash(value, "receipt_sha256")


def _validate_determinism_receipt(value: object) -> None:
    if type(value) is not dict or frozenset(value) != _DETERMINISM_KEYS:
        raise R2QualificationError("R2_DETERMINISM_RECEIPT_INVALID")
    first = _hash(value["first_run_sha256"])
    second = _hash(value["second_run_sha256"])
    if (
        value["schema_version"] != "GLD_R2_DETERMINISM_RECEIPT_V1"
        or value["classification"] != "SYNTHETIC_ONLY"
        or value["status"]
        != "REPEATED_SYNTHETIC_DIAGNOSTICS_BYTE_IDENTICAL"
        or first != second
    ):
        raise R2QualificationError("R2_DETERMINISM_RECEIPT_INVALID")
    _sealed_receipt_hash(value, "receipt_sha256")


def _validate_scenarios(value: object) -> None:
    if type(value) is not list or len(value) != len(_EXPECTED_SCENARIOS):
        raise R2QualificationError("R2_ACCEPTANCE_SCENARIOS_INVALID")
    identifiers: list[str] = []
    paths: list[str] = []
    for row in value:
        if type(row) is not dict or frozenset(row) != _SCENARIO_KEYS:
            raise R2QualificationError("R2_ACCEPTANCE_SCENARIO_SCHEMA_INVALID")
        identifier = row["scenario_id"]
        if type(identifier) is not str or _IDENTIFIER_RE.fullmatch(identifier) is None:
            raise R2QualificationError("R2_ACCEPTANCE_SCENARIO_ID_INVALID")
        identifiers.append(identifier)
        expected_meta = _EXPECTED_SCENARIO_META.get(identifier)
        expected_result = _EXPECTED_SCENARIO_RESULTS.get(identifier)
        if (
            type(row["title_zh"]) is not str
            or not row["title_zh"]
            or type(row["test_boundary_zh"]) is not str
            or not row["test_boundary_zh"]
            or row["classification"] != "SYNTHETIC_ONLY"
            or row["automated_evidence_status"] != "AUTOMATED_EVIDENCE_PASS"
            or row["owner_status"] != "OWNER_ACCEPTANCE_PENDING"
            or row["expected"] != row["actual"]
            or type(row["expected"]) is not dict
            or type(row["actual"]) is not dict
            or type(row["reason_code"]) is not str
            or _REASON_RE.fullmatch(row["reason_code"]) is None
            or expected_meta is None
            or expected_result is None
            or row["expected"] != expected_result
            or row["actual"] != expected_result
            or (
                row["title_zh"],
                row["test_boundary_zh"],
                row["reason_code"],
            )
            != expected_meta
        ):
            raise R2QualificationError("R2_ACCEPTANCE_SCENARIO_SEMANTICS_INVALID")
        input_path = _safe_relative_path(row["input_path"])
        result_path = _safe_relative_path(row["result_path"])
        if (
            input_path != f"scenarios/{identifier}/input.json"
            or result_path != f"scenarios/{identifier}/result.json"
        ):
            raise R2QualificationError("R2_ACCEPTANCE_PATH_INVALID")
        paths.extend((input_path, result_path))
        _hash(row["input_sha256"])
        _hash(row["result_sha256"])
        hashes = row["artifact_hashes"]
        if type(hashes) is not dict or frozenset(hashes) != _ARTIFACT_HASH_KEYS:
            raise R2QualificationError("R2_ACCEPTANCE_ARTIFACT_HASHES_INVALID")
        for artifact_hash in hashes.values():
            _hash(artifact_hash)
    if tuple(identifiers) != _EXPECTED_SCENARIOS or len(paths) != len(set(paths)):
        raise R2QualificationError("R2_ACCEPTANCE_SCENARIOS_INVALID")


def _validate_rendered_artifacts(value: object) -> None:
    if type(value) is not list or len(value) != 2:
        raise R2QualificationError("R2_ACCEPTANCE_RENDERED_ARTIFACTS_INVALID")
    paths: list[str] = []
    for row in value:
        if type(row) is not dict or frozenset(row) != _RENDERED_ARTIFACT_KEYS:
            raise R2QualificationError("R2_ACCEPTANCE_RENDERED_ARTIFACTS_INVALID")
        paths.append(_safe_relative_path(row["path"]))
        _hash(row["sha256"])
    if tuple(paths) != ("index.html", "engineering-evidence.html"):
        raise R2QualificationError("R2_ACCEPTANCE_RENDERED_ARTIFACTS_INVALID")


def _validate_manifest_document(document: object) -> tuple[bytes, str]:
    """Return canonical bytes and seal after complete closed-schema validation."""

    if type(document) is not dict or frozenset(document) != _MANIFEST_KEYS:
        raise R2QualificationError("R2_ACCEPTANCE_MANIFEST_SCHEMA_INVALID")
    encoded = canonical_json_bytes(document)
    if (
        document["schema_version"] != ACCEPTANCE_MANIFEST_SCHEMA_VERSION
        or document["classification"] != "SYNTHETIC_ONLY"
        or document["scope"] != "RESEARCH_ONLY"
        or document["authority_status"] != "NO_DECISION_EFFECT"
        or document["status"] != "R2_INFRASTRUCTURE_VERIFIED"
        or document["next"] != "METADATA_AUTHORIZATION_REQUIRED"
        or document["actionable"] is not False
        or document["broker_order_count"] != 0
        or document["real_data_accessed"] is not False
        or document["real_theta_produced"] is not False
        or document["scientific_terminal_generated"] is not False
        or document["qualification_generated"] is not False
        or document["owner_receipt_generated"] is not False
        or document["owner_status"] != "OWNER_ACCEPTANCE_PENDING"
    ):
        raise R2QualificationError("R2_ACCEPTANCE_MANIFEST_SEMANTICS_INVALID")
    flow = document["candidate_flow"]
    if (
        type(flow) is not dict
        or frozenset(flow) != _CANDIDATE_FLOW_KEYS
        or flow["registry_count"] != 16
        or flow["promotable_count"] != 12
        or flow["control_count"] != 4
        or flow["evaluated_k"] is not None
        or flow["evaluated_k_status"] != "NOT_RUN_REAL_DATA"
        or flow["historical_theta_count"] != 0
        or flow["forward_theta_count"] != 0
        or flow["final_theta_count"] != 0
    ):
        raise R2QualificationError("R2_ACCEPTANCE_CANDIDATE_FLOW_INVALID")
    _validate_r1_receipt(document["r1_regression_receipt"])
    _validate_determinism_receipt(document["determinism_receipt"])
    _validate_scenarios(document["scenarios"])
    _validate_rendered_artifacts(document["rendered_artifacts"])
    unsigned = dict(document)
    sealed_hash = _hash(unsigned.pop("manifest_sha256"))
    if canonical_sha256(unsigned) != sealed_hash:
        raise R2QualificationError("R2_ACCEPTANCE_MANIFEST_INTEGRITY_MISMATCH")
    return encoded, sealed_hash


def validate_synthetic_acceptance_manifest(
    document: object,
) -> R2SyntheticAcceptanceManifestV1:
    """Validate one closed synthetic-only acceptance manifest."""

    encoded, sealed_hash = _validate_manifest_document(document)
    seal = _ManifestSeal()
    manifest = R2SyntheticAcceptanceManifestV1(
        manifest_sha256=sealed_hash,
        _canonical_bytes=encoded,
        _seal=seal,
    )
    seal.bind(
        manifest,
        canonical_bytes=encoded,
        manifest_sha256=sealed_hash,
    )
    return manifest


__all__ = [
    "ACCEPTANCE_MANIFEST_SCHEMA_VERSION",
    "R2SyntheticAcceptanceManifestV1",
    "publish_artifacts",
    "read_canonical_json_file",
    "validate_synthetic_acceptance_manifest",
]
