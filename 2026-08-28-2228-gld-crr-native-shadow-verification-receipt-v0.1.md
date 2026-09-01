# GLD CRR Native Shadow Verification Receipt v0.1

Created: 2026-08-28 22:28 CST

## 1. Status

```text
P0_CORRECTNESS_PASS
P1_BENCHMARK_FOUNDATION_FROZEN
NATIVE_SHADOW_VERIFIED
NATIVE_NOT_CONNECTED_TO_AUTHORITY_PATH
P1_PERFORMANCE_NOT_PASS
P2_SOURCE_NOT_IMPLEMENTED
NO_DECISION
```

This receipt proves only the first native shadow kernel, offline build provenance and strict local loader. The existing `compute_american_call_delta`, `MODEL_SHA256`, BCS evidence and DecisionArtifact authority path were not modified or connected.

## 2. Source identity

```text
bacd7228201a94650f66d4dcc820d1a93c2aa303ad26b15ddb97fece73cd31d5  native/crr_tree_kernel_v1.c
9ebde69d221dceccdf51e9eee5170d6d224753ab729466aea40a18f27596a652  tools/build_crr_native.py
0da75ee5d45aad0fcdfbd1607a0071d3af202e3070f28b3d2bc4a46ef2a4902f  src/gld_research_core/native_tree.py
3d2a7920b19371fff191288a129575c187745cba8196b1a652a9e6d16f9704a1  tests/test_crr_native_tree.py
```

## 3. Built artifact identity

```text
build_input_sha256=b72a688d47a7a865d6c8e37fbf4003375e231b2fa68b176a463fb3e6f1a20cf1
binary_sha256=38ed9d73f8ca42bb8ce3735e8d96b46db1b1b26ac70ef9c146b52348a0e0c7d3
backend_evidence_sha256=02e88b883e8a0f4614ec00a4f08358caa14dc0c712124503395875890fbd1400
build_manifest_semantic_sha256=cc404643c9fc28ec5931bb061d9e3e1f1b8707e98e86b966476994e0bf29523c
manifest_file_sha256=f4611b5ff4e485a930f872b0381b602eb3c6a07d389366ca8668e400683a9153
compiler_executable_sha256=179301dcb41ea78accc3fa0048a7e6f6710d891945a751a34addd622020c1818
compiler_flags_sha256=04220af59c429c62541ecce2dce6d367d2565aa48090a286cdc520d43588bdad
dependency_manifest_sha256=7b104eb2dc2fe49283def0a4fa0e5c8cec285c6e4b477c4abeaea6a431c45f32
python_header_manifest_sha256=de1077b7f69d2769ece64459499e63c214a8ae62ab543e69bafd74fb3c7e8379
runtime_environment_sha256=fc644ef2020d4e52fee10f4bd5ae8779c83abd8bf10cd074b6ae0e15071f21cc
target_environment_sha256=639525491418919cf7198a29598797a4eeefc818a6b56954e9561dd9e5ab88ef
```

Build target: Apple M4 arm64, macOS target 26.0, CPython 3.14 SOABI `cpython-314-darwin`, Apple Clang with strict scalar IEEE binary64 flags. Build is explicit and offline; import never builds or replaces a binary. The binary lives under its build-input hash and is ignored by Git, while this receipt retains its exact identity.

## 4. Semantic verification

Independent differential review exercised 350 tree cases across steps 2 through 1025, including normal, early-exercise-heavy, invalid probability, numeric and boundary cases.

```text
status: exact match
price float.hex(): exact match
delta float.hex(): exact match
early_exercise_nodes: exact match
result: 350/350 PASS
```

Loader/adversarial coverage includes:

- exact ABI and identity;
- source, binary, flags and nested manifest tamper rejection;
- exact JSON types, including bool-vs-int rejection;
- caller cannot construct a verified wrapper;
- no auto-build or reference fallback;
- malformed/unknown native status rejection;
- ambient rounding-mode drift fails closed;
- observed GIL release for concurrent Calls.

## 5. Test and review evidence

```text
native focused: 13/13 PASS
native + CRR focused: 39/39 PASS
fresh full suite: 160/160 PASS in 350.263s
compileall: PASS
Clang static analyze: PASS
git diff --check: PASS
```

Independent native code review found and then closed two loader blockers: direct construction of a public verified wrapper and nested JSON exact-type ambiguity. Final review: `APPROVED`, P0/P1/P2 findings = 0.

Code review: harness-native fallback — ce-code-review excludes untracked implementation files; dedicated code-reviewer reviewed explicit file set.

## 6. Shadow microbenchmark

100-sample single-tree nearest-rank P95 on the current Apple M4 host:

```text
steps=512  native=0.143542ms  reference=14.075666ms  speedup=98.06x
steps=1024 native=0.558583ms  reference=58.387250ms  speedup=104.53x
```

These are tree-kernel microbenchmarks, not Single-Call, Batch-64 or quote-to-artifact P95. They do not authorize a P1 pass.

## 7. Remaining gates

- exact-64 batch result and ordered semantic hash;
- same-process verified Delta evidence, BCS selection and DecisionArtifact;
- isolated child process and canonical parent terminal envelope;
- 5-second T1 kill/reap/late-result suppression;
- H16 reference holdout sealed after native source freeze;
- U64/E1/H16 full semantic differential;
- two fresh runner processes passing Single, Batch and E2E P95;
- final native backend execution provenance promotion.

No provider, broker, private data, order, stage, commit, push, PR, deployment or publication occurred.
