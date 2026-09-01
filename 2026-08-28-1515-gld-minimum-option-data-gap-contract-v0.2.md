# GLD Minimum Option Data Gap Contract v0.2

Created: 2026-08-28 15:15 Asia/Shanghai

状态：`OD06_BOUND / DUAL_CARRIER_GAPS_DEFINED / NO_ACQUISITION_AUTHORITY / NO_OUTCOME_ACCESS`

## Parent and bindings

| Artifact | Role | SHA-256 |
|---|---|---|
| [Minimum Option Data Gap Contract v0.1](/Users/jake/Desktop/Trading%20platform/2026-08-28-0739-gld-minimum-option-data-gap-contract-v0.1.md) | Parent | `f1778e47bfc94d6a04b88a13ef554cdf37e9b9b9d800273c256887e7db283e03` |
| [GLD Research Contract v0.2](/Users/jake/Desktop/Trading%20platform/2026-08-28-1515-gld-research-contract-v0.2.md) | Carrier and snapshot rules | `3940c27b4aec3585b9aa904a96f2118178589fafdcee46412441431ff9fcb8fc` |
| [OD-06 Receipt](/Users/jake/Desktop/Trading%20platform/2026-08-28-1515-gld-dual-carrier-owner-decision-od06-receipt.md) | Owner decision | `f16d8a44ba3b72a81db335fd52c440cfe78c15c5e9004151d074bb0a0fd5f5a7` |
| [GLD Call Delta CRR Model Contract v0.1](/Users/jake/Desktop/Trading%20platform/2026-08-28-1515-gld-call-delta-crr-model-v0.1.md) | Delta model | `06a4e216aa0a0f0c2e7192027ac5a19d288f799d32da59bf632bb26e7678caa8` |

## 1. Version relationship

本合同继承 Minimum Option Data Gap Contract v0.1，并只增加 `LC0` 与 `BCS0` 双 carrier 所需的同步报价、成本、生命周期和来源资格。v0.1 的 H0..H20、expiry safety、PIT opportunity universe、coverage denominator、hash integrity、sealed isolation 和 fail-closed 规则继续有效。

## 2. Real-time OptionQuoteSnapshotV1

每个 snapshot candidate 必须记录：

```text
snapshot_id
signal_snapshot_sha256
window_start_utc_ns
window_end_utc_ns
candidate_ordinal
capture_started_utc_ns
capture_completed_utc_ns
selected_at_utc_ns
source_id, source_version, entitlement_or_market_data_type
unified_snapshot_id_or_per_record_event_receive_times
GLD BBO/size/flags/identity
complete eligible Call universe BBO/size/flags/identity
source_receipt_sha256
candidate_terminal_state
candidate_reason_codes
```

资格窗口为 `[10:45:00, 10:46:00) America/New_York`。只选择第一份通过 source、identity、causality、freshness、BBO、size、flags、synchronization 和 completeness 的 candidate。被拒 candidate 留在 append-only ledger；不得删除，也不得用价格好坏作为拒绝或等待理由。

`quote_age <= 5 seconds`，同步对象的 `receive_skew <= 1 second`。逐腿查询只有在每条记录都有可验证 receive time 且满足 skew 时才可能合格；仅有调用顺序不构成同步证明。

## 3. Opportunity universe and pair reconstruction

每个 H0 与适用 H1..H20 checkpoint 必须保留：

- 已先选择的 `LC0` long Call 完整 identity、BBO/size/flags 和本地 Delta 输入/结果；
- 同 expiry、严格更高 strike 的全部 standard/unadjusted Call 候选，而非只存 BCS winner；
- 每个候选的 OCC identity、right、strike、expiry/last-trading instant、multiplier、deliverable、currency、adjustment、activation、publisher、event/receive time；
- synchronized GLD valuation input、PIT rate/distribution/borrow inputs 及其 version hashes；
- selector 排序输入和被拒原因；
- long/short quote age、cross-leg skew、displayed size 和 tick size。

组合级至少派生并保存：

```text
long_contract_identity
short_contract_identity
same_expiry_verified
strictly_higher_short_strike_verified
multiplier_compatible
deliverable_compatible
cross_leg_receive_skew_ms
proposed_combo_quantity
per_leg_size_sufficient
pair_qualification_status
pair_reason_codes
```

BCS pair 必须可以仅从该 checkpoint 重建，且证明 same expiry、`long_strike < short_strike`、1:1、same multiplier/deliverable/currency。不得跨 source 拼腿或用 winner-only 文件补齐 universe。

