from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from benchmarks.crr_p1_contract import canonical_json_bytes, load_reference_golden
from benchmarks.crr_p1_decision_contract import (
    BACKEND_EVIDENCE_SCHEMA_VERSION,
    DECISION_GOLDEN_SCHEMA_VERSION,
    DECISION_STATIC_MANIFEST_SCHEMA_VERSION,
    FREEZE_STATUS,
    BackendEvidenceEnvelopeV1,
    DecisionContractError,
    assert_formal_timed_receipt_permitted,
    derive_decision_semantic_document,
    load_decision_semantic_golden,
    load_decision_static_manifest,
    semantic_document_sha256,
    validate_backend_evidence_envelope,
)
from benchmarks.generate_crr_p1_decision_goldens import seal_decision_goldens


ROOT = Path(__file__).resolve().parents[1]
SOURCE_STATIC_MANIFEST = ROOT / "benchmarks" / "fixtures" / "static_manifest_v0.1.json"
SOURCE_STATIC_MANIFEST_SHA256 = (
    "5d828faf9edd8f415409aab4997cba24c01584decce315f073491f1f2e4e5ba4"
)


def _golden_document(fixture_id: str) -> dict[str, object]:
    path = (
        ROOT
        / "benchmarks"
        / "fixtures"
        / f"{fixture_id.lower()}_decision_semantic_golden_v0.1.json"
    )
    return json.loads(path.read_text(encoding="ascii"))


def _reseal(document: dict[str, object]) -> None:
    document["decision_semantic_sha256"] = semantic_document_sha256(document)


def _walk_keys(value: object) -> tuple[str, ...]:
    keys: list[str] = []
    if type(value) is dict:
        for key, item in value.items():
            keys.append(key)
            keys.extend(_walk_keys(item))
    elif type(value) is list:
        for item in value:
            keys.extend(_walk_keys(item))
    return tuple(keys)


