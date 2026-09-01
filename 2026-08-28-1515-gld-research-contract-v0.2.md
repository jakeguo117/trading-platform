# GLD Research Contract v0.2

Created: 2026-08-28 15:15 Asia/Shanghai

状态：`OWNER_SCOPE_CAPTURED / DUAL_CARRIER_RESEARCH_DEFINED / NO_OUTCOME_ACCESS`

## Parent

| Artifact | SHA-256 |
|---|---|
| [GLD Long Call Research Contract v0.1 Draft](/Users/jake/Desktop/Trading%20platform/2026-08-28-0102-gld-long-call-research-contract-v0.1-draft.md) | `d1c7ca5e65dc4c5cb263f328122201971f1d38e57491e1be99c6c5663dadd6df` |

## 1. 版本关系

本合同继承 `GLD Long Call Research Contract v0.1 Draft`、OD-01 至 OD-05、Post-open Cutoff Addendum v0.1 和 Minimum Option Data Gap Contract v0.1。发生冲突时，本合同只覆盖以下内容：

1. carrier universe 从 Long Call only 改为 `LC0` 与一个 `BCS0` challenger；
2. Gate A 终局改为 carrier-neutral；
3. 10:45 前的方向事实与 10:45 后的报价事实拆成两张 snapshot；
4. BCS selector、cost、lifecycle、risk 和 comparison 规则；
5. 本地 Delta 路径固定为 `GLD_CALL_DELTA_CRR_AM_V1`。

其余风险偏好、时间隔离、污染登记、trial ledger、Half Kelly、30% drawdown、10% settled-cash reserve、150% GLD-equivalent Delta 上限、人工执行和 `broker writes=0` 继续有效。

## 2. Gate A 共享边界

Gate A 不读取 option chain、carrier economics、P&L、账户或 Kelly。原终局：

```text
ENTRY_QUALIFIED_FOR_LONG_CALL_INTEGRATION
```

由以下 carrier-neutral 终局替代：

```text
ENTRY_QUALIFIED_FOR_CARRIER_INTEGRATION
```

Gate A 其余候选、输入、标签、fold、purge 和 sealed 规则不变。Gate A 未通过时，Gate B 不运行任何 carrier 参数或经济性研究。

## 3. 两张输入快照与一个决定事实

### 3.1 SignalSnapshotV1

```text
timezone = America/New_York
cutoff = 10:45:00
causal_gate = ts_event < cutoff AND ts_recv < cutoff
```

该 snapshot 只包含 Gate A 所需的方向事实、calendar/phase receipts、source/version hashes 和 completeness 状态。它不得包含 10:45:00 及之后的 market fact。

### 3.2 OptionQuoteSnapshotV1

- 捕获窗口固定为 `[10:45:00, 10:46:00) America/New_York`。
- 选择窗口内第一份通过纯数据质量资格的完整 GLD valuation input 与 Call opportunity universe。
- 数据质量只看 source qualification、identity、causality、freshness、BBO/size/flags、同步性和完整性；不得看哪张价格更便宜、spread 更窄或哪个 carrier 更优。
- quote age 上限固定为 `5 seconds`；被比较对象的 receive-time skew 上限固定为 `1 second`。
- quote 必须有正 bid/ask、`bid < ask`、正 displayed size、合格 flags 和完整 identity。对拟议数量 `q`，每条腿 displayed size 必须 `>= q`；否则 liquidity capacity 限制 q 或产生 `SIZE_INSUFFICIENT`。
- 一分钟内只允许因技术性不完整重试；10:46 前无合格快照则为 `OPTION_SNAPSHOT_WINDOW_EXHAUSTED`。

### 3.3 DecisionArtifactV1

最终 artifact 至少绑定：

```text
signal_snapshot_sha256
option_quote_snapshot_sha256
research_contract_sha256
delta_model_sha256
fee_schedule_sha256
rule_package_version
terminal_state
reason_codes
```

事实字段只能由确定性程序生成。LLM 可被完全删除；即使未来增加说明文字，也不得修改 artifact 的状态、合约、数量、数值或条件。

人工操作完成后的 broker reconciliation 仍使用独立 `Snapshot B`。Snapshot B 不得覆盖或回填上述输入快照。

## 4. Carrier packages

主比较只包含：

1. `LC0_LONG_CALL_NO_ROLL`；
2. `BCS0_DEBIT_CALL_SPREAD_NO_ROLL`。

下游独立 challenger：

3. `LC1_LONG_CALL_ONE_ROLL_UP`。

`LC1` 不进入 `LC0` 对 `BCS0` 的主比较。BCS 没有 roll package。

## 5. BCS deterministic selector

选择顺序不可交换：

1. 先用 Long Call selector 产生唯一 `LC0` Call；失败则 `LC0` 与 `BCS0` 均无合格结构。
2. BCS long leg identity 必须逐字段等于该 `LC0` Call。
3. short Call 必须：同 expiry、严格更高 strike、同 multiplier/deliverable/currency、Call、standard/unadjusted。
4. short Call 本地 causal Delta 必须在闭区间 `[0.20, 0.30]`，且严格小于 long Delta。
5. short winner 的唯一排序：`abs(delta - 0.25)` 升序、strike 降序、OCC identity ASCII 升序。
6. 必须满足 `0 < all_in_entry_debit < (short_strike - long_strike) × multiplier`。

