"""Command-line entry point for the fixed CRR P1 T1 preflight."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


if __package__ in {None, ""}:
    repository_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository_root))
    sys.path.insert(0, str(repository_root / "src"))

from benchmarks.crr_p1_t1_preflight import (  # noqa: E402
    T1PreflightError,
    run_formal_t1_preflight,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the fixed GLD CRR P1 T1 watchdog preflight."
    )
    parser.add_argument(
        "--performance-component-aggregate-path",
        required=True,
        type=Path,
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
        output_path = run_formal_t1_preflight(
            performance_component_aggregate_path=(
                arguments.performance_component_aggregate_path
            ),
            output_directory=arguments.output_directory,
        )
    except (OSError, T1PreflightError, ValueError) as error:
        reason = (
            error.reason_code
            if isinstance(error, T1PreflightError)
            else "T1_CLI_INPUT_INVALID"
        )
        print(reason, file=sys.stderr)
        return 1
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
