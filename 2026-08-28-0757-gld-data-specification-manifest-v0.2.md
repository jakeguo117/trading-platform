# GLD Data Specification Manifest v0.2

Created: 2026-08-28 07:57 Asia/Shanghai

状态：`OD04_HASH_BOUND / OPENING_PHASE_FROZEN / NORMALIZER_CORE_ALLOWED / CUTOFF_DECISION_PENDING`

## Parent

| Artifact | SHA-256 |
|---|---|
| [Data Specification Manifest v0.1](/Users/jake/Desktop/Trading%20platform/2026-08-28-0751-gld-data-specification-manifest-v0.1.md) | `c4fd1e74f7bbc8dcc8849ac2b9c1a0612855d3cf507c774b2c08965319355bb3` |

## OD-04 binding

| Artifact | SHA-256 |
|---|---|
| [OD-04 Receipt](/Users/jake/Desktop/Trading%20platform/2026-08-28-0757-gld-long-call-owner-decision-od04-receipt.md) | `aa4ae6b8576657c2636f678a8855aab374e10912a3dc10109d04e2023143f13a` |
| [Opening Phase Addendum](/Users/jake/Desktop/Trading%20platform/2026-08-28-0757-gld-opening-phase-addendum-v0.1.md) | `bdcb665cea35918287954b0e615f251fda66b3b84e772a464897676bb79cf531` |

## Frozen behavior

- `[09:30:00, 09:31:00) America/New_York` 标记 `OPENING_TRANSITION_UNCLASSIFIED`。
- 该半开区间内的 records/bars 对 fast-confirmation consumer 不可见。
- 09:31:00 之后的数据仍须通过 phase/completeness/PIT gates，不自动成为可用输入。
- 任何未来引入 opening minute 的方案都属于新规则版本，需另行资格和 Owner Gate。

## Allowed next work

- 实现不依赖 exact cutoff 的 normalizer core 与 synthetic/contract tests；
- 查证权威 XNYS calendar 与 GLD corporate-action/no-action 证据；
- 形成最多两个 exact post-open cutoff 候选并将每个记入 trial ledger。

## Still prohibited

- outcome/P&L 访问、回测或参数选择；
- Databento/broker 连接、补数、付费或交易；
- 将旧数据升级为 historical Sealed。

