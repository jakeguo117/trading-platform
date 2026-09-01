from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from gld_simulation.bundle import (
    MAX_RAW_DOCUMENTS_CANONICAL_BYTES,
    RAW_DOCUMENT_TOP_LEVEL_KEYS,
    RawBundle,
    load_raw_bundle,
)
from gld_simulation.canonical import (
    MAX_CANONICAL_DEPTH,
    MAX_CANONICAL_INTEGER_DIGITS,
    MAX_CANONICAL_KEY_CHARS,
    MAX_CANONICAL_NODES,
    MAX_CANONICAL_STRING_CHARS,
    canonical_json_bytes,
    parse_canonical_json,
)
from gld_simulation.errors import RawBundleError


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "gld_simulation" / "v1"
SCENARIOS = ("pass", "no_action", "no_decision")
EXPECTED_LOGICAL_KEYS = {
    "rule_package", "calendar", "daily_bars", "minute_bars",
    "market_status", "option_contracts", "market_quotes", "pit_inputs",
    "fee_schedule", "account_snapshot_a", "frozen_kelly_receipt",
}


def _write_canonical_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _load_manifest(root: Path) -> dict[str, object]:
    return json.loads((root / "manifest.json").read_bytes())


def _rewrite_manifest(root: Path, manifest: dict[str, object]) -> None:
    _write_canonical_json(root / "manifest.json", manifest)


def _copy_fixture(parent: Path, scenario: str = "pass") -> Path:
    destination = parent / scenario
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE_ROOT / scenario, destination)
    return destination


def _entry(manifest: dict[str, object], logical_key: str) -> dict[str, object]:
    files = manifest["files"]
    if type(files) is not list:
        raise AssertionError("manifest files must be a list")
    for value in files:
        if type(value) is dict and value.get("logical_key") == logical_key:
            return value
    raise AssertionError(f"missing manifest entry: {logical_key}")


def _rewrite_raw_document(
    root: Path,
    manifest: dict[str, object],
    logical_key: str,
    payload: dict[str, object],
) -> None:
    file_entry = _entry(manifest, logical_key)
    raw_path = root / str(file_entry["path"])
    encoded = canonical_json_bytes(payload) + b"\n"
    raw_path.write_bytes(encoded)
    file_entry["byte_size"] = len(encoded)
    file_entry["sha256"] = sha256(encoded).hexdigest()
    _rewrite_manifest(root, manifest)


