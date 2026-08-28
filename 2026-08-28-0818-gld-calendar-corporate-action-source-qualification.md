# GLD Calendar and Corporate-Action Source Qualification

Created: 2026-08-28 08:18 Asia/Shanghai

状态：`XNYS_SCHEDULE_QUALIFIED / ACTUAL_CALENDAR_PARTIAL / GLD_ACTION_COVERAGE_PARTIAL`

## 1. XNYS/NYSE Arca Calendar

日期固定的 NYSE/ICE 官方公告可以覆盖计划日历：

- [NYSE Group 2023–2025 holidays and early closings](https://ir.theice.com/press/news-details/2022/NYSE-Group-Announces-2023-2024-and-2025-Holiday-and-Early-Closings-Calendar/default.aspx)
- [NYSE Group 2025–2027 holidays and early closings](https://ir.theice.com/press/news-details/2024/NYSE-Group-Announces-2025-2026-and-2027-Holiday-and-Early-Closings-Calendar/default.aspx)
- [NYSE Group 2026–2028 holidays and early closings](https://ir.theice.com/press/news-details/2025/NYSE-Group-Announces-2026-2027-and-2028-Holiday-and-Early-Closings-Calendar/)

交易时段的当前官方入口：

- [NYSE Trading Hours and Calendars](https://www.nyse.com/trade/hours-calendars)
- [NYSE Rules PDF](https://www.nyse.com/publicdocs/nyse/regulation/nyse/NYSE_Rules.pdf)

但年度日历不等于实际日历。NYSE 之后单独公告 2025-01-09 因卡特全国哀悼日休市：

- [NYSE January 9, 2025 special closure](https://ir.theice.com/press/news-details/2024/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of-Mourning/default.aspx)

所以必须分开：

```text
scheduled_calendar = annual official schedule
actual_observed_calendar = scheduled_calendar + ad_hoc closure/status overlays
```

临时事件线索入口：[NYSE Trader Update History](https://www.nyse.com/TRADER-UPDATE/HISTORY) 和 [Market Status History](https://www.nyse.com/market-status/history)。最终 receipt 必须保存具体公告，不能只保存搜索页。

终局：

- `XNYS_SCHEDULE_2023_2027 = QUALIFIED`
- `XNYS_ACTUAL_SESSION_HISTORY = PARTIAL`
- 2026-08-28 之后的日期只能是 `SCHEDULED_PROVISIONAL`。

## 2. GLD Corporate Actions

官方/法定入口：

- [SPDR Gold Shares GLD official site](https://www.spdrgoldshares.com/usa/gld/)
- [SEC EDGAR — SPDR Gold Trust, CIK 1222333](https://www.sec.gov/edgar/browse/?CIK=1222333)
- [GLD official prospectus](https://www.ssga.com/us/en/institutional/library-content/products/fund-docs/etfs/us/ps/SPDR_GOLD_TRUST_PROSPECTUS.pdf)
- [2026-06-30 GLD 10-Q](https://www.sec.gov/Archives/edgar/data/1222333/000143774926025680/gld20260630_10q.htm)

年度税务资料：

- [GLD 2023 Tax Information](https://files.spdrgoldshares.com/spdr-cms/2025-11/SPDR-Gold-Trust-Tax-Information-2023.pdf)
- [GLD 2024 Tax Information](https://files.spdrgoldshares.com/spdr-cms/2025-11/SPDR-Gold-Trust-Tax-Information-2024.pdf)
- [GLD 2025 Tax Information](https://files.spdrgoldshares.com/spdr-cms/2026-01/SPDR-Gold-Trust-Tax-Information-2025.pdf)

目前可安全冻结的口径：

- 必须将 `TRUST_EXPENSE_GOLD_SALE` 单独记录；它不是现金股息，也不是拆股调整。
- AP 日常申购赎回和 shares outstanding 变化不自动当作公司行动。
- 无股息、无现金分配、无拆股/合股不得合并成一个笼统 `NO_ACTION`；必须按类别和封闭日期区间分别证明。
- 2024 tax PDF 本轮只确认文件入口，未冻结正文结论。
- 没有找到 `split` 字样不等于无拆股证明；必须完成 EDGAR accession 和交易所行动通知的封闭清单。

终局：`GLD_ACTION_COVERAGE = PARTIAL`。

## 3. 本地 Receipt 必需字段

Calendar receipt：

```text
market_mic, related_market, coverage_start/end, as_of_utc
timezone, tzdb_version
source_class, official_url, published/retrieved/effective times, SHA-256
scheduled_sessions_hash, ad_hoc_overlay_hash, actual_sessions_hash
unresolved_future_dates, terminal_state
```

GLD action receipt：

```text
ticker=GLD, CIK=0001222333, exchange=NYSE_ARCA
source_class, official_url, accession, report period, filed/retrieved time, SHA-256
action_class = CASH_DISTRIBUTION | DIVIDEND | SPLIT | REVERSE_SPLIT
             | TRUST_EXPENSE_GOLD_SALE | CUSIP_CHANGE
state = POSITIVE_EVENTS_BOUND | NO_ACTION_COVERAGE | PARTIAL
      | UNRESOLVED | FUTURE_UNPROVABLE
```

当前只完成了公开官方来源资格研究，尚未下载或保存源 payload，也未生成最终本地 calendar/action ledger。

