# GLD CRR Bulk Binding Optimization and Invalid Rerun Receipt v0.1

Created: 2026-08-29 10:11 CST

```text
P0_CORRECTNESS_REPASS
CRR_BULK_BINDING_OPTIMIZATION_APPROVED
P1_FORMAL_RERUN_INVALID_POWER_LINEAGE
PLUGIN_OUTER_PROCESS_LIFECYCLE_NOT_VERIFIED
E1_PREFLIGHT_PENDING_OWNER_ACCEPTANCE
P2_SOURCE_NOT_IMPLEMENTED
NO_DECISION
```

本 receipt 封存 CRR 输入绑定优化与一次无效的正式性能重跑。它不证明 P1 latency 已通过，不授权 broker/private data、实时行情、订单、Git、部署或发布。

## 1. Optimization boundary

原路径在一次 E2E 内五次准备同一份 64 Call 输入；单项 binder 每次线性扫描整条 quote chain，并为每个合约重算整份 snapshot hash，形成平方级非定价开销。

优化新增 exact-type bulk binder：

- 保持 64 个合约的原始顺序；
- 每个 `CrrCallInputsV1` 与旧单项 binder 完全相同；
- U64/W64 的每个 `input_sha256` 完全相同；
- subclass/stateful input 回退旧 64-call 顺序 binder；
- exact input 在结束时重算 snapshot hash 以检测 mutation；
- 旧 failure reason precedence 保持不变；
- bulk callable 已进入 native Decision engine 与 public factory 的 runtime-integrity binding；
- `crr_delta.py` 未改写，冻结模型 source SHA 与 reference goldens 保持不变。

```text
936fd74640ab7e5410bce235efe78842e41f18607a868f403fba5dbf43857f88  src/gld_research_core/crr_input_binding.py
5b966b228429439bad7aaa21c329285b65755eb02f84120216614ed2deb44567  src/gld_research_core/crr_delta.py
a35e2c82732179e3e801b5023373d65aba0667e0d282c40d049720c4f721659d  src/gld_research_core/p1_native_decision.py
b5a972d578e41d7488ab786a4a7419dab0af59ac139b7c7ce75cde150325447e  src/gld_research_core/p1_decision_child.py
bae4ef8218280998261d3577285beabcfae00a780b47c27566f7cc44e626f106  benchmarks/crr_p1_performance.py
34c807a4ad32b6201e557912e362eb5bc29adf2627ebc664f21374203ff2e0cc  tests/test_crr_bulk_binding.py
```

新模块已加入 formal performance source-set lineage；未更新 fixtures 或 goldens。

## 2. Correctness and local performance evidence

```text
full local regression: 344/344 PASS
bulk binding tests: 12/12 PASS
native Decision tests: 14/14 PASS
Decision child tests: 35/35 PASS
performance contract tests: 20/20 PASS
independent terminal review: P0=0 / P1=0 / APPROVED
```

交替微基准（非正式）：

| Fixture | Sequential 64 | Bulk 64 | Speedup |
|---|---:|---:|---:|
| U64 | 221.119 ms | 12.627 ms | 17.51x |
| W64 | 232.910 ms | 12.412 ms | 18.76x |

短 E2E 探针（10 samples，非正式）为 median `1.303727s`、max `1.357754s`；Batch-64 median `0.762624s`。这些只证明优化方向，不替代正式双 Runner P95。

Code review: harness-native fallback — ce-code-review excludes untracked implementation files; dedicated code-reviewer reviewed explicit file set.

## 3. Formal rerun was invalid

正式目录：

```text
benchmarks/results/p1-20260829-0943-formal-v4
component aggregate SHA-256: e70bbdbfa5a9e1afbc35c4183694b40b8a57fc18120f166768f8cb503c22fd55
measurement_failure_reason_code: PERFORMANCE_CROSS_PROCESS_LINEAGE_MISMATCH
runner_processes_completed: 1
cold_processes_completed: 0
```

Runner 1 启动时是 qualified AC Power、Low Power Mode off；正式运行期间机器切换为 Battery Power 且 Low Power Mode on，后续 process 的 `machine_sha256` 与 Runner 1 不同。Coordinator 因此 fail-closed，没有写入 Runner 2 或 Cold30 晋升证据。

Runner 1 的 report-only 数值为 Single max-contract P95 `215.456917ms`、Batch-64 P95 `2.796489833s`、E2E P95 `4.038416291s`。由于整次 run 的 power lineage 不稳定，这些数值不得用于通过或稳定失败结论；即使单独查看，它们也没有满足 Batch/E2E SLO。

当前机器复核：

```text
Battery Power
99% / discharging
Low Power Mode on
thermal warning: none reported
```

## 4. Required next state

下一次正式重跑前必须同时满足：

- 连接 AC Power；
- active Low Power Mode = off；
- 从 coordinator 到两个 Runner 与 Cold30 全程 power/machine lineage 不变；
- 先保持机器空闲，再从全新 output directory 运行原规格；
- 不复用或覆盖本次无效目录。

在新正式 aggregate 通过前继续 `P1_PERFORMANCE_NOT_PASS / NO_DECISION`。E1 和 Plugin outer lifecycle 仍是独立 blocker。
