# GLD Long Call 算法研究与冻结计划
Created: 2026-08-28

状态：`JAKE_CONFIRMED / RESEARCH_EXECUTION_AUTHORIZED`

## 1. 目标

交付一套经过数据核验、历史研究和独立验证，能够明确回答以下四个问题的 GLD Long Call 规则包：

1. 什么时候买？
2. 买哪张 Call？
3. 买多少？
4. 什么时候卖或向上 roll strike？

终点是“已具备工程实施条件”。本计划不包含 Plugin 实施、broker 连接、订单创建或自动交易。

## 2. 已确认的偏好与边界

- 第一条完整研究链只做 GLD Long Call；SLV 延后独立验证。
- Jake 人工执行；系统不创建、修改、取消或提交 broker 订单。
- Half Kelly 管理整个 GLD 风险池，而不是每笔交易分别计算。
- 已实现净利润 50% 再投入、50% 隔离；未实现利润不扩大 eligible bankroll。
- 交易后 settled cash 至少为 NLV 的 10%；available funds 不得替代 settled cash。
- 最大绝对 GLD Delta 名义敞口为 eligible bankroll 的 150%。
- 最大可接受回撤为 30%；具体计算、缩仓和恢复算法重新验证。
- 首笔小仓、只加赢家、不向下摊平；具体可计算语义重新验证。
- 不设置固定全仓止盈线。
- 保留同到期日向上 roll strike 作为独立 challenger：完整卖出旧 Call、实现部分利润，再重新审批更高 strike 的新 Call。
- 本轮不研究延长到期日的 roll；它是未来独立课题。
- 决策时点架构已确认为 `A_POST_OPEN_CONFIRM`：前一交易日收盘形成慢趋势资格，当日 post-open 固定 cutoff 完成快确认。
- 持有风格已确认为 `B_MEDIUM_SWING`：以若干周为单位；exact horizons、maximum hold 和 expiry safety 在读取 P&L 前预注册。
- medium-swing envelope 已确认为 `A_FOUR_TRADING_WEEKS`：入场 session 记为 H0，最晚在第 20 个后续交易 session H20 退出；Gate A invalidation 可提前退出；H20 日期距 expiration date 至少 30 个日历日。
- 同到期 strike-up roll 不得重置 H0/H20 或延长 expiry safety 边界。
- opening phase 已确认为 `EXCLUDE_OPENING_TRANSITION_MINUTE`：`[09:30:00, 09:31:00) America/New_York` 整个分钟不进入 fast confirmation；只允许使用 09:31 之后已资格化的 continuous-session 事实。
- post-open cutoff 已确认为 `10:45:00 America/New_York`：fast-confirmation window 为 `[09:31:00, 10:45:00)`，最后一根可用完整分钟为 `[10:44:00, 10:45:00)`；不得因事件日临时移动 cutoff。
- 旧项目材料和数据只作候选研究输入，不自动成为当前规则或独立验证证据。

## 3. 合法终局

- `QUALIFIED_RULE`：存在通过全部关卡的完整规则包。
- `NO_QUALIFIED_RULE`：数据合格，但冻结候选全部失败。
- `INSUFFICIENT_EVIDENCE`：数据、覆盖率或未见样本不足，无法判断。
- `NOT_RUN`：所需权限、数据或前置合同尚未获得，研究未运行。

不得把证据不足包装成策略失败，也不得选择“最接近通过”的候选。

## 4. 研究阶段与待办

### R0｜冻结研究合同

在读取新结果前冻结：

- 决策时点、观察时点和结果评价时点；
- 慢趋势、快确认、合约、退出和 roll-up 的有限候选集；
- 候选总数与 trial ledger 记账规则；
- 数据字段、point-in-time 和污染区间；
- 时间顺序切分、purge/embargo、开发与最终验证边界；
- 成交、费用、spread、slippage、未成交和报价缺失模型；
- coverage、样本量、稳定性、集中度、风险和成本压力门槛；
- 停止规则和三种合法研究终局。

产物：`GLD Long Call Research Contract`。

关卡：Jake 确认 exact version/hash 后，才能读取收益结果。

### R1｜历史数据只读盘点与资格

先按研究合同列出的字段，只读盘点旧 GLD 文件夹中约六份历史数据：

- 文件身份、格式、大小、hash 和来源；
- 字段、日期范围、时区和交易日历；
- 原始数据、加工结果或已筛选事件的区别；
- source/event/available-at 时间语义；
- 缺失、重复、调整、合约身份和完整性；
- 许可、保存和后续使用边界；
- 已经查看过结果的污染日期。

