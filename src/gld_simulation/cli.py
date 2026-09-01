"""Thin local CLI for the simulation-only GLD decision-card workflow."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Iterable

from gld_normalizer.errors import NormalizationError

from .errors import RawBundleError
from .pipeline import SimulationPipelineError
from .workflow import run_simulation_workflow


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--native-manifest", type=Path, required=True)
    parser.add_argument(
        "--expected-backend-evidence-sha256",
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = run_simulation_workflow(
            bundle_root=arguments.bundle,
            native_manifest_path=arguments.native_manifest,
            expected_backend_evidence_sha256=(
                arguments.expected_backend_evidence_sha256
            ),
            output_dir=arguments.output,
        )
    except (RawBundleError, SimulationPipelineError, NormalizationError) as error:
        reason_code = getattr(error, "reason_code", "SIMULATION_FAILED")
        print(reason_code, file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("SIMULATION_FAILED", file=sys.stderr)
        return 1
    if result.status != "SUCCESS":
        reason_code = result.decision_document.get("reason_code")
        if type(reason_code) is not str or not reason_code.isascii():
            reason_code = "SIMULATION_FAIL_CLOSED"
        print(reason_code, file=sys.stderr)
        return 1
    print(result.decision_card_path)
    return 0


__all__ = ["main"]
