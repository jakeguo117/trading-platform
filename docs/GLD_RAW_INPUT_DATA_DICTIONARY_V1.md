# GLD Raw Input Data Dictionary V1

## 1. Status and boundary

本文件记录当前已经由 `src/gld_data_contracts/validation.py` 实现的 `EntryFactBundleV1` exact closed schema。字段名、嵌套位置和枚举以该 validator 为准；本文件没有把未来扩展字段写成已实现能力。

当前链路只服务 GLD：

```text
EntryFactBundleV1
→ validate_entry_bundle(...)
→ RequiredDailyTechnicalFactsV1
```

raw entry 禁止预填 SMA、IV、Delta、winner、quantity、preference、carrier pass/fail、signal state 或 `DecisionResult`。唯一允许的 Delta 语义 raw key 是 Snapshot A 已有风险 `current_gld_delta_exposure_nano_usd`。

全局类型规则：JSON number 必须是整数；金额/价格为 `nano_usd`；比例为 `ppm`；时间为 UTC nanoseconds；日期为 `YYYY-MM-DD`；对象不允许未知字段。所有无序列表都在去重后按 validator 规则排序，canonical JSON 与 SHA-256 不依赖输入文件或数组顺序。

固定时间语义：决策 cutoff 是 XNYS 当日开盘后 75 分钟，即 10:45 ET；option snapshot window 从 cutoff 开始且长度必须为 60 秒，即 `[10:45,10:46)` ET。分钟确认使用 minute-ending ordinal 630 至 644，即 10:30 至 10:44 的 15 根完整一分钟 bar。

旧 11-document adapter 的 classification 固定为 `SYNTHETIC_ONLY`，最高只能得到 `STRUCTURALLY_VALID_SYNTHETIC`。公开 legacy 入口用 dirfd + `O_NOFOLLOW|O_NONBLOCK` 有界读取，并在前后递归闭集扫描之间捕获私有 regular-file snapshot；未登记文件或目录、FIFO/symlink、路径深度超过 8 或 tree nodes 超过 4,096 均拒绝。direct Entry 文件也在同一 fd 读取前后复核 inode、size、mtime、ctime 与实际 byte length。结构通过不等于 provider 数据资格或策略有效性。本阶段没有访问 IBKR、真实账户或付费数据；未来 qualification 顺序仍是 IBKR 优先。

## 2. Exact top-level schema

顶层必须且只能包含下列 19 个 key。

| exact field | 来源 | 时间/单位 | 用途 | 缺失、未知或冲突后果 |
|---|---|---|---|---|
| `schema_version` | 本地合同 | string，固定 `ENTRY_FACT_BUNDLE_V1` | 选择 validator | version unsupported 或 schema invalid |
| `classification` | ingest/adapter | enum：`SYNTHETIC_ONLY`、`PRODUCTION_CANDIDATE` | 限制资格上限 | 其他值拒绝；synthetic 不可升级 |
| `scope` | 本地合同 | string，固定 `GLD_ENTRY_FACTS_ONLY` | 阻止混入决定结果 | 不匹配拒绝 |
| `bundle_id` | ingest/adapter | ASCII identifier | 重放与审计 identity | 缺失/非法拒绝 |
| `underlying` | instrument scope | string，固定 `GLD` | 防 SLV 或其他标的继承 | 非 GLD 拒绝 |
| `trading_date` | XNYS calendar | `YYYY-MM-DD` | 所有 daily/minute/session join | 非法或与日历冲突拒绝 |
| `cutoff_utc_ns` | XNYS session + rule | int UTC ns；必须为当日开盘后 75 分钟 | pre-snapshot causal cutoff 和 option window start | 缺失、错时或非 session 内拒绝 |
| `calendar` | calendar source receipt | closed object | XNYS 历史和 H20 | 缺失/未知 key 拒绝 |
| `daily_bars` | GLD daily source receipt | closed object | prior close、SMA、20-session high | 缺失/未知 key 拒绝 |
| `minute_bars` | GLD minute source receipt | closed object | 10:30–10:44 confirmation | 缺失/未知 key 拒绝 |
| `market_status` | minute/status source receipt | closed object，as-of 不晚于 cutoff | completeness 与 regular phase | 缺失/未知 key 拒绝 |
| `option_snapshot` | 单一 option snapshot source receipt | closed object，capture 位于 `[cutoff,cutoff+60s)` | GLD BBO、variable-length complete Call universe 和全部 option BBO | 缺失/未知 key 拒绝 |
| `pit_inputs` | 一个或多个 PIT source receipts | closed object，as-of 不晚于 snapshot capture | rate/carry/borrow 模型输入 | 缺失/未知 key 拒绝 |
| `fee_schedule` | fee source receipt | closed object，cutoff 时有效 | LC0/BCS0 after-fee economics | 缺失/未知 key 拒绝 |
| `account_snapshot_a` | account source receipt | closed object，cutoff 前且未 stale | 账户风险 facts | 缺失/未知 key 拒绝 |
| `rule_package` | 本地版本化 rule binding | closed object，cutoff 时有效 | 窗口、freshness、stress 和账户公式参数 | 缺失/未知 key 拒绝 |
| `model_package` | 本地版本化 model binding | closed object | 锁定 IV/Delta 本地实现 | 缺失/未知 key 拒绝 |
| `source_qualification_receipts` | provider capability evidence 或 synthetic adapter receipt | 非空 array | 将每个数据域绑定到来源能力；validator 为完整 receipt 派生 `receipt_sha256` | 缺失、duplicate、domain 未覆盖或过期拒绝；production trust 只匹配完整 receipt hash |
| `content_hashes` | 本地 canonical normalizer | closed object of 11 hashes | 逐域防篡改与确定性重放 | 缺失、未知 hash key 或 hash mismatch 拒绝 |

