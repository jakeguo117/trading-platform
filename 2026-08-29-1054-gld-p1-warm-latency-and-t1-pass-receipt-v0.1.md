# GLD P1 Warm Latency and T1 Pass Receipt v0.1

Created: 2026-08-29 10:54 CST

```text
P0_CORRECTNESS_REPASS
P1_WARM_LATENCY_PASS
T1_PREFLIGHT_PASS
COLD_FRESH_CONDITIONAL_FAIL
PLUGIN_OUTER_PROCESS_LIFECYCLE_NOT_VERIFIED
E1_PREFLIGHT_PENDING_OWNER_ACCEPTANCE
P1_PERFORMANCE_NOT_PASS
P2_SOURCE_NOT_IMPLEMENTED
NO_DECISION
```

本 receipt 封存稳定 AC / Low Power Mode off 环境下的正式 CRR P1 latency measurement 与绑定当前 aggregate 的 T1 watchdog。它证明 warm Single、Batch-64、E2E latency gate 通过，但不证明整个 P1 promotion gate 通过。

## 1. Formal evidence identity

```text
performance directory: benchmarks/results/p1-20260829-1031-formal-v5
component aggregate SHA-256: 150230cdeeb4ab368ace187a6de85cadc5f8207af25f4038fdd2ff8bc4912033
source-set SHA-256: feab2e0d14564919bf05e7894fd33b8432d62b0b737a95aba8e64a86e3807aa3
backend binding SHA-256: fb8bada64cbf3088c633ecac8a6a08ebe90ebdfd77f00bb3d379f731985614c1
machine SHA-256: e2e6cf8b00477d299c4a1287496e210e1a0c293381a5565936a6ab9951ebb143
runtime SHA-256: 92aca982b0919b62f598e5d77add437849e2719fc044f231af60c04fd13f2690
status: MEASUREMENT_COMPLETE
machine qualification: PASS
```

正式 T1 evidence：

```text
file: benchmarks/results/p1-20260829-1048-t1-formal-v2/t1-preflight.json
receipt SHA-256: e38644aa8e9b6431b1237161d7758f0b705c27e707a8448be66858a0f61f42dc
bound component aggregate SHA-256: 150230cdeeb4ab368ace187a6de85cadc5f8207af25f4038fdd2ff8bc4912033
file mode/link count: 0400 / 1
directory mode: 0500
```

## 2. Warm latency result

| Scope | Single max-contract P95 | Batch-64 P95 | E2E P95 | Result |
|---|---:|---:|---:|---|
| Runner 1 | 61.223 ms | 1.052 s | 1.579 s | PASS |
| Runner 2 | 47.482 ms | 1.024 s | 1.694 s | PASS |
| Combined | 53.692 ms | 1.039 s | 1.685 s | PASS |

冻结 SLO：

```text
Single Call P95 <= 250 ms
Batch-64 P95 <= 2 s
quote snapshot -> DecisionArtifact P95 <= 3 s
```

所有 warm gate 均保留原始样本量、nearest-rank P95 与原始语义验证；没有删除 outlier、减少候选、降低 CRR steps、放宽误差或 fallback 到 broker Delta。

## 3. Cold30 and lifecycle boundary

```text
Cold30 P95: 3.952483709 s
Cold SLO: 3.000000000 s
plugin_outer_process_lifecycle: NOT_VERIFIED
gate_policy: BLOCK_UNTIL_LIFECYCLE_VERIFIED
gate_applied: false
conditional_gate_pass: false
```

这意味着：

- 若 Plugin outer process 经证明为 persistent，Cold30 才可设为 report-only；
- 若 outer process 是 fresh per invocation，则必须应用 Cold P95 `<=3s`，当前正式数值失败；
- 不得为了通过而把未知 lifecycle 解释为 persistent。

## 4. T1 watchdog result

```text
scope: T1_ONLY
status: T1_PREFLIGHT_PASS
result: FAIL_CLOSED / P1_HARD_TIMEOUT / DEADLINE
wall elapsed: 5.112638000 s
supervisor elapsed: 5.112261708 s
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

T1 receipt 只关闭当前 aggregate 原本列出的 `T1_PREFLIGHT_NOT_SUPPLIED_OR_VERIFIED`；它不改变 E1 或 lifecycle blocker。

## 5. Independent evidence audit

独立只读审计确认：

- 143/143 JSON 均为 canonical；
- aggregate 覆盖的 142/142 文件无 missing、extra 或 hash mismatch；
- 2 Runner + Cold30 共 32 条 lineage 的 source、machine、runtime、backend、fixtures/goldens 完全一致；
- 全部为 AC Power、Low Power Mode off、thermal nominal、machine qualification PASS；
- 236 条 semantic records、15,104 个 Call terminals 均为 64 requested/bound/started/terminal，golden match，0 exception/semantic/terminal mismatch；
- 独立 nearest-rank 重算与 aggregate 精确一致；
- 无效的 `formal-v4` 仍独立保留为 `PERFORMANCE_CROSS_PROCESS_LINEAGE_MISMATCH`，未与 v5 复用文件或 inode。

## 6. Effective remaining P1 blockers

外部 T1 receipt 绑定后，有效 blocker 为：

```text
E1_PREFLIGHT_NOT_SUPPLIED_OR_VERIFIED
PLUGIN_OUTER_PROCESS_LIFECYCLE_NOT_VERIFIED
```

Warm latency blocker 已关闭。P1 仍是 `P1_PERFORMANCE_NOT_PASS / NO_DECISION`，不得进入 P2。下一步需要 Jake：

1. 明确接受或拒绝 E1 late-additive composite gate；
2. 选择并随后验证 Plugin outer lifecycle：fresh per invocation 或 persistent outer process。

若选择 fresh，当前 Cold30 失败，必须继续优化 cold initialization；若选择 persistent，必须真实实现并验证持久 lifecycle、停止/恢复边界，不能只改标签。
