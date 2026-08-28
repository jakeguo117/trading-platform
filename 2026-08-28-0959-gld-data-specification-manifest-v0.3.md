# GLD Data Specification Manifest v0.3

Created: 2026-08-28 09:59 Asia/Shanghai

状态：`OD05_HASH_BOUND / 1045_CUTOFF_FROZEN / STRUCTURE_SCOPE_REVIEW_PENDING / NO_OUTCOME_ACCESS`

## Parent

| Artifact | SHA-256 |
|---|---|
| [Data Specification Manifest v0.2](/Users/jake/Desktop/Trading%20platform/2026-08-28-0757-gld-data-specification-manifest-v0.2.md) | `0c735a1a3b0c4a060996fd5b2f451274d295f2e2ed34d0ed78048a244be24b1c` |

## OD-05 binding

| Artifact | SHA-256 |
|---|---|
| [OD-05 Receipt](/Users/jake/Desktop/Trading%20platform/2026-08-28-0958-gld-long-call-owner-decision-od05-receipt.md) | `234a322c849be9170a93f96c7a5988d2a6146a9f2ac09274c370fab482bc0fd7` |
| [Post-open Cutoff Addendum](/Users/jake/Desktop/Trading%20platform/2026-08-28-0958-gld-post-open-cutoff-addendum-v0.1.md) | `7e9fca098968aff9099360c237c1eb0c1e60c2dede7101541f1a4272c8cd1061` |

## Frozen behavior

- `[09:30:00, 09:31:00) America/New_York` 继续被排除。
- fast-confirmation window 固定为 `[09:31:00, 10:45:00)`。
- decision cutoff 固定为 `10:45:00 America/New_York`，计一个 clock trial。
- 只允许 cutoff 前 event 且 cutoff 前 receive 的已资格事实；禁止 late-data 回填和 clock fallback。

## Next unresolved owner boundary

当前 Research Contract 明确只研究 `Long Call`；same-expiry strike-up roll 仍是 Long Call 生命周期 challenger，不是第二种 Entry carrier。旧项目中的 Call/BCS 材料只作历史研究参考，未形成获准的 carrier selector。

Owner 已提出结构范围问题。在确认“继续 Long Call only”或“重开 carrier universe”之前，不启动 Gate B carrier economics，也不把旧 BCS 观察升级为当前规则。

## Still prohibited

- outcome/P&L 访问、回测或参数选择；
- Databento/broker 连接、补数、付费或交易；
- 将 10:45 的设计理由表述为收益证据；
- Long Call 不合格时 silent fallback 到 BCS、股票或其他结构。

