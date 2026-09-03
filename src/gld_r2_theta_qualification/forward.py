"""Synthetic-only Exact-100 Forward collection and diagnostic receipts.

This is a local, immutable state machine.  It neither obtains real data nor
authorizes promotion.  The 100th terminal episode seals the generation; an
unknown episode or sub-95% session coverage seals it as insufficient instead
of silently extending the sample.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from hashlib import sha256
import re

from gld_entry_decision_f0.evidence import round_half_even_divide
from gld_management_research.errors import ManagementResearchError
from gld_management_research.xnys_calendar import (
    SUPPORTED_END_DATE,
    SUPPORTED_START_DATE,
    action_1045_utc_ns,
    next_session_date,
)

from .contracts import (
    R2QualificationError,
    canonical_json_bytes,
    canonical_sha256,
)
from .statistics import (
    PRODUCTION_BLOCK_LENGTH,
    PRODUCTION_BOOTSTRAP_REPLICATES,
    PRODUCTION_LOWER_BOUND_INDEX,
    evaluate_single_policy_bootstrap,
)


PPM = 1_000_000
SYNTHETIC_EXACT_FORWARD_EPISODES = 100
SYNTHETIC_MINIMUM_COVERAGE_PPM = 950_000
SYNTHETIC_FORWARD_SEED_LABEL = "SYNTHETIC_FORWARD_BLOCK20_V1"

_MAX_RETURN_ABS_PPM = 100 * PPM
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_REASON_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_DAY_NS = 86_400_000_000_000
_EPOCH_DATE = date(1970, 1, 1)


class _SyntheticDiagnosticReceiptSeal:
    """Bind canonical diagnostic bytes to the exact factory-built instance."""

    __slots__ = ("_canonical_bytes", "_owner", "_receipt_sha256")

    def __init__(self) -> None:
        object.__setattr__(self, "_canonical_bytes", None)
        object.__setattr__(self, "_owner", None)
        object.__setattr__(self, "_receipt_sha256", None)

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("synthetic diagnostic receipt seals are immutable")

    def bind(
        self,
        owner: object,
        *,
        canonical_bytes: bytes,
        receipt_sha256: str,
    ) -> None:
        if self._owner is not None:
            raise RuntimeError("synthetic diagnostic receipt seal is already bound")
        object.__setattr__(self, "_owner", owner)
        object.__setattr__(self, "_canonical_bytes", canonical_bytes)
        object.__setattr__(self, "_receipt_sha256", receipt_sha256)

    def matches(
        self,
        owner: object,
        *,
        canonical_bytes: bytes,
        receipt_sha256: str,
    ) -> bool:
        return (
            self._owner is owner
            and self._canonical_bytes == canonical_bytes
            and self._receipt_sha256 == receipt_sha256
        )


def _first_session_after_t0(t0_utc_ns: int) -> date:
    approximate = _EPOCH_DATE + timedelta(days=t0_utc_ns // _DAY_NS)
    candidate = max(approximate, SUPPORTED_START_DATE)
    while candidate <= SUPPORTED_END_DATE:
        try:
            if action_1045_utc_ns(candidate) > t0_utc_ns:
                return candidate
        except ManagementResearchError:
            pass
        candidate += timedelta(days=1)
    raise R2QualificationError("FORWARD_T0_OUTSIDE_PINNED_CALENDAR")


def _hash(value: object, reason: str = "R2_HASH_INVALID") -> str:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        raise R2QualificationError(reason)
    return value


def _identifier(value: object, reason: str = "R2_IDENTIFIER_INVALID") -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise R2QualificationError(reason)
    return value


def _integer(
    value: object,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    reason: str = "R2_INTEGER_INVALID",
) -> int:
    if type(value) is not int:
        raise R2QualificationError(reason)
    if minimum is not None and value < minimum:
        raise R2QualificationError(reason)
    if maximum is not None and value > maximum:
        raise R2QualificationError(reason)
    return value


@dataclass(frozen=True, slots=True)
class SyntheticForwardSessionRow:
    session_id: str
    session_date: str
    session_ordinal: int
    observation_utc_ns: int
    input_sha256: str
    qualified: bool
    synthetic_return_ppm: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "session_date": self.session_date,
            "session_ordinal": self.session_ordinal,
            "observation_utc_ns": self.observation_utc_ns,
            "input_sha256": self.input_sha256,
            "qualified": self.qualified,
            "synthetic_return_ppm": self.synthetic_return_ppm,
        }


@dataclass(frozen=True, slots=True)
class SyntheticForwardTerminalEpisode:
    episode_id: str
    entry_session_id: str
    exit_session_id: str
    exit_utc_ns: int
    status: str
    synthetic_return_ppm: int | None
    input_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "episode_id": self.episode_id,
            "entry_session_id": self.entry_session_id,
            "exit_session_id": self.exit_session_id,
            "exit_utc_ns": self.exit_utc_ns,
            "status": self.status,
            "synthetic_return_ppm": self.synthetic_return_ppm,
            "input_sha256": self.input_sha256,
        }


@dataclass(frozen=True, slots=True)
class SyntheticExact100ForwardState:
    schema_version: str
    classification: str
    synthetic_theta_sha256: str
    t0_utc_ns: int
    collection_status: str
    sessions: tuple[SyntheticForwardSessionRow, ...]
    terminal_episodes: tuple[SyntheticForwardTerminalEpisode, ...]
    seal_reason_code: str | None
    sealed_manifest_sha256: str | None
    sealed_manifest_bytes: bytes | None

    @property
    def terminal_episode_count(self) -> int:
        return len(self.terminal_episodes)

    @property
    def complete_episode_count(self) -> int:
        return sum(episode.status == "COMPLETE" for episode in self.terminal_episodes)

    @property
    def unknown_episode_count(self) -> int:
        return sum(episode.status == "UNKNOWN" for episode in self.terminal_episodes)

    @property
    def qualified_session_count(self) -> int:
        return sum(row.qualified for row in self.sessions)

    @property
    def coverage_ppm(self) -> int:
        if not self.sessions:
            return 0
        return round_half_even_divide(
            self.qualified_session_count * PPM,
            len(self.sessions),
        )

    @property
    def sealed(self) -> bool:
        return self.collection_status in {
            "SEALED_SYNTHETIC_READY_FOR_DIAGNOSTIC",
            "SEALED_SYNTHETIC_INSUFFICIENT_EVIDENCE",
        }


def start_synthetic_exact_100_forward(
    *,
    synthetic_theta_sha256: str,
    t0_utc_ns: int,
) -> SyntheticExact100ForwardState:
    """Start a synthetic/local state with no outcome or network side effect."""

    return SyntheticExact100ForwardState(
        schema_version="GLD_R2_SYNTHETIC_FORWARD_EXACT_100_STATE_V1",
        classification="SYNTHETIC_ONLY",
        synthetic_theta_sha256=_hash(
            synthetic_theta_sha256,
            "SYNTHETIC_THETA_HASH_INVALID",
        ),
        t0_utc_ns=_integer(t0_utc_ns, minimum=1, reason="FORWARD_T0_INVALID"),
        collection_status="PENDING_SYNTHETIC_FORWARD_SEAL",
        sessions=(),
        terminal_episodes=(),
        seal_reason_code=None,
        sealed_manifest_sha256=None,
        sealed_manifest_bytes=None,
    )


def _require_collecting(state: SyntheticExact100ForwardState) -> None:
    if type(state) is not SyntheticExact100ForwardState:
        raise R2QualificationError("FORWARD_STATE_INVALID")
    _validate_state_semantics(state)
    if state.sealed:
        raise R2QualificationError("FORWARD_ALREADY_SEALED")
    if state.collection_status != "PENDING_SYNTHETIC_FORWARD_SEAL":
        raise R2QualificationError("FORWARD_STATE_INVALID")


def record_synthetic_forward_session(
    state: SyntheticExact100ForwardState,
    *,
    session_id: str,
    session_date: str,
    session_ordinal: int,
    observation_utc_ns: int,
    input_sha256: str,
    qualified: bool,
) -> SyntheticExact100ForwardState:
    """Append one frozen common-session row with an initial zero return."""

    _require_collecting(state)
    normalized_id = _identifier(session_id, "FORWARD_SESSION_ID_INVALID")
    if type(session_date) is not str or not session_date.isascii():
        raise R2QualificationError("FORWARD_SESSION_DATE_INVALID")
    try:
        parsed_date = date.fromisoformat(session_date)
        action_ns = action_1045_utc_ns(parsed_date)
    except (ValueError, ManagementResearchError) as exc:
        raise R2QualificationError("FORWARD_SESSION_DATE_INVALID") from exc
    if parsed_date.isoformat() != session_date:
        raise R2QualificationError("FORWARD_SESSION_DATE_INVALID")
    if normalized_id != f"XNYS-{session_date}":
        raise R2QualificationError("FORWARD_SESSION_ID_DATE_MISMATCH")
    ordinal = _integer(
        session_ordinal,
        minimum=1,
        reason="FORWARD_SESSION_ORDINAL_INVALID",
    )
    observation_ns = _integer(
        observation_utc_ns,
        minimum=state.t0_utc_ns + 1,
        reason="FORWARD_SESSION_TIME_INVALID",
    )
    if not action_ns <= observation_ns < action_ns + 60_000_000_000:
        raise R2QualificationError("FORWARD_SESSION_CLOCK_INVALID")
    if type(qualified) is not bool:
        raise R2QualificationError("FORWARD_SESSION_QUALIFICATION_INVALID")
    if normalized_id in {row.session_id for row in state.sessions}:
        raise R2QualificationError("FORWARD_SESSION_DUPLICATE")
    if state.sessions:
        previous = state.sessions[-1]
        if ordinal != previous.session_ordinal + 1:
            raise R2QualificationError("FORWARD_SESSION_ORDINAL_NOT_CONTIGUOUS")
        try:
            expected_date = next_session_date(previous.session_date).isoformat()
        except ManagementResearchError as exc:
            raise R2QualificationError("FORWARD_SESSION_DATE_INVALID") from exc
        if session_date != expected_date:
            raise R2QualificationError("FORWARD_SESSION_DATE_NOT_CONTIGUOUS")
        if observation_ns <= previous.observation_utc_ns:
            raise R2QualificationError("FORWARD_SESSION_TIME_NOT_INCREASING")
    else:
        if ordinal != 1:
            raise R2QualificationError("FORWARD_SESSION_ORDINAL_NOT_CONTIGUOUS")
        if parsed_date != _first_session_after_t0(state.t0_utc_ns):
            raise R2QualificationError("FORWARD_FIRST_SESSION_AFTER_T0_INVALID")
    return replace(
        state,
        sessions=state.sessions
        + (
            SyntheticForwardSessionRow(
                session_id=normalized_id,
                session_date=session_date,
                session_ordinal=ordinal,
                observation_utc_ns=observation_ns,
                input_sha256=_hash(
                    input_sha256,
                    "FORWARD_SESSION_INPUT_HASH_INVALID",
                ),
                qualified=qualified,
            ),
        ),
    )


def _manifest_document(
    state: SyntheticExact100ForwardState,
    *,
    collection_status: str,
    reason_code: str,
) -> dict[str, object]:
    return {
        "schema_version": "GLD_R2_SYNTHETIC_FORWARD_SEALED_MANIFEST_V1",
        "classification": state.classification,
        "synthetic_theta_sha256": state.synthetic_theta_sha256,
        "t0_utc_ns": state.t0_utc_ns,
        "collection_status": collection_status,
        "seal_reason_code": reason_code,
        "exact_terminal_episode_target": SYNTHETIC_EXACT_FORWARD_EPISODES,
        "terminal_episode_count": len(state.terminal_episodes),
        "complete_episode_count": state.complete_episode_count,
        "unknown_episode_count": state.unknown_episode_count,
        "session_count": len(state.sessions),
        "qualified_session_count": state.qualified_session_count,
        "coverage_ppm": state.coverage_ppm,
        "session_rows": [row.as_dict() for row in state.sessions],
        "terminal_episodes": [
            episode.as_dict() for episode in state.terminal_episodes
        ],
    }


def _seal_if_exact_100(state: SyntheticExact100ForwardState) -> SyntheticExact100ForwardState:
    if len(state.terminal_episodes) < SYNTHETIC_EXACT_FORWARD_EPISODES:
        return state
    if len(state.terminal_episodes) != SYNTHETIC_EXACT_FORWARD_EPISODES:
        raise R2QualificationError("FORWARD_EPISODE_TARGET_EXCEEDED")

    coverage_sufficient = (
        state.qualified_session_count * PPM
        >= SYNTHETIC_MINIMUM_COVERAGE_PPM * len(state.sessions)
    )
    if state.unknown_episode_count:
        status = "SEALED_SYNTHETIC_INSUFFICIENT_EVIDENCE"
        reason = "SYNTHETIC_FORWARD_UNKNOWN_EPISODE"
    elif not coverage_sufficient:
        status = "SEALED_SYNTHETIC_INSUFFICIENT_EVIDENCE"
        reason = "SYNTHETIC_FORWARD_COVERAGE_BELOW_95PCT"
    else:
        status = "SEALED_SYNTHETIC_READY_FOR_DIAGNOSTIC"
        reason = "SYNTHETIC_FORWARD_EXACT_100_COMPLETE"

    proto = replace(
        state,
        collection_status=status,
        seal_reason_code=reason,
    )
    _validate_state_semantics(proto)
    document = _manifest_document(
        proto,
        collection_status=status,
        reason_code=reason,
    )
    manifest_bytes = canonical_json_bytes(document)
    return replace(
        proto,
        sealed_manifest_sha256=canonical_sha256(document),
        sealed_manifest_bytes=manifest_bytes,
    )


def record_synthetic_forward_terminal_episode(
    state: SyntheticExact100ForwardState,
    *,
    episode_id: str,
    entry_session_id: str,
    exit_session_id: str,
    exit_utc_ns: int,
    status: str,
    synthetic_return_ppm: int | None,
    input_sha256: str,
) -> SyntheticExact100ForwardState:
    """Record one terminal slot; slot 100 seals even when it is unknown."""

    _require_collecting(state)
    if len(state.terminal_episodes) >= SYNTHETIC_EXACT_FORWARD_EPISODES:
        raise R2QualificationError("FORWARD_ALREADY_SEALED")
    normalized_episode_id = _identifier(
        episode_id,
        "FORWARD_EPISODE_ID_INVALID",
    )
    normalized_entry_id = _identifier(
        entry_session_id,
        "FORWARD_SESSION_ID_INVALID",
    )
    normalized_exit_id = _identifier(
        exit_session_id,
        "FORWARD_SESSION_ID_INVALID",
    )
    session_ids = {row.session_id for row in state.sessions}
    if normalized_entry_id not in session_ids or normalized_exit_id not in session_ids:
        raise R2QualificationError("FORWARD_EPISODE_SESSION_NOT_RECORDED")
    if normalized_episode_id in {
        episode.episode_id for episode in state.terminal_episodes
    }:
        raise R2QualificationError("FORWARD_EPISODE_DUPLICATE")
    if normalized_entry_id in {
        episode.entry_session_id for episode in state.terminal_episodes
    }:
        raise R2QualificationError("FORWARD_ENTRY_SESSION_ALREADY_ATTRIBUTED")
    normalized_exit_ns = _integer(
        exit_utc_ns,
        minimum=state.t0_utc_ns + 1,
        reason="FORWARD_EXIT_TIME_INVALID",
    )
    if state.terminal_episodes and (
        normalized_exit_ns <= state.terminal_episodes[-1].exit_utc_ns
    ):
        raise R2QualificationError("FORWARD_EXIT_TIME_NOT_INCREASING")
    if status not in {"COMPLETE", "UNKNOWN"}:
        raise R2QualificationError("FORWARD_EPISODE_STATUS_INVALID")
    if status == "COMPLETE":
        rows_by_id = {row.session_id: row for row in state.sessions}
        if not (
            rows_by_id[normalized_entry_id].qualified
            and rows_by_id[normalized_exit_id].qualified
        ):
            raise R2QualificationError(
                "FORWARD_COMPLETE_REQUIRES_QUALIFIED_SESSIONS"
            )
        normalized_return = _integer(
            synthetic_return_ppm,
            minimum=-_MAX_RETURN_ABS_PPM,
            maximum=_MAX_RETURN_ABS_PPM,
            reason="FORWARD_RETURN_PPM_INVALID",
        )
    else:
        if synthetic_return_ppm is not None:
            raise R2QualificationError("FORWARD_UNKNOWN_RETURN_FORBIDDEN")
        normalized_return = None
    rows_by_id = {row.session_id: row for row in state.sessions}
    entry_row = rows_by_id[normalized_entry_id]
    exit_row = rows_by_id[normalized_exit_id]
    if entry_row.session_ordinal > exit_row.session_ordinal:
        raise R2QualificationError("FORWARD_EPISODE_SESSION_ORDER_INVALID")
    if exit_row.session_ordinal - entry_row.session_ordinal > 20:
        raise R2QualificationError("FORWARD_EPISODE_EXCEEDS_H20")
    if state.terminal_episodes:
        previous_exit_row = rows_by_id[state.terminal_episodes[-1].exit_session_id]
        if entry_row.session_ordinal <= previous_exit_row.session_ordinal:
            raise R2QualificationError("FORWARD_EPISODE_OVERLAP_INVALID")
    if normalized_exit_ns != exit_row.observation_utc_ns:
        raise R2QualificationError("FORWARD_EXIT_TIME_SESSION_MISMATCH")
    episode = SyntheticForwardTerminalEpisode(
        episode_id=normalized_episode_id,
        entry_session_id=normalized_entry_id,
        exit_session_id=normalized_exit_id,
        exit_utc_ns=normalized_exit_ns,
        status=status,
        synthetic_return_ppm=normalized_return,
        input_sha256=_hash(input_sha256, "FORWARD_EPISODE_INPUT_HASH_INVALID"),
    )

    sessions = state.sessions
    if normalized_return is not None:
        sessions = tuple(
            replace(row, synthetic_return_ppm=normalized_return)
            if row.session_id == normalized_entry_id
            else row
            for row in sessions
        )
    next_state = replace(
        state,
        sessions=sessions,
        terminal_episodes=state.terminal_episodes + (episode,),
    )
    return _seal_if_exact_100(next_state)


def _verify_sealed_state(state: SyntheticExact100ForwardState) -> None:
    if type(state) is not SyntheticExact100ForwardState or not state.sealed:
        raise R2QualificationError("FORWARD_NOT_SEALED")
    _validate_state_semantics(state)
    if (
        state.seal_reason_code is None
        or state.sealed_manifest_sha256 is None
        or state.sealed_manifest_bytes is None
    ):
        raise R2QualificationError("FORWARD_SEAL_INTEGRITY_MISMATCH")
    document = _manifest_document(
        state,
        collection_status=state.collection_status,
        reason_code=state.seal_reason_code,
    )
    if (
        canonical_json_bytes(document) != state.sealed_manifest_bytes
        or canonical_sha256(document) != state.sealed_manifest_sha256
    ):
        raise R2QualificationError("FORWARD_SEAL_INTEGRITY_MISMATCH")


def _validate_state_semantics(state: SyntheticExact100ForwardState) -> None:
    """Validate state-machine meaning independently of its checksum."""

    if (
        type(state) is not SyntheticExact100ForwardState
        or state.schema_version != "GLD_R2_SYNTHETIC_FORWARD_EXACT_100_STATE_V1"
        or state.classification != "SYNTHETIC_ONLY"
    ):
        raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
    _hash(state.synthetic_theta_sha256, "SYNTHETIC_THETA_HASH_INVALID")
    _integer(state.t0_utc_ns, minimum=1, reason="FORWARD_T0_INVALID")
    if len({row.session_id for row in state.sessions}) != len(state.sessions):
        raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
    previous_date: str | None = None
    previous_time: int | None = None
    for ordinal, row in enumerate(state.sessions, start=1):
        if type(row) is not SyntheticForwardSessionRow or row.session_ordinal != ordinal:
            raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
        if row.session_id != f"XNYS-{row.session_date}":
            raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
        try:
            parsed_date = date.fromisoformat(row.session_date)
            action_ns = action_1045_utc_ns(parsed_date)
        except (ValueError, ManagementResearchError) as exc:
            raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID") from exc
        if (
            parsed_date.isoformat() != row.session_date
            or type(row.qualified) is not bool
            or type(row.synthetic_return_ppm) is not int
            or not -_MAX_RETURN_ABS_PPM <= row.synthetic_return_ppm <= _MAX_RETURN_ABS_PPM
            or not action_ns
            <= row.observation_utc_ns
            < action_ns + 60_000_000_000
            or row.observation_utc_ns <= state.t0_utc_ns
        ):
            raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
        _hash(row.input_sha256, "FORWARD_SEAL_SEMANTICS_INVALID")
        if previous_date is not None:
            try:
                expected_date = next_session_date(previous_date).isoformat()
            except ManagementResearchError as exc:
                raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID") from exc
            if row.session_date != expected_date:
                raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
        if previous_time is not None and row.observation_utc_ns <= previous_time:
            raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
        previous_date = row.session_date
        previous_time = row.observation_utc_ns
    if state.sessions:
        try:
            expected_first = _first_session_after_t0(state.t0_utc_ns)
        except R2QualificationError as exc:
            raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID") from exc
        if state.sessions[0].session_date != expected_first.isoformat():
            raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")

    rows_by_id = {row.session_id: row for row in state.sessions}
    episode_ids: set[str] = set()
    entry_ids: set[str] = set()
    expected_returns = {row.session_id: 0 for row in state.sessions}
    previous_exit_ns: int | None = None
    previous_exit_ordinal: int | None = None
    for episode in state.terminal_episodes:
        if (
            type(episode) is not SyntheticForwardTerminalEpisode
            or episode.episode_id in episode_ids
            or episode.entry_session_id in entry_ids
            or episode.entry_session_id not in rows_by_id
            or episode.exit_session_id not in rows_by_id
            or episode.status not in {"COMPLETE", "UNKNOWN"}
        ):
            raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
        entry_row = rows_by_id[episode.entry_session_id]
        exit_row = rows_by_id[episode.exit_session_id]
        if (
            entry_row.session_ordinal > exit_row.session_ordinal
            or exit_row.session_ordinal - entry_row.session_ordinal > 20
            or (
                previous_exit_ordinal is not None
                and entry_row.session_ordinal <= previous_exit_ordinal
            )
            or episode.exit_utc_ns != exit_row.observation_utc_ns
            or (
                previous_exit_ns is not None
                and episode.exit_utc_ns <= previous_exit_ns
            )
        ):
            raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
        _hash(episode.input_sha256, "FORWARD_SEAL_SEMANTICS_INVALID")
        if episode.status == "COMPLETE":
            if (
                type(episode.synthetic_return_ppm) is not int
                or not -_MAX_RETURN_ABS_PPM
                <= episode.synthetic_return_ppm
                <= _MAX_RETURN_ABS_PPM
                or not entry_row.qualified
                or not exit_row.qualified
            ):
                raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
            expected_returns[episode.entry_session_id] = episode.synthetic_return_ppm
        elif episode.synthetic_return_ppm is not None:
            raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
        episode_ids.add(episode.episode_id)
        entry_ids.add(episode.entry_session_id)
        previous_exit_ns = episode.exit_utc_ns
        previous_exit_ordinal = exit_row.session_ordinal
    if any(
        row.synthetic_return_ppm != expected_returns[row.session_id] for row in state.sessions
    ):
        raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")

    if state.collection_status == "PENDING_SYNTHETIC_FORWARD_SEAL":
        if (
            len(state.terminal_episodes) >= SYNTHETIC_EXACT_FORWARD_EPISODES
            or state.seal_reason_code is not None
            or state.sealed_manifest_sha256 is not None
            or state.sealed_manifest_bytes is not None
        ):
            raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
        return
    if len(state.terminal_episodes) != SYNTHETIC_EXACT_FORWARD_EPISODES:
        raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
    if (
        not state.sessions
        or state.terminal_episodes[-1].exit_session_id
        != state.sessions[-1].session_id
    ):
        raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
    coverage_sufficient = (
        state.qualified_session_count * PPM
        >= SYNTHETIC_MINIMUM_COVERAGE_PPM * len(state.sessions)
    )
    if state.unknown_episode_count:
        expected_status = "SEALED_SYNTHETIC_INSUFFICIENT_EVIDENCE"
        expected_reason = "SYNTHETIC_FORWARD_UNKNOWN_EPISODE"
    elif not coverage_sufficient:
        expected_status = "SEALED_SYNTHETIC_INSUFFICIENT_EVIDENCE"
        expected_reason = "SYNTHETIC_FORWARD_COVERAGE_BELOW_95PCT"
    else:
        expected_status = "SEALED_SYNTHETIC_READY_FOR_DIAGNOSTIC"
        expected_reason = "SYNTHETIC_FORWARD_EXACT_100_COMPLETE"
    if (
        state.collection_status != expected_status
        or state.seal_reason_code != expected_reason
    ):
        raise R2QualificationError("FORWARD_SEAL_SEMANTICS_INVALID")
def derive_synthetic_forward_seed(
    *,
    sealed_manifest_sha256: str,
    synthetic_theta_sha256: str,
) -> str:
    """Bind one synthetic manifest, synthetic theta, and diagnostic method."""

    manifest_hash = _hash(
        sealed_manifest_sha256,
        "FORWARD_MANIFEST_HASH_INVALID",
    )
    theta_hash = _hash(
        synthetic_theta_sha256,
        "SYNTHETIC_THETA_HASH_INVALID",
    )
    return sha256(
        (manifest_hash + theta_hash + SYNTHETIC_FORWARD_SEED_LABEL).encode("ascii")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class SyntheticForwardDiagnosticReceipt:
    """Synthetic-only diagnostic; never a policy qualification receipt."""

    schema_version: str
    classification: str
    diagnostic_status: str
    reason_code: str
    synthetic_scenario_sha256: str
    synthetic_theta_sha256: str
    sealed_manifest_sha256: str
    synthetic_forward_seed_sha256: str
    synthetic_observed_mean_ppm: int | None
    synthetic_lower_bound_ppm: int | None
    bootstrap_replicates: int | None
    block_length: int | None
    lower_bound_index: int | None
    bootstrap_distribution_sha256: str | None
    diagnostic_receipt_sha256: str
    _canonical_bytes: bytes = field(repr=False, compare=False)
    _seal: _SyntheticDiagnosticReceiptSeal = field(repr=False, compare=False)

    def __copy__(self) -> SyntheticForwardDiagnosticReceipt:
        return self

    def __deepcopy__(
        self,
        memo: dict[int, object],
    ) -> SyntheticForwardDiagnosticReceipt:
        memo[id(self)] = self
        return self

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "synthetic Forward diagnostic receipts cannot be pickled"
        )

    def _unsigned_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "classification": self.classification,
            "diagnostic_status": self.diagnostic_status,
            "reason_code": self.reason_code,
            "synthetic_scenario_sha256": self.synthetic_scenario_sha256,
            "synthetic_theta_sha256": self.synthetic_theta_sha256,
            "sealed_manifest_sha256": self.sealed_manifest_sha256,
            "synthetic_forward_seed_sha256": self.synthetic_forward_seed_sha256,
            "synthetic_observed_mean_ppm": self.synthetic_observed_mean_ppm,
            "synthetic_lower_bound_ppm": self.synthetic_lower_bound_ppm,
            "bootstrap_replicates": self.bootstrap_replicates,
            "block_length": self.block_length,
            "lower_bound_index": self.lower_bound_index,
            "bootstrap_distribution_sha256": self.bootstrap_distribution_sha256,
        }

    def as_dict(self) -> dict[str, object]:
        unsigned = self._unsigned_dict()
        if (
            self.schema_version
            != "GLD_R2_SYNTHETIC_FORWARD_DIAGNOSTIC_RECEIPT_V1"
            or self.classification != "SYNTHETIC_ONLY"
            or type(self.diagnostic_status) is not str
            or type(self.reason_code) is not str
            or _REASON_RE.fullmatch(self.reason_code) is None
        ):
            raise R2QualificationError(
                "SYNTHETIC_DIAGNOSTIC_RECEIPT_SEMANTICS_INVALID"
            )
        for value in (
            self.synthetic_scenario_sha256,
            self.synthetic_theta_sha256,
            self.sealed_manifest_sha256,
            self.synthetic_forward_seed_sha256,
        ):
            _hash(value, "SYNTHETIC_DIAGNOSTIC_RECEIPT_SEMANTICS_INVALID")
        if self.synthetic_forward_seed_sha256 != derive_synthetic_forward_seed(
            sealed_manifest_sha256=self.sealed_manifest_sha256,
            synthetic_theta_sha256=self.synthetic_theta_sha256,
        ):
            raise R2QualificationError(
                "SYNTHETIC_DIAGNOSTIC_RECEIPT_SEMANTICS_INVALID"
            )
        statistic_fields = (
            self.synthetic_observed_mean_ppm,
            self.synthetic_lower_bound_ppm,
            self.bootstrap_replicates,
            self.block_length,
            self.lower_bound_index,
            self.bootstrap_distribution_sha256,
        )
        if self.diagnostic_status == "SYNTHETIC_INSUFFICIENT_EVIDENCE":
            if (
                any(value is not None for value in statistic_fields)
                or self.reason_code
                not in {
                    "SYNTHETIC_FORWARD_UNKNOWN_EPISODE",
                    "SYNTHETIC_FORWARD_COVERAGE_BELOW_95PCT",
                }
            ):
                raise R2QualificationError(
                    "SYNTHETIC_DIAGNOSTIC_RECEIPT_SEMANTICS_INVALID"
                )
        else:
            if (
                type(self.synthetic_observed_mean_ppm) is not int
                or type(self.synthetic_lower_bound_ppm) is not int
                or type(self.bootstrap_replicates) is not int
                or type(self.block_length) is not int
                or type(self.lower_bound_index) is not int
                or type(self.bootstrap_distribution_sha256) is not str
                or not -_MAX_RETURN_ABS_PPM
                <= self.synthetic_observed_mean_ppm
                <= _MAX_RETURN_ABS_PPM
                or not -_MAX_RETURN_ABS_PPM
                <= self.synthetic_lower_bound_ppm
                <= _MAX_RETURN_ABS_PPM
                or self.bootstrap_replicates < 1
                or self.block_length != PRODUCTION_BLOCK_LENGTH
                or not 0
                <= self.lower_bound_index
                < self.bootstrap_replicates
            ):
                raise R2QualificationError(
                    "SYNTHETIC_DIAGNOSTIC_RECEIPT_SEMANTICS_INVALID"
                )
            _hash(
                self.bootstrap_distribution_sha256,
                "SYNTHETIC_DIAGNOSTIC_RECEIPT_SEMANTICS_INVALID",
            )
            expected_status = "SYNTHETIC_DIAGNOSTIC_ONLY"
            expected_reason = (
                "SYNTHETIC_FORWARD_LCB_POSITIVE"
                if self.synthetic_lower_bound_ppm > 0
                else "SYNTHETIC_FORWARD_LCB_NONPOSITIVE"
            )
            if (
                self.diagnostic_status != expected_status
                or self.reason_code != expected_reason
            ):
                raise R2QualificationError(
                    "SYNTHETIC_DIAGNOSTIC_RECEIPT_SEMANTICS_INVALID"
                )
        if (
            type(self.diagnostic_receipt_sha256) is not str
            or _HASH_RE.fullmatch(self.diagnostic_receipt_sha256) is None
            or canonical_sha256(unsigned) != self.diagnostic_receipt_sha256
        ):
            raise R2QualificationError(
                "SYNTHETIC_DIAGNOSTIC_RECEIPT_INTEGRITY_MISMATCH"
            )
        document = {
            **unsigned,
            "diagnostic_receipt_sha256": self.diagnostic_receipt_sha256,
        }
        if (
            type(self._canonical_bytes) is not bytes
            or canonical_json_bytes(document) != self._canonical_bytes
            or type(self._seal) is not _SyntheticDiagnosticReceiptSeal
            or not self._seal.matches(
                self,
                canonical_bytes=self._canonical_bytes,
                receipt_sha256=self.diagnostic_receipt_sha256,
            )
        ):
            raise R2QualificationError(
                "SYNTHETIC_DIAGNOSTIC_RECEIPT_INTEGRITY_MISMATCH"
            )
        return document


def _synthetic_diagnostic_receipt(
    *,
    diagnostic_status: str,
    reason_code: str,
    synthetic_scenario_sha256: str,
    state: SyntheticExact100ForwardState,
    synthetic_forward_seed_sha256: str,
    synthetic_observed_mean_ppm: int | None,
    synthetic_lower_bound_ppm: int | None,
    bootstrap_replicates: int | None,
    block_length: int | None,
    lower_bound_index: int | None,
    bootstrap_distribution_sha256: str | None,
) -> SyntheticForwardDiagnosticReceipt:
    unsigned = {
        "schema_version": "GLD_R2_SYNTHETIC_FORWARD_DIAGNOSTIC_RECEIPT_V1",
        "classification": state.classification,
        "diagnostic_status": diagnostic_status,
        "reason_code": reason_code,
        "synthetic_scenario_sha256": synthetic_scenario_sha256,
        "synthetic_theta_sha256": state.synthetic_theta_sha256,
        "sealed_manifest_sha256": state.sealed_manifest_sha256,
        "synthetic_forward_seed_sha256": synthetic_forward_seed_sha256,
        "synthetic_observed_mean_ppm": synthetic_observed_mean_ppm,
        "synthetic_lower_bound_ppm": synthetic_lower_bound_ppm,
        "bootstrap_replicates": bootstrap_replicates,
        "block_length": block_length,
        "lower_bound_index": lower_bound_index,
        "bootstrap_distribution_sha256": bootstrap_distribution_sha256,
    }
    assert type(state.sealed_manifest_sha256) is str
    diagnostic_hash = canonical_sha256(unsigned)
    canonical_bytes = canonical_json_bytes(
        {**unsigned, "diagnostic_receipt_sha256": diagnostic_hash}
    )
    seal = _SyntheticDiagnosticReceiptSeal()
    receipt = SyntheticForwardDiagnosticReceipt(
        **unsigned,
        diagnostic_receipt_sha256=diagnostic_hash,
        _canonical_bytes=canonical_bytes,
        _seal=seal,
    )
    seal.bind(
        receipt,
        canonical_bytes=canonical_bytes,
        receipt_sha256=diagnostic_hash,
    )
    return receipt


def build_synthetic_forward_diagnostic_receipt(
    state: SyntheticExact100ForwardState,
    *,
    synthetic_scenario_sha256: str,
    test_replicates: int | None = None,
    test_lower_bound_index: int | None = None,
) -> SyntheticForwardDiagnosticReceipt:
    """Evaluate a sealed synthetic manifest without producing policy evidence."""

    _verify_sealed_state(state)
    scenario_hash = _hash(
        synthetic_scenario_sha256,
        "SYNTHETIC_SCENARIO_HASH_INVALID",
    )
    assert state.sealed_manifest_sha256 is not None
    forward_seed = derive_synthetic_forward_seed(
        sealed_manifest_sha256=state.sealed_manifest_sha256,
        synthetic_theta_sha256=state.synthetic_theta_sha256,
    )
    if state.collection_status == "SEALED_SYNTHETIC_INSUFFICIENT_EVIDENCE":
        return _synthetic_diagnostic_receipt(
            diagnostic_status="SYNTHETIC_INSUFFICIENT_EVIDENCE",
            reason_code=(
                "SYNTHETIC_FORWARD_UNKNOWN_EPISODE"
                if state.unknown_episode_count
                else "SYNTHETIC_FORWARD_COVERAGE_BELOW_95PCT"
            ),
            synthetic_scenario_sha256=scenario_hash,
            state=state,
            synthetic_forward_seed_sha256=forward_seed,
            synthetic_observed_mean_ppm=None,
            synthetic_lower_bound_ppm=None,
            bootstrap_replicates=None,
            block_length=None,
            lower_bound_index=None,
            bootstrap_distribution_sha256=None,
        )

    replicate_count = (
        PRODUCTION_BOOTSTRAP_REPLICATES
        if test_replicates is None
        else test_replicates
    )
    rank_index = (
        PRODUCTION_LOWER_BOUND_INDEX
        if test_lower_bound_index is None
        else test_lower_bound_index
    )
    result = evaluate_single_policy_bootstrap(
        tuple(row.synthetic_return_ppm for row in state.sessions if row.qualified),
        seed_sha256=forward_seed,
        replicates=replicate_count,
        block_length=PRODUCTION_BLOCK_LENGTH,
        lower_bound_index=rank_index,
    )
    reason = (
        "SYNTHETIC_FORWARD_LCB_POSITIVE"
        if result.lower_bound_ppm > 0
        else "SYNTHETIC_FORWARD_LCB_NONPOSITIVE"
    )
    return _synthetic_diagnostic_receipt(
        diagnostic_status="SYNTHETIC_DIAGNOSTIC_ONLY",
        reason_code=reason,
        synthetic_scenario_sha256=scenario_hash,
        state=state,
        synthetic_forward_seed_sha256=forward_seed,
        synthetic_observed_mean_ppm=result.observed_mean_ppm,
        synthetic_lower_bound_ppm=result.lower_bound_ppm,
        bootstrap_replicates=result.replicates,
        block_length=result.block_length,
        lower_bound_index=result.lower_bound_index,
        bootstrap_distribution_sha256=canonical_sha256(
            {
                "schema_version": "GLD_R2_SYNTHETIC_FORWARD_BOOTSTRAP_DISTRIBUTION_V1",
                "means_ppm": list(result.bootstrap_means_ppm),
            }
        ),
    )


__all__ = [
    "SYNTHETIC_EXACT_FORWARD_EPISODES",
    "SyntheticExact100ForwardState",
    "SYNTHETIC_FORWARD_SEED_LABEL",
    "SyntheticForwardDiagnosticReceipt",
    "SyntheticForwardSessionRow",
    "SyntheticForwardTerminalEpisode",
    "SYNTHETIC_MINIMUM_COVERAGE_PPM",
    "build_synthetic_forward_diagnostic_receipt",
    "derive_synthetic_forward_seed",
    "record_synthetic_forward_session",
    "record_synthetic_forward_terminal_episode",
    "start_synthetic_exact_100_forward",
]