class GldSimulationRawBundleTests(unittest.TestCase):
    def test_canonical_json_is_stable_and_rejects_ambiguous_numbers(self) -> None:
        self.assertEqual(
            canonical_json_bytes({"z": 1, "a": [True, None, "GLD"]}),
            b'{"a":[true,null,"GLD"],"z":1}',
        )
        with self.assertRaisesRegex(RawBundleError, "CANONICAL_JSON_FLOAT_FORBIDDEN"):
            canonical_json_bytes({"price": 200.5})
        with self.assertRaisesRegex(RawBundleError, "CANONICAL_JSON_KEY_INVALID"):
            canonical_json_bytes({1: "not a JSON object key"})  # type: ignore[dict-item]

    def test_canonical_json_resource_limits_have_stable_reason_codes(self) -> None:
        too_deep: object = None
        for _ in range(MAX_CANONICAL_DEPTH + 1):
            too_deep = [too_deep]
        cases = (
            (
                lambda: canonical_json_bytes(too_deep),
                "CANONICAL_JSON_DEPTH_LIMIT_EXCEEDED",
            ),
            (
                lambda: canonical_json_bytes([None] * MAX_CANONICAL_NODES),
                "CANONICAL_JSON_NODE_LIMIT_EXCEEDED",
            ),
            (
                lambda: canonical_json_bytes("x" * (MAX_CANONICAL_STRING_CHARS + 1)),
                "CANONICAL_JSON_STRING_LIMIT_EXCEEDED",
            ),
            (
                lambda: canonical_json_bytes(
                    {"k" * (MAX_CANONICAL_KEY_CHARS + 1): None}
                ),
                "CANONICAL_JSON_KEY_LENGTH_LIMIT_EXCEEDED",
            ),
            (
                lambda: canonical_json_bytes(10**MAX_CANONICAL_INTEGER_DIGITS),
                "CANONICAL_JSON_INTEGER_DIGITS_LIMIT_EXCEEDED",
            ),
        )
        for invoke, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(
                RawBundleError, reason
            ):
                invoke()

    def test_canonical_json_runtime_resource_errors_map_to_raw_bundle_error(self) -> None:
        with mock.patch(
            "gld_simulation.canonical.json.dumps", side_effect=MemoryError
        ), self.assertRaisesRegex(
            RawBundleError, "CANONICAL_JSON_RESOURCE_LIMIT_EXCEEDED"
        ):
            canonical_json_bytes({"ok": True})
        with mock.patch(
            "gld_simulation.canonical.json.loads", side_effect=RecursionError
        ), self.assertRaisesRegex(
            RawBundleError, "CANONICAL_JSON_DEPTH_LIMIT_EXCEEDED"
        ):
            parse_canonical_json(b"{}\n", source="resource-test.json")

    def test_committed_raw_fixtures_are_self_contained_and_answer_free(self) -> None:
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                bundle = load_raw_bundle(FIXTURE_ROOT / scenario)
                self.assertIsInstance(bundle, RawBundle)
                self.assertEqual(bundle.classification, "SYNTHETIC_FIXTURE_ONLY")
                self.assertEqual(set(bundle.files), EXPECTED_LOGICAL_KEYS)
                contracts = bundle.read_json("option_contracts")
                quotes = bundle.read_json("market_quotes")
                self.assertIs(type(contracts), dict)
                self.assertIs(type(quotes), dict)
                self.assertEqual(len(contracts["contracts"]), 64)
                self.assertEqual(len(quotes["option_bbo"]), 64)
                self.assertEqual(
                    bundle.manifest_sha256,
                    sha256(
                        (FIXTURE_ROOT / scenario / "manifest.json")
                        .read_bytes().removesuffix(b"\n")
                    ).hexdigest(),
                )
                self.assertEqual(len(bundle.raw_content_sha256), 64)
                self.assertEqual(set(bundle.as_document()), EXPECTED_LOGICAL_KEYS)
                for logical_key, document in bundle.as_document().items():
                    self.assertIs(type(document), dict)
                    self.assertEqual(
                        set(document), RAW_DOCUMENT_TOP_LEVEL_KEYS[logical_key]
                    )

    def test_raw_content_hash_does_not_bind_absolute_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = load_raw_bundle(FIXTURE_ROOT / "pass")
            second = load_raw_bundle(_copy_fixture(Path(temporary)))
            self.assertEqual(first.raw_content_sha256, second.raw_content_sha256)
            self.assertEqual(first.manifest_sha256, second.manifest_sha256)

    def test_file_integrity_failures_are_rejected(self) -> None:
        for mutation, reason in (
            ("tamper", "RAW_FILE_SIZE_MISMATCH"),
            ("unregistered", "RAW_BUNDLE_UNREGISTERED_FILE"),
            ("symlink", "RAW_BUNDLE_SYMLINK_FORBIDDEN"),
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = _copy_fixture(Path(temporary))
                if mutation == "tamper":
                    (root / "control" / "rule-package.json").write_bytes(b"{}\n")
                elif mutation == "unregistered":
                    (root / "surprise.json").write_bytes(b"{}\n")
                else:
                    (root / "linked.json").symlink_to(root / "control" / "rule-package.json")
                with self.assertRaisesRegex(RawBundleError, reason):
                    load_raw_bundle(root)

    def test_manifest_paths_must_be_normalized_relative_paths(self) -> None:
        for unsafe_path in (
            "../outside.json", "/tmp/outside.json", "a/../b.json",
            "bad\\path.json", "bad\u0000path.json",
        ):
            with self.subTest(unsafe_path=unsafe_path), tempfile.TemporaryDirectory() as temporary:
                root = _copy_fixture(Path(temporary))
                manifest = _load_manifest(root)
                files = manifest["files"]
                self.assertIs(type(files), list)
                files[0]["path"] = unsafe_path
                _rewrite_manifest(root, manifest)
                with self.assertRaisesRegex(RawBundleError, "RAW_MANIFEST_PATH_INVALID"):
                    load_raw_bundle(root)

    def test_manifest_requires_exact_size_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = _copy_fixture(base / "first")
            manifest = _load_manifest(root)
            file_entry = _entry(manifest, "rule_package")
            file_entry["byte_size"] = int(file_entry["byte_size"]) + 1
            _rewrite_manifest(root, manifest)
            with self.assertRaisesRegex(RawBundleError, "RAW_FILE_SIZE_MISMATCH"):
                load_raw_bundle(root)
            root = _copy_fixture(base / "second")
            manifest = _load_manifest(root)
            _entry(manifest, "rule_package")["sha256"] = "0" * 64
            _rewrite_manifest(root, manifest)
            with self.assertRaisesRegex(RawBundleError, "RAW_FILE_SHA256_MISMATCH"):
                load_raw_bundle(root)

    def test_recursive_derived_answer_fields_are_rejected(self) -> None:
        for forbidden_key in (
            "signal_state", "winner", "quantity", "DecisionResult",
            "decision_result", "expected_result", "expected_decision_result_v1",
            "precomputed_signal_state",
        ):
            with self.subTest(
                forbidden_key=forbidden_key
            ), tempfile.TemporaryDirectory() as temporary:
                root = _copy_fixture(Path(temporary))
                manifest = _load_manifest(root)
                file_entry = _entry(manifest, "account_snapshot_a")
                raw_path = root / str(file_entry["path"])
                payload = json.loads(raw_path.read_bytes())
                positions = payload["positions"]
                self.assertIs(type(positions), list)
                positions.append(
                    {"still_nested": [{forbidden_key: "PRECOMPUTED"}]}
                )
                encoded = canonical_json_bytes(payload) + b"\n"
                raw_path.write_bytes(encoded)
                file_entry["byte_size"] = len(encoded)
                file_entry["sha256"] = sha256(encoded).hexdigest()
                _rewrite_manifest(root, manifest)
                with self.assertRaisesRegex(
                    RawBundleError, "RAW_DERIVED_ANSWER_FIELD_FORBIDDEN"
                ):
                    load_raw_bundle(root)

    def test_missing_business_fact_is_valid_raw_bundle_not_corruption(self) -> None:
        bundle = load_raw_bundle(FIXTURE_ROOT / "no_decision")
        account = bundle.read_json("account_snapshot_a")
        self.assertIs(type(account), dict)
        self.assertIsNone(account["settled_cash_nano_usd"])
        self.assertEqual(bundle.as_document()["account_snapshot_a"], account)

    def test_raw_document_schema_and_classification_are_bound(self) -> None:
        for field, replacement, reason in (
            ("schema_version", "SIM_WRONG_SCHEMA_V1", "RAW_FILE_SCHEMA_MISMATCH"),
            ("classification", "REAL_MARKET_DATA", "RAW_FILE_CLASSIFICATION_INVALID"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = _copy_fixture(Path(temporary))
                manifest = _load_manifest(root)
                file_entry = _entry(manifest, "rule_package")
                raw_path = root / str(file_entry["path"])
                payload = json.loads(raw_path.read_bytes())
                payload[field] = replacement
                encoded = canonical_json_bytes(payload) + b"\n"
                raw_path.write_bytes(encoded)
                file_entry["byte_size"] = len(encoded)
                file_entry["sha256"] = sha256(encoded).hexdigest()
                _rewrite_manifest(root, manifest)
                with self.assertRaisesRegex(RawBundleError, reason):
                    load_raw_bundle(root)

    def test_manifest_and_document_cannot_jointly_claim_unknown_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _copy_fixture(Path(temporary))
            manifest = _load_manifest(root)
            file_entry = _entry(manifest, "rule_package")
            raw_path = root / str(file_entry["path"])
            payload = json.loads(raw_path.read_bytes())
            payload["schema_version"] = "SIM_GLD_RULE_PACKAGE_V999"
            file_entry["schema"] = "SIM_GLD_RULE_PACKAGE_V999"
            _rewrite_raw_document(root, manifest, "rule_package", payload)
            with self.assertRaisesRegex(
                RawBundleError, "RAW_MANIFEST_FILE_SCHEMA_UNSUPPORTED"
            ):
                load_raw_bundle(root)

    def test_each_raw_document_has_an_exact_top_level_key_set(self) -> None:
        for mutation in ("extra", "missing"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = _copy_fixture(Path(temporary))
                manifest = _load_manifest(root)
                file_entry = _entry(manifest, "daily_bars")
                raw_path = root / str(file_entry["path"])
                payload = json.loads(raw_path.read_bytes())
                if mutation == "extra":
                    payload["unexpected_raw_fact"] = "not-derived-but-unsupported"
                else:
                    del payload["underlying"]
                _rewrite_raw_document(root, manifest, "daily_bars", payload)
                with self.assertRaisesRegex(
                    RawBundleError, "RAW_FILE_DOCUMENT_KEYS_INVALID"
                ):
                    load_raw_bundle(root)

    def test_canonical_raw_documents_fit_supervisor_request_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _copy_fixture(Path(temporary))
            manifest = _load_manifest(root)
            file_entry = _entry(manifest, "daily_bars")
            raw_path = root / str(file_entry["path"])
            payload = json.loads(raw_path.read_bytes())
            payload["underlying"] = "G" * MAX_RAW_DOCUMENTS_CANONICAL_BYTES
            _rewrite_raw_document(root, manifest, "daily_bars", payload)
            with self.assertRaisesRegex(
                RawBundleError,
                "RAW_DOCUMENTS_SUPERVISOR_SIZE_LIMIT_EXCEEDED",
            ):
                load_raw_bundle(root)

    def test_manifest_schema_and_file_set_are_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = _copy_fixture(Path(temporary))
            manifest = _load_manifest(root)
            files = manifest["files"]
            self.assertIs(type(files), list)
            duplicate = deepcopy(files[0])
            duplicate["logical_key"] = "another_rule_package"
            files.append(duplicate)
            _rewrite_manifest(root, manifest)
            with self.assertRaisesRegex(RawBundleError, "RAW_MANIFEST_PATH_DUPLICATE"):
                load_raw_bundle(root)


if __name__ == "__main__":
    unittest.main()
