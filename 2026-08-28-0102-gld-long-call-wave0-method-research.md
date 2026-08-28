# GLD Long Call Wave 0 方法研究报告
Created: 2026-08-28

状态：`METHOD_RESEARCH_COMPLETE / NO_MARKET_DATA_ACCESSED / NO_BACKTEST_RUN`

## 1. 结论摘要

Wave 0 的目标不是找出交易参数，而是决定“后面怎样研究，才不容易把偶然结果误当成规则”。三路独立研究与整合审查得到以下共同结论：

1. Entry、Long Call 合约/退出、Half Kelly 必须分阶段研究，但最后必须作为一个完整规则包验证。
2. Entry 只保留两套完整候选；不能把各自部件重新组合成更多变体。
3. Long Call 只保留一个不可晋升 control 与两个可晋升候选；同到期 roll-up 是独立 challenger。
4. Half Kelly 是 Jake 的风险偏好，但 robust full Kelly 的估计器、置信方法和输入分布仍须验证。
5. Kelly 必须逐时间段估算并冻结，再应用于下一时间段；同一 OOS 数据不能既估算 Kelly 又证明 Kelly 有效。
6. 旧 74 个事件和旧项目已查看结果只能作为 Development；不能重新称为 blind。
7. 最终验证必须一次性运行完整规则包；任何人看到结果后都不能在同一区间调参重跑。
8. 当前只能冻结 Research Contract 的结构。具体时钟、持有风格、样本/coverage/统计阈值，要在读取收益结果前继续确认。

## 2. Track A：什么时候买

### 2.1 公开证据能支持什么

