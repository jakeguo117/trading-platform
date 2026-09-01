# GLD Management Research F0 Owner Acceptance Receipt v0.1

## Decision

| field | value |
|---|---|
| `decision_id` | `GLD_MANAGEMENT_RESEARCH_F0_OWNER_ACCEPTANCE_V1` |
| `decision` | `OWNER_ACCEPTED_COMPLETE` |
| `owner` | `Jake` |
| `accepted_at` | `2026-09-01 12:29 Asia/Shanghai` |
| `product` | `Trading Platform` |
| `feature` | `GLD 持仓管理` |
| `version` | `F0 研究验证版` |
| `authority_effect` | `NONE` |

Jake 的原话：

> 呃我看了一下，大概都没什么问题，我觉得我这边可以 approve。

## Accepted scope

Jake 接受简版 Owner 首页中的六组业务判断，以及它们所代表的本地 synthetic F0 行为：

1. 中性环境保持仓位。
2. 转弱先等待，连续确认后分层减仓。
3. 恢复先确认，之后逐层加回且不超过原始批准仓位。
4. Hard Stop 和终局锁定覆盖高分。
5. 旧报价或缺失数据停止行动。
6. BCS 始终按完整 1:1 组合管理。

这次接受不声称 Jake 逐项审阅了14份工程JSON、hash或内部reason code；这些场景与程序结果的一致性由自动证据和独立代码复审承担。

## Bound evidence

| evidence | value |
|---|---|
| acceptance manifest path | `artifacts/gld-management-score-f0-owner-acceptance-v1/acceptance-manifest.json` |
| acceptance manifest file SHA-256 | `535749fbd33cbd26f7452fa9c08dabdc81037a243a97ebacc7725195771269f7` |
| manifest canonical SHA-256 | `c42fafcd7733ccb1b10d99928cbdd6fad69c48780615db039b079b0fb93e6ced` |
| accepted asset revision | `GLD_INDICATOR_MANAGEMENT_RESEARCH_ASSET 0.6` |
| accepted asset revision 0.6 SHA-256 | `90d64d583add318ff0d4205b07bff73e0fe570a61aba35c9064ef0bd290d032f` |
| F0 focused tests | `49 / 49 PASS` |
| full local regression | `556 / 556 PASS` |
| independent review | `APPROVE; P0=0 / P1=0 / P2=0` |

自动生成的manifest及场景证据继续保持`OWNER_ACCEPTANCE_PENDING`。它们证明程序结果，不代替或伪造Owner签字；本回执单独记录人工决定。

## Not accepted or proven

这次批准不表示接受或证明以下事项：

- 真实GLD收益、after-cost盈利或参数最优性；
- 真实数据源、provider日历或账户资格；
- 将F0晋升为权威`f`、`DecisionResult`或交易状态；
- 券商连接、broker写入、部署或真实交易；
- Jake逐项审阅了全部工程证据文件。

全部结果继续保持`RESEARCH_ONLY / NO_DECISION_EFFECT / actionable=false / broker_order_count=0`。

## Immutability

本回执为追加式Owner证据。后续如修改接受范围，只能新增明确引用本回执的superseding receipt，不得覆盖或改写本文件。
