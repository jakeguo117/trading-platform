# GLD Additional Data Catalogs V1

## 1. Implementation status

本文件同时包含已实现合同和未来字段目录。状态必须严格区分：

| contract | 当前状态 | 当前权威实现 |
|---|---|---|
| `CarrierEvidenceBundleV1` | `IMPLEMENTED_CLOSED_SCHEMA` | `validate_carrier_evidence(...)` |
| `SourceQualificationReceiptV1` | `IMPLEMENTED_CLOSED_SCHEMA`，作为 Entry 子对象 | `validate_entry_bundle(...)` |
| `DataQualificationResultV1` | `IMPLEMENTED_TYPED_RESULT` | `validate_entry_bundle(...)` |
| `ResearchFeatureBundleV1` | `CATALOG_ONLY_NOT_IMPLEMENTED` | 无 runtime parser/validator/output |
| `ManagementFactBundleV1` | `CATALOG_ONLY_NOT_IMPLEMENTED` | 无 runtime parser/validator/state machine |
| `ReconciliationSnapshotBV1` | `CATALOG_ONLY_NOT_IMPLEMENTED` | 无 runtime parser/validator/broker connection |

`CATALOG_ONLY_NOT_IMPLEMENTED` 只表示字段需求目录：不能作为已交付 schema、不能产生当前 canonical hash、不能进入当前或未来每日决策输入，也不证明管理、退出、对账或 provider capability 已经可运行。

所有内容只服务 GLD；金额为整数 `nano_usd`，比例为整数 `ppm`，时间为 UTC nanoseconds。当前 `f`、`DecisionResult`、HTML 与交易状态没有修改。

## 2. CarrierEvidenceBundleV1 — implemented exact schema

### 2.1 Bundle envelope

顶层必须且只能包含：

| exact field | 类型/语义 | validator 约束 |
|---|---|---|
| `schema_version` | 固定 `CARRIER_EVIDENCE_BUNDLE_V1` | 不匹配拒绝 |
| `classification` | schema 保留 `SYNTHETIC_ONLY` 或 `HISTORICAL_RESEARCH_CANDIDATE` | V1 validator 当前只接受 `SYNTHETIC_ONLY`；historical candidate 在 estimator authority 实现前稳定拒绝 |
| `scope` | 固定 `GLD_ENTRY_CARRIER_EVIDENCE_ONLY` | 不匹配拒绝 |
| `underlying` | 固定 `GLD` | 非 GLD 拒绝 |
| `rule_package_sha256` | lowercase SHA-256 | 非法 hash 拒绝 |
| `estimator_package_sha256` | lowercase SHA-256 | V1 必须等于固定 synthetic arithmetic-mean / zero-Kelly estimator package hash |
| `carriers` | array | 必须规范化为恰好 `BCS0`、`LC0` 两项 |
| `content_hashes` | exact object `{BCS0, LC0}` | 每项必须等于对应 carrier `evidence_sha256` |

LC0 与 BCS0 可以引用共同的历史市场底层，但当前 validator 明确禁止共享以下任何值：`evidence_receipt_id`、`evidence_sha256`、`input_sha256`、`distribution_binding.distribution_id`、`distribution_sha256`、`episode_set_sha256`。opaque-only evidence 或缺少 distribution binding 直接拒绝。

### 2.2 Each `carriers[]` exact fields

| exact field | 类型/单位 | 用途与约束 |
|---|---|---|
| `carrier_id` | `LC0` 或 `BCS0` | carrier identity |
| `evidence_receipt_id` | identifier | carrier 独立 evidence receipt |
| `episode_cohort_id` | identifier | cohort binding |
| `entry_clock_id` | identifier | entry clock binding |
| `exit_clock_id` | identifier | exit clock binding |
| `cost_model_version` | identifier | after-cost return 口径 |
| `rule_version` | identifier | carrier rule version |
| `episodes` | nonempty array | 不能只给 opaque summary |
| `coverage` | exact closed object | eligible/included/excluded 守恒 |
| `splits` | exact closed object | development/walk-forward/sealed counts 与 episodes 一致 |
| `distribution_binding` | exact closed object | return distribution 与 episode set 绑定 |
| `expected_net_return_on_entry_debit_ppm` | signed int，-10,000,000..100,000,000 ppm | future expected-profit input |
| `uncertainty_method` | 固定 `NOT_ESTIMATED_SYNTHETIC_ONLY_V1` | 明确 V1 未估 uncertainty |
| `full_kelly_ppm` | 固定 0 | synthetic 不自报 Kelly |
| `robust_full_kelly_ppm` | 固定 0 | synthetic 不自报 robust Kelly |
| `half_kelly_ppm` | 固定 0 | synthetic 不自报 Half-Kelly |
| `estimator_version` | 固定 `SYNTHETIC_ALL_EPISODE_ARITHMETIC_MEAN_V1` | descriptive synthetic mean binding，不是策略 estimator authority |
| `fee_schedule_sha256` | SHA-256 | fee version binding |
| `exit_policy_sha256` | SHA-256 | exit policy binding |
| `input_sha256` | SHA-256 | carrier evidence input binding |
| `evidence_sha256` | SHA-256 | 必须等于前述全部 carrier 字段（不含自身）的 canonical hash |

