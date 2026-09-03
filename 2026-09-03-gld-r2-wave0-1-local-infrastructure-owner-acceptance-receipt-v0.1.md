# GLD R2 Wave 0–1 Local Infrastructure Owner Acceptance Receipt v0.1

## Decision

| field | value |
|---|---|
| `decision_id` | `GLD_R2_WAVE_0_1_LOCAL_INFRASTRUCTURE_OWNER_ACCEPTANCE_V1` |
| `decision` | `OWNER_ACCEPTED_LOCAL_INFRASTRUCTURE` |
| `owner` | `Jake` |
| `accepted_at` | `2026-09-03 Asia/Shanghai` |
| `product` | `Trading Platform` |
| `milestone` | `R2｜历史证据与 theta 资格化 F1` |
| `accepted_scope` | `Wave 0–1 本地 synthetic 筛选基础设施` |
| `machine_status` | `R2_INFRASTRUCTURE_VERIFIED` |
| `next_gate` | `METADATA_AUTHORIZATION_REQUIRED` |
| `r2_completion` | `NOT_COMPLETE` |
| `authority_effect` | `NONE` |

Jake 的原话：

> 我批准 R2 Wave 0–1 本地基础设施验收。

## Accepted scope

Jake 接受当前 Owner 页面所展示的本地筛选基础设施已经按获批的 R2 Wave 0–1 计划实现并通过验收，具体包括：

1. 16 个固定完整政策包已形成 closed registry，其中 12 个可进入 Development 筛选，4 个 no-stop controls 只作诊断。
2. LC0 与 BCS0 是每个政策包内部的独立证据路径，不扩大候选维度；本代固定 `q=1`。
3. Development disposition、ENTRY/LABEL20、共同 mask、五折 Walk-forward、carrier/joint statistics、退出/费用压力接口和 Exact-100 Forward container 均已有 deterministic synthetic 本地实现。
4. Unknown 或 insufficient evidence 不会被误判为策略失败；只有充分证据下全部候选失败时，未来真实流程才可得出 `NO_QUALIFIED_POLICY`。
5. 候选筛选终局只能为零个或最多一个机器合格候选；qualification identity 与后续 Owner 决定分离。
6. 当前可见流程诚实停止在 `R2_INFRASTRUCTURE_VERIFIED -> METADATA_AUTHORIZATION_REQUIRED`，没有把 synthetic 诊断表达为真实研究结论。

这次人工接受只关闭 Wave 0–1 本地基础设施的 Owner 验收节点，不关闭 R2，也不接受或产生任何研究 `theta`。

## Bound pre-acceptance evidence

| evidence | SHA-256 |
|---|---|
| `artifacts/gld-r2-infrastructure-acceptance-v1/acceptance-manifest.json` | `5104745484e17422d05fb448516eddd4082db00c94d4c782b568e0f9c492318d` |
| `artifacts/gld-r2-infrastructure-acceptance-v1/index.html` | `c443e8b1220a0ac6e478420f4348fc3080b9541155fb2ce8be3355456f718e16` |
| `artifacts/gld-r2-infrastructure-acceptance-v1/engineering-evidence.html` | `cb853d9b62621ffd4e0d9c7251793c035e10fe1a249dc127437e7163e9849e6d` |

Acceptance and verification facts follow. The full-repository run and the two independent reviews are the already-verified evidence Jake accepted; they were not repeated merely because this documentation receipt was added. The R2 focused suite was re-run while recording the receipt.

| field | value |
|---|---|
| acceptance manifest internal canonical SHA-256 | `e711f9a3de310d2f39c17adb4c66ec873861f80f10754c2964a428b40fb2b847` |
| bound acceptance file count | `15` |
| synthetic Owner scenarios | `6 / 6 expected == actual` |
| R2 focused tests, re-run during receipt recording | `118 / 118 PASS` |
| R1 Entry regression | `63 / 63 PASS` |
| full local regression, accepted pre-receipt evidence | `737 / 737 PASS` |
| independent code review, accepted pre-receipt evidence | `APPROVE; blockers = 0` |
| independent security review, accepted pre-receipt evidence | `APPROVE; blockers = 0` |
| accepted R1 baseline commit | `b21400d799f7b441d35f528a5980b1ce855997a7` |
| R1 formal acceptance assets versus accepted commit | `BYTE_IDENTICAL` |

The generated acceptance manifest intentionally remains:

```text
owner_status = OWNER_ACCEPTANCE_PENDING
owner_receipt_generated = false
```

That manifest is immutable pre-acceptance engineering evidence. It is not rewritten to simulate an Owner signature. This separate, append-only receipt records the human decision.

## State after acceptance

The machine and authority state remains:

```text
status = R2_INFRASTRUCTURE_VERIFIED
next = METADATA_AUTHORIZATION_REQUIRED
classification = SYNTHETIC_ONLY
scope = RESEARCH_ONLY
authority_status = NO_DECISION_EFFECT
actionable = false
broker_order_count = 0
real_data_accessed = false
real_theta_produced = false
historical_theta_count = 0
forward_theta_count = 0
final_theta_count = 0
```

## Not authorized or proven

This approval does not authorize or prove:

- metadata, provider/API, credentials, downloads, licensing, fees or real-data access;
- Development outcome, Walk-forward outcome, historical `theta` or qualification;
- Forward collection, sealed reveal or reuse of a failed interval;
- R3 planning or execution, Active `theta`, deployment, broker writes, orders or trading;
- stage, commit, push, pull request or merge;
- strategy optimality, after-cost profitability or future performance.

The next step remains a separate, exact Jake authorization package for metadata only. No later gate inherits authority from this receipt.

## Immutability

This receipt is an append-only Owner record. Any later change to the accepted scope or decision must be expressed by a new superseding receipt that identifies this file; this file and the three bound pre-acceptance assets must not be overwritten.
