"""Command-line entry point for the fixed CRR P1 performance run."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


if __package__ in {None, ""}:
    repository_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository_root))
    sys.path.insert(0, str(repository_root / "src"))

from benchmarks.crr_p1_performance import (  # noqa: E402
    PerformanceRunError,
    run_formal_performance,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the fixed GLD CRR P1 performance evidence workflow."
    )
    parser.add_argument(
        "--native-manifest-path",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--expected-backend-evidence-sha256",
        required=True,
    )
    parser.add_argument(
        "--output-directory",
        required=True,
        type=Path,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        aggregate_path = run_formal_performance(
            native_manifest_path=arguments.native_manifest_path,
            expected_backend_evidence_sha256=(
                arguments.expected_backend_evidence_sha256
            ),
            output_directory=arguments.output_directory,
        )
    except (OSError, PerformanceRunError, ValueError) as error:
        reason = (
            error.reason_code
            if isinstance(error, PerformanceRunError)
            else "PERFORMANCE_CLI_INPUT_INVALID"
        )
        print(reason, file=sys.stderr)
        return 1
    print(aggregate_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
