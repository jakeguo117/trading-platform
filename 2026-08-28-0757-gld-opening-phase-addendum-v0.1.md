# GLD Underlying Opening Phase Addendum v0.1

Created: 2026-08-28 07:57 Asia/Shanghai

状态：`BOUND_TO_OD04 / IMPLEMENTATION_RULE_FROZEN`

本 addendum 扩展 `GLD Underlying Normalization Contract v0.1`，只冻结 opening phase 处理：

```text
regular_open_local = XNYS calendar value
opening_transition_start = regular_open_local
opening_transition_end = regular_open_local + 1 minute
interval_semantics = [start, end)
market_phase = OPENING_TRANSITION_UNCLASSIFIED
fast_confirmation_eligible = false
```

09:31:00 及之后的记录仍必须通过 calendar、timestamp、halt/reopen、duplicate、completeness 和 causal availability gates，不因时间达到 09:31 自动升级为 `CORE_CONTINUOUS`。

验收必须证明：

1. 09:29:59.999999999 不进入 opening-transition；
2. 09:30:00.000000000 进入 opening-transition；
3. 09:30:59.999999999 进入 opening-transition；
4. 09:31:00.000000000 不进入 opening-transition；
5. DST、holiday、half-day 不改变该 local-time 半开区间语义；
6. 该区间的任何 record/bar 都不能被 fast-confirmation consumer 读取。

