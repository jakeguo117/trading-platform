# GLD CRR P1 Benchmark Contract v0.1

Created: 2026-08-28 20:46 CST

状态：`FIXTURES_AND_REFERENCE_GOLDENS_FROZEN / NATIVE_SHADOW_NEXT / P1_NOT_PASS`

## 1. 目的与边界

本合同只资格化本地 GLD CRR 决策事实核心的性能，不资格化行情来源、账户、券商连接或订单能力。P1 未通过时保持：

```text
BLOCKED_REFERENCE_ENGINE_NOT_REALTIME_QUALIFIED
NO_DECISION
actionable=false
broker_order_count=0
```

`64 Call` 指一张最终选中的 `OptionQuoteSnapshotV1` 内恰好 64 个唯一、必要的 Call 合约，并产生 64 个 `CrrCallInputsV1` 和 64 个完整 CRR terminal results。它不是 `OptionSnapshotCandidateLedgerV1` 在 60 秒窗口内允许的 candidate-snapshot 数量上限。

## 2. 不可改变的模型语义

任何优化后端都必须保留：

- coarse steps `512/513`；
- fine steps `1024/1025`；
- 固定 32 次 bisection；
- IV bracket、price residual、IV/Delta convergence thresholds；
- American early-exercise 比较、materiality 与 exact node count；
- `ROUND_HALF_EVEN` 的 ppm/nano-USD 输出；
- candidate universe、canonical ordinal、selector、tie-break、fee、freshness、failure priority 与 fail-closed 行为；
- 禁止 broker Delta、BSM Delta、moneyness、邻近 strike 或 partial winner fallback。

语义一致不要求 reference 与 native backend 使用相同 provenance hash。新后端必须绑定自身 source、binary、compiler/build flags、dependencies、runtime、machine、run 与 evidence identities。

## 3. 三层计时入口

### Single Call

- 起点：进入公开 sealed 单 Call API 之前；
- 终点：结果完成 verification 与 provenance registration；
- 并行预算：1 worker、1 kernel thread；
- Gate：U64 中每个合约各自 P95 `<= 250ms`；最终 Gate 取 64 个 per-contract P95 的最大值，同时报告 pooled P95。

### Batch-64

- 起点：收到按 canonical ordinal 排列的 64 个已绑定 inputs；
- 终点：64 个 terminal results 全部完成、验证、恢复原顺序并生成 batch semantic hash；
- 并行预算：资格机固定 8 workers；无 nested kernel/BLAS/OpenMP/GPU threads；
- Gate：P95 `<= 2s`。

### Quote Snapshot to DecisionArtifact

- 起点：typed signal、candidate ledger、selected snapshot、PIT、fee、rule/research bindings 进入 orchestrator，尚未 validation；
- 终点：`DecisionArtifactV1` 已构造、验证并实际计算 `artifact_sha256`；
- 必须包含 quote/ledger validation、64 Call binding、完整 CRR batch、Delta evidence、BCS selector/cost、carrier terminals 与 artifact hash；
- 不包含 P2 才会实现的 broker 网络等待或 raw feed decode；
- Gate：P95 `<= 3s`。

现有 `build_decision_artifact()` 只消费已完成的 Delta，不能充当本 E2E 入口。必须新增 typed batch API 和 quote-to-artifact orchestrator，且不得返回 partial tuple。

## 4. Hard timeout

E2E 外层 supervisor 使用绝对 `monotonic_ns` deadline。到 `t0 + 5_000_000_000ns`：

- 丢弃所有 partial results、selection、winner 与未完成 artifact；
- 返回 `NO_DECISION / CRR_HARD_TIMEOUT`；
- `actionable=false`、`broker_order_count=0`、selection absent；
- generation token 阻止迟到 worker 发布；
- 本次 worker group 必须终止或隔离回收，cleanup 不得阻塞 timeout terminal。

真实不合作 worker 的验收窗口为 `5.0s <= elapsed <= 5.25s`，并证明没有迟到 artifact 或遗留 worker。只对 `future.result(timeout=...)` 不足以满足本合同。

## 5. 冻结 corpus

### U64 success load

- 一个 `PASS` synthetic SignalSnapshot；
- 一个质量合格、恰好 64 unique American standard/unadjusted GLD Calls 的 OptionQuoteSnapshot；
- 4 个冻结 expiry × 每个 expiry 16 个冻结 strikes；覆盖 30–365 DTE 与 ITM/ATM/OTM；
- 所有 quotes、tick、timestamps、PIT rate/expense/borrow、identity 与 canonical tuple order 逐字段冻结；
- 64 个 Call 在 reference engine 全部成功并执行完整求解；
- 固定一个 LC0 long binding；其同 expiry higher-strike universe 必须产生唯一、预登记的 0.25 Delta BCS winner。

