# GLD Underlying Normalization Contract v0.1

Created: 2026-08-28 07:39 Asia/Shanghai

状态：`DESIGN_READY / ENTRY_FACTS_BLOCKED / NO_OUTCOME_ACCESS`

## 1. 目的与边界

本合同只把两段已核验的 GLD `trades` 确定性转换为 Gate A 可使用的 point-in-time 日线和 post-open 分钟事实。

本合同不：

- 计算 Entry signal、未来 return、P&L、MFE 或 MAE；
- 选择 cutoff、参数、期权合约、数量或退出；
- 连接 provider/broker，也不读取 account 数据；
- 把旧数据升级为 Sealed。

时钟绑定 [OD-03 Receipt](/Users/jake/Desktop/Trading%20platform/2026-08-28-0733-gld-long-call-owner-decision-od03-receipt.md)：H0 为入场 session，H1..H20 为后续 20 个 XNYS sessions，`minimum_expiry_date = H20_date + 30 calendar days`。

## 2. 输入身份绑定

### Input A｜历史主体段

- 路径：[historical CSV.zst](</Users/jake/Desktop/GLD_MD_Project_2026-08-08/data/private/f3-momentum-recovery-v0.1/batches/h0-historical-underlying-v0.1/raw/0001-equs-mini-gld-trades-2023-03-28-through-2025-01-14.csv.zst>)
- Format：`CSV_ZSTD`
- SHA-256：`9ee80806c4e5d212faf19a49251f50d55b8351ed13f5846bdff0732b64827288`
- Bytes：`57,390,794`
- Rows：`2,099,775`
- `ts_event` min/max ns：`1680003515638704515 / 1736898604107396607`
- Header：`ts_recv,ts_event,rtype,publisher_id,instrument_id,action,side,depth,price,size,flags,ts_in_delta,sequence`

### Input B｜延伸段

- 路径：[extension DBN.zst](</Users/jake/Desktop/GLD_MD_Project_2026-08-08/data/private/f3-momentum-recovery-v0.1/batches/plugin-mvp-v0.1/sample-extension-gld-trades.dbn.zst>)
- Format：`DBN_V1_ZSTD`
- SHA-256：`edf34c3bd0ac71c26fc92c128156feb966930fff385aa30a8519404e382b5d7a`
- Bytes：`38,282,792`
- Rows：`2,528,516`
- `ts_event` min/max ns：`1736944684009092478 / 1786751496671499233`
- DBN metadata bytes：`198`；`rtype=0`；record length `48`；one publisher；one instrument。

两份输入同时绑定 `EQUS.MINI / trades / request symbol=GLD / USD`。row 内没有 symbol 列，所以必须同时验证 request manifest；不得只根据 instrument ID 推断 GLD。

任一 path、bytes、hash、row count、schema 或 min/max 不符合即 `INPUT_BINDING_MISMATCH`，不产生事实表。

## 3. 权威 XNYS Calendar

必须构建独立、版本冻结的 `XNYS_CALENDAR_V1`：

1. 权威输入是 NYSE 官方 trading-hours/holiday 资料的本地不可变副本；
2. 绑定 source URL、retrieved-at、effective range 和 SHA-256；
3. 确定性 builder 生成每个 session；
4. `exchange_calendars` 等 package 只做 cross-check，不是权威来源；
5. 官方来源与 package 冲突时为 `CALENDAR_CONFLICT`。

每行至少包含：

```text
calendar_version, calendar_source_sha256, session_date
timezone=America/New_York, is_trading_session
regular_open_local, regular_close_local
regular_open_utc_ns, regular_close_utc_ns
is_half_day, holiday_name
session_ordinal, previous_session_date, next_session_date
```

规则：

- open/close 来自 calendar，不用第一笔/最后一笔 trade 推断；
- half-day 计一个 session，weekend/holiday 不计；
- 时区使用版本冻结的 IANA tz database；
- 覆盖最早数据前的 slow-trend warmup，以及最后数据后 H20 + 30 日历日。

