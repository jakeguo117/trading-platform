# GLD Entry Decision f F0 Owner Decision OD-07 Receipt

Created: 2026-09-01 17:48 Asia/Shanghai

状态：`OD07_CAPTURED / R1_LOCAL_IMPLEMENTATION_AUTHORIZED / RESEARCH_ONLY`

## 本次Owner决定

Jake批准在本地 synthetic 边界内实现唯一候选 Entry 小F：

```text
Validated Entry Facts
+ Required Technical Facts
+ Trial Policy theta
+ Independent LC0/BCS0 Evidence
-> Entry f F0
-> DecisionResultF0 + DecisionTraceF0
-> Final Decision Card
```

该实现必须保持 `RESEARCH_ONLY / NO_DECISION_EFFECT / actionable=false / broker_order_count=0`。完成只证明程序可运行、可解释、可重复并会失败关闭，不证明参数最优、策略盈利或真实交易能力。

## 对OD-06的追加决定

OD-06原文和hash保持不变。本回执仅为R1 Entry f F0追加以下候选语义：

- LC0与BCS0分别从完整Call universe独立选择；BCS0 long不再被强制绑定为LC0 long。
- 只有一个结构完整合格时，输出该单一方案。
- 两个结构都完整合格时，程序按预注册Preference规则输出唯一首选和一个解释性备选。
- 备选不是自动fallback。首选之后不可交易时，必须取得新的原子快照并重新运行f。
- 每个结果都包含固定八阶段Decision Trace；前置阻断后，后续阶段继续存在并明确标记没有运行的原因和恢复方式。

这些语义只适用于新的隔离包 `gld_entry_decision_f0`。现有simulation、Technical Facts V1、Management F0、旧DecisionResult和旧HTML不被改写。

## Evidence与Policy边界

- LC0和BCS0必须分别绑定独立episode、return distribution、uncertainty receipt、Kelly receipt与退出政策。
- 每份Evidence还必须绑定产生episode的Entry theta、费用版本及最晚估计outcome时点；政策变化或未来outcome都不能复用旧Kelly。
- R1只用本地synthetic fixtures验证Evidence V2估计器和决策算法。
- R1不访问真实历史outcome，不产生Policy winner，也不产生Owner批准的Active theta。
- Gate、Delta、account-risk、drawdown、Hard Stop和Preference参数均标记为 `TRIAL_CANDIDATE_NOT_VERIFIED`。
- 真数据资格、after-cost walk-forward、sealed/OOS选择与theta晋升属于Roadmap R2，不能由R1结果自动触发。

## 非授权事项

本回执不授权branch、stage、commit、push、PR、merge、部署、发布、真实行情、IBKR/provider、真实账户、真实历史outcome、下单或交易。
