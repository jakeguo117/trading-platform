# GLD P1 Performance and T1 Verification Receipt v0.1

Created: 2026-08-29 08:50 CST

```text
P0_CORRECTNESS_REPASS
P1_LATENCY_NOT_PASS
T1_PREFLIGHT_PASS
PLUGIN_OUTER_PROCESS_LIFECYCLE_NOT_VERIFIED
E1_PREFLIGHT_PENDING_OWNER_ACCEPTANCE
P2_SOURCE_NOT_IMPLEMENTED
NO_DECISION
```

本 receipt 只封存修复后当前源码的正式 CRR latency run、T1 watchdog preflight 与独立证据审计。它不证明 P1 已通过，不授权实时行情、broker/private data、订单、Git、部署或发布。

## 1. Current source and evidence identity

```text
eeedaef62ab3457587a02b452bdc6cf877124ef3690a1f2380d1cfb2001908e0  src/gld_research_core/p1_process_supervisor.py
e0c96680bbd9a4cf1e4c92a41a15d52d6f8f3f93161266500027b093f066c71f  benchmarks/crr_p1_performance.py
4f838f04cc5af6826987d0196860b3881896dbdf07882a039782ab489abdd08f  tools/run_crr_p1_performance.py
a0af1441b953dedae6c22d7a09e7f7bd35e5e3a96fb72acf30380d16d51bb95c  benchmarks/crr_p1_t1_preflight.py
814fff085bbd31ad38469e178f49fc765f499a10930a13cc9ac024d43eb0a0a8  tools/run_crr_p1_t1_preflight.py
0cb708714677438970d96801c40729bbfd05bbaf202201d05dfd36f2a41175d3  tests/test_crr_p1_t1_preflight.py
```

正式 performance evidence：

```text
directory: benchmarks/results/p1-20260829-0822-formal-v3
component aggregate SHA-256: dfa55c42b53e1242d68a92e54442e702fc4de45b6c9a16987be2b3126f26255c
source-set SHA-256: 1a9f71393ee510790eb20fac8e8ed77fa8d273f8b21f8fae01ac9bdc90658430
backend binding SHA-256: fb8bada64cbf3088c633ecac8a6a08ebe90ebdfd77f00bb3d379f731985614c1
machine SHA-256: e2e6cf8b00477d299c4a1287496e210e1a0c293381a5565936a6ab9951ebb143
runtime SHA-256: 92aca982b0919b62f598e5d77add437849e2719fc044f231af60c04fd13f2690
```

正式 T1 evidence：

```text
file: benchmarks/results/p1-20260829-0850-t1-formal/t1-preflight.json
receipt SHA-256: fd9a162351d58fb42475d19c52691bcfc0e649c408ddcc4ed6371c506d4cd6a4
bound component aggregate SHA-256: dfa55c42b53e1242d68a92e54442e702fc4de45b6c9a16987be2b3126f26255c
file mode/link count: 0400 / 1
directory mode: 0500
```

旧的 `p1-20260829-0600-formal` 只作历史测量；其 performance source 已变化，不能用于当前晋升。`p1-20260829-0715-formal-v2` 在 P0 blocker 被发现后主动中断，没有形成可晋升证据。

## 2. Formal latency result

| Scope | Single max-contract P95 | Batch-64 P95 | E2E P95 | Result |
|---|---:|---:|---:|---|
| Runner 1 | 47.106 ms | 0.824 s | 2.666 s | PASS |
| Runner 2 | 51.238 ms | 1.244 s | 3.071 s | E2E FAIL |
| Combined | 45.821 ms | 1.201 s | 3.0126635 s | E2E FAIL |

冻结 SLO：

```text
Single Call P95 <= 250 ms
Batch-64 P95 <= 2 s
quote snapshot -> DecisionArtifact P95 <= 3 s
```

Single 与 Batch 均通过；Combined E2E 超过硬边界 `12.6635 ms`，Runner 2 E2E 超过 `71.285125 ms`。不得四舍五入、选择 Runner 1、删样本或重复跑到偶然通过来宣称 P1 pass。

Cold30：

```text
P95: 5.811242792 s
plugin_outer_process_lifecycle: NOT_VERIFIED
gate_policy: BLOCK_UNTIL_LIFECYCLE_VERIFIED
```

当前 lifecycle 未验证，因此 Cold gate 未应用，但 `PLUGIN_OUTER_PROCESS_LIFECYCLE_NOT_VERIFIED` 是显式晋升 blocker。只有 verified persistent outer 才能把 Cold30 设为 report-only；verified fresh outer per invocation 必须应用 Cold P95 `<= 3s`，当前数值会失败。

## 3. T1 watchdog result

```text
scope: T1_ONLY
status: T1_PREFLIGHT_PASS
result: FAIL_CLOSED / P1_HARD_TIMEOUT / DEADLINE
wall elapsed: 5.106437167 s
supervisor elapsed: 5.106141375 s
messages observed / accepted / late suppressed: 1 / 0 / 1
artifact: absent
semantic receipt: absent
actionable: false
broker_order_count: 0
TERM / KILL / child reaped: true / true / true
reader joined / pipe closed / process closed: true / true / true
process group gone / descendant leak: true / false
partial result published: false
```

T1 receipt 顶层仍是 `P1_PERFORMANCE_NOT_PASS / NO_DECISION`。它只解决 performance aggregate 生成时尚未提供的 T1 gate，不改变 E1、lifecycle 或 latency blocker。

## 4. Evidence audit

独立 audit 已验证：

- 142 个 referenced artifacts 全部存在、为 regular file、路径安全、canonical JSON 与 SHA-256 精确匹配；
- 加 aggregate 共 143 个文件，总大小 9,239,500 bytes；最大文件 3,619,626 bytes，均低于冻结的 8 MiB 单文件与 64 MiB 集合边界；
- 两个 runner 与 30 个 cold process 的 source、machine、runtime、backend lineage 一致，process identities 各不相同；
- 当前 live source/machine/runtime 与 receipt 精确一致；
- T1 receipt 与 component aggregate hash binding 精确一致；
- 没有残留 performance/T1 child、resource tracker 或 order side effect。

Code review: harness-native fallback — ce-code-review excludes untracked implementation files; dedicated code-reviewer reviewed explicit file set.

## 5. Effective remaining P1 blockers

```text
E1_PREFLIGHT_NOT_SUPPLIED_OR_VERIFIED
PLUGIN_OUTER_PROCESS_LIFECYCLE_NOT_VERIFIED
LATENCY_GATE_NOT_PASS
```

下一步只允许定位并消除 E2E 非定价开销或其他不改变冻结语义的实现开销；不得降低 CRR steps、减少 64 candidates、放宽误差、改 selector/golden，或 fallback 到 broker Delta。E1 late-additive composite gate 仍需 Jake 明确接受。P1 全部关卡通过前不得进入 P2。
