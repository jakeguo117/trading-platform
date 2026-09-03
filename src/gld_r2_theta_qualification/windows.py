"""Outcome-blind R2 ENTRY/LABEL20 windows and common-row masks."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .contracts import R2QualificationError


PPM = 1_000_000
MIN_COMMON_COVERAGE_PPM = 950_000
LABEL_SESSIONS = 20
WALK_FORWARD_FOLDS = 5
TOTAL_ENTRY_WINDOWS = 1 + WALK_FORWARD_FOLDS

QUALIFIED = "QUALIFIED"
COMMON_DATA_MISSING = "COMMON_DATA_MISSING"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

_COMMON_ROW_STATUSES = frozenset({QUALIFIED, COMMON_DATA_MISSING})
_EVALUABLE_CANDIDATE_ROW_STATUSES = frozenset(
    {"ACTION", "NO_ACTION", "OCCUPIED"}
)
_CANDIDATE_ID_RE = re.compile(r"R2C(?:0[0-9]|1[0-5])\Z")


@dataclass(frozen=True, slots=True)
class HalfOpenSessionWindowV1:
    name: str
    start_index: int
    end_index: int

    @property
    def length(self) -> int:
        return self.end_index - self.start_index

    def contains(self, session_index: int) -> bool:
        return self.start_index <= session_index < self.end_index

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "start_index": self.start_index,
            "end_index": self.end_index,
        }


@dataclass(frozen=True, slots=True)
class EntryLabelSegmentV1:
    entry: HalfOpenSessionWindowV1
    label: HalfOpenSessionWindowV1

    def as_dict(self) -> dict[str, object]:
        return {"entry": self.entry.as_dict(), "label": self.label.as_dict()}


@dataclass(frozen=True, slots=True)
class R2EntryLabelWindowsV1:
    total_after_warmup_sessions: int
    base_entry_sessions: int
    remainder_entry_sessions: int
    development: EntryLabelSegmentV1
    walk_forward: tuple[EntryLabelSegmentV1, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "R2_ENTRY_LABEL_WINDOWS_V1",
            "total_after_warmup_sessions": self.total_after_warmup_sessions,
            "base_entry_sessions": self.base_entry_sessions,
            "remainder_entry_sessions": self.remainder_entry_sessions,
            "development": self.development.as_dict(),
            "walk_forward": [segment.as_dict() for segment in self.walk_forward],
        }


def build_r2_entry_label_windows(
    total_after_warmup_sessions: int,
) -> R2EntryLabelWindowsV1:
    """Build DEV plus WF1..WF5 as interleaved half-open ENTRY/LABEL20 windows."""

    if (
        type(total_after_warmup_sessions) is not int
        or not 0 <= total_after_warmup_sessions <= 10_000_000
    ):
        raise R2QualificationError("R2_WINDOW_SESSION_COUNT_INVALID")
    usable_entry = total_after_warmup_sessions - TOTAL_ENTRY_WINDOWS * LABEL_SESSIONS
    base = usable_entry // TOTAL_ENTRY_WINDOWS
    remainder = usable_entry % TOTAL_ENTRY_WINDOWS
    if base <= 0:
        raise R2QualificationError(
            INSUFFICIENT_EVIDENCE, "R2_ENTRY_WINDOW_BASE_NON_POSITIVE"
        )

    segments: list[EntryLabelSegmentV1] = []
    cursor = 0
    for index in range(TOTAL_ENTRY_WINDOWS):
        label = "DEV" if index == 0 else f"WF{index}"
        entry_length = base + (remainder if index == TOTAL_ENTRY_WINDOWS - 1 else 0)
        entry = HalfOpenSessionWindowV1(
            name=f"{label}_ENTRY",
            start_index=cursor,
            end_index=cursor + entry_length,
        )
        cursor = entry.end_index
        label_window = HalfOpenSessionWindowV1(
            name=f"{label}_LABEL20",
            start_index=cursor,
            end_index=cursor + LABEL_SESSIONS,
        )
        cursor = label_window.end_index
        segments.append(EntryLabelSegmentV1(entry=entry, label=label_window))

    if cursor != total_after_warmup_sessions:
        raise R2QualificationError("R2_WINDOW_CONSTRUCTION_INVARIANT_FAILED")
    return R2EntryLabelWindowsV1(
        total_after_warmup_sessions=total_after_warmup_sessions,
        base_entry_sessions=base,
        remainder_entry_sessions=remainder,
        development=segments[0],
        walk_forward=tuple(segments[1:]),
    )


@dataclass(frozen=True, slots=True)
class CommonMaskAssessmentV1:
    total_entry_rows: int
    evaluable_entry_rows: int
    missing_entry_rows: int
    coverage_ppm: int
    status: str
    reason_code: str

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "R2_COMMON_MASK_ASSESSMENT_V1",
            "total_entry_rows": self.total_entry_rows,
            "evaluable_entry_rows": self.evaluable_entry_rows,
            "missing_entry_rows": self.missing_entry_rows,
            "coverage_ppm": self.coverage_ppm,
            "status": self.status,
            "reason_code": self.reason_code,
        }


def _validated_common_statuses(row_statuses: object) -> tuple[str, ...]:
    if type(row_statuses) not in {list, tuple} or not row_statuses:
        raise R2QualificationError("COMMON_MASK_ROWS_INVALID")
    statuses = tuple(row_statuses)
    if any(type(status) is not str or status not in _COMMON_ROW_STATUSES for status in statuses):
        raise R2QualificationError("COMMON_MASK_ROW_STATUS_INVALID")
    return statuses


def assess_common_mask(row_statuses: list[str] | tuple[str, ...]) -> CommonMaskAssessmentV1:
    """Measure coverage without deleting missing sessions from the denominator."""

    statuses = _validated_common_statuses(row_statuses)
    total = len(statuses)
    evaluable = sum(status == QUALIFIED for status in statuses)
    missing = total - evaluable
    coverage_ppm = evaluable * PPM // total
    if coverage_ppm < MIN_COMMON_COVERAGE_PPM:
        return CommonMaskAssessmentV1(
            total_entry_rows=total,
            evaluable_entry_rows=evaluable,
            missing_entry_rows=missing,
            coverage_ppm=coverage_ppm,
            status=INSUFFICIENT_EVIDENCE,
            reason_code="COMMON_MASK_COVERAGE_INSUFFICIENT",
        )
    return CommonMaskAssessmentV1(
        total_entry_rows=total,
        evaluable_entry_rows=evaluable,
        missing_entry_rows=missing,
        coverage_ppm=coverage_ppm,
        status=QUALIFIED,
        reason_code="COMMON_MASK_QUALIFIED",
    )


def assert_joint_rows_evaluable(
    common_row_statuses: list[str] | tuple[str, ...],
    candidate_row_statuses: dict[str, list[str] | tuple[str, ...]],
) -> None:
    """Reject candidate-specific unknowns without silently shrinking joint rows."""

    common = _validated_common_statuses(common_row_statuses)
    if type(candidate_row_statuses) is not dict or not candidate_row_statuses:
        raise R2QualificationError("CANDIDATE_ROW_MATRIX_INVALID")
    for candidate_id, raw_rows in candidate_row_statuses.items():
        if (
            type(candidate_id) is not str
            or _CANDIDATE_ID_RE.fullmatch(candidate_id) is None
        ):
            raise R2QualificationError("CANDIDATE_ROW_MATRIX_INVALID")
        if type(raw_rows) not in {list, tuple} or len(raw_rows) != len(common):
            raise R2QualificationError(
                "CANDIDATE_ROW_COUNT_MISMATCH", candidate_id
            )
        for row_index, (common_status, candidate_status) in enumerate(
            zip(common, raw_rows, strict=True)
        ):
            if common_status == COMMON_DATA_MISSING:
                if candidate_status != COMMON_DATA_MISSING:
                    raise R2QualificationError(
                        "COMMON_MASK_CANDIDATE_ROW_MISMATCH",
                        f"{candidate_id}:{row_index}",
                    )
                continue
            if candidate_status == "UNEVALUABLE":
                raise R2QualificationError(
                    "CANDIDATE_SPECIFIC_UNEVALUABLE",
                    f"{candidate_id}:{row_index}",
                )
            if candidate_status not in _EVALUABLE_CANDIDATE_ROW_STATUSES:
                raise R2QualificationError(
                    "CANDIDATE_ROW_STATUS_INVALID",
                    f"{candidate_id}:{row_index}",
                )


__all__ = [
    "COMMON_DATA_MISSING",
    "CommonMaskAssessmentV1",
    "EntryLabelSegmentV1",
    "HalfOpenSessionWindowV1",
    "INSUFFICIENT_EVIDENCE",
    "LABEL_SESSIONS",
    "MIN_COMMON_COVERAGE_PPM",
    "QUALIFIED",
    "R2EntryLabelWindowsV1",
    "WALK_FORWARD_FOLDS",
    "assess_common_mask",
    "assert_joint_rows_evaluable",
    "build_r2_entry_label_windows",
]