任何身份、Delta、报价、费用或唯一排序输入缺失都 fail closed；不得 fallback 到 broker Greek、moneyness、邻近 strike 或另一报价来源。

## 6. BCS fill and cost

所有期权价格为每股美元报价，费用为完整组合美元金额。

```text
entry_debit = (long_ask - short_bid) × multiplier + all_entry_fees
exit_credit = (long_bid - short_ask) × multiplier - all_exit_fees
net_pnl = exit_credit - entry_debit
max_loss = entry_debit
theoretical_expiry_max_profit = width × multiplier - entry_debit
```

`theoretical_expiry_max_profit` 仅是到期 payoff cap 诊断，不是 H20 或提前退出时可实现收益。

Base 禁止 midpoint、last、theoretical price 或 price improvement。Adverse stress 对四个 side 分别施加至少一枚 tick：entry long ask 上调、entry short bid 下调、exit long bid 下调、exit short ask 上调；不得先净额后只加一枚 tick。

决策卡只能描述一个原生 `1:1` vertical/combination 人工订单，不得生成逐腿下单指令。

## 7. BCS lifecycle

BCS 状态：

```text
PROPOSED -> MANAGED -> HOLD | EXIT_DUE -> CLOSING -> CLOSED
```

优先级：

```text
latched EXIT_DUE > policy full exit > expiry safety
> Gate A invalidation > H20 maximum hold > hold
```

- BCS 不 roll，不调整单腿 strike，不延长 expiry 或 H 时钟。
- 退出必须处理完整组合；不假设 partial fill。
- partial fill、残腿、assignment、exercise、contract adjustment、identity mismatch 或 broker unknown 都进入 `RECONCILIATION_BLOCKED`，阻断新开仓直到 Snapshot B 证明终局组合状态。
- 无 executable exit quote 时保持 latched `EXIT_DUE_NONEXECUTABLE`，不得假定平仓。

## 8. Fair comparison and risk

`LC0` 与 `BCS0` 必须共享 Gate A Entry、episode/cohort、已先选择 long-leg identity、expiry、decision clock、exit clock、fold、cost stress 和 coverage denominator。

- 每个 carrier 单独计算净收益分布、uncertainty set、robust/full/half Kelly、max loss、planned loss、Delta、cash 和 liquidity capacity。
- BCS net Delta 为 `long_delta - short_delta`；不能只看 long leg Delta。
- 任一事实未知或冲突为 `NO_DECISION`；quantity=0 是合法终局。
- 两个 carrier 都通过时为 `OWNER_SELECTION_REQUIRED`；不得运行时自动切换。
- 一个 carrier 的报价失败不得 silent fallback 到另一个 carrier。

BCS 计一个完整新增 carrier trial：`N_carrier_additional = 1`。任何第二个 BCS short-Delta target、不同 width、不同 expiry、ratio、roll 或动态 selector 都属于新 generation，不能塞入本 trial。

## 9. Quote-source qualification and notification

IBKR 只有在一次完整 snapshot 中可证明以下字段后，才能生成 IBKR 人工决策卡：完整 Call-chain discovery、双边 BBO/size、contract identity、market-data entitlement/type、统一 snapshot id 或可验证 receive times、fee schedule 和 source/version receipt。

逐腿无同步证明的顺序查询不合格。IBKR 不合格或无 executable quote 时：

```text
terminal_state = NO_DECISION
reason_code = IBKR_EXECUTABLE_QUOTE_UNAVAILABLE
notification_channel = LOCAL_BLOCKING
broker_order_count = 0
```

Robinhood 只作 read-only shadow evidence。不得跨 broker 拼腿、择优价源、把 Robinhood price 标成 IBKR executable price，或因 shadow quote 存在而清除 `NO_DECISION`。

历史研究仍要求 Databento OPRA CMBP-1 或等价的 point-in-time 双腿 BBO/size/identity/lifecycle 数据。任何下载前必须先取得精确 query cost，并由 Owner 另行批准；本合同不授权连接或下载。

## 10. Local Delta path

唯一权威模型为 `GLD_CALL_DELTA_CRR_AM_V1`，详见独立 hash-bound model contract。BSM 和 broker Greeks 只作 shadow diagnostic，不参与 PASS/FAIL 或 selector。模型失败不得 fallback。

## 11. Terminal states

合同级：

```text
NOT_RUN
DATA_NOT_QUALIFIED
INSUFFICIENT_EVIDENCE
NO_PROMOTABLE_CARRIER
OWNER_SELECTION_REQUIRED
QUALIFIED_RULE_PENDING_OWNER_DECISION
```

实时 artifact 级至少包括：

```text
NO_DECISION
LONG_CALL_DECISION_ELIGIBLE
BCS_DECISION_ELIGIBLE
OWNER_SELECTION_REQUIRED
RECONCILIATION_BLOCKED
```

不存在“最接近通过”。任何 PASS 不产生 broker、Plugin、交易或发布权限。

## 12. 当前允许与阻塞项

当前只允许本地文档、synthetic fixtures、纯函数、hash、单元测试和 fail-closed 集成测试。继续禁止 outcome/P&L、真实数据、Databento/broker 连接、订单和部署。

以下仍未冻结，因此继续阻止 provider query 和 outcome access：每日 management checkpoint 精确时刻、invalidation checkpoint 语义、`Q_path`、effective-dated fee source、clean sealed/forward 区间及最终总 trial budget。