为每个 candidate H0 生成 `h1_date..h20_date, minimum_expiry_date, underlying_h0_h20_coverage_state`。

## 4. 时间和 Point-in-Time

所有原始时间戳统一为 Unix UTC nanoseconds，并保存：

```text
ts_event_ns, ts_recv_ns, ts_event_utc, ts_recv_utc
session_date, event_local_time, utc_offset_seconds, timezone_version
```

历史 provider availability proxy 为 `provider_available_at_ns = ts_recv_ns`，同时必须标记：

```text
availability_basis = PROVIDER_TS_RECV_PROXY
runtime_available_at_proven = false
```

某一 decision cutoff 只能使用：

```text
ts_event_ns < cutoff_ns AND ts_recv_ns < cutoff_ns
```

所有 interval 均为 `[start, end)`。cutoff 后才收到的迟到记录不能回填该次 decision snapshot。最终 reconciled bar 与 causal-as-of bar 是两个对象；Gate A 只消费后者。

## 5. Canonical Trade Schema

`normalized_trade_fact_v1`：

```text
normalization_contract_hash
source_file_id, source_file_sha256, source_format, source_record_ordinal
dataset, source_schema, canonical_symbol, symbol_binding_basis, currency
publisher_id, instrument_id, rtype, action, side, depth
price_nano_usd, size, flags, ts_in_delta, sequence
ts_event_ns, ts_recv_ns, provider_available_at_ns
session_date, market_phase
natural_key_hash, economic_payload_hash, raw_record_hash, record_state
```

- `price` 保留 provider signed int64 nanounits，不转 float；
- `size` 保留无损整数；
- CSV/DBN decoder 必须产生相同 canonical types；
- 不在日志输出 price/size；
- 未知 enum/record semantics 为 `UNSUPPORTED_SOURCE_SEMANTICS`。

全局 causal sort：

```text
(ts_recv_ns, ts_event_ns, publisher_id, instrument_id,
 sequence, source_file_id, source_record_ordinal)
```

OHLC 顺序：先应用已证明的 action/correction，再按 `(ts_event_ns, sequence, ts_recv_ns, source_file_id, source_record_ordinal)` 确定 open/close。

## 6. Duplicate 和 Retransmission

```text
natural_key = dataset | publisher_id | instrument_id | rtype | ts_event_ns | sequence
economic_payload = action | side | depth | price_nano_usd | size | flags | ts_in_delta
```

- 相同 key + 相同 payload：保留最早 `ts_recv_ns`，其余进 `duplicate_ledger_v1`，不重复计 volume/trade count；
- 相同 key + 不同 payload：只有 provider-versioned correction contract 能解析时处理，否则 `CONFLICTING_RETRANSMISSION`；
- 必须全局检查，不只查相邻记录；
- Input B 已观察到至少 714 条相邻 exact duplicates；714 不是最终总数上限。

## 7. Opening Phase

当前 trades 数据没有已资格化的 auction-condition 字段，因此不得仅凭 09:30 时间戳声称某笔是 auction 或 continuous。

phase enum：

```text
PREMARKET, CORE_OPEN_AUCTION, OPENING_TRANSITION_UNCLASSIFIED
CORE_CONTINUOUS, HALT_OR_REOPEN_AUCTION, POSTMARKET, UNKNOWN
```

建议 fail-safe v1：

- `[regular_open, regular_open + 1 minute)` 标记 `OPENING_TRANSITION_UNCLASSIFIED`，不进入 fast confirmation；
- `[regular_open + 1 minute, regular_close)` 才可能为 `CORE_CONTINUOUS`；
- 如果未来需要消费 09:30 minute，必须先补齐 version-bound auction/status/condition source。

该建议仍需 OD-04 Owner 确认。

## 8. Corporate Actions 和 Adjustment

原始价格始终保留 raw。另建 `GLD_CORPORATE_ACTION_LEDGER_V1`：

```text
action_id, action_type, announcement_at, available_at
ex_date, effective_at, split_ratio, cash_distribution, currency
source_id, source_version, source_sha256
```