## 4. BCS cost and fill evidence

每个 entry/exit checkpoint 至少需要：

```text
long_bid, long_ask, long_bid_size, long_ask_size, long_tick
short_bid, short_ask, short_bid_size, short_ask_size, short_tick
multiplier
effective_dated_long_and_short_fee_components
fee_schedule_sha256
combination_order_identity_or_capability_fact
```

Base entry 使用 long ask 与 short bid；Base exit 使用 long bid 与 short ask。四个交易 side 的 adverse tick stress 分别保存。对数量 `q`，任一腿对应 displayed size 小于 `q` 时为 `SIZE_INSUFFICIENT`；不得假定 partial fill 或 price improvement。

## 5. Historical source boundary

历史 Gate B 数据必须提供 point-in-time 双边 BBO/size、identity、event/receive time、definition/lifecycle updates 和完整 higher-strike opportunity universe。仅有 OHLC、trade bars、当前 broker snapshot 或 vendor Greeks 不合格。

拟用 Databento OPRA CMBP-1 或功能等价来源时，必须在任何下载前保存 exact provider query、schema、date/symbol scope 和 `metadata.get_cost` 等价的精确成本证明，并另行取得 Owner 下载授权。本合同不授权连接、认证、付费或下载。

## 6. Lifecycle and reconciliation evidence

除 v0.1 lifecycle 字段外，BCS 还必须保存：

- 两条腿各自的 definition updates 与 pair identity；
- exercise/assignment notice、effective time、数量和受影响腿；
- combination partial fill、残腿、cancel/reject 和 final broker state；
- post-manual-action Snapshot B 的 positions、open orders、fills、cash 和 contract identities；
- adjustment 后是否仍能证明完整组合，以及 blocking state 的清除证据。

任何未知、冲突、partial fill、残腿、assignment/exercise 或 identity adjustment 都为 `RECONCILIATION_BLOCKED`，不得仅凭理论 payoff 推断仓位终局。

## 7. Required hashes

每批在 v0.1 hash 集合之上增加：

```text
signal_snapshot_sha256
option_quote_snapshot_sha256
snapshot_candidate_ledger_sha256
quote_source_qualification_receipt_sha256
call_opportunity_universe_sha256
delta_model_contract_sha256
delta_run_inputs_sha256
bcs_selector_contract_sha256
bcs_pair_identity_sha256
four_side_cost_stress_sha256
broker_reconciliation_snapshot_b_sha256
parent_manifest_sha256
```

Manifest 不得包含 credentials、account token、session cookie 或不必要的私人账户数据。

## 8. Additional hard coverage gates

```text
QUOTE_SOURCE_QUALIFIED
FIRST_QUALIFIED_SNAPSHOT_PROVEN
PAIR_IDENTITY_COMPLETE
CROSS_LEG_SYNCHRONIZED
FOUR_SIDE_COST_COMPLETE
BCS_LIFECYCLE_COMPLETE
BROKER_RECONCILED_OR_NOT_TRADED
```

任一 gate 未通过，相关 episode 留在 denominator，但不能进入可晋升 numerator。

## 9. Additional terminal reasons

```text
QUOTE_SOURCE_NOT_QUALIFIED
OPTION_SNAPSHOT_WINDOW_EXHAUSTED
FIRST_QUALIFIED_SNAPSHOT_NOT_PROVEN
CROSS_LEG_SKEW_EXCEEDED
PAIR_IDENTITY_INCOMPLETE
SHORT_DELTA_INPUT_INCOMPLETE
SHORT_STRIKE_NOT_ELIGIBLE
NET_DEBIT_INVALID
FOUR_SIDE_COST_INCOMPLETE
COMBINATION_ORDER_CAPABILITY_UNPROVEN
ASSIGNMENT_OR_EXERCISE_UNKNOWN
PARTIAL_FILL_OR_RESIDUAL_LEG
RECONCILIATION_BLOCKED
```

## 10. Still blocking acquisition and outcome access

以下仍需另行冻结：每日 management checkpoint 精确时刻、invalidation checkpoint、`Q_path`、effective-dated fee source、clean sealed/forward interval、最终总 trial budget，以及 IBKR/RH quote-source qualification receipts。

在这些缺口关闭前，只允许 synthetic fixtures、schema validation、pure-function tests 和 fail-closed integration；不得构造 provider query、读取 P&L 或把任何 broker schema 视为已资格真实能力。