底层 GLD 买点研究至少需要日频和预定判断窗口内的 OHLCV、交易日历和调整口径。Long Call 研究还需要完整合约定义、到期日、strike、Call/Put、乘数、bid/ask/size、因果 Delta、费用和 last-trade-date，以及所有可能退出/roll 时点的完整路径。

产物：数据清单、数据需求矩阵、数据资格回执和缺口表。

关卡：本地数据不足时停止，不自行连接 Databento。先向 Jake 提交精确数据范围、字段、预计成本、许可与保存请求。

### R2｜什么时候买：Gate A

重新研究：

- 什么慢趋势状态允许做多；
- 什么快信号触发当天行动；
- 如何排除孤立跳空或假突破；
- 同一 signal episode 如何避免重复开仓；
- 买点的有效期、失效条件和重新判断条件；
- 如何用固定后续窗口衡量底层 GLD 的上涨空间、下跌空间、持续性和早期失败；
- 规则是否跨连续时间段和相邻参数稳定。

使用按时间向前滚动的 development/walk-forward。旧 74 个事件和旧项目已查看区间只作开发材料，不能重新称为 blind。

产物：事件母集、时间切分、完整 trial ledger、Entry 研究报告，以及最多一个冻结 Entry 候选。

关卡：Gate A 不通过时，不优化期权 DTE、strike 或退出参数，终局为 `NO_QUALIFIED_RULE` 或 `INSUFFICIENT_EVIDENCE`。

### R3｜买什么和什么时候卖：Gate B

只比较少量、预先声明的完整联合规则包，不分别挑最优参数后拼接。联合研究：

- 主合约 selector：DTE、Delta/moneyness、strike 和唯一 tie-break；
- 标准、未调整合约身份和到期安全；
- quote freshness、bid/ask、size/depth、spread、费用与 adverse slippage；
- DTE × maximum hold × expiry buffer；
- Delta/moneyness × premium × Half Kelly/cash/Delta 容量；
- signal invalidation、planned stop、gap、最大持有和退出 precedence；
- 无固定止盈条件下的利润保护；
- 应退出但报价缺失时保持 exit-due，并停止新增风险。

禁止用 midpoint、last、理论价、无限 forward fill 或邻近 expiry/strike 补齐缺失报价。缺失事件继续留在 coverage denominator。

产物：Long Call 联合研究合同、合约 selector 规格、退出生命周期状态表、成本模型、按时间向前滚动结果和完整 OOS trade ledger。

### R3B｜同到期日 Roll-up Challenger

Roll-up 与基础 Long Call 生命周期分开验证：

- 完整卖出旧 Call；
- 重新检查趋势、signal episode、剩余 DTE、报价、成本和风险容量；
- 按确定性 selector 选择更高 strike；
- 比较继续持有、直接退出和 roll-up 的 after-cost 净改善；
- 明确 roll 是否允许在同一 signal episode 中形成新的受批仓位；
- 不允许向后延长 expiry。

产物：Roll-up challenger 报告和独立接受/拒绝结论。

关卡：不因基础策略通过而自动启用 roll-up。

### R4｜买多少：Half Kelly 总 GLD 风险池

先冻结可计算语义：

- protected principal、eligible bankroll、50/50 利润结算时点；
- flow-adjusted high-water mark、30% drawdown 分母、触发与恢复；
- 150% GLD Delta 聚合公式；
- held positions、pending orders、proposal、加赢家和 roll-up 的共用风险预算；
- max-loss、planned-loss、cash/reserve 和 liquidity 容量。

Kelly 必须逐时间段估算：每个 fold 只使用此前数据估算并冻结，再应用到下一 fold；最终密封验证使用验证前已冻结的 Kelly。不能用同一份 OOS ledger 既估算 Kelly 又证明 Kelly 有效。

最终风险容量：

1. 估算合格、稳健的 full Kelly；
2. 取 50%；
3. 与 drawdown capacity 结合；
4. 聚合 held + pending + proposal；
5. 再受 max-loss、planned-loss、Delta、cash 和 liquidity 五容量限制；
6. 输出最小非负整数数量。

Kelly 为负、不稳定或置信不足时，新增数量为零。事实或规则缺失时为 `NO_DECISION`，不能猜测数量。

产物：逐 fold Kelly 账本、组合风险账本、Half Kelly 风险报告和整数 sizing 规则。

### R5｜密封验证与压力测试

至少分为三层时间区间：

1. Gate A Entry development；
2. Gate B 联合策略 development；
3. 最终端到端 sealed validation。