### 2.3 `episodes[]`

episodes 按 `episode_id` 排序且 ID 唯一。

| exact field | 类型/单位 | validator 约束 |
|---|---|---|
| `episode_id` | identifier | unique |
| `entry_utc_ns` | int UTC ns | 必须早于 exit |
| `exit_utc_ns` | int UTC ns | 必须晚于 entry |
| `entry_debit_nano_usd` | positive int nano_usd | return denominator |
| `after_cost_return_on_entry_debit_ppm` | signed int，-10,000,000..100,000,000 ppm | episode-level after-cost return |
| `split` | `DEVELOPMENT`、`WALK_FORWARD` 或 `SEALED_OOS` | split membership |
| `input_sha256` | SHA-256 | episode input binding |

当前 validator 验证 return 已绑定到正的 entry-debit denominator 和 episode hash，但不会重新从 gross fills 计算 return；真实 outcome/回测证据本阶段没有取得。

当前 exact schema 的证据边界也必须明确：它已有 bundle-level `classification`，但没有 `as_of_utc_ns`、episode gross P/L 与逐项 fee、walk-forward fold membership、uncertainty interval bounds 或独立 method-version 字段。这些缺失项不能从当前 receipt 推断；当前 local demonstration 明确标记为 `SYNTHETIC_ONLY`。`cost_model_version`、`fee_schedule_sha256`、`exit_policy_sha256` 和 `uncertainty_method` 是绑定 token/hash，不等于 validator 已重算相应研究过程。

### 2.4 Coverage, splits and distribution

`coverage` exact fields：

- `eligible_episode_count`
- `included_episode_count`
- `excluded_episode_count`
- `coverage_ppm`
- `exclusion_reasons`

必须满足：`eligible = included + excluded`、`included = len(episodes)`、`coverage_ppm = included * 1_000_000 // eligible`。因此 eligible 必须为正，否则公式无法通过。`exclusion_reasons[]` exact keys 是 `reason_code` 与 positive `count`；按 reason 排序、不得重复，count 合计必须等于 excluded。

`splits` exact fields：`development_count`、`walk_forward_count`、`sealed_oos_count`。三者均为 positive int，且逐项等于 episodes 实际 split 计数，所以每类至少一条。

`distribution_binding` exact fields：

| exact field | 类型/语义 | validator 约束 |
|---|---|---|
| `distribution_id` | identifier | LC0/BCS0 不得共享 |
| `return_unit` | 固定 `ON_ENTRY_DEBIT_PPM` | 其他单位拒绝 |
| `episode_set_sha256` | SHA-256 | 必须等于 normalized episodes canonical hash，且两 carrier 不同 |
| `distribution_sha256` | SHA-256 | 必须等于 carrier ID、distribution ID、return unit 与排序后 episode returns 的 canonical hash，且两 carrier 不同 |

`expected_net_return_on_entry_debit_ppm` 必须等于当前 included synthetic episodes 的 signed arithmetic mean，除法采用 toward-zero integer rounding。validator 因而会拒绝“episodes 全亏损、summary 却声明正收益”或任意 opaque distribution hash。V1 estimator ID 固定为 `SYNTHETIC_ALL_EPISODE_ARITHMETIC_MEAN_V1`，uncertainty 固定为 `NOT_ESTIMATED_SYNTHETIC_ONLY_V1`，`full/robust/half_kelly_ppm` 全部必须为 0。也就是说，本阶段没有声称估计出 Kelly；`HISTORICAL_RESEARCH_CANDIDATE` 会以 `CARRIER_HISTORICAL_ESTIMATOR_NOT_IMPLEMENTED` fail closed。下一阶段必须先批准 development/walk-forward/sealed 的估计与 uncertainty 语义，才能让 `f` 使用 expected return 或 Kelly。