## 3. `calendar`

`calendar` exact keys：`schema_version`、`source_receipt_id`、`timezone`、`decision_session_ordinal`、`sessions`。

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `calendar.schema_version` | 本地 normalizer | 固定 `XNYS_CALENDAR_FACTS_V1` | schema binding | 拒绝 |
| `calendar.source_receipt_id` | calendar source | ASCII identifier | 绑定覆盖 `CALENDAR` 的 receipt | 引用不存在/未覆盖拒绝 |
| `calendar.timezone` | XNYS source | 固定 `America/New_York` | DST 和 10:45 语义 | 其他值拒绝 |
| `calendar.decision_session_ordinal` | calendar source | positive int | 唯一定位 decision session | 找不到或与 trading date/cutoff 冲突拒绝 |
| `calendar.sessions` | calendar source | array；按 `session_ordinal` 升序 canonicalize | 至少覆盖 decision 前 220 和后 20 个 session | coverage 不足、duplicate 或时序不单调拒绝 |

每个 `calendar.sessions[]` 必须且只能包含：

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `session_ordinal` | calendar source | positive int | session 稳定排序/join key | duplicate/非正拒绝 |
| `trading_date` | calendar source | `YYYY-MM-DD` | civil trading date | duplicate/date order 冲突拒绝 |
| `open_utc_ns` | calendar source | int UTC ns | cutoff 对齐 | 不早于 close 或时序冲突拒绝 |
| `close_utc_ns` | calendar source | int UTC ns | bar 完整性、session coverage | 不晚于 open 或时序冲突拒绝 |
| `is_full_session` | calendar source | bool | session 属性 fact | 非 bool 拒绝；当前 validator 保留该 fact，不以它单独筛除 H20 |

每个 session 的 UTC open/close 必须映射回其声明的 `America/New_York` civil date；open 必须为 09:30:00 ET，日期不得为周末。10:45 cutoff 还必须映射到 decision `trading_date` 的 10:45:00 ET。具体 XNYS holiday/session 真值仍由被资格化的 calendar observation 负责，validator 不凭本地当前日历静默补全。

## 4. `daily_bars`

容器 exact keys：`schema_version`、`source_receipt_id`、`price_scale`、`bars`。

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `daily_bars.schema_version` | normalizer | 固定 `GLD_DAILY_OHLCV_FACTS_V1` | schema binding | 拒绝 |
| `daily_bars.source_receipt_id` | daily source | identifier，receipt 须覆盖 `DAILY_BARS` | lineage | 无效引用拒绝 |
| `daily_bars.price_scale` | normalizer | 固定 `NANO_USD` | 防单位混用 | 其他值拒绝 |
| `daily_bars.bars` | daily source | array，按 `session_ordinal` 升序 | 至少包含 rule 要求的最近完整历史，当前下限 220 | 缺最近 session、duplicate 或不足拒绝 |

每个 `daily_bars.bars[]` exact keys：

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `session_ordinal` | calendar-aligned daily source | positive int，严格早于 decision ordinal | calendar join | 非 prior session/duplicate/无日历项拒绝 |
| `trading_date` | daily source | `YYYY-MM-DD` | 与 calendar 校验 | 不匹配拒绝 |
| `open_nano_usd` | daily source | positive int nano_usd | OHLC fact | 缺失/非法拒绝 |
| `high_nano_usd` | daily source | positive int nano_usd | 20-session high | 小于 open/close 或小于 low 拒绝 |
| `low_nano_usd` | daily source | positive int nano_usd | OHLC integrity | 大于 open/close 或 high 拒绝 |
| `close_nano_usd` | daily source | positive int nano_usd | prior close、SMA | 缺失/非法拒绝 |
| `volume` | daily source | nonnegative int shares | raw completeness fact | 缺失/负值拒绝 |
| `complete` | daily source | bool，必须 true | 排除 partial session | false 拒绝 |
| `max_event_utc_ns` | daily source | int UTC ns，位于该 XNYS session `[open, close]` | causal evidence | session 前或 close 后 event 拒绝 |
| `max_receive_utc_ns` | local capture | int UTC ns，`event <= receive <= cutoff` | 防 future data | 错序或 cutoff 后拒绝 |

## 5. `minute_bars`

容器 exact keys与 daily 相同：`schema_version`、`source_receipt_id`、`price_scale`、`bars`；version 固定 `GLD_MINUTE_OHLCV_FACTS_V1`，receipt domain 固定 `MINUTE_BARS`，scale 固定 `NANO_USD`。

