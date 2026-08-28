# GLD Long Call Owner Decision OD-05 Receipt

Created: 2026-08-28 09:58 Asia/Shanghai

状态：`OD05_CAPTURED / CUTOFF_FROZEN / STRUCTURE_SCOPE_QUESTION_OPEN`

## Owner decision

- `OD-05 = 5A_SINGLE_1045_ET_CUTOFF`
- Timezone：`America/New_York`
- Fast-confirmation observation window：`[09:31:00, 10:45:00)`
- Decision cutoff：`10:45:00`
- Last eligible completed 1-minute bar：`[10:44:00, 10:45:00)`
- Clock trials consumed：`1`

## Owner reasoning captured

当前目标是以两周以上为典型持有尺度的 medium-swing position，因此相对 10:15 晚 30 分钟可以接受；Owner 更重视降低开盘阶段噪声对当天行动判定的影响。

这只是研究设计理由，不是 10:45 已经产生更高收益或更优成交的证据。

## Deterministic behavior

- cutoff 不因宏观事件、盘前涨跌或人工判断临时移动。
- 只允许 `ts_event < cutoff AND ts_recv < cutoff` 的已资格事实。
- cutoff 后才收到的数据不能回填本次 decision snapshot。
- session、phase、halt/reopen、completeness 或 source authority 未证明时输出 `NO_DECISION`，不得改用 10:15、前收盘或其他 fallback clock。

## Non-meaning

OD-05 不冻结慢趋势、快确认公式、阈值、DTE、Delta/strike、数量、退出或 roll 条件；不授权 outcome/P&L、Databento、broker 或交易。

OD-05 本身也不改变当前 Research Contract 的 `Long Call only` 结构范围。Owner 已询问当前是否只有 Long Call，因此在继续结构经济性研究前，必须明确说明当前范围及是否需要重新打开 carrier universe。

