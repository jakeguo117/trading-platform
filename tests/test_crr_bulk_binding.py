from __future__ import annotations

import copy
from dataclasses import fields, replace
import unittest
from unittest.mock import patch

from benchmarks.crr_p1_contract import load_corpus
from gld_normalizer.errors import NormalizationError
import gld_research_core.facts as facts_module
import gld_research_core.crr_input_binding as binding_module
import gld_research_core.p1_native_decision as decision_module
from gld_research_core.crr_delta import (
    bind_crr_call_inputs_from_snapshot,
)
from gld_research_core.crr_input_binding import (
    bind_crr_call_inputs_bulk_from_snapshot,
)
from gld_research_core.facts import OptionQuoteSnapshotV1, OptionQuoteV1
from gld_research_core.p1_native_decision import (
    P1_CONTRACT_SHA256,
    RESEARCH_CONTRACT_SHA256,
    P1NativeDecisionError,
)


class _StatefulSnapshot(OptionQuoteSnapshotV1):
    @property
    def snapshot_sha256(self) -> str:
        reads = getattr(self, "snapshot_hash_reads", 0) + 1
        object.__setattr__(self, "snapshot_hash_reads", reads)
        return f"{reads:064x}"


class CrrBulkInputBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = load_corpus("U64")
        cls.corpora = (cls.corpus, load_corpus("W64"))

    def _bind_one_by_one(
        self,
        contracts: tuple[object, ...],
        *,
        corpus: object | None = None,
    ) -> tuple[object, ...]:
        selected_corpus = self.corpus if corpus is None else corpus
        return tuple(
            bind_crr_call_inputs_from_snapshot(
                selected_corpus.snapshot,
                contract=contract,
                pit_inputs=selected_corpus.pit_inputs,
            )
            for contract in contracts
        )

    def test_bulk_matches_single_binding_in_requested_order(self) -> None:
        for corpus in self.corpora:
            contracts = tuple(
                quote.contract for quote in corpus.snapshot.option_quotes
            )
            for ordered_contracts in (contracts, tuple(reversed(contracts))):
                with self.subTest(
                    fixture=corpus.fixture_id,
                    first=ordered_contracts[0].occ_symbol,
                ):
                    expected = self._bind_one_by_one(
                        ordered_contracts,
                        corpus=corpus,
                    )
                    actual = bind_crr_call_inputs_bulk_from_snapshot(
                        corpus.snapshot,
                        contracts=ordered_contracts,
                        pit_inputs=corpus.pit_inputs,
                    )

                    self.assertEqual(actual, expected)
                    self.assertEqual(
                        tuple(item.contract_id for item in actual),
                        tuple(item.contract_id for item in expected),
                    )
                    self.assertEqual(
                        tuple(item.input_sha256 for item in actual),
                        tuple(item.input_sha256 for item in expected),
                    )

    def test_duplicate_requested_contract_preserves_single_call_semantics(self) -> None:
        contracts = tuple(
            quote.contract for quote in self.corpus.snapshot.option_quotes
        )
        duplicated = (contracts[0], contracts[0], *contracts[2:])

        actual = bind_crr_call_inputs_bulk_from_snapshot(
            self.corpus.snapshot,
            contracts=duplicated,
            pit_inputs=self.corpus.pit_inputs,
        )

        self.assertEqual(actual, self._bind_one_by_one(duplicated))
        self.assertEqual(actual[0], actual[1])
        self.assertEqual(actual[0].input_sha256, actual[1].input_sha256)

    def test_missing_and_same_symbol_contract_mismatch_fail_like_single_binding(
        self,
    ) -> None:
        contracts = tuple(
            quote.contract for quote in self.corpus.snapshot.option_quotes
        )
        missing = replace(
            contracts[0],
            occ_symbol=f"{contracts[0].occ_symbol[:-8]}00999000",
            strike_nano_usd=999_000_000_000,
        )
        same_symbol_mismatch = replace(
            contracts[0],
            activation_utc_ns=contracts[0].activation_utc_ns - 1,
        )

        for bad_contract in (missing, same_symbol_mismatch):
            with self.subTest(contract=bad_contract):
                with self.assertRaises(NormalizationError) as single_error:
                    bind_crr_call_inputs_from_snapshot(
                        self.corpus.snapshot,
                        contract=bad_contract,
                        pit_inputs=self.corpus.pit_inputs,
                    )
                with self.assertRaises(NormalizationError) as bulk_error:
                    bind_crr_call_inputs_bulk_from_snapshot(
                        self.corpus.snapshot,
                        contracts=(bad_contract, *contracts[1:]),
                        pit_inputs=self.corpus.pit_inputs,
                    )

                self.assertEqual(
                    bulk_error.exception.reason_code,
                    single_error.exception.reason_code,
                )
                self.assertEqual(
                    bulk_error.exception.reason_code,
                    "DELTA_INPUT_BINDING_MISMATCH",
                )

    def test_causality_failures_match_single_binding(self) -> None:
        contracts = tuple(
            quote.contract for quote in self.corpus.snapshot.option_quotes
        )
        future_book = replace(
            self.corpus.snapshot.option_quotes[0].top_of_book,
            ts_event_ns=self.corpus.snapshot.capture_utc_ns + 1,
            ts_recv_ns=self.corpus.snapshot.capture_utc_ns + 1,
        )
        future_quote = OptionQuoteV1(
            contract=contracts[0],
            top_of_book=future_book,
        )
        future_snapshot = replace(
            self.corpus.snapshot,
            option_quotes=(future_quote, *self.corpus.snapshot.option_quotes[1:]),
        )
        stale_pit = replace(
            self.corpus.pit_inputs,
            as_of_utc_ns=self.corpus.pit_inputs.as_of_utc_ns - 1,
        )
        cases = (
            (future_snapshot, self.corpus.pit_inputs),
            (self.corpus.snapshot, stale_pit),
        )

        for snapshot, pit_inputs in cases:
            with self.subTest(snapshot=snapshot, pit_inputs=pit_inputs):
                with self.assertRaises(NormalizationError) as single_error:
                    bind_crr_call_inputs_from_snapshot(
                        snapshot,
                        contract=contracts[0],
                        pit_inputs=pit_inputs,
                    )
                with self.assertRaises(NormalizationError) as bulk_error:
                    bind_crr_call_inputs_bulk_from_snapshot(
                        snapshot,
                        contracts=contracts,
                        pit_inputs=pit_inputs,
                    )

                self.assertEqual(
                    bulk_error.exception.reason_code,
                    single_error.exception.reason_code,
                )
                self.assertEqual(
                    bulk_error.exception.reason_code,
                    "SNAPSHOT_NOT_CAUSAL",
                )

    def test_bulk_shares_hashes_and_rechecks_snapshot_integrity_once(self) -> None:
        contracts = tuple(
            quote.contract for quote in self.corpus.snapshot.option_quotes
        )
        original_binding_hash = binding_module.canonical_snapshot_sha256
        original_facts_hash = facts_module.canonical_snapshot_sha256
        snapshot_calls = 0
        underlying_calls = 0

        def count_facts_hash(value: object) -> str:
            nonlocal snapshot_calls
            if value is self.corpus.snapshot:
                snapshot_calls += 1
            return original_facts_hash(value)

        def count_binding_hash(value: object) -> str:
            nonlocal underlying_calls
            if value is self.corpus.snapshot.underlying_top:
                underlying_calls += 1
            return original_binding_hash(value)

        with (
            patch.object(
                facts_module,
                "canonical_snapshot_sha256",
                side_effect=count_facts_hash,
            ),
            patch.object(
                binding_module,
                "canonical_snapshot_sha256",
                side_effect=count_binding_hash,
            ),
        ):
            result = bind_crr_call_inputs_bulk_from_snapshot(
                self.corpus.snapshot,
                contracts=contracts,
                pit_inputs=self.corpus.pit_inputs,
            )

        self.assertEqual(len(result), 64)
        self.assertEqual(snapshot_calls, 2)
        self.assertEqual(underlying_calls, 1)

    def test_stateful_snapshot_subclass_uses_single_binding_semantics(self) -> None:
        values = {
            item.name: getattr(self.corpus.snapshot, item.name)
            for item in fields(OptionQuoteSnapshotV1)
        }
        sequential_snapshot = _StatefulSnapshot(**values)
        bulk_snapshot = _StatefulSnapshot(**values)
        contracts = tuple(
            quote.contract for quote in self.corpus.snapshot.option_quotes
        )

        expected = tuple(
            bind_crr_call_inputs_from_snapshot(
                sequential_snapshot,
                contract=contract,
                pit_inputs=self.corpus.pit_inputs,
            )
            for contract in contracts
        )
        actual = bind_crr_call_inputs_bulk_from_snapshot(
            bulk_snapshot,
            contracts=contracts,
            pit_inputs=self.corpus.pit_inputs,
        )

        self.assertEqual(actual, expected)
        self.assertEqual(sequential_snapshot.snapshot_hash_reads, 64)
        self.assertEqual(bulk_snapshot.snapshot_hash_reads, 64)

    def test_exact_fast_path_retries_single_binding_after_snapshot_mutation(
        self,
    ) -> None:
        snapshot = copy.deepcopy(self.corpus.snapshot)
        contracts = tuple(quote.contract for quote in snapshot.option_quotes)
        original_facts_hash = facts_module.canonical_snapshot_sha256
        snapshot_hash_calls = 0

        def mutate_after_first_snapshot_hash(value: object) -> str:
            nonlocal snapshot_hash_calls
            digest = original_facts_hash(value)
            if value is snapshot:
                snapshot_hash_calls += 1
                if snapshot_hash_calls == 1:
                    object.__setattr__(
                        snapshot.underlying_top,
                        "bid_size",
                        snapshot.underlying_top.bid_size + 1,
                    )
            return digest

        with patch.object(
            facts_module,
            "canonical_snapshot_sha256",
            side_effect=mutate_after_first_snapshot_hash,
        ):
            actual = bind_crr_call_inputs_bulk_from_snapshot(
                snapshot,
                contracts=contracts,
                pit_inputs=self.corpus.pit_inputs,
            )

        expected = tuple(
            bind_crr_call_inputs_from_snapshot(
                snapshot,
                contract=contract,
                pit_inputs=self.corpus.pit_inputs,
            )
            for contract in contracts
        )
        self.assertEqual(actual, expected)
        self.assertEqual(snapshot_hash_calls, 66)

    def test_wrong_container_and_contract_type_fail_closed(self) -> None:
        contracts = tuple(
            quote.contract for quote in self.corpus.snapshot.option_quotes
        )
        cases = (
            [*contracts],
            (contracts[0], object(), *contracts[2:]),
        )

        for bad_contracts in cases:
            with self.subTest(container=type(bad_contracts).__name__):
                with self.assertRaises(NormalizationError) as raised:
                    bind_crr_call_inputs_bulk_from_snapshot(
                        self.corpus.snapshot,
                        contracts=bad_contracts,  # type: ignore[arg-type]
                        pit_inputs=self.corpus.pit_inputs,
                    )
                self.assertEqual(
                    raised.exception.reason_code,
                    "DELTA_INPUT_MISSING",
                )

        stale_pit = replace(
            self.corpus.pit_inputs,
            as_of_utc_ns=self.corpus.pit_inputs.as_of_utc_ns - 1,
        )
        with self.assertRaises(NormalizationError) as single_error:
            bind_crr_call_inputs_from_snapshot(
                self.corpus.snapshot,
                contract=object(),  # type: ignore[arg-type]
                pit_inputs=stale_pit,
            )
        with self.assertRaises(NormalizationError) as bulk_error:
            bind_crr_call_inputs_bulk_from_snapshot(
                self.corpus.snapshot,
                contracts=(object(), *contracts[1:]),  # type: ignore[arg-type]
                pit_inputs=stale_pit,
            )
        self.assertEqual(single_error.exception.reason_code, "DELTA_INPUT_MISSING")
        self.assertEqual(bulk_error.exception.reason_code, "DELTA_INPUT_MISSING")

    def test_u64_w64_failure_order_matches_the_single_binding_loop(self) -> None:
        for corpus in self.corpora:
            contracts = tuple(
                quote.contract for quote in corpus.snapshot.option_quotes
            )
            missing = replace(
                contracts[1],
                occ_symbol=f"{contracts[1].occ_symbol[:-8]}00998000",
                strike_nano_usd=998_000_000_000,
            )
            future_underlying = replace(
                corpus.snapshot,
                underlying_top=replace(
                    corpus.snapshot.underlying_top,
                    ts_event_ns=corpus.snapshot.capture_utc_ns + 1,
                    ts_recv_ns=corpus.snapshot.capture_utc_ns + 1,
                ),
            )
            odd_underlying = replace(
                corpus.snapshot,
                underlying_top=replace(
                    corpus.snapshot.underlying_top,
                    ask_nano_usd=(
                        corpus.snapshot.underlying_top.ask_nano_usd + 1
                    ),
                ),
            )
            stale_pit = replace(
                corpus.pit_inputs,
                as_of_utc_ns=corpus.pit_inputs.as_of_utc_ns - 1,
            )
            cases = (
                (
                    future_underlying,
                    corpus.pit_inputs,
                    (missing, *contracts[1:]),
                    "DELTA_INPUT_BINDING_MISMATCH",
                ),
                (
                    odd_underlying,
                    corpus.pit_inputs,
                    (contracts[0], missing, *contracts[2:]),
                    "DELTA_INPUT_MIDPOINT_NON_INTEGRAL",
                ),
                (
                    future_underlying,
                    corpus.pit_inputs,
                    (contracts[0], missing, *contracts[2:]),
                    "SNAPSHOT_NOT_CAUSAL",
                ),
                (
                    future_underlying,
                    stale_pit,
                    (missing, *contracts[1:]),
                    "SNAPSHOT_NOT_CAUSAL",
                ),
            )
            for snapshot, pit_inputs, ordered_contracts, expected_reason in cases:
                with self.subTest(
                    fixture=corpus.fixture_id,
                    expected_reason=expected_reason,
                ):
                    with self.assertRaises(NormalizationError) as single_error:
                        tuple(
                            bind_crr_call_inputs_from_snapshot(
                                snapshot,
                                contract=contract,
                                pit_inputs=pit_inputs,
                            )
                            for contract in ordered_contracts
                        )
                    with self.assertRaises(NormalizationError) as bulk_error:
                        bind_crr_call_inputs_bulk_from_snapshot(
                            snapshot,
                            contracts=ordered_contracts,
                            pit_inputs=pit_inputs,
                        )
                    self.assertEqual(
                        single_error.exception.reason_code,
                        expected_reason,
                    )
                    self.assertEqual(
                        bulk_error.exception.reason_code,
                        expected_reason,
                    )

    def test_missing_contract_precedes_invalid_snapshot_hash_like_single_binding(
        self,
    ) -> None:
        snapshot = copy.deepcopy(self.corpus.snapshot)
        contracts = tuple(quote.contract for quote in snapshot.option_quotes)
        missing = replace(
            contracts[0],
            occ_symbol=f"{contracts[0].occ_symbol[:-8]}00999000",
            strike_nano_usd=999_000_000_000,
        )
        object.__setattr__(snapshot, "source_id", object())
        ordered_contracts = (missing, *contracts[1:])

        with self.assertRaises(NormalizationError) as single_error:
            tuple(
                bind_crr_call_inputs_from_snapshot(
                    snapshot,
                    contract=contract,
                    pit_inputs=self.corpus.pit_inputs,
                )
                for contract in ordered_contracts
            )
        with self.assertRaises(NormalizationError) as bulk_error:
            bind_crr_call_inputs_bulk_from_snapshot(
                snapshot,
                contracts=ordered_contracts,
                pit_inputs=self.corpus.pit_inputs,
            )
        self.assertEqual(
            single_error.exception.reason_code,
            "DELTA_INPUT_BINDING_MISMATCH",
        )
        self.assertEqual(
            bulk_error.exception.reason_code,
            "DELTA_INPUT_BINDING_MISMATCH",
        )

    def test_native_decision_protects_bulk_binder_before_and_after_engine_creation(
        self,
    ) -> None:
        original = decision_module.bind_crr_call_inputs_bulk_from_snapshot

        def replacement(*args: object, **kwargs: object) -> object:
            return original(*args, **kwargs)

        compute = lambda *_args, **_kwargs: None
        verify = lambda _value: False
        with patch.object(
            decision_module,
            "bind_crr_call_inputs_bulk_from_snapshot",
            replacement,
        ):
            with self.assertRaises(P1NativeDecisionError) as factory_error:
                decision_module.create_p1_native_decision_shadow_engine(
                    compute_call=compute,
                    verify_call=verify,
                    combined_backend_evidence_sha256="a" * 64,
                )
        self.assertEqual(
            factory_error.exception.reason_code,
            "NATIVE_DECISION_RUNTIME_INTEGRITY_INVALID",
        )

        run, _, _ = (
            decision_module._create_p1_native_decision_shadow_engine_for_tests(
                compute_call=compute,
                verify_call=verify,
                combined_backend_evidence_sha256="a" * 64,
            )
        )
        with patch.object(
            decision_module,
            "bind_crr_call_inputs_bulk_from_snapshot",
            replacement,
        ):
            with self.assertRaises(P1NativeDecisionError) as runtime_error:
                run(
                    signal=self.corpus.signal,
                    candidate_snapshots=(self.corpus.snapshot,),
                    pit_inputs=self.corpus.pit_inputs,
                    lc0_binding=self.corpus.lc0_binding,
                    fees=self.corpus.fees,
                    research_contract_sha256=RESEARCH_CONTRACT_SHA256,
                    p1_contract_sha256=P1_CONTRACT_SHA256,
                    rule_package_version=self.corpus.signal.rule_version,
                    quantity=1,
                )
        self.assertEqual(
            runtime_error.exception.reason_code,
            "NATIVE_DECISION_RUNTIME_INTEGRITY_INVALID",
        )

    def test_duplicate_snapshot_symbol_and_failure_precedence_match_single_loop(
        self,
    ) -> None:
        contracts = tuple(
            quote.contract for quote in self.corpus.snapshot.option_quotes
        )
        duplicate_snapshot = copy.deepcopy(self.corpus.snapshot)
        object.__setattr__(
            duplicate_snapshot,
            "option_quotes",
            (
                duplicate_snapshot.option_quotes[0],
                duplicate_snapshot.option_quotes[0],
                *duplicate_snapshot.option_quotes[2:],
            ),
        )
        missing = replace(
            contracts[1],
            occ_symbol=f"{contracts[1].occ_symbol[:-8]}00998000",
            strike_nano_usd=998_000_000_000,
        )
        odd_underlying = replace(
            self.corpus.snapshot,
            underlying_top=replace(
                self.corpus.snapshot.underlying_top,
                ask_nano_usd=(
                    self.corpus.snapshot.underlying_top.ask_nano_usd + 1
                ),
            ),
        )
        future_underlying = replace(
            self.corpus.snapshot,
            underlying_top=replace(
                self.corpus.snapshot.underlying_top,
                ts_event_ns=self.corpus.snapshot.capture_utc_ns + 1,
                ts_recv_ns=self.corpus.snapshot.capture_utc_ns + 1,
            ),
        )
        stale_pit = replace(
            self.corpus.pit_inputs,
            as_of_utc_ns=self.corpus.pit_inputs.as_of_utc_ns - 1,
        )
        cases = (
            (
                duplicate_snapshot,
                self.corpus.pit_inputs,
                contracts,
                "DELTA_INPUT_BINDING_MISMATCH",
            ),
            (
                odd_underlying,
                self.corpus.pit_inputs,
                (contracts[0], missing, *contracts[2:]),
                "DELTA_INPUT_MIDPOINT_NON_INTEGRAL",
            ),
            (
                future_underlying,
                self.corpus.pit_inputs,
                (contracts[0], missing, *contracts[2:]),
                "SNAPSHOT_NOT_CAUSAL",
            ),
            (
                future_underlying,
                stale_pit,
                (missing, *contracts[1:]),
                "SNAPSHOT_NOT_CAUSAL",
            ),
            (
                future_underlying,
                self.corpus.pit_inputs,
                (missing, *contracts[1:]),
                "DELTA_INPUT_BINDING_MISMATCH",
            ),
        )

        for snapshot, pit_inputs, ordered_contracts, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                with self.assertRaises(NormalizationError) as single_error:
                    tuple(
                        bind_crr_call_inputs_from_snapshot(
                            snapshot,
                            contract=contract,
                            pit_inputs=pit_inputs,
                        )
                        for contract in ordered_contracts
                    )
                with self.assertRaises(NormalizationError) as bulk_error:
                    bind_crr_call_inputs_bulk_from_snapshot(
                        snapshot,
                        contracts=ordered_contracts,
                        pit_inputs=pit_inputs,
                    )

                self.assertEqual(single_error.exception.reason_code, expected_reason)
                self.assertEqual(bulk_error.exception.reason_code, expected_reason)


if __name__ == "__main__":
    unittest.main()
