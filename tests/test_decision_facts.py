from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timezone
from unittest import mock
import unittest

import gld_research_core.facts as facts_module
from gld_normalizer.errors import NormalizationError
from gld_research_core.facts import (
    MAX_OPTION_SNAPSHOT_CANDIDATES,
    QUOTE_AGE_LIMIT_NS,
    QUOTE_QUALITY_POLICY_SHA256,
    QUOTE_QUALITY_POLICY_VERSION,
    RECEIVE_SKEW_LIMIT_NS,
    REAL_OPTION_FACT_EXECUTION_STATUS,
    OptionContractV1,
    OptionQuoteSnapshotV1,
    OptionQuoteV1,
    OptionSnapshotCandidateLedgerV1,
    SignalSnapshotV1,
    TopOfBookV1,
    build_option_snapshot_candidate_ledger,
    canonical_snapshot_sha256,
    select_first_complete_option_snapshot,
    validate_option_snapshot_quality,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
def utc_nanoseconds(value: datetime) -> int:
    return int(value.timestamp()) * 1_000_000_000


WINDOW_START_NS = utc_nanoseconds(
    datetime(2026, 8, 28, 14, 45, tzinfo=timezone.utc)
)
WINDOW_END_NS = WINDOW_START_NS + 60_000_000_000
EXPIRY_NS = utc_nanoseconds(
    datetime(2026, 12, 18, 21, 0, tzinfo=timezone.utc)
)
LAST_TRADING_NS = utc_nanoseconds(
    datetime(2026, 12, 18, 20, 59, tzinfo=timezone.utc)
)
ACTIVATION_NS = utc_nanoseconds(
    datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
)


def signal_snapshot(**changes: object) -> SignalSnapshotV1:
    values: dict[str, object] = {
        "signal_id": "gld-gate-a-2026-08-28",
        "trading_date": "2026-08-28",
        "rule_version": "gld-research-v0.2",
        "rule_sha256": SHA_A,
        "calendar_authority_id": "XNYS_OFFICIAL_SESSION_CALENDAR",
        "calendar_version": "2026-08-28",
        "calendar_sha256": SHA_A,
        "phase_receipt_sha256": SHA_B,
        "completeness_receipt_sha256": SHA_A,
        "cutoff_utc_ns": WINDOW_START_NS,
        "max_event_utc_ns": WINDOW_START_NS - 2,
        "max_receive_utc_ns": WINDOW_START_NS - 1,
        "signal_state": "PASS",
        "input_fact_sha256": SHA_B,
    }
    values.update(changes)
    return SignalSnapshotV1(**values)  # type: ignore[arg-type]


def option_contract(**changes: object) -> OptionContractV1:
    values: dict[str, object] = {
        "occ_symbol": "GLD   261218C00300000",
        "underlying": "GLD",
        "right": "C",
        "strike_nano_usd": 300_000_000_000,
        "expiry_utc_ns": EXPIRY_NS,
        "last_trading_utc_ns": LAST_TRADING_NS,
        "activation_utc_ns": ACTIVATION_NS,
        "multiplier": 100,
        "deliverable": "100 GLD",
        "currency": "USD",
        "standard_unadjusted": True,
        "exercise_style": "AMERICAN",
    }
    values.update(changes)
    return OptionContractV1(**values)  # type: ignore[arg-type]


def top_of_book(**changes: object) -> TopOfBookV1:
    values: dict[str, object] = {
        "bid_nano_usd": 9_000_000_000,
        "ask_nano_usd": 10_000_000_000,
        "bid_size": 10,
        "ask_size": 12,
        "tick_nano_usd": 10_000_000,
        "ts_event_ns": WINDOW_START_NS + 9_000_000_000,
        "ts_recv_ns": WINDOW_START_NS + 9_100_000_000,
        "flags": (),
    }
    values.update(changes)
    return TopOfBookV1(**values)  # type: ignore[arg-type]


def option_quote(
    *,
    contract: OptionContractV1 | None = None,
    book: TopOfBookV1 | None = None,
) -> OptionQuoteV1:
    return OptionQuoteV1(
        contract=contract or option_contract(),
        top_of_book=book or top_of_book(),
    )


def option_snapshot(**changes: object) -> OptionQuoteSnapshotV1:
    capture = WINDOW_START_NS + 10_000_000_000
    values: dict[str, object] = {
        "signal_snapshot_sha256": signal_snapshot().snapshot_sha256,
        "window_start_utc_ns": WINDOW_START_NS,
        "window_end_utc_ns": WINDOW_END_NS,
        "capture_utc_ns": capture,
        "underlying_top": top_of_book(
            bid_nano_usd=299_900_000_000,
            ask_nano_usd=300_100_000_000,
            ts_event_ns=capture - 1_000_000_000,
            ts_recv_ns=capture - 900_000_000,
        ),
        "option_quotes": (
            option_quote(
                book=top_of_book(
                    ts_event_ns=capture - 850_000_000,
                    ts_recv_ns=capture - 800_000_000,
                )
            ),
        ),
        "source_id": "SYNTHETIC_FIXTURE_ONLY",
        "source_version": "fixture-v1",
        "market_data_type": "SYNTHETIC_NOT_ENTITLED",
        "source_receipt_sha256": SHA_A,
        "candidate_ordinal": 1,
        "candidate_provenance_sha256": SHA_B,
    }
    values.update(changes)
    return OptionQuoteSnapshotV1(**values)  # type: ignore[arg-type]


class SignalSnapshotTests(unittest.TestCase):
    def test_real_source_binding_is_explicitly_not_implemented(self) -> None:
        self.assertEqual(
            REAL_OPTION_FACT_EXECUTION_STATUS,
            "NOT_IMPLEMENTED_QUALIFIED_SOURCE_BINDING_REQUIRED",
        )

    def test_cutoff_is_strict_for_event_and_receive_times(self) -> None:
        for field_name in ("max_event_utc_ns", "max_receive_utc_ns"):
            with self.subTest(field=field_name):
                with self.assertRaises(NormalizationError) as raised:
                    signal_snapshot(**{field_name: WINDOW_START_NS})
                self.assertEqual(
                    raised.exception.reason_code,
                    "SIGNAL_CAUSALITY_VIOLATION",
                )

    def test_numeric_fields_reject_bool_float_and_uint64_sentinel(self) -> None:
        for invalid in (True, 1.5, 2**64 - 1):
            with self.subTest(invalid=invalid):
                with self.assertRaises(NormalizationError):
                    signal_snapshot(cutoff_utc_ns=invalid)
                with self.assertRaises(NormalizationError):
                    option_contract(strike_nano_usd=invalid)
                with self.assertRaises(NormalizationError):
                    top_of_book(ts_recv_ns=invalid)
                with self.assertRaises(NormalizationError):
                    option_snapshot(capture_utc_ns=invalid)

    def test_signal_binding_and_state_are_strict(self) -> None:
        for change, reason_code in (
            ({"rule_version": ""}, "SIGNAL_RULE_BINDING_INVALID"),
            ({"rule_sha256": "not-a-hash"}, "SIGNAL_RULE_BINDING_INVALID"),
            ({"input_fact_sha256": "not-a-hash"}, "SIGNAL_SNAPSHOT_INVALID"),
            ({"signal_state": "UNKNOWN"}, "SIGNAL_SNAPSHOT_INVALID"),
            (
                {
                    "max_event_utc_ns": WINDOW_START_NS - 1,
                    "max_receive_utc_ns": WINDOW_START_NS - 2,
                },
                "SIGNAL_CAUSALITY_VIOLATION",
            ),
        ):
            with self.subTest(change=change):
                with self.assertRaises(NormalizationError) as raised:
                    signal_snapshot(**change)
                self.assertEqual(raised.exception.reason_code, reason_code)

    def test_identical_signal_content_has_identical_hash(self) -> None:
        first = signal_snapshot()
        second = signal_snapshot()
        self.assertEqual(first.snapshot_sha256, second.snapshot_sha256)
        self.assertEqual(len(first.snapshot_sha256), 64)

    def test_cutoff_is_derived_from_trading_date_in_new_york(self) -> None:
        summer = signal_snapshot()
        winter_start = utc_nanoseconds(
            datetime(2026, 1, 5, 15, 45, tzinfo=timezone.utc)
        )
        winter = signal_snapshot(
            signal_id="gld-gate-a-2026-01-05",
            trading_date="2026-01-05",
            cutoff_utc_ns=winter_start,
            max_event_utc_ns=winter_start - 2,
            max_receive_utc_ns=winter_start - 1,
        )

        self.assertEqual(summer.cutoff_utc_ns, WINDOW_START_NS)
        self.assertEqual(winter.cutoff_utc_ns, winter_start)

        with self.assertRaises(NormalizationError) as raised:
            signal_snapshot(cutoff_utc_ns=WINDOW_START_NS + 1)
        self.assertEqual(raised.exception.reason_code, "SIGNAL_CUTOFF_INVALID")

    def test_trading_date_and_calendar_authority_fail_closed(self) -> None:
        cases = (
            ({"trading_date": "2026-08-29"}, "TRADING_CALENDAR_BINDING_INVALID"),
            ({"trading_date": "2026-8-28"}, "TRADING_CALENDAR_BINDING_INVALID"),
            ({"calendar_authority_id": ""}, "TRADING_CALENDAR_BINDING_INVALID"),
            ({"calendar_version": ""}, "TRADING_CALENDAR_BINDING_INVALID"),
            ({"calendar_sha256": "bad"}, "TRADING_CALENDAR_BINDING_INVALID"),
        )
        for changes, reason_code in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(NormalizationError) as raised:
                    signal_snapshot(**changes)
                self.assertEqual(raised.exception.reason_code, reason_code)


class CanonicalSnapshotHashTests(unittest.TestCase):
    def test_each_facts_hash_root_has_an_exact_domain_registration(self) -> None:
        roots = (
            signal_snapshot(),
            option_contract(),
            top_of_book(),
            option_quote(),
            option_snapshot(),
        )

        for root in roots:
            with self.subTest(root=type(root).__name__):
                self.assertEqual(len(canonical_snapshot_sha256(root)), 64)

    def test_root_requires_an_exact_whitelisted_fact_type(self) -> None:
        original = signal_snapshot()
        forged_mapping = {
            "schema": "SignalSnapshotV1",
            "fields": {
                field.name: getattr(original, field.name)
                for field in fields(original)
            },
        }

        with self.assertRaises(NormalizationError) as raised:
            canonical_snapshot_sha256(forged_mapping)
        self.assertEqual(raised.exception.reason_code, "CANONICAL_SNAPSHOT_INVALID")

    def test_hash_envelope_is_domain_and_schema_version_separated(self) -> None:
        snapshot = signal_snapshot()
        old_unseparated_payload = facts_module.json.dumps(
            facts_module._canonical_value(snapshot),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")

        self.assertNotEqual(
            snapshot.snapshot_sha256,
            facts_module.sha256(old_unseparated_payload).hexdigest(),
        )

    def test_cycles_depth_nodes_and_encoded_bytes_fail_closed(self) -> None:
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        cyclic_snapshot = signal_snapshot()
        object.__setattr__(cyclic_snapshot, "rule_version", cyclic)
        with self.assertRaises(NormalizationError) as raised:
            canonical_snapshot_sha256(cyclic_snapshot)
        self.assertEqual(raised.exception.reason_code, "CANONICAL_SNAPSHOT_INVALID")

        deep: object = "leaf"
        for _ in range(40):
            deep = {"next": deep}
        deep_snapshot = signal_snapshot()
        object.__setattr__(deep_snapshot, "rule_version", deep)
        with self.assertRaises(NormalizationError) as raised:
            canonical_snapshot_sha256(deep_snapshot)
        self.assertEqual(raised.exception.reason_code, "CANONICAL_SNAPSHOT_LIMIT_EXCEEDED")

        with mock.patch.object(facts_module, "MAX_CANONICAL_NODES", 5):
            with self.assertRaises(NormalizationError) as raised:
                canonical_snapshot_sha256(signal_snapshot())
        self.assertEqual(raised.exception.reason_code, "CANONICAL_SNAPSHOT_LIMIT_EXCEEDED")

        with mock.patch.object(facts_module, "MAX_CANONICAL_ENCODED_BYTES", 32):
            with self.assertRaises(NormalizationError) as raised:
                canonical_snapshot_sha256(signal_snapshot())
        self.assertEqual(raised.exception.reason_code, "CANONICAL_SNAPSHOT_LIMIT_EXCEEDED")


class OptionFactTests(unittest.TestCase):
    def test_public_facts_are_frozen_slotted_dataclasses(self) -> None:
        ledger = build_option_snapshot_candidate_ledger(
            (option_snapshot(),),
            signal_snapshot=signal_snapshot(),
        )
        instances = (
            signal_snapshot(),
            option_contract(),
            top_of_book(),
            option_quote(),
            option_snapshot(),
            ledger,
        )
        for item in instances:
            with self.subTest(type=type(item).__name__):
                self.assertTrue(hasattr(type(item), "__slots__"))
                self.assertGreater(len(fields(item)), 0)
                with self.assertRaises(FrozenInstanceError):
                    setattr(item, fields(item)[0].name, object())

    def test_contract_identity_is_complete_and_consistent(self) -> None:
        invalid_changes = (
            {"occ_symbol": "BAD"},
            {"occ_symbol": "GLD261218C00300000"},
            {"occ_symbol": "GLD   261332C00300000"},
            {"occ_symbol": "GLD   261218P00300000"},
            {"occ_symbol": "GLD   261218C00300001"},
            {"underlying": "SLV"},
            {"right": "P"},
            {"strike_nano_usd": 0},
            {"expiry_utc_ns": LAST_TRADING_NS},
            {"activation_utc_ns": LAST_TRADING_NS},
            {"multiplier": 1},
            {"deliverable": "ADJUSTED"},
            {"currency": "EUR"},
            {"standard_unadjusted": False},
            {"exercise_style": "EUROPEAN"},
            {"exercise_style": "american"},
            {"exercise_style": ""},
            {"exercise_style": None},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                with self.assertRaises(NormalizationError) as raised:
                    option_contract(**changes)
                self.assertEqual(raised.exception.reason_code, "IDENTITY_UNRESOLVED")

    def test_top_of_book_rejects_missing_crossed_and_zero_bid_quotes(self) -> None:
        cases = (
            ({"bid_nano_usd": 0}, "ZERO_BID"),
            ({"bid_nano_usd": 10_000_000_000}, "QUOTE_CROSSED"),
            ({"bid_nano_usd": 11_000_000_000}, "QUOTE_CROSSED"),
            ({"ask_nano_usd": 0}, "QUOTE_MISSING"),
            ({"bid_size": 0}, "QUOTE_MISSING"),
            ({"ask_size": 0}, "QUOTE_MISSING"),
            ({"tick_nano_usd": 0}, "QUOTE_MISSING"),
            ({"ts_recv_ns": WINDOW_START_NS}, "QUOTE_MISSING"),
            ({"flags": ["HALTED"]}, "QUOTE_MISSING"),
        )
        for changes, reason_code in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(NormalizationError) as raised:
                    top_of_book(**changes)
                self.assertEqual(raised.exception.reason_code, reason_code)

    def test_snapshot_uses_a_sixty_second_half_open_window(self) -> None:
        start = option_snapshot(capture_utc_ns=WINDOW_START_NS)
        self.assertEqual(start.capture_utc_ns, WINDOW_START_NS)

        for changes in (
            {"capture_utc_ns": WINDOW_END_NS},
            {"capture_utc_ns": WINDOW_START_NS - 1},
            {"window_end_utc_ns": WINDOW_END_NS + 1},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(NormalizationError) as raised:
                    option_snapshot(**changes)
                self.assertEqual(
                    raised.exception.reason_code,
                    "OPTION_SNAPSHOT_WINDOW_INVALID",
                )

    def test_snapshot_rejects_carry_forward_quotes_from_before_window(self) -> None:
        before_start = WINDOW_START_NS - 1
        for field_name in ("ts_event_ns", "ts_recv_ns"):
            changes = {
                "ts_event_ns": WINDOW_START_NS,
                "ts_recv_ns": WINDOW_START_NS,
                field_name: before_start,
            }
            if field_name == "ts_recv_ns":
                changes["ts_event_ns"] = before_start
            with self.subTest(field=field_name):
                with self.assertRaises(NormalizationError) as raised:
                    option_snapshot(
                        option_quotes=(
                            option_quote(book=top_of_book(**changes)),
                        ),
                    )
                self.assertEqual(
                    raised.exception.reason_code,
                    "OPTION_QUOTE_BEFORE_WINDOW",
                )

    def test_snapshot_binding_quotes_and_identity_are_strict(self) -> None:
        duplicate = option_quote()
        for changes, reason_code in (
            ({"signal_snapshot_sha256": "bad"}, "OPTION_SNAPSHOT_BINDING_MISMATCH"),
            ({"source_receipt_sha256": "bad"}, "OPTION_SNAPSHOT_BINDING_MISMATCH"),
            ({"option_quotes": ()}, "QUOTE_MISSING"),
            ({"option_quotes": [duplicate]}, "QUOTE_MISSING"),
            ({"option_quotes": (duplicate, duplicate)}, "IDENTITY_UNRESOLVED"),
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(NormalizationError) as raised:
                    option_snapshot(**changes)
                self.assertEqual(raised.exception.reason_code, reason_code)

    def test_identical_option_snapshot_content_has_identical_hash(self) -> None:
        first = option_snapshot()
        second = option_snapshot()
        self.assertEqual(first.snapshot_sha256, second.snapshot_sha256)
        self.assertEqual(len(first.snapshot_sha256), 64)
        self.assertFalse(hasattr(first, "source_authority_qualified"))


class FirstCompleteSnapshotSelectionTests(unittest.TestCase):
    @staticmethod
    def shifted_snapshot(
        capture_utc_ns: int,
        *,
        option_bid_nano_usd: int,
        candidate_ordinal: int,
        candidate_provenance_sha256: str,
    ) -> OptionQuoteSnapshotV1:
        option_receive = capture_utc_ns - 200_000_000
        underlying_receive = capture_utc_ns - 300_000_000
        return option_snapshot(
            capture_utc_ns=capture_utc_ns,
            candidate_ordinal=candidate_ordinal,
            candidate_provenance_sha256=candidate_provenance_sha256,
            underlying_top=top_of_book(
                bid_nano_usd=299_900_000_000,
                ask_nano_usd=300_100_000_000,
                ts_event_ns=underlying_receive - 10_000_000,
                ts_recv_ns=underlying_receive,
            ),
            option_quotes=(
                option_quote(
                    book=top_of_book(
                        bid_nano_usd=option_bid_nano_usd,
                        ask_nano_usd=option_bid_nano_usd + 1_000_000_000,
                        ts_event_ns=option_receive - 10_000_000,
                        ts_recv_ns=option_receive,
                    )
                ),
            ),
        )

    def test_first_append_only_ordinal_wins_without_price_hash_tie_break(self) -> None:
        first_ordinal_expensive = self.shifted_snapshot(
            WINDOW_START_NS + 10_000_000_000,
            option_bid_nano_usd=5_000_000_000,
            candidate_ordinal=1,
            candidate_provenance_sha256=SHA_A,
        )
        second_ordinal_better_price = self.shifted_snapshot(
            WINDOW_START_NS + 10_000_000_000,
            option_bid_nano_usd=9_000_000_000,
            candidate_ordinal=2,
            candidate_provenance_sha256=SHA_B,
        )

        selected = select_first_complete_option_snapshot(
            (second_ordinal_better_price, first_ordinal_expensive),
            signal_snapshot=signal_snapshot(),
        )

        self.assertEqual(
            selected.snapshot_sha256,
            first_ordinal_expensive.snapshot_sha256,
        )

    def test_ledger_hash_binds_rejected_candidates_not_only_selection(self) -> None:
        capture = WINDOW_START_NS + 10_000_000_000
        rejected_a = option_snapshot(
            candidate_ordinal=1,
            candidate_provenance_sha256="1" * 64,
            capture_utc_ns=capture,
            underlying_top=top_of_book(
                bid_nano_usd=299_800_000_000,
                ask_nano_usd=300_100_000_000,
                ts_event_ns=capture - 6_100_000_000,
                ts_recv_ns=capture - 6_000_000_000,
            ),
            option_quotes=(
                option_quote(
                    book=top_of_book(
                        ts_event_ns=capture - 6_100_000_000,
                        ts_recv_ns=capture - 6_000_000_000,
                    )
                ),
            ),
        )
        rejected_b = replace(
            rejected_a,
            candidate_provenance_sha256="3" * 64,
            underlying_top=top_of_book(
                bid_nano_usd=299_900_000_000,
                ask_nano_usd=300_200_000_000,
                ts_event_ns=capture - 6_100_000_000,
                ts_recv_ns=capture - 6_000_000_000,
            ),
        )
        selected = self.shifted_snapshot(
            WINDOW_START_NS + 20_000_000_000,
            option_bid_nano_usd=7_000_000_000,
            candidate_ordinal=2,
            candidate_provenance_sha256="2" * 64,
        )
        signal = signal_snapshot()
        ledger_a = build_option_snapshot_candidate_ledger(
            (selected, rejected_a),
            signal_snapshot=signal,
        )
        ledger_b = build_option_snapshot_candidate_ledger(
            (rejected_b, selected),
            signal_snapshot=signal,
        )

        self.assertIsInstance(ledger_a, OptionSnapshotCandidateLedgerV1)
        self.assertEqual(
            tuple(item.candidate_ordinal for item in ledger_a.candidates),
            (1, 2),
        )
        self.assertNotEqual(ledger_a.ledger_sha256, ledger_b.ledger_sha256)
        self.assertEqual(
            select_first_complete_option_snapshot(
                ledger_a,
                signal_snapshot=signal,
            ).snapshot_sha256,
            selected.snapshot_sha256,
        )
        self.assertEqual(
            select_first_complete_option_snapshot(
                ledger_b,
                signal_snapshot=signal,
            ).snapshot_sha256,
            selected.snapshot_sha256,
        )

    def test_candidate_ledger_has_a_conservative_structural_count_cap(self) -> None:
        self.assertEqual(MAX_OPTION_SNAPSHOT_CANDIDATES, 64)
        base = option_snapshot()
        candidates = tuple(
            replace(
                base,
                candidate_ordinal=ordinal,
                candidate_provenance_sha256=f"{ordinal:064x}",
            )
            for ordinal in range(1, MAX_OPTION_SNAPSHOT_CANDIDATES + 2)
        )

        at_cap = build_option_snapshot_candidate_ledger(
            candidates[:-1],
            signal_snapshot=signal_snapshot(),
        )
        self.assertEqual(len(at_cap.candidates), MAX_OPTION_SNAPSHOT_CANDIDATES)
        with self.assertRaises(NormalizationError) as raised:
            build_option_snapshot_candidate_ledger(
                candidates,
                signal_snapshot=signal_snapshot(),
            )
        self.assertEqual(
            raised.exception.reason_code,
            "OPTION_CANDIDATE_LEDGER_INVALID",
        )

    def test_candidate_ledger_requires_exact_tuple_and_frozen_policy(self) -> None:
        values: dict[str, object] = {
            "signal_snapshot_sha256": signal_snapshot().snapshot_sha256,
            "window_start_utc_ns": WINDOW_START_NS,
            "window_end_utc_ns": WINDOW_END_NS,
            "quote_quality_policy_version": QUOTE_QUALITY_POLICY_VERSION,
            "quote_quality_policy_sha256": QUOTE_QUALITY_POLICY_SHA256,
            "candidates": (option_snapshot(),),
        }
        for changes, reason_code in (
            ({"candidates": ()}, "OPTION_CANDIDATE_LEDGER_INVALID"),
            ({"candidates": [option_snapshot()]}, "OPTION_CANDIDATE_LEDGER_INVALID"),
            (
                {"quote_quality_policy_version": "RELAXED_V2"},
                "QUOTE_QUALITY_POLICY_MISMATCH",
            ),
            (
                {"quote_quality_policy_sha256": SHA_A},
                "QUOTE_QUALITY_POLICY_MISMATCH",
            ),
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(NormalizationError) as raised:
                    OptionSnapshotCandidateLedgerV1(
                        **(values | changes)  # type: ignore[arg-type]
                    )
                self.assertEqual(raised.exception.reason_code, reason_code)

    def test_duplicate_ordinal_or_provenance_fails_closed(self) -> None:
        first = self.shifted_snapshot(
            WINDOW_START_NS + 10_000_000_000,
            option_bid_nano_usd=5_000_000_000,
            candidate_ordinal=1,
            candidate_provenance_sha256=SHA_A,
        )
        duplicate_ordinal = self.shifted_snapshot(
            WINDOW_START_NS + 20_000_000_000,
            option_bid_nano_usd=6_000_000_000,
            candidate_ordinal=1,
            candidate_provenance_sha256=SHA_B,
        )
        duplicate_provenance = replace(
            duplicate_ordinal,
            candidate_ordinal=2,
            candidate_provenance_sha256=SHA_A,
        )
        for candidates in (
            (first, duplicate_ordinal),
            (first, duplicate_provenance),
        ):
            with self.subTest(candidates=candidates):
                with self.assertRaises(NormalizationError) as raised:
                    select_first_complete_option_snapshot(
                        candidates,
                        signal_snapshot=signal_snapshot(),
                    )
                self.assertEqual(
                    raised.exception.reason_code,
                    "OPTION_CANDIDATE_PROVENANCE_INVALID",
                )

    def test_candidate_ordinals_must_cover_the_append_only_prefix(self) -> None:
        missing_first = self.shifted_snapshot(
            WINDOW_START_NS + 10_000_000_000,
            option_bid_nano_usd=5_000_000_000,
            candidate_ordinal=2,
            candidate_provenance_sha256=SHA_A,
        )

        with self.assertRaises(NormalizationError) as raised:
            select_first_complete_option_snapshot(
                (missing_first,),
                signal_snapshot=signal_snapshot(),
            )
        self.assertEqual(
            raised.exception.reason_code,
            "OPTION_CANDIDATE_PROVENANCE_INVALID",
        )

    def test_candidate_capture_time_cannot_reverse_append_only_order(self) -> None:
        first = self.shifted_snapshot(
            WINDOW_START_NS + 20_000_000_000,
            option_bid_nano_usd=5_000_000_000,
            candidate_ordinal=1,
            candidate_provenance_sha256=SHA_A,
        )
        second = self.shifted_snapshot(
            WINDOW_START_NS + 10_000_000_000,
            option_bid_nano_usd=6_000_000_000,
            candidate_ordinal=2,
            candidate_provenance_sha256=SHA_B,
        )

        with self.assertRaises(NormalizationError) as raised:
            select_first_complete_option_snapshot(
                (first, second),
                signal_snapshot=signal_snapshot(),
            )
        self.assertEqual(
            raised.exception.reason_code,
            "OPTION_CANDIDATE_PROVENANCE_INVALID",
        )

    def test_stale_skewed_and_halted_candidates_are_skipped(self) -> None:
        stale_capture = WINDOW_START_NS + 10_000_000_000
        stale = option_snapshot(
            capture_utc_ns=stale_capture,
            candidate_ordinal=1,
            candidate_provenance_sha256="1" * 64,
            underlying_top=top_of_book(
                ts_event_ns=stale_capture - 6_100_000_000,
                ts_recv_ns=stale_capture - 6_000_000_000,
            ),
            option_quotes=(
                option_quote(
                    book=top_of_book(
                        ts_event_ns=stale_capture - 6_100_000_000,
                        ts_recv_ns=stale_capture - 6_000_000_000,
                    )
                ),
            ),
        )
        skew_capture = WINDOW_START_NS + 20_000_000_000
        skewed = option_snapshot(
            capture_utc_ns=skew_capture,
            candidate_ordinal=2,
            candidate_provenance_sha256="2" * 64,
            underlying_top=top_of_book(
                ts_event_ns=skew_capture - 100_000_000,
                ts_recv_ns=skew_capture - 50_000_000,
            ),
            option_quotes=(
                option_quote(
                    book=top_of_book(
                        ts_event_ns=skew_capture - 2_100_000_000,
                        ts_recv_ns=skew_capture - 2_000_000_000,
                    )
                ),
            ),
        )
        halted_capture = WINDOW_START_NS + 30_000_000_000
        halted = option_snapshot(
            capture_utc_ns=halted_capture,
            candidate_ordinal=3,
            candidate_provenance_sha256="3" * 64,
            option_quotes=(
                option_quote(
                    book=top_of_book(
                        ts_event_ns=halted_capture - 200_000_000,
                        ts_recv_ns=halted_capture - 100_000_000,
                        flags=("HALTED",),
                    )
                ),
            ),
            underlying_top=top_of_book(
                bid_nano_usd=299_900_000_000,
                ask_nano_usd=300_100_000_000,
                ts_event_ns=halted_capture - 200_000_000,
                ts_recv_ns=halted_capture - 100_000_000,
            ),
        )
        qualified = self.shifted_snapshot(
            WINDOW_START_NS + 40_000_000_000,
            option_bid_nano_usd=7_000_000_000,
            candidate_ordinal=4,
            candidate_provenance_sha256="4" * 64,
        )

        selected = select_first_complete_option_snapshot(
            (qualified, halted, stale, skewed),
            signal_snapshot=signal_snapshot(),
        )

        self.assertEqual(selected.snapshot_sha256, qualified.snapshot_sha256)

    def test_no_qualified_candidate_has_one_stable_terminal_failure(self) -> None:
        capture = WINDOW_START_NS + 10_000_000_000
        stale = option_snapshot(
            capture_utc_ns=capture,
            underlying_top=top_of_book(
                ts_event_ns=capture - 6_100_000_000,
                ts_recv_ns=capture - 6_000_000_000,
            ),
        )
        with self.assertRaises(NormalizationError) as raised:
            select_first_complete_option_snapshot(
                (stale,),
                signal_snapshot=signal_snapshot(),
            )
        self.assertEqual(
            raised.exception.reason_code,
            "EXECUTABLE_OPTION_SNAPSHOT_UNAVAILABLE",
        )

    def test_selector_limits_require_exact_bounded_integers(self) -> None:
        candidate = option_snapshot()
        for invalid in (True, 1.5, 0, 2**64 - 1):
            with self.subTest(invalid=invalid):
                with self.assertRaises(NormalizationError):
                    select_first_complete_option_snapshot(
                        (candidate,),
                        signal_snapshot=signal_snapshot(),
                        quote_age_limit_ns=invalid,  # type: ignore[arg-type]
                    )
                with self.assertRaises(NormalizationError):
                    select_first_complete_option_snapshot(
                        (candidate,),
                        signal_snapshot=signal_snapshot(),
                        receive_skew_limit_ns=invalid,  # type: ignore[arg-type]
                    )

    def test_quote_quality_policy_is_public_frozen_and_hash_bound(self) -> None:
        self.assertEqual(
            QUOTE_QUALITY_POLICY_VERSION,
            "GLD_OPTION_QUOTE_QUALITY_POLICY_V1",
        )
        self.assertEqual(QUOTE_AGE_LIMIT_NS, 5_000_000_000)
        self.assertEqual(RECEIVE_SKEW_LIMIT_NS, 1_000_000_000)
        self.assertEqual(
            QUOTE_QUALITY_POLICY_SHA256,
            "bca07ef4d032136c320353d4c3c3b4063ae4344968295f5e04b9b77ced08c6dc",
        )

    def test_six_second_stale_quote_cannot_be_allowed_by_override(self) -> None:
        capture = WINDOW_START_NS + 10_000_000_000
        stale = option_snapshot(
            capture_utc_ns=capture,
            underlying_top=top_of_book(
                ts_event_ns=capture - 6_100_000_000,
                ts_recv_ns=capture - 6_000_000_000,
            ),
            option_quotes=(
                option_quote(
                    book=top_of_book(
                        ts_event_ns=capture - 6_100_000_000,
                        ts_recv_ns=capture - 6_000_000_000,
                    )
                ),
            ),
        )

        with self.assertRaises(NormalizationError) as raised:
            validate_option_snapshot_quality(
                stale,
                quote_age_limit_ns=6_000_000_000,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "QUOTE_QUALITY_POLICY_MISMATCH",
        )
        with self.assertRaises(NormalizationError) as raised:
            select_first_complete_option_snapshot(
                (stale,),
                signal_snapshot=signal_snapshot(),
                quote_age_limit_ns=6_000_000_000,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "QUOTE_QUALITY_POLICY_MISMATCH",
        )

    def test_one_point_nine_second_skew_cannot_be_allowed_by_override(self) -> None:
        capture = WINDOW_START_NS + 10_000_000_000
        skewed = option_snapshot(
            capture_utc_ns=capture,
            underlying_top=top_of_book(
                ts_event_ns=capture - 110_000_000,
                ts_recv_ns=capture - 100_000_000,
            ),
            option_quotes=(
                option_quote(
                    book=top_of_book(
                        ts_event_ns=capture - 2_010_000_000,
                        ts_recv_ns=capture - 2_000_000_000,
                    )
                ),
            ),
        )

        with self.assertRaises(NormalizationError) as raised:
            validate_option_snapshot_quality(
                skewed,
                receive_skew_limit_ns=2_000_000_000,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "QUOTE_QUALITY_POLICY_MISMATCH",
        )
        with self.assertRaises(NormalizationError) as raised:
            select_first_complete_option_snapshot(
                (skewed,),
                signal_snapshot=signal_snapshot(),
                receive_skew_limit_ns=2_000_000_000,
            )
        self.assertEqual(
            raised.exception.reason_code,
            "QUOTE_QUALITY_POLICY_MISMATCH",
        )

    def test_mixed_signal_bindings_fail_closed(self) -> None:
        first = option_snapshot()
        second = option_snapshot(signal_snapshot_sha256=SHA_B)
        with self.assertRaises(NormalizationError) as raised:
            select_first_complete_option_snapshot(
                (first, second),
                signal_snapshot=signal_snapshot(),
            )
        self.assertEqual(
            raised.exception.reason_code,
            "OPTION_SNAPSHOT_BINDING_MISMATCH",
        )


class PublicSeamTests(unittest.TestCase):
    def test_no_provider_or_order_authority_seams_are_public(self) -> None:
        forbidden = {
            "QualifiedOptionSnapshot",
            "from_ibkr",
            "from_robinhood",
            "create_order",
            "submit_order",
        }
        self.assertTrue(forbidden.isdisjoint(set(dir(facts_module))))


if __name__ == "__main__":
    unittest.main()