- 必须有 action 或明确 `NO_ACTION_COVERAGE`，不得从缺文件推断无 action；
- 只能使用当时已生效且已可得的 action；
- 不得用今天的全历史 adjusted close 回填过去；
- split 和 cash distribution 分开表达。

输出分层：`session_ohlcv_raw_v1`、`session_ohlcv_split_adjusted_pit_v1`、可选 `session_total_return_fact_v1`。Gate A 使用哪层由后续 Entry Contract 冻结。

## 9. Bars 和 Completeness

分钟表至少保存 OHLC、volume、trade_count、first/last event/receive time、market phase、duplicate count、bar state 和 bar hash。

日线表至少保存 session open/close、half-day、OHLCV、first/last event、eligible/missing/unknown-phase minute counts、completeness state 和 session hash。

不 forward-fill OHLC。无成交分钟只有在 transport completeness 已证明时才是 `OBSERVED_EMPTY`；否则为 `MISSING_UNKNOWN`，不得被消费。

某一 Entry decision 的 slow lookback、H0 opening-to-cutoff 以及 H0..H20 coverage 必须全部 `COMPLETE`。不得用整体 95% 覆盖率替代某个 decision 的 100% 所需输入。不合格 sessions 保留在 denominator。

## 10. 权威输出与 Hash

1. `normalized_trade_fact_v1.jsonl.zst`
2. `duplicate_ledger_v1.jsonl.zst`
3. `calendar_session_v1.jsonl`
4. `minute_trade_bar_v1.jsonl.zst`
5. `session_ohlcv_raw_v1.jsonl`
6. `session_ohlcv_adjusted_pit_v1.jsonl`
7. `h0_h20_calendar_map_v1.jsonl`
8. `session_data_quality_v1.jsonl`
9. `normalization_manifest_v1.json`

Canonical serialization：UTF-8、LF、keys lexicographic、无多余空格、null 显式、大整数/纳秒/价格以十进制字符串表示。SHA-256 计算在未压缩 canonical bytes；压缩文件另有 hash。

Manifest 绑定 contract/code/input/calendar/timezone/corporate-action/schema hashes、row/time range、duplicate/conflict/session counts、所有输出 hashes 和 terminal status。

## 11. Fail-Closed 终局

```text
INPUT_BINDING_MISMATCH
INPUT_SCHEMA_UNSUPPORTED
INPUT_TRUNCATED
SYMBOL_BINDING_UNPROVEN
INVALID_TIME_FACT
CALENDAR_MISSING
CALENDAR_CONFLICT
DUPLICATE_CONFLICT
SOURCE_ACTION_SEMANTICS_UNQUALIFIED
AUCTION_PHASE_UNRESOLVED
CORPORATE_ACTION_UNQUALIFIED
ADJUSTMENT_CONFLICT
SESSION_COVERAGE_INCOMPLETE
OUTPUT_HASH_MISMATCH
RAW_FACTS_READY_ADJUSTED_ENTRY_BLOCKED
ENTRY_FACTS_READY_FOR_GATE_A_PREREGISTRATION
```

只有最后一个状态可以进入 Gate A preregistration；仍不自动授权回测。

## 12. 验收集

必须用真实文件只读 contract tests 和 synthetic semantics tests 验证：

- 两输入 hash/bytes/schema/rows/min-max 精确匹配；
- CSV/DBN 同义记录生成相同 canonical record；
- 全局 duplicate 不重复计 volume，conflicting payload fail closed；
- late receive 不泄漏至早期 cutoff；
- DST、holiday、half-day、H1/H20 和 H20+30 日历日映射确定；
- opening transition、halt/reopen unknown、empty-vs-missing 分离；
- action/no-action 不足时 adjusted output 为空；
- 两次运行产生相同 canonical hashes；
- network/provider/broker/account/P&L/outcome calls 为 0。

## 13. 当前 Blockers

1. 权威 XNYS calendar 本地 artifact；
2. GLD corporate-action/no-action ledger；
3. OD-04：是否接受隔离整个 09:30–09:31 opening-transition minute。

