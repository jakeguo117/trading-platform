from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import replace
import json
import pickle
import unittest

from gld_r2_theta_qualification.cli import build_acceptance_artifacts
from gld_r2_theta_qualification.contracts import (
    R2QualificationError,
    canonical_json_bytes,
    canonical_sha256,
)
from gld_r2_theta_qualification.publish import (
    R2SyntheticAcceptanceManifestV1,
    validate_synthetic_acceptance_manifest,
)
from gld_r2_theta_qualification.render_html import (
    render_engineering_evidence,
    render_owner_acceptance_index,
)


def _reseal(document: dict[str, object]) -> dict[str, object]:
    unsigned = dict(document)
    unsigned.pop("manifest_sha256")
    document["manifest_sha256"] = canonical_sha256(unsigned)
    return document


def _replaced_with_document(
    manifest: R2SyntheticAcceptanceManifestV1,
    document: dict[str, object],
) -> R2SyntheticAcceptanceManifestV1:
    sealed = _reseal(document)
    return replace(
        manifest,
        manifest_sha256=str(sealed["manifest_sha256"]),
        _canonical_bytes=canonical_json_bytes(sealed),
    )


class R2ManifestIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        artifacts = build_acceptance_artifacts()
        cls.document = json.loads(artifacts["acceptance-manifest.json"])

    def _manifest(self) -> R2SyntheticAcceptanceManifestV1:
        return validate_synthetic_acceptance_manifest(deepcopy(self.document))

    def _assert_both_renderers_reject(
        self,
        manifest: R2SyntheticAcceptanceManifestV1,
        reason_code: str,
    ) -> None:
        for renderer in (
            render_owner_acceptance_index,
            render_engineering_evidence,
        ):
            with self.subTest(renderer=renderer.__name__):
                with self.assertRaises(R2QualificationError) as caught:
                    renderer(manifest)
                self.assertEqual(caught.exception.reason_code, reason_code)

    def test_exact_dataclass_replace_cannot_reuse_instance_bound_seal(self) -> None:
        replaced = replace(self._manifest())

        with self.assertRaises(R2QualificationError) as caught:
            _ = replaced.document
        self.assertEqual(
            caught.exception.reason_code,
            "R2_ACCEPTANCE_MANIFEST_INTEGRITY_MISMATCH",
        )
        self._assert_both_renderers_reject(
            replaced,
            "R2_ACCEPTANCE_MANIFEST_INTEGRITY_MISMATCH",
        )

    def test_copy_and_deepcopy_reuse_the_exact_immutable_instance(self) -> None:
        manifest = self._manifest()

        self.assertIs(copy(manifest), manifest)
        self.assertIs(deepcopy(manifest), manifest)

    def test_pickle_cannot_reconstruct_a_new_seal_owner_cycle(self) -> None:
        with self.assertRaisesRegex(
            TypeError,
            "validated acceptance manifests cannot be pickled",
        ):
            pickle.dumps(self._manifest())

    def test_replace_cannot_render_promotable_count_16(self) -> None:
        forged_document = deepcopy(self.document)
        flow = forged_document["candidate_flow"]
        assert type(flow) is dict
        flow["promotable_count"] = 16
        forged = _replaced_with_document(self._manifest(), forged_document)

        self._assert_both_renderers_reject(
            forged,
            "R2_ACCEPTANCE_CANDIDATE_FLOW_INVALID",
        )

    def test_replace_cannot_render_forged_owner_status(self) -> None:
        forged_document = deepcopy(self.document)
        forged_document["owner_status"] = "OWNER_APPROVED_R2_RESEARCH_THETA"
        forged = _replaced_with_document(self._manifest(), forged_document)

        self._assert_both_renderers_reject(
            forged,
            "R2_ACCEPTANCE_MANIFEST_SEMANTICS_INVALID",
        )

    def test_replace_cannot_render_forged_scenario_owner_status(self) -> None:
        forged_document = deepcopy(self.document)
        scenarios = forged_document["scenarios"]
        assert type(scenarios) is list and type(scenarios[0]) is dict
        scenarios[0]["owner_status"] = "OWNER_ACCEPTED_COMPLETE"
        forged = _replaced_with_document(self._manifest(), forged_document)

        self._assert_both_renderers_reject(
            forged,
            "R2_ACCEPTANCE_SCENARIO_SEMANTICS_INVALID",
        )

    def test_document_read_rejects_noncanonical_bytes(self) -> None:
        manifest = self._manifest()
        replaced = replace(
            manifest,
            _canonical_bytes=b" " + manifest._canonical_bytes,
        )

        with self.assertRaises(R2QualificationError) as caught:
            _ = replaced.document
        self.assertEqual(
            caught.exception.reason_code,
            "R2_ACCEPTANCE_MANIFEST_INTEGRITY_MISMATCH",
        )

    def test_document_read_rejects_manifest_field_hash_mismatch(self) -> None:
        manifest = self._manifest()
        replaced = replace(manifest, manifest_sha256="0" * 64)

        with self.assertRaises(R2QualificationError) as caught:
            _ = replaced.document
        self.assertEqual(
            caught.exception.reason_code,
            "R2_ACCEPTANCE_MANIFEST_INTEGRITY_MISMATCH",
        )

    def test_manifest_seal_fields_cannot_be_rebound_by_assignment(self) -> None:
        manifest = self._manifest()
        forged_document = deepcopy(self.document)
        scenarios = forged_document["scenarios"]
        assert type(scenarios) is list and type(scenarios[0]) is dict
        scenarios[0]["input_sha256"] = "0" * 64
        forged = _replaced_with_document(manifest, forged_document)

        attempted_values = {
            "_owner": forged,
            "_canonical_bytes": forged._canonical_bytes,
            "_manifest_sha256": forged.manifest_sha256,
        }
        for name, value in attempted_values.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    AttributeError,
                    "manifest seals are immutable",
                ):
                    setattr(forged._seal, name, value)

        with self.assertRaises(R2QualificationError) as caught:
            _ = forged.document
        self.assertEqual(
            caught.exception.reason_code,
            "R2_ACCEPTANCE_MANIFEST_INTEGRITY_MISMATCH",
        )
        self._assert_both_renderers_reject(
            forged,
            "R2_ACCEPTANCE_MANIFEST_INTEGRITY_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()
