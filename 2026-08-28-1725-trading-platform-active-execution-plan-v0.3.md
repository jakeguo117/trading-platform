# Trading Platform Active Execution Plan v0.3

Created: 2026-08-28 17:25 Asia/Shanghai

状态：`P0_CORRECTNESS_PASS / P1_PERFORMANCE_IN_PROGRESS / NO_BROKER_AUTHORITY`

## 1. 已接受结果

先交付一个可重放、确定性、默认 fail-closed 的 GLD 决策事实核心；随后在不改变模型语义和候选范围的条件下，使固定 CRR 负载达到实时卡片所需的延迟。只有性能验收通过后，才开始资格化只读券商行情。

当前所有结果仍是本地 synthetic research evidence，不是实时决策卡、broker 连接或交易权限。

## 2. 阶段顺序与关卡

### P0｜正确性封口

必须同时满足：

- `SignalSnapshotV1`、`OptionQuoteSnapshotV1`、American contract identity、CRR inputs/results、LC0/BCS0 selection、cost、episode、management snapshot、reconciliation receipt 与 `DecisionArtifactV1` 均可重放且 hash-bound；
- synthetic 或任意 hash 不能伪造 broker `CLOSING/CLOSED`；
- 双 carrier 同时通过只得到 `OWNER_SELECTION_REQUIRED`；
- 所有结果 `actionable=false`、`broker_order_count=0`；
- 代码、安全、量化终局复审没有 blocking finding。

P0 通过前不得开始性能重写。

P0 的运行时威胁边界是：本地进程只加载受信任、hash-bound 的项目代码，不允许任意第三方代码执行、`ctypes` 内存修改、debugger 注入或恶意 monkeypatch。Model/run/evidence hashes 用于 provenance、重放和变更检测，不是针对已取得同进程任意代码执行能力的密码学 attestation。若未来 Plugin host 不能保证该边界，必须另设进程隔离、签名 artifact 和最小 IPC schema 关卡；不得把 Python 对象 seal 描述成安全沙箱。

### P1｜CRR 性能优化

代表性 benchmark 负载固定为 **一张最终选中的 OptionQuoteSnapshot 内恰好 64 个必要 Call 合约**。这与 60 秒窗口内最多 64 张 candidate snapshots 的结构上限是两个不同维度，不得混用。验收目标：

```text
single_call_p95 <= 250 ms
batch_64_calls_p95 <= 2 seconds
quote_snapshot_to_decision_artifact_p95 <= 3 seconds
hard_timeout = 5 seconds -> NO_DECISION
```

禁止通过以下方式达标：

- 降低 CRR coarse/fine steps；
- 减少固定的必要候选；
- 放宽 IV、Delta、price residual 或 convergence 误差；
- fallback 到 broker Delta、BSM Delta、moneyness 或邻近 strike；
- 改变 short-Call selector、tie-break、fees、freshness 或 fail-closed 语义。

优化前后必须逐项一致：

```text
delta_ppm
coarse/fine iv_ppm
coarse/fine delta_ppm
contract winner
failure reason
early-exercise determination
DecisionArtifact terminal state and reason
```

这里的“一致”指交易语义和确定性输出一致，不要求跨 backend 复用 provenance hash。新的 compiled/vectorized backend 必须重新绑定其 source/binary、dependency、runtime、model、run、evidence 与最终 artifact hashes；这些 hashes 可以不同，但必须可验证、不可伪造，并能追溯到与 reference engine 完全一致的上述语义结果。

实现顺序：先冻结 benchmark fixtures、reference semantic goldens、reference provenance receipts 和测量方法，再比较可替换执行后端。任何新后端必须与现有 reference Python engine 做逐项 semantic golden differential，并独立验证自身 provenance；语义不一致即停止晋升。5 秒超时不得返回局部 winner。

### P2｜只读实时行情资格化

只有 P1 通过后开始：

- IBKR 为优先决策卡报价源；
- 只读资格至少证明完整 Call universe、同步双边 BBO/size、identity、market-data type/entitlement、event/receive timing、source/version receipt、fee schedule 与 combo capability；
- Robinhood 只在同一时间窗口保存 read-only shadow evidence；
- 不跨 broker 拼腿、不择优混价、不 silent fallback；
- IBKR 未资格或超时返回 `NO_DECISION`。

本计划当前不授权认证、私人账户读取、真实行情连接或 provider query。

### P3｜人工交易试运行与 Snapshot B

仅在开始人工交易试运行时设计：

- read-only positions；
- read-only open orders；
- read-only fills；
- pair identity、partial/residual leg、assignment/exercise、fees 与 terminal-state reconciliation。

在真实 broker authority 可证明前，public lifecycle 不产生 `CLOSING/CLOSED`，并保持 `RECONCILIATION_BLOCKED`。

## 3. 当前授权边界

已授权：更新本地计划、继续当前本地实现、synthetic fixtures、benchmark、pure-function tests、静态/动态本地验证。

未授权：券商或 provider 认证、私人数据、真实行情连接、Databento 下载、订单、通知发送、stage、commit、push、PR、部署或发布。

