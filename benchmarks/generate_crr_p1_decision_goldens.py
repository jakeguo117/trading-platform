"""Offline one-shot sealer for additive CRR P1 Decision semantic goldens.

The generator reads only already-sealed local benchmark artifacts and contract
bytes.  It has no provider, broker, order, native execution, or network path.
Any pre-existing target makes the entire seal operation fail before writing.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path

from .crr_p1_contract import canonical_json_bytes
from .crr_p1_decision_contract import (
    DecisionContractError,
    _build_decision_static_manifest_document,
    derive_decision_semantic_document,
)


_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
_TARGET_NAMES = (
    "u64_decision_semantic_golden_v0.1.json",
    "w64_decision_semantic_golden_v0.1.json",
    "decision_static_manifest_v0.1.json",
)


def seal_decision_goldens(
    *,
    output_root: Path = _FIXTURE_ROOT,
) -> tuple[Path, Path, Path]:
    """Seal both semantic goldens and their manifest exactly once."""

    if not isinstance(output_root, Path) or not output_root.is_dir():
        raise DecisionContractError("DECISION_OUTPUT_ROOT_INVALID")
    targets = tuple(output_root / name for name in _TARGET_NAMES)
    if any(path.exists() for path in targets):
        raise DecisionContractError("DECISION_RESEAL_REFUSED")

    u64_bytes = canonical_json_bytes(derive_decision_semantic_document("U64"))
    w64_bytes = canonical_json_bytes(derive_decision_semantic_document("W64"))
    file_hashes = {
        "fixtures/u64_decision_semantic_golden_v0.1.json": sha256(
            u64_bytes
        ).hexdigest(),
        "fixtures/w64_decision_semantic_golden_v0.1.json": sha256(
            w64_bytes
        ).hexdigest(),
    }
    manifest_bytes = canonical_json_bytes(
        _build_decision_static_manifest_document(file_hashes)
    )

    # All bytes are fully derived before the first write.  A partial filesystem
    # failure remains visible and the next invocation refuses to overwrite it.
    for path, raw in zip(
        targets,
        (u64_bytes, w64_bytes, manifest_bytes),
        strict=True,
    ):
        path.write_bytes(raw)
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seal additive CRR P1 Decision semantic goldens once."
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=_FIXTURE_ROOT,
        help="Existing directory for the three new sealed files.",
    )
    arguments = parser.parse_args(argv)
    try:
        created = seal_decision_goldens(output_root=arguments.output_root)
    except DecisionContractError as error:
        parser.error(error.reason_code)
    for path in created:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
