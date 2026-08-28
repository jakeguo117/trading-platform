# GLD Post-open Cutoff Addendum v0.1

Created: 2026-08-28 09:58 Asia/Shanghai

状态：`BOUND_TO_OD05 / CLOCK_RULE_FROZEN / NO_OUTCOME_ACCESS`

本 addendum 只冻结 Gate A H0 快确认的时钟语义：

```text
timezone = America/New_York
opening_transition_excluded = [09:30:00, 09:31:00)
fast_confirmation_window = [09:31:00, 10:45:00)
decision_cutoff = 10:45:00
last_complete_1m_bar = [10:44:00, 10:45:00)
causal_gate = ts_event < cutoff AND ts_recv < cutoff
clock_trial_count = 1
```

## Fail-closed rules

1. `10:45:00` 是固定 local exchange time，不因 DST、新闻日、gap 或盘前走势漂移。
2. 生成卡片可以发生在 cutoff 之后，但本次 Snapshot A 不得消费 cutoff 时刻及之后才 event/receive 的事实。
3. 任何未闭合 bar、late receive、halt/reopen、未知 phase、日历冲突或 coverage 缺口都不能被 LLM 补全。
4. 不允许 silent fallback 到 10:15、09:31、前收盘或最终修订数据。
5. 10:45 只是时钟边界，不代表该时刻一定存在 Entry；未通过后续规则时保持现金或 `NO_DECISION`。

## Acceptance boundaries

- 09:30:59.999999999 不可进入窗口；
- 09:31:00.000000000 可以进入窗口，但仍须通过全部资格门；
- 10:44:59.999999999 可以进入窗口；
- 10:45:00.000000000 不可进入窗口；
- 相同数据版本、规则版本、calendar/phase receipts 与 cutoff 必须得到相同结构化事实。