该 evidence 为下一阶段提供：

```text
expected_total_net_profit_nano_usd
= current_entry_debit_per_contract_nano_usd
× safe_quantity
× expected_net_return_on_entry_debit_ppm
÷ 1_000_000
```

当前阶段不据此输出 PASS/FAIL、quantity、winner 或 preference；若中间乘积不能整除 1,000,000，下一阶段 `f` 还必须显式冻结 signed rounding 规则，V1 evidence 合同不替它作决定。

## 3. SourceQualificationReceiptV1 — implemented exact schema

该 receipt 是 `EntryFactBundleV1.source_qualification_receipts[]` 的 implemented child schema。exact fields：

- `schema_version = SOURCE_QUALIFICATION_RECEIPT_V1`
- `receipt_id`
- `provider_id`
- `provider_version`
- `evidence_kind = SYNTHETIC_GENERATOR | PROVIDER_CAPABILITY_RECEIPT`
- `entitlement_status = NOT_APPLICABLE_SYNTHETIC | VERIFIED | NOT_VERIFIED`
- `field_coverage_ppm`
- `complete_universe_supported`
- `event_time_semantics`
- `receive_time_semantics`
- `atomic_snapshot_supported`
- `synchronization_max_skew_ns`
- `effective_from_utc_ns`
- `effective_to_utc_ns`
- `covered_domains`
- `evidence_sha256`

validator 还为上述完整 normalized receipt 计算一个只读 typed field `receipt_sha256`；它不是 raw schema 可填写字段。生产候选资格使用 owner-controlled immutable trust set 匹配完整 `receipt_sha256`，不能仅凭 raw `evidence_sha256` 自我声明可信。当前实现能校验 receipt effective time、domain reference、entitlement、精确 `UTC_NS_EXPLICIT` 时间语义、字段覆盖比例、完整 universe、atomic capability 和 synchronization bound。它没有访问 provider、验证签名或自动生成真实 capability proof；synthetic adapter receipt 仍只能支持 synthetic qualification。

不同 domain 可以引用不同 receipt。`option_snapshot` 的 GLD BBO 与全部 Call contract/BBO 位于一个对象、共用一个 `source_receipt_id`，因此当前合同不允许 atomic snapshot 内跨 provider 拼接。未来真实来源 qualification 顺序为 IBKR 优先；该顺序不表示 IBKR 当前已经合格。

## 4. DataQualificationResultV1 — implemented exact result

typed result exact fields：

| field | 语义 |
|---|---|
| `schema_version` | 固定 `DATA_QUALIFICATION_RESULT_V1` |
| `status` | `STRUCTURALLY_VALID_SYNTHETIC`、`DATA_QUALIFIED` 或 `DATA_NOT_QUALIFIED` |
| `reason_codes` | sorted stable reason tuple/list |
| `input_sha256` | normalized Entry bundle hash |
| `trusted_source_receipt_set_sha256` | caller-owned receipt trust set 排序后的 canonical hash；空集合也有稳定 hash |
| `qualification_sha256` | schema/status/reasons/input/trust-set hash 的 canonical hash |

synthetic 固定得到 `STRUCTURALLY_VALID_SYNTHETIC`。V1 的 production candidate 因缺少“本次 domain content hash ↔ provider observation/ingestion”attestation，固定包含 `SOURCE_OBSERVATION_ATTESTATION_NOT_IMPLEMENTED` 并返回 `DATA_NOT_QUALIFIED`；完整 receipt trust 与 capability proof 仍是必要条件，但不是充分条件。因此本阶段没有把任何真实来源标记为 `DATA_QUALIFIED`。该结果只描述数据资格，不证明策略参数有效，不产生交易结论。

## 5. ResearchFeatureBundleV1 — catalog only, not implemented

状态：`CATALOG_ONLY_NOT_IMPLEMENTED`。

未来合同的 `authority` 必须固定为 `RESEARCH_ONLY`。以下字段家族只能用于离线研究，不能进入 Entry hash、RequiredDailyTechnicalFacts hash、每日 `f` 输入、PASS/FAIL、quantity、preference 或 `DecisionResult`：

