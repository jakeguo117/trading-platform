# GLD P1 Production Child Workflow Receipt v0.1

Created: 2026-08-29 05:48 CST

## 1. Status

```text
P0_CORRECTNESS_PASS
P1_PRODUCTION_DECISION_CHILD_VERIFIED
P1_PROCESS_LOCAL_RESULT_CAPABILITY_VERIFIED
P1_FULL_WORKFLOW_5S_DEADLINE_VERIFIED
P1_FORMAL_LATENCY_NOT_RUN
E1_COMPOSITE_SEMANTIC_PENDING
P1_PERFORMANCE_NOT_PASS
P2_SOURCE_NOT_IMPLEMENTED
NO_DECISION
```

This receipt closes the local correctness path from one typed synthetic quote request through a fresh spawned native child, exact-64 Call evaluation, deterministic Decision projection, parent reconstruction, and final process-local verification. It does not claim a formal P95 result, live data readiness, broker authority, or an actionable DecisionArtifact.

## 2. Source identity

```text
1740ff0d861da896d8b2c32637fa485d73164b1045d8414575f5c30b0c101ba4  src/gld_research_core/p1_process_supervisor.py
97df0fc1939093f648302e86f22fc4a213b5e5a734d8e86465168184af0edf62  tests/test_p1_process_supervisor.py
2889d41352d61a4c6f488bd125956eae7a587025470e0a2019de3bb076811170  src/gld_research_core/p1_decision_child.py
81056e5991da9635a2638d1f2e1117bc26108d74ec1ca43c47bae6c1827d8c72  tests/test_p1_decision_child.py
```

Native execution remains bound to combined backend evidence:

```text
e3d8edc1c0537db2d6eab7c004d33bc4dedfc319e5348679ffb9a3a52e913bd7
```

## 3. Production child evidence

- one strict canonical typed request carries Signal, ordered candidate snapshots, PIT facts, LC0 binding, fees, rule package, research contract and P1 contract bindings;
- the parent factory pins one explicit prebuilt native manifest and expected backend evidence; requests cannot select a backend, manifest, timeout or fallback;
- the module-level spawn child loads and verifies the native kernel in the fresh process, completes all 64 Calls, verifies the same-process receipt, and emits exactly one canonical terminal frame;
- U64 bytes and typed entry paths match the frozen Call terminals, projection `5dd66c686c0c7df9f05b63f5807a2b748f1a1f83f1b271c7fb76366e8132664c` and runtime semantic `dcaa69eec511551f3b2e72733bf6f2b19907ee63ba2cd647ef3ac9b017fb44b3`;
- W64 remains untimed and is exercised exactly three times as warmup conformance;
- `SIGNAL_FAIL`, missing output, child-declared failure, malformed/duplicate transport, crash, timeout and late completion publish no semantic receipt or artifact;
- the clean `python -I` path proves the package and fresh child do not depend on ambient `PYTHONPATH`.

## 4. Process-local provenance capability

Generic receipt fields cannot independently distinguish every internal supervisor path. The integration therefore does not infer causal provenance from receipt shape.

- supervisor `run_issued` returns an opaque process-local capability rather than a readable result;
- one-shot `consume` returns the factory registry's original result only for the same request, invocation, child target and frozen policy;
- Decision reads no supervisor result field before successful consume;
- the bound Decision runner separately registers the final IntegrationResult and exposes `is_verified_result` for the original unchanged object;
- construction, copy, deepcopy, replace, pickle, cross-invocation, cross-runner, cross-factory, unsafe mutation and equal-valued nested replacement do not inherit trust;
- capability failure raises one stable parent-validation error and does not fabricate a receipt.

## 5. Full-workflow deadline

The fixed five-second budget starts at the Decision workflow entry, not at the later child start.

- `run_typed` signs the workflow ticket before typed encoding; the bytes compatibility entry signs it immediately on entry;
- the same absolute monotonic deadline governs parent preparation, spawn child, frame acceptance, cleanup, consume, parent reconstruction, identity registration and finalization;
- parent preparation that exhausts the budget does not start a child;
- every completion at or after the deadline becomes `FAIL_CLOSED / P1_HARD_TIMEOUT / WORKFLOW_DEADLINE`;
- a late path publishes no semantic receipt, artifact or artifact hash and remains `actionable=false`, `broker_order_count=0`;
- the half-open boundary is exact, and an uncooperative worker is terminated and reaped within the frozen `5.0s <= elapsed <= 5.25s` qualification window.

## 6. Verification evidence

Integrator related regression:

```text
118 tests
Ran in 115.719s
OK
```

The focused Decision suite passed 35/35 and the supervisor suite passed 30/30. Separate reviewers approved the supervisor workflow and Decision integration with P0/P1/P2 findings all equal to zero.

Code review: harness-native fallback — ce-code-review excludes untracked implementation files; dedicated code-reviewer reviewed explicit file set.

## 7. Remaining gates

- implement the frozen formal Single, Batch-64, E2E and Cold performance runner and preserve raw samples;
- complete two fresh runner-process P95 runs and T1 qualification;
- resolve the E1 historical-order gap through an explicitly accepted late-additive composite gate; do not claim a retroactive pre-native freeze;
- only after every U64/H16/E1/T1/latency gate passes may status become `P1_PERFORMANCE_PASS` and P2 read-only source qualification begin.

No provider, broker, private data, live quote, order, stage, commit, push, PR, deployment or publication occurred.
