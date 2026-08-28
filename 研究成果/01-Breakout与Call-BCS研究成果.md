# Breakout 与 Call/BCS 研究成果

## 1. 总结

旧项目已经完成 Breakout 事件扩展、signal repair、可执行报价配对、Call/BCS 初步比较、退出锚点修正和小样本退出方案探索。

正确结论不是“Breakout 已经有效”或“Call/BCS 已经胜出”，而是：研究方法和部分历史观察已经形成，但 Gate A、Gate B、独立 blind 和 Policy 晋升均未完成。

## 2. Breakout 事件与 signal repair

### 事件扩展

- 旧样本：35 个事件。
- 新增：39 个事件，覆盖 397 个 sessions。
- 合计：74 个事件。
- 成熟度：`COMPLETED_OBSERVATION`。
- 限制：事件数量达到目标不代表 signal 通过。

源文件：

- `plugins/gld-daily-decision-assistant/demo-output/extended-breakout-events-summary.json`

### Signal repair

- 在 74 个事件上找到多个 causal repair 候选，包括 Clean Breakout。
- 原始状态：`REPAIR_CANDIDATE_FOUND_NOT_BLIND_VALIDATED`。
- 成熟度：`NOT_BLIND_VALIDATED`。
- 限制：这是候选搜索结果，不是生产 Breakout 公式。

源文件：

- `plugins/gld-daily-decision-assistant/demo-output/breakout-signal-repair-summary.json`

## 3. Synthetic pipeline 观察

- 35 个 synthetic inputs 中：28 个 `SIMULATION_CANDIDATE`，7 个 `NO_TRADE`。
- Carrier 标签：14 Call、14 BCS。
- 订单和 broker instruction calls：0。
- 成熟度：`SYNTHETIC_ONLY`。
- 限制：只证明决策链的机械行为，不是历史有效性或 carrier 选择证据。

源文件：

- `plugins/gld-daily-decision-assistant/demo-output/synthetic-35-batch-summary.json`

## 4. 可执行报价覆盖

35 个事件在不同 freshness 窗口下的 paired-ready 数量：

| Freshness | Paired-ready | 不可评估 |
|---:|---:|---:|
| 120 秒 | 0 | 35 |
| 300 秒 | 9 | 26 |
| 600 秒 | 22 | 13 |

成熟度：`COMPLETED_OBSERVATION`。

限制：覆盖不足直接限制 Call/BCS 比较；不能只读取收益均值而忽略未覆盖事件。

源文件：

- `plugins/gld-daily-decision-assistant/demo-output/paired-event-input-grid-summary.json`

## 5. 初始 Call 与 BCS 比较

600 秒、zero-fee、22 个 paired-ready 样本：

| 指标 | Long Call | Debit BCS |
|---|---:|---:|
| Mean return on max risk | `-0.1964` | `-0.1235` |
| Positive event rate | `13.64%` | `27.27%` |

当时 BCS 相对好于 Call，但两者均为负；终局是：

`INSUFFICIENT_EXECUTABLE_QUOTE_COVERAGE_BELOW_50`

成熟度：`INSUFFICIENT_EVIDENCE`。

限制：不能据此采用 BCS，也不能把这一批结果外推到修正后的生命周期。

源文件：

- `plugins/gld-daily-decision-assistant/demo-output/extended-call-vs-bcs-comparison-summary.json`

## 6. 修正退出锚点后的比较

修正错误 anchor，并恢复五交易日 maximum hold 后，Clean Breakout 的观察发生明显变化。

### 600 秒、zero-fee

- Clean Breakout 总数：39。
- 可比较样本：17。
- Call mean return on max risk：`0.5054`。
- BCS mean return on max risk：`0.3551`。
- 两者 positive event rate：`58.82%`。
- Call worst observed return：`-0.4116`。
- BCS worst observed return：`-0.3480`。

### 300 秒、zero-fee