U64 不包含可提前结束的错误样本，以免成功性能负载因错误分布变快。

### W64 warmup load

与 U64 具有不同经济输入和不同 canonical hash。固定执行 3 次完整 E2E，不计时；禁止使用 U64 golden 预热完整结果、IV 或 Delta cache。

### E1 semantic/error corpus

包含现有 27-case matrix、early exercise true/false、IV/TREE 失败、selector instability、hostile Decimal 与 provenance mutation。E1 不进入 latency Gate，只进入 semantic Gate。

### T1 watchdog corpus

包含不合作 worker，用于证明 5 秒 timeout、late-result suppression 与 worker cleanup。

### H16 holdout

优化 backend source freeze 后才生成 16 个不同经济输入。reference golden 必须在优化 backend 看到输入之前先封存，防止只针对 U64 硬编码。

## 6. Semantic golden

每个 Call 的 backend-independent golden 至少包括：

```text
ordinal
input_sha256
PASS/FAIL
reason_code
contract_id
iv_ppm / delta_ppm
coarse_iv_ppm / fine_iv_ppm
coarse_delta_ppm / fine_delta_ppm
coarse/fine price_residual_nano_usd
coarse/fine early_exercise_nodes
early_exercise_detected
```

Batch/E2E golden 还包括：

```text
ordered 64-call terminal vector
long/short contract winner
long/short/net delta
entry-cost facts
LC0/BCS0 terminal status and reason
DecisionArtifact status and reason
owner_selection_required
actionable
broker_order_count
```

逐字段完全一致才通过；只比较最终 Delta 或最终 hash 不足。`semantic_output_sha256` 排除 backend provenance；evidence/artifact hashes 必须包含 semantic hash 和当前 backend provenance。

## 7. 样本与 P95

- 新 runner process 完成 import、fixture validation、backend/pool 初始化；
- 固定 W64 warmup 3 次，禁止自适应 warmup；
- Single：64 个 Call 各 30 次，共 1,920 samples；
- Batch-64：100 samples；
- E2E：100 samples；
- 至少两个全新的 runner processes 连续通过，不选择较快轮次；
- P95 使用 nearest-rank：`sorted_ns[ceil(0.95*N)-1]`；
- 唯一计时钟为 `perf_counter_ns()`；
- 不删除 outlier、不插值、不扣 timer overhead；
- 任意异常、缺结果、semantic mismatch 或非 64 terminal counts 使整轮失败，不进入 P95。

Cold-start 另报告 30 个 fresh-process samples。如果生产 Plugin 每次调用新进程，cold path 自动升级为正式 E2E Gate，并必须满足 3 秒。

## 8. Cache、并行与失败排序

允许：已加载 native code、只依赖模型常量的 immutable lookup、预创建固定 worker pool/buffers、一次 batch 内由同 snapshot 导出的公共量。

禁止：跨 invocation 的完整 result/IV/Delta memoization、U64 golden cache、candidate pruning、winner-first short circuit、动态增加 worker、nested threads、GPU 或任何 fallback。

receipt 必须证明：

```text
requested=64
bound=64
started=64
terminal=64
result_cache_hits=0
```

并行失败时按最小 canonical ordinal 选择 terminal reason，不按 worker completion order。

## 9. 资格机与运行 receipt

首台资格机冻结为：Apple M4、10 CPU cores、16GB、arm64、macOS 26.5.2 build 25F84、Python 3.14.3。正式 run 使用 AC power、Low Power Mode off、thermal nominal。

receipt 记录 model/chip/core/memory、OS/build、Python/cache tag、compiler、backend/source/binary/dependency hashes、worker/thread/GC/cache policy、timer resolution、原始 latency samples、P95 与 semantic result。不得记录序列号、Hardware UUID、UDID、用户名或私有账户信息。

## 10. 晋升条件

优化前必须封存：本合同、schema、runner source SHA、U64/W64/E1 canonical bytes 与 hashes、U64/E1 reference semantic goldens、winner/fee/rule/research hashes、测量次数/顺序/P95/worker/cache policy 和 qualification-machine receipt。

只有以下全部满足，才能记录 `P1_PERFORMANCE_PASS`：

1. U64、E1 与 H16 semantic differential 全绿；
2. 两个新 runner processes 的 Single、Batch、E2E P95 全绿；
3. T1 timeout/late-result/cleanup 全绿；
4. raw samples、backend provenance 和 qualification receipts 完整。

否则维持 `BLOCKED_REFERENCE_ENGINE_NOT_REALTIME_QUALIFIED / NO_DECISION`，不得进入 P2。
