"""Stable fail-closed errors for GLD Entry Decision f F0."""

from __future__ import annotations

import re


_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")


class EntryDecisionF0Error(ValueError):
    """Contract or evaluation failure carrying a stable reason code."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        if type(reason_code) is not str or _REASON_RE.fullmatch(reason_code) is None:
            raise ValueError("reason_code must be stable uppercase ASCII")
        if detail is not None and (type(detail) is not str or not detail.isascii()):
            raise ValueError("detail must be ASCII text")
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(reason_code if detail is None else f"{reason_code}: {detail}")


__all__ = ["EntryDecisionF0Error"]