| planned feature family | planned facts | planned raw dependencies |
|---|---|---|
| ATR | ATR14 | GLD daily OHLC |
| RSI | RSI14 | GLD daily close |
| MACD | 12/26/9 line、signal、histogram | GLD daily close |
| ADX | ADX14、DI facts | GLD daily OHLC |
| Bollinger Bands | 20-session mid/upper/lower | GLD daily close |
| option Greeks | Gamma、Vega、Theta | local model inputs |
| IV Rank | versioned historical IV rank | historical local IV facts |
| skew | strike/moneyness IV buckets | complete same-source option snapshots |
| term structure | expiry IV buckets | complete same-source option snapshots |
| option activity | volume、open interest、as-of | qualified option statistics source |
| volatility indices | VIX、GVZ | qualified index sources |
| US dollar | DXY or versioned proxy | qualified FX/index source |
| real rates | maturity real yields | PIT macro source |
| gold futures structure | front/deferred prices、calendar spreads | qualified futures source |
| ETF fund flows | shares/creation-redemption/qualified flows | ETF official or qualified source |

若未来实现，每个 research feature 也要有 type、unit/scale、exact inputs、formula/version、rounding、window、sorting、missing/conflict semantics、source fact hash 和 feature hash，并证明任何 research-only 变化不影响每日 authoritative hashes。

## 6. ManagementFactBundleV1 — catalog only, not implemented

状态：`CATALOG_ONLY_NOT_IMPLEMENTED`。下列是未来字段目录，不是当前 accepted JSON schema，也没有退出状态机：

- envelope: `trade_episode_id`、`carrier_id`、entry contract identity hashes、entry rule hash、exit policy hash、as-of/hash；
- H1–H20 schedule: horizon ID、XNYS session date/open/close、complete state；
- lifecycle updates: update ID、event/receive UTC ns、event type、position quantity by contract、source/update hashes；
- gate invalidation: gate ID/version、component facts、`NOT_EVALUATED | VALID | INVALIDATED | DATA_UNAVAILABLE`、reason codes；
- fresh exit snapshot: new same-source GLD/leg BBO、size、tick、flags、event/receive clocks；
- latch: policy ID/version、`CLEAR | ARMED | TRIGGERED | ACKNOWLEDGED | RELEASED`、transition evidence；
- expiry safety: H20 date、last-trading UTC ns、remaining horizon、`SAFE | WARNING | FORCED_EXIT_WINDOW | EXPIRED | DATA_UNAVAILABLE`。

该目录不授权真实退出、order writes、broker connection 或 lifecycle mutation。

## 7. ReconciliationSnapshotBV1 — catalog only, not implemented

状态：`CATALOG_ONLY_NOT_IMPLEMENTED`。下列是未来字段目录，不是当前 accepted JSON schema，也没有连接真实 broker：

- envelope: snapshot B ID、account hash、event/receive UTC ns、trade episode ID、USD currency、cash/settled cash、snapshot hash；
- orders: broker/local IDs、contract identity、side/type/TIF、submitted/remaining quantity、limit、status/time；
- fills: fill/order IDs、contract identity、signed quantity、price、event/receive clocks；
- positions: contract identity、quantity、average cost；
- fees: fee/fill IDs、scope、amount、effective rule hash；
- partial/residual legs: expected/filled/position quantity maps、partial IDs、residual IDs、mismatch reasons；
- assignment/exercise: event ID/type、option/underlying identity、quantity、strike、event/settlement date；
- adjustments: adjustment ID/type、amount/quantity、source record IDs、reason code。

未来实现必须验证 orders→fills 一对多数量守恒，避免 join explosion 与重复计费，并区分 partial、residual、assignment、exercise 和 correction。当前文档不构成对账已完成的证明。

## 8. Current non-effects

- catalog-only bundles 不被 runtime 读取；
- `ResearchFeatureBundleV1` 的任何假设变化不能改变 Entry 或 RequiredDailyTechnicalFacts；
- Management/Reconciliation 目录不改变当前交易状态；
- 所有现有输出仍为 simulation/read-only，broker writes 为 0；
- 单 carrier 输出一个、双 carrier 产生首选/备选的逻辑仍留到下一阶段唯一权威 `f`。