每个 `minute_bars.bars[]` exact keys：

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `session_ordinal` | minute source + calendar | positive int，必须等于 decision ordinal | session binding | 不匹配拒绝 |
| `trading_date` | minute source | `YYYY-MM-DD`，必须等于 top-level trading date | date binding | 不匹配拒绝 |
| `minute_ending_ordinal` | minute source | int 0..1439 | 确定 10:30–10:44 集合 | duplicate/非法拒绝 |
| `start_utc_ns` | minute source | int UTC ns | minute interval start | 与 end 不构成 60 秒拒绝 |
| `end_utc_ns` | minute source | int UTC ns | minute interval end | 630..644 必须按一分钟步长对齐 cutoff |
| `open_nano_usd` | minute source | positive int nano_usd | OHLC fact | 缺失/非法拒绝 |
| `high_nano_usd` | minute source | positive int nano_usd | OHLC integrity | OHLC 矛盾拒绝 |
| `low_nano_usd` | minute source | positive int nano_usd | OHLC integrity | OHLC 矛盾拒绝 |
| `close_nano_usd` | minute source | positive int nano_usd | 15根 closes 与 10:44 close | 缺失/非法拒绝 |
| `volume` | minute source | nonnegative int shares | raw completeness fact | 缺失/负值拒绝 |
| `complete` | minute source | bool，必须 true | 排除 partial minute | false 拒绝 |
| `max_event_utc_ns` | minute source | int UTC ns，位于该 bar `[start, end]` | causal evidence | bar 前或 bar end 后 event 拒绝 |
| `max_receive_utc_ns` | local capture | int UTC ns，`event <= receive <= cutoff` | 防 future data | 错序或 cutoff 后拒绝 |

ordinals 630..644 必须全部且各出现一次；不 forward-fill、不取最近邻。任何额外 minute bar 也必须满足 `minute_ending_ordinal ↔ America/New_York start`、60 秒区间、`end <= cutoff` 与 `start <= max_event <= end`，不能用 future extra bar 混入 raw bundle。

## 6. `market_status`

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `schema_version` | normalizer | 固定 `MARKET_STATUS_FACTS_V1` | schema binding | 拒绝 |
| `source_receipt_id` | status source | receipt 须覆盖 `MINUTE_BARS` | lineage | 无效引用拒绝 |
| `trading_date` | status source | `YYYY-MM-DD` | decision date binding | 不匹配拒绝 |
| `as_of_utc_ns` | status source | int UTC ns，必须等于 10:45 cutoff | 状态观察时点与 completeness 声明一致 | 早于或晚于 cutoff 都按状态冲突拒绝 |
| `market_phase` | status source | 固定 `REGULAR_TRADING` | 只允许正常 regular phase | 其他值拒绝 |
| `complete_through_utc_ns` | status source | int UTC ns，必须等于 10:45 cutoff | 声明分钟与状态数据完整覆盖到本次决策边界 | 早于或晚于 cutoff 都按状态/事实冲突拒绝 |
| `daily_history_complete` | status source | bool，必须 true | 完整性 gate | false 拒绝 |
| `intraday_history_complete` | status source | bool，必须 true | 完整性 gate | false 拒绝 |
| `option_universe_complete` | status source | bool，必须 true | 防选择性 universe | false 返回 `ENTRY_OPTION_UNIVERSE_INCOMPLETE` |
| `quote_snapshot_complete` | status source | bool，必须 true | BBO completeness | false 返回 `ENTRY_QUOTE_SNAPSHOT_INCOMPLETE` |
| `calendar_complete` | status source | bool，必须 true | calendar coverage declaration | false 拒绝 |
| `causal_cutoff_enforced` | source/adapter | bool，必须 true | 防未来数据 | false 拒绝 |

## 7. `option_snapshot`: complete variable-length Call universe and BBO

当前实现没有独立 universe 文档。完整 Call universe 是 `option_snapshot.option_quotes[]` 中每个 `{contract, top_of_book}` 的 variable-length 全集；数组至少一项，按 `contract.contract_id` 排序。64 不是 market/universe 规则，只是旧 benchmark fixture 大小，程序不按 contract 数量静默截断。BCS compatible pairs 采用 `BCS_ALL_COMPATIBLE_PAIRS_FACTORED_V1`：按 expiry/multiplier/deliverable_shares/currency/deliverable 分组，所有 pair features 引用一份共享 `BCS_PAIR_COMPONENT_STORE_V1` 及各自 component projection hash，并保存精确 pair count；`materialize_bcs_pair_facts(...)` 扫描 factored group（最坏 O(n)）解码任意显式 long/short pair，避免 O(n²) 预物化。Entry input 与 technical output 都受 canonical JSON 100,000-node 安全上限约束；这不是 market contract-count 规则，超出 output bound 会以 `TECHNICAL_OUTPUT_RESOURCE_LIMIT_EXCEEDED` 明确拒绝而不截断。reference CRR 对 contracts 仍是线性但较慢，生产吞吐需另行优化和验收。

