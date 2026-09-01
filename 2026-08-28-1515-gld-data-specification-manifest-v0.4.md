# GLD Data Specification Manifest v0.4

Created: 2026-08-28 15:15 Asia/Shanghai

状态：`OD06_HASH_BOUND / DUAL_CARRIER_SCOPE_FROZEN / LOCAL_SYNTHETIC_IMPLEMENTATION_ALLOWED / NO_OUTCOME_ACCESS`

## Parent

| Artifact | SHA-256 |
|---|---|
| [Data Specification Manifest v0.3](/Users/jake/Desktop/Trading%20platform/2026-08-28-0959-gld-data-specification-manifest-v0.3.md) | `be67b05e7ef88c901ad7c8e2c0857befbbf89044f5ef0c85c808bd077f2699a8` |

## New bindings

| Artifact | Role | SHA-256 | Supersedes |
|---|---|---|---|
| [GLD Research Contract v0.2](/Users/jake/Desktop/Trading%20platform/2026-08-28-1515-gld-research-contract-v0.2.md) | Carrier, snapshot and comparison rules | `3940c27b4aec3585b9aa904a96f2118178589fafdcee46412441431ff9fcb8fc` | Research Contract v0.1 的 Long Call-only scope 与 carrier-specific Gate A terminal |
| [OD-06 Receipt](/Users/jake/Desktop/Trading%20platform/2026-08-28-1515-gld-dual-carrier-owner-decision-od06-receipt.md) | Owner scope decision | `f16d8a44ba3b72a81db335fd52c440cfe78c15c5e9004151d074bb0a0fd5f5a7` | Manifest v0.3 的 `STRUCTURE_SCOPE_REVIEW_PENDING` |
| [Minimum Option Data Gap Contract v0.2](/Users/jake/Desktop/Trading%20platform/2026-08-28-1515-gld-minimum-option-data-gap-contract-v0.2.md) | Dual-leg synchronized data and lifecycle | `7f2ef0e62f5954d3893b04b4e6fd0d8823a06c4fdb8b8a30827165ea06bcdcd7` | Minimum Option Data Gap Contract v0.1 的 single-leg/roll-only gap scope |
| [GLD Call Delta CRR Model Contract v0.1](/Users/jake/Desktop/Trading%20platform/2026-08-28-1515-gld-call-delta-crr-model-v0.1.md) | Authoritative local Delta algorithm | `06a4e216aa0a0f0c2e7192027ac5a19d288f799d32da59bf632bb26e7678caa8` | v0.1 未决的 D1/D2 Delta path |

## Frozen behavior

- OD-01 至 OD-05、opening-phase rules、`[09:31,10:45)` fast-confirmation window 和 `10:45:00 America/New_York` cutoff 继续有效。
- `SignalSnapshotV1` 只消费 cutoff 前 event 且前 receive 的 Gate A 事实。
- `OptionQuoteSnapshotV1` 在 `[10:45,10:46)` 选择第一份纯数据质量合格快照；不得等待更优价格。
- Gate A 终局改为 `ENTRY_QUALIFIED_FOR_CARRIER_INTEGRATION`。
- 主 carrier 比较为 `LC0_LONG_CALL_NO_ROLL` 对 `BCS0_DEBIT_CALL_SPREAD_NO_ROLL`。
- BCS 是一个固定 challenger，不是 fallback；Long Call strike-up roll 是下游独立 challenger。
- BCS long leg 等于 Long Call winner；short Call target Delta `0.25`、band `[0.20,0.30]`、同 expiry、严格更高 strike、1:1。
- 本地 Delta 只允许 `GLD_CALL_DELTA_CRR_AM_V1`；broker Greeks 与 BSM 为 shadow-only。
- 两个 carrier 都通过时为 `OWNER_SELECTION_REQUIRED`，程序不得自动选结构。
- BCS 不 roll；决策卡只允许一个原生组合人工订单的事实描述，`broker writes=0`。

## Fail-closed invariants

- 相同已验证输入、research contract hash、model hash 和 rule version 产生相同结构化输出。
- LLM 不得修改状态、合约、腿、数量、价格、费用、风险数字或条件。
- 缺事实、模型不收敛、来源未资格、同步性不明、费用不全或 IBKR 无合格 executable quote 均为 `NO_DECISION`。
- IBKR 无合格报价时产生本地 blocking notification，order count 保持 0；不得 silent fallback。
- Robinhood 只作 read-only shadow，不得跨 broker 拼腿或被标为 IBKR executable price。
- partial fill、残腿、assignment/exercise、identity adjustment 或 broker mismatch 为 `RECONCILIATION_BLOCKED`，阻断新开仓。
- Manifest/hash verification 只证明文件绑定，不证明 source authority、数据完整性、策略收益或当前可行动。

## Still unresolved

- 每日 management checkpoint 精确时刻；
- invalidation checkpoint 语义；
- `Q_path`；
- effective-dated fee source；
- clean sealed/forward interval；
- 最终总 trial budget；
- IBKR 与 Robinhood quote-source qualification receipts；
- Databento/等价历史 BBO query 的精确成本。

## Still prohibited

- outcome/P&L 访问、回测、参数选择或收益结论；
- Databento/broker/private-data 连接、认证、查询、补数、购买或下载；
- 把 synthetic fixtures、MCP schema 或本地测试升级为真实 source qualification；
- broker order 创建、提交、修改或取消；
- 自动交易、部署或发布。

当前只允许继续本地 deterministic core、synthetic fixtures、hash verification、单元测试和 fail-closed 集成测试。