class CrrP1DecisionGoldenTests(unittest.TestCase):
    def test_manifest_seals_only_the_two_additive_goldens(self) -> None:
        manifest = load_decision_static_manifest()

        self.assertEqual(
            manifest["schema_version"],
            DECISION_STATIC_MANIFEST_SCHEMA_VERSION,
        )
        self.assertEqual(manifest["status"], FREEZE_STATUS)
        self.assertEqual(
            manifest["source_static_manifest_sha256"],
            SOURCE_STATIC_MANIFEST_SHA256,
        )
        self.assertEqual(
            set(manifest["files"]),
            {
                "fixtures/u64_decision_semantic_golden_v0.1.json",
                "fixtures/w64_decision_semantic_golden_v0.1.json",
            },
        )
        for relative_path, expected_sha256 in manifest["files"].items():
            raw = (ROOT / "benchmarks" / relative_path).read_bytes()
            self.assertEqual(sha256(raw).hexdigest(), expected_sha256)
            self.assertEqual(raw, canonical_json_bytes(json.loads(raw)))

    def test_u64_and_w64_load_exact_semantic_documents(self) -> None:
        expected = {
            "U64": {
                "role": "FORMAL_E2E_SEMANTIC",
                "timed": True,
                "long": "GLD   270226C00200000",
                "short": "GLD   270226C00236000",
                "long_delta": 554_798,
                "short_delta": 260_543,
                "net_delta": 294_255,
                "base": 1_153_300_000_000,
                "stress": 1_155_300_000_000,
                "width": 3_600_000_000_000,
                "cap": 2_446_700_000_000,
            },
            "W64": {
                "role": "UNTIMED_WARMUP_CONFORMANCE",
                "timed": False,
                "long": "GLD   270226C00200000",
                "short": "GLD   270226C00250000",
                "long_delta": 601_101,
                "short_delta": 254_075,
                "net_delta": 347_026,
                "base": 1_627_300_000_000,
                "stress": 1_629_300_000_000,
                "width": 5_000_000_000_000,
                "cap": 3_372_700_000_000,
            },
        }
        for fixture_id, facts in expected.items():
            with self.subTest(fixture_id=fixture_id):
                golden = load_decision_semantic_golden(fixture_id)
                document = golden.document
                projection = document["decision_projection"]
                selection = projection["selection"]
                cost = projection["entry_cost"]

                self.assertEqual(document["schema_version"], DECISION_GOLDEN_SCHEMA_VERSION)
                self.assertEqual(document["freeze_status"], FREEZE_STATUS)
                self.assertEqual(document["role"], facts["role"])
                self.assertIs(document["formal_timed_receipt_permitted"], facts["timed"])
                self.assertEqual(document["quantity"], 1)
                self.assertEqual(selection["long_call_id"], facts["long"])
                self.assertEqual(selection["short_call_id"], facts["short"])
                self.assertEqual(selection["long_delta_ppm"], facts["long_delta"])
                self.assertEqual(selection["short_delta_ppm"], facts["short_delta"])
                self.assertEqual(selection["net_delta_ppm"], facts["net_delta"])
                self.assertEqual(cost["base_net_debit_nano_usd"], facts["base"])
                self.assertEqual(cost["stressed_net_debit_nano_usd"], facts["stress"])
                self.assertEqual(cost["gross_expiry_width_value_nano_usd"], facts["width"])
                self.assertEqual(cost["max_loss_nano_usd"], facts["base"])
                self.assertEqual(
                    cost["theoretical_expiry_max_profit_nano_usd"],
                    facts["cap"],
                )
                self.assertIs(cost["theoretical_expiry_only"], True)
                self.assertEqual(
                    golden.projection_sha256,
                    sha256(
                        canonical_json_bytes(
                            _golden_document(fixture_id)["decision_projection"]
                        )
                    ).hexdigest(),
                )

    def test_lineage_binds_every_frozen_semantic_authority(self) -> None:
        required = {
            "source_static_manifest_sha256",
            "fixture_file_sha256",
            "call_golden_file_sha256",
            "corpus_semantic_sha256",
            "input_vector_sha256",
            "signal_snapshot_sha256",
            "option_snapshot_sha256",
            "candidate_ledger_sha256",
            "lc0_binding_sha256",
            "research_contract_sha256",
            "p1_contract_sha256",
            "rule_sha256",
            "delta_model_sha256",
            "quote_quality_policy_version",
            "quote_quality_policy_sha256",
            "fee_schedule_sha256",
            "rule_package_version",
        }
        for fixture_id in ("U64", "W64"):
            with self.subTest(fixture_id=fixture_id):
                document = load_decision_semantic_golden(fixture_id).document
                lineage = document["lineage"]
                self.assertEqual(set(lineage), required)
                self.assertEqual(
                    lineage["research_contract_sha256"],
                    "3940c27b4aec3585b9aa904a96f2118178589fafdcee46412441431ff9fcb8fc",
                )
                self.assertEqual(
                    lineage["p1_contract_sha256"],
                    "8191b769ecb222557eb0770f7e64eaf8bbcb35bf4a0d836ab91e52cc17c445d5",
                )
                self.assertEqual(
                    lineage["source_static_manifest_sha256"],
                    SOURCE_STATIC_MANIFEST_SHA256,
                )

    def test_call_terminals_are_the_exact_ordered_frozen_documents(self) -> None:
        for fixture_id in ("U64", "W64"):
            with self.subTest(fixture_id=fixture_id):
                document = load_decision_semantic_golden(fixture_id).document
                source = load_reference_golden(fixture_id)
                self.assertEqual(len(document["call_terminals"]), 64)
                self.assertEqual(
                    document["call_terminals"],
                    tuple(source["results"]),
                )
                self.assertEqual(
                    document["call_semantic_output_sha256"],
                    source["semantic_output_sha256"],
                )
                self.assertEqual(
                    tuple(item["ordinal"] for item in document["call_terminals"]),
                    tuple(range(1, 65)),
                )

    def test_projection_has_honest_non_actionable_terminal_states(self) -> None:
        for fixture_id in ("U64", "W64"):
            projection = load_decision_semantic_golden(fixture_id).document[
                "decision_projection"
            ]
            self.assertEqual(
                projection["carrier_terminals"],
                (
                    {
                        "carrier_id": "LC0",
                        "reason_code": "LC0_AUTHORITY_NOT_IMPLEMENTED",
                        "terminal_status": "NO_DECISION",
                    },
                    {
                        "carrier_id": "BCS0",
                        "reason_code": "RESEARCH_ONLY_NOT_ACTIONABLE",
                        "terminal_status": "PASS",
                    },
                ),
            )
            self.assertEqual(
                projection["decision"],
                {
                    "actionable": False,
                    "broker_order_count": 0,
                    "owner_selection_required": False,
                    "reason_code": "CARRIER_EVALUATION_INCOMPLETE",
                    "status": "NO_DECISION",
                },
            )

    def test_semantic_hash_covers_every_field_except_itself(self) -> None:
        document = _golden_document("U64")
        original_hash = document["decision_semantic_sha256"]
        self.assertEqual(original_hash, semantic_document_sha256(document))

        document["decision_projection"]["entry_cost"][
            "base_net_debit_nano_usd"
        ] += 1
        self.assertNotEqual(original_hash, semantic_document_sha256(document))

    def test_semantic_document_excludes_backend_and_artifact_provenance(self) -> None:
        banned = {
            "backend_evidence_sha256",
            "backend_input_sha256",
            "backend_execution_sha256",
            "backend_artifact_sha256",
            "bcs_selection_sha256",
            "evaluation_sha256",
            "artifact_sha256",
            "long_delta_evidence_sha256",
            "short_delta_evidence_sha256",
            "long_delta_run_sha256",
            "short_delta_run_sha256",
            "long_delta_runtime_fingerprint_sha256",
            "short_delta_runtime_fingerprint_sha256",
            "native_provenance_sha256",
            "execution_provenance_sha256",
        }
        for fixture_id in ("U64", "W64"):
            keys = set(_walk_keys(_golden_document(fixture_id)))
            self.assertTrue(keys.isdisjoint(banned))

    def test_reimplemented_projection_does_not_execute_bcs_decision_functions(self) -> None:
        with (
            patch(
                "gld_research_core.bcs.select_bcs_short_call",
                side_effect=AssertionError("decision code executed"),
            ),
            patch(
                "gld_research_core.bcs.price_bcs_entry",
                side_effect=AssertionError("decision code executed"),
            ),
            patch(
                "gld_research_core.bcs.build_decision_artifact",
                side_effect=AssertionError("decision code executed"),
            ),
        ):
            for fixture_id in ("U64", "W64"):
                self.assertEqual(
                    derive_decision_semantic_document(fixture_id)["fixture_id"],
                    fixture_id,
                )

    def test_tamper_reorder_count_and_numeric_bool_coercion_fail_closed(self) -> None:
        mutations = {
            "count": lambda doc: doc["call_terminals"].pop(),
            "reorder": lambda doc: doc["call_terminals"].reverse(),
            "terminal_float": lambda doc: doc["call_terminals"][0].__setitem__(
                "delta_ppm", float(doc["call_terminals"][0]["delta_ppm"])
            ),
            "quantity_bool": lambda doc: doc.__setitem__("quantity", True),
            "quantity_float": lambda doc: doc.__setitem__("quantity", 1.0),
            "owner_int": lambda doc: doc["decision_projection"]["decision"].__setitem__(
                "owner_selection_required", 0
            ),
            "orders_bool": lambda doc: doc["decision_projection"]["decision"].__setitem__(
                "broker_order_count", False
            ),
            "price_float": lambda doc: doc["decision_projection"]["entry_quotes"][
                "long"
            ].__setitem__("price_nano_usd", 17_540_000_000.0),
            "lineage": lambda doc: doc["lineage"].__setitem__(
                "fee_schedule_sha256", "0" * 64
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                document = deepcopy(_golden_document("U64"))
                mutate(document)
                _reseal(document)
                with self.assertRaises(DecisionContractError):
                    load_decision_semantic_golden("U64", document=document)

    def test_w64_is_structurally_barred_from_formal_timed_receipt(self) -> None:
        assert_formal_timed_receipt_permitted(
            load_decision_semantic_golden("U64")
        )
        with self.assertRaises(DecisionContractError) as raised:
            assert_formal_timed_receipt_permitted(
                load_decision_semantic_golden("W64")
            )
        self.assertEqual(
            raised.exception.reason_code,
            "DECISION_FORMAL_TIMED_ROLE_INVALID",
        )

    def test_backend_evidence_envelope_is_typed_separate_and_not_produced(self) -> None:
        semantic_sha = load_decision_semantic_golden(
            "U64"
        ).decision_semantic_sha256
        document = {
            "artifact_sha256": "d" * 64,
            "backend_evidence_sha256": "a" * 64,
            "execution_sha256": "c" * 64,
            "fixture_id": "U64",
            "input_sha256": "b" * 64,
            "schema_version": BACKEND_EVIDENCE_SCHEMA_VERSION,
            "semantic_sha256": semantic_sha,
        }
        envelope = validate_backend_evidence_envelope(document)
        self.assertIsInstance(envelope, BackendEvidenceEnvelopeV1)
        self.assertEqual(asdict(envelope), document)

        for field, invalid in (
            ("fixture_id", True),
            ("semantic_sha256", "f" * 64),
            ("backend_evidence_sha256", None),
            ("input_sha256", 2.0),
            ("execution_sha256", False),
            ("artifact_sha256", 0),
        ):
            with self.subTest(field=field):
                tampered = dict(document)
                tampered[field] = invalid
                with self.assertRaises(DecisionContractError):
                    validate_backend_evidence_envelope(tampered)

        for fixture_id in ("U64", "W64"):
            keys = set(_walk_keys(_golden_document(fixture_id)))
            self.assertNotIn(BACKEND_EVIDENCE_SCHEMA_VERSION, keys)
            self.assertNotIn(
                BACKEND_EVIDENCE_SCHEMA_VERSION,
                json.dumps(_golden_document(fixture_id), sort_keys=True),
            )

    def test_generator_is_offline_one_shot_and_refuses_reseal(self) -> None:
        source_before = SOURCE_STATIC_MANIFEST.read_bytes()
        with TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory)
            created = seal_decision_goldens(output_root=output_root)
            self.assertEqual(len(created), 3)
            for path in created:
                raw = path.read_bytes()
                self.assertEqual(raw, canonical_json_bytes(json.loads(raw)))
            with self.assertRaises(DecisionContractError) as raised:
                seal_decision_goldens(output_root=output_root)
            self.assertEqual(
                raised.exception.reason_code,
                "DECISION_RESEAL_REFUSED",
            )
        self.assertEqual(SOURCE_STATIC_MANIFEST.read_bytes(), source_before)
        self.assertEqual(
            sha256(source_before).hexdigest(),
            SOURCE_STATIC_MANIFEST_SHA256,
        )


if __name__ == "__main__":
    unittest.main()
