# GLD CRR Decision Semantic Late-Freeze Receipt v0.1

Created: 2026-08-29 00:58 CST

## 1. Status

```text
P0_CORRECTNESS_PASS
P1_NATIVE_FULL_CALL_SHADOW_VERIFIED
DECISION_SEMANTIC_GOLDENS_FROZEN
LATE_ADDITIVE_FREEZE_AFTER_NATIVE_PRICER_BEFORE_NATIVE_DECISION_LAYER
NATIVE_DECISION_PIPELINE_NOT_IMPLEMENTED
P1_PERFORMANCE_NOT_PASS
P2_SOURCE_NOT_IMPLEMENTED
NO_DECISION
```

This receipt freezes the backend-neutral decision semantics before implementation of the native BCS/Decision layer. It is additive and does not rewrite the original static manifest or claim that the complete DecisionArtifact golden was blind-sealed before the native pricer existed.

## 2. Source and artifact identity

```text
e2bc74f2a138d1fa7142713e24d2691b6499613fefecaae726f27b0798928c05  benchmarks/crr_p1_decision_contract.py
1bd68c15a210a41e1e03d034852b2f772e2fedc33c64feb715b53579703570e3  benchmarks/generate_crr_p1_decision_goldens.py
1b368edaf9bbd3ee13adbf25cc49b8bae7d86cae892ee13a617c6e0f9bb1f915  benchmarks/fixtures/u64_decision_semantic_golden_v0.1.json
cbaa77f225efe5b143d081a4bf4054c9a8b8e4193bd7f32617ca4635f04a41cf  benchmarks/fixtures/w64_decision_semantic_golden_v0.1.json
b001d080f15d3d65734c2bfecc3bc55c43709a7f181e0dae2f9868bc40f528f0  benchmarks/fixtures/decision_static_manifest_v0.1.json
714d4945fa99486a77b3dff73c78292ad6ef64c51ff0d09ec94d0c3906271deb  tests/test_crr_p1_decision_contract.py
```

The original `benchmarks/fixtures/static_manifest_v0.1.json` remains unchanged at `5d828faf9edd8f415409aab4997cba24c01584decce315f073491f1f2e4e5ba4`.

## 3. Frozen semantic identities

```text
U64 decision_semantic_sha256=7f18fa70313c0d8dfa672e62757d06c66e99a12a7a197eb6aaeb9ccdc3c60541
U64 projection_sha256=5dd66c686c0c7df9f05b63f5807a2b748f1a1f83f1b271c7fb76366e8132664c

W64 decision_semantic_sha256=e230592bf5325fac9942ea3c499de77a70663b8dd42909c956164fb7e09799c4
W64 projection_sha256=62d584710ff9608958ef199f8397c1c347632f08cfaafec958396d1eea80ebfd
```

U64 is the only `FORMAL_E2E_SEMANTIC` fixture. W64 is structurally limited to `UNTIMED_WARMUP_CONFORMANCE` and cannot produce a formal latency receipt.

## 4. Decision facts

### U64

```text
long=GLD   270226C00200000
short=GLD   270226C00236000
long/short/net delta_ppm=554798/260543/294255
base/stressed debit=1153300000000/1155300000000 nano-USD
gross width=3600000000000 nano-USD
max loss=1153300000000 nano-USD
theoretical expiry-only cap=2446700000000 nano-USD
```

### W64

```text
long=GLD   270226C00200000
short=GLD   270226C00250000
long/short/net delta_ppm=601101/254075/347026
base/stressed debit=1627300000000/1629300000000 nano-USD
gross width=5000000000000 nano-USD
max loss=1627300000000 nano-USD
theoretical expiry-only cap=3372700000000 nano-USD
```

Both goldens keep the current honest terminal state:

```text
LC0=NO_DECISION / LC0_AUTHORITY_NOT_IMPLEMENTED
BCS0=PASS / RESEARCH_ONLY_NOT_ACTIONABLE
overall=NO_DECISION / CARRIER_EVALUATION_INCOMPLETE
owner_selection_required=false
actionable=false
broker_order_count=0
```

## 5. Semantic boundary

The semantic document includes the exact ordered 64 Call terminals, winners, Delta facts, executable quote sides/sizes/ticks, fees, cost numerics, carrier states and overall Decision state. It binds the frozen fixture, contracts, rules, model, quote policy and fee schedule.

Backend-specific run/evidence/runtime hashes, native provenance and source `BcsSelection`/evaluation/artifact hashes are excluded from the cross-backend semantic hash. A separate typed `BackendEvidenceEnvelopeV1` schema is defined to bind those facts later; no backend envelope or performance receipt is produced here.

## 6. Verification

```text
worker focused + mapped regression: 16/16 PASS
integrator focused: 12/12 PASS in 10.987s
canonical JSON: PASS
compileall: PASS
git diff --check: PASS
scoped whitespace: PASS
```

Adversarial coverage rejects terminal reorder/count changes, lineage or semantic-field mutation, numeric bool/float coercion, backend fields inside the semantic document, and W64 formal-timed promotion. The one-shot offline generator refuses resealing existing files.

Independent code review: `APPROVED`, all severities = 0.

Code review: harness-native fallback — ce-code-review excludes untracked implementation files; dedicated code-reviewer reviewed explicit file set.

No provider, broker, private data, live quote, order, stage, commit, push, PR, deployment or publication occurred.
