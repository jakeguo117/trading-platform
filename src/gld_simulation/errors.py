"""Stable fail-closed errors for the simulation-only raw bundle boundary."""

from __future__ import annotations


class RawBundleError(ValueError):
    """A raw bundle contract failure with a stable ASCII reason code."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        if not reason_code or not reason_code.isascii():
            raise ValueError("reason_code must be non-empty ASCII")
        self.reason_code = reason_code
        self.detail = detail
        message = reason_code if detail is None else f"{reason_code}: {detail}"
        super().__init__(message)


__all__ = ["RawBundleError"]
