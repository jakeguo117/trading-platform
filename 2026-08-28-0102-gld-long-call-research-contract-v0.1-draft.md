# GLD Long Call Research Contract v0.1 Draft
Created: 2026-08-28

状态：`OWNER_DECISIONS_CAPTURED / DATA_QUALIFICATION_ALLOWED / NO_OUTCOME_ACCESS`

## 1. 合同目的

本合同只约束 GLD Long Call 的后续研究程序。它不产生当前交易规则、决策卡、合约、数量、Plugin、broker payload 或交易权限。

只有完成本合同、数据资格、Gate A、Gate B、Half Kelly 风险研究和 sealed validation，并经 Jake 接受 exact rule-package hash 后，规则才有资格进入工程实施规划。

## 2. 已确认偏好

- GLD first；只顺势做多；弱势或不清楚时全现金。
- 慢趋势决定做多资格，快确认决定是否行动。
- 最终结构只研究 Long Call。
- Half Kelly 管整个 GLD 风险池。
- 已实现净利润 50% 再投入、50% 隔离；浮盈不扩容。
- post-trade settled cash ≥10% NLV。
- absolute GLD Delta notional ≤150% eligible bankroll。
- 最大可接受 drawdown 30%。
- 不固定止盈；不向下摊平。
- 同到期 strike-up roll 只作独立 challenger：旧仓完整退出、新仓重新批准；最多一次，不延长 episode、maximum hold 或 expiry。
- 决策时点采用 `A_POST_OPEN_CONFIRM`：前一交易日收盘数据形成慢趋势资格，当日 post-open 固定 cutoff 完成快确认。
- 持有风格采用 `B_MEDIUM_SWING`：以若干周为单位，不以若干交易日的短周期为主研究范围。
- Jake 人工执行；broker writes=0。

## 3. Owner 决定卡

### OD-01｜决策时点架构

- **已选择：`A_POST_OPEN_CONFIRM`。** 前一交易日收盘数据形成慢趋势资格；当日 post-open 固定 cutoff 计算快确认并形成最终方向结论。
- `B_PRIOR_CLOSE_SIGNAL`：全部方向和买点在前一交易日收盘后形成；post-open 只取期权报价。

exact cutoff 在数据资格后、读取 P&L 前，从最多两个预注册时点中研究；每个时点进入 trial ledger。

### OD-02｜策略持有风格

- `A_SHORT_SWING`：以若干交易日为单位；本轮不选择。
- **已选择：`B_MEDIUM_SWING`。** 以若干周为单位。

本轮不同时研究两种风格。exact result horizons、maximum hold 和 expiry safety 必须在读取 P&L 前预注册；目标 DTE 必须覆盖 maximum hold 及 safety buffer。同到期 strike-up roll 不得延长 episode、maximum hold 或 expiry。

## 4. Track A 合同

### A 候选总数

恰好两套完整候选：

1. `A1_OWN_MOMENTUM_RANGE_HOLD`
2. `A2_MOVING_TREND_OPENING_ACTIVITY_CONFIRM`

禁止部件重组；任何 clock/window/filter/threshold 变化都是新 trial。

### A 输入边界

- 只使用 decision cutoff 前可得的 GLD 身份、日频/分钟 OHLCV、交易日历、corporate-action 和时间语义。
- 期权链、option P&L、Delta、DTE、账户和 Kelly 不得进入 Entry 标签或选择。
- 09:30 auction 与连续交易必须分开。

### A 事件与标签

- 重叠触发合并为一个 signal episode。
- Episode 未结束前的重复触发只记 duplicate。
- Re-entry 只能在 episode 结束和冻结 cooldown 后发生。
- 标签包含预注册 horizon 的 terminal return、MFE、MAE、early failure 和 invalidation time。
- 标签字段不能进入当日输入。

### A 时间隔离

- Development：可用于公式和阈值研究。
- Evaluation：两候选冻结后各运行一次，按预注册规则选择最多一个。
- Sealed：由未参与调参的 Agent 对唯一候选运行一次。
- Outcome window 重叠 fold 时 purge；fold gap 至少覆盖最长标签窗口。

### A 终局

- `DATA_NOT_QUALIFIED`
- `ENTRY_INSUFFICIENT_EVIDENCE`
- `ENTRY_HYPOTHESIS_REJECTED`
- `DEVELOPMENT_PASS_PENDING_SEALED`
- `ENTRY_QUALIFIED_FOR_LONG_CALL_INTEGRATION`

Gate A 未通过时不得运行 Gate B 参数研究。

## 5. Track B 合同

### B packages

1. `B0_MAX_HOLD_CONTROL`：不可晋升 control。
2. `B1_SIGNAL_EXIT`：Gate A invalidation 作为 planned stop；无固定 option-price stop，无 roll。
3. `B2_SIGNAL_EXIT_ONE_ROLL_UP`：B1 + 最多一次同到期 strike-up；B1 未通过时 B2 不可晋升。

B0/B1/B2 共享同一 Entry、cohort、contract selector、cost、fold、maximum hold 和 denominator。

### B selector

- Primary selector：causal Delta。
- Moneyness：eligibility、tie-break 和诊断；不能 silent fallback。
- Expiry：选择最早一个其 expiry-safety 仍覆盖 Gate A maximum hold 的合格到期日。
- Exact Call identity、standard/unadjusted、multiplier、deliverable、last-trading-date、bid/ask/size、causal Delta model/version 必须完整。
- Target Delta、eligibility band、quote age、receive skew、spread、size 和 hysteresis 在读取 P&L 前冻结。

### B fill/cost

