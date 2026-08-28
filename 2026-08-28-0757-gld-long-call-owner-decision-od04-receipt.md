# GLD Long Call Owner Decision OD-04 Receipt

Created: 2026-08-28 07:57 Asia/Shanghai

状态：`OD04_CAPTURED / OPENING_TRANSITION_EXCLUDED / NORMALIZER_DESIGN_UNBLOCKED`

## Owner decision

- `OD-04 = A_EXCLUDE_OPENING_TRANSITION_MINUTE`
- Timezone：`America/New_York`
- Excluded half-open interval：`[09:30:00, 09:31:00)`
- 该分钟全部标记为 `OPENING_TRANSITION_UNCLASSIFIED`。
- 该分钟不得进入 slow/fast signal 的 post-open fast-confirmation 输入。
- v1 fast confirmation 只能消费 09:31:00 之后、已通过 session/phase/completeness 资格的 causal-as-of 事实。
- 如未来需要使用开盘第一分钟，必须作为新规则版本，先补齐 version-bound auction/status/condition source 并重新验证。

## Non-meaning

该决定不冻结 exact post-open cutoff，不决定慢趋势、快确认、阈值、合约、数量、退出或 roll 触发条件。

## Parent specification identity

- Parent manifest：`2026-08-28-0751-gld-data-specification-manifest-v0.1.md`
- Parent manifest SHA-256：`c4fd1e74f7bbc8dcc8849ac2b9c1a0612855d3cf507c774b2c08965319355bb3`

## Authorized next boundary

- 允许实现不依赖 exact cutoff 的 normalizer 核心与 synthetic/contract tests。
- 允许形成 exact post-open cutoff 的最多两个预注册候选。
- 当前仍不允许 outcome/P&L 访问、回测、Databento/broker 连接、补数、付费或交易。