### 7.1 Snapshot envelope

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `schema_version` | normalizer | 固定 `GLD_CALL_ATOMIC_SNAPSHOT_V1` | schema binding | 拒绝 |
| `source_receipt_id` | market-data source | receipt 须覆盖 `OPTION_SNAPSHOT` | 强制 GLD 与全部 legs 同源 | 无效/过期引用拒绝 |
| `snapshot_id` | market-data capture | identifier | snapshot identity | 缺失/非法拒绝 |
| `capture_utc_ns` | local capture | int UTC ns，位于 window 内 | quote age reference | 窗口外拒绝 |
| `window_start_utc_ns` | rule + capture | int UTC ns，必须等于 cutoff | 固定 10:45 inclusive | 不匹配拒绝 |
| `window_end_utc_ns` | rule + capture | int UTC ns，必须为 start + 60 秒 | 固定 10:46 exclusive | 不匹配拒绝 |
| `universe_complete` | source capability/current capture | bool，必须 true | 全量 Call universe 声明 | false 拒绝 |
| `underlying_bbo` | 同一 source receipt | BBO object | GLD valuation | 缺失/identity 不为 GLD 拒绝 |
| `option_quotes` | 同一 source receipt | nonempty array of exact quote objects | 全部 Call contract identity 与 BBO | duplicate contract/OCC、未知字段或缺腿拒绝 |

### 7.2 `option_quotes[].contract`

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `contract_id` | option master | 最长 127 ASCII chars 的 identifier | quote/model/pair join；保证 canonical pair key 可编码 | duplicate、过长或非法拒绝 |
| `occ_symbol` | option master | OCC Call identity（padded 或 compact GLD root + YYMMDD + C + 8-digit strike） | 与 underlying/right/expiry/strike 交叉校验 | duplicate、格式、GLD root、Call right、expiry 或 strike 冲突拒绝 |
| `underlying` | option master | 固定 `GLD` | scope | 非 GLD 拒绝 |
| `option_type` | option master | 固定 `CALL` | Call-only universe | 非 CALL 拒绝 |
| `strike_nano_usd` | option master | positive int nano_usd/share | LC0/BCS width、model | 缺失/非正拒绝 |
| `expiry_date` | option master | UTC civil `YYYY-MM-DD` | DTE | 与 `expiry_utc_ns` UTC date 不符拒绝 |
| `last_trading_date` | exchange/clearing master | UTC civil `YYYY-MM-DD` | H20 expiry gap | 与 `last_trading_utc_ns` UTC date 不符拒绝 |
| `expiry_utc_ns` | option master | int UTC ns，严格晚于 cutoff | maturity | 已到期/日期冲突拒绝 |
| `last_trading_utc_ns` | exchange/clearing master | int UTC ns，`<= expiry_utc_ns` | expiry safety | 晚于 expiry/date 冲突拒绝 |
| `activation_utc_ns` | option master | int UTC ns，`<= cutoff` | 防未挂牌合约 | cutoff 后拒绝 |
| `multiplier` | option master | positive int shares/contract | debit、cap、Delta notional | 缺失/非正拒绝 |
| `deliverable_shares` | clearing master | positive int shares | deliverable identity 与 BCS pair compatibility | 缺失/非正拒绝；`standard_unadjusted=true` 时与 multiplier 不同则 identity mismatch |
| `deliverable` | clearing master | nonempty ASCII string | pair compatibility | 缺失/非法拒绝 |
| `currency` | option master | 固定 `USD` | 金额口径 | 非 USD 拒绝 |
| `exchange` | option master | identifier | venue fact | 缺失/非法拒绝 |
| `tick_nano_usd` | exchange/contract source | positive int nano_usd | BBO grid 与 stress | 与 BBO tick 不同拒绝 |
| `standard_unadjusted` | clearing master | bool | contract qualification fact | 非 bool 拒绝；true 要求 `deliverable_shares == multiplier`；false 保留为 fact，不从 universe 删除但只能与完全相同 deliverable identity 配对 |
| `exercise_style` | option master | `AMERICAN` 或 `EUROPEAN` | local model qualification | 其他值拒绝；EUROPEAN 保留但 model 可输出不合格 reason |

### 7.3 Exact BBO schema

`underlying_bbo` 和每个 `option_quotes[].top_of_book` 使用同一 exact key set：

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `instrument_id` | same market-data source | identifier | BBO/contract identity | underlying 非 GLD、option 与 contract ID 不同则拒绝 |
| `bid_nano_usd` | same source | positive int nano_usd | spread、BCS short credit、midpoint | `bid > ask` 或不在 tick grid 拒绝 |
| `ask_nano_usd` | same source | positive int nano_usd | LC0/BCS long debit、midpoint | 小于 bid 或不在 tick grid 拒绝 |
| `bid_size` | same source | nonnegative int contracts/shares | liquidity | 小于 `rule_package.min_quote_size` 拒绝 |
| `ask_size` | same source | nonnegative int contracts/shares | liquidity | 小于 `rule_package.min_quote_size` 拒绝 |
| `tick_nano_usd` | same source/contract rule | positive int nano_usd | price-grid check | 非正、quote off-grid 或 option contract tick 冲突拒绝 |
| `event_utc_ns` | source event clock | int UTC ns，位于 snapshot window | causality | 窗口外拒绝 |
| `receive_utc_ns` | local receive clock | int UTC ns，位于 window，`event <= receive <= capture` | quote age 与 skew | 错序、window 外或 stale 拒绝 |
| `flags` | same source | unique ASCII identifier array，排序 canonicalize | 原样输出 quote flags | duplicate/非法 flag 拒绝；当前 validator 不解释 flag 含义 |