- Entry：ask × multiplier + all entry fees。
- Exit：bid × multiplier − all exit fees。
- Roll：old bid credit − old exit fees − new ask debit − new entry fees。
- Base 不使用 midpoint、last、theoretical price 或假定 price improvement。
- Stress 至少包含每 side 一枚 adverse tick；更大 stress 在数据资格后冻结。
- Stale/crossed/zero-bid/size不足为 `NONEXECUTABLE`，事件继续留在 coverage denominator。

### B lifecycle

状态：`MANAGED → HOLD | ROLL_REVIEW | EXIT_DUE → CLOSING → CLOSED`

优先级：

1. latched EXIT_DUE；
2. policy full exit；
3. expiry safety；
4. signal invalidation/planned stop；
5. maximum hold；
6. roll review；
7. hold。

Exit 触发后 latch；signal recovery 不得清除。Exit due 无 executable bid 时保持 due、阻断新开。Roll 不得覆盖 exit。

### B roll challenger

- 旧 Call Delta 超出预注册 eligibility/hysteresis band，且同 expiry 存在严格更高 strike 的 target-Delta winner，才进入 roll review。
- 旧仓先完整关闭；确认后使用第二份 fresh snapshot 重新审批新 Call。
- Roll 后重新计算 held/pending/proposal、Half Kelly、cash、Delta 和五容量。
- 主比较 denominator 是全部 B1 scorable episodes；roll-triggered subset 只作机制诊断。

### B 终局

- `NOT_RUN_NOT_AUTHORIZED`
- `INSUFFICIENT_EVIDENCE`
- `NO_PROMOTABLE_TRACK_B_PACKAGE`
- `SIGNAL_EXIT_ELIGIBLE_NO_ROLL`
- `SIGNAL_EXIT_AND_ROLL_ELIGIBLE`
- `OWNER_SELECTION_REQUIRED`

## 6. Track C 合同

### C Kelly unit

Kelly unit 永远是 total GLD pool，不是单笔交易。

### C robust Half Kelly

- Robust full Kelly 对 training uncertainty set 的 worst-case expected log growth 求解。
- Full Kelly 合格后取 50%。
- 负 edge、不稳定或置信不足时新增数量为零。
- Half Kelly 不替代 30% drawdown hard boundary。

### C nested estimation

1. Inner training 只含 cutoff 前已闭合事件。
2. Inner rolling-origin 选择完整 package、uncertainty method 和 estimator。
3. Outer fold 前封存 estimator 与 robust/full/half Kelly hashes。
4. Outer fold 只应用、不重估。
5. Sealed 永不进入 estimator 或选择。

### C ledger semantics to freeze before sizing

- protected principal、eligible bankroll、loss absorption；
- reconciled realized net profit 的 50/50 结算；
- isolated profit、total wealth 与 drawdown denominator；
- flow-adjusted HWM；
- 30% drawdown action/lock/recovery；
- held、pending、proposal、winner add 和 roll 的联合路径；
- max-loss、planned-loss、Delta、cash 和 liquidity post-state。

### C five capacities

对每个整数 q 计算 post-state，最终 quantity 取以下五个容量支持的最小非负整数：

1. Max-loss；
2. Planned-loss；
3. GLD-equivalent Delta；
4. Settled cash/reserve；
5. Executable liquidity。

任一容量未知或冲突为 `NO_DECISION`；全部完整但 quantity=0 是合法零数量。

## 7. Trial 与多重检验合同

- 所有 Entry、selector、exit、roll、cost、Kelly、confidence、fold、drawdown、gate 和 Agent 生成变体都进入不可删 trial ledger。
- Outcome 后新增候选必须进入新 generation，不得扩容原 preregistration。
- Candidate budget `N_A/N_B/N_risk/N_roll/N_total` 在数据资格后、结果访问前冻结。
- DSR、PBO、White Reality Check 或预选 SPA 只作互补诊断；不能替代 sealed validation。
- 默认禁止 sequential peeking；如需中途监测，必须有独立预注册统计合同。

## 8. 数据与时间分段合同

- 旧项目已查看区间全部进入污染登记，最多用于 Development。
- 至少三层：Gate A Development、Gate B/Risk Development、端到端 Sealed。
- 如果样本不能支持 Gate A 与 Gate B 严格分段，可使用完整 nested chronological development，但仍须独立 Sealed。
- Sealed 优先使用可证明未看过的历史区间；若不存在，则等待 forward 数据。
- 没有 clean Sealed 时最高状态为 `DEVELOPMENT_PASS_PENDING_SEALED`。

## 9. Research Contract 总终局

- `NOT_RUN`
- `INSUFFICIENT_EVIDENCE`
- `NO_QUALIFIED_RULE`
- `QUALIFIED_RULE_PENDING_OWNER_DECISION`

不存在“最接近通过”。任何 PASS 只进入 Owner Gate，不产生 Plugin、broker 或交易权限。

## 10. 当前 Gate

OD-01 与 OD-02 已确认，本合同进入 `OWNER_DECISIONS_CAPTURED / DATA_QUALIFICATION_ALLOWED`。当前只允许：

- 盘点旧数据的 metadata、schema/fields、point-in-time 完整性、日期覆盖、来源/许可和污染状态；
- 核对中期持有所需的 underlying、options、calendar、cost 和 lifecycle 字段是否存在。

当前仍然禁止：

- 读取 outcome/P&L 或运行任何回测；
- 在数据资格完成前冻结 exact Entry cutoff、result horizons、maximum hold 或 expiry safety；
- 连接 Databento 或 broker。
