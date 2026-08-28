# GLD Minimum Option Data Gap Contract v0.1

Created: 2026-08-28 07:39 Asia/Shanghai

状态：`OD03_BOUND / DATA_GAPS_DEFINED / NO_ACQUISITION_AUTHORITY / NO_OUTCOME_ACCESS`

## 1. 目的

本合同只定义 Gate B 最少需要什么数据、什么算完整，以及缺数时如何 fail closed。它不授权连接 Databento/broker，不产生交易规则、回测、订单或 Plugin。

## 2. 时钟和 Expiry 资格

- H0 是入场 session；H1..H20 是后续第 1–20 个 XNYS sessions。
- 若没有更早退出，所有候选最晚 H20 进入退出流程。
- 合格 expiry：`expiration_date - H20_session_date >= 30 calendar days`。
- 如果 last-trading-date 早于 legal expiration，还必须证明 H20 仍可交易。
- same-expiry strike-up roll 不重置 H 时钟，新 Call 仍最晚 H20 退出。
- H20 不可执行时不得虚构成交：记 `EXIT_DUE_NONEXECUTABLE`，exit latch 保持。

## 3. H0 完整 PIT Opportunity Universe

每个 H0 cutoff 必须重建当时真实可见的完整候选集：

- GLD Call；standard、unadjusted；已激活、未停牌、未到期；
- expiry 满足 H20 + 30 日历日；
- 包含 causal-Delta selector 可能选择的全部 strikes，不得只下载 winner；
- 每个候选必须有 identity、right/CFI、strike、expiration、activation、last-trading-date、multiplier、deliverable、currency、adjusted status、publisher、event/receive time、bid/ask/size/flags 以及 Delta 或可复算输入。

Expiry selector 是：从 H0 PIT 完整集合中选择满足安全边界的最早 expiry。无合格 expiry 为 `NO_ELIGIBLE_EXPIRY`，不得事后人工挑选。

## 4. Delta 路径

读取 outcome 前必须二选一冻结，运行中禁止 silent fallback。

### D1｜供应商 causal Delta

必须有 Delta value、observation timestamp、vendor/model/version、输入价格 as-of、corporate-action/distribution assumption、missing/revision flags。

### D2｜本地确定性复算

必须有同步 GLD valuation input、option bid/ask、strike、expiration 精确时间、multiplier、PIT risk-free curve、PIT 已知 distribution/borrow、IV/volatility 因果求解输入和失败状态，并冻结 model/version/day-count/calendar/rounding。

理论价或 midpoint 只可用于 Delta 计算，不得代替 entry ask 或 exit bid。

## 5. H0..H20 Checkpoints

每个 episode 必须有：

- H0：完整 opportunity universe 及 selected Call 同步快照；
- H1..H20：每日固定 management checkpoint；
- Gate A invalidation 如果发生在固定 checkpoint 之外，另存精确 trigger checkpoint；
- H20：预注册退出 checkpoint；任何同 session retry 必须事先冻结，不得延伸至 H21。

每个适用 checkpoint 至少采集：

1. held Call 的 bid/ask/size/flags/event/receive time；
2. 同 expiry、严格更高 strike 的全部合格 Calls 同步双边报价；
3. held/roll candidates 的 causal Delta 或复算输入；
4. GLD 与 Gate A invalidation 的 PIT 输入；
5. 当日 security-definition/lifecycle updates。

在 target Delta band 和 hysteresis 冻结前，必须保留全部更高 strikes。

## 6. Roll 第二快照

B2 不得用一张快照同时假设卖出旧 Call 和买入新 Call：

1. 第一快照确认旧 Call 有 executable bid；
2. 旧仓完整退出；
3. 按冻结延迟取得第二份 fresh snapshot；
4. 使用第二快照重新选择同 expiry、更高 strike Call；
5. 使用新 ask、size、Delta 和全账户风险重新审批。

缺第二快照、旧仓未确认退出或新 Call 不再合格时，不 roll；不得恢复旧仓或重置 H 时钟。

## 7. B0/B1/B2 数据包

- `B0_MAX_HOLD_CONTROL`：H0 ask；held Call H1..H20 完整报价路径；H20 bid/size；policy/expiry/lifecycle 异常。
- `B1_SIGNAL_EXIT`：B0 + Gate A invalidation 因果输入、首次成立时点、latched state 及对应 option bid/size。
- `B2_SIGNAL_EXIT_ONE_ROLL_UP`：B1 + 每个 checkpoint 的 same-expiry higher-strike universe、roll 两快照、新 Call 从 roll session 至 H20 的连续路径。

数据必须能重放优先级：

```text
latched EXIT_DUE > policy full exit > expiry safety
> Gate A invalidation > H20 maximum hold > roll review > hold
```

## 8. Fill、No-Fill 和费用

Base fill：

- Entry：`displayed ask × multiplier + entry fees`；
- Exit：`displayed bid × multiplier - exit fees`；
- Roll：`old bid credit - old exit fees - new ask debit - new entry fees`；
- 禁止 midpoint、last、theoretical price 或假定 price improvement；
- Stress 至少保存每 side 一枚 adverse tick。

Executable quote 必须同时满足：非 stale、非无效 crossed/locked、正 bid/ask、size 足够、flags 合格、所有同步对象的 receive skew 在冻结上限内。

不假设 partial fill。原因码至少包含 `SIZE_INSUFFICIENT, QUOTE_MISSING, QUOTE_STALE, QUOTE_CROSSED, ZERO_BID, IDENTITY_UNRESOLVED, MARKET_HALTED, SECOND_SNAPSHOT_MISSING`。必须区分真实无报价与数据缺失。