## 4. 停止与回退规则

- 正确性新证据改变模型前提时，回到 P0；
- golden differential 不一致时停止该优化后端，不修改 golden truth 配合实现；
- 64 Call 或端到端延迟未达标时维持 `BLOCKED_REFERENCE_ENGINE_NOT_REALTIME_QUALIFIED`；
- 超过 5 秒、输入缺失、source 未资格、结果不完整或 hash 冲突均返回 `NO_DECISION`；
- 不因局部 benchmark 更快而提前进入实时行情阶段。

## 5. 当前 checkpoint

2026-08-28 20:35 CST：P0 本地正确性关卡通过。独立代码复审和独立量化/交易语义复审均为 `APPROVED/PASS`，本地全量回归为 `136/136 OK`。这只证明 synthetic reference core 的确定性与 fail-closed 边界，不证明实时性能、真实来源或可交易性。

P1 基线仍不通过：独立复审在当前共享主机测得三次单 Call 为 `9.683s / 9.874s / 7.034s`；这不是正式 P95，但已足以证明当前实现远高于 `250ms` 目标。状态继续为 `BLOCKED_REFERENCE_ENGINE_NOT_REALTIME_QUALIFIED`，下一步先冻结 benchmark fixture、semantic goldens、测量方法和 provenance receipts。

2026-08-28 22:28 CST：benchmark foundation 与 reference goldens 已冻结并独立复审通过；U64/W64 均为 64/64 PASS，正式 timed receipt 只允许 U64。Native Shadow 第一批已完成：C17 tree kernel、离线 hash-addressed builder 与 strict loader 未接入现有权威 engine；350-case raw differential 全一致，单树约 100x 加速，终局 review 为 APPROVED。

2026-08-28 23:08 CST：exact-64 batch semantics 与 H16 untimed reference holdout 已终局复审通过。Batch receipt 已绑定 originating runner、backend、64 个有序 terminal identity 与完整 hash chain，重建、复制、跨 runner 或 provenance 重挂均不能伪造验证。H16 的 exact-type 与 contract identity 绑定漏洞已修复，16-call semantics 保持冻结且不计时。下一步为 native 单 Call 完整求解与 exact-64 接入；在 U64/E1/H16 differential、E2E、T1 和正式 P95 全部通过前继续 `P1_NOT_PASS / NO_DECISION`。

2026-08-29 00:22 CST：native full-Call shadow 与 native exact-64 已通过终局复审。U64 64 条、H16 16 条和 exact-64 ordered semantic hash 全部与冻结 reference 一致；loader identity、combined provenance、private config 和 returned-compute guard 的两个 HIGH 漏洞已关闭。E1 mapped tests 已绿，但因当前没有可重放 corpus/golden，仍明确为 `FULL_NATIVE_E1_DIFFERENTIAL_PENDING`。下一步进入 backend-neutral BCS/DecisionArtifact child pipeline 和 T1 5-second supervisor；仍为 `P1_PERFORMANCE_NOT_PASS / NO_DECISION`。

2026-08-29 00:58 CST：U64/W64 backend-neutral Decision semantic goldens 已在 native Decision layer 实现前追加冻结。U64 是唯一 formal E2E semantic gate；W64 只允许三次 untimed warmup conformance。Semantic hash 包含完整 64 terminal、winner、Delta、entry quote/cost 和 carrier/overall terminal；backend-specific provenance 独立进入尚未生成的 evidence envelope。该项明确标记为 `LATE_ADDITIVE_FREEZE_AFTER_NATIVE_PRICER_BEFORE_NATIVE_DECISION_LAYER`，不改写原 manifest，不宣称 P1 pass。下一步正式实现 same-process native Decision projection、spawn child 和 T1 supervisor。

2026-08-29 02:26 CST：same-process native Decision 与 backend-agnostic process supervisor core 已分别通过终局复审。Decision 路径完成 64 Call 后才选择 BCS，U64 与三次 W64 结果与冻结 golden 一致，任何失败不暴露 partial decision。Supervisor 已证明 5s deadline、post-parse recheck、TERM→KILL→reap、迟到结果压制和普通后代进程组清理。两者尚未连成生产 child，因此状态仍为 `NOT_IMPLEMENTED_PRODUCTION_DECISION_CHILD / P1_PERFORMANCE_NOT_PASS / NO_DECISION`。

2026-08-29 05:48 CST：production Decision child、process-local issued capability 与完整 workflow 5 秒绝对 deadline 已终局复审通过。Typed/bytes 入口、fresh spawn、64 Call、Decision projection、parent reconstruction 与最终 identity verification 现已形成一个 fail-closed 本地闭环；U64 bytes/typed 语义一致，W64 仍严格只做三次 untimed warmup。Generic receipt 不再被当作 causal provenance；supervisor 与 Decision 分别使用 factory-local one-shot capability/identity registry。5 秒从 typed workflow 进入前开始，覆盖编码、child、cleanup 与 parent validation，迟到结果不发布 payload。相关集成回归 118/118 通过。正式 Single/Batch/E2E P95、Cold 30、E1 late-additive composite gate 尚未完成，因此仍为 `P1_PERFORMANCE_NOT_PASS / NO_DECISION`，不得进入 P2。

