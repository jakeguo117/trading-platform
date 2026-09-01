# GLD CRR Exact-64 and H16 Verification Receipt v0.1

Created: 2026-08-28 23:08 CST

## 1. Status

```text
P0_CORRECTNESS_PASS
P1_BENCHMARK_FOUNDATION_FROZEN
EXACT_64_BATCH_SEMANTICS_VERIFIED
H16_REFERENCE_HOLDOUT_VERIFIED
NATIVE_FULL_CALL_NOT_CONNECTED
P1_PERFORMANCE_NOT_PASS
P2_SOURCE_NOT_IMPLEMENTED
NO_DECISION
```

This receipt closes only the exact-64 orchestration semantics and the untimed H16 reference holdout. It does not prove native full-Call integration, quote-to-DecisionArtifact latency, the 5-second watchdog, or any P1 latency SLO.

## 2. Source and sealed artifact identity

```text
641ed0b0db79c97a13b0f56cf105bbebad27859e5be171735ced9d4638244616  src/gld_research_core/crr_batch.py
6b215497711fe9debef2c0c61316d2a742e7b8f57d9fe3864807666f0c89533b  tests/test_crr_batch.py
94f0fc9ff63be82e661f0d20165524e93e5eef6dd3cdfc945e5bea22c1ccf6d8  benchmarks/h16_contract.py
bff3e0647c930ec84b6ef3b9b6d6a2abadd5906eea7c29e20a70e8230470b04a  benchmarks/generate_h16_holdout.py
3ca26abb8dd0df28af2af129069559a49ed5d215a1d444370b73e0bf4808377c  benchmarks/holdout/h16_inputs_v0.1.json
609a1a3ab847b9947421faf069f4f962cf95906d33f7a6c176d8e4ab73128996  benchmarks/holdout/h16_reference_golden_v0.1.json
737cef587b07803fe901ec8f8d64b211ddb8f9df5859599db14dd20e24c350ef  benchmarks/holdout/h16_manifest_v0.1.json
74ab98c6cf2bb3cb1655cde68be48a9f9b8a59345852c78f44b267e4c139fce0  tests/test_crr_h16_holdout.py
bacd7228201a94650f66d4dcc820d1a93c2aa303ad26b15ddb97fece73cd31d5  native/crr_tree_kernel_v1.c
```

H16 semantic output SHA-256 remains `81be53ee8640a249a9f6e0f93ba7f85c766fb246e1026202848e193b9c60958a`. The corpus contains 16 unique inputs, has no U64/W64 input-hash or core-economic-tuple overlap, and remains explicitly `H16_NOT_TIMED`.

## 3. Exact-64 evidence

- exactly 64 unique, canonically ordered Calls from one snapshot;
- fixed 8-worker orchestration, all 64 started and collected, no cache or pruning;
- parallel failures resolve to the minimum canonical ordinal;
- ordered semantic output matches the frozen U64 golden;
- receipt verification is process-local and binds the originating runner, backend evidence, full receipt payload, all ordered terminal identities and PASS evidence, input-vector hash, semantic hash and provenance hash;
- direct construction, replace, shallow/deep copy, pickle, same-backend cross-runner use, terminal identity substitution, public provenance recomputation, backend reattachment and in-place mutation all fail verification.

Independent focused review: `APPROVED`, P0/P1/P2 findings = 0.

## 4. H16 evidence

- reference replay: 16/16 terminal, zero cache hits;
- result composition: 12 PASS and 4 FAIL (`TREE_NOT_CONVERGED` x2, `IV_UNIDENTIFIABLE` x1, `IV_NO_BRACKET` x1);
- early exercise covers both `true` and `false`;
- exact JSON types are enforced for manifest, corpus, nested coverage facts, golden counts and reference receipt;
- each golden result is bound to the corresponding corpus `ordinal`, `input_sha256` and `contract_id`;
- bool/int and int/float coercion, contract tamper with recomputed semantic hash, reorder, count change and fake hash all fail closed;
- sealed JSON, generator, native source and frozen benchmark contract were not changed during the correction.

Independent focused review: `APPROVED`, P0/P1/P2 findings = 0.

## 5. Integrator verification

```text
python3 -m unittest tests.test_crr_h16_holdout tests.test_crr_batch -v
Ran 26 tests in 25.873s
OK

compileall: PASS
git diff --check: PASS
```

Code review: harness-native fallback — ce-code-review excludes untracked implementation files; dedicated code-reviewer reviewed explicit file set.

## 6. Remaining P1 gates

- connect the verified native kernel to one full Call while preserving every semantic terminal field;
- connect that engine to exact-64 and pass U64/E1/H16 differential checks;
- implement the same-process verified BCS/DecisionArtifact shadow path;
- isolate the complete invocation in a child process and pass 5-second kill/reap/late-result suppression;
- pass two fresh-runner Single, Batch-64 and E2E P95 runs with raw samples and backend provenance.

No provider, broker, private data, live quote, order, stage, commit, push, PR, deployment or publication occurred.