全部 underlying/option receive times 的 `max - min` 必须不超过 `max_cross_leg_receive_skew_ns`。因此同一 snapshot 的 GLD valuation、所有 option identities 与 BBO 都由同一个 `source_receipt_id` 约束，不能跨 provider 拼接。

## 8. `pit_inputs`

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `schema_version` | normalizer | 固定 `PIT_MARKET_INPUTS_V1` | schema binding | 拒绝 |
| `source_receipt_ids` | rate/ETF/borrow sources | nonempty unique identifiers，排序；每项 receipt 覆盖 `PIT_INPUTS` | 多域 lineage | 缺失/duplicate/无效引用拒绝 |
| `as_of_utc_ns` | PIT sources | int UTC ns，`<= option_snapshot.capture_utc_ns` | 防 look-ahead | capture 后拒绝 |
| `rate_curve` | qualified rate source | nonempty array，按 tenor 排序 | deterministic interpolation | 空、duplicate tenor 或 point 非法拒绝 |
| `rate_curve[].tenor_days` | rate source | positive int calendar days | maturity bracket | 非正/duplicate 拒绝 |
| `rate_curve[].zero_rate_ppm` | rate source | int ppm，范围 -1,000,000..5,000,000 | risk-free rate | 越界/单位错误拒绝 |
| `expense_yield_ppm` | GLD expense source | int ppm，0..1,000,000 | carry | 缺失/越界拒绝 |
| `distribution_yield_ppm` | GLD distribution assumption source | int ppm，0..1,000,000 | carry | 缺失/越界拒绝 |
| `borrow_available` | borrow source | bool | model eligibility | 非 bool 拒绝；false 使 contract model facts 显式 non-converged |
| `borrow_rate_ppm` | borrow source | int ppm，-1,000,000..5,000,000 | borrow carry | 缺失/越界拒绝 |

## 9. `fee_schedule`

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `schema_version` | normalizer | 固定 `EFFECTIVE_FEE_SCHEDULE_V1` | schema binding | 拒绝 |
| `source_receipt_id` | fee source | receipt 覆盖 `FEES` | lineage | 无效引用拒绝 |
| `effective_from_utc_ns` | broker/exchange/clearing/regulatory source | int UTC ns，`<= cutoff` | PIT fee selection | cutoff 后拒绝 |
| `effective_to_utc_ns` | same sources | nullable int UTC ns；若非 null，`cutoff < effective_to` | half-open effective range | 过期/非法拒绝 |
| `currency` | fee source | 固定 `USD` | fee unit | 非 USD 拒绝 |
| `broker_schedule_id` | broker | identifier | fee lineage | 缺失/非法拒绝 |
| `exchange_schedule_id` | exchange | identifier | fee lineage | 缺失/非法拒绝 |
| `clearing_schedule_id` | clearing source | identifier | fee lineage | 缺失/非法拒绝 |
| `regulatory_schedule_id` | regulatory source | identifier | fee lineage | 缺失/非法拒绝 |
| `long_entry_fee_nano_usd_per_contract` | composite fee schedule | nonnegative int nano_usd/contract | LC0/BCS entry debit | 缺失/负值拒绝 |
| `long_exit_fee_nano_usd_per_contract` | composite fee schedule | nonnegative int nano_usd/contract | carrier evidence/exit catalog | 缺失/负值拒绝 |
| `short_entry_fee_nano_usd_per_contract` | composite fee schedule | nonnegative int nano_usd/contract | BCS entry debit | 缺失/负值拒绝 |
| `short_exit_fee_nano_usd_per_contract` | composite fee schedule | nonnegative int nano_usd/contract | carrier evidence/exit catalog | 缺失/负值拒绝 |

## 10. `account_snapshot_a`

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `schema_version` | normalizer | 固定 `ACCOUNT_SNAPSHOT_A_V1` | schema binding | 拒绝 |
| `source_receipt_id` | account source | receipt 覆盖 `ACCOUNT` | lineage | 无效引用拒绝 |
| `as_of_utc_ns` | account source | int UTC ns，`<= cutoff` 且 age `<= max_account_age_ns` | freshness | future/stale 拒绝 |
| `currency` | account source | 固定 `USD` | money unit | 非 USD 拒绝 |
| `net_liquidation_value_nano_usd` | account ledger | nonnegative int nano_usd | drawdown、cash reserve | 缺失/负值拒绝 |
| `settled_cash_nano_usd` | account ledger | nonnegative int nano_usd | raw cash fact；当前 technical cash-reserve formula 不直接使用它 | 缺失/负值拒绝 |
| `strategy_bankroll_nano_usd` | owner/local risk ledger | nonnegative int nano_usd | eligible bankroll base | 缺失/负值拒绝 |
| `strategy_high_watermark_nano_usd` | local risk ledger | positive int nano_usd | drawdown denominator | 缺失、0 或负值在 Entry validation 阶段拒绝 |
| `realized_profit_nano_usd` | reconciled ledger | nonnegative int nano_usd | eligible bankroll reinvestment | 缺失/负值拒绝 |
| `realized_loss_nano_usd` | reconciled ledger | nonnegative int nano_usd | eligible bankroll deduction | 缺失/负值拒绝 |
| `current_gld_delta_exposure_nano_usd` | reconciled risk snapshot | signed int nano_usd Delta-equivalent | existing exposure technical fact | 缺失/越界拒绝；当前 derivation 直接传递，不从 positions 重算 |
| `positions` | account source | array，按 `position_id` 排序 | Snapshot A raw positions | schema/duplicate 拒绝 |
| `open_orders` | account source | array，按 `order_id` 排序 | Snapshot A raw commitments | schema/duplicate 拒绝 |

