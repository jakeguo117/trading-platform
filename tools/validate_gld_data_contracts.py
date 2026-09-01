#!/usr/bin/env python3
"""Run the local GLD Data Contracts V1 acceptance CLI."""

from gld_data_contracts.cli import guarded_main


if __name__ == "__main__":
    raise SystemExit(guarded_main())
