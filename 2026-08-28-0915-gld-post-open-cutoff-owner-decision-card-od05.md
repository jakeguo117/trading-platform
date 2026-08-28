# GLD Post-open Cutoff Owner Decision Card OD-05

Created: 2026-08-28 09:15 Asia/Shanghai

状态：`AWAITING_OWNER_DECISION / NO_OUTCOME_ACCESS / NO_PNL_ACCESS`

## 这张卡只决定什么

固定 Gate A 在 H0 当天使用哪个 post-open cutoff 完成快确认。它不决定慢趋势公式、快信号公式、阈值、合约、数量、退出或 roll。

共同边界：

- `[09:30:00, 09:31:00) America/New_York` 已由 OD-04 排除；
- 只能使用 `ts_event < cutoff AND ts_recv < cutoff` 的因果事实；
- cutoff 本身不属于观察窗口；所有窗口均为半开区间；
- 必须消费已经闭合的 1-minute bar；
- 选择 cutoff 的依据只来自时钟、微观结构和事件时点研究，尚未查看任何 GLD outcome/P&L。

## 选项

### 5A｜10:45 ET 单一 cutoff（推荐）

- fast-confirmation window：`[09:31, 10:45)`；
- 最后一根可用完整分钟：`[10:44, 10:45)`；
- 只登记一个 clock trial；
- 取舍：比 10:15 慢 30 分钟，但给开盘价格发现和常见 10:00 ET 宏观数据发布后的吸收留出更多时间；仍不能证明它有更高收益。

### 5B｜10:15 ET 单一 cutoff

- fast-confirmation window：`[09:31, 10:15)`；
- 最后一根可用完整分钟：`[10:14, 10:15)`；
- 只登记一个 clock trial；
- 取舍：更及时，但对 10:00 ET 附近的计划事件和开盘后噪声留出的缓冲更少。

### 5C｜10:15 与 10:45 两个预注册候选

- 两个 clock 都在读取 outcome 前冻结；
- 各自完整进入 trial ledger，共计两个 trials；
- 取舍：可以用后续开发区间比较，但会消耗全部 cutoff 候选额度并增加选择偏差控制负担。

## 推荐理由与限制

推荐 `5A` 是为了先选择一个更稳健、解释更简单的时钟，而不是因为它已经显示更高回报。市场开盘后的成交与价格发现具有显著日内结构，宏观数据也可能集中在固定发布时间；因此避免把 cutoff 放在 10:00 或 10:30 这一类事件边界上更容易形成可重复语义。该判断是研究设计推断，必须由后续冻结后的 Gate A 验证，不能升级为收益证据。

## 公开依据

- [NYSE Trading Hours and Calendars](https://www.nyse.com/trade/hours-calendars)
- [Federal Reserve Bank of New York — U.S. Economic Indicators and Releases](https://www.newyorkfed.org/research/calendars/nationalecon_cal.html)
- [Admati and Pfleiderer — A Theory of Intraday Patterns](https://doi.org/10.1111/j.1540-6261.1988.tb02565.x)
- [White — A Reality Check for Data Snooping](https://doi.org/10.1111/1468-0262.00152)

## Owner response

- `5A`：10:45 ET 单一 cutoff；
- `5B`：10:15 ET 单一 cutoff；
- `5C`：两个都预注册，计两个 trials。