`positions[]` exact keys：

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `position_id` | account source | identifier | deduplication | duplicate/非法拒绝 |
| `instrument_id` | account source | identifier | instrument reference | 缺失/非法拒绝 |
| `signed_contract_count` | account source | signed int | position quantity | 缺失/越界拒绝 |
| `multiplier` | account/instrument source | positive int | exposure semantics | 非正拒绝 |
| `currency` | account source | 固定 `USD` | unit | 非 USD 拒绝 |

`open_orders[]` exact keys：

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `order_id` | account source | identifier | deduplication | duplicate/非法拒绝 |
| `instrument_id` | account source | identifier | instrument reference | 缺失/非法拒绝 |
| `remaining_contract_count` | account source | positive int contracts | pending commitment | 非正拒绝 |
| `side` | account source | `BUY` 或 `SELL` | direction | 其他值拒绝 |
| `limit_nano_usd` | account source | positive int nano_usd | order limit fact | 非正拒绝 |
| `status` | account source | `OPEN` 或 `PARTIALLY_FILLED` | active order state | 其他值拒绝 |

## 11. `rule_package`

当前 exact schema 已包含以下 16 个字段。最后五个是本阶段为 technical economics/account facts 新增的参数。

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `schema_version` | local package | 固定 `GLD_TECHNICAL_RULE_BINDING_V1` | schema binding | 拒绝 |
| `package_id` | local package | identifier | rule identity | 缺失/非法拒绝 |
| `version` | local package | identifier | deterministic replay | 缺失/非法拒绝 |
| `effective_from_utc_ns` | local package | int UTC ns，`<= cutoff` | effective dating | future rule 拒绝 |
| `cutoff_minute_ending_ordinal` | local package | int，固定 645 | 10:45 ET cutoff | 其他值拒绝 |
| `option_window_duration_ns` | local package | positive int，固定 60,000,000,000 | `[10:45,10:46)` window | 其他值拒绝 |
| `max_quote_age_ns` | local package | positive int ns | BBO freshness | 非正拒绝；超限 quote 拒绝 |
| `max_cross_leg_receive_skew_ns` | local package | positive int ns | underlying + all option receive synchronization | 非正拒绝；snapshot 超限拒绝 |
| `max_account_age_ns` | local package | positive int ns | Snapshot A freshness | 非正/账户超限拒绝 |
| `min_quote_size` | local package | positive int contracts/shares | BBO minimum displayed size | 非正/任一 BBO 不足拒绝 |
| `min_history_sessions` | local package | int 220..512 | daily history requirement | 越界或历史不足拒绝 |
| `entry_stress_long_ticks` | local package | int 0..1000 | LC0 与 BCS0 long ask adverse stress | 缺失/越界拒绝 |
| `entry_stress_short_ticks` | local package | int 0..1000 | BCS0 short bid adverse stress | 缺失/越界拒绝 |
| `realized_profit_reinvestment_ppm` | local package | int ppm 0..1,000,000 | eligible bankroll 加回 realized profit 的比例 | 缺失/越界拒绝 |
| `realized_loss_effect_ppm` | local package | int ppm 0..1,000,000 | eligible bankroll 扣除 realized loss 的比例 | 缺失/越界拒绝 |
| `minimum_cash_reserve_nlv_ppm` | local package | int ppm 0..1,000,000 | cash reserve 占 NLV 比例 | 缺失/越界拒绝 |

## 12. `model_package`

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `schema_version` | local model package | 固定 `LOCAL_OPTION_MODEL_BINDING_V1` | schema binding | 拒绝 |
| `package_id` | local model package | identifier | model package identity | 缺失/非法拒绝 |
| `version` | local model package | identifier | model implementation binding | 缺失/非法拒绝；technical runtime 还要求匹配冻结 model SHA |
| `formula_id` | local model package | identifier | local CRR formula identity | 缺失/非法拒绝；不接受 provider IV/Delta 为权威 |
| `coarse_steps` | local model package | positive int | coarse Delta | 非正拒绝 |
| `fine_steps` | local model package | positive int 且大于 coarse | fine Delta | 不大于 coarse 拒绝 |
| `iv_iterations` | local model package | int 1..1024 | deterministic IV solver iteration count | 越界拒绝 |
| `rounding_mode` | local model package | 固定 `HALF_EVEN_INTEGER` | midpoint/model rounding | 其他值拒绝 |
| `source_code_sha256` | local source artifact | lowercase SHA-256 | code binding | 非法 hash 拒绝；technical runtime 还要求冻结 hash 匹配 |
| `runtime_fingerprint_sha256` | local runtime manifest | lowercase SHA-256 | 将 Python/compiler/OS/machine runtime 明确纳入 X，避免隐式环境输入 | 非法 hash 拒绝；与当前 pinned runtime 不同则 technical derivation fail closed |

