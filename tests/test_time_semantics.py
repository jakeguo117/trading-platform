from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import gld_normalizer.time_semantics as time_semantics
from gld_normalizer.errors import NormalizationError
from gld_normalizer.time_semantics import (
    CalendarPayload,
    CalendarQualificationReceipt,
    CalendarSession,
    CalendarTerminalState,
    MAX_CALENDAR_SESSIONS,
    MAX_CONTROL_TEXT_CHARS,
    MAX_RECEIPT_PROOF_BYTES,
    MarketPhase,
    canonical_calendar_payload_sha256,
    canonical_calendar_sessions_sha256,
    classify_opening_transition,
    fast_confirmation_eligible,
    map_session_horizon,
    qualify_calendar,
    qualify_market_phase,
    validate_calendar_receipt_hash_binding,
)


NEW_YORK = ZoneInfo("America/New_York")
NANOSECONDS_PER_MINUTE = 60_000_000_000
SOURCE_SHA256 = "a" * 64


def utc_nanoseconds(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = value.astimezone(timezone.utc) - epoch
    seconds = delta.days * 86_400 + delta.seconds
    return (seconds * 1_000_000 + delta.microseconds) * 1_000


class OpeningTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.regular_open = datetime(2026, 8, 28, 9, 30, tzinfo=NEW_YORK)
        self.regular_open_utc_ns = utc_nanoseconds(self.regular_open)

    def test_exact_edt_nanosecond_boundaries(self) -> None:
        cases = (
            (self.regular_open_utc_ns - 1, None),
            (
                self.regular_open_utc_ns,
                MarketPhase.OPENING_TRANSITION_UNCLASSIFIED,
            ),
            (
                self.regular_open_utc_ns + NANOSECONDS_PER_MINUTE - 1,
                MarketPhase.OPENING_TRANSITION_UNCLASSIFIED,
            ),
            (self.regular_open_utc_ns + NANOSECONDS_PER_MINUTE, None),
        )
        for event_utc_ns, expected in cases:
            with self.subTest(event_utc_ns=event_utc_ns):
                self.assertEqual(
                    classify_opening_transition(
                        event_utc_ns,
                        self.regular_open,
                    ),
                    expected,
                )

    def test_exact_est_nanosecond_boundaries(self) -> None:
        regular_open = datetime(2026, 1, 5, 9, 30, tzinfo=NEW_YORK)
        open_utc_ns = utc_nanoseconds(regular_open)
        self.assertEqual(regular_open.astimezone(timezone.utc).hour, 14)

        self.assertIsNone(classify_opening_transition(open_utc_ns - 1, regular_open))
        self.assertEqual(
            classify_opening_transition(open_utc_ns, regular_open),
            MarketPhase.OPENING_TRANSITION_UNCLASSIFIED,
        )
        self.assertEqual(
            classify_opening_transition(
                open_utc_ns + NANOSECONDS_PER_MINUTE - 1,
                regular_open,
            ),
            MarketPhase.OPENING_TRANSITION_UNCLASSIFIED,
        )
        self.assertIsNone(
            classify_opening_transition(
                open_utc_ns + NANOSECONDS_PER_MINUTE,
                regular_open,
            )
        )

    def test_event_nanoseconds_require_exact_int_and_non_sentinel_uint64(self) -> None:
        invalid_values = (-1, 0, True, 1.5, 2**64 - 1, 2**64)
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(NormalizationError) as raised:
                    classify_opening_transition(value, self.regular_open)  # type: ignore[arg-type]
                self.assertEqual(
                    raised.exception.reason_code,
                    "INVALID_UTC_NANOSECONDS",
                )

    def test_nanosecond_range_endpoints_are_accepted(self) -> None:
        self.assertIsNone(classify_opening_transition(1, self.regular_open))
        self.assertIsNone(
            classify_opening_transition(2**64 - 2, self.regular_open)
        )

    def test_regular_open_runtime_and_derived_range_fail_closed(self) -> None:
        with self.assertRaises(NormalizationError) as wrong_type:
            classify_opening_transition(self.regular_open_utc_ns, "09:30")  # type: ignore[arg-type]
        self.assertEqual(
            wrong_type.exception.reason_code,
            "INVALID_REGULAR_OPEN_TYPE",
        )

        with self.assertRaises(NormalizationError) as naive:
            classify_opening_transition(
                self.regular_open_utc_ns,
                datetime(2026, 8, 28, 9, 30),
            )
        self.assertEqual(naive.exception.reason_code, "TIMEZONE_NAIVE")

        pre_epoch_open = datetime(1969, 1, 2, 9, 30, tzinfo=NEW_YORK)
        with self.assertRaises(NormalizationError) as out_of_range:
            classify_opening_transition(1, pre_epoch_open)
        self.assertEqual(
            out_of_range.exception.reason_code,
            "INVALID_UTC_NANOSECONDS",
        )


class CalendarHashBindingTests(unittest.TestCase):
    SESSION_DATES = (
        date(2026, 11, 20),
        date(2026, 11, 23),
        date(2026, 11, 24),
        date(2026, 11, 25),
        date(2026, 11, 27),  # Half-day; Thanksgiving 11/26 is omitted.
        date(2026, 11, 30),
    )

    @classmethod
    def payload(cls) -> CalendarPayload:
        sessions = tuple(
            CalendarSession(
                session_date=session_date,
                is_half_day=session_date == date(2026, 11, 27),
            )
            for session_date in cls.SESSION_DATES
        )
        return CalendarPayload(
            source_sha256=SOURCE_SHA256,
            version="xnys-structure-v1",
            sessions=sessions,
            sessions_sha256=canonical_calendar_sessions_sha256(sessions),
        )

    @classmethod
    def receipt(
        cls,
        payload: CalendarPayload,
        *,
        issued_at_utc_ns: int = 1,
    ) -> CalendarQualificationReceipt:
        return CalendarQualificationReceipt(
            authority_id="UNVERIFIED_RECEIPT_SOURCE",
            verifier_version="structure-only-v1",
            payload_sha256=canonical_calendar_payload_sha256(payload),
            source_sha256=payload.source_sha256,
            sessions_sha256=payload.sessions_sha256,
            calendar_version=payload.version,
            terminal_state=CalendarTerminalState.QUALIFIED,
            issued_at_utc_ns=issued_at_utc_ns,
            proof=b"unverified-proof-bytes",
        )

    def test_hash_binding_is_deterministic_but_never_authority_qualified(self) -> None:
        payload = self.payload()
        receipt = self.receipt(payload)

        validation = validate_calendar_receipt_hash_binding(payload, receipt)

        self.assertEqual(validation.sessions_sha256, payload.sessions_sha256)
        self.assertEqual(
            validation.payload_sha256,
            receipt.payload_sha256,
        )
        self.assertFalse(validation.authority_qualified)
        self.assertNotIn(date(2026, 11, 26), self.SESSION_DATES)
        self.assertTrue(payload.sessions[4].is_half_day)

    def test_hash_or_receipt_binding_mismatch_fails_closed(self) -> None:
        payload = self.payload()
        wrong_payload = replace(payload, sessions_sha256="b" * 64)
        with self.assertRaises(NormalizationError) as sessions_mismatch:
            validate_calendar_receipt_hash_binding(
                wrong_payload,
                self.receipt(wrong_payload),
            )
        self.assertEqual(
            sessions_mismatch.exception.reason_code,
            "CALENDAR_SESSIONS_HASH_MISMATCH",
        )

        receipt = replace(self.receipt(payload), source_sha256="c" * 64)
        with self.assertRaises(NormalizationError) as receipt_mismatch:
            validate_calendar_receipt_hash_binding(payload, receipt)
        self.assertEqual(
            receipt_mismatch.exception.reason_code,
            "CALENDAR_RECEIPT_BINDING_MISMATCH",
        )

    def test_receipt_timestamp_uses_the_same_nanosecond_gate(self) -> None:
        payload = self.payload()
        invalid_values = (-1, 0, True, 1.5, 2**64 - 1, 2**64)
        for value in invalid_values:
            with self.subTest(value=value):
                receipt = self.receipt(payload, issued_at_utc_ns=value)  # type: ignore[arg-type]
                with self.assertRaises(NormalizationError) as raised:
                    validate_calendar_receipt_hash_binding(payload, receipt)
                self.assertEqual(
                    raised.exception.reason_code,
                    "INVALID_UTC_NANOSECONDS",
                )

    def test_duplicate_unsorted_and_mutable_sessions_are_rejected(self) -> None:
        valid = self.payload().sessions
        with self.assertRaises(NormalizationError) as duplicate:
            canonical_calendar_sessions_sha256(valid + (valid[0],))
        self.assertEqual(
            duplicate.exception.reason_code,
            "DUPLICATE_SESSION_DATE",
        )

        with self.assertRaises(NormalizationError) as unsorted:
            canonical_calendar_sessions_sha256((valid[1], valid[0]))
        self.assertEqual(
            unsorted.exception.reason_code,
            "UNSORTED_SESSION_DATES",
        )

        with self.assertRaises(NormalizationError) as mutable:
            canonical_calendar_sessions_sha256(list(valid))
        self.assertEqual(
            mutable.exception.reason_code,
            "INVALID_CALENDAR_SESSIONS",
        )

    def test_structural_calendar_inputs_have_hard_resource_caps(self) -> None:
        sessions = tuple(
            CalendarSession(
                session_date=date(2000, 1, 1),
                is_half_day=False,
            )
            for _ in range(MAX_CALENDAR_SESSIONS + 1)
        )
        with self.assertRaises(NormalizationError) as session_cap:
            canonical_calendar_sessions_sha256(sessions)
        self.assertEqual(
            session_cap.exception.reason_code,
            "CALENDAR_SESSION_CAP_EXCEEDED",
        )

        payload = self.payload()
        oversized_version = replace(
            payload,
            version="v" * (MAX_CONTROL_TEXT_CHARS + 1),
        )
        with self.assertRaises(NormalizationError) as text_cap:
            canonical_calendar_payload_sha256(oversized_version)
        self.assertEqual(
            text_cap.exception.reason_code,
            "MISSING_CALENDAR_VERSION",
        )

        oversized_proof = replace(
            self.receipt(payload),
            proof=b"p" * (MAX_RECEIPT_PROOF_BYTES + 1),
        )
        with self.assertRaises(NormalizationError) as proof_cap:
            validate_calendar_receipt_hash_binding(payload, oversized_proof)
        self.assertEqual(
            proof_cap.exception.reason_code,
            "INVALID_CALENDAR_RECEIPT",
        )


class AuthorityUnavailableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.regular_open = datetime(2026, 8, 28, 9, 30, tzinfo=NEW_YORK)
        self.event_utc_ns = (
            utc_nanoseconds(self.regular_open) + NANOSECONDS_PER_MINUTE
        )
        self.payload = CalendarHashBindingTests.payload()
        self.receipt = CalendarHashBindingTests.receipt(self.payload)

    def assert_reason(self, reason_code: str, call: object) -> None:
        with self.assertRaises(NormalizationError) as raised:
            call()  # type: ignore[operator]
        self.assertEqual(raised.exception.reason_code, reason_code)

    def test_all_calendar_authority_apis_fail_closed(self) -> None:
        self.assert_reason(
            "CALENDAR_AUTHORITY_UNAVAILABLE",
            lambda: qualify_calendar(self.payload, self.receipt),
        )
        self.assert_reason(
            "CALENDAR_AUTHORITY_UNAVAILABLE",
            lambda: map_session_horizon(date(2026, 11, 20), self.payload),
        )

    def test_all_phase_authority_apis_fail_closed(self) -> None:
        self.assert_reason(
            "PHASE_AUTHORITY_UNAVAILABLE",
            lambda: qualify_market_phase(object()),
        )
        self.assert_reason(
            "PHASE_AUTHORITY_UNAVAILABLE",
            lambda: fast_confirmation_eligible(
                self.event_utc_ns,
                self.regular_open,
                MarketPhase.CORE_CONTINUOUS,
            ),
        )

    def test_no_qualified_artifact_or_minting_seam_remains(self) -> None:
        forbidden_names = (
            "QualifiedCalendar",
            "QualifiedMarketPhase",
            "_qualify_calendar_with_verifier",
            "_qualify_market_phase_with_verifier",
            "_QUALIFIED_CALENDAR_SEAL",
            "_QUALIFIED_PHASE_SEAL",
        )
        for name in forbidden_names:
            with self.subTest(name=name):
                self.assertFalse(hasattr(time_semantics, name))
                self.assertNotIn(name, time_semantics.__all__)


if __name__ == "__main__":
    unittest.main()
