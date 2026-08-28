# GLD Data Specification Manifest v0.1

Created: 2026-08-28 07:51 Asia/Shanghai

状态：`SPEC_V01_HASH_BOUND / CROSS_CONTRACT_STATIC_CHECK_PASS / OD04_REQUIRED`

## Bound owner decision

| Artifact | SHA-256 |
|---|---|
| [OD-03 Receipt](/Users/jake/Desktop/Trading%20platform/2026-08-28-0733-gld-long-call-owner-decision-od03-receipt.md) | `efeba1ebf7b159fdb9495df2d3470ee273439cedcef60b0405369f69aeb0c101` |

## Bound specifications

| Artifact | SHA-256 | Current terminal |
|---|---|---|
| [Underlying Normalization Contract](/Users/jake/Desktop/Trading%20platform/2026-08-28-0739-gld-underlying-normalization-contract-v0.1.md) | `024f8b276aa89b09a7573588ada0aa6a94de5de44d3973c26c98e3f452214f3f` | `ENTRY_FACTS_BLOCKED` |
| [Minimum Option Data Gap Contract](/Users/jake/Desktop/Trading%20platform/2026-08-28-0739-gld-minimum-option-data-gap-contract-v0.1.md) | `f1778e47bfc94d6a04b88a13ef554cdf37e9b9b9d800273c256887e7db283e03` | `DATA_GAPS_DEFINED / NO_ACQUISITION_AUTHORITY` |
| [Provenance and Sealed Protocol](/Users/jake/Desktop/Trading%20platform/2026-08-28-0739-gld-provenance-license-contamination-sealed-protocol-v0.1.md) | `adeb27be0f3342cff0794a15532dd49f5c99bc6e7dd5e337fb626c47c2a9d3e7` | `LEGACY_DEVELOPMENT_ONLY / FORWARD_REQUIRED` |

## Cross-contract invariants checked

- H0 是入场 session；H1..H20 是后续第 1–20 个 XNYS sessions。
- 若没有更早退出，最晚 H20 进入退出流程。
- `minimum_expiry_date = H20_date + 30 calendar days`。
- same-expiry roll 不重置 H0/H20，不延长 expiry。
- 所有 decision snapshot 同时受 `ts_event` 和 causal availability/receive time 限制。
- 任一关键输入、报价、Delta、费用、lifecycle 或 provenance 未证明即 fail closed。
- 旧 underlying、旧 35/74 events 和旧 options artifacts 只作 Development，不得作为 historical Sealed。
- Databento 连接、补数、付费、broker 和交易权限均未被授予。

## Remaining owner gate

`OD-04`：是否接受在 v1 中将 regular open 后第一分钟 `[09:30:00, 09:31:00) America/New_York` 整体标记为 `OPENING_TRANSITION_UNCLASSIFIED`，并从 fast confirmation 输入中排除。

在 OD-04 确认前，不实现 normalizer，不选 post-open cutoff，不生成 provider query，不运行回测。

