# GLD CRR P1 Pre-Optimization Freeze Receipt v0.1

Created: 2026-08-28 21:53 CST

## 1. Status

```text
P0_CORRECTNESS_PASS
P1_BENCHMARK_FOUNDATION_FROZEN
NATIVE_SHADOW_NEXT
P1_PERFORMANCE_NOT_PASS
P2_SOURCE_NOT_IMPLEMENTED
NO_DECISION
```

本 receipt 只证明正式性能优化前的 workload、reference semantics、runner/schema 和资格边界已经冻结。它不证明任何 P1 latency SLO、T1 watchdog、H16 holdout、native backend、实时行情或交易能力。

## 2. Contract and runner identity

```text
8191b769ecb222557eb0770f7e64eaf8bbcb35bf4a0d836ab91e52cc17c445d5  2026-08-28-2046-gld-crr-p1-benchmark-contract-v0.1.md
b648f87b15dddc3d180a2af55ef54761f0723316926950bee2e1e43dc961ba6a  benchmarks/__init__.py
0849e5c1883a85272658084a219d7501581e520bd93af07ad8263b852b575c33  benchmarks/crr_p1_contract.py
7f6208e891eb4cdd8d99523694a17f05e5356e0360cf1ddc8e3c00d99d862d24  benchmarks/generate_crr_p1_fixtures.py
a57627d703713f75a1c199f4d515c54c0acee247c2225e8b3e3c70a6543f3f87  tests/test_crr_p1_benchmark_contract.py
```

The generator is offline-only and is not imported by the official timed path. Formal latency receipts accept only U64; W64 remains untimed warmup-only.

## 3. Static corpus identity

```text
5d828faf9edd8f415409aab4997cba24c01584decce315f073491f1f2e4e5ba4  benchmarks/fixtures/static_manifest_v0.1.json
73688e0c2ed88d4edd7f5307af7738e09946d26b57f9c3e439811e3f3074e29b  benchmarks/fixtures/u64_v0.1.json
20cd4589ed4eac65aa38426ca98f3fac7cc7e31e6a66812eb18061bda86d6328  benchmarks/fixtures/u64_reference_golden_v0.1.json
697f27d241b28cbd9be0411b0d3bee1e00d86f8d7a49ff3d33b4384b8a948b9e  benchmarks/fixtures/w64_v0.1.json
81bbcdfc5f99c9bf2f1b5a87caf05f3a0430fa20842b6e70eabba7c5f52527ae  benchmarks/fixtures/w64_reference_golden_v0.1.json
09a7cc7935a854316e76bd8e31e872ac2e34f6bc10699e0c5a8dd91871c37a5b  benchmarks/fixtures/e1_manifest_v0.1.json
0385b9479788b7a95b3fe490a1acbc1a382aeb781b4fcb494a705a8e0818e4cf  benchmarks/fixtures/raw_sample_schema_v0.1.json
```

U64 and W64 each contain exactly one selected OptionQuoteSnapshot with 4 expiries x 16 canonically ordered unique Calls. Both contain 64 exact inputs and 64 reference PASS terminals.

- U64 DTE range: 42.2186–364.2186 days.
- W64 DTE range: 35.2186–357.2186 days.
- U64 LC0: `GLD   270226C00200000`.
- U64 unique BCS short: `GLD   270226C00236000`.
- W64 unique BCS short: `GLD   270226C00250000`.

## 4. Verification evidence

Integrator replay after final corpus regeneration:

```text
PYTHONPATH=src:. python3 -m unittest -v tests.test_crr_p1_benchmark_contract
Ran 11 tests in 194.376s
OK
```

This includes a fresh 128-Call reference recomputation of both static goldens. Additional evidence:

```text
worker independent 128-Call replay: OK in 139.363s
compileall: PASS
git diff --check: PASS
trailing whitespace: PASS
```

Independent code review required three correction rounds:

1. exact bind raw receipts to sealed fixture/input/golden and Single ordinal/terminal;
2. move the first expiry so every official Call is within the frozen 30–365 DTE range;
3. forbid W64 from becoming formal timed evidence.

All blocking findings are closed. Final verdict: `APPROVED`, P0/P1/P2 code findings = 0.

Code review: harness-native fallback — ce-code-review excludes untracked implementation files; dedicated code-reviewer reviewed explicit file set.

## 5. Frozen qualification rules

- U64 is the only formal timed corpus.
- W64 runs exactly three untimed warmup E2E passes and cannot produce a valid formal latency receipt.
- U64 success load may not be regenerated, replaced, pruned or reordered after this receipt.
- U64 and W64 golden semantics are backend-independent; new backend provenance hashes may differ.
- Single Gate uses the maximum of 64 per-contract nearest-rank P95 values.
- Batch and E2E require exactly 64 requested/bound/started/terminal Calls with zero result-cache hits.
- Any missing result, semantic mismatch, exception or timeout makes the whole invocation fail closed.
- The 5-second timeout cannot expose partial winner, partial results or actionable output.

## 6. Still pending

Before P1 can pass:

- implement and seal the C17 native tree kernel;
- generate H16 only after native source freeze and seal its reference golden before native execution;
- pass U64/E1/H16 full semantic differential;
- implement typed Batch-64 and quote-to-DecisionArtifact orchestration;
- pass two fresh-runner Single/Batch/E2E P95 runs;
- pass T1 process termination, late-result suppression and cleanup;
- seal native source/binary/compiler/runtime/machine provenance.

No provider, broker, private data, order, stage, commit, push or deployment authority is granted or exercised.