- 自身历史收益的持续性在多类期货与远期上有实证研究，但该资产范围、频率和周期不能直接迁移给 GLD。[Moskowitz、Ooi、Pedersen](https://doi.org/10.1016/j.jfineco.2011.11.003)
- 移动趋势与交易区间突破是可以机械定义的经典规则族，但早期结果不等于 GLD 生产公式。[Brock、Lakonishok、LeBaron](https://doi.org/10.1111/j.1540-6261.1992.tb04681.x)
- 成交量可以包含价格之外的信息，但公开论文不能证明 GLD 应采用哪个 volume threshold。[Blume、Easley、O’Hara](https://doi.org/10.1111/j.1540-6261.1994.tb04424.x)
- 在大量技术规则中挑选赢家会产生 data-snooping；必须记录完整候选宇宙与失败尝试。[Sullivan、Timmermann、White](https://doi.org/10.1111/0022-1082.00163)、[White Reality Check](https://doi.org/10.1111/1468-0262.00152)

### 2.2 建议冻结的两套候选

#### A1｜自身动量 + 区间突破保持

- 慢层：只使用判断时点前已完成的 GLD 日线计算自身趋势。
- 快层：突破一个由已完成数据形成的区间边界。
- 不在首次穿越瞬间触发；只在固定判断点仍保持于正确一侧时成立。
- 成交量不作硬条件。

#### A2｜移动趋势 + 开盘窗口价格/活跃度确认

- 慢层：价格相对固定移动趋势的位置和方向。
- 快层：固定开盘窗口内的位移、收盘位置和同一时钟相对成交量共同成立。
- 价格或活跃度任一失败即不产生买点。

两者是完整候选。更改 window、clock、filter 或 threshold 都算新 trial；不得把 A1 慢层与 A2 快层重新拼装。

### 2.3 时间与事件纪律

- 日历、时区、09:30 auction 和连续交易必须分开；NYSE Arca 的 auction/market-hours 由官方资料定义。[NYSE Auctions](https://www.nyse.com/trade/auctions)、[NYSE Hours](https://www.nyse.com/markets/hours-calendars)
- 相互重叠的触发合并成一个 signal episode；episode 未结束前的再次触发只记 duplicate。
- outcome window 跨越 fold 时必须 purge；fold 边界需预留覆盖最长标签窗口的 gap。依赖时间序列的 block/hv-block 方法支持在验证块周围隔离相关样本。[Racine 2000](https://doi.org/10.1016/S0304-4076(00)00030-0)
- Track A 的标签只评价底层 GLD 路径；不得用未来 option P&L 或 quote coverage 反向筛选 Entry。

## 3. Track B：买什么、什么时候卖、是否 roll-up

### 3.1 共同原则

- Long Call 的最大损失通常是 premium，上行理论上不封顶；同时存在时间价值衰减、流动性和到期处理风险。[OIC Long Call](https://www.optionseducation.org/strategies/all-strategies/long-call)、[OCC Options Disclosure](https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document)
- Delta 会随标的、IV 和剩余期限变化，因此必须绑定 causal source/model/version；它不是永恒常数。[OIC Delta](https://www.optionseducation.org/advancedconcepts/delta)
- 期权交易成本，尤其 bid/ask spread，可能消除表面异常收益，因此研究基准必须使用 ask entry / bid exit，而不是 midpoint。[Phillips、Smith](https://doi.org/10.1016/0304-405X(80)90016-1)
- 合约的 maturity、moneyness 与收益/风险共同变化，不能各自优化后拼接。[Coval、Shumway](https://doi.org/10.1111/0022-1082.00352)

### 3.2 建议的三个完整 package

#### B0｜MAX_HOLD_CONTROL

- 只有 policy hard exit、expiry safety 和 maximum hold。
- 不含 ordinary signal invalidation，不含 roll。
- 只作 control，不能晋升。

#### B1｜SIGNAL_EXIT

- 与 B0 使用同一 Entry、合约、成本、fold 和 maximum hold。
- 增加 Gate A 冻结的 completed-close invalidation。
- 该 invalidation 同时作为 planned stop；不增加固定 option-price stop。
- 无 roll；可晋升。

Stop 是否改善结果取决于 return process，不能仅因“看起来安全”就采用。[Kaminski、Lo](https://doi.org/10.1016/j.finmar.2013.07.001)

#### B2｜SIGNAL_EXIT_ONE_ROLL_UP

- 与 B1 相同，但每个 signal episode 最多一次同到期 strike-up。
- 触发不使用固定利润率；候选方向是旧 Call 的 Delta 超出预注册 eligibility/hysteresis band，并且同 expiry 存在严格更高 strike 的 target-Delta winner。
- 旧 Call 必须先 sell-to-close；成交确认后再读取新报价和风险事实，重新批准新 Call。
- Roll 不延长原 episode、maximum hold 或 expiry。
- B1 不通过时，B2 不可晋升。

OIC 对 opening/closing transaction 的定义支持把 roll 视为两笔不同交易，而不是修改旧合约。[OIC General Information](https://www.optionseducation.org/referencelibrary/faq/general-information)

### 3.3 合约 selector 建议

- Primary selector 使用 causal Delta；moneyness 只作 eligibility、tie-break 和诊断。
- Expiry 选择最早一个其 expiry-safety 仍覆盖 Gate A maximum-hold 的合格到期日，不另外优化 target DTE。
- 已选第一名报价失效时，该样本/决定不可执行；不能 silent fallback 到第二名。
- Standard/unadjusted、multiplier、deliverable、last-trading-date 和 American-style exercise 语义必须来自权威合约定义。[OCC ETF Options](https://www.theocc.com/clearance-and-settlement/clearing/etf-options)

0.50 Delta 可以作为一个待 Jake 批准的研究基准，但不是已证明最优参数。精确 target、band、quote age、spread、size 和 hysteresis 必须在读取 P&L 前冻结。

### 3.4 执行与退出状态

建议状态：

`MANAGED → HOLD | ROLL_REVIEW | EXIT_DUE → CLOSING → CLOSED`

优先级：

1. 已 latched 的 EXIT_DUE；
2. policy mandated full exit；
3. expiry safety；
4. signal invalidation/planned stop；
5. maximum hold；
6. roll review；
7. hold。

Exit 一旦触发即 latch；后续 signal recovery 不能清除。Exit due 但无 executable bid 时保持 due 并阻断新开。Roll 永远不能覆盖 exit。

## 4. Track C：Half Kelly 总 GLD 风险池

### 4.1 文献与边界

- Kelly 原始准则最大化期望对数财富增长；Fractional Kelly 是增长与风险之间的偏好折中，并不存在适用于所有策略的普适 50% 最优结论。[Kelly 1956](https://doi.org/10.1002/j.1538-7305.1956.tb03809.x)、[MacLean 等](https://doi.org/10.1287/mnsc.38.11.1562)
- Jake 已决定使用 Half Kelly，因此研究职责是先得到合格、稳健的 full Kelly，再取 50%；不能用 Half Kelly 修复负 edge 或不合格证据。
- Fractional Kelly 不能替代明确 drawdown 约束；风险约束和增长目标必须同时验证。[Busseti、Ryu、Boyd](https://web.stanford.edu/~boyd/papers/pdf/kelly.pdf)
- 对分布不确定性的 robust Kelly，可在训练数据形成的不确定集合上最大化最坏情形 expected log growth。[Sun、Boyd](https://web.stanford.edu/~boyd/papers/pdf/robust_kelly.pdf)

### 4.2 逐 fold 估计合同

1. 内层 training 只包含 cutoff 前已完整结束的事件。
2. 内层 rolling-origin 选择完整 rule package、uncertainty-set 方法和 estimator。
3. Outer fold 开始前封存 data/code/cost/trial/estimator hashes 和 robust/full/half Kelly。
4. Outer fold 只应用，不重估、不按结果改 size。
5. 该 outer fold 以后只能成为后续 development 历史。
6. 最终 sealed interval 永不进入任何估计或选择。

把完整选择过程放入内层、用外层评价，可避免普通交叉验证因模型选择而产生过度乐观偏差。[Cawley、Talbot](https://www.jmlr.org/papers/volume11/cawley10a/cawley10a.pdf)、[Varma、Simon](https://doi.org/10.1186/1471-2105-7-91)

### 4.3 账本语义待冻结

- protected principal、eligible bankroll、亏损吸收顺序；
- reconciled realized net profit 的 50/50 结算时点；
- isolated profit 是否继续进入 total-wealth/drawdown denominator；
- deposit/withdrawal 的 flow-adjusted HWM；
- 30% drawdown 的 wealth/HWM 口径、触发、锁定和恢复；
- held、pending、proposal、加赢家和 roll-up 的联合路径；
- max-loss、planned-loss、Delta、cash 和 liquidity 的 post-state 公式。

Drawdown 通常相对历史财富峰值定义，但具体 wealth 与 flow 语义仍需预注册。[Grossman、Zhou](https://doi.org/10.1111/j.1467-9965.1993.tb00044.x)

### 4.4 五容量

Kelly/drawdown 先形成 total-pool risk space。对每个整数 proposal quantity 分别验证：

- max-loss；
- planned-loss；
- GLD-equivalent Delta；
- post-trade settled cash/reserve；
- executable liquidity。

最终数量取五者支持的最小非负整数。任一项缺失或冲突为 `NO_DECISION`；五项完整但容量为零是合法零数量。

## 5. 反过拟合与多重检验

- 所有 Entry、selector、exit、roll、cost、Kelly、confidence、fold、drawdown 和 gate 变更都算 trial；失败、删除、Agent 自动生成和人工尝试不能省略。
- White Reality Check 检查经过规则搜索后的最佳候选是否真正优于基准。[White 2000](https://doi.org/10.1111/1468-0262.00152)
- Deflated Sharpe Ratio 针对 trial selection、非正态和样本长度校正表面 Sharpe。[Bailey、López de Prado](https://doi.org/10.2139/ssrn.2460551)
- Probability of Backtest Overfitting 可作为 development family diagnostic，但不能替代 sealed interval。[Bailey 等](https://escholarship.org/uc/item/4w1110bb)
- 最终 validation 失败后，只能等待新 forward 数据或另一个从未见过的区间；不能在原区间修改参数重跑。

## 6. 当前真正需要的 Owner 决定

### D1｜决策时点架构

- 选项 A（推荐）：前一交易日收盘数据形成慢趋势资格；当日 post-open 的一个固定 cutoff 计算快确认并形成最终卡。
- 选项 B：全部方向和买点在前一交易日收盘后形成；post-open 只取期权报价，不再确认 GLD 快信号。

Exact post-open cutoff 不需要 Jake 猜数值；若选择 A，可在结果访问前冻结最多两个候选时点，并将每个时点计入 trial ledger。

### D2｜策略持有风格

- 选项 A（推荐）：短周期 swing，以若干交易日为单位；exact maximum hold 与 result horizons 在结果访问前预注册。
- 选项 B：中周期 swing，以若干周为单位。
- 不建议两者同时研究；它们会产生不同 DTE、退出和数据路径，相当于两套策略。

### 默认方法决定

- 真正 Sealed 优先使用可证明从未看过的历史区间；若不存在，则等待 forward 数据。
- 没有 clean sealed 时，最高终局只能是 `DEVELOPMENT_PASS_PENDING_SEALED`，不能称为可实施算法。

## 7. 方法状态

- 已完成：公开方法研究、候选结构压缩、主要数据字段、时间隔离、风险账本和 terminal verdict 设计。
- 未完成：D1/D2 owner 决定、数据资格、任何数值门槛、任何收益研究或回测。
- 当前没有读取旧市场数据、Databento、IBKR、private、`.env` 或凭证。

## 8. 研究来源

1. [Kelly 1956](https://doi.org/10.1002/j.1538-7305.1956.tb03809.x)
2. [MacLean et al. growth-security analysis](https://doi.org/10.1287/mnsc.38.11.1562)
3. [Risk-Constrained Kelly](https://web.stanford.edu/~boyd/papers/pdf/kelly.pdf)
4. [Distributionally Robust Kelly](https://web.stanford.edu/~boyd/papers/pdf/robust_kelly.pdf)
5. [Time Series Momentum](https://doi.org/10.1016/j.jfineco.2011.11.003)
6. [Simple Technical Trading Rules](https://doi.org/10.1111/j.1540-6261.1992.tb04681.x)
7. [Data-Snooping and Technical Trading Rules](https://doi.org/10.1111/0022-1082.00163)
8. [White Reality Check](https://doi.org/10.1111/1468-0262.00152)
9. [Deflated Sharpe Ratio](https://doi.org/10.2139/ssrn.2460551)
10. [Probability of Backtest Overfitting](https://escholarship.org/uc/item/4w1110bb)
11. [OCC Options Disclosure](https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document)
12. [OIC Long Call](https://www.optionseducation.org/strategies/all-strategies/long-call)
13. [OIC Delta](https://www.optionseducation.org/advancedconcepts/delta)
14. [Trading Costs for Listed Options](https://doi.org/10.1016/0304-405X(80)90016-1)
15. [Expected Option Returns](https://doi.org/10.1111/0022-1082.00352)
16. [Stop-Loss Strategies](https://doi.org/10.1016/j.finmar.2013.07.001)

## 9. 方法说明

本报告由三个独立研究 Track 分别覆盖 Entry、Long Call/Exit/Roll、Half Kelly/Risk/Validation，再由整合层交叉检查。只使用当前项目公开文档、原始论文、期刊页和 OCC/OIC/NYSE 官方材料。没有使用博客结果决定任何候选或门槛。