当前 technical runtime 进一步要求 frozen model values：`coarse_steps=512` 与 `fine_steps=1024` 是有序 step-suite 的 anchor，实际 Delta suites 分别为 `(512,513)` 与 `(1024,1025)`，各 suite 先做两树算术平均再按 half-even 量化；IV iterations=32。`formula_id`、`version`、`source_code_sha256`、`runtime_fingerprint_sha256` 也必须与本地 CRR 常量完全相符；validator 结构通过不等于 model/runtime binding 通过。同 raw bundle 在不同 runtime 不会悄悄生成另一份 bytes，而会以 `TECHNICAL_MODEL_PACKAGE_UNSUPPORTED` fail closed。除此之外，本地模型入口及每次 `compute` 的前后都会读取当前线程的 C `fegetround()`；V1 只接受已明确审核的 macOS arm64/x86_64 ABI 上的 `FE_TONEAREST`，其他模式、无法读取或未审核平台均以 `TECHNICAL_NUMERIC_ENVIRONMENT_UNSUPPORTED` fail closed，不猜测跨平台 `FE_*` 宏值。reference compute/verify callable 在 module import 时捕获；public imported global 漂移或 sealed runtime 使用任何替换 callable，都会在 CRR 执行前以 `TECHNICAL_MODEL_RUNTIME_IDENTITY_MISMATCH` 拒绝，因此 wrapper 不能在内部瞬时切换 rounding、计算后恢复来绕过前后采样。相同三个检查点还要求 import-captured `sys.getprofile`/`sys.gettrace` 身份未漂移且 live 值都为 null；active profiler/trace hook 以 `TECHNICAL_MODEL_RUNTIME_HOOK_UNSUPPORTED` 拒绝，避免 callback 在真实 core 的 call/return 之间瞬时改变 rounding。

## 13. `source_qualification_receipts[]`

每项必须且只能包含以下字段，并按 `receipt_id` 排序。

| exact field | 来源 | 时间/单位 | 用途 | 缺失或冲突后果 |
|---|---|---|---|---|
| `schema_version` | local receipt contract | 固定 `SOURCE_QUALIFICATION_RECEIPT_V1` | schema binding | 拒绝 |
| `receipt_id` | qualification process/adapter | identifier | domain reference | duplicate/非法拒绝 |
| `provider_id` | provider/adapter | identifier | provider identity | 缺失/非法拒绝 |
| `provider_version` | provider/adapter | identifier | capability version | 缺失/非法拒绝 |
| `evidence_kind` | qualification process | `SYNTHETIC_GENERATOR` 或 `PROVIDER_CAPABILITY_RECEIPT` | 区分 synthetic 与 provider proof | 非法拒绝；production 非 provider proof 导致不合格 |
| `entitlement_status` | provider qualification | `NOT_APPLICABLE_SYNTHETIC`、`VERIFIED`、`NOT_VERIFIED` | entitlement gate | 非法拒绝；production 非 VERIFIED 导致不合格 |
| `field_coverage_ppm` | qualification tests | int 0..1,000,000 ppm | required-field coverage | 越界拒绝；production 非 1,000,000 导致不合格 |
| `complete_universe_supported` | qualification tests | bool | complete option universe capability | 非 bool 拒绝；OPTION_SNAPSHOT production receipt false 导致不合格 |
| `event_time_semantics` | provider qualification | identifier | event clock definition ID | 缺失/非法拒绝 |
| `receive_time_semantics` | local capture qualification | identifier | receive clock definition ID | 缺失/非法拒绝 |
| `atomic_snapshot_supported` | qualification tests | bool | same-source snapshot capability | 非 bool 拒绝；OPTION_SNAPSHOT production receipt false 导致不合格 |
| `synchronization_max_skew_ns` | qualification tests | nonnegative int ns | source sync capability bound | 非法拒绝；OPTION_SNAPSHOT bound 大于 rule 导致不合格 |
| `effective_from_utc_ns` | receipt issuer | int UTC ns，`<= cutoff` | effective dating | future receipt 拒绝 |
| `effective_to_utc_ns` | receipt issuer | nullable int UTC ns；若非 null，`cutoff < effective_to` | half-open validity | 过期拒绝 |
| `covered_domains` | qualification tests | nonempty unique identifier array，排序 | domain reference gate | empty/duplicate/非法拒绝 |
| `evidence_sha256` | qualification artifact | lowercase SHA-256 | evidence binding | 非法 hash 拒绝 |

