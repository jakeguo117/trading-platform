# GLD CRR Native Full-Call Verification Receipt v0.1

Created: 2026-08-29 00:22 CST

## 1. Status

```text
P0_CORRECTNESS_PASS
P1_BENCHMARK_FOUNDATION_FROZEN
NATIVE_FULL_CALL_SHADOW_VERIFIED
NATIVE_EXACT_64_SHADOW_VERIFIED
E1_MAPPED_TESTS_GREEN
FULL_NATIVE_E1_DIFFERENTIAL_PENDING
P1_PERFORMANCE_NOT_PASS
P2_SOURCE_NOT_IMPLEMENTED
NO_DECISION
```

This receipt proves that the loader-verified native tree can execute the complete frozen Call IV/Delta orchestration and exact-64 batch without changing backend-independent semantics. It does not prove quote-to-DecisionArtifact E2E, T1 timeout cleanup, latency SLOs, provider qualification or trading authority.

## 2. Source identity

```text
ed7e389fa97a77811567691bead512f4ad3eae833ac4225b36b27e49104578bf  src/gld_research_core/native_tree.py
6c67a277b9a61a122c015c1b44fa6ac84b7975fca8a97bc0f0b35f6770188ac2  tests/test_crr_native_tree.py
606a8628c60994c84c0b7c3ca7971c90dade21812c104157d1fe5499643c9212  src/gld_research_core/native_crr_delta.py
77415d8c2ac7bfa7e5d1bc8ce5506472163d3c3d35c4169abe4a23a486e44c9a  tests/test_native_crr_delta.py
641ed0b0db79c97a13b0f56cf105bbebad27859e5be171735ced9d4638244616  src/gld_research_core/crr_batch.py
6b215497711fe9debef2c0c61316d2a742e7b8f57d9fe3864807666f0c89533b  tests/test_crr_batch.py
```

## 3. Execution provenance

```text
combined_backend_evidence_sha256=e3d8edc1c0537db2d6eab7c004d33bc4dedfc319e5348679ffb9a3a52e913bd7
native_loader_verifier_code_sha256=d82da25362ed208448bf4c1cccda57e468e856a64f2c269844beab9356cfe020
u64_semantic_output_sha256=388e4cdc5847635bfdb57cded4893d1518c5b1f6a41d7ecfd48e814cfdfaafdc
```

`MODEL_SHA256` remains the frozen semantic-model identity. The combined backend evidence separately binds the native orchestrator and loader sources, loader verifier/trust provenance, native build and manifest evidence, ABI and binary dependencies, native runtime environment, and Python runtime fingerprint. The reference Python source is recorded as the semantic oracle and is explicitly not claimed as the executed pricing source.

## 4. Semantic evidence

- U64: all 64 native terminals match every frozen reference field exactly;
- H16: all 16 native terminals match, including 12 PASS, 4 expected FAIL reasons and both early-exercise states;
- exact-64: ordered terminal vector and semantic hash equal the frozen U64 golden; batch receipt verification is true;
- the 13 E1 mapped tests are green, including the separately mapped BCS selector instability test;
- E1 has no executable corpus/golden replay API, so `FULL_NATIVE_E1_DIFFERENTIAL` remains pending and is not inferred from the mapped tests.

No CRR steps, candidates, tolerances or failure semantics were reduced or bypassed. There is no broker-Delta fallback.

## 5. Integrity corrections closed

- loader trust is held by the successful load path and no longer depends on the diagnostic `_LOADED_KERNEL` global;
- exact-type kernel clones, global reattachment, copy/deepcopy/pickle, field or native function changes and manifest mutation fail verification;
- full-Call result construction, copies, serialization and cross-factory use fail process-local verification;
- frozen private constants, callable code/defaults and closure coverage are checked before result registration;
- empty, duplicate, partial or copied guard registries cannot conceal a changed numeric result under unchanged backend evidence;
- loader revocation is sticky for the affected process.

## 6. Verification and review

```text
post-fix full regression: 70/70 PASS in 125.845s
integrator focused regression: 43/43 PASS in 14.229s
compileall: PASS
git diff --check: PASS
scoped whitespace: PASS
```

Independent final code review reran the prior bypass and clone/reattachment probes, U64/H16 differential and exact-64 receipt. Verdict: `APPROVED`, P0/P1/P2 findings = 0.

Code review: harness-native fallback — ce-code-review excludes untracked implementation files; dedicated code-reviewer reviewed explicit file set.

## 7. Remaining P1 gates

- create one backend-neutral, same-process BCS and DecisionArtifact shadow semantic path;
- run the complete invocation in a sealed child process and accept only one canonical terminal envelope;
- implement 5-second timeout kill/reap, late-result suppression and cleanup evidence;
- finish the full native E1 gate or formally replace the non-executable mapping with a sealed replayable corpus;
- complete two fresh-runner Single, Batch-64 and E2E P95 qualification runs with raw samples.

No provider, broker, private data, live quote, order, stage, commit, push, PR, deployment or publication occurred.