2026-08-29 07:32 CST：正确性关卡因新的独立复审证据重新打开。旧版正式 performance run 的 133 个 raw receipts 与 142 个输出哈希已逐项验证，warm Single/Batch/E2E gate 数值通过；但 lifecycle policy 源码随后修正，旧 run 已失去当前源码资格。新版 lifecycle gate 现会在 Plugin outer-process lifecycle 未验证时显式阻断 P1；仅 verified persistent 可将 Cold30 视为 report-only，verified fresh 必须应用 Cold P95 `<= 3s`。第二次正式 run 在进行中发现 supervisor 的新 P0 blocker 后主动停止：主 cleanup 只运行至 `started + 5.25s`，极端调度暂停或清理耗尽窗口后缺少独立兜底 TERM→KILL→reap/close，可能遗留 child/process group 或 reader/FD。当前状态改为 `P0_CORRECTNESS_REOPENED / P1_PERFORMANCE_PAUSED / NO_DECISION`；先修复并终局复审安全回收，再用当前源码重跑 performance。T1 evidence validator 同时升级为完整 142 artifact schema/semantic/lineage/aggregate 重验与 no-follow/no-overwrite 输出，尚未获得终局复审。E1 late-additive composite gate 仍等待 Jake 明确接受。

2026-08-29 08:50 CST：P0 cleanup blocker 已关闭并通过 dedicated terminal review；persistent clock `BaseException`、no-pid start failure、Pipe/Process close one-shot failure 均有真实资源回归，supervisor 34/34、Decision child 35/35 通过。T1 evidence validator 的 142-artifact semantic/provenance 重验与 no-follow/no-overwrite 发布边界已终局复审通过。当前源码正式 performance run 完成：Runner 1 E2E 2.666s PASS，Runner 2 E2E 3.071s FAIL，Combined E2E 3.0126635s FAIL；Single 45.821ms、Batch 1.201s 通过。Cold30 P95 5.811s，但 Plugin outer lifecycle 仍未验证，故 lifecycle 是显式 blocker。正式 T1 watchdog 为 5.106s，迟到消息 1/接受 0，TERM→KILL→reap、reader/pipe/process/group 全部闭合，`T1_PREFLIGHT_PASS / T1_ONLY`；receipt 仍明确 `P1_PERFORMANCE_NOT_PASS / NO_DECISION`。有效剩余 blocker：E1、lifecycle、latency。证据见 `2026-08-29-0850-gld-p1-performance-and-t1-verification-receipt-v0.1.md`。下一步定位 E2E 可消除开销，不得通过重跑挑结果或改变冻结语义晋升。

2026-08-29 10:11 CST：E2E 瓶颈已定位为 64 Call 输入的平方级重复绑定；新 exact-type bulk binder 在不改变任何 `CrrCallInputsV1`、`input_sha256`、failure precedence 或 CRR 模型身份的前提下，将 U64/W64 64-call binding 从约 221–233ms 降至约 12.4–12.6ms。subclass fallback、mutation recheck 与 runtime-integrity replacement 均已关闭独立复审 finding，终审 `P0=0 / P1=0 / APPROVED`，全量回归 344/344 PASS。正式重跑 `p1-20260829-0943-formal-v4` 因运行中 AC Power 切换为 Battery Power + Low Power Mode on，触发 `PERFORMANCE_CROSS_PROCESS_LINEAGE_MISMATCH`；仅完成 Runner 1，Cold30=0，整次 run 无效且不得晋升。当前必须先恢复并全程保持 AC Power、Low Power Mode off，再从新目录重跑原规格。证据见 `2026-08-29-1011-gld-crr-bulk-binding-and-invalid-rerun-receipt-v0.1.md`；状态仍为 `P1_PERFORMANCE_NOT_PASS / NO_DECISION`。

2026-08-29 10:54 CST：稳定 AC / Low Power Mode off 的正式 `formal-v5` 完成并通过独立证据审计。Combined Single max-contract P95 `53.692ms`、Batch-64 P95 `1.039s`、E2E P95 `1.685s`，两个 Runner 与 combined warm latency gate 全 PASS；143/143 canonical JSON、142/142 aggregate-linked files、32 条 cross-process lineage、236 条 semantic records 与 15,104 个 Call terminals 全部一致。绑定当前 aggregate 的 T1 watchdog 也通过：5.113s fail-closed，迟到消息 1/接受 0，TERM→KILL→reap 与所有资源关闭成立。Cold30 P95 `3.952s`；outer lifecycle 未验证，故 cold gate 未应用但 lifecycle 仍是 blocker，若选择 fresh per invocation 则当前 Cold SLO 明确失败。外部 T1 receipt 生效后，剩余有效 blocker 为 E1 与 lifecycle。证据见 `2026-08-29-1054-gld-p1-warm-latency-and-t1-pass-receipt-v0.1.md`；仍为 `P1_PERFORMANCE_NOT_PASS / NO_DECISION`，不得进入 P2。