用于选择 Entry、合约、退出、roll 或 Kelly 的区间均不得再作为最终验证。最终验证一次性运行冻结的完整规则包：

- 不调参、不增删指标、不改变成本；
- 记录所有失败、缺失和不可成交事件；
- 检查成本/滑点恶化、深度下降、gap、Delta 误差、pending/partial fill、连续亏损和 drawdown 路径；
- 检查相邻参数、年份、事件和 fold 集中度；
- 独立复算关键指标和 artifact hash；
- 失败后只能等待新的 forward 数据或另一个未见区间，不能在原 validation 区间重跑新版本。

产物：sealed validation receipt、压力测试报告和独立复算回执。

### R6｜Jake 审核与规则冻结

交付：

- 一份普通中文规则说明；
- 一份机器可读但不连接 broker 的决策表；
- 数据、成本、方法、候选、时间切分和证据 hashes；
- `可以买 / 今天不买 / 无法判断` 的完整条件；
- 合约、数量、退出和 roll-up 的确定性规则；
- 所有失败候选、局限和未决项；
- `QUALIFIED_RULE`、`NO_QUALIFIED_RULE` 或 `INSUFFICIENT_EVIDENCE` 终局。

只有 Jake 接受 exact rule-package hash 后，规则才具备进入 Plugin 实施计划的资格。到此停止，不写 Plugin。

## 5. 多 Agent 执行波次

每一波最多使用 3 个研究 Agent，主 Agent 只负责冻结共同口径、整合和冲突审计。

### Wave 0｜研究合同

- Agent A：Entry 假设、事件定义和结果标签。
- Agent B：Long Call 合约、退出和 roll-up 完整候选包。
- Agent C：Half Kelly、数据切分、成本、指标和防过拟合设计。
- Integrator：限制候选总数，统一样本、时钟、成本、denominator 和停止规则；交 Jake 决定。

### Wave 1｜数据资格

- Agent A：GLD 日频/分钟价格、成交量和交易日历。
- Agent B：期权链、security definition、bid/ask、Delta 和完整退出/roll 路径。
- Agent C：point-in-time、许可、污染、缺失和可重放审计。
- Integrator：只发布资格回执；不读取策略收益。

### Wave 2｜Gate A Development

- 一个 Agent 运行冻结 Entry 候选。
- 一个 Agent 审计时间泄漏、事件重叠和 coverage。
- 一个 Agent 独立复算指标。
- Integrator 只能按预注册规则选择最多一个 Entry 候选。

### Wave 3｜Gate B 与 Risk Development

- Agent A：冻结合约 selector 与完整路径计算。
- Agent B：退出与 roll-up challenger 路径计算。
- Agent C：成本、coverage、Kelly 和风险账本审计。
- Integrator：只比较预注册的完整规则包；不得跨 Agent 拼装事后最优组件。

### Wave 4｜Sealed Validation

- Runner Agent 只运行 frozen rule package。
- Verifier Agent 独立复算和校验 hashes。
- Audit Agent 检查污染、coverage、成本和 terminal verdict。
- Integrator 在完整 receipt 生成前不读取验证结果；读到结果后也不能在同一区间修改规则。

## 6. 执行纪律

- 所有候选、失败和修改都进入 trial ledger；多个 Agent 不增加未登记试验次数。
- 同一输入、同一数据版本和同一规则版本必须得到相同结构化结果。
- 旧研究和旧参数只作 candidate clue；不继承生产权威。
- 数据不足、规则不完整或资格失败时返回明确 terminal verdict，不让 LLM 补数。
- 本地历史数据的读取范围以 R0/R1 合同为准。
- Databento API、凭证、下载、订阅、付费、许可承诺和原始数据保存需要 Jake 后续精确授权。
- 真实 IBKR 账户、当前持仓、市场数据、What-If、Plugin 安装、Git、部署、发布和交易均不在本计划当前执行权限内。

## 7. 完成标准

研究阶段只有同时满足以下条件才完成：

- 数据资格与污染边界可证明；
- 四个交易问题都有确定性、版本化规则或明确失败原因；
- Gate A、Gate B、Half Kelly 和风险账本使用一致的事实、时钟、成本和 denominators；
- 最终验证没有未来泄漏、事后调参或静默删样本；
- 结果支持重放、独立复算和 hash 核验；
- Jake 对最终 terminal verdict 和 exact rule-package hash 做出接受、修订或拒绝决定；
- 没有写 Plugin、连接 broker 或产生交易动作。
