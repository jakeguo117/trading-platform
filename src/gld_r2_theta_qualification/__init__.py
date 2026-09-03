"""Stable research-only foundation API for GLD R2 theta qualification."""

from .contracts import (
    CodeRuntimeBindingV1,
    R1FixedPolicyProjectionV1,
    R2QualificationError,
    build_r1_fixed_policy_projection,
    canonical_json_bytes,
    canonical_json_sha256,
    canonical_sha256,
    validate_r1_fixed_policy_projection,
)
from .registry import (
    R2CandidateRegistryV1,
    R2CandidateV1,
    build_r2_candidate_registry,
    validate_r2_candidate_registry,
)
from .windows import (
    R2EntryLabelWindowsV1,
    assess_common_mask,
    assert_joint_rows_evaluable,
    build_r2_entry_label_windows,
)


__all__ = [
    "CodeRuntimeBindingV1",
    "R1FixedPolicyProjectionV1",
    "R2CandidateRegistryV1",
    "R2CandidateV1",
    "R2EntryLabelWindowsV1",
    "R2QualificationError",
    "assess_common_mask",
    "assert_joint_rows_evaluable",
    "build_r1_fixed_policy_projection",
    "build_r2_candidate_registry",
    "build_r2_entry_label_windows",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "canonical_sha256",
    "validate_r1_fixed_policy_projection",
    "validate_r2_candidate_registry",
]