- 可比较样本：11。
- Call mean：`0.6047`。
- BCS mean：`0.4560`。
- 两者 positive event rate：`72.73%`。

历史观察：Call 的平均上行更高；BCS 在部分下行观察中损失更浅。

成熟度：`COMPLETED_OBSERVATION / NOT_BLIND_VALIDATED`。

限制：样本小、覆盖不完整、未 blind、未晋升 Policy；不能形成“Call 胜出”或“BCS 胜出”的最终规则。

源文件：

- `plugins/gld-daily-decision-assistant/demo-output/corrected-exit-call-vs-bcs-summary.json`

## 7. Exit overlay 探索

- 17 条可用 path 中完成 12 条。
- 60% take-profit：两种 carrier 均不值得继续。
- stop40 + profit60：两种 carrier 均不值得继续。
- stop40 单独方案：仍不确定。
- Plugin rule：保持不变。

成熟度：`EXPLORATORY / NOT_FOR_ADOPTION`。

限制：样本只有 12 条独立 paths，不足以采用固定 option-price stop 或 take-profit。

源文件：

- `plugins/gld-daily-decision-assistant/demo-output/option-overlay-exploratory-12-summary-v0.3.json`

## 8. 公平比较规则

旧设计要求：

- 只比较 `SINGLE_LONG_CALL` 与同到期、更高 strike 的 debit BCS；
- 使用相同 signal、事件、`available_at`、entry、fold 和 exit clock；
- Call 用 ask entry / bid exit；
- BCS 用 long ask - short bid entry，long bid - short ask exit；
- 费用、滑点和 adverse stress 必须完整进入比较。

实际执行终局为 0 market rows、0 paired rows、无 backtest/blind：

`INSUFFICIENT_EVIDENCE_TO_COMPARE`

成熟度：`DESIGN_ONLY / INSUFFICIENT_EVIDENCE`。

源文件：

- `side-projects/gld-breakout-single-call-vs-debit-bcs-design-v1/CANONICAL_DESIGN_CONTRACT_V1.md`
- `side-projects/gld-breakout-single-call-vs-debit-bcs-execution-v1/PAIRED_COMPARISON_PACKET_V1.json`
- `side-projects/gld-breakout-single-call-vs-debit-bcs-execution-v1/TERMINAL_COMPARISON_PACKET_V1.json`

## 9. 尚未运行的 Gate A / Gate B

候选方向：

- Gate A：验证冻结 Breakout signal 是否在 D5/D10 等预先声明时点具有足够持续性和稳健性。
- Gate B1：在相同机会中独立研究 Call/BCS 与退出 economics。
- Gate B2：只有两者各自合格后，才由 Jake 选择一个完整 carrier。

当前状态：

- 旧五交易日 preregistration：`OWNER_ACCEPTANCE_REQUIRED / NOT_RUN_NOT_AUTHORIZED`。
- 新短周期计划和 preregistration：untracked、`owner_approval_status=pending / NOT_RUN_NOT_AUTHORIZED`。
- 15-session lifecycle、D5/D10 Gate 和 successor 规则均未成为 CURRENT Policy。

源文件：

- `docs/specs/GLD_BREAKOUT_CALL_BCS_RISK_DEVELOPMENT_PREREGISTRATION_V0.1.json`
- `docs/plans/2026-08-24-1308-feat-short-cycle-breakout-mvp-plan.md`
- `docs/specs/GLD_SHORT_CYCLE_BREAKOUT_RESEARCH_PREREGISTRATION_V1.json`

## 10. 最终研究结论

1. Breakout 候选值得继续通过 Gate A 做严格验证，但尚未通过。
2. Call 与 BCS 的相对结果对 entry/exit anchor、quote coverage、成本和生命周期高度敏感。
3. 修正后的小样本倾向 Call 获得更高平均上行、BCS 缓和部分下行，但不足以选择 carrier。
4. 当前没有获准的生产 Breakout 公式、carrier selector 或退出规则。