实际 entry references 要求 receipt 覆盖：`CALENDAR`、`DAILY_BARS`、`MINUTE_BARS`、`OPTION_SNAPSHOT`、`PIT_INPUTS`、`FEES`、`ACCOUNT`。`market_status.source_receipt_id` 也按 `MINUTE_BARS` domain 检查。option receipt 还必须在 snapshot capture 时有效。

## 14. `content_hashes`

该对象必须且只能包含下列 11 个 lowercase SHA-256：

- `calendar_sha256`
- `daily_bars_sha256`
- `minute_bars_sha256`
- `market_status_sha256`
- `option_snapshot_sha256`
- `pit_inputs_sha256`
- `fee_schedule_sha256`
- `account_snapshot_a_sha256`
- `rule_package_sha256`
- `model_package_sha256`
- `source_qualification_receipts_sha256`

每个值必须等于对应 normalized domain 的 canonical JSON hash。validator 随后对完整 normalized entry 计算 `entry_bundle_sha256`；该 digest 是 typed result 属性，不是 raw top-level key。

## 15. Implemented qualification result

`validate_entry_bundle` 返回的 `DataQualificationResultV1` exact fields 是：`schema_version`、`status`、`reason_codes`、`input_sha256`、`trusted_source_receipt_set_sha256`、`qualification_sha256`。Production candidate 的 trust-set hash 绑定 caller 提供、排序后的完整 canonical receipt hashes；`SYNTHETIC_ONLY` 固定绑定 canonical 空 trust set，外部参数不能改变同一 synthetic raw 的 qualification 或 technical bytes。raw bundle 不能自行填入或升级信任。

- `SYNTHETIC_ONLY` 固定为 `STRUCTURALLY_VALID_SYNTHETIC` + `SYNTHETIC_SOURCE_ONLY`。
- `PRODUCTION_CANDIDATE` 当前即使完整 receipt hash 属于 caller-owned trust set 且 capability gates 通过，也固定为 `DATA_NOT_QUALIFIED`，并带 `SOURCE_OBSERVATION_ATTESTATION_NOT_IMPLEMENTED`。原因是 capability receipt 只能证明 provider 能力，尚未把本次每个 domain content hash 绑定到可验证的 observation/ingestion attestation。
- receipt trust、capability、entitlement、精确 `UTC_NS_EXPLICIT` event/receive time semantics、coverage、complete universe、atomic snapshot 或 synchronization 任一不通过时，会叠加相应排序后的稳定 reason codes。

当前实现中，closed-schema、float/type、时间、stale quote、identity、universe completeness、fee/account alignment、派生算术范围或 content hash 失败会先抛出带稳定 reason code 的 `DataContractError`，不会返回一个 `DATA_NOT_QUALIFIED` typed result。`DATA_NOT_QUALIFIED` 当前专用于结构已经通过、但 production observation attestation 或 source capability gates 未全部闭合的情况；V1 没有可达到的真实 `DATA_QUALIFIED` 路径。

这只是数据资格，不输出 carrier PASS/FAIL、最终数量、preference 或 Y，也不修改当前 `f`、DecisionResult、HTML 或交易状态。

## 16. Technical provenance and numeric boundary

每项 materialized feature 的 `source_fact_hash` 是 `SELECTED_FORMULA_INPUTS_V1` 投影的 canonical SHA-256：它包含 catalog 声明的 `input_fields`，以及该公式本次实际选中的字段和值。比如 SMA50 只绑定最后 50 个 session 的 ordinal/close；未被公式读取的 volume 或账户字段不会改变该 feature 的 provenance。完整 raw bundle 的所有字段仍由 technical document 顶层 `entry_bundle_sha256` 绑定，因此局部 provenance 精确化不会丢失全量 lineage。

Entry、catalog、投影、materialized feature 与所有 canonical JSON/hash 输入只允许整数，不接受 JSON float。当前本地 American CRR reference engine 内部仍使用 Python IEEE/libm 数值运算，再量化为整数 ppm/nano_usd；其 Python/compiler/OS/machine fingerprint 是 `model_package.runtime_fingerprint_sha256` 的显式输入。

technical document 顶层只保存一次 `numeric_runtime_environment={schema_version,c_fenv_rounding_mode,c_fenv_rounding_value}` 及其 canonical SHA-256，避免按合约重复 receipt。每个 `MODEL.CONVERGENCE_FACTS_BY_CONTRACT` 只引用这一个环境 hash，并分别保留 CRR core 的 `core_run_sha256`；公开 `run_sha256` 是 `{schema_version=TECHNICAL_MODEL_COMPOSITE_RUN_BINDING_V1,core_run_sha256,numeric_runtime_environment_sha256}` 的 canonical SHA-256。typed seal 会重新计算该 composite binding；`NOT_CONVERGED` 仍记录环境 hash，但 core/run hash 必须为 null。相同 raw 与 formula version 若 runtime fingerprint 或 live C rounding mode 不符合冻结条件会 fail closed，而不会静默产出另一份被声称等价的 technical facts。若未来要求数值内核本身也完全禁止 binary float，必须另行实现并版本化定点或 Decimal 权威模型；V1 不把这件事写成已完成。