Fee table 必须按生效日版本化，覆盖 broker commission、exchange/clearing/regulatory/pass-through fees，并绑定 fee-schedule hash。

## 9. Lifecycle

从 H0 至关闭必须保存 initial definition、definition updates、symbol/instrument mapping、split/distribution、deliverable/multiplier 变更、halt/delist/expiration/last-trading-date 以及 adjusted contract 身份映射和可关闭报价。

H0 只选 standard/unadjusted。持有后发生 adjustment 时不得静默删除或当作原合约；无法重建身份为 `LIFECYCLE_UNRESOLVED`。

## 10. 最小 Acquisition Matrix

| 数据块 | 旧数据可复用 | 必须补数/补证 |
|---|---|---|
| XNYS calendar | 旧 session dates 只做开发核对 | 权威、版本化完整 calendar |
| Gate A/underlying | 旧 causal artifacts 只作 Development | 新规则完整 PIT path |
| H0 option universe | 旧 35 entry dates 只作 schema/selector development | 新 Gate A 全机会集和 evaluation/sealed |
| Selected entry | 旧 selected legs 可作原型 | 新 cohort 同步 snapshots |
| Held Call path | 旧 H2 可作原型，未证明 H0–H20 完整 | 新 episodes 完整路径 |
| Higher-strike path | 旧 H1 只有 entry-date 广泛链 | H1–H20 同期 BBO/size/Delta |
| Roll 第二快照 | 未证明存在 | 全部 roll-review checkpoints |
| Delta | 旧六件 artifact 无 Delta | D1 或 D2 完整路径 |
| Lifecycle | 旧 entry definitions 可部分复用 | H1–H20 updates/adjustment mapping |
| Fees | 未发现 | 完整 effective-dated fee schedule |
| Trades | Base fill 非必需 | 除非预注册诊断，否则不补 |
| Clean validation | 旧 35 日只作 Development | 未见历史或 forward |

## 11. Hard Coverage Gates

1. `HASH_INTEGRITY`：manifest/raw/normalized/schema hashes 一致。
2. `UNIVERSE_COMPLETE`：H0 是 PIT 完整集合，非 winner-only。
3. `IDENTITY_COMPLETE`：selected/held/roll candidates 的 identity/lifecycle 100% 可解析。
4. `PIT_CAUSAL`：所有输入在 decision checkpoint 前已可得。
5. `DELTA_REPRODUCIBLE`：所有排名候选使用同一 Delta 路径。
6. `CRITICAL_QUOTE_OBSERVED`：H0 entry、首次 exit-due、H20、roll 两快照均可区分有效报价、真实 no-quote 与缺数。
7. `PATH_COMPLETE`：scorable episode 覆盖全部适用 checkpoints；缺失 episode 仍留在 denominator。
8. `COST_COMPLETE`：每个 side 都有 multiplier 和费用。
9. `LIFECYCLE_COMPLETE`：持有期间可重建。
10. `SEALED_ISOLATED`：sealed 数据与 hashes 未被选择参数的 Agent 接触。

## 12. Hash 绑定

每批至少生成：

```text
acquisition_spec_hash, provider_query_hash, raw_artifact_sha256
schema_hash, normalized_artifact_sha256, calendar_hash
security_definition_hash, checkpoint_schedule_hash
delta_model_or_vendor_hash, fee_schedule_hash
cost_fill_contract_hash, parent_manifest_hash
```

Manifest 不得包含 credentials。

## 13. 终局

合同级：

```text
NOT_ACQUIRED, ACQUIRED_UNVERIFIED, HASH_MISMATCH, SCHEMA_MISMATCH
PIT_NOT_PROVEN, UNIVERSE_INCOMPLETE, DELTA_INPUT_INCOMPLETE
CHECKPOINT_COVERAGE_INCOMPLETE, COST_MODEL_INCOMPLETE
LIFECYCLE_INCOMPLETE, CONTAMINATED_DEVELOPMENT_ONLY
DATA_QUALIFIED_DEVELOPMENT, DATA_QUALIFIED_SEALED
```

Episode/checkpoint 级：

```text
DATA_MISSING, OBSERVED_NONEXECUTABLE, NO_ELIGIBLE_EXPIRY
NO_ELIGIBLE_STRIKE, ENTRY_NO_FILL, EXIT_DUE_NONEXECUTABLE
ROLL_OLD_EXIT_NO_FILL, ROLL_NEW_ENTRY_NO_FILL, LIFECYCLE_UNRESOLVED
```

只有 `DATA_QUALIFIED_*` 可进入对应研究波次。

## 14. 验收集

使用 synthetic fixtures 和 metadata，在 0 outcome/P&L 访问下验证：

- H20/expiry selector 跨 holiday/half-day 稳定；无 expiry fail closed；
- 同一 PIT snapshot 重放得到相同 Call identity；
- cutoff 后报价不泄漏；Delta 缺输入不 fallback 到 moneyness；
- stale/crossed/zero-bid/size/feed-missing 分别产生不同 reason code；
- B0 H20 exit due；B1 首次 invalidation latch；B2 最多一次 roll、先卖后买、不重置 H 时钟；
- exit 优先于 roll；adjustment 无法映射则 fail closed；
- fill 严格使用 ask/bid/multiplier/fees；
- 缺任一 critical checkpoint 保留 denominator 但不进 numerator。

## 15. 还需冻结才能形成唯一补数请求

- 每日 management checkpoint 精确时刻；
- invalidation trigger 时点语义；
- quote age、receive skew、size 门槛；
- D1 或 D2 Delta 路径；
- episode path coverage promotion threshold `Q_path`；
- fee schedule 来源；
- clean Sealed/Forward 区间。

这些未冻结前不产生 provider query，否则容易第二次缺数。

