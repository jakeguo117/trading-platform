# GLD Entry Decision f F0 Owner Acceptance Receipt v0.1

- 时间：2026-09-01（Asia/Shanghai）
- 产品：`Trading Platform`
- Milestone：`R1｜GLD Entry Decision f F0（研究验证版）`
- 状态：`OWNER_ACCEPTED_COMPLETE / RESEARCH_ONLY / NO_DECISION_EFFECT`

## Owner明确批准

Jake原话：

> R1 1–10全部认同

据此，以下十项Owner业务判断全部接受：

1. 缺数据为`NO_DECISION`，完整数据但Gate失败为`NO_ACTION`。
2. Trend与Breakout分别按冻结quota判断，不得互相补分。
3. LC0与BCS0独立筛选、独立evidence、独立数量和失败原因。
4. 单一结构合格时输出该单一方案。
5. Safe Quantity取Kelly、账户、现金、Delta、流动性与外部风险cap的最小值；0不得补成1。
6. 双合格产生确定首选与解释性备选；完全平局固定LC0优先。
7. 最终方案绑定Hard Stop、Confirmed Invalidation、H20、expiry safety与Management F0版本。
8. Final Decision Card可从输入、Gate、结构、数量、Preference、退出一路复述到最终结论。
9. Fail-closed时保留八个stage，并说明停止位置、原因与恢复方式。
10. 相同有效输入产生相同结果，且`broker_order_count=0`。

## 验收前资产绑定

- 正式目录：`artifacts/gld-entry-decision-f0-owner-acceptance-v1/`
- 文件数量：`45`
- 场景数量：`8`
- acceptance manifest文件SHA-256：`f0004cdb593121f1cb41f632f4b36c3399fe5d380d938e909f0cd4bcf02141a2`
- manifest内部SHA-256：`057643ecdca236ef512defa29d543b34a493843dae502af98b20a9271d9ed67f`
- Owner首页SHA-256：`5590a21a5fbccc34a1ce495484f9ec16b8c29c89b10def6349c59ed6d28aa18b`
- 验收前Entry f规范 `artifacts/gld-entry-decision-f0-owner-acceptance-v1/reference/GLD_ENTRY_DECISION_F0_SPEC.md` SHA-256：`240c6dc5df135c1062db067ad6c8e215fa39a041403a488e1aed32c0f9d83c81`
- 验收前Entry Policy研究资产 `artifacts/gld-entry-decision-f0-owner-acceptance-v1/reference/GLD_ENTRY_POLICY_RESEARCH_ASSET_F0.md` SHA-256：`1541636445c029602f7b0356b03b4149a4e8b7fb1cda545dd8b5664218ed082e`

自动生成的manifest继续保持`OWNER_ACCEPTANCE_PENDING`。它证明验收前工程证据，不得被修改或解释为程序替Owner签字；本回执是独立、追加式人工接受记录。

## 自动与独立证据

- R1专项回归：`63 / 63 PASS`。
- 全仓回归：`619 / 619 PASS`，`769.791s`，`OK`。
- 两个全新输出目录：各`45`个文件，逐字节一致。
- 独立代码复审：`P0=0 / P1=0 / APPROVE`；一个不阻断本地R1验收的目录持久性P2保留为工程说明。
- 所有场景：`RESEARCH_ONLY / NO_DECISION_EFFECT / actionable=false / broker_order_count=0`。

首次私有GitHub备份前另完成Codex Security Standard Scan `7d096e8b-ee8e-4da1-a4af-79ee32287519`：未发现凭证、网络写入或broker写入；三个已验证低风险本地工程问题不改变本次Owner验收结论，也不授权扩展修复范围。

## 未被本次批准的事项

- Gate、Delta、10%账户风险、drawdown、Hard Stop及Preference参数仍为候选，不证明最优或盈利。
- `policy_winner`仍为`null`；`ACTIVE_ENTRY_POLICY_NOT_AVAILABLE`继续有效。
- 未批准真实行情、历史outcome、provider/IBKR访问、真实账户、broker写入、订单、部署或交易。
- R2只有在本回执与可复现核心完成私有GitHub备份并经新鲜克隆验证后，才可进入`CURRENT_FOCUS / PLAN_READY`；不得自动开始研究实施。
