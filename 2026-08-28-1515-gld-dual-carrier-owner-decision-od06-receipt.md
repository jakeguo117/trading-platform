# GLD Dual-Carrier Owner Decision OD-06 Receipt

Created: 2026-08-28 15:15 Asia/Shanghai

状态：`OD06_CAPTURED / BCS_CHALLENGER_AUTHORIZED_FOR_RESEARCH / NO_OUTCOME_ACCESS`

## Contract binding

| Artifact | SHA-256 |
|---|---|
| [GLD Research Contract v0.2](/Users/jake/Desktop/Trading%20platform/2026-08-28-1515-gld-research-contract-v0.2.md) | `3940c27b4aec3585b9aa904a96f2118178589fafdcee46412441431ff9fcb8fc` |

## Owner decision

- 本版本只服务 `GLD`；`SLV` 保持 `NOT_QUALIFIED / SHADOW`，待 GLD 全链路跑通后再单独评估。
- Gate A 继续输出唯一方向资格，Gate B 同时研究两个预注册 carrier：
  1. `LC0_LONG_CALL_NO_ROLL`；
  2. `BCS0_DEBIT_CALL_SPREAD_NO_ROLL`。
- BCS 是一个固定 challenger，不是 Long Call 失败时的 fallback，也不是运行时动态 selector。
- BCS 为同到期、同 multiplier/deliverable/currency、standard/unadjusted 的 `1:1` debit vertical。
- BCS long leg 必须与 `LC0` 已先选定的 Call 完全相同；不得为了配对 BCS 重新选择 long leg。
- BCS short Call target Delta 为 `0.25`，资格区间为 `[0.20, 0.30]`。唯一排序为：
  1. `abs(delta - 0.25)` 最小；
  2. strike 较高；
  3. OCC identity 按 ASCII lexical 较小。
- BCS 不 roll。Long Call 的同到期 strike-up roll 仍是下游独立 challenger，不进入 `LC0` 对 `BCS0` 的主比较。
- `LC0` 和 `BCS0` 都通过时，必须进入 Owner selection；程序不得自动二选一。
- 两个结构都只生成决定事实和人工决策卡；Jake 在 IBKR 人工提交一个原生组合订单，`broker writes=0`。

## Quote-source boundary

- IBKR 是拟用的实时报价与人工执行 broker，但在字段、时钟、entitlement、同步性和身份资格被证明前，状态仍是 `QUOTE_SOURCE_NOT_QUALIFIED`。
- IBKR 无法形成合格、可执行的完整报价时，输出 `NO_DECISION` 和本地 blocking notification；不得 silent fallback。
- Robinhood 只允许成为 read-only shadow quote。其报价不得被称为 IBKR executable price，也不得与 IBKR 的另一条腿混合。
- 历史 Gate B 仍须使用完整 point-in-time 双边 BBO/size 数据；broker 当前报价不能替代历史数据。

## Snapshot boundary

- `SignalSnapshotV1`：只绑定 `10:45:00 America/New_York` 前 event 且前 receive 的 Gate A 事实。
- `OptionQuoteSnapshotV1`：只在 `[10:45:00, 10:46:00)` 内选择第一份纯数据质量合格的完整快照。
- 技术性不完整可以在一分钟窗口内重试；是否重试不得取决于价格、spread 方向或哪个 carrier 更有利。
- 最终 `DecisionArtifactV1` 必须同时绑定两个 snapshot hash、规则版本和 Delta 模型 hash。
- 人工交易后的 broker reconciliation 继续称为 `Snapshot B`，不得与上述两张研究输入快照混名。

## Determinism and risk boundary

- 决策卡的状态、合约、腿、数量、价格事实、费用、风险数字和条件逐字段由程序生成；LLM 不得改写。
- 相同已验证输入、相同规则版本和相同模型版本必须产生相同结构化输出。
- 两个 carrier 各自估计收益分布和 Half Kelly，不得复用对方 Kelly。
- 无固定止盈；BCS 不 roll；任何 assignment、exercise、partial fill、残腿或未知 broker 状态都阻断新开仓，直到 Snapshot B 对账完成。

## Non-meaning

本 receipt 不证明 Entry、Long Call 或 BCS 有收益，不授权 outcome/P&L 访问、Databento 下载、broker/private-data 访问、下单、部署或发布。它只授权在本地 synthetic fixtures 上冻结合同、实现确定性纯函数和测试 fail-closed 行为。
