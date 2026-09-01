# GLD Deterministic Core P0 Verification Receipt v0.1

Created: 2026-08-28 20:35 CST

## 1. Terminal status

```text
LOCAL_SYNTHETIC_REFERENCE_CORE
P0_CORRECTNESS_PASS
P1_PERFORMANCE_NOT_PASS
P2_SOURCE_NOT_IMPLEMENTED
NO_DECISION
actionable=false
broker_order_count=0
```

本 receipt 只证明当前本地、synthetic、受信任进程边界内的确定性事实核心通过 P0 正确性关卡。它不证明实时行情、PIT 来源、LC0 authority、Snapshot B、券商连接、订单能力、CRR 性能或可交易性。

## 2. Frozen authority inputs

```text
f16d8a44ba3b72a81db335fd52c440cfe78c15c5e9004151d074bb0a0fd5f5a7  2026-08-28-1515-gld-dual-carrier-owner-decision-od06-receipt.md
3940c27b4aec3585b9aa904a96f2118178589fafdcee46412441431ff9fcb8fc  2026-08-28-1515-gld-research-contract-v0.2.md
06a4e216aa0a0f0c2e7192027ac5a19d288f799d32da59bf632bb26e7678caa8  2026-08-28-1515-gld-call-delta-crr-model-v0.1.md
7f2ef0e62f5954d3893b04b4e6fd0d8823a06c4fdb8b8a30827165ea06bcdcd7  2026-08-28-1515-gld-minimum-option-data-gap-contract-v0.2.md
a929d8a779ac406cf6517bd9a2b8d9d07acaab30463359b1257ef9d07e4ee75f  2026-08-28-1515-gld-data-specification-manifest-v0.4.md
```

## 3. Verified implementation identity

```text
542cf81600abc06b1c6864bf33ee4d13cc84696b40e5cc9c9416cf4017ab4b9b  src/gld_research_core/facts.py
5b966b228429439bad7aaa21c329285b65755eb02f84120216614ed2deb44567  src/gld_research_core/crr_delta.py
a111e27f01c3c389b8aba35187885f442c35ab1533963899677e18ee1b81effe  src/gld_research_core/bcs.py
2361ba8d97dfd79274b8e5a83784d6015f75ffae6cef4ff920aeb8572477f387  src/gld_research_core/__init__.py
64b4b62a04058a8dd13f1d6644717fd839dc969443941f130b4b9f478369923d  tests/test_decision_facts.py
871cfe53d6dfa4a29bf9cee5da34e368b54ffc1f9a99e804bb3d67bd033be2cf  tests/test_crr_delta.py
f64a3aa938efcde869e2fc06f61fd6f9da2a707cd48a7ba40abe6fb9f8311d0f  tests/test_bcs.py
59bf1f757c4f63e7a30440cc7977902e36bb73bc8baa8aa61c6f9cf3f7d4a0ac  tests/test_v02_regressions.py
ebcad9321aa2e1903530ee12fc72aa18e299c4a184124c410617d39ee4903dee  README.md
ba5c79857a694e31393551a345aace0234661b6e056c76b2391f82194e48303f  pyproject.toml
```

Runtime/model receipts:

```text
MODEL_SHA256=054970e2903152c62c0f71e7bfb10b983795ea8f314d89b033ddda6ed7eca1ad
MODEL_CONFIG_SHA256=5ec6c0b60801e9aa7d2d4dc09459902e51dfdad4f183fb91a20bcd3c8467ac00
MODEL_SOURCE_ARTIFACT_SHA256=5b966b228429439bad7aaa21c329285b65755eb02f84120216614ed2deb44567
RUNTIME_FINGERPRINT_SHA256=300e6050a7d82087a4d31468a9df1a8711e406d656f2b87538df5f1dda7afc82
QUOTE_QUALITY_POLICY_SHA256=bca07ef4d032136c320353d4c3c3b4063ae4344968295f5e04b9b77ced08c6dc
```

Environment: macOS 26.5.2 build 25F84, arm64, Python 3.14.3.

Repository checkpoint: branch `feat/gld-bcs-v02`, baseline commit `26f00def32eacbe84439bab20476943388398974`, no remote configured. All P0 feature files remain unstaged and uncommitted because no stage/commit/push authorization was given.

## 4. Direct verification evidence

Integrator full regression:

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
Ran 136 tests in 330.597s
OK
```

Additional local checks:

```text
python3 -m compileall -q src tests  -> PASS
git diff --check                    -> PASS
public exports: 61, missing: []     -> PASS
```

Independent code review:

```text
P0 findings: 0
P1 findings: 0
P2 findings: 0
Verdict: APPROVED
Independent full regression: 136/136 OK in 304.070s
```

Code review: harness-native fallback — ce-code-review excludes untracked implementation files; dedicated code-reviewer reviewed explicit file set.

Independent quant/trading-semantics review:

```text
P0 findings: 0
Verdict: PASS
Focused scope: 102/102 OK in 333.336s
27-case CRR matrix: 25 deterministic round trips, 2 preregistered TREE_NOT_CONVERGED fail-closed results
```

The reviews explicitly verified hostile Decimal isolation, exact nested PIT carry authority, fixed quote-quality policy, full ordered candidate-ledger binding, first-qualified selection, BCS lifecycle lineage/latch persistence, LC0 authority failure forcing the whole artifact to `NO_DECISION`, absence of public broker/order send seams, and zero actionable/order outputs.

## 5. P1 performance blocker

The independent quant review measured three consecutive single-Call runs on the current host:

```text
9.683s / 9.874s / 7.034s
mean 8.864s
```

This is not a formal P95 measurement, but it proves the reference Python implementation does not meet `single_call_p95 <= 250ms`. The 64-Call batch, quote-to-artifact and hard-timeout acceptance paths have not yet been implemented or measured. Therefore:

```text
FULL_CHAIN_EXECUTION_STATUS=BLOCKED_REFERENCE_ENGINE_NOT_REALTIME_QUALIFIED
```

No result may be promoted to P2 until the fixed 64-Call benchmark, semantic golden differential and all P1 SLOs pass without changing CRR steps, candidate scope, tolerances, failure semantics or fallback policy.

## 6. Trusted-process boundary

Hashes and frozen Python objects provide provenance, replay and ordinary mutation detection inside a trusted process. They are not cryptographic attestation against arbitrary same-process code execution, `ctypes`, debugger injection or malicious monkeypatching. A future untrusted Plugin host requires process isolation, signed artifacts and a minimal IPC schema before promotion.

## 7. Explicitly absent authority

No broker/provider authentication, private account access, real market-data query, Databento download, order creation/submission, notification send, stage, feature commit, push, PR, deployment or publication occurred.
