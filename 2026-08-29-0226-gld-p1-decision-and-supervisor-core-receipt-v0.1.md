# GLD P1 Native Decision and Supervisor Core Receipt v0.1

Created: 2026-08-29 02:26 CST

## 1. Status

```text
P0_CORRECTNESS_PASS
P1_NATIVE_FULL_CALL_SHADOW_VERIFIED
P1_NATIVE_DECISION_SAME_PROCESS_VERIFIED
P1_PROCESS_SUPERVISOR_CORE_VERIFIED
NOT_IMPLEMENTED_PRODUCTION_DECISION_CHILD
P1_PERFORMANCE_NOT_PASS
P2_SOURCE_NOT_IMPLEMENTED
NO_DECISION
```

This receipt proves two independent local units: the same-process native Decision semantic path and the backend-agnostic process supervisor. They are not yet connected to one production child target, so quote-to-DecisionArtifact E2E and P1 latency remain unqualified.

## 2. Source identity

```text
d53766dfaa947b023742bc8aff23963deb8e97b096a54f74c5075d02c6b881a7  src/gld_research_core/p1_native_decision.py
027ffa51b6d57a6cf83ca14fd5ec80cfc2bbffbdf71b5d005a944261432b3f8f  tests/test_p1_native_decision.py
f8e1b307397cfb2b55b4f144dc6c46e32c1bae43f2152e79fabcf77142b76513  src/gld_research_core/p1_process_supervisor.py
037e510b069a6d226ab763dd50bc9af8f91e50c61553c48ea799f914e64af485  tests/test_p1_process_supervisor.py
```

## 3. Same-process Decision evidence

- exactly 64 canonical unique Calls, fixed 8 workers, one kernel thread and zero cache hits;
- all 64 terminal before selection; deterministic minimum failure ordinal;
- no failure exposes a partial winner, projection or artifact;
- coarse/fine BCS winner agreement, fixed tie-break, executable quote sides and fee/cost formulas;
- U64 projection `5dd66c686c0c7df9f05b63f5807a2b748f1a1f83f1b271c7fb76366e8132664c` and semantic `7f18fa70313c0d8dfa672e62757d06c66e99a12a7a197eb6aaeb9ccdc3c60541` match;
- W64 projection `62d584710ff9608958ef199f8397c1c347632f08cfaafec958396d1eea80ebfd` and semantic `e230592bf5325fac9942ea3c499de77a70663b8dd42909c956164fb7e09799c4` match on exactly three full untimed runs;
- process-local receipt verification rejects construction/copy/pickle/cross-factory and changed input/result/backend lineage;
- import-time helper, core factory, test seam and nested input boundaries fail closed under the reviewed contract;
- output remains `NATIVE_DECISION_SHADOW_ONLY_NOT_P1_QUALIFIED`, `actionable=false`, `broker_order_count=0`.

Final independent review: `APPROVED`, all severities = 0. Combined related suite: 53/53 PASS; reviewer target suite: 14/14 PASS.

## 4. Supervisor-core evidence

- explicit `spawn`, fresh non-daemon child, one-way single-use Pipe and canonical bytes only;
- fixed five-second absolute monotonic deadline with post-parse completion recheck;
- generation closes before cleanup, so late or partial frames cannot publish;
- bounded TERM grace, KILL, reap, Pipe/reader/process cleanup and no repeated-invocation FD/thread/child leak;
- separate session/process group, confirmation barrier and ordinary descendant group cleanup;
- repeated SIGINT and cleanup-stage `BaseException` are deferred until cleanup is retried and completed;
- strict single-frame, duplicate-key/type/size/token/request/artifact-hash validation;
- immutable receipt records deadline, process/session/group, message, cleanup and containment facts;
- every result is non-actionable with zero orders and `qualification_status=NOT_CLAIMED`.

The supervisor explicitly does not claim containment against a same-user child that actively escapes the process group on macOS. It is failure isolation, not a hostile sandbox.

Final independent review: `APPROVED`, P0/P1/P2 findings = 0. Worker and integrator no-ambient-PYTHONPATH runs: 18/18 PASS in about 28.7s.

## 5. Remaining integration gate

- define one canonical typed request for full Signal/Option/PIT/LC0/fee facts;
- pin one explicit prebuilt manifest path and backend evidence in the parent factory;
- spawn a module-level child that loads/verifies the native kernel, completes 64 Call + Decision, and sends exactly one canonical terminal envelope;
- parent must reconstruct and validate the complete semantic receipt/artifact before the same five-second deadline;
- pass success, crash, malformed, missing, timeout and late-result cases through the real production child path;
- only then begin formal Single/Batch/E2E P95 qualification.

Code review: harness-native fallback — ce-code-review excludes untracked implementation files; dedicated code-reviewer reviewed explicit file set.

No provider, broker, private data, live quote, order, stage, commit, push, PR, deployment or publication occurred.
